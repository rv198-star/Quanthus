"""Synthetic regression for the #736 v2 contract repair.

Every fixture is synthetic: no real market data, news text or protected content.
Bar bytes are invented and never decoded. The v2 contract points under test:
reject-before-open on the reader gate, member/reader/step binding, production
DATES/core-manifest schema, frozen-12-only projection, benchmark result-side
placement, and anchor success/blocked separation.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.nvda_case_assembly import (
    FROZEN_EQUITIES,
    assemble,
    encoded,
    gated_read,
    sha,
)

CASE_ID = "VAL-NVDA-20261123"


def put_object(objects_root: Path, kind: str, raw: bytes) -> str:
    digest = sha(raw)
    target = objects_root / kind / digest[:2] / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return digest


def make_news_root(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "news"
    objects = root / "objects"
    objects.mkdir(parents=True)
    db = sqlite3.connect(str(root / "state.sqlite3"))
    db.execute(
        "CREATE TABLE items(id TEXT PRIMARY KEY,semantic TEXT,member_sha TEXT,first_seen TEXT)"
    )
    shas: dict[str, str] = {}
    rows = []
    for key, role, kind, seen_at in [
        (
            "sina-7x24:pre1",
            "INPUT_VISIBLE_BEFORE_CUTOFF",
            "case-input",
            "2026-11-24T00:00:00Z",
        ),
        (
            "sina-7x24:post1",
            "POST_CUTOFF_RESULT_CAPTURE",
            "case-result-capture",
            "2026-11-24T10:30:00Z",
        ),
    ]:
        wire_sha = put_object(objects, "wire", ("synthetic wire " + key).encode())
        record = {
            "schema": "quanthus.p10.forward-text-record.v1",
            "payload": {
                "source": key.split(":")[0],
                "source_record_id": key.split(":")[1],
                "synthetic": True,
            },
            "first_seen_at": seen_at,
            "version_first_seen_at": seen_at,
            "source_artifact_known_at": seen_at,
            "case_id": CASE_ID,
            "case_role": role,
            "raw_source_receipt": {
                "response_completed_at": seen_at,
                "source_http_date": "Tue, 24 Nov 2026 00:00:00 GMT",
                "raw": {"sha256": wire_sha, "size_bytes": 10},
            },
        }
        member_sha = put_object(objects, kind, encoded(record))
        rows.append((key, "sem", member_sha, record["first_seen_at"]))
        shas[key] = member_sha
    db.executemany("INSERT INTO items VALUES(?,?,?,?)", rows)
    db.commit()
    db.close()
    return root, shas


def write_core_day(root: Path, market_date: str, use_dates_key: bool = True):
    """Production-shaped synthetic core: manifest carries files[] with declared
    member sha256s; DATES.json uses the 'dates' dict with 'core' as a path."""
    core = root / ("core-" + market_date)
    files = []
    for table in ("dwd_index_bar", "dwd_security_bar"):
        rel = (
            "payload/security_market_observation/"
            + table
            + "/market_date="
            + market_date
            + "/part-00000.parquet"
        )
        part = core / rel
        part.parent.mkdir(parents=True, exist_ok=True)
        if table == "dwd_security_bar":
            identity_values = [
                "CN_STOCK_CODE:" + exchange + ":" + code
                for exchange in ("SZSE", "SSE")
                for code in FROZEN_EQUITIES
            ] + ["CN_STOCK_CODE:SZSE:000001"]
            frame = pa.table(
                {"listing_id": identity_values, "interval_size": ["1d"] * len(identity_values)}
            )
        else:
            frame = pa.table(
                {"index_id": ["399006", "000300", "999999"], "interval_size": ["1d", "1d", "1d"]}
            )
        frame = frame.add_column(
            frame.num_columns,
            "source_marker",
            pa.array([market_date] * frame.num_rows),
        )
        pq.write_table(frame, part)
        files.append(
            {"path": rel, "sha256": sha(part.read_bytes()), "size_bytes": part.stat().st_size}
        )
    manifest = {
        "schema_version": 1,
        "files": files,
        "row_counts": {"dwd_index_bar": 3, "dwd_security_bar": 5219},
    }
    mpath = core / "manifest.json"
    mpath.write_bytes(encoded(manifest))
    return core, sha(mpath.read_bytes())


def make_index(tmp_path: Path, dates: list) -> Path:
    days = {}
    for date in dates:
        core, msha = write_core_day(tmp_path / "resultcore", date)
        days[date] = {
            "market_date": date,
            "status": "QUALIFIED_1D_NATIVE5M",
            "core": str(core),
            "core_manifest_sha256": "sha256:" + msha,
            "collector_revision": "testrev",
            "consumer_admission": "NOT_GRANTED",
        }
    index = tmp_path / "DATES.json"
    index.write_bytes(encoded({"kind": "cn02_base24_daily_cycle_v1", "dates": days}))
    return index


def make_inventory(tmp_path: Path, dates: list) -> Path:
    members = []
    for date in dates:
        core, _msha = write_core_day(tmp_path / "invcore", date)
        for table in ("dwd_index_bar", "dwd_security_bar"):
            part = (
                core
                / "payload"
                / "security_market_observation"
                / table
                / ("market_date=" + date)
                / "part-00000.parquet"
            )
            members.append(
                {
                    "market_date": date,
                    "path": str(part),
                    "sha256": sha(part.read_bytes()),
                    "bytes": part.stat().st_size,
                    "source_manifest_sha256": "0" * 64,
                    "p10_verified_at_actual": "2026-11-23T22:39:00Z",
                    "row_access": "NO_MARKET_ROW_DECODE",
                }
            )
    inv = tmp_path / "inventory.json"
    inv.write_bytes(
        encoded({"schema": "inventory", "case_id": CASE_ID, "members": members, "gaps": []})
    )
    return inv


def policy_file(tmp_path: Path) -> Path:
    policy = {
        "case_id": CASE_ID,
        "input_cutoff": "2026-11-24T09:15:00+08:00",
        "definition_body_sha256": "e01e8c60",
        "main_codes": list(FROZEN_EQUITIES[:10]),
        "control_codes": list(FROZEN_EQUITIES[10:]),
        "benchmark_codes": [{"code": "399006"}, {"code": "000300"}],
        "event_window_end": "2026-11-25T16:00:00-05:00",
        "validation_authorizations": [],
    }
    path = tmp_path / "policy.json"
    path.write_bytes(encoded(policy))
    return path


def anchor_files(tmp_path: Path):
    root = tmp_path / "anchors"
    root.mkdir()
    ok = root / "anchor-NVDA.wire"
    ok.write_bytes(b"synthetic successful anchor wire")
    blocked = root / "ap-case-reference.wire"
    blocked.write_bytes(b"synthetic blocked stub")
    (root / "anchor-NVDA.receipt.json").write_bytes(encoded({"synthetic": True}))
    result = {
        "case": CASE_ID,
        "snapshots": [
            {
                "source": "anchor-NVDA",
                "raw_path": str(ok),
                "raw_sha256": sha(ok.read_bytes()),
                "row_count": 14,
                "http_status": 200,
                "role": "INPUT_PRE_CUTOFF_RAW_SOURCE",
                "status": "RAW_ANCHOR_CAPTURED_UNQUALIFIED",
                "response_completed_at": "2026-11-23T21:22:20Z",
                "ingested_at_actual": "2026-11-23T21:22:20Z",
                "source_http_date": "Mon, 23 Nov 2026 21:22:20 GMT",
                "consumer_qualification": "PENDING",
            },
            {
                "source": "ap-case-reference",
                "raw_path": str(blocked),
                "raw_sha256": sha(blocked.read_bytes()),
                "http_status": 403,
                "role": "INPUT_PRE_CUTOFF_RAW_SOURCE",
                "status": "HTTP_BLOCKED_OR_PARTIAL",
                "response_completed_at": "2026-11-23T21:22:30Z",
                "ingested_at_actual": "2026-11-23T21:22:30Z",
                "source_http_date": "Mon, 23 Nov 2026 21:22:30 GMT",
            },
        ],
    }
    result_path = tmp_path / "anchor-result.json"
    result_path.write_bytes(encoded(result))
    return result_path, root


def universe_file(tmp_path: Path) -> Path:
    org = {c: "gssz000" + c for c in FROZEN_EQUITIES}
    universe = {
        "codes": list(FROZEN_EQUITIES) + ["000001"],
        "company_org_ids": org,
        "source_identity_by_code": {
            c: (
                "CN_STOCK_CODE:"
                + ("SSE" if c.startswith(("600", "601", "688")) else "SZSE")
                + ":"
                + c
            )
            for c in FROZEN_EQUITIES
        },
        "population": 5224,
    }
    path = tmp_path / "source-universe.json"
    path.write_bytes(encoded(universe))
    return path


def run_assemble(
    tmp_path: Path,
    inventory_dates=("2026-11-20", "2026-11-23"),
    result_dates=("2026-11-24", "2026-11-25"),
):
    news_root, shas = make_news_root(tmp_path)
    inventory = make_inventory(tmp_path, list(inventory_dates))
    index = make_index(tmp_path, list(result_dates))
    anchor_result, anchor_root = anchor_files(tmp_path)
    out = tmp_path / "sealed"
    receipt = assemble(
        policy_path=policy_file(tmp_path),
        inventory_path=inventory,
        anchor_result_path=anchor_result,
        anchor_input_root=anchor_root,
        news_root=news_root,
        output_root=out,
        job_index_path=index,
        result_dates=list(result_dates),
        universe_path=universe_file(tmp_path),
    )
    return receipt, out, shas


def manifests(out: Path):
    return (
        json.loads((out / "input" / "manifest.json").read_bytes()),
        json.loads((out / "result" / "manifest.json").read_bytes()),
    )


def auth_file(tmp_path: Path, entries: list) -> Path:
    path = tmp_path / "auth.json"
    path.write_bytes(encoded({"schema": "authz-v1", "authorizations": entries}))
    return path


def result_member(out: Path) -> str:
    _i, result = manifests(out)
    return result["news_members"][0]["member_sha256"]


def test_input_output_partition_and_benchmark_placement(tmp_path: Path) -> None:
    receipt, out, _s = run_assemble(tmp_path)
    assert receipt["status"] == "SEALED"
    inp, result = manifests(out)
    # input closure never carries result-side members or benchmark frames
    assert all(b["market_date"] < "2026-11-24" for b in inp["bars"])
    assert inp["benchmark_exclusion"]
    assert "399006" not in json.dumps(inp["counts"])
    # benchmarks declared result-side only
    assert result["benchmark_role"] == "RESULT_SIDE_ONLY_NOT_DECISION_INPUT"
    assert sorted(result["benchmark_codes"]) == ["000300", "399006"]
    # index bar table is present in result closure for the two result dates
    idx = [b for b in result["bars"] if b["table"] == "dwd_index_bar"]
    assert sorted(b["market_date"] for b in idx) == ["2026-11-24", "2026-11-25"]
    # production schema binding: manifest-declared member sha + core binding
    assert all(
        b["sha_binding"].startswith("manifest-declared")
        for b in result["bars"]
        if "sha_binding" in b
    )
    assert result["index_binding"]["days"]["2026-11-24"]["status"] == "QUALIFIED_1D_NATIVE5M"
    assert all("side" not in b for b in inp["bars"])
    assert all(b["side"] == "RESULT_SIDE_POST_CUTOFF" for b in result["bars"])


def test_equity_projection_frozen12_only(tmp_path: Path) -> None:
    receipt, out, _s = run_assemble(tmp_path)
    projection = json.loads((out / "input" / "equity-projection.json").read_bytes())
    codes = sorted(e["code"] for e in projection["equities"])
    assert codes == sorted(FROZEN_EQUITIES)
    assert all(e["exchange"] in ("SSE", "SZSE") for e in projection["equities"])
    assert all(e["cninfo_org_id"] for e in projection["equities"])
    # universe extra member 000001 must not leak into the frozen projection
    assert "000001" not in codes
    # benchmarks never in the equity projection
    assert "399006" not in codes and "000300" not in codes


def test_missing_frozen_identity_is_auditable_gap(tmp_path: Path) -> None:
    news_root, _s = make_news_root(tmp_path)
    inventory = make_inventory(tmp_path, ["2026-11-23"])
    index = make_index(tmp_path, ["2026-11-24"])
    anchor_result, anchor_root = anchor_files(tmp_path)
    sparse = tmp_path / "sparse-universe.json"
    sparse.write_bytes(
        encoded({"codes": ["300308"], "company_org_ids": {"300308": "gssz000300308"}})
    )
    out = tmp_path / "sealed"
    receipt = assemble(
        policy_path=policy_file(tmp_path),
        inventory_path=inventory,
        anchor_result_path=anchor_result,
        anchor_input_root=anchor_root,
        news_root=news_root,
        output_root=out,
        job_index_path=index,
        result_dates=["2026-11-24"],
        universe_path=sparse,
    )
    assert receipt["status"] == "PARTIAL"
    assert any(g["reason"] == "FROZEN_CODE_NOT_IN_QUALIFIED_UNIVERSE" for g in receipt["gaps"])


def test_anchor_success_and_blocked_separated(tmp_path: Path) -> None:
    receipt, out, _s = run_assemble(tmp_path)
    inp, _r = manifests(out)
    by_source = {a["source"]: a for a in inp["anchors"]}
    assert by_source["anchor-NVDA"]["capture_class"] == "SUCCESSFUL_RAW_ANCHOR_CAPTURE"
    assert by_source["anchor-NVDA"]["row_count"] == 14
    assert by_source["ap-case-reference"]["capture_class"] == "VENDOR_BLOCKED_REFERENCE_STUB"
    assert by_source["ap-case-reference"]["row_count"] is None
    assert (
        by_source["ap-case-reference"]["consumer_qualification"]
        == "content not usable as reference"
    )
    assert "producer-observed clocks" in by_source["anchor-NVDA"]["capture_clock_role"]


def test_gate_rejects_before_opening_blob(tmp_path: Path) -> None:
    receipt, out, _s = run_assemble(tmp_path)
    member = result_member(out)
    artifact = out / "result" / "news" / "members" / member
    before = sha(artifact.read_bytes())
    empty = auth_file(tmp_path, [])
    with pytest.raises(PermissionError):
        gated_read(out / "result", empty, member, "mallory", "validate-step-1")
    # blob untouched byte-identical; rejection log has manifest digest + request metadata only
    assert sha(artifact.read_bytes()) == before
    log = [
        json.loads(x)
        for x in (out / "result" / "ACCESS-LOG.jsonl").read_text().strip().splitlines()
    ]
    assert len(log) == 1
    entry = log[0]
    assert entry["decision"] == "REJECTED_NO_AUTHORIZATION"
    assert entry["artifact_manifest_digest"] == member
    assert "artifact_sha256" not in entry  # no blob-open hash on rejection
    assert entry["authorization_file_sha256"] == sha(empty.read_bytes())


def test_gate_binds_reader_step_and_member(tmp_path: Path) -> None:
    receipt, out, _s = run_assemble(tmp_path)
    member = result_member(out)
    good = auth_file(
        tmp_path,
        [
            {
                "authorization_id": "auth-1",
                "reader": "validator-a",
                "declared_steps": ["validate-step-1"],
                "artifact_member_sha256s": [member],
            }
        ],
    )
    # wrong member not covered by the same authorization
    with pytest.raises(PermissionError):
        gated_read(out / "result", good, "0" * 64, "validator-a", "validate-step-1")
    # wrong reader
    with pytest.raises(PermissionError):
        gated_read(out / "result", good, member, "mallory", "validate-step-1")
    # wrong step
    with pytest.raises(PermissionError):
        gated_read(out / "result", good, member, "validator-a", "validate-step-2")
    result = gated_read(out / "result", good, member, "validator-a", "validate-step-1")
    assert result["entry"]["decision"] == "AUTHORIZED"
    assert result["entry"]["artifact_sha256"] == member
    log = (out / "result" / "ACCESS-LOG.jsonl").read_text().strip().splitlines()
    assert len(log) == 4


def test_caller_writable_auth_copy_cannot_expand_authority(tmp_path: Path) -> None:
    receipt, out, _s = run_assemble(tmp_path)
    member = result_member(out)
    # attacker-controlled copy inside the output area is not a valid authority source;
    # only the explicit authorization file path argument is read.
    rogue = out / "result" / "forge-auth.json"
    rogue.write_bytes(
        encoded(
            {
                "authorizations": [
                    {
                        "authorization_id": "rogue",
                        "reader": "mallory",
                        "declared_steps": ["validate-step-1"],
                        "artifact_member_sha256s": [member],
                    }
                ]
            }
        )
    )
    empty = auth_file(tmp_path, [])
    with pytest.raises(PermissionError):
        gated_read(out / "result", empty, member, "mallory", "validate-step-1")


def test_incomplete_authorization_entries_rejected(tmp_path: Path) -> None:
    receipt, out, _s = run_assemble(tmp_path)
    member = result_member(out)
    bad = auth_file(tmp_path, [{"authorization_id": "x", "reader": "validator-a"}])
    with pytest.raises(PermissionError):
        gated_read(out / "result", bad, member, "validator-a", "validate-step-1")
    log = [
        json.loads(x)
        for x in (out / "result" / "ACCESS-LOG.jsonl").read_text().strip().splitlines()
    ]
    assert log[0]["authorization_file_error"] == "AuthorizationError"


def test_manifest_declared_member_mismatch_raises(tmp_path: Path) -> None:
    news_root, _s = make_news_root(tmp_path)
    inventory = make_inventory(tmp_path, ["2026-11-23"])
    index = make_index(tmp_path, ["2026-11-24"])
    anchor_result, anchor_root = anchor_files(tmp_path)
    # corrupt one core bar after manifest was written
    core = tmp_path / "resultcore" / "core-2026-11-24"
    part = (
        core
        / "payload"
        / "security_market_observation"
        / "dwd_index_bar"
        / "market_date=2026-11-24"
        / "part-00000.parquet"
    )
    part.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="MEMBER_SHA_MISMATCH_DECLARED"):
        assemble(
            policy_path=policy_file(tmp_path),
            inventory_path=inventory,
            anchor_result_path=anchor_result,
            anchor_input_root=anchor_root,
            news_root=news_root,
            output_root=tmp_path / "sealed",
            job_index_path=index,
            result_dates=["2026-11-24"],
            universe_path=universe_file(tmp_path),
        )


def test_overlap_inventory_excluded_from_input(tmp_path: Path) -> None:
    receipt, out, _s = run_assemble(
        tmp_path, inventory_dates=("2026-11-23", "2026-11-24"), result_dates=("2026-11-24",)
    )
    inp, result = manifests(out)
    assert {b["market_date"] for b in inp["bars"]} == {"2026-11-23"}
    overlap = [
        b
        for b in result["bars"]
        if b.get("side") == "RESULT_SIDE_POST_CUTOFF" and b["market_date"] == "2026-11-24"
    ]
    assert overlap
    assert result["overlap_inventory_bars_resealed_blind"] == 2
    # dedupe: same sha sealed once even when core day covers the same bytes
    shas24 = [b["sha256"] for b in result["bars"] if b["market_date"] == "2026-11-24"]
    assert len(shas24) == len(set(shas24))


def test_seal_root_rerun_refuses_before_write(tmp_path: Path) -> None:
    receipt, out, _s = run_assemble(tmp_path)
    input_manifest_bytes = (out / "input" / "manifest.json").read_bytes()
    result_manifest_bytes = (out / "result" / "manifest.json").read_bytes()
    receipt_bytes = (out / "RECEIPT.json").read_bytes()
    sealed_input_bytes = (out / "input" / "SEALED.json").read_bytes()
    member = result_member(out)
    blob_before = (out / "result" / "news" / "members" / member).read_bytes()
    # rerun over the same root must refuse before writing anything
    rerun = tmp_path / "rerun-inputs"
    rerun.mkdir()
    news_root, _s2 = make_news_root(rerun)
    inventory = make_inventory(rerun, ["2026-11-20", "2026-11-23"])
    index = make_index(rerun, ["2026-11-24", "2026-11-25"])
    anchor_result, anchor_root = anchor_files(rerun)
    with pytest.raises(FileExistsError, match="SEALED_ROOT_ALREADY_PRESENT"):
        assemble(
            policy_path=policy_file(rerun),
            inventory_path=inventory,
            anchor_result_path=anchor_result,
            anchor_input_root=anchor_root,
            news_root=news_root,
            output_root=out,
            job_index_path=index,
            result_dates=["2026-11-24", "2026-11-25"],
            universe_path=universe_file(rerun),
        )
    # every original sealed byte survives untouched
    assert (out / "input" / "manifest.json").read_bytes() == input_manifest_bytes
    assert (out / "result" / "manifest.json").read_bytes() == result_manifest_bytes
    assert (out / "RECEIPT.json").read_bytes() == receipt_bytes
    assert (out / "input" / "SEALED.json").read_bytes() == sealed_input_bytes
    assert (out / "result" / "news" / "members" / member).read_bytes() == blob_before


def test_anchor_original_and_staged_path_same_sha(tmp_path: Path) -> None:
    receipt, out, _s = run_assemble(tmp_path)
    inp, _r = manifests(out)
    for anchor in inp["anchors"]:
        if anchor.get("status") == "unavailable":
            continue
        assert anchor["original_path"]
        assert anchor["staged_path"]
        assert (
            anchor["original_path"] != anchor["staged_path"]
            or anchor["original_path"] == anchor["staged_path"]
        )
        original = Path(anchor["original_path"])
        staged = Path(anchor["staged_path"])
        # both paths resolve to bytes of the one fixed sha256
        assert sha(original.read_bytes()) == anchor["sha256"]
        assert sha(staged.read_bytes()) == anchor["sha256"]
        assert "same" in anchor["path_binding"]
    # at least one successful and one blocked anchor are both path-bound
    classes = {a["source"]: a["capture_class"] for a in inp["anchors"] if "capture_class" in a}
    assert "SUCCESSFUL_RAW_ANCHOR_CAPTURE" in classes.values()
    assert "VENDOR_BLOCKED_REFERENCE_STUB" in classes.values()


def make_revised_news_root(tmp_path: Path) -> tuple[Path, dict]:
    """One news source item with two versions: v1 observed before the cutoff,
    v2 (revision) arriving after it. Each version must land on its own side."""
    root = tmp_path / "newsrev"
    objects = root / "objects"
    objects.mkdir(parents=True)
    db = sqlite3.connect(str(root / "state.sqlite3"))
    db.execute(
        "CREATE TABLE items(id TEXT PRIMARY KEY,semantic TEXT,member_sha TEXT,first_seen TEXT)"
    )
    shas: dict[str, str] = {}
    rows = []
    v1_sha = None
    key = "sina-7x24:rev1"
    for version, resp_at, role, kind in [
        (1, "2026-11-24T01:00:00Z", "INPUT_VISIBLE_BEFORE_CUTOFF", "case-input"),
        (2, "2026-11-24T10:30:00Z", "POST_CUTOFF_RESULT_CAPTURE", "case-result-capture"),
    ]:
        wire_sha = put_object(objects, "wire", f"wire-v{version}".encode())
        record = {
            "schema": "quanthus.p10.forward-text-record.v1",
            "payload": {"source": "sina-7x24", "source_record_id": "rev1", "version": version},
            "first_seen_at": "2026-11-24T01:00:00Z",
            "version_first_seen_at": resp_at,
            "source_artifact_known_at": resp_at,
            "case_id": CASE_ID,
            "case_role": role,
            "previous_member_sha256": v1_sha,
            "raw_source_receipt": {
                "response_completed_at": resp_at,
                "source_http_date": "Tue, 24 Nov 2026 00:00:00 GMT",
                "raw": {"sha256": wire_sha, "size_bytes": 8},
            },
        }
        member_sha = put_object(objects, kind, encoded(record))
        shas[f"v{version}"] = member_sha
        if v1_sha is None:
            v1_sha = member_sha
    # final sqlite state: the item row points at the newest version (producer semantics)
    rows.append((key, "sem2", shas["v2"], "2026-11-24T01:00:00Z"))
    db.executemany("INSERT INTO items VALUES(?,?,?,?)", rows)
    db.commit()
    db.close()
    return root, shas


def test_news_revision_versions_split_across_sides(tmp_path: Path) -> None:
    """Both versions of one revised item exist in the store: the pre-cutoff
    original must be recoverable into the input closure and the late revision
    into the result closure, chained by previous_member_sha256."""
    news_root, shas = make_revised_news_root(tmp_path)
    inventory = make_inventory(tmp_path, ["2026-11-23"])
    index = make_index(tmp_path, ["2026-11-24"])
    anchor_result, anchor_root = anchor_files(tmp_path)
    out = tmp_path / "sealed"
    # note: sqlite head points at v2; the assembler reads member objects, so the
    # v1 object remains resolvable through its own sha in the store even though
    # head moved on. The closure contract: whichever member versions are
    # enumerated, each lands strictly by its own case_role.
    assemble(
        policy_path=policy_file(tmp_path),
        inventory_path=inventory,
        anchor_result_path=anchor_result,
        anchor_input_root=anchor_root,
        news_root=news_root,
        output_root=out,
        job_index_path=index,
        result_dates=["2026-11-24"],
        universe_path=universe_file(tmp_path),
    )
    inp, result = manifests(out)
    input_members = [m for m in inp["news_members"] if m["source_key"] == "sina-7x24:rev1"]
    result_members = [m for m in result["news_members"] if m["source_key"] == "sina-7x24:rev1"]
    # one assembly run keeps both chain versions even though sqlite head is v2:
    # v1 before the input cutoff on the input side, v2 after it on the result side.
    assert [m["member_sha256"] for m in input_members] == [shas["v1"]]
    assert [m["member_sha256"] for m in result_members] == [shas["v2"]]
    assert result_members[0]["previous_member_sha256"] == shas["v1"]
    assert result_members[0]["sqlite_head_is_this_member"] is True
    assert input_members[0]["sqlite_head_is_this_member"] is False
    assert input_members[0]["clock_evidence"]["first_seen_at"] == "2026-11-24T01:00:00Z"
    # the pre-cutoff original object remains sealed by sha in the store tree
    v1_obj = news_root / "objects" / "case-input" / shas["v1"][:2] / shas["v1"]
    assert v1_obj.exists()
    result_obj = out / "result" / "news" / "members" / shas["v2"]
    input_obj = out / "input" / "news" / "members" / shas["v1"]
    assert result_obj.exists() and input_obj.exists()


def test_member_projection_counts_and_excludes_unrelated(tmp_path: Path) -> None:
    receipt, out, _s = run_assemble(tmp_path)
    inp, result = manifests(out)
    assert receipt["status"] == "SEALED"
    input_projection = inp["member_projection"]
    assert input_projection
    assert all(row["projection_member"] is True for row in input_projection)
    assert all(row["row_count"] == 1 for row in input_projection)
    assert all(row["benchmark"] is False for row in input_projection)
    # each frozen equity is projected per input date, while unrelated universe
    # members such as 000001 never become named projection rows.
    assert len(input_projection) == 2 * len(FROZEN_EQUITIES)
    assert "000001" not in {row["code"] for row in input_projection}
    assert all("open" not in row and "close" not in row for row in input_projection)
    assert inp["counts"]["benchmark_projection"] == 0
    result_projection = result["member_projection"]
    assert len(result_projection) == 2 * len(FROZEN_EQUITIES)
    benchmark_projection = result["benchmark_projection"]
    assert len(benchmark_projection) == 4
    assert {row["code"] for row in benchmark_projection} == {"399006", "000300"}
    assert all(row["row_count"] == 1 for row in benchmark_projection)


def test_member_projection_absent_and_duplicate_are_named_gaps(tmp_path: Path) -> None:
    news_root, _s = make_news_root(tmp_path)
    inventory = make_inventory(tmp_path, ["2026-11-23"])
    index = make_index(tmp_path, ["2026-11-24"])
    anchor_result, anchor_root = anchor_files(tmp_path)
    # Rewrite one input security member so one frozen identity is absent and
    # another occurs twice. Values are synthetic identity strings only.
    core = tmp_path / "invcore" / "core-2026-11-23"
    part = (
        core
        / "payload"
        / "security_market_observation"
        / "dwd_security_bar"
        / "market_date=2026-11-23"
        / "part-00000.parquet"
    )
    first = FROZEN_EQUITIES[0]
    second = FROZEN_EQUITIES[1]
    rest = [
        "CN_STOCK_CODE:" + ("SSE" if c.startswith(("600", "601", "688")) else "SZSE") + ":" + c
        for c in FROZEN_EQUITIES[2:]
    ]
    duplicate = "CN_STOCK_CODE:SZSE:" + second
    frame = pa.table({"listing_id": [duplicate, duplicate] + rest})
    pq.write_table(frame, part)
    raw = part.read_bytes()
    inv = json.loads(inventory.read_bytes())
    for member in inv["members"]:
        if member["path"] == str(part):
            member["sha256"] = sha(raw)
            member["bytes"] = len(raw)
    inventory.write_bytes(encoded(inv))
    out = tmp_path / "sealed"
    receipt = assemble(
        policy_path=policy_file(tmp_path),
        inventory_path=inventory,
        anchor_result_path=anchor_result,
        anchor_input_root=anchor_root,
        news_root=news_root,
        output_root=out,
        job_index_path=index,
        result_dates=["2026-11-24"],
        universe_path=universe_file(tmp_path),
    )
    assert receipt["status"] == "PARTIAL"
    reasons = {(g.get("item") or "", g["reason"]) for g in receipt["gaps"]}
    assert any(
        item.endswith(":dwd_security_bar:" + first) and reason == "IDENTITY_NOT_IN_SOURCE_MEMBER"
        for item, reason in reasons
    )
    assert any(
        item.endswith(":dwd_security_bar:" + second)
        and reason == "DUPLICATE_IDENTITY_ROWS_IN_SOURCE_MEMBER"
        for item, reason in reasons
    )


def test_preexisting_empty_and_junk_output_roots_refuse_before_write(tmp_path: Path) -> None:
    news_root, _s = make_news_root(tmp_path)
    inventory = make_inventory(tmp_path, ["2026-11-23"])
    index = make_index(tmp_path, ["2026-11-24"])
    anchor_result, anchor_root = anchor_files(tmp_path)
    for name in ("empty-root", "junk-root"):
        root = tmp_path / name
        root.mkdir()
        if name == "junk-root":
            (root / "unrelated.txt").write_text("junk")
        before = sorted(str(x.relative_to(root)) for x in root.rglob("*"))
        with pytest.raises(FileExistsError, match="OUTPUT_ROOT_ALREADY_EXISTS"):
            assemble(
                policy_path=policy_file(tmp_path),
                inventory_path=inventory,
                anchor_result_path=anchor_result,
                anchor_input_root=anchor_root,
                news_root=news_root,
                output_root=root,
                job_index_path=index,
                result_dates=["2026-11-24"],
                universe_path=universe_file(tmp_path),
            )
        after = sorted(str(x.relative_to(root)) for x in root.rglob("*"))
        assert after == before


def test_news_conflicting_or_missing_clocks_are_named_gaps(tmp_path: Path) -> None:
    root = tmp_path / "newsclock"
    objects = root / "objects"
    objects.mkdir(parents=True)
    sqlite3.connect(str(root / "state.sqlite3")).execute(
        "CREATE TABLE items(id TEXT PRIMARY KEY,semantic TEXT,member_sha TEXT,first_seen TEXT)"
    ).connection.close()

    def write_record(record_id: str, record: dict) -> str:
        wire_sha = put_object(objects, "wire", ("wire-" + record_id).encode())
        record.setdefault("schema", "quanthus.p10.forward-text-record.v1")
        record.setdefault("payload", {"source": "sina-7x24", "source_record_id": record_id})
        record.setdefault("case_id", CASE_ID)
        record.setdefault("case_role", "POST_CUTOFF_RESULT_CAPTURE")
        record.setdefault(
            "raw_source_receipt",
            {
                "response_completed_at": record.get("version_first_seen_at"),
                "raw": {"sha256": wire_sha, "size_bytes": 4},
            },
        )
        return put_object(objects, "case-result-capture", encoded(record))

    write_record(
        "clock-conflict",
        {
            "first_seen_at": "2026-11-24T01:00:00Z",
            "version_first_seen_at": "2026-11-24T00:30:00Z",
            "source_artifact_known_at": "2026-11-24T10:00:00Z",
        },
    )
    missing_sha = write_record(
        "clock-missing",
        {
            "first_seen_at": "2026-11-24T01:00:00Z",
            "version_first_seen_at": "2026-11-24T10:30:00Z",
        },
    )
    # Remove every decision clock from the missing-clock object after construction.
    missing_path = objects / "case-result-capture" / missing_sha[:2] / missing_sha
    missing_record = json.loads(missing_path.read_bytes())
    missing_record.pop("version_first_seen_at", None)
    missing_record["raw_source_receipt"]["response_completed_at"] = None
    raw = encoded(missing_record)
    assert sha(raw) != missing_sha
    # Keep the object at its original content-addressed path as a corrupt/missing
    # clock input: the test does not require production write semantics here.
    missing_path.write_bytes(raw)

    inventory = make_inventory(tmp_path, ["2026-11-23"])
    index = make_index(tmp_path, ["2026-11-24"])
    anchor_result, anchor_root = anchor_files(tmp_path)
    out = tmp_path / "sealed"
    receipt = assemble(
        policy_path=policy_file(tmp_path),
        inventory_path=inventory,
        anchor_result_path=anchor_result,
        anchor_input_root=anchor_root,
        news_root=root,
        output_root=out,
        job_index_path=index,
        result_dates=["2026-11-24"],
        universe_path=universe_file(tmp_path),
    )
    assert receipt["status"] == "PARTIAL"
    reasons = {g["reason"] for g in receipt["gaps"]}
    assert "VISIBILITY_CLOCK_CONFLICT" in reasons
    assert "VISIBILITY_CLOCK_MISSING" in reasons


def test_news_role_clock_mismatch_is_named_gap(tmp_path: Path) -> None:
    news_root, shas = make_revised_news_root(tmp_path)
    v2_path = (
        news_root / "objects" / "case-result-capture" / shas["v2"][:2] / shas["v2"]
    )
    v2_record = json.loads(v2_path.read_bytes())
    # Producer metadata claims input visibility, while the actual version clocks
    # are post-cutoff. This must not enter either side merely because a role string
    # or sqlite head says so.
    v2_record["case_role"] = "INPUT_VISIBLE_BEFORE_CUTOFF"
    v2_path.write_bytes(encoded(v2_record))
    inventory = make_inventory(tmp_path, ["2026-11-23"])
    index = make_index(tmp_path, ["2026-11-24"])
    anchor_result, anchor_root = anchor_files(tmp_path)
    out = tmp_path / "sealed"
    receipt = assemble(
        policy_path=policy_file(tmp_path),
        inventory_path=inventory,
        anchor_result_path=anchor_result,
        anchor_input_root=anchor_root,
        news_root=news_root,
        output_root=out,
        job_index_path=index,
        result_dates=["2026-11-24"],
        universe_path=universe_file(tmp_path),
    )
    assert receipt["status"] == "PARTIAL"
    mismatch = [
        g
        for g in receipt["gaps"]
        if g["reason"] in {"CASE_ROLE_OBJECT_KIND_MISMATCH", "CASE_ROLE_VISIBILITY_SIDE_MISMATCH"}
    ]
    assert mismatch
    assert any(g.get("member_sha256") == shas["v2"] for g in mismatch)
    inp, result = manifests(out)
    input_members = [m for m in inp["news_members"] if m["source_key"] == "sina-7x24:rev1"]
    result_members = [m for m in result["news_members"] if m["source_key"] == "sina-7x24:rev1"]
    assert [m["member_sha256"] for m in input_members] == [shas["v1"]]
    assert result_members == []
