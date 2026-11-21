//+------------------------------------------------------------------+
//| TRADE_V9_PAPER.mq5                                               |
//| Forex AI Server consumer.                                        |
//| UsePaperSimulation=true  -> virtual paper trades (no broker order)|
//| UsePaperSimulation=false -> REAL broker orders via CTrade        |
//| Pending orders: LIMIT ONLY (BUY_LIMIT / SELL_LIMIT), no Stop     |
//+------------------------------------------------------------------+
#property strict
#property version "2.13"

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
input bool   SyncRealHistory     = true;    // Đồng bộ lịch sử lệnh thật đã đóng

//--- Globals --------------------------------------------------------
CTrade        trade;
CPositionInfo posInfo;

struct PaperTrade
{
   string   id;
   string   symbol;
   string   timeframe;
   string   direction;
   string   orderType;   // "MARKET", "BUY_LIMIT", "SELL_LIMIT"
   bool     isPending;   // true khi đang chờ khớp Limit
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

// chống xử lý trùng signal_id trong phiên
string   processedSignalIds[];
string   lastHistoryDigest = "";

uint lastPollMs          = 0;
uint lastPositionsSyncMs = 0;
uint lastHeartbeatMs     = 0;
uint lastHistorySyncMs   = 0;

//--- Forward declarations -------------------------------------------
void SendPaperPositions();
void PublishRealClosedDeals();

//--- JSON & string helpers ------------------------------------------
string JsonEscape(string s)
{
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   StringReplace(s, "\r", " ");
   StringReplace(s, "\n", " ");
   return s;
}

string UpperTrim(string s)
{
   StringTrimLeft(s);
   StringTrimRight(s);
   StringToUpper(s);
   return s;
}

datetime ParseIsoOrEpochTime(string raw)
{
   StringTrimLeft(raw);
   StringTrimRight(raw);
   if(raw == "") return 0;

   bool allDigits = true;
   for(int i=0; i<StringLen(raw); i++)
   {
      ushort ch = StringGetCharacter(raw, i);
      if(ch < '0' || ch > '9') { allDigits = false; break; }
   }
   if(allDigits) return (datetime)StringToInteger(raw);

   // Hỗ trợ định dạng ISO như "2026-11-20T12:30:00Z" hoặc "2026.11.20 12:30:00"
   string s = raw;
   StringReplace(s, "T", " ");
   StringReplace(s, "Z", "");
   int plusPos = StringFind(s, "+");
   if(plusPos > 0) s = StringSubstr(s, 0, plusPos);
   StringReplace(s, "-", ".");
   return StringToTime(s);
}

bool HasProcessedSignal(string id)
{
   for(int i=0; i<ArraySize(processedSignalIds); i++)
      if(processedSignalIds[i] == id) return true;
   return false;
}

void MarkProcessedSignal(string id)
{
   if(id == "" || HasProcessedSignal(id)) return;
   int n = ArraySize(processedSignalIds);
   if(n >= 500)
   {
      for(int i=0; i<n-1; i++) processedSignalIds[i] = processedSignalIds[i+1];
      processedSignalIds[n-1] = id;
      return;
   }
   ArrayResize(processedSignalIds, n+1);
   processedSignalIds[n] = id;
}

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
      string out="";
      for(int i=p+1; i<StringLen(json); i++)
      {
         ushort ch=StringGetCharacter(json,i);
         if(ch=='\\' && i+1<StringLen(json))
         {
            ushort nx=StringGetCharacter(json,i+1);
            if(nx=='"' || nx=='\\') { out+=ShortToString(nx); i++; continue; }
         }
         if(ch=='"') return out;
         out+=ShortToString(ch);
      }
      return "";
   }
   int e=p;
   while(e<StringLen(json) && StringGetCharacter(json,e)!=',' &&
         StringGetCharacter(json,e)!='}' && StringGetCharacter(json,e)!=']' &&
         StringGetCharacter(json,e)!='\n') e++;
   string v=StringSubstr(json,p,e-p);
   StringTrimLeft(v); StringTrimRight(v);
   if(v=="null") return "";
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

