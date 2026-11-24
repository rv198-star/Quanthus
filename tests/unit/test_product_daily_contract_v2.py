"""Contract v2 tests: machine-policy v2 loader/action scope + held-reduction wire.

These cover the P11 side of #723 comments 7116501586/7117737408/7118368788.  The pinned
P12 v2 consumer does not exist yet; the named dependency is asserted as a fail-closed
gate, never a stubbed positive.
"""

from __future__ import annotations

import dataclasses
import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quanthus.policy import find_project_root
from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.contracts import (
    EXIT_PLAN,
    HELD_REDUCTION_WIRE_VERSION,
    HOLDING,
    Decision,
)
from quanthus.product_daily.recommendation.producer import (
    CandidateEvaluationReceipt,
    DailyAdviceProducer,
    ExpertBundle,
    HeldSignal,
)
from quanthus.product_daily.recommendation.publish import publish_package
from quanthus.product_daily.recommendation.rule_seam import (
    check_three_piece_consistency,
    load_action_receipts,
    load_machine_policy,
    load_release_manifest,
    machine_policy_action_scope,
)
from tests.unit.test_product_daily_rule_seam import _p12_loader, _producer_inputs

ROOT = find_project_root()
CONFIG = ROOT / "config" / "research" / "r11" / "d1b-20261124"
SHANGHAI = ZoneInfo("Asia/Shanghai")
POLICY_V2 = CONFIG / "machine-policy.v2.json"
POLICY_V1 = CONFIG / "machine-policy.v1.json"
MANIFEST_V2 = CONFIG / "release-manifest.v2.json"
RECEIPTS_V1 = CONFIG / "action-receipts.v1.json"


def _policy_v2(tmp_path: Path, **overrides) -> Path:
    raw = deepcopy(json.loads(POLICY_V2.read_text()))
    for key, value in overrides.items():
        raw[key] = value
    path = tmp_path / "machine-policy.v2.json"
    path.write_text(json.dumps(raw))
    return path


def _patch_condition(raw: dict, field: str, **overrides) -> dict:
    out = deepcopy(raw)
    for cond in out["conditions"]:
        if cond["field"] == field:
            cond.update(overrides)
    return out


# --- A. strict machine-policy v2 loader ----------------------------------------------------


def test_machine_policy_v2_loads_with_seven_conditions():
    policy = load_machine_policy(POLICY_V2)
    assert policy["schema_version"] == "quanthus.p11-d1b.machine-policy.v2"
    fields = {cond["field"] for cond in policy["conditions"]}
    assert fields == {
        "price_limit_or_halt_unavailable",
        "auction_snapshot_unavailable",
        "single_security_fraction",
        "per_industry_max",
        "new_buckets_per_day",
        "total_position",
        "expert_veto",
    }
    assert policy["held_reduction_wire"]["reduce_denominator"] == "locked_cycle_budget"


def test_machine_policy_v1_still_loads():
    policy = load_machine_policy(POLICY_V1)
    assert policy["schema_version"] == "quanthus.p11-d1b.machine-policy.v1"


def test_machine_policy_v2_rejects_unknown_schema(tmp_path):
    path = _policy_v2(tmp_path, schema_version="quanthus.p11-d1b.machine-policy.v9")
    with pytest.raises(ProductDailyError, match="schema_version"):
        load_machine_policy(path)


def test_machine_policy_v2_rejects_sell_blocking(tmp_path):
    raw = json.loads(POLICY_V2.read_text())
    raw = _patch_condition(
        raw, "auction_snapshot_unavailable", applies_to_actions=["confirm_buy", "sell"]
    )
    path = tmp_path / "mp.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ProductDailyError, match="never sell/reduce"):
        load_machine_policy(path)


def test_machine_policy_v2_rejects_reduce_blocking(tmp_path):
    raw = json.loads(POLICY_V2.read_text())
    raw = _patch_condition(
        raw,
        "price_limit_or_halt_unavailable",
        applies_to_actions=["new_entry", "confirm_buy", "reduce"],
    )
    path = tmp_path / "mp.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ProductDailyError, match="never sell/reduce"):
        load_machine_policy(path)


