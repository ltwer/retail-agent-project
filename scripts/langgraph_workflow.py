from datetime import date
from pathlib import Path
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from planning_agent import AnalysisPlan, load_csv_metadata, metric_request_problem, plan_question, validate_plan_dates
from report_generator import ReportDraft, build_evidence, generate_report_draft
from report_reviewer import ReviewResult, review_report, _metric_mismatch
from retail_analysis import (
    FilterResolutionError,
    analyze_plan,
    resolve_filter_scope,
    validate_explicit_filter_extraction,
)


class WorkflowState(TypedDict):
    csv_path: str | Path
    question: str
    latest_input: str
    analysis_request: str
    plan: AnalysisPlan
    steps: list[str]
    clarification_question: str | None
    unsupported_reason: str | None
    filter_scope: dict | None
    result: dict | None
    report_draft: ReportDraft | None
    draft_history: list[ReportDraft]
    evidence: dict[str, dict] | None
    review_result: ReviewResult | None
    review_history: list[ReviewResult]
    review_attempts: int
    final_report: ReportDraft | None


Planner = Callable[[str, str | Path], AnalysisPlan]
Calculator = Callable[..., dict]
Drafter = Callable[..., tuple[ReportDraft, dict[str, dict]]]
Reviewer = Callable[..., ReviewResult]
WorkflowUpdate = Callable[[str, dict], None]
WorkflowEvent = Callable[[dict], None]


def _analysis_request(state: WorkflowState) -> str:
    return state.get("analysis_request") or state["question"]


def _agent_request(state: WorkflowState) -> str:
    effective = _analysis_request(state)
    latest = state.get("latest_input") or state["question"]
    if latest == effective:
        return effective
    return f"{effective}\n\n用户本轮最新输入：{latest}"


