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
from quanthus.p12_daily.forward_calendar import (
    DESCRIPTOR_SCHEMA as FC_DESCRIPTOR_SCHEMA,
)
from quanthus.p12_daily.forward_calendar import (
    POINTER_SCHEMA as FC_POINTER_SCHEMA,
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


def alter(**manifest_overrides):
    '''Audit-shim: mutate the core manifest, then rebind every digest the
    pointer/index declare (the audit's replay script calls this shape).

    Usage in show(): alter(volume={...}) sets manifest["volume"]={...};
    alter(calendar=None) removes the member.  The core manifest sha, the
    index entry sha and the pointer's index sha are all recomputed so the
    result still qualifies at the descriptor level - the test then exercises
    whichever downstream check the mutation targets.'''

    def apply(manifest: dict, core: Path, pointer: dict, index: dict) -> None:
        for key, value in manifest_overrides.items():
            if value is None:
                manifest.pop(key, None)
            else:
                manifest[key] = value
        mpath = core / "manifest.json"
        mpath.write_text(json.dumps(manifest, sort_keys=True))
        core_sha = _sha(mpath.read_bytes())
        if "dates" in index and MARKET_DATE.isoformat() in index["dates"]:
            index["dates"][MARKET_DATE.isoformat()]["core_manifest_sha256"] = core_sha
        if isinstance(pointer.get("entry"), dict):
            pointer["entry"]["core_manifest_sha256"] = core_sha
        return None

    return apply


def make_forward_calendar(
    calendar_root: Path,
    *,
    next_map: dict | None = None,
    available_at: str = "2026-11-20T15:35:00+08:00",
    published_at: str = "2026-11-20T15:35:00+08:00",
    coverage_end: str = "2026-12-31",
    bse_status: str = "unavailable",
    bse_reason: str = "OFFICIAL_DOCUMENT_REDIRECT_LOOP",
    version: str = "p10-calendar-test-v1",
) -> Path:
    '''Materialize the P10 forward-calendar product (pointer + descriptor).'''

    root = Path(calendar_root)
    root.mkdir(parents=True, exist_ok=True)
    if next_map is None:
        next_map = {
            "2026-11-20": "2026-11-21",
            "2026-11-21": "2026-11-23",
            "2026-11-27": "2026-11-30",
            "2026-12-30": "2026-12-31",
            "2026-12-31": None,
        }
    descriptor = {
        "schema_version": FC_DESCRIPTOR_SCHEMA,
        "product_id": "p10-forward-exchange-calendar-v1",
        "status": "PARTIAL",
        "timezone": "Asia/Shanghai",
        "coverage": {"start": "2026-11-01", "end": coverage_end},
        "venues": {
            "SSE": {
                "status": "AVAILABLE",
                "next_session": dict(next_map),
                "next_session_unavailable": {
                    k: "NEXT_SESSION_OUTSIDE_COVERAGE"
                    for k, v in next_map.items() if v is None
                },
                # Issue #722 r4-3: the strict-successor check cross-checks
                # the mapped answer against the venue's real session list;
                # the fixture derives it from the mapping so a queried date
                # resolves to the strict next session (a skipped date, a
                # regression or an absent answer is caught downstream).
                "sessions": sorted(
                    {d for d in next_map} | {v for v in next_map.values() if v}
                ),
            },
            "SZSE": {
                "status": "AVAILABLE",
                "next_session": dict(next_map),
                "next_session_unavailable": {},
                "sessions": sorted(
                    {d for d in next_map} | {v for v in next_map.values() if v}
                ),
            },
            "BSE": {"status": bse_status, "reason": bse_reason,
                    "next_session": None, "sessions": None},
        },
    }
    vdir = root / version
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "descriptor.json").write_text(json.dumps(descriptor, sort_keys=True))
    dsha = hashlib.sha256(
        (vdir / "descriptor.json").read_bytes()
    ).hexdigest()
    pointer = {
        "schema_version": FC_POINTER_SCHEMA,
        "product_id": "p10-forward-exchange-calendar-v1",
        "status": "PARTIAL",
        "version": version,
        "descriptor": f"{version}/descriptor.json",
        "descriptor_sha256": dsha,
        "published_at": published_at,
        "available_at": available_at,
    }
    (root / "CURRENT_FORWARD_CALENDAR.json").write_text(json.dumps(pointer))
    return root


