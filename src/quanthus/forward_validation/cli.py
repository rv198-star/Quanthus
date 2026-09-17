"""Child-process CLI for the bounded P6 Forward Worker."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from quanthus.forward_validation.runner import (
    ForwardCycleJobInput,
    load_forward_worker_config,
    run_disabled_rehearsal,
)
from quanthus.policy import find_project_root


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one P6 Forward Worker cycle")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("forward cycle input must be an object")
    job_input = ForwardCycleJobInput.from_dict(raw)
    root = find_project_root()
    config = load_forward_worker_config(args.config, root=root)
    print(
        json.dumps(
            {
                "event": "p6-forward-progress",
                "stage": "binding-inputs",
                "detail": {"mode": "disabled_rehearsal"},
            },
            sort_keys=True,
        ),
        file=sys.stderr,
        flush=True,
    )
    run = run_disabled_rehearsal(
        job_input=job_input,
        config=config,
        staging=args.staging,
        run_root=args.run_root,
    )
    print(
        json.dumps(
            {
                "event": "p6-forward-progress",
                "stage": "rehearsal-sealed",
                "detail": {"run_id": run.run_id},
            },
            sort_keys=True,
        ),
        file=sys.stderr,
        flush=True,
    )
    print(
        json.dumps(
            {
                "run_id": run.run_id,
                "artifact_ref": str(run.root),
                "manifest_digest": run.manifest_digest,
                "input_digest": run.input_digest,
                "summary": dict(run.summary),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
