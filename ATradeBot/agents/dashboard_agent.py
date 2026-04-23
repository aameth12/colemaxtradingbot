"""
agents/dashboard_agent.py
-------------------------
Dashboard Agent — serves a live trading dashboard at http://localhost:8050.

Sections:
  1. Header          : bot name, status dot, live clock, today P&L
  2. Metrics row     : 5 KPI cards (open trades, win rate, pips, balance, drawdown)
  3. Charts          : equity curve · win/loss per symbol · session activity heatmap
  4. Open trades     : live positions with pips and green/red row colouring
  5. Trade log       : last 50 closed trades, sortable

Refresh: dcc.Interval every 5 seconds.
Data  : utils/shared_state.json (live) + data/trade_log.csv (history).
"""

import csv
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import dash
from dash import dcc, html, dash_table
from dash.dependencies import Input, Output
import plotly.graph_objects as go

from utils.config import (
    DASHBOARD_HOST,
    DASHBOARD_PORT,
    SHARED_STATE_PATH,
    SYMBOLS,
    TRADE_LOG_PATH,
)
from utils.logger import logger


# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------

_BG       = "#0d1117"
_CARD_BG  = "#161b22"
_BORDER   = "#30363d"
_TEXT     = "#e6edf3"
_MUTED    = "#8b949e"
_GREEN    = "#2ea043"
_RED      = "#f85149"
_BLUE     = "#388bfd"
_YELLOW   = "#d29922"

_DD_WARN  = 8.0   # drawdown % threshold — card turns red above this

# pip sizes per symbol (1 pip in price units)
_PIP = {"XAUUSD": 0.01, "AAPL": 0.01, "TSLA": 0.01, "NVDA": 0.01, "AMZN": 0.01}

_tbl_style = {
    "backgroundColor": _CARD_BG,
    "color": _TEXT,
    "border": f"1px solid {_BORDER}",
    "borderRadius": "8px",
    "overflow": "hidden",
}
_tbl_header = {"backgroundColor": "#21262d", "color": _MUTED, "fontWeight": "600",
                "fontSize": "12px", "border": f"1px solid {_BORDER}"}
_tbl_cell   = {"backgroundColor": _CARD_BG, "color": _TEXT, "fontSize": "13px",
                "border": f"1px solid {_BORDER}", "padding": "8px 12px"}

_file_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Data helpers
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


def _today_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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
        state.setdefault("agent_statuses", {}).setdefault("dashboard_agent", {})
        state["agent_statuses"]["dashboard_agent"].update({
            "running":        running,
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "error":          error,
        })
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, default=str)
        tmp.replace(path)


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------

def _chart_layout(title: str, **extra) -> dict:
    layout = dict(
        title=dict(text=title, font=dict(color=_TEXT, size=13), x=0.01, y=0.97),
        paper_bgcolor=_CARD_BG,
        plot_bgcolor=_CARD_BG,
        font=dict(color=_TEXT, size=11),
        margin=dict(l=50, r=16, t=44, b=44),
        showlegend=True,
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=_MUTED)),
        xaxis=dict(gridcolor=_BORDER, zerolinecolor=_BORDER, color=_MUTED),
        yaxis=dict(gridcolor=_BORDER, zerolinecolor=_BORDER, color=_MUTED),
    )
    layout.update(extra)
    return layout


def _equity_fig(rows: list[dict], current_balance: float) -> go.Figure:
    fig = go.Figure()
    sorted_rows = sorted(rows, key=lambda r: str(r.get("timestamp", "")))
    profits     = [_sf(r.get("profit")) for r in sorted_rows]
    timestamps  = [r.get("timestamp", "") for r in sorted_rows]

    if profits:
        start   = current_balance - sum(profits)
        equity  = [start + sum(profits[: i + 1]) for i in range(len(profits))]
        color   = _GREEN if equity[-1] >= equity[0] else _RED
        fig.add_trace(go.Scatter(
            x=timestamps, y=equity,
            mode="lines", line=dict(color=color, width=2),
            fill="tozeroy", fillcolor=color.replace(")", ", 0.08)").replace("rgb", "rgba"),
            name="Equity",
        ))
    else:
        if current_balance:
            fig.add_trace(go.Scatter(x=[_today_prefix()], y=[current_balance],
                                     mode="markers", marker=dict(color=_BLUE, size=8),
                                     name="Balance"))

    fig.update_layout(**_chart_layout("Equity Curve"))
    return fig