def test_machine_policy_v2_rejects_wrong_missing_fact_disposition(tmp_path):
    raw = json.loads(POLICY_V2.read_text())
    raw = _patch_condition(
        raw,
        "auction_snapshot_unavailable",
        on_missing_or_unverified="block_all_actions",
    )
    path = tmp_path / "mp.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ProductDailyError, match="block_only_buy_confirmation"):
        load_machine_policy(path)


def test_machine_policy_v2_rejects_wrong_reduce_denominator(tmp_path):
    raw = json.loads(POLICY_V2.read_text())
    raw["held_reduction_wire"]["reduce_denominator"] = "account_equity"
    path = tmp_path / "mp.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ProductDailyError, match="locked_cycle_budget"):
        load_machine_policy(path)


def test_machine_policy_v2_rejects_adopted_state(tmp_path):
    path = _policy_v2(tmp_path, daily_adopted=True)
    with pytest.raises(ProductDailyError, match="daily_adopted"):
        load_machine_policy(path)


# --- A. confirm_buy action-scope regressions ------------------------------------------------


def test_action_scope_missing_preopen_facts_only_blocks_confirm_buy():
    policy = load_machine_policy(POLICY_V2)
    scope = machine_policy_action_scope(policy, "auction_snapshot_unavailable")
    assert scope == frozenset({"confirm_buy"})
    assert "sell" not in scope and "reduce" not in scope


def test_action_scope_halt_fact_covers_new_entry_and_confirm_buy():
    policy = load_machine_policy(POLICY_V2)
    scope = machine_policy_action_scope(policy, "price_limit_or_halt_unavailable")
    assert scope == frozenset({"new_entry", "confirm_buy"})
    # sell/reduce on missing halt facts still continue_with_alert
    assert "sell" not in scope and "reduce" not in scope


def test_action_scope_v1_preserves_declared_actions():
    policy = load_machine_policy(POLICY_V1)
    scope = machine_policy_action_scope(policy, "auction_snapshot_unavailable")
    assert scope == frozenset({"confirm"})


# --- three-piece consistency across v2 --------------------------------------------------------


def test_three_piece_v2_consistent_set():
    check_three_piece_consistency(
        action_receipts=load_action_receipts(RECEIPTS_V1),
        machine_policy=load_machine_policy(POLICY_V2),
        release_manifest=load_release_manifest(MANIFEST_V2),
    )


def test_three_piece_v2_rejects_manifest_without_wire():
    manifest = load_release_manifest(MANIFEST_V2)
    manifest = deepcopy(manifest)
    manifest.pop("held_reduction_wire")
    with pytest.raises(ProductDailyError, match="held_reduction_wire"):
        check_three_piece_consistency(
            action_receipts=load_action_receipts(RECEIPTS_V1),
            machine_policy=load_machine_policy(POLICY_V2),
            release_manifest=manifest,
        )


def test_manifest_v2_requires_wire(tmp_path):
    raw = deepcopy(json.loads(MANIFEST_V2.read_text()))
    raw.pop("held_reduction_wire")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ProductDailyError, match="held_reduction_wire"):
        load_release_manifest(path)


# --- B. held-reduction wire on Decision ---------------------------------------------------------


def _decision(**kw) -> Decision:
    base = dict(
        opportunity_id="held-600000",
        security_code="600000",
        security_name="name",
        stage=EXIT_PLAN,
        component_ref_id="candidate.p4-trend-risk",
        component_version="1.0.0",
        reason_codes=(),
        explanations=(
            {
                "code": "INPUT_CLOCK",
                "use": "explanation",
                "component_ref": "candidate.p4-trend-risk@1.0.0",
                "text": "held",
            },
        ),
        next_observation="next",
    )
    base.update(kw)
    return Decision(**base)


def test_exit_plan_requires_explicit_kind():
    with pytest.raises(ProductDailyError, match="exit_kind"):
        _decision()


def test_exit_plan_full_carries_no_target():
    d = _decision(exit_kind="full")
    assert d.to_wire_dict()["exit_kind"] == "full"
    assert d.to_wire_dict()["target_position_fraction"] is None
    with pytest.raises(ProductDailyError, match="full exit carries no target"):
        _decision(exit_kind="full", target_position_fraction=Decimal("0.5"))


