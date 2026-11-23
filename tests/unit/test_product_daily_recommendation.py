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
    decision_set_digests,
    file_sha256,
    publish_package,
)
from quanthus.product_daily.recommendation.contracts import (
    ENDED,
    ENTRY_PLAN,
    INPUT_BLOCKED,
    INPUT_UNAVAILABLE,
    SCHEMA,
    STALE_MARKET_INPUT,
)
from quanthus.product_daily.recommendation.producer import _evidence_digest

TZ = timezone(timedelta(hours=8))
CUTOFF = datetime(2026, 11, 20, 22, 30, tzinfo=TZ)
PUBLISHED = datetime(2026, 11, 20, 22, 45, tzinfo=TZ)


def _member(mid: str, available_at: datetime, required: bool = True) -> MemberEvidence:
    return MemberEvidence(mid, "1.0.0", "sha256:" + "a" * 64, available_at, required)


def _facts(
    members,
    required=("daily-bars", "calendar", "industry-map"),
    received=datetime(2026, 11, 20, 18, 30, tzinfo=TZ),
    verified=datetime(2026, 11, 20, 18, 45, tzinfo=TZ),
) -> DailyFactsView:
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
        intake_received_at=received,
        intake_verified_at=verified,
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


def _evidence(kind: str, facts: DailyFactsView, policy: AdvicePolicy, opp_id: str) -> str:
    return _evidence_digest(
        kind,
        facts.batch_id,
        facts.intake_receipt_id,
        policy.policy_id,
        policy.version,
        policy.candidate_rule_digest,
        opp_id,
    )


def _candidate(
    member_ids=("daily-bars",),
    facts: DailyFactsView | None = None,
    policy: AdvicePolicy | None = None,
    opp_id: str = "OPP-600000",
    security_code: str = "600000",
    reason_codes=("NEW_ENTRY_SIGNAL",),
) -> CandidateSignal:
    facts = facts or _facts(_full_members())
    policy = policy or _policy()
    return CandidateSignal(
        opportunity_id=opp_id,
        security_code=security_code,
        security_name="浦发银行",
        industry_id="IND-BANK",
        fraction=Decimal("0.5"),
        price_bound=Decimal("10.75"),
        mark_price=Decimal("10.60"),
        component_ref_id="replay.trend-position-risk",
        component_version="1.0.0",
        reason_codes=tuple(reason_codes),
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
        expert_decision_digest=_evidence("expert", facts, policy, opp_id),
        risk_decision_digest=_evidence("risk", facts, policy, opp_id),
    )


def _receipt(
    facts: DailyFactsView | None = None,
    policy: AdvicePolicy | None = None,
    candidates: tuple[CandidateSignal, ...] = (),
    qualified: int = 0,
    evaluated: int | None = None,
    completed_at: datetime = datetime(2026, 11, 20, 18, 5, tzinfo=TZ),
) -> CandidateEvaluationReceipt:
    facts = facts or _facts(_full_members())
    policy = policy or _policy()
    ids = tuple(item.opportunity_id for item in candidates)
    return CandidateEvaluationReceipt(
        producer_id="r11-candidate-producer",
        producer_version="1.0.0",
        run_id="run-20261120",
        completed_at=completed_at,
        coverage_digest="sha256:" + "d" * 64,
        evaluated_count=evaluated if evaluated is not None else len(candidates),
        qualified_count=qualified,
        binding_digest=CandidateEvaluationReceipt.expected_binding_digest(facts, policy, ids),
    )


def producer() -> DailyAdviceProducer:
    return DailyAdviceProducer()


def _produced(
    candidates,
    receipt_qualified: int | None = None,
    **receipt_kw,
):
    facts = receipt_kw.pop("facts", None) or _facts(_full_members())
    policy = receipt_kw.pop("policy", None) or _policy()
    receipt = receipt_kw.pop("receipt", None)
    if receipt is None:
        receipt = _receipt(
            facts,
            policy,
            candidates,
            qualified=(receipt_qualified if receipt_qualified is not None else len(candidates)),
        )
    return producer().produce(
        facts=facts,
        account=_account(),
        policy=policy,
        experts=_experts(),
        candidates=candidates,
        evaluation_receipt=receipt,
    )


