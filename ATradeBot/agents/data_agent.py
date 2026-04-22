"""
agents/data_agent.py
--------------------
Data Agent — fetches OHLCV candles for all configured symbols via yfinance,
computes technical indicators with pandas_ta, and writes the latest snapshot
for each symbol/timeframe into utils/shared_state.json every 60 seconds.

Public API (importable by other agents):
    fetch_price_data(symbol, timeframe)  -> pd.DataFrame  (last 100 candles)
    calculate_indicators(df)             -> pd.DataFrame  (OHLCV + indicator cols)
    update_shared_state(symbol, data)    -> None          (writes to shared JSON)
"""

import json
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf
import pandas_ta  # noqa: F401 — side-effect: registers .ta accessor on DataFrame

from utils.config import (
    SYMBOLS,
    SHARED_STATE_PATH,
    LOG_FILE,
    LOG_LEVEL,
    LOG_ROTATION,
)
from utils.logger import logger


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# yfinance ticker symbols differ from internal names for spot gold
YFINANCE_MAP: dict[str, str] = {
    "XAUUSD": "XAUUSD=X",
    "AAPL":   "AAPL",
    "TSLA":   "TSLA",
    "NVDA":   "NVDA",
    "AMZN":   "AMZN",
}

# Fetch period per timeframe — long enough for SMA(200) warm-up bars.
# yfinance hard-limits intraday history to 60 days for both 5m and 15m.
FETCH_PERIOD: dict[str, str] = {
    "5m":  "30d",   # ~30 d × 8 h/d × 12 bars/h ≈ 2 880 bars
    "15m": "60d",   # ~60 d × 8 h/d × 4  bars/h ≈ 1 920 bars
}

TIMEFRAMES: list[str] = ["5m", "15m"]
POLL_INTERVAL: int = 60  # seconds between full refresh cycles

