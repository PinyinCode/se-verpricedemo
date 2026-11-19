//+------------------------------------------------------------------+
//|                                                    TradeManager.mq5 |
//|                                  Copyright 2026, MetaQuotes Ltd. |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, MetaQuotes Ltd."
#property link      "https://www.mql5.com"
#property version   "2.34"
#property strict

string ExpiryDate = "2099.12.30"; 
datetime expiry = StringToTime(ExpiryDate); 

#include <Trade/Trade.mqh>

//+------------------------------------------------------------------+
//| ENUMS CHO SYMBOL BUTTONS & SCALE BUTTONS                         |
//+------------------------------------------------------------------+
enum ENUM_SYMBOL_SOURCE
{
   INPUT_ONLY = 0,
   MARKET_WATCH = 1,
   BOTH = 2
};

enum ENUM_PANEL_POSITION
{
   PANEL_TOP_LEFT = 0,
   PANEL_TOP_RIGHT = 1,
   PANEL_BOTTOM_LEFT = 2,
   PANEL_BOTTOM_RIGHT = 3
};

// --- Copy Trade Settings ---
input group "===== 1. Copy Trade =====";
input bool EnableDivergenceAutoTrade = true;
input double DivRiskMoney = 25.0;
input double DivSLMultiplier = 1.0;
input bool EnableCopyTrade = true;
input double LotMultiplier = 1.0;

 int SL_Tolerance_Points = 5; 
 int MagicNumber = 12345;

// --- Drag SL Settings ---
input group "===== 2. Drag SL - Lot Calculator =====";
input bool EnableDragSL = true;
input double risk_amount = 25.0;
input double commission_per_lot = 5.0;
 color sl_line_color = clrDodgerBlue;
 int sl_line_width = 2;
 int sl_line_style = STYLE_DASH;
 int font_size = 10;
 int idle_timeout = 30;
 int DSL_MagicNumber = 12346;
 string DSL_ScriptName = "PlaceDSLTrade";

// --- Divergence Auto Trade Settings ---
input group "===== 4. Auto Trade from Divergence Signal =====";
input double DivMaxSpread = 0.05;
 int DivMagicNumber = 12347;
input int DivCooldownMinutes = 15;
input int DivMaxTotalPositions = 3;
input int DivMaxPerSymbol = 1;
input bool EnablePushNotify = true;
 int RefreshSeconds = 1;

// --- Timeframe Filter & Min SL Settings ---
input group "===== 4b. Divergence Timeframe & Min SL Filter =====";
input bool EnableM15Trades = true;
input bool EnableH1Trades = true;

// --- ATR Minimum SL Settings ---
input bool UseATRForMinSL = true;
input double ATRMultiplierM15 = 0.6;
input double ATRMultiplierH1 = 0.7;
input int ATRPeriod = 14;

input double FallbackSLPipsFX = 20.0;
input double FallbackSLPipsXAU = 3.0;
input double FallbackSLPipsBTC = 100.0;

// --- Risk:Reward & Trailing Settings ---
input group "===== 5. Trailing Stop & BreakEven =====";
input bool EnableRRTP = true;
input double DivRiskRewardRatio = 2;
input bool EnableAutoTrailing = true;
input double TrailingTriggerRR = 1.0;
input double TrailingLockRR = 0.5;
input bool EnableBreakEven = true;
input double BreakEvenTriggerRR = 0.5;
input bool UseStaticTrailing = true;

// --- Trading Time Filter ---
input group "===== 4c. Trading Time Filter (GMT+7) =====";
input bool EnableTradingTimeFilter = true;
input int TradingStartHour = 7;
input int TradingEndHour = 23;

// --- Quick Symbol Switch Settings ---
input group "===== 3. Quick Symbol Switch =====";
input bool ShowSymbolButtons = true;
input ENUM_SYMBOL_SOURCE SymbolSource = INPUT_ONLY;
input string CustomSymbols = "EURUSD,GBPUSD,USDJPY,XAUUSD";
input bool ExcludeIndices = true;
input bool IncludeCurrentSymbol = true;
 int MaxButtons = 10;
 int ButtonWidth = 80;
 int ButtonHeight = 25;
 int ButtonStartX = 90;
 int ButtonStartY = 50;
 color ButtonColor = clrDodgerBlue;
 color ButtonTextColor = clrWhite;
 color ActiveButtonColor = clrGreen;
 color ButtonBorderColor = clrWhite;
 int ButtonFontSize = 8;
 string ButtonFontName = "Arial";
 ENUM_PANEL_POSITION ButtonPosition = PANEL_BOTTOM_RIGHT;

// --- Anti-Zoom Protection Settings ---
input group "===== 6. Anti-Zoom Protection =====";
input bool EnableAntiZoomProtection = true;
input int ZoomDebounceMs = 2000;

// --- History Statistics Settings ---
input group "===== 7. History Statistics =====";
input bool ShowHistoryStats = true;
input int HistoryDays = 1;
input int MaxHistoryRows = 20;

CTrade trade;
CTrade tradeDSL;
CTrade tradeDiv;

string P="DBL_";
string g_zoomMsgPrefix = "ZOOM_";
string DSL_Prefix = "DSL_";
string HIST_Prefix = "HIST_";

int startX = 15;
int topMargin = 20; 

string FileName;

string OpenedCTIDs[];
string FrozenCTIDs[]; 
string ClosedCTIDs[]; 

struct PosRow
{
   ulong ticket;
   string sym;
   double profit;
   double entry;
   double sl;
   double tp;
   double vol;
   datetime time;
   ENUM_POSITION_TYPE type;
   int magic;       
   string comment;  
};

PosRow rows[];

struct HistDeal
{
   ulong ticket;
   string sym;
   double profit;
   double commission;
   double swap;
   double totalFee;
   double netProfit;
   double volume;
   datetime openTime;
   datetime closeTime;
   ENUM_DEAL_TYPE dealType;
   int magic;
   string comment;
};

struct SHistoryStats
{
   double totalNetProfit;
   double totalGrossProfit;
   double totalCommission;
   double totalSwap;
   double totalFee;
   double totalVolume;
   int totalTrades;
   int winCount;
   int lossCount;
   int breakevenCount;
   double totalWin;
   double totalLoss;
   double avgWin;
   double avgLoss;
   double winRate;
   double profitFactor;
};

HistDeal g_historyDeals[];
int g_historyDealCount = 0;
datetime g_lastHistoryUpdate = 0;

SHistoryStats g_cachedStats;
int g_cachedStatsDealCount = -1;
ulong g_lastHistoryDealTicket = 0;
int g_cachedChartHeight = 0;

datetime g_lastUpdateTime = 0;
int g_heartbeatCounter = 0;
bool g_heartbeatState = false;
color g_statusColor = clrDimGray;
string g_statusText = "KHỞI TẠO...";

