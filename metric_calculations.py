# -*- coding: utf-8 -*-
"""Pure financial metric calculations with explicit period-basis safeguards.

No database, AI, pandas, or numpy dependency. Every function returns an
availability flag and a stable reason so missing/invalid values cannot be
silently converted into zero or treated as a valid metric.
"""
from math import isfinite
import re


_PERIOD_RE = re.compile(r"^(\d{4})-Q([1-4])$")


def _finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def _result(value=None, reason=""):
    return {
        "available": value is not None,
        "value": value,
        "reason": reason,
    }


def safe_ratio(numerator, denominator, scale=1.0,
               denominator_policy="positive", decimals=6):
    """Calculate numerator / denominator * scale with explicit denominator rules."""
    num = _finite(numerator)
    den = _finite(denominator)
    factor = _finite(scale)
    if num is None or den is None or factor is None:
        return _result(reason="missing_or_invalid_input")
    if denominator_policy not in ("positive", "non_zero"):
        raise ValueError("unsupported denominator_policy")
    if denominator_policy == "positive" and den <= 0:
        return _result(reason="denominator_not_positive")
    if denominator_policy == "non_zero" and den == 0:
        return _result(reason="denominator_is_zero")
    value = num / den * factor
    if not isfinite(value):
        return _result(reason="non_finite_calculation")
    return _result(round(value, decimals))


def calculate_margin(profit, revenue, decimals=4):
    """Profit margin in percent; revenue must be positive."""
    return safe_ratio(profit, revenue, 100.0, "positive", decimals)


def calculate_growth(current, prior, decimals=4):
    """Growth in percent; prior period must be positive to avoid misleading signs."""
    current_value = _finite(current)
    prior_value = _finite(prior)
    if current_value is None or prior_value is None:
        return _result(reason="missing_or_invalid_input")
    if prior_value <= 0:
        return _result(reason="prior_period_not_positive")
    return _result(round((current_value - prior_value) / prior_value * 100.0,
                         decimals))


def _quarter_ordinal(period):
    match = _PERIOD_RE.fullmatch(str(period or "").strip().upper())
    if not match:
        return None
    return int(match.group(1)) * 4 + int(match.group(2))


def calculate_yoy_growth(current, current_period, prior, prior_period,
                          period_basis, decimals=4):
    """Compare same fiscal quarter one year apart; basis must be standalone quarterly."""
    if str(period_basis or "").strip().upper() != "QUARTERLY":
        return _result(reason="period_basis_not_verified_as_quarterly")
    current_ordinal = _quarter_ordinal(current_period)
    prior_ordinal = _quarter_ordinal(prior_period)
    if current_ordinal is None or prior_ordinal is None:
        return _result(reason="invalid_reporting_period")
    if current_ordinal - prior_ordinal != 4:
        return _result(reason="periods_not_same_quarter_year_apart")
    return calculate_growth(current, prior, decimals)


def calculate_ttm_sum(values_by_period, periods, period_basis, decimals=2):
    """Sum exactly four consecutive standalone quarters; never sum YTD values."""
    if str(period_basis or "").strip().upper() != "QUARTERLY":
        return _result(reason="period_basis_not_verified_as_quarterly")
    normalized = [str(item or "").strip().upper() for item in (periods or [])]
    if len(normalized) != 4:
        return _result(reason="ttm_requires_exactly_four_quarters")
    ordinals = [_quarter_ordinal(item) for item in normalized]
    if any(item is None for item in ordinals):
        return _result(reason="invalid_reporting_period")
    ordered = sorted(ordinals)
    if len(set(ordered)) != 4 or any(
        right - left != 1 for left, right in zip(ordered, ordered[1:])
    ):
        return _result(reason="ttm_periods_not_unique_and_consecutive")

    normalized_values = {}
    for period, value in (values_by_period or {}).items():
        key = str(period or "").strip().upper()
        normalized_values[key] = _finite(value)
    selected_periods = [period for period in normalized if period in normalized_values]
    if len(selected_periods) != 4:
        return _result(reason="ttm_quarter_value_missing")
    values = [normalized_values[period] for period in selected_periods]
    if any(value is None for value in values):
        return _result(reason="ttm_quarter_value_invalid")
    total = sum(values)
    if not isfinite(total):
        return _result(reason="non_finite_calculation")
    return _result(round(total, decimals))


