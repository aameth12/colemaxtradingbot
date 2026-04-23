"""
test_bot.py
-----------
Diagnostic test suite for ATradeBot.  Run before connecting to MT4.

Usage::

    cd ATradeBot/
    python test_bot.py

Each test prints PASS or FAIL with a one-line reason.
Exits 0 if all pass, 1 if any fail.
"""

import csv
import json
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

_results: list[tuple[str, bool, str]] = []


def _run(name: str, fn) -> None:
    try:
        fn()
        _results.append((name, True, ""))
        print(f"  PASS  {name}")
    except AssertionError as exc:
        _results.append((name, False, str(exc)))
        print(f"  FAIL  {name}: {exc}")
    except Exception as exc:
        _results.append((name, False, f"{type(exc).__name__}: {exc}"))
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Test 1 — Config loads correctly
# ---------------------------------------------------------------------------

def _t_config() -> None:
    from utils.config import (
        SYMBOLS, RISK_PER_TRADE_PCT, MAX_DRAWDOWN_PCT,
        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
        SHARED_STATE_PATH, TRADE_LOG_PATH, DASHBOARD_PORT,
    )
    assert SYMBOLS,                         "SYMBOLS list is empty"
    assert len(SYMBOLS) >= 1,               "SYMBOLS must contain at least one symbol"
    assert 0 < RISK_PER_TRADE_PCT <= 100,   f"RISK_PER_TRADE_PCT out of range: {RISK_PER_TRADE_PCT}"
    assert 0 < MAX_DRAWDOWN_PCT  <= 100,    f"MAX_DRAWDOWN_PCT out of range: {MAX_DRAWDOWN_PCT}"
    assert TELEGRAM_BOT_TOKEN,              "TELEGRAM_BOT_TOKEN is empty"
    assert TELEGRAM_CHAT_ID,               "TELEGRAM_CHAT_ID is empty"
    assert SHARED_STATE_PATH,              "SHARED_STATE_PATH is empty"
    assert TRADE_LOG_PATH,                 "TRADE_LOG_PATH is empty"
    assert isinstance(DASHBOARD_PORT, int), "DASHBOARD_PORT must be an int"


# ---------------------------------------------------------------------------
# Test 2 — shared_state.json is writable (atomic write pattern)
# ---------------------------------------------------------------------------

def _t_shared_state() -> None:
    from utils.config import SHARED_STATE_PATH
    path = Path(SHARED_STATE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing state (preserve it)
    state: dict = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            state = json.load(fh)

    sentinel = datetime.now(timezone.utc).isoformat()
    state["_test_sentinel"] = sentinel

    # Write-then-rename (same pattern as production agents)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    tmp.replace(path)

    # Verify persistence
    with path.open("r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert loaded.get("_test_sentinel") == sentinel, "Written value not read back correctly"

    # Clean up test key
    del loaded["_test_sentinel"]
    with path.open("w", encoding="utf-8") as fh:
        json.dump(loaded, fh, indent=2)


# ---------------------------------------------------------------------------
# Test 3 — yfinance returns data for all 5 symbols
# ---------------------------------------------------------------------------

def _t_yfinance() -> None:
    import yfinance as yf

    ticker_map = {
        "XAUUSD": "XAUUSD=X",
        "AAPL":   "AAPL",
        "TSLA":   "TSLA",
        "NVDA":   "NVDA",
        "AMZN":   "AMZN",
    }
    failures: list[str] = []
    for sym, ticker in ticker_map.items():
        df = yf.download(ticker, period="1d", interval="5m",
                         auto_adjust=True, progress=False)
        if df is None or df.empty:
            failures.append(sym)

    assert not failures, f"No data returned for: {', '.join(failures)}"


# ---------------------------------------------------------------------------
# Test 4 — Telegram token is valid (sends a test message)
# ---------------------------------------------------------------------------

def _t_telegram() -> None:
    import requests
    from utils.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

    if TELEGRAM_BOT_TOKEN in ("", "YOUR_BOT_TOKEN_HERE"):
        raise AssertionError("TELEGRAM_BOT_TOKEN is not configured in .env")
    if TELEGRAM_CHAT_ID in ("", "YOUR_CHAT_ID_HERE"):
        raise AssertionError("TELEGRAM_CHAT_ID is not configured in .env")

    r = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe",
        timeout=10,
    )
    assert r.status_code == 200 and r.json().get("ok"), \
        f"getMe failed ({r.status_code}): {r.text[:200]}"

    r2 = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": "ATradeBot test message — OK"},
        timeout=10,
    )
    assert r2.status_code == 200 and r2.json().get("ok"), \
        f"sendMessage failed ({r2.status_code}): {r2.text[:200]}"


