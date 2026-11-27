//+------------------------------------------------------------------+
//|                                     RSI_Div_TickAndM1_Advisor.mq5|
//|   Duong di gia thuc te: Uu tien CopyTicksRange -> Fallback M1    |
//|   Soi MFE/MAE/giat sau dinh & tinh con so RR/BE/Trailing/Bo loc |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property version   "5.00"
#property script_show_inputs

input datetime InpFromDate            = D'2026.11.01 00:00'; // Tu ngay (Server Time)
input datetime InpToDate              = D'2026.11.27 23:59'; // Den ngay (Server Time)
input long     InpMagicFilter         = 20240915;           // Magic EA (TRADE_V9 = 20240915, -1 = tat ca)
input string   InpSymbolFilter        = "";                 // Loc Symbol (de trong = tat ca)
input bool     InpUseRealTicks        = true;               // Uu tien du lieu tick thuc te (CopyTicksRange)
input int      InpMaxTicksPerTrade    = 20000;              // Gioi han tick/lenh de tranh treo terminal
input bool     InpExportCsv           = true;               // Xuat duong di gia chi tiet ra CSV
input bool     InpExportSummary       = true;               // Xuat bao cao tong hop ra TXT

// Cai dat hien tai de doi chieu
input double   Cfg_RiskMoney          = 25.0;
input double   Cfg_TargetRR           = 2.0;
input double   Cfg_BE_TriggerRR       = 0.5;
input double   Cfg_Trail_TriggerRR    = 1.0;
input double   Cfg_Trail_LockRR       = 0.5;
input double   Cfg_VolumeAutoRatio    = 1.2;
input double   Cfg_RejectMinWickRatio = 0.5;

struct SPathSample
{
   datetime time;
   double   price;
   double   rVal;
};

struct STradeEvalV5
{
   ulong       posId;
   string      sym;
   bool        isBuy;
   datetime    tOpen;
   datetime    tClose;
   double      entry;
   double      closePrice;
   double      lot;
   double      netProfit;
   double      oneR_Price;
   double      oneR_Money;
   double      realizedR;
   double      peakR;
   datetime    peakTime;
   double      troughR;
   double      postPeakDropR;
   string      sourceUsed; // "TICK" hoac "M1"
   SPathSample samples[];
};

double Clamp5(double v, double lo, double hi) { return v < lo ? lo : (v > hi ? hi : v); }
double Round5(double v, double st) { return st > 0.0 ? MathRound(v / st) * st : v; }

void AddSample(SPathSample &arr[], datetime tm, double px, bool isBuy, double entry, double oneRPrice)
{
   int n = ArraySize(arr);
   ArrayResize(arr, n + 1);
   arr[n].time  = tm;
   arr[n].price = px;
   arr[n].rVal  = (oneRPrice > 0.0)
      ? ((isBuy ? (px - entry) : (entry - px)) / oneRPrice)
      : 0.0;
}

void FinalizePathMetrics(STradeEvalV5 &tr)
{
   tr.peakR = -999999.0;
   tr.troughR = 999999.0;
   tr.peakTime = tr.tOpen;
   tr.postPeakDropR = 0.0;

   double runPeak = -999999.0;
   double maxDrop = 0.0;

   for(int i = 0; i < ArraySize(tr.samples); i++)
   {
      double rv = tr.samples[i].rVal;
      if(rv > tr.peakR)
      {
         tr.peakR = rv;
         tr.peakTime = tr.samples[i].time;
      }
      if(rv < tr.troughR)
         tr.troughR = rv;

      if(rv > runPeak)
         runPeak = rv;
      else if(runPeak > 0.0)
      {
         double drop = runPeak - rv;
         if(drop > maxDrop) maxDrop = drop;
      }
   }

   if(tr.peakR < -900000.0) tr.peakR = tr.realizedR;
   if(tr.troughR > 900000.0) tr.troughR = tr.realizedR;
   tr.postPeakDropR = maxDrop;
}

