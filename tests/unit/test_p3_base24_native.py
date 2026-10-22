from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.expert_strategy import p3_base24_native as native

TZ = ZoneInfo("Asia/Shanghai")
CLOCK = datetime(2025, 1, 2, 15, tzinfo=TZ)
EARLY = datetime(2024, 1, 1, tzinfo=TZ)
SHA = "sha256:" + "a" * 64


def sha(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def write(p: Path, value: object) -> None:
    p.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def seal(root: Path) -> str:
    files = {
        p.relative_to(root).as_posix(): {"sha256": sha(p), "bytes": p.stat().st_size}
        for p in sorted((root / "native").glob("*.parquet"))
    }
    write(root / "native-closure.json", {"files": files})
    files["native-closure.json"] = {
        "sha256": sha(root / "native-closure.json"),
        "bytes": (root / "native-closure.json").stat().st_size,
    }
    write(
        root / "manifest.json",
        {
            "schema_version": native.SOURCE_SCHEMA,
            "consumer_admission": "NOT_GRANTED",
            "execution_authorized": False,
            "native_versions_retained_locally": True,
            "legacy_equivalence": False,
            "knowledge_cutoff": "2026-10-20T23:00:00+08:00",
            "files": files,
        },
    )
    (root / "READY").write_text(sha(root / "manifest.json") + "\n")
    return sha(root / "manifest.json")


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "native").mkdir(parents=True)
    timestamp = pa.timestamp("us", tz="Asia/Shanghai")
    extra = {
        "dwd_security": {"company_id": pa.string(), "instrument_type": pa.string()},
        "dwd_listing": {
            "security_id": pa.string(),
            "venue_id": pa.string(),
            "listing_code": pa.string(),
        },
        "dwd_trading_venue": {"mic_code": pa.string()},
        "dwd_trading_calendar": {
            "venue_id": pa.string(),
            "trade_date": pa.date32(),
            "is_open": pa.bool_(),
            "open_at": timestamp,
            "close_at": timestamp,
        },
        "dwd_security_bar": {
            "interval_size": pa.string(),
            "price_basis": pa.string(),
            **{
                k: pa.decimal128(30, 10)
                for k in ("open", "high", "low", "close", "volume", "turnover")
            },
        },
        "dwd_tradability_state": {
            "is_tradable": pa.bool_(),
            "can_buy": pa.bool_(),
            "can_sell": pa.bool_(),
        },
        "dwd_corporate_action_term": {"corporate_action_id": pa.string()},
    }
    for table, fields in native.FIELDS.items():
        for field in fields:
            extra.setdefault(table, {}).setdefault(field, pa.string())
    records = {
        "dwd_security": [dict(security_id="s", company_id="co", instrument_type="A_SHARE")],
        "dwd_listing": [
            dict(listing_id="l", security_id="s", venue_id="sz", listing_code="000001")
        ],
        "dwd_trading_venue": [
            dict(venue_id=v, mic_code=m) for v, m in [("sz", "XSHE"), ("sh", "XSHG")]
        ],
        "dwd_trading_calendar": [],
        "dwd_security_bar": [
            dict(
                listing_id="l",
                calendar_session_id="sz-2025-01-02",
                interval_size="1d",
                price_basis="UNADJUSTED",
                open=Decimal("1.0000000001"),
                high=Decimal("2"),
                low=Decimal("1"),
                close=Decimal("1.1000000001"),
                volume=Decimal("0"),
                turnover=None,
                knowledge_from=CLOCK,
            )
        ],
        "dwd_tradability_state": [
            dict(
                listing_id="l",
                calendar_session_id="sz-2025-01-02",
                is_tradable=True,
                can_buy=None,
                can_sell=False,
            )
        ],
    }
    day = date(2024, 9, 1)
    while day <= date(2026, 9, 18):
        if day.weekday() < 5:
            for venue in ("sz", "sh"):
                records["dwd_trading_calendar"].append(
                    dict(
                        calendar_session_id=f"{venue}-{day}",
                        venue_id=venue,
                        trade_date=day,
                        is_open=True,
                        open_at=datetime.combine(day, datetime.min.time(), TZ),
                        close_at=datetime.combine(day, datetime.min.time().replace(hour=15), TZ),
                    )
                )
        day += timedelta(days=1)
    for table, keys in native.KEYS.items():
        fields = {k: (timestamp if k == "effective_from" else pa.string()) for k in keys}
        fields.update(
            {
                k: timestamp
                for k in (
                    "knowledge_from",
                    "knowledge_to",
                    "effective_from",
                    "effective_to",
                    "observation_start",
                    "observation_end",
                    "effective_at",
                    "affected_effective_at",
                )
            }
        )
        fields.update(
            {
                k: pa.string()
                for k in (
                    "source_record_id",
                    "source_revision",
                    "source_artifact_sha256",
                )
            }
        )
        fields.update(extra.get(table, {}))
        defaults = dict(
            knowledge_from=EARLY,
            effective_from=EARLY,
            observation_start=CLOCK,
            observation_end=CLOCK,
            effective_at=CLOCK,
            affected_effective_at=CLOCK,
            source_record_id="synthetic",
            source_revision="v1",
            source_artifact_sha256=SHA,
        )
        rows = [{k: {**defaults, **row}.get(k) for k in fields} for row in records.get(table, [])]
        pq.write_table(
            pa.Table.from_pylist(rows, schema=pa.schema(fields)),
            root / "native" / f"{table}.parquet",
        )
    seal(root)
    return root