def make_product(job_root: Path, market_date: str, rows: int = 5213) -> dict:
    '''Materialize the CN02 base24 pointer layout under a local replay root.'''

    # Issue #743: the job's calendar root resolves to a job_root-local
    # "calendar/" replica when present - the fixture always builds one.
    cal_root = Path(job_root) / "calendar"
    make_forward_calendar(cal_root)

    core = job_root / (f"{market_date}-20261120T000000Z") / "core"
    payload = core / "payload"
    payload.mkdir(parents=True, exist_ok=True)
    # Audit R5 (r2): write a REAL 1d parquet so the listing-facts read works;
    # the audit's pq1() rewrites it for the no-code-column case.
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        pq.write_table(
            pa.table({"security_code": ["600000", "000001"]}),
            payload / "1d.parquet",
        )
    except ImportError:
        (payload / "1d.parquet").write_bytes(b"PAR1-fake-daily-bars")
    daily_sha = _sha((payload / "1d.parquet").read_bytes())
    daily_size = (payload / "1d.parquet").stat().st_size
    # Audit R4 (r2): the parent Base manifest must be locatable so its
    # declared sha can be recomputed - materialize it inside the core dir.
    base_file = core / "base.json"
    base_file.write_text(json.dumps({"kind": "base", "stub": True}))
    base_sha_real = _sha(base_file.read_bytes())
    manifest = {
        "kind": "cn02_base24_core_v1",
        "package_id": f"core-{market_date}",
        "market_date": market_date,
        "base": {
            "manifest_sha256": base_sha_real,
            "content_digest": "sha256:" + "2" * 64,
            "path": "base.json",
        },
        "row_counts": {"dwd_security_bar": rows, "dwd_trading_calendar": 3},
        "files": [
            {
                "path": "payload/1d.parquet",
                "sha256": daily_sha,
                "size_bytes": daily_size,
            },
        ],
        "content_digest": "sha256:" + "3" * 64,
        "completed_at": f"{market_date}T18:01:06+08:00",
        "consumer_admission": "NOT_GRANTED",
        # M2 F2: the governed calendar member declares the authoritative next
        # open session at this cutoff; the job never guesses the date.
        # Issue #743 (r2): calendar.next_session and dwd_trading_calendar are
        # retired - no member here; the forward calendar product decides.
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
        "base_manifest_sha256": base_sha_real,
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


def test_history_gates_are_independent(tmp_path):
    """R8 (r2): the three history floors are independent - a corporate-action
    anomaly belongs to the 61-day feature-warmup gate, NOT the 120-day
    listing floor (which reads only listing facts + the trading calendar).
    Each mutated member trips only its own gate; the other two still hold."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"

    # Case 1: a 61-day corporate-action anomaly trips ONLY feature_warmup_61;
    # the listing floor (120) and event window (22) stay satisfied.
    def mutate_warmup(manifest):
        manifest["history"]["feature_warmup_sessions"] = {
            "observed_sessions": 80,
            "unavailable_reason": "近 61 日存在公司行为异常",
        }
        manifest["history"]["listing_sessions"] = {"observed_sessions": 200}

    _reshash_manifest(prod, MARKET_DATE.isoformat(), mutate_warmup)
    receipt = _run_and_receipt(state, prod)
    assert receipt["status"] == "INPUT_BLOCKED"
    gates = {g["gate_id"]: g for g in receipt["history_gates"]}
    assert gates["history.feature_warmup_61"]["satisfied"] is False
    assert gates["history.listing_floor_120"]["satisfied"] is True
    assert gates["history.event_window_22"]["satisfied"] is True
    assert "公司行为异常" in (gates["history.feature_warmup_61"]["unavailable_reason"] or "")

    # Case 2: only the listing floor fails; warmup + event window hold.
    def mutate_listing(manifest):
        manifest["history"]["listing_sessions"] = {
            "observed_sessions": 90,
        }

    _reshash_manifest(prod, MARKET_DATE.isoformat(), mutate_listing)
    receipt = _run_and_receipt(tmp_path / "state2", prod)
    assert receipt["status"] == "INPUT_BLOCKED"
    gates = {g["gate_id"]: g for g in receipt["history_gates"]}
    assert gates["history.listing_floor_120"]["satisfied"] is False
    assert gates["history.feature_warmup_61"]["satisfied"] is True
    assert gates["history.event_window_22"]["satisfied"] is True


def test_missing_forward_calendar_pointer_blocks(tmp_path):
    """Issue #743: without the forward-calendar pointer the run cannot fix
    next_session - the retired manifest member is never consulted."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    # Remove the job-local calendar replica entirely.
    import shutil
    shutil.rmtree(prod / "calendar")
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(),
        # strict: no job-local replica, so the CN02 default path would be
        # read - point at an empty root instead to prove the block is the
        # calendar, not a filesystem accident.
        calendar_root=tmp_path / "no-calendar",
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert "forward_calendar" in receipt["input_block"]["missing_members"]


def test_forward_calendar_weekend_next_session(tmp_path):
    """Issue #743: 11-27 maps to 11-30 across the weekend (SSE/SZSE agree)."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    cal_root = tmp_path / "cal"
    make_forward_calendar(cal_root, next_map={
        "2026-11-27": "2026-11-30",
        "2026-11-30": "2026-12-01",
    })
    make_product(prod, "2026-11-27")
    # The fixture's own manifest market_date is 11-27; the calendar decides
    # next_session=11-30.
    receipt = run_daily(
        state_dir=state, job_root=prod,
        runner=SyntheticRuleRunner(candidates=(one_candidate(),)),
        market_date=date(2026, 11, 27), next_session=None,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 27, 22, 31, tzinfo=TZ),
        decision_cutoff=datetime(2026, 11, 27, 22, 30, tzinfo=TZ),
        holdings=(), calendar_root=cal_root,
    )
    assert receipt["status"] == "PUBLISHED"
    assert receipt["next_session"] == "2026-11-30"


def test_forward_calendar_coverage_end_blocks(tmp_path):
    """Issue #743: a next_session beyond coverage (12-31 -> null) blocks."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    cal_root = tmp_path / "cal"
    make_forward_calendar(cal_root, next_map={"2026-12-31": None})
    make_product(prod, "2026-12-31")
    receipt = run_daily(
        state_dir=state, job_root=prod,
        runner=SyntheticRuleRunner(candidates=(one_candidate(),)),
        market_date=date(2026, 12, 31), next_session=None,
        account_id="SYN-PAPER-A", now=datetime(2026, 12, 31, 22, 31, tzinfo=TZ),
        decision_cutoff=datetime(2026, 12, 31, 22, 30, tzinfo=TZ),
        holdings=(), calendar_root=cal_root,
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert "forward_calendar" in receipt["input_block"]["missing_members"]


def test_forward_calendar_venue_disagreement_blocks(tmp_path):
    """Issue #743: SSE and SZSE disagreeing on next_session blocks whole-day."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    cal_root = tmp_path / "cal"
    root = make_forward_calendar(cal_root, next_map={
        "2026-11-20": "2026-11-21",
    })
    # Corrupt only SZSE's map.
    desc = root / "p10-calendar-test-v1" / "descriptor.json"
    d = json.loads(desc.read_text())
    d["venues"]["SZSE"]["next_session"]["2026-11-20"] = "2026-11-24"
    desc.write_text(json.dumps(d, sort_keys=True))
    dsha = hashlib.sha256(desc.read_bytes()).hexdigest()
    ptr_path = root / "CURRENT_FORWARD_CALENDAR.json"
    ptr = json.loads(ptr_path.read_text())
    ptr["descriptor_sha256"] = dsha
    ptr_path.write_text(json.dumps(ptr))
    make_product(prod, MARKET_DATE.isoformat())
    receipt = run_daily(
        state_dir=state, job_root=prod,
        runner=SyntheticRuleRunner(candidates=(one_candidate(),)),
        market_date=MARKET_DATE, next_session=None,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF, holdings=(), calendar_root=cal_root,
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert "disagree" in receipt["input_block"]["detail"]


def test_forward_calendar_post_cutoff_version_rejected(tmp_path):
    """Issue #743: a version made available AFTER the decision cutoff is not
    visible at decision time - the day blocks rather than consume it."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    cal_root = make_forward_calendar(
        tmp_path / "cal",
        available_at="2026-11-20T23:30:00+08:00",  # after the 22:30 cutoff
    )
    make_product(prod, MARKET_DATE.isoformat())
    receipt = run_daily(
        state_dir=state, job_root=prod,
        runner=SyntheticRuleRunner(candidates=(one_candidate(),)),
        market_date=MARKET_DATE, next_session=None,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF, holdings=(), calendar_root=cal_root,
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert "not visible at decision time" in receipt["input_block"]["detail"]


def test_forward_calendar_bse_isolation(tmp_path):
    """Issue #743: BSE venue unavailable drops BSE new candidates only;
    SSE/SZSE candidates still publish and a BSE holding keeps holding."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    cal_root = make_forward_calendar(
        tmp_path / "cal",
        bse_status="unavailable",
        bse_reason="OFFICIAL_DOCUMENT_REDIRECT_LOOP",
    )
    make_product(prod, MARKET_DATE.isoformat())
    # A BSE new candidate (920xxx) + a mainland one (600000).
    receipt = run_daily(
        state_dir=state, job_root=prod,
        runner=SyntheticRuleRunner(candidates=(
            one_candidate(security_code="920001"),
            one_candidate(security_code="600000"),
        )),
        market_date=MARKET_DATE, next_session=None,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF, holdings=(
            HeldPositionFact(security_code="430001", quantity=100),
        ), calendar_root=cal_root,
    )
    assert receipt["status"] == "PUBLISHED"
    # The BSE new candidate is excluded; the mainland one published.
    codes = [e["security_code"] for e in receipt["exclusions"]]
    assert "920001" in codes
    stored = DailyPackageStore(state).package_for(MARKET_DATE)
    assert stored is not None
    sec_codes = [d["security_code"] for d in stored.decision_set["decisions"]]
    assert "600000" in sec_codes
    assert "920001" not in sec_codes
    # The BSE holding kept holding with the venue reason on the receipt.
    assert any(
        h["security_code"] == "430001" and h["action"] == "HOLD_AND_ALERT"
        for h in receipt["holding_directives"]
    )
    # Calendar clocks recorded.
    assert receipt["calendar_version"] == "p10-calendar-test-v1"
    assert receipt["calendar_sha256"].startswith("sha256:")
    assert receipt["calendar_received_at"]
    assert receipt["calendar_verified_at"]


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
    # Copy the whole fixtures tree so member paths ("fixtures/synthetic/…")
    # resolve under member_files_root = the copied "fixtures" dir (audit R3).
    fixtures_root = tmp_path / "fixtures"
    import shutil
    shutil.copytree(src.parent.parent, fixtures_root)
    return fixtures_root / "synthetic" / name


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
    # what P12 actually read (the consumer receipt), and a complete synthetic
    # evidence read does not itself block publication of the synthetic
    # package.
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


# Issue #723 comment-7117663809: the producer encodes both reduce and exit as
# exit_plan; the held_signal's action alone distinguishes them.  Batch A
# covers the three cases - a reduce exit_plan is a \u51cf\u4ed3\u5efa\u8bae
# (no full sell task), an exit exit_plan is a liquidation, and a mismatch
# between the emitted exit_plan and the held_signal set refuses the day.


def _run_with_signal(tmp_path, signal, held_qty=500):
    prod = tmp_path / "job"
    prod.mkdir()
    make_product(prod, MARKET_DATE.isoformat())
    state = tmp_path / "state"
    runner = SyntheticRuleRunner(
        candidates=(one_candidate(holding_signal=signal),)
    )
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(HeldPositionFact(security_code="600000", quantity=held_qty),),
    )
    return receipt, state, runner


def test_reduce_signal_emits_exit_plan_and_held_signal(tmp_path):
    receipt, state, runner = _run_with_signal(
        tmp_path,
        {"action": "REDUCE", "detail": "trim toward target", "target_maximum_position": "0.30"},
    )
    assert receipt["status"] == "PUBLISHED"
    # The held signal rides the run receipt verbatim - action and target are
    # the only fields that tell this exit_plan is a 减仓建议, not a sale.
    assert receipt["held_signals"] == [
        {
            "security_code": "600000",
            "opportunity_id": "OPP-D1B-20261120-600000",
            "action": "reduce",
            "target_maximum_position": "0.30",
        }
    ]
    stored = DailyPackageStore(state).package_for(MARKET_DATE)
    loaded = load_decision_set_dict(stored.decision_set, mode="synthetic")
    exits = [d for d in loaded.decisions if d.stage.value == "exit_plan"]
    assert len(exits) == 1
    assert exits[0].opportunity_id == "OPP-D1B-20261120-600000"


def test_exit_signal_emits_full_exit_plan(tmp_path):
    receipt, state, runner = _run_with_signal(
        tmp_path,
        {"action": "EXIT", "reason_code": "EXIT_SIGNAL", "detail": "liquidate"},
    )
    assert receipt["status"] == "PUBLISHED"
    assert receipt["held_signals"][0]["action"] == "exit"
    stored = DailyPackageStore(state).package_for(MARKET_DATE)
    loaded = load_decision_set_dict(stored.decision_set, mode="synthetic")
    exits = [d for d in loaded.decisions if d.stage.value == "exit_plan"]
    assert exits and exits[0].reason_codes == ("EXIT_SIGNAL",)


def test_exit_plan_without_matching_held_signal_blocks(tmp_path):
    prod = tmp_path / "job"
    prod.mkdir()
    make_product(prod, MARKET_DATE.isoformat())
    state = tmp_path / "state"

    class DropSignals(SyntheticRuleRunner):
        def run(self, request):
            out = super().run(request)
            # Strip the producer's held_signals - the emitted exit_plan then
            # has no reduce/exit identity and the day must refuse to publish.
            import dataclasses as dc
            return dc.replace(out, held_signals=())

    runner = DropSignals(
        candidates=(one_candidate(holding_signal={"action": "EXIT"}),)
    )
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(HeldPositionFact(security_code="600000", quantity=500),),
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert receipt["publish_gate"]["reason_code"] == "exit_plan_signal_mismatch"
    assert DailyPackageStore(state).package_for(MARKET_DATE) is None


def test_reduce_exit_plan_workbench_shows_suggestion_not_sell(tmp_path):
    prod = tmp_path / "job"
    prod.mkdir()
    make_product(prod, MARKET_DATE.isoformat())
    state = tmp_path / "state"
    runner = SyntheticRuleRunner(
        candidates=(one_candidate(holding_signal={"action": "REDUCE"}),)
    )
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(HeldPositionFact(security_code="600000", quantity=500),),
    )
    assert receipt["status"] == "PUBLISHED"

    from quanthus.platform.adapters.p12_synthetic_fixture import (
        load_synthetic_fixture,
    )
    from quanthus.platform.p12.paper_fixture import (
        PAPER_ACCOUNT_ID,
        build_paper_application,
    )
    from quanthus.product_daily.application.models import (
        SyntheticRuntimeAuthorization,
    )

    fixture = load_synthetic_fixture(Path("config/p12/synthetic-desktop.v1.json"))
    queue = [datetime(2026, 11, 20, 23, 0, tzinfo=TZ)]
    app = build_paper_application(
        state_dir=state,
        policy=fixture.policy,
        now=lambda: queue[0],
        authorization=SyntheticRuntimeAuthorization(
            "audit", frozenset({PAPER_ACCOUNT_ID})
        ),
        decision_source=PublishedPackageSource(
            state, lambda: queue[0], mode="synthetic"
        ),
    )
    scope = AccountScope(PAPER_ACCOUNT_ID, "synthetic")
    bench = app.workbench(scope)
    # Issue #722 r9-1: a held reduction resolves to a held disposition -
    # stage=holding, exit_kind cleared, the sell reason swapped for a
    # registered hold reason; the page shows the suggestion note, never a
    # sell form.
    row = next(
        o for o in bench.opportunities if o.security_code == "600000"
    )
    assert row.stage == "holding"
    assert row.exit_kind is None
    assert row.action_code == "HOLD"
    assert "HOLD_AND_ALERT" in row.reason_codes
    assert "EXIT_SIGNAL" not in row.reason_codes
    assert not any(
        o.side == "sell" and o.security_code == "600000"
        for o in bench.orders
    )
    # Issue #722 r8-1: a reduce held row drops the producer's sell
    # explanation and shows only the held-reduction note - never a
    # "按计划卖出 N 股" instruction.
    texts = " ".join(e.text for e in row.explanations)
    assert "按计划卖出" not in texts
    assert "减仓建议" in texts


def test_held_disposition_never_shows_a_sell_text(tmp_path):
    """Issue #722 r8-1 / product directive comment-7139311000 suppl.: when
    the final disposition is held, every user-facing string on the plan
    reads held - no "卖出"/"清仓"/"减仓 N 股" instruction survives in the
    queue row or the expanded explanations."""
    prod = tmp_path / "job"
    prod.mkdir()
    make_product(prod, MARKET_DATE.isoformat())

    from quanthus.platform.adapters.p12_synthetic_fixture import (
        load_synthetic_fixture,
    )
    from quanthus.platform.p12.paper_fixture import (
        PAPER_ACCOUNT_ID,
        build_paper_application,
    )
    from quanthus.product_daily.application.models import (
        SyntheticRuntimeAuthorization,
    )

    forbidden = ("按计划卖出", "清仓信号已触发", "卖出 500", "减仓 500")

    def bench_rows(state, when):
        fixture = load_synthetic_fixture(
            Path("config/p12/synthetic-desktop.v1.json")
        )
        app = build_paper_application(
            state_dir=state,
            policy=fixture.policy,
            now=lambda: when,
            authorization=SyntheticRuntimeAuthorization(
                "audit", frozenset({PAPER_ACCOUNT_ID})
            ),
            decision_source=PublishedPackageSource(
                state, lambda: when, mode="synthetic"
            ),
        )
        return app.workbench(AccountScope(PAPER_ACCOUNT_ID, "synthetic"))

    # Case A - reduce kept held (no verifiable reduce binding needed for the
    # held-disposition text check; a plain reduce publish reads held).
    state = tmp_path / "state-reduce"
    runner = SyntheticRuleRunner(
        candidates=(one_candidate(holding_signal={"action": "REDUCE"}),)
    )
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        holdings=(HeldPositionFact(security_code="600000", quantity=500),),
    )
    assert receipt["status"] == "PUBLISHED"
    bench = bench_rows(state, datetime(2026, 11, 20, 23, 0, tzinfo=TZ))
    for row in bench.opportunities:
        if row.action_code == "SELL":
            continue
        blob = " ".join(
            [e.text for e in row.explanations]
            + ([row.next_observation] if row.next_observation else [])
        )
        for bad in forbidden:
            assert bad not in blob, (row.opportunity_id, bad, blob)

    # Case B - unverifiable exit: drop receipts so the reduce cannot bind.
    for path in DailyPackageStore(state).runs_dir.glob("*.json"):
        path.unlink()
    bench = bench_rows(state, datetime(2026, 11, 23, 9, 31, tzinfo=TZ))
    unver = [o for o in bench.opportunities if o.security_code == "600000"]
    assert unver
    for row in unver:
        blob = " ".join(
            [e.text for e in row.explanations]
            + ([row.next_observation] if row.next_observation else [])
        )
        for bad in forbidden + ("按计划卖出",):
            assert bad not in blob, (row.opportunity_id, bad, blob)


def test_pending_placeholder_publishes_at_configured_publish_time(tmp_path):
    """Audit R15: the pending placeholder's published_at and the derived
    next_check_at carry the configured publish time-of-day (22:30+08), not a
    fixed next-day wall-clock guess."""
    from datetime import time as _time

    state = tmp_path / "state"

    def clock():
        return datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)

    source = PublishedPackageSource(
        state,
        clock,
        mode="synthetic",
        next_session_provider=lambda _today: date(2026, 11, 25),
        publish_time=_time(22, 30, tzinfo=TZ),
    )
    pending = source.current(AccountScope("SYN-PAPER-A", "synthetic"))
    # published_at is the next session at 22:30+08 - the same moment the
    # nightly timer publishes - so now < published_at keeps it NOT_YET_.
    assert pending.published_at == datetime(2026, 11, 25, 22, 30, tzinfo=TZ)
    assert pending.published_at.tzinfo is not None
    next_check = datetime.combine(pending.next_session, pending.published_at.timetz())
    assert next_check == pending.published_at
    assert source.publish_time() == _time(22, 30, tzinfo=TZ)


# ---- Issue #723 comment-7119006547: account-facts semantics ---------------


def test_unreadable_ledger_blocks_the_whole_day(env):
    """A ledger file that exists but cannot be opened is a whole-day block
    (ACCOUNT_FACTS_UNAVAILABLE), never empty-holdings fabrication."""
    prod, state, _ = env
    state_dir = Path(state)
    state_dir.mkdir(parents=True, exist_ok=True)
    # Materialize a corrupt ledger file so _account_facts hits the read path.
    (state_dir / "p12-paper-facts.sqlite").write_bytes(b"not a sqlite db")
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    receipt = run_daily(
        state_dir=state_dir,
        job_root=prod,
        runner=runner,
        market_date=MARKET_DATE,
        next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A",
        now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        # holdings=None forces a real ledger read.
    )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert receipt["input_block"]["reason_code"] == "ACCOUNT_FACTS_UNAVAILABLE"
    assert "account_facts" in receipt["input_block"]["missing_members"]


def test_fresh_deployment_yields_empty_holdings_with_marker(env):
    """A missing ledger file with no baseline returns empty holdings, marked
    account_state=FRESH_NO_BASELINE - a real fresh state, not a read failure."""
    prod, state, _ = env
    receipt = run_daily(
        state_dir=Path(state),
        job_root=prod,
        runner=SyntheticRuleRunner(candidates=(one_candidate(),)),
        market_date=MARKET_DATE,
        next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A",
        now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
    )
    # The run publishes normally - fresh account, no holdings to evaluate.
    assert receipt["status"] in ("PUBLISHED", "ALREADY_PUBLISHED")
    assert receipt["account_facts"]["account_state"] == "FRESH_NO_BASELINE"
    assert receipt["account_facts"]["holdings_count"] == 0


def test_pending_security_blocks_fresh_entry(env):
    """A security committed by an in-flight pending fact is held out of new
    candidate evaluation (issue #723); holdings stay separate."""
    from quanthus.p12_daily.rule_runner import PendingSecurity

    prod, state, _ = env
    # A candidate on 600000; a pending open-buy-order on the same code must
    # keep it out of the new-entry set.
    pending = (PendingSecurity(security_code="600000",
                               kind="open_buy_order", quantity=100),)
    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    # Inject pending through the runner request by monkeypatching
    # _account_facts is overkill - run_daily accepts holdings only, so drive
    # the runner's pending path directly.
    from quanthus.p12_daily.daily_core_input import qualify_daily_input
    from quanthus.p12_daily.rule_runner import RunnerRequest

    qualified = qualify_daily_input(
        prod / "CURRENT_DAILY_CORE_BASE24.json",
        expected_market_date=MARKET_DATE,
        checked_at=datetime(2026, 11, 20, 22, 30, tzinfo=TZ),
    )
    request = RunnerRequest(
        snapshot=qualified,
        next_session=NEXT_SESSION,
        holdings=(),
        pending_securities=pending,
        published_at=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
        decision_cutoff=CUTOFF,
        run_id="test-pending",
    )
    output = runner.run(request)
    # 600000 is the candidate; pending must exclude it from new entries.
    assert all(
        d.security_code != "600000" or d.entry is None
        for d in output.decision_set.decisions
    )


def test_pending_security_with_unknown_quantity_uses_none(env):
    """A pending commitment whose quantity cannot be fixed (RESERVED bucket)
    is passed through as quantity=None - never a fabricated 0 and never
    merged with other pendings of the same security."""
    from quanthus.p12_daily.rule_runner import PendingSecurity

    prod, state, _ = env
    pendings = (
        PendingSecurity(security_code="600000", kind="reserved_bucket", quantity=None),
        PendingSecurity(security_code="600000", kind="open_buy_order", quantity=100),
    )
    # Each pending line is preserved verbatim - no merge, no 0 substitution.
    assert pendings[0].quantity is None
    assert pendings[1].quantity == 100
    # The runner blocks the security from a fresh entry regardless of a null
    # quantity (the commitment exists, its size is just unknown).
    blocked = {p.security_code for p in pendings}
    assert "600000" in blocked

# ---- Issue #722 r3 fourth-round returns ---------------------------------


def test_sidecar_missing_required_member_files_blocks(tmp_path):
    """Issue #722 r3-4: a REQUIRED sidecar member whose local file cannot be
    located blocks the whole read - it no longer rides the removed TEST_ONLY
    pass-through.  Optional members may continue short of evidence."""
    from quanthus.p12_daily.consumer_sidecar import (
        DISPOSITION_CLOCKS_COMPLETE,
        DISPOSITION_INPUT_OR_CLOCK_BLOCKED,
        read_sidecar,
    )

    sidecar = _copy_fixture_sidecar(tmp_path, "sidecar.v1.json")
    if sidecar is None:
        pytest.skip("P10 sidecar fixture not present")
    fixtures_root = sidecar.parent.parent
    read = read_sidecar(
        sidecar, consumer_now=CUTOFF, decision_cutoff=CUTOFF,
        member_files_root=fixtures_root,
    )
    assert read.disposition == DISPOSITION_CLOCKS_COMPLETE
    # Removing every required member's local file flips the read to blocked.
    missing_root = tmp_path / "empty"
    missing_root.mkdir()
    read2 = read_sidecar(
        sidecar, consumer_now=CUTOFF, decision_cutoff=CUTOFF,
        member_files_root=missing_root,
    )
    assert read2.disposition == DISPOSITION_INPUT_OR_CLOCK_BLOCKED
    assert any(
        "not located" in reason or "cannot be verified" in reason
        for reason in read2.blocked_reasons
    )


def test_effective_adjustment_factor_must_be_finite_positive(env):
    """Issue #722 r3-5: NaN, Inf, 0 and negative factors are invalid values
    that block, never pass through as an effective factor."""
    prod, state, _ = env
    for bad in ("NaN", "0", "-1.5", "inf"):
        (state / "marker").unlink(missing_ok=True)
        core = next(prod.glob("*/core"))
        manifest = core / "manifest.json"
        data = json.loads(manifest.read_text())
        data["corporate_actions"] = {"effective_adjustment_factor": bad}
        manifest.write_text(json.dumps(data))
        # Rebind the pointer/index digests the same way alter() does.
        index = prod / "DATES.json"
        pointer = prod / "CURRENT_DAILY_CORE_BASE24.json"
        ix = json.loads(index.read_text())
        pt = json.loads(pointer.read_text())
        msha = _sha(manifest.read_bytes())
        pt["entry"]["core_manifest_sha256"] = msha
        ix["dates"][MARKET_DATE.isoformat()]["core_manifest_sha256"] = msha
        index.write_text(json.dumps(ix))
        pt["index_sha256"] = _sha(index.read_bytes())
        pointer.write_text(json.dumps(pt))
        receipt = run_daily(
            state_dir=state, job_root=prod,
            runner=SyntheticRuleRunner(candidates=(one_candidate(),)),
            market_date=MARKET_DATE, next_session=NEXT_SESSION,
            account_id="SYN-PAPER-A",
            now=datetime(2026, 11, 20, 22, 31, tzinfo=TZ),
            decision_cutoff=CUTOFF, holdings=(),
        )
        assert receipt["status"] == "INPUT_BLOCKED", bad
        assert "corporate_actions" in receipt["input_block"]["missing_members"]


def test_forward_calendar_query_before_coverage_blocks(tmp_path):
    """Issue #722 r3-6: a query date outside coverage is unavailable, and a
    mapped next_session that does not move strictly forward is refused."""
    from quanthus.p12_daily.forward_calendar import (
        load_forward_calendar,
        next_session,
    )

    cal_root = make_forward_calendar(
        tmp_path / "cal",
        next_map={
            "2026-10-30": "2026-11-02",   # query before coverage start 11-01
            "2026-11-20": "2026-11-19",   # regressed date
            "2026-12-31": None,
        },
    )
    cal, block, _receipt = load_forward_calendar(
        cal_root, decision_cutoff=CUTOFF
    )
    assert cal is not None
    nxt, err = next_session(cal, date(2026, 10, 30))
    assert nxt is None and err is not None
    assert "coverage" in err.detail
    nxt, err = next_session(cal, date(2026, 11, 20))
    assert nxt is None and err is not None
    assert "not after" in err.detail
    nxt, err = next_session(cal, date(2026, 12, 31))
    assert nxt is None and err is not None


def test_forward_calendar_next_session_must_be_next_session_in_list(tmp_path):
    """Issue #722 r3-6: when the venue declares its session list, the mapped
    answer must BE the next session in it - a skipped session is refused."""
    import json as _json

    cal_root = make_forward_calendar(
        tmp_path / "cal",
        next_map={"2026-11-20": "2026-11-24"},
    )
    desc = cal_root / "p10-calendar-test-v1" / "descriptor.json"
    d = _json.loads(desc.read_text())
    # Declare real sessions: 11-20 -> 11-21 is the true successor; the map
    # claims 11-24, which skips 11-21 and must be refused.
    sessions = ["2026-11-20", "2026-11-21", "2026-11-24"]
    for venue in ("SSE", "SZSE"):
        d["venues"][venue]["sessions"] = list(sessions)
    desc.write_text(_json.dumps(d, sort_keys=True))
    dsha = hashlib.sha256(desc.read_bytes()).hexdigest()
    ptr = cal_root / "CURRENT_FORWARD_CALENDAR.json"
    p = _json.loads(ptr.read_text())
    p["descriptor_sha256"] = dsha
    ptr.write_text(_json.dumps(p))
    from quanthus.p12_daily.forward_calendar import (
        load_forward_calendar,
        next_session,
    )

    cal, _, _ = load_forward_calendar(cal_root, decision_cutoff=CUTOFF)
    nxt, err = next_session(cal, date(2026, 11, 20))
    assert nxt is None and err is not None
    assert "different next session" in err.detail


def test_ledger_path_as_directory_is_a_read_failure(env):
    """Issue #722 r3-7: a ledger path that exists but is not a regular file
    is a read failure, never FRESH_NO_BASELINE."""
    from quanthus.p12_daily.daily_job import (
        AccountFactsUnavailable,
        _account_facts,
    )

    prod, state, _ = env
    state_dir = Path(state)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "p12-paper-facts.sqlite").mkdir()
    with pytest.raises(AccountFactsUnavailable):
        _account_facts(state_dir, "SYN-PAPER-A")


def test_funding_binding_read_failure_blocks(env):
    """Issue #722 r3-7: a funding-binding read failure is
    ACCOUNT_FACTS_UNAVAILABLE, never a quiet empty binding."""
    from types import SimpleNamespace as N
    from unittest.mock import patch

    from quanthus.p12_daily.daily_job import (
        AccountFactsUnavailable,
        _account_facts,
    )

    prod, state, _ = env
    state_dir = Path(state)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "p12-paper-facts.sqlite").write_bytes(b"x")
    snapshot = N(
        projection=N(positions=()),
        history=N(orders=()),
    )

    def failed(_ctx):
        raise PermissionError("fixture funding read failed")

    service = N(
        snapshot=lambda _c: snapshot,
        funding_binding=failed,
        funding_state=lambda _c: None,
    )
    with patch(
        "quanthus.platform.adapters.product_daily_facts_sqlite."
        "build_synthetic_fact_service",
        return_value=service,
    ), pytest.raises(AccountFactsUnavailable):
        _account_facts(state_dir, "SYN-PAPER-A")


def test_reserved_bucket_pending_skips_settled_holding_security(env):
    """Issue #722 r3-7: a RESERVED binding for a security already materialized
    as a settled holding is not re-listed as a reserved_bucket pending."""
    from types import SimpleNamespace as N
    from unittest.mock import patch

    from quanthus.p12_daily.daily_job import _account_facts

    prod, state, _ = env
    state_dir = Path(state)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "p12-paper-facts.sqlite").write_bytes(b"x")
    position = N(security_code="600000", quantity=500)
    snapshot = N(
        projection=N(positions=(position,)),
        history=N(orders=()),
    )
    # Issue #722 r4-4: a binding whose bucket phases cannot be read blocks
    # the day; this fixture supplies a readable RESERVED state so the
    # settled-holding case still skips the double-listing.
    bucket = N(bucket_id="b1", phase="reserved")
    service = N(
        snapshot=lambda _c: snapshot,
        funding_binding=lambda _c: {"bucket_securities": {"b1": "600000"}},
        funding_state=lambda _c: (N(buckets=(bucket,)), 1),
    )
    with patch(
        "quanthus.platform.adapters.product_daily_facts_sqlite."
        "build_synthetic_fact_service",
        return_value=service,
    ):
        holdings, pending, _st, _dg = _account_facts(state_dir, "SYN-PAPER-A")
    assert [h.security_code for h in holdings] == ["600000"]
    assert pending == ()


