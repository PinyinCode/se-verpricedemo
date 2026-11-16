# -*- coding: utf-8 -*-
import unittest

from scoring_engine import score_industry_profile
from metric_catalog import (
    summarize_sector_metric_readiness,
    summarize_all_sector_metric_readiness,
)
from industry_profiles import PROFILE_TO_RULE


class TestDeterministicScoringEngine(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "profile_id": "STEEL",
            "profile_version": "test-1",
            "status": "production",
            "scoring_enabled": True,
            "score_weight": 10,
            "weights": {"quality": 10},
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
        self.quality = {"valid": True, "scoreable": True}
        self.specs = {
            "ROE": {
                "group": "quality", "weight": 5,
                "threshold_id": "ROE_v1",
                "thresholds": [(0, 0), (10, 5)],
                "direction": "higher",
                "period_basis": "TTM",
                "source_fields": ["isa20", "bsa78"],
            },
            "ROA": {
                "group": "quality", "weight": 5,
                "threshold_id": "ROA_v1",
                "thresholds": [(0, 0), (10, 5)],
                "direction": "higher",
                "period_basis": "TTM",
                "source_fields": ["isa20", "bsa53"],
            },
        }

    def _verified(self, value, source, metric_id):
        return {
            "metric_id": metric_id,
            "value": value, "available": True,
            "quality_status": "verified", "period_basis": "TTM",
            "period": "2026-Q3_TTM", "unit": "percent",
            "source_fields": (
                ["wrong_field"] if source == "wrong_field"
                else (["isa20", "bsa78"] if metric_id == "ROE" else ["isa20", "bsa53"])
            ),
        }


    def test_profile_metric_contract_rejects_missing_or_substituted_metric(self):
        profile = dict(
            self.profile,
            metric_groups={"quality": ["ROE", "ROA"]},
        )
        missing = score_industry_profile(
            profile,
            {"ROE": self._verified(10, "field_a", "ROE")},
            {"ROE": self.specs["ROE"]},
            quality_validation=self.quality,
        )
        self.assertEqual(missing["status"], "invalid_metric_configuration")
        self.assertEqual(
            missing["errors"][0]["code"],
            "metric_specs_do_not_match_profile_requirements",
        )

        substituted_specs = {
            "ROE": self.specs["ROE"],
            "proxy_metric": dict(self.specs["ROA"]),
        }
        substituted = score_industry_profile(
            profile, {}, substituted_specs,
            quality_validation=self.quality,
        )
        self.assertEqual(substituted["status"], "invalid_metric_configuration")
        self.assertEqual(
            substituted["errors"][0]["code"],
            "metric_specs_do_not_match_profile_requirements",
        )

    def test_profile_metric_contract_rejects_wrong_group_assignment(self):
        profile = dict(
            self.profile,
            weights={"quality": 5, "risk": 5},
            metric_groups={"quality": ["ROE"], "risk": ["ROA"]},
        )
        specs = {
            "ROE": dict(self.specs["ROE"], weight=5),
            "ROA": dict(self.specs["ROA"], group="quality", weight=5),
        }
        result = score_industry_profile(
            profile, {}, specs, quality_validation=self.quality
        )
        self.assertEqual(result["status"], "invalid_metric_configuration")
        self.assertEqual(
            result["errors"][0]["code"],
            "metric_group_differs_from_profile_requirement",
        )



    def test_production_approval_rejects_insufficient_parity_evidence(self):
        profile = dict(
            self.profile,
            approval=dict(
                self.profile["approval"],
                parity_sample_count=5,
                parity_pass_rate=1.0,
            ),
        )
        result = score_industry_profile(
            profile, {}, self.specs, quality_validation=self.quality
        )
        self.assertEqual(result["status"], "profile_not_approved")
        self.assertEqual(
            result["errors"][0]["code"],
            "production_approval_incomplete",
        )

    def test_production_profile_requires_explicit_approval_record(self):
        profile = dict(self.profile)
        profile.pop("approval")
        result = score_industry_profile(
            profile, {}, self.specs, quality_validation=self.quality
        )
        self.assertEqual(result["status"], "profile_not_approved")
        self.assertIn(
            "production_approval_record_missing", result["errors"]
        )

    def test_production_profile_requires_source_review_and_historical_parity(self):
        profile = dict(
            self.profile,
            approval=dict(self.profile["approval"], historical_parity="pending"),
        )
        result = score_industry_profile(
            profile, {}, self.specs, quality_validation=self.quality
        )
        self.assertEqual(result["status"], "profile_not_approved")
        self.assertEqual(
            result["errors"][0]["code"],
            "production_approval_incomplete",
        )


    def test_metric_catalog_rejects_cross_sector_metric_substitution(self):
        profile = dict(
            self.profile,
            metric_groups={"quality": ["BANK_PPOP_MARGIN"]},
        )
        unsupported_bank_metric_spec = {
            "group": "quality",
            "weight": 10,
            "threshold_id": "bank_ppop_margin_v1",
            "thresholds": [(0, 0), (10, 10)],
            "direction": "higher",
            "period_basis": "TTM",
            "source_fields": ["isb40", "isb38"],
            "unit": "percent",
        }
        result = score_industry_profile(
            profile, {}, {"BANK_PPOP_MARGIN": unsupported_bank_metric_spec},
            quality_validation=self.quality,
        )
        self.assertEqual(result["status"], "invalid_metric_configuration")
        self.assertEqual(
            result["errors"][0]["code"],
            "metric_not_supported_for_profile",
        )

    def test_enabled_profile_scores_with_fixed_weights(self):
        result = score_industry_profile(
            self.profile,
            {
                "ROE": self._verified(10, "field_a", "ROE"),
                "ROA": self._verified(10, "field_b", "ROA"),
            },
            self.specs,
            quality_validation=self.quality,
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["score"], 10)
        self.assertEqual(result["coverage_pct"], 100)
        self.assertEqual(result["score_pct_of_fixed_total"], 100)
        self.assertTrue(result["ready"])

    def test_missing_metric_does_not_redistribute_weight(self):
        result = score_industry_profile(
            self.profile,
            {"ROE": self._verified(10, "field_a", "ROE")},
            self.specs,
        )
        self.assertEqual(result["score"], 5)
        self.assertEqual(result["available_weight"], 5)
        self.assertEqual(result["missing_weight"], 5)
        self.assertEqual(result["coverage_pct"], 50)
        self.assertFalse(result["ready"])
        self.assertEqual(result["metric_results"]["ROA"]["reason"],
                         "metric_record_missing")


    def test_partial_missing_metrics_can_pass_fixed_coverage_gate(self):
        specs = {
            "ROE": dict(self.specs["ROE"], weight=7,
                              thresholds=[(0, 0), (10, 7)]),
            "ROA": dict(self.specs["ROA"], weight=3,
                              thresholds=[(0, 0), (10, 3)]),
        }
        result = score_industry_profile(
            self.profile,
            {"ROE": self._verified(10, "field_a", "ROE")},
            specs,
            minimum_coverage_pct=65,
            quality_validation=self.quality,
        )
        self.assertEqual(result["score"], 7)
        self.assertEqual(result["coverage_pct"], 70)
        self.assertTrue(result["ready"])

    def test_unverified_source_lineage_blocks_metric(self):
        metric = self._verified(10, "wrong_field", "ROE")
        result = score_industry_profile(
            self.profile, {"ROE": metric}, self.specs
        )
        self.assertFalse(result["metric_results"]["ROE"]["available"])
        self.assertEqual(
            result["metric_results"]["ROE"]["reason"],
            "metric_catalog_source_lineage_mismatch",
        )

    def test_period_basis_mismatch_blocks_metric(self):
        metric = self._verified(10, "field_a", "ROE")
        metric["period_basis"] = "YTD"
        result = score_industry_profile(
            self.profile, {"ROE": metric}, self.specs
        )
        self.assertFalse(result["metric_results"]["ROE"]["available"])
        self.assertEqual(
            result["metric_results"]["ROE"]["reason"],
            "metric_catalog_period_basis_mismatch",
        )


    def test_profile_metric_ids_are_immutable_and_runtime_proxies_are_rejected(self):
        profile = dict(self.profile, metric_groups={"quality": ["ROE", "ROA"]})
        missing = score_industry_profile(
            profile, {}, {"ROE": self.specs["ROE"]}
        )
        self.assertEqual(missing["status"], "invalid_metric_configuration")
        self.assertIn(
            "metric_specs_do_not_match_profile_requirements",
            [item["code"] for item in missing["errors"]],
        )
        proxy_specs = dict(self.specs)
        proxy_specs["proxy_metric"] = dict(
            self.specs["ROE"], group="quality"
        )
        proxy_specs.pop("ROA")
        proxy = score_industry_profile(profile, {}, proxy_specs)
        self.assertEqual(proxy["status"], "invalid_metric_configuration")
        self.assertIn(
            "metric_specs_do_not_match_profile_requirements",
            [item["code"] for item in proxy["errors"]],
        )

    def test_metric_spec_group_must_match_profile_requirement(self):
        profile = dict(self.profile, metric_groups={"quality": ["ROE", "ROA"]})
        specs = dict(self.specs)
        specs["ROE"] = dict(self.specs["ROE"], group="other")
        result = score_industry_profile(profile, {}, specs)
        self.assertEqual(result["status"], "invalid_metric_configuration")
        self.assertEqual(
            result["errors"][0]["code"],
            "metric_group_differs_from_profile_requirement",
        )

    def test_design_only_profile_cannot_score(self):
        profile = dict(self.profile, status="design_only", scoring_enabled=False)
        result = score_industry_profile(profile, {}, {})
        self.assertEqual(result["status"], "profile_not_enabled")
        self.assertIsNone(result["score"])

    def test_metric_weights_must_exactly_match_group_cap(self):
        specs = dict(self.specs)
        specs.pop("ROA")
        result = score_industry_profile(self.profile, {}, specs)
        self.assertEqual(result["status"], "invalid_metric_configuration")
        self.assertIn(
            "metric_weights_do_not_equal_fixed_group_weight",
            [item["code"] for item in result["errors"]],
        )


    def test_invalid_threshold_configuration_is_reported_not_raised(self):
        specs = dict(self.specs)
        specs["ROE"] = dict(self.specs["ROE"], thresholds=[])
        result = score_industry_profile(
            self.profile, {}, specs
        )
        self.assertEqual(result["status"], "invalid_metric_configuration")
        self.assertIn(
            "invalid_threshold_configuration",
            [item["code"] for item in result["errors"]],
        )

    def test_production_profile_requires_version(self):
        profile = dict(self.profile)
        profile.pop("profile_version")
        result = score_industry_profile(profile, {}, self.specs)
        self.assertEqual(result["status"], "invalid_profile")
        self.assertIn("profile_version_missing", result["errors"])


    def test_missing_or_invalid_bctc_quality_validation_blocks_scoring(self):
        complete = {
            "ROE": self._verified(10, "field_a", "ROE"),
            "ROA": self._verified(10, "field_b", "ROA"),
        }
        missing = score_industry_profile(self.profile, complete, self.specs)
        self.assertFalse(missing["ready"])
        self.assertIn(
            "bctc_quality_validation_missing",
            missing["data_gate"]["blocking_reasons"],
        )
        invalid = score_industry_profile(
            self.profile, complete, self.specs,
            quality_validation={"valid": False, "scoreable": False},
        )
        self.assertFalse(invalid["ready"])
        self.assertIn(
            "bctc_quality_validation_failed",
            invalid["data_gate"]["blocking_reasons"],
        )

    def test_blocking_quality_reason_overrides_full_coverage(self):
        result = score_industry_profile(
            self.profile,
            {
                "ROE": self._verified(10, "field_a", "ROE"),
                "ROA": self._verified(10, "field_b", "ROA"),
            },
            self.specs,
            blocking_reasons=["bctc_reconciliation_failed"],
            quality_validation=self.quality,
        )
        self.assertEqual(result["coverage_pct"], 100)
        self.assertFalse(result["ready"])
        self.assertIn("bctc_reconciliation_failed",
                      result["data_gate"]["blocking_reasons"])


class TestAllSectorMetricReadiness(unittest.TestCase):
    def test_every_registered_profile_has_explicit_readiness(self):
        report = summarize_all_sector_metric_readiness()
        self.assertEqual(report["profile_count"], len(PROFILE_TO_RULE))
        self.assertGreater(report["sector_count"], 0)
        self.assertFalse(report["production_score_ready"])
        self.assertEqual(set(report["by_profile"]), set(PROFILE_TO_RULE))
        for profile_id, row in report["by_profile"].items():
            with self.subTest(profile=profile_id):
                self.assertTrue(row["known_sector"])
                self.assertGreater(row["required_metric_count"], 0)
                self.assertGreaterEqual(row["source_mapping_coverage_pct"], 0)
                self.assertLessEqual(row["source_mapping_coverage_pct"], 100)
                self.assertFalse(row["production_score_ready"])
                self.assertEqual(
                    row["source_verified_count"],
                    len(row["verified_metric_ids"]),
                )
                self.assertEqual(
                    row["unavailable_count"],
                    len(row["unavailable_metric_ids"]),
                )
                self.assertEqual(
                    row["unsupported_count"],
                    len(row["unsupported_metric_ids"]),
                )
                self.assertEqual(
                    row["uncatalogued_count"],
                    len(row["uncatalogued_metric_ids"]),
                )

    def test_readiness_coverage_is_deterministic_and_not_approval(self):
        first = summarize_sector_metric_readiness("STEEL")
        second = summarize_sector_metric_readiness("STEEL")
        self.assertEqual(first, second)
        expected = round(
            first["source_verified_count"] /
            first["required_metric_count"] * 100, 2
        )
        self.assertEqual(first["source_mapping_coverage_pct"], expected)
        self.assertIn(
            first["readiness_status"],
            {
                "catalog_gaps", "source_data_unavailable",
                "source_lineage_catalogued", "no_verified_metrics",
            },
        )
        self.assertFalse(first["production_score_ready"])

    def test_unknown_sector_is_never_reported_as_ready(self):
        result = summarize_sector_metric_readiness("UNKNOWN_TEST_SECTOR")
        self.assertFalse(result["known_sector"])
        self.assertEqual(result["readiness_status"], "unknown_sector")
        self.assertEqual(result["source_mapping_coverage_pct"], 0.0)
        self.assertFalse(result["production_score_ready"])


if __name__ == "__main__":
    unittest.main()
