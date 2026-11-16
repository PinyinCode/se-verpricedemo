# -*- coding: utf-8 -*-
import unittest

from analysis_pipeline import (
    build_profile_approval_record,
    build_profile_approval_record,
    compare_scoring_parity,
    run_analysis_pipeline,
    validate_historical_parity,
)
from metric_contract import build_metric_record



class TestHistoricalParityValidation(unittest.TestCase):
    def setUp(self):
        self.legacy = {
            "ready": True,
            "score": 36.5,
            "coverage_pct": 90.0,
            "metric_results": {
                "ROE": {
                    "available": True, "value": 12.0,
                    "unit": "percent", "period_basis": "TTM",
                    "period": "2026-Q3_TTM", "source_fields": ["isa20", "bsa78"],
                    "score": 8, "weight": 10,
                },
                "NPL_RATIO": {
                    "available": False, "value": None,
                    "unit": "percent", "period_basis": "POINT_IN_TIME",
                },
            },
        }
        self.candidate = {
            "ready": True,
            "score": 36.7,
            "coverage_pct": 90.0,
            "metric_results": {
                "ROE": {
                    "available": True, "value": 12.0001,
                    "unit": "percent", "period_basis": "TTM",
                    "period": "2026-Q3_TTM", "source_fields": ["isa20", "bsa78"],
                    "score": 8, "weight": 10,
                },
                "NPL_RATIO": {
                    "available": False, "value": None,
                    "unit": "percent", "period_basis": "POINT_IN_TIME",
                },
            },
        }

    def test_matching_scorecards_pass_explicit_tolerances(self):
        result = compare_scoring_parity(
            self.legacy, self.candidate,
            score_tolerance=0.5,
            coverage_tolerance=0.1,
            metric_tolerances={"ROE": 0.001},
        )
        self.assertTrue(result["passed"], result["errors"])
        self.assertAlmostEqual(result["score_delta"], 0.2)



    def test_source_lineage_mismatch_fails_parity(self):
        candidate = {
            **self.candidate,
            "metric_results": {
                "ROE": {
                    **self.candidate["metric_results"]["ROE"],
                    "source_fields": ["isa22", "bsa78"],
                },
                "NPL_RATIO": self.candidate["metric_results"]["NPL_RATIO"],
            },
        }
        result = compare_scoring_parity(
            self.legacy, candidate, score_tolerance=0.5,
            coverage_tolerance=0.1, metric_tolerances={"ROE": 0.001},
        )
        self.assertFalse(result["passed"])
        self.assertIn("ROE:source_fields_mismatch_or_missing", result["errors"])

    def test_metric_level_score_and_weight_mismatches_fail_parity(self):
        legacy = {
            "score": 10, "coverage_pct": 100,
            "metric_results": {
                "ROE": {
                    "available": True, "value": 12,
                    "unit": "percent", "period_basis": "TTM",
                    "period": "2026-Q3_TTM", "source_fields": ["isa20", "bsa78"],
                    "score": 8, "weight": 10,
                },
            },
        }
        candidate = {
            "score": 10, "coverage_pct": 100,
            "metric_results": {
                "ROE": {
                    "available": True, "value": 12,
                    "unit": "percent", "period_basis": "TTM",
                    "period": "2026-Q3_TTM", "source_fields": ["isa20", "bsa78"],
                    "score": 7, "weight": 9,
                },
            },
        }
        result = compare_scoring_parity(
            legacy, candidate, score_tolerance=0.5
        )
        self.assertFalse(result["passed"])
        self.assertIn(
            "ROE:metric_score_delta_exceeds_tolerance", result["errors"]
        )
        self.assertIn("ROE:metric_weight_mismatch", result["errors"])


    def test_metric_source_lineage_mismatch_fails_parity(self):
        candidate = {
            **self.candidate,
            "metric_results": {
                **self.candidate["metric_results"],
                "ROE": {
                    **self.candidate["metric_results"]["ROE"],
                    "source_fields": ["isa22", "bsa78"],
                },
            },
        }
        result = compare_scoring_parity(
            self.legacy, candidate,
            score_tolerance=0.5, coverage_tolerance=0.1,
            metric_tolerances={"ROE": 0.001},
        )
        self.assertFalse(result["passed"])
        self.assertIn(
            "ROE:source_fields_mismatch_or_missing", result["errors"]
        )

    def test_metric_availability_mismatch_fails_parity(self):
        candidate = {
            **self.candidate,
            "metric_results": {
                **self.candidate["metric_results"],
                "NPL_RATIO": {
                    "available": True, "value": 2.0,
                    "unit": "percent", "period_basis": "POINT_IN_TIME",
                },
            },
        }
        result = compare_scoring_parity(self.legacy, candidate)
        self.assertFalse(result["passed"])
        self.assertIn(
            "NPL_RATIO:metric_availability_mismatch", result["errors"]
        )

    def test_historical_batch_requires_minimum_samples_and_pass_rate(self):
        cases = [
            {"symbol": "AAA", "legacy": self.legacy, "candidate": self.candidate},
            {"symbol": "BBB", "legacy": self.legacy, "candidate": self.candidate},
            {"symbol": "CCC", "legacy": self.legacy, "candidate": self.candidate},
        ]
        too_few = validate_historical_parity(cases, minimum_cases=20)
        self.assertFalse(too_few["ready_for_approval"])
        self.assertEqual(too_few["status"], "not_ready")
        enough = validate_historical_parity(
            cases, minimum_cases=3, minimum_pass_rate=1.0,
            score_tolerance=0.5, coverage_tolerance=0.1,
            metric_tolerances={"ROE": 0.001},
        )
        self.assertTrue(enough["ready_for_approval"])
        self.assertEqual(enough["passed_count"], 3)
        self.assertEqual(enough["score_tolerance"], 0.5)
        self.assertEqual(enough["coverage_tolerance"], 0.1)
        self.assertEqual(enough["metric_tolerances"], {"ROE": 0.001})
        self.assertEqual(enough["case_ids"], ["AAA", "BBB", "CCC"])


    def test_parity_cannot_pass_without_explicit_metric_and_coverage_tolerances(self):
        result = compare_scoring_parity(
            self.legacy, self.candidate, score_tolerance=0.5
        )
        self.assertFalse(result["passed"])
        self.assertIn("coverage_tolerance_required_or_invalid", result["errors"])
        self.assertIn("metric_tolerances_required", result["errors"])

    def test_period_mismatch_fails_metric_parity(self):
        legacy = {
            **self.legacy,
            "metric_results": {
                **self.legacy["metric_results"],
                "ROE": {**self.legacy["metric_results"]["ROE"], "period": "2025-Q3_TTM"},
            },
        }
        candidate = {
            **self.candidate,
            "metric_results": {
                **self.candidate["metric_results"],
                "ROE": {**self.candidate["metric_results"]["ROE"], "period": "2026-Q3_TTM"},
            },
        }
        result = compare_scoring_parity(
            legacy, candidate, score_tolerance=0.5,
            coverage_tolerance=0.1, metric_tolerances={"ROE": 0.001},
        )
        self.assertFalse(result["passed"])
        self.assertIn("ROE:period_mismatch", result["errors"])


    def test_duplicate_historical_case_ids_block_approval(self):
        case = {
            "symbol": "AAA", "period": "2026-Q3",
            "legacy": self.legacy, "candidate": self.candidate,
        }
        result = validate_historical_parity(
            [case, case], minimum_cases=2, minimum_pass_rate=1.0,
            score_tolerance=0.5, coverage_tolerance=0.1,
            metric_tolerances={"ROE": 0.001},
        )
        self.assertFalse(result["ready_for_approval"])
        self.assertEqual(result["duplicate_case_count"], 1)
        self.assertIn(
            "duplicate_historical_case_id",
            [error for item in result["failures"] for error in item["errors"]],
        )


    def test_empty_metric_details_cannot_qualify_as_historical_parity(self):
        legacy = {"score": 20, "coverage_pct": 80, "metric_results": {}}
        candidate = {"score": 20, "coverage_pct": 80, "metric_results": {}}
        result = compare_scoring_parity(
            legacy, candidate, score_tolerance=0.5,
            coverage_tolerance=0.1, metric_tolerances={},
        )
        self.assertFalse(result["passed"])
        self.assertIn("metric_results_missing_or_empty", result["errors"])

    def test_scoring_readiness_mismatch_fails_parity(self):
        legacy = dict(self.legacy, ready=True)
        candidate = dict(self.candidate, ready=False)
        result = compare_scoring_parity(
            legacy, candidate, score_tolerance=0.5,
            coverage_tolerance=0.1, metric_tolerances={"ROE": 0.001},
        )
        self.assertFalse(result["passed"])
        self.assertIn("scoring_readiness_mismatch", result["errors"])


    def test_profile_approval_record_requires_passed_mapping_and_parity_evidence(self):
        parity = {
            "status": "passed", "ready_for_approval": True,
            "sample_count": 20, "pass_rate": 0.95,
            "parity_report_version": "1.0.0",
        }
        mapping_review = {
            "status": "passed",
            "evidence_ref": "mapping-review-2026-01",
        }
        approval = build_profile_approval_record(
            "thresholds-v1", "2026-11-16", "reviewer-id",
            "approval-evidence-1", "parity-report-1",
            parity, mapping_review,
        )
        self.assertEqual(approval["status"], "approved")
        self.assertEqual(approval["parity_sample_count"], 20)
        self.assertEqual(approval["parity_pass_rate"], 0.95)
        self.assertEqual(approval["source_mapping_review"], "passed")

        parity["sample_count"] = 19
        blocked = build_profile_approval_record(
            "thresholds-v1", "2026-11-16", "reviewer-id",
            "approval-evidence-1", "parity-report-1",
            parity, mapping_review,
        )
        self.assertEqual(blocked["status"], "pending")
        self.assertFalse(blocked["ready_for_approval"])
        self.assertIn("historical_parity_not_approved", blocked["errors"])


    def test_approval_record_is_built_only_from_passed_mapping_and_parity_evidence(self):
        parity = {
            "status": "passed",
            "ready_for_approval": True,
            "sample_count": 20,
            "pass_rate": 0.95,
            "parity_report_version": "1.0.0",
        }
        mapping_review = {
            "status": "passed",
            "evidence_ref": "mapping-review-fixture",
        }
        approval = build_profile_approval_record(
            "thresholds-v1", "2026-11-16", "reviewer",
            "approval-evidence", "parity-report", parity, mapping_review,
        )
        self.assertEqual(approval["status"], "approved")
        self.assertEqual(approval["parity_sample_count"], 20)
        self.assertEqual(approval["parity_pass_rate"], 0.95)
        self.assertEqual(approval["source_mapping_review"], "passed")

        insufficient = build_profile_approval_record(
            "thresholds-v1", "2026-11-16", "reviewer",
            "approval-evidence", "parity-report",
            dict(parity, sample_count=19), mapping_review,
        )
        self.assertEqual(insufficient["status"], "pending")
        self.assertFalse(insufficient["ready_for_approval"])
        self.assertIn(
            "historical_parity_not_approved", insufficient["errors"]
        )

    def test_invalid_or_missing_scorecard_cannot_pass(self):
        result = compare_scoring_parity({}, self.candidate)
        self.assertFalse(result["passed"])
        self.assertIn("score_missing_or_invalid", result["errors"])