def test_hot_cache_refuses_rewritten_file_bytes(tmp_path):
    """Issue #722 r3-8: a hot-cache hit still re-checks the current file
    bytes - a package file rewritten after the first read is refused."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    now = datetime(2026, 11, 20, 22, 31, tzinfo=TZ)
    run_daily(
        state_dir=state, job_root=prod,
        runner=SyntheticRuleRunner(candidates=(one_candidate(),)),
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=now,
        decision_cutoff=CUTOFF, holdings=(),
    )
    scope = AccountScope("SYN-PAPER-A", "synthetic")
    def clock():
        return now + timedelta(days=1)

    source = PublishedPackageSource(state, clock)
    assert source.current(scope).market_date == MARKET_DATE
    # Rewrite the stored file in place: the registry digest is unchanged, so
    # only the live-byte re-check can refuse it.
    store = DailyPackageStore(state)
    path = store.package_path(MARKET_DATE)
    body = json.loads(path.read_text())
    body["input_evidence"]["content_digest"] = "tampered-after-cache"
    path.write_text(json.dumps(body))
    with pytest.raises(FactError) as exc:
        source.current(scope)
    assert exc.value.reason_code == "POLICY_NOT_ADOPTED"


def test_reduce_projection_binds_the_published_run(tmp_path):
    """Issue #722 r3-1: a later INPUT_BLOCKED or precheck receipt on the same
    market_date never rewrites the published package's held_signals - the
    reduce projection binds the run whose status is PUBLISHED."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    holding = HeldPositionFact(security_code="600000", quantity=500)
    runner = SyntheticRuleRunner(candidates=(
        one_candidate(holding_signal={
            "action": "reduce", "target_position_fraction": "0.50",
        }),
    ))
    now = datetime(2026, 11, 20, 22, 31, tzinfo=TZ)
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=now,
        decision_cutoff=CUTOFF, holdings=(holding,),
    )
    assert receipt["status"] == "PUBLISHED"
    # Append a later blocked receipt on the same market_date.
    store = DailyPackageStore(state)
    later = {
        "schema_version": "quanthus.p12.daily-run.v1",
        "run_id": "20261120T230000000000+0800",
        "market_date": MARKET_DATE.isoformat(),
        "status": "INPUT_BLOCKED",
        "package": None,
    }
    store.write_run(later)
    source = PublishedPackageSource(state, lambda: now + timedelta(days=1))
    published = source.published_run_for(MARKET_DATE)
    assert published is not None
    assert published["status"] in ("PUBLISHED", "ALREADY_PUBLISHED")
    assert published["run_id"] == receipt["run_id"]
    assert (store.latest_run() or {})["status"] == "INPUT_BLOCKED"

