from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pytest

from quanthus.pipelines.securities_warehouse import base22 as b

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
CONFIG = Path(__file__).resolve().parents[2] / "config/pipelines"
REPO = Path(__file__).resolve().parents[2]
SHA = "85f59c1d94378a9edc7aac7404058c8887c1d61e"
KNOWLEDGE = datetime(2026, 8, 3, 23, 59, tzinfo=timezone.utc)
EARLY = datetime(2011, 1, 1, tzinfo=timezone.utc)
JULY = datetime(2026, 7, 31, 1, tzinfo=timezone.utc)
AUGUST = datetime(2026, 8, 3, 1, tzinfo=timezone.utc)


def _save(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b.canonical(obj) + b"\n")


def _row(schema: object, coordinate: datetime = JULY) -> dict:
    row = {}
    for field in schema:
        if field.nullable:
            value = None
        elif pa.types.is_string(field.type):
            value = field.name.removesuffix("_id")
        elif pa.types.is_timestamp(field.type):
            value = coordinate
        elif pa.types.is_date32(field.type):
            value = coordinate.date()
        elif pa.types.is_decimal(field.type):
            value = Decimal("1").quantize(Decimal(1).scaleb(-field.type.scale))
        elif pa.types.is_boolean(field.type):
            value = True
        else:
            value = 1
        row[field.name] = value
    row.update(knowledge_from=KNOWLEDGE, knowledge_to=None)
    if "effective_from" in row:
        row["effective_from"] = EARLY
    if "interval_size" in row:
        row["interval_size"] = "1d"
    if "price_basis" in row:
        row["price_basis"] = "UNADJUSTED"
    if "observation_end" in row:
        row["observation_end"] = coordinate.replace(hour=7)
    if "calendar_session_id" in row:
        row["calendar_session_id"] = "calendar-" + coordinate.date().isoformat()
    return row