class TestDeterministicAnalysisPipeline(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "profile_id": "STEEL",
            "profile_version": "1.0.0",
            "status": "production",
            "scoring_enabled": True,
            "score_weight": 10,
            "weights": {"profitability": 10},
            "metric_groups": {"profitability": ["ROE"]},
            "approval": {
                "status": "approved",
                "threshold_set_version": "test-thresholds-1",
                "approved_at": "2026-01-01",
                "reviewer": "test-reviewer",
                "evidence_ref": "test-fixture",
                "parity_report_ref": "test-parity-report",
                "parity_sample_count": 20,
                "parity_pass_rate": 1.0,
                "source_mapping_review": "passed",
                "historical_parity": "passed",
            },
        }
        self.specs = {
            "ROE": {
                "group": "profitability",
                "weight": 10,
                "threshold_id": "roe_ttm_v1",
                "thresholds": [(0, 0), (10, 10)],
                "direction": "higher",
                "period_basis": "TTM",
                "source_fields": ["isa20", "bsa78"],
                "unit": "percent",
            }
        }
        self.metric_values = {
            "ROE": build_metric_record(
                "ROE", {"available": True, "value": 10},
                "percent", "2026-Q3_TTM", "TTM", ["isa20", "bsa78"],
            )
        }
        self.rows = {
            "balance_sheet": [
                {"source_field": "bsa53", "period": "2026-Q3", "value": 100, "unit": "VND"},
                {"source_field": "bsa54", "period": "2026-Q3", "value": 60, "unit": "VND"},
                {"source_field": "bsa78", "period": "2026-Q3", "value": 40, "unit": "VND"},
                {"source_field": "bsa96", "period": "2026-Q3", "value": 100, "unit": "VND"},
            ],
            "income_statement": [
                {"source_field": "isa20", "period": "2026-Q3", "value": 10, "unit": "VND"},
            ],
            "cash_flow": [
                {"source_field": "cfa36", "period": "2026-Q3", "value": 20, "unit": "VND"},
                {"source_field": "cfa34", "period": "2026-Q3", "value": 100, "unit": "VND"},
                {"source_field": "cfa37", "period": "2026-Q3", "value": -5, "unit": "VND"},
                {"source_field": "cfa38", "period": "2026-Q3", "value": 115, "unit": "VND"},
            ],
        }
        self.bases = {
            "balance_sheet": "POINT_IN_TIME",
            "income_statement": "QUARTERLY",
            "cash_flow": "YTD",
        }

    def _run(self, rows=None, bases=None):
        return run_analysis_pipeline(
            self.profile,
            rows or self.rows,
            bases or self.bases,
            self.metric_values,
            self.specs,
            ["balance_sheet", "income_statement", "cash_flow"],
        )

    def test_valid_statements_flow_through_scoring_decision_and_report(self):
        result = self._run()
        self.assertTrue(result["quality_validation"]["valid"])
        self.assertTrue(result["quality_validation"]["scoreable"])
        self.assertEqual(result["scoring"]["score"], 10, result["scoring"])
        self.assertEqual(result["scoring"]["coverage_pct"], 100)
        self.assertEqual(
            result["status"], "score_available_valuation_unavailable"
        )
        self.assertIn("BÁO CÁO PHÂN TÍCH ĐỊNH LƯỢNG", result["report"]["text"])

    def test_missing_required_statement_blocks_scoring(self):
        rows = dict(self.rows)
        rows.pop("cash_flow")
        result = self._run(rows=rows)
        self.assertFalse(result["quality_validation"]["valid"])
        self.assertFalse(result["scoring"]["ready"])
        self.assertEqual(result["status"], "insufficient_data")

    def test_cashflow_reconciliation_mismatch_blocks_pipeline(self):
        rows = dict(self.rows)
        rows["cash_flow"] = list(self.rows["cash_flow"])
        rows["cash_flow"][-1] = dict(rows["cash_flow"][-1], value=120)
        result = self._run(rows=rows)
        self.assertFalse(result["quality_validation"]["valid"])
        self.assertFalse(result["scoring"]["ready"])
        self.assertTrue(any(
            "subtotal_reconciliation_failed" in reason
            for reason in result["quality_validation"]["errors"]
        ))

    def test_unknown_required_section_is_rejected(self):
        with self.assertRaises(ValueError):
            run_analysis_pipeline(
                self.profile, self.rows, self.bases, self.metric_values,
                self.specs, ["unknown_section"],
            )


if __name__ == "__main__":
    unittest.main()
