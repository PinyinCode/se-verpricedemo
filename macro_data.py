# -*- coding: utf-8 -*-
"""Verified macro/industry context for Vietnamese listed companies.

Only extracts dated facts from primary/public sources. Missing or stale data is
reported as missing; this module never manufactures estimates or directional
signals. Results are cached in-process to avoid repeated external requests.
"""
from __future__ import annotations

import calendar
from io import BytesIO
import html
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

import pandas as pd
import requests


NSO_LIST_URL = "https://www.nso.gov.vn/du-lieu-va-so-lieu-thong-ke/"
WORLD_BANK_MONTHLY_URL = (
    "https://thedocs.worldbank.org/en/doc/"
    "74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/"
    "CMO-Historical-Data-Monthly.xlsx"
)
CACHE_TTL_SECONDS = 12 * 60 * 60
REQUEST_TIMEOUT = (8, 25)
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; VNFinancialResearch/1.0)"}
_CACHE = {}
_CACHE_LOCK = threading.Lock()


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self._anchor_href = None
        self._anchor_text = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style", "nav", "footer", "header", "svg"):
            self._skip += 1
        if tag == "a":
            self._anchor_href = attrs.get("href")
            self._anchor_text = []
        if tag in ("p", "br", "li", "tr", "h1", "h2", "h3", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header", "svg") and self._skip:
            self._skip -= 1
        if tag == "a" and self._anchor_href:
            label = " ".join(" ".join(self._anchor_text).split())
            if label:
                self.links.append((self._anchor_href, label))
            self._anchor_href = None
            self._anchor_text = []
        if tag in ("p", "li", "tr", "h1", "h2", "h3", "div"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        value = html.unescape(data).strip()
        if value:
            self.parts.append(value + " ")
            if self._anchor_href is not None:
                self._anchor_text.append(value)

    @property
    def text(self):
        return re.sub(r"\n{3,}", "\n\n", " ".join(self.parts)).strip()


def _get(url):
    response = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response


def _clean_excerpt(value, limit=1500):
    value = re.sub(r"\s+", " ", value or "").strip()
    return value[:limit].rstrip()


def _nso_category_links():
    response = _get(NSO_LIST_URL)
    parser = _HTMLText()
    parser.feed(response.text)
    groups = {
        "CPI": ("chỉ số giá tiêu dùng",),
        "IIP": ("chỉ số sản xuất công nghiệp",),
        "GDP": ("tăng trưởng GDP", "tổng sản phẩm trong nước"),
        "CONSTRUCTION": ("hoạt động xây dựng", "đầu tư thực hiện từ nguồn ngân sách"),
    }
    found = {}
    for key, terms in groups.items():
        for href, label in parser.links:
            low = label.lower()
            if any(term in low for term in terms):
                url = urljoin(NSO_LIST_URL, href)
                if "nso.gov.vn" in url:
                    found[key] = (url, label)
                    break
    return found


def _fetch_nso_facts():
    facts = []
    try:
        links = _nso_category_links()
    except Exception as exc:
        return facts, [f"Cục Thống kê: không truy xuất được danh mục dữ liệu ({type(exc).__name__})."]

    for category, (url, title) in links.items():
        try:
            response = _get(url)
            parser = _HTMLText()
            parser.feed(response.text)
            body = parser.text
            date_match = re.search(r"(?:Ngày đăng|Kỳ tham chiếu)\s*:\s*([0-9/]+)", body, re.I)
            date_label = date_match.group(1) if date_match else "không đọc được ngày công bố"
            title_pos = body.lower().find(title.lower())
            excerpt = body[title_pos + len(title):] if title_pos >= 0 else body
            excerpt = _clean_excerpt(excerpt, 1250)
            if excerpt:
                facts.append({
                    "category": category,
                    "title": title,
                    "period_or_date": date_label,
                    "excerpt": excerpt,
                    "source": url,
                    "source_name": "Cục Thống kê Việt Nam",
                })
        except Exception as exc:
            facts.append({
                "category": category,
                "title": title,
                "error": f"không tải được ({type(exc).__name__})",
                "source": url,
                "source_name": "Cục Thống kê Việt Nam",
            })
    return facts, []


def _previous_months(count=5):
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    result = []
    for _ in range(count):
        month -= 1
        if month == 0:
            year -= 1
            month = 12
        result.append((year, month))
    return result


def _fetch_worldsteel_facts():
    facts = []
    for year, month in _previous_months(5):
        month_name = calendar.month_name[month].lower()
        url = (
            f"https://worldsteel.org/media/press-releases/{year}/"
            f"{month_name}-{year}-crude-steel-production/"
        )
        try:
            response = _get(url)
            parser = _HTMLText()
            parser.feed(response.text)
            body = parser.text
            china = re.search(
                r"China produced\s+([\d,.]+)\s*Mt\s+in\s+([A-Za-z]+\s+\d{4}),\s*"
                r"(up|down)\s+([\d,.]+)%",
                body, re.I
            )
            vietnam = re.search(
                r"Viet\s*Nam produced\s+([\d,.]+)\s*Mt\s+in\s+([A-Za-z]+\s+\d{4}),\s*"
                r"(up|down)\s+([\d,.]+)%",
                body, re.I
            )
            if china or vietnam:
                for country, match in (("Trung Quốc", china), ("Việt Nam", vietnam)):
                    if match:
                        direction = "tăng" if match.group(3).lower() == "up" else "giảm"
                        facts.append({
                            "category": "STEEL_PRODUCTION",
                            "title": f"Sản lượng thép thô {country}",
                            "period_or_date": match.group(2),
                            "excerpt": f"{country}: {match.group(1)} triệu tấn, {direction} {match.group(4)}% so với cùng kỳ.",
                            "source": url,
                            "source_name": "World Steel Association (worldsteel)",
                        })
                return facts
        except requests.HTTPError as exc:
            if getattr(exc.response, "status_code", None) not in (404, 403):
                continue
        except Exception:
            continue
    return facts


def _read_world_bank_commodities():
    """Read monthly commodity series from the World Bank Pink Sheet workbook."""
    response = _get(WORLD_BANK_MONTHLY_URL)
    sheets = pd.read_excel(BytesIO(response.content), sheet_name=None, header=None)
    wanted = {
        "iron_ore": ("iron ore",),
        "coal_australia": ("coal, australia", "coal australia"),
        "steel_rebar": ("steel (rebar)", "steel rebar"),
    }
    output = []
    for sheet_name, frame in sheets.items():
        if frame is None or frame.empty:
            continue
        header_row = None
        col_map = {}
        for row_idx in range(min(20, len(frame))):
            values = [str(v).strip().lower() for v in frame.iloc[row_idx].tolist()]
            for key, aliases in wanted.items():
                for col_idx, value in enumerate(values):
                    if any(alias in value for alias in aliases):
                        col_map[key] = col_idx
            if "iron_ore" in col_map or "coal_australia" in col_map or "steel_rebar" in col_map:
                header_row = row_idx
                break
        if header_row is None:
            continue

        date_col = 0
        date_rows = []
        for row_idx in range(header_row + 1, len(frame)):
            raw_date = str(frame.iat[row_idx, date_col]).strip()
            match = re.search(r"(20\d{2})\s*M\s*(0?[1-9]|1[0-2])", raw_date, re.I)
            if match:
                date_rows.append((int(match.group(1)), int(match.group(2)), row_idx))
        if not date_rows:
            continue
        date_rows.sort()
        latest = date_rows[-1]
        previous = date_rows[-2] if len(date_rows) > 1 else None
        for key, col_idx in col_map.items():
            current_value = pd.to_numeric(pd.Series([frame.iat[latest[2], col_idx]]), errors="coerce").iloc[0]
            previous_value = (
                pd.to_numeric(pd.Series([frame.iat[previous[2], col_idx]]), errors="coerce").iloc[0]
                if previous else float("nan")
            )
            if pd.isna(current_value):
                continue
            change = None
            if previous and pd.notna(previous_value) and float(previous_value) != 0:
                change = (float(current_value) / float(previous_value) - 1) * 100
            names = {
                "iron_ore": "Quặng sắt benchmark",
                "coal_australia": "Than Australia benchmark (không đồng nhất với giá than luyện cốc HPG mua thực tế)",
                "steel_rebar": "Thép thanh benchmark",
            }
            excerpt = f"{names[key]}: {float(current_value):,.2f} USD/tấn hoặc đơn vị công bố trong Pink Sheet; kỳ {latest[0]}-{latest[1]:02d}."
            if change is not None:
                excerpt += f" So với tháng trước: {change:+.2f}%."
            output.append({
                "category": "COMMODITY_PRICE",
                "title": names[key],
                "period_or_date": f"{latest[0]}-{latest[1]:02d}",
                "excerpt": excerpt,
                "source": WORLD_BANK_MONTHLY_URL,
                "source_name": "World Bank Commodity Markets - Pink Sheet",
            })
        if output:
            return output
    return output


def _fetch_macro_bundle(symbol, sector):
    facts, warnings = _fetch_nso_facts()
    if str(symbol).upper() == "HPG" or str(sector).upper() == "NGUYEN_VAT_LIEU_CHU_KY":
        facts.extend(_fetch_worldsteel_facts())
        try:
            facts.extend(_read_world_bank_commodities())
        except Exception as exc:
            warnings.append(
                f"World Bank Pink Sheet: chưa đọc được dữ liệu hàng hóa ({type(exc).__name__}); "
                "không thay bằng ước tính."
            )
    if not facts:
        warnings.append("Không lấy được dữ kiện vĩ mô/ngành đủ điều kiện xác minh trong lần cập nhật này.")
    return {
        "symbol": str(symbol).upper(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "facts": facts,
        "warnings": warnings,
    }


def get_macro_context(symbol, sector, force_refresh=False):
    """Return dated, source-linked facts for AI prompts; cache successful bundles for 12h."""
    key = (str(symbol).upper(), str(sector).upper())
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and not force_refresh and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]

    bundle = _fetch_macro_bundle(symbol, sector)
    with _CACHE_LOCK:
        _CACHE[key] = (now, bundle)
    return bundle


def format_macro_context(bundle):
    """Format source facts for the model without converting them into conclusions."""
    if not isinstance(bundle, dict):
        return "Không có dữ liệu vĩ mô/ngành được truy xuất."
    lines = [
        f"Thời điểm truy xuất: {bundle.get('fetched_at', 'không rõ')}.",
        "DỮ KIỆN VĨ MÔ/NGÀNH CÓ NGUỒN (chỉ được diễn giải đúng kỳ và đơn vị):",
    ]
    facts = bundle.get("facts") or []
    for fact in facts:
        if fact.get("excerpt"):
            lines.append(
                f"- [{fact.get('category')}] {fact.get('title')} | "
                f"Kỳ/ngày: {fact.get('period_or_date', 'không rõ')} | "
                f"{fact['excerpt']} | Nguồn: {fact.get('source_name')} | URL: {fact.get('source')}"
            )
        elif fact.get("error"):
            lines.append(
                f"- [{fact.get('category')}] {fact.get('title')}: {fact['error']} | "
                f"Nguồn: {fact.get('source')}"
            )
    for warning in bundle.get("warnings") or []:
        lines.append(f"- CẢNH BÁO: {warning}")
    if not facts:
        lines.append("- Không có dữ liệu định lượng đủ điều kiện xác minh.")
    return "\n".join(lines)