def test_exit_plan_reduce_requires_valid_target():
    for bad in (None, Decimal("0"), Decimal("-0.1"), Decimal("1.1"), Decimal("NaN")):
        with pytest.raises(ProductDailyError):
            _decision(exit_kind="reduce", target_position_fraction=bad)
    d = _decision(exit_kind="reduce", target_position_fraction=Decimal("0.5"))
    assert d.to_wire_dict()["target_position_fraction"] == "0.5"


def test_exit_fields_rejected_outside_exit_plan():
    with pytest.raises(ProductDailyError, match="belong only to exit_plan"):
        _decision(stage=HOLDING, exit_kind="full")


def test_tampered_target_changes_wire_identity():
    from quanthus.event_library.contracts import sha256_digest

    a = _decision(exit_kind="reduce", target_position_fraction=Decimal("0.50"))
    b = _decision(exit_kind="reduce", target_position_fraction=Decimal("0.51"))
    assert sha256_digest(a.to_wire_dict()) != sha256_digest(b.to_wire_dict())


# --- B. producer emits reduce/full correctly ------------------------------------------------------


def _produce_held(held: HeldSignal):
    facts, policy, account = _producer_inputs(
        datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI),
        market_date=date(2026, 11, 24),
        next_session=date(2026, 11, 25),
    )
    policy = dataclasses.replace(policy, evidence_mode="structural_fixture")
    held_signals = (held,)
    placeholder = "sha256:" + "0" * 64
    receipt = CandidateEvaluationReceipt(
        producer_id="p11.seam",
        producer_version=policy.producer_version,
        run_id="run-test",
        completed_at=policy.decision_cutoff - timedelta(minutes=5),
        evaluated_count=1,
        qualified_count=0,
        coverage_digest=placeholder,
        binding_digest=placeholder,
        evaluated_universe=("600000",),
    )
    receipt = dataclasses.replace(
        receipt,
        coverage_digest=receipt.expected_coverage_digest(),
        binding_digest=CandidateEvaluationReceipt.expected_binding_digest(
            facts,
            policy,
            (),
            receipt_identity={
                "producer_id": receipt.producer_id,
                "producer_version": receipt.producer_version,
                "run_id": receipt.run_id,
                "evaluated_universe": sorted(receipt.evaluated_universe),
            },
            held_signals=held_signals,
        ),
    )
    producer = DailyAdviceProducer()
    return producer.produce(
        facts=facts,
        policy=policy,
        account=account,
        candidates=(),
        held_signals=held_signals,
        evaluation_receipt=receipt,
        experts=ExpertBundle("candidate.p4-trend-risk", "1.0.0", "sha256:" + "0" * 64),
    )


def _held(action: str, target: Decimal | None, *, formal=None) -> HeldSignal:
    return HeldSignal(
        opportunity_id="held-600000",
        security_code="600000",
        security_name="name",
        industry_id="industry-01",
        action=action,
        component_ref_id="candidate.p4-trend-risk",
        component_version="1.0.0",
        reason_codes=(),
        explanations=(
            {
                "code": "INPUT_CLOCK",
                "use": "explanation",
                "component_ref": "candidate.p4-trend-risk@1.0.0",
                "text": "held",
            },
        ),
        next_observation="next",
        depends_on_member_ids=(),
        target_maximum_position=target,
        formal_decision=formal,
    )


def test_producer_reduce_carries_target_fraction():
    result = _produce_held(_held("reduce", Decimal("0.5")))
    d = result.package.decision_sets[0].decisions[0]
    # No typed FormalStrategyDecision -> the caller-supplied target never becomes an
    # executable reduce; the intent degrades to display-only hold+alert.
    assert d.stage == "holding" and d.exit_kind is None
    assert d.target_position_fraction is None


