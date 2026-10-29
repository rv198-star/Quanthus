"""Synthetic authentic links, misleading flags, offline closure and fail-closed IO."""

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from quanthus.expert_strategy import decision_chain as chain
from quanthus.expert_strategy import decision_review as review


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    reviews_root = tmp_path / "held-package"
    reviews_root.mkdir()
    cutoff = "2026-10-25T08:51:06.934678+00:00"
    entries = []
    for key, day, unit, allocated, filled in (
        ("e1", "2014-01-01", "u1", True, True),
        ("e2", "2014-01-02", "capital-pool", True, False),
        ("e3", "2014-01-02", "u2", True, False),
        ("e4", "2014-01-02", "u3", True, True),
    ):
        entries.append(
            dict(
                decision_key=key,
                market_date=day,
                unit_id=unit,
                surface="entry",
                formal_decision_digest="sha256:" + key,
                allocated=allocated,
                filled=filled,
                final_action="trial_entry",
                reason_codes=["retained"],
                aggregate_score="0.45000000000",
            )
        )
    decisions = entries + [
        dict(
            market_date="2014-01-03",
            unit_id="u1",
            decision_key="held:u1",
            surface="held",
            formal_decision_digest="sha256:h1",
            final_action="full_exit",
            allocated=False,
            filled=False,
        ),
        dict(
            market_date="2014-01-04",
            unit_id="u3",
            decision_key="held:u3",
            surface="held",
            formal_decision_digest="sha256:h3",
            final_action="no_action",
            allocated=False,
            filled=False,
        ),
    ]
    events = []

    def event(kind, day, payload, reason="retained"):
        events.append(
            dict(
                event_id=f"evt-{len(events)}",
                sequence=1,
                event_type=kind,
                event_time=day + "T15:00:00+08:00",
                reason_code=reason,
                payload=payload,
            )
        )

    for i, side, dd, filled in (
        (0, "buy", "2014-01-02", True),
        (2, "buy", "2014-01-03", False),
        (3, "buy", "2014-01-03", True),
        (4, "sell", "2014-01-04", True),
    ):
        d = decisions[i]
        common = dict(
            signal_id=f"signal{i}",
            unit_id=d["unit_id"],
            security_code="s1",
            side=side,
            quantity=100,
        )
        event(
            "signal_accepted",
            d["market_date"],
            {
                **common,
                "decision_at": d["market_date"] + "T15:01:00+08:00",
                "decision_version": "strategy-v1|" + d["formal_decision_digest"],
            },
        )
        event("order_intent_created", d["market_date"], {**common, "intent_id": f"intent{i}"})
        event("order_created", dd, {**common, "intent_id": f"intent{i}", "order_id": f"order{i}"})
        event(
            "order_status",
            dd,
            {"order_id": f"order{i}", "transition": "filled" if filled else "rejected"},
            "normal_buy_fill" if filled else "buy_blocked_limit_up",
        )
        if filled:
            event(
                "fill",
                dd,
                dict(
                    order_id=f"order{i}",
                    fill_id=f"fill{i}",
                    side=side,
                    quantity=100,
                    price="12.50000000000",
                    total_fees="0.000",
                    filled_at=dd + "T09:30:00+08:00",
                ),
            )
            event(
                "ledger_entry",
                dd,
                dict(
                    unit_id=d["unit_id"],
                    security_code="s1",
                    reference_type="fill",
                    reference_id=f"fill{i}",
                    postings=[{"account": "cash", "amount": "-1250.00000000000"}],
                ),
            )
    for day, unit, quantity, security in (
        ("2014-01-01", "u1", 0, None),
        ("2014-01-03", "u1", 100, "s1"),
        ("2014-01-04", "u1", 0, None),
        ("2014-01-04", "u3", 100, "s1"),
    ):
        event(
            "daily_snapshot",
            day,
            dict(
                unit_id=unit,
                session_date=day,
                security_code=security,
                position_quantity=quantity,
                cash="0.000",
            ),
        )
    event("unit_settlement", "2014-01-04", dict(unit_id="u1", settlement_id="settled1"))
    event(
        "ledger_entry",
        "2014-01-04",
        dict(
            unit_id="u1",
            security_code=None,
            reference_type="unit_close_settlement",
            reference_id="settled1",
        ),
    )
    event(
        "corporate_action",
        "2014-01-03",
        dict(action_id="ca1", security_code="s1", effective_date="2014-01-03", quantity_delta=10),
    )
    # A non-cash action is anchored by the original pending-order status, not guessed by code/date.
    event(
        "order_status",
        "2014-01-03",
        dict(order_id="order4", action_id="ca1", transition="adjusted", quantity_after=110),
    )
    events.reverse()  # Real exports are globally sorted, not local engine-call sequence groups.
    held = []
    for key, di in (("e1", 4), ("e4", 5)):
        d, entry = decisions[di], next(r for r in entries if r["decision_key"] == key)
        held.append(
            dict(
                identity=dict(
                    market_date=d["market_date"],
                    unit_id=d["unit_id"],
                    security_code="s1",
                    entry_key=key,
                    entry_date=entry["market_date"],
                ),
                snapshot=dict(
                    knowledge_cutoff=cutoff, historical_PIT_qualified=False, position_state="trial"
                ),
                baseline=dict(
                    baseline_decision_digest=d["formal_decision_digest"],
                    baseline_action=d["final_action"],
                    base_reason_code="retained",
                ),
                features=[dict(value=None, unavailable_reason="unknown")],
                structure=dict(available=False),
                sources={k: {"member_id": "m1"} for k in ("held", "trace", "shadow", "structure")},
            )
        )
    original = dict(
        variant_id="variant.B",
        source_manifest_sha256="sha256:original",
        production_authorized=False,
        scope=dict(
            decision_start="2012-01-04", decision_end="2021-12-30", execution_end="2021-12-31"
        ),
    )
    data = dict(
        decisions=decisions,
        events=events,
        coordinates={r["decision_key"]: "s1@" + r["market_date"] for r in entries},
        ledgers=[dict(unit_id=u, cash="100.000", security_code=None) for u in ("u1", "u2", "u3")],
        original_manifest=original,
        freeze={"test": "exact frozen fixture"},
        completion={"test": True},
        reconciliation=dict(
            reconciled=True,
            sealed_partition_access=False,
            ledger_count=sum(e["event_type"] == "ledger_entry" for e in events),
            decision_count=len(decisions),
            engine_event_count=len(events),
            fill_count=3,
        ),
    )
    paths = {}
    for role, value in data.items():
        path = source / (role + ".json")
        path.write_bytes(chain.encoded(value))
        paths[role] = path
    trace = dict(
        kind="expert545-unchanged-B-trace-v1",
        new_scientific_result=False,
        source_freeze_sha256=chain.sha(paths["freeze"].read_bytes()),
        trace_sha256="trace-digest",
        comparisons={
            name: dict(
                before_sha256=chain.sha(paths[role].read_bytes()),
                after_sha256=chain.sha(paths[role].read_bytes()),
                equal=True,
            )
            for name, role in (
                ("decisions.json", "decisions"),
                ("engine-events.json", "events"),
                ("manifest.json", "original_manifest"),
            )
        },
    )
    paths["trace_receipt"] = source / "trace_receipt.json"
    paths["trace_receipt"].write_bytes(chain.encoded(trace))
    rows = b"".join(chain.encoded(r) + b"\n" for r in held)
    paths["review_rows"] = reviews_root / "reviews.jsonl"
    paths["review_rows"].write_bytes(rows)
    review_manifest = dict(
        schema_version=review.SCHEMA,
        purpose=review.PURPOSE,
        execution_authorized=False,
        historical_PIT_qualified=False,
        consumer_admission="RESEARCH_DIAGNOSTIC_ONLY",
        scope=dict(first_date="2012-01-01", last_date="2021-12-31", source_cutoff=cutoff),
        retained_producer=dict(source_manifest_sha256="sha256:original"),
        files={"reviews.jsonl": dict(bytes=len(rows), sha256=chain.sha(rows))},
        members=[
            dict(
                id="m1",
                archive_path="/inert/trace/receipt.safe.json",
                sha256=chain.sha(paths["trace_receipt"].read_bytes()),
            ),
            dict(
                id="m2",
                archive_path="/inert/trace/held-contexts.private.jsonl.gz",
                sha256="trace-digest",
            ),
        ],
        census={"population": dict(reviews=2, episodes=2, issuers=1, unique_security_dates=2)},
    )
    paths["review_manifest"] = reviews_root / "manifest.json"
    paths["review_manifest"].write_bytes(chain.encoded(review_manifest))
    (reviews_root / "READY").write_text(chain.sha(paths["review_manifest"].read_bytes()) + "\n")
    selection = source / "selection.json"
    pins = {
        k: dict(path=str(p), bytes=p.stat().st_size, sha256=chain.sha(p.read_bytes()))
        for k, p in paths.items()
    }
    selection.write_bytes(chain.encoded(dict(roles=pins)))
    contract = dict(
        schema_version="quanthus.B-chain-contract.v1",
        purpose=chain.PURPOSE,
        roles={k: {f: v[f] for f in ("bytes", "sha256")} for k, v in pins.items()},
        selection_sha256=chain.sha(selection.read_bytes()),
        first_date="2012-01-01",
        last_date="2021-12-31",
        source_cutoff=cutoff,
        source_manifest_sha256="sha256:original",
        historical_PIT_qualified=False,
        execution_authorized=False,
        consumer_admission="RESEARCH_DIAGNOSTIC_ONLY",
        expected_counts=dict(
            entry_decisions=4,
            held_decisions=2,
            engine_events=len(events),
            filled_entries=2,
            filled_exits=1,
            review_rows=2,
        ),
        maximum_output_bytes=1024**2,
        minimum_free_bytes=0,
    )
    monkeypatch.setattr(chain, "load_contract", lambda root: contract)
    data.update(review_rows=held, review_manifest=review_manifest, trace_receipt=trace)
    output = tmp_path / "products" / "chain"
    output.parent.mkdir()
    return dict(
        source=source,
        output=output,
        selection=selection,
        contract=contract,
        paths=paths,
        data=data,
        root=tmp_path,
    )


