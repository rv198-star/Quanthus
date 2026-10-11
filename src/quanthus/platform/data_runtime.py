"""Explicit deployment selection for new native-source jobs.

The default preserves historical deployments. OCI enables the successor only
after its versioned source/projection receipts and operational gates pass.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from quanthus.policy import ConfigurationError, find_project_root


def native_consumers_enabled() -> bool:
    raw = os.environ.get("QUANTHUS_NATIVE_BASE23_CONSUMERS", "0")
    if raw not in {"0", "1"}:
        raise ConfigurationError("native Base 2.3 deployment selection must be 0 or 1")
    return raw == "1"


def replay_provider_path() -> str:
    if not native_consumers_enabled():
        return "config/replay-data-provider.development.v2.json"
    relative = "config/replay-data-provider.base23.v1.json"
    root = find_project_root()
    try:
        config = json.loads((root / relative).read_text(encoding="utf-8"))
        receipt = json.loads(
            (root / "config/p4-direct-dwd-canonical-projection.base23.v1.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError) as exc:
        raise ConfigurationError("native runtime source configuration is not published") from exc
    if not isinstance(config, dict) or not isinstance(receipt, dict):
        raise ConfigurationError("native runtime source configuration must be an object")
    materialization = config.get("materialization")
    if not isinstance(materialization, dict):
        raise ConfigurationError("native runtime source materialization is absent")
    config_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(config, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    if (
        config.get("research_only") is not True
        or config.get("execution_authorized") is not False
        or materialization.get("source_layout") != "base23-native-v1"
        or receipt.get("schema_version") != "quanthus.direct-dwd-canonical-projection.v1"
        or receipt.get("materializer_version") != "direct-dwd-native-base23-v1"
        or receipt.get("provider_config_digest") != config_digest
    ):
        raise ConfigurationError("native runtime canonical input receipt differs")
    return relative


def event_source_policy_path() -> str:
    if not native_consumers_enabled():
        return "config/p4-event-scan-source.research.v1.json"
    # Fail before accepting a new native job if the input receipt is absent.
    replay_provider_path()
    return "config/p4-event-scan-source.base23.v1.json"


def resolve_cli_source_defaults(args: Any) -> None:
    """Select defaults for new input jobs while preserving explicit historical paths."""
    event_commands = {
        "run-p4-event-scan",
        "run-p4-labeled-event-scan",
        "run-p4-reported-event-scan",
        "build-p4-label-corpus",
        "build-p4-comparison-report",
    }
    if args.command in event_commands:
        if args.provider_config is None:
            args.provider_config = Path(replay_provider_path())
        if hasattr(args, "source_policy") and args.source_policy is None:
            args.source_policy = Path(event_source_policy_path())
    elif (
        args.command == "run-candidate-batch"
        and args.provider_config is None
        and native_consumers_enabled()
    ):
        args.provider_config = Path(replay_provider_path())
