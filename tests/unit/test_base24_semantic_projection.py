from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.backtest import base24_semantic_projection as projection
from quanthus.pipelines.securities_warehouse import base24_input_view as reader
from quanthus.pipelines.securities_warehouse.base22 import file_sha, schemas
from quanthus.pipelines.securities_warehouse.base22_sources import _fingerprint

UTC = timezone.utc
OBS = datetime(2026, 9, 29, 16, tzinfo=UTC)  # Shanghai September 30.
KNOWN = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def view(tmp_path):
    models = Path("config/pipelines")
    model = json.loads((models / "dwd-domain-model.v1.json").read_bytes())
    ext = json.loads((models / "dwd-domain-model.v1.1-extension.json").read_bytes())
    schema = {
        name: pa.schema([pa.field(f.name, f.type, nullable=True) for f in value])
        for name, value in schemas(model, ext).items()
    }
    common = dict(
        record_source="synthetic",
        source_record_id="r",
        source_revision="v1",
        source_artifact_sha256="sha256:" + "a" * 64,
        knowledge_from=KNOWN,
        knowledge_to=None,
        effective_from=datetime(2000, 1, 1, tzinfo=UTC),
        effective_to=None,
        observation_start=OBS,
        observation_end=OBS,
        effective_at=OBS,
        announced_at=None,
    )
    records = {
        "dwd_security": [dict(security_id="s", company_id="company", instrument_type="A_SHARE")],
        "dwd_listing": [dict(listing_id="l", security_id="s", venue_id="v", listing_code="000001")],
        "dwd_trading_venue": [dict(venue_id="v", mic_code="XSHE")],
        "dwd_trading_calendar": [
            dict(calendar_session_id="c", venue_id="v", trade_date=date(2026, 9, 30), is_open=True)
        ],
        "dwd_tradability_state": [
            dict(
                listing_id="l",
                calendar_session_id="c",
                is_tradable=True,
                can_buy=True,
                can_sell=False,
            )
        ],
        "dwd_security_bar": [
            dict(
                listing_id="l",
                calendar_session_id="c",
                interval_size="1d",
                price_basis="UNADJUSTED",
                close=Decimal("1.0000000001"),
                volume=Decimal("0.0000000000"),
            )
        ],
        "dwd_corporate_action": [
            dict(corporate_action_id="a", security_id="s", company_id="company")
        ],
        "dwd_corporate_action_term": [
            dict(
                corporate_action_id="a",
                corporate_action_term_id="t",
                term_type="CASH_AMOUNT_PER_SHARE",
                amount_per_share=Decimal("0.1234567891"),
            )
        ],
    }
    source = tmp_path / "source"
    source.mkdir()
    files = {}
    for name in reader.TABLES:
        rows = [
            {k: {**common, **r}.get(k) for k in schema[name].names} for r in records.get(name, [])
        ]
        p = source / (name + ".parquet")
        pq.write_table(pa.Table.from_pylist(rows, schema=schema[name]), p)
        files[name] = (reader._File(p, _fingerprint(p)),)
    receipt = dict(
        scope=dict(business_start="2026-09-30", business_end="2026-09-30"),
        Daily=[],
        code_revision="a" * 40,
    )
    return reader.InputView(
        reader.ReadScope(date(2026, 9, 30), date(2026, 9, 30), datetime(2026, 10, 20, tzinfo=UTC)),
        "sha256:" + "b" * 64,
        json.dumps(receipt),
        MappingProxyType(files),
    )


def mutate(view, name, change):
    p = view._files[name][0].path
    t = pq.ParquetFile(p).read()
    rows = change(t.to_pylist())
    pq.write_table(pa.Table.from_pylist(rows, schema=t.schema), p)
    # Explicit new synthetic input qualification after a deliberate mutation.
    files = dict(view._files)
    files[name] = (reader._File(p, _fingerprint(p)),)
    from dataclasses import replace

    return replace(view, _files=MappingProxyType(files))


