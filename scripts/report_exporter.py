from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile
import uuid
import re

from report_generator import render_report_numbers
from result_chart import render_result_chart, summary_chart_payload
from web_presentation import ranking_display_note


class ReportExportError(RuntimeError):
    pass


def _unique_output_dir(outputs_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return outputs_dir / f"run_{stamp}_{uuid.uuid4().hex[:8]}"


def _safe_markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _positive_sign(value: str) -> str:
    if value.startswith("-") or value == "不可计算":
        return value
    return "+" + value


def _board_table(title: str, rows: list[dict]) -> str:
    if not rows:
        return f"### {title}\n\n无符合方向条件的项目。\n"
    lines = [
        f"### {title}",
        "",
        "| 维度项 | 前期金额 | 后期金额 | 差额 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        label = row["name"]
        if row.get("description"):
            label = f"{label} - {row['description']}"
        difference = _positive_sign(row["difference"])
        lines.append(
            f"| {_safe_markdown_cell(label)} | {row['period1']} | "
            f"{row['period2']} | {difference} |"
        )
    return "\n".join(lines) + "\n"


def _scope_markdown_lines(scope: dict) -> list[str]:
    countries = "、".join(scope.get("countries", [])) or "全部国家"
    products = "、".join(scope.get("products", [])) or "全部商品"
    lines = [f"- 国家筛选：{countries}", f"- 商品筛选：{products}"]
    if scope.get("message"):
        lines.append(f"- 记录情况：{scope['message']}")
    return lines


def _summary_total_label(scope: dict) -> str:
    countries = scope.get("countries", [])
    products = scope.get("products", [])
    country_names = {"Germany": "德国", "United Kingdom": "英国", "Netherlands": "荷兰", "France": "法国", "EIRE": "爱尔兰"}
    if len(countries) == 1 and not products:
        return f"{country_names.get(countries[0], countries[0])}正向交易总额"
    if products:
        return "商品筛选范围正向交易总额"
    if countries:
        return "筛选范围正向交易总额"
    return "全范围正向交易总额"


def _make_markdown(state: dict) -> str:
    plan = state["plan"]
    result = state["result"]
    report = state["final_report"]
    summary = render_report_numbers(result, plan)
    if result.get("analysis_type") == "summary" or plan.analysis_type == "summary":
        return _make_summary_markdown(state, summary)
        # Legacy renderer retained below as a compatibility reference.
        date_range = summary["date_range"]
        group_by = summary.get("group_by")
        group_names = {"day": "日期", "week": "周", "month": "月份", "country": "国家", "product": "商品"}
        lines = [
            f"# {report.title}",
            "",
        ]
        ranking = bool(group_by and summary.get("ranking_order"))
        if ranking:
            order = "最高" if summary["ranking_order"] == "desc" else "最低"
            lines.extend([
                f"## {group_names.get(group_by, group_by)}金额排名前 {summary['top_n']}（金额{order}）",
                "",
            ])
            if group_by in {"day", "week", "month"}:
                lines.extend([
                    "| 排名 | 项目 | 日期范围 | 覆盖情况 | 正向交易金额 | 证据编号 |",
                    "| ---: | --- | --- | --- | ---: | --- |",
                ])
            else:
                lines.extend([
                    "| 排名 | 项目 | 正向交易金额 | 证据编号 |",
                    "| ---: | --- | ---: | --- |",
                ])
            for index, (row, rendered) in enumerate(zip(result.get("groups", []), summary.get("groups", [])), 1):
                token = re.sub(r"[^A-Za-z0-9_-]+", "_", str(row["key"])).strip("_") or "unknown"
                evidence_id = f"{group_by}.{token}.amount"
                if group_by in {"day", "week", "month"}:
                    period = f"{row['start']} 至 {row['end']}"
                    coverage = "部分覆盖自然周期" if row.get("coverage") == "partial" else "完整"
                    lines.append(
                        f"| {index} | {_safe_markdown_cell(row['label'])} | {period} | {coverage} | "
                        f"{rendered['amount']} | `{evidence_id}` |"
                    )
                else:
                    lines.append(
                        f"| {index} | {_safe_markdown_cell(row['label'])} | "
                        f"{rendered['amount']} | `{evidence_id}` |"
                    )
            lines.append("")
        lines.extend([
            "## 分析范围与筛选",
            "",
            f"- 分析范围：{date_range['start']} 至 {date_range['end']}（含首尾）",
            *_scope_markdown_lines(result.get("filter_scope", {})),
            "- 统计口径：排除取消订单（InvoiceNo 以 C 开头）、Quantity ≤ 0 或 UnitPrice ≤ 0；金额为 Quantity × UnitPrice，单位为英镑。",
            f"- {_summary_total_label(result.get('filter_scope', {}))}：{summary['total']} [summary.total]",
            "",
        ])
        if group_by:
            temporal_group = group_by in {"day", "week", "month"}
            if temporal_group and not ranking:
                lines.extend([
                    f"## 按{group_names.get(group_by, group_by)}明细",
                    "",
                    "| 项目 | 日期范围 | 正向交易金额 | 覆盖情况 | 证据编号 |",
                    "| --- | --- | ---: | --- | --- |",
                ])
            elif not ranking:
                lines.extend([
                    f"## 按{group_names.get(group_by, group_by)}汇总",
                    "",
                    "| 项目 | 正向交易金额 | 证据编号 |",
                    "| --- | ---: | --- |",
                ])
            rows_to_render = zip(result.get("groups", []), summary.get("groups", [])) if not ranking else []
            for row, rendered in rows_to_render:
                token = re.sub(r"[^A-Za-z0-9_-]+", "_", str(row["key"])).strip("_") or "unknown"
                evidence_id = f"{group_by}.{token}.amount"
                period = "" if group_by in {"country", "product"} else rendered.get("name", "")
                if row.get("start") and row.get("end"):
                    period = f"{row['start']} 至 {row['end']}"
                if temporal_group:
                    coverage = "部分覆盖自然周期" if row.get("coverage") == "partial" else "完整"
                    lines.append(
                        f"| {_safe_markdown_cell(row['label'])} | {_safe_markdown_cell(period)} | "
                        f"{rendered['amount']} | {coverage} | `{evidence_id}` |"
                    )
                else:
                    lines.append(f"| {_safe_markdown_cell(row['label'])} | {rendered['amount']} | `{evidence_id}` |")
            lines.append("")
        lines.extend(["## 审核通过的结论", ""])
        for conclusion in report.conclusions:
            refs = ", ".join(f"`{evidence_id}`" for evidence_id in conclusion.evidence_ids)
            lines.append(f"- {conclusion.text}（证据：{refs}）")
        lines.extend(["", "## 分组金额图", "", "![分组正向交易金额](period_comparison.png)", ""])
        return "\n".join(lines)

    return _make_comparison_markdown(state, summary)
    period1 = f"{plan.period1_start.isoformat()} 至 {plan.period1_end.isoformat()}"
    period2 = f"{plan.period2_start.isoformat()} 至 {plan.period2_end.isoformat()}"
    lines = [
        f"# {report.title}",
        "",
        f"- 前期：{period1}",
        f"- 后期：{period2}",
        *_scope_markdown_lines(result.get("filter_scope", {})),
        "- 统计口径：仅统计正向交易明细（InvoiceNo 不以 C 开头、Quantity > 0、UnitPrice > 0），金额为 Quantity × UnitPrice，单位英镑；比较区间包含首尾日期。",
        "",
        "## 金额摘要",
        "",
        "| 指标 | 金额/比例 | 证据 |",
        "| --- | ---: | --- |",
        f"| 前期正向交易金额 | {summary['period1_total']} | total.period1 |",
        f"| 后期正向交易金额 | {summary['period2_total']} | total.period2 |",
        f"| 差额（后期减前期） | {_positive_sign(summary['difference'])} | total.delta |",
        f"| 变化率 | {_positive_sign(summary['change_rate'])} | total.change_pct |",
        "",
        "## 榜单",
        "",
    ]
    dimensions = plan.dimensions
    if "country" in dimensions and plan.direction in {"decline", "both"}:
        lines.append(_board_table("国家金额下降榜", summary["country_decreases"]))
    if "country" in dimensions and plan.direction in {"growth", "both"}:
        lines.append(_board_table("国家金额增长榜", summary["country_increases"]))
    if "product" in dimensions and plan.direction in {"decline", "both"}:
        lines.append(_board_table("商品金额下降榜", summary["product_decreases"]))
    if "product" in dimensions and plan.direction in {"growth", "both"}:
        lines.append(_board_table("商品金额增长榜", summary["product_increases"]))

    lines.extend(["## 审核通过的结论", ""])
    for conclusion in report.conclusions:
        refs = ", ".join(f"`{evidence_id}`" for evidence_id in conclusion.evidence_ids)
        lines.append(f"- {conclusion.text}（证据：{refs}）")
    lines.extend(["", "## 两期金额对比", "", "![两期金额对比](period_comparison.png)", ""])
    return "\n".join(lines)


def _metric_label(metric: str) -> str:
    return "正向交易金额 (£)" if metric == "amount" else "有效销售件数 (件)"


def _metric_value(metric: str, value: float) -> str:
    return f"£{value:,.2f}" if metric == "amount" else f"{value:,.0f} 件"


def _group_evidence_id(row: dict, fields: list[str], metric: str) -> str:
    token = lambda value: re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"
    suffix = "amount" if metric == "amount" else "quantity"
    if len(fields) == 2:
        time_dimension = next(item for item in fields if item in {"day", "week", "month"})
        business_dimension = next(item for item in fields if item in {"country", "product"})
        return f"{time_dimension}.{token(row['period_key'])}.{business_dimension}.{token(row['business_key'])}.{suffix}"
    dimension = fields[0] if fields else "total"
    return f"{dimension}.{token(row.get('key', 'total'))}.{suffix}"


def _make_summary_markdown(state: dict, rendered: dict) -> str:
    plan, result, report = state["plan"], state["result"], state["final_report"]
    fields = result.get("group_by_fields", plan.group_by)
    metrics = result.get("metrics", ["amount"])
    names = {"day": "日", "week": "周", "month": "月", "country": "国家", "product": "商品"}
    start, end = rendered["date_range"]["start"], rendered["date_range"]["end"]
    scope = result.get("filter_scope", {})
    grouping = " × ".join(names.get(item, item) for item in fields) or "总额"
    lines = [f"# {report.title}", ""]
    if plan.ranking_order:
        lines.extend([f"## {grouping}{'金额' if result.get('rank_by') == 'amount' else '销量'}排名前 {plan.top_n}", ""])
        if len(fields) == 1 and result.get("rankings"):
            note = ranking_display_note(result["rankings"][0])
            if note:
                lines.extend([note, ""])
        if len(fields) == 1:
            has_partial = any(row.get("coverage") == "partial" for row in result.get("groups", []))
            if fields == ["country"] and metrics == ["amount"]:
                lines.append("| 项目 | 正向交易金额 | 证据编号 |")
            else:
                header = "| 排名 | 项目 | " + " | ".join("正向交易金额" if metric == "amount" else "有效销售件数" for metric in metrics)
                if has_partial:
                    header += " | 覆盖情况"
                lines.append(header + " | 证据编号 |")
            if fields == ["country"] and metrics == ["amount"]:
                lines.append("| --- | ---: | --- |")
            else:
                divider = "| ---: | --- | " + " | ".join("---:" for _ in metrics)
                if has_partial:
                    divider += " | --- |"
                lines.append(divider + " | --- |")
            for index, row in enumerate(result.get("groups", []), 1):
                label = row.get("label", row.get("key", ""))
                if fields == ["country"] and metrics == ["amount"]:
                    cells = [label, _metric_value("amount", row.get("values", {}).get("amount", 0)),
                             f"`{_group_evidence_id(row, fields, 'amount')}`"]
                else:
                    cells = [str(row.get("rank", index)), label]
                    cells.extend(_metric_value(metric, row.get("values", {}).get(metric, 0)) for metric in metrics)
                    if has_partial:
                        cells.append("部分覆盖自然周期" if row.get("coverage") == "partial" else "完整周期")
                    cells.append(", ".join(f"`{_group_evidence_id(row, fields, metric)}`" for metric in metrics))
                lines.append("| " + " | ".join(_safe_markdown_cell(cell) for cell in cells) + " |")
            lines.append("")
        if len(fields) == 2:
            for ranking in result.get("rankings", []):
                period_name = names.get(ranking["time_dimension"], "时间")
                metric_name = "正向交易金额" if ranking["metric"] == "amount" else "有效销售件数"
                lines.extend([f"### {ranking['period_label']} · 每{period_name}{metric_name}{ranking['order_label']}榜", ""])
                note = ranking_display_note(ranking)
                if note:
                    lines.extend([note, ""])
                for row in ranking.get("rows", []):
                    lines.append(f"{row['rank']}. {row['label']} — {_metric_value(ranking['metric'], row['value'])}")
                if len(ranking.get("leaders", [])) > 1:
                    lines.append("并列第一：" + "、".join(row["label"] for row in ranking["leaders"]))
                identifier = f"ranking.{ranking['time_dimension']}.{ranking['period_key']}.{ranking['business_dimension']}"
                lines.append(f"证据：`{identifier}`")
                lines.append("")
    elif fields == ["week"]:
        lines.extend(["## 每周正向交易金额趋势" if "amount" in metrics else "## 每周有效销售件数趋势", ""])
    lines.extend([
        "## 分析范围与筛选",
        "",
        f"- 分析日期：{start} 至 {end}（含首尾）",
        *_scope_markdown_lines(scope),
        "- 口径：排除取消订单及 Quantity ≤ 0、UnitPrice ≤ 0 的明细；金额为 Quantity × UnitPrice。件数仅统计有效商品明细，排除已确认的非商品费用代码 POST、DOT、C2 等。",
        "",
        "## 范围总量",
        "",
        "| 指标 | 数值 | 证据 |",
        "| --- | ---: | --- |",
    ])
    for metric in metrics:
        value = result.get("metric_totals", {}).get(metric)
        evidence_id = "summary.total" if metric == "amount" else "summary.quantity"
        if value is not None:
            lines.append(f"| {_metric_label(metric)} | {_metric_value(metric, value)} | `{evidence_id}` |")
    lines.append("")

    if fields and (not plan.ranking_order or len(fields) == 2):
        lines.extend(["## 分组结果明细", ""])
        if len(fields) == 2:
            lines.extend([f"共 {len(result.get('groups', []))} 条明细", ""])
        headers = []
        if len(fields) == 2:
            headers += ["时间范围", "业务实体"]
        else:
            headers += (["排名", "项目"] if plan.ranking_order and fields == ["product"]
                        else ["项目"])
        headers.extend("正向交易金额" if metric == "amount" else "有效销售件数" for metric in metrics)
        if any(row.get("coverage") == "partial" for row in result.get("groups", [])):
            headers.append("覆盖情况")
        headers.append("证据编号")
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in result.get("groups", []):
            cells = []
            if len(fields) == 2:
                cells.extend([row.get("period_label", row.get("period_key", "")), row.get("business_label", row.get("business_key", ""))])
            else:
                if plan.ranking_order and fields == ["product"]:
                    cells.append(str(result.get("groups", []).index(row) + 1))
                cells.append(row.get("key", row.get("label", "")))
            cells.extend(_metric_value(metric, row.get("values", {}).get(metric, 0)) for metric in metrics)
            if any(item.get("coverage") == "partial" for item in result.get("groups", [])):
                cells.append("部分覆盖自然周期" if row.get("coverage") == "partial" else "完整周期")
            ids = ", ".join(f"`{_group_evidence_id(row, fields, metric)}`" for metric in metrics)
            cells.append(ids)
            lines.append("| " + " | ".join(_safe_markdown_cell(cell) for cell in cells) + " |")
        lines.append("")

    for ranking in (result.get("rankings", []) if len(fields) != 2 else []):
        metric = ranking["metric"]
        evidence_id = (
            f"ranking.{ranking['time_dimension']}.{ranking['period_key']}.{ranking['business_dimension']}"
            if ranking.get("time_dimension") else f"ranking.{ranking['business_dimension']}.overall"
        )
        lines.extend([f"### {ranking['period_label']} · {names.get(ranking['business_dimension'], ranking['business_dimension'])} {ranking['order_label']}（{_metric_label(metric)}）", ""])
        note = ranking_display_note(ranking)
        if note:
            lines.extend([note, ""])
        for row in ranking.get("rows", []):
            lines.append(f"{row['rank']}. {row['label']} — {_metric_value(metric, row['value'])}")
        leaders = ranking.get("leaders", [])
        if len(leaders) > 1:
            lines.append("并列第一：" + "、".join(row["label"] for row in leaders))
        lines.append(f"证据：`{evidence_id}`")
        lines.append("")

    lines.extend(["## 审核通过的结论", ""])
    for conclusion in report.conclusions:
        refs = ", ".join(f"`{value}`" for value in conclusion.evidence_ids)
        lines.append(f"- {conclusion.text}（证据：{refs}）")
    chart_label = "分组正向交易金额" if metrics == ["amount"] else "分组指标"
    lines.extend(["", "## 分组结果图", "", f"![{chart_label}](period_comparison.png)", ""])
    return "\n".join(lines)


def _make_comparison_markdown(state: dict, rendered: dict) -> str:
    plan, result, report = state["plan"], state["result"], state["final_report"]
    metrics = result.get("metrics", [result.get("metric", "amount")])
    lines = [
        f"# {report.title}", "",
        f"- 前期：{plan.period1_start.isoformat()} 至 {plan.period1_end.isoformat()}（含首尾）",
        f"- 后期：{plan.period2_start.isoformat()} 至 {plan.period2_end.isoformat()}（含首尾）",
        *_scope_markdown_lines(result.get("filter_scope", {})),
        "- 口径：排除取消订单及 Quantity ≤ 0、UnitPrice ≤ 0 的明细；金额为 Quantity × UnitPrice。件数统计相同有效交易中的商品数量，并排除 POST、DOT、C2 等非商品费用。",
        "",
        "## 两期指标", "",
        "| 指标 | 前期 | 后期 | 差额（后期减前期） | 变化率 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    rendered_metrics = rendered.get("metric_results", {})
    for metric in metrics:
        values = rendered_metrics[metric]
        prefix = "total" if metric == "amount" else "total.quantity"
        lines.append(
            f"| {_metric_label(metric)} | {values['period1_total']} [`{prefix}.period1`] "
            f"| {values['period2_total']} [`{prefix}.period2`] | {values['difference']} [`{prefix}.delta`] "
            f"| {values['change_rate']} [`{prefix}.change_pct`] |"
        )
    lines.extend(["", "## 榜单", ""])
    for metric in metrics:
        metric_result = rendered_metrics[metric]
        unit_label = "金额" if metric == "amount" else "销量"
        for dimension, key, title in (
            ("country", "country_decreases", "国家下降"),
            ("country", "country_increases", "国家增长"),
            ("product", "product_decreases", "商品下降"),
            ("product", "product_increases", "商品增长"),
        ):
            if dimension not in plan.dimensions:
                continue
            direction = "decline" if "decreases" in key else "growth"
            if plan.direction not in {direction, "both"}:
                continue
            rows = metric_result.get(key, [])
            lines.extend([f"### {title}榜（{unit_label}）", ""])
            if rows:
                lines.extend([
                    f"| {'国家' if dimension == 'country' else '商品'} | 前期（{unit_label}） | 后期（{unit_label}） | 差额（{unit_label}） | 证据 |",
                    "| --- | ---: | ---: | ---: | --- |",
                ])
                for row in rows:
                    token = re.sub(r"[^A-Za-z0-9_-]+", "_", str(row["name"])).strip("_") or "unknown"
                    evidence_id = f"{dimension}.{token}" + ("" if metric == "amount" else ".quantity") + ".delta"
                    entity = row["name"] + (f" - {row['description']}" if row.get("description") else "")
                    lines.append(
                        f"| {_safe_markdown_cell(entity)} | {row['period1']} | {row['period2']} "
                        f"| {row['difference']} | `{evidence_id}` |"
                    )
            else:
                lines.append("没有符合该方向的项目。")
            lines.append("")
    lines.extend(["## 审核通过的结论", ""])
    for conclusion in report.conclusions:
        refs = ", ".join(f"`{value}`" for value in conclusion.evidence_ids)
        lines.append(f"- {conclusion.text}（证据：{refs}）")
    chart_title = "两期金额对比" if metrics == ["amount"] else "两期指标对比"
    lines.extend(["", f"## {chart_title}", "", f"![{chart_title}](period_comparison.png)", ""])
    return "\n".join(lines)


def _make_comparison_chart(state: dict, path: Path) -> None:
    render_result_chart(state["result"], state["plan"], path)


def export_approved_report(state: dict, outputs_dir: str | Path) -> tuple[Path, Path]:
    review = state.get("review_result")
    final_report = state.get("final_report")
    if review is None or review.decision != "approve" or final_report is None:
        raise ReportExportError("仅审核通过且存在 final_report 的工作流状态可以导出。")

    outputs_dir = Path(outputs_dir)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    output_dir = _unique_output_dir(outputs_dir)
    with tempfile.TemporaryDirectory(prefix=".report_export_", dir=outputs_dir) as temp_name:
        temp_dir = Path(temp_name)
        markdown_path = temp_dir / "report.md"
        image_path = temp_dir / "period_comparison.png"
        markdown_path.write_text(_make_markdown(state), encoding="utf-8")
        _make_comparison_chart(state, image_path)
        shutil.move(str(temp_dir), str(output_dir))
    markdown_path = output_dir / "report.md"
    image_path = output_dir / "period_comparison.png"
    return markdown_path, image_path
