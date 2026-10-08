"""Shanghai partition coordinates as constant instant bounds, with decoded checks.

Range predicates make the early Parquet preflight inexpensive. Cold acceptance
also checks the actual decoded arrays, so file statistics never replace row proof.
No timestamp, quantity, knowledge interval or frozen consumer rule is rewritten.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .base22_bounded import BoundedError, identifier

SHANGHAI = ZoneInfo("Asia/Shanghai")


def coordinates(part: str, rule: Any, schema: Any) -> list[tuple[str, Any, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    labels = [p.split("=", 1) for p in part.split("/")] if part != "all" else []
    if any(len(p) != 2 for p in labels) or [p[0] for p in labels] != rule["partition_by"]:
        raise BoundedError("NATIVE_IMAGE_PARTITION_LABEL_CHANGED")
    values = dict(labels)
    result = []
    for field, value in labels:
        if field.endswith(("_year", "_month")):
            source, unit = field.rsplit("_", 1)
            if unit == "month":
                if source + "_year" not in values:
                    raise BoundedError("NATIVE_IMAGE_MONTH_WITHOUT_YEAR")
                continue
            try:
                year = int(value)
                month = int(values.get(source + "_month", "1"))
                if str(year) != value or str(month) != values.get(source + "_month", "1"):
                    raise ValueError
                lower = date(year, month, 1)
                upper = (
                    date(year + 1, 1, 1)
                    if source + "_month" not in values or month == 12
                    else date(year, month + 1, 1)
                )
            except (ValueError, OverflowError) as exc:
                raise BoundedError("NATIVE_IMAGE_PARTITION_LABEL_CHANGED") from exc
            kind = schema.field(source).type
            if pa.types.is_timestamp(kind):
                if kind.tz is None:
                    raise BoundedError("NATIVE_IMAGE_PARTITION_TIMEZONE_UNKNOWN")
                lo: Any = datetime.combine(lower, datetime.min.time(), SHANGHAI)
                hi: Any = datetime.combine(upper, datetime.min.time(), SHANGHAI)
            else:
                lo, hi = lower, upper
            result.append((source, lo, hi))
        else:
            result.append((field, value, None))
    return result


def predicates(part: str, rule: Any, schema: Any) -> list[str]:
    result = []
    for field, lower, upper in coordinates(part, rule, schema):
        expr = identifier(field)
        if upper is None:
            value = str(lower).replace("'", "''")
            result.append(f"CAST({expr} AS VARCHAR) IS DISTINCT FROM '{value}'")
        else:
            kind = "TIMESTAMPTZ" if isinstance(lower, datetime) else "DATE"
            result.append(
                f"({expr} IS NULL OR {expr}<{kind} '{lower.isoformat()}' "
                f"OR {expr}>={kind} '{upper.isoformat()}')"
            )
    return result


def check_decoded(batch: Any, part: str, rule: Any) -> None:
    import pyarrow as pa
    import pyarrow.compute as pc  # type: ignore[import-untyped]

    if not batch.num_rows:
        return
    if part == "empty":
        raise BoundedError("NATIVE_IMAGE_EMPTY_PARTITION_CHANGED")
    for field, lower, upper in coordinates(part, rule, batch.schema):
        array = batch.column(batch.schema.get_field_index(field))
        if upper is None:
            wrong = pc.not_equal(pc.cast(array, pa.string()), pa.scalar(str(lower)))
        else:
            wrong = pc.or_(
                pc.less(array, pa.scalar(lower, type=array.type)),
                pc.greater_equal(array, pa.scalar(upper, type=array.type)),
            )
        if pc.any(pc.or_(pc.is_null(array), pc.fill_null(wrong, True))).as_py():
            raise BoundedError("NATIVE_IMAGE_PARTITION_COORDINATE_CHANGED")
