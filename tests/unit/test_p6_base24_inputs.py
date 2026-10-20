"""Source handoff and consumer qualification: no worker or strategy execution."""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.backtest import base24_semantic_projection as projection
from quanthus.forward_validation.base24_inputs import open_base24_inputs
from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.pipelines.securities_warehouse import base24_p6_handoff as handoff
from quanthus.pipelines.securities_warehouse.base22 import file_sha, schemas
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError
from tests.unit.test_base24_semantic_projection import KNOWN, OBS, mutate
from tests.unit.test_base24_semantic_projection import (
    view as view,  # shared synthetic source fixture
)

UTC = timezone.utc


def status_file(tmp_path):
    model = json.loads(Path("config/pipelines/dwd-domain-model.v1.json").read_bytes())
    ext = json.loads(Path("config/pipelines/dwd-domain-model.v1.1-extension.json").read_bytes())
    schema = schemas(model, ext)["dwd_security_status_interval"]
    folder = tmp_path / "status-source"
    folder.mkdir()
    p = folder / "status.parquet"
    pq.write_table(pa.Table.from_pylist([], schema=schema), p)
    return p


def build(view, tmp_path):
    parent = tmp_path / "parent"
    projection.build(view, parent)
    status = status_file(tmp_path)
    root = tmp_path / "handoff"
    handoff.build(
        parent,
        root,
        expected_source_sha256=file_sha(parent / "manifest.json"),
        status_source=status,
        expected_status_sha256=file_sha(status),
        business_end="2026-09-30",
        code_revision="a" * 40,
    )
    return root


def reseal(root):
    m = json.loads((root / "manifest.json").read_bytes())
    for relative in m["files"]:
        p = root / relative
        record = dict(sha256=file_sha(p), bytes=p.stat().st_size)
        if p.suffix == ".parquet":
            q = pq.ParquetFile(p)
            record.update(
                rows=q.metadata.num_rows, schema=[[f.name, str(f.type)] for f in q.schema_arrow]
            )
        m["files"][relative] = record
    (root / "manifest.json").write_text(json.dumps(m))
    (root / "READY").write_text(file_sha(root / "manifest.json"))


def test_exact_binding_seven_tables_decimal_time_and_portable_reader(view, tmp_path):
    root = build(view, tmp_path)
    sha = file_sha(root / "manifest.json")
    original = open_base24_inputs(root, manifest_sha256=sha)
    assert not original.execution_authorized
    counts = {
        n: sum(b.num_rows for b in original.iter_batches(n, batch_size=1)) for n in projection.KEYS
    }
    assert all(n == 1 for n in counts.values())
    bar = next(original.iter_batches("daily_ohlcv_none")).to_pylist()[0]
    assert bar["dwd_close_decimal"] == Decimal("1.0000000001")
    assert bar["dwd_volume_decimal"] == 0
    assert bar["observation_start"] == OBS
    assert bar["dwd_bar_knowledge_from"] == KNOWN
    assert not original.assessment()["purposes"]["historical_replay"]
    assert original.assessment()["clocks"]["later"]["all_core_visible"] == 1
    for folder in ["parent", "source"]:
        shutil.rmtree(tmp_path / folder)
    shutil.rmtree(tmp_path / "status-source")
    moved = tmp_path / "moved"
    shutil.copytree(root, moved)
    shutil.rmtree(root)
    cold = open_base24_inputs(moved, manifest_sha256=sha)
    assert cold.identity == original.identity
    assert cold.assessment() == original.assessment()
    with pytest.raises(ForwardValidationContractError, match="purpose_not_qualified"):
        cold.historical_bundle()


@pytest.mark.parametrize("purpose", ["official", "paper_entry_source", "historical_replay", "typo"])
def test_no_implicit_purpose_or_execution_grant(view, tmp_path, purpose):
    root = build(view, tmp_path)
    consumer = open_base24_inputs(root, manifest_sha256=file_sha(root / "manifest.json"))
    with pytest.raises(ForwardValidationContractError, match="purpose_not_qualified"):
        list(consumer.iter_batches("daily_ohlcv_none", purpose=purpose))


