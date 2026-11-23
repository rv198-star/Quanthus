'''P12 D1-B batch A: daily advice runtime end-to-end tests.'''

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from quanthus.p12_daily.daily_job import run_daily
from quanthus.p12_daily.decision_loader import (
    INPUT_BLOCKED_IN_PACKAGE,
    load_decision_set_dict,
)
from quanthus.p12_daily.package_source import PublishedPackageSource
from quanthus.p12_daily.package_store import (
    PACKAGE_EXISTS_DIFFERS,
    DailyPackageStore,
)
from quanthus.p12_daily.rule_runner import (
    NO_QUALIFIED_CANDIDATE_ID,
    HeldPositionFact,
    SyntheticRuleRunner,
)
from quanthus.shared.fact_contracts import FactError
from quanthus.shared.values import AccountScope

TZ = timezone(timedelta(hours=8))
MARKET_DATE = date(2026, 11, 20)
NEXT_SESSION = date(2026, 11, 21)
BASE_SHA = "sha256:" + "1" * 64
CUTOFF = datetime(2026, 11, 20, 22, 30, tzinfo=TZ)


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def make_product(job_root: Path, market_date: str, rows: int = 5213) -> dict:
    '''Materialize the CN02 base24 pointer layout under a local replay root.'''

    core = job_root / (f"{market_date}-20261120T000000Z") / "core"
    payload = core / "payload"
    payload.mkdir(parents=True, exist_ok=True)
    (payload / "1d.parquet").write_bytes(b"PAR1-fake-daily-bars")
    manifest = {
        "kind": "cn02_base24_core_v1",
        "package_id": f"core-{market_date}",
        "market_date": market_date,
        "base": {"manifest_sha256": BASE_SHA, "content_digest": "sha256:" + "2" * 64},
        "row_counts": {"dwd_security_bar": rows, "dwd_trading_calendar": 3},
        "files": [
            {
                "path": "payload/1d.parquet",
                "sha256": _sha(b"PAR1-fake-daily-bars"),
                "size_bytes": len(b"PAR1-fake-daily-bars"),
            },
        ],
        "content_digest": "sha256:" + "3" * 64,
        "completed_at": f"{market_date}T18:01:06+08:00",
        "consumer_admission": "NOT_GRANTED",
        # M2 F2: the governed calendar member declares the authoritative next
        # open session at this cutoff; the job never guesses the date.
        "calendar": {"next_session": "2026-11-21"},
        # M2 section 3: the three independent history floors, each separately
        # satisfied by the observed sessions this input supplies.
        "history": {
            "event_window_sessions": {"observed_sessions": 30},
            "feature_warmup_sessions": {"observed_sessions": 80},
            "listing_sessions": {"observed_sessions": 200},
        },
        # M2 F11: corporate-action/adjustment fields carried verbatim; the
        # manifest attests they are all present and mappable.
        "corporate_actions": {
            "fields_complete": True,
            "cash_dividend_per_share": "0.00",
            "bonus_share_ratio": "0.00",
            "exceptions": [],
        },
    }
    mb = json.dumps(manifest, sort_keys=True).encode()
    (core / "manifest.json").write_bytes(mb)
    core_sha = _sha(mb)

    index = {"dates": {market_date: {"core": str(core), "core_manifest_sha256": core_sha,
                                   "market_date": market_date}}}
    ib = json.dumps(index, sort_keys=True).encode()
    (job_root / "DATES.json").write_bytes(ib)
    index_sha = _sha(ib)

    entry = {
        "kind": "cn02_base24_daily_cycle_v1",
        "market_date": market_date,
        "core": str(core),
        "core_manifest_sha256": core_sha,
        "base_manifest_sha256": BASE_SHA,
        "core_row_counts": manifest["row_counts"],
        "completed_at": manifest["completed_at"],
        "consumer_admission": "NOT_GRANTED",
        "status": "QUALIFIED_1D_NATIVE5M",
        "row_counts": {"1d": rows, "5m": 250224},
    }
    pointer = {
        "kind": "cn02_base24_daily_cycle_v1",
        "selected_scope": "core",
        "market_date": market_date,
        "consumer_admission": "NOT_GRANTED",
        "index_sha256": index_sha,
        "entry": entry,
    }
    (job_root / "CURRENT_DAILY_CORE_BASE24.json").write_text(
        json.dumps(pointer, indent=1)
    )
    return {"core_sha": core_sha, "index_sha": index_sha, "core": core}


