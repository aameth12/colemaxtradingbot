"""
agents/execution_agent.py
-------------------------
Execution Agent — responsible for:
  - Receiving approved trade signals from RiskAgent
  - Sending order commands to the MT4 EA via ZeroMQ (PUSH socket on ZMQ_PULL_PORT)
  - Logging all executions to data/trade_log.csv
  - Handling order confirmations and rejections from the EA
"""


class ExecutionAgent:
    def __init__(self):
        pass

    def run(self):
        pass


if __name__ == "__main__":
    agent = ExecutionAgent()
    agent.run()