def build_workflow(
    *,
    planner: Planner = plan_question,
    calculator: Calculator = analyze_plan,
    drafter: Drafter = generate_report_draft,
    reviewer: Reviewer = review_report,
    on_event: WorkflowEvent | None = None,
):
    def plan_node(state: WorkflowState) -> dict:
        request = _analysis_request(state)
        plan = planner(request, state["csv_path"])
        request = getattr(planner, "resolved_analysis_request", request)
        clarification = plan.clarification_question
        filter_scope = None
        if plan.status == "executable":
            try:
                validate_plan_dates(plan, load_csv_metadata(state["csv_path"]))
            except FilterResolutionError as exc:
                clarification = str(exc)
                plan = plan.model_copy(
                    update={"status": "clarification", "clarification_question": clarification}
                )
            except ValueError as exc:
                plan = plan.model_copy(
                    update={
                        "status": "unsupported",
                        "clarification_question": None,
                        "unsupported_reason": f"日期范围无法执行：{exc}",
                    }
                )
            metric_problem = metric_request_problem(request, plan) if plan.status == "executable" else None
            if metric_problem and not metric_problem[1]:
                plan = plan.model_copy(update={
                    "status": "unsupported", "clarification_question": None,
                    "unsupported_reason": metric_problem[0],
                })
            if plan.status == "executable":
                try:
                    filter_scope = resolve_filter_scope(
                        state["csv_path"], plan.country_filters, plan.product_filters
                    )
                    validate_explicit_filter_extraction(
                        request, state["csv_path"], filter_scope
                    )
                except FilterResolutionError as exc:
                    clarification = str(exc)
                    plan = plan.model_copy(
                        update={"status": "clarification", "clarification_question": clarification}
                    )
        metric_problem = metric_request_problem(request, plan) if plan.status == "executable" else None
        if metric_problem:
            reason, needs_clarification = metric_problem
            if needs_clarification:
                clarification = reason
                plan = plan.model_copy(update={
                    "status": "clarification", "clarification_question": reason,
                    "unsupported_reason": None,
                })
            else:
                plan = plan.model_copy(update={
                    "status": "unsupported", "clarification_question": None,
                    "unsupported_reason": reason,
                })
        if plan.status == "clarification" and not clarification:
            clarification = "请补充执行本次分析所需的信息。"
        return {
            "plan": plan,
            "analysis_request": request,
            "filter_scope": filter_scope,
            "steps": [*state.get("steps", []), "plan"],
            "clarification_question": clarification,
            "unsupported_reason": plan.unsupported_reason,
        }

    def after_plan(state: WorkflowState) -> str:
        plan = state["plan"]
        if plan.status != "executable" or state["clarification_question"] or state.get("unsupported_reason"):
            return "end"
        return "calculate"

    def calculate_node(state: WorkflowState) -> dict:
        plan = state["plan"]
        if calculator is analyze_plan:
            result = calculator(state["csv_path"], plan, filter_scope=state.get("filter_scope"))
        elif plan.analysis_type == "summary":
            result = calculator(state["csv_path"], plan)
        else:
            result = calculator(
                state["csv_path"],
                plan.period1_start,
                plan.period1_end,
                plan.period2_start,
                plan.period2_end,
                dimensions=plan.dimensions,
                direction=plan.direction,
                top_n=plan.top_n,
            )
        mismatch = _metric_mismatch(_analysis_request(state), plan, result)
        if mismatch:
            return {
                "result": result,
                "unsupported_reason": mismatch,
                "steps": [*state.get("steps", []), "calculate"],
            }
        return {
            "result": result,
            "steps": [*state.get("steps", []), "calculate"],
        }

    def draft_report_node(state: WorkflowState) -> dict:
        feedback = None
        if state.get("review_result") and state["review_result"].decision == "return":
            feedback = [issue.model_dump(mode="json") for issue in state["review_result"].issues]
        if feedback is None:
            generated = drafter(_agent_request(state), state["plan"], state["result"])
        else:
            generated = drafter(
                _agent_request(state),
                state["plan"],
                state["result"],
                review_feedback=feedback,
                evidence=state.get("evidence"),
            )
        if isinstance(generated, tuple):
            draft, evidence = generated
        else:
            draft = generated
            evidence = build_evidence(state["result"], state["plan"])
        return {
            "report_draft": draft,
            "draft_history": [*state.get("draft_history", []), draft],
            "evidence": evidence,
            "review_result": None,
            "steps": [*state.get("steps", []), "draft_report"],
        }

    def review_node(state: WorkflowState) -> dict:
        review = reviewer(
            _agent_request(state),
            state["plan"],
            state["result"],
            state["report_draft"],
            state["evidence"],
        )
        attempts = state.get("review_attempts", 0) + 1
        return {
            "review_result": review,
            "review_history": [*state.get("review_history", []), review],
            "review_attempts": attempts,
            "final_report": state["report_draft"] if review.decision == "approve" else None,
            "steps": [*state.get("steps", []), "review"],
        }

    def after_review(state: WorkflowState) -> str:
        if state["review_result"].decision == "approve":
            return "end"
        if state["review_result"].decision == "block":
            return "end"
        if state.get("review_attempts", 0) < 2:
            return "revise"
        return "end"

    def with_node_events(node: str, handler):
        def run(state: WorkflowState) -> dict:
            attempt = 1 + state.get("steps", []).count(node)
            if on_event is not None:
                on_event({"type": "node_started", "node": node, "attempt": attempt})
            try:
                return handler(state)
            except Exception as exc:
                if on_event is not None:
                    on_event({
                        "type": "node_failed", "node": node, "attempt": attempt,
                        "error": str(exc),
                    })
                raise
        return run

    graph = StateGraph(WorkflowState)
    graph.add_node("plan", with_node_events("plan", plan_node))
    graph.add_node("calculate", with_node_events("calculate", calculate_node))
    graph.add_node("draft_report", with_node_events("draft_report", draft_report_node))
    graph.add_node("review", with_node_events("review", review_node))
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", after_plan, {"calculate": "calculate", "end": END})
    graph.add_conditional_edges(
        "calculate", lambda state: "end" if state.get("unsupported_reason") else "draft_report",
        {"draft_report": "draft_report", "end": END},
    )
    graph.add_edge("draft_report", "review")
    graph.add_conditional_edges("review", after_review, {"revise": "draft_report", "end": END})
    return graph.compile()


def run_workflow(
    csv_path: str | Path,
    question: str,
    *,
    planner: Planner = plan_question,
    calculator: Calculator = analyze_plan,
    drafter: Drafter = generate_report_draft,
    reviewer: Reviewer = review_report,
    on_update: WorkflowUpdate | None = None,
    on_event: WorkflowEvent | None = None,
    analysis_request: str | None = None,
    latest_input: str | None = None,
) -> WorkflowState:
    workflow = build_workflow(
        planner=planner,
        calculator=calculator,
        drafter=drafter,
        reviewer=reviewer,
        on_event=on_event,
    )
    initial_state: WorkflowState = {
        "csv_path": csv_path,
        "question": question,
        "latest_input": latest_input or question,
        "analysis_request": analysis_request or question,
        "steps": [],
        "clarification_question": None,
        "unsupported_reason": None,
        "filter_scope": None,
        "result": None,
        "report_draft": None,
        "draft_history": [],
        "evidence": None,
        "review_result": None,
        "review_history": [],
        "review_attempts": 0,
        "final_report": None,
    }
    final_state = dict(initial_state)
    for event in workflow.stream(initial_state, stream_mode="updates"):
        for node, update in event.items():
            final_state.update(update)
            if on_update is not None:
                on_update(node, update)
    return final_state  # type: ignore[return-value]
