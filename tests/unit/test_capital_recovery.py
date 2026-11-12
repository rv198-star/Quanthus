"""Synthetic recovery failures and whole-window independent cents reconciliation."""

from __future__ import annotations

import copy
import importlib.util
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.backtest.capital_clock import CapitalReplayClock, bind_capital_action_context
from quanthus.backtest.capital_purpose import effective_event, load_purpose
from quanthus.hf_dwd_research import digest, file_digest, write

ROOT = Path(__file__).resolve().parents[2]
KNOWN = "2026-11-12T05:28:23+00:00"
NATIVE = "2026-11-11T20:19:54+00:00"
CUTOFF = "2026-11-12T06:00:00+00:00"


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def make_package(tmp_path, q, actions, terms):
    """Entirely invented rows: no frozen market evidence is committed as tests."""
    root = tmp_path / "purpose"
    root.mkdir()

    def plain(row):
        return {
            k: str(v) if isinstance(v, (date, datetime, Decimal)) else v for k, v in row.items()
        }

    original = {
        "dwd_corporate_action": [plain(a) for a in actions],
        "dwd_corporate_action_term": [plain(t) for t in terms],
    }
    sources = [{"sha256": a["source_artifact_sha256"].removeprefix("sha256:")} for a in actions]
    docs = {
        "source-records": original,
        "source-receipts": {"source_hash_probe": sources},
        "f10-exact-block": {"raw_block": "2019-12-31 10派1元(含税) 实施公告2020-03-02"},
        "f10-parser-proof": {
            "matched_record": {
                "stock_code": "000001",
                "ex_date": "2020-03-04",
                "record_date": "2020-03-03",
                "payment_date": "2020-03-04",
                "cash_per_share": "0.1",
                "source_row_key": "F10-raw",
            }
        },
        "em-parser-proof": {
            "raw": {
                "SECURITY_CODE": "000001",
                "EX_DIVIDEND_DATE": "2020-03-04 00:00:00",
                "EQUITY_RECORD_DATE": "2020-03-03 00:00:00",
                "NOTICE_DATE": "2020-03-02 00:00:00",
                "REPORT_DATE": "2019-12-31 00:00:00",
                "PRETAX_BONUS_RMB": "1",
                "IMPL_PLAN_PROFILE": "10派1元(含税,扣税后0.9元)",
            }
        },
        "history-identity-proof": {
            "exact_matches": 1,
            "raw_row": {
                "stock_code": "000001",
                "ex_dividend_date": "2020-03-04",
                "announce_date": "2020-03-02",
                "progress": "实施",
            },
            "reconstructed_action_id": "B",
            "reconstructed_source_record_id": "HISTORY-raw",
        },
        "independent-review": {"verdict": "pass"},
    }
    for name, value in docs.items():
        write(root / (name + ".json"), value)
    scope = {
        "use": "guarded_retrospective_capital_only",
        "uncovered_policy": "original_native_guard",
        "security_id": "S1",
        "listing_id": "L1",
        "stock_code": "000001",
        "ex_date": "2020-03-04",
        "covered_latest_actions": sorted(a["corporate_action_id"] for a in actions),
        "covered_latest_terms": sorted(t["corporate_action_term_id"] for t in terms),
    }
    event = {
        "economic_event_id": "synthetic-effective-cash",
        "ex_date": "2020-03-04",
        "effective_at": "2020-03-04T23:59:59+08:00",
        "record_date": "2020-03-03",
        "payment_date": "2020-03-04",
        "implementation_notice_date": "2020-03-02",
        "report_date": "2019-12-31",
        "action_type": "CASH_DIVIDEND",
        "currency": "CNY",
        "cash_per_share": "0.1",
        "bonus_shares": None,
        "transfer_shares": None,
    }
    m = {
        "schema": "quanthus.p10-capital-purpose.v1",
        "status": "qualified_scoped_retrospective",
        "native_authority": q["authority"],
        "purpose_version": "synthetic-v1",
        "observed_at": KNOWN,
        "qualified_at": KNOWN,
        "knowledge_from": KNOWN,
        "knowledge_to": None,
        "review_roles": {"review_status": "pass_after_package_completion"},
        "scope": scope,
        "effective_event": event,
        "rule": {"scope": "synthetic-only"},
        "sources": sources,
        "source_relations": [
            {"action_id": a["corporate_action_id"], "source_record_id": a["source_record_id"]}
            for a in actions
        ],
        "term_relations": [
            {
                "term_id": t["corporate_action_term_id"],
                "action_id": t["corporate_action_id"],
                "source_record_id": t["source_record_id"],
                "term_type": t["term_type"],
                "original_amount_per_share": str(t["amount_per_share"])
                if t["amount_per_share"] is not None
                else None,
                "original_ratio_numerator": str(t["ratio_numerator"])
                if t["ratio_numerator"] is not None
                else None,
                "original_ratio_denominator": str(t["ratio_denominator"])
                if t["ratio_denominator"] is not None
                else None,
                "effective_event_id": event["economic_event_id"],
            }
            for t in terms
        ],
    }
    m["rule_digest"] = digest(m["rule"])
    m["documents"] = {key: key + ".json" for key in docs}
    m["members"] = [
        {"path": p.name, "bytes": p.stat().st_size, "sha256": file_digest(p)}
        for p in sorted(root.iterdir())
    ]
    write(root / "purpose.json", m)
    q.update(
        schema="quanthus.native-capital-request.v2",
        request_id="RESEARCH-CAPITAL-002",
        task_id="RESEARCH-CAPITAL-002-T1",
        attempt_id="A1",
        source_knowledge_cutoff=CUTOFF,
        native_knowledge_cutoff=NATIVE,
    )
    q["purpose"] = {
        "revision": "b" * 40,
        "qualified_at": KNOWN,
        "purpose_path": "purpose.json",
        "purpose_sha256": file_digest(root / "purpose.json"),
        "members": [
            {"path": p.name, "bytes": p.stat().st_size, "sha256": file_digest(p)}
            for p in sorted(root.iterdir())
        ],
    }
    q["effective_input"] = {
        "content_digest": "sha256:" + "c" * 64,
        "manifest_sha256": "sha256:" + "d" * 64,
        "source_revision": "synthetic-composite",
        "clock_identity": {
            "native_content_digest": q["authority"]["content_digest"],
            "purpose_sha256": q["purpose"]["purpose_sha256"],
            "consumer_closure_sha256": "sha256:" + "e" * 64,
            "native_knowledge_cutoff": NATIVE,
            "purpose_qualified_at": KNOWN,
        },
    }
    return root