def _winloss_fig(rows: list[dict]) -> go.Figure:
    wins   = {s: 0 for s in SYMBOLS}
    losses = {s: 0 for s in SYMBOLS}
    for r in rows:
        sym = r.get("symbol", "")
        if sym in wins:
            p = _sf(r.get("profit"))
            if p > 0:
                wins[sym] += 1
            elif p < 0:
                losses[sym] += 1

    fig = go.Figure()
    fig.add_trace(go.Bar(x=SYMBOLS, y=[wins[s] for s in SYMBOLS],
                         name="Win",  marker_color=_GREEN))
    fig.add_trace(go.Bar(x=SYMBOLS, y=[losses[s] for s in SYMBOLS],
                         name="Loss", marker_color=_RED))
    fig.update_layout(**_chart_layout("Win / Loss by Symbol", barmode="group"))
    return fig


def _heatmap_fig(rows: list[dict]) -> go.Figure:
    day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    z = [[0] * 24 for _ in range(7)]

    for r in rows:
        ts_raw = str(r.get("timestamp", ""))
        if not ts_raw:
            continue
        try:
            ts  = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            z[ts.weekday()][ts.hour] += 1
        except (ValueError, TypeError):
            pass

    fig = go.Figure(data=go.Heatmap(
        z=z,
        x=list(range(24)),
        y=day_labels,
        colorscale=[[0, _CARD_BG], [0.001, "#1a3a5c"], [1, _BLUE]],
        showscale=False,
        hoverongaps=False,
        xgap=2,
        ygap=2,
    ))
    fig.update_layout(**_chart_layout(
        "Trade Activity (UTC hour × day)",
        xaxis=dict(gridcolor=_BORDER, zerolinecolor=_BORDER, color=_MUTED,
                   tickmode="array", tickvals=list(range(0, 24, 2)),
                   ticktext=[f"{h:02d}:00" for h in range(0, 24, 2)]),
        yaxis=dict(gridcolor=_BORDER, zerolinecolor=_BORDER, color=_MUTED),
    ))
    return fig


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def _kpi_card(label: str, val_id: str, wrap_id: Optional[str] = None) -> html.Div:
    inner = [
        html.Div(label, style={"color": _MUTED, "fontSize": "11px",
                                "textTransform": "uppercase", "letterSpacing": "0.05em",
                                "marginBottom": "8px"}),
        html.Div("—", id=val_id, style={"color": _TEXT, "fontSize": "24px",
                                         "fontWeight": "700", "fontVariantNumeric": "tabular-nums"}),
    ]
    base_style = {
        "background": _CARD_BG,
        "border": f"1px solid {_BORDER}",
        "borderRadius": "8px",
        "padding": "18px 20px",
        "flex": "1",
        "minWidth": "120px",
    }
    if wrap_id:
        return html.Div(inner, id=wrap_id, style=base_style)
    return html.Div(inner, style=base_style)


_OPEN_COLS = [
    {"name": "Symbol",    "id": "Symbol"},
    {"name": "Direction", "id": "Direction"},
    {"name": "Entry",     "id": "Entry"},
    {"name": "Current",   "id": "Current"},
    {"name": "Pips",      "id": "Pips"},
    {"name": "SL",        "id": "SL"},
    {"name": "TP",        "id": "TP"},
    {"name": "Duration",  "id": "Duration"},
]

_LOG_COLS = [
    {"name": "Time",      "id": "Time",      "type": "text"},
    {"name": "Symbol",    "id": "Symbol",    "type": "text"},
    {"name": "Direction", "id": "Direction", "type": "text"},
    {"name": "Price",     "id": "Price",     "type": "numeric"},
    {"name": "Lots",      "id": "Lots",      "type": "numeric"},
    {"name": "Profit $",  "id": "Profit",    "type": "numeric"},
    {"name": "Notes",     "id": "Notes",     "type": "text"},
]


