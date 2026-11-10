"""Exact model-key changes preserve old scalar history and all unaffected atoms."""

from copy import deepcopy
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract, partition
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_class_history_merge import (
    merge_class_partition,
)
from quanthus.pipelines.securities_warehouse.base22_notice_class_revision import TABLES
from tests.unit.test_base22_notice_class_revision import fixture, project


@pytest.mark.parametrize("table", TABLES)
def test_exact_complete_class_revision_merges_without_changing_any_other_atom(table):
    c = fixture()
    actions, terms, _ = project(c)
    old, patch = ([c[1]], actions) if table == TABLES[0] else (c[2], terms)
    rule = load_contract(Path("config/pipelines"))[0]["tables"][table]
    before = deepcopy(old)
    unaffected = deepcopy(old[0])
    unaffected["corporate_action_id"] = "other"
    if table == TABLES[1]:
        unaffected["corporate_action_term_id"] = "otherterm"
    history = old + [unaffected]
    frozen = deepcopy(history)
    output, proof = merge_class_partition(
        table, partition(old[0], rule), rule, history, before, patch
    )
    assert history == frozen and unaffected in output
    assert output[0] == patch[0]
    assert proof["closed_versions"] == 1
    assert proof["new_versions"] == (1 if table == TABLES[0] else 2)


@pytest.mark.parametrize(
    "defect",
    [
        "old_hash",
        "closure",
        "missing_class",
        "duplicate",
        "unknown_role",
        "new_clock",
        "other_event",
        "other_effective",
        "generic_cash",
        "option",
        "unbound_old_group",
    ],
)
def test_class_group_incomplete_unbound_or_unapproved_changes_are_rejected(defect):
    c = fixture()
    _, patch, _ = project(c)
    old = c[2]
    history = deepcopy(old)
    rule = load_contract(Path("config/pipelines"))[0]["tables"][TABLES[1]]
    if defect == "old_hash":
        history[0]["amount_per_share"] += 1
    elif defect == "closure":
        patch[0]["amount_per_share"] += 1
    elif defect == "missing_class":
        patch.pop()
    elif defect == "duplicate":
        patch.append(deepcopy(patch[-1]))
    elif defect == "unknown_role":
        patch[-1]["term_type"] = "SOURCE_CLASS_GROSS_CASH_GUESSED"
    elif defect == "new_clock":
        patch[-1]["knowledge_from"] = old[0]["knowledge_from"]
    elif defect == "other_event":
        patch[-1]["corporate_action_id"] = "other"
    elif defect == "other_effective":
        patch[-1]["effective_at"] = None
    elif defect == "generic_cash":
        patch[-1]["term_type"] = "CASH_AMOUNT_PER_SHARE"
    elif defect == "option":
        patch[-1]["option_id"] = "option"
    else:
        history.append({**old[0], "corporate_action_term_id": "unselected", "term_type": "OTHER"})
    with pytest.raises(BoundedError, match="CLASS_HISTORY"):
        merge_class_partition(TABLES[1], partition(old[0], rule), rule, history, old, patch)


