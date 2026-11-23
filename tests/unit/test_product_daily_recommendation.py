"""Synthetic contract tests for the R11 daily advice producer."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation import (
    AccountFactsView,
    AdmissionDisposition,
    AdvicePolicy,
    CandidateEvaluationReceipt,
    CandidateSignal,
    DailyAdviceProducer,
    DailyFactsView,
    ExpertBundle,
    MemberEvidence,
    package_digests,
    publish_package,
)
from quanthus.product_daily.recommendation.contracts import (
    ENDED,
    ENTRY_PLAN,
    INPUT_BLOCKED,
    SCHEMA,
)

TZ = timezone(timedelta(hours=8))
CUTOFF = datetime(2026, 11, 20, 22, 30, tzinfo=TZ)
PUBLISHED = datetime(2026, 11, 20, 22, 45, tzinfo=TZ)


def _member(mid: str, available_at: datetime, required: bool = True) -> MemberEvidence:
    return MemberEvidence(mid, "1.0.0", "sha256:" + "a" * 64, available_at, required)


def _facts(members, required=("daily-bars", "calendar", "industry-map")) -> DailyFactsView:
    return DailyFactsView(
        batch_id="batch-20261120",
        market_date=date(2026, 11, 20),
        next_session=date(2026, 11, 23),
        source_id="p11-daily",
        source_version="1.0.0",
        calendar_version="cal-1",
        price_basis="unadjusted",
        members=tuple(members),
        required_member_ids=tuple(required),
        intake_receipt_id="intake-20261120",
        intake_received_at=datetime(2026, 11, 20, 18, 30, tzinfo=TZ),
        intake_verified_at=datetime(2026, 11, 20, 18, 45, tzinfo=TZ),
    )


def _full_members(at=datetime(2026, 11, 20, 18, 0, tzinfo=TZ)) -> list[MemberEvidence]:
    return [
        _member("daily-bars", at),
        _member("calendar", at),
        _member("industry-map", at),
    ]


def _policy() -> AdvicePolicy:
    return AdvicePolicy(
        policy_id="r11-daily-advice",
        version="0.1.0",
        decision_cutoff=CUTOFF,
        published_at=PUBLISHED,
        window_ref="replay-manual-window:2026-11-23",
        eligible_at=datetime(2026, 11, 23, 9, 30, tzinfo=TZ),
        expires_at=datetime(2026, 11, 23, 15, 0, tzinfo=TZ),
        candidate_rule_digest="sha256:" + "b" * 64,
    )


def _account() -> AccountFactsView:
    return AccountFactsView(
        account_id="SYN-PAPER-A",
        as_of=datetime(2026, 11, 20, 15, 0, tzinfo=TZ),
        cash_available=Decimal("100000.00"),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        pending_commitment_total=Decimal("0"),
    )


def _experts() -> ExpertBundle:
    return ExpertBundle("replay.trend-position-risk", "1.0.0", "sha256:" + "c" * 64)


def _candidate(member_ids=("daily-bars",)) -> CandidateSignal:
    return CandidateSignal(
        opportunity_id="OPP-600000",
        security_code="600000",
        security_name="浦发银行",
        industry_id="IND-BANK",
        fraction=Decimal("0.5"),
        price_bound=Decimal("10.75"),
        mark_price=Decimal("10.60"),
        component_ref_id="replay.trend-position-risk",
        component_version="1.0.0",
        reason_codes=("NEW_ENTRY_SIGNAL",),
        explanations=(
            {
                "code": "REPLAY_ENTRY",
                "use": "action",
                "component_ref": "replay.trend-position-risk@1.0.0",
                "text": "entry plan for the next session window",
            },
            {
                "code": "REPLAY_SIZING",
                "use": "explanation",
                "component_ref": "replay.trend-position-risk@1.0.0",
                "text": "sizing is bounded by account cash, bucket budget and concentration caps",
            },
        ),
        invalidations=("cancel if open above price_bound",),
        next_observation="2026-11-23 close",
        depends_on_member_ids=tuple(member_ids),
        expert_decision_digest="sha256:" + "e" * 64,
        risk_decision_digest="sha256:" + "f" * 64,
    )


def _receipt(qualified: int = 1, evaluated: int = 1) -> CandidateEvaluationReceipt:
    return CandidateEvaluationReceipt(
        producer_id="r11-candidate-producer",
        producer_version="1.0.0",
        run_id="run-20261120",
        completed_at=datetime(2026, 11, 20, 18, 5, tzinfo=TZ),
        coverage_digest="sha256:" + "d" * 64,
        evaluated_count=evaluated,
        qualified_count=qualified,
    )


def producer() -> DailyAdviceProducer:
    return DailyAdviceProducer()


def test_entry_plan_package_is_publishable(tmp_path: Path):
    facts = _facts(_full_members())
    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=(_candidate(),),
        evaluation_receipt=_receipt(),
    )
    assert result.publishable
    pkg = result.package
    assert pkg is not None and pkg.schema == SCHEMA
    ds = pkg.decision_sets[0]
    assert ds.decisions[0].stage == ENTRY_PLAN
    assert ds.decisions[0].entry is not None
    assert ds.decisions[0].entry.eligible_at == datetime(2026, 11, 23, 9, 30, tzinfo=TZ)
    assert (
        dict(ds.audit.per_member_available_at)["daily-bars"]
        == datetime(2026, 11, 20, 18, 0, tzinfo=TZ).isoformat()
    )
    assert ds.audit.admission_disposition == "publishable"
    digests = package_digests(pkg)
    assert digests["2026-11-20"].startswith("sha256:")
    receipt = publish_package(pkg, directory=tmp_path, published_at=PUBLISHED)
    assert receipt.entry_plan_count == 1
    assert dict(receipt.package_digests)["2026-11-20"] == digests["2026-11-20"]


def test_required_member_after_cutoff_fails_closed():
    facts = _facts(
        [
            _member("daily-bars", datetime(2026, 11, 20, 23, 0, tzinfo=TZ)),
            _member("calendar", datetime(2026, 11, 20, 18, 0, tzinfo=TZ)),
            _member("industry-map", datetime(2026, 11, 20, 18, 0, tzinfo=TZ)),
        ]
    )
    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=(_candidate(),),
        evaluation_receipt=_receipt(),
    )
    assert not result.publishable
    assert result.package is None
    assert result.disposition is AdmissionDisposition.INPUT_NOT_READY
    assert result.diagnostics[0]["reason"] == "member_clock_blocked"


def test_blocked_optional_member_blocks_only_its_decision():
    facts = _facts(
        [
            _member("daily-bars", datetime(2026, 11, 20, 18, 0, tzinfo=TZ)),
            _member("calendar", datetime(2026, 11, 20, 18, 0, tzinfo=TZ)),
            _member(
                "industry-map",
                datetime(2026, 11, 20, 23, 0, tzinfo=TZ),
                required=False,
            ),
        ],
        required=("daily-bars", "calendar"),
    )
    both = (
        _candidate(("daily-bars",)),
        _candidate(("industry-map",)),
    )
    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=both,
        evaluation_receipt=_receipt(qualified=2, evaluated=2),
    )
    assert result.publishable
    stages = [d.stage for d in result.package.decision_sets[0].decisions]
    assert stages == [ENTRY_PLAN, INPUT_BLOCKED]


def test_empty_advice_requires_a_completion_receipt():
    facts = _facts(_full_members())
    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=(),
        evaluation_receipt=None,
    )
    assert not result.publishable
    assert result.package is None
    assert result.diagnostics[0]["reason"] == "candidate_evaluation_receipt_missing"

    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=(),
        evaluation_receipt=_receipt(qualified=0, evaluated=3),
    )
    assert result.publishable
    ds = result.package.decision_sets[0]
    assert len(ds.decisions) == 1
    assert ds.decisions[0].stage == ENDED
    assert ds.audit.admission_disposition == "empty_advice"


def test_receipt_mismatch_fails_closed():
    facts = _facts(_full_members())
    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=(),
        evaluation_receipt=_receipt(qualified=1, evaluated=3),
    )
    assert not result.publishable
    assert result.diagnostics[0]["reason"] == "receipt_candidate_mismatch"


def test_unqualified_candidate_is_not_publishable():
    facts = _facts(_full_members())
    signal = _candidate()
    object.__setattr__(signal, "expert_decision_digest", None)
    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=(signal,),
        evaluation_receipt=_receipt(),
    )
    stages = [d.stage for d in result.package.decision_sets[0].decisions]
    assert stages == [INPUT_BLOCKED]
    assert result.diagnostics[0]["reason"] == "unqualified_candidate"


def test_publish_refuses_same_date_rewrite(tmp_path: Path):
    facts = _facts(_full_members())
    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=(_candidate(),),
    )
    publish_package(result.package, directory=tmp_path, published_at=PUBLISHED)
    with pytest.raises(ProductDailyError):
        publish_package(result.package, directory=tmp_path, published_at=PUBLISHED)


def test_published_at_must_not_precede_cutoff():
    with pytest.raises(ProductDailyError):
        AdvicePolicy(
            policy_id="p",
            version="0.1.0",
            decision_cutoff=CUTOFF,
            published_at=CUTOFF - timedelta(hours=1),
            window_ref="w",
            eligible_at=datetime(2026, 11, 23, 9, 30, tzinfo=TZ),
            expires_at=datetime(2026, 11, 23, 15, 0, tzinfo=TZ),
            candidate_rule_digest="sha256:" + "b" * 64,
        )


def _p12_loader():
    """Return the real P12 fixture loader when the sibling P12 tree is importable.

    The continuation branch does not vendor P12's `platform/adapters`; the integration test
    only runs when the sibling P12 worktree is on disk and its modules import.  It stays
    synthetic-only and skips otherwise so the P11 suite remains self-contained.
    """

    p12_root = Path(
        "/Users/william/Projects/Github/Quanthus/artifacts/worktrees/p12-paper-integration-20261120"
    )
    p12_src = p12_root / "src"
    if not p12_src.is_dir():
        pytest.skip("P12 worktree not present")
    # `quanthus` is already bound to this worktree's src; register the sibling P12 package
    # path on the namespace so its extra subpackages (capabilities.decision_access,
    # platform.adapters.p12_synthetic_fixture) resolve from the P12 tree.
    # Register the sibling P12 tree on every already-bound quanthus subpackage whose
    # continuation counterpart does not contain the modules the loader needs.  Plain packages
    # keep their own __path__, so each level that exists on both trees must be extended.
    quanthus_pkg = sys.modules.get("quanthus")
    if quanthus_pkg is None:
        pytest.skip("quanthus package not initialized")
    p12_pkg = str(p12_src / "quanthus")
    for name in list(sys.modules):
        if not name.startswith("quanthus"):
            continue
        module = sys.modules[name]
        module_path = getattr(module, "__path__", None)
        if module_path is None:
            continue
        relative = name[len("quanthus") + 1 :].replace(".", "/") if name != "quanthus" else ""
        candidate = Path(p12_pkg) / relative
        if candidate.is_dir():
            candidate_str = str(candidate)
            if candidate_str not in module_path:
                module_path.insert(0, candidate_str)
    try:
        from quanthus.capabilities.decision_access import validate_decision_set
        from quanthus.platform.adapters.p12_synthetic_fixture import (
            load_synthetic_fixture,
        )
    except ImportError as exc:  # pragma: no cover - depends on sibling tree
        pytest.skip(f"P12 modules unavailable: {exc}")
    return load_synthetic_fixture, validate_decision_set


def _fixture_document(ds_dict: dict) -> dict:
    """Minimal P12 synthetic fixture carrying the produced decision_set verbatim."""

    return {
        "schema_version": "quanthus.p12.synthetic-desktop-fixture.v1",
        "fixture_id": "r11-g1-fixture",
        "version": "1.0.0",
        "synthetic": True,
        "mode": "synthetic",
        "synthetic_clock": "2026-11-20T22:50:00+08:00",
        "accounts": [
            {
                "account_id": "SYN-PAPER-A",
                "display_name": "模拟账户",
                "baseline_request_id": "r11-g1-baseline",
                "baseline_cash": "100000.00",
                "as_of": "2026-11-20T00:00:00+08:00",
                "source_id": "r11-g1",
                "coverage_id": "complete-cash-only",
            }
        ],
        "policy": {
            "composition_id": "r11-g1-composition",
            "composition_version": "1.0.0",
            "execution_profile": "close-to-next-open-v1",
            "funding": {
                "policy_id": "r11-g1-funding",
                "version": "1.0.0",
                "mode": "fixed_bucket",
                "unit_count": 2,
                "bucket_budget": "45000.00",
                "initial_reserve": "10000.00",
                "reserve_cap": None,
            },
            "risk": {
                "policy_id": "r11-g1-risk",
                "version": "1.0.0",
                "stock_limit": "0.60",
                "industry_limit": "0.60",
                "total_limit": "0.80",
                "lot_size": 100,
                "commission_rate": "0.0003",
                "minimum_commission": "5.00",
            },
        },
        "binding_catalog": {},
        "adoption": [],
        "decision_sets": [ds_dict],
    }


def test_produced_decision_set_loads_through_p12_loader(tmp_path: Path):
    load_synthetic_fixture, _ = _p12_loader()
    facts = _facts(_full_members())
    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=(_candidate(),),
        evaluation_receipt=_receipt(),
    )
    assert result.publishable
    ds_dict = result.package.decision_sets[0].to_dict()
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps(_fixture_document(ds_dict), ensure_ascii=False), encoding="utf-8"
    )
    loaded = load_synthetic_fixture(fixture_path)
    assert len(loaded.decisions) == 1
    loaded_ds = loaded.decisions[0]
    assert loaded_ds.market_date == date(2026, 11, 20)
    assert loaded_ds.decisions[0].stage.value == ENTRY_PLAN
    assert loaded_ds.decisions[0].entry is not None
    assert loaded_ds.decisions[0].entry.invalidations == ("cancel if open above price_bound",)


def test_p12_binding_digest_uses_the_validated_market_decision_set(tmp_path: Path):
    """The binding digest is over the P12-validated object, not the raw file bytes."""

    load_synthetic_fixture, _ = _p12_loader()
    facts = _facts(_full_members())
    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=(_candidate(),),
        evaluation_receipt=_receipt(),
    )
    ds_dict = result.package.decision_sets[0].to_dict()
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps(_fixture_document(ds_dict), ensure_ascii=False), encoding="utf-8"
    )

    import dataclasses

    from quanthus.shared.fact_contracts import digest as p12_digest

    validated = load_synthetic_fixture(fixture_path).decisions[0]
    expected = p12_digest(
        json.loads(json.dumps(dataclasses.asdict(validated), default=str, sort_keys=True))
    )

    from quanthus.product_daily.recommendation import p12_package_digests

    def loader(raw):
        return load_synthetic_fixture(tmp_path / "fixture.json").decisions[0]

    binding = p12_package_digests(ds_dict, loader)
    assert binding["2026-11-20"] == expected

    # The file-content digest is a different surface and must not be conflated with binding.
    content_digest = package_digests(result.package)["2026-11-20"]
    assert binding["2026-11-20"] != content_digest
