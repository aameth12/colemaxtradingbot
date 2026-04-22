"""
utils/logger.py
---------------
Centralized logging setup using loguru.
All agents import `logger` from here instead of configuring their own.

Usage:
    from utils.logger import logger
    logger.info("Agent started")
"""

from loguru import logger  # noqa: F401

# TODO: Configure log rotation, log file path, and log level from config.py
# Example:
#   from utils.config import LOG_LEVEL, LOG_FILE, LOG_ROTATION
#   import os
#   os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
#   logger.add(LOG_FILE, rotation=LOG_ROTATION, level=LOG_LEVEL)
