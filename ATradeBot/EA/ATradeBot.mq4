//+------------------------------------------------------------------+
//|                                                    ATradeBot.mq4 |
//|                                    ATradeBot — Colemax MT4 EA    |
//|                                                                   |
//| STATUS: EA not implemented yet.                                   |
//|                                                                   |
//| This Expert Advisor is the MT4-side bridge between the Colemax   |
//| terminal and the Python agent system via ZeroMQ.                 |
//|                                                                   |
//| Planned responsibilities:                                         |
//|   - Publish live tick data on ZMQ_SUB_PORT  (32770)              |
//|   - Receive order commands from Python on ZMQ_PULL_PORT (32769)  |
//|   - Execute market/limit orders on behalf of Python agents        |
//|   - Report account info and position updates back to Python       |
//|   - Implements DWX_ZeroMQ_Connector protocol                     |
//|                                                                   |
//| Assets   : XAUUSD, AAPL, TSLA, NVDA, AMZN (Stock CFDs)          |
//| Timeframes: M5 (primary), M15 (confirmation)                     |
//| Broker   : Colemax                                                |
//+------------------------------------------------------------------+