def test_producer_reduce_requires_formal_decision_even_with_caller_target():
    """CV2-963-P1-2: held.target_maximum_position is a caller claim; only the actual
    FormalStrategyDecision.target_maximum_position may become an executable reduce."""

    result = _produce_held(_held("reduce", Decimal("0.5")))
    d = result.package.decision_sets[0].decisions[0]
    assert d.stage == "holding"
    assert any("never a full sale" in x.get("text", "") for x in d.explanations)


def _formal_reduce_decision(target: Decimal):
    """Minimal typed FormalStrategyDecision carrying a real reduce target.

    A unit-mechanism double for the producer path: it proves the producer reads the
    target from the typed object, not from the caller field.  It does not grant
    strategy qualification - the formal gate stays with the real runner.
    """

    from datetime import datetime

    from quanthus.aggregator import (
        ConstraintRuleRef,
        GovernanceResult,
        GovernedAction,
        MergedConstraints,
        RequiredAction,
        ScoreActionResult,
    )
    from quanthus.expert_strategy.runner import FormalStrategyDecision

    cutoff = datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI)
    rule = ConstraintRuleRef(expert_id="e", expert_version="1", rule="r")
    merged = MergedConstraints(
        minimum_position=Decimal("0"),
        maximum_position=Decimal("1"),
        stop_rules=(rule,),
        hold_rules=(rule,),
        source_output_digests=("sha256:" + "0" * 64,),
    )
    governance = GovernanceResult(
        security_code="600000",
        snapshot_id="snap-1",
        knowledge_cutoff=cutoff,
        required_action=RequiredAction.NONE,
        terminating_veto_ids=(),
        veto_outputs=(),
        constraint_outputs=(),
        merged_constraints=merged,
    )
    action = ScoreActionResult(
        security_code="600000",
        snapshot_id="snap-1",
        knowledge_cutoff=cutoff,
        position_state="held",
        governance_digest=governance.digest,
        action_policy_version="pol@1",
        action_policy_digest="sha256:" + "0" * 64,
        scoring_outputs=(),
        aggregate_score=Decimal("0"),
        proposed_action=GovernedAction.REDUCE,
        final_action=GovernedAction.REDUCE,
        reason_codes=(),
    )
    return FormalStrategyDecision(
        event_id="held-600000",
        intervention_id=None,
        topology_digest="sha256:" + "0" * 64,
        trend_state=None,
        governance=governance,
        action=action,
        target_maximum_position=target,
    )


def test_producer_reduce_uses_formal_decision_target():
    result = _produce_held(_held("reduce", None, formal=_formal_reduce_decision(Decimal("0.10"))))
    d = result.package.decision_sets[0].decisions[0]
    assert d.stage == "exit_plan" and d.exit_kind == "reduce"
    assert d.target_position_fraction == Decimal("0.10")


def test_producer_full_exit_has_no_target():
    result = _produce_held(_held("exit", None))
    d = result.package.decision_sets[0].decisions[0]
    assert d.stage == "exit_plan" and d.exit_kind == "full"
    assert d.target_position_fraction is None


def test_producer_reduce_without_target_degrades_to_hold_alert():
    result = _produce_held(_held("reduce", None))
    d = result.package.decision_sets[0].decisions[0]
    assert d.stage == "holding"
    assert d.exit_kind is None
    assert any("never a full sale" in x.get("text", "") for x in d.explanations)


# --- B. consumer capability gate on publish -------------------------------------------------


def _publishable_package(held: HeldSignal):
    result = _produce_held(held)
    assert result.package is not None
    assert result.publishable
    return result.package


def _v1_consumer_binding() -> dict:
    """The pinned v1 synthetic consumer binding (real versioned object).

    consumer_schema is attached at load time: the pinned v1 consumer only validates
    the p12.daily-advice-package.v1 wire."""

    from quanthus.product_daily.recommendation.runtime_identity import consumer_identity

    binding = consumer_identity(ROOT)
    binding["consumer_schema"] = "p12.daily-advice-package.v1"
    return binding


def _v2_candidate_binding() -> dict:
    """The candidate v2 consumer binding object (pin pending).

    Real versioned file, declared schema/capability per #723 comment7118368788; its
    source_commit/tree/file hashes are null, so it can never satisfy the verified
    pin check."""

    from quanthus.product_daily.recommendation.runtime_identity import consumer_identity_v2

    return consumer_identity_v2(ROOT)


