"""Source-admit Daily, identity extensions and closed-day events into one period.

Only frozen package/evidence members are opened. Legacy retained releases use
an explicit parent-pinned adapter; no source READY or old catalog is invented.
Daily and Base share the actual source projection, not two diverging converters.
"""

from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from quanthus.pipelines.contracts import OdsRelease

from .base22_bounded import BoundedError, Budget
from .base23_identity_overlay import build_overlay
from .base24_delivery import ONE_MINUTE_DISPOSITION
from .base_build_delta import _connection, build_delta, relation
from .base_build_freeze import cold_verify
from .base_build_inputs import canonical, file_sha, identity, member
from .base_build_profile import REFERENCE_TABLES, UPDATED_TABLES
from .daily_base24 import verify as verify_daily
from .daily_core_domain import (
    _artifact,
    _audit_non_output_daily_artifacts,
    _project_announcements,
    _project_daily_tables,
    _project_trading_calendar,
)
from .native_base23_reader import _read_native_tables

POLICY = Path(__file__).resolve().parents[4] / "config/pipelines/p10-acquisition-prevention.v1.json"
PARENTS = tuple(sorted(REFERENCE_TABLES | {"dwd_trading_venue", "dwd_market_index"}))
LEDGER_CONTRACT = "p10-daily-delivery-security-day-v1"


