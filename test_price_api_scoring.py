# -*- coding: utf-8 -*-
"""Runtime regression tests for the production scoring/valuation functions.

price_api.py starts a Flask/MongoDB service at import time, so these tests
compile only the pure production functions and their literal sector maps.
"""
import ast
import unittest
from pathlib import Path

import math


class _NP:
    isfinite = staticmethod(math.isfinite)


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
        "_get_bctc_optional_value",
        "_build_bctc_selected_fields_pipeline",
        "get_sector",
        "get_sub_sector",
        "compute_fair_value_zone",
        "compute_final_decision",
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

    namespace = {"np": _NP}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "price_api_scoring_extract.py", "exec"), namespace)
    return namespace


PROD = load_production_functions()



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
    def test_pipeline_filters_only_three_loan_fields_for_latest_period(self):
        pipeline = PROD["_build_bctc_selected_fields_pipeline"]()
        project = pipeline[1]["$project"]
        condition = project["balance_sheet"]["$filter"]["cond"]
        self.assertEqual(
            condition["$and"][0]["$in"][1],
            ["bsb103", "bsb104", "bsb105"],
        )
        self.assertEqual(condition["$and"][1]["$eq"], ["$$row.period", "$latest_period"])

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

    def test_bank_insurance_and_holding_use_pb_only_until_more_data_exists(self):
        for symbol in ("CTG", "BVH", "VIC", "GEX"):
            with self.subTest(symbol=symbol):
                _, result = self.fair_value(symbol)
                self.assertEqual(set(result["methods"]), {"PB_method"})

    def test_primary_metric_controls_available_valuation_methods(self):
        expected = {
            "FPT": {"PE_method", "PB_method", "Graham"},
            "KDH": {"PE_method", "PB_method"},
            "HPG": {"PE_method", "PB_method"},
            "DPM": {"PE_method", "PB_method", "Graham"},
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


class TestDecisionCoverageGuard(unittest.TestCase):
    def call_decision(self, coverage):
        return PROD["compute_final_decision"](
            fund_score=40, tech_score=16, val_score=25, total_score=81,
            verdict="RẺ - TRONG VÙNG MUA", cfo=100, npat=100,
            roe=15, debt_to_equity=0.5, discount_to_fair_pct=10,
            close_price=100, fair_high=120, fair_low=110,
            sector="CONG_NGHE_BAN_LE", fundamental_coverage_pct=coverage,
        )

    def test_coverage_below_65_blocks_buy_recommendation(self):
        label, _, reason = self.call_decision(64.9)
        self.assertEqual(label, "⚠️ THIẾU DỮ LIỆU")
        self.assertIn("64.9%", reason)

    def test_coverage_at_65_can_proceed_to_normal_decision_rules(self):
        label, _, _ = self.call_decision(65.0)
        self.assertNotEqual(label, "⚠️ THIẾU DỮ LIỆU")


if __name__ == "__main__":
    unittest.main()
