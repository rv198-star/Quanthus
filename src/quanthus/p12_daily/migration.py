'''One-time, auditable ledger migration rehearsal for the daily advice source.

The paper ledger's configuration binding is append-only over one decision
source, so moving an existing store from the empty p12-paper-decisions source
to the daily-package source is a deliberate migration, not a re-open.  This
module rehearses it on a copy only (the 8790 public ledger is never touched):

1. Copy the ledger file.
2. Open the copy with the new daily-package source and let the
   application run its recorded one-time migration.
3. Compare every ledger surface - cash, positions, funding buckets, orders
   and receipts - between the untouched source and the migrated copy.
4. Roll back by discarding the copy; the original stays byte-identical.

The report names the recorded migration entry inside the new binding so the
exercise is auditable end to end.
'''

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.shared.fact_contracts import FactError


@dataclass(frozen=True)
class MigrationReport:
    # Evidence a rehearsal leaves behind: per-surface comparison plus the
    # recorded migration entry and rollback verification.
    source_store: Path
    rehearsal_store: Path
    migration_record: dict | None
    surfaces: dict[str, bool]
    consistent: bool
    source_intact: bool
    performed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_store": str(self.source_store),
            "rehearsal_store": str(self.rehearsal_store),
            "migration_record": self.migration_record,
            "surfaces": self.surfaces,
            "consistent": self.consistent,
            "source_intact": self.source_intact,
            "performed_at": self.performed_at,
        }


def ledger_surfaces(app) -> dict[str, Any]:
    # The surfaces a migration must preserve verbatim across the rebind.
    projection = app.paper_projection()
    history = app.paper_history()
    funding = None
    read_state = getattr(app, "funding_state", None)
    if callable(read_state):
        try:
            funding = app.funding_state(app.scope)
        except FactError:
            funding = None
    return {
        "cash": str(history.baseline_cash),
        "positions": sorted(
            (p.security_code, int(p.quantity)) for p in projection.positions
        ),
        "funding_buckets": (
            None
            if funding is None
            else sorted(
                (
                    b.bucket_id,
                    str(getattr(b, "budget", "")),
                    str(getattr(b, "phase", "")),
                )
                for b in funding[0].buckets
            )
        ),
        "orders": sorted(
            (o.order_id, o.security_code, o.status, o.accepted_total_qty)
            for o in history.orders
        ),
        "reports": sorted(
            (r.report_id, r.receipt_id, r.status, r.security_code, r.quantity)
            for r in history.reports
        ),
    }


def compare_surfaces(before: dict[str, Any], after: dict[str, Any]) -> dict[str, bool]:
    return {name: before.get(name) == after.get(name) for name in before}


def rehearse(
    *,
    source_store: Path,
    rehearsal_dir: Path,
    open_app: Callable[[Path], Any],
) -> MigrationReport:
    # Rehearse the source migration on a copied ledger.  open_app builds the
    # paper application on a given state dir with the daily-package source
    # bound; the migration itself happens inside build_paper_application's
    # recorded one-time path.
    source_store = Path(source_store)
    rehearsal_dir = Path(rehearsal_dir)
    if not source_store.is_file():
        raise FactError("FACT_NOT_FOUND", f"source ledger missing: {source_store}")
    rehearsal_dir.mkdir(parents=True, exist_ok=True)
    copy_store = rehearsal_dir / source_store.name
    before_bytes = source_store.read_bytes()
    shutil.copy2(source_store, copy_store)

    migrated_app = open_app(rehearsal_dir)
    after = ledger_surfaces(migrated_app)
    binding = migrated_app.facts_binding()
    migrations = (binding.binding.get("migrations") or []) if binding else []

    source_app = open_app(source_store.parent)
    before = ledger_surfaces(source_app)
    consistent_map = compare_surfaces(before, after)
    source_intact = source_store.read_bytes() == before_bytes
    return MigrationReport(
        source_store=source_store,
        rehearsal_store=copy_store,
        migration_record=migrations[-1] if migrations else None,
        surfaces=consistent_map,
        consistent=all(consistent_map.values()),
        source_intact=source_intact,
        performed_at=datetime.now(timezone.utc).isoformat(),
    )
