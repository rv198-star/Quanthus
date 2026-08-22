from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from quanthus.backtest.contracts import UnitState
from quanthus.expert_evaluation import cli
from quanthus.expert_evaluation.production import _EventInput
from quanthus.expert_evaluation.runner import ExpertEvaluationJobInput, ProducerBinding
from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    canonical_json_bytes,
    digest_value,
)
from quanthus.expert_strategy.formal_attribution_production import (
    FORMAL_ATTRIBUTION_PRODUCER_ID,
    FORMAL_ATTRIBUTION_PRODUCER_VERSION,
    FORMAL_RELEASE_SCHEMA,
    formal_attribution_job_input,
    formal_attribution_producer_for,
    verify_formal_attribution_release,
)
from quanthus.expert_strategy.formal_p2_replay import (
    JANUARY_SENTINEL_DECISION_DIGEST,
    PARENT_EVENT_RUN_DIGEST,
    FormalP2ReplayRuntime,
    _decision_digest_in_window,
    _load_parent_industry_mapping,
    _stable_id,
    _transition,
    _UnitRuntime,
    run_paired_block_bootstrap,
)
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.position_lifecycle import (
    PositionEvidenceKind,
    PositionEvidenceRef,
    PositionTransitionKind,
    initialize_position_lifecycle,
)

ROOT = Path(__file__).resolve().parents[2]


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _formal_release(root: Path) -> dict[str, object]:
    payload = canonical_json_bytes(
        {
            "sentinel_decision_digest": JANUARY_SENTINEL_DECISION_DIGEST,
            "sealed_partition_access": False,
        }
    )
    artifact = root / "replays" / "baseline.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(payload)
    manifest: dict[str, object] = {
        "schema_version": FORMAL_RELEASE_SCHEMA,
        "release_id": "formal-attribution-test",
        "strategy_id": "candidate.p4-trend-risk",
        "strategy_version": "1.0.1",
        "strategy_card_digest": "sha256:" + "1" * 64,
        "preregistration_digest": "sha256:" + "2" * 64,
        "plan_digest": "sha256:" + "3" * 64,
        "evaluation_bundle_digest": "sha256:" + "4" * 64,
        "file_count": 1,
        "combined_size_bytes": len(payload),
        "sealed_partition_access": False,
        "files": [
            {
                "path": "replays/baseline.json",
                "sha256": _sha256(payload),
                "size_bytes": len(payload),
            }
        ],
    }
    manifest["release_digest"] = digest_value(manifest)
    (root / "release.json").write_bytes(canonical_json_bytes(manifest))
    (root / "READY").write_text(f'{manifest["release_digest"]}\n', encoding="ascii")
    return manifest


def _daily_returns(root: Path, *, changed_date: bool = False) -> None:
    start = date(2020, 1, 1)
    rows = []
    for index in range(44):
        market_date = start + timedelta(days=index + (1 if changed_date and index == 43 else 0))
        rows.append({"market_date": market_date.isoformat(), "return": str(index / 10000)})
    root.mkdir(parents=True, exist_ok=True)
    (root / "daily-returns.json").write_bytes(canonical_json_bytes(rows))


def test_formal_job_input_freezes_exact_registry_data_partition_and_interventions() -> None:
    registry = ExpertStrategyRegistry(ROOT)
    job_input = formal_attribution_job_input(registry)

    assert ExpertEvaluationJobInput.from_dict(job_input.to_dict()) == job_input
    assert job_input.operation == "bounded_intervention"
    assert job_input.partition.to_dict() == {
        "artifact_id": "partition.development-2020-2021",
        "version": "1.0.0",
        "digest": "sha256:accba0894d9892df25c3465f2e572f25d859631bac4a14fa0bcaa236db621bbc",
        "decision_start": "2020-01-01",
        "decision_end": "2021-12-30",
    }
    assert job_input.dwd.base[:2] == ("dwd-base-202607-v2.1", "2.1.0")
    assert [item[0] for item in job_input.dwd.corrections] == [
        "dwd-base-202607-v2.1-correction-20260813-001"
    ]
    assert job_input.expected_parent_run_digests == (PARENT_EVENT_RUN_DIGEST,)
    assert job_input.requested_interventions == tuple(sorted(registry.intervention_ids))
    assert tuple(item.identity for item in job_input.evaluation_specs) == (
        registry.evaluation_spec_identities
    )
    assert job_input.producer.producer_id == FORMAL_ATTRIBUTION_PRODUCER_ID
    assert job_input.producer.producer_version == FORMAL_ATTRIBUTION_PRODUCER_VERSION


