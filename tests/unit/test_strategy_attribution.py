import json
import unittest
from dataclasses import replace
from datetime import date
from decimal import ROUND_DOWN, ROUND_UP, Decimal, localcontext

from quanthus.policy import find_project_root
from quanthus.strategy_attribution import (
    SYNTHETIC_ARTIFACT_PRODUCER_ID,
    SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
    AnalysisCheckEvidence,
    AnalysisCheckKind,
    AnalysisCheckScope,
    ArtifactKind,
    ArtifactVerificationReceipt,
    AttributionCapability,
    AttributionError,
    ClaimClassification,
    ClaimStatus,
    ConditionComparison,
    ExperimentManifest,
    InMemoryArtifactResolver,
    MetricPreference,
    MetricSpec,
    ValidationFlag,
    analysis_check_result_envelope,
    analysis_check_target_digest,
    canonical_bytes,
    condition_analysis_result_envelope,
    digest,
    evaluate_attribution,
    experiment_evidence_envelope,
    native_trace_envelope,
)
from quanthus.strategy_attribution_demo import (
    SyntheticStrategyAttributionAdapter,
    build_demo_workspace,
)


def _overall_check_binding(question, baseline, intervention):
    metric_ids = tuple(question.specs_by_id)
    return {
        "scope": AnalysisCheckScope.OVERALL_METRIC_VECTOR,
        "metric_ids": metric_ids,
        "target_evidence_digest": analysis_check_target_digest(
            metric_ids=metric_ids,
            baseline_experiment_digest=baseline.digest,
            intervention_experiment_digest=intervention.digest,
        ),
    }


class _ArtifactOverrideResolver:
    def __init__(self, delegate, overrides: dict[str, bytes]) -> None:
        self._delegate = delegate
        self._overrides = dict(overrides)

    def resolve(self, artifact_digest: str) -> bytes:
        if artifact_digest in self._overrides:
            return self._overrides[artifact_digest]
        return self._delegate.resolve(artifact_digest)


def _reissue_synthetic_experiment(
    adapter: SyntheticStrategyAttributionAdapter,
    experiment: ExperimentManifest,
    resolver: InMemoryArtifactResolver | None = None,
) -> tuple[ExperimentManifest, InMemoryArtifactResolver]:
    if resolver is None:
        resolver = InMemoryArtifactResolver(adapter.artifact_resolver().artifacts())
    manifest = adapter.capability_manifest()
    trace = native_trace_envelope(
        trace_id=f"trace.test-reissue.{experiment.experiment_id}",
        question_digest=experiment.question_digest,
        capability_manifest_digest=experiment.capability_manifest_digest,
        native_topology_digest=experiment.native_topology_digest,
        context_digest=experiment.context_digest,
        producer_id=SYNTHETIC_ARTIFACT_PRODUCER_ID,
        producer_version=SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
        source_kind=manifest.evidence_source_kind,
        payload={
            "fixture": "reissued_matching_experiment_evidence",
            "experiment_id": experiment.experiment_id,
            "role": experiment.role.value,
        },
    )
    draft = replace(experiment, native_trace_digest=resolver.register(trace))
    result = experiment_evidence_envelope(
        draft,
        producer_id=SYNTHETIC_ARTIFACT_PRODUCER_ID,
        producer_version=SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
        source_kind=manifest.evidence_source_kind,
    )
    return replace(draft, artifact_digest=resolver.register(result)), resolver


def _reissue_synthetic_analysis_check(
    adapter: SyntheticStrategyAttributionAdapter,
    check: AnalysisCheckEvidence,
    resolver: InMemoryArtifactResolver,
) -> AnalysisCheckEvidence:
    envelope = analysis_check_result_envelope(
        check,
        producer_id=SYNTHETIC_ARTIFACT_PRODUCER_ID,
        producer_version=SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
        source_kind=adapter.capability_manifest().evidence_source_kind,
    )
    return replace(check, result_digest=resolver.register(envelope))


