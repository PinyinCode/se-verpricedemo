# -*- coding: utf-8 -*-
"""Industry profile registry for the deterministic analysis engine.

This is a configuration/validation layer only. It does not change the
production scoring path in price_api.py. Profiles backed by SECTOR_RULES are
marked design_only until their metric definitions, source fields, period
basis, thresholds, and historical parity tests are completed.
"""
from copy import deepcopy

from bctc_rules import SECTOR_RULES


PROFILE_TO_RULE = {
    "NGAN_HANG": "NGAN_HANG",
    "CHUNG_KHOAN": "CHUNG_KHOAN",
    "BAT_DONG_SAN": "BAT_DONG_SAN",
    "TECHNOLOGY": "CONG_NGHE",
    "BAN_LE": "BAN_LE",
    "CONG_NGHE_BAN_LE": "BAN_LE",
    "STEEL": "THEP",
    "FERTILIZER": "PHAN_BON",
    "ENERGY": "NANG_LUONG",
    "DIEN_NUOC_HA_TANG": "TIEN_ICH",
    "HOLDING": "HOLDING",
    "MARITIME": "CANG_LOGISTICS",
    "CONSUMER": "TIEU_DUNG",
    "BUILDING_MATERIALS": "VAT_LIEU_XAY_DUNG",
    "INSURANCE": "INSURANCE",
}

# Groups without a defensible profile are deliberately not assigned a
# neighboring industry's thresholds or weights.
UNCONFIGURED_PROFILES = {
    "AGRI": {
        "status": "not_configured",
        "reason": "Need separate aquaculture, livestock, crop and agricultural-input profiles with commodity-specific data.",
    },
    "NGUYEN_VAT_LIEU_CHU_KY": {
        "status": "not_configured",
        "reason": "This umbrella group mixes steel, chemicals, fertilizers and other materials; split into sub-industries before assigning weights.",
    },
    "AVIATION": {
        "status": "not_configured",
        "reason": "Need fleet/lease liabilities, load factor, yield and route economics data.",
    },
    "CONSTRUCTION": {
        "status": "not_configured",
        "reason": "Need backlog quality, receivables aging, contract cash flow and project data.",
    },
    "PETROLEUM": {
        "status": "not_configured",
        "reason": "Need separate upstream/downstream/refining business drivers and cycle normalization.",
    },
}

# Canonical profile aliases only; no fuzzy matching.
PROFILE_ALIASES = {
    "NGAN_HANG": "NGAN_HANG",
    "CHUNG_KHOAN": "CHUNG_KHOAN",
    "BAT_DONG_SAN": "BAT_DONG_SAN",
    "TECHNOLOGY": "TECHNOLOGY",
    "CONG_NGHE": "TECHNOLOGY",
    "BAN_LE": "BAN_LE",
    "CONG_NGHE_BAN_LE": "CONG_NGHE_BAN_LE",
    "STEEL": "STEEL",
    "THEP": "STEEL",
    "FERTILIZER": "FERTILIZER",
    "PHAN_BON": "FERTILIZER",
    "ENERGY": "ENERGY",
    "NANG_LUONG": "ENERGY",
    "DIEN_NUOC_HA_TANG": "DIEN_NUOC_HA_TANG",
    "TIEN_ICH": "DIEN_NUOC_HA_TANG",
    "HOLDING": "HOLDING",
    "MARITIME": "MARITIME",
    "CANG_LOGISTICS": "MARITIME",
    "CONSUMER": "CONSUMER",
    "TIEU_DUNG": "CONSUMER",
    "AGRI": "AGRI",
    "BUILDING_MATERIALS": "BUILDING_MATERIALS",
    "VAT_LIEU_XAY_DUNG": "BUILDING_MATERIALS",
    "INSURANCE": "INSURANCE",
    "NGUYEN_VAT_LIEU_CHU_KY": "NGUYEN_VAT_LIEU_CHU_KY",
    "AVIATION": "AVIATION",
    "CONSTRUCTION": "CONSTRUCTION",
    "PETROLEUM": "PETROLEUM",
}

