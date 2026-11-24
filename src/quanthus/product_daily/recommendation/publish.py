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


# Three-layer version identity per #723 comment7121035802:
#
# - the P11 business package envelope carries schema_version
#   (quanthus.r11.daily-advice.v1 for the frozen v1 release;
#   p11.daily-advice-package.v2 when the release serializes v2 decision_sets);
# - each raw decision_set self-describes through its own decision_set_schema field
#   ("p12.decision-set.v2"); a v1 decision_set carries no such field and is read
#   byte-for-byte as the frozen historical payload;
# - the pinned consumer enforces the decision-set schema through
#   load_decision_set(raw, *, expected_schema="p12.decision-set.v2") - P11 only
#   ever names the decision-set layer, never the P12-owned package/projection
#   schema identities.
P11_ENVELOPE_V1 = "quanthus.r11.daily-advice.v1"
P11_ENVELOPE_V2 = "p11.daily-advice-package.v2"
V1_DS_SCHEMA = "p12.decision-set.v1"
V2_DS_SCHEMA = "p12.decision-set.v2"
DECISION_SET_SCHEMA_FIELD = "decision_set_schema"
# Wire fields that exist only on the v2 decision-set surface.
_V2_ONLY_FIELDS = ("exit_kind", "target_position_fraction")


def _serialize_decision_sets(
    package: DailyAdvicePackage, *, wire_version: str
) -> list[dict[str, Any]]:
    """Serialize decision_sets on the explicitly selected wire version.

    v1 -> the frozen historical payload: no decision_set_schema field, no exit
    fields.  v2 -> the p12.decision-set.v2 payload
    (exit_kind/target_position_fraction present) with the self-describing
    decision_set_schema field on each decision_set.  A v1 raw that still shows
    v2-only fields is a version inconsistency and is refused, never silently
    dropped; a v2 exit_plan always carries exit_kind (the Decision contract
    enforces it before serialization).  Any other version is refused.
    """
    if wire_version == V1_DS_SCHEMA:
        sets = [item.to_dict() for item in package.decision_sets]
        for raw in sets:
            for decision in raw["decisions"]:
                for field in _V2_ONLY_FIELDS:
                    if field in decision:
                        raise ProductDailyError(
                            "version_inconsistent",
                            f"decision {decision.get('opportunity_id')} on "
                            f"{raw['market_date']} carries v2-only field {field} "
                            f"without decision_set_schema={V2_DS_SCHEMA}",
                        )
        return sets
    if wire_version == V2_DS_SCHEMA:
        sets = []
        for item in package.decision_sets:
            raw = item.to_wire_dict()
            raw[DECISION_SET_SCHEMA_FIELD] = V2_DS_SCHEMA
            sets.append(raw)
        return sets
    raise ProductDailyError(
        "unpublishable_package",
        f"unregistered decision_set wire version: {wire_version}",
    )


def _package_file_dict(package: DailyAdvicePackage, *, wire_version: str) -> dict[str, Any]:
    """Package file payload for one wire version.

    The outer schema_version is the P11 envelope identity selected by the same
    versioned wire choice: quanthus.r11.daily-advice.v1 for the frozen v1
    surface, p11.daily-advice-package.v2 when the release serializes v2
    decision_sets (#723 comment7121035802).  The consumer-facing protocol
    version travels on each decision_set's own decision_set_schema field;
    business package.version keeps its own meaning.
    """
    out = package.to_dict()
    out["decision_sets"] = _serialize_decision_sets(package, wire_version=wire_version)
    if wire_version == V2_DS_SCHEMA:
        out["schema_version"] = P11_ENVELOPE_V2
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


def _consumer_pin_unresolved(project_root: Path | None = None) -> bool:
    """True when the versioned P12 v2 consumer pin is not bound to real bytes.

    The only legitimate source of consumer authority is the versioned binding file
    on disk (config/research/r11/d1b-20261124/p12-consumer.daily-advice-package.v2.json
    via runtime_identity.consumer_identity_v2).  A caller-supplied dict, a module
    attribute, a renamed function or an injected namespace is never a pin.  While
    the binding's pin_state is pending or its source_commit/tree/files are not
    resolved to real hashes, every executable-reduce and every v2 release refuses
    before any write - exactly the state today (#723: the real
    quanthus.p12_daily.decision_loader pin is not delivered).
    """

    try:
        from quanthus.policy import find_project_root
        from quanthus.product_daily.recommendation.runtime_identity import (
            consumer_identity_v2,
        )

        root = project_root if project_root is not None else find_project_root()
        binding = consumer_identity_v2(root)
    except Exception:
        return True
    if binding.get("pin_state") != "bound":
        return True
    if not binding.get("source_commit") or not binding.get("source_tree"):
        return True
    files = binding.get("source_files_sha256")
    return not isinstance(files, dict) or not files


