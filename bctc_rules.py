# -*- coding: utf-8 -*-
"""VCI BCTC dictionary and sector rules.

Unknown VCI fields are deliberately retained as raw fields. This module never
infers a financial meaning from a numeric suffix alone.
"""
import re
import unicodedata

def normalize_label(value):
    text = str(value or "").strip().lower()
    text = "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    ).replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()

# Exact source-field mappings verified from VCI metadata / user-provided catalog.
VCI_FIELD_MAP = {
    "balance_sheet": {
        "bsa1": "BS_SHORT_TERM_ASSETS",
        "bsa2": "BS_CASH_AND_GOLD",
        "bsa23": "BS_LONG_TERM_ASSETS",
        "bsa29": "BS_FIXED_ASSETS",
        "bsa30": "BS_TANGIBLE_FIXED_ASSETS",
        "bsa31": "BS_TANGIBLE_FIXED_ASSETS_COST",
        "bsa32": "BS_TANGIBLE_FIXED_ASSETS_DEPRECIATION",
        "bsa33": "BS_FINANCE_LEASE_ASSETS",
        "bsa34": "BS_FINANCE_LEASE_ASSETS_COST",
        "bsa35": "BS_FINANCE_LEASE_ASSETS_DEPRECIATION",
        "bsa36": "BS_INTANGIBLE_FIXED_ASSETS",
        "bsa37": "BS_INTANGIBLE_FIXED_ASSETS_COST",
        "bsa38": "BS_INTANGIBLE_FIXED_ASSETS_AMORTIZATION",
        "bsa40": "BS_INVESTMENT_PROPERTY",
        "bsa41": "BS_INVESTMENT_PROPERTY_COST",
        "bsa42": "BS_INVESTMENT_PROPERTY_DEPRECIATION",
        "bsa43": "BS_LONG_TERM_INVESTMENTS",
        "bsa44": "BS_INVESTMENTS_IN_SUBSIDIARIES",
        "bsa45": "BS_INVESTMENTS_IN_JOINT_VENTURES",
        "bsa46": "BS_OTHER_LONG_TERM_INVESTMENTS",
        "bsa47": "BS_PROVISION_FOR_LONG_TERM_INVESTMENTS",
        "bsa53": "BS_TOTAL_ASSETS",
        "bsa54": "BS_LIABILITIES",
        "bsa78": "BS_OWNERS_EQUITY",
        "bsa80": "BS_CHARTER_CAPITAL",
        "bsa81": "BS_SHARE_PREMIUM",
        "bsa82": "BS_OTHER_CAPITAL",
        "bsa83": "BS_TREASURY_SHARES",
        "bsa84": "BS_ASSET_REVALUATION_RESERVE",
        "bsa85": "BS_FX_DIFFERENCES",
        "bsa90": "BS_RETAINED_EARNINGS",
        "bsa95": "BS_NON_CONTROLLING_INTEREST_PRE_2015",
        "bsa96": "BS_TOTAL_LIABILITIES_AND_EQUITY",
        "bsa210": "BS_NON_CONTROLLING_INTEREST",
        "bsa276": "BS_JOINT_VENTURE_CAPITAL",
        "bsa277": "BS_INVESTMENTS_IN_ASSOCIATES",
        "bsb100": "BS_TRADING_SECURITIES",
        "bsb101": "BS_PROVISION_FOR_TRADING_SECURITIES",
        "bsb102": "BS_DERIVATIVE_AND_OTHER_FINANCIAL_ASSETS",
        # VCI trả hai dòng cùng tên "Cho vay khách hàng". Chưa đủ căn cứ
        # xác nhận bsb103 là dư nợ gộp hay dư nợ sau dự phòng.
        "bsb103": "BS_CUSTOMER_LOANS_UNVERIFIED",
        "bsb104": "BS_CUSTOMER_LOANS_DETAIL_UNVERIFIED",
        "bsb105": "BS_PROVISION_FOR_CUSTOMER_LOANS",
        "bsb106": "BS_INVESTMENT_SECURITIES",
        "bsb107": "BS_AVAILABLE_FOR_SALE_SECURITIES",
        "bsb108": "BS_HELD_TO_MATURITY_SECURITIES",
        "bsb109": "BS_PROVISION_FOR_INVESTMENT_SECURITIES",
        "bsb110": "BS_OTHER_ASSETS_BANK",
        "bsb111": "BS_DUE_TO_GOVERNMENT_AND_CENTRAL_BANK",
        "bsb112": "BS_DUE_TO_CREDIT_INSTITUTIONS",
        "bsb113": "BS_CUSTOMER_DEPOSITS",
        "bsb114": "BS_DERIVATIVE_AND_OTHER_FINANCIAL_LIABILITIES",
        "bsb115": "BS_TRUSTED_FUNDS",
        "bsb116": "BS_VALUABLE_PAPERS_ISSUED",
        "bsb117": "BS_OTHER_LIABILITIES_BANK",
        "bsb118": "BS_EQUITY_OF_CREDIT_INSTITUTION",
        "bsb119": "BS_CONSTRUCTION_INVESTMENT_CAPITAL",
        "bsb120": "BS_PREFERENCE_SHARES",
        "bsb121": "BS_FUNDS_OF_CREDIT_INSTITUTION",
    },
    "income_statement": {
        "isa16": "IS_PROFIT_BEFORE_TAX",
        "isa17": "IS_CURRENT_INCOME_TAX",
        "isa18": "IS_DEFERRED_INCOME_TAX",
        "isa19": "IS_TOTAL_INCOME_TAX",
        "isa20": "IS_NET_PROFIT_AFTER_TAX",
        "isa21": "IS_NON_CONTROLLING_INTEREST_PROFIT",
        "isa22": "IS_NET_PROFIT_ATTRIBUTABLE",
        "isa23": "IS_BASIC_EPS",
        "isa24": "IS_DILUTED_EPS",
        "isb25": "IS_INTEREST_INCOME",
        "isb26": "IS_INTEREST_EXPENSE",
        "isb27": "IS_NET_INTEREST_INCOME",
        "isb28": "IS_SERVICE_INCOME",
        "isb29": "IS_SERVICE_EXPENSE",
        "isb30": "IS_NET_SERVICE_INCOME",
        "isb31": "IS_NET_FX_INCOME",
        "isb32": "IS_NET_TRADING_SECURITIES_INCOME",
        "isb33": "IS_NET_INVESTMENT_SECURITIES_INCOME",
        "isb34": "IS_OTHER_INCOME_BANK",
        "isb35": "IS_OTHER_EXPENSE_BANK",
        "isb36": "IS_NET_OTHER_INCOME_BANK",
        "isb37": "IS_DIVIDEND_INCOME",
        "isb38": "IS_TOTAL_OPERATING_INCOME_BANK",
        # VCI có thể đặt nhãn "Chi phí quản lý doanh nghiệp"; chỉ dùng cho CIR
        # khi metadata xác nhận đúng dòng "Chi phí hoạt động".
        "isb39": "IS_OPERATING_EXPENSE_BANK_UNVERIFIED",
        "isb40": "IS_PROFIT_BEFORE_CREDIT_PROVISIONS",
        "isb41": "IS_CREDIT_LOSS_PROVISION",
    },
    "cash_flow": {
        "cfa15": "CF_INCOME_TAX_PAID",
        "cfa18": "CF_NET_CASH_FLOWS_FROM_OPERATING_ACTIVITIES",
        "cfa19": "CF_PURCHASE_OF_FIXED_ASSETS",
        "cfa20": "CF_PROCEEDS_FROM_DISPOSAL_OF_FIXED_ASSETS",
        "cfa23": "CF_INVESTMENTS_IN_OTHER_ENTITIES",
        "cfa24": "CF_PROCEEDS_FROM_INVESTMENTS_IN_OTHER_ENTITIES",
        "cfa25": "CF_DIVIDENDS_AND_PROFIT_DISTRIBUTIONS_RECEIVED",
        "cfa26": "CF_NET_CASH_FLOWS_FROM_INVESTING_ACTIVITIES",
        "cfa27": "CF_PROCEEDS_FROM_SHARE_ISSUANCE",
        "cfa32": "CF_DIVIDENDS_PAID",
        "cfa34": "CF_NET_CHANGE_IN_CASH",
        "cfa36": "CF_CASH_AT_BEGINNING",
        "cfa37": "CF_FX_EFFECT_ON_CASH",
        "cfa38": "CF_CASH_AT_END",
        "cfa43": "CF_INCOME_TAX_PAID_IN_PERIOD",
        "cfb48": "CF_BANK_CASH_AT_CENTRAL_BANK",
        "cfb49": "CF_BANK_CHANGE_DUE_FROM_CREDIT_INSTITUTIONS",
        "cfb50": "CF_BANK_CHANGE_TRADING_SECURITIES",
        "cfb51": "CF_BANK_CHANGE_DERIVATIVE_ASSETS",
        "cfb52": "CF_BANK_CHANGE_CUSTOMER_LOANS",
        "cfb53": "CF_BANK_CHANGE_INTEREST_RECEIVABLE",
        "cfb54": "CF_BANK_CHANGE_PROVISIONS",
        "cfb55": "CF_BANK_CHANGE_OTHER_OPERATING_ASSETS",
        "cfb56": "CF_BANK_CHANGE_GOVERNMENT_AND_CENTRAL_BANK_FUNDING",
        "cfb57": "CF_BANK_CHANGE_DUE_TO_CREDIT_INSTITUTIONS",
        "cfb58": "CF_BANK_CHANGE_CUSTOMER_DEPOSITS",
        "cfb59": "CF_BANK_CHANGE_DERIVATIVE_LIABILITIES",
        "cfb60": "CF_BANK_CHANGE_TRUSTED_FUNDS",
        "cfb61": "CF_BANK_CHANGE_VALUABLE_PAPERS",
        "cfb62": "CF_BANK_CHANGE_INTEREST_PAYABLE",
        "cfb63": "CF_BANK_CHANGE_OTHER_OPERATING_LIABILITIES",
        "cfb64": "CF_BANK_OPERATING_CASH_BEFORE_TAX",
        "cfb65": "CF_BANK_FUNDS_PAID",
        "cfb66": "CF_BANK_RECOVERY_OF_BAD_DEBTS",
        "cfb67": "CF_BANK_DISPOSAL_OF_FIXED_ASSETS",
        "cfb68": "CF_BANK_PURCHASE_OF_INVESTMENT_PROPERTY",
        "cfb69": "CF_BANK_PROCEEDS_FROM_INVESTMENT_PROPERTY",
        "cfb70": "CF_BANK_PAYMENT_FOR_INVESTMENT_PROPERTY_DISPOSAL",
        "cfb71": "CF_BANK_PROCEEDS_FROM_LONG_TERM_SECURITIES_AND_BORROWINGS",
        "cfb72": "CF_BANK_REPAYMENT_OF_LONG_TERM_SECURITIES_AND_BORROWINGS",
        "cfb73": "CF_BANK_PURCHASE_OF_TREASURY_SHARES",
        "cfb74": "CF_BANK_PROCEEDS_FROM_TREASURY_SHARES",
        "cfb75": "CF_BANK_INTEREST_RECEIVED",
        "cfb76": "CF_BANK_INTEREST_PAID",
        "cfb77": "CF_BANK_SERVICE_INCOME_RECEIVED",
        "cfb78": "CF_BANK_NET_FX_INCOME",
        "cfb79": "CF_BANK_SECURITIES_TRADING_INCOME",
        "cfb80": "CF_BANK_OTHER_INCOME",
        "cfb81": "CF_BANK_STAFF_AND_MANAGEMENT_PAYMENTS",
    },
}

