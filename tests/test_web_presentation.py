import sys
import unittest
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from planning_agent import AnalysisPlan
from report_reviewer import ReviewIssue, ReviewResult
from retail_analysis import analyze_periods
from web_presentation import (
    board_rows,
    completed_node_event,
    conclusion_groups,
    summary_leader,
    summary_result_title,
    ranking_display_note,
    plan_summary,
    format_signed_money,
    format_signed_percent,
    run_status_label,
    initial_trace,
    start_node_event,
    complete_trace_event,
    skip_pending_trace,
    fail_trace_event,
    review_revision_details,
    render_review_comparison,
)


CSV_PATH = PROJECT_ROOT / "data" / "retail_demo.csv"


class WebPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = AnalysisPlan(
            period1_start=date(2011, 9, 12),
            period1_end=date(2011, 9, 18),
            period2_start=date(2011, 9, 19),
            period2_end=date(2011, 9, 25),
            dimensions=["country", "product"],
            direction="both",
            top_n=3,
        )
        cls.result = analyze_periods(
            CSV_PATH,
            cls.plan.period1_start,
            cls.plan.period1_end,
            cls.plan.period2_start,
            cls.plan.period2_end,
            dimensions=cls.plan.dimensions,
            direction=cls.plan.direction,
            top_n=cls.plan.top_n,
        )

    def test_sign_format_is_explicit_for_delta_and_rate(self):
        self.assertEqual(format_signed_money(self.result["difference"]), "+£108,634.53")
        self.assertEqual(format_signed_percent(self.result["change_rate"]), "+47.20%")
        self.assertEqual(format_signed_money(-112.08), "-£112.08")

    def test_ranking_note_explains_tie_expansion_and_insufficient_results(self):
        self.assertEqual(
            ranking_display_note({
                "requested_count": 5, "actual_count": 7,
                "rows": [{"rank": rank} for rank in [1, 1, 3, 4, 5, 5, 5]],
            }),
            "请求前 5 名，第 5 名并列，实际展示 7 项。",
        )
        self.assertEqual(
            ranking_display_note({"requested_count": 5, "actual_count": 2, "rows": []}),
            "请求前 5 名，本次有 2 项符合条件。",
        )

    def test_summary_ranking_title_and_leader_include_selected_scope_and_item(self):
        plan = AnalysisPlan(
            analysis_type="summary", time_scope="full_data", group_by=["product"],
            ranking_order="desc", top_n=5, country_filters=["德国"],
        )
        result = {
            "date_range": {"start": date(2011, 9, 12), "end": date(2011, 10, 2)},
            "group_by": "product",
            "filter_scope": {"countries": ["Germany"], "products": []},
            "groups": [{"key": "22580", "label": "22580 - ADVENT CALENDAR GINGHAM SACK", "amount": 237.6}],
        }

        title = summary_result_title(plan, result)
        leader_label, leader_amount = summary_leader(plan, result)

        self.assertEqual(title, "2011-09-12 至 2011-10-02 · 德国商品金额前 5（返回 1 项）")
        self.assertEqual(leader_label, "榜首商品 22580")
        self.assertEqual(leader_amount, "£237.60")

    def test_weekly_trend_title_does_not_claim_a_ranking(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["week"], ranking_order=None, top_n=5,
        )
        result = {
            "date_range": {"start": date(2011, 9, 12), "end": date(2011, 10, 2)},
            "group_by": "week", "filter_scope": {"countries": [], "products": []},
            "groups": [{"key": "2011-09-19_2011-09-25", "label": "2011-09-19 至 2011-09-25", "amount": 338771.79}],
        }

        self.assertEqual(summary_result_title(plan, result), "2011-09-12 至 2011-10-02 · 每周正向交易金额趋势")

    def test_plan_summary_names_selected_metrics_and_combined_grouping(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["week", "country"], metrics=["amount", "quantity"],
            rank_by="amount", ranking_order="desc", top_n=1,
        )

        summary = plan_summary(plan)

        self.assertIn("指标 正向交易金额、有效销售件数", summary)
        self.assertIn("分组 周 × 国家", summary)
        self.assertIn("按正向交易金额最高排序，前 1 项", summary)

    def test_summary_leader_displays_all_tied_entities_and_composition_periods(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="date_range",
            start_date=date(2011, 9, 1), end_date=date(2011, 9, 30),
            group_by=["product"], ranking_order="desc", rank_by="amount",
        )
        tied_result = {
            "groups": [{"key": "A", "amount": 10}],
            "group_by_fields": ["product"],
            "rank_by": "amount",
            "rankings": [{"metric": "amount", "leaders": [
                {"key": "A", "value": 10}, {"key": "B", "value": 10},
            ]}],
        }
        label, value = summary_leader(plan, tied_result)
        self.assertEqual(label, "榜首商品（并列）")
        self.assertEqual(value, "A、B · £10.00")

        combined_plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="date_range",
            start_date=date(2011, 9, 1), end_date=date(2011, 10, 31),
            group_by=["month", "product"], ranking_order="desc", rank_by="amount",
        )
        combined_result = {
            "groups": [{"key": "x"}], "group_by_fields": ["month", "product"], "rank_by": "amount",
            "rankings": [
                {"metric": "amount", "period_label": "2011-09", "leaders": [{"key": "A", "value": 10}]},
                {"metric": "amount", "period_label": "2011-10", "leaders": [{"key": "B", "value": 20}]},
            ],
        }
        combined_label, combined_value = summary_leader(combined_plan, combined_result)
        self.assertEqual(combined_label, "各月榜首")
        self.assertEqual(combined_value, "2 期，见下表")

    def test_trace_shows_started_skipped_failed_and_retry_states(self):
        trace = initial_trace()
        current = start_node_event(trace, {"node": "plan", "attempt": 1})
        self.assertEqual(current["label"], "规划智能体 · 进行中")
        complete_trace_event(trace, "plan", 1, {"detail": "已识别分析范围"})
        skip_pending_trace(trace, "计划需要补充日期")
        self.assertEqual([event["status"] for event in trace], ["completed", "skipped", "skipped", "skipped"])
        self.assertTrue(all("未执行" in event["detail"] for event in trace[1:]))

        retry_trace = initial_trace()
        start_node_event(retry_trace, {"node": "review", "attempt": 1})
        complete_trace_event(retry_trace, "review", 1, {"detail": "退回：证据不能支持缺货原因"})
        revision = start_node_event(retry_trace, {"node": "draft_report", "attempt": 2})
        second_review = start_node_event(retry_trace, {"node": "review", "attempt": 2})
        self.assertEqual(revision["label"], "报告返修 · 进行中")
        self.assertEqual(second_review["label"], "第 2 次审核 · 进行中")
        self.assertIn("证据不能支持缺货原因", retry_trace[3]["detail"])

        failed_trace = initial_trace()
        start_node_event(failed_trace, {"node": "plan", "attempt": 1})
        fail_trace_event(failed_trace, "plan", 1, "planner failed")
        self.assertEqual(failed_trace[0]["status"], "failed")
        self.assertFalse(any(event["status"] == "running" for event in failed_trace))

    def test_review_trace_keeps_summary_short_and_full_feedback_collapsed(self):
        issue = ReviewIssue(
            conclusion_index=0,
            evidence_ids=["week.2011-09-19_2011-09-25.amount"],
            problem="结论范围太宽，必须同时比较其余各周的金额后才能判断这一周是最高周。" * 3,
            requested_change="补充其余各周证据。",
        )
        event = completed_node_event(
            "review", {"review_result": ReviewResult(decision="return", issues=[issue])}, [],
        )

        self.assertLessEqual(len(event["detail"]), 90)
        self.assertIn(issue.problem, event["details"])

    def test_review_revision_details_keep_draft_evidence_and_both_review_outcomes(self):
        from report_generator import ReportDraft

        initial = ReportDraft(title="初稿", conclusions=[{
            "text": "英国金额下降是因为缺货。", "evidence_ids": ["country.United_Kingdom.delta"],
        }])
        revised = ReportDraft(title="返修稿", conclusions=[{
            "text": "英国金额下降，现有数据无法确认原因。", "evidence_ids": ["country.United_Kingdom.delta"],
        }])
        first = ReviewResult(decision="return", issues=[ReviewIssue(
            conclusion_index=0, evidence_ids=["country.United_Kingdom.delta"],
            problem="金额差额不能证明缺货", requested_change="删除缺货因果解释",
        )])
        second = ReviewResult(decision="approve", issues=[])

        comparison = review_revision_details([initial, revised], [first, second], attempt=2)

        self.assertEqual(comparison["first_draft"]["conclusions"][0]["text"], "英国金额下降是因为缺货。")
        self.assertEqual(comparison["first_draft"]["conclusions"][0]["evidence_ids"], ["country.United_Kingdom.delta"])
        self.assertEqual(comparison["first_review"]["issues"][0]["problem"], "金额差额不能证明缺货")
        self.assertEqual(comparison["revision_draft"]["conclusions"][0]["text"], "英国金额下降，现有数据无法确认原因。")
        self.assertEqual(comparison["revision_draft"]["conclusions"][0]["evidence_ids"], ["country.United_Kingdom.delta"])
        self.assertEqual(comparison["second_review"]["decision"], "approve")
        self.assertEqual(comparison["pairing"], "unpaired")

        class Capture:
            def __init__(self):
                self.messages = []

            def markdown(self, value): self.messages.append(value)
            def caption(self, value): self.messages.append(value)
            def write(self, value): self.messages.append(value)

        capture = Capture()
        render_review_comparison(comparison, capture)
        rendered = "\n".join(capture.messages)
        self.assertIn("英国金额下降是因为缺货。", rendered)
        self.assertIn("country.United_Kingdom.delta", rendered)
        self.assertIn("金额差额不能证明缺货", rendered)
        self.assertIn("英国金额下降，现有数据无法确认原因。", rendered)
        self.assertIn("通过", rendered)

    def test_review_trace_distinguishes_first_approval_and_second_return(self):
        approved = completed_node_event(
            "review", {"review_result": ReviewResult(decision="approve", issues=[])}, [],
            review_history=[ReviewResult(decision="approve", issues=[])],
        )
        returned = ReviewResult(decision="return", issues=[ReviewIssue(
            conclusion_index=0, evidence_ids=["summary.total"],
            problem="解释仍不受证据支持", requested_change="删除未经支持的解释",
        )])
        first_return = ReviewResult(decision="return", issues=[ReviewIssue(
            conclusion_index=0, evidence_ids=["summary.total"],
            problem="初次说明无依据", requested_change="补充支持结论的证据",
        )])
        final_event = completed_node_event(
            "review", {"review_result": returned}, [{"node": "review", "attempt": 1, "status": "completed"}],
            draft_history=[
                type("Draft", (), {"title": "初稿", "conclusions": []})(),
                type("Draft", (), {"title": "返修稿", "conclusions": []})(),
            ],
            review_history=[first_return, returned],
        )

        self.assertEqual(approved["detail"], "首次审核通过。")
        self.assertIn("复核仍退回", final_event["detail"])
        self.assertIn("解释仍不受证据支持", final_event["details"]["second_review"]["issues"][0]["problem"])
        self.assertNotIn("复核仍退回：退回：", final_event["detail"])

    def test_review_comparison_marks_only_explicitly_removed_conclusion_as_deleted(self):
        from report_generator import ReportDraft

        initial = ReportDraft(title="初稿", conclusions=[{
            "text": "促销导致增长。", "evidence_ids": ["total.delta"],
        }])
        revised = ReportDraft(title="返修稿", conclusions=[{
            "text": "金额发生变化。", "evidence_ids": ["total.delta"],
        }])
        first = ReviewResult(decision="return", issues=[ReviewIssue(
            conclusion_index=0, evidence_ids=["total.delta"],
            problem="证据不能确认促销原因", requested_change="删除该结论并删除未经验证的因果说法",
        )])
        second = ReviewResult(decision="approve", issues=[])

        comparison = review_revision_details([initial, revised], [first, second])

        self.assertEqual(comparison["first_review"]["issues"][0]["disposition"], "已删除")

    def test_board_rows_show_actual_count_when_fewer_items_match(self):
        rows, note = board_rows(self.result, "country", "decline", self.plan.top_n)

        self.assertEqual([row["国家"] for row in rows], ["Sweden", "Japan"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(note, "请求前 3 项，本次有 2 项符合条件。")
        self.assertEqual(rows[0]["差额"], "-£4,114.08")

    def test_node_events_report_real_outputs_and_keep_review_rework_order(self):
        first_review = ReviewResult(
            decision="return",
            issues=[
                ReviewIssue(
                    conclusion_index=0,
                    evidence_ids=["country.Sweden.delta"],
                    problem="证据不能说明缺货",
                    requested_change="移除缺货因果",
                )
            ],
        )
        events = []
        events.append(completed_node_event("plan", {"plan": self.plan}, events))
        events.append(completed_node_event("calculate", {"result": self.result}, events, plan=self.plan))
        events.append(completed_node_event("draft_report", {"report_draft": type("Draft", (), {
            "title": "销售比较",
            "conclusions": [type("Conclusion", (), {"text": "国家销售情况发生变化。"})()],
        })()}, events))
        events.append(completed_node_event("review", {"review_result": first_review}, events))
        events.append(completed_node_event("draft_report", {"report_draft": type("Draft", (), {
            "title": "修订销售比较",
            "conclusions": [type("Conclusion", (), {"text": "修订后的国家结论。"})()],
        })()}, events))
        events.append(completed_node_event("review", {"review_result": ReviewResult(decision="approve", issues=[])}, events))

        self.assertIn("2011-09-12", events[0]["detail"])
        self.assertIn("£230,137.26 → £338,771.79", events[1]["detail"])
        self.assertIn("证据不能说明缺货", events[3]["detail"])
        self.assertIn("修订后的国家结论", events[4]["detail"])
        self.assertEqual(events[5]["label"], "第 2 次审核")
        self.assertEqual(events[5]["detail"], "复核通过，返修稿已通过审核。")
        self.assertTrue(all(event["status"] == "completed" for event in events))

    def test_conclusions_group_by_actual_requested_dimension_and_delta_sign(self):
        evidence = {
            "total.period1": {"value": 230137.26, "unit": "GBP"},
            "total.period2": {"value": 338771.791, "unit": "GBP"},
            "total.delta": {"value": 108634.531, "unit": "GBP"},
            "total.change_pct": {"value": 47.2, "unit": "percent"},
            "country.Sweden.delta": {"value": -4114.08, "unit": "GBP"},
            "product.22688.delta": {"value": -2926.9, "unit": "GBP"},
        }
        report = type(
            "Draft",
            (),
            {
                "conclusions": [
                    type("Conclusion", (), {"text": "总体金额增长。", "evidence_ids": ["total.change_pct"]})(),
                    type("Conclusion", (), {"text": "Sweden 降幅较大。", "evidence_ids": ["country.Sweden.delta"]})(),
                    type("Conclusion", (), {"text": "商品 22688 下降。", "evidence_ids": ["product.22688.delta"]})(),
                ]
            },
        )()

        overview, groups = conclusion_groups(report, evidence, ["country", "product"], "both")

        self.assertIn("£230,137.26", overview)
        self.assertIn("+47.20%", overview)
        self.assertEqual([group[2] for group in groups], ["国家 · 下降", "商品 · 下降"])
        self.assertEqual(groups[0][3][0].text, "Sweden 降幅较大。")

    def test_global_run_status_is_single_contextual_label(self):
        self.assertEqual(run_status_label({"status": "approved"}), "审核通过")
        self.assertEqual(run_status_label({"status": "clarification"}), "需补充日期")
        self.assertEqual(run_status_label({"status": "clarification", "clarification_kind": "drill_choice"}), "需选择引用对象")
        unresolved_plan = AnalysisPlan(
            analysis_type="summary", time_scope="full_data", group_by=["product"],
            ranking_order="desc", country_filters=["Atlantis"],
        )
        self.assertEqual(
            run_status_label({"status": "clarification", "state": {"plan": unresolved_plan, "filter_scope": None}}),
            "需确认筛选条件",
        )
        self.assertEqual(
            run_status_label({
                "status": "failed",
                "state": {"review_result": type("Review", (), {"decision": "return"})()},
            }),
            "审核未通过",
        )
        self.assertEqual(run_status_label({"status": "failed", "state": None}), "运行失败")

    def test_plan_trace_does_not_repeat_full_clarification_question(self):
        question = "请补充前期和后期的开始、结束日期。"
        plan = AnalysisPlan(
            period1_start=None, period1_end=None, period2_start=None, period2_end=None,
            dimensions=["product"], direction="growth", clarification_question=question,
        )
        event = completed_node_event(
            "plan", {"plan": plan, "clarification_question": question}, [],
        )

        self.assertNotIn(question, event["detail"])
        self.assertIn("日期不完整", event["detail"])

    def test_summary_plan_and_calculation_trace_show_resolved_scope(self):
        plan = AnalysisPlan(
            analysis_type="summary", time_scope="full_data", group_by=["product"],
            ranking_order="desc", country_filters=["英国"],
        )
        plan_event = completed_node_event(
            "plan", {"plan": plan, "filter_scope": {"countries": ["United Kingdom"], "products": []}}, [],
        )
        result_event = completed_node_event(
            "calculate",
            {"result": {
                "analysis_type": "summary", "summary": {"total": 100},
                "groups": [{"label": "Widget", "amount": 50}],
                "filter_scope": {"countries": ["United Kingdom"], "products": []},
            }},
            [], plan=plan,
        )

        self.assertIn("国家条件 英国", plan_event["detail"])
        self.assertIn("国家 United Kingdom", result_event["detail"])

    def test_combined_cross_dimension_conclusion_is_not_duplicated(self):
        evidence = {
            "total.period1": {"value": 230137.26},
            "total.period2": {"value": 338771.79},
            "total.delta": {"value": 108634.53},
            "total.change_pct": {"value": 47.2},
            "country.Sweden.delta": {"value": -4114.08},
            "product.22688.delta": {"value": -2926.9},
        }
        conclusion = type(
            "Conclusion",
            (),
            {
                "text": "整体增长时，瑞典和商品 22688 仍在下降。",
                "evidence_ids": ["country.Sweden.delta", "product.22688.delta"],
            },
        )()
        report = type("Draft", (), {"conclusions": [conclusion]})()

        overview, groups = conclusion_groups(report, evidence, ["country", "product"], "both")

        self.assertIn("+47.20%", overview)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][2], "多维度 · 下降")


if __name__ == "__main__":
    unittest.main()
