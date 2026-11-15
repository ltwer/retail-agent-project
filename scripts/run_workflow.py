import argparse
import json
import sys
from pathlib import Path

from langgraph_workflow import run_workflow
from planning_agent import ModelConfigurationError, PlannerRequestError
from report_generator import ReportGenerationError, render_report_numbers
from report_exporter import export_approved_report
from report_reviewer import ReviewRequestError
from web_presentation import format_metric_value


def _money(value: float) -> str:
    return f"-£{abs(value):,.2f}" if value < 0 else f"£{value:,.2f}"


def _print_board(title: str, entries: list[dict], key: str, metric: str = "amount") -> None:
    print(f"\n{title}")
    if not entries:
        print("  (无符合条件的项目)")
        return
    for entry in entries:
        label = entry[key]
        if key == "StockCode" and entry.get("Description"):
            label = f"{label} - {entry['Description']}"
        print(
            f"  {label}: 前期 {format_metric_value(metric, entry['period1'])} | "
            f"后期 {format_metric_value(metric, entry['period2'])} | 差额 "
            f"{_money(entry['difference']) if metric == 'amount' else ('-' if entry['difference'] < 0 else '+') + format_metric_value(metric, abs(entry['difference']))}"
        )


def _print_evidence(evidence: dict[str, dict]) -> None:
    print("\n计算表与证据编号:")
    for evidence_id, item in evidence.items():
        value = item["value"]
        if item["unit"] == "GBP":
            rendered = _money(value)
        elif item["unit"] == "percent":
            rendered = f"{value:.2f}%"
        elif item["unit"] == "items":
            rendered = f"{value:,.0f} 件"
        else:
            rendered = str(value)
        print(f"  {evidence_id}: {item['label']} = {rendered}")


def _print_report_numbers(result: dict, plan) -> None:
    summary = render_report_numbers(result, plan)
    if summary.get("analysis_type") == "summary":
        print(f"  分析范围：{summary['date_range']['start']} 至 {summary['date_range']['end']}")
        print(f"  正向交易总金额：{summary['total']} [summary.total]")
        for row in summary["groups"]:
            print(f"  {row['name']}: {row['amount']}")
        return
    print("\n报告数据摘要（由计算结果渲染）:")
    print(f"  前期正向交易金额：{summary['period1_total']} [total.period1]")
    print(f"  后期正向交易金额：{summary['period2_total']} [total.period2]")
    print(f"  差额：{summary['difference']} [total.delta]")
    print(f"  变化率：{summary['change_rate']} [total.change_pct]")
    for label, key, evidence_prefix in (
        ("国家下降榜", "country_decreases", "country"),
        ("国家增长榜", "country_increases", "country"),
        ("商品下降榜", "product_decreases", "product"),
        ("商品增长榜", "product_increases", "product"),
    ):
        rows = summary[key]
        if not rows:
            continue
        print(f"  {label}:")
        for row in rows:
            token = str(row["name"]).replace(" ", "_")
            print(
                f"    {row['name']}: {row['period1']} → {row['period2']}，"
                f"差额 {row['difference']} [{evidence_prefix}.{token}.delta]"
            )