# These labels are mapped only when the wording is sufficiently specific.
TITLE_RULES = {
    "balance_sheet": [
        (("no xau", "non performing loans"), "BS_NON_PERFORMING_LOANS"),
        (("no nhom 2", "group 2 debt"), "BS_GROUP_2_DEBT"),
        (("du phong rui ro tin dung", "loan loss provision"), "BS_PROVISION_FOR_CUSTOMER_LOANS"),
        (("tong no phai tra va von chu so huu", "liabilities and equity"), "BS_TOTAL_LIABILITIES_AND_EQUITY"),
        (("von chu so huu", "owners equity"), "BS_OWNERS_EQUITY"),
        (("tien gui cua khach hang", "customer deposits"), "BS_CUSTOMER_DEPOSITS"),
        (("tong cong tai san", "total assets"), "BS_TOTAL_ASSETS"),
    ],
    "income_statement": [
        (("thu nhap lai thuan", "net interest income"), "IS_NET_INTEREST_INCOME"),
        (("thu nhap lai va cac khoan thu nhap tuong tu", "interest income"), "IS_INTEREST_INCOME"),
        (("chi phi lai va cac chi phi tuong tu", "interest expense"), "IS_INTEREST_EXPENSE"),
        (("tong thu nhap hoat dong", "total operating income"), "IS_TOTAL_OPERATING_INCOME_BANK"),
        (("chi phi hoat dong", "operating expenses"), "IS_OPERATING_EXPENSE_BANK"),
        (("du phong ton that tin dung", "credit loss provision"), "IS_CREDIT_LOSS_PROVISION"),
        (("loi nhuan sau thue cua co dong cong ty me", "profit attributable to owners"), "IS_NET_PROFIT_ATTRIBUTABLE"),
    ],
    "cash_flow": [
        (("luu chuyen tien thuan tu hoat dong san xuat kinh doanh", "net cash flows from operating activities"), "CF_NET_CASH_FLOWS_FROM_OPERATING_ACTIVITIES"),
        (("luu chuyen tien thuan tu hoat dong dau tu", "net cash flows from investing activities"), "CF_NET_CASH_FLOWS_FROM_INVESTING_ACTIVITIES"),
    ],
}

