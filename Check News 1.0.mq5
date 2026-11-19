//+------------------------------------------------------------------+
//|                                              NewsTimeFilter.mq5  |
//|                                  Chức năng: Lọc giờ tin tức       |
//|                                  Ghi Global Variable cho EA/Indi  |
//|                                  GMT+7 tự động                    |
//+------------------------------------------------------------------+
#property copyright "News Time Filter"
#property link      ""
#property version   "1.00"
#property indicator_chart_window
#property indicator_buffers 0
#property indicator_plots   0

//+------------------------------------------------------------------+
//| Input parameters                                                 |
//+------------------------------------------------------------------+
// Giờ tin tức từng ngày - Định dạng: "HH:MM" (chỉ 1 giờ)
input string News_Monday    = "19:30";      // Thứ 2
input string News_Tuesday   = "19:30";      // Thứ 3
input string News_Wednesday = "19:30";      // Thứ 4
input string News_Thursday  = "19:30";      // Thứ 5
input string News_Friday    = "19:30";      // Thứ 6
input bool EnableNewsFilter = true;               // Bật/Tắt bộ lọc tin tức
input int NewsBeforeMinutes = 4;                  // Số phút cấm trước tin
input int NewsAfterMinutes = 4;                   // Số phút cấm sau tin
input int NewsCheckIntervalSeconds = 30;          // Chu kỳ quét (giây)

// Global Variable Name (để indicator/EA khác đọc)
input string GV_BlockedName = "GV_NewsFilter_Blocked";      // =1 nếu đang cấm, =0 nếu an toàn
input string GV_RemainingSeconds = "GV_NewsFilter_Remain";  // Số giây còn lại trong vùng cấm
input string GV_NextBlockSeconds = "GV_NewsFilter_Next";    // Số giây đến vùng cấm tiếp theo

// Vị trí hiển thị bảng tin tức
input int PanelX = 20;                            // Vị trí X
input int PanelY = 220;                            // Vị trí Y (khoảng cách từ ĐÁY lên)
input int PanelFontSize = 9;                      // Cỡ chữ



//+------------------------------------------------------------------+
//| Global variables                                                 |
//+------------------------------------------------------------------+
string PREFIX = "NF_";

// Múi giờ
int g_serverToGMTOffset = 0;
int g_gmt7TargetOffset = 7 * 3600;
datetime g_lastOffsetCalcTime = 0;

// News data
struct SNewsEvent
{
   int dayOfWeek;      // 1=T2 -> 5=T6
   int totalMinutes;   // Tổng phút trong ngày
   int blockStartMin;  // Phút bắt đầu cấm
   int blockEndMin;    // Phút kết thúc cấm
};

SNewsEvent g_newsEvents[];
int g_newsEventCount = 0;

datetime g_lastCheckTime = 0;
bool g_isBlocked = false;
int g_remainSeconds = 0;     // Số giây còn lại nếu đang bị cấm
int g_nextBlockSeconds = 0;  // Số giây đến vùng cấm tiếp theo

//+------------------------------------------------------------------+
//| Helper functions - Timezone                                      |
//+------------------------------------------------------------------+
void CalculateServerGMTOffset()
{
   datetime serverTime = TimeTradeServer();
   datetime gmtTime = TimeGMT();
   g_serverToGMTOffset = (int)(serverTime - gmtTime);
   
   int offsetHours = (int)MathRound((double)g_serverToGMTOffset / 3600.0);
   g_serverToGMTOffset = offsetHours * 3600;
   g_lastOffsetCalcTime = TimeCurrent();
}

datetime ServerTimeToGMT7(datetime serverTime)
{
   return (serverTime - g_serverToGMTOffset) + g_gmt7TargetOffset;
}

datetime GetGMT7Now()
{
   return ServerTimeToGMT7(TimeTradeServer());
}

