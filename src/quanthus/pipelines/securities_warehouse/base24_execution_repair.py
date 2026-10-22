"""Disconnected acceptance harness: production adapters, exact pinned inputs, no network."""

import datetime
import json
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import duckdb
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from quanthus.pipelines.securities_warehouse import base24_p6_purpose as p
from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_sources import _tree, physical_path
from quanthus.pipelines.securities_warehouse.base24_execution_bounds import repair_stale_bounds
from quanthus.pipelines.securities_warehouse.base24_execution_evidence import verify_evidence
from quanthus.pipelines.securities_warehouse.base24_single_observation import verify_single_day

from .base22 import _decode, schemas
from .base24_execution_evidence import _require
from .base24_status_evidence import _instant

VERSION = "p10-execution-repair-offline.v1"


def validate_context(
    row: dict[str, Any], cutoff: datetime.datetime, window_proof: dict[str, Any] | None = None
) -> None:
    """A proof's own clock cannot supersede its consumer's actual cutoff."""
    known = [row.get("bar_known"), row.get("state_known")]
    for name in ("statuses", "lifecycle", "regimes", "dated_status"):
        known.extend(item.get("known") for item in row.get(name) or [])
    _require(
        all(t is not None and t.tzinfo is not None and (t <= cutoff) for t in known),
        "CONTEXT_KNOWLEDGE_AFTER_CUTOFF",
    )
    if window_proof is not None:
        _require(
            window_proof["knowledge_cutoff"] <= cutoff and window_proof["rule_known_at"] <= cutoff,
            "WINDOW_AFTER_CONSUMER_CUTOFF",
        )


def native_rows(path: Path, expected: Any) -> list[dict[str, Any]]:
    table = pq.read_table(path)
    _require(
        set(table.column_names) == set(expected.names)
        and all(table.schema.field(f.name).type == f.type for f in expected),
        "NATIVE_FULL_SCHEMA",
    )
    rows = table.to_pylist()
    _require(
        all(r[f.name] is not None for r in rows for f in expected if not f.nullable),
        "NATIVE_REQUIRED_VALUE",
    )
    return cast(list[dict[str, Any]], rows)


def validate_native(
    row: dict[str, Any],
    listings: list[dict[str, Any]],
    calendars: list[dict[str, Any]],
    cutoff: datetime.datetime,
) -> None:
    """Model-specific day-state closure, including actual knowledge intervals."""

    def visible(r: dict[str, Any]) -> bool:
        return (
            r.get("knowledge_from") is not None
            and r["knowledge_from"] <= cutoff
            and (r.get("knowledge_to") is None or cutoff < r["knowledge_to"])
        )

    _require(visible(row), "NATIVE_NOT_VISIBLE_AT_CUTOFF")
    (start, end) = (row["observation_start"], row["observation_end"])
    _require(start < end, "NATIVE_OBSERVATION_INTERVAL")
    ls = [
        r
        for r in listings
        if r["listing_id"] == row["listing_id"]
        and visible(r)
        and (r["effective_from"] <= start)
        and (r["effective_to"] is None or start < r["effective_to"])
    ]
    cs = [
        r
        for r in calendars
        if r["calendar_session_id"] == row["calendar_session_id"] and visible(r)
    ]
    _require(len(ls) == len(cs) == 1, "NATIVE_REFERENCE_CARDINALITY")
    _require(ls[0]["venue_id"] == cs[0]["venue_id"], "NATIVE_CALENDAR_VENUE")
    _require(
        cs[0]["trade_date"] == start.astimezone(ZoneInfo("Asia/Shanghai")).date(),
        "NATIVE_CALENDAR_BUSINESS_DATE",
    )


