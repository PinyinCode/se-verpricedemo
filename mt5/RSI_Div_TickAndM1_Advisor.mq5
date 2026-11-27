//+------------------------------------------------------------------+
//|                               RSI_Div_TickAndM1_Advisor_v14.mq5  |
//|   Bo sung theo doi duong di gia SAU KHI CHOT LENH (Post-Exit)    |
//|   Do fullWavePeakR, postCloseExtraR & ty le lenh bo lo song dai  |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property version   "14.00"
#property script_show_inputs

input group "=== Khoang thoi gian (Mac dinh 0 = Hom nay GMT+7) ==="
input datetime        InpFromDate_GMT7      = 0;                  // Tu ngay GMT+7 (0 = 00:00 hom nay GMT+7)
input datetime        InpToDate_GMT7        = 0;                  // Den ngay GMT+7 (0 = 23:59:59 hom nay GMT+7)
input int             InpManualServerOffHr  = 0;                  // Offset Server so voi GMT+7 (0 = tu dong)

input group "=== Bo loc lenh & Duong di gia ==="
input long            InpMagicFilter        = 20240915;           // Magic EA (TRADE_V9 = 20240915, -1 = tat ca)
input string          InpSymbolFilter       = "";                 // Loc Symbol (de trong = tat ca)
input bool            InpUseRealTicks       = true;               // Uu tien du lieu tick (CopyTicksRange) -> M1
input int             InpMaxTicksPerTrade   = 20000;              // Gioi han tick/lenh
input int             InpPostExitBarsM15    = 24;                 // So nen M15 soi them SAU khi dong lenh (24 = 6h)
input double          InpWarnSpreadSL_Pct   = 15.0;               // Nguong canh bao Spread/SL (%)
input bool            InpExportCsv          = true;               // Xuat duong di gia chi tiet ra CSV
input bool            InpExportSummary      = true;               // Xuat bao cao tong hop ra TXT

input group "=== Tham so dang dung (Doi chieu voi Web Config) ==="
input double          Cfg_RiskMoney         = 25.0;               // Risk / lenh ($)
input int             Cfg_MaxPositions      = 5;                  // Max positions
input double          Cfg_TargetRR          = 2.0;                // Reward / Risk ratio
input double          Cfg_RiskTolerancePct  = 0.25;               // Risk tolerance (%)
input double          Cfg_CommFallbackPerLot= 0.0;                // Commission fallback ($/lot)
input int             Cfg_SpreadBufferPts   = 0;                  // Spread buffer (points)
input double          Cfg_Trail_TriggerRR   = 1.0;                // Trailing trigger (R)
input double          Cfg_Trail_LockRR      = 0.5;                // Trailing lock (R)
input double          Cfg_BE_TriggerRR      = 1.0;                // BE trigger (R)
input string          Cfg_TrailingMode      = "Dynamic";          // Trailing mode

input group "=== Thong so Reject & ATR (Doi chieu Indicator) ==="
input ENUM_TIMEFRAMES InpATR_TF             = PERIOD_M15;         // Khung thoi gian doc ATR lich su
input int             Cfg_ATR_Period        = 14;                 // Chu ky ATR (x ATR)
input bool            RejectOnCandleClose   = false;              // true: cho nen dong; false: xet ngay tren nen dang chay
input int             RejectMaxBarsAfter    = 1;                  // So nen toi da sau nen cuc tri duoc phep xac nhan
input double          RejectMinWickRatio    = 0.5;                // Gia dong phai lui it nhat ti le nay cua bien do (0-1)
input double          RejectMinRangeATR     = 0.3;                // Bien do toi thieu cua cu reject (x ATR)
input double          RejectMinBreakATR     = 0.05;               // Regular: do quet toi thieu qua dinh/day cu (x ATR)
input double          RejectMinCloseBackATR = 0.02;               // Regular: dong lai ben trong muc cu toi thieu (x ATR)
input bool            RejectRequireCloseDiv = false;              // Bat buoc them dieu kien gia dong nen nhu ban cu

struct SPathPtV14
{
   datetime time;
   double   price;
   double   rVal;
   bool     isPostClose;
};

