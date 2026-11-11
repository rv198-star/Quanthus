"""Semantic goldens and fail-closed R1 worker contracts (synthetic data only)."""

from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
import types
from datetime import date, timedelta
from pathlib import Path

import pytest

from quanthus import hf_dwd_research as worker
from quanthus.hf_dwd_research_sql import SSE, SZSE, calculate

duckdb = pytest.importorskip("duckdb")

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def specimen():
    return json.loads((ROOT / "config/research/research-r1/request.v2.json").read_text())


@pytest.fixture
def synthetic(tmp_path, specimen):
    q = copy.deepcopy(specimen)
    q["scope"].update(
        read_start="2020-01-02",
        decision_start="2020-03-02",
        decision_end="2020-03-31",
        execution_end="2020-04-30",
    )
    c = duckdb.connect()
    c.execute("SET TimeZone='Asia/Shanghai'")
    c.execute("""CREATE TABLE dwd_trading_calendar(calendar_session_id VARCHAR,venue_id VARCHAR,
                 trade_date DATE,is_open BOOLEAN,knowledge_to TIMESTAMP)""")
    c.execute("""CREATE TABLE dwd_listing(listing_id VARCHAR,security_id VARCHAR,venue_id VARCHAR,
                 effective_from DATE,effective_to DATE,knowledge_to TIMESTAMP)""")
    c.execute("""CREATE TABLE dwd_security(security_id VARCHAR,instrument_type VARCHAR,
                 effective_from DATE,effective_to DATE,knowledge_to TIMESTAMP)""")
    c.execute("""CREATE TABLE dwd_security_bar(listing_id VARCHAR,calendar_session_id VARCHAR,
                 observation_start DATE,open DOUBLE,close DOUBLE,turnover DOUBLE,
                 interval_size VARCHAR,price_basis VARCHAR,knowledge_to TIMESTAMP)""")
    c.execute("""CREATE TABLE dwd_tradability_state(listing_id VARCHAR,calendar_session_id VARCHAR,
                 observation_start DATE,upper_price DOUBLE,is_tradable BOOLEAN,
                 can_buy BOOLEAN,knowledge_to TIMESTAMP)""")
    days = []
    d = date(2019, 1, 1)
    while d <= date(2020, 4, 30):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    c.executemany(
        "INSERT INTO dwd_trading_calendar VALUES(?,?,?,?,?)",
        [(v + str(d), v, d, True, None) for v in (SSE, SZSE) for d in days],
    )
    c.execute("INSERT INTO dwd_listing VALUES('L','S',?,'2010-01-01',NULL,NULL)", [SSE])
    c.execute(
        "INSERT INTO dwd_security VALUES('S','common_equity','2010-01-01','2020-03-16',NULL),"
        "('S','common_equity','2020-03-16',NULL,NULL)"
    )
    market = [x for x in days if x >= date(2020, 1, 2)]
    for i, d in enumerate(market):
        price = 10 * 1.003**i
        sid = SSE + str(d)
        c.execute(
            "INSERT INTO dwd_security_bar VALUES(?,?,?,?,?,?,?,?,?)",
            ["L", sid, d, price, price, 2e8, "1d", "UNADJUSTED", None],
        )
        c.execute(
            "INSERT INTO dwd_tradability_state VALUES(?,?,?,?,?,?,?)",
            ["L", sid, d, price * 1.1, True, True, None],
        )

    def run():
        paths = {}
        for table in [
            "dwd_security_bar",
            "dwd_trading_calendar",
            "dwd_tradability_state",
            "dwd_listing",
            "dwd_security",
        ]:
            p = tmp_path / (table + ".parquet")
            c.execute(f"COPY {table} TO '{p}' (FORMAT PARQUET)")
            paths[table] = [str(p)]
        result_connection = duckdb.connect()
        try:
            return calculate(result_connection, q, paths), result_connection
        except Exception:
            result_connection.close()
            raise

    yield c, run, q
    c.close()


