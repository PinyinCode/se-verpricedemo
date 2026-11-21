# -*- coding: utf-8 -*-
"""Forex AI bridge API. Demo/paper-trading only; no broker order execution."""
from __future__ import annotations

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
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("FOREX_DB_PATH", str(BASE_DIR / "forex_demo.db")))
API_KEY = os.getenv("FOREX_API_KEY", "").strip()
STALE_SECONDS = int(os.getenv("SIGNAL_STALE_SECONDS", "300"))

_db_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


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
        if "digits" not in cols:
            conn.execute("ALTER TABLE candles ADD COLUMN digits INTEGER NOT NULL DEFAULT 5")
        if "tick_size" not in cols:
            conn.execute("ALTER TABLE candles ADD COLUMN tick_size REAL NOT NULL DEFAULT 0.00001")
        if "tick_value" not in cols:
            conn.execute("ALTER TABLE candles ADD COLUMN tick_value REAL NOT NULL DEFAULT 0")
        if "volume_min" not in cols:
            conn.execute("ALTER TABLE candles ADD COLUMN volume_min REAL NOT NULL DEFAULT 0")
        if "volume_max" not in cols:
            conn.execute("ALTER TABLE candles ADD COLUMN volume_max REAL NOT NULL DEFAULT 0")
        if "volume_step" not in cols:
            conn.execute("ALTER TABLE candles ADD COLUMN volume_step REAL NOT NULL DEFAULT 0")

        signal_cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)")}
        if "order_type" not in signal_cols:
            conn.execute("ALTER TABLE signals ADD COLUMN order_type TEXT NOT NULL DEFAULT 'MARKET'")
        if "risk_money" not in signal_cols:
            conn.execute("ALTER TABLE signals ADD COLUMN risk_money REAL NOT NULL DEFAULT 25")
        if "requested_lot" not in signal_cols:
            conn.execute("ALTER TABLE signals ADD COLUMN requested_lot REAL NOT NULL DEFAULT 0")
        if "tp" not in signal_cols:
            conn.execute("ALTER TABLE signals ADD COLUMN tp REAL NOT NULL DEFAULT 0")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_positions (
          id TEXT PRIMARY KEY, ticket TEXT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
          direction TEXT NOT NULL, entry REAL NOT NULL, sl REAL NOT NULL, tp REAL NOT NULL,
          volume REAL NOT NULL, risk REAL NOT NULL, profit REAL NOT NULL,
          opened_at REAL NOT NULL, updated_at TEXT NOT NULL
        )""")
        pos_cols = {row[1] for row in conn.execute("PRAGMA table_info(paper_positions)")}
        if "ticket" not in pos_cols:
            conn.execute("ALTER TABLE paper_positions ADD COLUMN ticket TEXT")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_history (
          id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
          direction TEXT NOT NULL, entry REAL NOT NULL, sl REAL NOT NULL, tp REAL NOT NULL,
          volume REAL NOT NULL, risk REAL NOT NULL, profit REAL NOT NULL,
          outcome TEXT NOT NULL, opened_at REAL NOT NULL, closed_at REAL NOT NULL
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
        conn.execute("""
        CREATE TABLE IF NOT EXISTS today_signals (
          signal_key TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
          direction TEXT NOT NULL, divergence TEXT NOT NULL, signal_type TEXT NOT NULL,
          signal_time INTEGER NOT NULL, price1 REAL NOT NULL, price2 REAL NOT NULL,
          rsi1 REAL NOT NULL, rsi2 REAL NOT NULL,
          time1 INTEGER NOT NULL DEFAULT 0, time2 INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL
        )""")
        today_cols = {row[1] for row in conn.execute("PRAGMA table_info(today_signals)")}
        if "time1" not in today_cols:
            conn.execute("ALTER TABLE today_signals ADD COLUMN time1 INTEGER NOT NULL DEFAULT 0")
        if "time2" not in today_cols:
            conn.execute("ALTER TABLE today_signals ADD COLUMN time2 INTEGER NOT NULL DEFAULT 0")


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
    title="Forex AI Demo Server",
    version="0.1.2",
    description="Signal analysis and MT5 demo bridge. No live order execution.",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "dashboard")), name="static")


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    if not API_KEY:
        raise HTTPException(status_code=503, detail="Server chưa cấu hình FOREX_API_KEY")
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="API key không hợp lệ")


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


class PaperPositionIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    ticket: str | int | None = None
    symbol: str = Field(min_length=3, max_length=32)
    timeframe: str = Field(default="—", min_length=1, max_length=16)
    direction: Literal["BUY", "SELL"]
    entry: float = Field(gt=0)
    sl: float = Field(ge=0)
    tp: float = Field(ge=0)
    volume: float = Field(gt=0)
    risk: float = Field(default=0, ge=0)
    profit: float
    opened_at: float = Field(default=0, ge=0)


class PaperPositionsIn(BaseModel):
    positions: list[PaperPositionIn] = Field(default_factory=list, max_length=100)


class PaperClosedIn(PaperPositionIn):
    outcome: Literal["PAPER_TP", "PAPER_SL", "MANUAL_CLOSE"]
    closed_at: float = Field(gt=0)


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
    volume: float | None = Field(default=None, ge=0)
    entry: float | None = Field(default=None, ge=0)
    source: str | None = Field(default=None, max_length=40)

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


class AckIn(BaseModel):
    signal_id: str
    status: Literal["SIMULATED", "REJECTED", "ERROR"]
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
            "service": "forex-ai-demo",
            "database": "ok",
            "mode": "DEMO_ONLY",
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


@app.post("/api/signals", dependencies=[Depends(require_key)])
def create_signal(payload: SignalIn):
    created = utc_now()
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
        signal_id = secrets.token_hex(12)
        conn.execute(
            "INSERT INTO signals(id,symbol,timeframe,direction,order_type,entry,sl,tp,score,risk_money,requested_lot,source,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, payload.symbol, payload.timeframe, payload.direction, payload.order_type,
             payload.entry, payload.sl, payload.tp, payload.score, payload.risk_money, payload.requested_lot,
             payload.source, "pending", created),
        )
        conn.execute("INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
                     ("signal_created", f"{payload.symbol} {payload.timeframe} {payload.order_type} {signal_id}", created))
    return {"ok": True, "signal_id": signal_id, "order_type": payload.order_type, "status": "pending", "created_at": created}


@app.post("/api/chart/request")
def chart_request(payload: SignalIn):
    """Queue a chart-originated paper request; this endpoint never executes broker orders."""
    if payload.requested_lot <= 0:
        raise HTTPException(status_code=422, detail="Thiếu lot đã tính từ Risk và khoảng Entry-SL")
    paper_payload = payload.model_copy(update={"source": "web-chart"})
    return create_signal(paper_payload)


@app.get("/api/mt5/next", dependencies=[Depends(require_key)])
def next_signal(consumer: str = Query(default="mt5-demo", min_length=2, max_length=40)):
    now = time.time()
    with db() as conn:
        conn.execute(
            "UPDATE signals SET status='pending',claimed_by=NULL,claimed_at=NULL "
            "WHERE status='claimed' AND claimed_at < ?",
            (datetime.fromtimestamp(now - 45, timezone.utc).isoformat(),),
        )
        row = conn.execute(
            "SELECT * FROM signals WHERE status='pending' AND source!='web-chart' ORDER BY created_at LIMIT 1"
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


@app.get("/api/chart/next", dependencies=[Depends(require_key)])
def next_chart_request():
    """Dedicated consumer for manual chart PAPER requests; isolated from AI signal queue."""
    now = time.time()
    with db() as conn:
        conn.execute(
            "UPDATE signals SET status='pending',claimed_by=NULL,claimed_at=NULL "
            "WHERE source='web-chart' AND status='claimed' AND claimed_at < ?",
            (datetime.fromtimestamp(now - 45, timezone.utc).isoformat(),),
        )
        row = conn.execute(
            "SELECT * FROM signals WHERE source='web-chart' AND status='pending' "
            "ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return {"ok": True, "signal": None}
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])).total_seconds()
        if age > STALE_SECONDS:
            conn.execute("UPDATE signals SET status='expired' WHERE id=?", (row["id"],))
            return {"ok": True, "signal": None}
        consumer = "trade-v9-paper-chart"
        conn.execute(
            "UPDATE signals SET status='claimed',claimed_by=?,claimed_at=? "
            "WHERE id=? AND status='pending'",
            (consumer, utc_now(), row["id"]),
        )
        result = dict(row)
        result.update(status="claimed", claimed_by=consumer)
        return {"ok": True, "signal": result}


@app.post("/api/chart/ack", dependencies=[Depends(require_key)])
def acknowledge_chart_request(payload: AckIn):
    now = utc_now()
    with db() as conn:
        cur = conn.execute(
            "UPDATE signals SET status='completed',ack_at=?,ack_status=?,ack_message=? "
            "WHERE id=? AND status='claimed' AND source='web-chart' "
            "AND claimed_by='trade-v9-paper-chart'",
            (now, payload.status, payload.message, payload.signal_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu chart PAPER đang được nhận")
        conn.execute("INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
                     ("chart_paper_ack", f"{payload.signal_id}: {payload.status} {payload.message}", now))
    return {"ok": True}


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


@app.post("/api/paper/positions", dependencies=[Depends(require_key)])
def update_paper_positions(payload: PaperPositionsIn):
    now = utc_now()
    with db() as conn:
        conn.execute("DELETE FROM paper_positions")
        for p in payload.positions:
            ticket_str = str(p.ticket).strip() if p.ticket is not None else p.id
            conn.execute(
                "INSERT INTO paper_positions(id,ticket,symbol,timeframe,direction,entry,sl,tp,volume,risk,profit,opened_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (p.id, ticket_str, p.symbol.upper(), p.timeframe, p.direction, p.entry, p.sl, p.tp,
                 p.volume, p.risk, p.profit, p.opened_at, now),
            )
    return {"ok": True, "open_positions": len(payload.positions), "updated_at": now}


def _enqueue_close_position(payload: ClosePositionIn, x_api_key: str | None) -> dict:
    if API_KEY and x_api_key and not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="API key không hợp lệ")

    ticket_str = str(payload.ticket).strip() if payload.ticket is not None else ""
    pos_id = (payload.position_id or payload.id or ticket_str or "").strip()
    if not pos_id and not payload.symbol:
        raise HTTPException(status_code=422, detail="Thiếu thông tin vị thế cần đóng")

    now = utc_now()
    with db() as conn:
        row = None
        if pos_id:
            row = conn.execute(
                "SELECT * FROM paper_positions WHERE id=? OR ticket=?",
                (pos_id, pos_id),
            ).fetchone()
        if row is None and payload.symbol:
            if payload.direction:
                row = conn.execute(
                    "SELECT * FROM paper_positions WHERE symbol=? AND direction=? ORDER BY opened_at ASC LIMIT 1",
                    (payload.symbol, payload.direction),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM paper_positions WHERE symbol=? ORDER BY opened_at ASC LIMIT 1",
                    (payload.symbol,),
                ).fetchone()

        resolved_id = row["id"] if row else pos_id
        resolved_ticket = (row["ticket"] if row and row["ticket"] else "") or ticket_str or resolved_id
        resolved_sym = row["symbol"] if row else (payload.symbol or "")
        resolved_dir = row["direction"] if row else (payload.direction or "")

        existing = conn.execute(
            "SELECT request_id FROM close_requests WHERE status='pending' "
            "AND ((? != '' AND position_id=?) OR (? != '' AND ticket=?) OR (? != '' AND symbol=? AND (? = '' OR direction=?)))",
            (resolved_id, resolved_id, resolved_ticket, resolved_ticket, resolved_sym, resolved_sym, resolved_dir, resolved_dir),
        ).fetchone()
        if existing:
            return {"ok": True, "request_id": existing["request_id"], "status": "pending",
                    "message": "Đã xếp hàng lệnh đóng vị thế"}

        request_id = secrets.token_hex(10)
        conn.execute(
            "INSERT INTO close_requests(request_id,position_id,ticket,symbol,direction,status,created_at) "
            "VALUES(?,?,?,?,?,'pending',?)",
            (request_id, resolved_id, resolved_ticket, resolved_sym, resolved_dir, now),
        )
        conn.execute(
            "INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
            ("close_requested", f"Yêu cầu đóng {resolved_sym} {resolved_dir} ({resolved_id or resolved_ticket or request_id})", now),
        )
    return {"ok": True, "request_id": request_id, "status": "pending",
            "message": f"Đã gửi yêu cầu đóng {resolved_sym} {resolved_dir}".strip()}


@app.post("/api/chart/close")
def chart_close_position(payload: ClosePositionIn, x_api_key: str | None = Header(default=None)):
    return _enqueue_close_position(payload, x_api_key)


@app.post("/api/paper/positions/close")
def queue_close_position(payload: ClosePositionIn, x_api_key: str | None = Header(default=None)):
    return _enqueue_close_position(payload, x_api_key)


@app.get("/api/paper/close-requests", dependencies=[Depends(require_key)])
def get_close_requests():
    now = time.time()
    cutoff = datetime.fromtimestamp(now - 45, timezone.utc).isoformat()
    with db() as conn:
        conn.execute(
            "UPDATE close_requests SET status='expired',acked_at=?,message='Hết hạn chờ MT5' "
            "WHERE status='pending' AND created_at < ?",
            (utc_now(), cutoff),
        )
        rows = [dict(r) for r in conn.execute(
            "SELECT request_id,position_id,ticket,symbol,direction "
            "FROM close_requests WHERE status='pending' ORDER BY created_at ASC LIMIT 20"
        ).fetchall()]
    return {"ok": True, "close_requests": rows}


@app.post("/api/paper/close-requests/ack", dependencies=[Depends(require_key)])
def ack_close_request(payload: CloseRequestAckIn):
    now = utc_now()
    with db() as conn:
        req_row = conn.execute(
            "SELECT * FROM close_requests WHERE request_id=?", (payload.request_id,)
        ).fetchone()
        conn.execute(
            "UPDATE close_requests SET status='acked',acked_at=?,ok=?,message=? WHERE request_id=?",
            (now, 1 if payload.ok else 0, payload.message, payload.request_id),
        )
        pos_target = (payload.position_id or (req_row["position_id"] if req_row else "") or "").strip()
        ticket_target = ((req_row["ticket"] if req_row and req_row["ticket"] else "") or "").strip()
        if payload.ok:
            if pos_target and ticket_target:
                conn.execute("DELETE FROM paper_positions WHERE id=? OR ticket=? OR id=? OR ticket=?",
                             (pos_target, pos_target, ticket_target, ticket_target))
            elif pos_target:
                conn.execute("DELETE FROM paper_positions WHERE id=? OR ticket=?", (pos_target, pos_target))
            elif ticket_target:
                conn.execute("DELETE FROM paper_positions WHERE id=? OR ticket=?", (ticket_target, ticket_target))
        conn.execute(
            "INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
            ("close_ack", f"{payload.request_id} ({pos_target or ticket_target}): ok={payload.ok} {payload.message}".strip(), now),
        )
    return {"ok": True}


@app.post("/api/paper/closed", dependencies=[Depends(require_key)])
def record_paper_closed(payload: PaperClosedIn):
    with db() as conn:
        conn.execute("DELETE FROM paper_positions WHERE id=?", (payload.id,))
        conn.execute(
            "INSERT OR REPLACE INTO paper_history(id,symbol,timeframe,direction,entry,sl,tp,volume,risk,profit,outcome,opened_at,closed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (payload.id, payload.symbol.upper(), payload.timeframe, payload.direction,
             payload.entry, payload.sl, payload.tp, payload.volume, payload.risk,
             payload.profit, payload.outcome, payload.opened_at, payload.closed_at),
        )
        conn.execute("INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
                     ("paper_closed", f"{payload.symbol} {payload.direction} {payload.outcome} P/L={payload.profit:.2f}", utc_now()))
    return {"ok": True}


@app.post("/api/mt5/history", dependencies=[Depends(require_key)])
def update_real_history(payload: RealHistoryBatchIn):
    now = utc_now()
    with db() as conn:
        if payload.replace:
            conn.execute("DELETE FROM real_history")
        for item in payload.trades:
            ticket_str = str(item.ticket).strip() if item.ticket is not None else ""
            row_id = (item.id or ticket_str or f"{item.symbol}_{item.direction}_{int(item.closed_at)}_{abs(hash((item.symbol, item.entry, item.volume, item.profit))) % 100000}").strip()
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
        conn.execute("DELETE FROM today_signals")
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
        "mode": "DEMO_ONLY",
        "symbol": symbol,
        "timeframe": timeframe,
        **meta,
        "candles": candles,
        "signals": signals,
        "server_time": utc_now(),
    }


@app.get("/api/dashboard")
def dashboard_data():
    with db() as conn:
        signals = [dict(r) for r in conn.execute(
            "SELECT id,symbol,timeframe,direction,order_type,entry,sl,tp,score,source,status,created_at,ack_status,ack_message "
            "FROM signals ORDER BY created_at DESC LIMIT 100").fetchall()]
        paper_positions = [dict(r) for r in conn.execute(
            "SELECT id,ticket,symbol,timeframe,direction,entry,sl,tp,volume,risk,profit,opened_at,updated_at "
            "FROM paper_positions ORDER BY opened_at DESC").fetchall()]
        paper_history = [dict(r) for r in conn.execute(
            "SELECT id,symbol,timeframe,direction,entry,sl,tp,volume,risk,profit,outcome,opened_at,closed_at "
            "FROM paper_history ORDER BY closed_at DESC LIMIT 50").fetchall()]
        real_history = [dict(r) for r in conn.execute(
            "SELECT id,ticket,symbol,timeframe,direction,entry,sl,tp,volume,profit,outcome,opened_at,closed_at "
            "FROM real_history ORDER BY closed_at DESC LIMIT 100").fetchall()]
        heartbeats = [dict(r) for r in conn.execute(
            "SELECT component,status,details,updated_at FROM heartbeats ORDER BY component").fetchall()]
        for item in heartbeats:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(item["updated_at"])).total_seconds()
            item["age_seconds"] = int(max(0, age))
            if age > 45 and item["status"] == "online":
                item["status"] = "stale"
        counts = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status,COUNT(*) AS n FROM signals WHERE status!='completed' GROUP BY status").fetchall()}
        counts["completed"] = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE status='completed' AND ack_status='SIMULATED'"
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
            item["symbols"] = json.loads(item.pop("symbols_json"))
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(item["updated_at"])).total_seconds()
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
        "mode": "DEMO_ONLY",
        "signals": signals,
        "today_signals": today_signals,
        "paper_positions": paper_positions,
        "open_positions": paper_positions,
        "paper_history": paper_history,
        "real_history": real_history,
        "mt5_history": real_history,
        "heartbeats": heartbeats,
        "scan_status": scans,
        "counts": counts,
        "events": events,
        "server_time": utc_now(),
    }
