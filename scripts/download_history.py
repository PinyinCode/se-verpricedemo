#!/usr/bin/env python3
"""Download real historical OHLC candles from Yahoo Finance into docs/data/history.json."""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "XAUUSD=X",
    "BTCUSD": "BTC-USD",
    "USDCAD": "CAD=X",
    "NZDUSD": "NZDUSD=X",
    "USDCHF": "CHF=X",
}
INTERVALS = {"M15": ("15m", "60d"), "H1": ("60m", "730d")}
OUT = Path("docs/data/history.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HistoricalDataBot/1.0)"}


def download(ticker, interval, period):
    query = urllib.parse.urlencode({"range": period, "interval": interval, "events": "history"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker, safe='')}?{query}"
    last_error = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=35) as response:
                payload = json.loads(response.read().decode("utf-8"))
            result = payload["chart"]["result"][0]
            timestamps = result.get("timestamp") or []
            quote = result["indicators"]["quote"][0]
            rows = []
            for i, stamp in enumerate(timestamps):
                values = [quote.get(k, [None] * len(timestamps))[i] for k in ("open", "high", "low", "close")]
                if stamp is None or any(v is None for v in values):
                    continue
                rows.append({
                    "time": int(stamp),
                    "open": round(float(values[0]), 8),
                    "high": round(float(values[1]), 8),
                    "low": round(float(values[2]), 8),
                    "close": round(float(values[3]), 8),
                })
            if len(rows) < 20:
                raise RuntimeError(f"Only {len(rows)} valid candles for {ticker} {interval}")
            return rows
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Download failed for {ticker} {interval}: {last_error}")


def main():
    data = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Yahoo Finance chart data",
        "timezone": "UTC",
        "symbols": {},
    }
    failures = []
    for symbol, ticker in SYMBOLS.items():
        data["symbols"][symbol] = {}
        for tf, (interval, period) in INTERVALS.items():
            try:
                rows = download(ticker, interval, period)
                data["symbols"][symbol][tf] = rows
                print(f"OK {symbol} {tf}: {len(rows)} candles")
            except Exception as exc:
                failures.append(str(exc))
                print(f"ERROR {symbol} {tf}: {exc}")
            time.sleep(1)
    # Fail the workflow instead of committing an incomplete weekly snapshot.
    expected = len(SYMBOLS) * len(INTERVALS)
    actual = sum(len(v) for v in data["symbols"].values())
    if actual != expected:
        raise SystemExit("Incomplete historical snapshot; nothing will be committed. " + " | ".join(failures))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