string GetDayNameVN(int dayOfWeek)
{
   string names[] = {"CN", "T2", "T3", "T4", "T5", "T6", "T7"};
   if(dayOfWeek >= 0 && dayOfWeek <= 6) return names[dayOfWeek];
   return "??";
}

//+------------------------------------------------------------------+
//| Format seconds to HH:MM:SS                                       |
//+------------------------------------------------------------------+
string FormatSeconds(int totalSec)
{
   if(totalSec < 0) totalSec = 0;
   if(totalSec >= 999999) return "N/A";
   
   int h = totalSec / 3600;
   int m = (totalSec % 3600) / 60;
   int s = totalSec % 60;
   return StringFormat("%02d:%02d:%02d", h, m, s);
}

//+------------------------------------------------------------------+
//| Parse news events từ input                                       |
//+------------------------------------------------------------------+
void ParseNewsDay(SNewsEvent &events[], int dayOfWeek, string newsStr)
{
   if(newsStr == "") return;
   
   string times[];
   int count = StringSplit(newsStr, ',', times);
   
   for(int i = 0; i < count; i++)
   {
      string t = times[i];
      StringTrimLeft(t);
      StringTrimRight(t);
      if(t == "") continue;
      
      string parts[];
      if(StringSplit(t, ':', parts) != 2) continue;
      
      int hour = (int)StringToInteger(parts[0]);
      int minute = (int)StringToInteger(parts[1]);
      
      if(hour < 0 || hour > 23 || minute < 0 || minute > 59) continue;
      
      int totalMin = hour * 60 + minute;
      
      int idx = ArraySize(events);
      ArrayResize(events, idx + 1);
      events[idx].dayOfWeek = dayOfWeek;
      events[idx].totalMinutes = totalMin;
      events[idx].blockStartMin = totalMin - NewsBeforeMinutes;
      events[idx].blockEndMin = totalMin + NewsAfterMinutes;
   }
}

void InitNewsSchedule()
{
   ArrayResize(g_newsEvents, 0);
   g_newsEventCount = 0;
   
   if(!EnableNewsFilter) return;
   
   SNewsEvent temp[];
   ArrayResize(temp, 0);
   
   ParseNewsDay(temp, 1, News_Monday);
   ParseNewsDay(temp, 2, News_Tuesday);
   ParseNewsDay(temp, 3, News_Wednesday);
   ParseNewsDay(temp, 4, News_Thursday);
   ParseNewsDay(temp, 5, News_Friday);
   
   // Sort by day then time
   for(int i = 0; i < ArraySize(temp) - 1; i++)
   {
      for(int j = i + 1; j < ArraySize(temp); j++)
      {
         if(temp[i].dayOfWeek > temp[j].dayOfWeek ||
           (temp[i].dayOfWeek == temp[j].dayOfWeek && temp[i].totalMinutes > temp[j].totalMinutes))
         {
            SNewsEvent tmp = temp[i];
            temp[i] = temp[j];
            temp[j] = tmp;
         }
      }
   }
   
   g_newsEventCount = ArraySize(temp);
   ArrayResize(g_newsEvents, g_newsEventCount);
   for(int i = 0; i < g_newsEventCount; i++)
      g_newsEvents[i] = temp[i];
}