class Fixture:
    """No market inputs: every source, row, clock, revision and hash is synthetic."""

    def __init__(self, root: Path):
        self.root = root
        self.contract, self.model, self.extension = b.load_contract(CONFIG)
        self.schemas = b.schemas(self.model, self.extension)
        self.history = {name: [_row(schema)] for name, schema in self.schemas.items()}
        self.august = {
            name: [_row(self.schemas[name], AUGUST)]
            for name in (
                "dwd_trading_calendar",
                "dwd_security_bar",
                "dwd_security_valuation",
                "dwd_index_bar",
                "dwd_tradability_state",
                "dwd_disclosure",
                "dwd_disclosure_subject",
            )
        }
        for name in ("dwd_disclosure", "dwd_disclosure_subject"):
            self.history[name][0]["disclosure_id"] = "disclosure-july"
            self.august[name][0]["disclosure_id"] = "disclosure-august"
        # Nullable FK references default to null. Keep concrete required action/report identities.
        self.history["dwd_industry_node"][0]["industry_scheme_id"] = "industry_scheme"
        self.history["dwd_company_industry_membership"][0]["industry_node_id"] = "industry_node"
        self.history["dwd_security_bar"][0]["price_basis"] = "none"
        self.august["dwd_security_bar"][0]["price_basis"] = "none"
        # Preserve a high-precision financial value; never turn it into a float.
        self.history["dwd_security_bar"][0]["close"] = Decimal("1.0000000001")
        self.history["dwd_security_bar"][0]["high"] = Decimal("2.0000000000")
        self.repair = {
            "dwd_company_identifier": [copy.deepcopy(self.history["dwd_company_identifier"][0])]
        }
        self.repair["dwd_company_identifier"][0]["identifier_authority"] = "repaired-authority"
        self.output = root / "candidate"
        self.plan = {
            "schema_version": 1,
            "contract_id": b.CONTRACT_ID,
            "purpose": "synthetic_contract",
            "through_date": "2026-08-31",
            "code_revision": SHA,
            "sources": {},
            "tables": {},
            "quality_gates": {
                str(n): {"status": "synthetic_case", "reason": "fixture only"}
                for n in range(470, 475)
            },
            "august_open_dates": ["2026-08-03"],
        }
        self.refresh()

    def source(self, name: str, role: str, tables: dict, *, extra: dict | None = None) -> dict:
        root = self.root / "sources" / name
        root.mkdir(parents=True, exist_ok=True)
        raw = root / "raw/synthetic-evidence.json"
        _save(raw, {"fixture": name, "real_data": False})
        member = {
            **b._file_record(root, raw),
            "record_source": "synthetic/" + name,
            "source_revision": "fixture-v1",
        }
        files = []
        for table, rows in sorted(tables.items()):
            grouped = {}
            for row in rows:
                row.update(
                    record_source=member["record_source"],
                    source_revision=member["source_revision"],
                    source_artifact_sha256=member["sha256"],
                    source_record_id="row-" + table,
                )
                grouped.setdefault(b.partition(row, self.contract["tables"][table]), []).append(row)
            for part, records in grouped.items():
                path = root / "payload" / table / part / "part.parquet"
                path.parent.mkdir(parents=True, exist_ok=True)
                pq.write_table(pa.Table.from_pylist(records, schema=self.schemas[table]), path)
                files.append(
                    {
                        **b._file_record(root, path),
                        "table": table,
                        "partition": part,
                        "row_count": len(records),
                    }
                )
        members = [member]
        if role == "august":
            calendar = root / "evidence/calendar.json"
            _save(
                calendar,
                {
                    "kind": "base22_authoritative_calendar_census",
                    "through_date": "2026-08-31",
                    "open_dates": self.plan["august_open_dates"],
                },
            )
            members.append(
                {
                    **b._file_record(root, calendar),
                    "record_source": "synthetic/calendar-census",
                    "source_revision": "fixture-v1",
                }
            )
            self.plan["calendar_evidence"] = {
                "source_id": "august",
                "path": "evidence/calendar.json",
            }
            for table in sorted(self.schemas):
                mode = "project" if table in tables else "no_change"
                path = root / ("evidence/" + table + ".json")
                _save(
                    path,
                    {
                        "table": table,
                        "mode": mode,
                        "through_date": "2026-08-31",
                        "no_required_changes": mode == "no_change",
                        "census_digest": b.digest([]),
                    },
                )
                members.append(
                    {
                        **b._file_record(root, path),
                        "record_source": "synthetic/census",
                        "source_revision": "fixture-v1",
                    }
                )
        manifest = {
            "schema_version": 1,
            "kind": "base22_typed_projection_input",
            "purpose": "synthetic_contract",
            "role": role,
            "through_date": "2026-08-31" if role == "august" else "2026-07-31",
            "files": files,
            "raw_members": members,
            "basis_policy": "p10-daily-unadjusted-v1",
            "artifact_census": [{"artifact": "dividends_delta", "row_count": 0, "handler": None}],
            **(extra or {}),
        }
        _save(root / "manifest.json", manifest)
        (root / "READY").write_text(b.file_sha(root / "manifest.json") + "\n")
        self.plan["sources"][name] = {
            "root": str(root),
            "role": role,
            "authority": "synthetic-test-v1",
            "manifest_sha256": b.file_sha(root / "manifest.json"),
        }
        return manifest

    def refresh(self) -> None:
        history = self.source("history", "history", self.history)
        repair = self.source("repair", "repair", self.repair)
        table = "dwd_company_identifier"
        before = self.history[table][0]
        after = self.repair[table][0]
        key = [after[field] for field in self.contract["tables"][table]["version_key"]]
        self.plan["replacements"] = [
            {
                "key": [table, key],
                "source_id": "repair",
                "before": b.digest(before),
                "after": b.digest(after),
                "authority": "synthetic-repair-v1",
            }
        ]
        self.plan["tables"] = {
            name: {
                "reason": "healthy history, exact repair and bounded extension",
                "source_ids": ["history"]
                + (["repair"] if name in self.repair else [])
                + (["august"] if name in self.august else []),
                "required_partitions": {"placeholder": {}},
                "august_disposition": {
                    "mode": "project" if name in self.august else "no_change",
                    "evidence_source": "august",
                    "evidence_path": "evidence/" + name + ".json",
                },
            }
            for name in self.schemas
        }
        stats = dict.fromkeys(
            (
                "basis_normalized",
                "zero_values",
                "null_values",
                "late_knowledge_rows",
                "unavailable_rows",
                "identical_cross_source_rows",
            ),
            0,
        )
        with b._db(":memory:") as db:
            b._ingest(
                db,
                self.plan,
                {"history": history, "repair": repair},
                self.contract,
                self.schemas,
                {"history", "repair"},
                stats,
            )
            history_digest = b.digest({name: b._summary(db, name) for name in self.schemas})
            august = self.source(
                "august", "august", self.august, extra={"repaired_history_digest": history_digest}
            )
            b._ingest(
                db, self.plan, {"august": august}, self.contract, self.schemas, {"august"}, stats
            )
            for name in self.schemas:
                summary = b._summary(db, name)["partitions"]
                self.plan["tables"][name]["required_partitions"] = {
                    part: {"rows": obj["rows"], "key_digest": obj["key_digest"]}
                    for part, obj in summary.items()
                }

    def change_manifest(self, source: str, mutate) -> None:
        root = Path(self.plan["sources"][source]["root"])
        manifest = json.loads((root / "manifest.json").read_bytes())
        mutate(manifest)
        _save(root / "manifest.json", manifest)
        sha = b.file_sha(root / "manifest.json")
        (root / "READY").write_text(sha + "\n")
        self.plan["sources"][source]["manifest_sha256"] = sha

    def mutate_rows(self, source: str, table: str, mutate) -> None:
        root = Path(self.plan["sources"][source]["root"])
        manifest = json.loads((root / "manifest.json").read_bytes())
        item = next(file for file in manifest["files"] if file["table"] == table)
        path = root / item["path"]
        rows = pq.ParquetFile(path).read().to_pylist()
        mutate(rows)
        pq.write_table(pa.Table.from_pylist(rows, schema=self.schemas[table]), path)
        item.update(b._file_record(root, path), row_count=len(rows))
        self.change_manifest(source, lambda value: value.update(files=manifest["files"]))

    def build(self) -> dict:
        return b.build_base22(plan=self.plan, config_root=CONFIG, output=self.output)


