from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base24_input_view as reader
from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base24_delivery import ONE_MINUTE_DISPOSITION, PURPOSE
from quanthus.pipelines.securities_warehouse.native_base24_reader import KIND
from tests.unit.test_native_base24_reader import reseal

UTC = timezone.utc


def stamp(day, hour=0):
    return datetime(2026, 9, day, hour, tzinfo=UTC)


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path / "base24"
    root.mkdir()
    schema = pa.schema(
        [
            ("knowledge_from", pa.timestamp("us", tz="UTC")),
            ("knowledge_to", pa.timestamp("us", tz="UTC")),
            ("observation_start", pa.timestamp("us", tz="UTC")),
            ("interval_size", pa.string()),
            ("price_basis", pa.string()),
            ("close", pa.decimal128(38, 10)),
        ]
    )
    rows = []
    for start, end, value in [
        (stamp(1), stamp(15), "1.0000000001"),
        (stamp(15), None, "2.0000000002"),
        (None, None, "3.0000000003"),
        (stamp(29), None, "4.0000000004"),
    ]:
        rows.append(
            {
                "knowledge_from": start,
                "knowledge_to": end,
                "observation_start": stamp(9, 16),
                "interval_size": "1d",
                "price_basis": "UNADJUSTED",
                "close": Decimal(value),
            }
        )
    layout = {}
    for table in reader.TABLES:
        partition = "interval_size=1d" if table == "dwd_security_bar" else "all"
        path = root / "payload" / table / partition / "part.parquet"
        path.parent.mkdir(parents=True)
        if table == "dwd_security_bar":
            data = pa.Table.from_pylist(rows, schema=schema)
        else:
            data = pa.table(
                {
                    "knowledge_from": pa.array([stamp(1)], pa.timestamp("us", tz="UTC")),
                    "knowledge_to": pa.array([None], pa.timestamp("us", tz="UTC")),
                    "effective_from": pa.array([stamp(1)], pa.timestamp("us", tz="UTC")),
                    "effective_to": pa.array([None], pa.timestamp("us", tz="UTC")),
                }
            )
        pq.write_table(data, path)
        layout[table] = {
            "files": [
                {
                    "table": table,
                    "partition": partition,
                    "path": path.relative_to(root).as_posix(),
                    "rows": len(data),
                    "size_bytes": path.stat().st_size,
                    "sha256": file_sha(path),
                }
            ],
            "expected_partition_rows": {partition: len(data)},
        }
    materialization = {"kind": KIND, "purpose": PURPOSE, "layout": layout}
    manifest = {
        "kind": KIND,
        "version": "2.4",
        "purpose": PURPOSE,
        "business_through": "2026-09-30",
        "business_timezone": "Asia/Shanghai",
        "default_intervals": ["1d", "5m"],
        "one_minute_disposition": ONE_MINUTE_DISPOSITION,
        "consumer_admission": "NOT_GRANTED",
        "summaries": {
            t: {p: {"rows": n} for p, n in e["expected_partition_rows"].items()}
            for t, e in layout.items()
        },
    }
    cfg = {
        "through_date": "2026-09-30",
        "layers": {"dwd": {"format": "native_base24", "package_id": "synthetic-24"}},
    }
    reseal(root, manifest, materialization, cfg)
    return {
        "base_root": root,
        "base_config": cfg,
        "scope": reader.ReadScope(date(2026, 9, 10), date(2026, 9, 10), stamp(15)),
        "code_revision": "a" * 40,
        "policy_digests": {"frozen": "sha256:" + "b" * 64},
    }


def values(view, **kwargs):
    return [
        row
        for batch in view.iter_batches("dwd_security_bar", **kwargs)
        for row in batch.to_pylist()
    ]


def test_real_native_reader_and_asof_half_open_versions_preserve_decimal(inputs):
    view = reader.open_input_view(**inputs)
    rows = values(view, columns=("close", "observation_start"), batch_size=1)
    assert rows == [{"close": Decimal("2.0000000002"), "observation_start": stamp(9, 16)}]
    assert len(values(view, all_versions_for_governance=True)) == 4
    inputs["scope"] = reader.ReadScope(date(2026, 9, 10), date(2026, 9, 10), stamp(14))
    assert values(reader.open_input_view(**inputs))[0]["close"] == Decimal("1.0000000001")