def _build_layout() -> html.Div:
    return html.Div([
        dcc.Interval(id="interval", interval=5_000, n_intervals=0),

        # ── Header ────────────────────────────────────────────────────────
        html.Div([
            html.Div([
                html.Span(id="dot", style={
                    "display": "inline-block", "width": "12px", "height": "12px",
                    "borderRadius": "50%", "background": _RED,
                    "marginRight": "10px", "verticalAlign": "middle",
                }),
                html.Span("ATradeBot", style={
                    "color": _TEXT, "fontSize": "20px", "fontWeight": "700",
                    "verticalAlign": "middle",
                }),
            ]),
            html.Div([
                html.Span(id="clock", style={"color": _MUTED, "fontSize": "14px",
                                              "marginRight": "24px",
                                              "fontVariantNumeric": "tabular-nums"}),
                html.Span("Today P&L: ", style={"color": _MUTED, "fontSize": "14px"}),
                html.Span(id="pnl-header", style={"color": _TEXT, "fontSize": "16px",
                                                    "fontWeight": "700",
                                                    "fontVariantNumeric": "tabular-nums"}),
            ]),
        ], style={
            "display": "flex", "justifyContent": "space-between", "alignItems": "center",
            "padding": "16px 24px", "background": _CARD_BG,
            "borderBottom": f"1px solid {_BORDER}", "marginBottom": "20px",
        }),

        # ── Metrics row ───────────────────────────────────────────────────
        html.Div([
            _kpi_card("Open Trades",  "card-open"),
            _kpi_card("Win Rate",     "card-winrate"),
            _kpi_card("Today Pips",   "card-pips"),
            _kpi_card("Balance",      "card-balance"),
            _kpi_card("Drawdown",     "card-dd", wrap_id="card-dd-wrap"),
        ], style={"display": "flex", "gap": "12px", "padding": "0 24px",
                  "marginBottom": "20px"}),

        # ── Charts ────────────────────────────────────────────────────────
        html.Div([
            html.Div(dcc.Graph(id="fig-equity",  config={"displayModeBar": False},
                               style={"height": "260px"}),
                     style={"flex": "2", "background": _CARD_BG,
                            "border": f"1px solid {_BORDER}", "borderRadius": "8px",
                            "overflow": "hidden"}),
            html.Div(dcc.Graph(id="fig-winloss", config={"displayModeBar": False},
                               style={"height": "260px"}),
                     style={"flex": "1", "background": _CARD_BG,
                            "border": f"1px solid {_BORDER}", "borderRadius": "8px",
                            "overflow": "hidden"}),
            html.Div(dcc.Graph(id="fig-heatmap", config={"displayModeBar": False},
                               style={"height": "260px"}),
                     style={"flex": "1.5", "background": _CARD_BG,
                            "border": f"1px solid {_BORDER}", "borderRadius": "8px",
                            "overflow": "hidden"}),
        ], style={"display": "flex", "gap": "12px", "padding": "0 24px",
                  "marginBottom": "20px"}),

        # ── Open trades table ─────────────────────────────────────────────
        html.Div([
            html.Div("Open Positions", style={"color": _MUTED, "fontSize": "12px",
                                               "textTransform": "uppercase",
                                               "letterSpacing": "0.05em",
                                               "marginBottom": "10px"}),
            dash_table.DataTable(
                id="tbl-open",
                columns=_OPEN_COLS,
                data=[],
                sort_action="native",
                style_table={**_tbl_style, "overflowX": "auto"},
                style_header=_tbl_header,
                style_cell=_tbl_cell,
                style_data_conditional=[],
            ),
        ], style={"padding": "0 24px", "marginBottom": "20px"}),

        # ── Trade log table ───────────────────────────────────────────────
        html.Div([
            html.Div("Trade Log (last 50)", style={"color": _MUTED, "fontSize": "12px",
                                                    "textTransform": "uppercase",
                                                    "letterSpacing": "0.05em",
                                                    "marginBottom": "10px"}),
            dash_table.DataTable(
                id="tbl-log",
                columns=_LOG_COLS,
                data=[],
                sort_action="native",
                page_size=25,
                style_table={**_tbl_style, "overflowX": "auto"},
                style_header=_tbl_header,
                style_cell=_tbl_cell,
                style_data_conditional=[
                    {"if": {"filter_query": "{Profit} > 0"},
                     "backgroundColor": "#0d2818", "color": _GREEN},
                    {"if": {"filter_query": "{Profit} < 0"},
                     "backgroundColor": "#2d0f0f", "color": _RED},
                ],
            ),
        ], style={"padding": "0 24px", "paddingBottom": "40px"}),

    ], style={"background": _BG, "minHeight": "100vh",
              "fontFamily": "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"})