@pytest.fixture
def fixture(tmp_path):
    return Fixture(tmp_path)


def test_complete_33_table_image_repairs_extends_and_preserves_times(fixture):
    result = fixture.build()
    assert len(result["tables"]) == 33
    assert result == b.verify_base22(fixture.output)
    manifest = json.loads((fixture.output / "manifest.json").read_bytes())
    files = [item for item in manifest["files"] if item.get("table") == "dwd_security_bar"]
    rows = [
        row
        for item in files
        for row in pq.ParquetFile(fixture.output / item["path"]).read().to_pylist()
    ]
    assert {row["price_basis"] for row in rows} == {"UNADJUSTED"}
    assert rows[0]["close"] == Decimal("1.0000000001")
    assert all(row["knowledge_from"] == KNOWLEDGE for row in rows)
    assert manifest["built_at"] > "2026-08-31"
    assert manifest["consumer_admission"] == "not_granted"
    assert not (fixture.output.parent / "CURRENT").exists()
    stages = json.loads((fixture.output / "evidence/stages.json").read_bytes())
    assert stages["statistics"]["basis_normalized"] == 2
    assert stages["statistics"]["late_knowledge_rows"] == 2
    assert (
        next(item for item in stages["stages"] if item["stage"] == "repaired_history")[
            "disposition"
        ]
        == "build"
    )
    assert all(item["elapsed_seconds"] >= 0 for item in stages["stages"])
    assert all((fixture.output / item["path"]).stat().st_nlink == 1 for item in manifest["files"])


def test_repeated_execution_never_rewrites_sealed_output_and_rejects_changed_inputs(fixture):
    first = fixture.build()
    stamp = (fixture.output / "manifest.json").stat().st_mtime_ns
    assert fixture.build() == first
    assert (fixture.output / "manifest.json").stat().st_mtime_ns == stamp
    fixture.plan["code_revision"] = "a" * 40
    with pytest.raises(b.Base22Error, match="SEALED_OUTPUT_INPUT_CHANGED"):
        fixture.build()


@pytest.mark.parametrize(
    "failure,code",
    [
        ("table", "PLAN_SCOPE_OR_TABLE_SET_MISMATCH"),
        ("partition", "PARTITION_COVERAGE_OR_KEY_CENSUS_MISMATCH"),
        ("count", "SOURCE_ROW_COUNT_MISMATCH"),
        ("duplicate", "DUPLICATE_KEY_IN_SOURCE"),
        ("future", "BUSINESS_DATE_AFTER_CUTOFF"),
        ("unknown_source", "UNKNOWN_OR_CONFLICTING_SOURCE"),
        ("foreign", "FOREIGN_KEY_OR_TEMPORAL_CLOSURE_FAILED"),
        ("conflict", "CONFLICT_REQUIRES_EXACT_REPAIR"),
        ("dividend", "NONEMPTY_ARTIFACT_WITHOUT_HANDLER"),
        ("history_binding", "AUGUST_REPAIRED_HISTORY_BINDING_MISMATCH"),
        ("no_change", "AUGUST_CHANGE_CENSUS_UNPROVEN"),
        ("calendar", "AUTHORITATIVE_CALENDAR_CENSUS_MISMATCH"),
        ("basis", "CANONICAL_PRICE_BASIS_UNKNOWN"),
        ("schema", "SCHEMA_MISMATCH"),
    ],
)
def test_rejects_incomplete_or_ambiguous_inputs(fixture, failure, code):
    if failure == "table":
        fixture.plan["tables"].pop("dwd_security")
    elif failure == "partition":
        fixture.plan["tables"]["dwd_security_bar"]["required_partitions"].pop(
            "interval_size=1d/observation_start_year=2026/observation_start_month=8"
        )
    elif failure == "count":
        fixture.change_manifest("history", lambda m: m["files"][0].update(row_count=1000))
    elif failure == "duplicate":
        fixture.mutate_rows("august", "dwd_security_bar", lambda r: r.append(dict(r[0])))
    elif failure == "future":
        fixture.mutate_rows(
            "august",
            "dwd_disclosure",
            lambda r: r[0].update(effective_at=datetime(2026, 8, 31, 16, tzinfo=timezone.utc)),
        )
    elif failure == "unknown_source":
        fixture.mutate_rows(
            "august", "dwd_disclosure", lambda r: r[0].update(source_revision="unknown")
        )
    elif failure == "foreign":
        fixture.mutate_rows(
            "august", "dwd_security_bar", lambda r: r[0].update(calendar_session_id="orphan")
        )
    elif failure == "conflict":
        fixture.plan["replacements"] = []
    elif failure == "dividend":
        fixture.change_manifest("august", lambda m: m["artifact_census"][0].update(row_count=7))
    elif failure == "history_binding":
        fixture.change_manifest(
            "august", lambda m: m.update(repaired_history_digest=b.digest("old-base"))
        )
    elif failure == "no_change":
        fixture.plan["tables"]["dwd_security"]["august_disposition"]["mode"] = "project"
    elif failure == "calendar":
        fixture.plan["august_open_dates"] = ["2026-08-03", "2026-08-04"]
    elif failure == "basis":
        fixture.mutate_rows(
            "august", "dwd_security_bar", lambda r: r[0].update(price_basis="mystery")
        )
    elif failure == "schema":
        root = Path(fixture.plan["sources"]["august"]["root"])
        manifest = json.loads((root / "manifest.json").read_bytes())
        item = next(file for file in manifest["files"] if file["table"] == "dwd_security_bar")
        path = root / item["path"]
        schema = fixture.schemas["dwd_security_bar"]
        schema = schema.set(schema.get_field_index("volume"), pa.field("volume", pa.int64()))
        rows = pq.ParquetFile(path).read().to_pylist()
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
        item.update(b._file_record(root, path))
        fixture.change_manifest("august", lambda m: m.update(files=manifest["files"]))
    with pytest.raises(b.Base22Error, match=code):
        fixture.build()
    assert not fixture.output.exists()


