//+------------------------------------------------------------------+
//|                                    RSI_Volumes_Divergence.mq5     |
//|                          VERSION 2.3 - TỐI ƯU + FIX SWITCH SYMBOL |
//+------------------------------------------------------------------+
#property copyright   "Copyright 2000-2026, MetaQuotes Ltd."
#property link        "https://www.mql5.com"
#property description "RSI(Close) + Volume + Divergence Lines (Tối ưu)"

#property indicator_separate_window
#property indicator_minimum 10
#property indicator_maximum 90
#property indicator_level1  30
#property indicator_level2  70
#property indicator_buffers 5
#property indicator_plots   2

#property indicator_type1   DRAW_COLOR_HISTOGRAM
#property indicator_color1  clrGray,clrLightGray,clrYellow
#property indicator_style1  STYLE_SOLID
#property indicator_width1  2
#property indicator_label1  "Volume"

#property indicator_type2   DRAW_LINE
#property indicator_color2  clrDodgerBlue
#property indicator_width2  2
#property indicator_label2  "RSI Close"

input int                 InpPeriodRSI   = 14;
input ENUM_APPLIED_VOLUME InpVolumeType  = VOLUME_TICK;
input int                 InpMAPeriod    = 10;

input group "===== Divergence Lines ====="
input bool               ShowDivergence = true;
input string             ScanDataPrefix = "DIVDATA_";
input color              BearDivColor   = clrDeepPink;
input color              BullDivColor   = clrLime;
input int                DivLineWidth   = 2;
input int                RTLineWidth    = 3;
input bool               ShowDivLabels  = true;

input group "===== Anti-Zoom Protection ====="
input bool               EnableAntiZoomProtection = true;
input int                ZoomDebounceMs = 2000;
input int                DivUpdateIntervalSec = 3;

double    ExtVolumesBuffer[];
double    ExtColorsBuffer[];
double    ExtRSIBuffer[];
double    ExtPosBuffer[];
double    ExtNegBuffer[];

int       ExtPeriodRSI;
int       g_rates_total = 0;
long      g_tick_volume[];
long      g_volume[];

string    g_divPrefix = "RSIVOL_DIV_";
datetime  g_lastDivUpdate = 0;
string    g_currentTFStr = "";
string    g_lastSymbol = "";

// Cache
datetime  g_lastDivDataTime = 0;
int       g_lastDivCount = -1;

bool      g_isChartBusy = false;
datetime  g_chartBusyStartTime = 0;
bool      g_volumeNeedsRecalc = false;
int       g_zoomCount = 0;

string GetCurrentTFString()
{
   ENUM_TIMEFRAMES tf = Period();
   switch(tf)
   {
      case PERIOD_M1:  return "M1";  case PERIOD_M5:  return "M5";
      case PERIOD_M15: return "M15"; case PERIOD_M30: return "M30";
      case PERIOD_H1:  return "H1";  case PERIOD_H4:  return "H4";
      case PERIOD_D1:  return "D1";  case PERIOD_W1:  return "W1";
      case PERIOD_MN1: return "MN";  default:         return "M15";
   }
}

void OnInit()
{
   ExtPeriodRSI = (InpPeriodRSI < 1) ? 14 : InpPeriodRSI;

   SetIndexBuffer(0, ExtVolumesBuffer, INDICATOR_DATA);
   SetIndexBuffer(1, ExtColorsBuffer, INDICATOR_COLOR_INDEX);
   SetIndexBuffer(2, ExtRSIBuffer, INDICATOR_DATA);
   SetIndexBuffer(3, ExtPosBuffer, INDICATOR_CALCULATIONS);
   SetIndexBuffer(4, ExtNegBuffer, INDICATOR_CALCULATIONS);

   IndicatorSetInteger(INDICATOR_DIGITS, 2);
   PlotIndexSetInteger(0, PLOT_DRAW_BEGIN, 0);
   PlotIndexSetInteger(1, PLOT_DRAW_BEGIN, ExtPeriodRSI);
   PlotIndexSetInteger(0, PLOT_LINE_COLOR, 0, clrGray);
   PlotIndexSetInteger(0, PLOT_LINE_COLOR, 1, clrLightGray);
   PlotIndexSetInteger(0, PLOT_LINE_COLOR, 2, clrYellow);
   
   g_lastSymbol = _Symbol;
   g_currentTFStr = GetCurrentTFString();
   IndicatorSetString(INDICATOR_SHORTNAME, "RSI(" + string(ExtPeriodRSI) + ") + Vol v2.3");
   
   g_isChartBusy = false;
   g_volumeNeedsRecalc = false;
   g_lastDivUpdate = 0;
   g_lastDivDataTime = 0;
   g_lastDivCount = -1;
   g_zoomCount = 0;
   
   ObjectsDeleteAll(0, g_divPrefix);
   EventSetTimer(1);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectsDeleteAll(0, g_divPrefix);
}

