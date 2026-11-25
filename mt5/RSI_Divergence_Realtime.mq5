//+------------------------------------------------------------------+
//|                           RSI_Divergence_Realtime.mq5            |
//|   Realtime Signal Only -> Direct GV to EA + Full Server Sync     |
//|   Unified TimeGMT7() Clock + Synced TF for EA Candle Check       |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property link      "https://www.mql5.com"
#property version   "1.13"
#property indicator_chart_window
#property indicator_buffers 1
#property indicator_plots   1
#property indicator_type1   DRAW_NONE

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                 |
//+------------------------------------------------------------------+
string ExpiryDate = "2099.12.30";
datetime expiry = StringToTime(ExpiryDate);
string P = "DBL_";
#define MS_BARS_DEFAULT 100

input bool EnableM15 = true;
input bool EnableH1  = true;

// Thời gian quét giữ nguyên ở input của bộ quét
input bool EnableTimeFilter = true;
input int  ScanStartHour = 7;
input int  ScanEndHour = 23;

input bool EnableNewsFilter = true;
input string GV_NewsBlockedName = "GV_NewsFilter_Blocked";
input int NewsCheckIntervalSeconds = 30;
input bool EnablePushNotify = true;
input int NotifyCooldownMinutes = 5;
input double VolumeAutoRatio = 1.2;
input int MS_SwingBars = 3;
input int VolumeLookbackBars = 1;
input bool RT_RequireMonotonicMove = true;
input int RT_ConsecutiveBars = 1;
input bool EnableSignalOutput = true;
input int SignalExpirySeconds = 300;
input int RTPrintCooldownSeconds = 60;
input double RT_RetrackATRMultiplier = 0.3;
input string ExcludeSymbols = "";

// GỬI TÍN HIỆU TRỰC TIẾP CHO EA (QUA GLOBAL VARIABLES) & ĐỒNG BỘ SERVER
input group "=== Direct EA & Server Sync ==="
input bool   PublishToDirectEA_GV    = true;  // Ghi tín hiệu trực tiếp vào GV_IND_<SYM>_* cho EA
input bool   EnableServerSync        = true;  // Gửi heartbeat, scans & today-signals lên server
input bool   EnableServerSend        = true;  // Gửi tín hiệu realtime lên /api/signals/realtime
input bool   SendOnlyStrongSignal    = true;
input string ServerBaseUrl         = "https://investforex.onrender.com";
input string ServerSignalUrl         = "https://investforex.onrender.com/api/signals/realtime";
input string ServerApiKey            = "";
input int    ServerTimeoutMs         = 3000;
input int    ServerResendSeconds     = 30;
input int    HeartbeatIntervalSec    = 15;
input int    TodaySyncIntervalSec    = 20;

// TIMEZONE (Unified TimeGMT7)
int      g_brokerToGMTOffset     = 0;
datetime g_lastBrokerOffsetSync  = 0;
datetime g_lastScanM15           = 0, g_lastScanH1 = 0;
datetime g_todayStartGMT7        = 0;

struct SSymbolTFData
{
   string sym; ENUM_TIMEFRAMES tf; string tfStr; int rsiHandle; int atrHandle;
   datetime lastBullNotifyBar, lastBearNotifyBar, lastBullNotifyTime, lastBearNotifyTime;
   double bearSL, bullSL; datetime bearSLTime, bullSLTime;
   double maxVolBear, maxVolBull; int lastShiftCached; datetime lastBarTime;
   double prevHighPrice, lastHighPrice, prevHighRSI, lastHighRSI;
   datetime prevHighTime, lastHighTime;
   double prevLowPrice, lastLowPrice, prevLowRSI, lastLowRSI;
   datetime prevLowTime, lastLowTime;
   int lastHighShift, lastLowShift; double rsiBuf[];
   string histBearDisplay, histBullDisplay; datetime histBearTime, histBullTime;
   datetime lastStrongSignalPrintTime; string lastStrongSignalKey;
   string lastDirectEaSignalKey; datetime lastDirectEaSignalTime;
   string lastSentSignalKey; datetime lastSentSignalTime;

   double lastBearRegRTPrice;
   double lastBearHidRTPrice;
   double lastBullRegRTPrice;
   double lastBullHidRTPrice;

   datetime lastBearRegRTTime;
   datetime lastBearHidRTTime;
   datetime lastBullRegRTTime;
   datetime lastBullHidRTTime;

   bool pendingBearRegStrong;
   bool pendingBearHidStrong;
   bool pendingBullRegStrong;
   bool pendingBullHidStrong;
};

struct SRTLine { string text; datetime time; bool isToday; };
struct STodaySignal { string sym, tfStr, type, divType, signalType; datetime time, time1, time2; double price1, price2, rsi1, rsi2; };

struct SHudConfig
{
   bool enabled;
   bool showHeader;
   bool showFeatures;
   bool showRT;
   bool showHist;
   bool showToday;
   int  fontRT;
   int  fontHist;
   int  fontToday;
   int  fontHeader;
   int  startX;
   int  topMargin;
};

// GLOBAL VARIABLES
SSymbolTFData arrSymTF[];
STodaySignal g_todaySignals[];
int g_todaySignalCount = 0;
datetime g_lastUpdateTime = 0;
int g_heartbeatCounter = 0;
bool g_heartbeatState = false;
uint g_lastScanDurationMs = 0;
color g_statusColor = clrDimGray;
string g_statusText = "KHỞI TẠO...";
string g_latestRTSignal = "";
bool g_firstScanDone = false;
datetime g_initTime = 0;
datetime g_lastRTPrintTime = 0;
int g_lastRTCount = 0;
bool g_isNewsBlocked = false;
datetime g_lastNewsCheckTime = 0;

datetime g_lastServerHeartbeatTime = 0;
datetime g_lastTodaySyncTime = 0;
string g_lastSyncedSymbolsJson = "";

// FORWARD DECLARATIONS
void SyncBrokerGMTOffset(bool force = false);
datetime TimeGMT7();
datetime ServerTimeToGMT7(datetime serverTime);
datetime GetGMT7Now();
void UpdateTodayBounds();
bool IsInScanTime();
bool IsNewsFilterEnabledEffective();
bool IsNewsSafe();
void GetEnabledTimeframes(ENUM_TIMEFRAMES &tfs[], string &tfStrs[]);
double GetATRValue(int idx, string sym, ENUM_TIMEFRAMES tf, int period);
double GetCurrentSpreadPrice(string sym);
ENUM_TIMEFRAMES StringToEnum(string tfStr);

bool IsSymbolExcluded(string sym);
bool IsInTradeTime(datetime signalTime);

bool GetGvBool(string name, bool defVal);
int GetGvInt(string name, int defVal);

int GetSymbolTFIndex(string s, ENUM_TIMEFRAMES tf);
int InitSymTF(string sym, ENUM_TIMEFRAMES tf, string tfStr);
void ProcessSymbolTF(int idx, SRTLine &rtLines[], int &rtCount, string &histLines[], datetime &histTimes[], int &histCount);
double GetRSI(double &buf[], int shift);
bool IsRSISwingHigh(double &buf[], int shift);
bool IsRSISwingLow(double &buf[], int shift);
double GetVolumeRatioAtShift(string sym, int shift, ENUM_TIMEFRAMES tf);
bool CheckMiddleValid(string sym, ENUM_TIMEFRAMES tf, int shift2, int shift1, datetime refTime, bool isHigh);
bool IsMonotonicStructure(string sym, ENUM_TIMEFRAMES tf, int shiftNew, int shiftOld, bool isBearish);
double CalcVolumeRatio(string sym, ENUM_TIMEFRAMES tf, int shift, int bars, int maLen);
bool IsReversalCandle(string sym, ENUM_TIMEFRAMES tf, int shift);
bool ShouldRetrack(int idx, double currentPrice, double lastTrackedPrice, string sym, ENUM_TIMEFRAMES tf, datetime currentBarTime, datetime lastTrackedBarTime);

void AddTodaySignal(string sym, string tfStr, string type, string divType, string signalType, datetime time1, datetime time2, double price1, double price2, double rsi1, double rsi2);
void ClearOldTodaySignals();
string BuildTodaySignalsDisplay();
void Draw(string divergenceComment);
void UpdMsg(string n, string t, int x, int y, color c, int fontSize = 11, bool isBold = false);
void Cleanup(int validLineCount);
void ClearAll();
void SendDivergenceNotify(string sym, string tfStr, string divType, string direction, string timeStr);
void WriteHistoricalDivergence(string sym, string tfStr, bool isBearish, bool isRegular, datetime t1, double rsi1, double price1, datetime t2, double rsi2, double price2, double volRatio);
void WriteRTDivergence(string sym, string tfStr, bool isBearish, bool isRegular, datetime t1, double rsi1, double price1, datetime t2, double rsi2, double price2);
void ClearInactiveRTDivergence(string sym, string tfStr, ENUM_TIMEFRAMES tf);
void ClearAllDivergenceData(string sym, string tfStr);
void CleanupOldSignals();
void PrintStrongSignalOnce(SSymbolTFData &data, string sym, string tfStr, string type, string divType, double entryPrice, double slPrice, datetime signalTime, string signalKey);
void PrintRTSignalChange(int currentRTCount, string comment);
void WriteStrongSignalToGV(string sym, string tfStr, string type, double entryPrice, double slPrice, double highPrice, double lowPrice, bool isStrong, string divType, datetime signalTime, double score = 0);
void PublishScanSymbols(string symbols);
void PublishTodaySignals();
void CleanupInvalidRTTodaySignals();
bool IsValidSLForDirection(string type, double entryPrice, double slPrice);
void SyncHeartbeatToServer(string status, string details, bool force = false);
void SyncScansToServer(string scannedSymbols);
void SyncTodaySignalsToServer(bool force = false);

string EscapeJson(string s)
{
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   StringReplace(s, "\r", "\\r");
   StringReplace(s, "\n", "\\n");
   return s;
}

string BuildServerEndpoint(string path)
{
   string base = ServerBaseUrl;
   while(StringLen(base) > 0 && StringGetCharacter(base, StringLen(base) - 1) == '/')
      base = StringSubstr(base, 0, StringLen(base) - 1);
   if(StringLen(path) > 0 && StringGetCharacter(path, 0) != '/')
      path = "/" + path;
   return base + path;
}

int PostJsonToServer(string url, string jsonPayload, string &outResponse)
{
   outResponse = "";
   if(url == "") return -1;

   char postData[];
   int bodyLen = StringToCharArray(jsonPayload, postData, 0, WHOLE_ARRAY, CP_UTF8);
   if(bodyLen > 0)
      ArrayResize(postData, bodyLen - 1);

   string headers = "Content-Type: application/json\r\n";
   if(ServerApiKey != "")
      headers += "X-API-Key: " + ServerApiKey + "\r\n";

   char result[];
   string resultHeaders = "";
   ResetLastError();
   int httpCode = WebRequest("POST", url, headers, ServerTimeoutMs, postData, result, resultHeaders);
   if(ArraySize(result) > 0)
      outResponse = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   return httpCode;
}

void SyncHeartbeatToServer(string status, string details, bool force)
{
   if(!EnableServerSync) return;
   datetime nowGmt7 = TimeGMT7();
   int interval = MathMax(5, HeartbeatIntervalSec);
   if(!force && (nowGmt7 - g_lastServerHeartbeatTime) < interval) return;

   g_lastServerHeartbeatTime = nowGmt7;
   string url = BuildServerEndpoint("/api/heartbeat");
   string payload = StringFormat(
      "{\"component\":\"rsi-divergence-indicator\",\"status\":\"%s\",\"details\":\"%s\",\"gmt7\":%d}",
      EscapeJson(status),
      EscapeJson(details),
      (int)nowGmt7
   );
   string resp = "";
   PostJsonToServer(url, payload, resp);
}

void SyncScansToServer(string scannedSymbols)
{
   if(!EnableServerSync) return;
   string arrJson = "[";
   if(scannedSymbols != "")
   {
      string parts[];
      int n = StringSplit(scannedSymbols, ';', parts);
      int added = 0;
      for(int i = 0; i < n; i++)
      {
         string item = parts[i];
         StringTrimLeft(item);
         StringTrimRight(item);
         if(item == "") continue;
         if(added > 0) arrJson += ",";
         arrJson += "\"" + EscapeJson(item) + "\"";
         added++;
      }
   }
   arrJson += "]";

   if(arrJson == g_lastSyncedSymbolsJson) return;
   g_lastSyncedSymbolsJson = arrJson;

   string url = BuildServerEndpoint("/api/scans");
   string payload = StringFormat(
      "{\"component\":\"rsi-divergence-indicator\",\"symbols\":%s}",
      arrJson
   );
   string resp = "";
   PostJsonToServer(url, payload, resp);
}

