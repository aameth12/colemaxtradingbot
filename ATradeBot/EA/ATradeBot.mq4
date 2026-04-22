//+------------------------------------------------------------------+
//|                                                    ATradeBot.mq4 |
//|                                         ATradeBot — Colemax MT4  |
//|                                                                   |
//| Broker     : Colemax                                              |
//| Assets     : XAUUSD, AAPL, TSLA, NVDA, AMZN (Stock CFDs)        |
//| Timeframes : M5 (primary signal), M15 (trend filter)             |
//| Style      : Scalping                                             |
//|                                                                   |
//| MQL4 only — no handles, no CopyBuffer(), no MT5 syntax.          |
//+------------------------------------------------------------------+

#property copyright   "ATradeBot"
#property link        ""
#property version     "1.00"
#property strict                     // enforces stricter type checking in MQL4


//+------------------------------------------------------------------+
//|  SECTION 1 — INPUT PARAMETERS                                    |
//|  All user-configurable settings. Grouped by category so they     |
//|  appear cleanly in the EA inputs dialog.                         |
//+------------------------------------------------------------------+

// --- Trade sizing ---
input double InpLotSize         = 0.01;   // Fixed lot size (used when InpUseRiskPct = false)
input bool   InpUseRiskPct      = true;   // Use risk-% based lot sizing instead of fixed
input double InpRiskPct         = 2.0;    // % of balance to risk per trade (e.g. 2.0 = 2%)
input double InpMinLot          = 0.01;   // Minimum allowed lot size
input double InpMaxLot          = 2.0;    // Maximum allowed lot size

// --- Stop loss / take profit ---
input int    InpSLPips          = 20;     // Stop loss in pips
input int    InpTPPips          = 40;     // Take profit in pips (0 = disabled)
input bool   InpUseATRStops     = true;   // Scale SL/TP by ATR instead of fixed pips
input double InpATRSLMulti      = 1.5;    // SL = ATR * this multiplier
input double InpATRTPMulti      = 3.0;    // TP = ATR * this multiplier

// --- Trailing stop / breakeven ---
input bool   InpUseTrailing     = true;   // Enable trailing stop
input int    InpTrailStartPips  = 15;     // Pips in profit before trailing activates
input int    InpTrailStepPips   = 5;      // Trail step size in pips
input bool   InpUseBreakeven    = true;   // Enable breakeven
input int    InpBreakevenPips   = 10;     // Pips in profit to move SL to breakeven

// --- EMA parameters ---
input int    InpEMA9Period      = 9;      // Fast EMA period
input int    InpEMA21Period     = 21;     // Slow EMA period
input int    InpSMA200Period    = 200;    // Long-term SMA period (trend filter)

// --- RSI parameters ---
input int    InpRSIPeriod       = 14;     // RSI period
input double InpRSIOverbought   = 70.0;   // RSI overbought level
input double InpRSIOversold     = 30.0;   // RSI oversold level
input double InpRSIBullMin      = 45.0;   // BullishSignal: RSI lower bound (upward momentum confirmed)
input double InpRSIBullMax      = 65.0;   // BullishSignal: RSI upper bound (not yet stretched)
input double InpRSIBearMin      = 35.0;   // BearishSignal: RSI lower bound (not yet exhausted)
input double InpRSIBearMax      = 55.0;   // BearishSignal: RSI upper bound (downward momentum confirmed)

// --- MACD parameters ---
input int    InpMACDFastEMA     = 12;     // MACD fast EMA period
input int    InpMACDSlowEMA     = 26;     // MACD slow EMA period
input int    InpMACDSignal      = 9;      // MACD signal line period

// --- ATR parameters ---
input int    InpATRPeriod       = 14;     // ATR period

// --- Bollinger Bands parameters ---
input int    InpBBPeriod        = 20;     // Bollinger Bands period
input double InpBBDeviation     = 2.0;    // Bollinger Bands deviation

// --- Stochastic parameters ---
input int    InpStochK          = 5;      // Stochastic %K period
input int    InpStochD          = 3;      // Stochastic %D period
input int    InpStochSlowing    = 3;      // Stochastic slowing
input double InpStochOverbought = 80.0;   // Stochastic overbought level
input double InpStochOversold   = 20.0;   // Stochastic oversold level

// --- ADX parameters ---
input int    InpADXPeriod       = 14;     // ADX period
input double InpADXMinStrength  = 25.0;   // Minimum ADX value to consider trend valid