string JsonArrayValue(string json,string key)
{
   string token="\""+key+"\"";
   int p=StringFind(json,token);
   if(p<0) return "";
   p=StringFind(json,":",p+StringLen(token));
   if(p<0) return "";
   p++;
   while(p<StringLen(json) && StringGetCharacter(json,p)<=32) p++;
   if(p>=StringLen(json) || StringGetCharacter(json,p)!='[') return "";
   int depth=0; bool quoted=false;
   for(int i=p;i<StringLen(json);i++)
   {
      ushort c=StringGetCharacter(json,i);
      if(c=='"' && (i==0 || StringGetCharacter(json,i-1)!='\\')) quoted=!quoted;
      if(quoted) continue;
      if(c=='[') depth++;
      if(c==']' && --depth==0) return StringSubstr(json,p,i-p+1);
   }
   return "";
}

void ExtractTopLevelObjects(string arrJson,string &outObjects[])
{
   ArrayResize(outObjects,0);
   int depth=0; bool quoted=false; int start=-1;
   for(int i=0;i<StringLen(arrJson);i++)
   {
      ushort c=StringGetCharacter(arrJson,i);
      if(c=='"' && (i==0 || StringGetCharacter(arrJson,i-1)!='\\')) quoted=!quoted;
      if(quoted) continue;
      if(c=='{')
      {
         if(depth==0) start=i;
         depth++;
      }
      else if(c=='}')
      {
         depth--;
         if(depth==0 && start>=0)
         {
            int n=ArraySize(outObjects);
            ArrayResize(outObjects,n+1);
            outObjects[n]=StringSubstr(arrJson,start,i-start+1);
            start=-1;
         }
      }
   }
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
   int    openCount = UsePaperSimulation ? ArraySize(trades) : CountRealExposure();
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
   string body="{\"signal_id\":\""+JsonEscape(id)+"\",\"status\":\""+JsonEscape(state)+"\",\"message\":\""+JsonEscape(message)+"\"}";
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

//--- Đếm position & pending limit --------------------------------
int CountRealPositions()
{
   int n=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
      if(posInfo.SelectByIndex(i) && posInfo.Magic()==MagicNumber) n++;
   return n;
}

int CountRealLimitOrders()
{
   int n=0;
   for(int i=OrdersTotal()-1;i>=0;i--)
   {
      ulong tk=OrderGetTicket(i);
      if(tk==0) continue;
      if(OrderGetInteger(ORDER_MAGIC)!=MagicNumber) continue;
      ENUM_ORDER_TYPE ot=(ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      if(ot==ORDER_TYPE_BUY_LIMIT || ot==ORDER_TYPE_SELL_LIMIT)
         n++;
   }
   return n;
}

int CountRealExposure()
{
   return CountRealPositions() + CountRealLimitOrders();
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
      ulong tk=(ulong)StringToInteger(realTicketIds[i]);
      if(!PositionSelectByTicket(tk) && !OrderSelect(tk))
         ForgetRealPosition(realTicketIds[i]);
   }
}

//--- Chuẩn hóa & kiểm tra loại lệnh (chỉ MARKET, BUY_LIMIT, SELL_LIMIT) ---
bool ResolveOrderType(string rawOrderType,string direction,string &normOrderType,string &errOut)
{
   string ot = UpperTrim(rawOrderType);
   string dir = UpperTrim(direction);

   if(StringFind(ot, "STOP") >= 0)
   {
      errOut = "Stop orders are not supported (LIMIT only: BUY_LIMIT / SELL_LIMIT)";
      return false;
   }

   if(ot == "" || ot == "MARKET")
   {
      normOrderType = "MARKET";
      if(dir == "BUY" || dir == "SELL") return true;
      errOut = "Invalid direction for MARKET";
      return false;
   }

   if(ot == "BUY_LIMIT")
   {
      normOrderType = "BUY_LIMIT";
      if(dir != "BUY") { errOut = "BUY_LIMIT requires direction=BUY"; return false; }
      return true;
   }

   if(ot == "SELL_LIMIT")
   {
      normOrderType = "SELL_LIMIT";
      if(dir != "SELL") { errOut = "SELL_LIMIT requires direction=SELL"; return false; }
      return true;
   }

   errOut = "Unsupported order_type: " + ot;
   return false;
}

//--- Mở lệnh (paper hoặc real) ------------------------------------
bool OpenTrade(string id,string symbol,string tf,string direction,string rawOrderType,
               double entry,double sl,double tpRequested,
               double riskMoney,double requestedLot)
{
   if(!SymbolSelect(symbol,true))
   { Ack(id,"REJECTED","Symbol unavailable in Market Watch"); return false; }

   double bid=SymbolInfoDouble(symbol,SYMBOL_BID);
   double ask=SymbolInfoDouble(symbol,SYMBOL_ASK);
   if(bid<=0 || ask<=0) { Ack(id,"REJECTED","No live quote"); return false; }

   string normOrderType = "";
   string typeErr = "";
   if(!ResolveOrderType(rawOrderType, direction, normOrderType, typeErr))
   { Ack(id,"REJECTED",typeErr); return false; }

   bool isLimit = (normOrderType == "BUY_LIMIT" || normOrderType == "SELL_LIMIT");
   if(isLimit)
   {
      if(normOrderType == "BUY_LIMIT" && entry >= ask)
      { Ack(id,"REJECTED","BUY_LIMIT requires entry < current Ask"); return false; }
      if(normOrderType == "SELL_LIMIT" && entry <= bid)
      { Ack(id,"REJECTED","SELL_LIMIT requires entry > current Bid"); return false; }
   }

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
   entry=NormalizeDouble(entry,digits);
   sl=NormalizeDouble(sl,digits);
   tp=NormalizeDouble(tp,digits);

   // ==================== PAPER ====================
   if(UsePaperSimulation)
   {
      int n=ArraySize(trades); ArrayResize(trades,n+1);
      trades[n].id=id; trades[n].symbol=symbol; trades[n].timeframe=tf;
      trades[n].direction=direction; trades[n].orderType=normOrderType;
      trades[n].isPending=isLimit;
      trades[n].entry=entry; trades[n].sl=sl;
      trades[n].tp=tp; trades[n].volume=volume;
      trades[n].riskMoney=actualRisk; trades[n].opened=TimeCurrent();

      Print(isLimit ? "[PAPER LIMIT QUEUED] " : "[PAPER OPEN] ",
            symbol," ",tf," ",normOrderType," ",direction,
            " entry=",DoubleToString(entry,digits),
            " SL=",DoubleToString(sl,digits),
            " TP=",DoubleToString(tp,digits),
            " lots=",DoubleToString(volume,4),
            " risk=$",DoubleToString(actualRisk,2));
      Ack(id,"SIMULATED",
          isLimit ? (normOrderType + " paper pending queued; no broker order sent")
                  : "Paper position opened; no broker order sent");
      return true;
   }

   // ==================== REAL ====================
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFilling(PickFilling(symbol));
   trade.SetAsyncMode(false);

   bool ok = false;
   if(normOrderType == "BUY_LIMIT")
      ok = trade.BuyLimit(volume,entry,symbol,sl,tp,ORDER_TIME_GTC,0,OrderComment);
   else if(normOrderType == "SELL_LIMIT")
      ok = trade.SellLimit(volume,entry,symbol,sl,tp,ORDER_TIME_GTC,0,OrderComment);
   else
      ok = (direction=="BUY")
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

   ulong posTicket = trade.ResultOrder();
   if(PositionSelectByTicket(posTicket))
      posTicket = (ulong)PositionGetInteger(POSITION_TICKET);

   Print("[REAL OPEN] signal=",id," ",symbol," ",tf," ",normOrderType," ",direction,
         " lots=",DoubleToString(volume,4),
         " entry=",DoubleToString(entry,digits),
         " SL=",DoubleToString(sl,digits),
         " TP=",DoubleToString(tp,digits),
         " ticket=",posTicket,
         " risk=$",DoubleToString(actualRisk,2));

   RememberRealPosition(
      IntegerToString((long)posTicket),
      id, symbol, tf, direction, actualRisk);

   Ack(id,"SIMULATED",
       StringFormat("Real broker order placed (%s) lot=%.4f ticket=%I64u",
                    normOrderType,volume,posTicket));
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
      JsonEscape(trades[i].id),JsonEscape(trades[i].symbol),JsonEscape(trades[i].timeframe),trades[i].direction,
      trades[i].entry,trades[i].sl,trades[i].tp,trades[i].volume,trades[i].riskMoney,
      NormalizeDouble(realized,2),(double)trades[i].opened,JsonEscape(outcome),(double)TimeCurrent());
   string response; int status;
   if(!Request("POST","/api/paper/closed",body,response,status))
      Print("[V9] Closed history sync failed HTTP=",status," response=",response);
}

