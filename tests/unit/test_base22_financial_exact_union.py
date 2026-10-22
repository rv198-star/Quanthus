"""Explicit raw equivalence, unavailable date precision, and local cold atoms."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_financial_exact_union as u
from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits, Member
from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialCompanyIdentity,
    FinancialCompanyResolver,
    financial_reporting_schemas,
)

TABLE = "dwd_financial_fact"
SCHEMA = financial_reporting_schemas()[TABLE]
RULE = json.loads(Path("config/pipelines/base22-independent-image.v1.json").read_bytes())["tables"][
    TABLE
]
ACQ = datetime(2026, 8, 1, 2, 3, 4, 123456, tzinfo=timezone.utc)
BOUND = datetime(2026, 7, 31, 15, 59, 59, 999999, tzinfo=timezone.utc)
SUFFIX = "#stock_code=000001&statement_type=balance_sheet&period_end=20251231&item_index=0"


@pytest.fixture(autouse=True)
def synthetic_ro_fixture(monkeypatch, tmp_path):
    real = os.statvfs

    def snapshot(p):
        info = real(p)
        if Path(p).name not in {"source", "job", "local"}:
            return info
        return SimpleNamespace(
            **(
                {k: getattr(info, k) for k in dir(info) if k.startswith("f_")}
                | {"f_flag": info.f_flag | os.ST_RDONLY}
            )
        )

    monkeypatch.setattr(os, "statvfs", snapshot)


def limits():
    return Limits(
        batch_rows=2,
        memory_bytes=128 * 1024**2,
        temp_bytes=64 * 1024**2,
        allocated_bytes=128 * 1024**2,
        free_floor_bytes=1,
    )


def member(p, rows, schema=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), p)
    return Member(p, file_sha(p), p.stat().st_size, len(rows))


def raw(publication="2026-07-31", **changes):
    token = changes.pop("synthetic_token", "1.0000000001")
    body = {
        "result": {
            "data": {
                "report_list": {
                    "20251231": {
                        "publish_date": publication,
                        "data": [
                            {
                                "item_field": "f",
                                "item_title": "fixture",
                                "item_value": token,
                            }
                        ],
                    }
                }
            }
        }
    }
    encoded = json.dumps(body).encode()
    return {
        "stock_code": "000001",
        "statement_type": "balance_sheet",
        "source": "fixture",
        "source_revision": "v1",
        "acquired_at": ACQ.isoformat(),
        "raw_payload_bytes": len(encoded),
        "raw_payload_sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "raw_payload_gzip": repr(gzip.compress(encoded, mtime=0)).encode(),
    } | changes


def fact(artifact, relative, publication, **changes):
    return {
        "record_source": "fixture",
        "source_record_id": relative + SUFFIX,
        "source_revision": "v1",
        "source_artifact_sha256": artifact,
        "knowledge_from": ACQ,
        "knowledge_to": None,
        "period_start": date(2025, 1, 1),
        "period_end": date(2025, 12, 31),
        "published_at": publication,
        "financial_fact_id": "f",
        "financial_report_id": "r",
        "financial_concept_id": "c",
        "unit_code": "CNY",
        "value_decimal": Decimal("1.0000000001"),
        "value_text": None,
        "dimensions_json": "{}",
        "context_hash": "x",
        "decimals": 10,
        "is_nil": False,
    } | changes


def resolver():
    return FinancialCompanyResolver(
        [
            FinancialCompanyIdentity(
                "000001",
                "s",
                "c",
                datetime(1990, 1, 1, tzinfo=timezone.utc),
                None,
            )
        ]
    )


def fixture(tmp_path, publication="2026-07-31", raw_change=None, old_change=None, new_change=None):
    original = member(tmp_path / "source/original-raw.parquet", [raw(publication)])
    current = member(
        tmp_path / "source/current-raw.parquet",
        [raw(publication, package="new", **(raw_change or {}))],
    )
    original_rel = "raw/original.parquet"
    with Budget(tmp_path / "proof", [tmp_path / "source"], limits()) as budget:
        proof = member(
            tmp_path / "source/bridge.parquet",
            list(u.bridge_rows([(original, original_rel)], [current], budget, resolver())),
            u.bridge_schema(),
        )
        u.verify_bridge(proof, [(original, original_rel)], [current], budget, resolver())
    old_pub = BOUND if publication == "2026-07-31" else datetime.fromisoformat(publication)
    old = member(
        tmp_path / "source/old.parquet",
        [fact(original.sha256, original_rel, old_pub, **(old_change or {}))],
        SCHEMA,
    )
    new_pub = None if publication == "2026-07-31" else old_pub
    new = member(
        tmp_path / "source/new.parquet",
        [fact(current.sha256, "artifacts/current.parquet", new_pub, **(new_change or {}))],
        SCHEMA,
    )
    return original, current, proof, old, new


def build(tmp_path, items):
    _, _, bridge, old, new = items
    with Budget(tmp_path / "job", [tmp_path / "source"], limits()) as budget:
        out = budget.root / "output.parquet"
        receipt = u.materialize_year(
            [old],
            [new],
            bridge,
            table=TABLE,
            year=2025,
            schema=SCHEMA,
            rule=RULE,
            output=out,
            budget=budget,
            qualification_identity=digest("full-qualified-raw"),
        )
    return out, receipt


def audit(tmp_path, items, out, receipt):
    _, _, bridge, old, new = items
    with Budget(tmp_path / "audit", [tmp_path / "source", out.parent], limits()) as budget:
        return u.audit_year(
            [old], [new], bridge, out, receipt, schema=SCHEMA, rule=RULE, budget=budget
        )


def test_same_response_deduplicates_and_date_only_is_unknown_with_exact_old_atoms(tmp_path):
    items = fixture(tmp_path)
    before = [m.path.read_bytes() for m in items]
    out, receipt = build(tmp_path, items)
    assert receipt["duplicate_captures_archived"] == 1
    assert receipt["publication_date_bounds_made_unavailable"] == 1
    expected = pq.ParquetFile(items[3].path).read().to_pylist()[0] | {"published_at": None}
    assert pq.ParquetFile(out).read().to_pylist() == [expected]
    assert audit(tmp_path, items, out, receipt)["rows"] == 1
    assert [m.path.read_bytes() for m in items] == before
    assert (
        receipt["consumer_admission"] == "NOT_GRANTED"
        and receipt["historical_PIT_admission"] is False
    )


def test_genuine_publication_instant_and_acquisition_are_kept(tmp_path):
    items = fixture(tmp_path, "2026-07-31T15:00:01.123456+00:00")
    out, receipt = build(tmp_path, items)
    assert receipt["publication_date_bounds_made_unavailable"] == 0
    actual = pq.ParquetFile(out).read().to_pylist()[0]
    assert actual["published_at"].microsecond == 123456 and actual["knowledge_from"] == ACQ
    assert audit(tmp_path, items, out, receipt)["rows"] == 1


@pytest.mark.parametrize(
    "fault",
    [
        "raw-bytes",
        "raw-acquisition",
        "value",
        "clock-proof",
        "invented-publication",
        "unknown-artifact",
        "wrong-provider",
        "duplicate-native",
    ],
)
def test_new_contract_does_not_grant_arbitrary_priority_or_date_repair(tmp_path, fault):
    kw = {}
    if fault == "raw-bytes":
        kw["raw_change"] = {"synthetic_token": "2.0000000001"}
    if fault == "raw-acquisition":
        kw["raw_change"] = {"acquired_at": "2026-08-02T02:03:04.123456+00:00"}
    if fault == "value":
        kw["new_change"] = {"value_decimal": Decimal("2.0000000001")}
    if fault == "clock-proof":
        kw["old_change"] = {"published_at": ACQ}
    if fault == "invented-publication":
        kw["new_change"] = {"published_at": ACQ}
    if fault == "unknown-artifact":
        kw["new_change"] = {"source_artifact_sha256": "sha256:" + "0" * 64}
    if fault == "wrong-provider":
        kw["new_change"] = {"record_source": "other"}
    items = fixture(tmp_path, **kw)
    if fault == "duplicate-native":
        old = items[3]
        rows = pq.ParquetFile(old.path).read().to_pylist()
        items = (
            *items[:3],
            member(tmp_path / "source/duplicate.parquet", rows * 2, SCHEMA),
            items[4],
        )
    before = [m.path.read_bytes() for m in items]
    with pytest.raises(u.BoundedError):
        build(tmp_path, items)
    assert not (tmp_path / "job/output.parquet").exists()
    assert not (tmp_path / "job/output.parquet.partial").exists()
    assert [m.path.read_bytes() for m in items] == before


@pytest.mark.parametrize("fault", ["value", "publication", "end", "duplicate", "drop"])
def test_resealed_offline_output_is_semantically_rejected(tmp_path, fault):
    items = fixture(tmp_path)
    out, receipt = build(tmp_path, items)
    rows = pq.ParquetFile(out).read().to_pylist()
    if fault == "value":
        rows[0]["value_decimal"] = Decimal("2")
    if fault == "publication":
        rows[0]["published_at"] = BOUND
    if fault == "end":
        rows[0]["knowledge_to"] = ACQ
    if fault == "duplicate":
        rows += rows
    if fault == "drop":
        rows = []
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), out)
    receipt["output_sha256"] = file_sha(out)
    receipt["identity"] = digest({k: v for k, v in receipt.items() if k != "identity"})
    with pytest.raises(u.BoundedError, match="UNAPPROVED_ATOM_CHANGE"):
        audit(tmp_path, items, out, receipt)


def test_resealed_precision_boolean_rejected_by_offline_original_raw(tmp_path):
    original, current, proof, _, _ = fixture(tmp_path)
    rows = pq.ParquetFile(proof.path).read().to_pylist()
    rows[0]["date_only"] = False
    tampered = member(tmp_path / "source/tampered-bridge.parquet", rows, u.bridge_schema())
    with (
        Budget(tmp_path / "cold-proof", [tmp_path / "source"], limits()) as budget,
        pytest.raises(u.BoundedError, match="RAW_REPRODUCTION_FAILED"),
    ):
        u.verify_bridge(
            tampered, [(original, "raw/original.parquet")], [current], budget, resolver()
        )


def test_independent_move_reads_only_local_original_captures(tmp_path):
    items = fixture(tmp_path)
    out, receipt = build(tmp_path, items)
    (tmp_path / "source").rename(tmp_path / "local")
    moved = tuple(
        Member(tmp_path / "local" / m.path.name, m.sha256, m.size_bytes, m.rows) for m in items
    )
    assert audit(tmp_path, moved, out, receipt)["original_source_runtime_reads"] == 0
    assert not (out.parent / "READY").exists()


def test_distinct_acquisitions_at_same_parent_floor_keep_both_raw_clocks(tmp_path):
    original = member(tmp_path / "source/original-raw.parquet", [raw()])
    actual_new = datetime(2026, 8, 2, 2, 3, 4, 123456, tzinfo=timezone.utc)
    current = member(
        tmp_path / "source/current-raw.parquet", [raw(acquired_at=actual_new.isoformat())]
    )
    floor = datetime(2026, 8, 3, 23, 59, tzinfo=timezone.utc)
    parent = FinancialCompanyResolver(
        [
            FinancialCompanyIdentity(
                "000001",
                "s",
                "c",
                datetime(1990, 1, 1, tzinfo=timezone.utc),
                None,
                floor,
            )
        ]
    )
    with Budget(tmp_path / "proof", [tmp_path / "source"], limits()) as budget:
        rows = list(u.bridge_rows([(original, "raw/original.parquet")], [current], budget, parent))
        proof = member(tmp_path / "source/bridge.parquet", rows, u.bridge_schema())
        u.verify_bridge(proof, [(original, "raw/original.parquet")], [current], budget, parent)
    assert rows[0]["original_acquired_at"] == ACQ
    assert rows[0]["knowledge_from"] == floor and rows[0]["current_artifact_sha256s"] == [
        current.sha256
    ]
    old = member(
        tmp_path / "source/old.parquet",
        [fact(original.sha256, "raw/original.parquet", BOUND, knowledge_from=floor)],
        SCHEMA,
    )
    new = member(
        tmp_path / "source/new.parquet",
        [fact(current.sha256, "artifacts/current.parquet", None, knowledge_from=floor)],
        SCHEMA,
    )
    out, receipt = build(tmp_path, (original, current, proof, old, new))
    assert (
        receipt["duplicate_captures_archived"] == 1
        and pq.ParquetFile(out).read()["knowledge_from"][0].as_py() == floor
    )
    assert audit(tmp_path, (original, current, proof, old, new), out, receipt)["rows"] == 1


def test_corrupt_raw_response_hash_fails_before_bridge_qualification(tmp_path):
    broken = member(tmp_path / "source/raw.parquet", [raw(raw_payload_sha256="sha256:" + "0" * 64)])
    with (
        Budget(tmp_path / "proof", [tmp_path / "source"], limits()) as budget,
        pytest.raises(RuntimeError, match="SHA-256"),
    ):
        list(u.bridge_rows([(broken, "raw/original.parquet")], [broken], budget, resolver()))