// --- Session / time filter ---
input bool   InpUseTimeFilter    = true;  // Restrict trading to defined session hours
// Offset added to broker server time to obtain Israel time (UTC+3).
// Set to 0 if the server is already on UTC+3; set to 3 if server is UTC+0, etc.
input int    InpServerToILOffset = 0;     // Server → Israel time offset in hours (default 0)

// --- Risk / kill switch ---
input double InpMaxDailyDrawPct = 5.0;    // Daily drawdown % that triggers kill switch
input int    InpMaxDailyTrades  = 10;     // Maximum trades allowed per day
input int    InpMagicNumber     = 20250101; // Unique EA identifier — change if running multiple EAs


//+------------------------------------------------------------------+
//|  SECTION 2 — GLOBAL VARIABLES                                    |
//|  Runtime state shared across all event handlers and helpers.     |
//+------------------------------------------------------------------+

// --- EA identity ---
const string EA_NAME            = "ATradeBot";

// --- Bar-change detection ---
// Cached time of the last processed bar; used to fire logic once per candle.
datetime g_lastBarTime          = 0;

// --- Daily session tracking ---
double   g_dailyStartBalance    = 0.0;    // Balance recorded at session open
int      g_dailyTradeCount      = 0;      // Trades opened today
datetime g_currentDay           = 0;      // Date of the current trading day

// --- Kill switch ---
bool     g_killSwitch           = false;  // Set true to halt all new trade activity
string   g_killReason           = "";     // Human-readable reason stored for logging

// --- End-of-day tracking ---
bool     g_eodDone              = false;  // Set true once the 22:00 IL EOD close fires for today

// --- Indicator snapshot (populated each tick by RefreshIndicators) ---
// Moving averages
double   g_ema9                 = 0.0;
double   g_ema21                = 0.0;
double   g_sma200               = 0.0;

// RSI
double   g_rsi                  = 0.0;

// MACD
double   g_macdMain             = 0.0;
double   g_macdSignal           = 0.0;

// ATR
double   g_atr                  = 0.0;

// Bollinger Bands
double   g_bbUpper              = 0.0;
double   g_bbMiddle             = 0.0;
double   g_bbLower              = 0.0;

// Stochastic
double   g_stochMain            = 0.0;
double   g_stochSignal          = 0.0;

// ADX
double   g_adx                  = 0.0;
double   g_diPlus               = 0.0;
double   g_diMinus              = 0.0;

// --- Point value helper (accounts for 5-digit brokers) ---
// For XAUUSD and CFDs the pip is 0.01 or broker-specific; this is set in OnInit.
double   g_pipValue             = 0.0;


//+------------------------------------------------------------------+
//|  SECTION 3 — OnInit()                                            |
//|  Runs once when the EA is attached or the terminal restarts.     |
//|  Validates all inputs and prints a startup summary.              |
//+------------------------------------------------------------------+
int OnInit()
  {
   // --- Validate input parameters ---
   if(InpLotSize <= 0)
     {
      Alert(EA_NAME + ": InpLotSize must be > 0. EA will not run.");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpRiskPct <= 0 || InpRiskPct > 100)
     {
      Alert(EA_NAME + ": InpRiskPct must be between 0 and 100.");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpSLPips <= 0)
     {
      Alert(EA_NAME + ": InpSLPips must be > 0.");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpEMA9Period <= 0 || InpEMA21Period <= 0 || InpSMA200Period <= 0)
     {
      Alert(EA_NAME + ": All EMA/SMA periods must be > 0.");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpEMA9Period >= InpEMA21Period)
     {
      Alert(EA_NAME + ": InpEMA9Period must be less than InpEMA21Period.");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpRSIPeriod < 2)
     {
      Alert(EA_NAME + ": InpRSIPeriod must be >= 2.");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpADXMinStrength < 0 || InpADXMinStrength > 100)
     {
      Alert(EA_NAME + ": InpADXMinStrength must be between 0 and 100.");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpMaxDailyDrawPct <= 0 || InpMaxDailyDrawPct > 100)
     {
      Alert(EA_NAME + ": InpMaxDailyDrawPct must be between 0 and 100.");
      return(INIT_PARAMETERS_INCORRECT);
     }

   // --- Compute pip value for this symbol ---
   // Most 5-digit brokers: _Digits == 5 for FX, == 3 for JPY pairs, varies for CFDs.
   // For XAUUSD on Colemax _Digits is typically 2 (price quoted to 0.01).
   g_pipValue = (_Digits == 5 || _Digits == 3) ? _Point * 10 : _Point;

   // --- Seed daily tracking ---
   g_dailyStartBalance = AccountBalance();
   g_currentDay        = StringToTime(TimeToString(TimeCurrent(), TIME_DATE));

   // --- Startup banner ---
   Print("==============================================");
   Print(EA_NAME + " v", __MQLBUILD__, "  started");
   Print("Symbol       : ", _Symbol);
   Print("Timeframe    : ", Period(), " min");
   Print("Broker       : Colemax");
   Print("Magic number : ", InpMagicNumber);
   Print("Lot sizing   : ", InpUseRiskPct ? "Risk-based (" + DoubleToString(InpRiskPct, 1) + "%)" : "Fixed (" + DoubleToString(InpLotSize, 2) + " lots)");
   Print("SL/TP mode   : ", InpUseATRStops ? "ATR-based" : "Fixed pips");
   Print("Session (IL) : XAUUSD 10:00-21:00 | Stocks 16:30-21:00 | EOD close 22:00");
   Print("IL offset    : server + ", InpServerToILOffset, " h = Israel time");
   Print("Kill switch  : daily drawdown > ", InpMaxDailyDrawPct, "%");
   Print("==============================================");

   return(INIT_SUCCEEDED);
  }


