"""
agents/news_agent.py
--------------------
News Agent — scrapes the ForexFactory economic calendar for high-impact USD
events and maintains a pause_trading flag in shared_state.json so that other
agents can avoid entering trades during volatile news windows.

Public API (importable by other agents):
    fetch_forex_factory_calendar()  -> list[NewsEvent]
    is_news_window(symbol)          -> bool
    update_news_state()             -> None

Timing
------
    Calendar refresh : every 30 minutes  (CALENDAR_REFRESH_SECS)
    State JSON write : every 60 seconds  (STATE_UPDATE_SECS)

Config hooks
------------
    NEWS_HIGH_IMPACT_HALT = False  →  all checks are skipped; pause_trading
                                       is always written as false.
"""

import json
import re
import time
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from utils.config import (
    NEWS_HIGH_IMPACT_HALT,
    SHARED_STATE_PATH,
    LOG_FILE,
    LOG_LEVEL,
    LOG_ROTATION,
)
from utils.logger import logger


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FF_URL = "https://www.forexfactory.com/calendar"

# Browser-like request headers to reduce Cloudflare block probability
FF_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":           "en-US,en;q=0.9",
    "Accept-Encoding":           "gzip, deflate, br",
    "Connection":                "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control":             "max-age=0",
}

# All ATradeBot instruments are USD-priced
SYMBOL_CURRENCY_MAP: dict[str, str] = {
    "XAUUSD": "USD",
    "AAPL":   "USD",
    "TSLA":   "USD",
    "NVDA":   "USD",
    "AMZN":   "USD",
}

# Minutes before AND after an event during which trading is paused
NEWS_WINDOW_MINUTES: int = 30

# How often to re-scrape the full calendar (seconds)
CALENDAR_REFRESH_SECS: int = 30 * 60   # 30 minutes

# How often to re-evaluate the window and write to JSON (seconds)
STATE_UPDATE_SECS: int = 60            # 1 minute

# ForexFactory publishes times in US Eastern Time
_ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Module-level state
# Shared between the public functions and NewsAgent so callers do not need
# to hold a reference to the agent instance.
# ---------------------------------------------------------------------------

