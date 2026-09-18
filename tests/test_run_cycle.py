import json

from tsetlin_trader.run_cycle import run


def test_full_cycle_with_simulated_broker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = run(broker_provider="simulated")

    assert result["broker_provider"] == "simulated"
    assert result["risk_decision"] in ("trade", "halt")
    assert "signal" in result
    assert "rule_trace" in result["signal"]

    log_path = tmp_path / "results" / "decisions.jsonl"
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["broker_provider"] == "simulated"


def test_rejects_unknown_broker_provider(tmp_path, monkeypatch):
    import pytest

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        run(broker_provider="unknown_broker")