def one_candidate(**overrides):
    base = {
        "security_code": "600000",
        "security_name": "浦发银行",
        "mark_price": "10.60",
        "price_bound": "10.75",
        "fraction": "0.5",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def env(tmp_path):
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    meta = make_product(prod, MARKET_DATE.isoformat())
    return prod, state, meta


def test_publish_and_idempotent_rerun(env):
    prod, state, _ = env
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    now = datetime(2026, 11, 20, 22, 31, tzinfo=TZ)
    r1 = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=now,
        decision_cutoff=CUTOFF, holdings=(),
    )
    assert r1["status"] == "PUBLISHED"
    assert r1["package"]["entry_plan_count"] == 1
    r2 = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=now,
        decision_cutoff=CUTOFF, holdings=(),
    )
    assert r2["status"] == "ALREADY_PUBLISHED"
    assert r2["package"]["decision_set_digest"] == r1["package"]["decision_set_digest"]
    assert len(list((state / "daily-packages").glob("*.json"))) == 1


def test_same_day_different_content_refused(env):
    prod, state, _ = env
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    now = datetime(2026, 11, 20, 22, 31, tzinfo=TZ)
    run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=now,
        decision_cutoff=CUTOFF, holdings=(),
    )
    store = DailyPackageStore(state)
    with pytest.raises(FactError) as exc:
        store.publish(
            decision_set={"market_date": MARKET_DATE.isoformat(), "source_id": "x", "version": "y"},
            runner={}, input_evidence={}, published_at=now,
        )
    assert exc.value.reason_code == PACKAGE_EXISTS_DIFFERS


def test_input_blocked_writes_receipt_only(tmp_path):
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(),
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert receipt["input_block"]["reason_code"] == "INPUT_UNAVAILABLE"


def test_stale_input_blocked(env):
    prod, state, _ = env
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=date(2026, 11, 21), next_session=date(2026, 11, 22),
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 21, 20, 0, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(),
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert receipt["input_block"]["reason_code"] == "STALE_MARKET_INPUT"


def test_wrong_kind_blocked(tmp_path):
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    pointer = json.loads((prod / "CURRENT_DAILY_CORE_BASE24.json").read_text())
    pointer["selected_scope"] = "full"
    (prod / "CURRENT_DAILY_CORE_BASE24.json").write_text(json.dumps(pointer))
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(),
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert "selected_scope" in receipt["input_block"]["detail"]


def test_no_qualified_candidate_is_noop_package(env):
    prod, state, _ = env
    runner = SyntheticRuleRunner(candidates=())
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(),
    )
    assert receipt["status"] == "PUBLISHED"
    assert receipt["package"]["entry_plan_count"] == 0
    assert receipt["package"]["admission_disposition"] == "empty_advice"
    package = DailyPackageStore(state).package_for(MARKET_DATE)
    decisions = package.decision_set["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["opportunity_id"] == NO_QUALIFIED_CANDIDATE_ID
    assert decisions[0]["stage"] == "ended"
    assert decisions[0]["security_code"] == "000000"


def test_single_security_exclusion(env):
    prod, state, _ = env
    runner = SyntheticRuleRunner(
        candidates=(
            one_candidate(),
            one_candidate(
                security_code="000001",
                security_name="平安银行",
                unqualified_reason={
                    "reason_code": "STALE_MARKET_INPUT",
                    "detail": "member evidence for 000001 not qualified",
                },
            ),
        )
    )
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(),
    )
    assert receipt["status"] == "PUBLISHED"
    assert receipt["package"]["entry_plan_count"] == 1
    assert receipt["exclusions"][0]["security_code"] == "000001"


def test_held_security_not_reentered(env):
    prod, state, _ = env
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(HeldPositionFact(security_code="600000", quantity=500),),
    )
    assert receipt["package"]["entry_plan_count"] == 0


def test_package_source_and_retraction(env):
    prod, state, _ = env
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    now = datetime(2026, 11, 20, 22, 31, tzinfo=TZ)
    run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=now,
        decision_cutoff=CUTOFF, holdings=(),
    )
    def clock():
        return datetime(2026, 11, 21, 10, 0, tzinfo=TZ)
    source = PublishedPackageSource(
        state,
        clock,
        mode="synthetic",
        next_session_provider=lambda _today: NEXT_SESSION,
    )
    scope = AccountScope("SYN-PAPER-A", "synthetic")
    current = source.current(scope)
    assert current.market_date == MARKET_DATE
    assert len(current.decisions) == 1
    assert source.is_retracted(MARKET_DATE) is False
    DailyPackageStore(state).retract(
        market_date=MARKET_DATE,
        reason="manifest drift detected post-publish",
        retracted_at=datetime(2026, 11, 21, 8, 0, tzinfo=TZ),
        actor="test",
    )
    assert source.is_retracted(MARKET_DATE) is True
    stored = source.stored_for(MARKET_DATE)
    assert stored.retracted and stored.retraction["reason"].startswith("manifest")


