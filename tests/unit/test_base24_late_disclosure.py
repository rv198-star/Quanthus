"""Late revisions remain independent of the frozen ordinary Daily date window."""

from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base24_late_disclosure import project_late_disclosures


def fixture():
    raw = dict(
        security_id="600001",
        stock_code="600001",
        ann_id="doc-1",
        title="announcement",
        publish_time="2026-10-12 23:59:59.123456",
        announce_date="2026-10-12",
        name="company",
        url="https://example.invalid/1.pdf",
        source="cninfo.hisAnnouncement",
        acquired_at="2026-10-13T12:00:00+08:00",
        body_sha256=None,
    )
    source = dict(
        rows=[raw],
        sha256="sha256:" + "a" * 64,
        revision="actual-events-v1",
        market_date="2026-10-13",
        covered_after="2026-10-12",
    )
    early = datetime(2000, 1, 1, tzinfo=timezone.utc)
    common = dict(effective_from=early, effective_to=None, knowledge_from=early, knowledge_to=None)
    kw = dict(
        known=[],
        listings=[dict(common, listing_code="600001", listing_id="l1", security_id="s1")],
        securities=[dict(common, security_id="s1", company_id="c1")],
        observed_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        through=date(2026, 10, 14),
    )
    return source, kw


def test_late_business_keeps_actual_capture_and_new_knowledge_without_mutation():
    src, kw = fixture()
    before = deepcopy((src, kw))
    rows, proof = project_late_disclosures([src], **kw)
    assert (src, kw) == before
    row = rows["dwd_disclosure"][0]
    assert row["effective_at"] == datetime(2026, 10, 12, 15, 59, 59, 123456, tzinfo=timezone.utc)
    assert row["knowledge_from"] == kw["observed_at"]
    assert row["source_revision"] == "actual-events-v1"
    assert (
        proof["selected"][0]["captures"][0]["original_acquired_at"] == src["rows"][0]["acquired_at"]
    )
    assert proof["census"]["new_documents"] == 1
    assert not proof["historical_PIT_admission"] and not proof["old_rows_overwritten"]
    assert not proof["original_body_content_qualified"]
    assert rows["dwd_disclosure_subject"][0]["security_id"] == "s1"


def test_rolling_duplicate_latest_real_capture_selected_once_and_prior_kept():
    src, kw = fixture()
    next_src = deepcopy(src)
    next_src.update(
        market_date="2026-10-14",
        covered_after="2026-10-13",
        sha256="sha256:" + "b" * 64,
        revision="actual-events-v2",
    )
    next_src["rows"][0]["acquired_at"] = "2026-10-14T12:00:00+08:00"
    rows, p = project_late_disclosures([next_src, src], **kw)
    assert len(rows["dwd_disclosure"]) == 1 and p["census"]["late_captures"] == 2
    assert rows["dwd_disclosure"][0]["source_artifact_sha256"] == next_src["sha256"]
    assert len(p["selected"][0]["captures"]) == 2
    kw["known"] = deepcopy(rows["dwd_disclosure"])
    assert project_late_disclosures([src, next_src], **kw)[0]["dwd_disclosure"] == []
    next_src["rows"][0]["title"] = "revised title"
    changed, proof = project_late_disclosures([src, next_src], **kw)
    assert changed["dwd_disclosure"][0]["title"] == "revised title"
    assert proof["census"]["revised_documents"] == 1
    assert kw["known"][0]["title"] == "announcement"


@pytest.mark.parametrize(
    "defect",
    [
        "same-clock",
        "code-conflict",
        "pre-listing",
        "ambiguous-listing",
        "security-closed",
        "prior-company",
        "prior-conflict",
    ],
)
def test_exact_identity_or_version_conflicts_hold_only_affected_document(defect):
    src, kw = fixture()
    if defect in ["prior-company", "prior-conflict"]:
        kw["known"] = project_late_disclosures([src], **kw)[0]["dwd_disclosure"]
        if defect == "prior-company":
            kw["known"][0]["company_id"] = "different"
        else:
            kw["known"].append(deepcopy(kw["known"][0]))
            kw["known"][1]["title"] = "contradictory"
    elif defect in ["same-clock", "code-conflict"]:
        src["rows"].append(deepcopy(src["rows"][0]))
        if defect == "same-clock":
            src["rows"][1]["title"] = "contradictory"
        else:
            src["rows"][1].update(stock_code="600002", security_id="600002")
    elif defect == "pre-listing":
        kw["listings"][0]["effective_from"] = datetime(2026, 10, 13, tzinfo=timezone.utc)
    elif defect == "ambiguous-listing":
        kw["listings"].append(deepcopy(kw["listings"][0]))
    else:
        kw["securities"][0]["knowledge_to"] = kw["observed_at"]
    before = deepcopy((src, kw))
    rows, p = project_late_disclosures([src], **kw)
    assert (src, kw) == before
    assert not any(rows.values()) and len(p["held"]) == 1
    assert p["held"][0]["disposition"] == "NO_NEW_OUTPUT"


