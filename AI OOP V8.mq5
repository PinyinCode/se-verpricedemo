
void CleanupInvalidRTTodaySignals()
{
   for(int i=g_todaySignalCount-1;i>=0;i--)
   {
      if(g_todaySignals[i].signalType=="RT")
      {
         string prefix="DIVDATA_"+g_todaySignals[i].sym+"_"+g_todaySignals[i].tfStr+"_";
         string type=g_todaySignals[i].type;
         if(!GlobalVariableCheck(prefix+type+"_ACTIVE")||GlobalVariableGet(prefix+type+"_ACTIVE")==0)
         {
            if(i<g_todaySignalCount-1)
               g_todaySignals[i]=g_todaySignals[g_todaySignalCount-1];
            g_todaySignalCount--;
         }
      }
   }
   PublishTodaySignals();
}

void ClearOldTodaySignals()
{
   datetime gmt7Now=GetGMT7Now();
   datetime todayStartGMT7=gmt7Now-(gmt7Now%86400);
   int validCount=0;
   for(int i=0;i<g_todaySignalCount;i++)
   {
      datetime signalGMT7=ServerTimeToGMT7(g_todaySignals[i].time);
      if(signalGMT7>=todayStartGMT7)
      {
         if(validCount!=i)
            g_todaySignals[validCount]=g_todaySignals[i];
         validCount++;
      }
   }
   g_todaySignalCount=validCount;
   PublishTodaySignals();
}

string BuildTodaySignalsDisplay()
{
   CleanupInvalidRTTodaySignals();
   
   string display="\nTODAY SIGNALS (GMT+7) - "+IntegerToString(g_todaySignalCount)+" signals\n";
   display+=" \n";
   
   if(g_todaySignalCount > 0)
   {
      for(int i=0;i<g_todaySignalCount-1;i++)
         for(int j=i+1;j<g_todaySignalCount;j++)
            if(ServerTimeToGMT7(g_todaySignals[i].time)<ServerTimeToGMT7(g_todaySignals[j].time))
            {
               STodaySignal tmp=g_todaySignals[i];
               g_todaySignals[i]=g_todaySignals[j];
               g_todaySignals[j]=tmp;
            }
      for(int i=0;i<g_todaySignalCount;i++)
      {
         string icon=(g_todaySignals[i].type=="BEAR")?"[SELL]":"[BUY]";
         string sigTag=(g_todaySignals[i].signalType=="RT")?" [RT]":"";
         datetime gmt7Time=ServerTimeToGMT7(g_todaySignals[i].time);
         string timeStr=TimeToString(gmt7Time,TIME_MINUTES);
         display+=StringFormat("%s%s %s %s [%s] %s\n",icon,sigTag,g_todaySignals[i].sym,g_todaySignals[i].tfStr,g_todaySignals[i].divType,timeStr);
      }
   }
   else
   {
      display+="No signals yet\n";
   }
   
   display+="----------------------------------------\n";
   return display;
}

//+------------------------------------------------------------------+
//| DISPLAY FUNCTIONS (GIỮ NGUYÊN)                                     |
//+------------------------------------------------------------------+
void Draw(string divergenceComment)
{ 
   for(int i=ObjectsTotal(0)-1;i>=0;i--)
   {
      string name=ObjectName(0,i);
      if(StringFind(name,P)==0)
         ObjectDelete(0,name);
   }
   
   int rowH=18;
   int y=topMargin;
   datetime gmt7Time=GetGMT7Now();
   string timeStr=TimeToString(gmt7Time,TIME_MINUTES|TIME_SECONDS);
   string heartbeatChar=g_heartbeatState?"*":".";
   
   string tfListStr="";
   if(EnableM15) tfListStr+="M15 ";
   if(EnableH1) tfListStr+="H1 ";
   
   string timeFilterStr=EnableTimeFilter?" | "+IntegerToString(ScanStartHour)+"h-"+IntegerToString(ScanEndHour)+"h":" | 24/7";
   string signalStatus=EnableSignalOutput?"SIG:ON":"SIG:OFF";
   string notifyStatus=EnablePushNotify?"PUSH:ON":"PUSH:OFF";
   string monoStatus=RT_RequireMonotonicMove?"MONO:ON":"MONO:OFF";
   string newsStatus="";
   if(EnableNewsFilter)
      newsStatus=g_isNewsBlocked?"NEWS:BLOCKED":"NEWS:SAFE";
   
   string line1Status=StringFormat("[%s] IND: %s | %s | %s (GMT+7)%s",
                                    heartbeatChar,g_statusText,tfListStr,timeStr,timeFilterStr);
   UpdMsg("HEARTBEAT",line1Status,startX,y,g_statusColor,HeaderFontSize,true);
   y+=rowH+2;
   
   string line2Features=StringFormat("%s | %s | %s | %s",
                                      signalStatus,notifyStatus,monoStatus,newsStatus);
   color line2Color=g_isNewsBlocked?clrOrangeRed:clrDimGray;
   UpdMsg("FEATURES",line2Features,startX,y,line2Color,9,false);
   y+=rowH+6;
   
   UpdMsg("LINE_DECOR_1","================================================================================",startX,y,clrDarkGray,8);
   y+=10;
   
   if(divergenceComment!=""&&StringFind(divergenceComment,"CRASHED")<0)
   {
      string divLines[];
      int divCount=StringSplit(divergenceComment,'\n',divLines);
      bool inHistorical=false,inToday=false;
      int validLineCount=0;
      
      for(int i=0;i<divCount;i++)
      {
         if(divLines[i]=="")continue;
         
         if(StringFind(divLines[i],"[M15]")>=0||StringFind(divLines[i],"[H1]")>=0)
         {
            inHistorical=true;
            y+=4;
         }
         if(StringFind(divLines[i],"TODAY")>=0)
         {
            inHistorical=false;inToday=true;
            y+=4;
         }
         
         color c=clrLightBlue;
         int fontSize=RT_FontSize;
         
         if(inToday)
         {
            fontSize=TODAY_FontSize;
            if(StringFind(divLines[i],"[SELL]")>=0)
               c=clrLightCoral;
            else if(StringFind(divLines[i],"[BUY]")>=0)
               c=clrMediumAquamarine;
            else if(StringFind(divLines[i],"TODAY")>=0)
               c=clrOrange;
            else
               c=clrDarkGray;
         }
         else if(inHistorical)
         {
            fontSize=HIST_FontSize;
            if(StringFind(divLines[i],"Bear")>=0)
               c=clrPaleVioletRed;
            else if(StringFind(divLines[i],"Bull")>=0)
               c=clrDarkSeaGreen;
            else
               c=clrGold;
         }
         else
         {
            fontSize=RT_FontSize;
            if(StringFind(divLines[i],"SELL")>=0)
               c=clrDeepPink;
            else if(StringFind(divLines[i],"BUY")>=0)
               c=clrMediumSpringGreen;
            else if(StringFind(divLines[i],"---")>=0)
               c=clrCadetBlue;
         }
         
         UpdMsg("DIV_"+IntegerToString(validLineCount),divLines[i],startX,y,c,fontSize);
         y+=(inToday||inHistorical)?rowH-4:rowH;
         validLineCount++;
      }
      Cleanup(validLineCount);
   }
   else
   {
      Cleanup(0);
   }
}

void UpdMsg(string n, string t, int x, int y, color c, int fontSize=11, bool isBold=false)
{
   string o=P+n;
   if(ObjectFind(0,o)<0)
      ObjectCreate(0,o,OBJ_LABEL,0,0,0);
   ObjectSetString(0,o,OBJPROP_FONT,"Consolas");
   ObjectSetInteger(0,o,OBJPROP_FONTSIZE,fontSize);
   ObjectSetInteger(0,o,OBJPROP_CORNER,CORNER_LEFT_UPPER);
   ObjectSetInteger(0,o,OBJPROP_COLOR,c);
   ObjectSetInteger(0,o,OBJPROP_XDISTANCE,x);
   ObjectSetInteger(0,o,OBJPROP_YDISTANCE,y);
   ObjectSetString(0,o,OBJPROP_TEXT,t);
   ObjectSetInteger(0,o,OBJPROP_ANCHOR,ANCHOR_LEFT_UPPER);
   ObjectSetInteger(0,o,OBJPROP_SELECTABLE,false);
}

void Cleanup(int validLineCount)
{ 
   for(int i=ObjectsTotal(0)-1;i>=0;i--)
   {
      string name=ObjectName(0,i);
      if(StringFind(name,P)!=0)continue;
      if(StringFind(name,P+"HEARTBEAT")==0)continue;
      if(StringFind(name,P+"FEATURES")==0)continue;
      if(StringFind(name,P+"LINE_DECOR_")==0)continue;
      if(StringFind(name,P+"DIV_")==0)
      {
         int idx_obj=(int)StringToInteger(StringSubstr(name,StringLen(P+"DIV_")));
         if(idx_obj>=validLineCount)ObjectDelete(0,name);
      }
      else ObjectDelete(0,name);
   }
}

void ClearAll()
{
   for(int i=ObjectsTotal(0)-1;i>=0;i--)
   {
      string n=ObjectName(0,i);
      if(StringFind(n,P)==0)
         ObjectDelete(0,n);
   }
}

