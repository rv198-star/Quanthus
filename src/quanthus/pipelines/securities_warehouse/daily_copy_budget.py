"""A pre-reserved copy batch keeps exact per-file integrity without quadratic rescans."""

from __future__ import annotations

import os
import time
from pathlib import Path

from .base22_bounded import BoundedError, Budget


class CopyReservation:
    def __init__(self, budget: Budget, targets: dict[Path, int]):
        self.budget = budget
        self.targets = {budget.destination(p): size for p, size in targets.items()}
        # Reserve all data plus conservative inode/directory allocation before writes.
        self.reserve = sum(
            2 * size + 65536 * len(path.relative_to(budget.root).parts)
            for path, size in self.targets.items()
        )
        budget.check(reserve=self.reserve)
        self.current_size = 0
        self.seen: set[Path] = set()

    def destination(self, path: Path) -> Path:
        path = self.budget.destination(path)
        if path not in self.targets or path in self.seen:
            raise BoundedError("DAILY_COPY_UNRESERVED_DESTINATION")
        self.seen.add(path)
        self.current_size = self.targets[path]
        return path

    def check(self, *, reserve: int = 0, extra_rss: int = 0) -> None:
        if self.budget.error:
            raise BoundedError(self.budget.error)
        if reserve > self.current_size:
            raise BoundedError("DAILY_COPY_MEMBER_EXCEEDS_RESERVATION")
        fs = os.statvfs(self.budget.root)
        if fs.f_bavail * fs.f_frsize - reserve < self.budget.limits.free_floor_bytes:
            raise BoundedError("FREE_SPACE_FLOOR_REACHED")
        rss = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        if rss + extra_rss > self.budget.limits.rss_bytes:
            raise BoundedError("RSS_LIMIT_EXCEEDED")
        if time.monotonic() - self.budget.started > self.budget.limits.wall_seconds:
            raise BoundedError("STAGE_WALL_LIMIT_EXCEEDED")

    def finish(self) -> None:
        if self.seen != set(self.targets):
            raise BoundedError("DAILY_COPY_BATCH_INCOMPLETE")
        self.budget.check()
