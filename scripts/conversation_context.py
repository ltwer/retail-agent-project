from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import re
from typing import Any

from planning_agent import AnalysisPlan


@dataclass(frozen=True)
class TurnDecision:
    kind: str
    changes: dict[str, Any] = field(default_factory=dict)
    clarification: str | None = None


def source_identity(source: str, name: str, content: bytes) -> str:
    content_hash = sha256(content).hexdigest()
    return sha256(f"{source}\0{name}\0{content_hash}".encode("utf-8")).hexdigest()


def bind_context_to_source(context: dict, data_identity: str) -> tuple[dict, bool]:
    if context.get("data_identity") == data_identity:
        return context, False
    return {
        "data_identity": data_identity,
        "pending": None,
        "last_success": None,
        "reference_catalog": None,
    }, True


def classify_user_turn(message: str, context: dict, data_identity: str) -> TurnDecision:
    text = message.strip()
    if not text:
        return TurnDecision("ambiguous", clarification="请补充要分析的问题。")

    same_data = context.get("data_identity") == data_identity
    pending = context.get("pending") if same_data else None
    last_success = context.get("last_success") if same_data else None

    if pending:
        if pending.get("kind") == "drill_choice":
            selection = resolve_drill_choice(pending.get("candidates", []), text)
            if selection.get("status") == "resolved":
                return TurnDecision("answer_drill_choice", changes={"candidate": selection["candidate"]})
            return TurnDecision(
                "ambiguous",
                clarification=selection.get("question") or pending.get("clarification_question") or "请从候选项中选择一个实体。",
            )
        if _looks_like_date_answer(text):
            return TurnDecision("answer_clarification")
        if pending.get("kind") == "missing_analysis" and not _is_self_contained_question(text):
            return TurnDecision("answer_clarification")
        if _is_self_contained_question(text):
            return TurnDecision("new_question")
        return TurnDecision(
            "ambiguous",
            clarification="请先补充当前待澄清的信息，或输入一条完整的新分析问题。",
        )

    if _looks_like_result_reference(text):
        catalog = context.get("reference_catalog") or (last_success or {}).get("reference_catalog")
        if not catalog:
            return TurnDecision(
                "ambiguous",
                clarification="当前没有可引用的已审核榜单。请明确指定商品编号或国家，再说明要分析什么。",
            )
        return TurnDecision("reference_followup")

    if _is_self_contained_question(text):
        return TurnDecision("new_question")

    changes: dict[str, Any] = {}
    country = _country_change(text)
    if country:
        changes["country_filters"] = [country]

    metric = _rank_metric_change(text)
    if metric:
        changes["rank_by"] = metric

    top_n = _top_n_change(text)
    if top_n is not None:
        changes["top_n"] = top_n

    if changes:
        if not last_success:
            subject = "国家条件为" + "、".join(changes["country_filters"]) + "，" if changes.get("country_filters") else ""
            return TurnDecision(
                "ambiguous",
                changes=changes,
                clarification=f"当前没有可继承的成功分析。{subject}请说明要分析的内容和其他条件。",
            )
        return TurnDecision("modify_previous", changes=changes)

    if _looks_like_followup_fragment(text):
        missing = "国家" if "英国" in text or "德国" in text or "荷兰" in text else "分析内容"
        return TurnDecision("ambiguous", clarification=f"请说明要分析{missing}的什么内容。")
    return TurnDecision("new_question")