def _resign_image(root: Path, mutate) -> None:
    manifest = json.loads((root / "manifest.json").read_bytes())
    mutate(manifest)
    _save(root / "manifest.json", manifest)
    (root / "READY").write_text(b.file_sha(root / "manifest.json") + "\n")


@pytest.mark.parametrize(
    "failure",
    ["missing", "corrupt", "count", "semantic", "extra", "hardlink", "symlink", "wrong_table"],
)
def test_independent_verifier_fails_closed_even_with_forged_declarations(fixture, failure):
    fixture.build()
    root = fixture.root / "disposable-corruption-copy"
    shutil.copytree(fixture.output, root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    path = root / manifest["files"][0]["path"]
    if failure == "missing":
        path.unlink()
    elif failure == "corrupt":
        path.write_bytes(path.read_bytes()[:-1] + b"!")
    elif failure == "count":
        _resign_image(
            root, lambda m: next(f for f in m["files"] if "table" in f).update(row_count=999)
        )
    elif failure == "semantic":
        _resign_image(root, lambda m: m.update(semantic_digest=b.digest("forged")))
    elif failure == "extra":
        (root / "surprise").write_bytes(b"extra")
    elif failure == "hardlink":
        twin = fixture.root / "mutable-external-twin"
        os.link(path, twin)
    elif failure == "symlink":
        target = fixture.root / "external"
        shutil.copyfile(path, target)
        path.unlink()
        path.symlink_to(target)
    elif failure == "wrong_table":
        _resign_image(
            root, lambda m: next(f for f in m["files"] if "table" in f).update(table="dwd_fake")
        )
    with pytest.raises(b.Base22Error):
        b.verify_base22(root)
    assert b.verify_base22(fixture.output)


def test_twice_relocated_new_process_with_no_source_cache_network_or_environment(fixture):
    expected = fixture.build()
    first = fixture.root / "moved-once"
    shutil.copytree(fixture.output, first)
    final = fixture.root / "moved-twice"
    # Only disposable synthetic inputs are removed; production directories are never touched.
    shutil.rmtree(fixture.root / "sources")
    shutil.rmtree(fixture.output)
    shutil.rmtree(fixture.root / ".candidate.job")
    script = """
import sys, socket, json, os
from pathlib import Path
root = Path(sys.argv[1]).resolve()
blocked = [Path(name) for name in json.loads(sys.argv[2])]
def audit(event, args):
    if event.startswith('socket.'):
        raise AssertionError('source network forbidden')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        name = os.fsdecode(args[0])
        if any(Path(name).resolve().is_relative_to(old) for old in blocked):
            raise AssertionError('old input/cache read forbidden')
sys.addaudithook(audit)
from quanthus.pipelines.securities_warehouse.base22 import verify_base22
print(json.dumps(verify_base22(root), sort_keys=True))
"""

    def cold_scan(root, blocked):
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                script,
                str(root),
                json.dumps([str(fixture.root / name) for name in blocked]),
            ],
            env={"PATH": os.defpath},
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(result.stdout) == expected

    blocked = ["sources", ".candidate.job", "candidate"]
    cold_scan(first, blocked)
    shutil.copytree(first, final)
    shutil.rmtree(first)
    cold_scan(final, blocked + ["moved-once"])


