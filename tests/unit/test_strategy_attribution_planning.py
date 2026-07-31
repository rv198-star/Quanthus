import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from quanthus.strategy_attribution import (
    AdapterCapabilityManifest,
    AttributionCapability,
    AttributionError,
    ComponentDescriptor,
    InterventionSpec,
    digest,
)
from quanthus.strategy_attribution_demo import SyntheticStrategyAttributionAdapter
from quanthus.strategy_attribution_planning import (
    AttributionRunRequest,
    FrozenAdapterBinding,
    PlanNodeKind,
    PlanReasonCode,
    QuestionBindingKind,
    QuestionPlanStatus,
    compile_attribution_plan,
)

_CONTEXT = {
    "data_snapshot": "snapshot.synthetic.2025h1.v1",
    "eligible_universe": "universe.synthetic.v1",
    "capital": "capital.synthetic.v1",
    "matcher": "matcher.synthetic.v1",
    "costs": "costs.synthetic.v1",
    "settlement": "settlement.synthetic.v1",
    "policy": "policy.synthetic.v1",
    "execution_assumptions": "execution.synthetic.v1",
    "seeds": "seed-set.synthetic.v1",
}


def _request(*, manifest=None, questions=None, context=None):
    adapter = SyntheticStrategyAttributionAdapter()
    return AttributionRunRequest.create(
        adapter_manifest=manifest or adapter.capability_manifest(),
        questions=questions or adapter.questions(),
        context_bindings=context or _CONTEXT,
        period_start=date(2025, 1, 1),
        period_end=date(2025, 6, 30),
        metric_calculator_id="quanthus.synthetic-metric-calculator",
        metric_calculator_version="1.0.0",
    )


def _planned_question(plan, question_id):
    return next(item for item in plan.planned_questions if item.question_id == question_id)


def _reason_codes(planned_question):
    return {item.code for item in planned_question.reasons}


def _retarget_binding(planned_question, old_experiment_id, new_experiment_id):
    return replace(
        planned_question,
        experiment_bindings=tuple(
            replace(item, experiment_id=new_experiment_id)
            if item.experiment_id == old_experiment_id
            else item
            for item in planned_question.experiment_bindings
        ),
    )


def _replace_experiment(plan, old_experiment_id, replacement):
    return tuple(
        replacement if item.experiment_id == old_experiment_id else item
        for item in plan.experiments
    )


def _all_mapping_keys(value):
    if isinstance(value, dict):
        yield from value
        for item in value.values():
            yield from _all_mapping_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_mapping_keys(item)


def test_plan_is_canonical_and_independent_of_question_or_mapping_order() -> None:
    adapter = SyntheticStrategyAttributionAdapter()
    forward = _request(manifest=adapter.capability_manifest(), questions=adapter.questions())
    reverse = _request(
        manifest=adapter.capability_manifest(),
        questions=tuple(reversed(adapter.questions())),
        context=dict(reversed(tuple(_CONTEXT.items()))),
    )

    first = compile_attribution_plan(forward)
    second = compile_attribution_plan(reverse)

    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.digest == second.digest
    assert first.plan_id == second.plan_id
    assert forward.canonical_bytes() == reverse.canonical_bytes()


def test_request_and_plan_contain_definitions_but_no_result_facts() -> None:
    plan = compile_attribution_plan(_request())
    keys = set(_all_mapping_keys(plan.to_dict()))

    assert {
        "metrics",
        "artifact_digest",
        "native_trace_digest",
        "result_digest",
        "reproduced",
        "reconciled",
        "pit_validated",
        "partition_authorized",
        "passed",
        "receipts",
    }.isdisjoint(keys)
    assert plan.run_request.outcome_access_policy.value == (
        "forbid_target_outcomes_until_plan_persisted"
    )
    assert plan.run_request.metric_calculator_id == "quanthus.synthetic-metric-calculator"
    assert plan.run_request.context_bindings["seeds"] == "seed-set.synthetic.v1"
    assert plan.run_request.capital_constrained is True
    assert plan.run_request.point_in_time_required is True