void SyncTodaySignalsToServer(bool force)
{
   if(!EnableServerSync) return;
   datetime nowGmt7 = TimeGMT7();
   int interval = MathMax(5, TodaySyncIntervalSec);
   if(!force && (nowGmt7 - g_lastTodaySyncTime) < interval) return;

   g_lastTodaySyncTime = nowGmt7;
   string itemsJson = "[";
   int validCount = 0;
   for(int i = 0; i < g_todaySignalCount; i++)
   {
      if(g_todaySignals[i].signalType == "RT")
      {
         string prefix = "DIVDATA_" + g_todaySignals[i].sym + "_" + g_todaySignals[i].tfStr + "_";
         string tType = g_todaySignals[i].type;
         if(!GlobalVariableCheck(prefix + tType + "_ACTIVE") || GlobalVariableGet(prefix + tType + "_ACTIVE") == 0)
            continue;
      }
      string dir = (g_todaySignals[i].type == "BEAR") ? "SELL" : "BUY";
      string divNorm = (g_todaySignals[i].divType == "Hid") ? "Hid" : "Reg";
      if(validCount > 0) itemsJson += ",";
      itemsJson += StringFormat(
         "{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\",\"divergence\":\"%s\","
         "\"signal_type\":\"%s\",\"signal_time\":%d,\"price1\":%.8f,\"price2\":%.8f,"
         "\"rsi1\":%.2f,\"rsi2\":%.2f,\"time1\":%d,\"time2\":%d}",
         EscapeJson(g_todaySignals[i].sym),
         EscapeJson(g_todaySignals[i].tfStr),
         dir,
         divNorm,
         EscapeJson(g_todaySignals[i].signalType),
         (int)g_todaySignals[i].time,
         g_todaySignals[i].price1,
         g_todaySignals[i].price2,
         g_todaySignals[i].rsi1,
         g_todaySignals[i].rsi2,
         (int)g_todaySignals[i].time1,
         (int)g_todaySignals[i].time2
      );
      validCount++;
   }
   itemsJson += "]";

   string url = BuildServerEndpoint("/api/today-signals");
   string payload = StringFormat("{\"signals\":%s}", itemsJson);
   string resp = "";
   PostJsonToServer(url, payload, resp);
}

string BuildDedupSource(string signalKey)
{
   string cleanKey = signalKey;
   StringReplace(cleanKey, "|", "_");
   StringReplace(cleanKey, "SELL", "S");
   StringReplace(cleanKey, "BUY", "B");
   StringReplace(cleanKey, "Reg", "R");
   StringReplace(cleanKey, "Hid", "H");
   string dedupSource = "rt_" + cleanKey;
   if(StringLen(dedupSource) > 40)
      dedupSource = StringSubstr(dedupSource, StringLen(dedupSource) - 40, 40);
   return dedupSource;
}

//--- Ghi tín hiệu trực tiếp sang Global Variables cho EA ----------
void PublishDirectSignalToEA_GV(int idx, string sym, ENUM_TIMEFRAMES tf,
                                string side, string orderType,
                                double entryPrice, double slPrice, double tpPrice,
                                datetime signalTime, string signalKey)
{
   if(!PublishToDirectEA_GV) return;
   if(entryPrice <= 0 || slPrice <= 0) return;
   if(!IsValidSLForDirection(side, entryPrice, slPrice)) return;

   datetime nowGmt7 = TimeGMT7();
   if(idx >= 0 && idx < ArraySize(arrSymTF))
   {
      if(signalKey == arrSymTF[idx].lastDirectEaSignalKey &&
         (nowGmt7 - arrSymTF[idx].lastDirectEaSignalTime) < MathMax(1, ServerResendSeconds))
         return;
      arrSymTF[idx].lastDirectEaSignalKey = signalKey;
      arrSymTF[idx].lastDirectEaSignalTime = nowGmt7;
   }

   string symUp = sym;
   StringToUpper(symUp);
   string pfx = "GV_IND_" + symUp + "_";

   long symSeq = (long)GlobalVariableGet(pfx + "SEQ") + 1;
   double otCode = 0.0;
   if(orderType == "BUY_LIMIT") otCode = 1.0;
   else if(orderType == "SELL_LIMIT") otCode = 2.0;

   // Ghi TIME theo chuẩn TimeGMT7() và TF theo ENUM_TIMEFRAMES để EA đối chiếu đúng cây nến
   datetime sigTimeGmt7 = (signalTime > 0) ? ServerTimeToGMT7(signalTime) : nowGmt7;
   ENUM_TIMEFRAMES effectiveTf = (tf != PERIOD_CURRENT) ? tf :
      ((idx >= 0 && idx < ArraySize(arrSymTF)) ? arrSymTF[idx].tf : PERIOD_M15);

   GlobalVariableSet(pfx + "SEQ",        (double)symSeq);
   GlobalVariableSet(pfx + "TF",         (double)effectiveTf);
   GlobalVariableSet(pfx + "TIME",       (double)sigTimeGmt7);
   GlobalVariableSet(pfx + "DIR",        (side == "BUY") ? 1.0 : -1.0);
   GlobalVariableSet(pfx + "ORDER_TYPE", otCode);
   GlobalVariableSet(pfx + "ENTRY",      entryPrice);
   GlobalVariableSet(pfx + "SL",         slPrice);
   GlobalVariableSet(pfx + "TP",         tpPrice);
   GlobalVariableSet(pfx + "UPDATED",    (double)nowGmt7);

   long totalSeq = (long)GlobalVariableGet("GV_IND_SIG_SEQ") + 1;
   GlobalVariableSet("GV_IND_SIG_SEQ", (double)totalSeq);
}

void EmitRealtimeSignal(int idx, string sym, ENUM_TIMEFRAMES tf, string tfStr,
                        string side, string divType, bool isStrong,
                        double entryPrice, double slPrice, double tpPrice,
                        double refPrice1, double refPrice2, double volRatio,
                        datetime signalTime, string signalKey)
{
   if(SendOnlyStrongSignal && !isStrong) return;
   if(entryPrice <= 0 || slPrice <= 0) return;
   if(!IsValidSLForDirection(side, entryPrice, slPrice)) return;

   // 1. Luôn ghi trực tiếp qua GV cho EA — độc lập hoàn toàn với WebRequest
   PublishDirectSignalToEA_GV(idx, sym, tf, side, "MARKET", entryPrice, slPrice, tpPrice, signalTime, signalKey);

   // 2. Gửi HTTP lên server chỉ là luồng đồng bộ phụ; không ảnh hưởng việc vào lệnh
   if(!EnableServerSend) return;
   string targetUrl = (ServerSignalUrl != "") ? ServerSignalUrl : BuildServerEndpoint("/api/signals/realtime");

   datetime nowGmt7 = TimeGMT7();
   bool hasIdx = (idx >= 0 && idx < ArraySize(arrSymTF));
   int resendSec = MathMax(1, ServerResendSeconds);
   if(hasIdx && signalKey == arrSymTF[idx].lastSentSignalKey &&
      (nowGmt7 - arrSymTF[idx].lastSentSignalTime) < resendSec)
      return;

   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(digits <= 0) digits = 5;

   double score = isStrong ? MathMin(100.0, 70.0 + volRatio * 10.0) : MathMin(69.0, 40.0 + volRatio * 8.0);
   string dedupSource = BuildDedupSource(signalKey);

   string payload = StringFormat(
      "{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\",\"order_type\":\"MARKET\","
      "\"entry\":%s,\"sl\":%s,\"tp\":0,\"score\":%.2f,\"source\":\"%s\"}",
      EscapeJson(sym),
      EscapeJson(tfStr),
      EscapeJson(side),
      DoubleToString(entryPrice, digits),
      DoubleToString(slPrice, digits),
      score,
      EscapeJson(dedupSource)
   );

   string respStr = "";
   int httpCode = PostJsonToServer(targetUrl, payload, respStr);

   if(hasIdx)
   {
      arrSymTF[idx].lastSentSignalKey = signalKey;
      arrSymTF[idx].lastSentSignalTime = nowGmt7;
   }

   if(httpCode >= 200 && httpCode < 300)
   {
      Print("SERVER RT SIGNAL SENT [HTTP ", httpCode, "]: ", signalKey, " (", side, " ", divType, ") src=", dedupSource);
   }
}

//+------------------------------------------------------------------+
//| DASHBOARD HUD CONFIG (READ FROM GLOBAL VARIABLES)                |
//+------------------------------------------------------------------+
bool GetGvBool(string name, bool defVal)
{
   if(!GlobalVariableCheck(name)) return defVal;
   return (GlobalVariableGet(name) >= 0.5);
}

int GetGvInt(string name, int defVal)
{
   if(!GlobalVariableCheck(name)) return defVal;
   return (int)MathRound(GlobalVariableGet(name));
}

SHudConfig GetHudConfig()
{
   SHudConfig cfg;
   cfg.enabled      = GetGvBool("GV_HUD_ENABLED", true);
   cfg.showHeader   = GetGvBool("GV_HUD_SHOW_HEADER", true);
   cfg.showFeatures = GetGvBool("GV_HUD_SHOW_FEATURES", true);
   cfg.showRT       = GetGvBool("GV_HUD_SHOW_RT", true);
   cfg.showHist     = GetGvBool("GV_HUD_SHOW_HIST", true);
   cfg.showToday    = GetGvBool("GV_HUD_SHOW_TODAY", true);

   cfg.fontRT       = MathMax(7, MathMin(24, GetGvInt("GV_HUD_FONT_RT", 11)));
   cfg.fontHist     = MathMax(7, MathMin(24, GetGvInt("GV_HUD_FONT_HIST", 10)));
   cfg.fontToday    = MathMax(7, MathMin(24, GetGvInt("GV_HUD_FONT_TODAY", 9)));
   cfg.fontHeader   = MathMax(7, MathMin(24, GetGvInt("GV_HUD_FONT_HEADER", 9)));
   cfg.startX       = MathMax(0, GetGvInt("GV_HUD_START_X", 15));
   cfg.topMargin    = MathMax(0, GetGvInt("GV_HUD_TOP_MARGIN", 20));
   return cfg;
}

//+------------------------------------------------------------------+
//| UNIFIED TIMEGMT7() & NEWS                                        |
//+------------------------------------------------------------------+
void SyncBrokerGMTOffset(bool force)
{
   datetime now = TimeCurrent();
   if(!force && g_lastBrokerOffsetSync > 0 && (now - g_lastBrokerOffsetSync) < 300)
      return;

   datetime gt = TimeGMT();
   datetime st = TimeTradeServer();
   if(st <= 0) st = now;

   if(gt > 0 && st > 0)
   {
      int rawDiff = (int)(st - gt);
      int roundedHours = (int)MathRound((double)rawDiff / 3600.0);
      g_brokerToGMTOffset = roundedHours * 3600;
      g_lastBrokerOffsetSync = now;
   }
}

datetime TimeGMT7()
{
   datetime gt = TimeGMT();
   if(gt > 0)
      return gt + 7 * 3600;

   SyncBrokerGMTOffset(false);
   datetime st = TimeTradeServer();
   if(st <= 0) st = TimeCurrent();
   return (st - g_brokerToGMTOffset) + 7 * 3600;
}

datetime ServerTimeToGMT7(datetime serverTime)
{
   if(serverTime <= 0) return 0;
   SyncBrokerGMTOffset(false);
   return (serverTime - g_brokerToGMTOffset) + 7 * 3600;
}

datetime GetGMT7Now()
{
   return TimeGMT7();
}

void UpdateTodayBounds()
{
   datetime n = TimeGMT7();
   g_todayStartGMT7 = n - (n % 86400);
}

bool IsInScanTime()
{
   if(!EnableTimeFilter) return true;
   SyncBrokerGMTOffset(false);
   datetime n = TimeGMT7();
   MqlDateTime dt;
   TimeToStruct(n, dt);
   int h = dt.hour;
   if(ScanStartHour <= ScanEndHour)
      return (h >= ScanStartHour && h < ScanEndHour);
   else
      return (h >= ScanStartHour || h < ScanEndHour);
}

bool IsNewsFilterEnabledEffective()
{
   if(!EnableNewsFilter) return false;
   return GetGvBool("GV_Dash_NewsFilterEnabled", true);
}

bool IsNewsSafe()
{
   if(!IsNewsFilterEnabledEffective()) return true;
   if(!GlobalVariableCheck(GV_NewsBlockedName)) return true;
   return (GlobalVariableGet(GV_NewsBlockedName) < 0.5);
}

bool IsInTradeTime(datetime signalTime)
{
   if(!EnableTimeFilter) return true;
   datetime gmt7Time = ServerTimeToGMT7(signalTime);
   MqlDateTime dt; TimeToStruct(gmt7Time, dt);
   int h = dt.hour;
   if(ScanStartHour <= ScanEndHour) return (h >= ScanStartHour && h < ScanEndHour);
   else return (h >= ScanStartHour || h < ScanEndHour);
}