@pytest.mark.parametrize(
    "defect",
    [
        "future-capture",
        "naive-capture",
        "publication-after-capture",
        "wrong-shanghai-day",
        "submicrosecond",
        "unknown-field",
        "unknown-source",
        "wrong-sha",
        "wrong-source-date",
        "wrong-through",
        "known-future",
    ],
)
def test_invalid_source_or_clock_rejected(defect):
    src, kw = fixture()
    raw = src["rows"][0]
    if defect == "future-capture":
        raw["acquired_at"] = "2026-10-21T00:00:00+08:00"
    elif defect == "naive-capture":
        raw["acquired_at"] = "2026-10-13T00:00:00"
    elif defect == "publication-after-capture":
        raw["acquired_at"] = "2026-10-11T00:00:00+08:00"
    elif defect == "wrong-shanghai-day":
        raw["announce_date"] = "2026-10-11"
    elif defect == "submicrosecond":
        raw["publish_time"] = "2026-10-12 23:59:59.1234567"
    elif defect == "unknown-field":
        raw["ignored"] = "no"
    elif defect == "unknown-source":
        raw["source"] = "unknown"
    elif defect == "wrong-sha":
        src["sha256"] = "wrong"
    elif defect == "wrong-source-date":
        src["covered_after"] = src["market_date"]
    elif defect == "wrong-through":
        kw["through"] = date(2026, 9, 30)
    else:
        kw["known"] = project_late_disclosures([src], **kw)[0]["dwd_disclosure"]
        kw["known"][0]["knowledge_from"] = datetime(2026, 10, 21, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError):
        project_late_disclosures([src], **kw)


def test_ordinary_window_is_left_to_daily_and_same_schema_is_retained():
    src, kw = fixture()
    src["rows"][0].update(announce_date="2026-10-13", publish_time="2026-10-13 00:00:00")
    rows, proof = project_late_disclosures([src], **kw)
    assert not any(rows.values())
    assert proof["census"]["ordinary_window_captures"] == 1
    assert proof["row_counts"] == {"dwd_disclosure": 0, "dwd_disclosure_subject": 0}


def test_input_bound_rejects_before_source_materialization():
    src, kw = fixture()
    with pytest.raises(BoundedError, match="INPUT_BOUND"):
        project_late_disclosures([src] * 32, **kw)


