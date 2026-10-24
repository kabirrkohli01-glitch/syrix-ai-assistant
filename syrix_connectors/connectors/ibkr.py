"""Interactive Brokers Client Portal Gateway connector.

The Gateway must already be running and authenticated on loopback. SYRIX
never accepts a remote IBKR origin. Read tools default allowed after connect;
order placement defaults blocked and additionally requires per-call approval.
"""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from typing import Any, Dict, Tuple
from urllib.parse import urlparse

from .. import secure_store
from ..base import (
    HEALTH_AVAILABLE,
    HEALTH_CONNECTED,
    HEALTH_NEEDS_SETUP,
    PERM_EXECUTE,
    PERM_READ,
    RISK_CONFIRM,
    SCHEMA_VERSION,
    ActionResult,
    ConnectorAction,
    ConnectorBase,
    NeedsReauth,
)

_DEFAULT_ORIGIN = "https://127.0.0.1:5000/v1/api"


def _tool(name, description, props=None, required=()):
    return {
        "type": "function",
        "function": {
            "name": name, "description": description,
            "parameters": {
                "type": "object", "properties": dict(props or {}),
                "required": list(required), "additionalProperties": False,
            },
        },
    }


class IBKRConnector(ConnectorBase):
    id = "ibkr"
    name = "Interactive Brokers"
    description = "Read portfolio/account data; orders are blocked until enabled and confirmed"
    icon = "ibkr"
    category = "finance"
    auth_kind = "local_gateway"
    # Always open IBKR's real hosted Client Portal login.  The loopback API
    # gateway is checked separately below; it is not a public website and may
    # not be running on the user's machine.
    setup_url = "https://ndcdyn.interactivebrokers.com/sso/Login?RL=1&menu=A"
    schema_version = SCHEMA_VERSION

    def __init__(self):
        self._gateway_health_cached = HEALTH_NEEDS_SETUP
        self._gateway_health_checked_at = 0.0
        self._actions = OrderedDict((
            ("ibkr_accounts", ConnectorAction(
                "ibkr_accounts", "View accounts", "List authenticated IBKR accounts.",
                PERM_READ, tool_schema=_tool(
                    "ibkr_accounts", "List accounts from the local authenticated IBKR gateway."),
            )),
            ("ibkr_portfolio", ConnectorAction(
                "ibkr_portfolio", "View portfolio", "Read positions for one IBKR account.",
                PERM_READ, tool_schema=_tool(
                    "ibkr_portfolio", "Read current positions for an IBKR account.",
                    {"account_id": {"type": "string"}}, ("account_id",)),
            )),
            ("ibkr_summary", ConnectorAction(
                "ibkr_summary", "View account summary", "Read IBKR account balance summary.",
                PERM_READ, tool_schema=_tool(
                    "ibkr_summary", "Read balance and margin summary for an IBKR account.",
                    {"account_id": {"type": "string"}}, ("account_id",)),
            )),
            ("ibkr_live_orders", ConnectorAction(
                "ibkr_live_orders", "View live orders", "Read current IBKR orders.",
                PERM_READ, tool_schema=_tool(
                    "ibkr_live_orders", "Read live orders from the connected IBKR account."),
            )),
            ("ibkr_place_order", ConnectorAction(
                "ibkr_place_order", "Place order",
                "Place an IBKR order only when permission is enabled and this exact order is approved.",
                PERM_EXECUTE, risk=RISK_CONFIRM, timeout_s=20,
                tool_schema=_tool(
                    "ibkr_place_order", "Place one order through the local IBKR gateway.",
                    {
                        "account_id": {"type": "string"},
                        "conid": {"type": "integer", "description": "IBKR contract id."},
                        "side": {"type": "string", "enum": ["BUY", "SELL"]},
                        "quantity": {"type": "number", "exclusiveMinimum": 0},
                        "order_type": {"type": "string", "enum": ["MKT", "LMT"]},
                        "limit_price": {"type": "number", "exclusiveMinimum": 0},
                        "tif": {"type": "string", "enum": ["DAY", "GTC"]},
                    }, ("account_id", "conid", "side", "quantity", "order_type")),
            )),
        ))

    def actions(self):
        return self._actions

    def setup_hint(self) -> str:
        return (
            "Sign in through IBKR Client Portal. To let SYRIX read the account, "
            "also start IBKR Client Portal Gateway, then press Connect again."
        )

    def health(self, *, config: Dict[str, Any]) -> str:
        if not secure_store.available():
            return HEALTH_NEEDS_SETUP
        if secure_store.has_token(self.id):
            return HEALTH_CONNECTED
        # Before Connect, distinguish a running, authenticated gateway from
        # one that still needs its browser sign-in.  This keeps the first
        # click on Connect useful instead of attempting a request and simply
        # returning ``gateway_unavailable``.
        now = time.monotonic()
        if now - self._gateway_health_checked_at < 4.0:
            return self._gateway_health_cached
        try:
            import requests
            response = requests.get(
                self._origin(config) + "/iserver/auth/status",
                timeout=1.5, verify=False, headers={"User-Agent": "SYRIX-IBKR/1"},
            )
            payload = response.json() if response.ok else {}
            self._gateway_health_cached = (
                HEALTH_AVAILABLE if bool(payload.get("authenticated")) else HEALTH_NEEDS_SETUP
            )
        except Exception:
            self._gateway_health_cached = HEALTH_NEEDS_SETUP
        self._gateway_health_checked_at = now
        return self._gateway_health_cached

    def connect(self, *, config: Dict[str, Any], progress=None) -> Tuple[bool, str]:
        try:
            data = self._request("GET", "/iserver/auth/status", config=config)
            if not bool(data.get("authenticated")):
                return False, "gateway_not_authenticated"
            accounts = self._request("GET", "/portfolio/accounts", config=config)
            label = ""
            if isinstance(accounts, list) and accounts:
                label = str(accounts[0].get("accountId") or accounts[0].get("id") or "")
            if not secure_store.save_token(self.id, {"local_gateway_consent": True}):
                return False, "consent_store_failed"
            return True, (label[:4] + "…") if len(label) > 5 else (label or "Local Gateway")
        except Exception as exc:
            return False, f"gateway_unavailable:{type(exc).__name__}"

    def disconnect(self, *, config: Dict[str, Any]) -> None:
        secure_store.delete_token(self.id)

    def claims_query(self, text: str) -> bool:
        t = str(text or "")
        return bool(
            re.search(r"\b(?:ibkr|interactive\s+brokers|brokerage\s+account)\b", t, re.I)
            and re.search(r"\b(?:my|show|list|check|portfolio|positions?|balance|orders?|buy|sell)\b", t, re.I)
            and not re.search(r"\b(?:do\s+not|don['’]?t|never|hypothetical)\b", t, re.I)
        )

    def suggest_action(self, text: str):
        t = str(text or "")
        if not self.claims_query(t) or re.search(r"\b(?:buy|sell|place|submit)\b", t, re.I):
            return None
        if re.search(r"\b(?:orders?|trades?)\b", t, re.I):
            return "ibkr_live_orders", {}
        return "ibkr_accounts", {}

    def execute(self, action_key: str, args: Dict[str, Any], *,
                config: Dict[str, Any], request_id: str = "") -> ActionResult:
        if action_key == "ibkr_accounts":
            rows = self._request("GET", "/portfolio/accounts", config=config)
            return self._render("IBKR accounts", rows, ("accountId", "accountTitle", "type"))
        if action_key == "ibkr_portfolio":
            aid = self._account(args)
            rows = self._request("GET", f"/portfolio/{aid}/positions/0", config=config)
            return self._render("IBKR positions", rows,
                                ("conid", "contractDesc", "position", "mktPrice", "mktValue",
                                 "avgCost", "unrealizedPnl", "currency"))
        if action_key == "ibkr_summary":
            aid = self._account(args)
            data = self._request("GET", f"/portfolio/{aid}/summary", config=config)
            rows = []
            for key, value in (data.items() if isinstance(data, dict) else []):
                if isinstance(value, dict):
                    rows.append({"metric": key, "amount": value.get("amount"),
                                 "currency": value.get("currency")})
            return self._render("IBKR account summary", rows, ("metric", "amount", "currency"))
        if action_key == "ibkr_live_orders":
            data = self._request("GET", "/iserver/account/orders", config=config)
            rows = data.get("orders") if isinstance(data, dict) else []
            return self._render("IBKR live orders", rows,
                                ("orderId", "ticker", "side", "totalSize", "filledQuantity",
                                 "status", "orderType", "price"))
        if action_key == "ibkr_place_order":
            aid = self._account(args)
            order_type = str(args.get("order_type") or "").upper()
            payload = {
                "orders": [{
                    "conid": int(args.get("conid")),
                    "orderType": order_type,
                    "side": str(args.get("side") or "").upper(),
                    "quantity": float(args.get("quantity")),
                    "tif": str(args.get("tif") or "DAY").upper(),
                }]
            }
            if order_type == "LMT":
                price = float(args.get("limit_price") or 0)
                if price <= 0:
                    return ActionResult(False, error="missing_limit_price",
                                        human_message="A positive limit price is required.")
                payload["orders"][0]["price"] = price
            data = self._request("POST", f"/iserver/account/{aid}/orders",
                                 config=config, json=payload)
            # Some IBKR risk checks return a reply id requiring a second
            # confirmation. SYRIX never auto-confirms that second boundary.
            if isinstance(data, list) and data and data[0].get("id"):
                return ActionResult(
                    False, error="broker_confirmation_required",
                    human_message=(
                        "IBKR requires an additional broker-side confirmation. "
                        "Open Client Portal to review it; SYRIX will not auto-confirm."
                    ),
                )
            return self._render("IBKR order response", data,
                                ("order_id", "order_status", "encrypt_message"))
        return ActionResult(False, error="unknown_action", human_message="Unknown IBKR action.")

    @staticmethod
    def _account(args: Dict[str, Any]) -> str:
        account = str(args.get("account_id") or "").strip()
        if not account or not re.fullmatch(r"[A-Za-z0-9_-]{2,40}", account):
            raise ValueError("invalid_account_id")
        return account

    @staticmethod
    def _origin(config: Dict[str, Any]) -> str:
        origin = str(config.get("ibkr_gateway_url") or _DEFAULT_ORIGIN).rstrip("/")
        parsed = urlparse(origin)
        if parsed.scheme != "https" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("ibkr_gateway_must_be_loopback_https")
        return origin

    def _request(self, method: str, path: str, *, config: Dict[str, Any], **kwargs):
        import requests
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = requests.request(
            method, self._origin(config) + "/" + path.lstrip("/"),
            timeout=12, verify=False, headers={"User-Agent": "SYRIX-IBKR/1"}, **kwargs
        )
        if response.status_code in (401, 403):
            raise NeedsReauth()
        response.raise_for_status()
        return response.json() if response.content else {}

    @staticmethod
    def _render(title: str, payload: Any, fields: Tuple[str, ...]) -> ActionResult:
        rows = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
        rows = rows[:30]
        lines = [f"{title} ({len(rows)} item(s))"]
        for i, row in enumerate(rows, 1):
            if isinstance(row, dict):
                parts = [f"{field}: {str(row.get(field))[:200]}" for field in fields
                         if row.get(field) not in (None, "")]
                if parts:
                    lines.append(f"{i}. " + " | ".join(parts))
        return ActionResult(True, "\n".join(lines), count=len(rows))


__all__ = ["IBKRConnector"]
