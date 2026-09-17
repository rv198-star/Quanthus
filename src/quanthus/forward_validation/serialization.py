"""Canonical serialization helpers for prospective-validation governance artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from quanthus.forward_validation.contracts import plain_json


def canonical_json_bytes(value: object) -> bytes:
    """Return deterministic UTF-8 JSON bytes for semantic identity."""

    return json.dumps(
        plain_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def proposal_digest(payload: Mapping[str, object]) -> str:
    """Digest the proposal body while excluding its self-declared digest field."""

    return digest_value({key: value for key, value in payload.items() if key != "proposal_digest"})
