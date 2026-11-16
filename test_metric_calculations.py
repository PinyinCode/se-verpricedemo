# -*- coding: utf-8 -*-
import unittest

from metric_calculations import (
    calculate_growth,
    calculate_bank_ldr,
    calculate_bank_cir,
    calculate_bank_ppop_margin,
    calculate_bank_non_interest_income_share,
    calculate_bank_npl_ratio,
    calculate_bank_group2_ratio,
    calculate_bank_npl_coverage,
    calculate_bank_credit_growth,
    calculate_bank_nim,
    calculate_margin,
    calculate_ttm_sum,
    calculate_yoy_growth,
    calculate_ytd_to_quarter,
    safe_ratio,
)


class TestSafeRatioAndMargins(unittest.TestCase):
    def test_margin_is_percentage(self):
        self.assertEqual(calculate_margin(25, 100)["value"], 25.0)

    def test_zero_revenue_is_unavailable_not_zero_margin(self):
        result = calculate_margin(25, 0)
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "denominator_not_positive")

    def test_missing_input_is_not_coerced_to_zero(self):
        self.assertFalse(safe_ratio(None, 100)["available"])

    def test_non_zero_denominator_policy_allows_negative_denominator(self):
        self.assertEqual(
            safe_ratio(10, -2, denominator_policy="non_zero")["value"], -5.0
        )


class TestGrowthCalculations(unittest.TestCase):
    def test_growth(self):
        self.assertEqual(calculate_growth(120, 100)["value"], 20.0)

    def test_negative_or_zero_base_is_unavailable(self):
        self.assertEqual(
            calculate_growth(10, -5)["reason"], "prior_period_not_positive"
        )

    def test_yoy_requires_same_quarter_one_year_apart(self):
        result = calculate_yoy_growth(
            120, "2026-Q3", 100, "2025-Q3", "QUARTERLY"
        )
        self.assertEqual(result["value"], 20.0)

    def test_yoy_rejects_non_quarterly_basis(self):
        result = calculate_yoy_growth(
            120, "2026-Q3", 100, "2025-Q3", "YTD"
        )
        self.assertFalse(result["available"])

    def test_yoy_rejects_wrong_comparison_period(self):
        result = calculate_yoy_growth(
            120, "2026-Q3", 100, "2025-Q2", "QUARTERLY"
        )
        self.assertFalse(result["available"])


class TestTTMCalculation(unittest.TestCase):
    def test_ttm_sums_four_consecutive_quarters(self):
        values = {
            "2025-Q4": 40, "2026-Q1": 10,
            "2026-Q2": 20, "2026-Q3": 30,
        }
        result = calculate_ttm_sum(
            values, list(values), "QUARTERLY"
        )
        self.assertEqual(result["value"], 100)

    def test_ttm_refuses_ytd_values(self):
        result = calculate_ttm_sum(
            {"2026-Q1": 10, "2026-Q2": 20, "2026-Q3": 30, "2026-Q4": 40},
            ["2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"],
            "YTD",
        )
        self.assertEqual(
            result["reason"], "period_basis_not_verified_as_quarterly"
        )

    def test_ttm_rejects_missing_quarter(self):
        result = calculate_ttm_sum(
            {"2025-Q4": 40, "2026-Q1": 10, "2026-Q3": 30},
            ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3"],
            "QUARTERLY",
        )
        self.assertEqual(result["reason"], "ttm_quarter_value_missing")

    def test_ttm_rejects_gap(self):
        result = calculate_ttm_sum(
            {"2025-Q4": 40, "2026-Q1": 10, "2026-Q2": 20, "2026-Q4": 40},
            ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q4"],
            "QUARTERLY",
        )
        self.assertEqual(
            result["reason"], "ttm_periods_not_unique_and_consecutive"
        )


class TestYtdConversion(unittest.TestCase):
    def test_q1_ytd_equals_q1_standalone(self):
        self.assertEqual(
            calculate_ytd_to_quarter(15, None, 1, "YTD")["value"], 15
        )

    def test_q2_ytd_minus_q1_ytd(self):
        self.assertEqual(
            calculate_ytd_to_quarter(40, 15, 2, "YTD")["value"], 25
        )

    def test_ytd_conversion_requires_verified_basis(self):
        result = calculate_ytd_to_quarter(40, 15, 2, "QUARTERLY")
        self.assertFalse(result["available"])


class TestBankMetricCalculations(unittest.TestCase):
    def test_ldr_uses_gross_loans_and_deposits(self):
        self.assertEqual(calculate_bank_ldr(120, 100)["value"], 120.0)

    def test_cir_normalizes_verified_negative_expense(self):
        self.assertEqual(calculate_bank_cir(-40, 100)["value"], 40.0)

    def test_cir_rejects_unexpected_positive_expense_sign(self):
        self.assertEqual(
            calculate_bank_cir(40, 100)["reason"], "expense_sign_mismatch"
        )

    def test_ppop_margin(self):
        self.assertEqual(calculate_bank_ppop_margin(35, 100)["value"], 35.0)

    def test_non_interest_income_share(self):
        self.assertEqual(
            calculate_bank_non_interest_income_share(100, 70)["value"], 30.0
        )

    def test_npl_ratio_requires_positive_gross_loans(self):
        self.assertEqual(calculate_bank_npl_ratio(3, 100)["value"], 3.0)
        self.assertFalse(calculate_bank_npl_ratio(3, 0)["available"])

    def test_group2_ratio_is_not_inferred(self):
        self.assertEqual(calculate_bank_group2_ratio(5, 100)["value"], 5.0)
        self.assertFalse(calculate_bank_group2_ratio(None, 100)["available"])

    def test_npl_coverage_normalizes_negative_provision(self):
        self.assertEqual(calculate_bank_npl_coverage(-6, 3)["value"], 200.0)

    def test_npl_coverage_rejects_wrong_sign(self):
        self.assertEqual(
            calculate_bank_npl_coverage(6, 3)["reason"],
            "provision_sign_mismatch",
        )

    def test_credit_growth_requires_same_quarter_year_apart(self):
        self.assertEqual(
            calculate_bank_credit_growth(120, "2026-Q3", 100, "2025-Q3")["value"],
            20.0,
        )
        self.assertFalse(
            calculate_bank_credit_growth(120, "2026-Q3", 100, "2025-Q2")["available"]
        )

    def test_nim_ttm_not_annualized_twice(self):
        self.assertEqual(calculate_bank_nim(10, 100, "TTM")["value"], 10.0)

    def test_nim_quarterly_is_annualized_four_times(self):
        self.assertEqual(
            calculate_bank_nim(2.5, 100, "QUARTERLY")["value"], 10.0
        )

    def test_nim_requires_explicit_period_basis(self):
        self.assertEqual(
            calculate_bank_nim(10, 100, "YTD")["reason"],
            "income_period_basis_not_verified",
        )


if __name__ == "__main__":
    unittest.main()