//+------------------------------------------------------------------+
//| HELPERS                                                          |
//+------------------------------------------------------------------+
void GetEnabledTimeframes(ENUM_TIMEFRAMES &tfs[], string &tss[])
{
   ArrayResize(tfs, 0); ArrayResize(tss, 0);
   if(EnableM15) { int i = ArraySize(tfs); ArrayResize(tfs, i+1); ArrayResize(tss, i+1); tfs[i] = PERIOD_M15; tss[i] = "M15"; }
   if(EnableH1)  { int i = ArraySize(tfs); ArrayResize(tfs, i+1); ArrayResize(tss, i+1); tfs[i] = PERIOD_H1;  tss[i] = "H1"; }
}

double GetATRValue(int idx, string sym, ENUM_TIMEFRAMES tf, int p)
{
   if(idx < 0 || idx >= ArraySize(arrSymTF)) return 0;
   if(arrSymTF[idx].atrHandle == INVALID_HANDLE)
      arrSymTF[idx].atrHandle = iATR(sym, tf, p);
   int h = arrSymTF[idx].atrHandle;
   if(h == INVALID_HANDLE) return 0;
   double b[]; ArraySetAsSeries(b, true);
   if(CopyBuffer(h, 0, 0, 1, b) <= 0) return 0;
   return (b[0] > 0 && MathIsValidNumber(b[0])) ? b[0] : 0;
}

double GetCurrentSpreadPrice(string sym)
{
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   if(ask > 0 && bid > 0 && ask >= bid)
      return (ask - bid);

   long sprPts = SymbolInfoInteger(sym, SYMBOL_SPREAD);
   double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(sprPts > 0 && pt > 0)
      return (double)sprPts * pt;

   return 0.0;
}

ENUM_TIMEFRAMES StringToEnum(string ts)
{
   if(ts == "M15") return PERIOD_M15;
   if(ts == "H1")  return PERIOD_H1;
   if(ts == "H4")  return PERIOD_H4;
   if(ts == "D1")  return PERIOD_D1;
   return PERIOD_CURRENT;
}

bool IsSymbolExcluded(string sym)
{
   if(ExcludeSymbols == "") return false;
   string upperSym = sym; StringToUpper(upperSym);
   string excludeList[]; int count = StringSplit(ExcludeSymbols, ',', excludeList);
   for(int i = 0; i < count; i++)
   {
      string exclude = excludeList[i]; StringTrimLeft(exclude); StringTrimRight(exclude); StringToUpper(exclude);
      if(exclude != "" && StringFind(upperSym, exclude) >= 0) return true;
   }
   return false;
}

bool IsValidSLForDirection(string type, double entryPrice, double slPrice)
{
   if(entryPrice <= 0 || slPrice <= 0) return false;
   if(type == "SELL") return (slPrice > entryPrice);
   if(type == "BUY")  return (slPrice < entryPrice);
   return false;
}

//+------------------------------------------------------------------+
//| RSI & VOLUME FUNCTIONS                                           |
//+------------------------------------------------------------------+
double GetRSI(double &buf[], int shift)
{
   if(shift < 0 || shift >= ArraySize(buf)) return -1;
   return buf[shift];
}

bool IsRSISwingHigh(double &buf[], int shift)
{
   double center = GetRSI(buf, shift);
   if(center < 0) return false;
   for(int i = 1; i <= MS_SwingBars; i++)
   {
      double left = GetRSI(buf, shift + i);
      double right = GetRSI(buf, shift - i);
      if(left < 0 || right < 0) return false;
      if(center <= left || center <= right) return false;
   }
   return true;
}

bool IsRSISwingLow(double &buf[], int shift)
{
   double center = GetRSI(buf, shift);
   if(center < 0) return false;
   for(int i = 1; i <= MS_SwingBars; i++)
   {
      double left = GetRSI(buf, shift + i);
      double right = GetRSI(buf, shift - i);
      if(left < 0 || right < 0) return false;
      if(center >= left || center >= right) return false;
   }
   return true;
}

double GetVolumeRatioAtShift(string sym, int shift, ENUM_TIMEFRAMES tf)
{
   int bars = Bars(sym, tf);
   if(shift < 0 || shift >= bars) return 0;
   const int MA_LEN = 11;
   double bestVolRatio = 0;
   bestVolRatio = MathMax(bestVolRatio, CalcVolumeRatio(sym, tf, shift, bars, MA_LEN));
   if(shift - 1 >= 0)
      bestVolRatio = MathMax(bestVolRatio, CalcVolumeRatio(sym, tf, shift - 1, bars, MA_LEN));
   if(shift + 1 < bars && IsReversalCandle(sym, tf, shift))
      bestVolRatio = MathMax(bestVolRatio, CalcVolumeRatio(sym, tf, shift + 1, bars, MA_LEN));
   return bestVolRatio;
}

bool IsReversalCandle(string sym, ENUM_TIMEFRAMES tf, int shift)
{
   double open = iOpen(sym, tf, shift), close = iClose(sym, tf, shift), high = iHigh(sym, tf, shift), low = iLow(sym, tf, shift);
   double body = MathAbs(close - open), range = high - low;
   if(range <= 0) return false;
   double upperWick = high - MathMax(open, close), lowerWick = MathMin(open, close) - low;
   if(body / range < 0.3) return true;
   if(upperWick > body * 2 && lowerWick < body * 0.5) return true;
   if(lowerWick > body * 2 && upperWick < body * 0.5) return true;
   if(close > open && upperWick < range * 0.1 && lowerWick < range * 0.1) return true;
   if(close < open && upperWick < range * 0.1 && lowerWick < range * 0.1) return true;
   if(shift + 1 < Bars(sym, tf))
   {
      double prevOpen = iOpen(sym, tf, shift + 1), prevClose = iClose(sym, tf, shift + 1);
      if((close > open) && (prevClose < prevOpen) && (open < prevClose) && (close > prevOpen)) return true;
      if((close < open) && (prevClose > prevOpen) && (open > prevClose) && (close < prevOpen)) return true;
   }
   return false;
}

double CalcVolumeRatio(string sym, ENUM_TIMEFRAMES tf, int shift, int bars, int maLen)
{
   if(shift + maLen > bars || maLen <= 0) return 0;
   double sum = 0;
   for(int i = shift; i < shift + maLen; i++)
      sum += (double)iTickVolume(sym, tf, i);
   double volMA = sum / maLen;
   if(volMA <= 0) return 0;
   return (double)iTickVolume(sym, tf, shift) / volMA;
}

bool CheckMiddleValid(string sym, ENUM_TIMEFRAMES tf, int shift2, int shift1, datetime refTime, bool isHigh)
{
   int s1 = iBarShift(sym, tf, refTime);
   if(shift2 >= s1) return true;
   double refPrice = isHigh ? iHigh(sym, tf, s1) : iLow(sym, tf, s1);
   double newPrice = isHigh ? iHigh(sym, tf, shift2) : iLow(sym, tf, shift2);
   double bound = isHigh ? MathMax(refPrice, newPrice) : MathMin(refPrice, newPrice);
   for(int i = shift2 + 1; i < s1; i++)
   {
      double val = isHigh ? iHigh(sym, tf, i) : iLow(sym, tf, i);
      if(isHigh && val > bound) return false;
      if(!isHigh && val < bound) return false;
   }
   return true;
}

bool IsMonotonicStructure(string sym, ENUM_TIMEFRAMES tf, int shiftNew, int shiftOld, bool isBearish)
{
   if(shiftNew >= shiftOld) return false;
   int reqBars = MathMax(1, RT_ConsecutiveBars);
   int streak = 0;
   if(isBearish)
   {
      double oldHigh = iHigh(sym, tf, shiftOld);
      for(int i = shiftOld - 1; i >= shiftNew; i--)
      {
         if(iHigh(sym, tf, i) > oldHigh) streak++;
         else streak = 0;
         if(streak >= reqBars) return true;
      }
      return false;
   }
   else
   {
      double oldLow = iLow(sym, tf, shiftOld);
      for(int i = shiftOld - 1; i >= shiftNew; i--)
      {
         if(iLow(sym, tf, i) < oldLow) streak++;
         else streak = 0;
         if(streak >= reqBars) return true;
      }
      return false;
   }
}

bool ShouldRetrack(int idx, double currentPrice, double lastTrackedPrice, string sym, ENUM_TIMEFRAMES tf, datetime currentBarTime, datetime lastTrackedBarTime)
{
   if(lastTrackedPrice <= 0) return true;
   if(currentBarTime != lastTrackedBarTime) return true;
   double atrValue = GetATRValue(idx, sym, tf, 14);
   if(atrValue <= 0)
   {
      double changePercent = MathAbs(currentPrice - lastTrackedPrice) / lastTrackedPrice * 100.0;
      return (changePercent >= 0.1);
   }
   double priceChange = MathAbs(currentPrice - lastTrackedPrice);
   return (priceChange >= atrValue * RT_RetrackATRMultiplier);
}

int GetSymbolTFIndex(string s, ENUM_TIMEFRAMES tf)
{
   for(int i = 0; i < ArraySize(arrSymTF); i++)
      if(arrSymTF[i].sym == s && arrSymTF[i].tf == tf)
         return i;
   return -1;
}

int InitSymTF(string sym, ENUM_TIMEFRAMES tf, string tfStr)
{
   int idx = ArraySize(arrSymTF);
   ArrayResize(arrSymTF, idx + 1);
   ZeroMemory(arrSymTF[idx]);
   arrSymTF[idx].sym = sym;
   arrSymTF[idx].tf = tf;
   arrSymTF[idx].tfStr = tfStr;
   arrSymTF[idx].rsiHandle = iRSI(sym, tf, 14, PRICE_CLOSE);
   arrSymTF[idx].atrHandle = iATR(sym, tf, 14);
   arrSymTF[idx].lastShiftCached = MS_BARS_DEFAULT - 1;
   arrSymTF[idx].lastHighShift = -9999;
   arrSymTF[idx].lastLowShift = -9999;
   arrSymTF[idx].histBearTime = 0;
   arrSymTF[idx].histBullTime = 0;
   arrSymTF[idx].lastBearNotifyTime = 0;
   arrSymTF[idx].lastBullNotifyTime = 0;
   arrSymTF[idx].lastStrongSignalPrintTime = 0;
   arrSymTF[idx].lastStrongSignalKey = "";
   arrSymTF[idx].lastDirectEaSignalKey = "";
   arrSymTF[idx].lastDirectEaSignalTime = 0;
   arrSymTF[idx].lastSentSignalKey = "";
   arrSymTF[idx].lastSentSignalTime = 0;
   arrSymTF[idx].lastBearRegRTPrice = 0;
   arrSymTF[idx].lastBearHidRTPrice = 0;
   arrSymTF[idx].lastBullRegRTPrice = 0;
   arrSymTF[idx].lastBullHidRTPrice = 0;
   arrSymTF[idx].lastBearRegRTTime = 0;
   arrSymTF[idx].lastBearHidRTTime = 0;
   arrSymTF[idx].lastBullRegRTTime = 0;
   arrSymTF[idx].lastBullHidRTTime = 0;
   arrSymTF[idx].pendingBearRegStrong = false;
   arrSymTF[idx].pendingBearHidStrong = false;
   arrSymTF[idx].pendingBullRegStrong = false;
   arrSymTF[idx].pendingBullHidStrong = false;
   ArraySetAsSeries(arrSymTF[idx].rsiBuf, true);
   return idx;
}

//+------------------------------------------------------------------+
//| SIGNAL OUTPUT FUNCTIONS                                          |
//+------------------------------------------------------------------+
void SendDivergenceNotify(string sym, string tfStr, string divType, string direction, string timeStr)
{
   if(!EnablePushNotify) return;
   string msg = StringFormat("[IND] %s %s %s %s Div | %s", sym, tfStr, direction, divType, timeStr);
   if(SendNotification(msg))
      Print("Push sent: ", msg);
   else
      Print("Push FAILED! Error: ", GetLastError());
}

