import json
from pathlib import Path

from tsetlin_trader import ops


def test_alert_is_a_noop_without_a_url(monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    assert ops.alert("hello") is False


def test_alert_posts_json(monkeypatch):
    captured = {}

    class Response:
        status = 204
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        return Response()

    monkeypatch.setattr(ops.urllib.request, "urlopen", fake_urlopen)
    assert ops.alert("halted", url="https://example.invalid/hook") is True
    assert captured["url"] == "https://example.invalid/hook"
    assert captured["body"]["content"] == "halted"


def test_alert_never_raises(monkeypatch):
    def broken(request, timeout):
        raise OSError("no network")

    monkeypatch.setattr(ops.urllib.request, "urlopen", broken)
    assert ops.alert("x", url="https://example.invalid/hook") is False


def test_fingerprint_hashes_data_and_records_config(tmp_path, monkeypatch):
    prices = tmp_path / "p.csv"
    prices.write_text("date,SPY\n2026-01-01,1\n", encoding="utf-8")
    monkeypatch.setenv("SIGNAL_MODEL", "tmu")

    fp = ops.fingerprint(prices)

    assert len(fp["prices_sha256"]) == 64
    assert fp["config"]["SIGNAL_MODEL"] == "tmu"
    assert fp["packages"]["numpy"]
    assert ops.fingerprint(Path("nope.csv"))["prices_sha256"] is None