//+------------------------------------------------------------------+
//|  SECTION 4 — OnDeinit()                                          |
//|  Runs when the EA is removed, the chart is closed, or the        |
//|  terminal shuts down. Release resources and log the reason.      |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   // Log the deinitialization reason code so it appears in the Journal tab.
   // Common codes: 0=program, 1=remove, 2=recompile, 3=chartchange,
   //               4=chartclose, 5=parameters, 6=account, 7=template.
   Print(EA_NAME + " deinitialized. Reason code: ", reason);

   // Reset kill switch so a fresh attach starts clean.
   g_killSwitch  = false;
   g_killReason  = "";

   Print(EA_NAME + " shutdown complete.");
  }


//+------------------------------------------------------------------+
//|  SECTION 5 — OnTick()                                            |
//|  Fires on every incoming price tick. Heavy logic is gated behind |
//|  a new-bar check so decisions run once per completed candle.     |
//+------------------------------------------------------------------+
void OnTick()
  {
   // --- New-day reset ---
   // Refresh daily counters when the calendar date rolls over.
   datetime today = StringToTime(TimeToString(TimeCurrent(), TIME_DATE));
   if(today != g_currentDay)
     {
      g_currentDay        = today;
      g_dailyStartBalance = AccountBalance();
      g_dailyTradeCount   = 0;
      g_killSwitch        = false;   // reset intraday kill switch on new day
      g_killReason        = "";
      g_eodDone           = false;   // allow EOD close to fire again on the new day
      Print(EA_NAME + ": New trading day started. Balance reset to ", g_dailyStartBalance);
     }

   // --- End-of-day close at 22:00 IL time ---
   // Runs on every tick so it fires promptly when the clock crosses 22:00.
   // g_eodDone prevents repeat calls within the same calendar day.
   {
      int rawMin  = TimeHour(TimeCurrent()) * 60 + TimeMinute(TimeCurrent())
                    + InpServerToILOffset * 60;
      int ilHourX = (((rawMin % 1440) + 1440) % 1440) / 60;
      if(ilHourX >= 22 && !g_eodDone)
        {
         g_eodDone = true;
         CloseAllTradesEOD();
        }
   }

   // --- Kill switch check ---
   // If the kill switch is active, manage existing trades but open nothing new.
   if(g_killSwitch)
     {
      ManageOpenTrades();
      return;
     }

   // --- New-bar gate ---
   // All entry/exit decisions are made on a freshly closed candle (shift 1),
   // not on the live forming candle. This prevents re-triggering mid-bar.
   datetime currentBarTime = Time[0];
   if(currentBarTime == g_lastBarTime)
      return;
   g_lastBarTime = currentBarTime;

   // --- Refresh indicator snapshots ---
   // Populate all global indicator doubles from completed bar (shift 1).
   RefreshIndicators();

   // --- Daily drawdown kill switch ---
   // Check after indicators are fresh so equity is current.
   double drawdownPct = (g_dailyStartBalance - AccountEquity()) / g_dailyStartBalance * 100.0;
   if(drawdownPct >= InpMaxDailyDrawPct)
     {
      g_killSwitch = true;
      g_killReason = StringFormat("Daily drawdown %.2f%% reached limit %.2f%%", drawdownPct, InpMaxDailyDrawPct);
      Print(EA_NAME + " KILL SWITCH: ", g_killReason);
      CloseAllTrades();
      return;
     }

   // --- Manage existing open trades first ---
   ManageOpenTrades();

   // --- Entry logic gate ---
   if(!IsTradingAllowed())
      return;

   if(g_dailyTradeCount >= InpMaxDailyTrades)
     {
      // Daily trade cap reached — no new entries until tomorrow.
      return;
     }

   // --- Open-trade direction guard ---
   bool hasOpenBuy  = false;
   bool hasOpenSell = false;
   for(int i = 0; i < OrdersTotal(); i++)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderMagicNumber() != InpMagicNumber)        continue;
      if(OrderSymbol()      != _Symbol)               continue;
      if(OrderType() == OP_BUY)  hasOpenBuy  = true;
      if(OrderType() == OP_SELL) hasOpenSell = true;
     }

   // --- Signal evaluation ---
   if(BullishSignal() && !hasOpenBuy)
     {
      // SL: lowest low of last 5 closed candles minus ATR*0.5
      double slLow  = Low[iLowest(NULL, 0, MODE_LOW, 5, 1)];
      double slBuy  = NormalizeDouble(slLow - g_atr * 0.5, _Digits);
      double slDist = Ask - slBuy;
      double tpBuy  = NormalizeDouble(Ask + slDist * 2.0, _Digits);
      double slPips = slDist / g_pipValue;
      double lots   = GetLotSize(slPips);

      int ticket = OrderSend(_Symbol, OP_BUY, lots, Ask, 3, slBuy, tpBuy,
                             "ATradeBot", InpMagicNumber, 0, clrGreen);
      if(ticket > 0)
        {
         g_dailyTradeCount++;
         Print(EA_NAME + ": BUY opened ticket=", ticket,
               " lots=", lots, " sl=", slBuy, " tp=", tpBuy);
        }
      else
         Print(EA_NAME + ": BUY OrderSend failed error=", GetLastError());
     }
   else if(BearishSignal() && !hasOpenSell)
     {
      // SL: highest high of last 5 closed candles plus ATR*0.5
      double slHigh  = High[iHighest(NULL, 0, MODE_HIGH, 5, 1)];
      double slSell  = NormalizeDouble(slHigh + g_atr * 0.5, _Digits);
      double slDist  = slSell - Bid;
      double tpSell  = NormalizeDouble(Bid - slDist * 2.0, _Digits);
      double slPips  = slDist / g_pipValue;
      double lots    = GetLotSize(slPips);

      int ticket = OrderSend(_Symbol, OP_SELL, lots, Bid, 3, slSell, tpSell,
                             "ATradeBot", InpMagicNumber, 0, clrRed);
      if(ticket > 0)
        {
         g_dailyTradeCount++;
         Print(EA_NAME + ": SELL opened ticket=", ticket,
               " lots=", lots, " sl=", slSell, " tp=", tpSell);
        }
      else
         Print(EA_NAME + ": SELL OrderSend failed error=", GetLastError());
     }
  }