bool ClosePaperTradeAtIndex(int idx,string outcome)
{
   if(idx<0 || idx>=ArraySize(trades)) return false;
   string s=trades[idx].symbol;
   double bid=SymbolInfoDouble(s,SYMBOL_BID), ask=SymbolInfoDouble(s,SYMBOL_ASK);
   double realized=0;
   if(!trades[idx].isPending && bid>0 && ask>0)
   {
      ENUM_ORDER_TYPE orderType=(trades[idx].direction=="BUY") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      double closePrice=(trades[idx].direction=="BUY") ? bid : ask;
      OrderCalcProfit(orderType,s,trades[idx].volume,trades[idx].entry,closePrice,realized);
   }
   Print("[PAPER CLOSE] ",outcome," ",s," signal=",trades[idx].id,
         " volume=",DoubleToString(trades[idx].volume,4),
         " realized P/L=",DoubleToString(realized,2));
   SendPaperClosed(idx,outcome,realized);

   int last=ArraySize(trades)-1;
   for(int j=idx;j<last;j++) trades[j]=trades[j+1];
   ArrayResize(trades,last);
   return true;
}

void ManagePaperTrades()
{
   if(!UsePaperSimulation) return;

   for(int i=ArraySize(trades)-1;i>=0;i--)
   {
      string s=trades[i].symbol;
      double bid=SymbolInfoDouble(s,SYMBOL_BID), ask=SymbolInfoDouble(s,SYMBOL_ASK);
      if(bid<=0 || ask<=0) continue;

      if(trades[i].isPending)
      {
         bool triggered=false;
         if(trades[i].orderType=="BUY_LIMIT" && ask<=trades[i].entry)
            triggered=true;
         else if(trades[i].orderType=="SELL_LIMIT" && bid>=trades[i].entry)
            triggered=true;

         if(triggered)
         {
            trades[i].isPending=false;
            trades[i].opened=TimeCurrent();
            Print("[PAPER LIMIT TRIGGERED] ",trades[i].orderType," ",s," id=",trades[i].id,
                  " @ ",DoubleToString(trades[i].entry,(int)SymbolInfoInteger(s,SYMBOL_DIGITS)));
         }
         continue;
      }

      bool stop=false,target=false;
      if(trades[i].direction=="BUY") { stop=(bid<=trades[i].sl); target=(bid>=trades[i].tp); }
      else                            { stop=(ask>=trades[i].sl); target=(ask<=trades[i].tp); }
      if(!stop && !target) continue;

      string outcome=target ? "PAPER_TP" : "PAPER_SL";
      ClosePaperTradeAtIndex(i,outcome);
   }
}