void OnTimer()
{
 GlobalVariableSet("HB_RSI_Volumes", (double)TimeCurrent());
   
   // ===== PHÁT HIỆN CHUYỂN SYMBOL =====
   if(_Symbol != g_lastSymbol)
   {
      g_lastSymbol = _Symbol;
      g_currentTFStr = GetCurrentTFString();
      
      // Reset cache
      g_lastDivDataTime = 0;
      g_lastDivCount = -1;
      
      // Recalc volume cho symbol mới
      if(g_rates_total > 0)
         RecalculateVolumes();
   }
   
   // ===== KIỂM TRA ZOOM =====
   if(g_isChartBusy && EnableAntiZoomProtection)
   {
      int debounceSeconds = ZoomDebounceMs / 1000;
      if(debounceSeconds < 1) debounceSeconds = 1;
      
      if(TimeCurrent() - g_chartBusyStartTime >= debounceSeconds)
      {
         g_isChartBusy = false;
         g_zoomCount = 0;
         if(g_volumeNeedsRecalc)
         {
            RecalculateVolumes();
            g_volumeNeedsRecalc = false;
         }
         g_lastDivDataTime = 0;
         g_lastDivCount = -1;
      }
      else
      {
         return;
      }
   }
   
   if(!ShowDivergence) return;
   if(TimeCurrent() - g_lastDivUpdate < DivUpdateIntervalSec) return;
   g_lastDivUpdate = TimeCurrent();
   
   // ===== CACHE: CHỈ CẬP NHẬT NẾU DỮ LIỆU THAY ĐỔI =====
   string tfStr = g_currentTFStr;
   string prefix = ScanDataPrefix + _Symbol + "_" + tfStr + "_";
   
   datetime latestDataTime = 0;
   if(GlobalVariableCheck(prefix + "BEAR_TIME"))
      latestDataTime = MathMax(latestDataTime, (datetime)GlobalVariableGet(prefix + "BEAR_TIME"));
   if(GlobalVariableCheck(prefix + "BULL_TIME"))
      latestDataTime = MathMax(latestDataTime, (datetime)GlobalVariableGet(prefix + "BULL_TIME"));
   
   if(latestDataTime == g_lastDivDataTime && g_lastDivCount >= 0)
      return;
   
   g_lastDivDataTime = latestDataTime;
   ObjectsDeleteAll(0, g_divPrefix);
   
   int divCount = 0;
   
   // === REALTIME BEAR ===
   if(GlobalVariableCheck(prefix + "BEAR_ACTIVE") && GlobalVariableGet(prefix + "BEAR_ACTIVE") == 1)
   {
      if(GlobalVariableCheck(prefix + "BEAR_TIME"))
      {
         datetime lastUpdate = (datetime)GlobalVariableGet(prefix + "BEAR_TIME");
         if(TimeCurrent() - lastUpdate <= 15)
         {
            DrawLine((datetime)GlobalVariableGet(prefix + "BEAR_T1"), GlobalVariableGet(prefix + "BEAR_RSI1"),
                     (datetime)GlobalVariableGet(prefix + "BEAR_T2"), GlobalVariableGet(prefix + "BEAR_RSI2"),
                     true, (GlobalVariableGet(prefix + "BEAR_ISREG") == 1), true, tfStr + "_Bear_RT");
            divCount++;
         }
      }
   }
   
   // === REALTIME BULL ===
   if(GlobalVariableCheck(prefix + "BULL_ACTIVE") && GlobalVariableGet(prefix + "BULL_ACTIVE") == 1)
   {
      if(GlobalVariableCheck(prefix + "BULL_TIME"))
      {
         datetime lastUpdate = (datetime)GlobalVariableGet(prefix + "BULL_TIME");
         if(TimeCurrent() - lastUpdate <= 15)
         {
            DrawLine((datetime)GlobalVariableGet(prefix + "BULL_T1"), GlobalVariableGet(prefix + "BULL_RSI1"),
                     (datetime)GlobalVariableGet(prefix + "BULL_T2"), GlobalVariableGet(prefix + "BULL_RSI2"),
                     false, (GlobalVariableGet(prefix + "BULL_ISREG") == 1), true, tfStr + "_Bull_RT");
            divCount++;
         }
      }
   }
   
   // === HISTORICAL BEAR ===
   datetime latestBearTime = 0;
   int bestBearSlot = -1;
   for(int i = 0; i < 20; i++)
   {
      string key = prefix + "HIST_BEAR_" + IntegerToString(i) + "_ACTIVE";
      if(GlobalVariableCheck(key) && GlobalVariableGet(key) == 1)
      {
         string timeKey = prefix + "HIST_BEAR_" + IntegerToString(i) + "_TIME";
         if(GlobalVariableCheck(timeKey))
         {
            datetime tt = (datetime)GlobalVariableGet(timeKey);
            if(tt > latestBearTime) { latestBearTime = tt; bestBearSlot = i; }
         }
      }
   }
   if(bestBearSlot >= 0)
   {
      string base = prefix + "HIST_BEAR_" + IntegerToString(bestBearSlot) + "_";
      DrawLine((datetime)GlobalVariableGet(base + "T1"), GlobalVariableGet(base + "RSI1"),
               latestBearTime, GlobalVariableGet(base + "RSI2"),
               true, (GlobalVariableGet(base + "ISREG") == 1), false, tfStr + "_Bear_HIST");
      divCount++;
   }
   
   // === HISTORICAL BULL ===
   datetime latestBullTime = 0;
   int bestBullSlot = -1;
   for(int i = 0; i < 20; i++)
   {
      string key = prefix + "HIST_BULL_" + IntegerToString(i) + "_ACTIVE";
      if(GlobalVariableCheck(key) && GlobalVariableGet(key) == 1)
      {
         string timeKey = prefix + "HIST_BULL_" + IntegerToString(i) + "_TIME";
         if(GlobalVariableCheck(timeKey))
         {
            datetime tt = (datetime)GlobalVariableGet(timeKey);
            if(tt > latestBullTime) { latestBullTime = tt; bestBullSlot = i; }
         }
      }
   }
   if(bestBullSlot >= 0)
   {
      string base = prefix + "HIST_BULL_" + IntegerToString(bestBullSlot) + "_";
      DrawLine((datetime)GlobalVariableGet(base + "T1"), GlobalVariableGet(base + "RSI1"),
               latestBullTime, GlobalVariableGet(base + "RSI2"),
               false, (GlobalVariableGet(base + "ISREG") == 1), false, tfStr + "_Bull_HIST");
      divCount++;
   }
   
   g_lastDivCount = divCount;
}

