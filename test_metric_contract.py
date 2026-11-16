# -*- coding: utf-8 -*-
import unittest

from metric_contract import build_metric_record, validate_metric_record


class TestMetricContract(unittest.TestCase):
    def test_builds_verified_record_with_lineage(self):
        record = build_metric_record(
            "ROE", {"available": True, "value": 15.2},
            "percent", "2026-Q3_TTM", "TTM", ["isa20", "bsa78"],
        )
        self.assertTrue(record["available"])
        self.assertEqual(record["source_fields"], ["bsa78", "isa20"])
        self.assertTrue(validate_metric_record(record)["valid"])

    def test_missing_unit_or_lineage_makes_record_unavailable(self):
        missing_unit = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "", "2026-Q3_TTM", "TTM", ["isa20"],
        )
        self.assertFalse(missing_unit["available"])
        self.assertEqual(missing_unit["reason"], "unit_missing")
        missing_sources = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "percent", "2026-Q3_TTM", "TTM", [],
        )
        self.assertFalse(missing_sources["available"])
        self.assertEqual(missing_sources["reason"], "source_lineage_missing")



    def test_metric_id_is_required_and_must_match_expected_spec(self):
        missing_id = build_metric_record(
            "", {"available": True, "value": 15},
            "percent", "2026-Q3_TTM", "TTM", ["isa20"],
        )
        self.assertFalse(missing_id["available"])
        self.assertEqual(missing_id["reason"], "metric_id_missing")
        valid = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "percent", "2026-Q3_TTM", "TTM", ["isa20"],
        )
        self.assertEqual(
            validate_metric_record(valid, {"metric_id": "ROA"})["reason"],
            "metric_id_mismatch",
        )

    def test_source_lineage_must_be_an_explicit_collection(self):
        record = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "percent", "2026-Q3_TTM", "TTM", "isa20",
        )
        self.assertFalse(record["available"])
        self.assertEqual(record["reason"], "source_lineage_missing")
        valid_record = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "percent", "2026-Q3_TTM", "TTM", ["isa20"],
        )
        malformed = dict(valid_record, source_fields="isa20")
        self.assertEqual(
            validate_metric_record(malformed)["reason"],
            "metric_source_lineage_invalid",
        )
        self.assertEqual(
            validate_metric_record(valid_record, {
                "source_fields": "isa20",
            })["reason"],
            "expected_source_lineage_invalid",
        )

    def test_quality_issues_prevent_verified_status(self):
        record = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "percent", "2026-Q3_TTM", "TTM", ["isa20"],
            quality_issues=["equity_average_missing"],
        )
        self.assertFalse(record["available"])
        self.assertEqual(record["reason"], "quality_issues_present")


    def test_malformed_quality_issue_collection_blocks_verification(self):
        record = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "percent", "2026-Q3_TTM", "TTM", ["isa20"],
            quality_issues="missing_equity",
        )
        self.assertFalse(record["available"])
        self.assertEqual(record["reason"], "quality_issues_present")
        self.assertIn("quality_issues_invalid", record["quality_issues"])


    def test_validation_rejects_missing_metric_id_and_quality_issues(self):
        record = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "percent", "2026-Q3_TTM", "TTM", ["isa20", "bsa78"],
        )
        self.assertEqual(
            validate_metric_record(dict(record, metric_id=""))["reason"],
            "metric_id_missing",
        )
        self.assertEqual(
            validate_metric_record(dict(record, quality_issues="none"))["reason"],
            "metric_quality_issues_present",
        )
        self.assertEqual(
            validate_metric_record(dict(record, quality_issues=["bad_source"]))["reason"],
            "metric_quality_issues_present",
        )



    def test_catalogued_metric_lineage_and_basis_cannot_be_overridden(self):
        wrong_source = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "percent", "2026-Q3_TTM", "TTM", ["isa22", "bsa78"],
        )
        self.assertEqual(
            validate_metric_record(wrong_source)["reason"],
            "metric_catalog_source_lineage_mismatch",
        )
        wrong_basis = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "percent", "2026-Q3", "QUARTERLY", ["isa20", "bsa78"],
        )
        self.assertEqual(
            validate_metric_record(wrong_basis)["reason"],
            "metric_catalog_period_basis_mismatch",
        )


    def test_unavailable_or_uncatalogued_metrics_cannot_become_verified(self):
        npl = build_metric_record(
            "NPL_RATIO", {"available": True, "value": 1.2},
            "percent", "2026-Q3", "POINT_IN_TIME", ["bsb104"],
        )
        self.assertEqual(
            validate_metric_record(npl)["reason"],
            "metric_catalog_source_not_verified",
        )
        unknown = build_metric_record(
            "MADE_UP_METRIC", {"available": True, "value": 1.2},
            "percent", "2026-Q3_TTM", "TTM", ["isa20"],
        )
        self.assertEqual(
            validate_metric_record(unknown)["reason"],
            "metric_not_in_canonical_catalog",
        )


    def test_unavailable_catalog_metric_cannot_become_scoreable_by_payload(self):
        record = build_metric_record(
            "FCF", {"available": True, "value": 60},
            "VND", "2026-Q3_TTM", "TTM", ["cfa18", "cfa19"],
        )
        self.assertTrue(record["available"])
        self.assertEqual(
            validate_metric_record(record)["reason"],
            "metric_catalog_source_not_verified",
        )

    def test_unknown_metric_id_is_not_scoreable(self):
        record = build_metric_record(
            "MADE_UP_METRIC", {"available": True, "value": 10},
            "ratio", "2026-Q3_TTM", "TTM", ["isa20"],
        )
        self.assertTrue(record["available"])
        self.assertEqual(
            validate_metric_record(record)["reason"],
            "metric_not_in_canonical_catalog",
        )

    def test_spec_requires_exact_metric_identity(self):
        record = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "percent", "2026-Q3_TTM", "TTM", ["isa20", "bsa78"],
        )
        self.assertTrue(validate_metric_record(
            record, {"metric_id": "ROE"}
        )["valid"])
        self.assertEqual(
            validate_metric_record(record, {"metric_id": "ROA"})["reason"],
            "metric_id_mismatch",
        )


    def test_catalogued_metric_cannot_substitute_or_extend_source_lineage(self):
        valid = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "percent", "2026-Q3_TTM", "TTM", ["isa20", "bsa78"],
        )
        self.assertTrue(validate_metric_record(valid)["valid"])
        wrong_profit = dict(valid, source_fields=["isa22", "bsa78"])
        self.assertEqual(
            validate_metric_record(wrong_profit)["reason"],
            "metric_catalog_source_lineage_mismatch",
        )
        extra_source = dict(valid, source_fields=["isa20", "bsa78", "isa22"])
        self.assertEqual(
            validate_metric_record(extra_source)["reason"],
            "metric_catalog_source_lineage_mismatch",
        )

    def test_catalogued_metric_cannot_override_period_basis(self):
        record = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "percent", "2026-Q3_TTM", "TTM", ["isa20", "bsa78"],
        )
        wrong_basis = dict(record, period_basis="QUARTERLY")
        self.assertEqual(
            validate_metric_record(wrong_basis)["reason"],
            "metric_catalog_period_basis_mismatch",
        )


    def test_catalogued_metric_unit_cannot_be_overridden(self):
        record = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "percent", "2026-Q3_TTM", "TTM", ["isa20", "bsa78"],
        )
        wrong_unit = dict(record, unit="VND")
        self.assertEqual(
            validate_metric_record(wrong_unit)["reason"],
            "metric_catalog_unit_mismatch",
        )

    def test_spec_checks_period_unit_and_source_fields(self):
        record = build_metric_record(
            "ROE", {"available": True, "value": 15},
            "percent", "2026-Q3_TTM", "TTM", ["isa20", "bsa78"],
        )
        spec = {
            "period_basis": "TTM", "unit": "percent",
            "source_fields": ["isa20", "bsa78"],
        }
        self.assertTrue(validate_metric_record(record, spec)["valid"])
        self.assertEqual(
            validate_metric_record(record, dict(spec, unit="VND"))["reason"],
            "metric_unit_mismatch",
        )
        self.assertEqual(
            validate_metric_record(record, dict(spec, period_basis="YTD"))["reason"],
            "period_basis_mismatch_or_unverified",
        )


if __name__ == "__main__":
    unittest.main()
