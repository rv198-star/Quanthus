from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from quanthus.backtest.historical_bundle import HistoricalReplayBundle
from quanthus.backtest.historical_frames import HistoricalReplayFrameReader
from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest
from quanthus.event_library.contracts import event_record_from_dict
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_retained_audit as audit
from quanthus.expert_strategy import p3_retained_input as retained
from quanthus.expert_strategy.p3_strat_003_input_qualification import _bundle_manifest
from quanthus.industry_mapping import load_static_industry_mapping
from quanthus.platform import event_worker as worker_module
from quanthus.platform.event_worker import EventWorker, _structure_input, _structure_request_digest
from quanthus.platform.jobs import STRUCTURE_MVP_JOB, DurableJobQueue

from .test_p3_strat_003_candidate_input import _write_fixture
from .test_structure_mvp_worker import _qualification_user
from .test_structure_order_worker import DispatchStore

ROOT = Path(__file__).resolve().parents[2]


def _parquet(connection, sql: str, path: Path) -> Path:
    connection.execute("COPY (" + sql + ") TO '" + str(path) + "' (FORMAT PARQUET)")
    return path


@pytest.fixture
def fixture(tmp_path):
    connection = duckdb.connect(":memory:")
    connection.execute("""
      CREATE TABLE dates AS SELECT * FROM (VALUES
        (DATE '2024-12-30',1), (DATE '2024-12-31',2),
        (DATE '2025-01-02',3), (DATE '2025-01-03',4)) d(day,n);
      CREATE TABLE replay_calendar AS
        SELECT exchange,day AS trade_date,1 AS is_open
        FROM dates CROSS JOIN (VALUES ('SSE'),('SZSE')) v(exchange);
      CREATE TABLE replay_universe AS
        SELECT '600001' AS security_id,'600001' AS stock_code,
          'security-a' AS dwd_security_id,'listing-a' AS dwd_listing_id,
          'company-a' AS dwd_company_id,
          'venue_bec59e31eb6bb28111a10d7e6d2c689f' AS dwd_venue_id,
          'common_equity' AS dwd_instrument_type,'SSE' AS exchange,day AS market_date,
          TIMESTAMPTZ '2010-01-01 00:00:00+00' AS dwd_security_effective_from,
          TIMESTAMPTZ '2010-01-01 00:00:00+00' AS dwd_listing_effective_from,
          TIMESTAMPTZ '2026-08-03 00:00:00+00' AS dwd_security_knowledge_from,
          TIMESTAMPTZ '2026-08-03 00:00:00+00' AS dwd_listing_knowledge_from FROM dates;
      CREATE TABLE replay_bars AS
        SELECT u.*,market_date AS date, 1.0::DOUBLE AS open,2.0::DOUBLE AS high,
          1.0::DOUBLE AS low,1.5::DOUBLE AS close,100.0::DOUBLE AS volume,
          150.0::DOUBLE AS amount,1.0000000001::DECIMAL(38,10) AS dwd_open_decimal,
          2::DECIMAL(38,10) AS dwd_high_decimal,1::DECIMAL(38,10) AS dwd_low_decimal,
          1.5::DECIMAL(38,10) AS dwd_close_decimal,150::DECIMAL(38,10) AS dwd_turnover_decimal,
          TIMESTAMPTZ '2026-08-03 00:00:00+00' AS dwd_bar_knowledge_from,
          'record-' || market_date::VARCHAR AS dwd_bar_source_record_id,
          'revision-a' AS dwd_bar_source_revision,'sha256:source' AS dwd_bar_source_artifact_sha256
        FROM replay_universe u;
      CREATE TABLE replay_limits AS
        SELECT u.*,market_date AS execution_date,'1' AS observed_tradestatus,
          true AS is_executable,'TRADING' AS reason_code,
          TIMESTAMPTZ '2026-08-03 00:00:00+00' AS dwd_state_knowledge_from
        FROM replay_universe u;
      CREATE TABLE replay_action_exceptions_mapped(
        security_id VARCHAR, blocked_from DATE,blocked_through DATE);
      CREATE TABLE replay_unexplained_price_transitions(security_id VARCHAR,market_date DATE);
      CREATE TABLE factors AS SELECT * FROM (VALUES
        ('security-a',TIMESTAMPTZ '2025-01-01 16:00:00+00',
          TIMESTAMPTZ '2025-01-01 10:00:00+00',NULL::TIMESTAMPTZ),
        ('security-a',TIMESTAMPTZ '2025-01-01 16:10:00+00',
          TIMESTAMPTZ '2025-01-01 11:00:00+00',NULL::TIMESTAMPTZ))
        f(security_id,effective_from,knowledge_from,knowledge_to);
      CREATE TABLE candidate AS SELECT
        'security-a' AS dwd_security_id,'listing-a' AS dwd_listing_id,'600001' AS listing_code,
        'company-a' AS dwd_company_id,'SSE' AS venue_id,'common_equity' AS instrument_type,
        day AS session_date,n::BIGINT AS listing_sessions,
        1.0000000001::DECIMAL(38,10) AS open_price,2::DECIMAL(38,10) AS high_price,
        1::DECIMAL(38,10) AS low_price,1.5::DECIMAL(38,10) AS close_price,
        150::DECIMAL(38,10) AS turnover,true AS is_tradable,
        n=3 AS adjustment_effective,
        CASE WHEN n=3 THEN TIMESTAMPTZ '2025-01-01 16:00:00+00' END AS factor_effective_from,
        CASE WHEN n=3 THEN 2::BIGINT END AS factor_boundary_count,
        TIMESTAMPTZ '2026-08-03 00:00:00+00' AS dwd_bar_knowledge_from,
        TIMESTAMPTZ '2026-08-03 00:00:00+00' AS dwd_security_knowledge_from,
        TIMESTAMPTZ '2026-08-03 00:00:00+00' AS dwd_listing_knowledge_from,
        TIMESTAMPTZ '2026-08-03 00:00:00+00' AS dwd_state_knowledge_from,
        CASE WHEN n=3 THEN TIMESTAMPTZ '2025-01-01 11:00:00+00' END AS dwd_factor_knowledge_from,
        'record-' || day::VARCHAR AS dwd_bar_source_record_id,
        'revision-a' AS dwd_bar_source_revision,'sha256:source' AS dwd_bar_source_artifact_sha256,
        TIMESTAMPTZ '2010-01-01 00:00:00+00' AS dwd_security_effective_from,
        TIMESTAMPTZ '2010-01-01 00:00:00+00' AS dwd_listing_effective_from FROM dates;
    """)
    calendar_sql = """SELECT venue_id,day AS trade_date,true AS is_open,
      NULL::TIMESTAMPTZ AS knowledge_to FROM dates CROSS JOIN (VALUES
      ('venue_bec59e31eb6bb28111a10d7e6d2c689f'),
      ('venue_8c0bd9088a0dc88f28e5e7fa8efdce87')) v(venue_id)"""
    base = _parquet(connection, calendar_sql + " WHERE n<=3", tmp_path / "base.parquet")
    daily = _parquet(connection, calendar_sql + " WHERE n=4", tmp_path / "daily.parquet")
    factor = _parquet(connection, "SELECT * FROM factors", tmp_path / "factor.parquet")
    scope = {
        "history_start": "2024-12-30",
        "decision_start": "2025-01-01",
        "decision_end": "2025-01-02",
        "execution_end": "2025-01-03",
        "base_through": "2025-01-02",
        "warmup_open_sessions": 2,
    }
    yield connection, base, daily, factor, scope, tmp_path
    connection.close()


