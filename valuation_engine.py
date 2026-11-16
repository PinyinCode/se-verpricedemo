# -*- coding: utf-8 -*-
"""Deterministic valuation formulas with explicit, verified inputs only.

No default fair multiples, growth rates, discount rates, or forecasts are
embedded. The caller must provide assumptions and source lineage.
"""
from math import isfinite
import re


VALUATION_ENGINE_VERSION = "1.0.0"


def _finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _verified_input(payload, required_basis=None):
    if not isinstance(payload, dict):
        return None, "input_missing"
    if payload.get("available") is not True:
        return None, payload.get("reason") or "input_unavailable"
    if payload.get("quality_status") != "verified":
        return None, "input_quality_not_verified"
    if not payload.get("source_fields"):
        return None, "source_lineage_missing"
    if not str(payload.get("period") or "").strip():
        return None, "period_missing"
    if not str(payload.get("unit") or "").strip():
        return None, "unit_missing"
    basis = str(payload.get("period_basis") or "").strip().upper()
    if required_basis and basis != required_basis:
        return None, "period_basis_mismatch_or_unverified"
    value = _finite(payload.get("value"))
    if value is None:
        return None, "invalid_numeric_input"
    return value, ""


def _unavailable(method, reason):
    return {
        "method": method,
        "available": False,
        "value_per_share": None,
        "reason": reason,
        "engine_version": VALUATION_ENGINE_VERSION,
    }


def calculate_pe_value(eps_ttm, fair_pe):
    """EPS TTM × explicitly supplied fair P/E; fair P/E is never guessed."""
    eps, reason = _verified_input(eps_ttm, "TTM")
    if reason:
        return _unavailable("PE", "eps_" + reason)
    multiple = _finite(fair_pe)
    if multiple is None or multiple <= 0:
        return _unavailable("PE", "fair_pe_must_be_positive_and_explicit")
    if str(eps_ttm.get("unit") or "").strip().lower() != "vnd/share":
        return _unavailable("PE", "eps_unit_must_be_vnd_per_share")
    if eps <= 0:
        return _unavailable("PE", "eps_not_positive")
    return {
        "method": "PE", "available": True,
        "value_per_share": round(eps * multiple, 4),
        "inputs": {"eps_ttm": eps, "fair_pe": multiple},
        "source_fields": list(eps_ttm.get("source_fields") or []),
        "period_basis": "TTM",
        "reason": "",
        "engine_version": VALUATION_ENGINE_VERSION,
    }


def calculate_pb_value(book_value_per_share, fair_pb):
    """Book value per share × explicitly supplied fair P/B."""
    bvps, reason = _verified_input(book_value_per_share, "POINT_IN_TIME")
    if reason:
        return _unavailable("PB", "book_value_per_share_" + reason)
    multiple = _finite(fair_pb)
    if multiple is None or multiple <= 0:
        return _unavailable("PB", "fair_pb_must_be_positive_and_explicit")
    if str(book_value_per_share.get("unit") or "").strip().lower() != "vnd/share":
        return _unavailable("PB", "book_value_unit_must_be_vnd_per_share")
    if bvps <= 0:
        return _unavailable("PB", "book_value_per_share_not_positive")
    return {
        "method": "PB", "available": True,
        "value_per_share": round(bvps * multiple, 4),
        "inputs": {"book_value_per_share": bvps, "fair_pb": multiple},
        "source_fields": list(book_value_per_share.get("source_fields") or []),
        "period_basis": "POINT_IN_TIME",
        "reason": "",
        "engine_version": VALUATION_ENGINE_VERSION,
    }