void BuildPathTickFirst(STradeEvalV5 &tr)
{
   ArrayResize(tr.samples, 0);
   tr.sourceUsed = "M1";

   if(tr.oneR_Price <= 0.0)
   {
      AddSample(tr.samples, tr.tOpen, tr.entry, tr.isBuy, tr.entry, tr.oneR_Price);
      AddSample(tr.samples, tr.tClose, tr.closePrice, tr.isBuy, tr.entry, tr.oneR_Price);
      FinalizePathMetrics(tr);
      return;
   }

   bool gotTicks = false;
   if(InpUseRealTicks && tr.tClose >= tr.tOpen)
   {
      MqlTick ticks[];
      ulong fromMs = (ulong)tr.tOpen * 1000ULL;
      ulong toMs   = (ulong)(tr.tClose + 1) * 1000ULL;
      int copied = CopyTicksRange(tr.sym, ticks, COPY_TICKS_ALL, fromMs, toMs);
      if(copied > 1)
      {
         gotTicks = true;
         tr.sourceUsed = "TICK";
         int step = MathMax(1, copied / MathMax(100, InpMaxTicksPerTrade));
         AddSample(tr.samples, tr.tOpen, tr.entry, tr.isBuy, tr.entry, tr.oneR_Price);
         for(int i = 0; i < copied; i += step)
         {
            double px = tr.isBuy ? ticks[i].bid : ticks[i].ask;
            if(px <= 0.0) px = tr.isBuy ? ticks[i].last : ticks[i].last;
            if(px > 0.0)
               AddSample(tr.samples, ticks[i].time, px, tr.isBuy, tr.entry, tr.oneR_Price);
         }
         AddSample(tr.samples, tr.tClose, tr.closePrice, tr.isBuy, tr.entry, tr.oneR_Price);
      }
   }

   if(!gotTicks)
   {
      ENUM_TIMEFRAMES tf = PERIOD_M1;
      int sStart = iBarShift(tr.sym, tf, tr.tOpen, false);
      int sEnd   = iBarShift(tr.sym, tf, tr.tClose, false);
      if(sStart < 0) sStart = 0;
      if(sEnd < 0) sEnd = 0;
      if(sStart < sEnd) sStart = sEnd;

      AddSample(tr.samples, tr.tOpen, tr.entry, tr.isBuy, tr.entry, tr.oneR_Price);
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

         if(b != sStart) AddSample(tr.samples, bt, p1, tr.isBuy, tr.entry, tr.oneR_Price);
         AddSample(tr.samples, bt, p2, tr.isBuy, tr.entry, tr.oneR_Price);
         AddSample(tr.samples, bt, p3, tr.isBuy, tr.entry, tr.oneR_Price);
         AddSample(tr.samples, (b == sEnd ? tr.tClose : bt), p4, tr.isBuy, tr.entry, tr.oneR_Price);
      }
   }

   FinalizePathMetrics(tr);
}

