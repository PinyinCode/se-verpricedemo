# -*- coding: utf-8 -*-
"""Regression tests for the VCI BCTC dictionary and scoring guardrails."""
import unittest

from bctc_rules import (
    VCI_FIELD_MAP,
    SECTOR_RULES,
    DATA_QUALITY_RULES,
    canonicalize_vci_field,
    resolve_vci_unit,
    get_vci_field_rule,
    validate_bctc_rules,
)


class TestBctcDictionary(unittest.TestCase):
    def test_dictionary_and_sector_weights_are_valid(self):
        result = validate_bctc_rules()
        self.assertTrue(result["valid"], result["errors"])
        self.assertGreater(result["mapped_field_count"], 0)
        for sector, rule in SECTOR_RULES.items():
            self.assertEqual(sum(rule["weights"].values()), 50, sector)

    def test_equity_and_total_funding_are_distinct(self):
        mapping = VCI_FIELD_MAP["balance_sheet"]
        self.assertEqual(mapping["bsa78"], "BS_OWNERS_EQUITY")
        self.assertEqual(mapping["bsa96"], "BS_TOTAL_LIABILITIES_AND_EQUITY")
        self.assertNotEqual(mapping["bsa78"], mapping["bsa96"])

    def test_ambiguous_customer_loan_fields_are_not_gross_loan_inputs(self):
        mapping = VCI_FIELD_MAP["balance_sheet"]
        self.assertEqual(mapping["bsb103"], "BS_CUSTOMER_LOANS_UNVERIFIED")
        self.assertEqual(mapping["bsb104"], "BS_CUSTOMER_LOANS_DETAIL_UNVERIFIED")
        self.assertFalse(DATA_QUALITY_RULES["gross_customer_loans_mapping_verified"])

    def test_ambiguous_customer_loan_title_is_not_auto_mapped(self):
        self.assertIsNone(
            canonicalize_vci_field("balance_sheet", "bsa999", "Cho vay khách hàng")
        )
        self.assertFalse(get_vci_field_rule("balance_sheet", "bsb103")["scoreable"])
        self.assertFalse(get_vci_field_rule("balance_sheet", "bsb104")["scoreable"])

    def test_bank_cir_requires_verified_operating_expense_label(self):
        mapping = VCI_FIELD_MAP["income_statement"]
        self.assertEqual(mapping["isb38"], "IS_TOTAL_OPERATING_INCOME_BANK")
        self.assertEqual(mapping["isb39"], "IS_OPERATING_EXPENSE_BANK_UNVERIFIED")
        self.assertEqual(
            canonicalize_vci_field("income_statement", "isb39", "Chi phí hoạt động"),
            "IS_OPERATING_EXPENSE_BANK",
        )
        self.assertEqual(
            canonicalize_vci_field("income_statement", "isb39", "Chi phí quản lý doanh nghiệp"),
            "IS_OPERATING_EXPENSE_BANK_UNVERIFIED",
        )
        self.assertTrue(DATA_QUALITY_RULES["bank_cir_requires_verified_operating_expense_label"])

    def test_unknown_field_is_not_guessed(self):
        self.assertIsNone(canonicalize_vci_field("balance_sheet", "bsa999", "bsa999"))
        rule = get_vci_field_rule("balance_sheet", "bsa999")
        self.assertFalse(rule["known"])
        self.assertIsNone(rule["canonical_id"])

    def test_units_are_conservative_and_eps_is_per_share(self):
        self.assertEqual(resolve_vci_unit("income_statement", "isa23", "VND"), "VND/share")
        self.assertEqual(resolve_vci_unit("balance_sheet", "bsa2", None), "VND")
        self.assertEqual(resolve_vci_unit("balance_sheet", "bsa999", None), "")

    def test_subtotals_are_not_added_to_components(self):
        self.assertTrue(DATA_QUALITY_RULES["do_not_sum_subtotals_and_components"])
        self.assertTrue(DATA_QUALITY_RULES["ambiguous_duplicate_labels_are_not_scoring_inputs"])


if __name__ == "__main__":
    unittest.main()
