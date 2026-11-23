//+------------------------------------------------------------------+
//| ServerConnector_Demo.mq5                                         |
//| Polls Python server, syncs candles/signals/history.              |
//| Chart feed only (no trade execution). Complements TRADE_V9.      |
//+------------------------------------------------------------------+
#property strict
#property version "1.13"

#include <Trade\Trade.mqh>

input string ServerBaseURL = "https://investforex.onrender.com";
input string ApiKey = "SET_A_LONG_RANDOM_API_KEY";
input string ConsumerId = "mt5-demo-vps";
input int PollSeconds = 3;
input int HeartbeatSeconds = 15;

// Legacy divergence import (AI OOP V8 writes to COMMON files)
input bool EnableLegacyDivergenceImport = false;
input int MaxLegacySignalAgeSeconds = 300;

// Signal polling: TRADE_V9_PAPER owns the server consumer by default
input bool EnableSignalPolling = false;

// Chart candle feed (this EA's main job)
input bool EnableChartCandleFeed = true;
input int ChartFeedSeconds = 10;
input int ChartBarsToSend = 500;
input string FallbackSymbols = "EURUSD,GBPUSD,USDJPY,XAUUSD,BTCUSD,USDCAD,NZDUSD,USDCHF";
input string FallbackTimeframes = "M15,H1";
 
// Commission snapshot sync (reads MT5 deal history directly; no separate indicator required)
input bool EnableCommissionSync = true;
input int CommissionSyncSeconds = 60;
input string CommissionSymbols = "XAUUSD,USDJPY,EURUSD,GBPUSD";

// Real history sync: TRADE_V9_PAPER owns /api/mt5/history by default
input bool EnableRealHistorySync = false;
input int HistorySyncSeconds = 15;
input int HistoryLookbackDays = 7;

datetime g_lastHeartbeat = 0;
datetime g_lastPoll = 0;
string g_lastLegacySignature = "";
datetime g_lastScanPost = 0;
datetime g_lastCandlePost = 0;
datetime g_lastHistoryPost = 0;
datetime g_lastCommissionPost = 0;
string g_queueCursorName = "DIVSERVER_QUEUE_CURSOR";
string g_lastHistoryDigest = "";
string g_lastChartFeedState = "";

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

bool HttpRequest(string method,string path,string body,string &response,int &status)
{
   char data[], result[];
   string headers = "X-API-Key: " + ApiKey + "\r\nContent-Type: application/json\r\n";
   if(body != "")
   {
      StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
      int n=ArraySize(data);
      if(n>0 && data[n-1]==0) ArrayResize(data,n-1);
   }
   else ArrayResize(data,0);
   string responseHeaders;
   ResetLastError();
   status = WebRequest(method,ServerBaseURL+path,headers,5000,data,result,responseHeaders);
   if(status == -1)
   {
      Print("[SERVER] WebRequest failed: ",GetLastError(),
            ". Add server URL to MT5 Tools > Options > Expert Advisors > Allow WebRequest.");
      response="";
      return false;
   }
   response=CharArrayToString(result,0,-1,CP_UTF8);
   return (status>=200 && status<300);
}

// Read commission directly from MT5 deal history; no separate commission indicator/GV required.
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
   // Aggregate observed entry + exit commissions over opened lots (account currency / lot).
   return totalCommission/openedVolume;
}

