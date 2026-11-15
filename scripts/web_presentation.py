"""Pure presentation helpers for the Streamlit retail workflow UI."""

from __future__ import annotations

from datetime import date
from typing import Any


def format_money(value: float) -> str:
    return f"-£{abs(value):,.2f}" if value < 0 else f"£{value:,.2f}"


def format_signed_money(value: float) -> str:
    sign = "-" if value < 0 else "+"
    return f"{sign}£{abs(value):,.2f}"


def format_signed_percent(value: float | None) -> str:
    if value is None:
        return "不可计算"
    percent = value * 100
    sign = "-" if percent < 0 else "+"
    return f"{sign}{abs(percent):.2f}%"


def run_status_label(run: dict) -> str:
    status = run.get("status")
    if status == "unsupported":
        return "当前能力不支持"
    if status == "approved":
        return "审核通过"
    if status == "clarification":
        if run.get("clarification_kind") == "missing_analysis":
            return "需补充分析要求"
        if run.get("clarification_kind") == "drill_choice":
            return "需选择引用对象"
        state = run.get("state") or {}
        plan = state.get("plan")
        if plan and (plan.country_filters or plan.product_filters) and not state.get("filter_scope"):
            return "需确认筛选条件"
        return "需补充日期"
    if status == "running":
        return "运行中"
    state = run.get("state") or {}
    review = state.get("review_result")
    if review and review.decision == "return":
        return "审核未通过"
    return "运行失败"


def _date_text(value: date | None) -> str:
    return value.isoformat() if value is not None else "待补充"


def plan_summary(plan: Any) -> str:
    dimension_names = {"country": "国家", "product": "商品"}
    direction_names = {"growth": "增长", "decline": "下降", "both": "增长和下降", None: "无榜单"}
    if getattr(plan, "analysis_type", "comparison") == "summary":
        group_names = {"day": "日", "week": "周", "month": "月", "country": "国家", "product": "商品"}
        metric_names = {"amount": "正向交易金额", "quantity": "有效销售件数"}
        scope = "全量数据" if plan.time_scope == "full_data" else f"{_date_text(plan.start_date)} 至 {_date_text(plan.end_date)}"
        group = " × ".join(group_names.get(field, field) for field in plan.group_by) if plan.group_by else "总额"
        metrics = getattr(plan, "metrics", None) or ["amount"]
        metric_text = "、".join(metric_names.get(metric, metric) for metric in metrics)
        ranking = ""
        if plan.ranking_order:
            rank_metric = getattr(plan, "rank_by", "amount") or "amount"
            ranking = f"；按{metric_names.get(rank_metric, rank_metric)}{'最高' if plan.ranking_order == 'desc' else '最低'}排序，前 {plan.top_n} 项"
        return (
            f"汇总范围 {scope}；指标 {metric_text}；分组 {group}{ranking}。"
            f" 国家条件 {('、'.join(plan.country_filters) or '全部')}；"
            f"商品条件 {('、'.join(plan.product_filters) or '全部')}。"
        )
    dimensions = "、".join(dimension_names[value] for value in plan.dimensions) or "无"
    return (
        f"前期 {_date_text(plan.period1_start)} 至 {_date_text(plan.period1_end)}；"
        f"后期 {_date_text(plan.period2_start)} 至 {_date_text(plan.period2_end)}；"
        f"维度 {dimensions}；方向 {direction_names[plan.direction]}；榜单数 {plan.top_n}。"
    )


def filter_scope_summary(scope: dict | None) -> str:
    if not scope:
        return ""
    countries = "、".join(scope.get("countries", [])) or "全部国家"
    products = "、".join(scope.get("products", [])) or "全部商品"
    return f"筛选：国家 {countries}；商品 {products}。"


_COUNTRY_DISPLAY_NAMES = {
    "Germany": "德国",
    "United Kingdom": "英国",
    "Netherlands": "荷兰",
    "France": "法国",
    "EIRE": "爱尔兰",
}