def test_business_date_is_shanghai_not_utc(inputs):
    inputs["scope"] = reader.ReadScope(date(2026, 9, 9), date(2026, 9, 9), stamp(15))
    assert values(reader.open_input_view(**inputs)) == []


def test_effective_exclusive_midnight_and_same_day_overlap(inputs):
    view = reader.open_input_view(**inputs)
    path = inputs["base_root"] / "payload/dwd_listing/all/part.parquet"
    t = pq.read_table(path)
    ends = [stamp(9, 16), stamp(9, 17), None]
    t = pa.Table.from_pylist(
        [{**t.to_pylist()[0], "effective_to": end} for end in ends], schema=t.schema
    )
    pq.write_table(t, path)
    from dataclasses import replace

    # A separately verified descriptor is required after changing source bytes.
    with pytest.raises(reader.InputViewError, match="MEMBER_CHANGED"):
        list(view.iter_batches("dwd_listing"))
    scope = inputs["scope"]
    mask = reader._business_mask(t.to_batches()[0], ("effective_from", "effective_to"), scope)
    assert mask.to_pylist() == [False, True, True]
    assert replace(scope, business_end=date(2026, 9, 11)) != scope


def test_source_changed_missing_or_extra_never_falls_back(inputs):
    view = reader.open_input_view(**inputs)
    path = inputs["base_root"] / "payload/dwd_security_bar/interval_size=1d/part.parquet"
    path.write_bytes(b"corrupt")
    with pytest.raises(reader.InputViewError, match="MEMBER_CHANGED"):
        values(view)
    with pytest.raises(ValueError, match="parent file differs"):
        reader.open_input_view(**inputs)


@pytest.mark.parametrize("change", ["cutoff", "business_scope", "policy", "code"])
def test_full_scope_and_policy_identity_changes(inputs, change):
    old = reader.open_input_view(**inputs)
    if change == "cutoff":
        inputs["scope"] = reader.ReadScope(date(2026, 9, 10), date(2026, 9, 10), stamp(16))
    elif change == "business_scope":
        inputs["scope"] = reader.ReadScope(date(2026, 9, 1), date(2026, 9, 10), stamp(15))
    elif change == "policy":
        inputs["policy_digests"] = {"frozen": "sha256:" + "c" * 64}
    else:
        inputs["code_revision"] = "d" * 40
    assert old.identity != reader.open_input_view(**inputs).identity
    receipt = old.receipt()
    receipt["execution_authorized"] = True
    assert old.receipt()["execution_authorized"] is False
    assert old.receipt()["legacy_seven_dataset_equivalence"] is False


@pytest.mark.parametrize("bound", [0, 65537, True])
def test_batch_bound_enforced(inputs, bound):
    with pytest.raises(reader.InputViewError, match="BATCH_BOUND"):
        values(reader.open_input_view(**inputs), batch_size=bound)


def test_unknown_table_and_bad_columns_fail_closed(inputs):
    view = reader.open_input_view(**inputs)
    with pytest.raises(reader.InputViewError, match="TABLE_NOT_BOUND"):
        list(view.iter_batches("daily_ohlcv_none"))
    with pytest.raises(reader.InputViewError, match="COLUMNS_INVALID"):
        values(view, columns=("old_price_alias",))


def test_naive_cutoff_and_unbound_tail_rejected_before_source_read(inputs):
    with pytest.raises(reader.InputViewError, match="AWARE_CUTOFF"):
        reader.ReadScope(date(2026, 9, 1), date(2026, 9, 2), datetime(2026, 10, 1))
    inputs["scope"] = reader.ReadScope(date(2026, 9, 1), date(2026, 10, 8), stamp(15))
    with pytest.raises(reader.InputViewError, match="TAIL_NOT_BOUND"):
        reader.open_input_view(**inputs)


def daily_fixture(tmp_path, inputs, monkeypatch, day, prior=None, core_sha=None):
    root = tmp_path / str(day)
    (root / "members/core").mkdir(parents=True)
    (root / "payload").mkdir()
    original = inputs["base_root"] / "payload/dwd_security_bar/interval_size=1d/part.parquet"
    (root / "payload/1d.parquet").write_bytes(original.read_bytes())
    manifest = {
        "base": inputs["base_config"]["layers"]["dwd"],
        "identity": {"core": core_sha or "sha256:" + str(day.day % 10) * 64},
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "members/core/manifest.json").write_text(json.dumps({"prior": prior}))
    selection = reader.DailySelection(root, day, file_sha(root / "manifest.json"))
    return selection


