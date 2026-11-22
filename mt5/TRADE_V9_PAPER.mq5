//+------------------------------------------------------------------+
//| TRADE_V9_PAPER.mq5                                               |
//| Forex AI Server consumer - REAL orders only, hub executor.       |
//| Pending orders: LIMIT ONLY (BUY_LIMIT / SELL_LIMIT).             |
//| Tự tính SL/TP/Lot, tự trailing/BE, đọc settings từ server.       |
//+------------------------------------------------------------------+
#property strict
#property version "3.10"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

//--- Inputs ---------------------------------------------------------
input string ServerBaseURL       = "https://investforex.onrender.com";
input string ApiKey              = "SET_A_LONG_RANDOM_API_KEY";
input string ConsumerId          = "trade-v9";
input int    PollSeconds         = 3;
input int    SettingsPollSeconds = 5;
input int    CommissionPollSeconds = 60;
input int    HeartbeatSeconds    = 15;
input int    TrailingCheckSeconds = 2;

input long   MagicNumber         = 20240915;
input int    SlippagePoints      = 20;
input string OrderComment        = "TRADE-V9";
input bool   SyncRealHistory     = true;
input bool   AllowCancelExternalPending = false;

//--- Globals --------------------------------------------------------
CTrade        trade;
CPositionInfo posInfo;

// ===== Settings cache (poll từ server) =====
struct SSettings {
   bool   auto_trade_enabled;
   bool   trailing_enabled;
   bool   breakeven_enabled;
   bool   news_filter_enabled;
   bool   time_filter_enabled;
   bool   emergency_stop;
   bool   emergency_close_all;
   double risk_money;
   int    max_positions;
   double reward_risk_ratio;
   double risk_tolerance_pct;
   double commission_fallback;
   int    spread_buffer_points;
   double trailing_trigger_rr;
   double trailing_lock_rr;
   double breakeven_trigger_rr;
   string trailing_mode;
   int    trading_start_hour;
   int    trading_end_hour;
   datetime last_synced;
};
SSettings g_settings;

// ===== Commission cache =====
struct SCommission {
   string symbol;
   double commission_per_lot;
   double swap_long;
   double swap_short;
   double contract_size;
   double tick_value;
   double point_size;
   datetime received_at;
};
SCommission g_commissionCache[];

// ===== Trailing state =====
struct STrailState {
   ulong  ticket;
   string sym;
   double entryPrice;
   double initialSL;
   double oneR;
   double lockedSL;
   bool   breakEvenDone;
   bool   trailingActivated;
};
STrailState g_trailStates[];

// ===== Signal & processing dedup =====
string   processedSignalIds[];
string   processedSltpRequestIds[];
string   processedPendingModifyIds[];
string   lastHistoryDigest = "";

// ===== Internal tickets =====
string   realTicketIds[];

// ===== Timers =====
uint lastPollMs              = 0;
uint lastSettingsPollMs      = 0;
uint lastCommissionPollMs    = 0;
uint lastPositionsSyncMs     = 0;
uint lastHeartbeatMs         = 0;
uint lastHistorySyncMs       = 0;
uint lastSltpPollMs          = 0;
uint lastPendingModifyPollMs = 0;
uint lastTrailingCheckMs     = 0;

// ===== Server GMT offset =====
int g_serverToGMTOffset = 0;
datetime g_lastOffsetCalcTime = 0;

// ===== Emergency =====
bool g_emergencyInProgress = false;

//--- Forward declarations -------------------------------------------
void SendAccountSnapshot();
void PublishRealClosedDeals();
int  CountRealExposure();
void PollSettings();
void PollCommissions();
void ManageTrailing();
void CheckEmergencyStop();

//--- Time helpers ---------------------------------------------------
void CalculateServerGMTOffset()
{
   datetime st = TimeTradeServer(), gt = TimeGMT();
   if(st <= 0 || gt <= 0) return;
   g_serverToGMTOffset = (int)(st - gt);
   int h = (int)MathRound((double)g_serverToGMTOffset / 3600.0);
   g_serverToGMTOffset = h * 3600;
   g_lastOffsetCalcTime = TimeCurrent();
}

datetime ServerTimeToGMT7(datetime st)
{
   return (st - g_serverToGMTOffset) + 7 * 3600;
}

datetime GetGMT7Now()
{
   datetime st = TimeTradeServer();
   if(st <= 0) st = TimeCurrent();
   return ServerTimeToGMT7(st);
}

bool IsInTradingHours()
{
   if(!g_settings.time_filter_enabled) return true;
   datetime gmt7 = GetGMT7Now();
   MqlDateTime dt;
   TimeToStruct(gmt7, dt);
   int h = dt.hour;
   int start = g_settings.trading_start_hour;
   int end = g_settings.trading_end_hour;
   if(start <= end) return (h >= start && h < end);
   return (h >= start || h < end);
}

bool IsNewsBlocked()
{
   if(!g_settings.news_filter_enabled) return false;
   if(!GlobalVariableCheck("GV_NewsFilter_Blocked")) return false;
   return (GlobalVariableGet("GV_NewsFilter_Blocked") >= 0.5);
}

//--- JSON helpers ---------------------------------------------------
string JsonEscape(string s)
{
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   StringReplace(s, "\r", " ");
   StringReplace(s, "\n", " ");
   return s;
}

string UpperTrim(string s)
{
   StringTrimLeft(s);
   StringTrimRight(s);
   StringToUpper(s);
   return s;
}

string JsonValue(string json, string key)
{
   string token = "\"" + key + "\"";
   int p = StringFind(json, token);
   if(p < 0) return "";
   p = StringFind(json, ":", p + StringLen(token));
   if(p < 0) return "";
   p++;
   while(p < StringLen(json) && (StringGetCharacter(json,p)==' ' ||
         StringGetCharacter(json,p)=='\t' || StringGetCharacter(json,p)=='\r' ||
         StringGetCharacter(json,p)=='\n')) p++;
   if(p >= StringLen(json)) return "";
   if(StringGetCharacter(json,p)=='"')
   {
      string out = "";
      for(int i = p+1; i < StringLen(json); i++)
      {
         ushort ch = StringGetCharacter(json, i);
         if(ch == '\\' && i+1 < StringLen(json))
         {
            ushort nx = StringGetCharacter(json, i+1);
            if(nx == '"' || nx == '\\') { out += ShortToString(nx); i++; continue; }
         }
         if(ch == '"') return out;
         out += ShortToString(ch);
      }
      return "";
   }
   int e = p;
   while(e < StringLen(json) && StringGetCharacter(json,e)!=',' &&
         StringGetCharacter(json,e)!='}' && StringGetCharacter(json,e)!=']' &&
         StringGetCharacter(json,e)!='\n') e++;
   string v = StringSubstr(json, p, e-p);
   StringTrimLeft(v); StringTrimRight(v);
   if(v == "null") return "";
   return v;
}

