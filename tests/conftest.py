import os
import socket
import pytest


@pytest.fixture(autouse=True)
def offline_sandbox(monkeypatch):
    """Tests cannot inherit production secrets or contact a broker."""
    for key in list(os.environ):
        if key.startswith(("ALPACA_", "INTRADAY_", "TIINGO_", "TT_")) or key == "ALERT_WEBHOOK_URL":
            monkeypatch.delenv(key, raising=False)
    def denied(*args, **kwargs):
        raise AssertionError("network disabled in offline tests")
    monkeypatch.setattr(socket.socket, "connect", denied)