def calculate_ytd_to_quarter(current_ytd, prior_ytd, quarter_number,
                             period_basis, decimals=2):
    """Convert YTD to standalone quarter only when both inputs are verified YTD.

    Q1 is already the standalone quarter. Q2-Q4 require the previous YTD value.
    """
    if str(period_basis or "").strip().upper() != "YTD":
        return _result(reason="period_basis_not_verified_as_ytd")
    try:
        quarter = int(quarter_number)
    except (TypeError, ValueError):
        return _result(reason="invalid_quarter_number")
    if quarter not in (1, 2, 3, 4):
        return _result(reason="invalid_quarter_number")
    current = _finite(current_ytd)
    if current is None:
        return _result(reason="current_ytd_missing_or_invalid")
    if quarter == 1:
        return _result(round(current, decimals))
    prior = _finite(prior_ytd)
    if prior is None:
        return _result(reason="prior_ytd_missing_or_invalid")
    result = current - prior
    if not isfinite(result):
        return _result(reason="non_finite_calculation")
    return _result(round(result, decimals))



# General non-financial-company metrics. These functions accept only values
# whose source fields, period basis, units and signs were verified upstream.


def calculate_roe_ttm(parent_profit_ttm, average_parent_equity,
                      profit_basis, decimals=4):
    """Parent-attributable TTM profit / average parent equity, in percent."""
    if str(profit_basis or "").strip().upper() != "TTM":
        return _result(reason="profit_period_basis_not_verified_as_ttm")
    return safe_ratio(parent_profit_ttm, average_parent_equity,
                      100.0, "positive", decimals)


def calculate_roa_ttm(net_profit_ttm, average_total_assets,
                      profit_basis, decimals=4):
    """TTM net profit / average total assets, in percent."""
    if str(profit_basis or "").strip().upper() != "TTM":
        return _result(reason="profit_period_basis_not_verified_as_ttm")
    return safe_ratio(net_profit_ttm, average_total_assets,
                      100.0, "positive", decimals)


def calculate_roic_ttm(nopat_ttm, average_invested_capital,
                       profit_basis, decimals=4):
    """NOPAT TTM / average invested capital, in percent."""
    if str(profit_basis or "").strip().upper() != "TTM":
        return _result(reason="nopat_period_basis_not_verified_as_ttm")
    return safe_ratio(nopat_ttm, average_invested_capital,
                      100.0, "positive", decimals)


def calculate_cash_conversion(cfo_ttm, net_profit_ttm,
                              period_basis, decimals=4):
    """CFO / positive net profit; not intended for banks or insurers."""
    if str(period_basis or "").strip().upper() != "TTM":
        return _result(reason="period_basis_not_verified_as_ttm")
    profit = _finite(net_profit_ttm)
    if profit is None or profit <= 0:
        return _result(reason="net_profit_not_positive")
    return safe_ratio(cfo_ttm, profit, 1.0, "positive", decimals)


def calculate_inventory_turnover(cogs_ttm, average_inventory,
                                 period_basis, cogs_sign="negative",
                                 decimals=4):
    """TTM cost of goods sold / average inventory, in turns."""
    if str(period_basis or "").strip().upper() != "TTM":
        return _result(reason="cogs_period_basis_not_verified_as_ttm")
    cogs = _finite(cogs_ttm)
    if cogs is None:
        return _result(reason="missing_or_invalid_input")
    if cogs_sign == "negative":
        if cogs > 0:
            return _result(reason="cogs_sign_mismatch")
        cogs = abs(cogs)
    elif cogs_sign == "positive":
        if cogs < 0:
            return _result(reason="cogs_sign_mismatch")
    elif cogs_sign == "absolute":
        cogs = abs(cogs)
    else:
        raise ValueError("unsupported cogs_sign")
    return safe_ratio(cogs, average_inventory, 1.0, "positive", decimals)


def calculate_interest_coverage(ebit_ttm, interest_expense_ttm,
                                period_basis, expense_sign="negative",
                                decimals=4):
    """EBIT / interest expense; expense sign must be explicit."""
    if str(period_basis or "").strip().upper() != "TTM":
        return _result(reason="period_basis_not_verified_as_ttm")
    ebit = _finite(ebit_ttm)
    expense = _finite(interest_expense_ttm)
    if ebit is None or expense is None:
        return _result(reason="missing_or_invalid_input")
    if expense_sign == "negative":
        if expense > 0:
            return _result(reason="interest_expense_sign_mismatch")
        expense = abs(expense)
    elif expense_sign == "positive":
        if expense < 0:
            return _result(reason="interest_expense_sign_mismatch")
    elif expense_sign == "absolute":
        expense = abs(expense)
    else:
        raise ValueError("unsupported expense_sign")
    return safe_ratio(ebit, expense, 1.0, "positive", decimals)


def calculate_net_debt_to_ebitda(total_interest_bearing_debt,
                                 cash_and_equivalents, ebitda_ttm,
                                 ebitda_basis, decimals=4):
    """(interest-bearing debt - cash) / positive EBITDA TTM."""
    if str(ebitda_basis or "").strip().upper() != "TTM":
        return _result(reason="ebitda_period_basis_not_verified_as_ttm")
    debt = _finite(total_interest_bearing_debt)
    cash = _finite(cash_and_equivalents)
    ebitda = _finite(ebitda_ttm)
    if debt is None or cash is None or ebitda is None:
        return _result(reason="missing_or_invalid_input")
    if debt < 0 or cash < 0:
        return _result(reason="debt_or_cash_negative")
    if ebitda <= 0:
        return _result(reason="ebitda_not_positive")
    return _result(round((debt - cash) / ebitda, decimals))