@pytest.mark.parametrize("batch", [0, -1, 65537, True, 1.5])
def test_bounded_batch_contract(view, tmp_path, batch):
    root = build(view, tmp_path)
    consumer = open_base24_inputs(root, manifest_sha256=file_sha(root / "manifest.json"))
    with pytest.raises(ForwardValidationContractError, match="reader_scope"):
        list(consumer.iter_batches("daily_ohlcv_none", batch_size=batch))


@pytest.mark.parametrize("mode", ["missing", "corrupt", "extra", "symlink"])
def test_physical_corruption_no_fallback(view, tmp_path, mode):
    root = build(view, tmp_path)
    sha = file_sha(root / "manifest.json")
    p = root / "tables/daily_ohlcv_none.parquet"
    if mode == "missing":
        p.unlink()
    elif mode == "corrupt":
        p.write_bytes(b"corrupt")
    elif mode == "extra":
        (root / "extra").write_bytes(b"")
    else:
        saved = tmp_path / "external.parquet"
        p.rename(saved)
        p.symlink_to(saved)
    with pytest.raises((handoff.HandoffError, SourceAssessmentError)):
        open_base24_inputs(root, manifest_sha256=sha)


@pytest.mark.parametrize(
    "target",
    [
        "daily_ohlcv_none",
        "price_limit_execution_daily",
        "corporate_actions_normalized",
        "security_code_validity",
    ],
)
def test_repacked_semantic_changes_rejected(view, tmp_path, target):
    root = build(view, tmp_path)
    p = root / "tables" / (target + ".parquet")
    table = pq.read_table(p)
    rows = table.to_pylist()
    field, value = {
        "daily_ohlcv_none": ("dwd_close_decimal", Decimal("9.9999999999")),
        "price_limit_execution_daily": ("status_known", True),
        "corporate_actions_normalized": ("execution_authorized", True),
        "security_code_validity": ("exchange", "SSE"),
    }[target]
    rows[0][field] = value
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), p)
    reseal(root)
    with pytest.raises(handoff.HandoffError, match="SEMANTIC_VALUES"):
        handoff.verify(root, file_sha(root / "manifest.json"))


def test_repacked_schema_cast_rejected_even_when_values_equal(view, tmp_path):
    root = build(view, tmp_path)
    p = root / "tables/daily_ohlcv_none.parquet"
    t = pq.read_table(p)
    i = t.schema.get_field_index("dwd_close_decimal")
    t = t.set_column(i, "dwd_close_decimal", t.column(i).cast(pa.float64()))
    pq.write_table(t, p)
    reseal(root)
    with pytest.raises(handoff.HandoffError, match="SEMANTIC_SCHEMA"):
        handoff.verify(root, file_sha(root / "manifest.json"))


def test_all_original_versions_for_visibility_not_only_latest(view, tmp_path):
    early = datetime(2026, 9, 29, tzinfo=UTC)
    for name in (
        "dwd_security_bar",
        "dwd_tradability_state",
        "dwd_security",
        "dwd_listing",
        "dwd_trading_venue",
        "dwd_trading_calendar",
    ):
        view = mutate(
            view,
            name,
            lambda rows: (
                [{**r, "knowledge_from": early, "knowledge_to": KNOWN} for r in rows]
                + [{**r, "knowledge_from": KNOWN, "knowledge_to": None} for r in rows]
            ),
        )
    root = build(view, tmp_path)
    assessment = handoff.verify(root, file_sha(root / "manifest.json"))
    assert assessment["clocks"]["historical"]["all_core_visible"] == 1
    assert assessment["clocks"]["later"]["all_core_visible"] == 1
    assert not assessment["purposes"]["historical_replay"]


