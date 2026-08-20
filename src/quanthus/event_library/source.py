"""Strict source authority and development-only availability projection for P4 scans."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.event_library.contracts import require_sha256_digest, sha256_digest
from quanthus.pipelines.securities_warehouse.domain_base import (
    DomainBaseError,
    DomainBaseResult,
    verify_domain_base,
)


class EventScanSourceError(ValueError):
    """Raised when a P4 scan source is mutable, legacy, incomplete, or unbound."""


_SEMVER_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_EXPECTED_AUTHORITY = "direct_dwd_v2_1_plus_correction"
_EXPECTED_STATUS = "approved_development_only"
_EXPECTED_INCREMENT_MODE = "disabled_for_initial_development_scan"
_EXPECTED_FORBIDDEN_SOURCES = (
    "legacy_dual_lane_dwd",
    "legacy_dual_lane_dws",
    "legacy_operational_ads",
    "legacy_strategy_or_portable_ads",
    "legacy_release_set",
    "mutable_current_pointer",
)
_EXPECTED_FORBIDDEN_USES = (
    "arbitration",
    "permanent_blind",
    "simulation",
    "governance_admission",
    "execution_authorization",
)
_EXPECTED_PROJECTION_RULES = (
    (
        "dwd_security_bar",
        "candidate_signal_input",
        "session_close",
    ),
    (
        "dwd_listing",
        "eligibility_input",
        "effective_interval_at_session_close",
    ),
    (
        "dwd_security",
        "eligibility_input",
        "effective_interval_at_session_close",
    ),
    (
        "dwd_trading_calendar",
        "session_coordinate",
        "session_close",
    ),
    (
        "dwd_tradability_state",
        "eligibility_input",
        "session_close_after_correction_overlay",
    ),
    (
        "dwd_price_limit_regime",
        "t_plus_1_execution_input",
        "effective_interval_at_execution_open",
    ),
    (
        "dwd_adjustment_factor",
        "candidate_exclusion_only",
        "effective_date_conservative_exclusion",
    ),
)


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise EventScanSourceError(f"{path} must be an object")
    return value


def _sequence(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise EventScanSourceError(f"{path} must be an array")
    return value


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventScanSourceError(f"{path} must be a non-empty string")
    return value.strip()


def _date(value: object, path: str) -> date:
    try:
        return date.fromisoformat(_text(value, path))
    except ValueError as exc:
        raise EventScanSourceError(f"{path} must be an ISO date") from exc


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise EventScanSourceError(
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}"
        )


def _unique_text_tuple(value: object, path: str) -> tuple[str, ...]:
    items = tuple(
        _text(item, f"{path}[{index}]")
        for index, item in enumerate(_sequence(value, path))
    )
    if not items or len(items) != len(set(items)):
        raise EventScanSourceError(f"{path} must contain unique non-empty values")
    return items


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EventScanSourceError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True)
class DwdBaseBinding:
    package_id: str
    manifest_sha256: str
    content_digest: str
    kind: str
    through_date: date

    def __post_init__(self) -> None:
        require_sha256_digest(self.manifest_sha256, "base_release.manifest_sha256")
        require_sha256_digest(self.content_digest, "base_release.content_digest")
        if self.kind != "dwd_domain_base_package":
            raise EventScanSourceError("P4 source must be a DWD domain Base package")
        if self.package_id != "dwd-base-202607-v2.1":
            raise EventScanSourceError("P4 source must bind the accepted DWD Base V2.1 package")

    def to_dict(self) -> dict[str, str]:
        return {
            "package_id": self.package_id,
            "manifest_sha256": self.manifest_sha256,
            "content_digest": self.content_digest,
            "kind": self.kind,
            "through_date": self.through_date.isoformat(),
        }


@dataclass(frozen=True)
class DwdCorrectionBinding:
    correction_id: str
    manifest_sha256: str
    content_digest: str

    def __post_init__(self) -> None:
        require_sha256_digest(self.manifest_sha256, "correction_release.manifest_sha256")
        require_sha256_digest(self.content_digest, "correction_release.content_digest")
        if self.correction_id != "dwd-base-202607-v2.1-correction-20260813-001":
            raise EventScanSourceError("P4 source must bind accepted DWD correction-001")

    def to_dict(self) -> dict[str, str]:
        return {
            "correction_id": self.correction_id,
            "manifest_sha256": self.manifest_sha256,
            "content_digest": self.content_digest,
        }


@dataclass(frozen=True)
class DevelopmentPartition:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise EventScanSourceError("development partition start must not follow end")
        if self.start != date(2012, 1, 1) or self.end != date(2021, 12, 31):
            raise EventScanSourceError("P4 initial scan must remain inside 2012-2021 development")

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True)
class HistoricalProjectionRule:
    table: str
    usage: str
    economic_availability: str
    raw_knowledge_retained: bool

    def __post_init__(self) -> None:
        if not self.table or not self.usage or not self.economic_availability:
            raise EventScanSourceError("historical projection rule fields must not be empty")
        if self.raw_knowledge_retained is not True:
            raise EventScanSourceError("P4 projection must retain raw DWD knowledge lineage")

    def to_dict(self) -> dict[str, object]:
        return {
            "table": self.table,
            "usage": self.usage,
            "economic_availability": self.economic_availability,
            "raw_knowledge_retained": self.raw_knowledge_retained,
        }


@dataclass(frozen=True)
class HistoricalVisibilityPolicy:
    status: str
    projection_id: str
    per_decision_publication_proven: bool
    rules: tuple[HistoricalProjectionRule, ...]
    forbidden_shortcuts: tuple[str, ...]
    forbidden_uses: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status != "approved_development_retrospective":
            raise EventScanSourceError(
                "historical visibility must be approved only for retrospective development"
            )
        if self.projection_id != "p4-economic-availability-development-v1":
            raise EventScanSourceError("unexpected P4 economic-availability projection")
        if self.per_decision_publication_proven is not False:
            raise EventScanSourceError(
                "retrospective DWD projection must not claim historical publication proof"
            )
        observed = tuple(
            (rule.table, rule.usage, rule.economic_availability) for rule in self.rules
        )
        if observed != _EXPECTED_PROJECTION_RULES:
            raise EventScanSourceError("historical visibility projection rules differ")
        if len({rule.table for rule in self.rules}) != len(self.rules):
            raise EventScanSourceError("historical visibility projection tables must be unique")
        if self.forbidden_uses != _EXPECTED_FORBIDDEN_USES:
            raise EventScanSourceError("historical visibility forbidden uses differ")
        required_shortcuts = {
            "use_raw_dwd_reconstruction_time_as_t_close_visibility",
            "drop_raw_knowledge_from_from_projection_lineage",
            "reuse_legacy_ads_or_historical_bundle_as_p4_authority",
            "open_non_development_partition",
        }
        if set(self.forbidden_shortcuts) != required_shortcuts:
            raise EventScanSourceError("historical visibility forbidden shortcuts differ")

    def require_full_scan_authority(self) -> None:
        """Authorize only the development retrospective scan, never a stronger PIT claim."""

        if self.status != "approved_development_retrospective":
            raise EventScanSourceError("development historical projection is not approved")

    def rule_for(self, table: str) -> HistoricalProjectionRule:
        for rule in self.rules:
            if rule.table == table:
                return rule
        raise EventScanSourceError(f"table {table} has no P4 availability projection rule")

    def project_candidate_knowledge(
        self,
        *,
        table: str,
        session_close: datetime,
        effective_from: datetime | None = None,
    ) -> datetime:
        """Return the development-only economic-availability coordinate for candidate inputs.

        Raw DWD ``knowledge_from`` remains lineage and is never rewritten in the Base. This method
        supplies the separate P4 projection coordinate used by the frozen candidate interface.
        """

        _require_aware(session_close, "session_close")
        rule = self.rule_for(table)
        if rule.economic_availability in {
            "session_close",
            "session_close_after_correction_overlay",
        }:
            return session_close
        if rule.economic_availability in {
            "effective_interval_at_session_close",
            "effective_date_conservative_exclusion",
        }:
            if effective_from is None:
                raise EventScanSourceError(
                    f"{table} projection requires an effective_from coordinate"
                )
            _require_aware(effective_from, "effective_from")
            if effective_from > session_close:
                raise EventScanSourceError(
                    f"{table} effective coordinate is after the candidate session close"
                )
            return effective_from
        raise EventScanSourceError(
            f"{table} projection rule is not a candidate T-close availability rule"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "projection_id": self.projection_id,
            "per_decision_publication_proven": self.per_decision_publication_proven,
            "projection_rules": [rule.to_dict() for rule in self.rules],
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "forbidden_uses": list(self.forbidden_uses),
        }


@dataclass(frozen=True)
class StaticIndustryReference:
    version: str
    usage: str
    authority: str
    source_tables: tuple[str, ...]
    sha256: str | None = None

    def __post_init__(self) -> None:
        if self.version != "static-level1-concentration-research-v1":
            raise EventScanSourceError("unexpected static industry mapping version")
        if self.usage != "static_non_pit_concentration_only":
            raise EventScanSourceError("industry mapping may only be used for concentration")
        if self.authority == "legacy_checksum_bound_reference":
            if self.source_tables:
                raise EventScanSourceError("legacy industry reference must not declare DWD tables")
            if self.sha256 is None:
                raise EventScanSourceError("legacy industry reference requires sha256")
            require_sha256_digest(self.sha256, "static_industry_reference.sha256")
            return
        if self.authority != "dwd_v2_1_static_company_industry_projection":
            raise EventScanSourceError("unexpected static industry authority")
        expected = ("dwd_company_industry_membership", "dwd_industry_node")
        if self.source_tables != expected:
            raise EventScanSourceError("DWD static industry source tables differ")
        if self.sha256 is not None:
            raise EventScanSourceError("DWD static industry projection must not bind legacy sha256")

    def to_dict(self) -> dict[str, object]:
        if self.authority == "legacy_checksum_bound_reference":
            assert self.sha256 is not None
            return {"version": self.version, "usage": self.usage, "sha256": self.sha256}
        return {
            "version": self.version,
            "usage": self.usage,
            "authority": self.authority,
            "source_tables": list(self.source_tables),
        }


@dataclass(frozen=True)
class EventScanSourcePolicy:
    path: Path
    schema_version: str
    policy_id: str
    policy_version: str
    status: str
    authority: str
    provider_config: str
    runtime_root_env: str
    correction_root_env: str
    base_release: DwdBaseBinding
    correction_release: DwdCorrectionBinding
    development_partition: DevelopmentPartition
    historical_visibility: HistoricalVisibilityPolicy
    static_industry_reference: StaticIndustryReference
    daily_increment_mode: str
    forbidden_sources: tuple[str, ...]
    required_tables: tuple[str, ...]
    decision_reference: str
    semantic_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != "2.0.0":
            raise EventScanSourceError("event scan source schema_version must be 2.0.0")
        if _SEMVER_PATTERN.fullmatch(self.policy_version) is None:
            raise EventScanSourceError(
                "event scan source policy_version must use semantic versioning"
            )
        if self.status != _EXPECTED_STATUS:
            raise EventScanSourceError("P4 scan source must remain development-only")
        if self.authority != _EXPECTED_AUTHORITY:
            raise EventScanSourceError("P4 scan authority must be DWD V2.1 plus correction-001")
        if self.provider_config != "config/replay-data-provider.development.v2.json":
            raise EventScanSourceError(
                "P4 scan must reuse the accepted direct DWD provider contract"
            )
        for field_name, value in (
            ("runtime_root_env", self.runtime_root_env),
            ("correction_root_env", self.correction_root_env),
        ):
            if not re.fullmatch(r"[A-Z][A-Z0-9_]+", value):
                raise EventScanSourceError(
                    f"{field_name} must be an uppercase environment variable"
                )
        if self.daily_increment_mode != _EXPECTED_INCREMENT_MODE:
            raise EventScanSourceError(
                "initial development scan must not depend on post-2021 daily increments"
            )
        if self.forbidden_sources != _EXPECTED_FORBIDDEN_SOURCES:
            raise EventScanSourceError(
                "forbidden source list does not match the P4 authority decision"
            )
        required = {
            "dwd_security",
            "dwd_listing",
            "dwd_trading_calendar",
            "dwd_security_bar",
            "dwd_tradability_state",
            "dwd_price_limit_regime",
            "dwd_adjustment_factor",
            *self.static_industry_reference.source_tables,
        }
        if not required.issubset(self.required_tables):
            missing = sorted(required.difference(self.required_tables))
            raise EventScanSourceError(f"required DWD tables are missing: {missing}")
        require_sha256_digest(self.semantic_digest, "semantic_digest")

    @property
    def computed_semantic_digest(self) -> str:
        return sha256_digest(self.semantic_payload())

    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "status": self.status,
            "authority": self.authority,
            "provider_config": self.provider_config,
            "runtime_root_env": self.runtime_root_env,
            "correction_root_env": self.correction_root_env,
            "base_release": self.base_release.to_dict(),
            "correction_release": self.correction_release.to_dict(),
            "development_partition": self.development_partition.to_dict(),
            "historical_visibility": self.historical_visibility.to_dict(),
            "static_industry_reference": self.static_industry_reference.to_dict(),
            "daily_increment_mode": self.daily_increment_mode,
            "forbidden_sources": list(self.forbidden_sources),
            "required_tables": list(self.required_tables),
        }

    def require_development_date(self, value: date) -> None:
        if not self.development_partition.contains(value):
            raise EventScanSourceError(
                f"date {value.isoformat()} is outside the authorized development partition"
            )

    def resolve_runtime_root(self, environment: Mapping[str, str] | None = None) -> Path:
        values = os.environ if environment is None else environment
        raw = values.get(self.runtime_root_env)
        if raw is None or not raw.strip():
            raise EventScanSourceError(
                f"set {self.runtime_root_env} to the physical DWD Base V2.1 package"
            )
        root = Path(raw)
        if not root.is_absolute():
            raise EventScanSourceError("DWD Base runtime root must be absolute")
        if root.name != self.base_release.package_id:
            raise EventScanSourceError("DWD Base runtime root package id differs from policy")
        if root.is_symlink() or not root.is_dir():
            raise EventScanSourceError("DWD Base runtime root must be a physical directory")
        return root.resolve()

    def resolve_correction_root(self, environment: Mapping[str, str] | None = None) -> Path:
        values = os.environ if environment is None else environment
        raw = values.get(self.correction_root_env)
        if raw is None or not raw.strip():
            raise EventScanSourceError(
                f"set {self.correction_root_env} to the accepted DWD correction-001 package"
            )
        root = Path(raw)
        if not root.is_absolute():
            raise EventScanSourceError("DWD correction runtime root must be absolute")
        if root.name != self.correction_release.correction_id:
            raise EventScanSourceError("DWD correction runtime root differs from policy")
        if root.is_symlink() or not root.is_dir():
            raise EventScanSourceError("DWD correction runtime root must be a physical directory")
        return root.resolve()


def _validate_release_config(policy: EventScanSourcePolicy, release_path: Path) -> None:
    try:
        value = json.loads(release_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventScanSourceError(f"cannot read release identity: {release_path}") from exc
    release = _mapping(value, "release")
    layers = _mapping(release.get("layers"), "release.layers")
    dwd = _mapping(layers.get("dwd"), "release.layers.dwd")
    expected = policy.base_release
    observed = (
        dwd.get("package_id"),
        dwd.get("manifest_sha256"),
        dwd.get("content_digest"),
        release.get("through_date"),
    )
    required = (
        expected.package_id,
        expected.manifest_sha256,
        expected.content_digest,
        expected.through_date.isoformat(),
    )
    if observed != required:
        raise EventScanSourceError("P4 source policy disagrees with the V2.1 release authority")


def _validate_provider_config(policy: EventScanSourcePolicy, provider_path: Path) -> None:
    try:
        value = json.loads(provider_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventScanSourceError(
            f"cannot read replay provider identity: {provider_path}"
        ) from exc
    provider = _mapping(value, "replay_provider")
    if (
        provider.get("schema_version") != "quanthus.replay-data-provider.v2"
        or provider.get("mode") != "direct-dwd-job-local"
        or provider.get("research_only") is not True
        or provider.get("execution_authorized") is not False
    ):
        raise EventScanSourceError(
            "P4 source requires the accepted research-only direct DWD provider"
        )
    scope = _mapping(provider.get("scope"), "replay_provider.scope")
    if (
        scope.get("partition") != "development"
        or scope.get("reader_role") != "development_research"
        or scope.get("decision_start") != "2012-01-04"
        or scope.get("decision_end") != "2021-12-30"
        or scope.get("execution_end") != "2021-12-31"
    ):
        raise EventScanSourceError(
            "direct DWD provider development scope differs from P4 authority"
        )
    dwd = _mapping(provider.get("dwd_authority"), "replay_provider.dwd_authority")
    expected_base = policy.base_release
    if (
        dwd.get("root_env") != policy.runtime_root_env
        or dwd.get("package_id") != expected_base.package_id
        or dwd.get("manifest_sha256") != expected_base.manifest_sha256
        or dwd.get("content_digest") != expected_base.content_digest
        or dwd.get("through_date") != expected_base.through_date.isoformat()
        or dwd.get("relationship_to_replay_source") != "direct-job-local-source"
    ):
        raise EventScanSourceError("P4 Base identity differs from the direct DWD provider")
    correction = _mapping(provider.get("dwd_correction"), "replay_provider.dwd_correction")
    expected_correction = policy.correction_release
    if (
        correction.get("root_env") != policy.correction_root_env
        or correction.get("correction_id") != expected_correction.correction_id
        or correction.get("manifest_sha256") != expected_correction.manifest_sha256
        or correction.get("content_digest") != expected_correction.content_digest
    ):
        raise EventScanSourceError("P4 correction identity differs from the direct DWD provider")
    references = _mapping(
        provider.get("research_references"), "replay_provider.research_references"
    )
    industry = _mapping(references.get("industry_mapping"), "research_references.industry_mapping")
    if policy.static_industry_reference.sha256 is not None and (
        industry.get("sha256") != policy.static_industry_reference.sha256
    ):
        raise EventScanSourceError(
            "P4 legacy industry mapping digest differs from the direct DWD provider"
        )
    materialization = _mapping(provider.get("materialization"), "replay_provider.materialization")
    if (
        materialization.get("record_view") != "retrospective_dwd_record_view"
        or materialization.get("per_decision_publication_proven") is not False
        or materialization.get("job_local_projection") is not True
    ):
        raise EventScanSourceError("direct DWD materialization overstates historical publication")


def load_event_scan_source_policy(
    path: Path,
    *,
    release_path: Path | None = None,
    provider_path: Path | None = None,
) -> EventScanSourcePolicy:
    """Load the approved P4 development source policy without mutable-pointer defaults."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EventScanSourceError(f"cannot read event scan source policy: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EventScanSourceError(f"invalid event scan source policy JSON: {exc}") from exc
    raw = _mapping(value, "event_scan_source")
    _exact_keys(
        raw,
        {
            "schema_version",
            "policy_id",
            "policy_version",
            "status",
            "authority",
            "provider_config",
            "runtime_root_env",
            "correction_root_env",
            "base_release",
            "correction_release",
            "development_partition",
            "historical_visibility",
            "static_industry_reference",
            "daily_increment_mode",
            "forbidden_sources",
            "required_tables",
            "decision_reference",
            "semantic_digest",
        },
        "event_scan_source",
    )
    base = _mapping(raw["base_release"], "base_release")
    _exact_keys(
        base,
        {"package_id", "manifest_sha256", "content_digest", "kind", "through_date"},
        "base_release",
    )
    correction = _mapping(raw["correction_release"], "correction_release")
    _exact_keys(
        correction,
        {"correction_id", "manifest_sha256", "content_digest"},
        "correction_release",
    )
    development = _mapping(raw["development_partition"], "development_partition")
    _exact_keys(development, {"start", "end"}, "development_partition")
    historical = _mapping(raw["historical_visibility"], "historical_visibility")
    _exact_keys(
        historical,
        {
            "status",
            "projection_id",
            "per_decision_publication_proven",
            "projection_rules",
            "forbidden_shortcuts",
            "forbidden_uses",
        },
        "historical_visibility",
    )
    rules: list[HistoricalProjectionRule] = []
    for index, item in enumerate(_sequence(historical["projection_rules"], "projection_rules")):
        rule = _mapping(item, f"projection_rules[{index}]")
        _exact_keys(
            rule,
            {"table", "usage", "economic_availability", "raw_knowledge_retained"},
            f"projection_rules[{index}]",
        )
        rules.append(
            HistoricalProjectionRule(
                table=_text(rule["table"], f"projection_rules[{index}].table"),
                usage=_text(rule["usage"], f"projection_rules[{index}].usage"),
                economic_availability=_text(
                    rule["economic_availability"],
                    f"projection_rules[{index}].economic_availability",
                ),
                raw_knowledge_retained=rule["raw_knowledge_retained"] is True,
            )
        )
    static_industry = _mapping(raw["static_industry_reference"], "static_industry_reference")
    if set(static_industry) == {"version", "usage", "sha256"}:
        static_industry_authority = "legacy_checksum_bound_reference"
        static_industry_tables: tuple[str, ...] = ()
        static_industry_sha256: str | None = _text(
            static_industry["sha256"], "static_industry_reference.sha256"
        )
    else:
        _exact_keys(
            static_industry,
            {"version", "usage", "authority", "source_tables"},
            "static_industry_reference",
        )
        static_industry_authority = _text(
            static_industry["authority"], "static_industry_reference.authority"
        )
        static_industry_tables = _unique_text_tuple(
            static_industry["source_tables"], "static_industry_reference.source_tables"
        )
        static_industry_sha256 = None
    result = EventScanSourcePolicy(
        path=path.resolve(),
        schema_version=_text(raw["schema_version"], "schema_version"),
        policy_id=_text(raw["policy_id"], "policy_id"),
        policy_version=_text(raw["policy_version"], "policy_version"),
        status=_text(raw["status"], "status"),
        authority=_text(raw["authority"], "authority"),
        provider_config=_text(raw["provider_config"], "provider_config"),
        runtime_root_env=_text(raw["runtime_root_env"], "runtime_root_env"),
        correction_root_env=_text(raw["correction_root_env"], "correction_root_env"),
        base_release=DwdBaseBinding(
            package_id=_text(base["package_id"], "base_release.package_id"),
            manifest_sha256=_text(base["manifest_sha256"], "base_release.manifest_sha256"),
            content_digest=_text(base["content_digest"], "base_release.content_digest"),
            kind=_text(base["kind"], "base_release.kind"),
            through_date=_date(base["through_date"], "base_release.through_date"),
        ),
        correction_release=DwdCorrectionBinding(
            correction_id=_text(correction["correction_id"], "correction_release.correction_id"),
            manifest_sha256=_text(
                correction["manifest_sha256"], "correction_release.manifest_sha256"
            ),
            content_digest=_text(
                correction["content_digest"], "correction_release.content_digest"
            ),
        ),
        development_partition=DevelopmentPartition(
            start=_date(development["start"], "development_partition.start"),
            end=_date(development["end"], "development_partition.end"),
        ),
        historical_visibility=HistoricalVisibilityPolicy(
            status=_text(historical["status"], "historical_visibility.status"),
            projection_id=_text(
                historical["projection_id"], "historical_visibility.projection_id"
            ),
            per_decision_publication_proven=historical["per_decision_publication_proven"] is True,
            rules=tuple(rules),
            forbidden_shortcuts=_unique_text_tuple(
                historical["forbidden_shortcuts"],
                "historical_visibility.forbidden_shortcuts",
            ),
            forbidden_uses=_unique_text_tuple(
                historical["forbidden_uses"], "historical_visibility.forbidden_uses"
            ),
        ),
        static_industry_reference=StaticIndustryReference(
            version=_text(static_industry["version"], "static_industry_reference.version"),
            usage=_text(static_industry["usage"], "static_industry_reference.usage"),
            authority=static_industry_authority,
            source_tables=static_industry_tables,
            sha256=static_industry_sha256,
        ),
        daily_increment_mode=_text(raw["daily_increment_mode"], "daily_increment_mode"),
        forbidden_sources=_unique_text_tuple(raw["forbidden_sources"], "forbidden_sources"),
        required_tables=_unique_text_tuple(raw["required_tables"], "required_tables"),
        decision_reference=_text(raw["decision_reference"], "decision_reference"),
        semantic_digest=_text(raw["semantic_digest"], "semantic_digest"),
    )
    if result.semantic_digest != result.computed_semantic_digest:
        raise EventScanSourceError("event scan source semantic digest mismatch")
    if release_path is not None:
        _validate_release_config(result, release_path)
    if provider_path is not None:
        _validate_provider_config(result, provider_path)
    return result


def verify_event_scan_base(
    policy: EventScanSourcePolicy,
    *,
    environment: Mapping[str, str] | None = None,
) -> DomainBaseResult:
    """Deeply verify the exact physical DWD Base V2.1 selected for the scan."""

    root = policy.resolve_runtime_root(environment)
    try:
        result = verify_domain_base(root)
    except (OSError, DomainBaseError) as exc:
        raise EventScanSourceError("DWD Base V2.1 verification failed") from exc
    expected = policy.base_release
    if (
        result.package_id != expected.package_id
        or result.manifest_sha256 != expected.manifest_sha256
        or result.content_digest != expected.content_digest
    ):
        raise EventScanSourceError("physical DWD Base identity differs from approved source policy")
    try:
        manifest_value = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventScanSourceError("verified DWD Base manifest became unreadable") from exc
    manifest = _mapping(manifest_value, "dwd_base_manifest")
    row_counts = _mapping(manifest.get("row_counts"), "dwd_base_manifest.row_counts")
    missing = sorted(set(policy.required_tables).difference(row_counts))
    if missing:
        raise EventScanSourceError(f"physical DWD Base lacks required tables: {missing}")
    return result
