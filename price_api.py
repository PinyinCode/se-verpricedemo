import os
import sys
import time
import json
import base64
import threading
import concurrent.futures
import logging
import numpy as np
import pandas as pd
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Set

import requests
from flask import Flask, jsonify, request, abort
from flask_cors import CORS
from flask_compress import Compress
from pymongo import MongoClient, UpdateOne
from pymongo.errors import PyMongoError
from bctc_rules import canonicalize_vci_field, resolve_vci_unit, SECTOR_RULES, DATA_QUALITY_RULES
from analysis_cache import TTLCache
from bank_asset_quality import (
    discover_bank_asset_quality_fields,
    discover_bank_asset_quality_metadata,
)
from industrial_metric_discovery import (
    annotate_industrial_dictionary_candidates,
    discover_industrial_fields,
    discover_industrial_metadata,
)
from financial_metric_adapter import build_bank_metric_bundle
from financial_metric_adapter import (
    build_industrial_metric_bundle,
    quarantine_metrics,
)
from data_quality import (
    get_vci_reconciliation_equations,
    validate_bctc_rows,
    validate_ttm_periods,
    parse_quarter,
)
from industry_profiles import (
    INDUSTRY_PROFILES,
    get_industry_profile,
    normalize_profile_id,
    summarize_all_sector_scoring_readiness,
    validate_industry_profiles,
)
from scoring_engine import (
    SCORING_ENGINE_VERSION,
    assess_all_sector_formula_readiness,
)
from valuation_engine import VALUATION_ENGINE_VERSION
from decision_engine import DECISION_ENGINE_VERSION
from report_templates import REPORT_TEMPLATE_VERSION
from metric_catalog import (
    get_sector_metric_catalog,
    metric_is_supported_for_sector,
    summarize_sector_metric_readiness,
    summarize_all_sector_metric_readiness,
    validate_metric_catalog,
)
from analysis_core import ENGINE_VERSION
from metric_catalog import METRIC_CATALOG_VERSION
from metric_contract import METRIC_CONTRACT_VERSION

# Cache trung vị peer có giới hạn bộ nhớ; dữ liệu mục tiêu vẫn được chuẩn hóa trực tiếp.
_SECTOR_MEDIAN_CACHE = TTLCache(max_items=1, ttl_seconds=180)

# ===================== LOGGING CHẨN ĐOÁN =====================
logger = logging.getLogger("price_api")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    _formatter.converter = lambda ts: (datetime.fromtimestamp(ts, tz=timezone.utc) + timedelta(hours=7)).timetuple()
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
logger.propagate = False

def log_exception(context: str, exc: Exception):
    logger.error("%s | %s: %s", context, type(exc).__name__, str(exc), exc_info=True)

# ===================== VNSTOCK =====================
from vnstock import Market

try:
    from ptkt import ProfessionalTechnicalAnalyzer
except ImportError:
    logger.warning("Không tìm thấy file ptkt.py. Điểm kỹ thuật sẽ mặc định = 0 (chưa đủ dữ liệu).")
    ProfessionalTechnicalAnalyzer = None

# ===================== CONFIG =====================
MONGO_URI = os.environ.get("MONGO_URI", "").strip()
if not MONGO_URI:
    logger.critical("Thiếu biến môi trường MONGO_URI. Server dừng khởi động.")
    sys.exit(1)

SSI_API_KEY = os.environ.get("SSI_API_KEY", "").strip()
SSI_API_SECRET = os.environ.get("SSI_API_SECRET", "").strip()
if SSI_API_KEY and SSI_API_SECRET:
    os.environ["SSI_API_KEY"] = SSI_API_KEY
    os.environ["SSI_API_SECRET"] = SSI_API_SECRET
else:
    logger.warning("⚠️ CẢNH BÁO: Thiếu SSI_API_KEY hoặc SSI_API_SECRET. Có thể không lấy được giá realtime!")

ADMIN_SECRET_KEY = os.environ.get("ADMIN_SECRET_KEY")
ENV_FHSC_API_KEY = os.environ.get("FHSC_API_KEY", "").strip()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip()

SYMBOLS_JSON_FILE = "vn100_symbols.json"
VN100_INFO_FILE = "vn100_full_info.json"
SCREENER_JSON_FILE = "screener_results.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

VOLUME_MULTIPLIER = 10
_raw_origins = os.environ.get("ALLOWED_ORIGINS", "*").strip()
ALLOWED_ORIGINS = "*" if (_raw_origins == "*" or not _raw_origins) else [o.strip() for o in _raw_origins.split(",") if o.strip()]

ALIBABA_API_KEY = os.environ.get("ALIBABA_API_KEY", "").strip()
AI_API_URL = "https://ws-8lcqaxwxdv0h9xzy.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
AI_MODEL_NAME = os.environ.get("AI_MODEL_NAME", "qwen3.7-max-2026-06-08")
AI_TEMPERATURE = 0.15

LIQUID_SYMBOLS_TOP = [
    "VNM", "VCB", "VIC", "VHM", "HPG", 
    "FPT", "MWG", "TCB", "VPB", "ACB",
    "BID", "CTG", "MSN", "GAS", "PLX"
]

LIQUID_SYMBOLS_MID = [
    "SSI", "VND", "HCM", "VCI", "MBS",
    "STB", "SHB", "TPB", "MBB", "HDB",
    "VIB", "LPB", "OCB", "MSB", "EIB",
    "PNJ", "FRT", "DGW", "REE", "PC1",
    "DGC", "DPM", "DCM", "HSG", "NKG",
    "KDH", "NLG", "DXG", "CEO", "PDR"
]

VN100_METADATA = {}
if os.path.exists(VN100_INFO_FILE):
    try:
        with open(VN100_INFO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            for item in data:
                sym = str(item.get("symbol", "")).upper().strip()
                if sym:
                    VN100_METADATA[sym] = {
                        "company_name": item.get("company_name", "") or item.get("organName", "") or "",
                        "market_cap": float(item.get("market_cap", 0) or item.get("capitalization", 0) or 0),
                        "total_shares": int(item.get("total_shares", 0) or item.get("shares", 0) or 0)
                    }
        logger.info(f"Đã tải metadata cho {len(VN100_METADATA)} mã từ {VN100_INFO_FILE}")
    except Exception as e:
        logger.warning(f"Lỗi đọc {VN100_INFO_FILE}: {e}")

# ===================== DATABASE =====================
logger.info("Đang kết nối MongoDB...")
try:
    mongo_client = MongoClient(
        MONGO_URI, 
        serverSelectionTimeoutMS=30000, 
        connectTimeoutMS=15000, 
        socketTimeoutMS=60000,
        appname="price_api"
    )
    mongo_client.admin.command("ping")
    db = mongo_client["quant_db"]
    collection = db["stocks"]
    history_col = db["history_data"]
    bctc_col = db["bctc_data"]
    screener_col = db["screener_results"]
    settings_col = db["settings"]
    logger.info("MongoDB đã kết nối thành công!")
    
    if os.path.exists(SYMBOLS_JSON_FILE):
        with open(SYMBOLS_JSON_FILE, "r", encoding="utf-8") as f:
            json_symbols = set(str(s).upper().strip() for s in json.load(f))
        if json_symbols:
            db_symbols = set(doc["symbol"] for doc in collection.find({}, {"symbol": 1}) if doc.get("symbol"))
            for sym in (json_symbols - db_symbols): collection.insert_one({"symbol": sym})
            if db_symbols - json_symbols: collection.delete_many({"symbol": {"$in": list(db_symbols - json_symbols)}})
except Exception as e:
    log_exception("[MONGODB STARTUP] Lỗi kết nối MongoDB", e)
    sys.exit(1)

# ===================== APP =====================
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})

Compress(app)
app.config['COMPRESS_MIMETYPES'] = ['application/json', 'text/html', 'text/css', 'text/javascript']
app.config['COMPRESS_LEVEL'] = 6
app.config['COMPRESS_MIN_SIZE'] = 500

# === BIẾN TRẠNG THÁI ===
is_crawling = False
is_full_data_running = False
is_screener_running = False
is_ai_running = False
LAST_TELEGRAM_TIME = 0
stop_crawl_requested = False
is_auto_crawl_paused = False

# Đồng bộ thao tác bắt đầu crawl với API pause để tránh worker vượt qua lệnh dừng.
crawl_control_lock = threading.Lock()

# Lock bảo vệ thay thế collection kết quả Screener.
screener_lock = threading.Lock()

# Lock riêng để chỉ cho phép một tiến trình tính điểm (worker hoặc force API).
screener_state_lock = threading.Lock()

def _claim_screener_job():
    """Atomically reserve the single Screener calculation slot."""
    global is_screener_running
    with screener_state_lock:
        if is_screener_running:
            return False
        is_screener_running = True
        return True

def _release_screener_job():
    """Release the Screener calculation slot."""
    global is_screener_running
    with screener_state_lock:
        is_screener_running = False

def _safe_update_screener_result(symbol, result):
    """Update one Screener row only when no full refresh owns the dataset."""
    with screener_state_lock:
        if is_screener_running:
            return False
        with screener_lock:
            screener_col.update_one(
                {"symbol": symbol},
                {"$set": result},
                upsert=True,
            )
    return True

# === BIẾN CHO HOLIDAY CHECKER ===
holiday_checked_today = None
holiday_status = None
holiday_detected_date = None
holiday_check_lock = threading.Lock()
is_holiday_paused = False

# === CACHE CHO /api/history-all ===
_history_cache = {"data": None, "timestamp": 0, "lock": threading.Lock()}
HISTORY_CACHE_TTL = 60

def get_current_fhsc_key():
    setting = settings_col.find_one({"_id": "fhsc_key"})
    if setting and setting.get("value"): return setting["value"]
    return ENV_FHSC_API_KEY

# ===================== HELPERS =====================
# FIX BUG #7: Dùng timezone-aware datetime thay vì utcnow() deprecated
def _vn_now() -> datetime:
    """Trả về datetime hiện tại theo giờ VN (UTC+7), timezone-aware."""
    return datetime.now(timezone.utc) + timedelta(hours=7)

def _now_str() -> str:
    return _vn_now().strftime("%d/%m/%Y %H:%M:%S")


def _expected_latest_bctc_period(now=None) -> str:
    """
    Kỳ BCTC quý gần nhất thường đã đến hạn công bố theo lịch tại Việt Nam.
    Dùng để phát hiện dữ liệu bị cũ, không giả định mọi doanh nghiệp công bố cùng ngày.
    """
    now = now or _vn_now()
    year, month = now.year, now.month

    if month >= 11:
        return f"{year}-Q3"
    if month >= 8:
        return f"{year}-Q2"
    if month >= 5:
        return f"{year}-Q1"
    if month >= 4:
        return f"{year - 1}-Q4"
    return f"{year - 1}-Q3"

def _get_trading_date_str() -> str:
    vn_now = _vn_now()
    if vn_now.hour < 9: vn_now -= timedelta(days=1)
    while vn_now.weekday() >= 5: vn_now -= timedelta(days=1)
    return vn_now.strftime("%Y-%m-%d")

def _is_after_15h() -> bool:
    vn_now = _vn_now()
    return vn_now.hour > 15 or (vn_now.hour == 15 and vn_now.minute >= 5)

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try: requests.post(url, json=payload, timeout=10)
    except Exception as e: log_exception("[TELEGRAM]", e)

# ═══════════════════════════════════════════════════════════════
# FIX BUG #1 + #2: Helper ghi screener an toàn + invalidate cache
# ═══════════════════════════════════════════════════════════════
def _safe_replace_screener_results(results: list, tag: str = "") -> bool:
    """
    Thay thế toàn bộ Screener bằng collection staging và thao tác rename nguyên tử.
    Nếu ghi staging hoặc rename thất bại, collection đang phục vụ vẫn được giữ nguyên.
    """
    global _history_cache

    if not results:
        logger.warning("[SAFE-SCREENER] Danh sách rỗng, bỏ qua ghi đè.")
        return False

    acquired = screener_lock.acquire(timeout=120)
    if not acquired:
        logger.error("[SAFE-SCREENER] Không lấy được lock sau 120s, hủy ghi đè.")
        return False

    tmp_col = None
    renamed = False
    try:
        tmp_name = f"screener_results_tmp_{uuid.uuid4().hex}"
        tmp_col = db[tmp_name]

        # Ghi staging trước; không đụng collection đang được API phục vụ.
        tmp_col.insert_many(results, ordered=True)
        staged_count = tmp_col.count_documents({})
        if staged_count != len(results):
            raise RuntimeError(
                f"Staging count mismatch: expected={len(results)}, actual={staged_count}"
            )

        # MongoDB thực hiện rename + dropTarget trong một lệnh nguyên tử.
        # Nếu backend không hỗ trợ/quyền không đủ, lệnh lỗi và dữ liệu cũ còn nguyên.
        tmp_col.rename(screener_col.name, dropTarget=True)
        renamed = True

        logger.info(
            "[SAFE-SCREENER] Atomic swap thành công: %d mã%s",
            staged_count,
            f" ({tag})" if tag else ""
        )

        with _history_cache["lock"]:
            _history_cache["data"] = None
            _history_cache["timestamp"] = 0
        _SECTOR_MEDIAN_CACHE.clear()
        return True

    except Exception as e:
        log_exception("[SAFE-SCREENER] Lỗi atomic replace; dữ liệu hiện tại được giữ nếu rename chưa thành công", e)
        return False

    finally:
        if tmp_col is not None and not renamed:
            try:
                tmp_col.drop()
            except Exception:
                pass
        screener_lock.release()

# ===================== CAGR 5 NĂM =====================
def get_historical_return_rate_with_details(symbol, history_bars=None):
    """
    Tính CAGR 5 năm. Screener truyền history_bars đã preload để tránh truy vấn MongoDB theo từng mã.
    Caller cũ không truyền dữ liệu vẫn đọc MongoDB như trước.
    """
    try:
        if history_bars is None:
            hist_data = history_col.find_one({"symbol": symbol}, {"_id": 0, "history_d1": 1})
            history_bars = hist_data.get("history_d1", []) if hist_data else []
        if not history_bars: return None
        if len(history_bars) < 30: return None
            
        df = pd.DataFrame(history_bars)
        df['date'] = pd.to_datetime(df['time'])
        df = df.sort_values('date')
        
        max_date = df['date'].max()
        cutoff_date = max_date - pd.DateOffset(years=5)
        df_5years = df.loc[df['date'] >= cutoff_date].copy()
        
        if len(df_5years) < 30: return None
            
        df_5years['year_quarter'] = df_5years['date'].dt.to_period('Q')
        df_quarterly = df_5years.groupby('year_quarter').agg({'close': 'mean', 'date': 'first'}).reset_index()
        
        if len(df_quarterly) < 2: return None
            
        start_row, end_row = df_quarterly.iloc[0], df_quarterly.iloc[-1]
        days = (end_row['date'] - start_row['date']).days
        if days < 30: return None
            
        years = days / 365.25
        cagr = (end_row['close'] / start_row['close']) ** (1 / years) - 1
        cagr = max(-0.5, min(cagr, 1.0))
        
        return {
            "annual_rate_percent": round(cagr * 100, 2),
            "start_quarter": str(start_row['year_quarter']),
            "start_price_mean": round(start_row['close'], 2),
            "end_quarter": str(end_row['year_quarter']),
            "end_price_mean": round(end_row['close'], 2)
        }
    except Exception as e: return None

# ===================== BÓC TÁCH BCTC =====================
def _safe_float(v):
    try: return float(v) if v is not None else 0.0
    except: return 0.0

# FIX BUG #3: Không return item đầu tiên khi period=None; ưu tiên kỳ mới nhất
def _get_bctc_value(report_array, target_ids, period=None):
    """
    Tìm giá trị của một chỉ tiêu BCTC theo ID.
    FIX BUG #3: Nếu period=None → tự động lấy kỳ MỚI NHẤT (không lấy item đầu tiên).
    """
    if not report_array: return 0.0
    if isinstance(target_ids, str): target_ids = [target_ids]
    
    # Nếu không chỉ định kỳ → tìm kỳ mới nhất
    if period is None:
        candidate_periods = sorted(
            set(item.get("period") for item in report_array if item.get("period")),
            reverse=True
        )
        if candidate_periods:
            period = candidate_periods[0]
    
    for item in report_array:
        if item.get("id") in target_ids:
            if period is None or item.get("period") == period:
                return _safe_float(item.get("value"))
    return 0.0

def _period_quarter_index(period):
    match = re.fullmatch(r"(\d{4})-Q([1-4])", str(period or ""))
    return int(match.group(1)) * 4 + int(match.group(2)) - 1 if match else None


def _are_consecutive_quarters(periods):
    indexes = [_period_quarter_index(p) for p in periods]
    return (
        len(indexes) == 4
        and all(x is not None for x in indexes)
        and indexes == list(range(indexes[0], indexes[0] + 4))
    )


def _get_bctc_optional_value(report_array, target_ids, period=None):
    """Trả None nếu thiếu hoặc không phân giải được bản ghi; không lấy tùy tiện dòng đầu."""
    if not report_array:
        return None
    if isinstance(target_ids, str):
        target_ids = [target_ids]

    # Một số trường VCI gán chung ID cho dòng tổng và dòng thành phần.
    # Chỉ chọn dòng tổng khi source_field đã được xác định rõ.
    preferred_fields = {
        "BS_SHORT_TERM_ASSETS": ("bsa1",),
        "BS_LONG_TERM_ASSETS": ("bsa23",),
        "BS_TOTAL_ASSETS": ("bsa53",),
        "BS_LIABILITIES": ("bsa54",),
        "BS_OWNERS_EQUITY": ("bsa78",),
        "BS_TOTAL_LIABILITIES_AND_EQUITY": ("bsa96",),
    }

    # target_ids có thứ tự ưu tiên; không phụ thuộc thứ tự tài liệu MongoDB.
    for target_id in target_ids:
        candidates = [
            item for item in report_array
            if item.get("id") == target_id
            and (period is None or item.get("period") == period)
        ]
        if not candidates:
            continue

        preferred = preferred_fields.get(target_id, ())
        if preferred:
            preferred_candidates = [
                item for item in candidates
                if str(item.get("source_field", "")).lower() in preferred
            ]
            if preferred_candidates:
                candidates = preferred_candidates

        # Không bỏ qua dòng trùng bị thiếu/không hợp lệ: chọn dòng còn lại
        # sẽ che giấu dữ liệu nguồn mâu thuẫn hoặc hỏng.
        if any(item.get("value") is None or isinstance(item.get("value"), bool)
               for item in candidates):
            return None
        values = []
        for item in candidates:
            try:
                value = float(item.get("value"))
            except (TypeError, ValueError, OverflowError):
                return None
            if not np.isfinite(value):
                return None
            values.append(value)

        # Nhiều bản ghi chỉ được chấp nhận khi cùng cho một giá trị.
        # Nếu khác nhau mà không có source_field ưu tiên, coi là mơ hồ.
        if any(value != values[0] for value in values[1:]):
            return None
        return values[0]

    return None



def _valid_income_statement_periods(income_rows):
    """Only periods with a resolvable profit or revenue line count as reported income periods."""
    core_ids = (
        "IS_NET_PROFIT_AFTER_TAX",
        "IS_PROFIT_AFTER_TAX",
        "IS_NET_PROFIT",
        "IS_NET_REVENUE",
    )
    candidates = {
        row.get("period")
        for row in (income_rows or [])
        if isinstance(row, dict)
        and _period_quarter_index(row.get("period")) is not None
    }
    valid = [
        period for period in candidates
        if any(
            _get_bctc_optional_value(income_rows, metric_id, period) is not None
            for metric_id in core_ids
        )
    ]
    return sorted(valid, key=_period_quarter_index, reverse=True)


def extract_metrics_from_bctc_data(bctc_doc):
    """Trích xuất BCTC quý; chỉ công bố TTM khi có đủ 4 quý liên tiếp và đủ chỉ tiêu."""
    if not isinstance(bctc_doc, dict):
        return {}

    metrics = {}
    income = bctc_doc.get("income_statement") or []
    cashflow = bctc_doc.get("cash_flow") or []
    balance = bctc_doc.get("balance_sheet") or []

    # Bỏ kỳ chỉ có dòng phụ/ID lạ; freshness và TTM phải dựa trên dòng KQKD cốt lõi.
    periods = _valid_income_statement_periods(income)
    latest_period = periods[0] if periods else None
    latest_four = periods[:4]
    income_ttm_complete = _are_consecutive_quarters(list(reversed(latest_four)))

    def values_for(rows, ids, selected_periods):
        values = [_get_bctc_optional_value(rows, ids, p) for p in selected_periods]
        return values

    metrics["bctc_latest_income_period"] = latest_period or ""
    metrics["bctc_income_ttm_complete"] = False
    metrics["bctc_cfo_ttm_complete"] = False
    # Explicitly clear legacy values when the current source cannot verify them.
    metrics["gross_margin"] = 0.0
    metrics["gross_margin_available"] = False
    metrics["gross_margin_ttm"] = 0.0
    metrics["gross_margin_ttm_available"] = False
    metrics["debt_to_equity"] = 0.0
    metrics["debt_to_equity_available"] = False
    metrics["total_liabilities_to_equity"] = 0.0
    metrics["total_liabilities_to_equity_available"] = False

    if latest_period:
        latest_npat = _get_bctc_optional_value(
            income, ["IS_NET_PROFIT_AFTER_TAX", "IS_PROFIT_AFTER_TAX", "IS_NET_PROFIT"], latest_period
        )
        latest_rev = _get_bctc_optional_value(income, "IS_NET_REVENUE", latest_period)
        latest_cogs = _get_bctc_optional_value(income, "IS_COST_OF_GOODS_SOLD", latest_period)
        if latest_npat is not None:
            metrics["net_profit_latest_quarter"] = round(latest_npat / 1e9, 2)
        if latest_rev is not None:
            metrics["revenue_latest_quarter"] = round(latest_rev / 1e9, 2)
        # COGS is canonicalized to a non-positive expense during VCI ingestion.
        # If raw/legacy rows bypass that normalization, do not publish a misleading margin.
        if (
            latest_rev is not None and latest_cogs is not None
            and latest_rev > 0 and latest_cogs <= 0
        ):
            metrics["gross_margin"] = round((latest_rev + latest_cogs) / latest_rev * 100, 2)
            metrics["gross_margin_available"] = True

        prior_year_period = f"{int(latest_period[:4]) - 1}-{latest_period[4:]}"
        prior_npat = _get_bctc_optional_value(
            income, ["IS_NET_PROFIT_AFTER_TAX", "IS_PROFIT_AFTER_TAX", "IS_NET_PROFIT"], prior_year_period
        )
        prior_revenue = _get_bctc_optional_value(income, "IS_NET_REVENUE", prior_year_period)
        if latest_npat is not None and prior_npat is not None and prior_npat > 0:
            metrics["profit_growth_yoy"] = round((latest_npat / prior_npat - 1) * 100, 2)
            metrics["profit_growth_yoy_available"] = True
        else:
            metrics["profit_growth_yoy"] = 0.0
            metrics["profit_growth_yoy_available"] = False
        if latest_rev is not None and prior_revenue is not None and prior_revenue > 0:
            metrics["revenue_growth_yoy"] = round((latest_rev / prior_revenue - 1) * 100, 2)
            metrics["revenue_growth_yoy_available"] = True
        else:
            metrics["revenue_growth_yoy"] = 0.0
            metrics["revenue_growth_yoy_available"] = False

    if income_ttm_complete:
        npat_values = values_for(
            income, ["IS_NET_PROFIT_AFTER_TAX", "IS_PROFIT_AFTER_TAX", "IS_NET_PROFIT"], latest_four
        )
        revenue_values = values_for(income, "IS_NET_REVENUE", latest_four)
        cogs_values = values_for(income, "IS_COST_OF_GOODS_SOLD", latest_four)
        if all(v is not None for v in npat_values):
            metrics["net_profit_ttm"] = round(sum(npat_values) / 1e9, 2)
            metrics["bctc_income_ttm_complete"] = True
        else:
            metrics["net_profit_ttm"] = 0.0
        if all(v is not None for v in revenue_values):
            metrics["revenue_ttm"] = round(sum(revenue_values) / 1e9, 2)
        if (
            all(v is not None for v in revenue_values + cogs_values)
            and sum(revenue_values) > 0
            and all(value <= 0 for value in cogs_values)
        ):
            metrics["gross_margin_ttm"] = round(
                (sum(revenue_values) + sum(cogs_values)) / sum(revenue_values) * 100, 2
            )
            metrics["gross_margin_ttm_available"] = True
    else:
        metrics["net_profit_ttm"] = 0.0

    # CIR ngân hàng chỉ tính khi đủ 4 quý và có đủ hai dòng thu nhập/chi phí.
    # NIM/NPL/CASA/CAR không được suy diễn từ các trường chưa xác minh.
    bank_income_periods = sorted(
        {
            x.get("period") for x in income
            if _period_quarter_index(x.get("period")) is not None
            and latest_period is not None
            and _period_quarter_index(x.get("period"))
                <= _period_quarter_index(latest_period)
        },
        key=_period_quarter_index,
        reverse=True
    )
    bank_latest_four = bank_income_periods[:4]
    bank_window_aligned = (
        bool(latest_period)
        and len(bank_latest_four) == 4
        and bank_latest_four[0] == latest_period
        and _are_consecutive_quarters(list(reversed(bank_latest_four)))
    )
    if bank_window_aligned:
        bank_oi = values_for(income, "IS_TOTAL_OPERATING_INCOME_BANK", bank_latest_four)
        bank_opex = values_for(income, "IS_OPERATING_EXPENSE_BANK", bank_latest_four)
        # The canonical bank CIR contract requires negative expense rows.
        # abs() alone would silently accept a source sign-convention mismatch.
        if (
            all(v is not None for v in bank_oi + bank_opex)
            and sum(bank_oi) > 0
            and all(v <= 0 for v in bank_opex)
        ):
            metrics["bank_cir"] = round(abs(sum(bank_opex)) / sum(bank_oi) * 100, 2)
            metrics["bank_cir_available"] = True
        else:
            metrics["bank_cir_available"] = False
    else:
        metrics["bank_cir_available"] = False

    # CFO and NPAT must cover the same four-quarter window. A newer/older
    # cash-flow statement must never be compared with a different income TTM.
    income_end_index = _period_quarter_index(latest_period) if latest_period else None
    cf_periods = sorted(
        {
            x.get("period") for x in cashflow
            if _period_quarter_index(x.get("period")) is not None
            and income_end_index is not None
            and _period_quarter_index(x.get("period")) <= income_end_index
        },
        key=_period_quarter_index,
        reverse=True
    )
    cf_latest_four = cf_periods[:4]
    cfo_ttm_complete = (
        bool(latest_period)
        and len(cf_latest_four) == 4
        and cf_latest_four[0] == latest_period
        and _are_consecutive_quarters(list(reversed(cf_latest_four)))
    )
    cfo_source_is_vci = str(bctc_doc.get("source", "")).upper() == "VCI_DIRECT"

    def cfo_quarterly_from_ytd(period):
        # Báo cáo lưu chuyển tiền tệ VCI trình bày lũy kế từ đầu năm:
        # Q1 = YTD Q1; Q2 = YTD Q2 - YTD Q1; Q3 = YTD Q3 - YTD Q2;
        # Q4 = cả năm - YTD Q3.
        # Không tự áp dụng phép trừ cho nguồn không xác định cơ sở dữ liệu.
        if not cfo_source_is_vci:
            return None
        match = re.fullmatch(r"(\d{4})-Q([1-4])", str(period or ""))
        if not match:
            return None
        year, quarter = int(match.group(1)), int(match.group(2))
        current_ytd = _get_bctc_optional_value(
            cashflow, "CF_NET_CASH_FLOWS_FROM_OPERATING_ACTIVITIES", period
        )
        if current_ytd is None:
            return None
        if quarter == 1:
            return current_ytd
        previous_period = f"{year}-Q{quarter - 1}"
        previous_ytd = _get_bctc_optional_value(
            cashflow, "CF_NET_CASH_FLOWS_FROM_OPERATING_ACTIVITIES", previous_period
        )
        if previous_ytd is None:
            return None
        return current_ytd - previous_ytd

    if latest_period:
        latest_cfo_quarter = cfo_quarterly_from_ytd(latest_period)
        if latest_cfo_quarter is not None:
            metrics["cfo_latest_quarter"] = round(latest_cfo_quarter / 1e9, 2)
    if cfo_ttm_complete:
        cfo_quarter_values = [cfo_quarterly_from_ytd(p) for p in cf_latest_four]
        if all(v is not None for v in cfo_quarter_values):
            cfo_ttm = round(sum(cfo_quarter_values) / 1e9, 2)
            metrics["cfo_ttm"] = cfo_ttm
            metrics["cfo_latest"] = cfo_ttm  # Alias tương thích API cũ; đây là CFO TTM.
            metrics["bctc_cfo_ttm_complete"] = True
    if not metrics.get("bctc_cfo_ttm_complete"):
        metrics["cfo_ttm"] = 0.0
        metrics["cfo_latest"] = 0.0

    bs_periods = sorted(
        {x.get("period") for x in balance if _period_quarter_index(x.get("period")) is not None},
        key=_period_quarter_index,
        reverse=True
    )
    latest_bs_period = bs_periods[0] if bs_periods else None
    metrics["bctc_latest_balance_period"] = latest_bs_period or ""
    if latest_bs_period:
        eq = _get_bctc_optional_value(balance, ["BS_OWNERS_EQUITY", "BS_EQUITY"], latest_bs_period)
        total_assets = _get_bctc_optional_value(balance, "BS_TOTAL_ASSETS", latest_bs_period)
        if total_assets is None:
            short_assets = _get_bctc_optional_value(balance, "BS_SHORT_TERM_ASSETS", latest_bs_period)
            long_assets = _get_bctc_optional_value(balance, "BS_LONG_TERM_ASSETS", latest_bs_period)
            if short_assets is not None and long_assets is not None:
                total_assets = short_assets + long_assets
        liab = _get_bctc_optional_value(balance, "BS_LIABILITIES", latest_bs_period)

        if eq is not None:
            metrics["owners_equity"] = round(eq / 1e9, 2)
        if total_assets is not None:
            metrics["total_assets"] = round(total_assets / 1e9, 2)
        if liab is not None:
            metrics["total_liabilities"] = round(liab / 1e9, 2)
        # Total liabilities are not the same as interest-bearing debt.
        # Keep the valid balance-sheet ratio under its correct name and fail
        # closed for D/E until verified borrowings and cash mappings exist.
        metrics["total_liabilities_to_equity"] = (
            round(liab / eq, 3)
            if eq is not None and liab is not None and eq > 0 else 0.0
        )
        metrics["total_liabilities_to_equity_available"] = (
            eq is not None and liab is not None and eq > 0
        )
        metrics["debt_to_equity"] = 0.0
        metrics["debt_to_equity_available"] = False
        metrics["bctc_balance_sheet_valid"] = (
            eq is not None and liab is not None and eq > 0
        )
    else:
        metrics["bctc_balance_sheet_valid"] = False

    return metrics

# ===================== CÀO GIÁ =====================
def _fetch_one(symbol: str) -> Optional[dict]:
    print(f"    [FETCH] Đang lấy giá {symbol}...", flush=True)
    def _do():
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            past_7d = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            
            df = Market().equity(symbol).ohlcv(
                start=past_7d, 
                end=today, 
                interval="1m", 
                source="ssi"
            )
            
            if df is not None and not df.empty:
                df['time'] = pd.to_datetime(df['time'])
                df['date_str'] = df['time'].dt.strftime('%Y-%m-%d')
                
                df_today = df[df['date_str'] == today]
                
                if not df_today.empty:
                    latest_candle = df_today.iloc[-1]
                    total_volume_today = int(df_today['volume'].sum())
                    
                    return {
                        "close": float(latest_candle["close"]), 
                        "open": float(latest_candle["open"]), 
                        "high": float(latest_candle["high"]), 
                        "low": float(latest_candle["low"]), 
                        "volume": total_volume_today
                    }
                else:
                    latest_candle = df.iloc[-1]
                    return {
                        "close": float(latest_candle["close"]), 
                        "open": float(latest_candle["open"]), 
                        "high": float(latest_candle["high"]), 
                        "low": float(latest_candle["low"]), 
                        "volume": int(latest_candle["volume"])
                    }
            return None
        except Exception as e:
            print(f"    ❌ [FETCH ERROR] Lỗi gọi Vnstock cho {symbol}: {e}", flush=True)
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        try: 
            data = ex.submit(_do).result(timeout=15)
        except concurrent.futures.TimeoutError:
            print(f"    ❌ [FETCH TIMEOUT] Quá 15s không lấy được giá {symbol}", flush=True)
            return None
        except Exception as e:
            print(f"    ❌ [FETCH THREAD ERROR] {symbol}: {e}", flush=True)
            return None

    if not data or data["close"] <= 0 or data["open"] <= 0: 
        return None
        
    if data["close"] >= 1000:
        for k in ["close", "open", "high", "low"]: 
            data[k] /= 1000.0
            
    if 0 < data["volume"] < 100000: 
        data["volume"] *= VOLUME_MULTIPLIER
        
    return data

def _fetch_history_ssi(symbol: str, start_date: str, end_date: str):
    try:
        df = Market().equity(symbol).ohlcv(start=start_date, end=end_date, source="ssi")
        return df if df is not None and not df.empty else None
    except Exception: return None