def test_interruption_preserves_failure_reuses_one_history_stage_and_ready_is_last(fixture):
    original = b.verify_base22

    def fail_before_ready(root, *, require_ready=True):
        if not require_ready:
            assert not (root / "READY").exists()
            raise b.Base22Error("synthetic interruption before READY")
        return original(root, require_ready=require_ready)

    with (
        mock.patch.object(b, "verify_base22", side_effect=fail_before_ready),
        pytest.raises(b.Base22Error, match="synthetic interruption"),
    ):
        fixture.build()
    job = fixture.root / ".candidate.job"
    assert len(list(job.glob("attempt-*/FAILED.json"))) == 1
    assert not fixture.output.exists()
    assert not list(job.glob("attempt-*/image/READY"))
    stamp = (job / "history/rows.sqlite").stat().st_mtime_ns
    fixture.build()
    assert (job / "history/rows.sqlite").stat().st_mtime_ns == stamp
    stages = json.loads((fixture.output / "evidence/stages.json").read_bytes())
    assert (
        next(item for item in stages["stages"] if item["stage"] == "repaired_history")[
            "disposition"
        ]
        == "reuse"
    )
    assert len(list(job.glob("attempt-*/FAILED.json"))) == 1


def test_checkpoint_input_change_and_mid_build_input_mutation_are_rejected(fixture):
    with (
        mock.patch.object(b, "_materialize", side_effect=b.Base22Error("interrupt")),
        pytest.raises(b.Base22Error),
    ):
        fixture.build()
    fixture.plan["code_revision"] = "b" * 40
    with pytest.raises(b.Base22Error, match="INTERRUPTED_JOB_INPUT_CHANGED"):
        fixture.build()


def test_nontrading_day_events_and_rolling_duplicates_do_not_invent_sessions(fixture):
    for table in ("dwd_disclosure", "dwd_disclosure_subject"):
        fixture.august[table][0]["effective_at"] = datetime(2026, 8, 1, 1, tzinfo=timezone.utc)
    fixture.refresh()
    # A distinct rolling release can retain exactly the same source record evidence.
    original = copy.deepcopy(fixture.plan["sources"]["august"])
    fixture.plan["sources"]["rolling"] = dict(original)
    for entry in fixture.plan["tables"].values():
        if "august" in entry["source_ids"]:
            entry["source_ids"].append("rolling")
    result = fixture.build()
    assert result["tables"]["dwd_disclosure"]["rows"] == 2
    stats = json.loads((fixture.output / "evidence/stages.json").read_bytes())["statistics"]
    assert stats["identical_cross_source_rows"] == 7


def test_shanghai_boundary_and_unclipped_future_endpoint(fixture):
    fixture.history["dwd_listing"][0]["effective_to"] = datetime(2027, 1, 1, tzinfo=timezone.utc)
    fixture.refresh()
    fixture.build()
    assert b.business_date(datetime(2026, 8, 31, 15, 59, 59, tzinfo=timezone.utc)) == date(
        2026, 8, 31
    )
    assert b.business_date(datetime(2026, 8, 31, 16, tzinfo=timezone.utc)) == date(2026, 9, 1)


def test_real_data_purpose_is_not_authorized_by_synthetic_green(fixture):
    fixture.plan["purpose"] = "real_build"
    with pytest.raises(b.Base22Error, match="PLAN_SCOPE"):
        fixture.build()


def test_composite_planner_retains_july_source_cutoffs_and_freezes_a_private_plan(fixture):
    from quanthus.pipelines.securities_warehouse.planner import freeze_base22_composite_plan

    envelope = freeze_base22_composite_plan(plan=fixture.plan, config_root=CONFIG)
    assert envelope["source_manifests"]["history"]["through_date"] == "2026-07-31"
    assert envelope["source_manifests"]["august"]["through_date"] == "2026-08-31"
    assert envelope["plan_digest"] == b.digest(fixture.plan)
    fixture.plan["through_date"] = "2026-09-30"
    assert envelope["plan"]["through_date"] == "2026-08-31"


def test_unknown_knowledge_is_never_inferred(fixture):
    original = b._rows

    def unknown(path, schema):
        for row in original(path, schema):
            if "/august/payload/dwd_disclosure/" in str(path):
                row["knowledge_from"] = None
            yield row

    with (
        mock.patch.object(b, "_rows", side_effect=unknown),
        pytest.raises(b.Base22Error, match="REQUIRED_VALUE_OR_KNOWLEDGE_UNKNOWN"),
    ):
        fixture.build()


def test_input_change_during_seal_never_writes_ready(fixture):
    original = b._materialize

    def mutate_after_projection(*args):
        result = original(*args)
        raw = Path(fixture.plan["sources"]["august"]["root"]) / "raw/synthetic-evidence.json"
        raw.write_bytes(b"changed-after-projection")
        return result

    with (
        mock.patch.object(b, "_materialize", side_effect=mutate_after_projection),
        pytest.raises(b.Base22Error, match="FILE_HASH_OR_SIZE_MISMATCH"),
    ):
        fixture.build()
    assert not fixture.output.exists()
    assert not list((fixture.root / ".candidate.job").glob("attempt-*/image/READY"))


def test_precise_values_invalid_zero_and_null_remain_visible(fixture):
    fixture.history["dwd_security_bar"][0].update(open=Decimal(0), volume=None, turnover=Decimal(0))
    fixture.refresh()
    fixture.build()
    stats = json.loads((fixture.output / "evidence/stages.json").read_bytes())["statistics"]
    assert stats["zero_values"] == 2
    assert stats["null_values"] >= 1
    assert stats["unavailable_rows"] == 1


