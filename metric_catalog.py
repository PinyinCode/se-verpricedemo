# -*- coding: utf-8 -*-
"""Auditable metric catalog: verified source lineage versus explicit data gaps."""
from copy import deepcopy

from bctc_rules import SECTOR_METRICS, VCI_FIELD_MAP
from industry_profiles import PROFILE_TO_RULE, normalize_profile_id


METRIC_CATALOG_VERSION = "1.0.0"

METRIC_CATALOG = {
    "ROE": {
        "unit": "percent",
        "status": "verified_source_fields_formula_requires_average_equity",
        "formula": "net_profit_after_tax_TTM / average_owners_equity * 100",
        "source_fields": ["isa20", "bsa78"],
        "source_sections": {"income_statement": ["isa20"], "balance_sheet": ["bsa78"]},
        "period_basis": "TTM",
        "requires_average_balance": True,
        "sectors": ["NGAN_HANG", "CHUNG_KHOAN", "INSURANCE", "BAT_DONG_SAN",
                    "TECHNOLOGY", "BAN_LE", "STEEL", "FERTILIZER", "ENERGY",
                    "DIEN_NUOC_HA_TANG", "HOLDING", "MARITIME", "CONSUMER",
                    "BUILDING_MATERIALS", "AGRI", "NGUYEN_VAT_LIEU_CHU_KY",
                    "AVIATION", "CONSTRUCTION", "PETROLEUM", "PHARMA", "TEXTILE", "RUBBER", "PLASTICS_CHEMICALS", "AGRI_PROCESSING"],
        "notes": "Use consolidated net profit after tax and average owners equity. Do not use parent-attributable profit unless parent equity is separately verified.",
    },
    "ROA": {
        "unit": "percent",
        "status": "verified_source_fields_formula_requires_average_assets",
        "formula": "net_profit_after_tax_TTM / average_total_assets * 100",
        "source_fields": ["isa20", "bsa53"],
        "source_sections": {"income_statement": ["isa20"], "balance_sheet": ["bsa53"]},
        "period_basis": "TTM",
        "requires_average_balance": True,
        "sectors": ["NGAN_HANG", "CHUNG_KHOAN", "INSURANCE", "BAT_DONG_SAN",
                    "TECHNOLOGY", "BAN_LE", "STEEL", "FERTILIZER", "ENERGY",
                    "DIEN_NUOC_HA_TANG", "HOLDING", "MARITIME", "CONSUMER",
                    "BUILDING_MATERIALS", "AGRI", "NGUYEN_VAT_LIEU_CHU_KY",
                    "AVIATION", "CONSTRUCTION", "PETROLEUM", "PHARMA", "TEXTILE", "RUBBER", "PLASTICS_CHEMICALS", "AGRI_PROCESSING"],
        "notes": "Must use TTM net profit and average total assets; do not use only closing assets.",
    },
    "CREDIT_GROWTH": {
        "unit": "percent",
        "status": "verified_source_fields_formula_requires_same_quarter_yoy",
        "formula": "gross_customer_loans_current_quarter / gross_customer_loans_same_quarter_prior_year - 1",
        "source_fields": ["bsb104"],
        "source_sections": {"balance_sheet": ["bsb104"]},
        "period_basis": "YOY_POINT_IN_TIME",
        "sectors": ["NGAN_HANG"],
        "notes": "Growth is not an asset-quality measure; both point-in-time balances must be comparable.",
    },
    "LDR": {
        "unit": "percent",
        "status": "verified_source_fields_formula_available_after_unit_validation",
        "formula": "gross_customer_loans / customer_deposits * 100",
        "source_fields": ["bsb104", "bsb113"],
        "source_sections": {"balance_sheet": ["bsb104", "bsb113"]},
        "period_basis": "POINT_IN_TIME",
        "sectors": ["NGAN_HANG"],
        "notes": "Customer deposits are not CASA; compare LDR with relevant regulatory and peer context.",
    },
    "CIR": {
        "unit": "percent",
        "status": "verified_source_fields_formula_requires_verified_negative_expense",
        "formula": "abs(operating_expenses_TTM) / operating_income_TTM * 100",
        "source_fields": ["isb39", "isb38"],
        "source_sections": {"income_statement": ["isb39", "isb38"]},
        "period_basis": "TTM",
        "sectors": ["NGAN_HANG"],
        "notes": "isb39 is accepted only with the verified bank operating-expense interpretation and negative sign.",
    },
    "BANK_PPOP_MARGIN": {
        "unit": "percent",
        "status": "verified_source_fields_formula_requires_same_ttm_basis",
        "formula": "profit_before_credit_provisions_TTM / total_operating_income_TTM * 100",
        "source_fields": ["isb40", "isb38"],
        "source_sections": {"income_statement": ["isb40", "isb38"]},
        "period_basis": "TTM",
        "sectors": ["NGAN_HANG"],
        "notes": "PPOP is pre-provision operating profit; it is not net profit.",
    },
    "BANK_NON_INTEREST_INCOME_SHARE": {
        "unit": "percent",
        "status": "verified_source_fields_formula_requires_same_ttm_basis",
        "formula": "(total_operating_income_TTM - net_interest_income_TTM) / total_operating_income_TTM * 100",
        "source_fields": ["isb38", "isb27"],
        "source_sections": {"income_statement": ["isb38", "isb27"]},
        "period_basis": "TTM",
        "sectors": ["NGAN_HANG"],
        "notes": "Interpret as income diversification, not automatically as income quality.",
    },
    "CASH_CONVERSION": {
        "unit": "ratio",
        "status": "verified_source_fields_formula_non_financial_only",
        "formula": "operating_cash_flow_TTM / net_profit_after_tax_TTM",
        "source_fields": ["cfa18", "isa20"],
        "source_sections": {"cash_flow": ["cfa18"], "income_statement": ["isa20"]},
        "period_basis": "TTM",
        "sectors": ["BAT_DONG_SAN", "TECHNOLOGY", "BAN_LE", "STEEL",
                    "FERTILIZER", "ENERGY", "DIEN_NUOC_HA_TANG",
                    "MARITIME", "CONSUMER", "BUILDING_MATERIALS", "AGRI",
                    "NGUYEN_VAT_LIEU_CHU_KY", "AVIATION", "CONSTRUCTION",
                    "PETROLEUM", "PHARMA", "TEXTILE", "RUBBER", "PLASTICS_CHEMICALS", "AGRI_PROCESSING"],
        "notes": "Not applicable to banks and insurers; positive net profit and matching TTM basis required.",
    },
    "NIM": {
        "unit": "percent",
        "status": "unavailable_missing_average_interest_earning_assets",
        "formula": "net_interest_income_TTM / average_interest_earning_assets * 100",
        "source_fields": ["isb27"],
        "source_sections": {"income_statement": ["isb27"]},
        "period_basis": "TTM",
        "sectors": ["NGAN_HANG"],
        "notes": "Do not substitute total assets or customer loans for average interest-earning assets.",
    },
    "NPL_RATIO": {
        "unit": "percent",
        "status": "unavailable_npl_source_field_not_verified",
        "formula": "non_performing_loans / gross_customer_loans * 100",
        "source_fields": ["bsb104"],
        "source_sections": {"balance_sheet": ["bsb104"]},
        "period_basis": "POINT_IN_TIME",
        "sectors": ["NGAN_HANG"],
        "notes": "NPL balance remains unverified; label discovery candidates must not be scored.",
    },
    "GROUP_2_RATIO": {
        "unit": "percent",
        "status": "unavailable_group_2_source_field_not_verified",
        "formula": "group_2_loans / gross_customer_loans * 100",
        "source_fields": ["bsb104"],
        "source_sections": {"balance_sheet": ["bsb104"]},
        "period_basis": "POINT_IN_TIME",
        "sectors": ["NGAN_HANG"],
        "notes": "Group-2 loan balance must be mapped independently; never infer it from NPL or provisions.",
    },
    "NPL_COVERAGE": {
        "unit": "percent",
        "status": "unavailable_npl_and_provision_basis_not_verified",
        "formula": "loan_loss_provisions / non_performing_loans * 100",
        "source_fields": [],
        "source_sections": {},
        "period_basis": "POINT_IN_TIME",
        "sectors": ["NGAN_HANG"],
        "notes": "Requires verified NPL and matching provision balance; do not infer either input.",
    },
    "CAR": {
        "unit": "percent",
        "status": "unavailable_regulatory_capital_and_rwa_not_in_standard_bctc",
        "formula": "regulatory_capital / risk_weighted_assets * 100",
        "source_fields": [],
        "source_sections": {},
        "period_basis": "POINT_IN_TIME",
        "sectors": ["NGAN_HANG"],
        "notes": "Requires regulatory disclosure; equity/assets is not a substitute.",
    },
    "CASA": {
        "unit": "percent",
        "status": "unavailable_demand_deposit_source_field_not_verified",
        "formula": "demand_deposits / customer_deposits * 100",
        "source_fields": ["bsb113"],
        "source_sections": {"balance_sheet": ["bsb113"]},
        "period_basis": "POINT_IN_TIME",
        "sectors": ["NGAN_HANG"],
        "notes": "Customer deposits are known, but demand-deposit/CASA component is not verified.",
    },
    "INVENTORY_TURNOVER": {
        "unit": "times",
        "status": "unavailable_inventory_source_field_not_verified",
        "formula": "cost_of_goods_sold_TTM / average_inventory",
        "source_fields": [],
        "source_sections": {},
        "period_basis": "TTM",
        "sectors": ["STEEL", "FERTILIZER", "BAN_LE", "CONSUMER",
                    "BUILDING_MATERIALS"],
        "notes": "Requires verified inventory balances and cost-of-goods-sold mapping with consistent units.",
    },
    "CFO": {
        "unit": "VND",
        "status": "verified_source_field_formula_ttm_sum",
        "formula": "sum(quarterly_operating_cash_flow_over_four_consecutive_quarters)",
        "source_fields": ["cfa18"],
        "source_sections": {"cash_flow": ["cfa18"]},
        "period_basis": "TTM",
        "sectors": ["ENERGY", "DIEN_NUOC_HA_TANG", "MARITIME", "AGRI",
                    "NGUYEN_VAT_LIEU_CHU_KY", "AVIATION", "CONSTRUCTION",
                    "PETROLEUM", "BUILDING_MATERIALS", "CONSUMER", "BAN_LE",
                    "STEEL", "FERTILIZER", "BAT_DONG_SAN", "TECHNOLOGY", "PHARMA", "TEXTILE", "RUBBER", "PLASTICS_CHEMICALS", "AGRI_PROCESSING"],
        "notes": "CFO is a cash-flow measure; quarterize YTD source values before TTM aggregation.",
    },
    "FCF": {
        "unit": "VND",
        "status": "unavailable_capex_sign_and_completeness_not_verified",
        "formula": "operating_cash_flow_TTM - capital_expenditure_TTM",
        "source_fields": ["cfa18", "cfa19"],
        "source_sections": {"cash_flow": ["cfa18", "cfa19"]},
        "period_basis": "TTM",
        "sectors": ["STEEL", "FERTILIZER", "ENERGY", "DIEN_NUOC_HA_TANG",
                    "MARITIME", "CONSUMER", "BUILDING_MATERIALS"],
        "notes": "Requires complete capex definition and issuer-verified source sign convention.",
    },
    "INTEREST_COVERAGE": {
        "unit": "times",
        "status": "unavailable_ebit_and_interest_expense_source_fields_not_verified",
        "formula": "EBIT_TTM / abs(interest_expense_TTM)",
        "source_fields": [],
        "source_sections": {},
        "period_basis": "TTM",
        "sectors": ["BAT_DONG_SAN", "STEEL", "FERTILIZER",
                    "DIEN_NUOC_HA_TANG", "HOLDING", "BUILDING_MATERIALS"],
        "notes": "Requires separately verified EBIT and interest-expense fields with strict sign checks.",
    },
    "NET_DEBT": {
        "unit": "VND",
        "status": "unavailable_interest_bearing_debt_components_not_verified",
        "formula": "short_term_interest_bearing_debt + long_term_interest_bearing_debt - cash",
        "source_fields": [],
        "source_sections": {},
        "period_basis": "POINT_IN_TIME",
        "sectors": ["BAT_DONG_SAN", "STEEL", "FERTILIZER", "ENERGY",
                    "DIEN_NUOC_HA_TANG", "HOLDING", "MARITIME",
                    "BUILDING_MATERIALS"],
        "notes": "Do not substitute total liabilities for interest-bearing debt.",
    },
    "NET_DEBT_TO_EBITDA": {
        "unit": "ratio",
        "status": "unavailable_net_debt_and_ebitda_inputs_not_verified",
        "formula": "net_debt / EBITDA_TTM",
        "source_fields": [],
        "source_sections": {},
        "period_basis": "TTM",
        "sectors": ["BAT_DONG_SAN", "STEEL", "FERTILIZER", "ENERGY",
                    "DIEN_NUOC_HA_TANG", "HOLDING", "MARITIME",
                    "BUILDING_MATERIALS"],
        "notes": "Requires verified interest-bearing debt, cash and EBITDA; positive EBITDA is mandatory.",
    },
    "ROIC": {
        "unit": "percent",
        "status": "unavailable_nopat_and_invested_capital_components_not_verified",
        "formula": "NOPAT_TTM / average_invested_capital * 100",
        "source_fields": [],
        "source_sections": {},
        "period_basis": "TTM",
        "sectors": ["TECHNOLOGY", "STEEL", "FERTILIZER", "ENERGY",
                    "MARITIME", "BUILDING_MATERIALS"],
        "notes": "Do not approximate invested capital from total assets or NOPAT from net profit.",
    },
}