struct STradeV14
{
   ulong       posId;
   string      sym;
   bool        isBuy;
   datetime    tOpenSrv;
   datetime    tCloseSrv;
   datetime    tPostEndSrv;
   datetime    tOpenGMT7;
   datetime    tCloseGMT7;
   double      entry;
   double      closePrice;
   double      lot;
   double      netProfit;
   double      oneR_Price;
   double      oneR_Money;
   double      realizedR;
   double      peakR;           // Dinh R trong luc giu lenh [tOpen, tClose]
   double      troughR;         // Day R trong luc giu lenh
   double      postPeakR;       // Dinh R cao nhat SAU khi dong lenh (tClose -> tPostEnd)
   double      postExtraR;      // Phan chay them sau luc dong: max(0, postPeakR - MathMax(peakR, realizedR))
   double      fullWavePeakR;   // Dinh R tong the cua con song (ca truoc va sau khi dong)
   double      atrClosedAtOpen;
   double      slVsATR_X;
   double      peakRangeATR;
   double      spreadPtsAtOpen;
   double      spreadVsSL_Pct;
   bool        highSpreadLoss;
   bool        premature1RMiss; // Chot quanh ~1R nhung sau do song chay them >= +1.0R
   string      srcType;
   SPathPtV14  pts[];
};

double Clamp14(double v, double lo, double hi) { return v < lo ? lo : (v > hi ? hi : v); }
double Round14(double v, double st) { return st > 0.0 ? MathRound(v / st) * st : v; }

long CalcServerMinusGMT7Sec()
{
   if(InpManualServerOffHr != 0)
      return (long)InpManualServerOffHr * 3600LL;

   datetime gt = TimeGMT();
   datetime st = TimeTradeServer();
   if(st <= 0) st = TimeCurrent();

   if(gt > 0 && st > 0)
   {
      long srvMinusGmt = (long)MathRound((double)((long)st - (long)gt) / 3600.0) * 3600LL;
      return srvMinusGmt - 7LL * 3600LL;
   }
   return 0;
}

datetime GetCurrentGMT7Now(long srvMinusGmt7Sec)
{
   datetime gt = TimeGMT();
   if(gt > 0) return (datetime)((long)gt + 7LL * 3600LL);
   datetime st = TimeTradeServer();
   if(st <= 0) st = TimeCurrent();
   return (datetime)((long)st - srvMinusGmt7Sec);
}

void ResolveGMT7Range(long srvMinusGmt7Sec, datetime &outFromGmt7, datetime &outToGmt7)
{
   datetime nowGmt7 = GetCurrentGMT7Now(srvMinusGmt7Sec);
   long dayStartSec = ((long)nowGmt7 / 86400LL) * 86400LL;
   outFromGmt7 = (InpFromDate_GMT7 > 0) ? InpFromDate_GMT7 : (datetime)dayStartSec;
   outToGmt7   = (InpToDate_GMT7   > 0) ? InpToDate_GMT7   : (datetime)(dayStartSec + 86399LL);
}

datetime GMT7ToServer(datetime gmt7Dt, long srvMinusGmt7Sec) { return (datetime)((long)gmt7Dt + srvMinusGmt7Sec); }
datetime ServerToGMT7(datetime srvDt, long srvMinusGmt7Sec) { return (datetime)((long)srvDt - srvMinusGmt7Sec); }

double GetHistoricalClosedATR(string sym, ENUM_TIMEFRAMES tf, datetime srvTime, int atrPeriod)
{
   int p = MathMax(1, atrPeriod);
   int h = iATR(sym, tf, p);
   if(h == INVALID_HANDLE) return 0.0;

   int shift = iBarShift(sym, tf, srvTime, false);
   if(shift < 0) { IndicatorRelease(h); return 0.0; }

   int closedShift = shift + 1;
   int barsAvail = Bars(sym, tf);
   if(closedShift >= barsAvail) closedShift = shift;

   double buf[];
   ArraySetAsSeries(buf, true);
   double val = 0.0;
   if(CopyBuffer(h, 0, closedShift, 1, buf) > 0 && buf[0] > 0.0 && MathIsValidNumber(buf[0]))
      val = buf[0];

   IndicatorRelease(h);
   return val;
}

void PushPt14(SPathPtV14 &arr[], datetime tm, double px, bool isBuy, double entry, double oneRPrice, bool isPost)
{
   int n = ArraySize(arr);
   ArrayResize(arr, n + 1);
   arr[n].time        = tm;
   arr[n].price       = px;
   arr[n].isPostClose = isPost;
   arr[n].rVal        = (oneRPrice > 0.0) ? ((isBuy ? (px - entry) : (entry - px)) / oneRPrice) : 0.0;
}