# These are not scoreable inputs until their extraction/definition is proven.
# Keep them visible so a missing data pipeline cannot be mistaken for zero.
PROFILE_DATA_GAPS = {
    "NGAN_HANG": (
        "NPL_RATIO", "NPL_COVERAGE", "GROUP_2_LOANS", "CAR", "CASA",
    ),
    "CHUNG_KHOAN": (
        "MARGIN_LOAN_BOOK", "PROPRIETARY_TRADING_RISK", "BROKERAGE_SHARE",
        "CAPITAL_ADEQUACY",
    ),
    "BAT_DONG_SAN": (
        "PROJECT_RNAV", "LEGAL_STATUS", "PRESALES", "UNSOLD_INVENTORY",
        "RECEIVABLES_AGING",
    ),
    "TECHNOLOGY": (
        "RECURRING_REVENUE", "ROIC", "BACKLOG_OR_CONTRACTED_REVENUE",
    ),
    "BAN_LE": (
        "INVENTORY_TURNOVER", "SAME_STORE_SALES", "STORE_PRODUCTIVITY",
        "LEASE_ADJUSTED_DEBT",
    ),
    "STEEL": (
        "MID_CYCLE_MARGIN", "INVENTORY_DAYS", "RAW_MATERIAL_SPREAD",
        "CAPACITY_UTILIZATION",
    ),
    "FERTILIZER": (
        "MID_CYCLE_MARGIN", "INVENTORY_DAYS", "INPUT_OUTPUT_SPREAD",
        "CAPACITY_UTILIZATION",
    ),
    "ENERGY": (
        "PRODUCTION_VOLUME", "REALIZED_PRICE", "CONTRACTED_CASHFLOW",
        "PROJECT_CAPEX_PROGRESS",
    ),
    "DIEN_NUOC_HA_TANG": (
        "REGULATED_ASSET_BASE", "PROJECT_CAPEX", "DEBT_MATURITY",
        "CASH_DIVIDEND_COVERAGE",
    ),
    "HOLDING": (
        "LOOK_THROUGH_NAV", "SUBSIDIARY_VALUE", "PARENT_ONLY_DEBT",
        "HOLDING_DISCOUNT",
    ),
    "MARITIME": (
        "THROUGHPUT", "FLEET_UTILIZATION", "CHARTER_RATE",
        "BACKLOG", "CAPEX_COMMITMENT",
    ),
    "CONSUMER": (
        "BRAND_MIX", "MARKET_SHARE", "INVENTORY_TURNOVER",
        "DISTRIBUTION_EFFICIENCY",
    ),
    "AGRI": (
        "COMMODITY_CYCLE_MARGIN", "BIOLOGICAL_ASSET_RISK",
        "INVENTORY_QUALITY", "INPUT_COST_SPREAD",
    ),
    "BUILDING_MATERIALS": (
        "MID_CYCLE_MARGIN", "INVENTORY_DAYS", "ENERGY_COST_SENSITIVITY",
        "CAPACITY_UTILIZATION",
    ),
    "INSURANCE": (
        "SOLVENCY_MARGIN", "RESERVE_ADEQUACY", "LOSS_RATIO",
        "COMBINED_RATIO",
    ),
    "NGUYEN_VAT_LIEU_CHU_KY": (
        "MID_CYCLE_MARGIN", "INVENTORY_DAYS", "INPUT_OUTPUT_SPREAD",
        "CAPACITY_UTILIZATION",
    ),
    "AVIATION": (
        "LOAD_FACTOR", "YIELD_PER_PASSENGER", "FLEET_UTILIZATION",
        "LEASE_ADJUSTED_NET_DEBT",
    ),
    "CONSTRUCTION": (
        "BACKLOG_QUALITY", "RECEIVABLES_AGING", "CONTRACT_ASSET_QUALITY",
        "PROJECT_CASH_CONVERSION",
    ),
    "PETROLEUM": (
        "UPSTREAM_DOWNSTREAM_SEGMENT_SPLIT", "REALIZED_OIL_PRICE",
        "REFINING_MARGIN", "RESERVE_LIFE",
    ),
}


