"""Physical local replay of the two scoped necessary Base 2.3 repair packages.

This domain product is not a whole image. Source roots are archival strings only.
Integration must bind these local inputs, outputs and exclusions into a successor
image and run the complete native audit and two full independent relocations.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition
from .base22_bounded import BoundedError, Budget, Member, semantic_scan
from .base22_sources import _tree
from .base23_action_scope import check_output_geometry
from .base23_calendar_authority import compare_retained_calendar, derive_date_authority
from .base23_low_cost_audit import audit_low_cost_scope
from .base23_required import CONTRACT_ID, qualify_daily_observation, revise_share_units

MODULES = (
    "base23_required.py",
    "base23_required_scope.py",
    "base23_calendar_authority.py",
    "base23_low_cost_audit.py",
    "base23_native_image.py",
    "base22.py",
    "base22_bounded.py",
    "base22_sources.py",
    "base22_financial.py",
    "disclosure_event.py",
    "base23_action_scope.py",
)
CONFIG_SHA = "sha256:d02a7a2457f47597f7ba87fb905e9027f8878c9798d517f006e5d08201ebfdd2"


def code_closure() -> dict[str, str]:
    return {name: file_sha(Path(__file__).with_name(name)) for name in MODULES}


def replay_required(
    root: Path, body: dict[str, Any], budget: Budget
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], dict[str, Any]]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    def read(label: str, table: str, columns: list[str] | None = None) -> list[dict[str, Any]]:
        rows = []
        for item in body["inputs"][label][table]:
            p = _physical(root, item["path"], independent=True)
            Member(p, item["sha256"], item["size_bytes"], item["rows"]).verify()
            for batch in pq.ParquetFile(p).iter_batches(
                batch_size=budget.limits.batch_rows, columns=columns
            ):
                budget.check(extra_rss=batch.nbytes * 2)
                rows.extend(batch.to_pylist())
        return rows

    calendar_scope = root / "evidence/calendar"
    calendar_replay = audit_low_cost_scope(calendar_scope, budget)
    config = json.loads((calendar_scope / "calendar-config.json").read_bytes())
    docs = {v: (calendar_scope / f"official/{v}.html").read_bytes() for v in config["documents"]}
    authority = derive_date_authority(config, docs)
    calendars = read("native", "dwd_trading_calendar")
    venues = read("native", "dwd_trading_venue")
    comparison = compare_retained_calendar(authority, calendars, venues)
    calendar_by_id = {r["calendar_session_id"]: r for r in calendars}
    listings = {r["listing_id"]: r for r in read("native", "dwd_listing")}
    intervals = defaultdict(list)
    for row in read("native", "dwd_listing_status_interval"):
        intervals[row["listing_id"]].append(row)
    # No market price column enters the in-memory projection or safe receipts.
    fields = [
        "listing_id",
        "calendar_session_id",
        "observation_start",
        "observation_end",
        "knowledge_from",
        "knowledge_to",
        "is_tradable",
        "can_buy",
        "can_sell",
        "reason_code",
        "source_revision",
        "source_artifact_sha256",
        "record_source",
        "source_record_id",
        "input_revision_digest",
    ]
    daily = []
    by_day: dict[str, set[str]] = defaultdict(set)
    for row in read("native", "dwd_tradability_state", fields):
        proof = qualify_daily_observation(
            row,
            listings[row["listing_id"]],
            calendar_by_id[row["calendar_session_id"]],
            intervals[row["listing_id"]],
            observed_at=body["observed_at"],
        )
        day, listing_id = proof["trade_date"], proof["listing_id"]
        if listing_id in by_day[day]:
            raise BoundedError("REQUIRED_SELECTED_DAILY_DUPLICATE_LISTING_DATE")
        by_day[day].add(listing_id)
        daily.append(proof)
    expected_days = sorted({r["trade_date"] for r in authority["rows"] if r["is_open"]})
    selected = set(body["selected_listing_ids"])
    if sorted(by_day) != expected_days or any(by_day[d] != selected for d in expected_days):
        raise BoundedError("REQUIRED_SELECTED_DAILY_EXACT_UNIVERSE_OR_DAY_CLOSURE_CHANGED")
    units = root / "evidence/units"
    unit_receipt = audit_low_cost_scope(units, budget)
    u = json.loads((units / "SCOPE.json").read_bytes())
    actions = read("native", "dwd_corporate_action")
    terms = read("native", "dwd_corporate_action_term")
    actions_by_sha = {digest(r): r for r in actions}
    term_groups = defaultdict(list)
    for row in terms:
        term_groups[row["corporate_action_id"], row["effective_at"], row["knowledge_from"]].append(
            row
        )
    donor_actions = {}
    donor_terms = defaultdict(list)
    for table, items in u["outputs"].items():
        for item in items:
            for row in pq.ParquetFile(units / item["path"]).read().to_pylist():
                if table == "dwd_corporate_action":
                    key = row["corporate_action_id"]
                    if key in donor_actions:
                        raise BoundedError("REQUIRED_UNIT_DONOR_DUPLICATE")
                    donor_actions[key] = row
                else:
                    donor_terms[row["corporate_action_id"]].append(row)
            budget.check()
    raw_path = root / "evidence/ods-corporate-actions.parquet"
    raw_manifest_path = root / "evidence/ods-manifest.json"
    manifest = json.loads(raw_manifest_path.read_bytes())
    raw_sha = file_sha(raw_path)
    if not any(
        i["path"] == body["original_raw_path"] and i["sha256"] == raw_sha
        for i in manifest["content"]["files"]
    ):
        raise BoundedError("REQUIRED_ORIGINAL_ODS_MEMBER_NOT_IN_MANIFEST")
    raw_by_key = {}
    for row in pq.ParquetFile(raw_path).read().to_pylist():
        key = row["action_id"]
        if key in raw_by_key:
            raise BoundedError("REQUIRED_ORIGINAL_ODS_RAW_KEY_DUPLICATE")
        raw_by_key[key] = row
    vocabulary_path = root / "evidence/contract/disclosure-event-vocabulary.v1.json"
    vocabulary = json.loads(vocabulary_path.read_bytes())
    outputs: dict[str, list[dict[str, Any]]] = {
        "dwd_corporate_action": [],
        "dwd_corporate_action_term": [],
    }
    accepted, held = [], []
    seen = set()
    for choice in body["unit_repair_selection"]:
        before = actions_by_sha[choice["before_row_sha256"]]
        key = before["corporate_action_id"]
        if key in seen:
            raise BoundedError("REQUIRED_UNIT_SELECTION_DUPLICATE")
        seen.add(key)
        old_terms = term_groups[key, before["effective_at"], before["knowledge_from"]]
        donor = donor_actions[choice["incoming_id"]]
        dt = donor_terms[choice["incoming_id"]]
        raw = raw_by_key[before["source_record_id"]]
        binding = {
            "purpose": CONTRACT_ID,
            "original_manifest_sha256": file_sha(raw_manifest_path),
            "original_member_sha256": raw_sha,
            "original_raw_row_sha256": digest(raw),
            "before_row_sha256": digest(before),
            "before_term_rows_sha256": sorted(digest(t) for t in old_terms),
            "donor_row_sha256": digest(donor),
            "donor_term_rows_sha256": sorted(digest(t) for t in dt),
            "donor_scope_identity": u["identity"],
            "donor_raw_replay_identity": unit_receipt["identity"],
            "vocabulary_identity": digest(vocabulary),
            "vocabulary_sha256": file_sha(vocabulary_path),
        }
        try:
            values, proof = revise_share_units(
                raw,
                before,
                old_terms,
                donor,
                dt,
                vocabulary=vocabulary,
                binding=binding,
                observed_at=body["observed_at"],
            )
        except BoundedError as exc:
            held.append({**choice, "reason": str(exc)})
        else:
            for table, rows in values.items():
                outputs[table].extend(rows)
            accepted.append({**choice, "proof": proof})
    summary = {
        "contract_id": CONTRACT_ID,
        "calendar_authority_identity": authority["identity"],
        "calendar_replay_identity": calendar_replay["identity"],
        "calendar_comparison": comparison,
        "selected_daily_rows": len(daily),
        "selected_listing_count": len(selected),
        "distinct_dates": len(by_day),
        "daily_dispositions": dict(Counter(r["disposition"] for r in daily)),
        "selected_daily_sha256": digest(daily),
        "unit_replay_identity": unit_receipt["identity"],
        "accepted_unit_revisions": accepted,
        "held_unit_revisions": held,
        "output_rows_sha256": digest(outputs),
        "full_market_lifecycle_qualified": False,
        "historical_PIT_recovered": False,
        "canonical_adjusted_price_qualified": False,
        "consumer_admission": "NOT_GRANTED",
        "complete_Base23": False,
    }
    summary["identity"] = digest(summary)
    return outputs, daily, summary


def audit_required_scope(
    root: Path, budget: Budget, *, require_ready: bool = True
) -> dict[str, Any]:
    """Check complete physical closure, then regenerate every projected row."""
    import pyarrow.parquet as pq

    body = json.loads(_physical(root, "SCOPE.json", independent=True).read_bytes())
    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("code_closure") != code_closure()
        or body.get("consumer_admission") != "NOT_GRANTED"
        or body.get("complete_Base23") is not False
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
    ):
        raise BoundedError("REQUIRED_SCOPE_CONTRACT_OR_IDENTITY_CHANGED")
    expected = {"SCOPE.json", *body["physical_members"]}
    if require_ready:
        expected.add("READY")
        if (root / "READY").read_text().strip() != file_sha(root / "SCOPE.json"):
            raise BoundedError("REQUIRED_SCOPE_READY_CHANGED")
    if _tree(root) != expected:
        raise BoundedError("REQUIRED_SCOPE_PHYSICAL_SET_CHANGED")
    check_output_geometry(root, body, expected)
    if file_sha(_physical(root, "evidence/required-contract.json", independent=True)) != CONFIG_SHA:
        raise BoundedError("REQUIRED_SCOPE_EXACT_VERSIONED_CONFIG_CHANGED")
    for rel, item in body["physical_members"].items():
        path = _physical(root, rel, independent=True)
        if file_sha(path) != item["sha256"] or path.stat().st_size != item["size_bytes"]:
            raise BoundedError("REQUIRED_SCOPE_PHYSICAL_HASH_CHANGED")
        budget.check()
    native = json.loads((root / "evidence/native-MATERIALIZATION.json").read_bytes())
    manifest = json.loads((root / "evidence/native-manifest.json").read_bytes())
    if (
        manifest["materialization_sha256"]
        != file_sha(root / "evidence/native-MATERIALIZATION.json")
        or file_sha(root / "evidence/native-manifest.json") != body["native_manifest_sha256"]
        or file_sha(root / "evidence/ods-manifest.json") != body["ods_manifest_sha256"]
    ):
        raise BoundedError("REQUIRED_SCOPE_ORIGINAL_MANIFEST_BINDING_CHANGED")
    native_tables = {
        "dwd_corporate_action",
        "dwd_corporate_action_term",
        "dwd_listing",
        "dwd_listing_status_interval",
        "dwd_trading_calendar",
        "dwd_trading_venue",
        "dwd_tradability_state",
    }
    if (
        set(body["inputs"]) != {"native"}
        or set(body["inputs"]["native"]) != native_tables
        or len(body["selected_listing_ids"]) != len(set(body["selected_listing_ids"]))
    ):
        raise BoundedError("REQUIRED_SCOPE_NATIVE_TABLE_OR_UNIVERSE_SET_CHANGED")
    for table, files in body["inputs"]["native"].items():
        declared = {x["path"]: x for x in native["layout"][table]["files"]}
        selected = set(declared)
        if table == "dwd_tradability_state":
            selected = {
                p
                for p, x in declared.items()
                if "year=2026" in x["partition"]
                and any("month=" + str(n) in x["partition"] for n in (8, 9))
            }
        paths = [i["original_path"] for i in files]
        if len(paths) != len(set(paths)) or set(paths) != selected:
            raise BoundedError("REQUIRED_SCOPE_NATIVE_EXACT_INPUT_SET_CHANGED")
        for item in files:
            original = declared[item["original_path"]]
            if any(item[f] != original[f] for f in ("sha256", "size_bytes", "rows", "partition")):
                raise BoundedError("REQUIRED_SCOPE_NATIVE_MEMBER_BINDING_CHANGED")
    outputs, daily, summary = replay_required(root, body, budget)
    retained_summary = json.loads((root / "safe-summary.json").read_bytes())
    if digest(summary) != digest(retained_summary):
        raise BoundedError("REQUIRED_SCOPE_SOURCE_REPLAY_CHANGED")
    contract, model, extension = load_contract(root / "evidence/contract")
    from .base23_native_image import image_schemas

    ss = image_schemas(model, extension)
    for table, rows in outputs.items():
        paths = [root / item["path"] for item in body["outputs"][table]]
        actual = [r for p in paths for r in pq.ParquetFile(p).read().to_pylist()]
        if sorted(digest(r) for r in actual) != sorted(digest(r) for r in rows):
            raise BoundedError("REQUIRED_SCOPE_PROJECTED_TYPED_ATOMS_CHANGED")
        if semantic_scan(paths, ss[table], check=budget.check) != body["semantic_scans"][table]:
            raise BoundedError("REQUIRED_SCOPE_PROJECTED_SCHEMA_OR_SEMANTICS_CHANGED")
        rule = contract["tables"][table]
        for item in body["outputs"][table]:
            p = root / item["path"]
            Member(p, item["sha256"], item["size_bytes"], item["rows"]).verify()
            physical = pq.ParquetFile(p)
            if physical.metadata.num_rows != item["rows"] or any(
                partition(row, rule) != item["partition"] for row in physical.read().to_pylist()
            ):
                raise BoundedError("REQUIRED_SCOPE_OUTPUT_PARTITION_OR_CENSUS_CHANGED")
        keys = [tuple(row[f] for f in rule["version_key"]) for row in actual]
        if len(keys) != len(set(keys)):
            raise BoundedError("REQUIRED_SCOPE_PROJECTED_VERSION_KEY_DUPLICATE")
    actual_daily = pq.ParquetFile(root / "qualification/daily.parquet").read().to_pylist()
    if digest(actual_daily) != digest(daily):
        raise BoundedError("REQUIRED_SCOPE_DAILY_QUALIFICATION_REPLAY_CHANGED")
    result = {
        "contract_id": CONTRACT_ID,
        "scope_identity": body["identity"],
        "replay_identity": summary["identity"],
        "physical_members": len(body["physical_members"]),
        "output_counts": {t: len(v) for t, v in outputs.items()},
        "selected_daily_rows": len(daily),
        "consumer_admission": "NOT_GRANTED",
        "complete_Base23": False,
    }
    result["identity"] = digest(result)
    return result