_cached_events: list["NewsEvent"] = []
_scrape_ok:     bool              = True   # False after a failed fetch
_events_lock    = threading.Lock()
_file_lock      = threading.Lock()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class NewsEvent:
    """One economic calendar entry."""
    datetime_utc: datetime   # timezone-aware UTC
    currency:     str        # "USD", "EUR", …
    impact:       str        # "High" (only High events are stored)
    name:         str        # "Non-Farm Employment Change"

    def minutes_away(self, now: Optional[datetime] = None) -> float:
        """Return minutes until this event.  Positive = future, negative = past."""
        if now is None:
            now = datetime.now(timezone.utc)
        return (self.datetime_utc - now).total_seconds() / 60

    def __str__(self) -> str:
        return (
            f"{self.datetime_utc.strftime('%H:%M UTC')}  "
            f"{self.currency}  [{self.impact}]  {self.name}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_forex_factory_calendar() -> list[NewsEvent]:
    """
    Scrape the ForexFactory weekly calendar and return **High-impact events only**.

    Uses requests + BeautifulSoup with browser-like headers.  On any HTTP or
    parse failure the function logs a warning, clears the module-level event
    cache, and returns an empty list — causing ``is_news_window()`` to return
    ``False`` (do not block trading on stale / missing data).

    Returns
    -------
    list[NewsEvent]
        High-impact events for the current week, all currencies.
        Empty on failure.
    """
    global _cached_events, _scrape_ok

    # ── HTTP fetch ──────────────────────────────────────────────────────────
    try:
        session = requests.Session()
        session.headers.update(FF_HEADERS)
        resp = session.get(FF_URL, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning(f"ForexFactory HTTP fetch failed — pause_trading=false. Error: {exc}")
        with _events_lock:
            _scrape_ok     = False
            _cached_events = []
        return []

    # ── HTML parse ──────────────────────────────────────────────────────────
    try:
        today  = datetime.now(_ET).date()
        events = _parse_ff_html(resp.text, today)
    except Exception as exc:
        logger.warning(f"ForexFactory HTML parse failed — pause_trading=false. Error: {exc}")
        with _events_lock:
            _scrape_ok     = False
            _cached_events = []
        return []

    with _events_lock:
        _scrape_ok     = True
        _cached_events = events

    usd_count = sum(1 for e in events if e.currency == "USD")
    logger.info(
        f"ForexFactory refreshed — {len(events)} high-impact events total, "
        f"{usd_count} USD"
    )
    return events


def is_news_window(symbol: str) -> bool:
    """
    Return ``True`` when a high-impact event for *symbol*'s currency is
    within ``NEWS_WINDOW_MINUTES`` minutes (before or after the event time).

    A ``True`` result means the calling agent **should not open new trades**.

    Parameters
    ----------
    symbol : Internal name — "XAUUSD", "AAPL", "TSLA", "NVDA", "AMZN".
             All map to "USD" via ``SYMBOL_CURRENCY_MAP``.

    Returns ``False`` immediately when:

    * ``NEWS_HIGH_IMPACT_HALT`` is ``False`` in config, or
    * The last ForexFactory scrape failed (avoids blocking trading on
      missing data).
    """
    if not NEWS_HIGH_IMPACT_HALT:
        return False

    currency = SYMBOL_CURRENCY_MAP.get(symbol, "USD")
    now      = datetime.now(timezone.utc)

    with _events_lock:
        if not _scrape_ok:
            return False
        events = list(_cached_events)

    for event in events:
        if event.currency != currency:
            continue
        if -NEWS_WINDOW_MINUTES <= event.minutes_away(now) <= NEWS_WINDOW_MINUTES:
            return True

    return False


def update_news_state() -> None:
    """
    Evaluate the current news window for USD events and persist the result
    to ``shared_state["news"]``.

    Output written::

        {
          "news": {
            "pause_trading": true,
            "reason":        "Non-Farm Employment Change in 12 min",
            "until":         "14:30 UTC"
          }
        }

    ``pause_trading`` is always ``false`` when:

    * ``NEWS_HIGH_IMPACT_HALT`` is ``False`` in config, or
    * The last scrape failed.
    """
    pause  = False
    reason = ""
    until  = ""

    if NEWS_HIGH_IMPACT_HALT:
        now = datetime.now(timezone.utc)

        with _events_lock:
            ok     = _scrape_ok
            events = list(_cached_events)

        if ok:
            # Sort by absolute proximity — pick the event closest to right now
            for event in sorted(events, key=lambda e: abs(e.minutes_away(now))):
                if event.currency != "USD":
                    continue
                diff = event.minutes_away(now)
                if -NEWS_WINDOW_MINUTES <= diff <= NEWS_WINDOW_MINUTES:
                    pause    = True
                    until_dt = event.datetime_utc + timedelta(minutes=NEWS_WINDOW_MINUTES)
                    until    = until_dt.strftime("%H:%M UTC")
                    reason   = (
                        f"{event.name} in {int(diff)} min"
                        if diff >= 0
                        else f"{event.name} {int(-diff)} min ago"
                    )
                    break

    _write_shared_state({"news": {
        "pause_trading": pause,
        "reason":        reason,
        "until":         until,
    }})


# ---------------------------------------------------------------------------
# Private — HTML parsing
# ---------------------------------------------------------------------------

def _parse_ff_html(html: str, today: date) -> list[NewsEvent]:
    """
    Walk the ForexFactory calendar table and collect High-impact events.

    Handles:
    - Date separator rows (``calendar__row--dateline``) — updates current date
    - Event rows with no time cell — inherit the previous row's time
    - "Tentative" and "All Day" entries — skipped
    - High-impact filter applied early (before heavier event-name extraction)
    """
    soup  = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_=re.compile(r"calendar__table"))

    if table is None:
        raise ValueError(
            "calendar__table not found — ForexFactory may have changed its "
            "HTML structure or the request was blocked (403/Cloudflare)"
        )

    events:        list[NewsEvent] = []
    current_date:  date            = today
    last_time_str: str             = ""

    for row in table.find_all("tr"):
        row_classes = " ".join(row.get("class", []))

        # ── Date separator ──────────────────────────────────────────────────
        if "dateline" in row_classes:
            date_cell = row.find("td", class_=re.compile(r"calendar__date"))
            if date_cell:
                parsed = _parse_ff_date(date_cell.get_text(" ", strip=True), today.year)
                if parsed is not None:
                    current_date = parsed
            continue

        if "calendar__row" not in row_classes:
            continue

        # ── Time ────────────────────────────────────────────────────────────
        time_cell = row.find("td", class_=re.compile(r"calendar__time"))
        time_text = (time_cell.get_text(strip=True) if time_cell else "").strip()

        if time_text and time_text not in ("Tentative", "All Day"):
            last_time_str = time_text
        elif not time_text:
            time_text = last_time_str   # inherit from the previous event row

        if not time_text or time_text in ("Tentative", "All Day"):
            continue

        # ── Impact — early filter ────────────────────────────────────────────
        impact_cell = row.find("td", class_=re.compile(r"calendar__impact"))
        if _parse_impact(impact_cell) != "High":
            continue

        # ── Currency ────────────────────────────────────────────────────────
        currency_cell = row.find("td", class_=re.compile(r"calendar__currency"))
        currency      = (currency_cell.get_text(strip=True) if currency_cell else "").strip()

        # ── Event name ──────────────────────────────────────────────────────
        event_cell = row.find("td", class_=re.compile(r"calendar__event"))
        name = ""
        if event_cell:
            title_node = event_cell.find(class_=re.compile(r"calendar__event-title"))
            name       = (title_node or event_cell).get_text(strip=True)

        # ── Datetime → UTC ───────────────────────────────────────────────────
        event_dt = _parse_ff_time(current_date, time_text)
        if event_dt is None:
            continue

        events.append(NewsEvent(
            datetime_utc = event_dt.astimezone(timezone.utc),
            currency     = currency,
            impact        = "High",
            name          = name,
        ))

    return events


def _parse_ff_date(text: str, year: int) -> Optional[date]:
    """
    Parse a ForexFactory date string into a ``date`` object.

    Accepted forms: ``"Wed Jan 15"``, ``"Jan 15"``, ``"Today"`` (→ ``None``).
    Handles the December → January year boundary (events > 7 days in the past
    are assumed to belong to the next calendar year).

    Returns ``None`` if parsing fails or the input is "Today" (caller keeps
    the existing ``current_date``).
    """
    text = text.strip()
    if not text or text.lower() == "today":
        return None

    # Strip optional leading weekday abbreviation: "Wed Jan 15" → ["Jan", "15"]
    parts     = text.split()
    month_day = " ".join(parts[-2:]) if len(parts) >= 2 else text

    for fmt in ("%b %d", "%B %d"):
        try:
            parsed    = datetime.strptime(month_day, fmt)
            candidate = date(year, parsed.month, parsed.day)
            # Handle Dec→Jan wrap: if the date is more than a week in the past
            # it almost certainly belongs to the coming year.
            if (datetime.now().date() - candidate).days > 7:
                candidate = date(year + 1, parsed.month, parsed.day)
            return candidate
        except ValueError:
            continue

    return None


def _parse_ff_time(event_date: date, time_str: str) -> Optional[datetime]:
    """
    Combine a date and a ForexFactory time string (``"8:30am"``) into a
    timezone-aware datetime expressed in US Eastern Time.

    Returns ``None`` when the string cannot be matched.
    """
    m = re.match(r"^(\d{1,2}):(\d{2})\s*(am|pm)$", time_str.strip().lower())
    if not m:
        return None

    hour, minute, meridiem = int(m.group(1)), int(m.group(2)), m.group(3)
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    try:
        return datetime(
            event_date.year, event_date.month, event_date.day,
            hour, minute,
            tzinfo=_ET,   # ZoneInfo handles DST automatically
        )
    except ValueError:
        return None


def _parse_impact(cell) -> str:
    """
    Return ``"High"``, ``"Medium"``, ``"Low"``, or ``""`` from an impact table cell.

    Checks both CSS class names (``icon--ff-impact-red`` / ``-ora`` / ``-yel``)
    and the span ``title`` attribute for forward-compatibility with markup changes.
    """
    if cell is None:
        return ""
    span = cell.find("span")
    if span is None:
        return ""

    classes = " ".join(span.get("class", []))
    title   = span.get("title", "").lower()

    if "ff-impact-red" in classes or "high"   in title:
        return "High"
    if "ff-impact-ora" in classes or "medium" in title:
        return "Medium"
    if "ff-impact-yel" in classes or "low"    in title:
        return "Low"
    return ""


# ---------------------------------------------------------------------------
# Private — shared state helpers
# ---------------------------------------------------------------------------

def _write_shared_state(updates: dict) -> None:
    """
    Merge *updates* into shared_state.json (top-level key merge) under a lock.
    Uses write-then-rename for near-atomic file replacement.
    """
    path = Path(SHARED_STATE_PATH)
    with _file_lock:
        state: dict = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                state = json.load(fh)

        state.update(updates)

        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, default=str)
        tmp.replace(path)


