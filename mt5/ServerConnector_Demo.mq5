//+------------------------------------------------------------------+
//| ServerConnector_Demo.mq5                                         |
//| Non-blocking OnTick buffer + batched/coalesced tick flush.       |
//| Chart & account feed only (no trade execution).                  |
//+------------------------------------------------------------------+
#property strict
#property version "1.53"

#include <Trade\Trade.mqh>

input string ServerBaseURL = "https://investforex.onrender.com";
input string ApiKey = "SET_A_LONG_RANDOM_API_KEY";
input string ConsumerId = "mt5-demo-vps";
input int PollSeconds = 3;
input int HeartbeatSeconds = 15;
input bool ShowConnectionComment = true;

// Fast tick buffer & flush controls
input bool EnableFastTickPush = true;
input int FastTickMinMs = 900;          // Min interval (ms) between tick flushes per symbol
input int TickFlushIntervalMs = 900;    // Timer tick flush cadence (ms)
input string FastTickSymbols = "EURUSD,GBPUSD,USDJPY,XAUUSD,BTCUSD,USDCAD,NZDUSD,USDCHF"; // Symbols pushed every ~1s (chart symbol always included)

// Account info sync
input bool EnableAccountSync = true;
input int AccountSyncSeconds = 5;

// Legacy divergence import
input bool EnableLegacyDivergenceImport = false;
input int MaxLegacySignalAgeSeconds = 300;

// Chart candle feed (smart incremental + full snapshot)
input bool EnableChartCandleFeed = true;
input int ChartItemsPerSecond = 2;      // Candle uploads per second (rotating)
input int FullCandleSyncSeconds = 60;
input int FastCandleBars = 25;
input int ChartBarsToSend = 500;
input string FallbackSymbols = "EURUSD,GBPUSD,USDJPY,XAUUSD,BTCUSD,USDCAD,NZDUSD,USDCHF";
input string FallbackTimeframes = "M15,H1";

// Commission snapshot sync
input bool EnableCommissionSync = true;
input int CommissionSyncSeconds = 60;
input string CommissionSymbols = "XAUUSD,USDJPY,EURUSD,GBPUSD";

// Real history sync
input bool EnableRealHistorySync = false;
input int HistorySyncSeconds = 15;
input int HistoryLookbackDays = 7;

ulong g_lastHeartbeat = 0;
ulong g_lastPoll = 0;
ulong g_lastAccountPost = 0;
string g_lastLegacySignature = "";
ulong g_lastScanPost = 0;
ulong g_lastScanSent = 0;
ulong g_lastTodaySent = 0;
ulong g_lastAccountSent = 0;
int   g_chartCursor = 0;
ulong g_candleFullMs[];
string g_lastScanPayload = "";
string g_lastTodaySignalsPayload = "";
ulong g_lastHistoryPost = 0;
ulong g_lastCommissionPost = 0;
string g_queueCursorName = "DIVSERVER_QUEUE_CURSOR";
string g_lastHistoryDigest = "";
string g_lastChartFeedState = "";
string g_lastCommissionState = "";
string g_lastAccountState = "";

// Connection status comment state
string   g_connStatus      = "INIT";
int      g_lastConnHttp    = 0;
string   g_lastConnMsg     = "Waiting for first sync";
datetime g_lastConnOkTime  = 0;
ulong    g_tickBatchCount  = 0;
ulong    g_candleBatchCount= 0;

// Prevent overlapping HTTP calls on single-threaded EA
bool  g_netBusy = false;
ulong g_lastTickFlushMs = 0;

// Latest buffered tick per symbol (coalesced in OnTick)
string g_bufSym[];
double g_bufBid[];
double g_bufAsk[];
double g_bufLast[];
ulong  g_bufTimeMsc[];
int    g_bufDigits[];
bool   g_bufDirty[];
ulong  g_bufLastSentMs[];

// Per-(symbol, timeframe) last sent candle digest
string g_candleKeys[];
string g_candleDigests[];

string JsonEscape(string s)
{
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   StringReplace(s, "\r", " ");
   StringReplace(s, "\n", " ");
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
   while(p < StringLen(json) && (StringGetCharacter(json,p)==' ' || StringGetCharacter(json,p)=='\t')) p++;
   if(p >= StringLen(json)) return "";
   if(StringGetCharacter(json,p)=='\"')
   {
      string out = "";
      for(int i = p + 1; i < StringLen(json); i++)
      {
         ushort ch = StringGetCharacter(json, i);
         if(ch == '\\' && i + 1 < StringLen(json))
         {
            ushort nx = StringGetCharacter(json, i + 1);
            if(nx == '"' || nx == '\\') { out += ShortToString(nx); i++; continue; }
         }
         if(ch == '"') return out;
         out += ShortToString(ch);
      }
      return "";
   }
   int e = p;
   while(e < StringLen(json) && StringGetCharacter(json,e)!=',' &&
         StringGetCharacter(json,e)!='}' && StringGetCharacter(json,e)!='\n') e++;
   return StringSubstr(json,p,e-p);
}

string JsonObjectValue(string json, string key)
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
   if(p >= StringLen(json) || StringGetCharacter(json,p)!='{') return "";
   int depth=0;
   bool inString=false;
   bool escaped=false;
   for(int i=p; i<StringLen(json); i++)
   {
      ushort ch=StringGetCharacter(json,i);
      if(inString)
      {
         if(escaped) escaped=false;
         else if(ch=='\\') escaped=true;
         else if(ch=='"') inString=false;
         continue;
      }
      if(ch=='"') { inString=true; continue; }
      if(ch=='{') depth++;
      else if(ch=='}')
      {
         depth--;
         if(depth==0) return StringSubstr(json,p,i-p+1);
      }
   }
   return "";
}

