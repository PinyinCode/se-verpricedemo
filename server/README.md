# Forex AI Server — Demo Bridge

This is the first deployable foundation for the Python API + MT5 + dashboard architecture.

## Safety boundary
- The included MT5 EA is **paper/demo signal receiver only**.
- It contains no `OrderSend`, `CTrade.Buy`, or `CTrade.Sell` calls.
- It validates and logs signals, then acknowledges them to the API.
- Do not add live execution until the data pipeline, risk checks, and demo test suite have been independently reviewed.

## Run on the Windows VPS
1. Install Python 3.11+.
2. Open PowerShell in the `server` folder.
3. Create a virtual environment and install dependencies:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

4. Set a long random API key (keep it private):

   ```powershell
   $env:FOREX_API_KEY = "REPLACE_WITH_A_LONG_RANDOM_SECRET"
   ```

5. Start the service locally:

   ```powershell
   uvicorn app:app --host 127.0.0.1 --port 8000
   ```

6. Open `http://127.0.0.1:8000` on the VPS.

Keep the service bound to localhost initially. For remote access, put it behind HTTPS with a reverse proxy and Windows Firewall restrictions; never expose port 8000 directly to the public internet.

## MT5 setup
1. Compile `mt5/ServerConnector_Demo.mq5` in MetaEditor.
2. Attach it to one chart in the demo terminal.
3. Set `ServerBaseURL=http://127.0.0.1:8000` and the same `ApiKey`.
4. In MT5, add `http://127.0.0.1:8000` under Tools > Options > Expert Advisors > Allow WebRequest for listed URL.
5. Confirm the dashboard shows the MT5-Connector heartbeat as online.

## Create a test signal
From PowerShell on the VPS:

```powershell
$headers = @{ "X-API-Key" = $env:FOREX_API_KEY }
$body = @{
  symbol="EURUSD"; timeframe="M15"; direction="BUY";
  entry=1.08500; sl=1.08300; score=72; source="manual-test"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/signals" -Headers $headers -ContentType "application/json" -Body $body
```

The EA should print `[PAPER SIGNAL]`, acknowledge the signal, and the dashboard should show it as completed / SIMULATED.

## API endpoints
- `GET /api/health` — service and SQLite health.
- `POST /api/signals` — create a signal (API key required).
- `GET /api/mt5/next` — claim the next pending signal (API key required).
- `POST /api/mt5/ack` — acknowledge a claimed signal (API key required).
- `POST /api/heartbeat` — update a component heartbeat (API key required).
- `GET /api/dashboard` — dashboard data.
- `GET /` — web dashboard.

Database defaults to `server/forex_demo.db`; override with `FOREX_DB_PATH`.
