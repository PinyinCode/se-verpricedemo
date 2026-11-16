# -*- coding: utf-8 -*-
"""Formula adapters that turn verified VCI rows into canonical metric records."""
from copy import deepcopy

from data_quality import parse_quarter
from metric_calculations import (
    calculate_bank_credit_growth,
    calculate_bank_ldr,
    calculate_cash_conversion,
    calculate_roe_ttm,
    calculate_roa_ttm,
    calculate_ttm_sum,
    safe_ratio,
)
from bctc_metric_adapter import (
    build_ttm_metric_record,
    extract_source_value,
    extract_quarter_series,
)
from metric_contract import build_metric_record


def _ttm_period_label(periods):
    normalized = sorted(str(period) for period in (periods or []) if period)
    return "TTM through " + (normalized[-1] if normalized else "unknown_period")


def _average_balance(rows, field, current_period, prior_period):
    current = extract_source_value(rows, field, current_period)
    prior = extract_source_value(rows, field, prior_period)
    if not current["available"]:
        return None, current["reason"], None
    if not prior["available"]:
        return None, prior["reason"], None
    if current["unit"].lower() != prior["unit"].lower():
        return None, "average_balance_units_mismatch", None
    current_q = parse_quarter(current_period)
    prior_q = parse_quarter(prior_period)
    if not current_q or not prior_q:
        return None, "invalid_reporting_period", None
    current_ordinal = current_q[0] * 4 + current_q[1]
    prior_ordinal = prior_q[0] * 4 + prior_q[1]
    if current_ordinal - prior_ordinal != 4:
        return None, "balance_periods_not_one_year_apart", None
    if current["value"] <= 0 or prior["value"] <= 0:
        return None, "average_balance_not_positive", None
    average = (current["value"] + prior["value"]) / 2.0
    return average, "", current["unit"]


def build_return_metric_record(metric_id, income_rows, profit_field,
                               balance_rows, balance_field, periods,
                               current_period, prior_year_period,
                               income_period_basis, return_type="ROE"):
    """Build ROE/ROA from TTM profit and average comparable balance."""
    profit = build_ttm_metric_record(
        metric_id + "_PROFIT_TTM", income_rows, profit_field,
        periods, income_period_basis,
    )
    if not profit["available"]:
        return build_metric_record(
            metric_id, {"available": False, "reason": profit["reason"]},
            "percent", "TTM through " + str(current_period), "TTM",
            [profit_field, balance_field],
        )
    average, reason, balance_unit = _average_balance(
        balance_rows, balance_field, current_period, prior_year_period
    )
    if reason:
        return build_metric_record(
            metric_id, {"available": False, "reason": reason},
            "percent", "TTM through " + str(current_period), "TTM",
            [profit_field, balance_field],
        )
    if str(profit.get("unit") or "").lower() != str(balance_unit or "").lower():
        return build_metric_record(
            metric_id,
            {"available": False, "reason": "profit_and_balance_units_mismatch"},
            "percent", "TTM through " + str(current_period), "TTM",
            [profit_field, balance_field],
        )
    if return_type.upper() == "ROE":
        calculation = calculate_roe_ttm(profit["value"], average, "TTM")
    elif return_type.upper() == "ROA":
        calculation = calculate_roa_ttm(profit["value"], average, "TTM")
    else:
        raise ValueError("return_type must be ROE or ROA")
    return build_metric_record(
        metric_id, calculation, "percent",
        "TTM through " + str(current_period), "TTM",
        [profit_field, balance_field],
    )


def build_bank_ldr_record(balance_rows, period, issuer_mapping_verified=False):
    loans = extract_source_value(balance_rows, "bsb104", period)
    deposits = extract_source_value(balance_rows, "bsb113", period)
    if not loans["available"] or not deposits["available"]:
        reason = loans.get("reason") if not loans["available"] else deposits.get("reason")
        calculation = {"available": False, "reason": reason}
    elif loans["unit"].lower() != deposits["unit"].lower():
        calculation = {"available": False, "reason": "source_units_mismatch"}
    else:
        calculation = calculate_bank_ldr(
            loans["value"], deposits["value"]
        ) if issuer_mapping_verified else {
            "available": False, "reason": "issuer_mapping_not_verified"
        }
    return build_metric_record(
        "LDR", calculation, "percent", period, "POINT_IN_TIME",
        ["bsb104", "bsb113"],
    )