string JsonObjectValue(string json, string key)
{
   string token = "\"" + key + "\"";
   int p = StringFind(json, token);
   if(p < 0) return "";
   p = StringFind(json, ":", p + StringLen(token));
   if(p < 0) return "";
   p++;
   while(p < StringLen(json) && StringGetCharacter(json,p)<=32) p++;
   if(p >= StringLen(json) || StringGetCharacter(json,p)!='{') return "";
   int depth = 0; bool quoted = false;
   for(int i = p; i < StringLen(json); i++)
   {
      ushort c = StringGetCharacter(json, i);
      if(c == '"' && (i == 0 || StringGetCharacter(json,i-1) != '\\')) quoted = !quoted;
      if(quoted) continue;
      if(c == '{') depth++;
      if(c == '}' && --depth == 0) return StringSubstr(json, p, i-p+1);
   }
   return "";
}

string JsonArrayValue(string json, string key)
{
   string token = "\"" + key + "\"";
   int p = StringFind(json, token);
   if(p < 0) return "";
   p = StringFind(json, ":", p + StringLen(token));
   if(p < 0) return "";
   p++;
   while(p < StringLen(json) && StringGetCharacter(json,p)<=32) p++;
   if(p >= StringLen(json) || StringGetCharacter(json,p)!='[') return "";
   int depth = 0; bool quoted = false;
   for(int i = p; i < StringLen(json); i++)
   {
      ushort c = StringGetCharacter(json, i);
      if(c == '"' && (i == 0 || StringGetCharacter(json,i-1) != '\\')) quoted = !quoted;
      if(quoted) continue;
      if(c == '[') depth++;
      if(c == ']' && --depth == 0) return StringSubstr(json, p, i-p+1);
   }
   return "";
}

bool JsonBoolValue(string json, string key, bool defaultValue)
{
   string v = JsonValue(json, key);
   if(v == "") return defaultValue;
   if(v == "true" || v == "1") return true;
   if(v == "false" || v == "0") return false;
   return defaultValue;
}

void ExtractTopLevelObjects(string arrJson, string &outObjects[])
{
   ArrayResize(outObjects, 0);
   int depth = 0; bool quoted = false; int start = -1;
   for(int i = 0; i < StringLen(arrJson); i++)
   {
      ushort c = StringGetCharacter(arrJson, i);
      if(c == '"' && (i == 0 || StringGetCharacter(arrJson,i-1) != '\\')) quoted = !quoted;
      if(quoted) continue;
      if(c == '{')
      {
         if(depth == 0) start = i;
         depth++;
      }
      else if(c == '}')
      {
         depth--;
         if(depth == 0 && start >= 0)
         {
            int n = ArraySize(outObjects);
            ArrayResize(outObjects, n+1);
            outObjects[n] = StringSubstr(arrJson, start, i-start+1);
            start = -1;
         }
      }
   }
}

//--- HTTP -----------------------------------------------------------
bool Request(string method, string path, string body, string &response, int &status)
{
   char data[], result[];
   if(body != "")
   {
      StringToCharArray(body, data, 0, WHOLE_ARRAY, CP_UTF8);
      int n = ArraySize(data); if(n > 0 && data[n-1] == 0) ArrayResize(data, n-1);
   }
   else ArrayResize(data, 0);
   string headers = "X-API-Key: " + ApiKey + "\r\nContent-Type: application/json\r\n";
   string responseHeaders;
   status = WebRequest(method, ServerBaseURL + path, headers, 5000, data, result, responseHeaders);
   if(status == -1) { Print("[V9] WebRequest error=", GetLastError()); response=""; return false; }
   response = CharArrayToString(result, 0, -1, CP_UTF8);
   return status >= 200 && status < 300;
}

//--- Dedup tracking -------------------------------------------------
bool HasProcessedSignal(string id)
{
   for(int i = 0; i < ArraySize(processedSignalIds); i++)
      if(processedSignalIds[i] == id) return true;
   return false;
}

void MarkProcessedSignal(string id)
{
   if(id == "" || HasProcessedSignal(id)) return;
   int n = ArraySize(processedSignalIds);
   if(n >= 500)
   {
      for(int i = 0; i < n-1; i++) processedSignalIds[i] = processedSignalIds[i+1];
      processedSignalIds[n-1] = id;
      return;
   }
   ArrayResize(processedSignalIds, n+1);
   processedSignalIds[n] = id;
}

bool HasProcessedSltp(string id)
{
   for(int i = 0; i < ArraySize(processedSltpRequestIds); i++)
      if(processedSltpRequestIds[i] == id) return true;
   return false;
}

void MarkProcessedSltp(string id)
{
   if(id == "" || HasProcessedSltp(id)) return;
   int n = ArraySize(processedSltpRequestIds);
   if(n >= 500)
   {
      for(int i = 0; i < n-1; i++) processedSltpRequestIds[i] = processedSltpRequestIds[i+1];
      processedSltpRequestIds[n-1] = id;
      return;
   }
   ArrayResize(processedSltpRequestIds, n+1);
   processedSltpRequestIds[n] = id;
}

bool HasProcessedPendingModify(string id)
{
   for(int i = 0; i < ArraySize(processedPendingModifyIds); i++)
      if(processedPendingModifyIds[i] == id) return true;
   return false;
}

void MarkProcessedPendingModify(string id)
{
   if(id == "" || HasProcessedPendingModify(id)) return;
   int n = ArraySize(processedPendingModifyIds);
   if(n >= 500)
   {
      for(int i = 0; i < n-1; i++) processedPendingModifyIds[i] = processedPendingModifyIds[i+1];
      processedPendingModifyIds[n-1] = id;
      return;
   }
   ArrayResize(processedPendingModifyIds, n+1);
   processedPendingModifyIds[n] = id;
}

//--- Settings polling -----------------------------------------------
void InitDefaultSettings()
{
   g_settings.auto_trade_enabled    = true;
   g_settings.trailing_enabled      = true;
   g_settings.breakeven_enabled     = true;
   g_settings.news_filter_enabled   = true;
   g_settings.time_filter_enabled   = true;
   g_settings.emergency_stop        = false;
   g_settings.emergency_close_all   = true;
   g_settings.risk_money            = 25.0;
   g_settings.max_positions         = 3;
   g_settings.reward_risk_ratio     = 2.0;
   g_settings.risk_tolerance_pct    = 0.5;
   g_settings.commission_fallback   = 0.0;
   g_settings.spread_buffer_points  = 20;
   g_settings.trailing_trigger_rr   = 1.0;
   g_settings.trailing_lock_rr      = 0.5;
   g_settings.breakeven_trigger_rr  = 0.5;
   g_settings.trailing_mode         = "static";
   g_settings.trading_start_hour    = 7;
   g_settings.trading_end_hour      = 23;
   g_settings.last_synced           = 0;
}

