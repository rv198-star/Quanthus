"""Source-bound retrospective supplement, preserving every original P3/P2 gate.

Only exact separately qualified late observations use the retrospective reader.
The legacy reader keeps its historical-PIT validation. Healthy feature rows may
be reused only after proving complete input equivalence outside affected IDs and
exact raw-candidate equality before global concentration. No new candidate search.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time as clock
from collections import Counter
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from itertools import groupby
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from quanthus.backtest.baseline_research import (
    _create_feature_source_views,
    _feature_panel_query,
    _validate_feature_sources,
)
from quanthus.backtest.contracts import MARKET_TIMEZONE, CorporateAction, CorporateActionStep
from quanthus.backtest.engine import BacktestInputError
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_historical_bonus_successor as bonus
from quanthus.expert_strategy import p3_historical_rights_successor as prior
from quanthus.expert_strategy import p3_historical_robustness as h
from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime
from quanthus.pipelines.securities_warehouse import p10_historical_action_input as p10
from quanthus.pipelines.securities_warehouse.p10_historical_action_source import ActionFacts

VERSION = "p3-historical-official-action-successor-v1"
REQUEST = "config/research/p3-strat-003/historical-official-action-successor.v1.json"
MEMBERS = {
    "src/quanthus/expert_strategy/p3_historical_action_successor.py",
    "src/quanthus/pipelines/securities_warehouse/p10_historical_action_input.py",
    "src/quanthus/pipelines/securities_warehouse/p10_historical_action_source.py",
    "src/quanthus/expert_strategy/p3_historical_bonus_successor.py",
    "src/quanthus/expert_strategy/p3_historical_point_panel.py",
}


def load_request(repository: Path) -> dict[str, Any]:
    old = prior.load_request(repository)
    r: dict[str, Any] = json.loads((repository / REQUEST).read_bytes())
    if (
        r.get("schema_version") != VERSION
        or r.get("parent_request_sha256") != prior.native._sha(repository / prior.REQUEST)
        or r.get("reference_model") != p10.MODEL
        or r.get("optional_rights_policy") != old["optional_rights_policy"]
        or r.get("optional_rights_policy_sha256") != old["optional_rights_policy_sha256"]
        or set(r.get("code_config_members", {})) != MEMBERS
        or any(
            r.get(k) != old[k]
            for k in (
                "scope",
                "arms",
                "execution_order",
                "time_blocks",
                "annual_diagnostics",
                "analysis",
                "input_preparation_request_digest",
            )
        )
        or any(
            r.get(k) is not False
            for k in (
                "historical_PIT_claim",
                "production_authorized",
                "P6_mutation_authorized",
                "original_B_accepted",
                "original_D_activated",
                "parameter_search",
                "source_guards_waived",
            )
        )
        or r.get("maximum_additional_wall_seconds") != 7200
        or r.get("maximum_seconds_per_role") != 7200
        or r.get("previous_failures_costs_and_deadlines_retained") is not True
    ):
        raise ValueError("ACTION_SUCCESSOR_REQUEST")
    for name, expected in r["code_config_members"].items():
        if prior.native._sha(prior.native.physical(repository / name)) != expected:
            raise ValueError("ACTION_SUCCESSOR_CODE_CHANGED")
    return r


def _action_rows(facts: tuple[ActionFacts, ...], snapshot: str) -> list[dict[str, Any]]:
    return [
        {
            "security_id": f.security_code,
            "security_code": f.security_code,
            "action_id": "official-action:" + f.evidence_sha256,
            "effective_date": f.ex_date,
            "known_at": f.acquired_at,
            "cash_dividend_per_share": f.cash_per_share,
            "bonus_share_ratio": f.bonus_share_ratio,
            "source_revision": f.source_revision,
            "source_snapshot_id": snapshot,
            "evidence_digest": "sha256:" + f.evidence_sha256,
        }
        for f in facts
    ]


class SupplementedReader(h.BoundedHistoricalReader):
    """Original validation first; exact late-source family validated separately."""

    def __init__(self, bundle: Any, facts: tuple[ActionFacts, ...], cutoff: datetime) -> None:
        self.facts = {(f.security_code, f.ex_date): f for f in facts}
        if len(self.facts) != len(facts) or any(f.acquired_at > cutoff for f in facts):
            raise ValueError("ACTION_READER_SOURCE_CLOCK")
        self.cutoff = cutoff
        super().__init__(bundle)
        try:
            self._install_late_actions(bundle, facts)
        except BaseException:
            self.close()
            raise

    def _install_late_actions(self, bundle: Any, facts: tuple[ActionFacts, ...]) -> None:
        c = self._ensure_open()
        import pyarrow as pa

        rows = _action_rows(facts, bundle.manifest_sha256)
        c.register("qualified_late_action_arrow", pa.Table.from_pylist(rows))
        c.execute(
            "CREATE TEMP TABLE qualified_late_actions AS SELECT * FROM qualified_late_action_arrow"
        )
        c.unregister("qualified_late_action_arrow")
        for f in facts:
            if self._scalar(
                "SELECT count(*) FROM replay_actions_mapped "
                "WHERE security_id=? AND effective_date=?",
                (f.security_code, f.ex_date),
            ):
                raise ValueError("ACTION_READER_CONFLICTING_EXISTING_ACTION")
            valid = self._scalar(
                "SELECT count(*) FROM replay_validity WHERE security_id=? "
                "AND CAST(valid_from AS DATE)<=? "
                "AND (valid_to IS NULL OR CAST(valid_to AS DATE)>=?)",
                (f.security_code, f.record_date, f.ex_date),
            )
            sessions = self._scalar(
                "SELECT count(*) FROM replay_calendar c JOIN replay_universe u "
                "ON c.exchange=u.exchange AND c.trade_date=u.market_date "
                "WHERE u.security_id=? AND u.market_date IN (?,?) AND c.is_open=1",
                (f.security_code, f.record_date, f.ex_date),
            )
            if (
                valid != 1
                or sessions != 2
                or f.cash_payment_date != f.ex_date
                or (
                    f.bonus_share_ratio
                    and (f.share_credit_date != f.ex_date or f.share_listing_date != f.ex_date)
                )
            ):
                raise ValueError("ACTION_READER_IDENTITY_CALENDAR_OR_CREDIT")
        columns = (
            "security_id,security_code,action_id,effective_date,known_at,"
            "cash_dividend_per_share,bonus_share_ratio,source_revision,"
            "source_snapshot_id,evidence_digest"
        )
        c.execute(
            f"CREATE OR REPLACE VIEW replay_actions_mapped AS SELECT {columns} "
            f"FROM replay_actions UNION ALL SELECT {columns} FROM qualified_late_actions"
        )
        # Original unexplained guards remain. Recompute only exact new keys;
        # the corrected reference alone is never sufficient action evidence.
        for f in facts:
            row = self._rows(
                """SELECT b.close,
                (SELECT close FROM replay_bars p WHERE p.security_id=b.security_id
                 AND p.date<b.date AND p.close>0 ORDER BY p.date DESC LIMIT 1) AS prior_close,
                l.upper_limit_pct,l.lower_limit_pct FROM replay_bars b JOIN replay_limits l
                ON l.security_id=b.security_id AND l.execution_date=b.date
                WHERE b.security_id=? AND b.date=?""",
                (f.security_code, f.ex_date),
            )
            if len(row) != 1 or row[0]["prior_close"] is None:
                raise ValueError("ACTION_READER_REFERENCE_MISSING")
            r = row[0]
            gross = (
                Decimal(str(r["close"])) * (1 + f.bonus_share_ratio) + f.cash_per_share
            ) / Decimal(str(r["prior_close"]))
            if (
                not 1 - Decimal(str(r["lower_limit_pct"])) / 100 - Decimal("0.005")
                <= gross
                <= 1 + Decimal(str(r["upper_limit_pct"])) / 100 + Decimal("0.005")
            ):
                raise ValueError("ACTION_READER_RESIDUAL_TRANSITION")

    def _corporate_actions(
        self, security_code: str, execution_date: date, prior_mark_price: Decimal
    ) -> Any:
        fact = self.facts.get((security_code, execution_date))
        if fact is None:
            return super()._corporate_actions(security_code, execution_date, prior_mark_price)
        if self._block_reason(security_code, execution_date) is not None:
            raise ValueError("ACTION_READER_EXISTING_BLOCK_RETAINED")
        if fact.acquired_at > self.cutoff:
            raise ValueError("ACTION_READER_SOURCE_CLOCK")
        mark = ((prior_mark_price - fact.cash_per_share) / (1 + fact.bonus_share_ratio)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if mark <= 0:
            raise ValueError("ACTION_READER_NONPOSITIVE_MARK")
        aid = "official-action:" + fact.evidence_sha256
        action = CorporateAction(
            aid,
            security_code,
            execution_date,
            fact.acquired_at,
            fact.cash_per_share,
            fact.bonus_share_ratio,
            fact.source_revision + "|evidence=sha256:" + fact.evidence_sha256,
            self._ads_bundle.manifest_sha256,
        )
        self._record_access("runner_only", "qualified_late_actions")
        return (
            CorporateActionStep(
                action,
                datetime.combine(execution_date, time(9), tzinfo=MARKET_TIMEZONE),
                mark,
                self._snapshot(execution_date, role="execution").snapshot_id + ".action." + aid,
            ),
        )


class SupplementedEngine(bonus.HistoricalBonusEngine):
    def __init__(
        self, policy: Any, cutoff: datetime, snapshot: str, actions: dict[str, CorporateAction]
    ) -> None:
        super().__init__(policy, cutoff, snapshot, h.ACTION_SOURCE_SNAPSHOT)
        self.added_actions = actions

    def _validate_action_clock(
        self, run: Any, action: CorporateAction, applied_at: datetime
    ) -> None:
        expected = self.added_actions.get(action.action_id)
        if expected is None:
            super()._validate_action_clock(run, action, applied_at)
            return
        if (
            type(action) is not CorporateAction
            or action != expected
            or action.known_at > self.cutoff
            or run.official
            or not run.code_version.startswith(h.VERSION + ":")
            or run.data_snapshot_id not in {self.snapshot, expected.snapshot_id}
            or applied_at
            != datetime.combine(action.effective_date, time(9), tzinfo=MARKET_TIMEZONE)
        ):
            raise BacktestInputError("ACTION_SUCCESSOR_EXACT_ACTION_BINDING")


class HistoricalActionRuntime(bonus.HistoricalBonusRuntime):
    engine: Any

    def __init__(
        self, repository: Path, source: Path, prepared: Path, admission: dict[str, Any]
    ) -> None:
        self.historical_request = load_request(repository)
        self.request_digest = digest_value(self.historical_request)
        self.execution_request = self.historical_request
        self.execution_digest = self.request_digest
        self.effective_source_sha = self.historical_request["source_manifest_sha256"]
        if (
            admission.get("request_digest") != self.request_digest
            or admission.get("execution_digest") != self.execution_digest
            or admission.get("role") not in {"primary", "independent_cold_reproduction"}
            or admission.get("source_manifest_sha256") != self.effective_source_sha
            or admission.get("preparation_sha256")
            != prior.native._sha(prepared / "preparation.json")
            or admission.get("feature_sha256") != prior.native._sha(prepared / "features.parquet")
        ):
            raise ValueError("ACTION_SUCCESSOR_EXACT_ADMISSION")
        self.bundle, self.official_facts = p10.load_input(
            source, self.effective_source_sha.removeprefix("sha256:")
        )
        self.prepared = prepared
        self.preparation = h.read_preparation(prepared / "preparation.json")
        if (
            self.preparation.get("action_successor_request_digest") != self.request_digest
            or self.preparation.get("effective_source_manifest_sha256") != self.effective_source_sha
            or self.preparation.get("source_information_cutoff")
            != self.historical_request["source_information_cutoff"]
        ):
            raise ValueError("ACTION_SUCCESSOR_PREREQUISITE")
        self.prerequisite_digest = digest_value(
            {k: v for k, v in self.preparation.items() if k != "feature_disposition"}
        )
        self.snapshot_id = VERSION + "/" + self.request_digest.removeprefix("sha256:")
        self.info = datetime.fromisoformat(self.historical_request["source_information_cutoff"])
        if self.info != self.bundle.historical_bundle.snapshot.knowledge_cutoff:
            raise ValueError("ACTION_SUCCESSOR_SOURCE_CLOCK")
        # Retain the already qualified optional-rights disposition unchanged.
        original_facts = prior.load_rights_source(
            source / "parent/evidence/issuer", prior.p10.ISSUER_SHA
        )
        self.rights_dispositions = [
            prior.no_subscription_disposition(
                repository,
                original_facts,
                arm=arm,
                market_date=original_facts.ex_right_date,
                source_information_cutoff=self.info,
            )
            for arm in h.ARMS
        ]
        self.reader = SupplementedReader(self.bundle, self.official_facts, self.info)
        self.entry_runners: dict[str, Any] = {}
        try:
            FormalP2ReplayRuntime.__init__(
                self,
                repository,
                decision_start=date(2012, 1, 4),
                decision_end=date(2021, 12, 30),
                execution_end=date(2021, 12, 31),
                parent_event_run_digest=None,
                provider_config=VERSION,
            )
            self.stack.callback(self.reader.close)
            added = {
                "official-action:" + f.evidence_sha256: CorporateAction(
                    "official-action:" + f.evidence_sha256,
                    f.security_code,
                    f.ex_date,
                    f.acquired_at,
                    f.cash_per_share,
                    f.bonus_share_ratio,
                    f.source_revision + "|evidence=sha256:" + f.evidence_sha256,
                    self.effective_source_sha,
                )
                for f in self.official_facts
            }
            self.engine = SupplementedEngine(self.engine.policy, self.info, self.snapshot_id, added)
        except BaseException:
            self.reader.close()
            if hasattr(self, "stack"):
                self.stack.close()
            raise

    def _code_version(self) -> str:
        return f"{super()._code_version()}:{VERSION}:{self.request_digest}"

    def _manifest_metadata(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            **super()._manifest_metadata(intervention_id),
            "purpose": VERSION,
            "execution_adapter": VERSION,
            "official_action_supplement": True,
            "historical_PIT_claim": False,
            "exchange_historical_rule_certification": False,
            "reference_model": p10.MODEL,
        }


def prepare(
    repository: Path, source: Path, old_prepared: Path, output: Path, request: dict[str, Any]
) -> dict[str, Any]:
    """Bounded rebuild of affected IDs, with candidate-equivalence proof first."""
    import duckdb

    if request != load_request(repository) or output.exists() or output.is_symlink():
        raise ValueError("ACTION_PREPARATION_REQUEST_OR_OUTPUT")
    if (
        prior.native._sha(old_prepared / "preparation.json") != request["prior_preparation_sha256"]
        or prior.native._sha(old_prepared / "features.parquet") != request["prior_feature_sha256"]
    ):
        raise ValueError("ACTION_PREPARATION_PARENT_IDENTITY")
    bundle, facts = p10.load_input(
        source, request["source_manifest_sha256"].removeprefix("sha256:")
    )
    old = h.read_preparation(old_prepared / "preparation.json")
    cutoff = datetime.fromisoformat(request["source_information_cutoff"])
    selected = tuple(sorted({f.security_code for f in facts}))
    output.mkdir()
    (output / "spill").mkdir()
    c = duckdb.connect(
        config={
            "memory_limit": "4GB",
            "threads": 2,
            "max_temp_directory_size": "6GB",
            "temp_directory": str(output / "spill"),
        }
    )
    c.execute("SET timezone='UTC'")
    try:
        paths = {
            d.dataset: bundle.historical_bundle.partition_paths(d.dataset)
            for d in bundle.historical_bundle.datasets
        }
        feature_names = {
            "daily_ohlcv_none",
            "daily_universe",
            "price_limit_execution_daily",
            "security_code_validity",
            "corporate_actions_normalized",
            "corporate_action_exceptions",
        }
        _create_feature_source_views(
            c,
            {k: v for k, v in paths.items() if k in feature_names},
            selected,
            scope_end=date(2021, 12, 31),
        )
        for name in ("corporate_actions_normalized", "corporate_action_exceptions"):
            c.execute(f"CREATE VIEW source_{name}_mapped AS SELECT * FROM source_{name}")
        _validate_feature_sources(c)
        # New observed-time rows never pass through the legacy PIT validator.
        # They are separately source-qualified and selected only by this cutoff.
        import pyarrow as pa

        c.register(
            "new_action_arrow", pa.Table.from_pylist(_action_rows(facts, bundle.manifest_sha256))
        )
        c.execute("CREATE TEMP TABLE new_actions AS SELECT * FROM new_action_arrow")
        c.unregister("new_action_arrow")
        with SupplementedReader(bundle, facts, cutoff) as reader:
            mapping = reader.load_industry_mapping(
                expected_version="static-level1-concentration-research-v1"
            )
        dates = tuple(
            r[0]
            for r in c.execute(
                "SELECT trade_date FROM read_parquet(?) WHERE exchange IN ('SSE','SZSE') "
                "AND is_open=1 GROUP BY trade_date HAVING count(DISTINCT exchange)=2 "
                "ORDER BY trade_date",
                [str(paths["trading_calendar"][0])],
            ).fetchall()
        )
        c.execute(
            "CREATE TEMP TABLE selected_universe_sessions AS SELECT security_id,market_date,"
            "row_number() OVER(PARTITION BY security_id ORDER BY market_date) "
            "AS listing_sessions FROM source_daily_universe"
        )
        before = _raw_candidates(
            c, mapping, old["source_information_cutoff"], repository, dates=dates, added=False
        )
        after = _raw_candidates(
            c, mapping, old["source_information_cutoff"], repository, dates=dates, added=True
        )
        raw_candidates_equal = before == after
        c.execute(
            "CREATE OR REPLACE VIEW source_corporate_actions_normalized_mapped AS "
            "SELECT security_id,effective_date,known_at,cash_dividend_per_share,bonus_share_ratio "
            "FROM source_corporate_actions_normalized UNION ALL SELECT security_id,"
            "effective_date,known_at,cash_dividend_per_share,bonus_share_ratio FROM new_actions"
        )
        query = _feature_panel_query(
            date(2011, 1, 4),
            date(2021, 12, 31),
            output_order="security-date",
            continuity_asof_each_row=True,
            retrospective_action_cutoff=cutoff,
        )
        c.execute("CREATE TEMP TABLE rebuilt_features AS " + query)
        literals = ",".join("'" + s + "'" for s in selected)
        original = str(old_prepared / "features.parquet").replace("'", "''")
        target = str(output / "features.parquet").replace("'", "''")
        c.execute(
            f"COPY (SELECT * FROM (SELECT * FROM read_parquet('{original}') "
            f"WHERE security_id NOT IN ({literals}) UNION ALL SELECT * FROM rebuilt_features) "
            f"ORDER BY security_id,market_date) TO '{target}' "
            "(FORMAT PARQUET,COMPRESSION ZSTD,ROW_GROUP_SIZE 50000)"
        )
        # Full physical scan: healthy rows are exactly the role's own prior rows.
        delta = c.execute(
            f"SELECT count(*) FROM ((SELECT * FROM read_parquet('{target}') "
            f"WHERE security_id NOT IN ({literals}) EXCEPT ALL "
            f"SELECT * FROM read_parquet('{original}') WHERE security_id NOT IN ({literals})) "
            f"UNION ALL (SELECT * FROM read_parquet('{original}') "
            f"WHERE security_id NOT IN ({literals}) EXCEPT ALL "
            f"SELECT * FROM read_parquet('{target}') WHERE security_id NOT IN ({literals})))"
        ).fetchone()[0]
        if delta:
            raise ValueError("ACTION_PREPARATION_HEALTHY_ROW_CHANGED")
        census = c.execute(
            "SELECT count(*),count(*) FILTER(WHERE feature_ready),"
            "count(*) FILTER(WHERE entry_eligible),count(*) FILTER(WHERE exception_active),"
            "count(*) FILTER(WHERE unexplained_price_transition),"
            "count(DISTINCT security_id) FILTER(WHERE scope_continuity_excluded) "
            f"FROM read_parquet('{target}')"
        ).fetchone()
        report = dict(old["feature_report"])
        for k, v in zip(
            (
                "row_count",
                "feature_ready_count",
                "entry_eligible_count",
                "exception_active_count",
                "unexplained_transition_count",
                "continuity_excluded_security_count",
            ),
            census,
            strict=True,
        ):
            report[k] = v
        if census[0] != old["feature_report"]["row_count"]:
            raise ValueError("ACTION_PREPARATION_ROW_COUNT")
        report.update(
            data_content_digest=bundle.bundle_content_digest,
            data_manifest_sha256=bundle.manifest_sha256,
            bundle_snapshot_id=bundle.historical_bundle.snapshot.snapshot_id,
            bundle_digest=bundle.bundle_content_digest,
        )
        population = {}
        if not raw_candidates_equal:
            # The complete original raw population is not present in the retained
            # preparation. Rebuild all securities and perform the global retain
            # exactly once; never merge only old retained + repaired candidates.
            c.close()
            c = duckdb.connect(
                config={
                    "memory_limit": "4GB",
                    "threads": 2,
                    "max_temp_directory_size": "6GB",
                    "temp_directory": str(output / "spill"),
                }
            )
            c.execute("SET timezone='UTC'")
            _create_feature_source_views(
                c,
                {k: v for k, v in paths.items() if k in feature_names},
                None,
                scope_end=date(2021, 12, 31),
            )
            for name in ("corporate_actions_normalized", "corporate_action_exceptions"):
                c.execute(f"CREATE VIEW source_{name}_mapped AS SELECT * FROM source_{name}")
            _validate_feature_sources(c)
            population = _parallel_population(
                c, mapping, repository, source, request, dates=dates, output=output
            )
        proof = {
            "affected_security_ids": list(selected),
            "raw_candidates_equal_before_global_selection": raw_candidates_equal,
            "affected_raw_candidate_count": len(before),
            "unchanged_feature_rows_exact_difference": delta,
            "original_candidate_coordinates_retained": raw_candidates_equal,
            "full_global_candidate_rebuild": not raw_candidates_equal,
            "own_prior_preparation_sha256": prior.native._sha(old_prepared / "preparation.json"),
            "own_prior_feature_sha256": prior.native._sha(old_prepared / "features.parquet"),
            "qualified_source_manifest_sha256": bundle.manifest_sha256,
        }
        result = {
            **old,
            **population,
            "feature_report": report,
            "feature_identity": digest_value(
                {"source": bundle.manifest_sha256, "query": query, "proof": proof}
            ),
            "feature_disposition": "INDEPENDENT_AFFECTED_ID_REBUILD_OWN_HEALTHY_ROWS_REUSED",
            "action_successor_request_digest": digest_value(request),
            "effective_source_manifest_sha256": bundle.manifest_sha256,
            "source_information_cutoff": request["source_information_cutoff"],
            "incremental_proof": proof,
        }
        (output / "preparation.json").write_text(
            json.dumps(result, sort_keys=True, indent=2) + "\n"
        )
        return result
    finally:
        c.close()


def _raw_candidates(
    c: Any, mapping: Any, cutoff: str, repository: Path, *, dates: tuple[date, ...], added: bool
) -> list[Any]:
    return _scan_raw_candidates(c, mapping, cutoff, repository, dates=dates, added=added)[0]


def _rebuild_population(
    c: Any, mapping: Any, cutoff: str, repository: Path, *, dates: tuple[date, ...], output: Path
) -> dict[str, Any]:
    raw, reasons, evaluated, series_count, decoded = _scan_raw_candidates(
        c, mapping, cutoff, repository, dates=dates, added=True
    )
    return _finalize_population(
        raw,
        reasons,
        evaluated,
        series_count,
        decoded,
        mapping,
        repository,
        dates=dates,
        output=output,
    )


def _finalize_population(
    raw: list[Any],
    reasons: Counter[str],
    evaluated: int,
    series_count: int,
    decoded: int,
    mapping: Any,
    repository: Path,
    *,
    dates: tuple[date, ...],
    output: Path,
) -> dict[str, Any]:
    from quanthus.event_library.scan import CandidateScanShard

    # One immutable, independently built job-local raw census per role. It is
    # not a cross-job cache or ADM, and is bound by the preparation file hash.
    shard = CandidateScanShard(
        tuple(raw),
        tuple(sorted(mapping.by_security_id)),
        dict(reasons),
        evaluated,
        series_count,
        decoded,
        0.0,
        0.0,
    )
    (output / "raw-candidate-census.json").write_text(
        json.dumps(shard.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
    )
    card = h.load_candidate_rule_card(
        repository / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    kept, raw_count, isolated, gaps = h._retain_raw_candidates(
        raw, reasons=reasons, date_index={d: i for i, d in enumerate(dates)}, card=card
    )
    candidates = [
        {
            "security_id": r.security_id,
            "signal_date": r.signal_date.isoformat(),
            "business_close": r.signal_close.isoformat(),
            "static_industry_id": r.industry_id,
            "eligibility_evidence_digest": r.eligibility_evidence_digest,
            "feature_evidence_digest": digest_value(r.feature_evidence.to_dict()),
        }
        for r in kept
    ]
    return {
        "candidates": candidates,
        "evaluated_rows": evaluated,
        "raw_triggers": raw_count,
        "isolated": isolated,
        "retained_candidates": len(candidates),
        "industry_gap_identities": len(gaps),
        "reason_counts": dict(sorted(reasons.items())),
        "raw_candidate_census_sha256": prior.native._sha(output / "raw-candidate-census.json"),
    }


def _scan_raw_candidates(
    c: Any,
    mapping: Any,
    cutoff: str,
    repository: Path,
    *,
    dates: tuple[date, ...],
    added: bool,
    security_ids: tuple[str, ...] | None = None,
) -> tuple[list[Any], Counter[str], int, int, int]:
    """Exact existing raw scanner; bounded decode, one complete security at a time."""
    index = {d: i for i, d in enumerate(dates)}
    card = h.load_candidate_rule_card(
        repository / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    policy = h._RecentVisibilityPolicy(
        dates[0],
        date(2012, 1, 4),
        date(2021, 12, 30),
        date(2021, 12, 31),
        h.POPULATION_REQUEST_DIGEST,
    )
    action = (
        " OR EXISTS(SELECT 1 FROM new_actions a WHERE a.security_id=b.security_id "
        "AND a.effective_date=b.date)"
        if added
        else ""
    )
    selection = ""
    if security_ids is not None:
        if not security_ids or len(set(security_ids)) != len(security_ids):
            raise ValueError("ACTION_RAW_QUERY_SECURITY_SET")
        selection = " AND b.security_id IN (" + ",".join("?" for _ in security_ids) + ")"
    cursor = c.execute(
        """SELECT b.*,f.listing_sessions,
        EXISTS(SELECT 1 FROM source_corporate_action_exceptions_mapped e
          WHERE e.security_id=b.security_id AND e.blocked_from<=b.date
          AND(e.blocked_through IS NULL OR e.blocked_through>=b.date)) AS exception_active,
        v.stock_code,v.exchange,v.valid_from,l.observed_tradestatus,
        (EXISTS(SELECT 1 FROM source_corporate_actions_normalized a
          WHERE a.security_id=b.security_id AND a.effective_date=b.date)"""
        + action
        + """) AS action_boundary
        FROM source_daily_ohlcv_none b JOIN selected_universe_sessions f
          ON f.security_id=b.security_id AND f.market_date=b.date
        JOIN source_security_code_validity v ON v.security_id=b.security_id
        AND b.date>=CAST(v.valid_from AS DATE)
          AND(v.valid_to IS NULL OR b.date<=CAST(v.valid_to AS DATE))
        LEFT JOIN source_price_limit_execution_daily l
          ON l.security_id=b.security_id AND l.execution_date=b.date
        WHERE b.date >= DATE '2011-01-04'"""
        + selection
        + " ORDER BY b.security_id,v.valid_from,b.date",
        [] if security_ids is None else list(security_ids),
    )
    names = [r[0] for r in cursor.description]

    def rows() -> Any:
        while batch := cursor.fetchmany(8192):
            for row in batch:
                yield dict(zip(names, row, strict=True))

    raw = []
    reasons: Counter[str] = Counter()
    evaluated = series_count = decoded = 0
    for _, group in groupby(rows(), lambda r: (r["security_id"], r["stock_code"], r["valid_from"])):
        series = []
        for r in group:
            sid, day = r["security_id"], r["date"]
            membership = mapping.by_security_id.get(sid)
            series.append(
                h.CandidateDailyInput(
                    sid,
                    "legacy-listing:" + digest_value([sid, r["stock_code"], str(r["valid_from"])]),
                    r["stock_code"],
                    "legacy-security-reference:" + sid,
                    r["exchange"],
                    "common_equity",
                    None if membership is None else membership.level1_industry_id,
                    day,
                    datetime.combine(day, time(15), tzinfo=MARKET_TIMEZONE),
                    datetime.fromisoformat(cutoff),
                    **{
                        n + "_price": None if r[n] is None else Decimal(str(r[n]))
                        for n in ("open", "high", "low", "close")
                    },
                    turnover=None if r["amount"] is None else Decimal(str(r["amount"])),
                    is_tradable=r["observed_tradestatus"] == "1" and not r["exception_active"],
                    listing_sessions=r["listing_sessions"],
                    adjustment_effective=r["action_boundary"],
                    source_evidence_digest=digest_value(
                        {"source_manifest": h.SOURCE_SHA, "values": [str(v) for v in r.values()]}
                    ),
                )
            )
        found, blocked, count = h._scan_security_series(
            series,
            ordered_dates=dates,
            date_index=index,
            history_sessions=22,
            snapshot_id=h.VERSION + "/" + h.POPULATION_REQUEST_DIGEST.removeprefix("sha256:"),
            snapshot_manifest_digest=h.SOURCE_SHA,
            card=card,
            source_policy=policy,
            information_cutoff=datetime.fromisoformat(cutoff),
        )
        raw.extend(found)
        reasons.update(blocked)
        evaluated += count
        series_count += 1
        decoded += len(series)
    return raw, reasons, evaluated, series_count, decoded


def _parallel_population(
    c: Any,
    mapping: Any,
    repository: Path,
    source: Path,
    request: dict[str, Any],
    *,
    dates: tuple[date, ...],
    output: Path,
) -> dict[str, Any]:
    """Exactly two disjoint complete-security scans; one global frozen retain.

    Each role owns its two workers and source copy. Memory/spill budgets split
    2x2GB/2x3GB, one DuckDB thread per worker, preserving 4GB/6GB/two threads
    per role. Kernel cold isolation is inherited by subprocesses across exec.
    """
    from quanthus.event_library.scan import CandidateScanShard

    counts = c.execute(
        "SELECT security_id,count(*) FROM source_daily_universe "
        "GROUP BY security_id ORDER BY count(*) DESC,security_id"
    ).fetchall()
    c.close()  # release the role's prerequisite connection before workers
    import gc

    import pyarrow as pa

    gc.collect()
    pa.default_memory_pool().release_unused()
    buckets: list[list[str]] = [[], []]
    sizes = [0, 0]
    for sid, count in counts:
        index = min(range(2), key=lambda i: (sizes[i], i))
        buckets[index].append(sid)
        sizes[index] += count
    assert buckets[0] and buckets[1]
    plan = {
        "kind": VERSION + "/population-stage",
        "source_manifest_sha256": request["source_manifest_sha256"],
        "request_digest": digest_value(request),
        "device": output.stat().st_dev,
        "free_bytes_before": os.statvfs(output).f_bavail * os.statvfs(output).f_frsize,
        "free_inodes_before": os.statvfs(output).f_favail,
        "worker_count": 2,
        "threads_per_worker": 1,
        "DuckDB_memory_per_worker": "2GB",
        "spill_per_worker": "3GB",
        "assignments": [sorted(x) for x in buckets],
        "listing_rows_per_assignment": sizes,
        "global_retain": "one unchanged global pass after both raw shards",
    }
    (output / "population-stage-freeze.json").write_text(
        json.dumps(plan, sort_keys=True, indent=2) + "\n"
    )
    children = []
    started = clock.monotonic()
    try:
        for index, ids in enumerate(buckets):
            folder = output / f"population-{index:02d}"
            folder.mkdir()
            spec = {
                "kind": VERSION + "/raw-shard",
                "request_digest": digest_value(request),
                "source": str(source),
                "source_manifest_sha256": request["source_manifest_sha256"],
                "output": str(folder),
                "security_ids": sorted(ids),
                "dates": [d.isoformat() for d in dates],
                "industry": {
                    sid: mapping.by_security_id[sid].level1_industry_id
                    for sid in ids
                    if sid in mapping.by_security_id
                },
            }
            path = folder / "request.json"
            path.write_text(json.dumps(spec, sort_keys=True, indent=2) + "\n")
            log = (folder / "execution.txt").open("x")
            args = [sys.executable, "-m", __name__, "--raw-shard", str(repository), str(path)]
            child = subprocess.Popen(
                args, stdout=log, stderr=subprocess.STDOUT, env=os.environ.copy()
            )
            children.append((child, log, folder, sorted(ids)))
        while any(p.poll() is None for p, _, _, _ in children):
            if any(p.poll() not in (None, 0) for p, _, _, _ in children):
                raise ValueError("ACTION_RAW_SHARD_FAILED")
            clock.sleep(0.25)
        if any(p.returncode != 0 for p, _, _, _ in children):
            raise ValueError("ACTION_RAW_SHARD_FAILED")
        raw = []
        reasons: Counter[str] = Counter()
        evaluated = series_count = decoded = 0
        seen: set[str] = set()
        evidence = []
        for _, _, folder, ids in children:
            receipt = json.loads((folder / "COMPLETE.json").read_bytes())
            if (
                receipt["request_digest"] != digest_value(request)
                or receipt["source_manifest_sha256"] != request["source_manifest_sha256"]
                or receipt["sha256"] != prior.native._sha(folder / "raw.json")
            ):
                raise ValueError("ACTION_RAW_SHARD_IDENTITY")
            shard = CandidateScanShard.from_dict(json.loads((folder / "raw.json").read_bytes()))
            if list(shard.security_ids) != ids or seen.intersection(ids):
                raise ValueError("ACTION_RAW_SHARD_OWNERSHIP")
            seen.update(ids)
            raw.extend(shard.raw_candidates)
            reasons.update(shard.reason_counts)
            evaluated += shard.evaluated_rows
            series_count += shard.series_count
            decoded += shard.decoded_rows
            evidence.append(receipt)
        if seen != {sid for sid, _ in counts}:
            raise ValueError("ACTION_RAW_SHARD_COVERAGE")
        result = _finalize_population(
            raw,
            reasons,
            evaluated,
            series_count,
            decoded,
            SimpleNamespace(by_security_id={k: mapping.by_security_id.get(k) for k in seen}),
            repository,
            dates=dates,
            output=output,
        )
        (output / "population-stage-result.json").write_text(
            json.dumps(
                {
                    "workers": 2,
                    "elapsed_seconds": clock.monotonic() - started,
                    "shards": evidence,
                    "global_retain_once": True,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        result["raw_scan_execution"] = {
            "workers": 2,
            "global_retain_once": True,
            "shards": [
                {k: r[k] for k in ("sha256", "rows", "security_count", "raw_candidates")}
                for r in evidence
            ],
        }
        return result
    finally:
        for child, _log, _, _ in children:
            if child.poll() is None:
                child.terminate()
        for child, log, _, _ in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            log.close()


def _execute_raw_shard(repository: Path, path: Path) -> None:
    import duckdb

    from quanthus.event_library.scan import CandidateScanShard

    spec = json.loads(path.read_bytes())
    request = load_request(repository)
    if (
        spec.get("kind") != VERSION + "/raw-shard"
        or spec.get("request_digest") != digest_value(request)
        or spec.get("source_manifest_sha256") != request["source_manifest_sha256"]
    ):
        raise ValueError("ACTION_RAW_SHARD_REQUEST")
    ids = tuple(spec["security_ids"])
    if not ids or ids != tuple(sorted(set(ids))):
        raise ValueError("ACTION_RAW_SHARD_SECURITY_SET")
    output = Path(spec["output"])
    source = Path(spec["source"])
    if (
        output.resolve() != output.absolute()
        or source.resolve() != source.absolute()
        or output == source
        or output in source.parents
        or source in output.parents
        or output != path.parent
        or (output / "raw.json").exists()
    ):
        raise ValueError("ACTION_RAW_SHARD_OUTPUT")
    started = clock.monotonic()
    bundle, facts = p10.load_input(
        source, request["source_manifest_sha256"].removeprefix("sha256:")
    )
    paths = {
        d.dataset: bundle.historical_bundle.partition_paths(d.dataset)
        for d in bundle.historical_bundle.datasets
        if d.dataset
        in {
            "daily_ohlcv_none",
            "daily_universe",
            "price_limit_execution_daily",
            "security_code_validity",
            "corporate_actions_normalized",
            "corporate_action_exceptions",
        }
    }
    (output / "spill").mkdir()
    c = duckdb.connect(
        config={
            "memory_limit": "2GB",
            "threads": 1,
            "max_temp_directory_size": "3GB",
            "temp_directory": str(output / "spill"),
        }
    )
    try:
        c.execute("SET timezone='UTC'")
        _create_feature_source_views(c, paths, ids, scope_end=date(2021, 12, 31))
        for name in ("corporate_actions_normalized", "corporate_action_exceptions"):
            c.execute(f"CREATE VIEW source_{name}_mapped AS SELECT * FROM source_{name}")
        _validate_feature_sources(c)
        import pyarrow as pa

        c.register(
            "new_action_arrow", pa.Table.from_pylist(_action_rows(facts, bundle.manifest_sha256))
        )
        c.execute("CREATE TEMP TABLE new_actions AS SELECT * FROM new_action_arrow")
        c.unregister("new_action_arrow")
        c.execute(
            "CREATE TEMP TABLE selected_universe_sessions AS SELECT security_id,market_date,"
            "row_number() OVER(PARTITION BY security_id ORDER BY market_date) "
            "AS listing_sessions FROM source_daily_universe"
        )
        mapping = SimpleNamespace(
            by_security_id={
                sid: SimpleNamespace(level1_industry_id=value)
                for sid, value in spec["industry"].items()
            }
        )
        raw = []
        reasons: Counter[str] = Counter()
        evaluated = series_count = decoded = 0
        # Each block owns complete security/listing series. The same immutable
        # source views serve all blocks; only global retention waits for shards.
        for begin in range(0, len(ids), 128):
            part, counts, n, series_n, rows_n = _scan_raw_candidates(
                c,
                mapping,
                request["source_information_cutoff"],
                repository,
                dates=tuple(date.fromisoformat(d) for d in spec["dates"]),
                added=True,
                security_ids=ids[begin : begin + 128],
            )
            raw.extend(part)
            reasons.update(counts)
            evaluated += n
            series_count += series_n
            decoded += rows_n
            print(
                json.dumps(
                    {
                        "stage": "raw_block_complete",
                        "securities_completed": min(begin + 128, len(ids)),
                        "security_total": len(ids),
                        "rows": decoded,
                        "elapsed_seconds": clock.monotonic() - started,
                    }
                ),
                flush=True,
            )
        shard = CandidateScanShard(
            tuple(raw), ids, dict(reasons), evaluated, series_count, decoded, 0.0, 0.0
        )
        (output / "raw.json").write_text(
            json.dumps(shard.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
        )
        receipt = {
            "status": "COMPLETE_RAW_SCAN_ONLY",
            "request_digest": digest_value(request),
            "source_manifest_sha256": request["source_manifest_sha256"],
            "request_sha256": prior.native._sha(path),
            "sha256": prior.native._sha(output / "raw.json"),
            "security_count": len(ids),
            "rows": decoded,
            "raw_candidates": len(raw),
            "elapsed_seconds": clock.monotonic() - started,
            "production_authorized": False,
        }
        (output / "COMPLETE.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    finally:
        c.close()


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--raw-shard":
        raise SystemExit("expected --raw-shard REPOSITORY REQUEST")
    _execute_raw_shard(Path(sys.argv[2]), Path(sys.argv[3]))