def _projection(fixture):
    c, base, daily, factor, scope, tmp = fixture
    calendar = audit.calendar_audit(c, [base], [daily], scope)
    candidate = _parquet(c, "SELECT * FROM candidate", tmp / "candidate.parquet")
    return audit.projection_audit(c, candidate, factor), calendar


def test_exact_decimal_projection_and_raw_visibility_ledger(fixture):
    projection, calendar = _projection(fixture)
    assert projection["row_count"] == 4
    assert projection["compared_field_count"] == 27
    assert not any(projection["mismatch_counts"].values())
    assert calendar["date_pairs"] == [["2025-01-02", "2025-01-03"]]
    ledger = audit.coverage_ledger(fixture[0])
    assert ledger["yearly"][1]["late_dwd_bar_knowledge_from"] == 2
    assert ledger["yearly"][1]["late_dwd_factor_knowledge_from"] == 0
    assert ledger["yearly"][1]["securities"] == 1
    assert ledger["target_maturity"].startswith("NOT_EVALUATED")
    json.dumps(ledger)


@pytest.mark.parametrize(
    "assignment",
    [
        "open_price=open_price+0.0000000001",
        "dwd_bar_source_record_id='changed'",
        "dwd_company_id='other'",
        "dwd_bar_knowledge_from=NULL",
        "listing_sessions=99",
        "factor_boundary_count=1",
        "is_tradable=false",
        "listing_code='security-a'",
    ],
)
def test_projection_rejects_each_material_drift(fixture, assignment):
    fixture[0].execute("UPDATE candidate SET " + assignment + " WHERE session_date='2025-01-02'")
    with pytest.raises(ValueError, match="RETAINED_PROJECTION_DIFFERS"):
        _projection(fixture)