def summary_scope_label(plan: Any, result: dict) -> str:
    scope = result.get("filter_scope") or {}
    countries = plan.country_filters or scope.get("countries", [])
    products = plan.product_filters or scope.get("products", [])
    parts = [_COUNTRY_DISPLAY_NAMES.get(value, value) for value in countries]
    if products:
        parts.append("商品 " + "、".join(products))
    return " · ".join(parts) if parts else "全范围"


def summary_total_label(plan: Any, result: dict) -> str:
    scope = result.get("filter_scope") or {}
    countries = plan.country_filters or scope.get("countries", [])
    products = plan.product_filters or scope.get("products", [])
    if len(countries) == 1 and not products:
        return f"{_COUNTRY_DISPLAY_NAMES.get(countries[0], countries[0])}正向交易总额"
    if countries or products:
        return "筛选范围正向交易总额"
    return "全范围正向交易总额"


def summary_result_title(plan: Any, result: dict) -> str:
    date_range = result.get("date_range", {})
    start = date_range.get("start", plan.start_date)
    end = date_range.get("end", plan.end_date)
    if hasattr(start, "isoformat"):
        start = start.isoformat()
    if hasattr(end, "isoformat"):
        end = end.isoformat()
    group_names = {"day": "每日", "week": "每周", "month": "每月", "country": "国家", "product": "商品"}
    group_by = result.get("group_by")
    group = group_names.get(group_by, "总额")
    scope = result.get("filter_scope") or {}
    countries = plan.country_filters or scope.get("countries", [])
    products = plan.product_filters or scope.get("products", [])
    country_scope = "、".join(_COUNTRY_DISPLAY_NAMES.get(value, value) for value in countries)
    ranking = bool(plan.ranking_order)
    returned_suffix = ""
    if ranking:
        ranking_label = f"金额前 {plan.top_n}"
        if plan.ranking_order == "asc":
            ranking_label += "（金额最低）"
        actual_count = len(result.get("groups", []))
        if actual_count < plan.top_n:
            returned_suffix = f"（返回 {actual_count} 项）"
            ranking_label += returned_suffix
    elif group_by == "week":
        ranking_label = "每周正向交易金额趋势"
    else:
        ranking_label = f"按{group}汇总"
    if group_by == "product" and products:
        scope_label = country_scope
        ranking = f"商品 {'、'.join(products)}金额前 {plan.top_n}{returned_suffix}"
        if plan.ranking_order == "asc":
            ranking += "（金额最低）"
        result_label = f"{scope_label}{ranking}" if scope_label else ranking
    else:
        scope_parts = [country_scope] if country_scope else []
        if products:
            scope_parts.append("商品筛选 " + "、".join(products))
        scope_label = " · ".join(scope_parts)
        if ranking:
            result_label = f"{scope_label}{group}{ranking_label}"
        elif scope_label:
            result_label = f"{scope_label} · {ranking_label}"
        else:
            result_label = ranking_label
    return f"{start} 至 {end} · {result_label}"


def summary_leader(plan: Any, result: dict) -> tuple[str, str] | None:
    groups = result.get("groups", [])
    if not plan.ranking_order or not groups:
        return None
    item = groups[0]
    group_by = result.get("group_by")
    names = {"product": "商品", "country": "国家", "day": "日期", "week": "周", "month": "月份"}
    return f"榜首{names.get(group_by, '分组')} {item['key']}", format_money(item["amount"])


def summary_ranking_rows(plan: Any, result: dict) -> list[dict]:
    group_by = result.get("group_by")
    rows = []
    for rank, item in enumerate(result.get("groups", []), 1):
        if group_by == "product":
            name = str(item["label"])
            code = str(item["key"])
            description = name[len(code):].lstrip(" -·")
            rows.append({
                "排名": rank,
                "商品编号": code,
                "商品名称": description,
                "正向交易金额": format_money(item["amount"]),
            })
        elif group_by == "country":
            rows.append({
                "排名": rank,
                "国家": item["label"],
                "正向交易金额": format_money(item["amount"]),
            })
        else:
            row = {
                "排名": rank,
                "分组": item["label"],
                "正向交易金额": format_money(item["amount"]),
            }
            if item.get("coverage") == "partial":
                row["覆盖情况"] = "部分覆盖自然周期"
            rows.append(row)
    return rows


