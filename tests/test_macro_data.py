import unittest

import macro_data


class _FakeCache:
    def __init__(self):
        self.docs = {}

    def find_one(self, query):
        return self.docs.get(query.get("_id"))

    def update_one(self, query, update, upsert=False):
        key = query["_id"]
        self.docs[key] = {
            "_id": key,
            "payload": update["$set"]["payload"],
            "fetched_epoch": update["$set"]["fetched_epoch"],
        }


class MacroDataTests(unittest.TestCase):
    def test_article_parser_extracts_meta_date_and_article_text(self):
        parser = macro_data._ArticleParser()
        parser.feed(
            '<html><nav>menu hidden</nav><head>'
            '<meta property="article:published_time" content="2026-11-03T01:00:00Z">'
            '</head><main><h1>CPI tháng 10</h1>'
            '<p>CPI tăng 5,09% so với cùng kỳ.</p></main></html>'
        )
        self.assertEqual(
            parser.meta["article:published_time"], "2026-11-03T01:00:00Z"
        )
        self.assertIn("CPI tháng 10", parser.text)
        self.assertIn("CPI tăng 5,09%", parser.text)
        self.assertNotIn("menu hidden", parser.text)

    def test_industry_queries_are_derived_from_industry_label_not_ticker(self):
        steel = macro_data._industry_queries("Thép và vật liệu", "OTHER")
        banks = macro_data._industry_queries("Ngân hàng", "OTHER")
        self.assertTrue(any('"Thép và vật liệu"' in q for _, q in steel))
        self.assertTrue(any('"Ngân hàng"' in q for _, q in banks))
        self.assertTrue(any("sản lượng sản xuất" in q for _, q in steel))
        self.assertTrue(any("giá nguyên liệu" in q for _, q in banks))
        self.assertNotEqual(steel, banks)
        # A newly added industry label is handled without adding a sector rule.
        new_sector = macro_data._industry_queries("Dịch vụ giáo dục", None)
        self.assertTrue(new_sector)
        self.assertTrue(all("Dịch vụ giáo dục" in q for _, q in new_sector))

    def test_reference_period_extraction_distinguishes_period_from_publish_date(self):
        self.assertEqual(macro_data._extract_reference_period("CPI tháng 10 năm 2026"), "10-2026")
        self.assertEqual(macro_data._extract_reference_period("Sản xuất công nghiệp 9 tháng đầu năm 2026"), "9 tháng năm 2026")
        self.assertEqual(macro_data._extract_reference_period("GDP quý III/2026"), "QIII-2026")
        self.assertEqual(macro_data._extract_reference_period("Báo cáo năm 2025"), "2025")
        self.assertIsNone(macro_data._extract_reference_period("Công bố ngày 19/11/2026"))

    def test_industry_queries_are_categorized_across_common_business_dimensions(self):
        queries = macro_data._industry_queries("Dịch vụ logistics", "Vận tải")
        categories = {category for category, _ in queries}
        self.assertIn("INDUSTRY_OUTPUT", categories)
        self.assertIn("INDUSTRY_PRICING", categories)
        self.assertIn("INDUSTRY_DEMAND", categories)
        self.assertIn("INDUSTRY_TRADE", categories)
        self.assertIn("INDUSTRY_CAPEX", categories)
        self.assertTrue(all("Dịch vụ logistics" in query for _, query in queries))

    def test_format_macro_context_keeps_period_source_and_freshness(self):
        bundle = {
            "fetched_at": "2026-11-19T04:00:00+00:00",
            "industry": "Thép",
            "coverage": {"global_fact_count": 2, "industry_fact_count": 1},
            "facts": [{
                "category": "CPI",
                "title": "CPI Việt Nam",
                "period_or_date": "10/2026",
                "published_at": "2026-11-03",
                "freshness": "RECENT_PUBLICATION",
                "excerpt": "CPI tăng 5,09% YoY.",
                "source_name": "Cục Thống kê Việt Nam",
                "source": "https://www.nso.gov.vn/example",
            }],
            "warnings": ["Thiếu dữ liệu lãi suất."],
        }
        result = macro_data.format_macro_context(bundle)
        self.assertIn("10/2026", result)
        self.assertIn("5,09%", result)
        self.assertIn("2026-11-03", result)
        self.assertIn("RECENT_PUBLICATION", result)
        self.assertIn("https://www.nso.gov.vn/example", result)
        self.assertIn("Thiếu dữ liệu lãi suất", result)

    def test_nsdp_parser_discovers_sdmx_download_links(self):
        parser = macro_data._NSDPTableParser()
        parser.feed(
            '<table><tr><td>Interest Rates</td>'
            '<td><a href="/rates.xml">Download in SDMX</a></td></tr></table>'
        )
        row = parser.rows[0]
        self.assertEqual(row[0]["text"], "Interest Rates")
        self.assertEqual(row[1]["links"][0]["href"], "/rates.xml")
        self.assertEqual(macro_data._nsdp_category(row[0]["text"]), "INTEREST_RATE")

    def test_sdmx_parser_extracts_latest_observations_and_series_key(self):
        xml = b'<StructureSpecificData xmlns="urn:test"><DataSet><Series FREQ="M" REF_AREA="VNM" INDICATOR="CPI"><Obs TIME_PERIOD="2026-09" OBS_VALUE="105.2"/><Obs TIME_PERIOD="2026-10" OBS_VALUE="105.8"/></Series></DataSet></StructureSpecificData>'
        facts = macro_data._parse_sdmx_observations(
            xml, "Consumer Price Index", "CPI", "https://example.test/cpi.xml"
        )
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["period_or_date"], "2026-10")
        self.assertIn("FREQ=M", facts[0]["series_key"])
        self.assertIn("2026-10: 105.8", facts[0]["excerpt"])
        self.assertEqual(facts[0]["freshness"], "OBSERVATION_PERIOD_VERIFIED")

    def test_world_bank_indicator_catalog_has_unique_codes(self):
        codes = [item[1] for item in macro_data.WORLD_BANK_INDICATORS]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertIn("NY.GDP.MKTP.KD.ZG", codes)
        self.assertIn("PA.NUS.FCRF", codes)

    def test_shared_cache_fetches_once_per_key(self):
        key = "TEST_MACRO_CACHE"
        macro_data._CACHE.pop(key, None)
        calls = []
        def fetch():
            calls.append(1)
            return {"facts": [{"title": "test"}]}
        first = macro_data._get_cached_bundle(key, fetch)
        second = macro_data._get_cached_bundle(key, fetch)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first, second)
        macro_data._CACHE.pop(key, None)

    def test_industry_coverage_report_distinguishes_missing_stale_and_verified(self):
        missing = macro_data._summarize_industry_coverage("Giáo dục", {"facts": [], "warnings": ["Không có nguồn"]})
        stale = macro_data._summarize_industry_coverage("Dệt may", {"facts": [{
            "source": "https://example.test/old", "source_name": "Nguồn A",
            "freshness": "STALE_PUBLICATION", "published_at": "2024-01-01",
            "period_or_date": "2023",
        }]})
        covered = macro_data._summarize_industry_coverage("Ngân hàng", {"facts": [{
            "source": "https://example.test/new", "source_name": "Nguồn B",
            "freshness": "OBSERVATION_PERIOD_VERIFIED",
            "period_or_date": "2026-10",
        }]})
        self.assertEqual(missing["status"], "MISSING")
        self.assertEqual(stale["status"], "STALE")
        self.assertEqual(covered["status"], "COVERED")
        self.assertEqual(covered["source_count"], 1)
        self.assertEqual(covered["dated_fact_count"], 1)

    def test_scheduled_refresh_builds_global_and_each_distinct_industry_once(self):
        original_global = macro_data._fetch_global_bundle
        original_industry = macro_data._fetch_industry_pack
        cache = _FakeCache()
        calls = []
        try:
            macro_data._fetch_global_bundle = lambda: {
                "facts": [{"title": "macro"}], "warnings": []
            }
            def fake_industry(industry, sector):
                calls.append(industry)
                return ([{"title": industry}], [])
            macro_data._fetch_industry_pack = fake_industry
            result = macro_data.refresh_macro_contexts(
                [("Thép", "MATERIALS"), ("Thép", "MATERIALS"), ("Ngân hàng", "BANK")],
                cache_store=cache,
                force_refresh=True,
            )
            self.assertEqual(result["global_facts"], 1)
            self.assertEqual(len(result["industries"]), 2)
            self.assertEqual(len(calls), 2)
            self.assertIn("GLOBAL_VIETNAM_MACRO", cache.docs)
            self.assertIn("MACRO_COVERAGE_REPORT", cache.docs)
            self.assertTrue(result["coverage_report_persisted"])
            report = cache.docs["MACRO_COVERAGE_REPORT"]["payload"]
            self.assertEqual(report["coverage"]["industry_count"], 2)
            self.assertEqual(len(report["industries"]), 2)
        finally:
            macro_data._fetch_global_bundle = original_global
            macro_data._fetch_industry_pack = original_industry
            for key in list(macro_data._CACHE):
                if key == "GLOBAL_VIETNAM_MACRO" or key.startswith("INDUSTRY:"):
                    macro_data._CACHE.pop(key, None)


if __name__ == "__main__":
    unittest.main()
