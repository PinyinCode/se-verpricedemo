//+------------------------------------------------------------------+
//| TRADE_V9_PAPER.mq5                                               |
//| Forex AI Server consumer.                                        |
//| UsePaperSimulation=true  -> virtual paper trades (no broker order)|
//| UsePaperSimulation=false -> REAL broker orders via CTrade        |
//+------------------------------------------------------------------+
#property strict
#property version "2.00"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

//--- Inputs ---------------------------------------------------------
input string ServerBaseURL       = "https://investforex.onrender.com";
input string ApiKey              = "SET_A_LONG_RANDOM_API_KEY";
input string ConsumerId          = "trade-v9-paper";
input int    PollSeconds         = 3;
input int    HeartbeatSeconds    = 15;
input double RewardRiskRatio     = 2.0;
input int    MaxOpenPaperTrades  = 3;
input int    MaxSignalAgeSeconds = 300;

input bool   UsePaperSimulation  = true;    // true=paper, false=đặt lệnh thật
input long   MagicNumber         = 20240915;
input int    SlippagePoints      = 20;
input string OrderComment        = "TRADE-V9";

//--- Globals --------------------------------------------------------
CTrade        trade;
CPositionInfo posInfo;

struct PaperTrade
{
   string   id;
   string   symbol;
   string   timeframe;
   string   direction;
   double   entry;
   double   sl;
   double   tp;
   double   volume;
   double   riskMoney;
   datetime opened;
};

PaperTrade trades[];

// ánh xạ ticket -> thông tin gốc của signal (real mode)
string   realTicketIds[];
string   realSignalIds[];
string   realSymbols[];
string   realTimeframes[];
string   realDirections[];
double   realRisks[];
datetime realOpenTimes[];

uint lastPollMs          = 0;
uint lastPositionsSyncMs = 0;
uint lastHeartbeatMs     = 0;

//--- JSON helpers ---------------------------------------------------
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

//--- HTTP -----------------------------------------------------------
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
   if(status==-1) { Print("[V9] WebRequest error=",GetLastError()); response=""; return false; }
   response=CharArrayToString(result,0,-1,CP_UTF8);
   return status>=200 && status<300;
}

//--- Heartbeat ------------------------------------------------------
void SendHeartbeat()
{
   int    openCount = UsePaperSimulation ? ArraySize(trades) : CountRealPositions();
   string mode      = UsePaperSimulation ? "PAPER" : "REAL";
   string body=StringFormat(
      "{\"component\":\"TRADE-V9-%s\",\"status\":\"online\",\"details\":\"%s consumer active; positions=%d\"}",
      mode,mode,openCount);
   string response; int status;
   if(Request("POST","/api/heartbeat",body,response,status))
      lastHeartbeatMs=GetTickCount();
   else
      Print("[V9] Heartbeat failed HTTP=",status," response=",response);
}

//--- Ack ------------------------------------------------------------
bool Ack(string id,string state,string message)
{
   Print("[V9 ACK] signal=",id," status=",state," message=",message);
   string body="{\"signal_id\":\""+id+"\",\"status\":\""+state+"\",\"message\":\""+message+"\"}";
   string response; int status;
   bool ok=Request("POST","/api/chart/ack",body,response,status);
   if(!ok) Print("[V9 ACK ERROR] signal=",id," HTTP=",status," response=",response);
   return ok;
}

//--- Volume / filling ----------------------------------------------
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