# Bank-specific ratios. These helpers do not establish that a source field
# exists: callers must supply values only after field/unit/period validation.


def calculate_bank_ldr(gross_customer_loans, customer_deposits, decimals=4):
    """Gross customer loans / customer deposits, in percent."""
    return safe_ratio(
        gross_customer_loans, customer_deposits, 100.0, "positive", decimals
    )


def calculate_bank_cir(operating_expenses, operating_income,
                       expense_sign="negative", decimals=4):
    """Bank CIR in percent; default expects expense rows stored as negatives."""
    expense = _finite(operating_expenses)
    income = _finite(operating_income)
    if expense is None or income is None:
        return _result(reason="missing_or_invalid_input")
    if expense_sign == "negative":
        if expense > 0:
            return _result(reason="expense_sign_mismatch")
        expense = abs(expense)
    elif expense_sign == "positive":
        if expense < 0:
            return _result(reason="expense_sign_mismatch")
    elif expense_sign == "absolute":
        expense = abs(expense)
    else:
        raise ValueError("unsupported expense_sign")
    return safe_ratio(expense, income, 100.0, "positive", decimals)


def calculate_bank_ppop_margin(ppop, operating_income, decimals=4):
    """Pre-provision operating profit / total operating income, in percent."""
    return calculate_margin(ppop, operating_income, decimals)


def calculate_bank_non_interest_income_share(
    total_operating_income, net_interest_income, decimals=4
):
    """(total operating income - net interest income) / total operating income."""
    total = _finite(total_operating_income)
    nii = _finite(net_interest_income)
    if total is None or nii is None:
        return _result(reason="missing_or_invalid_input")
    if total <= 0:
        return _result(reason="denominator_not_positive")
    return _result(round((total - nii) / total * 100.0, decimals))


def calculate_bank_npl_ratio(non_performing_loans, gross_customer_loans,
                             decimals=4):
    """NPL / gross loans; caller must provide verified regulatory NPL amount."""
    return safe_ratio(
        non_performing_loans, gross_customer_loans, 100.0, "positive", decimals
    )


def calculate_bank_group2_ratio(group_2_loans, gross_customer_loans,
                                decimals=4):
    """Group-2 loans / gross loans; caller must provide verified group-2 amount."""
    return safe_ratio(
        group_2_loans, gross_customer_loans, 100.0, "positive", decimals
    )


def calculate_bank_npl_coverage(loan_loss_provisions, non_performing_loans,
                                provision_sign="negative", decimals=4):
    """Loan-loss provisions / NPL in percent; source provision is usually negative."""
    provision = _finite(loan_loss_provisions)
    npl = _finite(non_performing_loans)
    if provision is None or npl is None:
        return _result(reason="missing_or_invalid_input")
    if provision_sign == "negative":
        if provision > 0:
            return _result(reason="provision_sign_mismatch")
        provision = abs(provision)
    elif provision_sign == "positive":
        if provision < 0:
            return _result(reason="provision_sign_mismatch")
    elif provision_sign == "absolute":
        provision = abs(provision)
    else:
        raise ValueError("unsupported provision_sign")
    return safe_ratio(provision, npl, 100.0, "positive", decimals)


def calculate_bank_credit_growth(current_gross_loans, current_period,
                                 prior_gross_loans, prior_period,
                                 decimals=4):
    """Gross-loan YoY growth with exact same-quarter comparison."""
    current_ordinal = _quarter_ordinal(current_period)
    prior_ordinal = _quarter_ordinal(prior_period)
    if current_ordinal is None or prior_ordinal is None:
        return _result(reason="invalid_reporting_period")
    if current_ordinal - prior_ordinal != 4:
        return _result(reason="periods_not_same_quarter_year_apart")
    return calculate_growth(
        current_gross_loans, prior_gross_loans, decimals
    )


def calculate_bank_nim(net_interest_income, average_interest_earning_assets,
                       income_basis, decimals=4):
    """NIM in percent. TTM income is not re-annualized; quarterly income x4.

    The average interest-earning-assets denominator must already be supplied
    from verified period-end balances. This helper cannot derive it from total
    assets or customer loans.
    """
    basis = str(income_basis or "").strip().upper()
    if basis == "TTM":
        annualization = 1.0
    elif basis == "QUARTERLY":
        annualization = 4.0
    else:
        return _result(reason="income_period_basis_not_verified")
    return safe_ratio(
        net_interest_income, average_interest_earning_assets,
        100.0 * annualization, "positive", decimals
    )