def test_shared_baseline_and_intervention_are_planned_once_with_full_question_edges() -> None:
    plan = compile_attribution_plan(_request())
    nodes_by_kind = {
        kind: tuple(item for item in plan.experiments if item.kind is kind)
        for kind in PlanNodeKind
    }

    assert len(nodes_by_kind[PlanNodeKind.BASELINE_REPLAY]) == 1
    assert len(nodes_by_kind[PlanNodeKind.SINGLE_INTERVENTION]) == 5
    assert len(nodes_by_kind[PlanNodeKind.JOINT_INTERVENTION]) == 1
    assert len(nodes_by_kind[PlanNodeKind.CONDITION_ANALYSIS]) == 2
    assert len(plan.experiments) == 9

    baseline_id = nodes_by_kind[PlanNodeKind.BASELINE_REPLAY][0].experiment_id
    for planned in plan.planned_questions:
        assert planned.status is QuestionPlanStatus.READY
        assert baseline_id in planned.experiment_ids

    expert = _planned_question(plan, "question.expert-trend")
    router = _planned_question(plan, "question.market-state-router")
    expert_partner_id = next(
        item.experiment_id
        for item in expert.experiment_bindings
        if item.kind is QuestionBindingKind.INTERACTION_PARTNER
    )
    router_subject_id = next(
        item.experiment_id
        for item in router.experiment_bindings
        if item.kind is QuestionBindingKind.SUBJECT_INTERVENTION
    )
    assert expert_partner_id == router_subject_id
    risk = _planned_question(plan, "question.extension-risk")
    risk_subject_id = next(
        item.experiment_id
        for item in risk.experiment_bindings
        if item.kind is QuestionBindingKind.SUBJECT_INTERVENTION
    )
    risk_subject = next(item for item in plan.experiments if item.experiment_id == risk_subject_id)
    risk_source = next(
        item for item in plan.run_request.questions if item.question_id == "question.extension-risk"
    )
    assert AttributionCapability.OPPORTUNITY_TRACE in risk_source.required_capabilities
    assert risk_subject.required_capabilities == (
        AttributionCapability.BASELINE_REPLAY,
        AttributionCapability.LEGAL_INTERVENTION,
    )
    assert {
        item.kind for item in expert.experiment_bindings
    } == {
        QuestionBindingKind.BASELINE,
        QuestionBindingKind.SUBJECT_INTERVENTION,
        QuestionBindingKind.INTERACTION_PARTNER,
        QuestionBindingKind.JOINT_INTERVENTION,
        QuestionBindingKind.CONDITION_ANALYSIS,
    }
    positions = {item.experiment_id: index for index, item in enumerate(plan.experiments)}
    assert all(
        positions[parent] < positions[item.experiment_id]
        for item in plan.experiments
        for parent in item.dependency_experiment_ids
    )

    router_only = compile_attribution_plan(
        _request(questions=(next(
            item
            for item in plan.run_request.questions
            if item.question_id == "question.market-state-router"
        ),))
    )
    router_only_subject_id = next(
        item.experiment_id
        for item in router_only.planned_questions[0].experiment_bindings
        if item.kind is QuestionBindingKind.SUBJECT_INTERVENTION
    )
    assert router_only_subject_id == router_subject_id


def test_missing_capability_is_unavailable_and_has_no_execution_nodes() -> None:
    adapter = SyntheticStrategyAttributionAdapter()
    question = adapter.questions()[1]
    manifest = adapter.capability_manifest()
    manifest = replace(
        manifest,
        capabilities=tuple(
            item
            for item in manifest.capabilities
            if item is not AttributionCapability.DECISION_TRACE
        ),
    )

    plan = compile_attribution_plan(_request(manifest=manifest, questions=(question,)))
    planned = plan.planned_questions[0]

    assert planned.status is QuestionPlanStatus.UNAVAILABLE
    assert _reason_codes(planned) == {PlanReasonCode.MISSING_REQUIRED_CAPABILITY}
    assert planned.experiment_bindings == ()
    assert plan.experiments == ()


def test_required_but_unimplemented_check_is_unavailable() -> None:
    adapter = SyntheticStrategyAttributionAdapter()
    question = replace(adapter.questions()[1], uncertainty_required=True)

    plan = compile_attribution_plan(_request(questions=(question,)))
    planned = plan.planned_questions[0]

    assert planned.status is QuestionPlanStatus.UNAVAILABLE
    assert _reason_codes(planned) == {PlanReasonCode.REQUIRED_METHOD_UNAVAILABLE}
    assert plan.experiments == ()


def test_frozen_adapter_version_drift_is_rejected_before_execution() -> None:
    request = _request()
    drifted_binding = replace(request.adapter_binding, adapter_version="9.9.9")
    request = replace(request, adapter_binding=drifted_binding)

    plan = compile_attribution_plan(request)

    assert plan.experiments == ()
    assert {item.status for item in plan.planned_questions} == {QuestionPlanStatus.REJECTED}
    assert all(
        PlanReasonCode.ADAPTER_BINDING_DRIFT in _reason_codes(item)
        for item in plan.planned_questions
    )


