# -*- coding: utf-8 -*-
"""Industry profile registry for the deterministic analysis engine.

This is a configuration/validation layer only. It does not change the
production scoring path in price_api.py. Profiles backed by SECTOR_RULES are
marked design_only until their metric definitions, source fields, period
basis, thresholds, and historical parity tests are completed.
"""
from copy import deepcopy

from bctc_rules import SECTOR_METRICS, SECTOR_RULES


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
    "AGRI": "AGRI",
    "NGUYEN_VAT_LIEU_CHU_KY": "NGUYEN_VAT_LIEU_CHU_KY",
    "AVIATION": "AVIATION",
    "CONSTRUCTION": "CONSTRUCTION",
    "PETROLEUM": "PETROLEUM",
    "PHARMA": "PHARMA",
    "TEXTILE": "TEXTILE",
    "RUBBER": "RUBBER",
    "PLASTICS_CHEMICALS": "PLASTICS_CHEMICALS",
    "AGRI_PROCESSING": "AGRI_PROCESSING",
}

# Explicit routing audit for the existing deterministic production scorer.
# These names describe the actual branch in price_api.evaluate_stock_from_db;
# they do not certify industry-specific source coverage or threshold approval.
LEGACY_SCORING_PATHS = {
    "NGAN_HANG": "sector == NGAN_HANG",
    "CHUNG_KHOAN": "sector == CHUNG_KHOAN",
    "BAT_DONG_SAN": "sector == BAT_DONG_SAN",
    "TECHNOLOGY": "is_technology",
    "BAN_LE": "is_retail",
    "CONG_NGHE_BAN_LE": "is_retail",
    "STEEL": "is_steel",
    "FERTILIZER": "is_fertilizer",
    "ENERGY": "is_energy",
    "DIEN_NUOC_HA_TANG": "is_utility",
    "HOLDING": "is_holding",
    "MARITIME": "is_maritime",
    "CONSUMER": "is_consumer",
    "BUILDING_MATERIALS": "is_building_materials",
    "INSURANCE": "is_insurance",
    "AGRI": "is_agri",
    "NGUYEN_VAT_LIEU_CHU_KY": "is_cyclical_material",
    "AVIATION": "is_aviation",
    "CONSTRUCTION": "is_construction",
    "PETROLEUM": "is_petroleum",
    "PHARMA": "is_pharma",
    "TEXTILE": "is_textile",
    "RUBBER": "is_rubber",
    "PLASTICS_CHEMICALS": "is_plastics_chemicals",
    "AGRI_PROCESSING": "is_agri_processing",
}

# Maximum fixed fundamental weight currently backed by implemented BCTC inputs
# when every recognized input is present. This is not actual issuer coverage.
LEGACY_SCORING_AVAILABLE_WEIGHTS = {
    "NGAN_HANG": 24,
    "CHUNG_KHOAN": 18,
    "INSURANCE": 18,
    "BAT_DONG_SAN": 36,
    "TECHNOLOGY": 34,
    "BAN_LE": 30,
    "CONG_NGHE_BAN_LE": 30,
    "STEEL": 28,
    "FERTILIZER": 28,
    "ENERGY": 30,
    "DIEN_NUOC_HA_TANG": 24,
    "HOLDING": 34,
    "MARITIME": 36,
    "CONSUMER": 32,
    "BUILDING_MATERIALS": 30,
    "AGRI": 30,
    "NGUYEN_VAT_LIEU_CHU_KY": 28,
    "AVIATION": 30,
    "CONSTRUCTION": 36,
    "PETROLEUM": 20,
    "PHARMA": 40,
    "TEXTILE": 40,
    "RUBBER": 40,
    "PLASTICS_CHEMICALS": 40,
    "AGRI_PROCESSING": 40,
}

