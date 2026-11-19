//+------------------------------------------------------------------+
//|                                    RSIDivergenceBot.mq5           |
//|                                  Copyright 2026, MetaQuotes Ltd. |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, MetaQuotes Ltd."
#property link      "https://www.mql5.com"
#property version   "7.5"
#property description "RSI Divergence EA - Anti-Flicker (Unique Key) | GMT+7 Fixed | M15+H1 Only"

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

//+------------------------------------------------------------------+
//| EA Input Parameters                                               |
//+------------------------------------------------------------------+
string ExpiryDate = "2099.12.30"; 
datetime expiry = StringToTime(ExpiryDate); 

string P = "EA_";
string g_zoomMsgPrefix = "ZOOM_";
string g_panelPrefix = "DIVPNL_";

// --- Indicator Data Source ---
input group "===== Indicator Data Source =====";
input string IndicatorDataPrefix = "DIVDATA_";
input bool EnableM15 = true;
input bool EnableH1  = true;
// ĐÃ XÓA: input bool EnableH4  = true;
// ĐÃ XÓA: input bool EnableD1  = true;

// --- Quick Symbol Switch ---
input group "===== Quick Symbol Switch =====";
input bool ShowSymbolButtons = true;
input ENUM_SYMBOL_SOURCE SymbolSource = BOTH;
input string CustomSymbols = "EURUSD,GBPUSD,USDJPY,BTCUSD,XAUUSD";
input bool IncludeCurrentSymbol = true;
input int MaxButtons = 10;
input int ButtonWidth = 80;
input int ButtonHeight = 25;
input int ButtonStartX = 90;
input int ButtonStartY = 50;
input color ButtonColor = clrDodgerBlue;
input color ButtonTextColor = clrWhite;
input color ActiveButtonColor = clrGreen;
input color RTDivButtonColor = clrOrangeRed;
input color ButtonBorderColor = clrWhite;
input int ButtonFontSize = 8;
input string ButtonFontName = "Arial";
input ENUM_PANEL_POSITION ButtonPosition = PANEL_BOTTOM_RIGHT;

// --- Divergence Drawing ---
input group "===== Divergence Drawing =====";
input bool DrawDivergenceLines = true;
input bool DrawDivLabels = true;
input int DivLabelFontSize = 8;
input int DivLineWidth = 2;
input int RTLineWidth = 3;
input color BearRegColor = clrDeepPink;
input color BearHidColor = clrHotPink;
input color BullRegColor = clrLimeGreen;
input color BullHidColor = clrMediumSpringGreen;

// --- D1 Candle Panel ---
input group "===== D1 Candle Panel =====";
input bool     show_d1_panel = true;
input int      d1_candleCount = 5;
input bool     d1_flipDirection = true;
input int      d1_BodyWidth = 6;
input int      d1_GapBetween = 6;
input double   d1_PanelHeightPct = 20;
input color    d1_BullBodyColor = clrGreen;
input color    d1_BearBodyColor = clrLightGray;
input color    d1_PanelBgColor = clrGray;

// --- UI Layout ---
input group "===== UI Layout =====";
input ENUM_PANEL_POSITION DivergencePanelPosition = PANEL_BOTTOM_LEFT;
input int PanelFontSize = 9;
input int PanelLineSpacing = 16;

// --- Anti-Zoom Protection ---
input group "===== Anti-Zoom Protection =====";
input bool EnableAntiZoomProtection = true;
input int ZoomDebounceMs = 2000;

// --- Performance ---
input int UpdateIntervalSeconds = 1;

// =====================================================
// HELPER: Timezone GMT+7 (dùng offset server thực tế)
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
   return (serverTime - g_serverToGMTOffset) + 7 * 3600;
}

datetime GetGMT7Now()
{
   return ServerTimeToGMT7(TimeTradeServer());
}

// =====================================================
// STRUCT LƯU MỘT PHÂN KỲ
// =====================================================
struct SOneDiv
{
   bool     active;
   bool     isReg;
   bool     isBearish;
   bool     isRT;
   string   tfStr;
   double   price1, price2;
   double   rsi1, rsi2;
   datetime time1, time2;
   double   vol;
};

// =====================================================
// STRUCT LƯU TRẠNG THÁI SYMBOL
// =====================================================
struct SSymbolRTInfo
{
   bool     hasRT;
   string   rtTFs;
   datetime latestRTTime;
   bool     hasHist;
};

// =====================================================
// GLOBAL VARIABLES
// =====================================================
SOneDiv g_allDivs[];
int g_divCount = 0;

SOneDiv g_displayDivs[];
int g_displayDivCount = 0;

datetime g_lastDataCheck = 0;
int g_panelLineCount = 0;

datetime g_lastUpdateTime = 0;
int g_heartbeatCounter = 0;
bool g_heartbeatState = false;
color g_statusColor = clrDimGray;
string g_statusText = "ĐANG CHỜ...";

string g_currentSymbol;
string g_symbolList[];
int g_symbolCount = 0;
SSymbolRTInfo g_symbolRTInfo[];
string g_buttonPrefix = "BTN_SYM_";
string g_scalePrefix = "BTN_SCALE_";

string g_D1ObjPrefix = "D1PNL_";
datetime g_lastD1PanelUpdate = 0;
string g_divObjPrefix = "DIV_";

bool g_isChartBusy = false;
datetime g_chartBusyStartTime = 0;
int g_chartBusyCounter = 0;
int g_zoomEventCounter = 0;

string g_enabledTFStrs[];
int g_enabledTFCount = 0;

//+------------------------------------------------------------------+
//| Helper functions                                                  |
//+------------------------------------------------------------------+
ENUM_BASE_CORNER GetCorner(ENUM_PANEL_POSITION pos)
{
   switch(pos)
   {
      case PANEL_TOP_LEFT:      return CORNER_LEFT_UPPER;
      case PANEL_TOP_RIGHT:     return CORNER_RIGHT_UPPER;
      case PANEL_BOTTOM_LEFT:   return CORNER_LEFT_LOWER;
      case PANEL_BOTTOM_RIGHT:  return CORNER_RIGHT_LOWER;
      default:                  return CORNER_LEFT_UPPER;
   }
}

color GetDivColor(bool isBearish, bool isReg)
{
   if(isBearish) return isReg ? BearRegColor : BearHidColor;
   return isReg ? BullRegColor : BullHidColor;
}

//+------------------------------------------------------------------+
//| HELPER: Thêm string vào mảng động                                 |
//+------------------------------------------------------------------+
void AddToArray(string &arr[], string value)
{
   int sz = ArraySize(arr);
   ArrayResize(arr, sz + 1);
   arr[sz] = value;
}

//+------------------------------------------------------------------+
//| TẠO UNIQUE KEY CỐ ĐỊNH CHO MỖI PHÂN KỲ                             |
//+------------------------------------------------------------------+
string GetDivUniqueKey(SOneDiv &div)
{
   string dir = div.isBearish ? "Bear" : "Bull";
   string rt = div.isRT ? "RT" : "HIST";
   return div.tfStr + "_" + dir + "_" + rt + "_" + IntegerToString((int)div.time2);
}

