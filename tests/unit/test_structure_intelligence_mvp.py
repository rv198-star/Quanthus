from __future__ import annotations

import unittest
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from quanthus.aggregator import (
    GovernedAction,
    ScoreActionPolicy,
    aggregate_governance,
    aggregate_score_and_map_action,
)
from quanthus.backtest.contracts import (
    BacktestRunSpec,
    BacktestSignal,
    EventType,
    MarketSession,
    OpenExecutionState,
    OrderSide,
)
from quanthus.backtest.engine import BacktestEngine
from quanthus.backtest.policy import load_execution_policy
from quanthus.contracts import SnapshotRef
from quanthus.expert_protocols import (
    ConstraintOutput,
    ConstraintScope,
    ExpertApplicability,
    ExpertContext,
    RequiredAction,
    ScoringOutput,
    VetoAuthority,
    VetoOutput,
    run_scoring_expert,
)
from quanthus.policy import find_project_root
from quanthus.structure_intelligence import (
    StructureMvpScoringExpert,
    StructureMvpState,
    StructureSourceRow,
    build_structure_research_adm,
    source_row_from_direct_dwd_projection,
    structure_feature_fact,
)

ROOT = find_project_root()
SHANGHAI = ZoneInfo("Asia/Shanghai")
POLICY_PATH = ROOT / "tests" / "fixtures" / "p2" / "execution-policy.synthetic.v1.json"
SHA = "sha256:" + "a" * 64


def _cutoff(day: date) -> datetime:
    return datetime.combine(day, time(15, 0), SHANGHAI)


def _adapt(day: date, *, high: str, low: str, close: str, volume: str) -> StructureSourceRow:
    return source_row_from_direct_dwd_projection(
        {
            "date": day,
            "security_id": "600001",
            "dwd_security_id": "security-001",
            "high": Decimal(high),
            "low": Decimal(low),
            "close": Decimal(close),
            "volume": Decimal(volume),
            "dwd_bar_source_revision": "dwd-synthetic-v1",
            "dwd_bar_source_artifact_sha256": SHA,
        },
        snapshot_id=f"snapshot-{day.isoformat()}",
        knowledge_cutoff=_cutoff(day),
    )


def _source_rows() -> tuple[StructureSourceRow, ...]:
    start = date(2026, 1, 1)
    rows: list[StructureSourceRow] = []
    for index in range(20):
        day = start + timedelta(days=index)
        rows.append(_adapt(day, high="10.00", low="9.00", close="9.60", volume="100"))
    values = (
        ("10.30", "9.90", "10.20", "150"),
        ("10.25", "10.00", "10.10", "105"),
        ("10.30", "10.05", "10.15", "110"),
    )
    for offset, (high, low, close, volume) in enumerate(values, start=20):
        day = start + timedelta(days=offset)
        rows.append(_adapt(day, high=high, low=low, close=close, volume=volume))
    return tuple(rows)


