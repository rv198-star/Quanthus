"""Temporary dimension availability only; no action/market rows or window acceptance."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from quanthus.stage_learning.p5_input_census import (
    VENUES,
    bounded_tmp,
    checked_child,
    file_digest,
)

PROPOSAL_SHA = "sha256:ef0819b57e970f53f92629e55ff8533f2999e45b6cedb111cb279d7b09fa4a69"
START = "2012-01-01 00:00:00+08:00"
END_EXCLUSIVE = "2022-01-01 00:00:00+08:00"
COMMON = {
    **{
        key: "TIMESTAMP WITH TIME ZONE"
        for key in ("effective_from", "effective_to", "knowledge_from", "knowledge_to")
    },
    **{
        key: "VARCHAR"
        for key in (
            "record_source",
            "source_record_id",
            "source_revision",
            "source_artifact_sha256",
        )
    },
}
SCHEMAS = {
    "dwd_listing": {
        **COMMON,
        **dict.fromkeys(("listing_id", "security_id", "venue_id", "listing_code"), "VARCHAR"),
    },
    "dwd_security": {
        **COMMON,
        **dict.fromkeys(
            ("security_id", "company_id", "instrument_type", "instrument_currency"), "VARCHAR"
        ),
    },
}
STAGE_GRAPH = [
    {"stage": "exact_dimension_integrity", "depends_on": [], "lifecycle": "none"},
    {
        "stage": "historical_listing",
        "depends_on": ["exact_dimension_integrity"],
        "lifecycle": "TMP",
    },
    {"stage": "historical_security", "depends_on": ["historical_listing"], "lifecycle": "TMP"},
    {
        "stage": "dimension_diagnostics",
        "depends_on": ["historical_listing", "historical_security"],
        "lifecycle": "none",
    },
]


def verify_inputs(repo: Path, base: Path) -> tuple[dict[str, Path], dict[str, Any]]:
    activation_path = repo / "config/research/p3-p5-temporary-source-activation.v1.json"
    activation = json.loads(activation_path.read_text())
    bound = activation["sources"]["P5"]
    if (
        activation["state"] != "ACTIVE_BOUNDED_TEMPORARY_QUALIFICATION"
        or activation["usage"] != "temporary_research_qualification_only"
        or activation["temporary_success_closes_data_issues"] is not False
        or bound["proposal_sha256"] != PROPOSAL_SHA
        or bound["data_repair_issues"] != [471, 472]
    ):
        raise ValueError("TEMPORARY_ACTIVATION_INVALID")
    proposal_path = repo / bound["proposal_path"]
    if file_digest(proposal_path) != PROPOSAL_SHA:
        raise ValueError("PROPOSAL_CHECKSUM_MISMATCH")
    proposal = json.loads(proposal_path.read_text())
    frozen = proposal["metadata_verified_source"]
    manifest = checked_child(base, "manifest.json")
    if (
        file_digest(manifest) != frozen["base_manifest_sha256"]
        or checked_child(base, "READY").read_text().strip() != frozen["base_manifest_sha256"]
    ):
        raise ValueError("BASE_MANIFEST_OR_READY_MISMATCH")
    inventory_rows = json.loads(manifest.read_text())["files"]
    inventory = {item["path"]: item for item in inventory_rows}
    if len(inventory) != len(inventory_rows):
        raise ValueError("DUPLICATE_BASE_INVENTORY")
    paths = {}
    for member in frozen["dimension_members"]:
        if any(
            inventory.get(member["path"], {}).get(k) != member[k] for k in ("sha256", "size_bytes")
        ):
            raise ValueError("DIMENSION_INVENTORY_MISMATCH")
        path = checked_child(base, member["path"])
        if path.stat().st_size != member["size_bytes"] or file_digest(path) != member["sha256"]:
            raise ValueError("DIMENSION_CHECKSUM_MISMATCH")
        paths[path.stem] = path
    if set(paths) != set(SCHEMAS):
        raise ValueError("EXACT_DIMENSION_SET_REQUIRED")
    return paths, {
        "activation_sha256": file_digest(activation_path),
        "proposal_sha256": PROPOSAL_SHA,
        "base_manifest_sha256": frozen["base_manifest_sha256"],
        "selected_members": frozen["dimension_members"],
        "selected_source_bytes": sum(p.stat().st_size for p in paths.values()),
    }


def inspect_dimensions(connection: Any, paths: dict[str, Path]) -> dict[str, Any]:
    stages = []
    for table, schema in SCHEMAS.items():
        started = time.monotonic()
        observed = {
            row[0]: row[1]
            for row in connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(paths[table])]
            ).fetchall()
        }
        if any(observed.get(key) != value for key, value in schema.items()):
            raise ValueError("DIMENSION_SCHEMA_MISMATCH")
        # Scope ambiguity is a member failure, not a silent out-of-scope row.
        invalid_dates = connection.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE effective_from IS NULL "
            "OR (effective_to IS NOT NULL AND effective_to <= effective_from)",
            [str(paths[table])],
        ).fetchone()[0]
        if invalid_dates:
            raise ValueError("UNKNOWN_DIMENSION_SCOPE")
        identity_keys = (
            ("listing_id", "security_id", "venue_id", "listing_code")
            if table == "dwd_listing"
            else ("security_id", "company_id")
        )
        unknown_identity = connection.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE effective_from < ?::TIMESTAMPTZ "
            "AND (effective_to IS NULL OR effective_to > ?::TIMESTAMPTZ) AND ("
            + " OR ".join(f"{key} IS NULL OR trim({key})=''" for key in identity_keys)
            + ")",
            [str(paths[table]), END_EXCLUSIVE, START],
        ).fetchone()[0]
        if unknown_identity:
            raise ValueError("UNKNOWN_DIMENSION_IDENTITY_OR_SCOPE")
        predicate = (
            " AND venue_id IN (?, ?)"
            if table == "dwd_listing"
            else (" AND security_id IN (SELECT security_id FROM dwd_listing)")
        )
        parameters = [str(paths[table]), END_EXCLUSIVE, START]
        if table == "dwd_listing":
            parameters.extend(VENUES)
        connection.execute(
            f"CREATE TABLE {table} AS SELECT {', '.join(schema)} FROM read_parquet(?) "
            "WHERE effective_from < ?::TIMESTAMPTZ "
            "AND (effective_to IS NULL OR effective_to > ?::TIMESTAMPTZ)" + predicate,
            parameters,
        )
        required = [key for key in schema if key not in ("effective_to", "knowledge_to")]
        invalid = connection.execute(
            f"SELECT count(*) FROM {table} WHERE "
            + " OR ".join(f"{key} IS NULL" for key in required)
        ).fetchone()[0]
        if invalid:
            raise ValueError("UNKNOWN_DIMENSION_IDENTITY_OR_LINEAGE")
        count = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        projected_bytes = connection.execute(
            f"SELECT coalesce(sum(octet_length(encode(to_json(t)))), 0) FROM {table} t"
        ).fetchone()[0]
        stages.append(
            {
                "stage": "historical_" + table.removeprefix("dwd_"),
                "rows": count,
                "source_bytes": paths[table].stat().st_size,
                "projection_json_bytes": projected_bytes,
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "disposition": "built_once",
                "lifecycle": "TMP",
            }
        )
    started = time.monotonic()
    listing_count, security_count = [s["rows"] for s in stages]
    unresolved = connection.execute("""
        SELECT count(*) FROM dwd_listing l WHERE NOT EXISTS (
          SELECT 1 FROM dwd_security s WHERE s.security_id=l.security_id
          AND s.effective_from < coalesce(l.effective_to, 'infinity'::TIMESTAMPTZ)
          AND l.effective_from < coalesce(s.effective_to, 'infinity'::TIMESTAMPTZ))
        """).fetchone()[0]
    duplicates = {}
    for table, key in (("dwd_listing", "listing_id"), ("dwd_security", "security_id")):
        # Preserve competing knowledge versions. Never choose the current/latest version.
        duplicates[table] = connection.execute(f"""
            SELECT count(*) FROM (
              SELECT {key} FROM {table} GROUP BY {key} HAVING count(*)>1)
            """).fetchone()[0]
    knowledge = {}
    for table in SCHEMAS:
        values = connection.execute(
            f"""
            SELECT min(knowledge_from)::VARCHAR, max(knowledge_from)::VARCHAR,
                   count(*) FILTER (WHERE knowledge_from >= ?::TIMESTAMPTZ)
            FROM {table}
            """,
            [END_EXCLUSIVE],
        ).fetchone()
        knowledge[table] = dict(
            zip(("minimum", "maximum", "post_development_end_rows"), values, strict=True)
        )
    stages.append(
        {
            "stage": "dimension_diagnostics",
            "disposition": "reused_TMP",
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }
    )
    return {
        "disposition": "TEMPORARY_DIMENSION_AVAILABILITY_INSPECTED",
        "historical_listing_rows": listing_count,
        "linked_historical_security_rows": security_count,
        "listing_rows_without_any_overlapping_security_version": unresolved,
        "ids_with_multiple_retained_versions": duplicates,
        "knowledge_provenance": knowledge,
        "stages": stages,
        "session_identity_coverage_qualified": None,
        "original_normalization_scope_verified": False,
        "coordinate_window_qualification": None,
        "ACTIVE_qualification": None,
        "B_accepted": False,
        "historical_as_known": False,
        "corporate_action_payloads_read": False,
        "formal_DWD_repair": False,
        "limitation": (
            "Dimension availability is not per-session unique identity, complete action prefix, "
            "ACTIVE or 120-session input qualification."
        ),
        "remaining_data_issues": [471, 472],
    }


def run(repo: Path, base: Path, output: Path, revision: str) -> dict[str, Any]:
    import duckdb

    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("EXACT_CODE_REVISION_REQUIRED")
    paths, source = verify_inputs(repo, base)
    output.mkdir(parents=False, exist_ok=False, mode=0o700)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="p5-identity-", dir=output) as tmp:
        temporary = Path(tmp)
        connection = duckdb.connect(str(temporary / "identity.duckdb"))
        connection.execute("SET threads=2")
        connection.execute("SET memory_limit='1GiB'")
        connection.execute("SET max_temp_directory_size='4GiB'")
        try:
            with bounded_tmp(connection, temporary, deadline=started + 600, limit=4294967296):
                result = inspect_dimensions(connection, paths)
                result["tmp_bytes"] = sum(
                    p.stat().st_size for p in temporary.rglob("*") if p.is_file()
                )
        finally:
            connection.close()
    result.update(
        {
            "schema_version": "quanthus.p5-temporary-identity-receipt.v1",
            "code_revision": revision,
            "implementation_sha256": file_digest(Path(__file__)),
            "source": source,
            "projection_schema": SCHEMAS,
            "stage_graph": STAGE_GRAPH,
            "date_start": "2012-01-01",
            "date_end": "2021-12-31",
            "tmp_removed": True,
            "temporary_research_only": True,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    )
    (output / "receipt.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("repo", "base", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.repo, args.base, args.output, args.revision)))


if __name__ == "__main__":
    main()