//--- Nhận và thực thi lệnh đóng từ server ------------------------
void AckCloseCommand(string requestId,string targetId,bool ok,string detail)
{
   string body=StringFormat(
      "{\"request_id\":\"%s\",\"position_id\":\"%s\",\"ok\":%s,\"message\":\"%s\"}",
      JsonEscape(requestId),JsonEscape(targetId),(ok?"true":"false"),JsonEscape(detail));
   string response; int status;
   if(!Request("POST","/api/paper/close-requests/ack",body,response,status))
      Print("[V9 CLOSE ACK FAIL] req=",requestId," HTTP=",status," response=",response);
}

int FindPaperTradeIndex(string targetId,string symbol,string direction)
{
   if(targetId!="")
   {
      for(int i=0;i<ArraySize(trades);i++)
         if(trades[i].id==targetId) return i;
   }
   if(symbol!="")
   {
      for(int i=0;i<ArraySize(trades);i++)
         if(trades[i].symbol==symbol && (direction=="" || trades[i].direction==direction))
            return i;
   }
   return -1;
}

bool CloseRealPositionById(string targetId,string symbol,string &detailOut)
{
   ulong targetTicket=0;
   if(targetId!="")
   {
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         if(!posInfo.SelectByIndex(i)) continue;
         if(posInfo.Magic()!=MagicNumber) continue;
         string tk=IntegerToString((long)posInfo.Ticket());
         string posId=IntegerToString((long)PositionGetInteger(POSITION_IDENTIFIER));
         int rIdx=FindTicketIndex(tk);
         string sigId=(rIdx>=0) ? realSignalIds[rIdx] : "";
         if(tk==targetId || posId==targetId || (sigId!="" && sigId==targetId))
         {
            targetTicket=posInfo.Ticket();
            break;
         }
      }
   }
   if(targetTicket==0 && symbol!="")
   {
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         if(!posInfo.SelectByIndex(i)) continue;
         if(posInfo.Magic()!=MagicNumber) continue;
         if(posInfo.Symbol()==symbol)
         {
            targetTicket=posInfo.Ticket();
            break;
         }
      }
   }

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetAsyncMode(false);

   if(targetTicket!=0)
   {
      if(!PositionSelectByTicket(targetTicket))
      {
         detailOut="Position disappeared";
         return false;
      }
      trade.SetTypeFilling(PickFilling(PositionGetString(POSITION_SYMBOL)));
      if(!trade.PositionClose(targetTicket))
      {
         detailOut=StringFormat("PositionClose failed retcode=%d (%s)",
            trade.ResultRetcode(),trade.ResultRetcodeDescription());
         Print("[REAL CLOSE FAIL] ticket=",targetTicket," ",detailOut);
         return false;
      }
      ForgetRealPosition(IntegerToString((long)targetTicket));
      detailOut=StringFormat("Closed ticket=%I64u",targetTicket);
      Print("[REAL CLOSE] ",detailOut);
      return true;
   }

   // Nếu không có position mở thì kiểm tra pending limit order tương ứng để hủy
   for(int i=OrdersTotal()-1;i>=0;i--)
   {
      ulong ordTicket=OrderGetTicket(i);
      if(ordTicket==0) continue;
      if(OrderGetInteger(ORDER_MAGIC)!=MagicNumber) continue;
      ENUM_ORDER_TYPE ot=(ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      if(ot!=ORDER_TYPE_BUY_LIMIT && ot!=ORDER_TYPE_SELL_LIMIT) continue;
      string ordSym=OrderGetString(ORDER_SYMBOL);
      string tkStr=IntegerToString((long)ordTicket);
      int rIdx=FindTicketIndex(tkStr);
      string sigId=(rIdx>=0) ? realSignalIds[rIdx] : "";
      bool matchId=(targetId!="" && (tkStr==targetId || (sigId!="" && sigId==targetId)));
      bool matchSym=(symbol!="" && ordSym==symbol);
      if(matchId || matchSym)
      {
         if(trade.OrderDelete(ordTicket))
         {
            ForgetRealPosition(tkStr);
            detailOut=StringFormat("Deleted pending limit ticket=%I64u",ordTicket);
            Print("[REAL CANCEL LIMIT] ",detailOut);
            return true;
         }
      }
   }

   detailOut="Position not found";
   return false;
}