def test_new_knowledge_versions_are_retained_and_overlap_is_rejected(fixture):
    table = "dwd_financial_fact"
    old = fixture.history[table][0]
    old["knowledge_to"] = datetime(2026, 9, 1, tzinfo=timezone.utc)
    revised = dict(old, knowledge_from=datetime(2026, 9, 1, tzinfo=timezone.utc), knowledge_to=None)
    fixture.history[table].append(revised)
    fixture.refresh()
    assert fixture.build()["tables"][table]["rows"] == 2


def test_overlapping_knowledge_versions_do_not_select_a_latest_row(fixture):
    table = "dwd_financial_fact"
    fixture.history[table].append(
        dict(fixture.history[table][0], knowledge_from=datetime(2026, 9, 1, tzinfo=timezone.utc))
    )
    fixture.refresh()
    with pytest.raises(b.Base22Error, match="OVERLAPPING_KNOWLEDGE_VERSIONS"):
        fixture.build()


def test_frozen_contract_and_model_cannot_be_relabeled(fixture):
    target = fixture.root / "contracts"
    shutil.copytree(CONFIG, target)
    contract = target / "base22-independent-image.v1.json"
    obj = json.loads(contract.read_bytes())
    obj["through_date"] = "2026-09-30"
    _save(contract, obj)
    with pytest.raises(b.Base22Error, match="FROZEN_BASE22_CONTRACT_CHANGED"):
        b.load_contract(target)


@pytest.mark.parametrize("issue", ["470", "471", "472", "473", "474"])
def test_unqualified_real_repair_gate_never_inherits_synthetic_authority(fixture, issue):
    fixture.plan["quality_gates"][issue]["status"] = "unqualified"
    with pytest.raises(b.Base22Error, match="REAL_QUALITY_GATE_NOT_AUTHORIZED"):
        fixture.build()


def test_runtime_change_is_a_miss_for_the_same_job_identity(fixture):
    fixture.build()
    changed = dict(b.runtime_info(), pyarrow="different-runtime")
    with (
        mock.patch.object(b, "runtime_info", return_value=changed),
        pytest.raises(b.Base22Error, match="SEALED_OUTPUT_INPUT_CHANGED"),
    ):
        fixture.build()


def _path_snapshot(root: Path) -> dict:
    """Paths, file bytes and link destinations, including disposable staging targets."""
    return {
        path.relative_to(root).as_posix(): (
            ("symlink", os.readlink(path))
            if path.is_symlink()
            else ("directory", None)
            if path.is_dir()
            else ("file", path.read_bytes())
        )
        for path in root.rglob("*")
    }


@pytest.mark.parametrize(
    "source_id,layout",
    [
        (name, layout)
        for name in ("history", "repair", "august")
        for layout in ("equal", "contains_source", "inside_source")
    ]
    + [("history", "symlink")],
)
def test_staging_path_rejected_before_any_input_or_job_write(fixture, source_id, layout):
    source = Path(fixture.plan["sources"][source_id]["root"])
    job = fixture.output.with_name("." + fixture.output.name + ".job")
    if layout in {"equal", "contains_source"}:
        destination = job if layout == "equal" else job / "history-source"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(source, destination)
        fixture.plan["sources"][source_id]["root"] = str(destination)
    elif layout == "inside_source":
        fixture.output = source / "candidate"
    else:
        target = fixture.root / "disposable-staging-target"
        target.mkdir()
        (target / "sentinel").write_bytes(b"must remain unchanged")
        job.symlink_to(target, target_is_directory=True)
    before = _path_snapshot(fixture.root)
    try:
        with pytest.raises(b.Base22Error, match="(OUTPUT|STAGING)_"):
            fixture.build()
    finally:
        assert _path_snapshot(fixture.root) == before


def _repack_synthetic_image(root: Path) -> None:
    """Recompute all byte identities AND row/key summaries without validating semantics.

    Only disposable test copies use this helper. A fully consistent envelope must
    never hide the semantic defect that a cold verifier is expected to reject.
    """
    manifest = json.loads((root / "manifest.json").read_bytes())
    plan = json.loads((root / "evidence/input-plan.json").read_bytes())
    contract, model, extension = b.load_contract(root / "contract")
    physical_schemas = b.schemas(model, extension)
    with b._db(":memory:") as db:
        for item in manifest["files"]:
            path = root / item["path"]
            item.update(b._file_record(root, path))
            if "table" not in item:
                continue
            name = item["table"]
            rows = list(b._rows(path, physical_schemas[name]))
            item["row_count"] = len(rows)
            for row in rows:
                key = b.canonical([row[f] for f in contract["tables"][name]["version_key"]])
                db.execute(
                    "INSERT INTO rows VALUES (?,?,?,?,?)",
                    (
                        name,
                        key.decode(),
                        b.partition(row, contract["tables"][name]),
                        b.canonical(row).decode(),
                        "synthetic-repack",
                    ),
                )
        manifest["tables"] = {name: b._summary(db, name) for name in physical_schemas}
    manifest["semantic_digest"] = b.digest(manifest["tables"])
    manifest["input_plan_digest"] = b.digest(plan)
    _save(root / "manifest.json", manifest)
    (root / "READY").write_text(b.file_sha(root / "manifest.json") + "\n")


