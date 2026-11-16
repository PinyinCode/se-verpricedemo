# -*- coding: utf-8 -*-
"""Conservative discovery of bank asset-quality source fields.

This module finds candidate rows by their original VCI labels. It does not
assign canonical mappings or enable any scoring.
"""
import re
import unicodedata


_KEYWORDS = {
    "NPL": (
        "no xau", "no duoi tieu chuan", "no nghi ngo",
        "no co kha nang mat von", "non performing loan",
        "non-performing loan", "impaired loan", "bad debt",
    ),
    "GROUP_2": (
        "no nhom 2", "group 2 debt", "group 2 loan",
        "special mention loan", "special mention debt",
    ),
    "DEMAND_DEPOSITS": (
        "tien gui khong ky han", "tien gui thanh toan",
        "demand deposit", "current account deposit",
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
        try:
            value = float(row.get("value"))
        except (TypeError, ValueError, OverflowError):
            continue
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