def validate_payload(root: Path) -> None:
    """Strict checks over embedded evidence only; never opens historical roots."""
    repo = Path(__file__).resolve().parents[4]
    model = json.loads((repo / "config/pipelines/dwd-domain-model.v1.json").read_bytes())
    extension = json.loads(
        (repo / "config/pipelines/dwd-domain-model.v1.1-extension.json").read_bytes()
    )
    shape = schemas(model, extension)
    plan = json.loads((root / "plan.json").read_bytes())
    cutoff = _instant(plan["context_cutoff"])
    _require(
        plan.get("original_snapshot_unchanged") is True
        and plan.get("historical_pit_granted") is False
        and (plan.get("consumer_admission_granted") is False),
        "REPAIR_PURPOSE",
    )
    listings = native_rows(root / "single-day/dwd_listing.parquet", shape["dwd_listing"])
    calendars = native_rows(
        root / "single-day/dwd_trading_calendar.parquet", shape["dwd_trading_calendar"]
    )
    states = native_rows(
        root / "bounds/dwd_tradability_state.parquet", shape["dwd_tradability_state"]
    )
    _require(
        len(states) == len({(r["listing_id"], r["calendar_session_id"]) for r in states}),
        "NATIVE_DUPLICATE_KEY",
    )
    for row in states:
        validate_native(row, listings, calendars, cutoff)
        name = row["source_record_id"]
        _require(Path(name).name == name, "LOCAL_BOUND_PROOF_PATH")
        proof = json.loads((root / "bounds" / name).read_bytes())
        before = _decode(json.dumps(proof["original_native_state"]), shape["dwd_tradability_state"])
        allowed = {
            "upper_price",
            "lower_price",
            "knowledge_from",
            "knowledge_to",
            "record_source",
            "source_revision",
            "source_record_id",
            "source_artifact_sha256",
            "input_revision_digest",
        }
        _require(
            all(row[k] == before[k] for k in before if k not in allowed), "NATIVE_UNALLOWED_CHANGE"
        )
        _require(
            row["record_source"] == row["source_revision"] == proof["contract_id"]
            and row["knowledge_to"] is None
            and (row["source_artifact_sha256"] == file_sha(root / "bounds" / name))
            and (row["input_revision_digest"] is not None),
            "NATIVE_REPAIR_PROVENANCE",
        )
        _require(
            _instant(proof["authority_known_at"]) <= _instant(proof["repair_known_at"]) <= cutoff,
            "REPAIR_AFTER_CUTOFF",
        )
    for table in ("dwd_security_bar", "dwd_tradability_state"):
        for row in native_rows(root / "single-day" / (table + ".parquet"), shape[table]):
            validate_native(row, listings, calendars, cutoff)
    windows = windows_from_output(root / "authority")
    for name in ("before-124.private.parquet", "single-evaluation.private.parquet"):
        for context in pq.read_table(root / name).to_pylist():
            row = context["row"]
            window = windows.get((row["listing_code"], row["market_date"]))
            validate_context(row, cutoff, window)
    authority = json.loads((root / "authority/INPUTS.json").read_bytes())
    _require(_instant(authority["repair_known_at"]) <= cutoff, "AUTHORITY_AFTER_CUTOFF")
    single = json.loads((root / "single-day/plan.json").read_bytes())
    _require(_instant(single["repair_known_at"]) <= cutoff, "SINGLE_REPAIR_AFTER_CUTOFF")


def windows_from_output(root: Path) -> dict[tuple[str, datetime.date], dict[str, Any]]:
    data = json.loads((root / "OUTPUTS.json").read_text())
    windows = {}
    for w in data["ipo_exclusions"]:
        w["market_date"] = datetime.date.fromisoformat(w["market_date"])
        for k in ["rule_known_at", "knowledge_cutoff"]:
            w[k] = datetime.datetime.fromisoformat(w[k])
        for k in ["effective_from", "effective_to"]:
            if w["listing"][k] is not None:
                w["listing"][k] = datetime.date.fromisoformat(w["listing"][k])
        w["listing"]["knowledge_from"] = datetime.datetime.fromisoformat(
            w["listing"]["knowledge_from"]
        ).astimezone(ZoneInfo("Asia/Shanghai"))
        for r in w["sessions"]:
            r["day"] = datetime.date.fromisoformat(r["day"])
            r["knowledge_from"] = datetime.datetime.fromisoformat(r["knowledge_from"])
        windows[w["code"], w["market_date"]] = w
    return windows


