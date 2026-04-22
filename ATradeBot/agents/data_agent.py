"""
agents/data_agent.py
--------------------
Data Agent — responsible for:
  - Connecting to the MT4 EA via ZeroMQ (SUB socket on ZMQ_SUB_PORT)
  - Receiving live tick data for XAUUSD, AAPL, TSLA, NVDA, AMZN
  - Writing market snapshots to utils/shared_state.json (market_data section)
  - Providing OHLCV candle data on M5 and M15 timeframes to other agents
"""


class DataAgent:
    def __init__(self):
        pass

    def run(self):
        pass


if __name__ == "__main__":
    agent = DataAgent()
    agent.run()