//+------------------------------------------------------------------+
//| Kiểm tra trạng thái hiện tại và tính thời gian                   |
//+------------------------------------------------------------------+
void CheckNewsStatus()
{
   // Reset
   g_isBlocked = false;
   g_remainSeconds = 0;
   g_nextBlockSeconds = 999999;
   
   if(!EnableNewsFilter || g_newsEventCount == 0)
   {
      UpdateGlobalVariables();
      return;
   }
   
   datetime gmt7Now = GetGMT7Now();
   MqlDateTime dt;
   TimeToStruct(gmt7Now, dt);
   
   int dow = dt.day_of_week;
   int totalSec = dt.hour * 3600 + dt.min * 60 + dt.sec;
   
   // Kiểm tra xem có đang trong vùng cấm không
   for(int i = 0; i < g_newsEventCount; i++)
   {
      if(g_newsEvents[i].dayOfWeek == dow)
      {
         int blockStartTotalSec = g_newsEvents[i].blockStartMin * 60;
         int blockEndTotalSec = g_newsEvents[i].blockEndMin * 60;
         
         if(totalSec >= blockStartTotalSec && totalSec <= blockEndTotalSec)
         {
            g_isBlocked = true;
            g_remainSeconds = blockEndTotalSec - totalSec;
            break;
         }
      }
   }
   
   // Tìm vùng cấm tiếp theo (nếu không đang bị cấm)
   if(!g_isBlocked)
   {
      // Tìm trong ngày hôm nay TRƯỚC
      for(int i = 0; i < g_newsEventCount; i++)
      {
         if(g_newsEvents[i].dayOfWeek == dow)
         {
            int blockStartTotalSec = g_newsEvents[i].blockStartMin * 60;
            
            if(blockStartTotalSec > totalSec)
            {
               int secToBlock = blockStartTotalSec - totalSec;
               if(secToBlock < g_nextBlockSeconds)
                  g_nextBlockSeconds = secToBlock;
            }
         }
      }
      
      // Nếu hôm nay không còn vùng cấm nào
      if(g_nextBlockSeconds >= 999999)
      {
         int secRemainingToday = 86400 - totalSec;
         
         // Tìm ngày tiếp theo có tin (chỉ T2-T6)
         for(int daysAhead = 1; daysAhead <= 7; daysAhead++)
         {
            int checkDow = dow + daysAhead;
            
            // Wrap around: chỉ T2(1)-T6(5)
            while(checkDow > 5) checkDow -= 5;
            if(checkDow < 1) checkDow += 5;
            
            int earliestSec = 999999;
            
            for(int i = 0; i < g_newsEventCount; i++)
            {
               if(g_newsEvents[i].dayOfWeek == checkDow)
               {
                  int blockStartSec = g_newsEvents[i].blockStartMin * 60;
                  if(blockStartSec < earliestSec)
                     earliestSec = blockStartSec;
               }
            }
            
            if(earliestSec < 999999)
            {
               // Tính tổng số giây: hết hôm nay + các ngày trung gian + giây trong ngày đó
               g_nextBlockSeconds = secRemainingToday;
               
               // Cộng thêm giây cho các ngày trung gian
               for(int d = 1; d < daysAhead; d++)
               {
                  int midDow = dow + d;
                  while(midDow > 5) midDow -= 5;
                  if(midDow >= 1 && midDow <= 5)
                     g_nextBlockSeconds += 86400;
               }
               
               g_nextBlockSeconds += earliestSec;
               break;
            }
         }
      }
   }
   
   // Cập nhật Global Variables
   UpdateGlobalVariables();
}

//+------------------------------------------------------------------+
//| Ghi Global Variables                                              |
//+------------------------------------------------------------------+
void UpdateGlobalVariables()
{
   // GV_Blocked: 1 = đang cấm, 0 = an toàn
   if(GlobalVariableCheck(GV_BlockedName))
      GlobalVariableSet(GV_BlockedName, g_isBlocked ? 1.0 : 0.0);
   else
      GlobalVariableSet(GV_BlockedName, g_isBlocked ? 1.0 : 0.0);
   
   // GV_RemainingSeconds: Số giây còn lại nếu đang cấm
   if(GlobalVariableCheck(GV_RemainingSeconds))
      GlobalVariableSet(GV_RemainingSeconds, (double)g_remainSeconds);
   else
      GlobalVariableSet(GV_RemainingSeconds, (double)g_remainSeconds);
   
   // GV_NextBlockSeconds: Số giây đến vùng cấm tiếp theo
   if(GlobalVariableCheck(GV_NextBlockSeconds))
      GlobalVariableSet(GV_NextBlockSeconds, (double)g_nextBlockSeconds);
   else
      GlobalVariableSet(GV_NextBlockSeconds, (double)g_nextBlockSeconds);
}

