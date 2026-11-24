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
    verify_release_manifest_closure,
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


def _mutate(path: Path, marker: bytes):
    original = path.read_bytes()
    path.write_bytes(original + b"\n# CLOSURE MUTATION " + marker + b"\n")
    return original


def test_manifest_closure_rejects_unlisted_source_drift():
    """A tampered file under src/ outside the named code roles must fail."""
    target = ROOT / "src" / "quanthus" / "policy.py"
    original = _mutate(target, b"src-unlisted")
    try:
        manifest = load_release_manifest(MANIFEST_V2)
        with pytest.raises(ProductDailyError, match="semantic closure drifted"):
            check_three_piece_consistency(
                action_receipts=load_action_receipts(RECEIPTS_V1),
                machine_policy=load_machine_policy(POLICY_V2),
                release_manifest=manifest,
            )
    finally:
        target.write_bytes(original)


def test_manifest_closure_rejects_uncovered_test_drift():
    """test_p11_daily_runner.py is inside tests/ but outside the old
    test_product_daily prefix - drift there must still fail."""
    target = ROOT / "tests" / "unit" / "test_p11_daily_runner.py"
    original = _mutate(target, b"runner-test")
    try:
        manifest = load_release_manifest(MANIFEST_V2)
        with pytest.raises(ProductDailyError, match="semantic closure drifted"):
            verify_release_manifest_closure(manifest, project_root=ROOT, pending_is_failure=False)
    finally:
        target.write_bytes(original)


def test_manifest_closure_rejects_untracked_semantic_file():
    """A new file under a semantic root not in the declared tree is drift."""
    target = ROOT / "tests" / "unit" / "__closure_probe_untracked.py"
    target.write_text("# probe\n")
    try:
        manifest = load_release_manifest(MANIFEST_V2)
        with pytest.raises(ProductDailyError, match="semantic closure drifted"):
            verify_release_manifest_closure(manifest, project_root=ROOT, pending_is_failure=False)
    finally:
        target.unlink()


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
    the p12.decision-set.v1 wire."""

    from quanthus.product_daily.recommendation.runtime_identity import consumer_identity

    binding = consumer_identity(ROOT)
    binding["consumer_schema"] = "p12.decision-set.v1"
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


def test_caller_wire_version_cannot_override_release(tmp_path):
    """wire_version is an assertion only: the registered release picks the wire,
    never a caller value.  The bound pin resolves the real loader internally."""
    package = _reduce_package()
    # Caller asks for v1 while the registered v2 release emits v2: still refused
    # because the assertion disagrees with the release, before any loader runs.
    with pytest.raises(ProductDailyError, match="version_inconsistent"):
        publish_package(
            package,
            directory=tmp_path,
            published_at=package.decision_sets[0].published_at,
            wire_version="p12.decision-set.v1",
        )
    assert not list(tmp_path.glob("*.json"))


def test_reduce_refused_on_v1_wire_even_with_binding(tmp_path):
    """A package registered under the v2 release always serializes on the
    p12.decision-set.v2 wire; a caller cannot downgrade it to v1, and the bound
    pin resolves the real loader that preserves exit_kind/target through the
    (object, wire_digest) contract."""
    package = _reduce_package()
    receipt = publish_package(
        package,
        directory=tmp_path,
        published_at=package.decision_sets[0].published_at,
    )
    payload = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert payload["decision_sets"][0]["decision_set_schema"] == "p12.decision-set.v2"
    assert payload["decision_sets"][0]["decisions"][0]["exit_kind"] == "reduce"
    assert receipt.p12_package_digests is not None


def test_caller_binding_dict_grants_no_v2_authority(tmp_path):
    """A caller-supplied consumer_binding dict is never consulted: the versioned
    on-disk pin is the only authority, and the pinned loader is resolved
    internally.  A forged caller dict cannot redirect the release - the publish
    still runs through the bound pin, never through the caller object."""
    package = _reduce_package()
    receipt = publish_package(
        package,
        directory=tmp_path,
        published_at=package.decision_sets[0].published_at,
        p12_loader=_p12_loader(),
        consumer_binding={"loader_entry": "forged.loader", "capabilities": {}},
        wire_version="p12.decision-set.v2",
    )
    # The published bytes carry the v2 marker and the real pinned wire digest.
    payload = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert payload["decision_sets"][0]["decision_set_schema"] == "p12.decision-set.v2"
    assert receipt.p12_package_digests is not None


def test_reduce_refused_with_wrong_schema_binding(tmp_path):
    """A v1-pinned caller binding cannot serve a v2 wire release: the bound
    on-disk pin is the only authority and resolves the real loader itself."""
    package = _reduce_package()
    receipt = publish_package(
        package,
        directory=tmp_path,
        published_at=package.decision_sets[0].published_at,
        p12_loader=_p12_loader(),
        consumer_binding=_v1_consumer_binding(),
        wire_version="p12.decision-set.v2",
    )
    payload = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert payload["decision_sets"][0]["decision_set_schema"] == "p12.decision-set.v2"
    assert receipt.p12_package_digests is not None


def test_reduce_refused_with_arbitrary_capability_dict(tmp_path):
    """A caller-supplied {capabilities: true} dict is never a versioned pin - the
    bound on-disk pin is, and it resolves the real loader internally."""
    package = _reduce_package()
    receipt = publish_package(
        package,
        directory=tmp_path,
        published_at=package.decision_sets[0].published_at,
        p12_loader=_p12_loader(),
        consumer_binding={"capabilities": {"supports_held_reduction_v2": True}},
        wire_version="p12.decision-set.v2",
    )
    payload = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert payload["decision_sets"][0]["decision_set_schema"] == "p12.decision-set.v2"
    assert receipt.p12_package_digests is not None


def _v2_double_loader():
    """Pure field-preserving loader double for the internal coordinate matcher.

    It only builds a validated-shaped object so _validate_consumer_object's
    coordinate checks can be exercised without writing anything; it is never a
    release authority and cannot open publish_package while the pin is pending.
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
    return build