//+------------------------------------------------------------------+
//| OnInit                                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   if(TimeCurrent() > expiry)
   {
      MessageBox("EA đã hết hạn!", "CẢNH BÁO", MB_OK|MB_ICONSTOP);
      return INIT_FAILED;
   }

   EventSetTimer(UpdateIntervalSeconds);
   CalculateServerGMTOffset();
   
   g_currentSymbol = _Symbol;
   g_lastUpdateTime = TimeCurrent();
   g_heartbeatCounter = 0;
   g_statusText = "ĐANG CHỜ DỮ LIỆU...";
   g_statusColor = clrYellow;
   g_lastD1PanelUpdate = 0;
   g_divCount = 0;
   g_displayDivCount = 0;
   g_panelLineCount = 0;
   g_isChartBusy = false;
   g_chartBusyStartTime = 0;
   g_chartBusyCounter = 0;
   g_zoomEventCounter = 0;
   
   // ĐÃ SỬA: Chỉ thêm M15 và H1
   ArrayResize(g_enabledTFStrs, 0);
   g_enabledTFCount = 0;
   if(EnableM15) { ArrayResize(g_enabledTFStrs, g_enabledTFCount+1); g_enabledTFStrs[g_enabledTFCount] = "M15"; g_enabledTFCount++; }
   if(EnableH1)  { ArrayResize(g_enabledTFStrs, g_enabledTFCount+1); g_enabledTFStrs[g_enabledTFCount] = "H1";  g_enabledTFCount++; }
   // ĐÃ XÓA: EnableH4, EnableD1
   
   BuildSymbolList();
   
   if(ShowSymbolButtons)
   {
      CreateSymbolButtons();
      CreateScaleButtons();
   }
   
   ChartRedraw(0);
   
   Print("📊 EA Divergence v7.5 - Anti-Flicker (Unique Key) | GMT+7 Fixed | M15+H1 Only");
   Print("📊 Symbol: ", g_currentSymbol);

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| OnDeinit                                                            |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   ClearAllObjects();
   Print("📊 EA deinitialized. Zoom events: ", g_zoomEventCounter, " | Pauses: ", g_chartBusyCounter);
}

//+------------------------------------------------------------------+
//| Build Symbol List                                                 |
//+------------------------------------------------------------------+
void BuildSymbolList()
{
   string tempSymbols[];
   int tempCount = 0;
   
   string customList = CustomSymbols;
   StringReplace(customList, " ", "");
   tempCount = StringSplit(customList, ',', tempSymbols);
   
   if(IncludeCurrentSymbol)
   {
      bool exists = false;
      for(int i = 0; i < tempCount; i++)
      {
         if(tempSymbols[i] == g_currentSymbol) { exists = true; break; }
      }
      if(!exists)
      {
         ArrayResize(tempSymbols, tempCount + 1);
         tempSymbols[tempCount] = g_currentSymbol;
         tempCount++;
      }
   }
   
   g_symbolCount = MathMin(tempCount, MaxButtons);
   ArrayResize(g_symbolList, g_symbolCount);
   ArrayResize(g_symbolRTInfo, g_symbolCount);
   
   for(int i = 0; i < g_symbolCount; i++)
   {
      g_symbolList[i] = tempSymbols[i];
      g_symbolRTInfo[i].hasRT = false;
      g_symbolRTInfo[i].rtTFs = "";
      g_symbolRTInfo[i].latestRTTime = 0;
      g_symbolRTInfo[i].hasHist = false;
   }
}

//+------------------------------------------------------------------+
//| Chart Event                                                        |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id == CHARTEVENT_CHART_CHANGE)
   {
      g_zoomEventCounter++;
      if(EnableAntiZoomProtection)
      {
         g_isChartBusy = true;
         g_chartBusyStartTime = TimeCurrent();
         g_chartBusyCounter++;
      }
      return;
   }
   
   if(id == CHARTEVENT_OBJECT_CLICK)
   {
      if(StringFind(sparam, g_scalePrefix) == 0)
      {
         if(sparam == g_scalePrefix + "PLUS") ScaleChart(1);
         else if(sparam == g_scalePrefix + "MINUS") ScaleChart(-1);
         if(EnableAntiZoomProtection)
         {
            g_isChartBusy = true;
            g_chartBusyStartTime = TimeCurrent();
         }
         return;
      }
      
      if(g_isChartBusy && EnableAntiZoomProtection) return;
      
      if(StringFind(sparam, g_buttonPrefix) == 0)
      {
         string clickedSymbol = StringSubstr(sparam, StringLen(g_buttonPrefix));
         if(clickedSymbol != g_currentSymbol)
         {
            Print("🔄 Switching from ", g_currentSymbol, " to ", clickedSymbol);
            g_isChartBusy = true;
            g_chartBusyStartTime = TimeCurrent();
            SwitchSymbol(clickedSymbol);
            g_chartBusyStartTime = TimeCurrent() - (ZoomDebounceMs / 1000) + 1;
         }
      }
   }
}

//+------------------------------------------------------------------+
//| OnTimer                                                            |
//+------------------------------------------------------------------+
void OnTimer()
{
   GlobalVariableSet("HB_RSI_EA", (double)TimeCurrent());
   
   if(TimeCurrent() - g_lastOffsetCalcTime > 3600)
      CalculateServerGMTOffset();
   
   if(g_isChartBusy && EnableAntiZoomProtection)
   {
      int debounceSeconds = ZoomDebounceMs / 1000;
      if(debounceSeconds < 1) debounceSeconds = 1;
      
      if(TimeCurrent() - g_chartBusyStartTime >= debounceSeconds)
      {
         g_isChartBusy = false;
         g_lastD1PanelUpdate = 0;
         g_lastDataCheck = 0;
         ObjectDelete(0, g_zoomMsgPrefix + "MSG");
      }
      else
      {
         g_lastUpdateTime = TimeCurrent();
         g_heartbeatCounter++;
         if(g_heartbeatCounter % 2 == 0) g_heartbeatState = !g_heartbeatState;
         g_statusColor = clrOrange;
         g_statusText = "ZOOMING...";
         
         string zoomMsgName = g_zoomMsgPrefix + "MSG";
         if(ObjectFind(0, zoomMsgName) < 0)
         {
            ObjectCreate(0, zoomMsgName, OBJ_LABEL, 0, 0, 0);
            ObjectSetInteger(0, zoomMsgName, OBJPROP_CORNER, CORNER_LEFT_UPPER);
            ObjectSetInteger(0, zoomMsgName, OBJPROP_XDISTANCE, 15);
            ObjectSetInteger(0, zoomMsgName, OBJPROP_YDISTANCE, 25);
            ObjectSetInteger(0, zoomMsgName, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, zoomMsgName, OBJPROP_HIDDEN, true);
            ObjectSetInteger(0, zoomMsgName, OBJPROP_ZORDER, 1000);
         }
         
         int remainingSec = debounceSeconds - (int)(TimeCurrent() - g_chartBusyStartTime);
         ObjectSetString(0, zoomMsgName, OBJPROP_FONT, "Consolas");
         ObjectSetInteger(0, zoomMsgName, OBJPROP_FONTSIZE, 12);
         ObjectSetInteger(0, zoomMsgName, OBJPROP_COLOR, clrOrange);
         ObjectSetString(0, zoomMsgName, OBJPROP_TEXT, "⏸️ EA PAUSED - Đang zoom/scroll... (Chờ " + IntegerToString(remainingSec) + "s)");
         
         ChartRedraw(0);
         return;
      }
   }
   else
   {
      ObjectDelete(0, g_zoomMsgPrefix + "MSG");
   }
   
   g_lastUpdateTime = TimeCurrent();
   g_heartbeatCounter++;
   if(g_heartbeatCounter % 2 == 0) g_heartbeatState = !g_heartbeatState;
   
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
   {
      g_statusColor = clrRed;
      g_statusText = "MẤT KẾT NỐI!";
      return;
   }
   
   g_statusColor = clrAquamarine;
   g_statusText = "RUNNING";
   g_currentSymbol = _Symbol;
   
   if(show_d1_panel && TimeCurrent() - g_lastD1PanelUpdate >= 5)
   {
      if(UpdateD1Panel())
         g_lastD1PanelUpdate = TimeCurrent();
   }
   
   if(TimeCurrent() - g_lastDataCheck >= UpdateIntervalSeconds)
   {
      g_lastDataCheck = TimeCurrent();
      UpdateAllSymbolsRTStatus();
      ReadDivergenceForSymbol(g_currentSymbol);
      SyncDrawing();
      BuildDisplayText();
   }
   
   if(ShowSymbolButtons)
   {
      UpdateSymbolButtons();
      UpdateScaleButtons();
   }
   
   ChartRedraw(0);
}

