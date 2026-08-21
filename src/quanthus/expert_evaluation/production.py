"""Real development-only producer for the first expert engineering proof."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.backtest.baseline_research import BaselineFeaturePanel
from quanthus.backtest.data_provider import ReplayDataProvider, load_replay_data_provider_config
from quanthus.event_library.contracts import EventRecord, event_record_from_dict
from quanthus.event_library.label_corpus import verify_physical_label_corpus
from quanthus.event_library.physical_scan import verify_physical_event_scan
from quanthus.expert_evaluation.contracts import ArtifactRef, ExpertEvaluationSpec, ForecastSpec
from quanthus.expert_evaluation.experts.trend_stability import (
    evaluate_trend_stability,
    load_trend_stability_config,
)
from quanthus.expert_evaluation.resolver import load_verified_p4_labels, resolve_observation
from quanthus.expert_evaluation.runner import (
    DwdIdentity,
    ExpertEvaluationJobInput,
    PartitionBinding,
    ProducedArtifact,
    ProducerBinding,
    RegistryArtifactRef,
)
from quanthus.expert_evaluation.scoring import EvaluationRow, build_descriptive_scorecard
from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    canonical_json_bytes,
    decode_artifact,
    digest_value,
)
from quanthus.expert_evaluation.target_cards import load_target_decision_card
from quanthus.feature_registry import load_feature_registry

PRODUCER_ID = "producer.trend-stability-development"
PRODUCER_VERSION = "1.0.0"
PRODUCER_CONFIGURATION_DIGEST = digest_value(
    {
        "producer_id": PRODUCER_ID,
        "producer_version": PRODUCER_VERSION,
        "target_decision_digest": (
            "sha256:a67accc8688b2a63ef01afff160666119b72b5c0ec4adc421d266104e76087f5"
        ),
        "expert_semantic_digest": (
            "sha256:e66ee7024dc28ddca5059e0aa74a7333988eeacfa5cdc5743fa0933dd5b96fa7"
        ),
        "evaluation_spec_digest": (
            "sha256:d6f8cdc64042ee6795fda6979e08ca98c380a33e53664f8bd0d321d775669723"
        ),
    }
)
_DWD_BASE = (
    "dwd-base-202607-v2.1",
    "2.1.0",
    "sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9",
)
_DWD_CORRECTION = (
    "dwd-base-202607-v2.1-correction-20260813-001",
    "1.0.0",
    "sha256:1c1d1f4ea906797255fbc71cc3915bb905e51e7aea202d27042e33692b72e9da",
)
_DWD_EFFECTIVE_VIEW_DIGEST = digest_value(
    {"base": _DWD_BASE, "corrections": [_DWD_CORRECTION], "increments": []}
)
_EVALUATION_SPEC_DIGEST = (
    "sha256:d6f8cdc64042ee6795fda6979e08ca98c380a33e53664f8bd0d321d775669723"
)
_PARTITION_DIGEST = (
    "sha256:accba0894d9892df25c3465f2e572f25d859631bac4a14fa0bcaa236db621bbc"
)


@dataclass(frozen=True)
class _EventInput:
    record: EventRecord
    static_industry_id: str | None


def production_binding() -> ProducerBinding:
    return ProducerBinding(PRODUCER_ID, PRODUCER_VERSION, PRODUCER_CONFIGURATION_DIGEST)


def production_dwd_identity() -> DwdIdentity:
    return DwdIdentity(
        _DWD_BASE,
        (_DWD_CORRECTION,),
        (),
        _DWD_EFFECTIVE_VIEW_DIGEST,
        date(2026, 7, 31),
    )


def production_job_input(label_parent_digest: str) -> ExpertEvaluationJobInput:
    """Construct the only admitted #148 Job after a verified label parent is selected."""

    return ExpertEvaluationJobInput(
        operation="development_evaluation",
        evaluation_specs=(
            RegistryArtifactRef(
                "expert.trend-stability",
                "1.0.0",
                _EVALUATION_SPEC_DIGEST,
                "specs/trend-stability-p4-first-passage.evaluation.v1.json",
            ),
        ),
        formal_strategy=None,
        dwd=production_dwd_identity(),
        partition=PartitionBinding(
            "partition.development-2020-2021",
            "1.0.0",
            _PARTITION_DIGEST,
            date(2020, 1, 1),
            date(2021, 12, 31),
        ),
        requested_interventions=(),
        expected_parent_run_digests=(label_parent_digest,),
        producer=production_binding(),
    )