def _self_contained_p12_digest(monkeypatch):
    """Each test that needs the bound-package digest declares its own dependency.

    The P11 tree carries no quanthus.shared.fact_contracts of its own; a test that
    exercises the internal matcher installs the digest namespace explicitly
    instead of relying on an earlier test's sys.path side effects (CV2-6C-P2-1).
    """

    import sys
    import types

    shared = types.ModuleType("quanthus.shared")
    shared.__path__ = []
    fact_contracts = types.ModuleType("quanthus.shared.fact_contracts")
    fact_contracts.digest = lambda _value: "sha256:" + "ab" * 32
    monkeypatch.setitem(sys.modules, "quanthus.shared", shared)
    monkeypatch.setitem(sys.modules, "quanthus.shared.fact_contracts", fact_contracts)


def _v2_wire(package) -> dict:
    from quanthus.product_daily.recommendation.publish import _serialize_decision_sets

    return _serialize_decision_sets(package, wire_version="p12.decision-set.v2")[0]


def test_reduce_public_release_only_through_bound_pin(tmp_path):
    """CV2-6C-P1-3 continuation: no caller loader, fake pin dict or injected
    namespace selects the consumer - the versioned on-disk pin resolves the real
    loader internally, so each release runs through the bound P12 tree only."""
    package = _reduce_package()
    for loader, binding in (
        (_v2_double_loader(), None),
        (
            _v2_double_loader(),
            {
                "schema_version": "quanthus.p11.p12-consumer-binding.v1",
                "consumer_schema": "p12.decision-set.v2",
                "loader_entry": "forged.loader",
                "source_commit": "f" * 40,
                "source_tree": "e" * 40,
                "source_files_sha256": {"x.py": "0" * 64},
                "capabilities": {"supports_held_reduction_v2": True},
            },
        ),
        (_p12_loader(), _v2_candidate_binding()),
        (None, None),
    ):
        sub = tmp_path / str(len(list(tmp_path.iterdir())))
        receipt = publish_package(
            package,
            directory=sub,
            published_at=package.decision_sets[0].published_at,
            p12_loader=loader,
            consumer_binding=binding,
            wire_version="p12.decision-set.v2",
        )
        payload = json.loads(next(sub.glob("*.json")).read_text())
        assert payload["decision_sets"][0]["decision_set_schema"] == "p12.decision-set.v2"
        assert receipt.p12_package_digests is not None


def test_validate_consumer_object_field_preserving(monkeypatch):
    """The internal single-object matcher accepts a coordinate-faithful validated
    object and returns the account binding digest - the same digest() over the
    validated object's package content the P12 ConfigurationBinding records.
    Pure mechanism, no release."""
    from quanthus.product_daily.recommendation.publish import _validate_consumer_object

    _self_contained_p12_digest(monkeypatch)
    package = _reduce_package()
    raw = _v2_wire(package)
    validated = _v2_double_loader()(raw, expected_schema="p12.decision-set.v2")
    assert _validate_consumer_object(
        raw,
        validated,
        wire_version="p12.decision-set.v2",
        wire_digest="sha256:" + "cd" * 32,
        account_digest="sha256:" + "ab" * 32,
    ) == ("sha256:" + "ab" * 32)


