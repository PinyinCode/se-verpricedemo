//+------------------------------------------------------------------+
//|                                              Watchdog_Indi.mq5   |
//|                                  Multi-Symbol Commission Detector |
//|                                  CHỈ SCAN SYMBOL TRONG MARKETWATCH|
//+------------------------------------------------------------------+
#property copyright "Watchdog Indicator v2.36"
#property version   "2.36"
#property indicator_chart_window
#property indicator_buffers 1
#property indicator_plots   1
#property indicator_type1   DRAW_NONE

input group "===== Monitoring Settings =====";
input int CheckIntervalSeconds = 30;
input int CrashTimeoutMinutes = 10;
input int AlertRepeatMinutes = 60;
input bool EnablePushNotify = true;
input bool EnableSoundAlert = true;

input group "===== Monitored Components =====";
input bool Monitor_RSI_Indicator = true;
input bool Monitor_RSI_Volumes = true;
input bool Monitor_RSI_EA = true;
input bool Monitor_TradeManager = true;
input bool Monitor_NewsFilter = true;                 // Theo dõi NewsTimeFilter
input string NewsFilter_GV_Name = "GV_NewsFilter_Heartbeat"; // Tên GV heartbeat

input group "===== cTrader Monitoring =====";
input bool Monitor_cTrader = false;
input int cTrader_MagicNumber = 12345;
input string cTrader_HeartbeatFileName = "Heartbeat";

input group "===== Commission Settings =====";
input bool EnableAutoCommission = true;
input bool ScanAllMarketWatch = false;
input bool UseCustomSymbols = true;
input string CustomSymbols = "XAUUSD,USDJPY,EURUSD,GBPUSD";
input double DefaultCommission = 5.0;
input int CommissionCheckMinutes = 10;

input group "===== Display Settings =====";
input ENUM_BASE_CORNER PanelCorner = CORNER_RIGHT_LOWER;
input int PanelMarginX = 10;
input int PanelMarginY = 35;
input int FontSize = 9;
input int LineSpacing = 15;
input ENUM_BASE_CORNER LeftPanelCorner = CORNER_LEFT_LOWER;
input int LeftPanelMarginX = 10;
input int LeftPanelMarginY = 35;
input int LeftPanelFontSize = 10;
input int LeftPanelLineSpacing = 18;

//+------------------------------------------------------------------+
//| STRUCTS                                                          |
//+------------------------------------------------------------------+
struct SMonitoredComponent
{
   string   name;
   string   gvName;
   datetime lastHeartbeat;
   datetime lastAlertTime;
   bool     isCrashed;
   bool     alertSent;
   int      crashMinutes;
   bool     isCTrader;
   string   heartbeatFile;
};

struct SCommissionData
{
   string   symbol;
   double   commission;
   double   swapLong;
   double   swapShort;
   int      contractSize;
   double   tickValue;
   double   pointSize;
   datetime lastUpdate;
   bool     isAvailable;
};

//+------------------------------------------------------------------+
//| GLOBAL VARIABLES                                                 |
//+------------------------------------------------------------------+
SMonitoredComponent components[];
int componentCount = 0;
string P = "WDI_";
int g_heartbeatCounter = 0;
bool g_heartbeatState = false;
color g_statusColor = clrTeal;
string g_statusText = "ALL OK";
int g_totalCrashed = 0;
datetime g_lastCheckTime = 0;
double g_dummyBuffer[];

SCommissionData g_commissions[];
int g_commissionCount = 0;
datetime g_lastCommissionCheck = 0;
bool g_commissionReady = false;
string g_accountId = "";
string g_customSymbols[];
int g_customCount = 0;

string g_cTraderHeartbeatPath = "";

//+------------------------------------------------------------------+
//| HÀM TRỢ GIÚP: KIỂM TRA SYMBOL CÓ HỢP LỆ THEO CẤU HÌNH KHÔNG      |
//+------------------------------------------------------------------+
bool IsSymbolAllowed(string symbol)
{
   if(UseCustomSymbols)
   {
      for(int i = 0; i < g_customCount; i++)
      {
         if(symbol == g_customSymbols[i]) return true;
      }
      return false;
   }
   else if(ScanAllMarketWatch)
   {
      return SymbolInfoInteger(symbol, SYMBOL_SELECT) > 0;
   }
   
   return (symbol == _Symbol);
}

