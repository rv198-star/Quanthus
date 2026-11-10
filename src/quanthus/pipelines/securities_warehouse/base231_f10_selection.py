"""One metadata-only September F10 selection shared by the two patch domains.

Latest capture is a snapshot selection, never a priority for disputed historical
values or an inference of historical availability. All original files are kept
in the replay closure. Only selected financial/holder bodies are decoded.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .base23_ownership_reconfirmation import RAW_FIELDS

CATEGORIES = frozenset({"财务分析", "股东研究"})
THROUGH = date(2026, 9, 30)
META = ["stock_code", "category", "source", "source_revision", "acquired_at",
        "market_date", "content_sha256", "provider_updated_date"]


def select_metadata(files: list[tuple[str, Path]]) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    selected: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    scanned = relevant = 0
    if not files or len({name for name, _ in files}) != len(files):
        raise BoundedError("BASE231_SELECTION_FILES_INVALID")
    for name, path in sorted(files):
        parquet = pq.ParquetFile(path)
        if set(parquet.schema_arrow.names) != RAW_FIELDS:
            raise BoundedError("BASE231_SELECTION_RAW_SCHEMA_CHANGED")
        ordinal = 0
        for batch in parquet.iter_batches(batch_size=256, columns=META):
            for item in batch.to_pylist():
                here = ordinal
                ordinal += 1
                scanned += 1
                if item["category"] not in CATEGORIES:
                    continue
                if date.fromisoformat(item["market_date"]) > THROUGH:
                    continue
                relevant += 1
                clock = _clock(item["acquired_at"])
                if (item["source"] != "tdx.get_company_info_content"
                        or item["source_revision"] != "quanthus-fundamentals-provider-v1"):
                    raise BoundedError("BASE231_SELECTION_PROVIDER_UNQUALIFIED")
                key = item["source"], item["stock_code"], item["category"]
                entry = {**item, "member": name, "ordinal": here, "clock": clock}
                prior = selected.get(key)
                if prior is None or clock > prior[0]["clock"]:
                    selected[key] = [entry]
                elif clock == prior[0]["clock"]:
                    prior.append(entry)
    result, held = [], []
    for key, candidates in sorted(selected.items()):
        atoms = {(r["content_sha256"], r["provider_updated_date"]) for r in candidates}
        if len(atoms) != 1:
            held.append({"identity_sha256": digest(key), "reason": "SAME_CAPTURE_BODY_CONFLICT",
                         "candidates": candidates})
        else:
            result.append(min(candidates, key=lambda r: (r["member"], r["ordinal"])))
    return {"rule": "latest_actual_capture_per_provider_stock_category-v1",
            "business_through": THROUGH, "metadata_rows_scanned": scanned,
            "relevant_capture_rows": relevant, "selected": result, "held": held,
            "publication_or_PIT_inferred": False, "older_base_rows_removed": False}


def selected_bodies(
    files: list[tuple[str, Path]], selection: dict[str, Any],
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    import pyarrow as pa
    import pyarrow.compute as pc  # type: ignore[import-untyped]
    import pyarrow.parquet as pq

    by_member: dict[str, dict[int, dict[str, Any]]] = {}
    for row in selection["selected"]:
        by_member.setdefault(row["member"], {})[row["ordinal"]] = row
    seen = 0
    for name, path in sorted(files):
        wanted = by_member.get(name, {})
        if not wanted:
            continue
        ordinal = 0
        for batch in pq.ParquetFile(path).iter_batches(batch_size=16):
            mask = pa.array([n in wanted for n in range(ordinal, ordinal + batch.num_rows)])
            indices = [n for n in range(ordinal, ordinal + batch.num_rows) if n in wanted]
            # Unrelated source categories never enter Python dictionaries.
            filtered = batch.filter(mask)
            for number, raw in zip(indices, filtered.to_pylist(), strict=True):
                item = wanted[number]
                if any(raw[k] != item[k] for k in META):
                    raise BoundedError("BASE231_SELECTED_SOURCE_METADATA_CHANGED")
                if pc.utf8_length(pa.array([raw["content"]])).to_pylist()[0] > 1048576:
                    raise BoundedError("BASE231_SELECTED_BODY_BOUND")
                seen += 1
                yield item, raw
            ordinal += batch.num_rows
    if seen != len(selection["selected"]):
        raise BoundedError("BASE231_SELECTION_ROWS_MISSING")
