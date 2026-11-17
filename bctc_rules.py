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
        # Đã đối chiếu số liệu VCI trực tiếp: CTG Q2/Q3-2026,
        # VCB Q3-2026 và BID Q3-2026. bsb103 = bsb104 + bsb105
        # (bsb105 mang dấu âm trong dữ liệu nguồn).
        "bsb103": "BS_CUSTOMER_LOANS_NET",
        "bsb104": "BS_CUSTOMER_LOANS_GROSS",
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
        "isb39": "IS_OPERATING_EXPENSE_BANK",
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
        # Industrial-company labels: deliberately specific to avoid confusing
        # gross revenue, net revenue, operating profit and pre-tax profit.
        (("doanh thu thuan ve ban hang va cung cap dich vu", "net revenue from sales and services", "net revenue"), "IS_NET_REVENUE"),
        (("gia von hang ban", "cost of goods sold"), "IS_COST_OF_GOODS_SOLD"),
        (("loi nhuan gop ve ban hang va cung cap dich vu", "gross profit"), "IS_GROSS_PROFIT"),
        (("doanh thu hoat dong tai chinh", "finance income"), "IS_FINANCIAL_INCOME"),
        (("chi phi tai chinh", "finance costs"), "IS_FINANCIAL_EXPENSE"),
        (("chi phi ban hang", "selling expenses"), "IS_SELLING_EXPENSE"),
        (("chi phi quan ly doanh nghiep", "general and administrative expenses"), "IS_GENERAL_ADMIN_EXPENSE"),
        (("loi nhuan thuan tu hoat dong kinh doanh", "operating profit"), "IS_OPERATING_PROFIT"),
        (("chi phi thue thu nhap doanh nghiep hien hanh", "current income tax expense"), "IS_CURRENT_INCOME_TAX"),
        (("chi phi thue thu nhap doanh nghiep hoan lai", "deferred income tax expense"), "IS_DEFERRED_INCOME_TAX"),
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
        (("luu chuyen tien thuan tu hoat dong tai chinh", "net cash flows from financing activities"), "CF_NET_CASH_FLOWS_FROM_FINANCING_ACTIVITIES"),
        (("tien va tuong duong tien cuoi ky", "cash and cash equivalents at end of period"), "CF_CASH_AT_END"),
        (("tien va tuong duong tien dau ky", "cash and cash equivalents at beginning of period"), "CF_CASH_AT_BEGINNING"),
        (("anh huong cua thay doi ty gia hoi doai quy doi ngoai te", "effect of exchange rate changes on cash"), "CF_FX_EFFECT_ON_CASH"),
    ],
}

def resolve_vci_field_mapping(report_name, field_code, title):
    """Resolve a BCTC label while preserving how strong the mapping evidence is.

    Exact field-dictionary matches are distinct from title-rule and generic
    label candidates. Candidate matches are never issuer-level approval.
    """
    section = str(report_name or "").lower()
    field = str(field_code or "").lower()
    exact = VCI_FIELD_MAP.get(section, {}).get(field)
    if exact:
        return {
            "canonical_id": exact,
            "mapping_method": "EXACT_FIELD_DICTIONARY",
            "mapping_status": "FIELD_CODE_MATCH",
            "source_section": section,
            "source_field": field,
        }

    label = normalize_label(title or "")
    for phrases, canonical in TITLE_RULES.get(section, []):
        if any(normalize_label(p) in label for p in phrases):
            return {
                "canonical_id": canonical,
                "mapping_method": "TITLE_RULE",
                "mapping_status": "TITLE_RULE_CANDIDATE",
                "source_section": section,
                "source_field": field,
            }

    # Generic mappings are intentionally limited to unambiguous subtotal labels.
    if section == "balance_sheet":
        if label in ("tai san ngan han", "current assets"):
            return {
                "canonical_id": "BS_SHORT_TERM_ASSETS",
                "mapping_method": "GENERIC_LABEL",
                "mapping_status": "GENERIC_LABEL_CANDIDATE",
                "source_section": section,
                "source_field": field,
            }
        if label in ("tai san dai han", "non current assets"):
            return {
                "canonical_id": "BS_LONG_TERM_ASSETS",
                "mapping_method": "GENERIC_LABEL",
                "mapping_status": "GENERIC_LABEL_CANDIDATE",
                "source_section": section,
                "source_field": field,
            }
        if label in ("no phai tra", "total liabilities"):
            return {
                "canonical_id": "BS_LIABILITIES",
                "mapping_method": "GENERIC_LABEL",
                "mapping_status": "GENERIC_LABEL_CANDIDATE",
                "source_section": section,
                "source_field": field,
            }
    if section == "income_statement":
        if label == "loi nhuan sau thue":
            return {
                "canonical_id": "IS_NET_PROFIT_AFTER_TAX",
                "mapping_method": "GENERIC_LABEL",
                "mapping_status": "GENERIC_LABEL_CANDIDATE",
                "source_section": section,
                "source_field": field,
            }
        if "doanh thu thuan" in label or label == "net revenue":
            return {
                "canonical_id": "IS_NET_REVENUE",
                "mapping_method": "GENERIC_LABEL",
                "mapping_status": "GENERIC_LABEL_CANDIDATE",
                "source_section": section,
                "source_field": field,
            }
        if "gia von hang ban" in label or label == "cost of goods sold":
            return {
                "canonical_id": "IS_COST_OF_GOODS_SOLD",
                "mapping_method": "GENERIC_LABEL",
                "mapping_status": "GENERIC_LABEL_CANDIDATE",
                "source_section": section,
                "source_field": field,
            }

    return {
        "canonical_id": None,
        "mapping_method": "UNMAPPED",
        "mapping_status": "UNMAPPED",
        "source_section": section,
        "source_field": field,
    }