double equityAtDayStart = 0.0;
string GV_EQUITY_DAYSTART() { return "GV_EQUITY_DAYSTART_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)); }
string GV_EQUITY_DATE()     { return "GV_EQUITY_DATE_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)); }
bool g_waitingNewDayEquity = false;
int g_pendingDate = 0;
datetime g_newDayDetectTime = 0;

double g_SL_Price = 0.0;
bool g_SL_Dragging = false;
bool g_SL_Initialized = false;
datetime g_LastDragTime = 0;
datetime g_LastInteractionTime = 0;
string g_SL_LineName;
string g_SL_LabelName;
int g_LastCalcBars = 0;
double g_LastPrice = 0.0;
double g_LastSLPrice = 0.0;
bool g_SLObjectsCreated = false;
double g_LastLotSize = 0.0;
string g_LastSide = "NONE";

bool g_ClosingAll = false;
bool g_ClosingOne = false;

bool g_PriorityDragSL = false;
datetime g_LastCopyTradeTime = 0;
datetime g_LastBuildRowsTime = 0;
datetime g_LastDrawTime = 0;

double g_DSL_LotSize_Global = 0.0;
string g_DSL_Side_Global = "NONE";
double g_DSL_SLPrice_Global = 0.0;
bool g_DSL_Ready_Global = false;

string g_currentSymbol;
string g_symbolList[];
int g_symbolCount = 0;
string g_buttonPrefix = "BTN_SYM_";
string g_scalePrefix = "BTN_SCALE_";

datetime g_lastDivTradeTime = 0;

bool g_isChartBusy = false;
datetime g_chartBusyStartTime = 0;
int g_zoomEventCounter = 0;
int g_chartBusyCounter = 0;

// =====================================================
// NEWS FILTER INTEGRATION
// =====================================================
string GV_NewsBlocked = "GV_NewsFilter_Blocked";
string GV_NewsRemaining = "GV_NewsFilter_Remain";
string GV_NewsNext = "GV_NewsFilter_Next";
bool g_isNewsBlocked = false;
int g_newsRemainingSeconds = 0;
datetime g_lastNewsCheck = 0;
int g_newsCheckInterval = 5;

struct STrailState
{
   ulong ticket;
   string sym;
   double entryPrice;
   double initialSL;
   double oneR;
   double lockedSL;
   bool breakEvenDone;
   bool trailingActivated;
};
STrailState g_trailStates[];
int g_trailCount = 0;

ulong g_knownTickets[];
int g_knownTicketCount = 0;
bool g_chartSwitchLocked = false;

// =====================================================
// TIMEZONE HELPER
// =====================================================
int g_serverToGMTOffset = 0;
datetime g_lastOffsetCalcTime = 0;

void CalculateServerGMTOffset()
{
   datetime serverTime = TimeTradeServer();
   datetime gmtTime = TimeGMT();
   g_serverToGMTOffset = (int)(serverTime - gmtTime);
   int offsetHours = (int)MathRound((double)g_serverToGMTOffset / 3600.0);
   g_serverToGMTOffset = offsetHours * 3600;
   g_lastOffsetCalcTime = TimeCurrent();
}

datetime ServerTimeToGMT7(datetime serverTime)
{
   // Bước 1: Chuyển server time → GMT (UTC+0)
   datetime gmtTime = serverTime - g_serverToGMTOffset;
   
   // Bước 2: Chuyển GMT → GMT+7
   return gmtTime + 7 * 3600;
}

datetime GetGMT7Now()
{
   return ServerTimeToGMT7(TimeTradeServer());
}

//+------------------------------------------------------------------+
//| HELPER FUNCTIONS                                                  |
//+------------------------------------------------------------------+
int GetR(color c){return(int)(c&0xFF);}
int GetG(color c){return(int)((c>>8)&0xFF);}
int GetB(color c){return(int)((c>>16)&0xFF);}

color BlendColor(color c1,color c2,double t)
{
   int r=(int)(GetR(c1)+(GetR(c2)-GetR(c1))*t);
   int g=(int)(GetG(c1)+(GetG(c2)-GetG(c1))*t);
   int b=(int)(GetB(c1)+(GetB(c2)-GetB(c1))*t);
   r=MathMax(0,MathMin(255,r));
   g=MathMax(0,MathMin(255,g));
   b=MathMax(0,MathMin(255,b));
   return (color)((uchar)r | ((uchar)g<<8) | ((uchar)b<<16));
}

bool IsTimeframeEnabled(string tfStr)
{
   if(tfStr == "M15" && EnableM15Trades) return true;
   if(tfStr == "H1" && EnableH1Trades) return true;
   return false;
}

double GetRiskForTimeframe(string tfStr) { return DivRiskMoney; }
double GetSLMultiplierForTimeframe(string tfStr) { return DivSLMultiplier; }
int GetMaxPositionsForTimeframe(string tfStr) { return DivMaxTotalPositions; }
int GetCooldownForTimeframe(string tfStr) { return DivCooldownMinutes; }

ENUM_TIMEFRAMES GetTimeframeEnum(string tfStr)
{
   if(tfStr == "M15") return PERIOD_M15;
   if(tfStr == "H1")  return PERIOD_H1;
   return PERIOD_M15;
}

double GetATRMultiplierForTimeframe(string tfStr)
{
   if(tfStr == "M15") return ATRMultiplierM15;
   if(tfStr == "H1")  return ATRMultiplierH1;
   return 0.7;
}

string GetAssetType(string symbol)
{
   string upperSymbol = symbol;
   StringToUpper(upperSymbol);
   if(StringFind(upperSymbol, "XAU") >= 0 || StringFind(upperSymbol, "GOLD") >= 0) return "XAU";
   if(StringFind(upperSymbol, "BTC") >= 0 || StringFind(upperSymbol, "ETH") >= 0 || StringFind(upperSymbol, "CRYPTO") >= 0) return "BTC";
   return "FOREX";
}

double GetATRValue(string symbol, ENUM_TIMEFRAMES tf, int period)
{
   if(!SymbolSelect(symbol, true)) { Print("❌ GetATRValue: Symbol ", symbol, " not available"); return -1; }
   int atrHandle = iATR(symbol, tf, period);
   if(atrHandle == INVALID_HANDLE) { Print("❌ GetATRValue: Failed to create ATR handle for ", symbol, " | TF=", EnumToString(tf), " | Error: ", GetLastError()); return -1; }
   double atrBuffer[];
   ArraySetAsSeries(atrBuffer, true);
   if(CopyBuffer(atrHandle, 0, 0, 1, atrBuffer) <= 0) { Print("❌ GetATRValue: Failed to copy ATR buffer for ", symbol, " | TF=", EnumToString(tf), " | Error: ", GetLastError()); IndicatorRelease(atrHandle); return -1; }
   IndicatorRelease(atrHandle);
   double atrValue = atrBuffer[0];
   if(atrValue <= 0 || !MathIsValidNumber(atrValue)) { Print("❌ GetATRValue: Invalid ATR value (", DoubleToString(atrValue, 6), ") for ", symbol, " | TF=", EnumToString(tf)); return -1; }
   return atrValue;
}

double GetFallbackSLDistance(string symbol, string tfStr)
{
   string assetType = GetAssetType(symbol);
   double pipSize = GetPipSizeForSymbol(symbol);
   double basePips;
   if(assetType == "XAU") basePips = FallbackSLPipsXAU;
   else if(assetType == "BTC" || assetType == "CRYPTO") basePips = FallbackSLPipsBTC;
   else basePips = FallbackSLPipsFX;
   double tfMultiplier;
   if(tfStr == "M15") tfMultiplier = 0.7;
   else if(tfStr == "H1")  tfMultiplier = 1.0;
   else                    tfMultiplier = 1.0;
   double fallbackPips = basePips * tfMultiplier;
   double fallbackPrice = fallbackPips * pipSize;
   return fallbackPrice;
}

double GetMinSLDistance(string symbol, string tfStr)
{
   if(!UseATRForMinSL) return 0.0;
   ENUM_TIMEFRAMES atrTf = GetTimeframeEnum(tfStr);
   double atrValue = GetATRValue(symbol, atrTf, ATRPeriod);
   if(atrValue > 0) { double atrMultiplier = GetATRMultiplierForTimeframe(tfStr); double minSL = atrValue * atrMultiplier; return minSL; }
   return GetFallbackSLDistance(symbol, tfStr);
}

bool IsSymbolAllowedForDivTrade(string sym)
{
   string customList = CustomSymbols;
   StringReplace(customList, " ", "");
   string allowedSymbols[];
   int count = StringSplit(customList, ',', allowedSymbols);
   
   for(int i = 0; i < count; i++)
   {
      string upperSym = sym;
      StringToUpper(upperSym);
      string upperAllowed = allowedSymbols[i];
      StringToUpper(upperAllowed);
      
      if(StringFind(upperSym, upperAllowed) >= 0 || StringFind(upperAllowed, upperSym) >= 0)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| KIỂM TRA TRẠNG THÁI NEWS FILTER TỪ INDICATOR                       |
//+------------------------------------------------------------------+
bool IsNewsBlocked()
{
   if(GlobalVariableCheck(GV_NewsBlocked))
   {
      double blocked = GlobalVariableGet(GV_NewsBlocked);
      g_isNewsBlocked = (blocked >= 0.5);
      
      if(GlobalVariableCheck(GV_NewsRemaining))
         g_newsRemainingSeconds = (int)GlobalVariableGet(GV_NewsRemaining);
      else
         g_newsRemainingSeconds = 0;
      
      return g_isNewsBlocked;
   }
   
   g_isNewsBlocked = false;
   g_newsRemainingSeconds = 0;
   return false;
}

void UpdateNewsStatus()
{
   if(TimeCurrent() - g_lastNewsCheck < g_newsCheckInterval) return;
   g_lastNewsCheck = TimeCurrent();
   
   bool wasBlocked = g_isNewsBlocked;
   IsNewsBlocked();
   
   if(wasBlocked != g_isNewsBlocked)
   {
      if(g_isNewsBlocked)
         Print("🔴 [NEWS BLOCK] Trailing/BE disabled - News active for ", g_newsRemainingSeconds, " seconds");
      else
         Print("🟢 [NEWS UNBLOCK] Trailing/BE enabled - News window ended");
   }
}

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      MessageBox("❌ EA chưa được phép giao dịch!\nChuột phải EA → Properties → tick ☑ Allow Algo Trading", "CẢNH BÁO - EA ALGO OFF", MB_OK | MB_ICONSTOP);
      return INIT_FAILED;
   }
   if(TimeCurrent() > expiry) { MessageBox("Expert đã hết hạn!", "CẢNH BÁO - EXPIRED", MB_OK|MB_ICONSTOP); return INIT_FAILED; }
   
   FileName = "CopyTrade_" + IntegerToString(MagicNumber) + ".txt";
   trade.SetExpertMagicNumber(MagicNumber);
   tradeDSL.SetExpertMagicNumber(DSL_MagicNumber);
   tradeDiv.SetExpertMagicNumber(DivMagicNumber);
   
   if(EnableCopyTrade) { LoadFromPositions(); FreezeExistingTickets(); }
   InitDailyTracking();

   if(EnableDragSL)
   {
      g_SL_LineName = DSL_Prefix + "SL_LINE"; 
      g_SL_LabelName = DSL_Prefix + "SL_LABEL";
      g_SL_Price = SymbolInfoDouble(_Symbol, SYMBOL_BID); 
      g_SL_Initialized = false; 
      g_LastDragTime = 0; 
      g_LastInteractionTime = 0; 
      g_SLObjectsCreated = false; 
      g_LastLotSize = 0.0; 
      g_LastSide = "NONE";
      UpdateGlobalVariables(); 
      ChartSetInteger(ChartID(), CHART_EVENT_MOUSE_MOVE, true);
   }

   if(EnableDivergenceAutoTrade)
   {
      ClearDivSignal();
      Print("📡 Divergence Auto Trade ENABLED | Magic=", DivMagicNumber, " | Risk=$", DoubleToString(DivRiskMoney, 2), " | SL Multiplier=x", DoubleToString(DivSLMultiplier, 1), " | MaxPositions=", DivMaxTotalPositions, " | Cooldown=", DivCooldownMinutes, "min");
      Print("📡 Allowed Symbols: ", CustomSymbols); 
      string enabledTFs = ""; 
      if(EnableM15Trades) enabledTFs += "M15 "; 
      if(EnableH1Trades) enabledTFs += "H1 "; 
      Print("📡 Allowed Timeframes: ", enabledTFs == "" ? "NONE" : enabledTFs);
      if(UseATRForMinSL) Print("📡 ATR MinSL: ENABLED | Fallback FX=", DoubleToString(FallbackSLPipsFX, 1), " | XAU=", DoubleToString(FallbackSLPipsXAU, 1), " | BTC=", DoubleToString(FallbackSLPipsBTC, 1), " pips");
      if(EnableAutoTrailing) Print("📡 Trailing: ON | Trigger=", DoubleToString(TrailingTriggerRR, 2), "R | Lock=", DoubleToString(TrailingLockRR, 2), "R | Mode=", UseStaticTrailing ? "Static" : "Dynamic");
      if(EnableBreakEven) Print("📡 BreakEven: ON | Trigger=", DoubleToString(BreakEvenTriggerRR, 2), "R");
   }

   g_currentSymbol = _Symbol;
   if(ShowSymbolButtons) { BuildSymbolList(); if(g_symbolCount > 0) { CreateSymbolButtons(); CreateScaleButtons(); } }

   g_lastUpdateTime = TimeCurrent(); 
   g_statusText = "RUNNING"; 
   g_statusColor = clrTeal;
   g_isChartBusy = false; 
   g_chartBusyStartTime = 0; 
   g_zoomEventCounter = 0; 
   g_chartBusyCounter = 0; 
   g_trailCount = 0; 
   ArrayResize(g_trailStates, 0);
   ArrayResize(g_knownTickets, 0); 
   g_knownTicketCount = 0; 
   g_chartSwitchLocked = false;
   LoadKnownTickets();
   
   g_historyDealCount = 0; 
   ArrayResize(g_historyDeals, 0); 
   g_lastHistoryUpdate = 0;
   g_lastHistoryDealTicket = 0; 
   g_cachedStatsDealCount = -1; 
   UpdateChartHeightCache();

   Print("🛡️ Anti-zoom protection: ", EnableAntiZoomProtection ? "ENABLED" : "DISABLED");
   Print("🔄 Auto chart switch: ENABLED");
   Print("📊 History Stats: ", ShowHistoryStats ? "ENABLED" : "DISABLED");

   EventSetTimer(RefreshSeconds);
   CalculateServerGMTOffset();
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer(); ClearAll();
   if(EnableDragSL) { ObjectsDeleteAll(0, DSL_Prefix); ChartSetInteger(ChartID(), CHART_EVENT_MOUSE_MOVE, false); GlobalVariableDel("DSL_Ready"); GlobalVariableDel("DSL_LotSize"); GlobalVariableDel("DSL_Side"); GlobalVariableDel("DSL_SLPrice"); }
   if(EnableDivergenceAutoTrade) { ClearDivSignal(); }
   if(ShowSymbolButtons) { ObjectsDeleteAll(0, g_buttonPrefix); ObjectsDeleteAll(0, g_scalePrefix); }
   if(ShowHistoryStats) { ObjectsDeleteAll(0, HIST_Prefix); }
   g_cachedChartHeight = 0; g_cachedStatsDealCount = -1; g_lastHistoryDealTicket = 0;
   Print("📊 EA deinitialized. Reason: ", reason);
}

void OnTick() {}

//+------------------------------------------------------------------+
//| Timer function                                                   |
//+------------------------------------------------------------------+
void OnTimer()
{
   GlobalVariableSet("HB_TradeManager", (double)TimeCurrent());
   
   if(TimeCurrent() - g_lastOffsetCalcTime > 3600)
      CalculateServerGMTOffset();
   
   if(g_isChartBusy && EnableAntiZoomProtection)
   {
      int debounceSeconds = ZoomDebounceMs / 1000; if(debounceSeconds < 1) debounceSeconds = 1;
      if(TimeCurrent() - g_chartBusyStartTime >= debounceSeconds) g_isChartBusy = false;
      else { g_lastUpdateTime = TimeCurrent(); g_heartbeatCounter++; if(g_heartbeatCounter % 2 == 0) g_heartbeatState = !g_heartbeatState; g_statusColor = clrOrange; g_statusText = "ZOOMING..."; if(TimeCurrent() - g_LastDrawTime >= 1) { Draw(); g_LastDrawTime = TimeCurrent(); } return; }
   }
   
   g_PriorityDragSL = (g_SL_Dragging || (g_SL_Initialized && g_LastInteractionTime > 0 && TimeCurrent() - g_LastInteractionTime < 2));
   if(g_ClosingAll || g_ClosingOne) { BuildRows(); Draw(); return; }
   
   static datetime s_lastTimerCall = 0;
   g_lastUpdateTime = TimeCurrent(); g_heartbeatCounter++; if(g_heartbeatCounter % 2 == 0) g_heartbeatState = !g_heartbeatState;
   
   bool has_crash = false;
   if(s_lastTimerCall != 0) { int elapsed = (int)(TimeCurrent() - s_lastTimerCall); if(elapsed > RefreshSeconds * 15) has_crash = true; }
   s_lastTimerCall = TimeCurrent();
   if(!has_crash && !TerminalInfoInteger(TERMINAL_CONNECTED)) has_crash = true;
   if(!has_crash && SymbolsTotal(true) <= 0) has_crash = true;
   if(!has_crash && AccountInfoDouble(ACCOUNT_EQUITY) <= 0) has_crash = true;
   if(!has_crash && !SymbolInfoDouble(_Symbol, SYMBOL_BID)) has_crash = true;
   static bool s_copyFileExisted = false;
   if(!has_crash && EnableCopyTrade) { if(FileIsExist(FileName, FILE_COMMON)) s_copyFileExisted = true; }
   
   if(g_PriorityDragSL) { g_statusColor = clrGold; g_statusText = "DRAG SL..."; }
   else { g_statusColor = clrAquamarine; g_statusText = "RUNNING"; }

   if(EnableDragSL && !has_crash) { if(g_SL_Initialized && !g_SL_Dragging && g_LastInteractionTime > 0 && (int)(TimeCurrent() - g_LastInteractionTime) >= idle_timeout) ResetDragSLToDefault(); UpdateDragSL(); UpdateGlobalVariables(); }
   if(EnableCopyTrade && !has_crash) { if(!g_PriorityDragSL || TimeCurrent() - g_LastCopyTradeTime >= 3) { ProcessCopyTrade(); g_LastCopyTradeTime = TimeCurrent(); } }
   if(!has_crash) { if(!g_PriorityDragSL || TimeCurrent() - g_LastBuildRowsTime >= 2) { BuildRows(); g_LastBuildRowsTime = TimeCurrent(); } } else { if(ArraySize(rows) == 0) BuildRows(); }
   if(EnableDivergenceAutoTrade && !has_crash) CheckDivergenceSignal();
   if((EnableAutoTrailing || EnableBreakEven) && !has_crash) ManageTrailing();
   if(!has_crash) CheckNewPositionsAndSwitchChart();
   if(ShowHistoryStats && !has_crash && TimeCurrent() - g_lastHistoryUpdate >= 5) { LoadHistoryDeals(); g_lastHistoryUpdate = TimeCurrent(); }
   
   if(g_cachedChartHeight <= 0) UpdateChartHeightCache();
   if(!g_PriorityDragSL || has_crash || TimeCurrent() - g_LastDrawTime >= 1) { Draw(); g_LastDrawTime = TimeCurrent(); }
   if(ShowSymbolButtons && g_symbolCount > 0) { UpdateSymbolButtons(); UpdateScaleButtons(); }
   if(!has_crash) CheckAndSaveOnNewDay();
}

void UpdateChartHeightCache()
{
   int h = (int)ChartGetInteger(ChartID(), CHART_HEIGHT_IN_PIXELS, 0);
   g_cachedChartHeight = (h > 0) ? h : 600;
}

//+------------------------------------------------------------------+
//| LOAD HISTORY DEALS - CHỈ LẤY LỆNH ĐÓNG HÔM NAY THEO GMT+7        |
//+------------------------------------------------------------------+
void LoadHistoryDeals()
{
   datetime gmt7Now = GetGMT7Now();
   MqlDateTime dtNow;
   TimeToStruct(gmt7Now, dtNow);
   
   // Tính 00:00 hôm nay theo GMT+7
   MqlDateTime dtStart;
   dtStart.year = dtNow.year; 
   dtStart.mon = dtNow.mon; 
   dtStart.day = dtNow.day;
   dtStart.hour = 0; 
   dtStart.min = 0; 
   dtStart.sec = 0;
   datetime dayStartGMT7 = StructToTime(dtStart);
   datetime dayEndGMT7 = dayStartGMT7 + 86400 - 1;
   
   // Chuyển GMT+7 về giờ server để query
   // ServerTime = GMT7 - 7h - serverToGMTOffset
   datetime dayStartServer = dayStartGMT7 - 7 * 3600 - g_serverToGMTOffset;
   datetime dayEndServer = dayEndGMT7 - 7 * 3600 - g_serverToGMTOffset;
   
   HistorySelect(dayStartServer, dayEndServer);
   int totalDeals = HistoryDealsTotal();
   
   // Cache check - nếu không có deal mới thì không cần load lại
   if(totalDeals > 0)
   {
      ulong lastTicket = HistoryDealGetTicket(totalDeals - 1);
      if(lastTicket == g_lastHistoryDealTicket && lastTicket != 0) 
         return;
      g_lastHistoryDealTicket = lastTicket;
   }
   else if(totalDeals == 0 && g_historyDealCount == 0) 
      return;
   
   HistDeal tempDeals[];
   ArrayResize(tempDeals, 0);
   int count = 0;
   int maxRows = MaxHistoryRows > 0 ? MaxHistoryRows : 50;
   
   for(int i = totalDeals - 1; i >= 0 && count < maxRows; i--)
   {
      ulong dealTicket = HistoryDealGetTicket(i);
      if(dealTicket == 0) continue;
      
      ENUM_DEAL_TYPE dealType = (ENUM_DEAL_TYPE)HistoryDealGetInteger(dealTicket, DEAL_TYPE);
      if(dealType != DEAL_TYPE_BUY && dealType != DEAL_TYPE_SELL) continue;
      
      long entry = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
      if(entry != DEAL_ENTRY_OUT) continue;
      
      ulong orderTicket = HistoryDealGetInteger(dealTicket, DEAL_ORDER);
      string sym = HistoryDealGetString(dealTicket, DEAL_SYMBOL);
      double profit = HistoryDealGetDouble(dealTicket, DEAL_PROFIT);
      double volume = HistoryDealGetDouble(dealTicket, DEAL_VOLUME);
      
      // Chuyển đổi giờ mở lệnh sang GMT+7
      datetime openTime = ServerTimeToGMT7((datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME));
      
      double commissionPerLot = GetCommissionPerLot(sym);
      double commission = commissionPerLot * volume;
      double swap = 0;
      datetime closeTime = openTime;
      
      if(HistoryOrderSelect(orderTicket))
      {
         // Chuyển đổi giờ đóng lệnh sang GMT+7
         closeTime = ServerTimeToGMT7((datetime)HistoryOrderGetInteger(orderTicket, ORDER_TIME_DONE));
         
         double daysHeld = (double)(closeTime - openTime) / 86400.0;
         if(daysHeld < 0) daysHeld = 0;
         
         double swapLong = SymbolInfoDouble(sym, SYMBOL_SWAP_LONG);
         double swapShort = SymbolInfoDouble(sym, SYMBOL_SWAP_SHORT);
         ENUM_ORDER_TYPE orderType = (ENUM_ORDER_TYPE)HistoryOrderGetInteger(orderTicket, ORDER_TYPE);
         bool isBuy = (orderType == ORDER_TYPE_BUY || orderType == ORDER_TYPE_BUY_LIMIT || orderType == ORDER_TYPE_BUY_STOP);
         double swapRate = isBuy ? swapLong : swapShort;
         
         double point = SymbolInfoDouble(sym, SYMBOL_POINT);
         double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
         double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
         double pointValue = (tickSize > 0 && tickValue > 0) ? (tickValue / tickSize) * point : 0;
         swap = volume * swapRate * pointValue * daysHeld;
      }
      
      // === CHỈ GIỮ LẠI LỆNH ĐÓNG TRONG NGÀY HÔM NAY (GMT+7) ===
      if(closeTime < dayStartGMT7 || closeTime > dayEndGMT7)
         continue;
      
      ArrayResize(tempDeals, count + 1);
      HistDeal hd;
      hd.ticket = dealTicket; 
      hd.sym = sym; 
      hd.profit = profit;
      hd.commission = -commission; 
      hd.swap = swap;
      hd.totalFee = -commission + swap; 
      hd.netProfit = profit - commission + swap;
      hd.volume = volume; 
      hd.openTime = openTime;
      hd.dealType = (dealType == DEAL_TYPE_BUY) ? DEAL_TYPE_SELL : DEAL_TYPE_BUY;
      hd.magic = (int)HistoryDealGetInteger(dealTicket, DEAL_MAGIC);
      hd.comment = HistoryDealGetString(dealTicket, DEAL_COMMENT);
      hd.closeTime = closeTime;
      tempDeals[count] = hd;
      count++;
   }
   
   ArrayResize(g_historyDeals, count);
   if(count > 0) 
   { 
      for(int i = 0; i < count; i++) 
         g_historyDeals[i] = tempDeals[i]; 
   }
   g_historyDealCount = count;
   g_cachedStatsDealCount = -1;
}
//+------------------------------------------------------------------+
//| UPDATE HISTORY STATS CACHE                                       |
//+------------------------------------------------------------------+
void UpdateHistoryStatsCache()
{
   if(g_cachedStatsDealCount == g_historyDealCount) return;
   ZeroMemory(g_cachedStats);
   g_cachedStats.totalTrades = g_historyDealCount;
   
   for(int i = 0; i < g_historyDealCount; i++)
   {
      HistDeal hd = g_historyDeals[i];
      g_cachedStats.totalCommission += hd.commission; g_cachedStats.totalSwap += hd.swap;
      g_cachedStats.totalFee += hd.totalFee; g_cachedStats.totalGrossProfit += hd.profit;
      g_cachedStats.totalNetProfit += hd.netProfit; g_cachedStats.totalVolume += hd.volume;
      if(hd.netProfit > 0) { g_cachedStats.winCount++; g_cachedStats.totalWin += hd.netProfit; }
      else if(hd.netProfit < 0) { g_cachedStats.lossCount++; g_cachedStats.totalLoss += MathAbs(hd.netProfit); }
      else { g_cachedStats.breakevenCount++; }
   }
   
   g_cachedStats.avgWin = (g_cachedStats.winCount > 0) ? (g_cachedStats.totalWin / g_cachedStats.winCount) : 0;
   g_cachedStats.avgLoss = (g_cachedStats.lossCount > 0) ? (g_cachedStats.totalLoss / g_cachedStats.lossCount) : 0;
   g_cachedStats.winRate = (g_cachedStats.totalTrades > 0) ? ((double)g_cachedStats.winCount / g_cachedStats.totalTrades * 100.0) : 0.0;
   g_cachedStats.profitFactor = (g_cachedStats.totalLoss > 0) ? (g_cachedStats.totalWin / g_cachedStats.totalLoss) : (g_cachedStats.totalWin > 0 ? 999.0 : 0.0);
   g_cachedStatsDealCount = g_historyDealCount;
}

//+------------------------------------------------------------------+
//| DRAW HISTORY STATS                                               |
//+------------------------------------------------------------------+
void DrawHistoryStats()
{
   UpdateHistoryStatsCache();
   
   int histX = 15; int histY = 25; int rowH = 16;
   int maxVisibleRows = MaxHistoryRows > 0 ? MaxHistoryRows : 20;
   
   color headerColor;
   if(g_cachedStats.winRate >= 60) headerColor = clrLime;
   else if(g_cachedStats.winRate >= 40) headerColor = clrGold;
   else headerColor = clrTomato;
   
   string headerText = StringFormat("📊 HISTORY TODAY (GMT+7) | Trades: %d | WinRate: %.0f%% | PF: %.2f",
                                     g_cachedStats.totalTrades, g_cachedStats.winRate, g_cachedStats.profitFactor);
   UpdMsgFixedBottom("HEADER", headerText, histX, histY, headerColor, 10, true);
   histY += rowH + 4;
   
   color netColor = (g_cachedStats.totalNetProfit >= 0) ? clrMediumSpringGreen : clrDeepPink;
   string beText = (g_cachedStats.breakevenCount > 0) ? StringFormat(" | BE: %d", g_cachedStats.breakevenCount) : "";
   string plText = StringFormat("Net P/L: %+.2f | Fee: %.2f (Com: %.2f Swap: %.2f) | ✅Win: %d ❌Loss: %d%s",
                                 g_cachedStats.totalNetProfit, g_cachedStats.totalFee, g_cachedStats.totalCommission,
                                 g_cachedStats.totalSwap, g_cachedStats.winCount, g_cachedStats.lossCount, beText);
   UpdMsgFixedBottom("SUMMARY", plText, histX, histY, netColor, 9, false);
   histY += rowH + 4;
   
   UpdMsgFixedBottom("LINE", "──────────────────────────────────────────────────────────", histX, histY, clrDarkGray, 8, false);
   histY += 12;
   
   string colHeader = StringFormat("%-4s %-7s %s %-8s | %5s | %9s | %8s | %s", " ", "TIME", "T", "SYMBOL", "VOL", "P/L", "FEE", "RESULT");
   UpdMsgFixedBottom("COL_HEADER", colHeader, histX, histY, clrSilver, 8, false);
   histY += 14;
   
   int displayCount = MathMin(g_historyDealCount, maxVisibleRows);
   for(int i = 0; i < displayCount; i++)
   {
      if(histY > 420) break;
      int idx = g_historyDealCount - 1 - i;
      HistDeal hd = g_historyDeals[idx];
      
      // === SỬA: closeTime đã là GMT+7 từ LoadHistoryDeals(), không cần chuyển đổi nữa ===
      datetime gmt7Close = hd.closeTime;
      string timeStr = TimeToString(gmt7Close, TIME_MINUTES);
      string typeStr = (hd.dealType == DEAL_TYPE_BUY) ? "B" : "S";
      
      string resultIcon; color dealColor;
      if(hd.netProfit > 0) { resultIcon = "✅ WIN "; dealColor = clrMediumSpringGreen; }
      else if(hd.netProfit < 0) { resultIcon = "❌ LOSS"; dealColor = clrDeepPink; }
      else { resultIcon = "➖ BE  "; dealColor = clrDarkGray; }
      
      string magicTag = "";
      if(hd.magic == DivMagicNumber) magicTag = "div ";
      else if(hd.magic == DSL_MagicNumber) magicTag = "drg ";
      else if(hd.magic == MagicNumber) magicTag = "MT5 ";
      
      string dealText = StringFormat("%-4s %-7s %s %-8s | %5.2f | %+9.2f | %8.2f | %s",
                                      magicTag, timeStr, typeStr, hd.sym, hd.volume, hd.netProfit, hd.totalFee, resultIcon);
      string objName = "DEAL_" + IntegerToString(idx);
      UpdMsgFixedBottom(objName, dealText, histX, histY, dealColor, 8, false);
      histY += rowH;
   }
}
//+------------------------------------------------------------------+
//| UPDMSG FIXED BOTTOM                                              |
//+------------------------------------------------------------------+
void UpdMsgFixedBottom(string n, string t, int x, int yFromBottom, color c, int fontSize = 11, bool isBold = false)
{
   string o = HIST_Prefix + n;
   if(ObjectFind(0, o) < 0) ObjectCreate(0, o, OBJ_LABEL, 0, 0, 0);
   ObjectSetString(0, o, OBJPROP_FONT, "Consolas"); ObjectSetInteger(0, o, OBJPROP_FONTSIZE, fontSize);
   ObjectSetInteger(0, o, OBJPROP_CORNER, CORNER_LEFT_LOWER); ObjectSetInteger(0, o, OBJPROP_COLOR, c);
   ObjectSetInteger(0, o, OBJPROP_XDISTANCE, x); ObjectSetInteger(0, o, OBJPROP_YDISTANCE, yFromBottom);
   ObjectSetString(0, o, OBJPROP_TEXT, t); ObjectSetInteger(0, o, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
   ObjectSetInteger(0, o, OBJPROP_SELECTABLE, false); ObjectSetInteger(0, o, OBJPROP_ZORDER, 0);
   if(isBold) ObjectSetInteger(0, o, OBJPROP_FONTSIZE, fontSize + 1);
}

//+------------------------------------------------------------------+
//| CLEANUP HISTORY OBJECTS                                          |
//+------------------------------------------------------------------+
void CleanupHistoryObjects()
{
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i);
      if(StringFind(name, HIST_Prefix) != 0) continue;
      if(name == HIST_Prefix + "HEADER") continue;
      if(name == HIST_Prefix + "SUMMARY") continue;
      if(name == HIST_Prefix + "LINE") continue;
      if(name == HIST_Prefix + "COL_HEADER") continue;
      if(StringFind(name, HIST_Prefix + "DEAL_") == 0)
      {
         string idxStr = StringSubstr(name, StringLen(HIST_Prefix + "DEAL_"));
         int idx = (int)StringToInteger(idxStr);
         if(idx >= g_historyDealCount) ObjectDelete(0, name);
      }
   }
}

//+------------------------------------------------------------------+
//| Nạp danh sách ticket hiện có                                      |
//+------------------------------------------------------------------+
void LoadKnownTickets()
{
   ArrayResize(g_knownTickets, 0); g_knownTicketCount = 0;
   int total = PositionsTotal();
   for(int i = 0; i < total; i++) { ulong ticket = PositionGetTicket(i); if(ticket > 0 && PositionSelectByTicket(ticket)) { ArrayResize(g_knownTickets, g_knownTicketCount + 1); g_knownTickets[g_knownTicketCount] = ticket; g_knownTicketCount++; } }
   g_chartSwitchLocked = (g_knownTicketCount > 0);
   if(g_chartSwitchLocked) Print("🔒 Chart switch locked: ", g_knownTicketCount, " existing positions found");
}

//+------------------------------------------------------------------+
//| Kiểm tra vị thế mới và chuyển chart                               |
//+------------------------------------------------------------------+
void CheckNewPositionsAndSwitchChart()
{
   int total = PositionsTotal();
   ulong currentTickets[]; int currentCount = 0; ArrayResize(currentTickets, 0);
   for(int i = 0; i < total; i++) { ulong ticket = PositionGetTicket(i); if(ticket > 0 && PositionSelectByTicket(ticket)) { ArrayResize(currentTickets, currentCount + 1); currentTickets[currentCount] = ticket; currentCount++; } }
   if(currentCount == 0 && g_knownTicketCount > 0) { g_chartSwitchLocked = false; g_knownTicketCount = 0; ArrayResize(g_knownTickets, 0); Print("🔓 Chart switch unlocked: All positions closed"); return; }
   for(int i = 0; i < currentCount; i++) { ulong ticket = currentTickets[i]; bool isKnown = false; for(int j = 0; j < g_knownTicketCount; j++) if(g_knownTickets[j] == ticket) { isKnown = true; break; } if(!isKnown && !g_chartSwitchLocked) { if(PositionSelectByTicket(ticket)) { string newSymbol = PositionGetString(POSITION_SYMBOL); if(newSymbol != g_currentSymbol) { SwitchSymbol(newSymbol); Print("🔄 Auto-switched chart to: ", newSymbol, " (new position #", ticket, ")"); } g_chartSwitchLocked = true; Print("🔒 Chart switch locked: New position detected on ", newSymbol); } } }
   ArrayResize(g_knownTickets, currentCount); for(int i = 0; i < currentCount; i++) g_knownTickets[i] = currentTickets[i]; g_knownTicketCount = currentCount;
}

//+------------------------------------------------------------------+
//| DIVERGENCE AUTO TRADE                                             |
//+------------------------------------------------------------------+
void CheckDivergenceSignal()
{
   if(!EnableDivergenceAutoTrade) return;
   
   string prefix = "DIVSIGNAL_";
   
   if(!GlobalVariableCheck(prefix + "ACTIVE") || GlobalVariableGet(prefix + "ACTIVE") != 1) return;
   if(!GlobalVariableCheck(prefix + "TIME")) return;
   
   if(TimeCurrent() - (datetime)GlobalVariableGet(prefix + "TIME") > 300) 
   { 
      ClearDivSignal(); 
      return; 
   }
   
   if(!GlobalVariableCheck(prefix + "TYPE")) return;
   int type = (int)GlobalVariableGet(prefix + "TYPE");
   
   if(!GlobalVariableCheck(prefix + "ENTRY")) return;
   double entryPrice = GlobalVariableGet(prefix + "ENTRY");
   
   if(!GlobalVariableCheck(prefix + "SL")) return;
   double indicatorSL = GlobalVariableGet(prefix + "SL");
   
   string sym = ""; 
   string tfStr = "";
   if(!ReadDivSignalInfo(sym, tfStr)) 
   { 
      ClearDivSignal(); 
      return; 
   }
   
   if(!IsTimeframeEnabled(tfStr)) 
   { 
      Print("⏸️ Div Signal: Timeframe ", tfStr, " is disabled for ", sym); 
      ClearDivSignal(); 
      return; 
   }
   
   double riskMoney = GetRiskForTimeframe(tfStr); 
   double slMultiplier = GetSLMultiplierForTimeframe(tfStr);
   int maxTotalPositions = GetMaxPositionsForTimeframe(tfStr); 
   int cooldownMinutes = GetCooldownForTimeframe(tfStr);
   
   if(TimeCurrent() - g_lastDivTradeTime < cooldownMinutes * 60) 
      return;
   
   int totalPositions = PositionsTotal(); 
   int divCount = 0;
   for(int i = 0; i < totalPositions; i++) 
   { 
      ulong ticket = PositionGetTicket(i); 
      if(!PositionSelectByTicket(ticket)) continue; 
      if((int)PositionGetInteger(POSITION_MAGIC) == DivMagicNumber) divCount++; 
   }
   
   if(divCount >= maxTotalPositions) 
   { 
      Print("⏸️ Div Signal: Max positions reached (", divCount, "/", maxTotalPositions, ")"); 
      ClearDivSignal(); 
      return; 
   }
   
   if(!SymbolSelect(sym, true)) 
   { 
      Print("❌ Div Signal: Symbol ", sym, " not available"); 
      ClearDivSignal(); 
      return; 
   }
   
   if(!IsSymbolAllowedForDivTrade(sym))
   {
      Print("⏸️ Div Signal: Symbol ", sym, " not in allowed list (", CustomSymbols, ")");
      ClearDivSignal();
      return;
   }
   
   int symDivCount = 0;
   for(int i = 0; i < totalPositions; i++) 
   { 
      ulong ticket = PositionGetTicket(i); 
      if(!PositionSelectByTicket(ticket)) continue; 
      if((int)PositionGetInteger(POSITION_MAGIC) == DivMagicNumber && 
         PositionGetString(POSITION_SYMBOL) == sym) 
         symDivCount++; 
   }
   
   if(symDivCount >= DivMaxPerSymbol) 
   { 
      Print("⏸️ Div Signal: ", sym, " already has ", symDivCount, "/", DivMaxPerSymbol, " positions"); 
      ClearDivSignal(); 
      return; 
   }
   
   if(DivMaxSpread > 0) 
   { 
      double ask = SymbolInfoDouble(sym, SYMBOL_ASK); 
      double bid = SymbolInfoDouble(sym, SYMBOL_BID); 
      if(ask > 0 && bid > 0) 
      { 
         double spreadPercent = (ask - bid) / ask * 100.0; 
         if(spreadPercent > DivMaxSpread) 
         { 
            Print("⚠️ Div Signal: Spread too high on ", sym, " (", DoubleToString(spreadPercent, 3), "% | Max=", DoubleToString(DivMaxSpread, 3), "%)"); 
            ClearDivSignal();
            return; 
         } 
      } 
   }
   
   double currentPrice, adjustedSL; 
   double pipSize = GetPipSizeForSymbol(sym); 
   double point = SymbolInfoDouble(sym, SYMBOL_POINT); 
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double minSLDistance = GetMinSLDistance(sym, tfStr);
   
   if(type == 1) // BUY
   { 
      currentPrice = SymbolInfoDouble(sym, SYMBOL_ASK); 
      double originalDist = MathAbs(currentPrice - indicatorSL); 
      double multipliedDist = originalDist * slMultiplier; 
      double finalSLDistance = (minSLDistance > 0) ? MathMax(multipliedDist, minSLDistance) : multipliedDist; 
      adjustedSL = currentPrice - finalSLDistance; 
      
      double stopLevelDist = (SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL) + 10) * point; 
      double minAllowedSL = currentPrice - stopLevelDist; 
      if(adjustedSL > minAllowedSL) adjustedSL = minAllowedSL; 
   }
   else // SELL
   { 
      currentPrice = SymbolInfoDouble(sym, SYMBOL_BID); 
      double originalDist = MathAbs(indicatorSL - currentPrice); 
      double multipliedDist = originalDist * slMultiplier; 
      double finalSLDistance = (minSLDistance > 0) ? MathMax(multipliedDist, minSLDistance) : multipliedDist; 
      adjustedSL = currentPrice + finalSLDistance; 
      
      double stopLevelDist = (SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL) + 10) * point; 
      double minAllowedSL = currentPrice + stopLevelDist; 
      if(adjustedSL < minAllowedSL) adjustedSL = minAllowedSL; 
   }
   
   double finalSLDist = MathAbs(currentPrice - adjustedSL);
   
   Print("📏 SL [", sym, "|", tfStr, "]: Orig=", DoubleToString(MathAbs(currentPrice - indicatorSL)/pipSize, 1), 
         " pips | x", DoubleToString(slMultiplier, 2), "=", 
         DoubleToString(MathAbs(currentPrice - indicatorSL)*slMultiplier/pipSize, 1), " pips", 
         (minSLDistance > 0) ? StringFormat(" | ATR Min=%.1f pips", minSLDistance/pipSize) : "", 
         " | Final=", DoubleToString(finalSLDist/pipSize, 1), " pips");
   
   double slPips = finalSLDist / pipSize;
   double lotSize = CalculateLotSizeFromRiskSymbolEx(sym, slPips, riskMoney);
   
   if(lotSize <= 0) 
   { 
      Print("❌ Div Signal: Lot size = 0 (SL Pips: ", DoubleToString(slPips, 1), ")"); 
      ClearDivSignal(); 
      return; 
   }
   
   double minLot = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN); 
   double maxLot = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX); 
   double lotStep = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP); 
   if(lotStep <= 0) lotStep = 0.01;
   
   lotSize = MathFloor(lotSize / lotStep) * lotStep;
   
   if(lotSize < 0.9 * minLot) 
   { 
      Print("❌ Div Signal [", tfStr, "]: Lotsize (", DoubleToString(lotSize, 2), 
            ") < 0.9 * MinVolume (", DoubleToString(0.9 * minLot, 2), ")");
      Print("   Symbol: ", sym, " | SL Pips: ", DoubleToString(slPips, 1), " | Risk: $", DoubleToString(riskMoney, 2)); 
      ClearDivSignal(); 
      return; 
   }
   
   if(lotSize > maxLot) 
   { 
      Print("❌ Div Signal [", tfStr, "]: Lotsize (", DoubleToString(lotSize, 2), 
            ") > MaxVolume (", DoubleToString(maxLot, 2), ")"); 
      ClearDivSignal(); 
      return; 
   }
   
   if(lotSize < minLot) lotSize = minLot;
   
   double tpPrice = 0;
   
   if(EnableRRTP) 
   { 
      double slDist = finalSLDist; 
      double tpDist = slDist * DivRiskRewardRatio; 
      if(type == 1) 
         tpPrice = currentPrice + tpDist; 
      else 
         tpPrice = currentPrice - tpDist; 
   }
   
   totalPositions = PositionsTotal(); 
   divCount = 0; 
   symDivCount = 0;
   
   for(int i = 0; i < totalPositions; i++) 
   { 
      ulong ticket = PositionGetTicket(i); 
      if(!PositionSelectByTicket(ticket)) continue; 
      if((int)PositionGetInteger(POSITION_MAGIC) == DivMagicNumber) 
      { 
         divCount++; 
         if(PositionGetString(POSITION_SYMBOL) == sym) symDivCount++; 
      } 
   }
   
   if(symDivCount >= DivMaxPerSymbol) 
   { 
      Print("⏸️ Div Signal: ", sym, " max per symbol reached (re-check)"); 
      ClearDivSignal(); 
      return; 
   }
   
   if(divCount >= maxTotalPositions) 
   { 
      Print("⏸️ Div Signal: Max total positions reached (re-check)"); 
      ClearDivSignal(); 
      return; 
   }
   
   bool success = false;
   string comment = "DIV_" + tfStr + "_" + sym + "_" + TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES);
   
   if(type == 1) 
      success = tradeDiv.Buy(lotSize, sym, currentPrice, adjustedSL, tpPrice, comment);
   else 
      success = tradeDiv.Sell(lotSize, sym, currentPrice, adjustedSL, tpPrice, comment);
   
   if(success)
   {
      int newTotal = divCount + 1; 
      int newSymTotal = symDivCount + 1;
      
      Print("🚀 DIV ", (type == 1) ? "BUY" : "SELL", " OPENED: ", sym, " [", tfStr, "]", 
            " | Lot=", DoubleToString(lotSize, 2), 
            " | Entry=", DoubleToString(currentPrice, digits), 
            " | SL=", DoubleToString(adjustedSL, digits), 
            " | TP=", DoubleToString(tpPrice, digits),
            " | RR=1:", DoubleToString(DivRiskRewardRatio, 2), 
            " | SL Dist=", DoubleToString(slPips, 1), " pips", 
            " | Risk=$", DoubleToString(riskMoney, 2), 
            " | ", sym, "=", newSymTotal, "/", DivMaxPerSymbol, 
            " | Total=", newTotal);
      
      g_lastDivTradeTime = TimeCurrent(); 
      ClearDivSignal();
      
      if(EnablePushNotify) 
      {
         string notifyMsg = StringFormat("[DIV %s] %s %s | Lot=%.2f | RR=1:%.1f | SL=%.1f pips | Risk=$%.0f | %s=%d/%d | Total=%d", 
                                        tfStr, sym, (type == 1) ? "BUY" : "SELL", 
                                        lotSize, DivRiskRewardRatio, slPips, riskMoney,
                                        sym, newSymTotal, DivMaxPerSymbol, newTotal);
         SendNotification(notifyMsg);
      }
      
      BuildRows(); 
      Draw();
   }
   else 
   { 
      Print("❌ Div Signal: Trade FAILED on ", sym, " [", tfStr, "] Error=", GetLastError()); 
      ClearDivSignal(); 
   }
}