// File va GlobalVariable cua indicator ghi gio theo GMT+7
datetime NowGmt7() { return TimeGMT() + 7*3600; }

void RenderConnectionComment()
{
   if(!ShowConnectionComment) return;

   bool mt5Conn = (bool)TerminalInfoInteger(TERMINAL_CONNECTED);
   string okTimeStr = (g_lastConnOkTime > 0)
      ? TimeToString(g_lastConnOkTime, TIME_DATE|TIME_SECONDS)
      : "Chua co";

   string statusLabel = g_connStatus;
   if(!mt5Conn && statusLabel == "ONLINE")
      statusLabel = "MT5 DISCONNECTED";

   string txt =
      "=== SERVER CONNECTOR v1.53 ===\n" +
      StringFormat("Trang thai : %s (MT5:%s | HTTP:%d)\n",
                   statusLabel, mt5Conn ? "OK" : "MAT KET NOI", g_lastConnHttp) +
      StringFormat("Consumer   : %s | Server: %s\n", ConsumerId, ServerBaseURL) +
      StringFormat("Chi tiet   : %s\n", g_lastConnMsg) +
      StringFormat("Dong bo OK : %s (GMT+7) | Ticks:%I64u | Candles:%I64u",
                   okTimeStr, g_tickBatchCount, g_candleBatchCount);

   Comment(txt);
}

bool HttpRequest(string method,string path,string body,string &response,int &status,int timeoutMs=2500,bool allowWhenBusy=false)
{
   if(g_netBusy && !allowWhenBusy) return false;
   g_netBusy = true;

   char data[], result[];
   string headers = "X-API-Key: " + ApiKey + "\r\nContent-Type: application/json\r\nConnection: keep-alive\r\n";
   string url = ServerBaseURL + path;
   if(body != "")
   {
      StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
      int n=ArraySize(data);
      if(n>0 && data[n-1]==0) ArrayResize(data,n-1);
   }
   else ArrayResize(data,0);

   string responseHeaders;
   ResetLastError();
   status = WebRequest(method,url,headers,timeoutMs,data,result,responseHeaders);
   g_netBusy = false;
   g_lastConnHttp = status;

   if(status == -1)
   {
      int err = GetLastError();
      g_connStatus = "NET ERR";
      g_lastConnMsg = StringFormat("WebRequest err=%d (%s)", err, path);
      RenderConnectionComment();
      Print("[SERVER] WebRequest failed: err=",err," url=",url,
            ". Add server URL to MT5 Tools > Options > Expert Advisors > Allow WebRequest.");
      response="";
      return false;
   }
   response=CharArrayToString(result,0,-1,CP_UTF8);
   if(status>=200 && status<300)
   {
      g_connStatus = "ONLINE";
      g_lastConnOkTime = NowGmt7();
      g_lastConnMsg = StringFormat("OK %s %s (HTTP %d)", method, path, status);
      RenderConnectionComment();
      return true;
   }

   g_connStatus = "HTTP ERR";
   g_lastConnMsg = StringFormat("HTTP %d on %s", status, path);
   RenderConnectionComment();
   return false;
}

// Hen gio theo dong ho may, van chay khi thi truong khong co bao gia moi
bool Due(ulong lastMs, int seconds)
{
   return lastMs == 0 || GetTickCount64() - lastMs >= (ulong)seconds * 1000;
}

int EnsureTickSlot(string sym)
{
   for(int i=0; i<ArraySize(g_bufSym); i++)
      if(g_bufSym[i] == sym) return i;

   int n = ArraySize(g_bufSym);
   ArrayResize(g_bufSym, n+1);
   ArrayResize(g_bufBid, n+1);
   ArrayResize(g_bufAsk, n+1);
   ArrayResize(g_bufLast, n+1);
   ArrayResize(g_bufTimeMsc, n+1);
   ArrayResize(g_bufDigits, n+1);
   ArrayResize(g_bufDirty, n+1);
   ArrayResize(g_bufLastSentMs, n+1);

   g_bufSym[n] = sym;
   g_bufBid[n] = 0.0;
   g_bufAsk[n] = 0.0;
   g_bufLast[n] = 0.0;
   g_bufTimeMsc[n] = 0;
   g_bufDigits[n] = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   g_bufDirty[n] = false;
   g_bufLastSentMs[n] = 0;
   return n;
}

bool IsFastTickTarget(string sym)
{
   if(sym == _Symbol) return true;
   if(FastTickSymbols == "") return false;

   string list[];
   int n = StringSplit(FastTickSymbols, ',', list);
   for(int i=0; i<n; i++)
   {
      string item = list[i];
      StringTrimLeft(item); StringTrimRight(item); StringToUpper(item);
      if(item == sym) return true;
   }
   return false;
}

// Fast, zero-network buffer update inside OnTick()
void BufferTick(string sym)
{
   if(!EnableFastTickPush || sym == "" || !IsFastTickTarget(sym)) return;

   MqlTick tick;
   if(!SymbolInfoTick(sym, tick)) return;
   if(tick.bid <= 0.0 || tick.ask <= 0.0) return;

   int idx = EnsureTickSlot(sym);
   // Gia khong doi van gui lai moi 10s de cache server khong het han
   if(tick.bid == g_bufBid[idx] && tick.ask == g_bufAsk[idx] &&
      GetTickCount64() - g_bufLastSentMs[idx] < 10000) return;

   g_bufBid[idx] = tick.bid;
   g_bufAsk[idx] = tick.ask;
   g_bufLast[idx] = tick.last;
   g_bufTimeMsc[idx] = tick.time_msc;
   g_bufDirty[idx] = true;
}

