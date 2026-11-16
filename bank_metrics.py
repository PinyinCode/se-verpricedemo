# -*- coding: utf-8 -*-
"""Verified bank BCTC metric definitions.

This module is deliberately conservative: field IDs are accepted only when the
issuer mapping has been independently verified. It does not infer NPL, CAR,
CASA, NIM or credit-cost inputs, and it does not assign scores.
"""
from math import isfinite


BANK_FIELD_MAP = {
    "NET_INTEREST_INCOME": ("income_statement", "isb27"),
    "TOTAL_OPERATING_INCOME": ("income_statement", "isb38"),
    "OPERATING_EXPENSE": ("income_statement", "isb39"),
    "PPOP": ("income_statement", "isb40"),
    "CREDIT_LOSS_PROVISION": ("income_statement", "isb41"),
    "PROFIT_BEFORE_TAX": ("income_statement", "isa16"),
    "NET_PROFIT": ("income_statement", "isa20"),
    "PARENT_PROFIT": ("income_statement", "isa22"),
    "CUSTOMER_LOANS_NET": ("balance_sheet", "bsb103"),
    "CUSTOMER_LOANS_GROSS": ("balance_sheet", "bsb104"),
    "CUSTOMER_LOAN_PROVISION": ("balance_sheet", "bsb105"),
    "CUSTOMER_DEPOSITS": ("balance_sheet", "bsb113"),
}

# CTG reconciliations establish these relationships for the tested issuer and
# periods; other banks must pass their own reconciliation before use.
CTG_VERIFIED_EQUATIONS = (
    {"name": "net_loans_identity", "lhs": "bsb103", "rhs": (("bsb104", 1.0), ("bsb105", 1.0))},
    {"name": "ppop_identity", "lhs": "isb40", "rhs": (("isb38", 1.0), ("isb39", 1.0))},
    {"name": "pbt_identity", "lhs": "isa16", "rhs": (("isb40", 1.0), ("isb41", 1.0))},
)

UNVERIFIED_METRICS = {
    "NIM": "Requires average interest-earning assets and verified period basis.",
    "CREDIT_COST": "Requires comparable average loan balances, provision flow and annualization policy.",
    "NPL_RATIO": "No verified NPL numerator field.",
    "NPL_COVERAGE": "No verified NPL numerator field.",
    "CAR": "Requires regulatory capital and risk-weighted assets.",
    "CASA": "Requires demand deposits and total customer deposits.",
}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def _index_rows(rows):
    """Index VCI-style rows by source_field; conflicting duplicates are invalid."""
    result = {}
    conflicts = set()
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        field = str(row.get("source_field") or "").strip().lower()
        value = _number(row.get("value"))
        if not field or value is None:
            continue
        if field in result and result[field] != value:
            conflicts.add(field)
        else:
            result[field] = value
    return result, conflicts


def _unavailable(reason):
    return {"available": False, "value": None, "reason": reason}


def _available(value, formula):
    return {"available": True, "value": value, "reason": None, "formula": formula}


def calculate_bank_cir(rows, issuer_mapping_verified=False):
    """CIR = absolute operating expense / total operating income * 100."""
    if not issuer_mapping_verified:
        return _unavailable("issuer_mapping_not_verified")
    values, conflicts = _index_rows(rows)
    if conflicts.intersection({"isb38", "isb39"}):
        return _unavailable("conflicting_duplicate_source_field")
    income, expense = values.get("isb38"), values.get("isb39")
    if income is None or expense is None:
        return _unavailable("missing_required_field")
    if income <= 0:
        return _unavailable("non_positive_operating_income")
    return _available(abs(expense) / income * 100.0, "abs(isb39) / isb38 * 100")


def calculate_bank_ppop_margin(rows, issuer_mapping_verified=False):
    """PPOP margin = PPOP / total operating income * 100."""
    if not issuer_mapping_verified:
        return _unavailable("issuer_mapping_not_verified")
    values, conflicts = _index_rows(rows)
    if conflicts.intersection({"isb38", "isb40"}):
        return _unavailable("conflicting_duplicate_source_field")
    income, ppop = values.get("isb38"), values.get("isb40")
    if income is None or ppop is None:
        return _unavailable("missing_required_field")
    if income <= 0:
        return _unavailable("non_positive_operating_income")
    return _available(ppop / income * 100.0, "isb40 / isb38 * 100")


def validate_bank_loan_identity(rows, issuer_mapping_verified=False, tolerance=0.005):
    """Check net loans = gross loans + signed (negative) loan provision."""
    if not issuer_mapping_verified:
        return {"valid": False, "available": False, "reason": "issuer_mapping_not_verified"}
    values, conflicts = _index_rows(rows)
    fields = {"bsb103", "bsb104", "bsb105"}
    if conflicts.intersection(fields):
        return {"valid": False, "available": True, "reason": "conflicting_duplicate_source_field"}
    if not fields.issubset(values):
        return {"valid": False, "available": False, "reason": "missing_required_field"}
    net_value = values["bsb103"]
    expected = values["bsb104"] + values["bsb105"]
    allowed = max(abs(net_value) * tolerance, 1.0)
    difference = net_value - expected
    return {
        "valid": abs(difference) <= allowed,
        "available": True,
        "reason": None if abs(difference) <= allowed else "loan_identity_mismatch",
        "difference": difference,
        "tolerance": allowed,
    }


def calculate_bank_ldr(rows, issuer_mapping_verified=False):
    """LDR = gross customer loans / customer deposits * 100.

    The source fields are not assumed valid merely because their IDs exist in
    the dictionary; caller must attest issuer-level mapping verification.
    """
    if not issuer_mapping_verified:
        return _unavailable("issuer_mapping_not_verified")
    values, conflicts = _index_rows(rows)
    if conflicts.intersection({"bsb104", "bsb113"}):
        return _unavailable("conflicting_duplicate_source_field")
    loans, deposits = values.get("bsb104"), values.get("bsb113")
    if loans is None or deposits is None:
        return _unavailable("missing_required_field")
    if loans < 0:
        return _unavailable("negative_gross_customer_loans")
    if deposits <= 0:
        return _unavailable("non_positive_customer_deposits")
    return _available(loans / deposits * 100.0, "bsb104 / bsb113 * 100")


def get_unverified_bank_metrics():
    """Return a defensive copy of metrics that must remain unavailable."""
    return dict(UNVERIFIED_METRICS)
