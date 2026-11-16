# -*- coding: utf-8 -*-
import unittest

from decision_engine import evaluate_analysis_readiness
from valuation_engine import calculate_pe_value


class TestAnalysisDecisionGate(unittest.TestCase):
    def setUp(self):
        self.scoring = {
            "ready": True, "score": 37, "coverage_pct": 80,
        }
        self.valuation = calculate_pe_value({
            "value": 10, "available": True, "quality_status": "verified",
            "period_basis": "TTM", "source_fields": ["eps_ttm"],
            "period": "2026-Q3_TTM", "unit": "VND/share",
        }, 10)

    def test_ready_analysis_returns_neutral_valuation_gap(self):
        result = evaluate_analysis_readiness(
            self.scoring, self.valuation, market_price=80
        )
        self.assertEqual(result["status"], "analysis_ready")
        self.assertTrue(result["analysis_ready"])
        self.assertEqual(
            result["valuation_comparison"]["discount_pct"], 20
        )
        self.assertIn("không phải chỉ dẫn mua", result["interpretation"])

    def test_unready_score_blocks_overall_analysis(self):
        result = evaluate_analysis_readiness(
            dict(self.scoring, ready=False), self.valuation, 80
        )
        self.assertEqual(result["status"], "insufficient_data")
        self.assertFalse(result["analysis_ready"])
        self.assertIn("fundamental_scoring_not_ready",
                      result["blocking_reasons"])

    def test_quality_blocker_overrides_valid_valuation(self):
        result = evaluate_analysis_readiness(
            self.scoring, self.valuation, 80,
            quality_blockers=["bctc_reconciliation_failed"],
        )
        self.assertEqual(result["status"], "insufficient_data")

    def test_valuation_unavailable_does_not_block_score_status(self):
        result = evaluate_analysis_readiness(self.scoring, None, 80)
        self.assertEqual(
            result["status"], "score_available_valuation_unavailable"
        )
        self.assertFalse(result["analysis_ready"])


if __name__ == "__main__":
    unittest.main()