def test_formal_producer_dispatch_rejects_a_foreign_producer() -> None:
    registry = ExpertStrategyRegistry(ROOT)
    job_input = formal_attribution_job_input(registry)
    producer, resolver = formal_attribution_producer_for(
        job_input, ROOT / "config" / "expert-evaluations"
    )

    assert producer.registry.card.semantic_digest == registry.card.semantic_digest
    assert resolver.expected_bindings(job_input.formal_strategy, resolver.expert_registry_root)[
        0
    ] == registry.evaluation_spec_identities
    foreign = replace(
        job_input,
        producer=ProducerBinding(
            "producer.foreign",
            job_input.producer.producer_version,
            job_input.producer.configuration_digest,
        ),
    )
    with pytest.raises(ExpertEvaluationError, match="producer_not_admitted"):
        formal_attribution_producer_for(
            foreign, ROOT / "config" / "expert-evaluations"
        )


def test_cli_routes_formal_attribution_to_its_registered_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = ExpertStrategyRegistry(ROOT)
    job_input = formal_attribution_job_input(registry)
    input_path = tmp_path / "input.json"
    input_path.write_bytes(job_input.canonical_bytes)
    expected_producer = object()
    calls: list[tuple[object, object]] = []

    def producer_for(observed: ExpertEvaluationJobInput, registry_root: Path):
        assert observed == job_input
        assert registry_root == ROOT / "config" / "expert-evaluations"
        return expected_producer, registry

    def run_evaluation(observed: ExpertEvaluationJobInput, **kwargs: object):
        assert observed == job_input
        calls.append((kwargs["producer"], kwargs["formal_strategy_resolver"]))
        return SimpleNamespace(
            run_id="expert_run_formal_test",
            root=tmp_path / "runs" / "expert_run_formal_test",
            manifest_digest="sha256:" + "5" * 64,
            manifest={"evaluation_identities": [], "strategy_identity": None},
            summary={"operation": "bounded_intervention"},
        )

    monkeypatch.setattr(cli, "formal_attribution_producer_for", producer_for)
    monkeypatch.setattr(cli, "run_evaluation", run_evaluation)

    assert (
        cli.main(
            [
                "--input",
                str(input_path),
                "--registry-root",
                str(ROOT / "config" / "expert-evaluations"),
                "--staging",
                str(tmp_path / "staging"),
                "--run-root",
                str(tmp_path / "runs"),
            ]
        )
        == 0
    )
    assert calls == [(expected_producer, registry)]
    assert json.loads(capsys.readouterr().out)["run_id"] == "expert_run_formal_test"


