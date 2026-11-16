# -*- coding: utf-8 -*-
import unittest

from industry_profiles import (
    INDUSTRY_PROFILES,
    get_industry_profile,
    normalize_profile_id,
    summarize_all_sector_scoring_readiness,
    LEGACY_SCORING_PATHS,
    LEGACY_SCORING_AVAILABLE_WEIGHTS,
    LEGACY_SCORING_AVAILABLE_WEIGHTS,
    validate_industry_profiles,
)


class TestIndustryProfileRegistry(unittest.TestCase):

    def test_production_profile_registry_requires_auditable_approval(self):
        profile = {
            "profile_version": "1.0.0",
            "status": "production",
            "scoring_enabled": True,
            "score_weight": 10,
            "weights": {"quality": 10},
            "metric_groups": {"quality": ["ROE"]},
        }
        invalid = validate_industry_profiles({"TEST": profile})
        self.assertFalse(invalid["valid"])
        self.assertIn(
            "production_approval_incomplete",
            [item["code"] for item in invalid["errors"]],
        )

        approved = dict(profile, approval={
            "status": "approved",
            "threshold_set_version": "thresholds-1",
            "approved_at": "2026-11-16",
            "reviewer": "unit-test",
            "evidence_ref": "historical-fixture",
            "parity_report_ref": "parity-report-fixture",
            "parity_sample_count": 20,
            "parity_pass_rate": 0.95,
            "source_mapping_review": "passed",
            "historical_parity": "passed",
        })
        valid = validate_industry_profiles({"TEST": approved})
        self.assertTrue(valid["valid"], valid["errors"])
        self.assertEqual(valid["production_count"], 1)

        insufficient_count = dict(approved, approval=dict(
            approved["approval"], parity_sample_count=19
        ))
        self.assertFalse(
            validate_industry_profiles({"TEST": insufficient_count})["valid"]
        )
        insufficient_rate = dict(approved, approval=dict(
            approved["approval"], parity_pass_rate=0.94
        ))
        self.assertFalse(
            validate_industry_profiles({"TEST": insufficient_rate})["valid"]
        )

    def test_registry_configuration_is_consistent(self):
        result = validate_industry_profiles()
        self.assertTrue(result["valid"], result["errors"])
        self.assertGreaterEqual(result["profile_count"], 15)


    def test_every_profile_has_an_explicit_version(self):
        for profile_id, profile in INDUSTRY_PROFILES.items():
            with self.subTest(profile=profile_id):
                self.assertTrue(profile.get("profile_version"))

    def test_all_design_profiles_have_fixed_50_point_weights(self):
        for profile_id, profile in INDUSTRY_PROFILES.items():
            if profile["status"] != "design_only":
                continue
            with self.subTest(profile=profile_id):
                self.assertEqual(sum(profile["weights"].values()), 50)
                self.assertFalse(profile["scoring_enabled"])


    def test_design_profiles_expose_sector_specific_metric_requirements(self):
        bank = get_industry_profile("NGAN_HANG")
        self.assertIn("asset_quality", bank["metric_groups"])
        self.assertIn("NPL_RATIO", bank["metric_groups"]["asset_quality"])
        self.assertEqual(
            bank["metric_blueprint_status"],
            "requirements_only_no_approved_thresholds",
        )
        steel = get_industry_profile("THEP")
        self.assertIn("normalized_margin", steel["metric_groups"])
        self.assertNotEqual(
            bank["metric_groups"], steel["metric_groups"]
        )

    def test_bank_profile_does_not_claim_unverified_npl_car_or_casa(self):
        profile = get_industry_profile("NGAN_HANG")
        self.assertIn("NPL_RATIO", profile["data_gaps"])
        self.assertIn("CAR", profile["data_gaps"])
        self.assertIn("CASA", profile["data_gaps"])
        self.assertFalse(profile["scoring_enabled"])

    def test_previously_unconfigured_groups_now_have_distinct_design_blueprints(self):
        expected_groups = {
            "AGRI": "biological_assets",
            "NGUYEN_VAT_LIEU_CHU_KY": "normalized_margin",
            "AVIATION": "fleet_utilization",
            "CONSTRUCTION": "backlog_quality",
            "PETROLEUM": "segment_mix",
        }
        for group, required_metric_group in expected_groups.items():
            with self.subTest(group=group):
                profile = get_industry_profile(group)
                self.assertEqual(profile["status"], "design_only")
                self.assertIn(required_metric_group, profile["metric_groups"])
                self.assertEqual(sum(profile["weights"].values()), 50)
                self.assertFalse(profile["scoring_enabled"])

    def test_every_industry_has_an_explicit_legacy_scoring_route(self):
        self.assertEqual(set(LEGACY_SCORING_PATHS), set(INDUSTRY_PROFILES))
        report = summarize_all_sector_scoring_readiness()
        self.assertEqual(report["profile_count"], len(INDUSTRY_PROFILES))
        self.assertEqual(report["legacy_path_count"], len(INDUSTRY_PROFILES))
        self.assertTrue(report["all_profiles_have_legacy_path"])
        self.assertEqual(report["production_score_ready_count"], 0)
        for profile_id, item in report["by_profile"].items():
            with self.subTest(profile=profile_id):
                self.assertTrue(item["legacy_scoring_path_configured"])
                self.assertFalse(item["production_score_ready"])
                self.assertIn(
                    item["metric_readiness_status"],
                    {
                        "catalog_gaps", "source_data_unavailable",
                        "source_lineage_catalogued", "no_verified_metrics",
                    },
                )
                self.assertEqual(
                    item["source_verified_count"],
                    len(item["verified_metric_ids"]),
                )
                self.assertEqual(
                    item["unavailable_count"],
                    len(item["unavailable_metric_ids"]),
                )
                self.assertEqual(
                    item["unsupported_or_uncatalogued_count"],
                    len(item["unsupported_metric_ids"])
                    + len(item["uncatalogued_metric_ids"]),
                )
                self.assertGreaterEqual(item["source_mapping_coverage_pct"], 0)
                self.assertLessEqual(item["source_mapping_coverage_pct"], 100)

    def test_readiness_exposes_partial_trial_weight_and_missing_weight(self):
        report = summarize_all_sector_scoring_readiness()
        self.assertEqual(report["legacy_trial_partial_count"], 25)
        for profile_id, row in report["by_profile"].items():
            with self.subTest(profile=profile_id):
                self.assertEqual(
                    row["legacy_missing_weight"],
                    max(row["score_weight"] - row["legacy_max_available_weight"], 0),
                )
                self.assertIn(
                    row["legacy_trial_status"],
                    {"trial_partial", "trial_bctc_coverage_possible"},
                )
                self.assertEqual(
                    row["legacy_can_clear_65pct_gate"],
                    row["legacy_max_available_weight"] >= row["score_weight"] * 0.65,
                )

    def test_legacy_max_bctc_coverage_registry_is_complete_and_bounded(self):
        self.assertEqual(
            set(LEGACY_SCORING_AVAILABLE_WEIGHTS),
            set(INDUSTRY_PROFILES),
        )
        report = summarize_all_sector_scoring_readiness()
        for profile_id, profile in INDUSTRY_PROFILES.items():
            with self.subTest(profile=profile_id):
                weight = LEGACY_SCORING_AVAILABLE_WEIGHTS[profile_id]
                self.assertIsInstance(weight, int)
                self.assertGreaterEqual(weight, 0)
                self.assertLessEqual(weight, profile["score_weight"])
                row = report["by_profile"][profile_id]
                expected_pct = round(
                    weight / max(float(profile["score_weight"]), 1) * 100, 1
                )
                self.assertEqual(row["legacy_max_available_weight"], weight)
                self.assertEqual(row["legacy_max_bctc_coverage_pct"], expected_pct)
                self.assertEqual(
                    row["legacy_can_clear_65pct_gate"],
                    weight >= profile["score_weight"] * 0.65,
                )

    def test_legacy_scoring_route_registry_matches_real_screener_branches(self):
        from pathlib import Path
        import ast

        source_path = Path(__file__).with_name("price_api.py")
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename="price_api.py")
        evaluator = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "evaluate_stock_from_db"
        )
        evaluator_source = ast.get_source_segment(source, evaluator)
        required_markers = (
            'sector == "NGAN_HANG"',
            'sector == "CHUNG_KHOAN"',
            'sector == "BAT_DONG_SAN"',
            "is_technology",
            "is_retail",
            "is_steel or is_fertilizer",
            "is_energy",
            "is_utility",
            "is_holding",
            "is_maritime",
            "is_consumer",
            "is_building_materials",
            "is_insurance",
            "is_agri",
            "is_cyclical_material",
            "is_aviation",
            "is_construction",
            "is_petroleum",
            "is_pharma or is_textile or is_rubber or is_plastics_chemicals or is_agri_processing",
        )
        for marker in required_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, evaluator_source)

    def test_newly_split_industries_have_distinct_profiles_and_scoring_routes(self):
        expected = {
            "PHARMA": "product_portfolio",
            "TEXTILE": "order_growth",
            "RUBBER": "replacement_export_growth",
            "PLASTICS_CHEMICALS": "volume_price_spread",
            "AGRI_PROCESSING": "segment_growth",
        }
        for sector, group in expected.items():
            with self.subTest(sector=sector):
                profile = get_industry_profile(sector)
                self.assertEqual(profile["status"], "design_only")
                self.assertIn(group, profile["metric_groups"])
                self.assertEqual(sum(profile["weights"].values()), 50)
                self.assertTrue(LEGACY_SCORING_PATHS[sector])
                self.assertEqual(LEGACY_SCORING_AVAILABLE_WEIGHTS[sector], 40)

    def test_unknown_industry_has_no_fallback_profile(self):
        self.assertIsNone(get_industry_profile("UNKNOWN_SECTOR"))
        self.assertIsNone(normalize_profile_id("UNKNOWN_SECTOR"))

    def test_aliases_resolve_to_canonical_profiles(self):
        self.assertEqual(normalize_profile_id("THEP"), "STEEL")
        self.assertEqual(normalize_profile_id("PHAN_BON"), "FERTILIZER")
        self.assertEqual(normalize_profile_id("TIEN_ICH"), "DIEN_NUOC_HA_TANG")

    def test_profile_result_is_defensive_copy(self):
        profile = get_industry_profile("NGAN_HANG")
        profile["weights"]["growth"] = 999
        self.assertNotEqual(
            get_industry_profile("NGAN_HANG")["weights"]["growth"], 999
        )

    def test_profiles_expose_data_gaps_instead_of_silently_ignoring_them(self):
        for profile_id, profile in INDUSTRY_PROFILES.items():
            with self.subTest(profile=profile_id):
                self.assertIsInstance(profile.get("data_gaps"), list)


if __name__ == "__main__":
    unittest.main()