def _load_events(root: Path, start: date, end: date) -> tuple[_EventInput, ...]:
    verify_physical_event_scan(root)
    try:
        values = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExpertEvaluationError("event_library_unreadable", "P4 events are unreadable") from exc
    result: list[_EventInput] = []
    for value in values:
        if not isinstance(value, dict):
            raise ExpertEvaluationError("field_mismatch", "P4 event row must be an object")
        record = event_record_from_dict(value.get("event"))
        if start <= record.identity.signal_date <= end:
            industry = value.get("static_level1_industry_id")
            result.append(
                _EventInput(record, None if industry is None else str(industry))
            )
    result.sort(key=lambda item: item.record.event_id)
    if not result:
        raise ExpertEvaluationError("insufficient_sample", "approved event window is empty")
    return tuple(result)


def _find_parent_package(
    event_run_root: Path, expected_parent_digests: Sequence[str]
) -> tuple[Path, Path]:
    matches: list[tuple[Path, Path]] = []
    for run in sorted(event_run_root.iterdir() if event_run_root.is_dir() else ()):
        event_root = run / "event-library"
        label_root = run / "label-corpus"
        if not event_root.is_dir() or not label_root.is_dir():
            continue
        try:
            label = verify_physical_label_corpus(label_root)
            event = verify_physical_event_scan(event_root)
            label_manifest = json.loads(
                (label_root / "manifest.json").read_text(encoding="utf-8")
            )
        except Exception:
            continue
        event_binding = (
            label_manifest.get("event_library")
            if isinstance(label_manifest, dict)
            else None
        )
        if not isinstance(event_binding, dict) or (
            event_binding.get("manifest_sha256"),
            event_binding.get("content_digest"),
        ) != (event.manifest_sha256, event.content_digest):
            continue
        admitted = {
            label.manifest_sha256,
            label.content_digest,
            event.manifest_sha256,
            event.content_digest,
        }
        if admitted.intersection(expected_parent_digests):
            matches.append((event_root, label_root))
    if len(matches) != 1:
        reason = "parent_run_missing" if not matches else "parent_run_ambiguous"
        raise ExpertEvaluationError(reason, "exactly one verified P4 parent must be bound")
    return matches[0]


def _parquet(rows: Sequence[Mapping[str, str | None]]) -> bytes:
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ExpertEvaluationError(
            "producer_dependency_unavailable", "production producer requires pyarrow"
        ) from exc
    names = tuple(rows[0]) if rows else ("artifact_json",)
    columns = {name: [row.get(name) for row in rows] for name in names}
    table = pa.table(columns)
    sink = pa.BufferOutputStream()
    pq.write_table(
        table,
        sink,
        compression="NONE",
        use_dictionary=False,
        write_statistics=False,
        data_page_version="1.0",
    )
    return bytes(sink.getvalue().to_pybytes())