datetime ReadCTraderHeartbeat(string filePath)
{
   if(!FileIsExist(filePath, FILE_COMMON))
   {
      Print("Watchdog Error: File không tồn tại - ", filePath);
      return 0;
   }
   
   int handle = FileOpen(filePath, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE);
   
   if(handle == INVALID_HANDLE)
   {
      Print("Watchdog Error: Không thể mở file (Có thể đang bị cTrader khóa) - Lỗi: ", GetLastError());
      return 0;
   }
   
   string content = FileReadString(handle);
   FileClose(handle);
   
   if(content == "" || content == "0")
   {
      Print("Watchdog Error: File rỗng hoặc nội dung là 0");
      return 0;
   }
   
   string parts[];
   int count = StringSplit(content, '|', parts);
   
   if(count >= 3)
   {
      string status = "";
      if(count >= 5)
         status = parts[4];
      
      if(status == "STOPPED")
         return 0;
      
      string timeStr = parts[2];
      datetime heartbeatTime = StringToTime(timeStr);
      
      if(heartbeatTime == 0)
      {
         Print("Watchdog Error: Không thể parse thời gian từ chuỗi - ", timeStr);
         return 0;
      }
      
      heartbeatTime -= 7 * 3600;
      
      return heartbeatTime;
   }
   
   Print("Watchdog Error: Dữ liệu sai định dạng (Thiếu dấu |) - Nội dung: ", content);
   return 0;
}

//+------------------------------------------------------------------+
//| OnInit                                                           |
//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, g_dummyBuffer, INDICATOR_DATA);
   
   componentCount = 0;
   ArrayResize(components, 0);
   
   if(Monitor_RSI_Indicator)   AddComponent("RSI Indicator Scan", "HB_RSI_Indicator", false);
   if(Monitor_RSI_Volumes)     AddComponent("RSI Volumes", "HB_RSI_Volumes", false);
   if(Monitor_RSI_EA)          AddComponent("RSI EA", "HB_RSI_EA", false);
   if(Monitor_TradeManager)    AddComponent("TradeManager", "HB_TradeManager", false);
   if(Monitor_NewsFilter)      AddComponent("NewsTimeFilter", NewsFilter_GV_Name, false);
   
   if(Monitor_cTrader)
   {
      g_cTraderHeartbeatPath = cTrader_HeartbeatFileName + "_" + IntegerToString(cTrader_MagicNumber) + ".txt";
      AddComponent("cTrader Bot", "", true, g_cTraderHeartbeatPath);
   }
   
   g_lastCheckTime = 0;
   g_accountId = IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
   
   if(UseCustomSymbols)
   {
      string list = CustomSymbols;
      StringReplace(list, " ", "");
      g_customCount = StringSplit(list, ',', g_customSymbols);
   }
   
   if(EnableAutoCommission)
   {
      ArrayResize(g_commissions, 0);
      g_commissionCount = 0;
      g_commissionReady = false;
      g_lastCommissionCheck = 0;
      
      InitCommissionModule();
   }
   
   string notifyMsg = "🛡️ Watchdog da khoi dong - " + IntegerToString(componentCount) + " components";
   if(Monitor_cTrader)
      notifyMsg += " (incl. cTrader)";
   if(Monitor_NewsFilter)
      notifyMsg += " (incl. NewsFilter)";
   
   if(EnablePushNotify)
      SendNotification(notifyMsg);
   
   DoCheck();
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| OnDeinit                                                         |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   ClearAllObjects();
}

//+------------------------------------------------------------------+
//| OnCalculate                                                      |
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
   string currentAccount = IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
   if(currentAccount != g_accountId)
   {
      Print("Watchdog: Account changed from ", g_accountId, " to ", currentAccount, ". Resetting commission data...");
      g_accountId = currentAccount;
      if(EnableAutoCommission)
      {
         ArrayResize(g_commissions, 0);
         g_commissionCount = 0;
         g_commissionReady = false;
         g_lastCommissionCheck = 0;
         InitCommissionModule();
         Draw();
         ChartRedraw();
      }
   }

   if(TimeCurrent() - g_lastCheckTime < CheckIntervalSeconds)
      return rates_total;
   
   g_lastCheckTime = TimeCurrent();
   DoCheck();
   
   if(EnableAutoCommission && TimeCurrent() - g_lastCommissionCheck >= CommissionCheckMinutes * 60)
   {
      g_lastCommissionCheck = TimeCurrent();
      ScanCommission();
   }
   
   return rates_total;
}

