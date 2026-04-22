"""
agents/news_agent.py
--------------------
News Agent — responsible for:
  - Polling an economic calendar API for high-impact news events
  - Writing upcoming event info to shared_state.json news_events section
  - Setting trading_halted_for_news = true within NEWS_HALT_MINUTES of an event
  - Assets monitored: XAUUSD (Fed, CPI, NFP), AAPL/TSLA/NVDA/AMZN (earnings, macro)
"""


class NewsAgent:
    def __init__(self):
        pass

    def run(self):
        pass


if __name__ == "__main__":
    agent = NewsAgent()
    agent.run()
