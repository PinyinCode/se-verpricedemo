//+------------------------------------------------------------------+
//| ServerConnector_Demo.mq5                                         |
//| Polls Python server, syncs candles/signals/history, and handles  |
//| optional close requests without opening new live trades.         |
//+------------------------------------------------------------------+
#property strict
#property version "1.10"

#include <Trade\Trade.mqh>

input string ServerBaseURL = "https://investforex.onrender.com";
input string ApiKey = "SET_A_LONG_RANDOM_API_KEY";
input string ConsumerId = "mt5-demo-vps";
input int PollSeconds = 3;
input int HeartbeatSeconds = 15;
input bool EnableLegacyDivergenceImport = false;
input bool EnableSignalPolling = false; // TRADE_V9_PAPER owns the server consumer by default
input int MaxLegacySignalAgeSeconds = 300;
input bool EnableChartCandleFeed = true; // Uploads OHLC only; never places trades
input int ChartFeedSeconds = 5; // One symbol/timeframe per interval
input int ChartBarsToSend = 500;
input bool EnableRealHistorySync = true; // Syncs closed MT5 deals to /api/mt5/history
input int HistorySyncSeconds = 15;
input int HistoryLookbackDays = 7;
input bool EnableCloseRequests = false; // Keep false for read-only/paper-only connector

datetime g_lastHeartbeat = 0;
datetime g_lastPoll = 0;
string g_lastLegacySignature = "";
datetime g_lastScanPost = 0;
datetime g_lastCandlePost = 0;
datetime g_lastHistoryPost = 0;
int g_chartCursor = 0;
string g_queueCursorName = "DIVSERVER_QUEUE_CURSOR";
string g_lastHistoryDigest = "";
CTrade g_closeTrade;

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
      int e = StringFind(json, "\"", p+1);
      if(e < 0) return "";
      return StringSubstr(json,p+1,e-p-1);
   }
   int e = p;
   while(e < StringLen(json) && StringGetCharacter(json,e)!=',' &&
         StringGetCharacter(json,e)!='}' && StringGetCharacter(json,e)!='\n') e++;
   return StringSubstr(json,p,e-p);
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

void SendHeartbeat()
{
   string response; int status;
   string body="{\"component\":\"MT5-Connector\",\"status\":\"online\",\"details\":\"Connector active; synced with bridge API\"}";
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

void PublishChartCandles()
{
   string raw="";
   int file=FileOpen("DIVSCAN_SYMBOLS.txt",FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(file==INVALID_HANDLE) return;
   raw=FileReadString(file);
   FileClose(file);

   string fields[];
   if(StringSplit(raw,'#',fields)<2) return;
   datetime scanTime=(datetime)StringToInteger(fields[0]);
   if(scanTime<=0 || TimeCurrent()-scanTime>60) return;

   string entries[];
   int count=StringSplit(fields[1],';',entries);
   if(count<=0) return;
   int index=g_chartCursor%count;
   g_chartCursor++;

   string pair[];
   if(StringSplit(entries[index],'|',pair)<2) return;
   string symbol=pair[0];
   string tf=pair[1];
   ENUM_TIMEFRAMES period=ChartTimeframeFromText(tf);
   if(symbol=="" || period==PERIOD_CURRENT) return;
   if(!SymbolSelect(symbol,true)) return;

   MqlRates rates[];
   ArraySetAsSeries(rates,false);
   int copied=CopyRates(symbol,period,0,MathMax(50,MathMin(1000,ChartBarsToSend)),rates);
   if(copied<2) return;

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
   if(validCount<2) return;

   int digits=(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS);
   double tickSize=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE);
   double tickValue=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickValue<=0) tickValue=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE);
   if(tickSize<=0) tickSize=SymbolInfoDouble(symbol,SYMBOL_POINT);
   double volumeMin=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN);
   double volumeMax=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MAX);
   double volumeStep=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP);
   string body=StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"digits\":%d,\"tick_size\":%.10f,\"tick_value\":%.10f,\"volume_min\":%.8f,\"volume_max\":%.8f,\"volume_step\":%.8f,\"candles\":%s}",
      JsonEscape(symbol),JsonEscape(tf),digits,tickSize,tickValue,volumeMin,volumeMax,volumeStep,candles);
   string response;
   int status;
   if(!HttpRequest("POST","/api/mt5/candles",body,response,status))
      Print("[CHART FEED] Upload failed for ",symbol," ",tf,". HTTP=",status," response=",response);
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
      // For closing a BUY position, closing deal is SELL; for closing SELL, deal is BUY.
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
         dealTicket,dealTicket,JsonEscape(sym),dir,price,vol,profit,closedAt
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

