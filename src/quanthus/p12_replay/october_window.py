'''P10-pinned October replay windows + audit keys (issue #748 / #749).

Reads the frozen P10 ``WINDOW-AND-KEYS.json`` (october-action-feasibility)
and the P12 #737 exclusion config, and hands the rule an honest
``p11.raw-history-input.v1`` member set for each signal day:

* the day's actual dependency windows (event22 / feature61 / warmup61
  prior, verbatim from the P10 map - the replay never recomputes them),
* every use that depends on complete corporate-action/exception coverage
  reads ``UNKNOWN`` - the P10 review demonstrated 0 qualified
  security x window pairs, so nothing here certifies coverage,
* the 191 known direct-conflict match keys, the two missing
  implementation claims, the tax-basis cases and the 4,173 preserved
  visibility exceptions pass through verbatim (per-security x window
  intersection, source clock vs read clock kept separate),
* #737 securities/dates are filtered BEFORE the member read (P10's own
  rule: "Filter only P12 supplied identities and dates before payload
  reads for this purpose").
'''

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DEFAULT_WINDOW_KEYS = Path(
    "/Users/william/Documents/Codex/2026-11-25/"
    "quanthus-p10-october-action-feasibility/v1/WINDOW-AND-KEYS.json"
)
DEFAULT_EXCLUSION = (
    Path(__file__).resolve().parents[3]
    / "config/p12/replay-exclusion-737.v1.json"
)

AS_OBSERVED = "ENGINEERING_REPLAY_AS_OBSERVED"


@dataclass(frozen=True)
class DayWindow:
    """One signal day's verbatim P10 window map + the day's UNKNOWNs."""

    T: date
    next_session: date | None
    windows: dict
    action_census: str
    exception_census: str
    unattested_interval: list


@dataclass(frozen=True)
class ReplayExclusion:
    securities: frozenset[str]
    dates: frozenset[str]
    path: str
    sha256: str


def load_exclusion(path: Path = DEFAULT_EXCLUSION) -> ReplayExclusion:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    import hashlib

    sha = "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return ReplayExclusion(
        securities=frozenset(str(c).zfill(6) for c in doc.get("securities") or ()),
        dates=frozenset(str(d) for d in doc.get("excluded_dates") or ()),
        path=str(path),
        sha256=sha,
    )


def is_excluded(code: str, day: date, exclusion: ReplayExclusion) -> bool:
    """#737 rule: the (security, date) pair is dropped only when BOTH the
    security is listed AND the date falls inside the excluded_dates set.
    The October window (10-08..10-30) does not intersect the exclusion
    dates (11-23..11-25), so this is inert in phase 1 and documented for
    later phases - matching the exclusion file's own note."""

    return str(code).zfill(6) in exclusion.securities and day.isoformat() in exclusion.dates


@dataclass
class OctoberWindowMap:
    """The P10-pinned per-T windows and the audit keys to hand the rule."""

    doc: dict
    by_T: dict
    exclusion: ReplayExclusion

    @classmethod
    def load(
        cls,
        window_keys_path: Path = DEFAULT_WINDOW_KEYS,
        exclusion_path: Path = DEFAULT_EXCLUSION,
    ) -> "OctoberWindowMap":
        doc = json.loads(Path(window_keys_path).read_text(encoding="utf-8"))
        by_T = {}
        for item in doc.get("windows") or []:
            by_T[item["T"]] = DayWindow(
                T=date.fromisoformat(item["T"]),
                next_session=(
                    date.fromisoformat(item["next_session"])
                    if item.get("next_session")
                    else None
                ),
                windows=item.get("windows") or {},
                action_census=str(item.get("action_no_event_or_complete_census") or "UNKNOWN"),
                exception_census=str(item.get("exception_no_event_or_complete_census") or "UNKNOWN"),
                unattested_interval=list(item.get("unattested_calendar_interval_in_october") or []),
            )
        return cls(doc=doc, by_T=by_T, exclusion=load_exclusion(exclusion_path))

    def window_for(self, day: date) -> DayWindow | None:
        return self.by_T.get(day.isoformat())

    def raw_history_members(self, day: date) -> dict:
        """The p11.raw-history-input member set for one signal day.

        Every member carries its own schema + the verbatim P10 evidence;
        nothing is recomputed, filtered out, or promoted to a coverage
        claim.  #737 filtering happens upstream before native reads."""

        win = self.by_T.get(day.isoformat())
        conflicts = self.doc.get("191_effect_conflicts") or {}
        missing_impl = self.doc.get("two_missing_implementation_claims") or {}
        tax = self.doc.get("tax") or {}
        vis = self.doc.get("4173_visibility") or {}

        return {
            "windows": {
                "schema": "quanthus.p10.october-window-key-map.v1",
                "as_observed": AS_OBSERVED,
                "T": day.isoformat(),
                "next_session": win.next_session.isoformat() if win and win.next_session else None,
                "windows": win.windows if win else {},
                "unattested_calendar_interval_in_october": (
                    win.unattested_interval if win else []
                ),
            },
            "coverage": {
                "as_observed": AS_OBSERVED,
                # P10 RESULT: 0 demonstrated complete security x window
                # pairs - no use depending on action/exception coverage
                # may read qualified.  The rule decides the affected set.
                "action_no_event_or_complete_census": (
                    win.action_census if win else "UNKNOWN"
                ),
                "exception_no_event_or_complete_census": (
                    win.exception_census if win else "UNKNOWN"
                ),
                "qualified_pairs_demonstrated": 0,
                "note": (
                    "no named complete October security x window in the "
                    "reviewed metadata; dependent uses stay UNKNOWN"
                ),
            },
            "conflict_keys": {
                "as_observed": AS_OBSERVED,
                "global_records": conflicts.get("global_records", 191),
                # The rule filters by security_id + window membership
                # (direct_date_matches_by_T names which T's window each
                # conflict hits) - the keys pass through verbatim.
                "direct_coordinate_records": conflicts.get(
                    "direct_coordinate_records", []
                ),
                "join": conflicts.get("join", []),
                "key": conflicts.get("key", []),
            },
            "missing_implementation_claims": {
                "as_observed": AS_OBSERVED,
                "cases": missing_impl.get("cases", []),
                "identity_binding": missing_impl.get("identity_binding"),
            },
            "tax_basis_cases": {
                "as_observed": AS_OBSERVED,
                "cases": tax.get("cases", []),
                "affected_use": tax.get("affected_use"),
            },
            "p1_visibility_exceptions": {
                "as_observed": AS_OBSERVED,
                "original_count": vis.get("original_count", 4173),
                "exception_raw_key": vis.get("exception_raw_key", []),
                "predicate": vis.get("predicate", {}),
                "handling": (
                    "per-security dependency-window intersection; source "
                    "clock, actual read clock and logical_clock stay "
                    "separate - no cutoff is inferred"
                ),
            },
            "exclusion": {
                "as_observed": AS_OBSERVED,
                "config_path": self.exclusion.path,
                "config_sha256": self.exclusion.sha256,
                "securities": sorted(self.exclusion.securities),
                "dates": sorted(self.exclusion.dates),
                "applied_before_native_reads": True,
            },
        }
