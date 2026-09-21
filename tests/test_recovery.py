from datetime import timedelta
from types import SimpleNamespace
import json
import pytest
from tsetlin_trader.live_paper import Runner
from tsetlin_trader.watchdog import problems
from tsetlin_trader.storage import atomic_json, process_lock, AlreadyRunning
from tests.test_live_paper import FakeBroker, at


def pending_intent(tmp_path, broker):
    path = tmp_path / "state.json"
    runner = Runner("paper", path, broker=broker, token="private")
    runner.state.update(day="2026-09-21", entry_intent=True,
                        entry_client_id="tt-intraday-2026-09-21-SPY-buy")
    runner._save()
    return path


def test_crash_after_broker_acceptance_recovers_and_exits(tmp_path):
    broker = FakeBroker()
    path = pending_intent(tmp_path, broker)
    broker.positions = {"SPY": 5000}
    broker.find_order = lambda cid: SimpleNamespace(order_id="entry1", status="filled", client_order_id=cid)
    runner = Runner("paper", path, broker=broker, token="private")
    runner.tick(at(15, 45))
    assert runner.state["managed_position"]
    assert broker.orders == [("SPY", 0, "sell", None)]


def test_unresolved_intent_never_resubmits_or_resets_next_day(tmp_path):
    broker = FakeBroker()
    runner = Runner("paper", pending_intent(tmp_path, broker), broker=broker, token="private")
    runner.tick(at(10, 35, day=22))
    assert runner.state["entry_intent"]
    assert runner.state["paused"]
    assert "unresolved" in runner.state["last_error"]
    assert not broker.orders


def test_previous_day_recovery_preserves_order_references(tmp_path):
    broker = FakeBroker()
    path = pending_intent(tmp_path, broker)
    broker.positions = {"SPY": 5000}
    broker.find_order = lambda cid: SimpleNamespace(order_id="entry1", status="filled")
    runner = Runner("paper", path, broker=broker, token="private")
    runner.tick(at(9, 40, day=22))
    assert runner.state["entry_order"]["order_id"] == "entry1"
    assert runner.state["paused"]
    assert broker.orders[-1][2] == "sell"


def test_restarted_runner_is_paused_and_account_bound(tmp_path):
    broker = FakeBroker()
    path = tmp_path / "state.json"
    r = Runner("paper", path, broker=broker, token="private")
    r.control("resume", "private", at(10, 0))
    assert Runner("paper", path, broker=broker, token="private").state["paused"]
    broker.account_identity = lambda: "different-account"
    with pytest.raises(ValueError, match="another paper account"):
        Runner("paper", path, broker=broker, token="private")


def test_loss_latch_cannot_be_resumed(tmp_path):
    r = Runner("paper", tmp_path / "state.json", broker=FakeBroker(), token="private")
    r.state["loss_limit_hit"] = True
    with pytest.raises(ValueError, match="latch"):
        r.control("resume", "private")


def test_late_training_cannot_submit_order(tmp_path, monkeypatch):
    import pandas as pd
    broker = FakeBroker()
    r = Runner("paper", tmp_path / "state.json", broker=broker, token="private",
               data_client=lambda *a: pd.DataFrame(), clock_source=lambda: at(10, 36))
    monkeypatch.setenv("INTRADAY_ALPACA_API_KEY", "test")
    monkeypatch.setenv("INTRADAY_ALPACA_SECRET_KEY", "test")
    monkeypatch.setattr("tsetlin_trader.live_paper.decide", lambda *a: {"action": "buy_SPY"})
    r.control("resume", "private", at(10, 35))
    r.tick(at(10, 35))
    assert not broker.orders
    assert "deadline" in r.state["events"][-1]["message"]


def test_watchdog_stale_error_and_late_position():
    now = at(16, 0)
    state = {"updated_at": (now-timedelta(minutes=3)).isoformat(),
             "last_error": "timeout", "managed_position": True}
    assert len(problems(state, now)) == 3
    assert problems({"updated_at": now.isoformat()}, now) == []