def mutate(source: Path, table: str, change) -> None:
    p = source / "native" / f"{table}.parquet"
    t = pq.read_table(p)
    rows = t.to_pylist()
    change(rows)
    pq.write_table(pa.Table.from_pylist(rows, schema=t.schema), p)
    seal(source)


def build(source: Path, output: Path, **kwargs):
    plan = make_plan(source, **kwargs)
    return native.prepare(
        source,
        output,
        source_manifest_sha256=sha(source / "manifest.json"),
        code_revision="b" * 40,
        run_plan=plan,
        **kwargs,
    )


def make_plan(source, **kwargs):
    return {
        "schema_version": "quanthus.p3-base24-input-run-plan.v1",
        "purpose": "input_qualification_only",
        "request_digest": native.REQUEST,
        "scope": native.SCOPE,
        "source_manifest_sha256": sha(source / "manifest.json"),
        "input_read_authorized": True,
        "strategy_execution_authorized": False,
        "contract_digest": native.digest_value(native.CONTRACT),
        "implementation_sha256": sha(Path(native.__file__)),
        "authority_reference": "synthetic-test-only",
        "new_input_scope_id": "P3-BASE24-INPUT-SYNTHETIC",
        "code_revision": "b" * 40,
        "max_output_bytes": kwargs.get("max_output_bytes", 4 * 1024**3),
        "wall_seconds": kwargs.get("wall_seconds", 3600),
    }


def test_materialize_asof_and_move_without_sources(source, tmp_path):
    result = build(source, tmp_path / "out")
    assert result["consumer_admission"] == "NOT_GRANTED"
    assert result["D_activated"] is False
    assert result["scope"]["decision_end"] == "2026-09-17"
    assert len(result["warmup_dates"]) == 60
    with native.open_prepared(tmp_path / "out", manifest_sha256=result["manifest_sha256"]) as view:
        rows = [r for batch in view.market_batches(CLOCK) for r in batch.to_pylist()]
        assert rows[0]["close"] == Decimal("1.1000000001")
        assert rows[0]["bar_knowledge_from"] == CLOCK
        assert rows[0]["can_buy"] is None
    for name in ("move1", "move2"):
        destination = tmp_path / name
        shutil.copytree(tmp_path / "out", destination)
        if source.exists():
            shutil.rmtree(source)
        assert (
            native.verify(destination, manifest_sha256=result["manifest_sha256"])[
                "semantic_summary"
            ]
            == result["semantic_summary"]
        )


def test_later_revision_never_replaces_earlier_decision(source, tmp_path):
    later = CLOCK + timedelta(days=1)

    def change(rows):
        new = {**rows[0], "close": Decimal("1.8"), "knowledge_from": later}
        rows[0]["knowledge_to"] = later
        rows.append(new)

    mutate(source, "dwd_security_bar", change)
    result = build(source, tmp_path / "out")
    with native.open_prepared(tmp_path / "out", manifest_sha256=result["manifest_sha256"]) as view:
        first = next(view.market_batches(CLOCK)).to_pylist()[0]
        second = next(view.market_batches(later)).to_pylist()[0]
        assert first["close"] == Decimal("1.1000000001")
        assert second["close"] == Decimal("1.8")


@pytest.mark.parametrize("table", ["dwd_security_bar", "dwd_tradability_state"])
def test_calendar_date_mismatch_rejected(source, tmp_path, table):
    mutate(source, table, lambda rows: rows[0].update(calendar_session_id="sz-2026-08-03"))
    with pytest.raises(native.InputError, match="CALENDAR_DATE"):
        build(source, tmp_path / "out")
    assert not (tmp_path / "out" / "READY").exists()


def test_unknown_clock_remains_unavailable(source, tmp_path):
    mutate(source, "dwd_security_bar", lambda rows: rows[0].update(knowledge_from=None))
    result = build(source, tmp_path / "out")
    assert result["semantic_summary"]["dwd_security_bar"]["unknown_knowledge"] == 1
    with native.open_prepared(tmp_path / "out", manifest_sha256=result["manifest_sha256"]) as view:
        assert list(view.market_batches(CLOCK)) == []


@pytest.mark.parametrize("kind", ["equal", "within", "contains", "symlink"])
def test_staging_source_overlap_is_rejected_before_writes(source, tmp_path, kind):
    output = tmp_path / "out"
    staging = tmp_path / ".out.job"
    if kind == "equal":
        source.rename(staging)
        source = staging
    elif kind == "within":
        output = source / "out"
    elif kind == "contains":
        staging.mkdir()
        source.rename(staging / "source")
        source = staging / "source"
    else:
        staging.symlink_to(source, target_is_directory=True)
    before = {p.relative_to(source).as_posix(): sha(p) for p in source.rglob("*") if p.is_file()}
    with pytest.raises(native.InputError, match="OVERLAP|SYMLINK"):
        build(source, output)
    after = {p.relative_to(source).as_posix(): sha(p) for p in source.rglob("*") if p.is_file()}
    assert before == after


