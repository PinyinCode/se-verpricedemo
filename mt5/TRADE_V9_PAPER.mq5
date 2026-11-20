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
// Chart requests supply their own cash risk and calculated lot.
input double RewardRiskRatio = 2.0;
input int MaxOpenPaperTrades = 3;
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
   double riskMoney;
   datetime opened;
};
PaperTrade trades[];
uint lastPollMs=0;
uint lastPositionsSyncMs=0;
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
   Print("[PAPER ACK] signal=",id," status=",state," message=",message);
   string body="{\"signal_id\":\""+id+"\",\"status\":\""+state+"\",\"message\":\""+message+"\"}";
   string response; int status;
   bool ok=Request("POST","/api/chart/ack",body,response,status);
   if(!ok) Print("[PAPER ACK ERROR] signal=",id," HTTP=",status," response=",response);
   return ok;
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
   if(step<=0 || raw<vmin || raw>vmax) return 0;
   double v=MathFloor((raw+1e-10)/step)*step;
   if(v<vmin) return 0;
   return NormalizeDouble(v,8);
}

bool AddPaperTrade(string id,string symbol,string tf,string direction,double entry,double sl,double tpRequested,double riskMoney,double requestedLot)
{
   if(!SymbolSelect(symbol,true)) { Ack(id,"REJECTED","Symbol unavailable in Market Watch"); return false; }
   double bid=SymbolInfoDouble(symbol,SYMBOL_BID);
   double ask=SymbolInfoDouble(symbol,SYMBOL_ASK);
   if(bid<=0 || ask<=0) { Ack(id,"REJECTED","No live quote; paper entry not recorded"); return false; }
   double distance=MathAbs(entry-sl);
   double tickSize=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE);
   double tickValue=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickValue<=0) tickValue=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE);
   if(distance<=0 || tickSize<=0 || tickValue<=0 || riskMoney<=0)
   { Ack(id,"REJECTED","Invalid risk or MT5 tick contract data"); return false; }
   double volume=NormalizeVolume(symbol,requestedLot);
   if(volume<=0)
   { Ack(id,"REJECTED","Requested lot is outside MT5 min/max/step"); return false; }
   double actualRisk=(distance/tickSize)*tickValue*volume;
   if(actualRisk>riskMoney+MathMax(0.01,riskMoney*0.005))
   { Ack(id,"REJECTED","Calculated lot exceeds requested cash risk using MT5 contract data"); return false; }
   double tp=tpRequested>0 ? tpRequested : ((direction=="BUY") ? entry+distance*RewardRiskRatio : entry-distance*RewardRiskRatio);
   if((direction=="BUY" && tp<=entry) || (direction=="SELL" && tp>=entry))
   { Ack(id,"REJECTED","Invalid TP geometry"); return false; }

   int n=ArraySize(trades); ArrayResize(trades,n+1);
   trades[n].id=id; trades[n].symbol=symbol; trades[n].timeframe=tf;
   trades[n].direction=direction; trades[n].entry=entry; trades[n].sl=sl;
   trades[n].tp=tp; trades[n].volume=volume; trades[n].riskMoney=actualRisk; trades[n].opened=TimeCurrent();

   Print("[PAPER OPEN] ",symbol," ",tf," ",direction,
         " entry=",DoubleToString(entry,(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS)),
         " SL=",DoubleToString(sl,(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS)),
         " TP=",DoubleToString(tp,(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS)),
         " virtual lots=",DoubleToString(volume,4)," risk=$",DoubleToString(actualRisk,2),
         " requested risk=$",DoubleToString(riskMoney,2));
   Ack(id,"SIMULATED","Paper position opened with chart-calculated lot; MT5 risk revalidated; no broker order sent");
   return true;
}