//+------------------------------------------------------------------+
//| COMPONENT MANAGEMENT                                             |
//+------------------------------------------------------------------+
void AddComponent(string name, string gvName, bool isCTrader = false, string heartbeatFile = "")
{
   int idx = componentCount;
   ArrayResize(components, idx + 1);
   components[idx].name = name;
   components[idx].gvName = gvName;
   components[idx].lastHeartbeat = TimeCurrent();
   components[idx].lastAlertTime = 0;
   components[idx].isCrashed = false;
   components[idx].alertSent = false;
   components[idx].crashMinutes = 0;
   components[idx].isCTrader = isCTrader;
   components[idx].heartbeatFile = heartbeatFile;
   
   if(!isCTrader)
   {
      if(!GlobalVariableCheck(gvName))
         GlobalVariableSet(gvName, (double)TimeCurrent());
   }
   
   componentCount++;
}

//+------------------------------------------------------------------+
//| MAIN CHECK                                                       |
//+------------------------------------------------------------------+
void DoCheck()
{
   GlobalVariableSet("HB_Watchdog", (double)TimeCurrent());
   
   g_heartbeatCounter++;
   if(g_heartbeatCounter % 2 == 0) g_heartbeatState = !g_heartbeatState;
   
   datetime now = TimeCurrent();
   g_totalCrashed = 0;
   
   int timeoutSeconds = CrashTimeoutMinutes * 60;
   
   for(int i = 0; i < componentCount; i++)
   {
      datetime lastHb = 0;
      
      if(components[i].isCTrader)
         lastHb = ReadCTraderHeartbeat(components[i].heartbeatFile);
      else
      {
         if(GlobalVariableCheck(components[i].gvName))
            lastHb = (datetime)GlobalVariableGet(components[i].gvName);
      }
      
      components[i].lastHeartbeat = lastHb;
      
      int secondsSinceLastHb = (int)(now - lastHb);
      components[i].crashMinutes = secondsSinceLastHb / 60;
      
      // NewsFilter có timeout riêng: 70 giây
      int effectiveTimeout = timeoutSeconds;
      if(components[i].gvName == NewsFilter_GV_Name)
         effectiveTimeout = 70;
      
      if(secondsSinceLastHb >= effectiveTimeout)
      {
         components[i].isCrashed = true;
         g_totalCrashed++;
         
         bool shouldSendAlert = false;
         if(!components[i].alertSent)
            shouldSendAlert = true;
         else
         {
            int minutesSinceLastAlert = (int)((TimeCurrent() - components[i].lastAlertTime) / 60);
            if(minutesSinceLastAlert >= AlertRepeatMinutes)
               shouldSendAlert = true;
         }
         
         if(shouldSendAlert)
         {
            SendCrashAlert(i, components[i].crashMinutes);
            components[i].lastAlertTime = TimeCurrent();
            components[i].alertSent = true;
         }
      }
      else
      {
         if(components[i].isCrashed)
         {
            SendRecoveryAlert(i);
            components[i].isCrashed = false;
            components[i].alertSent = false;
            components[i].lastAlertTime = 0;
         }
      }
   }
   
   g_statusColor = (g_totalCrashed > 0) ? clrRed : clrLimeGreen;
   g_statusText = (g_totalCrashed > 0) ? "CRASHED: " + IntegerToString(g_totalCrashed) : "ALL OK";
   
   Draw();
   ChartRedraw();
}

//+------------------------------------------------------------------+
//| ALERTS                                                           |
//+------------------------------------------------------------------+
void SendCrashAlert(int idx, int minutesDown)
{
   string compName = components[idx].name;
   datetime gmt7 = TimeGMT() + 7 * 3600;
   string timeStr = TimeToString(gmt7, TIME_MINUTES|TIME_SECONDS);
   
   string downStr;
   if(components[idx].gvName == NewsFilter_GV_Name)
      downStr = IntegerToString(minutesDown * 60) + "s";
   else
      downStr = IntegerToString(minutesDown) + "min";
   
   string alertMsg;
   if(!components[idx].alertSent)
      alertMsg = StringFormat("[CRASH] %s - Offline %s | %s", compName, downStr, timeStr);
   else
      alertMsg = StringFormat("[CRASH] %s - Still offline %s | %s", compName, downStr, timeStr);
   
   if(EnablePushNotify) SendNotification(alertMsg);
   if(EnableSoundAlert) PlaySound("alert2.wav");
}

