# -*- coding: utf-8 -*-
"""Read-only expanded operating metrics from canonical VCI BCTC rows.

Only aggregate counts are exported. Canonical IDs are produced by the
explicit VCI field/title dictionary; raw issuer values never leave this module.
"""
from collections import Counter, defaultdict
from data_quality import parse_quarter

INCOME_FIELDS = {
    "IS_NET_REVENUE": "net_revenue",
    "IS_GROSS_PROFIT": "gross_profit",
    "IS_OPERATING_PROFIT": "operating_profit",
    "IS_NET_PROFIT_AFTER_TAX": "net_profit",
}
BALANCE_FIELDS = {
    "BS_LIABILITIES": "total_liabilities",
    "BS_OWNERS_EQUITY": "owners_equity",
}

def _period_key(value):
    parsed = parse_quarter(value)
    return (parsed[0] * 4 + parsed[1]) if parsed else None

def _series(rows, canonical_id, expected_section):
    result = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or str(row.get("id") or "").upper() != canonical_id:
            continue
        if str(row.get("source_section") or "").lower() not in ("", expected_section):
            continue
        period = str(row.get("period") or "").strip().upper()
        if _period_key(period) is None or not row.get("source_field"):
            continue
        try:
            value = float(row.get("value"))
        except (TypeError, ValueError, OverflowError):
            continue
        if value != value or abs(value) == float("inf"):
            continue
        # Duplicate canonical rows for a period are ambiguous: quarantine period.
        if period in result:
            result[period] = None
        else:
            result[period] = value
    return {period: value for period, value in result.items() if value is not None}

def _consecutive_latest(periods, count):
    valid = sorted(
        (( _period_key(period), period) for period in periods),
        reverse=True,
    )
    if len(valid) < count:
        return []
    selected = valid[:count]
    ordinals = [item[0] for item in selected]
    if any(ordinals[i] - ordinals[i + 1] != 1 for i in range(count - 1)):
        return []
    return [period for _ordinal, period in reversed(selected)]

def _safe_ratio(numerator, denominator, multiplier=1.0):
    if denominator == 0:
        return None
    value = numerator / denominator * multiplier
    if value != value or abs(value) == float("inf"):
        return None
    return value

def build_expanded_operating_metrics(stock_docs, bctc_by_symbol):
    """Calculate only ratios whose canonical BCTC source IDs are explicitly mapped."""
    counts = Counter()
    candidates = Counter()
    vintages = defaultdict(Counter)
    source_coverage = Counter()
    for stock in stock_docs if isinstance(stock_docs, list) else []:
        symbol = str(stock.get("symbol") or "").strip().upper()
        sector = str(stock.get("sector_profile") or "").strip().upper()
        if not symbol or not sector:
            continue
        candidates[sector] += 1
        doc = bctc_by_symbol.get(symbol)
        if not isinstance(doc, dict):
            counts[(sector, "missing_bctc")] += 1
            continue
        source = doc.get("source")
        verified = (
            isinstance(source, str) and source.strip().upper() == "VCI_DIRECT"
        ) or (
            isinstance(source, dict) and any(
                str(source.get(key) or "").strip().upper() == "VCI_DIRECT"
                for key in ("provider", "name", "source")
            )
        )
        if not verified:
            counts[(sector, "source_not_vci_direct")] += 1
            continue
        income = doc.get("income_statement") or []
        balance = doc.get("balance_sheet") or []
        series = {
            key: _series(income, key, "income_statement")
            for key in INCOME_FIELDS
        }
        balance_series = {
            key: _series(balance, key, "balance_sheet")
            for key in BALANCE_FIELDS
        }
        for key, values in {**series, **balance_series}.items():
            if values:
                source_coverage[(sector, key)] += 1

        revenue = series["IS_NET_REVENUE"]
        gross = series["IS_GROSS_PROFIT"]
        operating = series["IS_OPERATING_PROFIT"]
        net_profit = series["IS_NET_PROFIT_AFTER_TAX"]
        income_periods = _consecutive_latest(revenue.keys(), 8)
        if income_periods:
            recent, prior = income_periods[4:], income_periods[:4]
            # Ordered oldest to newest; prior TTM then current TTM.
            recent_revenue = sum(revenue[p] for p in recent)
            prior_revenue = sum(revenue[p] for p in prior)
            value = _safe_ratio(recent_revenue - prior_revenue, prior_revenue, 100.0)
            if value is not None:
                counts[(sector, "REVENUE_GROWTH_TTM_YOY")] += 1
                vintages[(sector, "REVENUE_GROWTH_TTM_YOY")][income_periods[-1]] += 1

        periods4 = _consecutive_latest(revenue.keys(), 4)
        if periods4:
            revenue_ttm = sum(revenue[p] for p in periods4)
            for metric_id, numerator_series in (
                ("GROSS_MARGIN_TTM", gross),
                ("OPERATING_MARGIN_TTM", operating),
                ("NET_PROFIT_MARGIN_TTM", net_profit),
            ):
                if all(p in numerator_series for p in periods4):
                    value = _safe_ratio(sum(numerator_series[p] for p in periods4),
                                        revenue_ttm, 100.0)
                    if value is not None:
                        counts[(sector, metric_id)] += 1
                        vintages[(sector, metric_id)][periods4[-1]] += 1

        liabilities = balance_series["BS_LIABILITIES"]
        equity = balance_series["BS_OWNERS_EQUITY"]
        common_periods = set(liabilities).intersection(equity)
        latest = sorted(common_periods, key=_period_key, reverse=True)
        if latest:
            period = latest[0]
            value = _safe_ratio(liabilities[period], equity[period], 100.0)
            if value is not None:
                counts[(sector, "LIABILITIES_TO_EQUITY")] += 1
                vintages[(sector, "LIABILITIES_TO_EQUITY")][period] += 1

    sectors = sorted(candidates.keys())
    metrics = (
        "REVENUE_GROWTH_TTM_YOY", "GROSS_MARGIN_TTM",
        "OPERATING_MARGIN_TTM", "NET_PROFIT_MARGIN_TTM",
        "LIABILITIES_TO_EQUITY",
    )
    report = {}
    for sector in sectors:
        report[sector] = {
            metric: {
                "candidate_issuers": candidates[sector],
                "calculated_issuers": counts[(sector, metric)],
                "vintages": dict(vintages[(sector, metric)]),
            }
            for metric in metrics
        }
    return {
        "source_policy": "VCI_DIRECT_only",
        "canonical_source_ids": {
            "income_statement": list(INCOME_FIELDS),
            "balance_sheet": list(BALANCE_FIELDS),
        },
        "metric_count": len(metrics),
        "calculated_observation_count": sum(
            counts[(sector, metric)] for sector in sectors for metric in metrics
        ),
        "source_coverage": {
            sector + ":" + field: count
            for (sector, field), count in sorted(source_coverage.items())
        },
        "by_sector": report,
        "production_enabled": False,
        "notes": {
            "revenue_growth": "Current TTM versus prior TTM; requires eight consecutive quarters.",
            "margins": "TTM numerator divided by TTM net revenue; all four quarters must be present.",
            "leverage": "Closing total liabilities divided by closing owners equity; point-in-time.",
            "inventory_turnover": "Not calculated: no exact inventory source mapping is approved.",
        },
    }
