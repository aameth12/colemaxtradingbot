# ATradeBot

An automated scalping bot for MetaTrader 4 on an **ATrade** brokerage account. The bot combines an MQL4 Expert Advisor (EA) running inside MT4 with a Python multi-agent backend connected via ZeroMQ. It trades **XAUUSD, AAPL, TSLA, NVDA, and AMZN** on M5 and M15 timeframes.

---

## What it does

- **Executes trades automatically** in MT4 using ATR-based stop losses, 2:1 risk/reward, and dynamic lot sizing
- **Filters by market session** — gold trades during Israeli market hours (10:00–21:00 IL), stocks during US pre-market overlap (16:30–21:00 IL)
- **Pauses around high-impact news** by scraping ForexFactory's economic calendar
- **Sends alerts and accepts commands** via a Telegram bot
- **Shows a live dashboard** at `http://localhost:8050` with charts, open positions, and trade history
- **Learns weekly** — analyses win rates per symbol and per hour, disables underperforming symbols, and increases risk when performance is strong

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         MT4 Terminal                            │
│   ATradeBot.mq4 EA  ←──── ZeroMQ ────→  ExecutionAgent.py     │
│   (order execution)                     (command routing)      │
└─────────────────────────────────────────────────────────────────┘
                                │
                    shared_state.json  ←──── DataAgent.py
                                │              (yfinance OHLCV + indicators)
                                │
                    ┌───────────┴───────────┐
                    │                       │
               NewsAgent.py          StrategyAgent.py
               (ForexFactory          (signals — stub)
                calendar)
                    │                       │
                    └───────────┬───────────┘
                                │
                         RiskAgent.py
                         (drawdown / kill switch — stub)
                                │
                    ┌───────────┴───────────┐
                    │                       │
            TelegramAgent.py        DashboardAgent.py
            (alerts + commands)     (localhost:8050)
```

All agents communicate through a single JSON file (`utils/shared_state.json`). The MT4 EA reads signals from and writes account state to the same file via a file bridge, or directly via ZeroMQ when live.

---

## Features

| Feature | Detail |
|---|---|
| Symbols | XAUUSD, AAPL, TSLA, NVDA, AMZN |
| Timeframes | M5 (signal), M15 (trend filter) |
| Lot sizing | ATR-based dynamic sizing: `lots = (balance × risk%) ÷ (SL_pips × pip_value)` |
| Stop loss | Lowest/highest low of last 5 bars ± ATR × 0.5 |
| Take profit | 2× SL distance (2:1 R:R) |
| Trailing stop | ATR × 1.5 trailing, breakeven at 1× SL profit |
| Kill switch | Halts all trading if daily drawdown exceeds 10% |
| News filter | Pauses ±30 min around high-impact USD events (ForexFactory) |
| Session filter | Israel-time windows, Monday buffer, Friday 19:00 cutoff |
| EOD close | All trades closed at 22:00 Israel time |
| Dashboard | Dash/Plotly on port 8050, refreshes every 5 seconds |
| Telegram | 8 commands + auto-alerts for entries, exits, kill switch, news pauses |
| Weekly learning | Disables symbols with <40% win rate for 2 weeks; increases risk when >60% |
| Tests | 9-point pre-flight test suite — run before connecting MT4 |

---

## Prerequisites

| Software | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.10 minimum (uses `zoneinfo`, `match`) |
| MetaTrader 4 | Any | Must be running on **Windows** |
| MT4 terminal | ATrade account | Any MT4 broker works |
| ZeroMQ for MT4 | DWX_ZeroMQ_Connector | Free, see setup below |
| Telegram account | Any | To create the bot |

> The Python backend can run on **Windows, macOS, or Linux**. The MT4 terminal itself is Windows-only. Many traders run MT4 on a Windows VPS and the Python code on their main machine or the same VPS.

---

## Setup Manual

### Step 1 — Clone the repository

```bash
git clone https://github.com/aameth12/atrade.git
cd atrade
```

### Step 2 — Install Python 3.11+

**Windows**
1. Download from [python.org/downloads](https://www.python.org/downloads/)
2. During install, check **"Add Python to PATH"**
3. Verify: open Command Prompt and run `python --version`

**macOS**
```bash
brew install python@3.11
```

**Linux (Ubuntu/Debian)**
```bash
sudo apt update && sudo apt install python3.11 python3.11-venv python3-pip
```

### Step 3 — Create a virtual environment

```bash
cd ATradeBot
python -m venv .venv
```

Activate it:

| Platform | Command |
|---|---|
| Windows (cmd) | `.venv\Scripts\activate.bat` |
| Windows (PowerShell) | `.venv\Scripts\Activate.ps1` |
| macOS / Linux | `source .venv/bin/activate` |

You should see `(.venv)` at the start of your prompt.

### Step 4 — Install dependencies

```bash
pip install -r requirements.txt
```

This installs everything — yfinance, pandas_ta, Dash, ZeroMQ, Telegram, loguru, and more. It will take 1–3 minutes.

> **Windows note:** If `pyzmq` fails to install, first run `pip install --upgrade pip setuptools wheel`, then retry.

### Step 5 — Create your `.env` file

```bash
cp .env.example .env
```

Open `.env` in any text editor and fill in your values:

```
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=-1001234567890
NEWS_API_KEY=optional_not_required
```

The bot works without `NEWS_API_KEY` — it scrapes ForexFactory directly.

### Step 6 — Create a Telegram bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the token BotFather gives you → paste into `TELEGRAM_BOT_TOKEN` in your `.env`

**Find your Chat ID:**
1. Search for **@userinfobot** in Telegram and send `/start`
2. It will reply with your user ID — paste that into `TELEGRAM_CHAT_ID`

> For a group chat, add your bot to the group, send any message, then visit:
> `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
> and find `"chat":{"id":-1001234567890}` — that negative number is your group Chat ID.