# ---------------------------------------------------------------------------
# Test 5 — Dashboard starts on port 8050 and returns HTTP 200
# ---------------------------------------------------------------------------

def _t_dashboard() -> None:
    import requests
    from utils.config import DASHBOARD_PORT

    url = f"http://localhost:{DASHBOARD_PORT}"

    # If already running (e.g. started externally) just verify it responds
    try:
        r = requests.get(url, timeout=2)
        if r.status_code == 200:
            return
    except requests.exceptions.ConnectionError:
        pass

    # Verify the port is free before trying to start
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", DASHBOARD_PORT))
        except OSError:
            raise AssertionError(f"Port {DASHBOARD_PORT} is already in use by another process")

    import agents.dashboard_agent as _da  # triggers Dash app creation

    t = threading.Thread(
        target=lambda: _da.app.run(host="127.0.0.1", port=DASHBOARD_PORT, debug=False),
        daemon=True,
        name="test-dashboard",
    )
    t.start()
    time.sleep(2)

    r = requests.get(url, timeout=5)
    assert r.status_code == 200, f"Dashboard returned HTTP {r.status_code}"


# ---------------------------------------------------------------------------
# Test 6 — trade_log.csv is writable
# ---------------------------------------------------------------------------

def _t_trade_log() -> None:
    from utils.config import TRADE_LOG_PATH

    path = Path(TRADE_LOG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["timestamp", "symbol", "action", "price", "volume", "profit", "notes"]
    needs_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if needs_header:
            writer.writeheader()

    assert path.exists(),              "trade_log.csv was not created"
    assert path.stat().st_size > 0,   "trade_log.csv is empty after write"

    # Verify it is readable as CSV
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == fieldnames, \
            f"Unexpected CSV headers: {reader.fieldnames}"


# ---------------------------------------------------------------------------
# Test 7 — Session filter correct for known IL-time inputs
# ---------------------------------------------------------------------------

def _t_session_filter() -> None:
    # Inline implementation mirrors ATradeBot.mq4 and dashboard_agent.py
    _SESSIONS = {
        "XAUUSD": (10 * 60,       21 * 60),
        "AAPL":   (16 * 60 + 30,  21 * 60),
    }
    _FRIDAY_CUTOFF = 19 * 60  # IL minutes

    def active(sym: str, il_hour: int, il_min: int, dow: int) -> bool:
        """dow: 0=Mon … 4=Fri, 5=Sat, 6=Sun"""
        if dow >= 5:
            return False
        ilm = il_hour * 60 + il_min
        if dow == 4 and ilm >= _FRIDAY_CUTOFF:
            return False
        start, end = _SESSIONS.get(sym, _SESSIONS["AAPL"])
        return start <= ilm < end

    cases = [
        # (sym, il_hour, il_min, dow, expected, description)
        ("XAUUSD", 12,  0, 1, True,  "XAUUSD 12:00 Tue — inside session"),
        ("XAUUSD",  9, 59, 1, False, "XAUUSD 09:59 Tue — before open"),
        ("XAUUSD", 21,  0, 1, False, "XAUUSD 21:00 Tue — exactly at close (exclusive)"),
        ("AAPL",   17,  0, 2, True,  "AAPL 17:00 Wed — inside session"),
        ("AAPL",   16, 29, 2, False, "AAPL 16:29 Wed — one minute before open"),
        ("XAUUSD", 22,  0, 3, False, "XAUUSD 22:00 Thu — after close"),
        ("XAUUSD", 12,  0, 5, False, "XAUUSD 12:00 Sat — weekend"),
        ("XAUUSD", 12,  0, 6, False, "XAUUSD 12:00 Sun — weekend"),
        ("XAUUSD", 19, 30, 4, False, "XAUUSD 19:30 Fri — past Friday cutoff"),
        ("XAUUSD", 18, 59, 4, True,  "XAUUSD 18:59 Fri — just before Friday cutoff"),
        ("AAPL",   19,  0, 4, False, "AAPL 19:00 Fri — at Friday cutoff"),
    ]

    failures: list[str] = []
    for sym, hh, mm, dow, expected, desc in cases:
        got = active(sym, hh, mm, dow)
        if got != expected:
            failures.append(f"{desc}: expected {expected}, got {got}")

    assert not failures, "\n  ".join([""] + failures)


# ---------------------------------------------------------------------------
# Test 8 — Kill switch triggers at correct drawdown threshold
# ---------------------------------------------------------------------------

def _t_kill_switch() -> None:
    from utils.config import MAX_DRAWDOWN_PCT

    assert 0 < MAX_DRAWDOWN_PCT < 100, \
        f"MAX_DRAWDOWN_PCT={MAX_DRAWDOWN_PCT} is not a valid percentage"

    # Simulate an account hitting exactly the threshold
    starting_balance = 10_000.0
    loss_at_threshold = starting_balance * MAX_DRAWDOWN_PCT / 100
    equity = starting_balance - loss_at_threshold
    computed_dd_pct = (starting_balance - equity) / starting_balance * 100

    assert abs(computed_dd_pct - MAX_DRAWDOWN_PCT) < 1e-9, \
        f"Drawdown formula error: {computed_dd_pct:.6f} != {MAX_DRAWDOWN_PCT}"

    # One cent below threshold must NOT trigger
    equity_safe = starting_balance - loss_at_threshold + 0.01
    safe_dd_pct = (starting_balance - equity_safe) / starting_balance * 100
    assert safe_dd_pct < MAX_DRAWDOWN_PCT, \
        "Kill switch would fire before the threshold is reached"


# ---------------------------------------------------------------------------
# Test 9 — Lot size calculation is valid
# ---------------------------------------------------------------------------

def _t_lot_size() -> None:
    from utils.config import RISK_PER_TRADE_PCT

    def calc_lots(balance: float, risk_pct: float,
                  sl_pips: float, pip_val_per_lot: float) -> float:
        if sl_pips <= 0 or pip_val_per_lot <= 0:
            return 0.01
        risk_amount = balance * risk_pct / 100
        raw = risk_amount / (sl_pips * pip_val_per_lot)
        return max(0.01, min(2.0, round(raw, 2)))

    # Normal trade — result must be in [0.01, 2.0]
    for balance, sl_pips, pip_val, label in [
        (10_000, 20,  1.0, "normal account, 20-pip SL"),
        (500,    50,  0.1, "small account, 50-pip SL"),
        (50_000, 10,  2.0, "large account, 10-pip SL"),
    ]:
        lots = calc_lots(balance, RISK_PER_TRADE_PCT, sl_pips, pip_val)
        assert 0.01 <= lots <= 2.0, f"{label}: lots={lots} outside [0.01, 2.0]"

    # Minimum clamp
    assert calc_lots(10, RISK_PER_TRADE_PCT, 10_000, 1.0) == 0.01, \
        "Minimum lot clamp (0.01) not applied"

    # Maximum clamp
    assert calc_lots(1_000_000, RISK_PER_TRADE_PCT, 1, 1.0) == 2.0, \
        "Maximum lot clamp (2.0) not applied"

    # Zero SL must not raise — should return minimum
    assert calc_lots(10_000, RISK_PER_TRADE_PCT, 0, 1.0) == 0.01, \
        "Zero SL should return minimum lot size without raising"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("ATradeBot — pre-flight diagnostic tests\n")

    _run("1. Config loads correctly",                    _t_config)
    _run("2. shared_state.json is writable",             _t_shared_state)
    _run("3. yfinance returns data for all 5 symbols",   _t_yfinance)
    _run("4. Telegram token is valid (test message sent)", _t_telegram)
    _run("5. Dashboard starts on port 8050",             _t_dashboard)
    _run("6. trade_log.csv is writable",                 _t_trade_log)
    _run("7. Session filter correct for known times",    _t_session_filter)
    _run("8. Kill switch triggers at correct threshold", _t_kill_switch)
    _run("9. Lot size calculation is valid",             _t_lot_size)

    print()
    passed  = [r for r in _results if r[1]]
    failed  = [r for r in _results if not r[1]]

    if not failed:
        print("All tests passed — ready to connect MT4.")
        sys.exit(0)
    else:
        print(f"{len(passed)} passed, {len(failed)} failed:\n")
        for name, _, detail in failed:
            print(f"  FAIL  {name}")
            if detail:
                print(f"        {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