def board_rows(result: dict, dimension: str, direction: str, top_n: int, metric: str = "amount") -> tuple[list[dict], str | None]:
    result_keys = {
        ("country", "decline"): ("country_decreases", "Country"),
        ("country", "growth"): ("country_increases", "Country"),
        ("product", "decline"): ("product_decreases", "StockCode"),
        ("product", "growth"): ("product_increases", "StockCode"),
    }
    result_key, key_field = result_keys[(dimension, direction)]
    rows = []
    for item in result.get(result_key, []):
        label = str(item[key_field])
        if dimension == "product" and item.get("Description"):
            label = f"{label} · {item['Description']}"
        difference = (format_signed_money(item["difference"]) if metric == "amount" else
                      f"{'-' if item['difference'] < 0 else '+'}{abs(item['difference']):,.0f} 件")
        rows.append({
            "国家" if dimension == "country" else "商品": label,
            f"前期{'金额' if metric == 'amount' else '销量'}": format_metric_value(metric, item["period1"]),
            f"后期{'金额' if metric == 'amount' else '销量'}": format_metric_value(metric, item["period2"]),
            "差额": difference,
        })
    note = f"请求前 {top_n} 项，本次有 {len(rows)} 项符合条件。" if len(rows) < top_n else None
    return rows, note