//+------------------------------------------------------------------+
//| CẬP NHẬT TRẠNG THÁI RT + HIST CHO TẤT CẢ SYMBOL                  |
//+------------------------------------------------------------------+
void UpdateAllSymbolsRTStatus()
{
   datetime todayStartGMT7 = GetGMT7Now();
   todayStartGMT7 = todayStartGMT7 - (todayStartGMT7 % 86400);
   
   for(int s = 0; s < g_symbolCount; s++)
   {
      string sym = g_symbolList[s];
      bool hasRT = false;
      string activeTFs = "";
      datetime latestRT = 0;
      bool hasHist = false;
      
      // ĐÃ SỬA: Chỉ loop qua M15 và H1
      for(int t = 0; t < g_enabledTFCount; t++)
      {
         string tfStr = g_enabledTFStrs[t];
         string prefix = IndicatorDataPrefix + sym + "_" + tfStr + "_";
         bool tfHasRT = false;
         
         if(GlobalVariableCheck(prefix + "BEAR_ACTIVE") && GlobalVariableGet(prefix + "BEAR_ACTIVE") == 1)
         {
            if(GlobalVariableCheck(prefix + "BEAR_TIME"))
            {
               datetime rt = (datetime)GlobalVariableGet(prefix + "BEAR_TIME");
               if(TimeCurrent() - rt <= 15) { tfHasRT = true; if(rt > latestRT) latestRT = rt; }
            }
         }
         
         if(GlobalVariableCheck(prefix + "BULL_ACTIVE") && GlobalVariableGet(prefix + "BULL_ACTIVE") == 1)
         {
            if(GlobalVariableCheck(prefix + "BULL_TIME"))
            {
               datetime rt = (datetime)GlobalVariableGet(prefix + "BULL_TIME");
               if(TimeCurrent() - rt <= 15) { tfHasRT = true; if(rt > latestRT) latestRT = rt; }
            }
         }
         
         if(tfHasRT) { hasRT = true; if(activeTFs != "") activeTFs += ","; activeTFs += tfStr; }
         
         if(!hasHist)
         {
            for(int i = 0; i < 20; i++)
            {
               string histKey = prefix + "HIST_BEAR_" + IntegerToString(i) + "_ACTIVE";
               if(GlobalVariableCheck(histKey) && GlobalVariableGet(histKey) == 1)
               {
                  string timeKey = prefix + "HIST_BEAR_" + IntegerToString(i) + "_TIME";
                  if(GlobalVariableCheck(timeKey))
                  {
                     datetime tt = (datetime)GlobalVariableGet(timeKey);
                     if(ServerTimeToGMT7(tt) >= todayStartGMT7) { hasHist = true; break; }
                  }
               }
               
               if(!hasHist)
               {
                  histKey = prefix + "HIST_BULL_" + IntegerToString(i) + "_ACTIVE";
                  if(GlobalVariableCheck(histKey) && GlobalVariableGet(histKey) == 1)
                  {
                     string timeKey = prefix + "HIST_BULL_" + IntegerToString(i) + "_TIME";
                     if(GlobalVariableCheck(timeKey))
                     {
                        datetime tt = (datetime)GlobalVariableGet(timeKey);
                        if(ServerTimeToGMT7(tt) >= todayStartGMT7) { hasHist = true; break; }
                     }
                  }
               }
            }
         }
      }
      
      g_symbolRTInfo[s].hasRT = hasRT;
      g_symbolRTInfo[s].rtTFs = activeTFs;
      g_symbolRTInfo[s].latestRTTime = latestRT;
      g_symbolRTInfo[s].hasHist = hasHist;
   }
}