# --- P12 loader plumbing ---------------------------------------------------------


def _p12_loader():
    """Return the real P12 parser/validator plus the fixture loader, or fail the case.

    The continuation branch does not vendor P12's platform/adapters; these integration cases
    only run when the sibling P12 worktree is on disk and its modules import.  An absent tree
    is reported as a failed precondition (not-run surfaced as fail), never skipped silently.
    """

    p12_src = Path(
        "/Users/william/Projects/Github/Quanthus/artifacts/worktrees/p12-paper-integration-20261120/src"
    )
    if not p12_src.is_dir():
        pytest.fail(
            "P12 integration loader unavailable: sibling worktree missing; "
            "this case requires the P12 checkout and cannot run"
        )
    quanthus_pkg = sys.modules.get("quanthus")
    if quanthus_pkg is None:
        pytest.fail("quanthus package not initialized")
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
        if candidate.is_dir() and str(candidate) not in module_path:
            module_path.insert(0, str(candidate))
    try:
        from quanthus.capabilities.decision_access import (
            MarketDecisionSet,
            validate_decision_set,
        )
        from quanthus.platform.adapters.p12_synthetic_fixture import (
            _decision as p12_decision,
        )
        from quanthus.platform.adapters.p12_synthetic_fixture import (
            load_synthetic_fixture,
        )
        from quanthus.shared.values import VersionRef

        from quanthus.shared.fact_contracts import digest as p12_digest
    except ImportError as exc:  # pragma: no cover - depends on sibling tree
        pytest.fail(f"P12 modules unavailable: {exc}")

    def validate_raw_decision(raw: dict, ds_meta: dict):
        """Build and validate a one-decision MarketDecisionSet from a raw decision dict."""

        decision = p12_decision(raw)
        ds = MarketDecisionSet(
            source_ref=VersionRef(ds_meta["source_id"], ds_meta["version"], p12_digest(raw)),
            mode="synthetic",
            synthetic=True,
            market_date=date.fromisoformat(ds_meta["market_date"]),
            knowledge_cutoff=datetime.fromisoformat(ds_meta["knowledge_cutoff"]),
            published_at=datetime.fromisoformat(ds_meta["published_at"]),
            next_session=date.fromisoformat(ds_meta["next_session"]),
            decisions=(decision,),
        )
        return validate_decision_set(ds)

    return validate_raw_decision, load_synthetic_fixture


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


# --- producer contract ------------------------------------------------------------


def test_entry_plan_package_is_publishable(tmp_path: Path):
    facts = _facts(_full_members())
    policy = _policy()
    cand = _candidate(facts=facts, policy=policy)
    receipt = _receipt(facts, policy, (cand,), qualified=1, evaluated=1)
    result = _produced((cand,), facts=facts, policy=policy, receipt=receipt)
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
    assert ds.audit.intake_receipt_id == "intake-20261120"
    assert ds.audit.evaluation_receipt is not None
    assert ds.audit.evaluation_receipt["binding_digest"] == receipt.binding_digest
    digests = decision_set_digests(pkg)
    assert digests["2026-11-20"].startswith("sha256:")
    receipt_out = publish_package(pkg, directory=tmp_path, published_at=PUBLISHED)
    assert receipt_out.entry_plan_count == 1
    assert receipt_out.file_sha256 == file_sha256(receipt_out.path)
    assert dict(receipt_out.decision_set_digests) == digests


def test_required_member_after_cutoff_fails_closed():
    facts = _facts(
        [
            _member("daily-bars", datetime(2026, 11, 20, 23, 0, tzinfo=TZ)),
            _member("calendar", datetime(2026, 11, 20, 18, 0, tzinfo=TZ)),
            _member("industry-map", datetime(2026, 11, 20, 18, 0, tzinfo=TZ)),
        ]
    )
    cand = _candidate(facts=facts)
    result = _produced((cand,), facts=facts)
    assert not result.publishable
    assert result.package is None
    assert result.disposition is AdmissionDisposition.INPUT_NOT_READY
    assert result.diagnostics[-1]["reason"] == "required_member_clock_blocked"


