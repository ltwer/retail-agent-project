from datetime import date
from pathlib import Path
import re
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from planning_agent import AnalysisPlan


REQUIRED_COLUMNS = {
    "InvoiceNo",
    "InvoiceDate",
    "StockCode",
    "Description",
    "Quantity",
    "UnitPrice",
    "Country",
}

# These stock codes describe postage, carriage, or payment fees rather than goods.
NON_PRODUCT_FEES = {
    "POST": "Postage",
    "DOT": "Dotcom postage",
    "C2": "Carriage",
    "BANK CHARGES": "Bank charges",
    "AMAZONFEE": "Amazon fee",
}

COUNTRY_ALIASES = {
    "uk": "United Kingdom",
    "gb": "United Kingdom",
    "de": "Germany",
    "nl": "Netherlands",
    "fr": "France",
    "great britain": "United Kingdom",
    "britain": "United Kingdom",
    "england": "United Kingdom",
    "英国": "United Kingdom",
    "荷兰": "Netherlands",
    "德国": "Germany",
    "法国": "France",
    "爱尔兰": "EIRE",
    "ireland": "EIRE",
}


class FilterResolutionError(ValueError):
    """A user supplied entity filter could not be resolved without guessing."""


def _as_date(value: str | date) -> date:
    return pd.Timestamp(value).date()


