"""Fail-closed candidate strategy packaging and replay adapters."""

from quanthus.candidate_strategy.contract import (
    CandidateContractError,
    CandidateManifest,
    load_candidate_manifest,
)
from quanthus.candidate_strategy.quantdinger_adapter import (
    QuantDingerBullishThreeLinesAdapter,
    QuantDingerCandidateAdapterError,
    build_quantdinger_adapter,
)
from quanthus.candidate_strategy.runner import (
    CandidateReplayEvidence,
    run_candidate_replay,
)

__all__ = [
    "CandidateContractError",
    "CandidateManifest",
    "CandidateReplayEvidence",
    "QuantDingerBullishThreeLinesAdapter",
    "QuantDingerCandidateAdapterError",
    "build_quantdinger_adapter",
    "load_candidate_manifest",
    "run_candidate_replay",
]