void PollSettings()
{
   string response; int status;
   if(!Request("GET", "/api/settings", "", response, status))
   {
      Print("[V9 SETTINGS] Poll failed HTTP=", status);
      return;
   }
   string obj = JsonObjectValue(response, "settings");
   if(obj == "") return;

   g_settings.auto_trade_enabled    = JsonBoolValue(obj, "auto_trade_enabled", true);
   g_settings.trailing_enabled      = JsonBoolValue(obj, "trailing_enabled", true);
   g_settings.breakeven_enabled     = JsonBoolValue(obj, "breakeven_enabled", true);
   g_settings.news_filter_enabled   = JsonBoolValue(obj, "news_filter_enabled", true);
   g_settings.time_filter_enabled   = JsonBoolValue(obj, "time_filter_enabled", true);
   g_settings.emergency_stop        = JsonBoolValue(obj, "emergency_stop", false);
   g_settings.emergency_close_all   = JsonBoolValue(obj, "emergency_close_all", true);
   g_settings.risk_money            = StringToDouble(JsonValue(obj, "risk_money"));
   g_settings.max_positions         = (int)StringToInteger(JsonValue(obj, "max_positions"));
   g_settings.reward_risk_ratio     = StringToDouble(JsonValue(obj, "reward_risk_ratio"));
   g_settings.risk_tolerance_pct    = StringToDouble(JsonValue(obj, "risk_tolerance_pct"));
   g_settings.commission_fallback   = StringToDouble(JsonValue(obj, "commission_fallback"));
   g_settings.spread_buffer_points  = (int)StringToInteger(JsonValue(obj, "spread_buffer_points"));
   g_settings.trailing_trigger_rr   = StringToDouble(JsonValue(obj, "trailing_trigger_rr"));
   g_settings.trailing_lock_rr      = StringToDouble(JsonValue(obj, "trailing_lock_rr"));
   g_settings.breakeven_trigger_rr  = StringToDouble(JsonValue(obj, "breakeven_trigger_rr"));
   g_settings.trailing_mode         = JsonValue(obj, "trailing_mode");
   if(g_settings.trailing_mode == "") g_settings.trailing_mode = "static";
   g_settings.trading_start_hour    = (int)StringToInteger(JsonValue(obj, "trading_start_hour"));
   g_settings.trading_end_hour      = (int)StringToInteger(JsonValue(obj, "trading_end_hour"));
   g_settings.last_synced           = TimeCurrent();

   // Sanity
   if(g_settings.risk_money <= 0) g_settings.risk_money = 25;
   if(g_settings.max_positions < 0) g_settings.max_positions = 0;
   if(g_settings.reward_risk_ratio <= 0) g_settings.reward_risk_ratio = 2.0;
   if(g_settings.spread_buffer_points < 0) g_settings.spread_buffer_points = 0;
}

//--- Commission cache -----------------------------------------------
void PollCommissions()
{
   string response; int status;
   if(!Request("GET", "/api/mt5/commissions", "", response, status))
   {
      Print("[V9 COMM] Poll failed HTTP=", status);
      return;
   }
   string arr = JsonArrayValue(response, "items");
   if(arr == "") return;

   string items[];
   ExtractTopLevelObjects(arr, items);
   ArrayResize(g_commissionCache, 0);

   for(int i = 0; i < ArraySize(items); i++)
   {
      SCommission c;
      c.symbol             = UpperTrim(JsonValue(items[i], "symbol"));
      c.commission_per_lot = StringToDouble(JsonValue(items[i], "commission_per_lot"));
      c.swap_long          = StringToDouble(JsonValue(items[i], "swap_long"));
      c.swap_short         = StringToDouble(JsonValue(items[i], "swap_short"));
      c.contract_size      = StringToDouble(JsonValue(items[i], "contract_size"));
      c.tick_value         = StringToDouble(JsonValue(items[i], "tick_value"));
      c.point_size         = StringToDouble(JsonValue(items[i], "point_size"));
      c.received_at        = TimeCurrent();
      if(c.symbol == "") continue;
      int n = ArraySize(g_commissionCache);
      ArrayResize(g_commissionCache, n+1);
      g_commissionCache[n] = c;
   }
   Print("[V9 COMM] Updated cache: ", ArraySize(g_commissionCache), " symbols");
}

double GetCommissionPerLot(string symbol)
{
   string symUp = symbol;
   StringToUpper(symUp);
   for(int i = 0; i < ArraySize(g_commissionCache); i++)
      if(g_commissionCache[i].symbol == symUp)
         return g_commissionCache[i].commission_per_lot;
   return g_settings.commission_fallback;
}

//--- Volume normalization ------------------------------------------
double NormalizeVolume(string symbol, double raw)
{
   double vmin = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0) step = 0.01;
   if(raw < vmin || raw > vmax) return 0;
   double v = MathFloor((raw + 1e-10) / step) * step;
   if(v < vmin) return 0;
   return NormalizeDouble(v, 8);
}