def test_sealed_output_is_verified_without_rebuild_and_drift_fails(source, tmp_path):
    result = build(source, tmp_path / "out")
    assert build(source, tmp_path / "out")["manifest_sha256"] == result["manifest_sha256"]
    mutate(source, "dwd_security_bar", lambda rows: rows[0].update(close=Decimal("1.4")))
    with pytest.raises(native.InputError, match="IDENTITY"):
        build(source, tmp_path / "out")


def test_corruption_rejected_without_external_fallback(source, tmp_path):
    result = build(source, tmp_path / "out")
    shutil.rmtree(source)
    (tmp_path / "out" / "native" / "dwd_security_bar.parquet").write_bytes(b"damaged")
    with pytest.raises(native.InputError, match="MEMBER"):
        native.verify(tmp_path / "out", manifest_sha256=result["manifest_sha256"])


def reseal_prepared(root: Path) -> str:
    """Adversarial fixture: update every transport hash, not just broken bytes."""
    m = json.loads((root / "manifest.json").read_text())
    m["files"] = {
        name: {"sha256": sha(root / name), "bytes": (root / name).stat().st_size}
        for name in m["files"]
    }
    write(root / "manifest.json", m)
    (root / "READY").write_text(sha(root / "manifest.json") + "\n")
    return sha(root / "manifest.json")


@pytest.mark.parametrize("table", ["dwd_security_bar", "dwd_tradability_state"])
def test_offline_date_validation_survives_complete_reseal(source, tmp_path, table):
    build(source, tmp_path / "out")
    root = tmp_path / "out"
    p = root / "native" / (table + ".parquet")
    t = pq.read_table(p)
    rows = t.to_pylist()
    rows[0]["calendar_session_id"] = "sz-2026-08-03"
    pq.write_table(pa.Table.from_pylist(rows, schema=t.schema), p)
    write(
        root / "checkpoints" / (table + ".json"),
        {"member": {"bytes": p.stat().st_size, "sha256": sha(p)}, "rows": len(rows)},
    )
    digest = reseal_prepared(root)
    shutil.rmtree(source)
    with pytest.raises(native.InputError, match="CALENDAR_DATE"):
        native.verify(root, manifest_sha256=digest)


@pytest.mark.parametrize("table", ["dwd_security_bar", "dwd_tradability_state"])
def test_wrong_venue_is_rejected(source, tmp_path, table):
    mutate(source, table, lambda rows: rows[0].update(calendar_session_id="sh-2025-01-02"))
    with pytest.raises(native.InputError, match="CALENDAR_LISTING_VENUE"):
        build(source, tmp_path / "out")


def test_completed_checkpoint_is_reused_after_interruption(source, tmp_path, monkeypatch):
    original = native._zero

    def interrupted(c, sql, reason):
        if reason == "MATERIALIZATION_CHANGED_VALUES":
            raise RuntimeError("synthetic interruption after checkpoint")
        original(c, sql, reason)

    with monkeypatch.context() as patch:
        patch.setattr(native, "_zero", interrupted)
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            build(source, tmp_path / "out")
    staging = tmp_path / ".out.job"
    first = staging / "native" / "dwd_security.parquet"
    before = (first.stat().st_ino, sha(first))
    assert not (staging / "READY").exists()
    result = build(source, tmp_path / "out")
    after = tmp_path / "out" / "native" / first.name
    assert before == (after.stat().st_ino, sha(after))
    stages = json.loads((tmp_path / "out" / "stages.json").read_text())["stages"]
    assert stages[0]["disposition"] == "reuse"
    assert stages[1]["disposition"] == "build"
    native.verify(tmp_path / "out", manifest_sha256=result["manifest_sha256"])