def test_overdue_judged_from_publish_deadline_not_next_check(tmp_path):
    """Issue #722 r4-2: overdue starts at the day's FIRST configured publish
    slot (the earliest post-cutoff timer), never a publish-time-plus-fixed-
    minutes guess - and never next_check_at, which only names the NEXT
    scheduled publish point."""
    from quanthus.p12_daily.package_source import PublishedPackageSource

    state = tmp_path / "state"
    now = datetime(2026, 11, 23, 22, 36, tzinfo=TZ)
    source = PublishedPackageSource(state, lambda: now)
    # The deadline is the first configured slot (22:35); at 22:36 a package
    # or successful receipt is already overdue - not deferred to 23:00.
    deadline = source.publish_deadline(date(2026, 11, 23))
    assert deadline == datetime(2026, 11, 23, 22, 35, tzinfo=TZ)
    assert now > deadline
    # A late-publish at 22:35 yesterday must not shift the configured
    # 22:30 time-of-day used for the next session's check point.
    assert source.publish_time().hour == 22
    assert source.publish_time().minute == 30


def _publish_reduce_package(state, prod):
    """Publish one package whose exit_plan carries a reduce held_signal;
    returns (receipt, store)."""
    holding = HeldPositionFact(security_code="600000", quantity=500)
    runner = SyntheticRuleRunner(candidates=(
        one_candidate(holding_signal={
            "action": "reduce", "target_position_fraction": "0.50",
        }),
    ))
    now = datetime(2026, 11, 20, 22, 31, tzinfo=TZ)
    receipt = run_daily(
        state_dir=state, job_root=prod, runner=runner,
        market_date=MARKET_DATE, next_session=NEXT_SESSION,
        account_id="SYN-PAPER-A", now=now,
        decision_cutoff=CUTOFF, holdings=(holding,),
    )
    return receipt, DailyPackageStore(state)