def canonicalize_vci_field(report_name, field_code, title):
    section = str(report_name or "").lower()
    field = str(field_code or "").lower()
    exact = VCI_FIELD_MAP.get(section, {}).get(field)
    if section == "income_statement" and field == "isb39":
        label = normalize_label(title or "")
        if label in ("chi phi hoat dong", "operating expenses", "operating expense"):
            return "IS_OPERATING_EXPENSE_BANK"
        return "IS_OPERATING_EXPENSE_BANK_UNVERIFIED"
    if exact:
        return exact
    label = normalize_label(title or "")
    for phrases, canonical in TITLE_RULES.get(section, []):
        if any(normalize_label(p) in label for p in phrases):
            return canonical
    # Generic mappings only for unambiguous subtotal labels.
    if section == "balance_sheet":
        if label in ("tai san ngan han", "current assets"):
            return "BS_SHORT_TERM_ASSETS"
        if label in ("tai san dai han", "non current assets"):
            return "BS_LONG_TERM_ASSETS"
        if label in ("no phai tra", "total liabilities"):
            return "BS_LIABILITIES"
    if section == "income_statement":
        if field == "isa20" or label == "loi nhuan sau thue":
            return "IS_NET_PROFIT_AFTER_TAX"
        if "doanh thu thuan" in label or label == "net revenue":
            return "IS_NET_REVENUE"
        if "gia von hang ban" in label or label == "cost of goods sold":
            return "IS_COST_OF_GOODS_SOLD"
    return None

