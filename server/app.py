# -*- coding: utf-8 -*-
"""Forex AI bridge API. MT5 real-order only; no paper simulation."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("FOREX_DB_PATH", str(BASE_DIR / "forex_demo.db")))
API_KEY = os.getenv("FOREX_API_KEY", "").strip()
STALE_SECONDS = int(os.getenv("SIGNAL_STALE_SECONDS", "300"))
CLOSE_REQUEST_TTL = int(os.getenv("CLOSE_REQUEST_TTL_SECONDS", "45"))
SLTP_REQUEST_TTL = int(os.getenv("SLTP_REQUEST_TTL_SECONDS", "60"))

_db_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_recently_acked: dict[str, float] = {}
_ACK_COOLDOWN = 10.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA synchronous=NORMAL")
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


def init_db() -> None:
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
          id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
          direction TEXT NOT NULL, order_type TEXT NOT NULL DEFAULT 'MARKET',
          entry REAL NOT NULL, sl REAL NOT NULL,
          score REAL NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT 'api',
          status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
          claimed_by TEXT, claimed_at TEXT, ack_at TEXT, ack_status TEXT, ack_message TEXT
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
          message TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS heartbeats (
          component TEXT PRIMARY KEY, status TEXT NOT NULL,
          details TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_status (
          component TEXT PRIMARY KEY, symbols_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS candles (
          symbol TEXT NOT NULL, timeframe TEXT NOT NULL, candle_time INTEGER NOT NULL,
          open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
          volume REAL NOT NULL DEFAULT 0, digits INTEGER NOT NULL DEFAULT 5,
          tick_size REAL NOT NULL DEFAULT 0.00001, tick_value REAL NOT NULL DEFAULT 0,
          PRIMARY KEY(symbol,timeframe,candle_time)
        )""")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(candles)")}
        for col, sql in [
            ("digits", "ALTER TABLE candles ADD COLUMN digits INTEGER NOT NULL DEFAULT 5"),
            ("tick_size", "ALTER TABLE candles ADD COLUMN tick_size REAL NOT NULL DEFAULT 0.00001"),
            ("tick_value", "ALTER TABLE candles ADD COLUMN tick_value REAL NOT NULL DEFAULT 0"),
            ("volume_min", "ALTER TABLE candles ADD COLUMN volume_min REAL NOT NULL DEFAULT 0"),
            ("volume_max", "ALTER TABLE candles ADD COLUMN volume_max REAL NOT NULL DEFAULT 0"),
            ("volume_step", "ALTER TABLE candles ADD COLUMN volume_step REAL NOT NULL DEFAULT 0"),
        ]:
            if col not in cols:
                conn.execute(sql)

        signal_cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)")}
        for col, sql in [
            ("order_type", "ALTER TABLE signals ADD COLUMN order_type TEXT NOT NULL DEFAULT 'MARKET'"),
            ("risk_money", "ALTER TABLE signals ADD COLUMN risk_money REAL NOT NULL DEFAULT 25"),
            ("requested_lot", "ALTER TABLE signals ADD COLUMN requested_lot REAL NOT NULL DEFAULT 0"),
            ("tp", "ALTER TABLE signals ADD COLUMN tp REAL NOT NULL DEFAULT 0"),
        ]:
            if col not in signal_cols:
                conn.execute(sql)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS mt5_account_items (
          id TEXT PRIMARY KEY, ticket TEXT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
          direction TEXT NOT NULL, entry REAL NOT NULL, sl REAL NOT NULL, tp REAL NOT NULL,
          volume REAL NOT NULL, profit REAL NOT NULL, opened_at REAL NOT NULL,
          is_pending INTEGER NOT NULL DEFAULT 0, order_type TEXT NOT NULL DEFAULT '',
          magic INTEGER NOT NULL DEFAULT 0, comment TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT 'MT5_EXTERNAL', updated_at TEXT NOT NULL
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS real_history (
          id TEXT PRIMARY KEY, ticket TEXT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL DEFAULT '—',
          direction TEXT NOT NULL, entry REAL NOT NULL, sl REAL NOT NULL DEFAULT 0, tp REAL NOT NULL DEFAULT 0,
          volume REAL NOT NULL, profit REAL NOT NULL, outcome TEXT NOT NULL DEFAULT 'CLOSED',
          opened_at REAL NOT NULL DEFAULT 0, closed_at REAL NOT NULL, updated_at TEXT NOT NULL
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS close_requests (
          request_id TEXT PRIMARY KEY, position_id TEXT, ticket TEXT,
          symbol TEXT, direction TEXT, status TEXT NOT NULL DEFAULT 'pending',
          created_at TEXT NOT NULL, acked_at TEXT, ok INTEGER, message TEXT
        )""")
        cr_cols = {row[1] for row in conn.execute("PRAGMA table_info(close_requests)")}
        if "kind" not in cr_cols:
            conn.execute("ALTER TABLE close_requests ADD COLUMN kind TEXT NOT NULL DEFAULT 'close'")
        if "is_pending" not in cr_cols:
            conn.execute("ALTER TABLE close_requests ADD COLUMN is_pending INTEGER NOT NULL DEFAULT 0")
        if "order_type" not in cr_cols:
            conn.execute("ALTER TABLE close_requests ADD COLUMN order_type TEXT NOT NULL DEFAULT ''")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS sltp_requests (
          request_id TEXT PRIMARY KEY, ticket TEXT NOT NULL, symbol TEXT,
          old_sl REAL NOT NULL DEFAULT 0, old_tp REAL NOT NULL DEFAULT 0,
          new_sl REAL NOT NULL DEFAULT 0, new_tp REAL NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'pending',
          created_at TEXT NOT NULL, acked_at TEXT, ok INTEGER, message TEXT
        )""")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS today_signals (
          signal_key TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
          direction TEXT NOT NULL, divergence TEXT NOT NULL, signal_type TEXT NOT NULL,
          signal_time INTEGER NOT NULL, price1 REAL NOT NULL, price2 REAL NOT NULL,
          rsi1 REAL NOT NULL, rsi2 REAL NOT NULL,
          time1 INTEGER NOT NULL DEFAULT 0, time2 INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL
        )""")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
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
    version="0.3.0",
    description="Signal analysis and MT5 real-order bridge.",
    lifespan=lifespan,
)

_frontend_origins = [x.strip() for x in os.getenv(
    "FRONTEND_ORIGINS", "https://pinyincode.github.io,http://localhost:8000,http://127.0.0.1:8000"
).split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_frontend_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "dashboard")), name="static")


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    if not API_KEY:
        raise HTTPException(status_code=503, detail="Server chưa cấu hình FOREX_API_KEY")
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="API key không hợp lệ")


# ============ Models ============

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
    candles: list[CandleItem] = Field(min_length=1, max_length=1000)

    @field_validator("symbol")
    @classmethod
    def normalize_candle_symbol(cls, value: str) -> str:
        return value.strip().upper()


class TodaySignalsIn(BaseModel):
    signals: list[TodaySignalItem] = Field(default_factory=list, max_length=200)


# ============ Routes ============

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return FileResponse(BASE_DIR / "dashboard" / "index.html")


@app.get("/chart", response_class=HTMLResponse)
def chart_page():
    return FileResponse(BASE_DIR / "dashboard" / "chart.html")


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


def _validate_limit_against_market(conn: sqlite3.Connection, payload: SignalIn) -> None:
    if payload.order_type not in ("BUY_LIMIT", "SELL_LIMIT"):
        return
    row = conn.execute(
        "SELECT close FROM candles WHERE symbol=? AND timeframe=? ORDER BY candle_time DESC LIMIT 1",
        (payload.symbol, payload.timeframe),
    ).fetchone()
    if row is None:
        return
    market_price = float(row["close"])
    if payload.order_type == "BUY_LIMIT" and payload.entry >= market_price:
        raise HTTPException(status_code=422, detail="BUY_LIMIT yêu cầu Entry thấp hơn giá hiện tại")
    if payload.order_type == "SELL_LIMIT" and payload.entry <= market_price:
        raise HTTPException(status_code=422, detail="SELL_LIMIT yêu cầu Entry cao hơn giá hiện tại")


def _insert_signal(conn: sqlite3.Connection, payload: SignalIn, source: str) -> dict:
    created = utc_now()
    signal_id = secrets.token_hex(12)
    conn.execute(
        "INSERT INTO signals(id,symbol,timeframe,direction,order_type,entry,sl,tp,score,risk_money,requested_lot,source,status,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (signal_id, payload.symbol, payload.timeframe, payload.direction, payload.order_type,
         payload.entry, payload.sl, payload.tp, payload.score, payload.risk_money, payload.requested_lot,
         source, "pending", created),
    )
    conn.execute(
        "INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
        ("signal_created", f"{payload.symbol} {payload.timeframe} {payload.order_type} {signal_id} ({source})", created),
    )
    return {
        "ok": True,
        "signal_id": signal_id,
        "order_type": payload.order_type,
        "status": "pending",
        "created_at": created,
    }


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
        return _insert_signal(conn, payload, payload.source)


@app.post("/api/chart/order", dependencies=[Depends(require_key)])
def chart_order(payload: SignalIn):
    if payload.requested_lot <= 0:
        raise HTTPException(status_code=422, detail="Thiếu lot đã tính từ Risk và khoảng Entry-SL")
    with db() as conn:
        _validate_limit_against_market(conn, payload)
        return _insert_signal(conn, payload, "web-chart")


@app.get("/api/mt5/next", dependencies=[Depends(require_key)])
def next_signal(consumer: str = Query(default="mt5", min_length=2, max_length=40)):
    now = time.time()
    with db() as conn:
        conn.execute(
            "UPDATE signals SET status='pending',claimed_by=NULL,claimed_at=NULL "
            "WHERE status='claimed' AND claimed_at < ?",
            (datetime.fromtimestamp(now - 45, timezone.utc).isoformat(),),
        )
        row = conn.execute(
            "SELECT * FROM signals WHERE status='pending' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return {"ok": True, "signal": None}
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])).total_seconds()
        if age > STALE_SECONDS:
            conn.execute("UPDATE signals SET status='expired' WHERE id=?", (row["id"],))
            return {"ok": True, "signal": None}
        conn.execute(
            "UPDATE signals SET status='claimed',claimed_by=?,claimed_at=? WHERE id=? AND status='pending'",
            (consumer, utc_now(), row["id"]),
        )
        result = dict(row)
        result.update(status="claimed", claimed_by=consumer)
        return {"ok": True, "signal": result}


@app.post("/api/mt5/ack", dependencies=[Depends(require_key)])
def acknowledge(payload: AckIn):
    now = utc_now()
    with db() as conn:
        cur = conn.execute(
            "UPDATE signals SET status='completed',ack_at=?,ack_status=?,ack_message=? "
            "WHERE id=? AND status='claimed'",
            (now, payload.status, payload.message, payload.signal_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=404, detail="Không tìm thấy tín hiệu đang được nhận")
        conn.execute("INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
                     ("mt5_ack", f"{payload.signal_id}: {payload.status} {payload.message}", now))
    return {"ok": True}


def _cleanup_recently_acked():
    now = time.time()
    expired = [k for k, t in _recently_acked.items() if now - t > _ACK_COOLDOWN]
    for k in expired:
        _recently_acked.pop(k, None)


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
    """Chỉ fallback sang symbol khi pos_id rỗng. Tránh đóng nhầm lệnh."""
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

        existing = conn.execute(
            "SELECT request_id FROM close_requests WHERE status='pending' AND kind=? "
            "AND ((? != '' AND position_id=?) OR (? != '' AND ticket=?) "
            "OR (? != '' AND symbol=? AND (? = '' OR direction=?)))",
            (resolved_kind, resolved_id, resolved_id, resolved_ticket, resolved_ticket,
             resolved_sym, resolved_sym, resolved_dir, resolved_dir),
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
            expire = time.time() + _ACK_COOLDOWN
            if pos_target:
                _recently_acked[pos_target] = expire
            if ticket_target:
                _recently_acked[ticket_target] = expire
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
        # Kiểm tra hình học SL/TP mới
        if payload.new_sl > 0:
            if direction == "BUY" and payload.new_sl >= entry:
                raise HTTPException(status_code=422, detail="SL BUY phải < Entry")
            if direction == "SELL" and payload.new_sl <= entry:
                raise HTTPException(status_code=422, detail="SL SELL phải > Entry")
        if payload.new_tp > 0:
            if direction == "BUY" and payload.new_tp <= entry:
                raise HTTPException(status_code=422, detail="TP BUY phải > Entry")
            if direction == "SELL" and payload.new_tp >= entry:
                raise HTTPException(status_code=422, detail="TP SELL phải < Entry")

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
def update_today_signals(payload: TodaySignalsIn):
    now = utc_now()
    with db() as conn:
        for item in payload.signals:
            key = "|".join((item.symbol, item.timeframe, item.direction,
                            item.divergence, item.signal_type, str(item.signal_time)))
            conn.execute(
                "INSERT OR REPLACE INTO today_signals(signal_key,symbol,timeframe,direction,divergence,signal_type,"
                "signal_time,price1,price2,rsi1,rsi2,time1,time2,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, item.symbol, item.timeframe, item.direction, item.divergence,
                 item.signal_type, item.signal_time, item.price1, item.price2,
                 item.rsi1, item.rsi2, item.time1, item.time2, now),
            )
    return {"ok": True, "count": len(payload.signals), "updated_at": now}


@app.post("/api/mt5/candles", dependencies=[Depends(require_key)])
def update_candles(payload: CandleBatchIn):
    now = utc_now()
    rows = []
    for candle in payload.candles:
        if candle.high < max(candle.open, candle.close, candle.low) or candle.low > min(candle.open, candle.close, candle.high):
            raise HTTPException(status_code=422, detail="OHLC không hợp lệ")
        rows.append((payload.symbol, payload.timeframe, candle.time, candle.open,
                     candle.high, candle.low, candle.close, candle.volume, payload.digits, payload.tick_size, payload.tick_value,
                     payload.volume_min, payload.volume_max, payload.volume_step))
    with db() as conn:
        conn.executemany(
            "INSERT INTO candles(symbol,timeframe,candle_time,open,high,low,close,volume,digits,tick_size,tick_value,volume_min,volume_max,volume_step) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,timeframe,candle_time) DO UPDATE SET "
            "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,"
            "digits=excluded.digits,tick_size=excluded.tick_size,tick_value=excluded.tick_value,"
            "volume_min=excluded.volume_min,volume_max=excluded.volume_max,volume_step=excluded.volume_step",
            rows,
        )
        conn.execute(
            "DELETE FROM candles WHERE symbol=? AND timeframe=? AND candle_time NOT IN "
            "(SELECT candle_time FROM candles WHERE symbol=? AND timeframe=? ORDER BY candle_time DESC LIMIT 2000)",
            (payload.symbol, payload.timeframe, payload.symbol, payload.timeframe),
        )
    return {"ok": True, "symbol": payload.symbol, "timeframe": payload.timeframe,
            "received": len(rows), "updated_at": now}


@app.get("/api/chart/data")
def chart_data(
    symbol: str = Query(min_length=3, max_length=32),
    timeframe: Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"] = Query(default="M15"),
    limit: int = Query(default=500, ge=50, le=2000),
):
    symbol = symbol.strip().upper()
    with db() as conn:
        candles = [dict(r) for r in conn.execute(
            "SELECT candle_time AS time,open,high,low,close,volume,digits,tick_size,tick_value,volume_min,volume_max,volume_step FROM candles "
            "WHERE symbol=? AND timeframe=? ORDER BY candle_time DESC LIMIT ?",
            (symbol, timeframe, limit),
        ).fetchall()]
        candles.reverse()
        signals = [dict(r) for r in conn.execute(
            "SELECT symbol,timeframe,direction,divergence,signal_type,signal_time,price1,price2,rsi1,rsi2,time1,time2 "
            "FROM today_signals WHERE symbol=? AND timeframe=? ORDER BY signal_time DESC LIMIT 200",
            (symbol, timeframe),
        ).fetchall()]
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
        "candles": candles,
        "signals": signals,
        "server_time": utc_now(),
    }


@app.get("/api/dashboard")
def dashboard_data():
    _cleanup_recently_acked()
    with db() as conn:
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
    return {
        "mode": "REAL_ONLY",
        "signals": signals,
        "today_signals": today_signals,
        "mt5_positions": mt5_positions,
        "mt5_pending_orders": mt5_pending_orders,
        "mt5_snapshot_updated_at": mt5_snapshot_updated_at,
        "real_history": real_history,
        "mt5_history": real_history,
        "heartbeats": heartbeats,
        "scan_status": scans,
        "counts": counts,
        "events": events,
        "server_time": utc_now(),
    }
