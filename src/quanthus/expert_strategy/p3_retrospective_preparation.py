"""Once-only R2 candidate/feature/common-mask preparation; no result endpoint.

The compact typed port journal is one immutable job-local intermediate, shared
by the candidate and mask consumers. It is not an ADM, source repair, qualified
support receipt, strict EventRecord/NativeJob, or permission to run P2. In
particular no late source clock is coerced into a historical BacktestSignal.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import resource
import shutil
import sys
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import Any

from quanthus.backtest.baseline_research import (
    BaselineFeaturePanel,
    BaselineFeatureRow,
    _sql_string,
)
from quanthus.backtest.inputs import CoverageStatus
from quanthus.backtest.strategy_replay import PITDailyBar
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import CandidateDailyInput
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_base24_bridge as strict
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_retrospective_bridge as bridge
from quanthus.expert_strategy import p3_retrospective_features as features
from quanthus.expert_strategy.p3_base24_batch import FROZEN_SCOPE, _code_closure

VERSION = "quanthus.p3-retrospective-shared-preparation.v1"
PURPOSE = "p3-base24-retrospective-comparison-v1"
MEMBERS = {
    "plan.json",
    "ports.jsonl.gz",
    "features.parquet",
    "candidates.json",
    "common-mask.jsonl",
    "variants.json",
}


def _typed(value: Any) -> Any:
    # JSON visits frozen dataclass fields directly; avoid millions of unnecessary
    # deepcopy operations on immutable Decimal/date/clock objects.
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    if isinstance(value, Decimal):
        return {"Decimal": str(value)}
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    raise TypeError("unsupported R2 port field type")


def _object(value: dict[str, Any]) -> Any:
    if set(value) == {"Decimal"}:
        return Decimal(value["Decimal"])
    if set(value) == {"datetime"}:
        return datetime.fromisoformat(value["datetime"])
    if set(value) == {"date"}:
        return date.fromisoformat(value["date"])
    return value


def decode_port(value: Mapping[str, Any]) -> bridge.RetrospectivePort:
    """Decode the explicit port schema; never unpickle or open a source path."""
    return _decode_typed_port(json.loads(json.dumps(value), object_hook=_object))


def _decode_typed_port(raw: dict[str, Any]) -> bridge.RetrospectivePort:
    # The stream parser already owns this fresh dictionary; no JSON round trip.
    selected = raw.pop("selected")
    if selected is not None:
        candidate = selected.pop("candidate")
        bar = selected.pop("bar")
        bar["coverage_status"] = CoverageStatus(bar["coverage_status"])
        selected["unavailable_reasons"] = tuple(selected["unavailable_reasons"])
        selected = strict.DecisionPort(
            candidate=None if candidate is None else CandidateDailyInput(**candidate),
            bar=PITDailyBar(**bar),
            **selected,
        )
    raw["unavailable_reasons"] = tuple(raw["unavailable_reasons"])
    return bridge.RetrospectivePort(selected=selected, **raw)


def _ports(root: Path) -> Iterator[bridge.RetrospectivePort]:
    with gzip.open(root / "ports.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            yield _decode_typed_port(json.loads(line, object_hook=_object))


def _write(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, default=_typed) + "\n")


def load_candidates(path: Path) -> dict[str, Any]:
    """Read a bounded row payload; the ordinary 4 MiB metadata limit stays frozen.

    The full frozen 8,016-candidate population occupies about 5 MiB. A separate
    16 MiB cap accommodates this row product without relaxing manifest readers.
    Full semantic verification and exact member hashes remain mandatory.
    """
    limit = 16 * 1024**2
    with native.physical(path).open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise native.InputError("RETRO_CANDIDATE_PAYLOAD_TOO_LARGE")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise native.InputError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    value = json.loads(payload, object_pairs_hook=pairs)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != features.VERSION
        or not isinstance(value.get("candidates"), list)
    ):
        raise native.InputError("RETRO_CANDIDATE_PAYLOAD_SCHEMA")
    return value


def load_actions(path: Path, cutoff: datetime) -> list[dict[str, Any]]:
    """Consume an exact P10 event file; qualification comes from its support seal."""
    raw = json.loads(path.read_text(), object_hook=_object)
    if not isinstance(raw, list):
        raise native.InputError("RETRO_ACTION_SCHEMA")
    keys, ids, output = set(), set(), []
    for a in raw:
        day = date.fromisoformat(a["effective_date"])
        known = datetime.fromisoformat(a["known_at"])
        cash, shares = a["cash_dividend_per_share"], a["bonus_share_ratio"]
        key = (a["security_id"], day)
        if (
            a.get("contract") != "p10-retained-implemented-plan-economic-view.v1"
            or not isinstance(a["security_id"], str)
            or not a["security_id"]
            or not native._SHA.fullmatch(a["economic_event_id"])
            or key in keys
            or a["economic_event_id"] in ids
            or not strict._aware(known)
            or known > cutoff
            or any(not isinstance(v, Decimal) or not v.is_finite() or v < 0 for v in (cash, shares))
            or (cash == 0 and shares == 0)
            or a.get("historical_pit_granted") is not False
        ):
            raise native.InputError("RETRO_ACTION_IDENTITY_OR_TIME")
        keys.add(key)
        ids.add(a["economic_event_id"])
        output.append({**a, "effective_date": day, "known_at": known})
    return output


def load_action_coverage(
    root: Path,
    plan: Mapping[str, Any],
    *,
    securities: Iterable[str] | None = None,
) -> dict[str, Any]:
    from quanthus.pipelines.securities_warehouse.p10_action_coverage import validate

    path = root / "action-coverage.json"
    if native._sha(path) != plan.get("action_coverage_sha256"):
        raise native.InputError("RETRO_ACTION_COVERAGE_IDENTITY")
    return validate(
        native._json(path),
        support_sha256=plan["support_manifest_sha256"],
        actions_sha256=plan["normalized_actions_sha256"],
        information_cutoff=datetime.fromisoformat(plan["information_cutoff"]),
        securities=securities,
    )


def members(plan: Mapping[str, Any]) -> set[str]:
    return (
        MEMBERS
        | ({"normalized-actions.json"} if plan.get("normalized_actions_sha256") else set())
        | ({"action-coverage.json"} if plan.get("action_coverage_sha256") else set())
    )


def _purpose_file(repository: Path, version: int) -> Path:
    if type(version) is not int or version not in (1, 2, 3):
        raise native.InputError("RETRO_PURPOSE_VERSION")
    return repository / f"config/research/p3-strat-003/retrospective-comparison.v{version}.json"


def _preparation_closure(repository: Path) -> dict[str, str]:
    closure = _code_closure(repository)
    # Admission is granted only after preparation is sealed. Its append-only
    # receipt registry cannot be included in its own prerequisite identity.
    # Every other activation-policy field remains bound, including purpose/gates.
    for filename in ("retrospective-execution.v1.json", "retrospective-execution.v2.json"):
        name = "config/research/p3-strat-003/" + filename
        policy = native._json(repository / name)
        closure[name] = digest_value({**policy, "admitted_consumer_receipt_sha256": []})
    component_name = "config/research/p3-strat-003/risk-components-execution.v1.json"
    if (repository / component_name).is_file():
        policy = native._json(repository / component_name)
        closure[component_name] = digest_value({**policy, "admitted_consumer_receipt_sha256": []})
    return closure


def _scope(days: Sequence[date], scope: tuple[date, date, date], synthetic: bool) -> None:
    if (
        type(synthetic) is not bool
        or (not synthetic and scope != FROZEN_SCOPE)
        or not days
        or tuple(days) != tuple(sorted(set(days)))
        or not scope[0] <= scope[1] < scope[2] == days[-1]
        or not all(d in days for d in scope[1:])
        or not any(scope[0] <= d <= scope[1] for d in days)
        or (not synthetic and len([d for d in days if d < scope[0]]) != 60)
    ):
        raise native.InputError("RETRO_PREPARATION_SCOPE")


def _checked(
    ports: Iterable[bridge.RetrospectivePort], plan: Mapping[str, Any]
) -> Iterator[bridge.RetrospectivePort]:
    previous = ("", date.min)
    days = set(map(date.fromisoformat, plan["trading_dates"]))
    for p in ports:
        key = (p.security_id, p.business_date)
        if (
            key <= previous
            or p.business_date not in days
            or p.information_cutoff.isoformat() != plan["information_cutoff"]
            or p.source_manifest_sha256 != plan["source_manifest_sha256"]
            or not native._SHA.fullmatch(p.row_sha256)
            or (p.execution_only and p.business_date <= date.fromisoformat(plan["scope"][1]))
        ):
            raise native.InputError("RETRO_PREPARATION_PORT_BINDING_OR_ORDER")
        if p.selected is not None and (
            p.selected.selected_at != p.information_cutoff
            or p.selected.source_manifest_sha256 != p.source_manifest_sha256
            or p.selected.bar.market_date != p.business_date
            or p.selected.bar.security_code != p.security_id
            or p.selected.execution_only != p.execution_only
            or not strict._aware(p.selected.open_at)
            or not strict._aware(p.selected.close_at)
            or p.selected.open_at.astimezone(native.TZ).date() != p.business_date
            or p.selected.close_at.astimezone(native.TZ).date() != p.business_date
            or not p.selected.open_at < p.selected.close_at <= p.information_cutoff
            or (
                p.selected.candidate is not None
                and (
                    p.selected.candidate.security_id != p.security_id
                    or p.selected.candidate.listing_id != p.listing_id
                    or p.selected.candidate.session_date != p.business_date
                    or p.selected.candidate.close_at != p.selected.close_at
                    or p.selected.candidate.knowledge_cutoff > p.information_cutoff
                )
            )
        ):
            raise native.InputError("RETRO_PREPARATION_SELECTED_PORT_BINDING")
        previous = key
        yield p


def _masks(
    repository: Path,
    root: Path,
    candidates: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    feature_digest: str,
) -> list[dict[str, Any]]:
    """Recheck the exact union rule from shared local ports and the one panel."""
    import duckdb

    days = tuple(map(date.fromisoformat, plan["trading_dates"]))
    index = {d: i for i, d in enumerate(days)}
    targets = {
        (c["security_id"], date.fromisoformat(c["signal_date"])) for c in candidates["candidates"]
    }
    card = load_candidate_rule_card(
        repository / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    output: list[dict[str, Any]] = []
    window: deque[bridge.RetrospectivePort] = deque(maxlen=22)
    prior = ""
    with duckdb.connect(":memory:") as c:
        c.execute("SET memory_limit='2GB'; SET threads=2")
        c.execute(
            "CREATE VIEW prepared_features AS SELECT * FROM read_parquet("
            + _sql_string(str(root / "features.parquet"))
            + ")"
        )
        names = [d[0] for d in c.execute("SELECT * FROM prepared_features LIMIT 0").description]
        if tuple(names) != BaselineFeaturePanel._OUTPUT_COLUMNS:
            raise native.InputError("RETRO_PREPARATION_FEATURE_SCHEMA")
        native._zero(
            c,
            "SELECT count(*) FROM (SELECT security_id,market_date "
            "FROM prepared_features GROUP BY ALL HAVING count(*)<>1)",
            "RETRO_PREPARATION_FEATURE_KEYS",
        )
        for p in _checked(_ports(root), plan):
            if p.security_id != prior:
                window.clear()
                prior = p.security_id
            window.append(p)
            key = p.security_id, p.business_date
            if key not in targets:
                continue
            i = index[p.business_date]
            if i < 21:
                raise native.InputError("RETRO_PREPARATION_CANDIDATE_HISTORY")
            values = c.execute(
                "SELECT * FROM prepared_features WHERE security_id=? AND market_date=?", key
            ).fetchone()
            feature = (
                None
                if values is None
                else BaselineFeatureRow.from_mapping(dict(zip(names, values, strict=True)))
            )
            mask = bridge.common_entry_mask(
                tuple(window),
                expected_dates=days[i - 21 : i + 1],
                card=card,
                feature=feature,
                feature_known_at=datetime.fromisoformat(plan["information_cutoff"]),
                feature_evidence_digest=feature_digest,
            )
            output.append(
                {
                    "security_id": p.security_id,
                    "business_date": p.business_date.isoformat(),
                    "eligible": mask.eligible,
                    "reasons": list(mask.reasons),
                    "evidence_digest": mask.evidence_digest,
                    "variants": {v: mask.for_variant(v) for v in strict.VARIANTS},
                }
            )
    if len(output) != len(targets):
        raise native.InputError("RETRO_PREPARATION_CANDIDATE_KEYS")
    return output


def materialize(
    repository: Path,
    output: Path,
    *,
    ports: Iterable[bridge.RetrospectivePort],
    trading_dates: Sequence[date],
    source_manifest_sha256: str,
    information_cutoff: datetime,
    support_manifest_sha256: str,
    input_manifest_sha256: str,
    industry_manifest_sha256: str,
    source_roots: Sequence[Path],
    scope: tuple[date, date, date] = FROZEN_SCOPE,
    synthetic: bool = False,
    normalized_actions: Path | None = None,
    purpose_version: int = 1,
    action_coverage: Path | None = None,
) -> dict[str, Any]:
    """Materialize result-blind prerequisites, not source/support admission.

    Caller must qualify the exact supplied support separately. Unknown support
    remains blocked. Real use also requires a frozen input/read budget and the
    original protected-partition authority; this is not a result-run command.
    """
    import duckdb
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    _scope(trading_dates, scope, synthetic)
    if not strict._aware(information_cutoff) or not all(
        native._SHA.fullmatch(s)
        for s in (
            source_manifest_sha256,
            support_manifest_sha256,
            input_manifest_sha256,
            industry_manifest_sha256,
        )
    ):
        raise native.InputError("RETRO_PREPARATION_IDENTITY")
    output = native.physical(output)
    if not source_roots:
        raise native.InputError("RETRO_PREPARATION_SOURCE_ROOTS_REQUIRED")
    for source in source_roots:
        native._isolate(native.physical(source), output, output)
    if output.exists() or output.is_symlink():
        raise native.InputError("RETRO_PREPARATION_OUTPUT_EXISTS")
    repository = repository.resolve()
    purpose_path = _purpose_file(repository, purpose_version)
    purpose = native._json(purpose_path)
    if (
        purpose.get("purpose_id") != PURPOSE
        or purpose.get("result_execution_authorized") is not False
        or purpose.get("original_request_replaced") is not False
        or purpose.get("original_request_digest") != native.REQUEST
        or purpose.get("variants") != list(strict.VARIANTS)
        or (
            not synthetic
            and (
                purpose.get("input_manifest_sha256") != input_manifest_sha256
                or purpose.get("source_manifest_sha256") != source_manifest_sha256
                or purpose.get("information_cutoff") != information_cutoff.isoformat()
            )
        )
    ):
        raise native.InputError("RETRO_PREPARATION_PURPOSE")
    action_sha = None
    if normalized_actions is not None:
        normalized_actions = native.physical(normalized_actions)
        native._isolate(normalized_actions, output, output)
        load_actions(normalized_actions, information_cutoff)
        action_sha = native._sha(normalized_actions)
    if not synthetic and purpose.get("support_manifest_sha256") not in (
        None,
        support_manifest_sha256,
    ):
        raise native.InputError("RETRO_PURPOSE_SUPPORT_BINDING")
    if (
        not synthetic
        and purpose_version >= 2
        and (
            purpose.get("support_manifest_sha256") != support_manifest_sha256
            or purpose.get("industry_manifest_sha256") != industry_manifest_sha256
            or action_sha is None
            or purpose.get("normalized_actions_sha256") != action_sha
        )
    ):
        raise native.InputError("RETRO_PURPOSE_P10_CLOSURE")
    coverage_sha = None
    if action_coverage is not None:
        from quanthus.pipelines.securities_warehouse.p10_action_coverage import validate

        action_coverage = native.physical(action_coverage)
        native._isolate(action_coverage, output, output)
        if purpose_version != 3 or action_sha is None:
            raise native.InputError("RETRO_ACTION_COVERAGE_REQUIRES_V3_ACTIONS")
        validate(
            native._json(action_coverage),
            support_sha256=support_manifest_sha256,
            actions_sha256=action_sha,
            information_cutoff=information_cutoff,
        )
        coverage_sha = native._sha(action_coverage)
    if purpose_version == 3 and (
        coverage_sha is None
        or (not synthetic and purpose.get("action_coverage_sha256") != coverage_sha)
    ):
        raise native.InputError("RETRO_PURPOSE_ACTION_COVERAGE")
    wall_budget = 7200 if purpose_version >= 2 else 600
    write_budget = 4 * 1024**3 if purpose_version >= 2 else 1024**3
    space, fs = shutil.disk_usage(output.parent), os.statvfs(output.parent)
    plan = {
        "schema_version": VERSION,
        "purpose_id": PURPOSE,
        "source_manifest_sha256": source_manifest_sha256,
        "input_manifest_sha256": input_manifest_sha256,
        "industry_manifest_sha256": industry_manifest_sha256,
        "support_manifest_sha256": support_manifest_sha256,
        "information_cutoff": information_cutoff.isoformat(),
        "scope": [d.isoformat() for d in scope],
        "trading_dates": [d.isoformat() for d in trading_dates],
        "synthetic": synthetic,
        "code_config_members": _preparation_closure(repository),
        "purpose_version": purpose_version,
        "normalized_actions_sha256": action_sha,
        "purpose_sha256": native._sha(purpose_path),
        "runtime": {
            "python": sys.version.split()[0],
            "duckdb": version("duckdb"),
            "pyarrow": version("pyarrow"),
        },
        "budgets": {
            "wall_seconds": wall_budget,
            "duckdb_memory": "2GB",
            "duckdb_threads": 2,
            "RSS_bytes": 4 * 1024**3,
            "write_bytes": write_budget,
            "minimum_free_bytes": 8 * 1024**3,
        },
        "journal_lifecycle": "immutable job-local TMP retained for these consumers and cold checks",
        "source_stream_builds": 1,
        "feature_panel_builds": 1,
        "original_B_accepted": False,
        "D_activated": False,
    }
    if coverage_sha is not None:
        plan["action_coverage_sha256"] = coverage_sha
    start = time.monotonic()
    resource_snapshot = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "output_parent_device": output.parent.stat().st_dev,
        "available_bytes": space.free,
        "available_inodes": fs.f_favail,
    }

    def guard() -> None:
        if (
            time.monotonic() - start > wall_budget
            or resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 > 4 * 1024**3
            or shutil.disk_usage(output.parent).free < 8 * 1024**3
            or (
                output.exists()
                and sum(p.stat().st_size for p in output.rglob("*") if p.is_file()) > write_budget
            )
        ):
            raise native.InputError("RETRO_PREPARATION_BUDGET")

    guard()
    output.mkdir(parents=True)
    _write(output / "plan.json", plan)
    if normalized_actions is not None:
        shutil.copyfile(normalized_actions, output / "normalized-actions.json")
        if native._sha(output / "normalized-actions.json") != action_sha:
            raise native.InputError("RETRO_ACTION_INPUT_CHANGED")
    if action_coverage is not None:
        shutil.copyfile(action_coverage, output / "action-coverage.json")
        load_action_coverage(output, plan)
    rows = 0
    security_ids: set[str] = set()
    with (
        (output / "ports.jsonl.gz").open("xb") as stored,
        gzip.GzipFile(
            fileobj=stored, filename="", mode="wb", mtime=0, compresslevel=1
        ) as compressed,
        io.TextIOWrapper(compressed, encoding="utf-8") as journal,
    ):

        def observed() -> Iterator[bridge.RetrospectivePort]:
            nonlocal rows
            for p in _checked(ports, plan):
                journal.write(json.dumps(p, default=_typed, sort_keys=True) + "\n")
                rows += 1
                security_ids.add(p.security_id)
                if rows % 8192 == 0:
                    guard()
                if p.business_date <= scope[1]:
                    yield p

        history_report = features.write_history(
            observed(),
            output / "history-TMP.parquet",
            information_cutoff=information_cutoff,
            source_manifest_sha256=source_manifest_sha256,
        )
    if not rows:
        raise native.InputError("RETRO_PREPARATION_EMPTY_INPUT")
    if coverage_sha is not None:
        load_action_coverage(output, plan, securities=security_ids)
    stages = [
        {
            "stage": "shared_ports_and_history_TMP",
            "disposition": "build_once",
            "rows": rows,
            "history_rows": history_report["rows"],
            "bytes": (output / "ports.jsonl.gz").stat().st_size
            + (output / "history-TMP.parquet").stat().st_size,
            "elapsed_seconds": round(time.monotonic() - start, 6),
        }
    ]
    prior = time.monotonic()
    guard()
    with duckdb.connect(":memory:") as c:
        c.execute("SET memory_limit='2GB'; SET threads=2")
        spill = output / "spill-TMP"
        spill.mkdir()
        c.execute("SET temp_directory=?", [str(spill)])
        features.feature_panel(
            c,
            history=output / "history-TMP.parquet",
            trading_dates=trading_dates,
            scope_start=scope[0],
            scope_end=scope[1],
            output=output / "features.parquet",
            normalized_actions=(output / "normalized-actions.json") if action_sha else None,
            information_cutoff=information_cutoff if action_sha else None,
        )
    guard()
    stages.append(
        {
            "stage": "feature_panel",
            "disposition": "build_once",
            "rows": pq.ParquetFile(output / "features.parquet").metadata.num_rows,
            "bytes": (output / "features.parquet").stat().st_size,
            "elapsed_seconds": round(time.monotonic() - prior, 6),
        }
    )
    return _finish_preparation(
        repository,
        output,
        plan=plan,
        rows=rows,
        stages=stages,
        resource_snapshot=resource_snapshot,
        guard=guard,
    )


def _finish_preparation(
    repository: Path,
    output: Path,
    *,
    plan: Mapping[str, Any],
    rows: int,
    stages: list[dict[str, Any]],
    resource_snapshot: Mapping[str, Any],
    guard: Callable[[], None],
) -> dict[str, Any]:
    """Finish an owned, verified prerequisite stage without rebuilding it.

    A recovery caller must bind exact prerequisite bytes and unchanged producer
    semantics before physically copying them into a new isolated output. The full
    ordinary verifier and consumer admission still govern the resulting package.
    """
    import pyarrow.parquet as pq

    if native._json(output / "plan.json") != plan:
        raise native.InputError("RETRO_RECOVERY_PLAN_CHANGED")
    if plan["code_config_members"] != _preparation_closure(repository):
        raise native.InputError("RETRO_PREPARATION_CODE_CONFIG_CHANGED")
    trading_dates = tuple(map(date.fromisoformat, plan["trading_dates"]))
    start, end, tail = map(date.fromisoformat, plan["scope"])
    scope = (start, end, tail)
    _scope(trading_dates, scope, plan["synthetic"])
    information_cutoff = datetime.fromisoformat(plan["information_cutoff"])
    source_manifest_sha256 = plan["source_manifest_sha256"]
    support_manifest_sha256 = plan["support_manifest_sha256"]
    member_set = members(plan)
    guard()
    entries = {"features.parquet": native._entry(output / "features.parquet")}
    prior = time.monotonic()
    candidates = features.candidate_observations(
        _ports(output),
        trading_dates=trading_dates,
        repository=repository,
        source_manifest_sha256=source_manifest_sha256,
        information_cutoff=information_cutoff,
        support_manifest_sha256=support_manifest_sha256,
    )
    candidates["candidates"] = [
        r
        for r in candidates["candidates"]
        if scope[0].isoformat() <= r["signal_date"] <= scope[1].isoformat()
    ]
    _write(output / "candidates.json", candidates)
    stages.append(
        {
            "stage": "candidate_observations",
            "disposition": "reuse_shared_ports",
            "rows": len(candidates["candidates"]),
            "bytes": (output / "candidates.json").stat().st_size,
            "elapsed_seconds": round(time.monotonic() - prior, 6),
        }
    )
    prior = time.monotonic()
    masks = _masks(
        repository, output, candidates, plan, feature_digest=entries["features.parquet"]["sha256"]
    )
    with (output / "common-mask.jsonl").open("x", encoding="utf-8") as stream:
        for mask in masks:
            stream.write(json.dumps(mask, sort_keys=True) + "\n")
    stages.append(
        {
            "stage": "common_mask",
            "disposition": "reuse_shared_ports_and_features",
            "rows": len(masks),
            "bytes": (output / "common-mask.jsonl").stat().st_size,
            "elapsed_seconds": round(time.monotonic() - prior, 6),
        }
    )
    entries.update(
        {
            n: native._entry(output / n)
            for n in sorted(member_set - {"variants.json", "features.parquet"})
        }
    )
    shared = digest_value({n: entries[n]["sha256"] for n in sorted(entries)})
    request = native._json(repository / "config/research/p3-strat-003/request.v1.json")
    _write(
        output / "variants.json",
        [
            {
                **v,
                "shared_prerequisite_digest": shared,
                "purpose_id": PURPOSE,
                "execution_authorized": False,
                "result_namespace": PURPOSE + "/" + v["variant_id"],
            }
            for v in request["variants"]
        ],
    )
    entries["variants.json"] = native._entry(output / "variants.json")
    stages.append(
        {
            "stage": "four_variant_inputs",
            "disposition": "reuse_same_prerequisite",
            "rows": 4,
            "bytes": entries["variants.json"]["bytes"],
        }
    )
    guard()
    # Only successful consumed, owned TMP is removed; a failed job keeps evidence.
    (output / "history-TMP.parquet").unlink(missing_ok=True)
    spill = output / "spill-TMP"
    if spill.exists():
        spill.rmdir()
    result = {
        "status": "RESULT_BLIND_SHARED_PREPARATION_COMPLETE",
        "port_rows": rows,
        "feature_rows": pq.ParquetFile(output / "features.parquet").metadata.num_rows,
        "candidates": len(candidates["candidates"]),
        "eligible_candidates": sum(m["eligible"] for m in masks),
        "source_stream_builds": 1,
        "feature_panel_builds": 1,
        "real_comparisons_executed": 0,
        "support_qualified_by_this_package": False,
        "original_B_accepted": False,
        "D_activated": False,
    }
    manifest = {
        "schema_version": VERSION,
        "files": entries,
        "result": result,
        "stage_telemetry": stages,
        "resource_snapshot": resource_snapshot,
    }
    _write(output / "manifest.json", manifest)
    _write(
        output / "PREPARATION_COMPLETE.json",
        {
            "manifest_sha256": native._sha(output / "manifest.json"),
            "result_execution_authorized": False,
        },
    )
    return {**result, "manifest_sha256": native._sha(output / "manifest.json")}


def verify_integrity(
    repository: Path, root: Path, *, manifest_sha256: str
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Recheck every physical byte and binding before reusing an admitted proof."""
    root = native.physical(root)
    if (
        not native._SHA.fullmatch(manifest_sha256)
        or native._sha(root / "manifest.json") != manifest_sha256
    ):
        raise native.InputError("RETRO_PREPARATION_EXTERNAL_MANIFEST_BINDING")
    plan = native._json(root / "plan.json")
    member_set = members(plan)
    if native._tree(root) != member_set | {"manifest.json", "PREPARATION_COMPLETE.json"}:
        raise native.InputError("RETRO_PREPARATION_PHYSICAL_FILES")
    seal, manifest = (
        native._json(root / "PREPARATION_COMPLETE.json"),
        native._json(root / "manifest.json"),
    )
    if (
        seal
        != {
            "manifest_sha256": native._sha(root / "manifest.json"),
            "result_execution_authorized": False,
        }
        or manifest.get("schema_version") != VERSION
        or set(manifest.get("files", {})) != member_set
    ):
        raise native.InputError("RETRO_PREPARATION_MANIFEST")
    for name in sorted(member_set):
        native._check_member(root / name, manifest["files"][name])
    plan = native._json(root / "plan.json")
    if plan["code_config_members"] != _preparation_closure(repository.resolve()):
        raise native.InputError("RETRO_PREPARATION_CODE_CONFIG_CHANGED")
    purpose_path = _purpose_file(repository, plan.get("purpose_version", 1))
    purpose = native._json(purpose_path)
    if (
        plan.get("schema_version") != VERSION
        or plan.get("purpose_id") != PURPOSE
        or plan.get("purpose_sha256") != native._sha(purpose_path)
        or plan.get("original_B_accepted") is not False
        or plan.get("D_activated") is not False
        or plan.get("runtime")
        != {
            "python": sys.version.split()[0],
            "duckdb": version("duckdb"),
            "pyarrow": version("pyarrow"),
        }
        or (
            plan.get("synthetic") is False
            and (
                plan.get("input_manifest_sha256") != purpose.get("input_manifest_sha256")
                or plan.get("source_manifest_sha256") != purpose.get("source_manifest_sha256")
                or plan.get("information_cutoff") != purpose.get("information_cutoff")
            )
        )
    ):
        raise native.InputError("RETRO_PREPARATION_PLAN_BINDING")
    if (
        plan.get("synthetic") is False
        and plan.get("purpose_version", 1) >= 2
        and any(
            plan.get(key) is None or plan.get(key) != purpose.get(key)
            for key in (
                "support_manifest_sha256",
                "industry_manifest_sha256",
                "normalized_actions_sha256",
            )
        )
    ):
        raise native.InputError("RETRO_PURPOSE_P10_CLOSURE")
    if plan.get("normalized_actions_sha256"):
        if native._sha(root / "normalized-actions.json") != plan["normalized_actions_sha256"]:
            raise native.InputError("RETRO_ACTION_FILE_BINDING")
        load_actions(
            root / "normalized-actions.json", datetime.fromisoformat(plan["information_cutoff"])
        )
    if plan.get("purpose_version") == 3:
        if plan.get("synthetic") is False and plan.get("action_coverage_sha256") != purpose.get(
            "action_coverage_sha256"
        ):
            raise native.InputError("RETRO_PURPOSE_ACTION_COVERAGE")
        load_action_coverage(root, plan)
    load_candidates(root / "candidates.json")
    days = tuple(map(date.fromisoformat, plan["trading_dates"]))
    if not isinstance(plan.get("scope"), list) or len(plan["scope"]) != 3:
        raise native.InputError("RETRO_PREPARATION_SCOPE")
    start, end, tail = map(date.fromisoformat, plan["scope"])
    _scope(days, (start, end, tail), plan["synthetic"])
    return root, manifest, plan