def _trading_sessions(bundle: Any, start: date, end: date) -> tuple[date, ...]:
    try:
        import duckdb
    except ImportError as exc:
        raise ExpertEvaluationError(
            "producer_dependency_unavailable", "production producer requires duckdb"
        ) from exc
    paths = tuple(bundle.partition_paths("trading_calendar"))
    if not paths:
        raise ExpertEvaluationError("calendar_unavailable", "trading calendar is empty")
    expression = ",".join("'" + str(path).replace("'", "''") + "'" for path in paths)
    connection = duckdb.connect(":memory:")
    try:
        rows = connection.execute(
            f"""
            SELECT trade_date
            FROM read_parquet([{expression}])
            WHERE is_open AND trade_date BETWEEN ? AND ?
            GROUP BY trade_date
            HAVING count(DISTINCT exchange) >= 2
            ORDER BY trade_date
            """,
            [start, end],
        ).fetchall()
    finally:
        connection.close()
    sessions = tuple(row[0] for row in rows)
    if not sessions or any(not isinstance(item, date) for item in sessions):
        raise ExpertEvaluationError("calendar_unavailable", "joint market sessions are missing")
    return sessions


class TrendStabilityOperationProducer:
    """Recompute observations from DWD and settle only through the accepted P4 package."""

    def __init__(self, registry_root: Path, event_run_root: Path) -> None:
        self.registry_root = registry_root.resolve()
        self.event_run_root = event_run_root.resolve()
        self.repository_root = self.registry_root.parents[1]

    def produce(
        self,
        job_input: ExpertEvaluationJobInput,
        specs: tuple[ExpertEvaluationSpec, ...],
        progress: Callable[[str, Mapping[str, Any]], None],
        canceled: Callable[[], bool],
    ) -> tuple[ProducedArtifact, ...]:
        if job_input.producer != production_binding():
            raise ExpertEvaluationError("producer_binding_mismatch", "production producer differs")
        if job_input.dwd != production_dwd_identity():
            raise ExpertEvaluationError("dwd_binding_mismatch", "effective DWD v2.1 differs")
        if job_input.operation != "development_evaluation" or len(specs) != 1:
            raise ExpertEvaluationError("unsupported_operation", "first proof accepts one spec")
        partition_bounds = (
            job_input.partition.decision_start.isoformat(),
            job_input.partition.decision_end.isoformat(),
        )
        if partition_bounds != (
            "2020-01-01",
            "2021-12-31",
        ):
            raise ExpertEvaluationError("partition_mismatch", "first proof is fixed to 2020-2021")
        if not job_input.expected_parent_run_digests:
            raise ExpertEvaluationError("parent_run_missing", "accepted P4 parent is required")
        target = load_target_decision_card(
            self.registry_root / "targets/p4-event-first-passage-reuse.v1.json",
            self.repository_root,
        )
        target.require_result_access_authority()
        expert_config = load_trend_stability_config(
            self.registry_root / "experts/trend-stability.v1.json"
        )
        forecast = decode_artifact(
            (
                self.registry_root
                / "specs/trend-stability-p4-first-passage.forecast.v1.json"
            ).read_bytes(),
            ForecastSpec,
        )
        spec = specs[0]
        if spec.digest != _EVALUATION_SPEC_DIGEST or spec.forecast_spec_digest != forecast.digest:
            raise ExpertEvaluationError("registry_drift", "first evaluation spec binding differs")
        event_root, label_root = _find_parent_package(
            self.event_run_root, job_input.expected_parent_run_digests
        )
        events = _load_events(
            event_root,
            target.eligible_population.evaluation_start,
            target.eligible_population.evaluation_end,
        )
        labels, label_ref = load_verified_p4_labels(label_root)
        if label_ref.digest not in job_input.expected_parent_run_digests:
            verified_label = verify_physical_label_corpus(label_root)
            if verified_label.manifest_sha256 not in job_input.expected_parent_run_digests:
                raise ExpertEvaluationError("parent_run_mismatch", "P4 label parent is not exact")
        provider_config = load_replay_data_provider_config(
            self.repository_root / "config/replay-data-provider.development.v2.json"
        )
        registry = load_feature_registry(
            self.repository_root / "config/p3-baseline-features.research.v1.json"
        )
        provider = ReplayDataProvider(provider_config)
        evaluation_rows: list[EvaluationRow] = []
        trading_sessions: tuple[date, ...] = ()
        with tempfile.TemporaryDirectory(prefix="quanthus-expert-panel-") as temporary:
            panel_path = Path(temporary) / "feature-panel.parquet"
            progress("building-shared-projection", {"event_count": len(events)})
            with provider.open(
                scope_start=target.eligible_population.evaluation_start,
                scope_end=target.eligible_population.evaluation_end,
            ) as session, BaselineFeaturePanel.build(
                session.reader.bundle,
                registry,
                scope_start=target.eligible_population.evaluation_start,
                scope_end=target.eligible_population.evaluation_end,
                output=panel_path,
                data_content_digest=session.reader.data_content_digest,
                data_manifest_sha256=session.reader.data_manifest_sha256,
            ) as panel:
                trading_sessions = _trading_sessions(
                    session.reader.bundle,
                    target.eligible_population.evaluation_start,
                    target.eligible_population.evaluation_end,
                )
                progress("producing-observations", {"event_count": len(events)})
                for index, event in enumerate(events, start=1):
                    if canceled():
                        raise ExpertEvaluationError("canceled", "evaluation was canceled")
                    feature = panel.feature(
                        event.record.identity.signal_date,
                        event.record.identity.security_id,
                    )
                    observation = evaluate_trend_stability(
                        event=event.record,
                        feature=feature,
                        config=expert_config,
                        spec=spec,
                    )
                    resolution = None
                    if observation.applicability.value == "applicable":
                        resolution = resolve_observation(
                            observation,
                            labels.get(event.record.event_id),
                            resolver=forecast.resolver,
                            label_corpus=label_ref,
                        )
                    evaluation_rows.append(
                        EvaluationRow(observation, resolution, event.static_industry_id)
                    )
                    if index % 1000 == 0:
                        progress(
                            "producing-observations",
                            {"completed": index, "total": len(events)},
                        )
        progress("settling-outcomes", {"event_count": len(events)})
        scorecard = build_descriptive_scorecard(
            evaluation_rows,
            spec=spec,
            target_digest=forecast.target.digest,
            resolver=forecast.resolver,
            uncertainty_method=ArtifactRef(
                "uncertainty.nonoverlap-22-session-block-bootstrap",
                "1.0.0",
                digest_value(dict(target.evaluation["uncertainty"])),
            ),
            calendar_year_slice=next(
                item for item in spec.slices if item.artifact_id == "slice.calendar-year"
            ),
            trading_sessions=trading_sessions,
        )
        observations = tuple(
            {
                "subject_id": row.observation.subject_id,
                "observation_date": row.observation.observation_anchor.date().isoformat(),
                "artifact_json": row.observation.canonical_bytes().decode("utf-8"),
            }
            for row in evaluation_rows
        )
        settlements = tuple(
            {
                "subject_id": row.observation.subject_id,
                "ledger_state": (
                    "abstained" if row.resolution is None else row.resolution.ledger_state
                ),
                "artifact_json": (
                    None
                    if row.resolution is None or row.resolution.settlement is None
                    else row.resolution.settlement.canonical_bytes().decode("utf-8")
                ),
            }
            for row in evaluation_rows
        )
        progress("building-scorecard", {"settled": scorecard.sample_ledger.settled})
        progress("running-interventions", {"state": "not_requested"})
        return (
            ProducedArtifact("observations.parquet", _parquet(observations), len(observations)),
            ProducedArtifact("settlements.parquet", _parquet(settlements), len(settlements)),
            ProducedArtifact(
                "sample-ledger.json",
                canonical_json_bytes(scorecard.sample_ledger.to_dict()),
                1,
            ),
            ProducedArtifact("scorecard.json", scorecard.canonical_bytes(), 1),
        )


def production_producer_for(
    job_input: ExpertEvaluationJobInput, registry_root: Path
) -> TrendStabilityOperationProducer:
    if job_input.producer.producer_id != PRODUCER_ID:
        raise ExpertEvaluationError("producer_not_admitted", "operation producer is not admitted")
    event_root = Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs"))
    return TrendStabilityOperationProducer(registry_root, event_root)