def test_unknown_clock_remains_unknown_not_inferred_from_snapshot(view, tmp_path):
    view = mutate(view, "dwd_security_bar", lambda r: [{**r[0], "knowledge_from": None}])
    root = build(view, tmp_path)
    a = handoff.verify(root, file_sha(root / "manifest.json"))
    assert a["clocks"]["historical"]["bar"] == a["clocks"]["later"]["bar"] == 1
    assert pq.read_table(root / "native/dwd_security_bar.parquet")["knowledge_from"].null_count == 1


@pytest.mark.parametrize("table", ["dwd_security_bar", "dwd_tradability_state"])
def test_cold_date_mismatch_after_full_repack(view, tmp_path, table):
    root = build(view, tmp_path)
    p = root / "native" / (table + ".parquet")
    t = pq.read_table(p)
    rows = [{**r, "observation_start": datetime(2026, 8, 3, tzinfo=UTC)} for r in t.to_pylist()]
    pq.write_table(pa.Table.from_pylist(rows, schema=t.schema), p)
    reseal(root)
    with pytest.raises(handoff.HandoffError, match="SEMANTIC_VALUES|CALENDAR_LISTING_DATE"):
        handoff.verify(root, file_sha(root / "manifest.json"))


def test_source_change_is_detected_before_output_creation(view, tmp_path):
    parent = tmp_path / "parent"
    projection.build(view, parent)
    status = status_file(tmp_path)
    (parent / "tables/daily_ohlcv_none.parquet").write_bytes(b"changed")
    output = tmp_path / "absent"
    with pytest.raises(handoff.HandoffError, match="SOURCE_MEMBER"):
        handoff.build(
            parent,
            output,
            expected_source_sha256=file_sha(parent / "manifest.json"),
            status_source=status,
            expected_status_sha256=file_sha(status),
            business_end="2026-09-30",
            code_revision="a" * 40,
        )
    assert not output.exists()


def test_mutation_after_open_and_detached_assessment(view, tmp_path):
    root = build(view, tmp_path)
    c = open_base24_inputs(root, manifest_sha256=file_sha(root / "manifest.json"))
    a = c.assessment()
    a["purposes"]["paper_entry_source"] = True
    assert not c.assessment()["purposes"]["paper_entry_source"]
    p = root / "tables/daily_ohlcv_none.parquet"
    p.write_bytes(p.read_bytes() + b"!")
    with pytest.raises(ForwardValidationContractError, match="member_changed"):
        list(c.iter_batches("daily_ohlcv_none"))


def test_later_context_and_historical_context_are_distinct_purposes(view, tmp_path):
    from dataclasses import replace

    from quanthus.pipelines.securities_warehouse.base24_input_view import ReadScope

    start = date(2026, 1, 1)
    known = datetime(2026, 9, 30, 16, tzinfo=UTC)
    for name in ("dwd_security_bar", "dwd_tradability_state", "dwd_trading_calendar"):

        def expand(rows, name=name):
            result = []
            for i in range(120):
                day = start + timedelta(days=i)
                r = {**rows[0], "calendar_session_id": f"c{i}", "knowledge_from": known}
                if name == "dwd_trading_calendar":
                    r["trade_date"] = day
                else:
                    instant = datetime(day.year, day.month, day.day, tzinfo=UTC)
                    r["observation_start"] = r["observation_end"] = instant
                result.append(r)
            return result

        view = mutate(view, name, expand)
    receipt = json.loads(view.receipt_json)
    receipt["scope"]["business_start"] = start.isoformat()
    view = replace(
        view,
        scope=ReadScope(start, date(2026, 9, 30), view.scope.knowledge_cutoff),
        receipt_json=json.dumps(receipt),
    )
    root = build(view, tmp_path)
    a = handoff.verify(root, file_sha(root / "manifest.json"))
    assert a["history_sessions"] == 120
    assert a["purposes"]["later_market_context_available"]
    assert not a["purposes"]["historical_market_context"]
    assert not a["purposes"]["paper_entry_source"]
    assert not a["knowledge_time_reinterpreted"]


