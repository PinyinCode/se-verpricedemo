# -*- coding: utf-8 -*-
"""Forex AI bridge API. MT5 real-order only; no paper simulation."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError
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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


# MongoDB mirror for the public, read-only Paper chart feed.
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB_NAME = os.getenv("MONGODB_DB", "forex_market").strip() or "forex_market"
MONGODB_COLLECTION = os.getenv("MONGODB_CANDLES_COLLECTION", "paper_candles").strip() or "paper_candles"
_mongo_client = None
_mongo_lock = threading.Lock()
_mongo_indexes_ready = False


def _paper_candles_collection():
    """Return the shared Mongo collection; the connection URI never leaves the server."""
    global _mongo_client, _mongo_indexes_ready
    if not MONGODB_URI:
        return None
    try:
        from pymongo import MongoClient
        with _mongo_lock:
            if _mongo_client is None:
                _mongo_client = MongoClient(
                    MONGODB_URI,
                    serverSelectionTimeoutMS=5000,
                    connectTimeoutMS=5000,
                    appname="ForexPaperFeed",
                )
            collection = _mongo_client[MONGODB_DB_NAME][MONGODB_COLLECTION]
            if not _mongo_indexes_ready:
                collection.create_index(
                    [("symbol", 1), ("timeframe", 1), ("time", 1)],
                    unique=True,
                    name="uq_paper_symbol_tf_time",
                )
                collection.create_index(
                    [("symbol", 1), ("timeframe", 1), ("time", -1)],
                    name="ix_paper_latest",
                )
                _mongo_indexes_ready = True
            return collection
    except Exception as exc:
        print(f"[MONGO] Paper feed unavailable: {type(exc).__name__}")
        return None


def _mirror_candles_to_mongo(payload: "CandleBatchIn", rows: list[tuple]) -> bool:
    """Upsert each MT5 candle by its timestamp and keep newest 2000 per symbol/timeframe."""
    collection = _paper_candles_collection()
    if collection is None:
        return False
    try:
        from pymongo import ReplaceOne
        operations = []
        for row in rows:
            doc = {
                "symbol": row[0], "timeframe": row[1], "time": row[2],
                "open": row[3], "high": row[4], "low": row[5], "close": row[6],
                "volume": row[7], "digits": row[8], "tick_size": row[9],
                "tick_value": row[10], "volume_min": row[11],
                "volume_max": row[12], "volume_step": row[13],
                "updated_at": utc_now(),
            }
            operations.append(ReplaceOne(
                {"symbol": doc["symbol"], "timeframe": doc["timeframe"], "time": doc["time"]},
                doc,
                upsert=True,
            ))
        if operations:
            collection.bulk_write(operations, ordered=False)
        cutoff_cursor = collection.find(
            {"symbol": payload.symbol, "timeframe": payload.timeframe},
            {"_id": 0, "time": 1},
        ).sort("time", -1).skip(1999).limit(1)
        cutoff_doc = next(iter(cutoff_cursor), None)
        if cutoff_doc:
            collection.delete_many({
                "symbol": payload.symbol,
                "timeframe": payload.timeframe,
                "time": {"$lt": cutoff_doc["time"]},
            })
        return True
    except Exception as exc:
        print(f"[MONGO] Candle mirror failed: {type(exc).__name__}")
        return False


def _rsi_values(closes: list[float], period: int = 14) -> list[tuple[int, float]]:
    if len(closes) <= period:
        return []
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result = [(period, value(avg_gain, avg_loss))]
    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        result.append((i, value(avg_gain, avg_loss)))
    return result


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


def _init_settings_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        auto_trade_enabled    INTEGER NOT NULL DEFAULT 1,
        trailing_enabled      INTEGER NOT NULL DEFAULT 1,
        breakeven_enabled     INTEGER NOT NULL DEFAULT 1,
        news_filter_enabled   INTEGER NOT NULL DEFAULT 1,
        news_block_before_min INTEGER NOT NULL DEFAULT 3,
        news_block_after_min  INTEGER NOT NULL DEFAULT 3,
        news_events_json      TEXT    NOT NULL DEFAULT '[]',
        time_filter_enabled   INTEGER NOT NULL DEFAULT 1,
        telegram_notifications_enabled INTEGER NOT NULL DEFAULT 1,
        emergency_stop        INTEGER NOT NULL DEFAULT 0,
        emergency_close_all   INTEGER NOT NULL DEFAULT 1,
        risk_money            REAL    NOT NULL DEFAULT 25,
        max_positions         INTEGER NOT NULL DEFAULT 3,
        reward_risk_ratio     REAL    NOT NULL DEFAULT 2.0,
        risk_tolerance_pct    REAL    NOT NULL DEFAULT 0.5,
        commission_fallback   REAL    NOT NULL DEFAULT 0,
        spread_buffer_points  INTEGER NOT NULL DEFAULT 20,
        trailing_trigger_rr   REAL    NOT NULL DEFAULT 1.0,
        trailing_lock_rr      REAL    NOT NULL DEFAULT 0.5,
        breakeven_trigger_rr  REAL    NOT NULL DEFAULT 0.5,
        trailing_mode         TEXT    NOT NULL DEFAULT 'static',
        trading_start_hour    INTEGER NOT NULL DEFAULT 7,
        trading_end_hour      INTEGER NOT NULL DEFAULT 23,
        updated_at            TEXT    NOT NULL,
        updated_by            TEXT    NOT NULL DEFAULT 'system'
    )
    """)
    settings_cols = {row[1] for row in conn.execute("PRAGMA table_info(settings)")}
    for col, sql in [
        ("telegram_notifications_enabled", "ALTER TABLE settings ADD COLUMN telegram_notifications_enabled INTEGER NOT NULL DEFAULT 1"),
        ("news_block_before_min", "ALTER TABLE settings ADD COLUMN news_block_before_min INTEGER NOT NULL DEFAULT 3"),
        ("news_block_after_min", "ALTER TABLE settings ADD COLUMN news_block_after_min INTEGER NOT NULL DEFAULT 3"),
        ("news_events_json", "ALTER TABLE settings ADD COLUMN news_events_json TEXT NOT NULL DEFAULT '[]'"),
    ]:
        if col not in settings_cols:
            conn.execute(sql)

    now = utc_now()
    conn.execute(
        "INSERT OR IGNORE INTO settings(id, updated_at, updated_by) VALUES(1, ?, 'system')",
        (now,),
    )


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
        CREATE TABLE IF NOT EXISTS pending_modify_requests (
          request_id TEXT PRIMARY KEY, ticket TEXT NOT NULL, symbol TEXT,
          order_type TEXT NOT NULL DEFAULT '',
          old_entry REAL NOT NULL DEFAULT 0, old_sl REAL NOT NULL DEFAULT 0, old_tp REAL NOT NULL DEFAULT 0,
          new_entry REAL NOT NULL DEFAULT 0, new_sl REAL NOT NULL DEFAULT 0, new_tp REAL NOT NULL DEFAULT 0,
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
        conn.execute("""
        CREATE TABLE IF NOT EXISTS symbol_commissions (
          account_id TEXT NOT NULL, symbol TEXT NOT NULL,
          commission_per_lot REAL NOT NULL,
          swap_long REAL NOT NULL DEFAULT 0, swap_short REAL NOT NULL DEFAULT 0,
          contract_size REAL NOT NULL DEFAULT 0, tick_value REAL NOT NULL DEFAULT 0,
          point_size REAL NOT NULL DEFAULT 0,
          source_updated_at INTEGER NOT NULL DEFAULT 0,
          received_at TEXT NOT NULL,
          PRIMARY KEY(account_id, symbol)
        )""")
        _init_settings_table(conn)


# Daily, read-only MongoDB -> GitHub historical snapshot for Paper.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "PinyinCode/se-verpricedemo").strip()
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip() or "main"
HISTORY_EXPORT_PATH = "docs/data/history.json"
HISTORY_EXPORT_HOUR = int(os.getenv("HISTORY_EXPORT_HOUR", "23"))
HISTORY_EXPORT_MINUTE = int(os.getenv("HISTORY_EXPORT_MINUTE", "55"))
_history_export_stop = threading.Event()
_history_export_thread: threading.Thread | None = None
_last_history_export_day: str | None = None


def _github_json_request(url: str, method: str = "GET", payload: dict | None = None) -> dict | list:
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN chưa được cấu hình")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ForexPaperHistoryExporter",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _mongo_history_snapshot(export_day: str) -> dict:
    collection = _paper_candles_collection()
    if collection is None:
        raise RuntimeError("MongoDB Paper feed chưa sẵn sàng")
    groups = list(collection.aggregate([
        {"$group": {"_id": {"symbol": "$symbol", "timeframe": "$timeframe"}}}
    ]))
    symbols: dict[str, dict] = {}
    total = 0
    for group in groups:
        key = group.get("_id") or {}
        symbol = str(key.get("symbol") or "").strip().upper()
        timeframe = str(key.get("timeframe") or "").strip().upper()
        if not symbol or not timeframe:
            continue
        docs = list(collection.find(
            {"symbol": symbol, "timeframe": timeframe},
            {"_id": 0, "time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        ).sort("time", -1).limit(2000))
        docs.reverse()
        if len(docs) < 20:
            print(f"[GITHUB_HISTORY] Skip {symbol} {timeframe}: only {len(docs)} candles")
            continue
        rows = []
        for doc in docs:
            row = {
                "time": int(doc["time"]),
                "open": float(doc["open"]),
                "high": float(doc["high"]),
                "low": float(doc["low"]),
                "close": float(doc["close"]),
            }
            if doc.get("volume") is not None:
                row["volume"] = float(doc["volume"])
            rows.append(row)
        symbols.setdefault(symbol, {})[timeframe] = rows
        total += len(rows)
    if not total:
        raise RuntimeError("MongoDB chưa có đủ dữ liệu nến để tạo snapshot")
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "exportDateGMT7": export_day,
        "source": "MT5 -> Server -> MongoDB",
        "timezone": "UTC",
        "symbols": symbols,
    }


def _export_mongo_history_to_github(export_day: str) -> bool:
    if not GITHUB_TOKEN:
        print("[GITHUB_HISTORY] Bỏ qua: chưa cấu hình GITHUB_TOKEN")
        return False
    api_root = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"
    encoded_path = HISTORY_EXPORT_PATH.replace("/", "%2F")

    # A successful commit for this GMT+7 date is the daily lock, including after restarts.
    commits = _github_json_request(
        f"{api_root}/commits?path={encoded_path}&per_page=1&sha={GITHUB_BRANCH}"
    )
    if commits:
        committed_at = (commits[0].get("commit", {}).get("committer", {}).get("date")
                        or commits[0].get("commit", {}).get("author", {}).get("date"))
        if committed_at:
            committed_day = datetime.fromisoformat(
                committed_at.replace("Z", "+00:00")
            ).astimezone(_GMT7).date().isoformat()
            if committed_day == export_day:
                print(f"[GITHUB_HISTORY] Đã commit snapshot hôm nay ({export_day}); không tạo commit mới")
                return True

    snapshot = _mongo_history_snapshot(export_day)
    content_url = f"{api_root}/contents/{HISTORY_EXPORT_PATH}?ref={GITHUB_BRANCH}"
    existing_sha = None
    try:
        existing = _github_json_request(content_url)
        existing_sha = existing.get("sha")
    except HTTPError as exc:
        if exc.code != 404:
            raise

    body = {
        "message": f"data: daily MongoDB candle snapshot {export_day}",
        "content": base64.b64encode(
            (json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        ).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if existing_sha:
        body["sha"] = existing_sha
    result = _github_json_request(
        f"{api_root}/contents/{HISTORY_EXPORT_PATH}", method="PUT", payload=body
    )
    commit_sha = result.get("commit", {}).get("sha", "")
    print(f"[GITHUB_HISTORY] Snapshot committed: {export_day}; candles={sum(len(v) for x in snapshot['symbols'].values() for v in x.values())}; sha={commit_sha[:12]}")
    return True


def _daily_history_export_loop() -> None:
    global _last_history_export_day
    target_hour = min(23, max(0, HISTORY_EXPORT_HOUR))
    target_minute = min(59, max(0, HISTORY_EXPORT_MINUTE))
    while not _history_export_stop.is_set():
        now = datetime.now(_GMT7)
        target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        if now >= target and _last_history_export_day != now.date().isoformat():
            export_day = now.date().isoformat()
            try:
                if _export_mongo_history_to_github(export_day):
                    _last_history_export_day = export_day
                    continue
            except Exception as exc:
                print(f"[GITHUB_HISTORY] Export failed: {type(exc).__name__}: {exc}")
            _history_export_stop.wait(900)
        else:
            wait_seconds = max(5, min(60, (target - now).total_seconds()))
            _history_export_stop.wait(wait_seconds)


def _start_daily_history_exporter() -> None:
    global _history_export_thread
    if not GITHUB_TOKEN:
        print("[GITHUB_HISTORY] Daily exporter disabled: GITHUB_TOKEN chưa được cấu hình")
        return
    if _history_export_thread and _history_export_thread.is_alive():
        return
    _history_export_stop.clear()
    _history_export_thread = threading.Thread(
        target=_daily_history_export_loop,
        name="daily-mongo-github-history",
        daemon=True,
    )
    _history_export_thread.start()
    print(f"[GITHUB_HISTORY] Daily snapshot scheduled at {HISTORY_EXPORT_HOUR:02d}:{HISTORY_EXPORT_MINUTE:02d} GMT+7")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    _start_daily_history_exporter()
    try:
        yield
    finally:
        _history_export_stop.set()
        if _history_export_thread and _history_export_thread.is_alive():
            _history_export_thread.join(timeout=2)
        global _conn, _mongo_client, _mongo_indexes_ready
        with _db_lock:
            if _conn is not None:
                _conn.close()
                _conn = None
        with _mongo_lock:
            if _mongo_client is not None:
                _mongo_client.close()
                _mongo_client = None
            _mongo_indexes_ready = False


app = FastAPI(
    title="Forex AI Server",
    version="0.5.2",
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
    candles: list[CandleItem] = Field(min_length=1, max_length=1000)

    @field_validator("symbol")
    @classmethod
    def normalize_candle_symbol(cls, value: str) -> str:
        return value.strip().upper()


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


# ---- SETTINGS & NEWS endpoints ----

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


@app.get("/api/settings", dependencies=[Depends(require_key)])
def get_settings():
    """Public read: dashboard và EA cần đọc cấu hình đồng bộ."""
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
    """Helper cho TRADE_V9: trả commission_per_lot mới nhất cho 1 symbol."""
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


# ---- Signal endpoints (2-path: GV direct trade + Manual web-chart queue) ----

def _get_latest_market_price(
    conn: sqlite3.Connection,
    symbol: str,
    timeframe: str | None = None,
) -> float | None:
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


def _validate_limit_against_market(conn: sqlite3.Connection, payload: SignalIn) -> None:
    if payload.order_type not in ("BUY_LIMIT", "SELL_LIMIT"):
        return
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
) -> dict:
    created = utc_now()
    signal_id = secrets.token_hex(12)
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
    """Nhận tín hiệu realtime để hiển thị lên Dashboard; việc vào lệnh do EA đọc trực tiếp từ GV."""
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


@app.post("/api/chart/order", dependencies=[Depends(require_key)])
def chart_order(payload: SignalIn):
    if payload.requested_lot <= 0:
        raise HTTPException(status_code=422, detail="Thiếu lot đã tính từ Risk và khoảng Entry-SL")
    with db() as conn:
        _validate_limit_against_market(conn, payload)
        return _insert_signal(conn, payload, "web-chart", status="pending")


@app.get("/api/mt5/next", dependencies=[Depends(require_key)])
def next_signal(consumer: str = Query(default="mt5", min_length=2, max_length=40)):
    """Chỉ cấp lệnh đặt tay (source='web-chart') cho EA; tín hiệu indicator đi qua GV."""
    now_dt = datetime.now(timezone.utc).astimezone(_GMT7)
    now_ts = now_dt.timestamp()
    stale_cutoff = (datetime.now(timezone.utc) - timedelta(seconds=STALE_SECONDS)).isoformat()

    with db() as conn:
        settings_row = conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
        s = _settings_row_to_dict(settings_row) if settings_row else {}

        if s.get("emergency_stop"):
            return {"ok": True, "signal": None, "reason": "emergency_stop", "consumer": consumer}

        news_status = evaluate_news_filter(s, now_ts=now_ts)
        if news_status["blocked"]:
            return {
                "ok": True,
                "signal": None,
                "reason": news_status.get("reason") or "news_window_3m",
                "news": news_status,
                "consumer": consumer,
            }

        if s.get("time_filter_enabled", True):
            start_h = int(s.get("trading_start_hour", 7))
            end_h = int(s.get("trading_end_hour", 23))
            cur_h = now_dt.hour
            in_hours = (cur_h >= start_h and cur_h < end_h) if start_h <= end_h else (cur_h >= start_h or cur_h < end_h)
            if not in_hours:
                return {"ok": True, "signal": None, "reason": "outside_trading_hours", "consumer": consumer}

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
            "WHERE id=? AND status IN ('claimed','pending','displayed','logged')",
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
def update_today_signals(payload: TodaySignalsIn):
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

    telegram_sent = (
        sum(1 for item in new_rt_signals if send_telegram_rt_signal(item))
        if telegram_enabled else 0
    )
    return {
        "ok": True,
        "count": len(payload.signals),
        "new_realtime": len(new_rt_signals),
        "telegram_sent": telegram_sent,
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "telegram_enabled": telegram_enabled,
        "updated_at": now,
    }


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
    mongo_synced = _mirror_candles_to_mongo(payload, rows)
    return {"ok": True, "symbol": payload.symbol, "timeframe": payload.timeframe,
            "received": len(rows), "mongo_synced": mongo_synced, "updated_at": now}


@app.get("/api/paper/chart")
def paper_chart_data(
    symbol: str = Query(min_length=3, max_length=32),
    timeframe: Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"] = Query(default="M15"),
    limit: int = Query(default=500, ge=50, le=2000),
):
    """Public read-only candles for Paper; no API key and no broker/order access."""
    collection = _paper_candles_collection()
    if collection is None:
        raise HTTPException(status_code=503, detail="MongoDB Paper feed chưa sẵn sàng")
    symbol = symbol.strip().upper()
    docs = list(collection.find(
        {"symbol": symbol, "timeframe": timeframe},
        {"_id": 0, "time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
    ).sort("time", -1).limit(limit))
    docs.reverse()
    if not docs:
        raise HTTPException(status_code=404, detail=f"Chưa có dữ liệu {symbol} {timeframe}")
    closes = [float(item["close"]) for item in docs]
    rsi_data = [
        {"time": docs[index]["time"], "value": round(value, 4)}
        for index, value in _rsi_values(closes)
    ]
    return {
        "mode": "PAPER_READ_ONLY",
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": docs,
        "rsi": rsi_data,
        "count": len(docs),
        "latest_time": docs[-1]["time"],
        "server_time": utc_now(),
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


@app.get("/api/dashboard", dependencies=[Depends(require_key)])
def dashboard_data():
    _cleanup_recently_acked()
    with db() as conn:
        _expire_stale_pending_modifies(conn)
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