void BufferAllTicks()
{
   BufferTick(_Symbol);
   string list[];
   int n = StringSplit(FastTickSymbols, ',', list);
   for(int i=0; i<n; i++)
   {
      string s = list[i];
      StringTrimLeft(s); StringTrimRight(s); StringToUpper(s);
      if(s != "" && s != _Symbol && SymbolSelect(s, true)) BufferTick(s);
   }
}

void FlushBufferedTicks()
{
   if(!EnableFastTickPush || g_netBusy) return;

   ulong nowMs = GetTickCount64();
   int flushCadence = MathMax(50, TickFlushIntervalMs);
   if(nowMs - g_lastTickFlushMs < (ulong)flushCadence) return;

   int minGap = MathMax(50, FastTickMinMs);
   string itemsJson = "";
   int chosenIndices[];
   ArrayResize(chosenIndices, 0);

   for(int i=0; i<ArraySize(g_bufSym); i++)
   {
      if(!g_bufDirty[i]) continue;
      if(nowMs - g_bufLastSentMs[i] < (ulong)minGap) continue;

      if(itemsJson != "") itemsJson += ",";
      itemsJson += StringFormat(
         "{\"symbol\":\"%s\",\"bid\":%.*f,\"ask\":%.*f,\"last\":%.*f,\"time_msc\":%I64u,\"timestamp\":%d,\"consumer_id\":\"%s\"}",
         JsonEscape(g_bufSym[i]),
         g_bufDigits[i], g_bufBid[i],
         g_bufDigits[i], g_bufAsk[i],
         g_bufDigits[i], g_bufLast[i],
         g_bufTimeMsc[i],
         (int)TimeCurrent(),
         JsonEscape(ConsumerId)
      );

      int k = ArraySize(chosenIndices);
      ArrayResize(chosenIndices, k+1);
      chosenIndices[k] = i;
   }

   if(ArraySize(chosenIndices) == 0) return;

   g_lastTickFlushMs = nowMs;
   string body = "{\"ticks\":[" + itemsJson + "]}";
   string resp;
   int status = 0;
   if(HttpRequest("POST", "/api/mt5/ticks/batch", body, resp, status, 900, false))
   {
      g_tickBatchCount += (ulong)ArraySize(chosenIndices);
      for(int j=0; j<ArraySize(chosenIndices); j++)
      {
         int idx = chosenIndices[j];
         g_bufDirty[idx] = false;
         g_bufLastSentMs[idx] = nowMs;
      }
      RenderConnectionComment();
   }
}

string GetAccountModeText()
{
   ENUM_ACCOUNT_TRADE_MODE mode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(mode == ACCOUNT_TRADE_MODE_REAL) return "REAL";
   if(mode == ACCOUNT_TRADE_MODE_CONTEST) return "CONTEST";
   return "DEMO";
}

void PublishAccountInfo()
{
   if(!EnableAccountSync || g_netBusy) return;

   long   login         = (long)AccountInfoInteger(ACCOUNT_LOGIN);
   long   leverage      = (long)AccountInfoInteger(ACCOUNT_LEVERAGE);
   bool   tradeAllowed  = (bool)AccountInfoInteger(ACCOUNT_TRADE_ALLOWED);
   bool   expertAllowed = (bool)AccountInfoInteger(ACCOUNT_TRADE_EXPERT);
   string name          = AccountInfoString(ACCOUNT_NAME);
   string server        = AccountInfoString(ACCOUNT_SERVER);
   string company       = AccountInfoString(ACCOUNT_COMPANY);
   string currency      = AccountInfoString(ACCOUNT_CURRENCY);
   string modeText      = GetAccountModeText();

   double balance       = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity        = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin        = AccountInfoDouble(ACCOUNT_MARGIN);
   double freeMargin    = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double marginLevel   = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   double profit        = AccountInfoDouble(ACCOUNT_PROFIT);
   int    posCount      = PositionsTotal();
   int    ordersCount   = OrdersTotal();

   string stateSig = StringFormat("%I64d|%.2f|%.2f|%.2f|%.2f|%.2f|%d|%d|%s",
                                  login, balance, equity, margin, freeMargin, profit,
                                  posCount, ordersCount, modeText);
   if(stateSig == g_lastAccountState && !Due(g_lastAccountSent, 10)) return;

   string body = StringFormat(
      "{\"account_id\":\"%I64d\",\"login\":%I64d,\"name\":\"%s\",\"server\":\"%s\","
      "\"broker\":\"%s\",\"currency\":\"%s\",\"leverage\":%I64d,\"account_mode\":\"%s\","
      "\"trade_allowed\":%s,\"expert_allowed\":%s,"
      "\"balance\":%.2f,\"equity\":%.2f,\"margin\":%.2f,\"free_margin\":%.2f,"
      "\"margin_level\":%.2f,\"profit\":%.2f,\"positions_count\":%d,\"orders_count\":%d,"
      "\"timestamp\":%d}",
      login, login, JsonEscape(name), JsonEscape(server),
      JsonEscape(company), JsonEscape(currency), leverage, JsonEscape(modeText),
      tradeAllowed ? "true" : "false", expertAllowed ? "true" : "false",
      balance, equity, margin, freeMargin,
      marginLevel, profit, posCount, ordersCount,
      (int)TimeCurrent()
   );

   string response;
   int status = 0;
   if(HttpRequest("POST", "/api/mt5/account-info", body, response, status))
   {
      g_lastAccountState = stateSig;
      g_lastAccountSent = GetTickCount64();
   }
   else
   {
      string errSig = "ERR|" + IntegerToString(status) + "|" + response;
      if(errSig != g_lastAccountState)
      {
         g_lastAccountState = errSig;
         Print("[ACCOUNT] Sync failed. HTTP=", status, " response=", response);
      }
   }
}