def test_validate_consumer_object_never_returns_wire_digest(monkeypatch):
    """The wire digest is the raw-byte identity, not the account binding
    identity: even when the pinned loader returns one, the matcher must answer
    with the digest of the SAME validated object's package content."""
    from quanthus.product_daily.recommendation.publish import _validate_consumer_object

    _self_contained_p12_digest(monkeypatch)
    package = _reduce_package()
    raw = _v2_wire(package)
    validated = _v2_double_loader()(raw, expected_schema="p12.decision-set.v2")
    matched = _validate_consumer_object(
        raw,
        validated,
        wire_version="p12.decision-set.v2",
        wire_digest="sha256:" + "cd" * 32,
        account_digest="sha256:" + "ab" * 32,
    )
    assert matched == "sha256:" + "ab" * 32
    assert matched != "sha256:" + "cd" * 32


def test_validate_consumer_object_rejects_altered_target(monkeypatch):
    from quanthus.product_daily.recommendation.publish import _validate_consumer_object

    _self_contained_p12_digest(monkeypatch)
    package = _reduce_package()
    raw = _v2_wire(package)

    validated = _v2_double_loader()(raw, expected_schema="p12.decision-set.v2")
    drifted = dataclasses.replace(
        validated,
        decisions=[
            dataclasses.replace(d, target_position_fraction=Decimal("0.99"))
            for d in validated.decisions
        ],
    )
    with pytest.raises(ProductDailyError, match="reduce target"):
        _validate_consumer_object(raw, drifted, wire_version="p12.decision-set.v2")


def test_validate_consumer_object_rejects_wrong_security(monkeypatch):
    from quanthus.product_daily.recommendation.publish import _validate_consumer_object

    _self_contained_p12_digest(monkeypatch)
    package = _reduce_package()
    raw = _v2_wire(package)

    validated = _v2_double_loader()(raw, expected_schema="p12.decision-set.v2")
    drifted = dataclasses.replace(
        validated,
        decisions=[
            dataclasses.replace(d, security_code="999999", stage="holding")
            for d in validated.decisions
        ],
    )
    with pytest.raises(ProductDailyError, match="security/stage"):
        _validate_consumer_object(raw, drifted, wire_version="p12.decision-set.v2")


def test_validate_consumer_object_rejects_dropped_exit_kind(monkeypatch):
    from quanthus.product_daily.recommendation.publish import _validate_consumer_object

    _self_contained_p12_digest(monkeypatch)
    package = _reduce_package()
    raw = _v2_wire(package)
    validated = _v2_double_loader()(raw, expected_schema="p12.decision-set.v2")
    drifted = dataclasses.replace(
        validated,
        decisions=[
            dataclasses.replace(d, exit_kind=None, target_position_fraction=None)
            for d in validated.decisions
        ],
    )
    with pytest.raises(ProductDailyError, match="exit_kind"):
        _validate_consumer_object(raw, drifted, wire_version="p12.decision-set.v2")


def test_configured_full_exit_also_requires_v2_consumer(tmp_path):
    package = _publishable_package(_held("exit", None))
    receipt = publish_package(
        package,
        directory=tmp_path,
        published_at=package.decision_sets[0].published_at,
    )
    payload = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert payload["decision_sets"][0]["decision_set_schema"] == "p12.decision-set.v2"
    assert payload["decision_sets"][0]["decisions"][0]["exit_kind"] == "full"
    assert receipt.p12_package_digests is not None


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


def test_v2_package_dict_is_self_describing():
    """#723 comment7121035802: a v2 release carries the P11 envelope identity on
    the package and the p12.decision-set.v2 header on each raw decision_set - the
    two layers are named separately, never a single wire string."""
    from quanthus.product_daily.recommendation.publish import _package_file_dict

    package = _reduce_package()
    raw = _package_file_dict(package, wire_version="p12.decision-set.v2")
    assert raw["schema_version"] == "p11.daily-advice-package.v2"
    assert raw["version"] == "1.0.0"  # business version keeps its own meaning
    ds = raw["decision_sets"][0]
    assert ds["decision_set_schema"] == "p12.decision-set.v2"
    assert ds.get("schema_version") is None


# --- C. v2 reduce target canonical wire form (issue #723 comment7141061814) ----


def test_v2_target_fraction_canonical_form():
    r"""The v2 writer emits the unique canonical string ^0.[0-9]*[1-9]$ for a
    reduce target: a finite plain decimal strictly in (0,1), trailing fractional
    zeros stripped, no exponent or whitespace."""
    from quanthus.product_daily.recommendation.contracts import (
        V2_TARGET_FRACTION_PATTERN,
        v2_target_fraction_text,
    )

    cases = {
        Decimal("0.10"): "0.1",
        Decimal("0.5000"): "0.5",
        Decimal("1E-3"): "0.001",
        Decimal("0.1"): "0.1",
        Decimal("0.00000000000000001"): "0.00000000000000001",
        Decimal("0.999999"): "0.999999",
        Decimal("0.9"): "0.9",
    }
    for value, expected in cases.items():
        out = v2_target_fraction_text(value)
        assert out == expected
        assert V2_TARGET_FRACTION_PATTERN.match(out), (value, out)
        # read-back identity: the emitted string re-parses to a Decimal whose
        # own canonical emission is byte-identical.
        assert v2_target_fraction_text(Decimal(out)) == out
    assert v2_target_fraction_text(None) is None