def _build_profiles():
    profiles = {}
    for profile_id, rule_key in PROFILE_TO_RULE.items():
        rule = SECTOR_RULES.get(rule_key)
        if rule is None:
            # A missing rule is a configuration error, not a zero-weight profile.
            profiles[profile_id] = {
                "profile_id": profile_id,
                "status": "not_configured",
                "reason": "No matching sector rule exists.",
                "score_weight": 50,
                "weights": {},
                "valuation_methods": [],
                "data_gaps": list(PROFILE_DATA_GAPS.get(profile_id, ())),
            }
            continue
        profiles[profile_id] = {
            "profile_id": profile_id,
            "status": "design_only",
            "rule_source": rule_key,
            "score_weight": 50,
            "weights": deepcopy(rule.get("weights", {})),
            "valuation_methods": list(rule.get("valuation", ())),
            "excluded_metrics": list(rule.get("exclude", ())),
            "data_gaps": list(PROFILE_DATA_GAPS.get(profile_id, ())),
            "scoring_enabled": False,
            "status_note": (
                "Blueprint only. Do not route production scores through this "
                "profile until source mapping, period basis, thresholds and "
                "historical parity tests are approved."
            ),
        }
    for profile_id, item in UNCONFIGURED_PROFILES.items():
        profiles[profile_id] = {
            "profile_id": profile_id,
            "status": item["status"],
            "reason": item["reason"],
            "score_weight": 50,
            "weights": {},
            "valuation_methods": [],
            "data_gaps": list(PROFILE_DATA_GAPS.get(profile_id, ())),
            "scoring_enabled": False,
        }
    return profiles


INDUSTRY_PROFILES = _build_profiles()


def normalize_profile_id(value):
    key = str(value or "").strip().upper()
    return PROFILE_ALIASES.get(key)


def get_industry_profile(value):
    """Return a defensive copy; unknown groups never inherit a generic profile."""
    profile_id = normalize_profile_id(value)
    if profile_id is None:
        return None
    profile = INDUSTRY_PROFILES.get(profile_id)
    return deepcopy(profile) if profile else None


def validate_industry_profiles():
    """Return configuration diagnostics; no profile is enabled by this check."""
    errors = []
    warnings = []
    for profile_id, profile in INDUSTRY_PROFILES.items():
        if profile.get("status") == "design_only":
            weights = profile.get("weights", {})
            if not weights:
                errors.append({"profile": profile_id, "code": "missing_weights"})
            if any(
                isinstance(weight, bool) or not isinstance(weight, (int, float))
                or weight <= 0 for weight in weights.values()
            ):
                errors.append({"profile": profile_id, "code": "invalid_weight"})
            if sum(weights.values()) != profile.get("score_weight"):
                errors.append({
                    "profile": profile_id,
                    "code": "weight_total_mismatch",
                    "actual": sum(weights.values()),
                    "expected": profile.get("score_weight"),
                })
            if profile.get("scoring_enabled") is not False:
                errors.append({"profile": profile_id, "code": "prematurely_enabled"})
            warnings.append({
                "profile": profile_id,
                "code": "design_only_not_production_scoring",
            })
        elif profile.get("status") == "not_configured":
            if profile.get("weights") or profile.get("scoring_enabled") is not False:
                errors.append({"profile": profile_id, "code": "unsafe_unconfigured_profile"})
    return {
        "valid": not errors,
        "profile_count": len(INDUSTRY_PROFILES),
        "configured_count": sum(
            1 for profile in INDUSTRY_PROFILES.values()
            if profile.get("status") == "design_only"
        ),
        "unconfigured_count": sum(
            1 for profile in INDUSTRY_PROFILES.values()
            if profile.get("status") == "not_configured"
        ),
        "errors": errors,
        "warnings": warnings,
    }
