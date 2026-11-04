"""Build-period adapter for ordinary, self-contained standard Daily deliveries.

Shares the existing source converters, typed delta and security-day/window rules.
Selection/configuration is derived from delivery manifests, without source hunts.
"""

from __future__ import annotations

import importlib
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .base22_bounded import BoundedError, Budget
from .base24_delivery import ONE_MINUTE_DISPOSITION
from .base_build_delta import _connection, build_delta, literal, relation
from .base_build_freeze import CONTRACT_V2, validate_selection
from .base_build_inputs import GENERIC_KINDS, _tree, file_sha, identity, member
from .base_build_period import (
    LEDGER_CONTRACT,
    PARENTS,
    VerifiedInputs,
    _save,
    load,
    qualified_calendar_union,
)
from .base_build_profile import REFERENCE_TABLES, UPDATED_TABLES
from .native_base23_reader import _read_native_tables
from .standard_daily import _sources, inspect, verify

MODE = "@standard-calendar-day-deliveries-v1"


def _coalesce_events(
    tables: dict[str, list[Path]], schemas: dict[str, Any], output: Path, budget: Budget
) -> None:
    """Select an original earliest observation only when every business fact agrees."""
    lineage = {
        "record_source",
        "source_record_id",
        "source_revision",
        "source_artifact_sha256",
        "knowledge_from",
        "knowledge_to",
    }
    source_key = (
        "record_source",
        "source_record_id",
        "source_revision",
        "source_artifact_sha256",
        "knowledge_from",
    )
    output.mkdir()
    with _connection(budget) as connection:
        for table, key, view in (
            ("dwd_disclosure", "disclosure_id", "docs"),
            ("dwd_disclosure_subject", "disclosure_subject_id", "subjects"),
        ):
            connection.execute(
                "CREATE TEMP VIEW " + view + " AS SELECT * FROM " + relation(tables[table])
            )
            facts = ",".join('"' + c + '"' for c in schemas[table].names if c not in lineage)
            bad = connection.execute(
                "SELECT "
                + key
                + " FROM "
                + view
                + " GROUP BY "
                + key
                + " HAVING count(DISTINCT row("
                + facts
                + "))>1 OR bool_or(knowledge_to IS NOT NULL OR knowledge_from IS NULL) LIMIT 1"
            ).fetchone()
            if bad:
                raise BoundedError("BUILD_STANDARD_EVENT_REVISION_REQUIRES_DISPOSITION")
        same = " AND ".join("s." + k + " IS NOT DISTINCT FROM d." + k for k in source_key)
        condition = "s.disclosure_id=d.disclosure_id AND " + same
        if connection.execute(
            "SELECT 1 FROM subjects s LEFT JOIN docs d ON "
            + condition
            + " WHERE d.disclosure_id IS NULL LIMIT 1"
        ).fetchone():
            raise BoundedError("BUILD_STANDARD_EVENT_SUBJECT_SOURCE_MISSING")
        keys = ",".join("d." + k for k in ("disclosure_id", *source_key))
        connection.execute(
            "CREATE TEMP VIEW subject_sets AS SELECT "
            + keys
            + ", list_sort(list(s.disclosure_subject_id) "
            "FILTER(WHERE s.disclosure_subject_id IS NOT NULL)) ids "
            "FROM (SELECT DISTINCT * FROM docs) d "
            "LEFT JOIN (SELECT DISTINCT * FROM subjects) s ON " + condition + " GROUP BY " + keys
        )
        if connection.execute(
            "SELECT disclosure_id FROM subject_sets GROUP BY disclosure_id "
            "HAVING count(DISTINCT coalesce(ids,[]::VARCHAR[]))>1 LIMIT 1"
        ).fetchone():
            raise BoundedError("BUILD_STANDARD_EVENT_SUBJECT_REVISION_REQUIRES_DISPOSITION")
        order = "knowledge_from,source_artifact_sha256,source_revision,source_record_id"
        connection.execute(
            "CREATE TEMP VIEW chosen_docs AS SELECT * FROM docs "
            "QUALIFY row_number() OVER(PARTITION BY disclosure_id ORDER BY " + order + ")=1"
        )
        for table, query in (
            ("dwd_disclosure", "SELECT * FROM chosen_docs"),
            (
                "dwd_disclosure_subject",
                "SELECT s.* FROM subjects s JOIN chosen_docs d ON "
                + condition
                + " QUALIFY row_number() OVER(PARTITION BY s.disclosure_subject_id "
                "ORDER BY s.knowledge_from)=1",
            ),
        ):
            path = output / (table + ".parquet")
            connection.execute(
                "COPY ("
                + query
                + ") TO "
                + literal(str(path))
                + " (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            tables[table] = [path]


def deliveries(inputs: VerifiedInputs) -> list[tuple[str, Path, dict[str, Any]]]:
    selection = inputs.frozen["content"]["selection"]
    ids = [p["id"] for p in selection["packages"] if p["role"] == "daily"]
    for entry in selection["evidence"]:
        if entry["purpose"] == "daily_source_evidence":
            ids.append(entry["id"])
        elif entry["purpose"] != "historical_quality":
            raise BoundedError("BUILD_STANDARD_EXTERNAL_REFERENCE_PREPARATION_NOT_ALLOWED")
    result = [(sid, Path(inputs.roots[sid]), inspect(Path(inputs.roots[sid]))) for sid in ids]
    if len({body["market_date"] for _, _, body in result}) != len(result):
        raise BoundedError("BUILD_STANDARD_DELIVERY_DAY_DUPLICATE")
    return sorted(result, key=lambda v: v[2]["market_date"])


def prepare(
    *,
    inputs: VerifiedInputs,
    binding: dict[str, Any],
    rules: dict[str, Any],
    schemas: dict[str, Any],
    output: Path,
    budget: Budget,
    closed_days: list[dict[str, str]],
    calendar_reference: dict[str, str],
) -> dict[str, Any]:
    from .native_base_standard_reader import read_native_tables as read_standard

    selection = inputs.frozen["content"]["selection"]
    selected = deliveries(inputs)
    all_days = []
    day = date.fromisoformat(binding["predecessor_through"]) + timedelta(days=1)
    while day <= date.fromisoformat(binding["through_date"]):
        all_days.append(day.isoformat())
        day += timedelta(days=1)
    if [b["market_date"] for _, _, b in selected] != all_days:
        raise BoundedError("BUILD_STANDARD_CALENDAR_DAY_DELIVERY_MISSING_OR_EXTRA")
    packages = {p["id"]: p for p in selection["packages"] if p["role"] == "daily"}
    if set(packages) != {sid for sid, _, b in selected if b["day_kind"] == "market"}:
        raise BoundedError("BUILD_STANDARD_MARKET_PACKAGE_SET_CHANGED")
    derived_closed = [
        {
            "evidence_id": sid,
            "day": b["market_date"],
            **{
                "release_path": b["sources"]["calendar_daily_events"]["path"],
                "release_id": b["sources"]["calendar_daily_events"]["release_id"],
                "manifest_sha256": b["sources"]["calendar_daily_events"]["manifest_sha256"],
            },
        }
        for sid, _, b in selected
        if b["day_kind"] == "closed"
    ]
    if closed_days != derived_closed:
        raise BoundedError("BUILD_STANDARD_CLOSED_DELIVERY_CONFIGURATION_CHANGED")
    originals = [_sources(root, body) for _, root, body in selected]
    calendars = [
        r.get("market_daily_core", r.get("calendar_reference"))["artifacts"]["trading_calendar"]
        for r in originals
    ]
    days, _, calendar_union = qualified_calendar_union(
        calendars, binding["predecessor_through"], binding["through_date"]
    )
    if days != selection["expected_days"] or days != [
        b["market_date"] for _, _, b in selected if b["day_kind"] == "market"
    ]:
        raise BoundedError("BUILD_STANDARD_OBSERVED_CALENDAR_DIFFERS")
    anchor = selection["expected_days_evidence"]
    calendar_path = member(Path(inputs.roots[anchor["package_id"]]), anchor["path"])
    if (
        file_sha(calendar_path) != anchor["sha256"]
        or anchor["sha256"] not in {a.sha256 for _, a in calendars}
        or calendar_reference["evidence_id"] != anchor["package_id"]
        or calendar_reference["path"] != anchor["path"]
    ):
        raise BoundedError("BUILD_STANDARD_CALENDAR_ANCHOR_CHANGED")
    base = next(p for p in selection["packages"] if p["role"] == "dwd")
    predecessor = Path(inputs.roots[base["id"]])
    if base["manifest_sha256"] != binding["predecessor_manifest_sha256"]:
        raise BoundedError("BUILD_STANDARD_PARENT_IDENTITY_CHANGED")
    first = next(root for sid, root, _ in selected if sid in packages)
    config = load(member(first, "config.json"))
    metadata = load(member(predecessor, "manifest.json"))
    if metadata["kind"] == GENERIC_KINDS["dwd"]:
        parent = read_standard(
            predecessor, config, PARENTS, relocated_members=inputs.predecessor_members
        )
    else:
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
            relocated_members=inputs.predecessor_members,
        )
    output = budget.destination(output)
    output.mkdir()
    tables: dict[str, list[Path]] = {t: [] for t in UPDATED_TABLES | REFERENCE_TABLES}
    ledger, admissions = [], []
    for _sid, root, body in selected:
        day = body["market_date"]
        budget.begin_stage("standard-daily-replay-" + day)
        replay = verify(root, parent=parent, budget=budget, output=output / "source-replay" / day)
        for table, paths in replay.tables.items():
            tables[table].extend(paths)
        for table, path in replay.references.items():
            tables[table].append(path)
        for row in replay.master:
            scope = {
                "contract": LEDGER_CONTRACT,
                "venue": row["exchange"],
                "stock_code": row["stock_code"],
                "market_date": day,
                "intervals": ["1d", "5m"],
            }
            ledger.append(
                {
                    "window_id": identity(scope),
                    **{k: v for k, v in scope.items() if k not in {"contract", "intervals"}},
                    "status": "completed",
                    "disposition": replay.native_grades[row["stock_code"]]["quality_grade"],
                    "parent_daily_manifest_sha256": file_sha(root / "manifest.json"),
                }
            )
        admissions.append(
            {
                "day": day,
                "kind": body["day_kind"],
                "parent_daily": file_sha(root / "manifest.json"),
                "source_manifests": replay.source_manifests,
                "held_disclosures": 0,
                "ods_only_artifacts": replay.ods_only,
            }
        )
    _coalesce_events(tables, schemas, output / "event-observations", budget)
    pa, pq = importlib.import_module("pyarrow"), importlib.import_module("pyarrow.parquet")
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
    configuration = {
        "source_evidence_id": MODE,
        "closed_days": closed_days,
        "calendar_reference": calendar_reference,
    }
    _save(output / "CONFIG.json", configuration)
    result = {
        "kind": "p10-standard-base-period-admission-v1",
        "binding": binding,
        "source_configuration_identity": identity(configuration),
        "calendar_sha256": file_sha(calendar_path),
        "calendar_source_union": calendar_union,
        "daily": admissions,
        "delta_identity": delta["identity"],
        "delta_relative_path": "delta",
        "new_window_contract": LEDGER_CONTRACT,
        "new_windows": len(ledger),
        "ledger_sha256": file_sha(ledger_path),
        "new_quality_held": 0,
        "lifecycle": "TMP",
        "full_Base_acceptance": False,
        "READY": False,
    }
    result["identity"] = identity(result)
    _save(output / "ADMISSION.json", result)
    return result


