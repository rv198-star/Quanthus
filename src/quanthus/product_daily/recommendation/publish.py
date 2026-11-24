"""Materialize a produced package into one directory and receipt its digests.

Writes are append-only per market_date and per package_id file: an existing file is
never rewritten.  Under P12's append-only binding rule a new release may only *add*
market dates; re-publishing the same market date with different content would be rejected by
the consumer, so this module refuses that case up front instead of letting an unusable file
exist on disk.

Three digest surfaces are kept distinct on purpose and named explicitly:

- file_sha256: SHA-256 of the exact bytes written to the package file.  It is the
  identity of the on-disk artifact.
- decision_set_digests: SHA-256 of each decision_set canonical JSON (compact,
  sort-keys, ASCII-free).  It identifies the decision_set content independent of file
  framing.
- p12_package_digests: the digest the P12 account binding records for the same content,
  computed through the real P12 loader so the identity is what the consumer actually binds.

A caller must never conflate them: binding continuity is judged on p12_package_digests,
artifact identity on file_sha256.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from quanthus.product_daily.contracts import ProductDailyError, require_aware
from quanthus.product_daily.recommendation.contracts import (
    DailyAdvicePackage,
    PublishReceipt,
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def decision_set_digests(package: DailyAdvicePackage) -> dict[str, str]:
    """sha256 of each decision_set canonical JSON (content identity, not file bytes)."""

    return {
        item.market_date.isoformat(): "sha256:"
        + hashlib.sha256(_canonical_bytes(item.to_dict())).hexdigest()
        for item in package.decision_sets
    }


def file_sha256(path: Path | str) -> str:
    """SHA-256 of the exact on-disk bytes of one written package file."""

    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def package_digests(package: DailyAdvicePackage) -> dict[str, str]:
    """Deprecated alias kept for callers: canonical decision_set digests.

    Use decision_set_digests for the content surface or file_sha256 for the artifact
    surface; this alias exists only so older call sites keep working.
    """

    return decision_set_digests(package)


def p12_package_digests(
    decision_set_dict: dict[str, Any],
    loader: Callable[[dict[str, Any]], Any],
) -> dict[str, str]:
    """Digest as the P12 account binding records it, computed through the P12 loader.

    loader is a callable receiving one raw decision_set dict and returning the
    P12-validated MarketDecisionSet (for example a small wrapper around
    platform.adapters.p12_synthetic_fixture._decision / validate_decision_set).  The
    raw input is consumed by the loader, so tampering with the dict changes the digest.
    """

    validated = loader(decision_set_dict)
    # The binding digest must come from the same P12 package that validated the
    # object, not from whatever quanthus.shared happens to be importable in this
    # process.  Resolve it through the validated object's module so a P11 tree
    # without a bound P12 package fails closed instead of hashing self-consistently.
    import importlib

    # type(validated).__module__ is e.g. quanthus.capabilities.decision_access; the
    # digest lives at <top package>.shared.fact_contracts inside the same P12 tree.
    top_package = type(validated).__module__.split(".", 1)[0]
    try:
        p12_digest = importlib.import_module(top_package + ".shared.fact_contracts").digest
    except ImportError as exc:  # pragma: no cover - depends on bound P12 package
        raise ProductDailyError(
            "p12_binding_unavailable",
            "the P12 fact_contracts digest is required for the account binding and "
            "is only present in the bound P12 package; cannot compute it here",
        ) from exc
    content = json.loads(json.dumps(dataclasses.asdict(validated), default=str, sort_keys=True))
    return {
        validated.market_date.isoformat(): p12_digest(content),
    }


def _package_file_bytes(package: DailyAdvicePackage) -> bytes:
    return (
        json.dumps(package.to_dict(), ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    ).encode("utf-8")


def publish_package(
    package: DailyAdvicePackage,
    *,
    directory: Path | str,
    published_at: datetime,
    elapsed_ms: int = 0,
    p12_loader: Callable[[dict[str, Any]], Any] | None = None,
    consumer_binding: dict[str, Any] | None = None,
) -> PublishReceipt:
    """Write one package file append-only and return a receipt with all digest surfaces.

    published_at must equal every decision_set's own published_at; a mismatch would
    produce an artifact whose receipt disagrees with its payload, so it is refused.

    When p12_loader is supplied (a callable that validates one raw decision_set dict
    into a MarketDecisionSet) the receipt additionally carries the P12 binding digest
    for each released date; without it p12_package_digests is None and the caller
    must not treat the file as bound on the consumer side.
    """

    require_aware(published_at, "published_at")
    for item in package.decision_sets:
        if item.published_at != published_at:
            raise ProductDailyError(
                "invalid_receipt",
                "publish_package published_at differs from the decision_set's",
            )
    has_blocked = any(
        decision.stage == "input_blocked"
        for item in package.decision_sets
        for decision in item.decisions
    )
    if has_blocked:
        raise ProductDailyError(
            "unpublishable_package",
            "a package containing any blocked decision is a diagnostic artifact, "
            "never a published advice day",
        )

    # Held-reduction wire v2 gate: an executable reduce must only be released through a
    # pinned consumer whose binding declares the capability AND whose actual loader
    # preserves exit_kind/target_position_fraction on the validated object for the same
    # opportunity/security.  A bare caller boolean can never grant that: a loader that
    # silently drops the fields would read the same exit_plan as a full liquidation, so
    # without a real field-preserving validated object the reduce stays refused /
    # display-only.  consumer_binding is the versioned consumer_identity() mapping
    # (source_commit + capabilities), not a free-standing flag.
    has_reduce = any(
        decision.stage == "exit_plan" and decision.exit_kind == "reduce"
        for item in package.decision_sets
        for decision in item.decisions
    )
    if has_reduce:
        _require_field_preserving_v2_consumer(package, p12_loader, consumer_binding)

    # The P12 binding digest is computed and validated *before* any bytes land so a loader
    # failure leaves the directory untouched.
    p12_digests: dict[str, str] | None = None
    if p12_loader is not None:
        merged: dict[str, str] = {}
        for item in package.decision_sets:
            merged.update(p12_package_digests(item.to_dict(), p12_loader))
        p12_digests = merged

    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)

    # The on-disk layout is one file per (package_id, version).  A second write of the same
    # package_id for the same market date but different content is exactly what P12's
    # append-only binding rejects, so refuse before any bytes land.
    this_sets = [item.to_dict() for item in package.decision_sets]
    this_dates = {item["market_date"] for item in this_sets}
    for existing in sorted(target_dir.glob(package.package_id + ".v*.json")):
        try:
            other = json.loads(existing.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProductDailyError(
                "corrupt_artifact",
                f"{existing} exists but is unreadable; refusing to publish next to a "
                "damaged same-lineage file",
            ) from exc
        other_sets = other.get("decision_sets", [])
        other_dates = {item.get("market_date") for item in other_sets}
        if other_dates & this_dates and other_sets != this_sets:
            raise ProductDailyError(
                "append_only_conflict",
                str(existing) + " already covers a market date this package rewrites; "
                "a same-date replacement is not append-only and P12 would refuse it",
            )

    path = target_dir / (package.package_id + ".v" + package.version + ".json")
    if path.exists():
        raise ProductDailyError(
            "idempotency_conflict",
            str(path) + " already exists; a same-(id, version) rewrite is never allowed",
        )
    payload = _package_file_bytes(package)
    path.write_bytes(payload)

    decision_count = sum(len(item.decisions) for item in package.decision_sets)
    entry_count = sum(
        1 for item in package.decision_sets for d in item.decisions if d.stage == "entry_plan"
    )
    return PublishReceipt(
        package_id=package.package_id,
        version=package.version,
        path=str(path),
        file_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
        decision_set_digests=tuple(sorted(decision_set_digests(package).items())),
        p12_package_digests=(None if p12_digests is None else tuple(sorted(p12_digests.items()))),
        decision_count=decision_count,
        entry_plan_count=entry_count,
        admission_disposition=package.decision_sets[0].audit.admission_disposition
        if package.decision_sets
        else "empty_advice",
        published_at=published_at,
        elapsed_ms=elapsed_ms,
    )


def _require_field_preserving_v2_consumer(
    package: DailyAdvicePackage,
    p12_loader: Callable[[dict[str, Any]], Any] | None,
    consumer_binding: dict[str, Any] | None,
) -> None:
    """Fail closed unless a real pinned v2 consumer preserves the reduce fields.

    Three conditions, all checked before any filesystem write:

    1. consumer_binding must be the versioned consumer_identity() mapping and declare
       capabilities.supports_held_reduction_v2 == true (a caller-supplied bool is not a
       binding).
    2. A real p12_loader must be supplied; the absence of a loader can never be a
       validated consumer.
    3. For every decision_set containing an exit_kind=reduce decision, the loader's
       validated object must carry exit_kind == "reduce" and a target_position_fraction
       equal to the serialized value on the same opportunity_id/security_code.  A loader
       that drops or alters either field (the pinned v1 consumer does exactly this) fails
       the gate.
    """

    capability = (consumer_binding or {}).get("capabilities") or {}
    if capability.get("supports_held_reduction_v2") is not True:
        raise ProductDailyError(
            "unpublishable_package",
            "an executable reduce requires a pinned P12 v2 consumer "
            "(consumer_binding capabilities.supports_held_reduction_v2); the current "
            "consumer would drop exit_kind/target_position_fraction and read it as a "
            "full sell",
        )
    if p12_loader is None:
        raise ProductDailyError(
            "unpublishable_package",
            "an executable reduce requires the actual pinned P12 v2 loader to validate "
            "and preserve its fields; no loader means no validated consumer",
        )
    for item in package.decision_sets:
        reduce_ids = {
            decision.opportunity_id
            for decision in item.decisions
            if decision.stage == "exit_plan" and decision.exit_kind == "reduce"
        }
        if not reduce_ids:
            continue
        raw = item.to_dict()
        try:
            validated = p12_loader(raw)
        except Exception as exc:
            raise ProductDailyError(
                "unpublishable_package",
                f"pinned v2 consumer failed to validate the reduce decision_set: {exc}",
            ) from exc
        by_id = {decision.opportunity_id: decision for decision in validated.decisions}
        expected = {
            decision.opportunity_id: decision
            for decision in item.decisions
            if decision.opportunity_id in reduce_ids
        }
        for opportunity_id, decision in expected.items():
            seen = by_id.get(opportunity_id)
            if (
                seen is None
                or getattr(seen, "exit_kind", None) != "reduce"
                or getattr(seen, "target_position_fraction", None) is None
                or str(getattr(seen, "target_position_fraction", ""))
                != str(decision.target_position_fraction)
            ):
                raise ProductDailyError(
                    "unpublishable_package",
                    f"pinned consumer dropped or altered the reduce fields on "
                    f"{opportunity_id}; an executable reduce may only be released "
                    "through a field-preserving p12.daily-advice-package.v2 loader",
                )