def test_stable_identity_cannot_be_substituted_by_legacy_code(fixture):
    fixture[0].execute("UPDATE replay_bars SET dwd_security_id='600001'")
    with pytest.raises(ValueError, match="SOURCE_STABLE_IDENTITY_DIFFERS"):
        _projection(fixture)


@pytest.mark.parametrize(
    "sql,reason",
    [
        ("DELETE FROM candidate WHERE session_date='2025-01-02'", "RETAINED_PROJECTION_DIFFERS"),
        ("INSERT INTO candidate SELECT * FROM candidate LIMIT 1", "STABLE_IDENTITY_DUPLICATE"),
        (
            "UPDATE candidate SET session_date='2025-01-04' WHERE session_date='2025-01-03'",
            "PROJECTION_DATE_OUTSIDE_EXACT_CALENDAR",
        ),
    ],
)
def test_candidate_pair_set_is_exact(fixture, sql, reason):
    fixture[0].execute(sql)
    with pytest.raises(ValueError, match=reason):
        _projection(fixture)


@pytest.mark.parametrize("day", ["2024-12-31", "2025-01-02"])
def test_calendar_catches_day_missing_on_both_venues(fixture, day):
    c, base, daily, _, scope, _ = fixture
    c.execute("DELETE FROM replay_calendar WHERE trade_date=?", [day])
    with pytest.raises(ValueError, match="RETAINED_OPEN_CALENDAR_SET_DIFFERS"):
        audit.calendar_audit(c, [base], [daily], scope)


def test_authoritative_calendar_duplicate_is_not_deduplicated(fixture):
    c, base, daily, _, scope, _ = fixture
    with pytest.raises(ValueError, match="AUTHORITATIVE_CALENDAR_DUPLICATE"):
        audit.calendar_audit(c, [base, base], [daily], scope)


def test_empty_authoritative_day_cannot_disappear_from_ledger(fixture):
    c = fixture[0]
    c.execute("DELETE FROM replay_universe WHERE market_date='2025-01-03'")
    c.execute("DELETE FROM candidate WHERE session_date='2025-01-03'")
    _projection(fixture)
    with pytest.raises(ValueError, match="EMPTY_AUTHORITATIVE_OPEN_SESSION"):
        audit.coverage_ledger(c)


def test_reason_counts_overlap_and_nonfinite_is_invalid(fixture):
    c = fixture[0]
    c.execute("UPDATE replay_bars SET open='NaN' WHERE date='2025-01-02'")
    c.execute(
        "UPDATE replay_limits SET is_executable=false, reason_code='ZERO_ACTIVITY_OBSERVATION'"
    )
    _projection(fixture)
    ledger = audit.coverage_ledger(c)
    assert ledger["yearly"][1]["invalid_bar"] == 1
    assert ledger["yearly"][1]["zero_activity"] == 2
    assert ledger["yearly"][1]["source_non_executable"] == 2
    assert ledger["yearly"][1]["universe_pairs"] == 2
    assert ledger["reason_counts_overlap"] is True


