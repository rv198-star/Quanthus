"""E1 production entry: P11-owned runtime assets + per-T advice run.

P12 supplies already-read raw rows, the scenario account and member clocks;
P11 owns the per-T DailyFactsView / AdvicePolicy / split+source policy /
SeamResources and the actual same-job panel computation.  The production
path NEVER takes a test fixture, a caller label or a synthetic card for a
real account projection -- the fixed registry, fixed candidate card and the
real same-job BaselineFeaturePanelReport are the only inputs.

Public entry points:

  load_e1_rule_assets(root)
      -> E1RuleAssets pinning the frozen strategy registry, candidate rule
      card, feature registry, trend config + trend spec, with optional
      verified code/digest pins.  A changed version, identity or digest
      refuses the asset at construction, never silently re-accepts it.

  run_e1_from_history(raw_history, *, assets, facts, policy, account, ...)
      -> (RuleDayOutput, BaselineFeaturePanelReport).  The panel is built
      ONCE inside the same job from the qualified raw rows; the
      features_by_security it produces feed both the entry evidence gate and
      the per-session scan, so the report's counts and content digest are
      the job's actual computation -- never an input hash relabelled as a
      panel row digest.

E5 remains proposed: this module never signs adoption.  The replay channel
(run_e1_replay_day) wraps the already-signed replay path so P12 keeps the
sealed coverage gate, #737 exclusions, replay isolation and the
final-display day -- no raw-H1 shortcut that skips them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

from quanthus.backtest.baseline_research import BaselineFeaturePanelReport
from quanthus.event_library.contracts import sha256_digest
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.expert_evaluation.experts.trend_stability import (
    load_trend_stability_config,
)
from quanthus.expert_evaluation.serialization import artifact_from_bytes
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.expert_strategy.runner import FormalStrategyRunner
from quanthus.feature_registry import load_feature_registry
from quanthus.forward_validation.base24_runtime import (
    AfterCloseDecisionClock,
    P6Base24CandidatePolicy,
)
from quanthus.forward_validation.prospective_source import (
    P6ProspectiveSplitPolicy,
)
from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.contracts import DailyFactsView
from quanthus.product_daily.recommendation.history_input import (
    run_daily_advice_from_history,
)
from quanthus.product_daily.recommendation.producer import (
    AccountFactsView,
    AdvicePolicy,
)
from quanthus.product_daily.recommendation.rule_seam import (
    RuleDayOutput,
    SeamResources,
)

# --- fixed production asset paths (never caller-supplied) --------------------

_CANDIDATE_CARD = "config/p4-candidate-price-volume-transition.research.v1.json"
_FEATURE_REGISTRY = "config/p3-baseline-features.research.v1.json"
_TREND_CONFIG = "config/expert-evaluations/experts/trend-stability.v1.json"
# The pinned consumer rule for the October calendar consume: a trusted
# projection of the P10 #747 RESULT (its own identity + per-venue/date proven
# basis).  Loaded from the release-verified repository root, never caller bytes.
_OCTOBER_CALENDAR_RULE = "config/research/r11/d1b-20261124/october-calendar-consume-rule.v1.json"
_TREND_SPEC = "config/expert-evaluations/specs/trend-stability-p4-first-passage.evaluation.v1.json"

# The E1 production panel evaluator identity, bound to the frozen registry.
_E1_EVALUATOR_VERSION = "p11-e1-runtime-v1"

# The registered release manifest whose code_entry/named assets pin this E1
# construction.  Loaded through the verified release loader; its code_commit/
# code_tree are the executable-source identity, never a caller pin.
_E1_RELEASE_MANIFEST_ID = "p11-d1b-fixed-rule-20261124-v2"


def _fail(code: str, message: str) -> None:
    raise ProductDailyError(code, message)


@dataclass(frozen=True)
class E1RuleAssets:
    """The frozen P11 rule assets an E1 run binds; never a synthesized card.

    runner carries the ExpertStrategyRegistry card + topology digest; card is
    the candidate transition card; feature_registry/trend_config/trend_spec
    drive the panel and the trend expert.  code_commit/code_tree and the
    content digests pin the exact construction so a version, identity or
    digest change refuses the asset.
    """

    runner: FormalStrategyRunner
    card: Any
    feature_registry: Any
    trend_config: Any
    trend_spec: Any
    code_commit: str
    code_tree: str
    card_digest: str
    registry_digest: str
    topology_digest: str
    # The release-verified repository root: trusted consumer rules (e.g. the
    # October calendar-consume rule) are read from it so a caller can never
    # inject an unproven schedule basis.
    repository: Path | None = None


def load_e1_rule_assets(
    root: str | Path,
    *,
    expected_code_commit: str | None = None,
    expected_code_tree: str | None = None,
    expected_card_digest: str | None = None,
    expected_registry_digest: str | None = None,
) -> E1RuleAssets:
    """Load and pin the frozen E1 rule assets from the repository root.

    The bound identities come from the FIXED release manifest's real
    code_entry (commit/tree) and the named asset files' bytes -- never from
    the caller's pins.  A caller-supplied expected_* is only an assertion:
    when it differs from the manifest/file truth the asset is refused, never
    echoed back as if verified.  A wrong commit/tree (even a valid 40-hex
    string) and any changed asset byte all fail closed.
    """
    repository = Path(root).resolve()
    if not repository.is_dir():
        _fail("e1_root_missing", f"{repository} is not a repository root")

    # The release manifest is the single source of truth for the bound code
    # identity and the named asset digests.  Verified through the registered
    # release loader so a drifted semantic closure or a wrong commit/tree is
    # caught before any asset is constructed.
    code_commit, code_tree = _verify_release_code_identity(repository)

    registry = ExpertStrategyRegistry(repository)
    runner = FormalStrategyRunner(registry.card, registry.topology_manifest.digest)
    card = load_candidate_rule_card(repository / _CANDIDATE_CARD)
    feature_registry = load_feature_registry(repository / _FEATURE_REGISTRY)
    trend_config = load_trend_stability_config(repository / _TREND_CONFIG)
    trend_spec = artifact_from_bytes((repository / _TREND_SPEC).read_bytes())

    card_digest = getattr(card, "semantic_digest", None) or sha256_digest(
        getattr(card, "to_dict", lambda: repr(card))()
    )
    registry_digest = feature_registry.digest

    # Caller pins are assertions only: a supplied value that disagrees with the
    # verified manifest/file identity refuses the asset, never passes through.
    for name, expected, actual in (
        ("code_commit", expected_code_commit, code_commit),
        ("code_tree", expected_code_tree, code_tree),
        ("card_digest", expected_card_digest, card_digest),
        ("registry_digest", expected_registry_digest, registry_digest),
    ):
        if expected is not None and actual is not None and expected != actual:
            _fail(
                "e1_asset_pin_mismatch",
                f"{name}={actual!r} differs from the verified pin {expected!r}",
            )

    return E1RuleAssets(
        runner=runner,
        card=card,
        feature_registry=feature_registry,
        trend_config=trend_config,
        trend_spec=trend_spec,
        code_commit=code_commit,
        code_tree=code_tree,
        card_digest=card_digest,
        registry_digest=registry_digest,
        topology_digest=registry.topology_manifest.digest,
        repository=repository,
    )


def _verify_release_code_identity(repository: Path) -> tuple[str, str]:
    """Resolve the E1 code commit/tree from the registered release manifest.

    Uses the verified release loader so the manifest's code_entry is checked
    against the real git closure (the executable-source commit, distinct from
    a manifest-only rebind HEAD).  Returns (code_commit, code_tree); a missing
    release or a closure drift fails closed rather than echoing caller pins.
    """
    from quanthus.product_daily.recommendation.release_contract import (
        load_verified_release,
    )

    try:
        release = load_verified_release(repository, _E1_RELEASE_MANIFEST_ID)
    except ProductDailyError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        _fail("e1_release_unverified", f"{exc}")
    code = release.manifest.get("code_entry") or {}
    commit = code.get("code_commit")
    tree = code.get("code_tree")
    if not commit or not tree:
        _fail("e1_release_unverified", "release manifest lacks code_entry identity")
    return commit, tree


def _split_policy_for(market_date: date) -> P6ProspectiveSplitPolicy:
    """The frozen prospective split for this E1 signal day.

    activation_market_date stays at the chain's 2026-01-01 floor so the
    replayed October days are all post-activation; a signal before the floor
    is refused by the policy itself, never re-anchored here.
    """
    return P6ProspectiveSplitPolicy(
        activation_market_date=date(2026, 1, 1), signal_market_date=market_date
    )


def run_e1_from_history(
    raw_history: Mapping[str, Any],
    *,
    assets: E1RuleAssets,
    facts: DailyFactsView,
    policy: AdvicePolicy,
    account: AccountFactsView,
    run_id: str,
    snapshot_id: str,
    snapshot_manifest_digest: str,
    market_date: date | None = None,
    split_policy: Any | None = None,
    source_policy: Any | None = None,
    clock: Callable[[], datetime] | None = None,
    input_exclusions: list[dict[str, Any]] | None = None,
    account_disposition: str = "ok",
    pending_security_codes: list[str] | None = None,
    pending_securities: list[Any] | None = None,
) -> tuple[RuleDayOutput, BaselineFeaturePanelReport]:
    """Run one E1 signal day through the real same-job panel + rule chain.

    raw_history is the P12-supplied p11.raw-history-input.v1 envelope for THIS
    T (already-read, PIT-trimmed rows).  The panel report is computed once
    inside the job from the features_by_security the run produced; counts and
    identities are re-checkable, never borrowed from a test fixture or a
    caller summary.
    """
    signal = market_date or facts.market_date
    if split_policy is None:
        split_policy = _split_policy_for(signal)
    if clock is None:
        # Default to the signal day's logical decision instant (the T-close
        # coordinate the chain binds); a None clock must not reach the seam.
        decision_at = policy.decision_cutoff
        clock = lambda: decision_at  # noqa: E731
    if source_policy is None:
        source_policy = _default_source_policy(facts=facts, policy=policy, market_date=signal)

    # The panel report is built ONCE inside the job from the features the chain
    # computes; the factory is invoked with the actual features_by_security so
    # the report is the same-job computation, not a recomputed market pass.
    report_holder: dict[str, BaselineFeaturePanelReport] = {}

    def _factory(features_by_security: Mapping[str, Any]) -> BaselineFeaturePanelReport:
        report = _panel_report_for(
            assets=assets,
            features_by_security=features_by_security,
            signal_date=facts.market_date,
            snapshot_id=snapshot_id,
            snapshot_manifest_digest=snapshot_manifest_digest,
        )
        report_holder["report"] = report
        return report

    # A placeholder report only satisfies SeamResources' __post_init__ identity
    # check; the factory above rebinds the real one before the receipt is cut.
    resources = SeamResources(
        feature_registry=assets.feature_registry,
        panel_report=_pending_panel_placeholder(assets),
        trend_config=assets.trend_config,
        trend_spec=assets.trend_spec,
    )

    output = run_daily_advice_from_history(
        raw_history,
        runner=assets.runner,
        resources=resources,
        facts=facts,
        policy=policy,
        account=account,
        run_id=run_id,
        snapshot_id=snapshot_id,
        snapshot_manifest_digest=snapshot_manifest_digest,
        card=assets.card,
        split_policy=split_policy,
        source_policy=source_policy,
        clock=clock,
        input_exclusions=input_exclusions,
        account_disposition=account_disposition,
        pending_security_codes=pending_security_codes,
        pending_securities=pending_securities,
        panel_report_factory=_factory,
    )
    return output, report_holder["report"]


def _default_source_policy(
    *, facts: DailyFactsView, policy: AdvicePolicy, market_date: date
) -> P6Base24CandidatePolicy:
    """Build the use-correct source policy for this E1 day.

    The decision coordinate is the policy's own decision_cutoff, the
    observation end is the T close and the next open comes from
    facts.next_session -- real bound inputs, never a fixture.  history_start
    keeps the chain's floor.
    """
    tz = policy.decision_cutoff.tzinfo
    next_open = (
        datetime.combine(facts.next_session, time(9, 30), tzinfo=tz)
        if getattr(facts, "next_session", None) is not None
        else None
    )
    clock = AfterCloseDecisionClock(
        market_date=market_date,
        source_available_at=policy.decision_cutoff,
        decision_at=policy.decision_cutoff,
        next_session_open=next_open,
    )
    return P6Base24CandidatePolicy(
        clock=clock,
        history_start=date(2026, 1, 1),
        inputs_manifest_sha256=getattr(facts, "manifest_sha256", None) or "sha256:" + "0" * 64,
    )


def _panel_report_for(
    *,
    assets: E1RuleAssets,
    features_by_security: Mapping[str, Any],
    signal_date: date,
    snapshot_id: str,
    snapshot_manifest_digest: str,
) -> BaselineFeaturePanelReport:
    """Build the real panel report from THIS job's computed features.

    Counts come from the actual SecurityFeatureFacts rows; data_content_digest
    binds the per-security feature bytes, not a caller label.  The report is
    re-checkable against the job's inputs.
    """
    ready = sum(1 for f in features_by_security.values() if getattr(f, "feature_ready", False))
    eligible = sum(1 for f in features_by_security.values() if getattr(f, "entry_eligible", False))
    exceptions = sum(
        1 for f in features_by_security.values() if getattr(f, "exception_active", False)
    )
    # Actual per-row verdicts from THIS computation, never a hard-filled 0 or
    # a fixed scope floor: unexplained transitions and continuity exclusions
    # are counted from the computed rows, and the scope is the real signal-to-
    # earliest-input window the panel covered.
    unexplained = sum(
        1
        for f in features_by_security.values()
        if getattr(f, "unexplained_price_transition", False)
    )
    continuity_excluded = sum(
        1 for f in features_by_security.values() if getattr(f, "scope_continuity_excluded", False)
    )
    scope_start = min(
        (f.signal_date for f in features_by_security.values() if getattr(f, "signal_date", None)),
        default=signal_date,
    )
    return BaselineFeaturePanelReport(
        evaluator_version=_E1_EVALUATOR_VERSION,
        feature_registry_version=assets.feature_registry.registry_version,
        feature_registry_digest=assets.feature_registry.digest,
        bundle_snapshot_id=snapshot_id,
        bundle_digest=snapshot_manifest_digest,
        scope_start=scope_start,
        scope_end=signal_date,
        row_count=len(features_by_security),
        feature_ready_count=ready,
        entry_eligible_count=eligible,
        exception_active_count=exceptions,
        unexplained_transition_count=unexplained,
        continuity_excluded_security_count=continuity_excluded,
        data_content_digest=sha256_digest(
            {code: _feature_row_dict(f) for code, f in sorted(features_by_security.items())}
        ),
    )


def _feature_row_dict(facts: Any) -> dict[str, Any]:
    """Project a SecurityFeatureFacts to a JSON-safe panel-row mapping.

    Decimal/date values are normalized so the content digest is stable and
    re-checkable; the row is the actual computed facts, not a summary.
    """
    out = asdict(facts)
    for key, value in list(out.items()):
        if isinstance(value, Decimal):
            out[key] = str(value)
        elif isinstance(value, date):
            out[key] = value.isoformat()
        elif isinstance(value, tuple):
            out[key] = list(value)
    return out


def _package_instant(value: Any) -> datetime:
    try:
        instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        _fail("replay_package_clock_mismatch", "missing or invalid receipt timestamp")
    if instant.utcoffset() is None:
        _fail("replay_package_clock_mismatch", "receipt timestamp has no UTC offset")
    return instant


_P1_LEDGER_ID = "ODS/canonical/ref_adjustment_factor_pit_exceptions.parquet"
_P1_PACKAGE = "base-ods"
_P1_MEMBER_KEY = f"{_P1_PACKAGE}::{_P1_LEDGER_ID}"


def _p1_read_error(
    request: Any,
    read: Mapping[str, Any],
    consumption: Mapping[str, Any],
    source_read: datetime,
    observed: datetime,
) -> str | None:
    """Join the reported READ to this request's source declaration and both intake receipts."""

    def matches(row: Mapping[str, Any]) -> bool:
        return (
            row.get("member_id") == _P1_LEDGER_ID
            and row.get("member_key", _P1_MEMBER_KEY) == _P1_MEMBER_KEY
            and row.get("package_key", _P1_MEMBER_KEY) == _P1_MEMBER_KEY
            and row.get("package_id", _P1_PACKAGE) == _P1_PACKAGE
        )

    if not matches(read) or read.get("member_key") != _P1_MEMBER_KEY:
        return "p1_member_identity_mismatch"
    sources = [r for r in request.source_members if r.get("member_id") == _P1_LEDGER_ID]
    rows = consumption.get("members") if isinstance(consumption, Mapping) else None
    if not isinstance(rows, (list, tuple)):
        return "p1_read_without_unique_member"
    ordinary = [
        r
        for r in rows
        if isinstance(r, Mapping) and r.get("member_id") == _P1_LEDGER_ID
    ]
    intake = [m for m in request.member_evidence if m.member_id == _P1_LEDGER_ID]
    if len(sources) != 1 or len(ordinary) != 1 or len(intake) != 1:
        return "p1_read_without_unique_member"
    source, row, member = sources[0], ordinary[0], intake[0]
    if (
        not matches(source)
        or source.get("package_id") != _P1_PACKAGE
        or not matches(row)
        or row.get("status", "READ") != "READ"
    ):
        return "p1_member_identity_mismatch"
    digest = source.get("sha256")
    if (
        read.get("sha_match") is not True
        or row.get("sha_match") is not True
        or not (digest == read.get("sha256") == row.get("sha256") == member.digest)
        or any(
            r[key] != digest
            for r in (row, read)
            for key in ("declared_sha256", "actual_sha256")
            if key in r
        )
    ):
        return "p1_member_digest_mismatch"
    try:
        verified = _package_instant(read.get("verified_at"))
        matching_clock = (
            _package_instant(consumption.get("run_read_at")) == source_read
            and source_read <= verified <= observed
            and verified == _package_instant(row.get("verify_at")) == member.available_at
        )
    except ProductDailyError:
        matching_clock = False
    return None if matching_clock else "p1_member_verify_clock_mismatch"


