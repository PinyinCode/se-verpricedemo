# -*- coding: utf-8 -*-
import unittest

from bank_metrics import (
    calculate_bank_cir,
    calculate_bank_ldr,
    calculate_bank_ppop_margin,
    get_unverified_bank_metrics,
    validate_bank_loan_identity,
)


def rows(values):
    return [{"source_field": key, "value": value} for key, value in values.items()]


class TestBankMetrics(unittest.TestCase):
    def test_cir_uses_absolute_negative_expense(self):
        result = calculate_bank_cir(rows({"isb38": 100.0, "isb39": -60.0}), True)
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["value"], 60.0)

    def test_cir_requires_issuer_verification(self):
        result = calculate_bank_cir(rows({"isb38": 100.0, "isb39": -60.0}))
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "issuer_mapping_not_verified")

    def test_cir_rejects_nonpositive_income(self):
        self.assertEqual(
            calculate_bank_cir(rows({"isb38": 0, "isb39": -5}), True)["reason"],
            "non_positive_operating_income",
        )

    def test_ppop_margin(self):
        result = calculate_bank_ppop_margin(rows({"isb38": 100, "isb40": 35}), True)
        self.assertAlmostEqual(result["value"], 35.0)

    def test_loan_identity_accepts_signed_provision(self):
        result = validate_bank_loan_identity(
            rows({"bsb103": 90, "bsb104": 100, "bsb105": -10}), True
        )
        self.assertTrue(result["valid"])

    def test_loan_identity_rejects_wrong_sign_or_value(self):
        result = validate_bank_loan_identity(
            rows({"bsb103": 110, "bsb104": 100, "bsb105": -10}), True
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "loan_identity_mismatch")

    def test_conflicting_duplicates_are_rejected(self):
        data = rows({"isb38": 100, "isb39": -50})
        data.append({"source_field": "isb39", "value": -55})
        self.assertEqual(
            calculate_bank_cir(data, True)["reason"],
            "conflicting_duplicate_source_field",
        )

    def test_ldr_uses_gross_loans_and_customer_deposits(self):
        result = calculate_bank_ldr(rows({"bsb104": 120, "bsb113": 150}), True)
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["value"], 80.0)

    def test_ldr_requires_issuer_verification(self):
        result = calculate_bank_ldr(rows({"bsb104": 120, "bsb113": 150}))
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "issuer_mapping_not_verified")

    def test_ldr_rejects_nonpositive_deposits(self):
        result = calculate_bank_ldr(rows({"bsb104": 120, "bsb113": 0}), True)
        self.assertEqual(result["reason"], "non_positive_customer_deposits")

    def test_ldr_rejects_conflicting_deposit_rows(self):
        data = rows({"bsb104": 120, "bsb113": 150})
        data.append({"source_field": "bsb113", "value": 151})
        self.assertEqual(
            calculate_bank_ldr(data, True)["reason"],
            "conflicting_duplicate_source_field",
        )

    def test_unverified_metrics_are_explicit(self):
        gaps = get_unverified_bank_metrics()
        self.assertIn("NPL_RATIO", gaps)
        self.assertIn("CAR", gaps)
        gaps.clear()
        self.assertIn("NPL_RATIO", get_unverified_bank_metrics())


if __name__ == "__main__":
    unittest.main()