//+------------------------------------------------------------------+
//| ONINIT                                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   datetime ct = TimeCurrent();
   if(ct > expiry)
   {
      MessageBox("Expired!","EXPIRED",MB_OK|MB_ICONSTOP);
      return INIT_FAILED;
   }
   
   EventSetTimer(1);
   CalculateServerGMTOffset();
   UpdateTodayBounds();
   g_lastUpdateTime = ct;
   g_heartbeatCounter = 0;
   g_firstScanDone = false;
   g_initTime = ct;
   g_statusText = "KHỞI TẠO...";
   g_statusColor = clrDimGray;
   g_lastScanM15 = g_lastScanH1 = 0;
   g_lastRTPrintTime = g_lastRTCount = 0;
   g_isNewsBlocked = false;
   g_lastNewsCheckTime = 0;
   ArrayResize(g_todaySignals, 0);
   g_todaySignalCount = 0;
   CleanupOldSignals();
   
   string tfList = "";
   if(EnableM15) tfList += "M15 ";
   if(EnableH1) tfList += "H1 ";
   if(tfList == "") tfList = "NONE!";
   
   Print("═══════════════════════════════════════════");
   Print("RSI Divergence Realtime v1.0");
   Print("   Timeframes: ", tfList);
   Print("═══════════════════════════════════════════");
   
   g_statusText = "RUNNING";
   g_statusColor = clrTeal;
   
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| ONTIMER                                                           |
//+------------------------------------------------------------------+
void PublishScanSymbols(string symbols)
{
   int h=FileOpen("DIVSCAN_SYMBOLS.txt",FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(h==INVALID_HANDLE) return;
   FileWriteString(h,IntegerToString((int)TimeCurrent())+"#"+symbols);
   FileClose(h);
}

void OnTimer()
{
   GlobalVariableSet("HB_RSI_Indicator",(double)TimeCurrent());
   g_lastUpdateTime=TimeCurrent();
   g_heartbeatCounter++;
   if(g_heartbeatCounter%2==0) g_heartbeatState=!g_heartbeatState;
   
   ClearOldTodaySignals();
   UpdateTodayBounds();
   
   if(EnableNewsFilter && TimeCurrent()-g_lastNewsCheckTime>=NewsCheckIntervalSeconds)
   {
      bool wasBlocked=g_isNewsBlocked;
      g_isNewsBlocked=!IsNewsSafe();
      g_lastNewsCheckTime=TimeCurrent();
   }
   
   if(g_isNewsBlocked)
   {
      PublishScanSymbols("");
      g_statusColor=clrOrangeRed;
      g_statusText="NEWS BLOCKED";
      Draw("NEWS BLOCKED");
      return;
   }
   
   bool isScanTime=IsInScanTime();
   bool has_crash=false;
   
   if(g_firstScanDone)
   {
      if(ArraySize(arrSymTF)==0)
         has_crash=true;
      else
      {
         bool allInvalid=true;
         for(int i=0;i<ArraySize(arrSymTF);i++)
            if(arrSymTF[i].rsiHandle!=INVALID_HANDLE)
            {allInvalid=false;break;}
         if(allInvalid) has_crash=true;
         if(!has_crash && !TerminalInfoInteger(TERMINAL_CONNECTED))
            has_crash=true;
      }
   }
   
   if(has_crash)
   {
      PublishScanSymbols("");
      g_statusColor=clrRed;
      g_statusText="CRASHED!";
      Draw("CRASHED");
      return;
   }
   
   if(!isScanTime)
   {
      PublishScanSymbols("");
      g_statusColor=clrGray;
      g_statusText="STANDBY";
      Draw("STANDBY");
      return;
   }
   
   g_statusColor=clrAquamarine;
   g_statusText="RUNNING";
   CleanupOldSignals();
   
   datetime now=TimeCurrent();
   bool sM15=false,sH1=false;
   if(EnableM15) sM15=true;
   if(EnableH1 && now-g_lastScanH1>=5) { sH1=true; g_lastScanH1=now; }
   
   if(!sM15 && !sH1)
   {
      string td=BuildTodaySignalsDisplay();
      string dm=g_latestRTSignal==""?"Scanning...":g_latestRTSignal;
      Draw(dm+td);
      return;
   }
   
   SRTLine rtLines[];
   int rtCount=0;
   string histLines[];
   datetime histTimes[];
   int histCount=0;
   
   string scannedSymbols="";
   int totalSymbols=SymbolsTotal(true);
   for(int s=0; s<totalSymbols; s++)
   {
      string sym=SymbolName(s,true);
      if(IsSymbolExcluded(sym)) continue;
      
      if(EnableM15)
      {
         int idx=GetSymbolTFIndex(sym,PERIOD_M15);
         if(idx==-1) idx=InitSymTF(sym,PERIOD_M15,"M15");
         if(sM15)
         {
            ProcessSymbolTF(idx,rtLines,rtCount,histLines,histTimes,histCount);
            ClearInactiveRTDivergence(sym,"M15",PERIOD_M15);
         }
         if(scannedSymbols!="") scannedSymbols+=";";
         scannedSymbols+=sym+"|M15";
      }
      
      if(EnableH1)
      {
         int idx=GetSymbolTFIndex(sym,PERIOD_H1);
         if(idx==-1) idx=InitSymTF(sym,PERIOD_H1,"H1");
         if(sH1)
         {
            ProcessSymbolTF(idx,rtLines,rtCount,histLines,histTimes,histCount);
            ClearInactiveRTDivergence(sym,"H1",PERIOD_H1);
         }
         if(scannedSymbols!="") scannedSymbols+=";";
         scannedSymbols+=sym+"|H1";
      }
   }
   PublishScanSymbols(scannedSymbols);
   
   string dc="";
   static string slr="";
   
   if(rtCount>0)
   {
      for(int i=0; i<rtCount-1; i++)
         for(int j=i+1; j<rtCount; j++)
            if(rtLines[i].time>rtLines[j].time)
            {
               SRTLine t=rtLines[i];
               rtLines[i]=rtLines[j];
               rtLines[j]=t;
            }
      string gc="--- RT SIGNALS (1/Bar) ---\n\n";
      for(int i=0; i<rtCount; i++)
         gc+=rtLines[i].text+"\n";
      g_latestRTSignal=gc;
      dc=gc;
      if(gc!=slr)
      {
         PrintRTSignalChange(rtCount,gc);
         slr=gc;
      }
   }
   else
   {
      g_latestRTSignal="";
      dc="Scanning...";
      slr="";
      g_lastRTCount=0;
   }
   
   if(histCount>0)
   {
      for(int i=0; i<histCount-1; i++)
         for(int j=i+1; j<histCount; j++)
            if(histTimes[i]<histTimes[j])
            {
               datetime t=histTimes[i];
               histTimes[i]=histTimes[j];
               histTimes[j]=t;
               string tl=histLines[i];
               histLines[i]=histLines[j];
               histLines[j]=tl;
            }
      for(int i=0; i<histCount; i++)
         dc+="\n"+histLines[i];
   }
   
   string td=BuildTodaySignalsDisplay();
   dc+=td;
   Draw(dc);
   g_firstScanDone=true;
}

//+------------------------------------------------------------------+
//| ONDEINIT                                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   ClearAll();
   
   ENUM_TIMEFRAMES tfs[];
   string tfStrs[];
   GetEnabledTimeframes(tfs,tfStrs);
   for(int s=0;s<SymbolsTotal(true);s++)
   {
      string sym=SymbolName(s,true);
      for(int t=0;t<ArraySize(tfs);t++)
         ClearAllDivergenceData(sym,tfStrs[t]);
   }
   
   for(int i=0;i<ArraySize(arrSymTF);i++)
      if(arrSymTF[i].rsiHandle!=INVALID_HANDLE)
         IndicatorRelease(arrSymTF[i].rsiHandle);
   
   CleanupOldSignals();
}