void SendPaperClosed(int i,string outcome,double realized)
{
   string body=StringFormat("{\"id\":\"%s\",\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\",\"entry\":%.10f,\"sl\":%.10f,\"tp\":%.10f,\"volume\":%.8f,\"risk\":%.2f,\"profit\":%.2f,\"opened_at\":%.0f,\"outcome\":\"%s\",\"closed_at\":%.0f}",
      trades[i].id,trades[i].symbol,trades[i].timeframe,trades[i].direction,
      trades[i].entry,trades[i].sl,trades[i].tp,trades[i].volume,trades[i].riskMoney,
      realized,(double)trades[i].opened,outcome,(double)TimeCurrent());
   string response; int status;
   if(!Request("POST","/api/paper/closed",body,response,status))
      Print("[PAPER V9] Closed history sync failed HTTP=",status," response=",response);
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
      double realized=0;
      ENUM_ORDER_TYPE orderType=(trades[i].direction=="BUY") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      double closePrice=(trades[i].direction=="BUY") ? bid : ask;
      OrderCalcProfit(orderType,s,trades[i].volume,trades[i].entry,closePrice,realized);
      string outcome=target ? "PAPER_TP" : "PAPER_SL";
      Print("[PAPER CLOSE] ",outcome," ",s," signal=",trades[i].id,
            " volume=",DoubleToString(trades[i].volume,4)," realized P/L=",DoubleToString(realized,2));
      SendPaperClosed(i,outcome,realized);
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
   Print("[PAPER RECEIVE] signal=",id," ",symbol," ",tf," ",direction,
         " entry=",DoubleToString(entry,8)," SL=",DoubleToString(sl,8));
   double tp=StringToDouble(JsonValue(obj,"tp"));
   double riskMoney=StringToDouble(JsonValue(obj,"risk_money"));
   double requestedLot=StringToDouble(JsonValue(obj,"requested_lot"));
   if(id=="" || symbol=="" || entry<=0 || sl<=0 || riskMoney<=0 || requestedLot<=0 ||
      (direction!="BUY" && direction!="SELL"))
   { Print("[PAPER REJECT] Malformed signal id=",id); if(id!="") Ack(id,"REJECTED","Malformed signal"); return; }
   if((direction=="BUY" && sl>=entry) || (direction=="SELL" && sl<=entry))
   { Print("[PAPER REJECT] Invalid entry/SL geometry signal=",id); Ack(id,"REJECTED","Invalid entry/SL geometry"); return; }
   if(tp>0 && ((direction=="BUY" && tp<=entry) || (direction=="SELL" && tp>=entry)))
   { Print("[PAPER REJECT] Invalid TP geometry signal=",id); Ack(id,"REJECTED","Invalid TP geometry"); return; }
   if(ArraySize(trades)>=MaxOpenPaperTrades)
   { Print("[PAPER REJECT] Max paper positions reached signal=",id," open=",ArraySize(trades)); Ack(id,"REJECTED","Paper max positions reached"); return; }
   if(!AddPaperTrade(id,symbol,tf,direction,entry,sl,tp,riskMoney,requestedLot)) return;
}

void SendPaperPositions()
{
   string body="{\"positions\":[";
   for(int i=0;i<ArraySize(trades);i++)
   {
      string symbol=trades[i].symbol;
      double bid=SymbolInfoDouble(symbol,SYMBOL_BID), ask=SymbolInfoDouble(symbol,SYMBOL_ASK);
      double profit=0;
      if(bid>0 && ask>0)
      {
         ENUM_ORDER_TYPE ot=(trades[i].direction=="BUY") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
         double closePrice=(trades[i].direction=="BUY") ? bid : ask;
         OrderCalcProfit(ot,symbol,trades[i].volume,trades[i].entry,closePrice,profit);
      }
      if(i>0) body+=",";
      body+=StringFormat("{\"id\":\"%s\",\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\",\"entry\":%.10f,\"sl\":%.10f,\"tp\":%.10f,\"volume\":%.8f,\"risk\":%.2f,\"profit\":%.2f,\"opened_at\":%.0f}",
         trades[i].id,symbol,trades[i].timeframe,trades[i].direction,trades[i].entry,
         trades[i].sl,trades[i].tp,trades[i].volume,trades[i].riskMoney,profit,(double)trades[i].opened);
   }
   body+="]}";
   string response; int status;
   if(!Request("POST","/api/paper/positions",body,response,status))
      Print("[PAPER V9] Position sync failed HTTP=",status," response=",response);
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
   if(lastPositionsSyncMs==0 || (uint)(nowMs-lastPositionsSyncMs)>=3000)
   {
      lastPositionsSyncMs=nowMs;
      SendPaperPositions();
   }
}