@pytest.mark.parametrize("defect", [None, "resealed_new_class_atom", "resealed_old_atom"])
def test_offline_full_resealed_patch_still_needs_the_qualified_source_atoms(
    tmp_path, monkeypatch, defect
):
    import os
    import shutil
    from types import SimpleNamespace

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha, schemas
    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits, semantic_scan
    from quanthus.pipelines.securities_warehouse.base22_class_history_merge import (
        PURPOSE,
        audit_class_history_scope,
    )
    from quanthus.pipelines.securities_warehouse.base22_notice_class_revision import CONTRACT_ID
    from quanthus.pipelines.securities_warehouse.base22_sources import _tree

    c = fixture()
    actions, terms, source_proof = project(c)
    image = tmp_path / "image"
    image.mkdir()
    shutil.copytree("config/pipelines", image / "contract")
    _, model, extension = load_contract(image / "contract")
    ss = schemas(model, extension)
    evidence = image / "evidence"
    evidence.mkdir()
    source = {"contract_id": CONTRACT_ID, "proofs": {"action": source_proof}}
    source["identity"] = digest(source)
    (evidence / "qualified-source-descriptor.json").write_bytes(canonical(source))
    registration = {
        "scope_identity": source["identity"],
        "descriptor_sha256": file_sha(evidence / "qualified-source-descriptor.json"),
    }
    (evidence / "qualified-source-cold-registration.json").write_bytes(canonical(registration))
    statuses = ["LOCAL_PASS", "LOCAL_PASS", "EXPECTED_FAIL_CLOSED", "EXPECTED_FAIL_CLOSED"]
    for label, identity in [("history_cold", "history"), ("cash_cold", source["identity"])]:
        proof = {
            "scope_identity": identity,
            "source_unchanged": True,
            "results": [
                {"status": status, "old_source_reads": 0, "network_calls": 0} for status in statuses
            ],
        }
        (evidence / (label + ".json")).write_bytes(canonical(proof))
    if defect == "resealed_new_class_atom":
        terms[-1]["amount_per_share"] += 1
    if defect == "resealed_old_atom":
        c[2][0]["amount_per_share"] += 1
        terms[0]["amount_per_share"] += 1
    body = {
        "purpose": PURPOSE,
        "READY": False,
        "complete_Base22": False,
        "consumer_admission": "NOT_GRANTED",
        "outputs": [],
        "partition_bindings": {},
        "parents": {},
        "output_rows": {},
        "selected_versions_each": {},
        "added_versions_each": {},
        "history_scope_identity": "history",
        "cash_scope_identity": source["identity"],
    }

    def write(table, name, rows):
        path = image / (table + "-" + name + ".parquet")
        pq.write_table(pa.Table.from_pylist(rows, schema=ss[table]), path)
        return {"table": table, "path": path.name, "rows": len(rows)}

    for table, old, patch in [(TABLES[0], [c[1]], actions), (TABLES[1], c[2], terms)]:
        rule = load_contract(image / "contract")[0]["tables"][table]
        part = partition(old[0], rule)
        output, proof = merge_class_partition(table, part, rule, old, old, patch)
        binding = {
            "history": write(table, "history", old),
            "before": write(table, "before", old),
            "patch": write(table, "patch", patch),
            "proof": proof,
        }
        item = write(table, "output", output)
        item.update(partition=part, semantic_scan=semantic_scan([image / item["path"]], ss[table]))
        body["outputs"].append(item)
        body["partition_bindings"][table] = {part: binding}
        body["output_rows"][table] = len(output)
        body["selected_versions_each"][table] = len(old)
        body["added_versions_each"][table] = len(patch) - len(old)
    for table, column, value in [
        ("dwd_security", "security_id", "security"),
        ("dwd_listed_company", "company_id", "company"),
    ]:
        path = image / (table + ".parquet")
        pq.write_table(pa.table({column: [value]}), path)
        body["parents"][table] = [{"path": path.name}]
    body["physical_members"] = {
        p: {"sha256": file_sha(image / p), "size_bytes": (image / p).stat().st_size}
        for p in _tree(image)
    }
    body["identity"] = digest(body)
    (image / "QUALIFICATION.json").write_bytes(canonical(body))
    actual = os.statvfs
    monkeypatch.setattr(
        os,
        "statvfs",
        lambda p: SimpleNamespace(f_flag=os.ST_RDONLY) if Path(p) == image else actual(p),
    )
    with Budget(tmp_path / "TMP", [image], Limits(free_floor_bytes=1)) as budget:
        if defect is None:
            receipt = audit_class_history_scope(image, body, budget)
            assert receipt["added_versions_each"] == {TABLES[0]: 1, TABLES[1]: 2}
        else:
            with pytest.raises(BoundedError, match="TYPED_PATCH_NOT_ADMITTED_BY_SOURCE"):
                audit_class_history_scope(image, body, budget)
