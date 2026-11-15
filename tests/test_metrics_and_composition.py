import csv
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from planning_agent import AnalysisPlan, metric_request_problem
from report_generator import ReportDraft, build_evidence, render_report_numbers
from report_reviewer import review_report
from report_exporter import export_approved_report
from retail_analysis import analyze_plan
from result_chart import summary_chart_payload
from report_reviewer import ReviewResult


CSV_FIELDS = [
    "InvoiceNo", "InvoiceDate", "StockCode", "Description", "Quantity",
    "UnitPrice", "Country",
]


class MetricsAndCompositionTests(unittest.TestCase):
    def write_csv(self, rows):
        temp = tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", suffix=".csv", delete=False)
        self.addCleanup(lambda: Path(temp.name).unlink(missing_ok=True))
        writer = csv.DictWriter(temp, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        temp.close()
        return Path(temp.name)

    def test_explicit_quantity_request_rejects_amount_plan_before_calculation(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], metrics=["amount"], rank_by="amount",
            ranking_order="desc", top_n=5,
        )
        problem = metric_request_problem("德国前五销量最多的商品是哪些？", plan)
        self.assertIsNotNone(problem)
        self.assertFalse(problem[1])
        self.assertIn("销量", problem[0])

    def test_amount_leader_is_explicit_when_amount_and_quantity_are_both_displayed(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["month", "product"], metrics=["amount", "quantity"],
            rank_by="amount", ranking_order="desc", top_n=1,
        )
        problem = metric_request_problem(
            "筛选英国、德国及商品 23243、22580、21495，按月 × 商品汇总金额和件数，并分别展示每月金额榜首",
            plan,
        )

        self.assertIsNone(problem)

    def test_amount_and_quantity_rank_different_products_and_keep_units(self):
        path = self.write_csv([
            {"InvoiceNo": "1", "InvoiceDate": "2011-09-12", "StockCode": "A", "Description": "Alpha", "Quantity": 3, "UnitPrice": 100, "Country": "Germany"},
            {"InvoiceNo": "2", "InvoiceDate": "2011-09-12", "StockCode": "B", "Description": "Beta", "Quantity": 20, "UnitPrice": 10, "Country": "Germany"},
        ])
        base = dict(
            status="executable", analysis_type="summary", time_scope="date_range",
            start_date=date(2011, 9, 12), end_date=date(2011, 9, 12),
            group_by=["product"], ranking_order="desc", top_n=2,
            country_filters=["Germany"],
        )
        amount_plan = AnalysisPlan(**base, metrics=["amount"], rank_by="amount")
        quantity_plan = AnalysisPlan(**base, metrics=["quantity"], rank_by="quantity")

        amount_result = analyze_plan(path, amount_plan)
        quantity_result = analyze_plan(path, quantity_plan)
        combined_plan = AnalysisPlan(**base, metrics=["amount", "quantity"], rank_by="amount")
        combined_result = analyze_plan(path, combined_plan)
        quantity_evidence = build_evidence(quantity_result, quantity_plan)
        quantity_summary = render_report_numbers(quantity_result, quantity_plan)
        chart_payload = summary_chart_payload(quantity_plan, quantity_result)
        switched_quantity_chart = summary_chart_payload(combined_plan, combined_result, metric="quantity")

        self.assertEqual(amount_result["groups"][0]["key"], "A")
        self.assertEqual(amount_result["groups"][0]["amount"], 300)
        self.assertEqual(quantity_result["groups"][0]["key"], "B")
        self.assertEqual(quantity_result["groups"][0]["quantity"], 20)
        self.assertEqual(quantity_result["rank_by"], "quantity")
        self.assertEqual(quantity_evidence["product.B.quantity"]["unit"], "items")
        self.assertEqual(quantity_evidence["ranking.product.overall"]["value"]["rows"][0]["value"], 20)
        self.assertEqual(quantity_summary["groups"][0]["quantity"], "20 件")
        self.assertEqual(chart_payload["metric"], "quantity")
        self.assertEqual(chart_payload["unit"], "件")
        self.assertEqual(switched_quantity_chart["labels"][0], "B")

    def test_germany_quantity_top_five_keeps_competition_ranks_across_ties(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], metrics=["amount", "quantity"],
            rank_by="quantity", ranking_order="desc", top_n=5,
            country_filters=["Germany"],
        )
        result = analyze_plan(PROJECT_ROOT / "data" / "retail_demo.csv", plan)
        ranking = result["rankings"][0]

        self.assertEqual([row["rank"] for row in ranking["rows"]], [1, 1, 3, 4, 5, 5, 5])
        self.assertEqual([row["rank"] for row in result["groups"]], [1, 1, 3, 4, 5, 5, 5])
        self.assertEqual([row["values"]["quantity"] for row in result["groups"]], [150, 150, 144, 132, 125, 125, 125])

    def test_month_product_filtered_details_keep_four_rows_and_each_month_ranks(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["month", "product"], metrics=["amount", "quantity"],
            rank_by="amount", ranking_order="desc", top_n=1,
            country_filters=["United Kingdom", "Germany"],
            product_filters=["23243", "22580", "21495"],
        )
        result = analyze_plan(PROJECT_ROOT / "data" / "retail_demo.csv", plan)

        self.assertEqual(len(result["groups"]), 4)
        october_row = next(row for row in result["groups"] if row["period_key"] == "2011-10")
        self.assertEqual(october_row["business_key"], "23243")
        self.assertEqual(october_row["values"], {"amount": 9.9, "quantity": 2.0})
        self.assertEqual(
            [(ranking["period_key"], ranking["rows"][0]["key"], ranking["rows"][0]["rank"])
             for ranking in result["rankings"]],
            [("2011-09", "23243", 1), ("2011-10", "23243", 1)],
        )

    def test_week_country_composition_keeps_full_rows_and_ranks_per_week(self):
        path = self.write_csv([
            {"InvoiceNo": "1", "InvoiceDate": "2011-09-12", "StockCode": "A", "Description": "Alpha", "Quantity": 1, "UnitPrice": 100, "Country": "Germany"},
            {"InvoiceNo": "2", "InvoiceDate": "2011-09-12", "StockCode": "B", "Description": "Beta", "Quantity": 1, "UnitPrice": 80, "Country": "France"},
            {"InvoiceNo": "3", "InvoiceDate": "2011-09-19", "StockCode": "A", "Description": "Alpha", "Quantity": 1, "UnitPrice": 50, "Country": "Germany"},
            {"InvoiceNo": "4", "InvoiceDate": "2011-09-19", "StockCode": "B", "Description": "Beta", "Quantity": 1, "UnitPrice": 120, "Country": "France"},
            {"InvoiceNo": "5", "InvoiceDate": "2011-09-25", "StockCode": "A", "Description": "Alpha", "Quantity": 1, "UnitPrice": 1, "Country": "Germany"},
        ])
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="date_range",
            start_date=date(2011, 9, 12), end_date=date(2011, 9, 25),
            group_by=["week", "country"], metrics=["amount", "quantity"],
            ranking_order="desc", rank_by="amount", top_n=1,
        )

        result = analyze_plan(path, plan)

        self.assertEqual(len(result["groups"]), 4)
        self.assertEqual(len(result["rankings"]), 2)
        winners = {row["period_key"]: row["rows"][0]["key"] for row in result["rankings"]}
        self.assertEqual(winners["2011-09-12_2011-09-18"], "Germany")
        self.assertEqual(winners["2011-09-19_2011-09-25"], "France")
        self.assertTrue(all("quantity" in row for row in result["groups"]))
        alternate_chart = summary_chart_payload(plan, result, metric="quantity")
        self.assertEqual(alternate_chart["kind"], "trend")
        self.assertEqual(alternate_chart["values"], [2.0, 3.0])
        self.assertIn("metric.quantity.policy", result["evidence"])
        self.assertEqual(
            result["evidence"]["metric.quantity.policy"]["value"]["excluded_non_product_fee_codes"],
            ["AMAZONFEE", "BANK CHARGES", "C2", "DOT", "POST"],
        )

        state = {
            "plan": plan,
            "result": result,
            "final_report": ReportDraft(title="Weekly country analysis", conclusions=[{
                "text": "各周金额榜首已分别列出；完整的各国每周件数明细见分组表。",
                "evidence_ids": ["ranking.week.2011-09-12_2011-09-18.country"],
            }]),
            "review_result": ReviewResult(decision="approve", issues=[]),
            "evidence": result["evidence"],
        }
        with tempfile.TemporaryDirectory() as output:
            markdown_path, _ = export_approved_report(state, Path(output))
            markdown = markdown_path.read_text(encoding="utf-8")
        self.assertIn("Germany", markdown)
        self.assertIn("France", markdown)
        self.assertIn("有效销售件数", markdown)
        self.assertIn("week.2011-09-12_2011-09-18.country.Germany.quantity", markdown)
        self.assertIn("每周正向交易金额最高榜", markdown)

    def test_summary_reviewer_does_not_require_prose_to_repeat_rendered_tables(self):
        path = self.write_csv([
            {"InvoiceNo": "1", "InvoiceDate": "2011-09-12", "StockCode": "A", "Description": "Alpha", "Quantity": 1, "UnitPrice": 10, "Country": "Germany"},
            {"InvoiceNo": "2", "InvoiceDate": "2011-09-19", "StockCode": "B", "Description": "Beta", "Quantity": 2, "UnitPrice": 20, "Country": "France"},
        ])
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="date_range",
            start_date=date(2011, 9, 12), end_date=date(2011, 9, 19),
            group_by=["week", "country"], metrics=["amount", "quantity"],
            rank_by="amount", ranking_order="desc", top_n=1,
        )
        result = analyze_plan(path, plan)
        draft = ReportDraft(title="Weekly analysis", conclusions=[{
            "text": "每周金额榜首分别列入自动生成的表格，完整件数表也由本地结果渲染。",
            "evidence_ids": ["summary.quantity"],
        }])
        env = {
            "OPENAI_API_MODE": "chat_completions", "OPENAI_API_KEY": "test",
            "OPENAI_MODEL": "test", "OPENAI_BASE_URL": "https://example.invalid/v1",
        }
        with patch("report_reviewer.chat_completion_json", return_value='{"decision":"approve","issues":[]}'):
            review = review_report(
                "每周哪个国家销售额最高，同时列出各国每周销量？",
                plan, result, draft, result["evidence"], client=object(), environ=env,
            )
        self.assertEqual(review.decision, "approve")

    def test_month_product_composition_reuses_metric_aggregation(self):
        path = self.write_csv([
            {"InvoiceNo": "1", "InvoiceDate": "2011-09-12", "StockCode": "A", "Description": "Alpha", "Quantity": 3, "UnitPrice": 10, "Country": "Germany"},
            {"InvoiceNo": "2", "InvoiceDate": "2011-10-02", "StockCode": "A", "Description": "Alpha", "Quantity": 2, "UnitPrice": 10, "Country": "Germany"},
            {"InvoiceNo": "3", "InvoiceDate": "2011-10-02", "StockCode": "B", "Description": "Beta", "Quantity": 4, "UnitPrice": 5, "Country": "Germany"},
        ])
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="date_range",
            start_date=date(2011, 9, 12), end_date=date(2011, 10, 2),
            group_by=["month", "product"], metrics=["quantity"],
            ranking_order="desc", rank_by="quantity", top_n=2,
        )

        result = analyze_plan(path, plan)

        self.assertEqual(len(result["groups"]), 3)
        self.assertEqual(len(result["rankings"]), 2)
        october = next(row for row in result["rankings"] if row["period_key"] == "2011-10")
        self.assertEqual(october["rows"][0]["key"], "B")
        self.assertEqual(october["rows"][0]["value"], 4)

    def test_top_one_includes_all_entities_tied_for_first(self):
        path = self.write_csv([
            {"InvoiceNo": "1", "InvoiceDate": "2011-09-12", "StockCode": "A", "Description": "Alpha", "Quantity": 3, "UnitPrice": 10, "Country": "Germany"},
            {"InvoiceNo": "2", "InvoiceDate": "2011-09-12", "StockCode": "B", "Description": "Beta", "Quantity": 3, "UnitPrice": 10, "Country": "Germany"},
            {"InvoiceNo": "3", "InvoiceDate": "2011-09-12", "StockCode": "C", "Description": "Gamma", "Quantity": 1, "UnitPrice": 10, "Country": "Germany"},
        ])
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="date_range",
            start_date=date(2011, 9, 12), end_date=date(2011, 9, 12),
            group_by=["product"], metrics=["amount"], rank_by="amount",
            ranking_order="desc", top_n=1,
        )
        result = analyze_plan(path, plan)
        self.assertEqual([row["key"] for row in result["groups"]], ["A", "B"])
        self.assertEqual(result["rankings"][0]["actual_count"], 2)
        self.assertEqual([row["key"] for row in result["rankings"][0]["leaders"]], ["A", "B"])

    def test_two_period_quantity_comparison_uses_items_and_keeps_legacy_amount_default(self):
        path = self.write_csv([
            {"InvoiceNo": "1", "InvoiceDate": "2011-09-12", "StockCode": "A", "Description": "Alpha", "Quantity": 3, "UnitPrice": 100, "Country": "Germany"},
            {"InvoiceNo": "2", "InvoiceDate": "2011-09-19", "StockCode": "B", "Description": "Beta", "Quantity": 20, "UnitPrice": 10, "Country": "Germany"},
            {"InvoiceNo": "3", "InvoiceDate": "2011-09-19", "StockCode": "POST", "Description": "Postage", "Quantity": 1, "UnitPrice": 5, "Country": "Germany"},
        ])
        base = dict(
            status="executable", analysis_type="comparison", time_scope="comparison",
            period1_start=date(2011, 9, 12), period1_end=date(2011, 9, 12),
            period2_start=date(2011, 9, 19), period2_end=date(2011, 9, 19),
            dimensions=["product"], direction="both", top_n=5,
        )

        quantity_result = analyze_plan(path, AnalysisPlan(**base, metrics=["quantity"], rank_by="quantity"))
        legacy_result = analyze_plan(path, AnalysisPlan(**base))

        self.assertEqual(quantity_result["period1_total"], 3)
        self.assertEqual(quantity_result["period2_total"], 20)
        self.assertEqual(quantity_result["product_increases"][0]["StockCode"], "B")
        self.assertEqual(quantity_result["metric"], "quantity")
        self.assertEqual(legacy_result["period1_total"], 300)
        self.assertEqual(legacy_result["period2_total"], 205)

    def test_ranking_claim_with_only_total_evidence_is_returned(self):
        path = self.write_csv([
            {"InvoiceNo": "1", "InvoiceDate": "2011-09-12", "StockCode": "A", "Description": "Alpha", "Quantity": 3, "UnitPrice": 100, "Country": "Germany"},
            {"InvoiceNo": "2", "InvoiceDate": "2011-09-12", "StockCode": "B", "Description": "Beta", "Quantity": 20, "UnitPrice": 10, "Country": "Germany"},
        ])
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="date_range",
            start_date=date(2011, 9, 12), end_date=date(2011, 9, 12),
            group_by=["product"], metrics=["quantity"], rank_by="quantity",
            ranking_order="desc", top_n=2,
        )
        result = analyze_plan(path, plan)
        draft = ReportDraft(title="商品销量排名", conclusions=[{
            "text": "商品 B 销量排名第一。", "evidence_ids": ["summary.quantity"],
        }])

        review = review_report(
            "销量最多的商品是什么？", plan, result, draft, result["evidence"],
            client=object(), environ={},
        )

        self.assertEqual(review.decision, "return")
        self.assertIn("排名", review.issues[0].problem)
        self.assertIn("summary.quantity", review.issues[0].evidence_ids)

    def test_wrong_entity_claim_with_valid_ranking_evidence_is_returned(self):
        path = self.write_csv([
            {"InvoiceNo": "1", "InvoiceDate": "2011-09-12", "StockCode": "A", "Description": "Alpha", "Quantity": 3, "UnitPrice": 100, "Country": "Germany"},
            {"InvoiceNo": "2", "InvoiceDate": "2011-09-12", "StockCode": "B", "Description": "Beta", "Quantity": 20, "UnitPrice": 10, "Country": "Germany"},
        ])
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="date_range",
            start_date=date(2011, 9, 12), end_date=date(2011, 9, 12),
            group_by=["product"], metrics=["amount"], rank_by="amount",
            ranking_order="desc", top_n=2,
        )
        result = analyze_plan(path, plan)
        draft = ReportDraft(title="商品金额排名", conclusions=[{
            "text": "商品 B 的销售额最高，排名第一。",
            "evidence_ids": ["ranking.product.overall"],
        }])

        env = {
            "OPENAI_API_MODE": "chat_completions", "OPENAI_API_KEY": "test",
            "OPENAI_MODEL": "test", "OPENAI_BASE_URL": "https://example.invalid/v1",
        }
        with patch("report_reviewer.chat_completion_json", return_value='{"decision":"approve","issues":[]}'):
            review = review_report(
                "销售额最高的商品是什么？", plan, result, draft, result["evidence"],
                client=object(), environ=env,
            )

        self.assertEqual(review.decision, "return")
        self.assertIn("B", review.issues[0].problem)

    def test_scope_sentence_about_ranking_request_does_not_need_its_own_ranking_citation(self):
        path = self.write_csv([
            {"InvoiceNo": "1", "InvoiceDate": "2011-09-12", "StockCode": "A", "Description": "Alpha", "Quantity": 3, "UnitPrice": 100, "Country": "Germany"},
            {"InvoiceNo": "2", "InvoiceDate": "2011-09-12", "StockCode": "B", "Description": "Beta", "Quantity": 20, "UnitPrice": 10, "Country": "France"},
        ])
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="date_range",
            start_date=date(2011, 9, 12), end_date=date(2011, 9, 12),
            group_by=["country"], metrics=["amount", "quantity"], rank_by="amount",
            ranking_order="desc", top_n=2,
        )
        result = analyze_plan(path, plan)
        draft = ReportDraft(title="国家金额排名", conclusions=[
            {"text": "本报告回答哪个国家销售额最高，并保留各国销量明细。", "evidence_ids": ["filter_scope"]},
            {"text": "Germany 销售额最高。", "evidence_ids": ["ranking.country.overall"]},
        ])
        env = {
            "OPENAI_API_MODE": "chat_completions", "OPENAI_API_KEY": "test",
            "OPENAI_MODEL": "test", "OPENAI_BASE_URL": "https://example.invalid/v1",
        }
        with patch("report_reviewer.chat_completion_json", return_value='{"decision":"approve","issues":[]}'):
            review = review_report(
                "哪个国家销售额最高？同时列出各国销量", plan, result, draft, result["evidence"],
                client=object(), environ=env,
            )
        self.assertEqual(review.decision, "approve")


if __name__ == "__main__":
    unittest.main()