def test_bse_is_physically_excluded_not_only_hidden_by_consumer(view, tmp_path):
    view = mutate(
        view, "dwd_trading_venue", lambda r: r + [{**r[0], "venue_id": "b", "mic_code": "BJSE"}]
    )
    view = mutate(
        view, "dwd_listing", lambda r: r + [{**r[0], "listing_id": "lb", "venue_id": "b"}]
    )
    view = mutate(
        view,
        "dwd_trading_calendar",
        lambda r: r + [{**r[0], "calendar_session_id": "cb", "venue_id": "b"}],
    )
    for name in ("dwd_security_bar", "dwd_tradability_state"):
        view = mutate(
            view, name, lambda r: r + [{**r[0], "listing_id": "lb", "calendar_session_id": "cb"}]
        )
    root = build(view, tmp_path)
    assert pq.read_table(root / "tables/daily_universe.parquet").num_rows == 1
    assert pq.read_table(root / "native/dwd_listing.parquet")["listing_id"].to_pylist() == ["l"]
    assert set(
        pq.read_table(root / "native/dwd_trading_venue.parquet")["mic_code"].to_pylist()
    ) == {"XSHE"}


def test_out_of_year_market_observations_physically_excluded(view, tmp_path):
    from dataclasses import replace

    from quanthus.pipelines.securities_warehouse.base24_input_view import ReadScope

    view = mutate(
        view,
        "dwd_trading_calendar",
        lambda r: r + [{**r[0], "calendar_session_id": "old", "trade_date": date(2025, 12, 31)}],
    )
    for name in ("dwd_security_bar", "dwd_tradability_state"):
        view = mutate(
            view,
            name,
            lambda r: (
                r
                + [
                    {
                        **r[0],
                        "calendar_session_id": "old",
                        "observation_start": datetime(2025, 12, 31, tzinfo=UTC),
                        "observation_end": datetime(2025, 12, 31, tzinfo=UTC),
                    }
                ]
            ),
        )
    view = replace(
        view, scope=ReadScope(date(2025, 12, 31), date(2026, 9, 30), view.scope.knowledge_cutoff)
    )
    root = build(view, tmp_path)
    assert pq.read_table(root / "native/dwd_security_bar.parquet").num_rows == 1
    assert pq.read_table(root / "native/dwd_tradability_state.parquet").num_rows == 1
    assert pq.read_table(root / "tables/daily_ohlcv_none.parquet")["date"].to_pylist() == [
        date(2026, 9, 30)
    ]


def test_contract_is_distinct_from_frozen_activation():
    from quanthus.forward_validation.activation import load_prospective_runtime_config

    runtime = load_prospective_runtime_config(
        Path("config/forward-validation/activation/p6-prospective-runtime.v1.json"), root=Path(".")
    )
    assert runtime is not None
    assert handoff.CONFIG["official_execution_authorized"] is False
    assert handoff.CONFIG["legacy_runtime_changed"] is False


def test_versioned_configuration_matches_sealed_contract():
    assert (
        json.loads(Path("config/forward-validation/data/p6-base24-inputs.v1.json").read_bytes())
        == handoff.CONFIG
    )


def test_offline_checks_do_not_write_to_the_immutable_handoff(view, tmp_path):
    from quanthus.pipelines.securities_warehouse.base22_sources import _fingerprint, _tree

    root = build(view, tmp_path)
    before = {k: _fingerprint(root / k) for k in _tree(root)}
    scratch = tmp_path / "private-check-scratch"
    scratch.mkdir()
    open_base24_inputs(root, manifest_sha256=file_sha(root / "manifest.json"), scratch_root=scratch)
    assert before == {k: _fingerprint(root / k) for k in _tree(root)}
    assert not list(scratch.iterdir())
    with pytest.raises(handoff.HandoffError, match="SCRATCH_INSIDE_HANDOFF"):
        handoff.verify(root, file_sha(root / "manifest.json"), scratch_root=root)
    assert before == {k: _fingerprint(root / k) for k in _tree(root)}
