# -*- coding: utf-8 -*-
"""Conservative adapter from raw VCI rows to normalized metric records."""
from math import isfinite

from data_quality import parse_quarter
from metric_calculations import (
    calculate_ttm_sum,
    calculate_ytd_to_quarter,
)
from metric_contract import build_metric_record


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def extract_source_value(rows, source_field, period):
    """Extract one exact source-field/period value; conflicting duplicates block."""
    field = str(source_field or "").strip().lower()
    target_period = str(period or "").strip().upper()
    matches = [
        row for row in (rows or [])
        if isinstance(row, dict)
        and str(row.get("source_field") or "").strip().lower() == field
        and str(row.get("period") or "").strip().upper() == target_period
    ]
    if not field or not target_period:
        return {"available": False, "reason": "source_field_or_period_missing"}
    if not matches:
        return {"available": False, "reason": "source_value_missing"}
    values = [_number(row.get("value")) for row in matches]
    units = [str(row.get("unit") or "").strip() for row in matches]
    if any(value is None for value in values):
        return {"available": False, "reason": "source_value_invalid"}
    if len(set(values)) > 1:
        return {"available": False, "reason": "conflicting_duplicate_source_period"}
    if not units[0] or any(unit.lower() != units[0].lower() for unit in units):
        return {"available": False, "reason": "source_unit_missing_or_conflicting"}
    return {
        "available": True,
        "value": values[0],
        "unit": units[0],
        "source_field": field,
        "period": target_period,
        "reason": "",
    }


def extract_quarter_series(rows, source_field, periods, period_basis):
    """Return four standalone quarterly values from quarterly or YTD source rows."""
    basis = str(period_basis or "").strip().upper()
    if basis not in ("QUARTERLY", "YTD"):
        return {"available": False, "reason": "period_basis_not_verified"}
    selected = [str(item or "").strip().upper() for item in (periods or [])]
    if len(selected) != 4:
        return {"available": False, "reason": "ttm_requires_exactly_four_quarters"}

    values = {}
    unit = None
    for period in selected:
        parsed = parse_quarter(period)
        if parsed is None:
            return {"available": False, "reason": "invalid_reporting_period"}
        current = extract_source_value(rows, source_field, period)
        if not current["available"]:
            return {"available": False, "reason": period + ":" + current["reason"]}
        if unit is None:
            unit = current["unit"]
        elif current["unit"].lower() != unit.lower():
            return {"available": False, "reason": "source_units_mismatch"}

        if basis == "QUARTERLY":
            quarter_value = current["value"]
        else:
            year, quarter = parsed
            prior_ytd = None
            if quarter > 1:
                prior_period = f"{year}-Q{quarter - 1}"
                prior = extract_source_value(rows, source_field, prior_period)
                if not prior["available"]:
                    return {
                        "available": False,
                        "reason": period + ":prior_ytd_" + prior["reason"],
                    }
                if prior["unit"].lower() != unit.lower():
                    return {"available": False, "reason": "source_units_mismatch"}
                prior_ytd = prior["value"]
            converted = calculate_ytd_to_quarter(
                current["value"], prior_ytd, quarter, "YTD"
            )
            if not converted["available"]:
                return {"available": False, "reason": period + ":" + converted["reason"]}
            quarter_value = converted["value"]
        values[period] = quarter_value

    # This also verifies uniqueness and exact four-quarter continuity.
    ttm_check = calculate_ttm_sum(values, selected, "QUARTERLY")
    if not ttm_check["available"]:
        return {"available": False, "reason": ttm_check["reason"]}
    return {
        "available": True,
        "values_by_period": values,
        "unit": unit,
        "periods": sorted(selected),
        "reason": "",
    }


def build_ttm_metric_record(metric_id, rows, source_field, periods,
                            source_period_basis, unit=None):
    """Build a verified TTM sum record from four consecutive source quarters."""
    series = extract_quarter_series(
        rows, source_field, periods, source_period_basis
    )
    if not series["available"]:
        return build_metric_record(
            metric_id,
            {"available": False, "reason": series["reason"]},
            unit or "",
            "TTM",
            "TTM",
            [source_field],
        )
    calculation = calculate_ttm_sum(
        series["values_by_period"], series["periods"], "QUARTERLY"
    )
    return build_metric_record(
        metric_id,
        calculation,
        unit or series["unit"],
        "TTM through " + series["periods"][-1],
        "TTM",
        [source_field],
    )


def build_point_in_time_metric_record(metric_id, rows, source_field,
                                      period, unit=None):
    """Build a verified point-in-time record from one exact BCTC source row."""
    source = extract_source_value(rows, source_field, period)
    calculation = (
        {"available": True, "value": source["value"]}
        if source["available"]
        else {"available": False, "reason": source["reason"]}
    )
    return build_metric_record(
        metric_id,
        calculation,
        unit or source.get("unit") or "",
        period,
        "POINT_IN_TIME",
        [source_field],
    )
