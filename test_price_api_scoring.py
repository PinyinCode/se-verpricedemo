# -*- coding: utf-8 -*-
"""Runtime regression tests for the production scoring/valuation functions.

price_api.py starts a Flask/MongoDB service at import time, so these tests
compile only the pure production functions and their literal sector maps.
"""
import ast
import unittest
from pathlib import Path

import math
import re
import statistics
import threading
from datetime import datetime, timedelta, timezone


class _NP:
    isfinite = staticmethod(math.isfinite)
    median = staticmethod(statistics.median)


def load_production_functions():
    source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="price_api.py")
    wanted_assignments = {
        "FALLBACK_SECTOR_MAPPING",
        "SECTOR_MAPPING",
        "SUB_SECTOR_MAPPING",
        "SUB_SECTOR_CONFIG",
    }
    wanted_functions = {
        "_safe_float",
        "_has_reported_ratio",
        "_is_finite_number",
        "_finite_float",
        "_bctc_period_is_current",
        "_period_quarter_index",
        "_valid_income_statement_periods",
        "_are_consecutive_quarters",
        "extract_metrics_from_bctc_data",
        "_expected_latest_bctc_period",
        "_vn_now",
        "normalize_stock_data",
        "build_bctc_quarterly_trend",
        "build_verified_facts",
        "build_fallback_narrative",
        "_get_bctc_optional_value",
        "_build_bctc_selected_fields_pipeline",
        "_build_bctc_summary_pipeline",
        "_ytd_support_period",
        "_build_bctc_bank_metric_pipeline",
        "_build_bctc_bank_asset_quality_pipeline",
        "_bank_asset_quality_endpoint_allowed",
        "_build_bctc_industrial_metric_pipeline",
        "_build_bctc_industrial_discovery_pipeline",
        "_industrial_metric_endpoint_allowed",
        "_bank_issuer_mapping_verified",
        "_build_bctc_bank_income_pipeline",
        "get_sector",
        "get_sub_sector",
        "compute_sector_medians",
        "compute_fair_value_zone",
        "get_median_for_symbol",
        "is_symbol_classified",
        "parse_quarter",
        "compute_fair_value_zone",
        "compute_final_decision",
        "cap_fundamental_score",
        "is_valid_debt_to_equity",
        "_claim_screener_job",
        "_release_screener_job",
        "_safe_update_screener_result",
        "_build_screener_diagnostics_pipeline",
    }
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {
                target.id for target in node.targets
                if isinstance(target, ast.Name)
            }
            if names & wanted_assignments:
                nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            nodes.append(node)

    namespace = {"np": _NP, "re": re, "datetime": datetime, "timedelta": timedelta, "timezone": timezone, "SECTOR_NAMES": {}, "DECISION_TO_RECOMMENDATION": {}}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "price_api_scoring_extract.py", "exec"), namespace)
    return namespace


PROD = load_production_functions()





