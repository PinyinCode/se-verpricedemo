# -*- coding: utf-8 -*-
import unittest

from industry_profiles import (
    INDUSTRY_PROFILES,
    get_industry_profile,
    normalize_profile_id,
    validate_industry_profiles,
)


class TestIndustryProfileRegistry(unittest.TestCase):
    def test_registry_configuration_is_consistent(self):
        result = validate_industry_profiles()
        self.assertTrue(result["valid"], result["errors"])
        self.assertGreaterEqual(result["profile_count"], 15)

    def test_all_design_profiles_have_fixed_50_point_weights(self):
        for profile_id, profile in INDUSTRY_PROFILES.items():
            if profile["status"] != "design_only":
                continue
            with self.subTest(profile=profile_id):
                self.assertEqual(sum(profile["weights"].values()), 50)
                self.assertFalse(profile["scoring_enabled"])

    def test_bank_profile_does_not_claim_unverified_npl_car_or_casa(self):
        profile = get_industry_profile("NGAN_HANG")
        self.assertIn("NPL_RATIO", profile["data_gaps"])
        self.assertIn("CAR", profile["data_gaps"])
        self.assertIn("CASA", profile["data_gaps"])
        self.assertFalse(profile["scoring_enabled"])

    def test_aviation_construction_and_petroleum_are_not_given_neighbor_weights(self):
        for group in ("AVIATION", "CONSTRUCTION", "PETROLEUM", "AGRI", "NGUYEN_VAT_LIEU_CHU_KY"):
            with self.subTest(group=group):
                profile = get_industry_profile(group)
                self.assertEqual(profile["status"], "not_configured")
                self.assertEqual(profile["weights"], {})
                self.assertFalse(profile["scoring_enabled"])

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