void SendRecoveryAlert(int idx)
{
   string compName = components[idx].name;
   datetime gmt7 = TimeGMT() + 7 * 3600;
   string timeStr = TimeToString(gmt7, TIME_MINUTES|TIME_SECONDS);
   
   string alertMsg = StringFormat("[RECOVERED] %s - Back online | %s", compName, timeStr);
   
   if(EnablePushNotify) SendNotification(alertMsg);
   if(EnableSoundAlert) PlaySound("ok.wav");
}

//+------------------------------------------------------------------+
//| COMMISSION MODULE                                                |
//+------------------------------------------------------------------+
void InitCommissionModule()
{
   LoadAllCommissionsFromFile();
   
   if(g_commissionCount == 0 && IsSymbolAllowed(_Symbol))
   {
      AddOrUpdateCommission(_Symbol, DefaultCommission);
      SetCommissionGV(_Symbol, DefaultCommission);
   }
   
   for(int i = 0; i < g_commissionCount; i++)
      UpdateSwapInfo(i);
   
   PushAllCommissionsToGV();
   g_commissionReady = true;
}

void UpdateSwapInfo(int idx)
{
   string symbol = g_commissions[idx].symbol;
   
   if(!SymbolInfoInteger(symbol, SYMBOL_SELECT))
   {
       if(symbol != _Symbol) 
           return;
       else
           SymbolSelect(symbol, true);
   }
   
   g_commissions[idx].swapLong = 0;
   g_commissions[idx].swapShort = 0;
   g_commissions[idx].contractSize = 0;
   g_commissions[idx].tickValue = 0;
   g_commissions[idx].pointSize = 0;
   
   g_commissions[idx].swapLong = SymbolInfoDouble(symbol, SYMBOL_SWAP_LONG);
   g_commissions[idx].swapShort = SymbolInfoDouble(symbol, SYMBOL_SWAP_SHORT);
   g_commissions[idx].contractSize = (int)SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   g_commissions[idx].tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   g_commissions[idx].pointSize = SymbolInfoDouble(symbol, SYMBOL_POINT);
   
   double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickSize > 0 && g_commissions[idx].pointSize > 0)
   {
      double pointsPerTick = tickSize / g_commissions[idx].pointSize;
      if(g_commissions[idx].tickValue > 0 && pointsPerTick > 0)
      {
         double pointValue = g_commissions[idx].tickValue / pointsPerTick;
         g_commissions[idx].swapLong = g_commissions[idx].swapLong * pointValue;
         g_commissions[idx].swapShort = g_commissions[idx].swapShort * pointValue;
      }
   }
}

void ScanCommission()
{
   string symbols[];
   int symCount = 0;
   
   if(UseCustomSymbols)
   {
      for(int i = 0; i < g_customCount; i++)
      {
         string sym = g_customSymbols[i];
         if(SymbolInfoInteger(sym, SYMBOL_SELECT))
         {
            ArrayResize(symbols, symCount + 1);
            symbols[symCount] = sym;
            symCount++;
         }
      }
   }
   else if(ScanAllMarketWatch)
   {
      int total = SymbolsTotal(true);
      for(int i = 0; i < total; i++)
      {
         string sym = SymbolName(i, true);
         if(SymbolInfoInteger(sym, SYMBOL_SELECT))
         {
            ArrayResize(symbols, symCount + 1);
            symbols[symCount] = sym;
            symCount++;
         }
      }
   }
   else
   {
      ArrayResize(symbols, 1);
      symbols[0] = _Symbol;
      symCount = 1;
   }
   
   bool hasUpdate = false;
   
   for(int i = 0; i < symCount; i++)
   {
      string symbol = symbols[i];
      
      double commission = 0;
      bool hasDeals = HasRecentDeals(symbol);
      if(hasDeals)
      {
         commission = ReadCommissionFromHistory(symbol);
      }
      
      int idx = FindCommissionIndex(symbol);
      
      if(commission > 0)
      {
         if(idx >= 0)
         {
            if(MathAbs(commission - g_commissions[idx].commission) > 0.1)
            {
               g_commissions[idx].commission = commission;
               g_commissions[idx].lastUpdate = TimeCurrent();
               UpdateSwapInfo(idx);
               SetCommissionGV(symbol, commission);
               hasUpdate = true;
            }
         }
         else
         {
            AddOrUpdateCommission(symbol, commission);
            int newIdx = FindCommissionIndex(symbol);
            if(newIdx >= 0) UpdateSwapInfo(newIdx);
            SetCommissionGV(symbol, commission);
            hasUpdate = true;
         }
      }
      else
      {
         if(idx >= 0)
         {
            UpdateSwapInfo(idx);
            SetCommissionGV(symbol, g_commissions[idx].commission);
            hasUpdate = true;
         }
         else if(IsSymbolAllowed(symbol))
         {
            AddOrUpdateCommission(symbol, DefaultCommission);
            SetCommissionGV(symbol, DefaultCommission);
            hasUpdate = true;
         }
      }
   }
   
   if(hasUpdate)
   {
      SaveAllCommissionsToFile();
   }
}

