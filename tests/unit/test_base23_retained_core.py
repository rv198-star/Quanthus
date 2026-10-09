"""Retained Daily qualification preserves values and independently dated parents."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import (
    canonical,
    digest,
    file_sha,
    load_contract,
    partition,
    schemas,
)
from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    Limits,
    semantic_scan,
)
from quanthus.pipelines.securities_warehouse.base23_retained_core import (
    CONTRACT_ID,
    QUALIFICATION,
    audit_retained_scope,
    reobserve_partition,
)
from tests.unit.test_base23_core import run_day, september_case


def retained_case(table):
    raw, status, args = september_case()
    output, _, _ = run_day(raw, status, args)
    row = output[table][0]
    row["knowledge_from"] -= timedelta(days=1)
    if table == "dwd_security_bar":
        row["price_basis"] = "none"
    contract, model, extension = load_contract(Path("config/pipelines"))
    kwargs = dict(
        table=table,
        day=args["market_date"],
        schema=schemas(model, extension)[table],
        rule=contract["tables"][table],
        listings=[args["listing"]],
        indexes=[],
        original_calendars=[args["calendar"]],
        qualified_calendars=[args["calendar"]],
        observed_at=args["observed_at"],
        member_sha256=digest(["retained-file"]),
        package_manifest_sha256=digest(["whole-original-package"]),
        qualification=QUALIFICATION,
        input_digest=digest(["complete-input-closure"]),
    )
    return row, kwargs


@pytest.mark.parametrize("table", ["dwd_security_bar", "dwd_tradability_state"])
def test_new_governance_version_preserves_original_values_times_and_archive(tmp_path, table):
    row, kwargs = retained_case(table)
    # A physically typed retained Parquet has the schema's declared decimal scale.
    row = pa.Table.from_pylist([row], schema=kwargs["schema"]).to_pylist()[0]
    before = deepcopy((row, kwargs))
    output, proof = reobserve_partition([row], **kwargs)
    assert proof["original_raw_core_recovered"] is False
    assert proof["provider_acquisition"] is None
    assert proof["accepted_child_inferred"] is False
    assert proof["row_evidence"][0]["original_warehouse_knowledge_from"] == row["knowledge_from"]
    assert output[0]["knowledge_from"] == kwargs["observed_at"]
    assert output[0]["observation_start"] == row["observation_start"]
    if table == "dwd_security_bar":
        assert output[0]["price_basis"] == "UNADJUSTED"
        assert output[0]["close"].as_tuple() == row["close"].as_tuple()
    else:
        assert "interval_size" not in row
        assert all(output[0][f] == row[f] for f in ["is_tradable", "can_buy", "can_sell"])
    file = tmp_path / "retained.parquet"
    typed = pa.Table.from_pylist(output, schema=kwargs["schema"])
    pq.write_table(typed, file)
    assert pq.ParquetFile(file).read().equals(typed)
    assert (row, kwargs) == before


@pytest.mark.parametrize("table", ["dwd_security_bar", "dwd_tradability_state"])
@pytest.mark.parametrize("fault", ["original-date", "new-date", "original-venue", "new-venue"])
def test_both_original_and_new_calendar_date_venue_are_required(table, fault):
    row, kwargs = retained_case(table)
    kwargs["original_calendars"] = deepcopy(kwargs["original_calendars"])
    kwargs["qualified_calendars"] = deepcopy(kwargs["qualified_calendars"])
    name = "original_calendars" if fault.startswith("original") else "qualified_calendars"
    if fault.endswith("date"):
        kwargs[name][0]["trade_date"] += timedelta(days=1)
    else:
        kwargs[name][0]["venue_id"] = "another-market"
    with pytest.raises(BoundedError, match="CALENDAR_DATE_VENUE"):
        reobserve_partition([row], **kwargs)


@pytest.mark.parametrize("fault", ["grade", "future", "duplicate", "minute", "basis", "null"])
def test_unqualified_scope_or_missing_critical_atoms_never_reobserve(fault):
    row, kwargs = retained_case("dwd_security_bar")
    rows = [row]
    if fault == "grade":
        kwargs["qualification"] = "RAW_ORIGINAL_RECOVERED"
    elif fault == "future":
        row["knowledge_from"] = kwargs["observed_at"] + timedelta(seconds=1)
    elif fault == "duplicate":
        rows.append(deepcopy(row))
    elif fault == "minute":
        row["interval_size"] = "1m"
    elif fault == "basis":
        row["price_basis"] = "hfq"
    else:
        row["close"] = None
    with pytest.raises(BoundedError, match="RETAINED_CORE"):
        reobserve_partition(rows, **kwargs)


@pytest.mark.parametrize("table", ["dwd_security_valuation", "dwd_index_bar"])
def test_retained_typed_measures_preserve_decimal_null_zero_and_business_times(tmp_path, table):
    from quanthus.pipelines.securities_warehouse.base22_daily_measures import (
        index_observation,
        valuation_observation,
    )
    from tests.unit.test_base22_daily_measures import index_case, valuation_case

    _, kwargs = retained_case("dwd_security_bar")
    contract, model, extension = load_contract(Path("config/pipelines"))
    ss = schemas(model, extension)
    if table == "dwd_security_valuation":
        raw, args, _ = valuation_case()
        rows, _ = valuation_observation(raw, **args)
        kwargs["listings"] = [args["listing"]]
    else:
        raw, args, _ = index_case()
        rows, _ = index_observation(raw, **args)
        kwargs["indexes"] = [args["index"]]
    for row in rows:
        row["observation_start"] += timedelta(days=31)
        row["observation_end"] += timedelta(days=31)
    kwargs.update(table=table, schema=ss[table], rule=contract["tables"][table])
    rows = pa.Table.from_pylist(rows, schema=ss[table]).to_pylist()
    before = deepcopy(rows)
    output, proof = reobserve_partition(rows, **kwargs)
    assert proof["numeric_classifications"]["ZERO"] == 1
    assert proof["numeric_classifications"]["NULL_UNAVAILABLE"] == 1
    for original, result in zip(rows, output, strict=True):
        for field in ss[table]:
            if pa.types.is_decimal(field.type):
                assert original[field.name] == result[field.name]
                if original[field.name] is not None:
                    assert original[field.name].as_tuple() == result[field.name].as_tuple()
    path = tmp_path / "measures.parquet"
    pq.write_table(pa.Table.from_pylist(output, schema=ss[table]), path)
    assert pq.ParquetFile(path).read().to_pylist() == output
    assert rows == before


def synthetic_scope(root):
    """Full original package, exact parents and pinned output proof, all synthetic."""
    import json
    import shutil

    from quanthus.pipelines.securities_warehouse.base22_sources import _tree

    raw, args = retained_case("dwd_security_bar")
    contract, model, extension = load_contract(Path("config/pipelines"))
    ss = schemas(model, extension)
    root.mkdir()
    for name in [
        "base22-independent-image.v1.json",
        "dwd-domain-model.v1.json",
        "dwd-domain-model.v1.1-extension.json",
    ]:
        (root / "contract").mkdir(exist_ok=True)
        shutil.copyfile(Path("config/pipelines") / name, root / "contract" / name)

    def write(rel, rows, table):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows, schema=ss[table]), path)
        return path

    lineage = {
        "record_source": "synthetic-parent",
        "source_record_id": "synthetic",
        "source_revision": "v1",
        "source_artifact_sha256": digest(["parent"]),
    }
    listing = {
        **lineage,
        **args["listings"][0],
        "security_id": "security-one",
        "board_code": "MAIN",
        "lot_size": 100,
    }
    args["listings"] = [listing]
    index = {
        **lineage,
        "knowledge_from": listing["knowledge_from"],
        "knowledge_to": None,
        "effective_from": listing["effective_from"],
        "effective_to": None,
        "index_id": "index-one",
        "index_code": "sh.000001",
        "index_name": "synthetic",
        "base_currency": "CNY",
        "methodology_version": None,
    }
    args["indexes"] = [index]
    calendar = {
        **lineage,
        **args["qualified_calendars"][0],
        "observation_start": raw["observation_start"],
        "observation_end": raw["observation_end"],
        "open_at": None,
        "close_at": None,
        "calendar_reason": None,
    }
    args["qualified_calendars"] = [calendar]
    args["original_calendars"] = [calendar]
    parents = {}
    for table, rows in {
        "dwd_listing": [listing],
        "dwd_market_index": [index],
        "dwd_trading_calendar": [calendar],
    }.items():
        rel = "parents/" + table + ".parquet"
        write(rel, rows, table)
        parents[table] = rel
    rows_by_table = {
        "dwd_security_bar": [raw],
        "dwd_tradability_state": [retained_case("dwd_tradability_state")[0]],
        "dwd_index_bar": [
            {
                **{k: v for k, v in raw.items() if k in ss["dwd_index_bar"].names},
                "index_id": "index-one",
                "calendar_session_id": None,
            }
        ],
        "dwd_security_valuation": [
            {
                **{k: v for k, v in raw.items() if k in ss["dwd_security_valuation"].names},
                "security_id": "security-one",
                "measure_type": "pb",
                "measure_value": None,
                "currency": None,
                "methodology": "provider_reported",
                "fiscal_basis": "ttm_or_reported",
            }
        ],
    }
    sources, files, counts = {}, [], {}
    for table, rows in {**rows_by_table, "dwd_trading_calendar": [calendar]}.items():
        rel = "original/payload/" + table + "/part.parquet"
        path = write(rel, rows, table)
        counts[table] = len(rows)
        files.append(
            {
                "path": rel.removeprefix("original/"),
                "sha256": file_sha(path),
                "size_bytes": path.stat().st_size,
            }
        )
        if table != "dwd_trading_calendar":
            sources[table] = {"path": rel, "sha256": file_sha(path)}
    manifest = {
        "kind": "cn01_daily_dwd_domain_increment",
        "schema_version": 1,
        "package_id": "synthetic-retained",
        "market_date": str(args["day"]),
        "files": files,
        "content_digest": digest(files),
        "row_counts": counts,
    }
    (root / "original/manifest.json").write_bytes(canonical(manifest))
    manifest_sha = file_sha(root / "original/manifest.json")
    (root / "original/READY").write_text(manifest_sha + "\n")
    history_parents = {
        t: {"archive_path": "payload/" + t + ".parquet", "sha256": file_sha(root / parents[t])}
        for t in ["dwd_listing", "dwd_market_index"]
    }
    history = {
        "layout": {
            t: {"files": [{"path": v["archive_path"], "sha256": v["sha256"]}]}
            for t, v in history_parents.items()
        }
    }
    (root / "history.json").write_bytes(canonical(history))
    calendar_scope = {
        "source_qualification": "RETAINED_BYTE_VERIFIED_GOVERNANCE_NOT_PROVIDER_PIT",
        "calendar_projection_proof": {"full_month_coverage": True},
        "physical_members": {
            "payload/calendar.parquet": {"sha256": file_sha(root / parents["dwd_trading_calendar"])}
        },
    }
    calendar_scope["identity"] = digest(calendar_scope)
    (root / "calendar.json").write_bytes(canonical(calendar_scope))
    plan = {
        "purpose": CONTRACT_ID,
        "qualification": QUALIFICATION,
        "original_raw_core_recovered": False,
        "provider_acquisition": None,
        "source_ids_by_table": {t: [v["sha256"]] for t, v in sources.items()},
        "package_manifest_sha256": manifest_sha,
        "history_materialization_sha256": file_sha(root / "history.json"),
        "calendar_scope_sha256": file_sha(root / "calendar.json"),
    }
    body = {
        "contract_id": CONTRACT_ID,
        "input_closure": plan,
        "input_digest": digest(plan),
        "projection_module_sha256": file_sha(
            Path("src/quanthus/pipelines/securities_warehouse/base23_retained_core.py")
        ),
        "business_date": str(args["day"]),
        "observed_at": args["observed_at"],
        "original_manifest": "original/manifest.json",
        "original_calendar": "original/payload/dwd_trading_calendar/part.parquet",
        "history_materialization": "history.json",
        "history_parents": history_parents,
        "calendar_scope": "calendar.json",
        "calendar_archive_path": "payload/calendar.parquet",
        "parents": parents,
        "sources": sources,
        "outputs": {},
        "proofs": {},
        "complete_Base23": False,
        "READY": False,
        "consumer_admission": "NOT_GRANTED",
    }
    for table, source in sources.items():
        rows = pq.ParquetFile(root / source["path"]).read().to_pylist()
        args.update(
            table=table,
            schema=ss[table],
            rule=contract["tables"][table],
            member_sha256=source["sha256"],
            package_manifest_sha256=manifest_sha,
            input_digest=body["input_digest"],
        )
        output, proof = reobserve_partition(rows, **args)
        part = partition(output[0], contract["tables"][table])
        rel = f"payload/{table}/{part}/part.parquet"
        path = write(rel, output, table)
        body["outputs"][table] = {
            "path": rel,
            "partition": part,
            "semantic_scan": semantic_scan([path], ss[table]),
        }
        rel = "proof-" + table + ".json"
        (root / rel).write_bytes(canonical(proof))
        body["proofs"][table] = rel
    body["physical_members"] = {
        rel: {"sha256": file_sha(root / rel), "size_bytes": (root / rel).stat().st_size}
        for rel in _tree(root)
    }
    body["identity"] = digest(body)
    (root / "SCOPE.json").write_bytes(canonical(body))
    assert json.loads((root / "SCOPE.json").read_bytes())["identity"] == body["identity"]
    return body


def test_complete_retained_scope_offline_replay_and_fully_rewrapped_empty_fault(tmp_path):
    import shutil

    root = tmp_path / "scope"
    body = synthetic_scope(root)
    with Budget(tmp_path / "audit", [root], Limits(free_floor_bytes=1)) as budget:
        receipt = audit_retained_scope(root, budget)
    assert receipt["original_raw_core_recovered"] is False
    assert len(receipt["summaries"]) == 4
    copy = tmp_path / "disposable"
    shutil.copytree(root, copy, copy_function=shutil.copyfile)
    rel = "payload/dwd_tradability_state/observation_start_month=2026-10/empty.parquet"
    path = copy / rel
    path.parent.mkdir(parents=True)
    _, model, extension = load_contract(copy / "contract")
    schema = schemas(model, extension)["dwd_tradability_state"]
    pq.write_table(pa.Table.from_pylist([], schema=schema), path)
    body["physical_members"][rel] = {"sha256": file_sha(path), "size_bytes": path.stat().st_size}
    body.pop("identity")
    body["identity"] = digest(body)
    (copy / "SCOPE.json").write_bytes(canonical(body))
    with (
        Budget(tmp_path / "audit-fault", [copy], Limits(free_floor_bytes=1)) as budget,
        pytest.raises(BoundedError, match="PHYSICAL_PARTITION_SET"),
    ):
        audit_retained_scope(copy, budget)