@pytest.fixture
def package(tmp_path):
    q = json.loads((ROOT / "config/research/research-capital/request.v1.json").read_text())
    actions = []
    terms = []
    for action_id, source in [("A", "F10-raw"), ("B", "HISTORY-raw"), ("C", "EM-raw")]:
        a = {
            "corporate_action_id": action_id,
            "security_id": "S1",
            "action_type": "CASH_DIVIDEND",
            "action_status": "IMPLEMENTED",
            "effective_at": datetime.fromisoformat("2020-03-04T23:59:59+08:00"),
            "ex_date": date(2020, 3, 4),
            "announced_at": None,
            "knowledge_from": datetime.fromisoformat(NATIVE),
            "knowledge_to": None,
            "source_record_id": source,
            "source_revision": "synthetic-native",
            "source_artifact_sha256": "sha256:" + action_id.lower() * 64,
        }
        actions.append(a)
        terms.append(
            {
                **a,
                "corporate_action_term_id": "T" + action_id,
                "term_type": "CASH_AMOUNT_PER_SHARE",
                "currency": "CNY",
                "amount_per_share": Decimal("1") if action_id == "B" else Decimal(".1"),
                "ratio_numerator": None,
                "ratio_denominator": None,
                "option_id": None,
            }
        )
    root = make_package(tmp_path, q, actions, terms)
    return q, root, actions, terms


