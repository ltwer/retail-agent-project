import unittest
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from planning_agent import AnalysisPlan, metric_request_problem
from retail_analysis import analyze_plan
from conversation_context import (
    apply_plan_changes,
    bind_context_to_source,
    classify_user_turn,
    merge_clarified_dates,
    serialize_plan_for_context,
    source_identity,
    summarize_plan_conditions,
    build_reference_catalog,
    resolve_reference,
    resolve_drill_choice,
    merge_drilldown_plan,
)


class ConversationContextTests(unittest.TestCase):
    csv_path = Path(__file__).resolve().parents[1] / "data" / "retail_demo.csv"

    def setUp(self):
        self.successful_plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], metrics=["quantity"], rank_by="quantity",
            ranking_order="desc", top_n=5, country_filters=["德国"],
        )

    def test_source_identity_changes_when_same_named_csv_content_changes(self):
        first = source_identity("upload", "retail.csv", b"first csv")
        second = source_identity("upload", "retail.csv", b"changed csv")

        self.assertNotEqual(first, second)

    def test_source_switch_clears_pending_and_inheritable_plan(self):
        context = {
            "data_identity": "old",
            "pending": {"kind": "clarification"},
            "last_success": {"plan": self.successful_plan.model_dump(mode="json")},
            "reference_catalog": {"boards": ["old board"]},
        }

        updated, changed = bind_context_to_source(context, "new")

        self.assertTrue(changed)
        self.assertIsNone(updated["pending"])
        self.assertIsNone(updated["last_success"])
        self.assertIsNone(updated["reference_catalog"])
        self.assertEqual(updated["data_identity"], "new")

    def test_classifies_supported_followup_operations(self):
        context = {"data_identity": "same", "last_success": {"plan": self.successful_plan.model_dump(mode="json")}}

        self.assertEqual(classify_user_turn("那英国呢？", context, "same").kind, "modify_previous")
        self.assertEqual(classify_user_turn("那 United Kingdom 呢？", context, "same").changes,
                         {"country_filters": ["United Kingdom"]})
        self.assertEqual(classify_user_turn("改按销售额排。", context, "same").changes, {"rank_by": "amount"})
        self.assertEqual(classify_user_turn("只看前 3。", context, "same").changes, {"top_n": 3})

    def test_self_contained_question_does_not_inherit_previous_plan(self):
        context = {"data_identity": "same", "last_success": {"plan": self.successful_plan.model_dump(mode="json")}}

        decision = classify_user_turn("英国销量最高的 5 个商品是什么？", context, "same")

        self.assertEqual(decision.kind, "new_question")

    def test_followup_without_matching_context_asks_for_analysis_subject(self):
        decision = classify_user_turn("那英国呢？", {}, "same")

        self.assertEqual(decision.kind, "ambiguous")
        self.assertIn("英国", decision.clarification)

    def test_date_only_reply_continues_pending_comparison(self):
        context = {"data_identity": "same", "pending": {"kind": "clarification"}}

        decision = classify_user_turn("2011 年 9 月 19–25 日和 9 月 26 日–10 月 2 日", context, "same")

        self.assertEqual(decision.kind, "answer_clarification")

    def test_country_and_rank_metric_changes_preserve_other_plan_conditions(self):
        updated = apply_plan_changes(self.successful_plan, {"country_filters": ["英国"], "rank_by": "amount"})

        self.assertEqual(updated.country_filters, ["英国"])
        self.assertEqual(updated.rank_by, "amount")
        self.assertEqual(updated.metrics, ["quantity", "amount"])
        self.assertEqual(updated.top_n, 5)
        self.assertEqual(updated.group_by, ["product"])

    def test_clarified_dates_fill_pending_comparison_without_losing_intent(self):
        pending = AnalysisPlan(
            status="clarification", analysis_type="comparison", time_scope="comparison",
            dimensions=["product"], direction="growth", rank_by="amount", metrics=["amount"], top_n=5,
            clarification_question="请补充两个比较日期区间。",
        )
        parsed = AnalysisPlan(
            status="executable", analysis_type="comparison", time_scope="comparison",
            period1_start=date(2011, 9, 19), period1_end=date(2011, 9, 25),
            period2_start=date(2011, 9, 26), period2_end=date(2011, 10, 2),
            dimensions=["country"], direction="decline", rank_by="quantity", metrics=["quantity"], top_n=2,
        )

        merged = merge_clarified_dates(pending, parsed)

        self.assertEqual(merged.status, "executable")
        self.assertEqual(merged.dimensions, ["product"])
        self.assertEqual(merged.direction, "growth")
        self.assertEqual(merged.rank_by, "amount")
        self.assertEqual(merged.top_n, 5)
        self.assertEqual(merged.period1_start, date(2011, 9, 19))
        self.assertEqual(merged.period2_end, date(2011, 10, 2))

    def test_condition_summary_names_dates_filters_metrics_and_rank_limit(self):
        summary = summarize_plan_conditions(self.successful_plan)

        self.assertIn("德国", summary)
        self.assertIn("有效销售件数", summary)
        self.assertIn("前 5", summary)

    def test_effective_conditions_make_changed_rank_metric_unambiguous_to_validator(self):
        plan = apply_plan_changes(self.successful_plan, {"rank_by": "amount"})
        effective = summarize_plan_conditions(plan)

        self.assertIn("按金额最高排序", effective)
        self.assertIsNone(metric_request_problem(effective, plan))

    def test_full_data_plan_round_trips_after_workflow_resolves_actual_dates(self):
        plan = self.successful_plan.model_copy(update={
            "start_date": date(2011, 9, 12), "end_date": date(2011, 10, 2),
        })

        payload = serialize_plan_for_context(plan)
        restored = AnalysisPlan.model_validate(payload)

        self.assertIsNone(restored.start_date)
        self.assertIsNone(restored.end_date)

    def test_sequential_country_metric_and_top_n_changes_keep_other_conditions(self):
        german_quantity = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], metrics=["quantity"], rank_by="quantity",
            ranking_order="desc", top_n=5, country_filters=["德国"],
        )
        british_quantity = apply_plan_changes(german_quantity, {"country_filters": ["英国"]})
        british_amount = apply_plan_changes(british_quantity, {"rank_by": "amount"})
        british_top_three = apply_plan_changes(british_amount, {"top_n": 3})
        result = analyze_plan(self.csv_path, british_top_three)

        self.assertEqual(british_top_three.country_filters, ["英国"])
        self.assertEqual(british_top_three.rank_by, "amount")
        self.assertEqual(british_top_three.top_n, 3)
        self.assertEqual(result["groups"][0]["key"], "23243")
        self.assertAlmostEqual(result["groups"][0]["values"]["amount"], 9204.45)

    def test_date_answer_preserves_product_growth_ranking_and_calculates_leader(self):
        pending = AnalysisPlan(
            status="clarification", analysis_type="comparison", time_scope="comparison",
            dimensions=["product"], direction="growth", rank_by="amount", metrics=["amount"], top_n=5,
            clarification_question="请补充前后期日期。",
        )
        parsed = AnalysisPlan(
            status="executable", analysis_type="comparison", time_scope="comparison",
            period1_start=date(2011, 9, 19), period1_end=date(2011, 9, 25),
            period2_start=date(2011, 9, 26), period2_end=date(2011, 10, 2),
            dimensions=["country"], direction="decline", rank_by="quantity", metrics=["quantity"], top_n=2,
        )

        merged = merge_clarified_dates(pending, parsed)
        result = analyze_plan(self.csv_path, merged)

        self.assertEqual((merged.dimensions, merged.direction, merged.rank_by, merged.top_n),
                         (["product"], "growth", "amount", 5))
        self.assertEqual(result["product_increases"][0]["StockCode"], "23284")
        self.assertAlmostEqual(result["product_increases"][0]["difference"], 4473.63)

    def test_reference_catalog_uses_real_germany_amount_ranking_and_evidence(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], metrics=["amount"], rank_by="amount",
            ranking_order="desc", top_n=5, country_filters=["Germany"],
        )
        result = analyze_plan(self.csv_path, plan)

        catalog = build_reference_catalog(plan, result)
        resolution = resolve_reference(catalog, "product_leader")

        self.assertEqual(resolution["status"], "resolved")
        self.assertEqual(resolution["candidate"]["entity_key"], "22580")
        self.assertEqual(resolution["candidate"]["rank"], 1)
        self.assertEqual(resolution["candidate"]["metric"], "amount")
        self.assertIn("ranking.product.overall", resolution["candidate"]["evidence_ids"])
        self.assertIn("product.22580.amount", resolution["candidate"]["evidence_ids"])

    def test_tied_germany_quantity_leaders_return_both_candidates(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], metrics=["quantity"], rank_by="quantity",
            ranking_order="desc", top_n=5, country_filters=["Germany"],
        )
        result = analyze_plan(self.csv_path, plan)

        resolution = resolve_reference(build_reference_catalog(plan, result), "product_leader")

        self.assertEqual(resolution["status"], "clarification")
        self.assertEqual([item["entity_key"] for item in resolution["candidates"]], ["21495", "21498"])
        self.assertTrue(all(item["metric"] == "quantity" and item["rank"] == 1 for item in resolution["candidates"]))

    def test_candidate_number_resolves_a_tied_leader(self):
        candidates = [
            {"dimension": "product", "entity_key": "21495", "entity_label": "21495 - SKULLS WRAP"},
            {"dimension": "product", "entity_key": "21498", "entity_label": "21498 - RED WRAP"},
        ]

        selection = resolve_drill_choice(candidates, "21498")

        self.assertEqual(selection["status"], "resolved")
        self.assertEqual(selection["candidate"]["entity_key"], "21498")

    def test_candidate_number_resolves_repeated_entity_across_period_boards(self):
        candidates = [
            {"dimension": "product", "entity_key": "21498", "entity_label": "21498 - RED WRAP",
             "rank": 1, "board_id": "week1", "board_evidence_id": "ranking.week1.product", "period_label": "第一周",
             "evidence_ids": ["ranking.week1.product", "product.21498.quantity"]},
            {"dimension": "product", "entity_key": "21498", "entity_label": "21498 - RED WRAP",
             "rank": 1, "board_id": "week2", "board_evidence_id": "ranking.week2.product", "period_label": "第二周",
             "evidence_ids": ["ranking.week2.product", "product.21498.quantity"]},
        ]

        selection = resolve_drill_choice(candidates, "21498")

        self.assertEqual(selection["status"], "resolved")
        self.assertEqual(selection["candidate"]["entity_key"], "21498")
        self.assertEqual(selection["candidate"]["board_evidence_ids"], [
            "ranking.week1.product", "ranking.week2.product",
        ])
        self.assertEqual(selection["candidate"]["evidence_ids"], [
            "ranking.week1.product", "product.21498.quantity", "ranking.week2.product",
        ])

    def test_reference_catalog_uses_decline_country_leader_from_comparison_result(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="comparison", time_scope="comparison",
            period1_start=date(2011, 9, 12), period1_end=date(2011, 9, 18),
            period2_start=date(2011, 9, 19), period2_end=date(2011, 9, 25),
            metrics=["amount"], rank_by="amount", dimensions=["country"],
            direction="decline", top_n=5,
        )
        result = analyze_plan(self.csv_path, plan)

        resolution = resolve_reference(build_reference_catalog(plan, result), "country_decline_leader")

        self.assertEqual(resolution["status"], "resolved")
        self.assertEqual(resolution["candidate"]["entity_key"], "Sweden")
        self.assertEqual(resolution["candidate"]["rank"], 1)
        self.assertIn("ranking.country.decline", resolution["candidate"]["evidence_ids"])

    def test_reference_resolution_reports_ambiguous_period_boards(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["week", "country"], metrics=["amount"], rank_by="amount",
            ranking_order="desc", top_n=1,
        )
        result = analyze_plan(self.csv_path, plan)

        resolution = resolve_reference(build_reference_catalog(plan, result), "country_leader")

        self.assertEqual(resolution["status"], "clarification")
        self.assertGreater(len(resolution["candidates"]), 1)
        self.assertTrue(all(item["period_label"] for item in resolution["candidates"]))

    def test_drilldown_plan_inherits_relevant_scope_and_drops_old_rank_limit(self):
        source = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="date_range",
            start_date=date(2011, 9, 12), end_date=date(2011, 10, 2),
            group_by=["product"], metrics=["amount"], rank_by="amount",
            ranking_order="desc", top_n=5, country_filters=["Germany"],
        )
        request = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["week"], metrics=["quantity"], rank_by="quantity",
        )
        candidate = {
            "dimension": "product", "entity_key": "22580", "entity_label": "22580 - ADVENT CALENDAR GINGHAM SACK",
            "rank": 1, "metric": "amount", "evidence_ids": ["ranking.product.overall"],
        }

        merged = merge_drilldown_plan(source, request, candidate)

        self.assertEqual(merged.analysis_type, "summary")
        self.assertEqual(merged.time_scope, "date_range")
        self.assertEqual((merged.start_date, merged.end_date), (date(2011, 9, 12), date(2011, 10, 2)))
        self.assertEqual(merged.country_filters, ["Germany"])
        self.assertEqual(merged.product_filters, ["22580"])
        self.assertEqual(merged.group_by, ["week"])
        self.assertEqual(merged.metrics, ["quantity"])
        self.assertIsNone(merged.ranking_order)
        self.assertEqual(merged.top_n, 5)

    def test_drilldown_clears_metadata_dates_from_a_full_data_plan_before_validation(self):
        source = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], metrics=["amount"], rank_by="amount",
            ranking_order="desc", top_n=5, country_filters=["Germany"],
        )
        # plan_question validates the full-data range in-place after model validation.
        request = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["week"], metrics=["quantity"], rank_by="quantity",
        ).model_copy(update={"start_date": date(2011, 9, 12), "end_date": date(2011, 10, 2)})
        candidate = {"dimension": "product", "entity_key": "22580", "entity_label": "22580"}

        merged = merge_drilldown_plan(source, request, candidate, "这个商品每周卖了多少件？")

        self.assertEqual(merged.time_scope, "full_data")
        self.assertIsNone(merged.start_date)
        self.assertIsNone(merged.end_date)

    def test_drilldown_preserves_non_executable_planner_status_and_explanation(self):
        source = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], metrics=["amount"], rank_by="amount",
            ranking_order="desc", top_n=5, country_filters=["Germany"],
        )
        request = AnalysisPlan(
            status="unsupported", analysis_type="summary", time_scope="full_data",
            group_by=["week"], metrics=["quantity"], rank_by="quantity",
            unsupported_reason="当前能力不支持该请求。",
        )

        merged = merge_drilldown_plan(
            source, request, {"dimension": "product", "entity_key": "22580"},
            "这个商品在德国每周卖了多少件？",
        )

        self.assertEqual(merged.status, "unsupported")
        self.assertEqual(merged.unsupported_reason, "当前能力不支持该请求。")

    def test_drilldown_comparison_keeps_new_periods_and_adds_referenced_product(self):
        source = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], metrics=["amount"], rank_by="amount",
            ranking_order="desc", top_n=5, country_filters=["Germany"],
        )
        request = AnalysisPlan(
            status="executable", analysis_type="comparison", time_scope="comparison",
            period1_start=date(2011, 9, 19), period1_end=date(2011, 9, 25),
            period2_start=date(2011, 9, 26), period2_end=date(2011, 10, 2),
            dimensions=["country"], direction="decline", metrics=["amount"], rank_by="amount",
        )

        merged = merge_drilldown_plan(
            source, request, {"dimension": "product", "entity_key": "22580"},
            "这个商品比较两期金额变化",
        )

        self.assertEqual(merged.analysis_type, "comparison")
        self.assertEqual(merged.product_filters, ["22580"])
        self.assertEqual(merged.country_filters, ["Germany"])
        self.assertEqual((merged.period1_start, merged.period2_end), (date(2011, 9, 19), date(2011, 10, 2)))

    def test_reference_phrases_are_not_misclassified_as_self_contained_questions(self):
        context = {
            "data_identity": "same",
            "last_success": {
                "plan": self.successful_plan.model_dump(mode="json"),
                "reference_catalog": {"boards": [{"kind": "summary_rank"}]},
            },
        }

        decision = classify_user_turn("榜首商品每周卖了多少件？", context, "same")

        self.assertEqual(decision.kind, "reference_followup")

    def test_candidate_number_reply_resumes_pending_drill_choice(self):
        context = {
            "data_identity": "same",
            "pending": {"kind": "drill_choice", "candidates": [
                {"dimension": "product", "entity_key": "21495", "entity_label": "21495 wrap"},
                {"dimension": "product", "entity_key": "21498", "entity_label": "21498 wrap"},
            ]},
        }

        decision = classify_user_turn("21498", context, "same")

        self.assertEqual(decision.kind, "answer_drill_choice")
        self.assertEqual(decision.changes["candidate"]["entity_key"], "21498")

    def test_start_new_or_missing_catalog_cannot_resolve_result_reference(self):
        decision = classify_user_turn("这个商品每周卖了多少件？", {}, "same")

        self.assertEqual(decision.kind, "ambiguous")
        self.assertIn("指定商品编号或国家", decision.clarification)


if __name__ == "__main__":
    unittest.main()