//+------------------------------------------------------------------+
//|  SECTION 6 — HELPER FUNCTIONS                                    |
//+------------------------------------------------------------------+

//--- 6a. RefreshIndicators -------------------------------------------
// Called once per new bar inside OnTick().
// Uses MQL4 direct-value indicator calls (shift 1 = last closed candle).
// NULL = current symbol, 0 = current timeframe.
void RefreshIndicators()
  {
   // --- Moving averages ---
   g_ema9   = iMA(NULL, 0, InpEMA9Period,   0, MODE_EMA, PRICE_CLOSE, 1);
   g_ema21  = iMA(NULL, 0, InpEMA21Period,  0, MODE_EMA, PRICE_CLOSE, 1);
   g_sma200 = iMA(NULL, 0, InpSMA200Period, 0, MODE_SMA, PRICE_CLOSE, 1);

   // --- RSI ---
   g_rsi = iRSI(NULL, 0, InpRSIPeriod, PRICE_CLOSE, 1);

   // --- MACD ---
   // MODE_MAIN   = MACD histogram / main line
   // MODE_SIGNAL = signal line
   g_macdMain   = iMACD(NULL, 0, InpMACDFastEMA, InpMACDSlowEMA, InpMACDSignal, PRICE_CLOSE, MODE_MAIN,   1);
   g_macdSignal = iMACD(NULL, 0, InpMACDFastEMA, InpMACDSlowEMA, InpMACDSignal, PRICE_CLOSE, MODE_SIGNAL, 1);

   // --- ATR ---
   g_atr = iATR(NULL, 0, InpATRPeriod, 1);

   // --- Bollinger Bands ---
   // MODE_UPPER = upper band, MODE_MAIN = middle (basis), MODE_LOWER = lower band
   g_bbUpper  = iBands(NULL, 0, InpBBPeriod, InpBBDeviation, 0, PRICE_CLOSE, MODE_UPPER, 1);
   g_bbMiddle = iBands(NULL, 0, InpBBPeriod, InpBBDeviation, 0, PRICE_CLOSE, MODE_MAIN,  1);
   g_bbLower  = iBands(NULL, 0, InpBBPeriod, InpBBDeviation, 0, PRICE_CLOSE, MODE_LOWER, 1);

   // --- Stochastic ---
   // MODE_MAIN = %K line, MODE_SIGNAL = %D line
   // Last arg 0 = Low/High price field (standard stochastic)
   g_stochMain   = iStochastic(NULL, 0, InpStochK, InpStochD, InpStochSlowing, MODE_SMA, 0, MODE_MAIN,   1);
   g_stochSignal = iStochastic(NULL, 0, InpStochK, InpStochD, InpStochSlowing, MODE_SMA, 0, MODE_SIGNAL, 1);

   // --- ADX ---
   // MODE_MAIN   = ADX strength line
   // MODE_PLUSDI = +DI line
   // MODE_MINUSDI= -DI line
   g_adx     = iADX(NULL, 0, InpADXPeriod, PRICE_CLOSE, MODE_MAIN,    1);
   g_diPlus  = iADX(NULL, 0, InpADXPeriod, PRICE_CLOSE, MODE_PLUSDI,  1);
   g_diMinus = iADX(NULL, 0, InpADXPeriod, PRICE_CLOSE, MODE_MINUSDI, 1);
  }


