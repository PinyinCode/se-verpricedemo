# -*- coding: utf-8 -*-
"""End-to-end deterministic analysis orchestration; no database or AI calls."""
from math import isfinite

from data_quality import get_vci_reconciliation_equations, validate_bctc_rows
from decision_engine import evaluate_analysis_readiness
from report_templates import render_analysis_report
from scoring_engine import score_industry_profile


SUPPORTED_SECTIONS = ("balance_sheet", "income_statement", "cash_flow")


def run_analysis_pipeline(profile, statement_rows, period_basis_by_section,
                          metric_values, metric_specs, required_sections,
                          primary_valuation=None, market_price=None,
                          minimum_coverage_pct=65.0,
                          expected_units_by_section=None,
                          sign_policies_by_section=None,
                          required_fields_by_section=None):
    """Validate statements, score verified metrics, evaluate readiness and report.

    This function deliberately does not extract raw MongoDB data or invent
    metric formulas. The caller must supply normalized statement rows and
    metric records carrying verified source lineage.
    """
    if not isinstance(statement_rows, dict):
        raise ValueError("statement_rows_must_be_object")
    if not isinstance(period_basis_by_section, dict):
        raise ValueError("period_basis_by_section_must_be_object")
    if not isinstance(metric_values, dict) or not isinstance(metric_specs, dict):
        raise ValueError("metric_values_and_specs_must_be_objects")
    sections = list(dict.fromkeys(required_sections or []))
    if not sections or any(section not in SUPPORTED_SECTIONS for section in sections):
        raise ValueError("required_sections_must_be_supported_and_nonempty")

    expected_units_by_section = expected_units_by_section or {}
    sign_policies_by_section = sign_policies_by_section or {}
    required_fields_by_section = required_fields_by_section or {}
    section_results = {}
    combined_errors = []
    combined_warnings = []
    all_valid = True
    all_scoreable = True

    for section in sections:
        rows = statement_rows.get(section)
        if not isinstance(rows, list) or not rows:
            section_results[section] = {
                "valid": False, "scoreable": False,
                "errors": [{"code": "required_statement_rows_missing"}],
                "warnings": [],
            }
            all_valid = False
            all_scoreable = False
            combined_errors.append(section + ":required_statement_rows_missing")
            continue

        result = validate_bctc_rows(
            rows,
            section,
            expected_units=expected_units_by_section.get(section),
            sign_policies=sign_policies_by_section.get(section),
            required_fields=required_fields_by_section.get(section),
            equations=get_vci_reconciliation_equations(section),
            period_basis=period_basis_by_section.get(section),
        )
        section_results[section] = result
        if result["valid"] is not True:
            all_valid = False
            combined_errors.extend(
                section + ":" + str(item.get("code"))
                for item in result.get("errors", [])
            )
        if result["scoreable"] is not True:
            all_scoreable = False
            combined_errors.append(section + ":statement_not_scoreable")
        combined_warnings.extend(
            section + ":" + str(item.get("code"))
            for item in result.get("warnings", [])
        )

    quality_validation = {
        "valid": all_valid,
        "scoreable": all_valid and all_scoreable,
        "errors": combined_errors,
        "warnings": combined_warnings,
        "sections": {
            section: {
                "valid": result["valid"],
                "scoreable": result["scoreable"],
                "error_count": len(result.get("errors", [])),
                "warning_count": len(result.get("warnings", [])),
            }
            for section, result in section_results.items()
        },
    }

    scoring = score_industry_profile(
        profile,
        metric_values,
        metric_specs,
        minimum_coverage_pct=minimum_coverage_pct,
        blocking_reasons=combined_errors,
        quality_validation=quality_validation,
    )
    decision = evaluate_analysis_readiness(
        scoring,
        primary_valuation=primary_valuation,
        market_price=market_price,
        quality_blockers=combined_errors,
    )
    report = render_analysis_report(decision, scoring)
    return {
        "status": decision["status"],
        "quality_validation": quality_validation,
        "section_quality": section_results,
        "scoring": scoring,
        "decision": decision,
        "report": report,
    }

PARITY_REPORT_VERSION = "1.0.0"