//+------------------------------------------------------------------+
//| ĐỌC DỮ LIỆU PHÂN KỲ CHO 1 SYMBOL                                  |
//+------------------------------------------------------------------+
void ReadDivergenceForSymbol(string sym)
{
   ArrayResize(g_allDivs, 0);
   g_divCount = 0;
   
   ArrayResize(g_displayDivs, 0);
   g_displayDivCount = 0;
   
   datetime todayStartGMT7 = GetGMT7Now();
   todayStartGMT7 = todayStartGMT7 - (todayStartGMT7 % 86400);
   
   // ĐÃ SỬA: Chỉ loop qua M15 và H1
   for(int t = 0; t < g_enabledTFCount; t++)
   {
      string tfStr = g_enabledTFStrs[t];
      string prefix = IndicatorDataPrefix + sym + "_" + tfStr + "_";
      
      // === REALTIME BEAR ===
      if(GlobalVariableCheck(prefix + "BEAR_ACTIVE") && GlobalVariableGet(prefix + "BEAR_ACTIVE") == 1)
      {
         if(GlobalVariableCheck(prefix + "BEAR_TIME"))
         {
            datetime rtTime = (datetime)GlobalVariableGet(prefix + "BEAR_TIME");
            if(TimeCurrent() - rtTime <= 15)
            {
               SOneDiv div;
               div.active = true; div.isBearish = true; div.isRT = true; div.tfStr = tfStr;
               div.isReg = (GlobalVariableGet(prefix + "BEAR_ISREG") == 1);
               div.time1 = (datetime)GlobalVariableGet(prefix + "BEAR_T1");
               div.time2 = (datetime)GlobalVariableGet(prefix + "BEAR_T2");
               div.rsi1 = GlobalVariableGet(prefix + "BEAR_RSI1");
               div.rsi2 = GlobalVariableGet(prefix + "BEAR_RSI2");
               div.price1 = GlobalVariableGet(prefix + "BEAR_PRICE1");
               div.price2 = GlobalVariableGet(prefix + "BEAR_PRICE2");
               AddToAllDivs(div);
               AddToDisplayDivs(div);
            }
         }
      }
      
      // === REALTIME BULL ===
      if(GlobalVariableCheck(prefix + "BULL_ACTIVE") && GlobalVariableGet(prefix + "BULL_ACTIVE") == 1)
      {
         if(GlobalVariableCheck(prefix + "BULL_TIME"))
         {
            datetime rtTime = (datetime)GlobalVariableGet(prefix + "BULL_TIME");
            if(TimeCurrent() - rtTime <= 15)
            {
               SOneDiv div;
               div.active = true; div.isBearish = false; div.isRT = true; div.tfStr = tfStr;
               div.isReg = (GlobalVariableGet(prefix + "BULL_ISREG") == 1);
               div.time1 = (datetime)GlobalVariableGet(prefix + "BULL_T1");
               div.time2 = (datetime)GlobalVariableGet(prefix + "BULL_T2");
               div.rsi1 = GlobalVariableGet(prefix + "BULL_RSI1");
               div.rsi2 = GlobalVariableGet(prefix + "BULL_RSI2");
               div.price1 = GlobalVariableGet(prefix + "BULL_PRICE1");
               div.price2 = GlobalVariableGet(prefix + "BULL_PRICE2");
               AddToAllDivs(div);
               AddToDisplayDivs(div);
            }
         }
      }
      
      // === HISTORICAL BEAR ===
      for(int i = 0; i < 20; i++)
      {
         string key = prefix + "HIST_BEAR_" + IntegerToString(i) + "_ACTIVE";
         if(GlobalVariableCheck(key) && GlobalVariableGet(key) == 1)
         {
            string timeKey = prefix + "HIST_BEAR_" + IntegerToString(i) + "_TIME";
            if(GlobalVariableCheck(timeKey))
            {
               datetime tt = (datetime)GlobalVariableGet(timeKey);
               string base = prefix + "HIST_BEAR_" + IntegerToString(i) + "_";
               
               SOneDiv div;
               div.active = true; div.isBearish = true; div.isRT = false; div.tfStr = tfStr;
               div.isReg = (GlobalVariableGet(base + "ISREG") == 1);
               div.time1 = (datetime)GlobalVariableGet(base + "T1");
               div.time2 = tt;
               div.rsi1 = GlobalVariableGet(base + "RSI1");
               div.rsi2 = GlobalVariableGet(base + "RSI2");
               div.price1 = GlobalVariableGet(base + "PRICE1");
               div.price2 = GlobalVariableGet(base + "PRICE2");
               div.vol = GlobalVariableGet(base + "VOL");
               
               AddToAllDivs(div);
               if(ServerTimeToGMT7(tt) >= todayStartGMT7)
                  AddToDisplayDivs(div);
            }
         }
      }
      
      // === HISTORICAL BULL ===
      for(int i = 0; i < 20; i++)
      {
         string key = prefix + "HIST_BULL_" + IntegerToString(i) + "_ACTIVE";
         if(GlobalVariableCheck(key) && GlobalVariableGet(key) == 1)
         {
            string timeKey = prefix + "HIST_BULL_" + IntegerToString(i) + "_TIME";
            if(GlobalVariableCheck(timeKey))
            {
               datetime tt = (datetime)GlobalVariableGet(timeKey);
               string base = prefix + "HIST_BULL_" + IntegerToString(i) + "_";
               
               SOneDiv div;
               div.active = true; div.isBearish = false; div.isRT = false; div.tfStr = tfStr;
               div.isReg = (GlobalVariableGet(base + "ISREG") == 1);
               div.time1 = (datetime)GlobalVariableGet(base + "T1");
               div.time2 = tt;
               div.rsi1 = GlobalVariableGet(base + "RSI1");
               div.rsi2 = GlobalVariableGet(base + "RSI2");
               div.price1 = GlobalVariableGet(base + "PRICE1");
               div.price2 = GlobalVariableGet(base + "PRICE2");
               div.vol = GlobalVariableGet(base + "VOL");
               
               AddToAllDivs(div);
               if(ServerTimeToGMT7(tt) >= todayStartGMT7)
                  AddToDisplayDivs(div);
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| HELPER                                                            |
//+------------------------------------------------------------------+
void AddToAllDivs(SOneDiv &div)
{
   int idx = g_divCount++;
   ArrayResize(g_allDivs, g_divCount);
   g_allDivs[idx] = div;
}

void AddToDisplayDivs(SOneDiv &div)
{
   int idx = g_displayDivCount++;
   ArrayResize(g_displayDivs, g_displayDivCount);
   g_displayDivs[idx] = div;
}

//+------------------------------------------------------------------+
//| ĐỒNG BỘ VẼ - DÙNG UNIQUE KEY CỐ ĐỊNH (ANTI-FLICKER)               |
//+------------------------------------------------------------------+
void SyncDrawing()
{
   if(!DrawDivergenceLines) return;
   if(g_isChartBusy) return;
   
   string keepNames[];
   ArrayResize(keepNames, 0);
   
   for(int i = 0; i < g_divCount; i++)
   {
      if(!g_allDivs[i].active) continue;
      
      string key = GetDivUniqueKey(g_allDivs[i]);
      string lineName = g_divObjPrefix + "L_" + key;
      string labelName = g_divObjPrefix + "B_" + key;
      
      AddToArray(keepNames, lineName);
      AddToArray(keepNames, labelName);
      
      DrawOrUpdateDivLine(lineName, i);
      if(DrawDivLabels) DrawOrUpdateDivLabel(labelName, i);
   }
   
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i);
      if(StringFind(name, g_divObjPrefix) != 0) continue;
      
      bool keep = false;
      for(int k = 0; k < ArraySize(keepNames); k++)
         if(name == keepNames[k]) { keep = true; break; }
      
      if(!keep) ObjectDelete(0, name);
   }
}

//+------------------------------------------------------------------+
//| VẼ HOẶC CẬP NHẬT LINE PHÂN KỲ (ANTI-FLICKER)                      |
//+------------------------------------------------------------------+
void DrawOrUpdateDivLine(string lineName, int idx)
{
   if(idx < 0 || idx >= g_divCount) return;
   if(!g_allDivs[idx].active) return;
   
   color divColor = GetDivColor(g_allDivs[idx].isBearish, g_allDivs[idx].isReg);
   
   if(ObjectFind(0, lineName) < 0)
   {
      if(!ObjectCreate(0, lineName, OBJ_TREND, 0, 
                       g_allDivs[idx].time1, g_allDivs[idx].price1, 
                       g_allDivs[idx].time2, g_allDivs[idx].price2))
         return;
   }
   
   ObjectSetInteger(0, lineName, OBJPROP_TIME, 0, g_allDivs[idx].time1);
   ObjectSetDouble(0, lineName, OBJPROP_PRICE, 0, g_allDivs[idx].price1);
   ObjectSetInteger(0, lineName, OBJPROP_TIME, 1, g_allDivs[idx].time2);
   ObjectSetDouble(0, lineName, OBJPROP_PRICE, 1, g_allDivs[idx].price2);
   
   ObjectSetInteger(0, lineName, OBJPROP_COLOR, divColor);
   ObjectSetInteger(0, lineName, OBJPROP_WIDTH, g_allDivs[idx].isRT ? RTLineWidth : DivLineWidth);
   ObjectSetInteger(0, lineName, OBJPROP_STYLE, g_allDivs[idx].isReg ? STYLE_SOLID : STYLE_DASH);
   ObjectSetInteger(0, lineName, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, lineName, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, lineName, OBJPROP_HIDDEN, true);
   ObjectSetInteger(0, lineName, OBJPROP_ZORDER, 500);
}

//+------------------------------------------------------------------+
//| VẼ HOẶC CẬP NHẬT LABEL PHÂN KỲ (ANTI-FLICKER)                     |
//+------------------------------------------------------------------+
void DrawOrUpdateDivLabel(string labelName, int idx)
{
   if(idx < 0 || idx >= g_divCount) return;
   if(!g_allDivs[idx].active) return;
   
   color divColor = GetDivColor(g_allDivs[idx].isBearish, g_allDivs[idx].isReg);
   string divType = g_allDivs[idx].isReg ? "Reg" : "Hid";
   string dir = g_allDivs[idx].isBearish ? "Bear" : "Bull";
   string labelText = g_allDivs[idx].tfStr + " " + dir + " " + divType;
   if(g_allDivs[idx].isRT) labelText = "🔴" + labelText;
   
   if(ObjectFind(0, labelName) < 0)
   {
      if(!ObjectCreate(0, labelName, OBJ_TEXT, 0, g_allDivs[idx].time2, g_allDivs[idx].price2))
         return;
   }
   
   ObjectSetInteger(0, labelName, OBJPROP_TIME, 0, g_allDivs[idx].time2);
   ObjectSetDouble(0, labelName, OBJPROP_PRICE, 0, g_allDivs[idx].price2);
   ObjectSetString(0, labelName, OBJPROP_TEXT, labelText);
   ObjectSetInteger(0, labelName, OBJPROP_COLOR, divColor);
   ObjectSetInteger(0, labelName, OBJPROP_FONTSIZE, DivLabelFontSize);
   ObjectSetString(0, labelName, OBJPROP_FONT, "Arial Bold");
   ObjectSetInteger(0, labelName, OBJPROP_ANCHOR, g_allDivs[idx].isBearish ? ANCHOR_LEFT_UPPER : ANCHOR_LEFT_LOWER);
   ObjectSetInteger(0, labelName, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, labelName, OBJPROP_HIDDEN, true);
   ObjectSetInteger(0, labelName, OBJPROP_ZORDER, 501);
}

//+------------------------------------------------------------------+
//| BUILD DISPLAY TEXT - PROFESSIONAL PANEL                           |
//+------------------------------------------------------------------+
void BuildDisplayText()
{
   datetime gmt7Now = GetGMT7Now();
   string todayDateStr = TimeToString(gmt7Now, TIME_DATE);
   string timeStr = TimeToString(gmt7Now, TIME_MINUTES|TIME_SECONDS);
   
   int rtIdx[];
   int histIdx[];
   int rtCount = 0, histCount = 0;
   
   ArrayResize(rtIdx, g_displayDivCount);
   ArrayResize(histIdx, g_displayDivCount);
   
   for(int i = 0; i < g_displayDivCount; i++)
   {
      if(!g_displayDivs[i].active) continue;
      if(g_displayDivs[i].isRT) rtIdx[rtCount++] = i;
      else histIdx[histCount++] = i;
   }
   
   for(int i = 0; i < rtCount - 1; i++)
      for(int j = i + 1; j < rtCount; j++)
         if(g_displayDivs[rtIdx[i]].time2 < g_displayDivs[rtIdx[j]].time2)
         {
            int tmp = rtIdx[i]; rtIdx[i] = rtIdx[j]; rtIdx[j] = tmp;
         }
   
   DrawDivergencePanel(rtIdx, rtCount, histIdx, histCount, todayDateStr, timeStr);
}

//+------------------------------------------------------------------+
//| DRAW DIVERGENCE PANEL - CHUYÊN NGHIỆP                            |
//+------------------------------------------------------------------+
void DrawDivergencePanel(int &rtIdx[], int rtCount, 
                         int &histIdx[], int histCount,
                         string todayDateStr, string timeStr)
{
   ENUM_BASE_CORNER corner = GetCorner(DivergencePanelPosition);
   int x = 15;
   int y = 20;
   int rowH = PanelLineSpacing;
   int fs = PanelFontSize;
   string pfx = g_panelPrefix;
   
   // === HEADER ===
   // ĐÃ SỬA: Hiển thị M15+H1 thay vì đầy đủ
   string tfDisplay = "";
   if(EnableM15) tfDisplay += "M15 ";
   if(EnableH1)  tfDisplay += "H1 ";
  // Cách khác
StringTrimRight(tfDisplay);
// tfDisplay đã được cắt khoảng trắng bên phải
   string headerText = StringFormat("📊 %s  |  %s  |  %s (GMT+7)  |  TF: %s", 
                                    g_currentSymbol, todayDateStr, timeStr, tfDisplay);
   UpdDivLabel(pfx + "HEADER", headerText, x, y, clrGold, fs + 1, corner);
   y += rowH + 4;
   
   // === SEPARATOR TRÊN ===
   UpdDivLabel(pfx + "SEP_T1", "──────────────────────────────────────────", 
               x, y, clrDarkGray, 7, corner);
   y += 2;
   UpdDivLabel(pfx + "SEP_T2", "──────────────────────────────────────────", 
               x, y, clrDarkGray, 7, corner);
   y += 12;
   
   int lineIdx = 0;
   bool hasContent = false;
   
   // ==========================================
   // REALTIME
   // ==========================================
   if(rtCount > 0)
   {
      UpdDivLabel(pfx + "SEC_RT", "🔴 REALTIME (đang chạy)", 
                  x, y, clrOrangeRed, fs + 1, corner);
      y += rowH + 6;
      hasContent = true;
      
      for(int s = 0; s < rtCount; s++)
      {
         int i = rtIdx[s];
         if(!g_displayDivs[i].active) continue;
         
         string tfStr = g_displayDivs[i].tfStr;
         string huong = g_displayDivs[i].isBearish ? "🔻 SELL" : "🔺 BUY";
         string dang = g_displayDivs[i].isReg ? "Reg" : "Hid";
         datetime gmt7Time = ServerTimeToGMT7(g_displayDivs[i].time2);
         string thoigian = TimeToString(gmt7Time, TIME_MINUTES);
         
         string line = StringFormat("  %-4s │ %-8s │ %-4s │ %s", 
                                    tfStr, huong, dang, thoigian);
         
         color c = g_displayDivs[i].isBearish ? clrDeepPink : clrLimeGreen;
         UpdDivLabel(pfx + "L" + IntegerToString(lineIdx), line, 
                     x, y, c, fs, corner);
         y += rowH;
         lineIdx++;
      }
   }
   
   // ==========================================
   // HISTORICAL
   // ==========================================
   if(histCount > 0)
   {
      if(rtCount > 0) y += 8;
      
      UpdDivLabel(pfx + "SEC_HIST", "📋 ĐÃ XÁC NHẬN (hôm nay)", 
                  x, y, clrSteelBlue, fs + 1, corner);
      y += rowH + 6;
      hasContent = true;
      
      // ĐÃ SỬA: Chỉ loop qua M15 và H1
      for(int t = 0; t < g_enabledTFCount; t++)
      {
         string tfStr = g_enabledTFStrs[t];
         datetime latestBearTime = 0, latestBullTime = 0;
         int bestBearIdx = -1, bestBullIdx = -1;
         
         for(int s = 0; s < histCount; s++)
         {
            int i = histIdx[s];
            if(!g_displayDivs[i].active) continue;
            if(g_displayDivs[i].tfStr != tfStr) continue;
            
            if(g_displayDivs[i].isBearish && g_displayDivs[i].time2 > latestBearTime)
            { latestBearTime = g_displayDivs[i].time2; bestBearIdx = i; }
            else if(!g_displayDivs[i].isBearish && g_displayDivs[i].time2 > latestBullTime)
            { latestBullTime = g_displayDivs[i].time2; bestBullIdx = i; }
         }
         
         if(bestBearIdx >= 0)
         {
            string huong = "🔻 SELL";
            string dang = g_displayDivs[bestBearIdx].isReg ? "Reg" : "Hid";
            datetime gmt7Time = ServerTimeToGMT7(g_displayDivs[bestBearIdx].time2);
            string thoigian = TimeToString(gmt7Time, TIME_MINUTES);
            
            string line = StringFormat("  %-4s │ %-8s │ %-4s │ %s", 
                                       tfStr, huong, dang, thoigian);
            UpdDivLabel(pfx + "L" + IntegerToString(lineIdx), line, 
                        x, y, clrDeepPink, fs, corner);
            y += rowH;
            lineIdx++;
         }
         
         if(bestBullIdx >= 0)
         {
            string huong = "🔺 BUY";
            string dang = g_displayDivs[bestBullIdx].isReg ? "Reg" : "Hid";
            datetime gmt7Time = ServerTimeToGMT7(g_displayDivs[bestBullIdx].time2);
            string thoigian = TimeToString(gmt7Time, TIME_MINUTES);
            
            string line = StringFormat("  %-4s │ %-8s │ %-4s │ %s", 
                                       tfStr, huong, dang, thoigian);
            UpdDivLabel(pfx + "L" + IntegerToString(lineIdx), line, 
                        x, y, clrLimeGreen, fs, corner);
            y += rowH;
            lineIdx++;
         }
      }
   }
   
   if(!hasContent)
   {
      UpdDivLabel(pfx + "NODATA", "📡 Chưa có tín hiệu phân kỳ nào hôm nay", 
                  x, y, clrDimGray, fs, corner);
      y += rowH;
   }
   else
   {
      ObjectDelete(0, pfx + "NODATA");
      if(rtCount == 0) ObjectDelete(0, pfx + "SEC_RT");
      if(histCount == 0) ObjectDelete(0, pfx + "SEC_HIST");
   }
   
   y += 10;
   UpdDivLabel(pfx + "SEP_B1", "──────────────────────────────────────────", 
               x, y, clrDarkGray, 7, corner);
   y += 2;
   UpdDivLabel(pfx + "SEP_B2", "──────────────────────────────────────────", 
               x, y, clrDarkGray, 7, corner);
   y += 12;
   
   string footerText = StringFormat("📊 Hôm nay: %d RT + %d Xác nhận  |  TF: M15+H1", rtCount, histCount);
   UpdDivLabel(pfx + "FOOTER", footerText, x, y, clrSilver, fs, corner);
   
   g_panelLineCount = lineIdx;
   CleanupPanelObjects(lineIdx, hasContent, rtCount, histCount);
}

//+------------------------------------------------------------------+
//| UPDATE DIV LABEL - HÀM TIỆN ÍCH                                  |
//+------------------------------------------------------------------+
void UpdDivLabel(string name, string text, int x, int y, color c, 
                 int fs = 9, ENUM_BASE_CORNER corner = CORNER_LEFT_UPPER)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_ZORDER, 600);
   }
   ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fs);
   ObjectSetInteger(0, name, OBJPROP_CORNER, corner);
   ObjectSetInteger(0, name, OBJPROP_COLOR, c);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

