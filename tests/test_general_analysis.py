import sys
import unittest
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from langgraph_workflow import run_workflow
from planning_agent import AnalysisPlan
from report_generator import ReportDraft, build_evidence
from report_reviewer import ReviewResult
from retail_analysis import analyze_plan, analyze_periods


CSV_PATH = PROJECT_ROOT / "data" / "retail_demo.csv"


def plan(**overrides):
    payload = {
        "status": "executable",
        "analysis_type": "summary",
        "time_scope": "full_data",
        "start_date": None,
        "end_date": None,
        "period1_start": None,
        "period1_end": None,
        "period2_start": None,
        "period2_end": None,
        "group_by": ["week"],
        "dimensions": [],
        "direction": None,
        "top_n": 5,
        "ranking_order": "desc",
        "clarification_question": None,
        "unsupported_reason": None,
    }
    payload.update(overrides)
    return AnalysisPlan.model_validate(payload)


class GeneralAnalysisTests(unittest.TestCase):
    def test_full_data_weekly_summary_returns_top_week_and_all_groups(self):
        validated = plan()
        result = analyze_plan(CSV_PATH, validated)

        self.assertEqual(result["analysis_type"], "summary")
        self.assertEqual(result["date_range"], {"start": date(2011, 9, 12), "end": date(2011, 10, 2)})
        self.assertEqual(len(result["groups"]), 3)
        self.assertEqual(result["groups"][0]["start"], date(2011, 9, 19))
        self.assertEqual(result["groups"][0]["end"], date(2011, 9, 25))
        self.assertAlmostEqual(result["groups"][0]["amount"], 338771.791)
        self.assertEqual(result["groups"][0]["coverage"], "full")
        self.assertEqual(result["groups"][-1]["coverage"], "full")

    def test_weekly_entity_filter_includes_zero_activity_weeks_and_evidence(self):
        validated = plan(
            group_by=["week"], metrics=["quantity"], rank_by="quantity",
            ranking_order=None, country_filters=["Germany"], product_filters=["21498"],
        )

        result = analyze_plan(CSV_PATH, validated)

        self.assertEqual([row["key"] for row in result["groups"]], [
            "2011-09-12_2011-09-18", "2011-09-19_2011-09-25", "2011-09-26_2011-10-02",
        ])
        self.assertEqual([row["values"]["quantity"] for row in result["groups"]], [0.0, 125.0, 25.0])
        self.assertEqual(result["evidence"]["week.2011-09-12_2011-09-18.quantity"]["value"], 0.0)

    def test_truncated_natural_week_is_marked_partial(self):
        validated = plan(
            time_scope="date_range",
            start_date=date(2011, 9, 13),
            end_date=date(2011, 10, 1),
        )
        result = analyze_plan(CSV_PATH, validated)

        rows = {row["key"]: row for row in result["groups"]}
        first_partial = rows["2011-09-12_2011-09-18"]
        last_partial = rows["2011-09-26_2011-10-02"]
        self.assertEqual(first_partial["start"], date(2011, 9, 13))
        self.assertEqual(first_partial["end"], date(2011, 9, 18))
        self.assertEqual(first_partial["coverage"], "partial")
        self.assertEqual(last_partial["start"], date(2011, 9, 26))
        self.assertEqual(last_partial["end"], date(2011, 10, 1))
        self.assertEqual(last_partial["coverage"], "partial")

    def test_single_range_country_amount_ranking_does_not_need_second_range(self):
        validated = plan(
            analysis_type="summary",
            time_scope="date_range",
            start_date=date(2011, 9, 19),
            end_date=date(2011, 9, 25),
            group_by=["country"],
            ranking_order="desc",
            top_n=3,
        )
        result = analyze_plan(CSV_PATH, validated)

        self.assertEqual([row["key"] for row in result["groups"]], ["United Kingdom", "Netherlands", "EIRE"])
        self.assertAlmostEqual(result["groups"][0]["amount"], 276111.681)
        self.assertAlmostEqual(result["summary"]["total"], 338771.791)

    def test_legacy_comparison_adapter_preserves_existing_totals_and_boards(self):
        old = analyze_periods(
            CSV_PATH,
            date(2011, 9, 12), date(2011, 9, 18),
            date(2011, 9, 19), date(2011, 9, 25),
            dimensions=["country", "product"], direction="both", top_n=3,
        )
        validated = plan(
            analysis_type="comparison",
            time_scope="comparison",
            start_date=None,
            end_date=None,
            period1_start=date(2011, 9, 12), period1_end=date(2011, 9, 18),
            period2_start=date(2011, 9, 19), period2_end=date(2011, 9, 25),
            group_by=[], dimensions=["country", "product"], direction="both", top_n=3,
            ranking_order=None,
        )
        result = analyze_plan(CSV_PATH, validated)

        self.assertEqual(result["analysis_type"], "comparison")
        self.assertEqual(result["period1_total"], old["period1_total"])
        self.assertEqual(result["period2_total"], old["period2_total"])
        self.assertEqual(result["country_decreases"], old["country_decreases"])
        self.assertEqual(result["product_increases"], old["product_increases"])
        self.assertIn("total.change_pct", result["evidence"])

    def test_summary_evidence_is_generated_from_each_real_group(self):
        validated = plan()
        result = analyze_plan(CSV_PATH, validated)

        self.assertIn("summary.total", result["evidence"])
        self.assertIn("week.2011-09-19_2011-09-25.amount", result["evidence"])
        self.assertAlmostEqual(result["evidence"]["week.2011-09-19_2011-09-25.amount"]["value"], 338771.791)

    def test_weekly_full_data_plan_runs_report_and_review_path(self):
        validated = plan()
        draft = ReportDraft(
            title="每周销售汇总",
            conclusions=[{
                "text": "2011 年 9 月 19 日至 25 日所在周金额最高。",
                "evidence_ids": ["week.2011-09-19_2011-09-25.amount"],
            }],
        )
        events = []
        state = run_workflow(
            CSV_PATH,
            "从提供的数据开始到结束，哪一周卖得最好？",
            planner=lambda *_: validated,
            drafter=lambda _question, _plan, result: (draft, result["evidence"]),
            reviewer=lambda *_: ReviewResult(decision="approve", issues=[]),
            on_update=lambda node, update: events.append(node),
        )

        self.assertEqual(state["steps"], ["plan", "calculate", "draft_report", "review"])
        self.assertEqual(events, state["steps"])
        self.assertEqual(state["plan"].start_date, date(2011, 9, 12))
        self.assertEqual(state["plan"].end_date, date(2011, 10, 2))
        self.assertAlmostEqual(state["result"]["groups"][0]["amount"], 338771.791)
        self.assertEqual(state["review_result"].decision, "approve")
        self.assertIsNotNone(state["final_report"])

    def test_clarification_and_unsupported_plans_end_after_plan(self):
        for unsupported, question in ((False, "哪些商品卖得更好了？"), (True, "这次增长是因为促销吗？")):
            with self.subTest(question=question):
                validated = plan(
                    status="unsupported" if unsupported else "clarification",
                    analysis_type=None,
                    time_scope=None,
                    start_date=None,
                    end_date=None,
                    period1_start=None,
                    period1_end=None,
                    period2_start=None,
                    period2_end=None,
                    group_by=[],
                    dimensions=[],
                    direction=None,
                    ranking_order=None,
                    clarification_question=None if unsupported else "请补充前期和后期的日期区间。",
                    unsupported_reason="数据中没有促销活动字段，不能判断增长原因。" if unsupported else None,
                )
                state = run_workflow(
                    CSV_PATH,
                    question,
                    planner=lambda *_: validated,
                    calculator=lambda *_args, **_kwargs: self.fail("must not calculate"),
                    drafter=lambda *_args, **_kwargs: self.fail("must not draft"),
                    reviewer=lambda *_args, **_kwargs: ReviewResult(decision="approve", issues=[]),
                )
                self.assertEqual(state["steps"], ["plan"])
                self.assertEqual(state["plan"].status, "unsupported" if unsupported else "clarification")
                self.assertIsNone(state["result"])


if __name__ == "__main__":
    unittest.main()
