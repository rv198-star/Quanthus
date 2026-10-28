"""File and execution identities for the bounded #545 offline runner.

These checks qualify stage inputs, not research value or production admission.
The broad Python/config closure intentionally prefers a cache miss to an omitted
transitive dependency. Historical eleven-file freezes remain sealed evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

HELD_POPULATION = (8393, 343, 538)
INPUT_KIND = "expert545-held-structure-inputs-v2"


def file_sha(path: Path) -> str:
    """Reject aliases and concurrent changes instead of hashing a moving target."""
    if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file():
        raise ValueError("HELD_NONPHYSICAL_FILE:" + str(path))
    before = path.stat()
    value = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        for block in iter(lambda: stream.read(8 * 1024**2), b""):
            value.update(block)
        after = os.fstat(stream.fileno())
    final = path.stat()
    identities = {
        (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        for s in (before, opened, after, final)
    }
    if len(identities) != 1:
        raise ValueError("HELD_FILE_CHANGED_DURING_READ:" + str(path))
    return value.hexdigest()


def _scan_error(error: OSError) -> None:
    raise error


def execution_pins(root: Path) -> dict[str, str]:
    """Include all project Python, config and dependency declarations, plus both CLIs."""
    root = root.resolve(strict=True)
    paths = []
    for directory in (root / "src/quanthus", root / "config"):
        if directory.resolve(strict=True) != directory:
            raise ValueError("HELD_CODE_CLOSURE_SYMLINK")
        for parent, dirs, files in os.walk(directory, onerror=_scan_error, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            if any((Path(parent) / d).is_symlink() for d in dirs):
                raise ValueError("HELD_CODE_CLOSURE_SYMLINK")
            for name in sorted(files):
                path = Path(parent) / name
                if directory.name == "config" or path.suffix == ".py":
                    paths.append(path)
    paths.extend(
        root / "scripts" / name
        for name in ("expert545_held_structure.py", "expert545_capture_b_trace.py")
    )
    paths.extend(
        p
        for p in root.iterdir()
        if p.is_file()
        and (
            p.name in {"pyproject.toml", "uv.lock", "poetry.lock"}
            or p.name.startswith("requirements")
            and p.suffix == ".txt"
        )
    )
    return {p.relative_to(root).as_posix(): file_sha(p) for p in sorted(paths)}


def verify_execution(root: Path, frozen: dict[str, Any]) -> None:
    if frozen.get("kind") != INPUT_KIND:
        raise ValueError("HELD_LEGACY_INCOMPLETE_CODE_CLOSURE")
    expected = frozen.get("code_config_sha256")
    observed = execution_pins(root)
    if not isinstance(expected, dict) or set(expected) != set(observed):
        raise ValueError("HELD_CODE_CLOSURE")
    for name, actual in observed.items():
        if expected[name] != actual:
            raise ValueError("HELD_FROZEN_CODE_CHANGED:" + name)
    # Disk identities alone do not prove which copy Python actually imported.
    source = root.resolve() / "src"
    for name, module in tuple(sys.modules.items()):
        if name != "quanthus" and not name.startswith("quanthus."):
            continue
        origin = getattr(module, "__file__", None)
        if origin is None:
            raise ValueError("HELD_IMPORTED_CODE_ORIGIN:" + name)
        path = Path(origin).resolve(strict=True)
        if not path.is_relative_to(source):
            raise ValueError("HELD_IMPORTED_CODE_ORIGIN:" + name)
        key = path.relative_to(root.resolve()).as_posix()
        if key not in expected or file_sha(path) != expected[key]:
            raise ValueError("HELD_IMPORTED_CODE_ORIGIN:" + name)


def verified_held(
    frozen: dict[str, Any], *, population: tuple[int, int, int]
) -> list[dict[str, Any]]:
    path = Path(frozen["held_file"])
    if file_sha(path) != frozen["held_sha256"]:
        raise ValueError("HELD_POPULATION_BYTES")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != frozen["held_sha256"]:
        raise ValueError("HELD_POPULATION_BYTES")
    rows: list[dict[str, Any]] = json.loads(payload)["rows"]["B"]
    keys = set()
    for row in rows:
        if (
            any(
                not isinstance(row.get(k), str) or not row[k]
                for k in ("security_code", "market_date", "unit_id", "entry_key")
            )
            or type(row.get("quantity")) is not int
            or row["quantity"] < 0
            or date.fromisoformat(row["market_date"]).isoformat() != row["market_date"]
        ):
            raise ValueError("HELD_POPULATION_IDENTITY")
        key = row["market_date"], row["unit_id"]
        if key in keys:
            raise ValueError("HELD_POPULATION_DUPLICATE")
        keys.add(key)
    if (
        len(rows),
        len({r["security_code"] for r in rows}),
        len({r["entry_key"] for r in rows}),
    ) != population:
        raise ValueError("HELD_FROZEN_DENOMINATOR")
    return rows


def verify_selected_sources(frozen: dict[str, Any]) -> None:
    selected = {*frozen["selected"].values(), *frozen["supplemental"]}
    if set(frozen["source_pins"]) != selected:
        raise ValueError("HELD_SELECTED_SOURCE_CLOSURE")
    for name, identity in frozen["source_pins"].items():
        path = Path(name)
        if (
            file_sha(path) != identity["sha256"].removeprefix("sha256:")
            or path.stat().st_size != identity["bytes"]
        ):
            raise ValueError("HELD_SELECTED_SOURCE_CHANGED:" + name)
