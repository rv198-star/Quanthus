"""OCI compact-path compatibility without weakening immutable P6 identity."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.forward_validation.package_locator import resolve_daily_package

DATE = date(2026, 9, 23)
PACKAGE = "dwd-daily-20260923-31d9d426a212-ac89bfd"
SHORT = "31d9d426a212-ac89bfd"


def _publish(root: Path, name: str = SHORT) -> tuple[Path, str]:
    package = root / "packages" / DATE.isoformat() / name
    package.mkdir(parents=True)
    body = (
        json.dumps({"package_id": PACKAGE, "market_date": DATE.isoformat()}, sort_keys=True) + "\n"
    ).encode()
    (package / "manifest.json").write_bytes(body)
    (package / "READY").write_text("sha256:" + hashlib.sha256(body).hexdigest() + "\n")
    return package, "sha256:" + hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize("dirname", [SHORT, PACKAGE])
def test_resolves_both_publisher_layouts_without_changing_semantic_identity(
    tmp_path: Path, dirname: str
) -> None:
    directory, digest = _publish(tmp_path, dirname)
    assert resolve_daily_package(tmp_path, DATE, PACKAGE, manifest_sha256=digest) == directory
    assert (
        resolve_daily_package(
            tmp_path,
            DATE,
            PACKAGE,
            manifest_sha256=digest,
            manifest_path=f"packages/{DATE}/{dirname}/manifest.json",
        )
        == directory
    )


def test_rejects_pointer_escape_ambiguous_identity_and_checksum_drift(tmp_path: Path) -> None:
    compact, digest = _publish(tmp_path)
    for pointer in (
        "../../outside/manifest.json",
        f"packages/2026-09-22/{SHORT}/manifest.json",
        f"packages/{DATE}/invented/manifest.json",
    ):
        with pytest.raises(ForwardValidationContractError):
            resolve_daily_package(
                tmp_path, DATE, PACKAGE, manifest_sha256=digest, manifest_path=pointer
            )
    with pytest.raises(ForwardValidationContractError, match="manifest_drift"):
        resolve_daily_package(tmp_path, DATE, PACKAGE, manifest_sha256="sha256:" + "a" * 64)
    _publish(tmp_path, PACKAGE)
    with pytest.raises(ForwardValidationContractError, match="missing_or_ambiguous"):
        resolve_daily_package(tmp_path, DATE, PACKAGE, manifest_sha256=digest)
    (compact / "manifest.json").write_text("{}")
    with pytest.raises(ForwardValidationContractError):
        resolve_daily_package(
            tmp_path,
            DATE,
            PACKAGE,
            manifest_sha256=digest,
            manifest_path=f"packages/{DATE}/{SHORT}/manifest.json",
        )


def test_symlinked_package_is_not_an_admitted_mirror(tmp_path: Path) -> None:
    published, digest = _publish(tmp_path)
    symlink = published.parent / PACKAGE
    symlink.symlink_to(published, target_is_directory=True)
    with pytest.raises(ForwardValidationContractError, match="missing_or_ambiguous"):
        resolve_daily_package(
            tmp_path,
            DATE,
            PACKAGE,
            manifest_sha256=digest,
            manifest_path=f"packages/{DATE}/{PACKAGE}/manifest.json",
        )
