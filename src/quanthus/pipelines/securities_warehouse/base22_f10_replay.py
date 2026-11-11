"""One bounded F10 response replay from image-local raw bytes and dated parents."""

from __future__ import annotations

from datetime import date
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_f10_dividend import materialize_dividend_record, parse_dividend_content
from .base22_f10_sources import bind_f10_receipt
from .base22_outputs import knowledge_versions

TABLES = ("dwd_corporate_action", "dwd_corporate_action_term")


def replay_f10_group(
    rows: list[dict[str, Any]],
    receipt: dict[str, Any],
    *,
    market_date: date,
    observed_at: Any,
    source_sha256: str,
    revision: str,
    resolver: Any,
    vocabulary: dict[str, Any],
    schemas: dict[str, Any],
    rules: dict[str, Any] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Recompute exact values, real knowledge floor and every dated disposition.

    The caller supplies only the copied original response, receipt and immutable
    local identities. Original source_root strings are never dereferenced here.
    Global uniqueness, physical files and parent closure are separate mandatory
    scope checks; this group replay cannot accept a whole Base 2.2 image.
    """
    response = bind_f10_receipt(rows, receipt, market_date=market_date, observed_at=observed_at)
    dividend = [row for row in rows if row["category"] == "分红扩股"]
    if len(dividend) > 1:
        raise BoundedError("F10_REPLAY_DUPLICATE_DIVIDEND_CATEGORY")
    projected: dict[str, list[dict[str, Any]]] = {table: [] for table in TABLES}
    proofs: list[dict[str, Any]] = []
    for raw in dividend:
        records, source_proof = parse_dividend_content(raw)
        identity_proofs: list[dict[str, Any]] = []
        for record in records:
            parent, reason = resolver.resolve_for_report(
                raw["stock_code"], period_end=record["ex_date"]
            )
            if parent is None:
                identity_proofs.append(
                    {
                        "record_sha256": digest(record),
                        "disposition": "DATED_PARENT_" + str(reason),
                    }
                )
                continue
            output = materialize_dividend_record(
                record,
                acquired_at=raw["acquired_at"],
                source_sha256=source_sha256,
                revision=revision,
                parent=parent,
                vocabulary=vocabulary,
                schemas=schemas,
                observed_at=observed_at,
            )
            identity_proofs.append(
                {
                    "record_sha256": digest(record),
                    "parent": {
                        "company_id": parent.company_id,
                        "security_id": parent.security_id,
                        "knowledge_from": parent.knowledge_from,
                    },
                    "outputs_sha256": digest(output),
                    "output_rows": {table: len(rs) for table, rs in output.items()},
                }
            )
            for table, rs in output.items():
                if table not in projected:
                    raise BoundedError("F10_REPLAY_UNEXPECTED_OUTPUT_TABLE")
                projected[table].extend(rs)
        proofs.append(
            {
                "source_index": 0,
                "proof": source_proof,
                "dated_identity_dispositions": identity_proofs,
            }
        )
    if rules is not None:
        projected = {table: knowledge_versions(rs, rules[table]) for table, rs in projected.items()}
    # Parquet's fixed Decimal scale changes its textual representation. Keep the
    # original row hashes as precision evidence and compare physical rows against
    # the explicit schema representation, without changing values or instants.
    import pyarrow as pa  # type: ignore[import-untyped]

    serialized = {}
    for table, rs in projected.items():
        typed = pa.Table.from_pylist(rs, schema=schemas[table]).to_pylist()
        if typed != rs:
            raise BoundedError("F10_REPLAY_SERIALIZATION_VALUE_OR_TIME_CHANGED")
        serialized[table] = [digest(row) for row in typed]
    proof = {
        "response_proof": response,
        "source_proofs": proofs,
        "merged_output_rows": {table: len(rs) for table, rs in projected.items()},
        "merged_outputs_sha256": {table: digest(rs) for table, rs in projected.items()},
        "merged_row_hashes": {
            table: [digest(row) for row in rs] for table, rs in projected.items()
        },
        "serialized_row_hashes": serialized,
        "full_base22_or_legacy_acceptance": False,
    }
    return projected, proof
