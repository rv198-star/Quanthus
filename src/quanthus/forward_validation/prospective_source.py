# ruff: noqa: E501
"""Prospective P6 effective-view construction over Base V2.1 plus immutable daily DWD releases.

The module deliberately does not reuse the P4 development source/split authority.  It reuses the
accepted data transforms and candidate semantics after building one P6-specific 2026 effective
view whose target release has passed the P6 visibility/admission boundary.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.backtest.baseline_research import BaselineFeaturePanel, BaselineFeaturePanelReport
from quanthus.backtest.direct_dwd_provider import _materialize as _materialize_base_projection
from quanthus.backtest.historical_bundle import HistoricalReplayBundle
from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest
from quanthus.event_library.contracts import EventRecord, SplitAssignment, event_record_from_dict
from quanthus.event_library.physical_scan import iter_projected_candidate_series
from quanthus.event_library.rule_card import CandidateRuleCard, load_candidate_rule_card
from quanthus.event_library.scan import CandidateScanResult, scan_forward_candidate_series
from quanthus.feature_registry import FeatureRegistry, load_feature_registry
from quanthus.forward_validation.activation import P6ActivationDecision, P6ProspectiveRuntimeConfig
from quanthus.forward_validation.contracts import ForwardValidationContractError, fail
from quanthus.forward_validation.data_boundary import (
    P6ActivationBoundary,
    P6ReleaseAdmission,
    ProspectiveDwdRelease,
    classify_release_admission,
)
from quanthus.forward_validation.package_locator import resolve_daily_package
from quanthus.forward_validation.serialization import canonical_json_bytes, digest_value
from quanthus.industry_mapping import StaticIndustryMapping, load_static_industry_mapping
from quanthus.pipelines.securities_warehouse.daily_core_domain import (
    verify_daily_domain_increment as verify_daily_core_increment,
)
from quanthus.pipelines.securities_warehouse.daily_domain import (
    DailyDomainBuildError,
    verify_daily_domain_increment,
)

_MARKET_TZ = ZoneInfo("Asia/Shanghai")
_DATASETS = (
    "corporate_action_exceptions",
    "corporate_actions_normalized",
    "daily_ohlcv_none",
    "daily_universe",
    "price_limit_execution_daily",
    "security_code_validity",
    "trading_calendar",
)


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


def _sql_path(path: Path) -> str:
    return "'" + str(path.resolve()).replace("'", "''") + "'"


def _sql_paths(paths: Sequence[Path]) -> str:
    if not paths:
        fail("prospective_daily_chain_incomplete", "no daily package table paths")
    return "[" + ",".join(_sql_path(path) for path in paths) + "]"


def _copy(connection: Any, query: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    connection.execute(
        f"COPY ({query}) TO {_sql_path(output)} "
        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 50000)"
    )


def _market_close(value: date) -> datetime:
    return datetime.combine(value, time(15, 0), tzinfo=_MARKET_TZ)


@dataclass(frozen=True)
class P6DailyChainPackage:
    root: Path
    package_id: str
    market_date: date
    manifest_sha256: str
    content_digest: str
    code_revision: str
    covered_after_date: date
    prior_package_id: str | None
    prior_manifest_sha256: str | None


@dataclass(frozen=True)
class P6ProspectiveVisibility:
    projection_id: str = "p6-prospective-effective-view-v1"

    def project_candidate_knowledge(
        self,
        *,
        table: str,
        session_close: datetime,
        effective_from: datetime | None = None,
    ) -> datetime:
        if table not in {
            "dwd_security_bar",
            "dwd_tradability_state",
            "dwd_security",
            "dwd_listing",
            "dwd_adjustment_factor",
        }:
            fail("prospective_source_table_not_admitted", table)
        if session_close.tzinfo is None or session_close.utcoffset() is None:
            fail("invalid_instant", "session_close")
        if effective_from is not None:
            if effective_from.tzinfo is None or effective_from.utcoffset() is None:
                fail("invalid_instant", "effective_from")
            if effective_from > session_close:
                fail("post_cutoff_evidence_violation", table)
        return session_close


@dataclass(frozen=True)
class P6ProspectiveSourcePolicy:
    activation_market_date: date
    signal_market_date: date
    history_start: date
    semantic_digest: str
    historical_visibility: P6ProspectiveVisibility = P6ProspectiveVisibility()

    def require_stage_b_authority(self) -> None:
        return None

    def require_scan_date(self, value: date) -> None:
        if value < date(2026, 1, 1):
            fail("sealed_partition_access_attempt", value.isoformat())
        if value < self.history_start or value > self.signal_market_date + timedelta(days=14):
            fail("prospective_scan_date_outside_scope", value.isoformat())

    # Compatibility for older candidate-scan callers.  The semantic scope is prospective, not
    # development; scan.py prefers require_scan_date when available.
    def require_development_date(self, value: date) -> None:
        self.require_scan_date(value)

    def candidate_signal_date_allowed(self, value: date) -> bool:
        return value == self.signal_market_date


@dataclass(frozen=True)
class P6ProspectiveSplitPolicy:
    activation_market_date: date
    signal_market_date: date
    policy_id: str = "p6-prospective-activation-split"
    policy_version: str = "1.0.0"

    @property
    def semantic_digest(self) -> str:
        return digest_value(
            {
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "activation_market_date": self.activation_market_date.isoformat(),
                "signal_market_date": self.signal_market_date.isoformat(),
                "assignment": SplitAssignment.PROSPECTIVE.value,
                "historical_context_floor": "2026-01-01",
                "purge_sessions": 22,
                "embargo_sessions": 22,
            }
        )

    def require_stage_b_authority(self) -> None:
        return None

    def assign_event_split(
        self,
        *,
        signal_date: date,
        feature_start: date,
        outcome_end: date,
        trading_dates: Sequence[date],
    ) -> SplitAssignment:
        ordered = tuple(trading_dates)
        if not ordered or tuple(sorted(set(ordered))) != ordered:
            fail("prospective_split_invalid_calendar", "trading dates must be sorted and unique")
        dates = set(ordered)
        if any(value not in dates for value in (feature_start, signal_date, outcome_end)):
            fail("prospective_split_invalid_calendar", "event date is not an open session")
        if signal_date != self.signal_market_date or signal_date <= self.activation_market_date:
            fail("prospective_signal_before_activation", signal_date.isoformat())
        if feature_start < date(2026, 1, 1):
            fail("sealed_partition_access_attempt", feature_start.isoformat())
        if not feature_start <= signal_date < outcome_end:
            fail("prospective_split_invalid_event", "feature_start <= signal < T+1 is required")
        return SplitAssignment.PROSPECTIVE


@dataclass(frozen=True)
class P6ProspectiveCandidate:
    record: EventRecord
    listing_id: str
    listing_code: str
    company_id: str
    static_level1_industry_id: str
    feature_start: date
    feature_evidence: Mapping[str, object]
    entry_state: str = "pending_next_ready_session"

    def __post_init__(self) -> None:
        if self.entry_state != "pending_next_ready_session":
            fail("prospective_entry_state_drift", self.entry_state)
        if self.feature_start > self.record.identity.signal_date:
            fail("prospective_candidate_invalid", "feature start follows signal date")
        for value in (
            self.listing_id,
            self.listing_code,
            self.company_id,
            self.static_level1_industry_id,
        ):
            if not value or value.strip() != value:
                fail("prospective_candidate_invalid", "candidate identity text is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "event": self.record.to_dict(),
            "listing_id": self.listing_id,
            "listing_code": self.listing_code,
            "company_id": self.company_id,
            "static_level1_industry_id": self.static_level1_industry_id,
            "feature_start": self.feature_start.isoformat(),
            "feature_evidence": dict(self.feature_evidence),
            "entry_state": self.entry_state,
            "entry_date": None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> P6ProspectiveCandidate:
        expected = {
            "event",
            "listing_id",
            "listing_code",
            "company_id",
            "static_level1_industry_id",
            "feature_start",
            "feature_evidence",
            "entry_state",
            "entry_date",
        }
        if set(value) != expected or value.get("entry_date") is not None:
            fail("prospective_candidate_invalid", "published candidate must keep entry pending")
        evidence = value.get("feature_evidence")
        if not isinstance(evidence, dict):
            fail("prospective_candidate_invalid", "feature evidence must be an object")
        try:
            feature_start = date.fromisoformat(str(value["feature_start"]))
        except (KeyError, ValueError) as exc:
            raise ForwardValidationContractError(
                "prospective_candidate_invalid", "feature_start"
            ) from exc
        return cls(
            record=event_record_from_dict(value.get("event")),
            listing_id=str(value.get("listing_id")),
            listing_code=str(value.get("listing_code")),
            company_id=str(value.get("company_id")),
            static_level1_industry_id=str(value.get("static_level1_industry_id")),
            feature_start=feature_start,
            feature_evidence=MappingProxyType(dict(evidence)),
            entry_state=str(value.get("entry_state")),
        )


@dataclass(frozen=True)
class P6ProspectiveCandidateSet:
    candidates: tuple[P6ProspectiveCandidate, ...]
    counters: Mapping[str, object]


@dataclass(frozen=True)
class P6ProspectiveProjection:
    root: Path
    candidate_path: Path
    candidate_set_path: Path
    feature_panel_path: Path
    manifest_path: Path
    manifest_sha256: str
    content_digest: str
    snapshot_id: str
    snapshot_manifest_digest: str
    trading_dates: tuple[date, ...]
    signal_market_date: date
    prior_market_date: date
    prior_package_id: str | None
    prior_manifest_sha256: str | None
    history_start: date
    bundle: HistoricalReplayBundle
    panel_report: BaselineFeaturePanelReport
    industry_mapping: StaticIndustryMapping
    source_policy: P6ProspectiveSourcePolicy
    split_policy: P6ProspectiveSplitPolicy

    def feature_panel(self) -> BaselineFeaturePanel:
        return BaselineFeaturePanel(self.feature_panel_path, self.panel_report)


def _verify_seed(config: P6ProspectiveRuntimeConfig) -> None:
    base_manifest = config.historical_base_root / "manifest.json"
    correction_manifest = config.correction_root / "manifest.json"
    if not base_manifest.is_file() or not correction_manifest.is_file():
        fail("prospective_seed_missing", "Base/correction is not mounted")
    if _sha(base_manifest) != config.historical_base_manifest_sha256:
        fail("binding_digest_drift", "historical Base manifest")
    if _sha(correction_manifest) != config.correction_manifest_sha256:
        fail("binding_digest_drift", "historical correction manifest")
    base = _object(base_manifest, "historical Base manifest")
    correction = _object(correction_manifest, "historical correction manifest")
    try:
        ready = (config.historical_base_root / "READY").read_text(encoding="ascii").strip()
        correction_ready = (config.correction_root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ForwardValidationContractError("prospective_seed_missing", "Base/correction READY") from exc
    if ready != config.historical_base_manifest_sha256 or correction_ready != config.correction_manifest_sha256:
        fail("binding_digest_drift", "Base/correction READY")
    if (
        base.get("package_id") != "dwd-base-202607-v2.1"
        or base.get("content_digest") != config.historical_base_content_digest
        or correction.get("correction_id") != "dwd-base-202607-v2.1-correction-20260813-001"
        or correction.get("content_digest") != config.correction_content_digest
    ):
        fail("prospective_seed_identity_drift", "Base/correction identity differs")


def _chain_package(
    config: P6ProspectiveRuntimeConfig, market_date: date, package_id: str, manifest_sha256: str
) -> Path:
    source = config.daily_root
    if config.daily_product_scope == "daily-core-v1":
        if config.daily_core_first_date is None or config.legacy_daily_root is None:
            fail("p6_daily_core_bridge_invalid", "missing exact cutover configuration")
        if market_date < config.daily_core_first_date:
            source = config.legacy_daily_root
            if (
                market_date == config.legacy_bridge_market_date
                and (
                    package_id != config.legacy_bridge_package_id
                    or manifest_sha256 != config.legacy_bridge_manifest_sha256
                )
            ):
                fail("p6_daily_core_bridge_drift", "cutover prior identity differs")
    try:
        return resolve_daily_package(
            source, market_date, package_id, manifest_sha256=manifest_sha256
        )
    except ForwardValidationContractError as exc:
        fail("prospective_daily_chain_gap", str(exc))


def load_daily_chain(
    *, config: P6ProspectiveRuntimeConfig, target: ProspectiveDwdRelease
) -> tuple[P6DailyChainPackage, ...]:
    _verify_seed(config)
    if config.daily_root.resolve() not in target.package_root.resolve().parents:
        fail("path_escape", "target DWD package is outside prospective daily root")
    if (
        config.daily_product_scope == "daily-core-v1"
        and (config.daily_core_first_date is None or target.market_date < config.daily_core_first_date)
    ):
        fail("p6_daily_core_scope_drift", "target must be a post-cutover core release")
    cursor = target.package_root.resolve()
    observed: list[P6DailyChainPackage] = []
    seen: set[tuple[date, str]] = set()
    while True:
        manifest_path = cursor / "manifest.json"
        raw = _object(manifest_path, "daily DWD manifest")
        is_core_date = (
            config.daily_product_scope == "daily-core-v1"
            and config.daily_core_first_date is not None
            and date.fromisoformat(str(raw.get("market_date"))) >= config.daily_core_first_date
        )
        if (
            is_core_date and raw.get("kind") != "cn01_daily_core_dwd_increment"
            or not is_core_date and raw.get("kind") != "cn01_daily_dwd_domain_increment"
        ):
            fail("p6_daily_chain_product_drift", "date/product does not match frozen cutover")
        try:
            verified = (
                verify_daily_core_increment(cursor) if is_core_date
                else verify_daily_domain_increment(cursor)
            )
        except DailyDomainBuildError as exc:
            raise ForwardValidationContractError("dwd_release_verification_failed", str(exc)) from exc
        key = (verified.market_date, verified.package_id)
        if key in seen:
            fail("prospective_daily_chain_cycle", verified.package_id)
        seen.add(key)
        if (
            raw.get("base", {}).get("package_id") != "dwd-base-202607-v2.1"
            or raw.get("base", {}).get("manifest_sha256") != config.historical_base_manifest_sha256
            or raw.get("base", {}).get("content_digest") != config.historical_base_content_digest
        ):
            fail("prospective_daily_chain_base_drift", verified.package_id)
        code_revision = raw.get("code_revision")
        if not isinstance(code_revision, str) or len(code_revision) != 40 or any(
            ch not in "0123456789abcdef" for ch in code_revision
        ):
            fail("invalid_revision", "daily DWD code_revision")
        covered_after = date.fromisoformat(str(raw.get("covered_after_date")))
        prior = raw.get("prior")
        prior_id: str | None = None
        prior_sha: str | None = None
        prior_date: date | None = None
        if prior is not None:
            if not isinstance(prior, dict):
                fail("prospective_daily_chain_invalid", "prior binding must be object")
            prior_id = str(prior.get("package_id"))
            prior_sha = str(prior.get("manifest_sha256"))
            prior_date = date.fromisoformat(str(prior.get("market_date")))
            if prior_date >= verified.market_date:
                fail("prospective_daily_chain_invalid", "prior date does not precede package")
            if (
                config.daily_product_scope == "daily-core-v1"
                and verified.market_date == config.daily_core_first_date
                and (
                    prior_date != config.legacy_bridge_market_date
                    or prior_id != config.legacy_bridge_package_id
                    or prior_sha != config.legacy_bridge_manifest_sha256
                )
            ):
                fail("p6_daily_core_bridge_drift", "cutover first package does not bind approved legacy predecessor")
        observed.append(
            P6DailyChainPackage(
                root=cursor,
                package_id=verified.package_id,
                market_date=verified.market_date,
                manifest_sha256=verified.manifest_sha256,
                content_digest=verified.content_digest,
                code_revision=code_revision,
                covered_after_date=covered_after,
                prior_package_id=prior_id,
                prior_manifest_sha256=prior_sha,
            )
        )
        if prior is None:
            if covered_after != config.historical_base_through_date:
                fail("prospective_daily_chain_gap", "first daily package does not advance Base")
            break
        assert prior_date is not None and prior_id is not None and prior_sha is not None
        next_root = _chain_package(config, prior_date, prior_id, prior_sha)
        if not next_root.is_dir() or _sha(next_root / "manifest.json") != prior_sha:
            fail("prospective_daily_chain_gap", f"missing prior package {prior_id}")
        cursor = next_root
    ordered = tuple(reversed(observed))
    if ordered[-1].manifest_sha256 != target.manifest_sha256:
        fail("prospective_daily_chain_invalid", "target package identity differs")
    for left, right in zip(ordered, ordered[1:], strict=False):
        if right.prior_package_id != left.package_id or right.prior_manifest_sha256 != left.manifest_sha256:
            fail("prospective_daily_chain_gap", f"{left.package_id}->{right.package_id}")
        if right.covered_after_date != left.market_date:
            fail("prospective_daily_chain_gap", "covered_after date differs from prior package")
    return ordered


def _daily_payload_paths(chain: Sequence[P6DailyChainPackage], table: str) -> tuple[Path, ...]:
    paths: list[Path] = []
    for package in chain:
        matches = sorted((package.root / "payload").glob(f"**/{table}/**/*.parquet"))
        if not matches:
            fail("prospective_daily_table_missing", f"{package.package_id}:{table}")
        paths.extend(path.resolve() for path in matches)
    return tuple(paths)


def _schema_digest(connection: Any, path: Path) -> str:
    rows = connection.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
    return digest_value([(str(row[0]), str(row[1])) for row in rows])


def _build_bundle(
    *,
    config: P6ProspectiveRuntimeConfig,
    target: ProspectiveDwdRelease,
    chain: Sequence[P6DailyChainPackage],
    history_start: date,
    output_root: Path,
) -> HistoricalReplayBundle:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise ForwardValidationContractError("dependency_missing", "duckdb") from exc
    base_root = output_root / "base-seed"
    base_root.mkdir(parents=True, exist_ok=False)
    _materialize_base_projection(
        base_root,
        dwd_root=config.historical_base_root,
        correction_root=config.correction_root,
        history_start=history_start,
        execution_end=config.historical_base_through_date,
        allow_empty_non_partitioned=True,
    )
    tables = output_root / "tables"
    tables.mkdir()
    base_tables = base_root / "tables"
    bar_paths = _daily_payload_paths(chain, "dwd_security_bar")
    state_paths = _daily_payload_paths(chain, "dwd_tradability_state")
    calendar_paths = _daily_payload_paths(chain, "dwd_trading_calendar")
    listings = config.historical_base_root / "payload/listed_company/dwd_listing.parquet"
    securities = config.historical_base_root / "payload/listed_company/dwd_security.parquet"
    if not listings.is_file() or not securities.is_file():
        fail("prospective_seed_missing", "Base listing/security tables")
    target_iso = target.market_date.isoformat()
    history_iso = history_start.isoformat()
    base_through = config.historical_base_through_date.isoformat()
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET threads=1")
        connection.execute("SET preserve_insertion_order=false")
        allowed_basis = "none" if config.daily_product_scope == "daily-core-v1" else "UNADJUSTED"
        connection.execute(
            f"""
            CREATE VIEW p6_listing AS SELECT * FROM read_parquet({_sql_path(listings)}) WHERE knowledge_to IS NULL;
            CREATE VIEW p6_security AS SELECT * FROM read_parquet({_sql_path(securities)}) WHERE knowledge_to IS NULL;
            CREATE VIEW p6_daily_bar AS SELECT * FROM read_parquet({_sql_paths(bar_paths)})
              WHERE interval_size='1d' AND price_basis='{allowed_basis}';
            CREATE VIEW p6_daily_state AS SELECT * FROM read_parquet({_sql_paths(state_paths)});
            CREATE VIEW p6_daily_calendar AS SELECT * FROM read_parquet({_sql_paths(calendar_paths)});
            """
        )
        daily_bar_query = f"""
            SELECT listing.listing_code AS security_id,
                   listing.listing_code AS stock_code,
                   security.security_id AS dwd_security_id,
                   security.company_id AS dwd_company_id,
                   listing.listing_id AS dwd_listing_id,
                   listing.venue_id AS dwd_venue_id,
                   security.instrument_type AS dwd_instrument_type,
                   security.effective_from AS dwd_security_effective_from,
                   listing.effective_from AS dwd_listing_effective_from,
                   security.knowledge_from AS dwd_security_knowledge_from,
                   listing.knowledge_from AS dwd_listing_knowledge_from,
                   state.knowledge_from AS dwd_state_knowledge_from,
                   CASE listing.venue_id
                     WHEN 'venue_bec59e31eb6bb28111a10d7e6d2c689f' THEN 'SSE'
                     WHEN 'venue_8c0bd9088a0dc88f28e5e7fa8efdce87' THEN 'SZSE'
                   END AS identity_exchange,
                   CAST(calendar.trade_date AS DATE) AS date,
                   CAST(bar.open AS DOUBLE) AS open,
                   CAST(bar.high AS DOUBLE) AS high,
                   CAST(bar.low AS DOUBLE) AS low,
                   CAST(bar.close AS DOUBLE) AS close,
                   CAST(bar.volume AS DOUBLE) AS volume,
                   CAST(bar.turnover AS DOUBLE) AS amount,
                   bar.open AS dwd_open_decimal,
                   bar.high AS dwd_high_decimal,
                   bar.low AS dwd_low_decimal,
                   bar.close AS dwd_close_decimal,
                   bar.turnover AS dwd_turnover_decimal,
                   bar.knowledge_from AS dwd_bar_knowledge_from,
                   bar.source_record_id AS dwd_bar_source_record_id,
                   bar.source_revision AS dwd_bar_source_revision,
                   bar.source_artifact_sha256 AS dwd_bar_source_artifact_sha256,
                   NULL::DOUBLE AS turnover_rate,
                   NULL::DOUBLE AS pct_change,
                   'none' AS adj_mode,
                   'dwd-unadjusted-v1' AS adj_version,
                   'p6_daily_dwd_security_bar' AS source,
                   bar.knowledge_from AS built_at
            FROM p6_daily_state state
            JOIN p6_listing listing USING (listing_id)
            JOIN p6_security security ON security.security_id=listing.security_id
              AND security.effective_from <= listing.effective_from
              AND (security.effective_to IS NULL OR listing.effective_to IS NOT NULL AND listing.effective_to <= security.effective_to)
              AND security.knowledge_from <= listing.knowledge_from
            JOIN p6_daily_calendar calendar
              ON calendar.calendar_session_id=state.calendar_session_id AND calendar.venue_id=listing.venue_id
            LEFT JOIN p6_daily_bar bar
              ON bar.listing_id=state.listing_id AND bar.calendar_session_id=state.calendar_session_id
            WHERE listing.venue_id IN ('venue_bec59e31eb6bb28111a10d7e6d2c689f','venue_8c0bd9088a0dc88f28e5e7fa8efdce87')
              AND CAST(calendar.trade_date AS DATE) BETWEEN DATE '{history_iso}' AND DATE '{target_iso}'
        """
        _copy(
            connection,
            f"SELECT * FROM read_parquet({_sql_path(base_tables / 'daily_ohlcv_none.parquet')}) "
            f"WHERE date BETWEEN DATE '{history_iso}' AND DATE '{base_through}' "
            f"UNION ALL BY NAME {daily_bar_query}",
            tables / "daily_ohlcv_none.parquet",
        )
        daily_universe_query = f"""
            SELECT listing.listing_code AS security_id,
                   listing.listing_code AS stock_code,
                   security.security_id AS dwd_security_id,
                   security.company_id AS dwd_company_id,
                   listing.listing_id AS dwd_listing_id,
                   listing.venue_id AS dwd_venue_id,
                   security.instrument_type AS dwd_instrument_type,
                   security.effective_from AS dwd_security_effective_from,
                   listing.effective_from AS dwd_listing_effective_from,
                   security.knowledge_from AS dwd_security_knowledge_from,
                   listing.knowledge_from AS dwd_listing_knowledge_from,
                   state.knowledge_from AS dwd_state_knowledge_from,
                   CASE listing.venue_id
                     WHEN 'venue_bec59e31eb6bb28111a10d7e6d2c689f' THEN 'SSE'
                     WHEN 'venue_8c0bd9088a0dc88f28e5e7fa8efdce87' THEN 'SZSE'
                   END AS exchange,
                   CAST(calendar.trade_date AS DATE) AS market_date,
                   CAST(listing.effective_from AS DATE) AS valid_from,
                   CAST(listing.effective_to AS DATE) AS valid_to,
                   'dwd-listing-current-v1' AS mapping_version,
                   listing.source_artifact_sha256 AS mapping_digest
            FROM p6_daily_state state
            JOIN p6_listing listing USING (listing_id)
            JOIN p6_security security ON security.security_id=listing.security_id
              AND security.effective_from <= listing.effective_from
              AND (security.effective_to IS NULL OR listing.effective_to IS NOT NULL AND listing.effective_to <= security.effective_to)
              AND security.knowledge_from <= listing.knowledge_from
            JOIN p6_daily_calendar calendar
              ON calendar.calendar_session_id=state.calendar_session_id AND calendar.venue_id=listing.venue_id
            WHERE listing.venue_id IN ('venue_bec59e31eb6bb28111a10d7e6d2c689f','venue_8c0bd9088a0dc88f28e5e7fa8efdce87')
              AND CAST(calendar.trade_date AS DATE) BETWEEN DATE '{history_iso}' AND DATE '{target_iso}'
        """
        _copy(
            connection,
            f"SELECT * FROM read_parquet({_sql_path(base_tables / 'daily_universe.parquet')}) "
            f"WHERE market_date BETWEEN DATE '{history_iso}' AND DATE '{base_through}' "
            f"UNION ALL BY NAME {daily_universe_query}",
            tables / "daily_universe.parquet",
        )
        _copy(
            connection,
            f"""
            WITH all_bars AS (
              SELECT dwd_listing_id, CAST(date AS DATE) AS market_date, dwd_close_decimal AS close
              FROM read_parquet({_sql_path(tables / 'daily_ohlcv_none.parquet')})
            ), closes AS (
              SELECT *, lag(close) OVER (PARTITION BY dwd_listing_id ORDER BY market_date) AS prior_close
              FROM all_bars
            ), daily AS (
              SELECT listing.listing_code AS security_id,
                     listing.listing_code AS stock_code,
                     security.security_id AS dwd_security_id,
                     security.company_id AS dwd_company_id,
                     listing.listing_id AS dwd_listing_id,
                     listing.venue_id AS dwd_venue_id,
                     security.instrument_type AS dwd_instrument_type,
                     security.effective_from AS dwd_security_effective_from,
                     listing.effective_from AS dwd_listing_effective_from,
                     security.knowledge_from AS dwd_security_knowledge_from,
                     listing.knowledge_from AS dwd_listing_knowledge_from,
                     state.knowledge_from AS dwd_state_knowledge_from,
                     listing.board_code AS board,
                     CASE listing.venue_id
                       WHEN 'venue_bec59e31eb6bb28111a10d7e6d2c689f' THEN 'SSE'
                       WHEN 'venue_8c0bd9088a0dc88f28e5e7fa8efdce87' THEN 'SZSE'
                     END AS exchange,
                     CAST(calendar.trade_date AS DATE) AS execution_date,
                     'unknown' AS st_status_label, NULL::BOOLEAN AS is_st_class,
                     true AS status_known, CAST(calendar.trade_date AS DATE) AS st_source_market_date,
                     state.source_revision AS st_revision,
                     CASE WHEN state.is_tradable THEN '1' ELSE '0' END AS observed_tradestatus,
                     state.source_record_id AS tradestatus_raw_shard,
                     NULL::INTEGER AS eligible_session_index,
                     false AS in_ipo_window, NULL::VARCHAR AS listing_window_rule,
                     NULL::INTEGER AS ipo_window_sessions,
                     'dwd_tradability_state' AS selected_rule_basis,
                     CASE WHEN closes.prior_close > 0 AND state.upper_price IS NOT NULL
                       THEN (CAST(state.upper_price AS DOUBLE)/CAST(closes.prior_close AS DOUBLE)-1)*100 END AS upper_limit_pct,
                     CASE WHEN closes.prior_close > 0 AND state.lower_price IS NOT NULL
                       THEN (1-CAST(state.lower_price AS DOUBLE)/CAST(closes.prior_close AS DOUBLE))*100 END AS lower_limit_pct,
                     closes.prior_close AS price_reference,
                     'prior_trading_close' AS price_reference_kind,
                     state.upper_price AS upper_limit_price,
                     state.lower_price AS lower_limit_price,
                     CAST(0.01 AS DECIMAL(3,2)) AS price_tick,
                     'dwd_published_price' AS rounding_rule,
                     true AS regime_known,
                     state.is_tradable AND state.can_buy AS entry_policy_allows,
                     state.is_tradable AND state.can_buy AND state.can_sell AS is_executable,
                     state.reason_code,
                     CAST(calendar.trade_date AS DATE) AS knowledge_cutoff,
                     NULL::VARCHAR AS ipo_interval_id, NULL::VARCHAR AS ipo_source,
                     state.source_revision AS revision
              FROM p6_daily_state state
              JOIN p6_listing listing USING (listing_id)
              JOIN p6_security security ON security.security_id=listing.security_id
                AND security.effective_from <= listing.effective_from
                AND (security.effective_to IS NULL OR listing.effective_to IS NOT NULL AND listing.effective_to <= security.effective_to)
                AND security.knowledge_from <= listing.knowledge_from
              JOIN p6_daily_calendar calendar
                ON calendar.calendar_session_id=state.calendar_session_id AND calendar.venue_id=listing.venue_id
              LEFT JOIN closes ON closes.dwd_listing_id=state.listing_id AND closes.market_date=CAST(calendar.trade_date AS DATE)
              WHERE listing.venue_id IN ('venue_bec59e31eb6bb28111a10d7e6d2c689f','venue_8c0bd9088a0dc88f28e5e7fa8efdce87')
                AND CAST(calendar.trade_date AS DATE) BETWEEN DATE '{history_iso}' AND DATE '{target_iso}'
            )
            SELECT * FROM read_parquet({_sql_path(base_tables / 'price_limit_execution_daily.parquet')})
              WHERE execution_date BETWEEN DATE '{history_iso}' AND DATE '{base_through}'
            UNION ALL BY NAME SELECT * FROM daily
            """,
            tables / "price_limit_execution_daily.parquet",
        )
        shutil.copy2(base_tables / "security_code_validity.parquet", tables / "security_code_validity.parquet")
        shutil.copy2(base_tables / "corporate_actions_normalized.parquet", tables / "corporate_actions_normalized.parquet")
        shutil.copy2(base_tables / "corporate_action_exceptions.parquet", tables / "corporate_action_exceptions.parquet")
        # Only session coordinates already bound by the Base or an immutable daily DWD package are
        # admitted.  Future session guesses are forbidden; next-open execution is resolved by the
        # next READY daily package when it actually arrives.
        _copy(
            connection,
            f"""
            WITH daily AS (
              SELECT CASE venue_id
                       WHEN 'venue_bec59e31eb6bb28111a10d7e6d2c689f' THEN 'SSE'
                       WHEN 'venue_8c0bd9088a0dc88f28e5e7fa8efdce87' THEN 'SZSE'
                     END AS exchange,
                     CAST(trade_date AS DATE) AS trade_date,
                     CAST(is_open AS BIGINT) AS is_open,
                     'day' AS session,
                     'dwd_trading_calendar' AS source,
                     knowledge_from AS built_at
              FROM p6_daily_calendar
              WHERE is_open
                AND venue_id IN ('venue_bec59e31eb6bb28111a10d7e6d2c689f','venue_8c0bd9088a0dc88f28e5e7fa8efdce87')
                AND CAST(trade_date AS DATE) BETWEEN DATE '{history_iso}' AND DATE '{target_iso}'
            )
            SELECT * FROM read_parquet({_sql_path(base_tables / 'trading_calendar.parquet')})
              WHERE trade_date BETWEEN DATE '{history_iso}' AND DATE '{base_through}'
            UNION ALL BY NAME
            SELECT * FROM daily WHERE trade_date > DATE '{base_through}'
            ORDER BY trade_date, exchange
            """,
            tables / "trading_calendar.parquet",
        )
        for dataset in ("daily_ohlcv_none", "daily_universe", "price_limit_execution_daily"):
            date_column = {
                "daily_ohlcv_none": "date",
                "daily_universe": "market_date",
                "price_limit_execution_daily": "execution_date",
            }[dataset]
            duplicate = connection.execute(
                f"SELECT count(*) FROM (SELECT security_id,{date_column} FROM read_parquet(?) GROUP BY ALL HAVING count(*)<>1)",
                [str(tables / f"{dataset}.parquet")],
            ).fetchone()
            if duplicate is None or int(duplicate[0]):
                fail("prospective_effective_view_duplicate", dataset)
        target_rows = connection.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE CAST(date AS DATE)=CAST(? AS DATE)",
            [str(tables / "daily_ohlcv_none.parquet"), target_iso],
        ).fetchone()
        if target_rows is None or int(target_rows[0]) <= 0:
            fail("prospective_target_not_complete_trading_session", target_iso)
        manifests: list[DatasetManifest] = []
        cutoff = _market_close(target.market_date).astimezone(timezone.utc)
        created = target.visibility.consumer_visible_at.astimezone(timezone.utc)
        source_revision = target.identity_digest
        for dataset in _DATASETS:
            file = tables / f"{dataset}.parquet"
            row = connection.execute("SELECT count(*) FROM read_parquet(?)", [str(file)]).fetchone()
            count = 0 if row is None else int(row[0])
            checksum = _sha(file)
            manifests.append(
                DatasetManifest(
                    dataset=dataset,
                    version="p6-prospective-effective-view-v1",
                    source_revisions={"p6_release_identity": source_revision},
                    row_count=count,
                    partitions=(f"tables/{dataset}.parquet",),
                    schema_hash=_schema_digest(connection, file),
                    content_checksums={f"tables/{dataset}.parquet": checksum},
                    created_at=created,
                    knowledge_cutoff=cutoff,
                )
            )
    finally:
        connection.close()
    snapshot_id = f"p6-effective-{target.identity_digest.removeprefix('sha256:')[:24]}"
    return HistoricalReplayBundle(
        root=output_root.resolve(),
        snapshot=SnapshotRef(
            market_date=target.market_date,
            knowledge_cutoff=_market_close(target.market_date),
            source_revision=target.identity_digest,
            snapshot_id=snapshot_id,
        ),
        universe_policy_version="p6-prospective-effective-view-v1",
        datasets=tuple(manifests),
    )


def _build_candidate_projection(
    *,
    config: P6ProspectiveRuntimeConfig,
    bundle: HistoricalReplayBundle,
    history_start: date,
    target_date: date,
    output: Path,
) -> tuple[tuple[date, ...], StaticIndustryMapping]:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise ForwardValidationContractError("dependency_missing", "duckdb") from exc
    if not config.static_industry_mapping.is_file() or _sha(config.static_industry_mapping) != config.static_industry_mapping_sha256:
        fail("binding_digest_drift", "static industry mapping")
    industry = load_static_industry_mapping(
        config.static_industry_mapping,
        expected_version="static-level1-concentration-research-v1",
    )
    bars = bundle.partition_paths("daily_ohlcv_none")[0]
    universe = bundle.partition_paths("daily_universe")[0]
    limits = bundle.partition_paths("price_limit_execution_daily")[0]
    calendar = bundle.partition_paths("trading_calendar")[0]
    factor_paths = sorted(
        (config.historical_base_root / "payload/security_market_observation").glob(
            "dwd_adjustment_factor/**/*.parquet"
        )
    )
    if not factor_paths:
        factor_paths = sorted(config.historical_base_root.glob("payload/**/dwd_adjustment_factor/**/*.parquet"))
    if not factor_paths:
        fail("prospective_seed_missing", "dwd_adjustment_factor")
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET threads=1")
        _copy(
            connection,
            f"""
            WITH ranked_universe AS (
              SELECT *, row_number() OVER (PARTITION BY dwd_listing_id ORDER BY market_date) AS listing_sessions
              FROM read_parquet({_sql_path(universe)})
            ), factor_boundary AS (
              SELECT security_id,
                     CAST(timezone('Asia/Shanghai', effective_from) AS DATE) AS effective_date,
                     min(effective_from) AS factor_effective_from,
                     max(knowledge_from) AS dwd_factor_knowledge_from,
                     count(*) AS factor_boundary_count
              FROM read_parquet({_sql_paths(factor_paths)})
              WHERE knowledge_to IS NULL
              GROUP BY security_id,effective_date
            )
            SELECT universe.dwd_security_id,
                   universe.dwd_listing_id,
                   universe.stock_code AS listing_code,
                   universe.dwd_company_id,
                   universe.exchange AS venue_id,
                   universe.dwd_instrument_type AS instrument_type,
                   universe.market_date AS session_date,
                   universe.listing_sessions,
                   bars.dwd_open_decimal AS open_price,
                   bars.dwd_high_decimal AS high_price,
                   bars.dwd_low_decimal AS low_price,
                   bars.dwd_close_decimal AS close_price,
                   bars.dwd_turnover_decimal AS turnover,
                   CASE WHEN limits.observed_tradestatus='1' THEN true WHEN limits.observed_tradestatus='0' THEN false ELSE NULL END AS is_tradable,
                   factor.factor_boundary_count IS NOT NULL AS adjustment_effective,
                   factor.factor_effective_from,
                   factor.factor_boundary_count,
                   bars.dwd_bar_knowledge_from,
                   universe.dwd_security_knowledge_from,
                   universe.dwd_listing_knowledge_from,
                   limits.dwd_state_knowledge_from,
                   factor.dwd_factor_knowledge_from,
                   bars.dwd_bar_source_record_id,
                   bars.dwd_bar_source_revision,
                   bars.dwd_bar_source_artifact_sha256,
                   universe.dwd_security_effective_from,
                   universe.dwd_listing_effective_from
            FROM ranked_universe universe
            LEFT JOIN read_parquet({_sql_path(bars)}) bars ON bars.security_id=universe.security_id AND bars.date=universe.market_date
            LEFT JOIN read_parquet({_sql_path(limits)}) limits ON limits.security_id=universe.security_id AND limits.execution_date=universe.market_date
            LEFT JOIN factor_boundary factor ON factor.security_id=universe.dwd_security_id AND factor.effective_date=universe.market_date
            WHERE universe.market_date BETWEEN DATE '{history_start.isoformat()}' AND DATE '{target_date.isoformat()}'
            ORDER BY universe.dwd_security_id, universe.dwd_listing_effective_from, universe.dwd_listing_id, universe.market_date
            """,
            output,
        )
        dates = tuple(
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT trade_date FROM read_parquet(?) WHERE is_open=1 ORDER BY trade_date",
                [str(calendar)],
            ).fetchall()
        )
    finally:
        connection.close()
    if len([value for value in dates if value <= target_date]) < 120:
        fail("prospective_history_insufficient", "fewer than 120 open sessions in 2026 effective view")
    if target_date not in dates:
        fail("prospective_target_not_complete_trading_session", target_date.isoformat())
    if any(value > target_date for value in dates):
        fail("post_cutoff_evidence_violation", "prospective calendar contains a future session")
    return dates, industry


def build_prospective_projection(
    *,
    config: P6ProspectiveRuntimeConfig,
    activation: P6ActivationDecision,
    target: ProspectiveDwdRelease,
    output_root: Path,
    collect_candidates: bool = True,
) -> P6ProspectiveProjection:
    boundary = P6ActivationBoundary(
        activation.decision_digest,
        activation.decision_at,
        activation.activation_market_date,
        activation.enabled,
    )
    admission: P6ReleaseAdmission = classify_release_admission(target, boundary)
    if not admission.official_evidence:
        fail("official_cycle_not_authorized", admission.reason_code)
    if target.market_date < date(2026, 1, 1):
        fail("prospective_scope_drift", "P6 prospective source cannot consume pre-2026 market data")
    history_start = max(date(2026, 1, 1), target.market_date - timedelta(days=config.history_calendar_days))
    root = output_root.resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    chain = load_daily_chain(config=config, target=target)
    bundle = _build_bundle(
        config=config,
        target=target,
        chain=chain,
        history_start=history_start,
        output_root=root / "effective-view",
    )
    candidate_path = root / "candidate-daily.parquet"
    trading_dates, industry = _build_candidate_projection(
        config=config,
        bundle=bundle,
        history_start=history_start,
        target_date=target.market_date,
        output=candidate_path,
    )
    feature_registry: FeatureRegistry = load_feature_registry(config.bindings["feature_registry"])
    feature_path = root / "feature-panel.parquet"
    feature = BaselineFeaturePanel.materialize(
        bundle,
        feature_registry,
        scope_start=history_start,
        scope_end=target.market_date,
        output=feature_path,
        data_content_digest=bundle.digest,
        data_manifest_sha256=target.manifest_sha256,
        output_order="security-date",
    )
    source_policy = P6ProspectiveSourcePolicy(
        activation_market_date=activation.activation_market_date,
        signal_market_date=target.market_date,
        history_start=history_start,
        semantic_digest=digest_value(
            {
                "runtime_sha256": config.config_sha256,
                "activation_decision_digest": activation.decision_digest,
                "target_release_identity": target.identity_digest,
                "history_start": history_start.isoformat(),
                "signal_market_date": target.market_date.isoformat(),
                "sealed_2022_2025_access": False,
            }
        ),
    )
    split_policy = P6ProspectiveSplitPolicy(
        activation_market_date=activation.activation_market_date,
        signal_market_date=target.market_date,
    )
    card: CandidateRuleCard = load_candidate_rule_card(config.bindings["candidate_rule"])

    class _Projection:
        def __init__(self, path: Path) -> None:
            self.candidate_path = path

    # The accepted P4 scanner requires a T+1 coordinate to finalize an event, but an honest T-close
    # prospective cycle cannot know the next open session yet.  Append one non-persisted sentinel
    # date solely for finalization; no market row exists for it and it is never exposed as an
    # execution date.  The next READY daily package resolves the actual T+1 session.
    scanner_dates = (*trading_dates, target.market_date + timedelta(days=1))
    if collect_candidates:
        scan: CandidateScanResult | None = scan_forward_candidate_series(
            iter_projected_candidate_series(
                _Projection(candidate_path),  # type: ignore[arg-type]
                source_policy=source_policy,  # type: ignore[arg-type]
                industry_mapping=industry,
            ),
            trading_dates=scanner_dates,
            snapshot_id=bundle.snapshot.snapshot_id,
            snapshot_manifest_digest=target.manifest_sha256,
            card=card,
            split_policy=split_policy,  # type: ignore[arg-type]
            source_policy=source_policy,  # type: ignore[arg-type]
        )
        missing_industry = int(scan.counters.reason_counts.get("static_industry_missing", 0))
        if missing_industry:
            fail("prospective_industry_mapping_incomplete", str(missing_industry))
    else:
        scan = None
    candidate_set_path = root / "candidate-events.json"
    published_candidates = tuple(
        P6ProspectiveCandidate(
            record=item.record,
            listing_id=item.listing_id,
            listing_code=item.listing_code,
            company_id=item.company_id,
            static_level1_industry_id=item.static_level1_industry_id,
            feature_start=item.feature_start,
            feature_evidence=MappingProxyType(item.feature_evidence.to_dict()),
        )
        for item in (() if scan is None else scan.candidates)
    )
    candidate_set_payload = {
        "schema_version": "quanthus.p6-prospective-candidate-set.v1",
        "signal_market_date": target.market_date.isoformat(),
        "entry_state": "pending_next_ready_session",
        "scanner_internal_t_plus_1_sentinel_persisted": False,
        "counters": (
            {
                "evaluated_rows": 0,
                "raw_triggers": 0,
                "isolated_candidates": 0,
                "concentration_retained": 0,
                "candidate_events": 0,
                "reason_counts": {},
            }
            if scan is None
            else scan.counters.to_dict()
        ),
        "candidates": [item.to_dict() for item in published_candidates],
    }
    candidate_set_path.write_bytes(canonical_json_bytes(candidate_set_payload) + b"\n")
    direct_prior = chain[-2] if len(chain) > 1 else None
    prior_market_date = (
        direct_prior.market_date if direct_prior is not None else config.historical_base_through_date
    )
    files = {
        "candidate_daily": _sha(candidate_path),
        "candidate_events": _sha(candidate_set_path),
        "feature_panel": _sha(feature_path),
        "effective_view_digest": bundle.digest,
    }
    manifest = {
        "schema_version": "quanthus.p6-prospective-projection.v1",
        "runtime_sha256": config.config_sha256,
        "activation_decision_digest": activation.decision_digest,
        "target_release_identity": target.identity_digest,
        "target_manifest_sha256": target.manifest_sha256,
        "history_start": history_start.isoformat(),
        "signal_market_date": target.market_date.isoformat(),
        "direct_prior": {
            "market_date": prior_market_date.isoformat(),
            "package_id": None if direct_prior is None else direct_prior.package_id,
            "manifest_sha256": None if direct_prior is None else direct_prior.manifest_sha256,
        },
        "daily_chain": [
            {
                "package_id": item.package_id,
                "market_date": item.market_date.isoformat(),
                "manifest_sha256": item.manifest_sha256,
                "content_digest": item.content_digest,
                "code_revision": item.code_revision,
            }
            for item in chain
        ],
        "candidate_scan": (
            {
                "collection_enabled": True,
                "evaluated_rows": scan.counters.evaluated_rows,
                "raw_triggers": scan.counters.raw_triggers,
                "candidate_events": scan.counters.candidate_events,
                "reason_counts": dict(sorted(scan.counters.reason_counts.items())),
                "event_ids": [item.record.event_id for item in published_candidates],
                "entry_state": "pending_next_ready_session",
                "scanner_internal_t_plus_1_sentinel_persisted": False,
            }
            if scan is not None
            else {
                "collection_enabled": False,
                "evaluated_rows": 0,
                "raw_triggers": 0,
                "candidate_events": 0,
                "reason_counts": {},
                "event_ids": [],
                "entry_state": "pending_next_ready_session",
                "scanner_internal_t_plus_1_sentinel_persisted": False,
            }
        ),
        "feature_panel": feature.report.payload_dict(),
        "files": files,
        "sealed_historical_partition_reads": 0,
    }
    manifest["content_digest"] = digest_value(manifest)
    manifest_path = root / "manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
    (root / "READY").write_text(_sha(manifest_path) + "\n", encoding="ascii")
    return P6ProspectiveProjection(
        root=root,
        candidate_path=candidate_path,
        candidate_set_path=candidate_set_path,
        feature_panel_path=feature_path,
        manifest_path=manifest_path,
        manifest_sha256=_sha(manifest_path),
        content_digest=str(manifest["content_digest"]),
        snapshot_id=bundle.snapshot.snapshot_id,
        snapshot_manifest_digest=target.manifest_sha256,
        trading_dates=trading_dates,
        signal_market_date=target.market_date,
        prior_market_date=prior_market_date,
        prior_package_id=None if direct_prior is None else direct_prior.package_id,
        prior_manifest_sha256=None if direct_prior is None else direct_prior.manifest_sha256,
        history_start=history_start,
        bundle=bundle,
        panel_report=feature.report,
        industry_mapping=industry,
        source_policy=source_policy,
        split_policy=split_policy,
    )


def scan_projection_candidates(
    projection: P6ProspectiveProjection,
    *,
    candidate_rule: Path,
) -> P6ProspectiveCandidateSet:
    """Load the already-materialized P6 candidate set without rebuilding the scan.

    The accepted P4 scanner runs exactly once while constructing the job-local projection.  Its
    internal synthetic T+1 coordinate is deliberately stripped before publication; downstream P6
    consumers see only ``entry_state=pending_next_ready_session`` and resolve the real execution
    date from the next immutable DWD package.
    """

    card = load_candidate_rule_card(candidate_rule)
    manifest = _object(projection.manifest_path, "P6 prospective projection manifest")
    files = manifest.get("files")
    scan = manifest.get("candidate_scan")
    if not isinstance(files, dict) or not isinstance(scan, dict):
        fail("prospective_projection_invalid", "candidate bindings are missing")
    expected_sha = files.get("candidate_events")
    if expected_sha != _sha(projection.candidate_set_path):
        fail("binding_digest_drift", "prospective candidate set")
    payload = _object(projection.candidate_set_path, "P6 prospective candidate set")
    if (
        payload.get("schema_version") != "quanthus.p6-prospective-candidate-set.v1"
        or payload.get("signal_market_date") != projection.signal_market_date.isoformat()
        or payload.get("entry_state") != "pending_next_ready_session"
        or payload.get("scanner_internal_t_plus_1_sentinel_persisted") is not False
    ):
        fail("prospective_candidate_set_drift", "candidate-set envelope differs")
    raw_candidates = payload.get("candidates")
    raw_counters = payload.get("counters")
    if not isinstance(raw_candidates, list) or not isinstance(raw_counters, dict):
        fail("prospective_candidate_set_drift", "candidate-set payload differs")
    candidates = tuple(
        P6ProspectiveCandidate.from_dict(item)
        for item in raw_candidates
        if isinstance(item, dict)
    )
    if len(candidates) != len(raw_candidates):
        fail("prospective_candidate_set_drift", "candidate rows are malformed")
    event_ids = [item.record.event_id for item in candidates]
    if len(event_ids) != len(set(event_ids)):
        fail("prospective_candidate_set_drift", "candidate event ids are duplicated")
    for item in candidates:
        record = item.record
        if (
            record.identity.signal_date != projection.signal_market_date
            or record.identity.candidate_rule_id != card.rule_id
            or record.identity.candidate_rule_version != card.rule_version
            or record.candidate_rule_digest != card.semantic_digest
            or record.split_assignment is not SplitAssignment.PROSPECTIVE
        ):
            fail("prospective_candidate_set_drift", record.event_id)
    if (
        int(raw_counters.get("candidate_events", -1)) != len(candidates)
        or scan.get("event_ids") != event_ids
        or scan.get("entry_state") != "pending_next_ready_session"
        or scan.get("scanner_internal_t_plus_1_sentinel_persisted") is not False
    ):
        fail("prospective_candidate_set_drift", "manifest/candidate-set reconciliation differs")
    return P6ProspectiveCandidateSet(
        candidates=candidates,
        counters=MappingProxyType(dict(raw_counters)),
    )
