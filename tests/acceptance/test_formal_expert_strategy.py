from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import quanthus.expert_strategy.production as formal
from quanthus.backtest.baseline_research import (
    BaselineFeaturePanelReport,
    BaselineFeatureRow,
)
from quanthus.event_library.contracts import EventIdentity, EventRecord, SplitAssignment
from quanthus.expert_evaluation.production import _EventInput
from quanthus.expert_evaluation.runner import bind_evaluation_specs, run_evaluation
from quanthus.expert_evaluation.serialization import ExpertEvaluationError
from quanthus.expert_strategy import ExpertStrategyRegistry

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_ROOT = ROOT / "config/expert-evaluations"
PARENT_DIGEST = "sha256:8b9d184c2947b82d1a98440e7e6313e3659735fa352ea5dd72ec4b90fc80050c"


def _event() -> EventRecord:
    digest = "sha256:" + "a" * 64
    shanghai = ZoneInfo("Asia/Shanghai")
    return EventRecord(
        EventIdentity(
            "security-cn-000001",
            date(2020, 1, 6),
            "p4-event.price-volume-state-transition",
            "1.0.0",
            "snapshot-v1",
        ),
        digest,
        digest,
        datetime(2020, 1, 6, 15, tzinfo=shanghai),
        datetime(2020, 1, 6, 15, tzinfo=shanghai),
        digest,
        digest,
        "security-cn-000001:2020-01",
        "p4-event-date-split",
        "1.0.0",
        digest,
        SplitAssignment.DEVELOPMENT,
    )


def _feature() -> BaselineFeatureRow:
    return BaselineFeatureRow(
        date(2020, 1, 6),
        "000001",
        Decimal("12"),
        Decimal("120"),
        Decimal("110"),
        Decimal("100"),
        Decimal("0.1"),
        Decimal("118"),
        Decimal("60"),
        Decimal("58"),
        Decimal("1.1"),
        Decimal("0.2"),
        Decimal("0.01"),
        Decimal("1000000"),
        300,
        True,
        True,
    )


class _Panel:
    report = BaselineFeaturePanelReport(
        "3.0.0",
        "p3-baseline-features-research-v1",
        "sha256:" + "b" * 64,
        "snapshot-v1",
        "sha256:" + "c" * 64,
        date(2020, 1, 1),
        date(2020, 1, 31),
        1,
        1,
        1,
        0,
        0,
        0,
        "sha256:" + "d" * 64,
        "sha256:" + "e" * 64,
    )

    def __enter__(self) -> _Panel:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def feature(self, market_date: date, security_id: str) -> BaselineFeatureRow:
        assert (market_date, security_id) == (date(2020, 1, 6), "000001")
        return _feature()


class _Provider:
    def __init__(self, _config: object) -> None:
        pass

    def open(self, **kwargs: object) -> object:
        assert kwargs == {
            "scope_start": date(2020, 1, 1),
            "scope_end": date(2020, 1, 31),
        }
        reader = SimpleNamespace(
            bundle=object(),
            data_content_digest="sha256:" + "d" * 64,
            data_manifest_sha256="sha256:" + "e" * 64,
        )
        return nullcontext(SimpleNamespace(reader=reader))


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_formal_sentinel_closes_registry_and_reproduces_byte_identically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = ExpertStrategyRegistry(ROOT)
    job_input = formal.formal_sentinel_job_input(PARENT_DIGEST, registry)
    specs = bind_evaluation_specs(job_input, REGISTRY_ROOT, registry)
    assert tuple(
        (item.assessment_id, item.assessment_version, item.digest) for item in specs
    ) == registry.evaluation_spec_identities
    event = _event()
    monkeypatch.setattr(
        formal,
        "_find_parent_package",
        lambda *_args: (tmp_path / "event-library", tmp_path / "label-corpus"),
    )
    monkeypatch.setattr(
        formal,
        "_load_events",
        lambda *_args: (_EventInput(event, "industry.test", "000001"),),
    )
    monkeypatch.setattr(formal, "ReplayDataProvider", _Provider)
    monkeypatch.setattr(
        formal.BaselineFeaturePanel,
        "build",
        lambda *_args, **_kwargs: _Panel(),
    )
    producer = formal.FormalStrategySentinelProducer(registry, tmp_path / "event-runs")

    first = run_evaluation(
        job_input,
        registry_root=REGISTRY_ROOT,
        staging=tmp_path / "attempt-a",
        run_root=tmp_path / "runs-a",
        producer=producer,
        formal_strategy_resolver=registry,
    )
    second = run_evaluation(
        job_input,
        registry_root=REGISTRY_ROOT,
        staging=tmp_path / "attempt-b",
        run_root=tmp_path / "runs-b",
        producer=producer,
        formal_strategy_resolver=registry,
    )

    assert first.run_id == second.run_id
    assert first.manifest_digest == second.manifest_digest
    assert _files(first.root) == _files(second.root)
    scorecard = json.loads((first.root / "scorecard.json").read_bytes())
    assert scorecard["economic_evidence"] is False
    assert scorecard["sealed_partition_access"] is False
    assert scorecard["decision_count"] == 1
    assert scorecard["action_counts"] == {"trial_entry": 1}
    assert first.manifest["strategy_identity"] == {
        "strategy_id": registry.card.strategy_id,
        "strategy_version": registry.card.strategy_version,
        "card_digest": registry.card.semantic_digest,
        "attribution_plan_digest": registry.sentinel_plan_digest,
    }
    serialized = b"".join(_files(first.root).values())
    assert b"order_id" not in serialized
    assert b"execution_authorized" not in serialized


def test_formal_producer_and_resolver_reject_scope_or_identity_drift(tmp_path: Path) -> None:
    registry = ExpertStrategyRegistry(ROOT)
    job_input = formal.formal_sentinel_job_input(PARENT_DIGEST, registry)
    producer, resolver = formal.formal_producer_for(job_input, REGISTRY_ROOT)

    assert producer.registry.topology_manifest.digest == registry.topology_manifest.digest
    assert resolver.expected_bindings(job_input.formal_strategy, REGISTRY_ROOT) == (
        registry.evaluation_spec_identities,
        registry.intervention_ids,
    )
    drifted = replace(job_input, requested_interventions=("intervention.unknown",))
    with pytest.raises(ExpertEvaluationError, match="intervention_not_admitted"):
        bind_evaluation_specs(drifted, REGISTRY_ROOT, registry)
