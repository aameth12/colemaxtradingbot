"""
run_bot.py
----------
ATradeBot entry point — starts all agents as daemon threads and keeps the
process alive while running the weekly-learning scheduler.

Usage::

    cd ATradeBot/
    python run_bot.py

Agents
------
DataAgent       : polls yfinance every 60 s, writes indicator_data to shared state
NewsAgent       : scrapes ForexFactory every 30 min, writes news pause flag
TelegramAgent   : Telegram bot — commands + auto-alerts (own async event loop)
DashboardAgent  : Dash web UI on http://localhost:8050
StrategyAgent   : signal generation (stub — activates when implemented)
RiskAgent       : drawdown monitor + kill switch (stub — activates when implemented)
ExecutionAgent  : ZeroMQ bridge to MT4 EA (stub — activates when implemented)

Weekly learning scheduler fires every Saturday at 09:00 local time.
"""

import sys
import threading
import time

import schedule

from agents.dashboard_agent  import DashboardAgent
from agents.data_agent       import DataAgent
from agents.execution_agent  import ExecutionAgent
from agents.news_agent       import NewsAgent
from agents.risk_agent       import RiskAgent
from agents.strategy_agent   import StrategyAgent
from agents.telegram_agent   import TelegramAgent
from utils.logger            import logger
from weekly_learning         import run_analysis


# ---------------------------------------------------------------------------
# Thread launcher
# ---------------------------------------------------------------------------

def _start(name: str, target) -> threading.Thread:
    t = threading.Thread(target=target, name=name, daemon=True)
    t.start()
    logger.info(f"{name} thread started")
    return t


# ---------------------------------------------------------------------------
# Watchdog: log if any thread dies unexpectedly
# ---------------------------------------------------------------------------

def _watchdog(threads: list[threading.Thread]) -> None:
    while True:
        time.sleep(30)
        for t in threads:
            if not t.is_alive():
                logger.error(
                    f"Thread '{t.name}' has stopped unexpectedly — "
                    "restart run_bot.py or check the logs"
                )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("Starting ATradeBot...")
    logger.info("ATradeBot starting")

    threads: list[threading.Thread] = []

    # Core agents — each blocks internally, so each gets its own thread
    threads.append(_start("data_agent",      DataAgent().run))
    threads.append(_start("news_agent",      NewsAgent().run))
    threads.append(_start("telegram_agent",  TelegramAgent().run))
    threads.append(_start("dashboard_agent", DashboardAgent().run))

    # Stub agents (no-op until implemented; threads will exit immediately
    # and be flagged by the watchdog — harmless until real logic is added)
    threads.append(_start("strategy_agent",  StrategyAgent().run))
    threads.append(_start("risk_agent",      RiskAgent().run))
    threads.append(_start("execution_agent", ExecutionAgent().run))

    # Weekly learning — schedule fires the job; this thread handles it
    schedule.every().saturday.at("09:00").do(run_analysis)
    logger.info("Weekly learning scheduler registered (Saturdays 09:00)")

    # Background watchdog
    _start("watchdog", lambda: _watchdog(threads))

    logger.info("All agents started")
    print("ATradeBot running.")
    print("  Dashboard   : http://localhost:8050")
    print("  Logs        : logs/atradebot.log")
    print("  Stop        : Ctrl-C")
    print()

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print("\nShutting down ATradeBot...")
        logger.info("ATradeBot stopped by user (KeyboardInterrupt)")
        sys.exit(0)


if __name__ == "__main__":
    main()
