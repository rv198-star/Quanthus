"""One explicitly scheduled Head2 correction with immutable parent provenance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace

from quanthus.stage_learning import head2_revision as hr
from quanthus.stage_learning.local_head import (
    HeadBindings,
    HeadCheckpoint,
    HeadRow,
    dump_checkpoint,
)
from quanthus.stage_learning.p5_assessment import AssessmentOutput, P5AssessmentError
from quanthus.stage_learning.p5_contracts import require_digest

SCHEMA = "quanthus.head2.scheduled-correction.v1"
MODE = "scheduled_supervised_correction"


@dataclass(frozen=True)
class ScheduledCorrection:
    update: hr.GuardedUpdate
    contract_digest: str
    parent_file_sha256: str
    mode: str = MODE

    def validate(self) -> None:
        self.update.validate()
        require_digest(self.contract_digest)
        require_digest(self.parent_file_sha256)
        if self.mode != MODE or self.parent_file_sha256 != (
            "sha256:" + hashlib.sha256(dump_checkpoint(self.update.parent)).hexdigest()
        ):
            raise P5AssessmentError("r10_scheduled_parent_or_mode")


def begin_scheduled_correction(
    parent: HeadCheckpoint,
    train: Sequence[HeadRow],
    update: Sequence[HeadRow],
    guard: Sequence[HeadRow],
    *,
    contract_digest: str,
    parent_file_sha256: str,
    stop_after: int | None = None,
) -> ScheduledCorrection:
    ordered = hr._inputs(parent, train, update, guard)
    if len(ordered[0]) != 24 or len(ordered[1]) != 7:
        raise P5AssessmentError("r10_scheduled_exact_24_and_7")
    state = ScheduledCorrection(
        hr.GuardedUpdate(
            parent,
            parent.weights,
            parent.bias,
            0,
            *(hr._rows_digest(group) for group in ordered),
        ),
        contract_digest,
        parent_file_sha256,
    )
    state.validate()
    return resume_scheduled_correction(state, *ordered, stop_after=stop_after)


def resume_scheduled_correction(
    child: ScheduledCorrection,
    train: Sequence[HeadRow],
    update: Sequence[HeadRow],
    guard: Sequence[HeadRow],
    *,
    stop_after: int | None = None,
) -> ScheduledCorrection:
    child.validate()
    result = replace(
        child,
        update=hr.resume_update(child.update, train, update, guard, stop_after=stop_after),
    )
    result.validate()
    return result


def scheduled_score(
    child: ScheduledCorrection, features: Sequence[float | None]
) -> AssessmentOutput:
    child.validate()
    return hr.update_score(child.update, features)


def dump_scheduled_correction(child: ScheduledCorrection) -> bytes:
    child.validate()
    value = {
        "schema": SCHEMA,
        "mode": child.mode,
        "contract_digest": child.contract_digest,
        "parent_file_sha256": child.parent_file_sha256,
        "update": json.loads(hr.dump_update(child.update)),
        "gradient_groups": {
            "parent_replay": {"rows": 24, "weight": 0.5},
            "supervised_update": {"rows": 7, "weight": 0.5},
        },
        "legacy_exposure": "UNKNOWN_DIAGNOSTIC_ONLY",
        "consumer_admission": "NOT_GRANTED",
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def load_scheduled_correction(
    raw: bytes,
    *,
    expected_sha256: str,
    contract_digest: str,
    parent_file_sha256: str,
    parent_digest: str,
    parent_bindings: HeadBindings,
) -> ScheduledCorrection:
    require_digest(expected_sha256)
    if "sha256:" + hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise P5AssessmentError("r10_scheduled_file_hash")
    value = json.loads(raw)
    if (
        value["schema"] != SCHEMA
        or value["mode"] != MODE
        or value["contract_digest"] != contract_digest
        or value["parent_file_sha256"] != parent_file_sha256
    ):
        raise P5AssessmentError("r10_scheduled_contract_binding")
    encoded = json.dumps(value["update"], sort_keys=True, separators=(",", ":")).encode()
    update = hr.load_update(
        encoded,
        expected_sha256="sha256:" + hashlib.sha256(encoded).hexdigest(),
        bindings=parent_bindings,
        parent_digest=parent_digest,
    )
    child = ScheduledCorrection(update, contract_digest, parent_file_sha256, value["mode"])
    child.validate()
    if dump_scheduled_correction(child) != raw:
        raise P5AssessmentError("r10_scheduled_canonical_encoding")
    return child
