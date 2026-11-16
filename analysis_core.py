# -*- coding: utf-8 -*-
"""Pure, deterministic primitives for the financial-analysis engine.

This module deliberately has no database, web, AI, pandas, or numpy dependency.
It is safe to import in tests and in memory-constrained API workers.
"""
from math import isfinite


ENGINE_VERSION = "1.0.0"


def _finite_number(value):
    """Return a finite float, or None for missing/invalid values."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def score_threshold_metric(value, weight, thresholds, direction="higher"):
    """Score one metric without awarding points when its value is unavailable.

    thresholds is an ascending sequence of (boundary, points).
    For direction='higher', points must be non-decreasing.
    For direction='lower', points must be non-increasing.
    """
    metric_value = _finite_number(value)
    metric_weight = _finite_number(weight)
    if metric_weight is None or metric_weight <= 0:
        raise ValueError("weight must be a positive finite number")
    if direction not in ("higher", "lower"):
        raise ValueError("direction must be 'higher' or 'lower'")

    try:
        rows = [(_finite_number(boundary), _finite_number(points))
                for boundary, points in thresholds]
    except (TypeError, ValueError):
        raise ValueError("thresholds must contain (boundary, points) pairs")
    if not rows or any(boundary is None or points is None for boundary, points in rows):
        raise ValueError("thresholds must be non-empty and finite")
    boundaries = [row[0] for row in rows]
    points = [row[1] for row in rows]
    if boundaries != sorted(boundaries) or len(set(boundaries)) != len(boundaries):
        raise ValueError("threshold boundaries must be strictly ascending")
    if any(point < 0 or point > metric_weight for point in points):
        raise ValueError("threshold points must be between 0 and weight")
    if direction == "higher" and points != sorted(points):
        raise ValueError("higher-is-better points must be non-decreasing")
    if direction == "lower" and points != sorted(points, reverse=True):
        raise ValueError("lower-is-better points must be non-increasing")

    if metric_value is None:
        return {
            "available": False,
            "value": None,
            "score": 0.0,
            "weight": metric_weight,
            "reason": "missing_or_invalid_value",
        }

    awarded = 0.0
    if direction == "higher":
        for boundary, points_at_boundary in rows:
            if metric_value >= boundary:
                awarded = points_at_boundary
            else:
                break
    else:
        awarded = rows[-1][1]
        for boundary, points_at_boundary in rows:
            if metric_value <= boundary:
                awarded = points_at_boundary
                break

    return {
        "available": True,
        "value": metric_value,
        "score": awarded,
        "weight": metric_weight,
        "reason": "",
    }


def aggregate_metric_scores(metric_results, expected_weight):
    """Aggregate fixed-weight metric results; never redistribute missing weight."""
    total_weight = _finite_number(expected_weight)
    if total_weight is None or total_weight <= 0:
        raise ValueError("expected_weight must be a positive finite number")

    score = 0.0
    available_weight = 0.0
    seen_weight = 0.0
    count_available = 0
    count_missing = 0

    for result in metric_results:
        if not isinstance(result, dict):
            raise ValueError("each metric result must be a dictionary")
        weight = _finite_number(result.get("weight"))
        points = _finite_number(result.get("score"))
        if weight is None or weight <= 0 or points is None:
            raise ValueError("each metric requires positive weight and finite score")
        if points < 0 or points > weight:
            raise ValueError("metric score must be between zero and its weight")
        seen_weight += weight
        if seen_weight > total_weight + 1e-9:
            raise ValueError("sum of metric weights exceeds expected_weight")
        if result.get("available") is True:
            score += points
            available_weight += weight
            count_available += 1
        else:
            count_missing += 1

    coverage = min(100.0, available_weight / total_weight * 100.0)
    return {
        "score": round(score, 4),
        "expected_weight": total_weight,
        "available_weight": round(available_weight, 4),
        "coverage_pct": round(coverage, 2),
        "missing_weight": round(max(0.0, total_weight - available_weight), 4),
        "available_metric_count": count_available,
        "missing_metric_count": count_missing,
        "score_pct_of_fixed_total": round(score / total_weight * 100.0, 2),
    }


def evaluate_data_gate(coverage_pct, minimum_coverage_pct=65.0, blocking_reasons=None):
    """Return a deterministic data-readiness gate, not an investment instruction."""
    coverage = _finite_number(coverage_pct)
    minimum = _finite_number(minimum_coverage_pct)
    if coverage is None or minimum is None or minimum < 0 or minimum > 100:
        raise ValueError("coverage values must be finite and minimum must be 0..100")
    reasons = [str(reason).strip() for reason in (blocking_reasons or []) if str(reason).strip()]
    if coverage < minimum:
        reasons.insert(0, "fundamental_coverage_below_minimum")
    return {
        "ready": not reasons,
        "coverage_pct": round(coverage, 2),
        "minimum_coverage_pct": round(minimum, 2),
        "blocking_reasons": reasons,
    }