# 50-point fundamental framework; scoring is gated by data availability.
SECTOR_RULES = {
    "NGAN_HANG": {"weights": {"growth": 8, "profitability": 10, "asset_quality": 16, "capital_funding": 10, "efficiency": 6}, "valuation": ["PB_ROE", "NORMALIZED_PE"], "exclude": ["industrial_debt_to_equity", "industrial_cfo_fcf"]},
    "CHUNG_KHOAN": {"weights": {"growth": 8, "profitability": 10, "margin_risk": 12, "capital_liquidity": 12, "efficiency": 8}, "valuation": ["PB", "NORMALIZED_PE"], "exclude": ["industrial_cfo_fcf"]},
    "INSURANCE": {"weights": {"growth": 8, "profitability": 10, "reserves_solvency": 14, "underwriting": 10, "investment_quality": 8}, "valuation": ["PB_ROE", "NORMALIZED_PE"], "exclude": ["industrial_debt_to_equity", "industrial_cfo_fcf"]},
    "BAT_DONG_SAN": {"weights": {"growth_delivery": 8, "profitability": 8, "inventory_projects": 14, "debt_liquidity": 12, "cash_conversion": 8}, "valuation": ["RNAV", "ADJUSTED_PB", "NORMALIZED_PE"]},
    "CONG_NGHE": {"weights": {"growth": 12, "profitability": 12, "recurring_revenue": 8, "cash_conversion": 10, "capital_efficiency": 8}, "valuation": ["GROWTH_ADJUSTED_PE", "ROIC"]},
    "BAN_LE": {"weights": {"growth": 10, "profitability": 10, "inventory_turnover": 10, "cash_conversion": 10, "store_efficiency": 10}, "valuation": ["PE", "EV_EBITDA_IF_VALID"]},
    "THEP": {"weights": {"cycle_growth": 8, "normalized_margin": 12, "inventory_working_capital": 10, "debt_cashflow": 12, "capital_efficiency": 8}, "valuation": ["MID_CYCLE_PE", "PB", "ROIC"]},
    "PHAN_BON": {"weights": {"cycle_growth": 8, "normalized_margin": 12, "inventory_working_capital": 10, "debt_cashflow": 12, "capital_efficiency": 8}, "valuation": ["MID_CYCLE_PE", "PB", "ROIC"]},
    "NANG_LUONG": {"weights": {"growth_volume_price": 10, "profitability": 10, "receivables_contracts": 10, "capex_projects": 10, "cash_conversion": 10}, "valuation": ["DCF_IF_VALID", "PE", "EV_EBITDA_IF_VALID"]},
    "TIEN_ICH": {"weights": {"growth": 8, "profitability": 10, "debt_capex": 12, "cashflow_dividend": 12, "asset_quality": 8}, "valuation": ["DCF_IF_VALID", "PE", "DIVIDEND"]},
    "HOLDING": {"weights": {"subsidiaries_associates": 12, "profitability": 8, "dividends_cash": 10, "debt_liquidity": 10, "capital_allocation": 10}, "valuation": ["NAV", "HOLDING_DISCOUNT", "PB"]},
    "CANG_LOGISTICS": {"weights": {"growth_throughput": 10, "profitability": 10, "utilization_backlog": 10, "capex_debt": 10, "cash_conversion": 10}, "valuation": ["PE", "EV_EBITDA_IF_VALID"]},
    "TIEU_DUNG": {"weights": {"growth": 10, "profitability": 12, "brand_mix": 8, "working_capital": 10, "cash_conversion": 10}, "valuation": ["PE", "EV_EBITDA_IF_VALID", "DIVIDEND"]},
    "VAT_LIEU_XAY_DUNG": {"weights": {"growth_cycle": 8, "profitability": 12, "inventory_working_capital": 10, "debt_capex": 10, "cash_conversion": 10}, "valuation": ["MID_CYCLE_PE", "PB", "ROIC"]},
}

