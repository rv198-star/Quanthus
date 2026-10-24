"""Normalize new opaque IDs through checked order/day and actual-fill edges.

The legacy financial graph normalizer remains unchanged. This opt-in adapter
adds two typed edges, without deleting fields, rounding values or sorting arrays.
Root coordinates are bijective handles; the legacy pass still hashes the full
order/fill payload, money, quantity, sequence and time behind every handle.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from typing import Any

from quanthus.backtest.contracts import MARKET_TIMEZONE
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_risk_entry_veto as legacy

VERSION = "p3-dated-share-business-graph-v1"
CARRY = "dated_share_residual_order_carried"


def business_bundle(artifacts: Any, coordinates: Any, **kwargs: Any) -> dict[str, str]:
    events = artifacts["engine-events.json"]
    if not any(
        "execution_slice_order_id" in e["payload"]
        or "partial_fill_ids" in e["payload"]
        or e["reason_code"] == CARRY
        for e in events
    ):
        return legacy.business_bundle(artifacts, coordinates, **kwargs)

    def instant(e: Any) -> datetime:
        at = datetime.fromisoformat(e["event_time"])
        if at.tzinfo is None:
            raise ValueError("DATED_GRAPH_UNKNOWN_TIMEZONE")
        return at

    def day(e: Any) -> str:
        return instant(e).astimezone(MARKET_TIMEZONE).date().isoformat()

    def slice_day(e: Any) -> str:
        # P2 creates an order at the earlier decision close, while its first
        # slice belongs to eligible_session. Later slice events are execution-time.
        if e["event_type"] == "order_created":
            return date.fromisoformat(e["payload"]["eligible_session"]).isoformat()
        return day(e)

    def definitions(kind: str, field: str) -> dict[str, Any]:
        rows = [e for e in events if e["event_type"] == kind]
        result = {e["payload"][field]: e for e in rows}
        if len(result) != len(rows):
            raise ValueError("DATED_GRAPH_DUPLICATE_DEFINITION")
        return result

    orders, fills = definitions("order_created", "order_id"), definitions("fill", "fill_id")
    roots: dict[str, str] = {}
    inverse_roots: dict[str, str] = {}

    def root(order: str) -> str:
        if order not in orders:
            raise ValueError("DATED_GRAPH_UNKNOWN_ORDER")
        if order not in roots:
            e, p = orders[order], orders[order]["payload"]
            if p.get("side") != "sell" or any(
                not isinstance(p.get(k), str) or not p[k] for k in ("unit_id", "security_code")
            ):
                raise ValueError("DATED_GRAPH_ROOT_IDENTITY")
            token = digest_value(
                [VERSION, p["unit_id"], p["security_code"], e["event_time"], e["sequence"]]
            )
            if inverse_roots.setdefault(token, order) != order:
                raise ValueError("DATED_GRAPH_ROOT_BIJECTION")
            roots[order] = token
        return roots[order]

    aliases: dict[str, tuple[str, str]] = {}
    inverse_aliases: dict[tuple[str, str], str] = {}
    tokens: dict[str, str] = {}
    for e in events:
        p = e["payload"]
        if "execution_slice_order_id" not in p:
            continue
        raw, order = p["execution_slice_order_id"], p.get("order_id")
        token = root(order)
        context = (token, slice_day(e))
        if (
            not isinstance(raw, str)
            or not raw
            or aliases.setdefault(raw, context) != context
            or inverse_aliases.setdefault(context, raw) != raw
            or (raw in orders and (raw != order or slice_day(e) != slice_day(orders[order])))
            or (e["event_type"] == "order_created" and raw != order)
        ):
            raise ValueError("DATED_GRAPH_SLICE_BIJECTION")
        tokens[raw] = "execution-slice:" + digest_value(context)

    def fill_token(identity: str, carry: Any) -> str:
        if identity not in fills:
            raise ValueError("DATED_GRAPH_UNKNOWN_FILL")
        e = fills[identity]
        p, c = e["payload"], carry["payload"]
        if (
            p.get("side") != "sell"
            or p.get("order_id") != c.get("order_id")
            or day(e) != day(carry)
            or instant(e) > instant(carry)
        ):
            raise ValueError("DATED_GRAPH_FILL_EDGE")
        return "dated-fill:" + digest_value([root(p["order_id"]), e["event_time"], e["sequence"]])

    normalized = deepcopy(artifacts)
    for original, target in zip(events, normalized["engine-events.json"], strict=True):
        p, out = original["payload"], target["payload"]
        if "execution_slice_order_id" in p:
            out["execution_slice_order_id"] = tokens[p["execution_slice_order_id"]]
        if "partial_fill_ids" not in p and original["reason_code"] != CARRY:
            continue
        references = p.get("partial_fill_ids")
        if original["reason_code"] != CARRY or not isinstance(references, list):
            raise ValueError("DATED_GRAPH_CARRY_REFERENCE")
        root(p.get("order_id"))
        expected = sorted(
            (
                e
                for e in fills.values()
                if e["payload"].get("order_id") == p["order_id"]
                and day(e) == day(original)
                and instant(e) <= instant(original)
            ),
            key=lambda e: (instant(e), e["sequence"]),
        )
        if (
            references != [e["payload"]["fill_id"] for e in expected]
            or type(p.get("quantity_filled")) is not int
            or p["quantity_filled"] != sum(e["payload"]["quantity"] for e in expected)
        ):
            raise ValueError("DATED_GRAPH_CARRY_FILL_CLOSURE")
        out["partial_fill_ids"] = [fill_token(identity, original) for identity in references]
    return legacy.business_bundle(normalized, coordinates, **kwargs)