def test_method_registry_version_drift_is_rejected_before_execution() -> None:
    request = replace(_request(), method_registry_version="future-registry.v2")

    plan = compile_attribution_plan(request)

    assert plan.experiments == ()
    assert {item.status for item in plan.planned_questions} == {QuestionPlanStatus.REJECTED}
    assert all(
        PlanReasonCode.METHOD_REGISTRY_DRIFT in _reason_codes(item)
        for item in plan.planned_questions
    )


@pytest.mark.parametrize("field_name", ("capital_constrained", "point_in_time_required"))
def test_required_execution_constraint_cannot_be_disabled(field_name) -> None:
    request = _request()

    with pytest.raises(ValueError):
        replace(request, **{field_name: False})


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    (
        (
            lambda question: replace(
                question,
                intervention_spec_digest=digest("drifted-intervention-spec"),
            ),
            PlanReasonCode.INTERVENTION_SPEC_DRIFT,
        ),
        (
            lambda question: replace(
                question,
                intervention_id="undeclared-intervention",
                intervention_spec_digest=digest("undeclared-intervention-spec"),
            ),
            PlanReasonCode.UNDECLARED_INTERVENTION,
        ),
    ),
)
def test_illegal_or_drifted_subject_intervention_is_rejected(mutation, expected_reason) -> None:
    adapter = SyntheticStrategyAttributionAdapter()
    question = mutation(adapter.questions()[1])

    plan = compile_attribution_plan(_request(questions=(question,)))
    planned = plan.planned_questions[0]

    assert planned.status is QuestionPlanStatus.REJECTED
    assert expected_reason in _reason_codes(planned)
    assert plan.experiments == ()


def test_joint_composition_order_conflict_is_rejected_stably() -> None:
    adapter = SyntheticStrategyAttributionAdapter()
    question = adapter.questions()[0]
    interaction = question.interaction_specs[0]
    question = replace(
        question,
        interaction_specs=(
            replace(interaction, composition_order=tuple(reversed(interaction.composition_order))),
        ),
    )

    first = compile_attribution_plan(_request(questions=(question,)))
    second = compile_attribution_plan(_request(questions=(question,)))
    planned = first.planned_questions[0]

    assert planned.status is QuestionPlanStatus.REJECTED
    assert PlanReasonCode.COMPOSITION_ORDER_CONFLICT in _reason_codes(planned)
    assert first.experiments == ()
    assert first.canonical_bytes() == second.canonical_bytes()


def test_undeclared_joint_intervention_is_rejected() -> None:
    adapter = SyntheticStrategyAttributionAdapter()
    question = adapter.questions()[0]
    interaction = question.interaction_specs[0]
    question = replace(
        question,
        interaction_specs=(
            replace(
                interaction,
                joint_intervention_spec_digest=digest("undeclared-joint-intervention"),
            ),
        ),
    )

    plan = compile_attribution_plan(_request(questions=(question,)))
    planned = plan.planned_questions[0]

    assert planned.status is QuestionPlanStatus.REJECTED
    assert PlanReasonCode.UNDECLARED_JOINT_INTERVENTION in _reason_codes(planned)
    assert plan.experiments == ()


def test_open_component_kind_and_single_strategy_root_do_not_change_planner_logic() -> None:
    source = SyntheticStrategyAttributionAdapter()
    original_question = source.questions()[1]
    intervention = InterventionSpec(
        intervention_id="neutralize-future-root",
        intervention_version="2040.1",
        operation="adapter-native-neutralization",
        fallback_semantics="Defined only by the future strategy adapter.",
    )
    root = ComponentDescriptor(
        component_id="future.root",
        component_version="2040.1",
        label="Future root",
        kind="unknown_future_topology_kind",
        role="opaque_strategy_root",
        description="A topology intentionally unknown to the attribution kernel.",
        legal_interventions=(intervention,),
    )
    manifest = AdapterCapabilityManifest(
        adapter_id="adapter.future-root",
        adapter_version="2040.1",
        capability_manifest_version="2040.1",
        strategy_id="strategy.future-root",
        strategy_version="2040.1",
        evidence_source_kind="synthetic_fixture_not_strategy_evidence",
        native_topology_digest=digest("future-root-topology"),
        capabilities=(
            AttributionCapability.BASELINE_REPLAY,
            AttributionCapability.LEGAL_INTERVENTION,
            AttributionCapability.DECISION_TRACE,
        ),
        components=(root,),
    )
    question = replace(
        original_question,
        question_id="question.future-root",
        subject_component_id=root.component_id,
        intervention_id=intervention.intervention_id,
        intervention_spec_digest=intervention.digest,
    )

    plan = compile_attribution_plan(_request(manifest=manifest, questions=(question,)))

    assert plan.planned_questions[0].status is QuestionPlanStatus.READY
    assert [item.kind for item in plan.experiments] == [
        PlanNodeKind.BASELINE_REPLAY,
        PlanNodeKind.SINGLE_INTERVENTION,
    ]
    assert all(root.kind not in item.to_dict() for item in plan.experiments)