void WriteStrongSignalToGV(string sym, string tfStr, string type, double entryPrice, double slPrice, double highPrice, double lowPrice, bool isStrong, string divType, datetime signalTime, double score = 0)
{
   if(!EnableSignalOutput || !isStrong) return;
   if(!IsValidSLForDirection(type, entryPrice, slPrice)) return;
   datetime nowGmt7 = TimeGMT7();
   datetime sigGmt7 = (signalTime > 0) ? ServerTimeToGMT7(signalTime) : nowGmt7;

   string prefix = "DIVSIGNAL_";
   GlobalVariableSet(prefix + "TYPE", type == "SELL" ? -1 : 1);
   GlobalVariableSet(prefix + "ORDER_TYPE", 0);
   GlobalVariableSet(prefix + "ENTRY", entryPrice);
   GlobalVariableSet(prefix + "SL", slPrice);
   GlobalVariableSet(prefix + "HIGH", highPrice);
   GlobalVariableSet(prefix + "LOW", lowPrice);
   GlobalVariableSet(prefix + "TIME", (double)sigGmt7);
   GlobalVariableSet(prefix + "ACTIVE", 1);
   GlobalVariableSet(prefix + "TF", StringToEnum(tfStr));
   if(score > 0) GlobalVariableSet(prefix + "SCORE", score);
   int q = FileOpen("DIVSIGNAL_QUEUE.txt", FILE_READ | FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(q != INVALID_HANDLE)
   {
      FileSeek(q, 0, SEEK_END);
      string queueLine = StringFormat("%s|%s|%s|%s|%d|%.8f|%.8f|%.2f|%d\r\n", sym, tfStr, type, divType, (int)sigGmt7, entryPrice, slPrice, score, (int)nowGmt7);
      FileWriteString(q, queueLine);
      FileClose(q);
   }
   int h = FileOpen("DIVSIGNAL_SYMBOL.txt", FILE_WRITE | FILE_TXT | FILE_COMMON);
   if(h != INVALID_HANDLE)
   {
      FileWriteString(h, sym + "|" + tfStr + "|" + type);
      FileClose(h);
   }
}

void WriteHistoricalDivergence(string sym, string tfStr, bool isBearish, bool isRegular, datetime t1, double rsi1, double price1, datetime t2, double rsi2, double price2, double volRatio)
{
   if(!EnableSignalOutput) return;
   string prefix = "DIVDATA_" + sym + "_" + tfStr + "_";
   string type = isBearish ? "BEAR" : "BULL";
   int slot = -1;
   datetime oldestTime = D'2099.01.01';
   for(int i = 0; i < 20; i++)
   {
      string key = prefix + "HIST_" + type + "_" + IntegerToString(i) + "_TIME";
      if(!GlobalVariableCheck(key))
      {
         slot = i;
         break;
      }
      else
      {
         datetime t = (datetime)GlobalVariableGet(key);
         if(t < oldestTime) { oldestTime = t; slot = i; }
      }
   }
   if(slot >= 0)
   {
      string base = prefix + "HIST_" + type + "_" + IntegerToString(slot);
      GlobalVariableSet(base + "_TIME", (double)t2);
      GlobalVariableSet(base + "_T1", (double)t1);
      GlobalVariableSet(base + "_RSI1", rsi1);
      GlobalVariableSet(base + "_PRICE1", price1);
      GlobalVariableSet(base + "_T2", (double)t2);
      GlobalVariableSet(base + "_RSI2", rsi2);
      GlobalVariableSet(base + "_PRICE2", price2);
      GlobalVariableSet(base + "_ISREG", isRegular ? 1 : 0);
      GlobalVariableSet(base + "_VOL", volRatio);
      GlobalVariableSet(base + "_ACTIVE", 1);
   }
}

void WriteRTDivergence(string sym, string tfStr, bool isBearish, bool isRegular, datetime t1, double rsi1, double price1, datetime t2, double rsi2, double price2)
{
   if(!EnableSignalOutput) return;
   string prefix = "DIVDATA_" + sym + "_" + tfStr + "_";
   string type = isBearish ? "BEAR" : "BULL";
   GlobalVariableSet(prefix + type + "_T1", (double)t1);
   GlobalVariableSet(prefix + type + "_RSI1", rsi1);
   GlobalVariableSet(prefix + type + "_PRICE1", price1);
   GlobalVariableSet(prefix + type + "_T2", (double)t2);
   GlobalVariableSet(prefix + type + "_RSI2", rsi2);
   GlobalVariableSet(prefix + type + "_PRICE2", price2);
   GlobalVariableSet(prefix + type + "_ISREG", isRegular ? 1 : 0);
   GlobalVariableSet(prefix + type + "_ACTIVE", 1);
   GlobalVariableSet(prefix + type + "_TIME", (double)TimeGMT7());
}

void ClearInactiveRTDivergence(string sym, string tfStr, ENUM_TIMEFRAMES tf)
{
   string prefix = "DIVDATA_" + sym + "_" + tfStr + "_";
   datetime nowGmt7 = TimeGMT7();

   if(GlobalVariableCheck(prefix + "BEAR_ACTIVE") && GlobalVariableGet(prefix + "BEAR_ACTIVE") == 1)
   {
      bool shouldClear = false;
      if(GlobalVariableCheck(prefix + "BEAR_TIME"))
      {
         if(nowGmt7 - (datetime)GlobalVariableGet(prefix + "BEAR_TIME") > 10)
            shouldClear = true;
      }
      else shouldClear = true;
      if(!shouldClear && GlobalVariableCheck(prefix + "BEAR_T2"))
      {
         datetime t2 = (datetime)GlobalVariableGet(prefix + "BEAR_T2");
         double price2 = GlobalVariableGet(prefix + "BEAR_PRICE2");
         int curShift = iBarShift(sym, tf, TimeCurrent());
         int divShift = iBarShift(sym, tf, t2);
         if(divShift >= 0 && curShift >= 0)
            for(int i = divShift - 1; i >= curShift; i--)
               if(iHigh(sym, tf, i) > price2)
               { shouldClear = true; break; }
      }
      if(shouldClear)
         GlobalVariableSet(prefix + "BEAR_ACTIVE", 0);
   }

   if(GlobalVariableCheck(prefix + "BULL_ACTIVE") && GlobalVariableGet(prefix + "BULL_ACTIVE") == 1)
   {
      bool shouldClear = false;
      if(GlobalVariableCheck(prefix + "BULL_TIME"))
      {
         if(nowGmt7 - (datetime)GlobalVariableGet(prefix + "BULL_TIME") > 10)
            shouldClear = true;
      }
      else shouldClear = true;
      if(!shouldClear && GlobalVariableCheck(prefix + "BULL_T2"))
      {
         datetime t2 = (datetime)GlobalVariableGet(prefix + "BULL_T2");
         double price2 = GlobalVariableGet(prefix + "BULL_PRICE2");
         int curShift = iBarShift(sym, tf, TimeCurrent());
         int divShift = iBarShift(sym, tf, t2);
         if(divShift >= 0 && curShift >= 0)
            for(int i = divShift - 1; i >= curShift; i--)
               if(iLow(sym, tf, i) < price2)
               { shouldClear = true; break; }
      }
      if(shouldClear)
         GlobalVariableSet(prefix + "BULL_ACTIVE", 0);
   }
}

void ClearAllDivergenceData(string sym, string tfStr)
{
   string prefix = "DIVDATA_" + sym + "_" + tfStr + "_";
   GlobalVariableDel(prefix + "BEAR_ACTIVE");
   GlobalVariableDel(prefix + "BULL_ACTIVE");
   for(int i = 0; i < 20; i++)
   {
      GlobalVariableDel(prefix + "HIST_BEAR_" + IntegerToString(i) + "_TIME");
      GlobalVariableDel(prefix + "HIST_BEAR_" + IntegerToString(i) + "_ACTIVE");
      GlobalVariableDel(prefix + "HIST_BULL_" + IntegerToString(i) + "_TIME");
      GlobalVariableDel(prefix + "HIST_BULL_" + IntegerToString(i) + "_ACTIVE");
   }
}

void CleanupOldSignals()
{
   string prefix = "DIVSIGNAL_";
   if(GlobalVariableCheck(prefix + "TIME"))
   {
      if(TimeGMT7() - (datetime)GlobalVariableGet(prefix + "TIME") > SignalExpirySeconds)
      {
         GlobalVariableSet(prefix + "ACTIVE", 0);
         FileDelete("DIVSIGNAL_SYMBOL.txt", FILE_COMMON);
      }
   }
}

void PrintStrongSignalOnce(SSymbolTFData &data, string sym, string tfStr, string type, string divType, double entryPrice, double slPrice, datetime signalTime, string signalKey)
{
   if(signalKey == data.lastStrongSignalKey) return;
   datetime gmt7Time = ServerTimeToGMT7(signalTime);
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(digits <= 0) digits = 5;
   Print("STRONG SIGNAL SENT: ", sym, " ", tfStr, " ", type, " ", divType, " Entry=", DoubleToString(entryPrice, digits), " SL=", DoubleToString(slPrice, digits), " Time=", TimeToString(gmt7Time, TIME_DATE | TIME_MINUTES));
   data.lastStrongSignalKey = signalKey;
   data.lastStrongSignalPrintTime = TimeGMT7();
}

void PrintRTSignalChange(int currentRTCount, string comment)
{
   datetime nowGmt7 = TimeGMT7();
   if(nowGmt7 - g_lastRTPrintTime >= RTPrintCooldownSeconds || currentRTCount != g_lastRTCount)
   {
      g_lastRTPrintTime = nowGmt7;
      g_lastRTCount = currentRTCount;
   }
}

void AddTodaySignal(string sym, string tfStr, string type, string divType, string signalType, datetime time1, datetime time2, double price1, double price2, double rsi1, double rsi2)
{
   for(int i = 0; i < g_todaySignalCount; i++)
      if(g_todaySignals[i].sym == sym && g_todaySignals[i].tfStr == tfStr && g_todaySignals[i].type == type && g_todaySignals[i].signalType == signalType && g_todaySignals[i].time == time2)
         return;
   int idx = g_todaySignalCount;
   ArrayResize(g_todaySignals, idx + 1);
   g_todaySignals[idx].sym = sym;
   g_todaySignals[idx].tfStr = tfStr;
   g_todaySignals[idx].type = type;
   g_todaySignals[idx].divType = divType;
   g_todaySignals[idx].signalType = signalType;
   g_todaySignals[idx].time = time2;
   g_todaySignals[idx].time1 = (time1 > 0) ? time1 : time2;
   g_todaySignals[idx].time2 = (time2 > 0) ? time2 : time1;
   g_todaySignals[idx].price1 = price1;
   g_todaySignals[idx].price2 = price2;
   g_todaySignals[idx].rsi1 = rsi1;
   g_todaySignals[idx].rsi2 = rsi2;
   g_todaySignalCount++;
}

void PublishTodaySignals()
{
   int h = FileOpen("DIVTODAY_SIGNALS.txt", FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_SHARE_READ);
   if(h == INVALID_HANDLE) return;
   datetime nowGMT7 = TimeGMT7();
   FileWriteString(h, "TIME|" + IntegerToString((int)nowGMT7) + "\r\n");
   for(int i = 0; i < g_todaySignalCount; i++)
   {
      string direction = (g_todaySignals[i].type == "BEAR") ? "SELL" : "BUY";
      string line = StringFormat("%s|%s|%s|%s|%s|%d|%.8f|%.8f|%.2f|%.2f|%d|%d\r\n",
         g_todaySignals[i].sym, g_todaySignals[i].tfStr, direction, g_todaySignals[i].divType,
         g_todaySignals[i].signalType, (int)g_todaySignals[i].time,
         g_todaySignals[i].price1, g_todaySignals[i].price2,
         g_todaySignals[i].rsi1, g_todaySignals[i].rsi2,
         (int)g_todaySignals[i].time1, (int)g_todaySignals[i].time2);
      FileWriteString(h, line);
   }
   FileClose(h);
}

void CleanupInvalidRTTodaySignals()
{
   for(int i = g_todaySignalCount - 1; i >= 0; i--)
   {
      if(g_todaySignals[i].signalType == "RT")
      {
         string prefix = "DIVDATA_" + g_todaySignals[i].sym + "_" + g_todaySignals[i].tfStr + "_";
         string type = g_todaySignals[i].type;
         if(!GlobalVariableCheck(prefix + type + "_ACTIVE") || GlobalVariableGet(prefix + type + "_ACTIVE") == 0)
         {
            if(i < g_todaySignalCount - 1)
               g_todaySignals[i] = g_todaySignals[g_todaySignalCount - 1];
            g_todaySignalCount--;
         }
      }
   }
}

void ClearOldTodaySignals()
{
   datetime gmt7Now = TimeGMT7();
   datetime todayStartGMT7 = gmt7Now - (gmt7Now % 86400);
   int validCount = 0;
   for(int i = 0; i < g_todaySignalCount; i++)
   {
      datetime signalGMT7 = ServerTimeToGMT7(g_todaySignals[i].time);
      if(signalGMT7 >= todayStartGMT7)
      {
         if(validCount != i)
            g_todaySignals[validCount] = g_todaySignals[i];
         validCount++;
      }
   }
   g_todaySignalCount = validCount;
}

string BuildTodaySignalsDisplay()
{
   CleanupInvalidRTTodaySignals();
   PublishTodaySignals();
   SyncTodaySignalsToServer(false);

   string display = "\nTODAY SIGNALS - " + IntegerToString(g_todaySignalCount) + " signals\n";
   display += " \n";

   if(g_todaySignalCount > 0)
   {
      for(int i = 0; i < g_todaySignalCount - 1; i++)
         for(int j = i + 1; j < g_todaySignalCount; j++)
            if(ServerTimeToGMT7(g_todaySignals[i].time) < ServerTimeToGMT7(g_todaySignals[j].time))
            {
               STodaySignal tmp = g_todaySignals[i];
               g_todaySignals[i] = g_todaySignals[j];
               g_todaySignals[j] = tmp;
            }
      for(int i = 0; i < g_todaySignalCount; i++)
      {
         string icon = (g_todaySignals[i].type == "BEAR") ? "[SELL]" : "[BUY]";
         string sigTag = (g_todaySignals[i].signalType == "RT") ? " [RT]" : "";
         datetime gmt7Time = ServerTimeToGMT7(g_todaySignals[i].time);
         string timeStr = TimeToString(gmt7Time, TIME_MINUTES);
         display += StringFormat("%s%s %s %s [%s] %s\n", icon, sigTag, g_todaySignals[i].sym, g_todaySignals[i].tfStr, g_todaySignals[i].divType, timeStr);
      }
   }
   else
   {
      display += "No signals yet\n";
   }

   display += "----------------------------------------\n";
   return display;
}

//+------------------------------------------------------------------+
//| DISPLAY FUNCTIONS                                                |
//+------------------------------------------------------------------+
void Draw(string divergenceComment)
{
   SHudConfig hud = GetHudConfig();
   if(!hud.enabled)
   {
      ClearAll();
      return;
   }

   int rowH = 18;
   int y = hud.topMargin;

   if(hud.showHeader)
   {
      datetime gmt7Time = TimeGMT7();
      string timeStr = TimeToString(gmt7Time, TIME_MINUTES | TIME_SECONDS);
      string dotChar = g_heartbeatState ? "●" : "○";
      string speedStr = (g_lastScanDurationMs >= 1000)
         ? DoubleToString((double)g_lastScanDurationMs / 1000.0, 1) + "s"
         : IntegerToString((int)g_lastScanDurationMs) + "ms";

      string timeFilterStr = EnableTimeFilter ? " | " + IntegerToString(ScanStartHour) + "h-" + IntegerToString(ScanEndHour) + "h" : " | 24/7";
      string line1Status = StringFormat("%s %s | %s | %s%s",
                                       dotChar, speedStr, g_statusText, timeStr, timeFilterStr);
      UpdMsg("HEARTBEAT", line1Status, hud.startX, y, g_statusColor, hud.fontHeader, true);
      y += rowH + 2;
   }
   else
   {
      string hn = P + "HEARTBEAT";
      if(ObjectFind(0, hn) >= 0) ObjectDelete(0, hn);
   }

   if(hud.showFeatures)
   {
      string tfListStr = "";
      if(EnableM15) tfListStr += "M15 ";
      if(EnableH1)  tfListStr += "H1 ";

      string signalStatus = EnableSignalOutput ? "SIG:ON" : "SIG:OFF";
      string notifyStatus = EnablePushNotify ? "PUSH:ON" : "PUSH:OFF";
      string monoStatus = RT_RequireMonotonicMove ? "MONO:ON" : "MONO:OFF";
      string newsStatus = "";
      if(IsNewsFilterEnabledEffective())
         newsStatus = g_isNewsBlocked ? "NEWS:BLOCKED" : "NEWS:SAFE";
      else
         newsStatus = "NEWS:OFF";

      string line2Features = StringFormat("%s | %s | %s | %s | %s",
                                         tfListStr, signalStatus, notifyStatus, monoStatus, newsStatus);
      color line2Color = g_isNewsBlocked ? clrOrangeRed : clrDimGray;
      UpdMsg("FEATURES", line2Features, hud.startX, y, line2Color, 9, false);
      y += rowH + 6;
   }
   else
   {
      string fn = P + "FEATURES";
      if(ObjectFind(0, fn) >= 0) ObjectDelete(0, fn);
   }

   if(hud.showHeader || hud.showFeatures)
   {
      UpdMsg("LINE_DECOR_1", "================================================================================", hud.startX, y, clrDarkGray, 8);
      y += 10;
   }
   else
   {
      string ln = P + "LINE_DECOR_1";
      if(ObjectFind(0, ln) >= 0) ObjectDelete(0, ln);
   }

   if(divergenceComment != "" && StringFind(divergenceComment, "CRASHED") < 0)
   {
      string divLines[];
      int divCount = StringSplit(divergenceComment, '\n', divLines);
      bool inHistorical = false, inToday = false;
      int validLineCount = 0;

      for(int i = 0; i < divCount; i++)
      {
         if(divLines[i] == "") continue;

         if(StringFind(divLines[i], "[M15]") >= 0 || StringFind(divLines[i], "[H1]") >= 0)
         {
            inHistorical = true;
            inToday = false;
            y += 4;
         }
         if(StringFind(divLines[i], "TODAY") >= 0)
         {
            inHistorical = false;
            inToday = true;
            y += 4;
         }

         if(inToday && !hud.showToday) continue;
         if(inHistorical && !hud.showHist) continue;
         if(!inToday && !inHistorical && !hud.showRT) continue;

         color c = clrLightBlue;
         int fontSize = hud.fontRT;

         if(inToday)
         {
            fontSize = hud.fontToday;
            if(StringFind(divLines[i], "[SELL]") >= 0)
               c = clrLightCoral;
            else if(StringFind(divLines[i], "[BUY]") >= 0)
               c = clrMediumAquamarine;
            else if(StringFind(divLines[i], "TODAY") >= 0)
               c = clrOrange;
            else
               c = clrDarkGray;
         }
         else if(inHistorical)
         {
            fontSize = hud.fontHist;
            if(StringFind(divLines[i], "Bear") >= 0)
               c = clrPaleVioletRed;
            else if(StringFind(divLines[i], "Bull") >= 0)
               c = clrDarkSeaGreen;
            else
               c = clrGold;
         }
         else
         {
            fontSize = hud.fontRT;
            if(StringFind(divLines[i], "SELL") >= 0)
               c = clrDeepPink;
            else if(StringFind(divLines[i], "BUY") >= 0)
               c = clrMediumSpringGreen;
            else if(StringFind(divLines[i], "---") >= 0)
               c = clrCadetBlue;
         }

         UpdMsg("DIV_" + IntegerToString(validLineCount), divLines[i], hud.startX, y, c, fontSize);
         y += (inToday || inHistorical) ? rowH - 4 : rowH;
         validLineCount++;
      }
      Cleanup(validLineCount);
   }
   else
   {
      Cleanup(0);
   }
}

void UpdMsg(string n, string t, int x, int y, color c, int fontSize = 11, bool isBold = false)
{
   string o = P + n;
   if(ObjectFind(0, o) < 0)
      ObjectCreate(0, o, OBJ_LABEL, 0, 0, 0);
   ObjectSetString(0, o, OBJPROP_FONT, "Consolas");
   ObjectSetInteger(0, o, OBJPROP_FONTSIZE, fontSize);
   ObjectSetInteger(0, o, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, o, OBJPROP_COLOR, c);
   ObjectSetInteger(0, o, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, o, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, o, OBJPROP_TEXT, t);
   ObjectSetInteger(0, o, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
   ObjectSetInteger(0, o, OBJPROP_SELECTABLE, false);
}

void Cleanup(int validLineCount)
{
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i);
      if(StringFind(name, P) != 0) continue;
      if(StringFind(name, P + "HEARTBEAT") == 0) continue;
      if(StringFind(name, P + "FEATURES") == 0) continue;
      if(StringFind(name, P + "LINE_DECOR_") == 0) continue;
      if(StringFind(name, P + "DIV_") == 0)
      {
         int idx_obj = (int)StringToInteger(StringSubstr(name, StringLen(P + "DIV_")));
         if(idx_obj >= validLineCount) ObjectDelete(0, name);
      }
      else ObjectDelete(0, name);
   }
}