bool HasRecentDeals(string symbol)
{
   if(!SymbolInfoInteger(symbol, SYMBOL_SELECT)) return false;
   if(!HistorySelect(TimeCurrent() - 30 * 24 * 60 * 60, TimeCurrent())) return false;
   
   int totalDeals = HistoryDealsTotal();
   int dealCount = 0;
   
   for(int i = totalDeals - 1; i >= 0; i--)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(HistoryDealGetString(deal, DEAL_SYMBOL) == symbol)
      {
         dealCount++;
         if(dealCount >= 2) return true;
      }
   }
   return false;
}

double ReadCommissionFromHistory(string symbol)
{
   if(!SymbolInfoInteger(symbol, SYMBOL_SELECT)) return 0.0;
   if(!HistorySelect(TimeCurrent() - 30 * 24 * 60 * 60, TimeCurrent())) return 0.0;
   
   int totalDeals = HistoryDealsTotal();
   if(totalDeals < 2) return 0.0;
   
   ulong lastPosition = 0;
   double totalCommission = 0.0;
   double volume = 0.0;
   int dealCount = 0;
   
   for(int i = totalDeals - 1; i >= 0; i--)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(HistoryDealGetString(deal, DEAL_SYMBOL) != symbol) continue;
      
      ulong posID = HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      if(lastPosition == 0) lastPosition = posID;
      if(posID != lastPosition) break;
      
      totalCommission += MathAbs(HistoryDealGetDouble(deal, DEAL_COMMISSION));
      volume = HistoryDealGetDouble(deal, DEAL_VOLUME);
      dealCount++;
   }
   
   if(dealCount >= 2 && volume > 0 && totalCommission > 0)
      return totalCommission / volume;
   
   return 0.0;
}

int FindCommissionIndex(string symbol)
{
   for(int i = 0; i < g_commissionCount; i++)
      if(g_commissions[i].symbol == symbol) return i;
   return -1;
}

void AddOrUpdateCommission(string symbol, double commission)
{
   int idx = FindCommissionIndex(symbol);
   if(idx >= 0)
   {
      g_commissions[idx].commission = commission;
      g_commissions[idx].lastUpdate = TimeCurrent();
      g_commissions[idx].isAvailable = true;
      UpdateSwapInfo(idx);
   }
   else
   {
      ArrayResize(g_commissions, g_commissionCount + 1);
      g_commissions[g_commissionCount].symbol = symbol;
      g_commissions[g_commissionCount].commission = commission;
      g_commissions[g_commissionCount].swapLong = 0;
      g_commissions[g_commissionCount].swapShort = 0;
      g_commissions[g_commissionCount].contractSize = 0;
      g_commissions[g_commissionCount].tickValue = 0;
      g_commissions[g_commissionCount].pointSize = 0;
      g_commissions[g_commissionCount].lastUpdate = TimeCurrent();
      g_commissions[g_commissionCount].isAvailable = true;
      g_commissionCount++;
      
      int newIdx = FindCommissionIndex(symbol);
      if(newIdx >= 0) UpdateSwapInfo(newIdx);
   }
}

string GetCommissionGVName(string symbol)
{
   string symbolPart = StringLen(symbol) >= 6 ? StringSubstr(symbol, 0, 6) : symbol;
   return "COMM_" + symbolPart + "_" + g_accountId;
}

void SetCommissionGV(string symbol, double commission)
{
   GlobalVariableSet(GetCommissionGVName(symbol), commission);
}

void PushAllCommissionsToGV()
{
   for(int i = 0; i < g_commissionCount; i++)
   {
      if(IsSymbolAllowed(g_commissions[i].symbol))
         SetCommissionGV(g_commissions[i].symbol, g_commissions[i].commission);
   }
}

