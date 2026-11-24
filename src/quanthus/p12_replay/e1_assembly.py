"""8792 phase-1 E1 web-entry assembly (issue #748 / 8792).

Binds the same-T #749 fixed envelope to the real P11 replay entry for the
browser POST /api/run-day and the replay advance path.  Per the coordinator
ruling the phase-1 real-rule path uses ``E1ReplayRunner`` (the P11 PUBLIC
``run_e1_replay_day`` seam) - never the low-level ``run_e1_from_history``
and never the labelled ``SyntheticRuleRunner``.

``build_e1_entry`` returns ``(runner, history_reader)`` for
``make_engine``:

* ``history_reader(day, decision_cutoff)`` calls the fixed #749
  ``read_history(market_date=day, decision_cutoff=..., holdings, pending,
  source_map)`` and returns the HistoryResult.raw_history envelope
  (p11.raw-history-input.v1).  The same call's coverage / member-read
  evidence is cached so the matching E1ReplayContext binds the actual
  consumed members, composite keys and first-read/verified clocks of this
  run - never a stale diagnostic.
* ``runner`` is an E1ReplayRunner whose E1ReplayContext carries the
  pinned scenario account, the registered rule card, #737 exclusions,
  the signed action coverage and the run-time member evidence the
  history_reader produced for that T.

A missing source, a missing envelope member or a failed assembly leaves a
named history_inputs_unavailable block on the run receipt - the phase-1
entry never fabricates members or falls back to a synthetic package.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.p12_daily.e1_replay import E1ReplayContext, E1ReplayRunner
from quanthus.p12_history.reader import read_history
from quanthus.p12_history.source_pins import SourceMap


def build_e1_entry(
    *,
    repo_root: str | Path,
    scenario: dict,
    action_coverage: dict,
    exclusions: tuple[dict, ...] | list[dict] = (),
    source_map: SourceMap | None = None,
    holdings=(),  # tuple of holding rows OR callable(day) -> rows
    pending=(),   # tuple of pending rows OR callable(day) -> rows
    open_sessions: tuple[str, ...] | list[str] = (),
    runner_ref_id: str = "p11.e1-replay-day",
    runner_version: str = "1.0.0",
) -> tuple[E1ReplayRunner, Callable[[date, datetime], dict]]:
    """Assemble the E1 runner + the per-T #749 history_reader.

    ``scenario`` carries the pinned scenario binding, e.g.::

        {
          "scenario_id": "spdb-20261008",
          "account_namespace": "historical-replay:spdb-20261008",
          "package_id": "replay-core-2026-10-08",
          "manifest_sha256": "sha256:...",
          "account": {...ReplayRequest.account mapping...},
          "policy": {...},
          "facts_template": {...},
          "snapshot_id": "...",
          "snapshot_manifest_digest": "sha256:...",
          "p12_evidence": {...},
        }

    The history_reader records each T's actual member-consumption and read
    clocks on the runner's per-T context, so the ReplayRequest's
    member_evidence / required_member_ids are the members THIS run really
    opened and sha-verified.
    """
    from quanthus.product_daily.recommendation.e1_runtime import (
        load_e1_rule_assets,
    )

    assets = load_e1_rule_assets(repo_root)
    smap = source_map or SourceMap()

    # The per-T context is rebuilt inside history_reader once the real
    # member_consumption for that T is known; ``state`` carries the runner
    # between calls so the context the run sees is the same T's evidence.
    state: dict[str, Any] = {"context": None, "result": None}

    def history_reader(day: date, decision_cutoff: datetime) -> dict:
        day_holdings = holdings(day) if callable(holdings) else holdings
        day_pending = pending(day) if callable(pending) else pending
        result = read_history(
            day,
            decision_cutoff,
            holdings=day_holdings,
            pending=day_pending,
            source_map=smap,
        )
        state["result"] = result
        mc = (result.coverage or {}).get("member_consumption") or {}
        members = mc.get("members") or []
        member_evidence = tuple(
            {
                "member_id": m.get("member_id"),
                "package_key": m.get("package_key"),
                "version": m.get("version"),
                "digest": m.get("sha256"),
                "verified_at": m.get("verify_at"),
                "required": bool(m.get("relevant_for_T")),
            }
            for m in members
            if isinstance(m, dict)
        )
        required_member_ids = tuple(
            str(m.get("member_id"))
            for m in members
            if isinstance(m, dict) and m.get("relevant_for_T")
        )
        state["context"] = E1ReplayContext(
            scenario_id=scenario["scenario_id"],
            market_dates=(day.isoformat(),),
            open_sessions=tuple(open_sessions),
            package_id=scenario["package_id"],
            manifest_sha256=scenario["manifest_sha256"],
            account=scenario["account"],
            policy=scenario["policy"],
            facts_template=scenario["facts_template"],
            snapshot_id=scenario["snapshot_id"],
            snapshot_manifest_digest=scenario["snapshot_manifest_digest"],
            exclusions=tuple(exclusions),
            member_evidence=member_evidence,
            required_member_ids=required_member_ids,
            action_coverage=action_coverage,
            p12_evidence=scenario.get("p12_evidence"),
        )
        return result.raw_history

    def context_supplier() -> E1ReplayContext | None:
        return state["context"]

    runner = E1ReplayRunner(
        runner_ref_id=runner_ref_id,
        runner_version=runner_version,
        assets=assets,
        # The context is bound per-T inside history_reader; until the first
        # read it is None so an assembly-incomplete run blocks honestly.
        context=None,
    )
    # Late-bind: the runner reads the same T's context at run() time.
    object.__setattr__(runner, "_context_supplier", context_supplier)
    return runner, history_reader




def load_e1_scenario(
    path: str | Path,
    *,
    repo_root: str | Path,
) -> tuple[E1ReplayRunner, Callable[[date, datetime], dict]]:
    """Load a frozen E1 scenario JSON and assemble runner + history_reader.

    The scenario file is the integration-time binding the coordinator
    freezes for one replay (scenario identity, signed action_coverage wire,
    exclusions, optional SourceMap member-map overrides).  Any missing
    file, unreadable JSON or absent required field raises ValueError - the
    CLI turns that into a named e1_assembly_unavailable block, never a
    silent fall back to the synthetic path.

    Shape::

        {
          "schema_version": "p12.e1-scenario.v1",
          "scenario": {...E1ReplayContext binding...},
          "action_coverage": {...signed wire...} | {"path": "..."},
          "exclusions": [...],
          "source_map": {
            "base_members_path": "...",   # optional overrides
            "daily_members_path": "...",
            "base_package_root": "...",
            "calendar_members_path": "..."
          },
          "open_sessions": ["2026-10-09", ...]
        }
    """
    doc = json.loads(Path(path).read_text())
    if doc.get("schema_version") != "p12.e1-scenario.v1":
        raise ValueError(
            "e1 scenario schema_version must be p12.e1-scenario.v1, got "
            + repr(doc.get("schema_version"))
        )
    scenario = doc.get("scenario")
    if not isinstance(scenario, dict):
        raise ValueError("e1 scenario requires a scenario object")
    for key in (
        "scenario_id",
        "package_id",
        "manifest_sha256",
        "account",
        "policy",
        "facts_template",
        "snapshot_id",
        "snapshot_manifest_digest",
    ):
        if scenario.get(key) is None:
            raise ValueError(f"e1 scenario.scenario missing {key}")

    coverage = doc.get("action_coverage")
    if isinstance(coverage, dict) and set(coverage) == {"path"}:
        coverage = json.loads(Path(coverage["path"]).read_text())
    if coverage is None:
        raise ValueError("e1 scenario requires action_coverage")

    smap_doc = doc.get("source_map") or {}
    smap = None
    if smap_doc:
        kwargs = {}
        for name in (
            "base_members_path",
            "daily_members_path",
            "base_package_root",
            "calendar_members_path",
        ):
            if smap_doc.get(name):
                kwargs[name] = Path(smap_doc[name])
        smap = SourceMap(**kwargs)

    exclusions = doc.get("exclusions") or ()
    if isinstance(exclusions, dict) and set(exclusions) == {"path"}:
        exclusions = tuple(json.loads(Path(exclusions["path"]).read_text()))

    return build_e1_entry(
        repo_root=repo_root,
        scenario=scenario,
        action_coverage=coverage,
        exclusions=tuple(exclusions),
        source_map=smap,
        holdings=doc.get("holdings") or (),
        pending=doc.get("pending") or (),
        open_sessions=tuple(doc.get("open_sessions") or ()),
    )

__all__ = ["build_e1_entry", "load_e1_scenario"]
