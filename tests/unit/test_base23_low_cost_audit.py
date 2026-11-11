from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base23_low_cost_audit as a
from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from tests.unit.test_base23_calendar_authority import fixture, mirror


def package(root):
    root.mkdir()
    (root / "official").mkdir()
    config, docs = fixture()
    for venue, data in docs.items():
        (root / f"official/{venue}.html").write_bytes(data)
    authority = a.derive_date_authority(config, docs)
    rows, venues = mirror(authority)
    pq.write_table(pa.Table.from_pylist(rows), root / "calendar.parquet")
    pq.write_table(pa.Table.from_pylist(venues), root / "venues.parquet")
    comparison = a.compare_retained_calendar(authority, rows, venues)
    for name, body in [("calendar-config.json", config), ("authority.json", authority),
                       ("comparison.json", comparison)]:
        (root / name).write_bytes(canonical(body) + b"\n")
    closure = {"purpose": a.CONTRACT_ID, "qualification": a.QUALIFICATION,
               "business_through": "2026-09-30", "timezone": "Asia/Shanghai",
               "code_closure": a.code_closure(), "runtime": a.runtime_identity()}
    body = {"contract_id": a.CONTRACT_ID, "kind": "calendar-date-authority",
            "qualification": a.QUALIFICATION, "input_closure": closure,
            "input_identity": digest(closure), "sealed_image_changed": False,
            "complete_Base23": False, "consumer_admission": "NOT_GRANTED",
            "calendar_inputs": ["calendar.parquet"], "venue_input": "venues.parquet",
            "physical_members": {str(p.relative_to(root)): {"sha256": file_sha(p),
                "size_bytes": p.stat().st_size} for p in root.rglob("*") if p.is_file()}}
    seal(root, body)
    return body


def seal(root, body):
    body.pop("identity", None)
    body["identity"] = digest(body)
    (root / "SCOPE.json").write_bytes(canonical(body) + b"\n")
    (root / "READY").write_text(file_sha(root / "SCOPE.json") + "\n")


def audit(root, tmp):
    with Budget(tmp, [root], Limits(allocated_bytes=2**26, temp_bytes=2**24,
                                  free_floor_bytes=1)) as budget:
        return a.audit_low_cost_scope(root, budget)


def test_complete_calendar_offline_replay(tmp_path):
    root = tmp_path / "package"
    package(root)
    assert audit(root, tmp_path / "tmp")["status"] == "LOW_COST_SCOPED_EXACT_REPLAY_PASS"


@pytest.mark.parametrize("damage", ["missing", "corrupt", "extra", "qualification",
                                   "code", "purpose", "false_scope"])
def test_fail_closed_even_for_resealed_contract_changes(tmp_path, damage):
    root = tmp_path / "package"
    body = package(root)
    if damage == "missing":
        (root / "calendar.parquet").unlink()
    elif damage == "corrupt":
        (root / "calendar.parquet").write_bytes(b"corrupt")
    elif damage == "extra":
        (root / "extra.json").write_text("{}")
    elif damage == "qualification":
        body["qualification"] = "unqualified"
    elif damage == "false_scope":
        body["complete_Base23"] = True
    else:
        closure = body["input_closure"]
        if damage == "purpose":
            closure["purpose"] = "research"
        else:
            closure["code_closure"] = {}
        body["input_identity"] = digest(closure)
    seal(root, body)
    with pytest.raises(BoundedError):
        audit(root, tmp_path / "tmp")


def test_rehashed_date_change_hits_semantic_rule(tmp_path):
    root = tmp_path / "package"
    body = package(root)
    p = root / "calendar.parquet"
    rows = pq.ParquetFile(p).read().to_pylist()
    rows[0]["is_open"] = not rows[0]["is_open"]
    pq.write_table(pa.Table.from_pylist(rows), p)
    body["physical_members"][p.name] = {"sha256": file_sha(p), "size_bytes": p.stat().st_size}
    seal(root, body)
    with pytest.raises(BoundedError, match="DATE_FLAG_CONFLICT"):
        audit(root, tmp_path / "tmp")
