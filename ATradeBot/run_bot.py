"""
run_bot.py
----------
ATradeBot entry point.
Starts all Python agents as concurrent threads or processes.

Usage:
    cd ATradeBot/
    python run_bot.py

Agents started:
    - DataAgent      — live market data via ZeroMQ from MT4 EA
    - StrategyAgent  — signal generation on M5/M15
    - RiskAgent      — drawdown monitoring and position sizing
    - ExecutionAgent — order routing to MT4 EA via ZeroMQ
    - NewsAgent      — economic calendar and news halt logic
    - TelegramAgent  — notifications and remote commands
    - DashboardAgent — web dashboard on port 8050
"""

import threading

from agents.data_agent import DataAgent
from agents.strategy_agent import StrategyAgent
from agents.risk_agent import RiskAgent
from agents.execution_agent import ExecutionAgent
from agents.news_agent import NewsAgent
from agents.telegram_agent import TelegramAgent
from agents.dashboard_agent import DashboardAgent
from utils.logger import logger


def main():
    # TODO: Reset shared_state.json to template defaults before starting agents
    # TODO: Instantiate each agent, wrap in daemon threads, start all, then join
    logger.info("ATradeBot starting — not yet implemented")
    pass


if __name__ == "__main__":
    main()