//+------------------------------------------------------------------+
//| Vẽ bảng tin tức - ĐÃ SỬA: Hiển thị ở góc dưới bên trái          |
//+------------------------------------------------------------------+
void DrawNewsPanel()
{
   // Xóa panel cũ
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i);
      if(StringFind(name, PREFIX) == 0)
         ObjectDelete(0, name);
   }
   
   if(!EnableNewsFilter) return;
   
   datetime gmt7Now = GetGMT7Now();
   MqlDateTime dt;
   TimeToStruct(gmt7Now, dt);
   
   int todayDow = dt.day_of_week;
   string timeNow = TimeToString(gmt7Now, TIME_MINUTES|TIME_SECONDS);
   
   int rowH = PanelFontSize + 5;
   int x = PanelX;
   
   // =============================================
   // SỬA: Panel mọc từ dưới lên
   // =============================================
   // Tính tổng số dòng cần hiển thị
   int totalLines = 0;
   totalLines++; // TITLE
   totalLines++; // STATUS
   totalLines++; // GVINFO
   totalLines++; // SEP1
   totalLines += 5; // 5 ngày T2-T6
   totalLines++; // GUIDE
   
   // Tính tổng chiều cao
   int totalHeight = totalLines * rowH + 10;
   
   // Bắt đầu từ vị trí Y (khoảng cách từ đáy lên)
   // Dòng đầu tiên (TITLE) sẽ ở vị trí cao nhất (gần đáy nhất)
   int startY = PanelY + totalHeight - rowH;
   // =============================================
   
   // Tiêu đề (dòng trên cùng của panel)
   CreateLabel("TITLE", "📰 NEWS TIME FILTER (GMT+7)", x, startY, clrGold, PanelFontSize + 1, true, clrNONE);
   int y = startY - rowH - 4;
   
   // Trạng thái hiện tại
   string statusText;
   color statusColor;
   
   if(g_isBlocked)
   {
      statusText = "🔴 BỊ CHẶN - còn " + FormatSeconds(g_remainSeconds);
      statusColor = clrRed;
   }
   else if(g_nextBlockSeconds < 999999)
   {
      statusText = "🟢 AN TOÀN - cấm sau " + FormatSeconds(g_nextBlockSeconds);
      statusColor = clrLime;
   }
   else
   {
      statusText = "🟢 AN TOÀN - không có tin";
      statusColor = clrLime;
   }
   
   CreateLabel("STATUS", "● " + statusText + " | " + timeNow, x, y, statusColor, PanelFontSize, true, clrNONE);
   y -= rowH + 4;
   
   // Global Variables info
   string gvInfo = StringFormat("GV: %s=%d | Remain: %s | Next: %s", 
                                GV_BlockedName, g_isBlocked ? 1 : 0,
                                FormatSeconds(g_remainSeconds),
                                FormatSeconds(g_nextBlockSeconds));
   CreateLabel("GVINFO", gvInfo, x, y, clrDimGray, PanelFontSize - 2, false, clrNONE);
   y -= rowH;
   
   // Đường kẻ
   CreateLabel("SEP1", "──────────────────────────────────", x, y, clrDarkGray, PanelFontSize - 1, false, clrNONE);
   y -= rowH - 2;
   
   // Hiển thị lịch T2-T6 (đảo ngược thứ tự để T2 ở trên, T6 ở dưới)
   //string days[] = {"T6", "T5", "T4", "T3", "T2"};
   int dayNums[] = {5, 4, 3, 2, 1};
   
   for(int d = 0; d < 5; d++)
   {
      int dayNum = dayNums[d];
      bool isToday = (todayDow == dayNum);
      
      // Thu thập các khung giờ cấm của ngày này
      string blocksStr = "";
      for(int i = 0; i < g_newsEventCount; i++)
      {
         if(g_newsEvents[i].dayOfWeek == dayNum)
         {
            if(blocksStr != "") blocksStr += "  ";
            blocksStr += StringFormat("%02d:%02d-%02d:%02d",
                                      g_newsEvents[i].blockStartMin / 60,
                                      g_newsEvents[i].blockStartMin % 60,
                                      g_newsEvents[i].blockEndMin / 60,
                                      g_newsEvents[i].blockEndMin % 60);
         }
      }
      
      if(blocksStr == "") blocksStr = "---";
      
      // Tạo dòng hiển thị
      string dayName;
      switch(dayNum)
      {
         case 1: dayName = "T2"; break;
         case 2: dayName = "T3"; break;
         case 3: dayName = "T4"; break;
         case 4: dayName = "T5"; break;
         case 5: dayName = "T6"; break;
      }
      
      string dayLine;
      if(isToday)
         dayLine = "▶ " + dayName + " (HÔM NAY): " + blocksStr;
      else
         dayLine = "  " + dayName + ": " + blocksStr;
      
      // Màu sắc - sửa: KHÔNG dùng màu đen, dùng màu sáng cho nền tối
      color dayColor;
      color bgColor = clrNONE;
      
      if(isToday)
      {
         dayColor = clrWhite;        // Chữ trắng thay vì đen
         bgColor = clrSteelBlue;     // Nền xanh dương thay vì vàng
      }
      else if(todayDow >= 6)  // Cuối tuần (T7, CN)
      {
         dayColor = clrLightGray;
      }
      else if(dayNum < todayDow)  // Ngày đã qua trong tuần
      {
         dayColor = clrDimGray;
      }
      else  // Ngày trong tương lai của tuần này
      {
         dayColor = clrLightGray;
      }
      
      CreateLabel("DAY" + IntegerToString(d), dayLine, x, y, dayColor, PanelFontSize, false, bgColor);
      y -= rowH;
   }
   
   // Hướng dẫn (dòng cuối cùng, gần đáy nhất)
   y -= 4;
   string guide = "Cấm " + IntegerToString(NewsBeforeMinutes) + "ph trước & " + IntegerToString(NewsAfterMinutes) + "ph sau tin";
   CreateLabel("GUIDE", guide, x, PanelY + PanelFontSize - 1, clrGray, PanelFontSize - 1, false, clrNONE);
}

