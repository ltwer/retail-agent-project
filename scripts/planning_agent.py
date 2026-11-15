import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Mapping

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from model_api import (
    ModelConfigurationError,
    ModelRequestError,
    chat_completion_json,
    create_openai_client,
    load_project_environment,
    response_format_unsupported,
    validate_model_configuration,
)


Dimension = Literal["country", "product"]
Direction = Literal["growth", "decline", "both"]
PlanStatus = Literal["executable", "clarification", "unsupported"]
AnalysisType = Literal["summary", "comparison"]
TimeScope = Literal["full_data", "date_range", "comparison"]
GroupBy = Literal["day", "week", "month", "country", "product"]
RankingOrder = Literal["desc", "asc"]
Metric = Literal["amount", "quantity"]
ReferenceIntent = Literal[
    "product_leader", "country_leader", "leader",
    "product_decline_leader", "country_decline_leader", "decline_leader",
    "product_growth_leader", "country_growth_leader", "growth_leader",
]
TIME_GROUPS = {"day", "week", "month"}
BUSINESS_GROUPS = {"country", "product"}
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "planning_system_zh.txt"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")


class AnalysisPlan(BaseModel):
    """Structured intent for the downstream retail analysis workflow."""

    model_config = ConfigDict(extra="forbid")

    status: PlanStatus = Field(default="executable", description="executable、clarification 或 unsupported")
    analysis_type: AnalysisType | None = Field(default="comparison", description="summary 为单范围汇总；comparison 为两期比较")
    time_scope: TimeScope | None = Field(default="comparison", description="full_data、date_range 或 comparison")
    start_date: date | None = Field(default=None, description="单范围分析开始日期；全量范围由程序填入")
    end_date: date | None = Field(default=None, description="单范围分析结束日期；全量范围由程序填入")
    group_by: list[GroupBy] = Field(default_factory=list, description="summary 分组：一个时间维度，或一个时间维度加一个业务维度")
    metrics: list[Metric] = Field(default_factory=lambda: ["amount"], description="计算和展示指标：amount 或 quantity；旧计划默认为 amount")
    rank_by: Metric = Field(default="amount", description="排行榜依据：amount 或 quantity；缺失时兼容默认 amount")
    ranking_order: RankingOrder | None = Field(default=None, description="按 rank_by desc 或 asc 排序；不排名时为 null")
    period1_start: date | None = Field(default=None, description="比较前期开始日期")
    period1_end: date | None = Field(default=None, description="比较前期结束日期")
    period2_start: date | None = Field(default=None, description="比较后期开始日期")
    period2_end: date | None = Field(default=None, description="比较后期结束日期")
    dimensions: list[Dimension] = Field(default_factory=list, description="比较榜单维度，可选 country 和 product")
    direction: Direction | None = Field(default=None, description="比较榜单方向；无需榜单时为 null")
    top_n: int = Field(default=5, ge=1, description="每张榜单或排名的条数，默认 5")
    country_filters: list[str] = Field(default_factory=list, description="国家筛选条件；用户原始国家名或别名，由本地 CSV 规范化")
    product_filters: list[str] = Field(default_factory=list, description="商品筛选条件；商品编号或描述，由本地 CSV 解析")
    reference_intent: ReferenceIntent | None = Field(
        default=None,
        description="用户引用上次结果时只标注榜单类别；实体必须由本地已审核结构化结果解析，非引用问题为 null",
    )
    clarification_question: str | None = Field(default=None, description="需要补充的信息问题")
    unsupported_reason: str | None = Field(default=None, description="数据或操作不支持时的原因")

    @field_validator("country_filters", "product_filters", mode="before")
    @classmethod
    def normalize_filter_list(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("筛选条件必须是字符串列表")
        normalized = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("筛选条件不能包含空值，且必须是字符串")
            item = item.strip()
            if item not in normalized:
                normalized.append(item)
        return normalized

    @field_validator("metrics", mode="before")
    @classmethod
    def normalize_metrics(cls, value):
        if value is None:
            return ["amount"]
        if not isinstance(value, list) or not value:
            raise ValueError("metrics 必须是至少含一个指标的列表")
        if len(set(value)) != len(value):
            raise ValueError("metrics 不能重复")
        return value

    @classmethod
    def model_json_schema(cls, by_alias: bool = True, ref_template: str = "#/$defs/{model}", **kwargs):
        """Expose every plan key in the model-derived contract.

        Values may still be null for clarification/unsupported plans; requiring
        the keys prevents the model from silently reverting to legacy aliases.
        """
        schema = super().model_json_schema(by_alias=by_alias, ref_template=ref_template, **kwargs)
        schema["required"] = list(schema.get("properties", {}))
        return schema

    @model_validator(mode="after")
    def validate_plan_consistency(self):
        legacy_missing_dates = self.analysis_type == "comparison" and any(
            value is None for value in (self.period1_start, self.period1_end, self.period2_start, self.period2_end)
        )
        if self.status == "executable" and legacy_missing_dates and self.clarification_question:
            self.status = "clarification"

        if self.status == "clarification":
            if not self.clarification_question or self.unsupported_reason:
                raise ValueError("clarification plan requires clarification_question and no unsupported_reason")
            if self.analysis_type == "comparison":
                if self.time_scope != "comparison" or self.group_by or self.ranking_order is not None:
                    raise ValueError("comparison 澄清计划不能包含 summary 范围、分组或排名字段")
                if not self.dimensions and self.direction is not None:
                    raise ValueError("不请求比较榜单时 direction 必须为空")
                if self.dimensions and self.direction is None:
                    raise ValueError("请求比较榜单时必须指定 direction")
            elif self.analysis_type == "summary":
                if self.time_scope not in {"full_data", "date_range"}:
                    raise ValueError("summary 澄清计划的 time_scope 必须是 full_data 或 date_range")
                if self.time_scope == "full_data" and (self.start_date is not None or self.end_date is not None):
                    raise ValueError("full_data 汇总的日期范围由程序从 CSV 读取")
                validate_grouping(self.group_by)
                if self.ranking_order is not None and not self.group_by:
                    raise ValueError("总额汇总不支持排名；排名需要指定一个分组维度")
                if self.dimensions or self.direction is not None or any(
                    value is not None for value in (self.period1_start, self.period1_end, self.period2_start, self.period2_end)
                ):
                    raise ValueError("summary 澄清计划不能包含两期比较字段")
            return self
        elif self.status == "unsupported":
            if not self.unsupported_reason or self.clarification_question:
                raise ValueError("unsupported plan requires unsupported_reason and no clarification_question")
            return self
        elif self.clarification_question or self.unsupported_reason:
            raise ValueError("executable plan cannot contain clarification or unsupported_reason")

        if self.analysis_type is None:
            if self.status == "executable":
                raise ValueError("executable plan requires analysis_type")
            return self

        if self.analysis_type == "summary":
            if self.time_scope not in {"full_data", "date_range"}:
                raise ValueError("summary plan requires time_scope full_data or date_range")
            if self.status == "executable" and self.time_scope == "date_range" and (self.start_date is None or self.end_date is None):
                raise ValueError("date_range summary requires start_date and end_date")
            if self.time_scope == "full_data" and (self.start_date is not None or self.end_date is not None):
                raise ValueError("full_data summary dates are read from CSV")
            validate_grouping(self.group_by)
            if self.ranking_order is not None and not self.group_by:
                raise ValueError("total summary cannot rank without a group_by dimension")
            if self.ranking_order is not None and self.rank_by not in self.metrics:
                raise ValueError("rank_by must be included in metrics when ranking is requested")
            if self.ranking_order is None and self.top_n != 5:
                raise ValueError("top_n must remain 5 when ranking is not requested")
            if self.dimensions or self.direction is not None or any(
                value is not None for value in (self.period1_start, self.period1_end, self.period2_start, self.period2_end)
            ):
                raise ValueError("summary plan cannot contain comparison fields")
        else:
            if self.time_scope != "comparison":
                raise ValueError("comparison plan requires time_scope comparison")
            periods = (
                (self.period1_start, self.period1_end),
                (self.period2_start, self.period2_end),
            )
            if self.status == "executable" and any(start is None or end is None for start, end in periods):
                raise ValueError("comparison plan requires both period date ranges")
            if self.start_date is not None or self.end_date is not None or self.group_by or self.ranking_order is not None:
                raise ValueError("comparison plan cannot contain summary fields")
            if not self.dimensions and self.direction is not None:
                raise ValueError("direction must be null without comparison dimensions")
            if self.dimensions and self.direction is None:
                raise ValueError("direction is required when comparison dimensions are requested")
            if self.dimensions and self.rank_by not in self.metrics:
                raise ValueError("rank_by must be included in metrics when comparison rankings are requested")
            if len(set(self.dimensions)) != len(self.dimensions):
                raise ValueError("comparison dimensions cannot repeat")
        return self


def validate_grouping(group_by: list[str]) -> None:
    if len(group_by) > 2 or len(set(group_by)) != len(group_by):
        raise ValueError("summary 最多支持两个不同分组")
    if len(group_by) == 2 and not (
        (group_by[0] in TIME_GROUPS and group_by[1] in BUSINESS_GROUPS)
        or (group_by[1] in TIME_GROUPS and group_by[0] in BUSINESS_GROUPS)
    ):
        raise ValueError("组合分组必须由一个时间维度（日/周/月）和一个业务维度（国家/商品）组成")


def metric_request_problem(question: str, plan: AnalysisPlan) -> tuple[str, bool] | None:
    """Return (user-facing reason, is clarification) when the plan misses intent."""
    text = question.casefold()
    wants_quantity = any(term in text for term in ("销量", "件数", "销售件数", "卖出多少件", "售出多少件"))
    wants_amount = any(term in text for term in ("销售额", "金额", "交易额", "英镑", "销售收入"))
    wants_both = wants_quantity and wants_amount
    required = {"quantity"} if wants_quantity else set()
    if wants_amount:
        required.add("amount")
    missing = required - set(plan.metrics)
    if missing:
        wanted = "销量（有效商品件数）" if missing == {"quantity"} else "销售额（英镑）和销量（件数）"
        actual = "、".join(plan.metrics)
        return (f"用户要求的指标为{wanted}，但当前计划只计算：{actual}。需重新规划后才能计算。", False)

    if plan.ranking_order:
        quantity_rank = any(term in text for term in ("销量最多", "销量最高", "件数最多", "件数最高", "数量最多", "按销量", "按件数"))
        amount_rank = any(term in text for term in (
            "销售额最高", "金额最高", "金额最多", "金额榜首", "金额榜", "金额排名",
            "按销售额", "按金额",
        ))
        if quantity_rank and not amount_rank:
            explicit_rank = "quantity"
        elif amount_rank and not quantity_rank:
            explicit_rank = "amount"
        elif wants_quantity and not wants_amount:
            explicit_rank = "quantity"
        elif wants_amount and not wants_quantity:
            explicit_rank = "amount"
        else:
            explicit_rank = None
        sell_best_default = any(term in text for term in ("卖得最好", "卖得最好的", "畅销", "最好卖", "销售排名", "销售榜"))
        if explicit_rank and plan.rank_by != explicit_rank:
            wanted = "销量（件数）" if explicit_rank == "quantity" else "销售额（金额）"
            actual = "销量（件数）" if plan.rank_by == "quantity" else "销售额（金额）"
            return (f"用户要求按{wanted}排名，但计划按{actual}排名；不能用错误指标回答。", False)
        if not explicit_rank and not sell_best_default:
            return ("请确认排名依据：按销售额（金额）还是按销量（件数）？", True)
    return None


def _resolve_redundant_rank_clarification(question: str, plan: AnalysisPlan) -> AnalysisPlan:
    """Recover only a summary rank clarification contradicted by its own plan."""
    text = question.casefold()
    if (
        plan.status == "clarification"
        and plan.analysis_type == "comparison"
        and plan.time_scope == "comparison"
        and plan.rank_by == "amount"
        and "amount" in plan.metrics
        and plan.dimensions
        and plan.direction
        and not any(term in text for term in ("销量", "件数", "销售件数"))
        and any(term in text for term in ("卖得更好", "卖得更好了", "卖得最好", "畅销"))
        and any(term in (plan.clarification_question or "") for term in ("排名依据", "销售额还是销量", "金额还是销量"))
    ):
        missing = [
            field for field in ("period1_start", "period1_end", "period2_start", "period2_end")
            if getattr(plan, field) is None
        ]
        if missing:
            question_text = (
                "请指定要比较的两个日期区间。" if len(missing) == 4
                else "还缺少：" + "、".join({
                    "period1_start": "前期开始日期", "period1_end": "前期结束日期",
                    "period2_start": "后期开始日期", "period2_end": "后期结束日期",
                }[field] for field in missing) + "。"
            )
            return plan.model_copy(update={"clarification_question": question_text})

    if plan.status != "clarification" or plan.analysis_type != "summary":
        return plan
    if not plan.ranking_order or not plan.group_by or plan.rank_by not in plan.metrics:
        return plan
    if plan.time_scope == "date_range" and (plan.start_date is None or plan.end_date is None):
        return plan
    if plan.time_scope not in {"full_data", "date_range"}:
        return plan
    clarification = (plan.clarification_question or "").casefold()
    if not any(token in clarification for token in ("排名依据", "按销售额", "按销量", "按金额", "按件数")):
        return plan
    candidate = plan.model_copy(update={
        "status": "executable",
        "clarification_question": None,
        "unsupported_reason": None,
    })
    if metric_request_problem(question, candidate) is not None:
        return plan
    return candidate



@dataclass(frozen=True)
class CsvMetadata:
    min_date: date
    max_date: date


class PlannerRequestError(RuntimeError):
    pass


def _response_format_unsupported(error) -> bool:
    return response_format_unsupported(error)


def _chat_completion_content(client, model: str, context: str) -> str:
    messages = [
        {"role": "system", "content": _chat_system_message()},
        {"role": "user", "content": context},
    ]
    try:
        return chat_completion_json(client, model, messages)
    except ModelRequestError as exc:
        raise PlannerRequestError(str(exc)) from None


def _parse_chat_plan(content: str) -> AnalysisPlan:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise PlannerRequestError(f"模型返回的 JSON 无效（第 {exc.lineno} 行，第 {exc.colno} 列）。") from None
    if not isinstance(payload, dict):
        raise PlannerRequestError("模型返回的 JSON 无效：根值必须是对象。")
    expected_period_fields = {"period1_start", "period1_end", "period2_start", "period2_end"}
    missing_period_fields = (
        expected_period_fields - set(payload)
        if payload.get("status", "executable") == "executable"
        and payload.get("analysis_type", "comparison") == "comparison"
        else set()
    )
    extra_fields = set(payload) - set(AnalysisPlan.model_fields)
    if missing_period_fields or extra_fields:
        details = []
        if missing_period_fields:
            details.append("缺失字段=[" + ", ".join(sorted(missing_period_fields)) + "]")
        if extra_fields:
            details.append("额外字段=[" + ", ".join(sorted(extra_fields)) + "]")
        raise PlannerRequestError("计划字段校验失败：" + "; ".join(details))
    if payload.get("analysis_type") == "comparison":
        # Comparison ranking is fully defined by rank_by plus direction; this
        # field belongs to summary plans and models may redundantly set it.
        payload = {**payload, "ranking_order": None}
    if (
        payload.get("status") == "clarification"
        and payload.get("analysis_type") == "comparison"
        and payload.get("time_scope") == "comparison"
        and all(payload.get(field) is None for field in expected_period_fields)
        and isinstance(payload.get("clarification_question"), str)
        and payload["clarification_question"].strip()
    ):
        # Date-free comparison clarifications cannot execute. Some gateways
        # still populate unused summary fields or an empty metric list; keep
        # the requested dimension/direction and the actual question.
        payload = {
            **payload,
            "start_date": None,
            "end_date": None,
            "group_by": [],
            "ranking_order": None,
            "metrics": payload.get("metrics") or ["amount"],
        }
    try:
        return AnalysisPlan.model_validate(payload)
    except ValidationError as exc:
        missing = set()
        extra = set()
        invalid = set()
        for item in exc.errors():
            field = ".".join(str(part) for part in item["loc"]) or "计划组合"
            error_type = item["type"]
            if error_type == "missing":
                missing.add(field)
            elif error_type == "extra_forbidden":
                extra.add(field)
            else:
                invalid.add((field, error_type))

        details = []
        if missing:
            details.append("缺失字段=[" + ", ".join(sorted(missing)) + "]")
        if extra:
            details.append("额外字段=[" + ", ".join(sorted(extra)) + "]")
        if invalid:
            formatted = ", ".join(
                f"{field} ({error_type})" for field, error_type in sorted(invalid)
            )
            details.append("格式/取值错误=[" + formatted + "]")
        raise PlannerRequestError("计划字段校验失败：" + "; ".join(details)) from None


def _chat_system_message() -> str:
    schema = json.dumps(AnalysisPlan.model_json_schema(), ensure_ascii=False, indent=2)
    return (
        SYSTEM_PROMPT.rstrip()
        + "\n\n结构化输出约定：必须严格使用下方 JSON Schema 中定义的字段名，不得改名、翻译或增加同义字段。"
        + "日期格式为 YYYY-MM-DD；无法确定的日期必须填 null，并在 clarification_question 中提出一条具体问题。"
        + "\nJSON Schema (source: AnalysisPlan.model_json_schema):\n"
        + schema
    )


def load_csv_metadata(csv_path: str | Path) -> CsvMetadata:
    """Read only InvoiceDate locally; transaction rows are never sent to the model."""
    dates = pd.read_csv(csv_path, usecols=["InvoiceDate"])["InvoiceDate"]
    parsed = pd.to_datetime(dates, errors="coerce").dropna()
    if parsed.empty:
        raise ValueError("CSV 的 InvoiceDate 列没有可用日期。")
    return CsvMetadata(parsed.min().date(), parsed.max().date())


def build_model_context(question: str, metadata: CsvMetadata) -> str:
    return (
        f"用户问题：\n{question.strip()}\n\n"
        f"CSV 可用日期范围：{metadata.min_date.isoformat()} 至 {metadata.max_date.isoformat()}（含首尾）\n"
        "可执行的操作字段：\n"
        "- analysis_type：summary 单范围汇总；comparison 两期比较\n"
        "- time_scope：full_data 整份 CSV；date_range 明确单区间；comparison 两期区间\n"
        "- group_by：day、week、month、country、product；可选一个分组，或时间维度加业务维度\n"
        "- metrics：amount（正向交易金额）和/或 quantity（有效商品件数）；旧计划默认 amount\n"
        "- rank_by：amount 或 quantity，表示独立的排名依据；用户问销量/件数必须为 quantity，问销售额/金额必须为 amount；“卖得最好”默认 amount\n"
        "- 用户只要求排名但无法确定金额还是销量时，status=clarification 并具体询问排名依据\n"
        "- ranking_order：desc 为 rank_by 指标最高，asc 为最低；top_n 为排名条数\n"
        "- country_filters：国家筛选字符串列表；product_filters：商品编号或描述列表\n"
        "- reference_intent：用户明确引用上轮已审核榜单时的类别枚举；‘榜首商品’选 product_leader，‘榜首国家’选 country_leader。实体及继承范围由程序处理\n"
        "- status：executable、clarification、unsupported\n"
        "可用于识别分析意图的字段：\n"
        "- InvoiceDate：交易日期\n"
        "- Country：国家维度\n"
        "- StockCode：商品代码，商品榜按此字段分组\n"
        "- Description：商品描述，仅用于理解和展示商品\n"
    )


def validate_plan_dates(plan: AnalysisPlan, metadata: CsvMetadata) -> None:
    if plan.status != "executable":
        return
    if plan.analysis_type == "summary":
        if plan.time_scope == "full_data":
            plan.start_date = metadata.min_date
            plan.end_date = metadata.max_date
        periods = (("分析范围", plan.start_date, plan.end_date),)
    else:
        periods = (
            ("前期", plan.period1_start, plan.period1_end),
            ("后期", plan.period2_start, plan.period2_end),
        )
    for name, start, end in periods:
        if start is None or end is None:
            continue
        if start > end:
            raise ValueError(f"{name}开始日期晚于结束日期。")
        if start < metadata.min_date or end > metadata.max_date:
            raise ValueError(
                f"{name}日期超出 CSV 可用日期范围："
                f"{metadata.min_date.isoformat()} 至 {metadata.max_date.isoformat()}。"
            )


def plan_question(
    question: str,
    csv_path: str | Path,
    *,
    client=None,
    environ: Mapping[str, str] | None = None,
) -> AnalysisPlan:
    metadata = load_csv_metadata(csv_path)
    env = load_project_environment(environ)
    mode = validate_model_configuration(env)

    if client is None:
        client = create_openai_client(env)

    context = build_model_context(question, metadata)
    if mode == "chat_completions":
        plan = _parse_chat_plan(_chat_completion_content(client, env["OPENAI_MODEL"], context))
    else:
        try:
            response = client.responses.parse(
                model=env["OPENAI_MODEL"],
                instructions=_chat_system_message(),
                input=context,
                text_format=AnalysisPlan,
            )
        except Exception as exc:
            from openai import OpenAIError

            if isinstance(exc, OpenAIError):
                raise PlannerRequestError(f"OpenAI 请求失败（{type(exc).__name__}）。") from None
            raise

        plan = response.output_parsed
        if not isinstance(plan, AnalysisPlan):
            raise PlannerRequestError("模型未返回可校验的结构化计划。")
    plan = _resolve_redundant_rank_clarification(question, plan)
    try:
        validate_plan_dates(plan, metadata)
        metric_problem = metric_request_problem(question, plan) if plan.status == "executable" else None
        if metric_problem:
            reason, needs_clarification = metric_problem
            if needs_clarification:
                return plan.model_copy(update={
                    "status": "clarification", "clarification_question": reason,
                    "unsupported_reason": None,
                })
            return plan.model_copy(update={
                "status": "unsupported", "clarification_question": None,
                "unsupported_reason": reason,
            })
    except ValueError as exc:
        invalid_payload = plan.model_dump(mode="python")
        invalid_payload.update(
            status="unsupported",
            clarification_question=None,
            unsupported_reason=f"日期范围无法执行：{exc}",
        )
        return AnalysisPlan.model_validate(invalid_payload)
    return plan
