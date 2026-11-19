import unittest
from unittest.mock import patch

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
        self.assertTrue(any("Thép và vật liệu" in q for _, q in steel))
        self.assertTrue(any("Ngân hàng" in q for _, q in banks))
        self.assertTrue(any("sản lượng" in q for _, q in steel))
        self.assertTrue(any("giá" in q for _, q in banks))
        self.assertNotEqual(steel, banks)
        # A newly added industry label is handled without adding a sector rule.
        new_sector = macro_data._industry_queries("Dịch vụ giáo dục", None)
        self.assertTrue(new_sector)
        self.assertTrue(all("Dịch vụ giáo dục" in q for _, q in new_sector))

    def test_internal_industry_codes_expand_to_vietnamese_search_terms(self):
        agriculture = macro_data._industry_queries("AGRI", None)
        banks = macro_data._industry_queries("NGAN_HANG", None)
        property = macro_data._industry_queries("BAT_DONG_SAN", None)
        self.assertTrue(all("AGRI" not in query for _, query in agriculture))
        self.assertTrue(any("nông nghiệp" in query for _, query in agriculture))
        self.assertTrue(any("ngân hàng" in query for _, query in banks))
        self.assertTrue(any("bất động sản" in query for _, query in property))
        self.assertTrue(all(len(query.split()) <= 6 for _, query in agriculture))
        self.assertTrue(any(query == "nông nghiệp" for _, query in agriculture))
        self.assertIn(
            "NV.AGR.TOTL.KD.ZG",
            [item[1] for item in macro_data._industry_context_indicators("AGRI", None)],
        )
        self.assertIn(
            "NV.SRV.TETC.KD.ZG",
            [item[1] for item in macro_data._industry_context_indicators("NGAN_HANG", None)],
        )

    def test_macro_category_gate_rejects_unrelated_publications(self):
        self.assertTrue(macro_data._macro_category_matches_article(
            "IIP", "Chỉ số sản xuất công nghiệp tháng 10 năm 2026",
            "Sản xuất công nghiệp tăng 8,2%."
        ))
        self.assertFalse(macro_data._macro_category_matches_article(
            "IIP", "Nhập khẩu tăng cao hơn xuất khẩu",
            "Kim ngạch nhập khẩu tăng 12%."
        ))
        self.assertFalse(macro_data._macro_category_matches_article(
            "CONSTRUCTION", "Chiến lược phát triển thống kê",
            "Ứng dụng dữ liệu lớn trong thống kê."
        ))
        self.assertTrue(macro_data._macro_category_matches_article(
            "EXCHANGE_RATE", "Tỷ giá của đồng Việt Nam với đô la Mỹ",
            "Tỷ giá bình quân liên ngân hàng."
        ))

    def test_industry_article_requires_sector_match_and_quantitative_evidence(self):
        self.assertTrue(macro_data._industry_query_matches_article(
            "ngân hàng giá nguyên liệu",
            "Ngành ngân hàng Việt Nam tăng trưởng tín dụng",
            "Tín dụng tăng 8,5% trong kỳ báo cáo."
        ))
        self.assertFalse(macro_data._industry_query_matches_article(
            "ngân hàng giá nguyên liệu",
            "Chiến lược phát triển thống kê quốc gia",
            "Sản lượng công nghiệp tăng 5%."
        ))
        self.assertFalse(macro_data._industry_query_matches_article(
            "hàng không sản lượng sản xuất",
            "Tổng quan thị trường vận tải",
            "Doanh thu tăng 12 tỷ đồng."
        ))

    def test_publication_period_does_not_scan_entire_article(self):
        class FakeResponse:
            text = (
                "<html><head><meta property='article:published_time' "
                "content='2023-10-05T00:00:00Z'></head><main>"
                "<h1>Ngành nông nghiệp Việt Nam qua các thời kỳ</h1>"
                "<p>Bài viết tổng quan về lịch sử phát triển.</p>"
                + ("Thông tin tham khảo 9 tháng năm 2026. " * 40)
                + "</main></html>"
            )
        with patch.object(macro_data, "_get", return_value=FakeResponse()):
            fact = macro_data._fetch_nso_publication(
                {"link": "https://www.nso.gov.vn/agri", "title": "Ngành nông nghiệp Việt Nam qua các thời kỳ"},
                "PERIOD_TEST", "nông nghiệp"
            )
        self.assertEqual(
            fact["period_or_date"],
            "Chưa xác định kỳ tham chiếu từ metadata"
        )

    def test_quantitative_evidence_requires_a_value_and_measurement_unit(self):
        self.assertFalse(macro_data._has_quantitative_evidence("Báo cáo năm 2026"))
        self.assertFalse(macro_data._has_quantitative_evidence("Công bố ngày 19/11/2026"))
        self.assertTrue(macro_data._has_quantitative_evidence("Sản lượng tăng 5,2%"))
        self.assertTrue(macro_data._has_quantitative_evidence("Sản lượng đạt 1,2 triệu tấn"))

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

    def test_nso_discovery_falls_back_to_wordpress_posts_endpoint(self):
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload
            def json(self):
                return self.payload

        calls = []
        def fake_get(url, params=None):
            calls.append(url)
            if url == macro_data.NSO_SEARCH_URL:
                return FakeResponse([])
            return FakeResponse([{
                "id": 7,
                "date": "2026-11-18T08:00:00",
                "link": "https://www.nso.gov.vn/example/",
                "title": {"rendered": "Sản xuất công nghiệp tháng 10 năm 2026"},
            }])

        with patch.object(macro_data, "_get", side_effect=fake_get):
            items = macro_data._discover_nso_publications("sản xuất công nghiệp")
        self.assertEqual(len(items), 1)
        self.assertIn("/wp-json/wp/v2/posts", calls[-1])

    def test_nso_discovery_reports_endpoint_failures(self):
        diagnostics = []
        with patch.object(
            macro_data, "_get",
            side_effect=macro_data.requests.Timeout("timed out")
        ):
            items = macro_data._discover_nso_publications(
                "ngành hàng không", diagnostics=diagnostics
            )
        self.assertEqual(items, [])
        self.assertEqual(len(diagnostics), 1)
        self.assertIn("timeout", diagnostics[0]["errors"][0])
        self.assertIn("timeout", diagnostics[0]["errors"][1])

    def test_nso_search_pack_retries_same_url_under_next_candidate_category(self):
        item = {"link": "https://www.nso.gov.vn/shared", "title": "Báo cáo thống kê"}
        calls = []
        def fake_fetch(_item, category, query):
            calls.append(category)
            if category == "IIP":
                return None
            return {
                "category": category,
                "excerpt": "Kim ngạch xuất khẩu tăng 7%.",
                "source": item["link"],
            }
        with patch.object(
            macro_data, "_discover_nso_publications", return_value=[item]
        ), patch.object(macro_data, "_fetch_nso_publication", side_effect=fake_fetch):
            facts, _ = macro_data._fetch_nso_search_pack(
                [("IIP", "sản xuất công nghiệp"), ("TRADE", "xuất nhập khẩu")],
                "", per_query=1, limit=1
            )
        self.assertEqual(calls, ["IIP", "TRADE"])
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["category"], "TRADE")

    def test_nso_search_pack_does_not_count_unreadable_articles_as_facts(self):
        with patch.object(
            macro_data, "_discover_nso_publications",
            return_value=[{"link": "https://www.nso.gov.vn/broken", "title": "Bài lỗi"}]
        ), patch.object(
            macro_data, "_fetch_nso_publication",
            return_value={"error": "Không tải được bài nguồn (Timeout)."}
        ):
            facts, warnings = macro_data._fetch_nso_search_pack(
                [("INDUSTRY_OUTPUT", "sản lượng")], "", per_query=1, limit=1
            )
        self.assertEqual(facts, [])
        self.assertTrue(any("không tải/đọc được nội dung" in item for item in warnings))
        self.assertTrue(any("không có bài thống kê đủ nội dung" in item for item in warnings))

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

    def test_industry_context_indicators_are_selected_from_live_labels(self):
        bank = macro_data._industry_context_indicators("Ngân hàng", None)
        agriculture = macro_data._industry_context_indicators("Nông nghiệp", None)
        manufacturing = macro_data._industry_context_indicators("Thép và vật liệu", None)
        self.assertIn("FS.AST.PRVT.GD.ZS", [item[1] for item in bank])
        self.assertIn("NV.AGR.TOTL.KD.ZG", [item[1] for item in agriculture])
        self.assertIn("AG.YLD.CREL.KG", [item[1] for item in agriculture])
        self.assertIn("NV.IND.TOTL.KD.ZG", [item[1] for item in manufacturing])
        self.assertIn("NV.IND.MANF.KD.ZG", [item[1] for item in manufacturing])

    def test_sector_context_catalog_covers_multiple_live_industry_labels(self):
        cases = {
            "Hàng không": {"IS.AIR.PSGR"},
            "Hàng hải": {"IS.SHP.GOOD.TU"},
            "Dầu khí": {"TX.VAL.FUEL.ZS.UN"},
            "Điện nước": {"EG.ELC.ACCS.ZS"},
            "Bán lẻ": {"NE.CON.PRVT.KD.ZG"},
            "Chứng khoán": {"CM.MKT.LCAP.GD.ZS"},
            "Du lịch": {"ST.INT.ARVL"},
            "Công nghệ": {"IT.NET.USER.ZS"},
            "Bất động sản": {"NV.IND.CON.ZS"},
        }
        for label, expected in cases.items():
            with self.subTest(industry=label):
                actual = {item[1] for item in macro_data._industry_context_indicators(label, None)}
                self.assertTrue(expected.issubset(actual))

    def test_unknown_industry_does_not_receive_guessed_sector_indicators(self):
        self.assertEqual(
            macro_data._industry_context_indicators("Ngành mới chưa phân loại", None),
            []
        )

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
        covered = macro_data._summarize_industry_coverage("Ngân hàng", {"facts": [
            {
                "source": "https://example.test/new-a", "source_name": "Nguồn B",
                "freshness": "OBSERVATION_PERIOD_VERIFIED",
                "period_or_date": "2026-09", "quantitative_evidence": True,
            },
            {
                "source": "https://example.test/new-b", "source_name": "Nguồn B",
                "freshness": "OBSERVATION_PERIOD_VERIFIED",
                "period_or_date": "2026-10", "quantitative_evidence": True,
            },
        ]})
        contextual = macro_data._summarize_industry_coverage("Ngân hàng", {"facts": [{
            "source": "https://api.worldbank.org/example", "source_name": "World Bank",
            "freshness": "ANNUAL_SERIES", "period_or_date": "2024",
            "directness": "CONTEXTUAL", "evidence_scope": "INDUSTRY_CONTEXT",
            "quantitative_evidence": True,
        }]})
        self.assertEqual(missing["status"], "MISSING")
        self.assertEqual(stale["status"], "STALE")
        self.assertEqual(covered["status"], "COVERED")
        self.assertEqual(covered["source_count"], 1)
        self.assertEqual(covered["direct_fact_count"], 2)
        self.assertEqual(covered["dated_fact_count"], 2)
        self.assertEqual(contextual["status"], "MISSING")
        self.assertEqual(contextual["contextual_fact_count"], 1)
        self.assertEqual(contextual["direct_fact_count"], 0)

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
