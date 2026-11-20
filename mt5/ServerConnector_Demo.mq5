//+------------------------------------------------------------------+
//| ServerConnector_Demo.mq5                                         |
//| Polls Python server and records PAPER signals only.              |
//| No OrderSend / CTrade calls are present in this EA.               |
//+------------------------------------------------------------------+
#property strict
#property version "1.00"

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

datetime g_lastHeartbeat = 0;
datetime g_lastPoll = 0;
string g_lastLegacySignature = "";
datetime g_lastScanPost = 0;
datetime g_lastCandlePost = 0;
int g_chartCursor = 0;
string g_queueCursorName = "DIVSERVER_QUEUE_CURSOR";

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
   string body="{\"component\":\"MT5-Connector\",\"status\":\"online\",\"details\":\"Demo connector active; no live trading\"}";
   if(HttpRequest("POST","/api/heartbeat",body,response,status))
      g_lastHeartbeat=TimeCurrent();
   else
      Print("[SERVER] Heartbeat failed. HTTP=",status," response=",response);
}

bool AckSignal(string id,string result,string message)
{
   string response; int status;
   string body="{\"signal_id\":\""+id+"\",\"status\":\""+result+"\",\"message\":\""+message+"\"}";
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
   string body=StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\",\"entry\":%.8f,\"sl\":%.8f,\"score\":%.2f,\"source\":\"%s\"}",symbol,timeframe,direction,entry,sl,score,source);
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
      // New queue rows include detection time; legacy rows fall back to pivot time.
      datetime detectedAt=(ArraySize(p)>=9)?(datetime)StringToInteger(p[8]):signalTime;
      if(symbol=="" || (direction!="BUY" && direction!="SELL") || signalTime<=0 || entry<=0 || sl<=0)
      { GlobalVariableSet(g_queueCursorName,(double)lineNo); continue; }

      // Check when the strong signal was detected, not the older chart pivot timestamp.
      // Legacy queue rows without detection time retain the previous age check.
      if(detectedAt<=0 || TimeCurrent()-detectedAt>MaxLegacySignalAgeSeconds)
      {
         GlobalVariableSet(g_queueCursorName,(double)lineNo);
         Print("[SERVER] Skipped expired queued signal: ",symbol," ",tf," ",direction);
         continue;
      }

      // The API source field is limited to 40 characters; keep the ID compact and unique per queue row.
      string source=StringFormat("div-%d-%d",(int)signalTime,(int)lineNo);
      string body=StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\",\"entry\":%.8f,\"sl\":%.8f,\"score\":%.2f,\"source\":\"%s\"}",symbol,tf,direction,entry,sl,score,source);
      string response; int status;
      if(!HttpRequest("POST","/api/signals",body,response,status))
      {
         // Skip malformed client payloads so one bad row cannot block later valid signals.
         // Authentication, network and server errors remain retryable.
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
         jsonItems+="\""+item+"\"";
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
         if(symbol=="" || (direction!="BUY" && direction!="SELL") || signalTime<=0) continue;
         if(sigType!="RT" && sigType!="HIST") sigType="RT";
         if(items!="") items+=",";
         items+=StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\",\"divergence\":\"%s\",\"signal_type\":\"%s\",\"signal_time\":%d,\"price1\":%.8f,\"price2\":%.8f,\"rsi1\":%.2f,\"rsi2\":%.2f}",
            symbol,tf,direction,divType,sigType,(int)signalTime,
            StringToDouble(p[6]),StringToDouble(p[7]),StringToDouble(p[8]),StringToDouble(p[9]));
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

// Sends OHLC history only. It does not create or execute trading signals.
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
   for(int i=0;i<copied;i++)
   {
      if(i>0) candles+=",";
      candles+="{\"time\":"+IntegerToString((int)rates[i].time)+
               ",\"open\":"+DoubleToString(rates[i].open,10)+
               ",\"high\":"+DoubleToString(rates[i].high,10)+
               ",\"low\":"+DoubleToString(rates[i].low,10)+
               ",\"close\":"+DoubleToString(rates[i].close,10)+
               ",\"volume\":"+IntegerToString((long)rates[i].tick_volume)+"}";
   }
   candles+="]";
   int digits=(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS);
   double tickSize=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE);
   double tickValue=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE);
   if(tickSize<=0) tickSize=SymbolInfoDouble(symbol,SYMBOL_POINT);
   string body=StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"digits\":%d,\"tick_size\":%.10f,\"tick_value\":%.10f,\"candles\":%s}",symbol,tf,digits,tickSize,tickValue,candles);
   string response;
   int status;
   if(!HttpRequest("POST","/api/mt5/candles",body,response,status))
      Print("[CHART FEED] Upload failed for ",symbol," ",tf,". HTTP=",status," response=",response);
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
   Print("ServerConnector DEMO initialized. Add ",ServerBaseURL,
         " to MT5 WebRequest allowlist. No trading functions enabled.");
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
   if(TimeCurrent()-g_lastPoll>=PollSeconds)
   {
      g_lastPoll=TimeCurrent();
      PublishScanStatus();
      PublishTodaySignals();
      ImportDivergenceQueue();
      ImportLegacyDivergenceSignal();
      if(EnableSignalPolling) PollSignal();
   }
}