double ReadCommissionPerLotFromHistory(string symbol, bool &hasHistory)
{
   hasHistory=false;
   datetime toTime=TimeCurrent();
   datetime fromTime=toTime-(datetime)(30*86400);
   if(!HistorySelect(fromTime,toTime)) return 0.0;

   double totalCommission=0.0;
   double openedVolume=0.0;
   int matchedDeals=0;

   for(int i=HistoryDealsTotal()-1;i>=0;i--)
   {
      ulong ticket=HistoryDealGetTicket(i);
      if(ticket==0 || HistoryDealGetString(ticket,DEAL_SYMBOL)!=symbol) continue;

      long entry=HistoryDealGetInteger(ticket,DEAL_ENTRY);
      if(entry!=DEAL_ENTRY_IN && entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_INOUT) continue;

      matchedDeals++;
      totalCommission+=MathAbs(HistoryDealGetDouble(ticket,DEAL_COMMISSION));
      if(entry==DEAL_ENTRY_IN || entry==DEAL_ENTRY_INOUT)
         openedVolume+=HistoryDealGetDouble(ticket,DEAL_VOLUME);
   }

   if(matchedDeals<=0 || openedVolume<=0) return 0.0;
   hasHistory=true;
   return totalCommission/openedVolume;
}

void PublishCommissionSnapshot()
{
   if(!EnableCommissionSync || g_netBusy) return;

   string symbols[];
   string accountId = IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN));

   int count = StringSplit(CommissionSymbols, ',', symbols);
   if(count <= 0) return;

   string items = "[";
   string stateSig = "acc=" + accountId + "|";
   int sent = 0;

   for(int i = 0; i < count; i++)
   {
      string symbol = symbols[i];
      StringTrimLeft(symbol); StringTrimRight(symbol); StringToUpper(symbol);
      if(symbol == "" || !SymbolSelect(symbol, true)) continue;

      bool hasHistory = false;
      double commission = ReadCommissionPerLotFromHistory(symbol, hasHistory);
      if(!hasHistory) continue;

      double point        = SymbolInfoDouble(symbol, SYMBOL_POINT);
      double tickValue    = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
      double contractSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE);
      if(point <= 0) continue;

      double swapLong  = SymbolInfoDouble(symbol, SYMBOL_SWAP_LONG);
      double swapShort = SymbolInfoDouble(symbol, SYMBOL_SWAP_SHORT);

      if(sent > 0) items += ",";
      items += "{\"symbol\":\"" + JsonEscape(symbol) + "\""
             + ",\"commission_per_lot\":" + DoubleToString(commission, 6)
             + ",\"swap_long\":" + DoubleToString(swapLong, 8)
             + ",\"swap_short\":" + DoubleToString(swapShort, 8)
             + ",\"contract_size\":" + DoubleToString(contractSize, 2)
             + ",\"tick_value\":" + DoubleToString(tickValue, 8)
             + ",\"point_size\":" + DoubleToString(point, 10)
             + ",\"source_updated_at\":" + IntegerToString((int)TimeCurrent()) + "}";

      stateSig += symbol + ":" + DoubleToString(commission, 6) + ":" +
                  DoubleToString(swapLong, 6) + ":" + DoubleToString(swapShort, 6) + ";";
      sent++;
   }

   items += "]";
   if(sent <= 0) return;

   string body = "{\"account_id\":\"" + accountId + "\",\"items\":" + items + "}";
   string response;
   int status = 0;

   if(HttpRequest("POST", "/api/mt5/commissions", body, response, status))
   {
      string okState = "OK|" + stateSig;
      if(okState != g_lastCommissionState)
      {
         g_lastCommissionState = okState;
         Print("[COMMISSION] History snapshot synced: ", sent, " symbols for account ", accountId);
      }
   }
   else
   {
      string errState = "ERR|" + IntegerToString(status) + "|" + response;
      if(errState != g_lastCommissionState)
      {
         g_lastCommissionState = errState;
         Print("[COMMISSION] Sync failed. HTTP=", status, " response=", response);
      }
   }
}

void SendHeartbeat()
{
   if(g_netBusy) return;
   string response; int status;
   long   login      = (long)AccountInfoInteger(ACCOUNT_LOGIN);
   string currency   = AccountInfoString(ACCOUNT_CURRENCY);
   double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity     = AccountInfoDouble(ACCOUNT_EQUITY);
   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);

   string details = StringFormat(
      "acc=%I64d (%s); bal=%.2f %s; eq=%.2f %s; free=%.2f %s",
      login, GetAccountModeText(), balance, currency, equity, currency, freeMargin, currency
   );
   string body = "{\"component\":\"MT5-Connector\",\"status\":\"online\",\"details\":\"" + JsonEscape(details) + "\"}";
   if(HttpRequest("POST","/api/heartbeat",body,response,status))
      g_lastHeartbeat=GetTickCount64();
   else
      Print("[SERVER] Heartbeat failed. HTTP=",status," response=",response);

   // Chuyen tiep heartbeat cua indicator (indicator khong goi duoc WebRequest)
   if(GlobalVariableCheck("HB_RSI_Indicator") &&
      (long)NowGmt7()-(long)GlobalVariableGet("HB_RSI_Indicator")<=30)
      HttpRequest("POST","/api/heartbeat",
         "{\"component\":\"rsi-divergence-indicator\",\"status\":\"online\",\"details\":\"via ServerConnector\"}",
         response,status);
}

