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
    SyntheticRuleRunner,
)
from quanthus.shared.fact_contracts import FactError
from quanthus.shared.values import AccountScope

TZ = timezone(timedelta(hours=8))
MARKET_DATE = date(2026, 11, 20)
NEXT_SESSION = date(2026, 11, 21)
BASE_SHA = "sha256:" + "1" * 64


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
    now = datetime(2026, 11, 20, 20, 0, tzinfo=TZ)
    r1 = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=now, holdings=(),
    )
    assert r1["status"] == "PUBLISHED"
    assert r1["package"]["entry_plan_count"] == 1
    r2 = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=now, holdings=(),
    )
    assert r2["status"] == "ALREADY_PUBLISHED"
    assert r2["package"]["decision_set_digest"] == r1["package"]["decision_set_digest"]
    assert len(list((state / "daily-packages").glob("*.json"))) == 1


def test_same_day_different_content_refused(env):
    prod, state, _ = env
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    now = datetime(2026, 11, 20, 20, 0, tzinfo=TZ)
    run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=now, holdings=(),
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
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 20, 0, tzinfo=TZ),
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
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 20, 0, tzinfo=TZ),
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
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 20, 0, tzinfo=TZ),
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
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 20, 0, tzinfo=TZ),
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
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 20, 0, tzinfo=TZ),
        holdings=(("600000", 500),),
    )
    assert receipt["package"]["entry_plan_count"] == 0


def test_package_source_and_retraction(env):
    prod, state, _ = env
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    now = datetime(2026, 11, 20, 20, 0, tzinfo=TZ)
    run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=now, holdings=(),
    )
    def clock():
        return datetime(2026, 11, 21, 10, 0, tzinfo=TZ)
    source = PublishedPackageSource(state, clock, mode="synthetic")
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
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 20, 0, tzinfo=TZ),
        holdings=(),
    )
    stored = DailyPackageStore(state).package_for(MARKET_DATE)
    loaded = load_decision_set_dict(stored.decision_set, mode="synthetic")
    assert loaded.market_date == MARKET_DATE
    assert loaded.decisions[0].entry is not None


def test_pending_source_when_no_package(tmp_path):
    state = tmp_path / "state"
    def clock():
        return datetime(2026, 11, 20, 20, 0, tzinfo=TZ)
    source = PublishedPackageSource(state, clock, mode="synthetic")
    scope = AccountScope("SYN-PAPER-A", "synthetic")
    pending = source.current(scope)
    assert pending.source_ref.version == "v1"
    assert pending.decisions == ()
    assert source.latest_run() is None