class StrategyAttributionTests(unittest.TestCase):
    def test_demo_workspace_is_deterministic_and_explicitly_synthetic(self) -> None:
        first = build_demo_workspace()
        second = build_demo_workspace()

        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first.source_kind, "synthetic_fixture_not_strategy_evidence")
        self.assertIn("allocator.capital", first.unavailable_component_ids)
        self.assertEqual(len(first.questions), 5)
        self.assertEqual(len(first.experiments), 12)
        self.assertEqual(len(first.condition_comparisons), 2)
        self.assertEqual(len(first.interaction_comparisons), 1)

    def test_workspace_is_independent_of_process_decimal_context(self) -> None:
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            first = build_demo_workspace().canonical_bytes()
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_UP
            second = build_demo_workspace().canonical_bytes()

        self.assertEqual(first, second)

    def test_paired_replay_orients_return_and_drawdown_without_one_weight(self) -> None:
        workspace = build_demo_workspace()
        claim = next(
            item for item in workspace.claims if item.subject_component_id == "expert.trend"
        )
        effects = {item.metric_id: item for item in claim.metric_effects}

        self.assertEqual(claim.status, ClaimStatus.VALID)
        self.assertEqual(claim.classification, ClaimClassification.BENEFICIAL)
        self.assertEqual(effects["total_return"].raw_effect, Decimal("0.27"))
        self.assertEqual(effects["total_return"].oriented_effect, Decimal("0.27"))
        self.assertEqual(effects["max_drawdown"].raw_effect, Decimal("-0.04"))
        self.assertEqual(effects["max_drawdown"].oriented_effect, Decimal("0.04"))
        self.assertIn(ValidationFlag.PAIRED_COMPARABLE, claim.validation_flags)
        self.assertIn(ValidationFlag.INTERACTION_CHECKED, claim.validation_flags)
        self.assertIn(ValidationFlag.CONDITION_CHECKED, claim.validation_flags)

    def test_mixed_primary_metrics_report_tradeoff(self) -> None:
        workspace = build_demo_workspace()
        claims = {item.subject_component_id: item for item in workspace.claims}

        self.assertEqual(
            claims["risk.extension"].classification,
            ClaimClassification.TRADEOFF,
        )
        self.assertEqual(
            claims["router.market_state"].classification,
            ClaimClassification.TRADEOFF,
        )
        self.assertEqual(
            claims["feature.noise_filter"].classification,
            ClaimClassification.HARMFUL,
        )

    def test_risk_claim_keeps_loss_avoidance_and_opportunity_cost_separate(self) -> None:
        workspace = build_demo_workspace()
        claim = next(
            item for item in workspace.claims if item.subject_component_id == "risk.extension"
        )
        effects = {item.metric_id: item for item in claim.metric_effects}

        self.assertEqual(effects["loss_exposure"].oriented_effect, Decimal("6800000"))
        self.assertEqual(effects["opportunity_cost"].oriented_effect, Decimal("-1400000"))
        self.assertNotEqual(
            effects["loss_exposure"].oriented_effect,
            effects["opportunity_cost"].oriented_effect,
        )

    def test_missing_adapter_capability_is_unavailable_not_strategy_invalidity(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[0]
        manifest = adapter.capability_manifest()
        reduced = replace(
            manifest,
            capabilities=tuple(
                item
                for item in manifest.capabilities
                if item is not AttributionCapability.LEGAL_INTERVENTION
            ),
            components=tuple(
                replace(component, legal_interventions=()) for component in manifest.components
            ),
            legal_joint_interventions=(),
        )
        baseline = replace(
            adapter.baseline(question),
            capability_manifest_digest=reduced.digest,
        )
        intervention = replace(
            adapter.intervention(question),
            capability_manifest_digest=reduced.digest,
        )

        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=reduced,
            question=question,
            baseline=baseline,
            intervention=intervention,
        )

        self.assertEqual(claim.status, ClaimStatus.UNAVAILABLE)
        self.assertEqual(claim.classification, ClaimClassification.INCONCLUSIVE)
        self.assertEqual(claim.metric_effects, ())
        self.assertIn("missing_capability:legal_intervention", claim.reason_codes)
        self.assertEqual(claim.validation_flags, ())
        self.assertEqual(claim.verification_receipts, ())

    def test_missing_condition_capability_cannot_publish_condition_claim(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[0]
        manifest = adapter.capability_manifest()
        reduced = replace(
            manifest,
            capabilities=tuple(
                item
                for item in manifest.capabilities
                if item is not AttributionCapability.CONDITION_MAP
            ),
        )
        baseline = replace(
            adapter.baseline(question),
            capability_manifest_digest=reduced.digest,
        )
        intervention = replace(
            adapter.intervention(question),
            capability_manifest_digest=reduced.digest,
        )

        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=reduced,
            question=question,
            baseline=baseline,
            intervention=intervention,
        )

        self.assertEqual(claim.status, ClaimStatus.UNAVAILABLE)
        self.assertEqual(claim.metric_effects, ())
        self.assertIn("missing_capability:condition_map", claim.reason_codes)
        self.assertEqual(claim.validation_flags, ())
        self.assertEqual(claim.verification_receipts, ())

    def test_context_drift_fails_closed_without_directional_metrics(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[1]
        baseline = adapter.baseline(question)
        context_bindings = {
            **dict(adapter.intervention(question).context_bindings),
            "capital": "CNY-CHANGED",
        }
        intervention = replace(
            adapter.intervention(question),
            context_bindings=context_bindings,
            context_digest=digest(context_bindings),
        )
        intervention, resolver = _reissue_synthetic_experiment(adapter, intervention)

        claim = evaluate_attribution(
            artifact_resolver=resolver,
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=baseline,
            intervention=intervention,
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertEqual(claim.metric_effects, ())
        self.assertIn("comparison_context_mismatch", claim.reason_codes)
        self.assertIn(ValidationFlag.BASELINE_REPRODUCED, claim.validation_flags)
        self.assertNotIn(ValidationFlag.PAIRED_COMPARABLE, claim.validation_flags)

    def test_native_trace_may_change_without_changing_exogenous_context(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[1]
        baseline = adapter.baseline(question)
        intervention = adapter.intervention(question)

        self.assertNotEqual(baseline.native_trace_digest, intervention.native_trace_digest)
        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=baseline,
            intervention=intervention,
        )
        self.assertEqual(claim.status, ClaimStatus.VALID)

    def test_target_range_and_unoriented_metrics_do_not_force_binary_direction(self) -> None:
        target = MetricSpec(
            metric_id="exposure",
            label="Exposure",
            unit="ratio",
            definition_version="1.0.0",
            definition_digest=digest("test-metric-definition:exposure:v1"),
            preference=MetricPreference.TARGET_RANGE,
            materiality=Decimal("0.01"),
            target_min=Decimal("0.4"),
            target_max=Decimal("0.6"),
        )
        unoriented = MetricSpec(
            metric_id="turnover",
            label="Turnover",
            unit="ratio",
            definition_version="1.0.0",
            definition_digest=digest("test-metric-definition:turnover:v1"),
            preference=MetricPreference.UNORIENTED,
            materiality=Decimal("0"),
        )

        self.assertEqual(
            target.oriented_effect(Decimal("0.5"), Decimal("0.8")),
            Decimal("0.2"),
        )
        self.assertIsNone(unoriented.oriented_effect(Decimal("0.3"), Decimal("0.2")))

    def test_metric_arithmetic_rejects_unsupported_decimal_exponents(self) -> None:
        metric = MetricSpec(
            metric_id="bounded",
            label="Bounded metric",
            unit="number",
            definition_version="1.0.0",
            definition_digest=digest("test-metric-definition:bounded:v1"),
            preference=MetricPreference.HIGHER,
            materiality=Decimal("0"),
        )

        with self.assertRaisesRegex(AttributionError, "decimal_out_of_range"):
            metric.oriented_effect(Decimal("1e1000000"), Decimal("0"))

    def test_signed_zero_has_one_identity_and_display(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[1]
        baseline = adapter.baseline(question)
        negative_zero = replace(
            baseline,
            metrics={
                **dict(baseline.metrics),
                "capital_utilization": Decimal("-0"),
            },
        )
        positive_zero = replace(
            baseline,
            metrics={
                **dict(baseline.metrics),
                "capital_utilization": Decimal("0"),
            },
        )
        intervention = replace(
            adapter.intervention(question),
            metrics={
                **dict(adapter.intervention(question).metrics),
                "capital_utilization": Decimal("0"),
            },
        )
        negative_zero, resolver = _reissue_synthetic_experiment(adapter, negative_zero)
        positive_zero, resolver = _reissue_synthetic_experiment(adapter, positive_zero, resolver)
        intervention, resolver = _reissue_synthetic_experiment(adapter, intervention, resolver)

        claim = evaluate_attribution(
            artifact_resolver=resolver,
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=negative_zero,
            intervention=intervention,
        )
        effect = next(
            item for item in claim.metric_effects if item.metric_id == "capital_utilization"
        )

        self.assertEqual(negative_zero.digest, positive_zero.digest)
        self.assertEqual(effect.to_dict()["raw_effect"], "0")
        self.assertEqual(effect.baseline_display, "0.0%")
        self.assertEqual(effect.raw_effect_display, "0.0 pp")

    def test_missing_required_condition_and_interaction_fail_closed(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[0]

        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=adapter.baseline(question),
            intervention=adapter.intervention(question),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertEqual(claim.classification, ClaimClassification.INCONCLUSIVE)
        self.assertEqual(claim.metric_effects, ())
        self.assertIn("missing_required_condition:state.trending", claim.reason_codes)
        self.assertIn(
            "missing_required_interaction:interaction.trend-with-market-state-router",
            claim.reason_codes,
        )
        self.assertIn(ValidationFlag.BASELINE_REPRODUCED, claim.validation_flags)
        self.assertIn(ValidationFlag.PAIRED_COMPARABLE, claim.validation_flags)

    def test_posthoc_condition_cannot_gain_condition_checked_flag(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        manifest = adapter.capability_manifest()
        question = adapter.questions()[1]
        baseline = adapter.baseline(question)
        intervention = adapter.intervention(question)
        posthoc = ConditionComparison(
            condition_id="posthoc.best_slice",
            condition_spec_digest=digest("posthoc-condition"),
            label="Best slice after reading outcomes",
            sample_count=1,
            question_digest=question.digest,
            adapter_capability_digest=manifest.digest,
            context_digest=baseline.context_digest,
            period_start=baseline.period_start,
            period_end=baseline.period_end,
            baseline_experiment_digest=baseline.digest,
            intervention_experiment_digest=intervention.digest,
            result_digest=digest("unresolved-posthoc-condition-result"),
            plan_receipt_digest=None,
            execution_work_digest=None,
            raw_execution_digest=None,
            reconciled=True,
            baseline_metrics=baseline.metrics,
            intervention_metrics=intervention.metrics,
        )

        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=manifest,
            question=question,
            baseline=baseline,
            intervention=intervention,
            conditions=(posthoc,),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertEqual(claim.metric_effects, ())
        self.assertIn("undeclared_condition:posthoc.best_slice", claim.reason_codes)
        self.assertNotIn(ValidationFlag.CONDITION_CHECKED, claim.validation_flags)

    def test_tampered_condition_metrics_fail_the_artifact_receipt(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[0]
        baseline = adapter.baseline(question)
        intervention = adapter.intervention(question)
        conditions = adapter.condition_comparisons(question)
        tampered = replace(
            conditions[0],
            baseline_metrics={key: Decimal("999") for key in conditions[0].baseline_metrics},
            intervention_metrics={
                key: Decimal("-999") for key in conditions[0].intervention_metrics
            },
        )

        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=baseline,
            intervention=intervention,
            conditions=(tampered, *conditions[1:]),
            interactions=adapter.interaction_runs(question),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertEqual(claim.condition_effects, ())
        self.assertNotIn(ValidationFlag.CONDITION_CHECKED, claim.validation_flags)
        self.assertTrue(
            any(
                "condition_evidence:artifact_payload_mismatch" in item
                for item in claim.reason_codes
            )
        )
        condition_receipts = tuple(
            receipt
            for receipt in claim.verification_receipts
            if receipt.artifact_kind is ArtifactKind.CONDITION_ANALYSIS_RESULT
        )
        self.assertEqual(len(condition_receipts), 2)
        self.assertTrue(any(not receipt.passed for receipt in condition_receipts))

    def test_interaction_with_foreign_experiment_metadata_fails_closed(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[0]
        comparison = adapter.interaction_runs(question)[0]
        bad_comparison = replace(
            comparison,
            partner_intervention=replace(
                comparison.partner_intervention,
                strategy_version="foreign-strategy-version",
                capital_constrained=False,
                reproduced=False,
                reconciled=False,
            ),
        )

        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=adapter.baseline(question),
            intervention=adapter.intervention(question),
            conditions=adapter.condition_comparisons(question),
            interactions=(bad_comparison,),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertEqual(claim.metric_effects, ())
        self.assertIn(ValidationFlag.CONDITION_CHECKED, claim.validation_flags)
        self.assertNotIn(ValidationFlag.INTERACTION_CHECKED, claim.validation_flags)
        self.assertTrue(
            any("partner_strategy_version_mismatch" in item for item in claim.reason_codes)
        )

    def test_interaction_composition_order_is_part_of_the_estimand(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[0]
        comparison = adapter.interaction_runs(question)[0]
        bad_comparison = replace(
            comparison,
            joint_intervention=replace(
                comparison.joint_intervention,
                intervention_ids=tuple(reversed(comparison.joint_intervention.intervention_ids)),
                changed_component_ids=tuple(
                    reversed(comparison.joint_intervention.changed_component_ids)
                ),
            ),
        )

        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=adapter.baseline(question),
            intervention=adapter.intervention(question),
            conditions=adapter.condition_comparisons(question),
            interactions=(bad_comparison,),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertIn(
            "interaction_composition_order_mismatch:interaction.trend-with-market-state-router",
            claim.reason_codes,
        )
        self.assertNotIn(ValidationFlag.INTERACTION_CHECKED, claim.validation_flags)

    def test_interaction_evidence_cannot_be_reused_for_another_subject_run(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[0]
        comparison = adapter.interaction_runs(question)[0]
        subject = replace(
            adapter.intervention(question),
            metrics={
                **dict(adapter.intervention(question).metrics),
                "total_return": Decimal("0.60"),
            },
        )

        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=adapter.baseline(question),
            intervention=subject,
            conditions=adapter.condition_comparisons(question),
            interactions=(comparison,),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertIn(
            "interaction_subject_mismatch:interaction.trend-with-market-state-router",
            claim.reason_codes,
        )
        self.assertNotIn(ValidationFlag.INTERACTION_CHECKED, claim.validation_flags)

    def test_interaction_missing_baseline_or_subject_metric_fails_closed(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[0]
        original_baseline = adapter.baseline(question)
        original_subject = adapter.intervention(question)
        comparison = adapter.interaction_runs(question)[0]

        for missing_role in ("baseline", "subject"):
            baseline = original_baseline
            subject = original_subject
            if missing_role == "baseline":
                baseline = replace(
                    baseline,
                    metrics={
                        key: value
                        for key, value in baseline.metrics.items()
                        if key != "total_return"
                    },
                    metric_spec_digests={
                        key: value
                        for key, value in baseline.metric_spec_digests.items()
                        if key != "total_return"
                    },
                )
            else:
                subject = replace(
                    subject,
                    metrics={
                        key: value
                        for key, value in subject.metrics.items()
                        if key != "total_return"
                    },
                    metric_spec_digests={
                        key: value
                        for key, value in subject.metric_spec_digests.items()
                        if key != "total_return"
                    },
                )

            with self.subTest(missing_role=missing_role):
                claim = evaluate_attribution(
                    artifact_resolver=adapter.artifact_resolver(),
                    adapter=adapter.capability_manifest(),
                    question=question,
                    baseline=baseline,
                    intervention=subject,
                    interactions=(comparison,),
                )
                self.assertEqual(claim.status, ClaimStatus.INVALID)
                self.assertIn(
                    "interaction_metric_mismatch:interaction.trend-with-market-state-router",
                    claim.reason_codes,
                )
                self.assertNotIn(
                    ValidationFlag.INTERACTION_CHECKED,
                    claim.validation_flags,
                )

    def test_question_cannot_self_declare_an_adapter_undeclared_joint_run(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        manifest = adapter.capability_manifest()
        question = adapter.questions()[0]
        reduced = replace(manifest, legal_joint_interventions=())
        baseline = replace(adapter.baseline(question), capability_manifest_digest=reduced.digest)
        subject = replace(adapter.intervention(question), capability_manifest_digest=reduced.digest)
        conditions = tuple(
            replace(
                item,
                adapter_capability_digest=reduced.digest,
                baseline_experiment_digest=baseline.digest,
                intervention_experiment_digest=subject.digest,
            )
            for item in adapter.condition_comparisons(question)
        )
        comparison = adapter.interaction_runs(question)[0]
        comparison = replace(
            comparison,
            baseline_experiment_digest=baseline.digest,
            subject_experiment_digest=subject.digest,
            partner_intervention=replace(
                comparison.partner_intervention,
                capability_manifest_digest=reduced.digest,
            ),
            joint_intervention=replace(
                comparison.joint_intervention,
                capability_manifest_digest=reduced.digest,
            ),
        )

        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=reduced,
            question=question,
            baseline=baseline,
            intervention=subject,
            conditions=conditions,
            interactions=(comparison,),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertIn(
            "undeclared_joint_intervention:interaction.trend-with-market-state-router",
            claim.reason_codes,
        )
        self.assertNotIn(ValidationFlag.INTERACTION_CHECKED, claim.validation_flags)

    def test_partner_intervention_semantic_drift_invalidates_bound_question(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        manifest = adapter.capability_manifest()
        question = adapter.questions()[0]
        partner_id = "neutralize_market_state_route"
        original_partner = manifest.intervention("router.market_state", partner_id)
        self.assertIsNotNone(original_partner)
        assert original_partner is not None
        changed_partner = replace(
            original_partner,
            operation="changed_native_route_semantics",
        )
        components = tuple(
            replace(component, legal_interventions=(changed_partner,))
            if component.component_id == "router.market_state"
            else component
            for component in manifest.components
        )
        joint = manifest.legal_joint_interventions[0]
        member_digests = list(joint.intervention_spec_digests)
        member_digests[joint.intervention_ids.index(partner_id)] = changed_partner.digest
        changed_joint = replace(
            joint,
            intervention_spec_digests=tuple(member_digests),
        )
        changed_manifest = replace(
            manifest,
            components=components,
            legal_joint_interventions=(changed_joint,),
        )

        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=changed_manifest,
            question=question,
            baseline=adapter.baseline(question),
            intervention=adapter.intervention(question),
            conditions=adapter.condition_comparisons(question),
            interactions=adapter.interaction_runs(question),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertIn(
            "partner_intervention_spec_digest_mismatch:interaction.trend-with-market-state-router",
            claim.reason_codes,
        )
        self.assertIn(
            "undeclared_joint_intervention:interaction.trend-with-market-state-router",
            claim.reason_codes,
        )

    def test_malformed_self_interaction_is_rejected_at_question_boundary(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[0]
        interaction = question.interaction_specs[0]
        malformed = replace(
            interaction,
            partner_component_id=question.subject_component_id,
            partner_intervention_id=question.intervention_id,
            composition_order=(question.intervention_id, "bogus-member"),
        )

        with self.assertRaisesRegex(AttributionError, "invalid_interaction_partner"):
            replace(question, interaction_specs=(malformed,))

    def test_capability_manifest_or_topology_drift_invalidates_bound_runs(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[1]
        manifest = adapter.capability_manifest()
        drifted = replace(
            manifest,
            capability_manifest_version="2.0.0",
            native_topology_digest=digest("changed-native-topology"),
        )

        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=drifted,
            question=question,
            baseline=adapter.baseline(question),
            intervention=adapter.intervention(question),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertEqual(claim.metric_effects, ())
        self.assertIn("capability_manifest_version_mismatch", claim.reason_codes)
        self.assertIn("native_topology_digest_mismatch", claim.reason_codes)

    def test_adapter_metadata_change_does_not_rewrite_native_baseline_evidence(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[1]
        manifest = adapter.capability_manifest()
        baseline = adapter.baseline(question)
        changed_manifest = replace(
            manifest,
            adapter_version="1.1.0",
            capability_manifest_version="1.1.0",
        )
        rebound = replace(
            baseline,
            adapter_version=changed_manifest.adapter_version,
            capability_manifest_version=changed_manifest.capability_manifest_version,
            capability_manifest_digest=changed_manifest.digest,
        )

        self.assertEqual(baseline.artifact_digest, rebound.artifact_digest)
        self.assertEqual(baseline.native_trace_digest, rebound.native_trace_digest)
        self.assertNotEqual(baseline.digest, rebound.digest)

    def test_adapter_serialized_digest_matches_digest_property(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        manifest = adapter.capability_manifest()
        self.assertEqual(manifest.digest, manifest.to_dict()["digest"])
        for question in adapter.questions():
            self.assertEqual(question.digest, question.to_dict()["digest"])
            for experiment in (
                adapter.baseline(question),
                adapter.intervention(question),
            ):
                self.assertEqual(experiment.digest, experiment.to_dict()["digest"])
        for comparison in adapter.condition_comparisons(adapter.questions()[0]):
            self.assertEqual(comparison.digest, comparison.to_dict()["digest"])
        for comparison in adapter.interaction_runs(adapter.questions()[0]):
            self.assertEqual(comparison.digest, comparison.to_dict()["digest"])

    def test_decision_summary_contains_only_predeclared_primary_metrics(self) -> None:
        workspace = build_demo_workspace()
        claim = next(
            item for item in workspace.claims if item.subject_component_id == "expert.trend"
        )
        artifact = claim.to_dict()
        summary_metric_ids = {
            item["metric_id"] for item in artifact["decision_summary"]["primary_effects"]
        }
        analysis_metric_ids = {
            item["metric_id"] for item in artifact["reason_analysis"]["metric_effects"]
        }

        self.assertEqual(summary_metric_ids, set(claim.primary_metric_ids))
        self.assertEqual(
            analysis_metric_ids,
            {item.metric_id for item in claim.metric_effects},
        )
        self.assertNotIn("win_rate", summary_metric_ids)
        self.assertEqual(
            artifact["decision_summary"]["next_action_code"],
            "no_strategy_action_synthetic_fixture",
        )

    def test_question_predeclares_every_deterministic_decision_action(self) -> None:
        question = SyntheticStrategyAttributionAdapter().questions()[0]

        self.assertEqual(len(question.decision_actions), 7)
        self.assertEqual(
            question.decision_action(ClaimStatus.VALID, ClaimClassification.BENEFICIAL),
            "no_strategy_action_synthetic_fixture",
        )
        with self.assertRaisesRegex(AttributionError, "invalid_decision_actions"):
            replace(question, decision_actions={"valid:beneficial": "retain"})

    def test_workspace_rejects_duplicate_claim_identity(self) -> None:
        workspace = build_demo_workspace()
        duplicated = (workspace.claims[0], workspace.claims[0])

        with self.assertRaisesRegex(AttributionError, "duplicate_claim_id"):
            replace(workspace, claims=duplicated)

    def test_workspace_rejects_duplicate_question_and_experiment_ids(self) -> None:
        workspace = build_demo_workspace()
        duplicate_question = replace(workspace.questions[0], question_version="different-version")
        duplicate_experiment = replace(
            workspace.experiments[0], artifact_digest=digest("different-artifact")
        )

        with self.assertRaisesRegex(AttributionError, "duplicate_question_id"):
            replace(
                workspace,
                questions=(*workspace.questions, duplicate_question),
            )
        with self.assertRaisesRegex(AttributionError, "duplicate_experiment_id"):
            replace(
                workspace,
                experiments=(*workspace.experiments, duplicate_experiment),
            )

    def test_workspace_recomputes_and_rejects_a_tampered_claim(self) -> None:
        workspace = build_demo_workspace()
        original = workspace.claims[0]
        tampered = replace(original, classification=ClaimClassification.HARMFUL)

        with self.assertRaisesRegex(AttributionError, "claim_derivation_mismatch"):
            replace(
                workspace,
                claims=(tampered, *workspace.claims[1:]),
            )

    def test_workspace_binds_source_baseline_and_evidence_closure(self) -> None:
        workspace = build_demo_workspace()
        foreign_draft = replace(
            workspace.experiments[0],
            experiment_id="experiment.unreferenced.foreign",
            artifact_digest=digest("unreferenced-foreign-artifact"),
        )
        foreign_artifact = experiment_evidence_envelope(
            foreign_draft,
            producer_id=SYNTHETIC_ARTIFACT_PRODUCER_ID,
            producer_version=SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
            source_kind=workspace.source_kind,
        )
        foreign_experiment = replace(
            foreign_draft,
            artifact_digest=foreign_artifact.digest,
        )
        joint = next(
            item for item in workspace.experiments if item.role.value == "joint_intervention"
        )

        with self.assertRaisesRegex(AttributionError, "workspace_source_mismatch"):
            replace(workspace, source_kind="formal_strategy_evidence")
        with self.assertRaisesRegex(AttributionError, "invalid_workspace_baseline"):
            replace(workspace, baseline=joint)
        with self.assertRaisesRegex(AttributionError, "unreferenced_workspace_experiment"):
            replace(
                workspace,
                experiments=(*workspace.experiments, foreign_experiment),
                artifacts=(*workspace.artifacts, foreign_artifact),
            )

    def test_workspace_copies_mutable_sequences_before_validation(self) -> None:
        workspace = build_demo_workspace()
        mutable_claims = list(workspace.claims)
        copied = replace(workspace, claims=mutable_claims)
        original_classification = copied.claims[0].classification

        mutable_claims[0] = replace(mutable_claims[0], classification=ClaimClassification.HARMFUL)

        self.assertIsInstance(copied.claims, tuple)
        self.assertEqual(copied.claims[0].classification, original_classification)

    def test_adapter_and_claim_copy_mutable_sequences(self) -> None:
        workspace = build_demo_workspace()
        mutable_capabilities = list(workspace.adapter.capabilities)
        mutable_components = list(workspace.adapter.components)
        adapter = replace(
            workspace.adapter,
            capabilities=mutable_capabilities,
            components=mutable_components,
        )
        original = workspace.claims[0]
        mutable_flags = list(original.validation_flags)
        mutable_effects = list(original.metric_effects)
        claim = replace(
            original,
            validation_flags=mutable_flags,
            metric_effects=mutable_effects,
        )

        mutable_capabilities.clear()
        mutable_components.clear()
        mutable_flags.clear()
        mutable_effects.clear()

        self.assertIsInstance(adapter.capabilities, tuple)
        self.assertTrue(adapter.capabilities)
        self.assertTrue(adapter.components)
        self.assertIsInstance(claim.validation_flags, tuple)
        self.assertTrue(claim.validation_flags)
        self.assertTrue(claim.metric_effects)

    def test_questions_bind_per_component_versioned_intervention_specs(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        manifest = adapter.capability_manifest()
        for question in adapter.questions():
            intervention = manifest.intervention(
                question.subject_component_id, question.intervention_id
            )
            self.assertIsNotNone(intervention)
            assert intervention is not None
            self.assertEqual(question.intervention_spec_digest, intervention.digest)
        allocator = manifest.component("allocator.capital")
        self.assertIsNotNone(allocator)
        assert allocator is not None
        self.assertEqual(allocator.legal_interventions, ())

    def test_metric_spec_input_order_is_canonical(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        manifest = adapter.capability_manifest()
        first_question = adapter.questions()[1]
        reordered_question = replace(
            first_question,
            metric_specs=tuple(reversed(first_question.metric_specs)),
        )

        first = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=manifest,
            question=first_question,
            baseline=adapter.baseline(first_question),
            intervention=adapter.intervention(first_question),
        )
        reordered = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=manifest,
            question=reordered_question,
            baseline=adapter.baseline(reordered_question),
            intervention=adapter.intervention(reordered_question),
        )

        self.assertEqual(first_question.digest, reordered_question.digest)
        self.assertEqual(first.claim_id, reordered.claim_id)
        self.assertEqual(first.digest, reordered.digest)
        self.assertEqual(first.to_dict(), reordered.to_dict())

    def test_claim_identity_binds_period_and_visible_context(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        manifest = adapter.capability_manifest()
        question = adapter.questions()[1]
        baseline = adapter.baseline(question)
        intervention = adapter.intervention(question)
        first = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=manifest,
            question=question,
            baseline=baseline,
            intervention=intervention,
        )
        changed_bindings = {
            **dict(baseline.context_bindings),
            "capital": "CNY-200000000",
        }
        changed_context_digest = digest(changed_bindings)
        changed_baseline = replace(
            baseline,
            context_bindings=changed_bindings,
            context_digest=changed_context_digest,
            period_start=date(2024, 1, 1),
            period_end=date(2024, 6, 30),
        )
        changed_intervention = replace(
            intervention,
            context_bindings=changed_bindings,
            context_digest=changed_context_digest,
            period_start=date(2024, 1, 1),
            period_end=date(2024, 6, 30),
        )
        changed_baseline, resolver = _reissue_synthetic_experiment(adapter, changed_baseline)
        changed_intervention, resolver = _reissue_synthetic_experiment(
            adapter, changed_intervention, resolver
        )
        changed = evaluate_attribution(
            artifact_resolver=resolver,
            adapter=manifest,
            question=question,
            baseline=changed_baseline,
            intervention=changed_intervention,
        )

        self.assertEqual(first.status, ClaimStatus.VALID)
        self.assertEqual(changed.status, ClaimStatus.VALID)
        self.assertNotEqual(first.claim_id, changed.claim_id)
        self.assertNotEqual(first.digest, changed.digest)
        self.assertNotEqual(first.context_digest, changed.context_digest)

    def test_required_uncertainty_and_stability_results_fail_closed_when_missing(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        manifest = adapter.capability_manifest()
        question = replace(
            adapter.questions()[1],
            uncertainty_required=True,
            uncertainty_method="block_bootstrap_v1",
            stability_required=True,
            stability_method="repeat_period_v1",
        )
        baseline = replace(
            adapter.baseline(adapter.questions()[1]),
            question_digest=question.digest,
        )
        intervention = replace(
            adapter.intervention(adapter.questions()[1]),
            question_digest=question.digest,
        )
        baseline, resolver = _reissue_synthetic_experiment(adapter, baseline)
        intervention, resolver = _reissue_synthetic_experiment(adapter, intervention, resolver)

        claim = evaluate_attribution(
            artifact_resolver=resolver,
            adapter=manifest,
            question=question,
            baseline=baseline,
            intervention=intervention,
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertEqual(claim.metric_effects, ())
        self.assertIn("missing_required_uncertainty_result", claim.reason_codes)
        self.assertIn("missing_required_stability_result", claim.reason_codes)
        self.assertIn(
            "unregistered_uncertainty_method:block_bootstrap_v1",
            claim.reason_codes,
        )
        self.assertIn(
            "unregistered_stability_method:repeat_period_v1",
            claim.reason_codes,
        )
        self.assertIn(ValidationFlag.BASELINE_REPRODUCED, claim.validation_flags)
        self.assertIn(ValidationFlag.PAIRED_COMPARABLE, claim.validation_flags)

    def test_evidence_receipts_require_content_digest_syntax(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        manifest = adapter.capability_manifest()
        question = adapter.questions()[1]
        baseline = adapter.baseline(question)
        intervention = adapter.intervention(question)

        with self.assertRaisesRegex(AttributionError, "invalid_digest"):
            replace(baseline, artifact_digest="not-a-content-digest")
        with self.assertRaisesRegex(AttributionError, "invalid_digest"):
            AnalysisCheckEvidence(
                check_id="check.invalid-digest.v1",
                kind=AnalysisCheckKind.UNCERTAINTY,
                method=question.uncertainty_method,
                **_overall_check_binding(question, baseline, intervention),
                question_digest=question.digest,
                adapter_capability_digest=manifest.digest,
                baseline_experiment_digest=baseline.digest,
                intervention_experiment_digest=intervention.digest,
                result_digest="passed-because-I-said-so",
                completed=True,
                passed=True,
            )

    def test_missing_artifact_resolver_cannot_validate_experiment_evidence(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[1]

        claim = evaluate_attribution(
            artifact_resolver=None,
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=adapter.baseline(question),
            intervention=adapter.intervention(question),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertEqual(claim.metric_effects, ())
        self.assertIn(
            "experiment_evidence:artifact_resolver_missing",
            claim.reason_codes,
        )
        self.assertTrue(claim.verification_receipts)
        self.assertTrue(all(not receipt.passed for receipt in claim.verification_receipts))

    def test_sha_shaped_nonexistent_artifact_cannot_validate_evidence(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[1]
        nonexistent_digest = f"sha256:{'a' * 64}"
        baseline = replace(
            adapter.baseline(question),
            artifact_digest=nonexistent_digest,
        )

        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=baseline,
            intervention=adapter.intervention(question),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertIn("experiment_evidence:artifact_unresolved", claim.reason_codes)
        receipt = next(
            item
            for item in claim.verification_receipts
            if item.artifact_digest == nonexistent_digest
        )
        self.assertFalse(receipt.passed)
        self.assertIn("artifact_unresolved", receipt.reason_codes)

    def test_forged_sha_alias_cannot_substitute_another_artifact(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[1]
        baseline = adapter.baseline(question)
        forged_digest = f"sha256:{'b' * 64}"
        forged_baseline = replace(baseline, artifact_digest=forged_digest)
        resolver = _ArtifactOverrideResolver(
            adapter.artifact_resolver(),
            {forged_digest: adapter.artifact_resolver().resolve(baseline.artifact_digest)},
        )

        claim = evaluate_attribution(
            artifact_resolver=resolver,
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=forged_baseline,
            intervention=adapter.intervention(question),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertIn(
            "experiment_evidence:artifact_lookup_digest_mismatch",
            claim.reason_codes,
        )
        receipt = next(
            item for item in claim.verification_receipts if item.artifact_digest == forged_digest
        )
        self.assertIn("artifact_lookup_digest_mismatch", receipt.reason_codes)

    def test_tampered_envelope_cannot_validate_against_its_original_digest(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[1]
        baseline = adapter.baseline(question)
        raw = json.loads(adapter.artifact_resolver().resolve(baseline.artifact_digest))
        raw["payload"]["metrics"]["total_return"] = "999"
        resolver = _ArtifactOverrideResolver(
            adapter.artifact_resolver(),
            {baseline.artifact_digest: canonical_bytes(raw)},
        )

        claim = evaluate_attribution(
            artifact_resolver=resolver,
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=baseline,
            intervention=adapter.intervention(question),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertIn(
            "experiment_evidence:artifact_digest_mismatch",
            claim.reason_codes,
        )
        receipt = next(
            item
            for item in claim.verification_receipts
            if item.artifact_digest == baseline.artifact_digest
            and item.artifact_kind is ArtifactKind.EXPERIMENT_RESULT
        )
        self.assertIn("artifact_digest_mismatch", receipt.reason_codes)

    def test_cross_question_artifact_cannot_authorize_a_receipt(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[1]
        baseline = adapter.baseline(question)
        intervention = adapter.intervention(question)
        foreign_baseline = adapter.baseline(adapter.questions()[2])
        rebound_baseline = replace(
            baseline,
            artifact_digest=foreign_baseline.artifact_digest,
        )

        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=rebound_baseline,
            intervention=intervention,
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertIn(
            "experiment_evidence:artifact_parent_mismatch",
            claim.reason_codes,
        )
        self.assertIn(
            "experiment_evidence:artifact_payload_mismatch",
            claim.reason_codes,
        )
        self.assertNotIn(ValidationFlag.BASELINE_REPRODUCED, claim.validation_flags)

    def test_unregistered_producer_cannot_issue_strategy_evidence(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[1]
        baseline = adapter.baseline(question)
        intervention = adapter.intervention(question)
        resolver = InMemoryArtifactResolver(adapter.artifact_resolver().artifacts())
        foreign_artifact = experiment_evidence_envelope(
            baseline,
            producer_id="foreign.strategy-adapter",
            producer_version="1.0.0",
            source_kind=adapter.capability_manifest().evidence_source_kind,
        )
        foreign_baseline = replace(
            baseline,
            artifact_digest=resolver.register(foreign_artifact),
        )

        claim = evaluate_attribution(
            artifact_resolver=resolver,
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=foreign_baseline,
            intervention=intervention,
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertIn(
            "experiment_evidence:artifact_producer_unregistered",
            claim.reason_codes,
        )
        self.assertNotIn(ValidationFlag.BASELINE_REPRODUCED, claim.validation_flags)

    def test_metric_values_bind_definition_and_calculator_versions(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        question = adapter.questions()[1]
        baseline = adapter.baseline(question)
        intervention = adapter.intervention(question)

        stale_calculator_artifact = replace(
            baseline,
            metric_calculator_version="2.0.0",
        )
        stale_claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=stale_calculator_artifact,
            intervention=intervention,
        )
        self.assertEqual(stale_claim.status, ClaimStatus.INVALID)
        self.assertIn(
            "experiment_evidence:artifact_payload_mismatch",
            stale_claim.reason_codes,
        )

        foreign_metric_definition = replace(
            baseline,
            metric_spec_digests={
                **dict(baseline.metric_spec_digests),
                "total_return": digest("foreign-total-return-definition"),
            },
        )
        foreign_metric_definition, resolver = _reissue_synthetic_experiment(
            adapter,
            foreign_metric_definition,
        )
        foreign_claim = evaluate_attribution(
            artifact_resolver=resolver,
            adapter=adapter.capability_manifest(),
            question=question,
            baseline=foreign_metric_definition,
            intervention=intervention,
        )
        self.assertEqual(foreign_claim.status, ClaimStatus.INVALID)
        self.assertIn("metric_definition_digest_mismatch", foreign_claim.reason_codes)

    def test_verification_receipts_are_stable_and_json_round_trippable(self) -> None:
        first = build_demo_workspace().claims[0].verification_receipts
        second = build_demo_workspace().claims[0].verification_receipts

        self.assertEqual(
            tuple(item.digest for item in first),
            tuple(item.digest for item in second),
        )
        for receipt in first:
            restored = ArtifactVerificationReceipt.from_bytes(receipt.canonical_bytes())

            self.assertEqual(restored.canonical_bytes(), receipt.canonical_bytes())
            self.assertEqual(restored.to_dict(), receipt.to_dict())

        tampered = json.loads(first[0].canonical_bytes())
        tampered["verified_facts"] = {"reproduced": False}
        with self.assertRaisesRegex(AttributionError, "receipt_digest_mismatch"):
            ArtifactVerificationReceipt.from_bytes(canonical_bytes(tampered))

    def test_unregistered_analysis_methods_cannot_publish_with_resolved_results(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        manifest = adapter.capability_manifest()
        original = adapter.questions()[1]
        question = replace(
            original,
            uncertainty_required=True,
            uncertainty_method="block_bootstrap_v1",
            stability_required=True,
            stability_method="repeat_period_v1",
        )
        baseline = replace(adapter.baseline(original), question_digest=question.digest)
        intervention = replace(adapter.intervention(original), question_digest=question.digest)
        baseline, resolver = _reissue_synthetic_experiment(adapter, baseline)
        intervention, resolver = _reissue_synthetic_experiment(adapter, intervention, resolver)
        uncertainty = _reissue_synthetic_analysis_check(
            adapter,
            AnalysisCheckEvidence(
                check_id="check.uncertainty.v1",
                kind=AnalysisCheckKind.UNCERTAINTY,
                method=question.uncertainty_method,
                **_overall_check_binding(question, baseline, intervention),
                question_digest=question.digest,
                adapter_capability_digest=manifest.digest,
                baseline_experiment_digest=baseline.digest,
                intervention_experiment_digest=intervention.digest,
                result_digest=digest("synthetic-uncertainty-result"),
                completed=True,
                passed=True,
            ),
            resolver,
        )
        stability = _reissue_synthetic_analysis_check(
            adapter,
            AnalysisCheckEvidence(
                check_id="check.stability.v1",
                kind=AnalysisCheckKind.STABILITY,
                method=question.stability_method,
                **_overall_check_binding(question, baseline, intervention),
                question_digest=question.digest,
                adapter_capability_digest=manifest.digest,
                baseline_experiment_digest=baseline.digest,
                intervention_experiment_digest=intervention.digest,
                result_digest=digest("synthetic-stability-result"),
                completed=True,
                passed=True,
            ),
            resolver,
        )

        claim = evaluate_attribution(
            artifact_resolver=resolver,
            adapter=manifest,
            question=question,
            baseline=baseline,
            intervention=intervention,
            analysis_checks=(uncertainty, stability),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertEqual(claim.metric_effects, ())
        self.assertIn(
            "unregistered_uncertainty_method:block_bootstrap_v1",
            claim.reason_codes,
        )
        self.assertIn(
            "unregistered_stability_method:repeat_period_v1",
            claim.reason_codes,
        )
        check_receipts = tuple(
            receipt
            for receipt in claim.verification_receipts
            if receipt.artifact_kind is ArtifactKind.ANALYSIS_CHECK_RESULT
        )
        self.assertEqual(len(check_receipts), 2)
        self.assertTrue(all(receipt.passed for receipt in check_receipts))

    def test_overall_uncertainty_cannot_satisfy_condition_scope(self) -> None:
        adapter = SyntheticStrategyAttributionAdapter()
        manifest = adapter.capability_manifest()
        original = adapter.questions()[1]
        condition_spec = adapter.questions()[0].condition_specs[0]
        question = replace(
            original,
            uncertainty_required=True,
            uncertainty_method="block_bootstrap_v1",
            condition_specs=(condition_spec,),
        )
        baseline = replace(adapter.baseline(original), question_digest=question.digest)
        intervention = replace(adapter.intervention(original), question_digest=question.digest)
        condition = ConditionComparison(
            condition_id=condition_spec.condition_id,
            condition_spec_digest=condition_spec.digest,
            label=condition_spec.label,
            sample_count=10,
            question_digest=question.digest,
            adapter_capability_digest=manifest.digest,
            context_digest=baseline.context_digest,
            period_start=baseline.period_start,
            period_end=baseline.period_end,
            baseline_experiment_digest=baseline.digest,
            intervention_experiment_digest=intervention.digest,
            result_digest=digest("unbound-condition-result"),
            plan_receipt_digest=None,
            execution_work_digest=None,
            raw_execution_digest=None,
            reconciled=True,
            baseline_metrics=baseline.metrics,
            intervention_metrics=intervention.metrics,
        )
        condition = replace(
            condition,
            result_digest=adapter.artifact_resolver().register(
                condition_analysis_result_envelope(
                    condition,
                    producer_id=SYNTHETIC_ARTIFACT_PRODUCER_ID,
                    producer_version=SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
                    source_kind=manifest.evidence_source_kind,
                )
            ),
        )
        uncertainty = AnalysisCheckEvidence(
            check_id="check.overall-uncertainty.v1",
            kind=AnalysisCheckKind.UNCERTAINTY,
            method=question.uncertainty_method,
            **_overall_check_binding(question, baseline, intervention),
            question_digest=question.digest,
            adapter_capability_digest=manifest.digest,
            baseline_experiment_digest=baseline.digest,
            intervention_experiment_digest=intervention.digest,
            result_digest=digest("synthetic-overall-uncertainty-result"),
            completed=True,
            passed=True,
        )

        claim = evaluate_attribution(
            artifact_resolver=adapter.artifact_resolver(),
            adapter=manifest,
            question=question,
            baseline=baseline,
            intervention=intervention,
            conditions=(condition,),
            analysis_checks=(uncertainty,),
        )

        self.assertEqual(claim.status, ClaimStatus.INVALID)
        self.assertEqual(claim.metric_effects, ())
        self.assertEqual(claim.condition_effects, ())
        self.assertIn(
            "missing_required_condition_uncertainty_scope",
            claim.reason_codes,
        )

    def test_committed_web_fixture_matches_kernel_artifact(self) -> None:
        path = find_project_root() / "web" / "public" / "data" / "attribution-demo.v1.json"
        expected = json.loads(build_demo_workspace().canonical_bytes())
        actual = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
