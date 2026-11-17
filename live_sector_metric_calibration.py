# -*- coding: utf-8 -*-
"""Read-only live extraction/calibration for shared BCTC formulas across sectors.

Only aggregate counts and proposed quantile curves are returned. No issuer-level
values are serialized. Calibration never enables production scoring.
"""
from collections import Counter, defaultdict

from data_quality import parse_quarter
from financial_metric_adapter import build_industrial_metric_bundle, build_bank_metric_bundle
from metric_catalog import get_metric_definition, metric_is_supported_for_sector
from industry_profiles import INDUSTRY_PROFILES
from threshold_calibration import calibrate_metric_thresholds

CORE_NONBANK_METRICS = ("ROE", "ROA", "CFO", "CASH_CONVERSION")
CORE_BANK_METRICS = ("ROE", "ROA", "CIR")

def _metric_supported(metric_id, sector):
    # Retail technology profiles use the same canonical BCTC metric contract.
    catalog_sector = "BAN_LE" if sector == "CONG_NGHE_BAN_LE" else sector
    return metric_is_supported_for_sector(metric_id, catalog_sector)



def _source_is_verified(doc):
    source = doc.get("source")
    if isinstance(source, str):
        return source.strip().upper() == "VCI_DIRECT"
    if isinstance(source, dict):
        values = (source.get("provider"), source.get("name"), source.get("source"))
        return any(str(value or "").strip().upper() == "VCI_DIRECT" for value in values)
    return False


def _rows(doc, key):
    value = doc.get(key)
    return value if isinstance(value, list) else []


def _periods(rows):
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = parse_quarter(row.get("period"))
        if parsed:
            found[str(row["period"]).strip().upper()] = parsed
    return sorted(found, key=lambda p: found[p], reverse=True)


def _four_consecutive(period_list):
    if len(period_list) < 4:
        return []
    selected = period_list[:4]
    parsed = [parse_quarter(item) for item in selected]
    if any(item is None for item in parsed):
        return []
    ordinals = [year * 4 + quarter for year, quarter in parsed]
    if any(ordinals[index] - ordinals[index + 1] != 1 for index in range(3)):
        return []
    return list(reversed(selected))


