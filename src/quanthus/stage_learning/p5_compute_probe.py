"""Synthetic full-path CPU probe for P5-STAGE-001-C.

The probe hashes its implementation but reads no market data. It exercises the frozen T0 encoder,
masked BCE, backward, optimizer/scheduler update, checkpoint save and fresh-process resume.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import platform
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from quanthus.stage_learning.p5_assessment import (
    P5AssessmentError,
    TemporalEncoderConfig,
    build_t0_model,
    validate_checkpoint_payload,
)

PROBE_VERSION = "quanthus.p5-stage-001-cpu-probe.v2"
BATCHES_PER_EPOCH = 3


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _input_digest(batch_size: int) -> str:
    return _digest(
        json.dumps(
            {
                "schema": PROBE_VERSION,
                "batch_size": batch_size,
                "seed": 17,
                "sessions": 120,
                "channels": 24,
                "batches_per_epoch": BATCHES_PER_EPOCH,
            },
            sort_keys=True,
        ).encode()
    )


def _code_digest() -> str:
    from quanthus.stage_learning import p5_assessment

    return _digest(Path(__file__).read_bytes() + Path(p5_assessment.__file__).read_bytes())


def _synthetic_batch(
    torch: Any,
    batch_size: int,
    batch_cursor: int = 0,
) -> tuple[Any, Any, Any, Any, Any]:
    generator = torch.Generator(device="cpu").manual_seed(17 + batch_cursor)
    values = torch.randn(batch_size, 120, 24, generator=generator)
    actual_missing = torch.rand(batch_size, 120, 24, generator=generator) < 0.03
    artificial_mask = torch.rand(batch_size, 120, 24, generator=generator) < 0.10
    artificial_mask = torch.logical_and(artificial_mask, torch.logical_not(actual_missing))
    labels = torch.randint(0, 2, (batch_size, 12), generator=generator).float()
    label_mask = torch.ones(batch_size, 12, dtype=torch.bool)
    label_mask[:, 6:9] = False
    label_mask[::5, 0] = False
    return values, actual_missing, artificial_mask, labels, label_mask


def _step(
    torch: Any,
    model: Any,
    optimizer: Any,
    scheduler: Any,
    batch: tuple[Any, Any, Any, Any, Any],
) -> float:
    values, actual_missing, artificial_mask, labels, label_mask = batch
    optimizer.zero_grad(set_to_none=True)
    logits = model(values, actual_missing, artificial_mask)
    losses = torch.nn.functional.binary_cross_entropy_with_logits(logits, labels, reduction="none")
    loss = losses[label_mask].mean()
    if not torch.isfinite(loss):
        raise RuntimeError("synthetic CPU probe produced a non-finite loss")
    loss.backward()
    optimizer.step()
    scheduler.step()
    return float(loss.detach())


def _checkpoint_payload(
    torch: Any,
    model: Any,
    optimizer: Any,
    scheduler: Any,
    *,
    batch_cursor: int,
    batch_size: int,
) -> dict[str, Any]:
    return {
        "model_state": model.state_dict(),
        "optimizer_state": optimizer.state_dict(),
        "scheduler_state": scheduler.state_dict(),
        "rng_state": {
            "torch": torch.random.get_rng_state(),
            "python": random.getstate(),
        },
        "sampler_state": {
            "next_batch": batch_cursor % BATCHES_PER_EPOCH,
            "epoch": batch_cursor // BATCHES_PER_EPOCH,
            "batches_per_epoch": BATCHES_PER_EPOCH,
            "seed": 17,
        },
        "epoch_cursor": batch_cursor // BATCHES_PER_EPOCH,
        "batch_cursor": batch_cursor,
        "seed": 17,
        "input_digest": _input_digest(batch_size),
        "model_digest": _digest(b"p5-stage-001-t0-4x128x4x512-12-head-v1"),
        "version_digest": _digest((PROBE_VERSION + _code_digest() + torch.__version__).encode()),
    }


def _state_digest(torch: Any, value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, torch.Tensor):
            tensor = item.detach().cpu().contiguous()
            return {
                "dtype": str(tensor.dtype),
                "shape": list(tensor.shape),
                "sha256": _digest(tensor.numpy().tobytes()),
            }
        if isinstance(item, dict):
            return [[str(key), normalize(item[key])] for key in sorted(item, key=str)]
        if isinstance(item, (tuple, list)):
            return [normalize(child) for child in item]
        return item

    return _digest(json.dumps(normalize(value), sort_keys=True, allow_nan=False).encode())


def _validate_resume(torch: Any, checkpoint: dict[str, Any], batch_size: int) -> None:
    validate_checkpoint_payload(checkpoint)
    expected = {
        "input_digest": _input_digest(batch_size),
        "model_digest": _digest(b"p5-stage-001-t0-4x128x4x512-12-head-v1"),
        "version_digest": _digest((PROBE_VERSION + _code_digest() + torch.__version__).encode()),
    }
    if any(checkpoint[key] != value for key, value in expected.items()):
        raise P5AssessmentError("checkpoint input/model/code identity differs")
    cursor = checkpoint["batch_cursor"]
    if checkpoint["seed"] != 17 or checkpoint["epoch_cursor"] != cursor // BATCHES_PER_EPOCH:
        raise P5AssessmentError("checkpoint seed or epoch cursor differs")
    if checkpoint["sampler_state"] != {
        "next_batch": cursor % BATCHES_PER_EPOCH,
        "epoch": cursor // BATCHES_PER_EPOCH,
        "batches_per_epoch": BATCHES_PER_EPOCH,
        "seed": 17,
    }:
        raise P5AssessmentError("checkpoint sampler state differs")
    if set(checkpoint["rng_state"]) != {"torch", "python"}:
        raise P5AssessmentError("checkpoint RNG state differs")


def _rng_successor(torch: Any) -> dict[str, Any]:
    return {"torch": torch.rand(3).tolist(), "python": [random.random() for _ in range(3)]}


def resume_only(
    checkpoint_path: Path,
    receipt_path: Path,
    batch_size: int,
    expected_checkpoint_sha256: str,
) -> None:
    import torch

    torch.set_num_threads(max(1, min(8, os.cpu_count() or 1)))
    if checkpoint_path.is_symlink() or checkpoint_path.stat().st_size > 64 * 1024 * 1024:
        raise P5AssessmentError("checkpoint is not a bounded regular source")
    encoded = checkpoint_path.read_bytes()
    if _digest(encoded) != expected_checkpoint_sha256:
        raise P5AssessmentError("checkpoint byte identity differs")
    checkpoint = torch.load(io.BytesIO(encoded), map_location="cpu", weights_only=True)
    _validate_resume(torch, checkpoint, batch_size)
    model = build_t0_model()
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.99)
    model.load_state_dict(checkpoint["model_state"])
    optimizer.load_state_dict(checkpoint["optimizer_state"])
    scheduler.load_state_dict(checkpoint["scheduler_state"])
    torch.random.set_rng_state(checkpoint["rng_state"]["torch"])
    random.setstate(checkpoint["rng_state"]["python"])
    before = int(checkpoint["batch_cursor"])
    loss = _step(torch, model, optimizer, scheduler, _synthetic_batch(torch, batch_size, before))
    next_state = _checkpoint_payload(
        torch,
        model,
        optimizer,
        scheduler,
        batch_cursor=before + 1,
        batch_size=batch_size,
    )
    receipt_path.write_text(
        json.dumps(
            {
                "fresh_process_pid": os.getpid(),
                "resumed_from_batch_cursor": before,
                "completed_batch_cursor": before + 1,
                "finite_loss": math.isfinite(loss),
                "loss": loss,
                "consumed_batch_id": before,
                "completed_epoch_cursor": next_state["epoch_cursor"],
                "state_digest": _state_digest(torch, next_state),
                "rng_successor": _rng_successor(torch),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def run_probe(*, batch_size: int = 32) -> dict[str, Any]:
    import torch

    if batch_size not in {32, 64, 128}:
        raise ValueError("batch_size must be one of the preregistered 32/64/128 options")
    torch.manual_seed(17)
    random.seed(17)
    torch.set_num_threads(max(1, min(8, os.cpu_count() or 1)))
    config = TemporalEncoderConfig()
    model = build_t0_model(config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.99)
    warmup_losses = [
        _step(torch, model, optimizer, scheduler, _synthetic_batch(torch, batch_size, cursor))
        for cursor in range(2)
    ]
    timings: list[float] = []
    losses: list[float] = []
    sizes: list[int] = []
    with tempfile.TemporaryDirectory(prefix="p5-stage-cpu-probe-") as temporary:
        root = Path(temporary)
        checkpoint_path = root / "checkpoint.pt"
        for index in range(3):
            started = time.perf_counter()
            loss = _step(
                torch,
                model,
                optimizer,
                scheduler,
                _synthetic_batch(torch, batch_size, index + 2),
            )
            torch.save(
                _checkpoint_payload(
                    torch,
                    model,
                    optimizer,
                    scheduler,
                    batch_cursor=index + 3,
                    batch_size=batch_size,
                ),
                checkpoint_path,
            )
            timings.append(time.perf_counter() - started)
            losses.append(loss)
            sizes.append(checkpoint_path.stat().st_size)
        resume_receipt = root / "resume.json"
        checkpoint_sha256 = _digest(checkpoint_path.read_bytes())
        uninterrupted_loss = _step(
            torch,
            model,
            optimizer,
            scheduler,
            _synthetic_batch(torch, batch_size, 5),
        )
        uninterrupted_state = _checkpoint_payload(
            torch,
            model,
            optimizer,
            scheduler,
            batch_cursor=6,
            batch_size=batch_size,
        )
        uninterrupted_digest = _state_digest(torch, uninterrupted_state)
        uninterrupted_rng = _rng_successor(torch)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "quanthus.stage_learning.p5_compute_probe",
                "--resume-only",
                str(checkpoint_path),
                "--receipt-path",
                str(resume_receipt),
                "--batch-size",
                str(batch_size),
                "--checkpoint-sha256",
                checkpoint_sha256,
            ],
            check=True,
            env={**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", "src")},
        )
        resumed = json.loads(resume_receipt.read_text(encoding="utf-8"))
        if (
            resumed["state_digest"] != uninterrupted_digest
            or resumed["rng_successor"] != uninterrupted_rng
            or resumed["loss"] != uninterrupted_loss
            or resumed["consumed_batch_id"] != 5
            or resumed["completed_batch_cursor"] != 6
            or resumed["completed_epoch_cursor"] != 2
        ):
            raise P5AssessmentError("fresh-process resume differs from uninterrupted execution")
    ordered = sorted(timings)
    median = ordered[len(ordered) // 2]
    return {
        "schema_version": "quanthus.p5-stage-001-cpu-probe-result.v2",
        "device": "cpu",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "batch_size": batch_size,
        "parameter_count": config.parameter_count(),
        "warmup_steps": 2,
        "warmup_losses": warmup_losses,
        "measured_steps": 3,
        "end_to_end_seconds": timings,
        "median_end_to_end_seconds": median,
        "median_windows_per_second": batch_size / median,
        "measured_losses": losses,
        "checkpoint_bytes": sizes,
        "checkpoint_resume": resumed,
        "uninterrupted_resume_exact_match": True,
        "measured_batch_ids": [2, 3, 4],
        "input_digest": _input_digest(batch_size),
        "code_digest": _code_digest(),
        "nan_or_infinite_observed": False,
        "market_data_rows_read": 0,
        "model_results_read": 0,
        "limitations": [
            "T0 synthetic path only",
            "RX6800 absent from current host",
            "T1 masked reconstruction path not admitted by B/C pre-result gates",
            "node-hour estimate pending B window counts",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--output")
    parser.add_argument("--resume-only")
    parser.add_argument("--receipt-path")
    parser.add_argument("--checkpoint-sha256")
    args = parser.parse_args()
    if args.resume_only:
        if not args.receipt_path or not args.checkpoint_sha256:
            parser.error("--receipt-path and --checkpoint-sha256 are required with --resume-only")
        resume_only(
            Path(args.resume_only), Path(args.receipt_path), args.batch_size, args.checkpoint_sha256
        )
        return
    result = run_probe(batch_size=args.batch_size)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