//--- 6b. IsTradingAllowed --------------------------------------------
// Returns false if any session rule, time filter, or broker condition
// prevents opening a new trade right now.
// Prints one diagnostic line per bar showing symbol, IL time, and result.
bool IsTradingAllowed()
  {
   // --- Broker / terminal checks ---
   if(!IsTradeAllowed())    return(false);
   if(!IsConnected())       return(false);
   if(IsTradeContextBusy()) return(false);
   if(IsStopped())          return(false);

   // --- Convert server time → Israel time (UTC+3) ---
   // InpServerToILOffset: hours to add to server time to reach IL time.
   // Uses modular arithmetic so the result stays in [0, 1439] minutes.
   int rawMin    = TimeHour(TimeCurrent()) * 60 + TimeMinute(TimeCurrent())
                   + InpServerToILOffset * 60;
   int ilMinutes = ((rawMin % 1440) + 1440) % 1440;
   int ilHour    = ilMinutes / 60;
   int ilMin     = ilMinutes % 60;
   int ilTotal   = ilHour * 60 + ilMin;   // total IL minutes since midnight
   int dow       = DayOfWeek();           // 0=Sun, 1=Mon … 5=Fri, 6=Sat

   string timeStr = StringFormat("%02d:%02d IL", ilHour, ilMin);

   // --- Session time filter ---
   if(InpUseTimeFilter)
     {
      // Block on Sunday — market gaps.
      if(dow == 0)
        {
         Print(EA_NAME + ": ", _Symbol, " ", timeStr,
               " — trading BLOCKED (Sunday)");
         return(false);
        }

      bool isXAUUSD = (_Symbol == "XAUUSD");
      bool isStock  = (_Symbol == "AAPL" || _Symbol == "TSLA" ||
                       _Symbol == "NVDA" || _Symbol == "AMZN");

      if(isXAUUSD)
        {
         // XAUUSD session: 10:00–21:00 IL
         if(ilHour < 10 || ilHour >= 21)
           {
            Print(EA_NAME + ": ", _Symbol, " ", timeStr,
                  " — trading BLOCKED (outside XAUUSD session 10:00-21:00 IL)");
            return(false);
           }
         // Monday: skip first 30 min of session (10:00–10:29)
         if(dow == 1 && ilTotal < 10 * 60 + 30)
           {
            Print(EA_NAME + ": ", _Symbol, " ", timeStr,
                  " — trading BLOCKED (Monday open buffer until 10:30 IL)");
            return(false);
           }
        }
      else if(isStock)
        {
         // Stock session: 16:30–21:00 IL
         if(ilTotal < 16 * 60 + 30 || ilHour >= 21)
           {
            Print(EA_NAME + ": ", _Symbol, " ", timeStr,
                  " — trading BLOCKED (outside stock session 16:30-21:00 IL)");
            return(false);
           }
         // Monday: skip first 30 min of session (16:30–16:59)
         if(dow == 1 && ilTotal < 17 * 60)
           {
            Print(EA_NAME + ": ", _Symbol, " ", timeStr,
                  " — trading BLOCKED (Monday open buffer until 17:00 IL)");
            return(false);
           }
        }
      else
        {
         Print(EA_NAME + ": ", _Symbol, " ", timeStr,
               " — trading BLOCKED (symbol not in approved list)");
         return(false);
        }

      // Friday: no new trades at or after 19:00 IL
      if(dow == 5 && ilHour >= 19)
        {
         Print(EA_NAME + ": ", _Symbol, " ", timeStr,
               " — trading BLOCKED (Friday cutoff 19:00 IL)");
         return(false);
        }
     }

   // --- Daily 10% hard kill switch ---
   // If total loss since session open reaches 10% of opening balance, halt
   // all trading, close all open positions, and block until the next day.
   if(g_dailyStartBalance > 0)
     {
      double dailyLossPct = (g_dailyStartBalance - AccountEquity()) /
                             g_dailyStartBalance * 100.0;
      if(dailyLossPct >= 10.0)
        {
         g_killSwitch = true;
         g_killReason = StringFormat("Daily loss %.2f%% reached hard limit 10%%",
                                     dailyLossPct);
         Print(EA_NAME + " KILL SWITCH: ", g_killReason);
         CloseAllTrades();
         return(false);
        }
     }

   Print(EA_NAME + ": ", _Symbol, " ", timeStr, " — trading ALLOWED");
   return(true);
  }


