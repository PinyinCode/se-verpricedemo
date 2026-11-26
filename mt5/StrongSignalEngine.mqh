//+------------------------------------------------------------------+
//| StrongSignalEngine.mqh                                           |
//| Module chấm điểm Strong Signal đa tầng cho RSI Divergence       |
//| 5 tầng: Divergence Quality + Volume + Rejection + Context + HTF |
//+------------------------------------------------------------------+
#ifndef __STRONG_SIGNAL_ENGINE_MQH__
#define __STRONG_SIGNAL_ENGINE_MQH__

//--- Cấu hình engine (được set từ Indicator input) ----------------
struct SSEConfig
{
   bool   enableScoring;
   bool   requireHTFAlignment;
   double scoreThresholdStrong;   // A+ >= 70
   double scoreThresholdGood;     // A  >= 55
   int    pendingTTLBars;
   double minRejectATR;
   int    freshnessMaxBars;
   bool   downgradeCounterTrend;
   int    atrPeriod;
   int    htfMAPeriod;
};

//--- Kết quả chấm điểm --------------------------------------------
struct SStrongResult
{
   bool   isStrong;        // true = emit strong signal
   bool   isGood;          // true = chất lượng khá (không emit nếu SendOnlyStrong)
   double totalScore;      // 0-90
   string grade;           // "A+", "A", "B", "C", "FILTERED"
   string reason;          // lý do chi tiết
   
   // Chi tiết từng tầng (debug/log)
   double s1_divergence;
   double s2_volume;
   double s3_rejection;
   double s4_context;
   bool   htfAligned;
};

//--- Pending signal state machine ---------------------------------
struct SPendingSignal
{
   bool     active;
   datetime createdAt;     // GMT+7
   datetime divTime;       // GMT+7 của bar divergence
   int      divShiftAtCreate;
   int      ttlBars;
   double   divPrice;      // giá tại divergence (high/low)
   double   divRSI;
   double   closeAtDiv;    // close bar divergence (để check reject direction)
   double   rejectMinDist; // = divPrice ± ATR*minRejectATR
};

//+------------------------------------------------------------------+
//| Khởi tạo pending rỗng                                            |
//+------------------------------------------------------------------+
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

//+------------------------------------------------------------------+
//| Tầng 1: Divergence Quality (0-30)                                |
//+------------------------------------------------------------------+
double SSE_ScoreDivergence(double rsi1, double rsi2, double price1, double price2,
                           int barsSinceDiv, int freshnessMaxBars)
{
   double rsiDepth = MathAbs(rsi1 - rsi2);                        // 0-30 thường
   double priceDeltaPct = (price1 > 0) ? MathAbs(price2 - price1) / price1 * 100.0 : 0;

   // RSI depth: >= 8 → full 15đ (divergence sâu)
   double rsiScore = MathMin(15.0, rsiDepth * 1.8);

   // Price delta: >= 0.5% → full 10đ
   double priceScore = MathMin(10.0, priceDeltaPct * 20.0);

   // Freshness: <= 3 bar → full 5đ; giảm dần đến 0 khi >= freshnessMaxBars
   double freshScore = 0.0;
   if(barsSinceDiv <= freshnessMaxBars && freshnessMaxBars > 0)
      freshScore = 5.0 * (1.0 - (double)MathMax(0, barsSinceDiv) / (double)freshnessMaxBars);
   if(freshScore < 0) freshScore = 0;

   return MathMax(0.0, MathMin(30.0, rsiScore + priceScore + freshScore));
}

//+------------------------------------------------------------------+
//| Tầng 2: Volume Confirmation (0-25)                               |
//+------------------------------------------------------------------+
double SSE_ScoreVolume(double volRatio, bool isReversalCandle, double barBodyRatio)
{
   // volRatio: 1.0 = trung bình, 2.5+ = cực cao
   double baseScore = MathMin(15.0, MathMax(0.0, (volRatio - 1.0) * 10.0));
   double reversalBonus = isReversalCandle ? 5.0 : 0.0;
   double bodyBonus = (barBodyRatio > 0.6) ? 5.0 : ((barBodyRatio > 0.3) ? 2.5 : 0.0);
   return MathMax(0.0, MathMin(25.0, baseScore + reversalBonus + bodyBonus));
}