void ImportLegacyDivergenceSignal()
{
   if(!EnableLegacyDivergenceImport || g_netBusy) return;
   string prefix="DIVSIGNAL_";
   if(!GlobalVariableCheck(prefix+"ACTIVE") || GlobalVariableGet(prefix+"ACTIVE")<0.5) return;
   if(!GlobalVariableCheck(prefix+"TIME") || !GlobalVariableCheck(prefix+"TYPE") ||
      !GlobalVariableCheck(prefix+"ENTRY") || !GlobalVariableCheck(prefix+"SL")) return;

   int file=FileOpen("DIVSIGNAL_SYMBOL.txt",FILE_READ|FILE_TXT|FILE_COMMON);
   if(file==INVALID_HANDLE) return;
   string line=FileReadString(file);
   FileClose(file);
   string parts[];
   int count=StringSplit(line,'|',parts);
   if(count<2) return;

   string symbol=parts[0];
   string timeframe=parts[1];
   int type=(int)GlobalVariableGet(prefix+"TYPE");
   double entry=GlobalVariableGet(prefix+"ENTRY");
   double sl=GlobalVariableGet(prefix+"SL");
   datetime signalTime=(datetime)GlobalVariableGet(prefix+"TIME");
   double score=0;
   if(GlobalVariableCheck(prefix+"SCORE")) score=GlobalVariableGet(prefix+"SCORE");

   string signature=IntegerToString((int)signalTime)+"|"+symbol+"|"+timeframe+"|"+IntegerToString(type);
   if(signature==g_lastLegacySignature) return;
   if(symbol=="" || entry<=0 || sl<=0 || signalTime<=0) return;
   if(NowGmt7()-signalTime>MaxLegacySignalAgeSeconds) return;

   string direction=(type>0)?"BUY":"SELL";
   string source="div-"+symbol+"-"+timeframe+"-"+IntegerToString((int)signalTime);
   string body=StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\",\"entry\":%.8f,\"sl\":%.8f,\"score\":%.2f,\"source\":\"%s\"}",
      JsonEscape(symbol),JsonEscape(timeframe),direction,entry,sl,score,JsonEscape(source));
   string response; int status;
   if(HttpRequest("POST","/api/signals",body,response,status))
   {
      g_lastLegacySignature=signature;
      Print("[SERVER] Imported legacy divergence: ",symbol," ",timeframe," ",direction,
            " signal_id=",JsonValue(response,"signal_id"));
   }
   else
      Print("[SERVER] Legacy signal import failed. HTTP=",status," response=",response);
}

void ImportDivergenceQueue()
{
   if(g_netBusy) return;
   int file=FileOpen("DIVSIGNAL_QUEUE.txt",FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(file==INVALID_HANDLE) return;
   long cursor=0;
   if(GlobalVariableCheck(g_queueCursorName)) cursor=(long)GlobalVariableGet(g_queueCursorName);
   // File bi xoa/ghi lai (it dong hon con tro) thi doc lai tu dau
   long totalLines=0;
   while(!FileIsEnding(file)) { FileReadString(file); totalLines++; }
   if(totalLines<cursor) { cursor=0; GlobalVariableSet(g_queueCursorName,0); }
   FileSeek(file,0,SEEK_SET);
   long lineNo=0;
   while(!FileIsEnding(file))
   {
      string line=FileReadString(file);
      if(lineNo++<cursor) continue;
      if(line=="") { GlobalVariableSet(g_queueCursorName,(double)lineNo); continue; }
      string p[];
      if(StringSplit(line,'|',p)<8) { GlobalVariableSet(g_queueCursorName,(double)lineNo); continue; }
      string symbol=p[0], tf=p[1], direction=p[2], divType=p[3];
      datetime signalTime=(datetime)StringToInteger(p[4]);
      double entry=StringToDouble(p[5]), sl=StringToDouble(p[6]), score=StringToDouble(p[7]);
      datetime detectedAt=(ArraySize(p)>=9)?(datetime)StringToInteger(p[8]):signalTime;
      if(symbol=="" || (direction!="BUY" && direction!="SELL") || signalTime<=0 || entry<=0 || sl<=0)
      { GlobalVariableSet(g_queueCursorName,(double)lineNo); continue; }

      if(detectedAt<=0 || NowGmt7()-detectedAt>MaxLegacySignalAgeSeconds)
      {
         GlobalVariableSet(g_queueCursorName,(double)lineNo);
         Print("[SERVER] Skipped expired queued signal: ",symbol," ",tf," ",direction);
         continue;
      }

      string source=StringFormat("div-%d-%d",(int)signalTime,(int)lineNo);
      string body=StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\",\"entry\":%.8f,\"sl\":%.8f,\"score\":%.2f,\"source\":\"%s\"}",
         JsonEscape(symbol),JsonEscape(tf),direction,entry,sl,score,JsonEscape(source));
      string response; int status;
      if(!HttpRequest("POST","/api/signals",body,response,status))
      {
         if(status==400 || status==422)
         {
            GlobalVariableSet(g_queueCursorName,(double)lineNo);
            Print("[SERVER] Invalid queued signal skipped. HTTP=",status," response=",response);
            continue;
         }
         Print("[SERVER] Divergence queue upload failed. HTTP=",status," response=",response);
         break;
      }
      GlobalVariableSet(g_queueCursorName,(double)lineNo);
      Print("[SERVER] Divergence uploaded: ",symbol," ",tf," ",direction," ",divType,
            " signal_id=",JsonValue(response,"signal_id"));
   }
   FileClose(file);
}

