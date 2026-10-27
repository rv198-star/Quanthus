"""Bound an official issuer notice inventory without granting action admission.

HTTP, budgets and immutable response storage belong to the caller. This parser
rejects pagination stalls on their first occurrence, rather than trusting the
server's ``hasMore`` flag or requested page size. An inventory is not proof that
its action bodies, economic identity or historical knowledge times are qualified.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


class NoticeCensus:
    """Accept consecutive frozen responses; a rejected response changes no state."""

    def __init__(self, code: str, org_id: str, start: date, end: date) -> None:
        if (not code or not org_id or type(start) is not date
            or type(end) is not date or start > end):
            raise ValueError("NOTICE_CENSUS_SCOPE")
        self.code = code
        self.org_id = org_id
        self.start = start
        self.end = end
        self.pages = 0
        self.complete = False
        self._total: int | None = None
        self._physical_rows = 0
        self._notices: dict[str, dict[str, Any]] = {}

    @property
    def unique_count(self) -> int:
        return len(self._notices)

    @property
    def notices(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(self._notices[k]) for k in sorted(self._notices))

    @property
    def physical_row_count(self) -> int:
        return self._physical_rows

    @property
    def exact_duplicate_count(self) -> int:
        return self._physical_rows-len(self._notices)

    def accept(self, page: int, response: Mapping[str, Any]) -> None:
        if type(page) is not int or self.complete or page != self.pages+1:
            raise ValueError("NOTICE_CENSUS_PAGE_ORDER")
        rows = response.get("announcements")
        total = response.get("totalAnnouncement")
        more = response.get("hasMore")
        if (not isinstance(rows, list) or type(total) is not int
            or total < 0 or type(more) is not bool):
            raise ValueError("NOTICE_CENSUS_SCHEMA")
        if self._total is not None and total != self._total:
            raise ValueError("NOTICE_CENSUS_CHANGED_TOTAL")
        proposed = dict(self._notices)
        for row in rows:
            if not isinstance(row, dict) or row.get("secCode") != self.code or str(
                row.get("orgId")
            ) != self.org_id:
                raise ValueError("NOTICE_CENSUS_ISSUER")
            key = row.get("announcementId")
            millis = row.get("announcementTime")
            if not isinstance(key, str) or not key or type(millis) is not int:
                raise ValueError("NOTICE_CENSUS_NOTICE_IDENTITY")
            try:
                day = datetime.fromtimestamp(millis/1000, timezone.utc).astimezone(SHANGHAI).date()
            except (OverflowError, OSError, ValueError) as exc:
                raise ValueError("NOTICE_CENSUS_DATE") from exc
            if not self.start <= day <= self.end:
                raise ValueError("NOTICE_CENSUS_DATE")
            if key in proposed and proposed[key] != row:
                raise ValueError("NOTICE_CENSUS_CONFLICT")
            proposed[key] = deepcopy(row)
        if more and (not rows or len(proposed) == len(self._notices)):
            raise ValueError("NOTICE_CENSUS_NO_PROGRESS")
        # Publisher total counts returned records, including exact duplicate
        # records. Preserve that census separately from canonical notice keys.
        physical_rows = self._physical_rows+len(rows)
        if physical_rows > total:
            raise ValueError("NOTICE_CENSUS_COUNT")
        if not more and physical_rows != total:
            raise ValueError("NOTICE_CENSUS_TERMINAL_COUNT")
        self._notices = proposed
        self._physical_rows = physical_rows
        self._total = total
        self.pages = page
        self.complete = not more
