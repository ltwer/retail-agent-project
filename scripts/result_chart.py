"""Shared chart rendering for the Streamlit result page and report exports."""

from __future__ import annotations

from pathlib import Path
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager


GREEN = "#2F6F55"
MINT = "#9BC9A6"
INK = "#173D32"
PAPER = "#F7F8F2"


def summary_chart_payload(plan, result: dict, metric: str | None = None) -> dict:
    group_fields = result.get("group_by_fields", plan.group_by)
    group_by = result.get("group_by")
    metric = metric or result.get("rank_by", getattr(plan, "rank_by", "amount"))
    groups = list(result.get("groups", []))
    metric_rankings = [ranking for ranking in result.get("rankings", []) if ranking.get("metric") == metric]
    if len(group_fields) == 2 and metric_rankings:
        labels, values = [], []
        for ranking in metric_rankings:
            leaders = ranking.get("leaders", [])
            for leader in leaders:
                labels.append(f"{ranking['period_label']} · {leader['label']}")
                values.append(float(leader["value"]))
        kind = "ranking"
    elif len(group_fields) == 2:
        # The complete time × business table remains in the result. A chart of
        # every cell is unreadable for large datasets, so the alternate metric
        # shows one local aggregate per time bucket.
        per_period: dict[str, dict] = {}
        for row in groups:
            key = str(row["period_key"])
            bucket = per_period.setdefault(key, {"label": str(row["period_label"]), "value": 0.0})
            bucket["value"] += float(row.get("values", {}).get(metric, 0.0))
        labels = [per_period[key]["label"] for key in sorted(per_period)]
        values = [per_period[key]["value"] for key in sorted(per_period)]
        kind = "trend"
    elif group_by in {"day", "week", "month"}:
        # A ranked time table keeps its requested order; its trend chart stays chronological.
        groups.sort(key=lambda row: row.get("natural_start") or row.get("start"))
        labels = [str(row["label"]) for row in groups]
        kind = "trend"
    elif group_by == "product":
        if getattr(plan, "ranking_order", None):
            groups.sort(
                key=lambda row: row.get("values", {}).get(metric, row.get(metric, 0.0)),
                reverse=plan.ranking_order == "desc",
            )
        labels = [str(row["key"]) for row in groups]
        kind = "ranking"
    elif group_by == "country":
        if getattr(plan, "ranking_order", None):
            groups.sort(
                key=lambda row: row.get("values", {}).get(metric, row.get(metric, 0.0)),
                reverse=plan.ranking_order == "desc",
            )
        labels = [str(row["key"]) for row in groups]
        kind = "ranking"
    else:
        labels = ["正向交易总额" if metric == "amount" else "有效销售件数"] if groups else []
        kind = "total"
    if not (len(group_fields) == 2):
        values = [float(row.get("values", {}).get(metric, row.get(metric, 0.0))) for row in groups]
    unit = "英镑" if metric == "amount" else "件"
    metric_name = "正向交易金额" if metric == "amount" else "有效销售件数"
    return {
        "kind": kind,
        "group_by": group_by,
        "group_by_fields": group_fields,
        "metric": metric,
        "metric_name": metric_name,
        "unit": unit,
        "labels": labels,
        "full_labels": [str(row["label"]) for row in groups],
        "values": values,
    }


def _chart_font():
    font_path = Path("C:/Windows/Fonts/Deng.ttf")
    if font_path.is_file():
        font_manager.fontManager.addfont(str(font_path))
        font = font_manager.FontProperties(fname=str(font_path))
        plt.rcParams["font.family"] = font.get_name()
        return font
    return None


def _annotate_horizontal_bars(ax, bars, values, font):
    max_value = max(values, default=0)
    ax.set_xlim(0, max_value * 1.28 if max_value > 0 else 1)
    for bar, value in zip(bars, values):
        ax.annotate(
            f"£{value:,.2f}" if getattr(ax, "_retail_metric", "amount") == "amount" else f"{value:,.0f} 件",
            (bar.get_width(), bar.get_y() + bar.get_height() / 2),
            xytext=(6, 0), textcoords="offset points", ha="left", va="center",
            fontsize=9, fontweight="bold", color=INK, fontproperties=font,
        )


