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
import hashlib
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.event_library.contracts import canonical_json_bytes, sha256_digest
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
    evidence_dir: str | Path | None = None,
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
    # between calls.  Contexts are keyed by market_date so the runner's
    # context supplier binds THIS run's same-T evidence, never the
    # last-read context a following request may already have replaced.
    state: dict[str, Any] = {"contexts": {}, "result": None}
    ev_dir = Path(evidence_dir) if evidence_dir is not None else None

    def _write_artifact(name: str, payload: Any) -> tuple[str, str]:
        """Persist THIS run's input artefact as a standalone recomputable
        file and return (uri, sha256).  The bytes are the canonical JSON so
        sha256(file) == the declared digest - the auditor recomputes the
        binding from the file alone, never from a frozen sibling's digest."""
        data = canonical_json_bytes(payload)
        if ev_dir is None:
            return "mem://" + name, hashlib.sha256(data).hexdigest()
        ev_dir.mkdir(parents=True, exist_ok=True)
        path = ev_dir / name
        path.write_bytes(data)
        return str(path), hashlib.sha256(data).hexdigest()

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
        # P11 _october_package_clock (b7dca041) binds each member_evidence to
        # THIS run's consumption receipt: available_at is the member's own
        # verify_at (the wall-clock instant its sha256 finished re-hashing
        # inside this read) and must sit inside [source_read_at, observed_at].
        # The envelope's supplier-declared visible_at is a source clock,
        # never this run's read/verify clock - using it both breaks the
        # same-run binding and is refused by the package-clock rule.  The
        # request-level projection converts verify_at's trailing 'Z' to the
        # equivalent '+00:00' offset so the pre-db240043 loader's bare
        # fromisoformat accepts it; the envelope and the consumption receipt
        # keep their original bytes.
        def _iso_offset(value):
            if isinstance(value, str) and value.endswith("Z"):
                return value[:-1] + "+00:00"
            return value

        verify_by_member = {
            m.get("member_id"): m.get("verify_at")
            for m in members
            if isinstance(m, dict)
        }
        member_evidence = tuple(
            {
                "member_id": m.get("member_id"),
                "package_key": m.get("package_key"),
                "version": m.get("version"),
                "digest": m.get("sha256"),
                "available_at": _iso_offset(
                    verify_by_member.get(m.get("member_id"))
                ),
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
        account = dict(scenario["account"])
        facts_template = dict(scenario["facts_template"])
        # The same Z->+00:00 projection applies to the account as_of /
        # as_of_overrides and facts_template intake clocks the pre-db240043
        # P11 loader reads with a bare fromisoformat.
        for key in ("as_of",):
            if isinstance(account.get(key), str) and account[key].endswith("Z"):
                account[key] = account[key][:-1] + "+00:00"
        ov = account.get("as_of_overrides")
        if isinstance(ov, dict):
            account["as_of_overrides"] = {
                k: (v[:-1] + "+00:00" if isinstance(v, str) and v.endswith("Z") else v)
                for k, v in ov.items()
            }
        for key in (
            "intake_received_at",
            "intake_first_read_at",
            "intake_verified_at",
        ):
            v = facts_template.get(key)
            if isinstance(v, str) and v.endswith("Z"):
                facts_template[key] = v[:-1] + "+00:00"
        # Rebind the intake clocks to THIS run's read/verify window: the
        # package-clock rule requires
        #   received <= first_read == run_read_at <= verified == max(verify_at)
        # so the static scenario receipt's frozen 11-24 instants are replaced
        # by this run's actual member-consumption clocks.  The sealed 163-
        # member v6 receipt stays the source artefact; its read_at is kept on
        # p12_evidence.envelope/source_clocks, never on facts_template.
        consumed_verify = sorted(
            v for v in verify_by_member.values() if isinstance(v, str)
        )
        run_read_at = mc.get("run_read_at")
        last_verify = consumed_verify[-1] if consumed_verify else run_read_at
        if isinstance(run_read_at, str) and isinstance(last_verify, str):
            facts_template["intake_received_at"] = run_read_at.replace(
                "Z", "+00:00"
            )
            facts_template["intake_first_read_at"] = run_read_at.replace(
                "Z", "+00:00"
            )
            facts_template["intake_verified_at"] = last_verify.replace(
                "Z", "+00:00"
            )
        # The snapshot_manifest_digest binds THIS run's produced raw_history
        # envelope: the P11 package-clock compares it to p12_evidence's
        # declared envelope sha256.  It is recomputed over the actual bytes
        # of this run's envelope - never the frozen v6 file digest and never
        # the scenario's static snapshot digest.
        envelope_digest = sha256_digest(result.raw_history)
        # Persist THIS run's actual input artefacts as standalone
        # recomputable files (canonical JSON, so sha256(file) equals the
        # declared digest), then bind p12_evidence's artefact refs to
        # those new files - never to the frozen sealed-envelope uri.
        # The sealed v6 artefact/read/coverage refs move under a
        # sealed_source provenance block so the original source bytes
        # stay auditable separately from this run's produced input.
        env_uri, env_sha = _write_artifact(
            "e1-envelope-" + day.isoformat() + ".json", result.raw_history
        )
        cov_uri, cov_sha = _write_artifact(
            "e1-coverage-" + day.isoformat() + ".json", result.coverage
        )
        read_receipt_payload = {
            "schema_version": "p12.e1-read-receipt.v1",
            "market_date": day.isoformat(),
            "run_read_at": run_read_at,
            "member_consumption": mc,
            "envelope_uri": env_uri,
            "envelope_sha256": env_sha,
        }
        read_uri, read_sha = _write_artifact(
            "e1-read-receipt-" + day.isoformat() + ".json", read_receipt_payload
        )
        base_evidence = scenario.get("p12_evidence") or {}
        p12_evidence = dict(base_evidence)
        p12_evidence["member_consumption"] = mc
        # The artefact refs bind THIS run's produced input bytes; the sealed
        # source artefact/read/coverage refs are preserved verbatim under
        # sealed_source as provenance, listed separately from the live refs.
        p12_evidence["sealed_source"] = {
            "envelope": base_evidence.get("envelope"),
            "read_receipt": base_evidence.get("read_receipt"),
            "coverage_receipt": base_evidence.get("coverage_receipt"),
            "note": (
                "Frozen #749 source artefacts this read consumed. The live "
                "envelope/read_receipt/coverage_receipt above point to this "
                "run's recomputable canonical-JSON artefacts, not to the "
                "sealed source files."
            ),
        }
        p12_evidence["envelope"] = {
            "uri": env_uri,
            "sha256": env_sha,
        }
        p12_evidence["read_receipt"] = {"uri": read_uri, "sha256": read_sha}
        p12_evidence["coverage_receipt"] = {"uri": cov_uri, "sha256": cov_sha}
        p12_evidence["read_at"] = run_read_at
        p12_evidence["source_clocks"] = {
            **(base_evidence.get("source_clocks") or {}),
            "run_read_at": run_read_at,
            "logical_decision_cutoff": decision_cutoff.isoformat(),
        }
        p12_evidence["coverage"] = result.coverage

        state["contexts"][day.isoformat()] = E1ReplayContext(
            scenario_id=scenario["scenario_id"],
            market_dates=(day.isoformat(),),
            open_sessions=tuple(open_sessions),
            package_id=scenario["package_id"],
            manifest_sha256=scenario["manifest_sha256"],
            account=account,
            policy=scenario["policy"],
            facts_template=facts_template,
            snapshot_id=scenario["snapshot_id"],
            snapshot_manifest_digest=envelope_digest,
            exclusions=tuple(exclusions),
            member_evidence=member_evidence,
            required_member_ids=required_member_ids,
            action_coverage=action_coverage,
            p12_evidence=p12_evidence,
        )
        return result.raw_history

    def context_supplier(day: date) -> E1ReplayContext | None:
        return state["contexts"].get(day.isoformat())

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
    evidence_dir: str | Path | None = None,
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
        evidence_dir=evidence_dir,
    )

__all__ = ["build_e1_entry", "load_e1_scenario"]
