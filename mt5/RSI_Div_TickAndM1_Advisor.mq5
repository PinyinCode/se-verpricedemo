//+------------------------------------------------------------------+
//|                               RSI_Div_TickAndM1_Advisor_v10.mq5  |
//|   Gop hoan chinh ban dau:                                        |
//|   - Mac dinh doc hom nay GMT+7 -> Tu quy doi ServerTime          |
//|   - Duong di gia thuc te (CopyTicksRange -> M1) + ATR nen dong   |
//|   - Day du thong so Reject + Tu tim TP & Trailing toi uu         |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property version   "10.00"
#property script_show_inputs

input group "=== Khoang thoi gian (Mac dinh 0 = Hom nay GMT+7) ==="
input datetime        InpFromDate_GMT7      = 0;                  // Tu ngay GMT+7 (0 = 00:00 hom nay GMT+7)
input datetime        InpToDate_GMT7        = 0;                  // Den ngay GMT+7 (0 = 23:59:59 hom nay GMT+7)
input int             InpManualServerOffHr  = 0;                  // Offset Server so voi GMT+7 neu can ep (0 = tu dong)

input group "=== Bo loc lenh & Duong di gia ==="
input long            InpMagicFilter        = 20240915;           // Magic EA (TRADE_V9 = 20240915, -1 = tat ca)
input string          InpSymbolFilter       = "";                 // Loc Symbol (de trong = tat ca)
input bool            InpUseRealTicks       = true;               // Uu tien du lieu tick (CopyTicksRange) -> M1
input int             InpMaxTicksPerTrade   = 20000;              // Gioi han tick/lenh
input bool            InpExportCsv          = true;               // Xuat duong di gia chi tiet ra CSV
input bool            InpExportSummary      = true;               // Xuat bao cao tong hop ra TXT

input group "=== Cai dat RR, Trailing & ATR ==="
input double          Cfg_RiskMoney         = 25.0;               // risk_money ($)
input double          Cfg_TargetRR          = 2.0;                // reward_risk_ratio
input double          Cfg_BE_TriggerRR      = 0.5;                // breakeven_trigger_rr
input double          Cfg_Trail_TriggerRR   = 1.0;                // trailing_trigger_rr
input double          Cfg_Trail_LockRR      = 0.5;                // trailing_lock_rr
input double          Cfg_VolumeAutoRatio   = 1.2;                // VolumeAutoRatio
input ENUM_TIMEFRAMES InpATR_TF             = PERIOD_M15;         // Khung thoi gian doc ATR lich su
input int             Cfg_ATR_Period        = 14;                 // Chu ky ATR (x ATR)

input group "=== Thong so Reject (Doi chieu Indicator) ==="
input bool            RejectOnCandleClose   = false;              // true: cho nen dong; false: xet ngay tren nen dang chay
input int             RejectMaxBarsAfter    = 1;                  // So nen toi da sau nen cuc tri duoc phep xac nhan
input double          RejectMinWickRatio    = 0.5;                // Gia dong phai lui it nhat ti le nay cua bien do (0-1)
input double          RejectMinRangeATR     = 0.3;                // Bien do toi thieu cua cu reject (x ATR)
input double          RejectMinBreakATR     = 0.05;               // Regular: do quet toi thieu qua dinh/day cu (x ATR)
input double          RejectMinCloseBackATR = 0.02;               // Regular: dong lai ben trong muc cu toi thieu (x ATR)
input bool            RejectRequireCloseDiv = false;              // Bat buoc them dieu kien gia dong nen nhu ban cu

struct SPathPtV10
{
   datetime time;
   double   price;
   double   rVal;
};

struct STradeV10
{
   ulong       posId;
   string      sym;
   bool        isBuy;
   datetime    tOpenSrv;
   datetime    tCloseSrv;
   datetime    tOpenGMT7;
   datetime    tCloseGMT7;
   double      entry;
   double      closePrice;
   double      lot;
   double      netProfit;
   double      oneR_Price;
   double      oneR_Money;
   double      realizedR;
   double      peakR;
   double      troughR;
   double      atrClosedAtOpen;
   double      peakRangeATR;
   bool        passedRangeATR;
   string      srcType;
   SPathPtV10  pts[];
};