# Data-availability and sign-convention guardrails. No missing metric is awarded points.
DATA_QUALITY_RULES = {
    "minimum_quarters_for_ttm": 4,
    "require_consecutive_quarters": True,
    "require_positive_denominator_for_margins": True,
    "unknown_unit_is_missing": True,
    "unknown_field_is_raw_only": True,
    "do_not_sum_subtotals_and_components": True,
    "banks_exclude_industrial_cfo": True,
    "banks_exclude_industrial_debt_to_equity": True,
    "car_requires_regulatory_capital_and_risk_weighted_assets": True,
    "npl_ratio_requires_npl_and_gross_customer_loans": True,
    "npl_coverage_requires_credit_provisions_and_npl": True,
    "gross_customer_loans_mapping_verified": False,
    "bank_cir_requires_verified_operating_expense_label": True,
    "group_2_loans_mapping_verified": False,
    "demand_deposits_mapping_verified": False,
    "regulatory_car_must_not_be_inferred_from_equity_assets": True,
    "ambiguous_duplicate_labels_are_not_scoring_inputs": True,
    "ytd_cashflow_must_be_quarterized_before_ttm": True,
}

def sector_weights_are_valid():
    return all(sum(v.get("weights", {}).values()) == 50 for v in SECTOR_RULES.values())


# Explicit unit policy: metadata wins. Fallback is allowed only for verified
# field codes in this dictionary; unknown fields keep an empty unit.
PER_SHARE_FIELDS = {
    ("income_statement", "isa23"),
    ("income_statement", "isa24"),
}
VERIFIED_CURRENCY_FIELDS = {
    key for section, fields in VCI_FIELD_MAP.items()
    for key in [(section, field) for field in fields]
    if key not in PER_SHARE_FIELDS
}

# Never sum a subtotal with its child rows. The customer-loan pair remains
# unresolved until raw CTG values and the full VCI hierarchy are inspected.
NO_DOUBLE_COUNT_GROUPS = {
    ("balance_sheet", "bsa29"): ("bsa30", "bsa33", "bsa36"),
    ("balance_sheet", "bsa30"): ("bsa31", "bsa32"),
    ("balance_sheet", "bsa33"): ("bsa34", "bsa35"),
    ("balance_sheet", "bsa36"): ("bsa37", "bsa38"),
    ("balance_sheet", "bsa40"): ("bsa41", "bsa42"),
    ("balance_sheet", "bsa43"): ("bsa44", "bsa45", "bsa46", "bsa47"),
    ("balance_sheet", "bsb100"): ("bsb101",),
    ("balance_sheet", "bsb106"): ("bsb107", "bsb108", "bsb109"),
    ("balance_sheet", "bsa96"): ("bsa54", "bsa78"),
}

def resolve_vci_unit(report_name, source_field, metadata_unit=None):
    """Metadata is authoritative; no blanket VND fallback for unknown fields."""
    key = (str(report_name or "").lower(), str(source_field or "").lower())
    # EPS là đơn vị trên mỗi cổ phiếu; sửa cả metadata VCI gán nhầm VND.
    if key in PER_SHARE_FIELDS:
        return "VND/share"
    unit = str(metadata_unit or "").strip()
    if unit:
        return unit
    if key in VERIFIED_CURRENCY_FIELDS:
        return "VND"
    return ""

UNVERIFIED_CANONICAL_IDS = {
    "BS_CUSTOMER_LOANS_UNVERIFIED",
    "BS_CUSTOMER_LOANS_DETAIL_UNVERIFIED",
    "IS_OPERATING_EXPENSE_BANK_UNVERIFIED",
}