def test_calendar_age_and_nonoverlapping_security_golden(synthetic):
    _, run, _ = synthetic
    result, c = run()
    assert result["duplicate_listing_date_keys"] == 0
    assert result["candidate"]["opportunities"] == 22
    assert result["candidate"]["price_guard_pass"] == 22
    assert result["candidate"]["reasons"] == {"pass": 22}
    assert c.execute("SELECT min(listing_age_floor_sessions) FROM labelled").fetchone()[0] > 120
    assert abs(c.execute("SELECT max(return_vol20) FROM labelled").fetchone()[0]) < 1e-10
    c.close()


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        ("b.open", "NULL", "missing_or_invalid_open"),
        ("b.open", "0", "missing_or_invalid_open"),
        ("b.open", "'NaN'::DOUBLE", "missing_or_invalid_open"),
        ("t.is_tradable", "false", "suspended"),
        ("t.can_buy", "false", "cannot_buy"),
        ("t.can_buy", "NULL", "missing_state"),
        ("t.upper_price", "NULL", "missing_or_invalid_upper"),
        ("t.upper_price", "0", "missing_or_invalid_upper"),
        ("t.upper_price", "1", "upper_price_block"),
    ],
)
def test_next_open_reasons_are_mutually_exclusive(synthetic, column, value, reason):
    source, run, _ = synthetic
    alias, name = column.split(".")
    table = "dwd_security_bar" if alias == "b" else "dwd_tradability_state"
    source.execute(f"UPDATE {table} SET {name}={value} WHERE observation_start=DATE '2020-03-04'")
    result, c = run()
    metrics = result["candidate"]
    expected_opportunities = 21 if reason == "suspended" else 22
    assert metrics["opportunities"] == expected_opportunities and metrics["blocked"] == 1
    assert metrics["reasons"] == {"pass": expected_opportunities - 1, reason: 1}
    assert sum(metrics["reasons"].values()) == metrics["opportunities"]
    c.close()


def test_next_session_survives_identity_expiry(synthetic):
    source, run, _ = synthetic
    source.execute("UPDATE dwd_listing SET effective_to=DATE '2020-03-16'")
    result, c = run()
    assert result["candidate"]["opportunities"] == 10
    assert result["candidate"]["reasons"] == {"pass": 9, "missing_next_identity": 1}
    c.close()


def test_overlapping_security_fails_instead_of_distinct(synthetic):
    source, run, _ = synthetic
    source.execute("INSERT INTO dwd_security VALUES('S','common_equity','2010-01-01',NULL,NULL)")
    with pytest.raises(ValueError, match="ambiguous listing/date"):
        run()


def test_duplicate_calendar_fails(synthetic):
    source, run, _ = synthetic
    source.execute("INSERT INTO dwd_trading_calendar SELECT * FROM dwd_trading_calendar LIMIT 1")
    with pytest.raises(ValueError, match="ambiguous calendar"):
        run()


def test_missing_price_warmup_fails_closed(synthetic):
    source, run, _ = synthetic
    source.execute(
        "UPDATE dwd_security_bar SET close=NULL WHERE observation_start=DATE '2020-02-28'"
    )
    result, c = run()
    assert result["candidate"]["opportunities"] < 22
    assert (
        c.execute(
            "SELECT count(*) FROM labelled WHERE selected AND positive_closes21<21"
        ).fetchone()[0]
        == 0
    )
    c.close()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda q: q["authority"].update(revision="main"),
        lambda q: q["scope"].update(execution_end="2025-01-01"),
        lambda q: q["scope"].update(partition="arbitration"),
        lambda q: q["budget"].update(max_download_bytes=-1),
        lambda q: q["budget"].update(max_publish_attempts=4),
        lambda q: q["publish"].update(path_prefix="runs/other"),
        lambda q: q["screen"].update(max_unadjusted_close_return_vol20=float("nan")),
    ],
)
def test_request_scope_identity_resource_rejection(specimen, mutation):
    mutation(specimen)
    with pytest.raises(worker.GuardError):
        worker.validate_request(specimen)


def test_successful_request_is_accepted(specimen):
    worker.validate_request(specimen)


