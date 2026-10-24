"""Owner-selected no-subscription disposition for a distinct retrospective version.

This records a real optional action without pretending it is a zero-value P2
cash/bonus action. It does not remove held-input guards or qualify price limits.
Neither cash, share count, observed market marks nor historical clocks are changed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.pipelines.securities_warehouse.p10_rights_issue_source import RightsIssueFacts

VERSION = "p3-retrospective-optional-rights-waive-v1"
POLICY = "config/research/p3-strat-003/optional-rights-waive.v1.json"


def no_subscription_disposition(
    repository: Path,
    facts: RightsIssueFacts,
    *,
    arm: str,
    market_date: date,
    source_information_cutoff: datetime,
) -> dict[str, Any]:
    """Record the same explicit no-election rule for every arm and qualified event.

    No equity value or inferred counterfactual subscription is calculated here.
    A receipt is not authority to proceed past a missing/invalid input guard.
    """
    if type(facts) is not RightsIssueFacts:
        raise ValueError("RIGHTS_CONSUMER_QUALIFIED_FACTS_REQUIRED")
    raw = (repository / POLICY).read_bytes()
    policy = json.loads(raw)
    if (
        policy.get("schema_version") != VERSION
        or policy.get("arms") != ["B", "T", "TCH"]
        or policy.get("optional_paid_rights") != "DO_NOT_SUBSCRIBE"
        or policy.get("decision_dates") != ["2012-01-04", "2021-12-30"]
        or policy.get("execution_through") != "2021-12-31"
        or policy.get("retain_actual_observed_old_share_mark") is not True
        or policy.get("all_source_and_execution_guards_retained") is not True
        or any(
            policy.get(k) is not False
            for k in (
                "subscription_cash_debit",
                "subscription_share_credit",
                "assumed_rights_sale",
                "dilution_compensation",
                "strict_historical_PIT_admission",
                "production_authorized",
                "P6_mutation_authorized",
            )
        )
    ):
        raise ValueError("RIGHTS_CONSUMER_POLICY")
    if (
        arm not in policy["arms"]
        or type(market_date) is not date
        or not date(2012, 1, 4) <= market_date <= date(2021, 12, 31)
    ):
        raise ValueError("RIGHTS_CONSUMER_SCOPE")
    if (
        source_information_cutoff.utcoffset() is None
        or source_information_cutoff < facts.source_information_cutoff
    ):
        raise ValueError("RIGHTS_CONSUMER_SOURCE_NOT_AVAILABLE")
    if market_date < facts.record_date:
        raise ValueError("RIGHTS_CONSUMER_EVENT_NOT_ACTIVE")
    waived = market_date > facts.subscription_end
    return {
        "kind": VERSION,
        "policy_sha256": hashlib.sha256(raw).hexdigest(),
        "source_manifest_sha256": facts.manifest_sha256,
        "source_event_sha256": facts.event_sha256,
        "source_information_cutoff": source_information_cutoff.isoformat(),
        "actual_source_acquired_through": facts.source_information_cutoff.isoformat(),
        "security_code": facts.security_code,
        "arm": arm,
        "market_date": market_date.isoformat(),
        "decision": "DO_NOT_SUBSCRIBE",
        "status": "WAIVED_AFTER_DEADLINE" if waived else "NO_SUBSCRIPTION_ELECTED",
        "subscription_cash_delta": "0",
        "subscription_share_delta": 0,
        "assumed_rights_sale_cash": "0",
        "old_share_mark": "RETAIN_ACTUAL_OBSERVED_UNCOMPENSATED",
        "price_limit_qualification": "NOT_GRANTED_BY_THIS_RECEIPT",
        "consumer_admission": "NOT_GRANTED_BY_THIS_RECEIPT",
        "historical_publication_instant": None,
        "historical_PIT_claim": False,
    }