def canonicalize_vci_field(report_name, field_code, title):
    """Backward-compatible canonical ID wrapper; use resolver for provenance."""
    return resolve_vci_field_mapping(report_name, field_code, title)["canonical_id"]


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
    "AGRI": {"weights": {"cycle_growth": 10, "profitability": 10, "biological_assets": 10, "working_capital": 10, "cash_conversion": 10}, "valuation": ["MID_CYCLE_PE", "PB", "DCF_IF_VALID"], "exclude": ["industrial_metrics_without_verified_inputs"]},
    "NGUYEN_VAT_LIEU_CHU_KY": {"weights": {"cycle_growth": 10, "normalized_margin": 12, "inventory_working_capital": 10, "debt_cashflow": 10, "capital_efficiency": 8}, "valuation": ["MID_CYCLE_PE", "PB", "ROIC"]},
    "AVIATION": {"weights": {"demand_growth": 8, "profitability": 10, "fleet_utilization": 12, "lease_debt": 10, "cashflow": 10}, "valuation": ["EV_EBITDAR_IF_VALID", "DCF_IF_VALID"]},
    "CONSTRUCTION": {"weights": {"backlog_quality": 12, "profitability": 8, "receivables_contract_assets": 12, "debt_liquidity": 10, "cash_conversion": 8}, "valuation": ["NORMALIZED_PE", "PB", "DCF_IF_VALID"]},
    "PETROLEUM": {"weights": {"segment_mix": 10, "production_price": 10, "normalized_margin": 12, "reserve_capex": 8, "debt_cashflow": 10}, "valuation": ["MID_CYCLE_PE", "EV_EBITDA_IF_VALID", "DCF_IF_VALID"]},
    "PHARMA": {"weights": {"growth": 10, "profitability": 12, "product_portfolio": 10, "working_capital": 8, "cash_conversion": 10}, "valuation": ["PE", "DCF_IF_VALID"]},
    "TEXTILE": {"weights": {"order_growth": 10, "normalized_margin": 10, "capacity_labor": 10, "working_capital": 10, "cash_conversion": 10}, "valuation": ["MID_CYCLE_PE", "PB"]},
    "RUBBER": {"weights": {"replacement_export_growth": 10, "normalized_margin": 12, "raw_material_inventory": 10, "debt_capex": 8, "cash_conversion": 10}, "valuation": ["MID_CYCLE_PE", "PB"]},
    "PLASTICS_CHEMICALS": {"weights": {"volume_price_spread": 10, "normalized_margin": 12, "capacity_product_mix": 8, "working_capital": 10, "debt_cashflow": 10}, "valuation": ["MID_CYCLE_PE", "PB"]},
    "AGRI_PROCESSING": {"weights": {"segment_growth": 10, "normalized_margin": 10, "biological_supply_chain": 10, "working_capital": 10, "cash_conversion": 10}, "valuation": ["MID_CYCLE_PE", "PB", "DCF_IF_VALID"]},
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
    "gross_customer_loans_mapping_verified": True,
    "bank_cir_requires_verified_operating_expense_label": True,
    "bank_isb39_verified_by_ppop_reconciliation": True,
    "bank_income_quarter_basis_verified": True,
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

