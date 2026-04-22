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
input double InpRiskPct         = 1.0;    // % of balance to risk per trade (e.g. 1.0 = 1%)
input double InpMinLot          = 0.01;   // Minimum allowed lot size
input double InpMaxLot          = 5.0;    // Maximum allowed lot size

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
input bool   InpUseTimeFilter   = true;   // Restrict trading to defined session hours
input int    InpSessionStartHr  = 8;      // Session open hour (broker server time)
input int    InpSessionEndHr    = 20;     // Session close hour (broker server time)
input bool   InpNoTradeOnFriday = true;   // Block new trades after Friday cutoff
input int    InpFridayCutoffHr  = 18;     // Last hour to open trades on Friday

// --- Risk / kill switch ---
input double InpMaxDailyDrawPct = 5.0;    // Daily drawdown % that triggers kill switch
input int    InpMaxDailyTrades  = 10;     // Maximum trades allowed per day
input int    InpMagicNumber     = 20240001; // Unique EA identifier — change if running multiple EAs


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
   if(InpSessionStartHr >= InpSessionEndHr)
     {
      Alert(EA_NAME + ": InpSessionStartHr must be before InpSessionEndHr.");
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
   Print("Session      : ", InpSessionStartHr, ":00 – ", InpSessionEndHr, ":00 server time");
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
      Print(EA_NAME + ": New trading day started. Balance reset to ", g_dailyStartBalance);
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

   // --- Signal evaluation ---
   // TODO: add "no existing trade in same direction" guard before calling OrderSend.
   if(BullishSignal())
     {
      // TODO: calculate entry, SL, TP via GetLotSize() / ATR, then OrderSend BUY.
     }
   else if(BearishSignal())
     {
      // TODO: calculate entry, SL, TP via GetLotSize() / ATR, then OrderSend SELL.
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
bool IsTradingAllowed()
  {
   // --- Broker / terminal checks ---
   if(!IsTradeAllowed())   return(false);   // EA not authorised to trade
   if(!IsConnected())      return(false);   // No connection to broker
   if(IsTradeContextBusy()) return(false);  // Another EA/script is using the trade context
   if(IsStopped())         return(false);   // Terminal is shutting down

   // --- Session time filter ---
   if(InpUseTimeFilter)
     {
      int currentHour = TimeHour(TimeCurrent());

      // Block outside configured session window.
      if(currentHour < InpSessionStartHr || currentHour >= InpSessionEndHr)
         return(false);

      // Block new trades after Friday cutoff.
      if(InpNoTradeOnFriday && DayOfWeek() == 5 && currentHour >= InpFridayCutoffHr)
         return(false);

      // Block on Sunday (market open gaps).
      if(DayOfWeek() == 0)
         return(false);
     }

   // TODO: add spread filter (reject if current spread > X pips)
   // TODO: add minimum bar volatility check (reject low-ATR environments)

   return(true);
  }


//--- 6c. GetLotSize --------------------------------------------------
// Returns a lot size based on the account balance and the current ATR-
// derived stop loss distance, clamped to InpMinLot / InpMaxLot.
// Falls back to InpLotSize when InpUseRiskPct is false.
double GetLotSize()
  {
   if(!InpUseRiskPct)
      return(InpLotSize);

   // TODO: implement risk-based sizing:
   //   riskAmount  = AccountBalance() * InpRiskPct / 100.0
   //   slDistance  = InpUseATRStops ? g_atr * InpATRSLMulti : InpSLPips * g_pipValue
   //   tickValue   = MarketInfo(_Symbol, MODE_TICKVALUE)
   //   tickSize    = MarketInfo(_Symbol, MODE_TICKSIZE)
   //   lotSize     = riskAmount / (slDistance / tickSize * tickValue)
   //   return MathMax(InpMinLot, MathMin(InpMaxLot, NormalizeDouble(lotSize, 2)))

   return(InpLotSize);   // placeholder until TODO above is implemented
  }


//--- 6d. BullishSignal -----------------------------------------------
// Returns true when all configured indicators collectively agree on a
// long entry. All values come from the g_* globals set by RefreshIndicators().
bool BullishSignal()
  {
   // TODO: implement confluence checks, e.g.:
   //   - Price above g_sma200            (uptrend filter)
   //   - g_ema9 > g_ema21               (fast MA above slow MA)
   //   - g_macdMain > g_macdSignal      (MACD bullish crossover)
   //   - g_rsi > 50 && g_rsi < InpRSIOverbought
   //   - g_stochMain > g_stochSignal && g_stochMain < InpStochOverbought
   //   - g_adx > InpADXMinStrength      (trend has sufficient strength)
   //   - g_diPlus > g_diMinus           (positive directional bias)
   //   - Close[1] > g_bbMiddle          (price above BB basis)

   return(false);
  }


//--- 6e. BearishSignal -----------------------------------------------
// Returns true when all configured indicators collectively agree on a
// short entry. Mirror logic of BullishSignal().
bool BearishSignal()
  {
   // TODO: implement confluence checks, e.g.:
   //   - Price below g_sma200
   //   - g_ema9 < g_ema21
   //   - g_macdMain < g_macdSignal
   //   - g_rsi < 50 && g_rsi > InpRSIOversold
   //   - g_stochMain < g_stochSignal && g_stochMain > InpStochOversold
   //   - g_adx > InpADXMinStrength
   //   - g_diMinus > g_diPlus
   //   - Close[1] < g_bbMiddle

   return(false);
  }


//--- 6f. ManageOpenTrades --------------------------------------------
// Iterates all open orders belonging to this EA (matched by magic number
// and symbol) and applies trailing stop and breakeven logic.
void ManageOpenTrades()
  {
   // TODO: implement per-order management:
   //
   //   for(int i = OrdersTotal() - 1; i >= 0; i--)
   //     {
   //       if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
   //       if(OrderMagicNumber() != InpMagicNumber)        continue;
   //       if(OrderSymbol()      != _Symbol)               continue;
   //
   //       double currentProfit = OrderType() == OP_BUY
   //                              ? Bid - OrderOpenPrice()
   //                              : OrderOpenPrice() - Ask;
   //
   //       // Breakeven: once InpBreakevenPips in profit, move SL to open price.
   //       // Trailing:  once InpTrailStartPips in profit, trail SL by InpTrailStepPips.
   //     }
  }


//--- 6g. CloseAllTrades ----------------------------------------------
// Market-closes every open order belonging to this EA on this symbol.
// Called by the daily drawdown kill switch.
void CloseAllTrades()
  {
   Print(EA_NAME + ": CloseAllTrades() triggered. Reason: ", g_killReason);

   // TODO: implement order closure loop:
   //
   //   for(int i = OrdersTotal() - 1; i >= 0; i--)
   //     {
   //       if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
   //       if(OrderMagicNumber() != InpMagicNumber)        continue;
   //       if(OrderSymbol()      != _Symbol)               continue;
   //
   //       double closePrice = OrderType() == OP_BUY ? Bid : Ask;
   //       bool   closed     = OrderClose(OrderTicket(), OrderLots(), closePrice, 3, clrRed);
   //       if(!closed)
   //          Print(EA_NAME + ": Failed to close ticket ", OrderTicket(), " Error: ", GetLastError());
   //     }
  }

//+------------------------------------------------------------------+
//|  END OF FILE                                                     |
//+------------------------------------------------------------------+
