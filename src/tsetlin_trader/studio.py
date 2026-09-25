"""Local studio: real prices, real rules, real paper-account data. Read-only.

    python -m tsetlin_trader.studio            # then open http://127.0.0.1:8770

What it does
  - reads your Tiingo price file and computes today's blend, stress filter and
    plan preview with a verified pure-Python engine (see studio_core.py)
  - reads your Alpaca PAPER account with GET requests only (keys never reach the browser)
  - reads the bot's own records (decision log, virtual portfolios, state, public feed)
  - runs the bot's broker-free signal preview and its `--plan-only` cycle on request

What it never does: place, change or cancel an order. The Alpaca client below has
no method that is not a GET, and there is no order endpoint on this server.
Real prices are licensed for internal use, so this server binds to 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import studio_core as core

DEV_END = "2020-12-31"          # the research repo keeps 2021-2025 as a locked holdout
DEV_START = "2008-01-01"
STALE_DAYS = 5                   # same limit the bot uses before refusing to trade
PAPER_BASE = "https://paper-api.alpaca.markets"
PUBLISHED_V03 = {                # research v0.3 report, 2 bps, 2008-2020 development period
    "blend": ("equal_weight", 0.0991, 0.783, -0.2056, 0.915),
    "constant_half": ("constant_half", 0.0507, 0.782, -0.1071, 0.457),
    "always_defensive": ("always_defensive", 0.1080, 0.751, -0.2004, 1.0),
    "SPY": ("SPY", 0.0980, 0.551, -0.5186, 1.0),
    "simple_risk_filter": ("simple_risk_filter", 0.0924, 0.783, -0.1444, 0.854),
}


def repo_root() -> Path:
    here = Path(__file__).resolve().parents[2]
    return here if (here / "pyproject.toml").exists() else Path.cwd()


ROOT = repo_root()


def load_env(path: Path | None = None) -> None:
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def state_dir() -> Path:
    raw = os.environ.get("TT_STATE_DIR")
    p = Path(raw) if raw else ROOT / "results"
    return p if p.is_absolute() else ROOT / p


def mask(value: str | None) -> str | None:
    return None if not value else "..." + value[-4:]


# ------------------------------------------------------------------ prices
STUDIO_CACHE = "data/studio-prices.csv"
PRICE_FILES = (STUDIO_CACHE, "data/tiingo-prices.csv", "data/tiingo-prices-2008.csv")
_price_lock = threading.Lock()
_price_cache: dict = {}


def load_prices() -> dict:
    """The freshest local price file (by last date, then by length)."""
    with _price_lock:
        best = None
        for rel in PRICE_FILES:
            path = ROOT / rel
            if not path.exists():
                continue
            key = (str(path), path.stat().st_mtime_ns)
            if _price_cache.get("key") == key and _price_cache.get("path") == str(path):
                cand = _price_cache["val"]
            else:
                try:
                    dates, prices = core.load_prices_csv(path)
                except (ValueError, OSError) as exc:
                    continue_msg = f"{rel}: {exc}"
                    _price_cache.setdefault("errors", []).append(continue_msg)
                    continue
                cand = {"path": rel, "dates": dates, "prices": prices}
                _price_cache.update(key=key, path=str(path), val=cand)
            if best is None or (cand["dates"][-1], len(cand["dates"])) > (best["dates"][-1], len(best["dates"])):
                best = cand
        if best is None:
            raise FileNotFoundError("No price file found. Use 'Refresh prices' or run the bot's data download.")
        return best


def data_info() -> dict:
    d = load_prices()
    last = d["dates"][-1]
    age = (dt.date.today() - dt.date.fromisoformat(last)).days
    return {
        "source": d["path"], "first_date": d["dates"][0], "last_date": last, "rows": len(d["dates"]),
        "age_days": age, "fresh": age <= STALE_DAYS,
        "note": "Tiingo adjusted closes, internal use only; never leaves this machine",
    }


def _tiingo_get(ticker: str, start: str, end: str, token: str) -> list[dict]:
    q = urllib.parse.urlencode({"startDate": start, "endDate": end, "format": "json", "resampleFreq": "daily"})
    req = urllib.request.Request(
        f"https://api.tiingo.com/tiingo/daily/{ticker}/prices?{q}",
        headers={"Authorization": f"Token {token}", "User-Agent": "tsetlin-trader-studio/1"}, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def refresh_prices(start: str = "2002-01-01") -> dict:
    token = os.environ.get("TIINGO_API_TOKEN")
    if not token:
        raise RuntimeError("TIINGO_API_TOKEN is not set in .env")
    end = dt.date.today().isoformat()
    series: dict[str, dict[str, float]] = {}
    for a in core.ASSETS:
        rows = _tiingo_get(a, start, end, token)
        series[a] = {r["date"][:10]: float(r["adjClose"]) for r in rows if r.get("adjClose") is not None}
        if not series[a]:
            raise RuntimeError(f"Tiingo returned no data for {a}")
    common = sorted(set.intersection(*(set(s) for s in series.values())))
    if len(common) < 700:
        raise RuntimeError(f"Only {len(common)} common trading days; refusing a partial download")
    out = ROOT / STUDIO_CACHE
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as h:
        w = csv.writer(h)
        w.writerow(["date", *core.ASSETS])
        for d in common:
            w.writerow([d, *[repr(series[a][d]) for a in core.ASSETS]])
    core.load_prices_csv(tmp)  # validate before it replaces anything
    os.replace(tmp, out)
    _price_cache.clear()
    return {"rows": len(common), "first_date": common[0], "last_date": common[-1], "written": STUDIO_CACHE}


# ------------------------------------------------------------------ alpaca (GET only)
class PaperReader:
    """Read-only Alpaca paper client. It has one method and it can only GET."""

    def __init__(self, key: str, secret: str) -> None:
        self._h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "User-Agent": "tsetlin-trader-studio/1"}

    def get(self, path: str, params: dict | None = None):
        if not path.startswith("/v2/"):
            raise ValueError("only /v2/ read endpoints are allowed")
        url = PAPER_BASE + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        req = urllib.request.Request(url, headers=self._h, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            hint = {
                401: "Alpaca rejected the keys. They were probably regenerated or revoked: put the current paper "
                     "ALPACA_API_KEY and ALPACA_SECRET_KEY in .env and restart the studio.",
                403: "Alpaca refused this request. Check that the keys belong to a PAPER account.",
                429: "Alpaca is rate-limiting requests. Wait a minute and reload.",
            }.get(exc.code, "")
            raise RuntimeError(f"Alpaca answered HTTP {exc.code}. {hint}".strip()) from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach Alpaca: {exc.reason}") from None


def alpaca() -> PaperReader | None:
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    return PaperReader(key, secret) if key and secret else None


def account_snapshot() -> dict:
    rd = alpaca()
    if rd is None:
        return {"ok": False, "error": "ALPACA_API_KEY / ALPACA_SECRET_KEY are not set in .env"}
    try:
        a = rd.get("/v2/account")
        pos = rd.get("/v2/positions")
        open_o = rd.get("/v2/orders", {"status": "open", "limit": 50})
        recent = rd.get("/v2/orders", {"status": "closed", "limit": 15, "direction": "desc"})
        hist = rd.get("/v2/account/portfolio/history", {"period": "1M", "timeframe": "1D"})
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    f = lambda x: None if x in (None, "") else float(x)  # noqa: E731
    stamps = hist.get("timestamp") or []
    return {
        "ok": True, "mode": "PAPER",
        "account": {
            "id": mask(a.get("id")), "number": mask(a.get("account_number")), "status": a.get("status"),
            "equity": f(a.get("equity")), "last_equity": f(a.get("last_equity")), "cash": f(a.get("cash")),
            "buying_power": f(a.get("buying_power")), "blocked": bool(a.get("trading_blocked") or a.get("account_blocked")),
            "currency": a.get("currency"),
        },
        "positions": [{"symbol": p["symbol"], "qty": f(p.get("qty")), "market_value": f(p.get("market_value")),
                       "avg_entry": f(p.get("avg_entry_price")), "unrealized_pl": f(p.get("unrealized_pl")),
                       "side": p.get("side")} for p in pos],
        "open_orders": [_order(o) for o in open_o],
        "recent_orders": [_order(o) for o in recent],
        "history": {"dates": [dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat() for t in stamps],
                    "equity": hist.get("equity") or []},
    }


def _order(o: dict) -> dict:
    return {"symbol": o.get("symbol"), "side": o.get("side"), "status": o.get("status"),
            "notional": o.get("notional"), "qty": o.get("qty"), "filled_qty": o.get("filled_qty"),
            "filled_avg_price": o.get("filled_avg_price"), "submitted_at": o.get("submitted_at"),
            "filled_at": o.get("filled_at"), "client_order_id": o.get("client_order_id"), "type": o.get("type")}


# ------------------------------------------------------------------ signals
def _f(x, nd=2):
    return "n/a" if x is None else f"{x:,.{nd}f}"


def compute_signals() -> dict:
    d = load_prices()
    dates, P = d["dates"], d["prices"]
    t = len(dates) - 1
    tw = core.target_weights(P)
    ma20, ma100 = core.sma(P["SPY"], 20)[t], core.sma(P["SPY"], 100)[t]
    r60 = {a: core.pct_change(P[a], 60)[t] for a in core.MOMENTUM_UNIVERSE}
    spy = P["SPY"][t]
    sleeves = []
    trend_w = tw["trend"][t]
    sleeves.append({"name": "trend", "weights": trend_w, "why": (
        f"SPY 20-day average {_f(ma20)} is {'above' if trend_w['SPY'] else 'not above'} its 100-day average "
        f"{_f(ma100)}, so it holds {'SPY' if trend_w['SPY'] else 'cash'}")})
    mom_w = tw["momentum"][t]
    leader = next((a for a in core.MOMENTUM_UNIVERSE if mom_w[a] > 0), None)
    sleeves.append({"name": "momentum", "weights": mom_w, "why": (
        "60-day returns: " + ", ".join(f"{a} {r60[a]*100:+.1f}%" for a in core.MOMENTUM_UNIVERSE if r60[a] is not None)
        + (f"; {leader} leads, so it holds {leader}" if leader else "; not enough history"))})
    def_w = tw["defensive"][t]
    sleeves.append({"name": "defensive", "weights": def_w, "why": (
        f"SPY {_f(spy)} is {'above' if def_w['SPY'] else 'below'} its 100-day average {_f(ma100)}, so it holds "
        f"{'SPY' if def_w['SPY'] else 'bonds (TLT)'}")})
    blend = tw["blend"][t]
    daily = core.daily_returns(P["SPY"])
    s20, s60 = core.rolling_std(daily, 20)[t], core.rolling_std(daily, 60)[t]
    down = r60["SPY"] is not None and r60["SPY"] < 0
    jumpy = s20 is not None and s60 is not None and s20 > s60
    stressed = bool(down and jumpy)
    factor = core.REDUCTION if stressed else 1.0
    filt = {a: blend[a] * factor for a in core.ASSETS}
    # what the rules said each week recently (targets only, never performance)
    hist = []
    stress_all = core.stress_flags(P)
    for i in range(t, max(t - 26 * 5, 0), -5):
        w = tw["blend"][i]
        f_ = core.REDUCTION if stress_all[i] else 1.0
        hist.append({"date": dates[i], "blend": w, "filter_factor": f_})
    hist.reverse()
    return {
        "as_of": dates[t], "data": data_info(),
        "sleeves": sleeves, "blend": blend, "filter": filt,
        "stress": {"on": stressed, "spy_60d": r60["SPY"], "vol_20d": s20, "vol_60d": s60, "spy_down": down, "vol_rising": jumpy},
        "recent_targets": hist,
        "recorded": recorded_signals(),
    }


def read_jsonl_tail(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _clean(rec: dict) -> dict:
    sig = rec.get("signal") or {}
    return {
        "logged_at": rec.get("logged_at"), "status": rec.get("status"), "plan_only": rec.get("plan_only"),
        "reason": rec.get("reason"), "signal_provider": rec.get("signal_provider"),
        "strategy": sig.get("strategy"), "as_of": sig.get("as_of"), "target_weights": sig.get("target_weights"),
        "shadows": {m: {"strategy": v.get("strategy"), "as_of": v.get("as_of"), "error": v.get("error")}
                    for m, v in (rec.get("shadow_signals") or {}).items()},
        "risk_decision": rec.get("risk_decision"), "risk_reason": rec.get("risk_reason"), "equity": rec.get("equity"),
        "trades": rec.get("trades"), "orders": [{"symbol": o.get("symbol"), "side": o.get("side"), "status": o.get("status"),
                                                 "notional": o.get("notional")} for o in (rec.get("orders") or [])],
        "commit": (rec.get("fingerprint") or {}).get("code_commit"),
    }


def recorded_signals() -> dict:
    recs = [_clean(r) for r in read_jsonl_tail(state_dir() / "decisions.jsonl", 200)]
    out = {"controller": None, "shadows": {}}
    for r in reversed(recs):
        if out["controller"] is None and r["strategy"]:
            out["controller"] = {k: r[k] for k in ("logged_at", "as_of", "strategy", "target_weights", "status", "plan_only", "signal_provider")}
        for m, v in r["shadows"].items():
            if m not in out["shadows"] and v.get("strategy"):
                out["shadows"][m] = {**v, "logged_at": r["logged_at"]}
    return out


def plan_preview() -> dict:
    """What a cycle would do to the real account, using the studio's own rules. Preview only."""
    snap = account_snapshot()
    sig = compute_signals()
    cap = float(os.environ.get("POSITION_FRACTION", "0.25"))
    if not snap.get("ok"):
        return {"ok": False, "error": snap.get("error"), "as_of": sig["as_of"], "cap": cap,
                "target_fractions": {a: sig["blend"][a] * cap for a in core.ASSETS if sig["blend"][a] * cap > 0}}
    equity = snap["account"]["equity"]
    held = {p["symbol"]: p["market_value"] for p in snap["positions"]}
    target = {a: equity * sig["blend"][a] * cap for a in core.ASSETS if sig["blend"][a] * cap > 0}
    band, thr = 0.005, 0.005 * equity
    sells, buys = [], []
    for s in sorted((set(held) | set(target)) & set(core.ASSETS)):
        h, w = held.get(s, 0.0), target.get(s, 0.0)
        d = w - h
        if w <= 0 and h > 0:
            sells.append({"side": "sell", "symbol": s, "notional": round(h, 2), "close_all": True})
        elif d > thr:
            buys.append({"side": "buy", "symbol": s, "notional": round(d, 2)})
        elif -d > thr:
            sells.append({"side": "sell", "symbol": s, "notional": round(-d, 2)})
    rec = sig["recorded"]["controller"]
    agree = None
    if rec and rec.get("target_weights") and rec.get("as_of") == sig["as_of"]:
        agree = all(abs((rec["target_weights"].get(a, 0) or 0) - sig["blend"][a]) < 1e-4 for a in core.ASSETS)
    return {"ok": True, "as_of": sig["as_of"], "equity": equity, "cap": cap, "band": band, "held": held,
            "target": target, "trades": sells + buys, "ignored": sorted(s for s in held if s not in core.ASSETS),
            "agrees_with_bot_record": agree, "bot_record_as_of": rec.get("as_of") if rec else None,
            "note": "Preview from the studio's own engine. The bot's plan-only cycle is the authority."}