def build_bank_credit_growth_record(balance_rows, current_period,
                                    prior_year_period,
                                    issuer_mapping_verified=False):
    current = extract_source_value(balance_rows, "bsb104", current_period)
    prior = extract_source_value(balance_rows, "bsb104", prior_year_period)
    if not current["available"] or not prior["available"]:
        reason = current.get("reason") if not current["available"] else prior.get("reason")
        calculation = {"available": False, "reason": reason}
    elif current["unit"].lower() != prior["unit"].lower():
        calculation = {"available": False, "reason": "source_units_mismatch"}
    elif not issuer_mapping_verified:
        calculation = {"available": False, "reason": "issuer_mapping_not_verified"}
    else:
        calculation = calculate_bank_credit_growth(
            current["value"], current_period, prior["value"], prior_year_period
        )
    return build_metric_record(
        "CREDIT_GROWTH", calculation, "percent", current_period,
        "YOY_POINT_IN_TIME", ["bsb104"],
    )


def build_ttm_ratio_record(metric_id, numerator_rows, numerator_field,
                           denominator_rows, denominator_field, periods,
                           source_period_basis, scale=100.0,
                           numerator_sign="as_is"):
    """Build a same-basis TTM ratio, optionally normalizing negative expenses."""
    numerator = extract_quarter_series(
        numerator_rows, numerator_field, periods, source_period_basis
    )
    denominator = extract_quarter_series(
        denominator_rows, denominator_field, periods, source_period_basis
    )
    reason = ""
    if not numerator["available"]:
        reason = "numerator_" + numerator["reason"]
    elif not denominator["available"]:
        reason = "denominator_" + denominator["reason"]
    elif numerator["unit"].lower() != denominator["unit"].lower():
        reason = "source_units_mismatch"
    if reason:
        calculation = {"available": False, "reason": reason}
    else:
        numerator_ttm = calculate_ttm_sum(
            numerator["values_by_period"], periods, "QUARTERLY"
        )
        denominator_ttm = calculate_ttm_sum(
            denominator["values_by_period"], periods, "QUARTERLY"
        )
        if not numerator_ttm["available"] or not denominator_ttm["available"]:
            calculation = {"available": False, "reason": "ttm_calculation_unavailable"}
        else:
            value = numerator_ttm["value"]
            if numerator_sign == "negative_expense":
                if value > 0:
                    calculation = {"available": False, "reason": "expense_sign_mismatch"}
                else:
                    value = abs(value)
                    calculation = safe_ratio(value, denominator_ttm["value"], scale)
            elif numerator_sign == "as_is":
                calculation = safe_ratio(value, denominator_ttm["value"], scale)
            else:
                raise ValueError("unsupported numerator_sign")
    return build_metric_record(
        metric_id, calculation, "percent",
        _ttm_period_label(periods), "TTM",
        [numerator_field, denominator_field],
    )


def build_average_balance_turnover_record(
        metric_id, flow_rows, flow_field, balance_rows, balance_field,
        periods, current_period, prior_year_period, flow_period_basis,
        flow_sign="as_is", issuer_mapping_verified=False):
    """Build TTM flow / average point-in-time balance after explicit mapping review."""
    if not issuer_mapping_verified:
        calculation = {"available": False, "reason": "issuer_mapping_not_verified"}
        return build_metric_record(
            metric_id, calculation, "times",
            "TTM through " + str(current_period), "TTM",
            [flow_field, balance_field],
        )
    flow = build_ttm_metric_record(
        metric_id + "_FLOW_TTM", flow_rows, flow_field,
        periods, flow_period_basis,
    )
    if not flow["available"]:
        calculation = {"available": False, "reason": flow["reason"]}
    else:
        average, reason, balance_unit = _average_balance(
            balance_rows, balance_field, current_period, prior_year_period
        )
        if reason:
            calculation = {"available": False, "reason": reason}
        elif str(flow.get("unit") or "").lower() != str(balance_unit or "").lower():
            calculation = {"available": False, "reason": "flow_and_balance_units_mismatch"}
        else:
            numerator = flow["value"]
            if flow_sign == "negative_expense":
                if numerator >= 0:
                    calculation = {"available": False, "reason": "expense_sign_mismatch"}
                else:
                    calculation = safe_ratio(abs(numerator), average, 1.0)
            elif flow_sign == "as_is":
                if numerator <= 0:
                    calculation = {"available": False, "reason": "flow_not_positive"}
                else:
                    calculation = safe_ratio(numerator, average, 1.0)
            else:
                raise ValueError("flow_sign must be as_is or negative_expense")
    return build_metric_record(
        metric_id, calculation, "times",
        "TTM through " + str(current_period), "TTM",
        [flow_field, balance_field],
    )


