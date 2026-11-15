import sys
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from langgraph_workflow import run_workflow
from planning_agent import AnalysisPlan
from retail_analysis import analyze_periods
from report_generator import ReportDraft, ReportEvidenceError, generate_report_draft, render_report_numbers
from report_exporter import export_approved_report, ReportExportError
from report_reviewer import ReviewIssue, ReviewResult, ReviewValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "data" / "retail_demo.csv"


class LangGraphWorkflowTests(unittest.TestCase):
    def test_planning_reporting_and_review_receive_turn_and_effective_request(self):
        plan = AnalysisPlan(
            period1_start=date(2011, 9, 19), period1_end=date(2011, 9, 25),
            period2_start=date(2011, 9, 26), period2_end=date(2011, 10, 2),
            dimensions=["product"], direction="growth", country_filters=["英国"],
        )
        latest = "那英国呢？"
        effective = "本次有效分析要求为英国范围的商品金额增长比较，保留原有分析指标和榜单条数。"
        planner_questions, draft_questions, review_questions = [], [], []
        draft = ReportDraft(title="报告", conclusions=[{
            "text": "商品金额增加。", "evidence_ids": ["summary.total"],
        }])

        state = run_workflow(
            CSV_PATH, latest,
            analysis_request=effective,
            latest_input=latest,
            planner=lambda question, _: (planner_questions.append(question) or plan),
            drafter=lambda question, *_args, **_kwargs: (draft_questions.append(question) or draft),
            reviewer=lambda question, *_args: (review_questions.append(question) or ReviewResult(decision="approve", issues=[])),
        )

        self.assertEqual(state["question"], latest)
        self.assertEqual(state["latest_input"], latest)
        self.assertEqual(state["analysis_request"], effective)
        self.assertEqual(planner_questions, [effective])
        self.assertIn(latest, draft_questions[0])
        self.assertIn(effective, draft_questions[0])
        self.assertIn(latest, review_questions[0])
        self.assertIn(effective, review_questions[0])

    def test_planner_can_refine_effective_request_for_report_and_review(self):
        from retail_analysis import analyze_plan

        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["week"], metrics=["quantity"], rank_by="quantity",
            country_filters=["Germany"], product_filters=["22580"],
        )
        planner_prompts, draft_prompts, review_prompts = [], [], []
        effective = "本次有效分析要求：德国商品22580按周汇总有效销售件数。"
        draft = ReportDraft(title="下钻", conclusions=[{
            "text": "按周展示所选商品件数。", "evidence_ids": ["summary.quantity"],
        }])

        def planner(_request, _path):
            planner_prompts.append(_request)
            planner.resolved_analysis_request = effective
            return plan

        state = run_workflow(
            CSV_PATH, "这个商品在德国每周卖了多少件？",
            planner=planner,
            calculator=lambda path, validated: analyze_plan(path, validated),
            drafter=lambda request, *_args, **_kwargs: (draft_prompts.append(request) or (draft, {})),
            reviewer=lambda request, *_args: (
                review_prompts.append(request) or ReviewResult(decision="approve", issues=[])
            ),
        )

        self.assertEqual(state["analysis_request"], effective)
        self.assertIn(effective, draft_prompts[0])
        self.assertIn(effective, review_prompts[0])
        self.assertEqual(state["draft_history"], [draft])
        self.assertEqual([item.decision for item in state["review_history"]], ["approve"])

    def test_clarification_ends_after_plan_without_calling_calculator(self):
        plan = AnalysisPlan(
            period1_start=None,
            period1_end=None,
            period2_start=None,
            period2_end=None,
            dimensions=["product"],
            direction="growth",
            clarification_question="请补充前期和后期各自的开始日期与结束日期。",
        )
        calculate_calls = []
        report_calls = []
        events = []

        def planner(question, csv_path):
            return plan

        def calculator(*args, **kwargs):
            calculate_calls.append((args, kwargs))
            raise AssertionError("clarification path must not calculate")

        def drafter(*args, **kwargs):
            report_calls.append((args, kwargs))
            raise AssertionError("clarification path must not draft a report")

        state = run_workflow(
            CSV_PATH,
            "哪些商品卖得更好了？",
            planner=planner,
            calculator=calculator,
            drafter=drafter,
            reviewer=lambda *_: ReviewResult(decision="approve", issues=[]),
            on_update=lambda node, update: events.append((node, update)),
        )

        self.assertEqual(state["steps"], ["plan"])
        self.assertEqual([node for node, _ in events], ["plan"])
        self.assertEqual(state["clarification_question"], plan.clarification_question)
        self.assertIsNone(state["result"])
        self.assertEqual(calculate_calls, [])
        self.assertEqual(report_calls, [])

    def test_workflow_emits_node_start_events_including_rework_attempts(self):
        plan = AnalysisPlan(
            period1_start=date(2011, 9, 19), period1_end=date(2011, 9, 25),
            period2_start=date(2011, 9, 26), period2_end=date(2011, 10, 2),
            dimensions=["product"], direction="decline",
        )
        report = ReportDraft(title="报告", conclusions=[{"text": "结论。", "evidence_ids": ["total.delta"]}])
        node_events = []
        review_count = 0

        def reviewer(*_):
            nonlocal review_count
            review_count += 1
            if review_count == 1:
                return ReviewResult(decision="return", issues=[ReviewIssue(
                    conclusion_index=0, evidence_ids=["total.delta"],
                    problem="需修正", requested_change="改写结论",
                )])
            return ReviewResult(decision="approve", issues=[])

        run_workflow(
            CSV_PATH, "测试",
            planner=lambda *_: plan,
            calculator=lambda *args, **kwargs: analyze_periods(*args, **kwargs),
            drafter=lambda *_args, **_kwargs: (report, None),
            reviewer=reviewer,
            on_event=node_events.append,
        )

        self.assertEqual(
            [(event["node"], event["attempt"]) for event in node_events],
            [("plan", 1), ("calculate", 1), ("draft_report", 1), ("review", 1),
             ("draft_report", 2), ("review", 2)],
        )
        self.assertTrue(all(event["type"] == "node_started" for event in node_events))

    def test_workflow_emits_failed_node_event_with_reason(self):
        events = []

        with self.assertRaisesRegex(RuntimeError, "planner failed"):
            run_workflow(
                CSV_PATH, "测试",
                planner=lambda *_: (_ for _ in ()).throw(RuntimeError("planner failed")),
                on_event=events.append,
            )

        self.assertEqual(events[0]["type"], "node_started")
        self.assertEqual(events[1]["type"], "node_failed")
        self.assertEqual(events[1]["node"], "plan")
        self.assertEqual(events[1]["error"], "planner failed")

    def test_planning_started_event_arrives_before_planner_returns(self):
        entered = threading.Event()
        release = threading.Event()
        events = []
        plan = AnalysisPlan(
            status="clarification", dimensions=["product"], direction="growth",
            clarification_question="请补充比较日期。",
        )

        def planner(*_):
            entered.set()
            release.wait(timeout=5)
            return plan

        worker = threading.Thread(target=lambda: run_workflow(
            CSV_PATH, "哪些商品卖得更好了？", planner=planner, on_event=events.append,
        ))
        worker.start()
        try:
            self.assertTrue(entered.wait(timeout=2))
            self.assertEqual(events[0], {"type": "node_started", "node": "plan", "attempt": 1})
            self.assertTrue(worker.is_alive())
        finally:
            release.set()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())

    def test_complete_plan_calls_existing_calculator_with_plan_parameters(self):
        plan = AnalysisPlan(
            period1_start=date(2011, 9, 19),
            period1_end=date(2011, 9, 25),
            period2_start=date(2011, 9, 26),
            period2_end=date(2011, 10, 2),
            dimensions=["country", "product"],
            direction="decline",
            top_n=5,
        )
        calculate_calls = []
        report_calls = []
        events = []

        def planner(question, csv_path):
            return plan

        def calculator(*args, **kwargs):
            calculate_calls.append((args, kwargs))
            return analyze_periods(*args, **kwargs)

        def drafter(question, plan, result):
            report_calls.append((question, plan, result))
            return ReportDraft(
                title="两期正向交易金额下降",
                conclusions=[
                    {
                        "text": "正向交易金额较前期下降。",
                        "evidence_ids": ["total.change_pct"],
                    },
                    {
                        "text": "United Kingdom 为国家金额降幅榜首。",
                        "evidence_ids": ["country.United_Kingdom.delta"],
                    },
                    {
                        "text": "商品 23243 为商品金额降幅榜首。",
                        "evidence_ids": ["product.23243.delta"],
                    },
                ],
            )

        state = run_workflow(
            CSV_PATH,
            "验收 A",
            planner=planner,
            calculator=calculator,
            drafter=drafter,
            reviewer=lambda *_: ReviewResult(decision="approve", issues=[]),
            on_update=lambda node, update: events.append((node, update)),
        )

        self.assertEqual(state["steps"], ["plan", "calculate", "draft_report", "review"])
        self.assertEqual([node for node, _ in events], ["plan", "calculate", "draft_report", "review"])
        self.assertIsNone(state["clarification_question"])
        self.assertEqual(len(calculate_calls), 1)
        args, kwargs = calculate_calls[0]
        self.assertEqual(args, (CSV_PATH, date(2011, 9, 19), date(2011, 9, 25), date(2011, 9, 26), date(2011, 10, 2)))
        self.assertEqual(kwargs, {"dimensions": ["country", "product"], "direction": "decline", "top_n": 5})
        self.assertEqual(round(state["result"]["period1_total"], 2), 338771.79)
        self.assertEqual(round(state["result"]["period2_total"], 2), 211800.22)
        self.assertEqual(round(state["result"]["change_rate"] * 100, 2), -37.48)
        number_summary = render_report_numbers(state["result"], plan)
        self.assertEqual(number_summary["period1_total"], "£338,771.79")
        self.assertEqual(number_summary["period2_total"], "£211,800.22")
        self.assertEqual(number_summary["change_rate"], "-37.48%")
        self.assertEqual(state["result"]["country_decreases"][0]["Country"], "United Kingdom")
        self.assertEqual(state["result"]["product_decreases"][0]["StockCode"], "23243")
        self.assertEqual(len(report_calls), 1)
        self.assertEqual(state["report_draft"].title, "两期正向交易金额下降")
        self.assertIsNotNone(state["final_report"])
        self.assertIn("total.change_pct", state["evidence"])
        self.assertIn("country.United_Kingdom.delta", state["evidence"])
        self.assertIn("product.23243.delta", state["evidence"])

    def test_report_draft_rejects_unknown_evidence_reference(self):
        from report_generator import validate_evidence_references

        draft = ReportDraft(
            title="无依据结论",
            conclusions=[
                {"text": "缺少引用。", "evidence_ids": ["product.unknown.delta"]}
            ],
        )
        with self.assertRaisesRegex(ReportEvidenceError, "product.unknown.delta"):
            validate_evidence_references(draft, {"total.change_pct": {"value": -37.48}})

    def test_report_generator_parses_structured_model_and_sends_only_summary_inputs(self):
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
        response_json = (
            '{"title":"销售金额变化初稿","explanations":['
            '{"text":"整体金额呈下降趋势。","evidence_ids":["total.change_pct"]},'
            '{"text":"英国变化明显。","evidence_ids":["country.United_Kingdom.delta"]}'
            ']}'
        )
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return type("Response", (), {"choices": [type("Choice", (), {"message": type("Message", (), {"content": response_json})()})()]})()

        client = type("Client", (), {"chat": type("Chat", (), {"completions": type("Completions", (), {"create": staticmethod(create)})()})()})()
        draft, evidence = generate_report_draft(
            "验收 A",
            plan,
            result,
            client=client,
            environ={
                "OPENAI_API_MODE": "chat_completions",
                "OPENAI_API_KEY": "unit-test-placeholder",
                "OPENAI_MODEL": "DeepSeek-V4.1-Flash",
                "OPENAI_BASE_URL": "https://agi.tongji.edu.cn/v1",
            },
        )

        self.assertEqual(draft.title, "国家、商品下降榜（按正向交易金额差额）")
        self.assertEqual(len(draft.conclusions), 4)
        self.assertIn("ranking.country.decline", draft.conclusions[0].evidence_ids)
        self.assertIn("ranking.product.decline", draft.conclusions[1].evidence_ids)
        self.assertIn("total.change_pct", evidence)
        self.assertIn("country.United_Kingdom.delta", evidence)
        self.assertIn("验收 A", captured["messages"][1]["content"])
        self.assertNotIn("rendered_numerical_summary", captured["messages"][1]["content"])
        self.assertNotIn(str(CSV_PATH), captured["messages"][1]["content"])

    def test_report_narrative_rejects_model_authored_currency_or_percentage(self):
        with self.assertRaisesRegex(ValueError, "金额和百分比由程序渲染"):
            ReportDraft(
                title="金额下降",
                conclusions=[
                    {
                        "text": "总额下降 37.48%，后期为 £211,800.22。",
                        "evidence_ids": ["total.change_pct"],
                    }
                ],
            )

    def test_program_builds_ranked_answer_and_reviewer_rejects_conflicting_model_explanation(self):
        from report_generator import build_ranking_conclusions
        from report_reviewer import review_report
        from retail_analysis import analyze_plan

        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], country_filters=["United Kingdom"],
            metrics=["amount"], rank_by="amount", ranking_order="desc", top_n=3,
        )
        result = analyze_plan(CSV_PATH, plan)
        evidence = result["evidence"]
        ranking_answers = build_ranking_conclusions(result, plan, evidence)
        first = ranking_answers[0]

        self.assertIn("23243", first.text)
        self.assertIn("85099B", first.text)
        self.assertIn("第 1 名", first.text)
        self.assertIn("第 2 名 85099B", first.text)
        self.assertNotIn("第 1 名 85099B", first.text)
        self.assertIn("ranking.product.overall", first.evidence_ids)
        self.assertNotIn("£", first.text)

        calculation_calls = []

        def calculator(*_args, **_kwargs):
            calculation_calls.append(1)
            return result

        def drafter(_question, validated_plan, calculated, *, review_feedback=None, evidence=None):
            ranked = build_ranking_conclusions(calculated, validated_plan, evidence)
            return ReportDraft(title="英国商品金额排名", conclusions=[*ranked, {
                "text": "85099B 是销售额最高的商品。",
                "evidence_ids": ["ranking.product.overall"],
            }])

        def reviewer(question, validated_plan, calculated, draft, evidence):
            return review_report(question, validated_plan, calculated, draft, evidence, environ={})

        state = run_workflow(
            CSV_PATH, "按正向交易金额最高排序，列出前 3 个商品。", planner=lambda *_: plan,
            calculator=calculator, drafter=drafter, reviewer=reviewer,
        )

        self.assertEqual(len(calculation_calls), 1)
        self.assertEqual(state["review_attempts"], 2)
        self.assertEqual(state["review_result"].decision, "return")
        self.assertIn("非榜首实体 85099B", state["review_result"].issues[0].problem)
        self.assertIsNone(state["final_report"])

    def test_reviewer_requires_evidence_for_return_and_rejects_unknown_ids(self):
        valid_evidence = {"country.United_Kingdom.delta": {"value": -10.0}}
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            ReviewResult(decision="return", issues=[])
        with self.assertRaisesRegex(ReviewValidationError, "不存在"):
            ReviewResult(
                decision="return",
                issues=[
                    {
                        "conclusion_index": 0,
                        "evidence_ids": ["country.unknown.delta"],
                        "problem": "证据不存在",
                        "requested_change": "改写结论",
                    }
                ],
            ).validate_evidence(valid_evidence)

    def test_review_return_reuses_calculation_and_revises_only_once(self):
        plan = AnalysisPlan(
            period1_start=date(2011, 9, 19),
            period1_end=date(2011, 9, 25),
            period2_start=date(2011, 9, 26),
            period2_end=date(2011, 10, 2),
            dimensions=["country", "product"],
            direction="decline",
            top_n=5,
        )
        calc_calls = []
        draft_calls = []
        review_calls = []
        report = ReportDraft(
            title="两期销售变化",
            conclusions=[
                {
                    "text": "英国降幅榜首，且该差额由缺货造成。",
                    "evidence_ids": ["country.United_Kingdom.delta"],
                }
            ],
        )

        def planner(question, csv_path):
            return plan

        def calculator(*args, **kwargs):
            calc_calls.append((args, kwargs))
            return analyze_periods(*args, **kwargs)

        def drafter(question, validated_plan, result, *, review_feedback=None, evidence=None):
            draft_calls.append((result, review_feedback, evidence))
            if review_feedback:
                return ReportDraft(
                    title="两期销售变化",
                    conclusions=[
                        {
                            "text": "英国降幅榜首，现有数据不能判断是否缺货。",
                            "evidence_ids": ["country.United_Kingdom.delta"],
                        }
                    ],
                ), evidence
            return report, None

        def reviewer(question, validated_plan, result, draft, evidence):
            review_calls.append((result, evidence))
            if len(review_calls) == 1:
                return ReviewResult(
                    decision="return",
                    issues=[
                        ReviewIssue(
                            conclusion_index=0,
                            evidence_ids=["country.United_Kingdom.delta"],
                            problem="金额差额不能证明缺货",
                            requested_change="删除缺货因果，说明数据不足以确认原因",
                        )
                    ],
                )
            return ReviewResult(decision="approve", issues=[])

        state = run_workflow(
            CSV_PATH,
            "验收 A",
            planner=planner,
            calculator=calculator,
            drafter=drafter,
            reviewer=reviewer,
        )

        self.assertEqual(state["steps"], ["plan", "calculate", "draft_report", "review", "draft_report", "review"])
        self.assertEqual(len(calc_calls), 1)
        self.assertEqual(len(draft_calls), 2)
        self.assertEqual(len(review_calls), 2)
        self.assertEqual(len(state["draft_history"]), 2)
        self.assertEqual(len(state["review_history"]), 2)
        self.assertEqual(state["draft_history"][0].conclusions[0].text, "英国降幅榜首，且该差额由缺货造成。")
        self.assertEqual(state["draft_history"][1].conclusions[0].text, "英国降幅榜首，现有数据不能判断是否缺货。")
        self.assertEqual(state["review_history"][0].decision, "return")
        self.assertEqual(state["review_history"][1].decision, "approve")
        self.assertIs(draft_calls[0][0], draft_calls[1][0])
        self.assertIs(draft_calls[1][2], state["evidence"])
        self.assertEqual(state["review_result"].decision, "approve")
        self.assertEqual(state["final_report"].conclusions[0].text, "英国降幅榜首，现有数据不能判断是否缺货。")
        self.assertEqual(state["result"]["period1_total"], calc_calls[0][1].get("unused", state["result"]["period1_total"]))

    def test_second_return_ends_without_marking_draft_as_final(self):
        plan = AnalysisPlan(
            period1_start=date(2011, 9, 19), period1_end=date(2011, 9, 25),
            period2_start=date(2011, 9, 26), period2_end=date(2011, 10, 2),
            dimensions=["product"], direction="decline",
        )
        report = ReportDraft(title="待审核", conclusions=[{"text": "商品金额发生变化。", "evidence_ids": ["total.delta"]}])
        reviews = []

        def reviewer(*args):
            reviews.append(1)
            return ReviewResult(
                decision="return",
                issues=[ReviewIssue(conclusion_index=0, evidence_ids=["total.delta"], problem="仍需修改", requested_change="补充解释")],
            )

        state = run_workflow(
            CSV_PATH,
            "test",
            planner=lambda *_: plan,
            calculator=analyze_periods,
            drafter=lambda *_args, **_kwargs: (report, None),
            reviewer=reviewer,
        )

        self.assertEqual(len(reviews), 2)
        self.assertEqual(state["steps"], ["plan", "calculate", "draft_report", "review", "draft_report", "review"])
        self.assertIsNone(state["final_report"])
        self.assertEqual(state["review_result"].decision, "return")
        self.assertEqual(len(state["draft_history"]), 2)
        self.assertEqual([item.decision for item in state["review_history"]], ["return", "return"])

    def test_wrong_metric_result_is_blocked_without_report_retry_or_publication(self):
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], metrics=["amount"], rank_by="amount",
            ranking_order="desc", top_n=5,
        )
        report = ReportDraft(title="錯指標", conclusions=[{
            "text": "商品排名依據已計算資料。", "evidence_ids": ["summary.total"],
        }])
        draft_calls = []
        review_calls = []

        def calculator(*_args, **_kwargs):
            return {
                "analysis_type": "summary", "metrics": ["amount"], "rank_by": "amount",
                "summary": {"total": 200.0}, "metric_totals": {"amount": 200.0},
                "groups": [{"key": "A", "label": "A", "amount": 200.0, "values": {"amount": 200.0}}],
                "rankings": [], "evidence": {"summary.total": {"value": 200.0, "unit": "GBP"}},
                "filter_scope": {"countries": ["Germany"], "products": []},
                "date_range": {"start": date(2011, 9, 12), "end": date(2011, 10, 2)},
                "group_by": "product", "group_by_fields": ["product"],
            }

        def drafter(*args, **kwargs):
            draft_calls.append((args, kwargs))
            return report, calculator_result

        calculator_result = calculator()

        state = run_workflow(
            CSV_PATH,
            "德国销量最多的前五个商品是什么？",
            planner=lambda *_: plan,
            calculator=calculator,
            drafter=drafter,
            reviewer=lambda *args: (review_calls.append(args), ReviewResult(decision="approve", issues=[]))[1],
        )

        self.assertEqual(state["plan"].status, "unsupported")
        self.assertIsNone(state["final_report"])
        self.assertEqual(draft_calls, [])
        self.assertEqual(review_calls, [])

    def test_summary_wrong_computed_metric_is_blocked_by_reviewer(self):
        from report_reviewer import review_report

        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], metrics=["quantity"], rank_by="quantity",
            ranking_order="desc", top_n=5,
        )
        result = {
            "analysis_type": "summary", "metrics": ["amount"], "rank_by": "amount",
            "evidence": {"summary.total": {"value": 200.0, "unit": "GBP"}},
        }
        report = ReportDraft(title="金额榜", conclusions=[{
            "text": "返回了商品排序。", "evidence_ids": ["summary.total"],
        }])
        review = review_report(
            "德国销量最多的前五个商品是什么？", plan, result, report,
            result["evidence"], client=object(), environ={},
        )

        self.assertEqual(review.decision, "block")
        self.assertIn("quantity", review.issues[0].problem)
        self.assertIn("amount", review.issues[0].problem)

    def test_wrong_ranking_citation_revises_once_and_reuses_calculation(self):
        from report_reviewer import review_report
        from retail_analysis import analyze_plan

        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], metrics=["amount"], rank_by="amount",
            ranking_order="desc", top_n=5,
        )
        calculation_calls = []
        draft_calls = []
        reviewer_calls = []

        def calculator(path, validated_plan):
            calculation_calls.append(1)
            return analyze_plan(path, validated_plan)

        def drafter(question, validated_plan, result, *, review_feedback=None, evidence=None):
            draft_calls.append(review_feedback)
            citation = "ranking.product.overall" if review_feedback else "summary.total"
            entity = result["groups"][0]["key"]
            return ReportDraft(title="商品金额排名", conclusions=[{
                "text": f"商品 {entity} 排名第一。", "evidence_ids": [citation],
            }]), result["evidence"]

        def reviewer(question, validated_plan, result, draft, evidence):
            reviewer_calls.append((result, evidence))
            if len(reviewer_calls) == 1:
                return review_report(question, validated_plan, result, draft, evidence, environ={})
            return ReviewResult(decision="approve", issues=[])

        state = run_workflow(
            CSV_PATH, "商品销售额排名前五", planner=lambda *_: plan,
            calculator=calculator, drafter=drafter, reviewer=reviewer,
        )

        self.assertEqual(state["steps"], ["plan", "calculate", "draft_report", "review", "draft_report", "review"])
        self.assertEqual(len(calculation_calls), 1)
        self.assertIs(reviewer_calls[0][0], reviewer_calls[1][0])
        self.assertIs(reviewer_calls[0][1], reviewer_calls[1][1])
        self.assertIsNone(draft_calls[0])
        self.assertTrue(draft_calls[1])
        self.assertEqual(state["review_result"].decision, "approve")
        self.assertIsNotNone(state["final_report"])

    def test_clarification_path_cannot_export_and_creates_no_outputs_directory(self):
        plan = AnalysisPlan(
            period1_start=None, period1_end=None, period2_start=None, period2_end=None,
            dimensions=["product"], direction="growth",
            clarification_question="请补充前期和后期日期区间。",
        )
        state = run_workflow(
            CSV_PATH,
            "哪些商品卖得更好了？",
            planner=lambda *_: plan,
            calculator=lambda *_args, **_kwargs: self.fail("calculate should not run"),
            drafter=lambda *_args, **_kwargs: self.fail("draft should not run"),
            reviewer=lambda *_args, **_kwargs: self.fail("review should not run"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = Path(temp_dir) / "outputs"
            with self.assertRaises(ReportExportError):
                export_approved_report(state, outputs)
            self.assertFalse(outputs.exists())


if __name__ == "__main__":
    unittest.main()
