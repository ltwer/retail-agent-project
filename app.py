from __future__ import annotations

import sys
import json
import tempfile
import uuid
from io import BytesIO
from datetime import datetime
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from langgraph_workflow import run_workflow
from planning_agent import AnalysisPlan, ModelConfigurationError, plan_question
from conversation_context import (
    apply_plan_changes,
    bind_context_to_source,
    classify_user_turn,
    build_reference_catalog,
    merge_clarified_dates,
    merge_drilldown_plan,
    reference_source_label,
    resolve_reference,
    serialize_plan_for_context,
    source_identity,
    summarize_plan_conditions,
)
from report_exporter import export_approved_report
from result_chart import render_result_chart
from web_app_helpers import CsvValidationError, validate_csv
from web_presentation import (
    board_rows,
    completed_node_event,
    complete_trace_event,
    conclusion_groups,
    fail_trace_event,
    format_money,
    format_metric_value,
    format_signed_money,
    format_signed_percent,
    filter_scope_summary,
    summary_leader,
    summary_result_title,
    ranking_display_note,
    summary_ranking_rows,
    summary_total_label,
    initial_trace,
    render_review_comparison,
    skip_pending_trace,
    start_node_event,
    run_status_label,
)


DEFAULT_CSV = PROJECT_ROOT / "data" / "retail_demo.csv"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
EXAMPLE_A = "比较 2011 年 9 月 12–18 日和 9 月 19–25 日，列出金额增长最多的 5 个国家。"
EXAMPLE_B = "哪些商品卖得更好了？"