def completed_node_event(
    node: str,
    update: dict,
    prior_events: list[dict],
    *,
    plan=None,
    draft_history: list[Any] | None = None,
    review_history: list[Any] | None = None,
) -> dict:
    names = {
        "plan": "规划智能体",
        "calculate": "本地计算",
        "draft_report": "分析智能体",
        "review": "审核智能体",
    }
    active = next((event for event in reversed(prior_events)
                   if event["node"] == node and event.get("status") == "running"), None)
    attempt = active.get("attempt", 1) if active else 1 + max(
        (event.get("attempt", 1) for event in prior_events
         if event["node"] == node and event.get("status") == "completed"),
        default=0,
    )
    label = names.get(node, node)

    if node == "plan":
        plan = update["plan"]
        detail = plan_summary(plan)
        detail += " " + filter_scope_summary(update.get("filter_scope"))
        clarification = update.get("clarification_question")
        if plan.status == "clarification" and clarification:
            dates_missing = plan.analysis_type == "comparison" and any(
                value is None for value in (plan.period1_start, plan.period1_end, plan.period2_start, plan.period2_end)
            )
            if dates_missing:
                detail += " 日期不完整，需要补充用户信息。"
            else:
                detail += f" 需要确认：{clarification}"
        elif plan.status == "unsupported":
            detail += " 当前能力不支持，工作流在规划后结束。"
    elif node == "calculate":
        result = update["result"]
        if result.get("analysis_type") == "summary":
            groups = result.get("groups", [])
            metrics = result.get("metrics", ["amount"])
            totals = result.get("metric_totals", {"amount": result.get("summary", {}).get("total", 0)})
            rendered_totals = []
            for metric in metrics:
                if metric not in totals:
                    continue
                label = "正向交易金额" if metric == "amount" else "有效销售件数"
                rendered_totals.append(f"{label} {format_metric_value(metric, totals[metric])}")
            detail = "；".join(rendered_totals) + f"；生成 {len(groups)} 行分组结果。"
            if groups and len(result.get("group_by_fields", [])) == 1 and plan and plan.ranking_order:
                metric = result.get("rank_by", "amount")
                value = groups[0].get("values", {}).get(metric, groups[0].get(metric, 0.0))
                detail += f" 榜首：{groups[0].get('label', groups[0].get('key'))}（{format_metric_value(metric, value)}）。"
            fields = result.get("group_by_fields", [])
            if result.get("rankings") and len(fields) == 2:
                rank_leaders = []
                for ranking in result["rankings"]:
                    names = "、".join(str(item["key"]) for item in ranking.get("leaders", []))
                    rank_leaders.append(f"{ranking['period_label']}榜首 {names}")
                business_name = {"country": "国家", "product": "商品"}.get(fields[1], fields[1])
                time_name = {"day": "日", "week": "周", "month": "月"}.get(fields[0], fields[0])
                detail += f" 各{time_name}各{business_name}榜首：" + "；".join(rank_leaders) + "。"
            scope_text = filter_scope_summary(result.get("filter_scope"))
            if scope_text:
                detail += " " + scope_text
            scope_message = result.get("filter_scope", {}).get("message")
            if scope_message:
                detail += " " + scope_message
        else:
            metric_results = result.get("metric_results") or {result.get("metric", "amount"): result}
            rendered_metrics = []
            for metric, metric_result in metric_results.items():
                metric_name = "正向交易金额" if metric == "amount" else "有效销售件数"
                if metric == "amount":
                    delta = format_signed_money(metric_result["difference"])
                else:
                    value = metric_result["difference"]
                    sign = "-" if value < 0 else "+"
                    delta = f"{sign}{abs(value):,.0f} 件"
                rendered_metrics.append(
                    f"{metric_name}：{format_metric_value(metric, metric_result['period1_total'])} → "
                    f"{format_metric_value(metric, metric_result['period2_total'])}；差额 {delta}；"
                    f"变化率 {format_signed_percent(metric_result['change_rate'])}"
                )
            detail = "；".join(rendered_metrics) + "。"
            scope_text = filter_scope_summary(result.get("filter_scope"))
            if scope_text:
                detail += " " + scope_text
            scope_message = result.get("filter_scope", {}).get("message")
            if scope_message:
                detail += " " + scope_message
        if plan and plan.dimensions:
            counts = []
            for dimension, title in (("country", "国家榜"), ("product", "商品榜")):
                if dimension not in plan.dimensions:
                    continue
                prefix = "country" if dimension == "country" else "product"
                growth_count = len(result.get(f"{prefix}_increases", [])) if plan.direction in {"growth", "both"} else 0
                decline_count = len(result.get(f"{prefix}_decreases", [])) if plan.direction in {"decline", "both"} else 0
                counts.append(f"{title}增长 {growth_count} 项、下降 {decline_count} 项")
            if counts:
                detail += " " + "；".join(counts) + "。"
    elif node == "draft_report":
        if attempt > 1:
            label = "报告返修"
        report = update["report_draft"]
        first_conclusion = report.conclusions[0].text if report.conclusions else "已生成初稿。"
        detail = f"{report.title}：{first_conclusion}"
        draft_snapshot = _draft_snapshot(report)
    elif node == "review":
        label = "审核智能体 · 第 1 次审核" if attempt == 1 else f"第 {attempt} 次审核"
        review = update["review_result"]
        if review.decision == "approve":
            detail = "首次审核通过。" if attempt == 1 else "复核通过，返修稿已通过审核。"
            details = None
        else:
            reasons = []
            short_reasons = []
            for issue in review.issues:
                evidence = "、".join(issue.evidence_ids)
                reasons.append(f"退回：{issue.problem}（证据 {evidence}）；建议：{issue.requested_change}")
                short_problem = issue.problem.strip()
                if len(short_problem) > 72:
                    short_problem = short_problem[:72].rstrip("，。；、") + "…"
                short_reasons.append(f"退回：{short_problem}" if attempt == 1 else short_problem)
            details = " ".join(reasons)
            if attempt == 1:
                detail = "；".join(short_reasons) or "审核退回，请查看详细意见。"
            else:
                detail = "复核仍退回：" + ("；".join(short_reasons) or "请查看审核意见。")
        review_snapshot = _review_snapshot(review)
        if review_history and draft_history:
            review_comparison = review_revision_details(draft_history, review_history, attempt=attempt)
            if attempt > 1:
                details = review_comparison
    else:
        detail = "节点完成。"

    event = {"node": node, "label": label, "detail": detail, "status": "completed", "attempt": attempt}
    if node == "review" and details:
        event["details"] = details
    if node == "draft_report":
        event["draft_snapshot"] = draft_snapshot
    if node == "review":
        event["review_snapshot"] = review_snapshot
        if review_history and draft_history:
            event["review_comparison"] = review_comparison
    return event


def _draft_snapshot(draft: Any) -> dict:
    return {
        "title": draft.title,
        "conclusions": [
            {"index": index, "text": item.text, "evidence_ids": list(getattr(item, "evidence_ids", []))}
            for index, item in enumerate(draft.conclusions)
        ],
    }


