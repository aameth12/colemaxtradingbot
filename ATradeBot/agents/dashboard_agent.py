"""
agents/dashboard_agent.py
-------------------------
Dashboard Agent — responsible for:
  - Serving a web dashboard (Flask or Dash) on DASHBOARD_HOST:DASHBOARD_PORT
  - Displaying live account info, open positions, agent statuses, and P&L
  - Reading all display data from utils/shared_state.json (no direct broker calls)
  - Refreshing automatically every few seconds via polling or WebSocket push
"""


class DashboardAgent:
    def __init__(self):
        pass

    def run(self):
        pass


if __name__ == "__main__":
    agent = DashboardAgent()
    agent.run()