bool ReadDivSignalInfo(string &sym, string &tfStr)
{
   if(!FileIsExist("DIVSIGNAL_SYMBOL.txt", FILE_COMMON)) return false;
   int h = FileOpen("DIVSIGNAL_SYMBOL.txt", FILE_READ|FILE_TXT|FILE_COMMON); if(h == INVALID_HANDLE) return false;
   string fullData = FileReadString(h); FileClose(h);
   string parts[]; int count = StringSplit(fullData, '|', parts);
   if(count >= 2) { sym = parts[0]; tfStr = parts[1]; return true; }
   if(count == 1) { sym = parts[0]; tfStr = "M15"; Print("⚠️ Div Signal: Old format detected, using default TF=M15"); return true; }
   return false;
}

void ClearDivSignal() { string prefix = "DIVSIGNAL_"; if(GlobalVariableCheck(prefix + "ACTIVE")) GlobalVariableSet(prefix + "ACTIVE", 0); FileDelete("DIVSIGNAL_SYMBOL.txt", FILE_COMMON); }

//+------------------------------------------------------------------+
//| MANAGE TRAILING (DÙNG THAM SỐ MẶC ĐỊNH)                            |
//+------------------------------------------------------------------+
//| MANAGE TRAILING (FIXED - KHÔNG MÂU THUẪN BE/TRAILING)             |
//+------------------------------------------------------------------+
void ManageTrailing()
{
   UpdateNewsStatus();
   
   if(g_isNewsBlocked)
   {
      static datetime lastBlockedLog = 0;
      if(TimeCurrent() - lastBlockedLog >= 30)
      {
         Print("⛔ [NEWS BLOCK] Trailing & BreakEven PAUSED - ", g_newsRemainingSeconds, "s remaining");
         lastBlockedLog = TimeCurrent();
      }
      return;
   }
   
   int total = PositionsTotal();
   
   // Xóa các trail state không còn tồn tại
   for(int i = g_trailCount - 1; i >= 0; i--) 
   { 
      if(!PositionSelectByTicket(g_trailStates[i].ticket)) 
      { 
         for(int j = i; j < g_trailCount - 1; j++) 
            g_trailStates[j] = g_trailStates[j + 1]; 
         g_trailCount--; 
         ArrayResize(g_trailStates, g_trailCount); 
      } 
   }
   
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i); 
      if(!PositionSelectByTicket(ticket)) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != DivMagicNumber) continue;
      
      string sym = PositionGetString(POSITION_SYMBOL);
      bool isBuy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      double entryPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL = PositionGetDouble(POSITION_SL);
      double currentTP = PositionGetDouble(POSITION_TP);
      double currentPrice = isBuy ? SymbolInfoDouble(sym, SYMBOL_BID) : SymbolInfoDouble(sym, SYMBOL_ASK);
      if(currentPrice <= 0) continue;
      double point = SymbolInfoDouble(sym, SYMBOL_POINT);
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      
      // Tìm hoặc tạo trail state
      int idx = FindTrailIndex(ticket);
      if(idx < 0) 
      { 
         STrailState ns; 
         ns.ticket = ticket; 
         ns.sym = sym; 
         ns.entryPrice = entryPrice; 
         ns.initialSL = currentSL;
         // === FIX: Luôn tính oneR từ entry so với initialSL gốc ===
         ns.oneR = MathAbs(entryPrice - currentSL);
         if(ns.oneR <= point) 
            ns.oneR = 10 * point;  // Fallback an toàn
         ns.lockedSL = 0; 
         ns.breakEvenDone = false; 
         ns.trailingActivated = false; 
         ArrayResize(g_trailStates, g_trailCount + 1); 
         g_trailStates[g_trailCount] = ns; 
         g_trailCount++; 
         idx = g_trailCount - 1; 
      }
      
      STrailState state = g_trailStates[idx];
      
      // === FIX: Đảm bảo oneR không bị sai khi state đã tồn tại ===
      // Nếu currentSL đã tốt hơn initialSL (đã từng BE/Trailing), giữ nguyên oneR gốc
      bool slImproved = isBuy ? (currentSL > state.initialSL + point) : (currentSL < state.initialSL - point);
      
      if(!slImproved && MathAbs(state.oneR - MathAbs(entryPrice - state.initialSL)) > point)
      {
         // Chỉ sửa oneR nếu SL chưa từng được cải thiện
         state.oneR = MathAbs(entryPrice - state.initialSL);
         if(state.oneR <= point) state.oneR = 10 * point;
      }
      
      double profitDist = isBuy ? (currentPrice - entryPrice) : (entryPrice - currentPrice);
      double rAchieved = (state.oneR > 0) ? (profitDist / state.oneR) : 0;
      double newSL = currentSL; 
      bool needModify = false;
      string modifyReason = "";
      
      // ============================================
      // BREAKEVEN - CHỈ KÉO 1 LẦN, ƯU TIÊN THẤP HƠN TRAILING
      // ============================================
      if(EnableBreakEven && !state.breakEvenDone && rAchieved >= BreakEvenTriggerRR)
      {
         int stopLevel = (int)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL); 
         double minDist = stopLevel * point;
         
         double beSL = isBuy ? (entryPrice + 0.2 * state.oneR) : (entryPrice - 0.2 * state.oneR);
         
         // Chỉ kéo nếu BE tốt hơn currentSL và chưa có trailing
         bool beBetter = isBuy ? (beSL > currentSL + point) : (beSL < currentSL - point);
         bool priceFarEnough = isBuy ? (currentPrice > entryPrice + minDist) : (currentPrice < entryPrice - minDist);
         
         if(beBetter && priceFarEnough && !state.trailingActivated)  // ← Thêm !state.trailingActivated
         {
            newSL = beSL;
            state.breakEvenDone = true;
            needModify = true;
            modifyReason = "BE";
         }
      }
      
      // ============================================
      // TRAILING STOP - ƯU TIÊN CAO HƠN BE
      // ============================================
      if(EnableAutoTrailing && rAchieved >= TrailingTriggerRR)
      {
         double trailDist = TrailingLockRR * state.oneR;
         
         if(UseStaticTrailing)
         {
            if(!state.trailingActivated)
            {
               double activationPrice = isBuy ? 
                  (entryPrice + TrailingTriggerRR * state.oneR) : 
                  (entryPrice - TrailingTriggerRR * state.oneR);
               
               state.lockedSL = isBuy ? 
                  (activationPrice - trailDist) : 
                  (activationPrice + trailDist);
               
               state.trailingActivated = true;
               state.breakEvenDone = true;  // ← Đánh dấu BE đã xong vì trailing tốt hơn
            }
            
            // Chỉ kéo nếu lockedSL tốt hơn currentSL
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
               state.breakEvenDone = true;  // ← Đánh dấu BE đã xong
               needModify = true;
               if(modifyReason == "") modifyReason = "TS";
            }
         }
      }
      
      // ============================================
      // THỰC HIỆN MODIFY
      // ============================================
      if(needModify) 
      { 
         int stopLevel = (int)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL); 
         double minDist = stopLevel * point; 
         bool valid = isBuy ? 
            (newSL < currentPrice - minDist) : 
            (newSL > currentPrice + minDist); 
         
         if(valid && MathAbs(newSL - currentSL) > point) 
         { 
            if(tradeDiv.PositionModify(ticket, newSL, currentTP)) 
            { 
               g_trailStates[idx] = state;
               Print("🔄 ", modifyReason, " | ", sym,
                     " | Ticket=", ticket, 
                     " | R=", DoubleToString(rAchieved, 2),
                     " | SL: ", DoubleToString(currentSL, digits), " → ", DoubleToString(newSL, digits));
            }
         }
      }
      
      // === LUÔN LƯU STATE ===
      g_trailStates[idx] = state;
   }
}
int FindTrailIndex(ulong ticket) { for(int i = 0; i < g_trailCount; i++) if(g_trailStates[i].ticket == ticket) return i; return -1; }