double Clamp10(double v, double lo, double hi) { return v < lo ? lo : (v > hi ? hi : v); }
double Round10(double v, double st) { return st > 0.0 ? MathRound(v / st) * st : v; }

long CalcServerMinusGMT7Sec()
{
   if(InpManualServerOffHr != 0)
      return (long)InpManualServerOffHr * 3600LL;

   datetime gt = TimeGMT();
   datetime st = TimeTradeServer();
   if(st <= 0) st = TimeCurrent();

   if(gt > 0 && st > 0)
   {
      long srvMinusGmt = (long)st - (long)gt;
      long roundedSrvMinusGmt = (long)MathRound((double)srvMinusGmt / 3600.0) * 3600LL;
      return roundedSrvMinusGmt - 7LL * 3600LL;
   }
   return 0;
}

datetime GetCurrentGMT7Now(long srvMinusGmt7Sec)
{
   datetime gt = TimeGMT();
   if(gt > 0)
      return (datetime)((long)gt + 7LL * 3600LL);

   datetime st = TimeTradeServer();
   if(st <= 0) st = TimeCurrent();
   return (datetime)((long)st - srvMinusGmt7Sec);
}

void ResolveGMT7Range(long srvMinusGmt7Sec, datetime &outFromGmt7, datetime &outToGmt7)
{
   datetime nowGmt7 = GetCurrentGMT7Now(srvMinusGmt7Sec);
   long dayStartSec = ((long)nowGmt7 / 86400LL) * 86400LL;
   datetime todayStart = (datetime)dayStartSec;
   datetime todayEnd   = (datetime)(dayStartSec + 86399LL);

   outFromGmt7 = (InpFromDate_GMT7 > 0) ? InpFromDate_GMT7 : todayStart;
   outToGmt7   = (InpToDate_GMT7   > 0) ? InpToDate_GMT7   : todayEnd;
}

datetime GMT7ToServer(datetime gmt7Dt, long srvMinusGmt7Sec)
{
   return (datetime)((long)gmt7Dt + srvMinusGmt7Sec);
}

datetime ServerToGMT7(datetime srvDt, long srvMinusGmt7Sec)
{
   return (datetime)((long)srvDt - srvMinusGmt7Sec);
}

