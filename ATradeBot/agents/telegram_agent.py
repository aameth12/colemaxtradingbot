"""
agents/telegram_agent.py
------------------------
Telegram Agent — sends trade alerts and responds to user commands.

Commands  : /status /today /alltime /assets /streak /session /pause /resume
Auto-alerts: trade opened/closed, kill switch triggered, news pause activated.

Data sources:
  - utils/shared_state.json  : live account, positions, signals, news state
  - data/trade_log.csv       : historical closed-trade records

Library: python-telegram-bot>=20.7 (async; requires [job-queue] extra).
Tokens : TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from utils/config.py.
"""

import csv
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from utils.config import (
    SHARED_STATE_PATH,
    SYMBOLS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_ENABLED,
    TRADE_LOG_PATH,
)
from utils.logger import logger


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MONITOR_INTERVAL = 5   # seconds between auto-alert checks
IL_UTC_OFFSET    = 3   # Israel Standard Time = UTC+3

# Session open/close times in IL minutes-from-midnight
_SESSION = {
    "XAUUSD": (10 * 60,       21 * 60),
    "AAPL":   (16 * 60 + 30,  21 * 60),
    "TSLA":   (16 * 60 + 30,  21 * 60),
    "NVDA":   (16 * 60 + 30,  21 * 60),
    "AMZN":   (16 * 60 + 30,  21 * 60),
}
_FRIDAY_CUTOFF_MIN = 19 * 60   # Friday: all sessions close at 19:00 IL

_file_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Shared-state I/O
# ---------------------------------------------------------------------------

def _read_state() -> dict:
    path = Path(SHARED_STATE_PATH)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state_key(key: str, value) -> None:
    path = Path(SHARED_STATE_PATH)
    with _file_lock:
        state: dict = {}
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    state = json.load(fh)
            except (json.JSONDecodeError, OSError):
                pass
        state[key] = value
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, default=str)
        tmp.replace(path)


def _set_agent_status(running: bool, error: str = "") -> None:
    path = Path(SHARED_STATE_PATH)
    with _file_lock:
        state: dict = {}
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    state = json.load(fh)
            except (json.JSONDecodeError, OSError):
                pass
        state.setdefault("agent_statuses", {}).setdefault("telegram_agent", {})
        state["agent_statuses"]["telegram_agent"].update({
            "running":        running,
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "error":          error,
        })
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, default=str)
        tmp.replace(path)


# ---------------------------------------------------------------------------
# Trade-log helpers
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


def _profit(row: dict) -> Optional[float]:
    try:
        return float(row.get("profit") or 0)
    except (ValueError, TypeError):
        return None


def _today_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _fmt_profit(p: float) -> str:
    return f"+${p:.2f}" if p >= 0 else f"-${abs(p):.2f}"


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _il_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=IL_UTC_OFFSET)


def _is_active(symbol: str) -> bool:
    il  = _il_now()
    dow = il.weekday()          # 0=Mon … 4=Fri, 5=Sat, 6=Sun
    if dow >= 5:
        return False
    ilm = il.hour * 60 + il.minute
    start, end = _SESSION.get(symbol, _SESSION["AAPL"])
    if dow == 4 and ilm >= _FRIDAY_CUTOFF_MIN:
        return False
    return start <= ilm < end


def _session_status(symbol: str) -> str:
    """Return a short human-readable string: 'active, closes in Xh Ym' or 'opens in …'."""
    il  = _il_now()
    dow = il.weekday()
    ilm = il.hour * 60 + il.minute
    start, end = _SESSION.get(symbol, _SESSION["AAPL"])
    eff_end = min(end, _FRIDAY_CUTOFF_MIN) if dow == 4 else end

    if _is_active(symbol):
        remaining = eff_end - ilm
        h, m = divmod(remaining, 60)
        return f"active, closes in {h}h {m}m"

    # Find minutes until next open, scanning up to 7 days ahead
    for delta in range(1, 8):
        target_dow = (dow + delta) % 7
        if target_dow >= 5:
            continue
        mins_until = delta * 1440 + start - ilm
        h, m = divmod(mins_until, 60)
        if h >= 48:
            d = h // 24
            h = h % 24
            return f"opens in {d}d {h}h {m}m"
        return f"opens in {h}h {m}m"
    return "closed"


