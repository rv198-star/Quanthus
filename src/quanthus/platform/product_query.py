"""Read-only projection of accepted Quanthus research evidence for product clients."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.policy import ConfigurationError

PRODUCT_CATALOG_SCHEMA = "quanthus.product-research-catalog.v1"
PRODUCT_EVIDENCE_SCHEMA = "quanthus.product-candidate-evidence.v1"
_DWD_PACKAGE_ID = "dwd-base-202607-v2.1"
_DWD_CONTENT_DIGEST = (
    "sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9"
)
_DWD_CORRECTION_ID = "dwd-base-202607-v2.1-correction-20260813-001"
_DWD_CORRECTION_CONTENT_DIGEST = (
    "sha256:1c1d1f4ea906797255fbc71cc3915bb905e51e7aea202d27042e33692b72e9da"
)
_QUANTDINGER_PROJECTION_DIGEST = (
    "sha256:336a417b5609432c2442923e7c9837389621a92cdd1e05f7e14fe30d4240b1cd"
)
_REPLAY_PARTITIONS = {
    "development": ("2012-01-01", "2021-12-31"),
    "arbitration": ("2022-01-01", "2024-12-31"),
    "permanent_blind": ("2025-01-01", "2025-12-31"),
    "recent_research": ("2026-01-01", "2026-07-30"),
}


def _catalog_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise ConfigurationError(f"product research catalog {field} is invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigurationError(f"product research catalog {field} is invalid") from exc


class ProductResearchCatalog:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def read(self) -> dict[str, Any]:
        try:
            value: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"cannot read product research catalog: {self.path}") from exc
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != PRODUCT_CATALOG_SCHEMA
            or not isinstance(value.get("strategies"), list)
        ):
            raise ConfigurationError("product research catalog schema is invalid")
        if value.get("research_only") is not True or value.get("execution_authorized") is not False:
            raise ConfigurationError("product research catalog must remain research-only")
        data_scope = value.get("data_scope")
        if not isinstance(data_scope, dict):
            raise ConfigurationError("product research catalog data scope is invalid")
        dwd_base = data_scope.get("dwd_base")
        if (
            not isinstance(dwd_base, dict)
            or dwd_base.get("build_id") != "V2-Build202607"
            or dwd_base.get("package_id") != _DWD_PACKAGE_ID
            or dwd_base.get("correction_id") != _DWD_CORRECTION_ID
            or dwd_base.get("correction_content_digest")
            != _DWD_CORRECTION_CONTENT_DIGEST
            or _catalog_date(dwd_base.get("through_date"), "DWD through date")
            != date(2026, 7, 31)
            or dwd_base.get("coverage_start") != "dataset-specific"
        ):
            raise ConfigurationError("product research catalog DWD base is invalid")
        replay_partitions = data_scope.get("replay_partitions")
        if not isinstance(replay_partitions, dict) or set(replay_partitions) != set(
            _REPLAY_PARTITIONS
        ):
            raise ConfigurationError("product research catalog replay partitions are invalid")
        for partition_name, expected_window in _REPLAY_PARTITIONS.items():
            scope = replay_partitions.get(partition_name)
            if not isinstance(scope, dict) or (
                scope.get("start"),
                scope.get("end"),
            ) != expected_window:
                raise ConfigurationError("product research catalog replay partitions are invalid")
        latest_batch = value.get("latest_candidate_batch")
        if (
            not isinstance(latest_batch, dict)
            or latest_batch.get("candidate_count") != 5
            or latest_batch.get("succeeded_count") != 5
            or latest_batch.get("failed_count") != 0
            or latest_batch.get("shared_provider") is not True
            or latest_batch.get("dedicated_ads_required") is not False
            or latest_batch.get("governance_transition_applied") is not False
            or latest_batch.get("execution_authorized") is not False
        ):
            raise ConfigurationError("latest candidate batch contract is invalid")
        for candidate in value["strategies"]:
            if not isinstance(candidate, dict) or candidate.get("kind") != "candidate-version":
                raise ConfigurationError("product research catalog may expose candidates only")
            if (
                candidate.get("pipeline_state") != "replayed"
                or candidate.get("replay_state") != "replay-succeeded"
                or candidate.get("governance_decision") != "not-submitted"
            ):
                raise ConfigurationError("candidate catalog must contain replay facts only")
            if candidate.get("execution_authorized") is not False:
                raise ConfigurationError("candidate catalog cannot grant execution authority")
            evidence_scope = candidate.get("evidence_scope")
            if not isinstance(evidence_scope, dict):
                raise ConfigurationError("candidate evidence scope is invalid")
            evidence_partition = evidence_scope.get("partition")
            if (
                not isinstance(evidence_partition, str)
                or evidence_partition not in replay_partitions
            ):
                raise ConfigurationError("candidate evidence partition is invalid")
            partition_scope = replay_partitions[evidence_partition]
            decision_start = _catalog_date(
                evidence_scope.get("decision_start"), "candidate decision start"
            )
            decision_end = _catalog_date(
                evidence_scope.get("decision_end"), "candidate decision end"
            )
            execution_end = _catalog_date(
                evidence_scope.get("execution_end"), "candidate execution end"
            )
            if not (
                _catalog_date(partition_scope.get("start"), "partition start")
                <= decision_start
                <= decision_end
                <= execution_end
                <= _catalog_date(partition_scope.get("end"), "partition end")
            ):
                raise ConfigurationError("candidate evidence scope exceeds its replay partition")
            if candidate.get("source_system") == "quantdinger":
                lineage = candidate.get("data_lineage")
                conformance = candidate.get("external_conformance")
                if (
                    candidate.get("evidence_grade")
                    != "direct-dwd-v2.1-external-conformance"
                    or candidate.get("evidence_ref")
                    != "docs/acceptance/dwd-v2-1-external-findings-hotfix-20260813.md"
                    or not isinstance(lineage, dict)
                    or lineage.get("dwd_package_id") != _DWD_PACKAGE_ID
                    or lineage.get("dwd_content_digest") != _DWD_CONTENT_DIGEST
                    or lineage.get("price_basis") != "UNADJUSTED"
                    or lineage.get("corporate_action_revision") != _DWD_CORRECTION_ID
                    or lineage.get("projection_sha256")
                    != _QUANTDINGER_PROJECTION_DIGEST
                    or lineage.get("point_in_time_publication_proven") is not False
                    or not isinstance(conformance, dict)
                    or conformance.get("status")
                    != "corrected-data-policy-divergence"
                    or conformance.get("signal_match")
                    != "37 shared + 1 governed downstream divergence"
                    or conformance.get("fill_date_side_match") != "36 shared / 38 native"
                    or conformance.get("quantdinger_native_compound_return") != 75.739509
                    or conformance.get("quanthus_common_cost_fixed_unit_return")
                    != 59.076934
                    or conformance.get("quanthus_full_p2_fixed_unit_return")
                    != 59.628843
                    or not isinstance(
                        conformance.get("governed_execution_difference"), str
                    )
                    or not isinstance(conformance.get("capital_policy_difference"), str)
                    or conformance.get("legacy_direct_dwd_v2_1") is not False
                    or candidate.get("operations") != 36
                    or candidate.get("closed_trades") != 18
                    or candidate.get("total_return") != 59.628843
                ):
                    raise ConfigurationError(
                        "QuantDinger candidate lacks direct DWD conformance evidence"
                    )
        return value

    def candidate(self, strategy_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.read()["strategies"] if item["strategy_id"] == strategy_id),
            None,
        )


class ProductCandidateEvidence:
    def __init__(
        self,
        path: Path,
        catalog: ProductResearchCatalog,
        *,
        store: Any | None = None,
        run_root: Path | None = None,
    ) -> None:
        self.path = path.resolve()
        self.catalog = catalog
        self.store = store
        self.run_root = run_root.resolve() if run_root is not None else None
        self._active_cache: dict[str, dict[str, Any]] = {}

    def read(self) -> dict[str, Any]:
        try:
            value: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(
                f"cannot read product candidate evidence: {self.path}"
            ) from exc
        catalog = self.catalog.read()
        digest_payload = dict(value) if isinstance(value, dict) else {}
        observed_digest = digest_payload.pop("content_digest", None)
        canonical = json.dumps(
            digest_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        computed_digest = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != PRODUCT_EVIDENCE_SCHEMA
            or value.get("catalog_version") != catalog.get("catalog_version")
            or value.get("generated_from") != "accepted-replay-artifacts"
            or computed_digest != observed_digest
            or not isinstance(value.get("strategies"), list)
        ):
            raise ConfigurationError("product candidate evidence identity is invalid")
        catalog_ids = {
            (item["strategy_id"], item["version"]) for item in catalog["strategies"]
        }
        evidence_ids = {
            (item.get("strategy_id"), item.get("strategy_version"))
            for item in value["strategies"]
            if isinstance(item, dict)
        }
        if evidence_ids != catalog_ids or len(value["strategies"]) != len(catalog_ids):
            raise ConfigurationError("product candidate evidence coverage is invalid")
        for item in value["strategies"]:
            if (
                not isinstance(item.get("equity_curve"), list)
                or len(item["equity_curve"]) < 2
                or not isinstance(item.get("trades"), list)
                or not isinstance(item.get("performance"), dict)
                or not isinstance(item.get("run_manifest"), dict)
                or not isinstance(item.get("parameters"), dict)
            ):
                raise ConfigurationError("product candidate evidence payload is invalid")
        return value

    def candidate(self, strategy_id: str) -> dict[str, Any] | None:
        active = self._active_candidate(strategy_id)
        if active is not None:
            return active
        return next(
            (item for item in self.read()["strategies"] if item["strategy_id"] == strategy_id),
            None,
        )

    def candidate_summary(self, strategy_id: str) -> dict[str, Any] | None:
        active = self._active_run_item(strategy_id)
        if active is not None:
            active_run, item = active
            summary = item.get("summary")
            if not isinstance(summary, dict):
                raise ConfigurationError("active candidate Run summary is invalid")
            return {
                "source_artifact_sha256": active_run.evidence_digest,
                "summary": {
                    "operations": int(summary["operations"]),
                    "closed_trades": int(summary["closed_trades"]),
                    "total_return": float(summary["total_return"]) * 100,
                    "max_drawdown": float(summary["maximum_drawdown"]) * 100,
                },
            }
        candidate = next(
            (item for item in self.read()["strategies"] if item["strategy_id"] == strategy_id),
            None,
        )
        if candidate is None:
            return None
        catalog_candidate = self.catalog.candidate(strategy_id)
        if catalog_candidate is None:
            return None
        return {
            "source_artifact_sha256": candidate["source_artifact_sha256"],
            "summary": {
                "operations": int(catalog_candidate["operations"]),
                "closed_trades": int(catalog_candidate["closed_trades"]),
                "total_return": float(catalog_candidate["total_return"]),
                "max_drawdown": float(catalog_candidate["max_drawdown"]),
            },
        }

    def warm_active(self) -> None:
        if self.store is None or self.run_root is None:
            return
        for candidate in self.catalog.read()["strategies"]:
            self._active_candidate(str(candidate["strategy_id"]))

    def _active_run_item(
        self, strategy_id: str
    ) -> tuple[Any, dict[str, Any]] | None:
        if self.store is None or self.run_root is None:
            return None
        catalog_candidate = self.catalog.candidate(strategy_id)
        if catalog_candidate is None:
            return None
        active = next(
            (
                item
                for item in self.store.candidate_active_runs()
                if item.strategy_id == strategy_id
                and item.strategy_version == catalog_candidate["version"]
            ),
            None,
        )
        if active is None:
            return None
        run = self.store.candidate_run(active.run_id)
        if run is None:
            raise ConfigurationError("active candidate Run does not exist")
        item = next(
            (
                value
                for value in run.candidates
                if value.get("candidate_id") == strategy_id
                and value.get("candidate_version") == catalog_candidate["version"]
            ),
            None,
        )
        if item is None or item.get("artifact_sha256") != active.evidence_digest:
            raise ConfigurationError("active candidate evidence identity differs")
        return active, item

    def _active_candidate(self, strategy_id: str) -> dict[str, Any] | None:
        active_run_item = self._active_run_item(strategy_id)
        if active_run_item is None:
            return None
        active, item = active_run_item
        cached = self._active_cache.get(active.evidence_digest)
        if cached is not None:
            return cached
        catalog_candidate = self.catalog.candidate(strategy_id)
        assert catalog_candidate is not None
        assert self.store is not None
        run = self.store.candidate_run(active.run_id)
        assert run is not None and self.run_root is not None
        run_path = Path(run.artifact_ref)
        try:
            relative_run = run_path.relative_to("/data/candidate-runs")
        except ValueError as exc:
            raise ConfigurationError("candidate Run artifact reference is invalid") from exc
        evidence_path = (self.run_root / relative_run / str(item["artifact"])).resolve()
        if self.run_root not in evidence_path.parents:
            raise ConfigurationError("candidate evidence path escapes the Run root")
        try:
            encoded = evidence_path.read_bytes()
            raw = json.loads(encoded)
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError("active candidate evidence is unreadable") from exc
        observed_sha = "sha256:" + hashlib.sha256(encoded).hexdigest()
        if observed_sha != active.evidence_digest or not isinstance(raw, dict):
            raise ConfigurationError("active candidate evidence checksum differs")
        if "replay_artifact" in raw:
            raw = raw["replay_artifact"]
        if (
            raw.get("strategy_id") != strategy_id
            or raw.get("strategy_version") != catalog_candidate["version"]
        ):
            raise ConfigurationError("active candidate evidence belongs to another strategy")
        bootstrap = next(
            value
            for value in self.read()["strategies"]
            if value["strategy_id"] == strategy_id
        )
        projection = _product_evidence(raw, bootstrap["parameters"], observed_sha)
        self._active_cache[active.evidence_digest] = projection
        return projection


def _monthly_curve(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observed: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in rows:
        observed[str(row["market_date"])[:7]] = {
            "market_date": str(row["market_date"]),
            "system_equity": float(row["system_equity"]),
        }
    first = {
        "market_date": str(rows[0]["market_date"]),
        "system_equity": float(rows[0]["system_equity"]),
    }
    values = list(observed.values())
    return [first, *values] if values[0]["market_date"] != first["market_date"] else values


def _product_trade(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "security_code": str(row.get("security_code") or row.get("security_id")),
        "entry_date": str(row["entry_date"]),
        "exit_date": str(row["exit_date"]),
        "holding_sessions": int(row["holding_sessions"]),
        "entry_quantity": int(row["entry_quantity"]),
        "pnl": float(row["pnl"]),
        "return_rate": float(row["return_rate"]) * 100,
        "dividends": float(row["dividends"]),
        "fees": float(row["entry_fees"]) + float(row["exit_fees"]),
        **({"exit_sources": row["exit_sources"]} if row.get("exit_sources") else {}),
    }


def _product_evidence(
    artifact: dict[str, Any], parameters: dict[str, Any], evidence_sha256: str
) -> dict[str, Any]:
    performance = artifact["performance"]
    manifest = artifact.get("run_manifest") or artifact["backtest"]["manifest"]
    return {
        "strategy_id": artifact["strategy_id"],
        "strategy_version": artifact["strategy_version"],
        "source_artifact_sha256": evidence_sha256,
        "run_manifest": {
            key: manifest.get(key)
            for key in (
                "run_id",
                "data_snapshot_id",
                "execution_policy_version",
                "execution_profile_id",
                "schedule_version",
                "rule_version",
                "feature_version",
                "parameter_hash",
                "official",
            )
        },
        "parameters": parameters,
        "performance": {
            "initial_equity": float(performance["initial_equity"]),
            "ending_equity": float(performance["ending_equity"]),
            "total_pnl": float(performance["total_pnl"]),
            "total_fees": float(performance["total_fees"]),
            "win_rate": float(performance["win_rate"] or 0) * 100,
            "sharpe_ratio": (
                float(performance["sharpe_ratio"])
                if performance.get("sharpe_ratio") is not None
                else None
            ),
            "exposure_rate": float(performance["exposure_rate"]) * 100,
            "profit_factor": (
                float(performance["profit_factor"])
                if performance.get("profit_factor") is not None
                else None
            ),
            "average_holding_sessions": (
                float(performance["average_holding_sessions"])
                if performance.get("average_holding_sessions") is not None
                else None
            ),
            "turnover": float(performance["turnover"]),
            "return_excluding_top_3_closed_trades": float(
                performance["return_excluding_top_3_closed_trades"]
            )
            * 100,
            "capital_matched_benchmark_total_return": (
                float(performance["capital_matched_benchmark_total_return"]) * 100
                if performance.get("capital_matched_benchmark_total_return") is not None
                else None
            ),
        },
        "equity_curve": _monthly_curve(artifact["equity_curve"]),
        "trades": [_product_trade(row) for row in artifact["trades"]],
        "validation": artifact.get("validation", []),
        "summary": {
            "operations": int(
                manifest.get("result_summary", {}).get("fills")
                or performance.get("operation_count")
                or (
                    int(performance["closed_trades"]) * 2
                    + (1 if artifact.get("pending_exit") else 0)
                )
            ),
            "closed_trades": int(performance["closed_trades"]),
            "total_return": float(performance["total_return"]) * 100,
            "max_drawdown": float(performance["maximum_drawdown"]) * 100,
        },
    }