def get_vci_field_rule(report_name, source_field):
    """Return dictionary ID plus conservative role/unit and double-count hints."""
    section = str(report_name or "").lower()
    field = str(source_field or "").lower()
    canonical = VCI_FIELD_MAP.get(section, {}).get(field)
    if not canonical:
        return {
            "canonical_id": None,
            "known": False,
            "unit_kind": "unknown",
            "double_count_with": (),
        }
    double_count_with = NO_DOUBLE_COUNT_GROUPS.get((section, field), ())
    scoreable = canonical not in UNVERIFIED_CANONICAL_IDS
    return {
        "canonical_id": canonical,
        "known": True,
        "scoreable": scoreable,
        "quarantined_reason": (
            "Duplicate VCI customer-loan labels are not yet resolved."
            if not scoreable else ""
        ),
        "unit_kind": "per_share" if (section, field) in PER_SHARE_FIELDS else "currency",
        "double_count_with": double_count_with,
    }

def score_coverage(available_groups, sector):
    """Return earned-capacity and coverage; missing metrics never redistribute points."""
    rule = SECTOR_RULES.get(sector)
    if not rule:
        return {"max_points": 50, "available_points": 0, "coverage_pct": 0.0}
    weights = rule.get("weights", {})
    available = set(available_groups or [])
    available_points = sum(int(points) for group, points in weights.items() if group in available)
    return {
        "max_points": 50,
        "available_points": available_points,
        "coverage_pct": round(available_points / 50 * 100, 1),
    }


# Metric dictionary: formula, source requirement, and interpretation.
# A metric is scoreable only when every input, unit, period basis and sign convention
# has been verified. A plausible label alone is not sufficient.
METRIC_FORMULAS = {
    "ROE": {"formula": "net_profit_attributable_TTM / average_parent_equity", "source": "BCTC", "higher_is_better": True},
    "ROA": {"formula": "net_profit_TTM / average_total_assets", "source": "BCTC", "higher_is_better": True},
    "NIM": {"formula": "annualized_net_interest_income / average_interest_earning_assets", "source": "UNAVAILABLE_UNTIL_AVERAGE_INTEREST_EARNING_ASSETS_ARE_VERIFIED", "higher_is_better": True},
    "NPL_RATIO": {"formula": "non_performing_loans / gross_customer_loans", "source": "UNAVAILABLE_UNTIL_BOTH_FIELDS_AND_GROSS_LOAN_BASIS_ARE_VERIFIED", "lower_is_better": True},
    "GROUP_2_RATIO": {"formula": "group_2_loans / gross_customer_loans", "source": "UNAVAILABLE_UNTIL_BOTH_FIELDS_AND_GROSS_LOAN_BASIS_ARE_VERIFIED", "lower_is_better": True},
    "NPL_COVERAGE": {"formula": "loan_loss_provisions / non_performing_loans", "source": "UNAVAILABLE_UNTIL_NPL_AND_PROVISION_BASIS_ARE_VERIFIED", "higher_is_better": True},
    "CAR": {"formula": "regulatory_capital / risk_weighted_assets", "source": "REGULATORY_DISCLOSURE_ONLY", "higher_is_better": True},
    "CASA": {"formula": "demand_deposits / customer_deposits", "source": "UNAVAILABLE_UNTIL_DEMAND_DEPOSIT_FIELD_IS_VERIFIED", "higher_is_better": True},
    "LDR": {"formula": "customer_loans / customer_deposits", "source": "BCTC_if_both_fields_verified", "interpretation": "compare_with_regulatory_and_peer_context"},
    "CIR": {"formula": "operating_expenses / operating_income", "source": "BCTC_if_bank_lines_verified", "lower_is_better": True},
    "CREDIT_GROWTH": {"formula": "latest_period_gross_loans / prior_year_same_period_gross_loans - 1", "source": "BCTC_if_comparable_periods", "interpretation": "growth_not_quality_by_itself"},
    "MARGIN_LOAN_RATIO": {"formula": "margin_loans / equity_or_total_assets", "source": "BROKER_DISCLOSURE", "interpretation": "higher_can_mean_higher_risk"},
    "BROKERAGE_SHARE": {"formula": "brokerage_revenue_or_market_share / relevant_market_total", "source": "EXCHANGE_OR_COMPANY_DISCLOSURE"},
    "UNDERWRITING_COMBINED_RATIO": {"formula": "(claims_incurred + underwriting_expenses) / earned_premium", "source": "INSURANCE_DISCLOSURE", "lower_is_better": True},
    "INVESTMENT_YIELD": {"formula": "investment_income / average_invested_assets", "source": "INSURANCE_BCTC_AND_DISCLOSURE"},
    "INVENTORY_TURNOVER": {"formula": "TTM_COGS / average_inventory", "source": "BCTC", "interpretation": "compare_with_same_sector"},
    "CASH_CONVERSION": {"formula": "CFO_TTM / net_profit_TTM", "source": "BCTC", "interpretation": "only_for_non_financial_companies"},
    "FCF": {"formula": "CFO_TTM - cash_capex_TTM", "source": "BCTC", "interpretation": "exclude_financial_institutions; validate_cashflow_sign"},
    "ROIC": {"formula": "NOPAT_TTM / average_invested_capital", "source": "BCTC_if_all_components_available"},
    "EV_EBITDA": {"formula": "enterprise_value / EBITDA_TTM", "source": "MARKET_AND_BCTC", "interpretation": "do_not_estimate_missing_DA_or_debt"},
    "RNAV": {"formula": "sum(project_level_net_asset_values)", "source": "PROJECT_DISCLOSURE", "interpretation": "not_derivable_from_standard_BCTC_alone"},
}