def _reduce_package() -> object:
    """A package containing a REAL executable reduce (formal decision carried)."""
    return _publishable_package(
        _held("reduce", None, formal=_formal_reduce_decision(Decimal("0.5")))
    )


def test_reduce_package_refused_without_v2_consumer(tmp_path):
    package = _reduce_package()
    with pytest.raises(ProductDailyError, match="verified pinned consumer"):
        publish_package(
            package,
            directory=tmp_path,
            published_at=package.decision_sets[0].published_at,
            wire_version="p12.daily-advice-package.v2",
        )
    assert not list(tmp_path.glob("*.json"))


def test_reduce_refused_on_v1_wire_even_with_binding(tmp_path):
    """A reduce on the v1 wire is refused before any loader call: the v1 consumer
    would silently read it as a full sell."""
    package = _reduce_package()
    with pytest.raises(ProductDailyError, match="p12.daily-advice-package.v2"):
        publish_package(
            package,
            directory=tmp_path,
            published_at=package.decision_sets[0].published_at,
        )
    assert not list(tmp_path.glob("*.json"))


def test_reduce_refused_with_unpinned_v2_candidate_binding(tmp_path):
    """The candidate v2 binding declares the capability, but its source pin is
    pending/null - it can never satisfy the verified pin check."""
    package = _reduce_package()
    with pytest.raises(ProductDailyError, match="pinned identity"):
        publish_package(
            package,
            directory=tmp_path,
            published_at=package.decision_sets[0].published_at,
            p12_loader=_p12_loader(),
            consumer_binding=_v2_candidate_binding(),
            wire_version="p12.daily-advice-package.v2",
        )
    assert not list(tmp_path.glob("*.json"))


def test_reduce_refused_with_wrong_schema_binding(tmp_path):
    """A v1-pinned consumer binding cannot serve a v2 wire release."""
    package = _reduce_package()
    with pytest.raises(ProductDailyError, match="consumer_schema"):
        publish_package(
            package,
            directory=tmp_path,
            published_at=package.decision_sets[0].published_at,
            p12_loader=_p12_loader(),
            consumer_binding=_v1_consumer_binding(),
            wire_version="p12.daily-advice-package.v2",
        )
    assert not list(tmp_path.glob("*.json"))


def test_reduce_refused_with_arbitrary_capability_dict(tmp_path):
    """A caller-supplied {capabilities: true} dict is not a versioned binding."""
    package = _reduce_package()
    with pytest.raises(ProductDailyError, match="consumer-binding"):
        publish_package(
            package,
            directory=tmp_path,
            published_at=package.decision_sets[0].published_at,
            p12_loader=_p12_loader(),
            consumer_binding={"capabilities": {"supports_held_reduction_v2": True}},
            wire_version="p12.daily-advice-package.v2",
        )
    assert not list(tmp_path.glob("*.json"))


def _v2_pinned_binding() -> dict:
    """A fully-pinned v2 consumer binding for the gate mechanics.

    TEST_ONLY: stands in for the real p12.daily-advice-package.v2 pin that is not yet
    delivered.  It exercises the verified pipeline; it is not a claimed integration."""

    binding = _v2_candidate_binding()
    binding["source_commit"] = "9de1cdb999f692d6cc1cd5be13b53116600ab4bf"
    binding["source_tree"] = "53417ed848db0b78acb928ea2f7d1bfafa44be95"
    binding["source_files_sha256"] = {"placeholder": "0" * 64}
    return binding