void PublishScanStatus()
{
   if(g_netBusy || !Due(g_lastScanPost,3)) return;
   g_lastScanPost=GetTickCount64();
   string raw="";
   int file=FileOpen("DIVSCAN_SYMBOLS.txt",FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(file!=INVALID_HANDLE)
   {
      raw=FileReadString(file);
      FileClose(file);
   }
   string fields[];
   int fieldCount=StringSplit(raw,'#',fields);
   datetime scanTime=0;
   string symbolsJson="[]";
   if(fieldCount>=1) scanTime=(datetime)StringToInteger(fields[0]);
   if(fieldCount>=2 && scanTime>0 && NowGmt7()-scanTime<=30)
   {
      string entries[];
      int entryCount=StringSplit(fields[1],';',entries);
      string jsonItems="";
      for(int i=0;i<entryCount;i++)
      {
         string pair[];
         if(StringSplit(entries[i],'|',pair)<2) continue;
         string item=pair[0]+" "+pair[1];
         if(item==" ") continue;
         if(jsonItems!="") jsonItems+=",";
         jsonItems+="\""+JsonEscape(item)+"\"";
      }
      symbolsJson="["+jsonItems+"]";
   }
   string body="{\"component\":\"AI-OOP-V8\",\"symbols\":"+symbolsJson+"}";
   if(body == g_lastScanPayload && !Due(g_lastScanSent,10)) return;

   string response; int status;
   if(HttpRequest("POST","/api/scans",body,response,status))
   {
      g_lastScanPayload = body;
      g_lastScanSent = GetTickCount64();
   }
   else
      Print("[SERVER] Scan status upload failed. HTTP=",status," response=",response);
}

void PublishTodaySignals()
{
   if(g_netBusy) return;
   string items="";
   int itemCount=0;
   int file=FileOpen("DIVTODAY_SIGNALS.txt",FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(file==INVALID_HANDLE) return;
   {
      if(!FileIsEnding(file)) FileReadString(file);
      while(!FileIsEnding(file) && itemCount<200) // server nhan toi da 200
      {
         string line=FileReadString(file);
         if(line=="") continue;
         string p[];
         if(StringSplit(line,'|',p)<10) continue;
         string symbol=p[0], tf=p[1], direction=p[2], divType=p[3], sigType=p[4];
         long signalTime=StringToInteger(p[5]);
         datetime pivotTime1=0,pivotTime2=0;
         if(ArraySize(p)>=12)
         {
            pivotTime1=(datetime)StringToInteger(p[10]);
            pivotTime2=(datetime)StringToInteger(p[11]);
         }
         if(symbol=="" || (direction!="BUY" && direction!="SELL") || signalTime<=0) continue;
         if(sigType!="RT" && sigType!="HIST") sigType="RT";
         itemCount++;
         if(items!="") items+=",";
         items+=StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\",\"divergence\":\"%s\",\"signal_type\":\"%s\",\"signal_time\":%d,\"price1\":%.8f,\"price2\":%.8f,\"rsi1\":%.2f,\"rsi2\":%.2f,\"time1\":%d,\"time2\":%d}",
            JsonEscape(symbol),JsonEscape(tf),direction,JsonEscape(divType),sigType,(int)signalTime,
            StringToDouble(p[6]),StringToDouble(p[7]),StringToDouble(p[8]),StringToDouble(p[9]),
            (int)pivotTime1,(int)pivotTime2);
      }
      FileClose(file);
   }
   // Do lech gio broker so voi UTC (lam tron 15 phut) de server loc dung tin hieu trong ngay
   int brokerOffset=(int)MathRound((double)(TimeTradeServer()-TimeGMT())/900.0)*900;
   string body="{\"broker_offset_sec\":"+IntegerToString(brokerOffset)+",\"signals\":["+items+"]}";
   if(body == g_lastTodaySignalsPayload && !Due(g_lastTodaySent,30)) return;

   string response; int status;
   if(HttpRequest("POST","/api/today-signals",body,response,status))
   {
      g_lastTodaySignalsPayload = body;
      g_lastTodaySent = GetTickCount64();
   }
   else
      Print("[SERVER] Today signals upload failed. HTTP=",status," response=",response);
}

ENUM_TIMEFRAMES ChartTimeframeFromText(string tf)
{
   if(tf=="M1") return PERIOD_M1;
   if(tf=="M5") return PERIOD_M5;
   if(tf=="M15") return PERIOD_M15;
   if(tf=="M30") return PERIOD_M30;
   if(tf=="H1") return PERIOD_H1;
   if(tf=="H4") return PERIOD_H4;
   if(tf=="D1") return PERIOD_D1;
   return PERIOD_CURRENT;
}

int BuildChartTargets(string &symbols[], string &timeframes[])
{
   ArrayResize(symbols, 0);
   ArrayResize(timeframes, 0);

   string raw="";
   int file=FileOpen("DIVSCAN_SYMBOLS.txt",FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(file!=INVALID_HANDLE)
   {
      raw=FileReadString(file);
      FileClose(file);
   }

   string fields[];
   if(StringSplit(raw,'#',fields)>=2)
   {
      datetime scanTime=(datetime)StringToInteger(fields[0]);
      if(scanTime>0 && NowGmt7()-scanTime<=60)
      {
         string entries[];
         int count=StringSplit(fields[1],';',entries);
         for(int i=0;i<count;i++)
         {
            string pair[];
            if(StringSplit(entries[i],'|',pair)<2) continue;
            string s=pair[0], t=pair[1];
            if(s=="" || t=="") continue;
            if(ChartTimeframeFromText(t)==PERIOD_CURRENT) continue;
            bool dup=false;
            for(int k=0;k<ArraySize(symbols);k++)
               if(symbols[k]==s && timeframes[k]==t) { dup=true; break; }
            if(dup) continue;
            int n=ArraySize(symbols);
            ArrayResize(symbols,n+1); ArrayResize(timeframes,n+1);
            symbols[n]=s; timeframes[n]=t;
         }
      }
   }

   if(ArraySize(symbols)==0)
   {
      string symList[];
      string tfList[];
      int sc=StringSplit(FallbackSymbols,',',symList);
      int tc=StringSplit(FallbackTimeframes,',',tfList);
      if(sc>0 && tc>0)
      {
         for(int i=0;i<sc;i++)
         {
            string s=symList[i];
            StringTrimLeft(s); StringTrimRight(s); StringToUpper(s);
            if(s=="") continue;
            for(int j=0;j<tc;j++)
            {
               string t=tfList[j];
               StringTrimLeft(t); StringTrimRight(t); StringToUpper(t);
               if(ChartTimeframeFromText(t)==PERIOD_CURRENT) continue;
               int n=ArraySize(symbols);
               ArrayResize(symbols,n+1); ArrayResize(timeframes,n+1);
               symbols[n]=s; timeframes[n]=t;
            }
         }
      }
   }
   return ArraySize(symbols);
}

double GetRealSymbolAsk(string symbol)
{
   MqlTick tick;
   if(SymbolInfoTick(symbol, tick) && tick.ask > 0.0 && MathIsValidNumber(tick.ask))
      return tick.ask;
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   if(ask > 0.0 && MathIsValidNumber(ask))
      return ask;
   return 0.0;
}

int FindCandleCacheIndex(string key)
{
   for(int i=0; i<ArraySize(g_candleKeys); i++)
      if(g_candleKeys[i] == key) return i;
   int n = ArraySize(g_candleKeys);
   ArrayResize(g_candleKeys, n+1);
   ArrayResize(g_candleDigests, n+1);
   ArrayResize(g_candleFullMs, n+1);
   g_candleFullMs[n] = 0;
   g_candleKeys[n] = key;
   g_candleDigests[n] = "";
   return n;
}

bool PublishCandleBatch(string symbol, string tfText, ENUM_TIMEFRAMES period, bool forceFull)
{
   if(g_netBusy || symbol=="" || period==PERIOD_CURRENT) return false;
   if(!SymbolSelect(symbol,true)) return false;

   if(!SeriesInfoInteger(symbol, period, SERIES_SYNCHRONIZED))
   {
      MqlRates warmup[];
      CopyRates(symbol, period, 0, 10, warmup);
      return false;
   }

   string cacheKey = symbol + "|" + tfText;
   int cacheIdx = FindCandleCacheIndex(cacheKey);

   int want = forceFull
      ? MathMax(50, MathMin(1000, ChartBarsToSend))
      : MathMax(5, MathMin(200, FastCandleBars));

   MqlRates rates[];
   ArraySetAsSeries(rates,false);
   int copied = CopyRates(symbol, period, 0, want, rates);
   if(copied < 2) return false;

   string candles="[";
   int validCount=0;

   for(int i=0;i<copied;i++)
   {
      double o=rates[i].open, h=rates[i].high, l=rates[i].low, c=rates[i].close;
      if(rates[i].time<=0 || o<=0 || h<=0 || l<=0 || c<=0) continue;
      if(h<MathMax(o,MathMax(c,l)) || l>MathMin(o,MathMin(c,h))) continue;
      if(validCount>0) candles+=",";
      long vol = (long)MathMax((long)0,rates[i].tick_volume);
      candles+="{\"time\":"+IntegerToString((int)rates[i].time)+
               ",\"open\":"+DoubleToString(o,10)+
               ",\"high\":"+DoubleToString(h,10)+
               ",\"low\":"+DoubleToString(l,10)+
               ",\"close\":"+DoubleToString(c,10)+
               ",\"volume\":"+IntegerToString(vol)+"}";
      validCount++;
   }
   candles+="]";
   if(validCount<2) return false;

   MqlRates lastBar = rates[copied-1];
   string digest = StringFormat("%d|%d|%.10f|%.10f|%.10f|%.10f|%I64d",
                                validCount, (int)lastBar.time, lastBar.open, lastBar.high,
                                lastBar.low, lastBar.close, (long)lastBar.tick_volume);

   if(!forceFull && digest == g_candleDigests[cacheIdx])
      return true;

   int digits=(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS);
   double tickSize=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE);
   double tickValue=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickValue<=0) tickValue=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE);
   if(tickSize<=0) tickSize=SymbolInfoDouble(symbol,SYMBOL_POINT);
   double volumeMin=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN);
   double volumeMax=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MAX);
   double volumeStep=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP);
   double askPrice=GetRealSymbolAsk(symbol);

   string askJson="";
   if(askPrice>0.0)
      askJson=StringFormat(",\"ask\":%.10f,\"ask_price\":%.10f",askPrice,askPrice);

   string body=StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"digits\":%d,\"tick_size\":%.10f,\"tick_value\":%.10f,\"volume_min\":%.8f,\"volume_max\":%.8f,\"volume_step\":%.8f%s,\"candles\":%s}",
      JsonEscape(symbol),JsonEscape(tfText),digits,tickSize,tickValue,volumeMin,volumeMax,volumeStep,askJson,candles);

   string response; int status;
   bool posted=HttpRequest("POST","/api/mt5/candles",body,response,status,2200);
   if(!posted)
   {
      Print("[CHART FEED] Upload failed for ",symbol," ",tfText,". HTTP=",status," response=",response);
      return false;
   }

   g_candleDigests[cacheIdx] = digest;
   g_candleBatchCount++;
   return true;
}