def _review_snapshot(review: Any, drafts: list[dict] | None = None) -> dict:
    conclusions = drafts[-1]["conclusions"] if drafts else []
    issues = []
    for issue in review.issues:
        target = next(
            (item for item in conclusions if item["index"] == issue.conclusion_index),
            None,
        )
        issues.append({
            "conclusion_index": issue.conclusion_index,
            "target_conclusion": target,
            "evidence_ids": list(issue.evidence_ids),
            "problem": issue.problem,
            "requested_change": issue.requested_change,
        })
    return {"decision": review.decision, "issues": issues}


def review_revision_details(
    draft_history: list[Any], review_history: list[Any], *, attempt: int = 2,
) -> dict | None:
    """Keep the two drafts and reviews distinct unless a stable link exists."""
    if not draft_history or not review_history:
        return None
    initial = _draft_snapshot(draft_history[0])
    first_review = _review_snapshot(review_history[0], [initial])
    revision = _draft_snapshot(draft_history[1]) if len(draft_history) > 1 else None
    second_review = _review_snapshot(review_history[1], [revision]) if len(review_history) > 1 and revision else None
    for issue in first_review["issues"]:
        target = issue.get("target_conclusion")
        if target is None:
            continue
        explicit_removal = any(term in issue["requested_change"] for term in ("删除该结论", "移除该结论"))
        if explicit_removal and revision:
            if not any(item["text"] == target["text"] for item in revision["conclusions"]):
                issue["disposition"] = "已删除"
    return {
        "first_draft": initial,
        "first_review": first_review,
        "revision_draft": revision,
        "second_review": second_review,
        "pairing": "unpaired",
        "pairing_note": (
            "当前结论没有稳定编号；初稿和返修稿分别列出，不推断逐条对应关系。"
            if revision else "首次审核记录；如发生返修，将分别保留返修稿和复核结果。"
        ),
    }


def render_review_comparison(comparison: dict, container) -> None:
    """Render saved draft/review snapshots inside the caller's collapsed expander."""
    def render_draft(title: str, draft: dict | None) -> None:
        container.markdown(f"**{title}**")
        if draft is None:
            container.caption("尚无返修稿。")
            return
        container.caption(draft.get("title", ""))
        for item in draft.get("conclusions", []):
            suffix = f"（{item['disposition']}）" if item.get("disposition") == "已删除" else ""
            container.write(f"{item['index'] + 1}. {item['text']}{suffix}")
            if item.get("evidence_ids"):
                container.caption("证据编号：" + " · ".join(item["evidence_ids"]))

    def render_review(title: str, review: dict | None) -> None:
        container.markdown(f"**{title}**")
        if review is None:
            container.caption("尚无复核结果。")
            return
        decision = {"approve": "通过", "return": "退回", "block": "阻止发布"}.get(review["decision"], review["decision"])
        container.write(decision)
        for issue in review.get("issues", []):
            if issue.get("target_conclusion"):
                target = issue["target_conclusion"]
                container.write(f"原结论（第 {target['index'] + 1} 条）：{target['text']}")
                if target.get("evidence_ids"):
                    container.caption("原结论证据编号：" + " · ".join(target["evidence_ids"]))
            if issue.get("disposition") == "已删除":
                container.caption("该结论已删除。")
            container.write("退回理由：" + issue["problem"])
            if issue.get("evidence_ids"):
                container.caption("审核指向证据：" + " · ".join(issue["evidence_ids"]))
            container.write("修改建议：" + issue["requested_change"])

    render_draft("初稿结论", comparison.get("first_draft"))
    render_review("首次审核", comparison.get("first_review"))
    if comparison.get("revision_draft"):
        render_draft("返修后结论", comparison["revision_draft"])
    else:
        container.caption("初稿和审核指向已定位；目前尚未生成返修稿。")
    render_review("第二次审核", comparison.get("second_review"))
    container.caption(comparison.get("pairing_note", ""))


_TRACE_STAGES = (
    ("plan", "规划智能体"),
    ("calculate", "本地计算"),
    ("draft_report", "分析智能体"),
    ("review", "审核智能体"),
)


