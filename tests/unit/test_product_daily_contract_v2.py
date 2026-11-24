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
    assert d.to_dict()["exit_kind"] == "full"
    assert d.to_dict()["target_position_fraction"] is None
    with pytest.raises(ProductDailyError, match="full exit carries no target"):
        _decision(exit_kind="full", target_position_fraction=Decimal("0.5"))


def test_exit_plan_reduce_requires_valid_target():
    for bad in (None, Decimal("0"), Decimal("-0.1"), Decimal("1.1"), Decimal("NaN")):
        with pytest.raises(ProductDailyError):
            _decision(exit_kind="reduce", target_position_fraction=bad)
    d = _decision(exit_kind="reduce", target_position_fraction=Decimal("0.5"))
    assert d.to_dict()["target_position_fraction"] == "0.5"


def test_exit_fields_rejected_outside_exit_plan():
    with pytest.raises(ProductDailyError, match="belong only to exit_plan"):
        _decision(stage=HOLDING, exit_kind="full")


def test_tampered_target_changes_wire_identity():
    from quanthus.event_library.contracts import sha256_digest

    a = _decision(exit_kind="reduce", target_position_fraction=Decimal("0.50"))
    b = _decision(exit_kind="reduce", target_position_fraction=Decimal("0.51"))
    assert sha256_digest(a.to_dict()) != sha256_digest(b.to_dict())


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


def _held(action: str, target: Decimal | None) -> HeldSignal:
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
    )


def test_producer_reduce_carries_target_fraction():
    result = _produce_held(_held("reduce", Decimal("0.5")))
    d = result.package.decision_sets[0].decisions[0]
    assert d.stage == "exit_plan" and d.exit_kind == "reduce"
    assert d.target_position_fraction == Decimal("0.5")


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


def test_reduce_package_refused_without_v2_consumer(tmp_path):
    package = _publishable_package(_held("reduce", Decimal("0.5")))
    with pytest.raises(ProductDailyError, match="supports_held_reduction_v2"):
        publish_package(
            package,
            directory=tmp_path,
            published_at=package.decision_sets[0].published_at,
        )


def test_reduce_package_publishes_with_v2_consumer(tmp_path):
    package = _publishable_package(_held("reduce", Decimal("0.5")))
    receipt = publish_package(
        package,
        directory=tmp_path,
        published_at=package.decision_sets[0].published_at,
        consumer_supports_held_reduction_v2=True,
    )
    raw = json.loads(Path(receipt.path).read_text())
    decision = raw["decision_sets"][0]["decisions"][0]
    assert decision["exit_kind"] == "reduce"
    assert decision["target_position_fraction"] == "0.5"


def test_full_exit_package_publishes_without_v2_consumer(tmp_path):
    package = _publishable_package(_held("exit", None))
    receipt = publish_package(
        package,
        directory=tmp_path,
        published_at=package.decision_sets[0].published_at,
    )
    assert receipt.file_sha256.startswith("sha256:")


# --- B. named dependency: the pinned v1 P12 loader drops the v2 fields -------------------------


def test_old_p12_loader_drops_reduce_fields_named_dependency():
    """The pinned 9de1cdb9 synthetic loader accepts an exit_plan but silently drops
    exit_kind/target_position_fraction - the precise reason an executable reduce must
    wait for the pinned p12.daily-advice-package.v2 consumer.  This is a documented gap,
    not a passed integration."""

    validate = _p12_loader()
    package = _publishable_package(_held("reduce", Decimal("0.5")))
    raw = package.decision_sets[0].to_dict()
    assert raw["decisions"][0]["exit_kind"] == "reduce"
    assert raw["decisions"][0]["target_position_fraction"] == "0.5"
    validated = validate(raw)  # the v1 loader still accepts the set
    # ...but the validated object carries neither field: a consumer that executed on it
    # would treat the reduce as an undifferentiated full exit_plan.
    assert not hasattr(validated.decisions[0], "exit_kind")
    assert not hasattr(validated.decisions[0], "target_position_fraction")


def test_wire_version_constant_matches_p12_handoff():
    assert HELD_REDUCTION_WIRE_VERSION == "p12.daily-advice-package.v2"