// Moi lan chi gui vai cap symbol/timeframe de khong chan EA va khong don tai len server
void PublishChartCandles()
{
   if(g_netBusy) return;
   string symbols[];
   string timeframes[];
   int total = BuildChartTargets(symbols, timeframes);
   if(total<=0) return;

   int batch = MathMin(total, MathMax(1, ChartItemsPerSecond));
   for(int n=0;n<batch;n++)
   {
      int i = g_chartCursor % total;
      g_chartCursor = (i + 1) % total;
      int cacheIdx = FindCandleCacheIndex(symbols[i] + "|" + timeframes[i]);
      bool forceFull = Due(g_candleFullMs[cacheIdx], MathMax(15, FullCandleSyncSeconds));
      if(PublishCandleBatch(symbols[i], timeframes[i], ChartTimeframeFromText(timeframes[i]), forceFull) && forceFull)
         g_candleFullMs[cacheIdx] = GetTickCount64();

      // Day tick xen ke de gia khong bi tre khi gui nen
      BufferAllTicks();
      FlushBufferedTicks();
   }
}

void PublishRealHistory()
{
   if(!EnableRealHistorySync || g_netBusy) return;
   datetime now=TimeCurrent();
   datetime fromTime=now-(datetime)(MathMax(1,HistoryLookbackDays)*86400);
   if(!HistorySelect(fromTime,now)) return;

   int totalDeals=HistoryDealsTotal();
   if(totalDeals<=0) return;

   string tradesJson="";
   int count=0;
   for(int i=totalDeals-1; i>=0 && count<200; i--)
   {
      ulong dealTicket=HistoryDealGetTicket(i);
      if(dealTicket==0) continue;
      long entryType=HistoryDealGetInteger(dealTicket,DEAL_ENTRY);
      if(entryType!=DEAL_ENTRY_OUT && entryType!=DEAL_ENTRY_INOUT) continue;

      string sym=HistoryDealGetString(dealTicket,DEAL_SYMBOL);
      if(sym=="") continue;
      long dealType=HistoryDealGetInteger(dealTicket,DEAL_TYPE);
      string dir=(dealType==DEAL_TYPE_BUY)?"SELL":"BUY";
      double vol=HistoryDealGetDouble(dealTicket,DEAL_VOLUME);
      double price=HistoryDealGetDouble(dealTicket,DEAL_PRICE);
      double profit=HistoryDealGetDouble(dealTicket,DEAL_PROFIT)
                  +HistoryDealGetDouble(dealTicket,DEAL_SWAP)
                  +HistoryDealGetDouble(dealTicket,DEAL_COMMISSION);
      long closedAt=HistoryDealGetInteger(dealTicket,DEAL_TIME);
      if(vol<=0 || closedAt<=0) continue;

      if(tradesJson!="") tradesJson+=",";
      tradesJson+=StringFormat(
         "{\"id\":\"mt5-%I64u\",\"ticket\":\"%I64u\",\"symbol\":\"%s\",\"timeframe\":\"MT5\",\"direction\":\"%s\",\"entry\":%.8f,\"sl\":0,\"tp\":0,\"volume\":%.4f,\"profit\":%.2f,\"outcome\":\"CLOSED\",\"opened_at\":0,\"closed_at\":%I64d}",
         dealTicket,dealTicket,JsonEscape(sym),dir,price,vol,NormalizeDouble(profit,2),closedAt
      );
      count++;
   }

   if(count<=0 || tradesJson==g_lastHistoryDigest) return;
   string body="{\"replace\":false,\"trades\":["+tradesJson+"]}";
   string response; int status;
   if(HttpRequest("POST","/api/mt5/history",body,response,status))
      g_lastHistoryDigest=tradesJson;
   else
      Print("[HISTORY] Real history sync failed. HTTP=",status," response=",response);
}