def verify(repository: Path, root: Path, *, manifest_sha256: str) -> dict[str, Any]:
    """Full cold semantic recomputation, before any consumer admission."""
    root, manifest, plan = verify_integrity(repository, root, manifest_sha256=manifest_sha256)
    member_set = members(plan)
    days = tuple(map(date.fromisoformat, plan["trading_dates"]))
    stored_candidates = load_candidates(root / "candidates.json")
    candidates = features.candidate_observations(
        _checked(_ports(root), plan),
        trading_dates=days,
        repository=repository,
        source_manifest_sha256=plan["source_manifest_sha256"],
        information_cutoff=datetime.fromisoformat(plan["information_cutoff"]),
        support_manifest_sha256=plan["support_manifest_sha256"],
    )
    candidates["candidates"] = [
        r
        for r in candidates["candidates"]
        if plan["scope"][0] <= r["signal_date"] <= plan["scope"][1]
    ]
    if candidates != stored_candidates:
        raise native.InputError("RETRO_PREPARATION_CANDIDATE_SEMANTICS")
    masks = _masks(
        repository,
        root,
        candidates,
        plan,
        feature_digest=manifest["files"]["features.parquet"]["sha256"],
    )
    stored_masks = [
        json.loads(line) for line in (root / "common-mask.jsonl").read_text().splitlines()
    ]
    if masks != stored_masks:
        raise native.InputError("RETRO_PREPARATION_MASK_SEMANTICS")
    shared = digest_value(
        {n: manifest["files"][n]["sha256"] for n in sorted(member_set - {"variants.json"})}
    )
    request = native._json(repository / "config/research/p3-strat-003/request.v1.json")
    expected = [
        {
            **v,
            "shared_prerequisite_digest": shared,
            "purpose_id": PURPOSE,
            "execution_authorized": False,
            "result_namespace": PURPOSE + "/" + v["variant_id"],
        }
        for v in request["variants"]
    ]
    if json.loads((root / "variants.json").read_text()) != expected:
        raise native.InputError("RETRO_PREPARATION_VARIANT_SEMANTICS")
    import pyarrow.parquet as pq

    port_rows = 0
    security_ids = set()
    for port in _ports(root):
        port_rows += 1
        security_ids.add(port.security_id)
    if plan.get("action_coverage_sha256"):
        load_action_coverage(root, plan, securities=security_ids)
    result = {
        "status": "RESULT_BLIND_SHARED_PREPARATION_COMPLETE",
        "port_rows": port_rows,
        "feature_rows": pq.ParquetFile(root / "features.parquet").metadata.num_rows,
        "candidates": len(candidates["candidates"]),
        "eligible_candidates": sum(m["eligible"] for m in masks),
        "source_stream_builds": 1,
        "feature_panel_builds": 1,
        "real_comparisons_executed": 0,
        "support_qualified_by_this_package": False,
        "original_B_accepted": False,
        "D_activated": False,
    }
    if result != manifest["result"]:
        raise native.InputError("RETRO_PREPARATION_RESULT_SEMANTICS")
    return {**result, "manifest_sha256": manifest_sha256}