def _parity_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def compare_scoring_parity(legacy_result, candidate_result,
                           score_tolerance=None,
                           coverage_tolerance=None,
                           metric_tolerances=None):
    """Compare legacy and new fundamental scorecards without changing either.

    Inputs may be full analysis results containing a 'scoring' object or
    scoring objects directly. Tolerances are absolute and must be approved
    externally; this function never chooses them automatically.
    """
    legacy = (
        legacy_result.get("scoring")
        if isinstance(legacy_result, dict) and isinstance(legacy_result.get("scoring"), dict)
        else legacy_result
    )
    candidate = (
        candidate_result.get("scoring")
        if isinstance(candidate_result, dict) and isinstance(candidate_result.get("scoring"), dict)
        else candidate_result
    )
    errors = []
    score_tol = _parity_number(score_tolerance)
    coverage_tol = _parity_number(coverage_tolerance)
    if score_tol is None or score_tol < 0:
        errors.append("score_tolerance_required_or_invalid")
    if coverage_tol is None or coverage_tol < 0:
        errors.append("coverage_tolerance_required_or_invalid")
    if not isinstance(metric_tolerances, dict):
        errors.append("metric_tolerances_required")
    if not isinstance(legacy, dict) or not isinstance(candidate, dict):
        return {
            "valid": False, "passed": False, "errors": ["scorecard_missing"],
            "parity_report_version": PARITY_REPORT_VERSION,
        }

    legacy_score = _parity_number(legacy.get("score"))
    candidate_score = _parity_number(candidate.get("score"))
    legacy_coverage = _parity_number(legacy.get("coverage_pct"))
    candidate_coverage = _parity_number(candidate.get("coverage_pct"))
    if None in (legacy_score, candidate_score):
        errors.append("score_missing_or_invalid")
        score_delta = None
    else:
        score_delta = round(candidate_score - legacy_score, 6)
        if score_tol is not None and abs(score_delta) > score_tol:
            errors.append("score_delta_exceeds_tolerance")
    if None in (legacy_coverage, candidate_coverage):
        errors.append("coverage_missing_or_invalid")
        coverage_delta = None
    else:
        coverage_delta = round(candidate_coverage - legacy_coverage, 6)
        if coverage_tol is not None and abs(coverage_delta) > coverage_tol:
            errors.append("coverage_delta_exceeds_tolerance")

    if type(legacy.get("ready")) is not bool or type(candidate.get("ready")) is not bool:
        errors.append("scoring_readiness_missing_or_invalid")
    elif legacy.get("ready") != candidate.get("ready"):
        errors.append("scoring_readiness_mismatch")
    legacy_metrics = legacy.get("metric_results")
    candidate_metrics = candidate.get("metric_results")
    if not isinstance(legacy_metrics, dict) or not isinstance(candidate_metrics, dict):
        errors.append("metric_results_invalid")
        legacy_metrics, candidate_metrics = {}, {}
    elif not legacy_metrics or not candidate_metrics:
        errors.append("metric_results_missing_or_empty")
    metric_differences = []
    tolerances = metric_tolerances if isinstance(metric_tolerances, dict) else {}
    for metric_id in sorted(set(legacy_metrics) | set(candidate_metrics)):
        left = legacy_metrics.get(metric_id)
        right = candidate_metrics.get(metric_id)
        difference = {"metric_id": metric_id, "passed": True, "errors": []}
        if not isinstance(left, dict) or not isinstance(right, dict):
            difference["passed"] = False
            difference["errors"].append("metric_missing_on_one_side")
        elif type(left.get("available")) is not bool or type(right.get("available")) is not bool:
            difference["passed"] = False
            difference["errors"].append("metric_availability_missing_or_invalid")
        elif left.get("available") != right.get("available"):
            difference["passed"] = False
            difference["errors"].append("metric_availability_mismatch")
        elif left.get("available") is True:
            for field in ("unit", "period_basis", "period"):
                if not str(left.get(field) or "").strip() or not str(right.get(field) or "").strip():
                    difference["passed"] = False
                    difference["errors"].append(field + "_missing")
                elif left.get(field) != right.get(field):
                    difference["passed"] = False
                    difference["errors"].append(field + "_mismatch")
            left_sources = sorted(str(item) for item in (left.get("source_fields") or []))
            right_sources = sorted(str(item) for item in (right.get("source_fields") or []))
            if not left_sources or left_sources != right_sources:
                difference["passed"] = False
                difference["errors"].append("source_fields_mismatch_or_missing")
            left_metric_score = _parity_number(left.get("score"))
            right_metric_score = _parity_number(right.get("score"))
            left_weight = _parity_number(left.get("weight"))
            right_weight = _parity_number(right.get("weight"))
            if None in (left_metric_score, right_metric_score):
                difference["passed"] = False
                difference["errors"].append("metric_score_missing_or_invalid")
            elif score_tol is not None and abs(left_metric_score - right_metric_score) > score_tol:
                difference["passed"] = False
                difference["errors"].append("metric_score_delta_exceeds_tolerance")
            if None in (left_weight, right_weight) or left_weight <= 0 or right_weight <= 0:
                difference["passed"] = False
                difference["errors"].append("metric_weight_missing_or_invalid")
            elif abs(left_weight - right_weight) > 1e-9:
                difference["passed"] = False
                difference["errors"].append("metric_weight_mismatch")
            if (
                left_metric_score is not None and left_weight is not None
                and not 0 <= left_metric_score <= left_weight
            ) or (
                right_metric_score is not None and right_weight is not None
                and not 0 <= right_metric_score <= right_weight
            ):
                difference["passed"] = False
                difference["errors"].append("metric_score_outside_weight")
            left_value = _parity_number(left.get("value"))
            right_value = _parity_number(right.get("value"))
            tolerance = _parity_number(tolerances.get(metric_id))
            if left_value is None or right_value is None:
                difference["passed"] = False
                difference["errors"].append("metric_value_missing_or_invalid")
            elif metric_id not in tolerances:
                difference["passed"] = False
                difference["errors"].append("metric_tolerance_missing")
            elif tolerance is None or tolerance < 0:
                difference["passed"] = False
                difference["errors"].append("metric_tolerance_invalid")
            else:
                difference["absolute_delta"] = round(abs(right_value - left_value), 8)
                if abs(right_value - left_value) > tolerance:
                    difference["passed"] = False
                    difference["errors"].append("metric_delta_exceeds_tolerance")
        if not difference["passed"]:
            errors.extend(metric_id + ":" + item for item in difference["errors"])
        metric_differences.append(difference)

    return {
        "valid": not errors,
        "passed": not errors,
        "score_delta": score_delta,
        "coverage_delta": coverage_delta,
        "metric_differences": metric_differences,
        "errors": errors,
        "parity_report_version": PARITY_REPORT_VERSION,
    }


