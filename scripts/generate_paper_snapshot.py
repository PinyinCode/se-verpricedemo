#!/usr/bin/env python3
"""Build a public, read-only Paper chart snapshot from the authenticated candle API."""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = os.environ["FOREX_API_BASE"].rstrip("/")
API_KEY = os.environ["FOREX_API_KEY"]
OUT = Path("docs/data/paper-snapshot.json")
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "USDCAD", "NZDUSD", "USDCHF"]
TIMEFRAMES = ["M15", "H1"]
GMT7 = timezone(timedelta(hours=7))


def rsi_series(closes, period=14):
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def value(g, l):
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[period] = value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = value(avg_gain, avg_loss)
    return out


def fetch(symbol, timeframe):
    query = urllib.parse.urlencode({"symbol": symbol, "timeframe": timeframe, "limit": 2000})
    req = urllib.request.Request(
        f"{BASE}/api/chart/data?{query}",
        headers={"X-API-Key": API_KEY, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))
    candles = payload.get("candles") or []
    if not candles:
        raise RuntimeError(f"No candles for {symbol} {timeframe}; refusing to publish an empty snapshot")
    candles.sort(key=lambda c: int(c["time"]))
    latest_day = datetime.fromtimestamp(int(candles[-1]["time"]), timezone.utc).astimezone(GMT7).date()
    values = rsi_series([float(c["close"]) for c in candles])
    selected = []
    rsi = []
    for candle, indicator in zip(candles, values):
        day = datetime.fromtimestamp(int(candle["time"]), timezone.utc).astimezone(GMT7).date()
        if day != latest_day:
            continue
        selected.append({
            "time": int(candle["time"]),
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": float(candle.get("volume") or 0),
        })
        if indicator is not None:
            rsi.append({"time": int(candle["time"]), "value": round(indicator, 4)})
    if not selected:
        raise RuntimeError(f"No latest-day candles for {symbol} {timeframe}")
    return {
        "trading_day": latest_day.isoformat(),
        "candles": selected,
        "rsi": rsi,
        "digits": int(payload.get("digits", 5)),
        "server_time": payload.get("server_time"),
    }


def main():
    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timezone": "Asia/Ho_Chi_Minh",
        "source": "authenticated MT5 candle feed; read-only snapshot",
        "data": {},
    }
    for symbol in SYMBOLS:
        snapshot["data"][symbol] = {}
        for timeframe in TIMEFRAMES:
            print(f"Fetching {symbol} {timeframe}...", flush=True)
            snapshot["data"][symbol][timeframe] = fetch(symbol, timeframe)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(OUT)
    print(f"Published snapshot: {OUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Snapshot generation failed: {exc}", file=sys.stderr)
        raise