def test_all_seven_real_materializations_and_offline_reconstruction(view, tmp_path):
    frozen = {m.path: file_sha(m.path) for f in view._files.values() for m in f}
    root = tmp_path / "projection"
    result = projection.build(view, root)
    assert set(result["summary"]["rows"]) == set(projection.KEYS)
    assert all(n == 1 for n in result["summary"]["rows"].values())
    bar = pq.read_table(root / "tables/daily_ohlcv_none.parquet").to_pylist()[0]
    assert bar["dwd_close_decimal"] == Decimal("1.0000000001")
    assert bar["dwd_volume_decimal"] == 0
    assert bar["observation_start"] == OBS
    assert bar["dwd_bar_knowledge_from"] == KNOWN
    assert bar["date"] == date(2026, 9, 30)
    assert bar["can_buy"] is True and bar["can_sell"] is False
    action = pq.read_table(root / "tables/corporate_actions_normalized.parquet").to_pylist()[0]
    assert action["cash_dividend_per_share"] == Decimal("0.1234567891")
    assert action["authority_evidence_digest"] is None
    assert action["execution_authorized"] is False
    assert action["factor_cross_check_status"] == "unavailable"
    assert all(file_sha(p) == sha for p, sha in frozen.items())
    shutil.rmtree(view._files["dwd_listing"][0].path.parent)
    moved = tmp_path / "moved"
    shutil.copytree(root, moved)
    assert projection.verify(moved)["rows"] == result["summary"]["rows"]
    with pytest.raises(projection.ProjectionError, match="NEW_PROJECTION"):
        projection.build(view, root)


@pytest.mark.parametrize("table", ["dwd_security_bar", "dwd_tradability_state"])
@pytest.mark.parametrize("problem", ["date", "venue"])
def test_date_and_venue_closure_in_build(view, tmp_path, table, problem):
    from dataclasses import replace

    view = replace(
        view,
        scope=reader.ReadScope(date(2026, 7, 1), date(2026, 9, 30), view.scope.knowledge_cutoff),
    )
    view = mutate(
        view,
        "dwd_trading_calendar",
        lambda r: [
            r[0],
            {
                **r[0],
                "calendar_session_id": "bad",
                "trade_date": date(2026, 8, 3) if problem == "date" else date(2026, 9, 30),
                "venue_id": "other" if problem == "venue" else "v",
            },
        ],
    )
    view = mutate(view, table, lambda r: [{**r[0], "calendar_session_id": "bad"}])
    with pytest.raises(projection.ProjectionError, match="CALENDAR_"):
        projection.build(view, tmp_path / table)


def reseal(root):
    manifest = json.loads((root / "manifest.json").read_bytes())
    for name in manifest["files"]:
        p = root / name
        manifest["files"][name] = dict(sha256=file_sha(p), bytes=p.stat().st_size)
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "READY").write_text(file_sha(root / "manifest.json"))


@pytest.mark.parametrize("table", ["dwd_security_bar", "dwd_tradability_state"])
def test_repacked_cold_calendar_date_rejected_independently_of_hashes(view, tmp_path, table):
    root = tmp_path / "p"
    projection.build(view, root)
    p = root / "native" / (table + ".parquet")
    t = pq.read_table(p)
    rows = [{**r, "observation_start": datetime(2026, 7, 1, tzinfo=UTC)} for r in t.to_pylist()]
    pq.write_table(pa.Table.from_pylist(rows, schema=t.schema), p)
    pq.write_table(
        pa.Table.from_pylist(rows, schema=t.schema),
        root / "selected" / (table + "-2026-09-01.parquet"),
    )
    reseal(root)
    with pytest.raises(projection.ProjectionError, match="CALENDAR_DATE"):
        projection.verify(root)


def test_unknown_knowledge_does_not_gain_historical_visibility(view, tmp_path):
    view = mutate(view, "dwd_security_bar", lambda r: [{**r[0], "knowledge_from": None}])
    root = tmp_path / "p"
    m = projection.build(view, root)
    assert m["summary"]["bar_absent_states"] == 1
    assert m["excluded_visibility"]["dwd_security_bar"] == 1
    assert pq.read_table(root / "native/dwd_security_bar.parquet").num_rows == 1
    projection.verify(root)


