# -*- coding: utf-8 -*-
"""Unit tests for the pure deterministic analysis primitives."""
import unittest

from analysis_core import (
    ENGINE_VERSION,
    aggregate_metric_scores,
    evaluate_data_gate,
    score_threshold_metric,
)


class TestThresholdMetricScoring(unittest.TestCase):
    def test_higher_is_better_uses_fixed_threshold_points(self):
        result = score_threshold_metric(
            15, 10, [(0, 0), (5, 3), (10, 7), (15, 10)], "higher"
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["score"], 10)

    def test_lower_is_better_uses_fixed_threshold_points(self):
        result = score_threshold_metric(
            1.2, 10, [(0.5, 10), (1.0, 7), (1.5, 3), (2.0, 0)], "lower"
        )
        self.assertEqual(result["score"], 3)

    def test_missing_metric_gets_zero_and_is_unavailable(self):
        result = score_threshold_metric(
            None, 8, [(0, 0), (10, 4), (20, 8)], "higher"
        )
        self.assertFalse(result["available"])
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["weight"], 8)

    def test_non_finite_metric_is_unavailable(self):
        result = score_threshold_metric(
            float("nan"), 5, [(0, 0), (1, 5)], "higher"
        )
        self.assertFalse(result["available"])

    def test_invalid_thresholds_are_rejected(self):
        with self.assertRaises(ValueError):
            score_threshold_metric(10, 5, [(1, 4), (0, 5)], "higher")


class TestFixedWeightAggregation(unittest.TestCase):
    def test_missing_weight_is_not_redistributed(self):
        metrics = [
            {"available": True, "score": 6, "weight": 10},
            {"available": False, "score": 0, "weight": 15},
        ]
        result = aggregate_metric_scores(metrics, expected_weight=25)
        self.assertEqual(result["score"], 6)
        self.assertEqual(result["available_weight"], 10)
        self.assertEqual(result["missing_weight"], 15)
        self.assertEqual(result["coverage_pct"], 40)
        self.assertEqual(result["score_pct_of_fixed_total"], 24)

    def test_metric_weights_cannot_exceed_group_weight(self):
        with self.assertRaises(ValueError):
            aggregate_metric_scores(
                [{"available": True, "score": 5, "weight": 5}],
                expected_weight=4,
            )

    def test_unavailable_metric_does_not_add_score_or_coverage(self):
        result = aggregate_metric_scores(
            [{"available": False, "score": 0, "weight": 10}],
            expected_weight=10,
        )
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["coverage_pct"], 0)


class TestDataReadinessGate(unittest.TestCase):
    def test_coverage_below_threshold_blocks_readiness(self):
        result = evaluate_data_gate(64.9, 65)
        self.assertFalse(result["ready"])
        self.assertIn("fundamental_coverage_below_minimum", result["blocking_reasons"])

    def test_coverage_at_threshold_can_be_ready(self):
        self.assertTrue(evaluate_data_gate(65, 65)["ready"])

    def test_explicit_quality_issue_blocks_even_with_high_coverage(self):
        result = evaluate_data_gate(95, 65, ["invalid_reporting_period"])
        self.assertFalse(result["ready"])
        self.assertIn("invalid_reporting_period", result["blocking_reasons"])


class TestEngineMetadata(unittest.TestCase):
    def test_engine_version_is_present(self):
        self.assertTrue(ENGINE_VERSION)


if __name__ == "__main__":
    unittest.main()
