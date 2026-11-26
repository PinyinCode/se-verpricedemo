# -*- coding: utf-8 -*-
"""Forex AI bridge API."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.request import Request, urlopen

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("FOREX_DB_PATH", str(BASE_DIR / "forex_demo.db")))
API_KEY = os.getenv("FOREX_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
STALE_SECONDS = int(os.getenv("SIGNAL_STALE_SECONDS", "300"))
CLOSE_REQUEST_TTL = int(os.getenv("CLOSE_REQUEST_TTL_SECONDS", "45"))
SLTP_REQUEST_TTL = int(os.getenv("SLTP_REQUEST_TTL_SECONDS", "60"))
PENDING_MODIFY_TTL = int(os.getenv("PENDING_MODIFY_TTL_SECONDS", "60"))

_db_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_recently_acked: dict[str, float] = {}
_ACK_COOLDOWN = 10.0
_GMT7 = timezone(timedelta(hours=7))

_ticks_lock = threading.Lock()
_latest_ticks: dict[str, dict] = {}
_last_candle_prune_ts: dict[tuple[str, str], float] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        _conn = conn
    return _conn


@contextmanager
def db():
    with _db_lock:
        conn = _get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _ensure_candles_ask_column(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(candles)").fetchall()}
    if "ask" not in cols:
        conn.execute("ALTER TABLE candles ADD COLUMN ask REAL")


def init_db() -> None:
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            auto_trade_enabled INTEGER NOT NULL DEFAULT 1,
            trailing_enabled INTEGER NOT NULL DEFAULT 1,
            breakeven_enabled INTEGER NOT NULL DEFAULT 1,
            news_filter_enabled INTEGER NOT NULL DEFAULT 1,
            news_block_before_min INTEGER NOT NULL DEFAULT 3,
            news_block_after_min INTEGER NOT NULL DEFAULT 3,
            news_events_json TEXT NOT NULL DEFAULT '[]',
            time_filter_enabled INTEGER NOT NULL DEFAULT 1,
            emergency_stop INTEGER NOT NULL DEFAULT 0,
            emergency_close_all INTEGER NOT NULL DEFAULT 0,
            telegram_notifications_enabled INTEGER NOT NULL DEFAULT 1,
            risk_money REAL NOT NULL DEFAULT 25,
            max_positions INTEGER NOT NULL DEFAULT 5,
            reward_risk_ratio REAL NOT NULL DEFAULT 2.0,
            risk_tolerance_pct REAL NOT NULL DEFAULT 0.25,
            commission_fallback REAL NOT NULL DEFAULT 0,
            spread_buffer_points INTEGER NOT NULL DEFAULT 0,
            trailing_trigger_rr REAL NOT NULL DEFAULT 1.0,
            trailing_lock_rr REAL NOT NULL DEFAULT 0.5,
            breakeven_trigger_rr REAL NOT NULL DEFAULT 1.0,
            trailing_mode TEXT NOT NULL DEFAULT 'dynamic',
            trading_start_hour INTEGER NOT NULL DEFAULT 7,
            trading_end_hour INTEGER NOT NULL DEFAULT 23,
            updated_at TEXT,
            updated_by TEXT
        )
        """)
        conn.execute("INSERT OR IGNORE INTO settings(id, updated_at, updated_by) VALUES(1, ?, 'system')", (utc_now(),))

        conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            order_type TEXT NOT NULL DEFAULT 'MARKET',
            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            risk_money REAL NOT NULL DEFAULT 25,
            requested_lot REAL NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'api',
            status TEXT NOT NULL DEFAULT 'logged',
            created_at TEXT NOT NULL,
            claimed_by TEXT,
            claimed_at TEXT,
            ack_at TEXT,
            ack_status TEXT,
            ack_message TEXT
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS mt5_account_items (
            id TEXT PRIMARY KEY,
            ticket TEXT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL DEFAULT 'MT5',
            direction TEXT NOT NULL,
            entry REAL NOT NULL,
            sl REAL NOT NULL DEFAULT 0,
            tp REAL NOT NULL DEFAULT 0,
            volume REAL NOT NULL,
            profit REAL NOT NULL DEFAULT 0,
            opened_at REAL NOT NULL DEFAULT 0,
            is_pending INTEGER NOT NULL DEFAULT 0,
            order_type TEXT NOT NULL DEFAULT '',
            magic INTEGER NOT NULL DEFAULT 0,
            comment TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'MT5_EXTERNAL',
            updated_at TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS mt5_account_info (
            account_id TEXT PRIMARY KEY,
            login INTEGER NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            server TEXT NOT NULL DEFAULT '',
            broker TEXT NOT NULL DEFAULT '',
            currency TEXT NOT NULL DEFAULT 'USD',
            leverage INTEGER NOT NULL DEFAULT 0,
            account_mode TEXT NOT NULL DEFAULT 'DEMO',
            trade_allowed INTEGER NOT NULL DEFAULT 1,
            expert_allowed INTEGER NOT NULL DEFAULT 1,
            balance REAL NOT NULL DEFAULT 0,
            equity REAL NOT NULL DEFAULT 0,
            margin REAL NOT NULL DEFAULT 0,
            free_margin REAL NOT NULL DEFAULT 0,
            margin_level REAL NOT NULL DEFAULT 0,
            profit REAL NOT NULL DEFAULT 0,
            positions_count INTEGER NOT NULL DEFAULT 0,
            orders_count INTEGER NOT NULL DEFAULT 0,
            source_timestamp INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS close_requests (
            request_id TEXT PRIMARY KEY,
            position_id TEXT,
            ticket TEXT,
            symbol TEXT,
            direction TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            acked_at TEXT,
            ok INTEGER,
            message TEXT,
            kind TEXT NOT NULL DEFAULT 'close',
            is_pending INTEGER NOT NULL DEFAULT 0,
            order_type TEXT NOT NULL DEFAULT 'MARKET'
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS sltp_requests (
            request_id TEXT PRIMARY KEY,
            ticket TEXT NOT NULL,
            symbol TEXT NOT NULL,
            old_sl REAL NOT NULL DEFAULT 0,
            old_tp REAL NOT NULL DEFAULT 0,
            new_sl REAL NOT NULL DEFAULT 0,
            new_tp REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            acked_at TEXT,
            ok INTEGER,
            message TEXT
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_modify_requests (
            request_id TEXT PRIMARY KEY,
            ticket TEXT NOT NULL,
            symbol TEXT NOT NULL,
            order_type TEXT NOT NULL DEFAULT '',
            old_entry REAL NOT NULL DEFAULT 0,
            old_sl REAL NOT NULL DEFAULT 0,
            old_tp REAL NOT NULL DEFAULT 0,
            new_entry REAL NOT NULL DEFAULT 0,
            new_sl REAL NOT NULL DEFAULT 0,
            new_tp REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            acked_at TEXT,
            ok INTEGER,
            message TEXT
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS real_history (
            id TEXT PRIMARY KEY,
            ticket TEXT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL DEFAULT '—',
            direction TEXT NOT NULL,
            entry REAL NOT NULL DEFAULT 0,
            sl REAL NOT NULL DEFAULT 0,
            tp REAL NOT NULL DEFAULT 0,
            volume REAL NOT NULL,
            profit REAL NOT NULL,
            outcome TEXT NOT NULL DEFAULT 'CLOSED',
            opened_at REAL NOT NULL DEFAULT 0,
            closed_at REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS heartbeats (
            component TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_status (
            component TEXT PRIMARY KEY,
            symbols_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS today_signals (
            signal_key TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            divergence TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            signal_time INTEGER NOT NULL,
            price1 REAL NOT NULL DEFAULT 0,
            price2 REAL NOT NULL DEFAULT 0,
            rsi1 REAL NOT NULL DEFAULT 0,
            rsi2 REAL NOT NULL DEFAULT 0,
            time1 INTEGER NOT NULL DEFAULT 0,
            time2 INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS candles (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            candle_time INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL DEFAULT 0,
            digits INTEGER NOT NULL DEFAULT 5,
            tick_size REAL NOT NULL DEFAULT 0.00001,
            tick_value REAL NOT NULL DEFAULT 0,
            volume_min REAL NOT NULL DEFAULT 0,
            volume_max REAL NOT NULL DEFAULT 0,
            volume_step REAL NOT NULL DEFAULT 0,
            ask REAL,
            PRIMARY KEY (symbol, timeframe, candle_time)
        )
        """)
        _ensure_candles_ask_column(conn)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS symbol_commissions (
            account_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            commission_per_lot REAL NOT NULL DEFAULT 0,
            swap_long REAL NOT NULL DEFAULT 0,
            swap_short REAL NOT NULL DEFAULT 0,
            contract_size REAL NOT NULL DEFAULT 0,
            tick_value REAL NOT NULL DEFAULT 0,
            point_size REAL NOT NULL DEFAULT 0.00001,
            source_updated_at INTEGER NOT NULL DEFAULT 0,
            received_at TEXT NOT NULL,
            PRIMARY KEY (account_id, symbol)
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)


def _parse_news_timestamp(raw_time: object) -> float | None:
    if raw_time is None:
        return None
    if isinstance(raw_time, (int, float)):
        return float(raw_time)
    s = str(raw_time).strip()
    if not s:
        return None
    if s.isdigit():
        return float(s)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_GMT7)
    return dt.timestamp()


def evaluate_news_filter(settings_dict: dict, now_ts: float | None = None) -> dict:
    enabled = bool(settings_dict.get("news_filter_enabled", True))
    before_min = max(0, int(settings_dict.get("news_block_before_min", 3)))
    after_min = max(0, int(settings_dict.get("news_block_after_min", 3)))
    before_sec = before_min * 60
    after_sec = after_min * 60

    raw_events = settings_dict.get("news_events_json", "[]")
    try:
        events = json.loads(raw_events) if isinstance(raw_events, str) else (raw_events or [])
        if not isinstance(events, list):
            events = []
    except Exception:
        events = []

    if not enabled:
        return {
            "blocked": False,
            "reason": None,
            "before_min": before_min,
            "after_min": after_min,
            "active_event": None,
        }

    if now_ts is None:
        now_ts = datetime.now(timezone.utc).timestamp()

    for ev in events:
        if not isinstance(ev, dict):
            continue
        ev_ts = _parse_news_timestamp(ev.get("time") or ev.get("timestamp") or ev.get("date"))
        if ev_ts is None:
            continue
        window_start = ev_ts - before_sec
        window_end = ev_ts + after_sec
        if window_start <= now_ts <= window_end:
            title = str(ev.get("title") or ev.get("name") or ev.get("event") or "News Event").strip()
            currency = str(ev.get("currency") or "").strip()
            label = f"{currency} {title}".strip() if currency else title
            return {
                "blocked": True,
                "reason": "news_window_3m",
                "detail": f"Chặn tin (-{before_min}m / +{after_min}m): {label}",
                "before_min": before_min,
                "after_min": after_min,
                "active_event": {
                    "title": title,
                    "currency": currency,
                    "event_time": ev_ts,
                    "blocked_from": window_start,
                    "blocked_until": window_end,
                },
            }

    return {
        "blocked": False,
        "reason": None,
        "detail": None,
        "before_min": before_min,
        "after_min": after_min,
        "active_event": None,
    }


def send_telegram_rt_signal(item: "TodaySignalItem") -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    local_time = datetime.fromtimestamp(
        item.signal_time, timezone.utc
    ).astimezone(_GMT7)
    direction = "▲ BUY" if item.direction == "BUY" else "▼ SELL"
    div_name = "Regular" if item.divergence.lower() in ("reg", "regular") else "Hidden"
    message = (
        "🔔 AI OOP V8 · REALTIME\n"
        f"{item.symbol} · {item.timeframe}\n"
        f"{direction} · {div_name} Divergence\n"
        f"Giá điểm phân kỳ: {item.price2:.8f}\n"
        f"RSI: {item.rsi1:.1f} → {item.rsi2:.1f}\n"
        f"Thời gian: {local_time:%d/%m/%Y %H:%M} (GMT+7)"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=6) as response:
            result = json.loads(response.read().decode("utf-8"))
        if result.get("ok") is True:
            return True
        print("[TELEGRAM] Telegram API rejected an RT notification")
    except Exception as exc:
        print(f"[TELEGRAM] RT notification failed: {type(exc).__name__}")
    return False


# ============ Fast Tick Cache & Helpers ============

def _set_latest_tick(
    symbol: str,
    bid: float | None,
    ask: float | None,
    last: float = 0.0,
    time_msc: int = 0,
    source: str = "mt5",
) -> dict:
    now_mono = time.monotonic()
    now_iso = utc_now()
    with _ticks_lock:
        prev = _latest_ticks.get(symbol, {})
        resolved_bid = float(bid) if (bid is not None and bid > 0) else prev.get("bid")
        resolved_ask = float(ask) if (ask is not None and ask > 0) else prev.get("ask")
        resolved_last = (
            float(last)
            if (last and last > 0)
            else (resolved_bid or resolved_ask or prev.get("last") or 0.0)
        )
        entry = {
            "symbol": symbol,
            "bid": resolved_bid,
            "ask": resolved_ask,
            "last": resolved_last,
            "time_msc": int(time_msc or prev.get("time_msc") or 0),
            "source": source,
            "updated_at": now_iso,
            "_mono": now_mono,
        }
        _latest_ticks[symbol] = entry
        return {k: v for k, v in entry.items() if not k.startswith("_")}


def _get_cached_tick(symbol: str, max_age_sec: float = 30.0) -> dict | None:
    now_mono = time.monotonic()
    with _ticks_lock:
        entry = _latest_ticks.get(symbol)
        if not entry:
            return None
        if (now_mono - float(entry.get("_mono", 0.0))) > max_age_sec:
            return None
        return {k: v for k, v in entry.items() if not k.startswith("_")}


def _get_all_cached_ticks() -> dict[str, dict]:
    with _ticks_lock:
        return {
            k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
            for k, v in _latest_ticks.items()
        }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with db() as conn:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_queue ON signals(status, source, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_source ON signals(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_today_signals_time ON today_signals(signal_time)")
    try:
        yield
    finally:
        global _conn
        with _db_lock:
            if _conn is not None:
                _conn.close()
                _conn = None


app = FastAPI(
    title="Forex AI Server",
    version="0.6.2-fast",
    description="Signal analysis and MT5 real-order bridge with runtime control panel.",
    lifespan=lifespan,
)

_frontend_origins = [x.strip() for x in os.getenv(
    "FRONTEND_ORIGINS", "https://pinyincode.github.io,http://localhost:8000,http://127.0.0.1:8000"
).split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_frontend_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "dashboard")), name="static")


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    if not API_KEY:
        raise HTTPException(status_code=503, detail="Server chưa cấu hình FOREX_API_KEY")
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="API key không hợp lệ")


# ============ Models ============

class TickIn(BaseModel):
    symbol: str = Field(min_length=3, max_length=32)
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    last: float = Field(default=0, ge=0)
    time_msc: int = Field(default=0, ge=0)
    timestamp: int = Field(default=0, ge=0)
    consumer_id: str = Field(default="mt5", max_length=40)

    @field_validator("symbol")
    @classmethod
    def normalize_tick_symbol(cls, value: str) -> str:
        return value.strip().upper()


class TickBatchIn(BaseModel):
    ticks: list[TickIn] = Field(min_length=1, max_length=200)


class SignalIn(BaseModel):
    symbol: str = Field(min_length=3, max_length=32)
    timeframe: Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
    direction: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "BUY_LIMIT", "SELL_LIMIT"] = Field(default="MARKET")
    entry: float = Field(gt=0)
    sl: float = Field(gt=0)
    tp: float = Field(default=0, ge=0)
    score: float = Field(default=0, ge=0, le=100)
    source: str = Field(default="api", max_length=40)
    risk_money: float = Field(default=25, gt=0, le=1000000)
    requested_lot: float = Field(default=0, ge=0, le=1000000)
    client_order_id: str | None = Field(default=None, min_length=8, max_length=64)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_limit_order(self) -> "SignalIn":
        if self.order_type == "BUY_LIMIT":
            if self.direction != "BUY":
                raise ValueError("BUY_LIMIT chỉ áp dụng cho hướng BUY")
            if self.sl >= self.entry:
                raise ValueError("BUY_LIMIT yêu cầu SL < Entry")
            if self.tp > 0 and self.tp <= self.entry:
                raise ValueError("BUY_LIMIT yêu cầu TP > Entry")
        elif self.order_type == "SELL_LIMIT":
            if self.direction != "SELL":
                raise ValueError("SELL_LIMIT chỉ áp dụng cho hướng SELL")
            if self.sl <= self.entry:
                raise ValueError("SELL_LIMIT yêu cầu SL > Entry")
            if self.tp > 0 and self.tp >= self.entry:
                raise ValueError("SELL_LIMIT yêu cầu TP < Entry")
        else:
            if (self.direction == "BUY" and self.sl >= self.entry) or (
                self.direction == "SELL" and self.sl <= self.entry
            ):
                raise ValueError("SL phải nằm đúng phía so với entry")
            if self.tp > 0:
                if self.direction == "BUY" and self.tp <= self.entry:
                    raise ValueError("BUY yêu cầu TP > Entry")
                if self.direction == "SELL" and self.tp >= self.entry:
                    raise ValueError("SELL yêu cầu TP < Entry")
        return self


class Mt5AccountInfoIn(BaseModel):
    account_id: str = Field(min_length=1, max_length=32)
    login: int = Field(ge=0)
    name: str = Field(default="", max_length=120)
    server: str = Field(default="", max_length=120)
    broker: str = Field(default="", max_length=120)
    currency: str = Field(default="USD", max_length=16)
    leverage: int = Field(default=0, ge=0)
    account_mode: str = Field(default="DEMO", max_length=24)
    trade_allowed: bool = True
    expert_allowed: bool = True
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    free_margin: float = 0.0
    margin_level: float = 0.0
    profit: float = 0.0
    positions_count: int = Field(default=0, ge=0)
    orders_count: int = Field(default=0, ge=0)
    timestamp: int = Field(default=0, ge=0)

    @field_validator("currency", "account_mode")
    @classmethod
    def normalize_upper_str(cls, value: str) -> str:
        return value.strip().upper()


class Mt5AccountItemIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    ticket: str | int | None = None
    symbol: str = Field(min_length=3, max_length=32)
    timeframe: str = Field(default="MT5", min_length=1, max_length=16)
    direction: Literal["BUY", "SELL"]
    entry: float = Field(gt=0)
    sl: float = Field(default=0, ge=0)
    tp: float = Field(default=0, ge=0)
    volume: float = Field(gt=0)
    profit: float = 0
    opened_at: float = Field(default=0, ge=0)
    is_pending: bool = False
    order_type: str = Field(default="", max_length=32)
    magic: int = 0
    comment: str = Field(default="", max_length=120)
    source: Literal["TRADE_V9", "MT5_EXTERNAL"] = "MT5_EXTERNAL"


class Mt5AccountSnapshotIn(BaseModel):
    items: list[Mt5AccountItemIn] = Field(default_factory=list, max_length=500)


class RealTradeItemIn(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    ticket: str | int | None = None
    symbol: str = Field(min_length=3, max_length=32)
    timeframe: str = Field(default="—", max_length=16)
    direction: str = Field(min_length=2, max_length=16)
    entry: float = Field(ge=0)
    sl: float = Field(default=0, ge=0)
    tp: float = Field(default=0, ge=0)
    volume: float = Field(gt=0)
    profit: float
    outcome: str = Field(default="CLOSED", max_length=40)
    opened_at: float = Field(default=0, ge=0)
    closed_at: float = Field(gt=0)

    @field_validator("symbol")
    @classmethod
    def normalize_real_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("direction")
    @classmethod
    def normalize_real_dir(cls, value: str) -> str:
        up = value.strip().upper()
        if "BUY" in up:
            return "BUY"
        if "SELL" in up:
            return "SELL"
        return up or "BUY"


class RealHistoryBatchIn(BaseModel):
    replace: bool = Field(default=False)
    trades: list[RealTradeItemIn] = Field(default_factory=list, max_length=500)


class ClosePositionIn(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    ticket: str | int | None = None
    position_id: str | None = Field(default=None, max_length=64)
    symbol: str | None = Field(default=None, max_length=32)
    direction: Literal["BUY", "SELL"] | None = None
    order_type: str | None = Field(default=None, max_length=32)
    volume: float | None = Field(default=None, ge=0)
    entry: float | None = Field(default=None, ge=0)
    source: str | None = Field(default=None, max_length=40)
    is_pending: bool | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_close_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        return cleaned or None


class CloseRequestAckIn(BaseModel):
    request_id: str = Field(min_length=1, max_length=64)
    position_id: str = Field(default="", max_length=64)
    ok: bool
    message: str = Field(default="", max_length=500)
    kind: Literal["close", "cancel"] = "close"


class ModifySltpIn(BaseModel):
    ticket: str | int
    symbol: str | None = Field(default=None, max_length=32)
    old_sl: float = Field(default=0, ge=0)
    old_tp: float = Field(default=0, ge=0)
    new_sl: float = Field(default=0, ge=0)
    new_tp: float = Field(default=0, ge=0)

    @field_validator("symbol")
    @classmethod
    def normalize_sltp_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        return cleaned or None


class SltpRequestAckIn(BaseModel):
    request_id: str = Field(min_length=1, max_length=64)
    ticket: str = Field(default="", max_length=64)
    ok: bool
    message: str = Field(default="", max_length=500)


class ModifyPendingIn(BaseModel):
    ticket: str | int
    symbol: str | None = Field(default=None, max_length=32)
    order_type: str | None = Field(default=None, max_length=32)
    old_entry: float = Field(default=0, ge=0)
    old_sl: float = Field(default=0, ge=0)
    old_tp: float = Field(default=0, ge=0)
    new_entry: float = Field(default=0, ge=0)
    new_sl: float = Field(default=0, ge=0)
    new_tp: float = Field(default=0, ge=0)

    @field_validator("symbol")
    @classmethod
    def normalize_pending_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        return cleaned or None


class PendingModifyAckIn(BaseModel):
    request_id: str = Field(min_length=1, max_length=64)
    ticket: str = Field(default="", max_length=64)
    ok: bool
    message: str = Field(default="", max_length=500)


class AckIn(BaseModel):
    signal_id: str
    status: Literal["PLACED", "REJECTED", "ERROR"]
    message: str = Field(default="", max_length=500)


class HeartbeatIn(BaseModel):
    component: str = Field(min_length=2, max_length=40)
    status: Literal["online", "warning", "offline"]
    details: str = Field(default="", max_length=500)


class ScanUpdateIn(BaseModel):
    component: str = Field(min_length=2, max_length=40)
    symbols: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("symbols")
    @classmethod
    def clean_symbols(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            item = value.strip().upper()[:48]
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned


class TodaySignalItem(BaseModel):
    symbol: str = Field(min_length=3, max_length=32)
    timeframe: Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
    direction: Literal["BUY", "SELL"]
    divergence: Literal["Reg", "Hid", "REG", "HID", "Regular", "Hidden"]
    signal_type: Literal["RT", "HIST"]
    signal_time: int = Field(gt=0)
    price1: float = Field(ge=0)
    price2: float = Field(ge=0)
    rsi1: float = Field(ge=0, le=100)
    rsi2: float = Field(ge=0, le=100)
    time1: int = Field(default=0, ge=0)
    time2: int = Field(default=0, ge=0)

    @field_validator("symbol")
    @classmethod
    def normalize_today_symbol(cls, value: str) -> str:
        return value.strip().upper()


class CandleItem(BaseModel):
    time: int = Field(gt=0)
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(default=0, ge=0)


class CandleBatchIn(BaseModel):
    symbol: str = Field(min_length=3, max_length=32)
    timeframe: Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
    digits: int = Field(default=5, ge=0, le=10)
    tick_size: float = Field(default=0.00001, gt=0)
    tick_value: float = Field(default=0, ge=0)
    volume_min: float = Field(default=0, ge=0)
    volume_max: float = Field(default=0, ge=0)
    volume_step: float = Field(default=0, ge=0)
    ask: float | None = Field(default=None, ge=0)
    ask_price: float | None = Field(default=None, ge=0)
    candles: list[CandleItem] = Field(min_length=1, max_length=1000)

    @field_validator("symbol")
    @classmethod
    def normalize_candle_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @property
    def resolved_ask(self) -> float | None:
        if self.ask is not None and self.ask > 0:
            return float(self.ask)
        if self.ask_price is not None and self.ask_price > 0:
            return float(self.ask_price)
        return None


class TodaySignalsIn(BaseModel):
    signals: list[TodaySignalItem] = Field(default_factory=list, max_length=200)


class CommissionItemIn(BaseModel):
    symbol: str = Field(min_length=3, max_length=32)
    commission_per_lot: float = Field(ge=0, le=1000000)
    swap_long: float = Field(default=0, ge=-1000000, le=1000000)
    swap_short: float = Field(default=0, ge=-1000000, le=1000000)
    contract_size: float = Field(default=0, ge=0, le=1000000000)
    tick_value: float = Field(default=0, ge=0, le=1000000)
    point_size: float = Field(default=0, gt=0, le=1000000)
    source_updated_at: int = Field(default=0, ge=0)

    @field_validator("symbol")
    @classmethod
    def normalize_commission_symbol(cls, value: str) -> str:
        return value.strip().upper()


class CommissionBatchIn(BaseModel):
    account_id: str = Field(min_length=1, max_length=32)
    items: list[CommissionItemIn] = Field(min_length=1, max_length=200)


class SettingsIn(BaseModel):
    auto_trade_enabled: bool | None = None
    trailing_enabled: bool | None = None
    breakeven_enabled: bool | None = None
    news_filter_enabled: bool | None = None
    news_block_before_min: int | None = Field(default=None, ge=0, le=120)
    news_block_after_min: int | None = Field(default=None, ge=0, le=120)
    news_events_json: str | None = Field(default=None, max_length=20000)
    time_filter_enabled: bool | None = None
    emergency_stop: bool | None = None
    emergency_close_all: bool | None = None
    telegram_notifications_enabled: bool | None = None

    risk_money: float | None = Field(default=None, gt=0, le=100000)
    max_positions: int | None = Field(default=None, ge=0, le=50)
    reward_risk_ratio: float | None = Field(default=None, ge=0.1, le=20)
    risk_tolerance_pct: float | None = Field(default=None, ge=0, le=5)

    commission_fallback: float | None = Field(default=None, ge=0, le=10000)
    spread_buffer_points: int | None = Field(default=None, ge=0, le=10000)

    trailing_trigger_rr: float | None = Field(default=None, ge=0, le=10)
    trailing_lock_rr: float | None = Field(default=None, ge=0, le=10)
    breakeven_trigger_rr: float | None = Field(default=None, ge=0, le=10)
    trailing_mode: Literal["static", "dynamic"] | None = None

    trading_start_hour: int | None = Field(default=None, ge=0, le=23)
    trading_end_hour: int | None = Field(default=None, ge=0, le=23)

    updated_by: str = Field(default="dashboard", max_length=40)

    @field_validator("news_events_json")
    @classmethod
    def validate_news_events_json(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip() or "[]"
        parsed = json.loads(cleaned)
        if not isinstance(parsed, list):
            raise ValueError("news_events_json phải là một JSON array [...]")
        return json.dumps(parsed, ensure_ascii=False)


# ============ Routes ============

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return FileResponse(BASE_DIR / "dashboard" / "index.html")


@app.get("/chart", response_class=HTMLResponse)
def chart_page():
    return FileResponse(BASE_DIR / "dashboard" / "chart.html")


@app.get("/api/auth/check", dependencies=[Depends(require_key)])
def auth_check():
    return {"ok": True}


@app.get("/api/health")
def health():
    t0 = time.perf_counter()
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "status": "ok",
            "service": "forex-ai",
            "database": "ok",
            "mode": "REAL_ONLY",
            "latency_ms": elapsed_ms,
            "time": utc_now(),
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database_error: {type(exc).__name__}")


def _settings_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in (
        "auto_trade_enabled", "trailing_enabled", "breakeven_enabled",
        "news_filter_enabled", "time_filter_enabled",
        "telegram_notifications_enabled", "emergency_stop", "emergency_close_all",
    ):
        if k in d:
            d[k] = bool(d[k])
    d["news_block_before_min"] = int(d.get("news_block_before_min") if d.get("news_block_before_min") is not None else 3)
    d["news_block_after_min"] = int(d.get("news_block_after_min") if d.get("news_block_after_min") is not None else 3)
    if not d.get("news_events_json"):
        d["news_events_json"] = "[]"
    return d


def _format_account_rows(rows: list[sqlite3.Row]) -> list[dict]:
    now_utc = datetime.now(timezone.utc)
    result: list[dict] = []
    for r in rows:
        item = dict(r)
        item["trade_allowed"] = bool(item.get("trade_allowed"))
        item["expert_allowed"] = bool(item.get("expert_allowed"))
        try:
            age = (now_utc - datetime.fromisoformat(item["updated_at"])).total_seconds()
        except Exception:
            age = 0.0
        item["age_seconds"] = int(max(0, age))
        item["stale"] = age > 30
        result.append(item)
    return result


@app.get("/api/settings", dependencies=[Depends(require_key)])
def get_settings():
    with db() as conn:
        row = conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
        if row is None:
            raise HTTPException(status_code=500, detail="Settings chưa khởi tạo")
        return {"ok": True, "settings": _settings_row_to_dict(row)}


@app.put("/api/settings", dependencies=[Depends(require_key)])
def update_settings(payload: SettingsIn):
    updates: dict[str, object] = {}
    for field, value in payload.model_dump(exclude_none=True).items():
        if field == "updated_by":
            continue
        if isinstance(value, bool):
            updates[field] = 1 if value else 0
        else:
            updates[field] = value

    if not updates:
        raise HTTPException(status_code=422, detail="Không có field nào để cập nhật")

    now = utc_now()
    updates["updated_at"] = now
    updates["updated_by"] = payload.updated_by

    set_clause = ", ".join(f"{k}=?" for k in updates.keys())
    values = list(updates.values())

    with db() as conn:
        conn.execute(f"UPDATE settings SET {set_clause} WHERE id=1", values)
        conn.execute(
            "INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
            ("settings_updated",
             f"by={payload.updated_by} fields={list(updates.keys())}",
             now),
        )
        row = conn.execute("SELECT * FROM settings WHERE id=1").fetchone()

    return {"ok": True, "settings": _settings_row_to_dict(row), "updated_at": now}


@app.get("/api/news/status")
def get_news_status():
    with db() as conn:
        row = conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
        s = _settings_row_to_dict(row) if row else {}
    status = evaluate_news_filter(s)
    return {"ok": True, **status, "server_time": utc_now()}


@app.get("/api/settings/commission", dependencies=[Depends(require_key)])
def get_commission_for_symbol(
    symbol: str = Query(min_length=3, max_length=32),
):
    sym = symbol.strip().upper()
    with db() as conn:
        row = conn.execute(
            "SELECT account_id, commission_per_lot, swap_long, swap_short, "
            "contract_size, tick_value, point_size, received_at "
            "FROM symbol_commissions WHERE symbol=? "
            "ORDER BY received_at DESC LIMIT 1",
            (sym,),
        ).fetchone()

        fallback_row = conn.execute(
            "SELECT commission_fallback FROM settings WHERE id=1"
        ).fetchone()
        fallback = float(fallback_row["commission_fallback"]) if fallback_row else 0.0

    if row is None:
        return {
            "ok": True,
            "symbol": sym,
            "has_data": False,
            "commission_per_lot": fallback,
            "source": "fallback",
        }

    return {
        "ok": True,
        "symbol": sym,
        "has_data": True,
        "commission_per_lot": float(row["commission_per_lot"]),
        "swap_long": float(row["swap_long"]),
        "swap_short": float(row["swap_short"]),
        "contract_size": float(row["contract_size"]),
        "tick_value": float(row["tick_value"]),
        "point_size": float(row["point_size"]),
        "account_id": row["account_id"],
        "received_at": row["received_at"],
        "source": "server_cache",
    }


def _get_latest_market_price(
    conn: sqlite3.Connection,
    symbol: str,
    timeframe: str | None = None,
) -> float | None:
    cached = _get_cached_tick(symbol, max_age_sec=15.0)
    if cached and cached.get("bid"):
        return float(cached["bid"])
    if timeframe:
        row = conn.execute(
            "SELECT close FROM candles WHERE symbol=? AND timeframe=? ORDER BY candle_time DESC LIMIT 1",
            (symbol, timeframe),
        ).fetchone()
        if row is not None:
            return float(row["close"])
    row = conn.execute(
        "SELECT close FROM candles WHERE symbol=? ORDER BY candle_time DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return float(row["close"]) if row is not None else None


def _get_latest_symbol_ask(
    conn: sqlite3.Connection,
    symbol: str,
    timeframe: str | None = None,
) -> float | None:
    cached = _get_cached_tick(symbol, max_age_sec=15.0)
    if cached and cached.get("ask"):
        return float(cached["ask"])
    if timeframe:
        row = conn.execute(
            "SELECT ask FROM candles WHERE symbol=? AND timeframe=? AND ask IS NOT NULL AND ask > 0 "
            "ORDER BY candle_time DESC LIMIT 1",
            (symbol, timeframe),
        ).fetchone()
        if row is not None:
            return float(row["ask"])
    row = conn.execute(
        "SELECT ask FROM candles WHERE symbol=? AND ask IS NOT NULL AND ask > 0 "
        "ORDER BY candle_time DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return float(row["ask"]) if row is not None else None


def _validate_limit_against_market(conn: sqlite3.Connection, payload: SignalIn) -> None:
    if payload.order_type not in ("BUY_LIMIT", "SELL_LIMIT"):
        return
    if payload.order_type == "BUY_LIMIT":
        market_price = (_get_latest_symbol_ask(conn, payload.symbol, payload.timeframe)
                        or _get_latest_market_price(conn, payload.symbol, payload.timeframe))
    else:
        market_price = _get_latest_market_price(conn, payload.symbol, payload.timeframe)
    if market_price is None:
        return
    if payload.order_type == "BUY_LIMIT" and payload.entry >= market_price:
        raise HTTPException(status_code=422, detail="BUY_LIMIT yêu cầu Entry thấp hơn giá hiện tại")
    if payload.order_type == "SELL_LIMIT" and payload.entry <= market_price:
        raise HTTPException(status_code=422, detail="SELL_LIMIT yêu cầu Entry cao hơn giá hiện tại")


def _insert_signal(
    conn: sqlite3.Connection,
    payload: SignalIn,
    source: str,
    status: str = "logged",
    signal_id: str | None = None,
) -> dict:
    created = utc_now()
    signal_id = signal_id or secrets.token_hex(12)
    conn.execute(
        "INSERT INTO signals(id,symbol,timeframe,direction,order_type,entry,sl,tp,score,risk_money,requested_lot,source,status,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (signal_id, payload.symbol, payload.timeframe, payload.direction, payload.order_type,
         payload.entry, payload.sl, payload.tp, payload.score, payload.risk_money, payload.requested_lot,
         source, status, created),
    )
    conn.execute(
        "INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
        ("signal_created", f"{payload.symbol} {payload.timeframe} {payload.order_type} {signal_id} ({source})", created),
    )
    return {
        "ok": True,
        "signal_id": signal_id,
        "order_type": payload.order_type,
        "status": status,
        "created_at": created,
    }


@app.post("/api/mt5/ticks", dependencies=[Depends(require_key)])
def ingest_tick(payload: TickIn):
    tick = _set_latest_tick(
        symbol=payload.symbol,
        bid=payload.bid,
        ask=payload.ask,
        last=payload.last,
        time_msc=payload.time_msc,
        source=payload.consumer_id,
    )
    return {"ok": True, "tick": tick}


@app.post("/api/mt5/ticks/batch", dependencies=[Depends(require_key)])
def ingest_tick_batch(payload: TickBatchIn):
    updated = [
        _set_latest_tick(
            symbol=item.symbol,
            bid=item.bid,
            ask=item.ask,
            last=item.last,
            time_msc=item.time_msc,
            source=item.consumer_id,
        )
        for item in payload.ticks
    ]
    return {"ok": True, "count": len(updated), "ticks": updated}


@app.get("/api/mt5/ticks", dependencies=[Depends(require_key)])
def get_latest_ticks(symbol: str | None = Query(default=None, max_length=32)):
    if symbol:
        sym = symbol.strip().upper()
        t = _get_cached_tick(sym, max_age_sec=120.0)
        return {"ok": True, "symbol": sym, "tick": t}
    return {"ok": True, "ticks": _get_all_cached_ticks(), "server_time": utc_now()}


@app.post("/api/signals", dependencies=[Depends(require_key)])
def create_signal(payload: SignalIn):
    with db() as conn:
        _validate_limit_against_market(conn, payload)
        if payload.source.startswith("div-"):
            existing = conn.execute(
                "SELECT id,status,created_at FROM signals WHERE source=?",
                (payload.source,),
            ).fetchone()
            if existing:
                return {"ok": True, "duplicate": True, "signal_id": existing["id"],
                        "status": existing["status"], "created_at": existing["created_at"]}
        return _insert_signal(conn, payload, payload.source, status="logged")


@app.post("/api/signals/realtime", dependencies=[Depends(require_key)])
def create_realtime_signal(payload: SignalIn):
    now_dt = datetime.now(timezone.utc).astimezone(_GMT7)
    now_ts = now_dt.timestamp()
    with db() as conn:
        settings_row = conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
        s = _settings_row_to_dict(settings_row) if settings_row else {}

        if s.get("emergency_stop"):
            return {"ok": True, "displayed": False, "reason": "emergency_stop"}

        if payload.source.startswith("div-"):
            existing = conn.execute(
                "SELECT id, status, created_at FROM signals WHERE source=?",
                (payload.source,),
            ).fetchone()
            if existing:
                return {
                    "ok": True,
                    "displayed": True,
                    "duplicate": True,
                    "signal_id": existing["id"],
                    "status": existing["status"],
                }

        news_status = evaluate_news_filter(s, now_ts=now_ts)
        notes: list[str] = []
        if not s.get("auto_trade_enabled", True):
            notes.append("auto_trade_disabled")
        if news_status["blocked"]:
            notes.append(news_status.get("reason") or "news_blocked")
            conn.execute(
                "INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
                ("signal_news_window",
                 f"{payload.symbol} {payload.timeframe} {payload.direction} ({payload.source}) - {news_status.get('detail')}",
                 utc_now()),
            )
        if s.get("time_filter_enabled", True):
            start_h = int(s.get("trading_start_hour", 7))
            end_h = int(s.get("trading_end_hour", 23))
            cur_h = now_dt.hour
            in_hours = (cur_h >= start_h and cur_h < end_h) if start_h <= end_h else (cur_h >= start_h or cur_h < end_h)
            if not in_hours:
                notes.append("outside_trading_hours")

        risk_money = float(s.get("risk_money", payload.risk_money))
        rr = float(s.get("reward_risk_ratio", 2.0))
        tp_val = payload.tp
        if tp_val <= 0 and rr > 0:
            sl_dist = abs(payload.entry - payload.sl)
            tp_val = (payload.entry + sl_dist * rr) if payload.direction == "BUY" else (payload.entry - sl_dist * rr)

        adjusted_payload = payload.model_copy(update={
            "risk_money": risk_money,
            "tp": tp_val,
        })
        created = _insert_signal(conn, adjusted_payload, payload.source, status="displayed")
        return {
            **created,
            "displayed": True,
            "execution_mode": "direct_gv",
            "filter_notes": notes,
        }


def _trade_block_reason(s: dict, now_dt: datetime) -> str | None:
    """Lý do đang chặn lệnh (emergency / tin / giờ giao dịch), None nếu được phép."""
    if s.get("emergency_stop"):
        return "emergency_stop"
    news_status = evaluate_news_filter(s, now_ts=now_dt.timestamp())
    if news_status["blocked"]:
        return news_status.get("reason") or "news_blocked"
    if s.get("time_filter_enabled", True):
        start_h = int(s.get("trading_start_hour", 7))
        end_h = int(s.get("trading_end_hour", 23))
        cur_h = now_dt.hour
        in_hours = (start_h <= cur_h < end_h) if start_h <= end_h else (cur_h >= start_h or cur_h < end_h)
        if not in_hours:
            return "outside_trading_hours"
    return None


@app.post("/api/chart/order", dependencies=[Depends(require_key)])
def chart_order(payload: SignalIn):
    if payload.requested_lot <= 0:
        raise HTTPException(status_code=422, detail="Thiếu lot đã tính từ Risk và khoảng Entry-SL")
    signal_id = (hashlib.sha256(payload.client_order_id.encode()).hexdigest()[:24]
                 if payload.client_order_id else None)
    with db() as conn:
        if signal_id:
            existing = conn.execute(
                "SELECT status, created_at, order_type FROM signals WHERE id=?", (signal_id,)
            ).fetchone()
            if existing:
                return {"ok": True, "duplicate": True, "signal_id": signal_id,
                        "order_type": existing["order_type"], "status": existing["status"],
                        "created_at": existing["created_at"]}
        settings_row = conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
        s = _settings_row_to_dict(settings_row) if settings_row else {}
        reason = _trade_block_reason(s, datetime.now(timezone.utc).astimezone(_GMT7))
        if reason:
            raise HTTPException(status_code=409, detail=f"Đang chặn giao dịch: {reason}")
        _validate_limit_against_market(conn, payload)
        return _insert_signal(conn, payload, "web-chart", status="pending", signal_id=signal_id)


@app.get("/api/mt5/next", dependencies=[Depends(require_key)])
def next_signal(consumer: str = Query(default="mt5", min_length=2, max_length=40)):
    now_dt = datetime.now(timezone.utc).astimezone(_GMT7)
    stale_cutoff = (datetime.now(timezone.utc) - timedelta(seconds=STALE_SECONDS)).isoformat()

    with db() as conn:
        settings_row = conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
        s = _settings_row_to_dict(settings_row) if settings_row else {}

        reason = _trade_block_reason(s, now_dt)
        if reason:
            # Hủy lệnh đang chờ để không bị gửi trễ khi hết chặn
            conn.execute(
                "UPDATE signals SET status='expired', ack_status='REJECTED', ack_message=? "
                "WHERE status='pending' AND source='web-chart'",
                (f"Bị chặn: {reason}",),
            )
            return {"ok": True, "signal": None, "reason": reason, "consumer": consumer}

        conn.execute(
            "UPDATE signals SET status='expired', ack_status='ERROR', "
            "ack_message='Không nhận được ACK - kiểm tra MT5' "
            "WHERE status='claimed' AND source='web-chart' AND claimed_at < ?",
            (stale_cutoff,),
        )

        conn.execute(
            "UPDATE signals SET status='expired', ack_status='ERROR', ack_message='Hết hạn chờ MT5' "
            "WHERE status='pending' AND source='web-chart' AND created_at < ?",
            (stale_cutoff,),
        )

        row = conn.execute(
            "SELECT * FROM signals WHERE status='pending' AND source='web-chart' "
            "ORDER BY created_at ASC LIMIT 1"
        ).fetchone()

        if row is None:
            return {"ok": True, "signal": None, "reason": "no_manual_order", "consumer": consumer}

        claimed_at = utc_now()
        conn.execute(
            "UPDATE signals SET status='claimed', claimed_by=?, claimed_at=? WHERE id=? AND status='pending'",
            (consumer, claimed_at, row["id"]),
        )
        conn.execute(
            "INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
            ("manual_signal_claimed", f"{row['symbol']} {row['order_type']} {row['id']} by {consumer}", claimed_at),
        )

        return {
            "ok": True,
            "consumer": consumer,
            "signal": {
                "id": row["id"],
                "symbol": row["symbol"],
                "timeframe": row["timeframe"],
                "direction": row["direction"],
                "order_type": row["order_type"],
                "entry": float(row["entry"]),
                "sl": float(row["sl"]),
                "tp": float(row["tp"]),
                "score": float(row["score"]),
                "risk_money": float(row["risk_money"]),
                "requested_lot": float(row["requested_lot"]),
                "source": row["source"],
                "created_at": row["created_at"],
            },
        }


@app.post("/api/mt5/ack", dependencies=[Depends(require_key)])
def acknowledge(payload: AckIn):
    now = utc_now()
    with db() as conn:
        cur = conn.execute(
            "UPDATE signals SET status='completed',ack_at=?,ack_status=?,ack_message=? "
            "WHERE id=? AND status IN ('claimed','pending','displayed','logged','expired')",
            (now, payload.status, payload.message, payload.signal_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=404, detail="Không tìm thấy tín hiệu cần xác nhận")
        conn.execute("INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
                     ("mt5_ack", f"{payload.signal_id}: {payload.status} {payload.message}", now))
    return {"ok": True}


def _cleanup_recently_acked():
    now = time.time()
    expired = [k for k, t in _recently_acked.items() if now - t > _ACK_COOLDOWN]
    for k in expired:
        _recently_acked.pop(k, None)


@app.post("/api/mt5/account-info", dependencies=[Depends(require_key)])
def update_mt5_account_info(payload: Mt5AccountInfoIn):
    now = utc_now()
    acc_id = payload.account_id.strip()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO mt5_account_info(
                account_id, login, name, server, broker, currency, leverage, account_mode,
                trade_allowed, expert_allowed, balance, equity, margin, free_margin,
                margin_level, profit, positions_count, orders_count, source_timestamp, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(account_id) DO UPDATE SET
                login=excluded.login,
                name=excluded.name,
                server=excluded.server,
                broker=excluded.broker,
                currency=excluded.currency,
                leverage=excluded.leverage,
                account_mode=excluded.account_mode,
                trade_allowed=excluded.trade_allowed,
                expert_allowed=excluded.expert_allowed,
                balance=excluded.balance,
                equity=excluded.equity,
                margin=excluded.margin,
                free_margin=excluded.free_margin,
                margin_level=excluded.margin_level,
                profit=excluded.profit,
                positions_count=excluded.positions_count,
                orders_count=excluded.orders_count,
                source_timestamp=excluded.source_timestamp,
                updated_at=excluded.updated_at
            """,
            (
                acc_id,
                payload.login,
                payload.name,
                payload.server,
                payload.broker,
                payload.currency,
                payload.leverage,
                payload.account_mode,
                1 if payload.trade_allowed else 0,
                1 if payload.expert_allowed else 0,
                payload.balance,
                payload.equity,
                payload.margin,
                payload.free_margin,
                payload.margin_level,
                payload.profit,
                payload.positions_count,
                payload.orders_count,
                payload.timestamp,
                now,
            ),
        )
    return {"ok": True, "account_id": acc_id, "updated_at": now}


@app.get("/api/mt5/account-info", dependencies=[Depends(require_key)])
def get_mt5_account_info(account_id: str | None = Query(default=None, max_length=32)):
    with db() as conn:
        if account_id:
            rows = conn.execute(
                "SELECT * FROM mt5_account_info WHERE account_id=?",
                (account_id.strip(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM mt5_account_info ORDER BY updated_at DESC"
            ).fetchall()
    return {"ok": True, "accounts": _format_account_rows(rows), "server_time": utc_now()}


@app.post("/api/mt5/account-snapshot", dependencies=[Depends(require_key)])
def update_mt5_account_snapshot(payload: Mt5AccountSnapshotIn):
    now = utc_now()
    _cleanup_recently_acked()
    with db() as conn:
        conn.execute("DELETE FROM mt5_account_items")
        if payload.items:
            filtered = [
                item for item in payload.items
                if str(item.id) not in _recently_acked
                and (str(item.ticket) if item.ticket is not None else "") not in _recently_acked
            ]
            if filtered:
                conn.executemany(
                    "INSERT INTO mt5_account_items(id,ticket,symbol,timeframe,direction,entry,sl,tp,volume,profit,"
                    "opened_at,is_pending,order_type,magic,comment,source,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            item.id,
                            str(item.ticket).strip() if item.ticket is not None else item.id,
                            item.symbol.upper(),
                            item.timeframe,
                            item.direction,
                            item.entry,
                            item.sl,
                            item.tp,
                            item.volume,
                            item.profit,
                            item.opened_at,
                            1 if item.is_pending else 0,
                            item.order_type,
                            item.magic,
                            item.comment,
                            item.source,
                            now,
                        )
                        for item in filtered
                    ],
                )
        conn.execute(
            "INSERT OR REPLACE INTO heartbeats(component,status,details,updated_at) VALUES(?,?,?,?)",
            ("mt5-account-snapshot", "online", f"{len(payload.items)} lệnh/vị thế", now),
        )
    return {"ok": True, "items": len(payload.items), "updated_at": now}


def _find_matching_item(
    conn: sqlite3.Connection,
    table: str,
    pos_id: str,
    want_pending: int | None,
    symbol: str | None,
    direction: str | None,
):
    if pos_id:
        if want_pending is None:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE id=? OR ticket=?",
                (pos_id, pos_id),
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE (id=? OR ticket=?) AND is_pending=?",
                (pos_id, pos_id, want_pending),
            ).fetchone()
        return row

    if symbol:
        clauses = ["symbol=?"]
        params: list = [symbol]
        if want_pending is not None:
            clauses.append("is_pending=?")
            params.append(want_pending)
        if direction:
            clauses.append("direction=?")
            params.append(direction)
        sql = f"SELECT * FROM {table} WHERE " + " AND ".join(clauses) + " ORDER BY opened_at ASC LIMIT 1"
        return conn.execute(sql, params).fetchone()
    return None


def _enqueue_close_position(payload: ClosePositionIn, kind: Literal["close", "cancel"]) -> dict:
    ticket_str = str(payload.ticket).strip() if payload.ticket is not None else ""
    pos_id = (payload.position_id or payload.id or ticket_str or "").strip()
    if not pos_id and not payload.symbol:
        raise HTTPException(status_code=422, detail="Thiếu thông tin vị thế/lệnh chờ cần đóng/hủy")

    if payload.is_pending is not None:
        want_pending: int | None = 1 if payload.is_pending else 0
    elif kind == "cancel":
        want_pending = 1
    else:
        want_pending = 0

    now = utc_now()
    with db() as conn:
        row = _find_matching_item(conn, "mt5_account_items", pos_id, want_pending, payload.symbol, payload.direction)
        if row is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy vị thế/lệnh chờ trên MT5")

        resolved_id = row["id"]
        resolved_ticket = (row["ticket"] if row["ticket"] else "") or ticket_str or resolved_id
        resolved_sym = row["symbol"]
        resolved_dir = row["direction"]
        resolved_pending = 1 if row["is_pending"] else 0
        resolved_kind = "cancel" if resolved_pending else kind
        resolved_order_type = (payload.order_type or row["order_type"] or (
            ("BUY_LIMIT" if resolved_dir == "BUY" else "SELL_LIMIT") if resolved_pending else "MARKET"
        )).strip().upper()

        # Chỉ coi là trùng khi đúng cùng một vị thế/ticket
        existing = conn.execute(
            "SELECT request_id FROM close_requests WHERE status='pending' AND kind=? "
            "AND (position_id=? OR ticket=?)",
            (resolved_kind, resolved_id, resolved_ticket),
        ).fetchone()
        if existing:
            return {
                "ok": True,
                "request_id": existing["request_id"],
                "status": "pending",
                "kind": resolved_kind,
                "order_type": resolved_order_type,
                "message": "Đã có yêu cầu đang chờ xử lý",
            }

        request_id = secrets.token_hex(10)
        conn.execute(
            "INSERT INTO close_requests(request_id,position_id,ticket,symbol,direction,status,created_at,kind,is_pending,order_type) "
            "VALUES(?,?,?,?,?,'pending',?,?,?,?)",
            (request_id, resolved_id, resolved_ticket, resolved_sym, resolved_dir, now,
             resolved_kind, resolved_pending, resolved_order_type),
        )
        verb = "Hủy" if resolved_kind == "cancel" else "Đóng"
        conn.execute(
            "INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
            (
                f"{resolved_kind}_requested",
                f"{verb} {resolved_sym} {resolved_dir} {resolved_order_type} ({resolved_id or resolved_ticket or request_id})",
                now,
            ),
        )
    return {
        "ok": True,
        "request_id": request_id,
        "status": "pending",
        "kind": resolved_kind,
        "order_type": resolved_order_type,
        "message": f"Đã gửi yêu cầu {verb.lower()} {resolved_sym} {resolved_order_type}".strip(),
    }


@app.post("/api/chart/close", dependencies=[Depends(require_key)])
def chart_close_position(payload: ClosePositionIn):
    return _enqueue_close_position(payload, kind="close")


@app.post("/api/chart/cancel", dependencies=[Depends(require_key)])
def chart_cancel_pending(payload: ClosePositionIn):
    return _enqueue_close_position(payload, kind="cancel")


def _fetch_pending_close_requests() -> list[dict]:
    now = time.time()
    cutoff = datetime.fromtimestamp(now - CLOSE_REQUEST_TTL, timezone.utc).isoformat()
    with db() as conn:
        conn.execute(
            "UPDATE close_requests SET status='expired',acked_at=?,message='Hết hạn chờ MT5' "
            "WHERE status='pending' AND created_at < ?",
            (utc_now(), cutoff),
        )
        return [dict(r) for r in conn.execute(
            "SELECT request_id, position_id, ticket, symbol, direction, kind, is_pending, order_type "
            "FROM close_requests WHERE status='pending' ORDER BY created_at ASC LIMIT 20"
        ).fetchall()]


@app.get("/api/mt5/close-requests", dependencies=[Depends(require_key)])
def get_close_requests():
    return {"ok": True, "close_requests": _fetch_pending_close_requests()}


@app.post("/api/mt5/close-requests/ack", dependencies=[Depends(require_key)])
def ack_close_request(payload: CloseRequestAckIn):
    now = utc_now()
    with db() as conn:
        req_row = conn.execute(
            "SELECT * FROM close_requests WHERE request_id=?", (payload.request_id,)
        ).fetchone()
        if req_row is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy close request")
        conn.execute(
            "UPDATE close_requests SET status='acked',acked_at=?,ok=?,message=? WHERE request_id=?",
            (now, 1 if payload.ok else 0, payload.message, payload.request_id),
        )
        pos_target = (payload.position_id or (req_row["position_id"] if req_row else "") or "").strip()
        ticket_target = ((req_row["ticket"] if req_row and req_row["ticket"] else "") or "").strip()
        if payload.ok:
            acked_time = time.time()
            if pos_target:
                _recently_acked[pos_target] = acked_time
            if ticket_target:
                _recently_acked[ticket_target] = acked_time
            if pos_target and ticket_target:
                conn.execute(
                    "DELETE FROM mt5_account_items WHERE id=? OR ticket=? OR id=? OR ticket=?",
                    (pos_target, pos_target, ticket_target, ticket_target),
                )
            elif pos_target:
                conn.execute("DELETE FROM mt5_account_items WHERE id=? OR ticket=?", (pos_target, pos_target))
            elif ticket_target:
                conn.execute("DELETE FROM mt5_account_items WHERE id=? OR ticket=?", (ticket_target, ticket_target))
        conn.execute(
            "INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
            ("close_ack",
             f"{payload.request_id} [{payload.kind}] ({pos_target or ticket_target}): ok={payload.ok} {payload.message}".strip(),
             now),
        )
    return {"ok": True}


@app.post("/api/mt5/modify-sltp", dependencies=[Depends(require_key)])
def modify_sltp(payload: ModifySltpIn):
    ticket_str = str(payload.ticket).strip()
    if not ticket_str:
        raise HTTPException(status_code=422, detail="Thiếu ticket")
    if payload.new_sl <= 0 and payload.new_tp <= 0:
        raise HTTPException(status_code=422, detail="Cần ít nhất SL hoặc TP mới")
    now = utc_now()
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM mt5_account_items WHERE (id=? OR ticket=?) AND is_pending=0",
            (ticket_str, ticket_str),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy vị thế đang mở với ticket này")
        sym = row["symbol"]
        entry = float(row["entry"])
        direction = row["direction"]
        # So với giá thị trường (không phải Entry) để cho phép hòa vốn / khóa lãi
        if direction == "BUY":
            ref = _get_latest_market_price(conn, sym) or entry
        else:
            ref = _get_latest_symbol_ask(conn, sym) or entry
        sl_changed = payload.new_sl > 0 and abs(payload.new_sl - float(row["sl"] or 0)) > 1e-10
        tp_changed = payload.new_tp > 0 and abs(payload.new_tp - float(row["tp"] or 0)) > 1e-10
        if sl_changed:
            if direction == "BUY" and payload.new_sl >= ref:
                raise HTTPException(status_code=422, detail="SL BUY phải thấp hơn giá hiện tại")
            if direction == "SELL" and payload.new_sl <= ref:
                raise HTTPException(status_code=422, detail="SL SELL phải cao hơn giá hiện tại")
        if tp_changed:
            if direction == "BUY" and payload.new_tp <= ref:
                raise HTTPException(status_code=422, detail="TP BUY phải cao hơn giá hiện tại")
            if direction == "SELL" and payload.new_tp >= ref:
                raise HTTPException(status_code=422, detail="TP SELL phải thấp hơn giá hiện tại")

        existing = conn.execute(
            "SELECT request_id FROM sltp_requests WHERE status='pending' AND ticket=?",
            (ticket_str,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE sltp_requests SET new_sl=?, new_tp=?, created_at=? WHERE request_id=?",
                (payload.new_sl, payload.new_tp, now, existing["request_id"]),
            )
            request_id = existing["request_id"]
        else:
            request_id = secrets.token_hex(10)
            conn.execute(
                "INSERT INTO sltp_requests(request_id,ticket,symbol,old_sl,old_tp,new_sl,new_tp,status,created_at) "
                "VALUES(?,?,?,?,?,?,?,'pending',?)",
                (request_id, ticket_str, sym, payload.old_sl, payload.old_tp,
                 payload.new_sl, payload.new_tp, now),
            )
        conn.execute(
            "INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
            ("sltp_requested",
             f"ticket={ticket_str} {sym} SL {payload.old_sl}->{payload.new_sl} TP {payload.old_tp}->{payload.new_tp}",
             now),
        )
    return {"ok": True, "request_id": request_id, "status": "pending", "ticket": ticket_str}


@app.get("/api/mt5/sltp-requests", dependencies=[Depends(require_key)])
def get_sltp_requests():
    now = time.time()
    cutoff = datetime.fromtimestamp(now - SLTP_REQUEST_TTL, timezone.utc).isoformat()
    with db() as conn:
        conn.execute(
            "UPDATE sltp_requests SET status='expired',acked_at=?,message='Hết hạn chờ MT5' "
            "WHERE status='pending' AND created_at < ?",
            (utc_now(), cutoff),
        )
        rows = [dict(r) for r in conn.execute(
            "SELECT request_id, ticket, symbol, old_sl, old_tp, new_sl, new_tp "
            "FROM sltp_requests WHERE status='pending' ORDER BY created_at ASC LIMIT 20"
        ).fetchall()]
    return {"ok": True, "sltp_requests": rows}


@app.post("/api/mt5/sltp-requests/ack", dependencies=[Depends(require_key)])
def ack_sltp_request(payload: SltpRequestAckIn):
    now = utc_now()
    with db() as conn:
        req_row = conn.execute(
            "SELECT * FROM sltp_requests WHERE request_id=?", (payload.request_id,)
        ).fetchone()
        if req_row is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy SLTP request")
        conn.execute(
            "UPDATE sltp_requests SET status='acked',acked_at=?,ok=?,message=? WHERE request_id=?",
            (now, 1 if payload.ok else 0, payload.message, payload.request_id),
        )
        conn.execute(
            "INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
            ("sltp_ack", f"{payload.request_id} ticket={payload.ticket}: ok={payload.ok} {payload.message}".strip(), now),
        )
    return {"ok": True}


def _expire_stale_pending_modifies(conn: sqlite3.Connection) -> None:
    cutoff = datetime.fromtimestamp(time.time() - PENDING_MODIFY_TTL, timezone.utc).isoformat()
    conn.execute(
        "UPDATE pending_modify_requests SET status='expired',acked_at=?,message='Hết hạn chờ MT5' "
        "WHERE status='pending' AND created_at < ?",
        (utc_now(), cutoff),
    )


@app.post("/api/mt5/modify-pending", dependencies=[Depends(require_key)])
def modify_pending(payload: ModifyPendingIn):
    ticket_str = str(payload.ticket).strip()
    if not ticket_str:
        raise HTTPException(status_code=422, detail="Thiếu ticket")
    if payload.new_entry <= 0 and payload.new_sl <= 0 and payload.new_tp <= 0:
        raise HTTPException(status_code=422, detail="Cần ít nhất Entry, SL hoặc TP mới")

    now = utc_now()
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM mt5_account_items WHERE (id=? OR ticket=?) AND is_pending=1",
            (ticket_str, ticket_str),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy lệnh chờ với ticket này")

        sym = row["symbol"]
        order_type = (payload.order_type or row["order_type"] or "").strip().upper()
        direction = row["direction"]

        entry_for_check = payload.new_entry if payload.new_entry > 0 else float(row["entry"])
        sl_for_check = payload.new_sl if payload.new_sl > 0 else float(row["sl"])
        tp_for_check = payload.new_tp if payload.new_tp > 0 else float(row["tp"])

        market_price = _get_latest_market_price(conn, sym, row["timeframe"] if row["timeframe"] != "MT5" else None)

        if order_type == "BUY_LIMIT" or (not order_type and direction == "BUY"):
            if market_price is not None and entry_for_check >= market_price:
                raise HTTPException(status_code=422, detail="BUY LIMIT yêu cầu Entry thấp hơn giá hiện tại")
            if sl_for_check > 0 and sl_for_check >= entry_for_check:
                raise HTTPException(status_code=422, detail="BUY LIMIT yêu cầu SL < Entry")
            if tp_for_check > 0 and tp_for_check <= entry_for_check:
                raise HTTPException(status_code=422, detail="BUY LIMIT yêu cầu TP > Entry")
        elif order_type == "SELL_LIMIT" or (not order_type and direction == "SELL"):
            if market_price is not None and entry_for_check <= market_price:
                raise HTTPException(status_code=422, detail="SELL LIMIT yêu cầu Entry cao hơn giá hiện tại")
            if sl_for_check > 0 and sl_for_check <= entry_for_check:
                raise HTTPException(status_code=422, detail="SELL LIMIT yêu cầu SL > Entry")
            if tp_for_check > 0 and tp_for_check >= entry_for_check:
                raise HTTPException(status_code=422, detail="SELL LIMIT yêu cầu TP < Entry")

        old_entry = payload.old_entry if payload.old_entry > 0 else float(row["entry"])
        old_sl = payload.old_sl if payload.old_sl > 0 else float(row["sl"])
        old_tp = payload.old_tp if payload.old_tp > 0 else float(row["tp"])

        existing = conn.execute(
            "SELECT request_id FROM pending_modify_requests WHERE status='pending' AND ticket=?",
            (ticket_str,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE pending_modify_requests SET new_entry=?, new_sl=?, new_tp=?, created_at=? "
                "WHERE request_id=?",
                (payload.new_entry, payload.new_sl, payload.new_tp, now, existing["request_id"]),
            )
            request_id = existing["request_id"]
        else:
            request_id = secrets.token_hex(10)
            conn.execute(
                "INSERT INTO pending_modify_requests(request_id,ticket,symbol,order_type,"
                "old_entry,old_sl,old_tp,new_entry,new_sl,new_tp,status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?)",
                (request_id, ticket_str, sym, order_type,
                 old_entry, old_sl, old_tp,
                 payload.new_entry, payload.new_sl, payload.new_tp, now),
            )
        conn.execute(
            "INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
            ("pending_modify_requested",
             f"ticket={ticket_str} {sym} {order_type} "
             f"Entry {old_entry}->{payload.new_entry} "
             f"SL {old_sl}->{payload.new_sl} "
             f"TP {old_tp}->{payload.new_tp}",
             now),
        )
    return {
        "ok": True,
        "request_id": request_id,
        "status": "pending",
        "ticket": ticket_str,
        "symbol": sym,
        "order_type": order_type,
    }


@app.get("/api/mt5/pending-modify-requests", dependencies=[Depends(require_key)])
def get_pending_modify_requests():
    with db() as conn:
        _expire_stale_pending_modifies(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT request_id, ticket, symbol, order_type, "
            "old_entry, old_sl, old_tp, new_entry, new_sl, new_tp "
            "FROM pending_modify_requests WHERE status='pending' ORDER BY created_at ASC LIMIT 20"
        ).fetchall()]
    return {"ok": True, "pending_modify_requests": rows}


@app.get("/api/mt5/pending-modify-requests/{request_id}", dependencies=[Depends(require_key)])
def get_pending_modify_request_status(request_id: str):
    req_id = request_id.strip()
    if not req_id:
        raise HTTPException(status_code=422, detail="Thiếu request_id")
    with db() as conn:
        _expire_stale_pending_modifies(conn)
        row = conn.execute(
            "SELECT request_id, ticket, symbol, order_type, "
            "old_entry, old_sl, old_tp, new_entry, new_sl, new_tp, "
            "status, created_at, acked_at, ok, message "
            "FROM pending_modify_requests WHERE request_id=?",
            (req_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy pending modify request")
        data = dict(row)
        data["ok"] = None if data["ok"] is None else bool(data["ok"])
    return {"ok": True, "request": data}


@app.post("/api/mt5/pending-modify-requests/ack", dependencies=[Depends(require_key)])
def ack_pending_modify_request(payload: PendingModifyAckIn):
    now = utc_now()
    with db() as conn:
        req_row = conn.execute(
            "SELECT * FROM pending_modify_requests WHERE request_id=?", (payload.request_id,)
        ).fetchone()
        if req_row is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy pending modify request")
        conn.execute(
            "UPDATE pending_modify_requests SET status='acked',acked_at=?,ok=?,message=? "
            "WHERE request_id=?",
            (now, 1 if payload.ok else 0, payload.message, payload.request_id),
        )
        if payload.ok:
            ticket_target = (payload.ticket or req_row["ticket"] or "").strip()
            if ticket_target:
                if float(req_row["new_entry"]) > 0:
                    conn.execute(
                        "UPDATE mt5_account_items SET entry=?, updated_at=? WHERE (id=? OR ticket=?) AND is_pending=1",
                        (float(req_row["new_entry"]), now, ticket_target, ticket_target),
                    )
                if float(req_row["new_sl"]) > 0:
                    conn.execute(
                        "UPDATE mt5_account_items SET sl=?, updated_at=? WHERE (id=? OR ticket=?) AND is_pending=1",
                        (float(req_row["new_sl"]), now, ticket_target, ticket_target),
                    )
                if float(req_row["new_tp"]) > 0:
                    conn.execute(
                        "UPDATE mt5_account_items SET tp=?, updated_at=? WHERE (id=? OR ticket=?) AND is_pending=1",
                        (float(req_row["new_tp"]), now, ticket_target, ticket_target),
                    )
        conn.execute(
            "INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
            ("pending_modify_ack",
             f"{payload.request_id} ticket={payload.ticket}: ok={payload.ok} {payload.message}".strip(),
             now),
        )
    return {"ok": True}


def _stable_trade_id(item: RealTradeItemIn) -> str:
    if item.id:
        return item.id
    if item.ticket is not None:
        return str(item.ticket).strip()
    raw = f"{item.symbol}|{item.direction}|{int(item.closed_at)}|{item.entry}|{item.volume}|{item.profit}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"real-{digest}"


@app.post("/api/mt5/history", dependencies=[Depends(require_key)])
def update_real_history(payload: RealHistoryBatchIn):
    now = utc_now()
    with db() as conn:
        if payload.replace:
            conn.execute("DELETE FROM real_history")
        for item in payload.trades:
            ticket_str = str(item.ticket).strip() if item.ticket is not None else ""
            row_id = _stable_trade_id(item).strip()
            conn.execute(
                "INSERT OR REPLACE INTO real_history(id,ticket,symbol,timeframe,direction,entry,sl,tp,volume,profit,outcome,opened_at,closed_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row_id, ticket_str or row_id, item.symbol, item.timeframe or "—", item.direction,
                 item.entry, item.sl, item.tp, item.volume, item.profit, item.outcome or "CLOSED",
                 item.opened_at, item.closed_at, now),
            )
    return {"ok": True, "count": len(payload.trades), "updated_at": now}


@app.post("/api/heartbeat", dependencies=[Depends(require_key)])
def heartbeat(payload: HeartbeatIn):
    with db() as conn:
        conn.execute(
            "INSERT INTO heartbeats(component,status,details,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(component) DO UPDATE SET status=excluded.status,details=excluded.details,updated_at=excluded.updated_at",
            (payload.component, payload.status, payload.details, utc_now()),
        )
    return {"ok": True}


@app.post("/api/scans", dependencies=[Depends(require_key)])
def update_scans(payload: ScanUpdateIn):
    now = utc_now()
    symbols_json = json.dumps(payload.symbols, ensure_ascii=True)
    with db() as conn:
        conn.execute(
            "INSERT INTO scan_status(component,symbols_json,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(component) DO UPDATE SET symbols_json=excluded.symbols_json,updated_at=excluded.updated_at",
            (payload.component, symbols_json, now),
        )
    return {"ok": True, "component": payload.component, "symbol_count": len(payload.symbols),
            "updated_at": now}


@app.post("/api/today-signals", dependencies=[Depends(require_key)])
def update_today_signals(payload: TodaySignalsIn, background_tasks: BackgroundTasks):
    now = utc_now()
    new_rt_signals = []
    with db() as conn:
        for item in payload.signals:
            key = "|".join((item.symbol, item.timeframe, item.direction,
                            item.divergence, item.signal_type, str(item.signal_time)))
            cur = conn.execute(
                "INSERT OR IGNORE INTO today_signals(signal_key,symbol,timeframe,direction,divergence,signal_type,"
                "signal_time,price1,price2,rsi1,rsi2,time1,time2,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, item.symbol, item.timeframe, item.direction, item.divergence,
                 item.signal_type, item.signal_time, item.price1, item.price2,
                 item.rsi1, item.rsi2, item.time1, item.time2, now),
            )
            if cur.rowcount == 1 and item.signal_type == "RT":
                new_rt_signals.append(item)

    with db() as conn:
        telegram_enabled_row = conn.execute(
            "SELECT telegram_notifications_enabled FROM settings WHERE id=1"
        ).fetchone()
        telegram_enabled = bool(telegram_enabled_row["telegram_notifications_enabled"]) if telegram_enabled_row else True

    # Gửi Telegram sau khi đã trả phản hồi cho EA
    telegram_queued = 0
    if telegram_enabled:
        for item in new_rt_signals:
            background_tasks.add_task(send_telegram_rt_signal, item)
            telegram_queued += 1
    return {
        "ok": True,
        "count": len(payload.signals),
        "new_realtime": len(new_rt_signals),
        "telegram_queued": telegram_queued,
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "telegram_enabled": telegram_enabled,
        "updated_at": now,
    }


@app.post("/api/mt5/candles", dependencies=[Depends(require_key)])
def update_candles(payload: CandleBatchIn):
    now = utc_now()
    ask_val = payload.resolved_ask
    rows = []
    for candle in payload.candles:
        if candle.high < max(candle.open, candle.close, candle.low) or candle.low > min(candle.open, candle.close, candle.high):
            raise HTTPException(status_code=422, detail="OHLC không hợp lệ")
        rows.append((
            payload.symbol, payload.timeframe, candle.time, candle.open,
            candle.high, candle.low, candle.close, candle.volume,
            payload.digits, payload.tick_size, payload.tick_value,
            payload.volume_min, payload.volume_max, payload.volume_step,
            ask_val,
        ))

    prune_key = (payload.symbol, payload.timeframe)
    now_mono = time.monotonic()
    should_prune = (now_mono - _last_candle_prune_ts.get(prune_key, 0.0)) >= 30.0

    with db() as conn:
        conn.executemany(
            "INSERT INTO candles(symbol,timeframe,candle_time,open,high,low,close,volume,digits,tick_size,tick_value,volume_min,volume_max,volume_step,ask) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,timeframe,candle_time) DO UPDATE SET "
            "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,"
            "digits=excluded.digits,tick_size=excluded.tick_size,tick_value=excluded.tick_value,"
            "volume_min=excluded.volume_min,volume_max=excluded.volume_max,volume_step=excluded.volume_step,"
            "ask=COALESCE(excluded.ask, candles.ask)",
            rows,
        )
        if should_prune:
            _last_candle_prune_ts[prune_key] = now_mono
            conn.execute(
                "DELETE FROM candles WHERE symbol=? AND timeframe=? AND candle_time NOT IN "
                "(SELECT candle_time FROM candles WHERE symbol=? AND timeframe=? ORDER BY candle_time DESC LIMIT 2000)",
                (payload.symbol, payload.timeframe, payload.symbol, payload.timeframe),
            )
    return {
        "ok": True,
        "symbol": payload.symbol,
        "timeframe": payload.timeframe,
        "ask": ask_val,
        "ask_price": ask_val,
        "received": len(rows),
        "updated_at": now,
    }


@app.post("/api/mt5/commissions", dependencies=[Depends(require_key)])
def update_commissions(payload: CommissionBatchIn):
    received = utc_now()
    rows = [
        (payload.account_id, item.symbol, item.commission_per_lot,
         item.swap_long, item.swap_short, item.contract_size,
         item.tick_value, item.point_size, item.source_updated_at, received)
        for item in payload.items
    ]
    with db() as conn:
        conn.executemany(
            "INSERT INTO symbol_commissions(account_id,symbol,commission_per_lot,swap_long,swap_short,"
            "contract_size,tick_value,point_size,source_updated_at,received_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,symbol) DO UPDATE SET "
            "commission_per_lot=excluded.commission_per_lot,swap_long=excluded.swap_long,"
            "swap_short=excluded.swap_short,contract_size=excluded.contract_size,"
            "tick_value=excluded.tick_value,point_size=excluded.point_size,"
            "source_updated_at=excluded.source_updated_at,received_at=excluded.received_at",
            rows,
        )
    return {"ok": True, "account_id": payload.account_id,
            "received": len(rows), "received_at": received}


@app.get("/api/mt5/commissions", dependencies=[Depends(require_key)])
def get_commissions(symbol: str | None = Query(default=None, min_length=3, max_length=32)):
    sql = (
        "SELECT account_id,symbol,commission_per_lot,swap_long,swap_short,"
        "contract_size,tick_value,point_size,source_updated_at,received_at "
        "FROM symbol_commissions"
    )
    params: tuple = ()
    if symbol:
        sql += " WHERE symbol=?"
        params = (symbol.strip().upper(),)
    sql += " ORDER BY received_at DESC, account_id"
    with db() as conn:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    return {"ok": True, "items": rows}


@app.get("/api/chart/data", dependencies=[Depends(require_key)])
def chart_data(
    symbol: str = Query(min_length=3, max_length=32),
    timeframe: Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"] = Query(default="M15"),
    limit: int = Query(default=500, ge=50, le=2000),
):
    symbol = symbol.strip().upper()
    with db() as conn:
        candles = [dict(r) for r in conn.execute(
            "SELECT candle_time AS time,open,high,low,close,volume,digits,tick_size,tick_value,volume_min,volume_max,volume_step,ask "
            "FROM candles WHERE symbol=? AND timeframe=? ORDER BY candle_time DESC LIMIT ?",
            (symbol, timeframe, limit),
        ).fetchall()]
        candles.reverse()
        signals = [dict(r) for r in conn.execute(
            "SELECT symbol,timeframe,direction,divergence,signal_type,signal_time,price1,price2,rsi1,rsi2,time1,time2 "
            "FROM today_signals WHERE symbol=? AND timeframe=? ORDER BY signal_time DESC LIMIT 200",
            (symbol, timeframe),
        ).fetchall()]
        ask_val = _get_latest_symbol_ask(conn, symbol, timeframe)
    cached_tick = _get_cached_tick(symbol, max_age_sec=30.0)
    bid_val = (
        cached_tick["bid"]
        if (cached_tick and cached_tick.get("bid"))
        else (candles[-1]["close"] if candles else None)
    )
    meta = {
        "digits": candles[-1]["digits"],
        "tick_size": candles[-1]["tick_size"],
        "tick_value": candles[-1]["tick_value"],
        "volume_min": candles[-1]["volume_min"],
        "volume_max": candles[-1]["volume_max"],
        "volume_step": candles[-1]["volume_step"],
    } if candles else {
        "digits": 5, "tick_size": 0.00001, "tick_value": 0,
        "volume_min": 0, "volume_max": 0, "volume_step": 0,
    }
    return {
        "mode": "REAL_ONLY",
        "symbol": symbol,
        "timeframe": timeframe,
        **meta,
        "bid": bid_val,
        "ask": ask_val,
        "ask_price": ask_val,
        "candles": candles,
        "signals": signals,
        "server_time": utc_now(),
    }


@app.get("/api/dashboard", dependencies=[Depends(require_key)])
def dashboard_data():
    _cleanup_recently_acked()
    with db() as conn:
        _expire_stale_pending_modifies(conn)
        mt5_accounts = _format_account_rows(conn.execute(
            "SELECT * FROM mt5_account_info ORDER BY updated_at DESC"
        ).fetchall())
        signals = [dict(r) for r in conn.execute(
            "SELECT id,symbol,timeframe,direction,order_type,entry,sl,tp,score,source,status,created_at,ack_status,ack_message "
            "FROM signals ORDER BY created_at DESC LIMIT 100").fetchall()]
        mt5_account_items = [dict(r) for r in conn.execute(
            "SELECT id,ticket,symbol,timeframe,direction,entry,sl,tp,volume,profit,opened_at,"
            "is_pending,order_type,magic,comment,source,updated_at "
            "FROM mt5_account_items ORDER BY opened_at DESC").fetchall()]
        mt5_positions = [
            {**p, "read_only": False, "origin": p["source"]}
            for p in mt5_account_items if not p["is_pending"]
        ]
        mt5_pending_orders = [
            {**p, "read_only": False, "origin": p["source"],
             "status": "Đang chờ trên MT5"}
            for p in mt5_account_items if p["is_pending"]
        ]
        recent_pending_modifies = [dict(r) for r in conn.execute(
            "SELECT request_id, ticket, symbol, order_type, old_entry, old_sl, old_tp, "
            "new_entry, new_sl, new_tp, status, created_at, acked_at, ok, message "
            "FROM pending_modify_requests ORDER BY created_at DESC LIMIT 30"
        ).fetchall()]
        for item in recent_pending_modifies:
            item["ok"] = None if item["ok"] is None else bool(item["ok"])
        snapshot_state = conn.execute(
            "SELECT updated_at FROM heartbeats WHERE component='mt5-account-snapshot'"
        ).fetchone()
        mt5_snapshot_updated_at = snapshot_state["updated_at"] if snapshot_state else None
        real_history = [dict(r) for r in conn.execute(
            "SELECT id,ticket,symbol,timeframe,direction,entry,sl,tp,volume,profit,outcome,opened_at,closed_at "
            "FROM real_history ORDER BY closed_at DESC LIMIT 100").fetchall()]
        heartbeats = [dict(r) for r in conn.execute(
            "SELECT component,status,details,updated_at FROM heartbeats ORDER BY component").fetchall()]
        for item in heartbeats:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(item["updated_at"])).total_seconds()
            except Exception:
                age = 0
            item["age_seconds"] = int(max(0, age))
            if age > 45 and item["status"] == "online":
                item["status"] = "stale"
        counts = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status,COUNT(*) AS n FROM signals WHERE status!='completed' GROUP BY status").fetchall()}
        counts["completed"] = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE status='completed' AND ack_status='PLACED'"
        ).fetchone()[0]
        counts["rejected"] = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE status='completed' AND ack_status='REJECTED'"
        ).fetchone()[0]
        counts["errors"] = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE status='completed' AND ack_status='ERROR'"
        ).fetchone()[0]
        scans = [dict(r) for r in conn.execute(
            "SELECT component,symbols_json,updated_at FROM scan_status ORDER BY component").fetchall()]
        for item in scans:
            try:
                item["symbols"] = json.loads(item.pop("symbols_json"))
            except Exception:
                item["symbols"] = []
                item.pop("symbols_json", None)
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(item["updated_at"])).total_seconds()
            except Exception:
                age = 0
            item["age_seconds"] = int(max(0, age))
            item["active"] = age <= 30 and len(item["symbols"]) > 0
            if age > 30:
                item["symbols"] = []
        events = [dict(r) for r in conn.execute(
            "SELECT event_type,message,created_at FROM events ORDER BY id DESC LIMIT 30").fetchall()]
        today_signals = [dict(r) for r in conn.execute(
            "SELECT symbol,timeframe,direction,divergence,signal_type,signal_time,price1,price2,rsi1,rsi2,time1,time2 "
            "FROM today_signals ORDER BY signal_time DESC LIMIT 200").fetchall()]
        settings_row = conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
        settings_dict = _settings_row_to_dict(settings_row) if settings_row else {}
        news_status = evaluate_news_filter(settings_dict)
    return {
        "mode": "REAL_ONLY",
        "mt5_accounts": mt5_accounts,
        "latest_ticks": _get_all_cached_ticks(),
        "signals": signals,
        "today_signals": today_signals,
        "mt5_positions": mt5_positions,
        "mt5_pending_orders": mt5_pending_orders,
        "recent_pending_modifies": recent_pending_modifies,
        "mt5_snapshot_updated_at": mt5_snapshot_updated_at,
        "real_history": real_history,
        "mt5_history": real_history,
        "heartbeats": heartbeats,
        "scan_status": scans,
        "counts": counts,
        "events": events,
        "settings": settings_dict,
        "news_status": news_status,
        "server_time": utc_now(),
    }