//+------------------------------------------------------------------+
//| Tạo Label - ĐÃ SỬA: CORNER_LEFT_LOWER                            |
//+------------------------------------------------------------------+
void CreateLabel(string id, string text, int x, int y, color clr, int fontSize, bool bold, color bgColor)
{
   string name = PREFIX + id;
   
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   
   ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fontSize);
   // =============================================
   // SỬA: CORNER_LEFT_UPPER → CORNER_LEFT_LOWER
   // =============================================
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_LOWER);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   // =============================================
   // SỬA: ANCHOR_LEFT_UPPER → ANCHOR_LEFT_LOWER
   // =============================================
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   
   if(bgColor != clrNONE)
   {
      ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bgColor);
      ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, bgColor);
   }
   else
   {
      ObjectSetInteger(0, name, OBJPROP_BGCOLOR, clrNONE);
      ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, clrNONE);
   }
}

//+------------------------------------------------------------------+
//| Xóa toàn bộ panel                                                |
//+------------------------------------------------------------------+
void ClearPanel()
{
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i);
      if(StringFind(name, PREFIX) == 0)
         ObjectDelete(0, name);
   }
}

//+------------------------------------------------------------------+
//| OnInit                                                           |
//+------------------------------------------------------------------+
int OnInit()
{
   CalculateServerGMTOffset();
   InitNewsSchedule();
   
   EventSetTimer(1);
   
   // Kiểm tra ngay
   CheckNewsStatus();
   
   // In log
   Print("╔══════════════════════════════════════════════╗");
   Print("║        📰 NEWS TIME FILTER v1.00            ║");
   Print("╠══════════════════════════════════════════════╣");
   Print("║  GMT+7 Auto Detect                          ║");
   Print("║  Chu kỳ quét: ", NewsCheckIntervalSeconds, " giây                       ║");
   Print("║  Cấm trước: ", NewsBeforeMinutes, "ph | Sau: ", NewsAfterMinutes, "ph                    ║");
   Print("╠══════════════════════════════════════════════╣");
   Print("║  GLOBAL VARIABLES:                          ║");
   Print("║  ● ", GV_BlockedName, " = 1 (Cấm) / 0 (An toàn)     ║");
   Print("║  ● ", GV_RemainingSeconds, " = Số giây còn lại    ║");
   Print("║  ● ", GV_NextBlockSeconds, " = Số giây đến cấm tiếp║");
   Print("╚══════════════════════════════════════════════╝");
   
   if(EnableNewsFilter)
   {
      Print("📰 Lịch tin đã tải: ", g_newsEventCount, " sự kiện");
      for(int i = 0; i < g_newsEventCount; i++)
      {
         Print("   ", GetDayNameVN(g_newsEvents[i].dayOfWeek), ": ",
               StringFormat("%02d:%02d", g_newsEvents[i].totalMinutes / 60, g_newsEvents[i].totalMinutes % 60),
               " -> Cấm: ",
               StringFormat("%02d:%02d-%02d:%02d", 
                           g_newsEvents[i].blockStartMin / 60, g_newsEvents[i].blockStartMin % 60,
                           g_newsEvents[i].blockEndMin / 60, g_newsEvents[i].blockEndMin % 60));
      }
   }
   
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| OnDeinit                                                         |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   ClearPanel();
   
   // Xóa Global Variables
   GlobalVariableDel(GV_BlockedName);
   GlobalVariableDel(GV_RemainingSeconds);
   GlobalVariableDel(GV_NextBlockSeconds);
   GlobalVariableDel("GV_NewsFilter_Heartbeat");
   
   Print("📰 NewsTimeFilter removed");
}