void BuildTradePathV14(STradeV14 &tr, int maxTicks, bool useTicks, ENUM_TIMEFRAMES atrTf, int atrPeriod, int postBarsM15)
{
   ArrayResize(tr.pts, 0);
   tr.peakR = -999999.0;
   tr.troughR = 999999.0;
   tr.postPeakR = -999999.0;
   tr.postExtraR = 0.0;
   tr.fullWavePeakR = -999999.0;
   tr.slVsATR_X = 0.0;
   tr.peakRangeATR = 0.0;
   tr.spreadPtsAtOpen = 0.0;
   tr.spreadVsSL_Pct = 0.0;
   tr.highSpreadLoss = false;
   tr.premature1RMiss = false;
   tr.srcType = "M1";

   int postSec = MathMax(1, postBarsM15) * 900; // Moi nen M15 = 900s
   tr.tPostEndSrv = (datetime)((long)tr.tCloseSrv + postSec);

   tr.atrClosedAtOpen = GetHistoricalClosedATR(tr.sym, atrTf, tr.tOpenSrv, atrPeriod);
   if(tr.atrClosedAtOpen > 0.0 && tr.oneR_Price > 0.0)
      tr.slVsATR_X = tr.oneR_Price / tr.atrClosedAtOpen;

   double ptSize = SymbolInfoDouble(tr.sym, SYMBOL_POINT);
   double spreadPrice = 0.0;

   // 1. Duong di gia TRONG luc giu lenh [tOpenSrv, tCloseSrv]
   bool gotTicks = false;
   if(useTicks && tr.tCloseSrv >= tr.tOpenSrv)
   {
      MqlTick ticks[];
      ulong fromMs = (ulong)tr.tOpenSrv * 1000ULL;
      ulong toMs   = (ulong)(tr.tCloseSrv + 1) * 1000ULL;
      int copied = CopyTicksRange(tr.sym, ticks, COPY_TICKS_ALL, fromMs, toMs);
      if(copied > 1)
      {
         gotTicks = true;
         tr.srcType = "TICK";
         for(int i = 0; i < copied; i++)
         {
            if(ticks[i].ask > 0.0 && ticks[i].bid > 0.0 && ticks[i].ask >= ticks[i].bid)
            {
               spreadPrice = ticks[i].ask - ticks[i].bid;
               break;
            }
         }
         int step = MathMax(1, copied / MathMax(100, maxTicks));
         PushPt14(tr.pts, tr.tOpenSrv, tr.entry, tr.isBuy, tr.entry, tr.oneR_Price, false);
         for(int i = 0; i < copied; i += step)
         {
            double px = tr.isBuy ? ticks[i].bid : ticks[i].ask;
            if(px <= 0.0) px = ticks[i].last;
            if(px > 0.0)
               PushPt14(tr.pts, ticks[i].time, px, tr.isBuy, tr.entry, tr.oneR_Price, false);
         }
         PushPt14(tr.pts, tr.tCloseSrv, tr.closePrice, tr.isBuy, tr.entry, tr.oneR_Price, false);
      }
   }

   if(spreadPrice <= 0.0 && ptSize > 0.0)
   {
      long spPts = SymbolInfoInteger(tr.sym, SYMBOL_SPREAD);
      if(spPts > 0) spreadPrice = (double)spPts * ptSize;
   }
   if(ptSize > 0.0 && spreadPrice > 0.0) tr.spreadPtsAtOpen = spreadPrice / ptSize;
   if(tr.oneR_Price > 0.0 && spreadPrice > 0.0) tr.spreadVsSL_Pct = (spreadPrice / tr.oneR_Price) * 100.0;

   if(!gotTicks)
   {
      ENUM_TIMEFRAMES tf = PERIOD_M1;
      int sStart = iBarShift(tr.sym, tf, tr.tOpenSrv, false);
      int sEnd   = iBarShift(tr.sym, tf, tr.tCloseSrv, false);
      if(sStart < 0) sStart = 0;
      if(sEnd < 0) sEnd = 0;
      if(sStart < sEnd) sStart = sEnd;

      PushPt14(tr.pts, tr.tOpenSrv, tr.entry, tr.isBuy, tr.entry, tr.oneR_Price, false);
      for(int b = sStart; b >= sEnd; b--)
      {
         datetime bt = iTime(tr.sym, tf, b);
         double op = iOpen(tr.sym, tf, b), hi = iHigh(tr.sym, tf, b);
         double lo = iLow(tr.sym, tf, b), cl = iClose(tr.sym, tf, b);
         if(op <= 0.0 || hi <= 0.0 || lo <= 0.0 || cl <= 0.0) continue;

         double p1 = (b == sStart) ? tr.entry : op;
         double p2 = (cl >= op) ? MathMin(p1, lo) : MathMax(p1, hi);
         double p3 = (cl >= op) ? MathMax(p2, hi) : MathMin(p2, lo);
         double p4 = (b == sEnd) ? tr.closePrice : cl;

         if(b != sStart) PushPt14(tr.pts, bt, p1, tr.isBuy, tr.entry, tr.oneR_Price, false);
         PushPt14(tr.pts, bt, p2, tr.isBuy, tr.entry, tr.oneR_Price, false);
         PushPt14(tr.pts, bt, p3, tr.isBuy, tr.entry, tr.oneR_Price, false);
         PushPt14(tr.pts, (b == sEnd ? tr.tCloseSrv : bt), p4, tr.isBuy, tr.entry, tr.oneR_Price, false);
      }
   }

   // 2. Duong di gia SAU KHI CHOT LENH (tCloseSrv -> tPostEndSrv) tren nen M15
   int pStart = iBarShift(tr.sym, PERIOD_M15, tr.tCloseSrv, false);
   int pEnd   = iBarShift(tr.sym, PERIOD_M15, tr.tPostEndSrv, false);
   if(pStart >= 0)
   {
      if(pEnd < 0) pEnd = 0;
      for(int b = pStart; b >= pEnd; b--)
      {
         datetime bt = iTime(tr.sym, PERIOD_M15, b);
         double hi = iHigh(tr.sym, PERIOD_M15, b);
         double lo = iLow(tr.sym, PERIOD_M15, b);
         double cl = iClose(tr.sym, PERIOD_M15, b);
         if(hi <= 0.0 || lo <= 0.0 || cl <= 0.0) continue;
         double favPx = tr.isBuy ? hi : lo;
         PushPt14(tr.pts, bt, favPx, tr.isBuy, tr.entry, tr.oneR_Price, true);
         PushPt14(tr.pts, bt, cl,    tr.isBuy, tr.entry, tr.oneR_Price, true);
      }
   }

   double minPx = 999999999.0, maxPx = -999999999.0;
   for(int k = 0; k < ArraySize(tr.pts); k++)
   {
      double px = tr.pts[k].price;
      if(px < minPx) minPx = px;
      if(px > maxPx) maxPx = px;

      if(tr.oneR_Price > 0.0)
      {
         double rv = tr.pts[k].rVal;
         if(rv > tr.fullWavePeakR) tr.fullWavePeakR = rv;
         if(!tr.pts[k].isPostClose)
         {
            if(rv > tr.peakR) tr.peakR = rv;
            if(rv < tr.troughR) tr.troughR = rv;
         }
         else
         {
            if(rv > tr.postPeakR) tr.postPeakR = rv;
         }
      }
   }

   if(tr.oneR_Price <= 0.0 || tr.peakR < -900000.0) tr.peakR = tr.realizedR;
   if(tr.oneR_Price <= 0.0 || tr.troughR > 900000.0) tr.troughR = tr.realizedR;
   if(tr.postPeakR < -900000.0) tr.postPeakR = MathMax(tr.peakR, tr.realizedR);
   if(tr.fullWavePeakR < -900000.0) tr.fullWavePeakR = MathMax(tr.peakR, tr.postPeakR);

   double anchorR = MathMax(tr.peakR, tr.realizedR);
   tr.postExtraR = MathMax(0.0, tr.postPeakR - anchorR);

   if(tr.atrClosedAtOpen > 0.0 && maxPx >= minPx) tr.peakRangeATR = (maxPx - minPx) / tr.atrClosedAtOpen;
   if(tr.realizedR < -0.15 && tr.spreadVsSL_Pct >= InpWarnSpreadSL_Pct) tr.highSpreadLoss = true;
   if(tr.realizedR >= 0.80 && tr.realizedR <= 1.15 && tr.postExtraR >= 1.0) tr.premature1RMiss = true;
}

