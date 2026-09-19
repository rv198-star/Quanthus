"""CLI for one frozen P3-STRAT-003 composite batch."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from quanthus.expert_strategy.p3_strat_003 import P3Strat003CompositeBackend


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one frozen P3-STRAT-003 composite batch")
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--provider-config", required=True)
    population = parser.add_mutually_exclusive_group(required=True)
    population.add_argument("--event-run-digest")
    population.add_argument("--candidate-input-manifest")
    parser.add_argument("--decision-start", required=True)
    parser.add_argument("--decision-end", required=True)
    parser.add_argument("--execution-end", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with P3Strat003CompositeBackend(
        args.repository_root,
        args.output_root,
        decision_start=date.fromisoformat(args.decision_start),
        decision_end=date.fromisoformat(args.decision_end),
        execution_end=date.fromisoformat(args.execution_end),
        provider_config=args.provider_config,
        parent_event_run_digest=args.event_run_digest,
        candidate_input_manifest=args.candidate_input_manifest,
    ) as backend:
        result = backend.execute_all()
    print(
        json.dumps(
            {
                "request_digest": result.request_digest,
                "batch_digest": result.digest,
                "variants": {
                    key: value.digest for key, value in sorted(result.variants.items())
                },
                "telemetry": dict(result.telemetry),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
