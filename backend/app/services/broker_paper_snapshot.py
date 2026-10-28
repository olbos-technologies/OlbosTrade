"""Bounded, ephemeral paper-account reads. Not reconciliation or order authority.

Account identity is verified before reading positions/orders. Independent section
failures stay unknown. Account/token/tenant changes invalidate the entire response
through the same conditional health write used by connection verification.
"""
import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx

from app.services import broker_connection_health as health, credential_cipher

BASE = "https://paper-api.alpaca.markets"
MAX_BYTES = 2 * 1024 * 1024
MAX_POSITIONS = 10000
ORDER_LIMIT = 500
COLLECTION_TIMEOUT = 20


class ReadUnavailable(Exception):
    pass


class AccessRejected(Exception):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


async def read(client, path, token):
    assert path in ("/v2/account", "/v2/positions", "/v2/orders")
    params = {"status": "open", "limit": ORDER_LIMIT, "nested": "true"} if path == "/v2/orders" else None
    try:
        async with client.stream("GET", BASE + path, params=params,
                headers={"Authorization": "Bearer " + token}) as response:
            if response.status_code in (401, 403):
                raise AccessRejected()
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ReadUnavailable()
                chunks.append(chunk)
        return json.loads(b"".join(chunks), object_pairs_hook=_object)
    except (httpx.HTTPError, ValueError, TypeError):
        raise ReadUnavailable() from None


def number(value, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= 80:
        raise ValueError()
    try:
        n = Decimal(value)
        if not n.is_finite() or not -18 <= n.as_tuple().exponent <= 18 or abs(n) >= Decimal("1e18"):
            raise ValueError()
        return format(n, "f")
    except InvalidOperation:
        raise ValueError() from None


def symbol(value, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9./ _-]{0,47}", value):
        raise ValueError()
    return value


def balances(data):
    currency = data["currency"]
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError()
    return {"currency": currency, **{key: number(data[key]) for key in ("equity", "cash", "buying_power")}}


def positions(data):
    if not isinstance(data, list) or len(data) > MAX_POSITIONS:
        raise ValueError()
    rows, seen = [], set()
    for row in data:
        asset_id = str(uuid.UUID(row["asset_id"]))
        if asset_id in seen or row["side"] not in ("long", "short"):
            raise ValueError()
        seen.add(asset_id)
        qty = number(row["qty"])
        if Decimal(qty) == 0:
            raise ValueError()
        rows.append({"asset_id": asset_id, "symbol": symbol(row["symbol"]), "qty": qty,
            "side": row["side"], **{key: number(row.get(key), optional=True)
                for key in ("avg_entry_price", "current_price", "market_value", "unrealized_pl")}})
    return rows


ORDER_STATUSES = {"new", "partially_filled", "filled", "done_for_day", "canceled", "expired",
    "replaced", "pending_cancel", "pending_replace", "accepted", "pending_new",
    "accepted_for_bidding", "stopped", "rejected", "suspended", "calculated", "held"}


def orders(data):
    if not isinstance(data, list) or len(data) > ORDER_LIMIT:
        raise ValueError()
    seen = set()

    def one(row, depth=0):
        if depth > 2 or len(seen) >= 2000:
            raise ValueError()
        oid = str(uuid.UUID(row["id"]))
        if oid in seen:
            raise ValueError()
        seen.add(oid)
        side = row.get("side")
        if side not in (None, "buy", "sell"):
            raise ValueError()
        kind = row.get("type")
        legs = row.get("legs")
        if legs is not None and (not isinstance(legs, list) or len(legs) > 32):
            raise ValueError()
        return {"id": oid, "symbol": symbol(row.get("symbol"), optional=True), "side": side,
            "type": kind if kind in ("market", "limit", "stop", "stop_limit", "trailing_stop") else "unknown",
            "status": row.get("status") if row.get("status") in ORDER_STATUSES else "unknown",
            **{key: number(row.get(key), optional=True) for key in ("qty", "filled_qty", "limit_price", "stop_price", "notional")},
            "legs": [one(leg, depth + 1) for leg in legs] if legs is not None else None}
    return [one(row) for row in data]


def unavailable(reason="read_failed"):
    return {"state": "unavailable", "data": None, "reason": reason}


def section(parser, payload):
    if isinstance(payload, ReadUnavailable):
        return unavailable()
    try:
        return {"state": "available", "data": parser(payload), "reason": None}
    except (ValueError, KeyError, TypeError, AttributeError):
        return unavailable("invalid_broker_data")


async def snapshot(db, organization_id, grant_id):
    grant = await health.load_grant(db, organization_id, grant_id)
    started = datetime.now(timezone.utc)
    result = {"connection_id": str(grant.id), "broker": "alpaca", "environment": "paper",
        "account_last4": grant.account_id[-4:], "collection_started_at": started.isoformat(),
        "snapshot_atomic": False, "execution_enabled": False, "positions_reconciled": False,
        "balances": unavailable(), "positions": unavailable(), "open_orders": unavailable()}
    label = "unavailable"
    try:
        token = credential_cipher.decrypt(grant.ciphertext)
        async with asyncio.timeout(COLLECTION_TIMEOUT):
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                account = await read(client, "/v2/account", token)
                label = health.account_health(account, grant.account_id)
                if label in ("verified", "restricted"):
                    result["balances"] = section(balances, account)
                    reads = await asyncio.gather(read(client, "/v2/positions", token),
                        read(client, "/v2/orders", token), return_exceptions=True)
                    if any(isinstance(item, AccessRejected) for item in reads):
                        raise AccessRejected()
                    result["positions"] = section(positions, reads[0])
                    result["open_orders"] = section(orders, reads[1])
                    if result["open_orders"]["state"] == "available" and len(reads[1]) == ORDER_LIMIT:
                        result["open_orders"].update(state="incomplete", reason="order_limit_reached")
                    # A broker-side paper reset can change the account behind a
                    # still-present token without changing our local grant.
                    final_account = await read(client, "/v2/account", token)
                    label = health.account_health(final_account, grant.account_id)
                    if label not in ("verified", "restricted"):
                        for key in ("balances", "positions", "open_orders"):
                            result[key] = unavailable("account_not_verified")
    except AccessRejected:
        label = "reconnect_required"
        for key in ("balances", "positions", "open_orders"):
            result[key] = unavailable("broker_access_rejected")
    except (ReadUnavailable, TimeoutError, ValueError, credential_cipher.CredentialCipherUnavailable,
            credential_cipher.CredentialDecryptionError):
        label = "unavailable"
        # A collection timeout invalidates the whole retrieval, including earlier sections.
        for key in ("balances", "positions", "open_orders"):
            result[key] = unavailable()
    check = await health.record_health(db, grant, label)
    result.update({key: check[key] for key in ("health", "last_checked_at", "status")})
    result["collection_completed_at"] = datetime.now(timezone.utc).isoformat()
    return result