def test_loader_refuses_input_blocked_decision():
    raw = {
        "source_id": "p12-daily-advice",
        "version": "v1",
        "market_date": "2026-11-20",
        "knowledge_cutoff": "2026-11-20T15:00:00+08:00",
        "published_at": "2026-11-20T20:00:00+08:00",
        "next_session": "2026-11-21",
        "decisions": [
            {
                "opportunity_id": "input-blocked",
                "security_code": "000000",
                "security_name": "blocked",
                "stage": "input_blocked",
                "component": {"ref_id": "x", "version": "1"},
                "reason_codes": ["INPUT_UNAVAILABLE"],
                "explanations": [],
                "next_observation": None,
            }
        ],
    }
    with pytest.raises(FactError) as exc:
        load_decision_set_dict(raw, mode="synthetic")
    assert exc.value.reason_code == INPUT_BLOCKED_IN_PACKAGE


def test_wire_dict_loads_through_same_loader(env):
    prod, state, _ = env
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(),
    )
    stored = DailyPackageStore(state).package_for(MARKET_DATE)
    loaded = load_decision_set_dict(stored.decision_set, mode="synthetic")
    assert loaded.market_date == MARKET_DATE
    assert loaded.decisions[0].entry is not None


def test_pending_source_when_no_package(tmp_path):
    state = tmp_path / "state"
    def clock():
        return datetime(2026, 11, 20, 22, 31, tzinfo=TZ)
    source = PublishedPackageSource(
        state,
        clock,
        mode="synthetic",
        next_session_provider=lambda _today: NEXT_SESSION,
    )
    scope = AccountScope("SYN-PAPER-A", "synthetic")
    pending = source.current(scope)
    assert pending.source_ref.version == "v1"
    assert pending.decisions == ()
    assert source.latest_run() is None


def test_history_gate_failure_blocks(tmp_path):
    """A history floor the input cannot prove blocks the whole day (M2 S3)."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    meta = make_product(prod, MARKET_DATE.isoformat())
    # Weaken the feature-warmup gate below its 61-session floor.
    manifest_path = meta["core"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["history"]["feature_warmup_sessions"] = {"observed_sessions": 40}
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    # re-hash manifest into the entry/index so the pointer still verifies
    core_sha = _sha(manifest_path.read_bytes())
    entry_dir = prod / "DATES.json"
    index = json.loads(entry_dir.read_text())
    index["dates"][MARKET_DATE.isoformat()]["core_manifest_sha256"] = core_sha
    entry_dir.write_text(json.dumps(index, sort_keys=True))
    pointer = json.loads((prod / "CURRENT_DAILY_CORE_BASE24.json").read_text())
    pointer["index_sha256"] = _sha(entry_dir.read_bytes())
    pointer["entry"]["core_manifest_sha256"] = core_sha
    (prod / "CURRENT_DAILY_CORE_BASE24.json").write_text(json.dumps(pointer))
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(),
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert receipt["input_block"]["missing_members"] == ["history.feature_warmup_61"]


def _reshash_manifest(prod, market_date, mutate):
    """Rewrite a manifest member and re-hash it through index and pointer."""
    meta = make_product(prod, market_date)
    manifest_path = meta["core"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    mutate(manifest)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    core_sha = _sha(manifest_path.read_bytes())
    index_path = prod / "DATES.json"
    index = json.loads(index_path.read_text())
    index["dates"][market_date]["core_manifest_sha256"] = core_sha
    index_path.write_text(json.dumps(index, sort_keys=True))
    pointer_path = prod / "CURRENT_DAILY_CORE_BASE24.json"
    pointer = json.loads(pointer_path.read_text())
    pointer["index_sha256"] = _sha(index_path.read_bytes())
    pointer["entry"]["core_manifest_sha256"] = core_sha
    pointer_path.write_text(json.dumps(pointer))


def _run_and_receipt(state, prod, candidates=None):
    runner = SyntheticRuleRunner(
        candidates=candidates if candidates is not None else (one_candidate(),)
    )
    return run_daily(
        state_dir=state,
        job_root=prod,
        runner=runner,
        market_date=MARKET_DATE,
        next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A",
        now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(),
    )


def test_history_feature_warmup_valid_bar_gap_blocks(tmp_path):
    """R8: a 60-day warmup window missing one valid bar is explicit evidence.

    The member reaches the 61-session count but carries an evidence gap reason
    (one valid bar absent in the recent window); the gate fails closed and the
    reason is preserved on the run receipt."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"

    def mutate(manifest):
        manifest["history"]["feature_warmup_sessions"] = {
            "observed_sessions": 80,
            "unavailable_reason": (
                "近 60 日特征窗口缺一条有效 bar，证据缺口未修复"
            ),
        }

    _reshash_manifest(prod, MARKET_DATE.isoformat(), mutate)
    receipt = _run_and_receipt(state, prod)
    assert receipt["status"] == "INPUT_BLOCKED"
    assert "history.feature_warmup_61" in receipt["input_block"]["missing_members"]
    gate = next(
        g
        for g in receipt["history_gates"]
        if g["gate_id"] == "history.feature_warmup_61"
    )
    assert gate["observed_sessions"] == 80
    assert "有效 bar" in (gate["unavailable_reason"] or "")


