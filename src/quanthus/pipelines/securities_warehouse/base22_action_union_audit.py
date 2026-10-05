"""Image-local exact selected action audit, explicitly short of full Base admission."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_action_union import CONTRACT_ID, select_uncontested_actions, selected_rows
from .base22_bounded import BoundedError, Budget, GlobalScan, Member
from .base22_f10_dividend import CONTRACT_ID as F10_CONTRACT_ID
from .base22_output_audit import audit_observation_stage
from .base22_sources import _tree

TABLES = ("dwd_corporate_action", "dwd_corporate_action_term")
PARENTS = ("dwd_listed_company", "dwd_security")


def audit_selected_action_scope(
    root: Path, descriptor: dict[str, Any], budget: Budget
) -> dict[str, Any]:
    """Recompute whole frozen-input absence, every selected column and scoped links.

    Raw F10 replay is an additional mandatory mode of the independent acceptance
    harness. This function cannot restore normalization evidence or admit a full
    company-action domain. Archive source_root strings are never dereferenced.
    """
    if (
        descriptor.get("projection_contract_id") != CONTRACT_ID
        or descriptor.get("kind") != "sealed_actual_domain_observations_not_complete_base22"
        or descriptor.get("consumer_admission") != "NOT_GRANTED"
        or descriptor.get("complete_Base22") is not False
        or descriptor.get("full471_acceptance") is not False
        or descriptor.get("native_rows_replaced") != 0
        or descriptor.get("source_priority_granted") is not False
        or descriptor.get("identity")
        != digest({k: v for k, v in descriptor.items() if k != "identity"})
        or set(descriptor.get("output_rows", {})) != {*TABLES, *PARENTS}
    ):
        raise BoundedError("ACTION_SELECTED_SCOPE_INVALID")
    stage = root / "observations" / descriptor["label"]
    expected_files = {f["path"]: f for f in descriptor["physical_files"]}
    descriptor_relative = (stage / "OBSERVATIONS.json").relative_to(root).as_posix()
    if len(expected_files) != len(descriptor["physical_files"]) or set(_tree(root)) != {
        *expected_files,
        descriptor_relative,
    }:
        raise BoundedError("ACTION_SELECTED_EXACT_PHYSICAL_SET_INVALID")
    for relative, item in expected_files.items():
        p = _physical(root, relative, independent=True)
        if p.stat().st_size != item["size_bytes"] or file_sha(p) != item["sha256"]:
            raise BoundedError("ACTION_SELECTED_PHYSICAL_BYTES_CHANGED")
        budget.check()
    config = stage / "contract"
    for name, sha in descriptor["configuration_sha256"].items():
        if (
            file_sha(_physical(root, str((config / name).relative_to(root)), independent=True))
            != sha
        ):
            raise BoundedError("ACTION_SELECTED_CONFIGURATION_CHANGED")
    contract, model, extension = load_contract(config)
    ss = schemas(model, extension)
    full = audit_observation_stage(root, descriptor, config)
    donor_root = _physical(
        root,
        descriptor["F10_local_root"] + "/observations/f10-dividend/OBSERVATIONS.json",
        independent=True,
    ).parents[2]
    donor_path = donor_root / "observations/f10-dividend/OBSERVATIONS.json"
    donor = json.loads(donor_path.read_bytes())
    if (
        file_sha(donor_path) != descriptor["F10_descriptor_sha256"]
        or donor.get("identity") != descriptor["F10_scope_identity"]
        or donor.get("identity") != digest({k: v for k, v in donor.items() if k != "identity"})
        or donor.get("full_471_acceptance") is not False
        or donor.get("full_33_table_image") is not False
        or donor.get("consumer_admission") != "NOT_GRANTED"
        or donor.get("projection_contract_id") != F10_CONTRACT_ID
    ):
        raise BoundedError("ACTION_SELECTED_F10_SOURCE_SCOPE_INVALID")
    audit_observation_stage(donor_root, donor, donor_root / "observations/f10-dividend/contract")

    def member(item: dict[str, Any]) -> Member:
        p = _physical(root, item["path"], independent=True)
        if p.stat().st_size != item["size_bytes"] or file_sha(p) != item["sha256"]:
            raise BoundedError("ACTION_SELECTED_INPUT_CHANGED")
        return Member(p, item["sha256"], item["size_bytes"], item["rows"])

    incoming = [
        member({**item, "path": descriptor["F10_local_root"] + "/" + item["path"]})
        for item in donor["outputs"]
        if item["table"] == TABLES[0]
    ]
    selection = select_uncontested_actions(
        [member(item) for item in descriptor["native_actions"]],
        incoming,
        [member(item) for item in descriptor["competing_actions"]],
        schema=ss[TABLES[0]],
        budget=budget,
    )
    for key in [
        "input_rows",
        "native_distinct_dated_event_keys",
        "dispositions",
        "selected_entity_ids",
        "selected_entity_id_census_sha256",
    ]:
        if selection[key] != descriptor["selection"][key]:
            raise BoundedError("ACTION_SELECTED_WHOLE_INPUT_SELECTION_CHANGED")
    selected = set(selection["selected_entity_ids"])
    expected: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for table in TABLES:
        source_members = [
            member({**item, "path": descriptor["F10_local_root"] + "/" + item["path"]})
            for item in donor["outputs"]
            if item["table"] == table
        ]
        for row in selected_rows(
            source_members, selected_ids=selected, schema=ss[table], budget=budget
        ):
            expected[table, partition(row, contract["tables"][table])][digest(row)] += 1
    actual: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    parents = {t: donor["parent_archive"][t] for t in PARENTS}
    for item in descriptor["outputs"]:
        table = item["table"]
        p = _physical(root, item["path"], independent=True)
        if table in TABLES:
            for batch in pq.ParquetFile(p).iter_batches(batch_size=1024):
                budget.check()
                for row in batch.to_pylist():
                    part = partition(row, contract["tables"][table])
                    if part != item["partition"]:
                        raise BoundedError("ACTION_SELECTED_PHYSICAL_PARTITION_CHANGED")
                    actual[table, part][digest(row)] += 1
        elif len(parents[table]) != 1 or file_sha(p) != parents[table][0]["sha256"]:
            raise BoundedError("ACTION_SELECTED_PARENT_BYTES_CHANGED")
    if dict(actual) != dict(expected):
        raise BoundedError("ACTION_SELECTED_VALUES_CLOCKS_OR_KEYS_CHANGED")
    source_ids = [(i["record_source"], i["revision"], i["sha256"]) for i in donor["inputs"]]
    with GlobalScan(budget) as scan:
        for table in sorted(descriptor["output_rows"]):
            scan.add(
                table,
                [root / item["path"] for item in descriptor["outputs"] if item["table"] == table],
            )
            scan.keys(
                table,
                contract["tables"][table]["business_key"],
                contract["tables"][table]["version_key"],
            )
            scan.row_rules(table, ss[table], contract["tables"][table])
            if table in TABLES:
                scan.lineage(table, source_ids)
        scan.reject(
            """SELECT 1 FROM dwd_corporate_action a WHERE NOT EXISTS
            (SELECT 1 FROM dwd_listed_company p WHERE p.company_id=a.company_id
             AND p.effective_from<=a.effective_at
             AND (p.effective_to IS NULL OR a.effective_at<p.effective_to)
             AND p.knowledge_from<=a.knowledge_from
             AND (p.knowledge_to IS NULL OR a.knowledge_from<p.knowledge_to))
            OR NOT EXISTS(SELECT 1 FROM dwd_security s WHERE s.security_id=a.security_id
             AND s.company_id=a.company_id AND s.knowledge_from<=a.knowledge_from
             AND (s.knowledge_to IS NULL OR a.knowledge_from<s.knowledge_to)) LIMIT 1""",
            "ACTION_SELECTED_DATED_PARENT_CLOSURE_FAILED",
        )
        scan.reject(
            """SELECT 1 FROM dwd_corporate_action_term t WHERE NOT EXISTS
            (SELECT 1 FROM dwd_corporate_action a WHERE a.corporate_action_id=t.corporate_action_id
             AND a.knowledge_from=t.knowledge_from
             AND a.knowledge_to IS NOT DISTINCT FROM t.knowledge_to
             AND a.record_source=t.record_source AND a.source_revision=t.source_revision
             AND a.source_artifact_sha256=t.source_artifact_sha256) LIMIT 1""",
            "ACTION_SELECTED_TERM_SOURCE_VERSION_CLOSURE_FAILED",
        )
    return {
        "status": "OFFLINE_UNCONTESTED_ACTION_SCOPE_PASS_NOT_FULL471_OR_BASE22",
        "identity": descriptor["identity"],
        "selected_actions": len(selected),
        "output_rows": full["tables"],
        "native_rows_replaced": 0,
        "source_priority_granted": False,
        "full471_or_Base22_acceptance": False,
    }
