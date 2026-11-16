# -*- coding: utf-8 -*-
import unittest

from bank_asset_quality import (
    classify_bank_candidate,
    discover_bank_asset_quality_fields,
    discover_bank_asset_quality_metadata,
)


class TestBankAssetQualityDiscovery(unittest.TestCase):
    def test_classifies_npl_labels_with_and_without_accents(self):
        self.assertEqual(classify_bank_candidate("Nợ xấu"), "NPL")
        self.assertEqual(classify_bank_candidate("Nợ có khả năng mất vốn"), "NPL")
        self.assertEqual(classify_bank_candidate("Non-performing loans"), "NPL")

    def test_classifies_group_two_and_demand_deposits(self):
        self.assertEqual(classify_bank_candidate("Nợ nhóm 2"), "GROUP_2")
        self.assertEqual(classify_bank_candidate("Tiền gửi không kỳ hạn"), "DEMAND_DEPOSITS")

    def test_does_not_guess_generic_customer_loan_label(self):
        self.assertIsNone(classify_bank_candidate("Cho vay khách hàng"))
        self.assertIsNone(classify_bank_candidate("Loans to customers"))

    def test_groups_period_values_by_original_source_field(self):
        result = discover_bank_asset_quality_fields([
            {"name": "Nợ xấu", "source_field": "bsb999", "id": "RAW",
             "period": "2026-Q2", "value": 12, "unit": "VND"},
            {"name": "Nợ xấu", "source_field": "bsb999", "id": "RAW",
             "period": "2026-Q3", "value": 14, "unit": "VND"},
        ])
        self.assertEqual(result["candidate_count"], 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["source_field"], "bsb999")
        self.assertEqual(candidate["period_values"]["2026-Q3"], 14.0)
        self.assertEqual(candidate["mapping_status"] if "mapping_status" in candidate else result["mapping_status"],
                         "DISCOVERY_ONLY_NOT_VERIFIED")

    def test_conflicting_duplicates_are_reported(self):
        result = discover_bank_asset_quality_fields([
            {"name": "Nợ nhóm 2", "source_field": "bsb998",
             "period": "2026-Q3", "value": 10},
            {"name": "Nợ nhóm 2", "source_field": "bsb998",
             "period": "2026-Q3", "value": 11},
        ])
        self.assertEqual(result["candidates"][0]["conflicting_periods"], ["2026-Q3"])


    def test_metadata_catalog_finds_fields_without_numeric_rows(self):
        result = discover_bank_asset_quality_metadata({
            "BALANCE_SHEET": {
                "bsb777": "Dư nợ nhóm 2",
                "bsb778": "Nợ có khả năng mất vốn",
                "bsb779": "Cho vay khách hàng",
            }
        })
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(
            {item["source_field"] for item in result["candidates"]},
            {"bsb777", "bsb778"},
        )

    def test_metadata_discovery_does_not_guess_generic_loan_field(self):
        result = discover_bank_asset_quality_metadata({
            "BALANCE_SHEET": {"bsb777": "Cho vay khách hàng"}
        })
        self.assertEqual(result["candidate_count"], 0)

    def test_rejects_invalid_limits(self):
        with self.assertRaises(ValueError):
            discover_bank_asset_quality_fields([], max_periods=13)


if __name__ == "__main__":
    unittest.main()