def build_reference_catalog(plan: AnalysisPlan, result: dict) -> dict[str, Any]:
    """Snapshot only the structured ranked rows from an approved calculation."""
    scope = result.get("filter_scope", {})
    catalog: dict[str, Any] = {
        "analysis_type": plan.analysis_type,
        "scope": {
            "countries": list(scope.get("countries", [])),
            "products": list(scope.get("products", [])),
            "date_range": _json_dates(scope.get("date_range", result.get("date_range", {}))),
        },
        "boards": [],
    }

    if plan.analysis_type == "summary":
        evidence = result.get("evidence", {})
        for ranking in result.get("rankings", []):
            dimension = ranking["business_dimension"]
            evidence_id = _summary_ranking_evidence_id(ranking)
            leaders = ranking.get("leaders", [])
            board_id = ".".join(filter(None, [
                ranking.get("time_dimension"), ranking.get("period_key"), dimension,
            ]))
            catalog["boards"].append({
                "board_id": board_id,
                "evidence_id": evidence_id,
                "dimension": dimension,
                "metric": ranking["metric"],
                "kind": "summary_rank",
                "order": ranking["order"],
                "requested_count": ranking.get("requested_count"),
                "actual_count": ranking.get("actual_count"),
                "period_label": ranking.get("period_label"),
                "leaders": [
                    _reference_entity(row, dimension, ranking["metric"], evidence_id, evidence)
                    for row in leaders
                ],
                "rows": [
                    _reference_entity(row, dimension, ranking["metric"], evidence_id, evidence)
                    for row in ranking.get("rows", [])
                ],
            })
        return catalog

    evidence = result.get("evidence", {})
    metric_results = result.get("metric_results") or {result.get("metric", "amount"): result}
    for metric, metric_result in metric_results.items():
        for dimension, value_field, direction_key in (
            ("country", "Country", "country"),
            ("product", "StockCode", "product"),
        ):
            if dimension not in plan.dimensions:
                continue
            for direction, suffix in (("decline", "decreases"), ("growth", "increases")):
                if plan.direction not in {direction, "both"}:
                    continue
                ranking_id = f"ranking.{dimension}.{direction}" + ("" if metric == "amount" else f".{metric}")
                source_rows = metric_result.get(f"{direction_key}_{suffix}", [])
                ranked_rows = _rank_comparison_rows(source_rows, value_field, direction, metric)
                for row in ranked_rows:
                    token = re.sub(r"[^A-Za-z0-9_-]+", "_", row["entity_key"]).strip("_") or "unknown"
                    value_id = f"{dimension}.{token}" + ("" if metric == "amount" else f".{metric}") + ".delta"
                    row["evidence_ids"] = [ranking_id] + ([value_id] if value_id in evidence else [])
                catalog["boards"].append({
                    "board_id": f"{dimension}.{direction}.{metric}",
                    "evidence_id": ranking_id,
                    "dimension": dimension,
                    "metric": metric,
                    "kind": direction,
                    "direction": direction,
                    "requested_count": plan.top_n,
                    "actual_count": len(ranked_rows),
                    "period_label": _comparison_period_label(plan),
                    "leaders": [row for row in ranked_rows if row["rank"] == 1],
                    "rows": ranked_rows,
                })
    return catalog