def reconstruct(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    plan = json.loads((root / "plan.json").read_text())
    validate_payload(root)
    verify_evidence(root / "authority", plan["authority_manifest"])
    verify_single_day(root / "single-day", plan["single_manifest"])
    windows = windows_from_output(root / "authority")
    policy = json.loads((root / "price-limit.v2.json").read_text())
    raw_sha = file_sha(root / "ipo-rules.parquet")
    evaluations = pq.read_table(root / "before-124.private.parquet").to_pylist()
    native = pq.read_table(root / "bounds/dwd_tradability_state.parquet").to_pylist()
    records = []
    keys = []
    fixed = 0
    digest_representation_changes = 0
    for x in evaluations:
        row = x["row"]
        key = (row["listing_code"], row["market_date"])
        ipo = [r for r in row["regimes"] if r["ipo"] is not None]
        wp = windows.get(key) if not ipo else None
        args: dict[str, Any] = dict(
            prior=x["prior"],
            ordinal=x["ordinal"],
            complete=x["complete"],
            policy=policy,
            raw_sha=raw_sha,
            window_proof=wp,
        )
        initial = p._state_row(row, **args)
        _require(
            all(initial[k] == x["result"][k] for k in initial if k != "evidence_digest"),
            "CACHED_BUSINESS_RESULT_CHANGED",
        )
        digest_representation_changes += (
            initial["evidence_digest"] != x["result"]["evidence_digest"]
        )
        if not initial["execution_source_qualified"]:
            proofpath = root / "bounds" / (key[0] + "-" + key[1].isoformat() + ".private.json")
            old = json.loads(proofpath.read_text())
            proof = repair_stale_bounds(
                row,
                **args,
                authority_sha=old["authority_sha256"],
                authority_known=datetime.datetime.fromisoformat(old["authority_known_at"]),
                repair_at=datetime.datetime.fromisoformat(old["repair_known_at"]),
            )
            _require(proof["identity"] == old["identity"], "REBUILT_BOUND_PROOF")
            matches = [
                n
                for n in native
                if n["listing_id"] == row["listing_id"]
                and n["observation_start"].astimezone(ZoneInfo("Asia/Shanghai")).date()
                == row["market_date"]
            ]
            _require(len(matches) == 1, "REPAIR_RECONSTRUCTION")
            n = matches[0]
            _require(
                (n["upper_price"], n["lower_price"], n["knowledge_from"])
                == (
                    proof["replacement_row"]["upper_price"],
                    proof["replacement_row"]["lower_price"],
                    proof["repair_known_at"],
                ),
                "REPAIR_RECONSTRUCTION",
            )
            _require(n["source_artifact_sha256"] == file_sha(proofpath), "REPAIR_RECONSTRUCTION")
            for name in ["is_tradable", "can_buy", "can_sell"]:
                _require(n[name] == row[name], "REPAIR_RECONSTRUCTION")
            result = proof["replacement_result"]
            fixed += 1
        else:
            result = initial
        _require(
            result["execution_source_qualified"] and (not result["execution_authorized"]),
            "REPAIR_RECONSTRUCTION",
        )
        records.append(result)
        keys.append([key[0], key[1].isoformat()])
    _require(fixed == len(native) == 10, "REPAIR_RECONSTRUCTION")
    x = pq.read_table(root / "single-evaluation.private.parquet").to_pylist()[0]
    row = x["row"]
    bar = pq.read_table(root / "single-day/dwd_security_bar.parquet").to_pylist()[0]
    state = pq.read_table(root / "single-day/dwd_tradability_state.parquet").to_pylist()[0]
    for k in ["open", "high", "low", "close", "volume"]:
        _require(row[k] == bar[k], "REPAIR_RECONSTRUCTION")
    _require(
        row["amount"] == bar["turnover"] and row["bar_known"] == bar["knowledge_from"],
        "REPAIR_RECONSTRUCTION",
    )
    for k in ["is_tradable", "can_buy", "can_sell"]:
        _require(row[k] == state[k], "REPAIR_RECONSTRUCTION")
    _require(
        row["state_known"] == state["knowledge_from"] and row["listing_id"] == state["listing_id"],
        "REPAIR_RECONSTRUCTION",
    )
    inventory = json.loads((root / "support/inventory.json").read_text())
    ods = json.loads((root / "authority/ods-manifest.json").read_text())["content"]["files"]
    for entry in inventory["selected_existing_members"]:
        _require(
            entry in ods
            and file_sha(root / "support" / Path(entry["path"]).name) == entry["sha256"],
            "REPAIR_RECONSTRUCTION",
        )
    c = duckdb.connect()
    c.execute("SET TimeZone='Asia/Shanghai'")
    close = c.execute(
        "SELECT CAST(close AS DECIMAL(38,10)) FROM read_parquet(?) WHERE stock_code='002569' "
        "AND date='2026-07-31'",
        [str(root / "support/daily_ohlcv_none_hotfix.parquet")],
    ).fetchall()
    _require(len(close) == 1 and close[0][0] == x["prior"], "REPAIR_RECONSTRUCTION")
    status = c.execute(
        "SELECT isST,tradestatus,acquired_at FROM read_parquet(?) WHERE stock_code='002569' "
        "AND market_date='2026-07-31'",
        [str(root / "support/daily_status_observation_hotfix.parquet")],
    ).fetchall()
    _require(len(status) == 1 and status[0][1] == "1", "REPAIR_RECONSTRUCTION")
    _require(
        datetime.datetime.fromisoformat(status[0][2])
        <= datetime.datetime.fromisoformat(plan["context_cutoff"]),
        "REPAIR_RECONSTRUCTION",
    )
    c.close()
    result = p._state_row(
        row,
        prior=x["prior"],
        ordinal=x["ordinal"],
        complete=x["complete"],
        policy=policy,
        raw_sha=raw_sha,
    )
    _require(
        all(result[k] == x["result"][k] for k in result if k != "evidence_digest"),
        "REPAIR_RECONSTRUCTION",
    )
    _require(result["execution_source_qualified"], "REPAIR_RECONSTRUCTION")
    records.append(result)
    keys.append(["002569", "2026-08-03"])
    _require(
        sorted(keys) == sorted(plan["target_keys"])
        and len({tuple(k) for k in keys}) == len(records) == 125,
        "REPAIR_RECONSTRUCTION",
    )
    records.sort(key=lambda r: (r["market_date"], r["listing_id"]))
    return (
        records,
        {
            "targets": 125,
            "qualified": 125,
            "bound_revisions": 10,
            "new_bar_rows": 1,
            "new_state_rows": 1,
            "cached_representation_digest_changes": digest_representation_changes,
            "consumer_admission_granted": False,
            "historical_pit_granted": False,
            "execution_authorized": False,
        },
    )


def verify(root: Path, manifest_sha: str, ready: bool = True) -> dict[str, Any]:
    root = physical_path(root)
    _require(file_sha(root / "manifest.json") == manifest_sha, "REPAIR_RECONSTRUCTION")
    manifest = json.loads((root / "manifest.json").read_text())
    _require(
        manifest["purpose"] == "P10-538-additive-source-repair"
        and manifest["consumer_admission_granted"] is False,
        "REPAIR_RECONSTRUCTION",
    )
    _require(
        set(_tree(root))
        == set(manifest["files"]) | {"manifest.json"} | ({"READY"} if ready else set()),
        "REPAIR_RECONSTRUCTION",
    )
    for name, sha in manifest["files"].items():
        _require(file_sha(root / name) == sha, "FILE_HASH:" + name)
    if ready:
        _require((root / "READY").read_text().strip() == manifest_sha, "REPAIR_RECONSTRUCTION")
    plan = json.loads((root / "plan.json").read_text())
    repo = Path(__file__).resolve().parents[4]
    for name, sha in plan["code_files"].items():
        _require(file_sha(repo / name) == sha, "CODE_IDENTITY")
    _require(file_sha(Path(__file__)) == plan["verifier_sha256"], "VERIFIER_IDENTITY")
    (rows, result) = reconstruct(root)
    _require(
        pq.read_table(root / "execution-125.private.parquet").to_pylist() == rows,
        "RECOMPUTED_EXECUTION",
    )
    result["manifest_sha256"] = manifest_sha
    return result


def seal_successor(
    source: Path, output: Path, *, source_sha: str, code_revision: str
) -> dict[str, Any]:
    """Qualify an immutable, bounded P10 handoff without republishing any Base."""
    import re
    import shutil

    from .base22 import canonical
    from .base22_sources import _fingerprint, disjoint_output

    source = physical_path(source)
    output = disjoint_output(output, [source, Path(__file__).resolve().parents[4]])
    _require(not output.exists(), "REPAIR_OUTPUT_EXISTS")
    _require(re.fullmatch("[0-9a-f]{40}", code_revision) is not None, "FULL_CODE_REVISION")
    _require(file_sha(source / "manifest.json") == source_sha, "ORIGINAL_REPAIR_IDENTITY")
    original = json.loads((source / "manifest.json").read_bytes())
    _require(
        set(_tree(source)) == set(original["files"]) | {"manifest.json", "READY"},
        "ORIGINAL_REPAIR_FILE_SET",
    )
    _require((source / "READY").read_text().strip() == source_sha, "ORIGINAL_READY")
    before = {name: _fingerprint(source / name) for name in _tree(source)}
    output.mkdir(parents=True)
    for name, sha in original["files"].items():
        path = source / name
        _require(file_sha(path) == sha, "ORIGINAL_MEMBER_HASH")
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    plan = json.loads((output / "plan.json").read_bytes())
    repo = Path(__file__).resolve().parents[4]
    plan.update(
        offline_contract=VERSION,
        original_repair_manifest=source_sha,
        code_revision=code_revision,
        verifier_sha256=file_sha(Path(__file__)),
        sealed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    for name in (
        "src/quanthus/pipelines/securities_warehouse/base24_execution_repair.py",
        "src/quanthus/pipelines/securities_warehouse/base22.py",
        "src/quanthus/pipelines/securities_warehouse/base22_sources.py",
        "src/quanthus/price_limits.py",
        "config/pipelines/dwd-domain-model.v1.json",
        "config/pipelines/dwd-domain-model.v1.1-extension.json",
    ):
        plan["code_files"][name] = file_sha(repo / name)
    (output / "plan.json").write_bytes(canonical(plan))
    manifest = {
        **original,
        "offline_contract": VERSION,
        "files": {name: file_sha(output / name) for name in sorted(_tree(output))},
    }
    (output / "manifest.json").write_bytes(canonical(manifest))
    result = verify(output, file_sha(output / "manifest.json"), ready=False)
    _require(
        all(_fingerprint(source / name) == stamp for name, stamp in before.items())
        and set(_tree(source)) == set(before),
        "ORIGINAL_REPAIR_CHANGED",
    )
    (output / "READY").write_text(file_sha(output / "manifest.json") + "\n")
    return {**result, "original_source_unchanged": True, "offline_contract": VERSION}
