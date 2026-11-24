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
import inspect
import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
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


def _serialize_decision_sets(
    package: DailyAdvicePackage, *, wire_version: str
) -> list[dict[str, Any]]:
    """Serialize decision_sets on the explicitly selected wire version.

    v1 -> the frozen historical payload (no exit fields).  v2 -> the
    p12.daily-advice-package.v2 payload (exit_kind/target_position_fraction
    present).  Any other declared version is refused before bytes exist.
    """
    if wire_version == "p12.daily-advice-package.v1":
        return [item.to_dict() for item in package.decision_sets]
    if wire_version == "p12.daily-advice-package.v2":
        return [item.to_wire_dict() for item in package.decision_sets]
    raise ProductDailyError(
        "unpublishable_package",
        f"unregistered decision_set wire version: {wire_version}",
    )


def _package_file_dict(package: DailyAdvicePackage, *, wire_version: str) -> dict[str, Any]:
    """Package file payload for one wire version.

    Outer schema_version stays the P11 package envelope identity
    (quanthus.r11.daily-advice.v1); the consumer-facing contract version travels on
    each decision_set's own wire payload and on the explicit wire_version declared
    alongside it (documented for P12 in HELD-REDUCTION-CONTRACT.candidate.v2.md).
    """
    out = package.to_dict()
    out["decision_sets"] = _serialize_decision_sets(package, wire_version=wire_version)
    return out


