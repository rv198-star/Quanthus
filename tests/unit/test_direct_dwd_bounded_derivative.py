from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from quanthus.backtest import direct_dwd_provider as provider
from quanthus.policy import ConfigurationError

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64


def _receipt(dwd: dict[str, str], correction: dict[str, str]) -> dict[str, object]:
    return {
        "schema_version": "quanthus.direct-dwd-canonical-projection.v1",
        "receipt_id": "canonical-parent-v1",
        "provider_config_digest": SHA_A,
        "materializer_version": provider.MATERIALIZER_VERSION,
        "history_start": "2011-01-01",
        "scope": {
            "partition": "development",
            "decision_start": "2012-01-04",
            "decision_end": "2021-12-30",
            "execution_end": "2021-12-31",
            "reader_role": "development_research",
        },
        "dwd_authority": dwd,
        "dwd_correction": correction,
        "canonical_bundle_digest": SHA_B,
        "provider_lineage_digest": SHA_C,
        "datasets": {name: {} for name in provider.DATASETS},
    }


def _write_receipt(tmp_path: Path, payload: dict[str, object]) -> Path:
    target = tmp_path / provider._CANONICAL_PROJECTION_RECEIPT
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return target


def test_bounded_derivative_binds_parent_authority_without_requiring_parent_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dwd = {
        "package_id": "dwd-base",
        "through_date": "2026-07-31",
        "content_digest": SHA_A,
        "manifest_sha256": SHA_B,
        "relationship_to_replay_source": "direct-job-local-source",
    }
    correction = {
        "correction_id": "correction-v1",
        "content_digest": SHA_B,
        "manifest_sha256": SHA_C,
    }
    path = _write_receipt(tmp_path, _receipt(dwd, correction))
    monkeypatch.setattr(provider, "find_project_root", lambda: tmp_path)

    bundle_digest, receipt_sha, receipt_id = (
        provider._validate_parent_canonical_projection_authority(
            actual_dwd=dwd,
            actual_correction=correction,
        )
    )

    assert bundle_digest == SHA_B
    assert receipt_id == "canonical-parent-v1"
    assert receipt_sha == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_bounded_derivative_rejects_parent_authority_or_materializer_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dwd = {
        "package_id": "dwd-base",
        "through_date": "2026-07-31",
        "content_digest": SHA_A,
        "manifest_sha256": SHA_B,
        "relationship_to_replay_source": "direct-job-local-source",
    }
    correction = {
        "correction_id": "correction-v1",
        "content_digest": SHA_B,
        "manifest_sha256": SHA_C,
    }
    payload = _receipt(dwd, correction)
    path = _write_receipt(tmp_path, payload)
    monkeypatch.setattr(provider, "find_project_root", lambda: tmp_path)

    with pytest.raises(ConfigurationError, match="parent canonical authority"):
        provider._validate_parent_canonical_projection_authority(
            actual_dwd={**dwd, "content_digest": SHA_C},
            actual_correction=correction,
        )

    payload["materializer_version"] = "different-materializer"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="parent canonical authority"):
        provider._validate_parent_canonical_projection_authority(
            actual_dwd=dwd,
            actual_correction=correction,
        )