# ---------------------------------------------------------------------------
# Dash app
# ---------------------------------------------------------------------------

app = dash.Dash(
    __name__,
    title="ATradeBot Dashboard",
    update_title=None,
    suppress_callback_exceptions=True,
)
app.layout = _build_layout()


# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------

@app.callback(
    Output("dot",          "style"),
    Output("clock",        "children"),
    Output("pnl-header",   "children"),
    Output("card-open",    "children"),
    Output("card-winrate", "children"),
    Output("card-pips",    "children"),
    Output("card-balance", "children"),
    Output("card-dd",      "children"),
    Output("card-dd-wrap", "style"),
    Output("fig-equity",   "figure"),
    Output("fig-winloss",  "figure"),
    Output("fig-heatmap",  "figure"),
    Output("tbl-open",     "data"),
    Output("tbl-open",     "style_data_conditional"),
    Output("tbl-log",      "data"),
    Input("interval",      "n_intervals"),
)
def refresh(_n):
    state     = _read_state()
    rows      = _load_trade_log()
    today     = _today_prefix()
    now_utc   = datetime.now(timezone.utc)

    # ── Header ────────────────────────────────────────────────────────────
    any_running = any(
        isinstance(v, dict) and v.get("running")
        for v in state.get("agent_statuses", {}).values()
    )
    dot_style = {
        "display": "inline-block", "width": "12px", "height": "12px",
        "borderRadius": "50%",
        "background": _GREEN if any_running else _RED,
        "marginRight": "10px", "verticalAlign": "middle",
    }
    clock_str = now_utc.strftime("%Y-%m-%d  %H:%M:%S UTC")

    perf    = state.get("performance", {})
    pnl_day = _sf(perf.get("pnl_today"))
    pnl_str = (f"+${pnl_day:.2f}" if pnl_day >= 0 else f"-${abs(pnl_day):.2f}")
    pnl_color = _GREEN if pnl_day >= 0 else _RED

    # ── Metrics ───────────────────────────────────────────────────────────
    positions = state.get("current_positions", {})
    open_count = len(positions)

    total_today = int(perf.get("total_trades_today", 0) or 0)
    wins_today  = int(perf.get("winning_trades_today", 0) or 0)
    win_rate    = (wins_today / total_today * 100) if total_today else 0.0
    win_rate_str = f"{win_rate:.1f}%"

    today_rows = [r for r in rows if str(r.get("timestamp", "")).startswith(today)]
    today_pips = 0.0
    for r in today_rows:
        notes = r.get("notes", "")
        if "pips=" in notes:
            try:
                today_pips += float(notes.split("pips=")[1].split()[0])
            except (IndexError, ValueError):
                pass
    pips_str = (f"+{today_pips:.1f}" if today_pips >= 0 else f"{today_pips:.1f}")

    acc        = state.get("account_info", {})
    balance    = _sf(acc.get("balance"))
    balance_str = f"${balance:,.2f}"

    dd_pct  = _sf(acc.get("drawdown_pct") or perf.get("max_drawdown_today_pct"))
    dd_str  = f"{dd_pct:.2f}%"
    dd_card_style = {
        "background": "#2d0f0f" if dd_pct > _DD_WARN else _CARD_BG,
        "border": f"1px solid {'#f85149' if dd_pct > _DD_WARN else _BORDER}",
        "borderRadius": "8px",
        "padding": "18px 20px",
        "flex": "1",
        "minWidth": "120px",
    }

    # ── Charts ────────────────────────────────────────────────────────────
    fig_equity  = _equity_fig(rows, balance)
    fig_winloss = _winloss_fig(rows)
    fig_heatmap = _heatmap_fig(rows)

    # ── Open positions table ──────────────────────────────────────────────
    market_data = state.get("market_data", {})
    open_rows: list[dict] = []
    for pos in positions.values():
        sym   = pos.get("symbol", "?")
        side  = str(pos.get("type", pos.get("action", "?"))).upper()
        entry = _sf(pos.get("open_price", pos.get("price")))
        sl    = _sf(pos.get("stop_loss",  pos.get("sl")))
        tp    = _sf(pos.get("take_profit", pos.get("tp")))

        md      = market_data.get(sym, {})
        current = _sf(md.get("ask" if "BUY" in side else "bid"))

        pip_size = _PIP.get(sym, 0.01)
        if entry > 0 and current > 0 and pip_size > 0:
            raw_pips = (current - entry) / pip_size if "BUY" in side else (entry - current) / pip_size
            pips_disp = round(raw_pips, 1)
        else:
            pips_disp = 0.0

        open_time = pos.get("open_time", pos.get("open_at", pos.get("time", "")))
        dur = "—"
        if open_time:
            try:
                ot  = datetime.fromisoformat(str(open_time).replace("Z", "+00:00"))
                delta = now_utc - ot
                h, rem = divmod(int(delta.total_seconds()), 3600)
                m = rem // 60
                dur = f"{h}h {m}m" if h else f"{m}m"
            except (ValueError, TypeError):
                pass

        open_rows.append({
            "Symbol":    sym,
            "Direction": side,
            "Entry":     f"{entry:.5g}" if entry else "—",
            "Current":   f"{current:.5g}" if current else "—",
            "Pips":      pips_disp,
            "SL":        f"{sl:.5g}" if sl else "—",
            "TP":        f"{tp:.5g}" if tp else "—",
            "Duration":  dur,
        })

    open_conditional = [
        {"if": {"filter_query": "{Pips} > 0"},
         "backgroundColor": "#0d2818", "color": _GREEN},
        {"if": {"filter_query": "{Pips} < 0"},
         "backgroundColor": "#2d0f0f", "color": _RED},
    ]

    # ── Trade log table ───────────────────────────────────────────────────
    log_rows: list[dict] = []
    for r in reversed(rows[-50:]):
        profit = _sf(r.get("profit"))
        log_rows.append({
            "Time":      str(r.get("timestamp", ""))[:19],
            "Symbol":    r.get("symbol", ""),
            "Direction": r.get("action", ""),
            "Price":     round(_sf(r.get("price")), 5),
            "Lots":      round(_sf(r.get("volume")), 2),
            "Profit":    round(profit, 2),
            "Notes":     str(r.get("notes", "")),
        })

    return (
        dot_style,
        clock_str,
        html.Span(pnl_str, style={"color": pnl_color}),
        str(open_count),
        win_rate_str,
        pips_str,
        balance_str,
        dd_str,
        dd_card_style,
        fig_equity,
        fig_winloss,
        fig_heatmap,
        open_rows,
        open_conditional,
        log_rows,
    )


# ---------------------------------------------------------------------------
# DashboardAgent
# ---------------------------------------------------------------------------

class DashboardAgent:
    """
    Wraps the Dash app and manages the agent heartbeat.

    Usage::

        agent = DashboardAgent()
        agent.run()        # blocks; Ctrl-C to stop
    """

    def run(self) -> None:
        _set_agent_status(running=True, error="")
        print(f"Dashboard at http://localhost:{DASHBOARD_PORT}")
        logger.info(f"DashboardAgent starting on {DASHBOARD_HOST}:{DASHBOARD_PORT}")
        try:
            app.run(
                host=DASHBOARD_HOST,
                port=DASHBOARD_PORT,
                debug=False,
            )
        except Exception as exc:
            logger.exception(f"DashboardAgent crashed: {exc}")
            _set_agent_status(running=False, error=str(exc))
            raise
        finally:
            _set_agent_status(running=False, error="")
            logger.info("DashboardAgent stopped")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent = DashboardAgent()
    agent.run()
