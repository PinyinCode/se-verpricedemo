# -*- coding: utf-8 -*-
"""Conservative BCTC data-quality checks.

The module validates supplied rows only. It never guesses a field's meaning,
period basis, unit, or sign convention. Callers must pass explicit policies
when those policies have been verified for the relevant issuer/statement.
"""
from math import isfinite
import re

from bctc_rules import VCI_FIELD_MAP, resolve_vci_unit


_PERIOD_RE = re.compile(r"^(\d{4})-Q([1-4])$")
_VALID_SIGN_POLICIES = {"positive", "non_negative", "negative", "non_positive"}

# Explicitly verified VCI statement identities. These are diagnostics only:
# callers opt in by passing get_vci_reconciliation_equations(section).
# Missing inputs remain unavailable; no values are inferred or repaired.
VCI_RECONCILIATION_EQUATIONS = {
    "income_statement": (
        {
            "id": "bank_ppop_equals_operating_income_plus_signed_operating_expense",
            "terms": {"isb38": 1, "isb39": 1},
            "result_field": "isb40",
        },
    ),
    "balance_sheet": (
        {
            "id": "bank_net_customer_loans",
            "terms": {"bsb104": 1, "bsb105": 1},
            "result_field": "bsb103",
        },
        {
            "id": "total_liabilities_and_equity",
            "terms": {"bsa54": 1, "bsa78": 1},
            "result_field": "bsa96",
        },
    ),
    "cash_flow": (
        {
            "id": "cash_beginning_plus_net_change_and_fx_equals_ending",
            "terms": {"cfa36": 1, "cfa34": 1, "cfa37": 1},
            "result_field": "cfa38",
        },
    ),
}


def get_vci_reconciliation_equations(section):
    """Return defensive copies of verified VCI equations for explicit validation."""
    from copy import deepcopy
    key = str(section or "").strip().lower()
    return deepcopy(VCI_RECONCILIATION_EQUATIONS.get(key, ()))


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def parse_quarter(period):
    """Return (year, quarter), or None for unsupported period labels."""
    match = _PERIOD_RE.fullmatch(str(period or "").strip().upper())
    return (int(match.group(1)), int(match.group(2))) if match else None


def consecutive_quarters(periods):
    """True only when all supplied quarter labels are valid and consecutive."""
    parsed = [parse_quarter(item) for item in periods]
    if not parsed or any(item is None for item in parsed):
        return False
    if len(set(parsed)) != len(parsed):
        return False
    ordered = sorted(parsed)
    ordinals = [year * 4 + quarter for year, quarter in ordered]
    return all(right - left == 1 for left, right in zip(ordinals, ordinals[1:]))


def validate_ttm_periods(periods, required_quarters=4):
    """Validate exact TTM quarter count and continuity; does not infer YTD basis."""
    normalized = [str(item or "").strip().upper() for item in (periods or [])]
    errors = []
    if len(normalized) != required_quarters:
        errors.append("ttm_requires_exact_quarter_count")
    if not consecutive_quarters(normalized):
        errors.append("ttm_periods_not_unique_and_consecutive")
    return {
        "valid": not errors,
        "periods": sorted(normalized),
        "required_quarters": required_quarters,
        "errors": errors,
        "period_basis_verified": False,
        "note": "Continuity alone does not prove quarterly standalone rather than YTD values.",
    }


def validate_sign(value, policy):
    """Return None if sign policy is satisfied, otherwise a stable issue code."""
    number = _number(value)
    if number is None:
        return "invalid_numeric_value"
    if policy not in _VALID_SIGN_POLICIES:
        return "unknown_sign_policy"
    if policy == "positive" and number <= 0:
        return "expected_positive_value"
    if policy == "non_negative" and number < 0:
        return "unexpected_negative_value"
    if policy == "negative" and number >= 0:
        return "expected_negative_value"
    if policy == "non_positive" and number > 0:
        return "unexpected_positive_value"
    return None


def reconcile_equations(values_by_field, equations, relative_tolerance=1e-6,
                        absolute_tolerance=1.0):
    """Check explicitly supplied linear identities, e.g. net=gross+negative_provision.

    Each equation is a dict with 'id', 'terms' (field -> coefficient), and
    'result_field'. Missing inputs are reported as unavailable, never imputed.
    """
    results = []
    for equation in equations or []:
        equation_id = str(equation.get("id") or "unnamed_equation")
        terms = equation.get("terms") or {}
        result_field = equation.get("result_field")
        inputs = {field: _number(values_by_field.get(field)) for field in terms}
        expected = _number(values_by_field.get(result_field))
        if expected is None or any(value is None for value in inputs.values()):
            results.append({
                "id": equation_id, "status": "unavailable",
                "reason": "missing_or_invalid_input",
            })
            continue
        calculated = sum(inputs[field] * float(coefficient)
                         for field, coefficient in terms.items())
        difference = calculated - expected
        tolerance = max(float(absolute_tolerance),
                        abs(expected) * float(relative_tolerance))
        results.append({
            "id": equation_id,
            "status": "pass" if abs(difference) <= tolerance else "fail",
            "calculated": calculated,
            "reported": expected,
            "difference": difference,
            "tolerance": tolerance,
        })
    return results