def test_bad_download_preserves_old_file_and_partial(tmp_path, monkeypatch):
    destination = tmp_path / "member"
    destination.write_bytes(b"old valid bytes")

    class Opener:
        def open(self, *a, **k):
            return io.BytesIO(b"wrong")

    monkeypatch.setattr(worker.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(worker.GuardError, match="hash differs"):
        worker.download_file(
            "https://huggingface.co/test",
            destination,
            "sha256:" + "0" * 64,
            5,
            {"downloaded": 0},
            100,
        )
    assert destination.read_bytes() == b"old valid bytes"
    assert destination.with_name("member.partial").read_bytes() == b"wrong"


def test_actual_network_byte_limit(tmp_path, monkeypatch):
    class Opener:
        def open(self, *a, **k):
            return io.BytesIO(b"123456")

    monkeypatch.setattr(worker.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(worker.GuardError, match="network byte budget"):
        worker.download_file(
            "https://huggingface.co/test", tmp_path / "file", None, None, {"downloaded": 0}, 3
        )
    assert not (tmp_path / "file").exists()


def test_stage_tamper_and_mixed_identity_fail(tmp_path):
    identity = {"id": "one"}
    worker.write(tmp_path / "r.json", worker.seal({"identity": identity, "value": 1}))
    assert worker.bound_read(tmp_path, "r.json", identity)["value"] == 1
    with pytest.raises(worker.GuardError, match="identity differs"):
        worker.bound_read(tmp_path, "r.json", {"id": "two"})
    d = worker.read(tmp_path / "r.json")
    d["value"] = 2
    worker.write(tmp_path / "r.json", d)
    with pytest.raises(worker.GuardError, match="content differs"):
        worker.bound_read(tmp_path, "r.json", identity)


def prepare_package(tmp_path, specimen, monkeypatch):
    identity = {"identity_digest": "same", "git": "a" * 40}
    worker.write(
        tmp_path / "fetch.json",
        worker.seal({"schema": "quanthus.research-fetch.v2", "identity": identity, "members": []}),
    )
    result = worker.seal(
        {"schema": "quanthus.research-result.v2", "identity": identity, "value": 42}
    )
    worker.write(tmp_path / "result.json", result)
    worker.write(
        tmp_path / "run.json",
        worker.seal(
            {
                "schema": "quanthus.research-run.v2",
                "identity": identity,
                "result_digest": result["digest"],
            }
        ),
    )
    worker.write(tmp_path / "resources.json", {"screen": {"wall_seconds": 0.1}})
    worker.write(tmp_path / "attempts.json", [])
    monkeypatch.setattr(worker, "verify_members", lambda *a: None)
    return identity, worker.build_package(specimen, [], identity, tmp_path)


def test_package_retry_bytes_are_identical(tmp_path, specimen, monkeypatch):
    identity, package = prepare_package(tmp_path, specimen, monkeypatch)
    first = {p.name: p.read_bytes() for p in package.iterdir()}
    worker.write(tmp_path / "attempts.json", [{"phase": "publish", "failure": "network"}])
    worker.build_package(specimen, [], identity, tmp_path)
    assert first == {p.name: p.read_bytes() for p in package.iterdir()}
    assert all(specimen["task_id"] in name and specimen["attempt_id"] in name for name in first)
    worker.verify_package(package)


def test_package_tamper_and_upload_budget_fail(tmp_path, specimen, monkeypatch):
    _, package = prepare_package(tmp_path, specimen, monkeypatch)
    p = package / worker.artifact_name(specimen, "result")
    p.write_text("{}")
    with pytest.raises(worker.GuardError, match="member differs"):
        worker.verify_package(package)


def test_upload_uses_returned_commit_and_all_file_readback(tmp_path, specimen, monkeypatch):
    identity, package = prepare_package(tmp_path, specimen, monkeypatch)
    commits = []
    reads = []

    class API:
        def __init__(self, **k):
            pass

        def dataset_info(self, *a):
            return types.SimpleNamespace(sha="b" * 40, private=True)

        def list_repo_files(self, **kw):
            return (
                [specimen["publish"]["path_prefix"] + "/" + p.name for p in package.iterdir()]
                if kw["revision"] == "c" * 40
                else []
            )

        def file_exists(self, **k):
            return False

        def create_commit(self, **kw):
            commits.append(kw)
            return types.SimpleNamespace(oid="c" * 40)

        def hf_hub_download(self, **kw):
            reads.append(kw)
            return str(package / kw["filename"].split("/")[-1])

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        types.SimpleNamespace(HfApi=API, CommitOperationAdd=lambda **kw: kw),
    )
    worker.publish(specimen, [], identity, tmp_path)
    assert worker.read(tmp_path / "publish.json")["revision"] == "c" * 40
    assert len(reads) == len(list(package.iterdir()))
    assert all(x["revision"] == "c" * 40 for x in reads)
    assert commits[0]["parent_commit"] == "b" * 40


def test_remote_content_conflict_never_overwrites(tmp_path, specimen, monkeypatch):
    identity, package = prepare_package(tmp_path, specimen, monkeypatch)
    wrong = tmp_path / "wrong"
    wrong.write_text("{}")

    class API:
        def __init__(self, **k):
            pass

        def dataset_info(self, *a):
            return types.SimpleNamespace(sha="b" * 40, private=True)

        def list_repo_files(self, **kw):
            return [specimen["publish"]["path_prefix"] + "/" + p.name for p in package.iterdir()]

        def file_exists(self, **k):
            return True

        def hf_hub_download(self, **k):
            return str(wrong)

        def create_commit(self, **k):
            pytest.fail("must not overwrite conflict")

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        types.SimpleNamespace(HfApi=API, CommitOperationAdd=lambda **kw: kw),
    )
    with pytest.raises(worker.GuardError, match="read-back differs"):
        worker.publish(specimen, [], identity, tmp_path)
    assert package.exists()


