"""Explicit input-only qualification of a new native Base 2.3 projection.

This operator entry point returns a candidate receipt; it never publishes it,
changes source admission, or runs a strategy. Normal consumers still require
an installed receipt and independently verify their actual materialization.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from quanthus.backtest import native_dwd_source
from quanthus.backtest.direct_dwd_provider import (
    _LOGICAL_PROJECTION_DATASETS,
    DATASETS,
    _digest,
    _logical_table_digest,
    _materialize,
    _native_lineage,
    _physical_direct_dwd_bundle,
)
from quanthus.policy import ConfigurationError


def qualify_native_projection(
    config: Any,
    *,
    root: Path,
    receipt_id: str,
    progress: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Build one empty-root TMP projection and return its real measured identity."""
    if not native_dwd_source.is_native(config) or not receipt_id.strip():
        raise ConfigurationError("native projection qualification requires its explicit contract")
    if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
        raise ConfigurationError("native projection qualification requires an empty TMP directory")
    native, correction, correction_manifest = native_dwd_source.verify_source(config)
    if progress is not None:
        progress("native-source-verified", {"binding": dict(native.binding)})
    counts = _materialize(
        root,
        dwd_root=config.dwd_root,
        correction_root=config.correction_root,
        history_start=config.history_start,
        execution_end=config.scope.execution_end,
        progress=progress,
    )
    dwd = dict(config.dwd_identity)
    bundle, files = _physical_direct_dwd_bundle(
        config,
        root=root,
        counts=counts,
        actual_dwd=dwd,
        actual_correction=correction,
        correction_manifest=correction_manifest,
    )
    datasets = {}
    for name in DATASETS:
        path = root / "tables" / f"{name}.parquet"
        datasets[name] = {
            "row_count": counts[name],
            "canonical_file_sha256": files[name],
            "canonical_file_bytes": path.stat().st_size,
            "logical_digest": (
                _logical_table_digest(path) if name in _LOGICAL_PROJECTION_DATASETS else None
            ),
        }
    lineage = {
        "materializer_version": native_dwd_source.MATERIALIZER_VERSION,
        "history_start": config.history_start.isoformat(),
        "dwd": dwd,
        "correction": dict(correction),
        "benchmark_sha256": config.benchmark_sha256,
        "industry_sha256": config.industry_sha256,
        "materialized_content_digest": bundle.digest,
        **_native_lineage(config),
    }
    return {
        "schema_version": "quanthus.direct-dwd-canonical-projection.v1",
        "receipt_id": receipt_id,
        "provider_config_digest": config.digest,
        "materializer_version": native_dwd_source.MATERIALIZER_VERSION,
        "history_start": config.history_start.isoformat(),
        "scope": config.scope.to_dict(),
        "dwd_authority": dwd,
        "dwd_correction": dict(correction),
        "canonical_bundle_digest": bundle.digest,
        "provider_lineage_digest": _digest(lineage),
        "datasets": datasets,
    }
