"""Materialize a produced package into one directory and receipt its digests.

Writes are append-only per `market_date`: an existing file for the same date is never
rewritten silently; a caller must bump `package.version` while keeping prior digests to extend
the set, matching P12's append-only binding rule.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from quanthus.product_daily.contracts import ProductDailyError, require_aware
from quanthus.product_daily.recommendation.contracts import (
    DailyAdvicePackage,
    PublishReceipt,
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def package_digests(package: DailyAdvicePackage) -> dict[str, str]:
    return {
        item.market_date.isoformat(): "sha256:"
        + hashlib.sha256(_canonical_bytes(item.to_dict())).hexdigest()
        for item in package.decision_sets
    }


def publish_package(
    package: DailyAdvicePackage,
    *,
    directory: Path | str,
    published_at: datetime,
    elapsed_ms: int = 0,
) -> PublishReceipt:
    require_aware(published_at, "published_at")
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    digests = package_digests(package)
    path = target_dir / f"{package.package_id}.v{package.version}.json"
    if path.exists():
        raise ProductDailyError(
            "idempotency_conflict",
            f"{path} already exists; bump package.version to extend the append-only set",
        )
    path.write_text(
        json.dumps(package.to_dict(), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    decision_count = sum(len(item.decisions) for item in package.decision_sets)
    entry_count = sum(
        1 for item in package.decision_sets for d in item.decisions if d.stage == "entry_plan"
    )
    return PublishReceipt(
        package_id=package.package_id,
        version=package.version,
        path=str(path),
        package_digests=tuple(sorted(digests.items())),
        decision_count=decision_count,
        entry_plan_count=entry_count,
        admission_disposition=package.decision_sets[0].audit.admission_disposition
        if package.decision_sets
        else "empty_advice",
        published_at=published_at,
        elapsed_ms=elapsed_ms,
    )