SECTOR_METRICS = {
    "NGAN_HANG": {
        "growth": ("CREDIT_GROWTH",),
        "profitability": ("ROE", "ROA", "NIM"),
        "asset_quality": ("NPL_RATIO", "GROUP_2_RATIO", "NPL_COVERAGE"),
        "capital_funding": ("CAR", "CASA", "LDR"),
        "efficiency": ("CIR",),
    },
    "CHUNG_KHOAN": {
        "growth": ("revenue_growth", "profit_growth"),
        "profitability": ("ROE", "ROA"),
        "margin_risk": ("MARGIN_LOAN_RATIO", "proprietary_income_share"),
        "capital_liquidity": ("capital_adequacy", "liquid_assets", "debt_maturity"),
        "efficiency": ("CIR", "operating_cost_ratio"),
    },
    "INSURANCE": {
        "growth": ("premium_growth", "profit_growth"),
        "profitability": ("ROE", "ROA", "INVESTMENT_YIELD"),
        "reserves_solvency": ("reserve_adequacy", "solvency_margin", "reinsurance_recovery"),
        "underwriting": ("UNDERWRITING_COMBINED_RATIO", "claims_ratio"),
        "investment_quality": ("investment_concentration", "asset_quality"),
    },
    "BAT_DONG_SAN": {
        "growth_delivery": ("presales_growth", "delivery_growth", "revenue_growth"),
        "profitability": ("normalized_net_margin", "ROE"),
        "inventory_projects": ("project_inventory", "customer_advances", "receivables_quality"),
        "debt_liquidity": ("net_debt", "debt_maturity", "interest_coverage"),
        "cash_conversion": ("CASH_CONVERSION", "CFO"),
    },
    "CONG_NGHE": {
        "growth": ("revenue_growth", "segment_growth", "backlog_growth"),
        "profitability": ("gross_margin", "ROE", "ROIC"),
        "recurring_revenue": ("recurring_revenue_share", "customer_retention"),
        "cash_conversion": ("CASH_CONVERSION", "receivables_days"),
        "capital_efficiency": ("ROIC", "employee_productivity", "capex_efficiency"),
    },
    "BAN_LE": {
        "growth": ("same_store_sales_growth", "revenue_growth"),
        "profitability": ("gross_margin", "operating_margin", "ROE"),
        "inventory_turnover": ("INVENTORY_TURNOVER", "inventory_days"),
        "cash_conversion": ("CASH_CONVERSION", "receivables_days", "payables_days"),
        "store_efficiency": ("sales_per_store", "store_payback", "lease_adjusted_return"),
    },
    "THEP": {
        "cycle_growth": ("volume_growth", "realized_price", "capacity_utilization"),
        "normalized_margin": ("mid_cycle_gross_margin", "operating_margin"),
        "inventory_working_capital": ("INVENTORY_TURNOVER", "inventory_days", "receivables_days"),
        "debt_cashflow": ("net_debt", "interest_coverage", "FCF"),
        "capital_efficiency": ("ROE", "ROIC", "capex_efficiency"),
    },
    "PHAN_BON": {
        "cycle_growth": ("volume_growth", "realized_price", "input_cost_index"),
        "normalized_margin": ("mid_cycle_gross_margin", "operating_margin"),
        "inventory_working_capital": ("INVENTORY_TURNOVER", "inventory_days", "receivables_days"),
        "debt_cashflow": ("net_debt", "interest_coverage", "FCF"),
        "capital_efficiency": ("ROE", "ROIC", "capex_efficiency"),
    },
    "NANG_LUONG": {
        "growth_volume_price": ("production_volume_growth", "realized_price", "contracted_volume"),
        "profitability": ("normalized_margin", "ROE", "ROIC"),
        "receivables_contracts": ("receivables_days", "contract_quality", "counterparty_risk"),
        "capex_projects": ("project_progress", "capex_to_budget", "project_return"),
        "cash_conversion": ("CFO", "FCF", "dividend_cash_coverage"),
    },
    "TIEN_ICH": {
        "growth": ("volume_growth", "regulated_tariff_growth"),
        "profitability": ("ROE", "ROIC", "operating_margin"),
        "debt_capex": ("net_debt", "interest_coverage", "capex_to_assets"),
        "cashflow_dividend": ("CFO", "FCF", "dividend_cash_coverage"),
        "asset_quality": ("asset_utilization", "receivables_days"),
    },
    "HOLDING": {
        "subsidiaries_associates": ("associate_profit_share", "subsidiary_dividends", "NAV"),
        "profitability": ("normalized_ROE", "recurring_profit_share"),
        "dividends_cash": ("cash_dividends_received", "holding_company_CFO"),
        "debt_liquidity": ("net_debt", "debt_maturity", "interest_coverage"),
        "capital_allocation": ("investment_return", "related_party_exposure"),
    },
    "CANG_LOGISTICS": {
        "growth_throughput": ("throughput_growth", "volume_growth", "revenue_growth"),
        "profitability": ("EBIT_margin", "ROE", "ROIC"),
        "utilization_backlog": ("capacity_utilization", "contract_backlog"),
        "capex_debt": ("net_debt", "capex_to_assets", "project_return"),
        "cash_conversion": ("CFO", "FCF", "receivables_days"),
    },
    "TIEU_DUNG": {
        "growth": ("volume_growth", "revenue_growth", "export_growth"),
        "profitability": ("gross_margin", "operating_margin", "ROE"),
        "brand_mix": ("premium_product_share", "brand_pricing_power"),
        "working_capital": ("INVENTORY_TURNOVER", "receivables_days", "payables_days"),
        "cash_conversion": ("CASH_CONVERSION", "FCF"),
    },
    "VAT_LIEU_XAY_DUNG": {
        "growth_cycle": ("volume_growth", "realized_price", "capacity_utilization"),
        "profitability": ("gross_margin", "operating_margin", "ROE"),
        "inventory_working_capital": ("INVENTORY_TURNOVER", "inventory_days", "receivables_days"),
        "debt_capex": ("net_debt", "interest_coverage", "capex_to_assets"),
        "cash_conversion": ("CASH_CONVERSION", "FCF"),
    },
}