def package(tmp_path):
    from pathlib import Path

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse import base24_late_disclosure as module
    from quanthus.pipelines.securities_warehouse.base22 import (
        canonical,
        file_sha,
        load_contract,
        schemas,
    )
    from quanthus.pipelines.securities_warehouse.disclosure_event import disclosure_event_schemas

    src, kw = fixture()
    root = tmp_path / "projection"
    (root / "inputs").mkdir(parents=True)
    (root / "tables").mkdir()
    repo = Path(__file__).resolve().parents[2]
    _, model, extension = load_contract(repo / "config/pipelines")
    ss = schemas(model, extension)
    for row in kw["listings"] + kw["securities"]:
        row.update(
            record_source="synthetic",
            source_record_id="synthetic-1",
            source_revision="v1",
            source_artifact_sha256="sha256:" + "0" * 64,
        )
    kw["listings"][0].update(venue_id="SSE", board_code="MAIN", listing_currency="CNY")
    kw["securities"][0].update(instrument_type="COMMON_STOCK", instrument_currency="CNY")
    for label, table, values in [
        ("known", "dwd_disclosure", kw["known"]),
        ("listing", "dwd_listing", kw["listings"]),
        ("security", "dwd_security", kw["securities"]),
    ]:
        pq.write_table(
            pa.Table.from_pylist(values, schema=ss[table]), root / "inputs" / (label + ".parquet")
        )
    # The projector uses the physically serialized, schema-exact reference values.
    kw["listings"] = pq.read_table(root / "inputs/listing.parquet").to_pylist()
    kw["securities"] = pq.read_table(root / "inputs/security.parquet").to_pylist()
    pq.write_table(pa.Table.from_pylist(src["rows"]), root / "inputs/source-00.parquet")
    src["sha256"] = file_sha(root / "inputs/source-00.parquet")
    rows, proof = project_late_disclosures([src], **kw)
    for table, values in rows.items():
        pq.write_table(
            pa.Table.from_pylist(values, schema=disclosure_event_schemas()[table]),
            root / "tables" / (table + ".parquet"),
        )
    census = dict(
        status="QUALIFIED_EXACT_PRIOR_DOCUMENT_CENSUS_INPUT",
        known_member_sha256=file_sha(root / "inputs/known.parquet"),
        original_input_identities=[dict(manifest_sha256="sha256:" + "b" * 64)],
    )
    (root / "inputs/prior-census.safe.json").write_bytes(canonical(census) + b"\n")
    (root / "PROJECTION.json").write_bytes(canonical(proof) + b"\n")
    plan = dict(
        kind=module.KIND,
        contract_id=module.CONTRACT,
        code_revision="c" * 40,
        module_sha256=file_sha(Path(module.__file__)),
        consumer_admission="NOT_GRANTED",
        execution_authorized=False,
        complete_Daily_admission=False,
        historical_PIT_admission=False,
        through=kw["through"].isoformat(),
        observed_at=kw["observed_at"].isoformat(),
        sources=[
            dict({k: v for k, v in src.items() if k != "rows"}, member="inputs/source-00.parquet")
        ],
    )
    return root, plan


def reseal(root, plan):
    from quanthus.pipelines.securities_warehouse.base22 import canonical, file_sha

    plan["files"] = {
        p.relative_to(root).as_posix(): dict(sha256=file_sha(p), bytes=p.stat().st_size)
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name not in {"manifest.json", "READY"}
    }
    (root / "manifest.json").write_bytes(canonical(plan) + b"\n")
    sha = file_sha(root / "manifest.json")
    (root / "READY").write_text(sha + "\n")
    return sha


def test_offline_replay_exact_two_table_package(tmp_path):
    from quanthus.pipelines.securities_warehouse.base24_late_disclosure import verify_projection

    root, plan = package(tmp_path)
    sha = reseal(root, plan)
    result = verify_projection(root, manifest_sha256=sha)
    assert result["row_counts"] == {"dwd_disclosure": 1, "dwd_disclosure_subject": 1}
    assert not result["original_complete_Base_absence_independently_proven_by_this_subset"]


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "corrupt",
        "extra-empty-partition",
        "fully-repacked-row",
        "fully-repacked-purpose",
        "fully-repacked-census",
        "fully-repacked-source",
        "fully-repacked-schema",
    ],
)
def test_offline_fail_closed_including_fully_repacked_inputs(tmp_path, defect):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base24_late_disclosure import verify_projection

    root, plan = package(tmp_path)
    sha = reseal(root, plan)
    table = root / "tables/dwd_disclosure.parquet"
    if defect == "missing":
        table.unlink()
    elif defect == "corrupt":
        table.write_bytes(b"invalid bytes")
    elif defect == "extra-empty-partition":
        pq.write_table(pq.read_table(table).slice(0, 0), root / "tables/extra.parquet")
    elif defect == "fully-repacked-row":
        data = pq.read_table(table)
        rows = data.to_pylist()
        rows[0]["title"] = "forged"
        pq.write_table(pa.Table.from_pylist(rows, schema=data.schema), table)
    elif defect == "fully-repacked-purpose":
        plan["complete_Daily_admission"] = True
    elif defect == "fully-repacked-census":
        p = root / "inputs/prior-census.safe.json"
        d = json.loads(p.read_text())
        d["status"] = "unqualified"
        p.write_text(json.dumps(d))
    elif defect == "fully-repacked-source":
        plan["sources"][0]["sha256"] = "sha256:" + "d" * 64
    else:
        pq.write_table(pa.table({"unknown": []}), root / "inputs/listing.parquet")
    if defect.startswith("fully-repacked") or defect == "extra-empty-partition":
        sha = reseal(root, plan)
    with pytest.raises((RuntimeError, FileNotFoundError)):
        verify_projection(root, manifest_sha256=sha)
