"""Synthetic full-path CPU probe for P5-STAGE-001-C.

The probe reads no repository or market data.  It exercises the frozen T0 encoder, masked BCE,
backward, optimizer/scheduler update, checkpoint save, a fresh process, and checkpoint resume.
"""
from __future__ import annotations

import argparse
import hashlib
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

from quanthus.stage_learning.p5_assessment import TemporalEncoderConfig, build_t0_model


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _synthetic_batch(torch: Any, batch_size: int) -> tuple[Any, Any, Any, Any, Any]:
    generator = torch.Generator(device="cpu").manual_seed(17)
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
    losses = torch.nn.functional.binary_cross_entropy_with_logits(
        logits, labels, reduction="none"
    )
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
) -> dict[str, Any]:
    return {
        "model_state": model.state_dict(),
        "optimizer_state": optimizer.state_dict(),
        "scheduler_state": scheduler.state_dict(),
        "rng_state": {
            "torch": torch.random.get_rng_state(),
            "python": random.getstate(),
        },
        "sampler_state": {"next_batch": batch_cursor},
        "epoch_cursor": 0,
        "batch_cursor": batch_cursor,
        "seed": 17,
        "input_digest": _digest(b"p5-stage-001-c-synthetic-120x24-batch-v1"),
        "model_digest": _digest(b"p5-stage-001-t0-4x128x4x512-12-head-v1"),
        "version_digest": _digest(b"quanthus.p5-stage-001-cpu-probe.v1"),
    }


def resume_only(checkpoint_path: Path, receipt_path: Path, batch_size: int) -> None:
    import torch

    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    model = build_t0_model()
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.99)
    model.load_state_dict(checkpoint["model_state"])
    optimizer.load_state_dict(checkpoint["optimizer_state"])
    scheduler.load_state_dict(checkpoint["scheduler_state"])
    torch.random.set_rng_state(checkpoint["rng_state"]["torch"])
    random.setstate(checkpoint["rng_state"]["python"])
    before = int(checkpoint["batch_cursor"])
    loss = _step(torch, model, optimizer, scheduler, _synthetic_batch(torch, batch_size))
    receipt_path.write_text(
        json.dumps(
            {
                "fresh_process_pid": os.getpid(),
                "resumed_from_batch_cursor": before,
                "completed_batch_cursor": before + 1,
                "finite_loss": math.isfinite(loss),
                "loss": loss,
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
    batch = _synthetic_batch(torch, batch_size)
    warmup_losses = [_step(torch, model, optimizer, scheduler, batch) for _ in range(2)]
    timings: list[float] = []
    losses: list[float] = []
    sizes: list[int] = []
    with tempfile.TemporaryDirectory(prefix="p5-stage-cpu-probe-") as temporary:
        root = Path(temporary)
        checkpoint_path = root / "checkpoint.pt"
        for index in range(3):
            started = time.perf_counter()
            loss = _step(torch, model, optimizer, scheduler, batch)
            torch.save(
                _checkpoint_payload(
                    torch, model, optimizer, scheduler, batch_cursor=index + 1
                ),
                checkpoint_path,
            )
            timings.append(time.perf_counter() - started)
            losses.append(loss)
            sizes.append(checkpoint_path.stat().st_size)
        resume_receipt = root / "resume.json"
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
            ],
            check=True,
            env={**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", "src")},
        )
        resumed = json.loads(resume_receipt.read_text(encoding="utf-8"))
    ordered = sorted(timings)
    median = ordered[len(ordered) // 2]
    return {
        "schema_version": "quanthus.p5-stage-001-cpu-probe-result.v1",
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
    args = parser.parse_args()
    if args.resume_only:
        if not args.receipt_path:
            parser.error("--receipt-path is required with --resume-only")
        resume_only(Path(args.resume_only), Path(args.receipt_path), args.batch_size)
        return
    result = run_probe(batch_size=args.batch_size)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