def calculate_dcf_value(forecast_fcf, discount_rate, terminal_growth,
                        net_debt, diluted_shares):
    """DCF per share using explicit annual FCF forecasts and terminal growth.

    Rates are decimals (0.10 means 10%). Forecasts must use consecutive
    fiscal-year labels and a common currency. net_debt is debt less cash;
    negative net debt means net cash. No forecast or rate is assumed here.
    """
    if not isinstance(forecast_fcf, (list, tuple)) or not forecast_fcf:
        return _unavailable("DCF", "forecast_fcf_missing")
    cashflows = []
    source_fields = set()
    forecast_unit = None
    forecast_years = []
    for index, item in enumerate(forecast_fcf):
        value, reason = _verified_input(item, "ANNUAL_FORECAST")
        if reason:
            return _unavailable("DCF", "forecast_year_" + str(index + 1) + "_" + reason)
        period_match = re.fullmatch(
            r"(\d{4})-FY", str(item.get("period") or "").strip().upper()
        )
        if not period_match:
            return _unavailable("DCF", "forecast_period_must_be_explicit_fiscal_year")
        forecast_years.append(int(period_match.group(1)))
        unit = str(item.get("unit") or "").strip()
        if not unit:
            return _unavailable("DCF", "forecast_cashflow_unit_missing")
        if forecast_unit is None:
            forecast_unit = unit
        elif unit.lower() != forecast_unit.lower():
            return _unavailable("DCF", "forecast_cashflow_units_mismatch")
        cashflows.append(value)
        source_fields.update(item.get("source_fields") or [])
    if any(right - left != 1 for left, right in zip(forecast_years, forecast_years[1:])):
        return _unavailable("DCF", "forecast_years_not_consecutive")
    rate = _finite(discount_rate)
    growth = _finite(terminal_growth)
    debt, debt_reason = _verified_input(net_debt, "POINT_IN_TIME")
    shares, shares_reason = _verified_input(diluted_shares, "POINT_IN_TIME")
    debt_unit = str(net_debt.get("unit") or "").strip() if isinstance(net_debt, dict) else ""
    shares_unit = str(diluted_shares.get("unit") or "").strip().lower() if isinstance(diluted_shares, dict) else ""
    if not debt_unit or debt_unit.lower() != forecast_unit.lower():
        return _unavailable("DCF", "net_debt_unit_missing_or_mismatched")
    net_debt_period = str(net_debt.get("period") or "").strip() if isinstance(net_debt, dict) else ""
    shares_period = str(diluted_shares.get("period") or "").strip() if isinstance(diluted_shares, dict) else ""
    if not net_debt_period or shares_period != net_debt_period:
        return _unavailable("DCF", "net_debt_and_share_periods_must_match")
    if shares_unit not in ("shares", "share", "cổ phiếu", "co phieu"):
        return _unavailable("DCF", "diluted_shares_unit_must_be_shares")
    if rate is None or growth is None:
        return _unavailable("DCF", "discount_rate_and_terminal_growth_required")
    if rate <= 0 or growth < 0 or growth >= rate:
        return _unavailable("DCF", "require_discount_rate_positive_and_terminal_growth_below_discount_rate")
    if debt_reason:
        return _unavailable("DCF", "net_debt_" + debt_reason)
    if shares_reason:
        return _unavailable("DCF", "diluted_shares_" + shares_reason)
    if shares <= 0:
        return _unavailable("DCF", "diluted_shares_not_positive")
    if cashflows[-1] <= 0:
        return _unavailable("DCF", "terminal_fcf_must_be_positive")
    present_value = sum(cf / ((1.0 + rate) ** year)
                        for year, cf in enumerate(cashflows, start=1))
    terminal_value = cashflows[-1] * (1.0 + growth) / (rate - growth)
    present_value += terminal_value / ((1.0 + rate) ** len(cashflows))
    equity_value = present_value - debt
    per_share = equity_value / shares
    if not isfinite(per_share):
        return _unavailable("DCF", "non_finite_calculation")
    return {
        "method": "DCF", "available": True,
        "value_per_share": round(per_share, 4),
        "inputs": {
            "forecast_years": len(cashflows),
            "discount_rate": rate,
            "terminal_growth": growth,
            "net_debt": debt,
            "diluted_shares": shares,
        },
        "source_fields": sorted(source_fields
                                | set(net_debt.get("source_fields") or [])
                                | set(diluted_shares.get("source_fields") or [])),
        "period_basis": "EXPLICIT_ANNUAL_FORECAST",
        "reason": "",
        "engine_version": VALUATION_ENGINE_VERSION,
    }


def compare_to_market_price(valuation, market_price):
    """Return a neutral price gap; does not produce a buy/sell instruction."""
    if not isinstance(valuation, dict) or valuation.get("available") is not True:
        return {"available": False, "discount_pct": None,
                "reason": "valuation_unavailable"}
    fair_value = _finite(valuation.get("value_per_share"))
    price = _finite(market_price)
    if fair_value is None or fair_value <= 0:
        return {"available": False, "discount_pct": None,
                "reason": "fair_value_not_positive"}
    if price is None or price <= 0:
        return {"available": False, "discount_pct": None,
                "reason": "market_price_not_positive"}
    return {
        "available": True,
        "fair_value_per_share": fair_value,
        "market_price": price,
        "discount_pct": round((fair_value - price) / fair_value * 100.0, 2),
        "reason": "",
    }