def test_finalization_can_resume_without_rewriting_members(source, tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(native, "_publish", lambda *_: (_ for _ in ()).throw(RuntimeError("stop")))
        with pytest.raises(RuntimeError, match="stop"):
            build(source, tmp_path / "out")
    staging = tmp_path / ".out.job"
    frozen = {n: sha(staging / n) for n in native._tree(staging)}
    result = build(source, tmp_path / "out")
    assert frozen == {n: sha(tmp_path / "out" / n) for n in frozen}
    assert native.verify(tmp_path / "out", manifest_sha256=result["manifest_sha256"])


@pytest.mark.parametrize("tamper", ["source", "contract", "checkpoint", "admission", "scope"])
def test_resealed_offline_contract_drift_fails(source, tmp_path, tamper):
    root = tmp_path / "out"
    build(source, root)
    if tamper == "source":
        p = root / "source-metadata.json"
        value = json.loads(p.read_text())
        value["source_manifest"]["consumer_admission"] = "GRANTED"
    elif tamper == "contract":
        p = root / "contract.json"
        value = json.loads(p.read_text())
        value["scope"]["decision_end"] = "2026-09-30"
    elif tamper == "checkpoint":
        p = root / "checkpoints" / "dwd_security.json"
        value = json.loads(p.read_text())
        value["rows"] = 100
    else:
        p = root / "manifest.json"
        value = json.loads(p.read_text())
        if tamper == "admission":
            value["B_accepted"] = True
        else:
            value["scope"]["execution_end"] = "2026-09-30"
    write(p, value)
    digest = reseal_prepared(root)
    with pytest.raises(native.InputError, match="DIFFERS"):
        native.verify(root, manifest_sha256=digest)


def test_ambiguous_knowledge_interval_never_chooses_arbitrary_revision(source, tmp_path):
    mutate(
        source,
        "dwd_security_bar",
        lambda rows: rows.append(
            {**rows[0], "close": Decimal("1.7"), "knowledge_from": CLOCK + timedelta(hours=1)}
        ),
    )
    result = build(source, tmp_path / "out")
    with (
        native.open_prepared(tmp_path / "out", manifest_sha256=result["manifest_sha256"]) as v,
        pytest.raises(native.InputError, match="AMBIGUOUS_ASOF_VERSION"),
    ):
        list(v.market_batches(CLOCK + timedelta(hours=1)))


def test_late_identity_is_not_silently_dropped_or_filled(source, tmp_path):
    mutate(
        source,
        "dwd_security",
        lambda rows: rows[0].update(knowledge_from=CLOCK + timedelta(days=1)),
    )
    result = build(source, tmp_path / "out")
    with (
        native.open_prepared(tmp_path / "out", manifest_sha256=result["manifest_sha256"]) as v,
        pytest.raises(native.InputError, match="ASOF_IDENTITY_UNAVAILABLE"),
    ):
        list(v.market_batches(CLOCK))


def test_unknown_and_future_census_preserves_unavailability(source, tmp_path):
    mutate(source, "dwd_security_bar", lambda rows: rows[0].update(knowledge_from=None))
    result = build(source, tmp_path / "out")
    with native.open_prepared(tmp_path / "out", manifest_sha256=result["manifest_sha256"]) as v:
        c = v.input_census(CLOCK)["tables"]["dwd_security_bar"]
        assert c == {
            "retained_versions": 1,
            "unknown_knowledge": 1,
            "not_yet_known": 0,
            "visible_versions": 0,
        }


@pytest.mark.parametrize("cutoff", [datetime(2025, 1, 2), datetime(2026, 9, 18, tzinfo=TZ)])
def test_cutoff_cannot_expand_or_be_naive(source, tmp_path, cutoff):
    result = build(source, tmp_path / "out")
    with (
        native.open_prepared(tmp_path / "out", manifest_sha256=result["manifest_sha256"]) as v,
        pytest.raises(native.InputError, match="CUTOFF|WINDOW"),
    ):
        list(v.market_batches(cutoff))


def test_budget_failure_never_writes_ready(source, tmp_path):
    with pytest.raises(native.InputError, match="BUDGET"):
        build(source, tmp_path / "out", max_output_bytes=1024)
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / ".out.job" / "READY").exists()


def test_metadata_does_not_open_parquet(source, monkeypatch):
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path.suffix != ".parquet"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    assert (
        native.source_metadata(source, sha(source / "manifest.json"))["payload_verified"] is False
    )


def test_exact_locked_model_schemas_work_without_invented_fields(source, tmp_path):
    model = json.loads(
        (
            Path(__file__).resolve().parents[2] / "config/pipelines/dwd-domain-model.v1.json"
        ).read_text()
    )

    def arrow_type(value):
        value = value.removesuffix("?")
        if value.startswith("decimal_"):
            _, precision, scale = value.split("_")
            return pa.decimal128(int(precision), int(scale))
        return {
            "timestamp_us_utc": pa.timestamp("us", tz="UTC"),
            "string": pa.string(),
            "date32": pa.date32(),
            "int32": pa.int32(),
            "int64": pa.int64(),
            "bool": pa.bool_(),
        }[value]

    for table in model["tables"]:
        if table["name"] not in native.KEYS:
            continue
        columns = {
            **model["column_sets"]["lineage"],
            **model["time_profiles"][table["time_profile"]],
            **table["columns"],
        }
        schema = pa.schema({k: arrow_type(v) for k, v in columns.items()})
        p = source / "native" / (table["name"] + ".parquet")
        data = pq.read_table(p).to_pylist()
        pq.write_table(pa.Table.from_pylist(data, schema=schema), p)
    seal(source)
    result = build(source, tmp_path / "out")
    with native.open_prepared(tmp_path / "out", manifest_sha256=result["manifest_sha256"]) as v:
        row = next(v.market_batches(CLOCK)).to_pylist()[0]
        assert row["close"] == Decimal("1.1000000001")
        assert row["knowledge_from"] == CLOCK


def test_visibility_exceptions_remain_unavailable(source, tmp_path):
    table = "dwd_quality_exception"
    p = source / "native" / (table + ".parquet")
    schema = pq.read_schema(p)
    records = [
        dict(
            quality_exception_id=f"q{i}",
            affected_effective_at=CLOCK,
            knowledge_from=None,
            source_record_id=f"r{i}",
            source_revision="v1",
            source_artifact_sha256=SHA,
        )
        for i in range(4173)
    ]
    pq.write_table(pa.Table.from_pylist(records, schema=schema), p)
    seal(source)
    result = build(source, tmp_path / "out")
    assert result["semantic_summary"][table]["rows"] == 4173
    assert result["semantic_summary"][table]["unknown_knowledge"] == 4173
    with native.open_prepared(tmp_path / "out", manifest_sha256=result["manifest_sha256"]) as v:
        assert v.input_census(CLOCK)["tables"][table]["visible_versions"] == 0