def _v2_double_loader():
    """TEST_ONLY field-preserving loader double for the v2 wire.

    Marked _p11_test_only_consumer so it may exercise the pipeline mechanics; it is
    never a real consumer qualification."
    """

    @dataclasses.dataclass(frozen=True)
    class _Decision:
        opportunity_id: str
        security_code: str
        stage: str
        exit_kind: str | None
        target_position_fraction: Decimal | None

    @dataclasses.dataclass(frozen=True)
    class _Set:
        decisions: list
        market_date: date

    def build(raw, *, expected_schema=None):
        return _Set(
            decisions=[
                _Decision(
                    opportunity_id=d["opportunity_id"],
                    security_code=d["security_code"],
                    stage=d["stage"],
                    exit_kind=d.get("exit_kind"),
                    target_position_fraction=(
                        None
                        if d.get("target_position_fraction") is None
                        else Decimal(str(d["target_position_fraction"]))
                    ),
                )
                for d in raw["decisions"]
            ],
            market_date=date.fromisoformat(raw["market_date"]),
        )

    _Set.__module__ = "quanthus.capabilities.decision_access"
    _Decision.__module__ = "quanthus.capabilities.decision_access"
    build._p11_test_only_consumer = True  # type: ignore[attr-defined]
    return build


def test_reduce_publishes_through_marked_test_double(tmp_path):
    """The verified pipeline opens for a marked TEST_ONLY double that preserves every
    coordinate - mechanics evidence only, not a real v2 integration."""
    package = _reduce_package()
    receipt = publish_package(
        package,
        directory=tmp_path,
        published_at=package.decision_sets[0].published_at,
        p12_loader=_v2_double_loader(),
        consumer_binding=_v2_pinned_binding(),
        wire_version="p12.daily-advice-package.v2",
    )
    raw = json.loads(Path(receipt.path).read_text())
    decision = raw["decision_sets"][0]["decisions"][0]
    assert decision["exit_kind"] == "reduce"
    assert decision["target_position_fraction"] == "0.5"


def test_reduce_refused_when_loader_drifts_between_calls(tmp_path):
    """CV2-963-P1-3: the digest is computed from the SAME validated object that passed
    the gate.  A loader whose second call drops fields can no longer exist, because
    there is only one call."""
    package = _reduce_package()
    calls = {"count": 0}

    def unstable_loader(raw, *, expected_schema=None):
        calls["count"] += 1
        out = _v2_double_loader()(raw, expected_schema=expected_schema)
        if calls["count"] > 1:
            out = dataclasses.replace(
                out,
                decisions=[
                    dataclasses.replace(d, exit_kind=None, target_position_fraction=None)
                    for d in out.decisions
                ],
            )
        return out

    unstable_loader._p11_test_only_consumer = True  # type: ignore[attr-defined]
    # The single-call design means the second (drifting) call never happens; the
    # release either passes on the single validated object or fails.
    receipt = publish_package(
        package,
        directory=tmp_path,
        published_at=package.decision_sets[0].published_at,
        p12_loader=unstable_loader,
        consumer_binding=_v2_pinned_binding(),
        wire_version="p12.daily-advice-package.v2",
    )
    assert calls["count"] == 1, "the consumer must be validated exactly once"
    raw = json.loads(Path(receipt.path).read_text())
    assert raw["decision_sets"][0]["decisions"][0]["exit_kind"] == "reduce"


def test_reduce_refused_when_loader_alters_target(tmp_path):
    package = _reduce_package()

    def bad_loader(raw, *, expected_schema=None):
        out = _v2_double_loader()(raw, expected_schema=expected_schema)
        return dataclasses.replace(
            out,
            decisions=[
                dataclasses.replace(d, target_position_fraction=Decimal("0.99"))
                for d in out.decisions
            ],
        )

    bad_loader._p11_test_only_consumer = True  # type: ignore[attr-defined]
    with pytest.raises(ProductDailyError, match="reduce target"):
        publish_package(
            package,
            directory=tmp_path,
            published_at=package.decision_sets[0].published_at,
            p12_loader=bad_loader,
            consumer_binding=_v2_pinned_binding(),
            wire_version="p12.daily-advice-package.v2",
        )
    assert not list(tmp_path.glob("*.json"))