def build_live_sector_formula_calibration(stock_docs, bctc_by_symbol,
                                          minimum_sample=20):
    """Build real ROE/ROA/CFO/cash-conversion records for supported nonbank sectors."""
    observations = defaultdict(list)
    candidates = Counter()
    calculated = Counter()
    reasons = Counter()
    supported_pairs = set()
    for profile_id in INDUSTRY_PROFILES:
        pool = CORE_BANK_METRICS if profile_id == "NGAN_HANG" else CORE_NONBANK_METRICS
        supported_pairs.update((profile_id, metric_id) for metric_id in pool
                               if _metric_supported(metric_id, profile_id))

    for stock in stock_docs if isinstance(stock_docs, list) else []:
        symbol = str(stock.get("symbol") or "").strip().upper()
        sector = str(stock.get("sector_profile") or "").strip().upper()
        if not symbol or not sector:
            continue
        pool = CORE_BANK_METRICS if sector == "NGAN_HANG" else CORE_NONBANK_METRICS
        sector_metrics = [metric_id for metric_id in pool
                          if _metric_supported(metric_id, sector)]
        for metric_id in sector_metrics:
            supported_pairs.add((sector, metric_id))
            candidates[(sector, metric_id)] += 1
        if not sector_metrics:
            continue
        bctc = bctc_by_symbol.get(symbol)
        if not isinstance(bctc, dict):
            for metric_id in sector_metrics:
                reasons[(sector, metric_id + ":missing_bctc_document")] += 1
            continue
        if not _source_is_verified(bctc):
            reasons[(sector, "source_not_verified_vci_direct")] += 1
            continue

        income = _rows(bctc, "income_statement")
        balance = _rows(bctc, "balance_sheet")
        cashflow = _rows(bctc, "cash_flow")
        income_periods = _four_consecutive(_periods(income))
        if not income_periods:
            reasons[(sector, "four_consecutive_income_quarters_missing")] += 1
            continue
        current_period = income_periods[-1]
        current_q = parse_quarter(current_period)
        prior_year_period = "%d-Q%d" % (current_q[0] - 1, current_q[1])
        # VCI income statement rows are quarter values; VCI cash-flow rows are
        # year-to-date and must be quarterized before TTM aggregation.
        try:
            if sector == "NGAN_HANG":
                metrics = build_bank_metric_bundle(
                    balance, income, current_period, prior_year_period,
                    income_periods, "QUARTERLY", issuer_mapping_verified=True,
                )
            else:
                metrics = build_industrial_metric_bundle(
                    balance, income, cashflow, current_period,
                    prior_year_period, income_periods, "QUARTERLY", "YTD",
                )
        except (TypeError, ValueError, KeyError, ZeroDivisionError):
            reasons[(sector, "metric_adapter_error")] += 1
            continue

        for metric_id in sector_metrics:
            record = metrics.get(metric_id) or {}
            if record.get("available") is not True:
                reasons[(sector, metric_id + ":" + str(record.get("reason") or "unavailable"))] += 1
                continue
            definition = get_metric_definition(metric_id) or {}
            if not str(definition.get("status") or "").lower().startswith("verified"):
                reasons[(sector, metric_id + ":catalog_formula_not_verified")] += 1
                continue
            source_fields = set(record.get("source_fields") or [])
            required_fields = set(definition.get("source_fields") or [])
            if not required_fields or not required_fields.issubset(source_fields):
                reasons[(sector, metric_id + ":source_lineage_mismatch")] += 1
                continue
            period_basis = str(record.get("period_basis") or "").upper()
            period_label = str(record.get("period") or "")
            if period_basis != "TTM" or not period_label.endswith(current_period):
                reasons[(sector, metric_id + ":period_contract_mismatch")] += 1
                continue
            observations[(sector, metric_id)].append({
                "symbol": symbol,
                "value": record["value"],
                "formula_status": definition["status"],
                "source_verified": True,
                "direction": "lower" if metric_id == "CIR" else "higher",
                "unit": record.get("unit") or definition.get("unit") or "",
                "period_basis": "TTM",
                "as_of": period_label,
            })
            calculated[(sector, metric_id)] += 1

    proposals = {}
    for sector, metric_id in sorted(supported_pairs):
        rows = observations.get((sector, metric_id), [])
        by_vintage = defaultdict(list)
        for row in rows:
            by_vintage[row["as_of"]].append(row)
        proposals.setdefault(sector, {})[metric_id] = {
            "candidate_issuers": candidates[(sector, metric_id)],
            "calculated_issuer_count": calculated[(sector, metric_id)],
            "vintages": {
                vintage: calibrate_metric_thresholds(
                    vintage_rows, minimum_sample=minimum_sample
                )
                for vintage, vintage_rows in sorted(by_vintage.items())
            },
        }
    ready = [
        (sector, metric_id, vintage)
        for sector, metrics in proposals.items()
        for metric_id, result in metrics.items()
        for vintage, proposal in result["vintages"].items()
        if proposal.get("ready") is True
    ]
    return {
        "metric_ids": {"bank": list(CORE_BANK_METRICS), "nonbank": list(CORE_NONBANK_METRICS)},
        "sector_count": len(proposals),
        "sector_metric_pair_count": sum(len(x) for x in proposals.values()),
        "calculated_observation_count": sum(calculated.values()),
        "ready_sector_metric_vintages": [
            {"sector": sector, "metric_id": metric_id, "vintage": vintage}
            for sector, metric_id, vintage in ready
        ],
        "blocked_reasons": {
            sector + ":" + reason: count
            for (sector, reason), count in sorted(reasons.items())
        },
        "by_sector": proposals,
        "minimum_sample": minimum_sample,
        "production_enabled": False,
        "approval_required": True,
        "source_policy": "VCI_DIRECT_only",
    }
