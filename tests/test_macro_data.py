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
        self.assertTrue(any("thép" in q.lower() for _, q in steel))
        self.assertTrue(any("tín dụng" in q.lower() for _, q in banks))
        self.assertNotEqual(steel, banks)

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
        xml = b\'''<?xml version="1.0"?>
        <StructureSpecificData xmlns="urn:test">
          <DataSet>
            <Series FREQ="M" REF_AREA="VNM" INDICATOR="CPI">
              <Obs TIME_PERIOD="2026-09" OBS_VALUE="105.2"/>
              <Obs TIME_PERIOD="2026-10" OBS_VALUE="105.8"/>
            </Series>
          </DataSet>
        </StructureSpecificData>\'''
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
        finally:
            macro_data._fetch_global_bundle = original_global
            macro_data._fetch_industry_pack = original_industry
            for key in list(macro_data._CACHE):
                if key == "GLOBAL_VIETNAM_MACRO" or key.startswith("INDUSTRY:"):
                    macro_data._CACHE.pop(key, None)


if __name__ == "__main__":
    unittest.main()