# Never sum a subtotal with its child rows. Customer loans: bsb104 is gross,
# bsb103 is net after the negative contra-asset bsb105; these are alternatives
# for different calculations and must never be added together.
NO_DOUBLE_COUNT_GROUPS = {
    ("balance_sheet", "bsa29"): ("bsa30", "bsa33", "bsa36"),
    ("balance_sheet", "bsa30"): ("bsa31", "bsa32"),
    ("balance_sheet", "bsa33"): ("bsa34", "bsa35"),
    ("balance_sheet", "bsa36"): ("bsa37", "bsa38"),
    ("balance_sheet", "bsa40"): ("bsa41", "bsa42"),
    ("balance_sheet", "bsa43"): ("bsa44", "bsa45", "bsa46", "bsa47"),
    ("balance_sheet", "bsb100"): ("bsb101",),
    ("balance_sheet", "bsb104"): ("bsb103", "bsb105"),
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
    "BANK_NON_INTEREST_INCOME_SHARE": {"formula": "(total_operating_income_TTM - net_interest_income_TTM) / total_operating_income_TTM", "source": "BCTC_if_same_basis_and_positive_total_operating_income", "interpretation": "income diversification; assess alongside recurring quality and volatility"},
    "BANK_PPOP_MARGIN": {"formula": "profit_before_credit_provisions_TTM / total_operating_income_TTM", "source": "BCTC_if_same_basis_and_positive_total_operating_income", "higher_is_better": True, "interpretation": "pre-provision operating profitability; not a substitute for ROA or ROE"},
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
    "AGRI": {
        "cycle_growth": ("commodity_price_realization", "volume_growth", "input_cost_spread"),
        "profitability": ("normalized_margin", "ROE", "ROIC"),
        "biological_assets": ("biological_asset_quality", "harvest_yield", "disease_or_weather_exposure"),
        "working_capital": ("INVENTORY_TURNOVER", "receivables_days", "payables_days"),
        "cash_conversion": ("CASH_CONVERSION", "CFO", "FCF"),
    },
    "NGUYEN_VAT_LIEU_CHU_KY": {
        "cycle_growth": ("volume_growth", "realized_price", "capacity_utilization"),
        "normalized_margin": ("mid_cycle_gross_margin", "operating_margin"),
        "inventory_working_capital": ("INVENTORY_TURNOVER", "inventory_days", "receivables_days"),
        "debt_cashflow": ("net_debt", "interest_coverage", "FCF"),
        "capital_efficiency": ("ROE", "ROIC", "capex_efficiency"),
    },
    "AVIATION": {
        "demand_growth": ("passenger_growth", "cargo_growth", "yield_per_passenger"),
        "profitability": ("normalized_margin", "ROE", "ROIC"),
        "fleet_utilization": ("load_factor", "fleet_utilization", "fleet_age"),
        "lease_debt": ("lease_adjusted_net_debt", "lease_maturity", "interest_coverage"),
        "cashflow": ("CFO", "FCF", "capex_commitment"),
    },
    "CONSTRUCTION": {
        "backlog_quality": ("backlog_growth", "backlog_conversion", "customer_advance_coverage"),
        "profitability": ("normalized_margin", "ROE", "gross_margin"),
        "receivables_contract_assets": ("receivables_days", "contract_asset_quality", "overdue_receivables"),
        "debt_liquidity": ("net_debt", "interest_coverage", "debt_maturity"),
        "cash_conversion": ("CFO", "CASH_CONVERSION", "FCF"),
    },
    "PETROLEUM": {
        "segment_mix": ("upstream_downstream_split", "segment_profit_share", "refining_exposure"),
        "production_price": ("production_volume_growth", "realized_oil_price", "reserve_life"),
        "normalized_margin": ("mid_cycle_margin", "refining_margin", "operating_margin"),
        "reserve_capex": ("reserve_replacement", "project_capex_progress", "capex_efficiency"),
        "debt_cashflow": ("net_debt", "interest_coverage", "FCF"),
    },
    "PHARMA": {
        "growth": ("revenue_growth", "volume_growth", "export_growth"),
        "profitability": ("gross_margin", "operating_margin", "ROE"),
        "product_portfolio": ("registered_product_share", "ethical_drug_share", "pipeline_quality"),
        "working_capital": ("inventory_days", "receivables_days", "expiry_writeoffs"),
        "cash_conversion": ("CASH_CONVERSION", "CFO", "FCF"),
    },
    "TEXTILE": {
        "order_growth": ("export_order_growth", "backlog_conversion", "revenue_growth"),
        "normalized_margin": ("mid_cycle_gross_margin", "operating_margin", "labor_cost_ratio"),
        "capacity_labor": ("capacity_utilization", "labor_productivity", "compliance_risk"),
        "working_capital": ("inventory_days", "receivables_days", "customer_advances"),
        "cash_conversion": ("CASH_CONVERSION", "CFO", "FCF"),
    },
    "RUBBER": {
        "replacement_export_growth": ("replacement_tire_share", "export_growth", "volume_growth"),
        "normalized_margin": ("mid_cycle_gross_margin", "operating_margin", "rubber_input_spread"),
        "raw_material_inventory": ("natural_rubber_cost", "inventory_days", "receivables_days"),
        "debt_capex": ("net_debt", "interest_coverage", "capex_efficiency"),
        "cash_conversion": ("CASH_CONVERSION", "CFO", "FCF"),
    },
    "PLASTICS_CHEMICALS": {
        "volume_price_spread": ("volume_growth", "realized_price", "resin_input_spread"),
        "normalized_margin": ("mid_cycle_gross_margin", "operating_margin", "energy_cost_ratio"),
        "capacity_product_mix": ("capacity_utilization", "value_added_product_share", "capex_efficiency"),
        "working_capital": ("inventory_days", "receivables_days", "payables_days"),
        "debt_cashflow": ("net_debt", "interest_coverage", "CFO"),
    },
    "AGRI_PROCESSING": {
        "segment_growth": ("segment_revenue_growth", "export_growth", "volume_growth"),
        "normalized_margin": ("mid_cycle_margin", "input_cost_spread", "operating_margin"),
        "biological_supply_chain": ("biological_asset_quality", "farm_yield", "feedstock_security"),
        "working_capital": ("inventory_days", "receivables_days", "payables_days"),
        "cash_conversion": ("CASH_CONVERSION", "CFO", "FCF"),
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
    if VCI_FIELD_MAP["balance_sheet"].get("bsb103") != "BS_CUSTOMER_LOANS_NET":
        errors.append("bsb103 must map to net customer loans.")
    if VCI_FIELD_MAP["balance_sheet"].get("bsb104") != "BS_CUSTOMER_LOANS_GROSS":
        errors.append("bsb104 must map to gross customer loans.")
    if VCI_FIELD_MAP["balance_sheet"].get("bsb105") != "BS_PROVISION_FOR_CUSTOMER_LOANS":
        errors.append("bsb105 must map to customer-loan provisions.")
    if not DATA_QUALITY_RULES["gross_customer_loans_mapping_verified"]:
        errors.append("Gross customer-loan mapping must remain verified after cross-bank reconciliation.")
    if any(
        "cho vay khach hang" in normalize_label(phrase)
        for phrases, _canonical in TITLE_RULES.get("balance_sheet", [])
        for phrase in phrases
    ):
        errors.append("Ambiguous customer-loan title must not be auto-mapped by label.")
    return {"valid": not errors, "errors": errors, "sector_count": len(SECTOR_RULES),
            "mapped_field_count": sum(len(v) for v in VCI_FIELD_MAP.values())}


# Versioned formula contracts for every metric in all 25 sector profiles.
# Formula definitions are explicit; no scoring thresholds are fabricated.
_FORMULA_TEMPLATES = {
    "ROE": ("net_profit_after_tax_TTM / average_owners_equity * 100", "percent", "BCTC", "higher", ["isa20", "bsa78"], "verified_formula_requires_average_equity"),
    "ROA": ("net_profit_after_tax_TTM / average_total_assets * 100", "percent", "BCTC", "higher", ["isa20", "bsa53"], "verified_formula_requires_average_assets"),
    "CREDIT_GROWTH": ("gross_customer_loans_current_quarter / gross_customer_loans_same_quarter_prior_year - 1", "percent", "BCTC", "higher", ["bsb104"], "verified_formula_requires_same_quarter_yoy"),
    "CASH_CONVERSION": ("operating_cash_flow_TTM / net_profit_after_tax_TTM", "ratio", "BCTC", "higher", ["cfa18", "isa20"], "verified_formula_non_financial_only"),
    "CFO": ("sum(quarterly_CFO_for_four_consecutive_quarters)", "VND", "BCTC_cash_flow", "higher", ["cfa18"], "verified_formula"),
    "FCF": ("operating_cash_flow_TTM - cash_capex_TTM", "VND", "BCTC_cash_flow", "higher", ["cfa18", "cfa19"], "blocked_capex_sign_and_scope"),
    "INVENTORY_TURNOVER": ("COGS_TTM / average_inventory", "times", "BCTC", "higher_peer_context", ["isa"], "blocked_inventory_source_mapping"),
    "NIM": ("net_interest_income_TTM / average_interest_earning_assets * 100", "percent", "BCTC_plus_disclosure", "higher", ["isb27"], "blocked_average_interest_earning_assets"),
    "NPL_RATIO": ("non_performing_loans / gross_customer_loans * 100", "percent", "regulatory_disclosure_plus_BCTC", "lower", ["bsb104"], "blocked_NPL_source"),
    "GROUP_2_RATIO": ("group_2_loans / gross_customer_loans * 100", "percent", "regulatory_disclosure_plus_BCTC", "lower", ["bsb104"], "blocked_group_2_source"),
    "NPL_COVERAGE": ("loan_loss_provisions / non_performing_loans", "ratio", "regulatory_disclosure", "higher", [], "blocked_both_inputs"),
    "CAR": ("regulatory_capital / risk_weighted_assets", "ratio", "regulatory_disclosure", "higher", [], "blocked_regulatory_inputs"),
    "CASA": ("demand_deposits / customer_deposits", "ratio", "bank_disclosure_plus_BCTC", "higher", ["bsb113"], "blocked_demand_deposit_mapping"),
    "LDR": ("gross_customer_loans / customer_deposits * 100", "percent", "BCTC", "peer_and_regulatory_context", ["bsb104", "bsb113"], "conditional_source_basis"),
    "CIR": ("abs(operating_expenses_TTM) / operating_income_TTM * 100", "percent", "BCTC", "lower", ["isb39", "isb38"], "conditional_bank_line_verification"),
    "UNDERWRITING_COMBINED_RATIO": ("(claims_incurred_TTM + underwriting_expenses_TTM) / earned_premium_TTM", "ratio", "insurance_disclosure", "lower", [], "blocked_disclosure_inputs"),
    "INVESTMENT_YIELD": ("investment_income_TTM / average_invested_assets", "ratio", "BCTC_plus_disclosure", "higher_peer_context", [], "blocked_average_invested_assets"),
    "ROIC": ("NOPAT_TTM / average_invested_capital", "ratio", "BCTC", "higher", [], "blocked_NOPAT_and_invested_capital"),
    "EV_EBITDA": ("(market_cap + interest_bearing_debt - cash) / EBITDA_TTM", "multiple", "market_data_plus_BCTC", "lower_peer_context", [], "blocked_debt_and_EBITDA_components"),
    "RNAV": ("sum(project_level_fair_value - project_level_net_debt - remaining_project_cost)", "VND", "project_disclosure", "higher", [], "blocked_project_level_inputs"),
}

_GROWTH_METRICS = {
    "revenue_growth", "profit_growth", "premium_growth", "presales_growth",
    "delivery_growth", "segment_growth", "backlog_growth", "same_store_sales_growth",
    "volume_growth", "production_volume_growth", "throughput_growth",
    "export_growth", "commodity_price_realization", "passenger_growth",
    "cargo_growth", "export_order_growth", "segment_revenue_growth",
    "regulated_tariff_growth", "input_cost_index", "realized_price",
}
_MARGIN_METRICS = {
    "gross_margin": ("gross_profit_TTM / net_revenue_TTM", "higher"),
    "operating_margin": ("operating_profit_TTM / net_revenue_TTM", "higher"),
    "normalized_net_margin": ("normalized_net_profit_TTM / net_revenue_TTM", "higher"),
    "normalized_margin": ("normalized_operating_profit_mid_cycle / net_revenue_mid_cycle", "higher"),
    "mid_cycle_gross_margin": ("gross_profit_mid_cycle / net_revenue_mid_cycle", "higher"),
    "mid_cycle_margin": ("operating_profit_mid_cycle / net_revenue_mid_cycle", "higher"),
    "ebit_margin": ("EBIT_TTM / net_revenue_TTM", "higher"),
    "labor_cost_ratio": ("labor_cost_TTM / net_revenue_TTM", "lower"),
    "energy_cost_ratio": ("energy_cost_TTM / net_revenue_TTM", "lower"),
}
_DAYS_METRICS = {
    "inventory_days": ("average_inventory / COGS_TTM * 365", "lower"),
    "receivables_days": ("average_trade_receivables / net_credit_sales_TTM * 365", "lower"),
    "payables_days": ("average_trade_payables / COGS_TTM * 365", "peer_context"),
}
_RATIO_METRICS = {
    "net_debt": ("short_term_interest_bearing_debt + long_term_interest_bearing_debt - cash_and_equivalents", "VND", "lower"),
    "interest_coverage": ("EBIT_TTM / abs(interest_expense_TTM)", "times", "higher"),
    "capex_to_assets": ("cash_capex_TTM / average_total_assets", "ratio", "lower_peer_context"),
    "dividend_cash_coverage": ("operating_cash_flow_TTM / cash_dividends_paid_TTM", "ratio", "higher"),
    "customer_advance_coverage": ("customer_advances / contract_assets_and_project_receivables", "ratio", "higher_peer_context"),
    "customer_advances": ("customer_advances_closing_balance", "VND", "disclosure_only"),
    "receivables_quality": ("(trade_receivables - overdue_receivables - impairment_allowance) / trade_receivables", "ratio", "higher"),
    "contract_asset_quality": ("(contract_assets - overdue_contract_assets - impairment_allowance) / contract_assets", "ratio", "higher"),
    "overdue_receivables": ("overdue_trade_receivables / total_trade_receivables", "ratio", "lower"),
    "expiry_writeoffs": ("expired_or_written_off_inventory / average_inventory", "ratio", "lower"),
    "related_party_exposure": ("related_party_receivables_and_investments / total_assets", "ratio", "lower"),
    "investment_concentration": ("largest_investment_exposure / total_invested_assets", "ratio", "lower"),
    "recurring_profit_share": ("recurring_operating_profit_TTM / total_profit_before_tax_TTM", "ratio", "higher"),
    "recurring_revenue_share": ("contracted_or_recurring_revenue_TTM / total_revenue_TTM", "ratio", "higher"),
    "premium_product_share": ("premium_product_revenue / total_product_revenue", "ratio", "higher"),
    "registered_product_share": ("revenue_from_registered_products / pharmaceutical_revenue", "ratio", "higher"),
    "ethical_drug_share": ("ethical_drug_revenue / pharmaceutical_revenue", "ratio", "higher"),
    "value_added_product_share": ("value_added_product_revenue / total_product_revenue", "ratio", "higher"),
    "associate_profit_share": ("share_of_profit_from_associates_TTM / group_net_profit_TTM", "ratio", "context"),
    "subsidiary_dividends": ("cash_dividends_received_from_subsidiaries_TTM", "VND", "disclosure_only"),
    "cash_dividends_received": ("cash_dividends_received_TTM", "VND", "disclosure_only"),
    "holding_company_CFO": ("parent_only_operating_cash_flow_TTM", "VND", "disclosure_only"),
    "NAV": ("sum(fair_value_of_investments_and_assets - holding_company_net_debt)", "VND", "blocked_lookthrough_inputs"),
    "load_factor": ("revenue_passenger_km / available_seat_km", "ratio", "higher"),
    "fleet_utilization": ("operating_fleet_days / available_fleet_days", "ratio", "higher"),
    "capacity_utilization": ("actual_output / practical_capacity", "ratio", "higher"),
    "sales_per_store": ("retail_revenue_TTM / average_operating_store_count", "VND_per_store", "higher"),
    "employee_productivity": ("revenue_TTM / average_employee_count", "VND_per_employee", "higher"),
    "store_payback": ("initial_store_investment / annual_store_cash_contribution", "years", "lower"),
    "lease_adjusted_return": ("lease_adjusted_operating_profit / average_lease_adjusted_capital", "ratio", "higher"),
    "capex_efficiency": ("incremental_operating_cash_flow / cash_capex", "ratio", "higher"),
    "project_progress": ("verified_completed_project_milestones / planned_project_milestones", "ratio", "higher"),
    "capex_to_budget": ("actual_cumulative_project_capex / approved_project_budget", "ratio", "context"),
    "project_return": ("project_operating_cash_flow / project_invested_capital", "ratio", "higher"),
    "backlog_conversion": ("revenue_recognized_from_opening_backlog / opening_backlog", "ratio", "higher"),
    "contract_backlog": ("signed_unrecognized_contract_value", "VND", "disclosure_only"),
    "backlog_growth": ("current_signed_backlog / prior_comparable_signed_backlog - 1", "ratio", "higher"),
    "customer_retention": ("retained_customers_at_period_end / customers_at_period_start", "ratio", "higher"),
    "brand_pricing_power": ("realized_price_growth - relevant_input_cost_inflation", "ratio", "higher"),
    "input_cost_spread": ("realized_selling_price_per_unit - weighted_average_input_cost_per_unit", "VND_per_unit", "higher"),
    "resin_input_spread": ("average_resin_selling_price - weighted_average_resin_input_cost", "VND_per_ton", "higher"),
    "rubber_input_spread": ("average_product_selling_price - natural_rubber_input_cost_per_unit", "VND_per_unit", "higher"),
    "natural_rubber_cost": ("natural_rubber_raw_material_cost_TTM / production_volume_TTM", "VND_per_unit", "lower_peer_context"),
    "realized_oil_price": ("recognized_upstream_revenue / sold_oil_equivalent_volume", "USD_per_boe", "market_context"),
    "refining_margin": ("refined_product_revenue - crude_oil_feedstock_cost", "VND_per_unit", "higher"),
    "reserve_life": ("proved_and_probable_economic_reserves / annual_production", "years", "higher"),
    "reserve_replacement": ("additions_to_economic_reserves / annual_production", "ratio", "higher"),
    "yield_per_passenger": ("passenger_revenue / revenue_passengers", "VND_per_passenger", "higher"),
    "passenger_growth": ("current_revenue_passengers / prior_year_revenue_passengers - 1", "ratio", "higher"),
    "cargo_growth": ("current_cargo_tonnage / prior_year_cargo_tonnage - 1", "ratio", "higher"),
    "replacement_tire_share": ("replacement_tire_sales_volume / total_tire_sales_volume", "ratio", "higher"),
    "farm_yield": ("harvested_output / cultivated_or_harvested_area", "tonnes_per_hectare", "higher_peer_context"),
    "biological_asset_quality": ("(biological_assets - impairment_allowance) / biological_assets", "ratio", "higher"),
    "feedstock_security": ("contracted_or_controlled_feedstock_volume / planned_input_volume", "ratio", "higher"),
    "disease_or_weather_exposure": ("production_volume_exposed_to_material_risk / total_production_volume", "ratio", "lower"),
    "reinsurance_recovery": ("recoverable_reinsurance_claims / gross_claims_incurred", "ratio", "context"),
    "reserve_adequacy": ("eligible_technical_reserves / estimated_required_technical_reserves", "ratio", "higher"),
    "solvency_margin": ("available_solvency_capital / required_solvency_capital", "ratio", "higher"),
    "claims_ratio": ("claims_incurred_TTM / earned_premium_TTM", "ratio", "lower"),
    "capital_adequacy": ("regulatory_eligible_capital / regulatory_required_capital", "ratio", "higher"),
    "liquid_assets": ("unencumbered_liquid_assets / short_term_liabilities", "ratio", "higher"),
    "debt_maturity": ("debt_due_within_12_months / total_interest_bearing_debt", "ratio", "lower"),
    "lease_maturity": ("lease_liabilities_due_within_12_months / total_lease_liabilities", "ratio", "lower"),
    "lease_adjusted_net_debt": ("interest_bearing_debt + lease_liabilities - cash_and_equivalents", "VND", "lower"),
    "lease_adjusted_debt": ("interest_bearing_debt + lease_liabilities", "VND", "lower"),
    "project_inventory": ("unsold_project_inventory_at_cost_and_net_realizable_value", "VND", "disclosure_only"),
    "unsold_inventory": ("unsold_completed_and_work_in_progress_projects", "VND", "disclosure_only"),
    "presales_growth": ("current_period_presales / prior_comparable_period_presales - 1", "ratio", "higher"),
    "delivery_growth": ("current_period_delivered_units / prior_comparable_period_delivered_units - 1", "ratio", "higher"),
    "throughput_growth": ("current_period_throughput / prior_comparable_period_throughput - 1", "ratio", "higher"),
    "production_volume_growth": ("current_period_production_volume / prior_comparable_period_production_volume - 1", "ratio", "higher"),
    "volume_growth": ("current_period_sales_volume / prior_comparable_period_sales_volume - 1", "ratio", "higher"),
    "export_growth": ("current_period_export_revenue_or_volume / prior_comparable_period_export_revenue_or_volume - 1", "ratio", "higher"),
    "revenue_growth": ("revenue_TTM / prior_year_revenue_TTM - 1", "ratio", "higher"),
    "profit_growth": ("net_profit_TTM / prior_year_net_profit_TTM - 1", "ratio", "higher"),
    "premium_growth": ("gross_written_premium_TTM / prior_year_gross_written_premium_TTM - 1", "ratio", "higher"),
    "segment_growth": ("segment_revenue_TTM / prior_year_segment_revenue_TTM - 1", "ratio", "higher"),
    "segment_revenue_growth": ("segment_revenue_TTM / prior_year_segment_revenue_TTM - 1", "ratio", "higher"),
    "same_store_sales_growth": ("same_store_sales_current_period / same_store_sales_prior_comparable_period - 1", "ratio", "higher"),
    "export_order_growth": ("current_export_orders / prior_comparable_export_orders - 1", "ratio", "higher"),
    "volume_price_spread": ("realized_selling_price_per_unit - weighted_average_input_cost_per_unit", "VND_per_unit", "higher"),
    "production_volume_growth": ("current_period_production_volume / prior_comparable_period_production_volume - 1", "ratio", "higher"),
    "regulated_tariff_growth": ("current_average_realized_tariff / prior_comparable_average_tariff - 1", "ratio", "higher"),
    "commodity_price_realization": ("company_realized_commodity_price / relevant_benchmark_price", "ratio", "market_context"),
    "upstream_downstream_split": ("upstream_segment_revenue / total_group_revenue", "ratio", "context"),
    "segment_profit_share": ("segment_operating_profit / total_segment_operating_profit", "ratio", "context"),
    "refining_exposure": ("downstream_refining_revenue / total_group_revenue", "ratio", "context"),
    "production_volume": ("reported_production_volume_for_period", "physical_unit", "disclosure_only"),
    "realized_price": ("recognized_revenue / sold_volume", "VND_per_unit", "market_context"),
    "contracted_volume": ("contracted_volume_for_forward_period", "physical_unit", "disclosure_only"),
    "yield_per_passenger": ("passenger_revenue / revenue_passengers", "VND_per_passenger", "higher"),
    "input_cost_index": ("weighted_input_cost_index_current / weighted_input_cost_index_base", "index", "market_context"),
    "volume_growth": ("current_period_sales_volume / prior_comparable_period_sales_volume - 1", "ratio", "higher"),
    "backlog_growth": ("current_signed_backlog / prior_comparable_signed_backlog - 1", "ratio", "higher"),
    "customer_advances": ("customer_advances_closing_balance", "VND", "disclosure_only"),
    "asset_utilization": ("revenue_TTM / average_operating_asset_base", "ratio", "higher"),
    "capital_efficiency": ("NOPAT_TTM / average_invested_capital", "ratio", "blocked_inputs"),
    "normalized_ROE": ("normalized_net_profit_TTM / average_parent_equity", "ratio", "blocked_normalization_method"),
    "proprietary_income_share": ("proprietary_trading_income_TTM / total_operating_income_TTM", "ratio", "disclosure_only"),
    "operating_cost_ratio": ("operating_expenses_TTM / operating_income_TTM", "ratio", "lower"),
    "margin_loan_ratio": ("margin_loans_closing_balance / regulatory_equity_or_total_assets", "ratio", "higher_risk"),
    "MARGIN_LOAN_RATIO": ("margin_loans_closing_balance / regulatory_equity_or_total_assets", "ratio", "higher_risk"),
}

def _formula_contract_for_metric(metric_id):
    key = str(metric_id).strip()
    upper = key.upper()
    if upper in _FORMULA_TEMPLATES:
        formula, unit, source, direction, fields, status = _FORMULA_TEMPLATES[upper]
        return {"metric_id": key, "formula": formula, "unit": unit,
                "source_requirement": source, "direction": direction,
                "source_fields": list(fields), "formula_status": status,
                "threshold_status": "needs_calibration",
                "scoreable": False,
                "missing_data_policy": "exclude_metric_weight_no_redistribution"}
    if key in _MARGIN_METRICS:
        formula, direction = _MARGIN_METRICS[key]
        unit = "ratio"
    elif key in _DAYS_METRICS:
        formula, direction = _DAYS_METRICS[key]
        unit = "days"
    elif key in _RATIO_METRICS:
        formula, unit, direction = _RATIO_METRICS[key]
    elif key.endswith("_growth") or key in _GROWTH_METRICS:
        formula = f"{key}_current_comparable_period / {key}_prior_comparable_period - 1"
        unit, direction = "ratio", "higher"
    else:
        # These are company/exchange/regulatory disclosure KPIs, not fields
        # derivable from standard financial statements. The formula is the
        # explicitly defined reported KPI; it remains blocked until source
        # lineage and issuer coverage are validated.
        formula = f"reported_{key}_for_period (issuer_or_regulatory_disclosure)"
        unit, direction = "disclosed_KPI", "peer_context"
    return {"metric_id": key, "formula": formula, "unit": unit,
            "source_requirement": "issuer_exchange_or_regulatory_disclosure",
            "direction": direction, "source_fields": [],
            "formula_status": "formula_defined_source_unverified",
            "threshold_status": "needs_calibration", "scoreable": False,
            "missing_data_policy": "exclude_metric_weight_no_redistribution"}

def build_sector_formula_contracts():
    """Return explicit formula contracts for all configured sectors and groups."""
    contracts = {}
    for sector, groups in SECTOR_METRICS.items():
        sector_contract = {}
        for group, metric_ids in groups.items():
            sector_contract[group] = [
                _formula_contract_for_metric(metric_id) for metric_id in metric_ids
            ]
        contracts[sector] = sector_contract
    return contracts

SECTOR_FORMULA_CONTRACTS = build_sector_formula_contracts()

def validate_sector_formula_contracts():
    """Structural completeness only; does not certify source coverage or thresholds."""
    errors = []
    if set(SECTOR_FORMULA_CONTRACTS) != set(SECTOR_RULES):
        errors.append("formula_contract_sector_registry_mismatch")
    for sector, rule in SECTOR_RULES.items():
        groups = SECTOR_FORMULA_CONTRACTS.get(sector, {})
        if set(groups) != set(rule.get("weights", {})):
            errors.append(f"{sector}:formula_group_registry_mismatch")
        for group, metrics in groups.items():
            if not metrics:
                errors.append(f"{sector}.{group}:empty_metric_contract")
            for metric in metrics:
                if not metric.get("formula") or not metric.get("unit"):
                    errors.append(f"{sector}.{group}.{metric.get('metric_id')}:formula_or_unit_missing")
                if metric.get("threshold_status") != "needs_calibration":
                    errors.append(f"{sector}.{group}.{metric.get('metric_id')}:threshold_gate_missing")
                if metric.get("scoreable") is not False:
                    errors.append(f"{sector}.{group}.{metric.get('metric_id')}:unsafe_scoreable_default")
                if metric.get("missing_data_policy") != "exclude_metric_weight_no_redistribution":
                    errors.append(f"{sector}.{group}.{metric.get('metric_id')}:missing_data_policy_invalid")
    return {"valid": not errors, "sector_count": len(SECTOR_FORMULA_CONTRACTS),
            "metric_count": sum(len(ms) for gs in SECTOR_FORMULA_CONTRACTS.values()
                                for ms in gs.values()), "errors": errors}

# Formula-contract registry is intentionally separate from production approval; see tests/test_bctc_rules.py.
