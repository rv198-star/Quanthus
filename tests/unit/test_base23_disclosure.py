from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base23_disclosure as m
from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from tests.unit.test_base22_independent_image import _row

SHA = "sha256:" + "a" * 64
CLOCK = datetime(2026, 10, 9, tzinfo=timezone.utc)


def case():
    _, model, ext = load_contract(Path("config/pipelines"))
    ss = schemas(model, ext)
    listing = _row(ss["dwd_listing"])
    security = _row(ss["dwd_security"])
    listing.update(listing_code="sz.002800", security_id="security-synthetic")
    security.update(security_id="security-synthetic", company_id="company-synthetic")
    for r in (listing, security):
        r.update(
            effective_from=datetime(2010, 1, 1, tzinfo=timezone.utc),
            effective_to=None,
            knowledge_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
            knowledge_to=None,
        )
    raw = dict(
        security_id="002800",
        stock_code="002800",
        ann_id="synthetic-doc",
        title="synthetic retained document",
        publish_time="2026-08-28 08:00:00",
        announce_date="2026-08-28",
        name="synthetic issuer",
        url="https://example.invalid/doc",
        source="cninfo.hisAnnouncement",
        acquired_at="2026-09-02T19:30:04+08:00",
    )
    return raw, dict(listings=[listing], securities=[security], observed_at=CLOCK, ss=ss)


def test_latest_capture_revision_preserves_older_raw_clocks_and_observes_now(tmp_path):
    raw, args = case()
    later = dict(raw, title="synthetic revised document", acquired_at="2026-09-03T19:30:04+08:00")
    before = deepcopy([raw, later])
    selected, held, union = m.select_documents(
        [(SHA, "v1", [raw]), (SHA, "v2", [later])], observed_at=CLOCK
    )
    assert not held and union["census"]["revised_capture_keys"] == 1
    rows, proof = m.project_documents(selected, **args)
    assert all(r["knowledge_from"] == CLOCK for v in rows.values() for r in v)
    assert rows[m.TABLES[0]][0]["effective_at"] == datetime(2026, 8, 28, tzinfo=timezone.utc)
    assert rows[m.TABLES[0]][0]["title"] == later["title"]
    assert len(proof["proofs"][0]["captures"]) == 2
    for table, values in rows.items():
        p = tmp_path / (table + ".parquet")
        typed = pa.Table.from_pylist(values, schema=args["ss"][table])
        pq.write_table(typed, p)
        assert pq.ParquetFile(p).read().equals(typed)
    assert [raw, later] == before


@pytest.mark.parametrize("fault", ["same-clock", "code-change"])
def test_conflicting_clock_or_identity_holds_only_affected_document(fault):
    raw, _ = case()
    revised = dict(raw, title="changed")
    if fault == "code-change":
        revised.update(
            stock_code="002801", security_id="002801", acquired_at="2026-09-03T19:30:04+08:00"
        )
    unaffected = dict(raw, ann_id="other-doc")
    selected, held, _ = m.select_documents(
        [(SHA, "v1", [raw, unaffected]), (SHA, "v2", [revised])], observed_at=CLOCK
    )
    assert len(selected) == len(held) == 1
    assert selected[0]["raw"]["ann_id"] == "other-doc"


@pytest.mark.parametrize(
    "fault",
    [
        "missing-acquisition",
        "future-acquisition",
        "october",
        "unknown-source",
        "unknown-field",
        "vendor-identity",
    ],
)
def test_raw_input_failures_cannot_become_qualified_documents(fault):
    raw, _ = case()
    if fault == "missing-acquisition":
        raw["acquired_at"] = None
    elif fault == "future-acquisition":
        raw["acquired_at"] = "2026-10-10T00:00:00+08:00"
    elif fault == "october":
        raw["announce_date"] = "2026-10-01"
    elif fault == "unknown-source":
        raw["source"] = "unknown"
    elif fault == "unknown-field":
        raw["extra"] = "unsupported"
    elif fault == "vendor-identity":
        raw["security_id"] = "wrong"
    with pytest.raises(RuntimeError):
        m.select_documents([(SHA, "v1", [raw])], observed_at=CLOCK)