# ===================== CÀO BCTC VCI =====================
def _fetch_bctc_ssi(symbol: str):
    symbol = str(symbol).upper().strip()
    if not symbol or not re.fullmatch(r"[A-Z0-9]{2,10}", symbol):
        logger.warning("[BCTC VCI] Mã cổ phiếu không hợp lệ: %r", symbol)
        return None

    # CORE_IDS giữ các ID tương thích với bộ tính điểm cũ.
    # Các dòng VCI khác được lưu bằng ID raw ổn định theo section + field_code.
    CORE_IDS = {
        "IS_NET_REVENUE", "IS_COST_OF_GOODS_SOLD", "IS_NET_PROFIT_AFTER_TAX",
        "CF_NET_CASH_FLOWS_FROM_OPERATING_ACTIVITIES", "BS_TOTAL_ASSETS",
        "BS_SHORT_TERM_ASSETS", "BS_LONG_TERM_ASSETS", "BS_OWNERS_EQUITY", "BS_LIABILITIES"
    }

    base_url = "https://iq.vietcap.com.vn/api/iq-insight-service"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
        ),
        "Referer": "https://iq.vietcap.com.vn/",
        "Origin": "https://iq.vietcap.com.vn",
    }
    session = requests.Session()
    session.headers.update(headers)

    def get_json(path, params=None, retries=3):
        url = f"{base_url}{path}"
        last_error = None
        for attempt in range(retries):
            try:
                response = session.get(url, params=params, timeout=(8, 25))
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, (dict, list)):
                    raise ValueError("VCI trả về JSON không hợp lệ")
                return payload
            except Exception as exc:
                last_error = exc
                if attempt < retries - 1:
                    time.sleep((attempt + 1) * 2)
        raise RuntimeError(f"VCI API lỗi {path}: {last_error}") from last_error

    def unwrap(payload):
        if not isinstance(payload, dict):
            return payload
        data = payload.get("data", payload)
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        return data

    def get_period(row):
        year = (
            row.get("yearReport") or row.get("year")
            or row.get("year_report")
        )
        quarter = (
            row.get("quarter") or row.get("quarterReport")
            or row.get("quarter_report")
        )
        length = row.get("lengthReport") or row.get("length_report")
        try:
            year = int(year)
            if quarter not in (None, ""):
                quarter = int(quarter)
            elif length not in (None, ""):
                quarter = int(length)
            else:
                return None
            if not 1 <= quarter <= 4:
                return None
            return f"{year}-Q{quarter}"
        except (TypeError, ValueError):
            return None

    def canonical_id(report_name, field_code, title):
        # Từ điển VCI dùng chung; mã chưa xác định vẫn được lưu dưới dạng VCI raw.
        return canonicalize_vci_field(report_name, field_code, title)
    try:
        logger.info("[BCTC VCI] Bắt đầu lấy BCTC cho %s", symbol)

        metadata = {}
        metadata_units = {}
        try:
            meta = unwrap(get_json(
                f"/v1/company/{symbol}/financial-statement/metrics"
            ))
            if isinstance(meta, dict):
                for section in (
                    "INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW"
                ):
                    section_items = [
                        item for item in (meta.get(section) or [])
                        if isinstance(item, dict) and item.get("field")
                    ]
                    metadata[section] = {
                        str(item.get("field")): (
                            item.get("titleVi")
                            or item.get("titleEn")
                            or item.get("field")
                        )
                        for item in section_items
                    }
                    metadata_units[section] = {
                        str(item.get("field")): (
                            item.get("unit")
                            or item.get("unitName")
                            or item.get("unit_name")
                            or item.get("measureUnit")
                            or ""
                        )
                        for item in section_items
                    }
        except Exception as meta_error:
            logger.warning(
                "[BCTC VCI] Không lấy được metadata %s: %s",
                symbol, meta_error
            )

        report_map = {
            "balance_sheet": "BALANCE_SHEET",
            "income_statement": "INCOME_STATEMENT",
            "cash_flow": "CASH_FLOW",
        }
        reports = {}
        latest_period = None
        latest_income_period = None
        ignored_fields = {
            "organCode", "ticker", "createDate", "updateDate",
            "yearReport", "lengthReport", "publicDate", "quarter",
            "quarterReport", "year", "year_report", "length_report",
            "quarter_report",
        }

        for report_name, section in report_map.items():
            try:
                payload = unwrap(get_json(
                    f"/v1/company/{symbol}/financial-statement",
                    params={"section": section},
                ))
                if isinstance(payload, dict):
                    rows = (
                        payload.get("quarters")
                        or payload.get("quarter")
                        or []
                    )
                elif isinstance(payload, list):
                    rows = payload
                else:
                    rows = []

                records = []
                titles = metadata.get(section, {})
                units = metadata_units.get(section, {})

                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    period = get_period(row)
                    if not period:
                        continue

                    if latest_period is None or period > latest_period:
                        latest_period = period
                    for field_code, raw_value in row.items():
                        if field_code in ignored_fields or raw_value is None:
                            continue
                        if isinstance(raw_value, (dict, list, bool)):
                            continue
                        try:
                            value = float(raw_value)
                        except (TypeError, ValueError):
                            continue
                        if not np.isfinite(value):
                            continue

                        title = titles.get(str(field_code), str(field_code))
                        item_id = canonical_id(report_name, field_code, title)
                        is_core = bool(item_id and item_id in CORE_IDS)

                        # Không loại bỏ các dòng ngoài bộ chỉ tiêu cốt lõi.
                        # Giữ field_code gốc để có thể ánh xạ chuyên sâu theo từng ngành.
                        if not item_id:
                            safe_field = re.sub(
                                r"[^A-Z0-9_]+", "_", str(field_code).upper()
                            ).strip("_")
                            if not safe_field:
                                continue
                            item_id = f"VCI_{report_name.upper()}_{safe_field}"

                        if item_id == "IS_COST_OF_GOODS_SOLD" and value > 0:
                            value = -value

                        # Không tự gán VND khi metadata không cung cấp đơn vị.
                        # Unit rỗng được xem là chưa xác minh và không dùng để suy luận tỷ lệ.
                        unit = resolve_vci_unit(report_name, field_code, units.get(str(field_code)))
                        records.append({
                            "id": item_id,
                            "name": str(title),
                            "period": period,
                            "value": value,
                            "unit": str(unit),
                            "unit_verified": bool(unit),
                            "source_field": str(field_code),
                            "source_section": section,
                            "is_core": is_core,
                        })

                if report_name == "income_statement":
                    valid_income_periods = _valid_income_statement_periods(records)
                    latest_income_period = (
                        valid_income_periods[0] if valid_income_periods else None
                    )

                if records:
                    unique_periods = sorted(list(set([item["period"] for item in records])), reverse=True)
                    # Giữ 12 quý (3 năm) để đánh giá xu hướng và chu kỳ ngành.
                    keep_periods = set(unique_periods[:12])
                    records = [item for item in records if item["period"] in keep_periods]

                    reports[report_name] = records
                    logger.info(
                        "[BCTC VCI] %s | %s: %d chỉ tiêu, %d kỳ",
                        symbol, report_name, len(records),
                        len({item["period"] for item in records})
                    )
                else:
                    logger.warning(
                        "[BCTC VCI] %s | %s không có dữ liệu cốt lõi",
                        symbol, report_name
                    )

            except Exception as report_error:
                logger.warning(
                    "[BCTC VCI] %s | lỗi %s: %s",
                    symbol, report_name, report_error
                )

        if reports:
            reports["latest_period"] = latest_period or "Chưa xác định"
            reports["latest_income_period"] = latest_income_period or "Chưa xác định"
            reports["source"] = "VCI_DIRECT"
            reports["vci_extraction_version"] = "2.0"
            reports["vci_field_catalog"] = metadata
            reports["vci_field_counts"] = {
                name: len({
                    str(item.get("source_field"))
                    for item in records
                    if isinstance(item, dict) and item.get("source_field")
                })
                for name, records in reports.items()
                if name in ("balance_sheet", "income_statement", "cash_flow")
                and isinstance(records, list)
            }
            reports["vci_period_counts"] = {
                name: len({
                    str(item.get("period"))
                    for item in records
                    if isinstance(item, dict) and item.get("period")
                })
                for name, records in reports.items()
                if name in ("balance_sheet", "income_statement", "cash_flow")
                and isinstance(records, list)
            }
            logger.info(
                "[BCTC VCI] Hoàn tất %s | kỳ mới nhất: %s",
                symbol, reports["latest_period"]
            )
            return reports

        logger.error("[BCTC VCI] Không lấy được BCTC cho %s", symbol)
        return None

    except Exception as exc:
        log_exception(f"[BCTC VCI] Lỗi lấy BCTC {symbol}", exc)
        return None

    finally:
        session.close()


# ===================== RATIO VNDIRECT =====================
VNDIRECT_RATIOS = {
    "pe": "PRICE_TO_EARNINGS",
    "pb": "PRICE_TO_BOOK",
    
    "eps": "EPS_TR",
    "roe": "ROAE_TR_AVG5Q",
    "roa": "ROAA_TR_AVG5Q",
    "dividend_yield": "DIVIDEND_YIELD"
}

def get_vndirect_ratio(symbol, ratio_code):
    url = "https://api-finfo.vndirect.com.vn/v4/ratios"
    params = {"q": f"code:{symbol}~ratioCode:{ratio_code}", "size": 1, "sort": "reportDate:desc"}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data:
                val = data[0].get("value")
                if val is not None:
                    return float(val)
    except Exception:
        pass
    return None

# ===================== CORE CRAWL =====================
# FIX BUG #8: Preload documents 1 lần thay vì find_one 2 lần/symbol
def _execute_crawl(symbols: list, is_retry: bool = False, is_manual: bool = False):
    global is_crawling, LAST_TELEGRAM_TIME, stop_crawl_requested, _history_cache

    # Kiểm tra pause và đánh dấu worker đang chạy trong cùng lock với API pause.
    # Nhờ vậy không thể có tình huống worker kiểm tra xong rồi mới khởi chạy sau lệnh pause.
    with crawl_control_lock:
        if is_crawling:
            return
        if is_auto_crawl_paused and not (is_manual or is_retry):
            print("[CRAWL] ⏸️ Bỏ qua lượt REAL-TIME vì hệ thống đang tạm dừng.", flush=True)
            return
        is_crawling = True
        if is_manual or is_retry:
            stop_crawl_requested = False
        elif not is_auto_crawl_paused:
            stop_crawl_requested = False
    
    print(f"\n[CRAWL] 🚀 [{'CÀO BÙ' if is_retry else ('CÀO TAY' if is_manual else 'REAL-TIME')}] Bắt đầu xử lý {len(symbols)} mã...", flush=True)
    try:
        vn_now = _vn_now()
        real_today = vn_now.strftime("%Y-%m-%d")
        current_hour = vn_now.hour
        current_minute = vn_now.minute

        is_closing_time = (current_hour > 15) or (current_hour == 15 and current_minute >= 5)

        alert_batch, pending_history_files = [], {}

        # FIX BUG #8: Preload tất cả documents cần thiết 1 lần
        preloaded_docs = {doc["symbol"]: doc for doc in collection.find(
            {"symbol": {"$in": symbols}}, 
            {"symbol": 1, "close": 1, "is_locked": 1, "trading_date": 1, "open": 1, "high": 1, "low": 1}
        )}
        preloaded_history = {doc["symbol"]: doc for doc in history_col.find(
            {"symbol": {"$in": symbols}}, 
            {"symbol": 1, "history_d1": 1}
        )}

        for chunk_idx in range(0, len(symbols), 5):
            if stop_crawl_requested:
                print("[CRAWL] 🛑 Đã nhận lệnh dừng khẩn cấp!", flush=True)
                break

            chunk = symbols[chunk_idx:chunk_idx+5]
            for sym in chunk:
                if stop_crawl_requested: break
                
                doc = preloaded_docs.get(sym)
                data = _fetch_one(sym)
                
                if data:
                    hist_doc = preloaded_history.get(sym)
                    prev_close = 0.0
                    avg_vol_20 = 0.0
                    latest_history_date = None 
                    
                    if hist_doc and hist_doc.get("history_d1"):
                        bars = hist_doc["history_d1"]
                        bars_sorted = sorted(bars, key=lambda x: x.get("time", ""))
                        
                        if bars_sorted and bars_sorted[-1]["time"] == real_today:
                            if len(bars_sorted) >= 2:
                                prev_close = float(bars_sorted[-2]["close"])
                                latest_history_date = bars_sorted[-2]["time"]
                        else:
                            prev_close = float(bars_sorted[-1]["close"])
                            latest_history_date = bars_sorted[-1]["time"]
                        
                        valid_vols = [b.get("volume", 0) for b in bars_sorted[-20:] if b.get("volume", 0) > 0]
                        if valid_vols: avg_vol_20 = sum(valid_vols) / len(valid_vols)

                    if prev_close <= 0 and doc:
                        prev_close = float(doc.get("close", 0))

                    vol_pct = round(data["volume"] / avg_vol_20 * 100, 2) if avg_vol_20 > 0 else 0
                    pct_change = round(((data["close"] - prev_close) / prev_close) * 100, 2) if prev_close > 0 else 0
                    
                    arrow, color = "—", "slate"
                    if pct_change > 0: arrow, color = "▲", "emerald"
                    elif pct_change < 0: arrow, color = "▼", "rose"

                    is_locked = doc.get("is_locked", True) if doc else True
                    if current_hour >= 9 and is_locked:
                        if data["close"] != prev_close:
                            is_locked = False
                            print(f"  🔓 [MỞ SỔ] {sym}: Giá biến động từ {prev_close} -> {data['close']}", flush=True)

                    today_record = doc if (doc and doc.get("trading_date") == real_today) else None
                    
                    if today_record:
                        final_open = today_record.get("open", data["open"])
                        final_high = max(today_record.get("high", data["high"]), data["high"])
                        final_low = min(today_record.get("low", data["low"]), data["low"])
                    else:
                        final_open = data["open"]
                        final_high = data["high"]
                        final_low = data["low"]

                    collection.update_one({"symbol": sym}, {"$set": {
                        "close": data["close"], "prev_close": prev_close, 
                        "open": final_open,
                        "high": final_high,
                        "low": final_low,
                        "volume": data["volume"],
                        "volume_percent": vol_pct, "avg_volume_20": round(avg_vol_20, 2),
                        "price_change_percent": pct_change,
                        "price_change_available": prev_close > 0,
                        "price_arrow": arrow, "price_color": color,
                        "trading_date": real_today, 
                        "updated_at": _now_str(), "is_locked": is_locked, "is_incomplete": False
                    }}, upsert=True)
                    
                    print(f"  ✅ [OK] {sym}: Giá {data['close']:,} (O:{final_open} H:{final_high} L:{final_low}) | KL: {data['volume']:,} | Vol%: {vol_pct}%", flush=True)
                    
                    if prev_close > 0 and abs(pct_change) >= 3.0 and vol_pct >= 120.0:
                        alert_batch.append({"sym": sym, "pct_change": pct_change, "volume_percent": vol_pct, "trend": "🔴 GIẢM" if pct_change < 0 else "🟢 TĂNG"})
                        
                    if is_closing_time:
                        has_traded_today = (not is_locked) or (data.get("volume", 0) > 0)
                        
                        if has_traded_today:
                            d1_bars = hist_doc.get("history_d1", []) if hist_doc else []
                            d1_bars = [b for b in d1_bars if b.get("time") != real_today]
                            
                            d1_bars.append({
                                "time": real_today, 
                                "open": final_open, 
                                "high": final_high, 
                                "low": final_low, 
                                "close": data["close"], 
                                "volume": data["volume"]
                            })
                            d1_bars.sort(key=lambda x: x["time"])
                            
                            history_col.update_one({"symbol": sym}, {"$set": {"history_d1": d1_bars}}, upsert=True)
                            collection.update_one({"symbol": sym}, {"$set": {"is_locked": True, "chot_so_today": real_today}})
                            
                            if hist_doc is None: hist_doc = {"symbol": sym}
                            hist_doc["history_d1"] = d1_bars
                            pending_history_files[f"history/{sym}.json"] = hist_doc
                        else:
                            collection.update_one({"symbol": sym}, {"$set": {"is_locked": True, "chot_so_today": real_today}})
                else:
                    print(f"  ❌ [LỖI] {sym}: Không lấy được dữ liệu.", flush=True)
                    collection.update_one({"symbol": sym}, {"$set": {"is_incomplete": True, "last_error_at": _now_str()}}, upsert=True)
            time.sleep(1)

        with _history_cache["lock"]:
            _history_cache["data"] = None
            _history_cache["timestamp"] = 0

        if is_closing_time and pending_history_files:
            files_to_push = list(pending_history_files.keys())
            for i in range(0, len(files_to_push), 10):
                chunk_files = files_to_push[i:i+10]
                chunk_data = {k: pending_history_files[k] for k in chunk_files}
                commit_multiple_files_to_github(chunk_data, f"Cập nhật nến {real_today}")
                time.sleep(2)

        if alert_batch:
            now_ts = time.time()
            if (now_ts - LAST_TELEGRAM_TIME) >= 7200:
                alert_batch.sort(key=lambda x: x["volume_percent"], reverse=True)
                msg_lines = [f"🚨 <b>CẢNH BÁO DÒNG TIỀN ĐỘT BIẾN</b>"]
                msg_lines.append(f"⏰ Lúc: {_now_str()}")
                for item in alert_batch:
                    msg_lines.append(f"• <b>{item['sym']}</b>: {item['pct_change']:+.2f}% {item['trend']} | Vol: {item['volume_percent']:.0f}%")
                send_telegram("\n".join(msg_lines))
                print(f"[TELEGRAM] 🚀 Đã gửi cảnh báo {len(alert_batch)} mã.", flush=True)
                LAST_TELEGRAM_TIME = now_ts
            else:
                print(f"[TELEGRAM] ⏳ Có {len(alert_batch)} mã đột biến nhưng chưa đủ 2 tiếng, bỏ qua gửi tin.", flush=True)
    finally:
        is_crawling = False
        stop_crawl_requested = False

# ===================== GITHUB HELPER =====================
def get_github_file_data(file_path):
    if not all([GITHUB_TOKEN, GITHUB_USERNAME, GITHUB_REPO]): return None, None
    api_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/contents/{file_path}?ref={GITHUB_BRANCH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        res = requests.get(api_url, headers=headers, timeout=10)
        if res.status_code == 200: return json.loads(base64.b64decode(res.json()["content"]).decode("utf-8")), res.json()["sha"]
        return None, None
    except: return None, None

def commit_multiple_files_to_github(files_to_update: dict, commit_message: str, max_retries: int = 3):
    if not all([GITHUB_TOKEN, GITHUB_USERNAME, GITHUB_REPO]) or not files_to_update: return False
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    base_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}"

    for attempt in range(max_retries):
        try:
            ref_res = requests.get(f"{base_url}/git/ref/heads/{GITHUB_BRANCH}", headers=headers, timeout=10)
            if ref_res.status_code != 200: time.sleep(2); continue
            base_commit_sha = ref_res.json()["object"]["sha"]

            tree_items = []
            for path, content_dict in files_to_update.items():
                b64_content = base64.b64encode(json.dumps(content_dict, ensure_ascii=False, indent=4).encode("utf-8")).decode("utf-8")
                blob_res = requests.post(f"{base_url}/git/blobs", headers=headers, json={"content": b64_content, "encoding": "base64"}, timeout=10)
                if blob_res.status_code != 201: break
                tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_res.json()["sha"]})
            else:
                tree_res = requests.post(f"{base_url}/git/trees", headers=headers, json={"base_tree": base_commit_sha, "tree": tree_items}, timeout=15)
                if tree_res.status_code != 201: time.sleep(2); continue
                commit_res = requests.post(f"{base_url}/git/commits", headers=headers, json={"message": commit_message, "tree": tree_res.json()["sha"], "parents": [base_commit_sha]}, timeout=10)
                if commit_res.status_code != 201: time.sleep(2); continue
                update_res = requests.patch(f"{base_url}/git/refs/heads/{GITHUB_BRANCH}", headers=headers, json={"sha": commit_res.json()["sha"], "force": True}, timeout=10)
                if update_res.status_code == 200: return True
            time.sleep(2)
        except Exception: time.sleep(2)
    return False

# ===================== CHẤM ĐIỂM =====================
FALLBACK_SECTOR_MAPPING = {
    "NGAN_HANG": ["ACB","BID","CTG","TCB","MBB","STB","VPB","HDB","VIB","TPB","SHB","SSB","LPB","MSB","OCB","EIB","VCB"],
    "CHUNG_KHOAN": ["SSI","VCI","HCM","VIX","MBS","FTS","BSI","CTS","ORS"],
    "BAT_DONG_SAN": ["VHM","VIC","VRE","NVL","PDR","DIG","NLG","KDH","CEO","DXG","TCH","HDG","KBC","IDC","SZC","BCM","PHR","SJS","VPI"],
    "CONG_NGHE_BAN_LE": ["FPT","MWG","PNJ","FRT","DGW"],
    "DIEN_NUOC_HA_TANG": ["POW","REE","PC1","NT2","GAS","GEG","BWE","TDM"],
    "NGUYEN_VAT_LIEU_CHU_KY": ["HPG","HSG","NKG","DGC","DCM","DPM","GVR","PHR","DHC"],
    "SAN_XUAT_TIEU_DUNG_PHONG_THU": ["VNM","MSN","SAB","VHC","ANV","BVH","PLX","PVD","PVS","BSR","GMD","HAH","PVT","HVN","APH","ASM","DBC","KDC","MSH","PAN","SBT","DRC","CSM","BMP","DHG","CTD","VCG","HHV","GEX"],
}
SECTOR_MAPPING = FALLBACK_SECTOR_MAPPING
SUB_SECTOR_MAPPING = {
    "TECHNOLOGY": ["FPT"],
    "ENERGY": ["GAS", "POW", "PC1", "NT2", "GEG"],
    "INSURANCE": ["BVH"], "PETROLEUM": ["PLX", "PVD", "PVS", "BSR"], "MARITIME": ["GMD", "HAH", "PVT"],
    "AVIATION": ["VJC", "HVN"], "CONSTRUCTION": ["CTD", "VCG", "HHV"],
    "BUILDING_MATERIALS": ["BMP"],
    "STEEL": ["HPG", "HSG", "NKG"],
    "FERTILIZER": ["DPM", "DCM"],
    "CONSUMER": ["VNM", "MSN", "SAB", "KDC"],
    "AGRI": ["VHC", "ANV", "DBC", "SBT", "ASM"],
    "HOLDING": ["VIC", "GEX"],
    "PHARMA": ["DHG"],
    "TEXTILE": ["MSH"],
    "RUBBER": ["DRC", "CSM"],
    "PLASTICS_CHEMICALS": ["APH"],
    "AGRI_PROCESSING": ["PAN"],
}
SUB_SECTOR_CONFIG = {
    "TECHNOLOGY": {"cap_pe": 25, "cap_pb": 4.0, "mos": 0.20, "primary_metric": "PE"},
    "NGAN_HANG": {"cap_pe": 14, "cap_pb": 1.8, "mos": 0.20, "primary_metric": "PB"},
    "CHUNG_KHOAN": {"cap_pe": 14, "cap_pb": 1.8, "mos": 0.25, "primary_metric": "PB"},
    "BAT_DONG_SAN": {"cap_pe": 12, "cap_pb": 2.0, "mos": 0.30, "primary_metric": "PB"},
    "CONG_NGHE_BAN_LE": {"cap_pe": 20, "cap_pb": 3.0, "mos": 0.20, "primary_metric": "PE"},
    "DIEN_NUOC_HA_TANG": {"cap_pe": 15, "cap_pb": 2.0, "mos": 0.15, "primary_metric": "PE"},
    "NGUYEN_VAT_LIEU_CHU_KY": {"cap_pe": 12, "cap_pb": 2.0, "mos": 0.30, "primary_metric": "PB"},
    "SAN_XUAT_TIEU_DUNG_PHONG_THU": {"cap_pe": 18, "cap_pb": 2.5, "mos": 0.25, "primary_metric": "PE"},
    "INSURANCE": {"cap_pe": 16, "cap_pb": 2.0, "mos": 0.25, "primary_metric": "PB"},
    "PETROLEUM": {"cap_pe": 12, "cap_pb": 1.5, "mos": 0.30, "primary_metric": "PB"},
    "MARITIME": {"cap_pe": 10, "cap_pb": 1.5, "mos": 0.30, "primary_metric": "PB"},
    "AVIATION": {"cap_pe": 15, "cap_pb": 3.0, "mos": 0.35, "primary_metric": "PB"},
    "CONSTRUCTION": {"cap_pe": 12, "cap_pb": 1.5, "mos": 0.30, "primary_metric": "PB"},
    "AGRI": {"cap_pe": 14, "cap_pb": 2.0, "mos": 0.25, "primary_metric": "PE"},
    "BUILDING_MATERIALS": {"cap_pe": 13, "cap_pb": 1.7, "mos": 0.25, "primary_metric": "PB"},
    "CONSUMER": {"cap_pe": 18, "cap_pb": 2.5, "mos": 0.20, "primary_metric": "PE"},
    "STEEL": {"cap_pe": 11, "cap_pb": 1.5, "mos": 0.30, "primary_metric": "PB"},
    "FERTILIZER": {"cap_pe": 12, "cap_pb": 1.7, "mos": 0.30, "primary_metric": "PE"},
    "HOLDING": {"cap_pe": 14, "cap_pb": 1.5, "mos": 0.30, "primary_metric": "PB"},
    "PHARMA": {"cap_pe": 20, "cap_pb": 2.5, "mos": 0.20, "primary_metric": "PE"},
    "TEXTILE": {"cap_pe": 12, "cap_pb": 1.5, "mos": 0.30, "primary_metric": "PB"},
    "RUBBER": {"cap_pe": 12, "cap_pb": 1.5, "mos": 0.30, "primary_metric": "PB"},
    "PLASTICS_CHEMICALS": {"cap_pe": 14, "cap_pb": 1.8, "mos": 0.25, "primary_metric": "PB"},
    "AGRI_PROCESSING": {"cap_pe": 14, "cap_pb": 1.8, "mos": 0.25, "primary_metric": "PB"},
}

def get_sector(symbol):
    for sector, symbols in SECTOR_MAPPING.items():
        if symbol in symbols: return sector
    return "SAN_XUAT_TIEU_DUNG_PHONG_THU"

def get_sub_sector(symbol):
    symbol = str(symbol or "").strip().upper()
    for sub, symbols in SUB_SECTOR_MAPPING.items():
        if symbol in symbols: return sub
    return get_sector(symbol)


def is_symbol_classified(symbol):
    """True only when the ticker is explicitly assigned to a known sector or subsector."""
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        return False
    return any(
        symbol in symbols
        for mapping in (SECTOR_MAPPING, SUB_SECTOR_MAPPING)
        for symbols in mapping.values()
    )

# FIX BUG #5: normalize_stock_data không ghi DB, trả update_fields cho caller
def _is_finite_number(value):
    if value is None or isinstance(value, bool):
        return False
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError, OverflowError):
        return False


def _finite_float(value, default=0.0):
    if not _is_finite_number(value):
        return default
    return float(value)


def _has_reported_ratio(data, key):
    """Only accept profitability ratios with an explicit source-availability flag.

    A non-zero vendor field is not proof of its denominator, period basis,
    consolidation scope or source lineage. Negative and zero values remain valid
    observations when the upstream ingestion explicitly confirms availability.
    """
    if not isinstance(data, dict):
        return False
    if data.get(f"{key}_available") is not True:
        return False
    return _is_finite_number(data.get(key))


def _bctc_period_is_current(latest_period, expected_period=None):
    """A scoring period is current only when the latest income statement reaches the expected quarter."""
    latest = str(latest_period or "").strip()
    expected = str(expected_period or _expected_latest_bctc_period()).strip()
    if not re.fullmatch(r"\d{4}-Q[1-4]", latest):
        return False
    if not re.fullmatch(r"\d{4}-Q[1-4]", expected):
        return False
    # Future quarters are malformed/future-dated source data, not proof of freshness.
    return latest == expected


def normalize_stock_data(symbol, data):
    """
    Chuẩn hóa dữ liệu. KHÔNG ghi DB (fix bug #5).
    Trả về dict normalized. Caller tự quyết định ghi DB nếu cần.
    """
    close_price = _finite_float(data.get("close", data.get("close_price", 0)))
    pe, pb = _finite_float(data.get("pe")), _finite_float(data.get("pb"))
    roe, roa, eps = (
        _finite_float(data.get("roe")),
        _finite_float(data.get("roa")),
        _finite_float(data.get("eps")),
    )
    
    eps_need_update = False
    update_fields = {}

    if eps >= 1000:
        eps = eps / 1000.0
        eps_need_update = True
    elif close_price > 0 and eps > (close_price * 5): 
        eps = eps / 1000.0
        eps_need_update = True
    if eps_need_update: 
        update_fields["eps"] = round(eps, 2)

    if update_fields:
        print(f"    [CHUẨN HÓA] {symbol}: Sửa lỗi đơn vị {list(update_fields.keys())} -> {update_fields}", flush=True)

    bctc = data.get("bctc_history", [])
    # Chỉ dùng tăng trưởng đã được tính từ hai quý tương ứng; không đọc nhầm
    # record đầu tiên trong mảng BCTC (mảng này là các dòng chỉ tiêu, không phải lịch sử tăng trưởng).
    profit_growth_yoy = _finite_float(data.get("profit_growth_yoy"))
    profit_growth_yoy_available = (
        data.get("profit_growth_yoy_available") is True
        and _is_finite_number(data.get("profit_growth_yoy"))
    )
    credit_growth_yoy = _finite_float(data.get("credit_growth_yoy"))
    bctc_latest_income_period = str(data.get("bctc_latest_income_period", "") or "").strip()
    bctc_expected_period = _expected_latest_bctc_period()
    bctc_period_is_current = _bctc_period_is_current(
        bctc_latest_income_period, bctc_expected_period
    )
    # Không suy ngược EPS từ P/E hoặc BVPS từ P/B vì sẽ tạo định giá vòng tròn.
    bvps = _finite_float(data.get("bvps"))
    
    return {
        "close_price": close_price, "pe": pe, "pb": pb, "roe": roe, "roa": roa, "eps": eps, "bvps": bvps,
        "dividend_yield": _finite_float(data.get("dividend_yield")),
        "cfo_latest": _finite_float(data.get("cfo_ttm", data.get("cfo_latest", 0))),
        "cfo_ttm": _finite_float(data.get("cfo_ttm", data.get("cfo_latest", 0))),
        "net_profit_ttm": _finite_float(data.get("net_profit_ttm")),
        "debt_to_equity": (
            _finite_float(data.get("debt_to_equity"))
            if data.get("debt_to_equity_available") is True
            and is_valid_debt_to_equity(data.get("debt_to_equity")) else 0.0
        ),
        "debt_to_equity_available": (
            data.get("debt_to_equity_available") is True
            and is_valid_debt_to_equity(data.get("debt_to_equity"))
        ),
        "total_liabilities_to_equity": _finite_float(data.get("total_liabilities_to_equity")),
        "total_liabilities_to_equity_available": data.get("total_liabilities_to_equity_available") is True,
        "gross_margin": (
            _finite_float(data.get("gross_margin"))
            if data.get("gross_margin_available") is True else 0.0
        ),
        "gross_margin_available": data.get("gross_margin_available") is True,
        "gross_margin_ttm": (
            _finite_float(data.get("gross_margin_ttm"))
            if data.get("gross_margin_ttm_available") is True else 0.0
        ),
        "gross_margin_ttm_available": data.get("gross_margin_ttm_available") is True,
        "profit_growth_yoy": profit_growth_yoy,
        "profit_growth_yoy_available": profit_growth_yoy_available,
        "bctc_latest_income_period": bctc_latest_income_period,
        "bctc_expected_period": bctc_expected_period,
        "bctc_period_is_current": bctc_period_is_current,
        "bctc_income_ttm_complete": bool(data.get("bctc_income_ttm_complete", False)),
        "bctc_cfo_ttm_complete": bool(data.get("bctc_cfo_ttm_complete", False)),
        "bctc_balance_sheet_valid": bool(data.get("bctc_balance_sheet_valid", False)),
        "roe_available": _has_reported_ratio(data, "roe"),
        "roa_available": _has_reported_ratio(data, "roa"),
        "credit_growth_yoy": credit_growth_yoy,
        "credit_growth_yoy_available": (data.get("credit_growth_yoy_available") is True and _is_finite_number(data.get("credit_growth_yoy"))),
        "bank_cir": _finite_float(data.get("bank_cir")),
        "bank_cir_available": (data.get("bank_cir_available") is True and _is_finite_number(data.get("bank_cir"))),
        "_update_fields": update_fields  # Caller dùng để ghi DB nếu cần
    }
def compute_sector_medians(normalized_db):
    """Build peer medians only from explicitly classified tickers and sufficient samples."""
    buckets = {}
    for symbol, nd in normalized_db.items():
        if not is_symbol_classified(symbol):
            continue
        for key in dict.fromkeys((get_sub_sector(symbol), get_sector(symbol))):
            b = buckets.setdefault(key, {"pe": [], "pb": [], "roe": []})
            if nd.get("pe", 0) > 0: b["pe"].append(nd["pe"])
            if nd.get("pb", 0) > 0: b["pb"].append(nd["pb"])
            if nd.get("roe_available", False): b["roe"].append(nd["roe"])
    medians = {}
    for key, b in buckets.items():
        pe_n, pb_n, roe_n = len(b["pe"]), len(b["pb"]), len(b["roe"])
        if max(pe_n, pb_n, roe_n) < 3:
            continue
        medians[key] = {
            "pe": float(np.median(b["pe"])) if pe_n >= 3 else 0.0,
            "pb": float(np.median(b["pb"])) if pb_n >= 3 else 0.0,
            "roe": float(np.median(b["roe"])) if roe_n >= 3 else 0.0,
            "n": max(pe_n, pb_n, roe_n),
            "n_pe": pe_n, "n_pb": pb_n, "n_roe": roe_n,
        }
    return medians

def get_cached_sector_medians(collection_obj, force_refresh=False):
    """Reuse peer medians briefly instead of scanning every stock per request.

    Cache is process-local and bounded. A 180-second TTL limits staleness;
    callers can force refresh after a known bulk data update.
    """
    cache_key = "sector_medians:v1"
    if not force_refresh:
        cached = _SECTOR_MEDIAN_CACHE.get(cache_key)
        if cached is not None:
            return cached, True

    peer_projection = {
        "_id": 0, "symbol": 1, "close": 1, "close_price": 1,
        "pe": 1, "pb": 1, "roe": 1, "roa": 1, "eps": 1,
        "dividend_yield": 1, "cfo_latest": 1, "net_profit_ttm": 1,
        "debt_to_equity": 1, "gross_margin": 1
    }
    normalized_peers = {}
    for peer in collection_obj.find({}, peer_projection):
        peer_symbol = peer.get("symbol")
        if peer_symbol:
            normalized_peers[peer_symbol] = normalize_stock_data(peer_symbol, peer)
    medians = compute_sector_medians(normalized_peers)
    _SECTOR_MEDIAN_CACHE.set(cache_key, medians)
    return medians, False


def get_median_for_symbol(symbol, sector_medians):
    sub, sector = get_sub_sector(symbol), get_sector(symbol)
    if not is_symbol_classified(symbol):
        return {"pe": 0.0, "pb": 0.0, "roe": 0.0, "n": 0}, "UNCLASSIFIED"
    if sub in sector_medians and sector_medians[sub].get("n", 0) >= 3:
        return sector_medians[sub], sub
    if sector in sector_medians and sector_medians[sector].get("n", 0) >= 3:
        return sector_medians[sector], sector
    # Không tự đặt P/E 12x hoặc P/B 2x khi thiếu nhóm so sánh đủ mẫu.
    return {"pe": 0.0, "pb": 0.0, "roe": 0.0, "n": 0}, sector