def initial_trace() -> list[dict]:
    return [
        {"node": node, "label": f"{label} · 待执行", "base_label": label,
         "detail": "待执行", "status": "pending", "attempt": 1}
        for node, label in _TRACE_STAGES
    ]


def start_node_event(trace: list[dict], event: dict) -> dict:
    node, attempt = event["node"], event.get("attempt", 1)
    existing = next((item for item in trace if item["node"] == node and item.get("attempt") == attempt), None)
    default_labels = dict(_TRACE_STAGES)
    if existing is None:
        if node == "draft_report":
            label = "报告返修"
        elif node == "review":
            label = f"第 {attempt} 次审核"
        else:
            label = default_labels.get(node, node)
        existing = {"node": node, "attempt": attempt, "base_label": label, "detail": "",
                    "status": "pending"}
        trace.append(existing)
    existing["status"] = "running"
    existing["label"] = f"{existing['base_label']} · 进行中"
    existing["detail"] = "当前节点正在执行。"
    return existing


def complete_trace_event(trace: list[dict], node: str, attempt: int, completed: dict) -> None:
    target = next((item for item in reversed(trace)
                   if item["node"] == node and item.get("attempt") == attempt), None)
    if target is None:
        target = start_node_event(trace, {"node": node, "attempt": attempt})
    target.update({key: value for key, value in completed.items() if key not in {"node", "attempt"}})
    target["status"] = "completed"
    target["label"] = target.get("base_label", completed.get("label", node))


def skip_pending_trace(trace: list[dict], reason: str) -> None:
    reason = reason.strip()
    if len(reason) > 72:
        reason = reason[:72].rstrip("，。；、") + "…"
    for event in trace:
        if event.get("status") == "pending":
            event["status"] = "skipped"
            event["label"] = f"{event.get('base_label', event['node'])} · 未执行"
            event["detail"] = f"未执行：{reason}"


def fail_trace_event(trace: list[dict], node: str, attempt: int, error: str) -> None:
    target = next((item for item in reversed(trace)
                   if item["node"] == node and item.get("attempt") == attempt), None)
    if target is None:
        target = start_node_event(trace, {"node": node, "attempt": attempt})
    target["status"] = "failed"
    target["label"] = f"{target.get('base_label', node)} · 失败"
    target["detail"] = error or "节点执行失败。"
    skip_pending_trace(trace, f"{node} 节点失败")


def conclusion_groups(report: Any, evidence: dict[str, dict], dimensions: list[str], direction: str):
    """Render the total overview from calculations and group narrative by evidence."""
    conclusions = list(report.conclusions)
    period1 = evidence.get("total.period1", {}).get("value")
    period2 = evidence.get("total.period2", {}).get("value")
    change_pct = evidence.get("total.change_pct", {}).get("value")
    delta = evidence.get("total.delta", {}).get("value")
    if period1 is None or period2 is None or delta is None:
        overview = None
    else:
        movement = "增长" if delta > 0 else "下降" if delta < 0 else "持平"
        rate_text = format_signed_percent(change_pct / 100) if change_pct is not None else "不可计算"
        overview = (
            f"正向交易金额从 {format_money(period1)} {movement}至 {format_money(period2)}，"
            f"差额 {format_signed_money(delta)}，变化率 {rate_text}。"
        )
    requested_directions = ["growth", "decline"] if direction == "both" else [direction]
    grouped: dict[tuple[str, str], list[Any]] = {}
    for conclusion in conclusions:
        categories = set()
        for identifier in conclusion.evidence_ids:
            dimension = identifier.split(".", 1)[0]
            if dimension not in dimensions or dimension not in {"country", "product"}:
                continue
            item = evidence.get(identifier)
            if not identifier.endswith(".delta") or item is None:
                continue
            value = item.get("value")
            if value is None or value == 0:
                continue
            category_direction = "growth" if value > 0 else "decline"
            if category_direction in requested_directions:
                categories.add((dimension, category_direction))
        if len(categories) > 1:
            directions = {category_direction for _, category_direction in categories}
            direction_key = next(iter(directions)) if len(directions) == 1 else "both"
            grouped.setdefault(("mixed", direction_key), []).append(conclusion)
        else:
            for category in categories:
                grouped.setdefault(category, []).append(conclusion)

    direction_names = {"growth": "增长", "decline": "下降"}
    dimension_names = {"country": "国家", "product": "商品"}
    ordered = []
    for dimension in dimensions:
        for category_direction in requested_directions:
            conclusions_for_group = grouped.get((dimension, category_direction), [])
            if conclusions_for_group:
                ordered.append(
                    (
                        dimension,
                        category_direction,
                        f"{dimension_names[dimension]} · {direction_names[category_direction]}",
                        conclusions_for_group,
                    )
                )
    for category_direction in ("growth", "decline", "both"):
        conclusions_for_group = grouped.get(("mixed", category_direction), [])
        if conclusions_for_group:
            title = "多维度 · 整体观察" if category_direction == "both" else f"多维度 · {direction_names[category_direction]}"
            ordered.append(("mixed", category_direction, title, conclusions_for_group))
    return overview, ordered


