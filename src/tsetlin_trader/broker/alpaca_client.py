"""Minimal Alpaca paper REST client with no pandas or alpaca-py dependency."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal

from .base import AccountSnapshot, BrokerClient, OrderResult

PAPER_BASE = "https://paper-api.alpaca.markets"


class AlpacaHTTPError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Alpaca paper API HTTP {status_code}: {message}")
        self.status_code = status_code


class PaperREST:
    """Small injectable transport, permanently bound to Alpaca's paper host."""
    def __init__(self, api_key: str, secret_key: str, timeout_s: float = 15.0) -> None:
        if not api_key or not secret_key:
            raise ValueError("Alpaca paper API credentials are required")
        self._headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key,
                         "Accept": "application/json", "User-Agent": "tsetlin-trader/1"}
        self._timeout_s = timeout_s

    def request(self, method: str, path: str, params: dict | None = None,
                body: dict | None = None):
        if not path.startswith("/v2/"):
            raise ValueError("only Alpaca v2 paper endpoints are allowed")
        url = PAPER_BASE + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = dict(self._headers)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                detail = str(exc.reason)
            raise AlpacaHTTPError(exc.code, detail[:500]) from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach Alpaca paper API: {exc.reason}") from None


class AlpacaClient(BrokerClient):
    """Paper-only broker client using JSON REST and no compiled dependencies."""
    def __init__(self, api_key: str, secret_key: str, transport=None,
                 poll_s: float = 2.0) -> None:
        self._poll_s = poll_s
        self._rest = transport or PaperREST(api_key, secret_key)

    def get_account(self) -> AccountSnapshot:
        a = self._rest.request("GET", "/v2/account")
        return AccountSnapshot(float(a["equity"]), float(a["cash"]), float(a["buying_power"]))

    def get_positions(self) -> dict[str, float]:
        return {p["symbol"]: float(p["market_value"])
                for p in self._rest.request("GET", "/v2/positions")}

    def open_order_symbols(self) -> set[str]:
        orders = self._rest.request("GET", "/v2/orders", {"status": "open", "limit": 500})
        return {o["symbol"] for o in orders}

    def is_trading_day(self, day: date) -> bool:
        return bool(self._rest.request("GET", "/v2/calendar",
                                       {"start": day.isoformat(), "end": day.isoformat()}))

    def is_market_open(self) -> bool:
        return bool(self._rest.request("GET", "/v2/clock")["is_open"])

    def is_full_trading_day(self, day: date) -> bool:
        days = self._rest.request("GET", "/v2/calendar",
                                  {"start": day.isoformat(), "end": day.isoformat()})
        if not days:
            return False
        try:
            return int(str(days[0].get("close", "")).split(":", 1)[0]) >= 16
        except (ValueError, IndexError):
            return False

    def cancel_order(self, order_id: str) -> None:
        self._rest.request("DELETE", f"/v2/orders/{urllib.parse.quote(order_id, safe='')}")

    def wait_for_open_orders(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while self.open_order_symbols():
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._poll_s)
        return True

    def get_order_status(self, order_id: str) -> str:
        return str(self._rest.request("GET", f"/v2/orders/{urllib.parse.quote(order_id, safe='')}")["status"])

    def account_identity(self) -> str:
        return str(self._rest.request("GET", "/v2/account")["id"])

    @staticmethod
    def _result(order: dict, fallback_notional: float = 0.0,
                fallback_side: str = "") -> OrderResult:
        return OrderResult(str(order.get("symbol", "")),
                           float(order.get("notional") or fallback_notional or 0),
                           str(order.get("side") or fallback_side).lower(),
                           str(order.get("status", "submitted")),
                           str(order.get("id", "")) or None,
                           order.get("client_order_id"))

    def find_order(self, client_order_id: str) -> OrderResult | None:
        try:
            order = self._rest.request("GET", "/v2/orders:by_client_order_id",
                                       {"client_order_id": client_order_id})
        except AlpacaHTTPError as exc:
            if exc.status_code == 404:
                return None
            raise
        return self._result(order)

    def submit_order(self, symbol: str, notional: float, side: str,
                     client_order_id: str | None = None) -> OrderResult:
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        notional = round(notional, 2)
        if notional < 1.0:
            return OrderResult(symbol, 0.0, side, "skipped_below_minimum_notional", None, client_order_id)
        body = {"symbol": symbol, "notional": str(notional), "side": side,
                "type": "market", "time_in_force": "day"}
        if client_order_id:
            body["client_order_id"] = client_order_id
        try:
            order = self._rest.request("POST", "/v2/orders", body=body)
        except Exception:
            if client_order_id:
                recovered = self.find_order(client_order_id)
                if recovered is not None:
                    return recovered
            raise
        return self._result(order, notional, side)

    def close_position(self, symbol: str) -> OrderResult:
        order = self._rest.request("DELETE", f"/v2/positions/{urllib.parse.quote(symbol, safe='')}")
        return self._result(order, fallback_side="sell")

    def close_position_idempotent(self, symbol: str, client_order_id: str) -> OrderResult:
        existing = self.find_order(client_order_id)
        if existing is not None:
            return existing
        position = self._rest.request("GET", f"/v2/positions/{urllib.parse.quote(symbol, safe='')}")
        quantity = Decimal(str(position["qty"]))
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError("only a positive long position can be closed")
        body = {"symbol": symbol, "qty": str(quantity), "side": "sell", "type": "market",
                "time_in_force": "day", "client_order_id": client_order_id}
        try:
            order = self._rest.request("POST", "/v2/orders", body=body)
        except Exception:
            existing = self.find_order(client_order_id)
            if existing is not None:
                return existing
            raise
        return self._result(order, fallback_side="sell")