def test_hash_deadline_is_checked_during_read(source):
    with pytest.raises(native.InputError, match="WALL_BUDGET"):
        native._sha(source / "native" / "dwd_security_bar.parquet", deadline=0)


def test_mutating_source_before_seal_fails(source, tmp_path, monkeypatch):
    original = native._write

    def mutate_after_checkpoint(path, value):
        original(path, value)
        if path.name == "dwd_security.json":
            (source / "native" / "dwd_security.parquet").touch()

    monkeypatch.setattr(native, "_write", mutate_after_checkpoint)
    with pytest.raises(native.InputError, match="SOURCE_CHANGED"):
        build(source, tmp_path / "out")
    assert not (tmp_path / "out" / "READY").exists()


def test_manifest_is_rejected_by_legacy_candidate_loader(source, tmp_path):
    from quanthus.expert_evaluation.serialization import ExpertEvaluationError
    from quanthus.expert_strategy.p3_strat_003 import P3Strat003CandidateInput

    build(source, tmp_path / "out")
    with pytest.raises(ExpertEvaluationError, match="manifest identity differs"):
        P3Strat003CandidateInput.load(
            tmp_path / "out" / "manifest.json",
            request_digest=native.REQUEST,
            decision_start=date(2025, 1, 1),
            decision_end=date(2026, 9, 17),
            execution_end=date(2026, 9, 18),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_read_authorized", False),
        ("strategy_execution_authorized", True),
        ("purpose", "strategy_run"),
        ("authority_reference", ""),
        ("new_input_scope_id", "P3-STRAT-003-B-spent-attempt"),
        ("implementation_sha256", SHA),
    ],
)
def test_unactivated_or_mismatched_plan_cannot_read_payload(
    source, tmp_path, monkeypatch, field, value
):
    plan = make_plan(source)
    plan[field] = value
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path.suffix != ".parquet"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    with pytest.raises(native.InputError, match="RUN_PLAN"):
        native.prepare(
            source,
            tmp_path / "out",
            source_manifest_sha256=sha(source / "manifest.json"),
            code_revision="b" * 40,
            run_plan=plan,
        )
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / ".out.job").exists()


