"""September cutoff with the unchanged bounded native global-key algorithm."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .base22_native_keys import NativeGlobalScan as AugustNativeScan
from .base23_sources import THROUGH


class NativeGlobalScan(AugustNativeScan):
    def _cutoff_predicate(self, cutoff: str, field: Any) -> str:
        import pyarrow as pa  # type: ignore[import-untyped]

        if pa.types.is_timestamp(field.type) and field.type.tz is not None:
            upper = datetime.combine(THROUGH+timedelta(days=1), time(),
                                     tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
            return f"{cutoff}>=TIMESTAMPTZ '{upper.isoformat()}'"
        expr = (f"CAST(timezone('Asia/Shanghai',{cutoff}) AS DATE)"
                if pa.types.is_timestamp(field.type) else cutoff)
        return f"{expr}>DATE '{THROUGH.isoformat()}'"