@pytest.mark.parametrize("fault", ["unlisted", "future-parent", "duplicate-parent"])
def test_missing_dated_native_identity_never_gets_generic_alias_fill(fault):
    raw, args = case()
    selected, _, _ = m.select_documents([(SHA, "v1", [raw])], observed_at=CLOCK)
    if fault == "unlisted":
        args["listings"][0]["effective_to"] = datetime(2026, 8, 1, tzinfo=timezone.utc)
    elif fault == "future-parent":
        args["securities"][0]["knowledge_from"] = datetime(2026, 10, 10, tzinfo=timezone.utc)
    else:
        args["listings"] *= 2
    rows, proof = m.project_documents(selected, **args)
    assert not any(rows.values()) and len(proof["holds"]) == 1


def test_submicrosecond_publication_is_not_truncated():
    raw, args = case()
    raw["publish_time"] = "2026-08-28 08:00:00.1234567"
    selected, _, _ = m.select_documents([(SHA, "v1", [raw])], observed_at=CLOCK)
    with pytest.raises(BoundedError, match="PRECISION"):
        m.project_documents(selected, **args)


def scope_fixture(tmp_path, monkeypatch):
    import os
    import shutil
    from types import SimpleNamespace

    from quanthus.pipelines.securities_warehouse.base22 import (
        canonical,
        digest,
        file_sha,
        partition,
    )
    from quanthus.pipelines.securities_warehouse.base22_bounded import semantic_scan
    from quanthus.pipelines.securities_warehouse.base22_sources import _tree
    from quanthus.pipelines.securities_warehouse.base23_disclosure_audit import PARENTS

    root = tmp_path / "scope"
    root.mkdir()
    original = os.statvfs

    def statvfs(path):
        x = original(path)
        if Path(path) != root:
            return x
        return SimpleNamespace(
            **{k: getattr(x, k) for k in dir(x) if k.startswith("f_") and k != "f_flag"},
            f_flag=x.f_flag | os.ST_RDONLY,
        )

    monkeypatch.setattr(os, "statvfs", statvfs)
    raw, args = case()
    c, model, ext = load_contract(Path("config/pipelines"))
    ss = schemas(model, ext)
    (root / "contract").mkdir()
    for name in [
        "base22-independent-image.v1.json",
        "dwd-domain-model.v1.json",
        "dwd-domain-model.v1.1-extension.json",
    ]:
        shutil.copyfile(Path("config/pipelines") / name, root / "contract" / name)
    rows = {"dwd_listing": args["listings"][0], "dwd_security": args["securities"][0]}
    rows["dwd_listed_company"] = dict(
        _row(ss["dwd_listed_company"]), company_id=args["securities"][0]["company_id"]
    )
    rows["dwd_trading_venue"] = dict(
        _row(ss["dwd_trading_venue"]), venue_id=args["listings"][0]["venue_id"]
    )
    for t in ("dwd_listed_company", "dwd_trading_venue"):
        rows[t].update(
            effective_from=datetime(1990, 1, 1, tzinfo=timezone.utc),
            effective_to=None,
            knowledge_from=datetime(2026, 7, 1, tzinfo=timezone.utc),
            knowledge_to=None,
        )
    parents = {}
    history = {"layout": {}}
    for t in PARENTS:
        rel = "parents/" + t + ".parquet"
        (root / rel).parent.mkdir(exist_ok=True)
        pq.write_table(pa.Table.from_pylist([rows[t]], schema=ss[t]), root / rel)
        parents[t] = dict(path=rel, archive_path="native/" + t + ".parquet")
        history["layout"][t] = dict(
            files=[dict(path=parents[t]["archive_path"], sha256=file_sha(root / rel))]
        )
    (root / "history.json").write_bytes(canonical(history))
    pq.write_table(pa.Table.from_pylist([raw]), root / "raw.parquet")
    sha = file_sha(root / "raw.parquet")
    manifest = dict(
        release_id="fixture-v1",
        market_date="2026-09-02",
        artifacts=[dict(name="announcements_delta", sha256=sha)],
    )
    (root / "release.json").write_bytes(canonical(manifest))
    (root / "READY").write_text(file_sha(root / "release.json") + "\n")
    sources = [
        dict(
            manifest_sha256=file_sha(root / "release.json"),
            revision="fixture-v1",
            market_date="2026-09-02",
            sha256=sha,
        )
    ]
    selected, held, union = m.select_documents([(sha, "fixture-v1", [raw])], observed_at=CLOCK)
    output, projection = m.project_documents(selected, **args)
    (root / "proof.json").write_bytes(
        canonical(dict(union=union, raw_holds=held, projection=projection))
    )
    outs = {}
    for t, rs in output.items():
        part = partition(rs[0], c["tables"][t])
        rel = f"payload/{t}/{part}/part.parquet"
        (root / rel).parent.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(rs, schema=ss[t]), root / rel)
        outs[t] = [dict(path=rel, partition=part, semantic_scan=semantic_scan([root / rel], ss[t]))]
    body = dict(
        contract_id=m.CONTRACT_ID,
        input_closure=dict(
            purpose=m.CONTRACT_ID,
            qualification=m.QUALIFICATION,
            sources=sources,
            source_ids_by_table={t: [sha] for t in m.TABLES},
            history_materialization_sha256=file_sha(root / "history.json"),
        ),
        projection_module_sha256=file_sha(Path(m.__file__)),
        business_through="2026-09-30",
        observed_at=CLOCK.isoformat(),
        history_materialization="history.json",
        parents=parents,
        sources=[dict(manifest="release.json", ready="READY", raw="raw.parquet")],
        outputs=outs,
        proof_path="proof.json",
        READY=False,
        complete_Base23=False,
        consumer_admission="NOT_GRANTED",
    )

    def reseal():
        body["physical_members"] = {
            p: dict(sha256=file_sha(root / p), size_bytes=(root / p).stat().st_size)
            for p in _tree(root)
            if p != "SCOPE.json"
        }
        body["input_digest"] = digest(body["input_closure"])
        body.pop("identity", None)
        body["identity"] = digest(body)
        (root / "SCOPE.json").write_bytes(canonical(body))

    reseal()
    return root, body, ss, reseal