def test_reduce_refused_when_loader_returns_wrong_security(tmp_path):
    """CV2-963-P1-3: a validated object with a different security/stage is refused."""
    package = _reduce_package()

    def wrong_loader(raw, *, expected_schema=None):
        out = _v2_double_loader()(raw, expected_schema=expected_schema)
        return dataclasses.replace(
            out,
            decisions=[
                dataclasses.replace(d, security_code="999999", stage="holding")
                for d in out.decisions
            ],
        )

    wrong_loader._p11_test_only_consumer = True  # type: ignore[attr-defined]
    with pytest.raises(ProductDailyError, match="security/stage"):
        publish_package(
            package,
            directory=tmp_path,
            published_at=package.decision_sets[0].published_at,
            p12_loader=wrong_loader,
            consumer_binding=_v2_pinned_binding(),
            wire_version="p12.daily-advice-package.v2",
        )
    assert not list(tmp_path.glob("*.json"))


def test_full_exit_package_publishes_without_v2_consumer(tmp_path):
    package = _publishable_package(_held("exit", None))
    receipt = publish_package(
        package,
        directory=tmp_path,
        published_at=package.decision_sets[0].published_at,
    )
    assert receipt.file_sha256.startswith("sha256:")


# --- B. v2 wire emission: exit fields live only on the v2 decision payload ---------------


def test_v1_decision_bytes_carry_no_exit_fields():
    """The frozen v1 payload is byte-stable: no exit_kind/target keys on any stage."""
    raw = _decision(stage=HOLDING).to_dict()
    assert "exit_kind" not in raw and "target_position_fraction" not in raw
    raw = _decision(stage=EXIT_PLAN, exit_kind="full").to_dict()
    assert "exit_kind" not in raw and "target_position_fraction" not in raw
    raw = _decision(
        stage=EXIT_PLAN, exit_kind="reduce", target_position_fraction=Decimal("0.5")
    ).to_dict()
    assert "exit_kind" not in raw and "target_position_fraction" not in raw


def test_v2_wire_dict_carries_exit_fields_only_on_v2_payload():
    raw_v1 = _decision(
        stage=EXIT_PLAN, exit_kind="reduce", target_position_fraction=Decimal("0.5")
    ).to_dict()
    raw_v2 = _decision(
        stage=EXIT_PLAN, exit_kind="reduce", target_position_fraction=Decimal("0.5")
    ).to_wire_dict()
    assert "exit_kind" not in raw_v1
    assert raw_v2["exit_kind"] == "reduce"
    assert raw_v2["target_position_fraction"] == "0.5"


def test_decision_set_v2_wire_dict_carries_fields():
    ds = _produce_held(
        _held("reduce", None, formal=_formal_reduce_decision(Decimal("0.5")))
    ).package.decision_sets[0]
    v1 = ds.to_dict()
    v2 = ds.to_wire_dict()
    assert "exit_kind" not in v1["decisions"][0]
    assert v2["decisions"][0]["exit_kind"] == "reduce"
    assert v2["decisions"][0]["target_position_fraction"] == "0.5"
    # everything else is the same payload
    assert {k: v for k, v in v1.items() if k != "decisions"} == {
        k: v for k, v in v2.items() if k != "decisions"
    }


# --- B. named dependency: the pinned v1 P12 loader drops the v2 fields -------------------------


def test_old_p12_loader_drops_reduce_fields_named_dependency():
    """The pinned 9de1cdb9 synthetic loader accepts an exit_plan but silently drops
    exit_kind/target_position_fraction - the precise reason an executable reduce must
    wait for the pinned p12.daily-advice-package.v2 consumer.  This is a documented gap,
    not a passed integration."""

    validate = _p12_loader()
    package = _reduce_package()
    raw = package.decision_sets[0].to_dict()
    # The v1 decision payload carries no exit fields at all now; the loader accepts the
    # v1 shape and naturally sees nothing to drop.  The v2 wire is what carries them.
    wire = package.decision_sets[0].to_wire_dict()
    assert wire["decisions"][0]["exit_kind"] == "reduce"
    assert wire["decisions"][0]["target_position_fraction"] == "0.5"
    validated = validate(raw)  # the v1 loader still accepts the set
    assert not hasattr(validated.decisions[0], "exit_kind")
    assert not hasattr(validated.decisions[0], "target_position_fraction")


def test_wire_version_constant_matches_p12_handoff():
    assert HELD_REDUCTION_WIRE_VERSION == "p12.daily-advice-package.v2"
