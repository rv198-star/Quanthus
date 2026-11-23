"""Materialize a produced package into one directory and receipt its digests.

Writes are append-only per `market_date`: an existing file for the same date is never
rewritten silently; a caller must bump `package.version` while keeping prior digests to extend
the set, matching P12's append-only binding rule.

Two digest surfaces exist and are kept distinct on purpose:

- `package_digests(package)` hashes the producer's own decision_set dict (canonical JSON,
  ensure_ascii=False) and receipts what was written.
- `p12_package_digests(decision_set, loader)` mirrors the digest the P12 account binding
  records for the same content: `digest(_package_content(MarketDecisionSet))`, i.e. the
  canonical `fact_contracts.digest` of `dataclasses.asdict` applied to the *validated*
  P12 `MarketDecisionSet`.  The caller passes the real P12 loader so the identity is the one
  the consumer actually binds.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

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
    """sha256 of each decision_set's canonical JSON (the file-content identity)."""

    return {
        item.market_date.isoformat(): "sha256:"
        + hashlib.sha256(_canonical_bytes(item.to_dict())).hexdigest()
        for item in package.decision_sets
    }


def p12_package_digests(decision_set_dict: dict[str, Any], loader) -> dict[str, str]:
    """Digest as the P12 account binding records it, computed through the P12 loader.

    `loader` is a callable receiving a decision_set dict and returning the P12-validated
    `MarketDecisionSet` (for example a small wrapper around
    `platform.adapters.p12_synthetic_fixture._decision` / `validate_decision_set`).  Using
    the real consumer-side object keeps this from drifting away from what P12 binds.
    """

    from quanthus.shared.fact_contracts import digest as p12_digest

    validated = loader(decision_set_dict)
    content = json.loads(json.dumps(dataclasses.asdict(validated), default=str, sort_keys=True))
    return {
        validated.market_date.isoformat(): p12_digest(content),
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