ENUM_ORDER_TYPE_FILLING PickFilling(string symbol)
{
   long modes = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   if((modes & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
   if((modes & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

//--- Lot size calculation with commission + spread ------------------
double CalculateLotWithCommission(string symbol, double slDistance, double riskMoney)
{
   double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickValue <= 0) tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);

   if(tickSize <= 0 || tickValue <= 0 || point <= 0) return 0;
   if(slDistance <= 0 || riskMoney <= 0) return 0;

   // Risk per 1.0 lot từ SL distance
   double slRiskPerLot = (slDistance / tickSize) * tickValue;

   // Commission per lot
   double commission = GetCommissionPerLot(symbol);

   // Spread cost per lot (tính theo giá hiện tại)
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double spreadPoints = (ask > 0 && bid > 0) ? (ask - bid) / point : 0;
   spreadPoints += g_settings.spread_buffer_points;  // buffer an toàn
   double spreadCostPerLot = (spreadPoints * point / tickSize) * tickValue;

   double riskPerLot = slRiskPerLot + commission + spreadCostPerLot;
   if(riskPerLot <= 0) return 0;

   double lot = riskMoney / riskPerLot;
   double vmin = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0) step = 0.01;

   lot = MathFloor((lot + 1e-10) / step) * step;

   if(lot < vmin) return 0;  // Không đủ lot tối thiểu → reject
   if(lot > vmax) lot = vmax;

   return NormalizeDouble(lot, 8);
}

//--- Count positions -----------------------------------------------
int CountRealPositions()
{
   int n = 0;
   for(int i = PositionsTotal()-1; i >= 0; i--)
      if(posInfo.SelectByIndex(i) && posInfo.Magic() == MagicNumber) n++;
   return n;
}

int CountRealLimitOrders()
{
   int n = 0;
   for(int i = OrdersTotal()-1; i >= 0; i--)
   {
      ulong tk = OrderGetTicket(i);
      if(tk == 0) continue;
      if(OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;
      ENUM_ORDER_TYPE ot = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      if(ot == ORDER_TYPE_BUY_LIMIT || ot == ORDER_TYPE_SELL_LIMIT)
         n++;
   }
   return n;
}

int CountRealExposure()
{
   return CountRealPositions() + CountRealLimitOrders();
}

//--- Ack ------------------------------------------------------------
bool Ack(string id, string state, string message)
{
   Print("[V9 ACK] signal=", id, " status=", state, " message=", message);
   string body = "{\"signal_id\":\""+JsonEscape(id)+"\",\"status\":\""+JsonEscape(state)+"\",\"message\":\""+JsonEscape(message)+"\"}";
   string response; int status;
   return Request("POST", "/api/mt5/ack", body, response, status);
}

//--- Trailing state tracking ----------------------------------------
int FindTrailIndex(ulong ticket)
{
   for(int i = 0; i < ArraySize(g_trailStates); i++)
      if(g_trailStates[i].ticket == ticket) return i;
   return -1;
}

void PruneTrailStates()
{
   for(int i = ArraySize(g_trailStates) - 1; i >= 0; i--)
   {
      if(!PositionSelectByTicket(g_trailStates[i].ticket))
      {
         int last = ArraySize(g_trailStates) - 1;
         for(int j = i; j < last; j++)
            g_trailStates[j] = g_trailStates[j+1];
         ArrayResize(g_trailStates, last);
      }
   }
}

//--- Trailing + Break-Even -----------------------------------------
void ManageTrailing()
{
   if(!g_settings.trailing_enabled && !g_settings.breakeven_enabled) return;
   if(g_settings.emergency_stop) return;

   PruneTrailStates();

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !posInfo.SelectByTicket(ticket)) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      string sym = PositionGetString(POSITION_SYMBOL);
      bool isBuy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      double entryPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL = PositionGetDouble(POSITION_SL);
      double currentTP = PositionGetDouble(POSITION_TP);
      double currentPrice = isBuy ? SymbolInfoDouble(sym, SYMBOL_BID) : SymbolInfoDouble(sym, SYMBOL_ASK);
      if(currentPrice <= 0) continue;

      double point = SymbolInfoDouble(sym, SYMBOL_POINT);
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      int stopLevel = (int)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL);
      double minDist = stopLevel * point;

      // Tìm hoặc tạo state
      int idx = FindTrailIndex(ticket);
      if(idx < 0)
      {
         STrailState ns;
         ns.ticket = ticket;
         ns.sym = sym;
         ns.entryPrice = entryPrice;
         ns.initialSL = currentSL;
         ns.oneR = MathAbs(entryPrice - currentSL);
         if(ns.oneR <= point) ns.oneR = 10 * point;
         ns.lockedSL = 0;
         ns.breakEvenDone = false;
         ns.trailingActivated = false;
         int n = ArraySize(g_trailStates);
         ArrayResize(g_trailStates, n+1);
         g_trailStates[n] = ns;
         idx = n;
      }

      STrailState state = g_trailStates[idx];

      double profitDist = isBuy ? (currentPrice - entryPrice) : (entryPrice - currentPrice);
      double rAchieved = (state.oneR > 0) ? (profitDist / state.oneR) : 0;

      double newSL = currentSL;
      bool needModify = false;
      string modifyReason = "";

      // Break-even
      if(g_settings.breakeven_enabled && !state.breakEvenDone && rAchieved >= g_settings.breakeven_trigger_rr)
      {
         double beSL = isBuy ? (entryPrice + 0.2 * state.oneR) : (entryPrice - 0.2 * state.oneR);
         bool beBetter = isBuy ? (beSL > currentSL + point) : (beSL < currentSL - point);
         bool priceFarEnough = isBuy ? (currentPrice > entryPrice + minDist) : (currentPrice < entryPrice - minDist);
         if(beBetter && priceFarEnough && !state.trailingActivated)
         {
            newSL = beSL;
            state.breakEvenDone = true;
            needModify = true;
            modifyReason = "BE";
         }
      }

      // Trailing
      if(g_settings.trailing_enabled && rAchieved >= g_settings.trailing_trigger_rr)
      {
         double trailDist = g_settings.trailing_lock_rr * state.oneR;

         if(g_settings.trailing_mode == "static")
         {
            if(!state.trailingActivated)
            {
               double activationPrice = isBuy
                  ? (entryPrice + g_settings.trailing_trigger_rr * state.oneR)
                  : (entryPrice - g_settings.trailing_trigger_rr * state.oneR);
               state.lockedSL = isBuy
                  ? (activationPrice - trailDist)
                  : (activationPrice + trailDist);
               state.trailingActivated = true;
               state.breakEvenDone = true;
            }
            bool tsBetter = isBuy ? (state.lockedSL > currentSL + point) : (state.lockedSL < currentSL - point);
            if(tsBetter)
            {
               newSL = state.lockedSL;
               needModify = true;
               if(modifyReason == "") modifyReason = "TS";
            }
         }
         else // dynamic
         {
            double trailSL = isBuy ? (currentPrice - trailDist) : (currentPrice + trailDist);
            bool tsBetter = isBuy ? (trailSL > currentSL + point) : (trailSL < currentSL - point);
            if(tsBetter)
            {
               newSL = trailSL;
               state.trailingActivated = true;
               state.breakEvenDone = true;
               needModify = true;
               if(modifyReason == "") modifyReason = "TS";
            }
         }
      }

      if(needModify)
      {
         bool valid = isBuy
            ? (newSL < currentPrice - minDist)
            : (newSL > currentPrice + minDist);
         if(valid && MathAbs(newSL - currentSL) > point)
         {
            trade.SetExpertMagicNumber(MagicNumber);
            trade.SetDeviationInPoints(SlippagePoints);
            trade.SetTypeFilling(PickFilling(sym));
            trade.SetAsyncMode(false);
            if(trade.PositionModify(ticket, newSL, currentTP))
            {
               g_trailStates[idx] = state;
               Print("[V9 ", modifyReason, "] ", sym, " ticket=", ticket,
                     " R=", DoubleToString(rAchieved, 2),
                     " SL ", DoubleToString(currentSL, digits), " -> ", DoubleToString(newSL, digits));
            }
         }
      }
      else
      {
         g_trailStates[idx] = state;
      }
   }
}

//--- Emergency stop -------------------------------------------------
void CloseAllMagicPositions()
{
   Print("[V9 EMERGENCY] Closing all positions magic=", MagicNumber);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      string sym = PositionGetString(POSITION_SYMBOL);
      trade.SetExpertMagicNumber(MagicNumber);
      trade.SetDeviationInPoints(SlippagePoints);
      trade.SetTypeFilling(PickFilling(sym));
      trade.SetAsyncMode(false);
      if(trade.PositionClose(ticket))
         Print("[V9 EMERGENCY] Closed position ticket=", ticket, " ", sym);
      else
         Print("[V9 EMERGENCY] FAILED close ticket=", ticket, " retcode=", trade.ResultRetcode());
   }
}

void CancelAllMagicPending()
{
   Print("[V9 EMERGENCY] Cancelling all pending magic=", MagicNumber);
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;
      ENUM_ORDER_TYPE ot = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      if(ot != ORDER_TYPE_BUY_LIMIT && ot != ORDER_TYPE_SELL_LIMIT &&
         ot != ORDER_TYPE_BUY_STOP && ot != ORDER_TYPE_SELL_STOP) continue;
      trade.SetExpertMagicNumber(MagicNumber);
      trade.SetAsyncMode(false);
      if(trade.OrderDelete(ticket))
         Print("[V9 EMERGENCY] Deleted pending ticket=", ticket);
      else
         Print("[V9 EMERGENCY] FAILED delete ticket=", ticket, " retcode=", trade.ResultRetcode());
   }
}

void CheckEmergencyStop()
{
   if(!g_settings.emergency_stop) { g_emergencyInProgress = false; return; }
   if(g_emergencyInProgress) return;
   g_emergencyInProgress = true;

   if(g_settings.emergency_close_all)
   {
      CloseAllMagicPositions();
      CancelAllMagicPending();
      SendAccountSnapshot();
   }
   Print("[V9 EMERGENCY] Stop complete");
}

//--- Heartbeat ------------------------------------------------------
void SendHeartbeat()
{
   int openCount = CountRealExposure();
   string body = StringFormat(
      "{\"component\":\"TRADE-V9-REAL\",\"status\":\"online\",\"details\":\"REAL consumer active; positions=%d; emergency=%s\"}",
      openCount, g_settings.emergency_stop ? "ON" : "OFF");
   string response; int status;
   if(Request("POST", "/api/heartbeat", body, response, status))
      lastHeartbeatMs = GetTickCount();
}

