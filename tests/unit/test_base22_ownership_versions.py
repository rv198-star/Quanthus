"""Actual typed global sort, bounded keys and precise successor intervals."""

import json
import subprocess
import sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_ownership_versions as v
from quanthus.pipelines.securities_warehouse.base22 import (
    canonical,
    file_sha,
    load_contract,
    schemas,
)
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.base22_f10_ownership import TABLES
from quanthus.pipelines.securities_warehouse.base22_ownership_versions import (
    ownership_versions,
    version_batches,
)
from quanthus.pipelines.securities_warehouse.ownership_capital import ownership_capital_schemas
from tests.unit.test_base22_f10_ownership import project, raw
from tests.unit.test_base22_independent_image import _row
from tests.unit.test_base22_ownership_audit import image, seal


def case():
    row = project(raw())[0][TABLES[0]][0]
    rule = load_contract(Path("config/pipelines"))[0]["tables"][TABLES[0]]
    return row, rule


def test_preserve_original_atoms_and_close_at_actual_changed_successor():
    old, rule = case()
    new = deepcopy(old)
    new["knowledge_from"] += timedelta(days=7)
    new["share_quantity"] += 1
    before = deepcopy([old, new])
    result = ownership_versions([new, old], rule)
    assert [r["share_quantity"] for r in result] == [old["share_quantity"], new["share_quantity"]]
    assert result[0]["knowledge_to"] == new["knowledge_from"]
    assert result[1]["knowledge_to"] is None and [old, new] == before


def test_identical_later_observation_is_not_a_fake_body_revision():
    old, rule = case()
    new = deepcopy(old)
    new["knowledge_from"] += timedelta(days=7)
    assert ownership_versions([new, old], rule) == [old]


@pytest.mark.parametrize(
    "defect", ["closed", "equal-clock-conflict", "other-key", "overflow", "empty"]
)
def test_source_or_interval_gaps_cannot_be_hidden_as_sorted_versions(defect):
    old, rule = case()
    new = deepcopy(old)
    rows = [old, new]
    if defect == "closed":
        old["knowledge_to"] = old["knowledge_from"] + timedelta(days=1)
    elif defect == "equal-clock-conflict":
        new["share_quantity"] += 1
    elif defect == "other-key":
        new["security_id"] = "other"
    elif defect == "overflow":
        rows *= 33
    else:
        rows = []
    with pytest.raises(RuntimeError, match="OWNERSHIP|EQUAL_CLOCK"):
        ownership_versions(rows, rule)


def test_cross_file_business_keys_and_actual_arrow_sort_have_identical_result(tmp_path):
    old, rule = case()
    schema = ownership_capital_schemas()[TABLES[0]]
    later = deepcopy(old)
    later["share_quantity"] += 1
    later["knowledge_from"] += timedelta(days=7)
    other = deepcopy(old)
    other["security_id"] = "other"
    limits = Limits(allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2, free_floor_bytes=1)
    with Budget(tmp_path / "job", [], limits) as budget:
        paths = []
        for n, rows in enumerate([[later, other], [old]]):
            p = budget.root / f"input-{n}.parquet"
            pq.write_table(pa.Table.from_pylist(rows, schema=schema), p)
            paths.append(p)
        result = [
            r for group in version_batches(TABLES[0], paths, rule, schema, budget) for r in group
        ]
        assert len(result) == 3
        same = [r for r in result if r["security_id"] == old["security_id"]]
        assert same == ownership_versions([old, later], rule)


def test_external_mutable_source_file_cannot_be_used_as_a_job_local_input(tmp_path):
    old, rule = case()
    schema = ownership_capital_schemas()[TABLES[0]]
    source = tmp_path / "source"
    source.mkdir()
    external = source / "external.parquet"
    pq.write_table(pa.Table.from_pylist([old], schema=schema), external)
    limits = Limits(allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2, free_floor_bytes=1)
    with Budget(tmp_path / "job", [source], limits) as budget, pytest.raises(BoundedError):
        list(version_batches(TABLES[0], [external], rule, schema, budget))