void ClearAll()
{
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
   {
      string n = ObjectName(0, i);
      if(StringFind(n, P) == 0)
         ObjectDelete(0, n);
   }
}

//+------------------------------------------------------------------+
//| ONINIT                                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   datetime nowGmt7 = TimeGMT7();
   if(nowGmt7 > expiry)
   {
      MessageBox("Expired!", "EXPIRED", MB_OK | MB_ICONSTOP);
      return INIT_FAILED;
   }

   EventSetTimer(1);
   SyncBrokerGMTOffset(true);
   UpdateTodayBounds();
   g_lastUpdateTime = nowGmt7;
   g_heartbeatCounter = 0;
   g_lastScanDurationMs = 0;
   g_firstScanDone = false;
   g_initTime = TimeCurrent();
   g_statusText = "KHỞI TẠO...";
   g_statusColor = clrDimGray;
   g_lastScanM15 = g_lastScanH1 = 0;
   g_lastRTPrintTime = g_lastRTCount = 0;
   g_isNewsBlocked = false;
   g_lastNewsCheckTime = 0;
   g_lastServerHeartbeatTime = 0;
   g_lastTodaySyncTime = 0;
   g_lastSyncedSymbolsJson = "";
   ArrayResize(g_todaySignals, 0);
   g_todaySignalCount = 0;
   CleanupOldSignals();

   string tfList = "";
   if(EnableM15) tfList += "M15 ";
   if(EnableH1)  tfList += "H1 ";
   if(tfList == "") tfList = "NONE!";

   Print("═══════════════════════════════════════════");
   Print("RSI Divergence Realtime v1.13 (TimeGMT7 + TF Sync)");
   Print("   Timeframes: ", tfList);
   Print("   GMT+7 Now: ", TimeToString(nowGmt7, TIME_DATE|TIME_SECONDS));
   Print("   Direct EA GV: ", PublishToDirectEA_GV ? "ON" : "OFF");
   Print("   Server Sync: ", EnableServerSync ? "ON" : "OFF");
   Print("═══════════════════════════════════════════");

   g_statusText = "RUNNING";
   g_statusColor = clrTeal;
   SyncHeartbeatToServer("online", "Initialized: " + tfList, true);

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| ONTIMER                                                           |
//+------------------------------------------------------------------+
void PublishScanSymbols(string symbols)
{
   int h = FileOpen("DIVSCAN_SYMBOLS.txt", FILE_WRITE | FILE_TXT | FILE_COMMON);
   if(h == INVALID_HANDLE) return;
   FileWriteString(h, IntegerToString((int)TimeGMT7()) + "#" + symbols);
   FileClose(h);
   SyncScansToServer(symbols);
}