void OnStart()
{
   if(InpToDate <= InpFromDate)
   {
      Print("[TICK-M1] InpToDate phai lon hon InpFromDate.");
      return;
   }
   if(!HistorySelect(InpFromDate, InpToDate))
   {
      Print("[TICK-M1] Khong tai duoc lich su. Err=", GetLastError());
      return;
   }

   STradeEvalV5 trades[];
   int n = 0;
   int tickTrades = 0, m1Trades = 0;

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

      datetime tOpen = 0;
      double entry = 0.0;
      bool isBuy = true;
      for(int j = i - 1; j >= 0; j--)
      {
         ulong dIn = HistoryDealGetTicket(j);
         if(dIn == 0) continue;
         if((ulong)HistoryDealGetInteger(dIn, DEAL_POSITION_ID) == posId &&
            HistoryDealGetInteger(dIn, DEAL_ENTRY) == DEAL_ENTRY_IN)
         {
            tOpen = (datetime)HistoryDealGetInteger(dIn, DEAL_TIME);
            entry = HistoryDealGetDouble(dIn, DEAL_PRICE);
            isBuy = (HistoryDealGetInteger(dIn, DEAL_TYPE) == DEAL_TYPE_BUY);
            break;
         }
      }
      if(tOpen == 0 || entry <= 0.0) continue;

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
      trades[n].tOpen      = tOpen;
      trades[n].tClose     = (datetime)HistoryDealGetInteger(dOut, DEAL_TIME);
      trades[n].entry      = entry;
      trades[n].closePrice = HistoryDealGetDouble(dOut, DEAL_PRICE);
      trades[n].lot        = vol;
      trades[n].netProfit  = netP;
      trades[n].oneR_Price = oneRPrice;
      trades[n].oneR_Money = oneRMoney;
      trades[n].realizedR  = (oneRMoney > 0.0) ? (netP / oneRMoney) : 0.0;

      BuildPathTickFirst(trades[n]);
      if(trades[n].sourceUsed == "TICK") tickTrades++; else m1Trades++;
      n++;
   }

   if(n == 0)
   {
      Print("[TICK-M1] Khong co lenh dong trong khoang ngay da chon.");
      return;
   }

   if(InpExportCsv)
   {
      int hCsv = FileOpen("RSI_Div_TickM1_Path.csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
      if(hCsv != INVALID_HANDLE)
      {
         FileWrite(hCsv, "pos_id", "symbol", "side", "src", "idx", "time", "price", "r_val", "peak_r", "trough_r", "realized_r");
         for(int i = 0; i < n; i++)
         {
            int dig = (int)SymbolInfoInteger(trades[i].sym, SYMBOL_DIGITS);
            for(int k = 0; k < ArraySize(trades[i].samples); k++)
            {
               FileWrite(
                  hCsv,
                  IntegerToString((long)trades[i].posId),
                  trades[i].sym,
                  trades[i].isBuy ? "BUY" : "SELL",
                  trades[i].sourceUsed,
                  IntegerToString(k),
                  TimeToString(trades[i].samples[k].time, TIME_DATE|TIME_MINUTES|TIME_SECONDS),
                  DoubleToString(trades[i].samples[k].price, dig),
                  DoubleToString(trades[i].samples[k].rVal, 2),
                  DoubleToString(trades[i].peakR, 2),
                  DoubleToString(trades[i].troughR, 2),
                  DoubleToString(trades[i].realizedR, 2)
               );
            }
         }
         FileClose(hCsv);
      }
   }

   int winCnt = 0, lossCnt = 0, beCnt = 0, leak1R = 0, leak15R = 0;
   double sumRealR = 0.0, sumWinR = 0.0, sumPeakR = 0.0, sumAdvR = 0.0;
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

      if(rr > 0.15) { winCnt++; gWin += pr; sumWinR += rr; }
      else if(rr < -0.15) { lossCnt++; gLoss += MathAbs(pr); }
      else { beCnt++; if(pr >= 0) gWin += pr; else gLoss += MathAbs(pr); }

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
   double pf = (gLoss > 0.0) ? (gWin / gLoss) : (gWin > 0.0 ? 99.0 : 0.0);

   double propRR = (winCnt >= 2) ? Round5(Clamp5(MathMax(avgWinR * 1.15, p75Peak * 0.85), 1.2, 3.0), 0.1) : Cfg_TargetRR;
   double propBE = ((double)leak1R / n > 0.20) ? 0.75 : (((double)beCnt / n > 0.25) ? Round5(Clamp5(Cfg_BE_TriggerRR + 0.25, 0.5, 1.5), 0.05) : Cfg_BE_TriggerRR);
   double propTrTrig = (winCnt >= 2) ? Round5(Clamp5(MathMin(Cfg_Trail_TriggerRR, MathMax(0.8, avgPeak * 0.55)), 0.7, 2.0), 0.05) : Cfg_Trail_TriggerRR;
   double propTrLock = ((double)leak15R / n > 0.15) ? Round5(Clamp5(MathMax(Cfg_Trail_LockRR + 0.20, 0.65), 0.3, propTrTrig - 0.15), 0.05) : Cfg_Trail_LockRR;
   double propVol = (avgAdv > 1.1 || pf < 1.0) ? Round5(Clamp5(Cfg_VolumeAutoRatio + 0.20, 1.0, 2.5), 0.05) : Cfg_VolumeAutoRatio;
   double propWick = (avgAdv > 1.1 || pf < 1.0) ? Round5(Clamp5(Cfg_RejectMinWickRatio + 0.08, 0.4, 0.8), 0.02) : Cfg_RejectMinWickRatio;

   double score = Clamp5((pf - 0.8) / 0.8, 0.0, 1.0) * 35.0
                + Clamp5((double)winCnt / n / 0.55, 0.0, 1.0) * 25.0
                + Clamp5(1.0 - ((double)leak15R / n * 2.5), 0.0, 1.0) * 25.0
                + Clamp5(1.0 - (avgAdv / 2.0), 0.0, 1.0) * 15.0;

   string outTxt = StringFormat(
      "================ TICK + M1 PRICE PATH SCORER ================\n"
      "Ky: %s -> %s | Lenh: %d (TICK: %d, M1 fallback: %d)\n"
      "W:%d / BE:%d / L:%d | Net: %.2f$ | PF: %.2f | AvgR: %+.2fR | AvgWinR: %.2fR\n"
      "PeakR: Avg=%.2fR, Med=%.2fR, P75=%.2fR | AvgAdverseR: %.2fR\n"
      "That thoat: >=1.0R nhung dong <0.3R (%d) | >=1.5R nhung dong <0.5R (%d)\n"
      "DIEM HIEU QUA: %.1f / 100\n"
      "-------------------------------------------------------------\n"
      "CON SO DE XUAT:\n"
      "  reward_risk_ratio    : %.2f -> %.2f\n"
      "  breakeven_trigger_rr : %.2f -> %.2f\n"
      "  trailing_trigger_rr  : %.2f -> %.2f\n"
      "  trailing_lock_rr     : %.2f -> %.2f\n"
      "  VolumeAutoRatio      : %.2f -> %.2f\n"
      "  RejectMinWickRatio   : %.2f -> %.2f\n"
      "=============================================================\n",
      TimeToString(InpFromDate, TIME_DATE|TIME_MINUTES),
      TimeToString(InpToDate, TIME_DATE|TIME_MINUTES),
      n, tickTrades, m1Trades,
      winCnt, beCnt, lossCnt, netP, pf, avgRealR, avgWinR,
      avgPeak, medPeak, p75Peak, avgAdv,
      leak1R, leak15R, score,
      Cfg_TargetRR, propRR,
      Cfg_BE_TriggerRR, propBE,
      Cfg_Trail_TriggerRR, propTrTrig,
      Cfg_Trail_LockRR, propTrLock,
      Cfg_VolumeAutoRatio, propVol,
      Cfg_RejectMinWickRatio, propWick
   );

   Print(outTxt);

   if(InpExportSummary)
   {
      int h = FileOpen("RSI_Div_TickM1_Summary.txt", FILE_WRITE | FILE_TXT | FILE_ANSI);
      if(h != INVALID_HANDLE) { FileWriteString(h, outTxt); FileClose(h); }
   }
}