def get_metric_definition(metric_id):
    """Return a defensive copy of one metric definition."""
    definition = METRIC_CATALOG.get(str(metric_id or "").strip().upper())
    return deepcopy(definition) if definition else None


def get_sector_metric_catalog(sector):
    """Return required metric IDs with catalog availability for one sector."""
    raw_sector = str(sector or "").strip().upper()
    profile_id = normalize_profile_id(raw_sector)
    sector_id = PROFILE_TO_RULE.get(profile_id, raw_sector) if profile_id else raw_sector
    required = SECTOR_METRICS.get(sector_id)
    if required is None:
        return None
    result = {}
    for group, metric_ids in required.items():
        result[group] = []
        for metric_id in metric_ids:
            definition = METRIC_CATALOG.get(str(metric_id).upper())
            declared_sectors = set(definition.get("sectors", [])) if definition else set()
            sector_supported = bool(
                definition
                and (profile_id in declared_sectors or sector_id in declared_sectors)
            )
            catalog_status = (
                definition.get("status") if sector_supported
                else ("not_verified_for_sector" if definition else "not_catalogued")
            )
            result[group].append({
                "metric_id": metric_id,
                "catalog_status": catalog_status,
                "unit": definition.get("unit") if sector_supported else None,
                "period_basis": definition.get("period_basis") if sector_supported else None,
                "formula": definition.get("formula") if sector_supported else None,
                "source_sections": deepcopy(definition.get("source_sections", {}))
                    if sector_supported else {},
                "source_fields": list(definition.get("source_fields", []))
                    if sector_supported else [],
                "reason": (
                    definition.get("notes", "") if sector_supported
                    else ("Metric này chưa được xác minh cho ngành " + sector_id
                          if definition else "Chưa có định nghĩa nguồn dữ liệu đã xác minh.")
                ),
            })
    return result



