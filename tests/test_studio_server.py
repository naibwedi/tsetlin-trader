import datetime as dt
import io
import json
import math
import types
import urllib.error

import pytest

from tsetlin_trader import studio


def weekday_dates(start, n):
    out, day = [], dt.date.fromisoformat(start)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


def write_prices(path, start="2003-01-02", n=5800):
    dates = weekday_dates(start, n)
    rows = ["date,SPY,QQQ,IWM,TLT"]
    p = {"SPY": 100.0, "QQQ": 100.0, "IWM": 100.0, "TLT": 100.0}
    drift = {"SPY": 0.0004, "QQQ": 0.0005, "IWM": 0.0003, "TLT": 0.0001}
    for i, d in enumerate(dates):
        for a in p:
            p[a] *= 1 + drift[a] + 0.008 * math.sin(i * (1.3 + len(a) * 0.1) + len(a))
        rows.append(f"{d}," + ",".join(f"{p[a]:.6f}" for a in ("SPY", "QQQ", "IWM", "TLT")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return dates


@pytest.fixture
def home(tmp_path, monkeypatch):
    write_prices(tmp_path / "data" / "p.csv")
    monkeypatch.setattr(studio, "ROOT", tmp_path)
    monkeypatch.setattr(studio, "PRICE_FILES", ("data/p.csv",))
    studio._price_cache.clear()
    monkeypatch.delenv("TT_STATE_DIR", raising=False)
    return tmp_path


# ---------------------------------------------------------------- the Alpaca client can only read
def test_paper_reader_exposes_only_get():
    assert [n for n in dir(studio.PaperReader) if not n.startswith("_")] == ["get"]


def test_paper_reader_sends_get_requests_to_the_paper_host(monkeypatch):
    seen = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        seen.append((req.get_method(), req.full_url, req.data))
        return Resp(b"{}")

    monkeypatch.setattr(studio.urllib.request, "urlopen", fake_urlopen)
    studio.PaperReader("k", "s").get("/v2/account")
    method, url, body = seen[0]
    assert method == "GET" and body is None
    assert url.startswith("https://paper-api.alpaca.markets/v2/")


def test_paper_reader_refuses_anything_but_v2_reads():
    with pytest.raises(ValueError):
        studio.PaperReader("k", "s").get("/v1/orders")


@pytest.mark.parametrize("code,fragment", [(401, "rejected the keys"), (403, "PAPER"), (429, "rate-limiting")])
def test_alpaca_errors_are_actionable(monkeypatch, code, fragment):
    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)

    monkeypatch.setattr(studio.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError, match=fragment):
        studio.PaperReader("k", "s").get("/v2/account")


def test_no_order_endpoints_exist_on_the_server():
    src = open(studio.__file__, encoding="utf-8").read()
    assert '"/v2/orders",' in src  # reading orders is allowed
    for forbidden in ("method=\"POST\"", "method='POST'", "method=\"DELETE\"", "method=\"PATCH\"", "method=\"PUT\""):
        assert forbidden not in src


# ---------------------------------------------------------------- the locked holdout stays locked
def test_backtest_never_reaches_past_the_development_period(home):
    out = studio.backtest("2008-01-01", "2026-12-31", 2.0)
    assert out["window"]["end"] <= studio.DEV_END
    assert out["window"]["clamped_to_development"] is True
    assert max(out["dates"]) <= studio.DEV_END
    assert "locked" in out["locked"]


def test_backtest_is_only_called_comparable_when_it_can_be(home):
    out = studio.backtest("2008-01-01", "2020-12-31", 2.0)
    assert out["validation"]["comparable"] is False  # synthetic history starts in 2003
    assert out["validation"]["why"]
    assert set(out["portfolios"]) >= {"blend", "simple_risk_filter", "SPY", "always_defensive"}
    assert out["portfolios"]["blend"]["curve"][0] == pytest.approx(100, abs=2)


def test_backtest_rejects_a_window_with_too_little_data(home):
    with pytest.raises(ValueError):
        studio.backtest("2020-12-01", "2020-12-31", 2.0)


# ---------------------------------------------------------------- signals and the plan preview
def test_signals_use_the_real_rules_and_read_recorded_shadows(home, monkeypatch):
    (home / "results").mkdir()
    rec = {"logged_at": "2026-09-18T19:39:19+00:00", "status": "executed", "plan_only": False, "signal_provider": "blend",
           "signal": {"as_of": "2026-09-17", "strategy": "blend", "target_weights": {"SPY": 1.0}},
           "shadow_signals": {"tmu": {"strategy": "cash", "as_of": "2026-09-17"}, "bernoulli": {"error": "boom"}}}
    (home / "results" / "decisions.jsonl").write_text(json.dumps(rec) + "\nnot json\n", encoding="utf-8")
    s = studio.compute_signals()
    assert sum(s["blend"].values()) <= 1 + 1e-9
    assert set(s["blend"]) == set(studio.core.ASSETS)
    assert {x["name"] for x in s["sleeves"]} == {"trend", "momentum", "defensive"}
    assert all(x["why"] for x in s["sleeves"])
    assert s["recorded"]["controller"]["strategy"] == "blend"
    assert s["recorded"]["shadows"]["tmu"]["strategy"] == "cash"
    assert "bernoulli" not in s["recorded"]["shadows"]  # an errored shadow is not a signal
    assert len(s["recent_targets"]) > 20


def fake_account(equity, positions):
    return {"ok": True, "account": {"equity": equity, "status": "ACTIVE", "blocked": False},
            "positions": [{"symbol": k, "market_value": v} for k, v in positions.items()], "open_orders": []}


def test_plan_preview_sizes_to_the_cap_and_sells_first(home, monkeypatch):
    monkeypatch.setenv("POSITION_FRACTION", "0.25")
    monkeypatch.setattr(studio, "account_snapshot", lambda: fake_account(100_000, {"IWM": 10_000, "AAPL": 5_000}))
    monkeypatch.setattr(studio, "compute_signals", lambda: {
        "as_of": "2026-09-24", "blend": {"SPY": 1.0, "QQQ": 0.0, "IWM": 0.0, "TLT": 0.0},
        "recorded": {"controller": None, "shadows": {}}})
    p = studio.plan_preview()
    assert p["ok"] and p["target"] == {"SPY": 25_000.0}
    assert [t["side"] for t in p["trades"]] == ["sell", "buy"]
    assert p["trades"][0] == {"side": "sell", "symbol": "IWM", "notional": 10000.0, "close_all": True}
    assert p["ignored"] == ["AAPL"]
    assert all(t["symbol"] != "AAPL" for t in p["trades"])


def test_plan_preview_ignores_drift_inside_the_band(home, monkeypatch):
    monkeypatch.setattr(studio, "account_snapshot", lambda: fake_account(100_000, {"SPY": 25_300}))
    monkeypatch.setattr(studio, "compute_signals", lambda: {
        "as_of": "d", "blend": {"SPY": 1.0, "QQQ": 0.0, "IWM": 0.0, "TLT": 0.0}, "recorded": {"controller": None, "shadows": {}}})
    assert studio.plan_preview()["trades"] == []


def test_plan_preview_flags_disagreement_with_the_bot_record(home, monkeypatch):
    monkeypatch.setattr(studio, "account_snapshot", lambda: fake_account(100_000, {}))
    monkeypatch.setattr(studio, "compute_signals", lambda: {
        "as_of": "2026-09-24", "blend": {"SPY": 1.0, "QQQ": 0.0, "IWM": 0.0, "TLT": 0.0},
        "recorded": {"controller": {"as_of": "2026-09-24", "target_weights": {"TLT": 1.0}}, "shadows": {}}})
    assert studio.plan_preview()["agrees_with_bot_record"] is False


def test_plan_preview_still_reports_targets_when_the_account_is_unreachable(home, monkeypatch):
    monkeypatch.setattr(studio, "account_snapshot", lambda: {"ok": False, "error": "Alpaca answered HTTP 401."})
    p = studio.plan_preview()
    assert p["ok"] is False and "401" in p["error"]
    assert sum(p["target_fractions"].values()) <= 0.25 + 1e-9


# ---------------------------------------------------------------- the server refuses strangers
def make_handler(host, headers=None, path="/api/refresh-prices"):
    h = studio.Handler.__new__(studio.Handler)
    h.headers = {"Host": host, **(headers or {})}
    h.server = types.SimpleNamespace(server_address=("127.0.0.1", 8770))
    h.path = path
    h.sent = []
    h._send = lambda status, body, ctype: h.sent.append(status)
    return h


def test_only_loopback_hosts_are_accepted():
    assert studio.Handler._ok_host(make_handler("127.0.0.1:8770"))
    assert studio.Handler._ok_host(make_handler("localhost:8770"))
    assert not studio.Handler._ok_host(make_handler("evil.example:8770"))
    assert not studio.Handler._ok_host(make_handler("127.0.0.1:9999"))


def test_posts_need_the_custom_header_and_a_loopback_host(monkeypatch):
    called = []
    monkeypatch.setattr(studio, "refresh_prices", lambda: called.append(1) or {})
    for h in (make_handler("127.0.0.1:8770"),
              make_handler("evil.example:8770", {"X-Studio": "1"})):
        studio.Handler.do_POST(h)
        assert h.sent == [403]
    assert called == []


def test_unknown_routes_are_404():
    h = make_handler("127.0.0.1:8770", {"X-Studio": "1"}, path="/api/place-order")
    studio.Handler.do_POST(h)
    assert h.sent == [404]
    g = make_handler("127.0.0.1:8770", path="/api/place-order")
    studio.Handler.do_GET(g)
    assert g.sent == [404]


def test_backtest_and_price_data_never_leave_the_machine_by_design():
    src = open(studio.__file__, encoding="utf-8").read()
    assert 'ThreadingHTTPServer(("127.0.0.1", port)' in src
    assert "0.0.0.0" not in src
