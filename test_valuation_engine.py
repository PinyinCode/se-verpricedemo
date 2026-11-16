# -*- coding: utf-8 -*-
import unittest

from valuation_engine import (
    calculate_dcf_value,
    calculate_pb_value,
    calculate_pe_value,
    compare_to_market_price,
)


def verified(value, basis, source):
    return {
        "value": value, "available": True, "quality_status": "verified",
        "period_basis": basis, "source_fields": [source],
        "unit": "VND" if basis in ("ANNUAL_FORECAST", "POINT_IN_TIME") else "VND/share",
        "period": "2027-FY" if basis == "ANNUAL_FORECAST" else ("2026-Q3_TTM" if basis == "TTM" else "2026-Q3"),
    }


class TestDeterministicValuation(unittest.TestCase):
    def test_pe_requires_verified_ttm_eps_and_explicit_multiple(self):
        result = calculate_pe_value(verified(5, "TTM", "isa22"), 10)
        self.assertTrue(result["available"])
        self.assertEqual(result["value_per_share"], 50)
        self.assertFalse(calculate_pe_value(
            verified(5, "YTD", "isa22"), 10
        )["available"])
        self.assertFalse(calculate_pe_value(
            verified(5, "TTM", "isa22"), None
        )["available"])

    def test_pb_requires_point_in_time_book_value(self):
        result = calculate_pb_value(
            dict(verified(20, "POINT_IN_TIME", "equity_per_share"), unit="VND/share"), 1.5
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["value_per_share"], 30)
        self.assertFalse(calculate_pb_value(
            verified(20, "TTM", "equity_per_share"), 1.5
        )["available"])

    def test_dcf_requires_all_explicit_verified_inputs(self):
        result = calculate_dcf_value(
            [dict(verified(100, "ANNUAL_FORECAST", "fcf_y1"), period="2027-FY"),
             dict(verified(110, "ANNUAL_FORECAST", "fcf_y2"), period="2028-FY")],
            0.10, 0.03,
            verified(50, "POINT_IN_TIME", "net_debt"),
            dict(verified(10, "POINT_IN_TIME", "diluted_shares"), unit="shares"),
        )
        self.assertTrue(result["available"])
        self.assertGreater(result["value_per_share"], 0)
        self.assertIn("fcf_y1", result["source_fields"])

    def test_dcf_rejects_terminal_growth_at_or_above_discount_rate(self):
        result = calculate_dcf_value(
            [dict(verified(100, "ANNUAL_FORECAST", "fcf_y1"), period="2027-FY")],
            0.08, 0.08,
            verified(0, "POINT_IN_TIME", "net_debt"),
            dict(verified(10, "POINT_IN_TIME", "shares"), unit="shares"),
        )
        self.assertFalse(result["available"])
        self.assertIn("terminal_growth_below_discount_rate", result["reason"])


    def test_dcf_rejects_nonconsecutive_forecast_years(self):
        result = calculate_dcf_value(
            [dict(verified(100, "ANNUAL_FORECAST", "fcf_y1"), period="2027-FY"),
             dict(verified(110, "ANNUAL_FORECAST", "fcf_y3"), period="2029-FY")],
            0.10, 0.03,
            verified(50, "POINT_IN_TIME", "net_debt"),
            dict(verified(10, "POINT_IN_TIME", "shares"), unit="shares"),
        )
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "forecast_years_not_consecutive")

    def test_dcf_rejects_unverified_forecast(self):
        bad = verified(100, "ANNUAL_FORECAST", "fcf_y1")
        bad["quality_status"] = "estimated_without_approval"
        result = calculate_dcf_value(
            [dict(bad, period="2027-FY")], 0.1, 0.02,
            verified(0, "POINT_IN_TIME", "net_debt"),
            verified(10, "POINT_IN_TIME", "shares"),
        )
        self.assertFalse(result["available"])

    def test_price_gap_is_neutral_and_requires_positive_values(self):
        valuation = calculate_pe_value(verified(5, "TTM", "eps"), 10)
        gap = compare_to_market_price(valuation, 40)
        self.assertEqual(gap["discount_pct"], 20)
        self.assertFalse(compare_to_market_price(valuation, 0)["available"])


if __name__ == "__main__":
    unittest.main()