class StructureIntelligenceMvpTests(unittest.TestCase):
    def test_dwd_projection_to_adm_is_pit_deterministic_and_accepts_breakout_only_later(
        self,
    ) -> None:
        rows = _source_rows()
        first = build_structure_research_adm(
            rows,
            upstream_identity="dwd-synthetic-manifest-v1",
            policy_digest=SHA,
        )
        second = build_structure_research_adm(
            tuple(reversed(rows)),
            upstream_identity="dwd-synthetic-manifest-v1",
            policy_digest=SHA,
        )

        self.assertEqual(first.content_digest, second.content_digest)
        self.assertEqual(first.manifest_digest, second.manifest_digest)
        self.assertEqual(first.rows[19].state, StructureMvpState.UNAVAILABLE)
        self.assertEqual(first.rows[20].state, StructureMvpState.BREAKOUT_ATTEMPT)
        self.assertEqual(first.rows[21].state, StructureMvpState.BREAKOUT_ATTEMPT)
        self.assertEqual(first.rows[22].state, StructureMvpState.BREAKOUT_ACCEPTED)
        self.assertTrue(first.manifest["research_only"])
        self.assertFalse(first.manifest["execution_authorized"])
        self.assertIn("generic_dws", first.manifest["forbidden_claims"])
        self.assertIn("operational_ads", first.manifest["forbidden_claims"])

    def test_history_reset_prevents_structure_window_from_bridging_invalid_gap(self) -> None:
        rows = list(_source_rows()[:20])
        reset_day = date(2026, 2, 1)
        rows.append(
            StructureSourceRow(
                market_date=reset_day,
                security_code="600001",
                dwd_security_id="security-001",
                snapshot_id=f"snapshot-{reset_day.isoformat()}",
                knowledge_cutoff=_cutoff(reset_day),
                high=Decimal("10.30"),
                low=Decimal("9.90"),
                close=Decimal("10.20"),
                volume=Decimal("150"),
                source_revision="dwd-synthetic-v1",
                source_artifact_sha256=SHA,
                history_reset=True,
            )
        )
        adm = build_structure_research_adm(
            rows,
            upstream_identity="dwd-synthetic-manifest-v1",
            policy_digest=SHA,
        )
        self.assertEqual(adm.rows[-1].state, StructureMvpState.UNAVAILABLE)
        self.assertEqual(adm.rows[-1].reason_code, "insufficient_history")

    def test_structure_fact_can_change_existing_aggregator_action_and_then_fill_through_p2(
        self,
    ) -> None:
        adm = build_structure_research_adm(
            _source_rows(),
            upstream_identity="dwd-synthetic-manifest-v1",
            policy_digest=SHA,
        )
        accepted = adm.rows[-1]
        self.assertEqual(accepted.state, StructureMvpState.BREAKOUT_ACCEPTED)
        structure_fact = structure_feature_fact(adm, accepted)
        context = ExpertContext(
            security_code=accepted.security_code,
            snapshot_id=accepted.snapshot_id,
            knowledge_cutoff=accepted.knowledge_cutoff,
            position_state="empty",
            feature_facts=(structure_fact,),
            label_results=(),
        )
        snapshot_evidence = next(
            item for item in context.evidence_refs if item.kind.value == "snapshot"
        )
        system_veto = VetoOutput(
            expert_id="system.structure-mvp-guardian",
            expert_version="0.1.0",
            applicability=ExpertApplicability.APPLICABLE,
            applicability_reason="mvp_system_guardian",
            reason_code="mvp_system_allows",
            evidence=(snapshot_evidence,),
            authority=VetoAuthority.SYSTEM,
            allow=True,
            required_action=RequiredAction.NONE,
        )
        base_constraint = ConstraintOutput(
            expert_id="system.structure-mvp-position",
            expert_version="0.1.0",
            applicability=ExpertApplicability.APPLICABLE,
            applicability_reason="mvp_position_boundary",
            reason_code="mvp_position_allowed",
            evidence=(snapshot_evidence,),
            minimum_position=Decimal("0"),
            maximum_position=Decimal("0.30"),
            stop_rule="existing_stop_policy_preserved",
            hold_rule="existing_hold_policy_preserved",
            scope=ConstraintScope.UNIT,
        )
        governance = aggregate_governance(context, (system_veto,), (base_constraint,))
        baseline = ScoringOutput(
            expert_id="expert.baseline-mvp",
            expert_version="0.1.0",
            applicability=ExpertApplicability.APPLICABLE,
            applicability_reason="mvp_baseline",
            reason_code="mvp_baseline_score",
            evidence=(snapshot_evidence,),
            signal=Decimal("0.30"),
            confidence=Decimal("1.00"),
        )
        policy = ScoreActionPolicy(
            policy_version="structure-mvp-action-v1",
            minimum_entry_score=Decimal("0.50"),
        )
        baseline_result = aggregate_score_and_map_action(
            context,
            governance,
            (baseline,),
            policy,
        )
        self.assertEqual(baseline_result.final_action, GovernedAction.NO_ACTION)

        structure_output = run_scoring_expert(StructureMvpScoringExpert(), context)
        combined = aggregate_score_and_map_action(
            context,
            governance,
            (baseline, structure_output),
            policy,
        )
        self.assertEqual(combined.final_action, GovernedAction.TRIAL_ENTRY)
        self.assertEqual(combined.aggregate_score, Decimal("0.55"))

        next_day = accepted.market_date + timedelta(days=1)
        signal = BacktestSignal(
            signal_id="structure-mvp-signal-001",
            snapshot=SnapshotRef(
                market_date=accepted.market_date,
                knowledge_cutoff=accepted.knowledge_cutoff,
                source_revision=adm.manifest_digest,
                snapshot_id=accepted.snapshot_id,
            ),
            decision_at=accepted.knowledge_cutoff + timedelta(minutes=5),
            profile_id="close-to-next-open-v1",
            window_id=None,
            security_code=accepted.security_code,
            side=OrderSide.BUY,
            unit_id="unit-001",
            quantity=100,
            aggregate_score=combined.aggregate_score,
            reason_code="structure_mvp_entry",
            decision_version="structure-mvp-action-v1",
        )
        execution_policy = load_execution_policy(POLICY_PATH)
        engine = BacktestEngine(execution_policy)
        session = MarketSession(
            session_date=next_day,
            open_at=datetime.combine(next_day, time(9, 30), SHANGHAI),
            close_at=datetime.combine(next_day, time(15, 0), SHANGHAI),
            security_code=accepted.security_code,
            open_price=Decimal("10.20"),
            close_price=Decimal("10.30"),
            open_execution_state=OpenExecutionState.EXECUTABLE,
            source_revision="p2-structure-mvp-synthetic-v1",
            snapshot_id=f"execution-{next_day.isoformat()}",
        )
        run = BacktestRunSpec(
            code_version="structure-mvp-test",
            parameter_hash=adm.manifest_digest,
            data_snapshot_id=accepted.snapshot_id,
            feature_version="structure-mvp-0.1.0",
            rule_version="structure-mvp-action-v1",
            run_id="structure-mvp-p2-001",
            execution_policy_version=execution_policy.policy_version,
            execution_profile_id="close-to-next-open-v1",
            schedule_version="close-to-next-open-synthetic-v1",
            official=False,
            data_content_digest=adm.content_digest,
            data_manifest_sha256=adm.manifest_digest,
        )
        result = engine.run_session(
            run=run,
            signal=signal,
            session=session,
            initial_cash=Decimal("100000"),
        )
        self.assertEqual(result.result_summary["fills"], 1)
        self.assertIn(EventType.FILL, tuple(event.event_type for event in result.events))


if __name__ == "__main__":
    unittest.main()
