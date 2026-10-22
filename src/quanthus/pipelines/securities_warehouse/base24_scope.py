"""Base24-only source projection; sealed monthly inputs and legacy gates are immutable."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

CONTRACT = "p10-base24-default-1d-native5m-v1"


class Base24ScopeError(ValueError):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _members(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    members = {}
    for member in document["content"]["files"]:
        path = member["path"]
        normalized = PurePosixPath(path)
        if (
            not isinstance(path, str)
            or normalized.is_absolute()
            or ".." in normalized.parts
            or normalized.as_posix() != path
            or path in members
            or len(member["sha256"].removeprefix("sha256:")) != 64
            or not isinstance(member["size_bytes"], int)
            or member["size_bytes"] < 0
        ):
            raise Base24ScopeError("INVALID_OR_DUPLICATE_MEMBER")
        members[path] = member
    return members


def select_default_members(
    monthly_bytes: bytes, seed_bytes: bytes, policy_bytes: bytes
) -> dict[str, Any]:
    """Freeze exact included/excluded members without opening data or old roots.

    Exclusion uses original dataset identity and explicit artifact ownership, never
    a broad filename substring. Shared calendar, acquisition, after-hours sources
    and original manifests remain included. Unknown 1m-lane artifacts fail closed.
    This is a source selection, not physical verification or consumer admission.
    """
    monthly, seed, policy = (json.loads(b) for b in (monthly_bytes, seed_bytes, policy_bytes))
    if (
        policy["contract_id"] != CONTRACT
        or policy["required_frequencies"] != ["1d", "5m"]
        or policy["optional_1m"] != "NOT_INCLUDED"
        or policy["derive_5m_from_1m"] is not False
        or policy["delete_retained_1m"] is not False
    ):
        raise Base24ScopeError("DEFAULT_PROFILE_MISMATCH")
    seed_sha = _digest(seed_bytes)
    inventories = monthly["content"]["source_inventory"]
    seeds = [i for i in inventories if i["role"] == "historical_seed"]
    if len(seeds) != 1 or seeds[0]["manifest_sha256"].removeprefix("sha256:") != seed_sha:
        raise Base24ScopeError("SEED_IDENTITY_MISMATCH")
    seed_members, members = _members(seed), _members(monthly)
    excluded_ids = set(policy["excluded_seed_dataset_ids"])
    excluded_names = set(policy["excluded_increment_artifact_names"])
    shared_names = set(policy["preserved_shared_1m_lane_artifact_names"])
    if excluded_names & shared_names:
        raise Base24ScopeError("CONFLICTING_ARTIFACT_OWNERSHIP")
    included, excluded = [], []
    for path, member in sorted(members.items()):
        reason = None
        if member["role"] == "historical_seed":
            original = seed_members.get(path)
            if (
                original is None
                or member["sha256"] != original["sha256"]
                or member["size_bytes"] != original["size_bytes"]
                or member["source_id"] != seed_sha
            ):
                raise Base24ScopeError("HISTORICAL_MEMBER_IDENTITY_MISMATCH")
            if original["dataset_id"] in excluded_ids:
                reason = "OPTIONAL_1M_DATASET_NOT_INCLUDED"
        elif member["role"] == "intraday_1m":
            name = PurePosixPath(path).name
            if name in excluded_names:
                reason = "OPTIONAL_1M_ARTIFACT_NOT_INCLUDED"
            elif name not in shared_names:
                raise Base24ScopeError("UNCLASSIFIED_1M_LANE_ARTIFACT")
        if "source_id" not in member and member["role"] != "local_governance_evidence":
            raise Base24ScopeError("SOURCE_IDENTITY_MISSING")
        record = {
            k: member[k]
            for k in ("path", "sha256", "size_bytes", "source_id", "role")
            if k in member
        }
        if reason:
            excluded.append(dict(record, disposition="NOT_INCLUDED", reason=reason))
        else:
            included.append(record)
    return dict(
        contract_id=CONTRACT,
        policy_sha256=_digest(policy_bytes),
        monthly_manifest_sha256=_digest(monthly_bytes),
        seed_manifest_sha256=seed_sha,
        included=included,
        excluded=excluded,
        included_logical_bytes=sum(m["size_bytes"] for m in included),
        excluded_logical_bytes=sum(m["size_bytes"] for m in excluded),
        optional_1m="NOT_INCLUDED",
        retained_sources_modified=False,
        source_qualification="NOT_GRANTED",
        consumer_admission="NOT_GRANTED",
    )