void DrawLine(datetime t1, double rsi1, datetime t2, double rsi2,
              bool isBearish, bool isRegular, bool isRT, string suffix)
{
   if(t1 == 0 || t2 == 0) return;
   if(rsi1 < 5 || rsi1 > 95 || rsi2 < 5 || rsi2 > 95) return;
   
   color divColor = isBearish ? BearDivColor : BullDivColor;
   string lineName = g_divPrefix + suffix;
   
   if(ObjectFind(0, lineName) < 0)
   {
      if(!ObjectCreate(0, lineName, OBJ_TREND, 1, t1, rsi1, t2, rsi2)) return;
      ObjectSetInteger(0, lineName, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, lineName, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, lineName, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, lineName, OBJPROP_ZORDER, 500);
   }
   
   ObjectSetInteger(0, lineName, OBJPROP_TIME, 0, t1);
   ObjectSetDouble(0, lineName, OBJPROP_PRICE, 0, rsi1);
   ObjectSetInteger(0, lineName, OBJPROP_TIME, 1, t2);
   ObjectSetDouble(0, lineName, OBJPROP_PRICE, 1, rsi2);
   ObjectSetInteger(0, lineName, OBJPROP_COLOR, divColor);
   ObjectSetInteger(0, lineName, OBJPROP_WIDTH, isRT ? RTLineWidth : DivLineWidth);
   ObjectSetInteger(0, lineName, OBJPROP_STYLE, isRegular ? STYLE_SOLID : STYLE_DASH);
   
   if(ShowDivLabels)
   {
      string divType = isRegular ? "Reg" : "Hid";
      string divDir = isBearish ? "Bear" : "Bull";
      string labelName = g_divPrefix + "LBL_" + suffix;
      string labelText = g_currentTFStr + " " + divDir + " " + divType;
      if(isRT) labelText = "🔴" + labelText;
      
      double labelY = isBearish ? MathMax(rsi1, rsi2) + 2.5 : MathMin(rsi1, rsi2) - 2.5;
      labelY = MathMax(11, MathMin(89, labelY));
      int anchor = isBearish ? ANCHOR_LOWER : ANCHOR_UPPER;
      
      if(ObjectFind(0, labelName) < 0)
      {
         if(!ObjectCreate(0, labelName, OBJ_TEXT, 1, t2, labelY)) return;
         ObjectSetInteger(0, labelName, OBJPROP_FONTSIZE, isRT ? 8 : 7);
         ObjectSetString(0, labelName, OBJPROP_FONT, "Arial Bold");
         ObjectSetInteger(0, labelName, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, labelName, OBJPROP_HIDDEN, true);
         ObjectSetInteger(0, labelName, OBJPROP_ZORDER, 501);
      }
      
      ObjectSetString(0, labelName, OBJPROP_TEXT, labelText);
      ObjectSetInteger(0, labelName, OBJPROP_COLOR, divColor);
      ObjectSetInteger(0, labelName, OBJPROP_TIME, 0, t2);
      ObjectSetDouble(0, labelName, OBJPROP_PRICE, 0, labelY);
      ObjectSetInteger(0, labelName, OBJPROP_ANCHOR, anchor);
   }
}