def test_atomic_json_rejects_nonfinite_without_destroying_previous(tmp_path):
    path = tmp_path / "state.json"
    atomic_json(path, {"value": 1})
    with pytest.raises(ValueError):
        atomic_json(path, {"value": float("nan")})
    assert json.loads(path.read_text()) == {"value": 1}


def test_kernel_lock_released_after_error(tmp_path):
    path = tmp_path / "runner.lock"
    with pytest.raises(RuntimeError):
        with process_lock(path):
            with pytest.raises(AlreadyRunning):
                with process_lock(path):
                    pass
            raise RuntimeError("crash")
    with process_lock(path):
        pass


def test_exit_timeout_recovers_without_second_close(tmp_path):
    broker = FakeBroker()
    path = pending_intent(tmp_path, broker)
    broker.positions = {"SPY": 5000}
    broker.find_order = lambda cid: SimpleNamespace(order_id="entry1" if "buy" in cid else "exit1", status="filled")
    r = Runner("paper", path, broker=broker, token="private")
    r.state.update(exit_intent=True, exit_client_id="tt-exit-2026-09-21-0")
    r._save()
    broker.positions = {}
    restarted = Runner("paper", path, broker=broker, token="private")
    restarted.tick(at(15, 46))
    assert restarted.state["exit_order"]["order_id"] == "exit1"
    assert not broker.orders


def test_rejected_exit_retries_with_new_identity(tmp_path):
    broker = FakeBroker()
    path = pending_intent(tmp_path, broker)
    broker.find_order = lambda cid: SimpleNamespace(order_id="entry1", status="filled")
    broker.positions = {"SPY": 5000}
    broker.get_order_status = lambda oid: "rejected" if oid == "bad-exit" else "filled"
    r = Runner("paper", path, broker=broker, token="private")
    r.state.update(exit_order={"order_id": "bad-exit", "status": "rejected"})
    r.tick(at(15, 46))
    assert r.state["exit_client_id"].endswith("-1")
    assert len(broker.orders) == 1


def test_process_lock_survives_file_age_and_releases_on_process_death(tmp_path):
    import subprocess
    import sys
    lock = tmp_path / "child.lock"
    script = "from pathlib import Path; from tsetlin_trader.storage import process_lock; import sys\nwith process_lock(Path(sys.argv[1])):\n print('locked', flush=True)\n sys.stdin.read()\n"
    child = subprocess.Popen([sys.executable, "-c", script, str(lock)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(AlreadyRunning):
            with process_lock(lock):
                pass
    finally:
        child.terminate()
        child.wait(timeout=10)
        child.stdin.close()
        child.stdout.close()
    with process_lock(lock):
        pass


def test_canceled_partial_entry_still_exits_filled_position(tmp_path):
    broker = FakeBroker()
    path = pending_intent(tmp_path, broker)
    broker.positions = {"SPY": 1200}
    broker.find_order = lambda cid: SimpleNamespace(order_id="entry1", status="canceled")
    broker.get_order_status = lambda oid: "canceled"
    r = Runner("paper", path, broker=broker, token="private")
    r.tick(at(15, 45))
    assert broker.orders[-1][2] == "sell"


def test_flatten_survives_closed_market_until_next_open(tmp_path):
    broker = FakeBroker()
    path = pending_intent(tmp_path, broker)
    broker.positions = {"SPY": 5000}
    broker.find_order = lambda cid: SimpleNamespace(order_id="entry1", status="filled")
    broker.is_market_open = lambda: False
    r = Runner("paper", path, broker=broker, token="private")
    r.tick(at(16, 5))
    assert not broker.orders
    assert r.state["flatten_requested"]
    broker.is_market_open = lambda: True
    r.tick(at(9, 35, day=22))
    assert broker.orders[-1][2] == "sell"


def test_broker_lookup_outage_pauses_without_orders(tmp_path):
    broker = FakeBroker()
    path = pending_intent(tmp_path, broker)
    def timeout(cid):
        raise TimeoutError("offline")
    broker.find_order = timeout
    r = Runner("paper", path, broker=broker, token="private")
    r.tick(at(10, 35))
    assert r.state["paused"] and "TimeoutError" in r.state["last_error"]
    assert not broker.orders