void OnTimer()
{
   datetime nowGmt7 = TimeGMT7();
   GlobalVariableSet("HB_RSI_Indicator", (double)nowGmt7);
   g_lastUpdateTime = nowGmt7;
   g_heartbeatCounter++;
   if(g_heartbeatCounter % 2 == 0) g_heartbeatState = !g_heartbeatState;

   SyncBrokerGMTOffset(false);
   ClearOldTodaySignals();
   UpdateTodayBounds();

   if(IsNewsFilterEnabledEffective() && nowGmt7 - g_lastNewsCheckTime >= NewsCheckIntervalSeconds)
   {
      g_isNewsBlocked = !IsNewsSafe();
      g_lastNewsCheckTime = nowGmt7;
   }
   else if(!IsNewsFilterEnabledEffective())
   {
      g_isNewsBlocked = false;
   }

   if(g_isNewsBlocked)
   {
      PublishScanSymbols("");
      g_statusColor = clrOrangeRed;
      g_statusText = "NEWS BLOCKED";
      SyncHeartbeatToServer("warning", "News filter active", false);
      Draw("NEWS BLOCKED");
      return;
   }

   bool isScanTime = IsInScanTime();
   bool has_crash = false;

   if(g_firstScanDone)
   {
      if(ArraySize(arrSymTF) == 0)
         has_crash = true;
      else
      {
         bool allInvalid = true;
         for(int i = 0; i < ArraySize(arrSymTF); i++)
            if(arrSymTF[i].rsiHandle != INVALID_HANDLE)
            { allInvalid = false; break; }
         if(allInvalid) has_crash = true;
         if(!has_crash && !TerminalInfoInteger(TERMINAL_CONNECTED))
            has_crash = true;
      }
   }

   if(has_crash)
   {
      PublishScanSymbols("");
      g_statusColor = clrRed;
      g_statusText = "CRASHED!";
      SyncHeartbeatToServer("offline", "Indicator or terminal disconnected", false);
      Draw("CRASHED");
      return;
   }

   if(!isScanTime)
   {
      PublishScanSymbols("");
      g_statusColor = clrGray;
      g_statusText = "STANDBY";
      SyncHeartbeatToServer("online", "Standby outside scan hours", false);
      Draw("STANDBY");
      return;
   }

   g_statusColor = clrAquamarine;
   g_statusText = "RUNNING";
   CleanupOldSignals();

   datetime now = TimeCurrent();
   bool sM15 = false, sH1 = false;
   if(EnableM15 && now - g_lastScanM15 >= 2) { sM15 = true; g_lastScanM15 = now; }
   if(EnableH1  && now - g_lastScanH1  >= 5) { sH1 = true;  g_lastScanH1 = now; }

   if(!sM15 && !sH1)
   {
      string td = BuildTodaySignalsDisplay();
      string dm = g_latestRTSignal == "" ? "Scanning..." : g_latestRTSignal;
      SyncHeartbeatToServer("online", StringFormat("Idle tick | symTF=%d", ArraySize(arrSymTF)), false);
      Draw(dm + td);
      return;
   }

   uint scanStartMs = GetTickCount();
   SRTLine rtLines[];
   int rtCount = 0;
   string histLines[];
   datetime histTimes[];
   int histCount = 0;

   string scannedSymbols = "";
   int totalSymbols = SymbolsTotal(true);
   for(int s = 0; s < totalSymbols; s++)
   {
      string sym = SymbolName(s, true);
      if(IsSymbolExcluded(sym)) continue;

      if(EnableM15)
      {
         int idx = GetSymbolTFIndex(sym, PERIOD_M15);
         if(idx == -1) idx = InitSymTF(sym, PERIOD_M15, "M15");
         if(sM15)
         {
            ProcessSymbolTF(idx, rtLines, rtCount, histLines, histTimes, histCount);
            ClearInactiveRTDivergence(sym, "M15", PERIOD_M15);
         }
         if(scannedSymbols != "") scannedSymbols += ";";
         scannedSymbols += sym + "|M15";
      }

      if(EnableH1)
      {
         int idx = GetSymbolTFIndex(sym, PERIOD_H1);
         if(idx == -1) idx = InitSymTF(sym, PERIOD_H1, "H1");
         if(sH1)
         {
            ProcessSymbolTF(idx, rtLines, rtCount, histLines, histTimes, histCount);
            ClearInactiveRTDivergence(sym, "H1", PERIOD_H1);
         }
         if(scannedSymbols != "") scannedSymbols += ";";
         scannedSymbols += sym + "|H1";
      }
   }
   PublishScanSymbols(scannedSymbols);
   g_lastScanDurationMs = GetTickCount() - scanStartMs;
   SyncHeartbeatToServer(
      "online",
      StringFormat("Scan %ums | RT=%d | Today=%d", (int)g_lastScanDurationMs, rtCount, g_todaySignalCount),
      false
   );

   string dc = "";
   static string slr = "";

   if(rtCount > 0)
   {
      for(int i = 0; i < rtCount - 1; i++)
         for(int j = i + 1; j < rtCount; j++)
            if(rtLines[i].time > rtLines[j].time)
            {
               SRTLine t = rtLines[i];
               rtLines[i] = rtLines[j];
               rtLines[j] = t;
            }
      string gc = "--- RT SIGNALS (1/Bar) ---\n\n";
      for(int i = 0; i < rtCount; i++)
         gc += rtLines[i].text + "\n";
      g_latestRTSignal = gc;
      dc = gc;
      if(gc != slr)
      {
         PrintRTSignalChange(rtCount, gc);
         slr = gc;
      }
   }
   else
   {
      g_latestRTSignal = "";
      dc = "Scanning...";
      slr = "";
      g_lastRTCount = 0;
   }

   if(histCount > 0)
   {
      for(int i = 0; i < histCount - 1; i++)
         for(int j = i + 1; j < histCount; j++)
            if(histTimes[i] < histTimes[j])
            {
               datetime t = histTimes[i];
               histTimes[i] = histTimes[j];
               histTimes[j] = t;
               string tl = histLines[i];
               histLines[i] = histLines[j];
               histLines[j] = tl;
            }
      for(int i = 0; i < histCount; i++)
         dc += "\n" + histLines[i];
   }

   string td = BuildTodaySignalsDisplay();
   dc += td;
   Draw(dc);
   g_firstScanDone = true;
}

//+------------------------------------------------------------------+
//| ONDEINIT                                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   SyncHeartbeatToServer("offline", "Indicator removed/deinit: " + IntegerToString(reason), true);
   EventKillTimer();
   ClearAll();

   ENUM_TIMEFRAMES tfs[];
   string tfStrs[];
   GetEnabledTimeframes(tfs, tfStrs);
   for(int i = 0; i < ArraySize(arrSymTF); i++)
      ClearAllDivergenceData(arrSymTF[i].sym, arrSymTF[i].tfStr);
   for(int s = 0; s < SymbolsTotal(true); s++)
   {
      string sym = SymbolName(s, true);
      for(int t = 0; t < ArraySize(tfs); t++)
         ClearAllDivergenceData(sym, tfStrs[t]);
   }

   for(int i = 0; i < ArraySize(arrSymTF); i++)
   {
      if(arrSymTF[i].rsiHandle != INVALID_HANDLE)
         IndicatorRelease(arrSymTF[i].rsiHandle);
      if(arrSymTF[i].atrHandle != INVALID_HANDLE)
         IndicatorRelease(arrSymTF[i].atrHandle);
   }

   CleanupOldSignals();
}

