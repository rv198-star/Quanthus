"""Bounded request correlation, JSON logging, and dependency-free metrics."""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
request_id_context: ContextVar[str] = ContextVar("platform_request_id", default="")


def request_id(incoming: str | None) -> str:
    candidate = (incoming or "").strip()
    return candidate if REQUEST_ID_PATTERN.fullmatch(candidate) else uuid.uuid4().hex


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "request_id": request_id_context.get(),
            },
            ensure_ascii=True,
            sort_keys=True,
        )


@dataclass(frozen=True)
class RequestMetric:
    method: str
    route: str
    status: int


class Metrics:
    def __init__(self) -> None:
        self._requests: Counter[RequestMetric] = Counter()
        self._duration_ms: Counter[tuple[str, str]] = Counter()
        self._lock = threading.Lock()

    def observe(self, *, method: str, route: str, status: int, duration_ms: int) -> None:
        with self._lock:
            self._requests[RequestMetric(method, route, status)] += 1
            self._duration_ms[(method, route)] += max(duration_ms, 0)

    def render(self) -> str:
        lines = [
            "# HELP quanthus_platform_http_requests_total Total platform HTTP requests.",
            "# TYPE quanthus_platform_http_requests_total counter",
        ]
        with self._lock:
            for metric, count in sorted(
                self._requests.items(),
                key=lambda item: (item[0].method, item[0].route, item[0].status),
            ):
                lines.append(
                    "quanthus_platform_http_requests_total"
                    f'{{method="{metric.method}",route="{metric.route}",status="{metric.status}"}} '
                    f"{count}"
                )
            lines.extend(
                (
                    "# HELP quanthus_platform_http_duration_milliseconds_total "
                    "Cumulative platform HTTP request duration.",
                    "# TYPE quanthus_platform_http_duration_milliseconds_total counter",
                )
            )
            for (method, route), duration in sorted(self._duration_ms.items()):
                lines.append(
                    "quanthus_platform_http_duration_milliseconds_total"
                    f'{{method="{method}",route="{route}"}} {duration}'
                )
        return "\n".join(lines) + "\n"
