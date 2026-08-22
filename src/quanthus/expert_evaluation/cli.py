"""Isolated child-process entry point for expert-evaluation Runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from quanthus.expert_evaluation.production import PRODUCER_ID, production_producer_for
from quanthus.expert_evaluation.runner import (
    ExpertEvaluationJobInput,
    FormalStrategyResolver,
    OperationProducer,
    fixture_producer_for,
    run_evaluation,
)
from quanthus.expert_evaluation.serialization import ExpertEvaluationError, parse_canonical_object
from quanthus.expert_strategy.production import FORMAL_PRODUCER_ID, formal_producer_for


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build one immutable expert-evaluation Run")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    return parser


def _progress(stage: str, detail: object) -> None:
    print(
        json.dumps(
            {"event": "expert-evaluation-progress", "stage": stage, "detail": detail},
            ensure_ascii=True,
            sort_keys=True,
        ),
        file=sys.stderr,
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        job_input = ExpertEvaluationJobInput.from_dict(
            parse_canonical_object(args.input.read_bytes())
        )
        producer: OperationProducer
        formal_strategy_resolver: FormalStrategyResolver | None = None
        if job_input.producer.producer_id == PRODUCER_ID:
            producer = production_producer_for(job_input, args.registry_root)
        elif job_input.producer.producer_id == FORMAL_PRODUCER_ID:
            producer, formal_strategy_resolver = formal_producer_for(
                job_input, args.registry_root
            )
        else:
            producer = fixture_producer_for(job_input)
        run = run_evaluation(
            job_input,
            registry_root=args.registry_root,
            staging=args.staging,
            run_root=args.run_root,
            producer=producer,
            progress=_progress,
            formal_strategy_resolver=formal_strategy_resolver,
        )
    except (OSError, ExpertEvaluationError) as exc:
        reason = exc.reason_code if isinstance(exc, ExpertEvaluationError) else "io_error"
        print(
            json.dumps(
                {"event": "expert-evaluation-error", "reason_code": reason},
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2
    print(
        json.dumps(
            {
                "run_id": run.run_id,
                "artifact_ref": str(run.root),
                "manifest_digest": run.manifest_digest,
                "evaluation_identities": run.manifest["evaluation_identities"],
                "strategy_identity": run.manifest["strategy_identity"],
                "summary": run.summary,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
