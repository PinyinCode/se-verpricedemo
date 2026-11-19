import unittest

from macro_data import _HTMLText, format_macro_context


class MacroDataTests(unittest.TestCase):
    def test_html_parser_extracts_anchor_and_article_text(self):
        parser = _HTMLText()
        parser.feed(
            '<html><nav>menu hidden</nav><main><h1>CPI tháng 10</h1>'
            '<a href="/report">Chỉ số giá tiêu dùng</a>'
            '<p>CPI tăng 5,09% so với cùng kỳ.</p></main></html>'
        )
        self.assertIn((" /report".strip(), "Chỉ số giá tiêu dùng"), parser.links)
        self.assertIn("CPI tháng 10", parser.text)
        self.assertIn("CPI tăng 5,09%", parser.text)
        self.assertNotIn("menu hidden", parser.text)

    def test_format_macro_context_keeps_period_source_and_warning(self):
        bundle = {
            "fetched_at": "2026-11-19T04:00:00+00:00",
            "facts": [{
                "category": "CPI",
                "title": "CPI Việt Nam",
                "period_or_date": "10/2026",
                "excerpt": "CPI tăng 5,09% YoY.",
                "source_name": "Cục Thống kê Việt Nam",
                "source": "https://www.nso.gov.vn/example",
            }],
            "warnings": ["Thiếu dữ liệu lãi suất."],
        }
        result = format_macro_context(bundle)
        self.assertIn("10/2026", result)
        self.assertIn("5,09%", result)
        self.assertIn("https://www.nso.gov.vn/example", result)
        self.assertIn("Thiếu dữ liệu lãi suất", result)


if __name__ == "__main__":
    unittest.main()