//+------------------------------------------------------------------+
//| PROCESS SYMBOL TF                                                |
//+------------------------------------------------------------------+
void ProcessSymbolTF(int idx, SRTLine &rtLines[], int &rtCount, string &histLines[], datetime &histTimes[], int &histCount)
{
   string sym = arrSymTF[idx].sym;
   ENUM_TIMEFRAMES tf = arrSymTF[idx].tf;
   string tfStr = arrSymTF[idx].tfStr;
   int rsiHandle = arrSymTF[idx].rsiHandle;

   if(rsiHandle == INVALID_HANDLE) return;

   int readCount = MS_BARS_DEFAULT + 50;
   if(CopyBuffer(rsiHandle, 0, 0, readCount, arrSymTF[idx].rsiBuf) < readCount) return;

   datetime currentBarTime = iTime(sym, tf, 0);
   if(currentBarTime != arrSymTF[idx].lastBarTime)
   {
      if(arrSymTF[idx].lastBarTime != 0 && arrSymTF[idx].lastShiftCached < MS_SwingBars)
         arrSymTF[idx].lastShiftCached = MS_SwingBars;
      arrSymTF[idx].lastBarTime = currentBarTime;

      // Sang nến mới mà nến trước chưa reject -> bỏ cờ chờ của nến cũ
      arrSymTF[idx].pendingBearRegStrong = false;
      arrSymTF[idx].pendingBearHidStrong = false;
      arrSymTF[idx].pendingBullRegStrong = false;
      arrSymTF[idx].pendingBullHidStrong = false;
   }

   double bestBearH1 = 0, bestBearH2 = 0, bestBearR1 = 0, bestBearR2 = 0;
   datetime bestBearT1 = 0, bestBearT2 = 0;
   bool bestBearIsReg = false;
   double bestBearVol = 0;

   double bestBullP1 = 0, bestBullP2 = 0, bestBullR1 = 0, bestBullR2 = 0;
   datetime bestBullT1 = 0, bestBullT2 = 0;
   bool bestBullIsReg = false;
   double bestBullVol = 0;

   int scanLimit = MathMax(arrSymTF[idx].lastShiftCached, MS_SwingBars);

   double prevHighPrice = 0, lastHighPrice = 0, prevHighRSI = 0, lastHighRSI = 0;
   datetime prevHighTime = 0, lastHighTime = 0;
   double prevLowPrice = 0, lastLowPrice = 0, prevLowRSI = 0, lastLowRSI = 0;
   datetime prevLowTime = 0, lastLowTime = 0;
   int lastHighShift = -9999, lastLowShift = -9999;

   for(int shift = scanLimit; shift >= MS_SwingBars; shift--)
   {
      if(IsRSISwingHigh(arrSymTF[idx].rsiBuf, shift))
      {
         if(MathAbs(shift - lastHighShift) >= MS_SwingBars)
         {
            datetime t = iTime(sym, tf, shift);
            double rsiVal = GetRSI(arrSymTF[idx].rsiBuf, shift);

            int shiftRight = MathMax(0, shift - 1);
            int shiftLeft  = shift + 1;
            double h = iHigh(sym, tf, shift);
            h = MathMax(h, iHigh(sym, tf, shiftLeft));
            h = MathMax(h, iHigh(sym, tf, shiftRight));

            lastHighShift = shift;
            prevHighPrice = lastHighPrice; prevHighRSI = lastHighRSI; prevHighTime = lastHighTime;
            lastHighPrice = h; lastHighRSI = rsiVal; lastHighTime = t;

            if(prevHighPrice > 0 && lastHighPrice > 0)
            {
               double h1 = prevHighPrice, h2 = lastHighPrice;
               double r1 = prevHighRSI, r2 = lastHighRSI;
               datetime t1 = prevHighTime, t2 = lastHighTime;
               bool regBearDiv = (h2 > h1) && (r2 < r1);
               bool hidBearDiv = (h2 < h1) && (r2 > r1);
               bool bearDiv = regBearDiv || hidBearDiv;
               int divShift = iBarShift(sym, tf, t2, false);

               if(bearDiv)
               {
                  bool highestValid = true;
                  int shift1 = iBarShift(sym, tf, t1, false);
                  double maxAllowed = MathMax(h1, h2);
                  for(int i = divShift + 1; i < shift1; i++)
                     if(iHigh(sym, tf, i) > maxAllowed) { highestValid = false; break; }

                  if(highestValid)
                  {
                     double peakVolRatio = GetVolumeRatioAtShift(sym, divShift, tf);
                     if(peakVolRatio >= VolumeAutoRatio)
                     {
                        if(t2 > bestBearT2 || bestBearT2 == 0)
                        {
                           bestBearH1 = h1; bestBearH2 = h2;
                           bestBearR1 = r1; bestBearR2 = r2;
                           bestBearT1 = t1; bestBearT2 = t2;
                           bestBearIsReg = regBearDiv;
                           bestBearVol = peakVolRatio;
                        }
                     }
                  }
               }
            }
         }
      }

      if(IsRSISwingLow(arrSymTF[idx].rsiBuf, shift))
      {
         if(MathAbs(shift - lastLowShift) >= MS_SwingBars)
         {
            datetime t = iTime(sym, tf, shift);
            double rsiVal = GetRSI(arrSymTF[idx].rsiBuf, shift);

            int shiftRight = MathMax(0, shift - 1);
            int shiftLeft  = shift + 1;
            double l = iLow(sym, tf, shift);
            l = MathMin(l, iLow(sym, tf, shiftLeft));
            l = MathMin(l, iLow(sym, tf, shiftRight));

            lastLowShift = shift;
            prevLowPrice = lastLowPrice; prevLowRSI = lastLowRSI; prevLowTime = lastLowTime;
            lastLowPrice = l; lastLowRSI = rsiVal; lastLowTime = t;

            if(prevLowPrice > 0 && lastLowPrice > 0)
            {
               double p1 = prevLowPrice, p2 = lastLowPrice;
               double r1 = prevLowRSI, r2 = lastLowRSI;
               datetime t1 = prevLowTime, t2 = lastLowTime;
               bool regBullDiv = (p2 < p1) && (r2 > r1);
               bool hidBullDiv = (p2 > p1) && (r2 < r1);
               bool bullDiv = regBullDiv || hidBullDiv;
               int divShift = iBarShift(sym, tf, t2, false);

               if(bullDiv)
               {
                  bool lowestValid = true;
                  int shift1 = iBarShift(sym, tf, t1, false);
                  double minAllowed = MathMin(p1, p2);
                  for(int i = divShift + 1; i < shift1; i++)
                     if(iLow(sym, tf, i) < minAllowed) { lowestValid = false; break; }

                  if(lowestValid)
                  {
                     double troughVolRatio = GetVolumeRatioAtShift(sym, divShift, tf);
                     if(troughVolRatio >= VolumeAutoRatio)
                     {
                        if(t2 > bestBullT2 || bestBullT2 == 0)
                        {
                           bestBullP1 = p1; bestBullP2 = p2;
                           bestBullR1 = r1; bestBullR2 = r2;
                           bestBullT1 = t1; bestBullT2 = t2;
                           bestBullIsReg = regBullDiv;
                           bestBullVol = troughVolRatio;
                        }
                     }
                  }
               }
            }
         }
      }
   }

   datetime gmt7Now = TimeGMT7();
   datetime todayStartGMT7 = gmt7Now - (gmt7Now % 86400);

   if(bestBearT2 > 0)
   {
      datetime gmt7Time = ServerTimeToGMT7(bestBearT2);
      string timeStr = TimeToString(gmt7Time, TIME_DATE | TIME_MINUTES);
      string divShort = bestBearIsReg ? "Reg" : "Hid";
      arrSymTF[idx].histBearDisplay = "Bear [" + divShort + "] (" + timeStr + ")";
      arrSymTF[idx].histBearTime = bestBearT2;

      WriteHistoricalDivergence(sym, tfStr, true, bestBearIsReg,
                                bestBearT1, bestBearR1, bestBearH1,
                                bestBearT2, bestBearR2, bestBearH2, bestBearVol);

      ArrayResize(histLines, histCount + 1);
      ArrayResize(histTimes, histCount + 1);
      histLines[histCount] = "[" + tfStr + "] " + sym + ": " + arrSymTF[idx].histBearDisplay;
      histTimes[histCount] = bestBearT2;
      histCount++;

      if(gmt7Time >= todayStartGMT7)
         AddTodaySignal(sym, tfStr, "BEAR", divShort, "HIST", bestBearT1, bestBearT2, bestBearH1, bestBearH2, bestBearR1, bestBearR2);
   }

   if(bestBullT2 > 0)
   {
      datetime gmt7Time = ServerTimeToGMT7(bestBullT2);
      string timeStr = TimeToString(gmt7Time, TIME_DATE | TIME_MINUTES);
      string divShort = bestBullIsReg ? "Reg" : "Hid";
      arrSymTF[idx].histBullDisplay = "Bull [" + divShort + "] (" + timeStr + ")";
      arrSymTF[idx].histBullTime = bestBullT2;

      WriteHistoricalDivergence(sym, tfStr, false, bestBullIsReg,
                                bestBullT1, bestBullR1, bestBullP1,
                                bestBullT2, bestBullR2, bestBullP2, bestBullVol);

      ArrayResize(histLines, histCount + 1);
      ArrayResize(histTimes, histCount + 1);
      histLines[histCount] = "[" + tfStr + "] " + sym + ": " + arrSymTF[idx].histBullDisplay;
      histTimes[histCount] = bestBullT2;
      histCount++;

      if(gmt7Time >= todayStartGMT7)
         AddTodaySignal(sym, tfStr, "BULL", divShort, "HIST", bestBullT1, bestBullT2, bestBullP1, bestBullP2, bestBullR1, bestBullR2);
   }

   arrSymTF[idx].lastHighShift = lastHighShift;
   arrSymTF[idx].lastLowShift = lastLowShift;
   arrSymTF[idx].prevHighPrice = prevHighPrice; arrSymTF[idx].lastHighPrice = lastHighPrice;
   arrSymTF[idx].prevHighRSI = prevHighRSI; arrSymTF[idx].lastHighRSI = lastHighRSI;
   arrSymTF[idx].prevHighTime = prevHighTime; arrSymTF[idx].lastHighTime = lastHighTime;
   arrSymTF[idx].prevLowPrice = prevLowPrice; arrSymTF[idx].lastLowPrice = lastLowPrice;
   arrSymTF[idx].prevLowRSI = prevLowRSI; arrSymTF[idx].lastLowRSI = lastLowRSI;
   arrSymTF[idx].prevLowTime = prevLowTime; arrSymTF[idx].lastLowTime = lastLowTime;
   arrSymTF[idx].lastShiftCached = MS_SwingBars - 1;

   datetime currentBarTimeForTrack = iTime(sym, tf, 0);
   double spreadVal = GetCurrentSpreadPrice(sym);

   // REALTIME BEARISH
   if(lastHighPrice > 0 && lastHighShift >= 0)
   {
      int startBear = MathMax(0, lastHighShift - 1);

      double rtMaxHigh = -DBL_MAX, rsiAtMaxHigh = 0;
      datetime timeAtMaxHigh = 0;
      int rtMaxHighShift = -1;
      for(int i = startBear; i >= 0; i--)
      {
         double curH = iHigh(sym, tf, i);
         if(curH > rtMaxHigh) { rtMaxHigh = curH; rsiAtMaxHigh = GetRSI(arrSymTF[idx].rsiBuf, i); timeAtMaxHigh = iTime(sym, tf, i); rtMaxHighShift = i; }
      }

      double rtMaxRSI = -DBL_MAX, priceAtMaxRSI = 0;
      datetime timeAtMaxRSI = 0;
      int rtMaxRSIShift = -1;
      for(int i = startBear; i >= 0; i--)
      {
         double curR = GetRSI(arrSymTF[idx].rsiBuf, i);
         if(curR > rtMaxRSI) { rtMaxRSI = curR; priceAtMaxRSI = iHigh(sym, tf, i); timeAtMaxRSI = iTime(sym, tf, i); rtMaxRSIShift = i; }
      }

      if(rtMaxHigh > lastHighPrice && rsiAtMaxHigh < lastHighRSI && rtMaxHighShift != lastHighShift)
      {
         if(!RT_RequireMonotonicMove || IsMonotonicStructure(sym, tf, rtMaxHighShift, lastHighShift, true))
         {
            if(CheckMiddleValid(sym, tf, rtMaxHighShift, lastHighShift, lastHighTime, true))
            {
               double peakVolRatio = GetVolumeRatioAtShift(sym, rtMaxHighShift, tf);
               if(peakVolRatio >= VolumeAutoRatio)
               {
                  datetime gmt7Time = ServerTimeToGMT7(timeAtMaxHigh);
                  string timeStr = TimeToString(gmt7Time, TIME_DATE | TIME_MINUTES);

                  double closeNew = iClose(sym, tf, rtMaxHighShift);
                  double closeOld = iClose(sym, tf, iBarShift(sym, tf, lastHighTime, false));
                  if(closeNew > closeOld)
                     arrSymTF[idx].pendingBearRegStrong = true;

                  double currentPrice = SymbolInfoDouble(sym, SYMBOL_BID);
                  bool rejectNow = (rtMaxHighShift == 0) && (currentPrice < lastHighPrice);
                  bool isStrong = arrSymTF[idx].pendingBearRegStrong && rejectNow;
                  if(isStrong)
                     arrSymTF[idx].pendingBearRegStrong = false;

                  double slSellReg = rtMaxHigh + spreadVal;

                  bool shouldTrack = ShouldRetrack(idx, currentPrice, arrSymTF[idx].lastBearRegRTPrice, sym, tf, currentBarTimeForTrack, arrSymTF[idx].lastBearRegRTTime);
                  if(shouldTrack && !IsInTradeTime(timeAtMaxHigh)) shouldTrack = false;

                  if(shouldTrack)
                  {
                     arrSymTF[idx].lastBearRegRTPrice = currentPrice;
                     arrSymTF[idx].lastBearRegRTTime = currentBarTimeForTrack;
                  }

                  string strengthTag = isStrong ? " [STRONG]" : " [WEAK]";

                  ArrayResize(rtLines, rtCount + 1);
                  rtLines[rtCount].text = "[SELL-RT] [" + tfStr + "] " + sym + " [Reg] (" + timeStr + ")" + strengthTag + " [Running...]";
                  rtLines[rtCount].time = timeAtMaxHigh;
                  rtLines[rtCount].isToday = (gmt7Time >= todayStartGMT7);
                  rtCount++;

                  WriteRTDivergence(sym, tfStr, true, true, lastHighTime, lastHighRSI, lastHighPrice, timeAtMaxHigh, rsiAtMaxHigh, rtMaxHigh);

                  if(gmt7Time >= todayStartGMT7)
                     AddTodaySignal(sym, tfStr, "BEAR", "Reg", "RT", lastHighTime, timeAtMaxHigh, lastHighPrice, rtMaxHigh, lastHighRSI, rsiAtMaxHigh);

                  if(timeAtMaxHigh >= g_initTime && IsNewsSafe())
                  {
                     string sigKey = sym + "|" + tfStr + "|SELL|Reg|" + IntegerToString(timeAtMaxHigh);
                     EmitRealtimeSignal(idx, sym, tf, tfStr, "SELL", "REG", isStrong, currentPrice, slSellReg, 0.0, lastHighPrice, rtMaxHigh, peakVolRatio, timeAtMaxHigh, sigKey);
                  }

                  if(isStrong && timeAtMaxHigh >= g_initTime && IsNewsSafe() && shouldTrack && IsValidSLForDirection("SELL", currentPrice, slSellReg))
                  {
                     WriteStrongSignalToGV(sym, tfStr, "SELL", currentPrice, slSellReg, slSellReg, lastHighPrice, true, "REG", timeAtMaxHigh);
                     string sigKey = sym + "|" + tfStr + "|SELL|Reg|" + IntegerToString(timeAtMaxHigh);
                     PrintStrongSignalOnce(arrSymTF[idx], sym, tfStr, "SELL", "Reg", currentPrice, slSellReg, timeAtMaxHigh, sigKey);
                  }

                  if(timeAtMaxHigh >= g_initTime && timeAtMaxHigh != arrSymTF[idx].lastBearNotifyBar)
                  {
                     datetime now2 = TimeGMT7();
                     if(EnablePushNotify && now2 - arrSymTF[idx].lastBearNotifyTime >= NotifyCooldownMinutes * 60 && IsNewsSafe())
                     {
                        SendDivergenceNotify(sym, tfStr, "Reg", "SELL", timeStr);
                        arrSymTF[idx].lastBearNotifyTime = now2;
                        arrSymTF[idx].lastBearNotifyBar = timeAtMaxHigh;
                     }
                  }
               }
            }
         }
      }
      else
      {
         arrSymTF[idx].pendingBearRegStrong = false;
      }

      if(rtMaxRSI > lastHighRSI && priceAtMaxRSI < lastHighPrice && rtMaxRSIShift != lastHighShift)
      {
         if(!RT_RequireMonotonicMove || IsMonotonicStructure(sym, tf, rtMaxRSIShift, lastHighShift, true))
         {
            if(CheckMiddleValid(sym, tf, rtMaxRSIShift, lastHighShift, lastHighTime, true))
            {
               double peakVolRatio = GetVolumeRatioAtShift(sym, rtMaxRSIShift, tf);
               if(peakVolRatio >= VolumeAutoRatio)
               {
                  datetime gmt7Time = ServerTimeToGMT7(timeAtMaxRSI);
                  string timeStr = TimeToString(gmt7Time, TIME_DATE | TIME_MINUTES);

                  double closeNew = iClose(sym, tf, rtMaxRSIShift);
                  double closeOld = iClose(sym, tf, iBarShift(sym, tf, lastHighTime, false));
                  if(closeNew < closeOld)
                     arrSymTF[idx].pendingBearHidStrong = true;

                  double slHidBear = MathMax(lastHighPrice, priceAtMaxRSI);
                  double slSellHid = slHidBear + spreadVal;

                  double currentPrice = SymbolInfoDouble(sym, SYMBOL_BID);
                  bool rejectNow = (rtMaxRSIShift == 0) && (currentPrice < slHidBear);
                  bool isStrong = arrSymTF[idx].pendingBearHidStrong && rejectNow;
                  if(isStrong)
                     arrSymTF[idx].pendingBearHidStrong = false;

                  bool shouldTrack = ShouldRetrack(idx, currentPrice, arrSymTF[idx].lastBearHidRTPrice, sym, tf, currentBarTimeForTrack, arrSymTF[idx].lastBearHidRTTime);
                  if(shouldTrack && !IsInTradeTime(timeAtMaxRSI)) shouldTrack = false;

                  if(shouldTrack)
                  {
                     arrSymTF[idx].lastBearHidRTPrice = currentPrice;
                     arrSymTF[idx].lastBearHidRTTime = currentBarTimeForTrack;
                  }

                  string strengthTag = isStrong ? " [STRONG]" : " [WEAK]";

                  ArrayResize(rtLines, rtCount + 1);
                  rtLines[rtCount].text = "[SELL-RT] [" + tfStr + "] " + sym + " [Hid] (" + timeStr + ")" + strengthTag + " [Running...]";
                  rtLines[rtCount].time = timeAtMaxRSI;
                  rtLines[rtCount].isToday = (gmt7Time >= todayStartGMT7);
                  rtCount++;

                  WriteRTDivergence(sym, tfStr, true, false, lastHighTime, lastHighRSI, lastHighPrice, timeAtMaxRSI, rtMaxRSI, priceAtMaxRSI);

                  if(gmt7Time >= todayStartGMT7)
                     AddTodaySignal(sym, tfStr, "BEAR", "Hid", "RT", lastHighTime, timeAtMaxRSI, lastHighPrice, priceAtMaxRSI, lastHighRSI, rtMaxRSI);

                  if(timeAtMaxRSI >= g_initTime && IsNewsSafe())
                  {
                     string sigKey = sym + "|" + tfStr + "|SELL|Hid|" + IntegerToString(timeAtMaxRSI);
                     EmitRealtimeSignal(idx, sym, tf, tfStr, "SELL", "HID", isStrong, currentPrice, slSellHid, 0.0, lastHighPrice, priceAtMaxRSI, peakVolRatio, timeAtMaxRSI, sigKey);
                  }

                  if(isStrong && timeAtMaxRSI >= g_initTime && IsNewsSafe() && shouldTrack)
                  {
                     if(IsValidSLForDirection("SELL", currentPrice, slSellHid))
                     {
                        WriteStrongSignalToGV(sym, tfStr, "SELL", currentPrice, slSellHid, slSellHid, lastHighPrice, true, "HID", timeAtMaxRSI);
                        string sigKey = sym + "|" + tfStr + "|SELL|Hid|" + IntegerToString(timeAtMaxRSI);
                        PrintStrongSignalOnce(arrSymTF[idx], sym, tfStr, "SELL", "Hid", currentPrice, slSellHid, timeAtMaxRSI, sigKey);
                     }
                  }

                  if(timeAtMaxRSI >= g_initTime && timeAtMaxRSI != arrSymTF[idx].lastBearNotifyBar)
                  {
                     datetime now2 = TimeGMT7();
                     if(EnablePushNotify && now2 - arrSymTF[idx].lastBearNotifyTime >= NotifyCooldownMinutes * 60 && IsNewsSafe())
                     {
                        SendDivergenceNotify(sym, tfStr, "Hid", "SELL", timeStr);
                        arrSymTF[idx].lastBearNotifyTime = now2;
                        arrSymTF[idx].lastBearNotifyBar = timeAtMaxRSI;
                     }
                  }
               }
            }
         }
      }
      else
      {
         arrSymTF[idx].pendingBearHidStrong = false;
      }
   }
   else
   {
      arrSymTF[idx].pendingBearRegStrong = false;
      arrSymTF[idx].pendingBearHidStrong = false;
   }

   // REALTIME BULLISH
   if(lastLowPrice > 0 && lastLowShift >= 0)
   {
      int startBull = MathMax(0, lastLowShift - 1);

      double rtMinLow = DBL_MAX, rsiAtMinLow = 0;
      datetime timeAtMinLow = 0;
      int rtMinLowShift = -1;
      for(int i = startBull; i >= 0; i--)
      {
         double curL = iLow(sym, tf, i);
         if(curL < rtMinLow) { rtMinLow = curL; rsiAtMinLow = GetRSI(arrSymTF[idx].rsiBuf, i); timeAtMinLow = iTime(sym, tf, i); rtMinLowShift = i; }
      }

      double rtMinRSI = DBL_MAX, priceAtMinRSI = 0;
      datetime timeAtMinRSI = 0;
      int rtMinRSIShift = -1;
      for(int i = startBull; i >= 0; i--)
      {
         double curR = GetRSI(arrSymTF[idx].rsiBuf, i);
         if(curR < rtMinRSI) { rtMinRSI = curR; priceAtMinRSI = iLow(sym, tf, i); timeAtMinRSI = iTime(sym, tf, i); rtMinRSIShift = i; }
      }

      if(rtMinLow < lastLowPrice && rsiAtMinLow > lastLowRSI && rtMinLowShift != lastLowShift)
      {
         if(!RT_RequireMonotonicMove || IsMonotonicStructure(sym, tf, rtMinLowShift, lastLowShift, false))
         {
            if(CheckMiddleValid(sym, tf, rtMinLowShift, lastLowShift, lastLowTime, false))
            {
               double troughVolRatio = GetVolumeRatioAtShift(sym, rtMinLowShift, tf);
               if(troughVolRatio >= VolumeAutoRatio)
               {
                  datetime gmt7Time = ServerTimeToGMT7(timeAtMinLow);
                  string timeStr = TimeToString(gmt7Time, TIME_DATE | TIME_MINUTES);

                  double closeNew = iClose(sym, tf, rtMinLowShift);
                  double closeOld = iClose(sym, tf, iBarShift(sym, tf, lastLowTime, false));
                  if(closeNew < closeOld)
                     arrSymTF[idx].pendingBullRegStrong = true;

                  double currentPrice = SymbolInfoDouble(sym, SYMBOL_ASK);
                  bool rejectNow = (rtMinLowShift == 0) && (currentPrice > lastLowPrice);
                  bool isStrong = arrSymTF[idx].pendingBullRegStrong && rejectNow;
                  if(isStrong)
                     arrSymTF[idx].pendingBullRegStrong = false;

                  bool shouldTrack = ShouldRetrack(idx, currentPrice, arrSymTF[idx].lastBullRegRTPrice, sym, tf, currentBarTimeForTrack, arrSymTF[idx].lastBullRegRTTime);
                  if(shouldTrack && !IsInTradeTime(timeAtMinLow)) shouldTrack = false;

                  if(shouldTrack)
                  {
                     arrSymTF[idx].lastBullRegRTPrice = currentPrice;
                     arrSymTF[idx].lastBullRegRTTime = currentBarTimeForTrack;
                  }

                  string strengthTag = isStrong ? " [STRONG]" : " [WEAK]";

                  ArrayResize(rtLines, rtCount + 1);
                  rtLines[rtCount].text = "[BUY-RT] [" + tfStr + "] " + sym + " [Reg] (" + timeStr + ")" + strengthTag + " [Running...]";
                  rtLines[rtCount].time = timeAtMinLow;
                  rtLines[rtCount].isToday = (gmt7Time >= todayStartGMT7);
                  rtCount++;

                  WriteRTDivergence(sym, tfStr, false, true, lastLowTime, lastLowRSI, lastLowPrice, timeAtMinLow, rsiAtMinLow, rtMinLow);

                  if(gmt7Time >= todayStartGMT7)
                     AddTodaySignal(sym, tfStr, "BULL", "Reg", "RT", lastLowTime, timeAtMinLow, lastLowPrice, rtMinLow, lastLowRSI, rsiAtMinLow);

                  if(timeAtMinLow >= g_initTime && IsNewsSafe())
                  {
                     string sigKey = sym + "|" + tfStr + "|BUY|Reg|" + IntegerToString(timeAtMinLow);
                     EmitRealtimeSignal(idx, sym, tf, tfStr, "BUY", "REG", isStrong, currentPrice, rtMinLow, 0.0, lastLowPrice, rtMinLow, troughVolRatio, timeAtMinLow, sigKey);
                  }

                  if(isStrong && timeAtMinLow >= g_initTime && IsNewsSafe() && shouldTrack && IsValidSLForDirection("BUY", currentPrice, rtMinLow))
                  {
                     WriteStrongSignalToGV(sym, tfStr, "BUY", currentPrice, rtMinLow, lastHighPrice, rtMinLow, true, "REG", timeAtMinLow);
                     string sigKey = sym + "|" + tfStr + "|BUY|Reg|" + IntegerToString(timeAtMinLow);
                     PrintStrongSignalOnce(arrSymTF[idx], sym, tfStr, "BUY", "Reg", currentPrice, rtMinLow, timeAtMinLow, sigKey);
                  }

                  if(timeAtMinLow >= g_initTime && timeAtMinLow != arrSymTF[idx].lastBullNotifyBar)
                  {
                     datetime now2 = TimeGMT7();
                     if(EnablePushNotify && now2 - arrSymTF[idx].lastBullNotifyTime >= NotifyCooldownMinutes * 60 && IsNewsSafe())
                     {
                        SendDivergenceNotify(sym, tfStr, "Reg", "BUY", timeStr);
                        arrSymTF[idx].lastBullNotifyTime = now2;
                        arrSymTF[idx].lastBullNotifyBar = timeAtMinLow;
                     }
                  }
               }
            }
         }
      }
      else
      {
         arrSymTF[idx].pendingBullRegStrong = false;
      }

      if(rtMinRSI < lastLowRSI && priceAtMinRSI > lastLowPrice && rtMinRSIShift != lastLowShift)
      {
         if(!RT_RequireMonotonicMove || IsMonotonicStructure(sym, tf, rtMinRSIShift, lastLowShift, false))
         {
            if(CheckMiddleValid(sym, tf, rtMinRSIShift, lastLowShift, lastLowTime, false))
            {
               double troughVolRatio = GetVolumeRatioAtShift(sym, rtMinRSIShift, tf);
               if(troughVolRatio >= VolumeAutoRatio)
               {
                  datetime gmt7Time = ServerTimeToGMT7(timeAtMinRSI);
                  string timeStr = TimeToString(gmt7Time, TIME_DATE | TIME_MINUTES);

                  double closeNew = iClose(sym, tf, rtMinRSIShift);
                  double closeOld = iClose(sym, tf, iBarShift(sym, tf, lastLowTime, false));
                  if(closeNew > closeOld)
                     arrSymTF[idx].pendingBullHidStrong = true;

                  double slHidBull = MathMin(lastLowPrice, priceAtMinRSI);

                  double currentPrice = SymbolInfoDouble(sym, SYMBOL_ASK);
                  bool rejectNow = (rtMinRSIShift == 0) && (currentPrice > slHidBull);
                  bool isStrong = arrSymTF[idx].pendingBullHidStrong && rejectNow;
                  if(isStrong)
                     arrSymTF[idx].pendingBullHidStrong = false;

                  bool shouldTrack = ShouldRetrack(idx, currentPrice, arrSymTF[idx].lastBullHidRTPrice, sym, tf, currentBarTimeForTrack, arrSymTF[idx].lastBullHidRTTime);
                  if(shouldTrack && !IsInTradeTime(timeAtMinRSI)) shouldTrack = false;

                  if(shouldTrack)
                  {
                     arrSymTF[idx].lastBullHidRTPrice = currentPrice;
                     arrSymTF[idx].lastBullHidRTTime = currentBarTimeForTrack;
                  }

                  string strengthTag = isStrong ? " [STRONG]" : " [WEAK]";

                  ArrayResize(rtLines, rtCount + 1);
                  rtLines[rtCount].text = "[BUY-RT] [" + tfStr + "] " + sym + " [Hid] (" + timeStr + ")" + strengthTag + " [Running...]";
                  rtLines[rtCount].time = timeAtMinRSI;
                  rtLines[rtCount].isToday = (gmt7Time >= todayStartGMT7);
                  rtCount++;

                  WriteRTDivergence(sym, tfStr, false, false, lastLowTime, lastLowRSI, lastLowPrice, timeAtMinRSI, rtMinRSI, priceAtMinRSI);

                  if(gmt7Time >= todayStartGMT7)
                     AddTodaySignal(sym, tfStr, "BULL", "Hid", "RT", lastLowTime, timeAtMinRSI, lastLowPrice, priceAtMinRSI, lastLowRSI, rtMinRSI);

                  if(timeAtMinRSI >= g_initTime && IsNewsSafe())
                  {
                     string sigKey = sym + "|" + tfStr + "|BUY|Hid|" + IntegerToString(timeAtMinRSI);
                     EmitRealtimeSignal(idx, sym, tf, tfStr, "BUY", "HID", isStrong, currentPrice, slHidBull, 0.0, lastLowPrice, priceAtMinRSI, troughVolRatio, timeAtMinRSI, sigKey);
                  }

                  if(isStrong && timeAtMinRSI >= g_initTime && IsNewsSafe() && shouldTrack)
                  {
                     if(IsValidSLForDirection("BUY", currentPrice, slHidBull))
                     {
                        WriteStrongSignalToGV(sym, tfStr, "BUY", currentPrice, slHidBull, lastHighPrice, lastLowPrice, true, "HID", timeAtMinRSI);
                        string sigKey = sym + "|" + tfStr + "|BUY|Hid|" + IntegerToString(timeAtMinRSI);
                        PrintStrongSignalOnce(arrSymTF[idx], sym, tfStr, "BUY", "Hid", currentPrice, slHidBull, timeAtMinRSI, sigKey);
                     }
                  }

                  if(timeAtMinRSI >= g_initTime && timeAtMinRSI != arrSymTF[idx].lastBullNotifyBar)
                  {
                     datetime now2 = TimeGMT7();
                     if(EnablePushNotify && now2 - arrSymTF[idx].lastBullNotifyTime >= NotifyCooldownMinutes * 60 && IsNewsSafe())
                     {
                        SendDivergenceNotify(sym, tfStr, "Hid", "BUY", timeStr);
                        arrSymTF[idx].lastBullNotifyTime = now2;
                        arrSymTF[idx].lastBullNotifyBar = timeAtMinRSI;
                     }
                  }
               }
            }
         }
      }
      else
      {
         arrSymTF[idx].pendingBullHidStrong = false;
      }
   }
   else
   {
      arrSymTF[idx].pendingBullRegStrong = false;
      arrSymTF[idx].pendingBullHidStrong = false;
   }
}

//+------------------------------------------------------------------+
//| ONCALCULATE                                                       |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[], const double &open[], const double &high[], const double &low[], const double &close[], const long &tick_volume[], const long &volume[], const int &spread[])
{
   return rates_total;
}
//+------------------------------------------------------------------+
