# -*- coding: utf-8 -*-
"""Deterministic fixed-weight sector scoring orchestration.

This module is deliberately profile-agnostic: it does not invent sector
thresholds or source mappings. Callers must supply an enabled, versioned
profile and explicit metric specifications. Existing design-only profiles
are rejected from production scoring.
"""
from copy import deepcopy

from analysis_core import (
    aggregate_metric_scores,
    evaluate_data_gate,
    score_threshold_metric,
)
from metric_contract import validate_metric_record
from metric_catalog import metric_is_supported_for_sector


SCORING_ENGINE_VERSION = "1.0.0"


def _valid_positive_number(value):
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return number > 0 and number < float("inf")


def _metric_input_is_verified(metric, spec):
    result = validate_metric_record(metric, spec)
    return result["valid"], result["reason"]


def score_industry_profile(profile, metric_values, metric_specs,
                            minimum_coverage_pct=65.0,
                            blocking_reasons=None,
                            quality_validation=None):
    """Score an explicitly enabled profile; missing metrics never gain weight.

    profile:
      {profile_id, profile_version, status='production',
       scoring_enabled=True, score_weight, weights={group: points}}
    metric_specs:
      {metric_id: {group, weight, thresholds, direction,
                   period_basis, source_fields}}
    metric_values:
      {metric_id: {value, available, quality_status, period_basis,
                   source_fields, reason}}
    """
    if not isinstance(profile, dict):
        return {"status": "invalid_profile", "ready": False,
                "errors": ["profile_must_be_object"]}
    if profile.get("status") != "production" or profile.get("scoring_enabled") is not True:
        return {
            "status": "profile_not_enabled",
            "ready": False,
            "profile_id": profile.get("profile_id"),
            "errors": ["profile_not_approved_for_production_scoring"],
            "score": None,
            "coverage_pct": 0.0,
        }

    if not str(profile.get("profile_version") or "").strip():
        return {"status": "invalid_profile", "ready": False,
                "errors": ["profile_version_missing"]}

    approval = profile.get("approval")
    if not isinstance(approval, dict):
        return {
            "status": "profile_not_approved",
            "ready": False,
            "profile_id": profile.get("profile_id"),
            "errors": ["production_approval_record_missing"],
            "score": None,
            "coverage_pct": 0.0,
        }
    required_approval_fields = (
        "threshold_set_version", "approved_at", "reviewer", "evidence_ref",
        "parity_report_ref",
    )
    missing_approval_fields = [
        field for field in required_approval_fields
        if not str(approval.get(field) or "").strip()
    ]
    parity_sample_count = approval.get("parity_sample_count")
    parity_pass_rate = approval.get("parity_pass_rate")
    valid_parity_sample_count = (
        isinstance(parity_sample_count, int)
        and not isinstance(parity_sample_count, bool)
        and parity_sample_count >= 20
    )
    try:
        parity_pass_rate = float(parity_pass_rate)
    except (TypeError, ValueError, OverflowError):
        parity_pass_rate = None
    valid_parity_pass_rate = (
        parity_pass_rate is not None
        and 0.95 <= parity_pass_rate <= 1.0
    )
    if (
        approval.get("status") != "approved"
        or approval.get("historical_parity") != "passed"
        or approval.get("source_mapping_review") != "passed"
        or not valid_parity_sample_count
        or not valid_parity_pass_rate
        or missing_approval_fields
    ):
        return {
            "status": "profile_not_approved",
            "ready": False,
            "profile_id": profile.get("profile_id"),
            "errors": [{
                "code": "production_approval_incomplete",
                "missing_fields": missing_approval_fields,
                "status": approval.get("status"),
                "historical_parity": approval.get("historical_parity"),
                "source_mapping_review": approval.get("source_mapping_review"),
                "parity_sample_count": parity_sample_count,
                "parity_pass_rate": parity_pass_rate,
            }],
            "score": None,
            "coverage_pct": 0.0,
        }
    weights = profile.get("weights")
    specs = metric_specs if isinstance(metric_specs, dict) else {}
    if not isinstance(metric_specs, dict):
        return {"status": "invalid_metric_configuration", "ready": False,
                "errors": [{"code": "metric_specs_must_be_object"}]}
    if not isinstance(metric_values, dict):
        metric_values = {}
    if not isinstance(weights, dict) or not weights:
        return {"status": "invalid_profile", "ready": False,
                "errors": ["profile_weights_missing"]}
    expected_total = profile.get("score_weight")
    if not _valid_positive_number(expected_total):
        return {"status": "invalid_profile", "ready": False,
                "errors": ["profile_score_weight_invalid"]}
    try:
        if abs(sum(float(value) for value in weights.values()) - float(expected_total)) > 1e-9:
            raise ValueError
        if any(not _valid_positive_number(value) for value in weights.values()):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        return {"status": "invalid_profile", "ready": False,
                "errors": ["profile_group_weights_invalid"]}

    errors = []
    specs_by_group = {group: [] for group in weights}

    # A sector profile's required metric IDs are an immutable scoring contract.
    # Do not let runtime specs replace unavailable metrics with easier proxies.
    required_groups = profile.get("metric_groups")
    if required_groups is not None:
        if not isinstance(required_groups, dict) or set(required_groups) != set(weights):
            return {
                "status": "invalid_metric_configuration", "ready": False,
                "profile_id": profile.get("profile_id"),
                "errors": [{"code": "profile_metric_groups_mismatch"}],
            }
        required_ids = []
        for group, metric_ids in required_groups.items():
            if not isinstance(metric_ids, (list, tuple)) or not metric_ids:
                return {
                    "status": "invalid_metric_configuration", "ready": False,
                    "profile_id": profile.get("profile_id"),
                    "errors": [{"group": group, "code": "profile_metric_requirements_invalid"}],
                }
            required_ids.extend(str(metric_id) for metric_id in metric_ids)
        configured_ids = [str(metric_id) for metric_id in specs]
        if len(required_ids) != len(set(required_ids)) or set(configured_ids) != set(required_ids):
            return {
                "status": "invalid_metric_configuration", "ready": False,
                "profile_id": profile.get("profile_id"),
                "errors": [{
                    "code": "metric_specs_do_not_match_profile_requirements",
                    "missing": sorted(set(required_ids) - set(configured_ids)),
                    "unexpected": sorted(set(configured_ids) - set(required_ids)),
                }],
            }
        for metric_id, spec in specs.items():
            expected_group = next(
                (group for group, ids in required_groups.items()
                 if str(metric_id) in {str(item) for item in ids}),
                None,
            )
            if isinstance(spec, dict) and spec.get("group") != expected_group:
                return {
                    "status": "invalid_metric_configuration", "ready": False,
                    "profile_id": profile.get("profile_id"),
                    "errors": [{
                        "metric_id": metric_id,
                        "code": "metric_group_differs_from_profile_requirement",
                        "expected_group": expected_group,
                    }],
                }
    for metric_id, spec in specs.items():
        if not metric_is_supported_for_sector(metric_id, profile.get("profile_id")):
            errors.append({
                "metric_id": metric_id,
                "code": "metric_not_supported_for_profile",
                "profile_id": profile.get("profile_id"),
            })
            continue
        if not isinstance(spec, dict) or spec.get("group") not in weights:
            errors.append({"metric_id": metric_id, "code": "unknown_metric_group"})
            continue
        if not _valid_positive_number(spec.get("weight")):
            errors.append({"metric_id": metric_id, "code": "invalid_metric_weight"})
            continue
        if not str(spec.get("threshold_id") or "").strip():
            errors.append({"metric_id": metric_id, "code": "threshold_id_missing"})
            continue
        if not str(spec.get("period_basis") or "").strip():
            errors.append({"metric_id": metric_id, "code": "period_basis_missing"})
            continue
        if not isinstance(spec.get("source_fields"), (list, tuple)) or not spec.get("source_fields"):
            errors.append({"metric_id": metric_id, "code": "source_fields_missing"})
            continue
        try:
            score_threshold_metric(
                None, spec["weight"], spec.get("thresholds") or [],
                spec.get("direction", "higher"),
            )
        except (TypeError, ValueError):
            errors.append({"metric_id": metric_id, "code": "invalid_threshold_configuration"})
            continue
        specs_by_group[spec["group"]].append((metric_id, spec))

    for group, group_weight in weights.items():
        assigned = sum(float(spec["weight"]) for _, spec in specs_by_group[group])
        if abs(assigned - float(group_weight)) > 1e-9:
            errors.append({
                "group": group,
                "code": "metric_weights_do_not_equal_fixed_group_weight",
                "assigned": assigned,
                "expected": float(group_weight),
            })
    if errors:
        return {"status": "invalid_metric_configuration", "ready": False,
                "profile_id": profile.get("profile_id"), "errors": errors}

    group_results = {}
    metric_results = {}
    for group, group_weight in weights.items():
        results = []
        for metric_id, spec in specs_by_group[group]:
            metric = (metric_values or {}).get(metric_id)
            validation_spec = dict(spec, metric_id=metric_id)
            verified, reason = _metric_input_is_verified(metric, validation_spec)
            scored = score_threshold_metric(
                metric.get("value") if verified else None,
                spec["weight"],
                spec.get("thresholds") or [],
                spec.get("direction", "higher"),
            )
            scored.update({
                "metric_id": metric_id,
                "group": group,
                "threshold_id": spec.get("threshold_id"),
                "source_fields": list(metric.get("source_fields") or []) if verified else [],
                "period": metric.get("period") if verified else None,
                "unit": metric.get("unit") if verified else None,
                "period_basis": metric.get("period_basis") if verified else None,
                "reason": "" if verified else reason,
            })
            results.append(scored)
            metric_results[metric_id] = deepcopy(scored)
        group_results[group] = aggregate_metric_scores(results, group_weight)

    # Aggregate individual metric weights, not whole group caps: a partially
    # populated group must not make its unavailable sibling metrics look covered.
    aggregate = aggregate_metric_scores(
        list(metric_results.values()),
        float(expected_total),
    )
    blockers = (
        list(blocking_reasons)
        if isinstance(blocking_reasons, (list, tuple, set)) else []
    )
    if not isinstance(quality_validation, dict):
        blockers.append("bctc_quality_validation_missing")
    elif quality_validation.get("valid") is not True:
        blockers.append("bctc_quality_validation_failed")
    elif quality_validation.get("scoreable") is not True:
        blockers.append("bctc_data_not_scoreable")
    # Missing metrics reduce available weight and coverage; they do not
    # independently block a profile that still clears its fixed coverage gate.
    gate = evaluate_data_gate(
        aggregate["coverage_pct"], minimum_coverage_pct, blockers
    )
    return {
        "status": "ready" if gate["ready"] else "insufficient_data",
        "ready": gate["ready"],
        "engine_version": SCORING_ENGINE_VERSION,
        "profile_id": profile.get("profile_id"),
        "profile_version": profile.get("profile_version"),
        "score": aggregate["score"],
        "score_weight": aggregate["expected_weight"],
        "score_pct_of_fixed_total": aggregate["score_pct_of_fixed_total"],
        "coverage_pct": aggregate["coverage_pct"],
        "available_weight": aggregate["available_weight"],
        "missing_weight": aggregate["missing_weight"],
        "available_metric_count": aggregate["available_metric_count"],
        "missing_metric_count": aggregate["missing_metric_count"],
        "group_results": group_results,
        "metric_results": metric_results,
        "data_gate": gate,
        "errors": [],
    }
