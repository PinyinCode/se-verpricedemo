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

    def test_separates_npl_ratio_from_npl_balance(self):
        self.assertEqual(classify_bank_candidate("Tỷ lệ nợ xấu"), "NPL_RATIO")
        self.assertEqual(classify_bank_candidate("Nợ xấu"), "NPL")

    def test_classifies_group_three_four_five_as_separate_components(self):
        self.assertEqual(classify_bank_candidate("Dư nợ nhóm 3"), "NPL_GROUP_3")
        self.assertEqual(classify_bank_candidate("Nợ nhóm 4"), "NPL_GROUP_4")
        self.assertEqual(classify_bank_candidate("Dư nợ nhóm 5"), "NPL_GROUP_5")

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



    def test_metadata_discovery_is_balance_sheet_only(self):
        catalog = {
            "BALANCE_SHEET": {"bsb200": "Nợ nhóm 2"},
            "INCOME_STATEMENT": {"isb200": "Nợ nhóm 2"},
            "CASH_FLOW": {"cfa200": "Nợ nhóm 2"},
        }
        result = discover_bank_asset_quality_metadata(catalog)
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates"][0]["source_field"], "bsb200")

    def test_non_finite_numeric_candidates_are_ignored(self):
        rows = [
            {"source_field": "bsb200", "name": "Nợ nhóm 2",
             "period": "2026-Q3", "value": float("nan"), "unit": "VND"},
            {"source_field": "bsb201", "name": "Nợ xấu",
             "period": "2026-Q3", "value": float("inf"), "unit": "VND"},
        ]
        result = discover_bank_asset_quality_fields(rows)
        self.assertEqual(result["candidate_count"], 0)

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
