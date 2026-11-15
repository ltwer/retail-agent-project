import sys
import tempfile
import unittest
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from planning_agent import (
    AnalysisPlan,
    CsvMetadata,
    ModelConfigurationError,
    PlannerRequestError,
    build_model_context,
    _parse_chat_plan,
    _resolve_redundant_rank_clarification,
    plan_question,
    validate_plan_dates,
)


class PlanningAgentTests(unittest.TestCase):
    def test_plan_schema_exposes_reference_intent_categories(self):
        schema = AnalysisPlan.model_json_schema()
        property_schema = schema["properties"]["reference_intent"]

        self.assertIn("reference_intent", schema["required"])
        self.assertIn("product_leader", str(property_schema))
        self.assertIn("country_decline_leader", str(property_schema))
        plan = AnalysisPlan(
            status="executable", analysis_type="summary", time_scope="full_data",
            group_by=["product"], ranking_order="desc", reference_intent="product_leader",
        )
        self.assertEqual(plan.reference_intent, "product_leader")

    def test_comparison_sell_better_clarification_keeps_only_missing_date_question(self):
        plan = AnalysisPlan(
            status="clarification", analysis_type="comparison", time_scope="comparison",
            metrics=["amount"], rank_by="amount", dimensions=["product"], direction="growth",
            clarification_question="请补充日期，并确认按销售额还是销量排名。",
        )

        resolved = _resolve_redundant_rank_clarification("哪些商品卖得更好了？", plan)

        self.assertEqual(resolved.status, "clarification")
        self.assertEqual(resolved.clarification_question, "请指定要比较的两个日期区间。")

    def test_comparison_plan_ignores_redundant_summary_ranking_order(self):
        payload = {
            "status": "executable", "analysis_type": "comparison", "time_scope": "comparison",
            "period1_start": "2011-09-19", "period1_end": "2011-09-25",
            "period2_start": "2011-09-26", "period2_end": "2011-10-02",
            "dimensions": ["product"], "direction": "growth", "rank_by": "amount",
            "ranking_order": "desc", "top_n": 5, "metrics": ["amount"],
            "country_filters": [], "product_filters": [],
            "clarification_question": None, "unsupported_reason": None,
            "start_date": None, "end_date": None, "group_by": [],
        }

        plan = _parse_chat_plan(json.dumps(payload))

        self.assertEqual(plan.analysis_type, "comparison")
        self.assertEqual(plan.rank_by, "amount")
        self.assertIsNone(plan.ranking_order)

    def test_explicit_amount_leader_recovers_only_redundant_rank_basis_clarification(self):
        plan = AnalysisPlan(
            status="clarification", analysis_type="summary", time_scope="full_data",
            group_by=["month", "product"], metrics=["amount", "quantity"],
            rank_by="amount", ranking_order="desc", top_n=1,
            country_filters=["United Kingdom", "Germany"],
            product_filters=["23243", "22580", "21495"],
            clarification_question="请确认排名依据：按销售额还是按销量？",
        )
        question = "筛选英国、德国及商品 23243、22580、21495，按月 × 商品汇总金额和件数，并分别展示每月金额榜首"

        resolved = _resolve_redundant_rank_clarification(question, plan)

        self.assertEqual(resolved.status, "executable")
        self.assertIsNone(resolved.clarification_question)
        self.assertEqual(resolved.rank_by, "amount")
        self.assertEqual(resolved.metrics, ["amount", "quantity"])

    def test_missing_date_clarification_is_not_recovered_as_executable(self):
        plan = AnalysisPlan(
            status="clarification", analysis_type="comparison", time_scope="comparison",
            dimensions=["product"], direction="growth",
            clarification_question="请补充前后两期的日期区间。",
        )

        resolved = _resolve_redundant_rank_clarification("哪些商品卖得更好了？", plan)

        self.assertEqual(resolved.status, "clarification")
        self.assertEqual(resolved.clarification_question, plan.clarification_question)

    def test_chat_completions_parses_valid_plan_using_explicit_base_url(self):
        plan_json = (
            '{"period1_start":"2011-09-19","period1_end":"2011-09-25",'
            '"period2_start":"2011-09-26","period2_end":"2011-10-02",'
            '"dimensions":["country","product"],"direction":"decline",'
            '"top_n":5,"clarification_question":null}'
        )
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=plan_json))]
        )
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return response

        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create)
            )
        )
        env = {
            "OPENAI_API_MODE": "chat_completions",
            "OPENAI_API_KEY": "unit-test-placeholder",
            "OPENAI_MODEL": "DeepSeek-V4.1-Flash",
            "OPENAI_BASE_URL": "https://agi.tongji.edu.cn/v1",
        }
        project_root = Path(__file__).resolve().parents[1]

        with patch("openai.OpenAI", return_value=client) as openai_client:
            plan = plan_question("验收 A", project_root / "data" / "retail_demo.csv", environ=env)

        self.assertEqual(plan.period1_start, date(2011, 9, 19))
        self.assertEqual(plan.period2_end, date(2011, 10, 2))
        self.assertEqual(plan.dimensions, ["country", "product"])
        self.assertEqual(plan.direction, "decline")
        self.assertEqual(plan.top_n, 5)
        self.assertEqual(openai_client.call_args.kwargs["base_url"], env["OPENAI_BASE_URL"])
        self.assertEqual(calls[0]["response_format"], {"type": "json_object"})
        system_message = calls[0]["messages"][0]["content"]
        request_schema = json.loads(
            system_message.split("JSON Schema (source: AnalysisPlan.model_json_schema):\n", 1)[1]
        )
        self.assertEqual(request_schema, AnalysisPlan.model_json_schema())
        self.assertTrue(
            {"period1_start", "period1_end", "period2_start", "period2_end"}.issubset(
                set(request_schema["required"])
            )
        )
        for field in ("period1_start", "period1_end", "period2_start", "period2_end"):
            self.assertIn(field, system_message)
        self.assertIn("YYYY-MM-DD", system_message)
        self.assertIn("null", system_message)

    def test_chat_completions_parses_clarification_response_without_guessing_dates(self):
        plan_json = (
            '{"period1_start":null,"period1_end":null,"period2_start":null,'
            '"period2_end":null,"dimensions":["product"],"direction":"growth",'
            '"top_n":5,"clarification_question":"请补充前期和后期各自的开始日期与结束日期。"}'
        )
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=plan_json))]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: response)
            )
        )
        env = {
            "OPENAI_API_MODE": "chat_completions",
            "OPENAI_API_KEY": "unit-test-placeholder",
            "OPENAI_MODEL": "DeepSeek-V4.1-Flash",
            "OPENAI_BASE_URL": "https://agi.tongji.edu.cn/v1",
        }

        with patch("openai.OpenAI", return_value=client):
            plan = plan_question(
                "哪些商品卖得更好了？",
                Path(__file__).resolve().parents[1] / "data" / "retail_demo.csv",
                environ=env,
            )

        self.assertIsNone(plan.period1_start)
        self.assertIsNone(plan.period1_end)
        self.assertIsNone(plan.period2_start)
        self.assertIsNone(plan.period2_end)
        self.assertIn("前期和后期", plan.clarification_question)

    def test_date_free_comparison_clarification_ignores_unused_summary_fields(self):
        from planning_agent import _parse_chat_plan

        payload = {
            "status": "clarification", "analysis_type": "comparison", "time_scope": "comparison",
            "start_date": None, "end_date": None, "group_by": ["product"],
            "metrics": [], "rank_by": "amount", "ranking_order": "desc",
            "period1_start": None, "period1_end": None,
            "period2_start": None, "period2_end": None,
            "dimensions": ["product"], "direction": "growth", "top_n": 5,
            "country_filters": [], "product_filters": [],
            "clarification_question": "请提供要比较的两个日期区间。",
            "unsupported_reason": None,
        }

        plan = _parse_chat_plan(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(plan.status, "clarification")
        self.assertEqual(plan.metrics, ["amount"])
        self.assertEqual(plan.group_by, [])
        self.assertIsNone(plan.ranking_order)
        self.assertTrue(all(getattr(plan, field) is None for field in (
            "period1_start", "period1_end", "period2_start", "period2_end",
        )))

    def test_chat_completions_falls_back_once_only_when_json_mode_is_unsupported(self):
        from openai import BadRequestError

        plan_json = (
            '{"period1_start":null,"period1_end":null,"period2_start":null,'
            '"period2_end":null,"dimensions":["product"],"direction":"growth",'
            '"top_n":5,"clarification_question":"请补充前后两期的完整日期区间。"}'
        )
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            if "response_format" in kwargs:
                raise BadRequestError(
                    "response_format json_object is not supported",
                    response=SimpleNamespace(status_code=400, headers={}, request=SimpleNamespace()),
                    body={"error": {"message": "response_format json_object is not supported"}},
                )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=plan_json))]
            )

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        env = {
            "OPENAI_API_MODE": "chat_completions",
            "OPENAI_API_KEY": "unit-test-placeholder",
            "OPENAI_MODEL": "DeepSeek-V4.1-Flash",
            "OPENAI_BASE_URL": "https://agi.tongji.edu.cn/v1",
        }

        with patch("openai.OpenAI", return_value=client):
            plan = plan_question(
                "哪些商品卖得更好了？",
                Path(__file__).resolve().parents[1] / "data" / "retail_demo.csv",
                environ=env,
            )

        self.assertEqual(len(calls), 2)
        self.assertIn("response_format", calls[0])
        self.assertNotIn("response_format", calls[1])
        self.assertIs(calls[0]["messages"], calls[1]["messages"])
        self.assertEqual(
            calls[0]["messages"][0]["content"],
            calls[1]["messages"][0]["content"],
        )
        fallback_system = calls[1]["messages"][0]["content"]
        for field in ("period1_start", "period1_end", "period2_start", "period2_end"):
            self.assertIn(field, fallback_system)
        self.assertIsNotNone(plan.clarification_question)

    def test_chat_completions_does_not_fallback_for_other_bad_requests(self):
        from openai import BadRequestError

        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            raise BadRequestError(
                "invalid model parameter",
                response=SimpleNamespace(status_code=400, headers={}, request=SimpleNamespace()),
                body={"error": {"message": "invalid model parameter"}},
            )

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        env = {
            "OPENAI_API_MODE": "chat_completions",
            "OPENAI_API_KEY": "unit-test-placeholder",
            "OPENAI_MODEL": "DeepSeek-V4.1-Flash",
            "OPENAI_BASE_URL": "https://agi.tongji.edu.cn/v1",
        }

        with patch("openai.OpenAI", return_value=client):
            with self.assertRaisesRegex(PlannerRequestError, "BadRequestError"):
                plan_question(
                    "问题",
                    Path(__file__).resolve().parents[1] / "data" / "retail_demo.csv",
                    environ=env,
                )

        self.assertEqual(len(calls), 1)

    def test_chat_completions_requires_explicit_base_url_before_client_creation(self):
        env = {
            "OPENAI_API_MODE": "chat_completions",
            "OPENAI_API_KEY": "unit-test-placeholder",
            "OPENAI_MODEL": "DeepSeek-V4.1-Flash",
        }
        with patch("openai.OpenAI") as openai_client:
            with self.assertRaisesRegex(ModelConfigurationError, "OPENAI_BASE_URL"):
                plan_question(
                    "问题",
                    Path(__file__).resolve().parents[1] / "data" / "retail_demo.csv",
                    environ=env,
                )
        openai_client.assert_not_called()

    def test_chat_completions_reports_empty_invalid_json_and_invalid_plan(self):
        env = {
            "OPENAI_API_MODE": "chat_completions",
            "OPENAI_API_KEY": "unit-test-placeholder",
            "OPENAI_MODEL": "DeepSeek-V4.1-Flash",
            "OPENAI_BASE_URL": "https://agi.tongji.edu.cn/v1",
        }
        csv_path = Path(__file__).resolve().parents[1] / "data" / "retail_demo.csv"
        for content, error in (
            (None, "空响应"),
            ("不是 JSON", "JSON 无效"),
            (
                '{"pre_period_start":"2011-09-19",'
                '"post_period_end":"2011-10-02"}',
                "缺失字段.*period1_start.*额外字段.*post_period_end.*pre_period_start",
            ),
            (
                '{"period1_start":"not-a-date","period1_end":null,"period2_start":null,'
                '"period2_end":null,"dimensions":[],"direction":null}',
                "格式/取值错误.*period1_start",
            ),
        ):
            client = SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=lambda **kwargs: SimpleNamespace(
                            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
                        )
                    )
                )
            )
            with self.subTest(content=content), patch("openai.OpenAI", return_value=client):
                with self.assertRaisesRegex(PlannerRequestError, error):
                    plan_question("问题", csv_path, environ=env)

    def test_planner_sends_question_and_metadata_only_then_validates_model_plan(self):
        class FakeResponses:
            def parse(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(output_parsed=expected_plan)

        expected_plan = AnalysisPlan(
            period1_start=date(2011, 9, 19),
            period1_end=date(2011, 9, 25),
            period2_start=date(2011, 9, 26),
            period2_end=date(2011, 10, 2),
            dimensions=["country", "product"],
            direction="decline",
        )
        fake = SimpleNamespace(responses=FakeResponses())
        project_root = Path(__file__).resolve().parents[1]

        result = plan_question(
            "验收 A 问题",
            project_root / "data" / "retail_demo.csv",
            client=fake,
            environ={"OPENAI_API_KEY": "unit-test-placeholder", "OPENAI_MODEL": "offline-test-model"},
        )

        self.assertEqual(result, expected_plan)
        self.assertEqual(fake.responses.kwargs["model"], "offline-test-model")
        self.assertIn("验收 A 问题", fake.responses.kwargs["input"])
        self.assertIn("2011-09-12", fake.responses.kwargs["input"])
        self.assertNotIn(str(project_root / "data" / "retail_demo.csv"), fake.responses.kwargs["input"])
        self.assertNotIn("WHITE HANGING HEART T-LIGHT HOLDER", fake.responses.kwargs["input"])

    def test_acceptance_a_plan_extracts_dates_dimensions_direction_and_default_count(self):
        plan = AnalysisPlan.model_validate(
            {
                "period1_start": "2011-09-19",
                "period1_end": "2011-09-25",
                "period2_start": "2011-09-26",
                "period2_end": "2011-10-02",
                "dimensions": ["country", "product"],
                "direction": "decline",
                "clarification_question": None,
            }
        )

        self.assertEqual(plan.top_n, 5)
        validate_plan_dates(
            plan,
            CsvMetadata(date(2011, 9, 12), date(2011, 10, 2)),
        )

    def test_acceptance_b_requires_both_comparison_periods(self):
        plan = AnalysisPlan.model_validate(
            {
                "period1_start": None,
                "period1_end": None,
                "period2_start": None,
                "period2_end": None,
                "dimensions": ["product"],
                "direction": "growth",
                "top_n": 5,
                "clarification_question": "请补充要比较的前期和后期日期区间（各自的开始日期与结束日期）。",
            }
        )

        self.assertIn("前期和后期", plan.clarification_question)

    def test_date_validation_rejects_reversed_and_out_of_range_periods(self):
        plan_data = {
            "period1_start": "2011-09-25",
            "period1_end": "2011-09-19",
            "period2_start": "2011-09-26",
            "period2_end": "2011-10-02",
            "dimensions": ["country"],
            "direction": "both",
            "top_n": 5,
            "clarification_question": None,
        }
        reversed_plan = AnalysisPlan.model_validate(plan_data)
        metadata = CsvMetadata(date(2011, 9, 12), date(2011, 10, 2))
        with self.assertRaisesRegex(ValueError, "前期开始日期晚于结束日期"):
            validate_plan_dates(reversed_plan, metadata)

        plan_data["period1_start"] = "2011-09-11"
        plan_data["period1_end"] = "2011-09-19"
        outside_plan = AnalysisPlan.model_validate(plan_data)
        with self.assertRaisesRegex(ValueError, "超出 CSV 可用日期范围"):
            validate_plan_dates(outside_plan, metadata)

    def test_out_of_range_model_plan_returns_user_facing_unsupported_plan(self):
        payload = {
            "status": "executable",
            "analysis_type": "summary",
            "time_scope": "date_range",
            "start_date": "2011-09-01",
            "end_date": "2011-09-11",
            "group_by": ["week"],
            "ranking_order": "desc",
            "period1_start": None,
            "period1_end": None,
            "period2_start": None,
            "period2_end": None,
            "dimensions": [],
            "direction": None,
            "top_n": 5,
            "clarification_question": None,
            "unsupported_reason": None,
        }
        env = {
            "OPENAI_API_MODE": "chat_completions",
            "OPENAI_API_KEY": "unit-test-placeholder",
            "OPENAI_MODEL": "test-model",
            "OPENAI_BASE_URL": "https://example.invalid/v1",
        }
        project_root = Path(__file__).resolve().parents[1]
        with patch("planning_agent._chat_completion_content", return_value=json.dumps(payload)):
            plan = plan_question(
                "分析数据可用日期之前的时间",
                project_root / "data" / "retail_demo.csv",
                client=object(),
                environ=env,
            )

        self.assertEqual(plan.status, "unsupported")
        self.assertIn("超出 CSV 可用日期范围", plan.unsupported_reason)

    def test_model_context_contains_metadata_but_no_local_csv_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "retail.csv"
            pd.DataFrame(
                {
                    "InvoiceDate": ["2011-09-12 08:24:00"],
                    "Description": ["LOCAL_ONLY_SENTINEL"],
                }
            ).to_csv(csv_path, index=False)
            metadata = CsvMetadata(date(2011, 9, 12), date(2011, 10, 2))

            context = build_model_context("验收问题", metadata)

        self.assertIn("2011-09-12", context)
        self.assertIn("2011-10-02", context)
        self.assertIn("验收问题", context)
        self.assertNotIn("LOCAL_ONLY_SENTINEL", context)
        self.assertNotIn(str(csv_path), context)

    def test_pydantic_rejects_invalid_date_strings(self):
        with self.assertRaises(ValidationError):
            AnalysisPlan.model_validate(
                {
                    "period1_start": "2011-09-99",
                    "period1_end": "2011-09-25",
                    "period2_start": "2011-09-26",
                    "period2_end": "2011-10-02",
                    "dimensions": [],
                    "direction": None,
                    "top_n": 5,
                    "clarification_question": None,
                }
            )


if __name__ == "__main__":
    unittest.main()
