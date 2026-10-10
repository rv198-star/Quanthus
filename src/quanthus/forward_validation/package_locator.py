"""Resolve immutable OCI DWD mirror package identities without guessing or rewriting them.

The source manifest's ``package_id`` is a semantic identity, *not* a physical directory
basename. OCI's warehouse mirror preserves the publisher's dated ``manifest_path``, whose
leaf is a compact suffix. Both bounded layouts are admitted only with matching manifest
identity and exact SHA; no recursive scan or fallback to CURRENT is allowed.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path, PurePosixPath

from quanthus.forward_validation.contracts import fail

_PACKAGE_ID = re.compile(r"^dwd-daily-\d{8}-[0-9a-z]{1,64}(?:-[0-9a-z]{1,64})?$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def resolve_daily_package(
    daily_root: Path,
    market_date: date,
    package_id: str,
    *,
    manifest_sha256: str,
    manifest_path: str | None = None,
) -> Path:
    """Return the exact dated package, verifying path, manifest identity and SHA.

    ``manifest_path`` is authoritative when provided. For historical prior links that only
    contain package identity and digest, try exactly two publisher-recognized directory
    layouts; reject missing/ambiguous inputs. Never perform arbitrary directory discovery.
    """
    if not _PACKAGE_ID.fullmatch(package_id) or not package_id.startswith(
        f"dwd-daily-{market_date:%Y%m%d}-"
    ):
        fail("prospective_package_identity_invalid", "dated package_id is invalid")
    if not _DIGEST.fullmatch(manifest_sha256):
        fail("prospective_package_digest_invalid", "package manifest SHA is invalid")
    base = daily_root.resolve()
    parent = base / "packages" / market_date.isoformat()
    suffix = package_id.removeprefix(f"dwd-daily-{market_date:%Y%m%d}-")
    if manifest_path is not None:
        rel = PurePosixPath(manifest_path)
        if (
            rel.is_absolute()
            or ".." in rel.parts
            or len(rel.parts) != 4
            or rel.parts[0:2] != ("packages", market_date.isoformat())
            or rel.parts[2] not in {package_id, suffix}
            or rel.parts[3] != "manifest.json"
        ):
            fail(
                "prospective_package_path_invalid",
                "CURRENT manifest_path is not the bound dated package",
            )
        choices = (base / Path(*rel.parts[:-1]),)
    else:
        choices = (parent / package_id, parent / suffix)
    matches: list[Path] = []
    for location in choices:
        if location.is_symlink() or not location.is_dir():
            continue
        path = location / "manifest.json"
        if path.is_symlink() or not path.is_file():
            continue
        if base not in path.resolve().parents:
            fail("path_escape", "package manifest escapes daily root")
        data = path.read_bytes()
        if f"sha256:{hashlib.sha256(data).hexdigest()}" != manifest_sha256:
            fail(
                "prospective_package_manifest_drift",
                "physical package SHA does not match frozen binding",
            )
        try:
            manifest = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            fail("prospective_package_manifest_invalid", type(exc).__name__)
        if not isinstance(manifest, dict) or (
            manifest.get("package_id"),
            manifest.get("market_date"),
        ) != (package_id, market_date.isoformat()):
            fail(
                "prospective_package_identity_drift",
                "physical manifest differs from full semantic identity",
            )
        matches.append(location.resolve())
    if len(matches) != 1:
        fail("prospective_package_missing_or_ambiguous", f"{market_date.isoformat()}: {package_id}")
    return matches[0]
