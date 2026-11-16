# -*- coding: utf-8 -*-
"""Discovery-only industrial BCTC source-field search.

Candidates are label matches for manual reconciliation. They never become
canonical mappings or score inputs automatically.
"""
import re
import unicodedata
from math import isfinite

from bctc_rules import resolve_vci_field_mapping

from bctc_rules import canonicalize_vci_field


_KEYWORDS = {
    "income_statement": {
        "NET_REVENUE": (
            "doanh thu thuan ve ban hang va cung cap dich vu",
            "net revenue from sales and services",
            "net sales and services",
        ),
        "COST_OF_GOODS_SOLD": (
            "gia von hang ban", "cost of goods sold",
        ),
        "GROSS_PROFIT": (
            "loi nhuan gop ve ban hang va cung cap dich vu",
            "gross profit",
        ),
        "FINANCIAL_EXPENSE": (
            "chi phi tai chinh", "finance costs", "financial expenses",
        ),
        "OPERATING_PROFIT": (
            "loi nhuan thuan tu hoat dong kinh doanh",
            "operating profit",
        ),
        "EBIT": (
            "loi nhuan truoc lai vay va thue",
            "loi nhuan truoc thue va lai vay",
            "earnings before interest and taxes",
        ),
        "EBITDA": (
            "loi nhuan truoc lai vay thue va khau hao",
            "loi nhuan truoc thue lai vay va khau hao",
            "earnings before interest taxes depreciation and amortization",
        ),
        "INTEREST_EXPENSE": (
            "chi phi lai vay",
            "interest expense",
            "interest on borrowings",
        ),
        "INCOME_TAX_EXPENSE": (
            "chi phi thue thu nhap doanh nghiep",
            "income tax expense",
        ),
    },
    "balance_sheet": {
        "CASH_AND_CASH_EQUIVALENTS": (
            "tien va cac khoan tuong duong tien",
            "cash and cash equivalents",
        ),
        "INVENTORY": (
            "hang ton kho", "inventories", "inventory",
        ),
        "SHORT_TERM_BORROWINGS": (
            "vay va no thue tai chinh ngan han",
            "short-term borrowings", "current borrowings",
        ),
        "LONG_TERM_BORROWINGS": (
            "vay va no thue tai chinh dai han",
            "long-term borrowings", "non-current borrowings",
        ),
        "TRADE_RECEIVABLES": (
            "phai thu khach hang", "trade receivables",
            "accounts receivable",
        ),
        "TRADE_PAYABLES": (
            "phai tra nguoi ban", "trade payables",
            "accounts payable",
        ),
    },
    "cash_flow": {
        "OPERATING_CASH_FLOW": (
            "luu chuyen tien thuan tu hoat dong kinh doanh",
            "net cash flows from operating activities",
            "net cash from operating activities",
        ),
        "CAPEX": (
            "tien chi de mua sam xay dung tscd",
            "purchase of fixed assets", "capital expenditures",
        ),
        "INTEREST_PAID": (
            "tien lai vay da tra", "interest paid",
        ),
        "DEPRECIATION_ADD_BACK": (
            "khau hao tai san co dinh",
            "depreciation and amortisation",
            "depreciation and amortization",
        ),
        "DIVIDENDS_PAID": (
            "tien chi tra co tuc",
            "dividends paid",
        ),
    },
}

_SECTION_ALIASES = {
    "BALANCE_SHEET": "balance_sheet",
    "INCOME_STATEMENT": "income_statement",
    "CASH_FLOW": "cash_flow",
    "CASHFLOW": "cash_flow",
}


def _normalize(value):
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("đ", "d")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _section(value):
    raw = str(value or "").strip()
    return _SECTION_ALIASES.get(raw.upper(), raw.lower())


def classify_industrial_candidate(name, section):
    """Classify only explicit labels inside the expected statement section."""
    section_name = _section(section)
    categories = _KEYWORDS.get(section_name, {})
    normalized = _normalize(name)
    for category, phrases in categories.items():
        if any(_normalize(phrase) in normalized for phrase in phrases):
            return category
    return None


