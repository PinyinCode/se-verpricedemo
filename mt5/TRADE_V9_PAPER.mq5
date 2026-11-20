//+------------------------------------------------------------------+
//| TRADE_V9_PAPER.mq5                                               |
//| Independent paper simulator for Forex AI Server signals.         |
//| No CTrade, OrderSend, PositionOpen, or broker order execution.    |
//+------------------------------------------------------------------+
#property strict
#property version "1.00"

input string ServerBaseURL = "https://investforex.onrender.com";
input string ApiKey = "SET_A_LONG_RANDOM_API_KEY";
input string ConsumerId = "trade-v9-paper";
input int PollSeconds = 3;
input int HeartbeatSeconds = 15;
input double VirtualRiskMoney = 25.0;
input double RewardRiskRatio = 2.0;
input int MaxOpenPaperTrades = 3;
input int MaxPerSymbol = 1;
input int CooldownMinutes = 15;
input int MaxSignalAgeSeconds = 300;

struct PaperTrade
{
   string id;
   string symbol;
   string timeframe;
   string direction;
   double entry;
   double sl;
   double tp;
   double volume;
   datetime opened;
};
PaperTrade trades[];
string lastSource[];
datetime lastTradeTime[];
uint lastPollMs=0;
uint lastHeartbeatMs=0;

string JsonValue(string json,string key)
{
   string token="\""+key+"\"";
   int p=StringFind(json,token);
   if(p<0) return "";
   p=StringFind(json,":",p+StringLen(token));
   if(p<0) return "";
   p++;
   while(p<StringLen(json) && (StringGetCharacter(json,p)==' ' ||
         StringGetCharacter(json,p)=='\t' || StringGetCharacter(json,p)=='\r' ||
         StringGetCharacter(json,p)=='\n')) p++;
   if(p>=StringLen(json)) return "";
   if(StringGetCharacter(json,p)=='"')
   {
      int e=StringFind(json,"\"",p+1);
      if(e<0) return "";
      return StringSubstr(json,p+1,e-p-1);
   }
   int e=p;
   while(e<StringLen(json) && StringGetCharacter(json,e)!=',' &&
         StringGetCharacter(json,e)!='}' && StringGetCharacter(json,e)!='\n') e++;
   string v=StringSubstr(json,p,e-p);
   StringTrimLeft(v); StringTrimRight(v);
   return v;
}

string JsonObjectValue(string json,string key)
{
   string token="\""+key+"\"";
   int p=StringFind(json,token);
   if(p<0) return "";
   p=StringFind(json,":",p+StringLen(token));
   if(p<0) return "";
   p++;
   while(p<StringLen(json) && StringGetCharacter(json,p)<=32) p++;
   if(p>=StringLen(json) || StringGetCharacter(json,p)!='{') return "";
   int depth=0; bool quoted=false;
   for(int i=p;i<StringLen(json);i++)
   {
      ushort c=StringGetCharacter(json,i);
      if(c=='"' && (i==0 || StringGetCharacter(json,i-1)!='\\')) quoted=!quoted;
      if(quoted) continue;
      if(c=='{') depth++;
      if(c=='}' && --depth==0) return StringSubstr(json,p,i-p+1);
   }
   return "";
}

bool Request(string method,string path,string body,string &response,int &status)
{
   char data[],result[];
   if(body!="")
   {
      StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
      int n=ArraySize(data); if(n>0 && data[n-1]==0) ArrayResize(data,n-1);
   }
   else ArrayResize(data,0);
   string headers="X-API-Key: "+ApiKey+"\r\nContent-Type: application/json\r\n";
   string responseHeaders;
   status=WebRequest(method,ServerBaseURL+path,headers,5000,data,result,responseHeaders);
   if(status==-1) { Print("[PAPER V9] WebRequest error=",GetLastError()); response=""; return false; }
   response=CharArrayToString(result,0,-1,CP_UTF8);
   return status>=200 && status<300;
}

void SendPaperHeartbeat()
{
   string body=StringFormat("{\"component\":\"TRADE-V9-PAPER\",\"status\":\"online\",\"details\":\"Paper consumer active; virtual_positions=%d; no broker orders\"}",ArraySize(trades));
   string response; int status;
   if(Request("POST","/api/heartbeat",body,response,status))
      lastHeartbeatMs=GetTickCount();
   else
      Print("[PAPER V9] Heartbeat failed HTTP=",status," response=",response);
}

bool Ack(string id,string state,string message)
{
   string body="{\"signal_id\":\""+id+"\",\"status\":\""+state+"\",\"message\":\""+message+"\"}";
   string response; int status;
   return Request("POST","/api/chart/ack",body,response,status);
}

int CountSymbol(string symbol)
{
   int n=0;
   for(int i=0;i<ArraySize(trades);i++) if(trades[i].symbol==symbol) n++;
   return n;
}

double NormalizeVolume(string symbol,double raw)
{
   double vmin=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN);
   double vmax=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP);
   if(step<=0) return 0;
   double v=MathFloor(raw/step)*step;
   v=MathMax(vmin,MathMin(vmax,v));
   return NormalizeDouble(v,2);
}

double CalcVirtualVolume(string symbol,double entry,double sl)
{
   double tickSize=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE);
   double tickValue=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE);
   double distance=MathAbs(entry-sl);
   if(tickSize<=0 || tickValue<=0 || distance<=0) return 0;
   double lossPerLot=(distance/tickSize)*tickValue;
   if(lossPerLot<=0) return 0;
   return NormalizeVolume(symbol,VirtualRiskMoney/lossPerLot);
}

