"""Validate the R11 reader identity before the formal replay reads any rows."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from quanthus.backtest.data_provider import ReplayDataProviderConfig, _sha256
from quanthus.backtest.direct_dwd_provider import (
    _CANONICAL_PROJECTION_RECEIPT,
    MATERIALIZER_VERSION,
    _digest,
    _direct_dwd_authority,
    _object,
)
from quanthus.policy import ConfigurationError, find_project_root


def expected_r11_reader_manifest(config: ReplayDataProviderConfig) -> str:
    """Resolve the frozen reader identity from the provider contract."""
    if config.mode == "shared-replay-projection":
        manifest = config.replay_identity["manifest_sha256"]
    elif config.mode == "direct-dwd-job-local":
        if config.history_start is None or config.correction_identity is None:
            raise ConfigurationError("canonical direct DWD requires history and correction")
        receipt = _object(
            find_project_root(config.path) / _CANONICAL_PROJECTION_RECEIPT,
            "direct DWD canonical projection receipt",
        )
        binding = {
            "schema_version": "quanthus.direct-dwd-canonical-projection.v1",
            "provider_config_digest": config.digest,
            "materializer_version": MATERIALIZER_VERSION,
            "history_start": config.history_start.isoformat(),
            "scope": config.scope.to_dict(),
            "dwd_authority": dict(config.dwd_identity),
            "dwd_correction": dict(config.correction_identity),
        }
        if any(receipt.get(field) != value for field, value in binding.items()):
            raise ConfigurationError("direct DWD canonical projection receipt binding differs")
        bundle_digest = receipt.get("canonical_bundle_digest")
        if not isinstance(bundle_digest, str) or not re.fullmatch(
            r"sha256:[a-f0-9]{64}", bundle_digest
        ):
            raise ConfigurationError("direct DWD canonical bundle identity is invalid")
        manifest = _digest(
            {
                "materializer_version": MATERIALIZER_VERSION,
                "history_start": config.history_start.isoformat(),
                "dwd": dict(config.dwd_identity),
                "correction": dict(config.correction_identity),
                "benchmark_sha256": config.benchmark_sha256,
                "industry_sha256": config.industry_sha256,
                "materialized_content_digest": bundle_digest,
            }
        )
        if manifest != receipt.get("provider_lineage_digest"):
            raise ConfigurationError("direct DWD canonical provider lineage differs")
    else:
        raise ConfigurationError("this replay provider mode exposes no admissible reader manifest")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", manifest):
        raise ConfigurationError("invalid replay reader manifest")
    return manifest


def _require_manifest_digest(path: Path, expected: str, label: str) -> None:
    try:
        observed = _sha256(path)
    except OSError as exc:
        raise ConfigurationError(f"cannot read {label} manifest: {path}") from exc
    if observed != expected:
        raise ConfigurationError(f"{label} identity differs from provider configuration")


def verify_r11_reader_admission(
    config: ReplayDataProviderConfig, admitted: str, *, scope_start: date, scope_end: date
) -> None:
    """Verify both the declared digest and its physical metadata without loading data tables."""
    config.scope.authorize(scope_start, scope_end)
    if expected_r11_reader_manifest(config) != admitted:
        raise ConfigurationError("R11 provider manifest does not match the admitted data")
    if config.mode == "direct-dwd-job-local":
        _direct_dwd_authority(config, None)
    else:
        _require_manifest_digest(config.replay_root / "manifest.json", admitted, "shared replay")
        _require_manifest_digest(
            config.dwd_root / "manifest.json",
            config.dwd_identity["manifest_sha256"],
            "DWD authority",
        )
