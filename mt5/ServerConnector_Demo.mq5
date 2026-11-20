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
input int MaxLegacySignalAgeSeconds = 300;

datetime g_lastHeartbeat = 0;
datetime g_lastPoll = 0;
string g_lastLegacySignature = "";
datetime g_lastScanPost = 0;
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
      if(symbol=="" || (direction!="BUY" && direction!="SELL") || signalTime<=0 || entry<=0 || sl<=0)
      { GlobalVariableSet(g_queueCursorName,(double)lineNo); continue; }
      string source=StringFormat("div-%s-%s-%s-%s-%d",symbol,tf,direction,divType,(int)signalTime);
      string body=StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\",\"entry\":%.8f,\"sl\":%.8f,\"score\":%.2f,\"source\":\"%s\"}",symbol,tf,direction,entry,sl,score,source);
      string response; int status;
      if(!HttpRequest("POST","/api/signals",body,response,status))
      {
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
   if(TimeCurrent()-g_lastPoll>=PollSeconds)
   {
      g_lastPoll=TimeCurrent();
      PublishScanStatus();
      ImportDivergenceQueue();
      ImportLegacyDivergenceSignal();
      PollSignal();
   }
}