def union_image(root, tmp):
    root.mkdir()
    obs = image(root / "observations")
    observation_root = root / "observations"
    contract, model, extension = load_contract(observation_root / "contract")
    ss = schemas(model, extension)
    company = _row(ss["dwd_listed_company"])
    sec = pq.read_table(observation_root / "dwd_security.parquet").to_pylist()[0]
    company.update(
        company_id="company",
        effective_from=sec["effective_from"],
        knowledge_from=sec["knowledge_from"],
    )
    p = observation_root / "dwd_listed_company.parquet"
    pq.write_table(pa.Table.from_pylist([company], schema=ss["dwd_listed_company"]), p)
    obs["parents"]["dwd_listed_company"] = [{"path": p.name, "rows": 1}]
    seal(observation_root, obs)
    old, rule = case()
    old["knowledge_from"] = sec["knowledge_from"]
    native = root / "native.parquet"
    pq.write_table(pa.Table.from_pylist([old], schema=ss[TABLES[0]]), native)
    original = (
        f"payload/ownership_capital/{TABLES[0]}/observation_start_year=2026/synthetic.parquet"
    )
    (root / "native-manifest.json").write_bytes(
        canonical({"files": [{"path": original, "sha256": file_sha(native)}]})
    )
    body = {
        "contract_id": v.CONTRACT_ID,
        "purpose": "QUALIFIED_OPEN_OWNERSHIP_OBSERVATION_NATIVE_UNION",
        "evaluator_sha256": file_sha(Path(v.__file__)),
        "consumer_admission": "NOT_GRANTED",
        "READY": False,
        "complete_Base22": False,
        "selected_tables": [TABLES[0]],
        "observations_root": "observations",
        "observation_scope_identity": obs["identity"],
        "original_native_manifest": "native-manifest.json",
        "original_native_manifest_sha256": file_sha(root / "native-manifest.json"),
        "native_members": [
            {"original_path": original, "path": native.name, "table": TABLES[0], "rows": 1}
        ],
        "parents": {t: obs["parents"][t] for t in ("dwd_security", "dwd_listed_company")},
        "outputs": [],
    }
    # Construction receives independent local typed copies inside its mutable Job.
    paths = [native] + [
        observation_root / i["path"] for i in obs["outputs"] if i["table"] == TABLES[0]
    ]
    import shutil

    limits = Limits(allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2, free_floor_bytes=1)
    with Budget(tmp, [root], limits) as budget:
        owned = []
        for n, p in enumerate(paths):
            dest = budget.root / f"input-{n}.parquet"
            shutil.copyfile(p, dest)
            owned.append(dest)
        result = [
            r
            for group in version_batches(TABLES[0], owned, rule, ss[TABLES[0]], budget)
            for r in group
        ]
    # The fixture's single source body is unchanged across all four real clocks.
    assert result == [old]
    p = root / "output.parquet"
    pq.write_table(pa.Table.from_pylist(result, schema=ss[TABLES[0]]), p)
    from quanthus.pipelines.securities_warehouse.base22 import partition
    from quanthus.pipelines.securities_warehouse.base22_bounded import semantic_scan

    body["outputs"] = [
        {
            "table": TABLES[0],
            "partition": partition(old, rule),
            "number": 0,
            "path": p.name,
            "rows": 1,
            "semantic_scan": semantic_scan([p], ss[TABLES[0]]),
        }
    ]
    body["output_rows"] = {TABLES[0]: 1}
    seal(root, body)
    return body


@pytest.mark.parametrize("defect", [None, "atom", "extra-empty", "missing-native", "foreign-key"])
def test_complete_union_under_actual_kernel_readonly_and_offline_process(tmp_path, defect):
    import os

    if os.geteuid() != 0:
        pytest.skip("actual kernel read-only namespace requires the current CN02 root environment")
    root = tmp_path / "image"
    body = union_image(root, tmp_path / "construction")
    if defect == "atom":
        p = root / "output.parquet"
        t = pq.ParquetFile(p).read()
        rows = t.to_pylist()
        rows[0]["share_quantity"] += 1
        pq.write_table(pa.Table.from_pylist(rows, schema=t.schema), p)
        from quanthus.pipelines.securities_warehouse.base22_bounded import semantic_scan

        body["outputs"][0]["semantic_scan"] = semantic_scan([p], t.schema)
    elif defect == "extra-empty":
        p = root / "extra.parquet"
        ss = ownership_capital_schemas()[TABLES[0]]
        pq.write_table(pa.Table.from_pylist([], schema=ss), p)
        body["outputs"].append(
            {**body["outputs"][0], "partition": "extra", "path": p.name, "number": 0, "rows": 0}
        )
    elif defect == "missing-native":
        body["native_members"] = []
    elif defect == "foreign-key":
        obs_root = root / "observations"
        obs = json.loads((obs_root / "QUALIFICATION.json").read_bytes())
        p = obs_root / "dwd_listed_company.parquet"
        t = pq.read_table(p)
        rows = t.to_pylist()
        rows[0]["company_id"] = "missing-company"
        pq.write_table(pa.Table.from_pylist(rows, schema=t.schema), p)
        seal(obs_root, obs)
        body["observation_scope_identity"] = obs["identity"]
    seal(root, body)
    program = """
import json,subprocess,sys
from pathlib import Path
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget,Limits,BoundedError
from quanthus.pipelines.securities_warehouse.base22_ownership_versions import audit_ownership_union
root=Path(sys.argv[1]);tmp=Path(sys.argv[2])
subprocess.run(['mount','--make-rprivate','/'],check=True)
subprocess.run(['mount','--bind',str(root),str(root)],check=True)
subprocess.run(['mount','-o','remount,bind,ro',str(root)],check=True)
try:
 limits=Limits(allocated_bytes=64*1024**2,temp_bytes=32*1024**2,free_floor_bytes=1)
 with Budget(tmp,[root],limits)as budget:
  result=audit_ownership_union(root,json.loads((root/'QUALIFICATION.json').read_bytes()),budget)
except BoundedError as exc:result={'failure':str(exc)}
print(json.dumps(result))
"""
    done = subprocess.run(
        [
            "unshare",
            "--mount",
            "--net",
            sys.executable,
            "-c",
            program,
            str(root),
            str(tmp_path / "audit"),
        ],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    if defect is None:
        assert result["output_rows"] == {TABLES[0]: 1}
        assert result["whole_domain_August_coverage"] == "NOT_QUALIFIED"
    else:
        assert "failure" in result
        if defect == "foreign-key":
            assert result["failure"].startswith("GLOBAL_FOREIGN_KEY_DATE_VENUE_CLOSURE_FAILED")