def refresh_manifest(q, root, mutate):
    m = json.loads((root / "purpose.json").read_text())
    mutate(m)
    write(root / "purpose.json", m)
    q["purpose"]["purpose_sha256"] = file_digest(root / "purpose.json")
    for item in q["purpose"]["members"]:
        if item["path"] == "purpose.json":
            item.update(
                bytes=(root / "purpose.json").stat().st_size,
                sha256=file_digest(root / "purpose.json"),
            )


def test_exact_purpose_builds_one_nonadditive_cash_event(package):
    q, root, actions, terms = package
    purpose = load_purpose(q, root)
    old = copy.deepcopy(actions)
    grouped = {
        a["corporate_action_id"]: [
            t for t in terms if t["corporate_action_id"] == a["corporate_action_id"]
        ]
        for a in actions
    }
    header, atoms, receipt = effective_event(q, purpose, actions, grouped)
    assert header["knowledge_from"] == datetime.fromisoformat(KNOWN)
    assert header["announced_at"] is None
    assert [t["amount_per_share"] for t in atoms] == [Decimal(".1")]
    assert receipt["source_action_ids"] == ["A", "B", "C"] and actions == old


@pytest.mark.parametrize(
    "change", ["native", "future", "scope", "duplicate", "unknown_mapping", "unit", "ex_date"]
)
def test_semantic_failures_reject_even_after_new_outer_hash(package, change):
    q, root, actions, terms = package

    def mutate(m):
        if change == "native":
            m["native_authority"] = {}
        elif change == "future":
            m["knowledge_from"] = m["qualified_at"] = "2027-01-01T00:00:00+00:00"
        elif change == "scope":
            m["scope"]["security_id"] = "OTHER"
        elif change == "duplicate":
            m["source_relations"].append(m["source_relations"][0])
        elif change == "unknown_mapping":
            m["term_relations"][0]["effective_event_id"] = "UNKNOWN"
        elif change == "unit":
            m["effective_event"]["cash_per_share"] = "1"
        elif change == "ex_date":
            m["effective_event"]["ex_date"] = None

    refresh_manifest(q, root, mutate)
    with pytest.raises((ValueError, TypeError)):
        purpose = load_purpose(q, root)
        effective_event(
            q,
            purpose,
            actions,
            {
                a["corporate_action_id"]: [
                    t for t in terms if t["corporate_action_id"] == a["corporate_action_id"]
                ]
                for a in actions
            },
        )


def test_byte_tamper_stops_before_interpretation(package):
    q, root, _, _ = package
    (root / "source-records.json").write_text("{}")
    with pytest.raises(ValueError, match="MEMBER"):
        load_purpose(q, root)


def test_per_share_text_cannot_be_relabelled_per_ten(package, monkeypatch):
    q, root, _, _ = package
    path = root / "f10-exact-block.json"
    write(path, {"raw_block": "每股派1元(含税)"})

    def mutate(m):
        for item in m["members"]:
            if item["path"] == path.name:
                item.update(bytes=path.stat().st_size, sha256=file_digest(path))

    for item in q["purpose"]["members"]:
        if item["path"] == path.name:
            item.update(bytes=path.stat().st_size, sha256=file_digest(path))
    refresh_manifest(q, root, mutate)
    with pytest.raises(ValueError, match="EXPLICIT_TEN_SHARE_TEXT"):
        load_purpose(q, root)
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from research_capital_independent import reference_purpose

    with pytest.raises(AssertionError):
        reference_purpose(q, root)


