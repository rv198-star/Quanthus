from __future__ import annotations

import copy
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base24_delivery import (
    BASE24_LIMITATION_IDS,
    HARD_CHECKS,
    INTERVALS,
    ONE_MINUTE_DISPOSITION,
    PURPOSE,
    assess_delivery,
    validate_contract,
)
from quanthus.pipelines.securities_warehouse.base24_image import (
    BAR_TABLE,
    KIND,
    PROFILE,
    build_composition_plan,
    validate_composition,
    validate_default_intervals,
)

CONFIG = Path("config/pipelines/base24-delivery.v1.json")


def base24_limitations():
    evidence = "evidence/base24/bounded-limitations.v1.json"
    return [
        {
            "id": "base24-bounded-continuous-session-turnover",
            "tables": [BAR_TABLE],
            "scope_kind": "security_days",
            "scope": ["SSE/600265/2023-02-10"],
            "disposition": "QUALIFIED_5M_CONTINUOUS_SESSION_ONLY",
            "blocked_uses": ["whole_day_turnover_from_5m"],
            "risk": "bounded",
            "repair_cost": "authority",
            "evidence_paths": [evidence],
        },
        {
            "id": "base24-exact-5m-unavailable",
            "tables": [BAR_TABLE],
            "scope_kind": "security_days",
            "scope": ["SZSE/000555/2023-12-21"],
            "disposition": "PRESERVE_UNAVAILABLE",
            "blocked_uses": ["5m_bar_on_exact_security_day"],
            "risk": "bounded",
            "repair_cost": "authority",
            "evidence_paths": [evidence],
        },
        {
            "id": "base24-optional-1m-not-included",
            "tables": [BAR_TABLE],
            "scope_kind": "interval",
            "scope": ["1m"],
            "disposition": "NOT_INCLUDED_DIAGNOSTIC_ONLY",
            "blocked_uses": ["1m_consumption"],
            "risk": "out of scope",
            "repair_cost": "none",
            "evidence_paths": [evidence],
        },
    ]


def test_base24_contract_is_explicit_successor_and_does_not_delete_retained_1m():
    contract = json.loads(CONFIG.read_bytes())
    validate_contract(contract)
    assert contract["required_intervals"] == list(INTERVALS)
    assert contract["optional_1m_disposition"] == ONE_MINUTE_DISPOSITION
    assert contract["derive_5m_from_1m"] is False
    assert PROFILE.kind == KIND
    assert PROFILE.purpose == PURPOSE
    assert dict(PROFILE.manifest_fields)["default_intervals"] == ["1d", "5m"]


def test_base24_delivery_accepts_only_evidence_bound_frequency_limitations():
    evidence_path = "evidence/base24/bounded-limitations.v1.json"
    result = assess_delivery(
        contract=json.loads(CONFIG.read_bytes()),
        tables={BAR_TABLE},
        limitations=base24_limitations(),
        evidence={evidence_path: "sha256:" + "a" * 64},
        checks=dict.fromkeys(HARD_CHECKS, "PASS"),
    )
    assert result["build_eligible"] is True
    assert result["known_limitation_count"] == 3
    assert {item["id"] for item in result["limitations"]} == BASE24_LIMITATION_IDS


@pytest.mark.parametrize("field,value", [
    ("scope_kind", "table_window"),
    ("disposition", "PRESERVE_UNAVAILABLE"),
    ("blocked_uses", ["cross_provider_exact_price"]),
])
def test_base24_frequency_limitation_contract_drift_fails(field, value):
    limitations = base24_limitations()
    limitations[0][field] = value
    with pytest.raises(BoundedError, match="BASE24_LIMITATION_UNBOUND_OR_UNSCOPED"):
        assess_delivery(
            contract=json.loads(CONFIG.read_bytes()),
            tables={BAR_TABLE},
            limitations=limitations,
            evidence={
                "evidence/base24/bounded-limitations.v1.json": "sha256:" + "a" * 64
            },
            checks=dict.fromkeys(HARD_CHECKS, "PASS"),
        )


@pytest.mark.parametrize("field", ["required_intervals", "derive_5m_from_1m", "purpose"])
def test_base24_contract_frequency_or_purpose_drift_fails(field):
    contract = json.loads(CONFIG.read_bytes())
    contract[field] = ["1d", "1m", "5m"] if field == "required_intervals" else "changed"
    with pytest.raises(BoundedError, match="BASE24_DELIVERY_CONTRACT_CHANGED"):
        validate_contract(contract)


def composition_fixture():
    tables = {}
    for index in range(33):
        table = BAR_TABLE if index == 0 else f"table_{index:02d}"
        source = "native5m_bar" if table == BAR_TABLE else "predecessor"
        tables[table] = {
            "source_ids": [source],
            "files": [{"source_id": source, "path": f"{table}.parquet"}],
        }
    plan = {"sources": {name: {} for name in ("predecessor", "native5m_bar")}, "tables": tables}
    descriptors = {name: {} for name in plan["sources"]}
    return plan, descriptors


def test_exact_33_table_composition_replaces_only_security_bar():
    plan, descriptors = composition_fixture()
    before = copy.deepcopy((plan, descriptors))
    validate_composition(plan, descriptors)
    assert (plan, descriptors) == before


