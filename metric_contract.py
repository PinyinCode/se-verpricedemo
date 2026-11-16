# -*- coding: utf-8 -*-
"""Standard metric data contract with explicit lineage and quality status."""
from math import isfinite


METRIC_CONTRACT_VERSION = "1.0.0"

VALID_PERIOD_BASES = {
    "QUARTERLY", "YTD", "ANNUAL", "TTM", "POINT_IN_TIME",
    "ANNUAL_FORECAST", "YOY_POINT_IN_TIME",
}


def _finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def build_metric_record(metric_id, calculation, unit, period, period_basis,
                        source_fields, quality_status="verified",
                        quality_issues=None):
    """Wrap a formula result in the canonical engine metric contract."""
    calculation = calculation if isinstance(calculation, dict) else {}
    value = _finite(calculation.get("value"))
    normalized_metric_id = str(metric_id or "").strip()
    basis = str(period_basis or "").strip().upper()
    raw_sources = source_fields if isinstance(source_fields, (list, tuple, set)) else []
    sources = sorted(set(
        str(item).strip() for item in raw_sources if str(item).strip()
    ))
    if quality_issues is None:
        raw_issues = []
    elif isinstance(quality_issues, (list, tuple, set)):
        raw_issues = quality_issues
    else:
        raw_issues = ["quality_issues_invalid"]
    issues = [str(item) for item in raw_issues if str(item)]
    reason = calculation.get("reason") or ""
    status = str(quality_status or "unverified").strip().lower()

    if calculation.get("available") is not True or value is None:
        status = "unavailable"
        reason = reason or "calculation_unavailable"
    elif not normalized_metric_id:
        status, reason = "unverified", "metric_id_missing"
    elif not str(unit or "").strip():
        status, reason = "unverified", "unit_missing"
    elif not str(period or "").strip():
        status, reason = "unverified", "period_missing"
    elif basis not in VALID_PERIOD_BASES:
        status, reason = "unverified", "period_basis_missing_or_invalid"
    elif not sources:
        status, reason = "unverified", "source_lineage_missing"
    elif issues:
        status, reason = "unverified", "quality_issues_present"
    elif status != "verified":
        reason = reason or "metric_quality_not_verified"

    available = (
        calculation.get("available") is True
        and value is not None
        and status == "verified"
        and not reason
    )
    return {
        "metric_id": normalized_metric_id,
        "value": value if available else None,
        "unit": str(unit or "").strip() or None,
        "period": str(period or "").strip() or None,
        "period_basis": basis or None,
        "source_fields": sources,
        "available": available,
        "quality_status": status,
        "quality_issues": issues,
        "reason": "" if available else (reason or "metric_not_verified"),
    }


def validate_metric_record(record, expected_spec=None):
    """Check metric provenance and basis against an explicit scoring spec."""
    if not isinstance(record, dict):
        return {"valid": False, "reason": "metric_record_missing"}
    if not str(record.get("metric_id") or "").strip():
        return {"valid": False, "reason": "metric_id_missing"}
    if record.get("available") is not True:
        return {"valid": False, "reason": record.get("reason") or "metric_unavailable"}
    if record.get("quality_status") != "verified":
        return {"valid": False, "reason": "metric_quality_not_verified"}
    if _finite(record.get("value")) is None:
        return {"valid": False, "reason": "metric_value_invalid"}
    raw_issues = record.get("quality_issues", [])
    if not isinstance(raw_issues, (list, tuple, set)) or raw_issues:
        return {"valid": False, "reason": "metric_quality_issues_present"}
    if not str(record.get("unit") or "").strip():
        return {"valid": False, "reason": "metric_unit_missing"}
    if not str(record.get("period") or "").strip():
        return {"valid": False, "reason": "metric_period_missing"}
    basis = str(record.get("period_basis") or "").strip().upper()
    if basis not in VALID_PERIOD_BASES:
        return {"valid": False, "reason": "metric_period_basis_invalid"}
    raw_sources = record.get("source_fields")
    if not isinstance(raw_sources, (list, tuple, set)):
        return {"valid": False, "reason": "metric_source_lineage_invalid"}
    sources = {str(item).strip() for item in raw_sources if str(item).strip()}
    if not sources:
        return {"valid": False, "reason": "metric_source_lineage_missing"}

    spec = expected_spec if isinstance(expected_spec, dict) else {}
    expected_metric_id = str(spec.get("metric_id") or "").strip()
    if expected_metric_id and str(record.get("metric_id") or "").strip() != expected_metric_id:
        return {"valid": False, "reason": "metric_id_mismatch"}
    raw_expected_sources = spec.get("source_fields")
    if raw_expected_sources is not None and not isinstance(raw_expected_sources, (list, tuple, set)):
        return {"valid": False, "reason": "expected_source_lineage_invalid"}
    expected_sources = set(
        str(item).strip() for item in (raw_expected_sources or [])
        if str(item).strip()
    )
    # Canonical catalog lineage is authoritative for catalogued metrics.
    # A caller-supplied scoring spec must not redefine the source fields or
    # period basis for a known metric ID.
    try:
        from metric_catalog import get_metric_definition
        catalog_definition = get_metric_definition(record.get("metric_id"))
    except (ImportError, AttributeError):
        catalog_definition = None
    if not catalog_definition:
        return {"valid": False, "reason": "metric_not_in_canonical_catalog"}
    catalog_status = str(catalog_definition.get("status") or "").strip().lower()
    if not catalog_status.startswith("verified"):
        return {"valid": False, "reason": "metric_catalog_source_not_verified"}
    if catalog_definition:
        catalog_basis = str(
            catalog_definition.get("period_basis") or ""
        ).strip().upper()
        if catalog_basis and basis != catalog_basis:
            return {"valid": False, "reason": "metric_catalog_period_basis_mismatch"}
        catalog_unit = str(
            catalog_definition.get("unit") or ""
        ).strip().lower()
        if not catalog_unit:
            return {"valid": False, "reason": "metric_catalog_unit_missing"}
        if str(record.get("unit") or "").strip().lower() != catalog_unit:
            return {"valid": False, "reason": "metric_catalog_unit_mismatch"}
        catalog_sources = {
            str(item).strip()
            for item in (catalog_definition.get("source_fields") or [])
            if str(item).strip()
        }
        if not catalog_basis:
            return {"valid": False, "reason": "metric_catalog_period_basis_missing"}
        if not catalog_sources:
            return {"valid": False, "reason": "metric_catalog_source_lineage_missing"}
        if sources != catalog_sources:
            return {"valid": False, "reason": "metric_catalog_source_lineage_mismatch"}
    expected_basis = str(spec.get("period_basis") or "").strip().upper()
    if expected_basis and basis != expected_basis:
        return {"valid": False, "reason": "period_basis_mismatch_or_unverified"}
    if expected_sources and not expected_sources.issubset(sources):
        return {"valid": False, "reason": "source_lineage_missing_or_incomplete"}
    expected_unit = str(spec.get("unit") or "").strip()
    if expected_unit and str(record.get("unit") or "").strip().lower() != expected_unit.lower():
        return {"valid": False, "reason": "metric_unit_mismatch"}
    return {"valid": True, "reason": ""}
