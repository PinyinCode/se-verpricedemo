# -*- coding: utf-8 -*-
"""Read-only extraction of verified bank credit-growth observations for calibration.

Only aggregate proposals leave this module; issuer values are never serialized.
"""
from collections import Counter

from data_quality import parse_quarter
from financial_metric_adapter import build_bank_credit_growth_record
from metric_catalog import get_metric_definition
from threshold_calibration import calibrate_metric_thresholds


def _rows(doc):
    value = doc.get("balance_sheet") or []
    return value if isinstance(value, list) else []


def _source_is_vci(doc):
    source = doc.get("source")
    if isinstance(source, str):
        return "vci" in source.strip().lower()
    if isinstance(source, dict):
        values = (source.get("provider"), source.get("name"), source.get("source"))
        return any("vci" in str(value or "").strip().lower() for value in values)
    return False


def build_bank_credit_growth_calibration(stock_docs, bctc_by_symbol,
                                          minimum_sample=20):
    """Calculate real bank YoY gross-loan growth from exact VCI rows."""
    definition = get_metric_definition("CREDIT_GROWTH") or {}
    formula_status = str(definition.get("status") or "")
    if not formula_status.lower().startswith("verified"):
        return {
            "metric_id": "CREDIT_GROWTH",
            "status": "blocked",
            "blockers": ["catalog_formula_not_verified"],
            "observations_calculated": 0,
            "proposals_by_period": {},
        }

    candidates = Counter()
    valid = Counter()
    observations_by_period = {}
    reasons = Counter()

    for stock in stock_docs if isinstance(stock_docs, list) else []:
        symbol = str(stock.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        sector = str(stock.get("sector_profile") or "").strip().upper()
        if sector != "NGAN_HANG":
            continue
        candidates[sector] += 1
        bctc = bctc_by_symbol.get(symbol)
        if not isinstance(bctc, dict):
            reasons["missing_bctc"] += 1
            continue
        if not _source_is_vci(bctc):
            reasons["source_not_verified_vci"] += 1
            continue

        balance_rows = _rows(bctc)
        periods = []
        for row in balance_rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("source_field") or "").strip().lower() != "bsb104":
                continue
            period = str(row.get("period") or "").strip().upper()
            parsed = parse_quarter(period)
            if parsed:
                periods.append((parsed[0], parsed[1], period))
        if not periods:
            reasons["gross_loan_periods_missing"] += 1
            continue
        latest = max(periods)
        year, quarter, current_period = latest
        prior_period = f"{year - 1}-Q{quarter}"
        record = build_bank_credit_growth_record(
            balance_rows, current_period, prior_period,
            issuer_mapping_verified=True,
        )
        if record.get("available") is not True:
            reasons[str(record.get("reason") or "metric_unavailable")] += 1
            continue
        valid[sector] += 1
        observations_by_period.setdefault(current_period, []).append({
            "symbol": symbol,
            "value": record["value"],
            "formula_status": formula_status,
            "source_verified": True,
            "direction": "higher",
            "unit": record["unit"],
            "period_basis": record["period_basis"],
            "as_of": current_period,
        })

    proposals = {}
    for period, observations in sorted(observations_by_period.items()):
        proposals[period] = calibrate_metric_thresholds(
            observations, minimum_sample=minimum_sample
        )
    ready_periods = [
        period for period, proposal in proposals.items()
        if proposal.get("ready") is True
    ]
    return {
        "metric_id": "CREDIT_GROWTH",
        "formula_status": formula_status,
        "candidate_issuers": candidates.get("NGAN_HANG", 0),
        "calculated_issuers": valid.get("NGAN_HANG", 0),
        "blocked_reasons": dict(reasons),
        "observations_calculated": sum(len(rows) for rows in observations_by_period.values()),
        "periods_found": len(observations_by_period),
        "ready_periods": ready_periods,
        "proposals_by_period": proposals,
        "production_enabled": False,
        "approval_required": True,
    }
