# -*- coding: utf-8 -*-
"""Conservative discovery of bank asset-quality source fields.

This module finds candidate rows by their original VCI labels. It does not
assign canonical mappings or enable any scoring.
"""
import re
import unicodedata
from math import isfinite


_KEYWORDS = {
    # Ratios are separated from monetary NPL balances to prevent mixing units.
    "NPL_RATIO": (
        "ty le no xau", "no xau ratio", "npl ratio",
        "non-performing loan ratio",
    ),
    "NPL": (
        "no xau", "du no xau",
        "no duoi tieu chuan", "du no duoi tieu chuan",
        "no nghi ngo", "du no nghi ngo",
        "no co kha nang mat von", "du no co kha nang mat von",
        "non performing loan", "non-performing loan",
        "impaired loan", "bad debt",
    ),
    # Group 3-5 are NPL components, not automatically the consolidated NPL total.
    "NPL_GROUP_3": (
        "no nhom 3", "du no nhom 3", "group 3 loan", "group 3 debt",
    ),
    "NPL_GROUP_4": (
        "no nhom 4", "du no nhom 4", "group 4 loan", "group 4 debt",
    ),
    "NPL_GROUP_5": (
        "no nhom 5", "du no nhom 5", "group 5 loan", "group 5 debt",
    ),
    "GROUP_2": (
        "no nhom 2", "du no nhom 2", "no can chu y",
        "du no can chu y", "group 2 debt", "group 2 loan",
        "special mention loan", "special mention debt",
    ),
    "DEMAND_DEPOSITS": (
        "tien gui khong ky han", "tien gui thanh toan",
        "demand deposit", "demand deposits",
        "current account deposit", "current account deposits",
        "non-term customer deposit",
    ),
}


def _normalize(value):
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("đ", "d")
    return re.sub(r"\s+", " ", text).strip()


def classify_bank_candidate(name):
    """Return a candidate category only for explicit, high-specificity labels."""
    normalized = _normalize(name)
    for category, phrases in _KEYWORDS.items():
        if any(_normalize(phrase) in normalized for phrase in phrases):
            return category
    return None


def discover_bank_asset_quality_fields(rows, max_periods=12, max_fields=30):
    """Group original source rows by field and category, retaining period values.

    Conflicting duplicate values for the same source field/period are surfaced
    as conflicts; they are never silently overwritten.
    """
    if not isinstance(max_periods, int) or not 1 <= max_periods <= 12:
        raise ValueError("max_periods must be between 1 and 12")
    if not isinstance(max_fields, int) or not 1 <= max_fields <= 100:
        raise ValueError("max_fields must be between 1 and 100")

    candidates = {}
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        category = classify_bank_candidate(row.get("name"))
        if not category:
            continue
        source_field = str(row.get("source_field") or "").strip()
        period = str(row.get("period") or "").strip().upper()
        if not source_field or not re.fullmatch(r"\d{4}-Q[1-4]", period):
            continue
        try:
            value = float(row.get("value"))
        except (TypeError, ValueError, OverflowError):
            continue
        if not isfinite(value):
            continue
        key = (category, source_field)
        item = candidates.setdefault(key, {
            "category": category,
            "source_field": source_field,
            "id": row.get("id"),
            "name": row.get("name"),
            "unit": row.get("unit"),
            "period_values": {},
            "conflicting_periods": [],
        })
        previous = item["period_values"].get(period)
        if previous is not None and previous != value:
            if period not in item["conflicting_periods"]:
                item["conflicting_periods"].append(period)
        else:
            item["period_values"][period] = value

    result = []
    for item in candidates.values():
        periods = sorted(item["period_values"], reverse=True)[:max_periods]
        item["period_values"] = {
            period: item["period_values"][period] for period in periods
        }
        item["conflicting_periods"].sort(reverse=True)
        result.append(item)
    result.sort(key=lambda x: (x["category"], x["source_field"]))
    return {
        "candidate_count": len(result),
        "candidates": result[:max_fields],
        "truncated": len(result) > max_fields,
        "mapping_status": "DISCOVERY_ONLY_NOT_VERIFIED",
    }



def discover_bank_asset_quality_metadata(catalog, max_fields=100):
    """Find matching labels in the VCI metadata catalog, including fields
    that have no numeric value in the stored quarterly statements.
    """
    if not isinstance(max_fields, int) or not 1 <= max_fields <= 300:
        raise ValueError("max_fields must be between 1 and 300")

    results = []
    if not isinstance(catalog, dict):
        catalog = {}
    for section, fields in catalog.items():
        if str(section or "").strip().upper() != "BALANCE_SHEET":
            continue
        if not isinstance(fields, dict):
            continue
        for source_field, title in fields.items():
            category = classify_bank_candidate(title)
            if not category:
                continue
            results.append({
                "category": category,
                "source_section": str(section),
                "source_field": str(source_field),
                "name": str(title),
            })
    results.sort(key=lambda x: (x["category"], x["source_section"], x["source_field"]))
    return {
        "candidate_count": len(results),
        "candidates": results[:max_fields],
        "truncated": len(results) > max_fields,
        "mapping_status": "METADATA_DISCOVERY_ONLY_NOT_VERIFIED",
    }
