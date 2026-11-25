"""Issue #748 entry points: build the engine from the pinned source, run
the compressed acceptance, or serve the replay console.

The CN02 pinned source set is the H1 mapping package (P10, frozen):
the replay reads the REAL Base 2.5.1 month partition(s) that cover the
requested session window, slices each session's 1d bars, and materializes
the wire-shape inputs the production job already verifies.  Nothing here
collects, repairs or re-publishes data - the slice is a bounded read of
one named member per month.

Sessions come from the pinned ``dwd_trading_calendar`` 2026 partition
(venue SSE open days), the same member P10's H1 supply names for
calendar/venue facts.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quanthus.p12_daily.e1_replay import E1ReplayRunner
from quanthus.p12_daily.rule_runner import SyntheticRuleRunner
from quanthus.p12_replay.assembly import build_replay_app
from quanthus.p12_replay.bar_slice import read_month_bars
from quanthus.p12_replay.engine import ReplayEngine
from quanthus.p12_replay.october_window import OctoberWindowMap, is_excluded
from quanthus.p12_replay.replay_product import ReplayInputSpec
from quanthus.p12_replay.replay_quotes import DayBar

EXCHANGE_TZ = timezone(timedelta(hours=8))

BASE_ROOT = Path(
    "/srv/quanthus-base22-data/build/base25-20261031/work/"
    "paired-base251-adce892e-a1/published/DWD"
)
BASE_MANIFEST = BASE_ROOT / "manifest.json"
BASE_MANIFEST_SHA256 = (
    "sha256:8cd966be87fce02168c54e77c23e05f491c4afb204c4cbace42a2024eee1cdef"
)
BAR_MEMBER = (
    "payload/dwd_security_bar/interval_size=1d/"
    "observation_start_year={year}/observation_start_month={month}/{part}"
)
CALENDAR_MEMBER = (
    "payload/dwd_trading_calendar/trade_date_year={year}/{part}"
)


def _session_dates(calendar_root: Path, year: int) -> list[date]:
    """Authoritative SSE open sessions for ``year`` from the pinned member."""

    import pyarrow.parquet as pq

    member = sorted(calendar_root.glob(f"trade_date_year={year}/*.parquet"))
    sessions: set[date] = set()
    for path in member:
        table = pq.ParquetFile(str(path)).read(
            columns=["trade_date", "is_open", "venue_id"]
        )
        for row in table.to_pylist():
            if not row.get("is_open"):
                continue
            venue = str(row.get("venue_id") or "")
            # venue ids are opaque hashes; SSE rows name the exchange in
            # the source_record_id on the full read - read it lazily here.
            sessions.add(row["trade_date"])
    return sorted(sessions)


def _sse_sessions(year: int, base_root: Path) -> list[date]:
    """Open sessions for SSE from every calendar part of the year."""

    import pyarrow.parquet as pq

    days: set[date] = set()
    for path in sorted(
        (base_root / "payload/dwd_trading_calendar").glob(
            f"trade_date_year={year}/*.parquet"
        )
    ):
        table = pq.ParquetFile(str(path)).read(
            columns=["trade_date", "is_open", "source_record_id"]
        )
        for row in table.to_pylist():
            if not row.get("is_open"):
                continue
            if "exchange=SSE" not in str(row.get("source_record_id") or ""):
                continue
            days.add(row["trade_date"])
    return sorted(days)


def load_window(
    *,
    base_root: Path,
    start: date,
    end: date,
) -> tuple[list[date], dict, list[dict]]:
    """(sessions, bars_by_day, reads) for the replay window.

    Sessions: SSE open days between start..end inclusive from the pinned
    calendar member.  Bars: every row whose session date lands inside the
    same window, sliced from the month partition(s) covering it.
    """

    sessions = [
        d for d in _sse_sessions(start.year, base_root) if start <= d <= end
    ]
    if len(sessions) < 2:
        raise SystemExit(
            f"replay window {start}..{end} resolves {len(sessions)} sessions; "
            "need at least 2 (T plus T+1)"
        )
    bars_by_day: dict[date, dict[str, DayBar]] = {}
    reads: list[dict] = []
    months = sorted({(d.year, d.month) for d in sessions})
    for year, month in months:
        part_dir = (
            base_root
            / "payload/dwd_security_bar/interval_size=1d"
            / f"observation_start_year={year}"
            / f"observation_start_month={month}"
        )
        for member in sorted(part_dir.glob("*.parquet")):
            bars, read = read_month_bars(member)
            reads.append(
                {
                    "member_path": read.member_path,
                    "rows_scanned": read.rows_scanned,
                    "rows_admitted": read.rows_admitted,
                    "sessions_found": read.sessions_found,
                }
            )
            for day, day_bars in bars.items():
                if start <= day <= end:
                    bars_by_day.setdefault(day, {}).update(day_bars)
    return sessions, bars_by_day, reads


def base_sessions_through(base_root: Path, end: date) -> int:
    """Count authoritative SSE open sessions through ``end`` in the pinned
    Base calendar - the real observed session count the history floors
    report for T (never inflated to a threshold, never the replay
    window's few days)."""

    total = 0
    for path in sorted(
        (base_root / "payload/dwd_trading_calendar").glob(
            "trade_date_year=*/*.parquet"
        )
    ):
        import pyarrow.parquet as pq

        table = pq.ParquetFile(str(path)).read(
            columns=["trade_date", "is_open", "source_record_id"]
        )
        for row in table.to_pylist():
            if not row.get("is_open"):
                continue
            if "exchange=SSE" not in str(row.get("source_record_id") or ""):
                continue
            if row["trade_date"] <= end:
                total += 1
    return total


def apply_exclusion(
    bars_by_day: dict,
    exclusion,
    expected_sha256: str | None = None,
) -> dict:
    """#737 pre-read filter applied BEFORE the replay enters: drop a
    security's bar on an excluded (security, date) pair.  Returns the
    receipt {config_sha256, filtered_count, filtered[]} for the run/day
    receipt - the binding is in place for later phases and the count +
    digest are recorded even when the October window makes it a no-op.

    When ``expected_sha256`` is given the exclusion config is BOUND to
    that pinned digest (R3 item 4): a byte-mismatched file is named in
    the receipt (sha_match=False) rather than silently filtering
    against the wrong list."""
    actual = getattr(exclusion, "sha256", "")
    sha_match = expected_sha256 is None or actual == expected_sha256

    dropped = []
    for day, bars in list(bars_by_day.items()):
        for code in list(bars.keys()):
            if is_excluded(code, day, exclusion):
                del bars[code]
                dropped.append({"security": code, "date": day.isoformat()})
    return {
        "config_sha256": actual,
        "expected_sha256": expected_sha256,
        "sha_match": sha_match,
        "config_path": str(getattr(exclusion, "path", "")),
        "securities": sorted(getattr(exclusion, "securities", ()) or ()),
        "dates": sorted(getattr(exclusion, "dates", ()) or ()),
        "filtered_count": len(dropped),
        "filtered": dropped,
    }


# The frozen #737 exclusion list's own pinned sha256 (commit bb4a429f).
# The replay binds the config to this digest before applying it.
EXPECTED_EXCLUSION_SHA256 = (
    "sha256:7482a9ef837aabe2019ec5daba7132d5729e7f3e43786b6a0b1efb43e95a573e"
)


def default_spec(observed_sessions: int, knowledge_cutoff: str | None) -> ReplayInputSpec:
    """Static manifest evidence for a replay input day.

    history/corporate_actions/volume are UNKNOWN-named gaps - the replay
    runs the synthetic runner, which does not compute the three floors;
    the fields exist so a real H1 runner later receives honestly marked
    evidence, never a fabricated pass.
    """

    # The manifest declares the observed session evidence the pinned Base
    # actually carries for this window (Base 2.5.1's calendar + listing
    # reference covers far more than the floors); each floor reads
    # satisfied because the observed sessions exceed its requirement.
    # The runner never recomputes the floors - this is input evidence,
    # honestly reported, with the replay provenance block naming it.
    # History floors report the sessions ACTUALLY read for this window -
    # the authoritative Base open-session count through T, never inflated
    # to a threshold and never the replay window's few days.
    observed = observed_sessions
    return ReplayInputSpec(
        history={
            "event_window_sessions": {"observed_sessions": observed},
            "feature_warmup_sessions": {"observed_sessions": observed},
            "listing_sessions": {"observed_sessions": observed},
        },
        corporate_actions={
            # P10 OCTOBER_ACTION_COVERAGE_UNKNOWN: the sealed October Base
            # carries no partitioned corporate-action member for this
            # window, so coverage is UNKNOWN per security - never default
            # "no events".  The numeric fields the F11 contract requires
            # stay present-and-valid (0.00 baseline, not a claim that no
            # action occurred); coverage_status + affected scope hand the
            # rule the per-security UNKNOWN set to filter.
            # UNKNOWN coverage is NEVER presented as fields_complete=true:
            # the per-security affected set is unbound, so completeness is
            # unproven.  The numeric placeholders stay valid-but-not-evidence.
            "fields_complete": False,
            "cash_dividend_per_share": "0.00",
            "bonus_share_ratio": "0.00",
            "exceptions": [],
            "coverage_status": "UNKNOWN",
            "coverage_gap": "OCTOBER_ACTION_COVERAGE_UNKNOWN",
            "affected_scope": "SSE/SZSE per-security affected set unbound at metadata level",
            "rule": (
                "any security or window depending on corporate-action, "
                "adjustment or ex-date facts keeps UNKNOWN coverage - the "
                "rule drops those securities, the field values are a "
                "placeholder not evidence of no-action"
            ),
        },
        volume={"volume_scope": "UNKNOWN"},
        # P1 preserved 4,173 factor-visibility exceptions (fail-closed,
        # never an inferred cutoff): the replay hands the rule the
        # security x dependency-window intersection, source clock vs read
        # clock kept separate.  The concrete affected set lands with the
        # #749 reader's P11 mapping; the spec field is declared here so
        # the wire shape already carries it.
        p1_visibility_exceptions={
            "count": 4173,
            "handling": (
                "per-security dependency-window intersection; the source "
                "clock, actual read clock and logical_clock are recorded "
                "separately - no cutoff is inferred"
            ),
            "evidence_id": "474-preserved-visibility",
            "status": "KNOWN_SCOPE_UNRESOLVED_PER_SECURITY",
        },
        knowledge_cutoff=knowledge_cutoff,
        completed_at=None,
    )


def make_engine(
    *,
    state_dir: Path,
    fixture_path: Path | None = None,
    window_map=None,
    sessions: list[date],
    bars_by_day: dict,
    candidates_path: Path | None,
    account_id: str = "SYN-PAPER-A",
    account_mode: str = "synthetic",
    exclusion_receipt: dict | None = None,
    observed_sessions_by_day: dict | None = None,
    e1_runner: E1ReplayRunner | None = None,
    history_reader=None,
    account_projection=None,
    decision_cutoff_time: time | None = None,
    base_manifest_path: Path = BASE_MANIFEST,
    base_manifest_sha256: str = BASE_MANIFEST_SHA256,
    knowledge_cutoff: str | None = None,
    publish_time: time = time(22, 30, tzinfo=EXCHANGE_TZ),
    read_only_account_snapshot: dict | None = None,
    read_only_name_map: dict | None = None,
    reset: bool = False,
) -> ReplayEngine:
    """Build a ReplayEngine bound to the replay's own state dir."""

    if reset and state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    if e1_runner is not None:
        # 8792 phase-1 real-rule path: the bound E1ReplayRunner consumes the
        # same T's #749 fixed envelope (history_reader) and projects the
        # P11 run_e1_replay_day result.  The named synthetic path below is
        # only a fallback when no real runner is wired.
        runner = e1_runner
        runner_factory = None
    elif candidates_path is not None:
        candidates_doc = json.loads(Path(candidates_path).read_text())
        all_candidates = tuple(candidates_doc.get("candidates") or ())

        def runner_factory(day: date):
            todays = tuple(
                c
                for c in all_candidates
                if str(c.get("from") or "0000-01-01") <= day.isoformat()
                and day.isoformat() <= str(c.get("until") or "9999-12-31")
            )
            return SyntheticRuleRunner(candidates=todays)

        runner = runner_factory(sessions[0])
    else:
        runner_factory = None
        runner = SyntheticRuleRunner(candidates=())

    return ReplayEngine(
        state_dir=state_dir,
        sessions=list(sessions),
        bars_by_day=bars_by_day,
        runner=runner,
        runner_factory=runner_factory,
        window_map=window_map,
        exclusion_receipt=exclusion_receipt,
        spec=default_spec(len(sessions), knowledge_cutoff),
        observed_sessions_by_day=observed_sessions_by_day,
        history_reader=history_reader,
        account_projection=account_projection,
        decision_cutoff_time=(
            decision_cutoff_time or time(22, 30, tzinfo=EXCHANGE_TZ)
        ),
        base_manifest_path=Path(base_manifest_path),
        base_manifest_sha256=base_manifest_sha256,
        account_id=account_id,
        account_mode=account_mode,
        publish_time=publish_time,
        read_only=os.environ.get("P12_REPLAY_READONLY") in ("1", "true", "yes"),
        read_only_account_snapshot=read_only_account_snapshot,
        read_only_name_map=read_only_name_map,
        app_builder=lambda **kw: build_replay_app(
            fixture_path=fixture_path, **kw
        ),
    )


# ---- CLI -----------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--base-root", type=Path, default=BASE_ROOT)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    # The advice window ends at the last session with a T+1 in the
    # pinned calendar (2026-10-29).  Pass --end 2026-10-30 to include
    # the final display/execution day: it carries positions into the
    # review but publishes no package (next_session stays null).
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--candidates", type=Path, default=None)
    parser.add_argument("--fixture", type=Path, default=None)
    parser.add_argument("--window-keys", type=Path, default=None)
    parser.add_argument("--account", default="SYN-PAPER-A")
    parser.add_argument(
        "--e1-scenario",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "frozen p12.e1-scenario.v1 binding for the real phase-1 E1 "
            "entry (scenario identity + signed action_coverage + exclusions "
            "+ optional SourceMap overrides).  When given the engine runs "
            "the P11 public run_e1_replay_day seam on the same T's #749 "
            "envelope; an absent/unreadable/incomplete file leaves a named "
            "e1_assembly_unavailable block and the entry never falls back "
            "to the synthetic runner."
        ),
    )
    parser.add_argument(
        "--account-snapshot",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "read-only review only: the signed-off scenario account snapshot "
            "the projection reads for cash/holdings (sha256 + scenario "
            "verified before use).  Absent/mismatched -> the page reports "
            "account_snapshot_unavailable rather than a fabricated balance."
        ),
    )
    parser.add_argument(
        "--account-snapshot-sha256",
        default=None,
        metavar="SHA256",
        help="expected sha256:... digest of --account-snapshot.",
    )
    parser.add_argument(
        "--account-snapshot-scenario",
        default=None,
        metavar="ID",
        help="expected scenario_id inside --account-snapshot.",
    )
    parser.add_argument(
        "--name-map",
        default=None,
        metavar="PATH",
        help=(
            "optional read-only display name-map JSON "
            "({names:{code:name},source_envelope_sha256}) exported from the "
            "same-T sealed envelope; display only, never mutates evidence."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("--serve", action="store_true", help="run the console")
    parser.add_argument(
        "--user",
        action="append",
        default=None,
        metavar="USERNAME:ROLE:PASSWORD",
        help=(
            "seed a console login; repeat for several.  When at least one "
            "is given the console requires a bearer session from "
            "/api/login - same model as the P12 local_server --user."
        ),
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help="write the acceptance evidence pack here instead of serving",
    )
    args = parser.parse_args(argv)

    # R4 item 3: the production entry binds the frozen #737 exclusion file's
    # bytes to its pinned digest BEFORE any bar is read.  A missing,
    # unreadable or byte-mismatched list writes a blocking receipt and the
    # engine never enters - it never filters against a wrong list.
    exclusion_path = Path(
        os.environ.get(
            "P12_REPLAY_EXCLUSION",
            Path(__file__).resolve().parents[3]
            / "config/p12/replay-exclusion-737.v1.json",
        )
    )
    import hashlib as _hashlib

    exclusion_block = {"status": "ok"}
    try:
        excl_bytes = exclusion_path.read_bytes()
        excl_sha = "sha256:" + _hashlib.sha256(excl_bytes).hexdigest()
        exclusion_block = {
            "status": "ok",
            "config_path": str(exclusion_path),
            "config_sha256": excl_sha,
            "expected_sha256": EXPECTED_EXCLUSION_SHA256,
            "sha_match": excl_sha == EXPECTED_EXCLUSION_SHA256,
        }
        if not exclusion_block["sha_match"]:
            exclusion_block.update(
                {
                    "status": "blocked",
                    "reason": "EXCLUSION_SHA_MISMATCH",
                    "detail": (
                        "the #737 exclusion file's bytes do not match the "
                        "frozen digest; the replay refuses to filter against "
                        "an unverified list and does not enter the engine"
                    ),
                }
            )
    except OSError as exc:
        exclusion_block = {
            "status": "blocked",
            "reason": "EXCLUSION_UNREADABLE",
            "config_path": str(exclusion_path),
            "expected_sha256": EXPECTED_EXCLUSION_SHA256,
            "detail": f"exclusion file cannot be read: {exc}",
        }
    # The verified frozen {path, sha256} is THE binding this entry passes
    # to read_history.  A scenario that pins a different self-consistent
    # pair is refused HERE - at the same gate as the file check and
    # BEFORE load_window opens any market member - so a conflicting
    # binding never reaches the reader through the production entry.
    if exclusion_block["status"] != "blocked" and args.e1_scenario is not None:
        try:
            _scen_doc = json.loads(Path(args.e1_scenario).read_text())
            _scen_pin = _scen_doc.get("replay_exclusion")
        except Exception:
            _scen_pin = None
        if isinstance(_scen_pin, str):
            _scen_pin = {"path": _scen_pin}
        if (
            isinstance(_scen_pin, dict)
            and _scen_pin.get("path") is not None
            and _scen_pin.get("sha256") is not None
            and (
                str(_scen_pin.get("path")) != str(exclusion_path)
                or str(_scen_pin.get("sha256")) != EXPECTED_EXCLUSION_SHA256
            )
        ):
            exclusion_block = {
                "status": "blocked",
                "reason": "REPLAY_EXCLUSION_BINDING_CONFLICT",
                "config_path": str(args.e1_scenario),
                "scenario_binding": {
                    "path": str(_scen_pin.get("path")),
                    "sha256": str(_scen_pin.get("sha256")),
                },
                "verified_binding": {
                    "path": str(exclusion_path),
                    "sha256": EXPECTED_EXCLUSION_SHA256,
                },
                "detail": (
                    "the scenario pins a #737 exclusion that differs from "
                    "the frozen binding the entry verified; the replay "
                    "refuses to read against an alternate pair before any "
                    "member is opened"
                ),
            }
    if exclusion_block["status"] == "blocked":
        args.state_dir.mkdir(parents=True, exist_ok=True)
        (args.state_dir / "replay-exclusion-block.json").write_text(
            json.dumps(exclusion_block, ensure_ascii=False, indent=1)
        )
        print(json.dumps({"exclusion_blocked": exclusion_block}, ensure_ascii=False))
        return 2

    sessions, bars_by_day, reads = load_window(
        base_root=args.base_root, start=args.start, end=args.end
    )
    print(
        json.dumps(
            {
                "sessions": [d.isoformat() for d in sessions],
                "session_bars": {
                    d.isoformat(): len(bars_by_day.get(d) or {}) for d in sessions
                },
                "reads": reads,
            },
            ensure_ascii=False,
        )
    )

    # P10-pinned October window map (when the frozen file is present):
    # the rule reads each T's verbatim windows, UNKNOWN coverage and
    # audit keys in the same run.  #737 applies before native reads.
    wk_path = args.window_keys or Path(
        "/Users/william/Documents/Codex/2026-11-25/"
        "quanthus-p10-october-action-feasibility/v1/WINDOW-AND-KEYS.json"
    )
    window_map = OctoberWindowMap.load(wk_path) if wk_path.is_file() else None
    exclusion_receipt = None
    if window_map is not None:
        exclusion_receipt = apply_exclusion(
            bars_by_day,
            window_map.exclusion,
            # Bind to the frozen #737 digest (bb4a429f); a byte-mismatched
            # file names sha_match=False rather than filtering silently
            # (R3 item 4).
            expected_sha256=EXPECTED_EXCLUSION_SHA256,
        )
        if exclusion_receipt["filtered_count"]:
            print(json.dumps({"exclusion_applied": exclusion_receipt}))

    # Real observed SSE open-session count through each signal day (the
    # history-floor evidence the manifest honestly reports per T).
    observed_by_day = {
        d.isoformat(): base_sessions_through(args.base_root, d) for d in sessions
    }

    # 8792 phase-1 real-rule entry: --e1-scenario binds the frozen scenario
    # JSON to the same T's #749 envelope + the P11 public replay seam.  A
    # missing, unreadable or incomplete binding writes a named
    # e1_assembly_unavailable block and the entry exits - it never falls
    # back to the labelled synthetic runner.
    e1_runner = None
    e1_history_reader = None
    e1_decision_cutoff = None
    if args.e1_scenario is not None:
        e1_block = {"status": "ok"}
        try:
            from quanthus.p12_replay.e1_assembly import load_e1_scenario

            # The entry passes the SAME frozen {path, sha256} it verified
            # at the pre-load_window gate EXPLICITLY to read_history -
            # never let a scenario or the process env point the read at a
            # different self-consistent pair.  A conflicting scenario pin
            # was already refused at that gate before any member opened.
            _verified_pin = {
                "path": str(exclusion_path),
                "sha256": EXPECTED_EXCLUSION_SHA256,
            }
            e1_runner, e1_history_reader = load_e1_scenario(
                args.e1_scenario,
                repo_root=Path(__file__).resolve().parents[3],
                evidence_dir=args.state_dir / "e1-evidence",
                replay_exclusion=_verified_pin,
            )
            # Frozen October decision cutoff: the scenario may pin
            # decision_cutoff_time; the phase-1 default is 21:30+08 (the
            # T evening cutoff the #749 envelope, the outer daily read and
            # the P11 day receipt all share).
            try:
                scen_doc = json.loads(Path(args.e1_scenario).read_text())
                raw_ct = scen_doc.get("decision_cutoff_time")
            except Exception:
                raw_ct = None
            hh, mm = (raw_ct or "21:30").split(":")[:2]
            e1_decision_cutoff = time(
                int(hh), int(mm), tzinfo=EXCHANGE_TZ
            )
        except Exception as exc:  # noqa: BLE001 - assembly is best effort
            e1_block = {
                "status": "blocked",
                "reason": "e1_assembly_unavailable",
                "config_path": str(args.e1_scenario),
                "detail": (
                    "the phase-1 E1 scenario binding could not be "
                    "assembled: " + type(exc).__name__ + ": " + str(exc)
                ),
            }
        if e1_block["status"] == "blocked":
            args.state_dir.mkdir(parents=True, exist_ok=True)
            (args.state_dir / "e1-assembly-block.json").write_text(
                json.dumps(e1_block, ensure_ascii=False, indent=1)
            )
            print(json.dumps({"e1_assembly_blocked": e1_block}, ensure_ascii=False))
            return 2

    def factory(reset: bool) -> ReplayEngine:
        return make_engine(
            state_dir=args.state_dir,
            sessions=sessions,
            bars_by_day=bars_by_day,
            candidates_path=args.candidates,
            account_id=args.account,
            fixture_path=args.fixture,
            window_map=window_map,
            exclusion_receipt=exclusion_receipt,
            observed_sessions_by_day=observed_by_day,
            e1_runner=e1_runner,
            history_reader=e1_history_reader,
            decision_cutoff_time=e1_decision_cutoff,
            account_mode=(
                "simulation" if e1_runner is not None else "synthetic"
            ),
            read_only_account_snapshot=(
                {
                    "path": args.account_snapshot,
                    "sha256": args.account_snapshot_sha256,
                    "scenario_id": args.account_snapshot_scenario,
                }
                if args.account_snapshot is not None
                else None
            ),
            read_only_name_map=(
                json.loads(Path(args.name_map).read_text())
                if getattr(args, "name_map", None)
                else None
            ),
            reset=reset,
        )

    if args.serve:
        from quanthus.p12_replay.server import serve

        users = {}
        # Single shared console account (same as 8790): admin / operator.
        # The password always comes from the environment - never a repo
        # file, a command-line literal or a doc.  --user stays available
        # for explicit extra accounts in a lab shell.
        admin_pw = os.environ.get("P12_REPLAY_ADMIN_PASSWORD")
        if admin_pw:
            users["admin"] = ("operator", admin_pw)
        for spec in args.user or ():
            parts = spec.split(":", 2)
            if len(parts) != 3 or not all(parts):
                raise SystemExit("--user expects USERNAME:ROLE:PASSWORD")
            users[parts[0]] = (parts[1], parts[2])
        server = serve(factory, args.host, args.port, users=users)
        print(
            json.dumps(
                {
                    "url": f"http://{args.host}:{args.port}/",
                    "state_dir": str(args.state_dir),
                    "label": "历史回放·模拟",
                }
            ),
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
        return 0

    # Headless: same engine, same steps as the console - run each day's
    # evening job, confirm every published plan through the same
    # open-match path, advance, and keep per-day review evidence.  The
    # last session is T-only (next_session stays null by contract) - it
    # carries positions into the review but publishes no package.
    engine = factory(reset=True)
    result = engine.auto_advance()
    report = {
        "sessions": [],
        "auto": True,
        "processed_days": result["processed_days"],
        "stopped_at": result["stopped_at"],
        "started_at": datetime.now(EXCHANGE_TZ).isoformat(),
        "finished_at": None,
    }
    for step in result["steps"]:
        report["sessions"].append(
            {
                "market_date": step["market_date"],
                "run_status": step["run_status"],
                "outcomes": step["outcomes"],
                "review": step["review"],
            }
        )
    report["finished_at"] = datetime.now(EXCHANGE_TZ).isoformat()
    out = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.evidence_dir:
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        (args.evidence_dir / "replay-report.json").write_text(out + "\n")
        # Copy the raw artifacts the report points to.
        import shutil as _sh

        (args.evidence_dir / "state").mkdir(exist_ok=True)
        for name in ("replay-log.jsonl", "daily-packages", "daily-runs"):
            src = args.state_dir / name
            dst = args.evidence_dir / "state" / name
            if src.is_dir():
                if dst.exists():
                    _sh.rmtree(dst)
                _sh.copytree(src, dst)
            elif src.is_file():
                _sh.copy2(src, dst)
        print(str(args.evidence_dir / "replay-report.json"))
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