//--- Open trade -----------------------------------------------------
bool ResolveOrderType(string rawOrderType, string direction, string &normOrderType, string &errOut)
{
   string ot = UpperTrim(rawOrderType);
   string dir = UpperTrim(direction);
   if(StringFind(ot, "STOP") >= 0) { errOut = "Stop orders not supported"; return false; }
   if(ot == "" || ot == "MARKET")
   {
      normOrderType = "MARKET";
      if(dir == "BUY" || dir == "SELL") return true;
      errOut = "Invalid direction for MARKET"; return false;
   }
   if(ot == "BUY_LIMIT")
   {
      normOrderType = "BUY_LIMIT";
      if(dir != "BUY") { errOut = "BUY_LIMIT requires direction=BUY"; return false; }
      return true;
   }
   if(ot == "SELL_LIMIT")
   {
      normOrderType = "SELL_LIMIT";
      if(dir != "SELL") { errOut = "SELL_LIMIT requires direction=SELL"; return false; }
      return true;
   }
   errOut = "Unsupported order_type: " + ot;
   return false;
}

bool OpenTrade(string id, string symbol, string tf, string direction, string rawOrderType,
               double entry, double sl, double tpRequested, double riskMoney)
{
   if(!SymbolSelect(symbol, true)) { Ack(id, "REJECTED", "Symbol unavailable"); return false; }

   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   if(bid <= 0 || ask <= 0) { Ack(id, "REJECTED", "No live quote"); return false; }

   string normOrderType = "";
   string typeErr = "";
   if(!ResolveOrderType(rawOrderType, direction, normOrderType, typeErr))
   { Ack(id, "REJECTED", typeErr); return false; }

   bool isLimit = (normOrderType == "BUY_LIMIT" || normOrderType == "SELL_LIMIT");
   if(isLimit)
   {
      if(normOrderType == "BUY_LIMIT" && entry >= ask)
      { Ack(id, "REJECTED", "BUY_LIMIT requires entry < current Ask"); return false; }
      if(normOrderType == "SELL_LIMIT" && entry <= bid)
      { Ack(id, "REJECTED", "SELL_LIMIT requires entry > current Bid"); return false; }
   }

   double slDistance = MathAbs(entry - sl);
   if(slDistance <= 0) { Ack(id, "REJECTED", "SL distance zero"); return false; }

   // Validate SL geometry
   if((direction == "BUY" && sl >= entry) || (direction == "SELL" && sl <= entry))
   { Ack(id, "REJECTED", "Invalid SL geometry"); return false; }

   // Tự tính lot từ risk
   double volume = CalculateLotWithCommission(symbol, slDistance, riskMoney);
   if(volume <= 0)
   {
      Ack(id, "REJECTED", StringFormat(
         "Cannot compute valid lot for risk $%.2f, SL dist %.5f (below min volume or risk too tight)",
         riskMoney, slDistance));
      return false;
   }

   // Verify total risk bao gồm commission + spread
   double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickValue <= 0) tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);

   double slRisk = (slDistance / tickSize) * tickValue * volume;
   double commission = GetCommissionPerLot(symbol) * volume;
   double spreadPoints = (ask - bid) / point + g_settings.spread_buffer_points;
   double spreadCost = (spreadPoints * point / tickSize) * tickValue * volume;
   double totalRisk = slRisk + commission + spreadCost;
   double budget = riskMoney * (1 + g_settings.risk_tolerance_pct / 100.0);

   if(totalRisk > budget)
   {
      Ack(id, "REJECTED", StringFormat(
         "Total risk $%.2f (SL $%.2f + Comm $%.2f + Spread $%.2f) > budget $%.2f",
         totalRisk, slRisk, commission, spreadCost, budget));
      return false;
   }

   // TP: dùng signal nếu có, không thì tính từ RR
   double tp = tpRequested > 0
      ? tpRequested
      : ((direction == "BUY")
         ? entry + slDistance * g_settings.reward_risk_ratio
         : entry - slDistance * g_settings.reward_risk_ratio);

   if((direction == "BUY" && tp <= entry) || (direction == "SELL" && tp >= entry))
   { Ack(id, "REJECTED", "Invalid TP geometry"); return false; }

   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   entry = NormalizeDouble(entry, digits);
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFilling(PickFilling(symbol));
   trade.SetAsyncMode(false);

   bool ok = false;
   if(normOrderType == "BUY_LIMIT")
      ok = trade.BuyLimit(volume, entry, symbol, sl, tp, ORDER_TIME_GTC, 0, OrderComment);
   else if(normOrderType == "SELL_LIMIT")
      ok = trade.SellLimit(volume, entry, symbol, sl, tp, ORDER_TIME_GTC, 0, OrderComment);
   else
      ok = (direction == "BUY")
         ? trade.Buy(volume, symbol, 0.0, sl, tp, OrderComment)
         : trade.Sell(volume, symbol, 0.0, sl, tp, OrderComment);

   if(!ok || trade.ResultRetcode() != TRADE_RETCODE_DONE)
   {
      string err = StringFormat("OrderSend failed retcode=%d (%s)",
         trade.ResultRetcode(), trade.ResultRetcodeDescription());
      Ack(id, "REJECTED", err);
      return false;
   }

   ulong posTicket = trade.ResultOrder();
   if(PositionSelectByTicket(posTicket))
      posTicket = (ulong)PositionGetInteger(POSITION_TICKET);

   Print("[V9 OPEN] signal=", id, " ", symbol, " ", tf, " ", normOrderType, " ", direction,
         " lots=", DoubleToString(volume, 4),
         " entry=", DoubleToString(entry, digits),
         " SL=", DoubleToString(sl, digits),
         " TP=", DoubleToString(tp, digits),
         " risk=$", DoubleToString(totalRisk, 2),
         " ticket=", posTicket);

   Ack(id, "PLACED", StringFormat("Order placed lot=%.4f ticket=%I64u risk=$%.2f",
      volume, posTicket, totalRisk));
   return true;
}

//--- Close / Cancel -------------------------------------------------
void AckCloseCommand(string requestId, string targetId, bool ok, string detail, string kind)
{
   string body = StringFormat(
      "{\"request_id\":\"%s\",\"position_id\":\"%s\",\"ok\":%s,\"message\":\"%s\",\"kind\":\"%s\"}",
      JsonEscape(requestId), JsonEscape(targetId), (ok?"true":"false"), JsonEscape(detail), JsonEscape(kind));
   string response; int status;
   Request("POST", "/api/mt5/close-requests/ack", body, response, status);
}