def render_result_chart(result: dict, plan, path: str | Path, metric: str | None = None) -> None:
    """Render directly from the already-filtered workflow result; never recalculate."""
    font = _chart_font()
    if result.get("analysis_type") == "summary" or plan.analysis_type == "summary":
        payload = summary_chart_payload(plan, result, metric)
        labels = payload["labels"]
        values = payload["values"]
        group_by = payload["group_by"]
        if payload["kind"] in {"ranking", "composition"}:
            row_count = max(1, len(labels))
            fig_height = max(3.4, 0.52 * row_count + 1.1)
            fig, ax = plt.subplots(figsize=(9.2, fig_height), constrained_layout=True)
            ax._retail_metric = payload["metric"]
            bars = ax.barh(labels[::-1], values[::-1], color=GREEN, height=0.66)
            _annotate_horizontal_bars(ax, bars, values[::-1], font)
            for tick in [*ax.get_yticklabels(), *ax.get_xticklabels()]:
                if font:
                    tick.set_fontproperties(font)
            titles = {"product": "商品", "country": "国家", "week": "周", "month": "月份", "day": "日期"}
            title = " · ".join(titles.get(value, value) for value in payload["group_by_fields"])
            ax.set_title(f"{title or '分组'}{payload['metric_name']}", fontsize=13, color=INK, pad=10, fontproperties=font)
            ax.set_xlabel(f"{payload['metric_name']}（{payload['unit']}）", fontsize=9, color=INK, fontproperties=font)
            ax.grid(axis="x", linestyle="--", alpha=0.25)
            ax.set_axisbelow(True)
        elif payload["kind"] == "trend":
            fig_height = max(3.2, min(5.2, 2.6 + len(labels) * 0.16))
            fig, ax = plt.subplots(figsize=(9.2, fig_height), constrained_layout=True)
            positions = list(range(len(labels)))
            ax.plot(positions, values, color=GREEN, marker="o", linewidth=2.2, markersize=6)
            ax.fill_between(positions, values, color=MINT, alpha=0.22)
            ax.set_xticks(positions, labels)
            for tick in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
                if font:
                    tick.set_fontproperties(font)
            ax.set_title(f"按时间分组的{payload['metric_name']}趋势", fontsize=13, color=INK, pad=10, fontproperties=font)
            ax.set_ylabel(f"{payload['metric_name']}（{payload['unit']}）", fontsize=9, color=INK, fontproperties=font)
            ax.grid(axis="y", linestyle="--", alpha=0.25)
            ax.set_axisbelow(True)
            if len(labels) > 6:
                plt.setp(ax.get_xticklabels(), rotation=35, ha="right")
            top = max(values, default=0)
            ax.set_ylim(0, top * 1.2 if top > 0 else 1)
            for x, value in zip(positions, values):
                label = f"£{value:,.2f}" if payload["metric"] == "amount" else f"{value:,.0f} 件"
                ax.annotate(label, (x, value), xytext=(0, 7), textcoords="offset points",
                            ha="center", fontsize=8, color=INK, fontproperties=font)
        else:
            fig, ax = plt.subplots(figsize=(8.6, 3.6), constrained_layout=True)
            value = values[0] if values else 0
            label = labels[0] if labels else "无记录"
            bar = ax.bar([label], [value], color=GREEN, width=0.55)
            ax.set_title(payload["metric_name"], fontsize=13, color=INK, pad=10, fontproperties=font)
            ax.set_ylabel(f"{payload['metric_name']}（{payload['unit']}）", fontsize=9, color=INK, fontproperties=font)
            ax.set_ylim(0, value * 1.2 if value > 0 else 1)
            if value:
                label = f"£{value:,.2f}" if payload["metric"] == "amount" else f"{value:,.0f} 件"
                ax.annotate(label, (bar[0].get_x() + bar[0].get_width()/2, value),
                            xytext=(0, 7), textcoords="offset points", ha="center", fontproperties=font)
        ax.spines[["top", "right"]].set_visible(False)
        fig.savefig(path, dpi=150, facecolor=PAPER)
        plt.close(fig)
        return

    metric = metric or result.get("rank_by", getattr(plan, "rank_by", "amount"))
    metric_result = (result.get("metric_results") or {}).get(metric, result)
    plan_start_1 = plan.period1_start.isoformat()
    plan_end_1 = plan.period1_end.isoformat()
    plan_start_2 = plan.period2_start.isoformat()
    plan_end_2 = plan.period2_end.isoformat()
    labels = [f"前期\n{plan_start_1} 至\n{plan_end_1}", f"后期\n{plan_start_2} 至\n{plan_end_2}"]
    values = [float(metric_result["period1_total"]), float(metric_result["period2_total"])]
    fig, ax = plt.subplots(figsize=(8, 3.65), constrained_layout=True)
    bars = ax.bar(labels, values, color=[GREEN, MINT], width=0.54)
    metric_name = "正向交易金额" if metric == "amount" else "有效销售件数"
    unit = "GBP" if metric == "amount" else "件"
    ax.set_title(f"两期{metric_name}对比", fontsize=13, color=INK, pad=9, fontproperties=font)
    ax.set_ylabel(f"{metric_name}（{unit}）", fontsize=9, color=INK, fontproperties=font)
    for tick in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
        if font:
            tick.set_fontproperties(font)
    ax.grid(axis="y", linestyle="--", alpha=0.28)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ymax = max(values)
    ax.set_ylim(0, ymax * 1.18 if ymax > 0 else 1)
    for bar, value in zip(bars, values):
        value_label = f"£{value:,.2f}" if metric == "amount" else f"{value:,.0f} 件"
        ax.annotate(value_label, (bar.get_x() + bar.get_width()/2, bar.get_height()),
                    xytext=(0, 7), textcoords="offset points", ha="center", va="bottom",
                    fontsize=9, fontweight="bold", fontproperties=font)
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message="Glyph .* missing from font")
        fig.savefig(path, dpi=160, facecolor="white")
    plt.close(fig)
