#!/usr/bin/env python3
"""Freeze and recount independent confirmation capacity; metadata only.

Private exact input paths belong in the run spec. No source hunt, acquisition,
fitting, label generation, raw price projection or capital engine is invoked.
"""

from __future__ import annotations

import argparse
import json
import resource
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.expert_strategy.held_structure_integrity import execution_pins, file_sha
from quanthus.stage_learning.entry_baseline import alias_exclusion, recover_baseline
from quanthus.stage_learning.entry_consolidation import EntryOpportunity
from quanthus.stage_learning.entry_qualification import (
    ListingScope,
    qualification_records,
    qualification_summary,
    qualify_population,
)
from quanthus.stage_learning.head2_revision import FEATURE_NAMES
from quanthus.stage_learning.local_head import HeadBindings, load_checkpoint
from quanthus.structure_v2_inputs import canonical, digest

ROOT = Path(__file__).resolve().parents[1]
PURPOSE = "config/research/learning-mvp/entry-confirmation-activation.v1.json"


def read(path: str | Path) -> Any:
    return json.loads(Path(path).read_bytes())


def write(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def verify(pins: dict[str, dict[str, Any]]) -> None:
    for name, pin in pins.items():
        p = Path(name)
        if (p.stat().st_size != pin["bytes"]
                or file_sha(p) != pin["sha256"].removeprefix("sha256:")):
            raise ValueError("ENTRY_CONFIRMATION_INPUT_CHANGED:" + name)


def codes() -> dict[str, str]:
    result = execution_pins(ROOT)
    for name in ("entry_confirmation_qualification.py", "entry_consolidation_diagnostic.py"):
        path = ROOT / "scripts" / name
        result[str(path.relative_to(ROOT))] = file_sha(path)
    return result


def guard(job: Path) -> None:
    b = read(job / "evidence/budget.frozen.json")
    deadline = datetime.fromisoformat(b["UTC"]) + timedelta(seconds=b["maximum_seconds"])
    if datetime.now(timezone.utc) >= deadline:
        raise ValueError("ENTRY_CONFIRMATION_DEADLINE")
    if shutil.disk_usage(job).free < b["minimum_free_bytes"]:
        raise ValueError("ENTRY_CONFIRMATION_DISK_FLOOR")
    if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 > b["RSS_bytes"]:
        raise ValueError("ENTRY_CONFIRMATION_RSS")
    accounting = Path(b.get("accounting_root", job))
    if sum(p.stat().st_size for p in accounting.rglob("*") if p.is_file()) > b["system_new_bytes"]:
        raise ValueError("ENTRY_CONFIRMATION_WRITE_BUDGET")


def freeze(job: Path, spec_path: Path) -> dict[str, Any]:
    guard(job)
    if (job / "inputs.frozen.private.json").exists():
        raise ValueError("ENTRY_CONFIRMATION_FREEZE_EXISTS")
    spec = read(spec_path)
    verify(spec["pins"])
    prior = read(spec["prior_inputs"])
    source = prior["source"]
    pins = dict(spec["pins"])
    # These are the complete prior *metadata* closure, not market-payload pins.
    pins.update(prior["spec"]["pins"])
    for key in ("security_code_validity", "trading_calendar"):
        path = source["selected"][key]
        pins[path] = source["source_pins"][path]
    for path in (spec_path, job / "evidence/budget.frozen.json"):
        pins[str(path)] = {"bytes": path.stat().st_size, "sha256": file_sha(path)}
    verify(pins)
    frozen = {"kind": "entry-confirmation-metadata-freeze-v1", "spec": spec,
              "pins": pins, "code": codes(), "source": source,
              "UTC": datetime.now(timezone.utc).isoformat()}
    write(job / "inputs.frozen.private.json", frozen)
    return {"kind": frozen["kind"], "input_files": len(pins), "new_market_rows_read": 0,
            "frozen_sha256": file_sha(job / "inputs.frozen.private.json")}


def _event(row: dict[str, Any]) -> EntryOpportunity:
    return EntryOpportunity(**{**row, "signal_date": date.fromisoformat(row["signal_date"])})


def run(job: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    guard(job)
    if (job / "evidence/qualification.safe.json").exists():
        raise ValueError("ENTRY_CONFIRMATION_OUTPUT_EXISTS")
    frozen = read(job / "inputs.frozen.private.json")
    verify(frozen["pins"])
    if frozen["code"] != codes():
        raise ValueError("ENTRY_CONFIRMATION_CODE_CHANGED")
    for name, module in tuple(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if ((name == "quanthus" or name.startswith("quanthus.")) and path
                and not Path(path).resolve().is_relative_to(ROOT / "src/quanthus")):
            raise ValueError("ENTRY_CONFIRMATION_IMPORTED_ORIGIN")
    spec = frozen["spec"]
    prior = read(spec["prior_inputs"])
    old_spec, source = prior["spec"], frozen["source"]
    purpose = read(ROOT / PURPOSE)
    raw = Path(old_spec["model"]).read_bytes()
    value = json.loads(raw)
    approved_sha = purpose["fixed_candidate_sha256"]
    model = load_checkpoint(raw, expected_sha256="sha256:" + approved_sha,
                            bindings=HeadBindings(**value["bindings"]))
    if not model.complete or model.policy.feature_names != FEATURE_NAMES:
        raise ValueError("ENTRY_CONFIRMATION_CANDIDATE")
    last_training = max(date.fromisoformat(u.anchor) for u in model.uses)
    r2, old = read(old_spec["exposure_r2"]), read(old_spec["old_metadata_index"])
    diagnostic_ids = set(r2["preserved_security_ids"])
    diagnostic_codes: set[str] = set()
    for pin in old["metadata_bindings"]:
        path = Path(old_spec["old_metadata_root"]) / pin["artifact"] / pin["name"]
        if str(path) not in frozen["pins"]:
            raise ValueError("ENTRY_CONFIRMATION_UNPINNED_OLD_METADATA")
        v = read(path)
        diagnostic_ids.update(v.get("evaluation_pool", {}))
        diagnostic_codes.update(str(c) for c in v.get("evaluation_pool", {}).values())
    for path in old_spec["recent_selections"]:
        for row in read(path)["selected"]:
            diagnostic_ids.add(row["security_id"])
            diagnostic_codes.add(row["security_code"])
    for row in old["rows"]:
        diagnostic_ids.add(row["security_id"])
        diagnostic_codes.add(row["code"])
    securities = pq.read_table(old_spec["identity_security"],
                               columns=["security_id", "company_id"]).to_pylist()
    listings = pq.read_table(old_spec["identity_listing"],
                            columns=["security_id", "listing_code"]).to_pylist()
    identities, diagnostic_blocked = alias_exclusion(
        listings, securities, diagnostic_ids, diagnostic_codes)
    all_ids = diagnostic_ids | set(r2["security_ids"])
    all_codes = diagnostic_codes | set(r2["codes"])
    proposed = read(spec["stage34_selection"])["proposed"]
    for row in proposed:
        all_ids.add(row["security_id"])
        all_codes.add(row["code"])
    used = tuple(_event(row) for row in read(spec["used_selection"]))
    all_codes.update(e.security_code for e in used)
    _, all_blocked = alias_exclusion(listings, securities, all_ids, all_codes)
    runtime = read(old_spec["runtime_request"])
    prep = read(old_spec["preparation"])
    if (runtime["source_manifest_sha256"] != source["source_manifest_sha256"]
            or prep["effective_source_manifest_sha256"] != runtime["source_manifest_sha256"]
            or prep["source_information_cutoff"] != source["source_cutoff"]):
        raise ValueError("ENTRY_CONFIRMATION_SOURCE_CLOSURE")
    original = recover_baseline(
        prep, read(old_spec["decisions"]),
        load_candidate_rule_card(
            ROOT / "config/p4-candidate-price-volume-transition.research.v1.json"),
        runtime_snapshot_id="p3-restructuring-disposition-successor-v1/"
        + digest(runtime).removeprefix("sha256:"),
    )
    calendars: dict[str, list[date]] = {}
    for row in pq.read_table(source["selected"]["trading_calendar"],
                             columns=["trade_date", "exchange", "is_open"]).to_pylist():
        d = row["trade_date"]
        if row["is_open"] == 1 and date(2012, 1, 1) <= d <= date(2021, 12, 31):
            calendars.setdefault(row["exchange"], []).append(d)
    for calendar in calendars.values():
        calendar.sort()
    scopes: dict[str, ListingScope] = {}
    for row in pq.read_table(source["selected"]["security_code_validity"]).to_pylist():
        if row["stock_code"] in scopes:
            raise ValueError("ENTRY_CONFIRMATION_DUPLICATE_VALIDITY")
        scopes[row["stock_code"]] = ListingScope(
            row["exchange"], date.fromisoformat(str(row["valid_from"])),
            date.fromisoformat(str(row["valid_to"])) if row.get("valid_to") else None,
        )
    rows = qualify_population(
        original, model_last_observation=last_training, identities=identities,
        diagnostic_blocked_aliases=diagnostic_blocked, all_exposed_aliases=all_blocked,
        consumed=used, sessions=calendars, listings=scopes,
    )
    diagnostic = {r.event_id for r in rows if r.diagnostic_disposition == "INCLUDE"}
    summary = qualification_summary(rows)
    receipt = read(spec["prior_selection_receipt"])
    if (diagnostic != {e.event_id for e in used}
            or summary["diagnostic_recount"] != receipt["selection_counts"]):
        raise ValueError("ENTRY_CONFIRMATION_DIAGNOSTIC_RECOUNT_CHANGED")
    pool = read(spec["inventory"])
    native_securities = {(r["security_id"], r["company_id"]) for r in securities}
    native_listings = {(r["security_id"], r["listing_code"]) for r in listings}
    for row in pool:
        if ((row["security_id"], row["company_id"]) not in native_securities
                or (row["security_id"], row["code"]) not in native_listings):
            raise ValueError("ENTRY_CONFIRMATION_INVENTORY_NATIVE_LINK")
    available = [r for r in pool if r["code"] not in all_blocked
                 and identities.get(r["code"]) == r["company_id"]]
    pool_codes = {r["code"] for r in pool}
    summary.update({
        "kind": "entry-independent-confirmation-qualification-v1",
        "UTC": datetime.now(timezone.utc).isoformat(),
        "model_last_observation": last_training.isoformat(),
        "frozen_input_sha256": file_sha(job / "inputs.frozen.private.json"),
        "original_stream_candidate_issuer_codes": len({e.security_code for e in original}),
        "consumed_events": len(used), "consumed_issuers": len({e.issuer_id for e in used}),
        "used_events_without_next_calendar_session": sum(
            r.next_open_session is None for r in rows if r.event_id in diagnostic),
        "terminal_used_events_now_with20211231_execution_coordinate": sum(
            r.next_open_session == date(2021, 12, 31) for r in rows if r.event_id in diagnostic),
        "execution_price_and_action_closure": "NOT_CHECKED_NO_INDEPENDENT_COHORT",
        "inventory": {
            "identities": len(pool), "stage34_used_issuers": len(proposed),
            "without_known_exposure_by_metadata_only": len(available),
            "post_training_original_B_codes_in_inventory": len({
                e.security_code for e in original if e.signal_date > last_training} & pool_codes),
            "unused_inventory_is_not_source_reference_or_B_opportunity_qualification": True,
            "unexposed_stock_existence_outside_this_stream": "NOT_RULED_OUT",
        },
        "source_market_payload_revalidation": "NOT_RUN_NOT_NEEDED_FOR_METADATA_STOP",
        "inputs_unchanged_after": True,
    })
    write(job / "qualification.private.json", qualification_records(rows))
    verify(frozen["pins"])
    if frozen["code"] != codes():
        raise ValueError("ENTRY_CONFIRMATION_CODE_CHANGED_AFTER")
    guard(job)
    write(job / "evidence/qualification.safe.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["freeze", "run"])
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--spec", type=Path)
    args = parser.parse_args()
    if args.job.resolve(strict=True) != args.job:
        parser.error("job must be a physical absolute directory")
    if args.command == "freeze":
        if args.spec is None:
            parser.error("freeze requires --spec")
        result = freeze(args.job, args.spec)
    else:
        result = run(args.job)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
