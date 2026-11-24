"""P10 daily metadata sidecars: explicit clocks, bound receipts and append-only events.

No source files, network, CURRENT pointers or consumer receipts are produced here.
Existing source verification supplies an identity-bound receipt; payloads are never rehashed.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

SCHEMA = "quanthus.p10.daily-consumer-sidecar.v1"
CONFIG_SCHEMA = "quanthus.p10.daily-consumer-config.v1"
BOUNDED_CONFIG_SCHEMA = "quanthus.p10.daily-consumer-config.v1.1"
M0_CONFIG_SCHEMA = "quanthus.p10.daily-consumer-config.v1.2"
SESSION_SCHEMA = "quanthus.p10.daily-consumer-sidecar.v2"
SESSION_CONFIG_SCHEMA = "quanthus.p10.daily-consumer-config.v2"
SESSION_BOUNDED_CONFIG_SCHEMA = "quanthus.p10.daily-consumer-config.v2.1"
M0_SESSION_CONFIG_SCHEMA = "quanthus.p10.daily-consumer-config.v2.2"
RUNTIME_VERSION = "p10-daily-consumer-sidecar-runtime-v2.2.1"
STORE_SCHEMA = "quanthus.p10.sidecar-verified-event-envelope.v1"
_SHA = re.compile(r"sha256:[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{40}")


class SidecarError(ValueError):
    """A stable metadata/identity failure, never an instruction to repair source data."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SidecarError(reason)


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SidecarError("NON_CANONICAL_JSON") from exc


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def record_id(record: Mapping[str, Any]) -> str:
    return digest({key: value for key, value in record.items() if key != "record_id"})


def _object(value: Any, fields: set[str], reason: str) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields, reason)
    return dict(value)


def _text(value: Any, reason: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), reason)
    return str(value)


def _sha(value: Any) -> str:
    text = _text(value, "SHA256_REQUIRED")
    _require(_SHA.fullmatch(text) is not None, "INVALID_SHA256")
    return text


def _instant(value: Any) -> datetime:
    text = _text(value, "TIME_REQUIRED")
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SidecarError("INVALID_TIME") from exc
    _require(result.tzinfo is not None and result.utcoffset() is not None, "TIMEZONE_REQUIRED")
    return result


def _path(value: Any) -> str:
    text = _text(value, "MEMBER_PATH_REQUIRED")
    p = PurePosixPath(text)
    _require(
        not p.is_absolute()
        and ".." not in p.parts
        and "\\" not in text
        and text == p.as_posix()
        and text not in {".", ""},
        "UNSAFE_MEMBER_PATH",
    )
    return text


@dataclass(frozen=True)
class MeasureRequirement:
    """Consumer-owned measures: a publisher cannot downgrade a volume use to price-only."""

    member_id: str
    measures: tuple[str, ...]
    volume_basis: str

    def __post_init__(self) -> None:
        _text(self.member_id, "MEMBER_ID_REQUIRED")
        _require(
            type(self.measures) is tuple
            and bool(self.measures)
            and all(
                isinstance(m, str) and m in {"open", "high", "low", "close", "volume", "turnover"}
                for m in self.measures
            )
            and len(set(self.measures)) == len(self.measures),
            "INVALID_CONSUMED_MEASURES",
        )
        quantity = bool(set(self.measures) & {"volume", "turnover"})
        _require(
            self.volume_basis in ({"FULL_DAY", "REGULAR_ONLY"} if quantity else {"NOT_REQUESTED"}),
            "INVALID_REQUIRED_VOLUME_BASIS",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.member_id,
            "measures": sorted(self.measures),
            "volume_basis": self.volume_basis,
        }


@dataclass(frozen=True)
class ResourceLimits:
    """Versioned consumer metadata budgets, never a warehouse-wide member threshold."""

    policy_id: str
    max_members: int | None = None
    max_evidence: int | None = None
    max_record_bytes: int | None = None
    max_events: int | None = None
    max_files: int | None = None
    max_store_bytes: int | None = None
    max_path_bytes: int | None = None

    def __post_init__(self) -> None:
        _text(self.policy_id, "RESOURCE_POLICY_REQUIRED")
        for name, value in asdict(self).items():
            if name != "policy_id" and value is not None:
                _require(type(value) is int and value > 0, "INVALID_RESOURCE_LIMIT")

    def check(self, name: str, amount: int) -> None:
        limit = getattr(self, name)
        _require(limit is None or amount <= limit, name.upper() + "_EXCEEDED")

    @classmethod
    def from_dict(cls, raw: Any) -> ResourceLimits:
        return cls(**_object(raw, set(cls.__dataclass_fields__), "INVALID_RESOURCE_LIMITS"))


