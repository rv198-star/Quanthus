"""Account for independent source rows without changing the frozen parser.

The full original body stays evidence. Derived row views are explicitly named
as deterministic interpretations, never original vendor payloads or event IDs.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_f10_ownership import _cells
from .base22_holder_literals import _CHANGE_HEADERS
from .base231_holder_scope import TABLE, project_change_tables
from .financial_reporting import FinancialCompanyResolver

CONTRACT_ID = "p10-base231-independent-holder-change-source-rows-v1"


def project_rows(
    raw: dict[str, Any],
    *,
    raw_row_sha256: str,
    source_sha256: str,
    resolver: FinancialCompanyResolver,
    observed_at: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if digest(raw) != raw_row_sha256:
        raise BoundedError("HOLDER_CHANGE_ROW_ORIGINAL_RAW_CHANGED")
    lines = raw["content"].splitlines()
    starts = [n for n, line in enumerate(lines) if tuple(_cells(line)) in _CHANGE_HEADERS]
    output: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    original, original_proof = project_change_tables(
        raw,
        source_sha256=source_sha256,
        resolver=resolver,
        observed_at=observed_at,
    )
    if original[TABLE]:
        raise BoundedError("HOLDER_CHANGE_ROW_RECOVERY_ONLY_HELD_ORIGINAL_SCOPE")
    if not starts or len(starts) > 16:
        raise BoundedError("HOLDER_CHANGE_ROW_EXACT_TABLE_EXTENT_UNPROVEN")
    for block, start in enumerate(starts):
        stop = starts[block + 1] if block + 1 < len(starts) else len(lines)
        closing = next(
            (n for n in range(start + 1, stop) if lines[n].strip().startswith("└")), None
        )
        if closing is None:
            decisions.append(dict(block=block, status="HELD_ORIGINAL_TABLE_INCOMPLETE"))
            continue
        row_starts = [
            n
            for n in range(start + 1, closing)
            if (
                len(cells := _cells(lines[n])) == 7 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0])
            )
        ]
        if not row_starts or len(row_starts) > 2048:
            decisions.append(dict(block=block, status="HELD_SOURCE_ROW_EXTENT_UNPROVEN"))
            continue
        prefix = lines[start : row_starts[0]]
        for index, first in enumerate(row_starts):
            end = row_starts[index + 1] if index + 1 < len(row_starts) else closing
            # Header, original unit rows, original wrapped atom and the actual
            # closing line form a derived view. No new source cell is invented.
            body = "\n".join(prefix + lines[first:end] + [lines[closing]])
            derived = {
                **raw,
                "content": body,
                "content_sha256": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
            }
            rows, proof = project_change_tables(
                derived,
                source_sha256=source_sha256,
                resolver=resolver,
                observed_at=observed_at,
            )
            decision = dict(
                block=block,
                source_line_start=first,
                source_line_end=end,
                original_atom_sha256=digest(lines[first:end]),
                derived_view_sha256=derived["content_sha256"],
                derived_view_is_original_vendor_payload=False,
                original_event_identity=None,
                original_disclosure_identity=None,
                status="QUALIFIED_SEPARATE_SOURCE_ROW" if rows[TABLE] else "HELD",
                parser_dispositions=proof["blocks"],
            )
            if rows[TABLE]:
                if len(rows[TABLE]) != 1:
                    raise BoundedError("HOLDER_CHANGE_ROW_SINGLE_SOURCE_ATOM_NOT_PROVEN")
                row = rows[TABLE][0]
                # Distinct original source atoms remain distinct observations.
                # A generated ID grants no transaction or cross-source identity.
                row.update(
                    record_source="p10_retained_holder_change_independent_row",
                    source_revision=CONTRACT_ID,
                    source_record_id=digest([raw_row_sha256, first, end]),
                    shareholding_change_id="shareholding_change_"
                    + digest([CONTRACT_ID, raw_row_sha256, first, end])[7:39],
                )
                output.append(row)
            decisions.append(decision)
    proof = dict(
        contract_id=CONTRACT_ID,
        original_raw_row_sha256=raw_row_sha256,
        original_source_member_sha256=source_sha256,
        original_whole_block_dispositions=original_proof["blocks"],
        source_atoms=len([d for d in decisions if "source_line_start" in d]),
        qualified_rows=len(output),
        decisions=decisions,
        rows_sha256=digest(output),
        net_change_admission="NOT_GRANTED",
        transaction_equivalence_granted=False,
        provider_PIT_qualified=False,
    )
    return output, proof