def compute_fair_value_zone(symbol, nd, sector_median, config, group_label):
    eps, bvps, close = nd["eps"], nd["bvps"], nd["close_price"]
    cfo, npat = nd["cfo_latest"], nd["net_profit_ttm"]
    roe = nd.get("roe", 0)
    roe_is_verified = nd.get("roe_available", False) is True
    methods = {}
    med_pe, med_pb, med_roe = sector_median.get("pe", 0.0), sector_median.get("pb", 0.0), sector_median.get("roe", 0.0)
    cap_pe, cap_pb, mos = config["cap_pe"], config["cap_pb"], config["mos"]
    is_financial = group_label in ("NGAN_HANG", "CHUNG_KHOAN", "INSURANCE")
    primary_metric = str(config.get("primary_metric", "PE")).upper()
    # Ngân hàng, bảo hiểm và holding chỉ dùng P/B trong mô hình hiện tại:
    # chưa có dữ liệu đủ tin cậy để định giá lợi nhuận/giá trị công ty con bằng P/E hoặc Graham.
    pb_only_groups = (
        "NGAN_HANG", "INSURANCE", "HOLDING", "PETROLEUM",
        "STEEL", "FERTILIZER", "NGUYEN_VAT_LIEU_CHU_KY",
        "TEXTILE", "RUBBER", "PLASTICS_CHEMICALS", "AGRI_PROCESSING",
    )
    
    # Chỉ dùng P/E khi lợi nhuận TTM dương; P/E là phương pháp chính/phụ theo cấu hình ngành.
    if group_label not in pb_only_groups and eps > 0 and npat > 0 and med_pe > 0:
        roe_bonus = 1.15 if (roe_is_verified and med_roe > 0 and roe > med_roe) else 1.0
        methods["PE_method"] = round(eps * min(med_pe * roe_bonus, cap_pe), 0)
    if bvps > 0 and med_pb > 0:
        methods["PB_method"] = round(bvps * min(med_pb, cap_pb), 0)
    # Graham chỉ phù hợp khi P/E là phương pháp chính; tránh áp vào nhóm chu kỳ/PB.
    if primary_metric == "PE" and not is_financial and group_label not in pb_only_groups and eps > 0 and npat > 0 and bvps > 0:
        methods["Graham"] = round((22.5 * eps * bvps) ** 0.5, 0)

    # CFO là thước đo chất lượng lợi nhuận, không phải phương pháp định giá độc lập.
    # Không cộng thêm 10% vào P/E để tránh đếm trùng cùng một nền lợi nhuận.
    valid_methods = {
        k: v for k, v in methods.items()
        if isinstance(v, (int, float)) and np.isfinite(v) and v > 0
    }

    if not valid_methods:
        return {
            "fair_low": 0, "fair_high": 0, "graham": 0, "methods": {},
            "mos_pct": 0.0,
            "mos_target_pct": round(mos * 100, 1),
            "margin_of_safety_pct": 0.0,
            "upside_to_fair_low_pct": 0.0,
            "rationale": "Không đủ DL.", "discount_to_fair_pct": 0.0,
            "verdict": "KHÔNG ĐỦ DỮ LIỆU"
        }

    # Ưu tiên phương pháp chính theo cấu hình ngành, phương pháp phụ có trọng số thấp hơn.
    weights = (
        {"PB_method": 3, "PE_method": 1, "Graham": 0}
        if primary_metric == "PB"
        else {"PE_method": 3, "PB_method": 1, "Graham": 2}
    )
    weighted = []
    for k, v in valid_methods.items(): weighted.extend([v] * int(weights.get(k, 1) * 2))
    weighted.sort()
    n = len(weighted)
    fair_high = weighted[n // 2] if n % 2 else (weighted[n // 2 - 1] + weighted[n // 2]) / 2
    
    fair_low = fair_high * (1 - mos)
    # MOS thực tế dùng Fair Low làm mẫu số; upside dùng giá hiện tại làm mẫu số.
    actual_mos = ((fair_low - close) / fair_low * 100) if fair_low > 0 and close > 0 else 0.0
    upside_to_fair_low = ((fair_low - close) / close * 100) if close > 0 else 0.0
    verdict = "KHÔNG CÓ GIÁ" if close <= 0 else ("RẺ - TRONG VÙNG MUA" if close <= fair_low else ("HỢP LÝ - CHỜ ĐIỀU CHỈNH" if close <= fair_high else ("HƠI ĐẮT - KHÔNG MUA" if close <= fair_high * 1.2 else "ĐẮT - TRÁNH")))

    return {
        "fair_low": round(fair_low, 0),
        "fair_high": round(fair_high, 0),
        "graham": round(valid_methods.get("Graham", 0), 0),
        "methods": {k: round(v, 0) for k, v in valid_methods.items()},
        # mos_pct giữ tương thích nhưng nay biểu thị MOS thực tế.
        "mos_pct": round(actual_mos, 1),
        "mos_target_pct": round(mos * 100, 1),
        "margin_of_safety_pct": round(actual_mos, 1),
        "upside_to_fair_low_pct": round(upside_to_fair_low, 1),
        "rationale": f"[{group_label}]",
        # Alias cũ vẫn giữ để không phá API; giá trị là upside, không phải MOS.
        "discount_to_fair_pct": round(upside_to_fair_low, 1),
        "verdict": verdict
    }

def is_valid_debt_to_equity(value):
    """Zero leverage is valid when the balance sheet itself is verified."""
    try:
        ratio = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return np.isfinite(ratio) and ratio >= 0


def cap_fundamental_score(score, available_points, max_points=50):
    """A fundamental score cannot exceed the weight supported by verified inputs."""
    try:
        available = float(available_points)
        earned = float(score)
        maximum = float(max_points)
    except (TypeError, ValueError, OverflowError):
        return 0
    if not np.isfinite(available) or not np.isfinite(earned) or not np.isfinite(maximum):
        return 0
    ceiling = max(0.0, min(maximum, available))
    return int(max(0.0, min(earned, ceiling)))


def compute_final_decision(fund_score, tech_score, val_score, total_score, verdict, cfo, npat, roe, debt_to_equity, discount_to_fair_pct=0.0, close_price=0.0, fair_high=0.0, fair_low=0.0, sector="", fundamental_coverage_pct=None, bctc_period_is_current=None):
    is_bank_or_sec = sector in ("NGAN_HANG", "CHUNG_KHOAN", "INSURANCE")
    try:
        coverage = float(fundamental_coverage_pct)
    except (TypeError, ValueError, OverflowError):
        coverage = None
    if coverage is None or not np.isfinite(coverage) or coverage < 65 or coverage > 100:
        coverage_label = (
            f"{coverage:.1f}%" if coverage is not None and np.isfinite(coverage)
            else "không xác định"
        )
        return ("⚠️ THIẾU DỮ LIỆU", "amber", f"Độ bao phủ PTCB không hợp lệ hoặc chỉ {coverage_label}; cần dữ liệu hợp lệ từ 65% đến 100% trước khi kết luận hoặc khuyến nghị mua.")
    if bctc_period_is_current is False:
        return ("⚠️ BCTC CHƯA CẬP NHẬT", "amber", "Kỳ BCTC mới nhất chưa đạt kỳ báo cáo dự kiến; tạm khóa kết luận cho đến khi dữ liệu được cập nhật.")
    if bctc_period_is_current is not True:
        return ("⚠️ BCTC CHƯA XÁC MINH", "amber", "Thiếu xác nhận kỳ BCTC mới nhất đạt kỳ báo cáo dự kiến; tạm khóa kết luận cho đến khi xác minh dữ liệu.")
    if fund_score < 20: return ("❌ TRÁNH", "rose", f"PTCB yếu")
    if npat < 0:
        return ("⚠️ CẢNH BÁO LỢI NHUẬN ÂM", "amber", "LNST TTM âm; chưa đủ cơ sở xác nhận điều kiện mua.")
    if not is_bank_or_sec and cfo < 0 and npat > 0:
        return ("⚠️ CẢNH BÁO LỢI NHUẬN ẢO", "amber", "CFO TTM âm trong khi LNST TTM dương.")
    if close_price > 0 and fair_high > 0 and close_price > fair_high * 1.2: return ("❌ TRÁNH", "rose", f"Giá vượt xa fair_high.")
    if close_price > 0 and fair_high > 0 and close_price > fair_high: return ("⚠️ CHỜ VỀ VÙNG MUA", "amber", f"Giá cao hơn fair_high.")
    if (verdict == "RẺ - TRONG VÙNG MUA" and fund_score >= 40 and tech_score >= 15 and val_score >= 24 and total_score >= 75 and discount_to_fair_pct >= 5): return ("🎯 MUA MẠNH", "emerald", "ĐỦ 4 YẾU TỐ.")
    if (verdict == "RẺ - TRONG VÙNG MUA" and fund_score >= 30 and tech_score >= 10 and val_score >= 18 and total_score >= 60): return ("✅ MUA TÍCH SẢN", "emerald", "ĐỦ 4 YẾU TỐ.")
    if (fund_score >= 30 and val_score >= 18 and verdict in ("HỢP LÝ - CHỜ ĐIỀU CHỈNH", "HƠI ĐẮT - KHÔNG MUA")): return ("⚠️ CHỜ VỀ VÙNG MUA", "amber", "DN tốt nhưng giá chưa rẻ.")
    if (verdict == "RẺ - TRONG VÙNG MUA" and fund_score >= 30 and val_score >= 18 and tech_score < 10): return ("⏳ CHỜ XÁC NHẬN KỸ THUẬT", "amber", "Chờ tín hiệu.")
    if (verdict == "RẺ - TRONG VÙNG MUA" and fund_score >= 30 and val_score < 18): return ("⏳ CHỜ ĐỊNH GIÁ TỐT HƠN", "amber", "Định giá chưa hấp dẫn.")
    return ("👀 THEO DÕI", "slate", "Chưa đủ ĐK.")

def evaluate_stock_from_db(symbol, nd, sector_median, group_label, config, tech_score_raw=0, tech_status="Chưa đủ dữ liệu kỹ thuật", pattern="Chưa xác định", technical_details=None, existing_ai_data=None, history_bars=None):
    sector = get_sector(symbol)
    profile_id = (
        normalize_profile_id(group_label)
        or normalize_profile_id(sector)
        or "UNCLASSIFIED"
    )
    sector_profile = get_industry_profile(profile_id)
    is_symbol_classified_flag = is_symbol_classified(symbol)
    is_insurance = str(group_label or "").upper() == "INSURANCE"
    is_financial_group = sector in ("NGAN_HANG", "CHUNG_KHOAN") or is_insurance
    is_technology = str(group_label or "").upper() == "TECHNOLOGY"
    is_petroleum = str(group_label or "").upper() == "PETROLEUM" or sector == "PETROLEUM"
    is_energy = str(group_label or "").upper() == "ENERGY" and not is_petroleum
    is_utility = sector == "DIEN_NUOC_HA_TANG" and not is_energy
    is_steel = str(group_label or "").upper() == "STEEL"
    is_fertilizer = str(group_label or "").upper() == "FERTILIZER"
    is_cyclical_material = sector == "NGUYEN_VAT_LIEU_CHU_KY" and not is_steel and not is_fertilizer
    is_maritime = str(group_label or "").upper() == "MARITIME"
    is_consumer = str(group_label or "").upper() == "CONSUMER"
    is_building_materials = str(group_label or "").upper() == "BUILDING_MATERIALS"
    is_agri = str(group_label or "").upper() == "AGRI"
    is_aviation = str(group_label or "").upper() == "AVIATION"
    is_construction = str(group_label or "").upper() == "CONSTRUCTION"
    is_holding = str(group_label or "").upper() == "HOLDING"
    is_pharma = str(group_label or "").upper() == "PHARMA"
    is_textile = str(group_label or "").upper() == "TEXTILE"
    is_rubber = str(group_label or "").upper() == "RUBBER"
    is_plastics_chemicals = str(group_label or "").upper() == "PLASTICS_CHEMICALS"
    is_agri_processing = str(group_label or "").upper() == "AGRI_PROCESSING"
    is_retail = sector == "CONG_NGHE_BAN_LE" and not is_technology

    # Các nhóm có scorecard riêng không được cộng thêm điểm từ nhánh tổng quát.
    uses_generic_fundamental_addons = (
        is_symbol_classified_flag
        and sector not in (
            "NGAN_HANG", "CHUNG_KHOAN", "BAT_DONG_SAN",
            "NGUYEN_VAT_LIEU_CHU_KY",
        )
        and not is_insurance
        and not is_technology
        and not is_retail
        and not is_energy
        and not is_utility
        and not is_maritime
        and not is_consumer
        and not is_building_materials
        and not is_steel
        and not is_fertilizer
        and not is_agri
        and not is_aviation
        and not is_construction
        and not is_holding
        and not is_petroleum
        and not is_pharma
        and not is_textile
        and not is_rubber
        and not is_plastics_chemicals
        and not is_agri_processing
    )
    pe, pb, roe, roa = nd["pe"], nd["pb"], nd["roe"], nd["roa"]
    close_price, cfo_latest, net_profit_ttm = nd["close_price"], nd["cfo_latest"], nd["net_profit_ttm"]
    debt_to_equity, profit_growth_yoy, credit_growth_yoy = nd["debt_to_equity"], nd["profit_growth_yoy"], nd["credit_growth_yoy"]
    
    data_warnings = []
    if close_price <= 0: data_warnings.append("Thiếu Giá")
    if pe <= 0 and sector not in ["NGAN_HANG", "CHUNG_KHOAN"] and not is_insurance and not is_holding: data_warnings.append("Thiếu P/E")
    if pb <= 0: data_warnings.append("Thiếu P/B")
    if not nd.get("roe_available", False): data_warnings.append("Thiếu ROE")
    if not nd.get("bctc_period_is_current", False):
        data_warnings.append(
            f"BCTC chưa cập nhật đến kỳ dự kiến {nd.get('bctc_expected_period', 'không rõ')} "
            f"(kỳ mới nhất: {nd.get('bctc_latest_income_period') or 'không rõ'})"
        )
    if not nd.get("bctc_income_ttm_complete", False): data_warnings.append("BCTC chưa đủ 4 quý liên tiếp để tính LNST TTM")
    if not nd.get("bctc_cfo_ttm_complete", False) and not is_financial_group:
        data_warnings.append("CFO chưa đủ 4 quý liên tiếp để tính CFO TTM")
    if not nd.get("bctc_balance_sheet_valid", False): data_warnings.append("Thiếu dữ liệu bảng cân đối kế toán hợp lệ")
    is_data_incomplete = len(data_warnings) > 0

    fund_score = 0
    fund_notes = []
    
    growth_available = bool(nd.get("profit_growth_yoy_available", False))
    if nd.get("roe_available", False) and roe < 0:
        fund_notes.append("ROE âm — chỉ tiêu đã có dữ liệu nhưng không tạo điểm sinh lời")
    if nd.get("roa_available", False) and roa < 0:
        fund_notes.append("ROA âm — chỉ tiêu đã có dữ liệu nhưng không tạo điểm hiệu quả tài sản")
    # Group label có thể là ngành con (ví dụ INSURANCE) trong khi sector
    # tổng quát vẫn là SAN_XUAT_TIEU_DUNG_PHONG_THU.
    fundamental_score_available = 0
    fundamental_missing_groups = []
    if sector == "NGAN_HANG":
        # 8 tăng trưởng, 10 sinh lời, 16 chất lượng tài sản,
        # 10 vốn/nguồn vốn, 6 hiệu quả. Không phân phối lại điểm thiếu.
        if nd.get("credit_growth_yoy_available", False):
            fundamental_score_available += 8
            if credit_growth_yoy >= 12:
                fund_score += 8
                fund_notes.append("Tăng trưởng tín dụng tốt")
            elif credit_growth_yoy >= 8:
                fund_score += 5
            elif credit_growth_yoy > 0:
                fund_score += 3
            elif credit_growth_yoy < 0:
                fund_notes.append("Tín dụng suy giảm")
        else:
            fundamental_missing_groups.append("Tăng trưởng tín dụng (8 điểm)")
            fund_notes.append("Chưa xác minh được tăng trưởng dư nợ cho vay")

        if nd.get("roe_available", False):
            fundamental_score_available += 7
            if roe >= 18: fund_score += 7
            elif roe >= 14: fund_score += 5
            elif roe >= 10: fund_score += 3
            elif roe > 0:
                fund_score += 1
        else:
            fundamental_missing_groups.append("ROE (7 điểm)")
        if nd.get("roa_available", False):
            fundamental_score_available += 3
            if roa >= 1.5: fund_score += 3
            elif roa >= 1.0: fund_score += 2
            elif roa >= 0.5: fund_score += 1
        else:
            fundamental_missing_groups.append("ROA (3 điểm)")
        fund_notes.append("Đánh giá sinh lời theo ROE/ROA")

        if nd.get("bank_cir_available", False):
            fundamental_score_available += 6
            cir = nd.get("bank_cir", 0)
            if cir <= 40: fund_score += 6
            elif cir <= 45: fund_score += 5
            elif cir <= 50: fund_score += 4
            elif cir <= 55: fund_score += 2
            else: fund_notes.append("CIR cao, hiệu quả chi phí yếu")
        else:
            fundamental_missing_groups.append("Hiệu quả CIR (6 điểm)")

        fundamental_missing_groups.extend([
            "Chất lượng tài sản: NPL/nợ nhóm 2/bao phủ nợ xấu (16 điểm)",
            "Vốn và nguồn vốn: CAR/CASA/LDR (10 điểm)"
        ])
    elif is_insurance:
        # Không dùng CFO/D/E kiểu doanh nghiệp sản xuất cho bảo hiểm.
        if growth_available:
            fundamental_score_available += 8
            if profit_growth_yoy >= 15: fund_score += 8
            elif profit_growth_yoy >= 5: fund_score += 5
            elif profit_growth_yoy >= 0: fund_score += 2
            else: fund_notes.append("Lợi nhuận bảo hiểm suy giảm")
        else:
            fundamental_missing_groups.append("Tăng trưởng lợi nhuận (8 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 7
            if roe >= 15: fund_score += 7
            elif roe >= 10: fund_score += 5
            elif roe > 0:
                fund_score += 2
        else:
            fundamental_missing_groups.append("ROE (7 điểm)")
        if nd.get("roa_available", False):
            fundamental_score_available += 3
            if roa >= 1.5: fund_score += 3
            elif roa >= 0.8: fund_score += 2
            elif roa > 0:
                fund_score += 1
        else:
            fundamental_missing_groups.append("ROA (3 điểm)")
        fundamental_missing_groups.extend([
            "Dự phòng nghiệp vụ và khả năng thanh toán (14 điểm)",
            "Hiệu quả khai thác/bồi thường (10 điểm)",
            "Chất lượng danh mục đầu tư (8 điểm)"
        ])
    elif sector == "CHUNG_KHOAN":
        # Công ty chứng khoán: 8 tăng trưởng, 10 sinh lời;
        # rủi ro margin, thanh khoản/vốn và hiệu quả chưa có dữ liệu chuẩn thì để thiếu.
        if growth_available:
            fundamental_score_available += 8
            if profit_growth_yoy >= 20: fund_score += 8; fund_notes.append("LN tăng mạnh")
            elif profit_growth_yoy >= 10: fund_score += 5
            elif profit_growth_yoy >= 0: fund_score += 2
            else: fund_notes.append("LN suy giảm YoY")
        else:
            fundamental_missing_groups.append("Tăng trưởng lợi nhuận (8 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 10
            if roe >= 15: fund_score += 10; fund_notes.append("ROE cao")
            elif roe >= 10: fund_score += 7
            elif roe >= 5: fund_score += 4
            elif roe > 0:
                fund_score += 1
        else:
            fundamental_missing_groups.append("Sinh lời ROE (10 điểm)")
        fundamental_missing_groups.extend([
            "Rủi ro dư nợ margin và chất lượng tài sản (12 điểm)",
            "Thanh khoản và an toàn vốn (12 điểm)",
            "Hiệu quả chi phí (8 điểm)"
        ])
    elif sector == "BAT_DONG_SAN" and not is_holding:
        # Bất động sản: BCTC hiện chưa có dữ liệu dự án/pháp lý/tồn kho theo dự án.
        # Không thay RNAV bằng P/B hoặc tự suy diễn presales.
        if growth_available:
            fundamental_score_available += 8
            if profit_growth_yoy >= 15: fund_score += 8
            elif profit_growth_yoy >= 5: fund_score += 5
            elif profit_growth_yoy >= 0: fund_score += 2
            else: fund_notes.append("Lợi nhuận bất động sản suy giảm")
        else:
            fundamental_missing_groups.append("Tăng trưởng lợi nhuận (8 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 8
            if roe >= 15: fund_score += 8
            elif roe >= 10: fund_score += 5
            elif roe > 0:
                fund_score += 2
        else:
            fundamental_missing_groups.append("Sinh lời ROE (8 điểm)")
        if (
            nd.get("bctc_balance_sheet_valid", False)
            and nd.get("debt_to_equity_available", False) is True
            and is_valid_debt_to_equity(debt_to_equity)
        ):
            fundamental_score_available += 12
            if debt_to_equity <= 1.0: fund_score += 12
            elif debt_to_equity <= 2.0: fund_score += 8
            elif debt_to_equity <= 3.0: fund_score += 4
            else: fund_notes.append("Đòn bẩy bất động sản cao")
        else:
            fundamental_missing_groups.append("Nợ vay và thanh khoản (12 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 8
            if net_profit_ttm > 0 and cfo_latest >= net_profit_ttm: fund_score += 8
            elif net_profit_ttm > 0 and cfo_latest > 0: fund_score += 5
            else: fund_notes.append("Chuyển đổi tiền mặt yếu")
        else:
            fundamental_missing_groups.append("Chuyển đổi lợi nhuận thành tiền (8 điểm)")
        fundamental_missing_groups.append(
            "Tồn kho dự án, người mua trả tiền trước, tiến độ bàn giao và pháp lý (14 điểm)"
        )
    elif is_maritime:
        # Cảng/logistics: chưa có throughput, công suất và backlog; không tự ước lượng.
        if growth_available:
            fundamental_score_available += 10
            if profit_growth_yoy >= 15: fund_score += 10
            elif profit_growth_yoy >= 5: fund_score += 6
            elif profit_growth_yoy >= 0: fund_score += 3
            else: fund_notes.append("Lợi nhuận logistics suy giảm")
        else:
            fundamental_missing_groups.append("Tăng trưởng (10 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 10
            if roe >= 15: fund_score += 10
            elif roe >= 10: fund_score += 7
            elif roe > 0:
                fund_score += 3
        else:
            fundamental_missing_groups.append("Sinh lời ROE (10 điểm)")
        if (
            nd.get("bctc_balance_sheet_valid", False)
            and nd.get("debt_to_equity_available", False) is True
            and is_valid_debt_to_equity(debt_to_equity)
        ):
            fundamental_score_available += 6
            if debt_to_equity <= 0.8: fund_score += 6
            elif debt_to_equity <= 1.5: fund_score += 4
            elif debt_to_equity <= 2.5: fund_score += 2
        else:
            fundamental_missing_groups.append("Đòn bẩy và capex (một phần 6/10 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 10
            if net_profit_ttm > 0 and cfo_latest >= net_profit_ttm: fund_score += 10
            elif cfo_latest > 0: fund_score += 5
        else:
            fundamental_missing_groups.append("Chuyển đổi lợi nhuận thành tiền (10 điểm)")
        fundamental_missing_groups.extend([
            "Sản lượng hàng hóa, công suất khai thác và backlog (10 điểm)",
            "Phần capex còn lại trong nhóm đòn bẩy/capex (4 điểm)"
        ])
    elif is_consumer:
        # Hàng tiêu dùng: chưa có dữ liệu thị phần/brand mix và vòng quay vốn lưu động.
        if growth_available:
            fundamental_score_available += 10
            if profit_growth_yoy >= 15: fund_score += 10
            elif profit_growth_yoy >= 5: fund_score += 7
            elif profit_growth_yoy >= 0: fund_score += 3
            else: fund_notes.append("Tăng trưởng tiêu dùng suy giảm")
        else:
            fundamental_missing_groups.append("Tăng trưởng (10 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 12
            if roe >= 18: fund_score += 12
            elif roe >= 12: fund_score += 8
            elif roe >= 8: fund_score += 4
        else:
            fundamental_missing_groups.append("Sinh lời ROE (12 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 10
            if net_profit_ttm > 0 and cfo_latest >= net_profit_ttm: fund_score += 10
            elif net_profit_ttm > 0 and cfo_latest > 0: fund_score += 6
            elif cfo_latest > 0: fund_score += 3
        else:
            fundamental_missing_groups.append("Chuyển đổi tiền mặt (10 điểm)")
        fundamental_missing_groups.extend([
            "Thị phần, sức mạnh thương hiệu và cơ cấu sản phẩm (8 điểm)",
            "Vòng quay tồn kho và vốn lưu động (10 điểm)"
        ])
    elif is_agri:
        # Nông nghiệp: giá hàng hóa, tồn kho và vốn lưu động chưa có chuỗi chuẩn.
        if growth_available:
            fundamental_score_available += 8
            if profit_growth_yoy >= 15: fund_score += 8
            elif profit_growth_yoy >= 5: fund_score += 5
            elif profit_growth_yoy >= 0: fund_score += 2
        else:
            fundamental_missing_groups.append("Tăng trưởng theo chu kỳ hàng hóa (8 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 12
            if roe >= 15: fund_score += 12
            elif roe >= 10: fund_score += 8
            elif roe > 0:
                fund_score += 3
        else:
            fundamental_missing_groups.append("Sinh lời ROE (12 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 10
            if net_profit_ttm > 0 and cfo_latest >= net_profit_ttm: fund_score += 10
            elif cfo_latest > 0: fund_score += 5
        else:
            fundamental_missing_groups.append("Chuyển đổi tiền mặt (10 điểm)")
        fundamental_missing_groups.extend([
            "Biên lợi nhuận và giá đầu vào/đầu ra (10 điểm)",
            "Tồn kho và vốn lưu động (10 điểm)"
        ])
    elif is_aviation:
        # Hàng không: chưa có dữ liệu hệ số sử dụng ghế, đội bay và nghĩa vụ thuê.
        if growth_available:
            fundamental_score_available += 10
            if profit_growth_yoy >= 15: fund_score += 10
            elif profit_growth_yoy >= 5: fund_score += 6
            elif profit_growth_yoy >= 0: fund_score += 3
        else:
            fundamental_missing_groups.append("Tăng trưởng (10 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 10
            if roe >= 15: fund_score += 10
            elif roe >= 8: fund_score += 6
            elif roe > 0:
                fund_score += 2
        else:
            fundamental_missing_groups.append("Sinh lời ROE (10 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 10
            if net_profit_ttm > 0 and cfo_latest >= net_profit_ttm: fund_score += 10
            elif cfo_latest > 0: fund_score += 5
        else:
            fundamental_missing_groups.append("Chuyển đổi tiền mặt (10 điểm)")
        fundamental_missing_groups.extend([
            "Hệ số sử dụng ghế và hiệu quả khai thác đội bay (10 điểm)",
            "Nợ thuê, đội bay và nghĩa vụ vốn (10 điểm)"
        ])
    elif is_construction:
        # Nhà thầu: phải thu, backlog và dòng tiền hợp đồng là rủi ro trọng yếu.
        if growth_available:
            fundamental_score_available += 8
            if profit_growth_yoy >= 15: fund_score += 8
            elif profit_growth_yoy >= 5: fund_score += 5
            elif profit_growth_yoy >= 0: fund_score += 2
        else:
            fundamental_missing_groups.append("Tăng trưởng (8 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 12
            if roe >= 15: fund_score += 12
            elif roe >= 10: fund_score += 8
            elif roe > 0:
                fund_score += 3
        else:
            fundamental_missing_groups.append("Sinh lời ROE (12 điểm)")
        if (
            nd.get("bctc_balance_sheet_valid", False)
            and nd.get("debt_to_equity_available", False) is True
            and is_valid_debt_to_equity(debt_to_equity)
        ):
            fundamental_score_available += 6
            if debt_to_equity <= 0.7: fund_score += 6
            elif debt_to_equity <= 1.2: fund_score += 4
            elif debt_to_equity <= 2: fund_score += 2
        else:
            fundamental_missing_groups.append("Đòn bẩy và vốn lưu động (6/10 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 10
            if net_profit_ttm > 0 and cfo_latest >= net_profit_ttm: fund_score += 10
            elif cfo_latest > 0: fund_score += 5
        else:
            fundamental_missing_groups.append("Chuyển đổi lợi nhuận thành tiền (10 điểm)")
        fundamental_missing_groups.extend([
            "Phải thu, backlog và chất lượng hợp đồng (10 điểm)",
            "Phần vốn lưu động còn thiếu trong nhóm đòn bẩy (4 điểm)"
        ])
    elif is_holding:
        # Holding: chưa có NAV từng công ty con/giá trị hợp lý khoản đầu tư.
        if nd.get("roe_available", False):
            fundamental_score_available += 8
            if roe >= 15: fund_score += 8
            elif roe >= 10: fund_score += 5
            elif roe > 0:
                fund_score += 2
        else:
            fundamental_missing_groups.append("ROE hợp nhất/ROE hoạt động cốt lõi (8 điểm)")
        if nd["dividend_yield"] > 0:
            fundamental_score_available += 10
            if nd["dividend_yield"] >= 5: fund_score += 10
            elif nd["dividend_yield"] >= 3: fund_score += 6
            else: fund_score += 3
        else:
            fundamental_missing_groups.append("Dòng cổ tức tiền mặt (10 điểm)")
        if (
            nd.get("bctc_balance_sheet_valid", False)
            and nd.get("debt_to_equity_available", False) is True
            and is_valid_debt_to_equity(debt_to_equity)
        ):
            fundamental_score_available += 6
            if debt_to_equity <= 0.5: fund_score += 6
            elif debt_to_equity <= 1.0: fund_score += 4
            else: fund_score += 1
        else:
            fundamental_missing_groups.append("Đòn bẩy công ty mẹ (6/10 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 10
            if cfo_latest > 0: fund_score += 10
        else:
            fundamental_missing_groups.append("CFO công ty mẹ và cổ tức nhận về (10 điểm)")
        fundamental_missing_groups.extend([
            "NAV và giá trị công ty con/liên kết (12 điểm)",
            "Phân bổ vốn và chất lượng đầu tư (4 điểm)"
        ])
    elif is_building_materials:
        # Vật liệu xây dựng: cần biên lợi nhuận theo chu kỳ, tồn kho và capex.
        if growth_available:
            fundamental_score_available += 8
            if profit_growth_yoy >= 15: fund_score += 8
            elif profit_growth_yoy >= 5: fund_score += 5
            elif profit_growth_yoy >= 0: fund_score += 2
        else:
            fundamental_missing_groups.append("Tăng trưởng chu kỳ (8 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 6
            if roe >= 15: fund_score += 6
            elif roe >= 10: fund_score += 4
            elif roe > 0:
                fund_score += 2
        else:
            fundamental_missing_groups.append("Sinh lời ROE (6 điểm)")
        if (
            nd.get("bctc_balance_sheet_valid", False)
            and nd.get("debt_to_equity_available", False) is True
            and is_valid_debt_to_equity(debt_to_equity)
        ):
            fundamental_score_available += 6
            if debt_to_equity <= 0.7: fund_score += 6
            elif debt_to_equity <= 1.2: fund_score += 4
            elif debt_to_equity <= 2.0: fund_score += 2
        else:
            fundamental_missing_groups.append("Nợ vay và capex (một phần 6/10 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 10
            if net_profit_ttm > 0 and cfo_latest >= net_profit_ttm: fund_score += 10
            elif cfo_latest > 0: fund_score += 5
        else:
            fundamental_missing_groups.append("Chuyển đổi tiền mặt (10 điểm)")
        fundamental_missing_groups.extend([
            "Biên lợi nhuận giữa chu kỳ (6 điểm)",
            "Tồn kho và vốn lưu động (10 điểm)",
            "Phần capex còn lại trong nhóm nợ vay/capex (4 điểm)"
        ])
    elif is_technology:
        # Công nghệ: tăng trưởng và sinh lời có dữ liệu; recurring revenue
        # và ROIC chưa được trích xuất nên không tự cộng điểm.
        if growth_available:
            fundamental_score_available += 12
            if profit_growth_yoy >= 20: fund_score += 12
            elif profit_growth_yoy >= 10: fund_score += 8
            elif profit_growth_yoy >= 0: fund_score += 4
            else: fund_notes.append("Lợi nhuận công nghệ suy giảm")
        else:
            fundamental_missing_groups.append("Tăng trưởng (12 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 9
            if roe >= 18: fund_score += 9
            elif roe >= 12: fund_score += 6
            elif roe > 0:
                fund_score += 3
        else:
            fundamental_missing_groups.append("ROE (9 điểm)")
        if nd.get("roa_available", False):
            fundamental_score_available += 3
            if roa >= 5: fund_score += 3
            elif roa >= 2.5: fund_score += 2
        else:
            fundamental_missing_groups.append("ROA (3 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 10
            if net_profit_ttm > 0 and cfo_latest >= net_profit_ttm:
                fund_score += 10
            elif net_profit_ttm > 0 and cfo_latest > 0:
                fund_score += 6
            elif cfo_latest > 0:
                fund_score += 3
            else:
                fund_notes.append("Chuyển đổi tiền mặt yếu")
        else:
            fundamental_missing_groups.append("Chuyển đổi lợi nhuận thành tiền (10 điểm)")
        fundamental_missing_groups.extend([
            "Doanh thu định kỳ và cơ cấu doanh thu (8 điểm)",
            "ROIC/năng suất vốn (8 điểm)"
        ])
    elif is_retail:
        # Bán lẻ: tồn kho/vòng quay cửa hàng chưa có dữ liệu chuẩn; chỉ chấm
        # tăng trưởng, sinh lời và chuyển đổi tiền mặt khi đủ kỳ báo cáo.
        if growth_available:
            fundamental_score_available += 10
            if profit_growth_yoy >= 20: fund_score += 10
            elif profit_growth_yoy >= 10: fund_score += 7
            elif profit_growth_yoy >= 0: fund_score += 3
            else: fund_notes.append("Lợi nhuận bán lẻ suy giảm")
        else:
            fundamental_missing_groups.append("Tăng trưởng (10 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 10
            if roe >= 18: fund_score += 10
            elif roe >= 12: fund_score += 7
            elif roe > 0:
                fund_score += 3
        else:
            fundamental_missing_groups.append("Sinh lời ROE (10 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 10
            if net_profit_ttm > 0 and cfo_latest >= net_profit_ttm:
                fund_score += 10
            elif net_profit_ttm > 0 and cfo_latest > 0:
                fund_score += 6
            elif cfo_latest > 0:
                fund_score += 3
        else:
            fundamental_missing_groups.append("Chuyển đổi lợi nhuận thành tiền (10 điểm)")
        fundamental_missing_groups.extend([
            "Vòng quay tồn kho và vốn lưu động (10 điểm)",
            "Năng suất cửa hàng/doanh thu trên diện tích (10 điểm)"
        ])
    elif is_petroleum:
        # Dầu khí: không lấy tăng trưởng LNST làm đại diện sản lượng/giá dầu,
        # không dùng biên lợi nhuận đỉnh chu kỳ thay cho biên chuẩn hóa.
        # Chỉ chấm phần BCTC phổ thông đã xác minh; các dữ liệu ngành để thiếu.
        if nd.get("roe_available", False):
            fundamental_score_available += 7
            if roe >= 15: fund_score += 7
            elif roe >= 10: fund_score += 5
            elif roe > 0:
                fund_score += 2
        else:
            fundamental_missing_groups.append("ROE (7 điểm)")
        if nd.get("roa_available", False):
            fundamental_score_available += 3
            if roa >= 5: fund_score += 3
            elif roa >= 2.5: fund_score += 2
        else:
            fundamental_missing_groups.append("ROA (3 điểm)")
        if (
            nd.get("bctc_balance_sheet_valid", False)
            and nd.get("debt_to_equity_available", False) is True
            and is_valid_debt_to_equity(debt_to_equity)
        ):
            fundamental_score_available += 5
            if debt_to_equity <= 0.5: fund_score += 5
            elif debt_to_equity <= 1.0: fund_score += 4
            elif debt_to_equity <= 2.0: fund_score += 2
        else:
            fundamental_missing_groups.append("Đòn bẩy tài chính (5 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 5
            if net_profit_ttm > 0 and cfo_latest >= net_profit_ttm: fund_score += 5
            elif cfo_latest > 0: fund_score += 3
        else:
            fundamental_missing_groups.append("CFO và chất lượng lợi nhuận (5 điểm)")
        fundamental_missing_groups.extend([
            "Cơ cấu thượng nguồn/trung nguồn/hạ nguồn (10 điểm)",
            "Sản lượng và giá bán thực hiện (10 điểm)",
            "Biên lợi nhuận chuẩn hóa giữa chu kỳ (12 điểm)",
            "Trữ lượng, vòng đời mỏ và hiệu quả capex (8 điểm)"
        ])
    elif is_energy:
        # Năng lượng: dữ liệu sản lượng/hợp đồng/phải thu và capex dự án chưa có.
        if growth_available:
            fundamental_score_available += 10
            if profit_growth_yoy >= 15: fund_score += 10
            elif profit_growth_yoy >= 5: fund_score += 6
            elif profit_growth_yoy >= 0: fund_score += 3
            else: fund_notes.append("Lợi nhuận năng lượng suy giảm")
        else:
            fundamental_missing_groups.append("Tăng trưởng theo chu kỳ/giá bán (10 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 7
            if roe >= 15: fund_score += 7
            elif roe >= 10: fund_score += 5
            elif roe > 0:
                fund_score += 2
        else:
            fundamental_missing_groups.append("ROE (7 điểm)")
        if nd.get("roa_available", False):
            fundamental_score_available += 3
            if roa >= 5: fund_score += 3
            elif roa >= 2.5: fund_score += 2
        else:
            fundamental_missing_groups.append("ROA (3 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 10
            if net_profit_ttm > 0 and cfo_latest >= net_profit_ttm: fund_score += 10
            elif net_profit_ttm > 0 and cfo_latest > 0: fund_score += 6
            elif cfo_latest > 0: fund_score += 3
        else:
            fundamental_missing_groups.append("Chuyển đổi tiền mặt (10 điểm)")
        fundamental_missing_groups.extend([
            "Phải thu và chất lượng hợp đồng (10 điểm)",
            "Capex, tiến độ và hiệu quả dự án (10 điểm)"
        ])
    elif is_utility:
        # Điện/nước/hạ tầng: cổ tức là tín hiệu phân phối tiền, không thay thế CFO.
        if growth_available:
            fundamental_score_available += 8
            if profit_growth_yoy >= 10: fund_score += 8
            elif profit_growth_yoy >= 0: fund_score += 5
            else: fund_notes.append("Lợi nhuận tiện ích suy giảm")
        else:
            fundamental_missing_groups.append("Tăng trưởng (8 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 10
            if roe >= 15: fund_score += 10
            elif roe >= 10: fund_score += 7
            elif roe > 0:
                fund_score += 3
        else:
            fundamental_missing_groups.append("Sinh lời ROE (10 điểm)")
        if nd["dividend_yield"] > 0:
            fundamental_score_available += 6
            if nd["dividend_yield"] >= 6: fund_score += 6
            elif nd["dividend_yield"] >= 4: fund_score += 4
            else: fund_score += 2
        else:
            fundamental_missing_groups.append("Lợi suất cổ tức (6 điểm)")
        fundamental_missing_groups.extend([
            "Nợ vay và nhu cầu capex (12 điểm)",
            "CFO, cổ tức tiền mặt và khả năng chi trả (6 điểm)",
            "Chất lượng tài sản và phải thu (8 điểm)"
        ])
    elif is_steel or is_fertilizer:
        # Tách thép và phân bón vì biên lợi nhuận, tồn kho và chu kỳ giá đầu vào khác nhau.
        if growth_available:
            fundamental_score_available += 8
            if profit_growth_yoy >= 15: fund_score += 8
            elif profit_growth_yoy >= 0: fund_score += 5
            else: fund_notes.append("Lợi nhuận giảm theo chu kỳ hàng hóa")
        else:
            fundamental_missing_groups.append("Tăng trưởng chu kỳ (8 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 8
            if roe >= (15 if is_steel else 18): fund_score += 8
            elif roe >= 10: fund_score += 5
            elif roe > 0:
                fund_score += 2
        else:
            fundamental_missing_groups.append("Hiệu quả vốn ROE (8 điểm)")
        if (
            nd.get("bctc_balance_sheet_valid", False)
            and nd.get("debt_to_equity_available", False) is True
            and is_valid_debt_to_equity(debt_to_equity)
        ):
            fundamental_score_available += 6
            if debt_to_equity <= 0.7: fund_score += 6
            elif debt_to_equity <= 1.2: fund_score += 4
            elif debt_to_equity <= 2.0: fund_score += 2
        else:
            fundamental_missing_groups.append("Đòn bẩy tài chính (6 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 6
            if net_profit_ttm > 0 and cfo_latest >= net_profit_ttm: fund_score += 6
            elif cfo_latest > 0: fund_score += 3
        else:
            fundamental_missing_groups.append("CFO và chất lượng lợi nhuận (6 điểm)")
        fundamental_missing_groups.extend([
            "Biên lợi nhuận chuẩn hóa giữa chu kỳ (12 điểm)",
            "Tồn kho, giá bán/đầu vào và vốn lưu động (10 điểm)"
        ])
    elif is_cyclical_material:
        # Vật liệu chu kỳ: không dùng P/E thấp như bằng chứng rẻ khi lợi nhuận ở đỉnh chu kỳ.
        if growth_available:
            fundamental_score_available += 8
            if profit_growth_yoy >= 15: fund_score += 8
            elif profit_growth_yoy >= 0: fund_score += 5
            else: fund_notes.append("Suy giảm lợi nhuận theo chu kỳ")
        else:
            fundamental_missing_groups.append("Tăng trưởng chu kỳ (8 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 8
            if roe >= 15: fund_score += 8
            elif roe >= 10: fund_score += 5
            elif roe > 0:
                fund_score += 2
        else:
            fundamental_missing_groups.append("Hiệu quả vốn ROE (8 điểm)")
        if (
            nd.get("bctc_balance_sheet_valid", False)
            and nd.get("debt_to_equity_available", False) is True
            and is_valid_debt_to_equity(debt_to_equity)
        ):
            fundamental_score_available += 6
            if debt_to_equity <= 0.7: fund_score += 6
            elif debt_to_equity <= 1.2: fund_score += 4
            elif debt_to_equity <= 2.0: fund_score += 2
        else:
            fundamental_missing_groups.append("Đòn bẩy tài chính (6 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 6
            if net_profit_ttm > 0 and cfo_latest >= net_profit_ttm: fund_score += 6
            elif cfo_latest > 0: fund_score += 3
        else:
            fundamental_missing_groups.append("CFO và chất lượng lợi nhuận (6 điểm)")
        fundamental_missing_groups.extend([
            "Biên lợi nhuận giữa chu kỳ (12 điểm)",
            "Tồn kho, giá nguyên liệu và vốn lưu động (10 điểm)"
        ])
    elif is_pharma or is_textile or is_rubber or is_plastics_chemicals or is_agri_processing:
        # Các nhóm mới tách khỏi rổ sản xuất/tiêu dùng chung. Chỉ chấm 40 điểm
        # có thể tính từ BCTC chuẩn; 10 điểm đặc thù ngành vẫn để thiếu.
        if growth_available:
            fundamental_score_available += 10
            growth_threshold = (
                20 if is_rubber else 15 if (is_pharma or is_plastics_chemicals or is_agri_processing)
                else 12
            )
            if profit_growth_yoy >= growth_threshold: fund_score += 10
            elif profit_growth_yoy >= 5: fund_score += 7
            elif profit_growth_yoy >= 0: fund_score += 3
            else: fund_notes.append("Lợi nhuận suy giảm")
        else:
            fundamental_missing_groups.append("Tăng trưởng lợi nhuận (10 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 10
            roe_threshold = 18 if is_pharma else 15
            if roe >= roe_threshold: fund_score += 10
            elif roe >= 10: fund_score += 7
            elif roe > 0:
                fund_score += 3
        else:
            fundamental_missing_groups.append("ROE (10 điểm)")
        if nd.get("roa_available", False):
            fundamental_score_available += 5
            if roa >= 5: fund_score += 5
            elif roa >= 2.5: fund_score += 3
            elif roa > 0: fund_score += 1
        else:
            fundamental_missing_groups.append("ROA (5 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 10
            if net_profit_ttm > 0 and cfo_latest >= net_profit_ttm: fund_score += 10
            elif cfo_latest > 0: fund_score += 6
            elif cfo_latest < 0: fund_notes.append("CFO âm")
        else:
            fundamental_missing_groups.append("CFO và chất lượng lợi nhuận (10 điểm)")
        if (
            nd.get("bctc_balance_sheet_valid", False)
            and nd.get("debt_to_equity_available", False) is True
            and is_valid_debt_to_equity(debt_to_equity)
        ):
            fundamental_score_available += 5
            if debt_to_equity <= 0.5: fund_score += 5
            elif debt_to_equity <= 1.0: fund_score += 4
            elif debt_to_equity <= 2.0: fund_score += 2
        else:
            fundamental_missing_groups.append("Đòn bẩy tài chính (5 điểm)")
        if is_pharma:
            gap = "Danh mục thuốc, sản phẩm đăng ký và rủi ro hết hạn (10 điểm)"
        elif is_textile:
            gap = "Đơn hàng xuất khẩu, công suất và năng suất lao động (10 điểm)"
        elif is_rubber:
            gap = "Cơ cấu lốp thay thế/xuất khẩu và biên giá cao su đầu vào (10 điểm)"
        elif is_plastics_chemicals:
            gap = "Biên chênh lệch hạt nhựa/hoá chất và cơ cấu sản phẩm (10 điểm)"
        else:
            gap = "Cơ cấu mảng nông nghiệp/chế biến và an toàn nguồn cung (10 điểm)"
        fundamental_missing_groups.append(gap)
    elif is_symbol_classified_flag:
        # Chỉ dùng thang điểm tổng quát cho mã đã được phân ngành rõ ràng.
        # Availability phải độc lập với chất lượng: ROE âm là dữ liệu thật nhưng không có điểm.
        if growth_available:
            if profit_growth_yoy >= 10: fund_score += 15
            elif profit_growth_yoy >= 0: fund_score += 8
            else: fund_score -= 10
        if nd.get("roe_available", False):
            if roe >= 14:
                fund_score += 20
                fund_notes.append("ROE cao")
            elif roe >= 10:
                fund_score += 10
            elif roe < 0:
                fund_notes.append("ROE âm")
        
    if uses_generic_fundamental_addons:
        if nd.get("roa_available", False):
            if roa >= 5.0: fund_score += 5
            elif roa >= 2.5: fund_score += 3

    # Đo độ bao phủ theo đúng các đầu vào hiện hữu; không tự coi thiếu dữ liệu là điểm.
    if uses_generic_fundamental_addons:
        if growth_available:
            fundamental_score_available += 15
        else:
            fundamental_missing_groups.append("Tăng trưởng lợi nhuận (15 điểm)")
        if nd.get("roe_available", False):
            fundamental_score_available += 20
        else:
            fundamental_missing_groups.append("Sinh lời ROE (20 điểm)")
        if nd.get("roa_available", False):
            fundamental_score_available += 5
        else:
            fundamental_missing_groups.append("Hiệu quả tài sản ROA (5 điểm)")
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            fundamental_score_available += 5
        else:
            fundamental_missing_groups.append("Chất lượng lợi nhuận/CFO TTM (5 điểm)")
        if (
            nd.get("bctc_balance_sheet_valid", False)
            and nd.get("debt_to_equity_available", False) is True
            and is_valid_debt_to_equity(debt_to_equity)
        ):
            fundamental_score_available += 5
        else:
            fundamental_missing_groups.append("Đòn bẩy và cấu trúc vốn (5 điểm)")

    if uses_generic_fundamental_addons:
        if nd.get("bctc_income_ttm_complete", False) and nd.get("bctc_cfo_ttm_complete", False):
            if net_profit_ttm < 0:
                fund_score -= 10
                fund_notes.append("LNST TTM âm")
            elif net_profit_ttm > 0 and cfo_latest > 0 and cfo_latest >= net_profit_ttm * 0.8:
                fund_score += 5
            elif cfo_latest < 0:
                fund_score -= 5
                fund_notes.append("CFO âm")
            elif net_profit_ttm == 0:
                fund_notes.append("Chưa đủ dữ liệu LNST TTM để đánh giá chất lượng lợi nhuận")
        if (
            nd.get("bctc_balance_sheet_valid", False)
            and nd.get("debt_to_equity_available", False) is True
            and is_valid_debt_to_equity(debt_to_equity)
        ):
            if 0 < debt_to_equity <= 1.0: fund_score += 5
            elif debt_to_equity > 2.5: fund_score -= 5; fund_notes.append("D/E cao")
    else:
        # Không cộng điểm mặc định cho nhóm tài chính khi thiếu chỉ tiêu chất lượng tài sản.
        if not nd.get("bctc_balance_sheet_valid", False):
            fund_notes.append("Thiếu dữ liệu bảng cân đối để đánh giá đòn bẩy")
    # Điểm không được vượt quá phần trọng số có dữ liệu xác minh.
    fundamental_score_available = max(0, min(50, int(fundamental_score_available)))
    fund_score = cap_fundamental_score(fund_score, fundamental_score_available)
    fundamental_score_coverage_pct = round(
        fundamental_score_available / 50 * 100, 1
    )
    coverage_note = (
        f"Bao phủ dữ liệu PTCB: {fundamental_score_available}/50 "
        f"({fundamental_score_coverage_pct}%)."
    )
    if fundamental_missing_groups:
        coverage_note += " Chưa chấm được: " + "; ".join(fundamental_missing_groups)

    # Không có điểm định giá mặc định: chỉ chấm khi có chỉ tiêu hợp lệ.
    # P/E chỉ hợp lệ khi LNST TTM dương và BCTC đủ 4 quý; P/B cần dữ liệu PB dương.
    val_score = 0
    valid_pe = bool(nd.get("bctc_income_ttm_complete", False) and net_profit_ttm > 0 and pe > 0)
    valid_pb = pb > 0
    valid_dividend = nd["dividend_yield"] > 0

    if sector == "BAT_DONG_SAN" and not is_holding:
        # RNAV chưa được tính do chưa có dữ liệu pháp lý/tiến độ từng dự án.
        # Chấm P/B thận trọng cùng ROE, không coi P/B thấp là đủ rẻ.
        if valid_pb and roe_is_verified and roe > 0:
            if pb <= 0.8 and roe >= 12: val_score = 30
            elif pb <= 1.0 and roe >= 10: val_score = 24
            elif pb <= 1.3 and roe >= 12: val_score = 18
            elif pb <= 1.8 and roe >= 10: val_score = 10
            elif pb <= 2.2: val_score = 5
    elif is_pharma:
        if valid_pe:
            if pe < 15: val_score = 30
            elif pe < 20: val_score = 22
            elif pe < 25: val_score = 12
    elif is_textile or is_rubber or is_plastics_chemicals or is_agri_processing:
        # Nhóm chu kỳ: không dùng P/E thấp đơn lẻ khi lợi nhuận biến động theo giá đầu vào.
        if valid_pb and roe_is_verified and roe > 0:
            if pb <= 0.9 and roe >= 12: val_score = 25
            elif pb <= 1.3 and roe >= 10: val_score = 18
            elif pb <= 1.7: val_score = 10
    elif is_petroleum:
        # Dầu khí: tránh P/E lợi nhuận giao ngay khi chưa có mid-cycle earnings.
        if valid_pb and roe_is_verified and roe > 0:
            if pb <= 0.8 and roe >= 12: val_score = 25
            elif pb <= 1.1 and roe >= 10: val_score = 20
            elif pb <= 1.5: val_score = 12
    elif is_steel or is_fertilizer:
        # P/E có thể thấp giả tạo ở đỉnh chu kỳ; chỉ cho điểm cao khi P/B cũng hợp lý.
        if valid_pb and roe_is_verified and roe > 0:
            if pb <= 0.8 and roe >= 12: val_score = 25
            elif pb <= 1.1 and roe >= 10: val_score = 20
            elif pb <= 1.5: val_score = 12
        if valid_pe and pe < (10 if is_steel else 12) and profit_growth_yoy <= 15:
            val_score = max(val_score, 15)
    elif is_holding:
        # NAV/holding discount chưa thể tính khi chưa có giá trị từng công ty con.
        if valid_pb and roe_is_verified and roe > 0:
            if pb <= 0.8 and roe >= 12: val_score = 25
            elif pb <= 1.1 and roe >= 10: val_score = 20
            elif pb <= 1.5: val_score = 12
    elif is_building_materials:
        if valid_pe:
            if pe < 12: val_score = 30
            elif pe < 16: val_score = 20
            elif pe < 22: val_score = 10
        elif valid_pb and roe_is_verified and roe > 0:
            if pb <= 1.0 and roe >= 12: val_score = 22
            elif pb <= 1.5 and roe >= 10: val_score = 15
            elif pb <= 2.0: val_score = 7
    elif sector == "NGUYEN_VAT_LIEU_CHU_KY":
        if valid_pe:
            if pe < 12: val_score = 30
            elif pe < 18: val_score = 18
            elif pe < 25: val_score = 8
        elif valid_pb and pb < 1.5:
            val_score = 12
    elif is_technology:
        if valid_pe:
            if nd.get("profit_growth_yoy_available") and profit_growth_yoy >= 20 and pe < 30: val_score = 30
            elif pe < 22: val_score = 24
            elif pe < 28: val_score = 15
            elif pe < 35: val_score = 7
    elif is_retail:
        if valid_pe:
            if pe < 16: val_score = 30
            elif pe < 21: val_score = 22
            elif pe < 27: val_score = 12
            elif pe < 35: val_score = 5
    elif sector == "NGAN_HANG" or is_insurance:
        # Ngân hàng và bảo hiểm: P/B phải xét cùng ROE; không dùng P/E đơn lẻ.
        if valid_pb and roe_is_verified and roe > 0:
            if pb <= 0.8 and roe >= 10: val_score = 30
            elif pb <= 1.0 and roe >= 12: val_score = 27
            elif pb <= 1.2 and roe >= 15: val_score = 25
            elif pb <= 1.5 and roe >= 18: val_score = 23
            elif pb <= 1.5 and roe >= 12: val_score = 18
            elif pb <= 2.0 and roe >= 15: val_score = 14
            elif pb <= 2.5 and roe >= 12: val_score = 8
    elif sector == "CHUNG_KHOAN":
        if valid_pb:
            if pb < 1.3 or (roe_is_verified and roe >= 16 and pb < 1.8): val_score = 30
            elif pb < 2.0: val_score = 18
            elif pb < 2.5: val_score = 8
    else:
        if valid_pe and pe < 15: val_score = 30
        elif (valid_pe and pe < 20) or (valid_dividend and nd["dividend_yield"] >= 5.0): val_score = 18
        elif (valid_pe and pe < 25) or (valid_dividend and nd["dividend_yield"] >= 3.0): val_score = 8
    val_score = int(np.clip(val_score, 0, 30))

    tech_score = int(np.clip(int(round((tech_score_raw / 100.0) * 20)), 0, 20))
    total_score = fund_score + val_score + tech_score
    potential_zone = "VÙNG TÍCH SẢN TỐI ƯU (A+)" if total_score >= 75 else ("VÙNG MUA AN TOÀN (B)" if total_score >= 60 else "THEO DÕI THÊM")
    
    fv = compute_fair_value_zone(symbol, nd, sector_median, config, group_label)
    decision, color, reason = compute_final_decision(fund_score, tech_score, val_score, total_score, fv["verdict"], cfo_latest, net_profit_ttm, roe, debt_to_equity, fv["discount_to_fair_pct"], close_price, fv["fair_high"], fv["fair_low"], sector=("INSURANCE" if is_insurance else sector), fundamental_coverage_pct=fundamental_score_coverage_pct, bctc_period_is_current=nd.get("bctc_period_is_current", False))

    sim_data = get_historical_return_rate_with_details(symbol, history_bars)
    meta = VN100_METADATA.get(symbol, {})

    ai_data = existing_ai_data or {
        "bctc": "", "catalyst": "", "risks": "", "outlook": "", "recommendation": "", 
        "price_zone": "", "macro_view": "", "ai_updated_at": "Chưa phân tích", "ai_source": ""
    }
    
    has_real_ai = bool(ai_data.get("bctc") and len(ai_data.get("bctc", "")) > 100)
    
    if not has_real_ai:
        metrics_for_template = {
            **nd, "fair_value_low": fv["fair_low"], "fair_value_high": fv["fair_high"],
            "valuation_verdict": fv["verdict"],
            "discount_to_fair_pct": fv["discount_to_fair_pct"],
            "margin_of_safety_pct": fv["margin_of_safety_pct"],
            "mos_target_pct": fv["mos_target_pct"],
            "upside_to_fair_low_pct": fv["upside_to_fair_low_pct"]
        }
        temp_template = build_fallback_narrative(symbol, metrics_for_template, {
            "fundamental_score": fund_score, "valuation_score": val_score, "total_score": total_score,
            "profit_growth_yoy": profit_growth_yoy, "final_decision": decision
        }, sector)
        
        ai_data.update({
            "bctc": temp_template.get("bctc", ""), "catalyst": temp_template.get("catalyst", ""),
            "risks": temp_template.get("risks", ""), "outlook": temp_template.get("outlook", ""),
            "recommendation": temp_template.get("recommendation", ""), "price_zone": temp_template.get("price_zone", ""),
            "macro_view": temp_template.get("macro_view", ""), "ai_source": "TEMPLATE"
        })

    return {
        "symbol": symbol, "sector": sector, "sub_sector": group_label, "close_price": close_price, "pe": pe, "pb": pb,
        "roe": roe, "roa": roa, "eps": round(nd["eps"], 0), "bvps": round(nd["bvps"], 0), "dividend_yield": nd["dividend_yield"], "profit_growth_yoy": profit_growth_yoy,
        "cfo_latest": cfo_latest, "net_profit_ttm": net_profit_ttm, "debt_to_equity": debt_to_equity, "gross_margin": nd["gross_margin"],
        "bank_cir": nd.get("bank_cir", 0),
        "bank_cir_available": bool(nd.get("bank_cir_available", False)),
        "fundamental_score": fund_score, "valuation_score": val_score, "technical_score": tech_score, "total_score": total_score,
        "fundamental_score_available": fundamental_score_available,
        "fundamental_score_coverage_pct": fundamental_score_coverage_pct,
        "fundamental_missing_groups": fundamental_missing_groups,
        "fundamental_scorecard": {
            "version": "2.0.0",
            "profile_id": profile_id,
            "profile_status": (
                sector_profile.get("status") if sector_profile else "not_configured"
            ),
            "profile_version": (
                sector_profile.get("profile_version") if sector_profile else None
            ),
            "profile_weights": (
                dict(sector_profile.get("weights") or {}) if sector_profile else {}
            ),
            "required_metric_groups": (
                {
                    key: list(value)
                    for key, value in (sector_profile.get("metric_groups") or {}).items()
                }
                if sector_profile else {}
            ),
            "profile_data_gaps": (
                list(sector_profile.get("data_gaps") or []) if sector_profile else []
            ),
            "status": (
                "insufficient_data"
                if fundamental_score_coverage_pct < 65
                else "partial"
                if fundamental_score_available < 50 or fundamental_missing_groups
                else "complete"
            ),
            "score": int(fund_score),
            "max_score": 50,
            "available_weight": int(fundamental_score_available),
            "missing_weight": max(0, 50 - int(fundamental_score_available)),
            "coverage_pct": round(float(fundamental_score_coverage_pct), 1),
            "missing_groups": list(fundamental_missing_groups),
            "notes": list(fund_notes),
            "production_profile_approved": False,
            "note": "Điểm chỉ tính trên chỉ tiêu BCTC đã xác minh; chỉ tiêu ngành chưa có dữ liệu được giữ ở trạng thái thiếu, không quy đổi thành 0.",
        },
        "potential_zone": potential_zone, "pattern": pattern, "technical_status": tech_status,
        "technical_score_breakdown": technical_details or {},
        "fundamental_notes": " | ".join(fund_notes + [coverage_note]), "fair_value_high": fv["fair_high"], "fair_value_low": fv["fair_low"], "graham_number": fv["graham"],
        "margin_of_safety_pct": fv["margin_of_safety_pct"],
        "mos_target_pct": fv["mos_target_pct"],
        "fair_value_methods": fv["methods"], "fair_value_rationale": fv["rationale"],
        "upside_to_fair_low_pct": fv["upside_to_fair_low_pct"],
        "discount_to_fair_pct": fv["discount_to_fair_pct"],
        "valuation_verdict": fv["verdict"], "final_decision": decision,
        "final_decision_color": color, "final_decision_reason": reason,
        
        "sim_annual_rate": sim_data["annual_rate_percent"] if sim_data else 15.0,
        "sim_start_quarter": sim_data["start_quarter"] if sim_data else "",
        "sim_start_price": sim_data["start_price_mean"] if sim_data else 0,
        "sim_end_quarter": sim_data["end_quarter"] if sim_data else "",
        "sim_end_price": sim_data["end_price_mean"] if sim_data else 0,
        
        "company_name": meta.get("company_name", ""),
        "market_cap": meta.get("market_cap", 0),
        "total_shares": meta.get("total_shares", 0),
        
        "data_warnings": data_warnings,
        "is_data_incomplete": is_data_incomplete,
        "bctc_latest_income_period": nd.get("bctc_latest_income_period", ""),
        "bctc_expected_period": nd.get("bctc_expected_period", ""),
        "bctc_period_is_current": bool(nd.get("bctc_period_is_current", False)),
        
        "bctc_evaluation": ai_data["bctc"],
        "outlook": ai_data["outlook"],
        "ai_recommendation": ai_data["recommendation"],
        "ai_price_zone": ai_data["price_zone"],
        "macro_view": ai_data["macro_view"],
        "ai_updated_at": ai_data["ai_updated_at"],
        "ai_source": ai_data["ai_source"]
    }

def aggregate_candles(data, interval):
    if not data or interval == 'D1': return data
    groups = {}
    for bar in data:
        date = datetime.strptime(bar['time'], '%Y-%m-%d')
        key = (date - timedelta(days=date.weekday())).strftime('%Y-%m-%d') if interval == 'W1' else date.strftime('%Y-%m-01')
        if key not in groups: groups[key] = {"time": key, "open": bar['open'], "high": bar['high'], "low": bar['low'], "close": bar['close'], "volume": bar['volume']}
        else:
            groups[key]['high'] = max(groups[key]['high'], bar['high']); groups[key]['low'] = min(groups[key]['low'], bar['low']); groups[key]['close'] = bar['close']; groups[key]['volume'] += bar['volume']
    return sorted(list(groups.values()), key=lambda x: x['time'])

# ===================== AI HELPERS =====================
SECTOR_NAMES = {"NGAN_HANG": "Ngân hàng", "CHUNG_KHOAN": "Chứng khoán", "BAT_DONG_SAN": "Bất động sản", "CONG_NGHE_BAN_LE": "Công nghệ & Bán lẻ", "DIEN_NUOC_HA_TANG": "Điện, Nước & Hạ tầng", "NGUYEN_VAT_LIEU_CHU_KY": "Vật liệu & Chu kỳ", "SAN_XUAT_TIEU_DUNG_PHONG_THU": "Sản xuất & Tiêu dùng"}

DECISION_TO_RECOMMENDATION = {
    "🎯 MUA MẠNH": ("MUA MẠNH", "Tỷ trọng 20-30% — Giải ngân toàn bộ hoặc theo 2-3 đợt"),
    "✅ MUA TÍCH SẢN": ("MUA TÍCH SẢN", "Tỷ trọng 10-15% — Giải ngân từng phần (DCA)"),
    "⚠️ CHỜ VỀ VÙNG MUA": ("CHỜ VỀ VÙNG MUA", "Tỷ trọng 0% — Chờ giá về fair_low mới mua"),
    "⏳ CHỜ XÁC NHẬN KỸ THUẬT": ("CHỜ XÁC NHẬN KỸ THUẬT", "Tỷ trọng 0% — Chờ breakout có volume xác nhận"),
    "⏳ CHỜ ĐỊNH GIÁ TỐT HƠN": ("CHỜ ĐỊNH GIÁ TỐT HƠN", "Tỷ trọng 0% — Định giá chưa hấp dẫn"),
    "❌ TRÁNH": ("TRÁNH", "Tỷ trọng 0% — Không mua"),
    "⚠️ CẢNH BÁO LỢI NHUẬN ẢO": ("CẢNH BÁO", "Tỷ trọng 0% — Lợi nhuận không có tiền mặt thật"),
    "👀 THEO DÕI": ("NẮM GIỮ / THEO DÕI", "Tỷ trọng 0% — Chưa đủ điều kiện mua"),
}

def get_sector_guidance(sector):
    guidance = {"NGAN_HANG": "D/E cao là bản chất. Trọng tâm: ROE, ROA, tín dụng.", "CHUNG_KHOAN": "Phụ thuộc thanh khoản. Trọng tâm: ROE, LNST YoY.", "BAT_DONG_SAN": "Ghi nhận theo dự án. Trọng tâm: P/B, ROE, dòng tiền trả trước.", "CONG_NGHE_BAN_LE": "Trọng tâm: ROE, biên gộp, LNST.", "DIEN_NUOC_HA_TANG": "CFO ổn định. Trọng tâm: ROE, cổ tức, CFO.", "NGUYEN_VAT_LIEU_CHU_KY": "Bẫy P/E thấp. Trọng tâm: P/B, ROE, biên gộp.", "SAN_XUAT_TIEU_DUNG_PHONG_THU": "Trọng tâm: ROE, biên gộp, CFO/LNST."}
    return guidance.get(sector, guidance["SAN_XUAT_TIEU_DUNG_PHONG_THU"])

def build_bctc_quarterly_trend(bctc_doc, max_periods=8):
    """Tổng hợp xu hướng BCTC tối đa 8 quý; không tự suy diễn số liệu thiếu."""
    if not isinstance(bctc_doc, dict):
        return "Không có dữ liệu BCTC theo quý."

    income = bctc_doc.get("income_statement", []) or []
    cashflow = bctc_doc.get("cash_flow", []) or []
    periods = _valid_income_statement_periods(income)[:max_periods]
    if not periods:
        return "Không có dữ liệu doanh thu/lợi nhuận theo quý."

    rows = {}
    for period in periods:
        revenue = _get_bctc_optional_value(income, "IS_NET_REVENUE", period)
        cogs = _get_bctc_optional_value(income, "IS_COST_OF_GOODS_SOLD", period)
        npat = _get_bctc_optional_value(
            income,
            ["IS_NET_PROFIT_AFTER_TAX", "IS_PROFIT_AFTER_TAX", "IS_NET_PROFIT"],
            period
        )
        cfo = _get_bctc_optional_value(
            cashflow, "CF_NET_CASH_FLOWS_FROM_OPERATING_ACTIVITIES", period
        )
        rows[period] = {
            "revenue": revenue, "npat": npat, "cfo": cfo,
            "gross_margin": (
                (revenue + cogs) / revenue * 100
                if (
                    revenue is not None and cogs is not None
                    and revenue > 0 and cogs <= 0
                )
                else None
            )
        }

    def growth(current, previous):
        if current is None or previous is None or previous == 0:
            return None
        return (current / previous - 1) * 100

    def fmt(value):
        return "không đủ dữ liệu" if value is None else f"{value:+.1f}%"

    def fmt_money(value):
        return "không đủ dữ liệu" if value is None else f"{value / 1e9:,.2f}"

    latest_period = periods[0]
    expected_period = _expected_latest_bctc_period()
    lines = [
        f"XU HƯỚNG BCTC THEO QUÝ (tối đa {len(periods)} quý; đơn vị tỷ VNĐ):",
        f"Kỳ dữ liệu mới nhất: {latest_period}; kỳ công bố dự kiến hiện tại: {expected_period}."
    ]
    if not _bctc_period_is_current(latest_period, expected_period):
        lines.append(
            f"CẢNH BÁO: Kỳ BCTC ({latest_period}) chưa khớp kỳ dự kiến "
            f"{expected_period}. Không được trình bày các số liệu này như "
            "tình hình tài chính mới nhất tại thời điểm hiện tại."
        )
    for period in reversed(periods):
        item = rows[period]
        try:
            year, quarter = period.split("-Q")
            prior_yoy = f"{int(year)-1}-Q{quarter}"
            prior_q = f"{int(year)-1}-Q4" if quarter == "1" else f"{year}-Q{int(quarter)-1}"
        except Exception:
            prior_yoy, prior_q = None, None

        yoy = rows.get(prior_yoy, {})
        prev = rows.get(prior_q, {})
        gm = "N/A" if item["gross_margin"] is None else f"{item['gross_margin']:.1f}%"
        lines.append(
            f"- {period}: Doanh thu {fmt_money(item['revenue'])}; "
            f"LNST {fmt_money(item['npat'])}; CFO {fmt_money(item['cfo'])}; "
            f"biên gộp {gm}; doanh thu YoY {fmt(growth(item['revenue'], yoy.get('revenue')) if yoy else None)}; "
            f"LNST YoY {fmt(growth(item['npat'], yoy.get('npat')) if yoy else None)}; "
            f"doanh thu QoQ {fmt(growth(item['revenue'], prev.get('revenue')) if prev else None)}; "
            f"LNST QoQ {fmt(growth(item['npat'], prev.get('npat')) if prev else None)}."
        )

    lines.append(
        "LƯU Ý: So sánh chỉ khi có đủ kỳ tương ứng; không suy diễn dữ liệu thiếu "
        "hoặc kết luận chắc chắn khi các kỳ báo cáo không đồng nhất."
    )
    return "\n".join(lines)


def build_verified_facts(symbol, sm, si, sector, bctc_doc=None):
    roe_text = (
        f"{_finite_float(sm.get('roe')):.1f}%"
        if _has_reported_ratio(sm, "roe") else "chưa xác minh"
    )
    roa_text = (
        f"{_finite_float(sm.get('roa')):.1f}%"
        if _has_reported_ratio(sm, "roa") else "chưa xác minh"
    )
    cfo_text = (
        f"{_finite_float(sm.get('cfo_ttm', sm.get('cfo_latest'))):,.0f} tỷ"
        if sm.get("bctc_cfo_ttm_complete") is True else "chưa đủ dữ liệu TTM"
    )
    npat_text = (
        f"{_finite_float(sm.get('net_profit_ttm')):,.0f} tỷ"
        if sm.get("bctc_income_ttm_complete") is True else "chưa đủ dữ liệu TTM"
    )
    lines = [
        f"MÃ {symbol} — NGÀNH {sector}",
        f"QUYẾT ĐỊNH: {si.get('final_decision')}",
        f"GIÁ: {sm.get('close',0):,.0f} | P/E {sm.get('pe',0):.2f} | P/B {sm.get('pb',0):.2f}",
        f"VÙNG ĐỊNH GIÁ: Fair Low {sm.get('fair_value_low',0):,.0f} | Fair High {sm.get('fair_value_high',0):,.0f} | Verdict: {sm.get('valuation_verdict')}",
        f"MOS thực tế: {sm.get('margin_of_safety_pct',0):.1f}% | MOS mục tiêu ngành: {sm.get('mos_target_pct',0):.1f}% | Upside đến Fair Low: {sm.get('upside_to_fair_low_pct',sm.get('discount_to_fair_pct',0)):.1f}%",
        f"SINH LỜI TTM: ROE {roe_text} | ROA {roa_text} | CFO {cfo_text} | LNST {npat_text}",
        f"ĐIỂM: Tổng {si.get('total_score')}/100",
        build_bctc_quarterly_trend(bctc_doc)
    ]
    return "\n".join(lines)

def build_fallback_narrative(symbol, stock_metrics, screener_item, sector):
    close = _finite_float(stock_metrics.get("close", stock_metrics.get("close_price", 0)))
    pe = _finite_float(stock_metrics.get("pe"))
    pb = _finite_float(stock_metrics.get("pb"))
    roe = _finite_float(stock_metrics.get("roe"))
    roa = _finite_float(stock_metrics.get("roa"))
    eps = _finite_float(stock_metrics.get("eps"))
    cfo = _finite_float(stock_metrics.get("cfo_ttm", stock_metrics.get("cfo_latest", 0)))
    npat = _finite_float(stock_metrics.get("net_profit_ttm"))
    de = _finite_float(stock_metrics.get("debt_to_equity"))
    gm = _finite_float(stock_metrics.get("gross_margin"))
    gm_available = (
        stock_metrics.get("gross_margin_available") is True
        and _is_finite_number(stock_metrics.get("gross_margin"))
    )
    div_yield = _finite_float(stock_metrics.get("dividend_yield"))
    roe_available = _has_reported_ratio(stock_metrics, "roe")
    roa_available = _has_reported_ratio(stock_metrics, "roa")
    cfo_available = stock_metrics.get("bctc_cfo_ttm_complete") is True
    npat_available = stock_metrics.get("bctc_income_ttm_complete") is True
    de_available = (
        stock_metrics.get("debt_to_equity_available") is True
        and is_valid_debt_to_equity(de)
    )

    fair_low = float(stock_metrics.get("fair_value_low", 0) or 0)
    fair_high = float(stock_metrics.get("fair_value_high", 0) or 0)
    upside_pct = float(stock_metrics.get("upside_to_fair_low_pct", stock_metrics.get("discount_to_fair_pct", 0)) or 0)
    verdict = stock_metrics.get("valuation_verdict", "CHƯA TÍNH")
    mos_pct = float(stock_metrics.get("margin_of_safety_pct", 0) or 0)
    mos_target_pct = float(stock_metrics.get("mos_target_pct", stock_metrics.get("mos_pct", 0)) or 0)
    
    fund_score = screener_item.get("fundamental_score", 0)
    val_score = screener_item.get("valuation_score", 0)
    tech_score = screener_item.get("technical_score", 0)
    total_score = screener_item.get("total_score", 0)
    growth_yoy = _finite_float(screener_item.get("profit_growth_yoy"))
    growth_available = (screener_item.get("profit_growth_yoy_available") is True and _is_finite_number(screener_item.get("profit_growth_yoy")) )
    final_decision = screener_item.get("final_decision", "CHƯA XÁC ĐỊNH")

    sector_name = SECTOR_NAMES.get(sector, sector)
    is_bank_or_sec = sector in ("NGAN_HANG", "CHUNG_KHOAN")

    roe_text = f"ROE {roe:.1f}%" if roe_available else "ROE chưa xác minh"
    roa_text = f"ROA {roa:.1f}%" if roa_available else "ROA chưa xác minh"
    pe_text = f"P/E {pe:.2f}x" if pe > 0 else "P/E chưa xác minh"
    pb_text = f"P/B {pb:.2f}x" if pb > 0 else "P/B chưa xác minh"
    bctc_parts = [f"Chỉ số sinh lời: {roe_text}, {roa_text}."]
    if is_bank_or_sec:
        de_text = f"D/E {de:.2f}" if de_available else "D/E chưa xác minh"
        bctc_parts.append(f"{pe_text} và {pb_text} phản ánh mức định giá của định chế tài chính. {de_text} cần được đọc theo đặc thù ngành.")
    else:
        bctc_parts.append(f"Định giá {pe_text} và {pb_text}.")
        if gm_available:
            bctc_parts.append(f"Biên lợi nhuận gộp {gm:.1f}%.")
        else:
            bctc_parts.append("Biên lợi nhuận gộp chưa xác minh.")

    if cfo_available and npat_available:
        if npat > 0 and cfo != 0:
            cfo_ratio = cfo / npat
            if cfo_ratio >= 1.0:
                bctc_parts.append(f"Chất lượng lợi nhuận tốt với CFO {cfo:,.0f} tỷ ≥ LNST {npat:,.0f} tỷ.")
            elif cfo_ratio >= 0.5:
                bctc_parts.append(f"CFO {cfo:,.0f} tỷ so với LNST {npat:,.0f} tỷ — chấp nhận được.")
            else:
                bctc_parts.append(f"⚠️ Cảnh báo: CFO/LNST chỉ {cfo_ratio:.2f}x.")
        else:
            bctc_parts.append(f"CFO TTM {cfo:,.0f} tỷ; LNST TTM {npat:,.0f} tỷ.")
    else:
        bctc_parts.append("Chất lượng lợi nhuận TTM: chưa đủ dữ liệu CFO/LNST.")

    bctc_parts.append(f"EPS đạt {eps:,.0f} VNĐ." if eps > 0 else "EPS chưa xác minh.")
    if div_yield > 0: bctc_parts.append(f"Cổ tức {div_yield}%/năm.")
    bctc = " ".join(bctc_parts)

    moat_map = {
        "NGAN_HANG": "tệp khách hàng bán lẻ trung thành và chi phí vốn thấp",
        "CHUNG_KHOAN": "thị phần môi giới ổn định và quan hệ với nhà đầu tư tổ chức",
        "BAT_DONG_SAN": "quỹ đất sạch tại vị trí đắc địa và thương hiệu uy tín",
        "CONG_NGHE_BAN_LE": "thương hiệu mạnh và chuỗi cung ứng tối ưu",
        "DIEN_NUOC_HA_TANG": "vị thế độc quyền tự nhiên và dòng tiền ổn định",
        "NGUYEN_VAT_LIEU_CHU_KY": "quy mô sản xuất lớn và chi phí biên thấp",
        "SAN_XUAT_TIEU_DUNG_PHONG_THU": "thương hiệu quốc dân và mạng lưới phân phối rộng",
    }
    moat = moat_map.get(sector, "lợi thế cạnh tranh dài hạn")
    growth_text = f"LNST tăng {growth_yoy:.1f}% YoY." if growth_available else "Chưa đủ dữ liệu xác minh tăng trưởng LNST YoY."
    catalyst = f"Lợi thế cạnh tranh cốt lõi nằm ở {moat}. {growth_text}"

    risk_map = {
        "NGAN_HANG": f"Rủi ro nợ xấu và áp lực thu hẹp NIM. D/E hiện ở mức {de:.2f}.",
        "CHUNG_KHOAN": f"Rủi ro thanh khoản thị trường và danh mục tự doanh.",
        "BAT_DONG_SAN": "Rủi ro pháp lý dự án và đòn bẩy nợ cao.",
        "CONG_NGHE_BAN_LE": "Rủi ro cạnh tranh và biên lợi nhuận bị thu hẹp.",
        "DIEN_NUOC_HA_TANG": "Rủi ro chính sách giá và chi phí vốn tăng.",
        "NGUYEN_VAT_LIEU_CHU_KY": (f"Rủi ro giá hàng hóa đảo chiều làm biên gộp {gm:.1f}% co lại." if gm > 0 else "Rủi ro chu kỳ giá hàng hóa; biên gộp chưa xác minh."),
        "SAN_XUAT_TIEU_DUNG_PHONG_THU": "Rủi ro cạnh tranh từ hàng nhập khẩu và sức mua yếu.",
    }
    risks = risk_map.get(sector, f"Rủi ro chung của ngành {sector_name}.")

    if verdict == "RẺ - TRONG VÙNG MUA":
        outlook = f"Giá {close:,.0f} VNĐ thấp hơn Fair Low {fair_low:,.0f} VNĐ. MOS thực tế {mos_pct:.1f}% (MOS mục tiêu ngành {mos_target_pct:.1f}%). Upside đến Fair Low so với giá hiện tại {upside_pct:.1f}%. Verdict: {verdict}. Quyết định hệ thống: {final_decision}."
    elif verdict == "HỢP LÝ - CHỜ ĐIỀU CHỈNH":
        outlook = f"Giá {close:,.0f} VNĐ giữa vùng mua {fair_low:,.0f} và fair value {fair_high:,.0f}. Chờ điều chỉnh về vùng mua. Quyết định: {final_decision}."
    elif verdict in ("HƠI ĐẮT - KHÔNG MUA", "ĐẮT - TRÁNH"):
        outlook = f"Giá {close:,.0f} VNĐ đã vượt fair value {fair_high:,.0f}. Định giá chưa hấp dẫn. Quyết định: {final_decision}."
    else:
        outlook = f"Chưa đủ dữ liệu định giá. Verdict: {verdict}."

    rec_text, rec_weight = DECISION_TO_RECOMMENDATION.get(final_decision, ("NẮM GIỮ / THEO DÕI", "Tỷ trọng 0%"))
    recommendation = f"{rec_text} — {rec_weight}"

    price_zone = (f"Vùng mua: {fair_low:,.0f} — {fair_high:,.0f} | Giá: {close:,.0f} | Verdict: {verdict}" if fair_low > 0 and fair_high > 0 else f"Chưa có vùng giá được xác minh | Giá: {close:,.0f} | Verdict: {verdict}")

    macro_map = {
        "NGAN_HANG": "Chu kỳ tín dụng phục hồi khi lãi suất thấp. Rủi ro nợ xấu BĐS còn hiện hữu.",
        "CHUNG_KHOAN": "Thanh khoản thị trường quyết định doanh thu ngành. Chính sách nâng hạng tạo catalyst.",
        "BAT_DONG_SAN": "Pháp lý BĐS đang dần tháo gỡ. Chu kỳ phục hồi phụ thuộc niềm tin người mua.",
        "CONG_NGHE_BAN_LE": "Sức mua tiêu dùng phục hồi. Xu hướng chuyển đổi số tạo dư địa tăng trưởng.",
        "DIEN_NUOC_HA_TANG": "Nhu cầu điện tăng theo GDP. Chính sách năng lượng tái tạo tạo cơ hội.",
        "NGUYEN_VAT_LIEU_CHU_KY": "Chu kỳ giá hàng hóa phụ thuộc kinh tế Trung Quốc và chính sách bảo hộ.",
        "SAN_XUAT_TIEU_DUNG_PHONG_THU": "Tiêu dùng nội địa phục hồi chậm. Cơ hội từ xuất khẩu nếu tỷ giá thuận lợi.",
    }
    macro_view = macro_map.get(sector, f"Ngành {sector_name} chịu ảnh hưởng chung từ vĩ mô.")

    return {
        "bctc": bctc,
        "catalyst": catalyst,
        "risks": risks,
        "valuation_verdict": verdict,
        "outlook": outlook,
        "recommendation": recommendation,
        "price_zone": price_zone,
        "macro_view": macro_view,
        "_source": "TEMPLATE",
    }

def _call_ai_for_symbol(symbol, sm, si, sector):
    try:
        # =======================================================
        # BƯỚC 1: KIỂM TRA DỮ LIỆU ĐẦU VÀO (CHẶN GỌI AI VÔ ÍCH)
        # =======================================================
        fair_low = float(sm.get("fair_value_low", 0) or 0)
        fair_high = float(sm.get("fair_value_high", 0) or 0)
        
        # Nếu chưa có vùng định giá, không gọi AI (tránh AI viết rác)
        if fair_low <= 0 or fair_high <= 0:
            print(f"  ⚠️ [{symbol}] Bỏ qua gọi AI: Chưa có dữ liệu Fair Value (cần chạy Screener trước).", flush=True)
            return None

        bctc_doc = bctc_col.find_one({"symbol": symbol}, {"_id": 0, "income_statement": 1, "cash_flow": 1})
        verified_facts = build_verified_facts(symbol, sm, si, sector, bctc_doc)
        sector_guidance = get_sector_guidance(sector)
        
        final_decision = si.get("final_decision", "CHƯA XÁC ĐỊNH")
        rec_text, rec_weight = DECISION_TO_RECOMMENDATION.get(final_decision, ("NẮM GIỮ / THEO DÕI", "Tỷ trọng 0%"))
        
        verdict = sm.get("valuation_verdict", "CHƯA TÍNH")
        upside_pct = float(sm.get("upside_to_fair_low_pct", sm.get("discount_to_fair_pct", 0)) or 0)
        mos_actual_pct = float(sm.get("margin_of_safety_pct", 0) or 0)
        mos_target_pct = float(sm.get("mos_target_pct", 0) or 0)
        close_price = float(sm.get("close", sm.get("close_price", 0)) or 0)
        
        # Chuẩn hóa đơn vị
        if close_price > 0 and close_price < 1000: close_price *= 1000
        if fair_low > 0 and fair_low < 1000: fair_low *= 1000
        if fair_high > 0 and fair_high < 1000: fair_high *= 1000

        prompt = f"""VAI TRÒ
Bạn là chuyên gia phân tích doanh nghiệp niêm yết tại Việt Nam, tư duy độc lập, thận trọng và dựa trên bằng chứng. Nhiệm vụ là diễn giải dữ liệu đã cung cấp, không phải quảng bá cổ phiếu hay cố tạo ra một khuyến nghị hấp dẫn.

MÃ CỔ PHIẾU: {symbol}

NGUYÊN TẮC ƯU TIÊN
- Khối DỮ KIỆN ĐÃ KIỂM CHỨNG là nguồn số liệu duy nhất. Không tự thêm số, tỷ lệ, mốc thời gian, sự kiện, giá hàng hóa, chính sách mới hoặc thông tin doanh nghiệp không có trong dữ kiện.
- Không dùng kiến thức nhớ được về doanh nghiệp để khẳng định tình hình hiện tại. Nếu dữ kiện không đủ, nói rõ "chưa có dữ liệu để kết luận".
- Phân biệt rõ: số liệu TTM (12 tháng gần nhất), số liệu của một quý, tăng trưởng YoY và tăng trưởng QoQ. Không so sánh các đại lượng khác kỳ hoặc khác cơ sở.
- Không tự tính lại ROE, ROA, CFO, LNST, biên gộp, giá trị hợp lý hay biên an toàn. Chỉ dùng phép tính đơn giản khi đủ đầu vào, nêu rõ cách tính và đơn vị.
- Không biến tương quan thành nguyên nhân. Không giải thích CFO tăng/giảm do vốn lưu động, mùa vụ, tồn kho, công nợ... nếu dữ kiện không chứng minh.
- Số 0, giá trị rỗng hoặc "không đủ dữ liệu" không được diễn giải thành kết quả kinh doanh bằng 0. Không coi dữ liệu thiếu là tín hiệu tích cực hoặc tiêu cực.
- Không dùng ngôn ngữ chắc chắn như "bền vững", "luôn phục hồi", "đầu ngành", "lợi thế độc quyền" nếu dữ kiện chưa chứng minh.
- Mọi giá cổ phiếu phải ghi đơn vị VNĐ và dùng đúng mức giá trong dữ kiện. Các số liệu BCTC ghi tỷ VNĐ nếu khối dữ kiện ghi đơn vị tỷ VNĐ.

--- DỮ KIỆN ĐÃ KIỂM CHỨNG ---
{verified_facts}

--- HƯỚNG DẪN THEO NGÀNH ---
{sector_guidance}

--- QUYẾT ĐỊNH VÀ ĐỊNH GIÁ DO HỆ THỐNG TÍNH ---
Quyết định: {final_decision}
Verdict: {verdict}
Giá hiện tại: {close_price:,.0f} VNĐ
Fair Low: {fair_low:,.0f} VNĐ
Fair High: {fair_high:,.0f} VNĐ
MOS thực tế: {mos_actual_pct:.1f}%
        MOS mục tiêu ngành: {mos_target_pct:.1f}%
        Upside đến Fair Low: {upside_pct:.1f}%

QUY TẮC KHÔNG ĐƯỢC VI PHẠM
1. KHÔNG được thay đổi quyết định của hệ thống. Trường recommendation bắt buộc là "{rec_text} — {rec_weight}". Không tự đề xuất tỷ trọng khác.
2. valuation_verdict phải sao chép nguyên văn "{verdict}".
3. Không tự suy ra giá mục tiêu mới, Fair Value mới, xác suất tăng giá hoặc mức lỗ tiềm năng.
4. Không gọi giá hiện tại là "rẻ" chỉ vì thấp hơn Fair High. Phải đối chiếu đồng thời với Fair Low, Fair High và Verdict.
5. Phân biệt MOS thực tế ({mos_actual_pct:.1f}%), MOS mục tiêu ngành ({mos_target_pct:.1f}%) và upside đến Fair Low ({upside_pct:.1f}%); không gọi upside là MOS.
6. Nếu dữ liệu BCTC mới nhất cũ hơn kỳ dự kiến hoặc có cảnh báo trong khối dữ kiện, phải nêu rõ hạn chế này và không mô tả dữ liệu cũ là hiện tại.
7. Phần vĩ mô chỉ được nêu yếu tố có căn cứ trong dữ kiện. Nếu không có dữ liệu vĩ mô cập nhật, phải ghi rõ chưa có dữ liệu xác minh; có thể nêu các biến số cần theo dõi dưới dạng rủi ro/giả thuyết, không được khẳng định chúng đang tăng, giảm hay đã xảy ra.
8. Phân biệt nhận định cấp ngành với lợi thế riêng của doanh nghiệp. Không tự gán thị phần, thương hiệu mạnh, độc quyền, năng lực cạnh tranh hoặc catalyst cụ thể khi chưa có dữ kiện.
9. Tránh lặp lại cùng một con số và cùng một kết luận ở nhiều mục. Mỗi mục phải có nhiệm vụ riêng.
10. Trả về JSON hợp lệ, không Markdown, không code fence, không lời dẫn. Giá trị của mọi trường là chuỗi tiếng Việt; escape dấu ngoặc kép nếu cần.

--- YÊU CẦU NỘI DUNG TỪNG TRƯỜNG ---
{{
  "bctc": "Khoảng 8-12 câu. Mở đầu bằng kỳ BCTC mới nhất và mức độ đầy đủ của dữ liệu. Đánh giá riêng chất lượng lợi nhuận TTM (ROE, ROA, LNST, CFO, CFO/LNST nếu đủ số liệu). Sau đó phân tích chuỗi tối đa 8 quý: doanh thu, LNST, biên gộp, YoY và QoQ khi có kỳ so sánh hợp lệ. Chỉ ra xu hướng tăng/giảm/đi ngang và quý bất thường nếu dữ kiện thể hiện rõ. Phân tích CFO theo quý riêng với CFO TTM; CFO quý không phải số dư tiền mặt. Không kết luận nguyên nhân biến động khi thiếu dữ kiện. Kết thúc bằng đánh giá cân bằng về điểm mạnh, điểm cần theo dõi và giới hạn dữ liệu.",
  "catalyst": "3-4 câu. Nêu động lực tăng trưởng có căn cứ. Chỉ nêu Economic Moat riêng của doanh nghiệp nếu có dữ liệu chứng minh; nếu chỉ có hướng dẫn ngành thì nói đó là đặc điểm chung của ngành, không khẳng định là lợi thế đã được xác nhận của doanh nghiệp. Không tự bịa kế hoạch, thị phần hoặc sự kiện.",
  "risks": "3-5 câu. Xếp 2-3 rủi ro quan trọng theo mức độ ảnh hưởng: rủi ro kinh doanh/ngành, tài chính/dòng tiền và định giá nếu có dữ kiện. Nêu cơ sở của từng rủi ro; phân biệt rủi ro đã thể hiện trong số liệu với rủi ro cần theo dõi.",
  "valuation_verdict": "Sao chép nguyên văn verdict do hệ thống cung cấp: '{verdict}'.",
  "outlook": "4-5 câu. Đối chiếu giá {close_price:,.0f} VNĐ với Fair Low {fair_low:,.0f} VNĐ và Fair High {fair_high:,.0f} VNĐ. Nêu rõ giá đang ở dưới, trong hay trên vùng định giá theo đúng dữ kiện. Diễn giải verdict và quyết định '{final_decision}'. Nêu riêng MOS thực tế {mos_actual_pct:.1f}% và upside đến Fair Low {upside_pct:.1f}%; không đánh đồng hai chỉ số. Nêu điều kiện cần có để đánh giá lại, không dự báo giá chắc chắn.",
  "recommendation": "BẮT BUỘC giữ nguyên: '{rec_text} — {rec_weight}'.",
  "macro_view": "3-5 câu. Chỉ phân tích yếu tố vĩ mô/ngành có bằng chứng trong dữ kiện. Nếu không có số liệu cập nhật, nói rõ chưa có dữ liệu xác minh; liệt kê ngắn gọn các biến số cần theo dõi và giải thích kênh tác động có thể có, dùng ngôn ngữ điều kiện như 'có thể', 'cần theo dõi'. Không bịa giá hàng hóa, tỷ giá, lãi suất, chính sách hoặc dự báo."
}}

TỰ KIỂM TRA TRƯỚC KHI TRẢ JSON
- Có số nào không xuất hiện trong dữ kiện hoặc không thể tính trực tiếp từ dữ kiện không? Nếu có, xóa.
- Có nhầm CFO quý với CFO TTM hoặc tiền mặt cuối kỳ không? Nếu có, sửa.
- Có nhầm YoY với QoQ, hoặc so sánh kỳ không tương ứng không? Nếu có, sửa.
- Có khẳng định nguyên nhân, lợi thế cạnh tranh hay diễn biến vĩ mô mà không có bằng chứng không? Nếu có, hạ thành giả thuyết cần theo dõi hoặc bỏ.
- recommendation và valuation_verdict có giữ nguyên dữ liệu hệ thống không? Nếu không, sửa.
Chỉ xuất JSON hợp lệ."""

        headers = {"Authorization": f"Bearer {ALIBABA_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": AI_MODEL_NAME,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": AI_TEMPERATURE,
        }

        for attempt in range(3):
            try:
                res = requests.post(AI_API_URL, headers=headers, json=payload, timeout=60)
                
                if res.status_code == 200:
                    content = res.json()["choices"][0]["message"]["content"].strip()
                    content = re.sub(r"^```json\s*", "", content)
                    content = re.sub(r"^```\s*", "", content)
                    content = re.sub(r"\s*```$", "", content)
                    
                    parsed = json.loads(content)
                    
                    # LUÔN GHI ĐÈ PRICE_ZONE BẰNG DỮ LIỆU CHUẨN TỪ HỆ THỐNG
                    parsed["price_zone"] = (
                        f"Vùng mua: {fair_low:,.0f} VNĐ — {fair_high:,.0f} VNĐ | "
                        f"Giá hiện tại: {close_price:,.0f} VNĐ | Verdict: {verdict}"
                    )

                    # LUÔN GHI ĐÈ KHUYẾN NGHỊ BẰNG DỮ LIỆU CHUẨN TỪ HỆ THỐNG
                    ai_rec = parsed.get("recommendation", "").upper()
                    expected_rec = rec_text.upper()
                    if expected_rec not in ai_rec:
                        print(f"  ⚠️ [{symbol}] AI tự đổi khuyến nghị → Ghi đè: '{rec_text}'", flush=True)
                        parsed["recommendation"] = f"{rec_text} — {rec_weight}"

                    parsed["_source"] = "AI"
                    return parsed
                else:
                    print(f"  ❌ [{symbol}] AI trả về HTTP {res.status_code} (Lần {attempt + 1}/3)", flush=True)
                    time.sleep(3)
                    
            except requests.exceptions.Timeout:
                print(f"  ⏳ [{symbol}] AI Timeout (Lần {attempt + 1}/3), thử lại...", flush=True)
                time.sleep(3)
            except Exception as e:
                print(f"  ❌ [{symbol}] Lỗi AI (Lần {attempt + 1}/3): {e}", flush=True)
                time.sleep(3)

        print(f"  ⚠️ [{symbol}] AI lỗi hoàn toàn (sau 3 lần thử). Bỏ qua mã này.", flush=True)
        return None
        
    except Exception as e:
        print(f"  ❌ Lỗi nghiêm trọng ở _call_ai_for_symbol cho {symbol}: {e}", flush=True)
        return None

# ===================== HOLIDAY CHECKER =====================
def _check_holiday_smart_standalone(vn_now):
    top_with_volume = 0
    top_with_change = 0
    top_total = 0
    
    for sym in LIQUID_SYMBOLS_TOP:
        doc = collection.find_one({"symbol": sym})
        if doc:
            top_total += 1
            vol = doc.get("volume", 0) or 0
            close_p = doc.get("close", 0) or 0
            prev_p = doc.get("prev_close", 0) or 0
            
            if vol > 0:
                top_with_volume += 1
            if prev_p > 0 and close_p != prev_p:
                top_with_change += 1
    
    if top_with_volume >= 3:
        return (False, "99.9%", f"TOP: {top_with_volume}/{top_total} mã có volume > 0")
    
    if top_with_change >= 3:
        return (False, "99%", f"TOP: {top_with_change}/{top_total} mã có biến động giá (volume=0)")
    
    mid_with_volume = 0
    mid_with_change = 0
    mid_total = 0
    
    for sym in LIQUID_SYMBOLS_MID:
        doc = collection.find_one({"symbol": sym})
        if doc:
            mid_total += 1
            vol = doc.get("volume", 0) or 0
            close_p = doc.get("close", 0) or 0
            prev_p = doc.get("prev_close", 0) or 0
            
            if vol > 0:
                mid_with_volume += 1
            if prev_p > 0 and close_p != prev_p:
                mid_with_change += 1
    
    if mid_with_volume >= 2 or mid_with_change >= 5:
        return (False, "95%", f"MID: {mid_with_volume}/{mid_total} mã volume>0, {mid_with_change}/{mid_total} mã biến động")
    
    return (
        True,
        "99.99%",
        f"Cả TOP ({top_total} mã) + MID ({mid_total} mã) đều không giao dịch. Volume=0, giá đứng."
    )


def holiday_checker_worker():
    global holiday_checked_today, holiday_status, holiday_detected_date, is_holiday_paused
    
    print("🗓️ [HOLIDAY] Đã bật thread check ngày lễ (9h30 hàng ngày)!", flush=True)
    
    while True:
        try:
            if is_auto_crawl_paused:
                time.sleep(30)
                continue
            
            vn_now = _vn_now()
            today_str = vn_now.strftime("%Y-%m-%d")
            current_hour = vn_now.hour
            current_minute = vn_now.minute
            weekday = vn_now.weekday()
            
            if weekday >= 5:
                time.sleep(60)
                continue
            
            is_after_930 = (current_hour > 9) or (current_hour == 9 and current_minute >= 30)
            is_before_15h = current_hour < 15
            if not (is_after_930 and is_before_15h):
                time.sleep(30)
                continue
            
            with holiday_check_lock:
                already_checked = (holiday_checked_today == today_str)
            
            if already_checked:
                # FIX BUG #9: Sleep dài hơn sau khi đã check (tránh spam log)
                time.sleep(1800)
                continue
            
            print(f"\n🗓️ [HOLIDAY] Check ngày lễ lúc {vn_now.strftime('%H:%M:%S')} (thread riêng)...", flush=True)
            
            is_holiday, confidence, details = _check_holiday_smart_standalone(vn_now)
            
            with holiday_check_lock:
                holiday_checked_today = today_str
                holiday_status = is_holiday
                if is_holiday:
                    holiday_detected_date = today_str
            
            if is_holiday:
                print(f"🌙 [HOLIDAY] KẾT LUẬN: NGÀY LỄ (Độ tin cậy {confidence})", flush=True)
                print(f"   Chi tiết: {details}", flush=True)
                print(f"🌙 [HOLIDAY] Tạm dừng crawl tới 8h55 hôm sau...", flush=True)
                
                is_holiday_paused = True
                
                tomorrow = vn_now + timedelta(days=1)
                while tomorrow.weekday() >= 5:
                    tomorrow += timedelta(days=1)
                wake_up_time = tomorrow.replace(hour=8, minute=55, second=0, microsecond=0)
                sleep_seconds = (wake_up_time - vn_now).total_seconds()
                time.sleep(min(sleep_seconds, 3600))
                
                is_holiday_paused = False
                print(f"☀️ [HOLIDAY] Đã hết thời gian nghỉ, tự mở lại hệ thống.", flush=True)
            else:
                print(f"✅ [HOLIDAY] KẾT LUẬN: NGÀY LÀM VIỆC (Độ tin cậy {confidence})", flush=True)
                print(f"   Chi tiết: {details}", flush=True)
                # FIX BUG #9: Sleep 1800s thay vì 60s (đã check xong)
                time.sleep(1800)
                
        except Exception as e:
            print(f"❌ [HOLIDAY] Lỗi: {e}", flush=True)
            time.sleep(30)


# ===================== AUTO CRAWLER WORKER =====================
def auto_crawler_worker():
    global holiday_status, holiday_checked_today, holiday_detected_date, is_holiday_paused
    
    print("⏰ [AUTO] Đã bật chế độ cào Real-time (Đọc kết quả check lễ từ thread riêng)!", flush=True)
    
    while True:
        try:
            global is_auto_crawl_paused
            
            vn_now_check = _vn_now()
            today_check = vn_now_check.strftime("%Y-%m-%d")
            with holiday_check_lock:
                if holiday_detected_date and holiday_detected_date != today_check:
                    if is_holiday_paused:
                        print(f"🔧 [AUTO] Auto-reset is_holiday_paused (đã sang ngày {today_check})", flush=True)
                        is_holiday_paused = False
                    holiday_status = None
                    holiday_checked_today = None
                    holiday_detected_date = None
            
            if is_auto_crawl_paused or is_holiday_paused:
                time.sleep(10)
                continue

            vn_now = _vn_now()
            today_str = vn_now.strftime("%Y-%m-%d")
            current_hour = vn_now.hour
            current_minute = vn_now.minute
            weekday = vn_now.weekday()

            is_weekend = weekday >= 5
            if is_weekend:
                tomorrow = vn_now + timedelta(days=1)
                while tomorrow.weekday() >= 5:
                    tomorrow += timedelta(days=1)
                wake_up_time = tomorrow.replace(hour=8, minute=55, second=0, microsecond=0)
                sleep_seconds = (wake_up_time - vn_now).total_seconds()
                if sleep_seconds > 0:
                    print(f"🌙 [AUTO] Hôm nay là cuối tuần. Tạm nghỉ đến {wake_up_time.strftime('%H:%M %d/%m')}...", flush=True)
                    time.sleep(min(sleep_seconds, 3600))
                    continue

            is_lunch_break = (current_hour == 11 and current_minute >= 30) or (current_hour == 12)
            is_trading_time = (9 <= current_hour < 15) and (not is_lunch_break)
            is_closing_window = (current_hour == 15 and current_minute < 5)

            if not (is_trading_time or is_closing_window):
                if is_lunch_break:
                    wake_up_time = vn_now.replace(hour=13, minute=0, second=0, microsecond=0)
                else:
                    if current_hour == 15 and current_minute < 5:
                        time.sleep(15)
                        continue
                    else:
                        wake_up_time = vn_now.replace(hour=8, minute=55, second=0, microsecond=0)
                        if current_hour >= 15:
                            wake_up_time += timedelta(days=1)
                        while wake_up_time.weekday() >= 5:
                            wake_up_time += timedelta(days=1)
                        
                sleep_seconds = (wake_up_time - vn_now).total_seconds()
                if sleep_seconds > 0:
                    print(f"🌙 [AUTO] Ngoài giờ giao dịch. Tạm nghỉ đến {wake_up_time.strftime('%H:%M %d/%m')}...", flush=True)
                    time.sleep(min(sleep_seconds, 3600))
                    continue

            with holiday_check_lock:
                current_holiday_status = holiday_status
                current_holiday_checked_date = holiday_checked_today
            
            is_after_930 = (current_hour > 9) or (current_hour == 9 and current_minute >= 30)
            
            if is_after_930 and current_holiday_checked_date != today_str:
                is_after_10h = (current_hour >= 10)
                if is_after_10h:
                    print(f"⚠️ [AUTO] Quá 10h mà chưa có kết quả check lễ → TỰ CHECK (fallback).", flush=True)
                    try:
                        is_holiday, confidence, details = _check_holiday_smart_standalone(vn_now)
                        with holiday_check_lock:
                            holiday_checked_today = today_str
                            holiday_status = is_holiday
                            if is_holiday:
                                holiday_detected_date = today_str
                        
                        if is_holiday:
                            print(f"🌙 [AUTO] Fallback: NGÀY LỄ (Độ tin cậy {confidence}): {details}", flush=True)
                            is_holiday_paused = True
                            tomorrow = vn_now + timedelta(days=1)
                            while tomorrow.weekday() >= 5:
                                tomorrow += timedelta(days=1)
                            wake_up = tomorrow.replace(hour=8, minute=55, second=0, microsecond=0)
                            time.sleep(min((wake_up - vn_now).total_seconds(), 3600))
                            is_holiday_paused = False
                            continue
                        else:
                            print(f"✅ [AUTO] Fallback: NGÀY LÀM VIỆC (Độ tin cậy {confidence}): {details}", flush=True)
                    except Exception as fe:
                        print(f"❌ [AUTO] Fallback check lỗi: {fe}. Mặc định ngày làm việc.", flush=True)
                        with holiday_check_lock:
                            holiday_checked_today = today_str
                            holiday_status = False
                else:
                    print(f"⏳ [AUTO] Chờ holiday_checker_worker check xong (đã qua 9h30)...", flush=True)
                    time.sleep(15)
                    continue
            
            if current_holiday_checked_date == today_str and current_holiday_status == True:
                print(f"🌙 [AUTO] Hôm nay là ngày lễ (theo holiday_checker). Tạm nghỉ...", flush=True)
                time.sleep(60)
                continue

            collection.update_many({"chot_so_today": {"$ne": today_str}}, {"$unset": {"chot_so_today": "", "is_locked": ""}})
            all_symbols = [doc["symbol"] for doc in collection.find({}, {"symbol": 1})]
            crawled_count = collection.count_documents({"chot_so_today": today_str})

            if crawled_count >= len(all_symbols) and len(all_symbols) > 0:
                if current_hour == 15 and current_minute < 5:
                    time.sleep(15)
                    continue
                time.sleep(60)
                continue

            if not is_crawling:
                symbols_to_crawl = []
                for sym in all_symbols:
                    doc = collection.find_one({"symbol": sym}, {"chot_so_today": 1})
                    if not doc or doc.get("chot_so_today") != today_str: 
                        symbols_to_crawl.append(sym)
                
                if not symbols_to_crawl: 
                    symbols_to_crawl = all_symbols 
                    
                _execute_crawl(symbols_to_crawl, is_manual=False)
                
            time.sleep(15)
            
        except Exception as e:
            print(f"❌ [AUTO] Lỗi: {e}", flush=True)
            time.sleep(10)


# ===================== FULL DATA CRAWLER =====================
def full_data_crawler_worker():
    while True:
        try:
            # Dùng chung công tắc pause với auto real-time để có thể dừng
            # toàn bộ lịch cào tự động khi kiểm thử.
            if is_auto_crawl_paused:
                time.sleep(10)
                continue

            vn_now = _vn_now()
            is_bctc_time = (0 <= vn_now.hour < 6 and vn_now.minute == 0 and vn_now.second < 10)
            if is_bctc_time:
                target_symbols = [doc["symbol"] for doc in collection.find({}, {"symbol": 1})]
                expected_period = _expected_latest_bctc_period(vn_now)

                for symbol in target_symbols:
                    # Nếu nhận lệnh pause trong lúc đang chạy, dừng trước mã tiếp theo.
                    if is_auto_crawl_paused:
                        logger.info("[BCTC AUTO] Đã tạm dừng theo yêu cầu kiểm thử.")
                        break

                    bctc_doc = bctc_col.find_one(
                        {"symbol": symbol},
                        {"updated_at": 1, "latest_period": 1, "latest_income_period": 1}
                    )
                    need = False
                    retry_days = 30

                    if not bctc_doc or not bctc_doc.get("updated_at"):
                        need = True
                    else:
                        latest_period = str(bctc_doc.get("latest_income_period") or "")
                        # Chỉ kỳ khớp chính xác mới được xem là hiện hành.
                        # Kỳ thiếu, sai định dạng, cũ hoặc nằm trong tương lai đều cần rà soát sớm.
                        period_is_stale = not _bctc_period_is_current(
                            latest_period, expected_period
                        )

                        # Dữ liệu chưa xác minh đúng kỳ: thử cập nhật mỗi 7 ngày.
                        # Dữ liệu khớp kỳ dự kiến: giữ chu kỳ kiểm tra định kỳ 30 ngày.
                        retry_days = 7 if period_is_stale else 30

                        try:
                            last_update = datetime.strptime(
                                bctc_doc["updated_at"], "%d/%m/%Y %H:%M:%S"
                            ).replace(tzinfo=timezone.utc)
                            age_days = (
                                datetime.now(timezone.utc) - last_update
                            ).total_seconds() / 86400
                            need = age_days >= retry_days
                        except (TypeError, ValueError):
                            need = True

                        if period_is_stale:
                            logger.info(
                                "[BCTC AUTO] %s chưa khớp kỳ dự kiến: latest=%s, kỳ dự kiến=%s, "
                                "lịch thử cập nhật=%d ngày",
                                symbol, latest_period, expected_period, retry_days
                            )

                    if need:
                        logger.info(
                            "[BCTC AUTO] Đang làm mới %s (kỳ hiện có=%s, kỳ dự kiến=%s)",
                            symbol,
                            (bctc_doc or {}).get("latest_period", "chưa có"),
                            expected_period
                        )
                        res = _fetch_bctc_ssi(symbol)
                        if res:
                            res.update({"symbol": symbol, "updated_at": _now_str()})
                            bctc_col.update_one(
                                {"symbol": symbol}, {"$set": res}, upsert=True
                            )

                            extracted = extract_metrics_from_bctc_data(res)
                            if extracted:
                                collection.update_one(
                                    {"symbol": symbol}, {"$set": extracted}
                                )
                            logger.info(
                                "[BCTC AUTO] %s cập nhật xong | kỳ mới nhất=%s",
                                symbol, res.get("latest_period", "không rõ")
                            )
                        else:
                            logger.warning("[BCTC AUTO] %s không lấy được dữ liệu mới", symbol)

                        time.sleep(3.0)
                time.sleep(3600)
            else:
                time.sleep(30)
        except Exception as exc:
            log_exception("[BCTC AUTO WORKER]", exc)
            time.sleep(60)


# ===================== SCREENER DATA PRELOAD =====================
def _load_screener_bctc(symbols, chunk_size=10):
    """Đọc BCTC theo từng nhóm mã, chỉ lấy 3 bảng cần cho tính điểm."""
    result = {}
    symbols = list(dict.fromkeys(symbols))
    total = len(symbols)
    for start in range(0, total, chunk_size):
        chunk = symbols[start:start + chunk_size]
        cursor = bctc_col.find(
            {"symbol": {"$in": chunk}},
            {
                "_id": 0, "symbol": 1,
                "source": 1, "income_statement": 1, "cash_flow": 1, "balance_sheet": 1
            }
        ).batch_size(chunk_size)
        for doc in cursor:
            if doc.get("symbol"):
                result[doc["symbol"]] = doc
        print(f"[SCREENER] BCTC preload: {min(start + len(chunk), total)}/{total} mã", flush=True)
    return result


def _load_screener_history(symbols, chunk_size=5, bars_limit=1400):
    """Đọc lịch sử theo nhóm nhỏ để tránh một truy vấn trả về payload quá lớn."""
    result = {}
    symbols = list(dict.fromkeys(symbols))
    total = len(symbols)
    for start in range(0, total, chunk_size):
        chunk = symbols[start:start + chunk_size]
        cursor = history_col.find(
            {"symbol": {"$in": chunk}},
            {
                "_id": 0, "symbol": 1,
                "history_d1": {"$slice": -bars_limit}
            }
        ).batch_size(chunk_size)
        for doc in cursor:
            if doc.get("symbol"):
                result[doc["symbol"]] = doc
        print(f"[SCREENER] History preload: {min(start + len(chunk), total)}/{total} mã", flush=True)
    return result


# ===================== SCREENER WORKER =====================
def screener_worker():
    while True:
        try:
            if is_screener_running:
                time.sleep(30)
                continue
            
            vn_now = _vn_now()
            today_str = vn_now.strftime("%Y-%m-%d")
            current_hour = vn_now.hour
            current_minute = vn_now.minute
            
            all_symbols_count = collection.count_documents({})
            crawled_count = collection.count_documents({"chot_so_today": today_str})
            
            is_1630 = (current_hour == 16 and vn_now.minute == 30 and vn_now.second < 10)
            is_after_close = (current_hour > 15) or (current_hour == 15 and current_minute >= 5)
            is_just_closed = (is_after_close and crawled_count >= all_symbols_count and all_symbols_count > 0)

            if is_1630 or is_just_closed:
                if not _claim_screener_job():
                    time.sleep(30)
                    continue

                owns_screener_job = True
                today_screener_check = screener_col.find_one({"date": today_str})
                if today_screener_check:
                    _release_screener_job()
                    owns_screener_job = False
                    time.sleep(60)
                    continue

                print(f"\n[SCREENER] ⏰ Kích hoạt chấm điểm (Lý do: {'16h30' if is_1630 else 'Vừa chốt sổ sau 15h05'})", flush=True)
                
                # TỐI ƯU: Preload toàn bộ dữ liệu vào RAM
                print("[SCREENER] ⚡ Đang tải trước toàn bộ dữ liệu (Stocks, BCTC, History)...", flush=True)
                # Không tải bctc_history cũ từ collection stocks: dữ liệu này được thay
                # bằng BCTC đã preload ngay bên dưới, tránh nhân đôi RAM.
                all_stocks = list(collection.find({}, {"bctc_history": 0}))
                screener_symbols = [doc["symbol"] for doc in all_stocks if doc.get("symbol")]
                all_bctc = _load_screener_bctc(screener_symbols, chunk_size=10)
                all_hist = _load_screener_history(screener_symbols, chunk_size=5, bars_limit=1400)
                
                stock_db = {}
                bulk_updates = []
                
                print("[SCREENER] 🧠 Đang xử lý dữ liệu trong RAM...", flush=True)
                for doc in all_stocks:
                    sym = doc["symbol"]
                    bctc = all_bctc.get(sym)
                    doc["bctc_history"] = bctc.get("income_statement", []) if bctc else []
                    
                    if bctc:
                        extracted = extract_metrics_from_bctc_data(bctc)
                        if extracted:
                            doc.update(extracted)
                            bulk_updates.append(UpdateOne({"symbol": sym}, {"$set": extracted}))
                    
                    stock_db[sym] = doc

                if bulk_updates:
                    collection.bulk_write(bulk_updates)
                    # Peer medians phụ thuộc các chỉ số vừa cập nhật; không giữ cache cũ.
                    _SECTOR_MEDIAN_CACHE.clear()
                    print(f"[SCREENER] ✅ Đã bulk update metrics cho {len(bulk_updates)} mã.", flush=True)

                normalized_db = {}
                norm_bulk_updates = []
                for s, r in stock_db.items():
                    nd = normalize_stock_data(s, r)
                    update_fields = nd.pop("_update_fields", {})
                    if update_fields:
                        norm_bulk_updates.append(UpdateOne({"symbol": s}, {"$set": update_fields}))
                    normalized_db[s] = nd
                
                if norm_bulk_updates:
                    collection.bulk_write(norm_bulk_updates)
                    _SECTOR_MEDIAN_CACHE.clear()

                # Sau chuẩn hóa chỉ giữ các trường còn dùng cho AI/template và báo cáo.
                # Giải phóng bản ghi gốc cùng toàn bộ BCTC thô trước khi chấm điểm.
                stock_db = {
                    sym: {
                        key: doc.get(key)
                        for key in (
                            "ai_bctc", "ai_catalyst", "ai_risks", "ai_macro_view",
                            "ai_outlook", "ai_recommendation", "ai_price_zone",
                            "ai_updated_at", "ai_source",
                            "price_change_percent", "volume_percent", "close"
                        )
                        if key in doc
                    }
                    for sym, doc in stock_db.items()
                }
                del all_stocks, all_bctc

                sector_medians = compute_sector_medians(normalized_db)
                results = []
                
                for symbol, nd in normalized_db.items():
                    grp = get_sub_sector(symbol)
                    cfg = SUB_SECTOR_CONFIG.get(grp) or SUB_SECTOR_CONFIG.get(get_sector(symbol))
                    med, _ = get_median_for_symbol(symbol, sector_medians)
                    
                    hist_doc = all_hist.get(symbol)
                    history_bars = hist_doc.get("history_d1", []) if hist_doc else []
                    tech_bars = history_bars[-200:]
                    tech_score_raw, tech_status, pattern, technical_details = 0, "Chưa đủ dữ liệu kỹ thuật", "Chưa xác định", {}
                    if ProfessionalTechnicalAnalyzer and tech_bars:
                        try:
                            analyzer = ProfessionalTechnicalAnalyzer(tech_bars)
                            ptkt_res = analyzer.analyze()
                            tech_score_raw = ptkt_res.get("technical_score", 0)
                            tech_status = ptkt_res.get("technical_status", "Chưa đủ dữ liệu kỹ thuật")
                            pattern = ptkt_res.get("pattern", "Chưa xác định")
                            technical_details = ptkt_res.get("technical_score_breakdown", {})
                        except Exception as tech_error:
                            print(f"[SCREENER] {symbol} lỗi phân tích kỹ thuật: {tech_error}", flush=True)
                        
                    stock_doc = stock_db.get(symbol, {})
                    existing_ai = {
                        "bctc": stock_doc.get("ai_bctc", ""), "catalyst": stock_doc.get("ai_catalyst", ""),
                        "risks": stock_doc.get("ai_risks", ""), "macro_view": stock_doc.get("ai_macro_view", ""),
                        "outlook": stock_doc.get("ai_outlook", ""), "recommendation": stock_doc.get("ai_recommendation", ""),
                        "price_zone": stock_doc.get("ai_price_zone", ""), "ai_updated_at": stock_doc.get("ai_updated_at", "Chưa phân tích"),
                        "ai_source": stock_doc.get("ai_source", "AI")
                    }

                    res = evaluate_stock_from_db(
                        symbol, nd, med, grp, cfg, 
                        tech_score_raw=tech_score_raw, tech_status=tech_status, pattern=pattern,
                        technical_details=technical_details,
                        existing_ai_data=existing_ai,
                        history_bars=history_bars
                    )
                    if res: 
                        res["date"] = today_str
                        results.append(res)
                
                if results:
                    print(f"[SCREENER] 🔄 Đang tính lại final_decision lần 2 để đảm bảo tính nhất quán...", flush=True)
                    for r in results:
                        sym = r["symbol"]
                        nd_final = normalized_db.get(sym, {})
                        # Persist BCTC freshness provenance with each Screener row.
                        # Previously diagnostics queried this field, but the worker
                        # never copied it into the result document; missing fields
                        # were therefore misreported as stale for every symbol.
                        r["bctc_latest_income_period"] = nd_final.get("bctc_latest_income_period", "")
                        r["bctc_expected_period"] = nd_final.get("bctc_expected_period", "")
                        r["bctc_period_is_current"] = (
                            bool(nd_final.get("bctc_period_is_current"))
                            if nd_final.get("bctc_latest_income_period") else None
                        )
                        
                        fd_label, fd_color, fd_reason = compute_final_decision(
                            fund_score=r.get("fundamental_score", 0),
                            tech_score=r.get("technical_score", 0),
                            val_score=r.get("valuation_score", 0),
                            total_score=r.get("total_score", 0),
                            verdict=r.get("valuation_verdict", "CHƯA TÍNH"),
                            cfo=nd_final.get("cfo_latest", 0),
                            npat=nd_final.get("net_profit_ttm", 0),
                            roe=nd_final.get("roe", 0),
                            debt_to_equity=nd_final.get("debt_to_equity", 0),
                            discount_to_fair_pct=r.get("discount_to_fair_pct", 0),
                            close_price=r.get("close_price", 0),
                            fair_high=r.get("fair_value_high", 0),
                            fair_low=r.get("fair_value_low", 0),
                            sector=get_sub_sector(sym),
                            fundamental_coverage_pct=float(r.get("fundamental_score_coverage_pct", 0) or 0),
                            bctc_period_is_current=nd_final.get("bctc_period_is_current", False)
                        )
                        r["final_decision"] = fd_label
                        r["final_decision_color"] = fd_color
                        r["final_decision_reason"] = fd_reason
                    print(f"[SCREENER] ✅ Đã tính lại final_decision cho {len(results)} mã.", flush=True)

                if results:
                    print(f"[SCREENER] 📝 Đang kiểm tra và tạo Template cho các mã chưa có AI...", flush=True)
                    template_count = 0
                    for r in results:
                        sym = r["symbol"]
                        existing = stock_db.get(sym, {})
                        
                        if existing and existing.get("ai_bctc") and len(existing.get("ai_bctc")) > 100:
                            continue
                        
                        nd_for_template = normalized_db.get(sym, {})
                        template_data = build_fallback_narrative(sym, nd_for_template, r, get_sector(sym))
                        
                        if template_data:
                            collection.update_one({"symbol": sym}, {"$set": {
                                "ai_bctc": template_data.get("bctc", ""),
                                "ai_catalyst": template_data.get("catalyst", ""),
                                "ai_risks": template_data.get("risks", ""),
                                "ai_outlook": template_data.get("outlook", ""),
                                "ai_recommendation": template_data.get("recommendation", ""),
                                "ai_price_zone": template_data.get("price_zone", ""),
                                "ai_macro_view": template_data.get("macro_view", ""),
                                "ai_updated_at": _now_str(),
                                "ai_source": "TEMPLATE"
                            }})
                            template_count += 1
                    print(f"[SCREENER] ✅ Đã tạo Template cho {template_count} mã (các mã có AI thật đã được bảo vệ).", flush=True)
                        
                if results:
                    results = sorted(results, key=lambda x: (x.get("total_score", 0), x.get("fundamental_score", 0)), reverse=True)
                    
                    protected_count = 0
                    for r in results:
                        sym = r["symbol"]
                        existing_stock = stock_db.get(sym, {})
                        if existing_stock and existing_stock.get("ai_bctc") and len(existing_stock.get("ai_bctc")) > 100:
                            r["bctc_evaluation"] = existing_stock.get("ai_bctc", "")
                            r["outlook"] = existing_stock.get("ai_outlook", "")
                            r["ai_recommendation"] = existing_stock.get("ai_recommendation", "")
                            r["ai_price_zone"] = existing_stock.get("ai_price_zone", "")
                            r["macro_view"] = existing_stock.get("ai_macro_view", "")
                            r["ai_updated_at"] = existing_stock.get("ai_updated_at", "")
                            r["ai_source"] = existing_stock.get("ai_source", "AI")
                            protected_count += 1
                    
                    if _safe_replace_screener_results(results, tag="worker"):
                        commit_multiple_files_to_github({SCREENER_JSON_FILE: results}, f"Update Screener {_now_str()}")
                        print(f"[SCREENER] ✅ Đã chấm điểm xong cho {len(results)} mã (Bảo vệ AI cho {protected_count} mã).", flush=True)
                    else:
                        print(f"[SCREENER] ⚠️ Không ghi được kết quả (lock timeout), giữ dữ liệu cũ.", flush=True)

                    try:
                        up_count = 0; down_count = 0; ref_count = 0
                        strong_movers = []; value_stocks = []; danger_stocks = []
                        missing_price_count = 0; missing_pe_count = 0; missing_pb_count = 0; missing_roe_count = 0
                        missing_pe_symbols = []; missing_pb_symbols = []; missing_roe_symbols = []; missing_price_symbols = []

                        for r in results:
                            decision = r.get("final_decision", "")
                            if "TRÁNH" in decision: 
                                if len(danger_stocks) < 3:
                                    danger_stocks.append(f"  • {r['symbol']} (Điểm: {r.get('total_score', 0)})")

                            stock_data = stock_db.get(r['symbol'], {})
                            pct_change = stock_data.get("price_change_percent", 0)
                            vol_pct = stock_data.get("volume_percent", 0)
                            close_price = stock_data.get("close", 0)

                            if pct_change > 0.05: up_count += 1
                            elif pct_change < -0.05: down_count += 1
                            else: ref_count += 1

                            if abs(pct_change) >= 3.0 and vol_pct >= 120.0:
                                arrow = "🔴" if pct_change < 0 else "🟢"
                                strong_movers.append(f"  {arrow} <b>{r['symbol']}</b>: {pct_change:+.2f}% (Vol: {vol_pct:.0f}%)")

                            if "RẺ" in r.get("valuation_verdict", "") and r.get("fundamental_score", 0) >= 30:
                                if len(value_stocks) < 3:
                                    value_stocks.append(f"  • <b>{r['symbol']}</b> ({r.get('sector')}): Giá {close_price:,.0f} - Vùng mua ≤ {r.get('fair_value_low',0):,.0f}")
                            
                            warnings = r.get("data_warnings", [])
                            if "Thiếu Giá" in warnings: 
                                missing_price_count += 1
                                if len(missing_price_symbols) < 5: missing_price_symbols.append(r['symbol'])
                            if "Thiếu P/E" in warnings: 
                                missing_pe_count += 1
                                if len(missing_pe_symbols) < 5: missing_pe_symbols.append(r['symbol'])
                            if "Thiếu P/B" in warnings: 
                                missing_pb_count += 1
                                if len(missing_pb_symbols) < 5: missing_pb_symbols.append(r['symbol'])
                            if "Thiếu ROE" in warnings: 
                                missing_roe_count += 1
                                if len(missing_roe_symbols) < 5: missing_roe_symbols.append(r['symbol'])

                        data_alert_lines = []
                        if missing_pe_count > 0:
                            sample = f" (VD: {', '.join(missing_pe_symbols)}...)" if missing_pe_symbols else ""
                            data_alert_lines.append(f"  • <b>{missing_pe_count} mã</b> thiếu P/E{sample}")
                        if missing_pb_count > 0:
                            sample = f" (VD: {', '.join(missing_pb_symbols)}...)" if missing_pb_symbols else ""
                            data_alert_lines.append(f"  • <b>{missing_pb_count} mã</b> thiếu P/B{sample}")
                        if missing_roe_count > 0:
                            sample = f" (VD: {', '.join(missing_roe_symbols)}...)" if missing_roe_symbols else ""
                            data_alert_lines.append(f"  • <b>{missing_roe_count} mã</b> thiếu ROE{sample}")
                        if missing_price_count > 0:
                            sample = f" (VD: {', '.join(missing_price_symbols)}...)" if missing_price_symbols else ""
                            data_alert_lines.append(f"  • <b>{missing_price_count} mã</b> bị thiếu Giá (Lỗi cào giá){sample}")

                        total_up_down = up_count + down_count
                        ratio_up = (up_count / total_up_down) * 100 if total_up_down > 0 else 50

                        if ratio_up >= 65:
                            market_view = "🟢 <b>TÂM LÝ HƯNG PHẤN:</b> Dòng tiền lan tỏa mạnh, ưu tiên nắm giữ cổ phiếu."
                        elif ratio_up <= 35:
                            market_view = "🔴 <b>TÂM LÝ BI QUAN:</b> Áp lực bán chiếm ưu thế, hạn chế mua mới."
                        else:
                            market_view = "🟡 <b>THỊ TRƯỜNG PHÂN HÓA:</b> Dòng tiền có sự chọn lọc, chỉ nên mua các mã có nền tảng tốt."

                        msg_lines = []
                        if data_alert_lines:
                            msg_lines.append(f"⚠️ <b>CẢNH BÁO LỖI DỮ LIỆU API!</b>")
                            msg_lines.append(f"Phát hiện các mã bị thiếu thông số để chấm điểm:")
                            msg_lines.extend(data_alert_lines)
                            msg_lines.append(f"  👉 <i>Hãy chạy lại API /api/crawl-ratios hoặc /api/force-bctc!</i>")
                            msg_lines.append(f"━━━━━━━━━━━━━━━━━━")

                        msg_lines.extend([
                            f"📊 <b>BÁO CÁO THỊ TRƯỜNG TỰ ĐỘNG</b>",
                            f"⏰ Cập nhật: {_now_str()}",
                            f"━━━━━━━━━━━━━━━━━━",
                            f"<b>1️⃣ TỔNG QUAN ĐỘ RỘNG</b>",
                            f"  🟢 Tăng: <b>{up_count}</b> | 🔴 Giảm: <b>{down_count}</b> | ⚪ Đứng: {ref_count}",
                            f"  👉 {market_view}",
                            f"━━━━━━━━━━━━━━━━━━",
                            f"<b>2️⃣ DÒNG TIỀN ĐỘT BIẾN (Hot Money)</b>"
                        ])
                        
                        if strong_movers:
                            msg_lines.extend(strong_movers)
                        else:
                            msg_lines.append("  💤 Không có mã nào biến động quá 3%.")

                        msg_lines.append(f"━━━━━━━━━━━━━━━━━━")
                        msg_lines.append(f"<b>3️⃣ CƠ HỘI GIẢI NGÂN (Vùng mua an toàn)</b>")
                        if value_stocks:
                            msg_lines.extend(value_stocks)
                        else:
                            msg_lines.append("  ⏳ Hiện chưa có mã nào thỏa mãn (Rẻ + Cơ bản tốt).")

                        msg_lines.append(f"━━━━━━━━━━━━━━━━━━")
                        msg_lines.append(f"<b>4️⃣ CẢNH BÁO RỦI RO</b>")
                        msg_lines.append(f"  ❌ Có <b>{len(danger_stocks)}</b> mã bị đánh giá TRÁNH (Giá đắt, nền tảng yếu).")
                        if danger_stocks:
                             msg_lines.append(f"  ⚠️ <i>Điển hình: {', '.join(danger_stocks).replace('  • ', '')}</i>")

                        send_telegram("\n".join(msg_lines))
                        print("[SCREENER] 🚀 Đã gửi báo cáo thị trường qua Telegram.", flush=True)
                    except Exception as te:
                        print(f"[SCREENER] ❌ Lỗi khi gửi Telegram: {te}", flush=True)

                _release_screener_job()
                owns_screener_job = False
                time.sleep(120)
            time.sleep(30)
        except Exception as e: 
            if locals().get("owns_screener_job", False):
                _release_screener_job()
                owns_screener_job = False
            print(f"❌ [SCREENER] Lỗi: {e}", flush=True)
            time.sleep(60)

# ===================== START WORKERS =====================
threading.Thread(target=auto_crawler_worker, daemon=True).start()
threading.Thread(target=holiday_checker_worker, daemon=True).start()
threading.Thread(target=full_data_crawler_worker, daemon=True).start()
threading.Thread(target=screener_worker, daemon=True).start()

# ===================== ROUTES =====================
@app.get("/")
def home(): 
    print("[API] 🏠 Gọi API trang chủ (/)", flush=True)
    return "API is running!", 200


@app.get("/healthz")
def healthz():
    """Liveness probe: process and Flask router are responding."""
    return jsonify({"status": "ok", "check": "live"}), 200


@app.get("/readyz")
def readyz():
    """Readiness probe: require MongoDB to answer before Render routes traffic."""
    try:
        mongo_client.admin.command("ping", maxTimeMS=3000)
        return jsonify({"status": "ok", "check": "ready", "database": "available"}), 200
    except Exception as exc:
        logger.warning("[READINESS] MongoDB ping failed: %s", type(exc).__name__)
        return jsonify({
            "status": "not_ready",
            "check": "ready",
            "database": "unavailable",
        }), 503


@app.get("/api/status")
def api_status():
    print("[API] 📊 Gọi API /api/status", flush=True)
    return jsonify({
        "status": "success", 
        "is_crawling": is_crawling, 
        "is_paused": is_auto_crawl_paused,
        "is_holiday_paused": is_holiday_paused,
        "holiday_status": holiday_status,
        "message": "Đang cào real-time" if is_crawling else "Sẵn sàng"
    })


@app.get("/api/admin/bctc-summary")
def api_admin_bctc_summary():
    """Đọc tóm tắt cấu trúc BCTC của một mã; không trả toàn bộ dữ liệu tài chính."""
    if not ADMIN_SECRET_KEY or request.headers.get("X-Admin-Key") != ADMIN_SECRET_KEY:
        return jsonify({"status": "error", "message": "Không có quyền truy cập."}), 403

    symbol = request.args.get("symbol", "").upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{2,10}", symbol):
        return jsonify({"status": "error", "message": "Mã cổ phiếu không hợp lệ."}), 400

    try:
        pipeline = _build_bctc_summary_pipeline(symbol)
        cursor = bctc_col.aggregate(pipeline, maxTimeMS=8000)
        doc = next(cursor, None)

        if not doc:
            return jsonify({
                "status": "error",
                "message": f"Không tìm thấy BCTC của {symbol}."
            }), 404

        statements = {}
        for statement_name in ("balance_sheet", "income_statement", "cash_flow"):
            rows = doc.get(statement_name) or []
            field_catalog = {}
            periods = set()

            for item in rows:
                if not isinstance(item, dict):
                    continue
                source_field = str(item.get("source_field") or "").strip()
                period = str(item.get("period") or "").strip()
                if period:
                    periods.add(period)
                if source_field:
                    existing = field_catalog.get(source_field)
                    if not existing:
                        field_catalog[source_field] = {
                            "source_field": source_field,
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "unit": item.get("unit"),
                            "is_core": bool(item.get("is_core", False)),
                        }
                    elif item.get("is_core"):
                        existing["is_core"] = True

            sorted_periods = sorted(periods, reverse=True)
            fields = sorted(
                field_catalog.values(),
                key=lambda item: (not item.get("is_core", False), item.get("source_field") or "")
            )
            quality = validate_bctc_rows(
                rows,
                statement_name,
                equations=get_vci_reconciliation_equations(statement_name),
            )
            equation_status_counts = {}
            failed_equations = []
            for check in quality.get("reconciliations", []):
                status = check.get("status", "unknown")
                equation_status_counts[status] = equation_status_counts.get(status, 0) + 1
                if status == "fail":
                    failed_equations.append({
                        "id": check.get("id"),
                        "period": check.get("period"),
                        "difference": check.get("difference"),
                    })

            statements[statement_name] = {
                "quality": {
                    "valid": quality["valid"],
                    "scoreable": quality["scoreable"],
                    "period_basis": quality["period_basis"],
                    "error_count": len(quality["errors"]),
                    "warning_count": len(quality["warnings"]),
                    "equation_status_counts": equation_status_counts,
                    "failed_equations": failed_equations[:20],
                    "failed_equations_truncated": len(failed_equations) > 20,
                    "note": "Chỉ là kiểm tra chất lượng dữ liệu; kỳ chưa được xác minh nên chưa đủ điều kiện chấm điểm.",
                },
                "record_count": len(rows),
                "field_count": len(field_catalog),
                "core_field_count": sum(1 for item in field_catalog.values() if item.get("is_core")),
                "period_count": len(periods),
                "periods": sorted_periods[:12],
                "fields": fields[:150],
                "fields_truncated": len(fields) > 150,
            }

        sector_profile = get_industry_profile(get_sub_sector(symbol))
        profile_registry = validate_industry_profiles()
        metric_catalog = get_sector_metric_catalog(get_sub_sector(symbol))
        metric_catalog_validation = validate_metric_catalog()
        engine_readiness = {
            "profile_id": sector_profile.get("profile_id") if sector_profile else None,
            "profile_version": sector_profile.get("profile_version") if sector_profile else None,
            "profile_status": sector_profile.get("status") if sector_profile else "unknown_profile",
            "scoring_enabled": bool(sector_profile and sector_profile.get("scoring_enabled") is True),
            "data_gaps": sector_profile.get("data_gaps", []) if sector_profile else [],
            "metric_blueprint_status": sector_profile.get("metric_blueprint_status") if sector_profile else None,
            "required_metric_groups": sector_profile.get("metric_groups", {}) if sector_profile else {},
            "metric_catalog": metric_catalog or {},
            "metric_readiness_summary": summarize_sector_metric_readiness(get_sub_sector(symbol)),
            "global_metric_readiness": summarize_all_sector_metric_readiness(),
            "metric_catalog_valid": metric_catalog_validation["valid"],
            "reason": (
                sector_profile.get("status_note") or sector_profile.get("reason") or ""
                if sector_profile else "Không có hồ sơ ngành đã cấu hình."
            ),
            "profile_registry_valid": profile_registry["valid"],
        }

        return jsonify({
            "status": "success",
            "symbol": symbol,
            "analysis_engine": {
                "core_version": ENGINE_VERSION,
                "metric_contract_version": METRIC_CONTRACT_VERSION,
                "metric_catalog_version": METRIC_CATALOG_VERSION,
                "scoring_version": SCORING_ENGINE_VERSION,
                "valuation_version": VALUATION_ENGINE_VERSION,
                "decision_version": DECISION_ENGINE_VERSION,
                "report_template_version": REPORT_TEMPLATE_VERSION,
                "readiness": engine_readiness,
            },
            "updated_at": doc.get("updated_at"),
            "latest_period": doc.get("latest_period"),
            "source": doc.get("source"),
            "vci_extraction_version": doc.get("vci_extraction_version"),
            "vci_field_counts_saved": doc.get("vci_field_counts") or {},
            "vci_period_counts_saved": doc.get("vci_period_counts") or {},
            "metadata_catalog_available": bool(doc.get("vci_field_catalog")),
            "statements": statements,
            "note": "Chỉ trả cấu trúc và danh mục trường; không trả giá trị BCTC."
        }), 200
    except Exception as e:
        log_exception(f"[API BCTC SUMMARY] Lỗi đọc {symbol}", e)
        return jsonify({
            "status": "error",
            "message": "Không đọc được tóm tắt BCTC. Kiểm tra log backend."
        }), 500


def _build_bctc_summary_pipeline(symbol):
    """Project only BCTC field metadata, never large numeric value arrays."""
    def compact_rows(statement_name):
        return {
            "$map": {
                "input": {"$ifNull": ["$" + statement_name, []]},
                "as": "row",
                "in": {
                    "source_field": "$$row.source_field",
                    "id": "$$row.id",
                    "name": "$$row.name",
                    "unit": "$$row.unit",
                    "period": "$$row.period",
                    "is_core": "$$row.is_core",
                },
            }
        }

    return [
        {"$match": {"symbol": symbol}},
        {"$project": {
            "_id": 0,
            "symbol": 1,
            "updated_at": 1,
            "latest_period": 1,
            "source": 1,
            "vci_extraction_version": 1,
            "vci_field_counts": 1,
            "vci_period_counts": 1,
            "vci_field_catalog": 1,
            "balance_sheet": compact_rows("balance_sheet"),
            "income_statement": compact_rows("income_statement"),
            "cash_flow": compact_rows("cash_flow"),
        }},
        {"$limit": 1},
    ]


def _build_bctc_bank_income_pipeline(periods):
    """Return only verified bank income-statement source fields for selected quarters."""
    source_fields = [
        "isb25", "isb26", "isb27", "isb28", "isb29", "isb30",
        "isb31", "isb32", "isb33", "isb34", "isb35", "isb36",
        "isb37", "isb38", "isb39", "isb40", "isb41",
        "isa16", "isa20", "isa22",
    ]
    return [
        {"$match": {}},
        {"$project": {
            "_id": 0,
            "symbol": 1,
            "updated_at": 1,
            "latest_period": 1,
            "source": 1,
            "income_statement": {
                "$filter": {
                    "input": {"$ifNull": ["$income_statement", []]},
                    "as": "row",
                    "cond": {
                        "$and": [
                            {"$in": ["$$row.source_field", source_fields]},
                            {"$in": ["$$row.period", list(periods)]},
                        ]
                    },
                }
            },
        }},
        {"$limit": 1},
    ]


def _build_bctc_selected_fields_pipeline(period=None):
    """Build Mongo aggregation that returns only selected diagnostic balance-sheet rows."""
    selected_period = period if period else "$latest_period"
    return [
        {"$match": {}},
        {"$project": {
            "_id": 0,
            "symbol": 1,
            "updated_at": 1,
            "latest_period": 1,
            "source": 1,
            "balance_sheet": {
                "$filter": {
                    "input": {"$ifNull": ["$balance_sheet", []]},
                    "as": "row",
                    "cond": {
                        "$and": [
                            {"$in": ["$$row.source_field", ["bsb103", "bsb104", "bsb105"]]},
                            {"$eq": ["$$row.period", selected_period]},
                        ]
                    },
                }
            },
        }},
        {"$limit": 1},
    ]


@app.get("/api/admin/bctc-bank-income")
def api_admin_bctc_bank_income():
    """Return a compact set of bank income-statement fields across selected quarters."""
    if not ADMIN_SECRET_KEY or request.headers.get("X-Admin-Key") != ADMIN_SECRET_KEY:
        return jsonify({"status": "error", "message": "Không có quyền truy cập."}), 403

    symbol = request.args.get("symbol", "").upper().strip()
    periods_raw = request.args.get(
        "periods",
        "2025-Q1,2025-Q2,2025-Q3,2025-Q4,2026-Q1,2026-Q2,2026-Q3"
    )
    periods = [item.strip() for item in periods_raw.split(",") if item.strip()]
    if not re.fullmatch(r"[A-Z0-9]{2,10}", symbol):
        return jsonify({"status": "error", "message": "Mã cổ phiếu không hợp lệ."}), 400
    if not periods or len(periods) > 12 or any(
        not re.fullmatch(r"\d{4}-Q[1-4]", item) for item in periods
    ):
        return jsonify({"status": "error", "message": "Danh sách kỳ BCTC không hợp lệ (tối đa 12 kỳ)."}), 400
    if len(set(periods)) != len(periods):
        return jsonify({"status": "error", "message": "Danh sách kỳ BCTC bị trùng."}), 400

    try:
        pipeline = _build_bctc_bank_income_pipeline(periods)
        pipeline[0]["$match"] = {"symbol": symbol}
        doc = next(bctc_col.aggregate(pipeline, maxTimeMS=8000), None)
        if not doc:
            return jsonify({"status": "error", "message": f"Không tìm thấy BCTC của {symbol}."}), 404

        rows = doc.get("income_statement") or []
        return jsonify({
            "status": "success",
            "symbol": symbol,
            "source": doc.get("source"),
            "updated_at": doc.get("updated_at"),
            "latest_period": doc.get("latest_period"),
            "requested_periods": periods,
            "fields": rows,
            "field_count": len(rows),
            "source_field_count": 20,
            "note": "Chỉ trả 20 trường thu nhập ngân hàng đã chọn trong các kỳ yêu cầu; chưa tự tính điểm hoặc kết luận CIR."
        }), 200
    except Exception as e:
        log_exception(f"[API BCTC BANK INCOME] Lỗi đọc {symbol}", e)
        return jsonify({
            "status": "error",
            "message": "Không đọc được các trường BCTC đã chọn. Kiểm tra log backend."
        }), 500


@app.get("/api/admin/bctc-bank-loans")
def api_admin_bctc_bank_loans():
    """Return only CTG-style customer-loan fields for one period; never the full BCTC."""
    if not ADMIN_SECRET_KEY or request.headers.get("X-Admin-Key") != ADMIN_SECRET_KEY:
        return jsonify({"status": "error", "message": "Không có quyền truy cập."}), 403

    symbol = request.args.get("symbol", "").upper().strip()
    period = request.args.get("period", "").strip()
    if not re.fullmatch(r"[A-Z0-9]{2,10}", symbol):
        return jsonify({"status": "error", "message": "Mã cổ phiếu không hợp lệ."}), 400
    if period and not re.fullmatch(r"\d{4}-Q[1-4]", period):
        return jsonify({"status": "error", "message": "Kỳ BCTC không hợp lệ."}), 400

    try:
        pipeline = _build_bctc_selected_fields_pipeline(period or None)
        pipeline[0]["$match"] = {"symbol": symbol}
        doc = next(bctc_col.aggregate(pipeline, maxTimeMS=8000), None)
        if not doc:
            return jsonify({"status": "error", "message": f"Không tìm thấy BCTC của {symbol}."}), 404

        rows = doc.get("balance_sheet") or []
        return jsonify({
            "status": "success",
            "symbol": symbol,
            "source": doc.get("source"),
            "updated_at": doc.get("updated_at"),
            "latest_period": doc.get("latest_period"),
            "requested_period": period or doc.get("latest_period"),
            "fields": rows,
            "field_count": len(rows),
            "note": "Chỉ trả bsb103, bsb104, bsb105 của một kỳ; chưa tự kết luận dư nợ gộp/net.",
        }), 200
    except Exception as e:
        log_exception(f"[API BCTC BANK LOANS] Lỗi đọc {symbol}", e)
        return jsonify({
            "status": "error",
            "message": "Không đọc được các trường BCTC đã chọn. Kiểm tra log backend."
        }), 500


def _bank_issuer_mapping_verified(balance_quality, income_quality,
                                    current_period, prior_year_period, periods):
    """Require every selected loan and PPOP identity to reconcile."""
    loan_checks = [
        item for item in (balance_quality or {}).get("reconciliations", [])
        if item.get("id") == "bank_net_customer_loans"
    ]
    ppop_checks = [
        item for item in (income_quality or {}).get("reconciliations", [])
        if item.get("id") == "bank_ppop_equals_operating_income_plus_signed_operating_expense"
        and item.get("period") in set(periods)
    ]
    loan_periods_passed = {
        item.get("period") for item in loan_checks if item.get("status") == "pass"
    }
    ppop_periods_passed = {
        item.get("period") for item in ppop_checks if item.get("status") == "pass"
    }
    return (
        (balance_quality or {}).get("valid") is True
        and (income_quality or {}).get("valid") is True
        and loan_periods_passed == {current_period, prior_year_period}
        and ppop_periods_passed == set(periods)
        and len(loan_checks) == 2
        and len(ppop_checks) == 4
    )


def _ytd_support_period(periods):
    """Return the preceding YTD quarter needed to quarterize the first TTM period."""
    if not periods:
        return None
    parts = str(periods[0] or "").strip().upper().split("-Q")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    year, quarter = int(parts[0]), int(parts[1])
    if quarter < 1 or quarter > 4 or quarter == 1:
        return None
    return f"{year}-Q{quarter - 1}"


def _build_bctc_bank_metric_pipeline(periods, current_period, prior_year_period, include_ytd_support=False):
    """Read only verified bank metric source rows for selected periods."""
    balance_fields = [
        "bsb103", "bsb104", "bsb105", "bsb113",
        "bsa53", "bsa54", "bsa78", "bsa96",
    ]
    income_fields = ["isa20", "isb27", "isb38", "isb39", "isb40"]
    income_periods = list(periods)
    support_period = _ytd_support_period(periods) if include_ytd_support else None
    if support_period and support_period not in income_periods:
        income_periods.append(support_period)
    return [
        {"$match": {}},
        {"$project": {
            "_id": 0,
            "symbol": 1,
            "updated_at": 1,
            "latest_period": 1,
            "source": 1,
            "balance_sheet": {
                "$filter": {
                    "input": {"$ifNull": ["$balance_sheet", []]},
                    "as": "row",
                    "cond": {"$and": [
                        {"$in": ["$$row.source_field", balance_fields]},
                        {"$in": ["$$row.period", [current_period, prior_year_period]]},
                    ]},
                }
            },
            "income_statement": {
                "$filter": {
                    "input": {"$ifNull": ["$income_statement", []]},
                    "as": "row",
                    "cond": {"$and": [
                        {"$in": ["$$row.source_field", income_fields]},
                        {"$in": ["$$row.period", income_periods]},
                    ]},
                }
            },
        }},
        {"$limit": 1},
    ]


@app.get("/api/admin/bctc-bank-metrics")
def api_admin_bctc_bank_metrics():
    """Return calculated bank metrics and explicit gaps; does not assign a score."""
    if not ADMIN_SECRET_KEY or request.headers.get("X-Admin-Key") != ADMIN_SECRET_KEY:
        return jsonify({"status": "error", "message": "Không có quyền truy cập."}), 403

    symbol = request.args.get("symbol", "").upper().strip()
    periods_raw = request.args.get("periods", "")
    periods = [item.strip().upper() for item in periods_raw.split(",") if item.strip()]
    current_period = request.args.get("current_period", "").strip().upper()
    prior_year_period = request.args.get("prior_year_period", "").strip().upper()
    income_basis = request.args.get("income_period_basis", "").strip().upper()

    if not re.fullmatch(r"[A-Z0-9]{2,10}", symbol):
        return jsonify({"status": "error", "message": "Mã cổ phiếu không hợp lệ."}), 400
    sector = get_sub_sector(symbol)
    if sector != "NGAN_HANG":
        return jsonify({
            "status": "error",
            "message": "Endpoint này chỉ áp dụng cho hồ sơ ngân hàng.",
            "sector": sector,
        }), 422
    period_check = validate_ttm_periods(periods)
    current_q = parse_quarter(current_period)
    prior_q = parse_quarter(prior_year_period)
    if not period_check["valid"] or not current_q or not prior_q:
        return jsonify({"status": "error", "message": "Cần đúng 4 quý liên tiếp và kỳ hiện tại/kỳ so sánh hợp lệ."}), 400
    periods = period_check["periods"]
    if periods[-1] != current_period:
        return jsonify({"status": "error", "message": "Kỳ hiện tại phải là quý cuối trong chuỗi TTM."}), 400
    if current_q[1] != prior_q[1] or current_q[0] - prior_q[0] != 1:
        return jsonify({"status": "error", "message": "Kỳ so sánh phải cùng quý của năm trước."}), 400
    if income_basis not in ("QUARTERLY", "YTD"):
        return jsonify({"status": "error", "message": "Phải khai báo income_period_basis là QUARTERLY hoặc YTD."}), 400

    try:
        pipeline = _build_bctc_bank_metric_pipeline(
            periods, current_period, prior_year_period,
            include_ytd_support=(income_basis == "YTD"),
        )
        pipeline[0]["$match"] = {"symbol": symbol}
        doc = next(bctc_col.aggregate(pipeline, maxTimeMS=8000), None)
        if not doc:
            return jsonify({"status": "error", "message": f"Không tìm thấy BCTC của {symbol}."}), 404

        balance_rows = doc.get("balance_sheet") or []
        income_rows = doc.get("income_statement") or []
        balance_quality = validate_bctc_rows(
            balance_rows, "balance_sheet",
            sign_policies={
                "bsb103": "positive", "bsb104": "positive",
                "bsb105": "non_positive", "bsb113": "positive",
            },
            equations=get_vci_reconciliation_equations("balance_sheet"),
            period_basis="POINT_IN_TIME",
        )
        income_quality = validate_bctc_rows(
            income_rows, "income_statement",
            sign_policies={"isb38": "positive", "isb39": "non_positive"},
            equations=get_vci_reconciliation_equations("income_statement"),
            period_basis=income_basis,
        )
        issuer_verified = _bank_issuer_mapping_verified(
            balance_quality, income_quality,
            current_period, prior_year_period, periods,
        )
        metrics = build_bank_metric_bundle(
            balance_rows, income_rows, current_period, prior_year_period,
            periods, income_basis, issuer_verified,
        )
        if not balance_quality.get("valid"):
            metrics = quarantine_metrics(
                metrics, ["ROE", "ROA", "LDR", "CREDIT_GROWTH"],
                "balance_sheet_quality_validation_failed",
            )
        if not income_quality.get("valid"):
            metrics = quarantine_metrics(
                metrics, ["ROE", "ROA", "CIR", "BANK_PPOP_MARGIN",
                          "BANK_NON_INTEREST_INCOME_SHARE"],
                "income_statement_quality_validation_failed",
            )
        profile = get_industry_profile("NGAN_HANG")
        return jsonify({
            "status": "success",
            "symbol": symbol,
            "source": doc.get("source"),
            "updated_at": doc.get("updated_at"),
            "current_period": current_period,
            "prior_year_period": prior_year_period,
            "ttm_periods": periods,
            "income_period_basis": income_basis,
            "issuer_mapping_verified": issuer_verified,
            "quality": {
                "balance_sheet": {
                    "valid": balance_quality["valid"],
                    "errors": balance_quality["errors"],
                    "reconciliations": balance_quality["reconciliations"],
                },
                "income_statement": {
                    "valid": income_quality["valid"],
                    "errors": income_quality["errors"],
                    "reconciliations": income_quality["reconciliations"],
                },
            },
            "metrics": metrics,
            "scoring": {
                "profile_status": profile.get("status") if profile else "unknown",
                "enabled": bool(profile and profile.get("scoring_enabled")),
                "required_metric_groups": profile.get("metric_groups", {}) if profile else {},
                "metric_catalog": get_sector_metric_catalog("NGAN_HANG") or {},
                "metric_readiness_summary": summarize_sector_metric_readiness("NGAN_HANG"),
                "note": "Chưa chấm điểm: hồ sơ ngân hàng vẫn design_only và còn thiếu NPL/CASA/CAR.",
            },
        }), 200
    except Exception as e:
        log_exception(f"[API BANK METRICS] Lỗi đọc {symbol}", e)
        return jsonify({
            "status": "error",
            "message": "Không tính được metric ngân hàng từ BCTC đã chọn."
        }), 500


def _build_bctc_industrial_metric_pipeline(periods, current_period, prior_year_period, include_income_ytd_support=False, include_cashflow_ytd_support=False):
    """Read only the core non-financial metrics' BCTC source rows."""
    income_periods = list(periods)
    cashflow_periods = list(periods)
    support_period = _ytd_support_period(periods)
    if include_income_ytd_support and support_period:
        income_periods.append(support_period)
    if include_cashflow_ytd_support and support_period:
        cashflow_periods.append(support_period)
    return [
        {"$match": {}},
        {"$project": {
            "_id": 0,
            "symbol": 1,
            "updated_at": 1,
            "latest_period": 1,
            "source": 1,
            "balance_sheet": {
                "$filter": {
                    "input": {"$ifNull": ["$balance_sheet", []]},
                    "as": "row",
                    "cond": {"$and": [
                        {"$in": ["$$row.source_field", ["bsa53", "bsa54", "bsa78", "bsa96"]]},
                        {"$in": ["$$row.period", [current_period, prior_year_period]]},
                    ]},
                }
            },
            "income_statement": {
                "$filter": {
                    "input": {"$ifNull": ["$income_statement", []]},
                    "as": "row",
                    "cond": {"$and": [
                        {"$in": ["$$row.source_field", ["isa20"]]},
                        {"$in": ["$$row.period", income_periods]},
                    ]},
                }
            },
            "cash_flow": {
                "$filter": {
                    "input": {"$ifNull": ["$cash_flow", []]},
                    "as": "row",
                    "cond": {"$and": [
                        {"$in": ["$$row.source_field", ["cfa18", "cfa34", "cfa36", "cfa37", "cfa38"]]},
                        {"$in": ["$$row.period", cashflow_periods]},
                    ]},
                }
            },
        }},
        {"$limit": 1},
    ]


def _industrial_metric_endpoint_allowed(sector):
    """Banks must use the bank-specific diagnostic pipeline."""
    return str(sector or "").strip().upper() != "NGAN_HANG"


@app.get("/api/admin/bctc-industrial-metrics")
def api_admin_bctc_industrial_metrics():
    """Return core non-financial metrics and missing inputs; no sector score."""
    if not ADMIN_SECRET_KEY or request.headers.get("X-Admin-Key") != ADMIN_SECRET_KEY:
        return jsonify({"status": "error", "message": "Không có quyền truy cập."}), 403

    symbol = request.args.get("symbol", "").upper().strip()
    periods_raw = request.args.get("periods", "")
    periods = [item.strip().upper() for item in periods_raw.split(",") if item.strip()]
    current_period = request.args.get("current_period", "").strip().upper()
    prior_year_period = request.args.get("prior_year_period", "").strip().upper()
    income_basis = request.args.get("income_period_basis", "").strip().upper()
    cash_basis = request.args.get("cashflow_period_basis", "").strip().upper()

    if not re.fullmatch(r"[A-Z0-9]{2,10}", symbol):
        return jsonify({"status": "error", "message": "Mã cổ phiếu không hợp lệ."}), 400
    sector = get_sub_sector(symbol)
    if not _industrial_metric_endpoint_allowed(sector):
        return jsonify({
            "status": "error",
            "message": "Mã ngân hàng phải dùng endpoint chỉ tiêu ngân hàng.",
            "sector": sector,
        }), 422
    period_check = validate_ttm_periods(periods)
    current_q, prior_q = parse_quarter(current_period), parse_quarter(prior_year_period)
    if not period_check["valid"] or not current_q or not prior_q:
        return jsonify({"status": "error", "message": "Cần đúng 4 quý liên tiếp và kỳ so sánh hợp lệ."}), 400
    periods = period_check["periods"]
    if periods[-1] != current_period or current_q[1] != prior_q[1] or current_q[0] - prior_q[0] != 1:
        return jsonify({"status": "error", "message": "Kỳ hiện tại hoặc kỳ so sánh không khớp chuỗi TTM."}), 400
    if income_basis not in ("QUARTERLY", "YTD") or cash_basis not in ("QUARTERLY", "YTD"):
        return jsonify({"status": "error", "message": "Phải khai báo rõ basis của báo cáo KQKD và LCTT."}), 400

    try:
        pipeline = _build_bctc_industrial_metric_pipeline(
            periods, current_period, prior_year_period,
            include_income_ytd_support=(income_basis == "YTD"),
            include_cashflow_ytd_support=(cash_basis == "YTD"),
        )
        pipeline[0]["$match"] = {"symbol": symbol}
        doc = next(bctc_col.aggregate(pipeline, maxTimeMS=8000), None)
        if not doc:
            return jsonify({"status": "error", "message": f"Không tìm thấy BCTC của {symbol}."}), 404

        balance_rows = doc.get("balance_sheet") or []
        income_rows = doc.get("income_statement") or []
        cash_rows = doc.get("cash_flow") or []
        quality = {
            "balance_sheet": validate_bctc_rows(
                balance_rows, "balance_sheet",
                equations=get_vci_reconciliation_equations("balance_sheet"),
                period_basis="POINT_IN_TIME",
            ),
            "income_statement": validate_bctc_rows(
                income_rows, "income_statement",
                period_basis=income_basis,
            ),
            "cash_flow": validate_bctc_rows(
                cash_rows, "cash_flow",
                equations=get_vci_reconciliation_equations("cash_flow"),
                period_basis=cash_basis,
            ),
        }
        metrics = build_industrial_metric_bundle(
            balance_rows, income_rows, cash_rows,
            current_period, prior_year_period, periods,
            income_basis, cash_basis,
        )
        if not quality["balance_sheet"].get("valid"):
            metrics = quarantine_metrics(
                metrics, ["ROE", "ROA"],
                "balance_sheet_quality_validation_failed",
            )
        if not quality["income_statement"].get("valid"):
            metrics = quarantine_metrics(
                metrics, ["ROE", "ROA", "CASH_CONVERSION"],
                "income_statement_quality_validation_failed",
            )
        if not quality["cash_flow"].get("valid"):
            metrics = quarantine_metrics(
                metrics, ["CASH_CONVERSION", "CFO"],
                "cash_flow_quality_validation_failed",
            )
        for metric_id in list(metrics):
            if not metric_is_supported_for_sector(metric_id, sector):
                metrics = quarantine_metrics(
                    metrics, [metric_id], "metric_not_applicable_for_sector"
                )
        profile = get_industry_profile(sector)
        return jsonify({
            "status": "success",
            "symbol": symbol,
            "source": doc.get("source"),
            "updated_at": doc.get("updated_at"),
            "current_period": current_period,
            "prior_year_period": prior_year_period,
            "ttm_periods": periods,
            "income_period_basis": income_basis,
            "cashflow_period_basis": cash_basis,
            "quality": {
                name: {
                    "valid": result["valid"],
                    "scoreable": result["scoreable"],
                    "errors": result["errors"],
                    "warnings": result["warnings"],
                    "reconciliations": result.get("reconciliations", []),
                }
                for name, result in quality.items()
            },
            "metrics": metrics,
            "scoring": {
                "profile_id": profile.get("profile_id") if profile else None,
                "profile_status": profile.get("status") if profile else "unknown_profile",
                "enabled": bool(profile and profile.get("scoring_enabled")),
                "required_metric_groups": profile.get("metric_groups", {}) if profile else {},
                "metric_catalog": get_sector_metric_catalog(sector) or {},
                "metric_readiness_summary": summarize_sector_metric_readiness(sector),
                "note": "Chỉ tính các metric có nguồn đã xác minh; chưa tự chấm điểm khi hồ sơ ngành chưa được phê duyệt.",
            },
        }), 200
    except Exception as e:
        log_exception(f"[API INDUSTRIAL METRICS] Lỗi đọc {symbol}", e)
        return jsonify({
            "status": "error",
            "message": "Không tính được metric doanh nghiệp từ BCTC đã chọn."
        }), 500


def _build_bctc_industrial_discovery_pipeline(symbol):
    """Fetch one issuer's compact recent BCTC rows and metadata for discovery."""
    def compact_rows(section):
        return {
            "$map": {
                "input": {
                    "$slice": [
                        {"$ifNull": ["$" + section, []]},
                        -2500,
                    ]
                },
                "as": "row",
                "in": {
                    "source_field": "$$row.source_field",
                    "id": "$$row.id",
                    "name": "$$row.name",
                    "period": "$$row.period",
                    "value": "$$row.value",
                    "unit": "$$row.unit",
                },
            }
        }

    return [
        {"$match": {"symbol": str(symbol or "").upper().strip()}},
        {"$project": {
            "_id": 0,
            "symbol": 1,
            "updated_at": 1,
            "latest_period": 1,
            "source": 1,
            "vci_field_catalog": 1,
            "balance_sheet": compact_rows("balance_sheet"),
            "income_statement": compact_rows("income_statement"),
            "cash_flow": compact_rows("cash_flow"),
        }},
        {"$limit": 1},
    ]


@app.get("/api/admin/bctc-industrial-discovery")
def api_admin_bctc_industrial_discovery():
    """Discover industrial source-field candidates without assigning mappings."""
    if not ADMIN_SECRET_KEY or request.headers.get("X-Admin-Key") != ADMIN_SECRET_KEY:
        return jsonify({"status": "error", "message": "Không có quyền truy cập."}), 403

    symbol = request.args.get("symbol", "").upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{2,10}", symbol):
        return jsonify({"status": "error", "message": "Mã cổ phiếu không hợp lệ."}), 400
    sector = get_sub_sector(symbol)
    if not _industrial_metric_endpoint_allowed(sector):
        return jsonify({
            "status": "error",
            "message": "Mã ngân hàng phải dùng endpoint khám phá chỉ tiêu ngân hàng.",
            "sector": sector,
        }), 422

    try:
        pipeline = _build_bctc_industrial_discovery_pipeline(symbol)
        doc = next(bctc_col.aggregate(pipeline, maxTimeMS=8000), None)
        if not doc:
            return jsonify({
                "status": "error",
                "message": f"Không tìm thấy BCTC của {symbol}."
            }), 404

        rows_by_section = {
            "balance_sheet": doc.get("balance_sheet") or [],
            "income_statement": doc.get("income_statement") or [],
            "cash_flow": doc.get("cash_flow") or [],
        }
        numeric_discovery = annotate_industrial_dictionary_candidates(
            discover_industrial_fields(
                rows_by_section, max_periods=12, max_fields=150
            )
        )
        metadata_discovery = annotate_industrial_dictionary_candidates(
            discover_industrial_metadata(
                doc.get("vci_field_catalog") or {}, max_fields=300
            )
        )
        return jsonify({
            "status": "success",
            "symbol": symbol,
            "source": doc.get("source"),
            "updated_at": doc.get("updated_at"),
            "latest_period": doc.get("latest_period"),
            "discovery": numeric_discovery,
            "metadata_discovery": metadata_discovery,
            "note": (
                "Ứng viên chỉ dùng để truy tìm source_field và đối chiếu nhãn. "
                "Chưa xác nhận canonical mapping, chưa tính điểm và không tự thay thế dữ liệu thiếu."
            ),
        }), 200
    except Exception as e:
        log_exception(f"[API INDUSTRIAL DISCOVERY] Lỗi đọc {symbol}", e)
        return jsonify({
            "status": "error",
            "message": "Không đọc được dữ liệu khám phá trường BCTC doanh nghiệp."
        }), 500


def _bank_asset_quality_endpoint_allowed(sector):
    """Bank asset-quality discovery is only meaningful for bank issuers."""
    return str(sector or "").strip().upper() == "NGAN_HANG"


def _build_bctc_bank_asset_quality_pipeline(symbol):
    """Fetch compact recent balance-sheet rows and VCI metadata for one bank."""
    compact_rows = {
        "$map": {
            "input": {
                "$slice": [
                    {"$ifNull": ["$balance_sheet", []]},
                    -2500,
                ]
            },
            "as": "row",
            "in": {
                "source_field": "$$row.source_field",
                "id": "$$row.id",
                "name": "$$row.name",
                "period": "$$row.period",
                "value": "$$row.value",
                "unit": "$$row.unit",
            },
        }
    }
    return [
        {"$match": {"symbol": str(symbol or "").upper().strip()}},
        {"$project": {
            "_id": 0,
            "symbol": 1,
            "updated_at": 1,
            "latest_period": 1,
            "source": 1,
            "vci_field_catalog": 1,
            "balance_sheet": compact_rows,
        }},
        {"$limit": 1},
    ]


@app.get("/api/admin/bctc-bank-asset-quality")
def api_admin_bctc_bank_asset_quality():
    """Return only explicit NPL/group-2/demand-deposit label candidates for one bank."""
    if not ADMIN_SECRET_KEY or request.headers.get("X-Admin-Key") != ADMIN_SECRET_KEY:
        return jsonify({"status": "error", "message": "Không có quyền truy cập."}), 403

    symbol = request.args.get("symbol", "").upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{2,10}", symbol):
        return jsonify({"status": "error", "message": "Mã cổ phiếu không hợp lệ."}), 400
    sector = get_sub_sector(symbol)
    if not _bank_asset_quality_endpoint_allowed(sector):
        return jsonify({
            "status": "error",
            "message": "Chỉ áp dụng khám phá chất lượng tài sản cho mã ngân hàng.",
            "sector": sector,
        }), 422


    try:
        pipeline = _build_bctc_bank_asset_quality_pipeline(symbol)
        doc = next(bctc_col.aggregate(pipeline, maxTimeMS=8000), None)
        if not doc:
            return jsonify({
                "status": "error",
                "message": f"Không tìm thấy BCTC của {symbol}."
            }), 404

        rows = doc.get("balance_sheet") or []
        discovery = discover_bank_asset_quality_fields(rows, max_periods=12, max_fields=30)
        reference_fields = [
            {
                "source_field": row.get("source_field"),
                "id": row.get("id"),
                "name": row.get("name"),
                "unit": row.get("unit"),
                "period": row.get("period"),
                "value": row.get("value"),
            }
            for row in rows
            if isinstance(row, dict)
            and str(row.get("source_field") or "").lower() in {"bsb104", "bsb105", "bsb113"}
        ]
        reference_fields.sort(
            key=lambda row: (str(row.get("source_field")), str(row.get("period")),)
        )
        return jsonify({
            "status": "success",
            "symbol": symbol,
            "source": doc.get("source"),
            "updated_at": doc.get("updated_at"),
            "latest_period": doc.get("latest_period"),
            "discovery": discovery,
            "metadata_discovery": discover_bank_asset_quality_metadata(
                doc.get("vci_field_catalog") or {}
            ),
            "reference_fields": reference_fields[-36:],
            "note": (
                "Các trường ứng viên chỉ phục vụ truy tìm source_field. "
                "Chưa xác nhận mapping, chưa tính NPL/CASA và không dùng để chấm điểm."
            ),
        }), 200
    except Exception as e:
        log_exception(f"[API BANK ASSET QUALITY] Lỗi đọc {symbol}", e)
        return jsonify({
            "status": "error",
            "message": "Không đọc được dữ liệu chẩn đoán BCTC ngân hàng."
        }), 500


@app.get("/api/pause-crawl")
def pause_crawl():
    global is_auto_crawl_paused, stop_crawl_requested
    with crawl_control_lock:
        is_auto_crawl_paused = True
        stop_crawl_requested = True
    print("\n[API] ⏸️ Đã nhận lệnh TẠM DỪNG. Worker đang chạy sẽ dừng ở điểm kiểm tra kế tiếp; không khởi chạy lượt tự động mới.", flush=True)
    return jsonify({
        "status": "success",
        "is_paused": True,
        "is_crawling": is_crawling,
        "message": "Đã tạm dừng cào tự động. Lượt đang chạy (nếu có) sẽ dừng trước mã tiếp theo."
    })


@app.get("/api/resume-crawl")
def resume_crawl():
    global is_auto_crawl_paused, stop_crawl_requested, holiday_status, holiday_checked_today, is_holiday_paused
    with crawl_control_lock:
        is_auto_crawl_paused = False
        stop_crawl_requested = False
    
    with holiday_check_lock:
        if holiday_status == True:
            print(f"\n[API] ⚠️ User override: Reset trạng thái ngày lễ. Hệ thống sẽ crawl lại.", flush=True)
            holiday_status = None
            holiday_checked_today = None
    
    if is_holiday_paused:
        print(f"\n[API] ⚠️ Reset is_holiday_paused (đang trong chế độ nghỉ lễ tự động).", flush=True)
        is_holiday_paused = False
    
    print("\n[API] ▶️ Đã TIẾP TỤC tiến trình cào tự động!", flush=True)
    return jsonify({"status": "success", "message": "Đã tiếp tục cào tự động (đã reset trạng thái ngày lễ nếu có)."})


@app.get("/api/holiday-status")
def api_holiday_status():
    with holiday_check_lock:
        return jsonify({
            "status": "success",
            "data": {
                "holiday_checked_today": holiday_checked_today,
                "holiday_status": holiday_status,
                "holiday_detected_date": holiday_detected_date,
                "is_auto_crawl_paused": is_auto_crawl_paused,
                "is_holiday_paused": is_holiday_paused,
                "current_time_vn": _now_str()
            }
        })


@app.post("/api/set-fhsc-key")
def api_set_fhsc_key():
    print("[API] 🔑 Gọi API /api/set-fhsc-key", flush=True)
    data = request.json
    new_key = data.get("key", "").strip()
    if not new_key: return jsonify({"status": "error", "message": "Key không được để trống"}), 400
    settings_col.update_one({"_id": "fhsc_key"}, {"$set": {"value": new_key, "updated_at": _now_str()}}, upsert=True)
    return jsonify({"status": "success", "message": "Đã lưu API Key mới thành công!"})


@app.get("/api/data")
def api_get_data():
    print("[API] 📈 Gọi API /api/data (Lấy toàn bộ giá)", flush=True)
    try:
        cursor = collection.find({}, {"_id": 0})
        data = {}
        for doc in cursor:
            sym = doc.get("symbol")
            if sym:
                if "prev_close" not in doc:
                    doc["prev_close"] = 0
                
                if "price_change_percent" not in doc or doc["price_change_percent"] == 0:
                    close_price = doc.get("close", 0)
                    prev_close = doc.get("prev_close", 0)
                    pct = 0.0
                    avail = False
                    arrow, color = "—", "slate"
                    if close_price > 0 and prev_close > 0:
                        raw_pct = ((close_price - prev_close) / prev_close) * 100
                        if -30.0 <= raw_pct <= 30.0:
                            pct = round(raw_pct, 2)
                            avail = True
                            if pct > 0: arrow, color = "▲", "emerald"
                            elif pct < 0: arrow, color = "▼", "rose"
                    doc["price_change_percent"] = pct
                    doc["price_change_available"] = avail
                    doc["price_arrow"] = arrow
                    doc["price_color"] = color
                
                doc["volume_percent"] = doc.get("volume_percent", 0.0)
                data[sym] = doc
        return jsonify({"status": "success", "count": len(data), "data": data})
    except Exception as e: 
        return jsonify({"status": "error", "message": str(e)}), 500


@app.get("/api/sync")
def api_sync():
    raw = request.args.get("symbols", "").strip()
    print(f"[API] 🔄 Gọi API /api/sync | Mã: {raw}", flush=True)
    if not raw: abort(400)
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    
    if is_crawling or _is_after_15h():
        updated_data = {}
        for sym in symbols:
            doc = collection.find_one({"symbol": sym})
            if doc and "close" in doc: 
                close_price = doc.get("close", 0)
                prev_close = doc.get("prev_close", 0)
                pct = 0.0
                avail = False
                arrow, color = "—", "slate"
                if close_price > 0 and prev_close > 0:
                    raw_pct = ((close_price - prev_close) / prev_close) * 100
                    if -30.0 <= raw_pct <= 30.0:
                        pct = round(raw_pct, 2)
                        avail = True
                        if pct > 0: arrow, color = "▲", "emerald"
                        elif pct < 0: arrow, color = "▼", "rose"

                updated_data[sym] = {
                    "close": close_price, "close_price": close_price,
                    "prev_close": prev_close, "price_change_percent": pct,
                    "price_change_available": avail, "price_arrow": arrow,
                    "price_color": color, "updated_at": doc.get("updated_at", _now_str())
                }
        return jsonify({
            "status": "success", 
            "updated_at": _now_str() + " (Lấy từ DB)", 
            "updated_data": updated_data
        })

    threading.Thread(target=_execute_crawl, args=(symbols, False, True)).start()
    return jsonify({
        "status": "success", 
        "updated_at": _now_str() + " (Bắt đầu cào)", 
        "updated_data": {}
    })


@app.get("/api/admin/sector-scoring-readiness")
def api_admin_sector_scoring_readiness():
    """Admin-only matrix of scorer routing, profile status and verified metric coverage."""
    if not ADMIN_SECRET_KEY or request.headers.get("X-Admin-Key") != ADMIN_SECRET_KEY:
        return jsonify({"status": "error", "message": "Không có quyền truy cập."}), 403
    try:
        report = summarize_all_sector_scoring_readiness()
        profile_validation = validate_industry_profiles()
        catalog_validation = validate_metric_catalog()
        formula_readiness = assess_all_sector_formula_readiness()
        return jsonify({
            "status": "success",
            "scoring_readiness": report,
            "formula_readiness": formula_readiness,
            "profile_registry_valid": profile_validation["valid"],
            "profile_registry_errors": profile_validation["errors"],
            "metric_catalog_valid": catalog_validation["valid"],
            "metric_catalog_errors": catalog_validation["errors"],
            "production_scoring_enabled": False,
            "note": (
                "Ma trận cho biết nhánh chấm điểm legacy và mức bao phủ nguồn đã xác minh. "
                "Không đồng nghĩa profile mới đã được phê duyệt để chấm điểm production."
            ),
        }), 200
    except Exception as e:
        log_exception("[API SECTOR SCORING READINESS]", e)
        return jsonify({
            "status": "error",
            "message": "Không tạo được ma trận mức độ sẵn sàng chấm điểm ngành."
        }), 500


def _build_screener_diagnostics_pipeline():
    """Compact, read-only aggregate for validating persisted Screener results."""
    return [
        {
            "$group": {
                "_id": {"$ifNull": ["$sub_sector", "UNCLASSIFIED"]},
                "symbol_count": {"$sum": 1},
                "avg_fundamental_coverage_pct": {
                    "$avg": "$fundamental_scorecard.coverage_pct"
                },
                "complete_scorecard_count": {
                    "$sum": {"$cond": [
                        {"$eq": ["$fundamental_scorecard.status", "complete"]}, 1, 0
                    ]}
                },
                "partial_scorecard_count": {
                    "$sum": {"$cond": [
                        {"$eq": ["$fundamental_scorecard.status", "partial"]}, 1, 0
                    ]}
                },
                "insufficient_scorecard_count": {
                    "$sum": {"$cond": [
                        {"$eq": ["$fundamental_scorecard.status", "insufficient_data"]}, 1, 0
                    ]}
                },
                "current_bctc_count": {
                    "$sum": {"$cond": [{"$eq": ["$bctc_period_is_current", True]}, 1, 0]}
                },
                "stale_bctc_count": {
                    "$sum": {"$cond": [{"$eq": ["$bctc_period_is_current", False]}, 1, 0]}
                },
                "unverified_bctc_count": {
                    "$sum": {"$cond": [
                        {"$in": [{"$type": "$bctc_period_is_current"}, ["missing", "null"]]},
                        1, 0
                    ]}
                },
            }
        },
        {"$sort": {"_id": 1}},
    ]


@app.get("/api/version")
def api_version():
    """Expose a non-secret release fingerprint and scoring mode for deployment checks."""
    try:
        readiness = summarize_all_sector_scoring_readiness()
        return jsonify({
            "status": "success",
            "service": "quant-price-api",
            "release": os.getenv("RENDER_GIT_COMMIT") or os.getenv("GITHUB_SHA") or "unknown",
            "engines": {
                "scoring": SCORING_ENGINE_VERSION,
                "valuation": VALUATION_ENGINE_VERSION,
                "decision": DECISION_ENGINE_VERSION,
                "report": REPORT_TEMPLATE_VERSION,
            },
            "sector_profile_count": readiness.get("profile_count", 0),
            "legacy_sector_path_count": readiness.get("legacy_path_count", 0),
            "new_profile_engine_enabled": False,
            "production_score_ready_profile_count": readiness.get(
                "production_score_ready_count", 0
            ),
            "scoring_mode": "legacy_deterministic_with_sector_blueprint_metadata",
        }), 200
    except Exception as e:
        log_exception("[API VERSION]", e)
        return jsonify({"status": "error", "message": "Không đọc được thông tin phiên bản."}), 500


@app.get("/api/screener/diagnostics")
def api_screener_diagnostics():
    """Compact production diagnostics; does not expose raw BCTC or issuer data."""
    try:
        rows = list(screener_col.aggregate(
            _build_screener_diagnostics_pipeline(),
            allowDiskUse=False,
            maxTimeMS=5000,
        ))
        by_sector = []
        total_symbols = 0
        total_current_bctc = 0
        total_stale_bctc = 0
        total_unverified_bctc = 0
        for row in rows:
            item = {
                "sector": row.get("_id") or "UNCLASSIFIED",
                "symbol_count": int(row.get("symbol_count") or 0),
                "avg_fundamental_coverage_pct": (
                    round(float(row["avg_fundamental_coverage_pct"]), 1)
                    if row.get("avg_fundamental_coverage_pct") is not None else None
                ),
                "complete_scorecard_count": int(row.get("complete_scorecard_count") or 0),
                "partial_scorecard_count": int(row.get("partial_scorecard_count") or 0),
                "insufficient_scorecard_count": int(row.get("insufficient_scorecard_count") or 0),
                "current_bctc_count": int(row.get("current_bctc_count") or 0),
                "stale_bctc_count": int(row.get("stale_bctc_count") or 0),
                "unverified_bctc_count": int(row.get("unverified_bctc_count") or 0),
            }
            by_sector.append(item)
            total_symbols += item["symbol_count"]
            total_current_bctc += item["current_bctc_count"]
            total_stale_bctc += item["stale_bctc_count"]
            total_unverified_bctc += item["unverified_bctc_count"]
        return jsonify({
            "status": "success" if total_symbols else "empty",
            "scoring_mode": "legacy_deterministic_with_sector_blueprint_metadata",
            "new_profile_engine_enabled": False,
            "total_symbols": total_symbols,
            "current_bctc_count": total_current_bctc,
            "stale_bctc_count": total_stale_bctc,
            "unverified_bctc_count": total_unverified_bctc,
            "sector_count": len(by_sector),
            "by_sector": by_sector,
            "note": (
                "Số liệu tổng hợp từ kết quả đã lưu. Profile ngành mới vẫn ở trạng thái "
                "design_only; không đồng nhất scorecard legacy với engine mới đã phê duyệt."
            ),
        }), 200
    except Exception as e:
        log_exception("[API SCREENER DIAGNOSTICS]", e)
        return jsonify({
            "status": "error",
            "message": "Không tạo được báo cáo kiểm tra Screener."
        }), 500


# FIX BUG #10: /api/screener trả 503 khi đang trong quá trình ghi, thay vì [] gây frontend crash

@app.get("/api/bctc/live-validation")
def api_bctc_live_validation():
    """Read-only validation of real BCTC documents against the extraction pipeline.

    Returns aggregate counts only; raw statements and issuer-level financial values
    are never exposed. This endpoint does not write to MongoDB or run production scoring.
    """
    try:
        try:
            requested_limit = int(request.args.get("limit", "100"))
        except (TypeError, ValueError):
            requested_limit = 100
        limit = max(1, min(requested_limit, 250))
        expected_period = _expected_latest_bctc_period()

        symbol_docs = list(
            collection.find({}, {"_id": 0, "symbol": 1})
            .sort("symbol", 1)
            .limit(limit)
        )
        symbols = [
            str(item.get("symbol") or "").strip().upper()
            for item in symbol_docs
            if item.get("symbol")
        ]
        bctc_docs = {}
        if symbols:
            cursor = bctc_col.find(
                {"symbol": {"$in": symbols}},
                {
                    "_id": 0, "symbol": 1, "source": 1,
                    "income_statement": 1, "cash_flow": 1, "balance_sheet": 1,
                },
            ).batch_size(25)
            for item in cursor:
                symbol = str(item.get("symbol") or "").strip().upper()
                if symbol:
                    bctc_docs[symbol] = item

        by_profile = {
            profile_id: {
                "symbol_count": 0,
                "bctc_document_count": 0,
                "missing_bctc_document_count": 0,
                "valid_income_document_count": 0,
                "current_period_count": 0,
                "stale_period_count": 0,
                "unverified_period_count": 0,
                "income_ttm_complete_count": 0,
                "cfo_ttm_complete_count": 0,
                "latest_npat_available_count": 0,
                "latest_revenue_available_count": 0,
                "gross_margin_available_count": 0,
                "source_counts": {},
                "latest_period_counts": {},
            }
            for profile_id in INDUSTRY_PROFILES
        }
        by_profile["UNCLASSIFIED"] = {
            "symbol_count": 0,
            "bctc_document_count": 0,
            "missing_bctc_document_count": 0,
            "valid_income_document_count": 0,
            "current_period_count": 0,
            "stale_period_count": 0,
            "unverified_period_count": 0,
            "income_ttm_complete_count": 0,
            "cfo_ttm_complete_count": 0,
            "latest_npat_available_count": 0,
            "latest_revenue_available_count": 0,
            "gross_margin_available_count": 0,
            "source_counts": {},
            "latest_period_counts": {},
        }

        totals = {
            "symbols_scanned": len(symbols),
            "bctc_document_count": 0,
            "missing_bctc_document_count": 0,
            "valid_income_document_count": 0,
            "current_period_count": 0,
            "stale_period_count": 0,
            "unverified_period_count": 0,
            "income_ttm_complete_count": 0,
            "cfo_ttm_complete_count": 0,
            "latest_npat_available_count": 0,
            "latest_revenue_available_count": 0,
            "gross_margin_available_count": 0,
        }

        for symbol in symbols:
            profile_id = normalize_profile_id(get_sub_sector(symbol)) or "UNCLASSIFIED"
            if profile_id not in by_profile:
                profile_id = "UNCLASSIFIED"
            row = by_profile[profile_id]
            row["symbol_count"] += 1
            bctc = bctc_docs.get(symbol)
            if not bctc:
                row["missing_bctc_document_count"] += 1
                totals["missing_bctc_document_count"] += 1
                continue

            row["bctc_document_count"] += 1
            totals["bctc_document_count"] += 1
            source_name = str(bctc.get("source") or "UNKNOWN").strip().upper()[:40]
            row["source_counts"][source_name] = row["source_counts"].get(source_name, 0) + 1

            income_rows = bctc.get("income_statement") or []
            cashflow_rows = bctc.get("cash_flow") or []
            balance_rows = bctc.get("balance_sheet") or []
            valid_periods = _valid_income_statement_periods(income_rows)
            metrics = extract_metrics_from_bctc_data(bctc)
            latest_period = metrics.get("bctc_latest_income_period") or ""
            row["latest_period_counts"][latest_period or "MISSING"] = (
                row["latest_period_counts"].get(latest_period or "MISSING", 0) + 1
            )

            if valid_periods:
                row["valid_income_document_count"] += 1
                totals["valid_income_document_count"] += 1
            if latest_period:
                if _bctc_period_is_current(latest_period, expected_period):
                    row["current_period_count"] += 1
                    totals["current_period_count"] += 1
                else:
                    row["stale_period_count"] += 1
                    totals["stale_period_count"] += 1
            else:
                row["unverified_period_count"] += 1
                totals["unverified_period_count"] += 1

            checks = {
                "income_ttm_complete_count": metrics.get("bctc_income_ttm_complete") is True,
                "cfo_ttm_complete_count": metrics.get("bctc_cfo_ttm_complete") is True,
                "latest_npat_available_count": (
                    metrics.get("net_profit_latest_quarter") is not None
                    and bool(latest_period)
                ),
                "latest_revenue_available_count": (
                    metrics.get("revenue_latest_quarter") is not None
                    and bool(latest_period)
                ),
                "gross_margin_available_count": metrics.get("gross_margin_available") is True,
            }
            for key, passed in checks.items():
                if passed:
                    row[key] += 1
                    totals[key] += 1

        for row in by_profile.values():
            row["bctc_coverage_pct"] = round(
                row["bctc_document_count"] / row["symbol_count"] * 100, 1
            ) if row["symbol_count"] else None
            row["current_period_pct_of_bctc"] = round(
                row["current_period_count"] / row["bctc_document_count"] * 100, 1
            ) if row["bctc_document_count"] else None

        return jsonify({
            "status": "success",
            "mode": "read_only_live_bctc_validation",
            "expected_latest_period": expected_period,
            "limit": limit,
            "truncated": collection.count_documents({}) > len(symbols),
            "totals": totals,
            "profile_count_with_symbols": sum(
                1 for key, value in by_profile.items()
                if key != "UNCLASSIFIED" and value["symbol_count"] > 0
            ),
            "by_profile": {
                key: value for key, value in sorted(by_profile.items())
                if value["symbol_count"] > 0
            },
            "writes_performed": False,
            "production_scoring_executed": False,
            "note": (
                "Dữ liệu đọc trực tiếp từ MongoDB bctc_data; chỉ trả số lượng và trạng thái "
                "kiểm tra, không trả BCTC thô, không ghi dữ liệu và không chạy chấm điểm production."
            ),
        }), 200
    except Exception as e:
        log_exception("[API BCTC LIVE VALIDATION]", e)
        return jsonify({
            "status": "error",
            "message": "Không kiểm tra được dữ liệu BCTC thực tế.",
        }), 500


@app.get("/api/screener")
def api_get_screener():
    print("[API] 🏆 Gọi API /api/screener (Lấy điểm số)", flush=True)
    acquired = screener_lock.acquire(timeout=5)
    if not acquired:
        return jsonify({
            "status": "retry",
            "message": "Screener đang cập nhật, vui lòng thử lại sau 2s",
            "data": []
        }), 503
    try:
        # Luôn đọc trong lock: chỉ kiểm tra locked() trước đó sẽ có race
        # nếu writer bắt đầu ngay sau lần kiểm tra.
        data = list(screener_col.find({}, {"_id": 0}))
        if not data:
            return jsonify({
                "status": "retry",
                "message": "Screener chưa có dữ liệu hoặc đang cập nhật",
                "data": []
            }), 503
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        log_exception("[API SCREENER]", e)
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        screener_lock.release()

@app.get("/api/ai-analyze")
def api_ai_analyze():
    symbol = request.args.get("symbol", "").upper().strip()
    print(f"[API] 🤖 Gọi API /api/ai-analyze | Mã: {symbol}", flush=True)
    if not symbol: 
        return jsonify({"status": "error", "message": "Thiếu mã"}), 400
        
    try:
        sm = collection.find_one({"symbol": symbol}, {"_id": 0})
        si = screener_col.find_one({"symbol": symbol}, {"_id": 0})
        bctc_doc = bctc_col.find_one({"symbol": symbol}, {"_id": 0})
        
        if not sm or not si: 
            return jsonify({"status": "error", "message": "Chưa đủ dữ liệu"}), 404
        
        ai_data = {
            "bctc": sm.get("ai_bctc", ""),
            "catalyst": sm.get("ai_catalyst", ""),
            "risks": sm.get("ai_risks", ""),
            "outlook": sm.get("ai_outlook", ""),
            "recommendation": sm.get("ai_recommendation", ""),
            "price_zone": sm.get("ai_price_zone", ""),
            "macro_view": sm.get("ai_macro_view", ""),
            "valuation_verdict": sm.get("valuation_verdict", "CHƯA TÍNH"),
            "ai_updated_at": sm.get("ai_updated_at", "Chưa phân tích"),
            "_source": "DATABASE"
        }

        bctc_warning = ""
        bctc_period = "Không rõ"
        bctc_updated_at = "Chưa cào"
        if bctc_doc:
            bctc_updated_at = bctc_doc.get("updated_at", "Chưa cào")
            bctc_period = bctc_doc.get("latest_period", "Không rõ")
            try:
                last_update_date = datetime.strptime(bctc_updated_at, "%d/%m/%Y %H:%M:%S")
                if (datetime.now(timezone.utc) - last_update_date.replace(tzinfo=timezone.utc)).days > 45:
                    bctc_warning = f"⚠️ CẢNH BÁO: BCTC đã cũ, kỳ mới nhất là {bctc_period}, cào lúc {bctc_updated_at}."
            except:
                pass
        
        if not ai_data["bctc"] or not ai_data["outlook"]:
            return jsonify({
                "status": "warning", 
                "message": "Mã này chưa có phân tích AI. Vui lòng đợi hệ thống chạy ngầm hoặc liên hệ Admin!",
                "data": None
            }), 200

        return jsonify({
            "status": "success", 
            "data": ai_data, 
            "bctc_period": bctc_period,
            "bctc_updated_at": bctc_updated_at,
            "warning": bctc_warning
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.get("/api/admin/force-ai-single")
def admin_force_ai_single():
    if not ADMIN_SECRET_KEY or request.headers.get("X-Admin-Key") != ADMIN_SECRET_KEY:
        return jsonify({"status": "error", "message": "Sai mật khẩu Admin!"}), 403
        
    global is_ai_running
    
    symbol = request.args.get("symbol", "").upper().strip()
    
    if not symbol:
        return jsonify({"status": "error", "message": "Vui lòng truyền tham số ?symbol=XXX"}), 400
        
    if is_ai_running:
        return jsonify({"status": "error", "message": "Hệ thống đang chạy AI hàng loạt, vui lòng đợi!"}), 400
    
    if not ALIBABA_API_KEY:
        return jsonify({"status": "error", "message": "Thiếu ALIBABA_API_KEY trong biến môi trường!"}), 500
    
    sm = collection.find_one({"symbol": symbol}, {"_id": 0})
    si = screener_col.find_one({"symbol": symbol}, {"_id": 0})
    
    if not sm or not si:
        return jsonify({
            "status": "error", 
            "message": f"Mã {symbol} chưa đủ dữ liệu nền. Hãy chạy /api/force-screener trước."
        }), 404

    is_ai_running = True
    print(f"\n[ADMIN-AI-SINGLE] 🚀 Bắt đầu chạy AI cho mã {symbol}...", flush=True)
    
    def _run():
        global is_ai_running
        try:
            sector = si.get("sector", "SAN_XUAT_TIEU_DUNG_PHONG_THU")
            # Fair Value nằm trong screener_col; ghép với dữ liệu gốc trước khi gọi AI.
            sm_for_ai = {**sm, **si}
            print(
                f"[AI-DEBUG] {symbol} | "
                f"collection low={sm.get('fair_value_low')!r}, high={sm.get('fair_value_high')!r} | "
                f"screener low={si.get('fair_value_low')!r}, high={si.get('fair_value_high')!r} | "
                f"merged low={sm_for_ai.get('fair_value_low')!r}, high={sm_for_ai.get('fair_value_high')!r}",
                flush=True
            )
            ai_result = _call_ai_for_symbol(symbol, sm_for_ai, si, sector)
            
            if ai_result:
                collection.update_one({"symbol": symbol}, {"$set": {
                    "ai_bctc": ai_result.get("bctc", ""),
                    "ai_catalyst": ai_result.get("catalyst", ""),
                    "ai_risks": ai_result.get("risks", ""),
                    "ai_outlook": ai_result.get("outlook", ""),
                    "ai_recommendation": ai_result.get("recommendation", ""),
                    "ai_price_zone": ai_result.get("price_zone", ""),
                    "ai_macro_view": ai_result.get("macro_view", ""),
                    "ai_updated_at": _now_str(),
                    "ai_source": "AI"
                }})
                
                screener_col.update_one({"symbol": symbol}, {"$set": {
                    "bctc_evaluation": ai_result.get("bctc", ""),
                    "outlook": ai_result.get("outlook", ""),
                    "ai_recommendation": ai_result.get("recommendation", ""),
                    "ai_price_zone": ai_result.get("price_zone", ""),
                    "macro_view": ai_result.get("macro_view", ""),
                    "ai_updated_at": _now_str(),
                    "ai_source": "AI"
                }})
                
                print(f"[ADMIN-AI-SINGLE] ✅ {symbol} - Đã lưu phân tích AI thành công vào cả 2 bảng!", flush=True)
            else:
                print(f"[ADMIN-AI-SINGLE] ❌ {symbol} - Gọi AI thất bại.", flush=True)
        finally:
            is_ai_running = False

    threading.Thread(target=_run, daemon=True).start()
    
    return jsonify({
        "status": "success", 
        "message": f"Đã bắt đầu chạy AI cho mã {symbol}. Theo dõi log Render!"
    })


@app.get("/api/admin/force-ai")
def admin_force_ai():
    global is_ai_running
    if is_ai_running:
        return jsonify({"status": "error", "message": "Đang chạy AI cho toàn bộ mã, vui lòng đợi!"}), 400
    
    if not ALIBABA_API_KEY:
        return jsonify({"status": "error", "message": "Thiếu ALIBABA_API_KEY!"}), 500
    
    all_symbols = [doc["symbol"] for doc in collection.find({}, {"symbol": 1})]
    
    is_ai_running = True
    print(f"\n[ADMIN-AI] 🚀 Bắt đầu chạy AI cho toàn bộ {len(all_symbols)} mã...", flush=True)
    
    def _run():
        global is_ai_running
        try:
            total = len(all_symbols)
            count = 0
            
            for i, sym in enumerate(all_symbols, 1):
                sm = collection.find_one({"symbol": sym}, {"_id": 0})
                si = screener_col.find_one({"symbol": sym}, {"_id": 0})
                
                if not sm or not si:
                    print(f"[ADMIN-AI] [{i}/{total}] ⚠️ {sym} - Thiếu dữ liệu, bỏ qua.", flush=True)
                    continue
                
                sector = si.get("sector", "SAN_XUAT_TIEU_DUNG_PHONG_THU")
                # Fair Value nằm trong screener_col; ghép với dữ liệu gốc trước khi gọi AI.
                sm_for_ai = {**sm, **si}
                print(
                    f"[AI-DEBUG] {sym} | "
                    f"collection low={sm.get('fair_value_low')!r}, high={sm.get('fair_value_high')!r} | "
                    f"screener low={si.get('fair_value_low')!r}, high={si.get('fair_value_high')!r} | "
                    f"merged low={sm_for_ai.get('fair_value_low')!r}, high={sm_for_ai.get('fair_value_high')!r}",
                    flush=True
                )
                ai_result = _call_ai_for_symbol(sym, sm_for_ai, si, sector)
                
                if ai_result:
                    collection.update_one({"symbol": sym}, {"$set": {
                        "ai_bctc": ai_result.get("bctc", ""),
                        "ai_catalyst": ai_result.get("catalyst", ""),
                        "ai_risks": ai_result.get("risks", ""),
                        "ai_outlook": ai_result.get("outlook", ""),
                        "ai_recommendation": ai_result.get("recommendation", ""),
                        "ai_price_zone": ai_result.get("price_zone", ""),
                        "ai_macro_view": ai_result.get("macro_view", ""),
                        "ai_updated_at": _now_str()
                    }})
                    count += 1
                    print(f"[ADMIN-AI] [{i}/{total}] ✅ {sym} - OK", flush=True)
                else:
                    print(f"[ADMIN-AI] [{i}/{total}] ❌ {sym} - Lỗi", flush=True)
                
                time.sleep(3.0)
            
            print(f"[ADMIN-AI] 🏁 Hoàn thành: {count}/{total} mã.", flush=True)
            
            gh_data, _ = get_github_file_data(SCREENER_JSON_FILE)
            if gh_data:
                for item in gh_data:
                    sym = item.get("symbol")
                    if sym:
                        stock_doc = collection.find_one({"symbol": sym}, {"ai_bctc": 1, "ai_outlook": 1, "ai_recommendation": 1, "ai_price_zone": 1, "ai_macro_view": 1, "ai_updated_at": 1})
                        if stock_doc:
                            item.update({
                                "bctc": stock_doc.get("ai_bctc", ""),
                                "outlook": stock_doc.get("ai_outlook", ""),
                                "ai_recommendation": stock_doc.get("ai_recommendation", ""),
                                "ai_price_zone": stock_doc.get("ai_price_zone", ""),
                                "macro_view": stock_doc.get("ai_macro_view", ""),
                                "ai_updated_at": stock_doc.get("ai_updated_at", "")
                            })
                commit_multiple_files_to_github({SCREENER_JSON_FILE: gh_data}, f"Update AI toàn bộ {_now_str()}")
                print(f"[ADMIN-AI] 🚀 Đã đồng bộ AI lên Github.", flush=True)
        finally:
            is_ai_running = False
    
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({
        "status": "success", 
        "message": f"Đã bắt đầu chạy AI cho {len(all_symbols)} mã. Xem log Render!"
    })


@app.get("/api/force-history")
def force_history():
    print(f"\n[API] 📥 Gọi API /api/force-history (Ép cào nến 30 ngày)", flush=True)
    global is_full_data_running
    if is_full_data_running: return jsonify({"status": "error", "message": "Đang chạy"}), 400
    is_full_data_running = True
    def _run():
        global is_full_data_running
        try:
            target = [doc["symbol"] for doc in collection.find({}, {"symbol": 1})]
            end, start = datetime.now().strftime("%Y-%m-%d"), (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            print(f"[HISTORY] 🚀 Bắt đầu cào nến 30 ngày cho {len(target)} mã...", flush=True)
            for sym in target:
                df = _fetch_history_ssi(sym, start, end)
                if df is not None and not df.empty:
                    bars = [{"time": r['time'].strftime('%Y-%m-%d') if hasattr(r['time'], 'strftime') else str(r['time'])[:10], "open": float(r['open']), "high": float(r['high']), "low": float(r['low']), "close": float(r['close']), "volume": int(r['volume'])} for _, r in df.iterrows()]
                    for b in bars: history_col.update_one({"symbol": sym}, {"$pull": {"history_d1": {"time": b["time"]}}})
                    history_col.update_one({"symbol": sym}, {"$push": {"history_d1": {"$each": bars, "$slice": -2500}}}, upsert=True)
                    doc_hist = history_col.find_one({"symbol": sym})
                    if doc_hist and doc_hist.get("history_d1"):
                        d1 = sorted(doc_hist["history_d1"], key=lambda x: x["time"])
                        history_col.update_one({"symbol": sym}, {"$set": {"history_w1": aggregate_candles(d1, 'W1'), "history_mn": aggregate_candles(d1, 'MN')}})
                time.sleep(1.0)
            print(f"[HISTORY] 🏁 Đã cào xong toàn bộ nến 30 ngày.", flush=True)
        finally: is_full_data_running = False
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "success", "message": "Đang cào 30 ngày nến..."})


@app.get("/api/crawl-full-history")
def crawl_full_history():
    print(f"\n[API] 📥 Gọi API /api/crawl-full-history (Ép cào nến 10 năm)", flush=True)
    global is_full_data_running
    if is_full_data_running: return jsonify({"status": "error", "message": "Đang chạy"}), 400
    is_full_data_running = True
    def _run():
        global is_full_data_running
        try:
            target = [doc["symbol"] for doc in collection.find({}, {"symbol": 1})]
            end, start = datetime.now().strftime("%Y-%m-%d"), (datetime.now() - timedelta(days=3650)).strftime("%Y-%m-%d")
            print(f"[HISTORY] 🚀 Bắt đầu cào nến 10 năm cho {len(target)} mã...", flush=True)
            for sym in target:
                df = _fetch_history_ssi(sym, start, end)
                if df is not None and not df.empty:
                    bars = [{"time": r['time'].strftime('%Y-%m-%d') if hasattr(r['time'], 'strftime') else str(r['time'])[:10], "open": float(r['open']), "high": float(r['high']), "low": float(r['low']), "close": float(r['close']), "volume": int(r['volume'])} for _, r in df.iterrows()]
                    history_col.update_one({"symbol": sym}, {"$set": {"history_d1": bars, "history_w1": aggregate_candles(bars, 'W1'), "history_mn": aggregate_candles(bars, 'MN')}}, upsert=True)
                time.sleep(1.5)
            print(f"[HISTORY] 🏁 Đã cào xong toàn bộ nến 10 năm.", flush=True)
        finally: is_full_data_running = False
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "success", "message": "Đang cào 10 năm nến..."})


@app.get("/api/force-bctc")
def force_bctc():
    global is_full_data_running
    if is_full_data_running: return jsonify({"status": "error", "message": "Đang chạy"}), 400
    is_full_data_running = True
    
    symbols_str = request.args.get("symbols", "").upper().strip()
    start_idx, limit_idx = int(request.args.get("start", 0)), int(request.args.get("limit", 15))
    specific = request.args.get("symbol", "").upper().strip()
    
    print(f"\n[API] 📥 Gọi API /api/force-bctc | Symbols: {symbols_str} | Specific: {specific}", flush=True)

    def _run():
        global is_full_data_running
        try:
            if symbols_str:
                target = [s.strip() for s in symbols_str.split(",") if s.strip()]
            elif specific:
                target = [specific]
            else:
                target = [doc["symbol"] for doc in collection.find({}, {"symbol": 1})][start_idx:start_idx+limit_idx]
                
            print(f"[BCTC-CRAWL] 🚀 Bắt đầu cào BCTC cho {len(target)} mã...", flush=True)
            
            for i, sym in enumerate(target):
                print(f"\n[BCTC-CRAWL] [{i+1}/{len(target)}] Đang xử lý {sym}...", flush=True)
                res = _fetch_bctc_ssi(sym)
                
                if res: 
                    bctc_col.update_one({"symbol": sym}, {"$set": {**res, "symbol": sym, "updated_at": _now_str()}}, upsert=True)
                    period = res.get("latest_period", "Không rõ")
                    print(f"  ✅ [THÀNH CÔNG] {sym} | Kỳ BCTC: {period}", flush=True)
                    
                    extracted = extract_metrics_from_bctc_data(res)
                    if extracted:
                        collection.update_one({"symbol": sym}, {"$set": extracted})
                        print(f"  ✅ [FINANCIALS] {sym}: Đã trích xuất thành công (CFO, LNST, D/E, GM)", flush=True)
                else:
                    print(f"  ❌ [THẤT BẠI] {sym} | Không lấy được dữ liệu", flush=True)
                    
                time.sleep(3.0)
                
            print(f"\n[BCTC-CRAWL] 🏁 Đã cào xong toàn bộ {len(target)} mã.", flush=True)
        finally: 
            is_full_data_running = False
            
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "success", "message": f"Đang cào BCTC cho {len(symbols_str.split(',')) if symbols_str else limit_idx} mã..."})


@app.get("/api/bctc-status")
def api_bctc_status():
    print("[API] 📋 Gọi API /api/bctc-status", flush=True)
    try:
        all_symbols = [doc["symbol"] for doc in collection.find({}, {"symbol": 1, "_id": 0})]
        result = []
        for sym in all_symbols:
            bctc_doc = bctc_col.find_one({"symbol": sym}, {"_id": 0, "symbol": 1, "updated_at": 1, "latest_period": 1})
            if bctc_doc and bctc_doc.get("updated_at"):
                result.append({
                    "symbol": sym, "has_bctc": True,
                    "updated_at": bctc_doc.get("updated_at", "Chưa rõ"),
                    "latest_period": bctc_doc.get("latest_period", "Chưa rõ")
                })
            else:
                result.append({
                    "symbol": sym, "has_bctc": False,
                    "updated_at": "Chưa cào", "latest_period": "Chưa có"
                })
        result = sorted(result, key=lambda x: x["symbol"])
        return jsonify({"status": "success", "data": result})
    except Exception as e:
        log_exception("[API BCTC STATUS]", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.get("/api/crawl-full-bctc")
def crawl_full_bctc():
    print(f"\n[API] 📥 Gọi API /api/crawl-full-bctc", flush=True)
    global is_full_data_running
    if is_full_data_running: return jsonify({"status": "error", "message": "Đang chạy"}), 400
    is_full_data_running = True
    def _run():
        global is_full_data_running
        try:
            target = [doc["symbol"] for doc in collection.find({}, {"symbol": 1})]
            print(f"[BCTC-CRAWL] 🚀 Bắt đầu cào toàn bộ BCTC cho {len(target)} mã...", flush=True)
            for i, sym in enumerate(target):
                print(f"[BCTC-CRAWL] [{i+1}/{len(target)}] Đang xử lý {sym}...", flush=True)
                res = _fetch_bctc_ssi(sym)
                if res: 
                    bctc_col.update_one({"symbol": sym}, {"$set": {**res, "symbol": sym, "updated_at": _now_str()}}, upsert=True)
                    extracted = extract_metrics_from_bctc_data(res)
                    if extracted:
                        collection.update_one({"symbol": sym}, {"$set": extracted})
                time.sleep(3.0)
            print(f"[BCTC-CRAWL] 🏁 Đã cào xong toàn bộ BCTC.", flush=True)
        finally: is_full_data_running = False
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "success", "message": "Đang cào FULL BCTC..."})


@app.get("/api/crawl-ratios")
def crawl_ratios():
    print(f"\n[API] 📥 Gọi API /api/crawl-ratios", flush=True)
    global is_full_data_running
    if is_full_data_running: return jsonify({"status": "error", "message": "Đang chạy"}), 400
    is_full_data_running = True

    def _run():
        global is_full_data_running
        try:
            target = [doc["symbol"] for doc in collection.find({}, {"symbol": 1})]
            print(f"[RATIO-CRAWL] 🚀 Bắt đầu cào bảng Ratio cho {len(target)} mã...", flush=True)
            
            for i, sym in enumerate(target):
                print(f"[RATIO-CRAWL] [{i+1}/{len(target)}] Đang lấy ratio cho {sym}...", flush=True)
                
                ratios = {}
                ratio_available = {}
                for key, code in VNDIRECT_RATIOS.items():
                    val = get_vndirect_ratio(sym, code)
                    # Availability follows an actual finite provider observation.
                    # A returned zero is valid; a missing/invalid response is not.
                    ratio_available[key] = _is_finite_number(val)
                    ratios[key] = val if ratio_available[key] else 0
                    time.sleep(0.2)

                roe_val = ratios.get("roe", 0)
                if roe_val and roe_val <= 1: roe_val *= 100
                
                roa_val = ratios.get("roa", 0)
                if roa_val and roa_val <= 1: roa_val *= 100
                
                div_val = ratios.get("dividend_yield", 0)
                if div_val and div_val <= 1: div_val *= 100

                eps_raw = ratios.get("eps", 0)
                if eps_raw >= 1000:
                    eps_raw = eps_raw / 1000.0
                eps_final = round(eps_raw, 2)

                collection.update_one({"symbol": sym}, {"$set": {
                    "pe": round(ratios.get("pe", 0), 2),
                    "pb": round(ratios.get("pb", 0), 2),
                    
                    "eps": eps_final, 
                    "roe": round(roe_val, 1),
                    "roe_available": ratio_available.get("roe", False),
                    "roa": round(roa_val, 1),
                    "roa_available": ratio_available.get("roa", False),
                    "dividend_yield": round(div_val, 2),
                    "ratio_updated_at": _now_str()
                }}, upsert=True)
                
                print(f"  ✅ [RATIO] {sym} thành công (P/E: {ratios.get('pe', 0)}, EPS: {eps_final}, ROE: {round(roe_val, 1)}%)", flush=True)
                time.sleep(0.5)

            print(f"[RATIO-CRAWL] 🏁 Đã cào xong toàn bộ bảng Ratio.", flush=True)
        finally: 
            is_full_data_running = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "success", "message": "Đang cào bảng Ratio..."})


@app.get("/api/score-single")
def api_score_single():
    """
    Chấm điểm một mã theo đúng công thức Screener hiện tại.
    Không preload BCTC/lịch sử của toàn thị trường và không ghi đè toàn bộ screener.
    """
    symbol = request.args.get("symbol", "").upper().strip()
    if not ADMIN_SECRET_KEY or request.headers.get("X-Admin-Key") != ADMIN_SECRET_KEY:
        return jsonify({"status": "error", "message": "Sai mật khẩu Admin!"}), 403
    if not symbol or not re.fullmatch(r"[A-Z0-9]{3,10}", symbol):
        return jsonify({"status": "error", "message": "Mã cổ phiếu không hợp lệ."}), 400
    if is_screener_running:
        return jsonify({
            "status": "busy",
            "message": "Screener toàn thị trường đang chạy. Vui lòng thử lại sau."
        }), 409

    print(f"[API] 🎯 Chấm điểm riêng mã {symbol}...", flush=True)
    try:
        # 1) Chỉ lấy dữ liệu gốc và BCTC của mã được yêu cầu.
        stock_doc = collection.find_one({"symbol": symbol}, {"_id": 0})
        if not stock_doc:
            return jsonify({"status": "error", "message": f"Không tìm thấy dữ liệu giá cho {symbol}."}), 404

        bctc_doc = bctc_col.find_one(
            {"symbol": symbol},
            {"_id": 0, "source": 1, "income_statement": 1, "cash_flow": 1, "balance_sheet": 1}
        ) or {}
        stock_doc["bctc_history"] = bctc_doc.get("income_statement", []) or []
        extracted = extract_metrics_from_bctc_data(bctc_doc)
        if extracted:
            stock_doc.update(extracted)
            collection.update_one({"symbol": symbol}, {"$set": extracted})
            # Mã vừa cập nhật cũng có thể làm thay đổi trung vị peer của ngành.
            _SECTOR_MEDIAN_CACHE.clear()

        # 2) Trung vị ngành lấy từ dữ liệu giá/định giá đang lưu sẵn.
        # Không tải BCTC hoặc lịch sử của các mã khác.
        sector_medians, peer_cache_hit = get_cached_sector_medians(collection)
        group_label = get_sub_sector(symbol)
        config = SUB_SECTOR_CONFIG.get(group_label) or SUB_SECTOR_CONFIG.get(get_sector(symbol))
        if not config:
            return jsonify({"status": "error", "message": f"Chưa có cấu hình chấm điểm cho ngành của {symbol}."}), 422
        sector_median, _ = get_median_for_symbol(symbol, sector_medians)
        nd = normalize_stock_data(symbol, stock_doc)
        update_fields = nd.pop("_update_fields", {})
        if update_fields:
            collection.update_one({"symbol": symbol}, {"$set": update_fields})
            # Chuẩn hóa có thể thay đổi PE/PB/ROE đầu vào của peer medians.
            _SECTOR_MEDIAN_CACHE.clear()

        # 3) Chỉ tải lịch sử của mã được yêu cầu; phân tích kỹ thuật dùng 200 nến gần nhất.
        hist_doc = history_col.find_one(
            {"symbol": symbol},
            {"_id": 0, "history_d1": {"$slice": -1400}}
        ) or {}
        history_bars = hist_doc.get("history_d1", []) or []
        tech_bars = history_bars[-200:]
        tech_score_raw, tech_status, pattern, technical_details = 0, "Chưa đủ dữ liệu kỹ thuật", "Chưa xác định", {}
        if ProfessionalTechnicalAnalyzer and tech_bars:
            try:
                analyzer = ProfessionalTechnicalAnalyzer(tech_bars)
                ptkt_res = analyzer.analyze()
                tech_score_raw = ptkt_res.get("technical_score", 0)
                tech_status = ptkt_res.get("technical_status", "Chưa đủ dữ liệu kỹ thuật")
                pattern = ptkt_res.get("pattern", "Chưa xác định")
                technical_details = ptkt_res.get("technical_score_breakdown", {})
            except Exception as tech_error:
                print(f"[SINGLE-SCREENER] {symbol} lỗi phân tích kỹ thuật: {tech_error}", flush=True)

        # 4) Giữ nguyên dữ liệu AI đã có, không gọi AI trong luồng chấm điểm.
        old_result = screener_col.find_one({"symbol": symbol}, {"_id": 0}) or {}
        existing_ai = {
            "bctc": stock_doc.get("ai_bctc") or old_result.get("bctc_evaluation", ""),
            "catalyst": stock_doc.get("ai_catalyst") or old_result.get("catalyst", ""),
            "risks": stock_doc.get("ai_risks") or old_result.get("risks", ""),
            "macro_view": stock_doc.get("ai_macro_view") or old_result.get("macro_view", ""),
            "outlook": stock_doc.get("ai_outlook") or old_result.get("outlook", ""),
            "recommendation": stock_doc.get("ai_recommendation") or old_result.get("ai_recommendation", ""),
            "price_zone": stock_doc.get("ai_price_zone") or old_result.get("ai_price_zone", ""),
            "ai_updated_at": stock_doc.get("ai_updated_at") or old_result.get("ai_updated_at", "Chưa phân tích"),
            "ai_source": stock_doc.get("ai_source") or old_result.get("ai_source", "AI")
        }

        result = evaluate_stock_from_db(
            symbol, nd, sector_median, group_label, config,
            tech_score_raw=tech_score_raw,
            tech_status=tech_status,
            pattern=pattern,
            technical_details=technical_details,
            existing_ai_data=existing_ai,
            history_bars=history_bars
        )
        if not result:
            return jsonify({"status": "error", "message": f"Không thể chấm điểm {symbol}."}), 422

        result["date"] = _vn_now().strftime("%Y-%m-%d")
        result["score_updated_at"] = _now_str()
        # Persist the same BCTC freshness provenance used by the scheduled worker.
        latest_period = str(nd.get("bctc_latest_income_period") or "").strip()
        result["bctc_latest_income_period"] = latest_period
        result["bctc_expected_period"] = (
            nd.get("bctc_expected_period") or _expected_latest_bctc_period()
        )
        result["bctc_period_is_current"] = (
            bool(nd.get("bctc_period_is_current")) if latest_period else None
        )

        # Cấp quyền ghi và cập nhật trong cùng critical section để tránh race với full refresh.
        if not _safe_update_screener_result(symbol, result):
            return jsonify({
                "status": "busy",
                "message": "Screener toàn thị trường đang chạy; kết quả riêng chưa được lưu."
            }), 409
        print(
            f"[SINGLE-SCREENER] ✅ {symbol} | Tổng {result.get('total_score')}/100 "
            f"| {result.get('final_decision')}",
            flush=True
        )
        return jsonify({
            "status": "success",
            "message": f"Đã chấm điểm riêng {symbol}.",
            "symbol": symbol,
            "data": result
        })
    except Exception as e:
        log_exception(f"[SINGLE-SCREENER] Lỗi chấm điểm {symbol}", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.get("/api/force-screener")
def force_screener():
    if not ADMIN_SECRET_KEY or request.headers.get("X-Admin-Key") != ADMIN_SECRET_KEY:
        return jsonify({"status": "error", "message": "Sai mật khẩu Admin!"}), 403
        
    print(f"\n[API] 📥 Gọi API /api/force-screener", flush=True)
    if not _claim_screener_job():
        return jsonify({"status": "error", "message": "Screener đang chạy (worker hoặc API khác), vui lòng đợi!"}), 409

    print(f"[SCREENER] 🚀 Bắt đầu chấm điểm lại toàn bộ (Chế độ tối ưu)...", flush=True)
    
    def _run():
        try:
            today_str = _vn_now().strftime("%Y-%m-%d")
            
            # TỐI ƯU: Preload dữ liệu
            print("[SCREENER] ⚡ Đang tải trước toàn bộ dữ liệu...", flush=True)
            all_stocks = list(collection.find({}))
            screener_symbols = [doc["symbol"] for doc in all_stocks if doc.get("symbol")]
            all_bctc = _load_screener_bctc(screener_symbols, chunk_size=10)
            all_hist = _load_screener_history(screener_symbols, chunk_size=5, bars_limit=1400)
            
            stock_db = {}
            bulk_updates = []
            
            for doc in all_stocks:
                sym = doc["symbol"]
                bctc = all_bctc.get(sym)
                doc["bctc_history"] = bctc.get("income_statement", []) if bctc else []
                
                if bctc:
                    extracted = extract_metrics_from_bctc_data(bctc)
                    if extracted:
                        doc.update(extracted)
                        bulk_updates.append(UpdateOne({"symbol": sym}, {"$set": extracted}))
                
                stock_db[sym] = doc

            if bulk_updates:
                collection.bulk_write(bulk_updates)
                _SECTOR_MEDIAN_CACHE.clear()

            norm_db = {}
            norm_bulk_updates = []
            for s, r in stock_db.items():
                nd = normalize_stock_data(s, r)
                update_fields = nd.pop("_update_fields", {})
                if update_fields:
                    norm_bulk_updates.append(UpdateOne({"symbol": s}, {"$set": update_fields}))
                norm_db[s] = nd
            
            if norm_bulk_updates:
                collection.bulk_write(norm_bulk_updates)
                _SECTOR_MEDIAN_CACHE.clear()
            
            medians = compute_sector_medians(norm_db)
            results = []
            
            for sym, nd in norm_db.items():
                grp = get_sub_sector(sym)
                cfg = SUB_SECTOR_CONFIG.get(grp) or SUB_SECTOR_CONFIG.get(get_sector(sym))
                med, _ = get_median_for_symbol(sym, medians)
                
                hist_doc = all_hist.get(sym)
                history_bars = hist_doc.get("history_d1", []) if hist_doc else []
                tech_bars = history_bars[-200:]
                tech_score_raw, tech_status, pattern, technical_details = 0, "Chưa đủ dữ liệu kỹ thuật", "Chưa xác định", {}
                if ProfessionalTechnicalAnalyzer and tech_bars:
                    try:
                        analyzer = ProfessionalTechnicalAnalyzer(tech_bars)
                        ptkt_res = analyzer.analyze()
                        tech_score_raw = ptkt_res.get("technical_score", 0)
                        tech_status = ptkt_res.get("technical_status", "Chưa đủ dữ liệu kỹ thuật")
                        pattern = ptkt_res.get("pattern", "Chưa xác định")
                        technical_details = ptkt_res.get("technical_score_breakdown", {})
                    except Exception as tech_error:
                        print(f"[SCREENER-ALL] {sym} lỗi phân tích kỹ thuật: {tech_error}", flush=True)
                    
                stock_doc = stock_db.get(sym, {})
                existing_ai = {
                    "bctc": stock_doc.get("ai_bctc", ""), "catalyst": stock_doc.get("ai_catalyst", ""),
                    "risks": stock_doc.get("ai_risks", ""), "macro_view": stock_doc.get("ai_macro_view", ""),
                    "outlook": stock_doc.get("ai_outlook", ""), "recommendation": stock_doc.get("ai_recommendation", ""),
                    "price_zone": stock_doc.get("ai_price_zone", ""), "ai_updated_at": stock_doc.get("ai_updated_at", "Chưa phân tích"),
                    "ai_source": stock_doc.get("ai_source", "AI")
                }
                
                res = evaluate_stock_from_db(
                    sym, nd, med, grp, cfg, 
                    tech_score_raw=tech_score_raw, tech_status=tech_status, pattern=pattern,
                    technical_details=technical_details,
                    existing_ai_data=existing_ai,
                    history_bars=history_bars
                )
                if res: 
                    res["date"] = today_str
                    results.append(res)
            
            if results:
                # Persist BCTC freshness provenance just like the scheduled worker.
                # Diagnostics reads these fields from screener_results; omitting them
                # makes valid source BCTC appear unverified in persisted results.
                for r in results:
                    normalized = norm_db.get(r.get("symbol"), {})
                    latest_period = str(
                        normalized.get("bctc_latest_income_period") or ""
                    ).strip()
                    r["bctc_latest_income_period"] = latest_period
                    r["bctc_expected_period"] = (
                        normalized.get("bctc_expected_period")
                        or _expected_latest_bctc_period()
                    )
                    r["bctc_period_is_current"] = (
                        bool(normalized.get("bctc_period_is_current"))
                        if latest_period else None
                    )

                results = sorted(results, key=lambda x: (x.get("total_score", 0), x.get("fundamental_score", 0)), reverse=True)
                
                for r in results:
                    existing_stock = stock_db.get(r["symbol"], {})
                    if existing_stock and existing_stock.get("ai_bctc") and len(existing_stock.get("ai_bctc")) > 100:
                        r["bctc_evaluation"] = existing_stock.get("ai_bctc", "")
                        r["outlook"] = existing_stock.get("ai_outlook", "")
                        r["ai_recommendation"] = existing_stock.get("ai_recommendation", "")
                        r["ai_price_zone"] = existing_stock.get("ai_price_zone", "")
                        r["macro_view"] = existing_stock.get("ai_macro_view", "")
                        r["ai_updated_at"] = existing_stock.get("ai_updated_at", "")
                        r["ai_source"] = existing_stock.get("ai_source", "AI")

                if _safe_replace_screener_results(results, tag="force-api"):
                    commit_multiple_files_to_github({SCREENER_JSON_FILE: results}, f"Force Screener {_now_str()}")
                    print(f"[SCREENER] ✅ Đã chấm điểm xong {len(results)} mã (Đã bảo vệ bài AI cũ).", flush=True)
                else:
                    print(f"[SCREENER] ⚠️ Không ghi được kết quả (lock timeout).", flush=True)
        except Exception as e:
            print(f"[SCREENER] ❌ Lỗi: {e}", flush=True)
        finally:
            _release_screener_job()

    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        _release_screener_job()
        raise
    return jsonify({"status": "success", "message": "Đang chấm điểm lại (tối ưu)..."})


@app.get("/api/force-realtime")
def force_realtime():
    print(f"\n[API] ⚡ Gọi API /api/force-realtime", flush=True)
    global is_crawling
    
    if is_crawling:
        return jsonify({"status": "error", "message": "Hệ thống đang cào, vui lòng đợi!"}), 400
        
    all_symbols = [doc["symbol"] for doc in collection.find({}, {"symbol": 1})]
    threading.Thread(target=_execute_crawl, args=(all_symbols, False, True)).start()
    
    return jsonify({
        "status": "success", 
        "message": f"Đã kích hoạt ép cào realtime cho {len(all_symbols)} mã."
    })


@app.get("/api/reset-today")
def reset_today():
    print(f"\n[API] 📥 Gọi API /api/reset-today", flush=True)
    today_str = _vn_now().strftime("%Y-%m-%d")
    result = collection.update_many({"chot_so_today": today_str}, {"$unset": {"chot_so_today": "", "is_locked": ""}})
    return jsonify({"status": "success", "message": f"Đã xóa cờ chốt sổ của {result.modified_count} mã."})


@app.get("/api/force-fix")
def force_fix_data():
    print(f"\n[API] 📥 Gọi API /api/force-fix", flush=True)
    collection.update_many({}, {"$unset": {"chot_so_today": "", "is_locked": "", "trading_date": ""}})
    all_symbols = [doc["symbol"] for doc in collection.find({}, {"symbol": 1})]
    threading.Thread(target=_execute_crawl, args=(all_symbols, False, True)).start()
    return jsonify({"status": "success", "message": f"Đang ép cào lại toàn bộ {len(all_symbols)} mã."})


@app.get("/api/retry-now")
def retry_now():
    print(f"\n[API] 📥 Gọi API /api/retry-now", flush=True)
    if is_crawling:
        return jsonify({"status": "error", "message": "Hệ thống đang chạy!"})
    incomplete_symbols = [doc["symbol"] for doc in collection.find({"is_incomplete": True}, {"symbol": 1})]
    if not incomplete_symbols:
        return jsonify({"status": "success", "message": "Không có mã nào bị lỗi!"})
    threading.Thread(target=_execute_crawl, args=(incomplete_symbols, True, True)).start()
    return jsonify({"status": "success", "message": f"Đã kích hoạt cào bù {len(incomplete_symbols)} mã!"})


# ===================== API LỊCH SỬ =====================
@app.get("/api/history-all")
def api_get_history_all():
    global _history_cache
    try:
        now = time.time()
        with _history_cache["lock"]:
            if _history_cache["data"] and (now - _history_cache["timestamp"]) < HISTORY_CACHE_TTL:
                print(f"[API] 🚀 /api/history-all: Trả cache ({len(_history_cache['data']['data'])} mã)", flush=True)
                return jsonify(_history_cache["data"])
        
        print("[API] 📊 /api/history-all: Đọc DB (50 nến/mã)...", flush=True)
        all_symbols = [doc["symbol"] for doc in collection.find({}, {"symbol": 1})]
        result = {}
        
        for sym in all_symbols:
            hist_doc = history_col.find_one(
                {"symbol": sym}, 
                {"_id": 0, "history_d1": {"$slice": -50}}
            )
            if hist_doc and hist_doc.get("history_d1"):
                result[sym] = {"history_d1": hist_doc["history_d1"]}
                
        response_data = {"status": "success", "count": len(result), "data": result}
        
        with _history_cache["lock"]:
            _history_cache["data"] = response_data
            _history_cache["timestamp"] = now
            
        print(f"[API] ✅ /api/history-all: Đã tải {len(result)} mã (50 nến)", flush=True)
        return jsonify(response_data)
    except Exception as e:
        log_exception("[API HISTORY ALL]", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.get("/api/history-all-more")
def api_get_history_all_more():
    try:
        print("[API] 📊 /api/history-all-more: Đọc nến cũ hơn (250 nến/mã)...", flush=True)
        all_symbols = [doc["symbol"] for doc in collection.find({}, {"symbol": 1})]
        result = {}
        
        for sym in all_symbols:
            hist_doc = history_col.find_one({"symbol": sym}, {"_id": 0})
            if hist_doc and hist_doc.get("history_d1"):
                bars = hist_doc["history_d1"]
                older_bars = bars[:-50][-250:] if len(bars) > 50 else []
                if older_bars:
                    result[sym] = {"history_d1": older_bars}
                
        response_data = {"status": "success", "count": len(result), "data": result}
        print(f"[API] ✅ /api/history-all-more: Đã tải {len(result)} mã (250 nến cũ)", flush=True)
        return jsonify(response_data)
    except Exception as e:
        log_exception("[API HISTORY ALL MORE]", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.get("/api/history-all-full")
def api_get_history_all_full():
    try:
        all_symbols = [doc["symbol"] for doc in collection.find({}, {"symbol": 1})]
        result = {}
        
        for sym in all_symbols:
            hist_doc = history_col.find_one({"symbol": sym}, {"_id": 0})
            if hist_doc and hist_doc.get("history_d1"):
                bars = hist_doc["history_d1"]
                older_bars = bars[:-300] if len(bars) > 300 else []
                if older_bars:
                    result[sym] = {"history_d1": older_bars}
        
        return jsonify({"status": "success", "count": len(result), "data": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.get("/api/history")
def api_get_history():
    symbol = request.args.get("symbol", "").upper().strip()
    if not symbol:
        return jsonify({"status": "error", "message": "Thiếu mã"}), 400
        
    print(f"[API] 📊 Gọi API /api/history | Mã: {symbol}", flush=True)
    
    try:
        hist_doc = history_col.find_one({"symbol": symbol}, {"_id": 0})
        history_d1 = hist_doc.get("history_d1", []) if hist_doc else []
        
        live_doc = collection.find_one({"symbol": symbol}, {"_id": 0})
        
        if live_doc and live_doc.get("close", 0) > 0:
            today_live = live_doc.get("trading_date")
            
            if not today_live:
                today_live = _vn_now().strftime("%Y-%m-%d")
            
            bar_today = {
                "time": today_live,
                "open": float(live_doc.get("open", 0) or live_doc["close"]),
                "high": float(live_doc.get("high", 0) or live_doc["close"]),
                "low": float(live_doc.get("low", 0) or live_doc["close"]),
                "close": float(live_doc["close"]),
                "volume": int(live_doc.get("volume", 0) or 0)
            }
            
            existing_idx = None
            for i, b in enumerate(history_d1):
                if b.get("time") == today_live:
                    existing_idx = i
                    break
            
            if existing_idx is not None:
                history_d1[existing_idx]["close"] = bar_today["close"]
                history_d1[existing_idx]["high"] = max(
                    float(history_d1[existing_idx].get("high", 0) or 0),
                    bar_today["high"]
                )
                old_low = float(history_d1[existing_idx].get("low", 0) or 0)
                history_d1[existing_idx]["low"] = (
                    min(old_low, bar_today["low"]) if old_low > 0 else bar_today["low"]
                )
                if bar_today["volume"] > 0:
                    history_d1[existing_idx]["volume"] = bar_today["volume"]
            else:
                history_d1.append(bar_today)
                history_d1.sort(key=lambda x: x["time"])
        
        if not history_d1:
            return jsonify({"status": "error", "message": "Không có dữ liệu lịch sử"}), 404
        
        return jsonify({
            "status": "success", 
            "symbol": symbol,
            "history_d1": history_d1,
            "history_w1": hist_doc.get("history_w1", []) if hist_doc else [],
            "history_mn": hist_doc.get("history_mn", []) if hist_doc else []
        })
    except Exception as e:
        log_exception("[API HISTORY]", e)
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)        
