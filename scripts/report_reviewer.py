import json
from pathlib import Path
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from model_api import (
    ModelRequestError,
    chat_completion_json,
    create_openai_client,
    load_project_environment,
    validate_model_configuration,
)
from planning_agent import AnalysisPlan
from report_generator import ReportDraft, _plan_payload, render_report_numbers


PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "review_system_zh.txt"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion_index: int = Field(ge=0)
    evidence_ids: list[str] = Field(min_length=1)
    problem: str = Field(min_length=1)
    requested_change: str = Field(min_length=1)


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "return", "block"]
    issues: list[ReviewIssue]

    @model_validator(mode="after")
    def validate_decision_issues(self):
        if self.decision == "approve" and self.issues:
            raise ValueError("通过审核时 issues 必须为空")
        if self.decision in {"return", "block"} and not self.issues:
            raise ValueError("退回或阻断时必须指出具体问题")
        return self

    def validate_evidence(
        self,
        draft_or_evidence: ReportDraft | Mapping[str, object],
        evidence: Mapping[str, object] | None = None,
    ) -> None:
        draft = draft_or_evidence if isinstance(draft_or_evidence, ReportDraft) else None
        evidence_map = evidence if evidence is not None else draft_or_evidence
        errors = []
        for issue in self.issues:
            if draft is not None and issue.conclusion_index >= len(draft.conclusions):
                errors.append(f"结论索引 {issue.conclusion_index} 不存在")
            for evidence_id in issue.evidence_ids:
                if evidence_id not in evidence_map:
                    errors.append(f"审核引用的证据编号 {evidence_id} 不存在")
        if errors:
            raise ReviewValidationError("审核结果校验失败：" + "; ".join(errors))


class ReviewValidationError(RuntimeError):
    pass


class ReviewRequestError(RuntimeError):
    pass


def review_report(
    question: str,
    plan: AnalysisPlan,
    result: dict,
    draft: ReportDraft,
    evidence: dict[str, dict],
    *,
    client=None,
    environ: Mapping[str, str] | None = None,
) -> ReviewResult:
    mismatch = _metric_mismatch(question, plan, result)
    if mismatch:
        return ReviewResult(decision="block", issues=[ReviewIssue(
            conclusion_index=0,
            evidence_ids=_metric_evidence_ids(result),
            problem=mismatch,
            requested_change="不要发布本次报告；按用户要求的指标重新规划并重新计算。",
        )])

    citation_issue = _ranking_citation_issue(plan, draft, evidence)
    if citation_issue is not None:
        index, cited, problem = citation_issue
        return ReviewResult(decision="return", issues=[ReviewIssue(
            conclusion_index=index,
            evidence_ids=cited,
            problem=problem,
            requested_change="改用本次结果中指标、对象和排序方向均匹配的 ranking 证据；若无法支持该结论则删除或改写。",
        )])

    env = load_project_environment(environ)
    mode = validate_model_configuration(env)
    if client is None:
        client = create_openai_client(env)

    payload = {
        "user_question": question,
        "validated_plan": _plan_payload(plan),
        "calculation_evidence": evidence,
        "rendered_numerical_summary": render_report_numbers(result, plan),
        "report_draft": draft.model_dump(mode="json"),
    }
    schema = json.dumps(ReviewResult.model_json_schema(), ensure_ascii=False, indent=2)
    system_prompt = SYSTEM_PROMPT.rstrip() + "\n\n输出 JSON Schema：\n" + schema
    try:
        if mode == "chat_completions":
            content = chat_completion_json(
                client,
                env["OPENAI_MODEL"],
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            )
            review = ReviewResult.model_validate(json.loads(content))
        else:
            response = client.responses.parse(
                model=env["OPENAI_MODEL"],
                instructions=system_prompt,
                input=json.dumps(payload, ensure_ascii=False),
                text_format=ReviewResult,
            )
            review = response.output_parsed
            if not isinstance(review, ReviewResult):
                raise ReviewRequestError("审核模型未返回可校验的结构化结果。")
    except ModelRequestError as exc:
        raise ReviewRequestError(str(exc)) from None
    except json.JSONDecodeError as exc:
        raise ReviewRequestError(f"审核模型返回的 JSON 无效（第 {exc.lineno} 行，第 {exc.colno} 列）。") from None
    except ValidationError as exc:
        fields = sorted({".".join(map(str, error["loc"])) for error in exc.errors()})
        raise ReviewRequestError("审核结果结构校验失败：" + ", ".join(fields)) from None
    review.validate_evidence(draft, evidence)
    return review


def _metric_evidence_ids(result: dict) -> list[str]:
    evidence = result.get("evidence", {})
    return list(evidence)[:1] or ["filter_scope"]


def _metric_mismatch(question: str, plan: AnalysisPlan, result: dict) -> str | None:
    from planning_agent import metric_request_problem

    intent_problem = metric_request_problem(question, plan) if plan.status == "executable" else None
    if intent_problem and not intent_problem[1]:
        return intent_problem[0]
    requested = set(plan.metrics or ["amount"])
    actual = set(result.get("metrics", ["amount"]))
    if not requested.issubset(actual):
        return f"计划要求指标 {', '.join(sorted(requested))}，但计算结果实际包含 {', '.join(sorted(actual))}。"
    if plan.ranking_order and result.get("rank_by", "amount") != plan.rank_by:
        return f"计划要求按 {plan.rank_by} 排名，但计算结果按 {result.get('rank_by', 'amount')} 排名。"
    return None