double SimFullWaveTP_V14(const STradeV14 &tr, double tpR)
{
   // Dung fullWavePeakR de mo phong TP tren toan bo con song (ca truoc va sau khi chot)
   if(tr.fullWavePeakR >= tpR) return tpR;
   return tr.realizedR;
}

double SimTrailAndBE_V14(const STradeV14 &tr, double beTrigR, double trigR, double lockR)
{
   bool beArmed = false;
   bool trailArmed = false;
   double dynLock = lockR;

   for(int k = 0; k < ArraySize(tr.pts); k++)
   {
      if(tr.pts[k].isPostClose) break; // Chi mo phong trailing trong pham vi lenh con mo
      double rv = tr.pts[k].rVal;
      if(!beArmed && beTrigR > 0.0 && rv >= beTrigR) beArmed = true;
      if(!trailArmed && trigR > 0.0 && rv >= trigR)
      {
         trailArmed = true;
         dynLock = lockR;
      }
      else if(trailArmed && rv > trigR)
      {
         double candLock = lockR + (rv - trigR) * 0.5;
         if(candLock > dynLock) dynLock = candLock;
      }
      if(trailArmed && rv <= dynLock) return dynLock;
      if(beArmed && !trailArmed && rv <= 0.05) return 0.05;
   }

   if(trailArmed) return MathMax(dynLock, tr.realizedR);
   if(beArmed) return MathMax(0.05, tr.realizedR);
   return tr.realizedR;
}

