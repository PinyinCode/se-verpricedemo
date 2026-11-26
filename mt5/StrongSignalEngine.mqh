//+------------------------------------------------------------------+
//| StrongSignalEngine.mqh                                           |
//| Module chấm điểm Strong Signal đa tầng cho RSI Divergence v2     |
//| 5 tầng: Divergence + Volume + Rejection + Context + HTF Gate     |
//+------------------------------------------------------------------+
#ifndef __STRONG_SIGNAL_ENGINE_MQH__
#define __STRONG_SIGNAL_ENGINE_MQH__

struct SSEConfig
{
   bool   enableScoring;
   bool   requireHTFAlignment;
   double scoreThresholdStrong;
   double scoreThresholdGood;
   int    pendingTTLBars;
   double minRejectATR;
   int    freshnessMaxBars;
   bool   downgradeCounterTrend;
   int    atrPeriod;
   int    htfMAPeriod;
};

struct SStrongResult
{
   bool   isStrong;
   bool   isGood;
   double totalScore;
   string grade;
   string reason;
   double s1_divergence;
   double s2_volume;
   double s3_rejection;
   double s4_context;
   bool   htfAligned;
};

struct SPendingSignal
{
   bool     active;
   datetime createdAt;
   datetime divTime;
   int      divShiftAtCreate;
   int      ttlBars;
   double   divPrice;
   double   divRSI;
   double   closeAtDiv;
   double   rejectMinDist;
};

void SSE_ResetPending(SPendingSignal &p)
{
   p.active = false;
   p.createdAt = 0;
   p.divTime = 0;
   p.divShiftAtCreate = -1;
   p.ttlBars = 0;
   p.divPrice = 0;
   p.divRSI = 0;
   p.closeAtDiv = 0;
   p.rejectMinDist = 0;
}

ENUM_TIMEFRAMES SSE_GetHTF(ENUM_TIMEFRAMES tf)
{
   if(tf == PERIOD_M15) return PERIOD_H1;
   if(tf == PERIOD_H1)  return PERIOD_H4;
   if(tf == PERIOD_H4)  return PERIOD_D1;
   return PERIOD_D1;
}

double SSE_ScoreDivergence(double rsi1, double rsi2, double price1, double price2,
                           int barsSinceDiv, int freshnessMaxBars)
{
   double rsiDepth = MathAbs(rsi1 - rsi2);
   double priceDeltaPct = (price1 > 0) ? MathAbs(price2 - price1) / price1 * 100.0 : 0.0;
   double rsiScore = MathMin(15.0, rsiDepth * 1.8);
   double priceScore = MathMin(10.0, priceDeltaPct * 20.0);
   double freshScore = 0.0;
   if(freshnessMaxBars > 0 && barsSinceDiv <= freshnessMaxBars)
   {
      freshScore = 5.0 * (1.0 - (double)MathMax(0, barsSinceDiv) / (double)freshnessMaxBars);
      if(freshScore < 0.0) freshScore = 0.0;
   }
   return MathMax(0.0, MathMin(30.0, rsiScore + priceScore + freshScore));
}

double SSE_ScoreVolume(double volRatio, bool isReversalCandle, double barBodyRatio)
{
   double baseScore = MathMin(15.0, MathMax(0.0, (volRatio - 1.0) * 10.0));
   double reversalBonus = isReversalCandle ? 5.0 : 0.0;
   double bodyBonus = (barBodyRatio > 0.6) ? 5.0 : ((barBodyRatio > 0.3) ? 2.5 : 0.0);
   return MathMax(0.0, MathMin(25.0, baseScore + reversalBonus + bodyBonus));
}

double SSE_ScoreRejection(string sym, ENUM_TIMEFRAMES tf,
                          double divPrice, double currentPrice,
                          int barsSinceDiv, double atrValue)
{
   if(barsSinceDiv < 0) return 0.0;
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(point <= 0.0) point = 0.00001;
   double rejectDist = MathAbs(currentPrice - divPrice);
   double atrSafe = (atrValue > 0.0) ? atrValue : (point * 100.0);
   double distInATR = rejectDist / atrSafe;
   double distScore = MathMin(10.0, distInATR * 10.0);

   double timingScore = 3.0;
   if(barsSinceDiv <= 2)      timingScore = 10.0;
   else if(barsSinceDiv <= 4) timingScore = 6.0;

   double closeScore = 5.0;
   return MathMax(0.0, MathMin(25.0, distScore + timingScore + closeScore));
}

double SSE_ScoreContext(bool isBearish, double rsiAtDiv,
                        double currentSpread, double avgSpread,
                        double htfRSI)
{
   double score = 0.0;
   if(isBearish && rsiAtDiv > 60.0)  score += 3.0;
   if(!isBearish && rsiAtDiv < 40.0) score += 3.0;

   if(htfRSI > 0.0)
   {
      if(isBearish && htfRSI > 50.0)  score += 4.0;
      if(!isBearish && htfRSI < 50.0) score += 4.0;
   }

   if(avgSpread > 0.0 && currentSpread > 0.0 && currentSpread <= avgSpread * 1.5)
      score += 3.0;

   return MathMax(0.0, MathMin(10.0, score));
}

bool SSE_CheckHTFAlignmentFromHandle(string sym, ENUM_TIMEFRAMES tf, int maHandle, bool isBearish)
{
   ENUM_TIMEFRAMES htf = SSE_GetHTF(tf);
   if(maHandle == INVALID_HANDLE) return true;
   double ma[];
   ArraySetAsSeries(ma, true);
   if(CopyBuffer(maHandle, 0, 0, 1, ma) <= 0) return true;
   double close = iClose(sym, htf, 0);
   if(close <= 0.0 || ma[0] <= 0.0 || !MathIsValidNumber(ma[0])) return true;

   double distPct = MathAbs(close - ma[0]) / ma[0] * 100.0;
   if(distPct < 0.5) return true;
   return isBearish ? (close < ma[0]) : (close > ma[0]);
}

