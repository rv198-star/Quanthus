"""One R2 feature materialization and frozen P4 business-candidate selection.

This is result-blind consumer engineering, not a replay entrypoint. In particular
the candidate dictionaries are deliberately not strict EventRecords. Their real
late knowledge clocks cannot satisfy that contract and must not be overwritten.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from itertools import groupby
from pathlib import Path
from typing import Any

from quanthus.backtest.baseline_research import _feature_panel_query, _sql_string
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import _retain_raw_candidates, _scan_security_series
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_retrospective_bridge as bridge
from quanthus.expert_strategy.p3_strat_003_input_qualification import _RecentVisibilityPolicy

VERSION = "quanthus.p3-retrospective-features.v1"


def candidate_observations(
    ports: Iterable[bridge.RetrospectivePort],
    *,
    trading_dates: Sequence[date],
    repository: Path,
    source_manifest_sha256: str,
    information_cutoff: datetime,
    support_manifest_sha256: str,
) -> dict[str, Any]:
    """Reuse exact P4 ratios, isolation and concentration, without a PIT claim.

    Each security's complete series must be passed together. Unknown clocks or
    support leave an observation gap; they never become a manufactured candidate.
    Output is input/eligibility evidence, not a strategy or economic result.
    """
    card = load_candidate_rule_card(
        repository / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    if (
        not trading_dates
        or tuple(trading_dates) != tuple(sorted(set(trading_dates)))
        or not native._SHA.fullmatch(source_manifest_sha256)
        or not native._SHA.fullmatch(support_manifest_sha256)
    ):
        raise native.InputError("RETRO_CANDIDATE_CALENDAR_OR_SOURCE")
    index = {d: i for i, d in enumerate(trading_dates)}
    snapshot = "p3-r2-" + digest_value(
        {
            "version": VERSION,
            "source": source_manifest_sha256,
            "snapshot": information_cutoff.isoformat(),
            "calendar": [d.isoformat() for d in trading_dates],
            "support": support_manifest_sha256,
            "candidate_rule": card.semantic_digest,
        }
    ).removeprefix("sha256:")
    policy: Any = _RecentVisibilityPolicy(
        trading_dates[0],
        date(2025, 1, 1),
        date(2026, 9, 17),
        date(2026, 9, 18),
        source_manifest_sha256,
    )
    unavailable = 0

    def checked() -> Iterable[bridge.RetrospectivePort]:
        previous = ("", date.min)
        for p in ports:
            key = (p.security_id, p.business_date)
            if (
                p.source_manifest_sha256 != source_manifest_sha256
                or p.information_cutoff != information_cutoff
            ):
                raise native.InputError("RETRO_CANDIDATE_MIXED_SOURCE_OR_SNAPSHOT")
            if key <= previous or p.business_date not in index:
                raise native.InputError("RETRO_CANDIDATE_ORDER_OR_DUPLICATE")
            previous = key
            yield p

    raw = []
    reasons: Counter[str] = Counter()
    evaluated = 0
    for _sid, group in groupby(checked(), lambda p: p.security_id):
        # One security's bounded frozen series, never the whole market in memory.
        selected = tuple(group)
        unavailable += sum(p.selected is None for p in selected)
        series = tuple(
            p.selected.candidate
            for p in selected
            if p.selected is not None and p.selected.candidate is not None
        )
        found, blocked, n = _scan_security_series(
            tuple(series),
            ordered_dates=trading_dates,
            date_index=index,
            history_sessions=22,
            snapshot_id=snapshot,
            snapshot_manifest_digest=source_manifest_sha256,
            card=card,
            source_policy=policy,
            information_cutoff=information_cutoff,
        )
        raw.extend(found)
        reasons.update(blocked)
        evaluated += n
    retained, raw_count, isolated_count, gaps = _retain_raw_candidates(
        raw,
        reasons=reasons,
        date_index=index,
        card=card,
    )
    return {
        "schema_version": VERSION,
        "input_grade": "R2_RETROSPECTIVE_SNAPSHOT_NOT_HISTORICAL_PIT",
        "source_manifest_sha256": source_manifest_sha256,
        "information_cutoff": information_cutoff.isoformat(),
        "candidate_rule": card.semantic_digest,
        "support_manifest_sha256": support_manifest_sha256,
        "evaluated": evaluated,
        "raw_triggers": raw_count,
        "isolated": isolated_count,
        "unavailable_ports": unavailable,
        "reason_counts": dict(sorted(reasons.items())),
        "industry_gap_identities": len(gaps),
        "candidates": [
            {
                "security_id": c.security_id,
                "listing_id": c.listing_id,
                "signal_date": c.signal_date.isoformat(),
                "business_close": c.signal_close.isoformat(),
                "source_knowledge_cutoff": c.knowledge_cutoff.isoformat(),
                "entry_date": c.entry_date.isoformat(),
                "static_industry_id": c.industry_id,
                "eligibility_evidence_digest": c.eligibility_evidence_digest,
                "feature_evidence_digest": digest_value(c.feature_evidence.to_dict()),
                "rank_digest": c.rank_digest,
            }
            for c in retained
        ],
        "original_B_accepted": False,
        "D_activated": False,
        "is_strict_EventRecord": False,
    }


def feature_panel(
    connection: Any,
    *,
    history: Path,
    trading_dates: Sequence[date],
    scope_start: date,
    scope_end: date,
    output: Path,
) -> None:
    """One prefix-correct existing feature SQL, shared by B/T/R/TR.

    history is a previously checked job-local stream with native Decimal values
    encoded as lossless strings. The production feature evaluator already uses
    DOUBLE for indicators; original raw values are never rewritten. This no-action
    port blocks both unknown and effective-action observations. Qualified action
    accounting remains a distinct required port, not an inferred zero action.
    """
    import pyarrow as pa  # type: ignore[import-untyped]

    if output.exists() or output.is_symlink():
        raise native.InputError("RETRO_FEATURE_OUTPUT_EXISTS")
    c = connection
    if (
        not trading_dates
        or tuple(trading_dates) != tuple(sorted(set(trading_dates)))
        or not trading_dates[0] <= scope_start <= scope_end <= trading_dates[-1]
    ):
        raise native.InputError("RETRO_FEATURE_CALENDAR_OR_SCOPE")
    c.execute(f"CREATE VIEW input AS SELECT * FROM read_parquet({_sql_string(str(history))})")
    native._zero(
        c,
        "SELECT count(*) FROM (SELECT security_id,market_date FROM input "
        "GROUP BY ALL HAVING count(*)<>1)",
        "RETRO_FEATURE_DUPLICATE_KEY",
    )
    c.register("dates", pa.table({"market_date": list(trading_dates)}))
    native._zero(
        c,
        "SELECT count(*) FROM input WHERE market_date IS NULL OR "
        "security_id IS NULL OR NOT EXISTS (SELECT 1 FROM dates d "
        "WHERE d.market_date=input.market_date)",
        "RETRO_FEATURE_DATE_OR_IDENTITY",
    )
    c.execute("""CREATE VIEW source_daily_universe AS SELECT security_id,dates.market_date
        FROM (SELECT security_id,min(market_date) AS first FROM input GROUP BY security_id) s
        CROSS JOIN dates WHERE dates.market_date>=s.first""")
    c.execute(
        "CREATE VIEW source_daily_ohlcv_none AS SELECT security_id, market_date AS date, "
        + ",".join(
            f"CAST({n} AS DOUBLE) AS {n}"
            for n in ("open", "high", "low", "close", "volume", "amount")
        )
        + " FROM input"
    )
    c.execute("""CREATE VIEW source_price_limit_execution_daily AS SELECT security_id,
        market_date AS execution_date, entry AS entry_policy_allows,
        executable AS is_executable, NULL::DOUBLE AS upper_limit_pct,
        NULL::DOUBLE AS lower_limit_pct, upper AS upper_limit_price,
        lower AS lower_limit_price FROM input""")
    c.execute("""CREATE VIEW source_corporate_actions_normalized_mapped AS SELECT
        NULL::VARCHAR AS security_id,NULL::DATE AS effective_date,
        NULL::DOUBLE AS cash_dividend_per_share,NULL::DOUBLE AS bonus_share_ratio,
        NULL::TIMESTAMPTZ AS known_at WHERE false""")
    c.execute("""CREATE VIEW source_corporate_action_exceptions_mapped AS SELECT
        security_id,market_date AS blocked_from,market_date AS blocked_through
        FROM input WHERE action_unknown_or_effective""")
    query = _feature_panel_query(scope_start, scope_end, continuity_asof_each_row=True)
    c.execute(f"COPY ({query}) TO {_sql_string(str(output))} (FORMAT PARQUET)")


def write_history(
    ports: Iterable[bridge.RetrospectivePort],
    path: Path,
    *,
    information_cutoff: datetime,
    source_manifest_sha256: str,
) -> Mapping[str, Any]:
    """One bounded shared TMP. Caller owns isolation/budget and success cleanup.

    Unknown session clocks are recorded in the census and retained as explicit
    NULL projections; their keys and unavailability remain in the denominator.
    Never silently drop them
    from a required admission denominator or call this output accepted input.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if path.exists() or path.is_symlink():
        raise native.InputError("RETRO_HISTORY_OUTPUT_EXISTS")
    names = ("open", "high", "low", "close", "volume", "amount", "upper", "lower")
    schema = pa.schema(
        [
            ("security_id", pa.string()),
            ("market_date", pa.date32()),
            *[(n, pa.string()) for n in names],
            ("entry", pa.bool_()),
            ("executable", pa.bool_()),
            ("action_unknown_or_effective", pa.bool_()),
            ("information_cutoff", pa.timestamp("us", tz="Asia/Shanghai")),
            ("source_row_digest", pa.string()),
        ]
    )
    rows = skipped = 0
    reasons: Counter[str] = Counter()
    with pq.ParquetWriter(path, schema) as writer:
        buffer = []
        for p in ports:
            if (
                p.source_manifest_sha256 != source_manifest_sha256
                or p.information_cutoff != information_cutoff
            ):
                raise native.InputError("RETRO_HISTORY_MIXED_SOURCE_OR_SNAPSHOT")
            if p.execution_only:
                continue
            values: tuple[Any, ...]
            unavailable = p.selected is None
            if unavailable:
                skipped += 1
                reasons.update(p.unavailable_reasons)
                values = (None,) * 8
                entry = executable = False
                unknown = True
            else:
                assert p.selected is not None
                s, b = p.selected, p.selected.bar
                if (
                    s.selected_at != information_cutoff
                    or s.source_manifest_sha256 != source_manifest_sha256
                    or b.market_date != p.business_date
                    or b.security_code != p.security_id
                ):
                    raise native.InputError("RETRO_HISTORY_PORT_BINDING")
                values = (
                    b.open_price,
                    b.high_price,
                    b.low_price,
                    b.close_price,
                    b.volume,
                    b.amount,
                    b.upper_limit_price,
                    b.lower_limit_price,
                )
                entry, executable = b.entry_policy_allows, b.is_executable
                unknown = s.adjustment_effective is not False
            buffer.append(
                {
                    "security_id": p.security_id,
                    "market_date": p.business_date,
                    **dict(
                        zip(names, (None if v is None else str(v) for v in values), strict=True)
                    ),
                    "entry": entry,
                    "executable": executable,
                    "action_unknown_or_effective": unknown,
                    "information_cutoff": information_cutoff,
                    "source_row_digest": p.row_sha256,
                }
            )
            rows += 1
            if len(buffer) == 8192:
                writer.write_table(pa.Table.from_pylist(buffer, schema=schema))
                buffer.clear()
        if buffer:
            writer.write_table(pa.Table.from_pylist(buffer, schema=schema))
    return {
        "rows": rows,
        "unavailable_observations": skipped,
        "unavailable_reasons": dict(sorted(reasons.items())),
        "lifecycle": "TMP",
        "original_B_accepted": False,
        "D_activated": False,
    }