def _p1_read_evidence(
    request: Any,
    evidence: Mapping[str, Any],
    source_read: datetime,
    observed: datetime,
) -> dict[str, Any]:
    """Preserve failed attempts. Only corroborated successful reads can certify the ledger."""
    required = _P1_LEDGER_ID in request.required_member_ids
    if "p1_exceptions" not in evidence:
        return dict(evidence)
    section = evidence.get("p1_exceptions")
    reads = section.get("reads") if isinstance(section, Mapping) else None
    error = None
    successful = []
    if not isinstance(reads, list) or not all(isinstance(r, Mapping) for r in reads):
        error = "p1_read_evidence_missing"
    else:
        successful = [r for r in reads if r.get("status") == "READ"]
        if len(successful) > 1 or (successful and section.get("p1_unverified")):
            error = "p1_read_evidence_conflict"
        elif successful:
            error = _p1_read_error(
                request,
                successful[0],
                evidence.get("member_consumption") or {},
                source_read,
                observed,
            )
        elif required:
            error = "p1_required_read_unverified"
    if error is None:
        return dict(evidence)
    if required:
        _fail("replay_p1_read_mismatch", f"{_P1_MEMBER_KEY}: {error}")
    # An optional claim may be unavailable to this run. Keep the reported
    # bytes/clocks for audit, outside the list of accepted READs. Native prices
    # and signed holder terms have their own independent proof.
    attempts = (
        [r for r in reads if isinstance(r, Mapping) and r.get("status") != "READ"]
        if isinstance(reads, list)
        else []
    )
    return {
        **evidence,
        "p1_exceptions": {
            "reads": attempts,
            "p1_unverified": True,
            "reason": error,
            "reported_evidence": section,
        },
    }


