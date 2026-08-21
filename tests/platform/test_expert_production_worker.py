from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import quanthus.expert_evaluation.production as production
from quanthus.backtest.baseline_research import BaselineFeatureRow
from quanthus.event_library.contracts import EventIdentity, EventRecord, SplitAssignment
from quanthus.expert_evaluation.contracts import ArtifactRef, ExpertEvaluationSpec, ExpertScorecard
from quanthus.expert_evaluation.resolver import P4LabelRow
from quanthus.expert_evaluation.runner import bind_evaluation_specs
from quanthus.expert_evaluation.serialization import ExpertEvaluationError, decode_artifact

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "config/expert-evaluations"


def _event() -> EventRecord:
    digest = "sha256:" + "a" * 64
    shanghai = ZoneInfo("Asia/Shanghai")
    return EventRecord(
        EventIdentity(
            "security-cn-000001",
            date(2020, 6, 30),
            "p4-event.price-volume-state-transition",
            "1.0.0",
            "snapshot-v1",
        ),
        digest,
        digest,
        datetime(2020, 6, 30, 15, tzinfo=shanghai),
        datetime(2020, 6, 30, 15, tzinfo=shanghai),
        digest,
        digest,
        "security-cn-000001:2020-06",
        "p4-event-date-split",
        "1.0.0",
        digest,
        SplitAssignment.DEVELOPMENT,
    )


def _feature() -> BaselineFeatureRow:
    return BaselineFeatureRow(
        date(2020, 6, 30),
        "security-cn-000001",
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


def test_parent_package_binds_one_outer_run_when_ab_contents_are_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "sha256:" + "d" * 64
    event = SimpleNamespace(manifest_sha256=digest, content_digest=digest)
    label = SimpleNamespace(manifest_sha256=digest, content_digest=digest)
    monkeypatch.setattr(production, "verify_physical_event_scan", lambda _root: event)
    monkeypatch.setattr(production, "verify_physical_label_corpus", lambda _root: label)

    admitted_digest = ""
    for run_id in ("run_a", "run_b"):
        run = tmp_path / "runs" / run_id
        (run / "event-library").mkdir(parents=True)
        (run / "label-corpus").mkdir()
        (run / "label-corpus" / "manifest.json").write_text(
            json.dumps(
                {
                    "event_library": {
                        "manifest_sha256": digest,
                        "content_digest": digest,
                    }
                }
            ),
            encoding="utf-8",
        )
        payload = json.dumps(
            {
                "schema_version": "quanthus.p4-event-run.v2",
                "run_id": run_id,
                "event_manifest_sha256": digest,
                "event_content_digest": digest,
                "label_manifest_sha256": digest,
                "label_content_digest": digest,
            },
            sort_keys=True,
        ).encode()
        (run / "run-manifest.json").write_bytes(payload)
        if run_id == "run_a":
            admitted_digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"

    event_root, label_root = production._find_parent_package(
        tmp_path, (admitted_digest,)
    )

    assert event_root.parent.name == "run_a"
    assert label_root.parent.name == "run_a"


def test_parent_package_rejects_inner_digest_as_parent_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "sha256:" + "d" * 64
    run = tmp_path / "run_a"
    (run / "event-library").mkdir(parents=True)
    (run / "label-corpus").mkdir()
    (run / "label-corpus" / "manifest.json").write_text(
        json.dumps(
            {
                "event_library": {
                    "manifest_sha256": digest,
                    "content_digest": digest,
                }
            }
        ),
        encoding="utf-8",
    )
    (run / "run-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "quanthus.p4-event-run.v2",
                "run_id": "run_a",
                "event_manifest_sha256": digest,
                "event_content_digest": digest,
                "label_manifest_sha256": digest,
                "label_content_digest": digest,
            }
        ),
        encoding="utf-8",
    )
    result = SimpleNamespace(manifest_sha256=digest, content_digest=digest)
    monkeypatch.setattr(production, "verify_physical_event_scan", lambda _root: result)
    monkeypatch.setattr(production, "verify_physical_label_corpus", lambda _root: result)

    with pytest.raises(ExpertEvaluationError, match="parent_run_missing"):
        production._find_parent_package(tmp_path, (digest,))


class _Panel:
    def __enter__(self) -> _Panel:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def feature(self, market_date: date, security_id: str) -> BaselineFeatureRow:
        assert (market_date, security_id) == (date(2020, 6, 30), "security-cn-000001")
        return _feature()


class _Provider:
    def __init__(self, _config: object) -> None:
        pass

    def open(self, **_kwargs: object) -> object:
        reader = SimpleNamespace(
            bundle=object(),
            data_content_digest="sha256:" + "b" * 64,
            data_manifest_sha256="sha256:" + "c" * 64,
        )
        return nullcontext(SimpleNamespace(reader=reader))


def test_production_worker_path_is_deterministic_and_emits_v2_scorecard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    label_digest = "sha256:" + "d" * 64
    job_input = production.production_job_input(label_digest)
    specs = bind_evaluation_specs(job_input, REGISTRY)
    assert isinstance(specs[0], ExpertEvaluationSpec)
    event = _event()
    label = P4LabelRow(
        event.event_id,
        date(2020, 7, 1),
        "settled",
        "positive",
        "settled_by_frozen_card",
        date(2020, 7, 20),
        "sha256:" + "e" * 64,
    )
    monkeypatch.setattr(
        production,
        "_find_parent_package",
        lambda *_args: (tmp_path / "event-library", tmp_path / "label-corpus"),
    )
    monkeypatch.setattr(
        production,
        "_load_events",
        lambda *_args: (production._EventInput(event, "industry.test"),),
    )
    monkeypatch.setattr(
        production,
        "load_verified_p4_labels",
        lambda _root: (
            {event.event_id: label},
            ArtifactRef("p4-label-corpus.accepted", "1.0.0", label_digest),
        ),
    )
    monkeypatch.setattr(production, "ReplayDataProvider", _Provider)
    monkeypatch.setattr(
        production,
        "_trading_sessions",
        lambda *_args: tuple(date(2020, 6, day) for day in range(9, 31)),
    )
    monkeypatch.setattr(
        production.BaselineFeaturePanel,
        "build",
        lambda *_args, **_kwargs: _Panel(),
    )
    producer = production.TrendStabilityOperationProducer(REGISTRY, tmp_path / "event-runs")

    first = producer.produce(job_input, specs, lambda *_args: None, lambda: False)
    second = producer.produce(job_input, specs, lambda *_args: None, lambda: False)

    assert [(item.path, item.payload) for item in first] == [
        (item.path, item.payload) for item in second
    ]
    artifacts = {item.path: item for item in first}
    scorecard = decode_artifact(artifacts["scorecard.json"].payload or b"", ExpertScorecard)
    assert scorecard.sample_ledger.semantic_dict() == {
        "eligible": 1,
        "applicable": 1,
        "abstained": 0,
        "settled": 1,
        "pending": 0,
        "unavailable": 0,
        "not_executable": 0,
        "censored": 0,
    }
    assert "no effectiveness claim" in " ".join(scorecard.limitations)