def _validate_consumer_object(
    raw: dict[str, Any],
    validated: Any,
    *,
    wire_version: str,
    wire_digest: str | None = None,
) -> str:
    """Single validated-object coordinate check + bound-package digest.

    The consumer callable is invoked exactly once per decision_set by the caller;
    the returned object is matched on market_date, every opportunity's
    security_code, stage and exit_kind, and each reduce's target with Decimal
    semantics.  On the pinned v2 surface the recorded binding digest is the real
    loader's own wire digest over the raw bytes (passed in as wire_digest); on the
    v1 surface the historical digest is recomputed from the validated object
    through its own package so a tree without the bound P12 package fails closed.
    This function never opens the release gate by itself - _consumer_pin_unresolved
    is the gate.
    """

    if getattr(validated, "market_date", None) is not None and (
        validated.market_date.isoformat() != raw["market_date"]
    ):
        raise ProductDailyError(
            "unpublishable_package",
            f"validated object market_date drifted for {raw['market_date']}",
        )
    if wire_version != V2_DS_SCHEMA:
        # The v1 surface keeps its historical digest-only contract: the returned
        # object is trusted to be the consumer's own validated set, and only the
        # bound-package digest is recorded.  Strict per-opportunity coordinate
        # matching is a property of the p12.decision-set.v2 contract.
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
        return str(p12_digest(content))
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
        if raw_decision["stage"] == "exit_plan" and wire_version == V2_DS_SCHEMA:
            expected_kind = raw_decision.get("exit_kind")
            if getattr(seen, "exit_kind", None) != expected_kind:
                raise ProductDailyError(
                    "unpublishable_package",
                    f"pinned consumer dropped or altered exit_kind on "
                    f"{opportunity_id}; an executable exit plan may only be "
                    f"released through a field-preserving {V2_DS_SCHEMA} loader",
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
    if wire_version == V2_DS_SCHEMA and wire_digest is not None:
        # The pinned contract's own digest over the raw wire bytes - including the
        # v2 fields - is the binding identity.  It comes from the same pinned call
        # that produced the validated object above, so a tampered payload that
        # never validates can never carry a forged digest through this surface.
        return wire_digest
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
    return str(p12_digest(content))


def publish_package(
    package: DailyAdvicePackage,
    *,
    directory: Path | str,
    published_at: datetime,
    elapsed_ms: int = 0,
    p12_loader: Callable[..., Any] | None = None,
    consumer_binding: dict[str, Any] | None = None,
    wire_version: str | None = None,
) -> PublishReceipt:
    """Write one package file append-only and return a receipt with all digest surfaces.

    published_at must equal every decision_set's own published_at; a mismatch would
    produce an artifact whose receipt disagrees with its payload, so it is refused.

    When p12_loader is supplied (a callable that validates one raw decision_set dict
    into a MarketDecisionSet) the receipt additionally carries the P12 binding digest
    for each released date; without it p12_package_digests is None and the caller
    must not treat the file as bound on the consumer side.

    The registered release selects the wire using the package policy identity.
    wire_version, if supplied by a compatibility caller, only asserts that choice;
    it never selects or downgrades a release.  Historical G1 fixture publication
    has a distinct registered policy and still emits the frozen v1 bytes.

    consumer_binding is retained for signature compatibility and grants no authority.
    """

    del consumer_binding  # never a release authority; see docstring
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

    from quanthus.policy import find_project_root
    from quanthus.product_daily.recommendation.release_contract import select_package_release

    release = select_package_release(package, find_project_root())
    if wire_version is not None and wire_version != release.wire_version:
        raise ProductDailyError(
            "version_inconsistent",
            "caller wire differs from the configured release; wire_version is an assertion only",
        )
    wire_version = release.wire_version

    # Wire version: the v1 byte surface is the frozen historical default and carries
    # no exit fields and no decision_set_schema header; an executable reduce may
    # only be serialized on the p12.decision-set.v2 surface and only through the
    # pinned consumer.  Serializing a reduce onto the v1 surface would let the old
    # consumer read it as an undifferentiated full sell, so that combination is
    # refused up front.
    has_reduce = any(
        decision.stage == "exit_plan" and decision.exit_kind == "reduce"
        for item in package.decision_sets
        for decision in item.decisions
    )
    if has_reduce and wire_version != V2_DS_SCHEMA:
        raise ProductDailyError(
            "unpublishable_package",
            "an executable reduce may only be serialized on the "
            f"{V2_DS_SCHEMA} wire; on the v1 wire the pinned consumer "
            "would drop exit_kind/target_position_fraction and read it as a full sell",
        )
    # The release gate is the versioned consumer pin, never a caller-supplied
    # loader, binding dict, attribute, module name or injected namespace.  While
    # the real p12.decision-set.v2 pin is pending, every executable reduce and
    # every v2-surface release refuses before a single byte is staged.
    needs_v2_consumer = has_reduce or wire_version == V2_DS_SCHEMA
    if needs_v2_consumer and _consumer_pin_unresolved():
        raise ProductDailyError(
            "unpublishable_package",
            "the versioned P12 v2 consumer pin (p12.decision-set.v2, "
            "quanthus.p12_daily.decision_loader.load_decision_set) is still "
            "pending - no caller loader, binding dict, attribute or namespace "
            "opens a public release until the real pin is bound",
        )
    # Once the versioned pin is bound, the consumer pipeline is resolved from the
    # binding itself - never from a caller-supplied callable.  A caller p12_loader
    # remains usable only on the v1 surface where no v2 release is involved; on
    # the v2 surface it is ignored rather than trusted.
    if needs_v2_consumer:
        from quanthus.product_daily.recommendation.runtime_identity import (
            resolve_pinned_v2_loader,
        )

        try:
            pinned_loader = resolve_pinned_v2_loader(find_project_root())
        except ProductDailyError as exc:
            raise ProductDailyError(
                "unpublishable_package",
                f"the bound P12 v2 consumer could not be resolved from its "
                f"versioned pin: {exc}",
            ) from exc
        p12_loader = pinned_loader
    elif p12_loader is None and has_reduce:
        raise ProductDailyError(
            "unpublishable_package",
            "an executable reduce requires the pinned consumer pipeline; "
            "no loader means no validated object, so the reduce stays refused",
        )

    # Single-call consumer pipeline: the loader is invoked exactly once per
    # decision_set (with expected_schema on the v2 surface), the returned object
    # is matched on every coordinate by _validate_consumer_object, and the binding
    # digest comes from the SAME validated object - all before any bytes land.
    p12_digests: dict[str, str] | None = None
    if p12_loader is not None:
        p12_digests = {}
        for raw in _serialize_decision_sets(package, wire_version=wire_version):
            try:
                loaded = p12_loader(raw)
            except ProductDailyError:
                raise
            except Exception as exc:
                if needs_v2_consumer:
                    raise ProductDailyError(
                        "unpublishable_package",
                        f"pinned consumer failed to validate decision_set "
                        f"{raw['market_date']}: {exc}",
                    ) from exc
                raise
            if wire_version == V2_DS_SCHEMA:
                # The pinned contract returns (MarketDecisionSet, wire_digest); the
                # validated object feeds coordinate checks, the wire digest covers
                # the raw bytes and is recorded as the binding digest.  An object
                # without the tuple shape is not the pinned reader and is refused.
                if (
                    not isinstance(loaded, tuple)
                    or len(loaded) != 2
                    or not isinstance(loaded[1], str)
                    or not loaded[1].startswith("sha256:")
                ):
                    raise ProductDailyError(
                        "unpublishable_package",
                        "the pinned v2 consumer did not return the declared "
                        "(MarketDecisionSet, wire_digest) pair",
                    )
                validated, wire_digest = loaded
            else:
                validated, wire_digest = loaded, None
            p12_digests[raw["market_date"]] = _validate_consumer_object(
                raw, validated, wire_version=wire_version, wire_digest=wire_digest
            )

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