# ------------------------------------------------------------------ backtest / research / records
def backtest(start: str, end: str, cost: float) -> dict:
    d = load_prices()
    dates, P = d["dates"], d["prices"]
    clamped = end > DEV_END
    end = min(end, DEV_END)
    start = max(start, dates[0])
    first = core.first_return_index(dates, start)
    last = max(i for i, x in enumerate(dates) if x <= end)
    if first >= last - 20:
        raise ValueError("Not enough data in that window")
    ev = core.evaluate(P, first, last, cost)
    step = 5
    idx = list(range(0, last - first + 1, step))
    if idx[-1] != last - first:
        idx.append(last - first)
    out = {"portfolios": {}, "dates": [dates[first + i] for i in idx]}
    for name, r in ev.items():
        c = core.curve(r["returns"])
        out["portfolios"][name] = {
            "cagr": r["cagr"], "sharpe": r["sharpe"], "max_drawdown": r["max_drawdown"],
            "sortino": r["sortino"], "volatility": r["annual_volatility"], "total_return": r["total_return"],
            "mean_exposure": r["mean_exposure"], "annual_turnover": r["annual_turnover"],
            "curve": [round(c[i] * 100, 3) for i in idx]}
    comparable = (start <= DEV_START and end == DEV_END and cost == 2.0 and dates[0] <= "2002-08-01")
    if comparable:
        rows = []
        for key, (label, cagr, sharpe, mdd, expo) in PUBLISHED_V03.items():
            m = ev[key]
            rows.append({"portfolio": label, "cagr": m["cagr"], "cagr_pub": cagr, "sharpe": m["sharpe"], "sharpe_pub": sharpe,
                         "max_drawdown": m["max_drawdown"], "mdd_pub": mdd, "exposure": m["mean_exposure"], "exposure_pub": expo})
        out["validation"] = {"comparable": True, "rows": rows,
                             "source": "research v0.3 report (risk-filter-development-report.md), 2 bps, 2008-2020"}
    else:
        why = []
        if dates[0] > "2002-08-01":
            why.append(f"price history starts {dates[0]}; the published run used data from 2002 (press Refresh prices)")
        if not (start <= DEV_START and end == DEV_END):
            why.append("window differs from the published 2008-2020 run")
        if cost != 2.0:
            why.append("cost differs from the published 2 bps run")
        out["validation"] = {"comparable": False, "why": why}
    out.update(window={"start": dates[first], "end": dates[last], "requested_start": start, "end": dates[last],
                       "days": last - first + 1, "cost_bps": cost, "clamped_to_development": clamped,
                       "first_scored_return": dates[first]})
    out["locked"] = "2021-2025 is the research repo's locked holdout, so backtests stop at 2020-12-31."
    return out


