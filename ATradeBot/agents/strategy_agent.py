"""
agents/strategy_agent.py
------------------------
Strategy Agent — responsible for:
  - Reading market data from shared_state.json (populated by DataAgent)
  - Implementing scalping strategies on M5 (primary) and M15 (trend filter)
  - Writing trade signals (BUY / SELL / HOLD) to shared_state.json last_signals
  - Supported assets: XAUUSD, AAPL, TSLA, NVDA, AMZN
"""


class StrategyAgent:
    def __init__(self):
        pass

    def run(self):
        pass


if __name__ == "__main__":
    agent = StrategyAgent()
    agent.run()
