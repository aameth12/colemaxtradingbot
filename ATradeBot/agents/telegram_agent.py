"""
agents/telegram_agent.py
------------------------
Telegram Agent — responsible for:
  - Sending trade notifications (entry, exit, P&L) to the configured Telegram chat
  - Sending kill-switch alerts when drawdown thresholds are breached
  - Optionally accepting commands from Telegram (e.g. /status, /halt, /resume)
  - Uses TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from utils/config.py
  Note: python-telegram-bot v20+ is async — run this agent inside asyncio.run()
"""


class TelegramAgent:
    def __init__(self):
        pass

    def run(self):
        pass


if __name__ == "__main__":
    agent = TelegramAgent()
    agent.run()
