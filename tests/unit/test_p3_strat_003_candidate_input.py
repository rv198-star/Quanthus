from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quanthus.event_library.contracts import (
    EventIdentity,
    EventRecord,
    SplitAssignment,
)
from quanthus.expert_evaluation.serialization import ExpertEvaluationError, digest_value
from quanthus.expert_strategy.p3_strat_003 import P3Strat003CandidateInput
from quanthus.expert_strategy.p3_strat_003_cli import _parser

ROOT = Path(__file__).resolve().parents[2]
MARKET_TZ = ZoneInfo("Asia/Shanghai")
REQUEST_DIGEST = "sha256:9de79141514aa1941d276da854d734786bb2b5bb83310a1d3c7792d60c952bde"
SHA = "sha256:" + "1" * 64


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _write_fixture(root: Path, *, signal_date: date = date(2025, 1, 2)) -> Path:
    industry_unsigned = {
        "schema_version": "1.0.0",
        "mapping_version": "static-level1-concentration-research-v1",
        "usage": "static_non_pit_concentration_only",
        "source": {
            "provider": "synthetic",
            "endpoint": "test",
            "retrieved_at": "2026-09-19T00:00:00+00:00",
            "source_revision": "synthetic-v1",
        },
        "memberships": [
            {
                "security_id": "sec-1",
                "stock_code": "000001",
                "exchange": "SZSE",
                "level1_industry_id": "industry-1",
            }
        ],
    }
    industry = {**industry_unsigned, "artifact_digest": _canonical_digest(industry_unsigned)}
    industry_path = root / "industry.json"
    industry_path.write_text(
        json.dumps(industry, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    close = datetime.combine(signal_date, datetime.min.time().replace(hour=15), tzinfo=MARKET_TZ)
    record = EventRecord(
        identity=EventIdentity(
            security_id="sec-1",
            signal_date=signal_date,
            candidate_rule_id="candidate.p4-test",
            candidate_rule_version="1.0.0",
            snapshot_id="snapshot-test",
        ),
        candidate_rule_digest=SHA,
        snapshot_manifest_digest=SHA,
        knowledge_cutoff=close,
        signal_close=close,
        eligibility_evidence_digest=SHA,
        feature_window_manifest_digest=SHA,
        isolation_group_id="isolation-1",
        split_policy_id="split.test",
        split_policy_version="1.0.0",
        split_policy_digest=SHA,
        split_assignment=SplitAssignment.PERMANENT_BLIND,
    )
    event_path = root / "events.jsonl"
    event_path.write_text(
        json.dumps(
            {
                "event": record.to_dict(),
                "listing_code": "000001",
                "static_level1_industry_id": "industry-1",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    payload = {
        "schema_version": "quanthus.p3-strat-003-candidate-input.v1",
        "work_item": "P3-STRAT-003-B",
        "request_digest": REQUEST_DIGEST,
        "scope": {
            "decision_start": "2025-01-01",
            "decision_end": "2026-09-17",
            "execution_end": "2026-09-18",
        },
        "replay_bundle_digest": "sha256:" + "2" * 64,
        "events": {
            "path": event_path.name,
            "sha256": "sha256:" + hashlib.sha256(event_path.read_bytes()).hexdigest(),
            "count": 1,
        },
        "industry_mapping": {
            "path": industry_path.name,
            "sha256": "sha256:" + hashlib.sha256(industry_path.read_bytes()).hexdigest(),
        },
    }
    manifest = {**payload, "semantic_digest": digest_value(payload)}
    manifest_path = root / "candidate-input.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return manifest_path


def test_candidate_input_is_outcome_blind_and_digest_bound(tmp_path: Path) -> None:
    manifest = _write_fixture(tmp_path)
    loaded = P3Strat003CandidateInput.load(
        manifest,
        request_digest=REQUEST_DIGEST,
        decision_start=date(2025, 1, 1),
        decision_end=date(2026, 9, 17),
        execution_end=date(2026, 9, 18),
    )

    assert loaded.manifest_digest == json.loads(manifest.read_text())["semantic_digest"]
    assert loaded.replay_bundle_digest == "sha256:" + "2" * 64
    assert len(loaded.events) == 1
    assert loaded.events[0].panel_security_id == "000001"
    assert loaded.events[0].record.identity.signal_date == date(2025, 1, 2)
    assert loaded.events[0].static_industry_id == "industry-1"


def test_candidate_input_rejects_scope_and_payload_drift(tmp_path: Path) -> None:
    manifest = _write_fixture(tmp_path)

    with pytest.raises(ExpertEvaluationError, match="scope differs"):
        P3Strat003CandidateInput.load(
            manifest,
            request_digest=REQUEST_DIGEST,
            decision_start=date(2025, 1, 2),
            decision_end=date(2026, 9, 17),
            execution_end=date(2026, 9, 18),
        )

    events = tmp_path / "events.jsonl"
    events.write_text(events.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ExpertEvaluationError, match="checksum differs"):
        P3Strat003CandidateInput.load(
            manifest,
            request_digest=REQUEST_DIGEST,
            decision_start=date(2025, 1, 1),
            decision_end=date(2026, 9, 17),
            execution_end=date(2026, 9, 18),
        )


def test_candidate_input_rejects_out_of_window_event(tmp_path: Path) -> None:
    manifest = _write_fixture(tmp_path, signal_date=date(2024, 12, 31))
    with pytest.raises(ExpertEvaluationError, match="outside frozen decision scope"):
        P3Strat003CandidateInput.load(
            manifest,
            request_digest=REQUEST_DIGEST,
            decision_start=date(2025, 1, 1),
            decision_end=date(2026, 9, 17),
            execution_end=date(2026, 9, 18),
        )


def test_runtime_v11_forbids_outcome_label_before_d() -> None:
    path = ROOT / "config/research/p3-strat-003/runtime.v1.1.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    declared = raw.pop("semantic_digest")
    assert declared == digest_value(raw)
    assert raw["input_contract"]["candidate_input_manifest_binding"].startswith("required_at_D")
    assert raw["input_contract"]["outcome_label_binding"] == "forbidden_before_D_result_gate"
    assert raw["entrypoint"]["population_arguments_mutually_exclusive"] is True
    assert raw["execution"]["p6_mutation_authorized"] is False


def test_cli_requires_exactly_one_population_source() -> None:
    common = [
        "--repository-root",
        ".",
        "--output-root",
        "/tmp/out",
        "--provider-config",
        "provider.json",
        "--decision-start",
        "2025-01-01",
        "--decision-end",
        "2026-09-17",
        "--execution-end",
        "2026-09-18",
    ]
    parsed = _parser().parse_args([*common, "--candidate-input-manifest", "candidate.json"])
    assert parsed.candidate_input_manifest == "candidate.json"
    assert parsed.event_run_digest is None

    with pytest.raises(SystemExit):
        _parser().parse_args(common)
    with pytest.raises(SystemExit):
        _parser().parse_args(
            [
                *common,
                "--candidate-input-manifest",
                "candidate.json",
                "--event-run-digest",
                "sha256:" + "1" * 64,
            ]
        )