def build_interest_coverage_record(income_rows, ebit_field,
                                   interest_expense_field, periods,
                                   income_period_basis,
                                   issuer_mapping_verified=False):
    """Build EBIT / absolute interest expense with strict negative-expense policy."""
    metric_id = "INTEREST_COVERAGE"
    source_fields = [ebit_field, interest_expense_field]
    if not issuer_mapping_verified:
        calculation = {"available": False, "reason": "issuer_mapping_not_verified"}
    else:
        ebit = build_ttm_metric_record(
            "EBIT_TTM", income_rows, ebit_field, periods, income_period_basis
        )
        interest = build_ttm_metric_record(
            "INTEREST_EXPENSE_TTM", income_rows, interest_expense_field,
            periods, income_period_basis,
        )
        if not ebit["available"]:
            calculation = {"available": False, "reason": "ebit_" + ebit["reason"]}
        elif not interest["available"]:
            calculation = {"available": False, "reason": "interest_" + interest["reason"]}
        elif str(ebit.get("unit") or "").lower() != str(interest.get("unit") or "").lower():
            calculation = {"available": False, "reason": "source_units_mismatch"}
        elif ebit["value"] <= 0:
            calculation = {"available": False, "reason": "ebit_not_positive"}
        elif interest["value"] >= 0:
            calculation = {"available": False, "reason": "interest_expense_sign_mismatch"}
        else:
            calculation = safe_ratio(ebit["value"], abs(interest["value"]), 1.0)
    return build_metric_record(
        metric_id, calculation, "times",
        _ttm_period_label(periods),
        "TTM", source_fields,
    )


def build_net_debt_to_ebitda_record(balance_rows, ebitda_rows,
                                    short_debt_field, long_debt_field,
                                    cash_field, ebitda_field, period,
                                    periods, ebitda_period_basis,
                                    issuer_mapping_verified=False):
    """Build (short + long debt - cash) / positive EBITDA TTM."""
    metric_id = "NET_DEBT_TO_EBITDA"
    source_fields = [
        short_debt_field, long_debt_field, cash_field, ebitda_field,
    ]
    if not issuer_mapping_verified:
        calculation = {"available": False, "reason": "issuer_mapping_not_verified"}
    else:
        short_debt = extract_source_value(balance_rows, short_debt_field, period)
        long_debt = extract_source_value(balance_rows, long_debt_field, period)
        cash = extract_source_value(balance_rows, cash_field, period)
        ebitda = build_ttm_metric_record(
            "EBITDA_TTM", ebitda_rows, ebitda_field,
            periods, ebitda_period_basis,
        )
        source_values = (short_debt, long_debt, cash)
        missing = next((item for item in source_values if not item["available"]), None)
        if missing:
            calculation = {"available": False, "reason": missing["reason"]}
        elif not ebitda["available"]:
            calculation = {"available": False, "reason": "ebitda_" + ebitda["reason"]}
        elif len({
            short_debt["unit"].lower(), long_debt["unit"].lower(),
            cash["unit"].lower(), str(ebitda.get("unit") or "").lower(),
        }) != 1:
            calculation = {"available": False, "reason": "source_units_mismatch"}
        elif any(item["value"] < 0 for item in source_values):
            calculation = {"available": False, "reason": "debt_or_cash_sign_mismatch"}
        elif ebitda["value"] <= 0:
            calculation = {"available": False, "reason": "ebitda_not_positive"}
        else:
            net_debt = short_debt["value"] + long_debt["value"] - cash["value"]
            calculation = safe_ratio(net_debt, ebitda["value"], 1.0)
    return build_metric_record(
        metric_id, calculation, "times",
        "TTM through " + str(period), "TTM", source_fields,
    )