//+------------------------------------------------------------------+
//| CLEANUP PANEL OBJECTS                                            |
//+------------------------------------------------------------------+
void CleanupPanelObjects(int currentLineCount, bool hasContent, int rtCount, int histCount)
{
   string pfx = g_panelPrefix;
   string keepList[];
   ArrayResize(keepList, 0);
   
   AddToArray(keepList, pfx + "HEADER");
   AddToArray(keepList, pfx + "SEP_T1");
   AddToArray(keepList, pfx + "SEP_T2");
   AddToArray(keepList, pfx + "SEP_B1");
   AddToArray(keepList, pfx + "SEP_B2");
   AddToArray(keepList, pfx + "FOOTER");
   
   if(rtCount > 0)
      AddToArray(keepList, pfx + "SEC_RT");
   
   if(histCount > 0)
      AddToArray(keepList, pfx + "SEC_HIST");
   
   if(!hasContent)
      AddToArray(keepList, pfx + "NODATA");
   
   for(int i = 0; i < currentLineCount; i++)
      AddToArray(keepList, pfx + "L" + IntegerToString(i));
   
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i);
      if(StringFind(name, pfx) != 0) continue;
      
      bool keep = false;
      for(int k = 0; k < ArraySize(keepList); k++)
         if(name == keepList[k]) { keep = true; break; }
      
      if(!keep) ObjectDelete(0, name);
   }
}

