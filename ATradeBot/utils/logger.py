"""
utils/logger.py
---------------
Centralised logging setup using loguru.
All agents import ``logger`` from here instead of configuring their own.

Usage::

    from utils.logger import logger
    logger.info("Agent started")

A rotating file handler is configured automatically on import.
Agents may call ``logger.add()`` again to add extra sinks (e.g. Telegram).
"""

import os
from loguru import logger

from utils.config import LOG_FILE, LOG_LEVEL, LOG_ROTATION

# Create the log directory if it does not exist yet
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logger.add(
    LOG_FILE,
    rotation=LOG_ROTATION,
    level=LOG_LEVEL,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {name}:{line} | {message}",
    enqueue=True,       # thread-safe; writes happen in a background thread
    backtrace=True,     # include full traceback on exceptions
    diagnose=False,     # set True locally to see variable values in tracebacks
)

__all__ = ["logger"]