def test_history_listing_floor_corporate_action_anomaly_blocks(tmp_path):
    """R8: a corporate-action anomaly inside the 61-day window is evidence.

    The listing floor reaches 120 sessions but the member records a recent
    corporate-action anomaly; the gate fails closed and the reason rides the
    receipt rather than being dropped."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"

    def mutate(manifest):
        manifest["history"]["listing_sessions"] = {
            "observed_sessions": 200,
            "unavailable_reason": (
                "近 61 日存在公司行为异常，上市满 120 日仍需按成员证据阻断"
            ),
        }

    _reshash_manifest(prod, MARKET_DATE.isoformat(), mutate)
    receipt = _run_and_receipt(state, prod)
    assert receipt["status"] == "INPUT_BLOCKED"
    assert "history.listing_floor_120" in receipt["input_block"]["missing_members"]
    gate = next(
        g
        for g in receipt["history_gates"]
        if g["gate_id"] == "history.listing_floor_120"
    )
    assert gate["observed_sessions"] == 200
    assert "公司行为异常" in (gate["unavailable_reason"] or "")


def test_missing_calendar_blocks(tmp_path):
    """Without the governed calendar member the run cannot fix next_session."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    meta = make_product(prod, MARKET_DATE.isoformat())
    manifest_path = meta["core"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["calendar"]
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    core_sha = _sha(manifest_path.read_bytes())
    index = json.loads((prod / "DATES.json").read_text())
    index["dates"][MARKET_DATE.isoformat()]["core_manifest_sha256"] = core_sha
    (prod / "DATES.json").write_text(json.dumps(index, sort_keys=True))
    pointer = json.loads((prod / "CURRENT_DAILY_CORE_BASE24.json").read_text())
    pointer["index_sha256"] = _sha((prod / "DATES.json").read_bytes())
    pointer["entry"]["core_manifest_sha256"] = core_sha
    (prod / "CURRENT_DAILY_CORE_BASE24.json").write_text(json.dumps(pointer))
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(),
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert "trading_calendar" in receipt["input_block"]["missing_members"]


def test_missing_corporate_actions_blocks(tmp_path):
    """M2 F11: absent corporate-action fields block the input, never default."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    meta = make_product(prod, MARKET_DATE.isoformat())
    manifest_path = meta["core"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["corporate_actions"]
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    core_sha = _sha(manifest_path.read_bytes())
    index = json.loads((prod / "DATES.json").read_text())
    index["dates"][MARKET_DATE.isoformat()]["core_manifest_sha256"] = core_sha
    (prod / "DATES.json").write_text(json.dumps(index, sort_keys=True))
    pointer = json.loads((prod / "CURRENT_DAILY_CORE_BASE24.json").read_text())
    pointer["index_sha256"] = _sha((prod / "DATES.json").read_bytes())
    pointer["entry"]["core_manifest_sha256"] = core_sha
    (prod / "CURRENT_DAILY_CORE_BASE24.json").write_text(json.dumps(pointer))
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(),
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert "corporate_actions" in receipt["input_block"]["missing_members"]


def _copy_fixture_sidecar(tmp_path, name):
    src = Path(
        "/Users/william/Documents/Codex/2026-11-24/"
        "quanthus-p10-daily-sidecar-v1/fixtures/synthetic"
    ) / name
    if not src.is_file():
        return None
    dest = tmp_path / name
    dest.write_bytes(src.read_bytes())
    return dest


def test_sidecar_publish_records_consumer_stamps(tmp_path):
    """A read sidecar yields per-member received/first_read/verified stamps."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    sidecar = _copy_fixture_sidecar(tmp_path, "sidecar.v1.json")
    if sidecar is None:
        pytest.skip("P10 sidecar fixture not present")
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(), sidecar_path=sidecar,
    )
    # The synthetic fixture's own clocks are 2000-dated; the run still records
    # what P12 actually read (the consumer receipt), and TEST_ONLY disposition
    # does not itself block publication of the synthetic package.
    assert receipt["status"] == "PUBLISHED"
    assert receipt["consumer_receipt"] is not None
    members = receipt["consumer_receipt"]["members"]
    assert members and all(m["received_at"] for m in members)
    assert members and all(m["first_read_at"] for m in members)
    assert members and all(m["verified_at"] for m in members)


