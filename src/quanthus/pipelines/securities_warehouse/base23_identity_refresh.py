"""Source-backed earlier identity versions the reference extension cannot admit.

The frozen reference-extension rule admits only identities whose effective
interval starts inside the build interval, so an identity the predecessor
already carries keeps the predecessor's knowledge clock even when a later
acquisition observed the same fact earlier. This module records that earlier
observation as one additional version per business key: identical business
values, identical effective interval, an earlier knowledge_from and the
capturing release artifact's own lineage. It never rewrites predecessor bytes,
never changes a business value and never mints a new identity.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .listed_company import _table_schemas

TABLES = (
    "dwd_listed_company",
    "dwd_company_identifier",
    "dwd_security",
    "dwd_security_identifier",
    "dwd_listing",
)
LINEAGE = frozenset(
    {
        "record_source",
        "source_record_id",
        "source_revision",
        "source_artifact_sha256",
        "knowledge_from",
        "knowledge_to",
    }
)


def _current_versions(
    rows: list[dict[str, Any]], business_key: Sequence[str]
) -> list[dict[str, Any]]:
    """Keep one row per business key: the latest source version per key."""
    selected: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = tuple(row[name] for name in business_key)
        held = selected.get(key)
        if held is None or row["knowledge_from"] > held["knowledge_from"]:
            selected[key] = row
    return [selected[key] for key in sorted(selected, key=lambda item: tuple(str(v) for v in item))]


def build_refresh(
    *,
    master: Path,
    observed_at: datetime,
    parents: Mapping[str, Sequence[Path]],
    business_keys: Mapping[str, Sequence[str]],
    output: Path,
    source_sha256: str,
    source_revision: str,
) -> dict[str, Path]:
    """Return one earlier-knowledge version set for identities already inherited."""
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("IDENTITY_REFRESH_OBSERVED_AT_TIMEZONE_REQUIRED")
    schemas = _table_schemas()
    rows = {
        table: [
            row
            for path in parents.get(table, ())
            for row in pq.ParquetFile(path).read().to_pylist()
        ]
        for table in TABLES
    }
    master_codes = {
        str(row["stock_code"])
        for row in pq.ParquetFile(master).read(columns=["stock_code"]).to_pylist()
    }
    by_code: dict[str, list[dict[str, Any]]] = {}
    for row in rows["dwd_listing"]:
        by_code.setdefault(str(row["listing_code"]), []).append(row)
    affected_codes = {
        code
        for code in master_codes
        if by_code.get(code) and min(row["knowledge_from"] for row in by_code[code]) > observed_at
    }
    if not affected_codes:
        return {}
    affected_listings = {
        row["listing_id"]
        for row in rows["dwd_listing"]
        if str(row["listing_code"]) in affected_codes
    }
    affected_securities = {
        row["security_id"] for row in rows["dwd_listing"] if row["listing_id"] in affected_listings
    }
    affected_companies = {
        row["company_id"]
        for row in rows["dwd_security"]
        if row["security_id"] in affected_securities
    }
    scope = {
        "dwd_listed_company": lambda row: row["company_id"] in affected_companies,
        "dwd_company_identifier": lambda row: row["company_id"] in affected_companies,
        "dwd_security": lambda row: row["security_id"] in affected_securities,
        "dwd_security_identifier": lambda row: row["security_id"] in affected_securities,
        "dwd_listing": lambda row: row["listing_id"] in affected_listings,
    }
    result: dict[str, Path] = {}
    for table in TABLES:
        business_key = list(business_keys[table])
        minted = []
        for row in _current_versions(
            [row for row in rows[table] if scope[table](row)], business_key
        ):
            if row["knowledge_from"] <= observed_at:
                continue
            refreshed = dict(row)
            refreshed.update(
                record_source="ref_security_master",
                source_record_id=(
                    "security_master.parquet#code=" + str(row["listing_code"])
                    if "listing_code" in row
                    else "security_master.parquet#id=" + str(row[business_key[0]])
                ),
                source_revision=source_revision,
                source_artifact_sha256=source_sha256,
                knowledge_from=observed_at,
                knowledge_to=None,
            )
            minted.append(refreshed)
        if not minted:
            continue
        path = output / "reference" / table / "part-00000.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(minted, schema=schemas[table]), path)
        result[table] = path
    return result