# Explicit audit helpers used by tests/admin diagnostics.
def validate_bctc_rules():
    """Return structural dictionary errors; does not inspect or mutate MongoDB."""
    errors = []
    if not sector_weights_are_valid():
        errors.append("Sector fundamental weights must total exactly 50 points.")
    for section, mapping in VCI_FIELD_MAP.items():
        seen = {}
        for source_field, canonical_id in mapping.items():
            if source_field in seen:
                errors.append(f"Duplicate source field: {section}.{source_field}")
            seen[source_field] = canonical_id
            if not canonical_id or not re.fullmatch(r"[A-Z][A-Z0-9_]+", canonical_id):
                errors.append(f"Invalid canonical ID: {section}.{source_field}")
    if VCI_FIELD_MAP["balance_sheet"].get("bsa78") != "BS_OWNERS_EQUITY":
        errors.append("bsa78 must map to BS_OWNERS_EQUITY.")
    if VCI_FIELD_MAP["balance_sheet"].get("bsa96") != "BS_TOTAL_LIABILITIES_AND_EQUITY":
        errors.append("bsa96 must map to BS_TOTAL_LIABILITIES_AND_EQUITY.")
    if VCI_FIELD_MAP["balance_sheet"].get("bsb103") == "BS_CUSTOMER_LOANS":
        errors.append("Ambiguous bsb103 must not be treated as verified gross customer loans.")
    if any(
        "cho vay khach hang" in normalize_label(phrase)
        for phrases, _canonical in TITLE_RULES.get("balance_sheet", [])
        for phrase in phrases
    ):
        errors.append("Ambiguous customer-loan title must not be auto-mapped by label.")
    return {"valid": not errors, "errors": errors, "sector_count": len(SECTOR_RULES),
            "mapped_field_count": sum(len(v) for v in VCI_FIELD_MAP.values())}