//--- 6c. GetLotSize --------------------------------------------------
// Returns a lot size based on account balance, risk %, and SL distance.
// Formula: lots = (balance * riskPct) / (slPips * pipValuePerLot)
// Clamped to hard limits: min 0.01, max 2.0 lots.
// Falls back to InpLotSize when InpUseRiskPct is false.
double GetLotSize(double slPips)
  {
   if(!InpUseRiskPct)
      return(InpLotSize);
   if(slPips <= 0)
      return(InpLotSize);

   double riskAmount     = AccountBalance() * InpRiskPct / 100.0;
   double tickValue      = MarketInfo(_Symbol, MODE_TICKVALUE);
   double tickSize       = MarketInfo(_Symbol, MODE_TICKSIZE);
   double pipValuePerLot = (tickSize > 0) ? tickValue * g_pipValue / tickSize : tickValue;
   if(pipValuePerLot <= 0)
      return(InpLotSize);

   double lots = riskAmount / (slPips * pipValuePerLot);
   return(MathMax(0.01, MathMin(2.0, NormalizeDouble(lots, 2))));
  }


//--- 6d. BullishSignal -----------------------------------------------
// Returns true only when ALL seven conditions are satisfied on the last
// closed candle. Uses the g_* globals populated by RefreshIndicators().
// Short-circuit: each failed condition exits immediately (no wasted checks).
bool BullishSignal()
  {
   // --- Symbol filter: only trade the approved asset list ---
   if(_Symbol != "XAUUSD" && _Symbol != "AAPL" &&
      _Symbol != "TSLA"   && _Symbol != "NVDA" && _Symbol != "AMZN")
      return(false);

   // --- Timeframe filter: M5 (primary signal) and M15 (trend confirmation) only ---
   if(Period() != PERIOD_M5 && Period() != PERIOD_M15)
      return(false);

   // Spread in price units, used by the ATR volatility check (condition 7).
   double spreadPrice = MarketInfo(_Symbol, MODE_SPREAD) * _Point;

   // 1. Trend alignment — full MA stack must be bullish.
   //    Close[1] > EMA9  : price momentum above the fast average.
   //    EMA9   > EMA21   : fast average above slow — short-term uptrend.
   //    EMA21  > SMA200  : slow average above long-term — macro bull structure.
   if(Close[1] <= g_ema9)   return(false);
   if(g_ema9   <= g_ema21)  return(false);
   if(g_ema21  <= g_sma200) return(false);

   // 2. RSI in the bullish sweet spot [InpRSIBullMin, InpRSIBullMax].
   //    Above minimum : momentum is present and biased upward.
   //    Below maximum : not yet stretched or overbought — room to run.
   if(g_rsi < InpRSIBullMin || g_rsi > InpRSIBullMax) return(false);

   // 3. MACD confirming bullish momentum on two levels:
   //    Main > Signal : histogram is positive — recent bullish crossover.
   //    Main > 0      : both fast and slow EMAs agree the bias is up.
   if(g_macdMain <= g_macdSignal) return(false);
   if(g_macdMain <= 0.0)          return(false);

   // 4. Stochastic below the overbought threshold.
   //    If %K is already above InpStochOverbought the up-move may be exhausted;
   //    we want to enter while there is still room for price to extend higher.
   if(g_stochMain >= InpStochOverbought) return(false);

   // 5. ADX above the minimum strength threshold.
   //    A reading below InpADXMinStrength indicates a ranging / choppy market
   //    where trend-following entries have poor expectancy.
   if(g_adx <= InpADXMinStrength) return(false);

   // 6. Close above the Bollinger Band middle (basis SMA).
   //    Price in the upper half of the band confirms bullish mean-reversion bias.
   if(Close[1] <= g_bbMiddle) return(false);

   // 7. ATR > 3 × spread.
   //    Ensures the current true-range movement is large enough to overcome
   //    transaction costs and still produce a meaningful scalp profit.
   if(g_atr <= spreadPrice * 3.0) return(false);

   return(true);
  }