double SSE_ReadRSIFromHandle(int handle)
{
   if(handle == INVALID_HANDLE) return -1.0;
   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(handle, 0, 0, 1, buf) <= 0) return -1.0;
   return MathIsValidNumber(buf[0]) ? buf[0] : -1.0;
}

SStrongResult SSE_Evaluate(SSEConfig &cfg,
                           string sym, ENUM_TIMEFRAMES tf,
                           bool isBearish, bool isRegular,
                           double divPrice1, double divPrice2,
                           double divRSI1,  double divRSI2,
                           int    barsSinceDiv,
                           double volRatio,
                           bool   isReversalCandle,
                           double barBodyRatio,
                           double currentPrice,
                           int    divShift,
                           double atrValue,
                           double htfRSI,
                           bool   htfAligned = true,
                           double currentSpread = 0.0,
                           double avgSpread = 0.0)
{
   SStrongResult r;
   r.isStrong = false;
   r.isGood = false;
   r.totalScore = 0.0;
   r.grade = "C";
   r.reason = "";
   r.s1_divergence = 0.0;
   r.s2_volume = 0.0;
   r.s3_rejection = 0.0;
   r.s4_context = 0.0;
   r.htfAligned = htfAligned;

   if(cfg.requireHTFAlignment && !htfAligned)
   {
      r.grade = "FILTERED";
      r.reason = "HTF not aligned (counter-trend)";
      return r;
   }

   r.s1_divergence = SSE_ScoreDivergence(divRSI1, divRSI2, divPrice1, divPrice2,
                                         barsSinceDiv, cfg.freshnessMaxBars);
   r.s2_volume     = SSE_ScoreVolume(volRatio, isReversalCandle, barBodyRatio);
   r.s3_rejection  = SSE_ScoreRejection(sym, tf, divPrice2, currentPrice,
                                        barsSinceDiv, atrValue);

   double effCurrentSpread = (currentSpread > 0.0) ? currentSpread :
                             MathMax(0.0, SymbolInfoDouble(sym, SYMBOL_ASK) - SymbolInfoDouble(sym, SYMBOL_BID));
   double effAvgSpread = (avgSpread > 0.0) ? avgSpread : effCurrentSpread;
   r.s4_context = SSE_ScoreContext(isBearish, divRSI2, effCurrentSpread, effAvgSpread, htfRSI);

   r.totalScore = r.s1_divergence + r.s2_volume + r.s3_rejection + r.s4_context;

   if(!r.htfAligned && cfg.downgradeCounterTrend)
      r.totalScore = MathMax(0.0, r.totalScore - 15.0);

   if(r.totalScore >= cfg.scoreThresholdStrong)
   {
      r.grade = "A+";
      r.isStrong = true;
      r.isGood = true;
   }
   else if(r.totalScore >= cfg.scoreThresholdGood)
   {
      r.grade = "A";
      r.isStrong = true;
      r.isGood = true;
   }
   else if(r.totalScore >= 40.0)
   {
      r.grade = "B";
   }
   else
   {
      r.grade = "C";
   }

   r.reason = StringFormat("S1=%.1f S2=%.1f S3=%.1f S4=%.1f Total=%.1f Grade=%s HTF=%s",
                           r.s1_divergence, r.s2_volume, r.s3_rejection, r.s4_context,
                           r.totalScore, r.grade, r.htfAligned ? "OK" : "NO");
   return r;
}

void SSE_CreatePending(SPendingSignal &p, datetime nowGmt7, datetime divTime,
                       int divShift, double divPrice, double divRSI,
                       double closeAtDiv, double atrValue, int ttlBars, double minRejectATR)
{
   p.active = true;
   p.createdAt = nowGmt7;
   p.divTime = divTime;
   p.divShiftAtCreate = divShift;
   p.ttlBars = ttlBars;
   p.divPrice = divPrice;
   p.divRSI = divRSI;
   p.closeAtDiv = closeAtDiv;
   p.rejectMinDist = (atrValue > 0.0) ? (atrValue * minRejectATR) : 0.0;
}

int SSE_CheckPending(SPendingSignal &p, string sym, ENUM_TIMEFRAMES tf,
                     bool isBearish, double currentPrice, datetime nowGmt7,
                     int currentDivShift, double atrValue)
{
   if(!p.active) return -1;
   int barsElapsed = 0;
   if(p.divShiftAtCreate >= 0 && currentDivShift >= 0 && currentDivShift < p.divShiftAtCreate)
      barsElapsed = p.divShiftAtCreate - currentDivShift;
   else
      barsElapsed = (int)((nowGmt7 - p.createdAt) / MathMax(60, PeriodSeconds(tf)));

   if(barsElapsed > p.ttlBars)
   {
      p.active = false;
      return -1;
   }

   bool rejected = isBearish ? (currentPrice < p.divPrice) : (currentPrice > p.divPrice);
   if(!rejected) return 0;

   double rejectDist = MathAbs(currentPrice - p.divPrice);
   double minDist = (atrValue > 0.0 && p.rejectMinDist <= 0.0) ? (atrValue * 0.3) : p.rejectMinDist;
   if(rejectDist < minDist) return 0;

   p.active = false;
   return 1;
}

#endif // __STRONG_SIGNAL_ENGINE_MQH__
