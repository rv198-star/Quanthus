"""Retained action-inventory coverage is independent of market-bar availability.

This carries the existing P10 retrospective claim, not universal historical
completeness. Missing bars never manufacture a price or transaction permission.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

VERSION = "p10-retained-action-inventory-coverage.v1"
CLAIM = (
    "absence in the exact retained implemented-action inventory; unknown or incomplete "
    "groups block their entire security; not universal historical completeness"
)


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def validate(
    value: Mapping[str, Any],
    *,
    support_sha256: str,
    actions_sha256: str,
    information_cutoff: datetime,
    securities: Iterable[str] | None = None,
) -> dict[str, Any]:
    manifest_text = value["support_manifest_json"]
    if _sha(manifest_text) != support_sha256:
        raise ValueError("ACTION_COVERAGE_SUPPORT_IDENTITY")
    manifest = json.loads(manifest_text)
    for key, member in (
        ("support_plan_json", "plan.json"),
        ("exceptions_json", "action-exceptions.json"),
    ):
        raw = value[key]
        expected = manifest["files"][member]
        if _sha(raw) != expected["sha256"] or len(raw.encode()) != expected["bytes"]:
            raise ValueError("ACTION_COVERAGE_SOURCE_MEMBER")
    plan, exceptions = json.loads(value["support_plan_json"]), json.loads(value["exceptions_json"])
    universe = value["security_ids"]
    unknown = sorted({x["security_id"] for x in exceptions})
    dates = plan["trading_dates"]
    if (
        value.get("schema_version") != VERSION
        or not isinstance(universe, list)
        or not universe
        or any(not isinstance(x, str) or not x for x in universe)
        or universe != sorted(set(universe))
        or plan["no_action_claim"] != CLAIM
        or plan["historical_pit_granted"] is not False
        or plan["information_cutoff"] != information_cutoff.isoformat()
        or manifest["files"]["actions.json"]["sha256"] != actions_sha256
        or value["unqualified_security_ids"] != unknown
        or value["business_from"] != dates[0]
        or value["business_through"] != dates[-1]
        or (securities is not None and universe != sorted(set(securities)))
    ):
        raise ValueError("ACTION_COVERAGE_SCOPE_OR_CLAIM")
    date.fromisoformat(value["business_from"])
    date.fromisoformat(value["business_through"])
    return dict(value)


def freeze(
    *,
    manifest_json: str,
    plan_json: str,
    exceptions_json: str,
    securities: Iterable[str],
    support_sha256: str,
    actions_sha256: str,
    information_cutoff: datetime,
) -> dict[str, Any]:
    plan, exceptions = json.loads(plan_json), json.loads(exceptions_json)
    value = {
        "schema_version": VERSION,
        "support_manifest_json": manifest_json,
        "support_plan_json": plan_json,
        "exceptions_json": exceptions_json,
        "security_ids": sorted(set(securities)),
        "unqualified_security_ids": sorted({x["security_id"] for x in exceptions}),
        "business_from": plan["trading_dates"][0],
        "business_through": plan["trading_dates"][-1],
    }
    return validate(
        value,
        support_sha256=support_sha256,
        actions_sha256=actions_sha256,
        information_cutoff=information_cutoff,
        securities=value["security_ids"],
    )