def _ranking_citation_issue(plan: AnalysisPlan, draft: ReportDraft, evidence: dict[str, dict]):
    """Do not let a total/single-row citation stand in for a ranked result."""
    import re

    cited_refs = {value for item in draft.conclusions for value in item.evidence_ids}
    expected_metric = getattr(plan, "rank_by", "amount")
    required_refs = {
        evidence_id for evidence_id, item in evidence.items()
        if item.get("unit") == "ranking" and item.get("value", {}).get("metric") == expected_metric
    }
    if plan.analysis_type == "comparison" and plan.dimensions and not required_refs:
        return (
            0,
            [value for value in cited_refs if value in evidence] or ([next(iter(evidence))] if evidence else []),
            f"本次要求按 {expected_metric} 排名，但计算证据中没有对应维度和方向的榜单证据。",
        )
    # Summary tables and bucket rankings are rendered directly from calculation
    # results in the final report. The model need not cite every row in prose;
    # concrete ranking claims below still require the matching ranking record.
    missing = required_refs - cited_refs
    if missing and plan.analysis_type != "summary":
        return (
            0,
            [value for value in cited_refs if value in evidence] or ([next(iter(evidence))] if evidence else []),
            f"报告未覆盖所有要求的排名结果，缺少证据引用：{', '.join(sorted(missing))}。",
        )

    rank_claim = re.compile(r"排名|榜首|最高|最多|第一|最大|最小")
    concrete_entities = set()
    for evidence_id in required_refs:
        value = evidence[evidence_id].get("value", {})
        concrete_entities.update(str(row.get("key")) for row in value.get("rows", []))
        concrete_entities.update(str(row.get("key")) for row in value.get("leaders", []))
    for index, conclusion in enumerate(draft.conclusions):
        if not rank_claim.search(conclusion.text):
            continue
        matched_entities = {entity for entity in concrete_entities if entity and entity in conclusion.text}
        if not matched_entities:
            continue
        requested_metric = None
        quantity_rank = re.search(r"(?:销量|件数|销售件数).{0,10}(?:排名|榜|最高|最多|第一|榜首)|(?:按|以).{0,8}(?:销量|件数).{0,8}(?:排名|排序|榜)", conclusion.text)
        amount_rank = re.search(r"(?:金额|销售额|交易额).{0,10}(?:排名|榜|最高|最多|第一|榜首)|(?:按|以).{0,8}(?:金额|销售额).{0,8}(?:排名|排序|榜)", conclusion.text)
        if quantity_rank and not amount_rank:
            requested_metric = "quantity"
        elif amount_rank and not quantity_rank:
            requested_metric = "amount"
        conclusion_metric = requested_metric or expected_metric
        valid_rank_refs = []
        for evidence_id in conclusion.evidence_ids:
            item = evidence.get(evidence_id)
            if not item or item.get("unit") != "ranking":
                continue
            rank_value = item.get("value", {})
            if rank_value.get("metric") != conclusion_metric:
                continue
            valid_rank_refs.append(evidence_id)
        if not valid_rank_refs:
            valid_citations = [value for value in conclusion.evidence_ids if value in evidence]
            if not valid_citations and evidence:
                valid_citations = [next(iter(evidence))]
            return (
                index,
                valid_citations,
                f"第 {index + 1} 条排名结论没有引用支持该排名的 {conclusion_metric} 排名证据；总额或单项数值不能证明榜首。",
            )
        if not matched_entities:
            continue
        if not matched_entities:
            continue
        matched_rank_ids = set(valid_rank_refs)
        unmatched_entities = set()
        for entity in matched_entities:
            entity_has_rank_reference = any(
                evidence_id in conclusion.evidence_ids
                and entity in {
                    str(row.get("key"))
                    for row in evidence[evidence_id].get("value", {}).get("rows", [])
                }
                for evidence_id in matched_rank_ids
            )
            if not entity_has_rank_reference:
                unmatched_entities.add(entity)
        if unmatched_entities:
            return (
                index,
                [value for value in conclusion.evidence_ids if value in evidence] or valid_rank_refs,
                f"第 {index + 1} 条将实体 {', '.join(sorted(unmatched_entities))} 写成排名结论，但没有引用覆盖该实体的对应排名记录。",
            )
        leader_claim = re.compile(r"最高|最多|第一|榜首|最大|冠军")
        if leader_claim.search(conclusion.text):
            for evidence_id in valid_rank_refs:
                value = evidence[evidence_id].get("value", {})
                leader_keys = {str(row.get("key")) for row in value.get("leaders", [])}
                all_keys = {str(row.get("key")) for row in value.get("rows", [])}
                for key in all_keys - leader_keys:
                    escaped = re.escape(key)
                    before = re.search(rf"{escaped}.{{0,16}}(?:最高|最多|第一|榜首|最大|冠军)", conclusion.text)
                    after = re.search(rf"(?:最高|最多|第一|榜首|最大|冠军).{{0,16}}{escaped}", conclusion.text)
                    if before or after:
                        return (
                            index,
                            [evidence_id],
                            f"第 {index + 1} 条把非榜首实体 {key} 写成最高项，但该排名证据的榜首是："
                            + "、".join(str(row.get("key")) for row in value.get("leaders", [])) + "。",
                        )
    return None