def test_sidecar_absent_published_at_null(tmp_path):
    """No sidecar: the receipt's supply-side publish time stays null."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(),
    )
    assert receipt["status"] == "PUBLISHED"
    assert receipt["consumer_receipt"] is None
    assert receipt["source_published_at"] is None


def test_sidecar_withdraw_does_not_replace(tmp_path):
    """A WITHDRAW event is recorded but never republishes the same day."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    sidecar = _copy_fixture_sidecar(tmp_path, "withdrawal.v1.json")
    if sidecar is None:
        pytest.skip("P10 sidecar fixture not present")
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(), sidecar_path=sidecar,
    )
    # Source withdraw recorded on the receipt; the day still publishes once
    # (frozen decision b: source revise/withdraw never becomes a same-day
    # replacement advice package).
    assert receipt["status"] == "PUBLISHED"
    assert receipt["consumer_receipt"]["event"] == "WITHDRAW"



def test_migration_rehearsal_preserves_ledger_surfaces(tmp_path):
    """Moving a bound paper store to the daily-package source keeps the ledger.

    The rehearsal copies the store, rebinds the copy to PublishedPackageSource
    and compares every ledger surface; the original stays byte-identical.
    """

    from quanthus.p12_daily.migration import rehearse
    from quanthus.platform.adapters.p12_synthetic_fixture import (
        load_synthetic_fixture,
    )
    from quanthus.platform.p12.paper_fixture import (
        PAPER_ACCOUNT_ID,
        PAPER_FACTS_STORE,
        build_paper_application,
    )
    from quanthus.policy import find_project_root
    from quanthus.product_daily.application.models import (
        SyntheticRuntimeAuthorization,
    )

    fixture = load_synthetic_fixture(
        find_project_root() / "config/p12/synthetic-desktop.v1.json"
    )
    auth = SyntheticRuntimeAuthorization("local-paper:test", frozenset({PAPER_ACCOUNT_ID}))
    now = lambda: datetime(2026, 11, 20, 22, 31, tzinfo=TZ)  # noqa: E731

    # Build the account on the legacy empty paper-decisions source and buy.
    src_dir = tmp_path / "src_state"
    src_dir.mkdir()
    app = build_paper_application(
        state_dir=src_dir, policy=fixture.policy, authorization=auth, now=now
    )
    # The baseline projection is recorded through the application surface.
    projection = app.paper_projection()
    assert projection.cash is not None

    source_store = src_dir / PAPER_FACTS_STORE
    assert source_store.is_file()

    # open_source_app keeps the store's existing binding (no rebind, so the
    # before surfaces are read without migrating); open_migrated_app binds the
    # daily-package source so the recorded migration runs on the copy only.
    pkg_state = tmp_path / "pkg_state"
    DailyPackageStore(pkg_state)  # ensure empty store exists for the source

    def open_source_app(state_dir):
        return build_paper_application(
            state_dir=state_dir,
            policy=fixture.policy,
            authorization=auth,
            now=now,
        )

    def open_migrated_app(state_dir):
        return build_paper_application(
            state_dir=state_dir,
            policy=fixture.policy,
            authorization=auth,
            now=now,
            decision_source=PublishedPackageSource(
                pkg_state, now, mode="synthetic",
                account_ids=frozenset({PAPER_ACCOUNT_ID}),
                next_session_provider=lambda _today: NEXT_SESSION,
            ),
        )

    report = rehearse(
        source_store=source_store,
        rehearsal_dir=tmp_path / "rehearsal",
        open_source_app=open_source_app,
        open_migrated_app=open_migrated_app,
    )
    assert report.source_intact is True
    assert report.migration_record is not None
    assert report.migration_record["record"]["kind"] == "decision_source_migration"
    assert report.migration_record["record"]["from"]["ref_id"] == "p12-paper-decisions"
    # Cash, positions, buckets, orders and receipts all carry across unchanged.
    assert report.consistent is True
    assert all(report.surfaces.values())


def test_blocked_pending_placeholder_keeps_account_open(tmp_path):
    '''A daily source with no package and no authoritative calendar must not
    keep the paper account from opening.

    The real base24 product publishes no calendar.next_session member; on a
    host where the governed calendar cannot be resolved, _pending_set fails
    closed with INPUT_UNAVAILABLE.  bind_configuration / establish_baseline
    then bind to the pending source identity with an empty package set, and
    workbench renders a NOT_YET_PUBLISHED page plus the run receipt instead
    of crashing the whole server.
    '''
    from quanthus.platform.adapters.p12_synthetic_fixture import (
        load_synthetic_fixture,
    )
    from quanthus.platform.p12.paper_fixture import (
        PAPER_ACCOUNT_ID,
        build_paper_application,
    )
    from quanthus.policy import find_project_root
    from quanthus.product_daily.application.models import (
        SyntheticRuntimeAuthorization,
    )

    fixture = load_synthetic_fixture(
        find_project_root() / "config/p12/synthetic-desktop.v1.json"
    )
    auth = SyntheticRuntimeAuthorization("local-paper:test", frozenset({PAPER_ACCOUNT_ID}))
    now = lambda: datetime(2026, 11, 24, 10, 0, tzinfo=TZ)  # noqa: E731

    pkg_state = tmp_path / "pkg_state"
    DailyPackageStore(pkg_state)
    # No next_session_provider: the real CN02 manifest carries no
    # calendar.next_session, so the placeholder cannot resolve a session.
    source = PublishedPackageSource(pkg_state, now, mode="synthetic")

    app_dir = tmp_path / "app_state"
    app_dir.mkdir()
    app = build_paper_application(
        state_dir=app_dir,
        policy=fixture.policy,
        authorization=auth,
        now=now,
        decision_source=source,
    )
    binding = app.bind_configuration()
    assert binding.binding["decision_source"]["ref_id"] == "p12-daily-advice"

    scope = AccountScope(PAPER_ACCOUNT_ID, "synthetic")
    view = app.workbench(scope)
    assert view.package is None
    assert view.publication.status == "NOT_YET_PUBLISHED"
    assert "INPUT_UNAVAILABLE" in view.action_blockers