def test_base_and_daily_nullability_metadata_unify_without_value_cast(view, tmp_path):
    from dataclasses import replace

    name = "dwd_trading_calendar"
    p = view._files[name][0].path
    t = pq.read_table(p)
    other = p.with_name("daily-calendar.parquet")
    pq.write_table(t, other)
    strict = pa.schema(
        [pa.field(f.name, f.type, nullable=f.name != "knowledge_from") for f in t.schema]
    )
    pq.write_table(t.cast(strict), p)
    files = dict(view._files)
    files[name] = tuple(reader._File(f, _fingerprint(f)) for f in [p, other])
    view = replace(view, _files=MappingProxyType(files))
    root = tmp_path / "p"
    projection.build(view, root)
    assert pq.read_schema(root / "native" / (name + ".parquet")).field("knowledge_from").nullable
    assert projection.verify(root)["rows"]["trading_calendar"] == 1


def test_equal_clock_conflicting_versions_fail(view, tmp_path):
    view = mutate(view, "dwd_security_bar", lambda r: [r[0], {**r[0], "close": Decimal("2")}])
    with pytest.raises(projection.ProjectionError, match="AMBIGUOUS_NATIVE_VERSION"):
        projection.build(view, tmp_path / "p")


def test_identical_repeats_and_later_known_version_are_explicit(view, tmp_path):
    view = mutate(
        view,
        "dwd_security_bar",
        lambda r: [
            r[0],
            r[0],
            {**r[0], "knowledge_from": datetime(2026, 10, 2, tzinfo=UTC), "close": Decimal("2")},
        ],
    )
    root = tmp_path / "p"
    projection.build(view, root)
    assert (
        pq.read_table(root / "tables/daily_ohlcv_none.parquet")["dwd_close_decimal"][0].as_py() == 2
    )
    assert pq.read_table(root / "native/dwd_security_bar.parquet").num_rows == 3


def test_month_bounded_build_and_cold_keep_global_keys(view, tmp_path):
    from dataclasses import replace

    prior = datetime(2026, 8, 30, 16, tzinfo=UTC)
    view = replace(
        view,
        scope=reader.ReadScope(date(2026, 8, 31), date(2026, 9, 30), view.scope.knowledge_cutoff),
    )
    receipt = view.receipt()
    receipt["scope"]["business_start"] = "2026-08-31"
    view = replace(view, receipt_json=json.dumps(receipt))
    view = mutate(
        view,
        "dwd_trading_calendar",
        lambda r: [
            r[0],
            {
                **r[0],
                "calendar_session_id": "august",
                "trade_date": date(2026, 8, 31),
                "observation_start": prior,
            },
        ],
    )
    for table in ["dwd_security_bar", "dwd_tradability_state"]:
        view = mutate(
            view,
            table,
            lambda r: [
                r[0],
                {
                    **r[0],
                    "calendar_session_id": "august",
                    "observation_start": prior,
                    "observation_end": prior,
                },
            ],
        )
    root = tmp_path / "p"
    projection.build(view, root)
    result = projection.verify(root)
    assert result["rows"]["daily_ohlcv_none"] == 2
    assert len(list((root / "selected").glob("*.parquet"))) == 4


def test_pinned_native_staging_reuse_and_changed_input_refusal(view, tmp_path):
    root = tmp_path / "original"
    projection.build(view, root)
    staging = tmp_path / "reused"
    staging.mkdir()
    shutil.copytree(root / "native", staging / "native")
    for name in ["input-view.json", "native-closure.json"]:
        shutil.copyfile(root / name, staging / name)
    pin = file_sha(staging / "native-closure.json")
    with pytest.raises(projection.ProjectionError, match="STAGED_NATIVE_IDENTITY"):
        projection.project_staged(staging, expected_staging_sha256="sha256:" + "0" * 64)
    assert not (staging / "tables").exists()
    projection.project_staged(staging, expected_staging_sha256=pin)
    assert projection.verify(staging)["rows"] == projection.verify(root)["rows"]
    with pytest.raises(projection.ProjectionError, match="STAGED_NATIVE_IDENTITY"):
        projection.project_staged(staging, expected_staging_sha256=pin)


