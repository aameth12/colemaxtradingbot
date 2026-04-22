"""
agents/risk_agent.py
--------------------
Risk Agent — responsible for:
  - Monitoring account drawdown against MAX_DRAWDOWN_PCT from config.py
  - Enforcing RISK_PER_TRADE_PCT position sizing per trade
  - Activating the kill_switch in shared_state.json when thresholds are breached
  - Validating signals from StrategyAgent before forwarding to ExecutionAgent
"""


class RiskAgent:
    def __init__(self):
        pass

    def run(self):
        pass


if __name__ == "__main__":
    agent = RiskAgent()
    agent.run()