void SaveAllCommissionsToFile()
{
   string fileName = "Commissions_" + g_accountId + ".csv";
   int handle = FileOpen(fileName, FILE_WRITE|FILE_CSV|FILE_COMMON, ",");
   if(handle == INVALID_HANDLE) return;
   
   FileWrite(handle, "Symbol", "Commission", "SwapLong", "SwapShort", "LastUpdate");
   for(int i = 0; i < g_commissionCount; i++)
   {
      if(IsSymbolAllowed(g_commissions[i].symbol))
      {
         FileWrite(handle, 
                   g_commissions[i].symbol, 
                   DoubleToString(g_commissions[i].commission, 6),
                   DoubleToString(g_commissions[i].swapLong, 6),
                   DoubleToString(g_commissions[i].swapShort, 6),
                   TimeToString(g_commissions[i].lastUpdate, TIME_DATE|TIME_MINUTES));
      }
   }
   FileClose(handle);
}

void LoadAllCommissionsFromFile()
{
   string fileName = "Commissions_" + g_accountId + ".csv";
   if(!FileIsExist(fileName, FILE_COMMON)) return;
   
   int handle = FileOpen(fileName, FILE_READ|FILE_CSV|FILE_COMMON, ",");
   if(handle == INVALID_HANDLE) return;
   
   FileReadString(handle);
   FileReadString(handle);
   
   string header3 = FileReadString(handle);
   bool hasSwapColumns = (header3 != "LastUpdate");
   
   if(hasSwapColumns)
   {
      FileReadString(handle);
      FileReadString(handle);
   }
   
   ArrayResize(g_commissions, 0);
   g_commissionCount = 0;
   
   while(!FileIsEnding(handle))
   {
      string symbol = FileReadString(handle);
      string comStr = FileReadString(handle);
      
      if(symbol != "" && comStr != "")
      {
         if(IsSymbolAllowed(symbol))
         {
            ArrayResize(g_commissions, g_commissionCount + 1);
            g_commissions[g_commissionCount].symbol = symbol;
            g_commissions[g_commissionCount].commission = StringToDouble(comStr);
            
            if(hasSwapColumns)
            {
               g_commissions[g_commissionCount].swapLong = StringToDouble(FileReadString(handle));
               g_commissions[g_commissionCount].swapShort = StringToDouble(FileReadString(handle));
               g_commissions[g_commissionCount].lastUpdate = StringToTime(FileReadString(handle));
            }
            else
            {
               g_commissions[g_commissionCount].swapLong = 0;
               g_commissions[g_commissionCount].swapShort = 0;
               g_commissions[g_commissionCount].lastUpdate = StringToTime(FileReadString(handle));
            }
            
            g_commissions[g_commissionCount].isAvailable = true;
            g_commissionCount++;
         }
         else
         {
            if(hasSwapColumns)
            {
               FileReadString(handle);
               FileReadString(handle);
               FileReadString(handle);
            }
            else
            {
               FileReadString(handle);
            }
         }
      }
   }
   FileClose(handle);
   
   for(int i = 0; i < g_commissionCount; i++)
      UpdateSwapInfo(i);
}

double GetCommission(string symbol)
{
   int idx = FindCommissionIndex(symbol);
   return (idx >= 0) ? g_commissions[idx].commission : DefaultCommission;
}

bool IsCommissionReady() { return g_commissionReady; }

//+------------------------------------------------------------------+
//| DRAW MAIN                                                        |
//+------------------------------------------------------------------+
void Draw()
{
   if(EnableAutoCommission)
      DrawCommissionPanel();
   
   DrawStatusPanel();
}