@dataclass(frozen=True)
class ConsumerConfig:
    """Versioned consumer requirements; a null field/pool binding stays blocked."""

    config_id: str
    consumer_id: str
    purpose_id: str
    field_spec_sha256: str | None
    universe_sha256: str | None
    policy_sha256: str
    code_revision: str
    requirements: tuple[tuple[str, bool], ...]
    resource_limits: ResourceLimits | None = None
    m0_ref: str | None = None
    measure_requirements: tuple[MeasureRequirement, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.config_id, self.consumer_id, self.purpose_id):
            _text(value, "CONFIG_IDENTITY_REQUIRED")
        _sha(self.policy_sha256)
        for sha in (self.field_spec_sha256, self.universe_sha256, self.m0_ref):
            if sha is not None:
                _sha(sha)
        _require(
            isinstance(self.code_revision, str)
            and _REVISION.fullmatch(self.code_revision) is not None,
            "CODE_REVISION_REQUIRED",
        )
        _require(
            type(self.requirements) is tuple and bool(self.requirements), "REQUIREMENTS_REQUIRED"
        )
        ids = []
        for item in self.requirements:
            _require(type(item) is tuple and len(item) == 2, "INVALID_REQUIREMENT")
            name, required = item
            _text(name, "MEMBER_ID_REQUIRED")
            _require(type(required) is bool, "REQUIRED_MUST_BE_BOOLEAN")
            ids.append(name)
        _require(len(ids) == len(set(ids)), "DUPLICATE_REQUIREMENT")
        _require(any(required for _, required in self.requirements), "HARD_REQUIREMENT_REQUIRED")
        if self.resource_limits is not None:
            _require(isinstance(self.resource_limits, ResourceLimits), "INVALID_RESOURCE_LIMITS")
            self.resource_limits.check("max_members", len(self.requirements))

        _require(type(self.measure_requirements) is tuple, "INVALID_MEASURE_REQUIREMENTS")
        measure_ids = []
        for measure in self.measure_requirements:
            _require(isinstance(measure, MeasureRequirement), "INVALID_MEASURE_REQUIREMENT")
            _require(measure.member_id in ids, "UNKNOWN_MEASURE_MEMBER")
            measure_ids.append(measure.member_id)
        _require(len(set(measure_ids)) == len(measure_ids), "DUPLICATE_MEASURE_MEMBER")

    def to_dict(self) -> dict[str, Any]:
        schema = (
            (SESSION_BOUNDED_CONFIG_SCHEMA if self.resource_limits else SESSION_CONFIG_SCHEMA)
            if self.measure_requirements
            else (BOUNDED_CONFIG_SCHEMA if self.resource_limits else CONFIG_SCHEMA)
        )
        result: dict[str, Any] = {
            "schema": schema,
            "config_id": self.config_id,
            "consumer_id": self.consumer_id,
            "purpose_id": self.purpose_id,
            "field_spec_sha256": self.field_spec_sha256,
            "universe_sha256": self.universe_sha256,
            "policy_sha256": self.policy_sha256,
            "code_revision": self.code_revision,
            "requirements": [
                {"id": name, "required": required} for name, required in sorted(self.requirements)
            ],
        }
        if self.resource_limits:
            result["resource_limits"] = asdict(self.resource_limits)
        if self.measure_requirements:
            result["measure_requirements"] = [
                m.to_dict() for m in sorted(self.measure_requirements, key=lambda m: m.member_id)
            ]
        if self.m0_ref is not None:
            result["schema"] = (
                M0_SESSION_CONFIG_SCHEMA if self.measure_requirements else M0_CONFIG_SCHEMA
            )
            result["m0_ref"] = self.m0_ref
        return result

    @classmethod
    def from_dict(cls, raw: Any) -> ConsumerConfig:
        _require(isinstance(raw, Mapping), "INVALID_CONSUMER_CONFIG")
        assert isinstance(raw, Mapping)
        explicit_m0 = raw.get("schema") in {M0_CONFIG_SCHEMA, M0_SESSION_CONFIG_SCHEMA}
        bounded = raw.get("schema") in {BOUNDED_CONFIG_SCHEMA, SESSION_BOUNDED_CONFIG_SCHEMA} or (
            explicit_m0 and "resource_limits" in raw
        )
        session_version = raw.get("schema") in {
            SESSION_CONFIG_SCHEMA,
            SESSION_BOUNDED_CONFIG_SCHEMA,
            M0_SESSION_CONFIG_SCHEMA,
        }
        obj = _object(
            raw,
            {
                "schema",
                "config_id",
                "consumer_id",
                "purpose_id",
                "field_spec_sha256",
                "universe_sha256",
                "policy_sha256",
                "code_revision",
                "requirements",
            }
            | ({"resource_limits"} if bounded else set())
            | ({"measure_requirements"} if session_version else set())
            | ({"m0_ref"} if explicit_m0 else set()),
            "INVALID_CONSUMER_CONFIG",
        )
        _require(
            obj["schema"]
            in {
                CONFIG_SCHEMA,
                BOUNDED_CONFIG_SCHEMA,
                M0_CONFIG_SCHEMA,
                SESSION_CONFIG_SCHEMA,
                SESSION_BOUNDED_CONFIG_SCHEMA,
                M0_SESSION_CONFIG_SCHEMA,
            }
            and isinstance(obj["requirements"], list),
            "INVALID_CONSUMER_CONFIG",
        )
        if explicit_m0:
            _sha(obj["m0_ref"])
        requirements = []
        for item in obj["requirements"]:
            row = _object(item, {"id", "required"}, "INVALID_REQUIREMENT")
            requirements.append((row["id"], row["required"]))
        measures = []
        if session_version:
            _require(
                isinstance(obj["measure_requirements"], list) and bool(obj["measure_requirements"]),
                "INVALID_MEASURE_REQUIREMENTS",
            )
            for item in obj["measure_requirements"]:
                row = _object(
                    item, {"id", "measures", "volume_basis"}, "INVALID_MEASURE_REQUIREMENT"
                )
                _require(isinstance(row["measures"], list), "INVALID_CONSUMED_MEASURES")
                measures.append(
                    MeasureRequirement(row["id"], tuple(row["measures"]), row["volume_basis"])
                )
        return cls(
            obj["config_id"],
            obj["consumer_id"],
            obj["purpose_id"],
            obj["field_spec_sha256"],
            obj["universe_sha256"],
            obj["policy_sha256"],
            obj["code_revision"],
            tuple(requirements),
            resource_limits=ResourceLimits.from_dict(obj["resource_limits"]) if bounded else None,
            m0_ref=obj["m0_ref"] if explicit_m0 else None,
            measure_requirements=tuple(measures),
        )

    @property
    def identity(self) -> str:
        return digest(self.to_dict())

    @property
    def binding_complete(self) -> bool:
        return (
            self.field_spec_sha256 is not None
            and self.universe_sha256 is not None
            and self.m0_ref is not None
        )