//+------------------------------------------------------------------+
//| RecalculateVolumes - Scale 10-30, fix switch symbol              |
//+------------------------------------------------------------------+
void RecalculateVolumes()
{
   if(g_rates_total == 0) return;
   if(g_isChartBusy && EnableAntiZoomProtection) { g_volumeNeedsRecalc = true; return; }
   
   // Tìm max volume trong vùng visible
   int visible_bars = (int)ChartGetInteger(0, CHART_VISIBLE_BARS);
   int first_visible = (int)ChartGetInteger(0, CHART_FIRST_VISIBLE_BAR); 
   int left_idx = g_rates_total - 1 - first_visible;
   int right_idx = left_idx + visible_bars;
   if(left_idx < 0) left_idx = 0;
   if(right_idx > g_rates_total) right_idx = g_rates_total;
   
   double max_vol = 0.0;
   if(left_idx < right_idx)
   {
      for(int i = left_idx; i < right_idx; i++)
      {
         double v = (InpVolumeType == VOLUME_TICK) ? (double)g_tick_volume[i] : (double)g_volume[i];
         if(v > max_vol) max_vol = v;
      }
   }
   
   // Fallback: quét toàn bộ nếu visible = 0
   if(max_vol <= 0.0)
   {
      for(int i = 0; i < g_rates_total; i++)
      {
         double v = (InpVolumeType == VOLUME_TICK) ? (double)g_tick_volume[i] : (double)g_volume[i];
         if(v > max_vol) max_vol = v;
      }
   }
   if(max_vol == 0) max_vol = 1.0;
   
   // Scale TOÀN BỘ bars về khoảng 10-30
   for(int i = 0; i < g_rates_total && !IsStopped(); i++)
   {
      double curr_vol = (InpVolumeType == VOLUME_TICK) ? (double)g_tick_volume[i] : (double)g_volume[i];
      double scaled = 10.0 + (curr_vol / max_vol) * 20.0;
      if(scaled > 30.0) scaled = 30.0;
      ExtVolumesBuffer[i] = scaled;
   }
   
   g_volumeNeedsRecalc = false;
}

//+------------------------------------------------------------------+
//| OnChartEvent                                                      |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id == CHARTEVENT_CHART_CHANGE)
   {
      g_zoomCount++;
      
      if(EnableAntiZoomProtection)
      {
         if(!g_isChartBusy)
         {
            g_isChartBusy = true;
            g_chartBusyStartTime = TimeCurrent();
         }
         g_volumeNeedsRecalc = true;
      }
   }
}