double CalculateLotSizeFromRiskSymbol(string symbol, double slPips) { return CalculateLotSizeFromRiskSymbolEx(symbol, slPips, DivRiskMoney); }
double GetPointMultiplierForSymbol(string sym) { int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS); return (digits == 3 || digits == 5) ? 10.0 : 1.0; }
double GetBrokerSpreadPips(string sym) { double ask = SymbolInfoDouble(sym, SYMBOL_ASK); double bid = SymbolInfoDouble(sym, SYMBOL_BID); if(ask <= 0 || bid <= 0) return 0; double pipSize = GetPipSizeForSymbol(sym); return (pipSize > 0) ? (ask - bid) / pipSize : (ask - bid); }
string ReadDivSignalSymbol() { if(!FileIsExist("DIVSIGNAL_SYMBOL.txt", FILE_COMMON)) return ""; int h = FileOpen("DIVSIGNAL_SYMBOL.txt", FILE_READ|FILE_TXT|FILE_COMMON); if(h == INVALID_HANDLE) return ""; string sym = FileReadString(h); FileClose(h); return sym; }
double GetPipSizeForSymbol(string sym) { int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS); if(digits == 2 || digits == 3) return(0.01); if(digits == 4 || digits == 5) return(0.0001); return(SymbolInfoDouble(sym, SYMBOL_POINT) * 10); }
double GetPipSize() { return GetPipSizeForSymbol(_Symbol); }
double GetPointMultiplier() { int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS); return (digits == 3 || digits == 5) ? 10.0 : 1.0; }