def summarize_sector_metric_readiness(sector):
    """Count catalog readiness without treating source verification as score approval."""
    catalog = get_sector_metric_catalog(sector)
    if catalog is None:
        return {
            "sector": str(sector or "").strip().upper(),
            "known_sector": False,
            "required_metric_count": 0,
            "source_verified_count": 0,
            "source_mapping_coverage_pct": 0.0,
            "unavailable_count": 0,
            "unsupported_count": 0,
            "uncatalogued_count": 0,
            "unsupported_or_uncatalogued_count": 0,
            "readiness_status": "unknown_sector",
            "verified_metric_ids": [],
            "unavailable_metric_ids": [],
            "unsupported_metric_ids": [],
            "uncatalogued_metric_ids": [],
            "production_score_ready": False,
            "reason": "sector_metric_catalog_not_configured",
        }
    entries = [item for group in catalog.values() for item in group]
    verified = [
        item for item in entries
        if str(item.get("catalog_status") or "").startswith("verified")
    ]
    unavailable = [
        item for item in entries
        if str(item.get("catalog_status") or "").startswith("unavailable")
    ]
    unsupported = [
        item for item in entries
        if item.get("catalog_status") == "not_verified_for_sector"
    ]
    uncatalogued = [
        item for item in entries
        if item.get("catalog_status") == "not_catalogued"
    ]
    required_count = len(entries)
    verified_count = len(verified)
    coverage_pct = round(verified_count / required_count * 100, 2) if required_count else 0.0
    if unsupported or uncatalogued:
        readiness_status = "catalog_gaps"
    elif unavailable:
        readiness_status = "source_data_unavailable"
    elif verified_count == required_count and required_count:
        readiness_status = "source_lineage_catalogued"
    else:
        readiness_status = "no_verified_metrics"

    return {
        "sector": str(sector or "").strip().upper(),
        "known_sector": True,
        "required_metric_count": required_count,
        "source_verified_count": verified_count,
        "source_mapping_coverage_pct": coverage_pct,
        "unavailable_count": len(unavailable),
        "unsupported_count": len(unsupported),
        "uncatalogued_count": len(uncatalogued),
        "unsupported_or_uncatalogued_count": len(unsupported) + len(uncatalogued),
        "readiness_status": readiness_status,
        "verified_metric_ids": sorted(item["metric_id"] for item in verified),
        "unavailable_metric_ids": sorted(item["metric_id"] for item in unavailable),
        "unsupported_metric_ids": sorted(item["metric_id"] for item in unsupported),
        "uncatalogued_metric_ids": sorted(item["metric_id"] for item in uncatalogued),
        "production_score_ready": False,
        "reason": "Source mapping coverage is descriptive only; it does not approve formulas, thresholds, profile weights or historical parity.",
    }