void OnStart()
{
   long srvMinusGmt7Sec = CalcServerMinusGMT7Sec();
   datetime fromGmt7 = 0, toGmt7 = 0;
   ResolveGMT7Range(srvMinusGmt7Sec, fromGmt7, toGmt7);
   if(toGmt7 < fromGmt7) { Print("[V14] Loi moc thoi gian."); return; }

   datetime fromSrv = GMT7ToServer(fromGmt7, srvMinusGmt7Sec);
   datetime toSrv   = GMT7ToServer(toGmt7, srvMinusGmt7Sec);

   if(!HistorySelect(fromSrv, toSrv))
   {
      Print("[V14] Khong tai duoc lich su. Err=", GetLastError());
      return;
   }

   STradeV14 trades[];
   int n = 0, cntTick = 0, cntM1 = 0;
   int totalDeals = HistoryDealsTotal();

   for(int i = 0; i < totalDeals; i++)
   {
      ulong dOut = HistoryDealGetTicket(i);
      if(dOut == 0) continue;
      long eType = HistoryDealGetInteger(dOut, DEAL_ENTRY);
      if(eType != DEAL_ENTRY_OUT && eType != DEAL_ENTRY_OUT_BY) continue;
      if(InpMagicFilter >= 0 && HistoryDealGetInteger(dOut, DEAL_MAGIC) != InpMagicFilter) continue;
      string sym = HistoryDealGetString(dOut, DEAL_SYMBOL);
      if(InpSymbolFilter != "" && sym != InpSymbolFilter) continue;

      ulong posId = (ulong)HistoryDealGetInteger(dOut, DEAL_POSITION_ID);
      if(posId == 0) continue;

      datetime tOpenSrv = 0;
      double entry = 0.0;
      bool isBuy = true;
      for(int j = i - 1; j >= 0; j--)
      {
         ulong dIn = HistoryDealGetTicket(j);
         if(dIn == 0) continue;
         if((ulong)HistoryDealGetInteger(dIn, DEAL_POSITION_ID) == posId &&
            HistoryDealGetInteger(dIn, DEAL_ENTRY) == DEAL_ENTRY_IN)
         {
            tOpenSrv = (datetime)HistoryDealGetInteger(dIn, DEAL_TIME);
            entry = HistoryDealGetDouble(dIn, DEAL_PRICE);
            isBuy = (HistoryDealGetInteger(dIn, DEAL_TYPE) == DEAL_TYPE_BUY);
            break;
         }
      }
      if(tOpenSrv == 0 || entry <= 0.0) continue;

      double vol = HistoryDealGetDouble(dOut, DEAL_VOLUME);
      double extraComm = (Cfg_CommFallbackPerLot > 0.0) ? (-Cfg_CommFallbackPerLot * vol) : 0.0;
      double netP = HistoryDealGetDouble(dOut, DEAL_PROFIT)
                  + HistoryDealGetDouble(dOut, DEAL_SWAP)
                  + HistoryDealGetDouble(dOut, DEAL_COMMISSION)
                  + extraComm;

      string gvR = "V9R_" + IntegerToString((long)posId);
      double oneRPrice = GlobalVariableCheck(gvR) ? GlobalVariableGet(gvR) : 0.0;
      double oneRMoney = (Cfg_RiskMoney > 0.0) ? Cfg_RiskMoney : 25.0;
      if(oneRPrice > 0.0)
      {
         double ts = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
         double tv = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
         if(ts > 0.0 && tv > 0.0 && vol > 0.0) oneRMoney = (oneRPrice / ts) * tv * vol;
      }

      ArrayResize(trades, n + 1);
      trades[n].posId      = posId;
      trades[n].sym        = sym;
      trades[n].isBuy      = isBuy;
      trades[n].tOpenSrv   = tOpenSrv;
      trades[n].tCloseSrv  = (datetime)HistoryDealGetInteger(dOut, DEAL_TIME);
      trades[n].tOpenGMT7  = ServerToGMT7(tOpenSrv, srvMinusGmt7Sec);
      trades[n].tCloseGMT7 = ServerToGMT7(trades[n].tCloseSrv, srvMinusGmt7Sec);
      trades[n].entry      = entry;
      trades[n].closePrice = HistoryDealGetDouble(dOut, DEAL_PRICE);
      trades[n].lot        = vol;
      trades[n].netProfit  = netP;
      trades[n].oneR_Price = oneRPrice;
      trades[n].oneR_Money = oneRMoney;
      trades[n].realizedR  = (oneRMoney > 0.0) ? (netP / oneRMoney) : 0.0;

      BuildTradePathV14(trades[n], InpMaxTicksPerTrade, InpUseRealTicks, InpATR_TF, Cfg_ATR_Period, InpPostExitBarsM15);
      if(trades[n].srcType == "TICK") cntTick++; else cntM1++;
      n++;
   }

   if(n == 0) { Print("[V14] Khong co lenh dong."); return; }

   if(InpExportCsv)
   {
      int hCsv = FileOpen("RSI_Div_V14_PricePath.csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
      if(hCsv != INVALID_HANDLE)
      {
         FileWrite(hCsv, "pos_id", "symbol", "side", "phase", "step", "time_gmt7", "price", "r_val", "peak_r", "post_peak_r", "post_extra_r", "full_wave_peak_r", "realized_r", "sl_atr", "range_atr", "spread_sl_pct");
         for(int i = 0; i < n; i++)
         {
            int dig = (int)SymbolInfoInteger(trades[i].sym, SYMBOL_DIGITS);
            for(int k = 0; k < ArraySize(trades[i].pts); k++)
            {
               FileWrite(
                  hCsv,
                  IntegerToString((long)trades[i].posId),
                  trades[i].sym,
                  trades[i].isBuy ? "BUY" : "SELL",
                  trades[i].pts[k].isPostClose ? "POST_CLOSE" : "IN_TRADE",
                  IntegerToString(k),
                  TimeToString(ServerToGMT7(trades[i].pts[k].time, srvMinusGmt7Sec), TIME_DATE|TIME_MINUTES|TIME_SECONDS),
                  DoubleToString(trades[i].pts[k].price, dig),
                  DoubleToString(trades[i].pts[k].rVal, 2),
                  DoubleToString(trades[i].peakR, 2),
                  DoubleToString(trades[i].postPeakR, 2),
                  DoubleToString(trades[i].postExtraR, 2),
                  DoubleToString(trades[i].fullWavePeakR, 2),
                  DoubleToString(trades[i].realizedR, 2),
                  DoubleToString(trades[i].slVsATR_X, 2),
                  DoubleToString(trades[i].peakRangeATR, 2),
                  DoubleToString(trades[i].spreadVsSL_Pct, 1)
               );
            }
         }
         FileClose(hCsv);
      }
   }

   int winCnt = 0, lossCnt = 0, beCnt = 0, leak1R = 0, leak15R = 0, passRangeCnt = 0, near1RCnt = 0, premMissCnt = 0, hiSpreadLossCnt = 0;
   double sumRealR = 0.0, sumWinR = 0.0, sumPeakR = 0.0, sumFullWavePeakR = 0.0, sumPostExtraR = 0.0, sumAdvR = 0.0;
   double sumWinRangeATR = 0.0, sumWinSlATR = 0.0;
   double sumLossRangeATR = 0.0, sumLossSlATR = 0.0, sumLossTroughR = 0.0, sumLossSpreadPct = 0.0;
   double maxLossSpreadPts = 0.0, gWin = 0.0, gLoss = 0.0, netP = 0.0;

   double fullPeaks[];
   ArrayResize(fullPeaks, n);
   string waveDetails = "";

   for(int i = 0; i < n; i++)
   {
      double rr = trades[i].realizedR;
      double pr = trades[i].netProfit;
      fullPeaks[i] = trades[i].fullWavePeakR;

      netP += pr;
      sumRealR += rr;
      sumPeakR += trades[i].peakR;
      sumFullWavePeakR += trades[i].fullWavePeakR;
      sumPostExtraR += trades[i].postExtraR;
      sumAdvR += MathAbs(MathMin(0.0, trades[i].troughR));
      if(trades[i].peakRangeATR >= RejectMinRangeATR) passRangeCnt++;
      if(trades[i].premature1RMiss) premMissCnt++;

      if(rr > 0.15)
      {
         winCnt++; gWin += pr; sumWinR += rr;
         sumWinRangeATR += trades[i].peakRangeATR;
         sumWinSlATR += trades[i].slVsATR_X;
      }
      else if(rr < -0.15)
      {
         lossCnt++; gLoss += MathAbs(pr);
         sumLossRangeATR += trades[i].peakRangeATR;
         sumLossSlATR += trades[i].slVsATR_X;
         sumLossTroughR += trades[i].troughR;
         sumLossSpreadPct += trades[i].spreadVsSL_Pct;
         if(trades[i].highSpreadLoss) hiSpreadLossCnt++;
         if(trades[i].spreadPtsAtOpen > maxLossSpreadPts) maxLossSpreadPts = trades[i].spreadPtsAtOpen;
      }
      else { beCnt++; if(pr >= 0) gWin += pr; else gLoss += MathAbs(pr); }

      if(rr >= 0.80 && rr <= 1.15) near1RCnt++;
      if(trades[i].peakR >= 1.0 && rr < 0.30) leak1R++;
      if(trades[i].peakR >= 1.5 && rr < 0.50) leak15R++;

      waveDetails += StringFormat("  * #%I64u %s (%s): Thuc=%+.2fR | DinhLucGiu=%.2fR | DinhSauChot=%.2fR (+%.2fR) | DinhSong=%.2fR\n",
         trades[i].posId, trades[i].sym, trades[i].isBuy ? "BUY" : "SELL",
         rr, trades[i].peakR, trades[i].postPeakR, trades[i].postExtraR, trades[i].fullWavePeakR);
   }

   for(int i = 0; i < n - 1; i++)
      for(int j = i + 1; j < n; j++)
         if(fullPeaks[i] > fullPeaks[j]) { double t = fullPeaks[i]; fullPeaks[i] = fullPeaks[j]; fullPeaks[j] = t; }

   double medFullPeak = fullPeaks[n / 2];
   double p75FullPeak = fullPeaks[(int)MathMin(n - 1, MathFloor(n * 0.75))];
   double avgPeak     = sumPeakR / n;
   double avgFullPeak = sumFullWavePeakR / n;
   double avgPostExt  = sumPostExtraR / n;
   double avgAdv      = sumAdvR / n;
   double avgWinR     = (winCnt > 0) ? (sumWinR / winCnt) : 0.0;
   double avgRealR    = sumRealR / n;
   double pf          = (gLoss > 0.0) ? (gWin / gLoss) : (gWin > 0.0 ? 99.0 : 0.0);

   double avgWinRngATR  = (winCnt > 0) ? (sumWinRangeATR / winCnt) : 0.0;
   double avgWinSlATR   = (winCnt > 0) ? (sumWinSlATR / winCnt) : 0.0;
   double avgLossRngATR = (lossCnt > 0) ? (sumLossRangeATR / lossCnt) : 0.0;
   double avgLossSlATR  = (lossCnt > 0) ? (sumLossSlATR / lossCnt) : 0.0;
   double avgLossTrough = (lossCnt > 0) ? (sumLossTroughR / lossCnt) : 0.0;
   double avgLossSpPct  = (lossCnt > 0) ? (sumLossSpreadPct / lossCnt) : 0.0;

   // Mo phong TP tren TOAN BO CON SONG (co ca gia chay tiep sau khi chot)
   double tpCands[]   = {1.2, 1.5, 1.8, 2.0, 2.5, 3.0, 3.5};
   double trigCands[] = {0.8, 1.0, 1.2, 1.5, 1.8};
   double lockCands[] = {0.3, 0.5, 0.7, 0.9, 1.1, 1.4};
   double beCands[]   = {0.5, 0.7, 0.8, 1.0, 1.2};

   double curTPSumR = 0.0, bestTPSumR = -999999.0, bestTP = Cfg_TargetRR;
   for(int i = 0; i < n; i++) curTPSumR += SimFullWaveTP_V14(trades[i], Cfg_TargetRR);
   bestTPSumR = curTPSumR;
   for(int c = 0; c < ArraySize(tpCands); c++)
   {
      double s = 0.0;
      for(int i = 0; i < n; i++) s += SimFullWaveTP_V14(trades[i], tpCands[c]);
      if(s > bestTPSumR) { bestTPSumR = s; bestTP = tpCands[c]; }
   }

   double curTrailSumR = 0.0, bestTrailSumR = -999999.0;
   double bestTrig = Cfg_Trail_TriggerRR, bestLock = Cfg_Trail_LockRR, bestBE = Cfg_BE_TriggerRR;
   for(int i = 0; i < n; i++) curTrailSumR += SimTrailAndBE_V14(trades[i], Cfg_BE_TriggerRR, Cfg_Trail_TriggerRR, Cfg_Trail_LockRR);
   bestTrailSumR = curTrailSumR;

   for(int bi = 0; bi < ArraySize(beCands); bi++)
   {
      double bR = beCands[bi];
      for(int ti = 0; ti < ArraySize(trigCands); ti++)
      {
         double trR = trigCands[ti];
         for(int li = 0; li < ArraySize(lockCands); li++)
         {
            double lkR = lockCands[li];
            if(lkR >= trR - 0.1) continue;
            double s = 0.0;
            for(int i = 0; i < n; i++) s += SimTrailAndBE_V14(trades[i], bR, trR, lkR);
            if(s > bestTrailSumR)
            {
               bestTrailSumR = s;
               bestBE = bR;
               bestTrig = trR;
               bestLock = lkR;
            }
         }
      }
   }

   double baseRngRef = (lossCnt > 0 && winCnt > 0)
      ? MathMin(avgWinRngATR, MathMax(avgLossRngATR, RejectMinRangeATR))
      : ((winCnt > 0) ? avgWinRngATR : avgLossRngATR);
   double propMinRng = Round14(Clamp14(baseRngRef * 0.35, 0.25, 0.85), 0.05);
   int propSpreadBufPts = (hiSpreadLossCnt > 0) ? (int)MathMax(Cfg_SpreadBufferPts, MathCeil(maxLossSpreadPts * 0.5)) : Cfg_SpreadBufferPts;
   double propBreak = Round14(Clamp14(RejectMinBreakATR + ((lossCnt > 0 && avgLossSlATR < 0.5) ? 0.02 : 0.0), 0.02, 0.30), 0.01);
   double propBack  = Round14(Clamp14(RejectMinCloseBackATR + ((lossCnt > 0 && avgLossSpPct > InpWarnSpreadSL_Pct) ? 0.02 : 0.0), 0.01, 0.20), 0.01);

   double score = Clamp14((pf - 0.8) / 0.8, 0.0, 1.0) * 35.0
                + Clamp14((double)winCnt / n / 0.55, 0.0, 1.0) * 25.0
                + Clamp14(1.0 - ((double)leak15R / n * 2.5), 0.0, 1.0) * 25.0
                + Clamp14(1.0 - (avgAdv / 2.0), 0.0, 1.0) * 15.0;

   string outTxt = StringFormat(
      "================ V14 FULL-WAVE: IN-TRADE + POST-EXIT ================\n"
      "GMT+7 : %s -> %s (Post-Exit: +%d nen M15)\n"
      "Lenh  : %d (TICK:%d / M1:%d) | W:%d / BE:%d / L:%d | Net: %.2f$\n"
      "PF: %.2f | AvgR: %+.2fR | AvgWinR: %.2fR | Chot ~1R: %d (Lo >=+1R sau chot: %d)\n"
      "Dinh Luc Giu: Avg=%.2fR | Dinh Ca Song: Avg=%.2fR, Med=%.2fR, P75=%.2fR\n"
      "Gia Chay Them Sau Chot (Avg postExtraR): +%.2fR | Score: %.1f/100\n"
      "---------------------------------------------------------------------\n"
      "CHI TIET TUNG LENH (TRUOC & SAU KHI CHOT):\n%s"
      "---------------------------------------------------------------------\n"
      "ATR THANG (%d): SL/ATR=%.2fx, Rng/ATR=%.2fx | THUA (%d): Trough=%.2fR, SL/ATR=%.2fx, Rng/ATR=%.2fx, Sp/SL=%.1f%%\n"
      "TOI UU TP (THEO TOAN BO CON SONG): %.2fR (%+.2fR) => %.2fR (%+.2fR | %+.2fR)\n"
      "TOI UU BE/TRAIL (TRONG LUC GIU): %.2f/%.2f/%.2f (%+.2fR) => %.2f/%.2f/%.2f (%+.2fR | %+.2fR)\n"
      "DE XUAT: SpreadBuf=%d->%d | MinRangeATR=%.2f->%.2f | BreakATR=%.2f->%.2f | CloseBackATR=%.2f->%.2f\n"
      "=====================================================================\n",
      TimeToString(fromGmt7, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
      TimeToString(toGmt7, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
      InpPostExitBarsM15,
      n, cntTick, cntM1, winCnt, beCnt, lossCnt, netP,
      pf, avgRealR, avgWinR, near1RCnt, premMissCnt,
      avgPeak, avgFullPeak, medFullPeak, p75FullPeak,
      avgPostExt, score,
      waveDetails,
      winCnt, avgWinSlATR, avgWinRngATR, lossCnt, avgLossTrough, avgLossSlATR, avgLossRngATR, avgLossSpPct,
      Cfg_TargetRR, curTPSumR, bestTP, bestTPSumR, (bestTPSumR - curTPSumR),
      Cfg_BE_TriggerRR, Cfg_Trail_TriggerRR, Cfg_Trail_LockRR, curTrailSumR,
      bestBE, bestTrig, bestLock, bestTrailSumR, (bestTrailSumR - curTrailSumR),
      Cfg_SpreadBufferPts, propSpreadBufPts,
      RejectMinRangeATR, propMinRng,
      RejectMinBreakATR, propBreak,
      RejectMinCloseBackATR, propBack
   );

   Print(outTxt);

   if(InpExportSummary)
   {
      int h = FileOpen("RSI_Div_V14_Summary.txt", FILE_WRITE | FILE_TXT | FILE_ANSI);
      if(h != INVALID_HANDLE) { FileWriteString(h, outTxt); FileClose(h); }
   }
}
