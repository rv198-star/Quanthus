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


def decision_set_to_dict(value: MarketDecisionSet) -> dict[str, Any]:
    '''Serialize a validated set back to the G1-compatible wire shape.'''

    decisions = []
    for item in value.decisions:
        entry = item.entry
        chart = item.chart
        decisions.append(
            {
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
        )
    return {
        "source_id": value.source_ref.ref_id,
        "version": value.source_ref.version,
        "market_date": value.market_date.isoformat(),
        "knowledge_cutoff": value.knowledge_cutoff.isoformat(),
        "published_at": value.published_at.isoformat(),
        "next_session": value.next_session.isoformat(),
        "decisions": decisions,
    }