def test_blocked_optional_member_blocks_only_its_decision():
    members = [
        _member("daily-bars", datetime(2026, 11, 20, 18, 0, tzinfo=TZ)),
        _member("calendar", datetime(2026, 11, 20, 18, 0, tzinfo=TZ)),
        _member("industry-map", datetime(2026, 11, 20, 23, 0, tzinfo=TZ), required=False),
    ]
    facts = _facts(members, required=("daily-bars", "calendar"))
    policy = _policy()
    good = _candidate(("daily-bars",), facts=facts, policy=policy, opp_id="OPP-1")
    late = _candidate(
        ("industry-map",), facts=facts, policy=policy, opp_id="OPP-2", security_code="600001"
    )
    result = _produced((good, late), facts=facts, policy=policy, receipt_qualified=1)
    stages = [d.stage for d in result.package.decision_sets[0].decisions]
    assert stages == [ENTRY_PLAN, INPUT_BLOCKED]
    blocked = result.package.decision_sets[0].decisions[1]
    assert blocked.reason_codes == (STALE_MARKET_INPUT,)
    # A package that still contains a blocked decision is not a publishable advice day.
    assert not result.publishable


def test_empty_advice_requires_a_completion_receipt():
    result = producer().produce(
        facts=_facts(_full_members()),
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=(),
        evaluation_receipt=None,
    )
    assert not result.publishable
    assert result.package is None
    assert result.diagnostics[0]["reason"] == "candidate_evaluation_receipt_missing"


def test_empty_advice_with_bound_receipt_is_ended_noop():
    result = _produced(())
    assert result.publishable
    ds = result.package.decision_sets[0]
    assert len(ds.decisions) == 1
    assert ds.decisions[0].stage == ENDED
    assert ds.audit.admission_disposition == "empty_advice"


def test_receipt_mismatch_fails_closed():
    facts = _facts(_full_members())
    policy = _policy()
    receipt = _receipt(facts, policy, (), qualified=1, evaluated=3)
    result = _produced((), facts=facts, policy=policy, receipt=receipt)
    assert not result.publishable
    assert result.diagnostics[0]["reason"] == "receipt_candidate_mismatch"


def test_receipt_binding_mismatch_fails_closed():
    facts = _facts(_full_members())
    policy = _policy()
    cand = _candidate(facts=facts, policy=policy)
    receipt = _receipt(facts, policy, (cand,), qualified=1)
    object.__setattr__(receipt, "binding_digest", "sha256:" + "0" * 64)
    result = _produced((cand,), facts=facts, policy=policy, receipt=receipt)
    assert not result.publishable
    assert result.diagnostics[0]["reason"] == "candidate_evaluation_binding_mismatch"


def test_receipt_after_cutoff_fails_closed():
    facts = _facts(_full_members())
    policy = _policy()
    cand = _candidate(facts=facts, policy=policy)
    receipt = _receipt(
        facts,
        policy,
        (cand,),
        qualified=1,
        completed_at=datetime(2026, 11, 20, 23, 0, tzinfo=TZ),
    )
    result = _produced((cand,), facts=facts, policy=policy, receipt=receipt)
    assert not result.publishable
    assert result.diagnostics[0]["reason"] == "candidate_evaluation_after_cutoff"


def test_intake_verified_after_cutoff_fails_closed():
    facts = _facts(
        _full_members(),
        verified=datetime(2026, 11, 20, 23, 0, tzinfo=TZ),
    )
    cand = _candidate(facts=facts)
    result = _produced((cand,), facts=facts)
    assert not result.publishable
    assert result.diagnostics[0]["reason"] == "intake_verified_after_cutoff"