void UpdateGlobalVariables()
{
   if(EnableDragSL) { double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN); bool lotsizeValid = (g_LastLotSize >= 0.9 * minLot); g_DSL_Ready_Global = (g_SL_Initialized && !g_SL_Dragging && g_LastLotSize > 0 && g_LastSide != "NONE" && lotsizeValid); g_DSL_LotSize_Global = g_LastLotSize; g_DSL_Side_Global = g_LastSide; g_DSL_SLPrice_Global = g_SL_Price; GlobalVariableSet("DSL_Ready", g_DSL_Ready_Global ? 1 : 0); GlobalVariableSet("DSL_LotSize", g_DSL_LotSize_Global); GlobalVariableSet("DSL_SLPrice", g_DSL_SLPrice_Global); GlobalVariableSet("DSL_Side", (g_DSL_Side_Global == "BUY") ? 1 : (g_DSL_Side_Global == "SELL") ? -1 : 0); }
}

void ProcessCopyTrade()
{
   if(!FileIsExist(FileName, FILE_COMMON)) return; int h = FileOpen(FileName, FILE_READ|FILE_COMMON|FILE_TXT|FILE_ANSI); if(h == INVALID_HANDLE) return;
   string currentFileCTIDs[]; while(!FileIsEnding(h)) { string line = FileReadString(h); if(StringLen(line) < 5) continue; string p[]; if(StringSplit(line,'|',p) != 6) continue; string symbol = p[0], side = p[1], ctid = p[5]; double lot = StringToDouble(p[2]), sl = StringToDouble(p[3]), tp = StringToDouble(p[4]); if(lot <= 0) continue; int cfSz = ArraySize(currentFileCTIDs); ArrayResize(currentFileCTIDs, cfSz+1); currentFileCTIDs[cfSz] = ctid; if(CTIDInFreeze(ctid) || CTIDInClosed(ctid)) continue; lot *= LotMultiplier; if(CTIDInRAM(ctid)) { CheckAndModifyLimits(ctid, sl, tp); continue; } if(OpenTrade(symbol, side, lot, sl, tp, ctid)) AddCTID_RAM(ctid); }
   FileClose(h); CheckAndCloseMissingTrades(currentFileCTIDs); CheckManuallyClosedTrades(currentFileCTIDs); LoadFromPositions();
   for(int i=ArraySize(FrozenCTIDs)-1; i>=0; i--) { bool found=false; for(int j=0; j<ArraySize(currentFileCTIDs); j++) if(FrozenCTIDs[i]==currentFileCTIDs[j]) { found=true; break; } if(!found) DeleteFrozenIndex(i); }
   for(int i=ArraySize(ClosedCTIDs)-1; i>=0; i--) { bool found=false; for(int j=0; j<ArraySize(currentFileCTIDs); j++) if(ClosedCTIDs[i]==currentFileCTIDs[j]) { found=true; break; } if(!found) DeleteClosedIndex(i); }
}

void ResetDragSLToDefault() { g_SL_Initialized = false; g_SL_Dragging = false; g_LastDragTime = 0; g_LastInteractionTime = 0; g_LastLotSize = 0.0; g_LastSide = "NONE"; g_PriorityDragSL = false; g_SL_Price = SymbolInfoDouble(_Symbol, SYMBOL_BID); if(ObjectFind(0, g_SL_LineName) >= 0) { int totalBars = Bars(_Symbol, _Period); datetime startTime = iTime(_Symbol, _Period, MathMin(10, totalBars-1)); datetime endTime = iTime(_Symbol, _Period, 0) + PeriodSeconds(_Period) * 20; ObjectSetDouble(0, g_SL_LineName, OBJPROP_PRICE, 0, g_SL_Price); ObjectSetDouble(0, g_SL_LineName, OBJPROP_PRICE, 1, g_SL_Price); ObjectSetInteger(0, g_SL_LineName, OBJPROP_TIME, 0, startTime); ObjectSetInteger(0, g_SL_LineName, OBJPROP_TIME, 1, endTime); ObjectSetInteger(0, g_SL_LineName, OBJPROP_COLOR, clrGray); ObjectSetInteger(0, g_SL_LineName, OBJPROP_STYLE, STYLE_DOT); ObjectSetInteger(0, g_SL_LineName, OBJPROP_WIDTH, 1); ObjectSetInteger(0, g_SL_LineName, OBJPROP_SELECTABLE, true); ObjectSetInteger(0, g_SL_LineName, OBJPROP_SELECTED, true); } if(ObjectFind(0, g_SL_LabelName) >= 0) { datetime endTime = iTime(_Symbol, _Period, 0) + PeriodSeconds(_Period) * 20; ObjectSetInteger(0, g_SL_LabelName, OBJPROP_TIME, 0, endTime); ObjectSetDouble(0, g_SL_LabelName, OBJPROP_PRICE, 0, g_SL_Price + 2 * _Point * GetPointMultiplier()); ObjectSetString(0, g_SL_LabelName, OBJPROP_TEXT, ">> SET <<"); ObjectSetInteger(0, g_SL_LabelName, OBJPROP_COLOR, clrSilver); ObjectSetInteger(0, g_SL_LabelName, OBJPROP_FONTSIZE, font_size); ObjectSetInteger(0, g_SL_LabelName, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER); ObjectSetInteger(0, g_SL_LabelName, OBJPROP_SELECTABLE, true); ObjectSetInteger(0, g_SL_LabelName, OBJPROP_SELECTED, false); } g_SLObjectsCreated = false; g_LastCalcBars = Bars(_Symbol, _Period); g_LastPrice = SymbolInfoDouble(_Symbol, SYMBOL_BID); g_LastSLPrice = g_SL_Price; UpdateGlobalVariables(); ChartSetInteger(ChartID(), CHART_EVENT_MOUSE_MOVE, true); ChartRedraw(); }

double GetCommissionPerLot(string symbol) { string symbolPart = StringLen(symbol) >= 6 ? StringSubstr(symbol, 0, 6) : symbol; string accountId = IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)); string gvName = "COMM_" + symbolPart + "_" + accountId; if(GlobalVariableCheck(gvName)) { double commission = GlobalVariableGet(gvName); if(commission > 0) return commission; } return commission_per_lot; }

string GetSide() { if(!g_SL_Initialized) return "NONE"; double current_price = SymbolInfoDouble(_Symbol, SYMBOL_BID); if(g_SL_Price < current_price) return "BUY"; return "SELL"; }

double CalculateLotSize() { if(!g_SL_Initialized) return 0.0; double current_price = SymbolInfoDouble(_Symbol, SYMBOL_BID); double sl_price = g_SL_Price; if(current_price == 0 || sl_price == 0) return(0.0); double contract_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE); double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE); double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT); int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS); double tickValue = (digits == 2 && _Symbol != "BTCUSDc") ? contract_size * tick_size : SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE); double pip = GetPipSize(); double slPips = MathAbs(current_price - sl_price) / pip; if(slPips <= 0) return(0.0); double value_per_point = (tick_size != 0.0) ? (tickValue / tick_size) : 0.0; if(value_per_point <= 0.0) value_per_point = 1.0; double pipValuePerLot = value_per_point * pip; if(pipValuePerLot <= 0.0) pipValuePerLot = 1.0; double com = GetCommissionPerLot(_Symbol); double brokerSpread = GetBrokerSpreadPips(_Symbol); if(brokerSpread <= 0) brokerSpread = 0.2; slPips += brokerSpread; double totalCostPerLot = slPips * pipValuePerLot + com; if(totalCostPerLot <= 0) return(0.0); double lotSize = risk_amount / totalCostPerLot; double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP); if(lotStep <= 0) lotStep = 0.01; lotSize = MathFloor(lotSize / lotStep) * lotStep; return(lotSize); }

double CalculateLotSizeFromRiskSymbolEx(string symbol, double slPips, double riskMoney)
{
   if(slPips <= 0 || riskMoney <= 0) return 0;
   
   double contract_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   double tick_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   
   double tickValue;
   if(digits == 2 && symbol != "BTCUSDc")
      tickValue = contract_size * tick_size;
   else
      tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   
   double value_per_point = (tick_size != 0.0) ? (tickValue / tick_size) : 0.0;
   if(value_per_point <= 0.0) value_per_point = 1.0;
   
   double pip = GetPipSizeForSymbol(symbol);
   double pipValuePerLot = value_per_point * pip;
   if(pipValuePerLot <= 0.0) pipValuePerLot = 1.0;
   
   double com = GetCommissionPerLot(symbol);
   double totalCostPerLot = slPips * pipValuePerLot + com;
   if(totalCostPerLot <= 0) return 0;
   
   double lotSize = riskMoney / totalCostPerLot;
   
   double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(lotStep <= 0) lotStep = 0.01;
   lotSize = MathFloor(lotSize / lotStep) * lotStep;
   
   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   lotSize = MathMax(minLot, MathMin(maxLot, lotSize));
   
   return lotSize;
}

bool NeedUpdateSL() { double current_price = SymbolInfoDouble(_Symbol, SYMBOL_BID); int current_bars = Bars(_Symbol, _Period); if(current_bars != g_LastCalcBars) return true; double multiplier = GetPointMultiplier(); if(MathAbs(current_price - g_LastPrice) >= _Point * multiplier * 0.5) return true; if(MathAbs(g_SL_Price - g_LastSLPrice) >= _Point) return true; if(g_SL_Dragging) return true; return false; }

void UpdateDragSL() { if(ObjectFind(0, g_SL_LineName) >= 0) { double price1 = ObjectGetDouble(0, g_SL_LineName, OBJPROP_PRICE, 0); double price2 = ObjectGetDouble(0, g_SL_LineName, OBJPROP_PRICE, 1); if(MathAbs(price1 - price2) > _Point) { ResetDragSLToDefault(); return; } g_SL_Price = price1; } if(!g_SL_Initialized && !g_SL_Dragging) { int current_bars = Bars(_Symbol, _Period); if(current_bars != g_LastCalcBars) g_SL_Price = SymbolInfoDouble(_Symbol, SYMBOL_BID); } if(!NeedUpdateSL() && g_SLObjectsCreated) return; int totalBars = Bars(_Symbol, _Period); datetime barTime = iTime(_Symbol, _Period, 0); datetime startTime = iTime(_Symbol, _Period, MathMin(10, totalBars-1)); datetime endTime = barTime + PeriodSeconds(_Period) * 20; double multiplier = GetPointMultiplier(); double labelOffset = 2 * _Point; color lineColor; int lineStyle, lineWidth; string side = "NONE"; double lotSize = 0.0; if(!g_SL_Initialized) { lineColor = clrGray; lineStyle = STYLE_DOT; lineWidth = 1; } else { side = GetSide(); lotSize = CalculateLotSize(); lineColor = (side == "SELL") ? clrRed : sl_line_color; lineStyle = sl_line_style; lineWidth = sl_line_width; g_LastLotSize = lotSize; g_LastSide = side; } if(ObjectFind(0, g_SL_LineName) < 0) { ObjectCreate(0, g_SL_LineName, OBJ_TREND, 0, startTime, g_SL_Price, endTime, g_SL_Price); ObjectSetInteger(0, g_SL_LineName, OBJPROP_RAY_RIGHT, true); ObjectSetInteger(0, g_SL_LineName, OBJPROP_BACK, false); ObjectSetInteger(0, g_SL_LineName, OBJPROP_SELECTABLE, true); ObjectSetInteger(0, g_SL_LineName, OBJPROP_SELECTED, true); } else { ObjectSetDouble(0, g_SL_LineName, OBJPROP_PRICE, 0, g_SL_Price); ObjectSetDouble(0, g_SL_LineName, OBJPROP_PRICE, 1, g_SL_Price); ObjectSetInteger(0, g_SL_LineName, OBJPROP_TIME, 0, startTime); ObjectSetInteger(0, g_SL_LineName, OBJPROP_TIME, 1, endTime); ObjectSetInteger(0, g_SL_LineName, OBJPROP_SELECTABLE, true); if(!g_SL_Initialized) ObjectSetInteger(0, g_SL_LineName, OBJPROP_SELECTED, true); } ObjectSetInteger(0, g_SL_LineName, OBJPROP_COLOR, lineColor); ObjectSetInteger(0, g_SL_LineName, OBJPROP_STYLE, lineStyle); ObjectSetInteger(0, g_SL_LineName, OBJPROP_WIDTH, lineWidth); string labelText; color labelColor; double labelPrice; int labelAnchor; if(!g_SL_Initialized) { labelText = ">> SET <<"; labelColor = clrSilver; labelPrice = g_SL_Price + labelOffset; labelAnchor = ANCHOR_LEFT_LOWER; } else { double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN); if(lotSize < 0.9 * minLot) { if(side == "BUY") { labelText = "[BUY " + DoubleToString(lotSize, 2) + " lots] ⚠️ TOO SMALL"; labelColor = clrOrange; labelPrice = g_SL_Price - labelOffset; labelAnchor = ANCHOR_LEFT_UPPER; } else { labelText = "[SELL " + DoubleToString(lotSize, 2) + " lots] ⚠️ TOO SMALL"; labelColor = clrOrange; labelPrice = g_SL_Price + labelOffset; labelAnchor = ANCHOR_LEFT_LOWER; } } else if(lotSize > SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX)) { if(side == "BUY") { labelText = "[BUY " + DoubleToString(lotSize, 2) + " lots] ⚠️ TOO LARGE"; labelColor = clrOrange; labelPrice = g_SL_Price - labelOffset; labelAnchor = ANCHOR_LEFT_UPPER; } else { labelText = "[SELL " + DoubleToString(lotSize, 2) + " lots] ⚠️ TOO LARGE"; labelColor = clrOrange; labelPrice = g_SL_Price + labelOffset; labelAnchor = ANCHOR_LEFT_LOWER; } } else { if(side == "BUY") { labelText = "[BUY " + DoubleToString(lotSize, 2) + " lots] ▶ Run " + DSL_ScriptName; labelColor = clrLime; labelPrice = g_SL_Price - labelOffset; labelAnchor = ANCHOR_LEFT_UPPER; } else { labelText = "[SELL " + DoubleToString(lotSize, 2) + " lots] ▶ Run " + DSL_ScriptName; labelColor = clrTomato; labelPrice = g_SL_Price + labelOffset; labelAnchor = ANCHOR_LEFT_LOWER; } } } if(ObjectFind(0, g_SL_LabelName) < 0) { ObjectCreate(0, g_SL_LabelName, OBJ_TEXT, 0, endTime, labelPrice); ObjectSetInteger(0, g_SL_LabelName, OBJPROP_BACK, false); } else { ObjectSetInteger(0, g_SL_LabelName, OBJPROP_TIME, 0, endTime); ObjectSetDouble(0, g_SL_LabelName, OBJPROP_PRICE, 0, labelPrice); } ObjectSetString(0, g_SL_LabelName, OBJPROP_TEXT, labelText); ObjectSetInteger(0, g_SL_LabelName, OBJPROP_COLOR, labelColor); ObjectSetInteger(0, g_SL_LabelName, OBJPROP_FONTSIZE, font_size); ObjectSetInteger(0, g_SL_LabelName, OBJPROP_ANCHOR, labelAnchor); ObjectSetInteger(0, g_SL_LabelName, OBJPROP_SELECTABLE, true); ObjectSetInteger(0, g_SL_LabelName, OBJPROP_SELECTED, false); g_SLObjectsCreated = true; g_LastPrice = SymbolInfoDouble(_Symbol, SYMBOL_BID); g_LastSLPrice = g_SL_Price; g_LastCalcBars = totalBars; }