@pytest.mark.parametrize("damage", ["source", "bar", "other", "table"])
def test_composition_drift_fails_closed(damage):
    plan, descriptors = composition_fixture()
    if damage == "source":
        plan["sources"]["unknown"] = {}
    elif damage == "bar":
        plan["tables"][BAR_TABLE]["source_ids"] = ["predecessor"]
    elif damage == "other":
        plan["tables"]["table_01"]["files"][0]["source_id"] = "native5m_bar"
    else:
        plan["tables"].pop("table_32")
    with pytest.raises(BoundedError, match="BASE24_"):
        validate_composition(plan, descriptors)


@pytest.mark.parametrize(
    "intervals,passes",
    [(["1d", "5m"], True), (["1d", "1m", "5m"], False), (["1d"], False)],
)
def test_physical_default_bar_interval_set_is_exact(tmp_path, intervals, passes):
    path = tmp_path / "bars.parquet"
    pq.write_table(pa.table({"interval_size": intervals}), path)
    if passes:
        validate_default_intervals(BAR_TABLE, [path])
    else:
        with pytest.raises(BoundedError, match="DEFAULT_INTERVAL_SET_CHANGED"):
            validate_default_intervals(BAR_TABLE, [path])


def test_interval_validator_does_not_apply_unreviewed_rule_to_other_tables(tmp_path):
    path = tmp_path / "other.parquet"
    pq.write_table(pa.table({"unrelated": ["1m"]}), path)
    validate_default_intervals("dwd_tradability_state", [path])


@pytest.mark.parametrize("rows_in_manifest", [True, False])
def test_composition_plan_binds_predecessor_32_tables_and_whole_bar_package(
    tmp_path, rows_in_manifest
):
    predecessor, bar = tmp_path / "predecessor", tmp_path / "bar"
    (predecessor / "evidence").mkdir(parents=True)
    (bar / "payload/dwd_security_bar/interval_size=5m/year=2026").mkdir(parents=True)
    (bar / "evidence").mkdir()
    table_names = [BAR_TABLE, *(f"table_{index:02d}" for index in range(1, 33))]
    lineage = [["source", "revision", "sha256:" + "a" * 64]]
    layout, plan_tables = {}, {}
    for table in table_names:
        item = {
            "path": f"payload/{table}/all/part.parquet",
            "partition": "all",
            "rows": 1,
            "size_bytes": 10,
            "sha256": "sha256:" + "b" * 64,
        }
        layout[table] = {"files": [item], "expected_partition_rows": {"all": 1}}
        plan_tables[table] = {"admitted_lineage": lineage}
    materialization = {"layout": layout}
    (predecessor / "MATERIALIZATION.json").write_text(json.dumps(materialization))
    (predecessor / "evidence/input-plan.json").write_text(json.dumps({"tables": plan_tables}))
    predecessor_manifest = {"business_through": "2026-09-30"}
    (predecessor / "manifest.json").write_text(json.dumps(predecessor_manifest))
    (predecessor / "READY").write_text(file_sha(predecessor / "manifest.json") + "\n")

    bar_path = bar / "payload/dwd_security_bar/interval_size=5m/year=2026/part.parquet"
    pq.write_table(
        pa.table(
            {
                "record_source": ["old", "native5m"],
                "source_revision": ["v1", "v2"],
                "source_artifact_sha256": ["sha256:" + "c" * 64, "sha256:" + "d" * 64],
            }
        ),
        bar_path,
    )
    bar_member = {
        "path": bar_path.relative_to(bar).as_posix(),
        "sha256": file_sha(bar_path).removeprefix("sha256:"),
        "size_bytes": bar_path.stat().st_size,
    }
    if rows_in_manifest:
        bar_member["rows"] = 2
    bar_manifest = {
        "business_through": "2026-09-30",
        "default_intervals": ["1d", "5m"],
        "one_minute_disposition": ONE_MINUTE_DISPOSITION,
        "consumer_admission": "NOT_GRANTED",
        "files": [bar_member],
    }
    if not rows_in_manifest:
        receipt = {
            "contract": "p10-base24-native5m-global-transfer-receipt-v1",
            "files": [{**bar_member, "rows": 2}],
        }
        receipt["identity"] = digest(receipt)
        (bar / "evidence/global-transfer-receipt.json").write_bytes(
            canonical(receipt) + b"\n"
        )
        bar_manifest["global_transfer_identity"] = receipt["identity"]
    (bar / "manifest.json").write_text(json.dumps(bar_manifest))
    (bar / "READY").write_text(
        file_sha(bar / "manifest.json").removeprefix("sha256:") + "\n"
    )
    plan = build_composition_plan(predecessor, bar, {"tables": {name: {} for name in table_names}})
    assert len(plan["tables"]) == 33
    assert plan["tables"][BAR_TABLE]["source_ids"] == ["native5m_bar"]
    assert plan["tables"]["table_01"]["source_ids"] == ["predecessor"]
    assert plan["tables"][BAR_TABLE]["rows"] == 2
    assert plan["tables"][BAR_TABLE]["admitted_lineage"] == [
        ["native5m", "v2", "sha256:" + "d" * 64],
        ["old", "v1", "sha256:" + "c" * 64],
    ]