bool CloseRealPositionById(string targetId, string symbol, string &detailOut, bool preferPending)
{
   ulong targetTicket = 0;

   if(!preferPending)
   {
      if(targetId != "")
      {
         for(int i = PositionsTotal()-1; i >= 0; i--)
         {
            if(!posInfo.SelectByIndex(i)) continue;
            if(posInfo.Magic() != MagicNumber) continue;
            string tk = IntegerToString((long)posInfo.Ticket());
            if(tk == targetId) { targetTicket = posInfo.Ticket(); break; }
         }
      }
      if(targetTicket == 0 && symbol != "")
      {
         for(int i = PositionsTotal()-1; i >= 0; i--)
         {
            if(!posInfo.SelectByIndex(i)) continue;
            if(posInfo.Magic() != MagicNumber) continue;
            if(posInfo.Symbol() == symbol) { targetTicket = posInfo.Ticket(); break; }
         }
      }
   }

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetAsyncMode(false);

   if(targetTicket != 0)
   {
      if(!PositionSelectByTicket(targetTicket)) { detailOut = "Position disappeared"; return false; }
      trade.SetTypeFilling(PickFilling(PositionGetString(POSITION_SYMBOL)));
      if(!trade.PositionClose(targetTicket))
      {
         detailOut = StringFormat("PositionClose failed retcode=%d", trade.ResultRetcode());
         return false;
      }
      detailOut = StringFormat("Closed ticket=%I64u", targetTicket);
      return true;
   }

   for(int i = OrdersTotal()-1; i >= 0; i--)
   {
      ulong ordTicket = OrderGetTicket(i);
      if(ordTicket == 0) continue;
      long ordMagic = OrderGetInteger(ORDER_MAGIC);
      if(ordMagic != MagicNumber && !AllowCancelExternalPending) continue;

      ENUM_ORDER_TYPE ot = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      if(ot != ORDER_TYPE_BUY_LIMIT && ot != ORDER_TYPE_SELL_LIMIT &&
         ot != ORDER_TYPE_BUY_STOP && ot != ORDER_TYPE_SELL_STOP) continue;

      string ordSym = OrderGetString(ORDER_SYMBOL);
      string tkStr = IntegerToString((long)ordTicket);
      bool matchId = (targetId != "" && tkStr == targetId);
      bool matchSym = (symbol != "" && ordSym == symbol);
      if(matchId || matchSym)
      {
         if(trade.OrderDelete(ordTicket))
         {
            detailOut = StringFormat("Deleted pending ticket=%I64u", ordTicket);
            return true;
         }
         detailOut = StringFormat("OrderDelete failed retcode=%d", trade.ResultRetcode());
         return false;
      }
   }

   detailOut = "Position/pending not found";
   return false;
}

void ExecuteCloseCommand(string cmdJson)
{
   string reqId = JsonValue(cmdJson, "request_id");
   string targetId = JsonValue(cmdJson, "position_id");
   if(targetId == "") targetId = JsonValue(cmdJson, "ticket");
   string symbol = UpperTrim(JsonValue(cmdJson, "symbol"));
   string isPendingStr = JsonValue(cmdJson, "is_pending");
   bool isPendingReq = (isPendingStr == "1" || isPendingStr == "true");
   string kindStr = JsonValue(cmdJson, "kind");
   if(kindStr == "") kindStr = (isPendingReq ? "cancel" : "close");
   if(reqId == "") reqId = targetId;
   if(targetId == "" && symbol == "") return;
   if(HasProcessedSltp("close:" + reqId)) return;

   bool preferPending = (kindStr == "cancel" || isPendingReq);
   string detail = "";
   bool closed = CloseRealPositionById(targetId, symbol, detail, preferPending);

   MarkProcessedSltp("close:" + reqId);
   AckCloseCommand(reqId, targetId, closed, detail, kindStr);
   if(closed)
   {
      SendAccountSnapshot();
      if(SyncRealHistory) PublishRealClosedDeals();
   }
}

void PollCloseCommands()
{
   string response; int status;
   if(!Request("GET", "/api/mt5/close-requests", "", response, status)) return;
   string arr = JsonArrayValue(response, "close_requests");
   if(arr == "") return;
   string items[];
   ExtractTopLevelObjects(arr, items);
   for(int i = 0; i < ArraySize(items); i++)
      ExecuteCloseCommand(items[i]);
}

//--- SL/TP modify ---------------------------------------------------
void AckSltpCommand(string requestId, string ticket, bool ok, string detail)
{
   string body = StringFormat(
      "{\"request_id\":\"%s\",\"ticket\":\"%s\",\"ok\":%s,\"message\":\"%s\"}",
      JsonEscape(requestId), JsonEscape(ticket), (ok?"true":"false"), JsonEscape(detail));
   string response; int status;
   Request("POST", "/api/mt5/sltp-requests/ack", body, response, status);
}

void ExecuteSltpCommand(string cmdJson)
{
   string reqId = JsonValue(cmdJson, "request_id");
   string ticket = JsonValue(cmdJson, "ticket");
   if(reqId == "" || ticket == "") return;
   if(HasProcessedSltp("sltp:" + reqId)) return;

   double newSl = StringToDouble(JsonValue(cmdJson, "new_sl"));
   double newTp = StringToDouble(JsonValue(cmdJson, "new_tp"));
   ulong tk = (ulong)StringToInteger(ticket);
   bool ok = false;
   string detail = "";

   if(!PositionSelectByTicket(tk))
      detail = "Position not found";
   else
   {
      string sym = PositionGetString(POSITION_SYMBOL);
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      double sl = NormalizeDouble(newSl, digits);
      double tp = NormalizeDouble(newTp, digits);
      trade.SetExpertMagicNumber(MagicNumber);
      trade.SetDeviationInPoints(SlippagePoints);
      trade.SetTypeFilling(PickFilling(sym));
      trade.SetAsyncMode(false);
      if(trade.PositionModify(tk, sl, tp))
      { ok = true; detail = "SL/TP updated"; }
      else
         detail = StringFormat("PositionModify failed retcode=%d", trade.ResultRetcode());
   }

   MarkProcessedSltp("sltp:" + reqId);
   AckSltpCommand(reqId, ticket, ok, detail);
   if(ok) SendAccountSnapshot();
}

void PollSltpRequests()
{
   string response; int status;
   if(!Request("GET", "/api/mt5/sltp-requests", "", response, status)) return;
   string arr = JsonArrayValue(response, "sltp_requests");
   if(arr == "") return;
   string items[];
   ExtractTopLevelObjects(arr, items);
   for(int i = 0; i < ArraySize(items); i++)
      ExecuteSltpCommand(items[i]);
}

//--- Pending modify -------------------------------------------------
void AckPendingModify(string requestId, string ticket, bool ok, string detail)
{
   string body = StringFormat(
      "{\"request_id\":\"%s\",\"ticket\":\"%s\",\"ok\":%s,\"message\":\"%s\"}",
      JsonEscape(requestId), JsonEscape(ticket), (ok?"true":"false"), JsonEscape(detail));
   string response; int status;
   Request("POST", "/api/mt5/pending-modify-requests/ack", body, response, status);
}