def _load_positive_transactions(csv_path: str | Path) -> pd.DataFrame:
    data = pd.read_csv(
        csv_path,
        dtype={"InvoiceNo": "string", "StockCode": "string", "Country": "string", "Description": "string"},
    )
    missing = REQUIRED_COLUMNS - set(data.columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")
    data["InvoiceDate"] = pd.to_datetime(data["InvoiceDate"], errors="coerce")
    data["Quantity"] = pd.to_numeric(data["Quantity"], errors="raise")
    data["UnitPrice"] = pd.to_numeric(data["UnitPrice"], errors="raise")
    data = data[
        data["InvoiceDate"].notna()
        & ~data["InvoiceNo"].str.startswith("C", na=False)
        & (data["Quantity"] > 0)
        & (data["UnitPrice"] > 0)
    ].copy()
    data["Amount"] = data["Quantity"] * data["UnitPrice"]
    data["Date"] = data["InvoiceDate"].dt.date
    return data


def _normalize_entity(value: object) -> str:
    return " ".join(str(value).strip().casefold().split())


def resolve_filter_scope(
    csv_path: str | Path,
    country_filters: list[str] | None = None,
    product_filters: list[str] | None = None,
) -> dict:
    """Resolve user-facing filter values against the complete local CSV."""
    countries_requested = list(dict.fromkeys(value.strip() for value in (country_filters or []) if value.strip()))
    products_requested = list(dict.fromkeys(value.strip() for value in (product_filters or []) if value.strip()))
    entities = pd.read_csv(
        csv_path,
        dtype={"Country": "string", "StockCode": "string", "Description": "string"},
        usecols=["Country", "StockCode", "Description"],
    )
    country_values = sorted(entities["Country"].dropna().unique().tolist())
    country_by_normalized = {_normalize_entity(value): value for value in country_values}
    resolved_countries: list[str] = []
    for requested in countries_requested:
        alias = COUNTRY_ALIASES.get(_normalize_entity(requested))
        match = alias if alias in country_values else country_by_normalized.get(_normalize_entity(requested))
        if match is None:
            raise FilterResolutionError(
                f"国家条件“{requested}”未在当前 CSV 的 Country 列中匹配到；请确认国家名称或别名。"
            )
        if match not in resolved_countries:
            resolved_countries.append(match)

    product_values = entities.dropna(subset=["StockCode"]).drop_duplicates("StockCode")
    code_values = product_values["StockCode"].tolist()
    codes_exact = {str(code).strip(): str(code).strip() for code in code_values}
    descriptions: dict[str, set[str]] = {}
    for row in entities.dropna(subset=["StockCode", "Description"])[["StockCode", "Description"]].itertuples(index=False):
        descriptions.setdefault(_normalize_entity(row.Description), set()).add(row.StockCode)

    resolved_products: list[str] = []
    product_labels: dict[str, str] = {}
    for requested in products_requested:
        normalized = _normalize_entity(requested)
        code = codes_exact.get(requested)
        matches = {code} if code is not None else descriptions.get(normalized, set())
        if not matches and code is None:
            # Permit a distinctive partial description, but never guess between codes.
            matches = {
                row.StockCode
                for row in entities.dropna(subset=["StockCode", "Description"])[["StockCode", "Description"]].itertuples(index=False)
                if normalized in _normalize_entity(row.Description)
            }
        if not matches:
            raise FilterResolutionError(
                f"商品条件“{requested}”未匹配到 StockCode 或 Description；请检查商品编号/描述。"
            )
        if len(matches) > 1:
            candidates = sorted(matches)
            raise FilterResolutionError(
                f"商品描述“{requested}”对应多个商品编号：{', '.join(candidates[:10])}；请确认要筛选的编号。"
            )
        resolved = next(iter(matches))
        if resolved not in resolved_products:
            resolved_products.append(resolved)
            if code is None:
                product_labels[resolved] = requested

    return {
        "requested_countries": countries_requested,
        "countries": resolved_countries,
        "requested_products": products_requested,
        "products": resolved_products,
        "product_labels": product_labels,
    }


def validate_explicit_filter_extraction(
    question: str,
    csv_path: str | Path,
    filter_scope: dict,
) -> None:
    """Prevent an explicit country/code in the question from becoming an all-data run."""
    question_folded = question.casefold()
    selected_countries = set(filter_scope.get("countries", []))
    country_values = pd.read_csv(
        csv_path,
        dtype={"Country": "string"},
        usecols=["Country"],
    )["Country"].dropna().unique()
    for country in country_values:
        aliases = [country]
        aliases.extend(alias for alias, canonical in COUNTRY_ALIASES.items() if canonical == country)
        mentioned = any(
            alias.casefold() in question_folded
            if any(ord(char) > 127 for char in alias)
            else re.search(rf"(?<![A-Za-z]){re.escape(alias.casefold())}(?![A-Za-z])", question_folded)
            for alias in aliases
        )
        if mentioned and country not in selected_countries:
            raise FilterResolutionError(
                f"检测到问题中明确提到国家“{country}”，但规划结果没有包含该国家筛选；请重新表述或确认是否要筛选该国家。"
            )

    selected_products = set(filter_scope.get("products", []))
    entities = pd.read_csv(
        csv_path,
        dtype={"StockCode": "string", "Description": "string"},
        usecols=["StockCode", "Description"],
    )
    mentioned_codes = {
        code for code in entities["StockCode"].dropna().unique()
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(code.casefold())}(?![A-Za-z0-9])", question_folded)
    }
    omitted_codes = sorted(mentioned_codes - selected_products)
    if omitted_codes:
        raise FilterResolutionError(
            "检测到问题中明确提到商品编号“"
            + "、".join(omitted_codes)
            + "”，但规划结果没有包含该商品筛选；请确认是否要筛选该编号。"
        )

    normalized_question = re.sub(r"[^a-z0-9]+", " ", question_folded).strip()
    mentioned_descriptions = set()
    for row in entities.dropna(subset=["StockCode", "Description"]).itertuples(index=False):
        description = re.sub(r"[^a-z0-9]+", " ", row.Description.casefold()).strip()
        if len(description) >= 12 and description in normalized_question:
            mentioned_descriptions.add(row.StockCode)
    omitted_descriptions = sorted(mentioned_descriptions - selected_products)
    if omitted_descriptions:
        raise FilterResolutionError(
            "检测到问题中明确提到商品描述对应编号“"
            + "、".join(omitted_descriptions)
            + "”，但规划结果没有包含该商品筛选；请确认是否要筛选该商品。"
        )


def _apply_entity_filters(data: pd.DataFrame, filter_scope: dict | None) -> pd.DataFrame:
    if not filter_scope:
        return data.copy()
    filtered = data
    countries = filter_scope.get("countries", [])
    products = filter_scope.get("products", [])
    if countries:
        filtered = filtered[filtered["Country"].isin(countries)]
    if products:
        filtered = filtered[filtered["StockCode"].isin(products)]
    return filtered.copy()