//+------------------------------------------------------------------+
//| DRAW - PANEL TRÁI: COMMISSION & SWAP                             |
//+------------------------------------------------------------------+
void DrawCommissionPanel()
{
   int x = LeftPanelMarginX;
   int y = LeftPanelMarginY;
   int rowH = LeftPanelLineSpacing;
   int fs = LeftPanelFontSize;
   string prefix = P + "L_";

   UpdPanelMsg(prefix + "HB", "📊 COMMISSION & SWAP", 
               x, y, clrGold, fs + 2, true, LeftPanelCorner);
   y += rowH + 4;
   
   UpdPanelMsg(prefix + "LN1", "═══════════════════════════════════", x, y, clrDarkGray, 7, false, LeftPanelCorner);
   y += 10;

   UpdPanelMsg(prefix + "HDR", "SYMBOL     COMM/LOT   SWAP LONG   SWAP SHORT", 
               x, y, clrDimGray, fs - 2, false, LeftPanelCorner);
   y += rowH - 4;

   if(!g_commissionReady || g_commissionCount == 0)
   {
      UpdPanelMsg(prefix + "NODATA", "⏳ Đang thu thập dữ liệu...", x, y, clrDimGray, fs, false, LeftPanelCorner);
      
      for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
      {
         string name = ObjectName(0, i);
         if(StringFind(name, P + "L_SYM_") == 0)
            ObjectDelete(0, name);
      }
      return;
   }

   for(int i = 0; i < g_commissionCount; i++)
   {
      SCommissionData cd = g_commissions[i];
      
      color symColor;
      if(cd.symbol == _Symbol)
         symColor = clrLimeGreen;
      else if(i % 2 == 0)
         symColor = clrLightSteelBlue;
      else
         symColor = clrSilver;
      
      string swapLongStr = FormatSwapValue(cd.swapLong);
      string swapShortStr = FormatSwapValue(cd.swapShort);
      
      string line = StringFormat("%-8s  $%-8.2f  %-10s  %-10s", 
                                  cd.symbol, cd.commission, swapLongStr, swapShortStr);
      
      UpdPanelMsg(prefix + "SYM_" + IntegerToString(i), line, 
                  x, y, symColor, fs, true, LeftPanelCorner);
      y += rowH;
   }
   
   y += 4;
   UpdPanelMsg(prefix + "LN2", "═══════════════════════════════════", x, y, clrDarkGray, 7, false, LeftPanelCorner);
   y += 8;
   UpdPanelMsg(prefix + "FOOTER", StringFormat("%d symbols tracked", g_commissionCount), 
               x, y, clrSilver, fs - 2, false, LeftPanelCorner);
   
   CleanupLeftPanelObjects();
}

string FormatSwapValue(double swap)
{
   if(swap == 0) return "N/A";
   
   string sign = (swap >= 0) ? "+" : "";
   
   if(MathAbs(swap) >= 10)
      return StringFormat("%s$%.2f", sign, swap);
   else if(MathAbs(swap) >= 1)
      return StringFormat("%s$%.3f", sign, swap);
   else
      return StringFormat("%s$%.4f", sign, swap);
}

void CleanupLeftPanelObjects()
{
   string keepPrefixes[];
   ArrayResize(keepPrefixes, 0);
   
   AddKeepPrefix(keepPrefixes, P + "L_HB");
   AddKeepPrefix(keepPrefixes, P + "L_LN1");
   AddKeepPrefix(keepPrefixes, P + "L_LN2");
   AddKeepPrefix(keepPrefixes, P + "L_HDR");
   AddKeepPrefix(keepPrefixes, P + "L_NODATA");
   AddKeepPrefix(keepPrefixes, P + "L_FOOTER");
   
   for(int i = 0; i < g_commissionCount; i++)
      AddKeepPrefix(keepPrefixes, P + "L_SYM_" + IntegerToString(i));
   
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i);
      if(StringFind(name, P + "L_") != 0) continue;
      
      bool keep = false;
      for(int k = 0; k < ArraySize(keepPrefixes); k++)
      {
         if(name == keepPrefixes[k])
         {
            keep = true;
            break;
         }
      }
      
      if(!keep) ObjectDelete(0, name);
   }
}

void AddKeepPrefix(string &arr[], string prefix)
{
   int sz = ArraySize(arr);
   ArrayResize(arr, sz + 1);
   arr[sz] = prefix;
}

