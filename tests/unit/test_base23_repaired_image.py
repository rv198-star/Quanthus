from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import base23_repaired_image as r
from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError


def binding():
    item = {"path": "payload/original/all/part-000000.parquet", "partition": "all",
            "rows": 1, "size_bytes": 10, "sha256": "sha256:" + "a" * 64}
    scope = {"contract_id": r.CONTRACT_ID, "identity": "sha256:scope",
             "consumer_admission": "NOT_GRANTED", "complete_Base23": False,
             "outputs": {t: [dict(item)] for t in
                         ["dwd_corporate_action", "dwd_corporate_action_term"]},
             "parents": {t: [dict(item)] for t in ["dwd_security", "dwd_listed_company"]},
             "physical_members": {}}
    plan = {"sources": {"actions": {"descriptor": "SCOPE.json",
             "descriptor_sha256": "pending", "role": "qualified_domain_output",
             "root": "/unreachable-old-source",
             "quality_gate": "QUALIFIED_FOR_GOVERNANCE_RETENTION"}},
            "repair_acceptance": {"kind": r.KIND, "source_id": "actions",
             "local_prefix": r.PREFIX, "scope_identity": scope["identity"],
             "replay_identity": "sha256:replay"}, "tables": {}}
    for t in scope["outputs"]:
        plan["tables"][t] = {"source_ids": ["actions"],
                             "files": [{**item, "table": t, "source_id": "actions"}]}
    for t in scope["parents"]:
        plan["tables"][t] = {"files": [dict(item)]}
    return plan, scope


@pytest.mark.parametrize("damage", ["none", "missing_table", "partial", "quality",
                                    "parent", "missing_parent", "scope", "prefix"])
def test_exact_domain_replacement_and_parent_binding(damage):
    plan, scope = copy.deepcopy(binding())
    table = "dwd_corporate_action"
    if damage == "missing_table":
        del scope["outputs"][table]
    elif damage == "partial":
        plan["tables"][table]["files"] = []
    elif damage == "quality":
        plan["sources"]["actions"]["quality_gate"] = "unqualified"
    elif damage == "parent":
        plan["tables"]["dwd_security"]["files"][0]["sha256"] = "changed"
    elif damage == "missing_parent":
        del scope["parents"]["dwd_security"]
    elif damage == "scope":
        plan["repair_acceptance"]["scope_identity"] = "changed"
    elif damage == "prefix":
        plan["repair_acceptance"]["local_prefix"] = "/external"
    if damage == "none":
        assert r.validate_repair_binding(plan, scope) == "actions"
    else:
        with pytest.raises(BoundedError):
            r.validate_repair_binding(plan, scope)


def test_missing_repair_file_fails_before_assembly(tmp_path):
    plan, scope = binding()
    p = tmp_path / "SCOPE.json"
    scope["physical_members"] = {"missing.json": {"sha256": "missing", "size_bytes": 1}}
    p.write_text(json.dumps(scope))
    sha = file_sha(p)
    plan["sources"]["actions"]["descriptor_sha256"] = sha
    (tmp_path / "READY").write_text(sha)
    with pytest.raises(BoundedError, match="LOCAL_REPAIR_SET_CHANGED"):
        r.repair_local_evidence(tmp_path, plan)
    assert sorted(f.name for f in tmp_path.iterdir()) == ["READY", "SCOPE.json"]


def test_full_mode_runs_repair_before_whole_scan_and_binds_both(monkeypatch):
    calls = []
    def repair(root, budget):
        calls.append("repair")
        return {"identity": "replayed"}
    def native(root, budget):
        calls.append("full-33")
        return {"identity": "native"}
    monkeypatch.setattr(r, "audit_repair_evidence", repair)
    monkeypatch.setattr(r, "audit_native_image", native)
    result = r.audit_repaired_image(Path("/synthetic"), None)
    assert calls == ["repair", "full-33"]
    assert result["native_receipt"]["identity"] == "native"
    assert result["repair_receipt"]["identity"] == "replayed"
    assert result["consumer_admission"] == "NOT_GRANTED"


def test_failed_repair_cannot_reach_full_acceptance(monkeypatch):
    def reject(root, budget):
        raise BoundedError("synthetic raw closure failure")
    monkeypatch.setattr(r, "audit_repair_evidence", reject)
    monkeypatch.setattr(r, "audit_native_image", lambda *_: pytest.fail("full scan reached"))
    with pytest.raises(BoundedError, match="raw closure"):
        r.audit_repaired_image(Path("/synthetic"), None)


def test_owned_copy_has_new_inode_and_durable_recovery_journal_before_retirement(tmp_path):
    source = tmp_path / "owned-successful-TMP"
    source.write_bytes(b"synthetic bytes")
    target = tmp_path / "independent-copy"
    with (tmp_path / "journal").open("x") as journal:
        entry = r.copy_owned_member_then_retire(
            source, target, sha256=file_sha(source), size_bytes=source.stat().st_size,
            journal=journal, check=lambda _: None,
        )
    assert not source.exists()
    assert target.read_bytes() == b"synthetic bytes"
    assert entry["old_device_inode"] != entry["new_device_inode"]
    assert json.loads((tmp_path / "journal").read_text()) == entry


def test_failed_copy_or_budget_never_retires_source(tmp_path):
    source = tmp_path / "owned-successful-TMP"
    source.write_bytes(b"synthetic bytes")
    target = tmp_path / "copy"
    def stop(_):
        raise BoundedError("registered budget exhausted")
    with ((tmp_path / "journal").open("x") as journal,
          pytest.raises(BoundedError, match="budget")):
        r.copy_owned_member_then_retire(
            source, target, sha256=file_sha(source), size_bytes=source.stat().st_size,
            journal=journal, check=stop,
        )
    assert source.read_bytes() == b"synthetic bytes"
    assert not target.exists()
    assert not (tmp_path / "journal").read_text()


def test_failed_journal_preserves_original_even_after_successful_independent_copy(tmp_path):
    source = tmp_path / "owned-TMP"
    source.write_bytes(b"synthetic bytes")
    class BrokenJournal:
        def write(self, _):
            raise OSError("synthetic evidence storage failure")
    target = tmp_path / "copy"
    with pytest.raises(OSError, match="evidence storage"):
        r.copy_owned_member_then_retire(
            source, target, sha256=file_sha(source), size_bytes=source.stat().st_size,
            journal=BrokenJournal(), check=lambda _: None,
        )
    assert source.read_bytes() == target.read_bytes() == b"synthetic bytes"
