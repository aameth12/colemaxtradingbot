"""
weekly_learning.py
------------------
Weekly self-improvement loop — runs every Saturday at 09:00 local time.

Actions
-------
1. Load and analyse trade_log.csv (win rate per symbol, win rate per hour,
   average achieved R:R).
2. Apply rules to utils/config.py:
     - Symbol win rate < 40% for 2 consecutive weeks → remove from SYMBOLS.
     - Overall win rate > 60% → increase RISK_PER_TRADE_PCT by 10%
       (capped at baseline × 1.20).
3. Persist this week's stats to data/weekly_stats.json for trend tracking.
4. Send a Telegram summary via the Bot API (plain HTTP, no async).

Usage (standalone)::

    cd ATradeBot/
    python weekly_learning.py        # blocks; runs scheduler

Imported by run_bot.py which calls run_analysis() directly via schedule.
"""

import csv
import json
import re
import schedule
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from utils.config import (
    BASE_DIR,
    RISK_PER_TRADE_PCT,
    SYMBOLS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_ENABLED,
    TRADE_LOG_PATH,
)
from utils.logger import logger


# ---------------------------------------------------------------------------
# Paths and thresholds
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(BASE_DIR) / "utils" / "config.py"
_STATS_PATH  = Path(BASE_DIR) / "data" / "weekly_stats.json"

_RISK_BASELINE   = RISK_PER_TRADE_PCT   # snapshot at import time
_LOW_WIN_RATE    = 0.40                 # disable symbol below this for 2 weeks
_HIGH_WIN_RATE   = 0.60                 # increase risk above this
_RISK_STEP       = 0.10                 # multiply risk by (1 + _RISK_STEP)
_MAX_RISK_UPLIFT = 0.20                 # never exceed baseline × (1 + this)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _load_trade_log() -> list[dict]:
    path = Path(TRADE_LOG_PATH)
    if not path.exists():
        return []
    try:
        with path.open("r", newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _sf(v, default: float = 0.0) -> float:
    try:
        return float(v or 0)
    except (ValueError, TypeError):
        return default


def _load_weekly_stats() -> dict:
    if not _STATS_PATH.exists():
        return {"baseline_risk": _RISK_BASELINE, "weeks": []}
    try:
        with _STATS_PATH.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {"baseline_risk": _RISK_BASELINE, "weeks": []}


def _save_weekly_stats(stats: dict) -> None:
    _STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATS_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=2)
    tmp.replace(_STATS_PATH)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze(rows: list[dict]) -> dict:
    """
    Return a summary dict from a list of trade_log rows.

    Keys
    ----
    total_trades        : int
    win_rate_per_symbol : {symbol: float 0-1}
    win_rate_per_hour   : {hour_str: float 0-1}
    avg_rr              : float  (avg_win_$ / avg_loss_$ — proxy for R:R)
    overall_win_rate    : float 0-1
    """
    if not rows:
        return {
            "total_trades":        0,
            "win_rate_per_symbol": {},
            "win_rate_per_hour":   {},
            "avg_rr":              0.0,
            "overall_win_rate":    0.0,
        }

    # Per-symbol tallies
    sym_wins:   dict[str, int] = {}
    sym_totals: dict[str, int] = {}
    hour_wins:   dict[int, int] = {}
    hour_totals: dict[int, int] = {}
    win_profits:  list[float] = []
    loss_profits: list[float] = []

    for r in rows:
        profit = _sf(r.get("profit"))
        sym    = r.get("symbol", "")
        ts_str = str(r.get("timestamp", ""))

        # Symbol stats
        sym_totals[sym] = sym_totals.get(sym, 0) + 1
        if profit > 0:
            sym_wins[sym] = sym_wins.get(sym, 0) + 1
            win_profits.append(profit)
        elif profit < 0:
            loss_profits.append(abs(profit))

        # Hour stats (UTC)
        try:
            ts   = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            hour = ts.hour
            hour_totals[hour] = hour_totals.get(hour, 0) + 1
            if profit > 0:
                hour_wins[hour] = hour_wins.get(hour, 0) + 1
        except (ValueError, TypeError):
            pass

    win_rate_per_symbol = {
        s: sym_wins.get(s, 0) / sym_totals[s]
        for s in sym_totals
    }
    win_rate_per_hour = {
        str(h): hour_wins.get(h, 0) / hour_totals[h]
        for h in hour_totals
    }

    total  = sum(sym_totals.values())
    wins   = sum(sym_wins.values())
    avg_win  = sum(win_profits) / len(win_profits)   if win_profits  else 0.0
    avg_loss = sum(loss_profits) / len(loss_profits) if loss_profits else 0.0
    avg_rr   = avg_win / avg_loss if avg_loss > 0 else 0.0

    return {
        "total_trades":        total,
        "win_rate_per_symbol": win_rate_per_symbol,
        "win_rate_per_hour":   win_rate_per_hour,
        "avg_rr":              round(avg_rr, 2),
        "overall_win_rate":    round(wins / total, 4) if total else 0.0,
    }


# ---------------------------------------------------------------------------
# Config patching (regex-based, single-line assignments only)
# ---------------------------------------------------------------------------

