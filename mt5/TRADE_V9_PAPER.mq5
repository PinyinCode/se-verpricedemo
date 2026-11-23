//+------------------------------------------------------------------+
//| TRADE_V9_PAPER.mq5                                               |
//| Đọc tín hiệu Indicator qua GV_IND_<SYM>_* + Lệnh tay /api/mt5/next|
//| Server cung cấp & đồng bộ thông số (/api/settings).              |
//| Tự tính SL/TP/Lot, chặn tin ±N phút, tự trailing/BE, cache local.|
//+------------------------------------------------------------------+
#property strict
#property version "3.19"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

#define SETTINGS_CACHE_FILE "trade_v9_settings_cache.json"

//--- Connection & Identity Inputs -----------------------------------
input string ServerBaseURL                = "https://investforex.onrender.com";
input string ApiKey                       = "SET_A_LONG_RANDOM_API_KEY";
input string ConsumerId                   = "trade-v9";
input long   MagicNumber                  = 20240915;
input string OrderComment                 = "TRADE-V9";
input string IndicatorSymbolsCsv          = ""; // Để trống = quét chart hiện tại + Market Watch; hoặc nhập "XAUUSD,EURUSD"

//--- Globals --------------------------------------------------------
CTrade        trade;
CPositionInfo posInfo;

struct SNewsEventItem {
   string title;
   string currency;
   long   event_ts;
};

struct SSettings {
   bool   auto_trade_enabled;
   bool   trailing_enabled;
   bool   breakeven_enabled;
   bool   news_filter_enabled;
   int    news_block_before_min;
   int    news_block_after_min;
   string news_events_json;
   bool   time_filter_enabled;
   bool   telegram_notifications_enabled;
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

   int    poll_seconds;
   int    settings_poll_seconds;
   int    commission_poll_seconds;
   int    heartbeat_seconds;
   int    trailing_check_seconds;
   int    slippage_points;
   bool   sync_real_history;
   bool   allow_cancel_external_pending;
   bool   allow_modify_external_pending;
   bool   allow_min_lot_when_exceed_budget;
   bool   reject_when_spread_exceeds_buf;
   bool   commission_is_round_turn;

   datetime last_synced;
};
SSettings g_settings;
SNewsEventItem g_newsEvents[];
string g_lastSavedSettingsJson = "";

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
string g_lastCommissionDigest = "";

struct SRiskBreakdown {
   bool   valid;
   double slRisk;
   double commission;
   double spreadCost;
   double total;
   double riskPerLot;
   string error;
};

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

string   processedSignalKeys[];
string   processedSltpRequestIds[];
string   processedPendingModifyIds[];
string   lastHistoryDigest = "";

uint lastIndicatorPollMs     = 0;
uint lastSettingsPollMs      = 0;
uint lastCommissionPollMs    = 0;
uint lastPositionsSyncMs     = 0;
uint lastHeartbeatMs         = 0;
uint lastHistorySyncMs       = 0;
uint lastSltpPollMs          = 0;
uint lastPendingModifyPollMs = 0;
uint lastTrailingCheckMs     = 0;

int g_serverToGMTOffset = 0;
datetime g_lastOffsetCalcTime = 0;
bool g_emergencyClosedOnce = false;

//--- Forward declarations -------------------------------------------
void SendAccountSnapshot();
void PublishRealClosedDeals();
int  CountRealExposure();
void PollSettings();
void SyncDashboardGlobalVariables();
void PollCommissions();
void ManageTrailing();
void CheckEmergencyStop();
SRiskBreakdown ComputeRiskBreakdown(string symbol, double slDistance, double volume);
ushort json_safe_idx(string s, int idx);
string JsonValue(string json, string key);
string JsonObjectValue(string json, string key);
string JsonArrayValue(string json, string key);
bool JsonBoolValue(string json, string key, bool defaultValue);
int JsonIntValue(string json, string key, int defaultValue);
double JsonDoubleValue(string json, string key, double defaultValue);
void ExtractTopLevelObjects(string arrJson, string &outObjects[]);

//--- Time & News helpers --------------------------------------------
void CalculateServerGMTOffset()
{
   datetime st = TimeTradeServer(), gt = TimeGMT();
   if(st <= 0 || gt <= 0) return;
   g_serverToGMTOffset = (int)(st - gt);
   int h = (int)MathRound((double)g_serverToGMTOffset / 3600.0);
   g_serverToGMTOffset = h * 3600;
   g_lastOffsetCalcTime = TimeCurrent();
}