def test_conflicting_metric_definitions_reject_every_affected_question() -> None:
    adapter = SyntheticStrategyAttributionAdapter()
    first = adapter.questions()[1]
    changed_metric = replace(first.metric_specs[0], definition_version="2.0.0")
    second = replace(
        first,
        question_id="question.feature-breakout-alternate-metric",
        metric_specs=(changed_metric, *first.metric_specs[1:]),
    )

    plan = compile_attribution_plan(_request(questions=(first, second)))

    assert plan.experiments == ()
    assert all(item.status is QuestionPlanStatus.REJECTED for item in plan.planned_questions)
    assert all(
        PlanReasonCode.METRIC_DEFINITION_CONFLICT in _reason_codes(item)
        for item in plan.planned_questions
    )


def test_plan_cannot_recompose_an_unavailable_request_as_ready() -> None:
    adapter = SyntheticStrategyAttributionAdapter()
    question = adapter.questions()[1]
    manifest = adapter.capability_manifest()
    missing_capability_manifest = replace(
        manifest,
        capabilities=tuple(
            item
            for item in manifest.capabilities
            if item is not AttributionCapability.DECISION_TRACE
        ),
    )
    unavailable = compile_attribution_plan(
        _request(manifest=missing_capability_manifest, questions=(question,))
    )
    ready = compile_attribution_plan(_request(manifest=manifest, questions=(question,)))

    with pytest.raises(AttributionError) as captured:
        replace(ready, run_request=unavailable.run_request)

    assert captured.value.reason_code == "plan_compilation_mismatch"


def test_plan_cannot_replace_a_legal_intervention_with_foreign_semantics() -> None:
    adapter = SyntheticStrategyAttributionAdapter()
    plan = compile_attribution_plan(_request(questions=(adapter.questions()[1],)))
    subject = next(
        item for item in plan.experiments if item.kind is PlanNodeKind.SINGLE_INTERVENTION
    )
    forged = replace(
        subject,
        component_ids=("foreign.component",),
        intervention_ids=("foreign.intervention",),
        intervention_spec_digests=(digest("foreign-intervention-spec"),),
    )
    planned_question = _retarget_binding(
        plan.planned_questions[0], subject.experiment_id, forged.experiment_id
    )

    with pytest.raises(AttributionError) as captured:
        replace(
            plan,
            experiments=_replace_experiment(plan, subject.experiment_id, forged),
            planned_questions=(planned_question,),
        )

    assert captured.value.reason_code == "plan_compilation_mismatch"


def test_plan_cannot_reverse_adapter_owned_joint_members() -> None:
    adapter = SyntheticStrategyAttributionAdapter()
    plan = compile_attribution_plan(_request(questions=(adapter.questions()[0],)))
    joint = next(
        item for item in plan.experiments if item.kind is PlanNodeKind.JOINT_INTERVENTION
    )
    reversed_joint = replace(
        joint,
        component_ids=tuple(reversed(joint.component_ids)),
        intervention_ids=tuple(reversed(joint.intervention_ids)),
        intervention_spec_digests=tuple(reversed(joint.intervention_spec_digests)),
    )
    planned_question = _retarget_binding(
        plan.planned_questions[0], joint.experiment_id, reversed_joint.experiment_id
    )

    with pytest.raises(AttributionError) as captured:
        replace(
            plan,
            experiments=_replace_experiment(plan, joint.experiment_id, reversed_joint),
            planned_questions=(planned_question,),
        )

    assert captured.value.reason_code == "plan_compilation_mismatch"