def discover_industrial_fields(rows_by_section, max_periods=12, max_fields=100):
    """Group numeric candidates by section, category and original VCI field."""
    if not isinstance(max_periods, int) or not 1 <= max_periods <= 12:
        raise ValueError("max_periods must be between 1 and 12")
    if not isinstance(max_fields, int) or not 1 <= max_fields <= 300:
        raise ValueError("max_fields must be between 1 and 300")
    if not isinstance(rows_by_section, dict):
        rows_by_section = {}

    candidates = {}
    for raw_section, rows in rows_by_section.items():
        section = _section(raw_section)
        if section not in _KEYWORDS:
            continue
        for row in rows or ():
            if not isinstance(row, dict):
                continue
            category = classify_industrial_candidate(row.get("name"), section)
            source_field = str(row.get("source_field") or "").strip()
            period = str(row.get("period") or "").strip().upper()
            if not category or not source_field or not re.fullmatch(r"\d{4}-Q[1-4]", period):
                continue
            try:
                value = float(row.get("value"))
            except (TypeError, ValueError, OverflowError):
                continue
            if not isfinite(value):
                continue
            key = (section, category, source_field)
            mapping = resolve_vci_field_mapping(
                section, source_field, row.get("name")
            )
            item = candidates.setdefault(key, {
                "source_section": section,
                "category": category,
                "source_field": source_field,
                "id": row.get("id"),
                "name": row.get("name"),
                "unit": row.get("unit"),
                "canonical_candidate_id": mapping.get("canonical_id"),
                "canonical_mapping_method": mapping.get("mapping_method"),
                "canonical_mapping_status": mapping.get("mapping_status"),
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
    result.sort(key=lambda x: (
        x["source_section"], x["category"], x["source_field"]
    ))
    return {
        "candidate_count": len(result),
        "candidates": result[:max_fields],
        "truncated": len(result) > max_fields,
        "mapping_status": "DISCOVERY_ONLY_NOT_VERIFIED",
    }


def annotate_industrial_dictionary_candidates(discovery):
    """Attach canonical dictionary matches as hints, never as verified mappings."""
    result = dict(discovery or {})
    annotated = []
    for original in result.get("candidates", []):
        item = dict(original)
        canonical = canonicalize_vci_field(
            item.get("source_section"),
            item.get("source_field"),
            item.get("name"),
        )
        item["canonical_candidate"] = canonical
        item["dictionary_match_status"] = (
            "CANDIDATE_ONLY" if canonical else "NO_CANONICAL_MATCH"
        )
        annotated.append(item)
    result["candidates"] = annotated
    result["mapping_status"] = "DISCOVERY_ONLY_NOT_VERIFIED"
    return result


def discover_industrial_metadata(catalog, max_fields=300):
    """Find candidate labels from VCI metadata, including empty numeric fields."""
    if not isinstance(max_fields, int) or not 1 <= max_fields <= 1000:
        raise ValueError("max_fields must be between 1 and 1000")
    if not isinstance(catalog, dict):
        catalog = {}
    results = []
    for raw_section, fields in catalog.items():
        section = _section(raw_section)
        if section not in _KEYWORDS or not isinstance(fields, dict):
            continue
        for source_field, title in fields.items():
            category = classify_industrial_candidate(title, section)
            if category:
                mapping = resolve_vci_field_mapping(section, source_field, title)
                results.append({
                    "source_section": section,
                    "category": category,
                    "source_field": str(source_field),
                    "name": str(title),
                    "canonical_candidate_id": mapping.get("canonical_id"),
                    "canonical_mapping_method": mapping.get("mapping_method"),
                    "canonical_mapping_status": mapping.get("mapping_status"),
                })
    results.sort(key=lambda x: (
        x["source_section"], x["category"], x["source_field"]
    ))
    return {
        "candidate_count": len(results),
        "candidates": results[:max_fields],
        "truncated": len(results) > max_fields,
        "mapping_status": "METADATA_DISCOVERY_ONLY_NOT_VERIFIED",
    }
