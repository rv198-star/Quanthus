"""Lightweight canonical Base 2.4 runtime contract shared by Worker and read-only API.

Only repository policy/configuration bytes are opened. No market reader, source
recovery, queue submission or activation authority belongs to this module.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quanthus.forward_validation.activation import (
    _object,
    _repo_binding,
    _sha,
    load_prospective_runtime_config,
)
from quanthus.forward_validation.contracts import fail

CLOCK_VERSION = "quanthus.p6-after-close-decision-clock.v1"
RUNTIME_SCHEMA = "quanthus.p6-base24-cycle-runtime.v1"
RELEASE_SCHEMA = "quanthus.p6-base24-qualified-daily.v1"


@dataclass(frozen=True)
class P6Base24CycleRuntime:
    path: Path
    config_sha256: str
    root: Path
    bindings: Mapping[str, Path]
    daily_root: Path


def load_runtime(path: Path, *, root: Path) -> P6Base24CycleRuntime:
    """Reuse frozen *policy* bindings, never the legacy source/activation path."""
    raw = _object(path, "Base 2.4 cycle runtime")
    reference = root / "config/forward-validation/activation/p6-prospective-runtime.v1.json"
    expected = {
        "schema_version": RUNTIME_SCHEMA,
        "work_item": "P6-R1",
        "state": "PRE_ACTIVATION",
        "frozen_policy_reference": {
            "path": str(reference.relative_to(root)),
            "sha256": _sha(reference),
        },
        "qualified_daily": {
            "root_env": "QUANTHUS_P6_BASE24_DAILY_ROOT",
            "default_root": "/data/p6-base24-daily",
            "schema_version": RELEASE_SCHEMA,
            "legacy_equivalence": False,
        },
        "clock_version": CLOCK_VERSION,
        "source_scope": "qualified_action_free_window_with_per_listing_holds",
        "first_official_session": "strictly_after_new_activation_market_date",
        "authority": {
            "official_execution_authorized": False,
            "paper_only": True,
            "notification_authorized": False,
            "broker_authorized": False,
            "live_capital_authorized": False,
            "protected_historical_partitions_authorized": False,
        },
    }
    if raw != expected:
        fail("p6_base24_cycle_runtime_drift", "explicit successor contract differs")
    _repo_binding(root, raw["frozen_policy_reference"], "frozen_policy_reference")
    # This loader checks repository bytes only. None of its legacy data paths is
    # opened or copied to the successor config.
    frozen = load_prospective_runtime_config(reference, root=root)
    daily_root = Path(os.environ.get("QUANTHUS_P6_BASE24_DAILY_ROOT", "/data/p6-base24-daily"))
    if not daily_root.is_absolute():
        fail("path_escape", "Base 2.4 qualified Daily root must be absolute")
    return P6Base24CycleRuntime(
        path.resolve(), _sha(path), root.resolve(), frozen.bindings, daily_root.resolve()
    )


def load_cycle_runtime(path: Path, *, root: Path) -> Any:
    schema = _object(path, "P6 runtime").get("schema_version")
    if schema == RUNTIME_SCHEMA:
        return load_runtime(path, root=root)
    return load_prospective_runtime_config(path, root=root)
