from __future__ import annotations

import json
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.learning_runner import StageLearningRunError, _require_authority

ROOT = find_project_root()


def test_current_delivery_plan_keeps_real_semantic_training_fail_closed() -> None:
    with pytest.raises(StageLearningRunError, match="training authority is not open"):
        _require_authority(ROOT)


def test_learning_authority_can_open_only_without_downstream_scope(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    plan = json.loads((ROOT / "config/stock-stage-learning-delivery.v1.json").read_text())
    plan["authorization"]["training_authorized"] = True
    (config / "stock-stage-learning-delivery.v1.json").write_text(json.dumps(plan))
    loaded = _require_authority(tmp_path)
    assert loaded["authorization"]["training_authorized"] is True

    plan["authorization"]["sealed_partition_access_authorized"] = True
    (config / "stock-stage-learning-delivery.v1.json").write_text(json.dumps(plan))
    with pytest.raises(StageLearningRunError, match="forbidden downstream"):
        _require_authority(tmp_path)


def test_learning_runner_source_has_no_future_settlement_consumer() -> None:
    source = (ROOT / "src/quanthus/stage_learning/learning_runner.py").read_text()
    assert "settlements.jsonl" not in source
    assert "settle_reference_contexts" not in source
    assert '"future_settlement_read": False' in source
    assert '"arbitration_read": False' in source
    assert '"permanent_blind_read": False' in source