def build_free_cash_flow_record(cashflow_rows, cfo_field, capex_field,
                                periods, cashflow_period_basis,
                                capex_sign="negative_outflow",
                                issuer_mapping_verified=False):
    """Build CFO less capex; capex source sign must be explicitly verified."""
    metric_id = "FCF"
    source_fields = [cfo_field, capex_field]
    output_unit = ""
    if not issuer_mapping_verified:
        calculation = {"available": False, "reason": "issuer_mapping_not_verified"}
    else:
        cfo = build_ttm_metric_record(
            "CFO_TTM", cashflow_rows, cfo_field, periods, cashflow_period_basis
        )
        output_unit = cfo.get("unit") or ""
        capex = build_ttm_metric_record(
            "CAPEX_TTM", cashflow_rows, capex_field, periods, cashflow_period_basis
        )
        if not cfo["available"]:
            calculation = {"available": False, "reason": "cfo_" + cfo["reason"]}
        elif not capex["available"]:
            calculation = {"available": False, "reason": "capex_" + capex["reason"]}
        elif str(cfo.get("unit") or "").lower() != str(capex.get("unit") or "").lower():
            calculation = {"available": False, "reason": "source_units_mismatch"}
        elif capex_sign == "negative_outflow":
            if capex["value"] > 0:
                calculation = {"available": False, "reason": "capex_sign_mismatch"}
            else:
                calculation = {"available": True, "value": cfo["value"] + capex["value"]}
        elif capex_sign == "positive_outflow":
            if capex["value"] < 0:
                calculation = {"available": False, "reason": "capex_sign_mismatch"}
            else:
                calculation = {"available": True, "value": cfo["value"] - capex["value"]}
        else:
            raise ValueError("capex_sign must be negative_outflow or positive_outflow")
    return build_metric_record(
        metric_id, calculation, output_unit,
        _ttm_period_label(periods), "TTM", source_fields,
    )


def build_bank_non_interest_income_share(income_rows, periods,
                                         income_period_basis):
    """(total operating income - net interest income) / total income."""
    total = extract_quarter_series(
        income_rows, "isb38", periods, income_period_basis
    )
    nii = extract_quarter_series(
        income_rows, "isb27", periods, income_period_basis
    )
    reason = ""
    if not total["available"]:
        reason = "total_income_" + total["reason"]
    elif not nii["available"]:
        reason = "net_interest_income_" + nii["reason"]
    elif total["unit"].lower() != nii["unit"].lower():
        reason = "source_units_mismatch"
    if reason:
        calculation = {"available": False, "reason": reason}
    else:
        total_ttm = calculate_ttm_sum(
            total["values_by_period"], periods, "QUARTERLY"
        )
        nii_ttm = calculate_ttm_sum(
            nii["values_by_period"], periods, "QUARTERLY"
        )
        if not total_ttm["available"] or not nii_ttm["available"]:
            calculation = {"available": False, "reason": "ttm_calculation_unavailable"}
        elif total_ttm["value"] <= 0:
            calculation = {"available": False, "reason": "total_operating_income_not_positive"}
        else:
            calculation = safe_ratio(
                total_ttm["value"] - nii_ttm["value"],
                total_ttm["value"], 100.0,
            )
    return build_metric_record(
        "BANK_NON_INTEREST_INCOME_SHARE", calculation, "percent",
        _ttm_period_label(periods), "TTM",
        ["isb38", "isb27"],
    )


def build_bank_metric_bundle(balance_sheet_rows, income_statement_rows,
                             current_period, prior_year_period,
                             ttm_periods, income_period_basis,
                             issuer_mapping_verified=False):
    """Build available bank metrics plus explicit unavailable asset-quality gaps."""
    metrics = {
        "ROE": build_return_metric_record(
            "ROE", income_statement_rows, "isa20",
            balance_sheet_rows, "bsa78", ttm_periods,
            current_period, prior_year_period, income_period_basis, "ROE",
        ),
        "ROA": build_return_metric_record(
            "ROA", income_statement_rows, "isa20",
            balance_sheet_rows, "bsa53", ttm_periods,
            current_period, prior_year_period, income_period_basis, "ROA",
        ),
        "LDR": build_bank_ldr_record(
            balance_sheet_rows, current_period, issuer_mapping_verified
        ),
        "CREDIT_GROWTH": build_bank_credit_growth_record(
            balance_sheet_rows, current_period, prior_year_period,
            issuer_mapping_verified,
        ),
        "CIR": build_ttm_ratio_record(
            "CIR", income_statement_rows, "isb39",
            income_statement_rows, "isb38", ttm_periods,
            income_period_basis, 100.0, "negative_expense",
        ),
        "BANK_PPOP_MARGIN": build_ttm_ratio_record(
            "BANK_PPOP_MARGIN", income_statement_rows, "isb40",
            income_statement_rows, "isb38", ttm_periods,
            income_period_basis,
        ),
        "BANK_NON_INTEREST_INCOME_SHARE": build_bank_non_interest_income_share(
            income_statement_rows, ttm_periods, income_period_basis
        ),
    }

    if not issuer_mapping_verified:
        for metric_id, source_fields in {
            "LDR": ["bsb104", "bsb113"],
            "CREDIT_GROWTH": ["bsb104"],
            "CIR": ["isb39", "isb38"],
            "BANK_PPOP_MARGIN": ["isb40", "isb38"],
            "BANK_NON_INTEREST_INCOME_SHARE": ["isb38", "isb27"],
        }.items():
            metrics[metric_id] = build_metric_record(
                metric_id,
                {"available": False, "reason": "issuer_mapping_not_verified"},
                "percent", current_period,
                "POINT_IN_TIME" if metric_id in ("LDR", "CREDIT_GROWTH") else "TTM",
                source_fields,
            )

    unavailable = {
        "NIM": ("Average interest-earning assets are not verified.", ["isb27"]),
        "NPL_RATIO": ("NPL numerator source field is not verified.", ["bsb104"]),
        "GROUP_2_RATIO": ("Group-2 loan source field is not verified.", ["bsb104"]),
        "NPL_COVERAGE": ("NPL and matching provision basis are not verified.", []),
        "CAR": ("Regulatory capital and risk-weighted assets are unavailable.", []),
        "CASA": ("Demand-deposit source field is not verified.", ["bsb113"]),
    }
    for metric_id, (reason, source_fields) in unavailable.items():
        metrics[metric_id] = build_metric_record(
            metric_id, {"available": False, "reason": reason},
            "percent", current_period, "POINT_IN_TIME", source_fields,
        )
    return metrics