def test_formal_release_verifier_rejects_deep_tampering_and_extra_inventory(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    expected = _formal_release(release)
    assert verify_formal_attribution_release(release) == expected

    artifact = release / "replays" / "baseline.json"
    original = artifact.read_bytes()
    artifact.write_bytes(original + b"\n")
    with pytest.raises(ExpertEvaluationError, match="formal_release_checksum_mismatch"):
        verify_formal_attribution_release(release)
    artifact.write_bytes(original)
    (release / "undeclared.txt").write_text("extra\n", encoding="ascii")
    with pytest.raises(ExpertEvaluationError, match="formal_release_inventory_mismatch"):
        verify_formal_attribution_release(release)


def test_paired_block_bootstrap_is_deterministic_and_rejects_date_drift(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline"
    intervention = tmp_path / "intervention"
    _daily_returns(baseline)
    _daily_returns(intervention)

    first = run_paired_block_bootstrap(
        baseline_root=baseline,
        intervention_root=intervention,
        question_id="question.trend-policy-effect",
        method="uncertainty.paired-nonoverlap-22-session-block-bootstrap@1.0.0",
    )
    second = run_paired_block_bootstrap(
        baseline_root=baseline,
        intervention_root=intervention,
        question_id="question.trend-policy-effect",
        method="uncertainty.paired-nonoverlap-22-session-block-bootstrap@1.0.0",
    )
    assert first == second
    assert first["complete_block_count"] == 2
    assert first["resamples"] == 2000

    _daily_returns(intervention, changed_date=True)
    with pytest.raises(ExpertEvaluationError, match="formal_bootstrap_pair_mismatch"):
        run_paired_block_bootstrap(
            baseline_root=baseline,
            intervention_root=intervention,
            question_id="question.trend-policy-effect",
            method="uncertainty.paired-nonoverlap-22-session-block-bootstrap@1.0.0",
        )


def test_action_change_metrics_count_each_held_review_date(tmp_path: Path) -> None:
    baseline = tmp_path / "variants" / "variant.native-baseline"
    baseline.mkdir(parents=True)
    baseline_decisions = [
        {
            "market_date": "2020-01-02",
            "decision_key": "held:capital-unit-01",
            "final_action": "reduce",
        },
        {
            "market_date": "2020-01-03",
            "decision_key": "held:capital-unit-01",
            "final_action": "hold",
        },
    ]
    (baseline / "decisions.json").write_bytes(canonical_json_bytes(baseline_decisions))
    intervention_decisions = [
        {**item, "final_action": "hold"} for item in baseline_decisions
    ]
    equity = [
        {
            "market_date": "2020-01-01",
            "system_equity": "100.00",
            "market_value": "0.00",
            "invested_capital_ratio": "0",
        },
        {
            "market_date": "2020-01-02",
            "system_equity": "100.00",
            "market_value": "10.00",
            "invested_capital_ratio": "0.1",
        },
        {
            "market_date": "2020-01-03",
            "system_equity": "100.00",
            "market_value": "10.00",
            "invested_capital_ratio": "0.1",
        },
    ]

    metrics, _ = FormalP2ReplayRuntime._metrics(
        Decimal("100.00"),
        equity,
        [],
        intervention_decisions,
        tmp_path / "variants" / "variant.position-risk-fallback",
        "intervention.position-risk-base-policy",
    )

    assert metrics["decision_change_counts"] == Decimal("1")
    assert metrics["action_change_rate"] == Decimal("0.5")


def test_sentinel_digest_excludes_later_january_decisions() -> None:
    decisions = [
        (date(2020, 1, 2), "digest-2020-b"),
        (date(2020, 1, 3), "digest-2020-a"),
        (date(2021, 1, 4), "digest-2021-must-stay-out"),
    ]

    observed = _decision_digest_in_window(
        decisions,
        date(2020, 1, 1),
        date(2020, 1, 31),
    )

    assert observed == digest_value(["digest-2020-a", "digest-2020-b"])


def test_formal_replay_loads_industry_from_parent_event_run_by_governed_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = cast(
        tuple[_EventInput, ...],
        (
            SimpleNamespace(
                record=SimpleNamespace(identity=SimpleNamespace(security_id="security-b")),
                static_industry_id="industry-2",
            ),
            SimpleNamespace(
                record=SimpleNamespace(identity=SimpleNamespace(security_id="security-a")),
                static_industry_id="industry-1",
            ),
        ),
    )
    observed: dict[str, object] = {}
    mapping = SimpleNamespace(
        by_security_id={
            "security-a": SimpleNamespace(level1_industry_id="industry-1"),
            "security-b": SimpleNamespace(level1_industry_id="industry-2"),
        }
    )

    def load(path: Path, **kwargs: object) -> object:
        observed.update(path=path, **kwargs)
        return mapping

    monkeypatch.setattr(
        "quanthus.expert_strategy.formal_p2_replay.load_static_industry_mapping", load
    )

    assert _load_parent_industry_mapping(tmp_path, events) is mapping
    assert observed == {
        "path": tmp_path / "adm" / "static_industry_mapping.json",
        "expected_version": "static-level1-concentration-research-v1",
        "required_security_ids": ("security-a", "security-b"),
    }


def test_formal_replay_rejects_event_industry_drift_from_parent_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = cast(
        tuple[_EventInput, ...],
        (
            SimpleNamespace(
                record=SimpleNamespace(identity=SimpleNamespace(security_id="security-a")),
                static_industry_id="event-industry",
            ),
        ),
    )
    mapping = SimpleNamespace(
        by_security_id={
            "security-a": SimpleNamespace(level1_industry_id="parent-industry")
        }
    )
    monkeypatch.setattr(
        "quanthus.expert_strategy.formal_p2_replay.load_static_industry_mapping",
        lambda *_args, **_kwargs: mapping,
    )

    with pytest.raises(ExpertEvaluationError, match="formal_attribution_industry_mismatch"):
        _load_parent_industry_mapping(tmp_path, events)


def test_formal_replay_builds_cooldown_transition_with_atomic_evidence() -> None:
    ledger = UnitState.empty(unit_id="capital-unit-01", cash=Decimal("100000.00"))
    runtime = _UnitRuntime(
        ledger=ledger,
        lifecycle=initialize_position_lifecycle(
            ledger_state=ledger,
            as_of=datetime(2020, 1, 1, tzinfo=timezone.utc),
        ),
        panel_security_id="000001",
        formal_security_id="security-a",
        opportunity_id="opportunity-a",
    )
    evidence = (
        PositionEvidenceRef(
            PositionEvidenceKind.UNIT_SETTLEMENT, "settlement-a", "policy-v1"
        ),
        PositionEvidenceRef(PositionEvidenceKind.CALENDAR, "calendar-a", "schedule-v1"),
    )

    transition = _transition(
        runtime,
        PositionTransitionKind.COOLDOWN_COMPLETED,
        datetime(2020, 1, 2, tzinfo=timezone.utc),
        ("cooldown_completed",),
        evidence,
    )

    assert transition.evidence == evidence


def test_formal_replay_stable_id_normalizes_calendar_dates() -> None:
    assert _stable_id("calendar", "capital-unit-01", date(2020, 1, 2)) == _stable_id(
        "calendar", "capital-unit-01", "2020-01-02"
    )
