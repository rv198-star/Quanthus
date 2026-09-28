"""Explicit candidate-only numerical tolerance; completeness is never optional.

This policy compares observations without changing prices, shares or amount.
The existing strict policy remains every caller's default. The bounded profile
is not production activation, source truth or permission to invent a minute.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MinuteQualityPolicy:
    id: str
    price_abs: Decimal
    price_epsilon: Decimal
    volume_abs: Decimal
    volume_relative: Decimal
    require_amount: bool

    def price_matches(self, observed: Decimal, reference: Decimal) -> bool:
        return abs(observed - reference) <= self.price_abs + self.price_epsilon

    def volume_matches(self, observed: Decimal, reference: Decimal) -> bool:
        delta = abs(observed - reference)
        # BOTH limits must pass. A zero reference permits no residual shares.
        return delta <= self.volume_abs and delta <= abs(reference) * self.volume_relative

    def evidence(self) -> dict[str, Any]:
        result = {
            "schema": "quanthus.minute-quality-policy.v1",
            "id": self.id,
            "price_abs_cny": format(self.price_abs, "f"),
            "price_representation_epsilon": format(self.price_epsilon, "f"),
            "daily_volume_abs_shares": format(self.volume_abs, "f"),
            "daily_volume_relative": format(self.volume_relative, "f"),
            "require_amount": self.require_amount,
            "cross_source_minute_volume": "exact",
            "cross_source_amount": "exact" if self.require_amount else "not_governed",
            "source_selection": "whole_security_priority_no_blending",
            "activation": (
                "explicit_candidate_only" if self != STRICT else "existing_strict_default"
            ),
        }
        canonical = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        return {**result, "semantic_sha256": hashlib.sha256(canonical).hexdigest()}


STRICT = MinuteQualityPolicy(
    "existing-exact-1m-v1", Decimal(0), Decimal(0), Decimal(0), Decimal(0), False
)
BOUNDED = MinuteQualityPolicy(
    "owner-bounded-1m-20260929-v1", Decimal("0.01"), Decimal("0.0000000001"),
    Decimal(100), Decimal("0.00001"), True,
)


def require_supported_policy(policy: MinuteQualityPolicy) -> None:
    if type(policy) is not MinuteQualityPolicy or type(policy.require_amount) is not bool:
        raise ValueError("unsupported minute quality policy")
    if policy not in (STRICT, BOUNDED):
        raise ValueError("unsupported minute quality policy")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate quality-policy field")
        result[key] = value
    return result


def load_policy(path: Path | None) -> MinuteQualityPolicy:
    """Only the versioned bounded candidate is supported, not arbitrary limits."""
    if path is None:
        return STRICT
    value = json.loads(path.read_bytes(), object_pairs_hook=_unique_object)
    expected = {key: val for key, val in BOUNDED.evidence().items() if key != "semantic_sha256"}
    if not isinstance(value, dict) or value.keys() != expected.keys():
        raise ValueError("unsupported or altered minute quality policy")
    if any(type(value[key]) is not type(val) or value[key] != val
           for key, val in expected.items()):
        raise ValueError("unsupported or altered minute quality policy")
    return BOUNDED
