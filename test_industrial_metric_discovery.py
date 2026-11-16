# -*- coding: utf-8 -*-
import unittest

from industrial_metric_discovery import (
    annotate_industrial_dictionary_candidates,
    classify_industrial_candidate,
    discover_industrial_fields,
    discover_industrial_metadata,
)


class TestIndustrialMetricDiscovery(unittest.TestCase):
    def test_classification_is_section_specific_and_conservative(self):
        self.assertEqual(
            classify_industrial_candidate(
                "Doanh thu thuần về bán hàng và cung cấp dịch vụ",
                "INCOME_STATEMENT",
            ),
            "NET_REVENUE",
        )
        self.assertEqual(
            classify_industrial_candidate("Hàng tồn kho", "BALANCE_SHEET"),
            "INVENTORY",
        )
        self.assertEqual(
            classify_industrial_candidate(
                "Tiền chi để mua sắm, xây dựng TSCĐ", "CASH_FLOW"
            ),
            "CAPEX",
        )
        self.assertIsNone(
            classify_industrial_candidate("Doanh thu thuần", "BALANCE_SHEET")
        )
        self.assertIsNone(
            classify_industrial_candidate("Cho vay khách hàng", "BALANCE_SHEET")
        )


    def test_discovery_covers_distinct_ebitda_interest_tax_and_cashflow_labels(self):
        cases = [
            ("Lợi nhuận trước lãi vay và thuế", "INCOME_STATEMENT", "EBIT"),
            ("Lợi nhuận trước lãi vay, thuế và khấu hao", "INCOME_STATEMENT", "EBITDA"),
            ("Lợi nhuận trước thuế, lãi vay và khấu hao", "INCOME_STATEMENT", "EBITDA"),
            ("Lợi nhuận trước thuế và lãi vay", "INCOME_STATEMENT", "EBIT"),
            ("Earnings before interest and taxes", "INCOME_STATEMENT", "EBIT"),
            ("Chi phí lãi vay", "INCOME_STATEMENT", "INTEREST_EXPENSE"),
            ("Chi phí thuế thu nhập doanh nghiệp", "INCOME_STATEMENT", "INCOME_TAX_EXPENSE"),
            ("Tiền chi trả cổ tức", "CASH_FLOW", "DIVIDENDS_PAID"),
            ("Khấu hao tài sản cố định", "CASH_FLOW", "DEPRECIATION_ADD_BACK"),
        ]
        for label, section, expected in cases:
            with self.subTest(label=label):
                self.assertEqual(
                    classify_industrial_candidate(label, section), expected
                )

    def test_numeric_candidates_keep_source_fields_periods_and_conflicts(self):
        rows = {
            "balance_sheet": [
                {"source_field": "bsa12", "id": "x", "name": "Hàng tồn kho",
                 "period": "2026-Q2", "value": 100, "unit": "VND"},
                {"source_field": "bsa12", "id": "x", "name": "Hàng tồn kho",
                 "period": "2026-Q3", "value": 120, "unit": "VND"},
                {"source_field": "bsa12", "id": "x", "name": "Hàng tồn kho",
                 "period": "2026-Q3", "value": 121, "unit": "VND"},
                {"source_field": "bsa13", "name": "Tiền và các khoản tương đương tiền",
                 "period": "2026-Q3", "value": 50, "unit": "VND"},
                {"source_field": "bsa14", "name": "Hàng tồn kho",
                 "period": "2026-Q3", "value": float("nan"), "unit": "VND"},
            ],
            "income_statement": [
                {"source_field": "isa5", "name": "Giá vốn hàng bán",
                 "period": "2026-Q3", "value": -70, "unit": "VND"},
                {"source_field": "isa1",
                 "name": "Doanh thu thuần về bán hàng và cung cấp dịch vụ",
                 "period": "2026-Q3", "value": 100, "unit": "VND"},
            ],
            "cash_flow": [
                {"source_field": "cfa18",
                 "name": "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
                 "period": "2026-Q3", "value": 25, "unit": "VND"},
            ],
        }
        result = discover_industrial_fields(rows)
        self.assertEqual(result["mapping_status"], "DISCOVERY_ONLY_NOT_VERIFIED")
        inventory = next(
            item for item in result["candidates"]
            if item["category"] == "INVENTORY"
        )
        self.assertEqual(inventory["source_field"], "bsa12")
        self.assertEqual(inventory["period_values"]["2026-Q2"], 100)
        self.assertEqual(inventory["conflicting_periods"], ["2026-Q3"])
        self.assertIn(
            "CASH_AND_CASH_EQUIVALENTS",
            {item["category"] for item in result["candidates"]},
        )
        by_category = {item["category"]: item for item in result["candidates"]}
        self.assertIn("COST_OF_GOODS_SOLD", by_category)
        self.assertEqual(
            by_category["COST_OF_GOODS_SOLD"]["canonical_candidate_id"],
            "IS_COST_OF_GOODS_SOLD",
        )
        self.assertEqual(
            by_category["NET_REVENUE"]["canonical_mapping_status"],
            "TITLE_RULE_CANDIDATE",
        )
        self.assertEqual(
            by_category["OPERATING_CASH_FLOW"]["canonical_mapping_status"],
            "FIELD_CODE_MATCH",
        )

    def test_metadata_discovery_finds_fields_without_numeric_rows(self):
        catalog = {
            "BALANCE_SHEET": {
                "bsa12": "Hàng tồn kho",
                "bsa21": "Vay và nợ thuê tài chính ngắn hạn",
            },
            "INCOME_STATEMENT": {
                "isa5": "Giá vốn hàng bán",
            },
            "CASH_FLOW": {
                "cfa19": "Tiền chi để mua sắm, xây dựng TSCĐ",
            },
        }
        result = discover_industrial_metadata(catalog)
        self.assertEqual(
            result["mapping_status"], "METADATA_DISCOVERY_ONLY_NOT_VERIFIED"
        )
        self.assertEqual(result["candidate_count"], 4)
        self.assertEqual(
            {item["category"] for item in result["candidates"]},
            {"INVENTORY", "SHORT_TERM_BORROWINGS",
             "COST_OF_GOODS_SOLD", "CAPEX"},
        )


    def test_dictionary_hint_does_not_upgrade_discovery_to_verified_mapping(self):
        discovery = discover_industrial_metadata({
            "INCOME_STATEMENT": {"isa5": "Giá vốn hàng bán"},
            "BALANCE_SHEET": {"bsa12": "Hàng tồn kho"},
        })
        annotated = annotate_industrial_dictionary_candidates(discovery)
        entries = {item["source_field"]: item for item in annotated["candidates"]}
        self.assertEqual(entries["isa5"]["canonical_candidate"], "IS_COST_OF_GOODS_SOLD")
        self.assertEqual(entries["isa5"]["dictionary_match_status"], "CANDIDATE_ONLY")
        self.assertIsNone(entries["bsa12"]["canonical_candidate"])
        self.assertEqual(annotated["mapping_status"], "DISCOVERY_ONLY_NOT_VERIFIED")

    def test_limits_are_validated_and_results_are_truncated(self):
        with self.assertRaises(ValueError):
            discover_industrial_fields({}, max_fields=0)
        rows = {
            "balance_sheet": [
                {"source_field": "bsa12", "name": "Hàng tồn kho",
                 "period": "2026-Q3", "value": 1, "unit": "VND"},
                {"source_field": "bsa13", "name": "Tiền và các khoản tương đương tiền",
                 "period": "2026-Q3", "value": 2, "unit": "VND"},
            ]
        }
        result = discover_industrial_fields(rows, max_fields=1)
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertTrue(result["truncated"])


if __name__ == "__main__":
    unittest.main()
