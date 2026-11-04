"""Self-contained calendar-day Daily delivery and fresh shared-source replay.

Original core/native products and complete source releases retain their bytes.
Original event windows remain explicit. The Builder may coalesce only unchanged
business facts across overlapping delivery observations. A delivery seal grants
no trading/research or independent acceptance.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.contracts import OdsRelease

from .base22_bounded import BoundedError, Budget, Limits, Member, independent_copy
from .base22_sources import disjoint_output
from .base23_identity_overlay import build_overlay
from .base_build_delta import _connection, relation
from .base_build_inputs import (
    GENERIC_KINDS,
    _tree,
    canonical,
    file_sha,
    identity,
    inventory,
    member,
)
from .daily_base24 import verify as verify_projection
from .daily_core_domain import (
    _artifact,
    _project_announcements,
    _project_daily_tables,
    _project_trading_calendar,
)
from .native_base23_reader import NativeBase23

CONTRACT = "p10-standard-calendar-day-delivery-v1"
POLICY = Path(__file__).resolve().parents[4] / "config/pipelines/standard-daily-delivery.v1.json"
EVENTS = {"dwd_disclosure", "dwd_disclosure_subject"}
COMMON = {"dwd_trading_calendar", *EVENTS}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise BoundedError("DAILY_DELIVERY_OBJECT_REQUIRED")
    return value


def save(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def copy_file(source: Path, target: Path, budget: Budget) -> None:
    independent_copy(Member(source, file_sha(source), source.stat().st_size, 0), target, budget)


def _sources(root: Path, body: dict[str, Any]) -> dict[str, Any]:
    from .base_build_period import selected_release, source_quality

    result = {}
    for role, record in body["sources"].items():
        release_path = member(root, record["path"])
        release = OdsRelease.load(release_path)
        files = {record["path"]: {"sha256": record["manifest_sha256"]}}
        for artifact in release.artifacts:
            path = member(release_path.parent, artifact.relative_path.as_posix())
            files[path.relative_to(root).as_posix()] = {"sha256": artifact.sha256}
        selected = selected_release(
            root, record["path"], record, day=record["market_date"], frozen_members=files
        )
        if selected["original_created_at"] != record["original_created_at"]:
            raise BoundedError("DAILY_DELIVERY_SOURCE_CLOCK_CHANGED")
        lane = "market_daily_core" if role == "calendar_reference" else role
        source_quality({lane: selected}, record["market_date"])
        result[role] = selected
    return result


def inspect(root: Path, *, sealed: bool = True) -> dict[str, Any]:
    """Physical/contract admission only; row admission always needs replay below."""
    manifest = load(member(root, "manifest.json"))
    content = manifest.get("content", {})
    if (
        manifest.get("kind") != GENERIC_KINDS["daily"]
        or manifest.get("source_contract") != CONTRACT
        or manifest.get("content_digest") != identity(content)
        or content.get("role") != "daily"
        or manifest.get("consumer_admission") != "NOT_GRANTED"
        or manifest.get("execution_authorized") is not False
    ):
        raise BoundedError("DAILY_DELIVERY_ENVELOPE_CONTRACT_CHANGED")
    if sealed:
        inventory(root, file_sha(root / "manifest.json"))
    else:
        declarations = content["files"]
        if _tree(root) != {r["path"] for r in declarations} | {"manifest.json"}:
            raise BoundedError("DAILY_DELIVERY_MEMBER_SET_CHANGED")
        for row in declarations:
            p = member(root, row["path"])
            if file_sha(p) != row["sha256"] or p.stat().st_size != row["size_bytes"]:
                raise BoundedError("DAILY_DELIVERY_MEMBER_CHANGED")
    body = load(member(root, "DELIVERY.json"))
    if (
        set(body)
        != {
            "contract",
            "day_kind",
            "market_date",
            "base",
            "projection",
            "sources",
            "table_paths",
            "reference_paths",
            "request_identity",
            "code_revision",
            "policy_sha256",
        }
        or body.get("contract") != CONTRACT
        or body.get("policy_sha256") != file_sha(POLICY)
        or file_sha(member(root, "contract.json")) != file_sha(POLICY)
        or body.get("day_kind") not in {"market", "closed"}
        or body["market_date"] != content.get("market_date")
        or body["day_kind"] != content.get("day_kind")
        or date.fromisoformat(body["market_date"]).isoformat() != body["market_date"]
        or body["market_date"] <= body["base"]["business_through"]
        or body["projection"] != ("projection" if body["day_kind"] == "market" else None)
        or set(body["sources"])
        != (
            {"market_daily_core", "calendar_daily_events"}
            if body["day_kind"] == "market"
            else {"calendar_reference", "calendar_daily_events"}
        )
    ):
        raise BoundedError("DAILY_DELIVERY_BODY_SCOPE_CHANGED")
    expected_tables = (
        {
            "dwd_security_bar",
            "dwd_security_valuation",
            "dwd_index_bar",
            "dwd_tradability_state",
            *COMMON,
        }
        if body["day_kind"] == "market"
        else COMMON
    )
    if set(body["table_paths"]) != expected_tables:
        raise BoundedError("DAILY_DELIVERY_TABLE_SET_CHANGED")
    for paths in body["table_paths"].values():
        if not paths or len(paths) != len(set(paths)):
            raise BoundedError("DAILY_DELIVERY_TABLE_PATHS_CHANGED")
        for path in paths:
            member(root, path)
    for path in body["reference_paths"].values():
        member(root, path)
    return body


@dataclass(frozen=True)
class Replay:
    tables: dict[str, list[Path]]
    references: dict[str, Path]
    universe: dict[str, Any] | None
    master: list[dict[str, Any]]
    native_grades: dict[str, dict[str, Any]]
    calendar: tuple[Path, Any]
    source_manifests: dict[str, str]
    ods_only: dict[str, Any]


def _replay(
    root: Path, body: dict[str, Any], parent: NativeBase23, budget: Budget, output: Path
) -> Replay:
    from .base_build_period import _views, compare_tables, source_universe
    from .daily_core_domain import _audit_non_output_daily_artifacts

    if body["base"] != dict(parent.binding):
        raise BoundedError("DAILY_DELIVERY_PARENT_BINDING_CHANGED")
    day = date.fromisoformat(body["market_date"])
    output = budget.destination(output)
    output.mkdir(parents=True, exist_ok=False)
    selected = _sources(root, body)
    market = selected.get("market_daily_core", selected.get("calendar_reference"))
    assert market is not None
    source_day = market["market_date"]
    universe, master = source_universe({"market_daily_core": market}, source_day)
    pq = importlib.import_module("pyarrow.parquet")
    parents = {table: list(parent.files(table)) for table in parent.table_paths}
    overlay = build_overlay(
        universe=universe,
        master=_artifact({"market_daily_core": market}, "market_daily_core", "security_master")[0],
        listings=[
            r for p in parent.files("dwd_listing") for r in pq.ParquetFile(p).read().to_pylist()
        ],
        venues=[
            r
            for p in parent.files("dwd_trading_venue")
            for r in pq.ParquetFile(p).read().to_pylist()
        ],
        output=output,
        source_sha256=_artifact(
            {"market_daily_core": market}, "market_daily_core", "security_master"
        )[1].sha256,
    )
    calendar = _artifact({"market_daily_core": market}, "market_daily_core", "trading_calendar")
    with _connection(budget) as connection:
        _views(connection, parents, overlay)
        grades = {}
        if body["day_kind"] == "market":
            projection = member(root, "projection")
            verified = verify_projection(projection)
            core = load(member(projection, "members/core/manifest.json"))
            if (
                core["market_date"] != body["market_date"]
                or core["base"] != body["base"]
                or core["acquisition_universe"] != universe
                or {k: v["manifest_sha256"] for k, v in core["selected_releases"].items()}
                != {k: v["manifest_sha256"] for k, v in selected.items()}
                or set(verified["source_grades"]) - {"R0", "SUSPENDED"}
            ):
                raise BoundedError("DAILY_DELIVERY_SOURCE_OR_NEW_QUALITY_HELD_CHANGED")
            actual_refs = {
                p.parent.name: p
                for p in (projection / "members/core/reference").glob("*/*.parquet")
            }
            if set(actual_refs) != set(overlay):
                raise BoundedError("DAILY_DELIVERY_REFERENCE_SET_CHANGED")
            for table, path in overlay.items():
                compare_tables([path], [actual_refs[table]], connection)
            path, artifact = _artifact(selected, "market_daily_core", "daily_ohlcv_none_delta")
            projected, held = _project_daily_tables(
                connection,
                selected=selected,
                work=output,
                market_date=day,
                covered_after=date.fromisoformat(core["covered_after_date"]),
                catalog_digest=core["catalog_digest"],
                bar_sources=[(path, artifact, "1d", "INTERVAL 0 SECOND")],
                check_identity=True,
            )
            if held:
                raise BoundedError("DAILY_DELIVERY_NEW_QUALITY_HELD")
            for table, path in projected.items():
                original = sorted(
                    (projection / "members/core").glob(
                        "payload/*/" + table + "/market_date=*/*.parquet"
                    )
                )
                compare_tables([path], original, connection)
            tables = {
                t: [member(projection, p) for p in paths]
                for t, paths in verified["table_paths"].items()
            }
            grades = {
                r["stock_code"]: r
                for r in map(
                    json.loads,
                    member(projection, "members/native5m/quality-status.jsonl")
                    .read_text()
                    .splitlines(),
                )
            }
            dispositions = _audit_non_output_daily_artifacts(selected, parquet=pq)
        else:
            events = {"calendar_daily_events": selected["calendar_daily_events"]}
            path, artifact = _artifact(events, "calendar_daily_events", "announcements_delta")
            disclosures, subjects, held = _project_announcements(
                connection,
                path=path,
                artifact=artifact,
                work=output,
                after=day - timedelta(days=1),
                through=day,
                check_identity=True,
            )
            if held:
                raise BoundedError("DAILY_DELIVERY_NEW_CLOSED_QUALITY_HELD")
            cal = _project_trading_calendar(
                connection,
                path=calendar[0],
                artifact=calendar[1],
                work=output,
                market_date=day,
                record_filename=calendar[1].relative_path.name,
                require_observed=True,
            )
            tables = {
                "dwd_disclosure": [disclosures],
                "dwd_disclosure_subject": [subjects],
                "dwd_trading_calendar": [cal],
            }
            dispositions = _audit_non_output_daily_artifacts(events, parquet=pq, closed_day=True)
        cal = tables["dwd_trading_calendar"]
        states = connection.execute("SELECT venue_id,is_open FROM " + relation(cal)).fetchall()
        if len(states) < 2 or any(
            bool(opened) != (body["day_kind"] == "market") for _, opened in states
        ):
            raise BoundedError("DAILY_DELIVERY_OBSERVED_CALENDAR_CONFLICT")
    return Replay(
        tables,
        overlay,
        universe if body["day_kind"] == "market" else None,
        master if body["day_kind"] == "market" else [],
        grades,
        calendar,
        {k: v["manifest_sha256"] for k, v in selected.items()},
        dispositions,
    )


def verify(root: Path, *, parent: NativeBase23, budget: Budget, output: Path) -> Replay:
    from .base_build_period import compare_tables

    body = inspect(root)
    replay = _replay(root, body, parent, budget, output)
    with _connection(budget) as connection:
        if set(replay.references) != set(body["reference_paths"]):
            raise BoundedError("DAILY_DELIVERY_REFERENCE_SET_CHANGED")
        for table, paths in replay.tables.items():
            compare_tables(paths, [member(root, p) for p in body["table_paths"][table]], connection)
        for table, path in replay.references.items():
            compare_tables([path], [member(root, body["reference_paths"][table])], connection)
    return replay


def publish(
    *,
    projection: Path | None,
    sources: dict[str, Path],
    config: Path,
    base_root: Path,
    output: Path,
    code_revision: str,
    budget: Budget,
) -> dict[str, Any]:
    from .daily_core_domain import _verify_base

    parent, _ = _verify_base(base_root, config)
    if not isinstance(parent, NativeBase23):
        raise BoundedError("DAILY_DELIVERY_NATIVE_PARENT_REQUIRED")
    import re

    if not re.fullmatch(r"[0-9a-f]{40}", code_revision):
        raise BoundedError("DAILY_DELIVERY_PINNED_REVISION_REQUIRED")
    kind = "market" if projection is not None else "closed"
    if set(sources) != (
        {"market_daily_core", "calendar_daily_events"}
        if projection is not None
        else {"calendar_reference", "calendar_daily_events"}
    ):
        raise BoundedError("DAILY_DELIVERY_SOURCE_SET_CHANGED")
    releases = {k: OdsRelease.load(member(p, "release.json")) for k, p in sources.items()}
    day = releases["calendar_daily_events"].market_date.isoformat()
    if day <= parent.binding["business_through"] or (
        projection is not None and releases["market_daily_core"].market_date.isoformat() != day
    ):
        raise BoundedError("DAILY_DELIVERY_DATE_OR_PARENT_CHANGED")
    request = {
        "base": dict(parent.binding),
        "config": file_sha(config),
        "code": code_revision,
        "projection": file_sha(projection / "manifest.json") if projection else None,
        "sources": {k: file_sha(p / "release.json") for k, p in sources.items()},
    }
    output = disjoint_output(
        output, [base_root, config, *sources.values(), *([projection] if projection else [])]
    )
    if output.exists():
        body = inspect(output)
        if body["request_identity"] != identity(request):
            raise BoundedError("DAILY_DELIVERY_EXISTING_IDENTITY_CHANGED")
        verify(output, parent=parent, budget=budget, output=budget.root / "reuse-replay")
        return body
    stage = budget.destination(budget.root / "delivery-staging")
    stage.mkdir()
    try:
        copy_file(config, stage / "config.json", budget)
        copy_file(POLICY, stage / "contract.json", budget)
        if projection:
            for path in sorted(_tree(projection)):
                copy_file(member(projection, path), member(stage, "projection/" + path), budget)
        records = {}
        for role, source in sources.items():
            release = releases[role]
            prefix = "sources/" + role
            for name in ["release.json", *(a.relative_path.as_posix() for a in release.artifacts)]:
                copy_file(member(source, name), member(stage, prefix + "/" + name), budget)
            if (source / "READY").exists():
                if (source / "READY").read_text().strip() != file_sha(source / "release.json"):
                    raise BoundedError("DAILY_DELIVERY_ORIGINAL_SOURCE_READY_CHANGED")
                copy_file(source / "READY", stage / prefix / "READY", budget)
            records[role] = {
                "path": prefix + "/release.json",
                "release_id": release.release_id,
                "manifest_sha256": file_sha(source / "release.json"),
                "market_date": release.market_date.isoformat(),
                "original_created_at": release.created_at.isoformat(),
            }
        body = {
            "contract": CONTRACT,
            "day_kind": kind,
            "market_date": day,
            "base": dict(parent.binding),
            "projection": "projection" if projection else None,
            "sources": records,
            "table_paths": {},
            "reference_paths": {},
            "request_identity": identity(request),
            "code_revision": code_revision,
            "policy_sha256": file_sha(POLICY),
        }
        replay = _replay(stage, body, parent, budget, budget.root / "publish-replay")
        for table, paths in replay.tables.items():
            body["table_paths"][table] = []
            for index, path in enumerate(paths):
                if stage in path.parents:
                    relative = path.relative_to(stage).as_posix()
                else:
                    relative = "canonical/" + table + f"/part-{index:06d}.parquet"
                    copy_file(path, member(stage, relative), budget)
                body["table_paths"][table].append(relative)
        for table, path in replay.references.items():
            relative = "reference/" + table + "/part.parquet"
            copy_file(path, member(stage, relative), budget)
            body["reference_paths"][table] = relative
        save(stage / "DELIVERY.json", body)
        content = {
            "role": "daily",
            "market_date": day,
            "day_kind": kind,
            "files": [
                {
                    "path": p,
                    "sha256": file_sha(member(stage, p)),
                    "size_bytes": member(stage, p).stat().st_size,
                }
                for p in sorted(_tree(stage))
            ],
        }
        save(
            stage / "manifest.json",
            {
                "kind": GENERIC_KINDS["daily"],
                "source_contract": CONTRACT,
                "content": content,
                "content_digest": identity(content),
                "built_at": datetime.now(timezone.utc).isoformat(),
                "consumer_admission": "NOT_GRANTED",
                "execution_authorized": False,
            },
        )
        inspect(stage, sealed=False)
        (stage / "READY").write_text(file_sha(stage / "manifest.json") + "\n")
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() or stage.stat().st_dev != output.parent.stat().st_dev:
            raise BoundedError("DAILY_DELIVERY_ATOMIC_PUBLICATION_REQUIRED")
        stage.rename(output)
        return body
    except BaseException as exc:
        if stage.exists():
            (stage / "READY").unlink(missing_ok=True)
        save(
            budget.root / "FAILURE.json",
            {
                "contract": CONTRACT,
                "reason_code": type(exc).__name__,
                "request_identity": identity(request),
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "status": "FAILED_PRESERVED",
            },
        )
        raise


def main() -> None:
    import argparse

    from .daily_core_domain import _verify_base
    from .native_base_standard_reader import governance_config

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    parent = commands.add_parser("parent-config", help="Bind Daily to a sealed standard Base")
    parent.add_argument("--base", type=Path, required=True)
    parent.add_argument("--build-id", required=True)
    parent.add_argument("--output", type=Path, required=True)
    publisher = commands.add_parser("publish", help="Publish one self-contained calendar day")
    publisher.add_argument("--config", type=Path, required=True)
    publisher.add_argument("--events", type=Path, required=True)
    publisher.add_argument("--projection", type=Path)
    sources = publisher.add_mutually_exclusive_group(required=True)
    sources.add_argument("--market-source", type=Path)
    sources.add_argument("--reference-source", type=Path)
    publisher.add_argument("--code-revision", required=True)
    verifier = commands.add_parser("verify", help="Freshly replay a delivered calendar day")
    verifier.add_argument("--delivery", type=Path, required=True)
    for command in (publisher, verifier):
        command.add_argument("--base", type=Path, required=True)
        command.add_argument("--work-root", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--limits", type=Path)
    args = parser.parse_args()
    if args.action == "parent-config":
        save(args.output, governance_config(args.base, build_id=args.build_id))
        print(json.dumps({"config": str(args.output)}))
        return
    limits = Limits(**load(args.limits)) if args.limits else Limits()
    if args.action == "publish":
        if (args.projection is None) != (args.market_source is None):
            parser.error("--projection and --market-source must be supplied together")
        source_roots = {
            "calendar_daily_events": args.events,
            "market_daily_core" if args.market_source else "calendar_reference": args.market_source
            or args.reference_source,
        }
        with Budget(
            args.work_root,
            [
                args.base,
                args.config,
                *source_roots.values(),
                *([args.projection] if args.projection else []),
            ],
            limits,
        ) as budget:
            body = publish(
                projection=args.projection,
                sources=source_roots,
                config=args.config,
                base_root=args.base,
                output=args.output,
                code_revision=args.code_revision,
                budget=budget,
            )
        print(json.dumps({"delivery": str(args.output), "day": body["market_date"]}))
    else:
        base, _ = _verify_base(args.base, args.delivery / "config.json")
        with Budget(args.work_root, [args.base, args.delivery], limits) as budget:
            replay = verify(args.delivery, parent=base, budget=budget, output=args.output)
        print(json.dumps({"status": "SOURCE_REPLAY_PASS", "tables": sorted(replay.tables)}))


if __name__ == "__main__":
    main()