double GetHistoricalClosedATR(string sym, ENUM_TIMEFRAMES tf, datetime srvTime, int atrPeriod)
{
   int p = MathMax(1, atrPeriod);
   int h = iATR(sym, tf, p);
   if(h == INVALID_HANDLE) return 0.0;

   int shift = iBarShift(sym, tf, srvTime, false);
   if(shift < 0)
   {
      IndicatorRelease(h);
      return 0.0;
   }

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

void PushPt10(SPathPtV10 &arr[], datetime tm, double px, bool isBuy, double entry, double oneRPrice)
{
   int n = ArraySize(arr);
   ArrayResize(arr, n + 1);
   arr[n].time  = tm;
   arr[n].price = px;
   arr[n].rVal  = (oneRPrice > 0.0)
      ? ((isBuy ? (px - entry) : (entry - px)) / oneRPrice)
      : 0.0;
}

void BuildTradePathV10(STradeV10 &tr, int maxTicks, bool useTicks, ENUM_TIMEFRAMES atrTf, int atrPeriod)
{
   ArrayResize(tr.pts, 0);
   tr.peakR = -999999.0;
   tr.troughR = 999999.0;
   tr.peakRangeATR = 0.0;
   tr.passedRangeATR = true;
   tr.srcType = "M1";

   tr.atrClosedAtOpen = GetHistoricalClosedATR(tr.sym, atrTf, tr.tOpenSrv, atrPeriod);

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
         int step = MathMax(1, copied / MathMax(100, maxTicks));
         PushPt10(tr.pts, tr.tOpenSrv, tr.entry, tr.isBuy, tr.entry, tr.oneR_Price);
         for(int i = 0; i < copied; i += step)
         {
            double px = tr.isBuy ? ticks[i].bid : ticks[i].ask;
            if(px <= 0.0) px = ticks[i].last;
            if(px > 0.0)
               PushPt10(tr.pts, ticks[i].time, px, tr.isBuy, tr.entry, tr.oneR_Price);
         }
         PushPt10(tr.pts, tr.tCloseSrv, tr.closePrice, tr.isBuy, tr.entry, tr.oneR_Price);
      }
   }

   if(!gotTicks)
   {
      ENUM_TIMEFRAMES tf = PERIOD_M1;
      int sStart = iBarShift(tr.sym, tf, tr.tOpenSrv, false);
      int sEnd   = iBarShift(tr.sym, tf, tr.tCloseSrv, false);
      if(sStart < 0) sStart = 0;
      if(sEnd < 0) sEnd = 0;
      if(sStart < sEnd) sStart = sEnd;

      PushPt10(tr.pts, tr.tOpenSrv, tr.entry, tr.isBuy, tr.entry, tr.oneR_Price);
      for(int b = sStart; b >= sEnd; b--)
      {
         datetime bt = iTime(tr.sym, tf, b);
         double op = iOpen(tr.sym, tf, b);
         double hi = iHigh(tr.sym, tf, b);
         double lo = iLow(tr.sym, tf, b);
         double cl = iClose(tr.sym, tf, b);
         if(op <= 0.0 || hi <= 0.0 || lo <= 0.0 || cl <= 0.0) continue;

         double p1 = (b == sStart) ? tr.entry : op;
         double p2 = (cl >= op) ? MathMin(p1, lo) : MathMax(p1, hi);
         double p3 = (cl >= op) ? MathMax(p2, hi) : MathMin(p2, lo);
         double p4 = (b == sEnd) ? tr.closePrice : cl;

         if(b != sStart) PushPt10(tr.pts, bt, p1, tr.isBuy, tr.entry, tr.oneR_Price);
         PushPt10(tr.pts, bt, p2, tr.isBuy, tr.entry, tr.oneR_Price);
         PushPt10(tr.pts, bt, p3, tr.isBuy, tr.entry, tr.oneR_Price);
         PushPt10(tr.pts, (b == sEnd ? tr.tCloseSrv : bt), p4, tr.isBuy, tr.entry, tr.oneR_Price);
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
         if(tr.pts[k].rVal > tr.peakR) tr.peakR = tr.pts[k].rVal;
         if(tr.pts[k].rVal < tr.troughR) tr.troughR = tr.pts[k].rVal;
      }
   }

   if(tr.oneR_Price <= 0.0)
   {
      tr.peakR = tr.realizedR;
      tr.troughR = tr.realizedR;
   }
   else
   {
      if(tr.peakR < -900000.0) tr.peakR = tr.realizedR;
      if(tr.troughR > 900000.0) tr.troughR = tr.realizedR;
   }

   if(tr.atrClosedAtOpen > 0.0 && maxPx >= minPx)
   {
      tr.peakRangeATR = (maxPx - minPx) / tr.atrClosedAtOpen;
      tr.passedRangeATR = (tr.peakRangeATR >= RejectMinRangeATR);
   }
}

double SimTP_V10(const STradeV10 &tr, double tpR)
{
   if(tr.peakR >= tpR) return tpR;
   return tr.realizedR;
}

double SimTrail_V10(const STradeV10 &tr, double trigR, double lockR)
{
   bool armed = false;
   for(int k = 0; k < ArraySize(tr.pts); k++)
   {
      double rv = tr.pts[k].rVal;
      if(!armed && rv >= trigR)
         armed = true;
      else if(armed && rv <= lockR)
         return lockR;
   }
   if(armed)
      return MathMax(lockR, tr.realizedR);
   return tr.realizedR;
}

