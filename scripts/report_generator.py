import json
import re
from datetime import date
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from model_api import (
    ModelRequestError,
    chat_completion_json,
    create_openai_client,
    load_project_environment,
    validate_model_configuration,
)
from planning_agent import AnalysisPlan


PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "report_system_zh.txt"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")


class ReportConclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="一条中文报告结论")
    evidence_ids: list[str] = Field(min_length=1, description="支持该结论的证据编号")

    @field_validator("text")
    @classmethod
    def reject_model_authored_amounts(cls, value: str) -> str:
        amount_pattern = (
            r"£\s*[\d,]+(?:\.\d+)?"
            r"|[\d,]+(?:\.\d+)?\s*(?:GBP|英镑)"
            r"|(?:金额|总额|差额|变化率)[^，。；;]{0,12}\d+(?:\.\d+)?\s*(?:%|英镑|GBP)"
            r"|\d+(?:\.\d+)?\s*%"
            r"|\d+(?:\.\d+)?\s*件"
        )
        if re.search(amount_pattern, value, re.IGNORECASE):
            raise ValueError("金额和百分比由程序渲染，模型正文不得自行写入数值")
        return value


class ReportDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, description="中文报告标题")
    conclusions: list[ReportConclusion] = Field(min_length=1, description="程序生成的排名答案和带证据解释")


class ReportModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, description="中文报告标题")
    explanations: list[ReportConclusion] = Field(
        default_factory=list,
        description="不重复名次、榜首、实体排名或数值的解释性观察",
    )


class ReportGenerationError(RuntimeError):
    pass


class ReportEvidenceError(ReportGenerationError):
    pass


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _plan_payload(plan: AnalysisPlan) -> dict:
    payload = plan.model_dump(mode="json")
    for field in ("period1_start", "period1_end", "period2_start", "period2_end"):
        payload[field] = _date_text(getattr(plan, field))
    return payload


