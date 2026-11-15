import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from planning_agent import AnalysisPlan
from report_exporter import export_approved_report
from report_generator import ReportDraft, build_evidence
from report_reviewer import ReviewIssue, ReviewResult
from retail_analysis import analyze_periods, analyze_plan
from web_presentation import completed_node_event


class StreamlitAppTests(unittest.TestCase):
    def test_switching_data_source_clears_runs_and_conversation_context(self):
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
        app.run(timeout=20)
        active_identity = app.session_state["conversation_context"]["data_identity"]
        app.session_state["run_history"] = [{
            "id": "old", "status": "clarification", "question": "old",
            "trace": [], "state": {"clarification_question": "补充日期"},
        }]
        app.session_state["conversation_context"] = {
            "data_identity": active_identity,
            "pending": {"kind": "clarification"},
            "last_success": {"plan": {"status": "executable"}},
            "reference_catalog": {"boards": [{"entity_key": "old"}]},
        }
        app.run(timeout=20)

        app.radio[0].set_value("上传 CSV").run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state["run_history"], [])
        self.assertIsNone(app.session_state["conversation_context"]["pending"])
        self.assertIsNone(app.session_state["conversation_context"]["last_success"])
        self.assertIsNone(app.session_state["conversation_context"]["reference_catalog"])

    def test_start_new_analysis_clears_pending_plan_and_current_result(self):
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
        app.run(timeout=20)
        identity = app.session_state["conversation_context"]["data_identity"]
        app.session_state["run_history"] = [{
            "id": "old", "status": "clarification", "question": "旧问题",
            "trace": [], "state": {"clarification_question": "补充日期"},
        }]
        app.session_state["conversation_context"] = {
            "data_identity": identity,
            "pending": {"kind": "clarification"},
            "last_success": {"plan": {"status": "executable"}},
            "reference_catalog": {"boards": [{"entity_key": "old"}]},
        }
        app.run(timeout=20)

        app.button(key="start_new_analysis").click().run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state["run_history"], [])
        self.assertIsNone(app.session_state["conversation_context"]["pending"])
        self.assertIsNone(app.session_state["conversation_context"]["last_success"])
        self.assertIsNone(app.session_state["conversation_context"]["reference_catalog"])
        self.assertEqual(app.session_state["question_input"], "")

    def test_drill_choice_clarification_lists_candidates_and_has_distinct_status(self):
        run = {
            "id": "drill-choice", "status": "clarification", "clarification_kind": "drill_choice",
            "question": "榜首商品每周卖了多少件？", "source_name": "retail_demo.csv",
            "conditions": "沿用上轮德国销量榜；候选对象尚未确定。",
            "clarification_question": "该引用对应多个候选，请回复 21495 或 21498。",
            "trace": [], "state": None, "error": None,
        }

        app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
        app.run(timeout=20)
        app.session_state["run_history"] = [run]
        app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        markdown = "\n".join(item.value for item in app.markdown)
        info_text = "\n".join(item.value for item in app.info)
        self.assertIn("需选择引用对象", markdown)
        self.assertIn("21495", info_text)
        self.assertIn("21498", info_text)
        self.assertEqual(len(app.download_button), 0)

    def test_combined_metric_radio_uses_existing_result_without_rerunning_workflow(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["week", "country"], metrics=["amount", "quantity"],
            rank_by="amount", ranking_order="desc", top_n=1,
        )
        result = analyze_plan(PROJECT_ROOT / "data" / "retail_demo.csv", plan)
        report = ReportDraft(title="Weekly country analysis", conclusions=[{
            "text": "各周金额榜首和完整件数明细均由本地结果渲染。",
            "evidence_ids": ["ranking.week.2011-09-12_2011-09-18.country"],
        }])
        state = {
            "plan": plan, "result": result, "report_draft": report,
            "final_report": report, "evidence": result["evidence"],
            "review_result": ReviewResult(decision="approve", issues=[]),
            "review_attempts": 1, "clarification_question": None,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path, image_path = export_approved_report(state, temp_dir)
            run = {
                "id": "combined-metrics", "question": "每周哪个国家销售额最高并列出各国每周销量？",
                "source_name": "retail_demo.csv", "status": "approved", "trace": [],
                "state": state, "report_path": str(report_path), "image_path": str(image_path),
            }
            with patch("langgraph_workflow.run_workflow", side_effect=AssertionError("workflow must not rerun")):
                app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
                app.run(timeout=20)
                app.session_state["run_history"] = [run]
                app.run(timeout=20)
                metric_radio = next(item for item in app.radio if item.key == "metric_combined-metrics")
                metric_radio.set_value("quantity").run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertIn("411,284 件", [item.value for item in app.metric])
        self.assertEqual(len(app.download_button), 2)
        self.assertEqual(len(app.image), 1)

    def test_clarification_state_renders_without_result_or_downloads(self):
        plan = AnalysisPlan(
            period1_start=None,
            period1_end=None,
            period2_start=None,
            period2_end=None,
            dimensions=["product"],
            direction="growth",
            clarification_question="请补充要比较的两个日期区间。",
        )
        run = {
            "id": "clarify-test",
            "question": "哪些商品卖得更好了？",
            "source_name": "retail_demo.csv",
            "status": "clarification",
            "trace": [{"node": "plan", "label": "规划智能体", "detail": "日期不完整，等待用户补充。"}],
            "state": {"plan": plan, "clarification_question": plan.clarification_question},
            "error": None,
        }

        app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
        app.run(timeout=20)
        app.session_state["run_history"] = [run]
        app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertIn(plan.clarification_question, [item.value for item in app.info])
        self.assertEqual(len(app.download_button), 0)
        page_markdown = "\n".join(item.value for item in app.markdown)
        self.assertNotIn(plan.clarification_question, page_markdown)
        self.assertEqual(page_markdown.count("需补充日期"), 1)

    def test_approved_result_state_renders_metrics_board_and_downloads(self):
        csv_path = PROJECT_ROOT / "data" / "retail_demo.csv"
        plan = AnalysisPlan(
            period1_start=date(2011, 9, 12),
            period1_end=date(2011, 9, 18),
            period2_start=date(2011, 9, 19),
            period2_end=date(2011, 9, 25),
            dimensions=["country", "product"],
            direction="both",
            top_n=3,
        )
        result = analyze_periods(
            csv_path,
            plan.period1_start,
            plan.period1_end,
            plan.period2_start,
            plan.period2_end,
            dimensions=plan.dimensions,
            direction=plan.direction,
            top_n=plan.top_n,
        )
        draft = ReportDraft(
            title="国家销售金额变化",
            conclusions=[
                {"text": "总体金额有所增长。", "evidence_ids": ["total.change_pct"]},
                {"text": "United Kingdom 为国家增长榜首。", "evidence_ids": ["country.United_Kingdom.delta"]},
                {"text": "Sweden 在国家金额榜中下降。", "evidence_ids": ["country.Sweden.delta"]},
                {"text": "商品 22688 金额下降。", "evidence_ids": ["product.22688.delta"]},
            ],
        )
        evidence = build_evidence(result, plan)
        state = {
            "plan": plan,
            "result": result,
            "report_draft": draft,
            "final_report": draft,
            "evidence": evidence,
            "review_result": ReviewResult(decision="approve", issues=[]),
            "review_attempts": 1,
            "clarification_question": None,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path, image_path = export_approved_report(state, temp_dir)
            run = {
                "id": "approved-test",
                "question": "验收 A",
                "source_name": "retail_demo.csv",
                "status": "approved",
                "trace": [],
                "state": state,
                "report_path": str(report_path),
                "image_path": str(image_path),
            }
            app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
            app.run(timeout=20)
            app.session_state["run_history"] = [run]
            app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        metric_values = [item.value for item in app.metric]
        self.assertIn("£230,137.26", metric_values)
        self.assertIn("£338,771.79", metric_values)
        self.assertIn("+47.20%", metric_values)
        self.assertEqual(len(app.download_button), 2)
        self.assertEqual(len([item for item in app.radio if item.label == "榜单维度"]), 1)
        self.assertTrue(any("£230,137.26" in item.value for item in app.markdown))
        self.assertTrue(any(item.label == "查看本次分析问题" for item in app.expander))
        page_markdown = "\n".join(item.value for item in app.markdown)
        self.assertEqual(page_markdown.count("审核通过"), 1)

    def test_board_evidence_and_download_interactions_do_not_call_workflow_again(self):
        csv_path = PROJECT_ROOT / "data" / "retail_demo.csv"
        plan = AnalysisPlan(
            period1_start=date(2011, 9, 12), period1_end=date(2011, 9, 18),
            period2_start=date(2011, 9, 19), period2_end=date(2011, 9, 25),
            dimensions=["country", "product"], direction="both", top_n=3,
        )
        result = analyze_periods(
            csv_path, plan.period1_start, plan.period1_end, plan.period2_start, plan.period2_end,
            dimensions=plan.dimensions, direction=plan.direction, top_n=plan.top_n,
        )
        draft = ReportDraft(
            title="本地演示",
            conclusions=[
                {"text": "总体金额变化。", "evidence_ids": ["total.change_pct"]},
                {"text": "国家增长结论。", "evidence_ids": ["country.United_Kingdom.delta"]},
            ],
        )
        state = {
            "plan": plan, "result": result, "report_draft": draft, "final_report": draft,
            "evidence": build_evidence(result, plan),
            "review_result": ReviewResult(decision="approve", issues=[]),
            "review_attempts": 1, "clarification_question": None,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path, image_path = export_approved_report(state, temp_dir)
            initial_draft = ReportDraft(title="初稿", conclusions=[{
                "text": "英国金额下降是因为缺货。",
                "evidence_ids": ["country.United_Kingdom.delta"],
            }])
            revised_draft = ReportDraft(title="返修稿", conclusions=[{
                "text": "英国金额下降，现有数据无法确认原因。",
                "evidence_ids": ["country.United_Kingdom.delta"],
            }])
            first_review = ReviewResult(decision="return", issues=[ReviewIssue(
                conclusion_index=0,
                evidence_ids=["country.United_Kingdom.delta"],
                problem="金额差额不能证明缺货",
                requested_change="删除缺货因果解释",
            )])
            second_review = ReviewResult(decision="approve", issues=[])
            trace = [completed_node_event("review", {
                "review_result": first_review,
            }, [], draft_history=[initial_draft], review_history=[first_review])]
            trace.append(completed_node_event("review", {
                "review_result": second_review,
            }, trace, draft_history=[initial_draft, revised_draft], review_history=[first_review, second_review]))
            run = {
                "id": "interaction-test", "question": "演示问题", "source_name": "retail_demo.csv",
                "status": "approved", "trace": trace, "state": state,
                "report_path": str(report_path), "image_path": str(image_path),
            }
            with patch("langgraph_workflow.run_workflow", side_effect=AssertionError("workflow must not rerun")):
                app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
                app.run(timeout=20)
                app.session_state["run_history"] = [run]
                app.run(timeout=20)

                audit_expander = next(item for item in app.expander if item.label == "查看审核意见")
                self.assertFalse(audit_expander.proto.expanded)
                comparison_expander = next(item for item in app.expander if item.label == "查看审核意见与返修对照")
                self.assertFalse(comparison_expander.proto.expanded)
                app.run(timeout=20)
                dimension_radio = next(item for item in app.radio if item.label == "榜单维度")
                dimension_radio.set_value("product").run(timeout=20)
                self.assertTrue(any(item.label == "展开本次计算证据" for item in app.expander))
                app.download_button[0].click().run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.download_button), 2)

    def test_new_clarification_run_hides_previous_report_and_downloads(self):
        clarify_plan = AnalysisPlan(
            period1_start=None, period1_end=None, period2_start=None, period2_end=None,
            dimensions=["product"], direction="growth",
            clarification_question="请补充要比较的两个日期区间。",
        )
        old_run = {"id": "old", "status": "approved", "question": "旧问题"}
        current_run = {
            "id": "new", "status": "clarification", "question": "哪些商品卖得更好了？",
            "source_name": "retail_demo.csv", "trace": [], "error": None,
            "state": {"plan": clarify_plan, "clarification_question": clarify_plan.clarification_question},
        }

        app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
        app.run(timeout=20)
        app.session_state["run_history"] = [old_run, current_run]
        app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.metric), 0)
        self.assertEqual(len(app.download_button), 0)
        self.assertIn(clarify_plan.clarification_question, [item.value for item in app.info])

    def test_review_failure_has_one_status_label_and_keeps_review_reason_in_trace(self):
        run = {
            "id": "failed-review",
            "question": "验收",
            "source_name": "retail_demo.csv",
            "status": "failed",
            "trace": [
                {
                    "node": "review",
                    "label": "审核智能体 · 第2次复核",
                    "detail": "退回：排名结论与证据不一致",
                    "status": "completed",
                }
            ],
            "state": {
                "review_result": ReviewResult(
                    decision="return",
                    issues=[
                        ReviewIssue(
                            conclusion_index=0,
                            evidence_ids=["total.delta"],
                            problem="排名结论与证据不一致",
                            requested_change="按金额榜单修正结论",
                        )
                    ],
                )
            },
            "error": "审核复核未通过，已达到一次返修上限。",
        }
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
        app.run(timeout=20)
        app.session_state["run_history"] = [run]
        app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        page_markdown = "\n".join(item.value for item in app.markdown)
        self.assertEqual(page_markdown.count("审核未通过"), 1)
        self.assertIn("排名结论与证据不一致", page_markdown)
        self.assertEqual(len(app.download_button), 0)

    def test_weekly_summary_success_renders_grouped_amounts_and_downloads(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["week"], ranking_order="desc", top_n=5,
        )
        result = analyze_plan(PROJECT_ROOT / "data" / "retail_demo.csv", plan)
        report = ReportDraft(
            title="每周销售汇总",
            conclusions=[{
                "text": "2011 年 9 月 19 日至 25 日所在周金额最高。",
                "evidence_ids": ["week.2011-09-19_2011-09-25.amount"],
            }],
        )
        state = {
            "plan": plan,
            "result": result,
            "report_draft": report,
            "final_report": report,
            "evidence": build_evidence(result, plan),
            "review_result": ReviewResult(decision="approve", issues=[]),
            "review_attempts": 1,
            "clarification_question": None,
        }
        trace = [completed_node_event("review", {
            "review_result": ReviewResult(decision="return", issues=[ReviewIssue(
                conclusion_index=0,
                evidence_ids=["week.2011-09-19_2011-09-25.amount"],
                problem="请补充完整周排名证据。",
                requested_change="纳入其余周金额。",
            )]),
        }, [])]
        trace.append(completed_node_event("review", {
            "review_result": ReviewResult(decision="approve", issues=[]),
        }, trace))
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path, image_path = export_approved_report(state, temp_dir)
            run = {
                "id": "weekly-summary", "question": "哪周卖得最好？",
                "source_name": "retail_demo.csv", "status": "approved", "trace": trace,
                "state": state, "report_path": str(report_path), "image_path": str(image_path),
            }
            app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
            app.run(timeout=20)
            app.session_state["run_history"] = [run]
            app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertIn("£338,771.79", [item.value for item in app.metric])
        self.assertEqual(len(app.download_button), 2)
        self.assertFalse(any(item.label == "榜单维度" for item in app.radio))
        self.assertTrue(any("2011-09-19 至 2011-09-25" in item.value.to_string() for item in app.dataframe))
        self.assertTrue(any("每周金额前 5" in item.value for item in app.markdown))
        self.assertTrue(any("返回 3 项" in item.value for item in app.markdown))
        self.assertEqual(len(app.image), 1)
        tree_text = repr(app._tree.root)
        self.assertLess(tree_text.index("Dataframe("), tree_text.index("Image("))
        self.assertTrue(any(item.label == "查看审核意见" for item in app.expander))
        self.assertFalse(next(item for item in app.expander if item.label == "查看审核意见").proto.expanded)

    def test_germany_top_five_first_view_names_leader_and_shows_table_beside_trace(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], ranking_order="desc", top_n=5,
            country_filters=["德国"],
        )
        result = analyze_plan(PROJECT_ROOT / "data" / "retail_demo.csv", plan)
        report = ReportDraft(
            title="德国商品排名",
            conclusions=[{
                "text": "22580 是金额榜首。",
                "evidence_ids": ["product.22580.amount"],
            }],
        )
        state = {
            "plan": plan, "result": result, "report_draft": report,
            "final_report": report, "evidence": build_evidence(result, plan),
            "review_result": ReviewResult(decision="approve", issues=[]),
            "review_attempts": 1, "clarification_question": None,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path, image_path = export_approved_report(state, temp_dir)
            run = {
                "id": "germany-top-five", "question": "德国卖得最好的 5 个商品是什么？",
                "source_name": "retail_demo.csv", "status": "approved", "trace": [],
                "state": state, "report_path": str(report_path), "image_path": str(image_path),
            }
            app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
            app.run(timeout=20)
            app.session_state["run_history"] = [run]
            app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("德国商品金额前 5" in item.value for item in app.markdown))
        self.assertIn("榜首商品 22580", [item.label for item in app.metric])
        self.assertIn("£237.60", [item.value for item in app.metric])
        self.assertIn("£11,325.12", [item.value for item in app.metric])
        self.assertTrue(any("22580" in str(frame.value.iloc[0].to_dict()) for frame in app.dataframe))
        self.assertEqual(len(app.download_button), 2)

    def test_germany_quantity_top_five_page_uses_shared_competition_ranks_and_tie_note(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], metrics=["amount", "quantity"],
            rank_by="quantity", ranking_order="desc", top_n=5,
            country_filters=["Germany"],
        )
        result = analyze_plan(PROJECT_ROOT / "data" / "retail_demo.csv", plan)
        report = ReportDraft(title="Germany quantity ranking", conclusions=[{
            "text": "The rows show the highest quantity products.",
            "evidence_ids": ["ranking.product.overall"],
        }])
        state = {
            "plan": plan, "result": result, "report_draft": report,
            "final_report": report, "evidence": result["evidence"],
            "review_result": ReviewResult(decision="approve", issues=[]),
            "review_attempts": 1, "clarification_question": None,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path, image_path = export_approved_report(state, temp_dir)
            run = {
                "id": "germany-quantity-ties", "question": "德国销量最高的 5 个商品，同时列出每个商品的正向交易金额",
                "source_name": "retail_demo.csv", "status": "approved", "trace": [],
                "state": state, "report_path": str(report_path), "image_path": str(image_path),
            }
            app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
            app.run(timeout=20)
            app.session_state["run_history"] = [run]
            app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        rank_table = app.dataframe[0].value
        self.assertEqual(rank_table["排名"].tolist(), [1, 1, 3, 4, 5, 5, 5])
        page_text = "\n".join(item.value for item in app.caption)
        self.assertIn("请求前 5 名，第 5 名并列，实际展示 7 项。", page_text)

    def test_month_product_table_passes_all_four_rows_and_uses_content_height(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["month", "product"], metrics=["amount", "quantity"],
            rank_by="amount", ranking_order="desc", top_n=1,
            country_filters=["United Kingdom", "Germany"],
            product_filters=["23243", "22580", "21495"],
        )
        result = analyze_plan(PROJECT_ROOT / "data" / "retail_demo.csv", plan)
        report = ReportDraft(title="Filtered monthly details", conclusions=[{
            "text": "Each month has its own amount leader.",
            "evidence_ids": ["ranking.month.2011-09.product", "ranking.month.2011-10.product"],
        }])
        state = {
            "plan": plan, "result": result, "report_draft": report,
            "final_report": report, "evidence": result["evidence"],
            "review_result": ReviewResult(decision="approve", issues=[]),
            "review_attempts": 1, "clarification_question": None,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path, image_path = export_approved_report(state, temp_dir)
            run = {
                "id": "monthly-four-rows", "question": "月度商品明细", "source_name": "retail_demo.csv",
                "status": "approved", "trace": [], "state": state,
                "report_path": str(report_path), "image_path": str(image_path),
            }
            app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
            app.run(timeout=20)
            app.session_state["run_history"] = [run]
            app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        details = app.dataframe[0]
        self.assertEqual(len(details.value), 4)
        month_product_rows = details.value.to_string()
        self.assertIn("2011-10", month_product_rows)
        self.assertIn("23243", month_product_rows)
        self.assertIn("£9.90", month_product_rows)
        self.assertIn("2 件", month_product_rows)
        self.assertTrue(any("共 4 条明细" in item.value for item in app.caption))

    def test_unsupported_plan_stops_after_planning_without_downloads(self):
        plan = AnalysisPlan(
            status="unsupported",
            analysis_type=None,
            time_scope=None,
            unsupported_reason="缺少促销字段，无法判断增长原因。",
        )
        run = {
            "id": "unsupported-test", "question": "这次增长是因为促销吗？",
            "source_name": "retail_demo.csv", "status": "unsupported", "trace": [],
            "state": {"plan": plan, "unsupported_reason": plan.unsupported_reason,
                      "clarification_question": plan.unsupported_reason},
            "error": plan.unsupported_reason,
        }
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
        app.run(timeout=20)
        app.session_state["run_history"] = [run]
        app.run(timeout=20)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.download_button), 0)
        self.assertTrue(any("缺少促销字段" in item.value for item in app.warning))


if __name__ == "__main__":
    unittest.main()