def _add_other_venue_session(fixture) -> str:
    venue = copy.deepcopy(fixture.history["dwd_trading_venue"][0])
    venue["venue_id"] = "other-venue"
    fixture.history["dwd_trading_venue"].append(venue)
    session = copy.deepcopy(fixture.history["dwd_trading_calendar"][0])
    session.update(venue_id="other-venue", calendar_session_id="other-venue-july")
    fixture.history["dwd_trading_calendar"].append(session)
    return session["calendar_session_id"]


@pytest.mark.parametrize("case", ["wrong_date", "wrong_venue", "correct"])
@pytest.mark.parametrize("mode", ["build", "cold"])
def test_daily_bar_calendar_date_and_listing_venue_closure(fixture, case, mode):
    other = _add_other_venue_session(fixture)
    fixture.refresh()
    session = {
        "wrong_date": "calendar-2026-08-03",
        "wrong_venue": other,
        "correct": "calendar-2026-07-31",
    }[case]
    if mode == "build":
        fixture.history["dwd_security_bar"][0]["calendar_session_id"] = session
        fixture.refresh()
        run = fixture.build
    else:
        fixture.build()
        root = fixture.root / "calendar-repacked"
        shutil.copytree(fixture.output, root)
        manifest = json.loads((root / "manifest.json").read_bytes())
        item = next(
            f
            for f in manifest["files"]
            if f.get("table") == "dwd_security_bar"
            and "observation_start_month=7" in f["partition"]
        )
        path = root / item["path"]
        rows = list(b._rows(path, fixture.schemas["dwd_security_bar"]))
        rows[0]["calendar_session_id"] = session
        pq.write_table(pa.Table.from_pylist(rows, schema=fixture.schemas["dwd_security_bar"]), path)
        _repack_synthetic_image(root)

        def run():
            return b.verify_base22(root)

    if case == "correct":
        assert run()["through_date"] == "2026-08-31"
    else:
        with pytest.raises(b.Base22Error, match="CALENDAR_DATE_OR_VENUE_CLOSURE_FAILED"):
            run()


@pytest.mark.parametrize(
    "case,reason",
    [
        ("empty_sources", "EXACT_TABLE_SOURCES_REQUIRED"),
        ("unknown_source", "UNKNOWN_SOURCE_ID"),
        ("unqualified_gate", "REAL_QUALITY_GATE_NOT_AUTHORIZED"),
        ("wrong_purpose", "PLAN_SCOPE_OR_TABLE_SET_MISMATCH"),
        ("other_table_source", "UNKNOWN_OR_CONFLICTING_SOURCE"),
    ],
)
def test_cold_revalidates_offline_plan_and_per_table_sources(fixture, case, reason):
    fixture.build()
    root = fixture.root / "plan-repacked"
    shutil.copytree(fixture.output, root)
    path = root / "evidence/input-plan.json"
    plan = json.loads(path.read_bytes())
    if case == "empty_sources":
        plan["tables"]["dwd_security"]["source_ids"] = []
    elif case == "unknown_source":
        plan["tables"]["dwd_security"]["source_ids"] = ["unknown"]
    elif case == "unqualified_gate":
        plan["quality_gates"]["471"]["status"] = "unqualified"
    elif case == "wrong_purpose":
        plan["purpose"] = "real_build"
    else:
        # A globally known raw member is not automatically admitted to this table.
        plan["tables"]["dwd_security"]["source_ids"] = ["repair"]
    _save(path, plan)
    _repack_synthetic_image(root)
    shutil.rmtree(fixture.root / "sources")
    with (
        mock.patch.object(b, "_verify_source", side_effect=AssertionError("external source read")),
        pytest.raises(b.Base22Error, match=reason),
    ):
        b.verify_base22(root)