def test_trend_observation_bound_to_package_and_receipt(tmp_path):
    """M2 F10: a P11 trend observation lands on the decision and the receipt."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    cand = one_candidate(
        trend_observation={
            "observation_digest": "sha256:trend-1",
            "applicability": "APPLIED",
            "raw_value": "0.83",
        }
    )
    runner = SyntheticRuleRunner(candidates=(cand,))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(),
    )
    assert receipt["status"] == "PUBLISHED"
    obs = receipt["trend_observations"][0]
    assert obs["observation_digest"] == "sha256:trend-1"
    # The trend identity stays on the run receipt audit, not on the shared
    # MarketDecisionSet wire shape - the decision carries no trend field.
    stored = DailyPackageStore(state).package_for(MARKET_DATE)
    loaded = load_decision_set_dict(stored.decision_set, mode="synthetic")
    codes = [e.code for d in loaded.decisions for e in d.explanations]
    assert "TREND_OBSERVATION" not in codes


def test_abstained_trend_observation_kept(tmp_path):
    """An abstained observation is still recorded and bound, not dropped."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    cand = one_candidate(
        trend_observation={
            "observation_digest": "sha256:trend-abstained",
            "applicability": "ABSTAINED",
            "raw_value": None,
        }
    )
    runner = SyntheticRuleRunner(candidates=(cand,))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(),
    )
    obs = receipt["trend_observations"][0]
    assert obs["applicability"] == "ABSTAINED"
    assert obs["raw_value"] is None
    assert obs["observation_digest"] == "sha256:trend-abstained"



def test_missing_decision_cutoff_blocks(tmp_path):
    """A run without a configured tz-aware decision cutoff is a whole-day block."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        holdings=(), decision_cutoff=None,
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert "decision_cutoff" in receipt["input_block"]["missing_members"]


def test_source_completed_after_close_still_publishes(tmp_path):
    """Source data finished after the 15:00 close is normal - 15:00 is only the
    observation anchor, never the knowledge cutoff.  The package knowledge
    cutoff stays the configured 22:30 decision cutoff."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    meta = make_product(prod, MARKET_DATE.isoformat())
    # Give the source a knowledge_cutoff and completed_at after the 15:00 close.
    manifest_path = meta["core"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["knowledge_cutoff"] = f"{MARKET_DATE.isoformat()}T18:00:00+08:00"
    manifest["completed_at"] = f"{MARKET_DATE.isoformat()}T19:30:00+08:00"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    core_sha = _sha(manifest_path.read_bytes())
    index = json.loads((prod / "DATES.json").read_text())
    index["dates"][MARKET_DATE.isoformat()]["core_manifest_sha256"] = core_sha
    (prod / "DATES.json").write_text(json.dumps(index, sort_keys=True))
    pointer = json.loads((prod / "CURRENT_DAILY_CORE_BASE24.json").read_text())
    pointer["index_sha256"] = _sha((prod / "DATES.json").read_bytes())
    pointer["entry"]["core_manifest_sha256"] = core_sha
    (prod / "CURRENT_DAILY_CORE_BASE24.json").write_text(json.dumps(pointer))
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        holdings=(), decision_cutoff=CUTOFF,
    )
    assert receipt["status"] == "PUBLISHED"
    # completed_at is recorded verbatim, source knowledge_cutoff verbatim; the
    # package knowledge cutoff is the configured 22:30 decision cutoff and the
    # package published_at is never earlier than it.
    assert receipt["input_timing"]["completed_at"] == "2026-11-20T19:30:00+08:00"
    assert receipt["input_timing"]["source_knowledge_cutoff"] == "2026-11-20T18:00:00+08:00"
    stored = DailyPackageStore(state).package_for(MARKET_DATE)
    loaded = load_decision_set_dict(stored.decision_set, mode="synthetic")
    assert loaded.knowledge_cutoff == CUTOFF
    assert loaded.published_at >= loaded.knowledge_cutoff