def test_zero_price_is_counted_invalid_like_reader_audit(fixture):
    fixture[0].execute("UPDATE replay_bars SET open=0,low=0 WHERE date='2025-01-02'")
    _projection(fixture)
    assert audit.coverage_ledger(fixture[0])["yearly"][1]["invalid_bar"] == 1


def test_dated_code_change_keeps_two_listing_histories(fixture):
    c = fixture[0]
    for table, date_column in (
        ("replay_universe", "market_date"),
        ("replay_bars", "date"),
        ("replay_limits", "execution_date"),
    ):
        c.execute(f"""UPDATE {table} SET security_id='600002',stock_code='600002',
          dwd_listing_id='listing-b' WHERE {date_column}>='2025-01-01'""")
    c.execute("""UPDATE candidate SET listing_code='600002',dwd_listing_id='listing-b',
      listing_sessions=listing_sessions-2 WHERE session_date>='2025-01-01'""")
    result, _ = _projection(fixture)
    assert result["row_count"] == 4


def test_bound_request_and_worker_accept_exact_entry_only():
    raw = retained.request(ROOT / retained.REQUEST_CONFIG)
    assert len(raw["files"]) == 52
    assert sum(m["size_bytes"] for m in raw["files"]) == 304584475
    job = {"code_revision": "a" * 40, "run_config": retained.REQUEST_CONFIG}
    assert _structure_input(job) == job
    assert _structure_request_digest(retained.REQUEST_CONFIG) == digest_value(raw)
    assert raw["authority"]["formal_dwd_repair"] is False
    assert raw["authority"]["D_activation"] is False
    with pytest.raises(RuntimeError, match="run_config_not_governed"):
        _structure_input(
            {**job, "run_config": "config/research/p3-strat-003/retained-input.v2.json"}
        )


def test_byte_guard_rejects_corruption_and_symlink_before_row_access(tmp_path):
    original = tmp_path / "original"
    original.write_bytes(b"original")
    member = {"path": "original", "size_bytes": 8, "sha256": retained._sha256(original)}
    assert retained.verify_inputs(tmp_path, {"files": [member]}) == 8
    original.write_bytes(b"modified")
    with pytest.raises(ValueError, match="RETAINED_SOURCE_DRIFT"):
        retained.verify_inputs(tmp_path, {"files": [member]})
    link = tmp_path / "link"
    link.symlink_to(original)
    with pytest.raises(ValueError, match="RETAINED_SOURCE_SYMLINK"):
        retained.verify_inputs(tmp_path, {"files": [{**member, "path": "link"}]})


def test_single_attempt_survives_new_output_and_fsyncs_directory(tmp_path, monkeypatch):
    calls = []
    real_fsync = retained.os.fsync

    def fsync(fd):
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(retained.os, "fsync", fsync)
    receipt = tmp_path / "attempts" / "once.json"
    retained.claim_attempt(receipt, output=tmp_path / "first", revision="a" * 40)
    assert len(calls) == 2
    with pytest.raises(ValueError, match="RETAINED_B_ATTEMPT_ALREADY_SPENT"):
        retained.claim_attempt(receipt, output=tmp_path / "different", revision="b" * 40)


