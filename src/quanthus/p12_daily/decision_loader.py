'''Load one wire decision_set dict into a validated P12 MarketDecisionSet.

The wire shape intentionally matches P11 G1 DailyAdvicePackage.decision_sets
entries (source_id/version/market_date/knowledge_cutoff/published_at/
next_session/decisions), so batch B can point the same loader at a G1-produced
package with no translation layer.  The caller declares the admitted mode and
synthetic flag; the loader never trusts a mode carried on the wire.

A published package must not contain an input_blocked decision (D1-B frozen
contract, 2026-11-24): blocked input lives only in run receipts, so the loader
refuses the stage up front.
'''

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from quanthus.capabilities.decision_access import (
    ChartEvidence,
    EntryIntent,
    Explanation,
    ExplanationUse,
    MarketDecision,
    MarketDecisionSet,
    OpportunityStage,
    PriceBar,
    validate_decision_set,
)
from quanthus.shared.fact_contracts import FactError, digest
from quanthus.shared.values import ADMITTED_MODES, VersionRef

INPUT_BLOCKED_IN_PACKAGE = "INPUT_BLOCKED_IN_PACKAGE"

# p12.decision-set.v2 (issue #723 comments 7118368788/7121035802/7121082946):
# the inner decision_set carries decision_set_schema="p12.decision-set.v2".
# A set without the field reads as v1 with the original bytes and digest
# unchanged; a set that carries exit_kind or target_position_fraction while
# missing the field is a version breach and is refused outright.
DECISION_SET_SCHEMA_V1 = "p12.decision-set.v1"
DECISION_SET_SCHEMA_V2 = "p12.decision-set.v2"
SUPPORTED_DECISION_SET_SCHEMAS = frozenset(
    {DECISION_SET_SCHEMA_V1, DECISION_SET_SCHEMA_V2}
)
_V2_ONLY_FIELDS = ("exit_kind", "target_position_fraction")
# Decimal-string shape admitted on the wire: optional minus, digits, optional
# fractional part.  A JSON number (0.5), exponent form ("5e-1"), a bare "." or
# a non-string token would still coerce through Decimal, so the v2 reader
# rejects them up front to keep wire and emitted schema aligned (issue #723).
_DECIMAL_WIRE_CHARS = frozenset("-0123456789.")