def _write_volume_member(meta, volume_member):
    """Attach a volume member to the core manifest and rebind its hashes."""
    manifest_path = meta["core"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["volume"] = volume_member
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    core_sha = _sha(manifest_path.read_bytes())
    prod = meta["core"].parent.parent
    index = json.loads((prod / "DATES.json").read_text())
    index["dates"][MARKET_DATE.isoformat()]["core_manifest_sha256"] = core_sha
    (prod / "DATES.json").write_text(json.dumps(index, sort_keys=True))
    pointer = json.loads((prod / "CURRENT_DAILY_CORE_BASE24.json").read_text())
    pointer["index_sha256"] = _sha((prod / "DATES.json").read_bytes())
    pointer["entry"]["core_manifest_sha256"] = core_sha
    (prod / "CURRENT_DAILY_CORE_BASE24.json").write_text(json.dumps(pointer))


def test_volume_scope_verbatim_full_day(env):
    """M2 v3: a declared FULL_DAY scope with its observation end is carried
    verbatim onto the receipt - the rule judges it, P12 does not rewrite it."""
    prod, state, meta = env
    _write_volume_member(meta, {
        "volume_scope": "FULL_DAY",
        "full_day_volume_amount_observation_end": "2026-11-20T15:30:00+08:00",
    })
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF, holdings=(),
    )
    assert receipt["status"] == "PUBLISHED"
    timing = receipt["input_timing"]
    assert timing["volume_scope"] == "FULL_DAY"
    assert timing["full_day_volume_amount_observation_end"] == "2026-11-20T15:30:00+08:00"
    # The 15:30 session anchor and the source-declared observation end are
    # separate fields; 15:00 stays only the price-close anchor.
    assert timing["price_close_anchor"].endswith("T15:00:00+08:00")
    assert timing["full_day_volume_amount_end"].endswith("T15:30:00+08:00")


def test_volume_scope_unknown_never_promoted(env):
    """M2 v3: an UNKNOWN scope and a missing observation end stay UNKNOWN;
    P12 does not promote it and hands the verdict to the rule."""
    prod, state, meta = env
    _write_volume_member(meta, {"volume_scope": "UNKNOWN"})
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF, holdings=(),
    )
    assert receipt["status"] == "PUBLISHED"
    timing = receipt["input_timing"]
    assert timing["volume_scope"] == "UNKNOWN"
    assert timing["full_day_volume_amount_observation_end"] is None


def test_volume_scope_full_day_without_end_keeps_scope_and_flags_breach(env):
    """Audit R1: a declared FULL_DAY scope stays verbatim even when its
    observation end is missing - the breach and evidence gap are recorded
    separately, the scope is never rewritten to UNKNOWN and never proven by
    the 15:30 anchor."""
    prod, state, meta = env
    _write_volume_member(meta, {"volume_scope": "FULL_DAY"})
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF, holdings=(),
    )
    timing = receipt["input_timing"]
    assert timing["volume_scope"] == "FULL_DAY"
    assert timing["full_day_volume_amount_observation_end"] is None
    assert timing["volume_member_breach"] is not None
    assert "FULL_DAY" in timing["volume_member_breach"]


def test_run_seam_evaluation_receipt_transported(env):
    """Run-seam: the synthetic runner mints its own evaluation receipt and
    source closure in the same run; P12 transports them verbatim onto the run
    receipt and the package input evidence, never constructing them."""
    prod, state, _ = env
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF, holdings=(), account_mode="synthetic",
    )
    assert receipt["status"] == "PUBLISHED"
    er = receipt["evaluation_receipt"]
    assert er["producer_id"] == "p12-synthetic-rule-producer"
    assert er["coverage_digest"].startswith("sha256:")
    assert er["evaluated_count"] == 1
    assert er["qualified_count"] == 1
    assert receipt["source_closure"]["package_id"]
    # The source closure rides inside the published package's input evidence.
    stored = DailyPackageStore(state).package_for(MARKET_DATE)
    assert stored.input_evidence["source_closure"]["package_id"]


def test_publish_gate_blocks_untrusted_producer_mode(env):
    """A synthetic producer id is only admitted for synthetic accounts; the
    same run under a simulation account mode is a whole-day block."""
    prod, state, _ = env
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF, holdings=(), account_mode="simulation",
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert receipt["publish_gate"]["reason_code"] == "untrusted_evaluation_producer"
    assert "evaluation_receipt" in receipt["input_block"]["missing_members"]


