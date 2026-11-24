"""Pinned source members for the bounded history reader (issue #749).

Every member the reader may open is declared in the frozen P10 H1
mapping package (BASE-MEMBERS.json for the parent Base,
DAILY-MEMBERS.json for the 2026-11 daily increments).  The reader
resolves a member against these maps, re-hashes its bytes, and refuses
an unmapped or mismatched path - it never guesses a location.

Members carry a member_id (the package-relative member path) so the
same id is used in the raw_history source_members[] list and in each
calendar/listing row's source_member_id back-reference.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

BASE_ROOT = Path(
    "/srv/quanthus-base22-data/build/base25-20261031/work/"
    "paired-base251-adce892e-a1/published/DWD"
)
BASE_PACKAGE_ID = "standard-base-2.5.1-8cd966be87fc"
BASE_VERSION = "2.5.1"
BASE_MANIFEST_SHA256 = (
    "sha256:8cd966be87fce02168c54e77c23e05f491c4afb204c4cbace42a2024eee1cdef"
)
BASE_BUSINESS_THROUGH = "2026-10-31"
# Members are addressed payload/<table>/... under the published DWD root.
BASE_PACKAGE_ROOT = BASE_ROOT

H1_BASE_MEMBERS = Path(
    "/Users/william/Documents/Codex/2026-11-24/"
    "quanthus-p10-h1-history-supply/v1/BASE-MEMBERS.json"
)
H1_DAILY_MEMBERS = Path(
    "/Users/william/Documents/Codex/2026-11-24/"
    "quanthus-p10-h1-history-supply/v1/DAILY-MEMBERS.json"
)


def sha256_file(path: Path) -> str:
    d = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            d.update(chunk)
    return "sha256:" + d.hexdigest()


@dataclass(frozen=True)
class ResolvedMember:
    """One named source member, resolved to an absolute path + pinned sha.

    member_id is the package-relative member path, used both as the
    raw_history source_members[] key and as calendar/listing rows'
    source_member_id back-reference.
    """

    member_id: str  # package-relative member path
    package_id: str
    version: str
    abs_path: Path
    sha256: str
    size_bytes: int
    readability: str
    package_kind: str  # "base" | "daily-core"
    market_date: str | None = None  # daily-core only


@dataclass(frozen=True)
class DailyCorePackage:
    """A pinned 2026-11 daily-core package for one OPEN market date."""

    market_date: str
    package_id: str
    manifest_path: Path
    manifest_sha256: str
    members: tuple[ResolvedMember, ...]


class SourceMap:
    """Resolve pinned members from the frozen H1 mapping package."""

    def __init__(
        self,
        base_members_path: Path = H1_BASE_MEMBERS,
        daily_members_path: Path = H1_DAILY_MEMBERS,
        base_package_root: Path = BASE_PACKAGE_ROOT,
    ) -> None:
        self.base_package_root = Path(base_package_root)
        self._base_doc = json.loads(Path(base_members_path).read_text())
        self._daily_doc = json.loads(Path(daily_members_path).read_text())
        self._base_tables = self._base_doc.get("tables") or {}
        self._daily_entries = self._daily_doc.get("entries") or {}

    # ---- Base -----------------------------------------------------------
    def base_members(self, table: str) -> list[ResolvedMember]:
        """All pinned members of one Base table (every partition file)."""
        entry = self._base_tables.get(table) or {}
        out = []
        for tup in entry.get("members") or []:
            rel = tup[0]
            out.append(
                ResolvedMember(
                    member_id=rel,
                    package_id=BASE_PACKAGE_ID,
                    version=BASE_VERSION,
                    abs_path=self.base_package_root / rel,
                    sha256=tup[1],
                    size_bytes=int(tup[2]),
                    readability=tup[3] if len(tup) > 3 else "UNKNOWN",
                    package_kind="base",
                    market_date=None,
                )
            )
        return out

    # ---- Daily core ---------------------------------------------------------
    def daily_open_days(self) -> list[str]:
        return sorted(
            d for d, e in self._daily_entries.items()
            if e.get("session_class") == "OPEN"
        )

    def daily_session_class(self, market_date: str) -> str | None:
        e = self._daily_entries.get(market_date)
        return None if e is None else e.get("session_class")

    def daily_core(self, market_date: str) -> DailyCorePackage | None:
        """The pinned daily-core package for one market date, or None."""
        e = self._daily_entries.get(market_date)
        if e is None:
            return None
        core = e.get("core_manifest") or {}
        manifest_path = core.get("path")
        members = []
        for tup in core.get("declared_members") or []:
            rel = tup[0]
            members.append(
                ResolvedMember(
                    member_id=rel,
                    package_id=core.get("package_id") or "",
                    version=str(core.get("schema_version") or "2"),
                    abs_path=(
                        Path(manifest_path).parent / rel if manifest_path else Path(rel)
                    ),
                    sha256=tup[1],
                    size_bytes=int(tup[2]),
                    readability=tup[3] if len(tup) > 3 else "UNKNOWN",
                    package_kind="daily-core",
                    market_date=market_date,
                )
            )
        return DailyCorePackage(
            market_date=market_date,
            package_id=core.get("package_id") or "",
            manifest_path=Path(manifest_path) if manifest_path else Path(""),
            manifest_sha256=core.get("sha256") or "",
            members=tuple(members),
        )

    def daily_member(
        self, market_date: str, name_fragment: str
    ) -> ResolvedMember | None:
        """One daily-core member whose relative path contains the fragment."""
        pkg = self.daily_core(market_date)
        if pkg is None:
            return None
        for m in pkg.members:
            if name_fragment in m.member_id:
                return m
        return None

    # ---- verification ---------------------------------------------------------
    def verify_member(self, member: ResolvedMember) -> dict:
        """Re-hash a member's bytes and compare to its pinned sha."""
        actual = sha256_file(member.abs_path)
        return {
            "member_id": member.member_id,
            "path": str(member.abs_path),
            "declared_sha256": member.sha256,
            "actual_sha256": actual,
            "matches": actual == member.sha256,
            "size_bytes": (
                member.abs_path.stat().st_size if member.abs_path.exists() else None
            ),
        }