def summarize_all_sector_metric_readiness():
    """Report profile-level readiness and deduplicated canonical sector views."""
    by_profile = {}
    totals = {
        "required_metric_count": 0,
        "source_verified_count": 0,
        "unavailable_count": 0,
        "unsupported_or_uncatalogued_count": 0,
    }
    for profile_id in sorted(PROFILE_TO_RULE):
        summary = summarize_sector_metric_readiness(profile_id)
        catalog = get_sector_metric_catalog(profile_id) or {}
        entries = [item for group in catalog.values() for item in group]
        verified = sorted(
            item["metric_id"] for item in entries
            if str(item.get("catalog_status") or "").startswith("verified")
        )
        unavailable = sorted(
            item["metric_id"] for item in entries
            if str(item.get("catalog_status") or "").startswith("unavailable")
        )
        unsupported = sorted(
            item["metric_id"] for item in entries
            if item.get("catalog_status") == "not_verified_for_sector"
        )
        uncatalogued = sorted(
            item["metric_id"] for item in entries
            if item.get("catalog_status") == "not_catalogued"
        )
        by_profile[profile_id] = {
            **summary,
            "profile_id": profile_id,
            "verified_metric_ids": verified,
            "unavailable_metric_ids": unavailable,
            "unsupported_metric_ids": unsupported,
            "uncatalogued_metric_ids": uncatalogued,
        }
        for key in totals:
            totals[key] += int(summary.get(key, 0))

    by_sector = {}
    for profile_id in sorted(PROFILE_TO_RULE):
        rule_id = PROFILE_TO_RULE[profile_id]
        if rule_id in by_sector:
            continue
        canonical_profile = (
            rule_id if PROFILE_TO_RULE.get(rule_id) == rule_id else profile_id
        )
        by_sector[rule_id] = {
            **by_profile[canonical_profile],
            "sector": rule_id,
        }

    return {
        "profile_count": len(by_profile),
        "sector_count": len(by_sector),
        **totals,
        "production_score_ready": False,
        "by_profile": by_profile,
        "by_sector": by_sector,
        "note": (
            "Profile aliases are reported separately; canonical sector totals "
            "are deduplicated. Source mapping coverage is not scoring approval; "
            "thresholds, historical parity and production profile approval remain separate gates."
        ),
    }


