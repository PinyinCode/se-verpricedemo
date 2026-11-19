# -*- coding: utf-8 -*-
"""Forex AI bridge API. Demo/paper-trading only; no broker order execution."""
from __future__ import annotations

import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("FOREX_DB_PATH", str(BASE_DIR / "forex_demo.db")))
API_KEY = os.getenv("FOREX_API_KEY", "").strip()
STALE_SECONDS = int(os.getenv("SIGNAL_STALE_SECONDS", "300"))

app = FastAPI(title="Forex AI Demo Server", version="0.1.0",
              description="Signal analysis and MT5 demo bridge. No live order execution.")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "dashboard")), name="static")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
          id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
          direction TEXT NOT NULL, entry REAL NOT NULL, sl REAL NOT NULL,
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


@app.on_event("startup")
def startup() -> None:
    init_db()


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    if not API_KEY:
        raise HTTPException(status_code=503, detail="Server chưa cấu hình FOREX_API_KEY")
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="API key không hợp lệ")


class SignalIn(BaseModel):
    symbol: str = Field(min_length=3, max_length=32)
    timeframe: Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
    direction: Literal["BUY", "SELL"]
    entry: float = Field(gt=0)
    sl: float = Field(gt=0)
    score: float = Field(default=0, ge=0, le=100)
    source: str = Field(default="api", max_length=40)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class AckIn(BaseModel):
    signal_id: str
    status: Literal["SIMULATED", "REJECTED", "ERROR"]
    message: str = Field(default="", max_length=500)


class HeartbeatIn(BaseModel):
    component: str = Field(min_length=2, max_length=40)
    status: Literal["online", "warning", "offline"]
    details: str = Field(default="", max_length=500)


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return FileResponse(BASE_DIR / "dashboard" / "index.html")


@app.get("/api/health")
def health():
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "service": "forex-ai-demo", "database": "ok",
                "mode": "DEMO_ONLY", "time": utc_now()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database_error: {type(exc).__name__}")


@app.post("/api/signals", dependencies=[Depends(require_key)])
def create_signal(payload: SignalIn):
    # A signal is informational only. The MT5 bridge records a simulation, never sends an order.
    if (payload.direction == "BUY" and payload.sl >= payload.entry) or (
        payload.direction == "SELL" and payload.sl <= payload.entry
    ):
        raise HTTPException(status_code=422, detail="SL phải nằm đúng phía so với entry")
    signal_id = secrets.token_hex(12)
    created = utc_now()
    with db() as conn:
        conn.execute(
            "INSERT INTO signals(id,symbol,timeframe,direction,entry,sl,score,source,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (signal_id, payload.symbol, payload.timeframe, payload.direction,
             payload.entry, payload.sl, payload.score, payload.source, "pending", created),
        )
        conn.execute("INSERT INTO events(event_type,message,created_at) VALUES(?,?,?)",
                     ("signal_created", f"{payload.symbol} {payload.timeframe} {payload.direction} {signal_id}", created))
    return {"ok": True, "signal_id": signal_id, "status": "pending", "created_at": created}


@app.get("/api/mt5/next", dependencies=[Depends(require_key)])
def next_signal(consumer: str = Query(default="mt5-demo", min_length=2, max_length=40)):
    now = time.time()
    with db() as conn:
        # Release stale claims so a restarted EA can receive the signal again.
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


@app.post("/api/heartbeat", dependencies=[Depends(require_key)])
def heartbeat(payload: HeartbeatIn):
    with db() as conn:
        conn.execute(
            "INSERT INTO heartbeats(component,status,details,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(component) DO UPDATE SET status=excluded.status,details=excluded.details,updated_at=excluded.updated_at",
            (payload.component, payload.status, payload.details, utc_now()),
        )
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard_data():
    with db() as conn:
        signals = [dict(r) for r in conn.execute(
            "SELECT id,symbol,timeframe,direction,entry,sl,score,status,created_at,ack_status,ack_message "
            "FROM signals ORDER BY created_at DESC LIMIT 100").fetchall()]
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
        events = [dict(r) for r in conn.execute(
            "SELECT event_type,message,created_at FROM events ORDER BY id DESC LIMIT 30").fetchall()]
    return {"mode": "DEMO_ONLY", "signals": signals, "heartbeats": heartbeats,
            "counts": counts, "events": events, "server_time": utc_now()}
