"""Complete source components may extend exact unchanged native observations."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22_holder_component_union import (
    check_component_global_keys,
    iter_selected_frames,
    qualify_component_additions,
    register_selection_views,
)
from quanthus.pipelines.securities_warehouse.base22_holder_components_v2 import TABLE
from tests.unit.test_base22_holder_components import BODY
from tests.unit.test_base22_holder_components_v2 import project


def case():
    rows = project(BODY)[0][TABLE]
    return {
        "current": [dict(rows[0]), dict(rows[2])],
        "source": rows,
        "source_identity": "sha256:" + "a" * 64,
        "native_identity": "sha256:" + "b" * 64,
        "observed_at": datetime(2026, 10, 8, tzinfo=timezone.utc),
    }


def test_extra_literal_component_keeps_native_atoms_clocks_and_no_exit_inference():
    args = case()
    before = [dict(r) for r in args["current"]]
    result = qualify_component_additions(**args)
    assert result["status"] == "EXACT_UNCHANGED_NATIVE_PLUS_LITERAL_COMPONENTS"
    assert len(result["new_rows"]) == 1
    assert result["new_rows"][0]["share_nature"] == "限售A股"
    assert result["new_rows"][0]["knowledge_from"] == args["observed_at"]
    assert result["new_rows"][0]["holding_quantity"] == args["source"][1]["holding_quantity"]
    assert args["current"] == before and result["closed_native_versions"] == 0


def test_new_complete_frame_needs_no_old_base_record():
    args = case()
    args["current"] = []
    result = qualify_component_additions(**args)
    assert len(result["new_rows"]) == 3


def test_exact_identical_complete_frame_is_reused_without_rebuilding_history():
    args = case()
    args["current"] = [dict(r) for r in args["source"]]
    result = qualify_component_additions(**args)
    assert result["new_rows"] == [] and result["unchanged_native_rows"] == 3


@pytest.mark.parametrize(
    "defect",
    [
        "quantity",
        "missing-current-holder",
        "different-provider",
        "duplicate",
        "different-frame",
        "clock",
        "raw-percentage",
    ],
)
def test_ambiguous_source_or_nonidentical_current_values_are_held(defect):
    args = case()
    if defect == "quantity":
        args["current"][0]["holding_quantity"] += 1
    elif defect == "missing-current-holder":
        args["source"].pop()
    elif defect == "different-provider":
        args["current"][0]["record_source"] = "unqualified-other-provider"
    elif defect == "duplicate":
        args["source"].append(dict(args["source"][0]))
    elif defect == "different-frame":
        args["source"][0]["holding_type"] = "other-frame"
    elif defect == "clock":
        args["observed_at"] = args["source"][0]["knowledge_from"]
    else:
        args["current"][0]["raw_holding_percentage"] = "1.1234567890120%"
    try:
        result = qualify_component_additions(**args)
    except RuntimeError:
        return
    assert result["status"].startswith("HELD") and result["new_rows"] == []


def selection(tmp_path, current, source):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas
    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, GlobalScan
    from tests.unit.test_base22_bounded import limits

    _, model, extension = load_contract(Path("config/pipelines"))
    schema = schemas(model, extension)[TABLE]
    budget = Budget(tmp_path / "job", [], limits(memory_bytes=256 * 1024**2))
    budget.__enter__()
    paths = []
    for name, rows in [("native", current), ("source", source)]:
        path = budget.root / (name + ".parquet")
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
        paths.append(path)
    scan = GlobalScan(budget)
    records = [
        {
            "source_record_id": r["source_record_id"],
            "source_artifact_sha256": r["source_artifact_sha256"],
        }
        for r in source[:1]
    ]
    queries = register_selection_views(scan, paths[:1], paths[1:], records, schema)
    return budget, scan, schema, queries


def test_typed_sql_selection_and_stream_share_exact_nullable_component_semantics(tmp_path):
    args = case()
    args["source"][1]["share_nature"] = None
    args["source"][1]["holding_percentage_unit"] = None
    budget, scan, schema, queries = selection(tmp_path, args["current"], args["source"])
    try:
        expected = scan.connection.execute(queries["eligibility"]).fetchall()
        assert len(expected) == 1 and expected[0][-1] == 1
        frames = list(iter_selected_frames(scan, queries["ordered_frames"], schema))
        assert len(frames) == 1
        current, source = frames[0]
        result = qualify_component_additions(**{**args, "current": current, "source": source})
        assert len(result["new_rows"]) == 1
        assert result["new_rows"][0]["share_nature"] is None
        check_component_global_keys(scan, "component_source_all")
    finally:
        scan.__exit__(None, None, None)
        budget.__exit__(None, None, None)


@pytest.mark.parametrize("defect", ["value", "provider", "missing-holder"])
def test_typed_sql_holds_entire_nonidentical_existing_frame(tmp_path, defect):
    args = case()
    if defect == "value":
        args["current"][0]["holding_quantity"] += 1
    elif defect == "provider":
        args["current"][0]["record_source"] = "another-provider"
    else:
        args["source"].pop()
    budget, scan, _, queries = selection(tmp_path, args["current"], args["source"])
    try:
        assert scan.connection.execute(queries["eligibility"]).fetchall() == []
        assert scan.connection.execute("SELECT count(*) FROM held_frames").fetchone() == (1,)
    finally:
        scan.__exit__(None, None, None)
        budget.__exit__(None, None, None)


@pytest.mark.parametrize("defect", ["duplicate", "overlap", "required-null"])
def test_nullable_global_component_keys_reject_cross_file_defects(tmp_path, defect):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError

    args = case()
    row = dict(args["source"][0], share_nature=None, holding_percentage_unit=None)
    budget, scan, schema, _ = selection(tmp_path, [row], args["source"])
    try:
        second = dict(row)
        if defect == "overlap":
            second["knowledge_from"] = args["observed_at"]
        elif defect == "required-null":
            second["holder_rank"] = None
        path = budget.root / "other-partition.parquet"
        broken_schema = pa.schema([pa.field(f.name, f.type, nullable=True) for f in schema])
        pq.write_table(pa.Table.from_pylist([second], schema=broken_schema), path)
        scan.add("combined", [budget.root / "native.parquet", path])
        with pytest.raises(BoundedError, match="COMPONENT_UNION_"):
            check_component_global_keys(scan, "combined")
    finally:
        scan.__exit__(None, None, None)
        budget.__exit__(None, None, None)


@pytest.mark.parametrize("defect", ["purpose", "ready", "PIT", "extra-file", "evaluator"])
def test_offline_union_rejects_resigned_wrong_scope_before_opening_sources(tmp_path, defect):
    from quanthus.pipelines.securities_warehouse import base22_holder_component_union as module
    from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha
    from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError

    body = {
        "contract_id": module.CONTRACT_ID,
        "purpose": module.PURPOSE,
        "evaluator_sha256": file_sha(Path(module.__file__)),
        "consumer_admission": "NOT_GRANTED",
        "READY": False,
        "complete_Base22": False,
        "complete_weekly_union": False,
        "historical_PIT_admission": False,
        "physical_members": {},
    }
    if defect == "purpose":
        body["purpose"] = "accepted-full-Base22"
    elif defect == "ready":
        body["READY"] = True
    elif defect == "PIT":
        body["historical_PIT_admission"] = True
    elif defect == "evaluator":
        body["evaluator_sha256"] = "sha256:" + "0" * 64
    else:
        (tmp_path / "unlisted-file").write_bytes(b"disposable")
    body["identity"] = digest(body)
    (tmp_path / "QUALIFICATION.json").write_bytes(b"disposable")
    with pytest.raises(BoundedError, match="OFFLINE_DESCRIPTOR_OR_PHYSICAL_SET"):
        module.audit_component_union(tmp_path, body, None)
