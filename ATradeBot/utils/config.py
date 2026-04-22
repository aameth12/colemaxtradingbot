"""
utils/config.py
---------------
Central configuration for ATradeBot.
All agents and the run_bot entry point import from here.
Sensitive values (tokens, keys) are loaded from a .env file via python-dotenv.
Never hard-code secrets in this file.
"""

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

# ---------------------------------------------------------------------------
# Broker / Platform
# ---------------------------------------------------------------------------
BROKER_NAME = "Colemax"
PLATFORM = "MetaTrader 4"

# ---------------------------------------------------------------------------
# Tradeable assets
# ---------------------------------------------------------------------------
SYMBOLS = ["XAUUSD", "AAPL", "TSLA", "NVDA", "AMZN"]

# ---------------------------------------------------------------------------
# Timeframes (in minutes — matches M5 / M15 MT4 constants)
# ---------------------------------------------------------------------------
TIMEFRAMES = [5, 15]           # M5 = 5, M15 = 15
PRIMARY_TIMEFRAME = 5          # Signal generation on M5
CONFIRMATION_TIMEFRAME = 15    # Trend filter on M15

# ---------------------------------------------------------------------------
# Risk management
# ---------------------------------------------------------------------------
RISK_PER_TRADE_PCT = 1.0       # Max % of account balance risked per trade
MAX_DRAWDOWN_PCT = 10.0        # Kill-switch threshold: halt all trading if hit
MAX_OPEN_TRADES = 5            # Maximum simultaneous open positions
REWARD_RISK_RATIO = 2.0        # Minimum R:R before entering a trade
SLIPPAGE_POINTS = 3            # Acceptable slippage in broker points

# ---------------------------------------------------------------------------
# Scalping parameters
# ---------------------------------------------------------------------------
SCALP_MIN_PIPS = 5             # Minimum target pips for a scalp trade
SCALP_MAX_HOLD_MINUTES = 60    # Auto-close if trade open longer than this

# ---------------------------------------------------------------------------
# ZeroMQ bridge ports (EA <-> Python)
# EA runs DWX_ZeroMQ_Connector; Python connects to these ports
# ---------------------------------------------------------------------------
ZMQ_PUSH_PORT = 32768          # EA pushes market data / events to Python
ZMQ_PULL_PORT = 32769          # Python pulls from EA (commands -> EA)
ZMQ_SUB_PORT  = 32770          # EA publishes tick data (subscribe from Python)
ZMQ_HOST      = "localhost"    # Host where MT4 terminal runs

# ---------------------------------------------------------------------------
# Telegram notifications
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "YOUR_CHAT_ID_HERE")
TELEGRAM_ENABLED   = True      # Set False to silence all Telegram messages

# ---------------------------------------------------------------------------
# News / economic calendar
# ---------------------------------------------------------------------------
NEWS_API_KEY          = os.getenv("NEWS_API_KEY", "YOUR_NEWS_API_KEY_HERE")
NEWS_HIGH_IMPACT_HALT = True   # Halt trading 15 min before/after high-impact news
NEWS_HALT_MINUTES     = 15     # Minutes before/after event to pause

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
DASHBOARD_HOST  = "0.0.0.0"
DASHBOARD_PORT  = 8050
DASHBOARD_DEBUG = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL    = "DEBUG"         # DEBUG | INFO | WARNING | ERROR
LOG_FILE     = os.path.join(os.path.dirname(__file__), "..", "logs", "atradebot.log")
LOG_ROTATION = "10 MB"         # Loguru rotation size

# ---------------------------------------------------------------------------
# File paths (shared state, trade log)
# ---------------------------------------------------------------------------
BASE_DIR          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED_STATE_PATH = os.path.join(BASE_DIR, "utils", "shared_state.json")
TRADE_LOG_PATH    = os.path.join(BASE_DIR, "data", "trade_log.csv")
