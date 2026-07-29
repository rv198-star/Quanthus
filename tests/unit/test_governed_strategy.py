import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from quanthus.aggregator import (
    GovernedAction,
    ScoreActionPolicy,
    aggregate_governance,
    aggregate_score_and_map_action,
)
from quanthus.backtest.baseline_research import BaselineFeatureRow
from quanthus.backtest.contracts import DecisionDisposition
from quanthus.backtest.strategy_replay import StrategyReplayFrame
from quanthus.contracts import SnapshotRef
from quanthus.exit_arbitration import ExitDecisionStatus, ExitTriggerSource
from quanthus.expert_protocols import (
    ExpertContext,
    run_constraint_expert,
    run_scoring_expert,
    run_veto_expert,
)
from quanthus.feature_registry import (
    FeatureCutoffRule,
    FeatureLineage,
    FeatureSourceEvidence,
)
from quanthus.governed_strategy import (
    CandidateScoringExpert,
    CandidateStateVeto,
    EntryEvidenceVeto,
    TrialPositionConstraint,
    evaluate_governed_labels,
    evaluate_governed_position_review,
    governed_position_decision_event,
    load_governed_feature_registry,
)
from quanthus.label_registry import FeatureFact, load_label_registry
from quanthus.policy import find_project_root, load_policy
from quanthus.position_lifecycle import PositionStage
from quanthus.strategy_policy import load_governed_strategy_policy

ROOT = find_project_root()
POLICY_PATH = ROOT / "config" / "p3-governed-strategy.research.v1.json"
FEATURE_PATH = ROOT / "config" / "p3-governed-feature-extension.research.v1.json"
LABEL_PATH = ROOT / "config" / "p3-governed-labels.research.v1.json"


class GovernedStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_governed_strategy_policy(POLICY_PATH)
        self.features = load_governed_feature_registry(FEATURE_PATH)
        self.labels = load_label_registry(LABEL_PATH)
        self.market_date = date(2020, 1, 2)
        self.cutoff = datetime(2020, 1, 2, 7, 0, tzinfo=timezone.utc)
        self.p0_policy = load_policy()

    def feature_row(
        self,
        *,
        price: str | None = "110",
        sma20: str | None = "105",
        sma60: str | None = "100",
        momentum: str | None = "0.08",
    ) -> BaselineFeatureRow:
        def optional(value: str | None) -> Decimal | None:
            return None if value is None else Decimal(value)

        return BaselineFeatureRow(
            market_date=self.market_date,
            security_id="security-001",
            close_price=Decimal("11"),
            total_return_index=optional(price),
            sma20=optional(sma20),
            sma60=optional(sma60),
            momentum20=optional(momentum),
            prior_high20=Decimal("109"),
            rsi14=Decimal("60"),
            rsi14_previous=Decimal("59"),
            volume_ratio20=Decimal("1.50"),
            realized_volatility20=Decimal("0.25"),
            daily_total_return=Decimal("0.01"),
            average_amount20=Decimal("200000000"),
            listing_sessions=120,
            feature_ready=True,
            entry_eligible=True,
        )

    def replay_frame(self) -> StrategyReplayFrame:
        snapshot = SnapshotRef(
            market_date=self.market_date,
            knowledge_cutoff=self.cutoff,
            source_revision="synthetic-governed-v1",
            snapshot_id="snapshot-2020-01-02",
        )
        return StrategyReplayFrame(
            snapshot=snapshot,
            decision_at=self.cutoff,
            execution_date=date(2020, 1, 3),
            execution_sessions=(),
        )

    def position_review(
        self,
        row: BaselineFeatureRow,
        *,
        holding_sessions: int = 4,
        prior_deterioration_streak: int = 0,
        marked_return: Decimal | None = Decimal("0.02"),
    ):
        return evaluate_governed_position_review(
            frame=self.replay_frame(),
            row=row,
            policy=self.policy,
            p0_policy=self.p0_policy,
            unit_id="unit-001",
            opportunity_id="opportunity-001",
            lifecycle_stage=PositionStage.TRIAL,
            holding_sessions=holding_sessions,
            prior_deterioration_streak=prior_deterioration_streak,
            marked_return=marked_return,
        )

    def facts(self, *, breadth: str = "0.55") -> tuple[FeatureFact, ...]:
        values = {
            "price.total_return_index": Decimal("110"),
            "price.sma20": Decimal("105"),
            "price.sma60": Decimal("100"),
            "price.momentum20": Decimal("0.08"),
            "price.prior_high20": Decimal("109"),
            "price.rsi14": Decimal("60"),
            "liquidity.volume_ratio20": Decimal("1.50"),
            "risk.realized_volatility20": Decimal("0.25"),
            "market.breadth_above_sma60": Decimal(breadth),
        }
        facts: list[FeatureFact] = []
        for definition in self.features.definitions:
            sources = tuple(
                FeatureSourceEvidence(
                    dataset=source.dataset,
                    column=source.column,
                    dataset_version="synthetic-v1",
                    manifest_id=f"manifest-{source.dataset}",
                    knowledge_cutoff=self.cutoff,
                )
                for source in definition.inputs
            )
            lineage = FeatureLineage(
                feature_id=definition.feature_id,
                feature_version=definition.version,
                definition_digest=definition.digest,
                snapshot_id="snapshot-2020-01-02",
                market_date=self.market_date,
                knowledge_cutoff=self.cutoff,
                source_revision="synthetic-governed-v1",
                universe_policy_version="p1-universe-v2",
                sources=sources,
                history_sessions=120,
                cross_section_complete=(
                    True
                    if definition.cutoff_rule is FeatureCutoffRule.COMPLETE_T_CLOSE_CROSS_SECTION
                    else None
                ),
                cutoff_rule=definition.cutoff_rule,
            )
            facts.append(
                FeatureFact(
                    security_code="security-001",
                    feature_id=definition.feature_id,
                    feature_version=definition.version,
                    lineage=lineage,
                    value=values[definition.feature_id],
                )
            )
        return tuple(facts)

    def label_results(self, *, breadth: str = "0.55"):
        return evaluate_governed_labels(
            self.policy,
            self.features,
            self.labels,
            security_id="security-001",
            snapshot_id="snapshot-2020-01-02",
            knowledge_cutoff=self.cutoff,
            facts=self.facts(breadth=breadth),
        )

    def test_digest_bound_feature_extension_and_concrete_labels(self) -> None:
        self.assertEqual(
            self.features.registry_version,
            "p3-governed-features-research-v1",
        )
        values = {item.label_id: item.value for item in self.label_results()}
        self.assertEqual(
            values,
            {
                "market.breadth-regime": "supportive",
                "price-volume.breakout-confirmation": "confirmed",
                "risk.extension-state": "contained",
                "trend.moderate-uptrend": "moderate",
            },
        )

    def test_concrete_experts_map_allowed_candidate_to_trial_entry(self) -> None:
        facts = self.facts()
        labels = self.label_results()
        context = ExpertContext(
            security_code="security-001",
            snapshot_id="snapshot-2020-01-02",
            knowledge_cutoff=self.cutoff,
            position_state="empty",
            feature_facts=facts,
            label_results=labels,
        )
        vetoes = (
            run_veto_expert(EntryEvidenceVeto(True), context),
            run_veto_expert(CandidateStateVeto(), context),
        )
        constraints = (run_constraint_expert(TrialPositionConstraint(self.policy), context),)
        scores = tuple(
            run_scoring_expert(CandidateScoringExpert(kind, self.policy), context)
            for kind in ("trend", "breakout", "risk")
        )
        governance = aggregate_governance(context, vetoes, constraints)
        result = aggregate_score_and_map_action(
            context,
            governance,
            scores,
            ScoreActionPolicy(
                "candidate.market-breadth-trend-breakout@1.0.0",
                self.policy.entry.minimum_aggregate_score,
            ),
        )

        self.assertEqual(result.final_action, GovernedAction.TRIAL_ENTRY)
        self.assertLessEqual(
            governance.merged_constraints.maximum_position,
            self.policy.portfolio.trial_position_fraction,
        )

    def test_weak_market_blocks_the_same_positive_security_score(self) -> None:
        facts = self.facts(breadth="0.44")
        labels = self.label_results(breadth="0.44")
        context = ExpertContext(
            security_code="security-001",
            snapshot_id="snapshot-2020-01-02",
            knowledge_cutoff=self.cutoff,
            position_state="empty",
            feature_facts=facts,
            label_results=labels,
        )
        vetoes = (
            run_veto_expert(EntryEvidenceVeto(True), context),
            run_veto_expert(CandidateStateVeto(), context),
        )
        governance = aggregate_governance(
            context,
            vetoes,
            (run_constraint_expert(TrialPositionConstraint(self.policy), context),),
        )
        scores = tuple(
            run_scoring_expert(CandidateScoringExpert(kind, self.policy), context)
            for kind in ("trend", "breakout", "risk")
        )
        result = aggregate_score_and_map_action(
            context,
            governance,
            scores,
            ScoreActionPolicy("candidate-action-v1", Decimal("0.35")),
        )

        self.assertEqual(result.proposed_action, GovernedAction.TRIAL_ENTRY)
        self.assertEqual(result.final_action, GovernedAction.NO_ACTION)

    def test_four_source_review_holds_when_every_source_is_clear(self) -> None:
        review = self.position_review(self.feature_row())

        self.assertEqual(review.arbitration.status, ExitDecisionStatus.HOLD)
        self.assertEqual(review.deterioration_streak, 0)
        self.assertEqual(
            tuple(item.source for item in review.evaluations),
            tuple(ExitTriggerSource),
        )

    def test_missing_structure_is_hold_with_alert_not_an_exit(self) -> None:
        review = self.position_review(self.feature_row(sma60=None))

        self.assertEqual(
            review.arbitration.status,
            ExitDecisionStatus.HOLD_WITH_UNAVAILABLE_EVIDENCE,
        )
        self.assertFalse(review.arbitration.exit_requested)
        self.assertTrue(review.arbitration.alert_required)
        self.assertEqual(
            review.arbitration.unavailable_sources,
            (ExitTriggerSource.STRUCTURAL_BREAK,),
        )

    def test_each_concrete_exit_rule_can_request_exit(self) -> None:
        cases = (
            (
                self.feature_row(price="90", sma20="99", sma60="100"),
                {},
                ExitTriggerSource.STRUCTURAL_BREAK,
            ),
            (
                self.feature_row(),
                {"marked_return": Decimal("-0.08")},
                ExitTriggerSource.CONSTRAINT_STOP,
            ),
            (
                self.feature_row(price="100", sma20="105", momentum="0"),
                {"prior_deterioration_streak": 1},
                ExitTriggerSource.CONSECUTIVE_DETERIORATION,
            ),
            (
                self.feature_row(),
                {"holding_sessions": 40},
                ExitTriggerSource.MAXIMUM_HOLDING_PERIOD,
            ),
        )
        for row, overrides, expected_source in cases:
            with self.subTest(source=expected_source.value):
                review = self.position_review(row, **overrides)
                self.assertTrue(review.arbitration.exit_requested)
                self.assertIn(expected_source, review.arbitration.triggered_sources)

    def test_position_review_materializes_complete_p2_decision_event(self) -> None:
        frame = self.replay_frame()
        review = self.position_review(self.feature_row(price="90", sma20="99", sma60="100"))
        event = governed_position_decision_event(
            review=review,
            frame=frame,
            policy=self.policy,
            run_id="governed-run-001",
            profile_id="close-to-next-open-v1",
            disposition=DecisionDisposition.ACCEPTED,
            signal_id="signal-exit-001",
            linked_order_ids=("order-exit-001",),
        )

        self.assertEqual(event.final_action, "full_exit")
        self.assertEqual(event.lifecycle_stage, "trial")
        self.assertEqual(len(event.expert_outputs), 4)
        self.assertEqual(event.signal_id, "signal-exit-001")


if __name__ == "__main__":
    unittest.main()