def recovery_clock():
    return CapitalReplayClock(
        date(2021, 8, 4),
        datetime.fromisoformat(CUTOFF),
        "sha256:" + "c" * 64,
        "RESEARCH-CAPITAL-002",
        "RESEARCH-CAPITAL-002-T1",
        "A1",
        json.dumps(
            {
                "native_content_digest": "sha256:" + "a" * 64,
                "purpose_sha256": "sha256:" + "b" * 64,
                "consumer_closure_sha256": "sha256:" + "e" * 64,
                "native_knowledge_cutoff": NATIVE,
                "purpose_qualified_at": KNOWN,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def test_recovery_clock_roundtrip_and_attempt_isolation():
    c = recovery_clock()
    assert CapitalReplayClock.from_json(c.to_json()) == c
    assert c.to_dict()["task_id"] == "RESEARCH-CAPITAL-002-T1"
    with pytest.raises(ValueError, match="matching parent"):
        bind_capital_action_context(replace(c, attempt_id="A2").to_json(), c.to_json())


def test_recovery_context_rejects_official_default_and_wrong_attempt():
    spec = importlib.util.spec_from_file_location(
        "clock_helper", Path(__file__).with_name("test_native_capital_replay.py")
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    c = replace(recovery_clock(), market_date=helper.DAY)
    snap = replace(helper.snapshot(), knowledge_cutoff=c.knowledge_cutoff)
    signal = replace(helper.signal(), snapshot=snap, snapshot_replay_clock_json=c.to_json())
    run = replace(
        helper.run(),
        data_content_digest=c.data_content_digest,
        run_id="RESEARCH-CAPITAL-002-T1-A1-candidate",
    )
    session = helper.session(helper.DAY + timedelta(days=1))
    engine = helper.BacktestEngine(helper.policy())
    state = helper.UnitState.empty(unit_id="U", cash=Decimal("100000.00"))
    assert engine.run_buy_session(run=run, signal=signal, session=session, opening_state=state)
    for changed in [
        replace(run, official=True),
        replace(run, run_id="RESEARCH-CAPITAL-002-T1-A2-candidate"),
    ]:
        with pytest.raises(helper.BacktestInputError):
            engine.run_buy_session(run=changed, signal=signal, session=session, opening_state=state)
    with pytest.raises(ValueError):
        replace(signal, snapshot_replay_clock_json=None)


def test_formal_guardian_failure_cannot_reach_adoption(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from research_capital_independent import admit_author

    q = {"schema": "quanthus.native-capital-request.v2"}
    identity = {"request_digest": digest(q), "git": "synthetic"}
    result = {"identity": identity, "decision": {"branch": "advance"}}
    result["digest"] = digest(result)
    write(tmp_path / "result.json", result)
    with pytest.raises(FileNotFoundError):
        admit_author(q, tmp_path)
    guardian = {
        "identity": identity,
        "mode": "formal",
        "exit_code": 0,
        "failure": None,
        "output_digest": result["digest"],
    }
    for update in [
        {"exit_code": 1},
        {"failure": "wall_exceeded"},
        {"output_digest": "wrong"},
        {"mode": "independent"},
    ]:
        write(tmp_path / "guardian.json", {**guardian, **update})
        with pytest.raises(AssertionError):
            admit_author(q, tmp_path)
    write(tmp_path / "guardian.json", guardian)
    assert admit_author(q, tmp_path) == result


def test_missing_price_bound_preserves_native_direction_facts():
    from quanthus.backtest.contracts import OpenExecutionState
    from quanthus.backtest.native_capital_replay import CapitalInputHold, NativeCapitalStore

    day = date(2021, 8, 4)
    store = NativeCapitalStore.__new__(NativeCapitalStore)
    store.identities = {"L": {"venue_id": "V"}}
    store.calendars = {
        ("V", day): {
            "open_at": datetime(2021, 8, 4, 1, 30, tzinfo=timezone.utc),
            "close_at": datetime(2021, 8, 4, 7, tzinfo=timezone.utc),
        }
    }
    row = {
        "listing_currency": "CNY",
        "lot_size": 100,
        "is_tradable": True,
        "can_buy": True,
        "can_sell": False,
        "reason_code": "TRADABLE_OBSERVED",
        "open": Decimal(10),
        "close": Decimal(10),
        "upper_price": None,
        "lower_price": Decimal(9),
    }
    row.update(
        {
            key: datetime.fromisoformat(NATIVE)
            for key in ("bar_known", "state_known", "listing_known", "security_known")
        }
    )
    store.native_cache = {("L", day): row}
    store.cutoff = datetime.fromisoformat(CUTOFF)
    store.native_revision = "synthetic"
    store.run_prefix = "RESEARCH-CAPITAL-002-T1-A1"
    store.accesses, store.arm = [], "candidate"
    s = store.session("L", day)
    assert s.open_execution_state is OpenExecutionState.MISSING_EVIDENCE
    assert (s.source_can_buy, s.source_can_sell) == (True, False)
    row["bar_known"] = None
    with pytest.raises(CapitalInputHold, match="SOURCE_KNOWLEDGE_UNKNOWN"):
        store.session("L", day)


def test_full_recovery_and_independent_decimal_path(tmp_path, monkeypatch):
    duckdb = pytest.importorskip("duckdb")
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    helper_spec = importlib.util.spec_from_file_location(
        "synthetic_helper", Path(__file__).with_name("test_native_capital_pipeline.py")
    )
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    members = helper.synthetic_source(tmp_path, extra_calendar_venue=True)
    source = tmp_path / "source/DWD/payload"
    db = duckdb.connect()
    db.execute("SET TimeZone='Asia/Shanghai'")
    paths = {}
    for table in ("dwd_corporate_action", "dwd_corporate_action_term"):
        path = source / table / "all/part.parquet"
        paths[table] = path
        db.execute("CREATE TABLE " + table + " AS SELECT * FROM read_parquet(?)", [str(path)])
        for col in ("source_record_id", "source_revision", "source_artifact_sha256"):
            db.execute("ALTER TABLE " + table + " ADD COLUMN " + col + " VARCHAR")
        db.execute(
            "UPDATE " + table + " SET source_record_id='F10-raw',source_revision='synthetic',"
            "source_artifact_sha256=?",
            ["sha256:" + "a" * 64],
        )
    db.execute(
        "INSERT INTO dwd_corporate_action SELECT * REPLACE ('B' AS corporate_action_id,"
        "'HISTORY-raw' AS source_record_id) FROM dwd_corporate_action WHERE corporate_action_id='A'"
    )
    db.execute(
        "INSERT INTO dwd_corporate_action SELECT * REPLACE ('C' AS corporate_action_id,"
        "'EM-raw' AS source_record_id) FROM dwd_corporate_action WHERE corporate_action_id='A'"
    )
    db.execute(
        "INSERT INTO dwd_corporate_action_term SELECT * REPLACE ('TB' AS corporate_action_term_id,"
        "'B' AS corporate_action_id,'HISTORY-raw' AS source_record_id,1 AS amount_per_share) "
        "FROM dwd_corporate_action_term WHERE corporate_action_id='A'"
    )
    db.execute(
        "INSERT INTO dwd_corporate_action_term SELECT * REPLACE ('TC' AS corporate_action_term_id,"
        "'C' AS corporate_action_id,'EM-raw' AS source_record_id) "
        "FROM dwd_corporate_action_term WHERE corporate_action_id='A'"
    )
    values = {}
    for table, path in paths.items():
        cursor = db.execute("SELECT * FROM " + table + " ORDER BY corporate_action_id")
        values[table] = [
            dict(zip([c[0] for c in cursor.description], row, strict=True))
            for row in cursor.fetchall()
        ]
        db.execute("COPY " + table + " TO '" + str(path) + "' (FORMAT PARQUET)")
    db.close()
    q = json.loads((ROOT / "config/research/research-capital/request.v1.json").read_text())
    make_package(tmp_path, q, values["dwd_corporate_action"], values["dwd_corporate_action_term"])
    helper.module().formal(q, members, {"git": "synthetic-only"}, tmp_path)
    result = json.loads((tmp_path / "result.json").read_text())
    from research_capital_independent import IndependentFacts, referee

    facts = IndependentFacts(tmp_path / "source/DWD", members, result["nominal_choices"], q)
    assert facts.purpose_application == result["purpose_application"]
    costs = json.loads((ROOT / q["policy"]["path"]).read_text())
    for name in ("candidate", "control"):
        other = referee(facts, result["nominal_choices"], name, costs)
        assert other["status"] == result["arms"][name]["status"] == "completed"
        assert other["p2_step_receipts"] == result["arms"][name]["p2_step_receipts"]
        assert Decimal(other["ending_wealth"]) == Decimal(
            result["arms"][name]["result"]["performance"]["ending_equity"]
        )
        assert result["arms"][name]["result"]["run_id"].startswith("RESEARCH-CAPITAL-002-T1-A1")
    facts.db.close()
