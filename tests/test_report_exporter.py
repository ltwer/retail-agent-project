import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from planning_agent import AnalysisPlan
from report_generator import ReportDraft, ReportConclusion
from report_reviewer import ReviewResult, ReviewIssue
from report_exporter import (
    export_approved_report,
    ReportExportError,
    summary_chart_payload,
)
from result_chart import render_result_chart
from retail_analysis import analyze_periods, analyze_plan


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "data" / "retail_demo.csv"


class ReportExporterTests(unittest.TestCase):
    def make_state(self, review_decision="approve", final=True):
        plan = AnalysisPlan(
            period1_start=date(2011, 9, 19),
            period1_end=date(2011, 9, 25),
            period2_start=date(2011, 9, 26),
            period2_end=date(2011, 10, 2),
            dimensions=["country", "product"],
            direction="decline",
            top_n=5,
        )
        result = analyze_periods(
            CSV_PATH,
            plan.period1_start,
            plan.period1_end,
            plan.period2_start,
            plan.period2_end,
            dimensions=plan.dimensions,
            direction=plan.direction,
            top_n=plan.top_n,
        )
        report = ReportDraft(
            title="两期销售变化分析",
            conclusions=[
                ReportConclusion(
                    text="国家榜单显示英国对本期金额下降贡献最大。",
                    evidence_ids=["country.United_Kingdom.delta"],
                ),
                ReportConclusion(
                    text="商品榜单中 23243 的金额降幅最大。",
                    evidence_ids=["product.23243.delta"],
                ),
            ],
        )
        if review_decision == "return":
            review = ReviewResult(
                decision="return",
                issues=[ReviewIssue(
                    conclusion_index=0,
                    evidence_ids=["country.United_Kingdom.delta"],
                    problem="待修改",
                    requested_change="补充解释",
                )],
            )
        else:
            review = ReviewResult(decision=review_decision, issues=[])
        return {
            "question": "验收 A",
            "plan": plan,
            "result": result,
            "final_report": report if final else None,
            "review_result": review,
            "evidence": {},
        }

    def test_approved_state_exports_markdown_and_png_with_matching_values(self):
        state = self.make_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path, image_path = export_approved_report(state, Path(temp_dir))

            self.assertTrue(markdown_path.is_file())
            self.assertTrue(image_path.is_file())
            self.assertEqual(markdown_path.name, "report.md")
            self.assertEqual(image_path.name, "period_comparison.png")
            content = markdown_path.read_text(encoding="utf-8")
            self.assertIn("£338,771.79", content)
            self.assertIn("£211,800.22", content)
            self.assertIn("-37.48%", content)
            self.assertIn("2011-09-19 至 2011-09-25", content)
            self.assertIn("2011-09-26 至 2011-10-02", content)
            self.assertIn("![两期金额对比](period_comparison.png)", content)
            self.assertIn("国家筛选：全部国家", content)
            self.assertIn("商品筛选：全部商品", content)
            self.assertIn("country.United_Kingdom.delta", content)
            self.assertIn("product.23243.delta", content)
            self.assertGreater(image_path.stat().st_size, 1000)
            with Image.open(image_path) as chart:
                self.assertLessEqual(chart.height, 600)
                pixels = np.asarray(chart.convert("RGB"))
                self.assertTrue(np.any(np.all(pixels == (47, 111, 85), axis=2)))
                self.assertTrue(np.any(np.all(pixels == (155, 201, 166), axis=2)))

    def test_growth_report_marks_positive_delta_and_rate_with_plus_sign(self):
        state = self.make_state()
        state["plan"].direction = "growth"
        state["result"] = analyze_periods(
            CSV_PATH,
            date(2011, 9, 12), date(2011, 9, 18),
            date(2011, 9, 19), date(2011, 9, 25),
            dimensions=["country", "product"], direction="growth", top_n=3,
        )
        state["plan"].period1_start = date(2011, 9, 12)
        state["plan"].period1_end = date(2011, 9, 18)
        state["plan"].period2_start = date(2011, 9, 19)
        state["plan"].period2_end = date(2011, 9, 25)
        state["plan"].top_n = 3
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path, _ = export_approved_report(state, temp_dir)
            content = markdown_path.read_text(encoding="utf-8")

        self.assertIn("+£108,634.53", content)
        self.assertIn("+47.20%", content)
        self.assertIn("+£67,207.90", content)

    def test_unapproved_or_missing_final_report_creates_no_output_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "outputs"
            for state in (self.make_state("return", final=False), self.make_state(final=False)):
                with self.assertRaises(ReportExportError):
                    export_approved_report(state, root)
            self.assertFalse(root.exists())

    def test_two_approved_exports_use_distinct_directories(self):
        state = self.make_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            first_md, first_png = export_approved_report(state, Path(temp_dir))
            second_md, second_png = export_approved_report(state, Path(temp_dir))
            self.assertNotEqual(first_md.parent, second_md.parent)
            self.assertTrue(first_md.is_file())
            self.assertTrue(first_png.is_file())
            self.assertTrue(second_md.is_file())
            self.assertTrue(second_png.is_file())

    def test_full_data_week_summary_exports_group_table_and_chart(self):
        plan = AnalysisPlan(
            status="executable",
            analysis_type="summary",
            time_scope="date_range",
            start_date=date(2011, 9, 13),
            end_date=date(2011, 10, 1),
            group_by=["week"],
            ranking_order="desc",
            top_n=5,
        )
        result = analyze_plan(CSV_PATH, plan)
        evidence = result["evidence"]
        report = ReportDraft(
            title="每周正向交易金额汇总",
            conclusions=[{
                "text": "2011 年 9 月 19 日至 25 日所在周的金额最高。",
                "evidence_ids": ["week.2011-09-19_2011-09-25.amount"],
            }],
        )
        state = {
            "plan": plan,
            "result": result,
            "final_report": report,
            "review_result": ReviewResult(decision="approve", issues=[]),
            "evidence": evidence,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path, image_path = export_approved_report(state, temp_dir)
            content = markdown_path.read_text(encoding="utf-8")

            self.assertIn("2011-09-19 至 2011-09-25", content)
            self.assertIn("£338,771.79", content)
            self.assertIn("week.2011-09-19_2011-09-25.amount", content)
            self.assertIn("部分覆盖自然周期", content)
            self.assertIn("![分组正向交易金额](period_comparison.png)", content)
            self.assertGreater(image_path.stat().st_size, 1000)

    def test_country_summary_export_does_not_show_an_empty_date_column(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["country"], ranking_order="desc", top_n=3,
            country_filters=["英国"],
        )
        result = analyze_plan(CSV_PATH, plan)
        report = ReportDraft(
            title="英国商品国家排名",
            conclusions=[{
                "text": "英国金额排名第一。",
                "evidence_ids": ["country.United_Kingdom.amount"],
            }],
        )
        state = {
            "plan": plan, "result": result, "final_report": report,
            "review_result": ReviewResult(decision="approve", issues=[]),
            "evidence": result["evidence"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path, _ = export_approved_report(state, temp_dir)
            content = markdown_path.read_text(encoding="utf-8")

        self.assertIn("| 项目 | 正向交易金额 | 证据编号 |", content)
        self.assertNotIn("| 项目 | 日期范围 |", content)
        self.assertIn("国家筛选：United Kingdom", content)

    def test_ranked_summary_report_answers_with_ordered_table_before_scope(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], ranking_order="desc", top_n=5,
            country_filters=["德国"],
        )
        result = analyze_plan(CSV_PATH, plan)
        report = ReportDraft(
            title="德国商品榜",
            conclusions=[{
                "text": "22580 位列德国商品金额榜首。",
                "evidence_ids": ["product.22580.amount"],
            }],
        )
        state = {
            "plan": plan, "result": result, "final_report": report,
            "review_result": ReviewResult(decision="approve", issues=[]),
            "evidence": result["evidence"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path, _ = export_approved_report(state, temp_dir)
            content = markdown_path.read_text(encoding="utf-8")

        self.assertLess(content.index("## 商品金额排名前 5"), content.index("## 分析范围与筛选"))
        self.assertIn("| 1 | 22580", content)
        self.assertIn("£237.60", content)
        self.assertLess(content.index("22580"), content.index("国家筛选：Germany"))

    def test_germany_quantity_top_five_markdown_uses_competition_ranks_and_explains_tie(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], metrics=["amount", "quantity"],
            rank_by="quantity", ranking_order="desc", top_n=5,
            country_filters=["Germany"],
        )
        result = analyze_plan(CSV_PATH, plan)
        report = ReportDraft(title="Germany quantity leaders", conclusions=[{
            "text": "The listed products are the highest-volume products in Germany.",
            "evidence_ids": ["ranking.product.overall"],
        }])
        state = {
            "plan": plan, "result": result, "final_report": report,
            "review_result": ReviewResult(decision="approve", issues=[]),
            "evidence": result["evidence"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path, _ = export_approved_report(state, temp_dir)
            content = markdown_path.read_text(encoding="utf-8")

        self.assertIn("请求前 5 名，第 5 名并列，实际展示 7 项。", content)
        expected_ranks = ["1", "1", "3", "4", "5", "5", "5"]
        product_lines = [line for line in content.splitlines() if line.startswith("| ") and any(code in line for code in ("21495", "21498", "22945", "22813", "21497", "21499", "21500"))]
        self.assertEqual([line.split("|")[1].strip() for line in product_lines], expected_ranks)
        self.assertIn("150 件", product_lines[0])
        self.assertIn("£", product_lines[0])

    def test_month_product_markdown_contains_all_filtered_details_and_monthly_leaders(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["month", "product"], metrics=["amount", "quantity"],
            rank_by="amount", ranking_order="desc", top_n=1,
            country_filters=["United Kingdom", "Germany"],
            product_filters=["23243", "22580", "21495"],
        )
        result = analyze_plan(CSV_PATH, plan)
        report = ReportDraft(title="Filtered monthly product details", conclusions=[{
            "text": "23243 leads by amount in each month.",
            "evidence_ids": [
                "ranking.month.2011-09.product", "ranking.month.2011-10.product",
            ],
        }])
        state = {
            "plan": plan, "result": result, "final_report": report,
            "review_result": ReviewResult(decision="approve", issues=[]),
            "evidence": result["evidence"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path, _ = export_approved_report(state, temp_dir)
            content = markdown_path.read_text(encoding="utf-8")

        self.assertIn("共 4 条明细", content)
        self.assertIn("2011-10", content)
        self.assertIn("23243", content)
        self.assertIn("£9.90", content)
        self.assertIn("2 件", content)
        self.assertIn("2011-09-12 至 2011-09-30", content)
        self.assertIn("2011-10-01 至 2011-10-02", content)
        self.assertEqual(content.count("榜\n"), 2)

    def test_ranked_product_chart_payload_keeps_top_ten_order_and_short_labels(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], ranking_order="desc", top_n=10,
        )
        result = analyze_plan(CSV_PATH, plan)
        payload = summary_chart_payload(plan, result)

        self.assertEqual(len(payload["labels"]), 10)
        self.assertEqual(payload["labels"][0], "23243")
        self.assertEqual(payload["labels"][1], "85099B")
        self.assertEqual(payload["values"][0], result["groups"][0]["amount"])
        self.assertEqual(payload["full_labels"][0], result["groups"][0]["label"])

    def test_top_ten_product_png_scales_vertically_for_readable_labels(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], ranking_order="desc", top_n=10,
        )
        result = analyze_plan(CSV_PATH, plan)

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "top_ten.png"
            render_result_chart(result, plan, image_path)
            with Image.open(image_path) as image:
                self.assertGreaterEqual(image.height, 850)
                self.assertGreater(image.width, 900)


if __name__ == "__main__":
    unittest.main()