def test_publish_gate_blocks_forged_coverage(env):
    """A receipt whose coverage digest does not recompute over this run is a
    whole-day block - a self-minted receipt can never pass."""
    prod, state, _ = env

    class ForgedRunner(SyntheticRuleRunner):
        def run(self, request):
            output = super().run(request)
            forged = dict(output.evaluation_receipt)
            forged["coverage_digest"] = "sha256:" + "9" * 64
            return type(output)(
                decision_set=output.decision_set,
                exclusions=output.exclusions,
                evaluation_receipt=forged,
                source_closure=output.source_closure,
                holding_directives=output.holding_directives,
                trend_observations=output.trend_observations,
                history_gates=output.history_gates,
            )

    runner = ForgedRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF, holdings=(), account_mode="synthetic",
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert receipt["publish_gate"]["reason_code"] == (
        "candidate_evaluation_coverage_mismatch"
    )


def _volume_boundary_candidate(code):
    return one_candidate(
        security_code=code,
        security_name=code,
        unqualified_reason={
            "reason_code": "VOLUME_REGIME_BOUNDARY",
            "detail": "volume input crosses the 2026-07-06 trading-rule regime",
            "metric": "volume_amount",
            "window_start": "2026-06-30",
            "window_end": "2026-07-06",
            "regime_version": "st-band-2026-07-06.v1",
        },
    )


def test_volume_boundary_exclusion_keeps_p11_evidence(env):
    """Single-security drop: the receipt keeps the P11 evidence verbatim -
    security, metric, window bounds and regime version - and still publishes
    the rest of the package."""
    prod, state, _ = env
    runner = SyntheticRuleRunner(
        candidates=(one_candidate(), _volume_boundary_candidate("000001"))
    )
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF, holdings=(),
    )
    assert receipt["status"] == "PUBLISHED"
    assert receipt["package"]["entry_plan_count"] == 1
    exclusion = receipt["exclusions"][0]
    assert exclusion["security_code"] == "000001"
    assert exclusion["reason_code"] == "VOLUME_REGIME_BOUNDARY"
    assert exclusion["metric"] == "volume_amount"
    assert exclusion["window_start"] == "2026-06-30"
    assert exclusion["window_end"] == "2026-07-06"
    assert exclusion["regime_version"] == "st-band-2026-07-06.v1"


def test_volume_boundary_without_signal_is_hold_and_alert(env):
    """A held security whose evaluation depends on the missing volume input is
    HOLD_AND_ALERT: the position stays held and alerts, no sale is generated."""
    prod, state, _ = env
    runner = SyntheticRuleRunner(
        candidates=(_volume_boundary_candidate("600000"),)
    )
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(HeldPositionFact(security_code="600000", quantity=500),),
    )
    assert receipt["status"] == "PUBLISHED"
    directive = receipt["holding_directives"][0]
    assert directive["security_code"] == "600000"
    assert directive["action"] == "HOLD_AND_ALERT"
    assert directive["reason_code"] == "VOLUME_REGIME_BOUNDARY"
    stored = DailyPackageStore(state).package_for(MARKET_DATE)
    loaded = load_decision_set_dict(stored.decision_set, mode="synthetic")
    held = [d for d in loaded.decisions if d.security_code == "600000"]
    assert held and held[0].stage.value == "holding"
    assert "HOLD_AND_ALERT" in held[0].reason_codes


def test_volume_boundary_with_price_exit_still_sells(env):
    """A qualified volume-independent exit signal is never rewritten into a
    hold by the volume boundary: the EXIT_PLAN publishes and the boundary
    reason is recorded on the receipt."""
    prod, state, _ = env
    runner = SyntheticRuleRunner(
        candidates=(
            one_candidate(
                security_code="600000",
                security_name="浦发银行",
                unqualified_reason={
                    "reason_code": "VOLUME_REGIME_BOUNDARY",
                    "detail": "volume input crosses the 2026-07-06 trading-rule regime",
                    "metric": "volume_amount",
                    "window_start": "2026-06-30",
                    "window_end": "2026-07-06",
                    "regime_version": "st-band-2026-07-06.v1",
                },
                holding_signal={
                    "action": "EXIT",
                    "reason_code": "EXIT_SIGNAL",
                    "detail": "price exit signal qualified independently of volume",
                },
            ),
        )
    )
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(HeldPositionFact(security_code="600000", quantity=500),),
    )
    assert receipt["status"] == "PUBLISHED"
    assert receipt["exclusions"][0]["reason_code"] == "VOLUME_REGIME_BOUNDARY"
    directive = receipt["holding_directives"][0]
    assert directive["action"] == "EXIT_SIGNAL"
    assert directive["reason_code"] == "EXIT_SIGNAL"
    stored = DailyPackageStore(state).package_for(MARKET_DATE)
    loaded = load_decision_set_dict(stored.decision_set, mode="synthetic")
    exits = [d for d in loaded.decisions if d.stage.value == "exit_plan"]
    assert exits and exits[0].security_code == "600000"
