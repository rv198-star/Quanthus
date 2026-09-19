from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.expert_evaluation.serialization import ExpertEvaluationError, canonical_json_bytes
from quanthus.expert_strategy import p3_strat_003
from quanthus.expert_strategy.formal_attribution import FormalReplayEvidence

ROOT = Path(__file__).resolve().parents[2]


def _evidence(variant_id: str, token: str) -> FormalReplayEvidence:
    metrics = {
        "capital_constrained_net_total_return_delta": Decimal("0.01"),
        "maximum_drawdown_delta": Decimal("0.02"),
        "tail_loss_delta": Decimal("0.03"),
        "average_invested_capital_delta": Decimal("0.04"),
        "time_in_market_delta": Decimal("0.05"),
        "turnover_delta": Decimal("0.06"),
        "fee_delta": Decimal("7"),
        "missed_opportunity_count": Decimal("8"),
        "decision_change_counts": Decimal("9"),
        "action_change_rate": Decimal("0.10"),
    }
    return FormalReplayEvidence(
        variant_id=f"variant.{variant_id}",
        intervention_id=token,
        metrics=metrics,
        replay_digest=f"sha256:{variant_id.lower() * 64}"[:71],
        reconciliation_digest="sha256:" + "1" * 64,
        decision_trace_digest="sha256:" + "2" * 64,
        opportunity_trace_digest="sha256:" + "3" * 64,
        daily_return_artifact_digest="sha256:" + "4" * 64,
    )