def build(bundle):
    return chain.build_package(bundle["selection"], bundle["output"], bundle["root"])


def test_contract_remains_exact_retrospective_unadmitted():
    c = chain.load_contract(Path(__file__).resolve().parents[2])
    assert c["expected_counts"]["entry_decisions"] == 22955
    assert c["historical_PIT_qualified"] is False


def test_exact_chain_and_unobserved_allocation_are_distinct(bundle):
    receipt = build(bundle)
    manifest, data, index = chain.read_package(bundle["output"], receipt["manifest_sha256"])
    result = chain.query(data, index, security="s1", limit=100)
    by = {r["entry_key"]: r for r in result["rows"]}
    assert by["e1"]["observed_state"] == "FILLED_AND_EXITED"
    assert by["e2"]["observed_state"] == "NO_ORDER_OBSERVED"
    assert by["e2"]["entry_decision"]["recorded"]["allocated"] is True
    assert by["e2"]["unrecorded_rejection_reason"] == "NOT_RECORDED"
    assert by["e3"]["observed_state"] == "ORDER_OBSERVED_NOT_FILLED"
    assert any(e["recorded"]["reason_code"] == "buy_blocked_limit_up" for e in by["e3"]["events"])
    assert by["e4"]["observed_state"] == "FILLED_STILL_OPEN_AT_RUN_END"
    assert by["e4"]["held_reviews"][0]["recorded"]["features"][0]["value"] is None
    assert by["e4"]["held_decisions"][0]["recorded"]["final_action"] == "no_action"
    fill = next(e["recorded"] for e in by["e1"]["events"] if e["recorded"]["event_type"] == "fill")
    assert fill["payload"]["price"] == "12.50000000000"
    assert fill["payload"]["filled_at"] == "2014-01-02T09:30:00+08:00"
    assert len(index["unassigned_events"]) == 1
    assert manifest["stages"]["replays"] == 0
    assert all(e["recorded"]["payload"].get("unit_id", "u1") != "u3" for e in by["e1"]["events"])
    assert chain.query(data, index, unit="u1", day="2014-01-04")["matched"] == 1
    assert chain.query(data, index, episode="missing")["matched"] == 0


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("duplicate_event", "CHAIN_DUPLICATE_EVENT"),
        ("wrong_coordinate", "CHAIN_ENTRY_COORDINATE"),
        ("held_digest", "CHAIN_REVIEW_DECISION_BINDING"),
        ("signal_unit", "CHAIN_SIGNAL_DECISION"),
        ("unknown_order", "CHAIN_FILL_REFERENCE"),
        ("action_anchor", "CHAIN_ACTION_UNIT_ANCHOR"),
        ("unknown_event", "CHAIN_UNKNOWN_EVENT_TYPE"),
        ("missing_held", "CHAIN_HELD_DENOMINATOR"),
    ],
)
def test_conflicting_or_missing_links_rejected(bundle, mutation, reason):
    d = deepcopy(bundle["data"])
    if mutation == "duplicate_event":
        d["events"].append(deepcopy(d["events"][0]))
    elif mutation == "wrong_coordinate":
        d["coordinates"]["e1"] = "s1@2014-01-03"
    elif mutation == "held_digest":
        d["review_rows"][0]["baseline"]["baseline_decision_digest"] = "wrong"
    elif mutation == "signal_unit":
        next(e for e in d["events"] if e["event_type"] == "signal_accepted")["payload"][
            "unit_id"
        ] = "wrong"
    elif mutation == "unknown_order":
        next(e for e in d["events"] if e["event_type"] == "fill")["payload"]["order_id"] = "wrong"
    elif mutation == "action_anchor":
        next(
            e
            for e in d["events"]
            if e["payload"].get("action_id") == "ca1" and e["event_type"] == "order_status"
        )["payload"].pop("action_id")
    elif mutation == "unknown_event":
        d["events"][0]["event_type"] = "unknown"
    else:
        d["review_rows"].pop()
    with pytest.raises(chain.DecisionChainError, match=reason):
        chain.project(d)


