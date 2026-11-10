"""Bounded action-supplement admission without implicit supplier arbitration.

Only key/clock metadata is indexed. Incoming actions are complete observations,
not patches to individual amounts. Historical values remain physical versions;
different IDs at one economic coordinate require evidence, never blind addition.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import _clock

CONTRACT_ID = "p10-base23-native-action-supplement-merge-v1"
MAX_ACTION_METADATA_ROWS = 250_000


def _key(row: dict[str, Any]) -> tuple[str, datetime]:
    return str(row["corporate_action_id"]), _clock(row["effective_at"])


def _coordinate(row: dict[str, Any]) -> tuple[str, datetime]:
    # A mixed plan and a cash plan on the same date can describe the same action.
    # A different action_type is insufficient evidence for two economic events.
    return str(row["security_id"]), _clock(row["effective_at"])


def classify_actions(
    historical: Iterable[dict[str, Any]], incoming: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Freeze exact admissible action keys and sparse old-version closures.

    Locations are metadata identities supplied by the bounded physical reader.
    Holds affect only the incoming event and its complete term set. They never
    rewrite old amounts, infer historical acquisition, or remove old actions.
    """
    old_by_key: dict[tuple[str, datetime], list[dict[str, Any]]] = defaultdict(list)
    old_by_coordinate: dict[tuple[str, datetime], set[str]] = defaultdict(set)
    count = 0
    for row in historical:
        count += 1
        if count > MAX_ACTION_METADATA_ROWS:
            raise BoundedError("ACTION_MERGE_REGISTERED_METADATA_BOUND_EXCEEDED")
        old_by_key[_key(row)].append(row)
        old_by_coordinate[_coordinate(row)].add(str(row["corporate_action_id"]))
    new_by_key: dict[tuple[str, datetime], dict[str, Any]] = {}
    new_by_coordinate: dict[tuple[str, datetime], set[str]] = defaultdict(set)
    for row in incoming:
        count += 1
        if count > MAX_ACTION_METADATA_ROWS:
            raise BoundedError("ACTION_MERGE_REGISTERED_METADATA_BOUND_EXCEEDED")
        key = _key(row)
        if key in new_by_key:
            raise BoundedError("ACTION_MERGE_MULTIPLE_INCOMING_OBSERVATIONS")
        if row.get("knowledge_to") is not None or not row.get("record_source"):
            raise BoundedError("ACTION_MERGE_INCOMING_OBSERVATION_UNQUALIFIED")
        _clock(row["knowledge_from"])
        new_by_key[key] = row
        new_by_coordinate[_coordinate(row)].add(key[0])
    accepted = []
    held = []
    closures = []
    reasons: Counter[str] = Counter()
    for key, row in sorted(new_by_key.items()):
        coordinate = _coordinate(row)
        prior = old_by_key[key]
        start = _clock(row["knowledge_from"])
        reason = None
        if (old_by_coordinate[coordinate] | new_by_coordinate[coordinate]) - {key[0]}:
            reason = "DIFFERENT_ACTION_ID_SAME_ECONOMIC_COORDINATE_NO_PRIORITY"
        elif any(p["record_source"] != row["record_source"] for p in prior):
            reason = "SAME_ACTION_ID_DIFFERENT_PROVIDER_NO_PRIORITY"
        elif any(_clock(p["knowledge_from"]) >= start for p in prior):
            reason = "INCOMING_KNOWLEDGE_NOT_STRICTLY_NEW"
        elif any(p["knowledge_to"] is not None and _clock(p["knowledge_to"]) > start
                 for p in prior):
            reason = "IMMUTABLE_CLOSED_HISTORY_OVERLAPS_NEW_OBSERVATION"
        elif sum(p["knowledge_to"] is None for p in prior) > 1:
            raise BoundedError("ACTION_MERGE_MULTIPLE_OPEN_HISTORICAL_VERSIONS")
        identity = {"corporate_action_id": key[0], "effective_at": key[1]}
        if reason:
            held.append({**identity, "reason": reason})
            reasons[reason] += 1
            continue
        accepted.append({**identity, "knowledge_from": start,
                         "record_source": row["record_source"]})
        for old in prior:
            if old["knowledge_to"] is None:
                closures.append({"path": old["path"], "ordinal": old["ordinal"],
                                 "successor": start, **identity})
    result = {"contract_id": CONTRACT_ID, "historical_rows": count-len(new_by_key),
              "incoming_rows": len(new_by_key), "accepted": accepted, "held": held,
              "closures": closures, "hold_reasons": dict(sorted(reasons.items())),
              "consumer_admission": "NOT_GRANTED", "provider_acquisition_inferred": False}
    result["identity"] = digest(result)
    return result


def term_closures(
    historical: Iterable[dict[str, Any]], admission: dict[str, Any],
) -> list[dict[str, Any]]:
    """Close the whole old term observation, including a removed term role."""
    accepted = {_key(row): row for row in admission["accepted"]}
    result = []
    count = 0
    for row in historical:
        count += 1
        if count > 400_000:
            raise BoundedError("ACTION_MERGE_REGISTERED_TERM_METADATA_BOUND_EXCEEDED")
        next_row = accepted.get(_key(row))
        if next_row is None:
            continue
        start = next_row["knowledge_from"]
        if (row["record_source"] != next_row["record_source"]
                or _clock(row["knowledge_from"]) >= start
                or (row["knowledge_to"] is not None and _clock(row["knowledge_to"]) > start)):
            raise BoundedError("ACTION_MERGE_OLD_TERM_SOURCE_OR_INTERVAL_CONFLICT")
        if row["knowledge_to"] is None:
            result.append({"path": row["path"], "ordinal": row["ordinal"],
                           "successor": start})
    return result