def prices_view(years: float) -> dict:
    d = load_prices()
    dates, P = d["dates"], d["prices"]
    n = max(30, int(years * 252))
    lo = max(0, len(dates) - n)
    return {"dates": dates[lo:], "series": {a: P[a][lo:] for a in core.ASSETS}, "data": data_info()}


def research() -> dict:
    out = []
    for folder in sorted((ROOT / "results").glob("dev-benchmark*")):
        comp = folder / "comparison.csv"
        if not comp.exists():
            continue
        rows = list(csv.DictReader(open(comp, encoding="utf-8")))
        gates = list(csv.DictReader(open(folder / "decision-gates.csv", encoding="utf-8"))) if (folder / "decision-gates.csv").exists() else []
        spec = {}
        if (folder / "experiment-spec.json").exists():
            spec = json.loads((folder / "experiment-spec.json").read_text(encoding="utf-8"))
        keep = ("model", "run", "portfolio", "accuracy", "cagr", "sharpe", "max_drawdown", "config_feature_set", "config_tmu_seed")
        out.append({
            "folder": folder.name, "name": spec.get("name"), "question": spec.get("question"),
            "rows": [{k: r.get(k) for k in keep if k in r} for r in rows if r.get("portfolio") in ("selector", "equal_weight", "SPY")],
            "gates_passed": sum(1 for g in gates if g.get("primary_gate_passed") == "True"), "gates_total": len(gates),
        })
    return {"benchmarks": out}


