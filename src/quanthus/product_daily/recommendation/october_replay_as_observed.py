"""Explicit October as-observed replay entry (p11-e1-as-observed).

A versioned, clearly-graded historical-replay entry for the October
ENGINEERING_REPLAY_AS_OBSERVED use.  It does NOT change the strict H1 path or
the original member clocks: sealed members/calendar/listings whose archival
publish/acquire instant is later than the logical T are admitted as NAMED
post-cutoff archives on the receipt, never relabelled October-known, and
their business content date is asserted <= T before entry (a post-T business
fact stays isolated, never back-dated).

The three clocks stay distinct on the signed receipt:
  * logical decision instant = T 21:30 Asia/Shanghai (the bound coordinate);
  * original source clocks (knowledge_at / visible_at / knowledge_from /
    member version) preserved verbatim into the as_observed block;
  * this run's actual read/verify instant recorded separately.

Reuses the real rule core, the named coverage/exclusion gates and the
equivalent-day anchor proof; the caller (P12) owns market rows / account /
member clocks.  Not a PIT disguise: the digest binds the original envelope
identity, and the as_observed block is part of source_closure.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any

from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.history_input import (
    run_daily_advice_from_history,
)

AS_OBSERVED_SCHEMA = "p11.october-replay-as-observed.v1"


def _fail(code: str, message: str) -> None:
    raise ProductDailyError(code, message)


def _sealed_business_content_check(
    raw_history: Mapping[str, Any], market_date: date
) -> list[dict[str, Any]]:
    """Assert every member's declared business content ends on/before T.

    A sealed archive is admitted for its historical fact rows only; a member
    whose declared date_to extends PAST the signal date carries post-T business
    data and is isolated here (named, never silently consumed).  Returns the
    per-member business-content assertion records for the receipt.
    """
    out: list[dict[str, Any]] = []
    env = raw_history.get("raw_history", raw_history)
    cutoff = market_date.isoformat()
    for m in env.get("source_members") or ():
        if not isinstance(m, Mapping):
            continue
        date_to = m.get("date_to")
        out.append(
            {
                "member_id": m.get("member_id"),
                "business_content_through": date_to,
                "content_on_or_before_T": (
                    None if date_to is None else str(date_to) <= cutoff
                ),
            }
        )
    return out


def run_october_replay_as_observed(
    raw_history: Mapping[str, Any],
    *,
    runner: Any,
    resources: Any,
    facts: Any,
    policy: Any,
    account: Any,
    run_id: str,
    snapshot_id: str,
    snapshot_manifest_digest: str,
    card: Any,
    split_policy: Any,
    source_policy: Any,
    clock: Any = None,
    input_exclusions: list[dict[str, Any]] | None = None,
    anchor_mode: str = "equivalent_day",
    verified_at: datetime | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Run one T through the as-observed replay chain.

    Returns (RuleDayOutput, as_observed_block).  The block carries the named
    post-cutoff archives (sealed members / calendar / listings), the
    business-content assertion per member, and this run's actual read/verify
    instants -- the receipt's source_closure binds it, so the logical T, the
    original source clocks and the actual consumption instant are all
    re-checkable.  A member whose business content runs past T is named and
    the day isolated fail-closed.
    """
    signal = facts.market_date
    content_check = _sealed_business_content_check(raw_history, signal)
    leaking = [c for c in content_check if c["content_on_or_before_T"] is False]
    if leaking:
        _fail(
            "as_observed_post_T_content",
            f"member business content extends past {signal}: "
            f"{[c['member_id'] for c in leaking]}",
        )

    observed_sink: dict[str, Any] = {}
    actual_verified = (verified_at or datetime.now(timezone.utc)).isoformat()
    output = run_daily_advice_from_history(
        raw_history,
        runner=runner,
        resources=resources,
        facts=facts,
        policy=policy,
        account=account,
        run_id=run_id,
        snapshot_id=snapshot_id,
        snapshot_manifest_digest=snapshot_manifest_digest,
        card=card,
        split_policy=split_policy,
        source_policy=source_policy,
        clock=clock,
        input_exclusions=input_exclusions,
        anchor_mode=anchor_mode,
        as_observed=True,
        observed_sink=observed_sink,
    )

    as_observed = {
        "schema_version": AS_OBSERVED_SCHEMA,
        "logical_decision_at": policy.decision_cutoff.isoformat(),
        "signal_date": signal.isoformat(),
        # Original source clocks are preserved verbatim on the members; here we
        # name which ones are post-cutoff archives so a late-acquired original
        # is never passed off as October-known.
        "sealed_archives": {
            "members": observed_sink.get("sealed_members", []),
            "calendar": observed_sink.get("sealed_calendar", []),
            "listings": observed_sink.get("sealed_listings", []),
        },
        "business_content_assertion": content_check,
        "actual_consumption": {
            "verified_at": actual_verified,
            "run_id": run_id,
        },
        "anchor_mode": anchor_mode,
        "pit_disguise": False,
        "note": (
            "Post-cutoff archives keep their original publish/acquire clocks; "
            "their business content is asserted on/before T.  No clock is "
            "rewritten to fake October-known."
        ),
    }
    return output, as_observed