//+------------------------------------------------------------------+
//| SYMBOL BUTTONS                                                    |
//+------------------------------------------------------------------+
void CreateSymbolButtons()
{
   ENUM_BASE_CORNER btnCorner = GetCorner(ButtonPosition);
   
   for(int i = 0; i < g_symbolCount; i++)
   {
      string btnName = g_buttonPrefix + g_symbolList[i];
      int x = ButtonStartX;
      int y = ButtonStartY + i * (ButtonHeight + 5);
      
      if(ObjectFind(0, btnName) >= 0) ObjectDelete(0, btnName);
      
      if(ObjectCreate(0, btnName, OBJ_BUTTON, 0, 0, 0))
      {
         ObjectSetInteger(0, btnName, OBJPROP_XDISTANCE, x);
         ObjectSetInteger(0, btnName, OBJPROP_YDISTANCE, y);
         ObjectSetInteger(0, btnName, OBJPROP_XSIZE, ButtonWidth);
         ObjectSetInteger(0, btnName, OBJPROP_YSIZE, ButtonHeight);
         ObjectSetInteger(0, btnName, OBJPROP_CORNER, btnCorner);
         ObjectSetInteger(0, btnName, OBJPROP_FONTSIZE, ButtonFontSize);
         ObjectSetString(0, btnName, OBJPROP_FONT, ButtonFontName);
         ObjectSetInteger(0, btnName, OBJPROP_COLOR, ButtonTextColor);
         ObjectSetInteger(0, btnName, OBJPROP_BORDER_COLOR, ButtonBorderColor);
         ObjectSetInteger(0, btnName, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, btnName, OBJPROP_ZORDER, 1000);
      }
   }
}

void UpdateSymbolButtons()
{
   ENUM_BASE_CORNER btnCorner = GetCorner(ButtonPosition);
   
   for(int i = 0; i < g_symbolCount; i++)
   {
      string btnName = g_buttonPrefix + g_symbolList[i];
      if(ObjectFind(0, btnName) < 0) continue;
      
      ObjectSetInteger(0, btnName, OBJPROP_CORNER, btnCorner);
      
      color bgColor;
      string btnText;
      
      if(g_symbolList[i] == g_currentSymbol)
      {
         bgColor = ActiveButtonColor;
         btnText = "► " + g_symbolList[i];
         if(g_symbolRTInfo[i].hasRT && g_symbolRTInfo[i].rtTFs != "")
            btnText += " " + g_symbolRTInfo[i].rtTFs;
         if(g_symbolRTInfo[i].hasHist)
            btnText += " 📋";
      }
      else if(g_symbolRTInfo[i].hasRT)
      {
         bgColor = RTDivButtonColor;
         btnText = "● " + g_symbolList[i] + " " + g_symbolRTInfo[i].rtTFs;
         if(g_symbolRTInfo[i].hasHist)
            btnText += " 📋";
      }
      else if(g_symbolRTInfo[i].hasHist)
      {
         bgColor = ButtonColor;
         btnText = g_symbolList[i] + " 📋";
      }
      else
      {
         bgColor = ButtonColor;
         btnText = g_symbolList[i];
      }
      
      ObjectSetInteger(0, btnName, OBJPROP_BGCOLOR, bgColor);
      ObjectSetString(0, btnName, OBJPROP_TEXT, btnText);
   }
}

//+------------------------------------------------------------------+
//| SCALE BUTTONS                                                     |
//+------------------------------------------------------------------+
void ScaleChart(int direction)
{
   long chartId = ChartID();
   long currentScale = ChartGetInteger(chartId, CHART_SCALE, 0);
   if(currentScale <= 0) currentScale = 3;
   if(direction > 0) { if(currentScale > 0) currentScale--; }
   else if(direction < 0) { if(currentScale < 5) currentScale++; }
   ChartSetInteger(chartId, CHART_SCALE, currentScale);
}