//+------------------------------------------------------------------+
//| OnCalculate                                                       |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
   if(rates_total <= ExtPeriodRSI) return(0);
   
   // Phát hiện chuyển symbol hoặc load mới
   bool isNewSymbol = (_Symbol != g_lastSymbol);
   bool isNewLoad = (prev_calculated == 0);
   
   if(isNewSymbol || isNewLoad)
   {
      g_lastSymbol = _Symbol;
      g_currentTFStr = GetCurrentTFString();
   }
   
   g_rates_total = rates_total;
   ArrayResize(g_tick_volume, rates_total);
   ArrayResize(g_volume, rates_total);
   
   // Copy TOÀN BỘ volume data khi load mới hoặc chuyển symbol
   int start = (isNewLoad || isNewSymbol) ? 0 : prev_calculated - 1;
   
   for(int i = start; i < rates_total; i++)
   {
      g_tick_volume[i] = tick_volume[i];
      g_volume[i] = volume[i];
   }
   
   if(isNewLoad || isNewSymbol)
   {
      ArrayInitialize(ExtRSIBuffer, 0.0);
      ArrayInitialize(ExtPosBuffer, 0.0);
      ArrayInitialize(ExtNegBuffer, 0.0);
      ArrayInitialize(ExtVolumesBuffer, 0.0);
      ArrayInitialize(ExtColorsBuffer, 0.0);
   }

   // Color Volume
   for(int i = start; i < rates_total && !IsStopped(); i++)
   {
      double curr_vol = (InpVolumeType == VOLUME_TICK) ? (double)g_tick_volume[i] : (double)g_volume[i];
      double sum = 0; int cnt = 0;
      int jStart = MathMax(0, i - InpMAPeriod + 1);
      for(int j = jStart; j <= i; j++)
      {
         double vol_j = (InpVolumeType == VOLUME_TICK) ? (double)g_tick_volume[j] : (double)g_volume[j];
         sum += vol_j; cnt++;
      }
      double ma_volume = (cnt > 0) ? sum / cnt : curr_vol;
      double ratio = (ma_volume > 0) ? curr_vol / ma_volume : 1.0;
      if(ratio >= 1.25)       ExtColorsBuffer[i] = 2.0; 
      else if(ratio >= 1.0)   ExtColorsBuffer[i] = 1.0; 
      else                    ExtColorsBuffer[i] = 0.0; 
   }

   // RSI Close
   int pos = prev_calculated - 1;
   if(pos < ExtPeriodRSI || isNewSymbol)
   {
      double sum_pos = 0.0, sum_neg = 0.0;
      for(int i = 1; i <= ExtPeriodRSI; i++)
      {
         double diff = close[i] - close[i-1];
         sum_pos += (diff > 0 ? diff : 0); 
         sum_neg += (diff < 0 ? -diff : 0);
      }
      ExtPosBuffer[ExtPeriodRSI] = sum_pos / ExtPeriodRSI;
      ExtNegBuffer[ExtPeriodRSI] = sum_neg / ExtPeriodRSI;
      ExtRSIBuffer[ExtPeriodRSI] = (ExtNegBuffer[ExtPeriodRSI] != 0.0) ? 
         100.0 - (100.0 / (1.0 + ExtPosBuffer[ExtPeriodRSI] / ExtNegBuffer[ExtPeriodRSI])) : 50.0;
      pos = ExtPeriodRSI + 1;
   }
   
   for(int i = pos; i < rates_total && !IsStopped(); i++)
   {
      double diff = close[i] - close[i-1];
      double gain = (diff > 0.0 ? diff : 0.0);
      double loss = (diff < 0.0 ? -diff : 0.0);
      ExtPosBuffer[i] = (ExtPosBuffer[i-1] * (ExtPeriodRSI - 1) + gain) / ExtPeriodRSI;
      ExtNegBuffer[i] = (ExtNegBuffer[i-1] * (ExtPeriodRSI - 1) + loss) / ExtPeriodRSI;
      ExtRSIBuffer[i] = (ExtNegBuffer[i] != 0.0) ? 
         100.0 - 100.0 / (1.0 + ExtPosBuffer[i] / ExtNegBuffer[i]) : 50.0;
   }

   // Recalc volume khi load mới, chuyển symbol, hoặc có bar mới (không zoom)
   if(isNewLoad || isNewSymbol || (!g_isChartBusy && rates_total > prev_calculated))
      RecalculateVolumes();
   else if(g_isChartBusy)
      g_volumeNeedsRecalc = true;

   return(rates_total);
}
//+------------------------------------------------------------------+