void OpenDSLTrade() { if(!g_SL_Initialized || g_LastLotSize <= 0 || g_LastSide == "NONE") { Print("❌ DSL: Chưa sẵn sàng!"); return; } string symbol = _Symbol; double lot = g_LastLotSize, sl = g_SL_Price; double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN), maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX), step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP); lot = MathMax(minLot, MathMin(maxLot, lot)); lot = MathFloor(lot / step) * step; if(lot < 0.9 * minLot) { Print("❌ DSL: Lotsize too small"); return; } double ask = SymbolInfoDouble(symbol, SYMBOL_ASK); double bid = SymbolInfoDouble(symbol, SYMBOL_BID); bool success = false; if(g_LastSide == "BUY") { if(sl >= ask) { Print("❌ DSL BUY: SL too high"); return; } success = tradeDSL.Buy(lot, symbol, ask, sl, 0, "DSL_" + TimeToString(TimeCurrent())); } else { if(sl <= bid) { Print("❌ DSL SELL: SL too low"); return; } success = tradeDSL.Sell(lot, symbol, bid, sl, 0, "DSL_" + TimeToString(TimeCurrent())); } if(success) { Print("✅ DSL opened: ", symbol, " | Lot=", DoubleToString(lot, 2), " | SL=", DoubleToString(sl, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)), " | Risk=$", DoubleToString(risk_amount, 2)); ResetDragSLToDefault(); } else Print("❌ DSL FAILED: ", symbol, " | Error=", GetLastError()); }

void BuildRows() { ArrayResize(rows, 0); int total = PositionsTotal(); for(int i = 0; i < total; i++) { ulong ticket = PositionGetTicket(i); if(!PositionSelectByTicket(ticket)) continue; PosRow r; r.ticket = ticket; r.sym = PositionGetString(POSITION_SYMBOL); r.profit = PositionGetDouble(POSITION_PROFIT); r.entry = PositionGetDouble(POSITION_PRICE_OPEN); r.sl = PositionGetDouble(POSITION_SL); r.tp = PositionGetDouble(POSITION_TP); r.vol = PositionGetDouble(POSITION_VOLUME); r.time = (datetime)PositionGetInteger(POSITION_TIME); r.type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE); r.magic = (int)PositionGetInteger(POSITION_MAGIC); r.comment = PositionGetString(POSITION_COMMENT); int n = ArraySize(rows); ArrayResize(rows, n + 1); rows[n] = r; } for(int i = 0; i < ArraySize(rows) - 1; i++) for(int j = i + 1; j < ArraySize(rows); j++) if(rows[i].sym > rows[j].sym) { PosRow tmp = rows[i]; rows[i] = rows[j]; rows[j] = tmp; } }

//+------------------------------------------------------------------+
//| KIỂM TRA GIỜ GIAO DỊCH (GMT+7)                                   |
//+------------------------------------------------------------------+
bool IsInTradingHours()
{
   if(!EnableTradingTimeFilter) return true;
   
   datetime gmt7Now = GetGMT7Now();
   MqlDateTime dt;
   TimeToStruct(gmt7Now, dt);
   int currentHour = dt.hour;
   
   if(TradingStartHour <= TradingEndHour)
      return (currentHour >= TradingStartHour && currentHour < TradingEndHour);
   else
      return (currentHour >= TradingStartHour || currentHour < TradingEndHour);
}
//+------------------------------------------------------------------+
//| DRAW - HIỂN THỊ PANEL CHÍNH                                       |
//+------------------------------------------------------------------+
void Draw()
{
   int rowH = 18, totalRows = ArraySize(rows);
   double floating = 0; 
   int eaPositions = 0, dslPositions = 0, divPositions = 0;
   
   for(int i = 0; i < totalRows; i++) 
   { 
      if(PositionSelectByTicket(rows[i].ticket)) 
      { 
         floating += rows[i].profit; 
         if(rows[i].magic == MagicNumber) eaPositions++; 
         if(rows[i].magic == DSL_MagicNumber) dslPositions++; 
         if(rows[i].magic == DivMagicNumber) divPositions++; 
      } 
   }
   
   double eq = AccountInfoDouble(ACCOUNT_EQUITY), eq_diff = eq - equityAtDayStart;
   int y = topMargin;

   datetime gmt7Time = GetGMT7Now();
   string timeStr = TimeToString(gmt7Time, TIME_MINUTES|TIME_SECONDS);
   string heartbeatChar = g_heartbeatState ? "●" : "○";
   
   // =============================================
   // TRẠNG THÁI GIỜ GIAO DỊCH & NEWS
   // =============================================
   bool inTradingHours = IsInTradingHours();
   string timeIcon = EnableTradingTimeFilter ? (inTradingHours ? "●" : "○") : "●";
   string timeFilterStr = EnableTradingTimeFilter ? 
      StringFormat(" | %s %dh-%dh", timeIcon, TradingStartHour, TradingEndHour) : "";
   
   string newsStatusStr = "";
   color statusColorToShow = g_statusColor;
   
   if(GlobalVariableCheck(GV_NewsBlocked))
   {
      bool blocked = (GlobalVariableGet(GV_NewsBlocked) >= 0.5);
      if(blocked)
      {
         int remainSec = GlobalVariableCheck(GV_NewsRemaining) ? (int)GlobalVariableGet(GV_NewsRemaining) : 0;
         int remainMin = remainSec / 60;
         int remainSecMod = remainSec % 60;
         newsStatusStr = StringFormat(" | 🔴 NEWS: %02d:%02d", remainMin, remainSecMod);
         statusColorToShow = clrOrangeRed;
      }
      else
      {
         newsStatusStr = " | 🟢 NEWS: SAFE";
      }
   }
   else
   {
      newsStatusStr = " | ⚪ NEWS: N/A";
   }
   
   // Màu header tổng hợp
   if(!inTradingHours && EnableTradingTimeFilter)
      statusColorToShow = clrRed;
   else if(g_isNewsBlocked)
      statusColorToShow = clrOrangeRed;
   
   // =============================================
   // DÒNG 1: TRẠNG THÁI CHÍNH + THỜI GIAN + GIỜ GIAO DỊCH
   // =============================================
   string line1Status = StringFormat("[%s] EA: %s | %s (GMT+7)%s", 
                                      heartbeatChar, g_statusText, timeStr, timeFilterStr);
   UpdMsg("HEARTBEAT", line1Status, startX, y, statusColorToShow, 9, true); 
   y += rowH + 2;
   
   // =============================================
   // DÒNG 2: TÍNH NĂNG + NEWS STATUS
   // =============================================
   string switchStatus = g_chartSwitchLocked ? "[LOCKED]" : "[FREE]";
   string line2Features = StringFormat("COPY:%s | SL:%s | SWITCH:%s%s",
                                       EnableCopyTrade ? "ON" : "OFF",
                                       EnableDragSL ? "ON" : "OFF",
                                       switchStatus,
                                       newsStatusStr);
   UpdMsg("FEATURES", line2Features, startX, y, (g_isNewsBlocked ? clrOrangeRed : clrLightGray), 9, false);
   y += rowH + 2;
   
   // =============================================
   // DÒNG 3: CẢNH BÁO NGOÀI GIỜ HOẶC NEWS BLOCK
   // =============================================
   if(!inTradingHours && EnableTradingTimeFilter)
   {
      // Ngoài giờ trade → hiện TIME_WARNING, xóa NEWS_WARNING nếu có
      UpdMsg("TIME_WARNING", StringFormat("⏰ OUTSIDE TRADING HOURS - No new orders | Trading: %dh-%dh GMT+7", 
                                          TradingStartHour, TradingEndHour), 
             startX, y, clrRed, 9, true);
      y += rowH + 2;
      
      if(ObjectFind(0, P + "NEWS_WARNING") >= 0)
         ObjectDelete(0, P + "NEWS_WARNING");
   }
   else
   {
      // Trong giờ trade → xóa TIME_WARNING
      if(ObjectFind(0, P + "TIME_WARNING") >= 0)
         ObjectDelete(0, P + "TIME_WARNING");
      
      if(g_isNewsBlocked)
      {
         // News đang block
         int remainSec = g_newsRemainingSeconds;
         int remainMin = remainSec / 60;
         int remainSecMod = remainSec % 60;
         
         UpdMsg("NEWS_WARNING", StringFormat("⛔ NEWS BLOCK ACTIVE - Trailing & BreakEven PAUSED for %02d:%02d", 
                                             remainMin, remainSecMod), 
                startX, y, clrOrangeRed, 9, true);
         y += rowH + 2;
      }
      else
      {
         // News safe → xóa NEWS_WARNING
         if(ObjectFind(0, P + "NEWS_WARNING") >= 0)
            ObjectDelete(0, P + "NEWS_WARNING");
      }
   }
   
   // =============================================
   // DÒNG 4: DIV TRADE SYMBOLS
   // =============================================
   if(EnableDivergenceAutoTrade)
   {
      string cleanList = CustomSymbols;
      StringReplace(cleanList, " ", "");
      string syms[];
      int symCount = StringSplit(cleanList, ',', syms);
      
      string allDisplay = "";
      for(int i = 0; i < symCount; i++)
      {
         if(allDisplay != "") allDisplay += ",";
         allDisplay += syms[i];
      }
      
      string enabledTFs = "";
      if(EnableM15Trades) enabledTFs += "M15 ";
      if(EnableH1Trades) enabledTFs += "H1 ";
      
      string statusTag = "";
      if(!inTradingHours && EnableTradingTimeFilter)
         statusTag = " ⏰OUT";
      else if(g_isNewsBlocked)
         statusTag = " ⛔BLOCKED";
      
      UpdMsg("DIV_SYMBOLS", StringFormat("📡 %s | %s| $%.0f | Max%d%s", 
                                          allDisplay, enabledTFs, DivRiskMoney, DivMaxTotalPositions, statusTag), 
              startX, y, (!inTradingHours || g_isNewsBlocked) ? clrOrangeRed : clrSkyBlue, 8, false);
      y += rowH - 2;
   }
   
   y += 4;
   
   // =============================================
   // ĐƯỜNG KẺ PHÂN CÁCH
   // =============================================
   UpdMsg("LINE_DECOR_1", "=============================================================================================================", startX, y, clrDarkGray, 8); 
   y += 10;
   
   // =============================================
   // EQUITY & P/L
   // =============================================
   UpdMsg("ACC_EQ", StringFormat("Equity   : %12.2f", eq), startX, y, clrLightGray); 
   UpdMsg("ACC_START", StringFormat("Day Start: %12.2f", equityAtDayStart), startX + 220, y, clrDarkGray); 
   y += rowH;
   
   color plColor = (eq_diff >= 0) ? clrMediumSpringGreen : clrDeepPink; 
   color flColor = (floating >= 0) ? clrMediumSpringGreen : clrDeepPink;
   
   UpdMsg("ACC_FL", StringFormat("Floating : %12.2f", floating), startX, y, flColor); 
   UpdMsg("ACC_PL", StringFormat("Day P/L  : %12.2f", eq_diff), startX + 220, y, plColor); 
   y += rowH + 4;
   
   // =============================================
   // ĐƯỜNG PHÂN CÁCH + DANH SÁCH VỊ THẾ
   // =============================================
   UpdMsg("LINE_DECOR_2", "--------------------------------------------------------------------------", startX, y, clrDarkGray, 8); 
   y += 12;
   UpdMsg("LINE_DECOR_4", "------------------------ POSITIONS --------------------------", startX, y, clrDarkGray, 8); 
   y += 14;
   
   if(totalRows > 0) 
   { 
      string blockTag = "";
      if(!inTradingHours && EnableTradingTimeFilter) blockTag = " | ⏰ NO NEW ORDERS";
      else if(g_isNewsBlocked) blockTag = " | ⛔ TRAILING/BE BLOCKED";
      
      UpdMsg("TR_HEAD", StringFormat("POSITIONS: Total %d | Copy: %d | DSL: %d | DIV: %d%s", 
                                      totalRows, eaPositions, dslPositions, divPositions, blockTag), 
              startX, y, (!inTradingHours || g_isNewsBlocked) ? clrOrangeRed : clrLightSkyBlue, 9, true); 
      y += rowH + 4; 
   }
   else 
   { 
      UpdMsg("TR_HEAD", "NO ACTIVE POSITIONS", startX, y, clrDarkGray, 9, false); 
      y += rowH; 
   }

   // =============================================
   // DANH SÁCH CHI TIẾT TỪNG VỊ THẾ
   // =============================================
   ulong alive[];
   for(int i = 0; i < totalRows; i++) 
   { 
      PosRow r = rows[i]; 
      if(!PositionSelectByTicket(r.ticket)) continue; 
      
      int n = ArraySize(alive); 
      ArrayResize(alive, n + 1); 
      alive[n] = r.ticket;
      
      datetime local = ServerTimeToGMT7(r.time); 
      string posTimeStr = TimeToString(local, TIME_MINUTES); 
      string typeStr = (r.type == POSITION_TYPE_BUY) ? "B" : "S"; 
      color c, btnBg;
      
      if(r.magic == DSL_MagicNumber) 
      { 
         c = (r.type == POSITION_TYPE_BUY) ? clrLime : clrOrangeRed; 
         btnBg = clrSteelBlue; 
         typeStr = "*" + typeStr; 
      }
      else if(r.magic == DivMagicNumber) 
      { 
         c = (r.type == POSITION_TYPE_BUY) ? clrCyan : clrHotPink; 
         btnBg = clrDarkViolet; 
         typeStr = "~" + typeStr; 
      }
      else if(r.magic == MagicNumber) 
      { 
         c = (r.type == POSITION_TYPE_BUY) ? clrMediumSpringGreen : clrDeepPink; 
         btnBg = clrLightSeaGreen; 
      }
      else 
      { 
         c = (r.type == POSITION_TYPE_BUY) ? clrDarkSeaGreen : clrPaleVioletRed; 
         btnBg = clrDarkSlateGray; 
      }
      
      int digits = (int)SymbolInfoInteger(r.sym, SYMBOL_DIGITS); 
      string strSL = (r.sl > 0) ? DoubleToString(r.sl, digits) : "-"; 
      string strTP = (r.tp > 0) ? DoubleToString(r.tp, digits) : "-";
      string magicStr = (r.magic == DSL_MagicNumber) ? "*" : (r.magic == DivMagicNumber) ? "~" : (r.magic == MagicNumber) ? "+" : " ";
      
      string blockTag = "";
      
      if(r.magic == DivMagicNumber && g_isNewsBlocked)
         blockTag = " 🔒";
      
      string lineStr = StringFormat("%s %s %s %-7s | Net: %+.2f | V: %.2f | SL: %s | TP: %s%s", 
                                     magicStr, posTimeStr, typeStr, r.sym, r.profit, r.vol, strSL, strTP, blockTag);
      
      UpdMsg("T_LINE_" + (string)r.ticket, lineStr, startX, y, c, 9);
      
      if(g_statusText != "CRASHED!" && !(g_isChartBusy && EnableAntiZoomProtection)) 
         UpdBtn("X_" + (string)r.ticket, "X", startX + 580, y, 18, btnBg, "Consolas", 8);
      else 
         UpdMsg("X_" + (string)r.ticket, "[LOCKED]", startX + 580, y, clrDarkGray, 8);
      
      y += rowH; 
   }
   
   if(totalRows > 0 && g_statusText != "CRASHED!" && !(g_isChartBusy && EnableAntiZoomProtection)) 
   { 
      y += 8; 
      UpdBtn("CLOSE_ALL", "DISMISS ALL", startX, y, 120, BlendColor(clrBlack, clrCrimson, 0.45), "Trebuchet MS", 9); 
   }
   
   if(ShowHistoryStats) 
   { 
      DrawHistoryStats(); 
      CleanupHistoryObjects(); 
   }
   
   Cleanup(alive);
}
void Cleanup(ulong &alive[]) 
{ 
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--) 
   { 
      string name = ObjectName(0, i); 
      if(StringFind(name, P) != 0) continue; 
      if(StringFind(name, P + "HEARTBEAT") == 0) continue; 
      if(StringFind(name, P + "FEATURES") == 0) continue;
      if(StringFind(name, P + "DIV_SYMBOLS") == 0) continue; 
      if(StringFind(name, P + "ACC_") == 0) continue; 
      if(StringFind(name, P + "LINE_DECOR_") == 0) continue; 
      if(name == P + "TR_HEAD") continue; 
      if(name == P + "CLOSE_ALL") continue; 
      if(StringFind(name, P + "TIME_WARNING") == 0) continue;
      if(StringFind(name, P + "NEWS_WARNING") == 0) continue;
      bool isTradeObj = false; ulong ticket = 0; 
      if(StringFind(name, P + "T_LINE_") == 0) { isTradeObj = true; ticket = (ulong)StringToInteger(StringSubstr(name, StringLen(P + "T_LINE_"))); } 
      else if(StringFind(name, P + "X_") == 0) { isTradeObj = true; ticket = (ulong)StringToInteger(StringSubstr(name, StringLen(P + "X_"))); } 
      if(isTradeObj) { bool found = false; for(int j = 0; j < ArraySize(alive); j++) if(alive[j] == ticket) { found = true; break; } if(!found) ObjectDelete(0, name); } 
      else ObjectDelete(0, name); 
   } 
}