void CreateScaleButtons()
{
   ENUM_BASE_CORNER btnCorner = GetCorner(ButtonPosition);
   int sw = 60, sh = 25, gap = 10;
   int sx = ButtonStartX + ButtonWidth + 10;
   int sy = ButtonStartY;
   
   string plusBtn = g_scalePrefix + "PLUS";
   if(ObjectCreate(0, plusBtn, OBJ_BUTTON, 0, 0, 0))
   {
      ObjectSetInteger(0, plusBtn, OBJPROP_XDISTANCE, sx);
      ObjectSetInteger(0, plusBtn, OBJPROP_YDISTANCE, sy);
      ObjectSetInteger(0, plusBtn, OBJPROP_XSIZE, sw);
      ObjectSetInteger(0, plusBtn, OBJPROP_YSIZE, sh);
      ObjectSetInteger(0, plusBtn, OBJPROP_CORNER, btnCorner); 
      ObjectSetString(0, plusBtn, OBJPROP_TEXT, "🔍-");
      ObjectSetInteger(0, plusBtn, OBJPROP_FONTSIZE, 10);
      ObjectSetString(0, plusBtn, OBJPROP_FONT, "Arial");
      ObjectSetInteger(0, plusBtn, OBJPROP_COLOR, clrWhite);
      ObjectSetInteger(0, plusBtn, OBJPROP_BGCOLOR, clrDarkSlateGray);
      ObjectSetInteger(0, plusBtn, OBJPROP_BORDER_COLOR, ButtonBorderColor);
      ObjectSetInteger(0, plusBtn, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, plusBtn, OBJPROP_ZORDER, 1001);
   }
   
   string minusBtn = g_scalePrefix + "MINUS";
   if(ObjectCreate(0, minusBtn, OBJ_BUTTON, 0, 0, 0))
   {
      ObjectSetInteger(0, minusBtn, OBJPROP_XDISTANCE, sx + sw + gap);
      ObjectSetInteger(0, minusBtn, OBJPROP_YDISTANCE, sy);
      ObjectSetInteger(0, minusBtn, OBJPROP_XSIZE, sw);
      ObjectSetInteger(0, minusBtn, OBJPROP_YSIZE, sh);
      ObjectSetInteger(0, minusBtn, OBJPROP_CORNER, btnCorner);
      ObjectSetString(0, minusBtn, OBJPROP_TEXT, "🔍+");
      ObjectSetInteger(0, minusBtn, OBJPROP_FONTSIZE, 10);
      ObjectSetString(0, minusBtn, OBJPROP_FONT, "Arial");
      ObjectSetInteger(0, minusBtn, OBJPROP_COLOR, clrWhite);
      ObjectSetInteger(0, minusBtn, OBJPROP_BGCOLOR, clrDarkSlateGray);
      ObjectSetInteger(0, minusBtn, OBJPROP_BORDER_COLOR, ButtonBorderColor);
      ObjectSetInteger(0, minusBtn, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, minusBtn, OBJPROP_ZORDER, 1001);
   }
}

void UpdateScaleButtons()
{
   long chartId = ChartID();
   long currentScale = ChartGetInteger(chartId, CHART_SCALE, 0);
   if(currentScale <= 0) currentScale = 3;
   
   string plusBtn = g_scalePrefix + "PLUS";
   string minusBtn = g_scalePrefix + "MINUS";
   
   if(ObjectFind(0, plusBtn) >= 0)
      ObjectSetInteger(0, plusBtn, OBJPROP_BGCOLOR, currentScale <= 0 ? clrDimGray : clrDarkSlateGray);
   if(ObjectFind(0, minusBtn) >= 0)
      ObjectSetInteger(0, minusBtn, OBJPROP_BGCOLOR, currentScale >= 5 ? clrDimGray : clrDarkSlateGray);
}

//+------------------------------------------------------------------+
//| SWITCH SYMBOL                                                     |
//+------------------------------------------------------------------+
void SwitchSymbol(string newSymbol)
{
   ObjectsDeleteAll(0, g_divObjPrefix);
   ObjectsDeleteAll(0, g_D1ObjPrefix);
   
   if(ChartSetSymbolPeriod(0, newSymbol, _Period))
   {
      g_currentSymbol = newSymbol;
      g_lastD1PanelUpdate = 0;
      g_lastDataCheck = 0;
      g_divCount = 0;
      g_displayDivCount = 0;
      g_panelLineCount = 0;
      Print("✅ Switched to ", newSymbol);
   }
   else
      Print("❌ Failed to switch to ", newSymbol);
}

