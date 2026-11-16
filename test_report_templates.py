# -*- coding: utf-8 -*-
import unittest

from report_templates import render_analysis_report


class TestDeterministicReportTemplate(unittest.TestCase):
    def test_report_contains_data_readiness_and_valuation(self):
        report = render_analysis_report({
            "status": "analysis_ready", "score": 35,
            "coverage_pct": 80, "valuation_method": "PE",
            "valuation_comparison": {
                "available": True, "fair_value_per_share": 100,
                "market_price": 80, "discount_pct": 20,
            },
            "blocking_reasons": [],
        })
        self.assertIn("Đủ điều kiện phân tích định lượng", report["text"])
        self.assertIn("20.00%", report["text"])
        self.assertIn("không phải chỉ dẫn mua", report["text"])

    def test_missing_data_is_not_rendered_as_zero(self):
        report = render_analysis_report({
            "status": "insufficient_data",
            "score": None, "coverage_pct": None,
            "blocking_reasons": ["bctc_not_verified"],
        })
        self.assertIn("Chưa có dữ liệu", report["text"])
        self.assertNotIn("Điểm nền tảng: 0.00", report["text"])

    def test_unavailable_metrics_are_listed_deterministically(self):
        report = render_analysis_report(
            {"status": "insufficient_data", "blocking_reasons": []},
            {"metric_results": {
                "ROE": {"available": False, "reason": "missing_equity"},
                "ROA": {"available": False, "reason": "missing_assets"},
            }},
        )
        self.assertLess(report["text"].index("ROA"), report["text"].index("ROE"))


if __name__ == "__main__":
    unittest.main()
