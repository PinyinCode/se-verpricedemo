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
    METRIC_FORMULAS,
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

    def test_customer_loan_fields_verified_by_cross_bank_reconciliation(self):
        mapping = VCI_FIELD_MAP["balance_sheet"]
        self.assertEqual(mapping["bsb103"], "BS_CUSTOMER_LOANS_NET")
        self.assertEqual(mapping["bsb104"], "BS_CUSTOMER_LOANS_GROSS")
        self.assertEqual(mapping["bsb105"], "BS_PROVISION_FOR_CUSTOMER_LOANS")
        self.assertTrue(DATA_QUALITY_RULES["gross_customer_loans_mapping_verified"])
        for field in ("bsb103", "bsb104", "bsb105"):
            self.assertTrue(get_vci_field_rule("balance_sheet", field)["scoreable"])

    def test_customer_loan_gross_net_and_provision_are_not_double_counted(self):
        self.assertEqual(
            get_vci_field_rule("balance_sheet", "bsb104")["double_count_with"],
            ("bsb103", "bsb105"),
        )

    def test_ambiguous_customer_loan_title_is_not_auto_mapped(self):
        self.assertIsNone(
            canonicalize_vci_field("balance_sheet", "bsa999", "Cho vay khách hàng")
        )

    def test_bank_cir_requires_verified_operating_expense_label(self):
        mapping = VCI_FIELD_MAP["income_statement"]
        self.assertEqual(mapping["isb38"], "IS_TOTAL_OPERATING_INCOME_BANK")
        self.assertEqual(mapping["isb39"], "IS_OPERATING_EXPENSE_BANK")
        self.assertEqual(
            canonicalize_vci_field("income_statement", "isb39", "Chi phí hoạt động"),
            "IS_OPERATING_EXPENSE_BANK",
        )
        self.assertEqual(
            canonicalize_vci_field("income_statement", "isb39", "Chi phí quản lý doanh nghiệp"),
            "IS_OPERATING_EXPENSE_BANK",
        )
        self.assertTrue(DATA_QUALITY_RULES["bank_cir_requires_verified_operating_expense_label"])
        self.assertTrue(DATA_QUALITY_RULES["bank_isb39_verified_by_ppop_reconciliation"])
        self.assertTrue(get_vci_field_rule("income_statement", "isb39")["scoreable"])

    def test_ctg_bank_income_statement_fields_and_new_metrics(self):
        mapping = VCI_FIELD_MAP["income_statement"]
        expected = {
            "isb25": "IS_INTEREST_INCOME", "isb26": "IS_INTEREST_EXPENSE",
            "isb27": "IS_NET_INTEREST_INCOME", "isb28": "IS_SERVICE_INCOME",
            "isb29": "IS_SERVICE_EXPENSE", "isb30": "IS_NET_SERVICE_INCOME",
            "isb31": "IS_NET_FX_INCOME", "isb32": "IS_NET_TRADING_SECURITIES_INCOME",
            "isb33": "IS_NET_INVESTMENT_SECURITIES_INCOME", "isb34": "IS_OTHER_INCOME_BANK",
            "isb35": "IS_OTHER_EXPENSE_BANK", "isb36": "IS_NET_OTHER_INCOME_BANK",
            "isb37": "IS_DIVIDEND_INCOME", "isb38": "IS_TOTAL_OPERATING_INCOME_BANK",
            "isb39": "IS_OPERATING_EXPENSE_BANK",
            "isb40": "IS_PROFIT_BEFORE_CREDIT_PROVISIONS", "isb41": "IS_CREDIT_LOSS_PROVISION",
        }
        for field, canonical in expected.items():
            self.assertEqual(mapping[field], canonical, field)
        self.assertIn("BANK_NON_INTEREST_INCOME_SHARE", METRIC_FORMULAS)
        self.assertIn("BANK_PPOP_MARGIN", METRIC_FORMULAS)
        self.assertEqual(mapping["isb39"], "IS_OPERATING_EXPENSE_BANK")

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
