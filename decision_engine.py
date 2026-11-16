# -*- coding: utf-8 -*-
"""Deterministic analysis readiness and neutral valuation comparison."""
from math import isfinite

from valuation_engine import compare_to_market_price


DECISION_ENGINE_VERSION = "1.0.0"


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def evaluate_analysis_readiness(scoring_result, primary_valuation=None,
                                market_price=None, quality_blockers=None):
    """Combine data gates and one explicitly selected valuation method.

    This is an analytical status, not a buy/sell/hold instruction. The caller
    selects the valuation method; this function never averages methods.
    """
    blockers = list(quality_blockers or [])
    if not isinstance(scoring_result, dict) or scoring_result.get("ready") is not True:
        blockers.append("fundamental_scoring_not_ready")
    score = _number(scoring_result.get("score")) if isinstance(scoring_result, dict) else None
    coverage = _number(scoring_result.get("coverage_pct")) if isinstance(scoring_result, dict) else None

    valuation_comparison = compare_to_market_price(primary_valuation, market_price)
    if blockers:
        status = "insufficient_data"
    elif not valuation_comparison.get("available"):
        status = "score_available_valuation_unavailable"
    else:
        status = "analysis_ready"

    return {
        "status": status,
        "analysis_ready": status == "analysis_ready",
        "score": score,
        "coverage_pct": coverage,
        "valuation_method": (
            primary_valuation.get("method")
            if isinstance(primary_valuation, dict) else None
        ),
        "valuation_comparison": valuation_comparison,
        "blocking_reasons": sorted(set(str(item) for item in blockers if str(item))),
        "engine_version": DECISION_ENGINE_VERSION,
        "interpretation": (
            "Kết quả phân tích định lượng; không phải chỉ dẫn mua, bán hoặc nắm giữ."
        ),
    }