void PublishCommissionSnapshot()
{
   if(!EnableCommissionSync) return;

   string symbols[];
   string accountId = IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN));

   int count = StringSplit(CommissionSymbols, ',', symbols);
   if(count <= 0) return;

   string items = "[";
   int sent = 0;

   for(int i = 0; i < count; i++)
   {
      string symbol = symbols[i];

      StringTrimLeft(symbol);
      StringTrimRight(symbol);
      StringToUpper(symbol);

      if(symbol == "" || !SymbolSelect(symbol, true))
         continue;

      bool hasHistory = false;
      double commission = ReadCommissionPerLotFromHistory(symbol, hasHistory);

      if(!hasHistory)
         continue;

      double point        = SymbolInfoDouble(symbol, SYMBOL_POINT);
      double tickValue    = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
      double contractSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE);

      if(point <= 0)
         continue;

      double swapLong  = SymbolInfoDouble(symbol, SYMBOL_SWAP_LONG);
      double swapShort = SymbolInfoDouble(symbol, SYMBOL_SWAP_SHORT);

      if(sent > 0)
         items += ",";

      items += "{\"symbol\":\"" + JsonEscape(symbol) + "\"";
      items += ",\"commission_per_lot\":" + DoubleToString(commission, 6);
      items += ",\"swap_long\":" + DoubleToString(swapLong, 8);
      items += ",\"swap_short\":" + DoubleToString(swapShort, 8);
      items += ",\"contract_size\":" + DoubleToString(contractSize, 2);
      items += ",\"tick_value\":" + DoubleToString(tickValue, 8);
      items += ",\"point_size\":" + DoubleToString(point, 10);
      items += ",\"source_updated_at\":" + IntegerToString((int)TimeCurrent()) + "}";

      sent++;
   }

   items += "]";

   if(sent <= 0)
      return;

   string body = "{\"account_id\":\"" + accountId + "\",\"items\":" + items + "}";

   string response;
   int status = 0;

   if(HttpRequest("POST", "/api/mt5/commissions", body, response, status))
   {
      Print("[COMMISSION] History snapshot synced: ",
            sent, " symbols for account ", accountId);
   }
   else
   {
      Print("[COMMISSION] Sync failed. HTTP=", status,
            " response=", response);
   }
}

void SendHeartbeat()
{
   string response; int status;
   string body="{\"component\":\"MT5-Connector\",\"status\":\"online\",\"details\":\"Connector active; candle/history feed only\"}";
   if(HttpRequest("POST","/api/heartbeat",body,response,status))
      g_lastHeartbeat=TimeCurrent();
   else
      Print("[SERVER] Heartbeat failed. HTTP=",status," response=",response);
}

bool AckSignal(string id,string result,string message)
{
   string response; int status;
   string body="{\"signal_id\":\""+JsonEscape(id)+"\",\"status\":\""+JsonEscape(result)+"\",\"message\":\""+JsonEscape(message)+"\"}";
   bool ok=HttpRequest("POST","/api/mt5/ack",body,response,status);
   if(!ok) Print("[SERVER] Ack failed. HTTP=",status," response=",response);
   return ok;
}