ENUM_ORDER_TYPE_FILLING PickFilling(string symbol)
{
   long modes=SymbolInfoInteger(symbol,SYMBOL_FILLING_MODE);
   if((modes & SYMBOL_FILLING_FOK)!=0) return ORDER_FILLING_FOK;
   if((modes & SYMBOL_FILLING_IOC)!=0) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

//--- Đếm position -----------------------------------------------
int CountRealPositions()
{
   int n=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
      if(posInfo.SelectByIndex(i) && posInfo.Magic()==MagicNumber) n++;
   return n;
}

//--- Ánh xạ ticket <-> signal -------------------------------------
int FindTicketIndex(string ticket)
{
   for(int i=0;i<ArraySize(realTicketIds);i++)
      if(realTicketIds[i]==ticket) return i;
   return -1;
}

void RememberRealPosition(string ticket,string signalId,string symbol,string tf,
                          string direction,double riskMoney)
{
   int n=ArraySize(realTicketIds);
   ArrayResize(realTicketIds,n+1);   ArrayResize(realSignalIds,n+1);
   ArrayResize(realSymbols,n+1);     ArrayResize(realTimeframes,n+1);
   ArrayResize(realDirections,n+1);  ArrayResize(realRisks,n+1);
   ArrayResize(realOpenTimes,n+1);
   realTicketIds[n]  = ticket;
   realSignalIds[n]  = signalId;
   realSymbols[n]    = symbol;
   realTimeframes[n] = tf;
   realDirections[n] = direction;
   realRisks[n]      = riskMoney;
   realOpenTimes[n]  = TimeCurrent();
}

void ForgetRealPosition(string ticket)
{
   int idx=FindTicketIndex(ticket);
   if(idx<0) return;
   int last=ArraySize(realTicketIds)-1;
   for(int j=idx;j<last;j++)
   {
      realTicketIds[j]  = realTicketIds[j+1];
      realSignalIds[j]  = realSignalIds[j+1];
      realSymbols[j]    = realSymbols[j+1];
      realTimeframes[j] = realTimeframes[j+1];
      realDirections[j] = realDirections[j+1];
      realRisks[j]      = realRisks[j+1];
      realOpenTimes[j]  = realOpenTimes[j+1];
   }
   ArrayResize(realTicketIds,last);
   ArrayResize(realSignalIds,last);
   ArrayResize(realSymbols,last);
   ArrayResize(realTimeframes,last);
   ArrayResize(realDirections,last);
   ArrayResize(realRisks,last);
   ArrayResize(realOpenTimes,last);
}

void PruneClosedRealPositions()
{
   if(UsePaperSimulation) return;
   for(int i=ArraySize(realTicketIds)-1;i>=0;i--)
   {
      if(!PositionSelectByTicket(StringToInteger(realTicketIds[i])))
         ForgetRealPosition(realTicketIds[i]);
   }
}

//--- Mở lệnh (paper hoặc real) ------------------------------------
bool OpenTrade(string id,string symbol,string tf,string direction,
               double entry,double sl,double tpRequested,
               double riskMoney,double requestedLot)
{
   if(!SymbolSelect(symbol,true))
   { Ack(id,"REJECTED","Symbol unavailable in Market Watch"); return false; }

   double bid=SymbolInfoDouble(symbol,SYMBOL_BID);
   double ask=SymbolInfoDouble(symbol,SYMBOL_ASK);
   if(bid<=0 || ask<=0) { Ack(id,"REJECTED","No live quote"); return false; }

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
   { Ack(id,"REJECTED","Calculated lot exceeds requested cash risk"); return false; }

   double tp=tpRequested>0 ? tpRequested
                           : ((direction=="BUY") ? entry+distance*RewardRiskRatio
                                                 : entry-distance*RewardRiskRatio);
   if((direction=="BUY" && tp<=entry) || (direction=="SELL" && tp>=entry))
   { Ack(id,"REJECTED","Invalid TP geometry"); return false; }

   int digits=(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS);
   sl=NormalizeDouble(sl,digits);
   tp=NormalizeDouble(tp,digits);

   // ==================== PAPER ====================
   if(UsePaperSimulation)
   {
      int n=ArraySize(trades); ArrayResize(trades,n+1);
      trades[n].id=id; trades[n].symbol=symbol; trades[n].timeframe=tf;
      trades[n].direction=direction; trades[n].entry=entry; trades[n].sl=sl;
      trades[n].tp=tp; trades[n].volume=volume;
      trades[n].riskMoney=actualRisk; trades[n].opened=TimeCurrent();

      Print("[PAPER OPEN] ",symbol," ",tf," ",direction,
            " entry=",DoubleToString(entry,digits),
            " SL=",DoubleToString(sl,digits),
            " TP=",DoubleToString(tp,digits),
            " lots=",DoubleToString(volume,4),
            " risk=$",DoubleToString(actualRisk,2));
      Ack(id,"SIMULATED","Paper position opened; no broker order sent");
      return true;
   }

   // ==================== REAL ====================
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFilling(PickFilling(symbol));
   trade.SetAsyncMode(false);

   bool ok = (direction=="BUY")
             ? trade.Buy (volume,symbol,0.0,sl,tp,OrderComment)
             : trade.Sell(volume,symbol,0.0,sl,tp,OrderComment);

   if(!ok || trade.ResultRetcode()!=TRADE_RETCODE_DONE)
   {
      string err=StringFormat("OrderSend failed retcode=%d (%s) [%s]",
         trade.ResultRetcode(),trade.ResultRetcodeDescription(),trade.ResultComment());
      Print("[REAL OPEN FAIL] ",err," signal=",id);
      Ack(id,"REJECTED",err);
      return false;
   }

   Print("[REAL OPEN] signal=",id," ",symbol," ",tf," ",direction,
         " lots=",DoubleToString(volume,4),
         " SL=",DoubleToString(sl,digits),
         " TP=",DoubleToString(tp,digits),
         " order=",trade.ResultOrder(),
         " risk=$",DoubleToString(actualRisk,2));

   RememberRealPosition(
      IntegerToString(trade.ResultOrder()),
      id, symbol, tf, direction, actualRisk);

   Ack(id,"SIMULATED",
       StringFormat("Real broker order placed lot=%.4f ticket=%I64d (not in server-side paper DB)",
                    volume,trade.ResultOrder()));
   return true;
}

//--- Đóng paper (chỉ paper mode) ----------------------------------
void SendPaperClosed(int i,string outcome,double realized)
{
   string body=StringFormat(
      "{\"id\":\"%s\",\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\","
      "\"entry\":%.10f,\"sl\":%.10f,\"tp\":%.10f,\"volume\":%.8f,"
      "\"risk\":%.2f,\"profit\":%.2f,\"opened_at\":%.0f,"
      "\"outcome\":\"%s\",\"closed_at\":%.0f}",
      trades[i].id,trades[i].symbol,trades[i].timeframe,trades[i].direction,
      trades[i].entry,trades[i].sl,trades[i].tp,trades[i].volume,trades[i].riskMoney,
      realized,(double)trades[i].opened,outcome,(double)TimeCurrent());
   string response; int status;
   if(!Request("POST","/api/paper/closed",body,response,status))
      Print("[V9] Closed history sync failed HTTP=",status," response=",response);
}

void ManagePaperTrades()
{
   if(!UsePaperSimulation) return;

   for(int i=ArraySize(trades)-1;i>=0;i--)
   {
      string s=trades[i].symbol;
      double bid=SymbolInfoDouble(s,SYMBOL_BID), ask=SymbolInfoDouble(s,SYMBOL_ASK);
      if(bid<=0 || ask<=0) continue;
      bool stop=false,target=false;
      if(trades[i].direction=="BUY") { stop=(bid<=trades[i].sl); target=(bid>=trades[i].tp); }
      else                            { stop=(ask>=trades[i].sl); target=(ask<=trades[i].tp); }
      if(!stop && !target) continue;

      double realized=0;
      ENUM_ORDER_TYPE orderType=(trades[i].direction=="BUY") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      double closePrice=(trades[i].direction=="BUY") ? bid : ask;
      OrderCalcProfit(orderType,s,trades[i].volume,trades[i].entry,closePrice,realized);

      string outcome=target ? "PAPER_TP" : "PAPER_SL";
      Print("[PAPER CLOSE] ",outcome," ",s," signal=",trades[i].id,
            " volume=",DoubleToString(trades[i].volume,4),
            " realized P/L=",DoubleToString(realized,2));
      SendPaperClosed(i,outcome,realized);

      int last=ArraySize(trades)-1;
      for(int j=i;j<last;j++) trades[j]=trades[j+1];
      ArrayResize(trades,last);
   }
}

//--- Poll server ---------------------------------------------------
void PollServer()
{
   string response; int status;
   if(!Request("GET","/api/chart/next","",response,status))
   { Print("[V9] Poll failed HTTP=",status); return; }
   if(StringFind(response,"\"signal\":null")>=0) return;

   string obj=JsonObjectValue(response,"signal");
   if(obj=="") return;

   string id        = JsonValue(obj,"id");
   string symbol    = JsonValue(obj,"symbol");
   string tf        = JsonValue(obj,"timeframe");
   string direction = JsonValue(obj,"direction");
   double entry     = StringToDouble(JsonValue(obj,"entry"));
   double sl        = StringToDouble(JsonValue(obj,"sl"));
   double tp        = StringToDouble(JsonValue(obj,"tp"));
   double riskMoney = StringToDouble(JsonValue(obj,"risk_money"));
   double reqLot    = StringToDouble(JsonValue(obj,"requested_lot"));

   Print("[V9 RECEIVE] signal=",id," ",symbol," ",tf," ",direction,
         " entry=",DoubleToString(entry,8)," SL=",DoubleToString(sl,8),
         " lot=",DoubleToString(reqLot,4)," risk=$",DoubleToString(riskMoney,2));

   if(id=="" || symbol=="" || entry<=0 || sl<=0 || riskMoney<=0 || reqLot<=0 ||
      (direction!="BUY" && direction!="SELL"))
   { Print("[V9 REJECT] Malformed signal id=",id);
     if(id!="") Ack(id,"REJECTED","Malformed signal"); return; }

   if((direction=="BUY" && sl>=entry) || (direction=="SELL" && sl<=entry))
   { Print("[V9 REJECT] Invalid entry/SL geometry signal=",id);
     Ack(id,"REJECTED","Invalid entry/SL geometry"); return; }

   if(tp>0 && ((direction=="BUY" && tp<=entry) || (direction=="SELL" && tp>=entry)))
   { Print("[V9 REJECT] Invalid TP geometry signal=",id);
     Ack(id,"REJECTED","Invalid TP geometry"); return; }

   int openCount = UsePaperSimulation ? ArraySize(trades) : CountRealPositions();
   if(openCount>=MaxOpenPaperTrades)
   { Print("[V9 REJECT] Max positions reached signal=",id," open=",openCount);
     Ack(id,"REJECTED","Max positions reached"); return; }

   OpenTrade(id,symbol,tf,direction,entry,sl,tp,riskMoney,reqLot);
}

//--- Sync positions ------------------------------------------------
void SendPaperPositions()
{
   string body="{\"positions\":[";

   if(UsePaperSimulation)
   {
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
         body+=StringFormat(
            "{\"id\":\"%s\",\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\","
            "\"entry\":%.10f,\"sl\":%.10f,\"tp\":%.10f,\"volume\":%.8f,"
            "\"risk\":%.2f,\"profit\":%.2f,\"opened_at\":%.0f}",
            trades[i].id,symbol,trades[i].timeframe,trades[i].direction,
            trades[i].entry,trades[i].sl,trades[i].tp,trades[i].volume,
            trades[i].riskMoney,profit,(double)trades[i].opened);
      }
   }
   else
   {
      bool first=true;
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         if(!posInfo.SelectByIndex(i)) continue;
         if(posInfo.Magic()!=MagicNumber) continue;

         string ticket=IntegerToString(posInfo.Ticket());
         int    idx   =FindTicketIndex(ticket);

         // nếu position mồ côi (EA restart), vẫn gửi với fallback hợp lệ
         // Ticket là ID duy nhất cho từng position, tránh trùng signal ID khi khớp nhiều phần.
         string positionId = ticket;
         string tfValue   = (idx>=0) ? realTimeframes[idx] : "M15";  // phải thuộc Literal
         double riskMoney = (idx>=0) ? realRisks[idx]      : 0.0;

         double profit=posInfo.Profit()+posInfo.Swap()+posInfo.Commission();
         if(!first) body+=",";
         first=false;
         body+=StringFormat(
            "{\"id\":\"%s\",\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\","
            "\"entry\":%.10f,\"sl\":%.10f,\"tp\":%.10f,\"volume\":%.8f,"
            "\"risk\":%.2f,\"profit\":%.2f,\"opened_at\":%.0f}",
            positionId, posInfo.Symbol(), tfValue,
            (posInfo.PositionType()==POSITION_TYPE_BUY?"BUY":"SELL"),
            posInfo.PriceOpen(), posInfo.StopLoss(), posInfo.TakeProfit(),
            posInfo.Volume(), riskMoney, profit, (double)posInfo.Time());
      }
   }
   body+="]}";

   string response; int status;
   bool ok=Request("POST","/api/paper/positions",body,response,status);
   if(!ok)
      Print("[V9] Position sync failed HTTP=",status,
            " body=",body," response=",response);
}