void CloseTicket(ulong ticket) { if(!PositionSelectByTicket(ticket)) return; string ctid = PositionGetString(POSITION_COMMENT); int magic = (int)PositionGetInteger(POSITION_MAGIC); g_ClosingOne = true; bool success = false; if(magic == DSL_MagicNumber) success = tradeDSL.PositionClose(ticket); else if(magic == DivMagicNumber) success = tradeDiv.PositionClose(ticket); else success = trade.PositionClose(ticket); if(!success) Print("❌ CLOSE FAIL ticket=", ticket); else if(ctid != "" && magic == MagicNumber) AddCTID_Closed(ctid); g_ClosingOne = false; BuildRows(); Draw(); }

void CloseAllPositions() { g_ClosingAll = true; int total = PositionsTotal(); for(int i = total - 1; i >= 0; i--) { ulong ticket = PositionGetTicket(i); if(ticket > 0 && PositionSelectByTicket(ticket)) { string symbol = PositionGetString(POSITION_SYMBOL); double volume = PositionGetDouble(POSITION_VOLUME); bool isBuy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY); MqlTradeRequest req = {}; MqlTradeResult res = {}; req.action = TRADE_ACTION_DEAL; req.symbol = symbol; req.volume = volume; req.position = ticket; req.deviation = 50; req.type_filling = ORDER_FILLING_IOC; req.type = isBuy ? ORDER_TYPE_SELL : ORDER_TYPE_BUY; req.price = isBuy ? SymbolInfoDouble(symbol, SYMBOL_BID) : SymbolInfoDouble(symbol, SYMBOL_ASK); if(!OrderSend(req, res)) Print("❌ CloseAll failed ticket=", ticket, " Error=", GetLastError()); } } g_ClosingAll = false; BuildRows(); Draw(); }

void OnChartEvent(const int id, const long &l, const double &d, const string &s) { if(id == CHARTEVENT_CHART_CHANGE) { if(EnableAntiZoomProtection) { g_isChartBusy = true; g_chartBusyStartTime = TimeCurrent(); } UpdateChartHeightCache(); return; } if(id == CHARTEVENT_OBJECT_CLICK) { if(StringFind(s, g_scalePrefix) == 0) { string scaleAction = StringSubstr(s, StringLen(g_scalePrefix)); if(scaleAction == "PLUS") ScaleChart(1); else if(scaleAction == "MINUS") ScaleChart(-1); if(EnableAntiZoomProtection) { g_isChartBusy = true; g_chartBusyStartTime = TimeCurrent(); } return; } if(g_isChartBusy && EnableAntiZoomProtection) return; if(StringFind(s, g_buttonPrefix) == 0) { string clickedSymbol = StringSubstr(s, StringLen(g_buttonPrefix)); if(clickedSymbol != g_currentSymbol) SwitchSymbol(clickedSymbol); return; } if(s == P + "CLOSE_ALL") { CloseAllPositions(); return; } if(StringFind(s, P + "X_") == 0) { CloseTicket((ulong)StringToInteger(StringSubstr(s, StringLen(P) + 2))); return; } } if(id != CHARTEVENT_OBJECT_CLICK && id != CHARTEVENT_OBJECT_DRAG && id != CHARTEVENT_MOUSE_MOVE) return; if(EnableDragSL) { if(id == CHARTEVENT_OBJECT_CLICK && s == g_SL_LineName) { g_SL_Dragging = true; g_PriorityDragSL = true; if(!g_SL_Initialized) g_SL_Initialized = true; g_LastDragTime = g_LastInteractionTime = TimeCurrent(); ChartSetInteger(ChartID(), CHART_MOUSE_SCROLL, false); return; } if(id == CHARTEVENT_OBJECT_DRAG && s == g_SL_LineName) { g_SL_Price = ObjectGetDouble(0, g_SL_LineName, OBJPROP_PRICE); if(!g_SL_Initialized) g_SL_Initialized = true; g_LastDragTime = g_LastInteractionTime = TimeCurrent(); UpdateDragSL(); UpdateGlobalVariables(); ChartRedraw(); return; } if(id == CHARTEVENT_MOUSE_MOVE && g_SL_Dragging) { int subWindow = 0; double price; datetime dt; if(ChartXYToTimePrice(ChartID(), (int)l, (int)d, subWindow, dt, price)) { g_SL_Price = price; ObjectSetDouble(0, g_SL_LineName, OBJPROP_PRICE, price); g_LastDragTime = g_LastInteractionTime = TimeCurrent(); UpdateDragSL(); UpdateGlobalVariables(); ChartRedraw(); } return; } if(id == CHARTEVENT_CLICK && g_SL_Dragging) { g_SL_Dragging = false; g_LastDragTime = g_LastInteractionTime = TimeCurrent(); g_SLObjectsCreated = false; UpdateDragSL(); UpdateGlobalVariables(); ChartRedraw(); ChartSetInteger(ChartID(), CHART_MOUSE_SCROLL, true); return; } } }

void BuildSymbolList() { string tempSymbols[]; int tempCount = 0; if(SymbolSource == INPUT_ONLY || SymbolSource == BOTH) { string customList = CustomSymbols; StringReplace(customList, " ", ""); tempCount = StringSplit(customList, ',', tempSymbols); } if(SymbolSource == MARKET_WATCH || SymbolSource == BOTH) { int totalSymbols = SymbolsTotal(true); for(int i = 0; i < totalSymbols; i++) { string sym = SymbolName(i, true); if(ExcludeIndices) { string description = SymbolInfoString(sym, SYMBOL_DESCRIPTION); if(StringFind(description, "Index") >= 0 || StringFind(description, "CFD") >= 0 || StringFind(description, "Cash") >= 0) continue; } bool exists = false; for(int j = 0; j < tempCount; j++) if(tempSymbols[j] == sym) { exists = true; break; } if(!exists) { ArrayResize(tempSymbols, tempCount + 1); tempSymbols[tempCount] = sym; tempCount++; } } } if(IncludeCurrentSymbol) { bool exists = false; for(int i = 0; i < tempCount; i++) if(tempSymbols[i] == g_currentSymbol) { exists = true; break; } if(!exists) { ArrayResize(tempSymbols, tempCount + 1); tempSymbols[tempCount] = g_currentSymbol; tempCount++; } } if(tempCount > MaxButtons) { string finalSymbols[]; int finalCount = 1; ArrayResize(finalSymbols, 1); finalSymbols[0] = g_currentSymbol; for(int i = 0; i < tempCount && finalCount < MaxButtons; i++) if(tempSymbols[i] != g_currentSymbol) { ArrayResize(finalSymbols, finalCount + 1); finalSymbols[finalCount] = tempSymbols[i]; finalCount++; } ArrayResize(g_symbolList, finalCount); for(int i = 0; i < finalCount; i++) g_symbolList[i] = finalSymbols[i]; g_symbolCount = finalCount; } else { ArrayResize(g_symbolList, tempCount); for(int i = 0; i < tempCount; i++) g_symbolList[i] = tempSymbols[i]; g_symbolCount = tempCount; } }

ENUM_BASE_CORNER GetCornerFromEnum(ENUM_PANEL_POSITION pos) { switch(pos) { case PANEL_TOP_LEFT: return CORNER_LEFT_UPPER; case PANEL_TOP_RIGHT: return CORNER_RIGHT_UPPER; case PANEL_BOTTOM_LEFT: return CORNER_LEFT_LOWER; case PANEL_BOTTOM_RIGHT: return CORNER_RIGHT_LOWER; default: return CORNER_LEFT_UPPER; } }

void CreateSymbolButtons() { for(int i = ObjectsTotal(0) - 1; i >= 0; i--) { string name = ObjectName(0, i); if(StringFind(name, g_buttonPrefix) == 0) ObjectDelete(0, name); } ENUM_BASE_CORNER btnCorner = GetCornerFromEnum(ButtonPosition); for(int i = 0; i < g_symbolCount; i++) { string btnName = g_buttonPrefix + g_symbolList[i]; int x = ButtonStartX, y = ButtonStartY + i * (ButtonHeight + 5); if(ObjectFind(0, btnName) >= 0) ObjectDelete(0, btnName); if(ObjectCreate(0, btnName, OBJ_BUTTON, 0, 0, 0)) { ObjectSetInteger(0, btnName, OBJPROP_XDISTANCE, x); ObjectSetInteger(0, btnName, OBJPROP_YDISTANCE, y); ObjectSetInteger(0, btnName, OBJPROP_XSIZE, ButtonWidth); ObjectSetInteger(0, btnName, OBJPROP_YSIZE, ButtonHeight); ObjectSetInteger(0, btnName, OBJPROP_CORNER, btnCorner); ObjectSetString(0, btnName, OBJPROP_TEXT, g_symbolList[i]); ObjectSetInteger(0, btnName, OBJPROP_FONTSIZE, ButtonFontSize); ObjectSetString(0, btnName, OBJPROP_FONT, ButtonFontName); ObjectSetInteger(0, btnName, OBJPROP_COLOR, ButtonTextColor); ObjectSetInteger(0, btnName, OBJPROP_BGCOLOR, (g_symbolList[i] == g_currentSymbol) ? ActiveButtonColor : ButtonColor); ObjectSetInteger(0, btnName, OBJPROP_BORDER_COLOR, ButtonBorderColor); ObjectSetInteger(0, btnName, OBJPROP_SELECTABLE, false); ObjectSetInteger(0, btnName, OBJPROP_ZORDER, 1000); } } }

void UpdateSymbolButtons() { ENUM_BASE_CORNER btnCorner = GetCornerFromEnum(ButtonPosition); for(int i = 0; i < g_symbolCount; i++) { string btnName = g_buttonPrefix + g_symbolList[i]; if(ObjectFind(0, btnName) >= 0) { ObjectSetInteger(0, btnName, OBJPROP_BGCOLOR, (g_symbolList[i] == g_currentSymbol) ? ActiveButtonColor : ButtonColor); ObjectSetInteger(0, btnName, OBJPROP_CORNER, btnCorner); } } ChartRedraw(0); }

