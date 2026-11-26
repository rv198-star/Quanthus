#!/usr/bin/env python3
"""Bounded live smoke for the Decision API adapter (issue #755).

Runs exactly one batch of three canonical questions (predicate, choice,
score) against one decision-model endpoint, records the raw wire response
into a sealed replay fixture, then verifies offline replay round-trip.

This script performs a real network call.  It exists for explicitly
authorized schema verification only; offline tests, CI, historical replay
and regression harnesses must never call it.  The batch content is a fixed
public-style news sample and carries no account, position or order data.

Credential resolution order:

1. QUANTHUS_DECISION_API_KEY environment variable;
2. the magpie provider experimental_bearer_token in the local Codex
   config.toml -- a development convenience for the maintainer host only.

Exit codes: 0 success; 2 usage/config error; 3 transport/http failure;
4 schema verification failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from quanthus.decision_api import (  # noqa: E402
    ChoiceOption,
    ChoiceQuestion,
    DecisionError,
    DecisionRequest,
    PredicateQuestion,
    RecordingBackend,
    ReplayBackend,
    ScoreLevel,
    ScoreQuestion,
    dialect_parser,
    request_digest,
)
from quanthus.decision_api.backends import (  # noqa: E402
    DECISIONS_DIALECT_OPENAI_GA,
    HttpBackendConfig,
    OpenAIDecisionsBackend,
)

SMOKE_INPUT = (
    "【证监会就程序化交易新规公开征求意见】证监会11月27日发布程序化交易管理规定（征求意见稿），"
    "拟对高频交易实施差异化收费并强化异常交易监控；央行今日开展3800亿元7天期逆回购操作，净投放2100亿元。"
)


def resolve_api_key() -> str | None:
    env_key = os.environ.get("QUANTHUS_DECISION_API_KEY")
    if env_key:
        return env_key
    config = pathlib.Path.home() / ".codex" / "config.toml"
    if config.exists():
        text = config.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"\[model_providers\.magpie\](.*?)(?:\n\[|\Z)", text, flags=re.S)
        for line in (match.group(1) if match else "").splitlines():
            if "=" in line:
                key, value = [part.strip() for part in line.split("=", 1)]
                if key == "experimental_bearer_token":
                    return value.strip('"').strip("'")
    return None


def build_request(model: str) -> DecisionRequest:
    return DecisionRequest(
        model=model,
        input=SMOKE_INPUT,
        questions=(
            PredicateQuestion(
                name="is_macro_policy",
                instructions="判断输入是否包含央行公开市场操作或证券监管政策类宏观事件。",
            ),
            ChoiceQuestion(
                name="event_tier",
                instructions="该快讯属于哪个影响层级？",
                choices=(
                    ChoiceOption("GLOBAL_MACRO", "全球宏观货币、汇率或主权级系统性事件"),
                    ChoiceOption("DOMESTIC_POLICY", "国内监管、货币或财政级政策事件"),
                    ChoiceOption("INDUSTRY_REGIME", "特定产业链重大政策、技术或供需格局事件"),
                    ChoiceOption("NOISE", "无实质增量的常规噪音"),
                ),
            ),
            ScoreQuestion(
                name="liquidity_impact",
                instructions="评估对A股流动性环境的边际影响量级（0=明显收紧，4=明显宽松）。",
                levels=(
                    ScoreLevel("tightening", "明显收紧"),
                    ScoreLevel("mildly_tight", "中性偏紧"),
                    ScoreLevel("neutral", "中性"),
                    ScoreLevel("mildly_loose", "中性偏松"),
                    ScoreLevel("easing", "明显宽松"),
                ),
            ),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://gw.72live.com")
    parser.add_argument("--model", default="google/gemini-decision-1")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--chain",
        default=None,
        help="provider chain JSON (quanthus.decision-providers.v1); overrides --base-url/--model "
        "and resolves keys from the configured api_key_env environment variables",
    )
    parser.add_argument("--output", required=True, help="sealed replay fixture path")
    args = parser.parse_args()

    if args.chain:
        from quanthus.decision_api.providers import build_priority_backend, load_provider_chain

        try:
            chain = load_provider_chain(args.chain)
            backend = build_priority_backend(chain)
        except DecisionError as error:
            print(f"SMOKE_CHAIN_CONFIG: {error}", file=sys.stderr)
            return 2
        request = build_request(chain.enabled()[0].model)
    else:
        api_key = resolve_api_key()
        if not api_key:
            print("SMOKE_KEY_UNAVAILABLE: set QUANTHUS_DECISION_API_KEY", file=sys.stderr)
            return 2
        url = args.base_url.rstrip("/") + "/v1/decisions"
        backend = OpenAIDecisionsBackend(
            HttpBackendConfig(url=url, api_key=api_key, timeout_seconds=args.timeout)
        )
        request = build_request(args.model)

    parser_fn = dialect_parser(DECISIONS_DIALECT_OPENAI_GA)
    output = pathlib.Path(args.output)
    recording = RecordingBackend(backend, output, parser_fn)

    try:
        response = recording.decide(request)
    except DecisionError as error:
        print(f"SMOKE_LIVE_FAILED: {error}", file=sys.stderr)
        last_attempts = getattr(backend, "last_attempts", ())
        if last_attempts:
            print("attempts: " + json.dumps(last_attempts, ensure_ascii=False), file=sys.stderr)
        return 3

    digest = request_digest(request)
    raw_bytes = output.read_bytes()
    fixture_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    answers = response.answers
    if len(answers) != 3 or any(answer.type == "refusal" for answer in answers):
        print(
            f"SMOKE_ANSWER_SHAPE_VIOLATION: {json.dumps(response.raw, ensure_ascii=False)[:300]}",
            file=sys.stderr,
        )
        return 4

    replay = ReplayBackend(output, parser_fn)
    replayed = replay.decide(request)
    same_shape = [answer.type for answer in replayed.answers] == ["predicate", "choice", "score"]

    named = response.named()
    summary = {
        "status": "ok" if same_shape else "replay_shape_mismatch",
        "model": response.model,
        "digest": digest,
        "fixture": str(output),
        "fixture_sha256": "sha256:" + fixture_sha256,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.total_tokens,
        },
        "attempts": [
            {
                "provider_id": attempt["provider_id"],
                "outcome": attempt["outcome"],
                "elapsed_seconds": attempt["elapsed_seconds"],
            }
            for attempt in getattr(backend, "last_attempts", ())
        ],
        "answers": {
            "is_macro_policy": round(named["is_macro_policy"].probability, 4),
            "event_tier": {
                "choice": named["event_tier"].choice,
                "confidence": round(named["event_tier"].confidence, 4),
            },
            "liquidity_impact": {
                "score": round(named["liquidity_impact"].score, 4),
                "confidence": round(named["liquidity_impact"].confidence, 4),
            },
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0 if same_shape else 4


if __name__ == "__main__":
    sys.exit(main())
