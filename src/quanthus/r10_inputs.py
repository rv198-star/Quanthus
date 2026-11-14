"""Fixed native inputs for the Owner-approved R10 controlled replay."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime
from itertools import groupby
from pathlib import Path
from typing import Any

from quanthus.backtest.capital_clock import CapitalReplayClock
from quanthus.backtest.native_capital_adapter import NativeAdapterError, qualify_action
from quanthus.concentration import StaticIndustryMembership
from quanthus.event_library.contracts import SplitAssignment
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import CandidateDailyInput, scan_forward_candidate_series
from quanthus.industry_mapping import IndustryMappingSource, StaticIndustryMapping
from quanthus.r9_qualification import case_facts, quality_blocks
from quanthus.structure_v2_inputs import canonical, digest

MANIFEST_SHA = "8cd966be87fce02168c54e77c23e05f491c4afb204c4cbace42a2024eee1cdef"
REVISION = "1923c874b43d47e69190a6dc9546e6fd8f3d1ca3"
EXTRA_TABLES = {
    "dwd_company_industry_membership",
    "dwd_industry_node",
    "dwd_industry_scheme",
    "dwd_market_index",
}


def read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: Any) -> None:
    payload = canonical(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def rows(db: Any, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cur = db.execute(sql, params or [])
    names = [v[0] for v in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def check_contract(c: dict[str, Any]) -> None:
    if (
        c["schema_version"] != "quanthus.r10-controlled-replay.v1"
        or c["owner_approved"] is not True
        or c["source"]["revision"] != REVISION
        or c["purpose"] != "EXPOSED_CONTROLLED_ENGINEERING_REPLAY"
        or c["consumer_admission"] != "NOT_GRANTED"
    ):
        raise ValueError("R10_CONTROLLED_CONTRACT")
    if datetime.now().astimezone() >= datetime.fromisoformat(c["runtime"]["total_deadline"]):
        raise ValueError("R10_TOTAL_DEADLINE")


class ScopedPolicy:
    policy_id = "r10-controlled-development"
    policy_version = "1.0.0"
    prospective_clock_json = None

    def __init__(self, native: NativeInputs):
        self.native = native
        self.semantic_digest = digest({"purpose": native.c["purpose"], "version": "1.0.0"})
        self.snapshot_replay_cutoff = native.cutoff

    def require_stage_b_authority(self) -> None:
        check_contract(self.native.c)

    def require_scan_date(self, day: date) -> None:
        if not date(2020, 1, 2) <= day <= date(2021, 12, 31):
            raise ValueError("R10_PROTECTED_DATE")

    def candidate_signal_date_allowed(self, day: date) -> bool:
        return day <= date(2021, 8, 31)

    def assign_event_split(self, **kwargs: Any) -> SplitAssignment:
        self.require_scan_date(kwargs["signal_date"])
        return SplitAssignment.SNAPSHOT_REPLAY

    def replay_clock_for_date(self, day: date) -> str:
        return self.native.clock(day).to_json()


class NativeInputs:
    def __init__(
        self,
        project: Path,
        contract: Path,
        root: Path,
        *,
        build: bool = False,
        physical_dwd_root: Path | None = None,
    ):
        import duckdb

        self.project, self.contract, self.root = project, contract, root
        self.c = read(contract)
        check_contract(self.c)
        self.cutoff = datetime.fromisoformat(self.c["source"]["knowledge_cutoff"])
        self.contract_digest = "sha256:" + sha(contract)
        self.primary = physical_dwd_root or Path(self.c["source"]["primary_dwd_root"])
        self.full = physical_dwd_root or Path(self.c["source"]["full_dwd_root"])
        closure = read(project / self.c["source"]["primary_closure"])["members"]
        if sha(self.full / "manifest.json") != MANIFEST_SHA:
            raise ValueError("R10_SOURCE_MANIFEST")
        inventory = read(self.full / "manifest.json")["content"]["files"]
        by_path = {m["path"]: m for m in inventory}
        extra = [
            m
            for m in inventory
            if m["path"].split("/")[1:2]
            and (
                m["path"].split("/")[1] in EXTRA_TABLES
                or (
                    m["path"].startswith("payload/dwd_index_bar/interval_size=1d/")
                    and any(f"observation_start_year={y}/" in m["path"] for y in [2020, 2021])
                )
            )
        ]
        if len(closure) != 126 or len(extra) != 28:
            raise ValueError("R10_SOURCE_MEMBER_COUNT")
        self.members = [{**m, "root": "primary"} for m in closure] + [
            {**m, "root": "full"} for m in extra
        ]
        for m in self.members:
            if {k: m[k] for k in ("path", "size_bytes", "sha256")} != by_path[m["path"]]:
                raise ValueError("R10_SOURCE_MEMBER_IDENTITY")
            p = (self.primary if m["root"] == "primary" else self.full) / m["path"]
            if (
                p.is_symlink()
                or not p.is_file()
                or p.stat().st_size != m["size_bytes"]
                or "sha256:" + sha(p) != m["sha256"]
            ):
                raise ValueError("R10_SOURCE_MEMBER_HASH:" + m["path"])
        self.content_digest = digest(
            [{k: m[k] for k in ("path", "size_bytes", "sha256")} for m in self.members]
        )
        self.snapshot_id = "r10-native-" + self.content_digest[-24:]
        root.mkdir(parents=True, exist_ok=True)
        self.read_only = not build
        self.db = duckdb.connect(str(root / "native.duckdb"), read_only=self.read_only)
        self.db.execute("SET threads=2")
        self.db.execute("SET memory_limit='1GB'")
        self.db.execute("SET TimeZone='Asia/Shanghai'")
        self.db.execute("SET autoinstall_known_extensions=false")
        self.db.execute("SET autoload_known_extensions=false")
        if build:
            self._build()
        self.identities = {
            r["listing_id"]: r
            for r in rows(self.db, "SELECT * FROM identities ORDER BY listing_id")
        }
        self.industry = self._industry_object()
        self.calendar = rows(
            self.db, "SELECT * FROM calendar WHERE mic_code='XSHG' ORDER BY trade_date"
        )
        self.dates = [r["trade_date"] for r in self.calendar]
        self.date_index = {d: i for i, d in enumerate(self.dates)}
        self.session_ids = {
            (r["venue_id"], r["trade_date"]): r["calendar_session_id"]
            for r in rows(self.db, "SELECT venue_id,trade_date,calendar_session_id FROM calendar")
        }
        self.venues = {
            r["venue_id"]: {"XSHG": "SSE", "XSHE": "SZSE"}.get(r["mic_code"], "OTHER")
            for r in rows(self.db, "SELECT venue_id,mic_code FROM dwd_trading_venue")
        }

    def clock(self, day: date) -> CapitalReplayClock:
        identity = {
            "native_content_digest": self.content_digest,
            "purpose_sha256": self.contract_digest,
            "consumer_closure_sha256": self.contract_digest,
            "native_knowledge_cutoff": self.cutoff.isoformat(),
            "purpose_qualified_at": self.c["owner_approved_at"],
        }
        return CapitalReplayClock(
            day,
            self.cutoff,
            self.content_digest,
            "RESEARCH-R10-001",
            "RESEARCH-R10-001-T1",
            "A1",
            json.dumps(identity, sort_keys=True, separators=(",", ":")),
        )

    def _build(self) -> None:
        db = self.db
        tables = defaultdict(list)
        for m in self.members:
            if m["path"].startswith("payload/"):
                tables[m["path"].split("/")[1]].append(
                    str((self.primary if m["root"] == "primary" else self.full) / m["path"])
                )
        for table, paths in sorted(tables.items()):
            db.read_parquet(paths, hive_partitioning=True).create_view("raw_" + table)
            instant = self.cutoff.isoformat()
            db.execute(
                f"CREATE VIEW {table} AS SELECT * FROM raw_{table} "
                f"WHERE knowledge_from<=TIMESTAMPTZ '{instant}' AND "
                f"(knowledge_to IS NULL OR knowledge_to>TIMESTAMPTZ '{instant}')"
            )
        db.execute("""CREATE TABLE calendar AS SELECT c.*,v.mic_code
          FROM dwd_trading_calendar c JOIN dwd_trading_venue v USING(venue_id)
          WHERE c.is_open AND c.trade_date BETWEEN DATE '2019-01-01' AND DATE '2021-12-31'
            AND v.mic_code IN ('XSHG','XSHE');
          CREATE TABLE identities AS SELECT l.*,s.company_id,s.instrument_type,
            s.knowledge_from security_known,s.effective_from security_from,
            s.effective_to security_to
          FROM dwd_listing l JOIN dwd_security s USING(security_id)
          WHERE s.instrument_type='common_equity' AND l.listing_currency='CNY' AND l.lot_size=100
            AND CAST(l.effective_from AS DATE)<=DATE '2021-12-31'
            AND (l.effective_to IS NULL OR CAST(l.effective_to AS DATE)>DATE '2020-01-02')
            AND CAST(s.effective_from AS DATE)<=DATE '2021-12-31'
            AND (s.effective_to IS NULL OR CAST(s.effective_to AS DATE)>DATE '2020-01-02');""")
        if db.execute(
            "SELECT count(*) FROM (SELECT listing_id FROM identities GROUP BY 1 HAVING count(*)<>1)"
        ).fetchone()[0]:
            raise ValueError("R10_LISTING_AMBIGUITY")
        for mic in ["XSHG", "XSHE"]:
            days = db.execute(
                "SELECT trade_date FROM calendar WHERE mic_code=? ORDER BY trade_date", [mic]
            ).fetchall()
            if mic == "XSHG":
                first_days = days
            elif days != first_days:
                raise ValueError("R10_VENUE_CALENDAR_MISMATCH")
        db.execute("""CREATE TABLE native_panel AS SELECT l.listing_id,l.security_id,
          l.company_id,l.venue_id,
          l.listing_code,l.instrument_type,l.listing_currency,l.lot_size,l.security_known,
          l.knowledge_from listing_known,c.trade_date,c.calendar_session_id,
          c.open_at,c.close_at,c.mic_code,
          row_number() OVER(PARTITION BY l.listing_id ORDER BY c.trade_date) listing_sessions
          FROM identities l JOIN calendar c ON c.venue_id=l.venue_id
          AND c.trade_date>=CAST(l.effective_from AS DATE)
          AND (l.effective_to IS NULL OR c.trade_date<CAST(l.effective_to AS DATE))
          AND c.trade_date>=CAST(l.security_from AS DATE)
          AND (l.security_to IS NULL OR c.trade_date<CAST(l.security_to AS DATE));
          CREATE TABLE daily AS SELECT p.*,b.open,b.high,b.low,b.close,b.volume,b.turnover,
           b.currency,b.price_basis,b.observation_start bar_start,b.observation_end bar_end,
           b.source_record_id,b.source_revision,b.source_artifact_sha256,b.knowledge_from bar_known,
           t.knowledge_from state_known,t.observation_start state_start,t.observation_end state_end,
           t.is_tradable,t.can_buy,t.can_sell,t.reason_code,t.upper_price,t.lower_price
          FROM native_panel p LEFT JOIN dwd_security_bar b ON b.listing_id=p.listing_id
            AND b.calendar_session_id=p.calendar_session_id AND b.interval_size='1d'
            AND b.price_basis='UNADJUSTED'
          LEFT JOIN dwd_tradability_state t ON t.listing_id=p.listing_id
            AND t.calendar_session_id=p.calendar_session_id
          WHERE p.trade_date>=DATE '2020-01-02';
          CREATE INDEX daily_key ON daily(listing_id,trade_date);
          CREATE VIEW capital_execution AS SELECT * FROM daily;
          CREATE VIEW capital_actions AS SELECT * FROM dwd_corporate_action
            WHERE CAST(effective_at AS DATE) BETWEEN DATE '2020-01-02' AND DATE '2021-12-31';
          CREATE VIEW capital_terms AS SELECT * FROM dwd_corporate_action_term
            WHERE CAST(effective_at AS DATE) BETWEEN DATE '2020-01-02' AND DATE '2021-12-31';
          CREATE VIEW capital_quality AS SELECT * FROM dwd_quality_exception
            WHERE CAST(affected_effective_at AS DATE)
              BETWEEN DATE '2020-01-02' AND DATE '2021-12-31';""")
        if db.execute(
            "SELECT count(*) FROM (SELECT listing_id,trade_date FROM daily "
            "GROUP BY 1,2 HAVING count(*)<>1)"
        ).fetchone()[0]:
            raise ValueError("R10_NATIVE_ROW_DUPLICATION")
        self._build_industry()
        self._build_actions()
        db.execute("CHECKPOINT")
        write(
            self.root / "source-members.json",
            {
                "members": self.members,
                "content_digest": self.content_digest,
                "manifest_sha256": MANIFEST_SHA,
            },
        )

    def _build_industry(self) -> None:
        db = self.db
        nodes = {r["industry_node_id"]: r for r in rows(db, "SELECT * FROM dwd_industry_node")}
        grouped = defaultdict(list)
        for r in rows(
            db,
            "SELECT * FROM dwd_company_industry_membership "
            "WHERE membership_type='PRIMARY_CLASSIFICATION'",
        ):
            grouped[r["company_id"]].append(r)
        memberships = []
        for company, source in sorted(grouped.items()):
            last = max((r["source_declared_as_of"], r["effective_from"]) for r in source)
            selected = [
                r for r in source if (r["source_declared_as_of"], r["effective_from"]) == last
            ]
            ids = {r["industry_node_id"] for r in selected}
            if len(ids) != 1:
                continue
            node = nodes.get(next(iter(ids)))
            visited = set()
            while node and node["node_level"] > 1:
                if node["industry_node_id"] in visited:
                    raise ValueError("R10_INDUSTRY_CYCLE")
                visited.add(node["industry_node_id"])
                node = nodes.get(node["parent_node_id"])
            if node and node["node_level"] == 1:
                memberships.append(
                    (company, node["industry_node_id"], str(last[0]), digest(selected))
                )
        db.execute(
            "CREATE TABLE industry(company_id VARCHAR,industry_id VARCHAR,"
            "snapshot_as_of VARCHAR,evidence_digest VARCHAR)"
        )
        db.executemany("INSERT INTO industry VALUES (?,?,?,?)", memberships)

    def _industry_object(self) -> StaticIndustryMapping:
        revision = self.c["source"]["revision"]
        records = rows(
            self.db,
            "SELECT i.security_id,g.* FROM identities i JOIN industry g USING(company_id) "
            "ORDER BY security_id",
        )
        # A native security must have a single static concentration membership.
        unique = {}
        for r in records:
            if (
                r["security_id"] in unique
                and unique[r["security_id"]]["industry_id"] != r["industry_id"]
            ):
                raise ValueError("R10_INDUSTRY_SECURITY_AMBIGUITY")
            unique[r["security_id"]] = r
        version = "r10-csrc2012-static-concentration-v1"
        return StaticIndustryMapping(
            self.root / "industry.json",
            "1.0.0",
            version,
            IndustryMappingSource(
                "sealed-native", "dwd_company_industry_membership", self.cutoff, revision
            ),
            tuple(
                StaticIndustryMembership(
                    k, r["industry_id"], version, revision, r["evidence_digest"]
                )
                for k, r in sorted(unique.items())
            ),
            digest(records),
        )

    def _build_actions(self) -> None:
        db = self.db
        terms = defaultdict(list)
        for r in rows(db, "SELECT * FROM capital_terms ORDER BY corporate_action_term_id"):
            terms[r["corporate_action_id"]].append(r)
        actions = defaultdict(list)
        for r in rows(db, "SELECT * FROM capital_actions WHERE action_status='IMPLEMENTED'"):
            actions[r["security_id"], r["ex_date"]].append(r)
        identity = defaultdict(list)
        for r in rows(db, "SELECT listing_id,security_id FROM identities"):
            identity[r["security_id"]].append(r["listing_id"])
        db.execute("""CREATE TABLE source_corporate_actions_normalized_mapped(
           security_id VARCHAR,effective_date DATE,known_at TIMESTAMPTZ,
           cash_dividend_per_share DECIMAL(38,10),bonus_share_ratio DECIMAL(38,18));
           CREATE TABLE source_corporate_action_exceptions_mapped(
           security_id VARCHAR,blocked_from DATE,blocked_through DATE,reason_code VARCHAR);""")
        normal, exceptions = [], []
        for (security, day), group in sorted(
            actions.items(), key=lambda x: (x[0][0], str(x[0][1]))
        ):
            reason, item = None, None
            try:
                if day is None or len(group) != 1:
                    raise NativeAdapterError("ACTION_DATE_OR_MULTIPLE_EVENT_UNQUALIFIED")
                item = qualify_action(group[0], terms[group[0]["corporate_action_id"]])
            except NativeAdapterError as exc:
                reason = str(exc)
            for listing in identity[security]:
                if reason:
                    exceptions.append((listing, day or date(2020, 1, 2), day, reason))
                else:
                    assert item is not None
                    known = max(
                        c.knowledge_from for c in (item.source_clock, *item.term_source_clocks)
                    )
                    normal.append((listing, day, known, item.cash_per_share, item.shares_per_share))
        for q in rows(
            db, "SELECT * FROM capital_quality WHERE resolution_state NOT IN ('RESOLVED','CLOSED')"
        ):
            if q["affected_table"] not in {"dwd_corporate_action", "dwd_corporate_action_term"}:
                continue
            key = json.loads(q["affected_business_key_json"])
            codes = set(str(v) for v in key.values())
            for i in rows(db, "SELECT listing_id,security_id,listing_code FROM identities"):
                if codes & set(i.values()):
                    day = q["affected_effective_at"].date()
                    exceptions.append((i["listing_id"], day, day, q["quality_exception_id"]))
        if normal:
            db.executemany(
                "INSERT INTO source_corporate_actions_normalized_mapped VALUES (?,?,?,?,?)", normal
            )
        if exceptions:
            db.executemany(
                "INSERT INTO source_corporate_action_exceptions_mapped VALUES (?,?,?,?)", exceptions
            )

    def scan(self) -> Any:
        policy = ScopedPolicy(self)
        query = """SELECT d.*,g.industry_id FROM daily d LEFT JOIN industry g USING(company_id)
          WHERE d.trade_date<=DATE '2021-08-31' ORDER BY d.security_id,d.trade_date"""
        cursor = self.db.execute(query)
        columns = [r[0] for r in cursor.description]

        def all_rows():
            while batch := cursor.fetchmany(2048):
                for values in batch:
                    yield dict(zip(columns, values, strict=True))

        action_dates = defaultdict(set)
        unknown = set()
        # This separate connection preserves the active streaming cursor.
        other = __import__("duckdb").connect(
            str(self.root / "native.duckdb"), read_only=self.read_only
        )
        for security, day in other.execute(
            "SELECT security_id,ex_date FROM capital_actions WHERE action_status='IMPLEMENTED'"
        ).fetchall():
            if day is None:
                unknown.add(security)
            else:
                action_dates[security].add(day)
        other.close()

        def series():
            for security, group in groupby(all_rows(), key=lambda r: r["security_id"]):
                yield tuple(
                    CandidateDailyInput(
                        security_id=r["security_id"],
                        listing_id=r["listing_id"],
                        listing_code=r["listing_code"],
                        company_id=r["company_id"],
                        venue_id=self.venues[r["venue_id"]],
                        instrument_type=r["instrument_type"],
                        static_level1_industry_id=r["industry_id"],
                        session_date=r["trade_date"],
                        close_at=r["close_at"],
                        knowledge_cutoff=self.cutoff,
                        open_price=r["open"],
                        high_price=r["high"],
                        low_price=r["low"],
                        close_price=r["close"],
                        turnover=r["turnover"],
                        is_tradable=r["is_tradable"],
                        listing_sessions=r["listing_sessions"],
                        adjustment_effective=(
                            None
                            if security in unknown
                            else r["trade_date"] in action_dates[security]
                        ),
                        source_evidence_digest=digest(
                            {
                                "record": r["source_record_id"],
                                "revision": r["source_revision"],
                                "bar_known": str(r["bar_known"]),
                                "state_known": str(r["state_known"]),
                            }
                        ),
                    )
                    for r in group
                )

        return scan_forward_candidate_series(
            series(),
            trading_dates=[d for d in self.dates if date(2020, 1, 2) <= d <= date(2021, 9, 1)],
            snapshot_id=self.snapshot_id,
            snapshot_manifest_digest="sha256:" + MANIFEST_SHA,
            card=load_candidate_rule_card(
                self.project / "config/p4-candidate-price-volume-transition.research.v1.json"
            ),
            split_policy=policy,
            source_policy=policy,
        )

    def identity(
        self, listing: str, anchor: date, role: str, *, signal: date | None = None
    ) -> dict[str, Any]:
        i = self.identities[listing]
        days = [d for d in self.dates if d <= anchor][-40:]
        token = hashlib.sha256(
            f"R10-001|{i['company_id']}|{listing}|{anchor}|{role}".encode()
        ).hexdigest()
        return {
            k: i[k] for k in ("company_id", "security_id", "listing_id", "listing_code", "venue_id")
        } | {
            "role": role,
            "anchor": str(anchor),
            "signal_date": str(signal or anchor),
            "sample_id": "r10-" + token[:24],
            "episode_id": "r10-episode-" + token,
            "sessions": [
                {"date": str(d), "session_id": self.session_ids[i["venue_id"], d]} for d in days
            ],
            "legacy_exposure": "LEGACY_EXPOSURE_UNKNOWN",
        }

    def qualify_cases(self, identities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        quality = rows(
            self.db,
            "SELECT affected_table,affected_business_key_json,quality_exception_id,"
            "affected_effective_at FROM capital_quality "
            "WHERE resolution_state NOT IN ('RESOLVED','CLOSED')",
        )
        action_dates = defaultdict(list)
        for security, day in self.db.execute(
            "SELECT security_id,ex_date FROM capital_actions WHERE action_status='IMPLEMENTED'"
        ).fetchall():
            action_dates[security].append(day)
        result = []
        for identity in identities:
            native = rows(
                self.db,
                """SELECT c.trade_date market_date,c.calendar_session_id,c.open_at,c.close_at,
                d.high,d.low,d.close,d.volume,d.source_record_id,d.source_revision,d.source_artifact_sha256,
                d.bar_known knowledge_from,
                (d.currency='CNY' AND d.price_basis='UNADJUSTED' AND d.bar_start=c.open_at
                 AND d.bar_end=c.close_at
                 AND d.high>=d.close AND d.close>=d.low AND d.low>0) IS TRUE bar_ok,
                (d.is_tradable AND d.state_start=c.open_at
                 AND d.state_end=c.close_at) IS TRUE state_ok
                FROM calendar c LEFT JOIN daily d ON d.listing_id=?
                  AND d.calendar_session_id=c.calendar_session_id
                WHERE c.venue_id=? AND c.trade_date BETWEEN ?::DATE AND ?::DATE
                ORDER BY c.trade_date""",
                [
                    identity["listing_id"],
                    identity["venue_id"],
                    identity["sessions"][0]["date"],
                    identity["anchor"],
                ],
            )
            reasons = []
            if any(
                d is None or identity["sessions"][0]["date"] <= str(d) <= identity["anchor"]
                for d in action_dates[identity["security_id"]]
            ):
                reasons.append("native_action_or_unknown_date")
            for q in quality:
                if quality_blocks(
                    identity,
                    q["affected_table"],
                    json.loads(q["affected_business_key_json"]),
                    q["affected_effective_at"],
                ):
                    reasons.append("unresolved_required_input:" + q["quality_exception_id"])
            case = case_facts(
                identity,
                native,
                cutoff=self.cutoff,
                exchange=self.venues[identity["venue_id"]],
                request_digest=self.contract_digest,
                calendar_digest=digest(
                    [m for m in self.members if "/dwd_trading_calendar/" in m["path"]]
                ),
                action_digest=digest(
                    [m for m in self.members if "/dwd_corporate_action" in m["path"]]
                ),
                block_reasons=sorted(set(reasons)),
                include_primary=True,
            )
            case["admission_digest"] = digest(case.pop("admissions"))
            result.append(case)
        return result

    def close(self) -> None:
        self.db.close()