void ExecuteCloseCommand(string cmdJson)
{
   string reqId     = JsonValue(cmdJson,"request_id");
   string targetId  = JsonValue(cmdJson,"position_id");
   if(targetId=="") targetId = JsonValue(cmdJson,"ticket");
   if(targetId=="") targetId = JsonValue(cmdJson,"id");
   string symbol    = JsonValue(cmdJson,"symbol");
   string direction = JsonValue(cmdJson,"direction");
   if(reqId=="") reqId = targetId;

   if(targetId=="" && symbol=="") return;

   Print("[V9 CLOSE REQ] id=",targetId," symbol=",symbol," mode=",(UsePaperSimulation?"PAPER":"REAL"));

   bool closed=false;
   string detail="";

   if(UsePaperSimulation)
   {
      int idx=FindPaperTradeIndex(targetId,symbol,direction);
      if(idx>=0)
      {
         closed=ClosePaperTradeAtIndex(idx,"MANUAL_CLOSE");
         detail="Closed paper position";
      }
      else
      {
         detail="Paper position not found";
      }
   }
   else
   {
      closed=CloseRealPositionById(targetId,symbol,detail);
   }

   AckCloseCommand(reqId,targetId,closed,detail);
   if(closed)
   {
      SendPaperPositions();
      if(!UsePaperSimulation) PublishRealClosedDeals();
   }
}