### Step 7 — Install the ZeroMQ connector in MT4

This is the bridge that lets Python talk to the MT4 terminal.

1. Download **DWX_ZeroMQ_Connector** from GitHub:
   [github.com/darwinex/dwx-zeromq-connector](https://github.com/darwinex/dwx-zeromq-connector)

2. Copy the following files into your MT4 `MQL4/Libraries/` folder:
   - `libzmq.dll`
   - `mql4/Libraries/Zmq.mqh`

3. Restart MT4.

**How to find your MT4 data folder:**
In MT4: menu **File → Open Data Folder** → navigate to `MQL4/`

### Step 8 — Install the ATradeBot EA in MT4

1. Copy `ATradeBot/EA/ATradeBot.mq4` into your MT4 `MQL4/Experts/` folder
2. In MT4, press **F5** or click **Refresh** in the Navigator panel
3. **ATradeBot** will appear under **Expert Advisors**

### Step 9 — Attach the EA to a chart

1. In MT4, open a chart for one of your symbols (e.g. XAUUSD)
2. Set the timeframe to **M5**
3. Drag **ATradeBot** from the Navigator onto the chart
4. In the EA settings dialog:

| Input | Recommended value | Description |
|---|---|---|
| InpUseRiskPct | true | Use percentage-based lot sizing |
| InpRiskPct | 2.0 | Risk 2% of balance per trade |
| InpMagicNumber | 20250101 | Unique ID for this EA's trades |
| InpServerToILOffset | 0 | Offset from server time to Israel time (adjust if needed) |
| InpUseATRStops | true | ATR-based stop losses |

5. Click **OK** — the EA smiley face in the top-right corner of the chart should be **green**

> **Allow live trading:** Go to **Tools → Options → Expert Advisors** and check **"Allow automated trading"**. Also click the **AutoTrading** button in the MT4 toolbar (it turns green when enabled).

### Step 10 — Run the pre-flight tests

Before connecting everything together, run the diagnostic suite:

```bash
cd ATradeBot
python test_bot.py
```

Expected output:
```
ATradeBot — pre-flight diagnostic tests

  PASS  1. Config loads correctly
  PASS  2. shared_state.json is writable
  PASS  3. yfinance returns data for all 5 symbols
  PASS  4. Telegram token is valid (test message sent)
  PASS  5. Dashboard starts on port 8050
  PASS  6. trade_log.csv is writable
  PASS  7. Session filter correct for known times
  PASS  8. Kill switch triggers at correct threshold
  PASS  9. Lot size calculation is valid

All tests passed — ready to connect MT4.
```

Fix any `FAIL` lines before proceeding. Common failures:
- **Test 3** fails with no internet → check your connection
- **Test 4** fails → double-check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`
- **Test 5** fails → port 8050 already in use — check `DASHBOARD_PORT` in `config.py`

### Step 11 — Start the bot

```bash
cd ATradeBot
python run_bot.py
```

You will see:
```
Starting ATradeBot...
ATradeBot running.
  Dashboard   : http://localhost:8050
  Logs        : logs/atradebot.log
  Stop        : Ctrl-C
```

Open `http://localhost:8050` in your browser to see the live dashboard.

Check your Telegram — the bot should be online and accepting `/status` commands.

### Step 12 — Verify MT4 is connected

With the EA running in MT4 and `run_bot.py` running in Python:

1. Open MT4's **Experts** tab at the bottom — you should see log lines from ATradeBot.mq4
2. Send `/status` to your Telegram bot — it should show agent statuses
3. The dashboard at `localhost:8050` should show a **green** status dot

> If the EA shows "not connected" errors, check that MT4 and Python are on the same machine, and that the ZeroMQ ports (32768, 32769, 32770) are not blocked by your firewall.

---

## Stopping the bot

Press **Ctrl-C** in the terminal running `run_bot.py`.

To also stop the EA: right-click the chart in MT4 → **Expert Advisors → Remove**.

---

## Configuration reference

All settings live in `utils/config.py`. Secrets come from `ATradeBot/.env`.

| Setting | Default | Description |
|---|---|---|
| `SYMBOLS` | `["XAUUSD", "AAPL", "TSLA", "NVDA", "AMZN"]` | Active trading symbols |
| `RISK_PER_TRADE_PCT` | `1.0` | % of balance risked per trade |
| `MAX_DRAWDOWN_PCT` | `10.0` | Kill-switch threshold |
| `MAX_OPEN_TRADES` | `5` | Maximum simultaneous positions |
| `REWARD_RISK_RATIO` | `2.0` | Minimum R:R to take a trade |
| `NEWS_HIGH_IMPACT_HALT` | `True` | Pause trading for high-impact news |
| `NEWS_HALT_MINUTES` | `15` | Minutes before/after event to pause |
| `DASHBOARD_PORT` | `8050` | Port for the live dashboard |
| `LOG_LEVEL` | `"DEBUG"` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `TELEGRAM_ENABLED` | `True` | Set `False` to silence all Telegram messages |

---

## Telegram commands

Send these to your bot in Telegram. Only messages from your configured `TELEGRAM_CHAT_ID` are processed.

| Command | What it shows |
|---|---|
| `/status` | Kill switch, open trades, today P&L, balance, equity, drawdown, running agents |
| `/today` | All closed trades today: symbol, direction, price, profit, win/loss |
| `/alltime` | Total trades, win rate, total P&L, best and worst trade |
| `/assets` | Each symbol: tradeable now?, last signal + confidence, today's trade count |
| `/streak` | Current streak, longest win streak, longest loss streak |
| `/session` | Active sessions now in Israel time, countdown to next session open |
| `/pause` | Manually pause all trading (writes to shared state) |
| `/resume` | Resume trading after a manual pause |

**Auto-alerts (no command needed):**

```
BUY XAUUSD @ 2345.50 | SL: 2340.00 | TP: 2356.00
CLOSED XAUUSD +45 pips | +$23.50 | Win
KILL SWITCH: daily loss limit hit. All trades closed.
NEWS PAUSE: NFP in 15 min. Trading paused.
```

---

## Live dashboard

Open `http://localhost:8050` while the bot is running.

| Section | Contents |
|---|---|
| Header | Status dot (green = agents running), live UTC clock, today P&L |
| Metrics | Open trades · Win rate · Today pips · Balance · Drawdown (turns red above 8%) |
| Equity curve | Cumulative P&L over all recorded trades |
| Win/loss chart | Wins vs losses grouped by symbol |
| Session heatmap | Trade activity by UTC hour and day of week |
| Open positions | Live table — green rows = profitable, red rows = losing |
| Trade log | Last 50 closed trades, sortable by any column |

Refreshes every 5 seconds automatically.

---

## Weekly learning

Every Saturday at 09:00 (system local time), `weekly_learning.py` runs automatically:

1. Loads the full trade history from `data/trade_log.csv`
2. Calculates win rate per symbol, win rate per hour of day, and average R:R ratio
3. **Disables a symbol** if its win rate is below 40% for two consecutive weeks
4. **Increases risk by 10%** (up to a +20% cap above baseline) if overall win rate exceeds 60%
5. Saves this week's stats to `data/weekly_stats.json`
6. Sends a full summary to your Telegram

> Configuration changes (symbol list, risk percentage) are written directly to `utils/config.py`. A **restart of `run_bot.py`** is required for them to take effect. The Telegram summary will say so when changes are applied.

---

## Project file structure

```
atrade/
│
├── README.md                        ← you are here
│
└── ATradeBot/
    ├── run_bot.py                   ← START HERE — launches all agents
    ├── test_bot.py                  ← run this first to check everything works
    ├── weekly_learning.py           ← weekly analysis + config auto-adjustment
    ├── requirements.txt             ← Python dependencies
    ├── .env.example                 ← copy to .env and fill in secrets
    │
    ├── EA/
    │   └── ATradeBot.mq4            ← MQL4 Expert Advisor (copy to MT4)
    │
    ├── agents/
    │   ├── data_agent.py            ← OHLCV + indicators via yfinance (60s)
    │   ├── news_agent.py            ← ForexFactory calendar scraper (30min)
    │   ├── telegram_agent.py        ← Telegram bot — commands and auto-alerts
    │   ├── dashboard_agent.py       ← Dash web UI on port 8050
    │   ├── strategy_agent.py        ← signal generation (stub)
    │   ├── risk_agent.py            ← drawdown monitor + kill switch (stub)
    │   └── execution_agent.py       ← ZeroMQ bridge to MT4 EA (stub)
    │
    ├── utils/
    │   ├── config.py                ← all settings — edit this file
    │   ├── logger.py                ← loguru rotating file logger
    │   └── shared_state.json        ← live state shared between all agents
    │
    ├── data/
    │   ├── trade_log.csv            ← closed trade history (written by EA)
    │   └── weekly_stats.json        ← auto-created by weekly_learning.py
    │
    └── logs/
        └── atradebot.log            ← auto-created on first run
```

---

## Agent descriptions

| Agent | File | Status | Interval |
|---|---|---|---|
| DataAgent | `agents/data_agent.py` | Complete | Every 60 s |
| NewsAgent | `agents/news_agent.py` | Complete | Calendar 30 min, state 60 s |
| TelegramAgent | `agents/telegram_agent.py` | Complete | Event-driven + 5 s monitor |
| DashboardAgent | `agents/dashboard_agent.py` | Complete | 5 s UI refresh |
| StrategyAgent | `agents/strategy_agent.py` | Stub | — |
| RiskAgent | `agents/risk_agent.py` | Stub | — |
| ExecutionAgent | `agents/execution_agent.py` | Stub | — |

The three stub agents start as daemon threads but do nothing until their logic is implemented. The MT4 EA handles risk management and execution directly until those agents are completed.

---

## Troubleshooting

**MT4 EA shows "DLL imports not allowed"**
Go to **Tools → Options → Expert Advisors** and check **"Allow DLL imports"**.

**`python test_bot.py` test 3 fails (yfinance)**
Markets may be closed (weekend). yfinance returns empty data outside trading hours for some symbols. Try again on a weekday, or check your internet connection.

**Dashboard shows all zeros**
The DataAgent needs a minute to complete its first poll. Wait 60–90 seconds and refresh the page.

**Telegram bot doesn't respond**
- Confirm `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in your `.env` are correct
- Make sure you sent the bot a message first (bots can't initiate conversations)
- Check `logs/atradebot.log` for errors

**EA placed a trade but Python doesn't see it**
The ZeroMQ ExecutionAgent is currently a stub. Trades placed by the EA are independent of Python until the ExecutionAgent is implemented. The trade log (`data/trade_log.csv`) must be written by the EA or ExecutionAgent manually.

**`pyzmq` install fails on Windows**
```bash
pip install --upgrade pip setuptools wheel
pip install pyzmq
```

**Port 8050 already in use**
Change `DASHBOARD_PORT` in `utils/config.py` to any free port (e.g. 8051).

---

## Risk disclaimer

This software is provided for educational and research purposes. Automated trading carries significant financial risk. Past performance does not guarantee future results. Always test on a **demo account** before using real money. You are solely responsible for any trading losses incurred while using this software.

---

## License

MIT License — see `LICENSE` file for details.
