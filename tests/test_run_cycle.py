import json
import os
import time

import pytest

from tsetlin_trader.run_cycle import AlreadyRunning, load_dotenv, run, run_lock


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ALLOW_FRESH_STATE", raising=False)


def read_log(tmp_path):
    lines = (tmp_path / "results" / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def test_full_cycle_with_simulated_broker(tmp_path):
    result = run(broker_provider="simulated", signal_provider="mock")

    assert result["status"] == "executed"
    assert result["risk_decision"] in ("trade", "halt")
    assert "rule_trace" in result["signal"]
    assert len(read_log(tmp_path)) == 1
    assert (tmp_path / "results" / "state.json").exists()
    assert not (tmp_path / "results" / ".run.lock").exists()


def test_plan_only_places_no_orders_and_saves_no_state(tmp_path):
    result = run(broker_provider="simulated", signal_provider="mock", plan_only=True)

    assert result["status"] == "planned"
    assert result["orders"] == []
    assert not (tmp_path / "results" / "state.json").exists()


def test_rejects_unknown_broker():
    with pytest.raises(ValueError, match="BROKER_PROVIDER"):
        run(broker_provider="nope", signal_provider="mock")


def test_rejects_unknown_signal_provider():
    with pytest.raises(ValueError, match="SIGNAL_PROVIDER"):
        run(broker_provider="simulated", signal_provider="nope")


def test_skips_when_orders_are_pending(monkeypatch):
    from tsetlin_trader.broker.simulated_client import SimulatedBroker

    monkeypatch.setattr(SimulatedBroker, "open_order_symbols", lambda self: {"SPY"})
    result = run(broker_provider="simulated", signal_provider="mock")

    assert result["status"] == "skipped_open_orders_pending"
    assert result["pending_symbols"] == ["SPY"]


def test_second_concurrent_run_is_skipped(tmp_path):
    with run_lock():
        result = run(broker_provider="simulated", signal_provider="mock")
    assert result["status"] == "skipped_locked"
    assert read_log(tmp_path)[-1]["status"] == "skipped_locked"


def test_stale_lock_is_replaced(tmp_path):
    lock = tmp_path / "results" / ".run.lock"
    lock.parent.mkdir()
    lock.write_text("dead")
    old = time.time() - 3 * 60 * 60
    os.utime(lock, (old, old))

    with run_lock():
        pass  # no AlreadyRunning
    assert not lock.exists()


def test_fresh_lock_raises(tmp_path):
    with run_lock():
        with pytest.raises(AlreadyRunning):
            with run_lock():
                pass


def test_halt_liquidates_without_needing_a_signal(tmp_path, monkeypatch):
    """Safety must not depend on data or a model. A broken signal provider is fatal
    for trading but must not stop the breaker from going to cash."""
    from tsetlin_trader.broker.simulated_client import SimulatedBroker
    from tsetlin_trader.risk.manager import RiskManager
    from tsetlin_trader.signal import mock_provider

    def boom(self):
        raise RuntimeError("data source down")

    monkeypatch.setattr(mock_provider.MockSignalProvider, "get_current_signal", boom)

    state = tmp_path / "results" / "state.json"
    RiskManager(peak_equity=200_000).save_state(state)  # account at 100k => 50% drawdown

    original_init = SimulatedBroker.__init__

    def seeded(self, starting_cash=100_000.0):
        original_init(self, starting_cash)
        self._positions["SPY"] = 30_000
        self._cash = 70_000

    monkeypatch.setattr(SimulatedBroker, "__init__", seeded)

    result = run(broker_provider="simulated", signal_provider="mock")

    assert result["status"] == "executed"
    assert result["risk_decision"] == "halt"
    assert result["signal"] is None
    assert result["trades"] == [{"symbol": "SPY", "side": "sell", "notional": 30_000, "close_all": True}]


def test_alpaca_refuses_to_start_without_state(monkeypatch):
    from tsetlin_trader import run_cycle
    from tsetlin_trader.broker.simulated_client import SimulatedBroker

    monkeypatch.setattr(run_cycle, "build_broker", lambda name: SimulatedBroker())
    result = run(broker_provider="alpaca", signal_provider="mock")

    assert result["status"] == "refused_missing_state"


def test_alpaca_starts_fresh_when_explicitly_allowed(monkeypatch):
    from tsetlin_trader import run_cycle
    from tsetlin_trader.broker.simulated_client import SimulatedBroker

    monkeypatch.setattr(run_cycle, "build_broker", lambda name: SimulatedBroker())
    monkeypatch.setenv("ALLOW_FRESH_STATE", "1")
    result = run(broker_provider="alpaca", signal_provider="mock")

    assert result["status"] == "executed"


def test_positions_outside_universe_are_reported_and_kept(monkeypatch):
    from tsetlin_trader.broker.simulated_client import SimulatedBroker

    original_init = SimulatedBroker.__init__

    def seeded(self, starting_cash=100_000.0):
        original_init(self, starting_cash)
        self._positions["AAPL"] = 5_000
        self._cash = 95_000

    monkeypatch.setattr(SimulatedBroker, "__init__", seeded)
    result = run(broker_provider="simulated", signal_provider="mock")

    assert result["ignored_symbols"] == ["AAPL"]
    assert all(t["symbol"] != "AAPL" for t in result["trades"])


def test_load_dotenv_reads_values_but_never_overrides(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "# comment\nNEW_KEY=abc\nEXISTING=from_file\nQUOTED='hello'\n\nBAD LINE\n", encoding="utf-8"
    )
    monkeypatch.delenv("NEW_KEY", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)
    monkeypatch.setenv("EXISTING", "from_shell")

    load_dotenv(tmp_path / ".env")

    assert os.environ["NEW_KEY"] == "abc"
    assert os.environ["QUOTED"] == "hello"
    assert os.environ["EXISTING"] == "from_shell"


def test_shadow_signals_and_virtual_portfolios_are_logged(tmp_path, monkeypatch):
    from logic_alpha_tm.data import synthetic_prices

    import tsetlin_trader.signal.logic_alpha_provider as provider_module

    (tmp_path / "data").mkdir()
    synthetic_prices().rename_axis("date").to_csv(tmp_path / "data" / "tiingo-prices.csv")
    monkeypatch.setattr(provider_module, "MAX_DATA_AGE_DAYS", 10_000)
    monkeypatch.delenv("TIINGO_API_TOKEN", raising=False)
    monkeypatch.setenv("SIGNAL_MODEL", "logistic")
    monkeypatch.setenv("SHADOW_MODELS", "bernoulli,logistic,not_a_model")

    result = run(broker_provider="simulated", signal_provider="logic_alpha")

    shadow = result["shadow_signals"]
    assert "logistic" not in shadow
    assert shadow["bernoulli"]["strategy"] in {"trend", "momentum", "defensive", "cash"}
    assert "error" in shadow["not_a_model"]
    vp = result["virtual_portfolios"]
    assert set(vp["portfolios"]) == {"blend", "blend_filtered", "tm"}
    assert (tmp_path / "results" / "portfolios.json").exists()