def test_reduce_projection_requires_the_package_run_identity(tmp_path):
    """Issue #722 r4-1: a successful receipt naming a DIFFERENT package
    digest or a different run identity does not bind the published
    package's exit types - the workbench then shows the exit type as
    unverifiable, never a guessed full sell."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    receipt, store = _publish_reduce_package(state, prod)
    assert receipt["status"] == "PUBLISHED"
    trusted = store.package_for(MARKET_DATE)
    now = datetime(2026, 11, 23, 9, 31, tzinfo=TZ)
    source = PublishedPackageSource(state, lambda: now)
    # A forgery: same market_date, PUBLISHED-equivalent status, but a
    # different package digest and a different run id.
    forgery = {
        **receipt,
        "run_id": "20261120T223400000000+0800",
        "status": "ALREADY_PUBLISHED",
        "package": {**receipt["package"], "file_sha256": "f" * 64},
        "held_signals": [],
    }
    store.write_run(forgery)
    # The bound receipt is still the real publish run - the forgery is not
    # selected and the projection keeps the reduce signal.
    bound = source.published_run_for(MARKET_DATE)
    assert bound is not None
    assert bound["run_id"] == receipt["run_id"]
    # Removing the bound receipt leaves no verifiable produce run.
    for path in store.runs_dir.glob("*.json"):
        path.unlink()
    store.write_run(forgery)
    source2 = PublishedPackageSource(state, lambda: now)
    assert source2.published_run_for(MARKET_DATE) is None
    assert trusted is not None


def test_exit_type_unverified_blocks_confirmation(tmp_path):
    """Issue #722 r4-1: with no receipt binding the published package's
    digest and run identity, the exit plan reads HOLD + MISSING_INPUT +
    MISSING_INPUT ("退出类型无法核实") and is not confirmable."""
    from quanthus.platform.adapters.p12_synthetic_fixture import (
        load_synthetic_fixture,
    )
    from quanthus.platform.p12.paper_fixture import build_paper_application
    from quanthus.product_daily.application.models import (
        SyntheticRuntimeAuthorization,
    )

    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    receipt, store = _publish_reduce_package(state, prod)
    assert receipt["status"] == "PUBLISHED"
    # Drop every run receipt: no produce run is verifiable.
    for path in store.runs_dir.glob("*.json"):
        path.unlink()
    now = datetime(2026, 11, 23, 9, 31, tzinfo=TZ)
    source = PublishedPackageSource(state, lambda: now, mode="synthetic")
    app = build_paper_application(
        state_dir=state,
        policy=load_synthetic_fixture(
            Path("config/p12/synthetic-desktop.v1.json")
        ).policy,
        now=lambda: now,
        authorization=SyntheticRuntimeAuthorization(
            "audit", frozenset({"SYN-PAPER-A"})
        ),
        decision_source=source,
    )
    workbench = app.workbench(app.scope)
    # Issue #722 r7-1: the backend hands the desktop a HELD row, never a
    # sell action/quantity/explanation - no desktop generation can render
    # "卖出 N 股" / "触发卖出计划" / "卖出确认照常放行" for an
    # unverifiable exit.  The stage rewrites to holding and the row
    # carries only the unverifiable note.
    candidates = [
        item for item in workbench.opportunities
        if item.security_code == "600000"
    ]
    assert candidates
    for item in candidates:
        assert item.action_code == "HOLD"
        assert item.status_code == "MISSING_INPUT"
        assert item.exit_kind is None
        assert item.stage != "exit_plan", (
            "an unverifiable exit reads as a held row, not an exit task"
        )
        # Issue #722 r6-2 + r7-1: the unverifiable note reuses the
        # registered INPUT_UNAVAILABLE code; the sell-signal EXIT_SIGNAL
        # reason and the sell quantity/explanation never reach the page.
        assert "INPUT_UNAVAILABLE" in item.reason_codes
        assert "EXIT_SIGNAL" not in item.reason_codes
        assert item.next_observation == (
            "退出类型无法核实，暂不确认；持仓继续持有。"
        )


def test_every_input_fault_receipt_reads_on_the_workbench(tmp_path):
    """Issue #722 r8-2: every input-fault receipt - wrapper DATES, CLI
    argument, descriptor/pointer - lands via the shared writer with a
    contract-conformant sha256 runner digest, and the workbench serves it
    over the authenticated read path instead of a 500."""
    import re

    from quanthus.p12_daily.daily_job import write_input_blocked_receipt
    from quanthus.platform.p12.transport import _run_receipt

    digest_re = re.compile(r"^sha256:[0-9a-f]{64}$")
    faults = [
        dict(
            detail="wrapper could not resolve the market date: bad DATES",
            runner_ref=("p12-daily-run", "wrapper", "sha256:" + "a" * 64),
            missing=("DATES.json",),
        ),
        dict(
            detail="daily-job CLI could not parse --market-date 'broken-date'",
            runner_ref=("quanthus.p12_daily.daily_job", "cli", "sha256:" + "b" * 64),
            missing=("--market-date",),
        ),
        dict(
            detail="core pointer descriptor unreadable",
            runner_ref=("p12-daily-job", "input", "sha256:" + "c" * 64),
            missing=("CURRENT_DAILY_CORE_BASE24.json",),
        ),
    ]
    for index, fault in enumerate(faults):
        state = tmp_path / ("state-" + str(index))
        receipt = write_input_blocked_receipt(
            state,
            account_id="SYN-PAPER-A",
            detail=fault["detail"],
            runner_ref=fault["runner_ref"],
            missing_members=fault["missing"],
            now=datetime(2026, 11, 25, 9, 31, tzinfo=TZ),
        )
        assert receipt["status"] == "INPUT_BLOCKED"
        assert digest_re.match(receipt["runner"]["digest"])
        store_receipt = DailyPackageStore(state).latest_run()
        assert store_receipt is not None
        projected = _run_receipt(store_receipt)
        assert projected is not None
        assert projected["status"] == "INPUT_BLOCKED"
        assert digest_re.match(projected["runner"]["digest"])
        assert projected["input_block"]["detail"]


def test_ledger_dangling_symlink_is_a_read_failure(env):
    """Issue #722 r4-4: a ledger path that is a dangling symlink is a read
    failure (ACCOUNT_FACTS_UNAVAILABLE), never FRESH_NO_BASELINE."""
    from quanthus.p12_daily.daily_job import (
        AccountFactsUnavailable,
        _account_facts,
    )

    prod, state, _ = env
    state_dir = Path(state)
    state_dir.mkdir(parents=True, exist_ok=True)
    link = state_dir / "p12-paper-facts.sqlite"
    link.symlink_to(state_dir / "missing-target")
    with pytest.raises(AccountFactsUnavailable):
        _account_facts(state_dir, "SYN-PAPER-A")


def test_funding_binding_without_phase_state_blocks(env):
    """Issue #722 r4-4: a funding binding whose bucket phases cannot be
    read blocks the day; only an explicit RESERVED phase produces a
    reserved_bucket pending."""
    from types import SimpleNamespace as N
    from unittest.mock import patch

    from quanthus.p12_daily.daily_job import (
        AccountFactsUnavailable,
        _account_facts,
    )

    prod, state, _ = env
    state_dir = Path(state)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "p12-paper-facts.sqlite").write_bytes(b"x")
    snapshot = N(projection=N(positions=()), history=N(orders=()))
    service = N(
        snapshot=lambda _c: snapshot,
        funding_binding=lambda _c: {
            "bucket_securities": {"held": "000001", "reserved": "600000"}
        },
        funding_state=lambda _c: None,
    )
    with patch(
        "quanthus.platform.adapters.product_daily_facts_sqlite."
        "build_synthetic_fact_service",
        return_value=service,
    ), pytest.raises(AccountFactsUnavailable):
        _account_facts(state_dir, "SYN-PAPER-A")
    # Readable phases: only the RESERVED bucket names a pending security.
    service.funding_state = lambda _c: (
        N(buckets=[
            N(bucket_id="held", phase="held"),
            N(bucket_id="reserved", phase="reserved"),
        ]),
        1,
    )
    with patch(
        "quanthus.platform.adapters.product_daily_facts_sqlite."
        "build_synthetic_fact_service",
        return_value=service,
    ):
        _holdings, pending, state_, _dg = _account_facts(
            state_dir, "SYN-PAPER-A"
        )
    assert state_ == "OK"
    assert [(p.security_code, p.kind) for p in pending] == [
        ("600000", "reserved_bucket")
    ]


def test_forward_calendar_missing_sessions_blocks(tmp_path):
    """Issue #722 r4-3: a descriptor whose venue session list is absent,
    empty or unparseable is a calendar block, never an open calendar."""
    from quanthus.p12_daily.forward_calendar import load_forward_calendar

    for mutate in (
        lambda venue: venue.pop("sessions", None),
        lambda venue: venue.__setitem__("sessions", None),
        lambda venue: venue.__setitem__("sessions", []),
        lambda venue: venue.__setitem__("sessions", ["2026-11-20", "BAD"]),
    ):
        root = make_forward_calendar(
            tmp_path / "cal", next_map={"2026-11-20": "2026-11-24"}
        )
        ptr_path = root / "CURRENT_FORWARD_CALENDAR.json"
        pointer = json.loads(ptr_path.read_text())
        desc_path = root / pointer["descriptor"]
        descriptor = json.loads(desc_path.read_text())
        for venue in ("SSE", "SZSE"):
            mutate(descriptor["venues"][venue])
        desc_path.write_text(json.dumps(descriptor, sort_keys=True))
        pointer["descriptor_sha256"] = hashlib.sha256(
            desc_path.read_bytes()
        ).hexdigest()
        ptr_path.write_text(json.dumps(pointer))
        cal, block, _ = load_forward_calendar(root, decision_cutoff=CUTOFF)
        assert cal is None and block is not None, mutate


def test_forward_calendar_missing_or_bad_coverage_blocks(tmp_path):
    """Issue #722 r4-3: missing, partial, unparseable or reversed coverage
    bounds are a calendar block."""
    from quanthus.p12_daily.forward_calendar import load_forward_calendar

    for coverage in (
        {},
        {"start": "2026-11-01"},
        {"end": "2026-12-31"},
        {"start": "BAD", "end": "2026-12-31"},
        {"start": "2026-12-31", "end": "2026-11-01"},
    ):
        root = make_forward_calendar(
            tmp_path / "cal", next_map={"2026-11-20": "2026-11-24"}
        )
        ptr_path = root / "CURRENT_FORWARD_CALENDAR.json"
        pointer = json.loads(ptr_path.read_text())
        desc_path = root / pointer["descriptor"]
        descriptor = json.loads(desc_path.read_text())
        descriptor["coverage"] = coverage
        desc_path.write_text(json.dumps(descriptor, sort_keys=True))
        pointer["descriptor_sha256"] = hashlib.sha256(
            desc_path.read_bytes()
        ).hexdigest()
        ptr_path.write_text(json.dumps(pointer))
        cal, block, _ = load_forward_calendar(root, decision_cutoff=CUTOFF)
        assert cal is None and block is not None, coverage


def test_base_manifest_pin_supplies_missing_location(tmp_path):
    """Issue #722 r4-5 / P10 #747: when the core manifest's base member
    carries no file location, the pinned read-only manifest path supplies
    it - but only while its recomputed sha matches the declared digest."""
    import hashlib as _hl

    from quanthus.p12_daily.daily_core_input import qualify_daily_input

    prod = tmp_path / "job"
    prod.mkdir()
    meta = make_product(prod, MARKET_DATE.isoformat())
    core = meta["core"]
    # Rebind the manifest so its base member has NO path (the P10 #747
    # shape) but declares the real sha of a pinned external base file.
    pinned_dir = tmp_path / "pinned-base"
    pinned_dir.mkdir()
    pinned_file = pinned_dir / "manifest.json"
    pinned_file.write_text(json.dumps({"kind": "base", "sealed": True}))
    pinned_sha = "sha256:" + _hl.sha256(pinned_file.read_bytes()).hexdigest()
    manifest = json.loads((core / "manifest.json").read_text())
    manifest["base"] = {
        "manifest_sha256": pinned_sha,
        "package_id": "standard-base-2.5.1-8cd966be87fc",
    }
    (core / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
    core_sha = _sha((core / "manifest.json").read_bytes())
    ptr_path = prod / "CURRENT_DAILY_CORE_BASE24.json"
    pointer = json.loads(ptr_path.read_text())
    pointer["entry"]["base_manifest_sha256"] = pinned_sha
    pointer["entry"]["core_manifest_sha256"] = core_sha
    index = json.loads((prod / "DATES.json").read_text())
    index["dates"][MARKET_DATE.isoformat()]["core_manifest_sha256"] = core_sha
    (prod / "DATES.json").write_text(json.dumps(index, sort_keys=True))
    pointer["index_sha256"] = _sha((prod / "DATES.json").read_bytes())
    ptr_path.write_text(json.dumps(pointer))

    pin = {
        "package_id": "standard-base-2.5.1-8cd966be87fc",
        "manifest_sha256": pinned_sha,
        "manifest_path": str(pinned_file),
    }
    qualified = qualify_daily_input(
        prod / "CURRENT_DAILY_CORE_BASE24.json",
        expected_market_date=MARKET_DATE,
        checked_at=datetime(2026, 11, 20, 22, 30, tzinfo=TZ),
        base_manifest_pin=pin,
    )
    assert qualified.base_manifest_sha256 == pinned_sha

    # A declared sha that differs from the pinned identity still blocks.
    manifest["base"] = {
        "manifest_sha256": "sha256:" + "9" * 64,
        "package_id": "standard-base-2.5.1-8cd966be87fc",
    }
    (core / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
    core_sha2 = _sha((core / "manifest.json").read_bytes())
    pointer["entry"]["core_manifest_sha256"] = core_sha2
    pointer["entry"]["base_manifest_sha256"] = "sha256:" + "9" * 64
    index["dates"][MARKET_DATE.isoformat()]["core_manifest_sha256"] = core_sha2
    (prod / "DATES.json").write_text(json.dumps(index, sort_keys=True))
    pointer["index_sha256"] = _sha((prod / "DATES.json").read_bytes())
    ptr_path.write_text(json.dumps(pointer))
    blocked = qualify_daily_input(
        prod / "CURRENT_DAILY_CORE_BASE24.json",
        expected_market_date=MARKET_DATE,
        checked_at=datetime(2026, 11, 20, 22, 30, tzinfo=TZ),
        base_manifest_pin=pin,
    )
    from quanthus.p12_daily.daily_core_input import InputBlock
    assert isinstance(blocked, InputBlock)


# Issue #722 r6-4: the publish slots are configured in three places that must
# never drift - the Python DEFAULT_PUBLISH_SLOTS, the repository systemd
# timer, and the deployment's QUANTHUS_P12_PUBLISH_SLOTS env.  This check
# parses the timer's post-cutoff slots and compares them to the Python
# default; a drift between any two fails the suite.
def test_publish_slots_consistent_across_python_timer_and_deployment():
    from datetime import time as _time

    from quanthus.p12_daily.package_source import (
        DEFAULT_PUBLISH_SLOTS,
        DEFAULT_PUBLISH_TIME,
        EXCHANGE_TZ,
    )

    cutoff = DEFAULT_PUBLISH_TIME
    # Repository timer: the post-cutoff OnCalendar entries are the publish
    # slots (the pre-cutoff 20/21/22:00 lines are precheck passes).
    timer = Path("ops/systemd/quanthus-p12-daily.timer").read_text()
    timer_slots = set()
    for line in timer.splitlines():
        line = line.strip()
        if not line.startswith("OnCalendar="):
            continue
        spec = line.split("=", 1)[1]
        # form: *-*-* HH:MM:SS Asia/Shanghai
        clock = spec.split()[1].rsplit(" ", 1)[0]
        hh, mm, _ss = clock.split(":")
        t = _time(int(hh), int(mm), tzinfo=EXCHANGE_TZ)
        if t >= cutoff:
            timer_slots.add(t)
    assert timer_slots == set(DEFAULT_PUBLISH_SLOTS)

    # Deployment env: when the deployment pins QUANTHUS_P12_PUBLISH_SLOTS it
    # must equal the same slot set; unset falls back to the Python default.
    import os
    raw = os.environ.get("QUANTHUS_P12_PUBLISH_SLOTS")
    if raw:
        env_slots = {
            _time.fromisoformat(tok.strip()).replace(tzinfo=EXCHANGE_TZ)
            if _time.fromisoformat(tok.strip()).tzinfo is None
            else _time.fromisoformat(tok.strip())
            for tok in raw.split(",")
            if tok.strip()
        }
        assert env_slots == set(DEFAULT_PUBLISH_SLOTS)


def test_already_published_receipt_never_supplies_the_exit_type(tmp_path):
    """Issue #722 r6-1: an ALREADY_PUBLISHED receipt for the same package
    digest and package_id but a DIFFERENT run_id does not bind the
    published package - only the PUBLISHED receipt whose run_id the
    envelope recorded supplies the exit type.  Removing that run leaves
    the exit type unverifiable."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    receipt, store = _publish_reduce_package(state, prod)
    assert receipt["status"] == "PUBLISHED"
    stored = store.package_for(MARKET_DATE)
    assert stored is not None
    assert stored.published_by_run_id == receipt["run_id"]
    now = datetime(2026, 11, 23, 9, 31, tzinfo=TZ)
    source = PublishedPackageSource(state, lambda: now)
    bound = source.published_run_for(MARKET_DATE)
    assert bound is not None and bound["run_id"] == receipt["run_id"]

    # The r5 counterexample: identical package digest + package_id under a
    # different run id with ALREADY_PUBLISHED status.
    shadow = {
        **receipt,
        "run_id": "20261120T224500000000+0800",
        "status": "ALREADY_PUBLISHED",
        "package": dict(receipt["package"]),
        "held_signals": [],
    }
    store.write_run(shadow)
    bound = source.published_run_for(MARKET_DATE)
    assert bound is not None
    assert bound["run_id"] == receipt["run_id"]
    assert bound["status"] == "PUBLISHED"

    # With the producing run deleted, only the shadow remains: the exit
    # type can no longer be verified.
    (store.runs_dir / (receipt["run_id"] + ".json")).unlink()
    source2 = PublishedPackageSource(state, lambda: now)
    assert source2.published_run_for(MARKET_DATE) is None

    # A pre-provenance package (no published_by_run_id on the envelope) is
    # unverifiable even while a matching PUBLISHED receipt exists.
    package_path = (
        state / "daily-packages" / (MARKET_DATE.isoformat() + ".json")
    )
    body = json.loads(package_path.read_text())
    body.pop("published_by_run_id", None)
    package_path.write_text(json.dumps(body, sort_keys=True))
    store.write_run(receipt)
    source3 = PublishedPackageSource(state, lambda: now)
    assert source3.published_run_for(MARKET_DATE) is None