def _json_dates(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_dates(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_dates(item) for item in value]
    return value.isoformat() if hasattr(value, "isoformat") else value


def _summary_ranking_evidence_id(ranking: dict) -> str:
    if ranking.get("time_dimension"):
        return f"ranking.{ranking['time_dimension']}.{ranking['period_key']}.{ranking['business_dimension']}"
    return f"ranking.{ranking['business_dimension']}.overall"


def _reference_entity(row: dict, dimension: str, metric: str, evidence_id: str, evidence: dict) -> dict:
    key = str(row.get("key"))
    if dimension == "product":
        value_id = f"product.{key}.amount" if metric == "amount" else f"product.{key}.quantity"
    else:
        value_id = f"country.{key}.amount" if metric == "amount" else f"country.{key}.quantity"
    return {
        "dimension": dimension,
        "entity_key": key,
        "entity_label": str(row.get("label", key)),
        "rank": int(row.get("rank", 0)),
        "metric": metric,
        "value": row.get("value"),
        "evidence_ids": [evidence_id] + ([value_id] if value_id in evidence else []),
    }


def _rank_comparison_rows(rows: list[dict], key_field: str, direction: str, metric: str = "amount") -> list[dict]:
    ordered = sorted(rows, key=lambda row: row.get("difference", 0), reverse=direction == "growth")
    previous = object()
    rank = 0
    ranked = []
    for position, row in enumerate(ordered, 1):
        value = row.get("difference", 0)
        if position == 1 or value != previous:
            rank = position
        key = str(row[key_field])
        ranked.append({
            "dimension": "country" if key_field == "Country" else "product",
            "entity_key": key,
            "entity_label": str(row.get("Description") or key) if key_field == "StockCode" else key,
            "rank": rank,
            "metric": metric,
            "value": value,
            "difference": value,
        })
        previous = value
    return ranked


def _comparison_period_label(plan: AnalysisPlan) -> str:
    return (
        f"{plan.period1_start} 至 {plan.period1_end} → "
        f"{plan.period2_start} 至 {plan.period2_end}"
    )


_REFERENCE_INTENTS = {
    "product_leader": ("product", "summary_rank"),
    "country_leader": ("country", "summary_rank"),
    "leader": (None, "summary_rank"),
    "product_decline_leader": ("product", "decline"),
    "country_decline_leader": ("country", "decline"),
    "decline_leader": (None, "decline"),
    "product_growth_leader": ("product", "growth"),
    "country_growth_leader": ("country", "growth"),
    "growth_leader": (None, "growth"),
}


def resolve_reference(catalog: dict | None, intent: str | None) -> dict[str, Any]:
    if not catalog or not catalog.get("boards"):
        return {"status": "clarification", "candidates": [], "question": "上次已审核结果中没有可引用的榜单。请明确指定商品编号或国家。"}
    if intent not in _REFERENCE_INTENTS:
        return {"status": "clarification", "candidates": [], "question": "我无法确定你指的是哪类上次结果。请说明是榜首商品、榜首国家，还是增长/下降榜。"}
    dimension, board_kind = _REFERENCE_INTENTS[intent]
    boards = [
        board for board in catalog["boards"]
        if (
            board["kind"] == "summary_rank" if board_kind == "summary_rank"
            else board.get("direction") == board_kind
        )
        and (dimension is None or board["dimension"] == dimension)
    ]
    candidates = []
    for board in boards:
        for row in board.get("leaders", []):
            candidate = dict(row)
            candidate.update({
                "board_id": board["board_id"], "board_evidence_id": board["evidence_id"],
                "period_label": board.get("period_label"),
                "board_dimension": board["dimension"],
                "board_kind": board["kind"],
                "direction": board.get("direction"),
            })
            candidates.append(candidate)
    if not candidates:
        label = {"product": "商品", "country": "国家"}.get(dimension, "实体")
        return {"status": "clarification", "candidates": [], "question": f"上次已审核结果中没有可引用的{label}榜首。请明确指定{label}名称或编号。"}
    if len(boards) == 1 and len(candidates) == 1:
        return {"status": "resolved", "candidate": candidates[0], "candidates": candidates}
    return {
        "status": "clarification",
        "candidates": candidates,
        "question": _candidate_question(candidates),
    }


def _candidate_question(candidates: list[dict]) -> str:
    options = []
    for item in candidates:
        dimension_name = "商品" if item["dimension"] == "product" else "国家"
        period = f"（{item['period_label']}）" if item.get("period_label") else ""
        options.append(f"{dimension_name} {item['entity_key']}{period}，第 {item['rank']} 名")
    return "该引用对应多个候选，请回复其中一个商品编号或国家名：" + "；".join(options)


def resolve_drill_choice(candidates: list[dict], answer: str) -> dict[str, Any]:
    value = " ".join(answer.strip().casefold().split())
    matching = [
        item for item in candidates
        if value in {
            " ".join(str(item["entity_key"]).casefold().split()),
            " ".join(str(item.get("entity_label", "")).casefold().split()),
            " ".join(f"{item['entity_key']} {item.get('period_label') or ''}".casefold().split()),
        }
    ]
    if len(matching) == 1:
        return {"status": "resolved", "candidate": matching[0]}
    if len(matching) > 1:
        entities = {(item.get("dimension"), str(item.get("entity_key"))) for item in matching}
        if len(entities) == 1:
            selected = dict(matching[0])
            selected["board_ids"] = list(dict.fromkeys(
                item["board_id"] for item in matching if item.get("board_id")
            ))
            selected["board_evidence_ids"] = list(dict.fromkeys(
                item["board_evidence_id"] for item in matching if item.get("board_evidence_id")
            ))
            selected["evidence_ids"] = list(dict.fromkeys(
                evidence_id
                for item in matching
                for evidence_id in item.get("evidence_ids", [])
            ))
            selected["period_labels"] = list(dict.fromkeys(
                item["period_label"] for item in matching if item.get("period_label")
            ))
            return {"status": "resolved", "candidate": selected}
        return {"status": "clarification", "question": _candidate_question(matching)}
    return {"status": "clarification", "question": _candidate_question(candidates)}


def merge_drilldown_plan(
    source_plan: AnalysisPlan,
    drill_plan: AnalysisPlan,
    candidate: dict,
    question: str = "",
) -> AnalysisPlan:
    """Use new analysis operations while inheriting relevant source scope."""
    values = drill_plan.model_dump(mode="python")
    if drill_plan.status != "executable":
        values["reference_intent"] = None
        return AnalysisPlan.model_validate(values)
    requested_full_data = any(term in question for term in ("整份数据", "全量数据", "全部数据", "从数据开始到结束"))
    if drill_plan.analysis_type == "summary" and drill_plan.time_scope == "full_data":
        # plan_question resolves full-data dates against CSV metadata in-place;
        # strip those runtime dates before validating the merged plan.
        values["start_date"] = None
        values["end_date"] = None
    if (
        drill_plan.analysis_type == "summary"
        and source_plan.analysis_type == "summary"
        and source_plan.time_scope == "date_range"
        and drill_plan.time_scope == "full_data"
        and not requested_full_data
    ):
        values.update(
            time_scope="date_range",
            start_date=source_plan.start_date,
            end_date=source_plan.end_date,
        )
    if not values.get("country_filters"):
        values["country_filters"] = list(source_plan.country_filters)
    if not values.get("product_filters"):
        values["product_filters"] = list(source_plan.product_filters)
    dimension = candidate["dimension"]
    if dimension == "product":
        values["product_filters"] = [str(candidate["entity_key"])]
    elif dimension == "country":
        values["country_filters"] = [str(candidate["entity_key"])]
    if drill_plan.analysis_type == "summary" and not values.get("ranking_order"):
        values["top_n"] = 5
    values.update(reference_intent=None, clarification_question=None, unsupported_reason=None)
    return AnalysisPlan.model_validate(values)


def reference_source_label(candidate: dict, source_plan: AnalysisPlan) -> str:
    dimension = candidate.get("dimension")
    metric = candidate.get("metric")
    entity_name = "商品" if dimension == "product" else "国家"
    metric_name = {"amount": "金额", "quantity": "销量"}.get(metric, "")
    if candidate.get("direction") == "decline":
        phrase = f"下降最多的{entity_name}"
    elif candidate.get("direction") == "growth":
        phrase = f"增长最多的{entity_name}"
    else:
        phrase = f"{metric_name}榜首" if metric_name else "榜首"
        phrase += entity_name
    scope = []
    country_names = {
        "Germany": "德国", "United Kingdom": "英国", "Netherlands": "荷兰",
        "France": "法国", "EIRE": "爱尔兰",
    }
    scope.extend(country_names.get(value, value) for value in source_plan.country_filters)
    scope.extend(source_plan.product_filters)
    scope_text = "、".join(scope)
    return f"沿用上轮{scope_text + ' ' if scope_text else ''}{phrase} {candidate['entity_key']}"


def _looks_like_result_reference(text: str) -> bool:
    return any(term in text for term in (
        "这个商品", "该商品", "榜首商品", "这个国家", "该国家", "榜首国家",
        "下降最多的国家", "降幅最大的国家", "增长最多的国家", "增长最多的商品",
        "下降最多的商品", "这个榜首", "上面的商品", "这个实体",
    ))


def apply_plan_changes(plan: AnalysisPlan, changes: dict[str, Any]) -> AnalysisPlan:
    values = plan.model_dump(mode="python")
    if "country_filters" in changes:
        values["country_filters"] = list(changes["country_filters"])
    if "rank_by" in changes:
        if not plan.ranking_order:
            raise ValueError("当前分析没有榜单，无法修改排名指标。")
        metric = changes["rank_by"]
        values["rank_by"] = metric
        values["metrics"] = list(dict.fromkeys([*values["metrics"], metric]))
    if "top_n" in changes:
        if not plan.ranking_order:
            raise ValueError("当前分析没有榜单，无法修改榜单条数。")
        values["top_n"] = int(changes["top_n"])
    values.update(status="executable", clarification_question=None, unsupported_reason=None)
    return AnalysisPlan.model_validate(values)


def serialize_plan_for_context(plan: AnalysisPlan) -> dict[str, Any]:
    values = plan.model_dump(mode="json")
    if plan.time_scope == "full_data":
        values["start_date"] = None
        values["end_date"] = None
    return values


def merge_clarified_dates(previous: AnalysisPlan, parsed: AnalysisPlan) -> AnalysisPlan:
    if previous.analysis_type != "comparison":
        raise ValueError("当前待澄清计划不是两期比较。")

    values = previous.model_dump(mode="python")
    date_fields = ("period1_start", "period1_end", "period2_start", "period2_end")
    for field_name in date_fields:
        if getattr(parsed, field_name) is not None:
            values[field_name] = getattr(parsed, field_name)

    missing = [name for name in date_fields if values[name] is None]
    values["unsupported_reason"] = parsed.unsupported_reason
    if parsed.status == "unsupported":
        values.update(status="unsupported", clarification_question=None)
    elif missing:
        values.update(
            status="clarification",
            clarification_question=missing_dates_question(values),
            unsupported_reason=None,
        )
    else:
        values.update(status="executable", clarification_question=None, unsupported_reason=None)
    return AnalysisPlan.model_validate(values)


def missing_dates_question(plan_values: dict[str, Any]) -> str:
    labels = {
        "period1_start": "前期开始日期",
        "period1_end": "前期结束日期",
        "period2_start": "后期开始日期",
        "period2_end": "后期结束日期",
    }
    missing = [labels[field] for field in labels if plan_values.get(field) is None]
    if len(missing) == 4:
        return "请补充前期和后期各自的起止日期（共两个完整区间）。"
    return "还缺少：" + "、".join(missing) + "。"


def summarize_plan_conditions(plan: AnalysisPlan, actual_date_range: dict | None = None) -> str:
    pieces = []
    if plan.analysis_type == "comparison":
        pieces.append("分析方式：两期比较")
        if plan.period1_start and plan.period1_end:
            pieces.append(f"前期 {plan.period1_start} 至 {plan.period1_end}")
        elif plan.period1_start:
            pieces.append(f"前期开始 {plan.period1_start}")
        if plan.period2_start and plan.period2_end:
            pieces.append(f"后期 {plan.period2_start} 至 {plan.period2_end}")
        elif plan.period2_start:
            pieces.append(f"后期开始 {plan.period2_start}")
    elif plan.time_scope == "full_data":
        actual_start = (actual_date_range or {}).get("start") or plan.start_date
        actual_end = (actual_date_range or {}).get("end") or plan.end_date
        if actual_start and actual_end:
            pieces.append(f"日期范围：整份数据（实际 {actual_start} 至 {actual_end}）")
        else:
            pieces.append("日期范围：整份数据")
    elif plan.start_date or plan.end_date:
        pieces.append(f"日期范围：{plan.start_date or '未定'} 至 {plan.end_date or '未定'}")

    if plan.country_filters:
        pieces.append("国家：" + "、".join(plan.country_filters))
    if plan.product_filters:
        pieces.append("商品：" + "、".join(plan.product_filters))
    metric_labels = {"amount": "正向交易金额", "quantity": "有效销售件数"}
    pieces.append("展示指标：" + "、".join(metric_labels[item] for item in plan.metrics))
    if plan.ranking_order:
        rank_label = "金额" if plan.rank_by == "amount" else "销量"
        order_label = "最高" if plan.ranking_order == "desc" else "最低"
        pieces.append(f"排名依据：按{rank_label}{order_label}排序，前 {plan.top_n} 项")
    if plan.analysis_type == "comparison":
        dimensions = {"country": "国家", "product": "商品"}
        if plan.dimensions:
            pieces.append("榜单维度：" + "、".join(dimensions[item] for item in plan.dimensions))
        if plan.direction:
            direction = {"growth": "增长", "decline": "下降", "both": "增长和下降"}[plan.direction]
            pieces.append("方向：" + direction)
    elif plan.group_by:
        names = {"day": "日", "week": "周", "month": "月", "country": "国家", "product": "商品"}
        pieces.append("分组：" + " × ".join(names[item] for item in plan.group_by))
    return "；".join(pieces) if pieces else "尚未确定分析条件。"


def _looks_like_date_answer(text: str) -> bool:
    has_year = bool(re.search(r"20\d{2}", text))
    has_range = bool(re.search(r"\d{1,2}\s*(?:日|号)?\s*(?:至|到|[-–—~～])\s*\d{1,2}", text))
    has_month = bool(re.search(r"\d{1,2}\s*月", text))
    return has_year and has_month and has_range


def _is_self_contained_question(text: str) -> bool:
    return any(term in text for term in (
        "是什么", "有哪些", "多少", "比较", "列出", "汇总", "分析", "说明",
        "哪一周", "哪些商品", "哪些国家", "卖得更好", "卖得最好", "最高的",
    ))


def _country_change(text: str) -> str | None:
    match = re.search(r"(?:那|换成|改成|国家改为|国家换成)\s*([^，。！？?！!]+?)(?:呢)?[？?。！!]*$", text)
    if not match:
        return None
    return match.group(1).strip()


def _rank_metric_change(text: str) -> str | None:
    if re.search(r"(?:改|换|按)\s*(?:按)?\s*(?:销售额|金额|交易额).{0,8}(?:排|排名|排序)?", text):
        return "amount"
    if re.search(r"(?:改|换|按)\s*(?:按)?\s*(?:销量|件数|销售件数|数量).{0,8}(?:排|排名|排序)?", text):
        return "quantity"
    return None


def _top_n_change(text: str) -> int | None:
    match = re.search(r"(?:只看|看|列出|显示)?\s*前\s*(\d{1,3}|[一二三四五六七八九十]+)", text, re.IGNORECASE)
    if not match:
        return None
    token = match.group(1)
    if token.isdigit():
        value = int(token)
    elif token == "十":
        value = 10
    elif "十" in token:
        tens, _, ones = token.partition("十")
        value = (int(tens) if tens else 1) * 10 + ({"": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}[ones])
    else:
        value = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}[token]
    return value if 1 <= value <= 100 else None


def _looks_like_followup_fragment(text: str) -> bool:
    return bool(re.match(r"^(?:那|改|换|只看|还是|改成|换成)", text))