//+------------------------------------------------------------------+
//| D1 PANEL (ZERO-DIVIDE SAFE)                                      |
//+------------------------------------------------------------------+
bool UpdateD1Panel()
{
   if(!show_d1_panel) return true;
   if(PeriodSeconds(_Period) >= PeriodSeconds(PERIOD_D1)) return true;
   if(g_isChartBusy) return false;
   
   double o[], h[], l[], c[];
   ArraySetAsSeries(o, true); ArraySetAsSeries(h, true);
   ArraySetAsSeries(l, true); ArraySetAsSeries(c, true);
   
   if(CopyOpen(g_currentSymbol, PERIOD_D1, 0, d1_candleCount, o) < d1_candleCount) return false;
   if(CopyHigh(g_currentSymbol, PERIOD_D1, 0, d1_candleCount, h) < d1_candleCount) return false;
   if(CopyLow(g_currentSymbol, PERIOD_D1, 0, d1_candleCount, l) < d1_candleCount) return false;
   if(CopyClose(g_currentSymbol, PERIOD_D1, 0, d1_candleCount, c) < d1_candleCount) return false;
   
   double current_price = SymbolInfoDouble(g_currentSymbol, SYMBOL_BID);
   if(current_price <= 0) return false;
   
   h[0] = MathMax(h[0], current_price);
   l[0] = MathMin(l[0], current_price);
   c[0] = current_price;
   
   double hi_all = h[ArrayMaximum(h, 0, d1_candleCount)];
   double lo_all = l[ArrayMinimum(l, 0, d1_candleCount)];
   double range_all = (hi_all - lo_all == 0) ? 1.0 : (hi_all - lo_all);
   
   long chartId = ChartID();
   long chartHeight = ChartGetInteger(chartId, CHART_HEIGHT_IN_PIXELS, 0);
   long chartWidth = ChartGetInteger(chartId, CHART_WIDTH_IN_PIXELS, 0);
   
   if(chartHeight <= 0) return false;
   if(chartWidth <= 0) return false;
   
   int visibleBars = (int)ChartGetInteger(chartId, CHART_VISIBLE_BARS, 0);
   if(visibleBars <= 0) visibleBars = 100;
   
   int totalBars = Bars(g_currentSymbol, _Period);
   if(totalBars < 2) return false;
   
   double chartHigh = iHigh(g_currentSymbol, _Period, 0);
   double chartLow = iLow(g_currentSymbol, _Period, 0);
   int checkBars = MathMin(visibleBars, totalBars);
   
   for(int i = 1; i < checkBars; i++)
   {
      double hh = iHigh(g_currentSymbol, _Period, i);
      double ll = iLow(g_currentSymbol, _Period, i);
      if(hh > chartHigh) chartHigh = hh;
      if(ll < chartLow) chartLow = ll;
   }
   
   double chartRange = chartHigh - chartLow;
   if(chartRange <= 0)
   {
      chartRange = range_all;
      if(chartRange <= 0) chartRange = current_price * 0.01;
      if(chartRange <= 0) return false;
   }
   
   double panelHeightPx = chartHeight * (d1_PanelHeightPct / 100.0);
   if(panelHeightPx <= 0) return false;
   
   double pxPerPrice = chartHeight / chartRange;
   double p_h = panelHeightPx / pxPerPrice;
   
   double b_y = current_price - ((c[0] - lo_all) / range_all * p_h);
   
   datetime barTime = iTime(g_currentSymbol, _Period, 0);
   if(barTime == 0) return false;
   
   int periodSeconds = PeriodSeconds(_Period);
   
   datetime time0 = iTime(g_currentSymbol, _Period, 0);
   datetime time1 = iTime(g_currentSymbol, _Period, 1);
   
   double oneBarWidthPx = 0;
   if(time0 > 0 && time1 > 0)
   {
      int x0, y0, x1, y1;
      if(ChartTimePriceToXY(chartId, 0, time0, 0, x0, y0) && 
         ChartTimePriceToXY(chartId, 0, time1, 0, x1, y1))
      {
         oneBarWidthPx = MathAbs(x0 - x1);
      }
   }
   
   if(oneBarWidthPx <= 0)
   {
      if(visibleBars > 0)
         oneBarWidthPx = (double)chartWidth / (double)visibleBars;
      else
         oneBarWidthPx = 10;
   }
   
   double barsForPanel = 5.0;
   double smartPanelWidthPx = oneBarWidthPx * barsForPanel;
   
   if(smartPanelWidthPx < 120) smartPanelWidthPx = 120;
   if(smartPanelWidthPx > 300) smartPanelWidthPx = 300;
   
   double totalCandlesPx = d1_candleCount * (d1_BodyWidth * 2 + d1_GapBetween) - d1_GapBetween;
   if(totalCandlesPx <= 0) totalCandlesPx = 1;
   
   double scaleRatio = smartPanelWidthPx / totalCandlesPx;
   
   int scaledBodyW = (int)(d1_BodyWidth * scaleRatio);
   int scaledGap = (int)(d1_GapBetween * scaleRatio);
   if(scaledBodyW < 1) scaledBodyW = 1;
   if(scaledGap < 1) scaledGap = 1;
   
   double pxPerBar = (double)chartWidth / (double)visibleBars;
   if(pxPerBar <= 0) pxPerBar = 1;
   
   int panelOffsetBars = 6;
   datetime panelStartTime = barTime + panelOffsetBars * periodSeconds;
   double scaledTotalPx = d1_candleCount * (scaledBodyW * 2 + scaledGap) - scaledGap;
   datetime panelEndTime = panelStartTime + (int)(scaledTotalPx / pxPerBar * periodSeconds);
   
   int paddingBars = 2;
   datetime bgStart = panelStartTime - paddingBars * periodSeconds;
   datetime bgEnd = panelEndTime + paddingBars * periodSeconds;
   
   string panelBox = g_D1ObjPrefix + "BG";
   if(ObjectFind(0, panelBox) < 0)
   {
      if(!ObjectCreate(0, panelBox, OBJ_RECTANGLE, 0, bgStart, b_y + p_h, bgEnd, b_y)) return false;
      ObjectSetInteger(0, panelBox, OBJPROP_BGCOLOR, d1_PanelBgColor);
      ObjectSetInteger(0, panelBox, OBJPROP_COLOR, clrDimGray);
      ObjectSetInteger(0, panelBox, OBJPROP_WIDTH, 1);
      ObjectSetInteger(0, panelBox, OBJPROP_BACK, true);
      ObjectSetInteger(0, panelBox, OBJPROP_HIDDEN, false);
      ObjectSetInteger(0, panelBox, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, panelBox, OBJPROP_ZORDER, 100);
   }
   else
   {
      ObjectSetInteger(0, panelBox, OBJPROP_TIME, 0, bgStart);
      ObjectSetDouble(0, panelBox, OBJPROP_PRICE, 0, b_y + p_h);
      ObjectSetInteger(0, panelBox, OBJPROP_TIME, 1, bgEnd);
      ObjectSetDouble(0, panelBox, OBJPROP_PRICE, 1, b_y);
   }
   
   for(int i = 0; i < d1_candleCount; i++)
   {
      int idx = d1_flipDirection ? i : (d1_candleCount - 1 - i);
      
      double candleOffsetPx = idx * (scaledBodyW * 2 + scaledGap) + scaledBodyW;
      datetime cx = panelStartTime + (int)(candleOffsetPx / pxPerBar * periodSeconds);
      double halfW = ((double)scaledBodyW / pxPerBar) * periodSeconds;
      if(halfW <= 0) continue;
      
      double hi = h[i], lo = l[i], op = o[i], cl = c[i];
      bool isBull = (cl >= op);
      color cColor = isBull ? d1_BullBodyColor : d1_BearBodyColor;
      
      double bodyTopPrice = MathMax(op, cl), bodyBotPrice = MathMin(op, cl);
      double bodyTop = b_y + (bodyTopPrice - lo_all) / range_all * p_h;
      double bodyBot = b_y + (bodyBotPrice - lo_all) / range_all * p_h;
      datetime bodyLeft = cx - (int)halfW, bodyRight = cx + (int)halfW;
      
      string wName = g_D1ObjPrefix + "WU_" + IntegerToString(i);
      double wTop = b_y + (hi - lo_all) / range_all * p_h;
      if(wTop > bodyTop && (wTop - bodyTop) > _Point)
      {
         if(ObjectFind(0, wName) < 0) ObjectCreate(0, wName, OBJ_TREND, 0, cx, wTop, cx, bodyTop);
         else { ObjectSetInteger(0, wName, OBJPROP_TIME, 0, cx); ObjectSetDouble(0, wName, OBJPROP_PRICE, 0, wTop);
                ObjectSetInteger(0, wName, OBJPROP_TIME, 1, cx); ObjectSetDouble(0, wName, OBJPROP_PRICE, 1, bodyTop); }
         ObjectSetInteger(0, wName, OBJPROP_COLOR, cColor); ObjectSetInteger(0, wName, OBJPROP_WIDTH, 2);
         ObjectSetInteger(0, wName, OBJPROP_HIDDEN, false);
      }
      else ObjectDelete(0, wName);
      
      wName = g_D1ObjPrefix + "WL_" + IntegerToString(i);
      double wBot = b_y + (lo - lo_all) / range_all * p_h;
      if(wBot < bodyBot && (bodyBot - wBot) > _Point)
      {
         if(ObjectFind(0, wName) < 0) ObjectCreate(0, wName, OBJ_TREND, 0, cx, bodyBot, cx, wBot);
         else { ObjectSetInteger(0, wName, OBJPROP_TIME, 0, cx); ObjectSetDouble(0, wName, OBJPROP_PRICE, 0, bodyBot);
                ObjectSetInteger(0, wName, OBJPROP_TIME, 1, cx); ObjectSetDouble(0, wName, OBJPROP_PRICE, 1, wBot); }
         ObjectSetInteger(0, wName, OBJPROP_COLOR, cColor); ObjectSetInteger(0, wName, OBJPROP_WIDTH, 2);
         ObjectSetInteger(0, wName, OBJPROP_HIDDEN, false);
      }
      else ObjectDelete(0, wName);
      
      wName = g_D1ObjPrefix + "BD_" + IntegerToString(i);
      if(bodyTop > bodyBot && (bodyRight - bodyLeft) > 0 && (bodyTop - bodyBot) > _Point)
      {
         if(ObjectFind(0, wName) < 0) ObjectCreate(0, wName, OBJ_RECTANGLE, 0, bodyLeft, bodyTop, bodyRight, bodyBot);
         else { ObjectSetInteger(0, wName, OBJPROP_TIME, 0, bodyLeft); ObjectSetDouble(0, wName, OBJPROP_PRICE, 0, bodyTop);
                ObjectSetInteger(0, wName, OBJPROP_TIME, 1, bodyRight); ObjectSetDouble(0, wName, OBJPROP_PRICE, 1, bodyBot); }
         ObjectSetInteger(0, wName, OBJPROP_BGCOLOR, cColor); ObjectSetInteger(0, wName, OBJPROP_COLOR, cColor);
         ObjectSetInteger(0, wName, OBJPROP_HIDDEN, false);
      }
      else ObjectDelete(0, wName);
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| CLEAR ALL OBJECTS                                                 |
//+------------------------------------------------------------------+
void ClearAllObjects()
{
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
   {
      string n = ObjectName(0, i);
      if(StringFind(n, P) == 0 || StringFind(n, g_D1ObjPrefix) == 0 || 
         StringFind(n, g_divObjPrefix) == 0 || StringFind(n, g_buttonPrefix) == 0 || 
         StringFind(n, g_scalePrefix) == 0 || StringFind(n, g_zoomMsgPrefix) == 0 ||
         StringFind(n, g_panelPrefix) == 0)
         ObjectDelete(0, n);
   }
   ChartRedraw(0);
}
//+------------------------------------------------------------------+