def _set_agent_status(running: bool, error: str = "") -> None:
    """Write news_agent heartbeat into shared_state["agent_statuses"]."""
    path = Path(SHARED_STATE_PATH)
    with _file_lock:
        state: dict = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                state = json.load(fh)

        state.setdefault("agent_statuses", {}).setdefault("news_agent", {})
        state["agent_statuses"]["news_agent"].update({
            "running":        running,
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "error":          error,
        })

        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, default=str)
        tmp.replace(path)


# ---------------------------------------------------------------------------
# NewsAgent class
# ---------------------------------------------------------------------------

class NewsAgent:
    """
    Polling agent that keeps shared_state["news"] fresh.

    Behaviour
    ---------
    * On startup: fetches the calendar immediately and prints today's
      high-impact USD events to the log.
    * Every 30 minutes: re-scrapes ForexFactory.
    * Every 60 seconds: evaluates the news window and writes pause_trading
      to shared_state.json.
    * When NEWS_HIGH_IMPACT_HALT = False: all checks are skipped;
      pause_trading is always written as false.

    Usage::

        agent = NewsAgent()
        agent.run()        # blocks; Ctrl-C to stop
    """

    def __init__(self) -> None:
        pass   # logger is configured globally by utils/logger.py on import

    # ------------------------------------------------------------------
    # Startup helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _print_todays_events() -> None:
        """Log all high-impact USD events scheduled for today (UTC date)."""
        today = datetime.now(timezone.utc).date()
        with _events_lock:
            todays = [
                e for e in _cached_events
                if e.currency == "USD" and e.datetime_utc.date() == today
            ]

        if not todays:
            logger.info("NewsAgent: no high-impact USD events today.")
            return

        logger.info(f"NewsAgent: {len(todays)} high-impact USD event(s) today:")
        for event in sorted(todays, key=lambda e: e.datetime_utc):
            logger.info(f"  {event}")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run_loop(self) -> None:
        """
        Self-correcting polling loop.

        The calendar is re-scraped every ``CALENDAR_REFRESH_SECS`` (30 min).
        The pause flag is re-evaluated and written every ``STATE_UPDATE_SECS``
        (60 s).  Each sleep is adjusted for elapsed work time so the actual
        interval stays close to the target.
        """
        logger.info(
            f"NewsAgent starting — "
            f"window=±{NEWS_WINDOW_MINUTES} min, "
            f"filter={'ON' if NEWS_HIGH_IMPACT_HALT else 'OFF (NEWS_HIGH_IMPACT_HALT=False)'}"
        )
        _set_agent_status(running=True, error="")

        # Epoch zero forces an immediate calendar fetch on the first iteration
        last_fetch = datetime(1970, 1, 1, tzinfo=timezone.utc)

        while True:
            cycle_start = time.monotonic()
            now         = datetime.now(timezone.utc)

            # ── Calendar refresh (every 30 min) ──────────────────────────────
            if (now - last_fetch).total_seconds() >= CALENDAR_REFRESH_SECS:
                fetch_forex_factory_calendar()
                last_fetch = now
                self._print_todays_events()

            # ── State update (every cycle = 60 s) ────────────────────────────
            update_news_state()
            _set_agent_status(running=True, error="")

            elapsed   = time.monotonic() - cycle_start
            sleep_for = max(0.0, STATE_UPDATE_SECS - elapsed)
            logger.debug(
                f"NewsAgent cycle {elapsed:.1f}s — sleeping {sleep_for:.0f}s"
            )
            time.sleep(sleep_for)

    def run(self) -> None:
        """Start the agent — blocks until interrupted or unrecoverable error."""
        try:
            self.run_loop()
        except KeyboardInterrupt:
            logger.info("NewsAgent stopped by user (KeyboardInterrupt).")
            _set_agent_status(running=False, error="Stopped by user")
        except Exception as exc:
            logger.exception(f"NewsAgent crashed: {exc}")
            _set_agent_status(running=False, error=str(exc))
            raise


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent = NewsAgent()
    agent.run()
