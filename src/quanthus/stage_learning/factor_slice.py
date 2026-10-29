"""One additive factor sidecar and its real, retrospective research queue consumer.

No labels, model, orders, source qualification or historical PIT admission live here.
The old FeatureRegistry and existing Facts engines are consumed without modification.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any

from quanthus.feature_registry import load_feature_registry
from quanthus.pipelines.securities_warehouse.p10_stage34_prefix import checked_member, sha_file
from quanthus.stage_learning.core import build_activity_observations
from quanthus.stage_learning.native_prefix import (
    NativeAction,
    NativePrefixScope,
    project_native_prefix,
)
from quanthus.structure_v2 import build_structure_v2
from quanthus.structure_v2_inputs import Admission, InputPolicy, canonical, digest, exact_sha

POLICY = "config/research/factor541-volume-review.v1.json"
USE_POLICY = "config/research/factor541-five-session-diagnostic.v1.json"
PURPOSE = "RETROSPECTIVE_RESEARCH_REVIEW_QUEUE_ONLY"
FACTOR = "liquidity.volume_ratio20"
SUPPORTED_POLICY_DIGEST = "sha256:2e15957487b5234d3c83b67489ccc108e06baa3f3bd1e7a51e20d05ed4cf1a52"
TABLES = ("dwd_listing", "dwd_trading_calendar", "dwd_security_bar",
          "dwd_tradability_state", "dwd_observation_gap", "dwd_quality_exception")


class FactorSliceError(ValueError):
    """An input or policy identity cannot support this bounded slice."""


def load_catalog(repo: Path) -> dict[str, Any]:
    """One canonical asset per registry id/version, with cross-domain views/aliases."""
    spec = json.loads((repo / POLICY).read_text())
    # A new window, unit, scope or missing-value policy needs a new implementation.
    if digest(spec) != SUPPORTED_POLICY_DIGEST:
        raise FactorSliceError("FACTOR_UNSUPPORTED_POLICY_CONTRACT")
    expected_consumer = {
        "id": "price-breakout-research-review-queue-v1",
        "price_pool": "close_strictly_above_prior20_high",
        "baseline": "price_pool_without_volume_gate",
        "candidate": "price_pool_with_volume_ratio_gte_1",
        "volume_minimum": "1", "slots_per_day": 5,
        "ranking": "ascending_sha256(seed,market_date,security_id)",
        "seed": "factor541-volume-review-v1",
        "unknown_factor": "abstain_from_candidate_retain_in_denominator",
        "filler_rows": False, "positions_or_orders": False,
    }
    if (spec["purpose"] != PURPOSE or spec["consumer"] != expected_consumer
            or spec["scope"]["historical_as_known"] is not False
            or spec["scope"]["knowledge_cutoff"] is not None
            or any(value is not False for value in spec["authority"].values())):
        raise FactorSliceError("FACTOR_PURPOSE_OR_POLICY")
    registry_path = checked_member(repo, spec["registry"]["path"])
    if sha_file(registry_path) != spec["registry"]["sha256"]:
        raise FactorSliceError("FACTOR_REGISTRY_CHANGED")
    registry = load_feature_registry(registry_path)
    factor = registry.get(FACTOR, "1.0.0")
    if factor.unit != "ratio" or factor.formula != (
        "volume_t / mean(volume over the 20 sessions before T)"
    ):
        raise FactorSliceError("FACTOR_DEFINITION_CHANGED")
    assets = [{"asset_id": f"{d.feature_id}@{d.version}",
               "definition_digest": d.digest, "definition": d.to_dict(),
               "business_domains": (["liquidity", "structure_activity"]
                                    if d.feature_id == FACTOR else [d.feature_id.split('.')[0]]),
               "formation_kind": "deterministic_causal_numeric_feature",
               "computed_in_this_slice": d.feature_id == FACTOR}
              for d in sorted(registry.definitions, key=lambda d: d.key)]
    return {"schema_version": "quanthus.factor-catalog-sidecar.v1",
            "registry_digest": registry.digest, "assets": assets,
            "implementation_aliases": {"stage.activity.volume-ratio20": f"{FACTOR}@1.0.0"},
            "selected_definition_digest": factor.digest, "spec_digest": digest(spec),
            "spec": spec, "legacy_registry_or_feature_lineage_modified": False,
            "legacy_PIT_feature_fact": False}


def _unique_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    keys = [(r["security_id"], r["market_date"]) for r in rows]
    if len(set(keys)) != len(keys):
        raise FactorSliceError("DUPLICATE_SECURITY_DAY")
    for row in rows:
        if (date.fromisoformat(row["market_date"]).year != 2021
                or not row["security_id"] or not row["listing_id"]
                or row["historical_knowledge_cutoff"] is not None):
            raise FactorSliceError("FACTOR_SCOPE_OR_KNOWLEDGE")
        if type(row["admitted"]) is not bool:
            raise FactorSliceError("FACTOR_ADMISSION_TYPE")
        if (row["feature_id"] != FACTOR or row["feature_version"] != "1.0.0"
                or not exact_sha(row["definition_digest"])
                or not exact_sha(row["source_member_digest"])):
            raise FactorSliceError("FACTOR_DEFINITION_OR_SOURCE_BINDING")
        for name in ("resistance_margin20", "volume_ratio20"):
            value = row[name]
            if value is not None and (not isinstance(value, str)
                                      or not Decimal(value).is_finite()):
                raise FactorSliceError("FACTOR_NUMBER")
        if row["volume_ratio20"] is not None and Decimal(row["volume_ratio20"]) < 0:
            raise FactorSliceError("FACTOR_NEGATIVE_VOLUME")


def project_facts(admissions: tuple[Admission, ...], *, spec_digest: str,
                  calendar_ref: str, definition_digest: str,
                  anchors: dict[tuple[str, date], Decimal] | None = None,
                  component_costs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Build each expensive prerequisite once; no candidate/reference inputs exist."""
    if any(a.row.market_date.year != 2021 for a in admissions):
        raise FactorSliceError("FACTOR_PROTECTED_OR_OUT_OF_SCOPE")
    started = time.perf_counter()
    structure = build_structure_v2(admissions, spec_digest=spec_digest,
                                   calendar_ref=calendar_ref, lookbacks=(20,))
    structure_seconds = time.perf_counter() - started
    started = time.perf_counter()
    activity = {a.key: a for a in build_activity_observations(admissions, window=20)}
    if component_costs is not None:
        component_costs.extend([
            {"component": "shared_price_structure", "elapsed_seconds": structure_seconds,
             "rows": len(admissions), "build_count": 1},
            {"component": "volume_activity_engine", "elapsed_seconds": time.perf_counter()-started,
             "rows": len(admissions), "build_count": 1,
             "claim": "isolated_engine_component_not_total_factor_incremental_cost"}])
    high_anchors = {a.revision_id: a for a in structure.anchors if a.side == "high"}
    prior_dates = {}
    previous_dates: dict[str, date] = {}
    for a in sorted(admissions, key=lambda a: a.key):
        prior_dates[a.key] = previous_dates.get(a.row.security_code)
        previous_dates[a.row.security_code] = a.row.market_date
    # Structure also measures old frozen episode targets. Only the rolling anchor
    # ending on the immediately prior input session defines this consumer's pool.
    measures = {(m.security_code, m.market_date): m for m in structure.measurements
                if m.anchor_revision in high_anchors
                and high_anchors[m.anchor_revision].window_end
                == prior_dates[m.security_code, m.market_date]}
    if anchors is not None:
        if anchors:
            raise FactorSliceError("FACTOR_ANCHOR_TMP_MUST_START_EMPTY")
        anchors.update({key: m.anchor_level for key, m in measures.items()})
    rows = []
    for a in sorted(admissions, key=lambda a: a.key):
        r, volume = a.row, activity[a.key]
        price = measures.get(a.key)
        rows.append({
            "security_id": r.security_id, "listing_id": r.listing_id,
            "security_code": r.security_code, "market_date": r.market_date.isoformat(),
            "admitted": a.admitted, "admission_reason": a.reason,
            "source_member_digest": r.member_digest, "source_revision": r.source_revision,
            "source_artifact_sha256": r.source_artifact_sha256,
            "source_dwd_known_from": r.dwd_known_from,
            "result_as_of": r.result_as_of.isoformat(),
            "historical_knowledge_cutoff": None,
            "coordinate_ref": r.coordinate_ref, "calendar_ref": r.calendar_member_digest,
            "action_ref": r.action_member_digest,
            "resistance_margin20": None if price is None else format(price.value, "f"),
            "price_fact_revision": None if price is None else price.identity.revision_id,
            "volume_ratio20": (None if volume.volume_ratio is None
                               else format(volume.volume_ratio, "f")),
            "volume_reason": volume.reason, "volume_fact_revision": volume.revision_id,
            "feature_id": FACTOR, "feature_version": "1.0.0",
            "definition_digest": definition_digest,
            "factor_revision": digest({"fact": volume.revision_id,
                                       "definition": definition_digest}),
        })
    _unique_rows(rows)
    return rows