@dataclass(frozen=True)
class VerifiedInputs:
    """In-process shared prerequisite; never deserialized from an old receipt."""

    frozen: dict[str, Any]
    roots: dict[str, str]
    verified_identity: str

    @classmethod
    def verify(cls, frozen: dict[str, Any], roots: dict[str, str]) -> VerifiedInputs:
        actual = cold_verify(frozen, roots)
        return cls(actual, dict(roots), actual["identity"])


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise BoundedError("BUILD_PERIOD_OBJECT_REQUIRED")
    return value


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def selected_release(
    root: Path,
    relative: str,
    expected: dict[str, str],
    *,
    day: str,
    frozen_members: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Admit retained evidence by its parent's exact selection and member hashes.

    This adapter deliberately does not create READY for an archive that omitted
    the original marker. The already sealed Daily (or closed-day registration)
    pins the original release manifest. Its original status/clocks stay intact.
    """
    path = member(root, relative)
    if (
        relative not in frozen_members
        or file_sha(path) != expected["manifest_sha256"]
        or frozen_members[relative]["sha256"] != expected["manifest_sha256"]
    ):
        raise BoundedError("BUILD_PERIOD_SOURCE_PARENT_BINDING_CHANGED")
    release = OdsRelease.load(path)
    if release.release_id != expected["release_id"] or release.market_date.isoformat() != day:
        raise BoundedError("BUILD_PERIOD_SOURCE_SCOPE_CHANGED")
    artifacts = {}
    for artifact in release.artifacts:
        p = member(path.parent, artifact.relative_path.as_posix())
        rel = p.relative_to(root).as_posix()
        if (
            rel not in frozen_members
            or file_sha(p) != artifact.sha256
            or p.stat().st_size != artifact.size_bytes
            or frozen_members[rel]["sha256"] != artifact.sha256
        ):
            raise BoundedError("BUILD_PERIOD_SOURCE_ARTIFACT_NOT_FROZEN_OR_CHANGED")
        artifacts[artifact.name] = p, artifact
    return {
        "release_id": release.release_id,
        "manifest_sha256": file_sha(path),
        "market_date": day,
        "artifacts": artifacts,
        "original_created_at": release.created_at.isoformat(),
    }


def expected_days(calendar: Path, after: str, through: str) -> list[str]:
    pq = importlib.import_module("pyarrow.parquet")
    cells = {}
    start, end = (
        date.fromisoformat(after) + timedelta(days=1),
        date.fromisoformat(through),
    )
    for batch in pq.ParquetFile(calendar).iter_batches(batch_size=8192):
        for row in batch.to_pylist():
            day = date.fromisoformat(str(row["trade_date"]))
            if row["exchange"] not in {"SSE", "SZSE"} or not start <= day <= end:
                continue
            key = row["exchange"], day
            opened = row["is_open"]
            if type(opened) not in (bool, int) or opened not in (0, 1):
                raise BoundedError("BUILD_PERIOD_CALENDAR_OPEN_INVALID")
            if key in cells and cells[key] != bool(opened):
                raise BoundedError("BUILD_PERIOD_CALENDAR_CONFLICT")
            cells[key] = bool(opened)
    result = []
    day = start
    while day <= end:
        if any((venue, day) not in cells for venue in ("SSE", "SZSE")):
            raise BoundedError("BUILD_PERIOD_CALENDAR_DATE_MISSING")
        if cells["SSE", day] != cells["SZSE", day]:
            raise BoundedError("BUILD_PERIOD_ASYMMETRIC_CALENDAR_REQUIRES_ADAPTER")
        if cells["SSE", day]:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


def source_universe(
    selected: dict[str, Any], day: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pq = importlib.import_module("pyarrow.parquet")
    path, _ = _artifact(selected, "market_daily_core", "source_universe")
    master_path, artifact = _artifact(selected, "market_daily_core", "security_master")
    universe = load(path)
    codes = universe.get("codes", [])
    rows = pq.ParquetFile(master_path).read().to_pylist()
    if (
        universe.get("market_date") != day
        or universe.get("historical_pit_authorized") is not False
        or universe.get("policy_id") != "p10-acquisition-prevention-v1"
        or universe.get("scope") != "SSE_SZSE_A_SHARE_00_30_60_68"
        or not codes
        or len(codes) != len(set(codes))
        or universe.get("population") != len(codes)
        or universe.get("files", {}).get("security_master.parquet") != artifact.sha256
        or {r["stock_code"] for r in rows} != set(codes)
        or len(rows) != len(codes)
    ):
        raise BoundedError("BUILD_PERIOD_INDEPENDENT_UNIVERSE_CHANGED")
    for row in rows:
        code = row["stock_code"]
        if (
            not re.fullmatch(r"(?:00|30|60|68)[0-9]{4}", code)
            or row["exchange"] != ("SSE" if code.startswith("6") else "SZSE")
            or row["target_date"] != day
            or not row["list_date"]
            or row["list_date"] > day
            or (row["delist_date"] is not None and day >= row["delist_date"])
        ):
            raise BoundedError("BUILD_PERIOD_LISTING_DATE_OR_SCOPE_CONFLICT")
    return universe, rows


def source_quality(selected: dict[str, Any], day: str) -> None:
    """Check required physical observations and retained acquisition-policy binding."""
    pq = importlib.import_module("pyarrow.parquet")
    for lane in selected:
        ledger_path, _ = _artifact(selected, lane, "acquisition_quality")
        ledger = load(ledger_path)
        raw_path, raw_artifact = _artifact(selected, lane, "original_responses")
        if (
            ledger.get("policy_id") != "p10-acquisition-prevention-v1"
            or ledger.get("policy_sha256") != file_sha(POLICY)
            or ledger.get("market_date") != day
            or ledger.get("original_responses_sha256") != raw_artifact.sha256
            or file_sha(raw_path) != raw_artifact.sha256
        ):
            raise BoundedError("BUILD_PERIOD_ACQUISITION_QUALITY_BINDING_CHANGED")
        required = (
            {
                "daily_ohlcv_none_delta",
                "daily_status_observation_delta",
                "valuation_daily_delta",
                "security_master",
                "trading_calendar",
                "index_daily_delta",
            }
            if lane == "market_daily_core"
            else {"announcements_delta"}
        )
        entries = {r["dataset"]: r for r in ledger["datasets"]}
        if len(entries) != len(ledger["datasets"]):
            raise BoundedError("BUILD_PERIOD_ACQUISITION_DATASET_DUPLICATE")
        for name in required:
            path, _ = _artifact(selected, lane, name)
            entry = entries.get(name, {})
            if (
                entry.get("physical_status") != "ready"
                or entry.get("daily_core_blocked") is not False
                or entry.get("row_count") != pq.ParquetFile(path).metadata.num_rows
            ):
                raise BoundedError("BUILD_PERIOD_REQUIRED_SOURCE_NOT_QUALIFIED")


def compare_tables(expected: list[Path], observed: list[Path], connection: Any) -> None:
    """Compare every typed value and multiplicity; never rely on row count or hash alone."""
    left, right = relation(expected), relation(observed)
    query = (
        f"SELECT 1 FROM ((SELECT * FROM {left} EXCEPT ALL SELECT * FROM {right}) "
        f"UNION ALL (SELECT * FROM {right} EXCEPT ALL SELECT * FROM {left})) LIMIT 1"
    )
    if connection.execute(query).fetchone() is not None:
        raise BoundedError("BUILD_PERIOD_SOURCE_REPLAY_DIFFERS")


def _views(connection: Any, parents: dict[str, list[Path]], overlay: dict[str, Path]) -> None:
    for view, table in (
        ("base_listing", "dwd_listing"),
        ("base_security", "dwd_security"),
        ("base_venue", "dwd_trading_venue"),
        ("base_market_index", "dwd_market_index"),
    ):
        paths = parents[table] + ([overlay[table]] if table in overlay else [])
        connection.execute(
            "CREATE OR REPLACE TEMP VIEW " + view + " AS SELECT * FROM " + relation(paths)
        )


def prepare_period(
    *,
    inputs: VerifiedInputs,
    binding: dict[str, Any],
    rules: dict[str, Any],
    schemas: dict[str, Any],
    output: Path,
    budget: Budget,
    source_evidence_id: str,
    closed_days: list[dict[str, str]],
    calendar_reference: dict[str, str],
) -> dict[str, Any]:
    """Replay all declared Daily sources and then materialize the shared typed delta."""
    frozen, roots = inputs.frozen, inputs.roots
    selection = frozen["content"]["selection"]
    if (
        inputs.verified_identity != frozen["identity"]
        or binding["frozen_input_identity"] != frozen["identity"]
        or binding["predecessor_through"] != selection["predecessor_through"]
        or binding["through_date"] != selection["through_date"]
        or binding["target_version"] != selection["target_version"]
    ):
        raise BoundedError("BUILD_PERIOD_SCOPE_BINDING_CHANGED")
    output = budget.destination(output)
    if output.exists():
        raise BoundedError("BUILD_PERIOD_OUTPUT_EXISTS_PRESERVED")
    output.mkdir()
    inventory = frozen["content"]["packages"]
    source_root = Path(roots[source_evidence_id])
    source_members = {r["path"]: r for r in inventory[source_evidence_id]["content"]["files"]}
    release_index: dict[str, list[str]] = {}
    for relative, item in source_members.items():
        if relative.endswith("/release.json"):
            release_index.setdefault(item["sha256"], []).append(relative)
    calendar = selection["expected_days_evidence"]
    selected_calendar_path = member(Path(roots[calendar["package_id"]]), calendar["path"])
    if calendar_reference["evidence_id"] != calendar["package_id"]:
        raise BoundedError("BUILD_PERIOD_CALENDAR_PARENT_BINDING_CHANGED")
    reference_root = Path(roots[calendar["package_id"]])
    reference_members = {
        r["path"]: r for r in inventory[calendar["package_id"]]["content"]["files"]
    }
    if (
        not {
            calendar_reference[k] for k in ("path", "parent_release", "parent_core", "parent_daily")
        }
        <= reference_members.keys()
    ):
        raise BoundedError("BUILD_PERIOD_CALENDAR_PARENT_NOT_FROZEN")
    calendar_path = member(reference_root, calendar_reference["path"])
    calendar_release = OdsRelease.load(member(reference_root, calendar_reference["parent_release"]))
    calendar_artifacts = [
        a for a in calendar_release.artifacts if a.name == calendar_reference["artifact_name"]
    ]
    if len(calendar_artifacts) != 1 or calendar_artifacts[0].sha256 != file_sha(calendar_path):
        raise BoundedError("BUILD_PERIOD_CALENDAR_PARENT_BINDING_CHANGED")
    calendar_artifact = calendar_artifacts[0]
    parent_core_path = member(reference_root, calendar_reference["parent_core"])
    parent_core = load(parent_core_path)
    parent_daily = load(member(reference_root, calendar_reference["parent_daily"]))
    release_sha = file_sha(member(reference_root, calendar_reference["parent_release"]))
    if (
        parent_core.get("selected_releases", {}).get("market_daily_core", {}).get("manifest_sha256")
        != release_sha
        or not any(
            r.get("path") == "members/core/manifest.json"
            and r.get("sha256") == file_sha(parent_core_path)
            for r in parent_daily.get("files", [])
        )
        or parent_core.get("market_date") != calendar_release.market_date.isoformat()
        or parent_daily.get("market_date") != parent_core.get("market_date")
    ):
        raise BoundedError("BUILD_PERIOD_CALENDAR_PARENT_CHAIN_CHANGED")
    days = expected_days(calendar_path, binding["predecessor_through"], binding["through_date"])
    if days != selection["expected_days"]:
        raise BoundedError("BUILD_PERIOD_CALENDAR_SELECTION_DIFFERS")
    if (
        expected_days(
            selected_calendar_path,
            binding["predecessor_through"],
            binding["through_date"],
        )
        != days
    ):
        raise BoundedError("BUILD_PERIOD_SELECTED_CALENDAR_DIFFERS_FROM_SOURCE")
    packages = sorted(
        (r for r in selection["packages"] if r["role"] == "daily"),
        key=lambda r: r["day"],
    )
    if days[-1] < binding["through_date"] and [r["day"] for r in closed_days] != [
        binding["through_date"]
    ]:
        raise BoundedError("BUILD_PERIOD_CUTOFF_CLOSED_DAY_SOURCE_REQUIRED")
    for package in packages:
        core = load(Path(roots[package["id"]]) / "members/core/manifest.json")
        for expected in core["selected_releases"].values():
            if len(release_index.get(expected["manifest_sha256"], [])) != 1:
                raise BoundedError("BUILD_PERIOD_SELECTED_SOURCE_NOT_UNIQUE")
    base = next(r for r in selection["packages"] if r["role"] == "dwd")
    if base["manifest_sha256"] != binding["predecessor_manifest_sha256"]:
        raise BoundedError("BUILD_PERIOD_PREDECESSOR_IDENTITY_CHANGED")
    predecessor = Path(roots[base["id"]])
    first_daily = Path(roots[packages[0]["id"]])
    config = load(first_daily / "config.json")
    metadata = load(predecessor / "manifest.json")
    parent = _read_native_tables(
        predecessor,
        config,
        PARENTS,
        profile=(
            metadata["kind"],
            selection["predecessor_version"],
            metadata["purpose"],
            config["layers"]["dwd"]["format"],
        ),
        independent_files=True,
        default_intervals=("1d", "5m"),
        one_minute_disposition=ONE_MINUTE_DISPOSITION,
    )
    parents = {table: parent.files(table) for table in PARENTS}
    pq, pa = (
        importlib.import_module("pyarrow.parquet"),
        importlib.import_module("pyarrow"),
    )
    listings = [row for p in parents["dwd_listing"] for row in pq.ParquetFile(p).read().to_pylist()]
    venues = [
        row for p in parents["dwd_trading_venue"] for row in pq.ParquetFile(p).read().to_pylist()
    ]
    tables: dict[str, list[Path]] = {t: [] for t in UPDATED_TABLES | REFERENCE_TABLES}
    admissions, ledger = [], []
    after = binding["predecessor_through"]
    last_overlay = {}
    for package in packages:
        day, root = package["day"], Path(roots[package["id"]])
        budget.begin_stage("source-replay-" + day)
        print(
            json.dumps({"stage": "period-source-admission", "day": day, "status": "STARTED"}),
            flush=True,
        )
        verified = verify_daily(root)
        if set(verified["source_grades"]) - {"R0", "SUSPENDED"}:
            raise BoundedError("BUILD_PERIOD_NEW_QUALITY_HELD_OR_NON48_SOURCE")
        core = load(root / "members/core/manifest.json")
        if core["covered_after_date"] != after or core["base"] != dict(parent.binding):
            raise BoundedError("BUILD_PERIOD_DAILY_CHAIN_OR_BASE_CHANGED")
        selected = {}
        for lane, expected in core["selected_releases"].items():
            matches = release_index.get(expected["manifest_sha256"], [])
            if len(matches) != 1:
                raise BoundedError("BUILD_PERIOD_SELECTED_SOURCE_NOT_UNIQUE")
            selected[lane] = selected_release(
                source_root,
                matches[0],
                expected,
                day=day,
                frozen_members=source_members,
            )
        if set(selected) != {"market_daily_core", "calendar_daily_events"}:
            raise BoundedError("BUILD_PERIOD_DAILY_LANE_SET_CHANGED")
        source_quality(selected, day)
        universe, master_rows = source_universe(selected, day)
        if core["acquisition_universe"] != universe:
            raise BoundedError("BUILD_PERIOD_CORE_UNIVERSE_DIFFERS_FROM_SOURCE")
        work = output / "source-replay" / day
        work.mkdir(parents=True)
        master, artifact = _artifact(selected, "market_daily_core", "security_master")
        overlay = build_overlay(
            universe=universe,
            master=master,
            listings=listings,
            venues=venues,
            output=work,
            source_sha256=artifact.sha256,
        )
        actual_references = {
            p.parent.name: p for p in (root / "members/core/reference").glob("*/*.parquet")
        }
        if set(actual_references) != set(overlay):
            raise BoundedError("BUILD_PERIOD_REFERENCE_EXTENSION_SET_CHANGED")
        with _connection(budget) as connection:
            for table, path in overlay.items():
                compare_tables([path], [actual_references[table]], connection)
                tables[table].append(path)
            _views(connection, parents, overlay)
            path, artifact = _artifact(selected, "market_daily_core", "daily_ohlcv_none_delta")
            projected, held = _project_daily_tables(
                connection,
                selected=selected,
                work=work,
                market_date=date.fromisoformat(day),
                covered_after=date.fromisoformat(after),
                catalog_digest=core["catalog_digest"],
                bar_sources=[(path, artifact, "1d", "INTERVAL 0 SECOND")],
                check_identity=True,
            )
            if held:
                raise BoundedError("BUILD_PERIOD_NEW_DISCLOSURE_QUALITY_HELD")
            dispositions = _audit_non_output_daily_artifacts(selected, parquet=pq)
            for table, path in projected.items():
                originals = sorted(
                    (root / "members/core").glob("payload/*/" + table + "/market_date=*/*.parquet")
                )
                compare_tables([path], originals, connection)
                tables[table].extend([member(root, p) for p in verified["table_paths"][table]])
            states_path, _ = _artifact(
                selected, "market_daily_core", "daily_status_observation_delta"
            )
            states = pq.ParquetFile(states_path).read().to_pylist()
            if len(states) != len(master_rows) or {r["stock_code"] for r in states} != set(
                universe["codes"]
            ):
                raise BoundedError("BUILD_PERIOD_SECURITY_DAY_DENOMINATOR_DIFFERS")
        native_status = {
            r["stock_code"]: r
            for r in (
                json.loads(s)
                for s in (root / "members/native5m/quality-status.jsonl").read_text().splitlines()
            )
        }
        for row in master_rows:
            code = row["stock_code"]
            scope = {
                "contract": LEDGER_CONTRACT,
                "venue": row["exchange"],
                "stock_code": code,
                "market_date": day,
                "intervals": ["1d", "5m"],
            }
            ledger.append(
                {
                    "window_id": identity(scope),
                    **{k: v for k, v in scope.items() if k not in {"contract", "intervals"}},
                    "status": "completed",
                    "disposition": native_status[code]["quality_grade"],
                    "parent_daily_manifest_sha256": package["manifest_sha256"],
                }
            )
        admissions.append(
            {
                "day": day,
                "parent_daily": package["manifest_sha256"],
                "universe_population": len(master_rows),
                "source_proof": verified,
                "source_manifests": {k: v["manifest_sha256"] for k, v in selected.items()},
                "ods_only_artifacts": dispositions,
                "held_disclosures": held,
            }
        )
        after, last_overlay = day, overlay
        print(
            json.dumps(
                {
                    "stage": "period-source-admission",
                    "day": day,
                    "expected_security_days": len(master_rows),
                    "status": "PASS",
                }
            ),
            flush=True,
        )
    if after < binding["through_date"] and [r["day"] for r in closed_days] != [
        binding["through_date"]
    ]:
        raise BoundedError("BUILD_PERIOD_CUTOFF_CLOSED_DAY_SOURCE_REQUIRED")
    for bound in closed_days:
        print(
            json.dumps({"stage": "closed-day-events", "day": bound["day"], "status": "STARTED"}),
            flush=True,
        )
        if not after < bound["day"] <= binding["through_date"]:
            raise BoundedError("BUILD_PERIOD_CLOSED_DAY_SCOPE_CHANGED")
        evidence_id = bound["evidence_id"]
        source_root = Path(roots[evidence_id])
        source_members = {r["path"]: r for r in inventory[evidence_id]["content"]["files"]}
        selected = {
            "calendar_daily_events": selected_release(
                source_root,
                bound["release_path"],
                bound,
                day=bound["day"],
                frozen_members=source_members,
            )
        }
        source_quality(selected, bound["day"])
        work = output / "source-replay" / bound["day"]
        work.mkdir(parents=True)
        with _connection(budget) as connection:
            _views(connection, parents, last_overlay)
            path, artifact = _artifact(selected, "calendar_daily_events", "announcements_delta")
            disclosure, subjects, held = _project_announcements(
                connection,
                path=path,
                artifact=artifact,
                work=work,
                after=date.fromisoformat(after),
                through=date.fromisoformat(bound["day"]),
                check_identity=True,
            )
            if held:
                raise BoundedError("BUILD_PERIOD_NEW_CLOSED_DISCLOSURE_QUALITY_HELD")
            dispositions = _audit_non_output_daily_artifacts(selected, parquet=pq, closed_day=True)
        tables["dwd_disclosure"].append(disclosure)
        tables["dwd_disclosure_subject"].append(subjects)
        count = pq.ParquetFile(disclosure).metadata.num_rows
        admissions.append(
            {
                "day": bound["day"],
                "kind": "CLOSED_DAY_EVENTS",
                "release_manifest_sha256": bound["manifest_sha256"],
                "disclosures": count,
                "subjects": pq.ParquetFile(subjects).metadata.num_rows,
                "held_disclosures": held,
                "ods_only_artifacts": dispositions,
            }
        )
        after = bound["day"]
    if after != binding["through_date"]:
        raise BoundedError("BUILD_PERIOD_EVENT_COVERAGE_INCOMPLETE")
    # Daily delivers its actual target sessions. Preserve those clocks, and fill
    # intervening closed calendar dates from the independently pinned reference.
    day = date.fromisoformat(binding["predecessor_through"]) + timedelta(days=1)
    while day <= date.fromisoformat(binding["through_date"]):
        if day.isoformat() not in days:
            with _connection(budget) as connection:
                _views(connection, parents, {})
                path = _project_trading_calendar(
                    connection,
                    path=calendar_path,
                    artifact=calendar_artifact,
                    work=output / "closed-calendar" / day.isoformat(),
                    market_date=day,
                    record_filename=calendar_artifact.relative_path.name,
                )
                if (
                    connection.execute(
                        "SELECT 1 FROM " + relation([path]) + " WHERE is_open LIMIT 1"
                    ).fetchone()
                    is not None
                ):
                    raise BoundedError("BUILD_PERIOD_CLOSED_CALENDAR_OPEN_CONFLICT")
                if pq.ParquetFile(path).metadata.num_rows < 2:
                    raise BoundedError("BUILD_PERIOD_CLOSED_CALENDAR_VENUE_MISSING")
            tables["dwd_trading_calendar"].append(path)
        day += timedelta(days=1)
    for table in REFERENCE_TABLES:
        if not tables[table]:
            p = output / "empty-reference" / (table + ".parquet")
            p.parent.mkdir(exist_ok=True)
            pq.write_table(pa.Table.from_pylist([], schema=schemas[table]), p)
            tables[table] = [p]
    ledger_path = output / "security-days.parquet"
    if len({r["window_id"] for r in ledger}) != len(ledger):
        raise BoundedError("BUILD_PERIOD_WINDOW_DUPLICATE")
    pq.write_table(pa.Table.from_pylist(ledger), ledger_path, compression="zstd")
    delta = build_delta(
        tables=tables,
        rules=rules,
        schemas=schemas,
        binding=binding,
        output=output / "delta",
        budget=budget,
    )
    result = {
        "kind": "p10-standard-base-period-admission-v1",
        "binding": binding,
        "calendar_sha256": file_sha(calendar_path),
        "daily": admissions,
        "delta_identity": delta["identity"],
        "delta_relative_path": "delta",
        "new_window_contract": LEDGER_CONTRACT,
        "new_windows": len(ledger),
        "ledger_sha256": file_sha(ledger_path),
        "new_quality_held": sum(a["held_disclosures"] for a in admissions),
        "lifecycle": "TMP",
        "full_Base_acceptance": False,
        "READY": False,
    }
    result["identity"] = identity(result)
    _save(output / "ADMISSION.json", result)
    return result