void AddPaperTrade(string id,string symbol,string tf,string direction,double entry,double sl)
{
   if(!SymbolSelect(symbol,true)) { Ack(id,"REJECTED","Symbol unavailable in Market Watch"); return; }
   double bid=SymbolInfoDouble(symbol,SYMBOL_BID);
   double ask=SymbolInfoDouble(symbol,SYMBOL_ASK);
   if(bid<=0 || ask<=0) { Ack(id,"REJECTED","No live quote; paper entry not recorded"); return; }
   double risk=MathAbs(entry-sl);
   double tp=(direction=="BUY") ? entry+risk*RewardRiskRatio : entry-risk*RewardRiskRatio;
   double volume=CalcVirtualVolume(symbol,entry,sl);
   if(volume<=0) { Ack(id,"REJECTED","Cannot calculate virtual volume from symbol tick data"); return; }

   int n=ArraySize(trades); ArrayResize(trades,n+1);
   trades[n].id=id; trades[n].symbol=symbol; trades[n].timeframe=tf;
   trades[n].direction=direction; trades[n].entry=entry; trades[n].sl=sl;
   trades[n].tp=tp; trades[n].volume=volume; trades[n].opened=TimeCurrent();

   Print("[PAPER OPEN] ",symbol," ",tf," ",direction,
         " entry=",DoubleToString(entry,(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS)),
         " SL=",DoubleToString(sl,(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS)),
         " TP=",DoubleToString(tp,(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS)),
         " virtual lots=",DoubleToString(volume,2)," risk=$",DoubleToString(VirtualRiskMoney,2));
   Ack(id,"SIMULATED","Paper position opened; no broker order sent");
}

void ManagePaperTrades()
{
   for(int i=ArraySize(trades)-1;i>=0;i--)
   {
      string s=trades[i].symbol;
      double bid=SymbolInfoDouble(s,SYMBOL_BID), ask=SymbolInfoDouble(s,SYMBOL_ASK);
      if(bid<=0 || ask<=0) continue;
      bool stop=false,target=false;
      if(trades[i].direction=="BUY") { stop=(bid<=trades[i].sl); target=(bid>=trades[i].tp); }
      else { stop=(ask>=trades[i].sl); target=(ask<=trades[i].tp); }
      if(!stop && !target) continue;
      string outcome=target ? "PAPER_TP" : "PAPER_SL";
      Print("[PAPER CLOSE] ",outcome," ",s," signal=",trades[i].id,
            " virtual result=",target ? "+" : "-",DoubleToString(VirtualRiskMoney*(target?RewardRiskRatio:1.0),2));
      int last=ArraySize(trades)-1;
      for(int j=i;j<last;j++) trades[j]=trades[j+1];
      ArrayResize(trades,last);
   }
}

void PollServer()
{
   string response; int status;
   string path="/api/chart/next";
   if(!Request("GET",path,"",response,status)) { Print("[PAPER V9] Poll failed HTTP=",status); return; }
   if(StringFind(response,"\"signal\":null")>=0) return;
   string obj=JsonObjectValue(response,"signal");
   if(obj=="") return;
   string id=JsonValue(obj,"id"), symbol=JsonValue(obj,"symbol");
   string tf=JsonValue(obj,"timeframe"), direction=JsonValue(obj,"direction");
   double entry=StringToDouble(JsonValue(obj,"entry")), sl=StringToDouble(JsonValue(obj,"sl"));
   if(id=="" || symbol=="" || entry<=0 || sl<=0 ||
      (direction!="BUY" && direction!="SELL"))
   { if(id!="") Ack(id,"REJECTED","Malformed signal"); return; }
   if((direction=="BUY" && sl>=entry) || (direction=="SELL" && sl<=entry))
   { Ack(id,"REJECTED","Invalid entry/SL geometry"); return; }
   if(ArraySize(trades)>=MaxOpenPaperTrades) { Ack(id,"REJECTED","Paper max positions reached"); return; }
   if(CountSymbol(symbol)>=MaxPerSymbol) { Ack(id,"REJECTED","Paper per-symbol limit reached"); return; }
   for(int i=0;i<ArraySize(lastTradeTime);i++)
      if(lastSource[i]==symbol && TimeCurrent()-lastTradeTime[i]<CooldownMinutes*60)
      { Ack(id,"REJECTED","Paper cooldown active"); return; }
   AddPaperTrade(id,symbol,tf,direction,entry,sl);
   int n=ArraySize(lastSource);
   if(n==0) { ArrayResize(lastSource,1); ArrayResize(lastTradeTime,1); }
   else { ArrayResize(lastSource,n+1); ArrayResize(lastTradeTime,n+1); }
   lastSource[n]=symbol; lastTradeTime[n]=TimeCurrent();
}

int OnInit()
{
   if(StringFind(ServerBaseURL,"https://")!=0)
   { Print("[PAPER V9] HTTPS server URL required."); return INIT_PARAMETERS_INCORRECT; }
   if(ApiKey=="" || ApiKey=="SET_A_LONG_RANDOM_API_KEY")
   { Print("[PAPER V9] Set ApiKey privately in EA inputs."); return INIT_PARAMETERS_INCORRECT; }
   EventSetTimer(1);
   Print("[PAPER V9] Started. Simulation only; broker order functions are not used.");
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); }
void OnTick() {}
void OnTimer()
{
   ManagePaperTrades();
   uint nowMs=GetTickCount();
   if(lastHeartbeatMs==0 || (uint)(nowMs-lastHeartbeatMs)>=(uint)(HeartbeatSeconds*1000))
      SendPaperHeartbeat();
   if(lastPollMs==0 || (uint)(nowMs-lastPollMs)>=(uint)(PollSeconds*1000))
   {
      lastPollMs=nowMs;
      PollServer();
   }
}