def _package_file_bytes(package: DailyAdvicePackage, *, wire_version: str) -> bytes:
    return (
        json.dumps(
            _package_file_dict(package, wire_version=wire_version),
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _require_verified_p12_consumer(
    package: DailyAdvicePackage,
    *,
    wire_version: str,
    p12_loader: Callable[..., Any],
    consumer_binding: dict[str, Any] | None,
) -> dict[str, tuple[Any, str]]:
    """Resolve and verify the pinned consumer once; return validated objects + digests.

    One verified pipeline for every published decision_set:

    1. consumer_binding must be the versioned binding object (schema
       quanthus.p11.p12-consumer-binding.v1) carrying non-empty source_commit /
       source_tree / loader_entry / source_files_sha256 identities - a free-standing
       caller dict is never a pinned consumer.
    2. The binding's consumer_schema must equal the wire_version the release is
       serialized on; a v1-pinned consumer can never validate a v2 wire.
    3. For an executable reduce the binding must additionally declare
       capabilities.supports_held_reduction_v2 == true.  The real pinned v2 pin is
       not delivered yet, so that declaration cannot currently exist legitimately:
       every executable reduce release stays fail-closed until the actual
       quanthus.p12_daily.decision_loader pin arrives.
    4. The loader is invoked exactly once per decision_set with the expected schema;
       the returned validated object is checked on market_date, every opportunity's
       security_code, stage and exit_kind, and each reduce's target with Decimal
       semantics.  The P12 binding digest is computed from the SAME validated object
       - a loader that drifts between a "gate call" and a "digest call" can no
       longer exist because there is only one call.
    """

    binding = consumer_binding
    has_reduce = any(
        decision.stage == "exit_plan" and decision.exit_kind == "reduce"
        for item in package.decision_sets
        for decision in item.decisions
    )
    if has_reduce:
        # The pinned consumer binding is required only to release an executable
        # reduce.  A v1 non-reduce package published with a loader still goes through
        # the same single-call validated-object pipeline for its digest, but it does
        # not need a pinned v2 consumer identity because it carries no field the v1
        # consumer could misread.
        if not isinstance(binding, dict):
            raise ProductDailyError(
                "unpublishable_package",
                "a pinned consumer binding is required to publish: supply the "
                "versioned quanthus.p11.p12-consumer-binding object, not a caller flag",
            )
        if binding.get("schema_version") != "quanthus.p11.p12-consumer-binding.v1":
            raise ProductDailyError(
                "unpublishable_package",
                "consumer_binding must be the versioned "
                "quanthus.p11.p12-consumer-binding.v1 object",
            )
        for field in ("source_commit", "source_tree", "loader_entry", "source_files_sha256"):
            value = binding.get(field)
            if not value or (field == "source_files_sha256" and not isinstance(value, dict)):
                raise ProductDailyError(
                    "unpublishable_package",
                    f"consumer_binding missing pinned identity field: {field}",
                )
        if binding.get("consumer_schema") != wire_version:
            raise ProductDailyError(
                "unpublishable_package",
                f"consumer_binding consumer_schema {binding.get('consumer_schema')!r} does "
                f"not match the release wire version {wire_version!r}; a consumer pinned "
                "to another schema cannot validate this payload",
            )
        capability = binding.get("capabilities") or {}
        if capability.get("supports_held_reduction_v2") is not True:
            raise ProductDailyError(
                "unpublishable_package",
                "an executable reduce requires a pinned P12 v2 consumer declaring "
                "capabilities.supports_held_reduction_v2; the only pinned consumer "
                "today (9de1cdb9 synthetic) drops exit_kind/target_position_fraction "
                "and would read a reduce as a full sell, so this release stays "
                "refused until the real p12.daily-advice-package.v2 pin arrives",
            )
    if has_reduce:
        # The verified-consumer restriction is what makes an executable reduce safe.
        # A non-reduce v1 release uses the loader only as a digest oracle, so it does
        # not need a pinned v2 consumer identity.
        import importlib.util

        loader_module = getattr(p12_loader, "__module__", "")
        loader_name = getattr(p12_loader, "__name__", "")
        is_planned_entry = (
            loader_module == "quanthus.p12_daily.decision_loader"
            and loader_name == "load_decision_set"
            and importlib.util.find_spec("quanthus.p12_daily.decision_loader") is not None
        )
        test_only = getattr(p12_loader, "_p11_test_only_consumer", False) is True
        if not is_planned_entry and not test_only:
            raise ProductDailyError(
                "unpublishable_package",
                "the pinned consumer for an executable reduce must be the real pinned "
                "quanthus.p12_daily.decision_loader.load_decision_set (not yet "
                "delivered - #723 comment7118368788) or an explicitly marked TEST_ONLY "
                "unit double; an unmarked caller callable is not a verified consumer",
            )
    if has_reduce:
        try:
            signature = inspect.signature(p12_loader)
            accepts_schema = "expected_schema" in signature.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()
            )
        except (TypeError, ValueError):
            accepts_schema = False
        if not accepts_schema:
            raise ProductDailyError(
                "unpublishable_package",
                "the pinned consumer loader must accept expected_schema so the wire "
                "version is enforced by the consumer, not assumed by the producer",
            )
    verified: dict[str, tuple[Any, str]] = {}
    sets = _serialize_decision_sets(package, wire_version=wire_version)
    for raw in sets:
        try:
            if has_reduce:
                validated = p12_loader(raw, expected_schema=wire_version)
            else:
                validated = p12_loader(raw)
        except ProductDailyError:
            raise
        except Exception as exc:
            if has_reduce:
                raise ProductDailyError(
                    "unpublishable_package",
                    f"pinned consumer failed to validate decision_set {raw['market_date']}: {exc}",
                ) from exc
            raise
        if has_reduce:
            # The verified consumer must agree on every serialized coordinate, not
            # merely on the fields this release happens to gate.
            if getattr(validated, "market_date", None) is not None and (
                validated.market_date.isoformat() != raw["market_date"]
            ):
                raise ProductDailyError(
                    "unpublishable_package",
                    f"validated object market_date drifted for {raw['market_date']}",
                )
            raw_by_id = {d["opportunity_id"]: d for d in raw["decisions"]}
            seen_by_id = {
                getattr(d, "opportunity_id", None): d for d in getattr(validated, "decisions", ())
            }
            if set(seen_by_id) != set(raw_by_id):
                raise ProductDailyError(
                    "unpublishable_package",
                    f"validated object dropped or invented opportunities for {raw['market_date']}",
                )
            for opportunity_id, raw_decision in raw_by_id.items():
                seen = seen_by_id[opportunity_id]
                seen_stage = getattr(seen, "stage", None)
                seen_stage = getattr(seen_stage, "value", seen_stage)
                if (
                    getattr(seen, "security_code", None) != raw_decision["security_code"]
                    or seen_stage != raw_decision["stage"]
                ):
                    raise ProductDailyError(
                        "unpublishable_package",
                        f"validated object altered security/stage on {opportunity_id}",
                    )
                if raw_decision["stage"] == "exit_plan":
                    expected_kind = raw_decision.get("exit_kind")
                    if getattr(seen, "exit_kind", None) != expected_kind:
                        raise ProductDailyError(
                            "unpublishable_package",
                            f"pinned consumer dropped or altered exit_kind on "
                            f"{opportunity_id}; an executable exit plan may only be "
                            "released through a field-preserving "
                            "p12.daily-advice-package.v2 loader",
                        )
                    if expected_kind == "reduce":
                        seen_target = getattr(seen, "target_position_fraction", None)
                        expected_target = raw_decision.get("target_position_fraction")
                        try:
                            same_target = (
                                seen_target is not None
                                and expected_target is not None
                                and (Decimal(str(seen_target)) == Decimal(str(expected_target)))
                            )
                        except Exception:
                            same_target = False
                        if not same_target:
                            raise ProductDailyError(
                                "unpublishable_package",
                                f"pinned consumer dropped or altered the reduce target "
                                f"on {opportunity_id} (Decimal comparison)",
                            )
        # Compute the binding digest from the SAME validated object that passed every
        # check above; resolve the digest function through the validated object's own
        # package so a tree without the bound P12 package fails closed.
        import importlib

        top_package = type(validated).__module__.split(".", 1)[0]
        try:
            p12_digest = importlib.import_module(top_package + ".shared.fact_contracts").digest
        except ImportError as exc:
            raise ProductDailyError(
                "p12_binding_unavailable",
                "the P12 fact_contracts digest is required for the account binding and "
                "is only present in the bound P12 package; cannot compute it here",
            ) from exc
        content = json.loads(json.dumps(dataclasses.asdict(validated), default=str, sort_keys=True))
        verified[raw["market_date"]] = (validated, p12_digest(content))
    return verified


def publish_package(
    package: DailyAdvicePackage,
    *,
    directory: Path | str,
    published_at: datetime,
    elapsed_ms: int = 0,
    p12_loader: Callable[[dict[str, Any]], Any] | None = None,
    consumer_binding: dict[str, Any] | None = None,
    wire_version: str = "p12.daily-advice-package.v1",
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

    # Wire version: the v1 byte surface is the frozen historical default and carries
    # no exit fields; an executable reduce may only be serialized on the
    # p12.daily-advice-package.v2 wire and only through the verified consumer
    # pipeline.  Serializing a reduce onto the v1 wire would let the old consumer
    # read it as an undifferentiated full sell, so that combination is refused up
    # front.
    has_reduce = any(
        decision.stage == "exit_plan" and decision.exit_kind == "reduce"
        for item in package.decision_sets
        for decision in item.decisions
    )
    if has_reduce and wire_version != "p12.daily-advice-package.v2":
        raise ProductDailyError(
            "unpublishable_package",
            "an executable reduce may only be serialized on the "
            "p12.daily-advice-package.v2 wire; on the v1 wire the pinned consumer "
            "would drop exit_kind/target_position_fraction and read it as a full sell",
        )
    # An executable reduce can never be released without the verified consumer
    # pipeline: no loader means no validated object, so it stays refused/display-only.
    if has_reduce and p12_loader is None:
        raise ProductDailyError(
            "unpublishable_package",
            "an executable reduce requires the verified pinned consumer pipeline; "
            "no loader means no validated object, so the reduce stays refused",
        )

    # One verified consumer pipeline: resolve the pinned binding, call the loader
    # exactly once per decision_set with expected_schema, match the validated object
    # on every coordinate (date/opportunity/security/stage/kind/Decimal target), and
    # compute the P12 binding digest from the SAME validated object - all before any
    # bytes land, so every invalid/no-consumer/old-loader/identity-mismatch/
    # drifting-loader path leaves zero artifacts.
    p12_digests: dict[str, str] | None = None
    if p12_loader is not None:
        verified = _require_verified_p12_consumer(
            package,
            wire_version=wire_version,
            p12_loader=p12_loader,
            consumer_binding=consumer_binding,
        )
        p12_digests = {market_date: digest for market_date, (_, digest) in verified.items()}

    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)

    # The on-disk layout is one file per (package_id, version).  A second write of the same
    # package_id for the same market date but different content is exactly what P12's
    # append-only binding rejects, so refuse before any bytes land.
    this_sets = _serialize_decision_sets(package, wire_version=wire_version)
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
    payload = _package_file_bytes(package, wire_version=wire_version)
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