//+------------------------------------------------------------------+
//| Tầng 3: Rejection Quality (0-25)                                 |
//+------------------------------------------------------------------+
double SSE_ScoreRejection(string sym, ENUM_TIMEFRAMES tf,
                          double divPrice, double currentPrice,
                          int barsSinceDiv, double atrValue, int atrIdx)
{
   if(barsSinceDiv < 0) return 0;

   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(point <= 0) point = 0.00001;

   double rejectDist = MathAbs(currentPrice - divPrice);
   double atrSafe = (atrValue > 0) ? atrValue : (point * 100);
   double distInATR = rejectDist / atrSafe;

   // Dist score: >= 1.0 ATR reject → full 10đ (linear 0-1.0 ATR)
   double distScore = MathMin(10.0, distInATR * 10.0);

   // Timing: reject ở bar 0-2 → full 10đ; bar 3-4 → 6đ; >4 → 3đ
   double timingScore;
   if(barsSinceDiv <= 2)      timingScore = 10.0;
   else if(barsSinceDiv <= 4) timingScore = 6.0;
   else                       timingScore = 3.0;

   // Close quality: bar reject phải có thân nến ngược hướng
   double closeScore = 5.0;  // mặc định nếu đã check ở caller

   return MathMax(0.0, MathMin(25.0, distScore + timingScore + closeScore));
}

//+------------------------------------------------------------------+
//| Tầng 4: Context Score (0-10)                                     |
//+------------------------------------------------------------------+
double SSE_ScoreContext(bool isBearish, double rsiAtDiv,
                        double currentSpread, double avgSpread,
                        double htfRSI)
{
   double score = 0;

   // RSI tại divergence phải > 60 (bear) hoặc < 40 (bull)
   if(isBearish && rsiAtDiv > 60.0) score += 3.0;
   if(!isBearish && rsiAtDiv < 40.0) score += 3.0;

   // Higher-TF RSI đồng hướng
   if(htfRSI > 0)
   {
      if(isBearish && htfRSI > 50.0) score += 4.0;
      if(!isBearish && htfRSI < 50.0) score += 4.0;
   }

   // Spread không giãn bất thường
   if(avgSpread > 0 && currentSpread > 0 && currentSpread < avgSpread * 1.5)
      score += 3.0;

   return MathMax(0.0, MathMin(10.0, score));
}

//+------------------------------------------------------------------+
//| HTF Trend Alignment (hard gate)                                  |
//+------------------------------------------------------------------+
bool SSE_IsHTFAligned(string sym, ENUM_TIMEFRAMES tf, bool isBearish, int htfMAPeriod)
{
   ENUM_TIMEFRAMES htf = (tf == PERIOD_M15) ? PERIOD_H1 :
                        ((tf == PERIOD_H1)  ? PERIOD_H4 :
                        ((tf == PERIOD_H4)  ? PERIOD_D1 : PERIOD_D1));

   int maHandle = iMA(sym, htf, htfMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   if(maHandle == INVALID_HANDLE) return true;  // fail-safe: không filter nếu lỗi

   double ma[];
   ArraySetAsSeries(ma, true);
   if(CopyBuffer(maHandle, 0, 0, 1, ma) <= 0) { IndicatorRelease(maHandle); return true; }
   IndicatorRelease(maHandle);

   double close = iClose(sym, htf, 0);
   if(close <= 0 || ma[0] <= 0) return true;

   // Nếu giá cách MA < 0.5% → coi như neutral (vẫn align)
   double distPct = MathAbs(close - ma[0]) / ma[0] * 100.0;
   if(distPct < 0.5) return true;

   if(isBearish) return close < ma[0];
   return close > ma[0];
}

//+------------------------------------------------------------------+
//| HTF RSI (cho Context score)                                      |
//+------------------------------------------------------------------+
double SSE_GetHTFRSI(string sym, ENUM_TIMEFRAMES tf, int period)
{
   ENUM_TIMEFRAMES htf = (tf == PERIOD_M15) ? PERIOD_H1 :
                        ((tf == PERIOD_H1)  ? PERIOD_H4 : PERIOD_D1);
   int h = iRSI(sym, htf, period, PRICE_CLOSE);
   if(h == INVALID_HANDLE) return -1;
   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(h, 0, 0, 1, buf) <= 0) { IndicatorRelease(h); return -1; }
   IndicatorRelease(h);
   return buf[0];
}

//+------------------------------------------------------------------+
//| ATR trung bình spread N bar (cho Context score)                  |
//+------------------------------------------------------------------+
double SSE_GetAvgSpread(string sym, ENUM_TIMEFRAMES tf, int bars)
{
   int h = iATR(sym, tf, 14);  // dùng ATR làm proxy spread volatility
   if(h == INVALID_HANDLE) return 0;
   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(h, 0, 0, 1, buf) <= 0) { IndicatorRelease(h); return 0; }
   IndicatorRelease(h);
   // Spread hiện tại
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   return (ask - bid);
}