def validate_historical_parity(cases, minimum_cases=20,
                               minimum_pass_rate=0.95,
                               score_tolerance=None,
                               coverage_tolerance=None,
                               metric_tolerances=None):
    """Evaluate a batch of legacy-vs-new scorecards for approval evidence."""
    if not isinstance(minimum_cases, int) or minimum_cases < 1:
        raise ValueError("minimum_cases must be a positive integer")
    pass_rate_floor = _parity_number(minimum_pass_rate)
    if pass_rate_floor is None or not 0 <= pass_rate_floor <= 1:
        raise ValueError("minimum_pass_rate must be between 0 and 1")
    if not isinstance(cases, (list, tuple)):
        raise ValueError("cases must be a list or tuple")

    results = []
    failures = []
    seen_case_ids = set()
    duplicate_case_ids = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            result = {"valid": False, "passed": False, "errors": ["case_invalid"]}
            symbol = str(index)
            case_id = "invalid:" + str(index)
        else:
            symbol = str(case.get("symbol") or index)
            period = str(case.get("period") or "").strip().upper()
            case_id = str(case.get("case_id") or (
                symbol + ":" + period if period else symbol
            ))
            if case_id in seen_case_ids:
                duplicate_case_ids.append(case_id)
            seen_case_ids.add(case_id)
            result = compare_scoring_parity(
                case.get("legacy"), case.get("candidate"),
                score_tolerance, coverage_tolerance, metric_tolerances,
            )
        results.append(result)
        if not result["passed"]:
            failures.append({
                "symbol": symbol,
                "errors": list(result.get("errors") or []),
            })

    sample_count = len(results)
    passed_count = sum(1 for result in results if result["passed"])
    pass_rate = passed_count / sample_count if sample_count else 0.0
    enough_cases = sample_count >= minimum_cases
    if duplicate_case_ids:
        failures.extend({
            "symbol": case_id,
            "errors": ["duplicate_historical_case_id"],
        } for case_id in sorted(set(duplicate_case_ids)))
    ready = (
        enough_cases
        and pass_rate >= pass_rate_floor
        and not duplicate_case_ids
    )
    return {
        "status": "passed" if ready else "not_ready",
        "ready_for_approval": ready,
        "sample_count": sample_count,
        "minimum_cases": minimum_cases,
        "passed_count": passed_count,
        "failed_count": sample_count - passed_count,
        "pass_rate": round(pass_rate, 4),
        "minimum_pass_rate": pass_rate_floor,
        "score_tolerance": score_tolerance,
        "coverage_tolerance": coverage_tolerance,
        "metric_tolerances": dict(metric_tolerances) if isinstance(metric_tolerances, dict) else {},
        "case_ids": sorted(seen_case_ids)[:100],
        "case_ids_truncated": len(seen_case_ids) > 100,
        "duplicate_case_count": len(set(duplicate_case_ids)),
        "duplicate_case_ids": sorted(set(duplicate_case_ids))[:100],
        "failures": failures[:100],
        "failures_truncated": len(failures) > 100,
        "parity_report_version": PARITY_REPORT_VERSION,
    }