def _key_token(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def build_evidence(result: dict, plan: AnalysisPlan) -> dict[str, dict]:
    if result.get("evidence"):
        return result["evidence"]
    if result.get("analysis_type") == "summary" or plan.analysis_type == "summary":
        evidence = {}
        summary = result.get("summary", {})
        metric_totals = result.get("metric_totals", {"amount": summary.get("total")})
        for metric, total in metric_totals.items():
            if total is None:
                continue
            evidence_id = "summary.total" if metric == "amount" else "summary.quantity"
            evidence[evidence_id] = {
                "label": "分析范围正向交易金额" if metric == "amount" else "分析范围有效销售件数",
                "value": total,
                "unit": "GBP" if metric == "amount" else "items",
            }
        group_by = result.get("group_by") or "total"
        for row in result.get("groups", []):
            token = _key_token(row["key"])
            values = row.get("values", {"amount": row.get("amount")})
            for metric, value in values.items():
                if value is None:
                    continue
                suffix = "amount" if metric == "amount" else "quantity"
                evidence[f"{group_by}.{token}.{suffix}"] = {
                    "label": f"{row['label']}正向交易金额" if metric == "amount" else f"{row['label']}有效销售件数",
                    "value": value,
                    "unit": "GBP" if metric == "amount" else "items",
                }
        for ranking in result.get("rankings", []):
            identifier = f"ranking.{ranking['business_dimension']}.overall"
            if ranking.get("time_dimension"):
                identifier = f"ranking.{ranking['time_dimension']}.{ranking['period_key']}.{ranking['business_dimension']}"
            evidence[identifier] = {
                "label": f"{ranking['period_label']}按{ranking['metric']}排名",
                "value": {
                    "metric": ranking["metric"], "order": ranking["order"],
                    "requested_count": ranking["requested_count"],
                    "actual_count": ranking["actual_count"],
                    "rows": ranking["rows"], "leaders": ranking["leaders"],
                },
                "unit": "ranking",
            }
        return evidence

    evidence = {}

    def add(identifier: str, label: str, value: float, unit: str) -> None:
        evidence[identifier] = {"label": label, "value": value, "unit": unit}

    metric_results = result.get("metric_results") or {result.get("metric", "amount"): result}
    for metric, metric_result in metric_results.items():
        suffix = "" if metric == "amount" else f".{metric}"
        label = "正向交易金额" if metric == "amount" else "有效销售件数"
        unit = "GBP" if metric == "amount" else "items"
        add(f"total.period1{suffix}", f"前期{label}", metric_result["period1_total"], unit)
        add(f"total.period2{suffix}", f"后期{label}", metric_result["period2_total"], unit)
        add(f"total.delta{suffix}", f"{label}差额（后期减前期）", metric_result["difference"], unit)
        if metric_result["change_rate"] is not None:
            add(f"total.change_pct{suffix}", f"{label}变化率", metric_result["change_rate"] * 100, "percent")

        dimensions = (
            ("country", "country", "country_decreases", "country_increases", "Country"),
            ("product", "product", "product_decreases", "product_increases", "StockCode"),
        )
        for dimension, label_key, down_key, up_key, key_field in dimensions:
            if dimension not in plan.dimensions:
                continue
            for direction, result_key in (("decline", down_key), ("growth", up_key)):
                if plan.direction not in {direction, "both"}:
                    continue
                rows = metric_result.get(result_key, [])
                ranking_id = f"ranking.{dimension}.{direction}{suffix}"
                evidence[ranking_id] = {
                    "label": f"{label_key}按{label}差額{direction}排名",
                    "value": {
                        "scope": _render_filter_scope(metric_result.get("filter_scope", {})),
                        "metric": metric,
                        "order": direction,
                        "requested_count": plan.top_n,
                        "actual_count": len(rows),
                        "rows": [
                            {"key": str(row[key_field]), "description": row.get("Description"),
                             "period1": row["period1"], "period2": row["period2"],
                             "difference": row["difference"]}
                            for row in rows
                        ],
                    },
                    "unit": "ranking",
                }
                for row in rows:
                    token = _key_token(row[key_field])
                    item_label = row[key_field]
                    prefix = f"{label_key}.{token}{suffix}"
                    add(f"{prefix}.period1", f"{item_label} 前期{label}", row["period1"], unit)
                    add(f"{prefix}.period2", f"{item_label} 后期{label}", row["period2"], unit)
                    add(f"{prefix}.delta", f"{item_label} {label}差额", row["difference"], unit)

    return evidence


def validate_evidence_references(draft: ReportDraft, evidence: Mapping[str, object]) -> None:
    unknown = sorted(
        {
            evidence_id
            for conclusion in draft.conclusions
            for evidence_id in conclusion.evidence_ids
            if evidence_id not in evidence
        }
    )
    if unknown:
        raise ReportEvidenceError("报告引用了不存在的证据编号：" + ", ".join(unknown))


def build_ranking_conclusions(
    result: dict, plan: AnalysisPlan, evidence: Mapping[str, dict] | None = None,
) -> list[ReportConclusion]:
    """Build rank claims only from this run's structured ranking records."""
    evidence = evidence if evidence is not None else build_evidence(result, plan)
    answers = []
    metric_names = {"amount": "正向交易金额", "quantity": "有效销售件数"}
    dimension_names = {"product": "商品", "country": "国家", "week": "周", "month": "月份", "day": "日期"}

    rankings = result.get("rankings", [])
    for ranking in rankings:
        evidence_id = (
            f"ranking.{ranking['time_dimension']}.{ranking['period_key']}.{ranking['business_dimension']}"
            if ranking.get("time_dimension")
            else f"ranking.{ranking['business_dimension']}.overall"
        )
        if evidence_id not in evidence:
            continue
        label = dimension_names.get(ranking.get("business_dimension"), "分组")
        if ranking.get("time_dimension"):
            label = f"{ranking['period_label']}的{label}"
        answers.append(_ranking_conclusion(
            label=label,
            metric=ranking["metric"],
            order_label=ranking.get("order_label", "最高"),
            requested=ranking["requested_count"],
            rows=ranking.get("rows", []),
            leaders=ranking.get("leaders", []),
            evidence_id=evidence_id,
        ))

    if not rankings and plan.analysis_type == "comparison":
        for evidence_id, item in evidence.items():
            if item.get("unit") != "ranking":
                continue
            value = item.get("value", {})
            rows = value.get("rows", [])
            if not rows:
                continue
            metric = value.get("metric", plan.rank_by or "amount")
            direction = value.get("order", "growth")
            ordered = sorted(rows, key=lambda row: row.get("difference", 0), reverse=direction == "growth")
            _assign_comparison_ranks(ordered)
            leaders = [row for row in ordered if row.get("rank") == 1]
            dimension = "country" if evidence_id.startswith("ranking.country.") else "product"
            direction_label = "增长" if direction == "growth" else "下降"
            answers.append(_ranking_conclusion(
                label=f"{dimension_names[dimension]}{direction_label}榜",
                metric=metric,
                order_label="金额增加最多" if direction == "growth" else "金额下降最多",
                requested=value.get("requested_count", plan.top_n),
                rows=ordered,
                leaders=leaders,
                evidence_id=evidence_id,
            ))
    return answers


def _assign_comparison_ranks(rows: list[dict]) -> None:
    previous = object()
    rank = 0
    for position, row in enumerate(rows, 1):
        current = row.get("difference", 0)
        if position == 1 or current != previous:
            rank = position
        row["rank"] = rank
        previous = current


def _ranking_conclusion(
    *, label: str, metric: str, order_label: str, requested: int,
    rows: list[dict], leaders: list[dict], evidence_id: str,
) -> ReportConclusion:
    shown = len(rows)
    order = "、".join(
        f"第 {row.get('rank', index)} 名 {row.get('label', row.get('key', ''))}"
        for index, row in enumerate(rows, 1)
    ) or "无符合条件的项目"
    text = f"{label}按{metric_names(metric)}{order_label}排序，请求前 {requested} 项，实际展示 {shown} 项：{order}。"
    if len(leaders) > 1:
        text += "并列第一：" + "、".join(row.get("label", row.get("key", "")) for row in leaders) + "。"
    if shown > requested:
        cutoff_rank = rows[requested - 1].get("rank", requested)
        text += f"第 {cutoff_rank} 名并列，故实际展示 {shown} 项。"
    return ReportConclusion(text=text, evidence_ids=[evidence_id])


def metric_names(metric: str) -> str:
    return {"amount": "正向交易金额", "quantity": "有效销售件数"}.get(metric, metric)


def render_report_numbers(result: dict, plan: AnalysisPlan) -> dict:
    """Render numerical facts outside the model-authored narrative."""
    def money(value: float) -> str:
        return f"-£{abs(value):,.2f}" if value < 0 else f"£{value:,.2f}"

    def render_metric(metric: str, value: float) -> str:
        return money(value) if metric == "amount" else f"{value:,.0f} 件"

    if result.get("analysis_type") == "summary" or plan.analysis_type == "summary":
        metrics = list(result.get("metrics", getattr(plan, "metrics", ["amount"])))
        metric_totals = result.get("metric_totals", {"amount": result["summary"].get("total", 0.0)})
        return {
            "analysis_type": "summary",
            "date_range": {
                "start": result["date_range"]["start"].isoformat(),
                "end": result["date_range"]["end"].isoformat(),
            },
            "total": money(result["summary"]["total"]) if result["summary"].get("total") is not None else None,
            "group_by": result.get("group_by"),
            "group_by_fields": result.get("group_by_fields", plan.group_by),
            "metrics": metrics,
            "rank_by": result.get("rank_by", getattr(plan, "rank_by", "amount")),
            "metric_totals": {metric: render_metric(metric, metric_totals[metric]) for metric in metrics if metric in metric_totals},
            "ranking_order": plan.ranking_order,
            "top_n": plan.top_n,
            "filter_scope": _render_filter_scope(result.get("filter_scope", {})),
            "groups": [
                {
                    "key": row["key"],
                    "name": row["label"],
                    "values": {metric: render_metric(metric, row["values"][metric]) for metric in metrics if metric in row["values"]},
                    "amount": money(row["values"]["amount"]) if "amount" in row["values"] else None,
                    "quantity": render_metric("quantity", row["values"]["quantity"]) if "quantity" in row["values"] else None,
                    "coverage": row.get("coverage"),
                    "period_key": row.get("period_key"),
                    "business_key": row.get("business_key"),
                }
                for row in result.get("groups", [])
            ],
            "rankings": [
                {
                    **{key: ranking[key] for key in ("time_dimension", "business_dimension", "period_key", "period_label", "metric", "order", "order_label", "requested_count", "actual_count")},
                    "rows": [{**item, "value": render_metric(ranking["metric"], item["value"])} for item in ranking["rows"]],
                    "leaders": [{**item, "value": render_metric(ranking["metric"], item["value"])} for item in ranking["leaders"]],
                }
                for ranking in result.get("rankings", [])
            ],
        }

    metric_results = result.get("metric_results") or {result.get("metric", "amount"): result}
    primary_metric = result.get("rank_by", result.get("metric", "amount"))
    rendered_metrics = {}
    for metric, metric_result in metric_results.items():
        rendered = lambda value: render_metric(metric, value)
        rendered_metrics[metric] = {
            "period1_total": rendered(metric_result["period1_total"]),
            "period2_total": rendered(metric_result["period2_total"]),
            "difference": _signed_rendered(metric, metric_result["difference"]),
            "change_rate": (
                _signed_percent(metric_result["change_rate"])
                if metric_result["change_rate"] is not None else "不可计算"
            ),
            "country_decreases": [
                {"name": row["Country"], "period1": rendered(row["period1"]), "period2": rendered(row["period2"]),
                 "difference": _signed_rendered(metric, row["difference"])}
                for row in metric_result.get("country_decreases", [])
            ],
            "country_increases": [
                {"name": row["Country"], "period1": rendered(row["period1"]), "period2": rendered(row["period2"]),
                 "difference": _signed_rendered(metric, row["difference"])}
                for row in metric_result.get("country_increases", [])
            ],
            "product_decreases": [
                {"name": row["StockCode"], "description": row.get("Description"),
                 "period1": rendered(row["period1"]), "period2": rendered(row["period2"]),
                 "difference": _signed_rendered(metric, row["difference"])}
                for row in metric_result.get("product_decreases", [])
            ],
            "product_increases": [
                {"name": row["StockCode"], "description": row.get("Description"),
                 "period1": rendered(row["period1"]), "period2": rendered(row["period2"]),
                 "difference": _signed_rendered(metric, row["difference"])}
                for row in metric_result.get("product_increases", [])
            ],
        }
    primary = rendered_metrics.get(primary_metric, next(iter(rendered_metrics.values())))
    return {
        "analysis_type": "comparison",
        "metric": primary_metric,
        "metrics": list(metric_results),
        "filter_scope": _render_filter_scope(result.get("filter_scope", {})),
        "period1_total": primary["period1_total"],
        "period2_total": primary["period2_total"],
        "difference": primary["difference"],
        "change_rate": primary["change_rate"],
        **{key: primary[key] for key in ("country_decreases", "country_increases", "product_decreases", "product_increases")},
        "metric_results": rendered_metrics,
    }


def _signed_rendered(metric: str, value: float) -> str:
    rendered = f"£{abs(value):,.2f}" if metric == "amount" else f"{abs(value):,.0f} 件"
    return f"{'-' if value < 0 else '+'}{rendered}"


def _signed_percent(value: float) -> str:
    percent = value * 100
    return f"{'-' if percent < 0 else '+'}{abs(percent):.2f}%"


def _render_filter_scope(scope: dict) -> dict:
    def date_text(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    date_range = scope.get("date_range", {})
    rendered_range = {}
    for key, value in date_range.items():
        if isinstance(value, dict):
            rendered_range[key] = {name: date_text(part) for name, part in value.items()}
        else:
            rendered_range[key] = date_text(value)
    return {
        "date_range": rendered_range,
        "countries": list(scope.get("countries", [])),
        "requested_countries": list(scope.get("requested_countries", [])),
        "products": list(scope.get("products", [])),
        "requested_products": list(scope.get("requested_products", [])),
        "range_has_records": scope.get("range_has_records"),
        "range_record_count": scope.get("range_record_count"),
        "period1_has_records": scope.get("period1_has_records"),
        "period2_has_records": scope.get("period2_has_records"),
        "message": scope.get("message"),
    }


def generate_report_draft(
    question: str,
    plan: AnalysisPlan,
    result: dict,
    *,
    review_feedback: list[dict] | None = None,
    evidence: dict[str, dict] | None = None,
    client=None,
    environ: Mapping[str, str] | None = None,
) -> tuple[ReportDraft, dict[str, dict]]:
    evidence = evidence if evidence is not None else build_evidence(result, plan)
    env = load_project_environment(environ)
    mode = validate_model_configuration(env)

    if client is None:
        client = create_openai_client(env)

    user_payload = {
        "user_question": question,
        "validated_plan": _plan_payload(plan),
        "local_summary_results_and_evidence": evidence,
        "revision_feedback": review_feedback,
    }
    schema = json.dumps(ReportModelOutput.model_json_schema(), ensure_ascii=False, indent=2)
    system_prompt = SYSTEM_PROMPT.rstrip() + "\n\n输出 JSON Schema：\n" + schema
    try:
        if mode == "chat_completions":
            content = chat_completion_json(
                client,
                env["OPENAI_MODEL"],
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
            )
            payload = json.loads(content)
            model_output = ReportModelOutput.model_validate(payload)
        else:
            response = client.responses.parse(
                model=env["OPENAI_MODEL"],
                instructions=system_prompt,
                input=json.dumps(user_payload, ensure_ascii=False),
                text_format=ReportDraft,
            )
            model_output = response.output_parsed
            if not isinstance(model_output, ReportModelOutput):
                raise ReportGenerationError("模型未返回可校验的结构化报告初稿。")
    except ModelRequestError as exc:
        raise ReportGenerationError(str(exc)) from None
    except json.JSONDecodeError as exc:
        raise ReportGenerationError(f"报告模型返回的 JSON 无效（第 {exc.lineno} 行，第 {exc.colno} 列）。") from None
    except ValidationError as exc:
        fields = sorted({".".join(map(str, error["loc"])) for error in exc.errors()})
        raise ReportGenerationError("报告结构校验失败：" + ", ".join(fields)) from None

    draft = ReportDraft(
        title=(
            _programmatic_ranking_title(plan)
            if plan.ranking_order or plan.analysis_type == "comparison"
            else model_output.title
        ),
        conclusions=[*build_ranking_conclusions(result, plan, evidence), *model_output.explanations],
    )
    validate_evidence_references(draft, evidence)
    return draft, evidence


def _programmatic_ranking_title(plan: AnalysisPlan) -> str:
    metric = metric_names(plan.rank_by or "amount")
    if plan.analysis_type == "comparison":
        dimension = "、".join({"country": "国家", "product": "商品"}[item] for item in plan.dimensions)
        direction = {"growth": "增长", "decline": "下降", "both": "增长与下降"}.get(plan.direction, "变化")
        return f"{dimension}{direction}榜（按{metric}差额）"
    dimension = " × ".join({"day": "日", "week": "周", "month": "月", "country": "国家", "product": "商品"}[item] for item in plan.group_by)
    direction = "最高" if plan.ranking_order == "desc" else "最低"
    return f"{dimension}{metric}{direction}前 {plan.top_n}"