void PollCloseRequests()
{
   if(!EnableCloseRequests) return;
   string response; int status;
   if(!HttpRequest("GET","/api/paper/close-requests","",response,status)) return;
   if(StringFind(response,"\"close_requests\":[]")>=0) return;

   // Handle close requests sequentially for each open position matching symbol/ticket.
   int pos=0;
   while(true)
   {
      int objStart=StringFind(response,"{",pos);
      if(objStart<0) break;
      int objEnd=StringFind(response,"}",objStart);
      if(objEnd<0) break;
      string item=StringSubstr(response,objStart,objEnd-objStart+1);
      pos=objEnd+1;

      string reqId=JsonValue(item,"request_id");
      if(reqId=="") continue;
      string ticketStr=JsonValue(item,"ticket");
      string sym=JsonValue(item,"symbol");
      string dir=JsonValue(item,"direction");

      bool closedAny=false;
      string msg="No matching open position";
      for(int i=PositionsTotal()-1; i>=0; i--)
      {
         ulong posTicket=PositionGetTicket(i);
         if(posTicket==0 || !PositionSelectByTicket(posTicket)) continue;
         string pSym=PositionGetString(POSITION_SYMBOL);
         ENUM_POSITION_TYPE pType=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
         string pDir=(pType==POSITION_TYPE_BUY)?"BUY":"SELL";
         bool matchTicket=(ticketStr!="" && IntegerToString((long)posTicket)==ticketStr);
         bool matchSymDir=(sym!="" && pSym==sym && (dir=="" || pDir==dir));
         if(matchTicket || matchSymDir)
         {
            if(g_closeTrade.PositionClose(posTicket))
            {
               closedAny=true;
               msg="Closed position "+IntegerToString((long)posTicket);
            }
            else
            {
               msg="Close failed: "+IntegerToString(g_closeTrade.ResultRetcode());
            }
            break;
         }
      }

      string ackBody=StringFormat("{\"request_id\":\"%s\",\"position_id\":\"%s\",\"ok\":%s,\"message\":\"%s\"}",
         JsonEscape(reqId),JsonEscape(ticketStr),closedAny?"true":"false",JsonEscape(msg));
      string ackResp; int ackStatus;
      HttpRequest("POST","/api/paper/close-requests/ack",ackBody,ackResp,ackStatus);
   }
}

void PollSignal()
{
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
   string entry=JsonValue(signalJson,"entry");
   string sl=JsonValue(signalJson,"sl");
   string created=JsonValue(signalJson,"created_at");
   if(id=="" || symbol=="" || (direction!="BUY" && direction!="SELL") || entry=="" || sl=="")
   {
      Print("[SERVER] Invalid signal payload: ",response);
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
   Print("[PAPER SIGNAL] ",symbol," ",tf," ",direction,
         " entry=",DoubleToString(entryPrice,8)," SL=",DoubleToString(slPrice,8),
         " id=",id," created=",created);
   AckSignal(id,"SIMULATED","Signal received and validated; paper mode only");
}

int OnInit()
{
   if(StringFind(ServerBaseURL,"http://127.0.0.1")!=0 && StringFind(ServerBaseURL,"https://")!=0)
   {
      Print("Use localhost or HTTPS server URL.");
      return INIT_PARAMETERS_INCORRECT;
   }
   EventSetTimer(1);
   Print("ServerConnector v1.10 initialized. Add ",ServerBaseURL,
         " to MT5 WebRequest allowlist.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason){ EventKillTimer(); }

void OnTimer()
{
   if(TimeCurrent()-g_lastHeartbeat>=HeartbeatSeconds) SendHeartbeat();
   if(EnableChartCandleFeed && TimeCurrent()-g_lastCandlePost>=MathMax(2,ChartFeedSeconds))
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
      PollCloseRequests();
      if(EnableSignalPolling) PollSignal();
   }
}