void OnStart()
{
   long srvMinusGmt7Sec = CalcServerMinusGMT7Sec();
   datetime fromGmt7 = 0, toGmt7 = 0;
   ResolveGMT7Range(srvMinusGmt7Sec, fromGmt7, toGmt7);

   if(toGmt7 < fromGmt7)
   {
      Print("[V10] Loi: Moc ket thuc phai lon hon hoac bang moc bat dau.");
      return;
   }

   datetime fromSrv = GMT7ToServer(fromGmt7, srvMinusGmt7Sec);
   datetime toSrv   = GMT7ToServer(toGmt7, srvMinusGmt7Sec);

   PrintFormat("[V10 TIME] GMT+7 [%s -> %s] => Server [%s -> %s] (Offset Srv-GMT7 = %+.1f gio)",
               TimeToString(fromGmt7, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
               TimeToString(toGmt7, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
               TimeToString(fromSrv, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
               TimeToString(toSrv, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
               (double)srvMinusGmt7Sec / 3600.0);

   if(!HistorySelect(fromSrv, toSrv))
   {
      Print("[V10] Khong tai duoc lich su. Err=", GetLastError());
      return;
   }

   STradeV10 trades[];
   int n = 0;
   int cntTick = 0, cntM1 = 0;

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

      datetime tCloseSrv = (datetime)HistoryDealGetInteger(dOut, DEAL_TIME);
      double netP = HistoryDealGetDouble(dOut, DEAL_PROFIT)
                  + HistoryDealGetDouble(dOut, DEAL_SWAP)
                  + HistoryDealGetDouble(dOut, DEAL_COMMISSION);
      double vol = HistoryDealGetDouble(dOut, DEAL_VOLUME);

      string gvR = "V9R_" + IntegerToString((long)posId);
      double oneRPrice = GlobalVariableCheck(gvR) ? GlobalVariableGet(gvR) : 0.0;
      double oneRMoney = (Cfg_RiskMoney > 0.0) ? Cfg_RiskMoney : 25.0;
      if(oneRPrice > 0.0)
      {
         double ts = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
         double tv = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
         if(ts > 0.0 && tv > 0.0 && vol > 0.0)
            oneRMoney = (oneRPrice / ts) * tv * vol;
      }

      ArrayResize(trades, n + 1);
      trades[n].posId      = posId;
      trades[n].sym        = sym;
      trades[n].isBuy      = isBuy;
      trades[n].tOpenSrv   = tOpenSrv;
      trades[n].tCloseSrv  = tCloseSrv;
      trades[n].tOpenGMT7  = ServerToGMT7(tOpenSrv, srvMinusGmt7Sec);
      trades[n].tCloseGMT7 = ServerToGMT7(tCloseSrv, srvMinusGmt7Sec);
      trades[n].entry      = entry;
      trades[n].closePrice = HistoryDealGetDouble(dOut, DEAL_PRICE);
      trades[n].lot        = vol;
      trades[n].netProfit  = netP;
      trades[n].oneR_Price = oneRPrice;
      trades[n].oneR_Money = oneRMoney;
      trades[n].realizedR  = (oneRMoney > 0.0) ? (netP / oneRMoney) : 0.0;

      BuildTradePathV10(trades[n], InpMaxTicksPerTrade, InpUseRealTicks, InpATR_TF, Cfg_ATR_Period);
      if(trades[n].srcType == "TICK") cntTick++; else cntM1++;
      n++;
   }

   if(n == 0)
   {
      Print("[V10] Khong co lenh dong nao trong khoang thoi gian da chon.");
      return;
   }

   if(InpExportCsv)
   {
      int hCsv = FileOpen("RSI_Div_V10_PricePath.csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
      if(hCsv != INVALID_HANDLE)
      {
         FileWrite(hCsv, "pos_id", "symbol", "side", "src", "step", "time_gmt7", "time_server", "price", "r_val", "peak_r", "trough_r", "realized_r", "atr_closed", "range_atr");
         for(int i = 0; i < n; i++)
         {
            int dig = (int)SymbolInfoInteger(trades[i].sym, SYMBOL_DIGITS);
            for(int k = 0; k < ArraySize(trades[i].pts); k++)
            {
               datetime ptSrv = trades[i].pts[k].time;
               datetime ptG7  = ServerToGMT7(ptSrv, srvMinusGmt7Sec);
               FileWrite(
                  hCsv,
                  IntegerToString((long)trades[i].posId),
                  trades[i].sym,
                  trades[i].isBuy ? "BUY" : "SELL",
                  trades[i].srcType,
                  IntegerToString(k),
                  TimeToString(ptG7, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
                  TimeToString(ptSrv, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
                  DoubleToString(trades[i].pts[k].price, dig),
                  DoubleToString(trades[i].pts[k].rVal, 2),
                  DoubleToString(trades[i].peakR, 2),
                  DoubleToString(trades[i].troughR, 2),
                  DoubleToString(trades[i].realizedR, 2),
                  DoubleToString(trades[i].atrClosedAtOpen, dig),
                  DoubleToString(trades[i].peakRangeATR, 2)
               );
            }
         }
         FileClose(hCsv);
      }
   }

   int winCnt = 0, lossCnt = 0, beCnt = 0, leak1R = 0, leak15R = 0, passRangeCnt = 0, near1RCnt = 0;
   double sumRealR = 0.0, sumWinR = 0.0, sumPeakR = 0.0, sumAdvR = 0.0, sumRangeATR = 0.0;
   double gWin = 0.0, gLoss = 0.0, netP = 0.0;
   double peaks[];
   ArrayResize(peaks, n);

   for(int i = 0; i < n; i++)
   {
      double rr = trades[i].realizedR;
      double pr = trades[i].netProfit;
      peaks[i] = trades[i].peakR;

      netP += pr;
      sumRealR += rr;
      sumPeakR += trades[i].peakR;
      sumAdvR += MathAbs(MathMin(0.0, trades[i].troughR));
      sumRangeATR += trades[i].peakRangeATR;
      if(trades[i].passedRangeATR) passRangeCnt++;

      if(rr > 0.15) { winCnt++; gWin += pr; sumWinR += rr; }
      else if(rr < -0.15) { lossCnt++; gLoss += MathAbs(pr); }
      else { beCnt++; if(pr >= 0) gWin += pr; else gLoss += MathAbs(pr); }

      if(rr >= 0.80 && rr <= 1.15) near1RCnt++;
      if(trades[i].peakR >= 1.0 && rr < 0.30) leak1R++;
      if(trades[i].peakR >= 1.5 && rr < 0.50) leak15R++;
   }

   for(int i = 0; i < n - 1; i++)
      for(int j = i + 1; j < n; j++)
         if(peaks[i] > peaks[j]) { double t = peaks[i]; peaks[i] = peaks[j]; peaks[j] = t; }

   double medPeak = peaks[n / 2];
   double p75Peak = peaks[(int)MathMin(n - 1, MathFloor(n * 0.75))];
   double avgPeak = sumPeakR / n;
   double avgAdv  = sumAdvR / n;
   double avgWinR = (winCnt > 0) ? (sumWinR / winCnt) : 0.0;
   double avgRealR = sumRealR / n;
   double avgRangeATR = sumRangeATR / n;
   double pf = (gLoss > 0.0) ? (gWin / gLoss) : (gWin > 0.0 ? 99.0 : 0.0);

   // Mo phong tim TP toi uu va Trailing toi uu ngay trong ban V10
   double tpCands[]   = {1.0, 1.2, 1.3, 1.4, 1.5, 1.8, 2.0, 2.5, 3.0};
   double trigCands[] = {0.6, 0.8, 1.0, 1.2, 1.5};
   double lockCands[] = {0.2, 0.3, 0.5, 0.7, 0.9, 1.1};

   double curTPSumR = 0.0, bestTPSumR = -999999.0, bestTP = Cfg_TargetRR;
   for(int i = 0; i < n; i++) curTPSumR += SimTP_V10(trades[i], Cfg_TargetRR);
   bestTPSumR = curTPSumR;

   for(int c = 0; c < ArraySize(tpCands); c++)
   {
      double s = 0.0;
      for(int i = 0; i < n; i++) s += SimTP_V10(trades[i], tpCands[c]);
      if(s > bestTPSumR) { bestTPSumR = s; bestTP = tpCands[c]; }
   }

   double curTrailSumR = 0.0, bestTrailSumR = -999999.0;
   double bestTrig = Cfg_Trail_TriggerRR, bestLock = Cfg_Trail_LockRR;
   for(int i = 0; i < n; i++) curTrailSumR += SimTrail_V10(trades[i], Cfg_Trail_TriggerRR, Cfg_Trail_LockRR);
   bestTrailSumR = curTrailSumR;

   for(int ti = 0; ti < ArraySize(trigCands); ti++)
   {
      double trR = trigCands[ti];
      for(int li = 0; li < ArraySize(lockCands); li++)
      {
         double lkR = lockCands[li];
         if(lkR >= trR - 0.1) continue;
         double s = 0.0;
         for(int i = 0; i < n; i++) s += SimTrail_V10(trades[i], trR, lkR);
         if(s > bestTrailSumR)
         {
            bestTrailSumR = s;
            bestTrig = trR;
            bestLock = lkR;
         }
      }
   }

   double propBE     = ((double)leak1R / n > 0.20) ? 0.75 : (((double)beCnt / n > 0.25) ? Round10(Clamp10(Cfg_BE_TriggerRR + 0.25, 0.5, 1.5), 0.05) : Cfg_BE_TriggerRR);
   double propMinRng = Round10(Clamp10(avgRangeATR * 0.35, 0.20, 1.00), 0.05);
   double propBreak  = Round10(Clamp10(RejectMinBreakATR + ((avgAdv > 1.1 || pf < 1.0) ? 0.03 : 0.0), 0.02, 0.30), 0.01);
   double propBack   = Round10(Clamp10(RejectMinCloseBackATR + ((avgAdv > 1.1 || pf < 1.0) ? 0.02 : 0.0), 0.01, 0.20), 0.01);

   double score = Clamp10((pf - 0.8) / 0.8, 0.0, 1.0) * 35.0
                + Clamp10((double)winCnt / n / 0.55, 0.0, 1.0) * 25.0
                + Clamp10(1.0 - ((double)leak15R / n * 2.5), 0.0, 1.0) * 25.0
                + Clamp10(1.0 - (avgAdv / 2.0), 0.0, 1.0) * 15.0;

   string outTxt = StringFormat(
      "================ V10 GOP FULL: PATH + ATR + REJECT + OPT ================\n"
      "GMT+7 : %s -> %s (Server: %s -> %s)\n"
      "Lenh  : %d (TICK:%d / M1:%d) | W:%d / BE:%d / L:%d | Net: %.2f$\n"
      "PF: %.2f | AvgR: %+.2fR | AvgWinR: %.2fR | Chot ~1R (0.8-1.15R): %d lenh\n"
      "ATR TF: %s (P%d) | AvgRangeATR: %.2fx (Dat %.2fx: %d/%d) | AvgAdvR: %.2fR\n"
      "PeakR : Avg=%.2fR, Med=%.2fR, P75=%.2fR\n"
      "Leak  : >=1.0R nhung dong <0.3R (%d) | >=1.5R nhung dong <0.5R (%d)\n"
      "DIEM HIEU QUA: %.1f / 100\n"
      "-------------------------------------------------------------------------\n"
      "TOI UU TP (reward_risk_ratio):\n"
      "  Hien tai: %.2fR (%+.2fR)  =>  Toi uu: %.2fR (%+.2fR | %+.2fR)\n"
      "TOI UU TRAILING (trigger / lock):\n"
      "  Hien tai: %.2f / %.2f (%+.2fR) => Toi uu: %.2f / %.2f (%+.2fR | %+.2fR)\n"
      "-------------------------------------------------------------------------\n"
      "CON SO DE XUAT BO LOC & BE:\n"
      "  breakeven_trigger_rr : %.2f -> %.2f\n"
      "  RejectMinRangeATR    : %.2f -> %.2f\n"
      "  RejectMinBreakATR    : %.2f -> %.2f\n"
      "  RejectMinCloseBackATR: %.2f -> %.2f\n"
      "=========================================================================\n",
      TimeToString(fromGmt7, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
      TimeToString(toGmt7, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
      TimeToString(fromSrv, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
      TimeToString(toSrv, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
      n, cntTick, cntM1, winCnt, beCnt, lossCnt, netP,
      pf, avgRealR, avgWinR, near1RCnt,
      EnumToString(InpATR_TF), Cfg_ATR_Period,
      avgRangeATR, RejectMinRangeATR, passRangeCnt, n, avgAdv,
      avgPeak, medPeak, p75Peak,
      leak1R, leak15R, score,
      Cfg_TargetRR, curTPSumR, bestTP, bestTPSumR, (bestTPSumR - curTPSumR),
      Cfg_Trail_TriggerRR, Cfg_Trail_LockRR, curTrailSumR, bestTrig, bestLock, bestTrailSumR, (bestTrailSumR - curTrailSumR),
      Cfg_BE_TriggerRR, propBE,
      RejectMinRangeATR, propMinRng,
      RejectMinBreakATR, propBreak,
      RejectMinCloseBackATR, propBack
   );

   Print(outTxt);

   if(InpExportSummary)
   {
      int h = FileOpen("RSI_Div_V10_Summary.txt", FILE_WRITE | FILE_TXT | FILE_ANSI);
      if(h != INVALID_HANDLE) { FileWriteString(h, outTxt); FileClose(h); }
   }
}