void ExecutePendingModify(string cmdJson)
{
   string reqId = JsonValue(cmdJson, "request_id");
   string ticket = JsonValue(cmdJson, "ticket");
   if(reqId == "" || ticket == "") return;
   if(HasProcessedPendingModify(reqId)) return;

   double newEntry = StringToDouble(JsonValue(cmdJson, "new_entry"));
   double newSl = StringToDouble(JsonValue(cmdJson, "new_sl"));
   double newTp = StringToDouble(JsonValue(cmdJson, "new_tp"));
   ulong tk = (ulong)StringToInteger(ticket);
   bool ok = false;
   string detail = "";

   if(!OrderSelect(tk))
      detail = "Pending order not found";
   else
   {
      string sym = OrderGetString(ORDER_SYMBOL);
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      double curPrice = OrderGetDouble(ORDER_PRICE_OPEN);
      double curSl = OrderGetDouble(ORDER_SL);
      double curTp = OrderGetDouble(ORDER_TP);
      double price = NormalizeDouble(newEntry > 0 ? newEntry : curPrice, digits);
      double sl = NormalizeDouble(newSl > 0 ? newSl : curSl, digits);
      double tp = NormalizeDouble(newTp > 0 ? newTp : curTp, digits);
      datetime expiration = (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);

      trade.SetExpertMagicNumber(MagicNumber);
      trade.SetDeviationInPoints(SlippagePoints);
      trade.SetTypeFilling(PickFilling(sym));
      trade.SetAsyncMode(false);
      if(trade.OrderModify(tk, price, sl, tp, ORDER_TIME_GTC, expiration, 0.0))
      { ok = true; detail = "Pending modified"; }
      else
         detail = StringFormat("OrderModify failed retcode=%d", trade.ResultRetcode());
   }

   MarkProcessedPendingModify(reqId);
   AckPendingModify(reqId, ticket, ok, detail);
   if(ok) SendAccountSnapshot();
}

void PollPendingModifyRequests()
{
   string response; int status;
   if(!Request("GET", "/api/mt5/pending-modify-requests", "", response, status)) return;
   string arr = JsonArrayValue(response, "pending_modify_requests");
   if(arr == "") return;
   string items[];
   ExtractTopLevelObjects(arr, items);
   for(int i = 0; i < ArraySize(items); i++)
      ExecutePendingModify(items[i]);
}

//--- Poll signal ----------------------------------------------------
void PollServer()
{
   // Nếu emergency ON hoặc auto_trade OFF, vẫn poll để ack reject (tránh kẹt queue)
   string path = "/api/mt5/next?consumer=" + ConsumerId;
   string response; int status;
   if(!Request("GET", path, "", response, status)) return;
   if(StringFind(response, "\"signal\":null") >= 0) return;

   string obj = JsonObjectValue(response, "signal");
   if(obj == "") return;

   string id = JsonValue(obj, "id");
   string symbol = JsonValue(obj, "symbol");
   string tf = JsonValue(obj, "timeframe");
   string direction = JsonValue(obj, "direction");
   string orderType = JsonValue(obj, "order_type");
   double entry = StringToDouble(JsonValue(obj, "entry"));
   double sl = StringToDouble(JsonValue(obj, "sl"));
   double tp = StringToDouble(JsonValue(obj, "tp"));
   double riskMoney = StringToDouble(JsonValue(obj, "risk_money"));

   if(id == "") return;
   if(HasProcessedSignal(id)) return;

   if(symbol == "" || entry <= 0 || sl <= 0 ||
      (direction != "BUY" && direction != "SELL"))
   {
      MarkProcessedSignal(id);
      Ack(id, "REJECTED", "Malformed signal");
      return;
   }

   // Check emergency
   if(g_settings.emergency_stop)
   {
      MarkProcessedSignal(id);
      Ack(id, "REJECTED", "Emergency Stop ON");
      return;
   }

   // Check auto trade
   if(!g_settings.auto_trade_enabled)
   {
      MarkProcessedSignal(id);
      Ack(id, "REJECTED", "AutoTrade OFF (dashboard toggle)");
      return;
   }

   // Check time filter
   if(!IsInTradingHours())
   {
      MarkProcessedSignal(id);
      Ack(id, "REJECTED", StringFormat("Outside trading hours (%d-%d GMT+7)",
         g_settings.trading_start_hour, g_settings.trading_end_hour));
      return;
   }

   // Check news filter
   if(IsNewsBlocked())
   {
      MarkProcessedSignal(id);
      Ack(id, "REJECTED", "News block active");
      return;
   }

   // Check max positions
   int openCount = CountRealExposure();
   if(openCount >= g_settings.max_positions)
   {
      MarkProcessedSignal(id);
      Ack(id, "REJECTED", StringFormat("Max positions reached (%d/%d)", openCount, g_settings.max_positions));
      return;
   }

   // Determine risk_money: ưu tiên settings dashboard, fallback signal
   double useRisk = g_settings.risk_money > 0 ? g_settings.risk_money : riskMoney;
   if(useRisk <= 0) useRisk = 25.0;

   MarkProcessedSignal(id);
   OpenTrade(id, symbol, tf, direction, orderType, entry, sl, tp, useRisk);
}

//--- Account snapshot ----------------------------------------------
void SendAccountSnapshot()
{
   string itemsJson = "";
   bool first = true;

   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      string symbol = PositionGetString(POSITION_SYMBOL);
      long magic = PositionGetInteger(POSITION_MAGIC);
      string comment = PositionGetString(POSITION_COMMENT);
      string source = (magic == MagicNumber || comment == OrderComment) ? "TRADE_V9" : "MT5_EXTERNAL";
      long type = PositionGetInteger(POSITION_TYPE);
      string direction = (type == POSITION_TYPE_BUY ? "BUY" : "SELL");
      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);
      double volume = PositionGetDouble(POSITION_VOLUME);
      double profit = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      datetime opened = (datetime)PositionGetInteger(POSITION_TIME);

      if(!first) itemsJson += ",";
      first = false;
      itemsJson += StringFormat(
         "{\"id\":\"P%I64u\",\"ticket\":\"%I64u\",\"symbol\":\"%s\",\"timeframe\":\"MT5\","
         "\"direction\":\"%s\",\"entry\":%.10f,\"sl\":%.10f,\"tp\":%.10f,"
         "\"volume\":%.8f,\"profit\":%.2f,\"opened_at\":%I64d,\"is_pending\":false,"
         "\"order_type\":\"MARKET\",\"magic\":%I64d,\"comment\":\"%s\",\"source\":\"%s\"}",
         ticket, ticket, JsonEscape(symbol), direction, entry, sl, tp, volume,
         NormalizeDouble(profit, 2), (long)opened, magic, JsonEscape(comment), source);
   }

   for(int i = OrdersTotal()-1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      ENUM_ORDER_TYPE type = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      string orderType = "";
      string direction = "";
      if(type == ORDER_TYPE_BUY_LIMIT) { orderType = "BUY_LIMIT"; direction = "BUY"; }
      else if(type == ORDER_TYPE_SELL_LIMIT) { orderType = "SELL_LIMIT"; direction = "SELL"; }
      else if(type == ORDER_TYPE_BUY_STOP) { orderType = "BUY_STOP"; direction = "BUY"; }
      else if(type == ORDER_TYPE_SELL_STOP) { orderType = "SELL_STOP"; direction = "SELL"; }
      else continue;

      string symbol = OrderGetString(ORDER_SYMBOL);
      long magic = OrderGetInteger(ORDER_MAGIC);
      string comment = OrderGetString(ORDER_COMMENT);
      string source = (magic == MagicNumber || comment == OrderComment) ? "TRADE_V9" : "MT5_EXTERNAL";
      double entry = OrderGetDouble(ORDER_PRICE_OPEN);
      double sl = OrderGetDouble(ORDER_SL);
      double tp = OrderGetDouble(ORDER_TP);
      double volume = OrderGetDouble(ORDER_VOLUME_CURRENT);
      datetime opened = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
      if(entry <= 0 || volume <= 0) continue;

      if(!first) itemsJson += ",";
      first = false;
      itemsJson += StringFormat(
         "{\"id\":\"O%I64u\",\"ticket\":\"%I64u\",\"symbol\":\"%s\",\"timeframe\":\"MT5\","
         "\"direction\":\"%s\",\"entry\":%.10f,\"sl\":%.10f,\"tp\":%.10f,"
         "\"volume\":%.8f,\"profit\":0,\"opened_at\":%I64d,\"is_pending\":true,"
         "\"order_type\":\"%s\",\"magic\":%I64d,\"comment\":\"%s\",\"source\":\"%s\"}",
         ticket, ticket, JsonEscape(symbol), direction, entry, sl, tp, volume,
         (long)opened, orderType, magic, JsonEscape(comment), source);
   }

   string body = "{\"items\":[" + itemsJson + "]}";
   string response; int status;
   if(!Request("POST", "/api/mt5/account-snapshot", body, response, status))
      Print("[V9] Account snapshot sync failed HTTP=", status);
}