datetime GetCurrentUtcTimestamp()
{
   datetime gt = TimeGMT();
   if(gt > 0) return gt;
   datetime st = TimeTradeServer();
   if(st <= 0) st = TimeCurrent();
   return st - g_serverToGMTOffset;
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

int ParseTzOffsetSeconds(string s, int startPos)
{
   int sign = 1;
   int p = startPos;
   if(p < StringLen(s) && StringGetCharacter(s, p) == '+') { sign = 1; p++; }
   else if(p < StringLen(s) && StringGetCharacter(s, p) == '-') { sign = -1; p++; }
   string rem = StringSubstr(s, p);
   StringReplace(rem, ":", "");
   int hh = 0, mm = 0;
   if(StringLen(rem) >= 2) hh = (int)StringToInteger(StringSubstr(rem, 0, 2));
   if(StringLen(rem) >= 4) mm = (int)StringToInteger(StringSubstr(rem, 2, 2));
   return sign * (hh * 3600 + mm * 60);
}

long ParseIsoOrEpochToUtc(string rawTime)
{
   string s = rawTime;
   StringTrimLeft(s);
   StringTrimRight(s);
   if(s == "") return 0;

   bool allDigits = true;
   for(int i = 0; i < StringLen(s); i++)
   {
      ushort ch = StringGetCharacter(s, i);
      if(ch < '0' || ch > '9') { allDigits = false; break; }
   }
   if(allDigits)
   {
      long val = StringToInteger(s);
      if(val > 100000000000LL) val /= 1000;
      return val;
   }

   int tzOffsetSec = 7 * 3600;
   string dtPart = s;
   int len = StringLen(s);
   if(StringGetCharacter(s, len - 1) == 'Z' || StringGetCharacter(s, len - 1) == 'z')
   {
      tzOffsetSec = 0;
      dtPart = StringSubstr(s, 0, len - 1);
   }
   else if(len >= 6)
   {
      int plusPos = StringFind(s, "+", 10);
      int minusPos = StringFind(s, "-", 10);
      int tzPos = (plusPos >= 0) ? plusPos : minusPos;
      if(tzPos >= 10)
      {
         tzOffsetSec = ParseTzOffsetSeconds(s, tzPos);
         dtPart = StringSubstr(s, 0, tzPos);
      }
   }

   StringReplace(dtPart, "T", " ");
   datetime localDt = StringToTime(dtPart);
   if(localDt <= 0) return 0;
   return (long)localDt - tzOffsetSec;
}

void RebuildNewsScheduleCache()
{
   ArrayResize(g_newsEvents, 0);
   string trimmed = g_settings.news_events_json;
   StringTrimLeft(trimmed);
   StringTrimRight(trimmed);
   if(trimmed == "" || trimmed == "[]") return;

   string objs[];
   ExtractTopLevelObjects(trimmed, objs);
   for(int i = 0; i < ArraySize(objs); i++)
   {
      string tStr = JsonValue(objs[i], "time");
      if(tStr == "") tStr = JsonValue(objs[i], "timestamp");
      if(tStr == "") tStr = JsonValue(objs[i], "date");
      long evTs = ParseIsoOrEpochToUtc(tStr);
      if(evTs <= 0) continue;

      SNewsEventItem item;
      item.title    = JsonValue(objs[i], "title");
      if(item.title == "") item.title = JsonValue(objs[i], "name");
      if(item.title == "") item.title = JsonValue(objs[i], "event");
      if(item.title == "") item.title = "News Event";
      item.currency = JsonValue(objs[i], "currency");
      item.event_ts = evTs;

      int n = ArraySize(g_newsEvents);
      ArrayResize(g_newsEvents, n + 1);
      g_newsEvents[n] = item;
   }
}

bool IsWithinConfiguredNewsWindow()
{
   long nowUtc = (long)GetCurrentUtcTimestamp();
   long beforeSec = (long)MathMax(0, g_settings.news_block_before_min) * 60;
   long afterSec  = (long)MathMax(0, g_settings.news_block_after_min) * 60;

   for(int i = 0; i < ArraySize(g_newsEvents); i++)
   {
      long evTs = g_newsEvents[i].event_ts;
      if(nowUtc >= (evTs - beforeSec) && nowUtc <= (evTs + afterSec))
         return true;
   }
   return false;
}

bool IsNewsBlocked()
{
   if(!g_settings.news_filter_enabled) return false;
   if(IsWithinConfiguredNewsWindow()) return true;
   if(GlobalVariableCheck("GV_NewsFilter_Blocked") && GlobalVariableGet("GV_NewsFilter_Blocked") >= 0.5)
      return true;
   return false;
}

//--- JSON & string helpers ------------------------------------------
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

ushort json_safe_idx(string s, int idx)
{
   if(idx < 0 || idx >= StringLen(s)) return 0;
   return StringGetCharacter(s, idx);
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
            if(nx == '"' || nx == '\\' || nx == '/') { out += ShortToString(nx); i++; continue; }
            if(nx == 'n') { out += "\n"; i++; continue; }
            if(nx == 'r') { out += "\r"; i++; continue; }
            if(nx == 't') { out += "\t"; i++; continue; }
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

int JsonIntValue(string json, string key, int defaultValue)
{
   string v = JsonValue(json, key);
   if(v == "") return defaultValue;
   return (int)StringToInteger(v);
}

double JsonDoubleValue(string json, string key, double defaultValue)
{
   string v = JsonValue(json, key);
   if(v == "") return defaultValue;
   return StringToDouble(v);
}

void ExtractTopLevelObjects(string arrJson, string &outObjects[])
{
   ArrayResize(outObjects, 0);
   int depth = 0; bool quoted = false; int start = -1;
   for(int i = 0; i < StringLen(arrJson); i++)
   {
      ushort c = StringGetCharacter(arrJson, i);
      if(c == '"' && (i == 0 || json_safe_idx(arrJson, i-1) != '\\')) quoted = !quoted;
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
void PushDedupId(string &arr[], string id, int maxItems = 1000)
{
   if(id == "") return;
   for(int i = 0; i < ArraySize(arr); i++)
      if(arr[i] == id) return;
   int n = ArraySize(arr);
   if(n >= maxItems)
   {
      for(int i = 0; i < n-1; i++) arr[i] = arr[i+1];
      arr[n-1] = id;
      return;
   }
   ArrayResize(arr, n+1);
   arr[n] = id;
}

bool HasProcessedSignalKey(string key)
{
   for(int i = 0; i < ArraySize(processedSignalKeys); i++)
      if(processedSignalKeys[i] == key) return true;
   return false;
}
void MarkProcessedSignalKey(string key) { PushDedupId(processedSignalKeys, key); }

bool HasProcessedSltp(string id)
{
   for(int i = 0; i < ArraySize(processedSltpRequestIds); i++)
      if(processedSltpRequestIds[i] == id) return true;
   return false;
}
void MarkProcessedSltp(string id) { PushDedupId(processedSltpRequestIds, id); }

bool HasProcessedPendingModify(string id)
{
   for(int i = 0; i < ArraySize(processedPendingModifyIds); i++)
      if(processedPendingModifyIds[i] == id) return true;
   return false;
}
void MarkProcessedPendingModify(string id) { PushDedupId(processedPendingModifyIds, id); }

//--- Settings cache & polling ---------------------------------------
void InitDefaultSettings()
{
   g_settings.auto_trade_enabled             = true;
   g_settings.trailing_enabled               = true;
   g_settings.breakeven_enabled              = true;
   g_settings.news_filter_enabled            = true;
   g_settings.news_block_before_min          = 3;
   g_settings.news_block_after_min           = 3;
   g_settings.news_events_json               = "[]";
   g_settings.time_filter_enabled            = true;
   g_settings.telegram_notifications_enabled = true;
   g_settings.emergency_stop                 = false;
   g_settings.emergency_close_all            = true;
   g_settings.risk_money                     = 25.0;
   g_settings.max_positions                  = 3;
   g_settings.reward_risk_ratio              = 2.0;
   g_settings.risk_tolerance_pct             = 0.5;
   g_settings.commission_fallback            = 0.0;
   g_settings.spread_buffer_points           = 20;
   g_settings.trailing_trigger_rr            = 1.0;
   g_settings.trailing_lock_rr               = 0.5;
   g_settings.breakeven_trigger_rr           = 0.5;
   g_settings.trailing_mode                  = "static";
   g_settings.trading_start_hour             = 7;
   g_settings.trading_end_hour               = 23;

   g_settings.poll_seconds                   = 1;
   g_settings.settings_poll_seconds          = 5;
   g_settings.commission_poll_seconds        = 60;
   g_settings.heartbeat_seconds              = 15;
   g_settings.trailing_check_seconds         = 2;
   g_settings.slippage_points                = 20;
   g_settings.sync_real_history              = true;
   g_settings.allow_cancel_external_pending  = false;
   g_settings.allow_modify_external_pending  = true;
   g_settings.allow_min_lot_when_exceed_budget = false;
   g_settings.reject_when_spread_exceeds_buf = false;
   g_settings.commission_is_round_turn       = false;

   g_settings.last_synced                    = 0;
   ArrayResize(g_newsEvents, 0);
}

void SyncDashboardGlobalVariables()
{
   GlobalVariableSet("GV_Dash_TimeFilterEnabled", g_settings.time_filter_enabled ? 1.0 : 0.0);
   GlobalVariableSet("GV_Dash_NewsFilterEnabled", g_settings.news_filter_enabled ? 1.0 : 0.0);
   GlobalVariableSet("GV_Dash_TradingStartHour",  (double)g_settings.trading_start_hour);
   GlobalVariableSet("GV_Dash_TradingEndHour",    (double)g_settings.trading_end_hour);
   GlobalVariableSet("GV_Dash_TelegramEnabled",   g_settings.telegram_notifications_enabled ? 1.0 : 0.0);
   GlobalVariableSet("GV_Dash_EmergencyStop",     g_settings.emergency_stop ? 1.0 : 0.0);
   GlobalVariableSet("GV_NewsFilter_Blocked",     IsWithinConfiguredNewsWindow() ? 1.0 : 0.0);
}

void SaveSettingsCacheToDisk(string settingsObjJson)
{
   if(settingsObjJson == "" || settingsObjJson == g_lastSavedSettingsJson) return;
   int h = FileOpen(SETTINGS_CACHE_FILE, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE)
   {
      Print("[V9 CACHE] Cannot write ", SETTINGS_CACHE_FILE, " err=", GetLastError());
      return;
   }
   FileWriteString(h, settingsObjJson);
   FileClose(h);
   g_lastSavedSettingsJson = settingsObjJson;
   Print("[V9 CACHE] Saved settings snapshot to ", SETTINGS_CACHE_FILE);
}

bool ApplySettingsJson(string obj, bool saveDisk)
{
   if(obj == "") return false;

   g_settings.auto_trade_enabled             = JsonBoolValue(obj, "auto_trade_enabled", g_settings.auto_trade_enabled);
   g_settings.trailing_enabled               = JsonBoolValue(obj, "trailing_enabled", g_settings.trailing_enabled);
   g_settings.breakeven_enabled              = JsonBoolValue(obj, "breakeven_enabled", g_settings.breakeven_enabled);
   g_settings.news_filter_enabled            = JsonBoolValue(obj, "news_filter_enabled", g_settings.news_filter_enabled);
   g_settings.news_block_before_min          = JsonIntValue(obj, "news_block_before_min", g_settings.news_block_before_min);
   g_settings.news_block_after_min           = JsonIntValue(obj, "news_block_after_min", g_settings.news_block_after_min);

   string rawNewsJson                        = JsonValue(obj, "news_events_json");
   if(rawNewsJson != "") g_settings.news_events_json = rawNewsJson;

   g_settings.time_filter_enabled            = JsonBoolValue(obj, "time_filter_enabled", g_settings.time_filter_enabled);
   g_settings.telegram_notifications_enabled = JsonBoolValue(obj, "telegram_notifications_enabled", g_settings.telegram_notifications_enabled);
   g_settings.emergency_stop                 = JsonBoolValue(obj, "emergency_stop", g_settings.emergency_stop);
   g_settings.emergency_close_all            = JsonBoolValue(obj, "emergency_close_all", g_settings.emergency_close_all);
   g_settings.risk_money                     = JsonDoubleValue(obj, "risk_money", g_settings.risk_money);
   g_settings.max_positions                  = JsonIntValue(obj, "max_positions", g_settings.max_positions);
   g_settings.reward_risk_ratio              = JsonDoubleValue(obj, "reward_risk_ratio", g_settings.reward_risk_ratio);
   g_settings.risk_tolerance_pct             = JsonDoubleValue(obj, "risk_tolerance_pct", g_settings.risk_tolerance_pct);
   g_settings.commission_fallback            = JsonDoubleValue(obj, "commission_fallback", g_settings.commission_fallback);
   g_settings.spread_buffer_points           = JsonIntValue(obj, "spread_buffer_points", g_settings.spread_buffer_points);
   g_settings.trailing_trigger_rr            = JsonDoubleValue(obj, "trailing_trigger_rr", g_settings.trailing_trigger_rr);
   g_settings.trailing_lock_rr               = JsonDoubleValue(obj, "trailing_lock_rr", g_settings.trailing_lock_rr);
   g_settings.breakeven_trigger_rr           = JsonDoubleValue(obj, "breakeven_trigger_rr", g_settings.breakeven_trigger_rr);

   string tm                                 = JsonValue(obj, "trailing_mode");
   if(tm != "") g_settings.trailing_mode     = tm;

   g_settings.trading_start_hour             = JsonIntValue(obj, "trading_start_hour", g_settings.trading_start_hour);
   g_settings.trading_end_hour               = JsonIntValue(obj, "trading_end_hour", g_settings.trading_end_hour);

   g_settings.poll_seconds                   = MathMax(1, JsonIntValue(obj, "poll_seconds", g_settings.poll_seconds));
   g_settings.settings_poll_seconds          = MathMax(1, JsonIntValue(obj, "settings_poll_seconds", g_settings.settings_poll_seconds));
   g_settings.commission_poll_seconds        = MathMax(5, JsonIntValue(obj, "commission_poll_seconds", g_settings.commission_poll_seconds));
   g_settings.heartbeat_seconds              = MathMax(5, JsonIntValue(obj, "heartbeat_seconds", g_settings.heartbeat_seconds));
   g_settings.trailing_check_seconds         = MathMax(1, JsonIntValue(obj, "trailing_check_seconds", g_settings.trailing_check_seconds));
   g_settings.slippage_points                = MathMax(0, JsonIntValue(obj, "slippage_points", g_settings.slippage_points));
   g_settings.sync_real_history              = JsonBoolValue(obj, "sync_real_history", g_settings.sync_real_history);
   g_settings.allow_cancel_external_pending  = JsonBoolValue(obj, "allow_cancel_external_pending", g_settings.allow_cancel_external_pending);
   g_settings.allow_modify_external_pending  = JsonBoolValue(obj, "allow_modify_external_pending", g_settings.allow_modify_external_pending);
   g_settings.allow_min_lot_when_exceed_budget = JsonBoolValue(obj, "allow_min_lot_when_exceed_budget", g_settings.allow_min_lot_when_exceed_budget);
   g_settings.reject_when_spread_exceeds_buf = JsonBoolValue(obj, "reject_when_spread_exceeds_buf", g_settings.reject_when_spread_exceeds_buf);
   g_settings.commission_is_round_turn       = JsonBoolValue(obj, "commission_is_round_turn", g_settings.commission_is_round_turn);

   if(g_settings.risk_money <= 0) g_settings.risk_money = 25;
   if(g_settings.max_positions < 0) g_settings.max_positions = 0;
   if(g_settings.reward_risk_ratio <= 0) g_settings.reward_risk_ratio = 2.0;
   if(g_settings.spread_buffer_points < 0) g_settings.spread_buffer_points = 0;
   if(g_settings.news_block_before_min < 0) g_settings.news_block_before_min = 3;
   if(g_settings.news_block_after_min < 0) g_settings.news_block_after_min = 3;
   if(g_settings.trading_start_hour < 0 || g_settings.trading_start_hour > 23) g_settings.trading_start_hour = 7;
   if(g_settings.trading_end_hour < 0 || g_settings.trading_end_hour > 23) g_settings.trading_end_hour = 23;

   if(!g_settings.emergency_stop)
      g_emergencyClosedOnce = false;

   g_settings.last_synced = TimeCurrent();
   RebuildNewsScheduleCache();
   SyncDashboardGlobalVariables();

   if(saveDisk)
      SaveSettingsCacheToDisk(obj);
   return true;
}

void LoadCachedSettingsFromDisk()
{
   if(!FileIsExist(SETTINGS_CACHE_FILE)) return;
   int h = FileOpen(SETTINGS_CACHE_FILE, FILE_READ | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return;

   string content = "";
   while(!FileIsEnding(h))
      content += FileReadString(h);
   FileClose(h);

   StringTrimLeft(content);
   StringTrimRight(content);
   if(content != "")
   {
      g_lastSavedSettingsJson = content;
      ApplySettingsJson(content, false);
      Print("[V9 CACHE] Loaded cached settings from disk");
   }
}

void PollSettings()
{
   string response; int status;
   if(!Request("GET", "/api/settings", "", response, status))
   {
      Print("[V9 SETTINGS] Poll failed HTTP=", status, " (using cached settings)");
      return;
   }
   string obj = JsonObjectValue(response, "settings");
   if(obj == "") return;
   ApplySettingsJson(obj, true);
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
   string digest = "";

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
      digest += c.symbol + ":" + DoubleToString(c.commission_per_lot, 4) + ";";
   }
   if(digest != g_lastCommissionDigest)
   {
      g_lastCommissionDigest = digest;
      Print("[V9 COMM] Updated cache: ", ArraySize(g_commissionCache), " symbols");
   }
}

double GetCommissionPerLot(string symbol)
{
   string symUp = symbol;
   StringToUpper(symUp);
   double c = g_settings.commission_fallback;
   for(int i = 0; i < ArraySize(g_commissionCache); i++)
      if(g_commissionCache[i].symbol == symUp)
      { c = g_commissionCache[i].commission_per_lot; break; }
   if(g_settings.commission_is_round_turn) c /= 2.0;
   return c;
}

//--- Volume normalization ------------------------------------------
int VolumeStepDigits(string symbol)
{
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0) return 2;
   int d = 0;
   double tmp = step;
   while(tmp < 1.0 && d < 8) { tmp *= 10; d++; }
   return d;
}

ENUM_ORDER_TYPE_FILLING PickFilling(string symbol)
{
   long modes = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   if((modes & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
   if((modes & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

//--- Unified risk breakdown ----------------------------------------
SRiskBreakdown ComputeRiskBreakdown(string symbol, double slDistance, double volume)
{
   SRiskBreakdown r;
   r.valid = false;
   r.slRisk = r.commission = r.spreadCost = r.total = r.riskPerLot = 0;

   double tickSize  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickValue <= 0) tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double ask   = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double bid   = SymbolInfoDouble(symbol, SYMBOL_BID);

   if(tickSize <= 0 || tickValue <= 0 || point <= 0 || ask <= 0 || bid <= 0)
   {
      r.error = StringFormat("Bad symbol params %s (tickSize=%.10f tickVal=%.10f point=%.10f ask=%.5f bid=%.5f)",
         symbol, tickSize, tickValue, point, ask, bid);
      return r;
   }
   if(slDistance <= 0 || volume <= 0)
   {
      r.error = "slDistance/volume <= 0";
      return r;
   }

   double spreadPoints = (ask - bid) / point + g_settings.spread_buffer_points;

   r.slRisk     = (slDistance / tickSize) * tickValue * volume;
   r.commission = GetCommissionPerLot(symbol) * volume;
   r.spreadCost = (spreadPoints * point / tickSize) * tickValue * volume;
   r.total      = r.slRisk + r.commission + r.spreadCost;
   r.riskPerLot = r.total / volume;
   r.valid      = true;
   return r;
}

double CalculateLotWithCommission(string symbol, double slDistance, double riskMoney)
{
   if(riskMoney <= 0) return 0;

   SRiskBreakdown rb = ComputeRiskBreakdown(symbol, slDistance, 1.0);
   if(!rb.valid) { Print("[V9 LOT] ", rb.error); return 0; }

   double lot = riskMoney / rb.riskPerLot;
   double vmin = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0)
   {
      Print("[V9 LOT] symbol ", symbol, " VOLUME_STEP invalid=", step);
      return 0;
   }

   int stepDigits = VolumeStepDigits(symbol);
   lot = MathFloor((lot + 1e-9) / step) * step;
   lot = NormalizeDouble(lot, stepDigits);

   if(lot < vmin)
   {
      if(g_settings.allow_min_lot_when_exceed_budget && vmin > 0 && vmin <= vmax)
         return NormalizeDouble(vmin, stepDigits);
      return 0;
   }
   if(lot > vmax) lot = vmax;
   return lot;
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
         else
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
         newSL = NormalizeDouble(newSL, digits);
         bool valid = isBuy
            ? (newSL < currentPrice - minDist)
            : (newSL > currentPrice + minDist);
         if(valid && MathAbs(newSL - currentSL) > point)
         {
            trade.SetExpertMagicNumber(MagicNumber);
            trade.SetDeviationInPoints(g_settings.slippage_points);
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
      trade.SetDeviationInPoints(g_settings.slippage_points);
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
   if(!g_settings.emergency_stop)
   {
      g_emergencyClosedOnce = false;
      return;
   }
   if(g_settings.emergency_close_all && !g_emergencyClosedOnce)
   {
      g_emergencyClosedOnce = true;
      CloseAllMagicPositions();
      CancelAllMagicPending();
      SendAccountSnapshot();
   }
}

//--- Heartbeat ------------------------------------------------------
void SendHeartbeat()
{
   int openCount = CountRealExposure();
   string body = StringFormat(
      "{\"component\":\"TRADE-V9-REAL\",\"status\":\"online\",\"details\":\"DIRECT-IND+MANUAL consumer; positions=%d; emergency=%s; news_block=%s\"}",
      openCount, g_settings.emergency_stop ? "ON" : "OFF", IsNewsBlocked() ? "ON" : "OFF");
   string response; int status;
   if(Request("POST", "/api/heartbeat", body, response, status))
      lastHeartbeatMs = GetTickCount();
}

//--- Open trade directly from Indicator signal -----------------------
bool ResolveOrderTypeFromCode(int otCode, string direction, string &normOrderType, string &errOut)
{
   if(otCode == 0)
   {
      normOrderType = "MARKET";
      if(direction == "BUY" || direction == "SELL") return true;
      errOut = "Invalid direction for MARKET";
      return false;
   }
   if(otCode == 1)
   {
      normOrderType = "BUY_LIMIT";
      if(direction != "BUY") { errOut = "BUY_LIMIT requires direction=BUY"; return false; }
      return true;
   }
   if(otCode == 2)
   {
      normOrderType = "SELL_LIMIT";
      if(direction != "SELL") { errOut = "SELL_LIMIT requires direction=SELL"; return false; }
      return true;
   }
   errOut = "Unsupported order_type code";
   return false;
}

bool ExecuteIndicatorSignal(string sigKey, string symbol, string direction, int otCode,
                            double entry, double sl, double tpRequested)
{
   if(!SymbolSelect(symbol, true))
   {
      Print("[V9 IND SKIP] ", sigKey, " symbol unavailable: ", symbol);
      return false;
   }

   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(bid <= 0 || ask <= 0 || point <= 0)
   {
      Print("[V9 IND SKIP] ", sigKey, " no live quote");
      return false;
   }

   if(g_settings.reject_when_spread_exceeds_buf && g_settings.spread_buffer_points > 0)
   {
      double curSpread = (ask - bid) / point;
      if(curSpread > (double)g_settings.spread_buffer_points)
      {
         Print("[V9 IND SKIP] ", sigKey, " spread ", DoubleToString(curSpread, 1),
               " > buffer ", g_settings.spread_buffer_points);
         return false;
      }
   }

   string normOrderType = "";
   string typeErr = "";
   if(!ResolveOrderTypeFromCode(otCode, direction, normOrderType, typeErr))
   {
      Print("[V9 IND SKIP] ", sigKey, " ", typeErr);
      return false;
   }

   if(normOrderType == "MARKET" && entry <= 0)
      entry = (direction == "BUY") ? ask : bid;

   bool isLimit = (normOrderType == "BUY_LIMIT" || normOrderType == "SELL_LIMIT");
   if(isLimit)
   {
      if(normOrderType == "BUY_LIMIT" && entry >= ask)
      {
         Print("[V9 IND SKIP] ", sigKey, " BUY_LIMIT requires entry < Ask");
         return false;
      }
      if(normOrderType == "SELL_LIMIT" && entry <= bid)
      {
         Print("[V9 IND SKIP] ", sigKey, " SELL_LIMIT requires entry > Bid");
         return false;
      }
   }

   double slDistance = MathAbs(entry - sl);
   if(slDistance <= 0)
   {
      Print("[V9 IND SKIP] ", sigKey, " SL distance zero");
      return false;
   }
   if((direction == "BUY" && sl >= entry) || (direction == "SELL" && sl <= entry))
   {
      Print("[V9 IND SKIP] ", sigKey, " invalid SL geometry");
      return false;
   }

   double riskMoney = g_settings.risk_money > 0 ? g_settings.risk_money : 25.0;
   double volume = CalculateLotWithCommission(symbol, slDistance, riskMoney);
   if(volume <= 0)
   {
      Print("[V9 IND SKIP] ", sigKey, " cannot compute valid lot for risk $", DoubleToString(riskMoney, 2));
      return false;
   }

   SRiskBreakdown rb = ComputeRiskBreakdown(symbol, slDistance, volume);
   if(!rb.valid)
   {
      Print("[V9 IND SKIP] ", sigKey, " ", rb.error);
      return false;
   }

   double budget = riskMoney * (1.0 + g_settings.risk_tolerance_pct / 100.0);
   if(rb.total > budget + 1e-9)
   {
      double vmin = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
      double vmax = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
      double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
      if(step <= 0) return false;
      int stepDigits = VolumeStepDigits(symbol);
      double maxLotForBudget = budget / rb.riskPerLot;
      double reducedLot = MathFloor((maxLotForBudget + 1e-9) / step) * step;
      reducedLot = NormalizeDouble(reducedLot, stepDigits);

      if(reducedLot < vmin)
      {
         if(g_settings.allow_min_lot_when_exceed_budget && vmin > 0 && vmin <= vmax)
            reducedLot = NormalizeDouble(vmin, stepDigits);
         else
         {
            Print("[V9 IND SKIP] ", sigKey, " risk/lot exceeds budget");
            return false;
         }
      }
      if(reducedLot > vmax) reducedLot = vmax;
      volume = reducedLot;
      rb = ComputeRiskBreakdown(symbol, slDistance, volume);
      if(!rb.valid) return false;
   }

   if(rb.total > budget + 1e-9 && !g_settings.allow_min_lot_when_exceed_budget)
   {
      Print("[V9 IND SKIP] ", sigKey, " total risk ", DoubleToString(rb.total, 2),
            " > budget ", DoubleToString(budget, 2));
      return false;
   }

   double tp = tpRequested > 0
      ? tpRequested
      : ((direction == "BUY")
         ? entry + slDistance * g_settings.reward_risk_ratio
         : entry - slDistance * g_settings.reward_risk_ratio);

   if((direction == "BUY" && tp <= entry) || (direction == "SELL" && tp >= entry))
   {
      Print("[V9 IND SKIP] ", sigKey, " invalid TP geometry");
      return false;
   }

   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   entry = NormalizeDouble(entry, digits);
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(g_settings.slippage_points);
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
      Print("[V9 IND ERR] ", sigKey, " OrderSend failed retcode=", trade.ResultRetcode(),
            " (", trade.ResultRetcodeDescription(), ")");
      return false;
   }

   ulong posTicket = trade.ResultOrder();
   if(PositionSelectByTicket(posTicket))
      posTicket = (ulong)PositionGetInteger(POSITION_TICKET);

   Print("[V9 IND OPEN] ", sigKey, " ", symbol, " ", normOrderType, " ", direction,
         " lots=", DoubleToString(volume, 4),
         " entry=", DoubleToString(entry, digits),
         " SL=", DoubleToString(sl, digits),
         " TP=", DoubleToString(tp, digits),
         " risk=$", DoubleToString(rb.total, 2),
         " ticket=", posTicket);
   return true;
}

void CheckIndicatorSymbolSignal(string symbol)
{
   string symUp = UpperTrim(symbol);
   if(symUp == "") return;

   string pfx = "GV_IND_" + symUp + "_";
   if(!GlobalVariableCheck(pfx + "SEQ")) return;

   long seq = (long)GlobalVariableGet(pfx + "SEQ");
   if(seq <= 0) return;

   long sigTime = (long)GlobalVariableGet(pfx + "TIME");
   double dirVal = GlobalVariableGet(pfx + "DIR");
   int otCode = (int)MathRound(GlobalVariableGet(pfx + "ORDER_TYPE"));
   double entry = GlobalVariableGet(pfx + "ENTRY");
   double sl = GlobalVariableGet(pfx + "SL");
   double tp = GlobalVariableGet(pfx + "TP");

   string sigKey = symUp + ":" + IntegerToString(seq) + ":" + IntegerToString(sigTime);
   if(HasProcessedSignalKey(sigKey)) return;

   long nowTs = (long)TimeCurrent();
   if(sigTime > 0 && (nowTs - sigTime) > 300)
   {
      MarkProcessedSignalKey(sigKey);
      return;
   }

   string direction = (dirVal > 0) ? "BUY" : ((dirVal < 0) ? "SELL" : "");
   if(direction == "" || sl <= 0)
   {
      MarkProcessedSignalKey(sigKey);
      return;
   }

   if(g_settings.emergency_stop)
   {
      MarkProcessedSignalKey(sigKey);
      Print("[V9 IND BLOCK] ", sigKey, " Emergency Stop ON");
      return;
   }
   if(!g_settings.auto_trade_enabled)
   {
      MarkProcessedSignalKey(sigKey);
      Print("[V9 IND BLOCK] ", sigKey, " AutoTrade OFF");
      return;
   }
   if(!IsInTradingHours())
   {
      MarkProcessedSignalKey(sigKey);
      Print("[V9 IND BLOCK] ", sigKey, " outside trading hours");
      return;
   }
   if(IsNewsBlocked())
   {
      MarkProcessedSignalKey(sigKey);
      Print("[V9 IND BLOCK] ", sigKey, " news block active (-",
            g_settings.news_block_before_min, "m/+", g_settings.news_block_after_min, "m)");
      return;
   }
   int openCount = CountRealExposure();
   if(openCount >= g_settings.max_positions)
   {
      MarkProcessedSignalKey(sigKey);
      Print("[V9 IND BLOCK] ", sigKey, " max positions reached (", openCount, "/", g_settings.max_positions, ")");
      return;
   }

   MarkProcessedSignalKey(sigKey);
   if(ExecuteIndicatorSignal(sigKey, symUp, direction, otCode, entry, sl, tp))
      SendAccountSnapshot();
}

void PollIndicatorSignals()
{
   if(IndicatorSymbolsCsv != "")
   {
      string parts[];
      int cnt = StringSplit(IndicatorSymbolsCsv, ',', parts);
      for(int i = 0; i < cnt; i++)
         CheckIndicatorSymbolSignal(parts[i]);
      return;
   }

   CheckIndicatorSymbolSignal(_Symbol);

   int totalSym = SymbolsTotal(true);
   for(int i = 0; i < totalSym; i++)
   {
      string s = SymbolName(i, true);
      if(s != "" && s != _Symbol)
         CheckIndicatorSymbolSignal(s);
   }
}

//--- Manual Chart Orders via /api/mt5/next + /api/mt5/ack -----------
void AckManualSignal(string signalId, string statusStr, string detail)
{
   if(signalId == "") return;
   string body = StringFormat(
      "{\"signal_id\":\"%s\",\"status\":\"%s\",\"message\":\"%s\"}",
      JsonEscape(signalId), JsonEscape(statusStr), JsonEscape(detail));
   string response; int status;
   Request("POST", "/api/mt5/ack", body, response, status);
}

void ExecuteManualSignalJson(string sigJson)
{
   string sigId = JsonValue(sigJson, "id");
   if(sigId == "") return;
   if(HasProcessedSignalKey("manual:" + sigId)) return;

   string symbol    = UpperTrim(JsonValue(sigJson, "symbol"));
   string direction = UpperTrim(JsonValue(sigJson, "direction"));
   string orderType = UpperTrim(JsonValue(sigJson, "order_type"));
   if(orderType == "") orderType = "MARKET";

   double entry        = StringToDouble(JsonValue(sigJson, "entry"));
   double sl           = StringToDouble(JsonValue(sigJson, "sl"));
   double tp           = StringToDouble(JsonValue(sigJson, "tp"));
   double reqLot       = StringToDouble(JsonValue(sigJson, "requested_lot"));
   double riskMoneyReq = StringToDouble(JsonValue(sigJson, "risk_money"));

   MarkProcessedSignalKey("manual:" + sigId);

   if(g_settings.emergency_stop)
   {
      AckManualSignal(sigId, "REJECTED", "Emergency Stop ON");
      return;
   }
   if(!SymbolSelect(symbol, true))
   {
      AckManualSignal(sigId, "ERROR", "Symbol unavailable: " + symbol);
      return;
   }

   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(bid <= 0 || ask <= 0 || point <= 0)
   {
      AckManualSignal(sigId, "ERROR", "No live quote");
      return;
   }

   if(g_settings.reject_when_spread_exceeds_buf && g_settings.spread_buffer_points > 0)
   {
      double curSpread = (ask - bid) / point;
      if(curSpread > (double)g_settings.spread_buffer_points)
      {
         AckManualSignal(sigId, "REJECTED", "Spread exceeds buffer");
         return;
      }
   }

   if(orderType == "MARKET" && entry <= 0)
      entry = (direction == "BUY") ? ask : bid;

   int otCode = 0;
   if(orderType == "BUY_LIMIT") otCode = 1;
   else if(orderType == "SELL_LIMIT") otCode = 2;

   if(otCode == 1 && entry >= ask)
   {
      AckManualSignal(sigId, "REJECTED", "BUY_LIMIT requires entry < Ask");
      return;
   }
   if(otCode == 2 && entry <= bid)
   {
      AckManualSignal(sigId, "REJECTED", "SELL_LIMIT requires entry > Bid");
      return;
   }

   double slDist = MathAbs(entry - sl);
   if(slDist <= 0)
   {
      AckManualSignal(sigId, "REJECTED", "Invalid SL distance");
      return;
   }

   double volume = 0;
   if(reqLot > 0)
   {
      double vmin = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
      double vmax = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
      double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
      if(step > 0)
      {
         int stepDigits = VolumeStepDigits(symbol);
         volume = NormalizeDouble(MathFloor((reqLot + 1e-9) / step) * step, stepDigits);
         if(volume < vmin) volume = NormalizeDouble(vmin, stepDigits);
         if(volume > vmax) volume = vmax;
      }
   }
   if(volume <= 0)
   {
      double rm = riskMoneyReq > 0 ? riskMoneyReq : g_settings.risk_money;
      volume = CalculateLotWithCommission(symbol, slDist, rm);
   }
   if(volume <= 0)
   {
      AckManualSignal(sigId, "REJECTED", "Cannot calculate valid lot");
      return;
   }

   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double normEntry = NormalizeDouble(entry, digits);
   double normSl    = NormalizeDouble(sl, digits);
   double normTp    = tp > 0
      ? NormalizeDouble(tp, digits)
      : NormalizeDouble(
         (direction == "BUY")
            ? normEntry + slDist * g_settings.reward_risk_ratio
            : normEntry - slDist * g_settings.reward_risk_ratio,
         digits
      );

   if((direction == "BUY" && (normSl >= normEntry || normTp <= normEntry)) ||
      (direction == "SELL" && (normSl <= normEntry || normTp >= normEntry)))
   {
      AckManualSignal(sigId, "REJECTED", "Invalid SL/TP geometry");
      return;
   }

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(g_settings.slippage_points);
   trade.SetTypeFilling(PickFilling(symbol));
   trade.SetAsyncMode(false);

   bool ok = false;
   if(otCode == 1)
      ok = trade.BuyLimit(volume, normEntry, symbol, normSl, normTp, ORDER_TIME_GTC, 0, OrderComment);
   else if(otCode == 2)
      ok = trade.SellLimit(volume, normEntry, symbol, normSl, normTp, ORDER_TIME_GTC, 0, OrderComment);
   else
      ok = (direction == "BUY")
         ? trade.Buy(volume, symbol, 0.0, normSl, normTp, OrderComment)
         : trade.Sell(volume, symbol, 0.0, normSl, normTp, OrderComment);

   if(ok && trade.ResultRetcode() == TRADE_RETCODE_DONE)
   {
      string msg = StringFormat("Manual %s %s %.4f @ %.*f ticket=%I64u",
                                symbol, orderType, volume, digits, normEntry, trade.ResultOrder());
      Print("[V9 MANUAL OPEN] ", msg);
      AckManualSignal(sigId, "PLACED", msg);
      SendAccountSnapshot();
   }
   else
   {
      string errMsg = StringFormat("OrderSend retcode=%d (%s)",
                                   trade.ResultRetcode(), trade.ResultRetcodeDescription());
      Print("[V9 MANUAL ERR] ", symbol, " ", errMsg);
      AckManualSignal(sigId, "ERROR", errMsg);
   }
}

void PollManualChartOrders()
{
   string response; int status;
   if(!Request("GET", "/api/mt5/next?consumer=" + ConsumerId, "", response, status)) return;
   string sigObj = JsonObjectValue(response, "signal");
   if(sigObj == "") return;
   ExecuteManualSignalJson(sigObj);
}

//--- Close / Cancel / SLTP / Pending Modify from Server Bridge ------
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
   trade.SetDeviationInPoints(g_settings.slippage_points);
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
      if(ordMagic != MagicNumber && !g_settings.allow_cancel_external_pending) continue;

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
      if(g_settings.sync_real_history) PublishRealClosedDeals();
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
      trade.SetDeviationInPoints(g_settings.slippage_points);
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
   double newSl    = StringToDouble(JsonValue(cmdJson, "new_sl"));
   double newTp    = StringToDouble(JsonValue(cmdJson, "new_tp"));
   ulong tk = (ulong)StringToInteger(ticket);
   bool ok = false;
   string detail = "";

   if(tk == 0 || !OrderSelect(tk))
   {
      detail = "Pending order not found";
   }
   else
   {
      long ordMagic = OrderGetInteger(ORDER_MAGIC);
      if(ordMagic != MagicNumber && !g_settings.allow_modify_external_pending)
      {
         detail = "External pending order not allowed";
      }
      else
      {
         ENUM_ORDER_TYPE ot = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
         if(ot != ORDER_TYPE_BUY_LIMIT && ot != ORDER_TYPE_SELL_LIMIT)
         {
            detail = "Only BUY_LIMIT / SELL_LIMIT modify supported";
         }
         else
         {
            string sym = OrderGetString(ORDER_SYMBOL);
            SymbolSelect(sym, true);
            int digits     = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
            double point   = SymbolInfoDouble(sym, SYMBOL_POINT);
            int stopLevel  = (int)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL);
            double minDist = stopLevel * point;
            double bid     = SymbolInfoDouble(sym, SYMBOL_BID);
            double ask     = SymbolInfoDouble(sym, SYMBOL_ASK);

            double curPrice = OrderGetDouble(ORDER_PRICE_OPEN);
            double curSl    = OrderGetDouble(ORDER_SL);
            double curTp    = OrderGetDouble(ORDER_TP);

            double price = NormalizeDouble(newEntry > 0.0 ? newEntry : curPrice, digits);
            double sl    = NormalizeDouble(newSl > 0.0 ? newSl : curSl, digits);
            double tp    = NormalizeDouble(newTp > 0.0 ? newTp : curTp, digits);

            bool priceValid = true;
            if(bid <= 0.0 || ask <= 0.0)
            {
               detail = "No live quote";
               priceValid = false;
            }
            else if(ot == ORDER_TYPE_BUY_LIMIT)
            {
               if(price >= ask - minDist)
               {
                  detail = StringFormat("BUY_LIMIT price %.5f must be < Ask %.5f (minDist %.5f)", price, ask, minDist);
                  priceValid = false;
               }
               if(priceValid && sl > 0.0 && sl >= price)
               {
                  detail = "BUY_LIMIT SL must be < Entry";
                  priceValid = false;
               }
               if(priceValid && tp > 0.0 && tp <= price)
               {
                  detail = "BUY_LIMIT TP must be > Entry";
                  priceValid = false;
               }
            }
            else if(ot == ORDER_TYPE_SELL_LIMIT)
            {
               if(price <= bid + minDist)
               {
                  detail = StringFormat("SELL_LIMIT price %.5f must be > Bid %.5f (minDist %.5f)", price, bid, minDist);
                  priceValid = false;
               }
               if(priceValid && sl > 0.0 && sl <= price)
               {
                  detail = "SELL_LIMIT SL must be > Entry";
                  priceValid = false;
               }
               if(priceValid && tp > 0.0 && tp >= price)
               {
                  detail = "SELL_LIMIT TP must be < Entry";
                  priceValid = false;
               }
            }

            if(priceValid)
            {
               datetime expiration = (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);
               ENUM_ORDER_TYPE_TIME expType = (ENUM_ORDER_TYPE_TIME)OrderGetInteger(ORDER_TYPE_TIME);

               trade.SetExpertMagicNumber(MagicNumber);
               trade.SetDeviationInPoints(g_settings.slippage_points);
               trade.SetTypeFilling(PickFilling(sym));
               trade.SetAsyncMode(false);

               if(trade.OrderModify(tk, price, sl, tp, expType, expiration))
               {
                  ok = true;
                  detail = StringFormat("Pending #%I64u updated @ %.*f (SL %.*f, TP %.*f)",
                                        tk, digits, price, digits, sl, digits, tp);
                  Print("[V9 PENDING MODIFY] ", sym, " ", detail);
               }
               else
               {
                  detail = StringFormat("OrderModify failed retcode=%d (%s)",
                                        trade.ResultRetcode(), trade.ResultRetcodeDescription());
                  Print("[V9 PENDING MODIFY ERR] ", sym, " #", tk, " ", detail);
               }
            }
         }
      }
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

//--- Account snapshot & history -------------------------------------
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
   if(!g_settings.sync_real_history) return;
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
   LoadCachedSettingsFromDisk();
   SyncDashboardGlobalVariables();
   CalculateServerGMTOffset();

   PollSettings();
   PollCommissions();

   EventSetTimer(1);
   Print("[V9] Started v3.19 DIRECT-INDICATOR + MANUAL HUB. magic=", MagicNumber,
         " | AutoTrade=", g_settings.auto_trade_enabled ? "ON" : "OFF",
         " | Trailing=", g_settings.trailing_enabled ? "ON" : "OFF",
         " | NewsWindow=-", g_settings.news_block_before_min, "m/+", g_settings.news_block_after_min, "m",
         " | Risk=$", DoubleToString(g_settings.risk_money, 2));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) { EventKillTimer(); }

void OnTick()
{
   PollIndicatorSignals();
}

void OnTimer()
{
   uint nowMs = GetTickCount();

   if(TimeCurrent() - g_lastOffsetCalcTime > 3600)
      CalculateServerGMTOffset();

   if(lastSettingsPollMs == 0 ||
      (uint)(nowMs - lastSettingsPollMs) >= (uint)(g_settings.settings_poll_seconds * 1000))
   {
      lastSettingsPollMs = nowMs;
      PollSettings();
   }

   CheckEmergencyStop();

   if(lastCommissionPollMs == 0 ||
      (uint)(nowMs - lastCommissionPollMs) >= (uint)(g_settings.commission_poll_seconds * 1000))
   {
      lastCommissionPollMs = nowMs;
      PollCommissions();
   }

   if(lastHeartbeatMs == 0 ||
      (uint)(nowMs - lastHeartbeatMs) >= (uint)(g_settings.heartbeat_seconds * 1000))
      SendHeartbeat();

   if(lastTrailingCheckMs == 0 ||
      (uint)(nowMs - lastTrailingCheckMs) >= (uint)(g_settings.trailing_check_seconds * 1000))
   {
      lastTrailingCheckMs = nowMs;
      ManageTrailing();
   }

   if(lastIndicatorPollMs == 0 ||
      (uint)(nowMs - lastIndicatorPollMs) >= (uint)(g_settings.poll_seconds * 1000))
   {
      lastIndicatorPollMs = nowMs;
      PollIndicatorSignals();
      PollManualChartOrders();
      PollCloseCommands();
   }

   if(lastSltpPollMs == 0 ||
      (uint)(nowMs - lastSltpPollMs) >= (uint)(g_settings.poll_seconds * 1000))
   {
      lastSltpPollMs = nowMs;
      PollSltpRequests();
   }

   if(lastPendingModifyPollMs == 0 ||
      (uint)(nowMs - lastPendingModifyPollMs) >= (uint)(g_settings.poll_seconds * 1000))
   {
      lastPendingModifyPollMs = nowMs;
      PollPendingModifyRequests();
   }

   if(lastPositionsSyncMs == 0 ||
      (uint)(nowMs - lastPositionsSyncMs) >= 3000)
   {
      lastPositionsSyncMs = nowMs;
      SendAccountSnapshot();
   }

   if(g_settings.sync_real_history &&
      (lastHistorySyncMs == 0 || (uint)(nowMs - lastHistorySyncMs) >= 15000))
   {
      lastHistorySyncMs = nowMs;
      PublishRealClosedDeals();
   }
}