def test_compute_environment_has_no_credentials(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "synthetic")
    monkeypatch.setenv("OTHER_API_KEY", "synthetic")
    env = worker.clean_environment("screen")
    assert "HF_TOKEN" not in env and "OTHER_API_KEY" not in env
    assert worker.clean_environment("publish")["HF_TOKEN"] == "synthetic"


@pytest.mark.parametrize(
    ("limit", "value", "expected"),
    [
        ("max_rss_bytes", 1, "rss_exceeded"),
        ("max_scratch_bytes", 1, "scratch_exceeded"),
        ("max_stage_wall_seconds", 1, "stage_wall_exceeded"),
    ],
)
def test_guardian_kills_real_child_on_limit(
    tmp_path, specimen, monkeypatch, limit, value, expected
):
    if sys.platform != "linux":
        pytest.skip("qualified worker guardian uses Linux procfs")
    identity = {"identity_digest": "synthetic"}
    if limit == "max_stage_wall_seconds":
        specimen["budget"][limit]["fetch"] = value
    else:
        specimen["budget"][limit] = value
    monkeypatch.setattr(worker, "load_bundle", lambda *a: (specimen, [], identity))
    original = subprocess.Popen
    monkeypatch.setattr(
        worker.subprocess,
        "Popen",
        lambda cmd, **kw: original([sys.executable, "-c", "import time; time.sleep(10)"], **kw),
    )
    assert worker.launch_guarded("fetch", tmp_path / "q", tmp_path, None) == 1
    assert worker.read(tmp_path / "attempts.json")[-1]["failure"] == expected


@pytest.mark.parametrize("damage", ["manifest", "member", "extra", "ready"])
def test_verified_closure_rejects_corruption_and_extra_files(tmp_path, damage):
    source = tmp_path / "source"
    path = source / "DWD/payload/dwd_security/all/one.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"valid")
    entry = {
        "path": "payload/dwd_security/all/one.parquet",
        "size_bytes": 5,
        "sha256": worker.file_digest(path),
    }
    manifest = {"content_digest": "sha256:" + "1" * 64, "content": {"files": [entry]}}
    worker.write(source / "DWD/manifest.json", manifest)
    mh = worker.file_digest(source / "DWD/manifest.json")
    (source / "DWD/READY").write_text(mh + "\n")
    q = {"authority": {"manifest_sha256": mh, "content_digest": manifest["content_digest"]}}
    worker.verify_members(source, q, [entry])
    if damage == "manifest":
        worker.write(source / "DWD/manifest.json", {})
    elif damage == "member":
        path.write_bytes(b"wrong")
    elif damage == "extra":
        (path.parent / "rogue.parquet").write_bytes(b"undeclared")
    else:
        (source / "DWD/READY").write_text("wrong")
    with pytest.raises(worker.GuardError):
        worker.verify_members(source, q, [entry])


def test_upload_failure_retains_frozen_package(tmp_path, specimen, monkeypatch):
    identity, package = prepare_package(tmp_path, specimen, monkeypatch)
    before = {x.name: x.read_bytes() for x in package.iterdir()}

    class API:
        def __init__(self, **k):
            pass

        def dataset_info(self, *a):
            return types.SimpleNamespace(sha="b" * 40, private=True)

        def list_repo_files(self, **kw):
            return (
                [specimen["publish"]["path_prefix"] + "/" + p.name for p in package.iterdir()]
                if kw["revision"] == "c" * 40
                else []
            )

        def file_exists(self, **k):
            return False

        def create_commit(self, **k):
            raise ConnectionError("synthetic transient")

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        types.SimpleNamespace(HfApi=API, CommitOperationAdd=lambda **kw: kw),
    )
    with pytest.raises(ConnectionError):
        worker.publish(specimen, [], identity, tmp_path)
    assert before == {x.name: x.read_bytes() for x in package.iterdir()}
    assert worker.build_package(specimen, [], identity, tmp_path) == package


def test_package_upload_size_guard(tmp_path, specimen, monkeypatch):
    specimen["budget"]["max_upload_bytes"] = 1
    with pytest.raises(worker.GuardError, match="upload budget"):
        prepare_package(tmp_path, specimen, monkeypatch)
    with pytest.raises(worker.GuardError, match="upload budget"):
        worker.build_package(specimen, [], {"identity_digest": "same", "git": "a" * 40}, tmp_path)


def test_missing_receipt_digest_is_rejected(tmp_path):
    identity = {"id": "same"}
    worker.write(
        tmp_path / "fetch.json", {"schema": "quanthus.research-fetch.v2", "identity": identity}
    )
    with pytest.raises(worker.GuardError, match="digest missing"):
        worker.bound_read(tmp_path, "fetch.json", identity)