# ---------------------------------------------------------------------------
# Authorization guard
# ---------------------------------------------------------------------------

def _authorized(update: Update) -> bool:
    try:
        return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    state     = _read_state()
    kill      = state.get("kill_switch", False)
    kill_why  = state.get("kill_switch_reason", "")
    acc       = state.get("account_info", {})
    perf      = state.get("performance", {})
    positions = state.get("current_positions", {})
    news      = state.get("news", {})
    statuses  = state.get("agent_statuses", {})
    m_pause   = state.get("manual_pause", False)

    agents_up = [
        name for name, info in statuses.items()
        if isinstance(info, dict) and info.get("running")
    ]

    open_summary = []
    for pos in positions.values():
        sym  = pos.get("symbol", "?")
        side = pos.get("type", pos.get("action", "?"))
        open_summary.append(f"{sym} {side}")

    pnl     = float(perf.get("pnl_today", 0) or 0)
    pnl_str = _fmt_profit(pnl)

    lines = [
        "STATUS",
        f"Kill switch: {'ON' + (f' ({kill_why})' if kill_why else '') if kill else 'OFF'}",
        f"Open trades: {len(positions)}" + (f" ({', '.join(open_summary)})" if open_summary else ""),
        f"Today P&L: {pnl_str}",
        f"Balance: ${float(acc.get('balance', 0) or 0):.2f}",
        f"Equity:  ${float(acc.get('equity', 0) or 0):.2f}",
        f"Drawdown: {float(acc.get('drawdown_pct', 0) or 0):.1f}%",
        f"Agents up: {', '.join(agents_up) if agents_up else 'none'}",
    ]
    if news.get("pause_trading"):
        lines.append(f"News pause: YES ({news.get('reason', '')})")
    if m_pause:
        lines.append("Manual pause: YES")

    await update.message.reply_text("\n".join(lines))


async def _cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    today = _today_prefix()
    rows  = [r for r in _load_trade_log() if str(r.get("timestamp", "")).startswith(today)]

    if not rows:
        await update.message.reply_text("Today's closed trades: none")
        return

    lines = [f"TODAY ({len(rows)} trades)"]
    for r in rows:
        p = _profit(r)
        if p is None:
            continue
        result = "Win" if p > 0 else ("Loss" if p < 0 else "BE")
        sym    = r.get("symbol", "?")
        action = r.get("action", "?")
        price  = r.get("price", "?")
        notes  = r.get("notes", "")
        entry  = ""
        if notes and "entry=" in notes:
            try:
                entry = "  entry:" + notes.split("entry=")[1].split()[0]
            except IndexError:
                pass
        lines.append(f"{sym} {action} @ {price}{entry}  {_fmt_profit(p)}  {result}")

    await update.message.reply_text("\n".join(lines))


async def _cmd_alltime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    profits = [p for p in (_profit(r) for r in _load_trade_log()) if p is not None]

    if not profits:
        await update.message.reply_text("All-time stats: no trades recorded")
        return

    total   = len(profits)
    wins    = sum(1 for p in profits if p > 0)
    losses  = sum(1 for p in profits if p < 0)
    rate    = wins / total * 100 if total else 0.0
    best    = max(profits)
    worst   = min(profits)
    total_p = sum(profits)

    lines = [
        "ALL-TIME STATS",
        f"Total trades: {total}",
        f"Wins: {wins}  Losses: {losses}  BE: {total - wins - losses}",
        f"Win rate: {rate:.1f}%",
        f"Total P&L: {_fmt_profit(total_p)}",
        f"Best trade:  +${best:.2f}",
        f"Worst trade: -${abs(worst):.2f}",
    ]
    await update.message.reply_text("\n".join(lines))


