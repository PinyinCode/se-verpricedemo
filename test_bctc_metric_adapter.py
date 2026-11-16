# -*- coding: utf-8 -*-
import unittest

from bctc_metric_adapter import (
    build_point_in_time_metric_record,
    build_ttm_metric_record,
    extract_quarter_series,
    extract_source_value,
)


class TestBctcMetricAdapter(unittest.TestCase):
    def test_exact_source_field_period_extraction(self):
        rows = [
            {"source_field": "bsb104", "period": "2026-Q3", "value": 100, "unit": "VND"},
            {"source_field": "bsb105", "period": "2026-Q3", "value": -5, "unit": "VND"},
        ]
        result = extract_source_value(rows, "bsb104", "2026-Q3")
        self.assertTrue(result["available"])
        self.assertEqual(result["value"], 100)
        self.assertFalse(extract_source_value(rows, "bsb104", "2026-Q2")["available"])

    def test_conflicting_duplicate_and_unit_conflict_block_extraction(self):
        rows = [
            {"source_field": "isa20", "period": "2026-Q3", "value": 100, "unit": "VND"},
            {"source_field": "isa20", "period": "2026-Q3", "value": 101, "unit": "VND"},
        ]
        self.assertEqual(
            extract_source_value(rows, "isa20", "2026-Q3")["reason"],
            "conflicting_duplicate_source_period",
        )
        rows[1]["value"] = 100
        rows[1]["unit"] = "USD"
        self.assertEqual(
            extract_source_value(rows, "isa20", "2026-Q3")["reason"],
            "source_unit_missing_or_conflicting",
        )

    def test_quarterly_ttm_sum_requires_four_consecutive_periods(self):
        periods = ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3"]
        rows = [
            {"source_field": "isa20", "period": period, "value": value, "unit": "VND"}
            for period, value in zip(periods, [40, 10, 20, 30])
        ]
        series = extract_quarter_series(rows, "isa20", periods, "QUARTERLY")
        self.assertTrue(series["available"])
        record = build_ttm_metric_record(
            "NET_PROFIT_TTM", rows, "isa20", periods, "QUARTERLY"
        )
        self.assertTrue(record["available"])
        self.assertEqual(record["value"], 100)
        self.assertEqual(record["period_basis"], "TTM")

    def test_ytd_values_are_quarterized_before_ttm(self):
        periods = ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3"]
        ytd = {
            "2025-Q3": 80, "2025-Q4": 120,
            "2026-Q1": 20, "2026-Q2": 50, "2026-Q3": 90,
        }
        rows = [
            {"source_field": "cfa18", "period": period, "value": value, "unit": "VND"}
            for period, value in ytd.items()
        ]
        series = extract_quarter_series(rows, "cfa18", periods, "YTD")
        self.assertTrue(series["available"], series.get("reason"))
        self.assertEqual(
            series["values_by_period"],
            {"2025-Q4": 40, "2026-Q1": 20, "2026-Q2": 30, "2026-Q3": 40},
        )
        record = build_ttm_metric_record(
            "CFO_TTM", rows, "cfa18", periods, "YTD"
        )
        self.assertEqual(record["value"], 130)


    def test_ytd_quarterization_for_ttm_starting_in_q2_requires_prior_q1(self):
        periods = ["2025-Q2", "2025-Q3", "2025-Q4", "2026-Q1"]
        ytd = {
            "2025-Q1": 10, "2025-Q2": 30,
            "2025-Q3": 60, "2025-Q4": 100, "2026-Q1": 20,
        }
        rows = [
            {"source_field": "isa20", "period": period, "value": value, "unit": "VND"}
            for period, value in ytd.items()
        ]
        series = extract_quarter_series(rows, "isa20", periods, "YTD")
        self.assertTrue(series["available"], series.get("reason"))
        self.assertEqual(
            series["values_by_period"],
            {"2025-Q2": 20, "2025-Q3": 30, "2025-Q4": 40, "2026-Q1": 20},
        )

    def test_ytd_quarterization_refuses_missing_prior_ytd(self):
        rows = [
            {"source_field": "cfa18", "period": "2026-Q2", "value": 50, "unit": "VND"},
        ]
        result = extract_quarter_series(
            rows, "cfa18", ["2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"], "YTD"
        )
        self.assertFalse(result["available"])

    def test_point_in_time_record_retains_source_provenance(self):
        rows = [
            {"source_field": "bsb113", "period": "2026-Q3", "value": 500, "unit": "VND"},
        ]
        record = build_point_in_time_metric_record(
            "CUSTOMER_DEPOSITS", rows, "bsb113", "2026-Q3"
        )
        self.assertTrue(record["available"])
        self.assertEqual(record["source_fields"], ["bsb113"])
        self.assertEqual(record["period_basis"], "POINT_IN_TIME")



if __name__ == "__main__":
    unittest.main()