//--- Lifecycle -----------------------------------------------------
int OnInit()
{
   if(StringFind(ServerBaseURL,"https://")!=0)
   { Print("[V9] HTTPS server URL required."); return INIT_PARAMETERS_INCORRECT; }
   if(ApiKey=="" || ApiKey=="SET_A_LONG_RANDOM_API_KEY")
   { Print("[V9] Set ApiKey privately in EA inputs."); return INIT_PARAMETERS_INCORRECT; }

   trade.SetExpertMagicNumber(MagicNumber);
   EventSetTimer(1);
   Print("[V9] Started in ",
         (UsePaperSimulation?"PAPER (no broker orders)":"REAL (live orders enabled)"),
         " mode. magic=",MagicNumber);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) { EventKillTimer(); }

void OnTick() {}

void OnTimer()
{
   ManagePaperTrades();
   PruneClosedRealPositions();

   uint nowMs=GetTickCount();

   if(lastHeartbeatMs==0 ||
      (uint)(nowMs-lastHeartbeatMs)>=(uint)(HeartbeatSeconds*1000))
      SendHeartbeat();

   if(lastPollMs==0 ||
      (uint)(nowMs-lastPollMs)>=(uint)(PollSeconds*1000))
   {
      lastPollMs=nowMs;
      PollServer();
   }

   if(lastPositionsSyncMs==0 ||
      (uint)(nowMs-lastPositionsSyncMs)>=3000)
   {
      lastPositionsSyncMs=nowMs;
      SendPaperPositions();
   }
}