def audit_arithmetic(admissions: tuple[Admission, ...],
                     facts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Independent list-based arithmetic oracle; no call to either Facts engine."""
    by_key = {(r["security_code"], r["market_date"]): r for r in facts}
    if len(by_key) != len(facts) or len(facts) != len(admissions):
        raise FactorSliceError("FACTOR_ORACLE_DENOMINATOR")
    history: dict[str, list[Admission]] = defaultdict(list)
    checked_price = checked_volume = zero_current = unknown = 0
    for a in sorted(admissions, key=lambda a: a.key):
        row = a.row
        previous = history[row.security_code]
        if (not a.admitted or row.reset_reason or (previous and (
                previous[-1].row.coordinate_ref != row.coordinate_ref
                or previous[-1].row.listing_id != row.listing_id
                or previous[-1].row.session_index + 1 != row.session_index))):
            previous.clear()
        price_value = volume_value = None
        if a.admitted and len(previous) >= 20:
            prior = previous[-20:]
            with localcontext(Context(prec=28)):
                highs = [p.row.high for p in prior if isinstance(p.row.high, Decimal)]
                assert len(highs) == 20 and isinstance(row.close, Decimal)
                price_value = row.close / max(highs) - 1
                if not a.missing_volume and not any(p.missing_volume for p in prior):
                    volumes = [p.row.volume for p in prior if isinstance(p.row.volume, Decimal)]
                    assert len(volumes) == 20 and isinstance(row.volume, Decimal)
                    average = sum(volumes, Decimal(0)) / 20
                    if average > 0:
                        volume_value = row.volume / average
        f = by_key[row.security_code, row.market_date.isoformat()]
        for name, value in (("resistance_margin20", price_value),
                            ("volume_ratio20", volume_value)):
            if f[name] != (None if value is None else format(value, "f")):
                raise FactorSliceError("FACTOR_ARITHMETIC_MISMATCH")
        checked_price += int(price_value is not None)
        checked_volume += int(volume_value is not None)
        zero_current += int(a.admitted and not a.missing_volume and row.volume == 0)
        unknown += int(volume_value is None)
        if a.admitted:
            previous.append(a)
    return {"status": "PASS_EXACT_ARITHMETIC", "rows": len(facts),
            "price_values_checked": checked_price, "volume_values_checked": checked_volume,
            "zero_current_volume_rows": zero_current, "unknown_volume_rows": unknown,
            "semantic_market_truth_or_prediction_quality": False}


@dataclass(frozen=True)
class PanelIdentity:
    """Expected identity supplied by a trusted producer/receipt, never by a raw panel.

    The producer freezes this only after native admission and exact arithmetic pass.
    Reloading an external panel requires this identity from the trusted receipt;
    recomputing it from that external panel would not establish source qualification.
    """

    panel_digest: str
    definition_digest: str
    source_members: tuple[tuple[str, str, str], ...]
    policy_digest: str = SUPPORTED_POLICY_DIGEST


def verify_panel(facts: Sequence[Mapping[str, Any]], identity: PanelIdentity | None) -> None:
    """Bind all row bytes and each source key to the previously frozen identity."""
    if not isinstance(identity, PanelIdentity):
        raise FactorSliceError("FACTOR_PANEL_IDENTITY_REQUIRED")
    _unique_rows(facts)
    catalog = load_catalog(Path(__file__).resolve().parents[3])
    if (identity.policy_digest != catalog["spec_digest"]
            or identity.definition_digest != catalog["selected_definition_digest"]
            or not exact_sha(identity.panel_digest)
            or digest(facts) != identity.panel_digest):
        raise FactorSliceError("FACTOR_PANEL_IDENTITY_BINDING")
    sources = {(s, d): member for s, d, member in identity.source_members}
    if (len(sources) != len(identity.source_members)
            or set(sources) != {(r["security_id"], r["market_date"]) for r in facts}
            or any(not exact_sha(m) for m in sources.values())):
        raise FactorSliceError("FACTOR_PANEL_SOURCE_BINDING")
    for row in facts:
        if (row["definition_digest"] != identity.definition_digest
                or row["source_member_digest"] != sources[row["security_id"], row["market_date"]]
                or not isinstance(row.get("volume_fact_revision"), str)
                or not exact_sha(row["volume_fact_revision"])
                or row["factor_revision"] != digest({"fact": row["volume_fact_revision"],
                                                    "definition": identity.definition_digest})):
            raise FactorSliceError("FACTOR_DEFINITION_OR_SOURCE_BINDING")


def consume_queue(facts: Sequence[Mapping[str, Any]], *, volume_gate: bool,
                  identity: PanelIdentity | None = None) -> dict[str, Any]:
    """A concrete daily research queue; selection is never an entry or sell signal."""
    verify_panel(facts, identity)
    if type(volume_gate) is not bool:
        raise FactorSliceError("FACTOR_GATE_TYPE")
    days: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in facts:
        days[r["market_date"]].append(r)
    output = []
    for day, rows in sorted(days.items()):
        pool = [r for r in rows if r["admitted"] and r["resistance_margin20"] is not None
                and Decimal(r["resistance_margin20"]) > 0]
        eligible = [r for r in pool if not volume_gate or (r["volume_ratio20"] is not None
                    and Decimal(r["volume_ratio20"]) >= 1)]
        eligible.sort(key=lambda r: digest(["factor541-volume-review-v1", day, r["security_id"]]))
        selected = eligible[:5]
        output.append({"market_date": day, "full_units": len(rows),
                       "price_pool_units": len(pool), "eligible_units": len(eligible),
                       "unknown_factor_in_price_pool": sum(r["volume_ratio20"] is None
                                                           for r in pool),
                       "used_slots": len(selected), "unused_slots": 5-len(selected),
                       "selected": [{k: r[k] for k in ("security_id", "market_date",
                                     "source_member_digest", "price_fact_revision",
                                     "factor_revision", "volume_ratio20")} for r in selected]})
    return {"days": output, "input_rows": len(facts),
            "full_denominator_digest": digest(sorted((r["security_id"], r["market_date"])
                                                    for r in facts)),
            "actual_review_slots": sum(d["used_slots"] for d in output),
            "maximum_review_slots": 5*len(output),
            "production_or_P6_admitted": False, "entry_or_exit_authorized": False}


def compare_queues(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    if (baseline["full_denominator_digest"] != candidate["full_denominator_digest"]
            or [d["market_date"] for d in baseline["days"]]
            != [d["market_date"] for d in candidate["days"]]):
        raise FactorSliceError("FACTOR_COMPARISON_DENOMINATOR")
    before = {(r["security_id"], r["market_date"]): r
              for d in baseline["days"] for r in d["selected"]}
    after = {(r["security_id"], r["market_date"]): r
             for d in candidate["days"] for r in d["selected"]}

    def volume_classes(rows: Mapping[Any, Mapping[str, Any]]) -> dict[str, int]:
        counts = Counter("unknown" if r["volume_ratio20"] is None else
                         "gte_1" if Decimal(r["volume_ratio20"]) >= 1 else "lt_1"
                         for r in rows.values())
        return {k: counts[k] for k in ("gte_1", "lt_1", "unknown")}

    price_pool = sum(d["price_pool_units"] for d in baseline["days"])
    gate_pool = sum(d["eligible_units"] for d in candidate["days"])
    return {"schema_version": "quanthus.factor-review-impact.v1",
            "full_rows": baseline["input_rows"], "days": len(baseline["days"]),
            "price_pool_units": price_pool, "volume_gate_pool_units": gate_pool,
            "price_pool_not_carried_forward": price_pool-gate_pool,
            "unknown_factor_in_price_pool": sum(d["unknown_factor_in_price_pool"]
                                                for d in baseline["days"]),
            "baseline_review_slots": len(before), "candidate_review_slots": len(after),
            "review_slots_saved": len(before)-len(after),
            "same_actual_cost": len(before) == len(after),
            "selected_retained": len(before.keys() & after.keys()),
            "selected_removed": len(before.keys()-after.keys()),
            "selected_replacements": len(after.keys()-before.keys()),
            "baseline_selected_volume_classes": volume_classes(before),
            "candidate_selected_volume_classes": volume_classes(after),
            "maximum_slots_per_arm": baseline["maximum_review_slots"],
            "human_minutes_saved": None, "predictive_or_economic_value": "NOT_ASSESSED",
            "screening_quality_claim": "DEFINITIONAL_VOLUME_FILTER_ONLY",
            "purpose_admission": "BOUNDED_RETROSPECTIVE_RESEARCH_QUEUE_ONLY",
            "trading_or_P6_admission": False}


def read_admissions(source: Path, handoff_sha: str) -> tuple[tuple[Admission, ...], str]:
    """Consume only the exact already-qualified subset, never hunt original archives."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    handoff_path = checked_member(source, "evidence/p10-source-handoff.safe.json")
    if sha_file(handoff_path) != handoff_sha:
        raise FactorSliceError("FACTOR_SOURCE_HANDOFF_CHANGED")
    h = json.loads(handoff_path.read_text())
    if (h["status"] != "QUALIFIED_RETROSPECTIVE_COORDINATE_ONLY"
            or h["selected_issuers"] != 128 or h["business_year"] != 2021
            or h["through"] != "2021-12-31" or h["historical_as_known"] is not False
            or h["selection_sha256"] != (
                "90dace3a2a140b3270c684a815e775ebd73ef1f194cb9285dabf1508748c5d89")):
        raise FactorSliceError("FACTOR_SOURCE_SCOPE")
    files = {r["path"]: r for r in h["files"]}
    if len(files) != len(h["files"]):
        raise FactorSliceError("FACTOR_SOURCE_DUPLICATE_MEMBER")

    def verified(name: str) -> Path:
        member = files[name]
        p = checked_member(source, name)
        if p.stat().st_size != member["size_bytes"] or sha_file(p) != member["sha256"]:
            raise FactorSliceError("FACTOR_SOURCE_MEMBER_CHANGED")
        return p

    selected = json.loads(verified(h["selection_path"]).read_text())["proposed"]
    tables = {}
    for m in h["native_materializations"]:
        if m["table"] not in TABLES:
            continue
        if m["sha256"] != files[m["path"]]["sha256"]:
            raise FactorSliceError("FACTOR_SOURCE_TABLE_IDENTITY")
        p = pq.ParquetFile(verified(m["path"]))
        if p.metadata.num_rows != m["rows"] or m["table"] in tables:
            raise FactorSliceError("FACTOR_SOURCE_TABLE_CENSUS")
        tables[m["table"]] = p.read().to_pylist()
    if set(tables) != set(TABLES) or len(selected) != 128:
        raise FactorSliceError("FACTOR_SOURCE_TABLES_OR_SELECTION")
    spec = digest({"source_handoff": handoff_sha, "adapter": "factor541-qualified-prefix-v1"})
    calendar_ref = digest(tables["dwd_trading_calendar"])
    admissions: list[Admission] = []
    for s in selected:
        if (s["proposed_start"] != s["first_listing"]
                or not "2021-01-01" <= s["first_listing"] <= s["proposed_through"]
                <= "2021-12-31"):
            raise FactorSliceError("FACTOR_SOURCE_WINDOW")
        actions = [NativeAction(a["action_id"], date.fromisoformat(a["ex_date"]),
                                a["action_type"], a["source_revision"], a["evidence_digest"])
                   for a in h["canonical_action_date_boundaries"] if a["code"] == s["code"]]
        scope = NativePrefixScope(
            s["code"], s["security_id"], s["listing_id"],
            "SSE" if s["code"].startswith("6") else "SZSE",
            date.fromisoformat(s["first_listing"]), date.fromisoformat(s["proposed_through"]),
            "sha256:"+handoff_sha, digest({"handoff": handoff_sha, "code": s["code"]}),
            digest([a.identity() for a in actions]))
        admissions.extend(project_native_prefix(
            scope, InputPolicy(spec, (s["code"],), calendar_ref),
            listings=[r for r in tables["dwd_listing"] if r["listing_id"] == s["listing_id"]],
            calendar=[r for r in tables["dwd_trading_calendar"] if r["venue_id"] == s["venue_id"]],
            bars=[r for r in tables["dwd_security_bar"] if r["listing_id"] == s["listing_id"]],
            states=[r for r in tables["dwd_tradability_state"]
                    if r["listing_id"] == s["listing_id"]],
            gaps=[r for r in tables["dwd_observation_gap"] if r["listing_id"] == s["listing_id"]],
            exceptions=tables["dwd_quality_exception"], actions=actions))
    if len(admissions) != 19302 or len({a.row.security_id for a in admissions}) != 128:
        raise FactorSliceError("FACTOR_SOURCE_FULL_DENOMINATOR")
    return tuple(sorted(admissions, key=lambda a: a.key)), calendar_ref


def execution_members(repo: Path) -> list[dict[str, str]]:
    """Exact closed Python tree plus the two semantic configuration members."""
    names = {POLICY, USE_POLICY, "config/p3-baseline-features.research.v1.json"}

    def unreadable(error: OSError) -> None:
        raise FactorSliceError("FACTOR_CODE_CLOSURE_UNREADABLE") from error

    root = repo / "src/quanthus"
    if not root.is_dir() or root.is_symlink():
        raise FactorSliceError("FACTOR_CODE_CLOSURE_ROOT")
    for directory, dirs, files in os.walk(root, onerror=unreadable):
        if any((Path(directory) / d).is_symlink() for d in dirs):
            raise FactorSliceError("FACTOR_CODE_CLOSURE_SYMLINK")
        names.update((Path(directory) / name).relative_to(repo).as_posix()
                     for name in files if name.endswith(".py"))
    return [{"path": name, "sha256": sha_file(checked_member(repo, name))}
            for name in sorted(names)]


def verify_execution_closure(repo: Path, members: Any) -> None:
    if (repo != Path(__file__).resolve().parents[3] or not isinstance(members, list)
            or not members or members != execution_members(repo)):
        raise FactorSliceError("FACTOR_CODE_CLOSURE_INCOMPLETE_OR_CHANGED")
    declared = {repo / m["path"] for m in members}
    # Imports from a different checkout must not be attributed to the declared tree.
    for name, module in tuple(sys.modules.items()):
        if name == "quanthus" or name.startswith("quanthus."):
            origin = getattr(module, "__file__", None)
            if origin is not None and Path(origin).resolve() not in declared:
                raise FactorSliceError("FACTOR_CODE_CLOSURE_EXECUTED_ORIGIN")


def run(*, source: Path, repo: Path, output: Path, budget_path: Path,
        utility: bool = False) -> dict[str, Any]:
    """A single immutable DAG run. A separate call is an independent reproduction Job."""
    started = time.monotonic()
    budget = json.loads(budget_path.read_text())
    if (repo.resolve() != repo or source.resolve() != source
            or output.resolve() != output or output.exists()
            or source.is_relative_to(output) or output.is_relative_to(source)
            or repo.is_relative_to(output) or output.is_relative_to(repo)
            or str(output) != budget["output"] or str(source) != budget["source_root"]
            or budget["purpose"] != PURPOSE or budget["new_source_requests"] != 0):
        raise FactorSliceError("FACTOR_FROZEN_PATH_OR_PURPOSE")
    stages = []

    def guard() -> None:
        stat = os.statvfs(output.parent)
        written = (sum(p.stat().st_size for p in output.iterdir() if p.is_file())
                   if output.exists() else 0)
        if (datetime.now(timezone.utc) > datetime.fromisoformat(budget["deadline"])
                or time.monotonic()-started > budget["maximum_seconds"]
                or resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024 > budget["rss_bytes"]
                or stat.f_bavail*stat.f_frsize < budget["minimum_free_bytes"]
                or written > budget["maximum_output_bytes"]):
            raise FactorSliceError("FACTOR_EXECUTION_BUDGET")

    guard()
    if utility:
        # Load the optional executor before checking actual module origins.
        from quanthus.stage_learning.factor_review_utility import evaluate, load_contract
    verify_execution_closure(repo, budget.get("code_and_config_members"))
    catalog = load_catalog(repo)
    utility_spec = None
    if type(utility) is not bool:
        raise FactorSliceError("FACTOR_USE_ACTIVATION_TYPE")
    if utility:
        utility_spec = load_contract(repo)
        if budget.get("utility_contract_sha256") != sha_file(checked_member(repo, USE_POLICY)):
            raise FactorSliceError("FACTOR_USE_CONTRACT_NOT_FROZEN")
        if budget.get("source_handoff_sha256") != utility_spec["scope"]["source_handoff_sha256"]:
            raise FactorSliceError("FACTOR_USE_SOURCE_HANDOFF_NOT_FROZEN")
    output.mkdir()

    def save(name: str, value: Any) -> str:
        guard()
        data = canonical(value)+b"\n"
        if (len(data)+sum(p.stat().st_size for p in output.iterdir())
                > budget["maximum_output_bytes"]):
            raise FactorSliceError("FACTOR_OUTPUT_BUDGET")
        with (output/name).open("xb") as stream:
            stream.write(data)
        return digest(value)

    def stage(name: str, fn: Any, disposition: str = "BUILT_ONCE") -> Any:
        guard()
        start = time.perf_counter()
        value = fn()
        stages.append({"stage": name, "elapsed_seconds": time.perf_counter()-start,
                       "disposition": disposition})
        guard()
        return value

    admissions, calendar_ref = stage("source_admission", lambda: read_admissions(
        source, budget["source_handoff_sha256"]))
    anchor_levels: dict[tuple[str, date], Decimal] = {}
    component_costs: list[dict[str, Any]] = []
    facts = stage("facts_and_factor", lambda: project_facts(
        admissions, spec_digest=catalog["spec_digest"], calendar_ref=calendar_ref,
        definition_digest=catalog["selected_definition_digest"],
        anchors=anchor_levels if utility else None, component_costs=component_costs))
    facts_digest = save("facts-factor.private.json", facts)
    oracle = stage("independent_arithmetic", lambda: audit_arithmetic(admissions, facts))
    identity = PanelIdentity(facts_digest, catalog["selected_definition_digest"], tuple(
        (a.row.security_id, a.row.market_date.isoformat(), a.row.member_digest)
        for a in admissions))
    identity_digest = save("panel-identity.private.json", {
        "panel_digest": identity.panel_digest, "definition_digest": identity.definition_digest,
        "source_members": identity.source_members, "policy_digest": identity.policy_digest})
    baseline = stage("baseline_queue", lambda: consume_queue(
        facts, volume_gate=False, identity=identity),
                     "REUSED_SHARED_FACTS")
    candidate = stage("factor_queue", lambda: consume_queue(
        facts, volume_gate=True, identity=identity),
                      "REUSED_SHARED_FACTS")
    queue_digest = save("queues.private.json", {"baseline": baseline, "candidate": candidate})
    summary = compare_queues(baseline, candidate)
    save("catalog.safe.json", catalog)
    save("arithmetic.safe.json", oracle)
    save("screening.safe.json", summary)
    save("component-costs.safe.json", component_costs)
    utility_digest = None
    if utility_spec is not None:
        result = stage("five_session_use_diagnostic", lambda: evaluate(
            admissions, facts, anchor_levels, baseline, candidate,
            identity=identity, spec=utility_spec), "REUSED_SHARED_FACTS_AND_ANCHORS")
        save("utility-outcomes.private.json", result["private_rows"])
        utility_digest = save("utility.safe.json", result["safe"])

        def queue_costs() -> dict[str, Any]:
            times: dict[bool, list[float]] = {False: [], True: []}
            for i in range(5):
                for gate in ((False, True) if i % 2 == 0 else (True, False)):
                    guard()
                    start = time.perf_counter()
                    consume_queue(facts, volume_gate=gate, identity=identity)
                    times[gate].append(time.perf_counter() - start)
            differences = [b-a for a, b in zip(times[False], times[True], strict=True)]
            return {"pairs": 5, "order": "alternating", "shared_Facts_builds": 0,
                    "baseline_seconds": times[False], "factor_seconds": times[True],
                    "median_factor_minus_baseline_seconds": statistics.median(differences),
                    "scope": "queue_with_identity_checks; shared_Facts_and_factor_already_built",
                    "total_factor_incremental_cost": "NOT_FULLY_ISOLATED",
                    "human_minutes": None}

        save("queue-costs.safe.json", stage("queue_cost_measurement", queue_costs,
                                          "REUSED_SHARED_FACTS_10_INVOCATIONS"))
    receipt = {"schema_version": "quanthus.factor541-run.v1", "purpose": PURPOSE,
               "source_handoff_sha256": budget["source_handoff_sha256"],
               "semantic_closure_digest": digest(budget["code_and_config_members"]),
               "policy_digest": catalog["spec_digest"], "facts_factor_digest": facts_digest,
               "panel_identity_digest": identity_digest,
               "utility_digest": utility_digest,
               "queue_digest": queue_digest, "screening_digest": digest(summary),
               "source_rows": len(admissions), "stages": stages,
               "source_field_times": "preserved; no inferred historical knowledge_cutoff",
               "facts_TMP_build_count": 1, "facts_TMP_consumers": 14 if utility else 3,
               "peak_RSS_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
               "output_bytes_before_receipt": sum(p.stat().st_size for p in output.iterdir()),
               "observed_at": datetime.now(timezone.utc).isoformat(),
               "elapsed_seconds": time.monotonic()-started,
               "new_source_requests": 0, "production_or_P6_admitted": False}
    save("receipt.safe.json", receipt)
    save("READY", {"receipt_sha256": sha_file(output/"receipt.safe.json")})
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("source", "repo", "output", "budget"):
        parser.add_argument("--"+option, required=True, type=Path)
    parser.add_argument("--utility", action="store_true")
    a = parser.parse_args()
    result = run(source=a.source, repo=a.repo, output=a.output, budget_path=a.budget,
                 utility=a.utility)
    print(json.dumps({"status": "COMPLETED_BOUNDED_RESEARCH_QUEUE",
                      "rows": result["source_rows"], "queue_digest": result["queue_digest"],
                      "production_or_P6_admitted": False}, sort_keys=True))


if __name__ == "__main__":
    # Keep one identity class when the optional executor imports this module.
    from quanthus.stage_learning.factor_slice import main as canonical_main
    canonical_main()