# Groups without a defensible profile are deliberately not assigned a
# neighboring industry's thresholds or weights.
UNCONFIGURED_PROFILES = {}

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
    "PHARMA": "PHARMA",
    "TEXTILE": "TEXTILE",
    "RUBBER": "RUBBER",
    "PLASTICS_CHEMICALS": "PLASTICS_CHEMICALS",
    "AGRI_PROCESSING": "AGRI_PROCESSING",
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
    "PHARMA": (
        "REGISTERED_PRODUCT_PORTFOLIO", "ETHICAL_DRUG_SHARE",
        "PRODUCT_PIPELINE", "EXPIRY_AND_WRITE_OFF_RISK",
    ),
    "TEXTILE": (
        "EXPORT_ORDER_BACKLOG", "LABOR_PRODUCTIVITY", "CAPACITY_UTILIZATION",
        "CUSTOMER_CONCENTRATION", "COMPLIANCE_AND_ORIGIN_RISK",
    ),
    "RUBBER": (
        "REPLACEMENT_TIRE_SHARE", "NATURAL_RUBBER_INPUT_SPREAD",
        "EXPORT_MARKET_MIX", "INVENTORY_QUALITY",
    ),
    "PLASTICS_CHEMICALS": (
        "RESIN_INPUT_SPREAD", "VALUE_ADDED_PRODUCT_MIX",
        "CAPACITY_UTILIZATION", "ENERGY_COST_SENSITIVITY",
    ),
    "AGRI_PROCESSING": (
        "SEGMENT_REVENUE_MIX", "FEEDSTOCK_SECURITY",
        "BIOLOGICAL_ASSET_QUALITY", "EXPORT_MARKET_CONCENTRATION",
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
                "profile_version": "1.0.0",
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
            "profile_version": "1.0.0",
            "status": "design_only",
            "rule_source": rule_key,
            "score_weight": 50,
            "weights": deepcopy(rule.get("weights", {})),
            "metric_groups": deepcopy(SECTOR_METRICS.get(rule_key, {})),
            "metric_blueprint_status": "requirements_only_no_approved_thresholds",
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
            "profile_version": "1.0.0",
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



def summarize_all_sector_scoring_readiness():
    """Combine profile design, legacy scorer routing and verified-source coverage."""
    from metric_catalog import summarize_sector_metric_readiness

    rows = {}
    for profile_id in sorted(INDUSTRY_PROFILES):
        profile = INDUSTRY_PROFILES[profile_id]
        metric_readiness = summarize_sector_metric_readiness(profile_id)
        path = LEGACY_SCORING_PATHS.get(profile_id)
        rows[profile_id] = {
            "profile_id": profile_id,
            "profile_status": profile.get("status"),
            "profile_scoring_enabled": profile.get("scoring_enabled") is True,
            "legacy_scoring_path": path,
            "legacy_scoring_path_configured": bool(path),
            "legacy_max_available_weight": LEGACY_SCORING_AVAILABLE_WEIGHTS.get(profile_id, 0),
            "legacy_missing_weight": max(
                float(profile.get("score_weight", 50) or 50)
                - LEGACY_SCORING_AVAILABLE_WEIGHTS.get(profile_id, 0), 0.0
            ),
            "legacy_max_bctc_coverage_pct": round(
                min(
                    LEGACY_SCORING_AVAILABLE_WEIGHTS.get(profile_id, 0),
                    float(profile.get("score_weight", 50) or 50),
                )
                / max(float(profile.get("score_weight", 50) or 50), 1) * 100, 1
            ),
            "legacy_can_clear_65pct_gate": (
                LEGACY_SCORING_AVAILABLE_WEIGHTS.get(profile_id, 0)
                >= float(profile.get("score_weight", 50) or 50) * 0.65
            ),
            "legacy_coverage_gate_status": (
                "can_clear_65pct"
                if LEGACY_SCORING_AVAILABLE_WEIGHTS.get(profile_id, 0)
                >= float(profile.get("score_weight", 50) or 50) * 0.65
                else "below_65pct"
            ),
            "legacy_trial_status": (
                "trial_partial"
                if path and LEGACY_SCORING_AVAILABLE_WEIGHTS.get(profile_id, 0)
                < float(profile.get("score_weight", 50) or 50)
                else "trial_bctc_coverage_possible"
                if path else "no_legacy_scoring_path"
            ),
            "score_weight": profile.get("score_weight", 0),
            "metric_group_count": len(profile.get("metric_groups") or {}),
            "required_metric_count": metric_readiness.get("required_metric_count", 0),
            "source_verified_count": metric_readiness.get("source_verified_count", 0),
            "source_mapping_coverage_pct": metric_readiness.get(
                "source_mapping_coverage_pct", 0.0
            ),
            "metric_readiness_status": metric_readiness.get(
                "readiness_status", "unknown_sector"
            ),
            "verified_metric_ids": list(metric_readiness.get("verified_metric_ids") or []),
            "unavailable_metric_ids": list(metric_readiness.get("unavailable_metric_ids") or []),
            "unsupported_metric_ids": list(metric_readiness.get("unsupported_metric_ids") or []),
            "uncatalogued_metric_ids": list(metric_readiness.get("uncatalogued_metric_ids") or []),
            "unavailable_count": metric_readiness.get("unavailable_count", 0),
            "unsupported_or_uncatalogued_count": metric_readiness.get(
                "unsupported_or_uncatalogued_count", 0
            ),
            "data_gaps": list(profile.get("data_gaps") or []),
            "production_score_ready": False,
            "note": (
                "Legacy score branch exists; this is not approval of the new "
                "metric profile, source completeness, thresholds or historical parity."
                if path else "No explicit legacy scoring branch is registered."
            ),
        }
    return {
        "profile_count": len(rows),
        "legacy_path_count": sum(
            1 for item in rows.values() if item["legacy_scoring_path_configured"]
        ),
        "legacy_trial_partial_count": sum(
            1 for item in rows.values() if item["legacy_trial_status"] == "trial_partial"
        ),
        "legacy_trial_coverage_possible_count": sum(
            1 for item in rows.values()
            if item["legacy_can_clear_65pct_gate"]
        ),
        "legacy_trial_below_65pct_count": sum(
            1 for item in rows.values() if not item["legacy_can_clear_65pct_gate"]
        ),
        "profile_scoring_enabled_count": sum(
            1 for item in rows.values() if item["profile_scoring_enabled"]
        ),
        "production_score_ready_count": 0,
        "all_profiles_have_legacy_path": all(
            item["legacy_scoring_path_configured"] for item in rows.values()
        ),
        "by_profile": rows,
        "note": (
            "Legacy paths indicate existing score branches only. New profile scoring "
            "stays disabled until verified sector metrics, thresholds and historical "
            "parity have been approved."
        ),
    }

def validate_industry_profiles(profiles=None):
    """Validate profile structure and require auditable approval for production profiles."""
    registry = profiles if isinstance(profiles, dict) else INDUSTRY_PROFILES
    errors = []
    warnings = []
    for profile_id, profile in registry.items():
        if not isinstance(profile, dict):
            errors.append({"profile": profile_id, "code": "profile_must_be_object"})
            continue
        if not str(profile.get("profile_version") or "").strip():
            errors.append({"profile": profile_id, "code": "profile_version_missing"})
        status = profile.get("status")
        if status == "design_only":
            weights = profile.get("weights", {})
            if not weights:
                errors.append({"profile": profile_id, "code": "missing_weights"})
            metric_groups = profile.get("metric_groups") or {}
            if set(metric_groups) != set(weights):
                errors.append({"profile": profile_id, "code": "metric_groups_do_not_match_weights"})
            if any(not isinstance(items, (list, tuple)) or not items
                   for items in metric_groups.values()):
                errors.append({"profile": profile_id, "code": "empty_metric_requirements"})
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
        elif status == "production":
            if profile.get("scoring_enabled") is not True:
                errors.append({"profile": profile_id, "code": "production_profile_not_enabled"})
            approval = profile.get("approval")
            required_fields = (
                "threshold_set_version", "approved_at", "reviewer",
                "evidence_ref", "parity_report_ref",
            )
            missing = [
                field for field in required_fields
                if not isinstance(approval, dict)
                or not str(approval.get(field) or "").strip()
            ]
            parity_count = (
                approval.get("parity_sample_count")
                if isinstance(approval, dict) else None
            )
            try:
                parity_rate = float(
                    approval.get("parity_pass_rate")
                    if isinstance(approval, dict) else None
                )
            except (TypeError, ValueError, OverflowError):
                parity_rate = None
            parity_valid = (
                isinstance(parity_count, int)
                and not isinstance(parity_count, bool)
                and parity_count >= 20
                and parity_rate is not None
                and 0.95 <= parity_rate <= 1.0
            )
            if (
                not isinstance(approval, dict)
                or approval.get("status") != "approved"
                or approval.get("source_mapping_review") != "passed"
                or approval.get("historical_parity") != "passed"
                or not parity_valid
                or missing
            ):
                errors.append({
                    "profile": profile_id,
                    "code": "production_approval_incomplete",
                    "missing_fields": missing,
                })
            weights = profile.get("weights") or {}
            groups = profile.get("metric_groups") or {}
            if not weights or set(groups) != set(weights):
                errors.append({"profile": profile_id, "code": "production_metric_groups_invalid"})
            if any(not isinstance(items, (list, tuple)) or not items for items in groups.values()):
                errors.append({"profile": profile_id, "code": "production_metric_requirements_invalid"})
            if any(
                isinstance(weight, bool) or not isinstance(weight, (int, float))
                or weight <= 0 for weight in weights.values()
            ) or sum(weights.values()) != profile.get("score_weight"):
                errors.append({"profile": profile_id, "code": "production_weights_invalid"})
        elif status == "not_configured":
            if profile.get("weights") or profile.get("scoring_enabled") is not False:
                errors.append({"profile": profile_id, "code": "unsafe_unconfigured_profile"})
        else:
            errors.append({"profile": profile_id, "code": "unknown_profile_status"})
    return {
        "valid": not errors,
        "profile_count": len(registry),
        "configured_count": sum(
            1 for profile in registry.values()
            if isinstance(profile, dict) and profile.get("status") == "design_only"
        ),
        "unconfigured_count": sum(
            1 for profile in registry.values()
            if isinstance(profile, dict) and profile.get("status") == "not_configured"
        ),
        "production_count": sum(
            1 for profile in registry.values()
            if isinstance(profile, dict) and profile.get("status") == "production"
        ),
        "errors": errors,
        "warnings": warnings,
    }