def quarantine_metrics(metrics, metric_ids, reason):
    """Return copied metric records with quality-dependent inputs blocked."""
    result = deepcopy(metrics or {})
    for metric_id in metric_ids:
        if metric_id not in result:
            continue
        record = result[metric_id]
        record["value"] = None
        record["available"] = False
        record["quality_status"] = "unavailable"
        record["reason"] = str(reason)
    return result


def build_industrial_metric_bundle(balance_sheet_rows, income_statement_rows,
                                   cash_flow_rows, current_period,
                                   prior_year_period, ttm_periods,
                                   income_period_basis,
                                   cashflow_period_basis):
    """Build core non-financial-company metrics without inventing sector data."""
    metrics = {
        "ROE": build_return_metric_record(
            "ROE", income_statement_rows, "isa20",
            balance_sheet_rows, "bsa78", ttm_periods,
            current_period, prior_year_period, income_period_basis, "ROE",
        ),
        "ROA": build_return_metric_record(
            "ROA", income_statement_rows, "isa20",
            balance_sheet_rows, "bsa53", ttm_periods,
            current_period, prior_year_period, income_period_basis, "ROA",
        ),
        "CASH_CONVERSION": build_cash_conversion_record(
            cash_flow_rows, income_statement_rows, ttm_periods,
            cashflow_period_basis, income_period_basis,
        ),
        "CFO": build_ttm_metric_record(
            "CFO", cash_flow_rows, "cfa18", ttm_periods,
            cashflow_period_basis,
        ),
    }
    unavailable = {
        "ROIC": ("NOPAT and invested-capital components are not verified.", []),
        "INVENTORY_TURNOVER": ("Inventory source field and cost basis are not verified.", []),
        "INTEREST_COVERAGE": ("EBIT and interest-expense mapping are not verified.", []),
        "NET_DEBT_TO_EBITDA": ("Interest-bearing debt and EBITDA components are not verified.", []),
        "FCF": ("Cash capex completeness and sign policy are not verified.", ["cfa18", "cfa19"]),
    }
    for metric_id, (reason, source_fields) in unavailable.items():
        metrics[metric_id] = build_metric_record(
            metric_id, {"available": False, "reason": reason},
            "ratio", "TTM through " + str(current_period), "TTM",
            source_fields,
        )
    return metrics


def build_cash_conversion_record(cashflow_rows, income_rows, periods,
                                 cashflow_period_basis,
                                 income_period_basis):
    cfo = build_ttm_metric_record(
        "CFO_TTM", cashflow_rows, "cfa18", periods, cashflow_period_basis
    )
    profit = build_ttm_metric_record(
        "NET_PROFIT_TTM", income_rows, "isa20", periods, income_period_basis
    )
    if not cfo["available"] or not profit["available"]:
        reason = cfo["reason"] if not cfo["available"] else profit["reason"]
        calculation = {"available": False, "reason": reason}
    elif str(cfo.get("unit") or "").lower() != str(profit.get("unit") or "").lower():
        calculation = {"available": False, "reason": "cashflow_and_profit_units_mismatch"}
    else:
        calculation = calculate_cash_conversion(
            cfo["value"], profit["value"], "TTM"
        )
    return build_metric_record(
        "CASH_CONVERSION", calculation, "ratio",
        _ttm_period_label(periods), "TTM",
        ["cfa18", "isa20"],
    )