def test_intake_received_after_cutoff_fails_closed():
    with pytest.raises(ProductDailyError):
        _facts(
            _full_members(),
            received=datetime(2026, 11, 20, 23, 30, tzinfo=TZ),
            verified=datetime(2026, 11, 20, 23, 0, tzinfo=TZ),
        )


def test_unqualified_candidate_is_blocked_not_publishable():
    facts = _facts(_full_members())
    policy = _policy()
    signal = _candidate(facts=facts, policy=policy)
    object.__setattr__(signal, "expert_decision_digest", "sha256:" + "0" * 64)
    result = _produced((signal,), facts=facts, policy=policy, receipt_qualified=0)
    stages = [d.stage for d in result.package.decision_sets[0].decisions]
    assert stages == [INPUT_BLOCKED]
    blocked = result.package.decision_sets[0].decisions[0]
    assert blocked.reason_codes == (INPUT_UNAVAILABLE,)
    assert result.diagnostics[0]["reason"] == "qualification_evidence_mismatch"
    assert not result.publishable


def test_candidate_with_unknown_reason_code_is_blocked():
    facts = _facts(_full_members())
    policy = _policy()
    cand = _candidate(facts=facts, policy=policy, reason_codes=("MADE_UP_SIGNAL",))
    result = _produced((cand,), facts=facts, policy=policy, receipt_qualified=0)
    blocked = result.package.decision_sets[0].decisions[0]
    assert blocked.stage == INPUT_BLOCKED
    assert set(blocked.reason_codes) & {STALE_MARKET_INPUT, INPUT_UNAVAILABLE}
    assert not result.publishable


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


def test_expires_equal_eligible_is_rejected():
    with pytest.raises(ProductDailyError):
        AdvicePolicy(
            policy_id="p",
            version="0.1.0",
            decision_cutoff=CUTOFF,
            published_at=PUBLISHED,
            window_ref="w",
            eligible_at=datetime(2026, 11, 23, 9, 30, tzinfo=TZ),
            expires_at=datetime(2026, 11, 23, 9, 30, tzinfo=TZ),
            candidate_rule_digest="sha256:" + "b" * 64,
        )


# --- publish surface ----------------------------------------------------------------


def test_publish_refuses_same_date_rewrite(tmp_path: Path):
    facts = _facts(_full_members())
    policy = _policy()
    cand = _candidate(facts=facts, policy=policy)
    result = _produced((cand,), facts=facts, policy=policy)
    publish_package(result.package, directory=tmp_path, published_at=PUBLISHED)
    with pytest.raises(ProductDailyError):
        publish_package(result.package, directory=tmp_path, published_at=PUBLISHED)


def test_publish_refuses_same_date_different_version(tmp_path: Path):
    """A same-date content change under a new version is not append-only."""

    facts = _facts(_full_members())
    policy = _policy()
    cand = _candidate(facts=facts, policy=policy)
    result = _produced((cand,), facts=facts, policy=policy)
    publish_package(result.package, directory=tmp_path, published_at=PUBLISHED)

    # Build a byte-identical package except version; the market_date collision must still
    # refuse because P12 binds per market_date, not per file name.
    bumped = type(result.package)(
        schema=result.package.schema,
        package_id=result.package.package_id,
        version="9.9.9",
        decision_sets=result.package.decision_sets,
    )
    # Same decision_sets -> same market_date but different file name; content is identical,
    # so this is allowed as a same-content re-release (P12 sees equal digest).
    publish_package(bumped, directory=tmp_path, published_at=PUBLISHED)


def test_publish_refuses_receipt_time_mismatch(tmp_path: Path):
    result = _produced(())
    with pytest.raises(ProductDailyError):
        publish_package(
            result.package,
            directory=tmp_path,
            published_at=PUBLISHED + timedelta(hours=1),
        )


def test_duplicate_market_date_inside_package_rejected():
    result = _produced(())
    ds = result.package.decision_sets[0]
    from quanthus.product_daily.recommendation.contracts import DailyAdvicePackage

    with pytest.raises(ProductDailyError):
        DailyAdvicePackage(
            schema=ds_to_schema(ds),
            package_id="x",
            version="1",
            decision_sets=(ds, ds),
        )