_MEMBER_FIELDS = {
    "id",
    "path",
    "sha256",
    "size_bytes",
    "required",
    "source_version",
    "state",
    "unavailable_reason",
    "source_available_at",
    "validated_at",
    "available_at",
}
_INVENTORY_FIELDS = ("id", "path", "sha256", "size_bytes", "required", "source_version")


def inventory_digest(members: Sequence[Mapping[str, Any]]) -> str:
    try:
        rows = [
            {
                key: item[key]
                for key in (*_INVENTORY_FIELDS, *(("session",) if "session" in item else ()))
            }
            for item in members
        ]
        return digest(sorted(rows, key=lambda item: item["id"]))
    except (KeyError, TypeError) as exc:
        raise SidecarError("INVALID_INVENTORY") from exc


def input_identity(record: Mapping[str, Any], config: ConsumerConfig) -> str:
    """Complete reusable input identity, excluding later publication/channel observations."""
    try:
        rows = [
            {
                key: item[key]
                for key in (
                    *_INVENTORY_FIELDS,
                    "source_available_at",
                    "state",
                    *(("session",) if record["schema"] == SESSION_SCHEMA else ()),
                )
            }
            for item in record["members"]
        ]
        source_proofs = [
            item for item in record["evidence"] if item["semantic"] == "source_artifact_known"
        ]
        return digest(
            {
                "consumer_config": config.to_dict(),
                "binding": record["binding"],
                "source": record["source"],
                "members": sorted(rows, key=lambda item: item["id"]),
                "source_proofs": sorted(source_proofs, key=lambda item: item["id"]),
            }
        )
    except (KeyError, TypeError) as exc:
        raise SidecarError("INVALID_INPUT_IDENTITY") from exc


@dataclass(frozen=True)
class InputVerification:
    """Caller-owned prior verification; no automatic source reads or generated times."""

    input_identity: str
    evidence_id: str
    uri: str
    sha256: str
    start: str
    end: str

    def __post_init__(self) -> None:
        for sha in (self.input_identity, self.sha256):
            _sha(sha)
        _text(self.evidence_id, "VERIFICATION_ID_REQUIRED")
        _text(self.uri, "VERIFICATION_URI_REQUIRED")
        _require(_instant(self.start) <= _instant(self.end), "VERIFICATION_TIME_REVERSED")

    def clock(self) -> dict[str, str]:
        return {
            "start": self.start,
            "end": self.end,
            "evidence_ref": self.evidence_id,
            "semantic": "validation_complete",
        }

    @classmethod
    def from_dict(cls, raw: Any) -> InputVerification:
        return cls(**_object(raw, set(cls.__dataclass_fields__), "INVALID_STORED_VERIFICATION"))


@dataclass(frozen=True)
class Assessment:
    disposition: str
    blockers: tuple[str, ...]
    member_blockers: tuple[tuple[str, tuple[str, ...]], ...]
    boundary_notes: tuple[str, ...] = ()


_TOP = {
    "schema",
    "record_id",
    "evidence_grade",
    "event",
    "binding",
    "scope",
    "source",
    "members",
    "publication",
    "evidence",
    "revision",
    "consumer_receipt",
}
_BINDING = {
    "delivery_id",
    "purpose_id",
    "market_date",
    "decision_cutoff",
    "consumer_id",
    "m0_ref",
    "observation_end",
}
_CLOCK = {"start", "end", "evidence_ref", "semantic"}
_EVIDENCE = {
    "id",
    "kind",
    "semantic",
    "uri",
    "sha256",
    "descriptor_sha256",
    "covered_member_ids",
}
_SEMANTICS = {
    "source_artifact_known",
    "validation_complete",
    "publication_commit",
    "consumer_channel_visible",
}
_REVISION_FIELDS = {
    "state",
    "source_chain_state",
    "previous_record_id",
    "reason",
    "replaces_descriptor_sha256",
}


_SESSION_FIELDS = {
    "session_scope",
    "volume_basis",
    "official_price_close_anchor",
    "full_day_volume_amount_observation_end",
    "applicability",
    "vendor_scope_evidence",
}
_APPLICABILITY_FIELDS = {
    "venue_mic",
    "board",
    "security_scope_sha256",
    "effective_start",
    "effective_end",
    "after_hours",
    "calendar_sha256",
    "rule_sha256",
    "evidence",
}


def _metadata_ref(raw: Any) -> None:
    ref = _object(raw, {"uri", "sha256"}, "INVALID_SESSION_EVIDENCE")
    _text(ref["uri"], "SESSION_EVIDENCE_URI_REQUIRED")
    _sha(ref["sha256"])


def _official_price_close(day: date) -> datetime:
    return datetime.combine(day, time(15), ZoneInfo("Asia/Shanghai"))