def test_runtime_contract_is_result_blind_and_d_binds_b_inputs() -> None:
    path = ROOT / "config/research/p3-strat-003/runtime.v1.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    declared = raw.pop("semantic_digest")
    canonical = json.dumps(
        raw,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    assert declared == "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert raw["entrypoint"]["variants"] == ["B", "T", "R", "TR"]
    assert raw["entrypoint"]["variant_order_fixed"] is True
    assert raw["execution"]["provider_builds_per_batch"] == 1
    assert raw["execution"]["feature_panel_builds_per_batch"] == 1
    assert raw["execution"]["cross_batch_cache_for_independent_reproduction"] is False
    assert raw["execution"]["p6_mutation_authorized"] is False
    assert raw["engineering_evidence"]["recent_history_result_read_authorized"] is False
    assert (
        raw["input_contract"]["manifest_digest_binding"]
        == "required_at_D_admission_after_B_acceptance"
    )
    assert raw["input_contract"]["provider_schema"] == "quanthus.replay-data-provider.v3"
    assert (
        raw["input_contract"]["provider_partition"]
        == p3_strat_003.RECENT_HISTORY_PARTITION
    )
    assert raw["input_contract"]["batch_input_identity_required"] is True


def test_request_is_frozen_and_only_four_variants_are_admitted() -> None:
    request = p3_strat_003.P3Strat003Request.load(ROOT)

    assert request.digest == p3_strat_003.REQUEST_DIGEST
    assert [item["variant_id"] for item in request.value["variants"]] == ["B", "T", "R", "TR"]
    assert request.variant("B")["entry_scorer"]["control_id"] == "control.p4-candidate-pass-through"
    with pytest.raises(ExpertEvaluationError, match="not admitted"):
        request.variant("X")


def test_research_variant_tokens_are_exact_and_fail_closed() -> None:
    assert {
        item: p3_strat_003.P3Strat003ReplayRuntime.token_for(item)
        for item in p3_strat_003.VARIANT_IDS
    } == {
        "B": "research.p3-strat-003.B",
        "T": "research.p3-strat-003.T",
        "R": "research.p3-strat-003.R",
        "TR": "research.p3-strat-003.TR",
    }
    assert p3_strat_003.P3Strat003ReplayRuntime.variant_for(
        "research.p3-strat-003.TR"
    ) == "TR"
    with pytest.raises(ExpertEvaluationError, match="not admitted"):
        p3_strat_003.P3Strat003ReplayRuntime.token_for("X")
    with pytest.raises(ExpertEvaluationError, match="not admitted"):
        p3_strat_003.P3Strat003ReplayRuntime.variant_for("research.unknown")


class _RouteRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def evaluate_entry_control(self, **kwargs: object) -> str:
        self.calls.append(("control", kwargs))
        return "control"

    def evaluate_entry(self, **kwargs: object) -> str:
        self.calls.append(("trend", kwargs))
        return "trend"


def test_factorial_routes_only_the_declared_two_factors() -> None:
    runtime = object.__new__(p3_strat_003.P3Strat003ReplayRuntime)
    runner = _RouteRunner()
    common = {
        "event_id": "event-1",
        "context": object(),
        "trend_state": "stable",
        "entry_evidence_ready": True,
    }

    assert runtime._evaluate_entry_decision(
        runner,
        intervention_id=runtime.token_for("B"),
        **common,
    ) == "control"
    assert runner.calls[-1][1]["position_risk_enabled"] is False

    assert runtime._evaluate_entry_decision(
        runner,
        intervention_id=runtime.token_for("R"),
        **common,
    ) == "control"
    assert runner.calls[-1][1]["position_risk_enabled"] is True

    assert runtime._evaluate_entry_decision(
        runner,
        intervention_id=runtime.token_for("T"),
        **common,
    ) == "trend"
    assert runner.calls[-1][1]["intervention_id"] == "intervention.position-risk-base-policy"

    assert runtime._evaluate_entry_decision(
        runner,
        intervention_id=runtime.token_for("TR"),
        **common,
    ) == "trend"
    assert runner.calls[-1][1]["intervention_id"] is None

    assert runtime._held_intervention_id(runtime.token_for("B")) == (
        "intervention.position-risk-base-policy"
    )
    assert runtime._held_intervention_id(runtime.token_for("T")) == (
        "intervention.position-risk-base-policy"
    )
    assert runtime._held_intervention_id(runtime.token_for("R")) is None
    assert runtime._held_intervention_id(runtime.token_for("TR")) is None


@dataclass
class _PanelReport:
    row_count: int = 123
    digest: str = "sha256:" + "5" * 64


@dataclass
class _Panel:
    report: _PanelReport


class _FakeRuntime:
    instances = 0

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args
        type(self).instances += 1
        self.decision_start = kwargs["decision_start"]
        self.decision_end = kwargs["decision_end"]
        self.execution_end = kwargs["execution_end"]
        self.parent_event_run_digest = kwargs["parent_event_run_digest"]
        self.provider_config = kwargs["provider_config"]
        self.panel = _Panel(_PanelReport())
        self.feature_panel_bytes = 456
        self.runs: list[str] = []
        self.closed = False

    @staticmethod
    def token_for(variant_id: str) -> str:
        return f"research.p3-strat-003.{variant_id}"

    @staticmethod
    def _code_version() -> str:
        return "p3-strat-003-recent-history-replay-1.0.0"

    def batch_input_identity(self) -> dict[str, object]:
        return {
            "request_digest": p3_strat_003.REQUEST_DIGEST,
            "formal_strategy_digest": "sha256:" + "7" * 64,
            "code_version": self._code_version(),
            "scope": {
                "decision_start": self.decision_start.isoformat(),
                "decision_end": self.decision_end.isoformat(),
                "execution_end": self.execution_end.isoformat(),
            },
            "parent_event_run_digest": self.parent_event_run_digest,
            "provider_config_path": self.provider_config,
            "provider_config_digest": "sha256:" + "8" * 64,
            "provider_partition": p3_strat_003.RECENT_HISTORY_PARTITION,
        }

    @property
    def batch_input_digest(self) -> str:
        return p3_strat_003.digest_value(self.batch_input_identity())

    def run_research_variant(
        self, variant_id: str, output_root: Path
    ) -> tuple[FormalReplayEvidence, dict[str, bytes]]:
        self.runs.append(variant_id)
        return _evidence(variant_id, self.token_for(variant_id)), {
            "manifest.json": canonical_json_bytes(
                {
                    "variant": variant_id,
                    "output": output_root.name,
                    "research_request_digest": p3_strat_003.REQUEST_DIGEST,
                    "research_variant_id": variant_id,
                    "research_batch_input_digest": self.batch_input_digest,
                    "research_code_version": self._code_version(),
                }
            ),
            "decisions.json": canonical_json_bytes([{"variant": variant_id}]),
        }

    def close(self) -> None:
        self.closed = True


def test_composite_backend_builds_one_runtime_and_persists_exact_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeRuntime.instances = 0
    monkeypatch.setattr(p3_strat_003, "P3Strat003ReplayRuntime", _FakeRuntime)

    backend = p3_strat_003.P3Strat003CompositeBackend(
        ROOT,
        tmp_path / "batch",
        decision_start=date(2025, 1, 2),
        decision_end=date(2026, 9, 17),
        execution_end=date(2026, 9, 18),
        parent_event_run_digest="sha256:" + "6" * 64,
        provider_config="config/research/p3-strat-003/provider.test.json",
    )
    result = backend.execute_all()

    assert _FakeRuntime.instances == 1
    assert backend.runtime.runs == ["B", "T", "R", "TR"]
    assert set(result.variants) == {"B", "T", "R", "TR"}
    assert result.telemetry["provider_builds"] == 1
    assert result.telemetry["feature_panel_builds"] == 1
    assert result.telemetry["feature_panel_rows"] == 123
    assert result.telemetry["feature_panel_bytes"] == 456
    assert set(result.telemetry["variant_elapsed_seconds"]) == {"B", "T", "R", "TR"}
    assert result.telemetry["variant_count"] == 4
    assert result.telemetry["cross_batch_cache_used"] is False
    assert json.loads((tmp_path / "batch/batch-telemetry.json").read_text())[
        "provider_reuse_disposition"
    ] == "one_open_session_shared_by_B_T_R_TR"
    for variant_id in p3_strat_003.VARIANT_IDS:
        root = tmp_path / "batch/variants" / f"variant.{variant_id}"
        assert (root / "evidence.json").is_file()
        assert (root / "manifest.json").is_file()
    backend.close()
    assert backend.runtime.closed is True


def test_composite_backend_resumes_only_missing_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeRuntime.instances = 0
    monkeypatch.setattr(p3_strat_003, "P3Strat003ReplayRuntime", _FakeRuntime)
    root = tmp_path / "batch"
    with p3_strat_003.P3Strat003CompositeBackend(
        ROOT,
        root,
        decision_start=date(2025, 1, 2),
        decision_end=date(2026, 9, 17),
        execution_end=date(2026, 9, 18),
        parent_event_run_digest="sha256:" + "6" * 64,
        provider_config="config/research/p3-strat-003/provider.test.json",
    ) as first:
        first.execute_variant("B")
        assert first.runtime.runs == ["B"]

    with p3_strat_003.P3Strat003CompositeBackend(
        ROOT,
        root,
        decision_start=date(2025, 1, 2),
        decision_end=date(2026, 9, 17),
        execution_end=date(2026, 9, 18),
        parent_event_run_digest="sha256:" + "6" * 64,
        provider_config="config/research/p3-strat-003/provider.test.json",
    ) as resumed:
        result = resumed.execute_all()
        assert resumed.runtime.runs == ["T", "R", "TR"]
        assert result.telemetry["variant_disposition"]["B"] == "reused_existing"
        assert result.telemetry["variant_elapsed_seconds"]["B"] == 0.0


def test_existing_batch_rejects_semantic_input_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(p3_strat_003, "P3Strat003ReplayRuntime", _FakeRuntime)
    root = tmp_path / "batch"
    with p3_strat_003.P3Strat003CompositeBackend(
        ROOT,
        root,
        decision_start=date(2025, 1, 2),
        decision_end=date(2026, 9, 17),
        execution_end=date(2026, 9, 18),
        parent_event_run_digest="sha256:" + "6" * 64,
        provider_config="config/research/p3-strat-003/provider.test.json",
    ) as first:
        first.execute_variant("B")

    with pytest.raises(ExpertEvaluationError, match="immutable artifact differs"):
        p3_strat_003.P3Strat003CompositeBackend(
            ROOT,
            root,
            decision_start=date(2025, 1, 2),
            decision_end=date(2026, 9, 16),
            execution_end=date(2026, 9, 17),
            parent_event_run_digest="sha256:" + "6" * 64,
            provider_config="config/research/p3-strat-003/provider.test.json",
        )


class _FailingRuntime(_FakeRuntime):
    def run_research_variant(
        self, variant_id: str, output_root: Path
    ) -> tuple[FormalReplayEvidence, dict[str, bytes]]:
        (output_root / "partial.txt").write_text("partial", encoding="ascii")
        raise RuntimeError(f"synthetic failure {variant_id}")


def test_variant_publication_is_atomic_and_failed_staging_is_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(p3_strat_003, "P3Strat003ReplayRuntime", _FailingRuntime)
    root = tmp_path / "batch"
    backend = p3_strat_003.P3Strat003CompositeBackend(
        ROOT,
        root,
        decision_start=date(2025, 1, 2),
        decision_end=date(2026, 9, 17),
        execution_end=date(2026, 9, 18),
        parent_event_run_digest="sha256:" + "6" * 64,
        provider_config="config/research/p3-strat-003/provider.test.json",
    )
    with pytest.raises(RuntimeError, match="synthetic failure B"):
        backend.execute_variant("B")
    assert not (root / "variants/variant.B").exists()
    assert list((root / "variants").glob(".variant.B.*")) == []
    backend.close()


def test_visible_partial_variant_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(p3_strat_003, "P3Strat003ReplayRuntime", _FakeRuntime)
    root = tmp_path / "batch"
    partial = root / "variants/variant.B"
    partial.mkdir(parents=True)
    (partial / "manifest.json").write_text("{}", encoding="ascii")
    backend = p3_strat_003.P3Strat003CompositeBackend(
        ROOT,
        root,
        decision_start=date(2025, 1, 2),
        decision_end=date(2026, 9, 17),
        execution_end=date(2026, 9, 18),
        parent_event_run_digest="sha256:" + "6" * 64,
        provider_config="config/research/p3-strat-003/provider.test.json",
    )
    with pytest.raises(ExpertEvaluationError, match="publication is incomplete"):
        backend.execute_variant("B")
    backend.close()


def test_independent_backends_rebuild_without_cross_batch_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeRuntime.instances = 0
    monkeypatch.setattr(p3_strat_003, "P3Strat003ReplayRuntime", _FakeRuntime)
    results = []
    for name in ("primary", "cold"):
        with p3_strat_003.P3Strat003CompositeBackend(
            ROOT,
            tmp_path / name,
            decision_start=date(2025, 1, 2),
            decision_end=date(2026, 9, 17),
            execution_end=date(2026, 9, 18),
            parent_event_run_digest="sha256:" + "6" * 64,
            provider_config="config/research/p3-strat-003/provider.test.json",
        ) as backend:
            results.append(backend.execute_all())
    assert _FakeRuntime.instances == 2
    assert results[0].request_digest == results[1].request_digest
    for variant_id in p3_strat_003.VARIANT_IDS:
        left = tmp_path / "primary/variants" / f"variant.{variant_id}/evidence.json"
        right = tmp_path / "cold/variants" / f"variant.{variant_id}/evidence.json"
        assert left.read_bytes() == right.read_bytes()
    assert results[0].telemetry["cross_batch_cache_used"] is False
    assert results[1].telemetry["cross_batch_cache_used"] is False
    assert results[0].digest == results[1].digest
    assert (tmp_path / "primary/batch-result.json").read_bytes() == (
        tmp_path / "cold/batch-result.json"
    ).read_bytes()


def test_composite_backend_fails_if_variant_order_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(p3_strat_003, "P3Strat003ReplayRuntime", _FakeRuntime)
    backend = p3_strat_003.P3Strat003CompositeBackend(
        ROOT,
        tmp_path / "batch",
        decision_start=date(2025, 1, 2),
        decision_end=date(2026, 9, 17),
        execution_end=date(2026, 9, 18),
        parent_event_run_digest="sha256:" + "6" * 64,
        provider_config="config/research/p3-strat-003/provider.test.json",
    )
    with pytest.raises(ExpertEvaluationError, match="requires earlier variants"):
        backend.execute_variant("T")
    backend.close()