def validate_bctc_rows(rows, section, expected_units=None, sign_policies=None,
                       required_fields=None, equations=None,
                       period_basis=None):
    """Validate normalized BCTC rows without silently repairing source data.

    Expected row shape: {source_field, id, period, value, unit}.
    expected_units/sign_policies are explicit caller-provided policies keyed by
    source_field. Unknown fields remain available as raw data but are not
    considered verified scoring inputs.
    """
    section = str(section or "").strip().lower()
    if section not in VCI_FIELD_MAP:
        raise ValueError("unsupported BCTC section")

    if rows is None:
        rows = []
    elif not isinstance(rows, (list, tuple)):
        raise ValueError("rows must be a list or tuple")
    if expected_units is None:
        expected_units = {}
    elif not isinstance(expected_units, dict):
        raise ValueError("expected_units must be an object")
    if sign_policies is None:
        sign_policies = {}
    elif not isinstance(sign_policies, dict):
        raise ValueError("sign_policies must be an object")
    if required_fields is None:
        required_fields = ()
    elif not isinstance(required_fields, (list, tuple, set)):
        raise ValueError("required_fields must be a list, tuple or set")
    if equations is None:
        equations = ()
    elif not isinstance(equations, (list, tuple)):
        raise ValueError("equations must be a list or tuple")
    required = {
        str(field).strip().lower()
        for field in required_fields if str(field).strip()
    }
    errors, warnings = [], []
    seen_source_period = {}
    seen_canonical_period = {}
    values_by_period = {}
    valid_rows = 0
    known_numeric_rows = 0
    unknown_fields = 0

    for index, row in enumerate(rows or []):
        if not isinstance(row, dict):
            errors.append({"code": "row_not_object", "row": index})
            continue

        source = str(row.get("source_field") or "").strip().lower()
        period = str(row.get("period") or "").strip().upper()
        canonical = VCI_FIELD_MAP[section].get(source)
        if not source or not canonical:
            unknown_fields += 1
            warnings.append({"code": "unknown_source_field_raw_only",
                             "row": index, "source_field": source or None})
        if parse_quarter(period) is None:
            errors.append({"code": "invalid_reporting_period", "row": index,
                           "period": period or None})
        value = _number(row.get("value"))
        if value is None:
            errors.append({"code": "invalid_numeric_value", "row": index,
                           "source_field": source or None, "period": period or None})
            continue

        valid_rows += 1
        if canonical:
            known_numeric_rows += 1
        source_key = (source, period)
        if source_key in seen_source_period:
            previous = seen_source_period[source_key]
            if previous != value:
                errors.append({"code": "conflicting_duplicate_source_period",
                               "source_field": source, "period": period})
            else:
                warnings.append({"code": "identical_duplicate_source_period",
                                 "source_field": source, "period": period})
        else:
            seen_source_period[source_key] = value

        if canonical:
            canonical_key = (canonical, period)
            if canonical_key in seen_canonical_period:
                previous = seen_canonical_period[canonical_key]
                if previous != value:
                    errors.append({"code": "conflicting_canonical_field_values",
                                   "canonical_id": canonical, "period": period,
                                   "source_field": source})
                else:
                    warnings.append({"code": "duplicate_canonical_value",
                                     "canonical_id": canonical, "period": period})
            else:
                seen_canonical_period[canonical_key] = value

        expected_unit = expected_units.get(source)
        actual_unit = str(row.get("unit") or "").strip()
        if expected_unit:
            if actual_unit and actual_unit.lower() != str(expected_unit).strip().lower():
                errors.append({"code": "unit_mismatch", "source_field": source,
                               "period": period, "expected": expected_unit,
                               "actual": actual_unit})
            elif not actual_unit:
                errors.append({"code": "missing_required_unit",
                               "source_field": source, "period": period})
        elif source in VCI_FIELD_MAP[section]:
            resolved = resolve_vci_unit(section, source, actual_unit)
            if not resolved:
                warnings.append({"code": "unit_unverified",
                                 "source_field": source, "period": period})

        sign_policy = sign_policies.get(source)
        if sign_policy:
            issue = validate_sign(value, sign_policy)
            if issue:
                errors.append({"code": issue, "source_field": source,
                               "period": period, "value": value})

        values_by_period.setdefault(period, {})[source] = value

    present_fields = {source for source, _period in seen_source_period}
    for field in sorted(required - present_fields):
        errors.append({"code": "required_field_missing", "source_field": field})

    equation_results = []
    for period, values in sorted(values_by_period.items()):
        results = reconcile_equations(values, equations or [])
        for result in results:
            result["period"] = period
            if result["status"] == "fail":
                errors.append({"code": "subtotal_reconciliation_failed",
                               "equation_id": result["id"], "period": period,
                               "difference": result["difference"]})
        equation_results.extend(results)

    basis = str(period_basis or "").strip().upper() or None
    if basis not in (None, "QUARTERLY", "YTD", "ANNUAL", "POINT_IN_TIME"):
        errors.append({"code": "unsupported_period_basis", "period_basis": basis})
    if basis is None:
        warnings.append({"code": "period_basis_not_verified",
                         "note": "Do not calculate TTM from these rows until basis is verified."})

    return {
        "valid": not errors,
        "scoreable": not errors and basis is not None and known_numeric_rows > 0,
        "section": section,
        "row_count": len(rows or []),
        "valid_numeric_row_count": valid_rows,
        "unknown_field_count": unknown_fields,
        "period_basis": basis,
        "errors": errors,
        "warnings": warnings,
        "reconciliations": equation_results,
    }