def select(
    *, ods: Path, dwd: Path, daily_roots: list[Path], target_version: str, through_date: str
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """Derive one ordinary selection/config from delivered packages only."""
    ods, dwd = ods.resolve(), dwd.resolve()
    daily_roots = [root.resolve() for root in daily_roots]
    metadata = load(member(dwd, "manifest.json"))
    predecessor = metadata.get("version", "2.4")
    after = metadata["business_through"]
    packages, evidence, roots, closed, calendars, market = [], [], {}, [], [], []
    for sid, role, path in (("base-ods", "ods", ods), ("base-dwd", "dwd", dwd)):
        roots[sid] = str(path)
        packages.append(
            {
                "id": sid,
                "role": role,
                "day": None,
                "manifest_sha256": file_sha(path / "manifest.json"),
            }
        )
    for root in sorted(daily_roots):
        body = inspect(root)
        day = body["market_date"]
        sid = "delivery-" + day
        if sid in roots:
            raise BoundedError("BUILD_STANDARD_DELIVERY_DAY_DUPLICATE")
        roots[sid] = str(root)
        sources = _sources(root, body)
        observed = sources.get("market_daily_core", sources.get("calendar_reference"))
        assert observed is not None
        calendar, artifact = observed["artifacts"]["trading_calendar"]
        calendars.append((calendar, artifact))
        if body["day_kind"] == "market":
            packages.append(
                {
                    "id": sid,
                    "role": "daily",
                    "day": day,
                    "manifest_sha256": file_sha(root / "manifest.json"),
                }
            )
            market.append((sid, root, body, calendar))
        else:
            evidence.append(
                {
                    "id": sid,
                    "purpose": "daily_source_evidence",
                    "parent_manifests": [file_sha(root / "manifest.json")],
                    "files": [
                        {
                            "path": p,
                            "sha256": file_sha(member(root, p)),
                            "size_bytes": member(root, p).stat().st_size,
                        }
                        for p in sorted(_tree(root))
                    ],
                }
            )
            source = body["sources"]["calendar_daily_events"]
            closed.append(
                {
                    "evidence_id": sid,
                    "day": day,
                    "release_path": source["path"],
                    "release_id": source["release_id"],
                    "manifest_sha256": source["manifest_sha256"],
                }
            )
    days, _, _ = qualified_calendar_union(calendars, after, through_date)
    if not market:
        raise BoundedError("BUILD_STANDARD_MARKET_DELIVERY_REQUIRED")
    sid, root, body, calendar = sorted(market)[0]
    reference = {
        "evidence_id": sid,
        "path": calendar.relative_to(root).as_posix(),
        "parent_release": body["sources"]["market_daily_core"]["path"],
        "parent_core": "projection/members/core/manifest.json",
        "parent_daily": "manifest.json",
        "artifact_name": "trading_calendar",
    }
    selection = {
        "contract": CONTRACT_V2,
        "predecessor_version": predecessor,
        "target_version": target_version,
        "predecessor_through": after,
        "through_date": through_date,
        "expected_days": days,
        "expected_days_evidence": {
            "package_id": sid,
            "path": reference["path"],
            "sha256": file_sha(calendar),
        },
        "packages": sorted(packages, key=lambda p: p["id"]),
        "evidence": sorted(evidence, key=lambda p: p["id"]),
    }
    validate_selection(selection)
    configuration = {
        "source_evidence_id": MODE,
        "closed_days": sorted(closed, key=lambda c: c["day"]),
        "calendar_reference": reference,
    }
    return selection, roots, configuration


def main() -> None:
    """Prepare the existing build Job from Base plus ordinary delivered days."""
    import argparse
    import json

    from .base_build import REPOSITORY, validate_job
    from .base_build_freeze import freeze
    from .base_build_profile import delivery_contract

    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("ods", "dwd", "output", "work-root", "environment-lock", "wheelhouse"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--daily", type=Path, action="append", required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--through-date", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--limits", type=Path)
    args = parser.parse_args()
    selection, roots, configuration = select(
        ods=args.ods,
        dwd=args.dwd,
        daily_roots=args.daily,
        target_version=args.target_version,
        through_date=args.through_date,
    )
    frozen = freeze(selection, roots)
    controls = args.output.resolve()
    job = load(REPOSITORY / "config/pipelines/base-build-job.example.v1.json")
    job.update(
        builder_revision=args.code_revision,
        work_root=str(args.work_root.resolve()),
        environment_lock=str(args.environment_lock.resolve()),
        wheelhouse=str(args.wheelhouse.resolve()),
        selection=str(controls / "selection.json"),
        frozen=str(controls / "frozen.json"),
        roots=str(controls / "roots.json"),
        **configuration,
    )
    if args.limits:
        job["limits"] = load(args.limits)
    validate_job(job)
    contract = delivery_contract(
        load(REPOSITORY / "config/pipelines/base24-delivery.v1.json"),
        version=args.target_version,
        predecessor_version=selection["predecessor_version"],
        through=args.through_date,
    )
    # Do not overwrite controls from a previous preparation or failed build.
    controls.mkdir(parents=True, exist_ok=False)
    for name, value in (
        ("selection", selection),
        ("roots", roots),
        ("frozen", frozen),
        ("job", job),
        ("contract", contract),
    ):
        _save(controls / (name + ".json"), value)
    print(
        json.dumps(
            {
                "job": str(controls / "job.json"),
                "contract": str(controls / "contract.json"),
                "frozen_input_identity": frozen["identity"],
            }
        )
    )


if __name__ == "__main__":
    main()
