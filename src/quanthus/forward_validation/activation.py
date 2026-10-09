# ruff: noqa: E501
"""P6-ACTIVATE-001 prospective runtime and activation-decision contracts.

The runtime contract may be implemented and rehearsed before activation.  Only a separately sealed
activation decision may authorize official prospective evidence; the decision never grants broker,
notification, or live-capital authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.forward_validation.contracts import ForwardValidationContractError, fail
from quanthus.forward_validation.serialization import digest_value

_RUNTIME_SCHEMA = "quanthus.p6-prospective-runtime.v1"
_RUNTIME_R1_SCHEMA = "quanthus.p6-prospective-runtime-r1.v1"
_MANDATE_SCHEMA = "quanthus.p6-owner-activation-mandate.v1"
_ACTIVATION_SCHEMA = "quanthus.p6-activation-decision.v1"
_ENTRY_DIGEST = "sha256:3c9356e49dbdd6356754b42cfd34af11949289121454a05bdbf0c0ff2aebaa18"
_STRATEGY_DIGEST = "sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c"
_CANDIDATE_DIGEST = "sha256:b04464c838705e71331515332ceb9e796ce26698c784d4615cff00dfdcf41c50"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_MARKET_TZ = ZoneInfo("Asia/Shanghai")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardValidationContractError("invalid_json", f"{label}: {path}") from exc
    if not isinstance(value, dict):
        fail("field_mismatch", f"{label} must contain one object")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        fail("invalid_digest", field)
    return value


def _sha40(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA40.fullmatch(value) is None:
        fail("invalid_revision", field)
    return value


def _instant(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        fail("invalid_instant", field)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ForwardValidationContractError("invalid_instant", field) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        fail("invalid_instant", field)
    return parsed


def _repo_binding(root: Path, value: object, field: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(value, Mapping) or "path" not in value or "sha256" not in value:
        fail("field_mismatch", f"{field} binding is incomplete")
    raw = value.get("path")
    if not isinstance(raw, str) or not raw or Path(raw).is_absolute() or ".." in Path(raw).parts:
        fail("path_escape", field)
    path = (root / raw).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        fail("binding_missing", field)
    expected = _digest(value.get("sha256"), f"{field}.sha256")
    if _sha(path) != expected:
        fail("binding_digest_drift", field)
    return path, _object(path, field)


@dataclass(frozen=True)
class P6ProspectiveRuntimeConfig:
    path: Path
    config_sha256: str
    root: Path
    bindings: Mapping[str, Path]
    historical_base_root: Path
    historical_base_manifest_sha256: str
    historical_base_content_digest: str
    historical_base_through_date: date
    correction_root: Path
    correction_manifest_sha256: str
    correction_content_digest: str
    daily_root: Path
    static_industry_mapping: Path
    static_industry_mapping_sha256: str
    history_calendar_days: int
    minimum_feature_sessions: int
    candidate_history_sessions: int
    daily_product_scope: str = "legacy-four-lane"
    legacy_daily_root: Path | None = None
    daily_core_first_date: date | None = None
    legacy_bridge_market_date: date | None = None
    legacy_bridge_package_id: str | None = None
    legacy_bridge_manifest_sha256: str | None = None


@dataclass(frozen=True)
class P6OwnerActivationMandate:
    path: Path
    mandate_id: str
    state: str
    entry_proposal_digest: str
    runtime_sha256: str
    expected_previous_production_revision: str
    activation_mode: str
    activation_conditions: tuple[str, ...]
    mandate_digest: str


@dataclass(frozen=True)
class P6ActivationDecision:
    path: Path | None
    decision_id: str
    state: str
    decision_at: datetime
    activation_market_date: date
    entry_proposal_digest: str
    runtime_sha256: str
    deployment_revision: str
    previous_production_revision: str
    disabled_rehearsal_manifest_digest: str
    rollback_drill_digest: str
    activation_mandate_digest: str
    decision_digest: str

    @property
    def enabled(self) -> bool:
        return self.state == "enabled"

    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": _ACTIVATION_SCHEMA,
            "work_item": "P6-ACTIVATE-001",
            "decision_id": self.decision_id,
            "state": self.state,
            "decision_at": self.decision_at.astimezone(timezone.utc).isoformat(),
            "activation_market_date": self.activation_market_date.isoformat(),
            "entry_proposal_digest": self.entry_proposal_digest,
            "runtime_sha256": self.runtime_sha256,
            "deployment_revision": self.deployment_revision,
            "previous_production_revision": self.previous_production_revision,
            "disabled_rehearsal_manifest_digest": self.disabled_rehearsal_manifest_digest,
            "rollback_drill_digest": self.rollback_drill_digest,
            "activation_mandate_digest": self.activation_mandate_digest,
            "approval_authority": "human",
            "official_prospective_cycle_authorized": self.enabled,
            "paper_authority_only": True,
            "notification_authorized": False,
            "broker_authorized": False,
            "live_capital_authorized": False,
            "arbitration_2022_2024_access_authorized": False,
            "permanent_blind_2025_access_authorized": False,
        }


def load_prospective_runtime_config(path: Path, *, root: Path) -> P6ProspectiveRuntimeConfig:
    root = root.resolve()
    raw = _object(path, "P6 prospective runtime")
    is_r1 = raw.get("schema_version") == _RUNTIME_R1_SCHEMA
    if (
        (raw.get("schema_version") != _RUNTIME_SCHEMA and not is_r1)
        or raw.get("work_item") != "P6-ACTIVATE-001"
        or raw.get("state") != (
            "FROZEN_PRE_ACTIVATION_RUNTIME_R1" if is_r1 else "FROZEN_PRE_ACTIVATION_RUNTIME"
        )
    ):
        fail("activation_runtime_drift", "prospective runtime identity differs")
    expected_bindings = {
        "entry_proposal",
        "data_boundary",
        "scorecards",
        "forward_worker",
        "paper_lane",
        "web_view",
        "candidate_rule",
        "trend_expert",
        "trend_evaluation_spec",
        "strategy_card",
        "feature_registry",
    }
    bindings_raw = raw.get("bindings")
    if not isinstance(bindings_raw, dict) or set(bindings_raw) != expected_bindings:
        fail("field_mismatch", "prospective runtime bindings differ")
    binding_paths: dict[str, Path] = {}
    binding_values: dict[str, dict[str, Any]] = {}
    for name in sorted(expected_bindings):
        path_value, object_value = _repo_binding(root, bindings_raw[name], f"bindings.{name}")
        binding_paths[name] = path_value
        binding_values[name] = object_value
    if binding_values["entry_proposal"].get("proposal_digest") != _ENTRY_DIGEST:
        fail("activation_runtime_drift", "entry proposal digest differs")
    if bindings_raw["candidate_rule"].get("semantic_digest") != _CANDIDATE_DIGEST:
        fail("activation_runtime_drift", "candidate-rule semantic digest differs")
    if binding_values["candidate_rule"].get("semantic_digest") != _CANDIDATE_DIGEST:
        fail("activation_runtime_drift", "candidate-rule artifact differs")
    if bindings_raw["strategy_card"].get("semantic_digest") != _STRATEGY_DIGEST:
        fail("activation_runtime_drift", "strategy semantic digest differs")
    if binding_values["strategy_card"].get("semantic_digest") != _STRATEGY_DIGEST:
        fail("activation_runtime_drift", "strategy-card artifact differs")

    seed = raw.get("historical_seed")
    daily = raw.get("prospective_daily")
    reference = raw.get("research_reference")
    view = raw.get("effective_view")
    partition = raw.get("prospective_partition")
    authority = raw.get("authority")
    if not all(isinstance(item, dict) for item in (seed, daily, reference, view, partition, authority)):
        fail("field_mismatch", "prospective runtime sections are incomplete")
    assert isinstance(seed, dict) and isinstance(daily, dict) and isinstance(reference, dict)
    assert isinstance(view, dict) and isinstance(partition, dict) and isinstance(authority, dict)
    try:
        base_through = date.fromisoformat(str(seed["base_through_date"]))
    except (KeyError, ValueError) as exc:
        raise ForwardValidationContractError("invalid_date", "historical_seed.base_through_date") from exc
    if (
        seed.get("base_package_id") != "dwd-base-202607-v2.1"
        or _digest(seed.get("base_manifest_sha256"), "base_manifest_sha256")
        != "sha256:541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b"
        or _digest(seed.get("base_content_digest"), "base_content_digest")
        != "sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9"
        or seed.get("correction_id") != "dwd-base-202607-v2.1-correction-20260813-001"
        or _digest(seed.get("correction_manifest_sha256"), "correction_manifest_sha256")
        != "sha256:4c8eeb5c0d74bbf2953f003364fd138832f15a5163254b710f9db8430767ffde"
        or _digest(seed.get("correction_content_digest"), "correction_content_digest")
        != "sha256:1c1d1f4ea906797255fbc71cc3915bb905e51e7aea202d27042e33692b72e9da"
        or base_through != date(2026, 7, 31)
    ):
        fail("activation_runtime_drift", "historical seed identity differs")
    base_root = Path(str(seed.get("base_root"))).resolve()
    correction_root = Path(str(seed.get("correction_root"))).resolve()
    env_name = str(daily.get("root_env"))
    legacy_daily_root: Path | None = None
    core_first_date: date | None = None
    legacy_bridge_date: date | None = None
    bridge_id: str | None = None
    bridge_sha: str | None = None
    if is_r1:
        if (
            env_name != "QUANTHUS_P6_DWD_DAILY_CORE_ROOT"
            or daily.get("default_root") != "/data/dwd-daily-core"
            or daily.get("package_kind") != "cn01_daily_core_dwd_increment"
            or daily.get("product_scope") != "daily-core-v1"
            or daily.get("chain_rule") != "daily_core_bridge_to_exact_legacy_predecessor"
            or daily.get("legacy_root_env") != "QUANTHUS_P6_DWD_DAILY_ROOT"
            or daily.get("legacy_default_root") != "/data/dwd-daily"
            or daily.get("daily_core_first_date") != "2026-09-24"
            or daily.get("legacy_bridge_date") != "2026-09-23"
            or daily.get("legacy_bridge_package_id")
               != "dwd-daily-20260923-31d9d426a212-ac89bfd"
            or daily.get("legacy_bridge_manifest_sha256")
               != "sha256:233d046d7d2300d46572565921089d638c078548368cada3caccd500db6671ee"
        ):
            fail("activation_runtime_drift", "P6-R1 daily-core source/bridge contract differs")
        core_first_date = date(2026, 9, 24)
        legacy_bridge_date = date(2026, 9, 23)
        bridge_id = str(daily["legacy_bridge_package_id"])
        bridge_sha = str(daily["legacy_bridge_manifest_sha256"])
        legacy_daily_root = Path(
            os.environ.get("QUANTHUS_P6_DWD_DAILY_ROOT", "/data/dwd-daily")
        ).resolve()
    elif (
        env_name != "QUANTHUS_P6_DWD_DAILY_ROOT"
        or daily.get("package_kind") != "cn01_daily_dwd_domain_increment"
        or daily.get("chain_rule") != "target_to_base_exact_prior_manifest_chain"
    ):
        fail("activation_runtime_drift", "legacy prospective daily contract differs")
    daily_root = Path(os.environ.get(env_name, str(daily.get("default_root")))).resolve()
    if (
        daily.get("successful_ready_package_implies_deferred_daily_artifacts_empty") is not True
        or set(daily.get("required_target_tables", ()))
        != {"dwd_trading_calendar", "dwd_security_bar", "dwd_tradability_state"}
    ):
        fail("activation_runtime_drift", "prospective daily required tables differ")
    mapping = Path(str(reference.get("static_industry_mapping"))).resolve()
    mapping_sha = _digest(reference.get("static_industry_mapping_sha256"), "static industry sha")
    if (
        reference.get("mapping_version") != "static-level1-concentration-research-v1"
        or reference.get("usage") != "frozen_candidate_concentration_only"
    ):
        fail("activation_runtime_drift", "industry reference contract differs")
    if (
        not isinstance(view.get("history_calendar_days"), int)
        or int(view["history_calendar_days"]) < 120
        or int(view.get("minimum_feature_sessions", 0)) != 61
        or int(view.get("candidate_history_sessions", 0)) != 22
        or view.get("build_once_per_job") is not True
    ):
        fail("activation_runtime_drift", "effective-view contract differs")
    if (
        partition.get("assignment") != "prospective"
        or partition.get("start_rule")
        != "first_complete_trading_session_strictly_after_activation_market_date"
        or partition.get("end_rule") != "current_admitted_release_market_date"
        or partition.get("purge_sessions") != 22
        or partition.get("embargo_sessions") != 22
        or partition.get("maximum_overlap_sessions") != 22
        or partition.get("sealed_historical_partitions_authorized") is not False
    ):
        fail("activation_runtime_drift", "prospective partition contract differs")
    if authority.get("pre_activation_runtime_implementation_authorized") is not True:
        fail("authority_drift", "pre-activation runtime implementation is not authorized")
    if authority.get("official_prospective_cycle_authorized") is not False:
        fail("authority_drift", "runtime config cannot activate P6")
    for name in (
        "notification_authorized",
        "broker_authorized",
        "live_capital_authorized",
        "arbitration_2022_2024_access_authorized",
        "permanent_blind_2025_access_authorized",
        "new_expert_authorized",
        "aggregator_change_authorized",
    ):
        if authority.get(name) is not False:
            fail("authority_drift", f"runtime config unexpectedly enables {name}")
    if authority.get("paper_authority_only") is not True:
        fail("authority_drift", "runtime must remain paper-only")
    return P6ProspectiveRuntimeConfig(
        path=path.resolve(),
        config_sha256=_sha(path),
        root=root,
        bindings=MappingProxyType(binding_paths),
        historical_base_root=base_root,
        historical_base_manifest_sha256=str(seed["base_manifest_sha256"]),
        historical_base_content_digest=str(seed["base_content_digest"]),
        historical_base_through_date=base_through,
        correction_root=correction_root,
        correction_manifest_sha256=str(seed["correction_manifest_sha256"]),
        correction_content_digest=str(seed["correction_content_digest"]),
        daily_root=daily_root,
        static_industry_mapping=mapping,
        static_industry_mapping_sha256=mapping_sha,
        history_calendar_days=int(view["history_calendar_days"]),
        minimum_feature_sessions=int(view["minimum_feature_sessions"]),
        candidate_history_sessions=int(view["candidate_history_sessions"]),
        daily_product_scope="daily-core-v1" if is_r1 else "legacy-four-lane",
        legacy_daily_root=legacy_daily_root,
        daily_core_first_date=core_first_date,
        legacy_bridge_market_date=legacy_bridge_date,
        legacy_bridge_package_id=bridge_id,
        legacy_bridge_manifest_sha256=bridge_sha,
    )


def load_owner_activation_mandate(
    path: Path, *, runtime: P6ProspectiveRuntimeConfig
) -> P6OwnerActivationMandate:
    raw = _object(path, "P6 owner activation mandate")
    expected = {
        "schema_version",
        "work_item",
        "mandate_id",
        "state",
        "entry_proposal_digest",
        "runtime_sha256",
        "expected_previous_production_revision",
        "activation_mode",
        "activation_conditions",
        "approval_authority",
        "paper_authority_only",
        "notification_authorized",
        "broker_authorized",
        "live_capital_authorized",
        "arbitration_2022_2024_access_authorized",
        "permanent_blind_2025_access_authorized",
        "mandate_digest",
    }
    if (
        set(raw) != expected
        or raw.get("schema_version") != _MANDATE_SCHEMA
        or raw.get("work_item") != "P6-ACTIVATE-001"
        or raw.get("state") != "OWNER_AUTHORIZED_CONDITIONAL"
        or raw.get("activation_mode") != "automatic_after_all_predeclared_gates"
    ):
        fail("activation_mandate_drift", "owner activation mandate identity differs")
    conditions_raw = raw.get("activation_conditions")
    if (
        not isinstance(conditions_raw, list)
        or not conditions_raw
        or any(not isinstance(item, str) or not item.strip() for item in conditions_raw)
        or len(conditions_raw) != len(set(conditions_raw))
    ):
        fail("activation_mandate_drift", "activation conditions are invalid")
    required_conditions = {
        "exact_origin_main_deployment_revision_is_running",
        "disabled_rehearsal_succeeds_through_real_replay_worker",
        "disabled_rehearsal_has_zero_dwd_shadow_paper_settlement_scorecard_notification_broker_live_side_effects",
        "rollback_drill_proves_disabled_admission_seeds_zero_official_jobs",
        "production_health_and_ready_are_green",
        "replay_worker_dwd_mounts_are_read_only",
        "official_authority_remains_paper_only",
    }
    if set(conditions_raw) != required_conditions:
        fail("activation_mandate_drift", "activation condition set differs")
    if raw.get("approval_authority") != "human" or raw.get("paper_authority_only") is not True:
        fail("activation_mandate_authority_drift", "owner mandate authority differs")
    for name in (
        "notification_authorized",
        "broker_authorized",
        "live_capital_authorized",
        "arbitration_2022_2024_access_authorized",
        "permanent_blind_2025_access_authorized",
    ):
        if raw.get(name) is not False:
            fail("activation_mandate_authority_drift", f"mandate unexpectedly enables {name}")
    entry = _digest(raw.get("entry_proposal_digest"), "entry_proposal_digest")
    runtime_sha = _digest(raw.get("runtime_sha256"), "runtime_sha256")
    previous = _sha40(
        raw.get("expected_previous_production_revision"),
        "expected_previous_production_revision",
    )
    if entry != _ENTRY_DIGEST or runtime_sha != runtime.config_sha256:
        fail("binding_digest_drift", "owner mandate binds another P6 runtime")
    semantic = {key: value for key, value in raw.items() if key != "mandate_digest"}
    mandate_digest = _digest(raw.get("mandate_digest"), "mandate_digest")
    if mandate_digest != digest_value(semantic):
        fail("digest_mismatch", "owner activation mandate digest differs")
    return P6OwnerActivationMandate(
        path=path.resolve(),
        mandate_id=str(raw.get("mandate_id")),
        state="OWNER_AUTHORIZED_CONDITIONAL",
        entry_proposal_digest=entry,
        runtime_sha256=runtime_sha,
        expected_previous_production_revision=previous,
        activation_mode="automatic_after_all_predeclared_gates",
        activation_conditions=tuple(str(item) for item in conditions_raw),
        mandate_digest=mandate_digest,
    )


def activation_decision_from_mapping(
    raw: Mapping[str, object],
    *,
    runtime: P6ProspectiveRuntimeConfig,
    path: Path | None = None,
    expected_mandate_digest: str | None = None,
) -> P6ActivationDecision:
    expected = {
        "schema_version",
        "work_item",
        "decision_id",
        "state",
        "decision_at",
        "activation_market_date",
        "entry_proposal_digest",
        "runtime_sha256",
        "deployment_revision",
        "previous_production_revision",
        "disabled_rehearsal_manifest_digest",
        "rollback_drill_digest",
        "activation_mandate_digest",
        "approval_authority",
        "official_prospective_cycle_authorized",
        "paper_authority_only",
        "notification_authorized",
        "broker_authorized",
        "live_capital_authorized",
        "arbitration_2022_2024_access_authorized",
        "permanent_blind_2025_access_authorized",
        "decision_digest",
    }
    if set(raw) != expected or raw.get("schema_version") != _ACTIVATION_SCHEMA or raw.get("work_item") != "P6-ACTIVATE-001":
        fail("activation_decision_drift", "activation decision fields/identity differ")
    state = str(raw.get("state"))
    if state not in {"enabled", "paused", "disabled"}:
        fail("activation_decision_drift", "activation state is invalid")
    decision_at = _instant(raw.get("decision_at"), "decision_at")
    try:
        market_date = date.fromisoformat(str(raw.get("activation_market_date")))
    except ValueError as exc:
        raise ForwardValidationContractError("invalid_date", "activation_market_date") from exc
    if decision_at.astimezone(_MARKET_TZ).date() != market_date:
        fail("activation_decision_drift", "activation market date differs from Asia/Shanghai decision date")
    decision = P6ActivationDecision(
        path=None if path is None else path.resolve(),
        decision_id=str(raw.get("decision_id")),
        state=state,
        decision_at=decision_at,
        activation_market_date=market_date,
        entry_proposal_digest=_digest(raw.get("entry_proposal_digest"), "entry_proposal_digest"),
        runtime_sha256=_digest(raw.get("runtime_sha256"), "runtime_sha256"),
        deployment_revision=_sha40(raw.get("deployment_revision"), "deployment_revision"),
        previous_production_revision=_sha40(raw.get("previous_production_revision"), "previous_production_revision"),
        disabled_rehearsal_manifest_digest=_digest(raw.get("disabled_rehearsal_manifest_digest"), "disabled_rehearsal_manifest_digest"),
        rollback_drill_digest=_digest(
            raw.get("rollback_drill_digest"), "rollback_drill_digest"
        ),
        activation_mandate_digest=_digest(
            raw.get("activation_mandate_digest"), "activation_mandate_digest"
        ),
        decision_digest=_digest(raw.get("decision_digest"), "decision_digest"),
    )
    if (
        decision.entry_proposal_digest != _ENTRY_DIGEST
        or decision.runtime_sha256 != runtime.config_sha256
    ):
        fail("binding_digest_drift", "activation decision binds another P6 runtime")
    if (
        expected_mandate_digest is not None
        and decision.activation_mandate_digest != expected_mandate_digest
    ):
        fail("binding_digest_drift", "activation decision binds another owner mandate")
    if raw.get("approval_authority") != "human" or raw.get("paper_authority_only") is not True:
        fail("activation_authority_drift", "activation decision authority differs")
    if raw.get("official_prospective_cycle_authorized") is not decision.enabled:
        fail("activation_authority_drift", "official-cycle authority does not match activation state")
    for name in (
        "notification_authorized",
        "broker_authorized",
        "live_capital_authorized",
        "arbitration_2022_2024_access_authorized",
        "permanent_blind_2025_access_authorized",
    ):
        if raw.get(name) is not False:
            fail("activation_authority_drift", f"activation unexpectedly enables {name}")
    if decision.decision_digest != digest_value(decision.semantic_payload()):
        fail("digest_mismatch", "activation decision digest differs")
    return decision


def load_activation_decision(
    path: Path,
    *,
    runtime: P6ProspectiveRuntimeConfig,
    expected_mandate_digest: str | None = None,
) -> P6ActivationDecision:
    return activation_decision_from_mapping(
        _object(path, "P6 activation decision"),
        runtime=runtime,
        path=path,
        expected_mandate_digest=expected_mandate_digest,
    )


def make_activation_decision_payload(
    *,
    runtime: P6ProspectiveRuntimeConfig,
    mandate: P6OwnerActivationMandate,
    decision_id: str,
    state: str,
    decision_at: datetime,
    deployment_revision: str,
    disabled_rehearsal_manifest_digest: str,
    rollback_drill_digest: str,
) -> dict[str, object]:
    if state not in {"enabled", "paused", "disabled"}:
        fail("activation_decision_drift", "activation state is invalid")
    if mandate.runtime_sha256 != runtime.config_sha256:
        fail("binding_digest_drift", "owner mandate binds another runtime")
    prototype = P6ActivationDecision(
        path=None,
        decision_id=decision_id,
        state=state,
        decision_at=decision_at,
        activation_market_date=decision_at.astimezone(_MARKET_TZ).date(),
        entry_proposal_digest=_ENTRY_DIGEST,
        runtime_sha256=runtime.config_sha256,
        deployment_revision=_sha40(deployment_revision, "deployment_revision"),
        previous_production_revision=mandate.expected_previous_production_revision,
        disabled_rehearsal_manifest_digest=_digest(
            disabled_rehearsal_manifest_digest,
            "disabled_rehearsal_manifest_digest",
        ),
        rollback_drill_digest=_digest(rollback_drill_digest, "rollback_drill_digest"),
        activation_mandate_digest=mandate.mandate_digest,
        decision_digest="sha256:" + "0" * 64,
    )
    semantic = prototype.semantic_payload()
    payload = {**semantic, "decision_digest": digest_value(semantic)}
    activation_decision_from_mapping(
        payload,
        runtime=runtime,
        expected_mandate_digest=mandate.mandate_digest,
    )
    return payload


def runtime_code_revision(
    *,
    revision_file: Path = Path("/app/config/runtime-code-revision.txt"),
) -> str | None:
    configured = os.environ.get("QUANTHUS_CODE_REVISION", "").strip()
    if configured:
        return _sha40(configured, "QUANTHUS_CODE_REVISION")
    try:
        value = revision_file.read_text(encoding="ascii").strip()
    except OSError:
        return None
    return _sha40(value, "runtime-code-revision")
