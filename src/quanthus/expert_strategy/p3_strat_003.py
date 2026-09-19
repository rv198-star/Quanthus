"""Additive P3-STRAT-003 four-variant research runner.

The module reuses the accepted formal strategy, aggregator, position policy and complete P2 replay.
It adds one explicit non-empty candidate pass-through control for research comparison only.  It does
not mutate the accepted strategy card and does not grant execution authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.event_library.contracts import event_record_from_dict
from quanthus.expert_evaluation.production import _EventInput
from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    canonical_json_bytes,
    digest_value,
)
from quanthus.expert_strategy.formal_attribution import FormalReplayEvidence
from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime
from quanthus.expert_strategy.runner import FormalStrategyRunner
from quanthus.industry_mapping import StaticIndustryMapping, load_static_industry_mapping

REQUEST_PATH = "config/research/p3-strat-003/request.v1.json"
REQUEST_SCHEMA = "quanthus.p3-strat-003-research-request.v1"
REQUEST_DIGEST = "sha256:9de79141514aa1941d276da854d734786bb2b5bb83310a1d3c7792d60c952bde"
VARIANT_IDS = ("B", "T", "R", "TR")
RECENT_HISTORY_PARTITION = "p3-strat-003-recent-history"
CANDIDATE_INPUT_SCHEMA = "quanthus.p3-strat-003-candidate-input.v1"
_TOKEN_BY_VARIANT = {
    "B": "research.p3-strat-003.B",
    "T": "research.p3-strat-003.T",
    "R": "research.p3-strat-003.R",
    "TR": "research.p3-strat-003.TR",
}
_VARIANT_BY_TOKEN = {value: key for key, value in _TOKEN_BY_VARIANT.items()}


@dataclass(frozen=True)
class P3Strat003Request:
    path: Path
    value: Mapping[str, Any]
    digest: str

    @classmethod
    def load(cls, repository_root: Path) -> P3Strat003Request:
        path = repository_root.resolve() / REQUEST_PATH
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_request_unreadable",
                "P3-STRAT-003 request is unreadable",
            ) from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != REQUEST_SCHEMA:
            raise ExpertEvaluationError(
                "p3_strat_003_request_invalid",
                "P3-STRAT-003 request schema differs",
            )
        declared = raw.get("semantic_digest")
        payload = {key: value for key, value in raw.items() if key != "semantic_digest"}
        computed = "sha256:" + hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        ).hexdigest()
        if declared != computed or declared != REQUEST_DIGEST:
            raise ExpertEvaluationError(
                "p3_strat_003_request_drift",
                "P3-STRAT-003 request digest differs",
            )
        variants = raw.get("variants")
        if not isinstance(variants, list) or [item.get("variant_id") for item in variants] != list(
            VARIANT_IDS
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_variant_set_drift",
                "P3-STRAT-003 variant set differs",
            )
        return cls(path, raw, computed)

    def variant(self, variant_id: str) -> Mapping[str, Any]:
        if variant_id not in VARIANT_IDS:
            raise ExpertEvaluationError(
                "p3_strat_003_variant_not_admitted",
                f"variant {variant_id!r} is not admitted",
            )
        return next(
            item for item in self.value["variants"] if item["variant_id"] == variant_id
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_candidate_input_unreadable",
            f"candidate input file is unreadable: {path.name}",
        ) from exc
    return "sha256:" + digest.hexdigest()


def _bound_child(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative or relative.startswith("/"):
        raise ExpertEvaluationError(
            "p3_strat_003_candidate_input_invalid",
            f"{label} path must be relative",
        )
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ExpertEvaluationError(
            "p3_strat_003_candidate_input_invalid",
            f"{label} path escapes candidate input root",
        )
    return path


@dataclass(frozen=True)
class P3Strat003CandidateInput:
    manifest_path: Path
    manifest_digest: str
    events: tuple[_EventInput, ...]
    industry: StaticIndustryMapping
    scope: Mapping[str, str]
    replay_bundle_digest: str

    @classmethod
    def load(
        cls,
        manifest_path: Path,
        *,
        request_digest: str,
        decision_start: date,
        decision_end: date,
        execution_end: date,
    ) -> P3Strat003CandidateInput:
        resolved = manifest_path.resolve()
        try:
            raw = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_candidate_input_unreadable",
                "P3-STRAT-003 candidate manifest is unreadable",
            ) from exc
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != CANDIDATE_INPUT_SCHEMA
            or raw.get("work_item") != "P3-STRAT-003-B"
            or raw.get("request_digest") != request_digest
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_candidate_input_invalid",
                "P3-STRAT-003 candidate manifest identity differs",
            )
        declared = raw.get("semantic_digest")
        payload = {key: value for key, value in raw.items() if key != "semantic_digest"}
        computed = digest_value(payload)
        if declared != computed:
            raise ExpertEvaluationError(
                "p3_strat_003_candidate_input_drift",
                "P3-STRAT-003 candidate manifest digest differs",
            )
        expected_scope = {
            "decision_start": decision_start.isoformat(),
            "decision_end": decision_end.isoformat(),
            "execution_end": execution_end.isoformat(),
        }
        scope = raw.get("scope")
        if scope != expected_scope:
            raise ExpertEvaluationError(
                "p3_strat_003_candidate_scope_mismatch",
                "candidate input scope differs from replay scope",
            )
        event_binding = raw.get("events")
        industry_binding = raw.get("industry_mapping")
        if not isinstance(event_binding, dict) or not isinstance(industry_binding, dict):
            raise ExpertEvaluationError(
                "p3_strat_003_candidate_input_invalid",
                "candidate input bindings are incomplete",
            )
        root = resolved.parent
        events_path = _bound_child(root, event_binding.get("path"), "events")
        industry_path = _bound_child(root, industry_binding.get("path"), "industry_mapping")
        if _sha256_file(events_path) != event_binding.get("sha256"):
            raise ExpertEvaluationError(
                "p3_strat_003_candidate_input_drift",
                "candidate events checksum differs",
            )
        if _sha256_file(industry_path) != industry_binding.get("sha256"):
            raise ExpertEvaluationError(
                "p3_strat_003_candidate_input_drift",
                "candidate industry mapping checksum differs",
            )
        industry = load_static_industry_mapping(
            industry_path,
            expected_version="static-level1-concentration-research-v1",
        )
        events: list[_EventInput] = []
        try:
            lines = events_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_candidate_input_unreadable",
                "candidate events are unreadable",
            ) from exc
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ExpertEvaluationError(
                    "p3_strat_003_candidate_input_invalid",
                    "candidate event JSON is invalid",
                ) from exc
            if not isinstance(value, dict):
                raise ExpertEvaluationError(
                    "p3_strat_003_candidate_input_invalid",
                    "candidate event row must be an object",
                )
            record = event_record_from_dict(value.get("event"))
            if not decision_start <= record.identity.signal_date <= decision_end:
                raise ExpertEvaluationError(
                    "p3_strat_003_candidate_scope_mismatch",
                    "candidate event falls outside frozen decision scope",
                )
            listing_code = value.get("listing_code")
            static_industry_id = value.get("static_level1_industry_id")
            if not isinstance(listing_code, str) or not listing_code:
                raise ExpertEvaluationError(
                    "p3_strat_003_candidate_input_invalid",
                    "candidate listing_code is missing",
                )
            expected_membership = industry.by_security_id.get(record.identity.security_id)
            if (
                expected_membership is None
                or static_industry_id != expected_membership.level1_industry_id
            ):
                raise ExpertEvaluationError(
                    "p3_strat_003_candidate_industry_mismatch",
                    "candidate industry differs from bound mapping",
                )
            events.append(
                _EventInput(
                    record,
                    str(static_industry_id),
                    listing_code,
                )
            )
        events.sort(key=lambda item: item.record.event_id)
        if len(events) != event_binding.get("count") or not events:
            raise ExpertEvaluationError(
                "p3_strat_003_candidate_input_invalid",
                "candidate event count differs or is empty",
            )
        if len({item.record.event_id for item in events}) != len(events):
            raise ExpertEvaluationError(
                "p3_strat_003_candidate_input_invalid",
                "candidate event identities are not unique",
            )
        replay_bundle_digest = raw.get("replay_bundle_digest")
        if not isinstance(replay_bundle_digest, str) or not replay_bundle_digest.startswith(
            "sha256:"
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_candidate_input_invalid",
                "replay bundle digest is invalid",
            )
        return cls(
            resolved,
            computed,
            tuple(events),
            industry,
            expected_scope,
            replay_bundle_digest,
        )


class P3Strat003ReplayRuntime(FormalP2ReplayRuntime):
    """Run the frozen B/T/R/TR factorial through one shared replay prerequisite."""

    def __init__(
        self,
        repository_root: Path,
        *,
        decision_start: date,
        decision_end: date,
        execution_end: date,
        provider_config: str,
        parent_event_run_digest: str | None = None,
        candidate_input_manifest: str | None = None,
    ) -> None:
        if (parent_event_run_digest is None) == (candidate_input_manifest is None):
            raise ExpertEvaluationError(
                "p3_strat_003_population_binding_invalid",
                "exactly one parent Event Run or outcome-blind candidate input must be bound",
            )
        self.request = P3Strat003Request.load(repository_root)
        self.candidate_input_manifest = (
            None
            if candidate_input_manifest is None
            else (repository_root.resolve() / candidate_input_manifest).resolve()
        )
        self.candidate_input: P3Strat003CandidateInput | None = None
        super().__init__(
            repository_root,
            decision_start=decision_start,
            decision_end=decision_end,
            execution_end=execution_end,
            parent_event_run_digest=parent_event_run_digest,
            provider_config=provider_config,
            allowed_provider_partitions=frozenset({RECENT_HISTORY_PARTITION}),
        )
        if (
            self.candidate_input is not None
            and self.session.reader.bundle.digest != self.candidate_input.replay_bundle_digest
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_candidate_bundle_mismatch",
                "candidate input binds a different replay bundle",
            )

    def _load_event_population(
        self,
        event_root: Path,
    ) -> tuple[tuple[_EventInput, ...], StaticIndustryMapping]:
        if self.candidate_input_manifest is None:
            return super()._load_event_population(event_root)
        self.candidate_input = P3Strat003CandidateInput.load(
            self.candidate_input_manifest,
            request_digest=self.request.digest,
            decision_start=self.decision_start,
            decision_end=self.decision_end,
            execution_end=self.execution_end,
        )
        return self.candidate_input.events, self.candidate_input.industry

    def _population_manifest_fields(self) -> dict[str, Any]:
        if self.candidate_input is None:
            return super()._population_manifest_fields()
        return {
            "parent_event_run_digest": None,
            "candidate_input_manifest_digest": self.candidate_input.manifest_digest,
            "candidate_input_replay_bundle_digest": self.candidate_input.replay_bundle_digest,
        }

    @staticmethod
    def token_for(variant_id: str) -> str:
        try:
            return _TOKEN_BY_VARIANT[variant_id]
        except KeyError as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_variant_not_admitted",
                f"variant {variant_id!r} is not admitted",
            ) from exc

    @staticmethod
    def variant_for(token: str | None) -> str:
        try:
            assert token is not None
            return _VARIANT_BY_TOKEN[token]
        except (AssertionError, KeyError) as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_variant_not_admitted",
                "research variant token is not admitted",
            ) from exc

    def _validate_intervention_id(self, intervention_id: str | None) -> None:
        self.variant_for(intervention_id)

    @staticmethod
    def _variant_id(intervention_id: str | None) -> str:
        variant_id = P3Strat003ReplayRuntime.variant_for(intervention_id)
        return f"variant.{variant_id}"

    def _baseline_variant_directory(self) -> str:
        return "variant.B"

    def _held_intervention_id(self, intervention_id: str | None) -> str | None:
        variant_id = self.variant_for(intervention_id)
        if variant_id in {"B", "T"}:
            return "intervention.position-risk-base-policy"
        return None

    def _code_version(self) -> str:
        return "p3-strat-003-recent-history-replay-1.0.0"

    def _parameter_payload(self, intervention_id: str | None) -> dict[str, Any]:
        variant_id = self.variant_for(intervention_id)
        return {
            "request_digest": self.request.digest,
            "formal_strategy": self.registry.card.semantic_digest,
            "variant_id": variant_id,
            "variant": dict(self.request.variant(variant_id)),
            "scope": {
                "decision_start": self.decision_start.isoformat(),
                "decision_end": self.decision_end.isoformat(),
                "execution_end": self.execution_end.isoformat(),
            },
            "population": self._population_manifest_fields(),
            "provider_config": self.provider_config,
        }

    def batch_input_identity(self) -> dict[str, Any]:
        return {
            "request_digest": self.request.digest,
            "formal_strategy_digest": self.registry.card.semantic_digest,
            "code_version": self._code_version(),
            "scope": {
                "decision_start": self.decision_start.isoformat(),
                "decision_end": self.decision_end.isoformat(),
                "execution_end": self.execution_end.isoformat(),
            },
            "population": self._population_manifest_fields(),
            "provider_config_path": self.provider_config,
            "provider_config_digest": self.session.config.digest,
            "provider_partition": self.session.config.scope.partition,
        }

    @property
    def batch_input_digest(self) -> str:
        return digest_value(self.batch_input_identity())

    def _manifest_metadata(self, intervention_id: str | None) -> dict[str, Any]:
        variant_id = self.variant_for(intervention_id)
        return {
            "research_request_digest": self.request.digest,
            "research_work_item": "P3-STRAT-003-D",
            "research_variant_id": variant_id,
            "research_batch_input_digest": self.batch_input_digest,
            "research_code_version": self._code_version(),
            "p6_authority": "none",
        }

    def _evaluate_entry_decision(
        self,
        runner: FormalStrategyRunner,
        *,
        event_id: str,
        context: Any,
        trend_state: str | None,
        entry_evidence_ready: bool,
        intervention_id: str | None,
    ) -> Any:
        variant_id = self.variant_for(intervention_id)
        if variant_id in {"B", "R"}:
            return runner.evaluate_entry_control(
                event_id=event_id,
                context=context,
                entry_evidence_ready=entry_evidence_ready,
                position_risk_enabled=variant_id == "R",
            )
        return runner.evaluate_entry(
            event_id=event_id,
            context=context,
            trend_state=trend_state,
            entry_evidence_ready=entry_evidence_ready,
            intervention_id=(
                "intervention.position-risk-base-policy" if variant_id == "T" else None
            ),
        )

    def _metrics_intervention_id(self, intervention_id: str | None) -> str | None:
        return None if self.variant_for(intervention_id) == "B" else intervention_id

    @property
    def feature_panel_bytes(self) -> int:
        return (Path(self.temporary.name) / "formal-feature-panel.parquet").stat().st_size

    def run_research_variant(
        self,
        variant_id: str,
        output_root: Path,
    ) -> tuple[FormalReplayEvidence, dict[str, bytes]]:
        return super().run_variant(self.token_for(variant_id), output_root)


@dataclass(frozen=True)
class P3Strat003BatchResult:
    request_digest: str
    variants: Mapping[str, FormalReplayEvidence]
    telemetry: Mapping[str, Any]

    @property
    def digest(self) -> str:
        return digest_value(
            {
                "request_digest": self.request_digest,
                "variants": {
                    key: value.digest for key, value in sorted(self.variants.items())
                },
            }
        )


class P3Strat003CompositeBackend:
    """Persist exact variants while sharing one opened provider/feature prerequisite."""

    def __init__(
        self,
        repository_root: Path,
        workspace_root: Path,
        *,
        decision_start: date,
        decision_end: date,
        execution_end: date,
        provider_config: str,
        parent_event_run_digest: str | None = None,
        candidate_input_manifest: str | None = None,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.workspace_root = workspace_root.resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.request = P3Strat003Request.load(self.repository_root)
        self._opened_at = time.perf_counter()
        runtime_started = time.perf_counter()
        self.runtime = P3Strat003ReplayRuntime(
            self.repository_root,
            decision_start=decision_start,
            decision_end=decision_end,
            execution_end=execution_end,
            provider_config=provider_config,
            parent_event_run_digest=parent_event_run_digest,
            candidate_input_manifest=candidate_input_manifest,
        )
        self._shared_prerequisite_elapsed = time.perf_counter() - runtime_started
        self._variant_elapsed: dict[str, float] = {}
        self._variant_disposition: dict[str, str] = {}
        self._write_immutable(
            self.workspace_root / "batch-input.json",
            canonical_json_bytes(
                {
                    "schema_version": "quanthus.p3-strat-003-batch-input.v1",
                    "identity": self.runtime.batch_input_identity(),
                    "digest": self.runtime.batch_input_digest,
                }
            ),
        )

    def close(self) -> None:
        self.runtime.close()

    def __enter__(self) -> P3Strat003CompositeBackend:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _variant_root(self, variant_id: str) -> Path:
        if variant_id not in VARIANT_IDS:
            raise ExpertEvaluationError(
                "p3_strat_003_variant_not_admitted",
                f"variant {variant_id!r} is not admitted",
            )
        return self.workspace_root / "variants" / f"variant.{variant_id}"

    def _load(self, variant_id: str) -> FormalReplayEvidence | None:
        root = self._variant_root(variant_id)
        path = root / "evidence.json"
        manifest_path = root / "manifest.json"
        if root.exists() and (not path.is_file() or not manifest_path.is_file()):
            raise ExpertEvaluationError(
                "p3_strat_003_partial_publication",
                f"variant {variant_id} publication is incomplete",
            )
        if not path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_persisted_manifest_invalid",
                f"variant {variant_id} manifest is unreadable",
            ) from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("research_request_digest") != self.request.digest
            or manifest.get("research_variant_id") != variant_id
            or manifest.get("research_batch_input_digest") != self.runtime.batch_input_digest
            or manifest.get("research_code_version") != self.runtime._code_version()
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_persisted_input_mismatch",
                f"variant {variant_id} was produced from different semantic inputs",
            )
        evidence = FormalReplayEvidence.from_bytes(path.read_bytes())
        expected_token = self.runtime.token_for(variant_id)
        if (
            evidence.variant_id != f"variant.{variant_id}"
            or evidence.intervention_id != expected_token
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_persisted_variant_mismatch",
                "persisted P3-STRAT-003 variant differs",
            )
        return evidence

    @staticmethod
    def _write_immutable(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise ExpertEvaluationError(
                    "p3_strat_003_immutable_artifact_conflict",
                    f"immutable artifact differs: {path.name}",
                )
            return
        path.write_bytes(payload)

    def execute_variant(self, variant_id: str) -> FormalReplayEvidence:
        existing = self._load(variant_id)
        if existing is not None:
            self._variant_elapsed.setdefault(variant_id, 0.0)
            self._variant_disposition[variant_id] = "reused_existing"
            return existing
        required_prefix = VARIANT_IDS[: VARIANT_IDS.index(variant_id)]
        missing = [item for item in required_prefix if self._load(item) is None]
        if missing:
            raise ExpertEvaluationError(
                "p3_strat_003_variant_order_violation",
                f"variant {variant_id} requires earlier variants: {missing}",
            )
        target = self._variant_root(variant_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{target.name}.",
                dir=target.parent,
            )
        )
        try:
            started = time.perf_counter()
            evidence, artifacts = self.runtime.run_research_variant(variant_id, staging)
            self._variant_elapsed[variant_id] = time.perf_counter() - started
            self._variant_disposition[variant_id] = "built"
            for relative, payload in artifacts.items():
                self._write_immutable(staging / relative, payload)
            self._write_immutable(
                staging / "evidence.json",
                canonical_json_bytes(evidence.to_dict()),
            )
            if target.exists():
                raise ExpertEvaluationError(
                    "p3_strat_003_immutable_artifact_conflict",
                    f"variant target appeared during publication: {variant_id}",
                )
            os.replace(staging, target)
            return evidence
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def execute_all(self) -> P3Strat003BatchResult:
        started = time.perf_counter()
        variants = {variant_id: self.execute_variant(variant_id) for variant_id in VARIANT_IDS}
        elapsed = time.perf_counter() - started
        panel_report = self.runtime.panel.report
        telemetry = {
            "schema_version": "quanthus.p3-strat-003-batch-telemetry.v1",
            "provider_builds": 1,
            "provider_reuse_disposition": "one_open_session_shared_by_B_T_R_TR",
            "shared_prerequisite_elapsed_seconds": round(
                self._shared_prerequisite_elapsed, 6
            ),
            "feature_panel_builds": 1,
            "feature_panel_rows": panel_report.row_count,
            "feature_panel_bytes": self.runtime.feature_panel_bytes,
            "feature_panel_digest": panel_report.digest,
            "variant_count": len(variants),
            "variant_elapsed_seconds": {
                key: round(self._variant_elapsed.get(key, 0.0), 6) for key in VARIANT_IDS
            },
            "variant_disposition": {
                key: self._variant_disposition.get(key, "unknown") for key in VARIANT_IDS
            },
            "elapsed_seconds": round(elapsed, 6),
            "runtime_open_seconds": round(time.perf_counter() - self._opened_at, 6),
            "cross_batch_cache_used": False,
        }
        self._write_immutable(
            self.workspace_root / "batch-telemetry.json",
            canonical_json_bytes(telemetry),
        )
        result = P3Strat003BatchResult(self.request.digest, variants, telemetry)
        self._write_immutable(
            self.workspace_root / "batch-result.json",
            canonical_json_bytes(
                {
                    "schema_version": "quanthus.p3-strat-003-batch-result.v1",
                    "request_digest": result.request_digest,
                    "variant_evidence_digests": {
                        key: value.digest for key, value in sorted(variants.items())
                    },
                    "telemetry_path": "batch-telemetry.json",
                    "digest": result.digest,
                }
            ),
        )
        return result
