# -*- coding: utf-8 -*-
"""Deterministic, proposal-only peer threshold calibration.

This module never approves thresholds or enables production. It accepts only
issuer-deduplicated observations whose formula and source lineage are already
verified, and emits a reviewable quantile proposal.
"""
from math import isfinite

CALIBRATION_VERSION = "1.0.0"
DEFAULT_MINIMUM_SAMPLE = 20
QUANTILES = (0.20, 0.40, 0.60, 0.80)
SCORE_POINTS = (0.0, 100.0 / 3.0, 200.0 / 3.0, 100.0)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def _quantile(sorted_values, probability):
    """Linear-interpolated quantile, deterministic for sorted finite inputs."""
    position = (len(sorted_values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def calibrate_metric_thresholds(observations, minimum_sample=DEFAULT_MINIMUM_SAMPLE):
    """Return a proposed curve or explicit blockers for one sector/metric.

    Required row fields: symbol, value, formula_status, source_verified,
    direction, unit, period_basis, as_of. Duplicate issuers are rejected rather
    than silently overweighted. All rows must share the same contract.
    """
    if isinstance(minimum_sample, bool) or not isinstance(minimum_sample, int) or minimum_sample < 5:
        raise ValueError("minimum_sample must be an integer >= 5")
    rows = observations if isinstance(observations, list) else []
    blockers = []
    if not rows:
        return {"ready": False, "status": "blocked", "blockers": ["no_observations"],
                "sample_count": 0, "thresholds": [], "approval_required": True}

    symbols = []
    values = []
    contracts = set()
    for row in rows:
        if not isinstance(row, dict):
            blockers.append("observation_not_object")
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        value = _number(row.get("value"))
        if not symbol:
            blockers.append("symbol_missing")
            continue
        if value is None:
            blockers.append("value_missing_or_non_finite")
            continue
        if not str(row.get("formula_status") or "").lower().startswith("verified"):
            blockers.append("formula_not_verified")
        if row.get("source_verified") is not True:
            blockers.append("source_lineage_not_verified")
        direction = str(row.get("direction") or "").strip().lower()
        unit = str(row.get("unit") or "").strip().lower()
        basis = str(row.get("period_basis") or "").strip().upper()
        as_of = str(row.get("as_of") or "").strip()
        if direction not in ("higher", "lower"):
            blockers.append("direction_invalid")
        if not unit or not basis or not as_of:
            blockers.append("metric_contract_incomplete")
        contracts.add((direction, unit, basis, as_of))
        symbols.append(symbol)
        values.append(value)

    if len(symbols) != len(set(symbols)):
        blockers.append("duplicate_symbol_observations")
    if len(contracts) != 1:
        blockers.append("mixed_metric_contract_or_data_vintage")
    unique_values = set(values)
    if len(symbols) < minimum_sample:
        blockers.append("insufficient_independent_issuer_sample")
    if len(unique_values) < 5:
        blockers.append("insufficient_value_dispersion")
    if blockers:
        return {
            "ready": False, "status": "blocked",
            "blockers": sorted(set(blockers)),
            "sample_count": len(set(symbols)),
            "thresholds": [], "approval_required": True,
        }

    direction, unit, basis, as_of = next(iter(contracts))
    ordered = sorted(values)
    boundaries = [_quantile(ordered, q) for q in QUANTILES]
    if len(set(boundaries)) != len(boundaries):
        return {
            "ready": False, "status": "blocked",
            "blockers": ["quantile_boundaries_not_distinct"],
            "sample_count": len(symbols), "thresholds": [],
            "approval_required": True,
        }

    points = SCORE_POINTS if direction == "higher" else tuple(reversed(SCORE_POINTS))
    thresholds = [[round(boundary, 8), points[index]]
                  for index, boundary in enumerate(boundaries)]
    return {
        "ready": True,
        "status": "proposed_not_approved",
        "calibration_version": CALIBRATION_VERSION,
        "sample_count": len(symbols),
        "unique_issuer_count": len(set(symbols)),
        "minimum_sample": minimum_sample,
        "quantiles": list(QUANTILES),
        "thresholds": thresholds,
        "direction": direction,
        "unit": unit,
        "period_basis": basis,
        "data_vintage": as_of,
        "approval_required": True,
        "production_enabled": False,
        "method": "sector_peer_empirical_quantiles_linear_interpolation",
    }


def calibrate_sector_metrics(payload, minimum_sample=DEFAULT_MINIMUM_SAMPLE):
    """Group rows by (sector, metric_id), preserving deterministic output order."""
    rows = payload.get("observations", []) if isinstance(payload, dict) else []
    grouped = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        key = (
            str(row.get("sector") or "").strip().upper(),
            str(row.get("metric_id") or "").strip().upper(),
        )
        if not all(key):
            continue
        grouped.setdefault(key, []).append(row)
    result = {}
    for sector, metric_id in sorted(grouped):
        result.setdefault(sector, {})[metric_id] = calibrate_metric_thresholds(
            grouped[(sector, metric_id)], minimum_sample=minimum_sample
        )
    return {
        "calibration_version": CALIBRATION_VERSION,
        "mode": "proposal_only",
        "minimum_sample": minimum_sample,
        "sector_count": len(result),
        "metric_count": sum(len(metrics) for metrics in result.values()),
        "production_enabled": False,
        "approval_required": True,
        "by_sector": result,
    }