int OnInit()
{
   if(StringFind(ServerBaseURL,"http://127.0.0.1")!=0 && StringFind(ServerBaseURL,"https://")!=0)
   {
      Print("Use localhost or HTTPS server URL.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(ApiKey=="" || ApiKey=="SET_A_LONG_RANDOM_API_KEY")
   {
      Print("[SERVER] Set ApiKey privately in EA inputs.");
      return INIT_PARAMETERS_INCORRECT;
   }
   EventSetTimer(1);
   RenderConnectionComment();
   Print("ServerConnector v1.53 initialized. Buffer OnTick + coalesced batch tick flush.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment("");
}

void OnTick()
{
   BufferTick(_Symbol);
}

void OnTimer()
{
   BufferAllTicks();
   FlushBufferedTicks();

   if(Due(g_lastHeartbeat, MathMax(5,HeartbeatSeconds))) SendHeartbeat();

   if(EnableAccountSync && Due(g_lastAccountPost, MathMax(1,AccountSyncSeconds)))
   {
      g_lastAccountPost=GetTickCount64();
      PublishAccountInfo();
   }

   if(EnableCommissionSync && Due(g_lastCommissionPost, MathMax(60,CommissionSyncSeconds)))
   {
      g_lastCommissionPost=GetTickCount64();
      PublishCommissionSnapshot();
   }

   if(EnableChartCandleFeed) PublishChartCandles();

   if(EnableRealHistorySync && Due(g_lastHistoryPost, MathMax(5,HistorySyncSeconds)))
   {
      g_lastHistoryPost=GetTickCount64();
      PublishRealHistory();
   }

   if(Due(g_lastPoll, MathMax(1,PollSeconds)))
   {
      g_lastPoll=GetTickCount64();
      PublishScanStatus();
      PublishTodaySignals();
      ImportDivergenceQueue();
      ImportLegacyDivergenceSignal();
   }

   RenderConnectionComment();
}