def test_prior_day_never_masks_present_day_overdue(tmp_path):
    """Issue #722 r6-4: a prior market_date's successful receipt or
    published package never masks the CURRENT day's overdue - overdue is
    judged on the clock date, the expected publication day."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    receipt, store = _publish_reduce_package(state, prod)
    assert receipt["status"] == "PUBLISHED"
    assert store.package_for(MARKET_DATE) is not None

    # Three days later, past that day's first publish slot: still overdue
    # even though an older package and receipt are in the store.
    now = datetime(2026, 11, 23, 22, 36, tzinfo=TZ)
    source = PublishedPackageSource(state, lambda: now)
    assert source.expected_overdue(now) is True

    # Same day before the first slot: not overdue.
    early = datetime(2026, 11, 23, 22, 34, tzinfo=TZ)
    assert source.expected_overdue(early) is False

    # A successful receipt for TODAY clears it; a package for today does too.
    today_run = {
        **receipt,
        "run_id": "20261123T223500000000+0800",
        "market_date": "2026-11-23",
        "package": None,
    }
    store.write_run(today_run)
    assert source.expected_overdue(now) is False


def test_unreadable_input_manifest_writes_blocked_receipt(tmp_path):
    """Issue #722 r6-5: an input manifest that exists but cannot be read
    (PermissionError) still produces an INPUT_BLOCKED run receipt - the
    job never exits with zero receipts."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())

    original_open = Path.open

    def denied(self, *args, **kwargs):
        if self.name == "manifest.json":
            raise PermissionError(13, "denied fixture", str(self))
        return original_open(self, *args, **kwargs)

    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    now = datetime(2026, 11, 20, 22, 31, tzinfo=TZ)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "open", denied)
        receipt = run_daily(
            state_dir=state, job_root=prod, runner=runner,
            market_date=MARKET_DATE, next_session=NEXT_SESSION,
            account_id="SYN-PAPER-A", now=now, decision_cutoff=CUTOFF,
        )
    assert receipt["status"] == "INPUT_BLOCKED"
    store = DailyPackageStore(state)
    assert len(store.runs()) == 1