@pytest.mark.parametrize("mutation", ["alter", "delete", "symlink"])
def test_input_failure_precedes_any_output(bundle, mutation):
    path = bundle["paths"]["events"]
    if mutation == "alter":
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "delete":
        path.unlink()
    else:
        saved = path.with_suffix(".saved")
        path.rename(saved)
        path.symlink_to(saved)
    with pytest.raises((ValueError, OSError)):
        build(bundle)
    assert not bundle["output"].exists()


def test_output_overlap_and_second_build_never_change_source(bundle):
    before = {k: p.read_bytes() for k, p in bundle["paths"].items()}
    with pytest.raises(ValueError, match="CHAIN_OUTPUT_SOURCE_OVERLAP"):
        chain.build_package(bundle["selection"], bundle["source"] / "new", bundle["root"])
    assert {k: p.read_bytes() for k, p in bundle["paths"].items()} == before
    build(bundle)
    with pytest.raises(ValueError, match="CHAIN_OUTPUT_EXISTS"):
        build(bundle)


def test_ready_is_last_and_partial_write_has_no_ready(bundle, monkeypatch):
    original = chain._write
    names = []

    def write(path, value):
        names.append(path.name)
        original(path, value)
        if path.name == "events.json":
            raise OSError("synthetic interruption")

    monkeypatch.setattr(chain, "_write", write)
    with pytest.raises(OSError, match="synthetic interruption"):
        build(bundle)
    assert names[-1] == "events.json"
    assert not (bundle["output"] / "READY").exists()