class TestVnDirectRatioAvailabilityPersistence(unittest.TestCase):
    def test_ratio_crawler_persists_explicit_roe_roa_availability(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        self.assertIn("ratio_available = {}", source)
        self.assertIn('ratio_available[key] = _is_finite_number(val)', source)
        self.assertIn('"roe_available": ratio_available.get("roe", False)', source)
        self.assertIn('"roa_available": ratio_available.get("roa", False)', source)


class TestScreenerDiagnosticsPipeline(unittest.TestCase):
    def test_version_endpoint_exposes_non_secret_release_and_scoring_mode(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/api/version")', source)
        self.assertIn('os.getenv("RENDER_GIT_COMMIT")', source)
        self.assertIn('"new_profile_engine_enabled": False', source)
        self.assertIn('"scoring_mode": "legacy_deterministic_with_sector_blueprint_metadata"', source)
        self.assertIn('"sector_profile_count": readiness.get("profile_count", 0)', source)

    def test_diagnostics_pipeline_groups_only_aggregated_scorecard_fields(self):
        pipeline = PROD["_build_screener_diagnostics_pipeline"]()
        self.assertEqual(len(pipeline), 2)
        group = pipeline[0]["$group"]
        self.assertEqual(group["_id"], {"$ifNull": ["$sub_sector", "UNCLASSIFIED"]})
        self.assertIn("symbol_count", group)
        self.assertIn("avg_fundamental_coverage_pct", group)
        self.assertIn("complete_scorecard_count", group)
        self.assertIn("partial_scorecard_count", group)
        self.assertIn("insufficient_scorecard_count", group)
        self.assertIn("current_bctc_count", group)
        self.assertIn("stale_bctc_count", group)
        self.assertIn("unverified_bctc_count", group)
        self.assertEqual(pipeline[1], {"$sort": {"_id": 1}})

    def test_worker_persists_bctc_freshness_and_period_provenance(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        self.assertIn('r["bctc_latest_income_period"] = nd_final.get("bctc_latest_income_period", "")', source)
        self.assertIn('r["bctc_expected_period"] = nd_final.get("bctc_expected_period", "")', source)
        self.assertIn('r["bctc_period_is_current"] = (', source)
        self.assertIn('"stale_bctc_count": total_stale_bctc', source)
        self.assertIn('"unverified_bctc_count": total_unverified_bctc', source)

    def test_admin_readiness_exposes_formula_contract_readiness(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        self.assertIn("assess_all_sector_formula_readiness", source)
        self.assertIn('"formula_readiness": formula_readiness', source)
        self.assertIn("formula_readiness = assess_all_sector_formula_readiness()", source)

    def test_diagnostics_endpoint_is_read_only_and_discloses_engine_mode(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/api/screener/diagnostics")', source)
        self.assertIn('"scoring_mode": "legacy_deterministic_with_sector_blueprint_metadata"', source)
        self.assertIn('"new_profile_engine_enabled": False', source)
        self.assertIn('maxTimeMS=5000', source)
        self.assertIn('"by_sector": by_sector', source)
        self.assertNotIn('request.args.get("symbol"', source[source.index('def api_screener_diagnostics'):source.index('# FIX BUG #10')])


class TestFallbackNarrativeMissingMetrics(unittest.TestCase):
    def test_fallback_does_not_claim_missing_financial_metrics_are_zero(self):
        stock = {
            "close": 100, "pe": 0, "pb": 0, "roe": 0, "roa": 0,
            "eps": 0, "cfo_latest": 0, "cfo_ttm": 0, "net_profit_ttm": 0,
            "debt_to_equity": 0, "gross_margin": 0, "dividend_yield": 0,
            "bctc_cfo_ttm_complete": False,
            "bctc_income_ttm_complete": False,
            "bctc_balance_sheet_valid": False,
            "fair_value_low": 0, "fair_value_high": 0,
            "valuation_verdict": "KHÔNG ĐỦ DỮ LIỆU",
        }
        screener = {
            "fundamental_score": 0, "valuation_score": 0,
            "technical_score": 0, "total_score": 0,
            "profit_growth_yoy": 0, "profit_growth_yoy_available": False,
            "final_decision": "UNMAPPED",
        }
        result = PROD["build_fallback_narrative"](
            "TEST", stock, screener, "UNMAPPED_SECTOR"
        )
        self.assertIn("ROE chưa xác minh", result["bctc"])
        self.assertIn("ROA chưa xác minh", result["bctc"])
        self.assertIn("Chất lượng lợi nhuận TTM: chưa đủ dữ liệu", result["bctc"])
        self.assertNotIn("ROE đạt 0.0%", result["bctc"])
        self.assertNotIn("ROA 0.0%", result["bctc"])
        self.assertNotIn("CFO/LNST chỉ 0.00x", result["bctc"])
        self.assertIn("Chưa đủ dữ liệu xác minh tăng trưởng LNST YoY", result["catalyst"])
        self.assertIn("Chưa có vùng giá được xác minh", result["price_zone"])

    def test_fallback_keeps_verified_negative_ratios_visible(self):
        stock = {
            "close": 100, "pe": 0, "pb": 1.2,
            "roe": -3.5, "roe_available": True,
            "roa": -0.8, "roa_available": True,
            "eps": 0, "cfo_ttm": -12, "cfo_latest": -12,
            "net_profit_ttm": -25, "debt_to_equity": 0.5,
            "gross_margin": 0, "dividend_yield": 0,
            "bctc_cfo_ttm_complete": True,
            "bctc_income_ttm_complete": True,
            "bctc_balance_sheet_valid": True,
            "fair_value_low": 0, "fair_value_high": 0,
            "valuation_verdict": "KHÔNG ĐỦ DỮ LIỆU",
        }
        screener = {
            "fundamental_score": 10, "valuation_score": 0,
            "technical_score": 0, "total_score": 10,
            "profit_growth_yoy": -20, "profit_growth_yoy_available": True,
            "final_decision": "UNMAPPED",
        }
        result = PROD["build_fallback_narrative"](
            "TEST", stock, screener, "NGAN_HANG"
        )
        self.assertIn("ROE -3.5%", result["bctc"])
        self.assertIn("ROA -0.8%", result["bctc"])
        self.assertIn("CFO TTM -12 tỷ", result["bctc"])
        self.assertIn("LNST TTM -25 tỷ", result["bctc"])
        self.assertIn("D/E chưa xác minh", result["bctc"])
        self.assertIn("LNST tăng -20.0% YoY", result["catalyst"])


class TestVerifiedFactsMissingMetrics(unittest.TestCase):
    def test_missing_profitability_and_ttm_metrics_are_not_shown_as_zero(self):
        stock = {
            "roe": 0, "roa": 0, "cfo_ttm": 0, "cfo_latest": 0,
            "net_profit_ttm": 0,
            "bctc_cfo_ttm_complete": False,
            "bctc_income_ttm_complete": False,
        }
        screener = {"final_decision": "THIẾU DỮ LIỆU", "total_score": 0}
        result = PROD["build_verified_facts"](
            "TEST", stock, screener, "TEST_SECTOR", {}
        )
        self.assertIn("ROE chưa xác minh", result)
        self.assertIn("ROA chưa xác minh", result)
        self.assertIn("CFO chưa đủ dữ liệu TTM", result)
        self.assertIn("LNST chưa đủ dữ liệu TTM", result)
        self.assertNotIn("ROE 0.0%", result)
        self.assertNotIn("ROA 0.0%", result)
        self.assertNotIn("CFO 0 tỷ", result)
        self.assertNotIn("LNST 0 tỷ", result)

    def test_verified_negative_profitability_and_cashflow_remain_visible(self):
        stock = {
            "roe": -3.5, "roe_available": True,
            "roa": -0.8, "roa_available": True,
            "cfo_ttm": -12, "cfo_latest": -12,
            "net_profit_ttm": -25,
            "bctc_cfo_ttm_complete": True,
            "bctc_income_ttm_complete": True,
        }
        screener = {"final_decision": "CẢNH BÁO", "total_score": 20}
        result = PROD["build_verified_facts"](
            "TEST", stock, screener, "TEST_SECTOR", {}
        )
        self.assertIn("ROE -3.5%", result)
        self.assertIn("ROA -0.8%", result)
        self.assertIn("CFO -12 tỷ", result)
        self.assertIn("LNST -25 tỷ", result)


class TestQuarterlyTrendMissingValues(unittest.TestCase):
    def test_missing_cfo_is_not_rendered_as_zero(self):
        report = {
            "income_statement": [
                {"id": "IS_NET_REVENUE", "period": "2026-Q3", "value": 100_000_000_000},
                {"id": "IS_NET_PROFIT_AFTER_TAX", "period": "2026-Q3", "value": 10_000_000_000},
            ],
            "cash_flow": [],
        }
        result = PROD["build_bctc_quarterly_trend"](report)
        self.assertIn("Doanh thu 100.00", result)
        self.assertIn("LNST 10.00", result)
        self.assertIn("CFO không đủ dữ liệu", result)
        self.assertNotIn("CFO 0.00", result)

    def test_missing_revenue_and_profit_are_not_rendered_as_zero(self):
        report = {
            "income_statement": [
                {"id": "IS_NET_REVENUE", "period": "2026-Q3", "value": None},
                {"id": "IS_NET_PROFIT_AFTER_TAX", "period": "2026-Q3", "value": None},
            ],
            "cash_flow": [],
        }
        result = PROD["build_bctc_quarterly_trend"](report)
        self.assertIn("Không có dữ liệu doanh thu/lợi nhuận theo quý", result)
        self.assertNotIn("Doanh thu 0.00", result)
        self.assertNotIn("LNST 0.00", result)

    def test_non_core_period_is_excluded_from_quarterly_trend(self):
        report = {
            "income_statement": [
                {"id": "IS_OTHER_EXPENSE", "period": "2026-Q3", "value": 5_000_000},
            ],
            "cash_flow": [],
        }
        result = PROD["build_bctc_quarterly_trend"](report)
        self.assertIn("Không có dữ liệu doanh thu/lợi nhuận theo quý", result)


class TestValidIncomeStatementPeriods(unittest.TestCase):
    def test_future_period_with_only_non_core_rows_does_not_advance_freshness(self):
        rows = [
            {"id": "IS_NET_PROFIT_AFTER_TAX", "period": "2026-Q2", "value": 125},
            {"id": "IS_OTHER_EXPENSE", "period": "2026-Q3", "value": 20},
        ]
        self.assertEqual(
            PROD["_valid_income_statement_periods"](rows),
            ["2026-Q2"],
        )

    def test_period_with_resolvable_revenue_is_a_valid_income_period(self):
        rows = [
            {"id": "IS_NET_REVENUE", "period": "2026-Q3", "value": 500},
            {"id": "IS_OTHER_EXPENSE", "period": "2026-Q4", "value": 15},
        ]
        self.assertEqual(
            PROD["_valid_income_statement_periods"](rows),
            ["2026-Q3"],
        )

    def test_ambiguous_or_invalid_core_rows_do_not_count_as_valid_periods(self):
        rows = [
            {"id": "IS_NET_PROFIT_AFTER_TAX", "period": "2026-Q3", "value": "bad"},
            {"id": "IS_NET_REVENUE", "period": "2026-Q3", "value": float("nan")},
        ]
        self.assertEqual(PROD["_valid_income_statement_periods"](rows), [])
    def test_auto_refresh_uses_income_statement_period_not_global_bctc_period(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        self.assertIn("latest_income_period = None", source)
        self.assertIn('if report_name == "income_statement":', source)
        self.assertIn("_valid_income_statement_periods(records)", source)
        self.assertIn(
            "valid_income_periods[0] if valid_income_periods else None",
            source,
        )
        self.assertIn(
            'reports["latest_income_period"] = latest_income_period or "Chưa xác định"',
            source,
        )
        self.assertIn(
            '{"updated_at": 1, "latest_period": 1, "latest_income_period": 1}',
            source,
        )
        self.assertIn(
            'latest_period = str(bctc_doc.get("latest_income_period") or "")',
            source,
        )
        self.assertNotIn(
            'latest_period = str(bctc_doc.get("latest_period") or "")',
            source,
        )



class TestBctcPeriodFreshness(unittest.TestCase):
    def test_screener_exposes_period_and_both_decision_passes_use_freshness(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        self.assertIn('"bctc_latest_income_period": nd.get("bctc_latest_income_period", "")', source)
        self.assertIn('bctc_period_is_current=nd.get("bctc_period_is_current", False)', source)
        self.assertIn('bctc_period_is_current=nd_final.get("bctc_period_is_current", False)', source)
        self.assertIn("BCTC CHƯA CẬP NHẬT", source)

    def test_expected_quarter_rolls_forward_on_reporting_calendar(self):
        cases = (
            (datetime(2026, 3, 31), "2025-Q3"),
            (datetime(2026, 4, 1), "2025-Q4"),
            (datetime(2026, 5, 1), "2026-Q1"),
            (datetime(2026, 8, 1), "2026-Q2"),
            (datetime(2026, 11, 1), "2026-Q3"),
        )
        for now, expected in cases:
            with self.subTest(now=now):
                self.assertEqual(PROD["_expected_latest_bctc_period"](now), expected)

    def test_latest_expected_quarter_is_current(self):
        self.assertTrue(PROD["_bctc_period_is_current"]("2026-Q3", "2026-Q3"))

    def test_previous_quarter_is_stale(self):
        self.assertFalse(PROD["_bctc_period_is_current"]("2026-Q2", "2026-Q3"))

    def test_future_quarter_is_not_accepted_as_current(self):
        self.assertFalse(PROD["_bctc_period_is_current"]("2026-Q4", "2026-Q3"))

    def test_far_future_year_is_not_accepted_as_current(self):
        self.assertFalse(PROD["_bctc_period_is_current"]("2099-Q4", "2026-Q3"))

    def test_missing_or_malformed_period_is_not_current(self):
        for period in ("", None, "2026-Q5", "Q3-2026", "not-a-period"):
            with self.subTest(period=period):
                self.assertFalse(PROD["_bctc_period_is_current"](period, "2026-Q3"))

    def test_auto_refresh_retries_any_unverified_period(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        self.assertIn(
            "period_is_stale = not _bctc_period_is_current(",
            source,
        )
        self.assertIn("retry_days = 7 if period_is_stale else 30", source)
        self.assertNotIn(
            'and latest_period < expected_period',
            source,
        )

    def test_normalizer_carries_bctc_freshness_metadata(self):
        expected = PROD["_expected_latest_bctc_period"]()
        normalized = PROD["normalize_stock_data"](
            "TEST",
            {"roe": 12, "bctc_latest_income_period": expected},
        )
        self.assertEqual(normalized["bctc_expected_period"], expected)
        self.assertEqual(normalized["bctc_latest_income_period"], expected)
        self.assertTrue(normalized["bctc_period_is_current"])


class TestFiniteNumericNormalization(unittest.TestCase):
    def test_finite_float_rejects_non_finite_and_non_numeric_values(self):
        for value in (None, True, "bad", float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.assertEqual(PROD["_finite_float"](value, default=-1), -1)
        self.assertEqual(PROD["_finite_float"]("12.5"), 12.5)

    def test_explicitly_available_ratio_still_requires_finite_numeric_value(self):
        for value in ("bad", float("nan"), float("inf"), True, None):
            with self.subTest(value=value):
                self.assertFalse(PROD["_has_reported_ratio"](
                    {"roe": value, "roe_available": True}, "roe"
                ))

    def test_normalizer_converts_malformed_market_inputs_to_missing_defaults(self):
        normalized = PROD["normalize_stock_data"](
            "TEST",
            {
                "close": "bad", "pe": float("nan"), "pb": float("inf"),
                "roe": "not-a-number", "roa": float("nan"), "eps": True,
                "bvps": "invalid", "dividend_yield": float("inf"),
                "cfo_ttm": "invalid", "net_profit_ttm": float("nan"),
                "debt_to_equity": float("inf"), "gross_margin": "bad",
                "profit_growth_yoy": float("nan"),
                "profit_growth_yoy_available": True,
                "credit_growth_yoy": float("inf"),
                "credit_growth_yoy_available": True,
                "bank_cir": "invalid", "bank_cir_available": True,
            },
        )
        for key in (
            "close_price", "pe", "pb", "roe", "roa", "eps", "bvps",
            "dividend_yield", "cfo_latest", "cfo_ttm", "net_profit_ttm",
            "debt_to_equity", "gross_margin", "profit_growth_yoy",
            "credit_growth_yoy", "bank_cir",
        ):
            self.assertEqual(normalized[key], 0.0, key)
        self.assertFalse(normalized["roe_available"])
        self.assertFalse(normalized["roa_available"])
        self.assertFalse(normalized["profit_growth_yoy_available"])
        self.assertFalse(normalized["credit_growth_yoy_available"])
        self.assertFalse(normalized["bank_cir_available"])


class TestReportedProfitabilityMetricAvailability(unittest.TestCase):
    def test_normalizer_requires_explicit_source_flags_for_negative_ratios(self):
        normalized = PROD["normalize_stock_data"](
            "TEST",
            {"roe": -3.4, "roa": -0.7, "pe": 8, "pb": 1.2},
        )
        self.assertEqual(normalized["roe"], -3.4)
        self.assertEqual(normalized["roa"], -0.7)
        self.assertFalse(normalized["roe_available"])
        self.assertFalse(normalized["roa_available"])

    def test_normalizer_accepts_negative_ratios_with_explicit_source_flags(self):
        normalized = PROD["normalize_stock_data"](
            "TEST",
            {
                "roe": -3.4, "roe_available": True,
                "roa": -0.7, "roa_available": True,
                "pe": 8, "pb": 1.2,
            },
        )
        self.assertTrue(normalized["roe_available"])
        self.assertTrue(normalized["roa_available"])

    def test_normalizer_keeps_zero_ratios_missing_without_explicit_confirmation(self):
        normalized = PROD["normalize_stock_data"](
            "TEST", {"roe": 0, "roa": 0}
        )
        self.assertFalse(normalized["roe_available"])
        self.assertFalse(normalized["roa_available"])

    def test_normalizer_accepts_explicitly_verified_zero_ratios(self):
        normalized = PROD["normalize_stock_data"](
            "TEST",
            {"roe": 0, "roa": 0, "roe_available": True, "roa_available": True},
        )
        self.assertTrue(normalized["roe_available"])
        self.assertTrue(normalized["roa_available"])

    def test_numeric_provider_ratios_without_provenance_are_not_verified(self):
        self.assertFalse(PROD["_has_reported_ratio"]({"roe": -4.2}, "roe"))
        self.assertFalse(PROD["_has_reported_ratio"]({"roa": -0.8}, "roa"))

    def test_explicit_source_flags_accept_negative_and_zero_ratios(self):
        self.assertTrue(PROD["_has_reported_ratio"](
            {"roe": -4.2, "roe_available": True}, "roe"
        ))
        self.assertTrue(PROD["_has_reported_ratio"](
            {"roa": 0, "roa_available": True}, "roa"
        ))

    def test_zero_is_not_assumed_available_without_source_flag(self):
        self.assertFalse(PROD["_has_reported_ratio"]({"roe": 0}, "roe"))
        self.assertFalse(PROD["_has_reported_ratio"]({"roa": 0}, "roa"))

    def test_explicit_source_flag_can_confirm_zero_ratio(self):
        self.assertTrue(PROD["_has_reported_ratio"](
            {"roe": 0, "roe_available": True}, "roe"
        ))

    def test_explicit_unavailable_flag_overrides_numeric_placeholder(self):
        self.assertFalse(PROD["_has_reported_ratio"](
            {"roe": -2.5, "roe_available": False}, "roe"
        ))

    def test_non_finite_and_non_numeric_values_are_unavailable(self):
        self.assertFalse(PROD["_has_reported_ratio"]({"roe": float("nan")}, "roe"))
        self.assertFalse(PROD["_has_reported_ratio"]({"roe": "not-a-number"}, "roe"))
        self.assertFalse(PROD["_has_reported_ratio"]({"roe": True}, "roe"))
    def test_generic_addons_do_not_double_count_specialized_industries(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        start = source.index("uses_generic_fundamental_addons =")
        end = source.index("\n    )", start)
        guard = source[start:end]
        for flag in (
            "is_petroleum", "is_pharma", "is_textile", "is_rubber",
            "is_plastics_chemicals", "is_agri_processing",
        ):
            with self.subTest(flag=flag):
                self.assertIn(f"and not {flag}", guard)
        self.assertEqual(source.count("if uses_generic_fundamental_addons:"), 3)

    def test_generic_sector_points_are_gated_by_verified_metric_availability(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        self.assertIn('if nd.get("roe_available", False):\n            if roe >= 14:', source)
        self.assertIn('if nd.get("roa_available", False):\n            if roa >= 5.0:', source)
        self.assertIn('if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):\n            if net_profit_ttm < 0:', source)
        self.assertIn('metrics["debt_to_equity_available"] = False', source)
        self.assertIn('and nd.get("debt_to_equity_available", False) is True', source)
        self.assertNotIn('if roe >= 14: fund_score += 20', source)

    def test_every_sector_scoring_branch_uses_availability_not_positive_profitability(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count('nd.get("roe_available", False)'), 20)
        self.assertGreaterEqual(source.count('nd.get("roa_available", False)'), 8)
        scoring = source[source.index("fund_score = 0"):source.index("# Không có điểm định giá mặc định")]
        self.assertIsNone(re.search(r"^\s+if roe > 0:", scoring, re.MULTILINE))
        self.assertIsNone(re.search(r"^\s+if roa > 0:", scoring, re.MULTILINE))




class TestBctcOptionalValueResolution(unittest.TestCase):
    def test_duplicate_canonical_id_with_different_values_is_not_guessed(self):
        rows = [
            {"id": "BS_CUSTOMER_LOANS", "source_field": "bsb103", "period": "2026-Q3", "value": 100},
            {"id": "BS_CUSTOMER_LOANS", "source_field": "bsb104", "period": "2026-Q3", "value": 90},
        ]
        self.assertIsNone(PROD["_get_bctc_optional_value"](rows, "BS_CUSTOMER_LOANS", "2026-Q3"))

    def test_known_total_source_field_wins_over_component(self):
        rows = [
            {"id": "BS_TOTAL_ASSETS", "source_field": "bsa53", "period": "2026-Q3", "value": 500},
            {"id": "BS_TOTAL_ASSETS", "source_field": "bsa1", "period": "2026-Q3", "value": 200},
        ]
        self.assertEqual(
            PROD["_get_bctc_optional_value"](rows, "BS_TOTAL_ASSETS", "2026-Q3"),
            500.0,
        )

    def test_duplicate_rows_with_identical_values_are_safe(self):
        rows = [
            {"id": "X", "source_field": "x1", "period": "2026-Q3", "value": 25},
            {"id": "X", "source_field": "x2", "period": "2026-Q3", "value": 25},
        ]
        self.assertEqual(PROD["_get_bctc_optional_value"](rows, "X", "2026-Q3"), 25.0)

    def test_target_id_order_is_deterministic(self):
        rows = [
            {"id": "ALIAS_B", "period": "2026-Q3", "value": 20},
            {"id": "ALIAS_A", "period": "2026-Q3", "value": 10},
        ]
        self.assertEqual(
            PROD["_get_bctc_optional_value"](rows, ["ALIAS_A", "ALIAS_B"], "2026-Q3"),
            10.0,
        )


class TestBctcDiagnosticPipeline(unittest.TestCase):

    def test_summary_pipeline_projects_metadata_without_financial_values(self):
        pipeline = PROD["_build_bctc_summary_pipeline"]("CTG")
        self.assertEqual(pipeline[0]["$match"], {"symbol": "CTG"})
        projection = pipeline[1]["$project"]
        for statement in ("balance_sheet", "income_statement", "cash_flow"):
            mapping = projection[statement]["$map"]
            self.assertEqual(mapping["as"], "row")
            self.assertEqual(mapping["in"]["source_field"], "$$row.source_field")
            self.assertNotIn("value", mapping["in"])
        self.assertEqual(pipeline[-1], {"$limit": 1})


    def test_bank_metric_pipeline_projects_only_required_source_fields(self):
        periods = ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3"]
        pipeline = PROD["_build_bctc_bank_metric_pipeline"](
            periods, "2026-Q3", "2025-Q3"
        )
        projection = pipeline[1]["$project"]
        balance_filter = projection["balance_sheet"]["$filter"]["cond"]["$and"]
        income_filter = projection["income_statement"]["$filter"]["cond"]["$and"]
        self.assertEqual(balance_filter[0]["$in"][0], "$$row.source_field")
        self.assertEqual(balance_filter[1]["$in"][0], "$$row.period")
        self.assertIn("bsb104", balance_filter[0]["$in"][1])
        self.assertIn("bsa78", balance_filter[0]["$in"][1])
        self.assertEqual(balance_filter[1]["$in"][1], ["2026-Q3", "2025-Q3"])
        self.assertEqual(income_filter[0]["$in"][0], "$$row.source_field")
        self.assertEqual(income_filter[1]["$in"][0], "$$row.period")
        self.assertIn("isb39", income_filter[0]["$in"][1])
        self.assertEqual(income_filter[1]["$in"][1], periods)

    def test_bank_issuer_verification_requires_every_period_equation(self):
        balance = {
            "valid": True,
            "reconciliations": [
                {"id": "bank_net_customer_loans", "period": "2026-Q3", "status": "pass"},
                {"id": "bank_net_customer_loans", "period": "2025-Q3", "status": "pass"},
            ],
        }
        periods = ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3"]
        income = {
            "valid": True,
            "reconciliations": [
                {"id": "bank_ppop_equals_operating_income_plus_signed_operating_expense",
                 "period": period, "status": "pass"}
                for period in periods
            ],
        }
        self.assertTrue(PROD["_bank_issuer_mapping_verified"](
            balance, income, "2026-Q3", "2025-Q3", periods
        ))
        income["reconciliations"].pop()
        self.assertFalse(PROD["_bank_issuer_mapping_verified"](
            balance, income, "2026-Q3", "2025-Q3", periods
        ))


    def test_industrial_metric_pipeline_is_compact_and_section_specific(self):
        periods = ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3"]
        pipeline = PROD["_build_bctc_industrial_metric_pipeline"](
            periods, "2026-Q3", "2025-Q3"
        )
        projection = pipeline[1]["$project"]
        balance_conditions = projection["balance_sheet"]["$filter"]["cond"]["$and"]
        income_conditions = projection["income_statement"]["$filter"]["cond"]["$and"]
        cash_conditions = projection["cash_flow"]["$filter"]["cond"]["$and"]
        self.assertEqual(balance_conditions[0]["$in"][0], "$$row.source_field")
        self.assertEqual(balance_conditions[1]["$in"][0], "$$row.period")
        self.assertIn("bsa78", balance_conditions[0]["$in"][1])
        self.assertEqual(income_conditions[0]["$in"][0], "$$row.source_field")
        self.assertEqual(income_conditions[1]["$in"][0], "$$row.period")
        self.assertEqual(income_conditions[0]["$in"][1], ["isa20"])
        self.assertEqual(
            cash_conditions[0]["$in"][0], "$$row.source_field"
        )
        self.assertEqual(
            cash_conditions[0]["$in"][1],
            ["cfa18", "cfa34", "cfa36", "cfa37", "cfa38"],
        )
        self.assertEqual(cash_conditions[1]["$in"][0], "$$row.period")


    def test_ytd_ttm_projection_fetches_prior_q3_support_row_only_when_needed(self):
        periods = ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3"]
        self.assertEqual(PROD["_ytd_support_period"](periods), "2025-Q3")
        self.assertEqual(PROD["_ytd_support_period"](
            ["2026-Q2", "2026-Q3", "2026-Q4", "2027-Q1"]
        ), "2026-Q1")
        self.assertEqual(PROD["_ytd_support_period"](
            ["2026-Q3", "2026-Q4", "2027-Q1", "2027-Q2"]
        ), "2026-Q2")
        self.assertIsNone(PROD["_ytd_support_period"](
            ["2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"]
        ))

        bank_pipeline = PROD["_build_bctc_bank_metric_pipeline"](
            periods, "2026-Q3", "2025-Q3", include_ytd_support=True
        )
        bank_periods = bank_pipeline[1]["$project"]["income_statement"]["$filter"]["cond"]["$and"][1]["$in"][1]
        self.assertEqual(bank_periods, periods + ["2025-Q3"])

        industrial_pipeline = PROD["_build_bctc_industrial_metric_pipeline"](
            periods, "2026-Q3", "2025-Q3",
            include_income_ytd_support=True,
            include_cashflow_ytd_support=True,
        )
        projection = industrial_pipeline[1]["$project"]
        income_periods = projection["income_statement"]["$filter"]["cond"]["$and"][1]["$in"][1]
        cash_periods = projection["cash_flow"]["$filter"]["cond"]["$and"][1]["$in"][1]
        self.assertEqual(income_periods, periods + ["2025-Q3"])
        self.assertEqual(cash_periods, periods + ["2025-Q3"])



    def test_bank_sector_cannot_enter_industrial_metric_endpoint(self):
        self.assertFalse(PROD["_industrial_metric_endpoint_allowed"]("NGAN_HANG"))
        self.assertTrue(PROD["_industrial_metric_endpoint_allowed"]("TECHNOLOGY"))
        self.assertTrue(PROD["_industrial_metric_endpoint_allowed"]("INSURANCE"))



    def test_bank_asset_quality_discovery_rejects_non_bank_sectors(self):
        self.assertTrue(PROD["_bank_asset_quality_endpoint_allowed"]("NGAN_HANG"))
        self.assertFalse(PROD["_bank_asset_quality_endpoint_allowed"]("INSURANCE"))
        self.assertFalse(PROD["_bank_asset_quality_endpoint_allowed"]("STEEL"))

    def test_bank_asset_quality_discovery_projection_is_bounded_and_compact(self):
        pipeline = PROD["_build_bctc_bank_asset_quality_pipeline"]("ctg")
        self.assertEqual(pipeline[0]["$match"], {"symbol": "CTG"})
        projection = pipeline[1]["$project"]
        self.assertEqual(
            set(projection) - {"_id"},
            {"symbol", "updated_at", "latest_period", "source",
             "vci_field_catalog", "balance_sheet"},
        )
        mapping = projection["balance_sheet"]["$map"]
        self.assertEqual(mapping["input"]["$slice"][1], -2500)
        self.assertEqual(mapping["as"], "row")
        self.assertEqual(
            set(mapping["in"]),
            {"source_field", "id", "name", "period", "value", "unit"},
        )
        self.assertEqual(mapping["in"]["source_field"], "$$row.source_field")
        self.assertEqual(pipeline[-1], {"$limit": 1})

    def test_industrial_discovery_projection_is_single_issuer_and_source_scoped(self):
        pipeline = PROD["_build_bctc_industrial_discovery_pipeline"]("fpt")
        self.assertEqual(pipeline[0]["$match"], {"symbol": "FPT"})
        projection = pipeline[1]["$project"]
        self.assertEqual(
            set(projection) - {"_id"},
            {"symbol", "updated_at", "latest_period", "source",
             "vci_field_catalog", "balance_sheet", "income_statement", "cash_flow"},
        )
        self.assertEqual(pipeline[-1], {"$limit": 1})
        for section in ("balance_sheet", "income_statement", "cash_flow"):
            with self.subTest(section=section):
                mapping = projection[section]["$map"]
                self.assertEqual(mapping["input"]["$slice"][1], -2500)
                self.assertEqual(mapping["as"], "row")
                self.assertEqual(
                    set(mapping["in"]),
                    {"source_field", "id", "name", "period", "value", "unit"},
                )
                self.assertEqual(mapping["in"]["source_field"], "$$row.source_field")

    def test_pipeline_filters_only_three_loan_fields_for_latest_period(self):
        pipeline = PROD["_build_bctc_selected_fields_pipeline"]()
        project = pipeline[1]["$project"]
        condition = project["balance_sheet"]["$filter"]["cond"]
        self.assertEqual(
            condition["$and"][0]["$in"][1],
            ["bsb103", "bsb104", "bsb105"],
        )
        self.assertEqual(condition["$and"][1]["$eq"], ["$$row.period", "$latest_period"])

    def test_bank_income_pipeline_returns_only_selected_fields_and_periods(self):
        periods = ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3"]
        pipeline = PROD["_build_bctc_bank_income_pipeline"](periods)
        condition = pipeline[1]["$project"]["income_statement"]["$filter"]["cond"]
        self.assertEqual(condition["$and"][0]["$in"][0], "$$row.source_field")
        self.assertEqual(len(condition["$and"][0]["$in"][1]), 20)
        self.assertEqual(condition["$and"][1]["$in"], ["$$row.period", periods])


    def test_pipeline_uses_explicit_requested_period(self):
        pipeline = PROD["_build_bctc_selected_fields_pipeline"]("2026-Q3")
        condition = pipeline[1]["$project"]["balance_sheet"]["$filter"]["cond"]
        self.assertEqual(condition["$and"][1]["$eq"], ["$$row.period", "2026-Q3"])


class TestProductionSectorRouting(unittest.TestCase):
    def test_eight_representative_symbols_route_to_expected_scorecards(self):
        expected = {
            "CTG": "NGAN_HANG",
            "FPT": "TECHNOLOGY",
            "KDH": "BAT_DONG_SAN",
            "HPG": "STEEL",
            "DPM": "FERTILIZER",
            "MWG": "CONG_NGHE_BAN_LE",
            "BVH": "INSURANCE",
            "GMD": "MARITIME",
        }
        for symbol, group in expected.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(PROD["get_sub_sector"](symbol), group)

    def test_holding_classification_includes_vic_and_gex(self):
        for symbol in ("VIC", "GEX"):
            with self.subTest(symbol=symbol):
                self.assertEqual(PROD["get_sub_sector"](symbol), "HOLDING")


    def test_unknown_symbols_are_not_assigned_to_a_generic_scoring_group(self):
        self.assertFalse(PROD["is_symbol_classified"]("ZZZ_UNKNOWN"))
        self.assertTrue(PROD["is_symbol_classified"]("CTG"))
        self.assertTrue(PROD["is_symbol_classified"]("FPT"))


    def test_every_subsector_symbol_has_a_configured_scorecard(self):
        mapping = PROD["SUB_SECTOR_MAPPING"]
        configs = PROD["SUB_SECTOR_CONFIG"]
        for group, symbols in mapping.items():
            for symbol in symbols:
                with self.subTest(group=group, symbol=symbol):
                    self.assertEqual(PROD["get_sub_sector"](symbol), group)
                    # Một số ngành con dùng cấu hình định giá của ngành cha.
                    config = configs.get(group) or configs.get(PROD["get_sector"](symbol))
                    self.assertIsNotNone(config, f"Thiếu cấu hình định giá cho {symbol}/{group}")
                    self.assertIn(str(config.get("primary_metric", "PE")).upper(), ("PE", "PB"))
                    self.assertGreater(float(config["cap_pe"]), 0)
                    self.assertGreater(float(config["cap_pb"]), 0)
                    self.assertGreater(float(config["mos"]), 0)
                    self.assertLess(float(config["mos"]), 1)


class TestProductionValuation(unittest.TestCase):
    def fair_value(self, symbol):
        group = PROD["get_sub_sector"](symbol)
        config = PROD["SUB_SECTOR_CONFIG"][group]
        nd = {
            "eps": 1000.0,
            "bvps": 10000.0,
            "close_price": 12000.0,
            "cfo_latest": 1500.0,
            "net_profit_ttm": 1000.0,
            "roe": 15.0,
        }
        median = {"pe": 10.0, "pb": 1.5, "roe": 12.0}
        return group, PROD["compute_fair_value_zone"](symbol, nd, median, config, group)

    def test_financial_holding_and_petroleum_use_pb_only_until_more_data_exists(self):
        for symbol in ("CTG", "BVH", "VIC", "GEX", "PLX", "PVD", "PVS", "BSR"):
            with self.subTest(symbol=symbol):
                _, result = self.fair_value(symbol)
                self.assertEqual(set(result["methods"]), {"PB_method"})

    def test_primary_metric_controls_available_valuation_methods(self):
        expected = {
            "FPT": {"PE_method", "PB_method", "Graham"},
            "KDH": {"PE_method", "PB_method"},
            "HPG": {"PB_method"},
            "DPM": {"PB_method"},
            "MWG": {"PE_method", "PB_method", "Graham"},
            "GMD": {"PE_method", "PB_method"},
        }
        for symbol, methods in expected.items():
            with self.subTest(symbol=symbol):
                _, result = self.fair_value(symbol)
                self.assertEqual(set(result["methods"]), methods)

    def test_pb_primary_sector_gives_pb_method_priority(self):
        _, result = self.fair_value("HPG")
        self.assertEqual(result["fair_high"], result["methods"]["PB_method"])

    def test_roe_bonus_requires_a_valid_peer_roe_median(self):
        group = "TECHNOLOGY"
        config = PROD["SUB_SECTOR_CONFIG"][group]
        nd = {
            "eps": 1000.0, "bvps": 0.0, "close_price": 12000.0,
            "cfo_latest": 100.0, "net_profit_ttm": 1000.0, "roe": 25.0,
        }
        result = PROD["compute_fair_value_zone"](
            "FPT", nd, {"pe": 10.0, "pb": 0.0, "roe": 0.0}, config, group
        )
        self.assertEqual(result["methods"]["PE_method"], 10000.0)

    def test_negative_ttm_profit_disables_pe_and_graham(self):
        group = "TECHNOLOGY"
        config = PROD["SUB_SECTOR_CONFIG"][group]
        nd = {
            "eps": 1000.0, "bvps": 10000.0, "close_price": 12000.0,
            "cfo_latest": -100.0, "net_profit_ttm": -50.0, "roe": -2.0,
        }
        result = PROD["compute_fair_value_zone"](
            "FPT", nd, {"pe": 10.0, "pb": 1.5, "roe": 12.0}, config, group
        )
        self.assertEqual(set(result["methods"]), {"PB_method"})

    def test_missing_valuation_inputs_return_no_fair_value(self):
        group = "STEEL"
        config = PROD["SUB_SECTOR_CONFIG"][group]
        nd = {
            "eps": 0.0, "bvps": 0.0, "close_price": 10000.0,
            "cfo_latest": 0.0, "net_profit_ttm": 0.0, "roe": 0.0,
        }
        result = PROD["compute_fair_value_zone"](
            "HPG", nd, {"pe": 0.0, "pb": 0.0, "roe": 0.0}, config, group
        )
        self.assertEqual(result["verdict"], "KHÔNG ĐỦ DỮ LIỆU")
        self.assertEqual(result["fair_low"], 0)


class TestDebtToEquityAvailability(unittest.TestCase):
    def test_verified_zero_debt_is_valid(self):
        self.assertTrue(PROD["is_valid_debt_to_equity"](0))

    def test_negative_or_invalid_debt_ratio_is_not_valid(self):
        for value in (-0.01, None, float("nan"), float("inf"), "invalid"):
            with self.subTest(value=value):
                self.assertFalse(PROD["is_valid_debt_to_equity"](value))


class TestPeerMedianQuality(unittest.TestCase):
    def test_unclassified_symbols_do_not_pollute_sector_medians(self):
        rows = {
            "CTG": {"pe": 8, "pb": 1.2, "roe": 15, "roe_available": True},
            "BID": {"pe": 9, "pb": 1.3, "roe": 14, "roe_available": True},
            "VCB": {"pe": 10, "pb": 1.4, "roe": 16, "roe_available": True},
            "ZZZ_UNKNOWN": {"pe": 1, "pb": 0.1, "roe": 99, "roe_available": True},
        }
        medians = PROD["compute_sector_medians"](rows)
        bank = medians["NGAN_HANG"]
        self.assertEqual(bank["pe"], 9)
        self.assertEqual(bank["n_pe"], 3)
        self.assertEqual(bank["roe"], 15)

    def test_peer_median_requires_three_samples_per_metric(self):
        rows = {
            "CTG": {"pe": 8, "pb": 1.2, "roe": 15, "roe_available": True},
            "BID": {"pe": 9, "pb": 0, "roe": 14, "roe_available": True},
            "VCB": {"pe": 10, "pb": 0, "roe": 16, "roe_available": True},
        }
        medians = PROD["compute_sector_medians"](rows)["NGAN_HANG"]
        self.assertEqual(medians["pe"], 9)
        self.assertEqual(medians["pb"], 0)
        self.assertEqual(medians["n_pb"], 1)

    def test_verified_negative_roe_is_included_in_peer_median(self):
        rows = {
            "CTG": {"pe": 8, "pb": 1.2, "roe": 15, "roe_available": True},
            "BID": {"pe": 9, "pb": 1.3, "roe": -5, "roe_available": True},
            "VCB": {"pe": 10, "pb": 1.4, "roe": 10, "roe_available": True},
            "MISSING": {"pe": 11, "pb": 1.5, "roe": 0, "roe_available": False},
        }
        bank = PROD["compute_sector_medians"](rows)["NGAN_HANG"]
        self.assertEqual(bank["n_roe"], 3)
        self.assertEqual(bank["roe"], 10)

    def test_unclassified_ticker_gets_no_peer_valuation_multiple(self):
        median, label = PROD["get_median_for_symbol"]("ZZZ_UNKNOWN", {})
        self.assertEqual(label, "UNCLASSIFIED")
        self.assertEqual(median["pe"], 0)
        self.assertEqual(median["pb"], 0)


class TestFundamentalScoreCeiling(unittest.TestCase):
    def test_score_never_exceeds_verified_available_weight(self):
        cases = [
            (45, 20, 50, 20),
            (20, 35, 50, 20),
            (-5, 30, 50, 0),
            (60, 80, 50, 50),
            (40, 15, 50, 15),
        ]
        for score, available, maximum, expected in cases:
            with self.subTest(score=score, available=available):
                self.assertEqual(
                    PROD["cap_fundamental_score"](score, available, maximum),
                    expected,
                )

    def test_invalid_score_inputs_fail_closed(self):
        for score, available in ((float("nan"), 20), (20, float("inf")), (20, None)):
            with self.subTest(score=score, available=available):
                self.assertEqual(PROD["cap_fundamental_score"](score, available), 0)


class TestVerifiedRoeValuation(unittest.TestCase):
    def test_unverified_roe_cannot_increase_pe_fair_value(self):
        nd = {
            "eps": 10, "bvps": 100, "close_price": 100,
            "cfo_latest": 0, "net_profit_ttm": 100,
            "roe": 30, "roe_available": False,
            "bctc_income_ttm_complete": True,
        }
        config = {"cap_pe": 20, "cap_pb": 2, "mos": 0.2, "primary_metric": "PE"}
        result = PROD["compute_fair_value_zone"](
            "TEST", nd, {"pe": 10, "pb": 0, "roe": 20}, config,
            "CONG_NGHE_BAN_LE",
        )
        self.assertEqual(result["methods"]["PE_method"], 100)

    def test_verified_roe_can_apply_existing_pe_bonus(self):
        nd = {
            "eps": 10, "bvps": 100, "close_price": 100,
            "cfo_latest": 0, "net_profit_ttm": 100,
            "roe": 30, "roe_available": True,
            "bctc_income_ttm_complete": True,
        }
        config = {"cap_pe": 20, "cap_pb": 2, "mos": 0.2, "primary_metric": "PE"}
        result = PROD["compute_fair_value_zone"](
            "TEST", nd, {"pe": 10, "pb": 0, "roe": 20}, config,
            "CONG_NGHE_BAN_LE",
        )
        self.assertEqual(result["methods"]["PE_method"], 115)

    def test_all_pb_valuation_roe_gates_require_verified_input(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        self.assertNotIn("if valid_pb and roe > 0:", source)
        self.assertGreaterEqual(
            source.count("if valid_pb and roe_is_verified and roe > 0:"),
            7,
        )
        self.assertIn(
            "if pb < 1.3 or (roe_is_verified and roe >= 16 and pb < 1.8): val_score = 30",
            source,
        )
        self.assertNotIn(
            "if pb < 1.3 or (roe >= 16 and pb < 1.8): val_score = 30",
            source,
        )


class TestDecisionCoverageGuard(unittest.TestCase):
    def call_decision(self, coverage):
        return PROD["compute_final_decision"](
            fund_score=40, tech_score=16, val_score=25, total_score=81,
            verdict="RẺ - TRONG VÙNG MUA", cfo=100, npat=100,
            roe=15, debt_to_equity=0.5, discount_to_fair_pct=10,
            close_price=100, fair_high=120, fair_low=110,
            sector="CONG_NGHE_BAN_LE", fundamental_coverage_pct=coverage,
            bctc_period_is_current=True,
        )

    def test_coverage_below_65_blocks_buy_recommendation(self):
        label, _, reason = self.call_decision(64.9)
        self.assertEqual(label, "⚠️ THIẾU DỮ LIỆU")
        self.assertIn("64.9%", reason)

    def test_coverage_at_65_can_proceed_to_normal_decision_rules(self):
        label, _, _ = self.call_decision(65.0)
        self.assertNotEqual(label, "⚠️ THIẾU DỮ LIỆU")

    def test_missing_coverage_fails_closed_instead_of_defaulting_to_100(self):
        kwargs = dict(
            fund_score=40, tech_score=16, val_score=25, total_score=81,
            verdict="RẺ - TRONG VÙNG MUA", cfo=100, npat=100,
            roe=15, debt_to_equity=0.5,
        )
        label, _, reason = PROD["compute_final_decision"](**kwargs)
        self.assertEqual(label, "⚠️ THIẾU DỮ LIỆU")
        self.assertIn("không xác định", reason)

    def test_stale_bctc_blocks_buy_recommendation_even_with_high_coverage(self):
        result = PROD["compute_final_decision"](
            fund_score=45, tech_score=18, val_score=28, total_score=91,
            verdict="RẺ - TRONG VÙNG MUA", cfo=100, npat=100,
            roe=18, debt_to_equity=0.4, discount_to_fair_pct=12,
            close_price=100, fair_high=120, fair_low=110,
            sector="CONG_NGHE_BAN_LE", fundamental_coverage_pct=90,
            bctc_period_is_current=False,
        )
        self.assertEqual(result[0], "⚠️ BCTC CHƯA CẬP NHẬT")
        self.assertIn("kỳ báo cáo dự kiến", result[2])

    def test_unknown_bctc_freshness_fails_closed(self):
        result = PROD["compute_final_decision"](
            fund_score=45, tech_score=18, val_score=28, total_score=91,
            verdict="RẺ - TRONG VÙNG MUA", cfo=100, npat=100,
            roe=18, debt_to_equity=0.4, discount_to_fair_pct=12,
            close_price=100, fair_high=120, fair_low=110,
            sector="CONG_NGHE_BAN_LE", fundamental_coverage_pct=90,
        )
        self.assertEqual(result[0], "⚠️ BCTC CHƯA XÁC MINH")
        self.assertIn("Thiếu xác nhận", result[2])

    def test_invalid_coverage_values_cannot_bypass_decision_gate(self):
        for coverage in (None, float("nan"), float("inf"), 100.1, -1):
            with self.subTest(coverage=coverage):
                label, _, _ = self.call_decision(coverage)
                self.assertEqual(label, "⚠️ THIẾU DỮ LIỆU")



class TestNewIndustryTickerClassification(unittest.TestCase):
    def test_no_ticker_remains_in_the_broad_generic_scoring_bucket(self):
        fallback_symbols = PROD["FALLBACK_SECTOR_MAPPING"].get(
            "SAN_XUAT_TIEU_DUNG_PHONG_THU", []
        )
        unassigned = [
            symbol for symbol in fallback_symbols
            if PROD["get_sub_sector"](symbol) == "SAN_XUAT_TIEU_DUNG_PHONG_THU"
        ]
        self.assertEqual(unassigned, [])

    def test_previously_generic_tickers_are_routed_to_distinct_industries(self):
        expected = {
            "DHG": "PHARMA",
            "MSH": "TEXTILE",
            "DRC": "RUBBER",
            "CSM": "RUBBER",
            "APH": "PLASTICS_CHEMICALS",
            "PAN": "AGRI_PROCESSING",
        }
        for symbol, sector in expected.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(PROD["get_sub_sector"](symbol), sector)
                self.assertTrue(PROD["is_symbol_classified"](symbol))


class TestSectorFundamentalCoverageCeilings(unittest.TestCase):
    def test_each_industry_branch_has_expected_available_weight_ceiling(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        start = source.index("    fund_score = 0", source.index("def evaluate_stock_from_db"))
        end = source.index(
            "    if uses_generic_fundamental_addons:", start
        )
        scoring = source[start:end]
        markers = [
            ('if sector == "NGAN_HANG":', 24),
            ("elif is_insurance:", 18),
            ('elif sector == "CHUNG_KHOAN":', 18),
            ('elif sector == "BAT_DONG_SAN" and not is_holding:', 36),
            ("elif is_maritime:", 36),
            ("elif is_consumer:", 32),
            ("elif is_agri:", 30),
            ("elif is_aviation:", 30),
            ("elif is_construction:", 36),
            ("elif is_holding:", 34),
            ("elif is_building_materials:", 30),
            ("elif is_technology:", 34),
            ("elif is_retail:", 30),
            ("elif is_petroleum:", 20),
            ("elif is_energy:", 30),
            ("elif is_utility:", 24),
            ("elif is_steel or is_fertilizer:", 28),
            ("elif is_cyclical_material:", 28),
            ("elif is_pharma or is_textile or is_rubber or is_plastics_chemicals or is_agri_processing:", 40),
        ]
        positions = [scoring.index(marker) for marker, _ in markers]
        self.assertEqual(positions, sorted(positions))
        for index, (marker, expected_weight) in enumerate(markers):
            branch_start = positions[index]
            branch_end = positions[index + 1] if index + 1 < len(positions) else len(scoring)
            branch = scoring[branch_start:branch_end]
            actual_weight = sum(
                int(line.rsplit("+=", 1)[1].strip())
                for line in branch.splitlines()
                if "fundamental_score_available +=" in line
            )
            with self.subTest(branch=marker):
                self.assertEqual(actual_weight, expected_weight)
                self.assertLessEqual(actual_weight, 50)


class TestCfoTtmPeriodAlignment(unittest.TestCase):
    def _document(self, cashflow_periods):
        income_periods = ("2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3")
        income = [
            {"id": "IS_NET_PROFIT_AFTER_TAX", "period": period, "value": 100}
            for period in income_periods
        ]
        cashflow = [
            {
                "id": "CF_NET_CASH_FLOWS_FROM_OPERATING_ACTIVITIES",
                "source_field": "cfa18",
                "period": period,
                "value": value,
            }
            for period, value in cashflow_periods
        ]
        return {"source": "VCI_DIRECT", "income_statement": income,
                "cash_flow": cashflow, "balance_sheet": []}

    def test_cfo_ttm_is_unavailable_when_cashflow_ends_before_income_ttm(self):
        document = self._document([
            ("2025-Q3", 80_000_000_000), ("2025-Q4", 100_000_000_000),
            ("2026-Q1", 20_000_000_000), ("2026-Q2", 50_000_000_000),
        ])
        result = PROD["extract_metrics_from_bctc_data"](document)
        self.assertFalse(result["bctc_cfo_ttm_complete"])
        self.assertEqual(result["cfo_ttm"], 0.0)

    def test_future_cashflow_periods_do_not_shift_the_matching_ttm_window(self):
        document = self._document([
            ("2025-Q3", 80_000_000_000), ("2025-Q4", 100_000_000_000),
            ("2026-Q1", 20_000_000_000), ("2026-Q2", 50_000_000_000),
            ("2026-Q3", 90_000_000_000), ("2026-Q4", 130_000_000_000),
        ])
        result = PROD["extract_metrics_from_bctc_data"](document)
        self.assertTrue(result["bctc_cfo_ttm_complete"])
        self.assertEqual(result["cfo_ttm"], 110.0)

    def test_cfo_ttm_uses_same_four_quarters_as_income_ttm(self):
        document = self._document([
            ("2025-Q3", 80_000_000_000), ("2025-Q4", 100_000_000_000),
            ("2026-Q1", 20_000_000_000), ("2026-Q2", 50_000_000_000), ("2026-Q3", 90_000_000_000),
        ])
        result = PROD["extract_metrics_from_bctc_data"](document)
        self.assertTrue(result["bctc_income_ttm_complete"])
        self.assertTrue(result["bctc_cfo_ttm_complete"])
        # Q4'25=20, Q1'26=20, Q2'26=30, Q3'26=40.
        self.assertEqual(result["cfo_ttm"], 110.0)




class TestLegacyBankCirQuality(unittest.TestCase):
    def _document(self, expense_values, extra_period=None):
        periods = ("2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3")
        income = []
        for period in periods:
            income.extend([
                {"id": "IS_NET_PROFIT_AFTER_TAX", "period": period, "value": 1_000_000_000},
                {"id": "IS_TOTAL_OPERATING_INCOME_BANK", "period": period, "value": 10_000_000_000},
                {"id": "IS_OPERATING_EXPENSE_BANK", "period": period,
                 "value": expense_values[periods.index(period)]},
            ])
        if extra_period:
            income.extend([
                {"id": "IS_TOTAL_OPERATING_INCOME_BANK", "period": extra_period,
                 "value": 10_000_000_000},
                {"id": "IS_OPERATING_EXPENSE_BANK", "period": extra_period,
                 "value": -5_000_000_000},
            ])
        return {"income_statement": income, "cash_flow": [], "balance_sheet": []}

    def test_legacy_bank_cir_rejects_positive_expense_sign(self):
        document = self._document([-5_000_000_000, -5_000_000_000,
                                   5_000_000_000, -5_000_000_000])
        result = PROD["extract_metrics_from_bctc_data"](document)
        self.assertFalse(result["bank_cir_available"])

    def test_legacy_bank_cir_uses_same_latest_income_period_window(self):
        document = self._document(
            [-5_000_000_000] * 4, extra_period="2026-Q4"
        )
        result = PROD["extract_metrics_from_bctc_data"](document)
        self.assertTrue(result["bank_cir_available"])
        self.assertEqual(result["bank_cir"], 50.0)



class TestLegacyBctcLeverageAndGrossMargin(unittest.TestCase):
    def test_total_liabilities_to_equity_is_not_reported_as_debt_to_equity(self):
        document = {
            "income_statement": [
                {"id": "IS_NET_PROFIT_AFTER_TAX", "period": "2026-Q3",
                 "value": 10_000_000_000},
            ],
            "cash_flow": [],
            "balance_sheet": [
                {"id": "BS_OWNERS_EQUITY", "period": "2026-Q3",
                 "value": 50_000_000_000},
                {"id": "BS_LIABILITIES", "period": "2026-Q3",
                 "value": 100_000_000_000},
            ],
        }
        result = PROD["extract_metrics_from_bctc_data"](document)
        self.assertTrue(result["bctc_balance_sheet_valid"])
        self.assertEqual(result["total_liabilities_to_equity"], 2.0)
        self.assertTrue(result["total_liabilities_to_equity_available"])
        self.assertEqual(result["debt_to_equity"], 0.0)
        self.assertFalse(result["debt_to_equity_available"])

    def test_gross_margin_rejects_positive_cogs_when_ingestion_sign_is_unverified(self):
        periods = ("2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3")
        income = []
        for period in periods:
            income.extend([
                {"id": "IS_NET_PROFIT_AFTER_TAX", "period": period, "value": 1_000_000_000},
                {"id": "IS_NET_REVENUE", "period": period, "value": 10_000_000_000},
                {"id": "IS_COST_OF_GOODS_SOLD", "period": period, "value": 6_000_000_000},
            ])
        result = PROD["extract_metrics_from_bctc_data"]({
            "income_statement": income, "cash_flow": [], "balance_sheet": []
        })
        self.assertEqual(result["gross_margin"], 0.0)
        self.assertFalse(result["gross_margin_available"])
        self.assertEqual(result["gross_margin_ttm"], 0.0)
        self.assertFalse(result["gross_margin_ttm_available"])

    def test_gross_margin_uses_normalized_negative_cogs(self):
        periods = ("2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3")
        income = []
        for period in periods:
            income.extend([
                {"id": "IS_NET_PROFIT_AFTER_TAX", "period": period, "value": 1_000_000_000},
                {"id": "IS_NET_REVENUE", "period": period, "value": 10_000_000_000},
                {"id": "IS_COST_OF_GOODS_SOLD", "period": period, "value": -6_000_000_000},
            ])
        result = PROD["extract_metrics_from_bctc_data"]({
            "income_statement": income, "cash_flow": [], "balance_sheet": []
        })
        self.assertEqual(result["gross_margin"], 40.0)
        self.assertTrue(result["gross_margin_available"])
        self.assertEqual(result["gross_margin_ttm"], 40.0)
        self.assertTrue(result["gross_margin_ttm_available"])


    def test_quarterly_report_does_not_render_margin_for_positive_cogs(self):
        report = PROD["build_bctc_quarterly_trend"]({
            "income_statement": [
                {"id": "IS_NET_PROFIT_AFTER_TAX", "period": "2026-Q3", "value": 1_000},
                {"id": "IS_NET_REVENUE", "period": "2026-Q3", "value": 10_000},
                {"id": "IS_COST_OF_GOODS_SOLD", "period": "2026-Q3", "value": 6_000},
            ],
            "cash_flow": [],
        })
        self.assertIn("biên gộp N/A", report)



if __name__ == "__main__":
    unittest.main()


class TestAdminBctcHeaderAuthentication(unittest.TestCase):
    def test_bctc_admin_endpoints_reject_query_string_authentication(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        protected_functions = (
            "api_admin_bctc_summary",
            "api_admin_bctc_bank_income",
            "api_admin_bctc_bank_loans",
            "api_admin_bctc_bank_metrics",
            "api_admin_bctc_industrial_metrics",
            "api_admin_bctc_industrial_discovery",
            "api_admin_bctc_bank_asset_quality",
        )
        tree = ast.parse(source, filename="price_api.py")
        functions = {
            node.name: ast.get_source_segment(source, node)
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in protected_functions:
            with self.subTest(endpoint=name):
                body = functions.get(name) or ""
                self.assertIn('request.headers.get("X-Admin-Key")', body)
                self.assertNotIn('request.args.get("key")', body)
                self.assertIn("not ADMIN_SECRET_KEY", body)


class TestBctcOptionalValueNumericSafety(unittest.TestCase):
    def test_non_finite_and_non_numeric_values_are_unavailable(self):
        for invalid in (float("nan"), float("inf"), float("-inf"), "not-a-number", True):
            with self.subTest(value=repr(invalid)):
                rows = [{"id": "METRIC_X", "period": "2026-Q3", "value": invalid}]
                self.assertIsNone(PROD["_get_bctc_optional_value"](rows, "METRIC_X", "2026-Q3"))

    def test_invalid_duplicate_is_not_ignored_in_favor_of_valid_row(self):
        rows = [
            {"id": "METRIC_X", "period": "2026-Q3", "value": 100},
            {"id": "METRIC_X", "period": "2026-Q3", "value": float("nan")},
        ]
        self.assertIsNone(PROD["_get_bctc_optional_value"](rows, "METRIC_X", "2026-Q3"))

    def test_valid_numeric_string_is_still_supported(self):
        rows = [{"id": "METRIC_X", "period": "2026-Q3", "value": "125.5"}]
        self.assertEqual(PROD["_get_bctc_optional_value"](rows, "METRIC_X", "2026-Q3"), 125.5)


class TestRenderHealthCheckConfiguration(unittest.TestCase):
    def test_render_uses_gunicorn_and_database_readiness_probe(self):
        root = Path(__file__).parent
        render_config = (root / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("gunicorn", render_config)
        self.assertIn("price_api:app", render_config)
        self.assertIn("healthCheckPath: /readyz", render_config)

    def test_readiness_probe_checks_database_and_fails_closed(self):
        api_source = (Path(__file__).parent / "price_api.py").read_text(encoding="utf-8")
        ready_block = api_source.split('@app.get("/readyz")', 1)[1].split('@app.get("/api/status")', 1)[0]
        self.assertIn('mongo_client.admin.command("ping"', ready_block)
        self.assertIn('"database": "unavailable"', ready_block)
        self.assertIn('), 503', ready_block)

    def test_render_health_check_path_has_a_matching_flask_route(self):
        root = Path(__file__).parent
        render_config = (root / "render.yaml").read_text(encoding="utf-8")
        api_source = (root / "price_api.py").read_text(encoding="utf-8")
        match = re.search(r"(?m)^\s*healthCheckPath:\s*['\"]?([^\s'\"]+)", render_config)
        self.assertIsNotNone(match, "render.yaml must declare healthCheckPath")
        route = match.group(1)
        self.assertRegex(
            api_source,
            r"@app\.(?:get|route)\(\s*['\"]" + re.escape(route) + r"['\"]",
            "Render healthCheckPath must resolve to a Flask route",
        )



class TestScreenerJobCoordination(unittest.TestCase):
    def setUp(self):
        # Functions extracted by exec retain PROD as their globals namespace.
        self.namespace = PROD
        self.namespace["screener_state_lock"] = threading.Lock()
        self.namespace["screener_lock"] = threading.Lock()
        self.namespace["is_screener_running"] = False

        class FakeCollection:
            def __init__(self):
                self.calls = []

            def update_one(self, query, update, upsert=False):
                self.calls.append((query, update, upsert))

        self.collection = FakeCollection()
        self.namespace["screener_col"] = self.collection

    def test_single_symbol_write_is_rejected_during_full_refresh(self):
        self.namespace["is_screener_running"] = True
        self.assertFalse(
            self.namespace["_safe_update_screener_result"]("FPT", {"total_score": 80})
        )
        self.assertEqual(self.collection.calls, [])

    def test_single_symbol_write_is_serialized_and_saved_when_idle(self):
        self.assertTrue(
            self.namespace["_safe_update_screener_result"]("FPT", {"total_score": 80})
        )
        self.assertEqual(
            self.collection.calls,
            [({"symbol": "FPT"}, {"$set": {"total_score": 80}}, True)],
        )

    def test_only_one_worker_or_force_job_can_claim_slot(self):
        claim = self.namespace["_claim_screener_job"]
        release = self.namespace["_release_screener_job"]
        self.assertTrue(claim())
        self.assertFalse(claim())
        self.assertTrue(self.namespace["is_screener_running"])
        release()
        self.assertFalse(self.namespace["is_screener_running"])
        self.assertTrue(claim())
        release()



class TestScreenerAtomicReplacement(unittest.TestCase):
    def test_full_refresh_stages_then_uses_atomic_rename(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        tree = ast.parse(source, filename="price_api.py")
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_safe_replace_screener_results"
        )
        calls = [
            node for node in ast.walk(function)
            if isinstance(node, ast.Call)
        ]
        call_names = [
            node.func.attr for node in calls
            if isinstance(node.func, ast.Attribute)
        ]
        self.assertIn("insert_many", call_names)
        self.assertIn("count_documents", call_names)
        self.assertIn("rename", call_names)
        self.assertNotIn("delete_many", call_names)
        rename_calls = [
            node for node in calls
            if isinstance(node.func, ast.Attribute) and node.func.attr == "rename"
        ]
        self.assertTrue(any(
            any(keyword.arg == "dropTarget" and
                isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                for keyword in call.keywords)
            for call in rename_calls
        ))



class TestScreenerMemoryGuard(unittest.TestCase):
    def test_full_refresh_excludes_stale_bctc_history_and_releases_raw_payloads(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        tree = ast.parse(source, filename="price_api.py")
        worker = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "screener_worker"
        )
        calls = [
            node for node in ast.walk(worker)
            if isinstance(node, ast.Call)
        ]
        stock_find = [
            node for node in calls
            if isinstance(node.func, ast.Attribute)
            and node.func.attr == "find"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "collection"
        ]
        self.assertTrue(any(
            len(call.args) >= 2
            and isinstance(call.args[1], ast.Dict)
            and any(
                isinstance(key, ast.Constant) and key.value == "bctc_history"
                and isinstance(value, ast.Constant) and value.value == 0
                for key, value in zip(call.args[1].keys, call.args[1].values)
            )
            for call in stock_find
        ))
        self.assertTrue(any(
            isinstance(node, ast.Delete)
            and {target.id for target in node.targets if isinstance(target, ast.Name)}
            >= {"all_stocks", "all_bctc"}
            for node in ast.walk(worker)
        ))



class TestPetroleumSectorScoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(source, filename="price_api.py")
        cls.evaluate = next(
            node for node in cls.tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "evaluate_stock_from_db"
        )
        cls.valuation = next(
            node for node in cls.tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "compute_fair_value_zone"
        )

    def test_petroleum_has_independent_fundamental_branch(self):
        source = ast.get_source_segment(
            Path(__file__).with_name("price_api.py").read_text(encoding="utf-8"),
            self.evaluate,
        )
        self.assertIn('is_petroleum =', source)
        self.assertIn('elif is_petroleum:', source)
        self.assertIn('Cơ cấu thượng nguồn/trung nguồn/hạ nguồn (10 điểm)', source)
        self.assertIn('Biên lợi nhuận chuẩn hóa giữa chu kỳ (12 điểm)', source)
        self.assertIn('Trữ lượng, vòng đời mỏ và hiệu quả capex (8 điểm)', source)
        self.assertIn('Đòn bẩy tài chính (5 điểm)', source)
        self.assertIn('CFO và chất lượng lợi nhuận (5 điểm)', source)

    def test_petroleum_avoids_spot_cycle_pe_valuation(self):
        source = ast.get_source_segment(
            Path(__file__).with_name("price_api.py").read_text(encoding="utf-8"),
            self.evaluate,
        )
        valuation_source = ast.get_source_segment(
            Path(__file__).with_name("price_api.py").read_text(encoding="utf-8"),
            self.valuation,
        )
        self.assertIn('elif is_petroleum:', source)
        self.assertIn('Dầu khí: tránh P/E lợi nhuận giao ngay', source)
        self.assertIn('"PETROLEUM"', valuation_source)
        for group in (
            '"STEEL"', '"FERTILIZER"', '"NGUYEN_VAT_LIEU_CHU_KY"',
            '"TEXTILE"', '"RUBBER"', '"PLASTICS_CHEMICALS"', '"AGRI_PROCESSING"',
        ):
            with self.subTest(pb_only_group=group):
                self.assertIn(group, valuation_source)



class TestAllIndustryGroupsHaveExplicitScoringRoutes(unittest.TestCase):
    def test_every_configured_subsector_has_a_dedicated_or_explicit_parent_route(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        tree = ast.parse(source, filename="price_api.py")
        evaluate = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "evaluate_stock_from_db"
        )
        body = ast.get_source_segment(source, evaluate)
        route_tokens = {
            "TECHNOLOGY": "is_technology",
            "ENERGY": "is_energy",
            "INSURANCE": "is_insurance",
            "PETROLEUM": "is_petroleum",
            "MARITIME": "is_maritime",
            "AVIATION": "is_aviation",
            "CONSTRUCTION": "is_construction",
            "BUILDING_MATERIALS": "is_building_materials",
            "STEEL": "is_steel",
            "FERTILIZER": "is_fertilizer",
            "CONSUMER": "is_consumer",
            "AGRI": "is_agri",
            "HOLDING": "is_holding",
            "NGAN_HANG": 'sector == "NGAN_HANG"',
            "CHUNG_KHOAN": 'sector == "CHUNG_KHOAN"',
            "BAT_DONG_SAN": 'sector == "BAT_DONG_SAN"',
            "CONG_NGHE_BAN_LE": "is_retail",
            "DIEN_NUOC_HA_TANG": "is_utility",
            "NGUYEN_VAT_LIEU_CHU_KY": "is_cyclical_material",
            "PHARMA": "is_pharma",
            "TEXTILE": "is_textile",
            "RUBBER": "is_rubber",
            "PLASTICS_CHEMICALS": "is_plastics_chemicals",
            "AGRI_PROCESSING": "is_agri_processing",
        }
        for group, token in route_tokens.items():
            with self.subTest(group=group):
                self.assertIn(group, PROD["SUB_SECTOR_MAPPING"] | {
                    "NGAN_HANG": [], "CHUNG_KHOAN": [],
                    "BAT_DONG_SAN": [], "CONG_NGHE_BAN_LE": [],
                    "DIEN_NUOC_HA_TANG": [], "NGUYEN_VAT_LIEU_CHU_KY": [],
                })
                self.assertIn(token, body)



class TestAuditableFundamentalScorecardOutput(unittest.TestCase):
    def test_stock_result_exposes_fixed_weight_sector_scorecard(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        tree = ast.parse(source, filename="price_api.py")
        evaluator = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "evaluate_stock_from_db"
        )
        result_return = next(
            node for node in ast.walk(evaluator)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
            and any(
                isinstance(key, ast.Constant) and key.value == "fundamental_scorecard"
                for key in node.value.keys
            )
        )
        keys = {
            key.value for key in result_return.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        self.assertIn("fundamental_scorecard", keys)
        scorecard_node = next(
            value for key, value in zip(result_return.value.keys, result_return.value.values)
            if isinstance(key, ast.Constant) and key.value == "fundamental_scorecard"
        )
        scorecard_keys = {
            key.value for key in scorecard_node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        self.assertTrue({
            "version", "profile_id", "status", "score", "max_score",
            "available_weight", "missing_weight", "coverage_pct",
            "missing_groups", "notes", "production_profile_approved",
            "profile_status", "profile_version", "profile_weights",
            "required_metric_groups", "profile_data_gaps",
        }.issubset(scorecard_keys))

    def test_scorecard_keeps_unverified_industry_metrics_visible(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        self.assertIn('"missing_groups": list(fundamental_missing_groups)', source)
        self.assertIn('"production_profile_approved": False', source)
        self.assertIn('"profile_status":', source)
        self.assertIn('"required_metric_groups":', source)
        self.assertIn('"profile_data_gaps":', source)
        self.assertIn('"max_score": 50', source)
        self.assertIn('"missing_weight": max(0, 50 - int(fundamental_score_available))', source)


class TestRunnableContainerConfiguration(unittest.TestCase):
    def test_dockerfile_starts_the_actual_flask_module(self):
        dockerfile = Path(__file__).with_name("Dockerfile").read_text(encoding="utf-8")
        command_lines = [line for line in dockerfile.splitlines() if line.strip().startswith("CMD ")]
        self.assertEqual(len(command_lines), 1)
        self.assertIn("price_api:app", command_lines[0])
        self.assertNotIn("main:app", command_lines[0])
        self.assertIn("PORT:-8080", command_lines[0])

    def test_trial_instructions_document_required_database_setting(self):
        readme = Path(__file__).with_name("README.md").read_text(encoding="utf-8")
        self.assertIn("MONGO_URI", readme)
        self.assertIn("/healthz", readme)


class TestSingleScoreBctcProjection(unittest.TestCase):
    def test_single_score_preserves_vci_source_for_cfo_quarterization(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        tree = ast.parse(source, filename="price_api.py")
        route = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "api_score_single"
        )
        projections = []
        for node in ast.walk(route):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if (
                node.func.attr == "find_one"
                and isinstance(owner, ast.Name)
                and owner.id == "bctc_col"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Dict)
            ):
                projections.append({
                    key.value for key in node.args[1].keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                })
        self.assertEqual(len(projections), 1)
        self.assertIn(
            "source",
            projections[0],
            "Single-symbol scoring must retain source=VCI_DIRECT so YTD CFO can be quarterized.",
        )


class TestBctcCfoYtdQuarterization(unittest.TestCase):
    @staticmethod
    def _sample_vci_bctc(source="VCI_DIRECT"):
        periods = ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"]
        income = []
        for period, revenue, profit in zip(
            periods, [1000, 1100, 1200, 1300], [100, 110, 120, 130]
        ):
            income.extend([
                {"id": "IS_NET_REVENUE", "period": period, "value": revenue},
                {"id": "IS_NET_PROFIT_AFTER_TAX", "period": period, "value": profit},
            ])
        cashflow = [
            {"id": "CF_NET_CASH_FLOWS_FROM_OPERATING_ACTIVITIES", "period": period, "value": value}
            for period, value in zip(periods, [100e9, 250e9, 450e9, 700e9])
        ]
        return {
            "source": source,
            "income_statement": income,
            "cash_flow": cashflow,
            "balance_sheet": [],
        }

    def test_vci_ytd_cfo_is_quarterized_before_ttm_sum(self):
        metrics = PROD["extract_metrics_from_bctc_data"](
            self._sample_vci_bctc()
        )
        self.assertTrue(metrics["bctc_cfo_ttm_complete"])
        self.assertEqual(metrics["cfo_latest_quarter"], 250.0)
        self.assertEqual(metrics["cfo_ttm"], 700.0)

    def test_unknown_cashflow_source_does_not_assume_ytd_basis(self):
        metrics = PROD["extract_metrics_from_bctc_data"](
            self._sample_vci_bctc(source="UNKNOWN")
        )
        self.assertFalse(metrics["bctc_cfo_ttm_complete"])
        self.assertEqual(metrics["cfo_ttm"], 0.0)


class TestFullScreenerBctcProjection(unittest.TestCase):
    def test_full_screener_preload_preserves_vci_source_for_cfo(self):
        source = Path(__file__).with_name("price_api.py").read_text(encoding="utf-8")
        tree = ast.parse(source, filename="price_api.py")
        loader = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_load_screener_bctc"
        )
        projections = []
        for node in ast.walk(loader):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "find"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Dict)
            ):
                projections.append({
                    key.value for key in node.args[1].keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                })
        self.assertEqual(len(projections), 1)
        self.assertIn(
            "source",
            projections[0],
            "Full Screener preload must preserve source=VCI_DIRECT for YTD CFO quarterization.",
        )