void ImportLegacyDivergenceSignal()
{
   if(!EnableLegacyDivergenceImport) return;
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
   if(TimeCurrent()-signalTime>MaxLegacySignalAgeSeconds) return;

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
   int file=FileOpen("DIVSIGNAL_QUEUE.txt",FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(file==INVALID_HANDLE) return;
   long cursor=0;
   if(GlobalVariableCheck(g_queueCursorName)) cursor=(long)GlobalVariableGet(g_queueCursorName);
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

      if(detectedAt<=0 || TimeCurrent()-detectedAt>MaxLegacySignalAgeSeconds)
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
   if(TimeCurrent()-g_lastScanPost<3) return;
   g_lastScanPost=TimeCurrent();
   string raw="";
   int file=FileOpen("DIVSCAN_SYMBOLS.txt",FILE_READ|FILE_TXT|FILE_COMMON);
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
   if(fieldCount>=2 && scanTime>0 && TimeCurrent()-scanTime<=30)
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
   string response; int status;
   if(!HttpRequest("POST","/api/scans",body,response,status))
      Print("[SERVER] Scan status upload failed. HTTP=",status," response=",response);
}

void PublishTodaySignals()
{
   string items="";
   int file=FileOpen("DIVTODAY_SIGNALS.txt",FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(file==INVALID_HANDLE) return;
   {
      if(!FileIsEnding(file)) FileReadString(file); // GMT+7 snapshot header
      while(!FileIsEnding(file))
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
         if(items!="") items+=",";
         items+=StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\",\"divergence\":\"%s\",\"signal_type\":\"%s\",\"signal_time\":%d,\"price1\":%.8f,\"price2\":%.8f,\"rsi1\":%.2f,\"rsi2\":%.2f,\"time1\":%d,\"time2\":%d}",
            JsonEscape(symbol),JsonEscape(tf),direction,JsonEscape(divType),sigType,(int)signalTime,
            StringToDouble(p[6]),StringToDouble(p[7]),StringToDouble(p[8]),StringToDouble(p[9]),
            (int)pivotTime1,(int)pivotTime2);
      }
      FileClose(file);
   }
   string body="{\"signals\":["+items+"]}";
   string response; int status;
   if(!HttpRequest("POST","/api/today-signals",body,response,status))
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

// Build "SYM|TF" list: prefer live scan file, fallback to fixed inputs
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
      if(scanTime>0 && TimeCurrent()-scanTime<=60)
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

bool PublishCandleBatch(string symbol, string tfText, ENUM_TIMEFRAMES period)
{
   if(symbol=="" || period==PERIOD_CURRENT) return false;
   if(!SymbolSelect(symbol,true)) return false;

   if(!SeriesInfoInteger(symbol, period, SERIES_SYNCHRONIZED))
   {
      // Trigger history load; next call will succeed
      MqlRates warmup[];
      CopyRates(symbol, period, 0, 10, warmup);
      return false;
   }

   MqlRates rates[];
   ArraySetAsSeries(rates,false);
   int want = MathMax(50, MathMin(1000, ChartBarsToSend));
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
      candles+="{\"time\":"+IntegerToString((int)rates[i].time)+
               ",\"open\":"+DoubleToString(o,10)+
               ",\"high\":"+DoubleToString(h,10)+
               ",\"low\":"+DoubleToString(l,10)+
               ",\"close\":"+DoubleToString(c,10)+
               ",\"volume\":"+IntegerToString((long)MathMax((long)0,rates[i].tick_volume))+"}";
      validCount++;
   }
   candles+="]";
   if(validCount<2) return false;

   int digits=(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS);
   double tickSize=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE);
   double tickValue=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickValue<=0) tickValue=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE);
   if(tickSize<=0) tickSize=SymbolInfoDouble(symbol,SYMBOL_POINT);
   double volumeMin=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN);
   double volumeMax=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MAX);
   double volumeStep=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP);
   string body=StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"digits\":%d,\"tick_size\":%.10f,\"tick_value\":%.10f,\"volume_min\":%.8f,\"volume_max\":%.8f,\"volume_step\":%.8f,\"candles\":%s}",
      JsonEscape(symbol),JsonEscape(tfText),digits,tickSize,tickValue,volumeMin,volumeMax,volumeStep,candles);
   string response; int status;
   if(!HttpRequest("POST","/api/mt5/candles",body,response,status))
   {
      Print("[CHART FEED] Upload failed for ",symbol," ",tfText,". HTTP=",status," response=",response);
      return false;
   }
   return true;
}

// Chỉ log khi danh sách symbol/TF thay đổi hoặc trạng thái kết nối/gửi (ok/fail) thay đổi; bỏ qua nến/giá mới.
void PublishChartCandles()
{
   string symbols[];
   string timeframes[];
   int total = BuildChartTargets(symbols, timeframes);
   if(total<=0) return;

   int ok=0, fail=0;
   string targetSig = "";
   string perItemStatus = "";

   for(int i=0;i<total;i++)
   {
      ENUM_TIMEFRAMES period=ChartTimeframeFromText(timeframes[i]);
      bool sent = PublishCandleBatch(symbols[i], timeframes[i], period);
      if(sent) ok++;
      else fail++;

      targetSig += symbols[i] + "|" + timeframes[i] + ";";
      perItemStatus += symbols[i] + "|" + timeframes[i] + ":" + (sent ? "OK" : "FAIL") + ";";
   }

   string stateSig = "targets=" + targetSig +
                     "#ok=" + IntegerToString(ok) +
                     "/fail=" + IntegerToString(fail) +
                     "/total=" + IntegerToString(total) +
                     "#items=" + perItemStatus;

   if(stateSig == g_lastChartFeedState) return;

   g_lastChartFeedState = stateSig;
   if(ok>0 || fail>0)
      Print("[CHART FEED] batch done: ok=",ok," skip=",fail," total=",total," | ",targetSig);
}