st.set_page_config(page_title="零售观察站", page_icon="📈", layout="wide")
st.markdown(
    """
    <style>
    :root {
      --paper: #f7f8f2;
      --ink: #173d32;
      --ink-soft: #31584b;
      --mint: #cfe5d5;
      --mint-strong: #92c7a8;
      --line: #dce4db;
      --muted: #63746b;
      --white: #ffffff;
      --gold: #c27b36;
      --red: #a84b42;
    }
    .stApp { background: var(--paper); color: #1e3029; }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stAppDeployButton"] { display: none; }
    .block-container { max-width: 1500px; padding-top: .65rem; padding-bottom: 2rem; }
    .masthead {
      background: var(--ink); color: #f4f7ee; border-radius: 8px;
      padding: .65rem 1.25rem .6rem; margin-bottom: .65rem;
      border-bottom: 3px solid var(--mint-strong);
    }
    .masthead h1 { color: #f4f7ee; font-size: 1.85rem; margin: 0; line-height: 1.1; }
    .masthead p { color: #d6e5d9; margin: .18rem 0 0; font-size: .9rem; }
    .section-title { color: var(--ink); font-size: 1.13rem; font-weight: 700; margin: .1rem 0 .35rem; }
    .eyebrow { color: #bdd5c7; text-transform: uppercase; font-size: .66rem; font-weight: 700; }
    .panel {
      background: var(--white); border: 1px solid var(--line); border-radius: 8px;
      padding: 1rem 1.1rem; margin: 0 0 1rem;
    }
    .run-status { font-size: 1rem; font-weight: 700; padding: .45rem .7rem; border-radius: 6px; margin: .2rem 0 .5rem; }
    .status-idle { background: #e8ede8; color: #46594f; }
    .status-running { background: #e3f0e6; color: #24573e; }
    .status-approved { background: #d9eddd; color: #205b35; }
    .status-clarification { background: #fff0d8; color: #805019; }
    .status-failed { background: #f7e3df; color: #8c332e; }
    .trace-item { border-left: 3px solid var(--line); padding: .05rem .45rem .42rem .7rem; margin-left: .2rem; }
    .trace-item[data-state="running"] { border-left-color: #c27b36; background: #fff9ef; }
    .trace-item[data-state="completed"] { border-left-color: var(--mint-strong); }
    .trace-item[data-state="failed"] { border-left-color: var(--red); background: #fff3f0; }
    .trace-item[data-state="skipped"] { opacity: .72; }
    .trace-state { float: right; color: #31584b; font-size: .72rem; font-weight: 700; }
    .trace-name { color: var(--ink); font-weight: 700; font-size: .95rem; }
    .trace-detail { color: var(--muted); font-size: .85rem; margin-top: .15rem; }
    div[data-testid="stMetric"] { background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: .48rem .72rem; }
    div[data-testid="stMetricValue"] { color: var(--ink); font-size: 1.35rem; }
    .stButton > button[kind="primary"], .stFormSubmitButton > button {
      background: var(--ink); color: #fff; border: 0; border-radius: 7px;
      min-height: 2.65rem; font-size: 1.02rem; font-weight: 700;
    }
    .stButton > button[kind="primary"]:hover, .stFormSubmitButton > button:hover { background: #245a48; color: #fff; }
    div[data-baseweb="tab-list"] { gap: .4rem; }
    div[data-baseweb="tab"] { background: #e9efe9; border-radius: 6px; }
    div[data-baseweb="tab-highlight"] { background: var(--mint-strong); }
    [data-testid="stDataFrame"] { overflow-x: auto; }
    [data-testid="stImage"] img { max-height: 680px; object-fit: contain; }
    .small-note { color: var(--muted); font-size: .88rem; }
    @media (max-width: 700px) {
      .block-container { padding: .55rem .65rem 1.5rem; }
      .masthead { padding: .65rem .85rem; }
      .masthead h1 { font-size: 1.55rem; }
      div[data-testid="stMetricValue"] { font-size: 1.22rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _status_class(status: str) -> str:
    return {
        "running": "status-running",
        "approved": "status-approved",
        "clarification": "status-clarification",
        "unsupported": "status-clarification",
        "failed": "status-failed",
    }.get(status, "status-idle")


def _draw_trace_event(item: dict, container) -> None:
    state_labels = {
        "pending": "待执行", "running": "进行中", "completed": "已完成",
        "skipped": "未执行", "failed": "失败",
    }
    state_label = state_labels.get(item.get("status"), "待执行")
    container.markdown(
        f'<div class="trace-item" data-state="{escape(item.get("status", "completed"))}">'
        f'<span class="trace-state">{state_label}</span>'
        f'<div class="trace-name">{escape(item["label"])}</div>'
        f'<div class="trace-detail">{escape(item["detail"])}</div>'
        "</div>",
        unsafe_allow_html=True,
    )
    if item.get("details"):
        comparison = item.get("review_comparison") or {}
        label = "查看审核意见与返修对照" if comparison.get("revision_draft") else "查看审核意见"
        with container.expander(label, expanded=False):
            details = comparison or item["details"]
            if isinstance(details, dict):
                render_review_comparison(details, container)
            else:
                container.write(details)


def _draw_trace(items: list[dict], slot) -> None:
    slot.empty()
    container = slot.container(border=True)
    with container:
        if not items:
            container.markdown('<div class="small-note">暂无节点事件。</div>', unsafe_allow_html=True)
            return
        for item in items:
            _draw_trace_event(item, container)


def _render_board(result: dict, dimension: str, direction: str, top_n: int) -> None:
    title = "增长榜" if direction == "growth" else "下降榜"
    rows, note = board_rows(result, dimension, direction, top_n)
    with st.container(border=True):
        st.markdown(f"**{title}**")
        if note:
            st.caption(note)
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", alt=f"{title}金额比较")
        else:
            st.caption("没有符合条件的项目。")


def _render_summary_ranking_table(run: dict) -> None:
    plan = run["state"]["plan"]
    result = run["state"]["result"]
    rows = summary_ranking_rows(plan, result)
    if not rows:
        st.info(result.get("filter_scope", {}).get("message") or "当前范围没有符合条件的记录。")
        return
    if plan.ranking_order:
        order = "最高" if plan.ranking_order == "desc" else "最低"
        st.markdown(f"**金额{order}榜 · 前 {plan.top_n} 项**")
    else:
        st.markdown("**分组金额**")
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        alt="按本次日期及实体筛选计算的排名结果",
    )


def _render_success_top(run: dict) -> None:
    state = run["state"]
    plan = state["plan"]
    result = state["result"]
    metrics_available = result.get("metrics", ["amount"])

    summary_left, trace_right = st.columns([2.4, 1], gap="large")
    with summary_left:
        st.markdown('<div class="run-status status-approved">审核通过</div>', unsafe_allow_html=True)
        st.caption("本次使用的条件：" + run.get("conditions", summarize_plan_conditions(plan)))
        display_metric = metrics_available[0]
        if len(metrics_available) > 1:
            display_metric = st.radio(
                "图表与指标", metrics_available,
                format_func=lambda value: "正向交易金额（£）" if value == "amount" else "有效销售件数（件）",
                horizontal=True, key=f"metric_{run['id']}", label_visibility="collapsed",
            )
        run["display_metric"] = display_metric
        if result.get("analysis_type") == "summary":
            st.markdown(f"### {summary_result_title(plan, result)}")
            metrics = st.columns(3, gap="small")
            total_value = result.get("metric_totals", {}).get(display_metric, 0)
            metrics[0].metric(summary_total_label(plan, result), format_metric_value(display_metric, total_value), border=True)
            date_range = result["date_range"]
            groups = result.get("groups", [])
            leader = summary_leader(plan, result)
            if leader:
                metrics[1].metric(leader[0], leader[1], border=True)
            else:
                metrics[1].metric("分组数", str(len(groups)), border=True)
            metrics[2].metric("分析日期范围", f"{date_range['start']} 至 {date_range['end']}", border=True)
            _render_summary_ranking_table(run)
        else:
            metric_result = result.get("metric_results", {}).get(display_metric, result)
            metrics = st.columns(4, gap="small")
            unit_name = "金额" if display_metric == "amount" else "销量"
            difference = (format_signed_money(metric_result["difference"]) if display_metric == "amount" else
                          f"{'-' if metric_result['difference'] < 0 else '+'}{abs(metric_result['difference']):,.0f} 件")
            metrics[0].metric(f"前期{unit_name}", format_metric_value(display_metric, metric_result["period1_total"]), border=True)
            metrics[1].metric(f"后期{unit_name}", format_metric_value(display_metric, metric_result["period2_total"]), border=True)
            metrics[2].metric("差额", difference, border=True)
            metrics[3].metric("变化率", format_signed_percent(metric_result["change_rate"]), border=True)
        with st.expander("查看本次分析问题"):
            st.write(run["question"])
        _render_success_content(run)
    with trace_right:
        st.markdown('<div class="section-title">执行轨迹 · 已完成</div>', unsafe_allow_html=True)
        _draw_trace(run["trace"], st.empty())


def _render_success_content(run: dict) -> None:
    state = run["state"]
    plan = state["plan"]
    result = state["result"]
    scope = result.get("filter_scope") or {}
    scope_text = filter_scope_summary(scope)
    if result.get("analysis_type") == "summary" or plan.analysis_type == "summary":
        _render_summary_success_content(run)
        return
    if scope.get("message"):
        st.warning(f"{scope_text} {scope['message']}", title="筛选范围无记录")
    elif scope_text:
        st.caption(scope_text)
    if len(plan.dimensions) > 1:
        dimension = st.radio(
            "榜单维度",
            plan.dimensions,
            format_func=lambda value: "国家" if value == "country" else "商品",
            horizontal=True,
            key=f"dimension_{run['id']}",
            label_visibility="collapsed",
        )
    else:
        dimension = plan.dimensions[0] if plan.dimensions else None

    directions = ["growth", "decline"] if plan.direction == "both" else [plan.direction]
    directions = [value for value in directions if value in {"growth", "decline"}]
    if dimension and len(directions) == 2:
        board_cols = st.columns(2, gap="small")
        for col, direction in zip(board_cols, directions):
            with col:
                _render_board(result, dimension, direction, plan.top_n)
    elif dimension and directions:
        _render_board(result, dimension, directions[0], plan.top_n)

    st.image(
        str(run["image_path"]),
        width="stretch",
        alt="两期正向交易金额对比，日期和金额与本次工作流结果一致",
    )

    st.markdown('<div class="section-title">报告结论</div>', unsafe_allow_html=True)
    overview, groups = conclusion_groups(
        state["final_report"], state["evidence"], plan.dimensions, plan.direction
    )
    if overview:
        with st.container(border=True):
            st.markdown("**总览**")
            st.write(overview)
    if groups:
        group_cols = st.columns(min(2, len(groups)), gap="small")
        for index, (_dimension, _direction, title, conclusions) in enumerate(groups):
            with group_cols[index % len(group_cols)]:
                with st.container(border=True):
                    st.markdown(f"**{title}**")
                    for conclusion in conclusions:
                        st.write(conclusion.text)
                        evidence_ids = " · ".join(f"`{escape(value)}`" for value in conclusion.evidence_ids)
                        st.markdown(evidence_ids)

    with st.expander("展开本次计算证据"):
        evidence_rows = []
        for identifier, item in state["evidence"].items():
            value = item["value"]
            if item["unit"] == "GBP":
                rendered_value = format_signed_money(value) if identifier.endswith(".delta") else format_money(value)
            elif item["unit"] == "percent":
                rendered_value = format_signed_percent(value / 100)
            else:
                rendered_value = str(value)
            evidence_rows.append({"证据编号": identifier, "指标": item["label"], "数值": rendered_value})
        st.dataframe(pd.DataFrame(evidence_rows), hide_index=True, width="stretch", alt="本次计算证据编号和数值")

    report_path = Path(run["report_path"])
    image_path = Path(run["image_path"])
    downloads = st.columns(2, gap="small")
    downloads[0].download_button(
        "下载报告 Markdown", report_path.read_bytes(), file_name="report.md", mime="text/markdown",
        key=f"download_report_{run['id']}", width="stretch",
    )
    downloads[1].download_button(
        "下载金额对比图", image_path.read_bytes(), file_name="period_comparison.png", mime="image/png",
        key=f"download_chart_{run['id']}", width="stretch",
    )
    with st.expander("导出详情"):
        st.code(f"Markdown：{report_path}\n图表：{image_path}", language=None, wrap_lines=True)


def _render_summary_success_content(run: dict) -> None:
    state = run["state"]
    result = state["result"]
    plan = state["plan"]
    if result.get("filter_scope", {}).get("message"):
        st.warning(result["filter_scope"]["message"], title="所选范围内无记录")
    st.image(
        str(run["image_path"]),
        width="stretch",
        alt="根据本次汇总或排名结果生成的金额图；类别排名从高到低展示",
    )

    st.markdown('<div class="section-title">报告结论</div>', unsafe_allow_html=True)
    for conclusion in state["final_report"].conclusions:
        with st.container(border=True):
            st.write(conclusion.text)
            refs = " · ".join(f"`{escape(value)}`" for value in conclusion.evidence_ids)
            st.markdown(refs)
    with st.expander("展开本次计算证据"):
        evidence_rows = []
        for identifier, item in state["evidence"].items():
            value = format_money(item["value"]) if item["unit"] == "GBP" else str(item["value"])
            evidence_rows.append({"证据编号": identifier, "指标": item["label"], "数值": value})
        st.dataframe(pd.DataFrame(evidence_rows), hide_index=True, width="stretch", alt="本次计算证据")
    report_path, image_path = Path(run["report_path"]), Path(run["image_path"])
    downloads = st.columns(2, gap="small")
    downloads[0].download_button(
        "下载报告 Markdown", report_path.read_bytes(), file_name="report.md", mime="text/markdown",
        key=f"download_report_{run['id']}", width="stretch",
    )
    downloads[1].download_button(
        "下载分组图表", image_path.read_bytes(), file_name="period_comparison.png", mime="image/png",
        key=f"download_chart_{run['id']}", width="stretch",
    )
    with st.expander("导出详情"):
        st.code(f"Markdown：{report_path}\n图表：{image_path}", language=None, wrap_lines=True)


def _record_update(run: dict, node: str, update: dict, trace_container, status_slot) -> None:
    if node == "plan":
        run["plan"] = update["plan"]
        run["clarification_question"] = update.get("clarification_question")
        run["filter_scope"] = update.get("filter_scope")
        run["conditions"] = summarize_plan_conditions(
            update["plan"],
            {"start": update["plan"].start_date, "end": update["plan"].end_date}
            if update["plan"].time_scope == "full_data" else None,
        )
        if run.get("reference_provenance"):
            run["conditions"] = f"{run['reference_provenance']}；本次：{run['conditions']}"
    elif node == "calculate":
        run["result"] = update["result"]
    elif node == "draft_report":
        run["report_draft"] = update["report_draft"]
        run["draft_history"] = update.get("draft_history", [update["report_draft"]])
    elif node == "review":
        run.setdefault("review_records", []).append(update["review_result"])
        run["review_history"] = update.get("review_history", run.get("review_records", []))
    active = next((item for item in reversed(run["trace"])
                   if item["node"] == node and item.get("status") == "running"), None)
    attempt = active.get("attempt", 1) if active else 1
    event = completed_node_event(
        node, update, run["trace"], plan=run.get("plan"),
        draft_history=run.get("draft_history"), review_history=run.get("review_history"),
    )
    complete_trace_event(run["trace"], node, attempt, event)
    if node == "plan" and (update["plan"].status != "executable" or update.get("clarification_question")):
        reason = update.get("clarification_question") or update.get("unsupported_reason") or "计划未进入计算阶段"
        skip_pending_trace(run["trace"], reason)
    _draw_trace(run["trace"], trace_container)
    status_slot.markdown(
        f'<div class="run-status status-running">运行中 · 已完成 {sum(item["status"] == "completed" for item in run["trace"])} 个节点事件</div>',
        unsafe_allow_html=True,
    )


def _record_workflow_event(run: dict, event: dict, trace_container, status_slot) -> None:
    node, attempt = event["node"], event.get("attempt", 1)
    if event["type"] == "node_started":
        start_node_event(run["trace"], event)
    elif event["type"] == "node_failed":
        fail_trace_event(run["trace"], node, attempt, event.get("error", "节点执行失败。"))
        run["status"] = "failed"
        run["error"] = event.get("error")
    _draw_trace(run["trace"], trace_container)
    active = next((item for item in reversed(run["trace"]) if item.get("status") == "running"), None)
    if active:
        label = active["label"].replace(" · 进行中", "")
        status_slot.markdown(
            f'<div class="run-status status-running">当前步骤：{escape(label)} · 进行中</div>',
            unsafe_allow_html=True,
        )


def _render_non_approved_run(run: dict):
    left, right = st.columns([2.05, 1], gap="large")
    with left:
        status_slot = st.empty()
        status_slot.markdown(
            f'<div class="run-status {_status_class(run["status"])}">{run_status_label(run)}</div>',
            unsafe_allow_html=True,
        )
        st.caption("本次使用的条件：" + run.get("conditions", "尚未确定分析条件。"))
        if run["status"] == "clarification":
            question = run.get("clarification_question") or (run.get("state") or {}).get("clarification_question")
            st.info(question or "请补充本次分析所需的信息。", title="需要补充信息")
            st.caption("可以直接在上方输入补充内容继续分析。")
        elif run["status"] == "unsupported":
            state = run.get("state") or {}
            st.warning(state.get("unsupported_reason") or state.get("clarification_question") or run.get("error"), title="当前能力不支持")
        elif run["status"] == "failed":
            state = run.get("state") or {}
            review = state.get("review_result")
            if review and review.decision == "return":
                st.caption("已达到返修上限，未生成最终报告。")
            else:
                st.error(run["error"] or "本次运行未完成。")
        else:
            st.markdown('<div class="run-status status-running">工作流运行中</div>', unsafe_allow_html=True)
    with right:
        st.markdown('<div class="section-title">执行轨迹</div>', unsafe_allow_html=True)
        trace_container = st.empty()
        _draw_trace(run["trace"], trace_container)
    return trace_container, status_slot


def _finish_run(run: dict, state: dict) -> None:
    run["state"] = state
    run["effective_request"] = state.get("analysis_request", run.get("effective_request"))
    plan = state.get("plan")
    if plan:
        result = state.get("result") or {}
        run["conditions"] = summarize_plan_conditions(plan, result.get("date_range"))
        if run.get("reference_provenance"):
            run["conditions"] = f"{run['reference_provenance']}；本次：{run['conditions']}"
        run["clarification_question"] = state.get("clarification_question")
    if plan and plan.status == "unsupported":
        run["status"] = "unsupported"
        run["error"] = plan.unsupported_reason
    elif state.get("unsupported_reason"):
        run["status"] = "unsupported"
        run["error"] = state["unsupported_reason"]
    elif state.get("clarification_question"):
        run["status"] = "clarification"
        if (run.get("reference_pending_drill") or {}).get("kind") == "drill_choice":
            run["clarification_kind"] = "drill_choice"
        run["clarification_question"] = state.get("clarification_question")
    elif state.get("review_result") and state["review_result"].decision == "approve" and state.get("final_report"):
        report_path, image_path = export_approved_report(state, OUTPUTS_DIR)
        run["status"] = "approved"
        run["report_path"] = str(report_path.resolve())
        run["image_path"] = str(image_path.resolve())
    else:
        run["status"] = "failed"
        review = state.get("review_result")
        if review and review.issues:
            run["error"] = "审核复核未通过，已达到一次返修上限。"
        else:
            run["error"] = "工作流未生成经审核通过的最终报告。"


st.session_state.setdefault("question_input", "")
st.session_state.setdefault("run_history", [])
st.session_state.setdefault("conversation_context", {
    "data_identity": None, "pending": None, "last_success": None,
})
def _render_board(result: dict, dimension: str, direction: str, top_n: int, metric: str = "amount") -> None:
    title = "增长榜" if direction == "growth" else "下降榜"
    rows, note = board_rows(result, dimension, direction, top_n, metric)
    with st.container(border=True):
        st.markdown(f"**{title} · {'金额' if metric == 'amount' else '销量'}**")
        if note:
            st.caption(note)
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.caption("没有符合条件的项目。")


def _render_summary_ranking_table(run: dict) -> None:
    plan, result = run["state"]["plan"], run["state"]["result"]
    rows = summary_ranking_rows(plan, result)
    if not rows:
        st.info(result.get("filter_scope", {}).get("message") or "当前范围没有符合条件的记录。")
        return
    metric_name = "金额" if result.get("rank_by") == "amount" else "销量"
    if plan.ranking_order and len(result.get("group_by_fields", [])) == 2:
        st.markdown("**各期国家 / 商品完整明细（排名另列）**")
    elif plan.ranking_order:
        order = "最高" if plan.ranking_order == "desc" else "最低"
        st.markdown(f"**按{metric_name}{order} · 前 {plan.top_n} 项**")
    else:
        st.markdown("**分组明细**")
    is_composition = len(result.get("group_by_fields", [])) == 2
    if is_composition:
        st.caption(f"共 {len(rows)} 条明细")
    st.dataframe(
        pd.DataFrame(rows), hide_index=True, width="stretch",
        height="content" if len(rows) <= 10 else "auto",
        alt="按计划分组的金额和件数明细" if is_composition else "本次分组或排名结果",
    )
    if len(result.get("group_by_fields", [])) == 2 and plan.ranking_order:
        time_field = result["group_by_fields"][0]
        time_name = {"day": "每日", "week": "每周", "month": "每月"}.get(time_field, "每期")
        for ranking in result.get("rankings", []):
            st.markdown(f"**{ranking['period_label']} · {time_name}{metric_name}{ranking['order_label']}榜**")
            note = ranking_display_note(ranking)
            if note:
                st.caption(note)
            rank_rows = [
                {"排名": item["rank"], "实体": item["label"], "指标": format_metric_value(ranking["metric"], item["value"])}
                for item in ranking["rows"]
            ]
            if len(ranking.get("leaders", [])) > 1:
                st.caption("并列第一：" + "、".join(row["label"] for row in ranking["leaders"]))
            st.dataframe(
                pd.DataFrame(rank_rows), hide_index=True, width="stretch",
                height="content" if len(rank_rows) <= 10 else "auto",
                alt=f"{ranking['period_label']}各项排名",
            )
    elif plan.ranking_order and result.get("rankings"):
        note = ranking_display_note(result["rankings"][0])
        if note:
            st.caption(note)


def _metric_chart_bytes(result: dict, plan, metric: str) -> bytes:
    buffer = BytesIO()
    render_result_chart(result, plan, buffer, metric=metric)
    return buffer.getvalue()


def _render_success_top(run: dict) -> None:
    state, plan, result = run["state"], run["state"]["plan"], run["state"]["result"]
    available_metrics = result.get("metrics", ["amount"])
    left, right = st.columns([2.4, 1], gap="large")
    with left:
        st.markdown('<div class="run-status status-approved">审核通过</div>', unsafe_allow_html=True)
        st.caption("本次使用的条件：" + run.get("conditions", summarize_plan_conditions(plan)))
        display_metric = available_metrics[0]
        if len(available_metrics) > 1:
            display_metric = st.radio(
                "查看指标", available_metrics,
                format_func=lambda value: "正向交易金额（£）" if value == "amount" else "有效销售件数（件）",
                horizontal=True, key=f"metric_{run['id']}", label_visibility="collapsed",
            )
        run["display_metric"] = display_metric
        if result.get("analysis_type") == "summary":
            st.markdown(f"### {summary_result_title(plan, result)}")
            cards = st.columns(3, gap="small")
            total = result.get("metric_totals", {}).get(display_metric)
            if total is None:
                total = result.get("summary", {}).get("total") or 0
            cards[0].metric(summary_total_label(plan, {**result, "rank_by": display_metric}), format_metric_value(display_metric, total), border=True)
            leader = summary_leader(plan, result)
            cards[1].metric(*(leader or ("分组数", str(len(result.get("groups", []))))), border=True)
            date_range = result["date_range"]
            cards[2].metric("分析日期范围", f"{date_range['start']} 至 {date_range['end']}", border=True)
            _render_summary_ranking_table(run)
        else:
            metric_result = result.get("metric_results", {}).get(display_metric, result)
            unit_name = "金额" if display_metric == "amount" else "销量"
            difference = (format_signed_money(metric_result["difference"]) if display_metric == "amount" else
                          f"{'-' if metric_result['difference'] < 0 else '+'}{abs(metric_result['difference']):,.0f} 件")
            cards = st.columns(4, gap="small")
            cards[0].metric(f"前期{unit_name}", format_metric_value(display_metric, metric_result["period1_total"]), border=True)
            cards[1].metric(f"后期{unit_name}", format_metric_value(display_metric, metric_result["period2_total"]), border=True)
            cards[2].metric("差额", difference, border=True)
            cards[3].metric("变化率", format_signed_percent(metric_result["change_rate"]), border=True)
        with st.expander("查看本次分析问题"):
            st.write(run["question"])
        _render_success_content(run)
    with right:
        st.markdown('<div class="section-title">执行轨迹 · 已完成</div>', unsafe_allow_html=True)
        _draw_trace(run["trace"], st.empty())


def _render_success_content(run: dict) -> None:
    state, plan, result = run["state"], run["state"]["plan"], run["state"]["result"]
    metric = run.get("display_metric", result.get("rank_by", "amount"))
    if result.get("analysis_type") == "summary" or plan.analysis_type == "summary":
        _render_summary_success_content(run)
        return
    scope = result.get("filter_scope") or {}
    if scope.get("message"):
        st.warning(f"{filter_scope_summary(scope)} {scope['message']}", title="筛选范围无记录")
    if len(plan.dimensions) > 1:
        dimension = st.radio("榜单维度", plan.dimensions,
                             format_func=lambda value: "国家" if value == "country" else "商品",
                             horizontal=True, key=f"dimension_{run['id']}", label_visibility="collapsed")
    else:
        dimension = plan.dimensions[0] if plan.dimensions else None
    directions = ["growth", "decline"] if plan.direction == "both" else [plan.direction]
    directions = [value for value in directions if value in {"growth", "decline"}]
    metric_result = result.get("metric_results", {}).get(metric, result)
    if dimension and len(directions) == 2:
        columns = st.columns(2, gap="small")
        for column, direction in zip(columns, directions):
            with column:
                _render_board(metric_result, dimension, direction, plan.top_n, metric)
    elif dimension and directions:
        _render_board(metric_result, dimension, directions[0], plan.top_n, metric)
    st.image(_metric_chart_bytes(result, plan, metric), width="stretch", alt="所选指标的本次分析图表")
    st.markdown('<div class="section-title">报告结论</div>', unsafe_allow_html=True)
    overview, groups = conclusion_groups(state["final_report"], state["evidence"], plan.dimensions, plan.direction)
    if overview:
        with st.container(border=True):
            st.markdown("**总览**")
            st.write(overview)
    for _, _, title, conclusions in groups:
        with st.container(border=True):
            st.markdown(f"**{title}**")
            for conclusion in conclusions:
                st.write(conclusion.text)
                st.markdown(" · ".join(f"`{escape(value)}`" for value in conclusion.evidence_ids))
    _render_evidence_table(state["evidence"])
    _render_downloads(run)


def _render_summary_success_content(run: dict) -> None:
    state, plan, result = run["state"], run["state"]["plan"], run["state"]["result"]
    if result.get("filter_scope", {}).get("message"):
        st.warning(result["filter_scope"]["message"], title="所选范围内无记录")
    metric = run.get("display_metric", result.get("rank_by", "amount"))
    st.image(_metric_chart_bytes(result, plan, metric), width="stretch", alt="所选指标的分组结果图表")
    st.markdown('<div class="section-title">报告结论</div>', unsafe_allow_html=True)
    for conclusion in state["final_report"].conclusions:
        with st.container(border=True):
            st.write(conclusion.text)
            st.markdown(" · ".join(f"`{escape(value)}`" for value in conclusion.evidence_ids))
    _render_evidence_table(state["evidence"])
    _render_downloads(run)


def _render_evidence_table(evidence: dict) -> None:
    with st.expander("展开本次计算证据"):
        rows = []
        for identifier, item in evidence.items():
            value = item.get("value")
            if item.get("unit") == "GBP" and isinstance(value, (int, float)):
                shown = format_signed_money(value) if identifier.endswith(".delta") else format_money(value)
            elif item.get("unit") == "percent":
                shown = format_signed_percent(value / 100)
            elif item.get("unit") == "items" and isinstance(value, (int, float)):
                shown = f"{value:,.0f} 件"
            else:
                shown = str(value)
            rows.append({"证据编号": identifier, "指标": item.get("label"), "计算值": shown})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_downloads(run: dict) -> None:
    report_path, image_path = Path(run["report_path"]), Path(run["image_path"])
    buttons = st.columns(2, gap="small")
    buttons[0].download_button("下载报告 Markdown", report_path.read_bytes(), file_name="report.md",
                               mime="text/markdown", key=f"download_report_{run['id']}", width="stretch")
    result = run.get("state", {}).get("result", {})
    metric = result.get("rank_by", "amount")
    image_bytes = image_path.read_bytes()
    buttons[1].download_button(f"下载图表 PNG（排名依据：{'金额' if metric == 'amount' else '销量'}）",
                               image_bytes, file_name="period_comparison.png", mime="image/png",
                               key=f"download_chart_{run['id']}", width="stretch")
    with st.expander("导出详情"):
        st.code(f"Markdown: {report_path}\nPNG: {image_path}", language=None, wrap_lines=True)


should_run = False

st.markdown(
    '<header class="masthead"><div class="eyebrow">ONLINE RETAIL · LOCAL WORKFLOW</div>'
    '<h1>零售观察站</h1><p>零售数据分析 · 证据审核 · 报告导出</p></header>',
    unsafe_allow_html=True,
)

previous_run = st.session_state.run_history[-1] if st.session_state.run_history else None
with st.container(border=True):
    example_cols = st.columns([1, 1, 1, 4])
    if example_cols[0].button("填入案例 A", width="stretch"):
        st.session_state.question_input = EXAMPLE_A
        st.session_state.expand_analysis_input = True
        st.rerun()
    if example_cols[1].button("填入案例 B", width="stretch"):
        st.session_state.question_input = EXAMPLE_B
        st.session_state.expand_analysis_input = True
        st.rerun()
    if example_cols[2].button("开始新分析", key="start_new_analysis", width="stretch"):
        st.session_state.run_history = []
        st.session_state.active_run_id = None
        st.session_state.conversation_context = {
            "data_identity": st.session_state.conversation_context.get("data_identity"),
            "pending": None,
            "last_success": None,
            "reference_catalog": None,
        }
        st.session_state.question_input = ""
        st.session_state.expand_analysis_input = True
        st.rerun()

    question_label = "输入新问题" if previous_run and previous_run["status"] == "approved" else "分析问题"
    input_expanded = st.session_state.get("expand_analysis_input", not previous_run or previous_run["status"] != "approved")
    submitted = False
    question = st.session_state.question_input
    source = "演示 CSV"
    upload = None
    with st.expander(question_label, expanded=input_expanded):
        input_cols = st.columns([2.6, 1], gap="medium")
        with input_cols[0]:
            question = st.text_area(
                "输入中文分析问题",
                key="question_input",
                placeholder="输入中文问题，说明两个日期区间和要查看的涨跌榜。",
                height=70,
                label_visibility="collapsed",
            )
        with input_cols[1]:
            source = st.radio("数据来源", ["演示 CSV", "上传 CSV"], horizontal=True, key="data_source")
            upload = None
            if source == "上传 CSV":
                upload = st.file_uploader("选择 CSV", type=["csv"], label_visibility="collapsed")
            submitted = st.button("运行分析", type="primary", width="stretch")

upload_bytes = upload.getvalue() if upload is not None else b""
source_name = upload.name if source == "上传 CSV" and upload is not None else (
    "" if source == "上传 CSV" else DEFAULT_CSV.name
)
source_bytes = upload_bytes if source == "上传 CSV" else DEFAULT_CSV.read_bytes()
data_identity = source_identity("upload" if source == "上传 CSV" else "demo", source_name, source_bytes)
context, source_changed = bind_context_to_source(st.session_state.conversation_context, data_identity)
st.session_state.conversation_context = context
if source_changed:
    st.session_state.run_history = []
    st.session_state.active_run_id = None
    previous_run = None

current_run = previous_run
input_error = None
if submitted:
    if not question.strip():
        input_error = "请先输入分析问题。"
    elif source == "上传 CSV" and upload is None:
        input_error = "请选择一份 CSV 文件。"
    else:
        question = question.strip()
        decision = classify_user_turn(question, context, data_identity)
        plan_override = None
        merge_previous_plan = None
        root_question = question
        turn_type = decision.kind
        effective_request = question
        planner = plan_question
        reference_provenance = None
        pending_drill_restore = None
        drill_planner = None
        if decision.kind == "modify_previous":
            last_success = context["last_success"]
            previous_plan = AnalysisPlan.model_validate(last_success["plan"])
            modified_plan = apply_plan_changes(previous_plan, decision.changes)
            root_question = last_success.get("question", last_success.get("analysis_request", question))
            actual_range = last_success.get("actual_date_range")
            effective_request = (
                f"本次有效分析要求：{summarize_plan_conditions(modified_plan, actual_range)}。"
            )
            plan_override = modified_plan.model_dump(mode="json")
        elif decision.kind == "answer_clarification":
            pending = context.get("pending") or {}
            root_question = pending.get("original_question", question)
            prior_plan = pending.get("plan")
            if prior_plan:
                previous_plan = AnalysisPlan.model_validate(prior_plan)
                merge_previous_plan = previous_plan.model_dump(mode="json")
                effective_request = (
                    f"本次有效分析要求：{summarize_plan_conditions(previous_plan, pending.get('actual_date_range'))}。\n"
                    f"用户补充的比较日期：{question}\n"
                    "保持以上分析任务、指标、方向、筛选和榜单条数不变，只补齐缺少的日期。"
                )
            else:
                effective_request = (
                    f"用户此前已经确定的条件：{pending.get('confirmed_conditions', '无')}。\n"
                    f"用户本轮补充：{question}\n请据此生成完整分析计划。"
                )
        elif decision.kind == "reference_followup":
            last_success = context.get("last_success") or {}
            reference_catalog = last_success.get("reference_catalog") or {}
            source_plan = AnalysisPlan.model_validate(last_success["plan"])
            actual_range = last_success.get("actual_date_range")
            planner_input = (
                "用户本轮真实问题（只根据这句话生成新的操作计划）：\n"
                f"{question}\n\n"
                "这是同一份 CSV 会话中的下钻追问，引用来源是最近一次审核通过的榜单。"
                "只规划本轮新操作；本轮未重述的相关日期和筛选由程序继承，不要追问已存在的范围。\n"
                "必须根据本轮用词设置 reference_intent 以标记被引用的榜单类别；不要猜实体，实体由本地程序解析。\n"
                "可用类别：product_leader、country_leader、leader、product_decline_leader、"
                "country_decline_leader、decline_leader、product_growth_leader、"
                "country_growth_leader、growth_leader。\n"
                "如果用户问每周件数，应计划 summary、group_by week、metrics quantity、rank_by quantity；本轮没有排名要求时不要设置排名。\n"
                "上轮可引用榜单类别概要（用于理解‘榜首商品’等指代，不要重复生成旧榜单计划）："
                + json.dumps([
                    {key: board.get(key) for key in ("dimension", "metric", "kind", "direction", "period_label")}
                    for board in reference_catalog.get("boards", [])
                ], ensure_ascii=False)
            )

            def drill_planner(_request, path):
                parsed = plan_question(planner_input, path)
                resolution = resolve_reference(reference_catalog, parsed.reference_intent)
                current_run["reference_resolution"] = resolution
                if resolution["status"] == "clarification":
                    current_run["reference_provenance"] = "引用上轮榜单候选，等待确认"
                    if resolution["candidates"]:
                        current_run["reference_pending_drill"] = {
                            "kind": "drill_choice",
                            "original_question": question,
                            "source_plan": serialize_plan_for_context(source_plan),
                            "source_actual_date_range": actual_range,
                            "drill_plan": serialize_plan_for_context(parsed),
                            "candidates": resolution["candidates"],
                            "clarification_question": resolution["question"],
                        }
                        resolved_request = f"用户下钻问题：{question}；上轮榜单有多个候选，等待用户选择。"
                    else:
                        current_run["reference_pending_drill"] = {
                            "kind": "missing_analysis", "original_question": question,
                            "confirmed_conditions": {"reference_question": question},
                            "missing": resolution["question"],
                        }
                        resolved_request = f"用户问题：{question}；缺少可解析的引用榜单类别，需要用户明确商品或国家。"
                    drill_planner.resolved_analysis_request = resolved_request
                    return parsed.model_copy(update={
                        "status": "clarification",
                        "clarification_question": resolution["question"],
                        "unsupported_reason": None,
                    })
                candidate = resolution["candidate"]
                merged = merge_drilldown_plan(source_plan, parsed, candidate, question)
                source_label = reference_source_label(candidate, source_plan)
                current_run["reference_candidate"] = candidate
                current_run["reference_provenance"] = source_label
                source_evidence = candidate.get("board_evidence_ids") or [candidate["board_evidence_id"]]
                resolved_request = (
                    f"本次有效分析要求：{summarize_plan_conditions(merged, actual_range)}。"
                    f"引用上轮榜单证据 {', '.join(source_evidence)}，对象 {candidate['entity_key']}。"
                    f"下钻原始问题：{question}"
                )
                drill_planner.resolved_analysis_request = resolved_request
                return merged

            planner = drill_planner
            effective_request = question
            root_question = question
        elif decision.kind == "answer_drill_choice":
            pending_drill = context.get("pending") or {}
            pending_drill_restore = pending_drill
            candidate = decision.changes["candidate"]
            source_plan = AnalysisPlan.model_validate(pending_drill["source_plan"])
            drill_plan = AnalysisPlan.model_validate(pending_drill["drill_plan"])
            modified_plan = merge_drilldown_plan(
                source_plan, drill_plan, candidate, pending_drill["original_question"],
            )
            root_question = pending_drill["original_question"]
            plan_override = modified_plan.model_dump(mode="json")
            reference_provenance = reference_source_label(candidate, source_plan)
            source_evidence = candidate.get("board_evidence_ids") or [candidate["board_evidence_id"]]
            effective_request = (
                f"本次有效分析要求：{summarize_plan_conditions(modified_plan, pending_drill.get('source_actual_date_range'))}。"
                f"引用上轮榜单证据 {', '.join(source_evidence)}，对象 {candidate['entity_key']}。"
                f"下钻原始问题：{root_question}"
            )
        elif decision.kind == "ambiguous":
            clarification = decision.clarification or "请说明要分析的内容。"
            trace = initial_trace()
            skip_pending_trace(trace, clarification)
            conditions = "；".join(decision.changes.get("country_filters", []))
            if conditions:
                conditions = "国家：" + conditions + "；仍需明确分析内容"
            else:
                conditions = "尚未确定分析条件。"
            current_run = {
                "id": uuid.uuid4().hex[:10], "question": question,
                "source_name": source_name or DEFAULT_CSV.name,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "status": "clarification", "clarification_kind": "missing_analysis",
                "clarification_question": clarification,
                "conditions": conditions, "trace": trace, "review_records": [],
                "state": None, "error": None, "effective_request": question,
                "turn_type": "ambiguous", "data_identity": data_identity,
            }
            existing_pending = context.get("pending") or {}
            if existing_pending.get("kind") != "drill_choice":
                context["pending"] = {
                    "kind": "missing_analysis", "original_question": question,
                    "confirmed_conditions": decision.changes or {}, "missing": clarification,
                }
                current_run["clarification_kind"] = "missing_analysis"
            else:
                current_run["clarification_kind"] = "drill_choice"
                current_run["reference_provenance"] = existing_pending.get("reference_provenance")
                current_run["conditions"] = "候选对象尚未确定。"
            st.session_state.run_history.append(current_run)
            st.session_state.active_run_id = current_run["id"]
            st.session_state.expand_analysis_input = True
        else:
            context["pending"] = None

        if decision.kind != "ambiguous":
            current_run = {
                "id": uuid.uuid4().hex[:10],
                "question": question,
                "source_name": source_name or DEFAULT_CSV.name,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "status": "running",
                "trace": initial_trace(),
                "review_records": [],
                "state": None,
                "error": None,
                "conditions": summarize_plan_conditions(modified_plan) if plan_override else "由规划智能体确认本次条件。",
                "effective_request": effective_request,
                "turn_type": turn_type,
                "root_question": root_question,
                "merge_previous_plan": merge_previous_plan,
                "plan_override": plan_override,
                "planner": planner,
                "reference_provenance": reference_provenance,
                "pending_drill_restore": pending_drill_restore,
                "data_identity": data_identity,
            }
            st.session_state.run_history.append(current_run)
            st.session_state.active_run_id = current_run["id"]
            st.session_state.expand_analysis_input = False
            should_run = True

if input_error:
    st.error(input_error)

if current_run:
    if current_run["status"] == "approved":
        _render_success_top(current_run)
    else:
        trace_container, status_slot = _render_non_approved_run(current_run)

    if should_run:
        status_slot.markdown(
            f'<div class="run-status status-running">运行中 · 已完成 {len(current_run["trace"])} 个节点</div>',
            unsafe_allow_html=True,
        )
        try:
            if source == "演示 CSV":
                csv_path = DEFAULT_CSV
                validate_csv(csv_path)
                run_csv = csv_path
            else:
                temp_dir = tempfile.TemporaryDirectory(prefix="retail_agent_upload_")
                csv_path = Path(temp_dir.name) / "uploaded.csv"
                csv_path.write_bytes(upload_bytes)
                validate_csv(csv_path)
                run_csv = csv_path

            if current_run.get("plan_override"):
                override = AnalysisPlan.model_validate(current_run["plan_override"])
                planner = lambda *_: override
            elif current_run.get("merge_previous_plan"):
                old_plan = AnalysisPlan.model_validate(current_run["merge_previous_plan"])

                def planner(request, path):
                    parsed = plan_question(request, path)
                    return merge_clarified_dates(old_plan, parsed)
            else:
                planner = current_run.get("planner", plan_question)

            state = run_workflow(
                run_csv,
                current_run["question"],
                analysis_request=current_run["effective_request"],
                latest_input=current_run["question"],
                planner=planner,
                on_update=lambda node, update: _record_update(
                    current_run, node, update, trace_container, status_slot
                ),
                on_event=lambda event: _record_workflow_event(
                    current_run, event, trace_container, status_slot
                ),
            )
            _finish_run(current_run, state)

            if current_run["status"] == "clarification" and current_run.get("reference_pending_drill"):
                pending_drill = current_run["reference_pending_drill"]
                context["pending"] = {
                    **pending_drill,
                    "data_identity": data_identity,
                    "reference_provenance": current_run.get("reference_provenance"),
                }
            elif current_run["status"] == "clarification":
                prior_pending = context.get("pending") or {}
                plan = state.get("plan")
                context["pending"] = {
                    "kind": "clarification",
                    "original_question": current_run.get("root_question") or prior_pending.get("original_question") or current_run["question"],
                    "analysis_request": current_run["effective_request"],
                    "plan": serialize_plan_for_context(plan) if plan else None,
                    "actual_date_range": state.get("result", {}).get("date_range") if state.get("result") else (
                        {"start": plan.start_date.isoformat(), "end": plan.end_date.isoformat()}
                        if plan and plan.time_scope == "full_data" and plan.start_date and plan.end_date else None
                    ),
                    "confirmed_conditions": summarize_plan_conditions(plan) if plan else {},
                    "missing": current_run.get("clarification_question") or state.get("clarification_question"),
                }
            elif current_run["status"] == "approved":
                context["pending"] = None
                context["last_success"] = {
                    "question": current_run.get("root_question") or current_run["question"],
                    "analysis_request": current_run["effective_request"],
                    "plan": serialize_plan_for_context(state["plan"]),
                    "actual_date_range": state.get("result", {}).get("date_range"),
                    "conditions": current_run["conditions"],
                    "data_identity": data_identity,
                    "reference_catalog": build_reference_catalog(state["plan"], state["result"]),
                }
                context["reference_catalog"] = context["last_success"]["reference_catalog"]
            elif current_run["status"] == "clarification":
                context["pending"] = None
            else:
                context["pending"] = current_run.get("pending_drill_restore")
        except (ModelConfigurationError, CsvValidationError, ValueError, OSError, RuntimeError) as exc:
            current_run["status"] = "failed"
            current_run["error"] = str(exc)
            if not any(item.get("status") == "failed" for item in current_run["trace"]):
                active = next((item for item in reversed(current_run["trace"])
                               if item.get("status") == "running"), None)
                if active:
                    fail_trace_event(current_run["trace"], active["node"], active.get("attempt", 1), str(exc))
                else:
                    skip_pending_trace(current_run["trace"], "输入或配置校验失败")
            _draw_trace(current_run["trace"], trace_container)
            status_slot.markdown(
                f'<div class="run-status status-failed">{escape(run_status_label(current_run))}：{escape(str(exc))}</div>',
                unsafe_allow_html=True,
            )
        st.session_state.expand_analysis_input = current_run["status"] != "approved"
        st.session_state.run_history[-1] = current_run
        st.rerun()
else:
    st.markdown('<div class="panel"><span class="small-note">提交问题后显示分析结果和实际执行轨迹。</span></div>', unsafe_allow_html=True)