//+------------------------------------------------------------------+
//| Đánh giá tổng hợp: 5 tầng                                        |
//+------------------------------------------------------------------+
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
                           int    atrIdx,
                           double atrValue,
                           double htfRSI)
{
   SStrongResult r;
   r.isStrong = false;
   r.isGood = false;
   r.totalScore = 0;
   r.grade = "C";
   r.reason = "";
   r.s1_divergence = 0;
   r.s2_volume = 0;
   r.s3_rejection = 0;
   r.s4_context = 0;
   r.htfAligned = true;

   //=== Gate: HTF alignment ===
   if(cfg.requireHTFAlignment)
   {
      bool aligned = SSE_IsHTFAligned(sym, tf, isBearish, cfg.htfMAPeriod);
      r.htfAligned = aligned;
      if(!aligned)
      {
         // Counter-trend: nếu downgrade bật → không strong
         if(cfg.downgradeCounterTrend)
         {
            r.grade = "FILTERED";
            r.reason = "HTF not aligned (counter-trend)";
            return r;
         }
      }
   }

   //=== Tầng 1-4 cộng điểm ===
   r.s1_divergence = SSE_ScoreDivergence(divRSI1, divRSI2, divPrice1, divPrice2,
                                          barsSinceDiv, cfg.freshnessMaxBars);
   r.s2_volume     = SSE_ScoreVolume(volRatio, isReversalCandle, barBodyRatio);
   r.s3_rejection  = SSE_ScoreRejection(sym, tf, divPrice2, currentPrice,
                                         barsSinceDiv, atrValue, atrIdx);
   double currentSpread = SymbolInfoDouble(sym, SYMBOL_ASK) - SymbolInfoDouble(sym, SYMBOL_BID);
   double avgSpread = SSE_GetAvgSpread(sym, tf, 20);
   r.s4_context = SSE_ScoreContext(isBearish, divRSI2, currentSpread, avgSpread, htfRSI);

   r.totalScore = r.s1_divergence + r.s2_volume + r.s3_rejection + r.s4_context;

   //=== Phân loại ===
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
      r.isStrong = false;
      r.isGood = false;
   }
   else
   {
      r.grade = "C";
      r.isStrong = false;
      r.isGood = false;
   }

   // Nếu counter-trend mà vẫn vượt ngưỡng → giảm 15đ
   if(!r.htfAligned && cfg.downgradeCounterTrend)
   {
      r.totalScore = MathMax(0.0, r.totalScore - 15.0);
      if(r.totalScore < cfg.scoreThresholdStrong) r.isStrong = false;
      if(r.totalScore < cfg.scoreThresholdGood)   r.isGood = false;
   }

   r.reason = StringFormat("S1=%.1f S2=%.1f S3=%.1f S4=%.1f Total=%.1f Grade=%s HTF=%s",
                            r.s1_divergence, r.s2_volume, r.s3_rejection, r.s4_context,
                            r.totalScore, r.grade, r.htfAligned ? "OK" : "NO");
   return r;
}

//+------------------------------------------------------------------+
//| Pending: tạo mới khi divergence hình thành                       |
//+------------------------------------------------------------------+
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

   double rejectDist = (atrValue > 0) ? atrValue * minRejectATR : 0;
   p.rejectMinDist = rejectDist;
}

//+------------------------------------------------------------------+
//| Pending: kiểm tra reject, có trigger strong không                |
//+------------------------------------------------------------------+
// Trả về: 1 = triggered (strong), 0 = pending còn hiệu lực, -1 = expired
int SSE_CheckPending(SPendingSignal &p, string sym, ENUM_TIMEFRAMES tf,
                     bool isBearish, double currentPrice, datetime nowGmt7,
                     int currentDivShift, double atrValue)
{
   if(!p.active) return -1;

   // Check TTL
   int barsElapsed = 0;
   if(p.divShiftAtCreate >= 0 && currentDivShift >= 0 && currentDivShift < p.divShiftAtCreate)
      barsElapsed = p.divShiftAtCreate - currentDivShift;
   else
      barsElapsed = (int)((nowGmt7 - p.createdAt) / PeriodSeconds(tf));

   if(barsElapsed > p.ttlBars)
   {
      p.active = false;
      return -1;
   }

   // Check reject: giá phải vượt ngược hướng div
   bool rejected = isBearish ? (currentPrice < p.divPrice) : (currentPrice > p.divPrice);
   if(!rejected) return 0;

   // Check reject đủ mạnh
   double rejectDist = MathAbs(currentPrice - p.divPrice);
   if(rejectDist < p.rejectMinDist) return 0;

   // Trigger!
   p.active = false;
   return 1;
}

#endif // __STRONG_SIGNAL_ENGINE_MQH__