def _october_package_clock(
    request: Any,
    evidence: Mapping[str, Any],
    source_read: datetime,
    observed: datetime,
    cutoff: datetime,
    envelope_digest: str,
    calendar: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify the producer's intake clocks against the reader's same-day receipt."""
    consumption = evidence.get("member_consumption") or {}
    envelope = evidence.get("envelope") or {}
    stated_cutoff = (evidence.get("source_clocks") or {}).get("logical_decision_cutoff")
    stated_hash = str(envelope.get("sha256", "")).removeprefix("sha256:")
    if (
        stated_hash != envelope_digest.removeprefix("sha256:")
        or _package_instant(stated_cutoff) != cutoff
        or _package_instant(consumption.get("run_read_at")) != source_read
        or calendar.get("consumer_rule_version") is None
    ):
        _fail("replay_package_clock_mismatch", "envelope, bound day or calendar rule differs")
    rows = consumption.get("members") or ()
    if not all(isinstance(row, Mapping) for row in rows):
        _fail("replay_package_clock_mismatch", "invalid member receipt")
    by_id = {r.get("member_id"): r for r in rows}
    if len(by_id) != len(rows):
        _fail("replay_package_clock_mismatch", "duplicate member receipt")
    used = {r.get("member_id") for r in rows if r.get("relevant_for_T") is True}
    if not used or used != set(request.required_member_ids):
        _fail("replay_package_clock_mismatch", "required members differ from the used receipt rows")
    verified = []
    for member in request.member_evidence:
        row = by_id.get(member.member_id)
        if row is None or row.get("sha_match") is not True or member.digest != row.get("sha256"):
            _fail("replay_package_clock_mismatch", f"unverified intake member {member.member_id}")
        when = _package_instant(row.get("verify_at"))
        if not source_read <= when <= observed or member.available_at != when:
            _fail("replay_package_clock_mismatch", f"verify_at differs: {member.member_id}")
        verified.append(when)
    facts = request.facts_template
    received = _package_instant(facts.get("intake_received_at"))
    first = _package_instant(facts.get("intake_first_read_at"))
    last = _package_instant(facts.get("intake_verified_at"))
    if not verified or not received <= first == source_read <= last or last != max(verified):
        _fail("replay_package_clock_mismatch", "intake differs from the read/hash receipts")
    return {
        "schema_version": "p11.october-replay-package-clock.v1",
        "market_date": cutoff.date().isoformat(),
        "logical_cutoff": cutoff.isoformat(),
        "source_read_at": source_read.isoformat(),
        "intake_verified_at": last.isoformat(),
        "evaluation_observed_at": observed.isoformat(),
        "envelope_sha256": envelope_digest,
        "member_consumption_digest": sha256_digest(consumption),
        "calendar_rule_version": calendar["consumer_rule_version"],
        "calendar_evidence_digest": sha256_digest(calendar),
    }


def _envelope_read_at(
    evidence: Mapping[str, Any] | None,
    observed: datetime,
    original_intake: datetime,
    cutoff: datetime,
) -> datetime:
    """Use the original input read when replaying a previously sealed envelope."""
    evidence = evidence or {}
    members = evidence.get("member_consumption")
    stamps = []
    for record, key in ((evidence, "read_at"), (members, "run_read_at")):
        if not isinstance(record, Mapping) or key not in record:
            continue
        try:
            value = datetime.fromisoformat(str(record[key]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProductDailyError("invalid_source_read_at", f"{key}: invalid timestamp") from exc
        if value.utcoffset() is None or value > observed:
            _fail("invalid_source_read_at", f"{key}: missing timezone or after replay observation")
        stamps.append(value)
    if len(set(stamps)) > 1:
        _fail("conflicting_source_read_at", "envelope and member receipt disagree on read time")
    if stamps:
        return stamps[0]
    if original_intake > min(cutoff, observed):
        _fail(
            "missing_original_read_at", "late archive intake requires its original reader receipt"
        )
    return original_intake


def run_e1_replay_day(
    request: Any,
    market_date: date,
    *,
    assets: E1RuleAssets,
    snapshot_id: str,
    snapshot_manifest_digest: str,
    clock: Callable[[], datetime] | None = None,
    observed_clock: Callable[[], datetime] | None = None,
    p12_evidence: Mapping[str, Any] | None = None,
    calendar_schedule_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """P12-facing replay entry: run one T through the signed replay path.

    Wraps paper_replay.run_replay_day with the fixed E1 assets so P12 keeps the
    sealed coverage gate, #737 exclusions, replay isolation and the
    final-display day - never a raw-H1 shortcut that skips them.  ``request``
    is an already-loaded ReplayRequest (or the raw mapping, loaded here).
    Returns the day receipt dict.
    """
    from quanthus.product_daily.recommendation import paper_replay
    from quanthus.product_daily.recommendation.history_input import (
        run_daily_advice_from_history as _hist,
    )

    req = (
        request
        if isinstance(request, paper_replay.ReplayRequest)
        else paper_replay.load_replay_request(request)
    )

    # Bind THIS replay day's real panel computation into the history call: the
    # factory is invoked once with the job's actual features_by_security, and
    # the rebound report is what source_closure signs (never the placeholder).
    report_holder: dict[str, BaselineFeaturePanelReport] = {}

    def _factory(features_by_security: Mapping[str, Any]) -> BaselineFeaturePanelReport:
        report = _panel_report_for(
            assets=assets,
            features_by_security=features_by_security,
            signal_date=market_date,
            snapshot_id=snapshot_id,
            snapshot_manifest_digest=snapshot_manifest_digest,
        )
        report_holder["report"] = report
        return report

    # Versioned as-observed replay: post-cutoff sealed members/calendar/
    # listings are admitted as NAMED archives on observed_sink (original clocks
    # preserved verbatim, never relabelled October-known); strict stays the
    # default elsewhere.  Their business content is asserted on/before T before
    # the day runs -- a post-T business fact stays isolated, never back-dated.
    observed_sink: dict[str, Any] = {}
    # October consumption-evidence block, folded into the issued
    # source_closure BEFORE its digest.  The same mutable dict is passed to
    # the history call (which signs it) and filled below once the member-use
    # analysis and the shared read instant exist, so the closure binds the
    # ORIGINAL envelope identity, selected member/row versions, original
    # clocks, this run's actual open/verify/observe instants, exclusions and
    # the full member catalogue -- never a post-hoc receipt replacement.
    consumption_evidence: dict[str, Any] = {}
    hist_with_panel = partial(
        _hist,
        panel_report_factory=_factory,
        anchor_mode="equivalent_day",
        as_observed=True,
        observed_sink=observed_sink,
        consumption_evidence=consumption_evidence,
        # The verified October scenario admits date-context warm-up rows
        # (named open sessions whose native instants are absent keep their
        # original nulls and qualify by business date).  Forward/H1/M2
        # entries never receive this string, so they keep the strict
        # per-bar instant requirement.
        numeric_context="october_replay_as_observed",
    )

    # This run's real read instant, resolved ONCE and shared with the replay
    # receipt's replay_observed_at so the binding is recomputable from the
    # receipt and final source_closure together.  A member sealed later than
    # this instant was not yet obtained by the actual verification and is
    # refused by dependency (sealed_after_actual_read).
    observed_at = (observed_clock or (lambda: datetime.now(timezone.utc)))()
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    shared_observed_clock = lambda: observed_at  # noqa: E731

    # Used-member analysis, keyed on the member ids the run actually consumes
    # (identity depends_on_member_ids, calendar/listing/register rows,
    # member_evidence, required_member_ids).  A member's declared coverage edge
    # may legitimately span the whole month/year; only a member WHOSE USED
    # business rows run past T+1 carries post-window fact and is refused.
    # Wholly-late partitions (November daily market rows, no October rows the
    # run reads) are excluded from the period's input and named with a reason,
    # never a hard failure.  The container edge and the used-row business date
    # are disclosed separately per member.
    source_read_at = _envelope_read_at(
        p12_evidence, observed_at,
        _package_instant(req.facts_template.get("intake_first_read_at")),
        paper_replay.replay_decision_at(market_date),
    )
    member_use = _analyze_member_use(req, market_date, observed_at=source_read_at)
    # Only a SHARED whole-day dependency blocks the whole day; a member bound
    # to one security isolates that security (neighbours keep evaluating),
    # and an unused member never blocks regardless of read timing.
    if member_use["blocking_shared_members"]:
        _fail(
            "as_observed_post_T_content",
            "shared member content/availability fails the whole-day gate of "
            f"signal {market_date}: {member_use['blocking_shared_members']}",
        )

    # Real projection: unused wholly-late partitions are moved OUT of the
    # period's input; a security whose exclusive dependency is unavailable is
    # demoted to held-only (its entry-scan inputs are cleared so it is never
    # re-evaluated, but an already-held position still surfaces its held
    # reminder from independently-available facts).  Neighbours keep
    # evaluating.  Isolated securities are named in the closure with reason,
    # original instant and scope.
    excluded_ids = {str(m["member_id"]) for m in member_use["excluded_late_partitions"]}
    isolated_securities = set(member_use["isolated_by_security"])
    demoted = []
    for s in req.securities:
        code = (
            (s.get("identity") or {}).get("listing_code")
            if isinstance(s, Mapping)
            else getattr(getattr(s, "identity", None), "listing_code", None)
        )
        if code not in isolated_securities:
            demoted.append(s)
            continue
        # Demote to held-only: keep the held markers/account position facts so
        # the held reminder still computes; clear every entry-scan input so it
        # is isolated, not silently evaluated on a stale member.
        iso = dict(s) if isinstance(s, Mapping) else dict(vars(s))
        iso["bars"] = []
        iso["actions"] = []
        iso["limits"] = []
        iso["close_at_by_date"] = {}
        iso["knowledge_cutoff_by_date"] = {}
        iso["entry_isolated_reason"] = "member_unavailable_at_read"
        iso["entry_isolated_member_ids"] = sorted(member_use["isolated_by_security"][code])
        demoted.append(iso)
    projected_req = (
        replace(
            req,
            source_members=tuple(
                m for m in req.source_members if str(_member_id_of(m)) not in excluded_ids
            ),
            securities=tuple(demoted),
        )
        if excluded_ids or isolated_securities
        else req
    )

    # Bind the October consumption evidence into the to-be-signed closure:
    # original envelope identity + selected member/row versions + original
    # clocks + this run's actual read + exclusions + the member catalogue.
    consumption_evidence.update(
        {
            "schema_version": "p11.october-consumption-evidence.v1",
            "request_digest": getattr(req, "request_digest", None),
            "package_id": getattr(req, "package_id", None),
            "manifest_sha256": getattr(req, "manifest_sha256", None),
            "signal_date": market_date.isoformat(),
            "logical_decision_at": None,  # filled by the receipt's logical field
            "actual_read_at": source_read_at.isoformat(),
            "member_use": member_use,
            "projected_out_member_ids": sorted(excluded_ids),
            "isolated_securities": {
                code: {
                    "member_ids": sorted(mids),
                    "reason": "member_unavailable_at_read",
                    "scope": "per_security_held_only",
                }
                for code, mids in member_use["isolated_by_security"].items()
            },
            # The named consumer rule by which this replay reads the exchange
            # calendar plus the PROVEN schedule evidence for the venue sessions
            # it actually relies on (P10 #747).  Each claimed session must
            # match a bound calendar_row's venue/date/is_open; a bare caller
            # timestamp never writes announced_before_T -- only a proven
            # exchange publication/revision version can.  Unknown publication
            # instants stay UNKNOWN_EXACT_INSTANT verbatim.
            "calendar_schedule_evidence": _calendar_schedule_evidence(
                req,
                market_date,
                calendar_schedule_evidence,
                _load_calendar_consume_rule(assets),
            ),
            "anchor_mode": "equivalent_day",
            "pit_disguise": False,
        }
    )

    # Native archive reads can finish weeks after the replay date. Authorize
    # their physical clock only for a verified receipt for this exact day.
    logical_cutoff = paper_replay.replay_decision_at(market_date)
    if p12_evidence and _package_instant(req.facts_template["intake_verified_at"]) > logical_cutoff:
        consumption_evidence["package_clock"] = _october_package_clock(
            req, p12_evidence, source_read_at, observed_at, logical_cutoff,
            snapshot_manifest_digest, consumption_evidence["calendar_schedule_evidence"],
        )
    consumption_evidence["p12"] = _p1_read_evidence(
        req, p12_evidence or {}, source_read_at, observed_at
    )
    receipt = paper_replay.run_replay_day(
        projected_req,
        market_date,
        run_daily_advice_from_history=hist_with_panel,
        runner=assets.runner,
        resources=SeamResources(
            feature_registry=assets.feature_registry,
            panel_report=_replay_panel_placeholder(assets),
            trend_config=assets.trend_config,
            trend_spec=assets.trend_spec,
        ),
        card=assets.card,
        split_policy=_split_policy_for(market_date),
        snapshot_id=snapshot_id,
        snapshot_manifest_digest=snapshot_manifest_digest,
        # Evaluation is as of the verified physical replay observation. The
        # strategy and decision-set cutoff still come from the bound market date.
        clock=shared_observed_clock if "package_clock" in consumption_evidence else clock,
        observed_clock=shared_observed_clock,
        source_read_at=source_read_at,
    )
    # Surface the real same-job panel report on the receipt so the caller sees
    # the actual computation, not the binding placeholder.
    if "report" in report_holder:
        receipt["feature_panel_report"] = report_holder["report"].to_dict()
    # The three clocks stay distinct on the signed receipt: logical T, original
    # source clocks (named post-cutoff archives), and this run's actual read.
    receipt["as_observed"] = {
        "schema_version": "p11.october-replay-as-observed.v1",
        "logical_decision_at": receipt.get("logical_decision_at"),
        "actual_read_at": source_read_at.isoformat(),
        "member_use": member_use,
        "sealed_archives": {
            "members": observed_sink.get("sealed_members", []),
            "calendar": observed_sink.get("sealed_calendar", []),
            "listings": observed_sink.get("sealed_listings", []),
        },
        "anchor_mode": "equivalent_day",
        "pit_disguise": False,
        "note": (
            "Post-cutoff archives keep their original publish/acquire clocks; "
            "each used member must be obtainable by this run's actual read "
            "instant; a member sealed later is refused by dependency.  Wholly "
            "post-T partitions are projected out of the period input.  No "
            "clock is rewritten to fake October-known."
        ),
    }
    return receipt


def _replay_panel_placeholder(assets: E1RuleAssets) -> BaselineFeaturePanelReport:
    """A metadata panel report for the replay channel.

    The replay's real panel is computed per-day inside run_replay_day; this
    bound report only satisfies SeamResources' identity check (its digest is
    not consumed as a panel row).  It is marked as a placeholder, never
    passed off as the job's computed panel.
    """
    return BaselineFeaturePanelReport(
        evaluator_version=_E1_EVALUATOR_VERSION + "-replay-binding",
        feature_registry_version=assets.feature_registry.registry_version,
        feature_registry_digest=assets.feature_registry.digest,
        bundle_snapshot_id="replay-binding",
        bundle_digest=assets.topology_digest,
        scope_start=date(2026, 1, 1),
        scope_end=date(2026, 1, 1),
        row_count=0,
        feature_ready_count=0,
        entry_eligible_count=0,
        exception_active_count=0,
        unexplained_transition_count=0,
        continuity_excluded_security_count=0,
    )


def _pending_panel_placeholder(assets: E1RuleAssets) -> BaselineFeaturePanelReport:
    """A pre-run placeholder that only satisfies SeamResources' identity check.

    The real panel report is rebound by panel_report_factory after the job
    computes features_by_security; this placeholder is never the receipt's
    bound report.
    """
    return BaselineFeaturePanelReport(
        evaluator_version=_E1_EVALUATOR_VERSION + "-pending",
        feature_registry_version=assets.feature_registry.registry_version,
        feature_registry_digest=assets.feature_registry.digest,
        bundle_snapshot_id="pending",
        bundle_digest=assets.topology_digest,
        scope_start=date(2026, 1, 1),
        scope_end=date(2026, 1, 1),
        row_count=0,
        feature_ready_count=0,
        entry_eligible_count=0,
        exception_active_count=0,
        unexplained_transition_count=0,
        continuity_excluded_security_count=0,
    )


def _member_id_of(m: Any) -> Any:
    return m.get("member_id") if isinstance(m, Mapping) else getattr(m, "member_id", None)


def _text_of(row: Any, field: str) -> Any:
    return row.get(field) if isinstance(row, Mapping) else getattr(row, field, None)


def _load_calendar_consume_rule(assets: E1RuleAssets) -> Mapping[str, Any] | None:
    """Load the pinned October calendar-consume rule from the release-verified
    repository root (a trusted projection of the P10 #747 RESULT).

    The rule declares its own identity; it is never read from caller bytes.
    A missing repository root or a malformed rule fails closed (the replay
    keeps reading the calendar through the consumer rule but records no proven
    publication version).
    """
    import json

    root = getattr(assets, "repository", None)
    if root is None:
        return None
    path = Path(root) / _OCTOBER_CALENDAR_RULE
    if not path.is_file():
        return None
    rule = json.loads(path.read_text())
    if rule.get("schema_version") != "quanthus.p11.calendar-consume-rule.v1":
        _fail(
            "calendar_consume_rule_invalid",
            f"{_OCTOBER_CALENDAR_RULE} schema_version is not the pinned v1 rule",
        )
    return rule


def _calendar_schedule_evidence(
    request: Any,
    market_date: date,
    evidence: Mapping[str, Any] | None,
    rule: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Bind the PROVEN exchange-schedule evidence into the signed closure.

    The replay reads the venue trading calendar through a NAMED consumer rule
    (consumer_rule_id/consumer_rule_version).  A caller may supply the P10
    #747 publication evidence: per venue+session_date the schedule basis
    (EXPLICIT_EXCHANGE_SCHEDULE / RULE_DERIVED_SCHEDULE), the named source
    documents (notice id / rule clause + sha256 originals) and the
    publication instants.

    Hard rules (P10 #747 contract):
      * only PROVEN exchange calendar facts are consumed -- every claimed
        session must match a bound calendar_row on venue_id + session_date;
        a claim for a session the envelope does not carry is refused, never
        silently adopted;
      * source_published_at / historical_channel_available_at keep their
        source grade verbatim (UNKNOWN_EXACT_INSTANT when the official page
        carries only a date); a bare caller timestamp never writes
        announced_before_T -- only a proven exchange publication/revision
        version can, and none is proven here;
      * a session's schedule basis is a fact ABOUT that session's calendar
        row; it never back-dates the source calendar's own archive
        knowledge_from (which stays bound verbatim elsewhere).

    Absent evidence yields a declared-empty block (the run still reads the
    calendar through the consumer rule; it just has no proven publication
    version to record), never a guessed label.
    """
    bound_rows = {
        (
            _text_of(row, "venue_id"),
            str(_text_of(row, "session_date")),
        ): (row.get("is_open") if isinstance(row, Mapping) else getattr(row, "is_open", None))
        for row in getattr(request, "calendar_rows", ()) or ()
    }
    block: dict[str, Any] = {
        "schema_version": "p11.calendar-schedule-evidence.v1",
        "consumer_rule_id": None,
        "consumer_rule_version": None,
        # The run never asserts T-known: a caller's lone timestamp is not a
        # proven exchange publication, so announced_before_T stays False.
        "announced_before_T": False,
        "sessions": [],
    }
    # The consumer rule declares its OWN identity; a caller may only ASK for
    # sessions, never name the rule or the basis.  Trusted facts come from the
    # pinned rule (a projection of the verified P10 #747 RESULT), never from
    # caller bytes: the rule's proven basis, named source-document digests and
    # UNKNOWN instants are what the closure binds.
    if rule is not None:
        block["consumer_rule_id"] = rule.get("consumer_rule_id")
        block["consumer_rule_version"] = rule.get("consumer_rule_version")
        block["p10_result_ref"] = rule.get("p10_result_ref")
    trusted = {
        (_text_of(t, "venue_id"), str(_text_of(t, "session_date"))): t
        for t in (rule or {}).get("sessions", ()) or ()
    }
    requested = [
        (str(_text_of(e, "session_date")), _text_of(e, "venue_id"))
        for e in (evidence or {}).get("sessions", ()) or ()
    ]
    sessions = []
    for sess, venue in requested:
        key = (venue, sess)
        if key not in bound_rows:
            _fail(
                "calendar_schedule_evidence_unbound",
                f"schedule evidence claims {venue} {sess} but no bound "
                "calendar_row carries that venue/session_date",
            )
        t = trusted.get(key)
        if t is None:
            # A claim for a session the trusted rule does not prove is a named
            # refusal, never a silently-adopted basis.
            _fail(
                "calendar_schedule_evidence_unproven",
                f"schedule claim for {venue} {sess} has no proven basis in the "
                "pinned consumer rule",
            )
        if bound_rows[key] is not t["is_open"]:
            _fail("calendar_schedule_evidence_conflict", f"calendar open status differs for {key}")
        # Emit the TRUSTED basis + named digests + UNKNOWN instants, not the
        # caller's.  A caller-supplied basis/notice that conflicts with the
        # trusted fact is refused.
        for field in ("schedule_basis",):
            want = _text_of(
                next(
                    x
                    for x in (evidence or {}).get("sessions", ()) or ()
                    if _text_of(x, "session_date") == sess and _text_of(x, "venue_id") == venue
                ),
                field,
            )
            if want is not None and want != _text_of(t, field):
                _fail(
                    "calendar_schedule_evidence_conflict",
                    f"caller schedule_basis {want!r} for {venue} {sess} "
                    f"conflicts with the trusted {_text_of(t, field)!r}",
                )
        sessions.append(
            {
                "venue_id": venue,
                "session_date": sess,
                "is_open": bound_rows[key],
                "schedule_basis": _text_of(t, "schedule_basis"),
                "source_documents": [
                    {
                        "kind": _text_of(d, "kind"),
                        "notice_id": _text_of(d, "notice_id"),
                        "rule_clause": _text_of(d, "rule_clause"),
                        "publication_date": _text_of(d, "publication_date"),
                        "sha256": _text_of(d, "sha256"),
                        "source_receipt_sha256": _text_of(d, "source_receipt_sha256"),
                        "published_at": _text_of(d, "published_at"),
                        "published_at_grade": _text_of(d, "published_at_grade"),
                    }
                    for d in _text_of(t, "source_documents") or ()
                ],
                "source_published_at": _text_of(t, "source_published_at"),
                "source_published_at_state": _text_of(t, "source_published_at_state"),
                "historical_channel_available_at": _text_of(t, "historical_channel_available_at"),
            }
        )
    block["sessions"] = sessions
    return block


def _date_to_of(m: Any) -> date | None:
    date_to = m.get("date_to") if isinstance(m, Mapping) else getattr(m, "date_to", None)
    if date_to is None:
        return None
    return date_to if isinstance(date_to, date) else date.fromisoformat(str(date_to)[:10])


def _date_from_of(m: Any) -> date | None:
    date_from = m.get("date_from") if isinstance(m, Mapping) else getattr(m, "date_from", None)
    if date_from is None:
        return None
    return date_from if isinstance(date_from, date) else date.fromisoformat(str(date_from)[:10])


def _instant_of(m: Any, field: str) -> datetime | None:
    value = m.get(field) if isinstance(m, Mapping) else getattr(m, field, None)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _member_available_at(m: Any) -> datetime | None:
    """The LATER of the member's publish (visible_at) and acquire
    (knowledge_at) instants -- the earliest instant a consumer could actually
    read it.  A member not yet obtained by THIS run's real read instant is
    unavailable at read and its dependent is refused by dependency.
    """
    instants = [
        x for x in (_instant_of(m, "visible_at"), _instant_of(m, "knowledge_at")) if x is not None
    ]
    return max(instants) if instants else None


def _used_member_ids(request: Any) -> dict[str, dict[str, Any]]:
    """Collect the member ids the run consumes, split shared vs per-security.

    A replay envelope is a PROJECTION: securities[] identity/dependency rows,
    action rows, calendar/listing rows, action registers and declared
    member_evidence each name the source member they came from.

    A member bound through a single security's depends_on_member_ids or
    actions rows is PER-SECURITY: its failure isolates that security only.
    A member bound through calendar_rows, listing_rows, action_coverage
    registers, member_evidence or required_member_ids is SHARED: it feeds
    the whole-day gate and its failure blocks the day.  A member referenced
    by NO consumed row is unused even though it ships inside the package.
    """
    used: dict[str, dict[str, Any]] = {}

    def add(member_id: Any, kind: str, code: str | None) -> None:
        if member_id is None:
            return
        rec = used.setdefault(str(member_id), {"shared": set(), "securities": set()})
        if code is None:
            rec["shared"].add(kind)
        else:
            rec["securities"].add(code)

    for sec in getattr(request, "securities", ()) or ():
        ident = sec.get("identity") if isinstance(sec, Mapping) else getattr(sec, "identity", None)
        code = (ident or {}).get("listing_code") if isinstance(ident, Mapping) else None
        dep = (ident or {}).get("depends_on_member_ids", ()) if isinstance(ident, Mapping) else ()
        for mid in dep:
            add(mid, "identity", code)
        acts = sec.get("actions") if isinstance(sec, Mapping) else getattr(sec, "actions", ())
        for act in acts or ():
            if isinstance(act, Mapping):
                add(act.get("source_member_id") or act.get("member_id"), "action", code)
    for row in getattr(request, "calendar_rows", ()) or ():
        if isinstance(row, Mapping):
            add(row.get("source_member_id"), "calendar", None)
    for row in getattr(request, "listing_rows", ()) or ():
        if isinstance(row, Mapping):
            add(row.get("source_member_id"), "listing", None)
    ac = getattr(request, "action_coverage", None)
    if isinstance(ac, Mapping):
        for reg in ac.get("registers", ()) or ():
            if isinstance(reg, Mapping):
                add(reg.get("source_member_id"), "action_register", None)
    for me in getattr(request, "member_evidence", ()) or ():
        me_id = _member_id_of(me)
        me_required = (
            bool(me.get("required", True))
            if isinstance(me, Mapping)
            else bool(getattr(me, "required", True))
        )
        # member_evidence binds the SHA/identity of an opened member; its
        # content dependency is still carried by the rows above.  A member the
        # reader opened but marked not relevant_for_T (required=False) is
        # shared evidence, not a same-T content dependency - it must not be
        # inflated into the shared required set and must not block neighbours.
        if me_id is not None and me_id not in used:
            add(
                me_id,
                "member_evidence_opened_not_used" if not me_required else "member_evidence",
                None,
            )
    for mid in getattr(request, "required_member_ids", ()) or ():
        add(mid, "required", None)
    return used


def _analyze_member_use(
    request: Any, market_date: date, observed_at: datetime | None = None
) -> dict[str, Any]:
    """Classify each source member by USED-row content vs the period.

    Three dependency tiers decide the blast radius of a failure:
      shared     -- the member feeds the whole-day gate (calendar / listing /
                    action registers / member_evidence / required).  Its
                    unavailability or post-window truth blocks the day.
      securities -- the member is bound only through one security's
                    depends_on_member_ids / actions.  Its failure isolates
                    that security (and surfaces a held reminder if the
                    security is already held); neighbours keep evaluating.
      unused     -- no consumed row references it; it is named, never a block.

    A member's date_to is a CONTAINER edge, disclosed not gated; the real
    post-window signal is a member whose coverage only BEGINS past T.

    Returns members (per-member disclosure incl. dependency tier), the
    shared blocking member ids, per-security isolation map, unused/late
    partition names, unavailable_at_read split by tier, match_window_through.
    """
    used = _used_member_ids(request)
    match_end = market_date + timedelta(days=1)
    members: list[dict[str, Any]] = []
    blocking_shared: list[str] = []
    isolated: dict[str, list[str]] = {}  # security_code -> member_ids
    excluded: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    for m in getattr(request, "source_members", ()) or ():
        mid = _member_id_of(m)
        d_to = _date_to_of(m)
        d_from = _date_from_of(m)
        avail = _member_available_at(m)
        u = (
            used.get(str(mid), {"shared": set(), "securities": set()})
            if mid is not None
            else {"shared": set(), "securities": set()}
        )
        shared = sorted(u["shared"])
        secs = sorted(u["securities"])
        is_used = bool(shared or secs)
        # Split "opened" evidence-only members from content dependencies: a
        # member whose only binding is member_evidence with required=False was
        # opened by the reader but carries no same-T row this run consumes.
        # It stays evidence-classified, never a shared blocking member.
        content_shared = [k for k in shared if k != "member_evidence_opened_not_used"]
        evidence_only = bool(shared) and not content_shared
        if content_shared:
            tier = "shared"
        elif secs:
            tier = "per_security" if len(secs) == 1 else "multi_security"
        elif evidence_only:
            tier = "opened_not_used"
        else:
            tier = "unused"
        is_used = bool(content_shared or secs)
        rec = {
            "member_id": mid,
            "container_date_from": d_from.isoformat() if d_from else None,
            "container_date_to": d_to.isoformat() if d_to else None,
            "available_at": avail.isoformat() if avail else None,
            # Back-compat union of binding kinds (shared + per-security).
            "used_by": sorted(set(shared) | ({"security"} if secs else set())),
            "dependency_tier": tier,
            "shared_kinds": shared,
            "securities": secs,
            "is_used": is_used,
        }
        unreadable = avail is not None and observed_at is not None and avail > observed_at
        wholly_late = d_from is not None and d_from > match_end
        # An evidence-only opened member that was not yet obtainable by this
        # run's read instant is named on the evidence list, never a block.
        if unreadable and tier == "opened_not_used":
            rec["disposition"] = "opened_not_used"
            rec["obtained_after_actual_read"] = True
            members.append(rec)
            continue
        if unreadable and is_used:
            rec["disposition"] = "unavailable_at_read"
            rec["reason"] = "obtained_after_actual_read"
            unavailable.append(
                {
                    "member_id": mid,
                    "available_at": avail.isoformat(),
                    "actual_read_at": observed_at.isoformat(),
                    "dependency_tier": tier,
                    "securities": secs,
                    "reason": "obtained_after_actual_read",
                }
            )
            if tier == "shared":
                blocking_shared.append(str(mid))
            else:
                for code in secs:
                    isolated.setdefault(code, []).append(str(mid))
            members.append(rec)
            continue
        if is_used and wholly_late:
            # A used member whose coverage only begins past the signal is a
            # post-window contradiction; tier decides the blast radius.
            rec["disposition"] = "blocked_post_window"
            if tier == "shared":
                blocking_shared.append(str(mid))
            else:
                for code in secs:
                    isolated.setdefault(code, []).append(str(mid))
        elif not is_used and wholly_late:
            rec["disposition"] = "excluded_late_partition"
            excluded.append(
                {
                    "member_id": mid,
                    "container_date_from": d_from.isoformat(),
                    "container_date_to": d_to.isoformat() if d_to else None,
                    "reason": "wholly_post_T_partition_no_used_rows",
                }
            )
        else:
            rec["disposition"] = "used" if is_used else "unused"
            if not is_used and unreadable:
                rec["unused_obtained_after_read"] = True
        members.append(rec)
    return {
        "members": members,
        "blocking_shared_members": sorted(set(blocking_shared)),
        # Back-compat alias for the previous blocking list.
        "blocking_used_members": sorted(set(blocking_shared)),
        "isolated_by_security": {k: sorted(set(v)) for k, v in isolated.items()},
        "excluded_late_partitions": excluded,
        "unavailable_at_read": unavailable,
        "member_count": len(members),
        # The three public counts are mutually exclusive by dependency_tier and
        # sum to member_count: a member is counted by the class that binds it,
        # not by its read disposition (an opened_not_used member is not also a
        # registered_not_read member even though it was not read for T).
        "used_for_T_count": sum(
            1
            for m in members
            if m.get("is_used")
            and m.get("dependency_tier") in ("shared", "per_security", "multi_security")
        ),
        "opened_not_used_count": sum(
            1 for m in members if m.get("dependency_tier") == "opened_not_used"
        ),
        "registered_not_read_count": sum(
            1 for m in members if m.get("dependency_tier") == "unused"
        ),
        "match_window_through": match_end.isoformat(),
    }