def records() -> dict:
    sd = state_dir()
    def jload(p: Path):
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except (ValueError, OSError):
            return None
    state, port, acct, live = jload(sd / "state.json"), jload(sd / "portfolios.json"), jload(sd / "account.json"), jload(ROOT / "docs" / "live.json")
    return {
        "state_dir": str(sd), "using_private_state_dir": bool(os.environ.get("TT_STATE_DIR")),
        "decisions": [_clean(r) for r in read_jsonl_tail(sd / "decisions.jsonl", 40)][::-1],
        "risk_state": state, "portfolios": port,
        "account_binding": ({"present": True, "keys": sorted(acct.keys())} if isinstance(acct, dict) else {"present": False}),
        "public_feed": ({"generated_at": live.get("generated_at"), "gate": live.get("gate"), "latest_run": live.get("latest_run")} if live else None),
    }


def git_info() -> dict:
    def run(*a):
        try:
            return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return {"commit": run("rev-parse", "--short", "HEAD") or None, "branch": run("rev-parse", "--abbrev-ref", "HEAD") or None,
            "dirty": bool(run("status", "--porcelain"))}


def readiness() -> dict:
    checks = []
    def add(id_, label, ok, detail, manual=False):
        checks.append({"id": id_, "label": label, "ok": ok, "detail": detail, "manual": manual})
    di = data_info()
    add("data", "Price data is fresh", di["fresh"], f"newest price {di['last_date']} ({di['age_days']} days old, limit {STALE_DAYS})")
    add("tiingo", "Tiingo token configured", bool(os.environ.get("TIINGO_API_TOKEN")), "needed to refresh prices")
    keys = bool(os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY"))
    add("keys", "Alpaca paper keys configured", keys, "read from .env; never sent to the browser")
    snap = account_snapshot() if keys else {"ok": False, "error": "no keys"}
    add("reach", "Alpaca paper account reachable and active",
        bool(snap.get("ok") and snap["account"]["status"] == "ACTIVE" and not snap["account"]["blocked"]),
        (f"status {snap['account']['status']}, account {snap['account']['number']}" if snap.get("ok") else snap.get("error", "")))
    if snap.get("ok"):
        add("flat", "Account flat with no open orders (required for a NEW paper account)",
            not snap["positions"] and not snap["open_orders"],
            f"{len(snap['positions'])} position(s), {len(snap['open_orders'])} open order(s)")
    sd = state_dir()
    add("statedir", "TT_STATE_DIR points to a private, existing directory", bool(os.environ.get("TT_STATE_DIR")) and sd.exists(),
        f"{sd}" if os.environ.get("TT_STATE_DIR") else "TT_STATE_DIR is not set; the bot refuses to trade an Alpaca account without it")
    add("state", "Risk state file present", (sd / "state.json").exists(), str(sd / "state.json"))
    add("binding", "Account binding file present", (sd / "account.json").exists(), str(sd / "account.json"))
    add("alert", "Alert webhook configured", bool(os.environ.get("ALERT_WEBHOOK_URL")), "delivery itself can only be verified by a real test alert")
    g = git_info()
    add("git", "Working tree is clean", not g["dirty"], f"{g['branch']} @ {g['commit']}")
    for id_, label in (("ci", "Full offline CI green at the deployed commit"),
                       ("host", "One supervised host, one process, reboot returns paused"),
                       ("watchdog", "Watchdog running independently, alert receipt verified"),
                       ("backup", "Private state backed up and recovery verified"),
                       ("operator", "Operator available to reconcile uncertain orders")):
        add(id_, label, None, "cannot be verified from here; confirm it yourself", manual=True)
    return {"checks": checks, "source": "docs/RELIABILITY.md, 'Required before supervised paper execution'",
            "verdict": "Read-only studio: this page never places orders. Live-money trading is out of scope."}


# ------------------------------------------------------------------ jobs (bot CLIs, no orders)
JOBS: dict[str, dict] = {}
_job_lock = threading.Lock()


def start_job(name: str, cmd: list[str], env_extra: dict[str, str], timeout: int) -> dict:
    with _job_lock:
        if JOBS.get(name, {}).get("status") == "running":
            return JOBS[name]
        JOBS[name] = {"status": "running", "started": time.time(), "cmd": " ".join(cmd[2:]), "output": None}

    def work():
        env = {**os.environ, **env_extra, "PYTHONIOENCODING": "utf-8"}
        try:
            p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
            res = {"status": "done" if p.returncode == 0 else "failed", "returncode": p.returncode,
                   "output": p.stdout[-12000:], "error": p.stderr.strip().splitlines()[-6:] if p.returncode else None}
        except subprocess.TimeoutExpired:
            res = {"status": "failed", "returncode": None, "output": None, "error": [f"timed out after {timeout}s"]}
        except OSError as exc:
            res = {"status": "failed", "returncode": None, "output": None, "error": [str(exc)]}
        with _job_lock:
            JOBS[name].update(res, finished=time.time())

    threading.Thread(target=work, daemon=True).start()
    return JOBS[name]


# ------------------------------------------------------------------ http
STATIC = Path(__file__).with_name("studio_static") / "index.html"


class Handler(BaseHTTPRequestHandler):
    server_version = "TsetlinStudio"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _ok_host(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://fonts.gstatic.com; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj, allow_nan=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def _guard(self, fn):
        try:
            self._json(fn())
        except FileNotFoundError as exc:
            self._json({"ok": False, "error": str(exc)}, 200)
        except (ValueError, RuntimeError) as exc:
            self._json({"ok": False, "error": str(exc)}, 200)
        except Exception as exc:  # never leak a traceback to the page
            self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_GET(self):
        if not self._ok_host():
            return self._send(403, b"forbidden host", "text/plain")
        url = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        route = url.path
        if route in ("/", "/index.html"):
            return self._send(200, STATIC.read_bytes(), "text/html; charset=utf-8")
        routes = {
            "/api/status": lambda: {"ok": True, "data": data_info(), "git": git_info(), "mode": "READ-ONLY",
                                    "env": {"tiingo": bool(os.environ.get("TIINGO_API_TOKEN")), "alpaca": bool(alpaca()),
                                            "state_dir": str(state_dir()), "private_state": bool(os.environ.get("TT_STATE_DIR"))}},
            "/api/account": account_snapshot,
            "/api/signals": compute_signals,
            "/api/plan-preview": plan_preview,
            "/api/prices": lambda: prices_view(float(q.get("years", "3"))),
            "/api/backtest": lambda: backtest(q.get("start", DEV_START), q.get("end", DEV_END), float(q.get("cost", "2"))),
            "/api/research": research,
            "/api/records": records,
            "/api/readiness": readiness,
            "/api/jobs": lambda: {"jobs": JOBS},
        }
        fn = routes.get(route)
        if fn is None:
            return self._send(404, b"not found", "text/plain")
        self._guard(fn)

    def do_POST(self):
        if not self._ok_host() or self.headers.get("X-Studio") != "1":
            return self._send(403, b"forbidden", "text/plain")
        route = urllib.parse.urlparse(self.path).path
        py = sys.executable
        if route == "/api/refresh-prices":
            return self._guard(lambda: {"ok": True, **refresh_prices()})
        if route == "/api/signal-preview":
            return self._guard(lambda: {"ok": True, "job": start_job(
                "signal_preview", [py, "-m", "tsetlin_trader.signal_preview"], {"SHADOW_MODELS": "tmu,bernoulli"}, 900)})
        if route == "/api/plan-cycle":
            if not alpaca():
                return self._json({"ok": False, "error": "Alpaca keys are not configured"})
            return self._guard(lambda: {"ok": True, "job": start_job(
                "plan_cycle", [py, "-m", "tsetlin_trader.run_cycle", "--broker", "alpaca", "--signal", "blend", "--plan-only"],
                {"SHADOW_MODELS": "tmu,bernoulli"}, 900)})
        return self._send(404, b"not found", "text/plain")


def serve(port: int) -> None:
    load_env()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Tsetlin Trader Studio  http://127.0.0.1:{port}   (read-only; Ctrl+C to stop)")
    print(f"  repo:  {ROOT}\n  state: {state_dir()}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8770)
    serve(ap.parse_args().port)


if __name__ == "__main__":
    main()