# Metric-aware presentation helpers. Kept pure so CLI and Streamlit render the
# same structured result without touching model or calculation state.
def format_metric_value(metric: str, value: float) -> str:
    return format_money(value) if metric == "amount" else f"{value:,.0f} 件"


def summary_total_label(plan: Any, result: dict) -> str:
    metric = result.get("rank_by", "amount")
    label = "正向交易金额" if metric == "amount" else "有效销售件数"
    scope = result.get("filter_scope") or {}
    countries = plan.country_filters or scope.get("countries", [])
    products = plan.product_filters or scope.get("products", [])
    country_names = {"Germany": "德国", "United Kingdom": "英国", "Netherlands": "荷兰", "France": "法国", "EIRE": "爱尔兰"}
    if len(countries) == 1 and not products:
        return f"{country_names.get(countries[0], countries[0])}{label}"
    return f"{'筛选范围' if countries or products else '全范围'}{label}"


def summary_result_title(plan: Any, result: dict) -> str:
    date_range = result.get("date_range", {})
    start, end = date_range.get("start", plan.start_date), date_range.get("end", plan.end_date)
    start = start.isoformat() if hasattr(start, "isoformat") else start
    end = end.isoformat() if hasattr(end, "isoformat") else end
    names = {"day": "日", "week": "周", "month": "月", "country": "国家", "product": "商品"}
    fields = result.get("group_by_fields", plan.group_by)
    grouping = " × ".join(names.get(item, item) for item in fields) or "总额"
    metric = result.get("rank_by", getattr(plan, "rank_by", "amount"))
    metric_name = "金额" if metric == "amount" else "销量"
    scope = result.get("filter_scope") or {}
    countries = plan.country_filters or scope.get("countries", [])
    products = plan.product_filters or scope.get("products", [])
    country_names = {"Germany": "德国", "United Kingdom": "英国", "Netherlands": "荷兰", "France": "法国", "EIRE": "爱尔兰"}
    scope_label = "、".join(country_names.get(item, item) for item in countries)
    if products:
        scope_label = "、".join(filter(None, [scope_label, "商品 " + "、".join(products)]))
    if plan.ranking_order:
        order = "最高" if plan.ranking_order == "desc" else "最低"
        actual = max((ranking.get("actual_count", 0) for ranking in result.get("rankings", [])), default=len(result.get("groups", [])))
        suffix = f"（返回 {actual} 项）" if actual < plan.top_n else ""
        ranking_title = (f"每周{metric_name}前 {plan.top_n}" if fields == ["week"]
                         else f"{grouping}{metric_name}前 {plan.top_n}")
        if order == "最低":
            ranking_title = ranking_title.replace("前 ", "最低前 ")
        title = f"{scope_label}{ranking_title}{suffix}"
    elif fields == ["week"]:
        title = "每周正向交易金额趋势" if metric == "amount" else "每周有效销售件数趋势"
    else:
        title = f"{scope_label}{grouping}汇总"
    return f"{start} 至 {end} · {title}"