def test_analysis_parent_identity_changes_node_identity_and_fails_recomposition() -> None:
    plan = compile_attribution_plan(_request())
    expert = _planned_question(plan, "question.expert-trend")
    condition_binding = next(
        item
        for item in expert.experiment_bindings
        if item.kind is QuestionBindingKind.CONDITION_ANALYSIS
    )
    condition = next(
        item for item in plan.experiments if item.experiment_id == condition_binding.experiment_id
    )
    baseline_id = next(
        item
        for item in condition.dependency_experiment_ids
        if next(node for node in plan.experiments if node.experiment_id == item).kind
        is PlanNodeKind.BASELINE_REPLAY
    )
    unrelated_single_id = next(
        item.experiment_id
        for item in plan.experiments
        if item.kind is PlanNodeKind.SINGLE_INTERVENTION
        and item.experiment_id not in condition.dependency_experiment_ids
    )
    wrong_parent = replace(
        condition,
        dependency_experiment_ids=(baseline_id, unrelated_single_id),
    )
    assert wrong_parent.experiment_id != condition.experiment_id
    retargeted_expert = _retarget_binding(
        expert, condition.experiment_id, wrong_parent.experiment_id
    )
    planned_questions = tuple(
        retargeted_expert if item.question_digest == expert.question_digest else item
        for item in plan.planned_questions
    )

    with pytest.raises(AttributionError) as captured:
        replace(
            plan,
            experiments=_replace_experiment(plan, condition.experiment_id, wrong_parent),
            planned_questions=planned_questions,
        )

    assert captured.value.reason_code == "plan_compilation_mismatch"


def test_run_request_is_shallowly_immutable() -> None:
    request = _request()

    with pytest.raises(TypeError):
        request.context_bindings["seeds"] = "changed"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        request.questions = ()  # type: ignore[misc]
    assert request.adapter_binding == FrozenAdapterBinding.from_manifest(request.adapter_manifest)


def test_caller_owned_containers_cannot_mutate_request_or_plan() -> None:
    adapter = SyntheticStrategyAttributionAdapter()
    context = dict(_CONTEXT)
    questions = list(adapter.questions())
    request = AttributionRunRequest.create(
        adapter_manifest=adapter.capability_manifest(),
        questions=questions,
        context_bindings=context,
        period_start=date(2025, 1, 1),
        period_end=date(2025, 6, 30),
        metric_calculator_id="quanthus.synthetic-metric-calculator",
        metric_calculator_version="1.0.0",
    )
    original_bytes = request.canonical_bytes()
    context["seeds"] = "mutated"
    questions.clear()

    assert request.canonical_bytes() == original_bytes
    plan = compile_attribution_plan(request)
    experiment = plan.experiments[0]
    binding = plan.planned_questions[0].experiment_bindings[0]
    with pytest.raises(TypeError):
        experiment.metric_spec_digests["new"] = digest("new")  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        binding.experiment_id = "mutated"  # type: ignore[misc]


class StrategyAttributionPlanningDiscoveryTests(unittest.TestCase):
    """Keep the pytest-style contract cases inside the repository unittest gate."""

    def test_planning_contract_suite(self) -> None:
        test_plan_is_canonical_and_independent_of_question_or_mapping_order()
        test_request_and_plan_contain_definitions_but_no_result_facts()
        test_shared_baseline_and_intervention_are_planned_once_with_full_question_edges()
        test_missing_capability_is_unavailable_and_has_no_execution_nodes()
        test_required_but_unimplemented_check_is_unavailable()
        test_frozen_adapter_version_drift_is_rejected_before_execution()
        test_method_registry_version_drift_is_rejected_before_execution()
        for field_name in ("capital_constrained", "point_in_time_required"):
            test_required_execution_constraint_cannot_be_disabled(field_name)
        for mutation, reason in (
            (
                lambda question: replace(
                    question,
                    intervention_spec_digest=digest("drifted-intervention-spec"),
                ),
                PlanReasonCode.INTERVENTION_SPEC_DRIFT,
            ),
            (
                lambda question: replace(
                    question,
                    intervention_id="undeclared-intervention",
                    intervention_spec_digest=digest("undeclared-intervention-spec"),
                ),
                PlanReasonCode.UNDECLARED_INTERVENTION,
            ),
        ):
            test_illegal_or_drifted_subject_intervention_is_rejected(
                mutation, reason
            )
        test_joint_composition_order_conflict_is_rejected_stably()
        test_undeclared_joint_intervention_is_rejected()
        test_open_component_kind_and_single_strategy_root_do_not_change_planner_logic()
        test_conflicting_metric_definitions_reject_every_affected_question()
        test_plan_cannot_recompose_an_unavailable_request_as_ready()
        test_plan_cannot_replace_a_legal_intervention_with_foreign_semantics()
        test_plan_cannot_reverse_adapter_owned_joint_members()
        test_analysis_parent_identity_changes_node_identity_and_fails_recomposition()
        test_run_request_is_shallowly_immutable()
        test_caller_owned_containers_cannot_mutate_request_or_plan()