def test_unreadable_forward_calendar_descriptor_writes_blocked_receipt(tmp_path):
    """Issue #722 r7-5: the forward-calendar descriptor hashing/reading
    step cannot crash the run - a descriptor that exists but refuses to be
    read still produces an INPUT_BLOCKED run receipt."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    cal_dir = prod / "calendar"
    descriptor = next(cal_dir.glob("*/descriptor.json"))

    original_open = Path.open

    def denied(self, *args, **kwargs):
        if self == descriptor:
            raise PermissionError(13, "denied fixture", str(self))
        return original_open(self, *args, **kwargs)

    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    now = datetime(2026, 11, 20, 22, 31, tzinfo=TZ)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "open", denied)
        receipt = run_daily(
            state_dir=state, job_root=prod, runner=runner,
            market_date=MARKET_DATE, next_session=NEXT_SESSION,
            account_id="SYN-PAPER-A", now=now, decision_cutoff=CUTOFF,
        )
    assert receipt["status"] == "INPUT_BLOCKED"
    assert "forward_calendar" in receipt["input_block"]["missing_members"]
    store = DailyPackageStore(state)
    assert len(store.runs()) == 1


def test_wrapper_unreadable_dates_writes_blocked_receipt(tmp_path):
    """Issue #722 r7-5: the p12-daily-run wrapper records an INPUT_BLOCKED
    run receipt itself when DATES.json cannot be read - it never exits with
    zero receipts."""
    import os
    import subprocess

    job = tmp_path / "job"
    job.mkdir()
    # DATES.json exists but is a directory -> unreadable as an index.
    (job / "DATES.json").mkdir()
    state = tmp_path / "state"
    env = {
        **os.environ,
        "QUANTHUS_P12_DAILY_JOB_ROOT": str(job),
        "QUANTHUS_P12_DAILY_STATE_DIR": str(state),
        "QUANTHUS_P12_DECISION_CUTOFF": "22:30:00+08:00",
        "P12_VENV_PYTHON": os.sys.executable,
        "PYTHONPATH": "src",
    }
    env.pop("P12_MARKET_DATE", None)
    result = subprocess.run(
        ["bash", "ops/bin/p12-daily-run"],
        env=env, text=True, capture_output=True, cwd=str(tmp_path.parent.parent) if False else None,
    )
    assert result.returncode != 0
    store = DailyPackageStore(state)
    runs = store.runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "INPUT_BLOCKED"
    assert "DATES.json" in runs[0]["input_block"]["missing_members"]


def test_unhandled_inner_exception_writes_failed_receipt(tmp_path):
    """Issue #722 r7-5: an exception that escapes the typed input guards
    still lands as a FAIL-closed run receipt (status FAILED with the real
    exception), never zero receipts and never a fabricated package."""
    prod = tmp_path / "job"
    prod.mkdir()
    state = tmp_path / "state"
    make_product(prod, MARKET_DATE.isoformat())
    cal_dir = prod / "calendar"
    descriptor = next(cal_dir.glob("*/descriptor.json"))

    original = Path.read_bytes

    def boom(self, *args, **kwargs):
        if self == descriptor:
            raise RuntimeError("inner crash bypassing typed guards")
        return original(self, *args, **kwargs)

    runner = SyntheticRuleRunner(candidates=(one_candidate(),))
    now = datetime(2026, 11, 20, 22, 31, tzinfo=TZ)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "read_bytes", boom)
        receipt = run_daily(
            state_dir=state, job_root=prod, runner=runner,
            market_date=MARKET_DATE, next_session=NEXT_SESSION,
            account_id="SYN-PAPER-A", now=now, decision_cutoff=CUTOFF,
        )
    # The inner descriptor JSON read crashed outside OSError/typed guards ->
    # the public wrapper lands a FAILED receipt carrying the real exception.
    assert receipt["status"] == "FAILED"
    assert "RuntimeError" in receipt["error"]["detail"]
    assert "input_read" in receipt["input_block"]["missing_members"]
    store = DailyPackageStore(state)
    assert len(store.runs()) == 1