def test_daily_explicit_revision_preserved_no_chain_rebinding(inputs, tmp_path, monkeypatch):
    calls = []

    def verified(root):
        calls.append(root)
        return {
            "manifest_sha256": file_sha(root / "manifest.json"),
            "market_date": root.name,
            "table_paths": {"dwd_security_bar": ["payload/1d.parquet", "payload/5m.parquet"]},
        }

    monkeypatch.setattr(reader.daily_base24, "verify", verified)
    a = daily_fixture(tmp_path, inputs, monkeypatch, date(2026, 10, 8))
    original_prior = {"manifest_sha256": "sha256:" + "f" * 64, "market_date": "2026-10-08"}
    b = daily_fixture(tmp_path, inputs, monkeypatch, date(2026, 10, 9), original_prior)
    inputs.update(
        scope=reader.ReadScope(date(2026, 9, 1), date(2026, 10, 9), stamp(30)), daily=(a, b)
    )
    view = reader.open_input_view(**inputs)
    assert calls == [a.root, b.root]
    receipt = view.receipt()
    assert receipt["Daily"][1]["original_prior"] == original_prior
    assert receipt["Daily"][1]["prior_relation"] == "DIFFERENT_REVISION_OR_GAP"
    assert receipt["P6_chain_admission"] == "NOT_GRANTED"
    list(view.iter_batches("dwd_security_bar", all_versions_for_governance=True))
    assert calls == [a.root, b.root]  # No repeat qualification per query.
    from dataclasses import replace

    inputs["daily"] = (a, replace(b, manifest_sha256="sha256:" + "0" * 64))
    with pytest.raises(reader.InputViewError, match="PIN_OR_BASE_DIFFERS"):
        reader.open_input_view(**inputs)


def test_legacy_or_unsealed_daily_not_admitted_by_new_reader(inputs, tmp_path):
    day = date(2026, 10, 8)
    selection = daily_fixture(tmp_path, inputs, None, day)
    inputs.update(scope=reader.ReadScope(date(2026, 9, 1), day, stamp(30)), daily=(selection,))
    with pytest.raises(ValueError, match="SCOPE_OR_SEAL"):
        reader.open_input_view(**inputs)


def test_verified_daily_identity_references_are_bound_without_old_root_fallback(
    inputs, tmp_path, monkeypatch
):
    from dataclasses import replace

    day = date(2026, 10, 8)
    selection = daily_fixture(tmp_path, inputs, None, day)
    name = "members/core/reference/dwd_listing/part-00000.parquet"
    path = selection.root / name
    path.parent.mkdir(parents=True)
    original = inputs["base_root"] / "payload/dwd_listing/all/part.parquet"
    path.write_bytes(original.read_bytes())
    manifest = json.loads((selection.root / "manifest.json").read_bytes())
    manifest["files"] = [
        {"path": name, "sha256": file_sha(path), "size_bytes": path.stat().st_size}
    ]
    (selection.root / "manifest.json").write_text(json.dumps(manifest))
    selection = replace(selection, manifest_sha256=file_sha(selection.root / "manifest.json"))
    monkeypatch.setattr(
        reader.daily_base24,
        "verify",
        lambda root: {
            "manifest_sha256": file_sha(root / "manifest.json"),
            "market_date": str(day),
            "table_paths": {"dwd_security_bar": ["payload/1d.parquet"]},
        },
    )
    inputs.update(scope=reader.ReadScope(date(2026, 9, 1), day, stamp(30)), daily=(selection,))
    view = reader.open_input_view(**inputs)
    assert len(view._files["dwd_listing"]) == 2
    assert view._files["dwd_listing"][1].path == path
    assert view.receipt()["Daily"][0]["reference_members"][0]["sha256"] == file_sha(path)
    assert sum(b.num_rows for b in view.iter_batches("dwd_listing")) == 2
    path.unlink()
    with pytest.raises((reader.InputViewError, ValueError, FileNotFoundError)):
        list(view.iter_batches("dwd_listing"))