//+------------------------------------------------------------------+
//| PROCESS SYMBOL TF (GIỮ NGUYÊN LOGIC GỐC)                           |
//+------------------------------------------------------------------+
void ProcessSymbolTF(int idx, SRTLine &rtLines[], int &rtCount, string &histLines[], datetime &histTimes[], int &histCount)
{
   string sym = arrSymTF[idx].sym;
   ENUM_TIMEFRAMES tf = arrSymTF[idx].tf;
   string tfStr = arrSymTF[idx].tfStr;
   int rsiHandle = arrSymTF[idx].rsiHandle;
   
   if(rsiHandle == INVALID_HANDLE) return;

   int readCount = MS_BARS_DEFAULT + 50;
   if(CopyBuffer(rsiHandle, 0, 0, readCount, arrSymTF[idx].rsiBuf) < readCount) return;

   datetime currentBarTime = iTime(sym, tf, 0);
   if(currentBarTime != arrSymTF[idx].lastBarTime)
   {
      if(arrSymTF[idx].lastBarTime != 0 && arrSymTF[idx].lastShiftCached < MS_SwingBars) 
         arrSymTF[idx].lastShiftCached = MS_SwingBars; 
      arrSymTF[idx].lastBarTime = currentBarTime;
   }

   double bestBearH1 = 0, bestBearH2 = 0, bestBearR1 = 0, bestBearR2 = 0;
   datetime bestBearT1 = 0, bestBearT2 = 0;
   bool    bestBearIsReg = false;
   double  bestBearVol = 0;
   
   double bestBullP1 = 0, bestBullP2 = 0, bestBullR1 = 0, bestBullR2 = 0;
   datetime bestBullT1 = 0, bestBullT2 = 0;
   bool    bestBullIsReg = false;
   double  bestBullVol = 0;

   int scanLimit = MathMax(arrSymTF[idx].lastShiftCached, MS_SwingBars);
   
   double prevHighPrice = 0, lastHighPrice = 0, prevHighRSI = 0, lastHighRSI = 0;
   datetime prevHighTime = 0, lastHighTime = 0;
   double prevLowPrice = 0, lastLowPrice = 0, prevLowRSI = 0, lastLowRSI = 0;
   datetime prevLowTime = 0, lastLowTime = 0;
   int lastHighShift = -9999, lastLowShift = -9999;

   // QUÉT SWING POINTS
   for(int shift = scanLimit; shift >= MS_SwingBars; shift--)
   {
      if(IsRSISwingHigh(arrSymTF[idx].rsiBuf, shift))
      {
         if(MathAbs(shift - lastHighShift) >= MS_SwingBars)
         {
            datetime t = iTime(sym, tf, shift);
            double rsiVal = GetRSI(arrSymTF[idx].rsiBuf, shift);
            
            int shiftRight = MathMax(0, shift - 1);
            int shiftLeft  = shift + 1;
            double h = iHigh(sym, tf, shift); 
            h = MathMax(h, iHigh(sym, tf, shiftLeft));
            h = MathMax(h, iHigh(sym, tf, shiftRight));
            
            lastHighShift = shift;
            prevHighPrice = lastHighPrice; prevHighRSI = lastHighRSI; prevHighTime = lastHighTime;
            lastHighPrice = h; lastHighRSI = rsiVal; lastHighTime = t;

            if(prevHighPrice > 0 && lastHighPrice > 0)
            {
               double h1 = prevHighPrice, h2 = lastHighPrice;
               double r1 = prevHighRSI, r2 = lastHighRSI;
               datetime t1 = prevHighTime, t2 = lastHighTime;
               bool regBearDiv = (h2 > h1) && (r2 < r1); 
               bool hidBearDiv = (h2 < h1) && (r2 > r1); 
               bool bearDiv = regBearDiv || hidBearDiv;
               int divShift = iBarShift(sym, tf, t2, false);

               if(bearDiv)
               {
                  bool highestValid = true;
                  int shift1 = iBarShift(sym, tf, t1, false);
                  double maxAllowed = MathMax(h1, h2);        
                  for(int i = divShift + 1; i < shift1; i++)
                     if(iHigh(sym, tf, i) > maxAllowed) { highestValid = false; break; }
                  
                  if(highestValid)
                  {
                     double peakVolRatio = GetVolumeRatioAtShift(sym, divShift, tf);
                     if(peakVolRatio >= VolumeAutoRatio)
                     {
                        if(t2 > bestBearT2 || bestBearT2 == 0)
                        {
                           bestBearH1 = h1; bestBearH2 = h2;
                           bestBearR1 = r1; bestBearR2 = r2;
                           bestBearT1 = t1; bestBearT2 = t2;
                           bestBearIsReg = regBearDiv;
                           bestBearVol = peakVolRatio;
                        }
                     }
                  }
               }
            }
         }
      }

      if(IsRSISwingLow(arrSymTF[idx].rsiBuf, shift))
      {
         if(MathAbs(shift - lastLowShift) >= MS_SwingBars)
         {
            datetime t = iTime(sym, tf, shift);
            double rsiVal = GetRSI(arrSymTF[idx].rsiBuf, shift);
            
            int shiftRight = MathMax(0, shift - 1);
            int shiftLeft  = shift + 1;
            double l = iLow(sym, tf, shift);
            l = MathMin(l, iLow(sym, tf, shiftLeft));
            l = MathMin(l, iLow(sym, tf, shiftRight));
            
            lastLowShift = shift;
            prevLowPrice = lastLowPrice; prevLowRSI = lastLowRSI; prevLowTime = lastLowTime;
            lastLowPrice = l; lastLowRSI = rsiVal; lastLowTime = t;

            if(prevLowPrice > 0 && lastLowPrice > 0)
            {
               double p1 = prevLowPrice, p2 = lastLowPrice;
               double r1 = prevLowRSI, r2 = lastLowRSI;
               datetime t1 = prevLowTime, t2 = lastLowTime;
               bool regBullDiv = (p2 < p1) && (r2 > r1); 
               bool hidBullDiv = (p2 > p1) && (r2 < r1); 
               bool bullDiv = regBullDiv || hidBullDiv;
               int divShift = iBarShift(sym, tf, t2, false);

               if(bullDiv)
               {
                  bool lowestValid = true;
                  int shift1 = iBarShift(sym, tf, t1, false);
                  double minAllowed = MathMin(p1, p2);        
                  for(int i = divShift + 1; i < shift1; i++)
                     if(iLow(sym, tf, i) < minAllowed) { lowestValid = false; break; }
                  
                  if(lowestValid)
                  {
                     double troughVolRatio = GetVolumeRatioAtShift(sym, divShift, tf);
                     if(troughVolRatio >= VolumeAutoRatio)
                     {
                        if(t2 > bestBullT2 || bestBullT2 == 0)
                        {
                           bestBullP1 = p1; bestBullP2 = p2;
                           bestBullR1 = r1; bestBullR2 = r2;
                           bestBullT1 = t1; bestBullT2 = t2;
                           bestBullIsReg = regBullDiv;
                           bestBullVol = troughVolRatio;
                        }
                     }
                  }
               }
            }
         }
      }
   }
   
   datetime gmt7Now = GetGMT7Now();
   datetime todayStartGMT7 = gmt7Now - (gmt7Now % 86400);
   
   // XỬ LÝ HISTORICAL
   if(bestBearT2 > 0)
   {
      datetime gmt7Time = ServerTimeToGMT7(bestBearT2);
      string timeStr = TimeToString(gmt7Time, TIME_DATE|TIME_MINUTES);
      string divShort = bestBearIsReg ? "Reg" : "Hid";
      arrSymTF[idx].histBearDisplay = "Bear [" + divShort + "] (" + timeStr + " GMT+7)";
      arrSymTF[idx].histBearTime = bestBearT2;
      
      WriteHistoricalDivergence(sym, tfStr, true, bestBearIsReg,
                                bestBearT1, bestBearR1, bestBearH1,
                                bestBearT2, bestBearR2, bestBearH2, bestBearVol);
      
      ArrayResize(histLines, histCount + 1);
      ArrayResize(histTimes, histCount + 1);
      histLines[histCount] = "[" + tfStr + "] " + sym + ": " + arrSymTF[idx].histBearDisplay;
      histTimes[histCount] = bestBearT2;
      histCount++;
      
      if(gmt7Time >= todayStartGMT7)
         AddTodaySignal(sym, tfStr, "BEAR", divShort, "HIST", bestBearT2, bestBearH1, bestBearH2, bestBearR1, bestBearR2);
   }
   
   if(bestBullT2 > 0)
   {
      datetime gmt7Time = ServerTimeToGMT7(bestBullT2);
      string timeStr = TimeToString(gmt7Time, TIME_DATE|TIME_MINUTES);
      string divShort = bestBullIsReg ? "Reg" : "Hid";
      arrSymTF[idx].histBullDisplay = "Bull [" + divShort + "] (" + timeStr + " GMT+7)";
      arrSymTF[idx].histBullTime = bestBullT2;
      
      WriteHistoricalDivergence(sym, tfStr, false, bestBullIsReg,
                                bestBullT1, bestBullR1, bestBullP1,
                                bestBullT2, bestBullR2, bestBullP2, bestBullVol);
      
      ArrayResize(histLines, histCount + 1);
      ArrayResize(histTimes, histCount + 1);
      histLines[histCount] = "[" + tfStr + "] " + sym + ": " + arrSymTF[idx].histBullDisplay;
      histTimes[histCount] = bestBullT2;
      histCount++;
      
      if(gmt7Time >= todayStartGMT7)
         AddTodaySignal(sym, tfStr, "BULL", divShort, "HIST", bestBullT2, bestBullP1, bestBullP2, bestBullR1, bestBullR2);
   }
   
   arrSymTF[idx].lastHighShift = lastHighShift;
   arrSymTF[idx].lastLowShift = lastLowShift;
   arrSymTF[idx].prevHighPrice = prevHighPrice; arrSymTF[idx].lastHighPrice = lastHighPrice;
   arrSymTF[idx].prevHighRSI = prevHighRSI; arrSymTF[idx].lastHighRSI = lastHighRSI;
   arrSymTF[idx].prevHighTime = prevHighTime; arrSymTF[idx].lastHighTime = lastHighTime;
   arrSymTF[idx].prevLowPrice = prevLowPrice; arrSymTF[idx].lastLowPrice = lastLowPrice;
   arrSymTF[idx].prevLowRSI = prevLowRSI; arrSymTF[idx].lastLowRSI = lastLowRSI;
   arrSymTF[idx].prevLowTime = prevLowTime; arrSymTF[idx].lastLowTime = lastLowTime;
   arrSymTF[idx].lastShiftCached = MS_SwingBars - 1;

   datetime currentBarTimeForTrack = iTime(sym, tf, 0);

   // REALTIME BEARISH
   if(lastHighPrice > 0 && lastHighShift >= 0)
   {
      int startBear = MathMax(0, lastHighShift - 1);
      
      double rtMaxHigh = -DBL_MAX, rsiAtMaxHigh = 0; 
      datetime timeAtMaxHigh = 0;
      int rtMaxHighShift = -1;
      for(int i = startBear; i >= 0; i--) 
      {
         double curH = iHigh(sym, tf, i);
         if(curH > rtMaxHigh) { rtMaxHigh = curH; rsiAtMaxHigh = GetRSI(arrSymTF[idx].rsiBuf, i); timeAtMaxHigh = iTime(sym, tf, i); rtMaxHighShift = i; }
      }
      
      double rtMaxRSI = -DBL_MAX, priceAtMaxRSI = 0; 
      datetime timeAtMaxRSI = 0;
      int rtMaxRSIShift = -1;
      for(int i = startBear; i >= 0; i--) 
      {
         double curR = GetRSI(arrSymTF[idx].rsiBuf, i);
         if(curR > rtMaxRSI) { rtMaxRSI = curR; priceAtMaxRSI = iHigh(sym, tf, i); timeAtMaxRSI = iTime(sym, tf, i); rtMaxRSIShift = i; }
      }
      
      // Regular Bear RT
      if(rtMaxHigh > lastHighPrice && rsiAtMaxHigh < lastHighRSI && rtMaxHighShift != lastHighShift)
      {
         if(!RT_RequireMonotonicMove || IsMonotonicStructure(sym, tf, rtMaxHighShift, lastHighShift, true))
         {
            if(CheckMiddleValid(sym, tf, rtMaxHighShift, lastHighShift, lastHighTime, true))
            {
               double peakVolRatio = GetVolumeRatioAtShift(sym, rtMaxHighShift, tf);
               if(peakVolRatio >= VolumeAutoRatio)
               {
                  datetime gmt7Time = ServerTimeToGMT7(timeAtMaxHigh);
                  string timeStr = TimeToString(gmt7Time, TIME_DATE|TIME_MINUTES);
                  double closeNew = iClose(sym, tf, rtMaxHighShift);
                  double closeOld = iClose(sym, tf, iBarShift(sym, tf, lastHighTime, false));
                  bool isStrong = (closeNew > closeOld);
                  
                  double currentPrice = SymbolInfoDouble(sym, SYMBOL_BID);
                  bool shouldTrack = ShouldRetrack(currentPrice, arrSymTF[idx].lastBearRegRTPrice, sym, currentBarTimeForTrack, arrSymTF[idx].lastBearRegRTTime);
                  if(shouldTrack && !IsInTradeTime(timeAtMaxHigh)) shouldTrack = false;
                  
                  if(shouldTrack)
                  {
                     arrSymTF[idx].lastBearRegRTPrice = currentPrice;
                     arrSymTF[idx].lastBearRegRTTime = currentBarTimeForTrack;
                  }
                  
                  string strengthTag = isStrong ? " [STRONG]" : " [WEAK]";
                  
                  ArrayResize(rtLines, rtCount + 1);
                  rtLines[rtCount].text = "[SELL-RT] [" + tfStr + "] " + sym + " [Reg] (" + timeStr + " GMT+7)" + strengthTag + " [Running...]";
                  rtLines[rtCount].time = timeAtMaxHigh;
                  rtLines[rtCount].isToday = (gmt7Time >= todayStartGMT7);
                  rtCount++;
                  
                  WriteRTDivergence(sym, tfStr, true, true, lastHighTime, lastHighRSI, lastHighPrice, timeAtMaxHigh, rsiAtMaxHigh, rtMaxHigh);
                  
                  if(gmt7Time >= todayStartGMT7)
                     AddTodaySignal(sym, tfStr, "BEAR", "Reg", "RT", timeAtMaxHigh, lastHighPrice, rtMaxHigh, lastHighRSI, rsiAtMaxHigh);
                  
                  if(isStrong && timeAtMaxHigh >= g_initTime && IsNewsSafe() && shouldTrack)
                  {
                     WriteStrongSignalToGV(sym, tfStr, "SELL", currentPrice, rtMaxHigh, rtMaxHigh, lastHighPrice, true, "REG", timeAtMaxHigh);
                     string sigKey = sym + "|" + tfStr + "|SELL|Reg|" + IntegerToString(timeAtMaxHigh);
                     PrintStrongSignalOnce(arrSymTF[idx], sym, tfStr, "SELL", "Reg", currentPrice, rtMaxHigh, timeAtMaxHigh, sigKey);
                  }
                  
                  if(timeAtMaxHigh >= g_initTime && timeAtMaxHigh != arrSymTF[idx].lastBearNotifyBar)
                  {
                     datetime now2 = TimeCurrent();
                     if(EnablePushNotify && now2 - arrSymTF[idx].lastBearNotifyTime >= NotifyCooldownMinutes * 60 && IsNewsSafe())
                     {
                        SendDivergenceNotify(sym, tfStr, "Reg", "SELL", timeStr);
                        arrSymTF[idx].lastBearNotifyTime = now2;
                        arrSymTF[idx].lastBearNotifyBar = timeAtMaxHigh;
                     }
                  }
               }
            }
         }
      }
      
      // Hidden Bear RT
      if(rtMaxRSI > lastHighRSI && priceAtMaxRSI < lastHighPrice && rtMaxRSIShift != lastHighShift)
      {
         if(!RT_RequireMonotonicMove || IsMonotonicStructure(sym, tf, rtMaxRSIShift, lastHighShift, true))
         {
            if(CheckMiddleValid(sym, tf, rtMaxRSIShift, lastHighShift, lastHighTime, true))
            {
               double peakVolRatio = GetVolumeRatioAtShift(sym, rtMaxRSIShift, tf);
               if(peakVolRatio >= VolumeAutoRatio)
               {
                  datetime gmt7Time = ServerTimeToGMT7(timeAtMaxRSI);
                  string timeStr = TimeToString(gmt7Time, TIME_DATE|TIME_MINUTES);
                  double closeNew = iClose(sym, tf, rtMaxRSIShift);
                  double closeOld = iClose(sym, tf, iBarShift(sym, tf, lastHighTime, false));
                  bool isStrong = (closeNew < closeOld);
                  
                  double currentPrice = SymbolInfoDouble(sym, SYMBOL_BID);
                  bool shouldTrack = ShouldRetrack(currentPrice, arrSymTF[idx].lastBearHidRTPrice, sym, currentBarTimeForTrack, arrSymTF[idx].lastBearHidRTTime);
                  if(shouldTrack && !IsInTradeTime(timeAtMaxRSI)) shouldTrack = false;
                  
                  if(shouldTrack)
                  {
                     arrSymTF[idx].lastBearHidRTPrice = currentPrice;
                     arrSymTF[idx].lastBearHidRTTime = currentBarTimeForTrack;
                  }
                  
                  string strengthTag = isStrong ? " [STRONG]" : " [WEAK]";
                  
                  ArrayResize(rtLines, rtCount + 1);
                  rtLines[rtCount].text = "[SELL-RT] [" + tfStr + "] " + sym + " [Hid] (" + timeStr + " GMT+7)" + strengthTag + " [Running...]";
                  rtLines[rtCount].time = timeAtMaxRSI;
                  rtLines[rtCount].isToday = (gmt7Time >= todayStartGMT7);
                  rtCount++;
                  
                  WriteRTDivergence(sym, tfStr, true, false, lastHighTime, lastHighRSI, lastHighPrice, timeAtMaxRSI, rtMaxRSI, priceAtMaxRSI);
                  
                  if(gmt7Time >= todayStartGMT7)
                     AddTodaySignal(sym, tfStr, "BEAR", "Hid", "RT", timeAtMaxRSI, lastHighPrice, priceAtMaxRSI, lastHighRSI, rtMaxRSI);
                  
                  if(isStrong && timeAtMaxRSI >= g_initTime && IsNewsSafe() && shouldTrack)
                  {
                     double sl = MathMax(lastHighPrice, priceAtMaxRSI);
                     WriteStrongSignalToGV(sym, tfStr, "SELL", currentPrice, sl, lastHighPrice, lastHighPrice, true, "HID", timeAtMaxRSI);
                     string sigKey = sym + "|" + tfStr + "|SELL|Hid|" + IntegerToString(timeAtMaxRSI);
                     PrintStrongSignalOnce(arrSymTF[idx], sym, tfStr, "SELL", "Hid", currentPrice, sl, timeAtMaxRSI, sigKey);
                  }
                  
                  if(timeAtMaxRSI >= g_initTime && timeAtMaxRSI != arrSymTF[idx].lastBearNotifyBar)
                  {
                     datetime now2 = TimeCurrent();
                     if(EnablePushNotify && now2 - arrSymTF[idx].lastBearNotifyTime >= NotifyCooldownMinutes * 60 && IsNewsSafe())
                     {
                        SendDivergenceNotify(sym, tfStr, "Hid", "SELL", timeStr);
                        arrSymTF[idx].lastBearNotifyTime = now2;
                        arrSymTF[idx].lastBearNotifyBar = timeAtMaxRSI;
                     }
                  }
               }
            }
         }
      }
   }
   
   // REALTIME BULLISH
   if(lastLowPrice > 0 && lastLowShift >= 0)
   {
      int startBull = MathMax(0, lastLowShift - 1);
      
      double rtMinLow = DBL_MAX, rsiAtMinLow = 0; 
      datetime timeAtMinLow = 0;
      int rtMinLowShift = -1;
      for(int i = startBull; i >= 0; i--) 
      {
         double curL = iLow(sym, tf, i);
         if(curL < rtMinLow) { rtMinLow = curL; rsiAtMinLow = GetRSI(arrSymTF[idx].rsiBuf, i); timeAtMinLow = iTime(sym, tf, i); rtMinLowShift = i; }
      }
      
      double rtMinRSI = DBL_MAX, priceAtMinRSI = 0; 
      datetime timeAtMinRSI = 0;
      int rtMinRSIShift = -1;
      for(int i = startBull; i >= 0; i--) 
      {
         double curR = GetRSI(arrSymTF[idx].rsiBuf, i);
         if(curR < rtMinRSI) { rtMinRSI = curR; priceAtMinRSI = iLow(sym, tf, i); timeAtMinRSI = iTime(sym, tf, i); rtMinRSIShift = i; }
      }
      
      // Regular Bull RT
      if(rtMinLow < lastLowPrice && rsiAtMinLow > lastLowRSI && rtMinLowShift != lastLowShift)
      {
         if(!RT_RequireMonotonicMove || IsMonotonicStructure(sym, tf, rtMinLowShift, lastLowShift, false))
         {
            if(CheckMiddleValid(sym, tf, rtMinLowShift, lastLowShift, lastLowTime, false))
            {
               double troughVolRatio = GetVolumeRatioAtShift(sym, rtMinLowShift, tf);
               if(troughVolRatio >= VolumeAutoRatio)
               {
                  datetime gmt7Time = ServerTimeToGMT7(timeAtMinLow);
                  string timeStr = TimeToString(gmt7Time, TIME_DATE|TIME_MINUTES);
                  double closeNew = iClose(sym, tf, rtMinLowShift);
                  double closeOld = iClose(sym, tf, iBarShift(sym, tf, lastLowTime, false));
                  bool isStrong = (closeNew < closeOld);
                  
                  double currentPrice = SymbolInfoDouble(sym, SYMBOL_ASK);
                  bool shouldTrack = ShouldRetrack(currentPrice, arrSymTF[idx].lastBullRegRTPrice, sym, currentBarTimeForTrack, arrSymTF[idx].lastBullRegRTTime);
                  if(shouldTrack && !IsInTradeTime(timeAtMinLow)) shouldTrack = false;
                  
                  if(shouldTrack)
                  {
                     arrSymTF[idx].lastBullRegRTPrice = currentPrice;
                     arrSymTF[idx].lastBullRegRTTime = currentBarTimeForTrack;
                  }
                  
                  string strengthTag = isStrong ? " [STRONG]" : " [WEAK]";
                  
                  ArrayResize(rtLines, rtCount + 1);
                  rtLines[rtCount].text = "[BUY-RT] [" + tfStr + "] " + sym + " [Reg] (" + timeStr + " GMT+7)" + strengthTag + " [Running...]";
                  rtLines[rtCount].time = timeAtMinLow;
                  rtLines[rtCount].isToday = (gmt7Time >= todayStartGMT7);
                  rtCount++;
                  
                  WriteRTDivergence(sym, tfStr, false, true, lastLowTime, lastLowRSI, lastLowPrice, timeAtMinLow, rsiAtMinLow, rtMinLow);
                  
                  if(gmt7Time >= todayStartGMT7)
                     AddTodaySignal(sym, tfStr, "BULL", "Reg", "RT", timeAtMinLow, lastLowPrice, rtMinLow, lastLowRSI, rsiAtMinLow);
                  
                  if(isStrong && timeAtMinLow >= g_initTime && IsNewsSafe() && shouldTrack)
                  {
                     WriteStrongSignalToGV(sym, tfStr, "BUY", currentPrice, rtMinLow, lastHighPrice, rtMinLow, true, "REG", timeAtMinLow);
                     string sigKey = sym + "|" + tfStr + "|BUY|Reg|" + IntegerToString(timeAtMinLow);
                     PrintStrongSignalOnce(arrSymTF[idx], sym, tfStr, "BUY", "Reg", currentPrice, rtMinLow, timeAtMinLow, sigKey);
                  }
                  
                  if(timeAtMinLow >= g_initTime && timeAtMinLow != arrSymTF[idx].lastBullNotifyBar)
                  {
                     datetime now2 = TimeCurrent();
                     if(EnablePushNotify && now2 - arrSymTF[idx].lastBullNotifyTime >= NotifyCooldownMinutes * 60 && IsNewsSafe())
                     {
                        SendDivergenceNotify(sym, tfStr, "Reg", "BUY", timeStr);
                        arrSymTF[idx].lastBullNotifyTime = now2;
                        arrSymTF[idx].lastBullNotifyBar = timeAtMinLow;
                     }
                  }
               }
            }
         }
      }
      
      // Hidden Bull RT
      if(rtMinRSI < lastLowRSI && priceAtMinRSI > lastLowPrice && rtMinRSIShift != lastLowShift)
      {
         if(!RT_RequireMonotonicMove || IsMonotonicStructure(sym, tf, rtMinRSIShift, lastLowShift, false))
         {
            if(CheckMiddleValid(sym, tf, rtMinRSIShift, lastLowShift, lastLowTime, false))
            {
               double troughVolRatio = GetVolumeRatioAtShift(sym, rtMinRSIShift, tf);
               if(troughVolRatio >= VolumeAutoRatio)
               {
                  datetime gmt7Time = ServerTimeToGMT7(timeAtMinRSI);
                  string timeStr = TimeToString(gmt7Time, TIME_DATE|TIME_MINUTES);
                  double closeNew = iClose(sym, tf, rtMinRSIShift);
                  double closeOld = iClose(sym, tf, iBarShift(sym, tf, lastLowTime, false));
                  bool isStrong = (closeNew > closeOld);
                  
                  double currentPrice = SymbolInfoDouble(sym, SYMBOL_ASK);
                  bool shouldTrack = ShouldRetrack(currentPrice, arrSymTF[idx].lastBullHidRTPrice, sym, currentBarTimeForTrack, arrSymTF[idx].lastBullHidRTTime);
                  if(shouldTrack && !IsInTradeTime(timeAtMinRSI)) shouldTrack = false;
                  
                  if(shouldTrack)
                  {
                     arrSymTF[idx].lastBullHidRTPrice = currentPrice;
                     arrSymTF[idx].lastBullHidRTTime = currentBarTimeForTrack;
                  }
                  
                  string strengthTag = isStrong ? " [STRONG]" : " [WEAK]";
                  
                  ArrayResize(rtLines, rtCount + 1);
                  rtLines[rtCount].text = "[BUY-RT] [" + tfStr + "] " + sym + " [Hid] (" + timeStr + " GMT+7)" + strengthTag + " [Running...]";
                  rtLines[rtCount].time = timeAtMinRSI;
                  rtLines[rtCount].isToday = (gmt7Time >= todayStartGMT7);
                  rtCount++;
                  
                  WriteRTDivergence(sym, tfStr, false, false, lastLowTime, lastLowRSI, lastLowPrice, timeAtMinRSI, rtMinRSI, priceAtMinRSI);
                  
                  if(gmt7Time >= todayStartGMT7)
                     AddTodaySignal(sym, tfStr, "BULL", "Hid", "RT", timeAtMinRSI, lastLowPrice, priceAtMinRSI, lastLowRSI, rtMinRSI);
                  
                  if(isStrong && timeAtMinRSI >= g_initTime && IsNewsSafe() && shouldTrack)
                  {
                     double sl = MathMin(lastLowPrice, priceAtMinRSI);
                     WriteStrongSignalToGV(sym, tfStr, "BUY", currentPrice, sl, lastHighPrice, lastLowPrice, true, "HID", timeAtMinRSI);
                     string sigKey = sym + "|" + tfStr + "|BUY|Hid|" + IntegerToString(timeAtMinRSI);
                     PrintStrongSignalOnce(arrSymTF[idx], sym, tfStr, "BUY", "Hid", currentPrice, sl, timeAtMinRSI, sigKey);
                  }
                  
                  if(timeAtMinRSI >= g_initTime && timeAtMinRSI != arrSymTF[idx].lastBullNotifyBar)
                  {
                     datetime now2 = TimeCurrent();
                     if(EnablePushNotify && now2 - arrSymTF[idx].lastBullNotifyTime >= NotifyCooldownMinutes * 60 && IsNewsSafe())
                     {
                        SendDivergenceNotify(sym, tfStr, "Hid", "BUY", timeStr);
                        arrSymTF[idx].lastBullNotifyTime = now2;
                        arrSymTF[idx].lastBullNotifyBar = timeAtMinRSI;
                     }
                  }
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| ONCALCULATE                                                       |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[], const double &open[], const double &high[], const double &low[], const double &close[], const long &tick_volume[], const long &volume[], const int &spread[])
{
   return rates_total;
}
//+------------------------------------------------------------------+
void PublishTodaySignals()
{
   int h=FileOpen("DIVTODAY_SIGNALS.txt",FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_SHARE_READ);
   if(h==INVALID_HANDLE) return;
   datetime nowGMT7=GetGMT7Now();
   FileWriteString(h,"GMT7|"+IntegerToString((int)nowGMT7)+"\r\n");
   for(int i=0;i<g_todaySignalCount;i++)
   {
      string direction=(g_todaySignals[i].type=="BEAR")?"SELL":"BUY";
      string line=StringFormat("%s|%s|%s|%s|%s|%d|%.8f|%.8f|%.2f|%.2f\r\n",
         g_todaySignals[i].sym,g_todaySignals[i].tfStr,direction,g_todaySignals[i].divType,
         g_todaySignals[i].signalType,(int)g_todaySignals[i].time,
         g_todaySignals[i].price1,g_todaySignals[i].price2,
         g_todaySignals[i].rsi1,g_todaySignals[i].rsi2);
      FileWriteString(h,line);
   }
   FileClose(h);
}

void CleanupInvalidRTTodaySignals()
{
   for(int i=g_todaySignalCount-1;i>=0;i--)
   {
      if(g_todaySignals[i].signalType=="RT")
      {
         string prefix="DIVDATA_"+g_todaySignals[i].sym+"_"+g_todaySignals[i].tfStr+"_";
         string type=g_todaySignals[i].type;
         if(!GlobalVariableCheck(prefix+type+"_ACTIVE")||GlobalVariableGet(prefix+type+"_ACTIVE")==0)
         {
            if(i<g_todaySignalCount-1)
               g_todaySignals[i]=g_todaySignals[g_todaySignalCount-1];
            g_todaySignalCount--;
         }
      }
   }
}

void ClearOldTodaySignals()
{
   datetime gmt7Now=GetGMT7Now();
   datetime todayStartGMT7=gmt7Now-(gmt7Now%86400);
   int validCount=0;
   for(int i=0;i<g_todaySignalCount;i++)
   {
      datetime signalGMT7=ServerTimeToGMT7(g_todaySignals[i].time);
      if(signalGMT7>=todayStartGMT7)
      {
         if(validCount!=i)
            g_todaySignals[validCount]=g_todaySignals[i];
         validCount++;
      }
   }
   g_todaySignalCount=validCount;
}

string BuildTodaySignalsDisplay()
{
   CleanupInvalidRTTodaySignals();
   
   string display="\nTODAY SIGNALS (GMT+7) - "+IntegerToString(g_todaySignalCount)+" signals\n";
   display+=" \n";
   
   if(g_todaySignalCount > 0)
   {
      for(int i=0;i<g_todaySignalCount-1;i++)
         for(int j=i+1;j<g_todaySignalCount;j++)
            if(ServerTimeToGMT7(g_todaySignals[i].time)<ServerTimeToGMT7(g_todaySignals[j].time))
            {
               STodaySignal tmp=g_todaySignals[i];
               g_todaySignals[i]=g_todaySignals[j];
               g_todaySignals[j]=tmp;
            }
      for(int i=0;i<g_todaySignalCount;i++)
      {
         string icon=(g_todaySignals[i].type=="BEAR")?"[SELL]":"[BUY]";
         string sigTag=(g_todaySignals[i].signalType=="RT")?" [RT]":"";
         datetime gmt7Time=ServerTimeToGMT7(g_todaySignals[i].time);
         string timeStr=TimeToString(gmt7Time,TIME_MINUTES);
         display+=StringFormat("%s%s %s %s [%s] %s\n",icon,sigTag,g_todaySignals[i].sym,g_todaySignals[i].tfStr,g_todaySignals[i].divType,timeStr);
      }
   }
   else
   {
      display+="No signals yet\n";
   }
   
   display+="----------------------------------------\n";
   return display;
}

//+------------------------------------------------------------------+
//| DISPLAY FUNCTIONS (GIỮ NGUYÊN)                                     |
//+------------------------------------------------------------------+
void Draw(string divergenceComment)
{ 
   for(int i=ObjectsTotal(0)-1;i>=0;i--)
   {
      string name=ObjectName(0,i);
      if(StringFind(name,P)==0)
         ObjectDelete(0,name);
   }
   
   int rowH=18;
   int y=topMargin;
   datetime gmt7Time=GetGMT7Now();
   string timeStr=TimeToString(gmt7Time,TIME_MINUTES|TIME_SECONDS);
   string heartbeatChar=g_heartbeatState?"*":".";
   
   string tfListStr="";
   if(EnableM15) tfListStr+="M15 ";
   if(EnableH1) tfListStr+="H1 ";
   
   string timeFilterStr=EnableTimeFilter?" | "+IntegerToString(ScanStartHour)+"h-"+IntegerToString(ScanEndHour)+"h":" | 24/7";
   string signalStatus=EnableSignalOutput?"SIG:ON":"SIG:OFF";
   string notifyStatus=EnablePushNotify?"PUSH:ON":"PUSH:OFF";
   string monoStatus=RT_RequireMonotonicMove?"MONO:ON":"MONO:OFF";
   string newsStatus="";
   if(EnableNewsFilter)
      newsStatus=g_isNewsBlocked?"NEWS:BLOCKED":"NEWS:SAFE";
   
   string line1Status=StringFormat("[%s] IND: %s | %s | %s (GMT+7)%s",
                                    heartbeatChar,g_statusText,tfListStr,timeStr,timeFilterStr);
   UpdMsg("HEARTBEAT",line1Status,startX,y,g_statusColor,HeaderFontSize,true);
   y+=rowH+2;
   
   string line2Features=StringFormat("%s | %s | %s | %s",
                                      signalStatus,notifyStatus,monoStatus,newsStatus);
   color line2Color=g_isNewsBlocked?clrOrangeRed:clrDimGray;
   UpdMsg("FEATURES",line2Features,startX,y,line2Color,9,false);
   y+=rowH+6;
   
   UpdMsg("LINE_DECOR_1","================================================================================",startX,y,clrDarkGray,8);
   y+=10;
   
   if(divergenceComment!=""&&StringFind(divergenceComment,"CRASHED")<0)
   {
      string divLines[];
      int divCount=StringSplit(divergenceComment,'\n',divLines);
      bool inHistorical=false,inToday=false;
      int validLineCount=0;
      
      for(int i=0;i<divCount;i++)
      {
         if(divLines[i]=="")continue;
         
         if(StringFind(divLines[i],"[M15]")>=0||StringFind(divLines[i],"[H1]")>=0)
         {
            inHistorical=true;
            y+=4;
         }
         if(StringFind(divLines[i],"TODAY")>=0)
         {
            inHistorical=false;inToday=true;
            y+=4;
         }
         
         color c=clrLightBlue;
         int fontSize=RT_FontSize;
         
         if(inToday)
         {
            fontSize=TODAY_FontSize;
            if(StringFind(divLines[i],"[SELL]")>=0)
               c=clrLightCoral;
            else if(StringFind(divLines[i],"[BUY]")>=0)
               c=clrMediumAquamarine;
            else if(StringFind(divLines[i],"TODAY")>=0)
               c=clrOrange;
            else
               c=clrDarkGray;
         }
         else if(inHistorical)
         {
            fontSize=HIST_FontSize;
            if(StringFind(divLines[i],"Bear")>=0)
               c=clrPaleVioletRed;
            else if(StringFind(divLines[i],"Bull")>=0)
               c=clrDarkSeaGreen;
            else
               c=clrGold;
         }
         else
         {
            fontSize=RT_FontSize;
            if(StringFind(divLines[i],"SELL")>=0)
               c=clrDeepPink;
            else if(StringFind(divLines[i],"BUY")>=0)
               c=clrMediumSpringGreen;
            else if(StringFind(divLines[i],"---")>=0)
               c=clrCadetBlue;
         }
         
         UpdMsg("DIV_"+IntegerToString(validLineCount),divLines[i],startX,y,c,fontSize);
         y+=(inToday||inHistorical)?rowH-4:rowH;
         validLineCount++;
      }
      Cleanup(validLineCount);
   }
   else
   {
      Cleanup(0);
   }
}

void UpdMsg(string n, string t, int x, int y, color c, int fontSize=11, bool isBold=false)
{
   string o=P+n;
   if(ObjectFind(0,o)<0)
      ObjectCreate(0,o,OBJ_LABEL,0,0,0);
   ObjectSetString(0,o,OBJPROP_FONT,"Consolas");
   ObjectSetInteger(0,o,OBJPROP_FONTSIZE,fontSize);
   ObjectSetInteger(0,o,OBJPROP_CORNER,CORNER_LEFT_UPPER);
   ObjectSetInteger(0,o,OBJPROP_COLOR,c);
   ObjectSetInteger(0,o,OBJPROP_XDISTANCE,x);
   ObjectSetInteger(0,o,OBJPROP_YDISTANCE,y);
   ObjectSetString(0,o,OBJPROP_TEXT,t);
   ObjectSetInteger(0,o,OBJPROP_ANCHOR,ANCHOR_LEFT_UPPER);
   ObjectSetInteger(0,o,OBJPROP_SELECTABLE,false);
}

void Cleanup(int validLineCount)
{ 
   for(int i=ObjectsTotal(0)-1;i>=0;i--)
   {
      string name=ObjectName(0,i);
      if(StringFind(name,P)!=0)continue;
      if(StringFind(name,P+"HEARTBEAT")==0)continue;
      if(StringFind(name,P+"FEATURES")==0)continue;
      if(StringFind(name,P+"LINE_DECOR_")==0)continue;
      if(StringFind(name,P+"DIV_")==0)
      {
         int idx_obj=(int)StringToInteger(StringSubstr(name,StringLen(P+"DIV_")));
         if(idx_obj>=validLineCount)ObjectDelete(0,name);
      }
      else ObjectDelete(0,name);
   }
}

void ClearAll()
{
   for(int i=ObjectsTotal(0)-1;i>=0;i--)
   {
      string n=ObjectName(0,i);
      if(StringFind(n,P)==0)
         ObjectDelete(0,n);
   }
}

//+------------------------------------------------------------------+
//| ONINIT                                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   datetime ct = TimeCurrent();
   if(ct > expiry)
   {
      MessageBox("Expired!","EXPIRED",MB_OK|MB_ICONSTOP);
      return INIT_FAILED;
   }
   
   EventSetTimer(1);
   CalculateServerGMTOffset();
   UpdateTodayBounds();
   g_lastUpdateTime = ct;
   g_heartbeatCounter = 0;
   g_firstScanDone = false;
   g_initTime = ct;
   g_statusText = "KHỞI TẠO...";
   g_statusColor = clrDimGray;
   g_lastScanM15 = g_lastScanH1 = 0;
   g_lastRTPrintTime = g_lastRTCount = 0;
   g_isNewsBlocked = false;
   g_lastNewsCheckTime = 0;
   ArrayResize(g_todaySignals, 0);
   g_todaySignalCount = 0;
   CleanupOldSignals();
   
   string tfList = "";
   if(EnableM15) tfList += "M15 ";
   if(EnableH1) tfList += "H1 ";
   if(tfList == "") tfList = "NONE!";
   
   Print("═══════════════════════════════════════════");
   Print("RSI Divergence Realtime v1.0");
   Print("   Timeframes: ", tfList);
   Print("═══════════════════════════════════════════");
   
   g_statusText = "RUNNING";
   g_statusColor = clrTeal;
   
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| ONTIMER                                                           |
//+------------------------------------------------------------------+
void PublishScanSymbols(string symbols)
{
   int h=FileOpen("DIVSCAN_SYMBOLS.txt",FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(h==INVALID_HANDLE) return;
   FileWriteString(h,IntegerToString((int)TimeCurrent())+"#"+symbols);
   FileClose(h);
}

void OnTimer()
{
   GlobalVariableSet("HB_RSI_Indicator",(double)TimeCurrent());
   g_lastUpdateTime=TimeCurrent();
   g_heartbeatCounter++;
   if(g_heartbeatCounter%2==0) g_heartbeatState=!g_heartbeatState;
   
   ClearOldTodaySignals();
   UpdateTodayBounds();
   
   if(EnableNewsFilter && TimeCurrent()-g_lastNewsCheckTime>=NewsCheckIntervalSeconds)
   {
      bool wasBlocked=g_isNewsBlocked;
      g_isNewsBlocked=!IsNewsSafe();
      g_lastNewsCheckTime=TimeCurrent();
   }
   
   if(g_isNewsBlocked)
   {
      PublishScanSymbols("");
      g_statusColor=clrOrangeRed;
      g_statusText="NEWS BLOCKED";
      Draw("NEWS BLOCKED");
      return;
   }
   
   bool isScanTime=IsInScanTime();
   bool has_crash=false;
   
   if(g_firstScanDone)
   {
      if(ArraySize(arrSymTF)==0)
         has_crash=true;
      else
      {
         bool allInvalid=true;
         for(int i=0;i<ArraySize(arrSymTF);i++)
            if(arrSymTF[i].rsiHandle!=INVALID_HANDLE)
            {allInvalid=false;break;}
         if(allInvalid) has_crash=true;
         if(!has_crash && !TerminalInfoInteger(TERMINAL_CONNECTED))
            has_crash=true;
      }
   }
   
   if(has_crash)
   {
      PublishScanSymbols("");
      g_statusColor=clrRed;
      g_statusText="CRASHED!";
      Draw("CRASHED");
      return;
   }
   
   if(!isScanTime)
   {
      PublishScanSymbols("");
      g_statusColor=clrGray;
      g_statusText="STANDBY";
      Draw("STANDBY");
      return;
   }
   
   g_statusColor=clrAquamarine;
   g_statusText="RUNNING";
   CleanupOldSignals();
   
   datetime now=TimeCurrent();
   bool sM15=false,sH1=false;
   if(EnableM15) sM15=true;
   if(EnableH1 && now-g_lastScanH1>=5) { sH1=true; g_lastScanH1=now; }
   
   if(!sM15 && !sH1)
   {
      string td=BuildTodaySignalsDisplay();
      string dm=g_latestRTSignal==""?"Scanning...":g_latestRTSignal;
      Draw(dm+td);
      return;
   }
   
   SRTLine rtLines[];
   int rtCount=0;
   string histLines[];
   datetime histTimes[];
   int histCount=0;
   
   string scannedSymbols="";
   int totalSymbols=SymbolsTotal(true);
   for(int s=0; s<totalSymbols; s++)
   {
      string sym=SymbolName(s,true);
      if(IsSymbolExcluded(sym)) continue;
      
      if(EnableM15)
      {
         int idx=GetSymbolTFIndex(sym,PERIOD_M15);
         if(idx==-1) idx=InitSymTF(sym,PERIOD_M15,"M15");
         if(sM15)
         {
            ProcessSymbolTF(idx,rtLines,rtCount,histLines,histTimes,histCount);
            ClearInactiveRTDivergence(sym,"M15",PERIOD_M15);
         }
         if(scannedSymbols!="") scannedSymbols+=";";
         scannedSymbols+=sym+"|M15";
      }
      
      if(EnableH1)
      {
         int idx=GetSymbolTFIndex(sym,PERIOD_H1);
         if(idx==-1) idx=InitSymTF(sym,PERIOD_H1,"H1");
         if(sH1)
         {
            ProcessSymbolTF(idx,rtLines,rtCount,histLines,histTimes,histCount);
            ClearInactiveRTDivergence(sym,"H1",PERIOD_H1);
         }
         if(scannedSymbols!="") scannedSymbols+=";";
         scannedSymbols+=sym+"|H1";
      }
   }
   PublishScanSymbols(scannedSymbols);
   
   string dc="";
   static string slr="";
   
   if(rtCount>0)
   {
      for(int i=0; i<rtCount-1; i++)
         for(int j=i+1; j<rtCount; j++)
            if(rtLines[i].time>rtLines[j].time)
            {
               SRTLine t=rtLines[i];
               rtLines[i]=rtLines[j];
               rtLines[j]=t;
            }
      string gc="--- RT SIGNALS (1/Bar) ---\n\n";
      for(int i=0; i<rtCount; i++)
         gc+=rtLines[i].text+"\n";
      g_latestRTSignal=gc;
      dc=gc;
      if(gc!=slr)
      {
         PrintRTSignalChange(rtCount,gc);
         slr=gc;
      }
   }
   else
   {
      g_latestRTSignal="";
      dc="Scanning...";
      slr="";
      g_lastRTCount=0;
   }
   
   if(histCount>0)
   {
      for(int i=0; i<histCount-1; i++)
         for(int j=i+1; j<histCount; j++)
            if(histTimes[i]<histTimes[j])
            {
               datetime t=histTimes[i];
               histTimes[i]=histTimes[j];
               histTimes[j]=t;
               string tl=histLines[i];
               histLines[i]=histLines[j];
               histLines[j]=tl;
            }
      for(int i=0; i<histCount; i++)
         dc+="\n"+histLines[i];
   }
   
   string td=BuildTodaySignalsDisplay();
   dc+=td;
   Draw(dc);
   g_firstScanDone=true;
}

//+------------------------------------------------------------------+
//| ONDEINIT                                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   ClearAll();
   
   ENUM_TIMEFRAMES tfs[];
   string tfStrs[];
   GetEnabledTimeframes(tfs,tfStrs);
   for(int s=0;s<SymbolsTotal(true);s++)
   {
      string sym=SymbolName(s,true);
      for(int t=0;t<ArraySize(tfs);t++)
         ClearAllDivergenceData(sym,tfStrs[t]);
   }
   
   for(int i=0;i<ArraySize(arrSymTF);i++)
      if(arrSymTF[i].rsiHandle!=INVALID_HANDLE)
         IndicatorRelease(arrSymTF[i].rsiHandle);
   
   CleanupOldSignals();
}

//+------------------------------------------------------------------+
//| PROCESS SYMBOL TF (GIỮ NGUYÊN LOGIC GỐC)                           |
//+------------------------------------------------------------------+
void ProcessSymbolTF(int idx, SRTLine &rtLines[], int &rtCount, string &histLines[], datetime &histTimes[], int &histCount)
{
   string sym = arrSymTF[idx].sym;
   ENUM_TIMEFRAMES tf = arrSymTF[idx].tf;
   string tfStr = arrSymTF[idx].tfStr;
   int rsiHandle = arrSymTF[idx].rsiHandle;
   
   if(rsiHandle == INVALID_HANDLE) return;

   int readCount = MS_BARS_DEFAULT + 50;
   if(CopyBuffer(rsiHandle, 0, 0, readCount, arrSymTF[idx].rsiBuf) < readCount) return;

   datetime currentBarTime = iTime(sym, tf, 0);
   if(currentBarTime != arrSymTF[idx].lastBarTime)
   {
      if(arrSymTF[idx].lastBarTime != 0 && arrSymTF[idx].lastShiftCached < MS_SwingBars) 
         arrSymTF[idx].lastShiftCached = MS_SwingBars; 
      arrSymTF[idx].lastBarTime = currentBarTime;
   }

   double bestBearH1 = 0, bestBearH2 = 0, bestBearR1 = 0, bestBearR2 = 0;
   datetime bestBearT1 = 0, bestBearT2 = 0;
   bool    bestBearIsReg = false;
   double  bestBearVol = 0;
   
   double bestBullP1 = 0, bestBullP2 = 0, bestBullR1 = 0, bestBullR2 = 0;
   datetime bestBullT1 = 0, bestBullT2 = 0;
   bool    bestBullIsReg = false;
   double  bestBullVol = 0;

   int scanLimit = MathMax(arrSymTF[idx].lastShiftCached, MS_SwingBars);
   
   double prevHighPrice = 0, lastHighPrice = 0, prevHighRSI = 0, lastHighRSI = 0;
   datetime prevHighTime = 0, lastHighTime = 0;
   double prevLowPrice = 0, lastLowPrice = 0, prevLowRSI = 0, lastLowRSI = 0;
   datetime prevLowTime = 0, lastLowTime = 0;
   int lastHighShift = -9999, lastLowShift = -9999;

   // QUÉT SWING POINTS
   for(int shift = scanLimit; shift >= MS_SwingBars; shift--)
   {
      if(IsRSISwingHigh(arrSymTF[idx].rsiBuf, shift))
      {
         if(MathAbs(shift - lastHighShift) >= MS_SwingBars)
         {
            datetime t = iTime(sym, tf, shift);
            double rsiVal = GetRSI(arrSymTF[idx].rsiBuf, shift);
            
            int shiftRight = MathMax(0, shift - 1);
            int shiftLeft  = shift + 1;
            double h = iHigh(sym, tf, shift); 
            h = MathMax(h, iHigh(sym, tf, shiftLeft));
            h = MathMax(h, iHigh(sym, tf, shiftRight));
            
            lastHighShift = shift;
            prevHighPrice = lastHighPrice; prevHighRSI = lastHighRSI; prevHighTime = lastHighTime;
            lastHighPrice = h; lastHighRSI = rsiVal; lastHighTime = t;

            if(prevHighPrice > 0 && lastHighPrice > 0)
            {
               double h1 = prevHighPrice, h2 = lastHighPrice;
               double r1 = prevHighRSI, r2 = lastHighRSI;
               datetime t1 = prevHighTime, t2 = lastHighTime;
               bool regBearDiv = (h2 > h1) && (r2 < r1); 
               bool hidBearDiv = (h2 < h1) && (r2 > r1); 
               bool bearDiv = regBearDiv || hidBearDiv;
               int divShift = iBarShift(sym, tf, t2, false);

               if(bearDiv)
               {
                  bool highestValid = true;
                  int shift1 = iBarShift(sym, tf, t1, false);
                  double maxAllowed = MathMax(h1, h2);        
                  for(int i = divShift + 1; i < shift1; i++)
                     if(iHigh(sym, tf, i) > maxAllowed) { highestValid = false; break; }
                  
                  if(highestValid)
                  {
                     double peakVolRatio = GetVolumeRatioAtShift(sym, divShift, tf);
                     if(peakVolRatio >= VolumeAutoRatio)
                     {
                        if(t2 > bestBearT2 || bestBearT2 == 0)
                        {
                           bestBearH1 = h1; bestBearH2 = h2;
                           bestBearR1 = r1; bestBearR2 = r2;
                           bestBearT1 = t1; bestBearT2 = t2;
                           bestBearIsReg = regBearDiv;
                           bestBearVol = peakVolRatio;
                        }
                     }
                  }
               }
            }
         }
      }

      if(IsRSISwingLow(arrSymTF[idx].rsiBuf, shift))
      {
         if(MathAbs(shift - lastLowShift) >= MS_SwingBars)
         {
            datetime t = iTime(sym, tf, shift);
            double rsiVal = GetRSI(arrSymTF[idx].rsiBuf, shift);
            
            int shiftRight = MathMax(0, shift - 1);
            int shiftLeft  = shift + 1;
            double l = iLow(sym, tf, shift);
            l = MathMin(l, iLow(sym, tf, shiftLeft));
            l = MathMin(l, iLow(sym, tf, shiftRight));
            
            lastLowShift = shift;
            prevLowPrice = lastLowPrice; prevLowRSI = lastLowRSI; prevLowTime = lastLowTime;
            lastLowPrice = l; lastLowRSI = rsiVal; lastLowTime = t;

            if(prevLowPrice > 0 && lastLowPrice > 0)
            {
               double p1 = prevLowPrice, p2 = lastLowPrice;
               double r1 = prevLowRSI, r2 = lastLowRSI;
               datetime t1 = prevLowTime, t2 = lastLowTime;
               bool regBullDiv = (p2 < p1) && (r2 > r1); 
               bool hidBullDiv = (p2 > p1) && (r2 < r1); 
               bool bullDiv = regBullDiv || hidBullDiv;
               int divShift = iBarShift(sym, tf, t2, false);

               if(bullDiv)
               {
                  bool lowestValid = true;
                  int shift1 = iBarShift(sym, tf, t1, false);
                  double minAllowed = MathMin(p1, p2);        
                  for(int i = divShift + 1; i < shift1; i++)
                     if(iLow(sym, tf, i) < minAllowed) { lowestValid = false; break; }
                  
                  if(lowestValid)
                  {
                     double troughVolRatio = GetVolumeRatioAtShift(sym, divShift, tf);
                     if(troughVolRatio >= VolumeAutoRatio)
                     {
                        if(t2 > bestBullT2 || bestBullT2 == 0)
                        {
                           bestBullP1 = p1; bestBullP2 = p2;
                           bestBullR1 = r1; bestBullR2 = r2;
                           bestBullT1 = t1; bestBullT2 = t2;
                           bestBullIsReg = regBullDiv;
                           bestBullVol = troughVolRatio;
                        }
                     }
                  }
               }
            }
         }
      }
   }
   
   datetime gmt7Now = GetGMT7Now();
   datetime todayStartGMT7 = gmt7Now - (gmt7Now % 86400);
   
   // XỬ LÝ HISTORICAL
   if(bestBearT2 > 0)
   {
      datetime gmt7Time = ServerTimeToGMT7(bestBearT2);
      string timeStr = TimeToString(gmt7Time, TIME_DATE|TIME_MINUTES);
      string divShort = bestBearIsReg ? "Reg" : "Hid";
      arrSymTF[idx].histBearDisplay = "Bear [" + divShort + "] (" + timeStr + " GMT+7)";
      arrSymTF[idx].histBearTime = bestBearT2;
      
      WriteHistoricalDivergence(sym, tfStr, true, bestBearIsReg,
                                bestBearT1, bestBearR1, bestBearH1,
                                bestBearT2, bestBearR2, bestBearH2, bestBearVol);
      
      ArrayResize(histLines, histCount + 1);
      ArrayResize(histTimes, histCount + 1);
      histLines[histCount] = "[" + tfStr + "] " + sym + ": " + arrSymTF[idx].histBearDisplay;
      histTimes[histCount] = bestBearT2;
      histCount++;
      
      if(gmt7Time >= todayStartGMT7)
         AddTodaySignal(sym, tfStr, "BEAR", divShort, "HIST", bestBearT2, bestBearH1, bestBearH2, bestBearR1, bestBearR2);
   }
   
   if(bestBullT2 > 0)
   {
      datetime gmt7Time = ServerTimeToGMT7(bestBullT2);
      string timeStr = TimeToString(gmt7Time, TIME_DATE|TIME_MINUTES);
      string divShort = bestBullIsReg ? "Reg" : "Hid";
      arrSymTF[idx].histBullDisplay = "Bull [" + divShort + "] (" + timeStr + " GMT+7)";
      arrSymTF[idx].histBullTime = bestBullT2;
      
      WriteHistoricalDivergence(sym, tfStr, false, bestBullIsReg,
                                bestBullT1, bestBullR1, bestBullP1,
                                bestBullT2, bestBullR2, bestBullP2, bestBullVol);
      
      ArrayResize(histLines, histCount + 1);
      ArrayResize(histTimes, histCount + 1);
      histLines[histCount] = "[" + tfStr + "] " + sym + ": " + arrSymTF[idx].histBullDisplay;
      histTimes[histCount] = bestBullT2;
      histCount++;
      
      if(gmt7Time >= todayStartGMT7)
         AddTodaySignal(sym, tfStr, "BULL", divShort, "HIST", bestBullT2, bestBullP1, bestBullP2, bestBullR1, bestBullR2);
   }
   
   arrSymTF[idx].lastHighShift = lastHighShift;
   arrSymTF[idx].lastLowShift = lastLowShift;
   arrSymTF[idx].prevHighPrice = prevHighPrice; arrSymTF[idx].lastHighPrice = lastHighPrice;
   arrSymTF[idx].prevHighRSI = prevHighRSI; arrSymTF[idx].lastHighRSI = lastHighRSI;
   arrSymTF[idx].prevHighTime = prevHighTime; arrSymTF[idx].lastHighTime = lastHighTime;
   arrSymTF[idx].prevLowPrice = prevLowPrice; arrSymTF[idx].lastLowPrice = lastLowPrice;
   arrSymTF[idx].prevLowRSI = prevLowRSI; arrSymTF[idx].lastLowRSI = lastLowRSI;
   arrSymTF[idx].prevLowTime = prevLowTime; arrSymTF[idx].lastLowTime = lastLowTime;
   arrSymTF[idx].lastShiftCached = MS_SwingBars - 1;

   datetime currentBarTimeForTrack = iTime(sym, tf, 0);

   // REALTIME BEARISH
   if(lastHighPrice > 0 && lastHighShift >= 0)
   {
      int startBear = MathMax(0, lastHighShift - 1);
      
      double rtMaxHigh = -DBL_MAX, rsiAtMaxHigh = 0; 
      datetime timeAtMaxHigh = 0;
      int rtMaxHighShift = -1;
      for(int i = startBear; i >= 0; i--) 
      {
         double curH = iHigh(sym, tf, i);
         if(curH > rtMaxHigh) { rtMaxHigh = curH; rsiAtMaxHigh = GetRSI(arrSymTF[idx].rsiBuf, i); timeAtMaxHigh = iTime(sym, tf, i); rtMaxHighShift = i; }
      }
      
      double rtMaxRSI = -DBL_MAX, priceAtMaxRSI = 0; 
      datetime timeAtMaxRSI = 0;
      int rtMaxRSIShift = -1;
      for(int i = startBear; i >= 0; i--) 
      {
         double curR = GetRSI(arrSymTF[idx].rsiBuf, i);
         if(curR > rtMaxRSI) { rtMaxRSI = curR; priceAtMaxRSI = iHigh(sym, tf, i); timeAtMaxRSI = iTime(sym, tf, i); rtMaxRSIShift = i; }
      }
      
      // Regular Bear RT
      if(rtMaxHigh > lastHighPrice && rsiAtMaxHigh < lastHighRSI && rtMaxHighShift != lastHighShift)
      {
         if(!RT_RequireMonotonicMove || IsMonotonicStructure(sym, tf, rtMaxHighShift, lastHighShift, true))
         {
            if(CheckMiddleValid(sym, tf, rtMaxHighShift, lastHighShift, lastHighTime, true))
            {
               double peakVolRatio = GetVolumeRatioAtShift(sym, rtMaxHighShift, tf);
               if(peakVolRatio >= VolumeAutoRatio)
               {
                  datetime gmt7Time = ServerTimeToGMT7(timeAtMaxHigh);
                  string timeStr = TimeToString(gmt7Time, TIME_DATE|TIME_MINUTES);
                  double closeNew = iClose(sym, tf, rtMaxHighShift);
                  double closeOld = iClose(sym, tf, iBarShift(sym, tf, lastHighTime, false));
                  bool isStrong = (closeNew > closeOld);
                  
                  double currentPrice = SymbolInfoDouble(sym, SYMBOL_BID);
                  bool shouldTrack = ShouldRetrack(currentPrice, arrSymTF[idx].lastBearRegRTPrice, sym, currentBarTimeForTrack, arrSymTF[idx].lastBearRegRTTime);
                  if(shouldTrack && !IsInTradeTime(timeAtMaxHigh)) shouldTrack = false;
                  
                  if(shouldTrack)
                  {
                     arrSymTF[idx].lastBearRegRTPrice = currentPrice;
                     arrSymTF[idx].lastBearRegRTTime = currentBarTimeForTrack;
                  }
                  
                  string strengthTag = isStrong ? " [STRONG]" : " [WEAK]";
                  
                  ArrayResize(rtLines, rtCount + 1);
                  rtLines[rtCount].text = "[SELL-RT] [" + tfStr + "] " + sym + " [Reg] (" + timeStr + " GMT+7)" + strengthTag + " [Running...]";
                  rtLines[rtCount].time = timeAtMaxHigh;
                  rtLines[rtCount].isToday = (gmt7Time >= todayStartGMT7);
                  rtCount++;
                  
                  WriteRTDivergence(sym, tfStr, true, true, lastHighTime, lastHighRSI, lastHighPrice, timeAtMaxHigh, rsiAtMaxHigh, rtMaxHigh);
                  
                  if(gmt7Time >= todayStartGMT7)
                     AddTodaySignal(sym, tfStr, "BEAR", "Reg", "RT", timeAtMaxHigh, lastHighPrice, rtMaxHigh, lastHighRSI, rsiAtMaxHigh);
                  
                  if(isStrong && timeAtMaxHigh >= g_initTime && IsNewsSafe() && shouldTrack)
                  {
                     WriteStrongSignalToGV(sym, tfStr, "SELL", currentPrice, rtMaxHigh, rtMaxHigh, lastHighPrice, true, "REG", timeAtMaxHigh);
                     string sigKey = sym + "|" + tfStr + "|SELL|Reg|" + IntegerToString(timeAtMaxHigh);
                     PrintStrongSignalOnce(arrSymTF[idx], sym, tfStr, "SELL", "Reg", currentPrice, rtMaxHigh, timeAtMaxHigh, sigKey);
                  }
                  
                  if(timeAtMaxHigh >= g_initTime && timeAtMaxHigh != arrSymTF[idx].lastBearNotifyBar)
                  {
                     datetime now2 = TimeCurrent();
                     if(EnablePushNotify && now2 - arrSymTF[idx].lastBearNotifyTime >= NotifyCooldownMinutes * 60 && IsNewsSafe())
                     {
                        SendDivergenceNotify(sym, tfStr, "Reg", "SELL", timeStr);
                        arrSymTF[idx].lastBearNotifyTime = now2;
                        arrSymTF[idx].lastBearNotifyBar = timeAtMaxHigh;
                     }
                  }
               }
            }
         }
      }
      
      // Hidden Bear RT
      if(rtMaxRSI > lastHighRSI && priceAtMaxRSI < lastHighPrice && rtMaxRSIShift != lastHighShift)
      {
         if(!RT_RequireMonotonicMove || IsMonotonicStructure(sym, tf, rtMaxRSIShift, lastHighShift, true))
         {
            if(CheckMiddleValid(sym, tf, rtMaxRSIShift, lastHighShift, lastHighTime, true))
            {
               double peakVolRatio = GetVolumeRatioAtShift(sym, rtMaxRSIShift, tf);
               if(peakVolRatio >= VolumeAutoRatio)
               {
                  datetime gmt7Time = ServerTimeToGMT7(timeAtMaxRSI);
                  string timeStr = TimeToString(gmt7Time, TIME_DATE|TIME_MINUTES);
                  double closeNew = iClose(sym, tf, rtMaxRSIShift);
                  double closeOld = iClose(sym, tf, iBarShift(sym, tf, lastHighTime, false));
                  bool isStrong = (closeNew < closeOld);
                  
                  double currentPrice = SymbolInfoDouble(sym, SYMBOL_BID);
                  bool shouldTrack = ShouldRetrack(currentPrice, arrSymTF[idx].lastBearHidRTPrice, sym, currentBarTimeForTrack, arrSymTF[idx].lastBearHidRTTime);
                  if(shouldTrack && !IsInTradeTime(timeAtMaxRSI)) shouldTrack = false;
                  
                  if(shouldTrack)
                  {
                     arrSymTF[idx].lastBearHidRTPrice = currentPrice;
                     arrSymTF[idx].lastBearHidRTTime = currentBarTimeForTrack;
                  }
                  
                  string strengthTag = isStrong ? " [STRONG]" : " [WEAK]";
                  
                  ArrayResize(rtLines, rtCount + 1);
                  rtLines[rtCount].text = "[SELL-RT] [" + tfStr + "] " + sym + " [Hid] (" + timeStr + " GMT+7)" + strengthTag + " [Running...]";
                  rtLines[rtCount].time = timeAtMaxRSI;
                  rtLines[rtCount].isToday = (gmt7Time >= todayStartGMT7);
                  rtCount++;
                  
                  WriteRTDivergence(sym, tfStr, true, false, lastHighTime, lastHighRSI, lastHighPrice, timeAtMaxRSI, rtMaxRSI, priceAtMaxRSI);
                  
                  if(gmt7Time >= todayStartGMT7)
                     AddTodaySignal(sym, tfStr, "BEAR", "Hid", "RT", timeAtMaxRSI, lastHighPrice, priceAtMaxRSI, lastHighRSI, rtMaxRSI);
                  
                  if(isStrong && timeAtMaxRSI >= g_initTime && IsNewsSafe() && shouldTrack)
                  {
                     double sl = MathMax(lastHighPrice, priceAtMaxRSI);
                     WriteStrongSignalToGV(sym, tfStr, "SELL", currentPrice, sl, lastHighPrice, lastHighPrice, true, "HID", timeAtMaxRSI);
                     string sigKey = sym + "|" + tfStr + "|SELL|Hid|" + IntegerToString(timeAtMaxRSI);
                     PrintStrongSignalOnce(arrSymTF[idx], sym, tfStr, "SELL", "Hid", currentPrice, sl, timeAtMaxRSI, sigKey);
                  }
                  
                  if(timeAtMaxRSI >= g_initTime && timeAtMaxRSI != arrSymTF[idx].lastBearNotifyBar)
                  {
                     datetime now2 = TimeCurrent();
                     if(EnablePushNotify && now2 - arrSymTF[idx].lastBearNotifyTime >= NotifyCooldownMinutes * 60 && IsNewsSafe())
                     {
                        SendDivergenceNotify(sym, tfStr, "Hid", "SELL", timeStr);
                        arrSymTF[idx].lastBearNotifyTime = now2;
                        arrSymTF[idx].lastBearNotifyBar = timeAtMaxRSI;
                     }
                  }
               }
            }
         }
      }
   }
   
   // REALTIME BULLISH
   if(lastLowPrice > 0 && lastLowShift >= 0)
   {
      int startBull = MathMax(0, lastLowShift - 1);
      
      double rtMinLow = DBL_MAX, rsiAtMinLow = 0; 
      datetime timeAtMinLow = 0;
      int rtMinLowShift = -1;
      for(int i = startBull; i >= 0; i--) 
      {
         double curL = iLow(sym, tf, i);
         if(curL < rtMinLow) { rtMinLow = curL; rsiAtMinLow = GetRSI(arrSymTF[idx].rsiBuf, i); timeAtMinLow = iTime(sym, tf, i); rtMinLowShift = i; }
      }
      
      double rtMinRSI = DBL_MAX, priceAtMinRSI = 0; 
      datetime timeAtMinRSI = 0;
      int rtMinRSIShift = -1;
      for(int i = startBull; i >= 0; i--) 
      {
         double curR = GetRSI(arrSymTF[idx].rsiBuf, i);
         if(curR < rtMinRSI) { rtMinRSI = curR; priceAtMinRSI = iLow(sym, tf, i); timeAtMinRSI = iTime(sym, tf, i); rtMinRSIShift = i; }
      }
      
      // Regular Bull RT
      if(rtMinLow < lastLowPrice && rsiAtMinLow > lastLowRSI && rtMinLowShift != lastLowShift)
      {
         if(!RT_RequireMonotonicMove || IsMonotonicStructure(sym, tf, rtMinLowShift, lastLowShift, false))
         {
            if(CheckMiddleValid(sym, tf, rtMinLowShift, lastLowShift, lastLowTime, false))
            {
               double troughVolRatio = GetVolumeRatioAtShift(sym, rtMinLowShift, tf);
               if(troughVolRatio >= VolumeAutoRatio)
               {
                  datetime gmt7Time = ServerTimeToGMT7(timeAtMinLow);
                  string timeStr = TimeToString(gmt7Time, TIME_DATE|TIME_MINUTES);
                  double closeNew = iClose(sym, tf, rtMinLowShift);
                  double closeOld = iClose(sym, tf, iBarShift(sym, tf, lastLowTime, false));
                  bool isStrong = (closeNew < closeOld);
                  
                  double currentPrice = SymbolInfoDouble(sym, SYMBOL_ASK);
                  bool shouldTrack = ShouldRetrack(currentPrice, arrSymTF[idx].lastBullRegRTPrice, sym, currentBarTimeForTrack, arrSymTF[idx].lastBullRegRTTime);
                  if(shouldTrack && !IsInTradeTime(timeAtMinLow)) shouldTrack = false;
                  
                  if(shouldTrack)
                  {
                     arrSymTF[idx].lastBullRegRTPrice = currentPrice;
                     arrSymTF[idx].lastBullRegRTTime = currentBarTimeForTrack;
                  }
                  
                  string strengthTag = isStrong ? " [STRONG]" : " [WEAK]";
                  
                  ArrayResize(rtLines, rtCount + 1);
                  rtLines[rtCount].text = "[BUY-RT] [" + tfStr + "] " + sym + " [Reg] (" + timeStr + " GMT+7)" + strengthTag + " [Running...]";
                  rtLines[rtCount].time = timeAtMinLow;
                  rtLines[rtCount].isToday = (gmt7Time >= todayStartGMT7);
                  rtCount++;
                  
                  WriteRTDivergence(sym, tfStr, false, true, lastLowTime, lastLowRSI, lastLowPrice, timeAtMinLow, rsiAtMinLow, rtMinLow);
                  
                  if(gmt7Time >= todayStartGMT7)
                     AddTodaySignal(sym, tfStr, "BULL", "Reg", "RT", timeAtMinLow, lastLowPrice, rtMinLow, lastLowRSI, rsiAtMinLow);
                  
                  if(isStrong && timeAtMinLow >= g_initTime && IsNewsSafe() && shouldTrack)
                  {
                     WriteStrongSignalToGV(sym, tfStr, "BUY", currentPrice, rtMinLow, lastHighPrice, rtMinLow, true, "REG", timeAtMinLow);
                     string sigKey = sym + "|" + tfStr + "|BUY|Reg|" + IntegerToString(timeAtMinLow);
                     PrintStrongSignalOnce(arrSymTF[idx], sym, tfStr, "BUY", "Reg", currentPrice, rtMinLow, timeAtMinLow, sigKey);
                  }
                  
                  if(timeAtMinLow >= g_initTime && timeAtMinLow != arrSymTF[idx].lastBullNotifyBar)
                  {
                     datetime now2 = TimeCurrent();
                     if(EnablePushNotify && now2 - arrSymTF[idx].lastBullNotifyTime >= NotifyCooldownMinutes * 60 && IsNewsSafe())
                     {
                        SendDivergenceNotify(sym, tfStr, "Reg", "BUY", timeStr);
                        arrSymTF[idx].lastBullNotifyTime = now2;
                        arrSymTF[idx].lastBullNotifyBar = timeAtMinLow;
                     }
                  }
               }
            }
         }
      }
      
      // Hidden Bull RT
      if(rtMinRSI < lastLowRSI && priceAtMinRSI > lastLowPrice && rtMinRSIShift != lastLowShift)
      {
         if(!RT_RequireMonotonicMove || IsMonotonicStructure(sym, tf, rtMinRSIShift, lastLowShift, false))
         {
            if(CheckMiddleValid(sym, tf, rtMinRSIShift, lastLowShift, lastLowTime, false))
            {
               double troughVolRatio = GetVolumeRatioAtShift(sym, rtMinRSIShift, tf);
               if(troughVolRatio >= VolumeAutoRatio)
               {
                  datetime gmt7Time = ServerTimeToGMT7(timeAtMinRSI);
                  string timeStr = TimeToString(gmt7Time, TIME_DATE|TIME_MINUTES);
                  double closeNew = iClose(sym, tf, rtMinRSIShift);
                  double closeOld = iClose(sym, tf, iBarShift(sym, tf, lastLowTime, false));
                  bool isStrong = (closeNew > closeOld);
                  
                  double currentPrice = SymbolInfoDouble(sym, SYMBOL_ASK);
                  bool shouldTrack = ShouldRetrack(currentPrice, arrSymTF[idx].lastBullHidRTPrice, sym, currentBarTimeForTrack, arrSymTF[idx].lastBullHidRTTime);
                  if(shouldTrack && !IsInTradeTime(timeAtMinRSI)) shouldTrack = false;
                  
                  if(shouldTrack)
                  {
                     arrSymTF[idx].lastBullHidRTPrice = currentPrice;
                     arrSymTF[idx].lastBullHidRTTime = currentBarTimeForTrack;
                  }
                  
                  string strengthTag = isStrong ? " [STRONG]" : " [WEAK]";
                  
                  ArrayResize(rtLines, rtCount + 1);
                  rtLines[rtCount].text = "[BUY-RT] [" + tfStr + "] " + sym + " [Hid] (" + timeStr + " GMT+7)" + strengthTag + " [Running...]";
                  rtLines[rtCount].time = timeAtMinRSI;
                  rtLines[rtCount].isToday = (gmt7Time >= todayStartGMT7);
                  rtCount++;
                  
                  WriteRTDivergence(sym, tfStr, false, false, lastLowTime, lastLowRSI, lastLowPrice, timeAtMinRSI, rtMinRSI, priceAtMinRSI);
                  
                  if(gmt7Time >= todayStartGMT7)
                     AddTodaySignal(sym, tfStr, "BULL", "Hid", "RT", timeAtMinRSI, lastLowPrice, priceAtMinRSI, lastLowRSI, rtMinRSI);
                  
                  if(isStrong && timeAtMinRSI >= g_initTime && IsNewsSafe() && shouldTrack)
                  {
                     double sl = MathMin(lastLowPrice, priceAtMinRSI);
                     WriteStrongSignalToGV(sym, tfStr, "BUY", currentPrice, sl, lastHighPrice, lastLowPrice, true, "HID", timeAtMinRSI);
                     string sigKey = sym + "|" + tfStr + "|BUY|Hid|" + IntegerToString(timeAtMinRSI);
                     PrintStrongSignalOnce(arrSymTF[idx], sym, tfStr, "BUY", "Hid", currentPrice, sl, timeAtMinRSI, sigKey);
                  }
                  
                  if(timeAtMinRSI >= g_initTime && timeAtMinRSI != arrSymTF[idx].lastBullNotifyBar)
                  {
                     datetime now2 = TimeCurrent();
                     if(EnablePushNotify && now2 - arrSymTF[idx].lastBullNotifyTime >= NotifyCooldownMinutes * 60 && IsNewsSafe())
                     {
                        SendDivergenceNotify(sym, tfStr, "Hid", "BUY", timeStr);
                        arrSymTF[idx].lastBullNotifyTime = now2;
                        arrSymTF[idx].lastBullNotifyBar = timeAtMinRSI;
                     }
                  }
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| ONCALCULATE                                                       |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[], const double &open[], const double &high[], const double &low[], const double &close[], const long &tick_volume[], const long &volume[], const int &spread[])
{
   return rates_total;
}
//+------------------------------------------------------------------+