def _legacy_main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="运行零售数据分析规划与计算工作流。")
    parser.add_argument("csv_path", type=Path, help="本地零售交易 CSV 路径")
    parser.add_argument("question", nargs="+", help="中文分析问题")
    args = parser.parse_args()

    try:
        state = run_workflow(args.csv_path, " ".join(args.question))
    except ModelConfigurationError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    except (PlannerRequestError, ReportGenerationError, ReviewRequestError, ValueError) as exc:
        print(f"工作流失败：{exc}", file=sys.stderr)
        return 1

    print("执行路径: " + " → ".join(state["steps"]) + " → 结束")
    print("\n规划结果:")
    print(json.dumps(state["plan"].model_dump(mode="json"), ensure_ascii=False, indent=2))

    if state["plan"].status == "clarification":
        print(f"\n需要补充信息：{state['clarification_question']}")
        return 0
    if state["plan"].status == "unsupported":
        print(f"\n当前能力不支持：{state['plan'].unsupported_reason}")
        return 0

    result = state["result"]
    print("\n计算结果:")
    scope = result.get("filter_scope", {})
    print("国家筛选: " + ("、".join(scope.get("countries", [])) or "全部国家"))
    print("商品筛选: " + ("、".join(scope.get("products", [])) or "全部商品"))
    if scope.get("message"):
        print("记录情况: " + scope["message"])
    if result.get("analysis_type") == "summary":
        date_range = result["date_range"]
        print(f"分析范围: {date_range['start']} 至 {date_range['end']}（含首尾）")
        print(f"正向交易总金额: {_money(result['summary']['total'])}")
        print(f"分组方式: {result.get('group_by') or '总额'}")
        for row in result.get("groups", []):
            coverage = "（部分覆盖自然周期）" if row.get("coverage") == "partial" else ""
            print(f"  {row['label']}: {_money(row['amount'])}{coverage}")
    else:
        print(f"前期金额: {_money(result['period1_total'])}")
        print(f"后期金额: {_money(result['period2_total'])}")
        print(f"差额（后期 - 前期）: {_money(result['difference'])}")
        rate = result["change_rate"]
        print(f"变化率: {rate:.2%}" if rate is not None else "变化率: 不可计算（前期金额为零）")

    plan = state["plan"]
    if plan.analysis_type == "comparison" and "country" in plan.dimensions:
        if plan.direction in {"decline", "both"}:
            _print_board("国家金额下降榜", result["country_decreases"], "Country")
        if plan.direction in {"growth", "both"}:
            _print_board("国家金额增长榜", result["country_increases"], "Country")
    if plan.analysis_type == "comparison" and "product" in plan.dimensions:
        if plan.direction in {"decline", "both"}:
            _print_board("商品金额下降榜", result["product_decreases"], "StockCode")
        if plan.direction in {"growth", "both"}:
            _print_board("商品金额增长榜", result["product_increases"], "StockCode")

    print("\n报告初稿:")
    print(f"标题：{state['report_draft'].title}")
    for conclusion in state["report_draft"].conclusions:
        print(f"- {conclusion.text} [证据：{', '.join(conclusion.evidence_ids)}]")
    _print_report_numbers(result, plan)

    print("\n审核结果:")
    review = state["review_result"]
    print("通过" if review.decision == "approve" else "退回")
    for issue in review.issues:
        print(
            f"- 第 {issue.conclusion_index + 1} 条：{issue.problem} "
            f"[证据：{', '.join(issue.evidence_ids)}] 修改：{issue.requested_change}"
        )

    if state["final_report"] is not None:
        print("\n最终报告:")
        print(f"标题：{state['final_report'].title}")
        for conclusion in state["final_report"].conclusions:
            print(f"- {conclusion.text} [证据：{', '.join(conclusion.evidence_ids)}]")
        outputs_dir = Path(__file__).resolve().parents[1] / "outputs"
        markdown_path, image_path = export_approved_report(state, outputs_dir)
        print("\n导出文件:")
        print(f"  Markdown: {markdown_path.resolve()}")
        print(f"  图表: {image_path.resolve()}")
    else:
        print("\n最终报告：审核未通过，已达到一次修改上限。")
    _print_evidence(state["evidence"])
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="运行零售数据分析工作流")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("question", nargs="+")
    args = parser.parse_args()
    question = " ".join(args.question)
    try:
        state = run_workflow(args.csv_path, question)
    except ModelConfigurationError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    except (PlannerRequestError, ReportGenerationError, ReviewRequestError, ValueError) as exc:
        print(f"工作流失败：{exc}", file=sys.stderr)
        return 1

    print("执行路径: " + " → ".join(state["steps"]) + " → 结束")
    print("\n规划结果:")
    print(json.dumps(state["plan"].model_dump(mode="json"), ensure_ascii=False, indent=2))
    if state["plan"].status == "clarification":
        print(f"\n需要补充信息：{state['clarification_question']}")
        return 0
    if state["plan"].status == "unsupported" or state.get("unsupported_reason"):
        reason = state.get("unsupported_reason") or state["plan"].unsupported_reason
        print(f"\n未执行报告：{reason}")
        return 0

    result, plan = state["result"], state["plan"]
    scope = result.get("filter_scope", {})
    print("\n计算结果:")
    print("国家筛选: " + ("、".join(scope.get("countries", [])) or "全部国家"))
    print("商品筛选: " + ("、".join(scope.get("products", [])) or "全部商品"))
    if result["analysis_type"] == "summary":
        date_range = result["date_range"]
        print(f"分析范围: {date_range['start']} 至 {date_range['end']}（含首尾）")
        print(f"分组方式: {' × '.join(result.get('group_by_fields', [])) or '总额'}")
        for metric, value in result.get("metric_totals", {}).items():
            label = "正向交易金额" if metric == "amount" else "有效销售件数"
            evidence_id = "summary.total" if metric == "amount" else "summary.quantity"
            print(f"{label}: {format_metric_value(metric, value)} [{evidence_id}]")
        for row in result.get("groups", []):
            values = " | ".join(
                f"{'正向交易金额' if metric == 'amount' else '有效销售件数'}: {format_metric_value(metric, value)}"
                for metric, value in row.get("values", {}).items()
            )
            coverage = "（部分覆盖自然周期）" if row.get("coverage") == "partial" else ""
            print(f"  {row['label']}: {values}{coverage}")
        for ranking in result.get("rankings", []):
            evidence_id = (f"ranking.{ranking['time_dimension']}.{ranking['period_key']}.{ranking['business_dimension']}"
                           if ranking.get("time_dimension") else f"ranking.{ranking['business_dimension']}.overall")
            metric_name = "正向交易金额" if ranking["metric"] == "amount" else "有效销售件数"
            print(f"  {ranking['period_label']} · 按{metric_name}{ranking['order_label']}榜 [{evidence_id}]")
            for index, item in enumerate(ranking["rows"], 1):
                print(f"    {index}. {item['label']}: {format_metric_value(ranking['metric'], item['value'])}")
            if len(ranking.get("leaders", [])) > 1:
                print("    并列第一: " + "、".join(item["label"] for item in ranking["leaders"]))
    else:
        for metric in result.get("metrics", ["amount"]):
            metric_result = result.get("metric_results", {}).get(metric, result)
            label = "正向交易金额" if metric == "amount" else "有效销售件数"
            print(f"{label}: {format_metric_value(metric, metric_result['period1_total'])} → {format_metric_value(metric, metric_result['period2_total'])}")
            delta = (_money(metric_result["difference"]) if metric == "amount" else
                     f"{'-' if metric_result['difference'] < 0 else '+'}{abs(metric_result['difference']):,.0f} 件")
            rate = metric_result["change_rate"]
            print(f"差额: {delta}；变化率: {rate:.2%}" if rate is not None else f"差额: {delta}；变化率不可计算")
            for dimension, label_name, down, up, key in (
                ("country", "国家", "country_decreases", "country_increases", "Country"),
                ("product", "商品", "product_decreases", "product_increases", "StockCode"),
            ):
                if dimension not in plan.dimensions:
                    continue
                for direction, result_key in (("下降", down), ("增长", up)):
                    if plan.direction not in ({"下降": "decline", "增长": "growth"}[direction], "both"):
                        continue
                    _print_board(f"{label_name}{'金额' if metric == 'amount' else '销量'}{direction}榜", metric_result[result_key], key, metric)

    print("\n报告初稿:")
    for conclusion in state["report_draft"].conclusions:
        print(f"- {conclusion.text} [证据：{', '.join(conclusion.evidence_ids)}]")
    print("\n审核结果:")
    review = state["review_result"]
    print("通过" if review.decision == "approve" else "阻止发布" if review.decision == "block" else "退回")
    for issue in review.issues:
        print(f"- 第 {issue.conclusion_index + 1} 条：{issue.problem} [证据：{', '.join(issue.evidence_ids)}] 修改：{issue.requested_change}")
    if state["final_report"] is not None:
        outputs_dir = Path(__file__).resolve().parents[1] / "outputs"
        markdown_path, image_path = export_approved_report(state, outputs_dir)
        print("\n最终报告:")
        for conclusion in state["final_report"].conclusions:
            print(f"- {conclusion.text} [证据：{', '.join(conclusion.evidence_ids)}]")
        print(f"Markdown: {markdown_path.resolve()}")
        print(f"PNG: {image_path.resolve()}")
    else:
        print("最终报告：未发布（审核未通过）")
    _print_evidence(state.get("evidence") or {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