void CreateScaleButtons() { for(int i = ObjectsTotal(0) - 1; i >= 0; i--) { string name = ObjectName(0, i); if(StringFind(name, g_scalePrefix) == 0) ObjectDelete(0, name); } ENUM_BASE_CORNER btnCorner = GetCornerFromEnum(ButtonPosition); int scaleButtonWidth = 60, scaleButtonHeight = 25, gap = 5, scaleX = ButtonStartX + ButtonWidth + 10; string minusBtn = g_scalePrefix + "PLUS", plusBtn = g_scalePrefix + "MINUS"; if(ObjectCreate(0, minusBtn, OBJ_BUTTON, 0, 0, 0)) { ObjectSetInteger(0, minusBtn, OBJPROP_XDISTANCE, scaleX); ObjectSetInteger(0, minusBtn, OBJPROP_YDISTANCE, ButtonStartY); ObjectSetInteger(0, minusBtn, OBJPROP_XSIZE, scaleButtonWidth); ObjectSetInteger(0, minusBtn, OBJPROP_YSIZE, scaleButtonHeight); ObjectSetInteger(0, minusBtn, OBJPROP_CORNER, btnCorner); ObjectSetString(0, minusBtn, OBJPROP_TEXT, "🔍−"); ObjectSetInteger(0, minusBtn, OBJPROP_FONTSIZE, 10); ObjectSetString(0, minusBtn, OBJPROP_FONT, "Arial"); ObjectSetInteger(0, minusBtn, OBJPROP_COLOR, clrWhite); ObjectSetInteger(0, minusBtn, OBJPROP_BGCOLOR, clrDarkSlateGray); ObjectSetInteger(0, minusBtn, OBJPROP_BORDER_COLOR, ButtonBorderColor); ObjectSetInteger(0, minusBtn, OBJPROP_SELECTABLE, false); ObjectSetInteger(0, minusBtn, OBJPROP_ZORDER, 1001); } if(ObjectCreate(0, plusBtn, OBJ_BUTTON, 0, 0, 0)) { ObjectSetInteger(0, plusBtn, OBJPROP_XDISTANCE, scaleX + scaleButtonWidth + gap); ObjectSetInteger(0, plusBtn, OBJPROP_YDISTANCE, ButtonStartY); ObjectSetInteger(0, plusBtn, OBJPROP_XSIZE, scaleButtonWidth); ObjectSetInteger(0, plusBtn, OBJPROP_YSIZE, scaleButtonHeight); ObjectSetInteger(0, plusBtn, OBJPROP_CORNER, btnCorner); ObjectSetString(0, plusBtn, OBJPROP_TEXT, "🔍+"); ObjectSetInteger(0, plusBtn, OBJPROP_FONTSIZE, 10); ObjectSetString(0, plusBtn, OBJPROP_FONT, "Arial"); ObjectSetInteger(0, plusBtn, OBJPROP_COLOR, clrWhite); ObjectSetInteger(0, plusBtn, OBJPROP_BGCOLOR, clrDarkSlateGray); ObjectSetInteger(0, plusBtn, OBJPROP_BORDER_COLOR, ButtonBorderColor); ObjectSetInteger(0, plusBtn, OBJPROP_SELECTABLE, false); ObjectSetInteger(0, plusBtn, OBJPROP_ZORDER, 1001); } }

void UpdateScaleButtons() { long currentScale = ChartGetInteger(ChartID(), CHART_SCALE, 0); if(currentScale <= 0) currentScale = 3; if(ObjectFind(0, g_scalePrefix + "PLUS") >= 0) ObjectSetInteger(0, g_scalePrefix + "PLUS", OBJPROP_BGCOLOR, (currentScale <= 0) ? clrDimGray : clrDarkSlateGray); if(ObjectFind(0, g_scalePrefix + "MINUS") >= 0) ObjectSetInteger(0, g_scalePrefix + "MINUS", OBJPROP_BGCOLOR, (currentScale >= 5) ? clrDimGray : clrDarkSlateGray); }

void ScaleChart(int direction) { long currentScale = ChartGetInteger(ChartID(), CHART_SCALE, 0); if(currentScale <= 0) currentScale = 3; if(direction > 0) { if(currentScale > 0) currentScale--; } else { if(currentScale < 5) currentScale++; } ChartSetInteger(ChartID(), CHART_SCALE, currentScale); UpdateScaleButtons(); }

void SwitchSymbol(string newSymbol) { if(newSymbol == g_currentSymbol) return; if(ChartSetSymbolPeriod(0, newSymbol, _Period)) { g_currentSymbol = newSymbol; if(EnableDragSL) { g_SL_Price = SymbolInfoDouble(_Symbol, SYMBOL_BID); g_SL_Initialized = false; g_SL_Dragging = false; g_LastDragTime = 0; g_LastInteractionTime = 0; g_SLObjectsCreated = false; g_LastLotSize = 0.0; g_LastSide = "NONE"; UpdateGlobalVariables(); } UpdateSymbolButtons(); BuildRows(); Draw(); ChartRedraw(0); } }

void CheckManuallyClosedTrades(string &currentFileCTIDs[]) { for(int i = 0; i < ArraySize(currentFileCTIDs); i++) { string ctid = currentFileCTIDs[i]; if(CTIDInClosed(ctid) || CTIDInFreeze(ctid)) continue; bool found = false; for(int j = 0; j < PositionsTotal(); j++) { ulong ticket = PositionGetTicket(j); if(!PositionSelectByTicket(ticket)) continue; if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue; if(PositionGetString(POSITION_COMMENT) == ctid) { found = true; break; } } if(!found && CTIDInRAM(ctid)) AddCTID_Closed(ctid); } }

bool CTIDInClosed(string ctid) { for(int i = 0; i < ArraySize(ClosedCTIDs); i++) if(ClosedCTIDs[i] == ctid) return true; return false; }

void AddCTID_Closed(string ctid) { if(CTIDInClosed(ctid)) return; int sz = ArraySize(ClosedCTIDs); ArrayResize(ClosedCTIDs, sz + 1); ClosedCTIDs[sz] = ctid; }

void DeleteClosedIndex(int index) { int sz = ArraySize(ClosedCTIDs); for(int i = index; i < sz - 1; i++) ClosedCTIDs[i] = ClosedCTIDs[i + 1]; ArrayResize(ClosedCTIDs, sz - 1); }

void CheckAndModifyLimits(string ctid, double fileSL, double fileTP) { for(int i = 0; i < PositionsTotal(); i++) { ulong ticket = PositionGetTicket(i); if(!PositionSelectByTicket(ticket)) continue; if(PositionGetInteger(POSITION_MAGIC) == MagicNumber && PositionGetString(POSITION_COMMENT) == ctid) { double curSL = PositionGetDouble(POSITION_SL), curTP = PositionGetDouble(POSITION_TP); string sym = PositionGetString(POSITION_SYMBOL); double pSize = SymbolInfoDouble(sym, SYMBOL_POINT); if(MathAbs(curSL - fileSL) / pSize > SL_Tolerance_Points || MathAbs(curTP - fileTP) / pSize > SL_Tolerance_Points) trade.PositionModify(ticket, fileSL, fileTP); break; } } }

void CheckAndCloseMissingTrades(string &currentFileCTIDs[]) { for(int i = PositionsTotal() - 1; i >= 0; i--) { ulong ticket = PositionGetTicket(i); if(!PositionSelectByTicket(ticket)) continue; if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue; string ctid = PositionGetString(POSITION_COMMENT); if(ctid == "" || CTIDInFreeze(ctid)) continue; bool found = false; for(int j = 0; j < ArraySize(currentFileCTIDs); j++) if(ctid == currentFileCTIDs[j]) { found = true; break; } if(!found) CloseTicket(ticket); } }

bool CTIDInRAM(string ctid) { for(int i = 0; i < ArraySize(OpenedCTIDs); i++) if(OpenedCTIDs[i] == ctid) return true; return false; }

void AddCTID_RAM(string ctid) { int sz = ArraySize(OpenedCTIDs); ArrayResize(OpenedCTIDs, sz + 1); OpenedCTIDs[sz] = ctid; }

void LoadFromPositions() { ArrayResize(OpenedCTIDs, 0); for(int i = 0; i < PositionsTotal(); i++) { ulong ticket = PositionGetTicket(i); if(!PositionSelectByTicket(ticket)) continue; if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue; string ctid = PositionGetString(POSITION_COMMENT); if(ctid != "") AddCTID_RAM(ctid); } }

void FreezeExistingTickets() { ArrayResize(FrozenCTIDs, 0); if(!FileIsExist(FileName, FILE_COMMON)) return; int h = FileOpen(FileName, FILE_READ | FILE_COMMON | FILE_TXT | FILE_ANSI); if(h == INVALID_HANDLE) return; while(!FileIsEnding(h)) { string line = FileReadString(h); if(StringLen(line) < 5) continue; string p[]; if(StringSplit(line, '|', p) != 6) continue; int sz = ArraySize(FrozenCTIDs); ArrayResize(FrozenCTIDs, sz + 1); FrozenCTIDs[sz] = p[5]; } FileClose(h); }

bool CTIDInFreeze(string ctid) { for(int i = 0; i < ArraySize(FrozenCTIDs); i++) if(FrozenCTIDs[i] == ctid) return true; return false; }

void DeleteFrozenIndex(int index) { int sz = ArraySize(FrozenCTIDs); for(int i = index; i < sz - 1; i++) FrozenCTIDs[i] = FrozenCTIDs[i + 1]; ArrayResize(FrozenCTIDs, sz - 1); }

bool OpenTrade(string symbol, string side, double lot, double sl, double tp, string ctid) { if(!SymbolSelect(symbol, true)) return false; double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN), maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX), step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP); lot = MathMax(minLot, MathMin(maxLot, lot)); lot = MathFloor(lot / step) * step; MqlTradeRequest req; MqlTradeResult res; ZeroMemory(req); ZeroMemory(res); req.action = TRADE_ACTION_DEAL; req.symbol = symbol; req.volume = lot; req.magic = MagicNumber; req.sl = sl; req.tp = tp; req.deviation = 20; req.type_filling = ORDER_FILLING_IOC; req.comment = ctid; if(side == "B") { req.type = ORDER_TYPE_BUY; req.price = SymbolInfoDouble(symbol, SYMBOL_ASK); } else { req.type = ORDER_TYPE_SELL; req.price = SymbolInfoDouble(symbol, SYMBOL_BID); } bool ok = OrderSend(req, res); return(ok && (res.retcode == 10008 || res.retcode == 10009)); }

int GetServerDateYMD() { MqlDateTime tm; TimeToStruct(TimeCurrent(), tm); return tm.year * 10000 + tm.mon * 100 + tm.day; }

string GetDailyEquityFileName() { return StringFormat("DailyEquity_%I64d.csv", AccountInfoInteger(ACCOUNT_LOGIN)); }

bool LoadDailyEquity(int &ymd, double &equity) { int h = FileOpen(GetDailyEquityFileName(), FILE_READ | FILE_CSV | FILE_COMMON); if(h == INVALID_HANDLE) return false; ymd = (int)FileReadNumber(h); equity = FileReadNumber(h); FileClose(h); return true; }

void SaveDailyEquity(int ymd, double equity) { int oldDate; double oldEq; if(LoadDailyEquity(oldDate, oldEq)) if(oldDate == ymd && MathAbs(oldEq - equity) < 0.0001) return; int h = FileOpen(GetDailyEquityFileName(), FILE_WRITE | FILE_CSV | FILE_COMMON); if(h == INVALID_HANDLE) return; FileWrite(h, ymd, DoubleToString(equity, 2)); FileClose(h); }

void InitDailyTracking()
{
   int today = GetServerDateYMD();
   
   int fileDate;
   double fileEquity;
   if(LoadDailyEquity(fileDate, fileEquity))
   {
      if(fileDate == today)
      {
         equityAtDayStart = fileEquity;
         GlobalVariableSet(GV_EQUITY_DAYSTART(), equityAtDayStart);
         GlobalVariableSet(GV_EQUITY_DATE(), today);
         Print("📊 Day Start Equity loaded from FILE: ", DoubleToString(equityAtDayStart, 2));
         return;
      }
      else
      {
         Print("📊 File date (", fileDate, ") != today (", today, ") - New day detected");
      }
   }
   else
   {
      Print("📊 No equity file found for this account");
   }
   
   if(GlobalVariableCheck(GV_EQUITY_DAYSTART()) && 
      GlobalVariableCheck(GV_EQUITY_DATE()) && 
      (int)GlobalVariableGet(GV_EQUITY_DATE()) == today)
   {
      equityAtDayStart = GlobalVariableGet(GV_EQUITY_DAYSTART());
      Print("📊 Day Start Equity loaded from GV: ", DoubleToString(equityAtDayStart, 2));
      return;
   }
   
   equityAtDayStart = AccountInfoDouble(ACCOUNT_EQUITY);
   GlobalVariableSet(GV_EQUITY_DAYSTART(), equityAtDayStart);
   GlobalVariableSet(GV_EQUITY_DATE(), today);
   SaveDailyEquity(today, equityAtDayStart);
   Print("📊 Day Start Equity INITIALIZED: ", DoubleToString(equityAtDayStart, 2));
}

void CheckAndSaveOnNewDay() { int today = GetServerDateYMD(); if(!GlobalVariableCheck(GV_EQUITY_DATE())) return; int savedDate = (int)GlobalVariableGet(GV_EQUITY_DATE()); if(savedDate != today && !g_waitingNewDayEquity) { g_waitingNewDayEquity = true; g_pendingDate = today; g_newDayDetectTime = TimeCurrent(); return; } if(g_waitingNewDayEquity) { if(TimeCurrent() - g_newDayDetectTime < 60) return; double curEquity = AccountInfoDouble(ACCOUNT_EQUITY); if(curEquity <= 0) return; equityAtDayStart = curEquity; GlobalVariableSet(GV_EQUITY_DAYSTART(), equityAtDayStart); GlobalVariableSet(GV_EQUITY_DATE(), g_pendingDate); SaveDailyEquity(g_pendingDate, equityAtDayStart); g_waitingNewDayEquity = false; } }

void UpdMsg(string n, string t, int x, int y, color c, int fontSize = 11, bool isBold = false) { string o = P + n; if(ObjectFind(0, o) < 0) ObjectCreate(0, o, OBJ_LABEL, 0, 0, 0); ObjectSetString(0, o, OBJPROP_FONT, "Consolas"); ObjectSetInteger(0, o, OBJPROP_FONTSIZE, fontSize); ObjectSetInteger(0, o, OBJPROP_CORNER, CORNER_LEFT_UPPER); ObjectSetInteger(0, o, OBJPROP_COLOR, c); ObjectSetInteger(0, o, OBJPROP_XDISTANCE, x); ObjectSetInteger(0, o, OBJPROP_YDISTANCE, y); ObjectSetString(0, o, OBJPROP_TEXT, t); ObjectSetInteger(0, o, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER); ObjectSetInteger(0, o, OBJPROP_SELECTABLE, false); }

void UpdBtn(string n, string t, int x, int y, int width = 35, color bg_color = clrDimGray, string font_name = "Consolas", int btn_font_size = 9) { string o = P + n; if(ObjectFind(0, o) < 0) { ObjectCreate(0, o, OBJ_BUTTON, 0, 0, 0); ObjectSetInteger(0, o, OBJPROP_COLOR, clrWhite); ObjectSetInteger(0, o, OBJPROP_YSIZE, 16); } ObjectSetInteger(0, o, OBJPROP_CORNER, CORNER_LEFT_UPPER); ObjectSetString(0, o, OBJPROP_FONT, font_name); ObjectSetInteger(0, o, OBJPROP_FONTSIZE, btn_font_size); ObjectSetInteger(0, o, OBJPROP_BGCOLOR, bg_color); ObjectSetInteger(0, o, OBJPROP_XSIZE, width); ObjectSetInteger(0, o, OBJPROP_XDISTANCE, x); ObjectSetInteger(0, o, OBJPROP_YDISTANCE, y - 1); ObjectSetString(0, o, OBJPROP_TEXT, t); ObjectSetInteger(0, o, OBJPROP_STATE, false); ObjectSetInteger(0, o, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER); ObjectSetInteger(0, o, OBJPROP_BORDER_COLOR, clrBlack); }

void ClearAll() { for(int i = ObjectsTotal(0) - 1; i >= 0; i--) { string n = ObjectName(0, i); if(StringFind(n, P) == 0) ObjectDelete(0, n); } if(EnableDragSL) ObjectsDeleteAll(0, DSL_Prefix); if(ShowHistoryStats) ObjectsDeleteAll(0, HIST_Prefix); }
//+------------------------------------------------------------------+