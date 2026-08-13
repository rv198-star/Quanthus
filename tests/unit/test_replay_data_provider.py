from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from quanthus.backtest.data_provider import load_replay_data_provider_config
from quanthus.policy import ConfigurationError

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "replay-data-provider.development.v1.json"
DIRECT_CONFIG = ROOT / "config" / "replay-data-provider.development.v2.json"


def test_default_provider_reuses_one_development_projection() -> None:
    config = load_replay_data_provider_config(CONFIG)

    assert config.provider_id == "development-shared-replay-v1"
    assert config.scope.partition == "development"
    assert config.scope.reader_role == "development_research"
    assert config.materialization["ordinary_replay_requires_dedicated_ads"] is False
    assert config.materialization["shared_projection_reused"] is True
    assert config.replay_identity["bundle_id"] == (
        "strategy-replay-development-1f1ea4d-v1"
    )
    assert config.dwd_identity["package_id"] == "dwd-base-202607-v2.1"
    assert config.dwd_identity["relationship_to_replay_source"] == (
        "authority-baseline-not-direct-replay-parent"
    )


def test_direct_provider_materializes_one_job_local_projection_from_dwd() -> None:
    config = load_replay_data_provider_config(DIRECT_CONFIG)

    assert config.provider_id == "development-direct-dwd-job-local-v1"
    assert config.mode == "direct-dwd-job-local"
    assert config.replay_identity == {}
    assert config.dwd_identity["relationship_to_replay_source"] == "direct-job-local-source"
    assert config.correction_identity == {
        "correction_id": "dwd-base-202607-v2.1-correction-20260813-001",
        "content_digest": (
            "sha256:1c1d1f4ea906797255fbc71cc3915bb905e51e7aea202d27042e33692b72e9da"
        ),
        "manifest_sha256": (
            "sha256:4c8eeb5c0d74bbf2953f003364fd138832f15a5163254b710f9db8430767ffde"
        ),
    }
    assert config.materialization["job_local_projection"] is True
    assert config.history_start == date(2011, 1, 1)
    assert config.materialization["per_decision_publication_proven"] is False


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("scope", "partition"), "arbitration", "development only"),
        (
            ("dwd_authority", "relationship_to_replay_source"),
            "direct-parent",
            "relationship does not match",
        ),
        (
            ("materialization", "ordinary_replay_requires_dedicated_ads"),
            True,
            "must not require a dedicated ADS",
        ),
    ],
)
def test_provider_rejects_scope_lineage_and_materialization_shortcuts(
    tmp_path: Path,
    path: tuple[str, str],
    value: object,
    message: str,
) -> None:
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    payload[path[0]][path[1]] = value
    candidate = tmp_path / "provider.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConfigurationError, match=message):
        load_replay_data_provider_config(candidate)
