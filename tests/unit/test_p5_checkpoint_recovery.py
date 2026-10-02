from __future__ import annotations

import copy
from pathlib import Path

import pytest

from quanthus.stage_learning import p5_compute_probe as probe
from quanthus.stage_learning.p5_assessment import P5AssessmentError, build_t0_model


def test_fresh_process_resume_matches_uninterrupted_across_epoch_boundary() -> None:
    pytest.importorskip("torch")
    result = probe.run_probe()
    assert result["uninterrupted_resume_exact_match"] is True
    assert result["measured_batch_ids"] == [2, 3, 4]
    assert result["checkpoint_resume"]["consumed_batch_id"] == 5
    assert result["checkpoint_resume"]["completed_batch_cursor"] == 6
    assert result["checkpoint_resume"]["completed_epoch_cursor"] == 2
    assert result["market_data_rows_read"] == 0


@pytest.fixture
def checkpoint():
    torch = pytest.importorskip("torch")
    model = build_t0_model()
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.99)
    return probe._checkpoint_payload(
        torch,
        model,
        optimizer,
        scheduler,
        batch_cursor=5,
        batch_size=32,
    )


@pytest.mark.parametrize("field", ["input_digest", "model_digest", "version_digest"])
def test_resume_rejects_changed_semantic_identity(checkpoint, field: str) -> None:
    torch = pytest.importorskip("torch")
    changed = copy.deepcopy(checkpoint)
    changed[field] = "sha256:" + "a" * 64
    with pytest.raises(P5AssessmentError, match="identity differs"):
        probe._validate_resume(torch, changed, 32)


def test_resume_rejects_batch_change_and_sampler_drift(checkpoint) -> None:
    torch = pytest.importorskip("torch")
    probe._validate_resume(torch, checkpoint, 32)
    with pytest.raises(P5AssessmentError, match="identity differs"):
        probe._validate_resume(torch, checkpoint, 64)
    checkpoint["sampler_state"]["next_batch"] = 0
    with pytest.raises(P5AssessmentError, match="sampler state"):
        probe._validate_resume(torch, checkpoint, 32)


def test_resume_rejects_epoch_and_rng_loss(checkpoint) -> None:
    torch = pytest.importorskip("torch")
    changed = copy.deepcopy(checkpoint)
    changed["epoch_cursor"] = 0
    with pytest.raises(P5AssessmentError, match="epoch cursor"):
        probe._validate_resume(torch, changed, 32)
    checkpoint["rng_state"].pop("python")
    with pytest.raises(P5AssessmentError, match="RNG state"):
        probe._validate_resume(torch, checkpoint, 32)


def test_resume_rejects_bytes_before_deserialization(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    path = tmp_path / "checkpoint.pt"
    path.write_bytes(b"untrusted invalid pickle")
    with pytest.raises(P5AssessmentError, match="byte identity"):
        probe.resume_only(path, tmp_path / "receipt.json", 32, "sha256:" + "0" * 64)
    assert not (tmp_path / "receipt.json").exists()


def test_sampler_generates_distinct_reproducible_batches() -> None:
    torch = pytest.importorskip("torch")
    first = probe._synthetic_batch(torch, 32, 0)[0]
    second = probe._synthetic_batch(torch, 32, 1)[0]
    assert not torch.equal(first, second)
    assert torch.equal(second, probe._synthetic_batch(torch, 32, 1)[0])
