import json

import pytest

from tsetlin_trader.run_cycle import run


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


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


def test_load_dotenv_reads_values_but_never_overrides(tmp_path, monkeypatch):
    from tsetlin_trader.run_cycle import load_dotenv

    (tmp_path / ".env").write_text(
        "# comment\nNEW_KEY=abc\nEXISTING=from_file\nQUOTED='hello'\n\nBAD LINE\n", encoding="utf-8"
    )
    monkeypatch.delenv("NEW_KEY", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)
    monkeypatch.setenv("EXISTING", "from_shell")

    load_dotenv(tmp_path / ".env")

    import os

    assert os.environ["NEW_KEY"] == "abc"
    assert os.environ["QUOTED"] == "hello"
    assert os.environ["EXISTING"] == "from_shell"