def _session_blockers(
    raw: Any,
    requirement: MeasureRequirement,
    day: date,
    clocks: Sequence[tuple[datetime, datetime] | None],
) -> set[str]:
    """Check declared, identity-bound metadata; upstream owns applicability/source truth."""
    price_anchor = _official_price_close(day)
    if raw is None:
        return {"SESSION_METADATA_UNKNOWN"}
    meta = _object(raw, _SESSION_FIELDS, "INVALID_SESSION_METADATA")
    _require(
        meta["session_scope"]
        in {"REGULAR_AND_APPLICABLE_AFTER_HOURS", "REGULAR_ONLY", "AFTER_HOURS_ONLY", "UNKNOWN"},
        "INVALID_SESSION_SCOPE",
    )
    _require(
        meta["volume_basis"] in {"FULL_DAY", "REGULAR_ONLY", "INDEPENDENT_AFTER_HOURS", "UNKNOWN"},
        "INVALID_VOLUME_BASIS",
    )
    reasons: set[str] = set()
    if set(requirement.measures) & {"open", "high", "low", "close"}:
        if meta["official_price_close_anchor"] is None:
            reasons.add("PRICE_CLOSE_ANCHOR_UNKNOWN")
        else:
            _require(
                _instant(meta["official_price_close_anchor"]) == price_anchor,
                "PRICE_CLOSE_ANCHOR_MISMATCH",
            )
    # Price-only use is explicitly in consumer config; unknown quantity facts do not gate it.
    if not set(requirement.measures) & {"volume", "turnover"}:
        return reasons
    if meta["volume_basis"] != requirement.volume_basis:
        reasons.add("VOLUME_BASIS_NOT_" + requirement.volume_basis)
    if meta["vendor_scope_evidence"] is None:
        reasons.add("VENDOR_VOLUME_SCOPE_UNKNOWN")
    else:
        _metadata_ref(meta["vendor_scope_evidence"])
    expected_scope = (
        "REGULAR_AND_APPLICABLE_AFTER_HOURS"
        if requirement.volume_basis == "FULL_DAY"
        else "REGULAR_ONLY"
    )
    if meta["session_scope"] != expected_scope:
        reasons.add("SESSION_SCOPE_INCOMPATIBLE")
    if requirement.volume_basis != "FULL_DAY":
        return reasons
    # None is UNKNOWN, including on a date before an expansion. N/A needs named evidence.
    applies = None
    app = meta["applicability"]
    if app is None:
        reasons.add("AFTER_HOURS_APPLICABILITY_UNKNOWN")
    else:
        app = _object(app, _APPLICABILITY_FIELDS, "INVALID_SESSION_APPLICABILITY")
        for field in ("venue_mic", "board"):
            _text(app[field], "APPLICABILITY_IDENTITY_REQUIRED")
        for field in ("security_scope_sha256", "calendar_sha256", "rule_sha256"):
            _sha(app[field])
        _metadata_ref(app["evidence"])
        try:
            start = date.fromisoformat(app["effective_start"])
            end = (
                date.fromisoformat(app["effective_end"])
                if app["effective_end"] is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise SidecarError("INVALID_SESSION_EFFECTIVE_RANGE") from exc
        _require(end is None or start <= end, "INVALID_SESSION_EFFECTIVE_RANGE")
        if not (start <= day and (end is None or day <= end)):
            reasons.add("SESSION_EFFECTIVE_SCOPE_MISMATCH")
        applies = app["after_hours"]
        _require(
            applies in {"APPLIES", "NOT_APPLICABLE", "UNKNOWN"}, "INVALID_AFTER_HOURS_APPLICABILITY"
        )
        if applies == "UNKNOWN":
            reasons.add("AFTER_HOURS_APPLICABILITY_UNKNOWN")
    volume_end_raw = meta["full_day_volume_amount_observation_end"]
    if volume_end_raw is None:
        reasons.add("VOLUME_OBSERVATION_END_UNKNOWN")
    else:
        volume_end = _instant(volume_end_raw)
        _require(
            volume_end.astimezone(ZoneInfo("Asia/Shanghai")).date() == day
            and volume_end >= price_anchor,
            "INVALID_VOLUME_OBSERVATION_END",
        )
        if applies == "APPLIES":
            floor = datetime.combine(day, time(15, 30), ZoneInfo("Asia/Shanghai"))
            if volume_end < floor:
                reasons.add("FULL_DAY_VOLUME_WINDOW_INCOMPLETE")
        for label, clock_value in zip(("SOURCE", "PUBLICATION", "AVAILABLE"), clocks, strict=True):
            if clock_value is not None and clock_value[0] < volume_end:
                reasons.add(label + "_PRECEDES_VOLUME_COMPLETENESS")
    return reasons


def assess(
    value: Mapping[str, Any],
    config: ConsumerConfig,
    verification: InputVerification,
    *,
    previous: Mapping[str, Any] | None = None,
) -> Assessment:
    """Validate metadata only. Complete source evidence still requires consumer qualification."""
    raw = canonical(dict(value))
    limits = config.resource_limits
    if limits:
        limits.check("max_record_bytes", len(raw))
    record = _object(_strict_json(raw), _TOP, "INVALID_RECORD_SCHEMA")
    _require(record["schema"] in {SCHEMA, SESSION_SCHEMA}, "UNSUPPORTED_SCHEMA")
    session_version = record["schema"] == SESSION_SCHEMA
    _require(
        session_version == bool(config.measure_requirements), "SESSION_CONFIG_VERSION_MISMATCH"
    )
    _require(record["record_id"] == record_id(record), "RECORD_ID_MISMATCH")
    grade = record["evidence_grade"]
    _require(grade in {"SYNTHETIC", "HISTORICAL_METADATA_ONLY", "SOURCE_OBSERVED"}, "INVALID_GRADE")
    event = record["event"]
    _require(event in {"PUBLISH", "REVISE", "WITHDRAW"}, "INVALID_EVENT")
    _require(record["consumer_receipt"] is None, "CONSUMER_RECEIPT_NOT_P10_OWNED")
    binding = _object(record["binding"], _BINDING, "INVALID_BINDING")
    _text(binding["delivery_id"], "DELIVERY_ID_REQUIRED")
    try:
        day = date.fromisoformat(binding["market_date"])
    except (TypeError, ValueError) as exc:
        raise SidecarError("INVALID_MARKET_DATE") from exc
    cutoff = _instant(binding["decision_cutoff"])
    _require(day == cutoff.astimezone(ZoneInfo("Asia/Shanghai")).date(), "CUTOFF_DATE_MISMATCH")
    observation_end = _instant(binding["observation_end"])
    _require(
        observation_end.astimezone(ZoneInfo("Asia/Shanghai")).date() == day
        and observation_end <= cutoff,
        "OBSERVATION_WINDOW_MISMATCH",
    )
    if session_version:
        _require(observation_end == _official_price_close(day), "PRICE_OBSERVATION_END_MISMATCH")
    _require(
        binding["consumer_id"] == config.consumer_id and binding["purpose_id"] == config.purpose_id,
        "CONSUMER_CONFIG_MISMATCH",
    )
    if binding["m0_ref"] is not None:
        _sha(binding["m0_ref"])
        if config.m0_ref is not None:
            _require(binding["m0_ref"] == config.m0_ref, "M0_REFERENCE_MISMATCH")
    _require(
        record["scope"] == {"intervals": ["1d"], "references": "required_only"}, "INVALID_SCOPE"
    )
    source = _object(record["source"], {"descriptor", "manifest"}, "INVALID_SOURCE")
    for role in ("descriptor", "manifest"):
        ref = _object(source[role], {"uri", "sha256"}, "INVALID_SOURCE_REF")
        _text(ref["uri"], "SOURCE_URI_REQUIRED")
        _sha(ref["sha256"])
    members = record["members"]
    _require(isinstance(members, list), "MEMBERS_REQUIRED")
    if limits:
        limits.check("max_members", len(members))
    required = dict(config.requirements)
    measures = {m.member_id: m for m in config.measure_requirements}
    found = []
    paths = []
    for item in members:
        m = _object(
            item,
            _MEMBER_FIELDS | ({"session"} if session_version else set()),
            "INVALID_MEMBER_SCHEMA",
        )
        name = _text(m["id"], "MEMBER_ID_REQUIRED")
        found.append(name)
        paths.append(_path(m["path"]))
        if limits:
            limits.check("max_path_bytes", len(m["path"].encode("utf-8")))
        _require(
            name in required and type(m["required"]) is bool and required[name] == m["required"],
            "MEMBER_REQUIREMENT_CHANGED",
        )
        _sha(m["sha256"])
        _require(type(m["size_bytes"]) is int and m["size_bytes"] >= 0, "INVALID_MEMBER_SIZE")
        _text(m["source_version"], "SOURCE_VERSION_REQUIRED")
        _require(m["state"] in {"PRESENT", "UNAVAILABLE", "WITHDRAWN"}, "INVALID_MEMBER_STATE")
        if m["state"] != "PRESENT":
            _text(m["unavailable_reason"], "UNAVAILABLE_REASON_REQUIRED")
        _require("5m" not in str(m["path"]).lower(), "FIVE_MINUTE_NOT_IN_SCOPE")
        if session_version and name not in measures:
            _require(m["session"] is None, "UNCONSUMED_SESSION_METADATA")
    _require(len(set(found)) == len(found) and len(set(paths)) == len(paths), "DUPLICATE_MEMBER")
    _require(set(found) <= required.keys(), "UNKNOWN_MEMBER")
    _require(
        input_identity(record, config) == verification.input_identity,
        "VERIFICATION_IDENTITY_MISMATCH",
    )
    proofs = record["evidence"]
    _require(isinstance(proofs, list), "EVIDENCE_REQUIRED")
    if limits:
        limits.check("max_evidence", len(proofs))
    evidence: dict[str, dict[str, Any]] = {}
    for item in proofs:
        proof = _object(item, _EVIDENCE, "INVALID_EVIDENCE")
        eid = _text(proof["id"], "EVIDENCE_ID_REQUIRED")
        _require(eid not in evidence, "DUPLICATE_EVIDENCE")
        _require(
            proof["kind"] in {"SYNTHETIC_FIXTURE", "OBSERVED_ATTESTATION"}, "INVALID_EVIDENCE_KIND"
        )
        _require(proof["semantic"] in _SEMANTICS, "INVALID_EVIDENCE_SEMANTIC")
        _text(proof["uri"], "EVIDENCE_URI_REQUIRED")
        _sha(proof["sha256"])
        _require(
            proof["descriptor_sha256"] == source["descriptor"]["sha256"], "EVIDENCE_SOURCE_MISMATCH"
        )
        ids = proof["covered_member_ids"]
        _require(
            isinstance(ids, list)
            and all(isinstance(i, str) for i in ids)
            and len(ids) == len(set(ids))
            and set(ids) <= set(found),
            "INVALID_EVIDENCE_COVERAGE",
        )
        if grade == "SOURCE_OBSERVED":
            _require(proof["kind"] == "OBSERVED_ATTESTATION", "SYNTHETIC_EVIDENCE_NOT_REAL")
        evidence[eid] = proof
    validation_proof = evidence.get(verification.evidence_id)
    _require(
        validation_proof is not None
        and validation_proof["semantic"] == "validation_complete"
        and validation_proof["uri"] == verification.uri
        and validation_proof["sha256"] == verification.sha256
        and set(validation_proof["covered_member_ids"]) == set(found),
        "VERIFICATION_EVIDENCE_MISMATCH",
    )

    def clock(raw: Any, semantic: str, ids: set[str]) -> tuple[datetime, datetime] | None:
        if raw is None:
            return None
        item = _object(raw, _CLOCK, "INVALID_CLOCK")
        _require(item["semantic"] == semantic, "CLOCK_SEMANTIC_MISMATCH")
        start, end = _instant(item["start"]), _instant(item["end"])
        _require(start <= end, "CLOCK_INTERVAL_REVERSED")
        e = evidence.get(item["evidence_ref"])
        _require(e is not None and e["semantic"] == semantic, "CLOCK_EVIDENCE_MISSING")
        assert e is not None
        _require(ids <= set(e["covered_member_ids"]), "CLOCK_MEMBER_COVERAGE_MISSING")
        return start, end

    pub = _object(
        record["publication"],
        {
            "publisher",
            "channel",
            "published_at",
            "visible_at",
            "covered_inventory_sha256",
        },
        "INVALID_PUBLICATION",
    )
    for actor in ("publisher", "channel"):
        if pub[actor] is not None:
            _text(pub[actor], "PUBLICATION_ACTOR_INVALID")
    _require(
        pub["covered_inventory_sha256"] == inventory_digest(members), "INVENTORY_IDENTITY_MISMATCH"
    )
    all_ids = set(found)
    published = clock(pub["published_at"], "publication_commit", all_ids)
    visible_event = clock(pub["visible_at"], "consumer_channel_visible", all_ids)
    blockers: set[str] = set()
    if grade == "SOURCE_OBSERVED":
        # No real source/M0/publisher loader is implemented by this isolated library.
        # URI spelling or a caller-supplied kind cannot open this integration gate.
        blockers.add("SOURCE_QUALIFICATION_UNBOUND")
        refs = [
            verification.uri,
            *(source[r]["uri"] for r in ("descriptor", "manifest")),
            *(p["uri"] for p in proofs),
        ]
        if any(uri.lower().startswith(("fixture:", "synthetic:", "test:")) for uri in refs):
            blockers.add("NON_OBSERVED_REFERENCE")
    if binding["m0_ref"] is None:
        blockers.add("M0_NOT_BOUND")
    if config.m0_ref is None and binding["m0_ref"] is not None:
        blockers.add("M0_REFERENCE_UNCONFIGURED")
    if not config.binding_complete or binding["m0_ref"] is None:
        blockers.add("CONSUMER_CLOSURE_PENDING")
    if not pub["publisher"] or not pub["channel"] or published is None:
        blockers.add("PUBLICATION_UNKNOWN")
    if visible_event is None:
        blockers.add("EVENT_VISIBILITY_UNKNOWN")
    if published and visible_event:
        _require(published[1] <= visible_event[0], "EVENT_CAUSALITY")
    if published and published[1] > cutoff or visible_event and visible_event[1] > cutoff:
        blockers.add("EVENT_AFTER_CUTOFF")
    member_blockers = []
    for name, hard in sorted(required.items()):
        matches = [m for m in members if m["id"] == name]
        reasons: set[str] = set()
        if not matches:
            reasons.add("MEMBER_MISSING")
        else:
            m = matches[0]
            if m["state"] != "PRESENT":
                reasons.add("MEMBER_" + m["state"])
            known = clock(m["source_available_at"], "source_artifact_known", {name})
            validated = clock(m["validated_at"], "validation_complete", {name})
            available = clock(m["available_at"], "consumer_channel_visible", {name})
            if name in measures:
                reasons.update(
                    _session_blockers(
                        m["session"],
                        measures[name],
                        day,
                        (known, published, available),
                    )
                )
            if m["validated_at"] is not None:
                _require(m["validated_at"] == verification.clock(), "VALIDATION_CLOCK_MISMATCH")
            if known and validated:
                _require(known[1] <= validated[0], "SOURCE_VALIDATION_CAUSALITY")
            if event != "WITHDRAW" and validated and published:
                _require(validated[1] <= published[0], "MEMBER_CAUSALITY")
            if event != "WITHDRAW" and published and available:
                _require(published[1] <= available[0], "MEMBER_CAUSALITY")
            for c in (known, validated, available):
                if c is None:
                    reasons.add("MEMBER_CLOCK_UNKNOWN")
                elif c[1] > cutoff:
                    reasons.add("MEMBER_AFTER_CUTOFF")
            if visible_event and available and event != "WITHDRAW":
                _require(available[1] <= visible_event[1], "EVENT_PRECEDES_MEMBER_VISIBILITY")
        if reasons:
            member_blockers.append((name, tuple(sorted(reasons))))
            if hard:
                blockers.add("HARD_MEMBER_BLOCKED:" + name)
    rev = _object(record["revision"], _REVISION_FIELDS, "INVALID_REVISION")
    _require(
        rev["state"] in {"COMPLETE", "UNKNOWN"}
        and rev["source_chain_state"] in {"COMPLETE", "UNKNOWN"},
        "INVALID_CHAIN_STATE",
    )
    if rev["state"] != "COMPLETE" or rev["source_chain_state"] != "COMPLETE":
        blockers.add("REVISION_CHAIN_UNKNOWN")
    if event == "PUBLISH":
        _require(
            previous is None
            and rev["previous_record_id"] is None
            and rev["replaces_descriptor_sha256"] is None,
            "PUBLISH_PREDECESSOR_UNEXPECTED",
        )
    else:
        _require(previous is not None, "PREDECESSOR_REQUIRED")
        assert previous is not None
        _require(
            previous["record_id"] == record_id(previous)
            and rev["previous_record_id"] == previous["record_id"],
            "PREDECESSOR_ID_MISMATCH",
        )
        for field in (
            "market_date",
            "consumer_id",
            "purpose_id",
            "m0_ref",
            "observation_end",
            "decision_cutoff",
        ):
            _require(previous["binding"][field] == binding[field], "PREDECESSOR_SCOPE_MISMATCH")
        _text(rev["reason"], "REVISION_REASON_REQUIRED")
        old_sha = previous["source"]["descriptor"]["sha256"]
        if event == "REVISE":
            _require(
                rev["replaces_descriptor_sha256"] == old_sha
                and source["descriptor"]["sha256"] != old_sha
                and source["manifest"]["sha256"] != previous["source"]["manifest"]["sha256"]
                and binding["delivery_id"] != previous["binding"]["delivery_id"],
                "REVISION_REQUIRES_NEW_IDENTITY",
            )
        else:
            _require(
                rev["replaces_descriptor_sha256"] == old_sha
                and source == previous["source"]
                and binding["delivery_id"] == previous["binding"]["delivery_id"],
                "WITHDRAWAL_TARGET_MISMATCH",
            )
            _require(
                canonical(members) == canonical(previous["members"])
                and pub["covered_inventory_sha256"]
                == previous["publication"]["covered_inventory_sha256"],
                "WITHDRAWAL_INVENTORY_MISMATCH",
            )
            blockers.add("WITHDRAWN_VERSION")
        old_clock = previous["publication"]["published_at"]
        if published and old_clock is not None:
            _require(_instant(old_clock["end"]) <= published[0], "EVENT_TIME_REGRESSION")
    if grade == "HISTORICAL_METADATA_ONLY":
        blockers.add("HISTORICAL_METADATA_ONLY")
    disposition = "INPUT_OR_CLOCK_BLOCKED" if blockers else "CLOCKS_COMPLETE_FOR_CONSUMER_REVIEW"
    if grade == "SYNTHETIC":
        disposition = "TEST_ONLY"
    unknown_limits = tuple(
        name
        for name in ResourceLimits.__dataclass_fields__
        if name != "policy_id" and (limits is None or getattr(limits, name) is None)
    )
    return Assessment(
        disposition,
        tuple(sorted(blockers)),
        tuple(member_blockers),
        tuple("UNCONFIGURED_RESOURCE_BOUND:" + name for name in unknown_limits),
    )


@dataclass(frozen=True)
class Selection:
    record_id: str | None
    disposition: str
    blockers: tuple[str, ...]
    boundary_notes: tuple[str, ...] = ()


def select_as_of(
    records: Sequence[Mapping[str, Any]],
    config: ConsumerConfig,
    verifications: Mapping[str, InputVerification],
    *,
    market_date: str,
    cutoff: str,
) -> Selection:
    """Use actual event visibility and every member clock, never file mtime or append order."""
    if config.resource_limits:
        config.resource_limits.check("max_events", len(records))
    notes = tuple(
        "UNCONFIGURED_RESOURCE_BOUND:" + name
        for name in ResourceLimits.__dataclass_fields__
        if name != "policy_id"
        and (config.resource_limits is None or getattr(config.resource_limits, name) is None)
    )
    at = _instant(cutoff)
    try:
        date.fromisoformat(market_date)
    except ValueError as exc:
        raise SidecarError("INVALID_MARKET_DATE") from exc
    heads: dict[tuple[str, str], Mapping[str, Any]] = {}
    eligible: list[tuple[Mapping[str, Any], Assessment]] = []
    visibility_gaps: set[str] = set()
    for record in records:
        if record["binding"]["market_date"] != market_date:
            continue
        key = (record["binding"]["market_date"], record["binding"]["purpose_id"])
        previous = heads.get(key)
        receipt = verifications.get(record["record_id"])
        _require(receipt is not None, "VERIFICATION_RECEIPT_REQUIRED")
        assert receipt is not None
        result = assess(record, config, receipt, previous=previous)
        heads[key] = record
        published = record["publication"]["published_at"]
        if published is not None and _instant(published["start"]) > at:
            # A later publication cannot have been visible before its own lower bound.
            # Keep the visibility field unknown; do not manufacture an available_at.
            continue
        visible = record["publication"]["visible_at"]
        if visible is None:
            visibility_gaps.add("EVENT_VISIBILITY_UNKNOWN")
            continue
        if _instant(visible["start"]) <= at < _instant(visible["end"]):
            visibility_gaps.add("EVENT_VISIBILITY_INTERVAL_CROSSES_CUTOFF")
            continue
        if _instant(visible["end"]) <= at:
            eligible.append((record, result))
    if visibility_gaps:
        return Selection(None, "INPUT_OR_CLOCK_BLOCKED", tuple(sorted(visibility_gaps)), notes)
    if not eligible:
        return Selection(None, "INPUT_OR_CLOCK_BLOCKED", ("NO_VISIBLE_VERSION",), notes)
    record, result = eligible[-1]
    # A member or event must also be available at the requested as-of, which may precede
    # the delivery's registered decision cutoff.
    for item in record["members"]:
        if not item["required"]:
            continue
        for field in ("source_available_at", "validated_at", "available_at"):
            c = item[field]
            if c is None or _instant(c["end"]) > at:
                return Selection(
                    None, "INPUT_OR_CLOCK_BLOCKED", ("MEMBER_NOT_VISIBLE_AS_OF",), notes
                )
    if record["event"] == "WITHDRAW":
        return Selection(None, "INPUT_OR_CLOCK_BLOCKED", ("WITHDRAWN_VERSION",), notes)
    return Selection(
        record["record_id"], result.disposition, result.blockers, result.boundary_notes
    )


def _strict_json(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            _require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def nonfinite(_: str) -> Any:
        raise SidecarError("NON_FINITE_JSON")

    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SidecarError("UNREADABLE_EVENT") from exc
    _require(isinstance(result, dict), "EVENT_OBJECT_REQUIRED")
    return dict(result)


class SidecarStore:
    """Explicit isolated metadata root; no automatic clock or publisher activation."""

    def __init__(self, root: Path, config: ConsumerConfig, *, protected_roots: Sequence[Path]):
        self.root = root.absolute()
        self.config = config
        for path in (self.root, *self.root.parents):
            _require(not path.is_symlink(), "STORE_SYMLINK")
        resolved = self.root.resolve()
        for protected in protected_roots:
            p = protected.resolve()
            _require(
                not resolved.is_relative_to(p) and not p.is_relative_to(resolved),
                "STORE_SOURCE_OVERLAP",
            )

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        _require(not self.root.is_symlink(), "STORE_SYMLINK")
        fd = os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _require(
                all(
                    p.name in {".lock", "CONSUMER.json", "records"}
                    or p.name.startswith(".pending-")
                    for p in self.root.iterdir()
                ),
                "STORE_NOT_DEDICATED",
            )
            self._write_once(self.root / "CONSUMER.json", canonical(self.config.to_dict()))
            (self.root / "records").mkdir(exist_ok=True)
            _require(not (self.root / "records").is_symlink(), "STORE_SYMLINK")
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _sync(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _write_once(self, path: Path, raw: bytes) -> None:
        _require(not path.is_symlink(), "STORE_SYMLINK")
        tmp: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".pending-", delete=False
            ) as f:
                tmp = Path(f.name)
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                _require(path.read_bytes() == raw, "IMMUTABLE_EVENT_CONFLICT")
            self._sync(path.parent)
        finally:
            if tmp is not None:
                with suppress(FileNotFoundError):
                    tmp.unlink()

    def _read(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        limits = self.config.resource_limits
        paths = []
        total_bytes = 0
        for path in (self.root / "records").iterdir():
            _require(not path.is_symlink(), "STORE_SYMLINK")
            if path.name.startswith(".pending-"):
                continue
            paths.append(path)
            if limits:
                limits.check("max_files", len(paths))
        heads: dict[str, Mapping[str, Any]] = {}
        for path in sorted(paths):
            _require(not path.is_symlink(), "STORE_SYMLINK")
            if path.name.startswith(".pending-"):
                continue
            _require(
                path.is_file()
                and re.fullmatch(r"[0-9]{8}-[0-9a-f]{64}\.json", path.name) is not None,
                "UNEXPECTED_EVENT_FILE",
            )
            _require(path.name.startswith(f"{len(records) + 1:08d}-"), "EVENT_SEQUENCE_GAP")
            size = path.stat().st_size
            total_bytes += size
            if limits:
                limits.check("max_record_bytes", size)
                limits.check("max_store_bytes", total_bytes)
                limits.check("max_events", len(records) + 1)
            envelope = _object(
                _strict_json(path.read_bytes()),
                {"schema", "record", "verification"},
                "STORED_VERIFICATION_RECEIPT_REQUIRED",
            )
            _require(envelope["schema"] == STORE_SCHEMA, "UNSUPPORTED_STORE_SCHEMA")
            record = _object(envelope["record"], _TOP, "INVALID_RECORD_SCHEMA")
            _require(
                record.get("record_id") == record_id(record)
                and path.name.endswith(record["record_id"][7:] + ".json"),
                "STORED_EVENT_ID_MISMATCH",
            )
            receipt = InputVerification.from_dict(envelope["verification"])
            binding = _object(record["binding"], _BINDING, "INVALID_BINDING")
            day = _text(binding["market_date"], "INVALID_MARKET_DATE")
            assess(record, self.config, receipt, previous=heads.get(day))
            heads[day] = record
            records.append(record)
        return records

    def read(self) -> list[dict[str, Any]]:
        with self._lock():
            return self._read()

    def append(self, record: Mapping[str, Any], verification: InputVerification) -> bool:
        """Return True for a new event, False for an exact retry, including post-commit crash."""
        record = _object(_strict_json(canonical(dict(record))), _TOP, "INVALID_RECORD_SCHEMA")
        _object(record["binding"], _BINDING, "INVALID_BINDING")
        with self._lock():
            records = self._read()
            for existing in records:
                if existing["binding"]["delivery_id"] == record["binding"]["delivery_id"]:
                    _require(
                        existing["source"] == record["source"]
                        and existing["binding"] == record["binding"],
                        "DELIVERY_ID_REBOUND",
                    )
            same = [r for r in records if r["record_id"] == record.get("record_id")]
            if same:
                _require(canonical(same[0]) == canonical(record), "IMMUTABLE_EVENT_CONFLICT")
                # Retry cannot replace any part of the originally committed verification.
                position = records.index(same[0])
                path = self.root / "records" / f"{position + 1:08d}-{record['record_id'][7:]}.json"
                original = _strict_json(path.read_bytes())["verification"]
                _require(original == asdict(verification), "IMMUTABLE_VERIFICATION_CONFLICT")
                prior = next(
                    (
                        r
                        for r in reversed(records[:position])
                        if r["binding"]["market_date"] == record["binding"]["market_date"]
                    ),
                    None,
                )
                assess(record, self.config, verification, previous=prior)
                return False
            previous = next(
                (
                    r
                    for r in reversed(records)
                    if r["binding"]["market_date"] == record["binding"]["market_date"]
                ),
                None,
            )
            assess(record, self.config, verification, previous=previous)
            name = f"{len(records) + 1:08d}-{record['record_id'][7:]}.json"
            raw = canonical(
                {
                    "schema": STORE_SCHEMA,
                    "record": dict(record),
                    "verification": asdict(verification),
                }
            )
            limits = self.config.resource_limits
            if limits:
                limits.check("max_events", len(records) + 1)
                limits.check("max_files", len(records) + 1)
                limits.check("max_record_bytes", len(raw))
                used = sum(
                    p.stat().st_size
                    for p in (self.root / "records").iterdir()
                    if not p.name.startswith(".pending-")
                )
                limits.check("max_store_bytes", used + len(raw))
            self._write_once(self.root / "records" / name, raw)
            return True