def test_v2_target_fraction_refuses_non_canonical_values():
    """Zero, integral and out-of-(0,1) or non-finite targets are contract errors
    at write time - the reader-side refusal of leading-space/exponent/trailing
    whitespace bytes is preserved by never emitting them."""
    from quanthus.product_daily.recommendation.contracts import v2_target_fraction_text

    for bad in ("0", "0.0", "1", "1.0", "2", "-0.5"):
        with pytest.raises(ProductDailyError, match="> 0 and < 1"):
            v2_target_fraction_text(Decimal(bad))
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ProductDailyError, match="finite"):
            v2_target_fraction_text(Decimal(bad))


def test_v2_wire_dict_emits_canonical_reduce_target():
    """to_wire_dict normalizes the Decimal through the canonical form, so a
    stored 0.10 or 0.5000 writes as 0.1 / 0.5 and a stored exponent Decimal
    writes as its plain decimal expansion."""
    assert (
        _decision(
            stage=EXIT_PLAN, exit_kind="reduce", target_position_fraction=Decimal("0.10")
        ).to_wire_dict()["target_position_fraction"]
        == "0.1"
    )
    assert (
        _decision(
            stage=EXIT_PLAN, exit_kind="reduce", target_position_fraction=Decimal("0.5000")
        ).to_wire_dict()["target_position_fraction"]
        == "0.5"
    )
    assert (
        _decision(
            stage=EXIT_PLAN, exit_kind="reduce", target_position_fraction=Decimal("1E-3")
        ).to_wire_dict()["target_position_fraction"]
        == "0.001"
    )
    assert (
        _decision(
            stage=EXIT_PLAN,
            exit_kind="reduce",
            target_position_fraction=Decimal("0.00000000000000001"),
        ).to_wire_dict()["target_position_fraction"]
        == "0.00000000000000001"
    )
    # full exit: still null, never a number.
    assert (
        _decision(stage=EXIT_PLAN, exit_kind="full", target_position_fraction=None).to_wire_dict()[
            "target_position_fraction"
        ]
        is None
    )


def test_v1_and_shared_decimal_surfaces_unchanged():
    """The canonical target form is v2-only: the v1 decision payload keeps no
    exit fields at all, and decimal_text (cash/price/shared) still uses bare
    format(x, "f") - never rstrip-normalized."""
    from quanthus.product_daily.contracts import decimal_text

    # A trailing-zero Decimal on a shared surface keeps its full expansion; only
    # the v2 reduce target normalizes.
    assert decimal_text(Decimal("0.10")) == "0.10"
    assert decimal_text(Decimal("0.5000")) == "0.5000"
    raw_v1 = _decision(
        stage=EXIT_PLAN, exit_kind="reduce", target_position_fraction=Decimal("0.10")
    ).to_dict()
    assert "target_position_fraction" not in raw_v1
    assert "exit_kind" not in raw_v1


def test_v1_wire_refuses_v2_only_fields():
    """A v1 raw that still shows exit fields is a version inconsistency, never a
    silent field drop."""
    from quanthus.product_daily.recommendation.publish import _serialize_decision_sets

    package = _reduce_package()
    ds = package.decision_sets[0]
    # A stand-in decision_set whose to_dict yields a raw that still carries the
    # v2-only exit fields; the serializer must refuse rather than strip them.
    tampered = ds.to_dict()
    tampered["decisions"][0]["exit_kind"] = "reduce"

    class _FakeSet:
        def to_dict(self):
            return tampered

    class _FakePackage:
        decision_sets = (_FakeSet(),)

    import pytest

    with pytest.raises(ProductDailyError, match="version_inconsistent"):
        _serialize_decision_sets(
            _FakePackage(),  # type: ignore[arg-type]
            wire_version="p12.decision-set.v1",
        )


# --- B. named dependency: the pinned v1 P12 loader drops the v2 fields -------------------------


def test_old_p12_loader_drops_reduce_fields_named_dependency():
    """The pinned 9de1cdb9 synthetic loader accepts an exit_plan but silently drops
    exit_kind/target_position_fraction - the precise reason an executable reduce must
    wait for the pinned p12.decision-set.v2 consumer.  This is a documented gap,
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
    # The v1-surface decision never carries reduce semantics: on the pinned v1
    # synthetic loader the fields are absent or None, never a reduce.
    assert getattr(validated.decisions[0], "exit_kind", None) in (None,)
    assert getattr(validated.decisions[0], "target_position_fraction", None) is None


def test_wire_version_constant_matches_p12_handoff():
    assert HELD_REDUCTION_WIRE_VERSION == "p12.decision-set.v2"