@pytest.mark.parametrize("mutation", ["corrupt", "missing", "extra", "hardlink", "index"])
def test_cold_reader_rejects_physical_and_repacked_semantic_corruption(bundle, mutation):
    receipt = build(bundle)
    root = bundle["output"]
    if mutation == "corrupt":
        (root / "events.json").write_bytes(b"broken")
    elif mutation == "missing":
        (root / "events.json").unlink()
    elif mutation == "extra":
        (root / "extra.json").write_text("{}")
    elif mutation == "hardlink":
        (root.parent / "linked.json").hardlink_to(root / "events.json")
    else:
        i = json.loads((root / "index.json").read_bytes())
        i["nodes"]["e2"]["events"] = i["nodes"]["e1"]["events"]
        (root / "index.json").write_bytes(chain.encoded(i))
        m = json.loads((root / "manifest.json").read_bytes())
        m["files"]["index.json"] = dict(
            bytes=(root / "index.json").stat().st_size,
            sha256=chain.sha((root / "index.json").read_bytes()),
        )
        (root / "manifest.json").write_bytes(chain.encoded(m))
        receipt["manifest_sha256"] = chain.sha((root / "manifest.json").read_bytes())
        (root / "READY").write_text(receipt["manifest_sha256"] + "\n")
    with pytest.raises(ValueError):
        chain.read_package(root, receipt["manifest_sha256"])


def test_relocated_package_without_sources_remains_readable(bundle):
    receipt = build(bundle)
    destination = bundle["root"] / "relocated"
    shutil.copytree(bundle["output"], destination)
    shutil.rmtree(bundle["source"])
    shutil.rmtree(bundle["paths"]["review_manifest"].parent)
    manifest, data, index = chain.read_package(destination, receipt["manifest_sha256"])
    assert chain.query(data, index, episode="e1")["matched"] == 1
    assert manifest["execution_authorized"] is False


def test_query_limits_html_escape_and_no_source_writes(bundle):
    receipt = build(bundle)
    manifest, data, index = chain.read_package(bundle["output"], receipt["manifest_sha256"])
    with pytest.raises(ValueError, match="CHAIN_QUERY_LIMIT"):
        chain.query(data, index, limit=101)
    result = chain.query(data, index, limit=1)
    result["rows"][0]["security_code"] = "<script>alert('x')</script>"
    page = chain.render_html(result, manifest, receipt["manifest_sha256"])
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "Content-Security-Policy" in page and "未知" in page
    with pytest.raises(ValueError, match="CHAIN_QUERY_SOURCE_OVERLAP"):
        chain.write_query(bundle["source"] / "query.json", b"{}", bundle["output"], manifest)
    target = bundle["root"] / "view.html"
    chain.write_query(target, page.encode(), bundle["output"], manifest)
    with pytest.raises(FileExistsError):
        chain.write_query(target, b"replace", bundle["output"], manifest)


def test_shanghai_date_and_naive_time_boundary():
    assert chain.business_day("2014-01-01T16:01:00+00:00") == "2014-01-02"
    with pytest.raises(ValueError, match="CHAIN_NAIVE_TIME"):
        chain.business_day("2014-01-01T16:01:00")