async def _cmd_assets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    state   = _read_state()
    signals = state.get("last_signals", {})
    news    = state.get("news", {})
    kill    = state.get("kill_switch", False)
    m_pause = state.get("manual_pause", False)
    today   = _today_prefix()

    today_by_sym: dict[str, int] = {}
    for r in _load_trade_log():
        if str(r.get("timestamp", "")).startswith(today):
            sym = r.get("symbol", "")
            today_by_sym[sym] = today_by_sym.get(sym, 0) + 1

    global_halt = kill or m_pause or news.get("pause_trading", False)

    lines = ["ASSETS"]
    for sym in SYMBOLS:
        tradeable = "NO" if global_halt else ("YES" if _is_active(sym) else "NO")
        sig    = signals.get(sym, {})
        action = (sig.get("action") or "none")
        conf   = float(sig.get("confidence", 0) or 0)
        sig_str = f"{action} ({conf:.0f}%)" if action and action != "none" else "none"
        count  = today_by_sym.get(sym, 0)
        lines.append(f"{sym:<6} tradeable:{tradeable}  signal:{sig_str}  today:{count}")

    await update.message.reply_text("\n".join(lines))


async def _cmd_streak(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    profits = [p for p in (_profit(r) for r in _load_trade_log()) if p is not None]

    if not profits:
        await update.message.reply_text("Streaks: no trades recorded")
        return

    outcomes = ["W" if p > 0 else "L" for p in profits]

    cur_type = outcomes[-1]
    cur_len  = 0
    for o in reversed(outcomes):
        if o == cur_type:
            cur_len += 1
        else:
            break
    cur_label = "win" if cur_type == "W" else "loss"

    # Longest win / loss streaks
    max_win = max_loss = run_w = run_l = 0
    for o in outcomes:
        if o == "W":
            run_w += 1; run_l = 0
        else:
            run_l += 1; run_w = 0
        if run_w > max_win:  max_win  = run_w
        if run_l > max_loss: max_loss = run_l

    lines = [
        "STREAKS",
        f"Current: {cur_len} {cur_label}{'s' if cur_len != 1 else ''}",
        f"Longest win streak:  {max_win}",
        f"Longest loss streak: {max_loss}",
    ]
    await update.message.reply_text("\n".join(lines))


async def _cmd_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    il      = _il_now()
    il_str  = il.strftime("%H:%M")
    day_str = il.strftime("%A")

    lines = [f"SESSIONS  (IL time {il_str}, {day_str})"]
    for sym in SYMBOLS:
        lines.append(f"{sym:<6} {_session_status(sym)}")

    await update.message.reply_text("\n".join(lines))


async def _cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    _write_state_key("manual_pause", True)
    logger.info("Telegram: manual pause enabled")
    await update.message.reply_text("Trading paused. Use /resume to re-enable.")


async def _cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    _write_state_key("manual_pause", False)
    logger.info("Telegram: manual pause cleared")
    await update.message.reply_text("Trading resumed.")


# ---------------------------------------------------------------------------
# Auto-alert monitor
# ---------------------------------------------------------------------------

async def _alert(bot: Bot, text: str) -> None:
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
    except Exception as exc:
        logger.error(f"Telegram alert failed: {exc}")


class _Monitor:
    """Detects state changes every MONITOR_INTERVAL seconds and fires alerts."""

    def __init__(self) -> None:
        state = _read_state()
        self._prev_kill     = bool(state.get("kill_switch", False))
        self._prev_news     = bool(state.get("news", {}).get("pause_trading", False))
        self._prev_pos_keys = set(state.get("current_positions", {}).keys())
        self._prev_positions: dict = dict(state.get("current_positions", {}))

    async def tick(self, bot: Bot) -> None:
        state     = _read_state()
        kill      = bool(state.get("kill_switch", False))
        news_d    = state.get("news", {})
        news_on   = bool(news_d.get("pause_trading", False))
        positions = state.get("current_positions", {})
        pos_keys  = set(positions.keys())

        # Kill switch
        if kill and not self._prev_kill:
            reason = state.get("kill_switch_reason", "daily loss limit hit")
            await _alert(bot, f"KILL SWITCH: {reason}. All trades closed.")
            logger.warning("Telegram: kill-switch alert sent")

        # News pause
        if news_on and not self._prev_news:
            reason = news_d.get("reason", "high-impact event")
            until  = news_d.get("until", "")
            until_str = f" until {until}" if until else ""
            await _alert(bot, f"NEWS PAUSE: {reason}{until_str}. Trading paused.")
            logger.info("Telegram: news-pause alert sent")

        # Trades opened
        for key in pos_keys - self._prev_pos_keys:
            pos   = positions[key]
            sym   = pos.get("symbol", "?")
            side  = pos.get("type", pos.get("action", "?"))
            entry = pos.get("open_price", pos.get("price", "?"))
            sl    = pos.get("stop_loss", pos.get("sl", "?"))
            tp    = pos.get("take_profit", pos.get("tp", "?"))
            msg   = f"{side} {sym} @ {entry} | SL: {sl} | TP: {tp}"
            await _alert(bot, msg)
            logger.info(f"Telegram: trade-opened: {msg}")

        # Trades closed
        for key in self._prev_pos_keys - pos_keys:
            pos  = self._prev_positions.get(key, {})
            sym  = pos.get("symbol", "?")
            side = pos.get("type", pos.get("action", "?"))

            # Try to find profit in the most-recent matching CSV row
            profit: Optional[float] = None
            pips_str = ""
            rows = _load_trade_log()
            for r in reversed(rows):
                if r.get("symbol") == sym:
                    profit = _profit(r)
                    notes  = r.get("notes", "")
                    if "pips=" in notes:
                        try:
                            pips_str = " | " + notes.split("pips=")[1].split()[0] + " pips"
                        except IndexError:
                            pass
                    break

            if profit is not None:
                result = "Win" if profit > 0 else ("Loss" if profit < 0 else "BE")
                msg = f"CLOSED {sym}{pips_str} | {_fmt_profit(profit)} | {result}"
            else:
                msg = f"CLOSED {sym} {side}"
            await _alert(bot, msg)
            logger.info(f"Telegram: trade-closed: {msg}")

        # Persist previous state
        self._prev_kill      = kill
        self._prev_news      = news_on
        self._prev_pos_keys  = pos_keys
        self._prev_positions = dict(positions)


# ---------------------------------------------------------------------------
# TelegramAgent
# ---------------------------------------------------------------------------

class TelegramAgent:
    """
    Telegram bot for ATradeBot.

    Registers all command handlers and a repeating monitor job, then starts
    the async polling loop.  Blocks until interrupted.

    Usage::

        agent = TelegramAgent()
        agent.run()
    """

    def __init__(self) -> None:
        self._monitor = _Monitor()

    def run(self) -> None:
        if not TELEGRAM_ENABLED:
            logger.info("TelegramAgent: TELEGRAM_ENABLED=False — skipping start")
            return

        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        app.add_handler(CommandHandler("status",  _cmd_status))
        app.add_handler(CommandHandler("today",   _cmd_today))
        app.add_handler(CommandHandler("alltime", _cmd_alltime))
        app.add_handler(CommandHandler("assets",  _cmd_assets))
        app.add_handler(CommandHandler("streak",  _cmd_streak))
        app.add_handler(CommandHandler("session", _cmd_session))
        app.add_handler(CommandHandler("pause",   _cmd_pause))
        app.add_handler(CommandHandler("resume",  _cmd_resume))

        monitor = self._monitor

        async def _monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
            await monitor.tick(context.bot)

        app.job_queue.run_repeating(_monitor_job, interval=MONITOR_INTERVAL, first=5)

        _set_agent_status(running=True, error="")
        logger.info("TelegramAgent starting — polling for commands and monitoring state")
        try:
            app.run_polling(drop_pending_updates=True)
        except Exception as exc:
            logger.exception(f"TelegramAgent crashed: {exc}")
            _set_agent_status(running=False, error=str(exc))
            raise
        finally:
            _set_agent_status(running=False, error="")
            logger.info("TelegramAgent stopped")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent = TelegramAgent()
    agent.run()