void PublishRealClosedDeals()
{
   if(!SyncRealHistory) return;
   datetime now = TimeCurrent();
   datetime fromTime = now - (datetime)(7 * 86400);
   if(!HistorySelect(fromTime, now)) return;
   int totalDeals = HistoryDealsTotal();
   if(totalDeals <= 0) return;

   string tradesJson = "";
   int count = 0;
   for(int i = totalDeals - 1; i >= 0 && count < 200; i--)
   {
      ulong dealTicket = HistoryDealGetTicket(i);
      if(dealTicket == 0) continue;
      if(HistoryDealGetInteger(dealTicket, DEAL_MAGIC) != MagicNumber) continue;
      long entryType = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
      if(entryType != DEAL_ENTRY_OUT && entryType != DEAL_ENTRY_INOUT) continue;
      string sym = HistoryDealGetString(dealTicket, DEAL_SYMBOL);
      if(sym == "") continue;
      long dealType = HistoryDealGetInteger(dealTicket, DEAL_TYPE);
      string dir = (dealType == DEAL_TYPE_BUY) ? "SELL" : "BUY";
      double vol = HistoryDealGetDouble(dealTicket, DEAL_VOLUME);
      double price = HistoryDealGetDouble(dealTicket, DEAL_PRICE);
      double profit = HistoryDealGetDouble(dealTicket, DEAL_PROFIT)
                  + HistoryDealGetDouble(dealTicket, DEAL_SWAP)
                  + HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
      long closedAt = HistoryDealGetInteger(dealTicket, DEAL_TIME);
      if(vol <= 0 || closedAt <= 0) continue;

      if(tradesJson != "") tradesJson += ",";
      tradesJson += StringFormat(
         "{\"id\":\"v9-%I64u\",\"ticket\":\"%I64u\",\"symbol\":\"%s\",\"timeframe\":\"MT5\","
         "\"direction\":\"%s\",\"entry\":%.8f,\"sl\":0,\"tp\":0,\"volume\":%.4f,"
         "\"profit\":%.2f,\"outcome\":\"CLOSED\",\"opened_at\":0,\"closed_at\":%I64d}",
         dealTicket, dealTicket, JsonEscape(sym), dir, price, vol,
         NormalizeDouble(profit, 2), closedAt);
      count++;
   }

   if(count <= 0 || tradesJson == lastHistoryDigest) return;
   string body = "{\"replace\":false,\"trades\":[" + tradesJson + "]}";
   string response; int status;
   if(Request("POST", "/api/mt5/history", body, response, status))
      lastHistoryDigest = tradesJson;
}

//--- Lifecycle ------------------------------------------------------
int OnInit()
{
   if(StringFind(ServerBaseURL, "https://") != 0 && StringFind(ServerBaseURL, "http://127.0.0.1") != 0)
   { Print("[V9] HTTPS or localhost server URL required."); return INIT_PARAMETERS_INCORRECT; }
   if(ApiKey == "" || ApiKey == "SET_A_LONG_RANDOM_API_KEY")
   { Print("[V9] Set ApiKey privately."); return INIT_PARAMETERS_INCORRECT; }

   trade.SetExpertMagicNumber(MagicNumber);
   InitDefaultSettings();
   CalculateServerGMTOffset();

   // Initial poll
   PollSettings();
   PollCommissions();

   EventSetTimer(1);
   Print("[V9] Started v3.10 REAL HUB. magic=", MagicNumber,
         " | AutoTrade=", g_settings.auto_trade_enabled ? "ON" : "OFF",
         " | Trailing=", g_settings.trailing_enabled ? "ON" : "OFF",
         " | Risk=$", DoubleToString(g_settings.risk_money, 2));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) { EventKillTimer(); }
void OnTick() {}

void OnTimer()
{
   uint nowMs = GetTickCount();

   // Recalc GMT offset mỗi giờ
   if(TimeCurrent() - g_lastOffsetCalcTime > 3600)
      CalculateServerGMTOffset();

   // Settings poll
   if(lastSettingsPollMs == 0 ||
      (uint)(nowMs - lastSettingsPollMs) >= (uint)(SettingsPollSeconds * 1000))
   {
      lastSettingsPollMs = nowMs;
      PollSettings();
   }

   // Emergency check
   CheckEmergencyStop();

   // Commission poll
   if(lastCommissionPollMs == 0 ||
      (uint)(nowMs - lastCommissionPollMs) >= (uint)(CommissionPollSeconds * 1000))
   {
      lastCommissionPollMs = nowMs;
      PollCommissions();
   }

   // Heartbeat
   if(lastHeartbeatMs == 0 ||
      (uint)(nowMs - lastHeartbeatMs) >= (uint)(HeartbeatSeconds * 1000))
      SendHeartbeat();

   // Trailing check
   if(lastTrailingCheckMs == 0 ||
      (uint)(nowMs - lastTrailingCheckMs) >= (uint)(TrailingCheckSeconds * 1000))
   {
      lastTrailingCheckMs = nowMs;
      ManageTrailing();
   }

   // Poll signal + close
   if(lastPollMs == 0 ||
      (uint)(nowMs - lastPollMs) >= (uint)(PollSeconds * 1000))
   {
      lastPollMs = nowMs;
      PollCloseCommands();
      PollServer();
   }

   // SLTP poll
   if(lastSltpPollMs == 0 ||
      (uint)(nowMs - lastSltpPollMs) >= (uint)(PollSeconds * 1000))
   {
      lastSltpPollMs = nowMs;
      PollSltpRequests();
   }

   // Pending modify poll
   if(lastPendingModifyPollMs == 0 ||
      (uint)(nowMs - lastPendingModifyPollMs) >= (uint)(PollSeconds * 1000))
   {
      lastPendingModifyPollMs = nowMs;
      PollPendingModifyRequests();
   }

   // Account snapshot
   if(lastPositionsSyncMs == 0 ||
      (uint)(nowMs - lastPositionsSyncMs) >= 3000)
   {
      lastPositionsSyncMs = nowMs;
      SendAccountSnapshot();
   }

   // History sync
   if(SyncRealHistory &&
      (lastHistorySyncMs == 0 || (uint)(nowMs - lastHistorySyncMs) >= 15000))
   {
      lastHistorySyncMs = nowMs;
      PublishRealClosedDeals();
   }
}