def audit_fixture(root, tmp_path):
    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits
    from quanthus.pipelines.securities_warehouse.base23_disclosure_audit import (
        audit_disclosure_scope,
    )

    with Budget(
        tmp_path / "audit",
        [root],
        Limits(
            memory_bytes=128 * 1024**2,
            allocated_bytes=128 * 1024**2,
            temp_bytes=64 * 1024**2,
            free_floor_bytes=1,
        ),
    ) as budget:
        return audit_disclosure_scope(root, budget)


def test_complete_image_local_disclosure_replay(tmp_path, monkeypatch):
    root, _, _, _ = scope_fixture(tmp_path, monkeypatch)
    result = audit_fixture(root, tmp_path)
    assert result["rows"] == {m.TABLES[0]: 1, m.TABLES[1]: 1}


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("source", "SOURCE_PLAN"),
        ("quality", "CONTRACT_UNQUALIFIED"),
        ("extra-empty", "PHYSICAL_PARTITIONS"),
        ("value", "TYPED_REPLAY"),
    ],
)
def test_fully_repacked_disclosure_faults_cannot_pass(tmp_path, monkeypatch, fault, reason):
    from quanthus.pipelines.securities_warehouse.base22_bounded import semantic_scan

    root, body, ss, reseal = scope_fixture(tmp_path, monkeypatch)
    if fault == "source":
        body["input_closure"]["source_ids_by_table"][m.TABLES[1]] = []
    elif fault == "quality":
        body["input_closure"]["qualification"] = "unqualified"
    elif fault == "extra-empty":
        p = root / f"payload/{m.TABLES[0]}/effective_at_year=2027/empty.parquet"
        p.parent.mkdir()
        pq.write_table(pa.Table.from_pylist([], schema=ss[m.TABLES[0]]), p)
    elif fault == "value":
        item = body["outputs"][m.TABLES[0]][0]
        p = root / item["path"]
        rs = pq.ParquetFile(p).read().to_pylist()
        rs[0]["title"] = "incorrect synthetic title"
        pq.write_table(pa.Table.from_pylist(rs, schema=ss[m.TABLES[0]]), p)
        item["semantic_scan"] = semantic_scan([p], ss[m.TABLES[0]])
    reseal()
    with pytest.raises(BoundedError, match=reason):
        audit_fixture(root, tmp_path)