# Single lock serialises all reads and writes to shared_state.json so that
# concurrent agents (running in threads) never corrupt the file.
_file_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_price_data(symbol: str, timeframe: str) -> pd.DataFrame:
    """
    Download OHLCV candles for *symbol* / *timeframe* via yfinance and return
    the **last 100 rows**.

    A longer period is fetched internally so that ``calculate_indicators()``
    has enough warm-up bars for SMA(200); only the 100 most-recent completed
    bars are returned.

    Parameters
    ----------
    symbol    : Internal name — "XAUUSD", "AAPL", "TSLA", "NVDA", or "AMZN".
    timeframe : yfinance interval string — "5m" or "15m".

    Returns
    -------
    pd.DataFrame
        Columns : Open, High, Low, Close, Volume.
        Index   : datetime (timezone-aware UTC).

    Raises
    ------
    ValueError
        If yfinance returns an empty response.
    """
    ticker = YFINANCE_MAP.get(symbol, symbol)
    period = FETCH_PERIOD.get(timeframe, "30d")

    raw = yf.download(
        ticker,
        period=period,
        interval=timeframe,
        auto_adjust=True,
        progress=False,
    )

    if raw is None or raw.empty:
        raise ValueError(
            f"yfinance returned no data for {symbol} "
            f"(ticker={ticker}, interval={timeframe}, period={period})"
        )

    # yfinance emits MultiIndex columns for single-ticker downloads in some
    # versions; flatten to simple column names.
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index.name = "datetime"
    return df.tail(100)


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Append all technical indicator columns required by ATradeBot to *df*.

    Uses pandas_ta for every calculation.  Returns a **new** DataFrame;
    the original is not modified.

    Indicators added
    ----------------
    EMA(9), EMA(21), SMA(200)
    RSI(14)
    MACD(12, 26, 9)  — main line, signal, histogram
    Stochastic(k=5, d=3, smooth_k=3)
    ATR(14)
    Bollinger Bands(20, 2.0)  — upper, middle, lower
    ADX(14)  — ADX strength, +DI, -DI

    Note
    ----
    SMA(200) requires ≥ 200 rows.  Passing fewer rows produces NaN values for
    that column only; all other indicators compute correctly from their own
    minimum look-back.

    Parameters
    ----------
    df : DataFrame with Open, High, Low, Close, Volume columns.

    Returns
    -------
    pd.DataFrame — original OHLCV columns plus all indicator columns.
    """
    out = df.copy()

    # Moving averages
    out.ta.ema(length=9,   append=True)
    out.ta.ema(length=21,  append=True)
    out.ta.sma(length=200, append=True)

    # Momentum
    out.ta.rsi(length=14, append=True)
    out.ta.macd(fast=12, slow=26, signal=9, append=True)
    out.ta.stoch(k=5, d=3, smooth_k=3, append=True)

    # Volatility
    out.ta.atr(length=14,          append=True)
    out.ta.bbands(length=20, std=2.0, append=True)

    # Trend strength
    out.ta.adx(length=14, append=True)

    return out


def update_shared_state(symbol: str, data: dict) -> None:
    """
    Merge *data* into ``shared_state["indicator_data"][symbol]`` and persist
    the file atomically.

    A threading lock prevents concurrent writes from corrupting the JSON.
    A write-then-rename pattern keeps the window during which the file is
    incomplete as short as possible.

    Parameters
    ----------
    symbol : e.g. ``"XAUUSD"``
    data   : Indicator payload.  Pass a flat dict for single-timeframe::

                 {"price": 2340.5, "ema9": 2341.2, "rsi14": 56.1, ...}

             Or a timeframe-keyed dict for multi-timeframe storage::

                 {"5m": {"price": ..., "ema9": ...}, "15m": {...}}
    """
    path = Path(SHARED_STATE_PATH)

    with _file_lock:
        state: dict = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                state = json.load(fh)

        state.setdefault("indicator_data", {})
        state["indicator_data"][symbol] = data

        # Write to a .tmp sibling then atomically rename to avoid readers
        # seeing a partially-written file.
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, default=str)
        tmp.replace(path)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _safe_float(value) -> Optional[float]:
    """Return a Python float rounded to 6 dp, or None for NaN / Inf / non-numeric."""
    try:
        f = float(value)
        if f != f or abs(f) == float("inf"):   # NaN test: NaN != NaN is True
            return None
        return round(f, 6)
    except (TypeError, ValueError):
        return None


def _col(df: pd.DataFrame, prefix: str) -> Optional[str]:
    """Return the first column name that starts with *prefix*, or None."""
    return next((c for c in df.columns if c.startswith(prefix)), None)


def _get(row: pd.Series, col: Optional[str]) -> Optional[float]:
    """Safely extract and convert row[col] to float, returning None if absent."""
    return _safe_float(row[col]) if col is not None else None


def _extract_latest(df: pd.DataFrame, symbol: str, timeframe: str) -> dict:
    """
    Pull the most-recent row of an indicator DataFrame into a plain dict
    suitable for JSON serialisation.

    Column names are resolved by prefix so the function tolerates
    pandas_ta version differences (e.g. ``ATRr_14`` vs ``ATR_14``).
    """
    row = df.iloc[-1]

    # ATR column name varies across pandas_ta releases
    atr_col  = _col(df, "ATRr_") or _col(df, "ATR_")

    return {
        "symbol":      symbol,
        "timeframe":   timeframe,
        "price":       _safe_float(row.get("Close")),
        "ema9":        _safe_float(row.get("EMA_9")),
        "ema21":       _safe_float(row.get("EMA_21")),
        "sma200":      _safe_float(row.get("SMA_200")),
        "rsi14":       _safe_float(row.get("RSI_14")),
        "macd":        _get(row, _col(df, "MACD_")),
        "macd_signal": _get(row, _col(df, "MACDs_")),
        "macd_hist":   _get(row, _col(df, "MACDh_")),
        "atr14":       _get(row, atr_col),
        "bb_upper":    _get(row, _col(df, "BBU_")),
        "bb_middle":   _get(row, _col(df, "BBM_")),
        "bb_lower":    _get(row, _col(df, "BBL_")),
        "stoch_k":     _get(row, _col(df, "STOCHk_")),
        "stoch_d":     _get(row, _col(df, "STOCHd_")),
        "adx":         _get(row, _col(df, "ADX_")),
        "di_plus":     _get(row, _col(df, "DMP_")),
        "di_minus":    _get(row, _col(df, "DMN_")),
        "updated_at":  datetime.now(timezone.utc).isoformat(),
    }


def _set_agent_status(running: bool, error: str = "") -> None:
    """Write data_agent heartbeat and status into shared_state.json."""
    path = Path(SHARED_STATE_PATH)
    with _file_lock:
        state: dict = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                state = json.load(fh)

        state.setdefault("agent_statuses", {}).setdefault("data_agent", {})
        state["agent_statuses"]["data_agent"].update({
            "running":        running,
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "error":          error,
        })

        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, default=str)
        tmp.replace(path)


# ---------------------------------------------------------------------------
# DataAgent class
# ---------------------------------------------------------------------------

class DataAgent:
    """
    Polling agent that keeps shared_state.json fresh with the latest OHLCV
    and indicator values for every configured symbol.

    Usage::

        agent = DataAgent()
        agent.run()        # blocks; use Ctrl-C to stop
    """

    def __init__(self) -> None:
        # Cache of the last successfully computed indicator DataFrame per
        # (symbol, timeframe).  Used as a fallback when yfinance is down.
        self._cache: dict[tuple[str, str], pd.DataFrame] = {}
        self._configure_logger()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    @staticmethod
    def _configure_logger() -> None:
        """Add a rotating file handler to the module logger."""
        log_path = Path(LOG_FILE)
        os.makedirs(log_path.parent, exist_ok=True)
        logger.add(
            str(log_path),
            rotation=LOG_ROTATION,
            level=LOG_LEVEL,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {message}",
            enqueue=True,   # thread-safe writes
        )

    # ------------------------------------------------------------------
    # Internal fetch (uses full history for accurate SMA200 warm-up)
    # ------------------------------------------------------------------

    def _fetch_with_indicators(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """
        Download the full configured history for *symbol* / *timeframe*,
        compute all indicators, and return the complete DataFrame.

        Unlike ``fetch_price_data()``, this method is **not** trimmed to 100
        rows so that SMA(200) has enough warm-up data before the last row is
        extracted.
        """
        ticker = YFINANCE_MAP.get(symbol, symbol)
        period = FETCH_PERIOD.get(timeframe, "30d")

        raw = yf.download(
            ticker,
            period=period,
            interval=timeframe,
            auto_adjust=True,
            progress=False,
        )

        if raw is None or raw.empty:
            raise ValueError(
                f"yfinance returned no data for {symbol} "
                f"(ticker={ticker}, interval={timeframe}, period={period})"
            )

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index.name = "datetime"

        return calculate_indicators(df)

    # ------------------------------------------------------------------
    # Per-symbol processing
    # ------------------------------------------------------------------

    def _process_symbol(self, symbol: str, timeframe: str) -> Optional[dict]:
        """
        Fetch, compute, and return the latest indicator snapshot for one
        symbol/timeframe combination.

        On any yfinance or computation failure:
          - logs the error with full traceback
          - returns the last cached snapshot if available, or None
        """
        try:
            df = self._fetch_with_indicators(symbol, timeframe)
            self._cache[(symbol, timeframe)] = df
            latest = _extract_latest(df, symbol, timeframe)
            logger.info(
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
                f"{symbol:6s} {timeframe} | "
                f"price={latest['price']}  "
                f"ema9={latest['ema9']}  "
                f"rsi14={latest['rsi14']}  "
                f"atr14={latest['atr14']}"
            )
            return latest

        except Exception as exc:
            cached = self._cache.get((symbol, timeframe))
            if cached is not None:
                stale = _extract_latest(cached, symbol, timeframe)
                stale["updated_at"] = datetime.now(timezone.utc).isoformat()
                logger.error(
                    f"{symbol} {timeframe} fetch failed — using cached values. "
                    f"Error: {exc}"
                )
                return stale
            else:
                logger.error(
                    f"{symbol} {timeframe} fetch failed — no cached values available. "
                    f"Error: {exc}"
                )
                return None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run_loop(self) -> None:
        """
        Poll all symbols and timeframes every ``POLL_INTERVAL`` seconds.

        Each cycle:
        1. Fetches fresh OHLCV data from yfinance for every (symbol, timeframe).
        2. Computes all technical indicators.
        3. Writes latest values to shared_state.json under ``indicator_data``.
        4. Logs a one-line summary per symbol/timeframe with timestamp.

        Timing is self-correcting: the sleep at the end of each cycle is
        reduced by however long the fetches took, so the *start* of each
        cycle stays close to the configured interval.
        """
        logger.info(
            f"DataAgent starting — symbols={SYMBOLS} "
            f"timeframes={TIMEFRAMES} interval={POLL_INTERVAL}s"
        )
        _set_agent_status(running=True, error="")

        while True:
            cycle_start = time.monotonic()

            for symbol in SYMBOLS:
                tf_results: dict[str, dict] = {}

                for timeframe in TIMEFRAMES:
                    result = self._process_symbol(symbol, timeframe)
                    if result is not None:
                        tf_results[timeframe] = result

                if tf_results:
                    update_shared_state(symbol, tf_results)

            # Keep the heartbeat fresh every cycle
            _set_agent_status(running=True, error="")

            elapsed   = time.monotonic() - cycle_start
            sleep_for = max(0.0, POLL_INTERVAL - elapsed)
            logger.debug(
                f"Cycle complete in {elapsed:.1f}s — "
                f"sleeping {sleep_for:.0f}s until next poll"
            )
            time.sleep(sleep_for)

    def run(self) -> None:
        """Start the agent.  Blocks until interrupted or an unrecoverable error."""
        try:
            self.run_loop()
        except KeyboardInterrupt:
            logger.info("DataAgent stopped by user (KeyboardInterrupt).")
            _set_agent_status(running=False, error="Stopped by user")
        except Exception as exc:
            logger.exception(f"DataAgent crashed: {exc}")
            _set_agent_status(running=False, error=str(exc))
            raise


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent = DataAgent()
    agent.run()
