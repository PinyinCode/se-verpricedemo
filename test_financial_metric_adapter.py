# -*- coding: utf-8 -*-
import unittest

from financial_metric_adapter import (
    build_bank_credit_growth_record,
    build_bank_metric_bundle,
    build_average_balance_turnover_record,
    build_interest_coverage_record,
    build_net_debt_to_ebitda_record,
    build_free_cash_flow_record,
    build_industrial_metric_bundle,
    build_bank_ldr_record,
    build_cash_conversion_record,
    build_return_metric_record,
    quarantine_metrics,
    build_ttm_ratio_record,
)


PERIODS = ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3"]


def rows(field, values, unit="VND"):
    return [
        {"source_field": field, "period": period, "value": value, "unit": unit}
        for period, value in zip(PERIODS, values)
    ]


class TestFinancialMetricAdapter(unittest.TestCase):

    def test_quality_failure_quarantines_metrics_without_mutating_original(self):
        original = {
            "ROE": {
                "value": 12, "available": True,
                "quality_status": "verified", "reason": "",
            },
            "ROA": {
                "value": 2, "available": True,
                "quality_status": "verified", "reason": "",
            },
        }
        blocked = quarantine_metrics(
            original, ["ROE"], "balance_sheet_quality_validation_failed"
        )
        self.assertFalse(blocked["ROE"]["available"])
        self.assertIsNone(blocked["ROE"]["value"])
        self.assertEqual(
            blocked["ROE"]["reason"],
            "balance_sheet_quality_validation_failed",
        )
        self.assertTrue(original["ROE"]["available"])
        self.assertTrue(blocked["ROA"]["available"])

    def test_bank_ldr_and_credit_growth_require_issuer_mapping_verification(self):
        balance = [
            {"source_field": "bsb104", "period": "2026-Q3", "value": 600, "unit": "VND"},
            {"source_field": "bsb113", "period": "2026-Q3", "value": 1000, "unit": "VND"},
            {"source_field": "bsb104", "period": "2025-Q3", "value": 500, "unit": "VND"},
        ]
        ldr = build_bank_ldr_record(balance, "2026-Q3", True)
        growth = build_bank_credit_growth_record(
            balance, "2026-Q3", "2025-Q3", True
        )
        self.assertTrue(ldr["available"])
        self.assertEqual(ldr["value"], 60)
        self.assertTrue(growth["available"])
        self.assertEqual(growth["value"], 20)
        self.assertFalse(
            build_bank_ldr_record(balance, "2026-Q3", False)["available"]
        )

    def test_ttm_cir_normalizes_negative_expense_and_checks_sign(self):
        income = rows("isb39", [-5, -5, -5, -5])
        operating_income = rows("isb38", [20, 20, 20, 20])
        result = build_ttm_ratio_record(
            "CIR", income, "isb39", operating_income, "isb38",
            PERIODS, "QUARTERLY", 100, "negative_expense",
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["value"], 25)
        wrong_sign = rows("isb39", [5, 5, 5, 5])
        blocked = build_ttm_ratio_record(
            "CIR", wrong_sign, "isb39", operating_income, "isb38",
            PERIODS, "QUARTERLY", 100, "negative_expense",
        )
        self.assertFalse(blocked["available"])
        self.assertEqual(blocked["reason"], "expense_sign_mismatch")

    def test_ppop_margin_uses_same_ttm_basis(self):
        ppop = rows("isb40", [15, 15, 15, 15])
        income = rows("isb38", [20, 20, 20, 20])
        result = build_ttm_ratio_record(
            "BANK_PPOP_MARGIN", ppop, "isb40", income, "isb38",
            PERIODS, "QUARTERLY",
        )
        self.assertEqual(result["value"], 75)

    def test_roe_and_roa_use_average_comparable_balances(self):
        profits = rows("isa20", [8, 8, 8, 8])
        assets_profit = rows("isa20", [8, 8, 8, 8])
        equity = [
            {"source_field": "bsa78", "period": "2025-Q3", "value": 400, "unit": "VND"},
            {"source_field": "bsa78", "period": "2026-Q3", "value": 500, "unit": "VND"},
        ]
        assets = [
            {"source_field": "bsa53", "period": "2025-Q3", "value": 800, "unit": "VND"},
            {"source_field": "bsa53", "period": "2026-Q3", "value": 1000, "unit": "VND"},
        ]
        roe = build_return_metric_record(
            "ROE", profits, "isa20", equity, "bsa78", PERIODS,
            "2026-Q3", "2025-Q3", "QUARTERLY", "ROE",
        )
        roa = build_return_metric_record(
            "ROA", assets_profit, "isa20", assets, "bsa53", PERIODS,
            "2026-Q3", "2025-Q3", "QUARTERLY", "ROA",
        )
        self.assertAlmostEqual(roe["value"], 7.1111, places=4)
        self.assertAlmostEqual(roa["value"], 3.5556, places=4)


    def test_bank_metric_bundle_produces_verified_metrics_and_keeps_data_gaps_unavailable(self):
        balance = [
            {"source_field": "bsb104", "period": "2026-Q3", "value": 600, "unit": "VND"},
            {"source_field": "bsb104", "period": "2025-Q3", "value": 500, "unit": "VND"},
            {"source_field": "bsb113", "period": "2026-Q3", "value": 1000, "unit": "VND"},
            {"source_field": "bsa78", "period": "2026-Q3", "value": 500, "unit": "VND"},
            {"source_field": "bsa78", "period": "2025-Q3", "value": 400, "unit": "VND"},
            {"source_field": "bsa53", "period": "2026-Q3", "value": 1000, "unit": "VND"},
            {"source_field": "bsa53", "period": "2025-Q3", "value": 800, "unit": "VND"},
        ]
        income = []
        for field, values in (
            ("isa20", [8, 8, 8, 8]),
            ("isb39", [-5, -5, -5, -5]),
            ("isb38", [20, 20, 20, 20]),
            ("isb40", [15, 15, 15, 15]),
            ("isb27", [5, 5, 5, 5]),
        ):
            income.extend(rows(field, values))
        # Parent-attributable profit differs; ROE must use consolidated isa20
        # because bsa78 is total owners equity, not a verified parent-only balance.
        income.extend(rows("isa22", [10, 10, 10, 10]))
        bundle = build_bank_metric_bundle(
            balance, income, "2026-Q3", "2025-Q3",
            PERIODS, "QUARTERLY", True,
        )
        self.assertAlmostEqual(bundle["ROE"]["value"], 7.1111, places=4)
        self.assertAlmostEqual(bundle["ROA"]["value"], 3.5556, places=4)
        self.assertEqual(bundle["LDR"]["value"], 60)
        self.assertEqual(bundle["CREDIT_GROWTH"]["value"], 20)
        self.assertEqual(bundle["CIR"]["value"], 25)
        self.assertEqual(bundle["BANK_PPOP_MARGIN"]["value"], 75)
        self.assertEqual(bundle["BANK_NON_INTEREST_INCOME_SHARE"]["value"], 75)
        for metric_id in ("NIM", "NPL_RATIO", "GROUP_2_RATIO",
                          "NPL_COVERAGE", "CAR", "CASA"):
            with self.subTest(metric=metric_id):
                self.assertFalse(bundle[metric_id]["available"])

    def test_bank_metric_bundle_quarantines_unverified_issuer_mappings(self):
        balance = [
            {"source_field": "bsb104", "period": "2026-Q3", "value": 600, "unit": "VND"},
            {"source_field": "bsb113", "period": "2026-Q3", "value": 1000, "unit": "VND"},
            {"source_field": "bsb104", "period": "2025-Q3", "value": 500, "unit": "VND"},
        ]
        income = []
        for field, values in (
            ("isb39", [-5, -5, -5, -5]),
            ("isb38", [20, 20, 20, 20]),
            ("isb40", [15, 15, 15, 15]),
            ("isb27", [5, 5, 5, 5]),
        ):
            income.extend(rows(field, values))
        bundle = build_bank_metric_bundle(
            balance, income, "2026-Q3", "2025-Q3",
            PERIODS, "QUARTERLY", False,
        )
        for metric_id in ("LDR", "CREDIT_GROWTH", "CIR",
                          "BANK_PPOP_MARGIN", "BANK_NON_INTEREST_INCOME_SHARE"):
            with self.subTest(metric=metric_id):
                self.assertFalse(bundle[metric_id]["available"])
                self.assertEqual(bundle[metric_id]["reason"],
                                 "issuer_mapping_not_verified")


    def test_industrial_metric_bundle_calculates_core_metrics_and_quarantines_gaps(self):
        equity = [
            {"source_field": "bsa78", "period": "2025-Q3", "value": 400, "unit": "VND"},
            {"source_field": "bsa78", "period": "2026-Q3", "value": 500, "unit": "VND"},
        ]
        assets = [
            {"source_field": "bsa53", "period": "2025-Q3", "value": 800, "unit": "VND"},
            {"source_field": "bsa53", "period": "2026-Q3", "value": 1000, "unit": "VND"},
        ]
        income = rows("isa20", [8, 8, 8, 8])
        cashflow = rows("cfa18", [15, 15, 15, 15])
        bundle = build_industrial_metric_bundle(
            equity + assets, income, cashflow,
            "2026-Q3", "2025-Q3", PERIODS,
            "QUARTERLY", "QUARTERLY",
        )
        self.assertAlmostEqual(bundle["ROE"]["value"], 7.1111, places=4)
        self.assertAlmostEqual(bundle["ROA"]["value"], 3.5556, places=4)
        self.assertEqual(bundle["CASH_CONVERSION"]["value"], 1.875)
        self.assertTrue(bundle["CFO"]["available"])
        self.assertEqual(bundle["CFO"]["value"], 60)
        self.assertEqual(bundle["CFO"]["period_basis"], "TTM")
        for metric_id in ("ROIC", "INVENTORY_TURNOVER",
                          "INTEREST_COVERAGE", "NET_DEBT_TO_EBITDA", "FCF"):
            with self.subTest(metric=metric_id):
                self.assertFalse(bundle[metric_id]["available"])


    def test_inventory_turnover_uses_ttm_cost_and_average_inventory(self):
        costs = rows("isa5", [-100, -100, -100, -100])
        inventory = [
            {"source_field": "bsa12", "period": "2025-Q3", "value": 200, "unit": "VND"},
            {"source_field": "bsa12", "period": "2026-Q3", "value": 300, "unit": "VND"},
        ]
        result = build_average_balance_turnover_record(
            "INVENTORY_TURNOVER", costs, "isa5", inventory, "bsa12",
            PERIODS, "2026-Q3", "2025-Q3", "QUARTERLY",
            "negative_expense", True,
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["value"], 1.6)
        blocked = build_average_balance_turnover_record(
            "INVENTORY_TURNOVER", rows("isa5", [100, 100, 100, 100]),
            "isa5", inventory, "bsa12", PERIODS,
            "2026-Q3", "2025-Q3", "QUARTERLY",
            "negative_expense", True,
        )
        self.assertFalse(blocked["available"])
        self.assertEqual(blocked["reason"], "expense_sign_mismatch")

    def test_interest_coverage_requires_positive_ebit_and_negative_interest_expense(self):
        income = rows("isa3", [20, 20, 20, 20]) + rows("isa4", [-5, -5, -5, -5])
        result = build_interest_coverage_record(
            income, "isa3", "isa4", PERIODS, "QUARTERLY", True
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["value"], 4.0)
        wrong_sign = rows("isa3", [20, 20, 20, 20]) + rows("isa4", [5, 5, 5, 5])
        blocked = build_interest_coverage_record(
            wrong_sign, "isa3", "isa4", PERIODS, "QUARTERLY", True
        )
        self.assertFalse(blocked["available"])
        self.assertEqual(blocked["reason"], "interest_expense_sign_mismatch")

    def test_net_debt_to_ebitda_checks_all_balance_units_and_positive_ebitda(self):
        balance = [
            {"source_field": "bsa21", "period": "2026-Q3", "value": 100, "unit": "VND"},
            {"source_field": "bsa22", "period": "2026-Q3", "value": 200, "unit": "VND"},
            {"source_field": "bsa1", "period": "2026-Q3", "value": 50, "unit": "VND"},
        ]
        ebitda = rows("isa6", [25, 25, 25, 25])
        result = build_net_debt_to_ebitda_record(
            balance, ebitda, "bsa21", "bsa22", "bsa1", "isa6",
            "2026-Q3", PERIODS, "QUARTERLY", True,
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["value"], 2.5)
        loss = rows("isa6", [-25, -25, -25, -25])
        blocked = build_net_debt_to_ebitda_record(
            balance, loss, "bsa21", "bsa22", "bsa1", "isa6",
            "2026-Q3", PERIODS, "QUARTERLY", True,
        )
        self.assertFalse(blocked["available"])
        self.assertEqual(blocked["reason"], "ebitda_not_positive")

    def test_fcf_requires_explicit_capex_sign_and_matching_units(self):
        cashflow = rows("cfa18", [20, 20, 20, 20]) + rows("cfa19", [-5, -5, -5, -5])
        result = build_free_cash_flow_record(
            cashflow, "cfa18", "cfa19", PERIODS, "QUARTERLY",
            "negative_outflow", True,
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["value"], 60)
        blocked = build_free_cash_flow_record(
            cashflow, "cfa18", "cfa19", PERIODS, "QUARTERLY",
            "positive_outflow", True,
        )
        self.assertFalse(blocked["available"])
        self.assertEqual(blocked["reason"], "capex_sign_mismatch")


    def test_ttm_adapters_handle_empty_periods_without_raising(self):
        cashflow = rows("cfa18", [20, 20, 20, 20])
        income = rows("isa20", [8, 8, 8, 8])
        conversion = build_cash_conversion_record(
            cashflow, income, [], "QUARTERLY", "QUARTERLY"
        )
        self.assertFalse(conversion["available"])
        self.assertEqual(conversion["period"], "TTM through unknown_period")
        fcf = build_free_cash_flow_record(
            cashflow, "cfa18", "cfa19", [], "QUARTERLY",
            "negative_outflow", True,
        )
        self.assertFalse(fcf["available"])
        self.assertEqual(fcf["period"], "TTM through unknown_period")

    def test_cash_conversion_requires_matching_ttm_and_positive_profit(self):
        cfo = rows("cfa18", [15, 15, 15, 15])
        profit = rows("isa20", [8, 8, 8, 8])
        result = build_cash_conversion_record(
            cfo, profit, PERIODS, "QUARTERLY", "QUARTERLY"
        )
        self.assertEqual(result["value"], 1.875)
        loss = rows("isa20", [-8, -8, -8, -8])
        blocked = build_cash_conversion_record(
            cfo, loss, PERIODS, "QUARTERLY", "QUARTERLY"
        )
        self.assertFalse(blocked["available"])
        self.assertEqual(blocked["reason"], "net_profit_not_positive")



    def test_return_metrics_reject_wrong_catalog_source_or_metric_identity(self):
        income = rows("isa20", [8, 8, 8, 8])
        equity = [
            {"source_field": "bsa78", "period": "2025-Q3", "value": 400, "unit": "VND"},
            {"source_field": "bsa78", "period": "2026-Q3", "value": 500, "unit": "VND"},
        ]
        wrong_source = build_return_metric_record(
            "ROE", income, "isa20", equity, "bsa53", PERIODS,
            "2026-Q3", "2025-Q3", "QUARTERLY", "ROE",
        )
        wrong_identity = build_return_metric_record(
            "ROE", income, "isa20", equity, "bsa78", PERIODS,
            "2026-Q3", "2025-Q3", "QUARTERLY", "ROA",
        )
        self.assertFalse(wrong_source["available"])
        self.assertEqual(wrong_source["reason"], "metric_source_lineage_mismatch")
        self.assertFalse(wrong_identity["available"])
        self.assertEqual(wrong_identity["reason"], "return_metric_identity_mismatch")

    def test_return_metrics_reject_period_window_not_ending_at_current_period(self):
        income = rows("isa20", [8, 8, 8, 8])
        equity = [
            {"source_field": "bsa78", "period": "2025-Q3", "value": 400, "unit": "VND"},
            {"source_field": "bsa78", "period": "2026-Q3", "value": 500, "unit": "VND"},
        ]
        result = build_return_metric_record(
            "ROE", income, "isa20", equity, "bsa78",
            ["2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2"],
            "2026-Q3", "2025-Q3", "QUARTERLY", "ROE",
        )
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "current_period_not_ttm_end")

    def test_return_metrics_reject_balance_period_not_same_quarter_prior_year(self):
        income = rows("isa20", [8, 8, 8, 8])
        equity = [
            {"source_field": "bsa78", "period": "2025-Q2", "value": 400, "unit": "VND"},
            {"source_field": "bsa78", "period": "2026-Q3", "value": 500, "unit": "VND"},
        ]
        result = build_return_metric_record(
            "ROE", income, "isa20", equity, "bsa78", PERIODS,
            "2026-Q3", "2025-Q2", "QUARTERLY", "ROE",
        )
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "balance_periods_not_same_quarter_year_apart")


if __name__ == "__main__":
    unittest.main()