def metric_is_supported_for_sector(metric_id, sector):
    """Check metric applicability after canonical sector/profile normalization."""
    definition = METRIC_CATALOG.get(str(metric_id or "").strip().upper())
    profile_id = normalize_profile_id(sector)
    if not definition or not profile_id:
        return False
    sector_id = PROFILE_TO_RULE.get(profile_id, profile_id)
    declared_sectors = set(definition.get("sectors", []))
    return profile_id in declared_sectors or sector_id in declared_sectors


def validate_metric_catalog():
    """Check every declared source field exists in the canonical VCI dictionary."""
    errors = []
    valid_period_bases = {
        "QUARTERLY", "YTD", "ANNUAL", "TTM", "POINT_IN_TIME",
        "ANNUAL_FORECAST", "YOY_POINT_IN_TIME",
    }
    for metric_id, definition in METRIC_CATALOG.items():
        status = str(definition.get("status") or "").strip().lower()
        if not (
            status.startswith("verified")
            or status.startswith("unavailable")
        ):
            errors.append({
                "metric_id": metric_id,
                "code": "canonical_metric_status_invalid",
            })
        if str(definition.get("period_basis") or "").strip().upper() not in valid_period_bases:
            errors.append({
                "metric_id": metric_id,
                "code": "canonical_metric_period_basis_invalid",
            })
        sectors = definition.get("sectors")
        if (
            not isinstance(sectors, (list, tuple))
            or not sectors
            or any(not str(item).strip() for item in sectors)
            or len({str(item).strip() for item in sectors}) != len(sectors)
        ):
            errors.append({
                "metric_id": metric_id,
                "code": "canonical_metric_sectors_invalid",
            })
        known_sectors = set(PROFILE_TO_RULE) | set(PROFILE_TO_RULE.values())
        if isinstance(sectors, (list, tuple)):
            unknown_sectors = sorted(
                str(item).strip() for item in sectors
                if str(item).strip() not in known_sectors
            )
            if unknown_sectors:
                errors.append({
                    "metric_id": metric_id,
                    "code": "canonical_metric_sector_unknown",
                    "sectors": unknown_sectors,
                })
        if not str(definition.get("formula") or "").strip():
            errors.append({
                "metric_id": metric_id,
                "code": "canonical_metric_formula_missing",
            })
        if status.startswith("verified"):
            if not isinstance(definition.get("source_fields"), (list, tuple)) or not definition.get("source_fields"):
                errors.append({
                    "metric_id": metric_id,
                    "code": "verified_metric_source_fields_missing",
                })
            if not isinstance(definition.get("source_sections"), dict) or not definition.get("source_sections"):
                errors.append({
                    "metric_id": metric_id,
                    "code": "verified_metric_source_sections_missing",
                })
            if not str(definition.get("formula") or "").strip():
                errors.append({
                    "metric_id": metric_id,
                    "code": "verified_metric_formula_missing",
                })
            if not str(definition.get("unit") or "").strip():
                errors.append({
                    "metric_id": metric_id,
                    "code": "verified_metric_unit_missing",
                })
        if not str(definition.get("notes") or "").strip():
            errors.append({
                "metric_id": metric_id,
                "code": "canonical_metric_notes_missing",
            })
        if not str(definition.get("unit") or "").strip():
            errors.append({
                "metric_id": metric_id,
                "code": "canonical_metric_unit_missing",
            })
        for section, fields in (definition.get("source_sections") or {}).items():
            section_map = VCI_FIELD_MAP.get(section, {})
            for source_field in fields:
                if source_field not in section_map:
                    errors.append({
                        "metric_id": metric_id,
                        "code": "source_field_not_in_vci_dictionary",
                        "section": section,
                        "source_field": source_field,
                    })
        declared_sources = set(definition.get("source_fields") or [])
        section_sources = {
            field
            for fields in (definition.get("source_sections") or {}).values()
            for field in fields
        }
        if declared_sources != section_sources:
            errors.append({
                "metric_id": metric_id,
                "code": "source_fields_do_not_match_section_lineage",
            })
        if definition.get("status", "").startswith("unavailable") and definition.get("source_fields") == []:
            if not definition.get("notes"):
                errors.append({"metric_id": metric_id, "code": "unavailable_reason_missing"})
    return {"valid": not errors, "metric_count": len(METRIC_CATALOG), "errors": errors}
