# -*- coding: utf-8 -*-
import unittest

from data_quality import (
    consecutive_quarters,
    parse_quarter,
    reconcile_equations,
    validate_bctc_rows,
    validate_sign,
    validate_ttm_periods,
)


class TestQuarterValidation(unittest.TestCase):
    def test_parse_quarter(self):
        self.assertEqual(parse_quarter("2026-Q3"), (2026, 3))
        self.assertIsNone(parse_quarter("2026-Q5"))

    def test_consecutive_across_year_boundary(self):
        self.assertTrue(consecutive_quarters(["2025-Q4", "2026-Q1"]))
        self.assertFalse(consecutive_quarters(["2025-Q4", "2026-Q2"]))
        self.assertFalse(consecutive_quarters(["2026-Q1", "2026-Q1"]))

    def test_ttm_requires_four_consecutive_quarters(self):
        result = validate_ttm_periods(
            ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3"]
        )
        self.assertTrue(result["valid"])
        self.assertFalse(result["period_basis_verified"])

    def test_ttm_rejects_gaps(self):
        result = validate_ttm_periods(
            ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q4"]
        )
        self.assertFalse(result["valid"])


class TestSignAndReconciliation(unittest.TestCase):
    def test_sign_policy(self):
        self.assertIsNone(validate_sign(-10, "non_positive"))
        self.assertEqual(validate_sign(10, "non_positive"),
                         "unexpected_positive_value")

    def test_reconciliation_passes(self):
        result = reconcile_equations(
            {"net": 90, "gross": 100, "provision": -10},
            [{"id": "loan_net", "terms": {"gross": 1, "provision": 1},
              "result_field": "net"}],
        )
        self.assertEqual(result[0]["status"], "pass")

    def test_reconciliation_does_not_impute_missing_values(self):
        result = reconcile_equations(
            {"gross": 100},
            [{"id": "loan_net", "terms": {"gross": 1, "provision": 1},
              "result_field": "net"}],
        )
        self.assertEqual(result[0]["status"], "unavailable")


class TestBctcRows(unittest.TestCase):
    def test_conflicting_duplicate_rows_block_scoring(self):
        rows = [
            {"source_field": "bsb103", "period": "2026-Q3",
             "value": 100, "unit": "VND"},
            {"source_field": "bsb103", "period": "2026-Q3",
             "value": 101, "unit": "VND"},
        ]
        result = validate_bctc_rows(rows, "balance_sheet",
                                    period_basis="POINT_IN_TIME")
        self.assertFalse(result["valid"])
        self.assertIn("conflicting_duplicate_source_period",
                      [item["code"] for item in result["errors"]])

    def test_bank_loan_reconciliation_with_negative_provision(self):
        rows = [
            {"source_field": "bsb103", "period": "2026-Q3",
             "value": 90, "unit": "VND"},
            {"source_field": "bsb104", "period": "2026-Q3",
             "value": 100, "unit": "VND"},
            {"source_field": "bsb105", "period": "2026-Q3",
             "value": -10, "unit": "VND"},
        ]
        equation = {"id": "net_loan_equals_gross_plus_provision",
                    "terms": {"bsb104": 1, "bsb105": 1},
                    "result_field": "bsb103"}
        result = validate_bctc_rows(
            rows, "balance_sheet",
            sign_policies={"bsb105": "non_positive"},
            equations=[equation], period_basis="POINT_IN_TIME",
        )
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["reconciliations"][0]["status"], "pass")

    def test_unknown_fields_are_retained_as_warning_not_guessed(self):
        result = validate_bctc_rows(
            [{"source_field": "bsa999", "period": "2026-Q3",
              "value": 1, "unit": "VND"}],
            "balance_sheet", period_basis="POINT_IN_TIME",
        )
        self.assertTrue(result["valid"])
        self.assertEqual(result["unknown_field_count"], 1)
        self.assertFalse(result["scoreable"])

    def test_missing_period_basis_prevents_scoreable_status(self):
        result = validate_bctc_rows(
            [{"source_field": "bsa53", "period": "2026-Q3",
              "value": 100, "unit": "VND"}],
            "balance_sheet",
        )
        self.assertTrue(result["valid"])
        self.assertFalse(result["scoreable"])

    def test_required_field_missing_blocks(self):
        result = validate_bctc_rows(
            [], "balance_sheet", required_fields=["bsb104"],
            period_basis="POINT_IN_TIME",
        )
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main()