def test_cli_prepares_and_reports_safe_census(source, tmp_path, capsys):
    plan = make_plan(source)
    p = tmp_path / "run-plan.json"
    write(p, plan)
    assert (
        native.main(
            [
                "prepare",
                "--root",
                str(source),
                "--output",
                str(tmp_path / "out"),
                "--run-plan",
                str(p),
                "--manifest-sha256",
                plan["source_manifest_sha256"],
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["B_accepted"] is False
    assert (
        native.main(
            [
                "census",
                "--root",
                str(tmp_path / "out"),
                "--manifest-sha256",
                result["manifest_sha256"],
                "--knowledge-cutoff",
                CLOCK.isoformat(),
            ]
        )
        == 0
    )
    text = capsys.readouterr().out
    assert "1.1000000001" not in text
    assert "close" not in json.loads(text)["tables"]["dwd_security_bar"]


def test_same_code_checkpoint_requires_same_identity(source, tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(native, "_publish", lambda *_: (_ for _ in ()).throw(RuntimeError("stop")))
        with pytest.raises(RuntimeError, match="stop"):
            build(source, tmp_path / "out")
    before = {n: sha(tmp_path / ".out.job" / n) for n in native._tree(tmp_path / ".out.job")}
    with pytest.raises(native.InputError, match="CHECKPOINT_IDENTITY"):
        build(source, tmp_path / "out", wall_seconds=60)
    assert before == {n: sha(tmp_path / ".out.job" / n) for n in before}


def test_unreadable_directory_cannot_pass_file_census(tmp_path, monkeypatch):
    def failed_walk(root, *, onerror, followlinks):
        onerror(PermissionError("synthetic directory scan denial"))
        return iter(())

    monkeypatch.setattr(native.os, "walk", failed_walk)
    with pytest.raises(native.InputError, match="DIRECTORY_SCAN_FAILED"):
        native._tree(tmp_path)


def test_asof_does_not_read_after_verified_file_changes(source, tmp_path):
    result = build(source, tmp_path / "out")
    with native.open_prepared(tmp_path / "out", manifest_sha256=result["manifest_sha256"]) as view:
        (tmp_path / "out/native/dwd_security.parquet").touch()
        with pytest.raises(native.InputError, match="CHANGED_DURING_READ"):
            list(view.market_batches(CLOCK))


def test_warmup_only_reads_exact_common_sessions_not_interval_holes(source, tmp_path):
    holiday = date(2024, 12, 2)

    def calendar_change(rows):
        for row in rows:
            if row["trade_date"] == holiday and row["venue_id"] == "sh":
                row["is_open"] = False

    mutate(source, "dwd_trading_calendar", calendar_change)

    def bar_change(rows):
        rows.append(
            {
                **rows[0],
                "calendar_session_id": "sz-2024-12-02",
                "observation_start": datetime(2024, 12, 2, 15, tzinfo=TZ),
                "observation_end": datetime(2024, 12, 2, 15, tzinfo=TZ),
            }
        )

    mutate(source, "dwd_security_bar", bar_change)
    result = build(source, tmp_path / "out")
    assert "2024-12-02" not in result["warmup_dates"]
    assert len(result["warmup_dates"]) == 60
    assert result["semantic_summary"]["dwd_security_bar"]["rows"] == 1


def test_resealed_wrong_plan_purpose_is_rejected_offline(source, tmp_path):
    build(source, tmp_path / "out")
    root = tmp_path / "out"
    plan = json.loads((root / "run-plan.json").read_text())
    plan["purpose"] = "production"
    write(root / "run-plan.json", plan)
    identity = json.loads((root / "identity.json").read_text())
    identity["run_plan_digest"] = native.digest_value(plan)
    write(root / "identity.json", identity)
    m = json.loads((root / "manifest.json").read_text())
    m["identity"] = identity
    write(root / "manifest.json", m)
    digest = reseal_prepared(root)
    with pytest.raises(native.InputError, match="INPUT_ONLY_RUN_PLAN"):
        native.verify(root, manifest_sha256=digest)


def test_published_contract_and_inactive_example_match_code():
    root = Path(__file__).resolve().parents[2] / "config/research/p3-strat-003"
    assert json.loads((root / "base24-native-consumption.v1.json").read_text()) == native.CONTRACT
    plan = json.loads((root / "base24-input-run-plan.example.v1.json").read_text())
    assert plan["contract_digest"] == native.digest_value(native.CONTRACT)
    assert plan["input_read_authorized"] is False
    assert plan["strategy_execution_authorized"] is False
    assert plan["scope"] == native.SCOPE


@pytest.mark.parametrize(
    "table,field,kind",
    [
        ("dwd_security_bar", "close", pa.float64()),
        ("dwd_tradability_state", "can_buy", pa.string()),
        ("dwd_trading_calendar", "trade_date", pa.string()),
    ],
)
def test_wrong_native_field_types_fail_before_materialization(source, tmp_path, table, field, kind):
    p = source / "native" / (table + ".parquet")
    t = pq.read_table(p)
    index = t.schema.get_field_index(field)
    t = t.set_column(index, field, t[field].cast(kind))
    pq.write_table(t, p)
    seal(source)
    with pytest.raises(native.InputError, match="NATIVE_CONSUMER_FIELD_TYPE"):
        build(source, tmp_path / "out")
    assert list((tmp_path / ".out.job/native").glob("*.parquet")) == []


@pytest.mark.parametrize(
    "table,field",
    [
        ("dwd_security_bar", "observation_start"),
        ("dwd_tradability_state", "observation_start"),
        ("dwd_trading_calendar", "trade_date"),
    ],
)
def test_unknown_business_date_is_not_silently_filtered(source, tmp_path, table, field):
    mutate(source, table, lambda rows: rows[0].update({field: None}))
    with pytest.raises(native.InputError, match="CALENDAR"):
        build(source, tmp_path / "out")
    assert not (tmp_path / "out/READY").exists()


def test_null_effective_key_cannot_disappear_from_interval_filter(source, tmp_path):
    mutate(
        source,
        "dwd_industry_scheme",
        lambda rows: rows.append(
            dict(
                industry_scheme_id="unknown-effective",
                effective_from=None,
                knowledge_from=EARLY,
                source_record_id="r",
                source_revision="v1",
                source_artifact_sha256=SHA,
            )
        ),
    )
    with pytest.raises(native.InputError, match="NATIVE_KEY_NULL"):
        build(source, tmp_path / "out")


def test_two_new_processes_without_source_cache_or_native_network(source, tmp_path):
    import ctypes.util

    if not ctypes.util.find_library("seccomp"):
        pytest.skip("Linux libseccomp unavailable; native network isolation not verified")
    result = build(source, tmp_path / "out")
    shutil.rmtree(source)
    script = """
import ctypes, ctypes.util, errno, json, socket, sys
from datetime import datetime
from pathlib import Path
from quanthus.expert_strategy.p3_base24_native import verify, NativeView
sec = ctypes.CDLL(ctypes.util.find_library("seccomp"), use_errno=True)
sec.seccomp_init.argtypes=[ctypes.c_uint32]; sec.seccomp_init.restype=ctypes.c_void_p
sec.seccomp_syscall_resolve_name.argtypes=[ctypes.c_char_p]
sec.seccomp_syscall_resolve_name.restype=ctypes.c_int
sec.seccomp_rule_add.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,ctypes.c_uint]
sec.seccomp_load.argtypes=[ctypes.c_void_p]; sec.seccomp_release.argtypes=[ctypes.c_void_p]
ctx=sec.seccomp_init(0x7fff0000)
assert ctx
for call in (b"socket",b"connect",b"socketpair"):
    syscall=sec.seccomp_syscall_resolve_name(call)
    assert sec.seccomp_rule_add(ctx,0x50000|errno.EACCES,syscall,0)==0
assert sec.seccomp_load(ctx)==0
sec.seccomp_release(ctx)
try: socket.socket(); raise AssertionError("native network was allowed")
except PermissionError: pass
m=verify(Path(sys.argv[1]),manifest_sha256=sys.argv[2])
with NativeView(Path(sys.argv[1])) as v:
    batches=list(v.market_batches(datetime.fromisoformat("2025-01-02T15:00:00+08:00")))
    assert sum(len(b) for b in batches)==1
print(json.dumps(m["semantic_summary"],sort_keys=True))
"""
    original = tmp_path / "out"
    summaries = []
    for i in (1, 2):
        copy = tmp_path / f"cold{i}"
        shutil.copytree(original, copy)
        shutil.rmtree(original)
        cwd = tmp_path / f"process{i}"
        cwd.mkdir()
        env = {
            "PATH": os.environ["PATH"],
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            "HOME": str(cwd),
            "TMPDIR": str(cwd),
            "XDG_CACHE_HOME": str(cwd),
            "PYTHONNOUSERSITE": "1",
        }
        p = subprocess.run(
            [sys.executable, "-c", script, str(copy), result["manifest_sha256"]],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert p.returncode == 0, p.stderr
        summaries.append(json.loads(p.stdout))
        original = copy
    assert summaries == [result["semantic_summary"]] * 2


@pytest.mark.parametrize("end", [CLOCK + timedelta(hours=1), None])
def test_state_observation_not_complete_at_cutoff_stays_unavailable(source, tmp_path, end):
    mutate(
        source,
        "dwd_tradability_state",
        lambda rows: rows[0].update(observation_end=end, can_buy=True),
    )
    result = build(source, tmp_path / "out")
    with native.open_prepared(tmp_path / "out", manifest_sha256=result["manifest_sha256"]) as v:
        row = next(v.market_batches(CLOCK)).to_pylist()[0]
        assert row["can_buy"] is None
        assert row["is_tradable"] is None
        assert row["state_knowledge_from"] is None
        if end is not None:
            row = next(v.market_batches(end)).to_pylist()[0]
            assert row["can_buy"] is True
            assert row["state_knowledge_from"] == EARLY


def test_asof_state_calendar_cannot_borrow_a_later_calendar_revision(source, tmp_path):
    earlier = CLOCK - timedelta(days=1)
    later = CLOCK + timedelta(days=1)

    def calendar(rows):
        original = next(r for r in rows if r["calendar_session_id"] == "sz-2025-01-02")
        revised = {**original, "trade_date": earlier.date(), "knowledge_from": later}
        original["knowledge_to"] = later
        rows.append(revised)

    mutate(source, "dwd_trading_calendar", calendar)
    mutate(
        source,
        "dwd_tradability_state",
        lambda rows: rows[0].update(
            observation_start=earlier, observation_end=earlier, can_buy=True
        ),
    )
    result = build(source, tmp_path / "out")
    with (
        native.open_prepared(tmp_path / "out", manifest_sha256=result["manifest_sha256"]) as v,
        pytest.raises(native.InputError, match="ASOF_STATE_CALENDAR_DATE"),
    ):
        list(v.market_batches(CLOCK))


def add_warmup_conflict(rows, table):
    if table == "dwd_trading_calendar":
        row = next(r for r in rows if r["calendar_session_id"] == "sz-2024-12-31")
        changed = {"is_open": False}
    else:
        row = next(r for r in rows if r["venue_id"] == "sz")
        changed = {"mic_code": "XBSE"}
    rows.append(
        {
            **row,
            **changed,
            "knowledge_from": datetime(2024, 12, 1, tzinfo=TZ),
            "knowledge_to": datetime(2025, 1, 1, 12, tzinfo=TZ),
        }
    )


@pytest.mark.parametrize("table", ["dwd_trading_calendar", "dwd_trading_venue"])
@pytest.mark.parametrize("mode", ["build", "cold"])
def test_warmup_rejects_conflicts_at_its_own_cutoff(source, tmp_path, table, mode):
    root = tmp_path / "out"
    if mode == "build":
        mutate(source, table, lambda rows: add_warmup_conflict(rows, table))
        with pytest.raises(native.InputError, match="AMBIGUOUS_WARMUP_VERSION"):
            build(source, root)
        assert not list((tmp_path / ".out.job/native").glob("*.parquet"))
    else:
        build(source, root)
        p = root / "native" / (table + ".parquet")
        data = pq.read_table(p)
        rows = data.to_pylist()
        add_warmup_conflict(rows, table)
        pq.write_table(pa.Table.from_pylist(rows, schema=data.schema), p)
        write(
            root / "checkpoints" / (table + ".json"),
            {"member": {"bytes": p.stat().st_size, "sha256": sha(p)}, "rows": len(rows)},
        )
        manifest = json.loads((root / "manifest.json").read_text())
        manifest["semantic_summary"][table]["rows"] = len(rows)
        write(root / "manifest.json", manifest)
        digest = reseal_prepared(root)
        shutil.rmtree(source)
        with pytest.raises(native.InputError, match="AMBIGUOUS_WARMUP_VERSION"):
            native.verify(root, manifest_sha256=digest)


@pytest.mark.parametrize("name", ["unexpected.txt", "READY"])
def test_resume_rejects_unexpected_files_before_touching_checkpoints(
    source, tmp_path, monkeypatch, name
):
    original = native._zero

    def stop_after_checkpoint(c, sql, reason):
        if reason == "MATERIALIZATION_CHANGED_VALUES":
            raise RuntimeError("synthetic interruption")
        original(c, sql, reason)

    with monkeypatch.context() as patch:
        patch.setattr(native, "_zero", stop_after_checkpoint)
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            build(source, tmp_path / "out")
    stage = tmp_path / ".out.job"
    (stage / name).write_text("unowned or stale receipt\n")
    before = {n: sha(stage / n) for n in native._tree(stage)}
    with pytest.raises(native.InputError, match="STAGING_FILE_SET_DIFFERS"):
        build(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert before == {n: sha(stage / n) for n in native._tree(stage)}


def test_failed_cutoff_change_cannot_leave_mixed_views(source, tmp_path):
    later = CLOCK + timedelta(hours=1)

    def securities(rows):
        revised = {**rows[0], "company_id": "later-company", "knowledge_from": later}
        rows[0]["knowledge_to"] = later
        rows.append(revised)

    mutate(source, "dwd_security", securities)
    mutate(
        source,
        "dwd_security_bar",
        lambda rows: rows.append({**rows[0], "close": Decimal("1.7"), "knowledge_from": later}),
    )
    result = build(source, tmp_path / "out")
    with native.open_prepared(tmp_path / "out", manifest_sha256=result["manifest_sha256"]) as v:
        v.at(CLOCK)
        with pytest.raises(native.InputError, match="AMBIGUOUS_ASOF_VERSION"):
            v.at(later)
        # A failed transition must preserve the whole previous snapshot, not only
        # the table that failed after other views have already advanced.
        assert v.connection.execute("SELECT company_id FROM dwd_security").fetchone() == ("co",)
        assert v.connection.execute("SELECT close FROM dwd_security_bar").fetchone() == (
            Decimal("1.1000000001"),
        )


@pytest.mark.parametrize("table", ["dwd_security_bar", "dwd_tradability_state"])
@pytest.mark.parametrize("mode", ["build", "cold"])
def test_reversed_observation_interval_cannot_be_consumed(source, tmp_path, table, mode):
    root = tmp_path / "out"
    if mode == "build":
        mutate(
            source, table, lambda rows: rows[0].update(observation_end=CLOCK - timedelta(hours=1))
        )
        with pytest.raises(native.InputError, match="OBSERVATION_INTERVAL_INVALID"):
            build(source, root)
        assert not (root / "READY").exists()
    else:
        build(source, root)
        p = root / "native" / (table + ".parquet")
        data = pq.read_table(p)
        rows = data.to_pylist()
        rows[0]["observation_end"] = CLOCK - timedelta(hours=1)
        pq.write_table(pa.Table.from_pylist(rows, schema=data.schema), p)
        write(
            root / "checkpoints" / (table + ".json"),
            {"member": {"bytes": p.stat().st_size, "sha256": sha(p)}, "rows": len(rows)},
        )
        digest = reseal_prepared(root)
        shutil.rmtree(source)
        with pytest.raises(native.InputError, match="OBSERVATION_INTERVAL_INVALID"):
            native.verify(root, manifest_sha256=digest)


def test_asof_state_venue_cannot_borrow_a_later_listing_revision(source, tmp_path):
    noon = CLOCK.replace(hour=12)
    later = CLOCK + timedelta(days=1)

    def listings(rows):
        original = dict(rows[0])
        rows[0]["effective_to"] = noon
        rows.extend(
            [
                {
                    **original,
                    "effective_from": noon,
                    "venue_id": "sh",
                    "knowledge_to": later,
                },
                {**original, "effective_from": noon, "knowledge_from": later},
            ]
        )

    mutate(source, "dwd_listing", listings)
    mutate(
        source,
        "dwd_security_bar",
        lambda rows: rows[0].update(observation_start=CLOCK.replace(hour=9)),
    )
    mutate(
        source,
        "dwd_tradability_state",
        lambda rows: rows[0].update(observation_start=CLOCK.replace(hour=14)),
    )
    result = build(source, tmp_path / "out")
    with (
        native.open_prepared(tmp_path / "out", manifest_sha256=result["manifest_sha256"]) as v,
        pytest.raises(native.InputError, match="ASOF_STATE_LISTING_VENUE"),
    ):
        list(v.market_batches(CLOCK))


def test_warmup_revision_boundary_uses_the_new_known_calendar(source, tmp_path):
    boundary = datetime(2025, 1, 1, tzinfo=TZ)

    def change(rows):
        original = next(r for r in rows if r["calendar_session_id"] == "sz-2024-12-31")
        revision = {**original, "knowledge_from": boundary, "is_open": False}
        original["knowledge_to"] = boundary
        rows.append(revision)

    mutate(source, "dwd_trading_calendar", change)
    result = build(source, tmp_path / "out")
    assert "2024-12-31" not in result["warmup_dates"]
    assert len(result["warmup_dates"]) == 60
    shutil.rmtree(source)
    native.verify(tmp_path / "out", manifest_sha256=result["manifest_sha256"])