void PollCloseCommands()
{
   string response; int status;
   if(!Request("GET","/api/paper/close-requests","",response,status))
      return;

   string arr=JsonArrayValue(response,"close_requests");
   if(arr=="") arr=JsonArrayValue(response,"requests");
   if(arr=="") arr=JsonArrayValue(response,"items");

   if(arr!="")
   {
      string items[];
      ExtractTopLevelObjects(arr,items);
      for(int i=0;i<ArraySize(items);i++)
         ExecuteCloseCommand(items[i]);
      return;
   }

   string single=JsonObjectValue(response,"close_request");
   if(single=="") single=JsonObjectValue(response,"request");
   if(single!="")
      ExecuteCloseCommand(single);
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
   string orderType = JsonValue(obj,"order_type");
   double entry     = StringToDouble(JsonValue(obj,"entry"));
   double sl        = StringToDouble(JsonValue(obj,"sl"));
   double tp        = StringToDouble(JsonValue(obj,"tp"));
   double riskMoney = StringToDouble(JsonValue(obj,"risk_money"));
   double reqLot    = StringToDouble(JsonValue(obj,"requested_lot"));
   datetime sigTime = ParseIsoOrEpochTime(JsonValue(obj,"created_at"));

   Print("[V9 RECEIVE] signal=",id," ",symbol," ",tf," ",(orderType==""?"MARKET":orderType)," ",direction,
         " entry=",DoubleToString(entry,8)," SL=",DoubleToString(sl,8),
         " lot=",DoubleToString(reqLot,4)," risk=$",DoubleToString(riskMoney,2));

   if(id=="" || symbol=="" || entry<=0 || sl<=0 || riskMoney<=0 || reqLot<=0 ||
      (direction!="BUY" && direction!="SELL"))
   { Print("[V9 REJECT] Malformed signal id=",id);
     if(id!="") Ack(id,"REJECTED","Malformed signal"); return; }

   if(HasProcessedSignal(id))
      return;

   if(MaxSignalAgeSeconds > 0 && sigTime > 0 && (TimeCurrent() - sigTime) > MaxSignalAgeSeconds)
   {
      MarkProcessedSignal(id);
      Print("[V9 REJECT] Signal expired age=", (TimeCurrent() - sigTime), "s id=", id);
      Ack(id,"REJECTED","Signal exceeded MaxSignalAgeSeconds");
      return;
   }

   if((direction=="BUY" && sl>=entry) || (direction=="SELL" && sl<=entry))
   { MarkProcessedSignal(id);
     Print("[V9 REJECT] Invalid entry/SL geometry signal=",id);
     Ack(id,"REJECTED","Invalid entry/SL geometry"); return; }

   if(tp>0 && ((direction=="BUY" && tp<=entry) || (direction=="SELL" && tp>=entry)))
   { MarkProcessedSignal(id);
     Print("[V9 REJECT] Invalid TP geometry signal=",id);
     Ack(id,"REJECTED","Invalid TP geometry"); return; }

   int openCount = UsePaperSimulation ? ArraySize(trades) : CountRealExposure();
   if(openCount>=MaxOpenPaperTrades)
   { Print("[V9 REJECT] Max positions reached signal=",id," open=",openCount);
     Ack(id,"REJECTED","Max positions reached"); return; }

   MarkProcessedSignal(id);
   OpenTrade(id,symbol,tf,direction,orderType,entry,sl,tp,riskMoney,reqLot);
}