//--- 6e. BearishSignal -----------------------------------------------
// Returns true only when ALL seven conditions are satisfied on the last
// closed candle. Exact directional mirror of BullishSignal().
bool BearishSignal()
  {
   // --- Symbol filter: only trade the approved asset list ---
   if(_Symbol != "XAUUSD" && _Symbol != "AAPL" &&
      _Symbol != "TSLA"   && _Symbol != "NVDA" && _Symbol != "AMZN")
      return(false);

   // --- Timeframe filter: M5 (primary signal) and M15 (trend confirmation) only ---
   if(Period() != PERIOD_M5 && Period() != PERIOD_M15)
      return(false);

   // Spread in price units, used by the ATR volatility check (condition 7).
   double spreadPrice = MarketInfo(_Symbol, MODE_SPREAD) * _Point;

   // 1. Trend alignment — full MA stack must be bearish.
   //    Close[1] < EMA9  : price momentum below the fast average.
   //    EMA9   < EMA21   : fast average below slow — short-term downtrend.
   //    EMA21  < SMA200  : slow average below long-term — macro bear structure.
   if(Close[1] >= g_ema9)   return(false);
   if(g_ema9   >= g_ema21)  return(false);
   if(g_ema21  >= g_sma200) return(false);

   // 2. RSI in the bearish sweet spot [InpRSIBearMin, InpRSIBearMax].
   //    Below maximum : momentum is present and biased downward.
   //    Above minimum : not yet oversold / exhausted — room to fall further.
   if(g_rsi < InpRSIBearMin || g_rsi > InpRSIBearMax) return(false);

   // 3. MACD confirming bearish momentum on two levels:
   //    Main < Signal : histogram is negative — recent bearish crossover.
   //    Main < 0      : both fast and slow EMAs agree the bias is down.
   if(g_macdMain >= g_macdSignal) return(false);
   if(g_macdMain >= 0.0)          return(false);

   // 4. Stochastic above the oversold threshold.
   //    If %K is already below InpStochOversold the down-move may be exhausted;
   //    we want to enter while there is still room for price to extend lower.
   if(g_stochMain <= InpStochOversold) return(false);

   // 5. ADX above the minimum strength threshold.
   //    Same logic as BullishSignal — rejects ranging / low-momentum markets.
   if(g_adx <= InpADXMinStrength) return(false);

   // 6. Close below the Bollinger Band middle (basis SMA).
   //    Price in the lower half of the band confirms bearish mean-reversion bias.
   if(Close[1] >= g_bbMiddle) return(false);

   // 7. ATR > 3 × spread.
   //    Same transaction-cost filter as BullishSignal.
   if(g_atr <= spreadPrice * 3.0) return(false);

   return(true);
  }