def _patch_config(key: str, new_value: str) -> None:
    """
    Replace the RHS of ``key = <value>`` in config.py using a regex, leaving
    any trailing comment intact.
    """
    content = _CONFIG_PATH.read_text(encoding="utf-8")

    # Match: KEY = <old_value>   # optional comment
    pattern     = rf'^({re.escape(key)}\s*=\s*)([^\n#]+)'
    replacement = rf'\g<1>{new_value}'

    new_content, n = re.subn(pattern, replacement, content, flags=re.MULTILINE)
    if n == 0:
        logger.warning(f"weekly_learning: could not find '{key}' in config.py — skipping patch")
        return

    _CONFIG_PATH.write_text(new_content, encoding="utf-8")
    logger.info(f"weekly_learning: patched config.py  {key} = {new_value}")


def _patch_symbols(new_symbols: list[str]) -> None:
    symbols_str = '["' + '", "'.join(new_symbols) + '"]'
    _patch_config("SYMBOLS", symbols_str)


def _patch_risk(new_risk: float) -> None:
    _patch_config("RISK_PER_TRADE_PCT", f"{new_risk:.2f}       ")


# ---------------------------------------------------------------------------
# Telegram (plain HTTP — no async required)
# ---------------------------------------------------------------------------

def _send_telegram(text: str) -> None:
    if not TELEGRAM_ENABLED:
        return
    if TELEGRAM_BOT_TOKEN in ("YOUR_BOT_TOKEN_HERE", ""):
        logger.warning("weekly_learning: TELEGRAM_BOT_TOKEN not set — skipping alert")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        logger.error(f"weekly_learning: Telegram send failed: {exc}")


# ---------------------------------------------------------------------------
# Main weekly job
# ---------------------------------------------------------------------------

def run_analysis() -> None:
    """
    Entry point called by the scheduler every Saturday 09:00.
    Analyse → adjust config → persist stats → send Telegram summary.
    """
    now        = datetime.now(timezone.utc)
    week_label = now.strftime("%G-W%V")   # ISO week, e.g. "2025-W17"
    logger.info(f"weekly_learning: starting analysis for {week_label}")

    rows    = _load_trade_log()
    stats   = _load_weekly_stats()
    result  = analyze(rows)
    weeks   = stats.get("weeks", [])
    baseline = float(stats.get("baseline_risk", _RISK_BASELINE))

    # Append this week
    weeks.append({"week": week_label, **result})
    # Keep only the last 52 weeks
    weeks = weeks[-52:]
    stats["weeks"] = weeks
    _save_weekly_stats(stats)

    # ── Rule 1: disable persistently underperforming symbols ──────────────
    # Get the last 2 week entries that have per-symbol win rates
    recent = [w for w in weeks if w.get("win_rate_per_symbol")][-2:]
    current_symbols = list(SYMBOLS)   # may differ from config if patched before

    disabled: list[str] = []
    if len(recent) >= 2:
        for sym in list(current_symbols):
            rates = [w["win_rate_per_symbol"].get(sym) for w in recent]
            rates = [r for r in rates if r is not None]
            if len(rates) == 2 and all(r < _LOW_WIN_RATE for r in rates):
                current_symbols.remove(sym)
                disabled.append(sym)
                logger.warning(f"weekly_learning: disabling {sym} (win rates {rates})")

    if disabled:
        _patch_symbols(current_symbols)

    # ── Rule 2: increase risk when overall win rate is strong ─────────────
    overall = result["overall_win_rate"]
    risk_changed = False

    if overall > _HIGH_WIN_RATE and result["total_trades"] >= 5:
        # Read current risk from config (may have been patched previously)
        config_text   = _CONFIG_PATH.read_text(encoding="utf-8")
        m = re.search(r'^RISK_PER_TRADE_PCT\s*=\s*([\d.]+)', config_text, re.MULTILINE)
        current_risk  = float(m.group(1)) if m else baseline
        max_risk      = round(baseline * (1 + _MAX_RISK_UPLIFT), 4)
        new_risk      = round(min(current_risk * (1 + _RISK_STEP), max_risk), 4)

        if new_risk > current_risk:
            _patch_risk(new_risk)
            risk_changed = True
            logger.info(
                f"weekly_learning: risk increased {current_risk:.2f}% → {new_risk:.2f}% "
                f"(overall win rate {overall:.1%})"
            )

    # ── Telegram summary ──────────────────────────────────────────────────
    lines = [f"WEEKLY SUMMARY — {week_label}"]
    lines.append(f"Total trades: {result['total_trades']}")
    lines.append(f"Overall win rate: {overall:.1%}")
    lines.append(f"Avg R:R: {result['avg_rr']:.2f}")

    lines.append("\nWin rate by symbol:")
    for sym, wr in sorted(result["win_rate_per_symbol"].items()):
        flag = " [DISABLED]" if sym in disabled else ""
        lines.append(f"  {sym:<6} {wr:.1%}{flag}")

    best_hour = max(result["win_rate_per_hour"].items(),
                    key=lambda x: x[1], default=(None, 0))
    if best_hour[0] is not None:
        lines.append(f"\nBest trading hour (UTC): {best_hour[0]}:00 ({best_hour[1]:.1%})")

    if disabled:
        lines.append(f"\nDisabled (2 weeks <40%): {', '.join(disabled)}")
        lines.append("Restart bot to apply symbol change.")
    if risk_changed:
        lines.append(f"\nRisk increased to {new_risk:.2f}%.")
        lines.append("Restart bot to apply risk change.")

    msg = "\n".join(lines)
    logger.info(f"weekly_learning:\n{msg}")
    _send_telegram(msg)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("weekly_learning scheduler starting — fires every Saturday at 09:00")
    schedule.every().saturday.at("09:00").do(run_analysis)
    while True:
        schedule.run_pending()
        time.sleep(60)