void PublishRealHistory()
{
   if(!EnableRealHistorySync) return;
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

// Optional: only if EnableSignalPolling=true and TRADE_V9 is off
void PollSignal()
{
   if(!EnableSignalPolling) return;
   string response; int status;
   string path="/api/mt5/next?consumer="+ConsumerId;
   if(!HttpRequest("GET",path,"",response,status))
   {
      Print("[SERVER] Poll failed. HTTP=",status," response=",response);
      return;
   }
   if(StringFind(response,"\"signal\":null")>=0) return;
   string signalJson=JsonObjectValue(response,"signal");
   if(signalJson=="")
   {
      Print("[SERVER] Invalid response: missing signal object: ",response);
      return;
   }
   string id=JsonValue(signalJson,"id");
   string symbol=JsonValue(signalJson,"symbol");
   string tf=JsonValue(signalJson,"timeframe");
   string direction=JsonValue(signalJson,"direction");
   string orderType=JsonValue(signalJson,"order_type");
   if(orderType=="") orderType="MARKET";
   string entry=JsonValue(signalJson,"entry");
   string sl=JsonValue(signalJson,"sl");
   string created=JsonValue(signalJson,"created_at");
   if(id=="" || symbol=="" || (direction!="BUY" && direction!="SELL") || entry=="" || sl=="")
   {
      Print("[SERVER] Invalid signal payload: ",response);
      return;
   }
   if(StringFind(orderType,"STOP")>=0 ||
      (orderType!="MARKET" && orderType!="BUY_LIMIT" && orderType!="SELL_LIMIT"))
   {
      AckSignal(id,"REJECTED","Unsupported order_type (LIMIT/MARKET only)");
      return;
   }
   double entryPrice=StringToDouble(entry), slPrice=StringToDouble(sl);
   bool valid=(entryPrice>0 && slPrice>0 &&
      ((direction=="BUY" && slPrice<entryPrice) || (direction=="SELL" && slPrice>entryPrice)));
   if(!valid)
   {
      AckSignal(id,"REJECTED","Invalid entry/SL geometry");
      return;
   }
   Print("[CONNECTOR SIGNAL] ",symbol," ",tf," ",orderType," ",direction,
         " entry=",DoubleToString(entryPrice,8)," SL=",DoubleToString(slPrice,8),
         " id=",id," created=",created);
   AckSignal(id,"PLACED","Signal acknowledged by connector (no trade placed)");
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
   Print("ServerConnector v1.13 initialized. Chart feed + AI OOP V8 sync only. ",
         "Add ",ServerBaseURL," to MT5 WebRequest allowlist.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason){ EventKillTimer(); }

void OnTick() {}

void OnTimer()
{
   if(TimeCurrent()-g_lastHeartbeat>=HeartbeatSeconds) SendHeartbeat();

   if(EnableCommissionSync && TimeCurrent()-g_lastCommissionPost>=MathMax(15,CommissionSyncSeconds))
   {
      g_lastCommissionPost=TimeCurrent();
      PublishCommissionSnapshot();
   }

   if(EnableChartCandleFeed && TimeCurrent()-g_lastCandlePost>=MathMax(5,ChartFeedSeconds))
   {
      g_lastCandlePost=TimeCurrent();
      PublishChartCandles();
   }

   if(EnableRealHistorySync && TimeCurrent()-g_lastHistoryPost>=MathMax(5,HistorySyncSeconds))
   {
      g_lastHistoryPost=TimeCurrent();
      PublishRealHistory();
   }

   if(TimeCurrent()-g_lastPoll>=PollSeconds)
   {
      g_lastPoll=TimeCurrent();
      PublishScanStatus();
      PublishTodaySignals();
      ImportDivergenceQueue();
      ImportLegacyDivergenceSignal();
      PollSignal(); // no-op unless EnableSignalPolling=true
   }
}