def _date_range_frame(data: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    return data[data["Date"].between(start, end)].copy()


def _period_bounds(group_by: str, value: object) -> tuple[date, date, str]:
    timestamp = pd.Timestamp(value)
    if group_by == "day":
        start = end = timestamp.date()
        key = start.isoformat()
    elif group_by == "week":
        start = (timestamp - pd.Timedelta(days=timestamp.weekday())).date()
        end = start + pd.Timedelta(days=6)
        key = f"{start.isoformat()}_{end.isoformat()}"
    elif group_by == "month":
        start = timestamp.to_period("M").start_time.date()
        end = timestamp.to_period("M").end_time.date()
        key = timestamp.strftime("%Y-%m")
    else:
        start = end = timestamp.date()
        key = str(value)
    return start, end, key


def _metric_unit(metric: str) -> str:
    return "GBP" if metric == "amount" else "items"


def _metric_name(metric: str) -> str:
    return "正向交易金额" if metric == "amount" else "有效销售件数"


def _summary_group_token(row: dict, group_by: list[str]) -> str:
    def token(value: object) -> str:
        return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"

    if len(group_by) == 2:
        parts = (row.get("period_key", ""), row.get("business_key", ""))
        time_dimension = next((item for item in group_by if item in {"day", "week", "month"}), group_by[0])
        business_dimension = next((item for item in group_by if item in {"country", "product"}), group_by[1])
        return f"{time_dimension}.{token(parts[0])}.{business_dimension}.{token(parts[1])}"
    else:
        dimension = group_by[0] if group_by else "total"
        return f"{dimension}.{token(row.get('key', 'total'))}"


def _summary_evidence(result: dict, plan: "AnalysisPlan") -> dict[str, dict]:
    evidence: dict[str, dict] = {}
    if "quantity" in result.get("metrics", []):
        evidence["metric.quantity.policy"] = {
            "label": "有效销售件数统计口径",
            "value": {
                "measure": "sum(Quantity)",
                "filters": ["non-cancelled invoice", "Quantity > 0", "UnitPrice > 0"],
                "excluded_non_product_fee_codes": sorted(NON_PRODUCT_FEES),
                "unit": "items",
            },
            "unit": "methodology",
        }
    for metric, value in result["metric_totals"].items():
        unit = _metric_unit(metric)
        name = _metric_name(metric)
        total_id = "summary.total" if metric == "amount" else "summary.quantity"
        evidence[total_id] = {"label": f"筛选范围{name}", "value": value, "unit": unit}
    dimensions = list(plan.group_by)
    for row in result["groups"]:
        token = _summary_group_token(row, dimensions)
        for metric, value in row["values"].items():
            suffix = "amount" if metric == "amount" else "quantity"
            evidence[f"{token}.{suffix}"] = {
                "label": f"{row['label']}{_metric_name(metric)}",
                "value": value,
                "unit": _metric_unit(metric),
            }
    for ranking in result.get("rankings", []):
        identifier = (
            f"ranking.{ranking['time_dimension']}.{ranking['period_key']}.{ranking['business_dimension']}"
            if ranking.get("time_dimension")
            else f"ranking.{ranking['business_dimension']}.overall"
        )
        evidence[identifier] = {
            "label": f"{ranking['period_label']}按{_metric_name(ranking['metric'])}的{ranking['order_label']}排名",
            "value": {
                "scope": _filter_scope_evidence(result.get("filter_scope", {}))["filter_scope"]["value"],
                "metric": ranking["metric"],
                "order": ranking["order"],
                "requested_count": ranking["requested_count"],
                "actual_count": ranking["actual_count"],
                "rows": ranking["rows"],
                "leaders": ranking["leaders"],
            },
            "unit": "ranking",
        }
    evidence.update(_filter_scope_evidence(result["filter_scope"]))
    return evidence


def _filter_scope_evidence(scope: dict) -> dict[str, dict]:
    def as_text(value):
        if isinstance(value, dict):
            return {key: as_text(part) for key, part in value.items()}
        if isinstance(value, (list, tuple)):
            return [as_text(part) for part in value]
        return value.isoformat() if hasattr(value, "isoformat") else value

    evidence: dict[str, dict] = {
        "filter_scope": {
            "label": "本次实际日期范围与实体筛选条件",
            "value": {
                "date_range": as_text(scope.get("date_range", {})),
                "countries": list(scope.get("countries", [])),
                "products": list(scope.get("products", [])),
                "range_has_records": scope.get("range_has_records"),
                "range_record_count": scope.get("range_record_count"),
                "period1_has_records": scope.get("period1_has_records"),
                "period2_has_records": scope.get("period2_has_records"),
                "message": scope.get("message"),
            },
            "unit": "scope",
        }
    }
    for country in scope.get("countries", []):
        token = re.sub(r"[^A-Za-z0-9_-]+", "_", country).strip("_") or "unknown"
        evidence[f"scope.country.{token}.filter"] = {
            "label": "本次国家筛选",
            "value": country,
            "unit": "entity",
        }
    for product in scope.get("products", []):
        token = re.sub(r"[^A-Za-z0-9_-]+", "_", product).strip("_") or "unknown"
        evidence[f"scope.product.{token}.filter"] = {
            "label": "本次商品筛选",
            "value": product,
            "unit": "entity",
        }
    return evidence


def _summary_analysis(csv_path: str | Path, plan: "AnalysisPlan", filter_scope: dict | None = None) -> dict:
    data = _load_positive_transactions(csv_path)
    data = _apply_entity_filters(data, filter_scope)
    if plan.time_scope == "full_data":
        from planning_agent import load_csv_metadata

        metadata = load_csv_metadata(csv_path)
        start = plan.start_date or metadata.min_date
        end = plan.end_date or metadata.max_date
        plan.start_date = start
        plan.end_date = end
    else:
        start, end = _as_date(plan.start_date), _as_date(plan.end_date)
    if start > end:
        raise ValueError("分析范围开始日期晚于结束日期。")
    scoped = _date_range_frame(data, start, end)
    metrics = list(plan.metrics or ["amount"])
    rank_by = plan.rank_by or "amount"
    if not plan.ranking_order and rank_by not in metrics:
        rank_by = metrics[0]
    calculated_metrics = list(dict.fromkeys(metrics + ([rank_by] if plan.ranking_order else [])))
    scope = dict(filter_scope or resolve_filter_scope(csv_path, plan.country_filters, plan.product_filters))
    scope.update(
        date_range={"start": start, "end": end},
        range_has_records=not scoped.empty,
        range_record_count=int(len(scoped)),
    )
    if scoped.empty:
        scope["message"] = "该日期范围内无符合筛选条件的记录。"
    group_by = list(plan.group_by)
    time_dimension = next((item for item in group_by if item in {"day", "week", "month"}), None)
    business_dimension = next((item for item in group_by if item in {"country", "product"}), None)
    descriptions = (
        scoped.dropna(subset=["StockCode", "Description"])
        .groupby("StockCode")["Description"]
        .agg(lambda values: values.mode().sort_values().iloc[0])
    )

    def aggregate(frame: pd.DataFrame, metric: str, dimensions: list[str]) -> pd.Series:
        columns = []
        if time_dimension and "time" in dimensions:
            frequency = {"day": "D", "week": "W-SUN", "month": "M"}[time_dimension]
            frame = frame.assign(_time=frame["InvoiceDate"].dt.to_period(frequency))
            columns.append("_time")
        if business_dimension and "business" in dimensions:
            business_column = "Country" if business_dimension == "country" else "StockCode"
            columns.append(business_column)
        value_column = "Amount" if metric == "amount" else "Quantity"
        if columns:
            return frame.groupby(columns, dropna=False, sort=True)[value_column].sum()
        return pd.Series({"total": frame[value_column].sum()})

    product_rows = scoped[~scoped["StockCode"].isin(NON_PRODUCT_FEES)]
    aggregate_data = {
        metric: (
            product_rows
            if metric == "quantity" or business_dimension == "product"
            else scoped
        )
        for metric in calculated_metrics
    }
    dimensions = (["time"] if time_dimension else []) + (["business"] if business_dimension else [])
    aggregates = {metric: aggregate(frame, metric, dimensions) for metric, frame in aggregate_data.items()}
    keys = list(dict.fromkeys(key for series in aggregates.values() for key in series.index.tolist()))
    if time_dimension and business_dimension is None:
        frequency = {"day": "D", "week": "W-SUN", "month": "M"}[time_dimension]
        first_period = pd.Timestamp(start).to_period(frequency)
        last_period = pd.Timestamp(end).to_period(frequency)
        keys = list(pd.period_range(first_period, last_period, freq=frequency))
    rows = []
    for composite_key in keys:
        values = {metric: float(series.get(composite_key, 0.0)) for metric, series in aggregates.items()}
        row = {"values": values}
        if "amount" in values:
            row["amount"] = values["amount"]
        if "quantity" in values:
            row["quantity"] = values["quantity"]
        if not dimensions:
            row.update(key="total", label="总额")
        elif time_dimension and business_dimension:
            period, business_value = composite_key
            bucket_start, bucket_end, period_key = _period_bounds(time_dimension, period.start_time)
            clipped_start, clipped_end = max(start, bucket_start), min(end, bucket_end)
            business_key = str(business_value)
            if business_dimension == "product" and business_key in descriptions:
                business_label = f"{business_key} - {descriptions[business_key]}"
            else:
                business_label = business_key
            row.update(
                key=f"{period_key}|{business_key}",
                period_key=period_key,
                period_label=f"{clipped_start.isoformat()} 至 {clipped_end.isoformat()}",
                business_key=business_key,
                business_label=business_label,
                label=f"{clipped_start.isoformat()} 至 {clipped_end.isoformat()} · {business_label}",
                start=clipped_start,
                end=clipped_end,
                natural_start=bucket_start,
                natural_end=bucket_end,
                coverage="full" if clipped_start == bucket_start and clipped_end == bucket_end else "partial",
            )
        elif time_dimension:
            period = composite_key
            bucket_start, bucket_end, period_key = _period_bounds(time_dimension, period.start_time)
            clipped_start, clipped_end = max(start, bucket_start), min(end, bucket_end)
            row.update(
                key=period_key, period_key=period_key,
                label=f"{clipped_start.isoformat()} 至 {clipped_end.isoformat()}",
                start=clipped_start, end=clipped_end,
                natural_start=bucket_start, natural_end=bucket_end,
                coverage="full" if clipped_start == bucket_start and clipped_end == bucket_end else "partial",
            )
        else:
            business_key = str(composite_key)
            business_label = business_key
            if business_dimension == "product" and business_key in descriptions:
                business_label = f"{business_key} - {descriptions[business_key]}"
            row.update(key=business_key, label=business_label, business_key=business_key)
        rows.append(row)

    rankings = []

    def assign_competition_ranks(ordered_rows: list[dict]) -> None:
        previous_value = None
        current_rank = 0
        for position, row in enumerate(ordered_rows, 1):
            value = row["values"].get(rank_by, 0.0)
            if position == 1 or value != previous_value:
                current_rank = position
            row["rank"] = current_rank
            previous_value = value

    if time_dimension and business_dimension and plan.ranking_order:
        by_period: dict[str, list[dict]] = {}
        for row in rows:
            by_period.setdefault(row["period_key"], []).append(row)
        for period_key, period_rows in by_period.items():
            ordered = sorted(period_rows, key=lambda row: row["values"].get(rank_by, 0.0), reverse=plan.ranking_order == "desc")
            assign_competition_ranks(ordered)
            cutoff = ordered[min(plan.top_n, len(ordered)) - 1]["values"].get(rank_by, 0.0)
            selected = [
                row for row in ordered
                if row["values"].get(rank_by, 0.0) >= cutoff
            ] if plan.ranking_order == "desc" else [
                row for row in ordered
                if row["values"].get(rank_by, 0.0) <= cutoff
            ]
            top_value = ordered[0]["values"].get(rank_by, 0.0)
            ties = [row for row in ordered if row["values"].get(rank_by, 0.0) == top_value]
            rankings.append({
                "time_dimension": time_dimension,
                "business_dimension": business_dimension,
                "period_key": period_key,
                "period_label": ordered[0]["period_label"],
                "metric": rank_by,
                "order": plan.ranking_order,
                "order_label": "最高" if plan.ranking_order == "desc" else "最低",
                "requested_count": plan.top_n,
                "actual_count": len(selected),
                "rows": [{"key": row["business_key"], "label": row["business_label"], "value": row["values"][rank_by], "rank": row["rank"]} for row in selected],
                "leaders": [{"key": row["business_key"], "label": row["business_label"], "value": row["values"][rank_by], "rank": row["rank"]} for row in ties],
            })
        if time_dimension == "week":
            rankings.sort(key=lambda item: item["period_key"])
    elif plan.ranking_order:
        rows.sort(key=lambda row: row["values"].get(rank_by, 0.0), reverse=plan.ranking_order == "desc")
        ordered = list(rows)
        assign_competition_ranks(ordered)
        ranking_dimension = group_by[0] if group_by else "total"
        top_value = ordered[0]["values"].get(rank_by, 0.0) if ordered else 0.0
        if ordered:
            cutoff = ordered[min(plan.top_n, len(ordered)) - 1]["values"].get(rank_by, 0.0)
            selected = [
                row for row in ordered
                if row["values"].get(rank_by, 0.0) >= cutoff
            ] if plan.ranking_order == "desc" else [
                row for row in ordered
                if row["values"].get(rank_by, 0.0) <= cutoff
            ]
        else:
            selected = []
        leaders = [row for row in ordered if row["values"].get(rank_by, 0.0) == top_value]
        rankings.append({
            "time_dimension": None,
            "business_dimension": ranking_dimension,
            "period_key": "overall",
            "period_label": f"{start.isoformat()} 至 {end.isoformat()}",
            "metric": rank_by,
            "order": plan.ranking_order,
            "order_label": "最高" if plan.ranking_order == "desc" else "最低",
            "requested_count": plan.top_n,
            "actual_count": len(selected),
            "rows": [{
                "key": row.get("business_key", row["key"]), "label": row["label"],
                "value": row["values"][rank_by], "rank": row["rank"],
            } for row in selected],
            "leaders": [{
                "key": row.get("business_key", row["key"]), "label": row["label"],
                "value": row["values"][rank_by], "rank": row["rank"],
            } for row in leaders],
        })
        if not (time_dimension and business_dimension is None):
            rows = selected

    calculated_totals = {
        metric: float((scoped if metric == "amount" else product_rows)[
            "Amount" if metric == "amount" else "Quantity"
        ].sum())
        for metric in calculated_metrics
    }
    metric_totals = {metric: calculated_totals[metric] for metric in metrics}
    summary = {
        "total": metric_totals.get("amount"),
        "group_count": len(rows),
        "requested_top_n": plan.top_n,
        "metric_totals": metric_totals,
    }
    result = {
        "analysis_type": "summary",
        "date_range": {"start": start, "end": end},
        "group_by": group_by[0] if len(group_by) == 1 else (group_by or None),
        "group_by_fields": group_by,
        "metrics": metrics,
        "rank_by": rank_by,
        "ranking_order": plan.ranking_order,
        "filter_scope": scope,
        "groups": rows,
        "tables": {"groups": rows, "rankings": rankings},
        "rankings": rankings,
        "summary": summary,
        "metric_totals": metric_totals,
    }
    result["group_by_time"] = time_dimension
    result["group_by_business"] = business_dimension
    result["evidence"] = _summary_evidence(result, plan)
    return result


def analyze_plan(csv_path: str | Path, plan: "AnalysisPlan", *, filter_scope: dict | None = None) -> dict:
    """Execute either a single-range summary plan or the legacy comparison plan."""
    if plan.status != "executable":
        raise ValueError("只有 executable 计划可以进入计算节点。")
    from planning_agent import load_csv_metadata, validate_plan_dates

    validate_plan_dates(plan, load_csv_metadata(csv_path))
    scope = filter_scope or resolve_filter_scope(csv_path, plan.country_filters, plan.product_filters)
    if plan.analysis_type == "summary":
        return _summary_analysis(csv_path, plan, scope)
    # Read the filtered transaction source once, then calculate every requested
    # metric from that same local frame. Legacy plans default to amount.
    source_data = _load_positive_transactions(csv_path)
    requested_metrics = list(dict.fromkeys(plan.metrics or ["amount"]))
    metric_results = {
        metric: analyze_periods(
            csv_path,
            plan.period1_start,
            plan.period1_end,
            plan.period2_start,
            plan.period2_end,
            dimensions=plan.dimensions,
            direction=plan.direction,
            top_n=plan.top_n,
            country_filters=plan.country_filters,
            product_filters=plan.product_filters,
            filter_scope=scope,
            metric=metric,
            _data=source_data,
        )
        for metric in requested_metrics
    }
    primary_metric = plan.rank_by if plan.rank_by in metric_results else requested_metrics[0]
    result = dict(metric_results[primary_metric])
    result["metrics"] = requested_metrics
    result["rank_by"] = primary_metric
    result["metric_results"] = metric_results
    if "amount" in metric_results:
        result["amount_result"] = metric_results["amount"]
    if "quantity" in metric_results:
        result["quantity_result"] = metric_results["quantity"]
    result["analysis_type"] = "comparison"
    result["date_range"] = {
        "period1": {"start": plan.period1_start, "end": plan.period1_end},
        "period2": {"start": plan.period2_start, "end": plan.period2_end},
    }
    result["filter_scope"] = scope
    result["filter_scope"]["date_range"] = result["date_range"]
    result["filter_scope"]["period1_has_records"] = result["period1_record_count"] > 0
    result["filter_scope"]["period2_has_records"] = result["period2_record_count"] > 0
    empty_periods = [
        label for label, present in (
            ("前期", result["filter_scope"]["period1_has_records"]),
            ("后期", result["filter_scope"]["period2_has_records"]),
        ) if not present
    ]
    if empty_periods:
        result["filter_scope"]["message"] = "、".join(empty_periods) + "在所选国家/商品筛选下无符合条件的记录。"
    scope.update(result["filter_scope"])
    result["summary"] = {
        "period1_total": result["period1_total"],
        "period2_total": result["period2_total"],
        "difference": result["difference"],
        "change_rate": result["change_rate"],
    }
    result["tables"] = {
        key: result[key]
        for key in ("country_decreases", "country_increases", "product_decreases", "product_increases", "fees")
    }
    from report_generator import build_evidence

    result["evidence"] = build_evidence(result, plan)
    if "quantity" in result.get("metrics", []):
        result["evidence"]["metric.quantity.policy"] = {
            "label": "有效销售件数统计口径",
            "value": {
                "measure": "sum(Quantity)",
                "filters": ["non-cancelled invoice", "Quantity > 0", "UnitPrice > 0"],
                "excluded_non_product_fee_codes": sorted(NON_PRODUCT_FEES),
                "unit": "items",
            },
            "unit": "methodology",
        }
    result["evidence"].update(_filter_scope_evidence(scope))
    return result


def _period_rows(data: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    return data[data["InvoiceDate"].dt.date.between(start, end)]


def _leaderboard(
    dimension: str,
    period1: pd.DataFrame,
    period2: pd.DataFrame,
    descriptions: pd.Series | None = None,
    top_n: int = 5,
    value_column: str = "Amount",
) -> tuple[list[dict], list[dict]]:
    first = period1.groupby(dimension, dropna=False)[value_column].sum().rename("period1")
    second = period2.groupby(dimension, dropna=False)[value_column].sum().rename("period2")
    comparison = pd.concat([first, second], axis=1).fillna(0.0)
    comparison["difference"] = comparison["period2"] - comparison["period1"]

    if descriptions is not None:
        comparison["Description"] = descriptions.reindex(comparison.index)

    def records(rows: pd.DataFrame) -> list[dict]:
        result = []
        for key, row in rows.iterrows():
            entry = {dimension: key}
            if descriptions is not None:
                entry["Description"] = row["Description"]
            entry.update(
                period1=float(row["period1"]),
                period2=float(row["period2"]),
                difference=float(row["difference"]),
            )
            result.append(entry)
        return result

    decreasing = comparison[comparison["difference"] < 0].sort_values(
        ["difference"], ascending=True, kind="stable"
    ).head(top_n)
    increasing = comparison[comparison["difference"] > 0].sort_values(
        ["difference"], ascending=False, kind="stable"
    ).head(top_n)
    return records(decreasing), records(increasing)


def analyze_periods(
    csv_path: str | Path,
    period1_start: str | date,
    period1_end: str | date,
    period2_start: str | date,
    period2_end: str | date,
    *,
    dimensions: list[str] | None = None,
    direction: str | None = "both",
    top_n: int = 5,
    country_filters: list[str] | None = None,
    product_filters: list[str] | None = None,
    filter_scope: dict | None = None,
    metric: str = "amount",
    _data: pd.DataFrame | None = None,
) -> dict:
    """Compare two inclusive date ranges in the retail transaction CSV."""
    start1, end1 = _as_date(period1_start), _as_date(period1_end)
    start2, end2 = _as_date(period2_start), _as_date(period2_end)
    if start1 > end1 or start2 > end2:
        raise ValueError("Each period start date must be on or before its end date.")
    if top_n < 1:
        raise ValueError("top_n must be at least 1.")
    if dimensions is not None and not set(dimensions).issubset({"country", "product"}):
        raise ValueError("dimensions may contain only country and product.")
    if dimensions and direction not in {"growth", "decline", "both"}:
        raise ValueError("direction must be growth, decline, or both when rankings are requested.")

    if metric not in {"amount", "quantity"}:
        raise ValueError("metric must be amount or quantity")
    value_column = "Amount" if metric == "amount" else "Quantity"
    data = _load_positive_transactions(csv_path) if _data is None else _data
    scope = filter_scope or resolve_filter_scope(csv_path, country_filters, product_filters)
    data = _apply_entity_filters(data, scope)

    period1 = _period_rows(data, start1, end1)
    period2 = _period_rows(data, start2, end2)
    if metric == "quantity":
        data = data[~data["StockCode"].isin(NON_PRODUCT_FEES)]
        period1 = _period_rows(data, start1, end1)
        period2 = _period_rows(data, start2, end2)
    total1 = float(period1[value_column].sum())
    total2 = float(period2[value_column].sum())
    difference = total2 - total1

    country_down, country_up = _leaderboard("Country", period1, period2, top_n=top_n, value_column=value_column)

    products = data[~data["StockCode"].isin(NON_PRODUCT_FEES)].copy()
    if scope.get("products"):
        # Explicit fee-code selection is intentional; otherwise fees never rank as goods.
        products = data[data["StockCode"].isin(scope["products"])].copy()
    product_period1 = _period_rows(products, start1, end1)
    product_period2 = _period_rows(products, start2, end2)
    descriptions = (
        products.dropna(subset=["StockCode", "Description"])
        .groupby("StockCode")["Description"]
        .agg(lambda values: values.mode().sort_values().iloc[0])
    )
    product_down, product_up = _leaderboard(
        "StockCode", product_period1, product_period2, descriptions, top_n=top_n, value_column=value_column
    )

    fees = {}
    for code, label in NON_PRODUCT_FEES.items():
        fee1 = float(period1.loc[period1["StockCode"] == code, value_column].sum())
        fee2 = float(period2.loc[period2["StockCode"] == code, value_column].sum())
        if fee1 or fee2:
            fees[code] = {
                "description": label,
                "period1": fee1,
                "period2": fee2,
                "difference": fee2 - fee1,
            }

    result = {
        "metric": metric,
        "period1_total": total1,
        "period2_total": total2,
        "difference": difference,
        "change_rate": difference / total1 if total1 else None,
        "fees": fees,
        "country_decreases": country_down,
        "country_increases": country_up,
        "product_decreases": product_down,
        "product_increases": product_up,
        "period1_record_count": int(len(period1)),
        "period2_record_count": int(len(period2)),
        "filter_scope": {
            **scope,
            "date_range": {
                "period1": {"start": start1, "end": end1},
                "period2": {"start": start2, "end": end2},
            },
            "period1_has_records": not period1.empty,
            "period2_has_records": not period2.empty,
        },
    }
    missing_periods = [
        label for label, empty in (("前期", period1.empty), ("后期", period2.empty)) if empty
    ]
    if missing_periods:
        result["filter_scope"]["message"] = "、".join(missing_periods) + "在所选国家/商品筛选下无符合条件的记录。"

    if dimensions is not None:
        if "country" not in dimensions:
            result["country_decreases"] = []
            result["country_increases"] = []
        if "product" not in dimensions:
            result["product_decreases"] = []
            result["product_increases"] = []
        if direction == "decline":
            result["country_increases"] = []
            result["product_increases"] = []
        elif direction == "growth":
            result["country_decreases"] = []
            result["product_decreases"] = []
        for key in (
            "country_decreases",
            "country_increases",
            "product_decreases",
            "product_increases",
        ):
            result[key] = result[key][:top_n]

    return result