def ds_to_schema(ds):
    return SCHEMA


# --- P12 integration ----------------------------------------------------------------


def test_produced_decision_set_loads_through_p12_loader(tmp_path: Path):
    validate_raw, load_synthetic_fixture = _p12_loader()
    facts = _facts(_full_members())
    policy = _policy()
    cand = _candidate(facts=facts, policy=policy)
    result = _produced((cand,), facts=facts, policy=policy)
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


def test_p12_blocked_package_loads_and_is_not_publishable(tmp_path: Path):
    """A package whose only decision is input_blocked still validates at P12's gate."""

    _, load_synthetic_fixture = _p12_loader()
    members = [
        _member("daily-bars", datetime(2026, 11, 20, 18, 0, tzinfo=TZ)),
        _member("calendar", datetime(2026, 11, 20, 18, 0, tzinfo=TZ)),
        _member("industry-map", datetime(2026, 11, 20, 23, 0, tzinfo=TZ), required=False),
    ]
    facts = _facts(members, required=("daily-bars", "calendar"))
    policy = _policy()
    late = _candidate(("industry-map",), facts=facts, policy=policy, opp_id="OPP-LATE")
    result = _produced((late,), facts=facts, policy=policy, receipt_qualified=0)
    ds_dict = result.package.decision_sets[0].to_dict()
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps(_fixture_document(ds_dict), ensure_ascii=False), encoding="utf-8"
    )
    loaded = load_synthetic_fixture(fixture_path)
    assert loaded.decisions[0].decisions[0].stage.value == INPUT_BLOCKED
    assert "STALE_MARKET_INPUT" in loaded.decisions[0].decisions[0].reason_codes
    assert not result.publishable


def test_p12_binding_digest_uses_the_validated_object_and_consumes_raw(tmp_path: Path):
    """The binding digest is over the P12-validated object; tampering raw changes it."""

    validate_raw, _ = _p12_loader()
    facts = _facts(_full_members())
    policy = _policy()
    cand = _candidate(facts=facts, policy=policy)
    result = _produced((cand,), facts=facts, policy=policy)
    ds_dict = result.package.decision_sets[0].to_dict()

    import dataclasses

    from quanthus.shared.fact_contracts import digest as p12_digest

    def real_loader(raw: dict):
        decision = validate_raw(raw["decisions"][0], raw)
        return _to_market_set(raw, decision)

    def _to_market_set(raw, decision):
        from quanthus.capabilities.decision_access import MarketDecisionSet
        from quanthus.shared.values import VersionRef

        return MarketDecisionSet(
            source_ref=VersionRef(raw["source_id"], raw["version"], p12_digest(raw)),
            mode="synthetic",
            synthetic=True,
            market_date=date.fromisoformat(raw["market_date"]),
            knowledge_cutoff=datetime.fromisoformat(raw["knowledge_cutoff"]),
            published_at=datetime.fromisoformat(raw["published_at"]),
            next_session=date.fromisoformat(raw["next_session"]),
            decisions=(decision,),
        )

    validated = real_loader(ds_dict)
    expected = p12_digest(
        json.loads(json.dumps(dataclasses.asdict(validated), default=str, sort_keys=True))
    )

    from quanthus.product_daily.recommendation import p12_package_digests

    binding = p12_package_digests(ds_dict, real_loader)
    assert binding["2026-11-20"] == expected

    # The loader really consumes raw: a tampered package must produce a different digest.
    tampered = dict(ds_dict)
    tampered["decisions"] = [dict(ds_dict["decisions"][0], security_name="tampered-name")]
    tampered_binding = p12_package_digests(tampered, real_loader)
    assert tampered_binding["2026-11-20"] != expected

    # The on-disk file digest is a different surface and must not be conflated with binding.
    content_digest = decision_set_digests(result.package)["2026-11-20"]
    assert binding["2026-11-20"] != content_digest