def test_cold_rejects_row_lineage_admitted_only_to_another_table(fixture):
    fixture.build()
    root = fixture.root / "lineage-repacked"
    shutil.copytree(fixture.output, root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    item = next(f for f in manifest["files"] if f.get("table") == "dwd_security")
    member = manifest["source_manifests"]["repair"]["raw_members"][0]
    rows = list(b._rows(root / item["path"], fixture.schemas["dwd_security"]))
    rows[0].update(
        record_source=member["record_source"],
        source_revision=member["source_revision"],
        source_artifact_sha256=member["sha256"],
    )
    pq.write_table(
        pa.Table.from_pylist(rows, schema=fixture.schemas["dwd_security"]), root / item["path"]
    )
    _repack_synthetic_image(root)
    shutil.rmtree(fixture.root / "sources")
    with pytest.raises(b.Base22Error, match="UNKNOWN_OR_CONFLICTING_SOURCE"):
        b.verify_base22(root)


@pytest.mark.parametrize("case", ["extra_zero_partition", "renamed_empty", "legal_empty"])
def test_cold_checks_physical_partitions_even_without_rows(fixture, case):
    empty_table = "dwd_quality_exception"
    if case != "extra_zero_partition":
        # Only synthetic fixture files are removed before declaring a genuinely empty table.
        shutil.rmtree(fixture.root / "sources/history/payload" / empty_table)
        fixture.history[empty_table] = []
        fixture.refresh()
        fixture.plan["tables"][empty_table]["required_partitions"] = {
            "empty": {"rows": 0, "key_digest": "sha256:" + hashlib.sha256().hexdigest()}
        }
    fixture.build()
    root = fixture.root / "partition-repacked"
    shutil.copytree(fixture.output, root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    if case == "extra_zero_partition":
        name = "dwd_security_bar"
        part = "interval_size=1d/observation_start_year=2026/observation_start_month=6"
        path = root / "payload" / name / part / "part-00000.parquet"
        path.parent.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist([], schema=fixture.schemas[name]), path)
        manifest["files"].append(
            {**b._file_record(root, path), "table": name, "partition": part, "row_count": 0}
        )
    elif case == "renamed_empty":
        item = next(f for f in manifest["files"] if f.get("table") == empty_table)
        original = root / item["path"]
        path = root / "payload" / empty_table / "all/part-00000.parquet"
        path.parent.mkdir()
        original.rename(path)
        original.parent.rmdir()
        item.update(path=path.relative_to(root).as_posix(), partition="all")
    _save(root / "manifest.json", manifest)
    _repack_synthetic_image(root)
    if case == "legal_empty":
        assert b.verify_base22(root)["tables"][empty_table]["rows"] == 0
    else:
        with pytest.raises(b.Base22Error, match="PHYSICAL_PARTITION_SET_MISMATCH"):
            b.verify_base22(root)


@pytest.mark.parametrize("mode", ["build", "cold"])
def test_daily_calendar_uses_shanghai_date_and_preserves_the_instant(fixture, mode):
    # UTC July 30 is already July 31 in Shanghai. The daily bar must use July 31.
    instant = datetime(2026, 7, 30, 16, 30, tzinfo=timezone.utc)
    if mode == "build":
        fixture.history["dwd_security_bar"][0]["observation_start"] = instant
        fixture.refresh()
        result = fixture.build()
        root = fixture.output
    else:
        fixture.build()
        root = fixture.root / "shanghai-repacked"
        shutil.copytree(fixture.output, root)
        manifest = json.loads((root / "manifest.json").read_bytes())
        item = next(
            f
            for f in manifest["files"]
            if f.get("table") == "dwd_security_bar"
            and "observation_start_month=7" in f["partition"]
        )
        rows = list(b._rows(root / item["path"], fixture.schemas["dwd_security_bar"]))
        rows[0]["observation_start"] = instant
        pq.write_table(
            pa.Table.from_pylist(rows, schema=fixture.schemas["dwd_security_bar"]),
            root / item["path"],
        )
        # A changed version key requires a correspondingly frozen key census.
        _repack_synthetic_image(root)
        manifest = json.loads((root / "manifest.json").read_bytes())
        plan_path = root / "evidence/input-plan.json"
        plan = json.loads(plan_path.read_bytes())
        plan["tables"]["dwd_security_bar"]["required_partitions"] = {
            key: {"rows": value["rows"], "key_digest": value["key_digest"]}
            for key, value in manifest["tables"]["dwd_security_bar"]["partitions"].items()
        }
        _save(plan_path, plan)
        _repack_synthetic_image(root)
        result = b.verify_base22(root)
    assert result["tables"]["dwd_security_bar"]["rows"] == 2
    manifest = json.loads((root / "manifest.json").read_bytes())
    item = next(
        f
        for f in manifest["files"]
        if f.get("table") == "dwd_security_bar" and "observation_start_month=7" in f["partition"]
    )
    row = next(b._rows(root / item["path"], fixture.schemas["dwd_security_bar"]))
    assert row["observation_start"] == instant
    assert row["calendar_session_id"] == "calendar-2026-07-31"


@pytest.mark.parametrize("case", ["absent", "changed"])
def test_cold_requires_image_local_qualification_evidence(fixture, case):
    fixture.build()
    root = fixture.root / "evidence-repacked"
    shutil.copytree(fixture.output, root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    relative = "evidence/qualification/august/evidence/calendar.json"
    path = root / relative
    if case == "absent":
        path.unlink()
        manifest["files"] = [f for f in manifest["files"] if f["path"] != relative]
        _save(manifest_path, manifest)
        reason = "INDEPENDENT_FILE_REQUIRED"
    else:
        document = json.loads(path.read_bytes())
        document["open_dates"] = []
        _save(path, document)
        reason = "QUALIFICATION_EVIDENCE_IDENTITY_MISMATCH"
    _repack_synthetic_image(root)
    shutil.rmtree(fixture.root / "sources")
    with pytest.raises(b.Base22Error, match=reason):
        b.verify_base22(root)