//+------------------------------------------------------------------+
//| OnCalculate                                                      |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
   return rates_total;
}

//+------------------------------------------------------------------+
//| OnTimer                                                          |
//+------------------------------------------------------------------+
void OnTimer()
{
   datetime now = TimeCurrent();
   
   // Cập nhật offset mỗi giờ
   if(now - g_lastOffsetCalcTime > 3600)
      CalculateServerGMTOffset();
   
   // Kiểm tra news theo chu kỳ
   if(EnableNewsFilter && (now - g_lastCheckTime >= NewsCheckIntervalSeconds || g_lastCheckTime == 0))
   {
      bool wasBlocked = g_isBlocked;
      CheckNewsStatus();
      g_lastCheckTime = now;
      
      // Log khi thay đổi trạng thái
      if(wasBlocked != g_isBlocked)
      {
         datetime gmt7Now = GetGMT7Now();
         if(g_isBlocked)
            Print("🔴 [NEWS] BẮT ĐẦU CẤM lúc ", TimeToString(gmt7Now, TIME_MINUTES|TIME_SECONDS), " GMT+7 - GV_Blocked=1");
         else
            Print("🟢 [NEWS] KẾT THÚC CẤM lúc ", TimeToString(gmt7Now, TIME_MINUTES|TIME_SECONDS), " GMT+7 - GV_Blocked=0");
      }
   }
   
   // Luôn vẽ lại panel để cập nhật thời gian đếm ngược
   DrawNewsPanel();
   
   // Heartbeat
   GlobalVariableSet("GV_NewsFilter_Heartbeat", (double)TimeCurrent());
}
//+------------------------------------------------------------------+