def summary_leader(plan: Any, result: dict) -> tuple[str, str] | None:
    if not plan.ranking_order or not result.get("groups"):
        return None
    metric = result.get("rank_by", "amount")
    rankings = [item for item in result.get("rankings", []) if item.get("metric") == metric]
    if rankings and len(result.get("group_by_fields", [])) == 2:
        descriptions = []
        all_winners = []
        for ranking in rankings:
            leaders = ranking.get("leaders", [])
            if not leaders:
                continue
            names = "、".join(str(item["key"]) for item in leaders)
            all_winners.append(names)
            value = format_metric_value(metric, leaders[0].get("value", 0))
            descriptions.append(f"{ranking.get('period_label', '')}：{names} · {value}")
        if descriptions:
            period_name = _group_label(result['group_by_fields'][0])
            if len(set(all_winners)) == 1:
                return f"各{period_name}榜首", f"{all_winners[0]}（{len(descriptions)} 期）"
            return f"各{period_name}榜首", f"{len(descriptions)} 期，见下表"
    if rankings and rankings[0].get("leaders"):
        leaders = rankings[0]["leaders"]
        value = leaders[0].get("value", 0)
        group_by = result.get("group_by") or (result.get("group_by_fields") or ["group"])[-1]
        names = {"product": "商品", "country": "国家", "week": "周", "month": "月份", "day": "日期"}
        label = f"榜首{names.get(group_by, '分组')} {leaders[0]['key']}"
        rendered_value = format_metric_value(metric, value)
        if len(leaders) > 1:
            label = f"榜首{names.get(group_by, '分组')}（并列）"
            rendered_value = f"{'、'.join(str(row['key']) for row in leaders)} · {rendered_value}"
        return label, rendered_value
    item = result["groups"][0]
    value = item.get("values", {}).get(metric, item.get(metric, 0))
    group_by = result.get("group_by") or (result.get("group_by_fields") or ["group"])[0]
    names = {"product": "商品", "country": "国家", "week": "周", "month": "月份", "day": "日期"}
    return f"榜首{names.get(group_by, '分组')} {item.get('business_key', item.get('key'))}", format_metric_value(metric, value)


def _group_label(field: str) -> str:
    return {"day": "日", "week": "周", "month": "月", "country": "国家", "product": "商品"}.get(field, field)


def summary_ranking_rows(plan: Any, result: dict) -> list[dict]:
    fields = result.get("group_by_fields", plan.group_by)
    metrics = result.get("metrics", ["amount"])
    business_dimension = result.get("group_by_business")
    rows = []
    for rank, item in enumerate(result.get("groups", []), 1):
        row: dict[str, Any] = {} if len(fields) == 2 else {"排名": item.get("rank", rank)}
        if len(fields) == 2:
            row["时间范围"] = item.get("period_label", item.get("period_key"))
            row["国家" if business_dimension == "country" else "商品编号"] = item.get("business_key")
            if business_dimension == "product":
                row["商品名称"] = item.get("business_label", "").partition(" - ")[2]
        else:
            row["分组"] = item.get("label", item.get("key"))
        for metric in metrics:
            label = "正向交易金额" if metric == "amount" else "有效销售件数"
            value = item.get("values", {}).get(metric, item.get(metric, 0))
            row[f"{label}（{'£' if metric == 'amount' else '件'}）"] = format_metric_value(metric, value)
        if item.get("coverage") == "partial":
            row["覆盖情况"] = "部分覆盖自然周期"
        rows.append(row)
    if len(fields) == 2 and plan.ranking_order:
        return rows  # Full detail remains visible; per-period leaders are shown separately.
    return rows


def ranking_display_note(ranking: dict) -> str | None:
    requested = int(ranking.get("requested_count", 0))
    actual = int(ranking.get("actual_count", len(ranking.get("rows", []))))
    if not requested:
        return None
    if actual < requested:
        return f"请求前 {requested} 名，本次有 {actual} 项符合条件。"
    if actual > requested:
        rows = ranking.get("rows", [])
        tied_rank = rows[requested - 1].get("rank", requested) if len(rows) >= requested else requested
        return f"请求前 {requested} 名，第 {tied_rank} 名并列，实际展示 {actual} 项。"
    return None