def _aware(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise FactError("INVALID_PACKAGE", f"decision_set {field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FactError("INVALID_PACKAGE", f"decision_set {field} must be timezone-aware")
    return parsed


def _decimal(value: Any, field: str) -> Decimal:
    try:
        out = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - fail closed on malformed decimals
        raise FactError("INVALID_PACKAGE", f"decision_set {field} is not a decimal") from exc
    if not out.is_finite():
        raise FactError("INVALID_PACKAGE", f"decision_set {field} must be finite")
    return out


def _wire_decimal(value: Any, field: str) -> Decimal:
    r"""Parse a Decimal that must arrive as a plain JSON string.

    The v2 schema types target_position_fraction as a string matching
    ``^-?[0-9]+(\.[0-9]+)?$``.  A bare JSON number, exponent notation or a
    non-string token would still coerce through ``Decimal``; this guard
    refuses them so the wire stays a plain decimal string.
    """

    if not isinstance(value, str):
        raise FactError(
            "INVALID_PACKAGE",
            f"decision_set {field} must be a decimal string, not "
            f"{type(value).__name__}",
        )
    body = value.lstrip("-")
    if (
        not value
        or not set(value) <= _DECIMAL_WIRE_CHARS
        or value.count(".") > 1
        or body in ("", ".")
        or body.startswith(".")
        or body.endswith(".")
    ):
        raise FactError(
            "INVALID_PACKAGE",
            f"decision_set {field} is not a plain decimal string",
        )
    return _decimal(value, field)


def _chart(raw: Any) -> ChartEvidence | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise FactError("INVALID_PACKAGE", "chart must be an object or null")
    bars = []
    for index, bar in enumerate(raw.get("bars") or ()):
        bars.append(
            PriceBar(
                session=date.fromisoformat(str(bar["session"])),
                open=_decimal(bar["open"], f"chart.bars[{index:d}].open"),
                high=_decimal(bar["high"], f"chart.bars[{index:d}].high"),
                low=_decimal(bar["low"], f"chart.bars[{index:d}].low"),
                close=_decimal(bar["close"], f"chart.bars[{index:d}].close"),
                volume=int(bar["volume"]),
            )
        )
    return ChartEvidence(
        price_basis=str(raw.get("price_basis", "none")),
        as_of=_aware(raw.get("as_of"), "chart.as_of"),
        bars=tuple(bars),
        support=None if raw.get("support") is None else _decimal(raw["support"], "chart.support"),
        volume_baseline=raw.get("volume_baseline"),
        volume_baseline_window=raw.get("volume_baseline_window"),
    )


def _decision(raw: dict[str, Any]) -> MarketDecision:
    stage = OpportunityStage(str(raw["stage"]))
    if stage is OpportunityStage.INPUT_BLOCKED:
        raise FactError(
            INPUT_BLOCKED_IN_PACKAGE,
            "a published package may not carry an input_blocked decision; "
            "blocked input belongs in the run receipt",
        )
    entry_raw = raw.get("entry")
    entry = None
    if entry_raw is not None:
        entry = EntryIntent(
            fraction=_decimal(entry_raw["fraction"], "entry.fraction"),
            price_bound=_decimal(entry_raw["price_bound"], "entry.price_bound"),
            mark_price=_decimal(entry_raw["mark_price"], "entry.mark_price"),
            industry_id=str(entry_raw["industry_id"]),
            eligible_at=_aware(entry_raw["eligible_at"], "entry.eligible_at"),
            expires_at=_aware(entry_raw["expires_at"], "entry.expires_at"),
            window_ref=str(entry_raw["window_ref"]),
            invalidations=tuple(str(x) for x in entry_raw.get("invalidations") or ()),
        )
    component = raw.get("component") or {}
    exit_kind = raw.get("exit_kind")
    target_fraction = raw.get("target_position_fraction")
    return MarketDecision(
        opportunity_id=str(raw["opportunity_id"]),
        security_code=str(raw["security_code"]),
        security_name=str(raw.get("security_name") or raw["security_code"]),
        stage=stage,
        reason_codes=tuple(str(x) for x in raw.get("reason_codes") or ()),
        explanations=tuple(
            Explanation(
                str(item["code"]),
                ExplanationUse(str(item["use"])),
                str(item["component_ref"]),
                str(item["text"]),
            )
            for item in raw.get("explanations") or ()
        ),
        next_observation=raw.get("next_observation"),
        chart=_chart(raw.get("chart")),
        entry=entry,
        component_ref=VersionRef(
            str(component.get("ref_id") or "unknown"),
            str(component.get("version") or "0"),
            digest(raw),
        ),
        exit_kind=None if exit_kind is None else str(exit_kind),
        target_position_fraction=(
            None
            if target_fraction is None
            else _wire_decimal(
                target_fraction, "decisions[].target_position_fraction"
            )
        ),
    )


def load_decision_set_dict(
    raw: dict[str, Any],
    *,
    mode: str,
    source_digest: str | None = None,
) -> MarketDecisionSet:
    '''Validate one wire decision_set dict into a MarketDecisionSet.

    mode is the account-side admitted mode ("synthetic" for batch A), so a G1
    simulation package cannot be loaded into a synthetic binding - and a wire
    "mode" field, if present, must agree with it.
    '''

    if not isinstance(raw, dict):
        raise FactError("INVALID_PACKAGE", "decision_set must be an object")
    if mode not in ADMITTED_MODES:
        raise FactError("POLICY_NOT_ADOPTED", "unknown decision mode")
    wire_mode = raw.get("mode")
    if wire_mode is not None and str(wire_mode) != mode:
        raise FactError(
            "POLICY_NOT_ADOPTED",
            f"decision_set wire mode {wire_mode!r} cannot serve mode {mode!r}",
        )
    # Version gate: the v1 reader admits a bare decision_set only.  A raw set
    # that declares decision_set_schema is handed to the version-aware path;
    # this reader refuses any declared schema it does not know, so a v2 file
    # is never silently read with its new fields dropped (issue #723).
    declared = raw.get("decision_set_schema")
    if declared is not None and str(declared) != DECISION_SET_SCHEMA_V1:
        raise FactError(
            "INVALID_PACKAGE",
            f"decision_set_schema {declared!r} is not admitted by this reader",
        )
    # A bare set that smuggles a v2-only field is a version breach: the legacy
    # reader must refuse it outright rather than silently drop the new
    # fields.  Key presence decides, matching the version-aware loader.
    leaked = [f for f in _V2_ONLY_FIELDS if _any_decision_has(raw, f)]
    if leaked:
        raise FactError(
            "INVALID_PACKAGE",
            "decision_set carries v2-only fields "
            f"{tuple(leaked)!r} without decision_set_schema",
        )
    return validate_decision_set(
        MarketDecisionSet(
            source_ref=VersionRef(
                str(raw["source_id"]),
                str(raw["version"]),
                source_digest or digest(raw),
            ),
            mode=mode,
            synthetic=(mode == "synthetic"),
            market_date=date.fromisoformat(str(raw["market_date"])),
            knowledge_cutoff=_aware(raw["knowledge_cutoff"], "knowledge_cutoff"),
            published_at=_aware(raw["published_at"], "published_at"),
            next_session=date.fromisoformat(str(raw["next_session"])),
            decisions=tuple(_decision(item) for item in raw.get("decisions") or ()),
        )
    )


def load_decision_set(
    raw: dict[str, Any],
    *,
    expected_schema: str,
    mode: str | None = None,
    source_digest: str | None = None,
) -> tuple[MarketDecisionSet, str]:
    '''Version-pinned decision_set reader (issue #723 first step).

    The consumer declares the schema it admits through ``expected_schema``.
    Returns ``(MarketDecisionSet, digest)`` where the digest covers the raw
    wire bytes - including the new v2 fields - so a tampered payload never
    validates under the pinned digest.

    * ``expected_schema="p12.decision-set.v2"``: ``decision_set_schema`` must
      be present and equal, and every ``exit_plan`` decision must carry an
      ``exit_kind``.  A set missing the marker reads as v1, never as v2.
    * A set without ``decision_set_schema`` reads as v1 with the original
      bytes and digest unchanged - unless it carries a v2-only field
      (``exit_kind`` / ``target_position_fraction``), a version breach refused
      regardless of the pin.
    * ``expected_schema="p12.decision-set.v1"`` admits only a bare (unmarked)
      set; a declared v2 set is refused, never downgraded.

    ``mode`` is optional.  When it is ``None`` (the frozen call signature) the
    set is loaded without an account-mode binding: ``MarketDecisionSet.mode``
    falls back to the wire ``mode`` field, or ``"simulation"`` when the wire
    carries none.  When ``mode`` is supplied it is the account-side admitted
    mode and is enforced against a wire ``mode`` field exactly as the v1
    reader does.  The published-package entry point always passes the account
    mode, so wire/account isolation is enforced for v1 and v2 alike at the
    package boundary - never silently relaxed here (issue #723 second step).
    '''

    if not isinstance(raw, dict):
        raise FactError("INVALID_PACKAGE", "decision_set must be an object")
    if expected_schema not in SUPPORTED_DECISION_SET_SCHEMAS:
        raise FactError(
            "POLICY_NOT_ADOPTED",
            f"unsupported expected_schema {expected_schema!r}",
        )

    declared = raw.get("decision_set_schema")

    # A bare set is v1 - unless it smuggles a v2-only field, which is a
    # version breach refused regardless of the expected schema.
    if declared is None:
        leaked = [f for f in _V2_ONLY_FIELDS if _any_decision_has(raw, f)]
        if leaked:
            raise FactError(
                "INVALID_PACKAGE",
                "decision_set carries v2-only fields "
                f"{tuple(leaked)!r} without decision_set_schema",
            )
        if expected_schema == DECISION_SET_SCHEMA_V2:
            raise FactError(
                "INVALID_PACKAGE",
                "expected a v2 decision_set but the schema marker is absent",
            )
        value = _load_marked(raw, mode=mode, source_digest=source_digest)
        return value, digest(raw)

    declared = str(declared)
    if declared != expected_schema:
        raise FactError(
            "INVALID_PACKAGE",
            f"decision_set_schema {declared!r} does not match expected "
            f"{expected_schema!r}",
        )
    if declared == DECISION_SET_SCHEMA_V2:
        # v2 requires every exit_plan decision to declare its exit kind, and
        # the kind binds the target: a reduce must carry a Decimal-string
        # target in (0, 1) so the reduction has a usable denominator, while a
        # full exit must leave the target null.  A missing target on reduce
        # makes the reduction unusable and is refused up front (issue #723).
        for item in raw.get("decisions") or ():
            if str(item.get("stage")) != "exit_plan":
                continue
            kind = item.get("exit_kind")
            if kind is None:
                raise FactError(
                    "INVALID_PACKAGE",
                    "a v2 exit_plan decision must carry exit_kind",
                )
            target = item.get("target_position_fraction")
            if kind == "reduce":
                if target is None:
                    raise FactError(
                        "INVALID_PACKAGE",
                        "a v2 reduce exit_plan must carry "
                        "target_position_fraction",
                    )
                parsed = _wire_decimal(
                    target, "decisions[].target_position_fraction"
                )
                if not (Decimal("0") < parsed < Decimal("1")):
                    raise FactError(
                        "INVALID_PACKAGE",
                        "a v2 reduce target_position_fraction must be in (0, 1)",
                    )
            elif kind == "full" and target is not None:
                raise FactError(
                    "INVALID_PACKAGE",
                    "a v2 full exit_plan must leave "
                    "target_position_fraction null",
                )
    value = _load_marked(raw, mode=mode, source_digest=source_digest)
    return value, digest(raw)


def _any_decision_has(raw: dict[str, Any], field: str) -> bool:
    # Key presence decides the version breach, not the value: an explicit
    # null ``exit_kind`` / ``target_position_fraction`` still marks the set as
    # v2-shaped and is refused without the schema marker (issue #723).
    return any(
        isinstance(item, dict) and field in item
        for item in raw.get("decisions") or ()
    )


def _effective_mode(raw: dict[str, Any], admitted: str | None) -> str:
    """Resolve the account mode a decision_set binds to.

    A supplied ``admitted`` mode is the caller's account-side binding; a wire
    ``mode`` field must then agree with it.  When ``admitted`` is ``None`` (the
    frozen reader signature) no account binding is asserted, so the set adopts
    the wire ``mode`` and defaults to ``"simulation"`` when the wire carries
    none.  The published-package entry always supplies its account mode.
    """

    wire_mode = raw.get("mode")
    if admitted is not None:
        if admitted not in ADMITTED_MODES:
            raise FactError("POLICY_NOT_ADOPTED", "unknown decision mode")
        if wire_mode is not None and str(wire_mode) != admitted:
            raise FactError(
                "POLICY_NOT_ADOPTED",
                f"decision_set wire mode {wire_mode!r} cannot serve mode "
                f"{admitted!r}",
            )
        return admitted
    if wire_mode is not None:
        if str(wire_mode) not in ADMITTED_MODES:
            raise FactError(
                "POLICY_NOT_ADOPTED",
                f"decision_set wire mode {wire_mode!r} is not admitted",
            )
        return str(wire_mode)
    return "simulation"


def _load_marked(
    raw: dict[str, Any],
    *,
    mode: str | None,
    source_digest: str | None,
) -> MarketDecisionSet:
    """Validate a set that declared a recognized decision_set_schema.

    ``mode=None`` means no account binding is asserted here: the set adopts the
    wire ``mode`` field, or ``"simulation"`` when absent.  A supplied ``mode``
    is the account-admitted mode and must agree with a wire ``mode`` field.
    """
    effective = _effective_mode(raw, mode)
    return validate_decision_set(
        MarketDecisionSet(
            source_ref=VersionRef(
                str(raw["source_id"]),
                str(raw["version"]),
                source_digest or digest(raw),
            ),
            mode=effective,
            synthetic=(effective == "synthetic"),
            market_date=date.fromisoformat(str(raw["market_date"])),
            knowledge_cutoff=_aware(raw["knowledge_cutoff"], "knowledge_cutoff"),
            published_at=_aware(raw["published_at"], "published_at"),
            next_session=date.fromisoformat(str(raw["next_session"])),
            decisions=tuple(_decision(item) for item in raw.get("decisions") or ()),
        )
    )


def decision_set_to_dict(value: MarketDecisionSet) -> dict[str, Any]:
    '''Serialize a validated set back to the G1-compatible wire shape.

    When any decision carries a v2 field (exit_kind / target_position_fraction)
    the wire set is marked ``decision_set_schema="p12.decision-set.v2"`` and each
    decision emits its v2 fields; otherwise the v1 shape is emitted unchanged
    (issue #723).
    '''

    v2 = any(
        item.exit_kind is not None or item.target_position_fraction is not None
        for item in value.decisions
    )

    decisions = []
    for item in value.decisions:
        entry = item.entry
        chart = item.chart
        wire = {
                "opportunity_id": item.opportunity_id,
                "security_code": item.security_code,
                "security_name": item.security_name,
                "stage": item.stage.value,
                "component": {
                    "ref_id": item.component_ref.ref_id,
                    "version": item.component_ref.version,
                },
                "reason_codes": list(item.reason_codes),
                "explanations": [
                    {
                        "code": x.code,
                        "use": x.use.value,
                        "component_ref": x.component_ref,
                        "text": x.text,
                    }
                    for x in item.explanations
                ],
                "next_observation": item.next_observation,
                "chart": None
                if chart is None
                else {
                    "price_basis": chart.price_basis,
                    "as_of": chart.as_of.isoformat(),
                    "support": None if chart.support is None else str(chart.support),
                    "volume_baseline": chart.volume_baseline,
                    "volume_baseline_window": chart.volume_baseline_window,
                    "bars": [
                        {
                            "session": bar.session.isoformat(),
                            "open": str(bar.open),
                            "high": str(bar.high),
                            "low": str(bar.low),
                            "close": str(bar.close),
                            "volume": bar.volume,
                        }
                        for bar in chart.bars
                    ],
                },
                "entry": None
                if entry is None
                else {
                    "fraction": str(entry.fraction),
                    "price_bound": str(entry.price_bound),
                    "mark_price": str(entry.mark_price),
                    "industry_id": entry.industry_id,
                    "eligible_at": entry.eligible_at.isoformat(),
                    "expires_at": entry.expires_at.isoformat(),
                    "window_ref": entry.window_ref,
                    "invalidations": list(entry.invalidations),
                },
            }
        if v2:
            wire["exit_kind"] = item.exit_kind
            wire["target_position_fraction"] = (
                None
                if item.target_position_fraction is None
                else str(item.target_position_fraction)
            )
        decisions.append(wire)
    body = {
        "source_id": value.source_ref.ref_id,
        "version": value.source_ref.version,
        "market_date": value.market_date.isoformat(),
        "knowledge_cutoff": value.knowledge_cutoff.isoformat(),
        "published_at": value.published_at.isoformat(),
        "next_session": value.next_session.isoformat(),
        "decisions": decisions,
    }
    if v2:
        body["decision_set_schema"] = DECISION_SET_SCHEMA_V2
    return body
