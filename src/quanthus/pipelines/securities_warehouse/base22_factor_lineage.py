"""Complete, bounded native factor replay without promoting declared dates to PIT.

The native input model uses SOURCE_EVIDENCE with no corporate-action reference.
Replaying that relationship does not recover the missing normalization revision,
resolve competing action amounts, or admit any historical information time.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import date
from decimal import localcontext
from typing import Any

from .base22 import THROUGH, canonical, digest
from .base22_bounded import BoundedError
from .disclosure_event import materialize_adjustment_factor_rows

CONTRACT_ID = "p10-base22-native-factor-exact-raw-replay-v1"
TABLES = ("dwd_adjustment_factor", "dwd_adjustment_factor_input")
MAX_RAW_ROWS = 65536
MAX_NATIVE_ROWS = MAX_RAW_ROWS * 3
UNAVAILABLE = "harvest_day_exception|no_proveable_announce"
POLICY = "factor_unavailable_before_knowledge_cutoff; no forge; degrade/block adj features"


def _source_key(row: dict[str, Any], *, exception: bool = False) -> tuple[str, date, str]:
    day = row.get("effective_date")
    if exception:
        try:
            day = date.fromisoformat(day) if isinstance(day, str) else None
        except ValueError:
            day = None
    if (
        type(day) is not date
        or day > THROUGH
        or not isinstance(row.get("stock_code"), str)
        or not row["stock_code"]
        or not isinstance(row.get("source_revision"), str)
        or not row["source_revision"]
    ):
        raise BoundedError("NATIVE_FACTOR_RAW_KEY_OR_BUSINESS_DATE_INVALID")
    return row["stock_code"], day, row["source_revision"]


def _version(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(k) for k in ("adjustment_factor_id", "effective_from", "knowledge_from"))


def replay_factor_lineage(
    raw_rows: Iterable[dict[str, Any]],
    exception_rows: Iterable[dict[str, Any]],
    native_rows: dict[str, Iterable[dict[str, Any]]],
    *,
    resolver: Any,
    vocabulary: dict[str, Any],
    raw_sha256: str,
    check: Callable[[], None] = lambda: None,
    binding_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Compare every old typed atom, global version and next effective endpoint.

    Only a bounded raw/key/hash index is retained. No historical row-document
    database is built, and no source clock or number is assigned a new meaning.
    The caller verifies physical files/schemas, hashes and dated parent inventory.
    """
    import pyarrow as pa  # type: ignore[import-untyped]

    if set(native_rows) != set(TABLES):
        raise BoundedError("NATIVE_FACTOR_EXACT_TABLE_SET_INVALID")
    if len(vocabulary.get("factor_methods", {})) != 3:
        raise BoundedError("NATIVE_FACTOR_METHOD_SET_INVALID")
    raw: dict[tuple[str, date, str], dict[str, Any]] = {}
    for i, row in enumerate(raw_rows):
        if i >= MAX_RAW_ROWS:
            raise BoundedError("NATIVE_FACTOR_RAW_SCOPE_EXCEEDED")
        key = _source_key(row)
        if key in raw:
            raise BoundedError("NATIVE_FACTOR_DUPLICATE_RAW_KEY")
        raw[key] = row
        if i % 1024 == 0:
            check()
    exceptions: dict[tuple[str, date, str], dict[str, Any]] = {}
    for i, row in enumerate(exception_rows):
        if i >= MAX_RAW_ROWS:
            raise BoundedError("NATIVE_FACTOR_EXCEPTION_SCOPE_EXCEEDED")
        key = _source_key(row, exception=True)
        original = raw.get(key)
        if (
            key in exceptions
            or original is None
            or row.get("exception_type") != "factor_pit_visibility"
            or row.get("reason_code") != "no_proveable_announce_after_em_exact_link"
            or row.get("visibility_origin") != UNAVAILABLE
            or original.get("visibility_origin") != UNAVAILABLE
            or row.get("policy") != POLICY
            or digest([row.get(f) for f in vocabulary["factor_methods"]])
            != digest([original.get(f) for f in vocabulary["factor_methods"]])
        ):
            raise BoundedError("NATIVE_FACTOR_ORIGINAL_UNAVAILABLE_CLOSURE_INVALID")
        exceptions[key] = row
    if set(exceptions) != {k for k, r in raw.items() if r.get("visibility_origin") == UNAVAILABLE}:
        raise BoundedError("NATIVE_FACTOR_UNAVAILABLE_KEY_CENSUS_INVALID")
    if not raw:
        raise BoundedError("NATIVE_FACTOR_RAW_SCOPE_EMPTY")
    expected_closes: dict[tuple[Any, ...], Any] = {}
    groups: dict[str, list[Any]] = defaultdict(list)
    admitted: dict[tuple[str, date, str], Any] = {}
    excluded: Counter[str] = Counter()
    excluded_unavailable = 0
    origins: Counter[str] = Counter()
    for index, (key, row) in enumerate(raw.items()):
        parent, reason = resolver.resolve_for_report(key[0], period_end=key[1])
        if parent is None:
            if not isinstance(reason, str) or not reason:
                raise BoundedError("NATIVE_FACTOR_DATED_IDENTITY_DISPOSITION_INVALID")
            excluded[reason] += 1
            excluded_unavailable += int(key in exceptions)
            continue
        admitted[key] = parent
        origins[str(row.get("visibility_origin"))] += 1
        with localcontext() as ctx:
            ctx.prec = 64
            projected = materialize_adjustment_factor_rows(
                row,
                source_sha256=raw_sha256,
                company_security_id=parent.security_id,
                vocabulary=vocabulary,
                parent_knowledge_from=parent.knowledge_from,
            )
        for factor in projected[TABLES[0]]:
            groups[factor["adjustment_factor_id"]].append(factor["effective_from"])
        if index % 1024 == 0:
            check()
    for factor_id, coordinates in groups.items():
        coordinates.sort()
        if len(set(coordinates)) != len(coordinates):
            raise BoundedError("NATIVE_FACTOR_GLOBAL_EFFECTIVE_KEY_DUPLICATE")
        for i, coordinate in enumerate(coordinates):
            expected_closes[factor_id, coordinate] = (
                coordinates[i + 1] if i + 1 < len(coordinates) else None
            )
    expected: dict[str, dict[tuple[Any, ...], tuple[str, dict[str, Any]]]] = {t: {} for t in TABLES}
    for index, (key, parent) in enumerate(admitted.items()):
        row = raw[key]
        with localcontext() as ctx:
            ctx.prec = 64
            projected = materialize_adjustment_factor_rows(
                row,
                source_sha256=raw_sha256,
                company_security_id=parent.security_id,
                vocabulary=vocabulary,
                parent_knowledge_from=parent.knowledge_from,
            )
        for table, rows in projected.items():
            for target in rows:
                target["effective_to"] = expected_closes[
                    target["adjustment_factor_id"], target["effective_from"]
                ]
                if table == TABLES[0]:
                    # Parquet's fixed Decimal scale is part of the typed before
                    # image. This exact cast rejects rounding/overflow; it does
                    # not pretend to recover the provider's original precision.
                    value = target["factor_value"]
                    typed = pa.scalar(value, type=pa.decimal128(38, 18)).as_py()
                    if typed != value:
                        raise BoundedError("NATIVE_FACTOR_EXACT_DECIMAL_CAST_CHANGED_VALUE")
                    target["factor_value"] = typed
                version = _version(target)
                if version in expected[table]:
                    raise BoundedError("NATIVE_FACTOR_GLOBAL_VERSION_KEY_DUPLICATE")
                binding = {
                    "adjustment_factor_id": target["adjustment_factor_id"],
                    "effective_from": target["effective_from"],
                    "knowledge_from": target["knowledge_from"],
                    "raw_row_sha256": digest(row),
                    "original_raw_key_sha256": digest(key),
                    "exception_row_sha256": digest(exceptions[key]) if key in exceptions else None,
                    "original_visibility_origin": row.get("visibility_origin"),
                    "historical_availability": "UNAVAILABLE"
                    if key in exceptions
                    else "DECLARED_SOURCE_DATE_NOT_PIT_QUALIFIED",
                    "provider_acquisition": None,
                }
                expected[table][version] = digest(target), binding
        if index % 1024 == 0:
            check()
    counts = {}
    binding_sha = hashlib.sha256()
    blocked = 0
    for table in TABLES:
        remaining = expected[table]
        count = 0
        for row in native_rows[table]:
            count += 1
            if count > MAX_NATIVE_ROWS:
                raise BoundedError("NATIVE_FACTOR_OUTPUT_SCOPE_EXCEEDED")
            item = remaining.pop(_version(row), None)
            if item is None or digest(row) != item[0]:
                raise BoundedError("NATIVE_FACTOR_EXACT_NATIVE_ATOM_OR_VERSION_MISMATCH")
            if table == TABLES[0]:
                binding = item[1]
                blocked += int(binding["historical_availability"] == "UNAVAILABLE")
                binding_sha.update(canonical(binding) + b"\n")
                if binding_sink is not None:
                    binding_sink(binding)
            if count % 1024 == 0:
                check()
        if remaining:
            raise BoundedError("NATIVE_FACTOR_REQUIRED_NATIVE_ROWS_MISSING")
        counts[table] = count
    check()
    result = {
        "contract_id": CONTRACT_ID,
        "raw_rows": len(raw),
        "admitted_raw_rows": len(admitted),
        "excluded_raw_dispositions": dict(sorted(excluded.items())),
        "exception_rows": len(exceptions),
        "unavailable_excluded_raw_rows": excluded_unavailable,
        "unavailable_native_factor_versions": blocked,
        "factor_rows": counts[TABLES[0]],
        "input_rows": counts[TABLES[1]],
        "native_visibility_origins": dict(sorted(origins.items())),
        "binding_stream_sha256": "sha256:" + binding_sha.hexdigest(),
        "exception_key_census_sha256": digest(sorted(digest(k) for k in exceptions)),
        "normalization_action_dependency_rows": 0,
        "factor_input_relationship": "EXACT_RAW_SOURCE_EVIDENCE_WITH_NULL_CORPORATE_ACTION_ID",
        "native_typed_atoms_and_global_effective_endpoints": "EXACT_REPLAY",
        "original_vendor_decimal_precision_recovered": False,
        "provider_acquisition_inferred": False,
        "historical_PIT_admission": False,
        "original_normalization_manifest_restored": False,
        "source_priority_or_consumer_admission": "NOT_GRANTED",
    }
    result["identity"] = digest(result)
    return result
