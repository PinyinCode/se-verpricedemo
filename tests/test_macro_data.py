import unittest
from datetime import datetime, timezone

import macro_data


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


if __name__ == "__main__":
    unittest.main()