//--- Sync positions & real closed history --------------------------
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
         if(!trades[i].isPending && bid>0 && ask>0)
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
            JsonEscape(trades[i].id),JsonEscape(symbol),JsonEscape(trades[i].timeframe),trades[i].direction,
            trades[i].entry,trades[i].sl,trades[i].tp,trades[i].volume,
            trades[i].riskMoney,NormalizeDouble(profit,2),(double)trades[i].opened);
      }
   }
   else
   {
      bool first=true;
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         if(!posInfo.SelectByIndex(i)) continue;
         if(posInfo.Magic()!=MagicNumber) continue;

         string ticket=IntegerToString((long)posInfo.Ticket());
         int    idx   =FindTicketIndex(ticket);

         string positionId = ticket;
         string tfValue   = (idx>=0) ? realTimeframes[idx] : "M15";
         double riskMoney = (idx>=0) ? realRisks[idx]      : 0.0;

         double profit=posInfo.Profit()+posInfo.Swap()+posInfo.Commission();
         if(!first) body+=",";
         first=false;
         body+=StringFormat(
            "{\"id\":\"%s\",\"symbol\":\"%s\",\"timeframe\":\"%s\",\"direction\":\"%s\","
            "\"entry\":%.10f,\"sl\":%.10f,\"tp\":%.10f,\"volume\":%.8f,"
            "\"risk\":%.2f,\"profit\":%.2f,\"opened_at\":%.0f}",
            JsonEscape(positionId), JsonEscape(posInfo.Symbol()), JsonEscape(tfValue),
            (posInfo.PositionType()==POSITION_TYPE_BUY?"BUY":"SELL"),
            posInfo.PriceOpen(), posInfo.StopLoss(), posInfo.TakeProfit(),
            posInfo.Volume(), riskMoney, NormalizeDouble(profit,2), (double)posInfo.Time());
      }
   }
   body+="]}";

   string response; int status;
   bool ok=Request("POST","/api/paper/positions",body,response,status);
   if(!ok)
      Print("[V9] Position sync failed HTTP=",status,
            " body=",body," response=",response);
}

void PublishRealClosedDeals()
{
   if(UsePaperSimulation || !SyncRealHistory) return;
   datetime now = TimeCurrent();
   datetime fromTime = now - (datetime)(7 * 86400);
   if(!HistorySelect(fromTime, now)) return;

   int totalDeals = HistoryDealsTotal();
   if(totalDeals <= 0) return;

   string tradesJson = "";
   int count = 0;
   for(int i = totalDeals - 1; i >= 0 && count < 200; i--)
   {
      ulong dealTicket = HistoryDealGetTicket(i);
      if(dealTicket == 0) continue;
      if(HistoryDealGetInteger(dealTicket, DEAL_MAGIC) != MagicNumber) continue;
      long entryType = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
      if(entryType != DEAL_ENTRY_OUT && entryType != DEAL_ENTRY_INOUT) continue;

      string sym = HistoryDealGetString(dealTicket, DEAL_SYMBOL);
      if(sym == "") continue;
      long dealType = HistoryDealGetInteger(dealTicket, DEAL_TYPE);
      string dir = (dealType == DEAL_TYPE_BUY) ? "SELL" : "BUY";
      double vol = HistoryDealGetDouble(dealTicket, DEAL_VOLUME);
      double price = HistoryDealGetDouble(dealTicket, DEAL_PRICE);
      double profit = HistoryDealGetDouble(dealTicket, DEAL_PROFIT)
                  + HistoryDealGetDouble(dealTicket, DEAL_SWAP)
                  + HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
      long closedAt = HistoryDealGetInteger(dealTicket, DEAL_TIME);
      if(vol <= 0 || closedAt <= 0) continue;

      if(tradesJson != "") tradesJson += ",";
      tradesJson += StringFormat(
         "{\"id\":\"v9-%I64u\",\"ticket\":\"%I64u\",\"symbol\":\"%s\",\"timeframe\":\"MT5\",\"direction\":\"%s\",\"entry\":%.8f,\"sl\":0,\"tp\":0,\"volume\":%.4f,\"profit\":%.2f,\"outcome\":\"CLOSED\",\"opened_at\":0,\"closed_at\":%I64d}",
         dealTicket, dealTicket, JsonEscape(sym), dir, price, vol, NormalizeDouble(profit,2), closedAt
      );
      count++;
   }

   if(count <= 0 || tradesJson == lastHistoryDigest) return;
   string body = "{\"replace\":false,\"trades\":[" + tradesJson + "]}";
   string response; int status;
   if(Request("POST", "/api/mt5/history", body, response, status))
      lastHistoryDigest = tradesJson;
   else
      Print("[V9] Real history sync failed HTTP=", status, " response=", response);
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
      PollCloseCommands();
      PollServer();
   }

   if(lastPositionsSyncMs==0 ||
      (uint)(nowMs-lastPositionsSyncMs)>=3000)
   {
      lastPositionsSyncMs=nowMs;
      SendPaperPositions();
   }

   if(!UsePaperSimulation && SyncRealHistory &&
      (lastHistorySyncMs==0 || (uint)(nowMs-lastHistorySyncMs)>=15000))
   {
      lastHistorySyncMs=nowMs;
      PublishRealClosedDeals();
   }
}