//--- 6f. ManageOpenTrades --------------------------------------------
// Iterates all open orders belonging to this EA (matched by magic number
// and symbol) and applies trailing stop and breakeven logic.
void ManageOpenTrades()
  {
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderMagicNumber() != InpMagicNumber)        continue;
      if(OrderSymbol()      != _Symbol)               continue;

      double openPrice = OrderOpenPrice();
      double currentSL = OrderStopLoss();
      double currentTP = OrderTakeProfit();
      double slDist    = MathAbs(openPrice - currentSL);
      double newSL     = currentSL;
      bool   modified  = false;

      if(OrderType() == OP_BUY)
        {
         double profitDist = Bid - openPrice;

         // Breakeven: move SL to entry once profit >= 1x original SL distance.
         if(InpUseBreakeven && slDist > 0 &&
            profitDist >= slDist && currentSL < openPrice)
           {
            newSL    = openPrice;
            modified = true;
           }

         // Trailing stop: trail SL by ATR*1.5 once price is above entry.
         if(InpUseTrailing && profitDist > 0)
           {
            double trailSL = NormalizeDouble(Bid - g_atr * 1.5, _Digits);
            if(trailSL > newSL)
              {
               newSL    = trailSL;
               modified = true;
              }
           }

         if(modified && newSL > currentSL)
           {
            newSL = NormalizeDouble(newSL, _Digits);
            if(!OrderModify(OrderTicket(), openPrice, newSL, currentTP, 0, clrBlue))
               Print(EA_NAME + ": OrderModify BUY failed ticket=",
                     OrderTicket(), " error=", GetLastError());
           }
        }
      else if(OrderType() == OP_SELL)
        {
         double profitDist = openPrice - Ask;

         // Breakeven: move SL to entry once profit >= 1x original SL distance.
         if(InpUseBreakeven && slDist > 0 &&
            profitDist >= slDist && currentSL > openPrice)
           {
            newSL    = openPrice;
            modified = true;
           }

         // Trailing stop: trail SL by ATR*1.5 once price is below entry.
         if(InpUseTrailing && profitDist > 0)
           {
            double trailSL = NormalizeDouble(Ask + g_atr * 1.5, _Digits);
            if(currentSL == 0 || trailSL < newSL)
              {
               newSL    = trailSL;
               modified = true;
              }
           }

         if(modified && (currentSL == 0 || newSL < currentSL))
           {
            newSL = NormalizeDouble(newSL, _Digits);
            if(!OrderModify(OrderTicket(), openPrice, newSL, currentTP, 0, clrBlue))
               Print(EA_NAME + ": OrderModify SELL failed ticket=",
                     OrderTicket(), " error=", GetLastError());
           }
        }
     }
  }


//--- 6g. CloseAllTrades ----------------------------------------------
// Market-closes every open order belonging to this EA on this symbol.
// Called by the daily drawdown kill switch.
void CloseAllTrades()
  {
   Print(EA_NAME + ": CloseAllTrades() triggered. Reason: ", g_killReason);

   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderMagicNumber() != InpMagicNumber)        continue;
      if(OrderSymbol()      != _Symbol)               continue;

      double closePrice = (OrderType() == OP_BUY) ? Bid : Ask;
      bool   closed     = OrderClose(OrderTicket(), OrderLots(), closePrice, 3, clrRed);
      if(!closed)
         Print(EA_NAME + ": Failed to close ticket ", OrderTicket(),
               " error=", GetLastError());
     }
  }

//--- 6h. CloseAllTradesEOD -------------------------------------------
// Market-closes every open order belonging to this EA on this symbol
// at end of day (22:00 IL). Prints per-order confirmation lines.
void CloseAllTradesEOD()
  {
   Print(EA_NAME + ": EOD close triggered — server ",
         StringFormat("%02d:%02d", TimeHour(TimeCurrent()), TimeMinute(TimeCurrent())));

   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderMagicNumber() != InpMagicNumber)        continue;
      if(OrderSymbol()      != _Symbol)               continue;

      string sym        = OrderSymbol();
      double closePrice = (OrderType() == OP_BUY) ? Bid : Ask;
      bool   closed     = OrderClose(OrderTicket(), OrderLots(), closePrice, 3, clrOrange);
      if(closed)
         Print("EOD close: ", sym, " at ", DoubleToString(closePrice, _Digits));
      else
         Print(EA_NAME + ": EOD close failed ticket=", OrderTicket(),
               " error=", GetLastError());
     }
  }

//+------------------------------------------------------------------+
//|  END OF FILE                                                     |
//+------------------------------------------------------------------+