def test_deployed_revision_must_match_before_any_output(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTHUS_CODE_REVISION", "b" * 40)
    with pytest.raises(ValueError, match="DEPLOYED_EVENT_IMAGE_REVISION_MISMATCH"):
        retained.build(
            request_path=ROOT / retained.REQUEST_CONFIG,
            output=tmp_path / "output",
            code_revision="a" * 40,
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("orphan_staging", [False, True])
def test_worker_dispatch_and_sealed_recovery_never_relaunch(tmp_path, monkeypatch, orphan_staging):
    store = DispatchStore()
    user = _qualification_user()
    store.create_user(user)
    job_input = {"code_revision": "a" * 40, "run_config": retained.REQUEST_CONFIG}
    queue = DurableJobQueue(store, {STRUCTURE_MVP_JOB: "event"})
    job, _ = queue.submit(
        kind=STRUCTURE_MVP_JOB,
        actor_user_id=user.user_id,
        idempotency_key="retained-input-fixture",
        input_ref=job_input,
    )
    worker = EventWorker(
        store,
        worker_id="test",
        run_root=tmp_path,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )
    commands = []

    def verify(root, *, code_revision, request_digest):
        assert code_revision == "a" * 40
        assert request_digest == _structure_request_digest(retained.REQUEST_CONFIG)
        return {
            "artifact_ref": str(root),
            "row_count": 4,
            "candidate_event_count": 1,
            "p2_probe_count": 0,
            "p2_fill_count": 0,
            "B_accepted": False,
        }

    class Child:
        returncode = 0

        def __init__(self, command, **_):
            commands.append(command)
            assert command[2] == "quanthus.expert_strategy.p3_retained_input"
            assert command[command.index("--request") + 1].endswith(retained.REQUEST_CONFIG)
            Path(command[command.index("--output") + 1]).mkdir(parents=True)

        def poll(self):
            return 0

    monkeypatch.setattr(worker_module, "verify_retained_input_artifact", verify)
    monkeypatch.setattr(worker_module.subprocess, "Popen", Child)
    if orphan_staging:
        prior = tmp_path / ".staging" / job.job_id / "1" / "p3-retained-input"
        prior.mkdir(parents=True)
        monkeypatch.setattr(worker_module, "recover_retained_staging", lambda *_args: prior)
    assert worker.run_once()
    observed = store.get_job(job.job_id)
    assert observed.state == "succeeded"
    store.update_job(replace(observed, state="running"))
    worker._execute_structure(
        job.job_id, observed.claim_generation, job_input, observed.input_digest
    )
    assert store.get_job(job.job_id).state == "succeeded"
    assert len(commands) == (0 if orphan_staging else 1)


def test_spent_attempt_recovery_is_bound_to_same_job_and_sealed_bytes(tmp_path, monkeypatch):
    job = "job_" + "a" * 32
    prior = tmp_path / ".staging" / job / "1" / "p3-retained-input"
    prior.mkdir(parents=True)
    receipt = tmp_path / "once.json"
    retained.claim_attempt(receipt, output=prior, revision="a" * 40)
    monkeypatch.setattr(retained, "request", lambda _: {"attempt_receipt": str(receipt)})
    verified = []
    monkeypatch.setattr(
        retained, "verify_retained_input_artifact", lambda path, **kw: verified.append((path, kw))
    )
    with pytest.raises(ValueError, match="HAS_NO_SEALED_RESULT"):
        retained.recover_retained_staging(tmp_path, job, 2, "a" * 40)
    (prior / "READY").write_text("sealed")
    assert retained.recover_retained_staging(tmp_path, job, 2, "a" * 40) == prior
    assert len(verified) == 1
    with pytest.raises(ValueError, match="IDENTITY_DIFFERS"):
        retained.recover_retained_staging(tmp_path, "job_" + "b" * 32, 2, "a" * 40)


def test_parent_worker_enforces_retained_wall_deadline(tmp_path, monkeypatch):
    store = DispatchStore()
    user = _qualification_user()
    store.create_user(user)
    queue = DurableJobQueue(store, {STRUCTURE_MVP_JOB: "event"})
    job, _ = queue.submit(
        kind=STRUCTURE_MVP_JOB,
        actor_user_id=user.user_id,
        idempotency_key="retained-deadline-fixture",
        input_ref={"code_revision": "a" * 40, "run_config": retained.REQUEST_CONFIG},
    )
    worker = EventWorker(
        store,
        worker_id="test",
        run_root=tmp_path,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )
    terminated = []

    class Child:
        returncode = None

        def __init__(self, *_args, **_kwargs):
            pass

        def poll(self):
            return self.returncode

        def terminate(self):
            terminated.append(True)
            self.returncode = -15

        def wait(self, **_kwargs):
            return self.returncode

    monkeypatch.setattr(
        worker_module, "retained_input_request", lambda _: {"budget": {"wall_seconds": 0}}
    )
    monkeypatch.setattr(worker_module, "recover_retained_staging", lambda *_: None)
    monkeypatch.setattr(worker_module.subprocess, "Popen", Child)
    monkeypatch.setattr(worker_module.time, "sleep", lambda _: None)
    assert worker.run_once()
    assert terminated == [True]
    observed = store.get_job(job.job_id)
    assert observed.state == "failed"
    assert observed.error_reason == "retained_input_wall_budget_exceeded_no_automatic_retry"


def _real_reader_bundle(fixture):
    c, _, _, _, _, tmp = fixture
    c.execute("""
      ALTER TABLE replay_limits ADD COLUMN entry_policy_allows BOOLEAN DEFAULT true;
      ALTER TABLE replay_limits ADD COLUMN upper_limit_pct DECIMAL(38,10) DEFAULT 10;
      ALTER TABLE replay_limits ADD COLUMN lower_limit_pct DECIMAL(38,10) DEFAULT 10;
      ALTER TABLE replay_limits ADD COLUMN upper_limit_price DECIMAL(38,10) DEFAULT 2;
      ALTER TABLE replay_limits ADD COLUMN lower_limit_price DECIMAL(38,10) DEFAULT 1;
      ALTER TABLE replay_action_exceptions_mapped ADD COLUMN exception_id VARCHAR;
      ALTER TABLE replay_action_exceptions_mapped ADD COLUMN security_code VARCHAR;
      ALTER TABLE replay_action_exceptions_mapped ADD COLUMN reason_code VARCHAR;
      ALTER TABLE replay_action_exceptions_mapped ADD COLUMN replay_disposition VARCHAR;
      ALTER TABLE replay_action_exceptions_mapped ADD COLUMN execution_authorized BOOLEAN;
      CREATE TABLE actions(action_id VARCHAR,security_code VARCHAR,effective_date DATE,
        known_at TIMESTAMPTZ,cash_dividend_per_share DECIMAL(38,10),
        bonus_share_ratio DECIMAL(38,10),source_revision VARCHAR,source_snapshot_id VARCHAR,
        evidence_digest VARCHAR,execution_authorized BOOLEAN);
      CREATE TABLE validity AS SELECT '600001' AS security_id,'600001' AS stock_code,
        'SSE' AS exchange, DATE '2010-01-01' AS valid_from,NULL::DATE AS valid_to;
    """)
    root = tmp / "bundle"
    root.mkdir()
    datasets = []
    cutoff = datetime(2026, 9, 18, 7, tzinfo=timezone.utc)
    for dataset, table in {
        "corporate_actions_normalized": "actions",
        "corporate_action_exceptions": "replay_action_exceptions_mapped",
        "daily_ohlcv_none": "replay_bars",
        "daily_universe": "replay_universe",
        "price_limit_execution_daily": "replay_limits",
        "trading_calendar": "replay_calendar",
        "security_code_validity": "validity",
    }.items():
        path = _parquet(c, "SELECT * FROM " + table, root / (dataset + ".parquet"))
        datasets.append(
            DatasetManifest(
                dataset=dataset,
                version="synthetic-v1",
                source_revisions={"fixture": "test"},
                row_count=audit.count(c, "SELECT count(*) FROM " + table),
                partitions=(path.name,),
                schema_hash=retained._schema_digest(c, path),
                content_checksums={path.name: retained._sha256(path)},
                created_at=cutoff,
                knowledge_cutoff=cutoff,
            )
        )
    return HistoricalReplayBundle(
        root=root,
        snapshot=SnapshotRef(
            market_date=date(2026, 9, 18),
            knowledge_cutoff=cutoff,
            source_revision="sha256:" + "a" * 64,
            snapshot_id="synthetic-retained",
        ),
        universe_policy_version="synthetic-v1",
        datasets=tuple(datasets),
    )


def test_shared_report_matches_real_reader_and_scanner_cleans_tmp(fixture):
    c, base, daily, factor, scope, tmp = fixture
    bundle = _real_reader_bundle(fixture)
    candidate = _parquet(c, "SELECT * FROM candidate", tmp / "candidate-daily.parquet")
    with HistoricalReplayFrameReader(bundle) as reader:
        conn = reader._ensure_open()
        calendar = audit.calendar_audit(conn, [base], [daily], scope)
        audit.projection_audit(conn, candidate, factor)
        audit.coverage_ledger(conn)
        shared = audit.replay_report(reader, date(2025, 1, 1), date(2025, 1, 2))
        independent = reader.audit(date(2025, 1, 1), date(2025, 1, 2))
        assert shared.to_dict() == independent.to_dict()
    mapping_unsigned = {
        "schema_version": "1.0.0",
        "mapping_version": "static-level1-concentration-research-v1",
        "usage": "static_non_pit_concentration_only",
        "source": {
            "provider": "synthetic",
            "endpoint": "test",
            "retrieved_at": "2026-10-01T00:00:00Z",
            "source_revision": "test",
        },
        "memberships": [
            {
                "security_id": "security-a",
                "stock_code": "600001",
                "exchange": "SSE",
                "level1_industry_id": "industry-a",
            }
        ],
    }
    mapping = tmp / "mapping.json"
    mapping.write_text(
        json.dumps({**mapping_unsigned, "artifact_digest": digest_value(mapping_unsigned)})
    )
    industry = load_static_industry_mapping(
        mapping, expected_version="static-level1-concentration-research-v1"
    )
    scan = retained._scan(tmp, ROOT, bundle, calendar, industry, "sha256:" + "a" * 64, scope)
    assert len(scan.candidates) == 0  # Four rows cannot satisfy the frozen candidate history gate.
    assert not list(tmp.glob(".candidate-scan-*"))


def test_complete_synthetic_artifact_seal_recovery_and_resealed_mapping_drift(fixture, monkeypatch):
    """Exercise the complete synthetic input and seal path; stub only candidate triggers."""
    c, base, _, _, scope, tmp = fixture
    c.execute("UPDATE dates SET day='2026-09-18' WHERE n=4")
    for table, column in (
        ("replay_calendar", "trade_date"),
        ("replay_universe", "market_date"),
        ("replay_bars", "date"),
        ("replay_limits", "execution_date"),
        ("candidate", "session_date"),
    ):
        c.execute(f"UPDATE {table} SET {column}='2026-09-18' WHERE {column}='2025-01-03'")
    for table in ("candidate", "replay_bars"):
        c.execute(f"""UPDATE {table} SET dwd_bar_source_record_id='record-2026-09-18'
                      WHERE dwd_bar_source_record_id='record-2025-01-03'""")
    bundle = _real_reader_bundle(fixture)
    source = tmp / "source"
    source.mkdir()
    retained.shutil.copytree(bundle.root, source / "replay")
    retained._write(
        source / "replay/bundle.json",
        _bundle_manifest(bundle, request_digest=retained.REQUEST_DIGEST),
    )
    _parquet(c, "SELECT * FROM candidate", source / "candidate-daily.parquet")
    _parquet(c, "SELECT * FROM factors", source / "factor.parquet")
    retained.shutil.copyfile(base, source / "base.parquet")
    _parquet(
        c,
        """SELECT venue_id,DATE '2026-09-18' AS trade_date,true AS is_open FROM (VALUES
       ('venue_bec59e31eb6bb28111a10d7e6d2c689f'),
       ('venue_8c0bd9088a0dc88f28e5e7fa8efdce87')) v(venue_id)""",
        source / "daily.parquet",
    )
    industry_root = source / "industry"
    industry_root.mkdir()
    helper = tmp / "candidate-fixture"
    helper.mkdir()
    _write_fixture(helper)
    mapping = json.loads((helper / "industry.json").read_bytes())
    mapping["memberships"][0].update(security_id="security-a", stock_code="600001", exchange="SSE")
    mapping["artifact_digest"] = digest_value(
        {k: v for k, v in mapping.items() if k != "artifact_digest"}
    )
    retained._write(industry_root / "static_industry_mapping.json", mapping)
    retained._write(
        industry_root / "receipt.json",
        {
            "source_counts": {"synthetic": 1},
            "disposition": "COMPLETE_TEMPORARY_STATIC_INDUSTRY_CLOSURE",
            "missing_count": 0,
            "duplicate_count": 0,
            "conflict_count": 0,
        },
    )
    proof = {
        "daily_chain": [{"market_date": "2026-09-18"}],
        "target_manifest": {"sha256": "sha256:" + "a" * 64},
        "target_sync": {"data": {"market_date": "2026-09-18"}},
        "semantic_equivalence": {
            "manifest": {
                "data": {
                    "result_digest": "sha256:" + "b" * 64,
                    "disposition": "PASS_CONSUMER_SEMANTIC_EQUIVALENCE_PROBE",
                }
            },
            "summary": {
                "data": {
                    "missing_boundary": {
                        "original_child_binding": {},
                        "research_reconstruction": {},
                    }
                }
            },
        },
    }
    retained._write(source / "source-lineage.json", proof)
    raw = retained.request(ROOT / retained.REQUEST_CONFIG)
    raw.update(
        input_root=str(source),
        attempt_receipt=str(tmp / "attempt.json"),
        files=[],
        scope={**scope, "decision_end": "2026-09-17", "execution_end": "2026-09-18"},
        expected={
            "candidate_rows": 4,
            "candidate_securities": 1,
            "decision_securities": 1,
            "daily_package_count": 1,
            "source_counts": {"synthetic": 1},
            "bundle_digest": bundle.digest,
        },
    )
    for path in sorted(source.rglob("*")):
        if path.is_file():
            raw["files"].append(
                {
                    "path": path.relative_to(source).as_posix(),
                    "sha256": retained._sha256(path),
                    "size_bytes": path.stat().st_size,
                    "role": {
                        "base.parquet": "base_calendar",
                        "daily.parquet": "daily_calendar",
                    }.get(path.name, "synthetic"),
                }
            )
    record = event_record_from_dict(json.loads((helper / "events.jsonl").read_bytes())["event"])
    record = replace(record, identity=replace(record.identity, security_id="security-a"))
    scan = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                record=record, listing_code="600001", static_level1_industry_id="industry-1"
            )
        ],
        counters=SimpleNamespace(
            evaluated_rows=4,
            raw_triggers=1,
            isolated_candidates=1,
            concentration_retained=1,
            candidate_events=1,
            reason_counts={},
        ),
    )
    monkeypatch.setattr(retained, "request", lambda _: raw)
    calls = []
    monkeypatch.setattr(retained, "_scan", lambda *_args: calls.append(True) or scan)
    monkeypatch.setenv("QUANTHUS_CODE_REVISION", "a" * 40)
    output = tmp / "result"
    result = retained.build(
        request_path=ROOT / retained.REQUEST_CONFIG, output=output, code_revision="a" * 40
    )
    assert result["candidate_event_count"] == 1
    assert result["B_accepted"] is False
    assert result["execution_authorized"] is False
    retained.build(
        request_path=ROOT / retained.REQUEST_CONFIG, output=output, code_revision="a" * 40
    )
    assert calls == [True]
    # Even consistent resealing of the candidate and outer manifests cannot substitute the S2 map.
    wrong = output / "candidate-input/static_industry_mapping.json"
    changed = json.loads(wrong.read_bytes())
    changed["source"]["source_revision"] = "changed"
    changed["artifact_digest"] = digest_value(
        {k: v for k, v in changed.items() if k != "artifact_digest"}
    )
    wrong.write_text(json.dumps(changed))
    candidate = json.loads((output / "candidate-input/manifest.json").read_bytes())
    candidate["industry_mapping"]["sha256"] = retained._sha256(wrong)
    candidate["semantic_digest"] = digest_value(
        {k: v for k, v in candidate.items() if k != "semantic_digest"}
    )
    (output / "candidate-input/manifest.json").write_text(json.dumps(candidate))
    summary = json.loads((output / "summary.json").read_bytes())
    summary["candidate_manifest_digest"] = candidate["semantic_digest"]
    (output / "summary.json").write_text(json.dumps(summary))
    manifest = json.loads((output / "manifest.json").read_bytes())
    manifest["candidate_manifest_digest"] = candidate["semantic_digest"]
    for member in manifest["files"].values():
        member["sha256"] = retained._sha256(output / member["path"])
    manifest["result_digest"] = digest_value(
        {k: v for k, v in manifest.items() if k != "result_digest"}
    )
    (output / "manifest.json").write_text(json.dumps(manifest))
    (output / "READY").write_text(retained._sha256(output / "manifest.json") + "\n")
    with pytest.raises(ValueError, match="RETAINED_EVIDENCE_CROSS_BINDING_DIFFERS"):
        retained.verify_retained_input_artifact(
            output, code_revision="a" * 40, request_digest=digest_value(raw)
        )
