import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from langgraph_workflow import run_workflow
from planning_agent import AnalysisPlan
from report_reviewer import ReviewResult
from retail_analysis import (
    FilterResolutionError,
    analyze_plan,
    analyze_periods,
    resolve_filter_scope,
    validate_explicit_filter_extraction,
)


def write_csv(path: Path, rows: list[dict]) -> Path:
    pd.DataFrame(rows, columns=[
        "InvoiceNo", "InvoiceDate", "StockCode", "Description", "Quantity", "UnitPrice", "Country"
    ]).to_csv(path, index=False)
    return path


def transaction(invoice, day, code, description, quantity, price, country):
    return {
        "InvoiceNo": invoice,
        "InvoiceDate": f"2011-09-{day:02d} 12:00:00",
        "StockCode": code,
        "Description": description,
        "Quantity": quantity,
        "UnitPrice": price,
        "Country": country,
    }


def summary_plan(**overrides):
    payload = {
        "analysis_type": "summary",
        "time_scope": "date_range",
        "start_date": date(2011, 9, 19),
        "end_date": date(2011, 9, 25),
        "group_by": ["product"],
        "ranking_order": "desc",
        "top_n": 5,
        "country_filters": [],
        "product_filters": [],
    }
    payload.update(overrides)
    return AnalysisPlan.model_validate(payload)


class FilterAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_country_alias_and_product_description_resolve_to_csv_values(self):
        csv_path = write_csv(self.root / "entities.csv", [
            transaction("1", 19, "00243A", "Blue Mug", 2, 5, "United Kingdom"),
            transaction("2", 19, "00400", "Blue Mug", 3, 4, "United Kingdom"),
            transaction("3", 20, "00800", "Green Plate", 1, 7, "Germany"),
        ])

        scope = resolve_filter_scope(csv_path, ["英国", "DE"], ["00243A"])

        self.assertEqual(scope["countries"], ["United Kingdom", "Germany"])
        self.assertEqual(scope["products"], ["00243A"])
        self.assertEqual(scope["requested_countries"], ["英国", "DE"])
        self.assertEqual(resolve_filter_scope(csv_path, [], ["Green Plate"])["products"], ["00800"])
        with self.assertRaisesRegex(FilterResolutionError, "多个商品编号"):
            resolve_filter_scope(csv_path, [], ["Blue Mug"])

    def test_unknown_entity_is_not_silently_ignored(self):
        csv_path = write_csv(self.root / "entities.csv", [
            transaction("1", 19, "1", "Widget", 1, 2, "Germany"),
        ])

        with self.assertRaisesRegex(FilterResolutionError, "国家条件.*Mars"):
            resolve_filter_scope(csv_path, ["Mars"], [])

    def test_explicit_country_or_product_omitted_by_plan_requires_clarification(self):
        csv_path = write_csv(self.root / "mentioned.csv", [
            transaction("1", 19, "00243A", "Tea Coffee Sugar Tins Pantry Set", 1, 2, "United Kingdom"),
            transaction("2", 25, "00243A", "Tea Coffee Sugar Tins Pantry Set", 1, 2, "United Kingdom"),
        ])
        plan = summary_plan(country_filters=[], product_filters=[])
        scope = resolve_filter_scope(csv_path, [], [])
        for question, expected in (
            ("英国卖得最好的商品", "国家“United Kingdom”"),
            ("商品 00243A 卖得如何", "商品编号“00243A”"),
            ("Tea Coffee Sugar Tins Pantry Set 排名", "商品描述对应编号“00243A”"),
        ):
            with self.subTest(question=question):
                with self.assertRaisesRegex(FilterResolutionError, expected):
                    validate_explicit_filter_extraction(question, csv_path, scope)

        self.assertEqual(plan.country_filters, [])

    def test_single_range_filter_intersection_and_top_n_ten(self):
        rows = []
        for index in range(12):
            rows.append(transaction(
                str(index + 1), 25 if index == 11 else 19, f"{index:05d}", f"Item {index}",
                1, index + 1, "United Kingdom" if index < 11 else "Germany",
            ))
        csv_path = write_csv(self.root / "many.csv", rows)
        plan = summary_plan(
            group_by=["product"], top_n=10,
            country_filters=["英国"], product_filters=[],
        )
        scope = resolve_filter_scope(csv_path, plan.country_filters, plan.product_filters)

        result = analyze_plan(csv_path, plan, filter_scope=scope)

        self.assertEqual(len(result["groups"]), 10)
        self.assertEqual(result["groups"][0]["key"], "00010")
        self.assertEqual(result["filter_scope"]["countries"], ["United Kingdom"])
        self.assertAlmostEqual(result["summary"]["total"], sum(range(1, 12)))

    def test_country_and_product_filters_intersect_and_country_rank_uses_product_filter(self):
        csv_path = write_csv(self.root / "intersection.csv", [
            transaction("1", 19, "00243A", "Tea Tin", 2, 5, "United Kingdom"),
            transaction("2", 19, "00243A", "Tea Tin", 1, 5, "Germany"),
            transaction("3", 19, "00999", "Other Item", 20, 5, "United Kingdom"),
            transaction("4", 25, "00999", "Other Item", 1, 5, "Germany"),
        ])
        plan = summary_plan(
            group_by=["country"], country_filters=["英国"], product_filters=["00243A"],
        )
        scope = resolve_filter_scope(csv_path, plan.country_filters, plan.product_filters)
        result = analyze_plan(csv_path, plan, filter_scope=scope)

        self.assertEqual([row["key"] for row in result["groups"]], ["United Kingdom"])
        self.assertEqual(result["groups"][0]["amount"], 10)
        self.assertEqual(result["filter_scope"]["countries"], ["United Kingdom"])
        self.assertEqual(result["filter_scope"]["products"], ["00243A"])
        self.assertEqual(result["evidence"]["filter_scope"]["unit"], "scope")
        self.assertEqual(result["evidence"]["filter_scope"]["value"]["products"], ["00243A"])

    def test_matched_entities_without_range_rows_are_reported_as_no_records(self):
        csv_path = write_csv(self.root / "entities.csv", [
            transaction("1", 19, "00123", "Widget", 1, 2, "Germany"),
            transaction("2", 25, "00123", "Widget", 1, 2, "Germany"),
        ])
        plan = summary_plan(start_date=date(2011, 9, 20), end_date=date(2011, 9, 21), country_filters=["Germany"])
        scope = resolve_filter_scope(csv_path, plan.country_filters, plan.product_filters)
        result = analyze_plan(csv_path, plan, filter_scope=scope)

        self.assertEqual(result["summary"]["total"], 0)
        self.assertEqual(result["filter_scope"]["range_has_records"], False)
        self.assertIn("该日期范围内无符合筛选条件的记录", result["filter_scope"]["message"])
        self.assertEqual(result["evidence"]["filter_scope"]["value"]["range_has_records"], False)
        self.assertIn("无符合筛选条件", result["evidence"]["filter_scope"]["value"]["message"])

    def test_comparison_uses_same_intersection_for_both_periods_and_top_n(self):
        rows = [
            transaction("1", 19, "00001", "Widget A", 3, 10, "Netherlands"),
            transaction("2", 20, "00002", "Widget B", 2, 10, "Netherlands"),
            transaction("3", 26, "00001", "Widget A", 1, 10, "Netherlands"),
            transaction("4", 27, "00002", "Widget B", 1, 10, "Netherlands"),
            transaction("5", 19, "00003", "Widget C", 100, 10, "Germany"),
            transaction("6", 26, "00003", "Widget C", 1, 10, "Germany"),
        ]
        csv_path = write_csv(self.root / "compare.csv", rows)
        result = analyze_periods(
            csv_path,
            date(2011, 9, 19), date(2011, 9, 25),
            date(2011, 9, 26), date(2011, 10, 2),
            dimensions=["product"], direction="decline", top_n=10,
            country_filters=["NL"], product_filters=[],
        )

        self.assertEqual(result["period1_total"], 50)
        self.assertEqual(result["period2_total"], 20)
        self.assertEqual([row["StockCode"] for row in result["product_decreases"]], ["00001", "00002"])
        self.assertEqual(result["filter_scope"]["countries"], ["Netherlands"])
        self.assertIn("period1_has_records", result["filter_scope"])
        self.assertIn("period2_has_records", result["filter_scope"])

    def test_comparison_leaderboard_honors_top_n_above_five(self):
        rows = []
        for index in range(7):
            code = f"{index:05d}"
            rows.append(transaction(f"a{index}", 19, code, f"Item {index}", 10 + index, 1, "Netherlands"))
            rows.append(transaction(f"b{index}", 26, code, f"Item {index}", 1, 1, "Netherlands"))
        csv_path = write_csv(self.root / "ten.csv", rows)

        result = analyze_periods(
            csv_path,
            date(2011, 9, 19), date(2011, 9, 25),
            date(2011, 9, 26), date(2011, 10, 2),
            dimensions=["product"], direction="decline", top_n=10,
            country_filters=["Netherlands"],
        )

        self.assertEqual(len(result["product_decreases"]), 7)

    def test_comparison_product_board_excludes_fees_unless_explicitly_selected(self):
        csv_path = write_csv(self.root / "fees.csv", [
            transaction("1", 19, "POST", "POSTAGE", 10, 5, "Netherlands"),
            transaction("2", 26, "POST", "POSTAGE", 1, 5, "Netherlands"),
            transaction("3", 19, "00123", "Widget", 2, 10, "Netherlands"),
            transaction("4", 26, "00123", "Widget", 1, 10, "Netherlands"),
        ])
        args = (
            csv_path,
            date(2011, 9, 19), date(2011, 9, 25),
            date(2011, 9, 26), date(2011, 10, 2),
        )

        default_result = analyze_periods(
            *args, dimensions=["product"], direction="decline", top_n=5,
            country_filters=["Netherlands"],
        )
        explicit_fee_result = analyze_periods(
            *args, dimensions=["product"], direction="decline", top_n=5,
            country_filters=["Netherlands"], product_filters=["POST"],
        )

        self.assertEqual([row["StockCode"] for row in default_result["product_decreases"]], ["00123"])
        self.assertEqual([row["StockCode"] for row in explicit_fee_result["product_decreases"]], ["POST"])

    def test_legacy_plan_without_optional_filters_remains_unfiltered(self):
        old_plan = AnalysisPlan(
            analysis_type="comparison", time_scope="comparison",
            period1_start=date(2011, 9, 19), period1_end=date(2011, 9, 25),
            period2_start=date(2011, 9, 26), period2_end=date(2011, 10, 2),
            dimensions=[], direction=None,
        )
        self.assertEqual(old_plan.country_filters, [])
        self.assertEqual(old_plan.product_filters, [])

    def test_filter_resolution_failure_ends_workflow_after_plan(self):
        csv_path = write_csv(self.root / "entities.csv", [
            transaction("1", 19, "1", "Widget", 1, 2, "Germany"),
            transaction("2", 25, "1", "Widget", 1, 2, "Germany"),
        ])
        plan = summary_plan(country_filters=["Atlantis"])
        calls = []

        def planner(*_):
            return plan

        state = run_workflow(
            csv_path, "Atlantis 商品榜", planner=planner,
            calculator=lambda *_a, **_k: self.fail("unresolved filters must not calculate"),
            drafter=lambda *_a, **_k: self.fail("unresolved filters must not draft"),
            reviewer=lambda *_: ReviewResult(decision="approve", issues=[]),
        )

        self.assertEqual(state["steps"], ["plan"])
        self.assertEqual(state["plan"].status, "clarification")
        self.assertIn("Atlantis", state["clarification_question"])
        self.assertIsNone(state["result"])

    def test_ambiguous_product_description_asks_for_candidate_code(self):
        csv_path = write_csv(self.root / "ambiguous.csv", [
            transaction("1", 19, "00101", "Blue Mug Large", 1, 2, "Germany"),
            transaction("2", 20, "00102", "Blue Mug Small", 1, 3, "Germany"),
            transaction("3", 25, "00101", "Blue Mug Large", 1, 2, "Germany"),
        ])
        plan = summary_plan(product_filters=["Blue Mug"])
        state = run_workflow(
            csv_path, "Blue Mug 销售排名", planner=lambda *_: plan,
            calculator=lambda *_a, **_k: self.fail("ambiguous filters must not calculate"),
            drafter=lambda *_a, **_k: self.fail("ambiguous filters must not draft"),
            reviewer=lambda *_: ReviewResult(decision="approve", issues=[]),
        )

        self.assertEqual(state["steps"], ["plan"])
        self.assertIn("00101", state["clarification_question"])
        self.assertIn("00102", state["clarification_question"])

    def test_full_data_filter_failure_preserves_program_resolved_dates(self):
        csv_path = write_csv(self.root / "entities.csv", [
            transaction("1", 19, "1", "Widget", 1, 2, "Germany"),
            transaction("2", 25, "1", "Widget", 1, 2, "Germany"),
        ])
        plan = AnalysisPlan(
            analysis_type="summary", time_scope="full_data", group_by=["country"],
            ranking_order="desc", country_filters=["Atlantis"],
        )
        state = run_workflow(
            csv_path, "Atlantis 国家排名", planner=lambda *_: plan,
            calculator=lambda *_a, **_k: self.fail("unresolved filters must not calculate"),
            drafter=lambda *_a, **_k: self.fail("unresolved filters must not draft"),
            reviewer=lambda *_: ReviewResult(decision="approve", issues=[]),
        )

        self.assertEqual(state["steps"], ["plan"])
        self.assertEqual(state["plan"].status, "clarification")
        self.assertEqual(state["plan"].start_date, date(2011, 9, 19))
        self.assertEqual(state["plan"].end_date, date(2011, 9, 25))


if __name__ == "__main__":
    unittest.main()
