//+------------------------------------------------------------------+
//| ServerConnector_Demo.mq5                                         |
//| Polls Python server and records PAPER signals only.              |
//| No OrderSend / CTrade calls are present in this EA.               |
//+------------------------------------------------------------------+
#property strict
#property version "1.00"

input string ServerBaseURL = "http://127.0.0.1:8000";
input string ApiKey = "SET_A_LONG_RANDOM_API_KEY";
input string ConsumerId = "mt5-demo-vps";
input int PollSeconds = 3;
input int HeartbeatSeconds = 15;
input bool EnableLegacyDivergenceImport = true;

datetime g_lastHeartbeat = 0;
datetime g_lastPoll = 0;
string g_lastSignalId = "";
string g_lastLegacySignature = "";

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
   HttpRequest("POST","/api/heartbeat",body,response,status);
   g_lastHeartbeat=TimeCurrent();
}

void AckSignal(string id,string result,string message)
{
   string response; int status;
   string body="{\"signal_id\":\""+id+"\",\"status\":\""+result+"\",\"message\":\""+message+"\"}";
   if(!HttpRequest("POST","/api/mt5/ack",body,response,status))
      Print("[SERVER] Ack failed. HTTP=",status," response=",response);
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

   string direction=(type>0)?"BUY":"SELL";
   string body=StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\",\"entry\":%.8f,\"sl\":%.8f,\"score\":%.2f,\"source\":\"legacy-rsi-divergence\"}",symbol,timeframe,direction,entry,sl,score);
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
   string id=JsonValue(response,"id");
   string symbol=JsonValue(response,"symbol");
   string tf=JsonValue(response,"timeframe");
   string direction=JsonValue(response,"direction");
   string entry=JsonValue(response,"entry");
   string sl=JsonValue(response,"sl");
   string created=JsonValue(response,"created_at");
   if(id=="" || symbol=="" || (direction!="BUY" && direction!="SELL") || entry=="" || sl=="")
   {
      Print("[SERVER] Invalid signal payload: ",response);
      return;
   }
   if(id==g_lastSignalId) return;
   double entryPrice=StringToDouble(entry), slPrice=StringToDouble(sl);
   bool valid=(entryPrice>0 && slPrice>0 &&
      ((direction=="BUY" && slPrice<entryPrice) || (direction=="SELL" && slPrice>entryPrice)));
   if(!valid)
   {
      AckSignal(id,"REJECTED","Invalid entry/SL geometry");
      return;
   }
   g_lastSignalId=id;
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
      ImportLegacyDivergenceSignal();
      PollSignal();
   }
}
