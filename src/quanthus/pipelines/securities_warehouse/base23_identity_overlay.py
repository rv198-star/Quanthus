"""Source-bound new listings beside immutable Base, with no historical PIT grant."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.securities_warehouse.listed_company import _stable_id, _table_schemas


def build_overlay(
    *,
    universe: dict[str, Any],
    master: Path,
    listings: list[dict[str, Any]],
    venues: list[dict[str, Any]],
    output: Path,
    source_sha256: str,
) -> dict[str, Path]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    day = universe["market_date"]
    zone = ZoneInfo("Asia/Shanghai")
    active = {
        row["listing_code"]
        for row in listings
        if row["effective_from"].astimezone(zone).date().isoformat() <= day
        and (
            row["effective_to"] is None
            or day < row["effective_to"].astimezone(zone).date().isoformat()
        )
    }
    expected = set(universe["codes"])
    missing = expected - active
    if not missing:
        return {}
    rows: dict[str, list[dict[str, Any]]] = {
        name: []
        for name in (
            "dwd_listed_company",
            "dwd_company_identifier",
            "dwd_security",
            "dwd_security_identifier",
            "dwd_listing",
        )
    }
    by_venue = {row["mic_code"]: row["venue_id"] for row in venues}
    master_rows = {row["stock_code"]: row for row in pq.ParquetFile(master).read().to_pylist()}
    knowledge = datetime.fromisoformat(universe["warehouse_observed_at"])
    for code in sorted(missing):
        source = master_rows[code]
        if any(row["listing_code"] == code for row in listings):
            raise ValueError("LISTING_IDENTITY_REUSE_REQUIRES_REVIEW:" + code)
        org = universe["company_org_ids"].get(code)
        if not org:
            raise ValueError("NEW_LISTING_COMPANY_IDENTITY_MISSING:" + code)
        exchange = source["exchange"]
        venue = by_venue["XSHG" if exchange == "SSE" else "XSHE"]
        start = (
            datetime.fromisoformat(source["list_date"])
            .replace(tzinfo=zone)
            .astimezone(timezone.utc)
        )
        end = (
            datetime.fromisoformat(source["delist_date"])
            .replace(tzinfo=zone)
            .astimezone(timezone.utc)
            if source["delist_date"]
            else None
        )
        company = _stable_id("company", "CNINFO_ORG_ID", org)
        security = _stable_id(
            "security", "ECONOMIC_SECURITY", exchange + ":" + code + ":" + source["list_date"]
        )
        lineage = {
            "record_source": source["source"],
            "source_record_id": "security_master.parquet#code=" + code,
            "source_revision": "p10-target-day-listing-overlay-v1",
            "source_artifact_sha256": source_sha256,
            "knowledge_from": knowledge,
            "knowledge_to": None,
            "effective_from": start,
            "effective_to": end,
        }
        rows["dwd_listed_company"].append(
            {
                **lineage,
                "company_id": company,
                "display_name": source["name"],
                "company_type": "listed_company",
                "resolution_scope": "CNINFO_ORG_ID:PRIMARY",
            }
        )
        rows["dwd_company_identifier"].append(
            {
                **lineage,
                "company_id": company,
                "identifier_namespace": "CNINFO_ORG_ID",
                "identifier_value": org,
                "identifier_authority": "cninfo",
            }
        )
        rows["dwd_security"].append(
            {
                **lineage,
                "security_id": security,
                "company_id": company,
                "instrument_type": "equity",
                "instrument_currency": "CNY",
            }
        )
        rows["dwd_security_identifier"].append(
            {
                **lineage,
                "security_id": security,
                "identifier_namespace": "CN_STOCK_CODE:" + exchange,
                "identifier_value": code,
                "venue_id": venue,
            }
        )
        listing = _stable_id("listing", security, venue, code, start.isoformat())
        rows["dwd_listing"].append(
            {
                **lineage,
                "listing_id": listing,
                "security_id": security,
                "venue_id": venue,
                "listing_code": code,
                "board_code": "STAR"
                if code.startswith("68")
                else ("CHINEXT" if code.startswith("30") else "MAIN"),
                "listing_currency": "CNY",
                "lot_size": None,
            }
        )
    result = {}
    for table, values in rows.items():
        # Same issuer may have more than one new listing; conserve each literal identity once.
        key_name = {
            "dwd_listed_company": "company_id",
            "dwd_company_identifier": "company_id",
            "dwd_security": "security_id",
            "dwd_security_identifier": "security_id",
            "dwd_listing": "listing_id",
        }[table]
        unique = {row[key_name]: row for row in values}
        path = output / "reference" / table / "part-00000.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pylist(list(unique.values()), schema=_table_schemas()[table]), path
        )
        result[table] = path
    return result