//+------------------------------------------------------------------+
//| DRAW - PANEL PHẢI: TRẠNG THÁI HOẠT ĐỘNG                          |
//+------------------------------------------------------------------+
void DrawStatusPanel()
{
   int x = PanelMarginX;
   int y = PanelMarginY;
   int rowH = LineSpacing;
   string prefix = P + "R_";
   
   datetime gmt7 = TimeGMT() + 7 * 3600;
   string timeStr = TimeToString(gmt7, TIME_MINUTES|TIME_SECONDS);
   string hb = g_heartbeatState ? "●" : "○";
   
   UpdPanelMsg(prefix + "HB", StringFormat("%s WATCHDOG: %s", hb, g_statusText), 
               x, y, g_statusColor, FontSize + 1, true, PanelCorner);
   
   y += rowH;
   UpdPanelMsg(prefix + "TIME", StringFormat("🕐 %s (GMT+7)", timeStr), 
               x, y, clrDarkGray, FontSize - 1, false, PanelCorner);
   
   y += rowH + 2;
   UpdPanelMsg(prefix + "LN1", "────────────────────────────────────", x, y, clrDarkGray, 7, false, PanelCorner);
   y += 10;
   
   if(componentCount == 0)
   {
      UpdPanelMsg(prefix + "NOCOMP", "⚠️ NO COMPONENTS", x, y, clrOrange, FontSize, true, PanelCorner);
      y += rowH;
   }
   
   for(int i = 0; i < componentCount; i++)
   {
      string icon, text;
      color c;
      
      string tag = components[i].isCTrader ? " [CT]" : "";
      
      if(components[i].lastHeartbeat == 0)
      {
         icon = "⚪"; text = "NO DATA"; c = clrDimGray;
      }
      else if(components[i].isCrashed)
      {
         // Hiển thị giây cho NewsFilter, phút cho các component khác
         if(components[i].gvName == NewsFilter_GV_Name)
         {
            icon = "🔴"; text = "CRASHED " + IntegerToString(components[i].crashMinutes * 60) + "s"; c = clrRed;
         }
         else
         {
            icon = "🔴"; text = "CRASHED " + IntegerToString(components[i].crashMinutes) + "min"; c = clrRed;
         }
      }
      else if(components[i].crashMinutes >= 2)
      {
         icon = "🟡"; text = "SLOW " + IntegerToString(components[i].crashMinutes) + "min"; c = clrOrange;
      }
      else
      {
         icon = "🟢"; text = "ONLINE"; c = clrLime;
      }
      
      UpdPanelMsg(prefix + "C" + IntegerToString(i), 
                  StringFormat("%s %-22s%s %s", icon, components[i].name, tag, text), 
                  x, y, c, FontSize - 1, false, PanelCorner);
      y += rowH;
   }
   
   y += 2;
   UpdPanelMsg(prefix + "LN2", "────────────────────────────────────", x, y, clrDarkGray, 7, false, PanelCorner);
   y += 10;
   
   string footerMsg;
   if(Monitor_cTrader || Monitor_NewsFilter)
      footerMsg = StringFormat("Check every %ds | Timeout: %dm (NF:70s)", CheckIntervalSeconds, CrashTimeoutMinutes);
   else
      footerMsg = StringFormat("Check every %ds | Timeout: %dm", CheckIntervalSeconds, CrashTimeoutMinutes);
   
   UpdPanelMsg(prefix + "FOOTER", footerMsg, x, y, clrSilver, FontSize - 2, false, PanelCorner);
   
   CleanupRightPanelObjects();
}

void CleanupRightPanelObjects()
{
   string keepPrefixes[];
   ArrayResize(keepPrefixes, 0);
   
   AddKeepPrefix(keepPrefixes, P + "R_HB");
   AddKeepPrefix(keepPrefixes, P + "R_TIME");
   AddKeepPrefix(keepPrefixes, P + "R_LN1");
   AddKeepPrefix(keepPrefixes, P + "R_LN2");
   AddKeepPrefix(keepPrefixes, P + "R_NOCOMP");
   AddKeepPrefix(keepPrefixes, P + "R_FOOTER");
   
   for(int i = 0; i < componentCount; i++)
      AddKeepPrefix(keepPrefixes, P + "R_C" + IntegerToString(i));
   
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i);
      if(StringFind(name, P + "R_") != 0) continue;
      
      bool keep = false;
      for(int k = 0; k < ArraySize(keepPrefixes); k++)
      {
         if(name == keepPrefixes[k])
         {
            keep = true;
            break;
         }
      }
      
      if(!keep) ObjectDelete(0, name);
   }
}

//+------------------------------------------------------------------+
//| OBJECTS - PANEL MESSAGE                                          |
//+------------------------------------------------------------------+
void UpdPanelMsg(string name, string text, int x, int y, color c, 
                 int fs = 11, bool bold = false, ENUM_BASE_CORNER corner = CORNER_RIGHT_LOWER)
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
   
   if(corner == CORNER_LEFT_LOWER || corner == CORNER_LEFT_UPPER)
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
   else
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_RIGHT_LOWER);
   
   ObjectSetInteger(0, name, OBJPROP_COLOR, c);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

void ClearAllObjects()
{
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
   {
      string n = ObjectName(0, i);
      if(StringFind(n, P) == 0)
         ObjectDelete(0, n);
   }
}
//+------------------------------------------------------------------+