def build_profile_approval_record(threshold_set_version, approved_at,
                                  reviewer, evidence_ref,
                                  parity_report_ref, parity_report,
                                  source_mapping_review):
    """Create auditable approval metadata only from passed review evidence."""
    required_text = {
        "threshold_set_version": threshold_set_version,
        "approved_at": approved_at,
        "reviewer": reviewer,
        "evidence_ref": evidence_ref,
        "parity_report_ref": parity_report_ref,
    }
    errors = [
        field + "_missing"
        for field, value in required_text.items()
        if not str(value or "").strip()
    ]
    if not isinstance(source_mapping_review, dict):
        errors.append("source_mapping_review_missing")
        source_review_ref = ""
        source_review_passed = False
    else:
        source_review_ref = str(source_mapping_review.get("evidence_ref") or "").strip()
        source_review_passed = source_mapping_review.get("status") == "passed"
        if not source_review_passed:
            errors.append("source_mapping_review_not_passed")
        if not source_review_ref:
            errors.append("source_mapping_review_evidence_missing")

    if not isinstance(parity_report, dict):
        errors.append("parity_report_missing")
        parity_count = 0
        parity_rate = None
        parity_passed = False
    else:
        parity_count = parity_report.get("sample_count")
        parity_rate = _parity_number(parity_report.get("pass_rate"))
        parity_passed = (
            parity_report.get("status") == "passed"
            and parity_report.get("ready_for_approval") is True
            and isinstance(parity_count, int)
            and not isinstance(parity_count, bool)
            and parity_count >= 20
            and parity_rate is not None
            and 0.95 <= parity_rate <= 1.0
        )
        if not parity_passed:
            errors.append("historical_parity_not_approved")

    if errors:
        return {
            "status": "pending",
            "ready_for_approval": False,
            "errors": errors,
        }
    return {
        "status": "approved",
        "threshold_set_version": str(threshold_set_version).strip(),
        "approved_at": str(approved_at).strip(),
        "reviewer": str(reviewer).strip(),
        "evidence_ref": str(evidence_ref).strip(),
        "parity_report_ref": str(parity_report_ref).strip(),
        "parity_sample_count": parity_count,
        "parity_pass_rate": parity_rate,
        "source_mapping_review": "passed",
        "source_mapping_review_ref": source_review_ref,
        "historical_parity": "passed",
        "ready_for_approval": True,
        "parity_report_version": parity_report.get("parity_report_version"),
    }
