# -*- coding: utf-8 -*-
import unittest

from metric_catalog import (
    get_metric_definition,
    metric_is_supported_for_sector,
    get_sector_metric_catalog,
    summarize_sector_metric_readiness,
    summarize_all_sector_metric_readiness,
    validate_metric_catalog,
)


class TestVerifiedMetricCatalog(unittest.TestCase):
    def test_catalog_source_fields_exist_in_vci_dictionary(self):
        result = validate_metric_catalog()
        self.assertTrue(result["valid"], result["errors"])
        self.assertGreaterEqual(result["metric_count"], 15)

    def test_bank_catalog_separates_verified_and_unavailable_metrics(self):
        catalog = get_sector_metric_catalog("NGAN_HANG")
        self.assertIn("growth", catalog)
        entries = {
            item["metric_id"]: item
            for group in catalog.values() for item in group
        }
        self.assertEqual(
            entries["CREDIT_GROWTH"]["catalog_status"],
            "verified_source_fields_formula_requires_same_quarter_yoy",
        )
        self.assertEqual(
            entries["NPL_RATIO"]["catalog_status"],
            "unavailable_npl_source_field_not_verified",
        )
        self.assertEqual(
            entries["CASA"]["catalog_status"],
            "unavailable_demand_deposit_source_field_not_verified",
        )
        self.assertEqual(
            entries["ROE"]["catalog_status"],
            "verified_source_fields_formula_requires_average_equity",
        )
        self.assertEqual(entries["ROA"]["catalog_status"],
            "verified_source_fields_formula_requires_average_assets")
        self.assertEqual(entries["ROE"]["unit"], "percent")
        self.assertEqual(entries["ROE"]["period_basis"], "TTM")
        self.assertEqual(
            entries["ROE"]["source_sections"],
            {"income_statement": ["isa20"], "balance_sheet": ["bsa78"]},
        )
        self.assertEqual(entries["NPL_RATIO"]["formula"], "non_performing_loans / gross_customer_loans * 100")
        self.assertEqual(entries["NPL_RATIO"]["source_fields"], ["bsb104"])

    def test_profile_alias_resolves_to_sector_metric_requirements(self):
        steel = get_sector_metric_catalog("STEEL")
        canonical = get_sector_metric_catalog("THEP")
        self.assertEqual(steel, canonical)
        self.assertIn("normalized_margin", steel)


    def test_metric_source_mapping_is_not_reused_across_sectors(self):
        insurance = get_sector_metric_catalog("INSURANCE")
        insurance_metric_ids = {
            item["metric_id"]
            for group in insurance.values() for item in group
        }
        # CIR is a bank operating-efficiency metric, not an insurance metric.
        self.assertNotIn("CIR", insurance_metric_ids)


    def test_readiness_summary_distinguishes_verified_sources_from_score_approval(self):
        bank = summarize_sector_metric_readiness("NGAN_HANG")
        self.assertTrue(bank["known_sector"])
        self.assertGreater(bank["required_metric_count"], 0)
        self.assertGreater(bank["source_verified_count"], 0)
        self.assertGreater(bank["unavailable_count"], 0)
        self.assertEqual(
            bank["unsupported_or_uncatalogued_count"],
            bank["unsupported_count"] + bank["uncatalogued_count"],
        )
        self.assertFalse(bank["production_score_ready"])
        unknown = summarize_sector_metric_readiness("UNKNOWN")
        self.assertFalse(unknown["known_sector"])
        self.assertEqual(unknown["required_metric_count"], 0)
        self.assertEqual(unknown["unsupported_count"], 0)
        self.assertEqual(unknown["uncatalogued_count"], 0)

    def test_metric_definitions_are_defensive_copies(self):
        definition = get_metric_definition("NPL_RATIO")
        definition["status"] = "verified"
        self.assertNotEqual(
            get_metric_definition("NPL_RATIO")["status"], "verified"
        )



    def test_cashflow_and_debt_metrics_expose_unverified_inputs_explicitly(self):
        energy = get_sector_metric_catalog("ENERGY")
        energy_entries = {
            item["metric_id"]: item
            for group in energy.values() for item in group
        }
        self.assertEqual(
            energy_entries["CFO"]["catalog_status"],
            "verified_source_field_formula_ttm_sum",
        )
        self.assertEqual(
            energy_entries["FCF"]["catalog_status"],
            "unavailable_capex_sign_and_completeness_not_verified",
        )
        steel = get_sector_metric_catalog("STEEL")
        steel_entries = {
            item["metric_id"]: item
            for group in steel.values() for item in group
        }
        self.assertEqual(
            steel_entries["interest_coverage"]["catalog_status"],
            "unavailable_ebit_and_interest_expense_source_fields_not_verified",
        )
        self.assertEqual(
            steel_entries["net_debt"]["catalog_status"],
            "unavailable_interest_bearing_debt_components_not_verified",
        )


    def test_global_metric_readiness_report_is_complete_and_never_approves_scoring(self):
        report = summarize_all_sector_metric_readiness()
        self.assertGreater(report["profile_count"], 0)
        self.assertGreater(report["sector_count"], 0)
        self.assertGreater(report["required_metric_count"], 0)
        self.assertFalse(report["production_score_ready"])
        self.assertEqual(
            report["required_metric_count"],
            sum(item["required_metric_count"] for item in report["by_profile"].values()),
        )
        self.assertEqual(
            report["source_verified_count"],
            sum(item["source_verified_count"] for item in report["by_profile"].values()),
        )
        self.assertIn("NGAN_HANG", report["by_sector"])
        self.assertIn("STEEL", report["by_profile"])
        steel = report["by_profile"]["STEEL"]
        self.assertEqual(
            steel["source_verified_count"], len(steel["verified_metric_ids"])
        )
        self.assertEqual(
            steel["unavailable_count"], len(steel["unavailable_metric_ids"])
        )
        self.assertEqual(
            steel["uncatalogued_count"], len(steel["uncatalogued_metric_ids"])
        )

    def test_metric_applicability_uses_canonical_sector_aliases(self):
        self.assertTrue(metric_is_supported_for_sector(
            "CASH_CONVERSION", "BAT_DONG_SAN"
        ))
        self.assertTrue(metric_is_supported_for_sector(
            "CASH_CONVERSION", "CONG_NGHE_BAN_LE"
        ))
        self.assertTrue(metric_is_supported_for_sector(
            "CASH_CONVERSION", "TECHNOLOGY"
        ))
        self.assertTrue(metric_is_supported_for_sector(
            "CFO", "ENERGY"
        ))
        self.assertTrue(metric_is_supported_for_sector(
            "CFO", "NANG_LUONG"
        ))
        self.assertTrue(metric_is_supported_for_sector(
            "CFO", "BAT_DONG_SAN"
        ))
        for sector in (
            "AGRI", "NGUYEN_VAT_LIEU_CHU_KY", "AVIATION",
            "CONSTRUCTION", "PETROLEUM", "PHARMA", "TEXTILE",
            "RUBBER", "PLASTICS_CHEMICALS", "AGRI_PROCESSING",
        ):
            with self.subTest(sector=sector):
                self.assertTrue(metric_is_supported_for_sector("ROE", sector))
                self.assertTrue(metric_is_supported_for_sector("ROA", sector))
                self.assertTrue(metric_is_supported_for_sector("CFO", sector))
                self.assertTrue(metric_is_supported_for_sector("CASH_CONVERSION", sector))
        self.assertTrue(metric_is_supported_for_sector(
            "CASH_CONVERSION", "STEEL"
        ))
        steel = get_sector_metric_catalog("STEEL")
        steel_roe = next(
            item for group in steel.values() for item in group
            if item["metric_id"] == "ROE"
        )
        self.assertEqual(
            steel_roe["catalog_status"],
            "verified_source_fields_formula_requires_average_equity",
        )
        self.assertFalse(metric_is_supported_for_sector(
            "CASH_CONVERSION", "NGAN_HANG"
        ))
        self.assertFalse(metric_is_supported_for_sector(
            "CASH_CONVERSION", "INSURANCE"
        ))
        self.assertFalse(metric_is_supported_for_sector(
            "UNKNOWN_METRIC", "BAT_DONG_SAN"
        ))

    def test_unknown_sector_and_metric_do_not_get_fallback(self):
        self.assertIsNone(get_sector_metric_catalog("UNKNOWN"))
        self.assertIsNone(get_metric_definition("UNKNOWN_METRIC"))


if __name__ == "__main__":
    unittest.main()