@pytest.mark.parametrize("tamper", [False, True])
def test_retained_job_selection_is_verified_and_never_rebuilt(view, tmp_path, tamper):
    root = tmp_path / "p"
    manifest = projection.build(view, root)
    stage = tmp_path / "reuse"
    stage.mkdir()
    for name in ["native", "selected"]:
        shutil.copytree(root / name, stage / name)
    shutil.copyfile(root / "input-view.json", stage / "input-view.json")
    selected = next((stage / "selected").glob("dwd_security_bar-*.parquet"))
    if tamper:
        t = pq.read_table(selected)
        rows = [{**r, "close": Decimal("9")} for r in t.to_pylist()]
        pq.write_table(pa.Table.from_pylist(rows, schema=t.schema), selected)
    closure = json.loads((root / "native-closure.json").read_bytes())
    closure["reused_selected"] = {
        name: dict(sha256=file_sha(stage / name), bytes=(stage / name).stat().st_size)
        for name in manifest["files"]
        if name.startswith("selected/")
    }
    (stage / "native-closure.json").write_text(json.dumps(closure))
    pin = file_sha(stage / "native-closure.json")
    before = (selected.stat().st_ino, selected.stat().st_mtime_ns, file_sha(selected))
    if tamper:
        with pytest.raises(projection.ProjectionError, match="SELECTED_NATIVE_SEMANTICS"):
            projection.project_staged(stage, expected_staging_sha256=pin)
    else:
        projection.project_staged(stage, expected_staging_sha256=pin)
        assert projection.verify(stage)["rows"] == manifest["summary"]["rows"]
    assert before == (selected.stat().st_ino, selected.stat().st_mtime_ns, file_sha(selected))


@pytest.mark.parametrize("damage", ["bytes", "missing", "forged_semantics", "extra"])
def test_cold_rejects_corruption_or_fully_repacked_semantic_changes(view, tmp_path, damage):
    root = tmp_path / "p"
    projection.build(view, root)
    p = root / "tables/daily_ohlcv_none.parquet"
    if damage == "missing":
        p.unlink()
    elif damage == "bytes":
        with p.open("r+b") as s:
            s.write(b"BAD!")
    elif damage == "extra":
        (root / "unknown").write_text("x")
    else:
        t = pq.read_table(p)
        rows = t.to_pylist()
        rows[0]["dwd_close_decimal"] = Decimal("9")
        pq.write_table(pa.Table.from_pylist(rows, schema=t.schema), p)
        manifest = json.loads((root / "manifest.json").read_bytes())
        manifest["files"]["tables/daily_ohlcv_none.parquet"] = dict(
            sha256=file_sha(p), bytes=p.stat().st_size
        )
        (root / "manifest.json").write_text(json.dumps(manifest))
        (root / "READY").write_text(file_sha(root / "manifest.json"))
    with pytest.raises((projection.ProjectionError, FileNotFoundError)):
        projection.verify(root)


def test_successor_revision_binding_preserves_both_identities_and_rejects_gap():
    previous = dict(market_date="2026-10-09", core_manifest_sha256="sha256:" + "a" * 64)
    current = dict(
        market_date="2026-10-12",
        core_manifest_sha256="sha256:" + "b" * 64,
        selected_previous_core=previous["core_manifest_sha256"],
        original_prior=dict(market_date="2026-10-09", manifest_sha256="sha256:" + "c" * 64),
    )
    assert (
        projection.revision_transitions({"Daily": [previous, current]})[0]["old_chain_equivalent"]
        is False
    )
    current["original_prior"]["market_date"] = "2026-10-08"
    with pytest.raises(projection.ProjectionError, match="GAP_IS_NOT_REVISION"):
        projection.revision_transitions({"Daily": [previous, current]})


def test_monthly_market_join_owned_once_per_phase(view, tmp_path, monkeypatch):
    calls = []
    original = projection._market

    def tracked(c, start, end):
        calls.append((start, end))
        return original(c, start, end)

    monkeypatch.setattr(projection, "_market", tracked)
    root = tmp_path / "shared"
    result = projection.build(view, root)
    assert calls == [("2026-09-01", "2026-10-01")]
    assert all(result["summary"]["rows"][n] == 1 for n in projection.MARKET_DATES)
    projection.verify(root)
    assert calls == [("2026-09-01", "2026-10-01")] * 2
