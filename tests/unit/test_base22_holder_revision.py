from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    Limits,
    Member,
)
from quanthus.pipelines.securities_warehouse.base22_holder_exact import PARSER, load_holder_contract
from quanthus.pipelines.securities_warehouse.base22_holder_native_exact import (
    load_native_holder_contract,
)
from quanthus.pipelines.securities_warehouse.base22_holder_revision import (
    CONTRACT_ID,
    audit_holder_revision_scope,
    materialize_holder_revision_partition,
    validate_holder_revision_partition,
)
from tests.unit.test_base22_independent_image import _row


def _inputs(tmp_path, defect=None):
    _, _, exact = load_native_holder_contract(Path("config/pipelines"))
    _, capture_schema = load_holder_contract(Path("config/pipelines"))
    old = _row(exact)
    old.update(
        record_source="same-vendor",
        published_at=old["knowledge_from"],
        raw_average_holding="1.1234567890123",
        average_holding=Decimal("1.1234567890"),
        average_holding_exact=Decimal("1.1234567890123"),
    )
    new = dict(
        old,
        raw_holder_count="42",
        holder_count=42,
        raw_average_holding="2.12345678901",
        average_holding=None,
        average_holding_exact=Decimal("2.12345678901"),
        parser_version=PARSER,
        published_at=None,
        knowledge_from=old["knowledge_from"] + timedelta(days=4),
        source_revision="observed-new",
        source_artifact_sha256="a" * 64,
    )
    if defect == "equal-clock":
        new["knowledge_from"] = old["knowledge_from"]
    if defect == "earlier-clock":
        new["knowledge_from"] = old["knowledge_from"] - timedelta(days=1)
    if defect == "other-provider":
        new["record_source"] = "other-provider"
    if defect == "other-company":
        new["company_id"] = "other-company"
    if defect == "other-security":
        new["security_id"] = "other-security"
    if defect == "other-period":
        new["period_end"] -= timedelta(days=1)
    if defect == "other-scope":
        new["observation_scope"] = "other-scope"
    if defect == "unknown-entity":
        new["shareholder_count_observation_id"] = "unknown"
    if defect == "closed-old":
        old["knowledge_to"] = new["knowledge_from"]
    if defect == "closed-new":
        new["knowledge_to"] = new["knowledge_from"] + timedelta(days=1)
    if defect == "invented-publication":
        new["published_at"] = new["knowledge_from"]
    if defect == "new-rounding":
        new["average_holding_exact"] = Decimal("2.12345678900")
    if defect == "wrong-parser":
        new["parser_version"] = "legacy"
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    native = inputs / "native.parquet"
    capture = inputs / "capture.parquet"
    pq.write_table(
        pa.Table.from_pylist([old] * (2 if defect == "duplicate-native" else 1), schema=exact),
        native,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [new] * (2 if defect == "duplicate-capture" else 1), schema=capture_schema
        ),
        capture,
    )

    def member(path):
        return Member(
            path, file_sha(path), path.stat().st_size, pq.ParquetFile(path).metadata.num_rows
        )

    return exact, capture_schema, [member(native)], member(capture), old, new


def _limits():
    return Limits(
        allocated_bytes=32 * 1024**2,
        temp_bytes=16 * 1024**2,
        free_floor_bytes=1,
        rss_bytes=4 * 1024**3,
        wall_seconds=60,
    )


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "equal-clock",
        "earlier-clock",
        "other-provider",
        "other-company",
        "other-security",
        "other-period",
        "other-scope",
        "unknown-entity",
        "closed-old",
        "closed-new",
        "invented-publication",
        "new-rounding",
        "wrong-parser",
        "duplicate-native",
        "duplicate-capture",
    ],
)
def test_only_real_later_same_provider_same_entity_closes_old_interval(tmp_path, defect):
    exact, cap, native, capture, old, new = _inputs(tmp_path, defect)
    before = {m.path: m.path.read_bytes() for m in [*native, capture]}
    with Budget(tmp_path / "job", [tmp_path / "inputs"], _limits()) as budget:
        output = budget.root / "part.parquet"
        if defect:
            with pytest.raises(BoundedError):
                materialize_holder_revision_partition(
                    native, capture, output, capture_schema=cap, target_schema=exact, budget=budget
                )
            assert not output.exists()
        else:
            result = materialize_holder_revision_partition(
                native, capture, output, capture_schema=cap, target_schema=exact, budget=budget
            )
            rows = pq.ParquetFile(output).read().to_pylist()
            expected = dict(old, knowledge_to=new["knowledge_from"])
            assert rows[0] == expected and rows[1] == new
            assert rows[1]["published_at"] is None
            assert result["old_knowledge_ends_closed"] == 1 and result["rows"] == 2
            assert output.stat().st_nlink == 1
            with pytest.raises(BoundedError, match="OUTPUT_EXISTS"):
                materialize_holder_revision_partition(
                    native, capture, output, capture_schema=cap, target_schema=exact, budget=budget
                )
    assert before == {m.path: m.path.read_bytes() for m in [*native, capture]}


@pytest.mark.parametrize(
    "change",
    [
        "old-value",
        "old-raw",
        "old-time",
        "old-end",
        "new-value",
        "new-time",
        "lost-old",
        "lost-new",
        "extra-row",
    ],
)
def test_offline_full_typed_replay_rejects_allowed_file_repack_tampering(tmp_path, change):
    exact, cap, native, capture, _, _ = _inputs(tmp_path)
    with Budget(tmp_path / "job", [tmp_path / "inputs"], _limits()) as budget:
        output = budget.root / "part.parquet"
        materialize_holder_revision_partition(
            native, capture, output, capture_schema=cap, target_schema=exact, budget=budget
        )
        rows = pq.ParquetFile(output).read().to_pylist()
        if change == "old-value":
            rows[0]["average_holding"] += Decimal("1")
        if change == "old-raw":
            rows[0]["raw_average_holding"] = "0"
        if change == "old-time":
            rows[0]["published_at"] += timedelta(seconds=1)
        if change == "old-end":
            rows[0]["knowledge_to"] += timedelta(seconds=1)
        if change == "new-value":
            rows[1]["average_holding_exact"] += Decimal("0.0000000000001")
        if change == "new-time":
            rows[1]["knowledge_from"] += timedelta(seconds=1)
        if change == "lost-old":
            rows = rows[1:]
        if change == "lost-new":
            rows = rows[:1]
        if change == "extra-row":
            rows.append(dict(rows[1]))
        pq.write_table(pa.Table.from_pylist(rows, schema=exact), output)
        # This passes no stale output hash to the validator. Exact source-derived
        # typed semantics, including the one permitted knowledge_to edit, reject it.
        with pytest.raises(BoundedError):
            validate_holder_revision_partition(
                native, capture, output, capture_schema=cap, target_schema=exact, budget=budget
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "candidate"),
        ("projection_contract_id", "legacy"),
        ("consumer_admission", "ADMITTED"),
        ("whole_image_schema_admission", "APPROVED"),
        ("whole33_acceptance", True),
        ("READY", True),
        ("original_4173_visibility", "ZEROED"),
    ],
)
def test_cold_revision_scope_rejects_admission_or_availability_laundering(tmp_path, field, value):
    from quanthus.pipelines.securities_warehouse.base22 import digest

    d = {
        "kind": "isolated_later_holder_native_union_not_complete_base22",
        "projection_contract_id": CONTRACT_ID,
        "consumer_admission": "NOT_GRANTED",
        "whole_image_schema_admission": "pending_final_candidate_review",
        "whole33_acceptance": False,
        "READY": False,
        "original_4173_visibility": "UNCHANGED_UNAVAILABLE",
    }
    d[field] = value
    d["identity"] = digest(d)
    with (
        Budget(tmp_path / "job", [], _limits()) as budget,
        pytest.raises(BoundedError, match="SCOPE_CONTRACT_INVALID"),
    ):
        audit_holder_revision_scope(budget.root, d, budget)


def _cold_image(tmp_path, budget):
    import json
    import shutil
    from datetime import datetime, timezone

    from quanthus.pipelines.securities_warehouse.base22 import (
        digest,
        load_contract,
        partition,
        schemas,
    )
    from quanthus.pipelines.securities_warehouse.base22_bounded import semantic_scan
    from quanthus.pipelines.securities_warehouse.base22_holder_exact import (
        exact_holder_observation,
        holder_evidence,
    )
    from quanthus.pipelines.securities_warehouse.base22_holder_native_exact import (
        CONTRACT_ID as NATIVE_ID,
    )
    from quanthus.pipelines.securities_warehouse.base22_holder_native_exact import (
        carry_native_holder_batch,
    )
    from quanthus.pipelines.securities_warehouse.base22_outputs import write_partition

    root = budget.root / "image"
    stage = root / "observations/union"
    cfg = stage / "contract"
    cfg.mkdir(parents=True)
    names = [
        "base22-holder-exact.v2.json",
        "base22-holder-native-exact.v3.json",
        "base22-independent-image.v1.json",
        "dwd-domain-model.v1.json",
        "dwd-domain-model.v1.1-extension.json",
    ]
    for name in names:
        shutil.copyfile(Path("config/pipelines") / name, cfg / name)
    contract, model, extension = load_contract(cfg)
    ss = schemas(model, extension)
    _, legacy, exact = load_native_holder_contract(cfg)
    _, cap = load_holder_contract(cfg)
    nr = stage / "archives/native"
    ns = nr / "observations/native"
    nc = ns / "contract"
    nc.mkdir(parents=True)
    for name in names[1:]:
        shutil.copyfile(cfg / name, nc / name)
    cr = stage / "archives/capture"
    cs = cr / "observations/capture"
    cc = cs / "contract"
    cc.mkdir(parents=True)
    for name in [names[0], *names[2:]]:
        shutil.copyfile(cfg / name, cc / name)
    raw = {
        "source": "eastmoney_rpt_holdernum_det",
        "code": "000001",
        "end_date": "2026-06-30",
        "shareholder_count": "10",
        "avg_hold_num": "2.12345678901",
        "acquired_at": "2026-08-12T04:22:18+08:00",
        "hold_notice_date": "2026-07-25",
    }
    rp = cs / "raw.parquet"
    pq.write_table(pa.Table.from_pylist([raw]), rp)
    parent_clock = datetime(2026, 8, 4, tzinfo=timezone.utc)
    capture = exact_holder_observation(
        raw,
        company_id="c",
        security_id="s",
        parent_knowledge=parent_clock,
        source_sha256=file_sha(rp),
        revision="captured-new",
        contract_id="p10-base22-holder-exact-v2",
    )["dwd_shareholder_count_observation"][0]
    old = dict(capture)
    old.pop("average_holding_exact")
    old.update(
        knowledge_from=parent_clock,
        parser_version="legacy",
        published_at=parent_clock,
        raw_average_holding="1.1234567890123",
        average_holding=Decimal("1.1234567890"),
        source_revision="native-old",
    )
    op = ns / "archives/native/period_end_year=2026/old.parquet"
    op.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([old], schema=legacy), op)
    native_row = carry_native_holder_batch(
        pa.RecordBatch.from_pylist([old], schema=legacy),
        legacy_schema=legacy,
        contract_id=NATIVE_ID,
    ).to_pylist()[0]
    outputs = []
    registry = {}
    groups = {"dwd_shareholder_count_observation": [native_row]}
    for t, k, value in [
        ("dwd_listed_company", "company_id", "c"),
        ("dwd_security", "security_id", "s"),
    ]:
        row = _row(ss[t])
        row.update(
            {
                k: value,
                "effective_from": datetime(2011, 1, 1, tzinfo=timezone.utc),
                "knowledge_from": datetime(2011, 1, 1, tzinfo=timezone.utc),
            }
        )
        groups[t] = [row]
    for t, rows in groups.items():
        p = ns / "payload" / t / "period_end_year=2026/part.parquet"
        p.parent.mkdir(parents=True)
        schema = exact if t == "dwd_shareholder_count_observation" else ss[t]
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), p)
        outputs.append(
            {
                "table": t,
                "partition": "period_end_year=2026",
                "path": str(p.relative_to(nr)),
                "rows": 1,
                "sha256": file_sha(p),
                "size_bytes": p.stat().st_size,
                "semantic_scan": semantic_scan([p], schema),
            }
        )
        registry[t] = [
            (r["record_source"], r["source_revision"], r["source_artifact_sha256"]) for r in rows
        ]
    native = {
        "kind": "isolated_native_holder_exact_preparation_not_complete_base22",
        "label": "native",
        "schema_contract_id": NATIVE_ID,
        "schema_contract_sha256": file_sha(nc / names[1]),
        "contract_members": {n: file_sha(nc / n) for n in names[1:]},
        "consumer_admission": "NOT_GRANTED",
        "whole_image_schema_admission": "pending_final_candidate_review",
        "whole33_acceptance": False,
        "READY": False,
        "outputs": outputs,
        "output_rows": {t: 1 for t in groups},
        "source_registries": registry,
        "original_native_archives": [
            {
                "path": str(op.relative_to(nr)),
                "sha256": file_sha(op),
                "size_bytes": op.stat().st_size,
                "rows": 1,
                "role": "original_native_holder",
                "partition": "period_end_year=2026",
            }
        ],
        "exact_native_precision_proofs": [
            {
                "partition": "period_end_year=2026",
                "output_local_member": outputs[0]["path"],
                "native_local_members": [str(op.relative_to(nr))],
                "rows": 1,
                "new_exact_rule": (
                    "exact_retained_native_decimal_text_else_null_only_for_source_null"
                ),
                "old_columns_unchanged": True,
            }
        ],
        "precision_counts": {"rows": 1, "exact_finite": 1, "raw_source_null": 0},
    }
    native["identity"] = digest(native)
    ndp = ns / "OBSERVATIONS.json"
    ndp.write_text(json.dumps(native))
    cp = cs / "payload/dwd_shareholder_count_observation/period_end_year=2026/part.parquet"
    ci = write_partition(
        [capture],
        table="dwd_shareholder_count_observation",
        expected_rows=1,
        expected_partition=partition(
            capture, contract["tables"]["dwd_shareholder_count_observation"]
        ),
        schema=cap,
        rule=contract["tables"]["dwd_shareholder_count_observation"],
        admitted={
            (
                capture["record_source"],
                capture["source_revision"],
                capture["source_artifact_sha256"],
            )
        },
        output=cp,
        budget=budget,
    )
    ci["path"] = str(cp.relative_to(cr))
    mf = cs / "release.json"
    mf.write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "name": "shareholder_count",
                        "sha256": file_sha(rp),
                        "size_bytes": rp.stat().st_size,
                    }
                ]
            }
        )
    )
    captured = {
        "kind": "sealed_actual_domain_observations_not_complete_base22",
        "input_kind": "retained_raw_governance",
        "label": "capture",
        "schema_contract_id": "p10-base22-holder-exact-v2",
        "schema_contract_sha256": file_sha(cc / names[0]),
        "contract_members": {n: file_sha(cc / n) for n in [names[0], *names[2:]]},
        "consumer_admission": "NOT_GRANTED",
        "outputs": [ci],
        "output_rows": {"dwd_shareholder_count_observation": 1},
        "archived_true_source_observations": [
            {"path": str(rp.relative_to(cr)), "sha256": file_sha(rp), "rows": 1}
        ],
        "inputs": [
            {
                "record_source": capture["record_source"],
                "revision": capture["source_revision"],
                "sha256": file_sha(rp),
                "original_manifest_sha256": file_sha(mf),
            }
        ],
        "original_manifests": [
            {
                "path": str(mf.relative_to(cr)),
                "sha256": file_sha(mf),
                "original_manifest_sha256": file_sha(mf),
            }
        ],
        "precision_evidence": [holder_evidence(capture)],
        "raw_origin_bindings": [
            {
                "row_sha256": digest(capture),
                "archive_path": str(rp.relative_to(cr)),
                "raw_row_index": 0,
                "raw_row_sha256": digest(raw),
                "parent_knowledge_from": parent_clock.isoformat(),
            }
        ],
    }
    captured["identity"] = digest(captured)
    cdp = cs / "OBSERVATIONS.json"
    cdp.write_text(json.dumps(captured))
    union_outputs = []
    for item in outputs:
        t = item["table"]
        source = nr / item["path"]
        p = stage / "payload" / t / "period_end_year=2026/part.parquet"
        p.parent.mkdir(parents=True)
        if t == "dwd_shareholder_count_observation":
            rec = materialize_holder_revision_partition(
                [Member(source, file_sha(source), source.stat().st_size, 1)],
                Member(cp, file_sha(cp), cp.stat().st_size, 1),
                p,
                capture_schema=cap,
                target_schema=exact,
                budget=budget,
            )
        else:
            shutil.copyfile(source, p)
            rec = {"rows": 1, "semantic_scan": semantic_scan([p], ss[t])}
        rec.update(
            {
                "table": t,
                "partition": "period_end_year=2026",
                "path": str(p.relative_to(root)),
                "native_prior_member": item["path"],
                "capture_member": ci["path"] if t == "dwd_shareholder_count_observation" else None,
            }
        )
        union_outputs.append(rec)
    body = {
        "kind": "isolated_later_holder_native_union_not_complete_base22",
        "label": "union",
        "projection_contract_id": CONTRACT_ID,
        "consumer_admission": "NOT_GRANTED",
        "whole_image_schema_admission": "pending_final_candidate_review",
        "whole33_acceptance": False,
        "READY": False,
        "original_4173_visibility": "UNCHANGED_UNAVAILABLE",
        "native_preparation_root": str(nr.relative_to(root)),
        "qualified_capture_root": str(cr.relative_to(root)),
        "native_descriptor_path": str(ndp.relative_to(nr)),
        "capture_descriptor_path": str(cdp.relative_to(cr)),
        "native_descriptor_sha256": file_sha(ndp),
        "capture_descriptor_sha256": file_sha(cdp),
        "outputs": union_outputs,
        "output_rows": {
            "dwd_shareholder_count_observation": 2,
            "dwd_listed_company": 1,
            "dwd_security": 1,
        },
    }
    return root, stage, body, exact


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "old-body",
        "new-body",
        "old-end",
        "empty-extra",
        "other-provider",
        "missing-local-source",
    ],
)
def test_full_cold_holder_domain_replays_local_native_and_raw_sources(tmp_path, fault):
    import json

    from quanthus.pipelines.securities_warehouse.base22 import Base22Error, digest
    from quanthus.pipelines.securities_warehouse.base22_bounded import semantic_scan
    from quanthus.pipelines.securities_warehouse.base22_sources import _tree

    with Budget(tmp_path / "cold", [], _limits()) as budget:
        root, stage, d, exact = _cold_image(tmp_path, budget)
        item = d["outputs"][0]
        p = root / item["path"]
        if fault in ["old-body", "new-body", "old-end"]:
            rows = pq.ParquetFile(p).read().to_pylist()
            if fault == "old-body":
                rows[0]["average_holding"] += Decimal("1")
            if fault == "new-body":
                rows[1]["average_holding_exact"] += Decimal("0.0000000000001")
            if fault == "old-end":
                rows[0]["knowledge_to"] += timedelta(seconds=1)
            pq.write_table(pa.Table.from_pylist(rows, schema=exact), p)
            item["semantic_scan"] = semantic_scan([p], exact)
        if fault == "empty-extra":
            pq.write_table(pa.Table.from_pylist([], schema=exact), p.with_name("extra.parquet"))
        if fault == "other-provider":
            cp = root / d["qualified_capture_root"] / d["capture_descriptor_path"]
            c = json.loads(cp.read_bytes())
            c["inputs"][0]["record_source"] = "other-provider"
            c.pop("identity")
            c["identity"] = digest(c)
            cp.write_text(json.dumps(c))
            d["capture_descriptor_sha256"] = file_sha(cp)
        if fault == "missing-local-source":
            source = root / d["native_preparation_root"] / d["native_descriptor_path"]
            source.unlink()
        d["physical_members"] = {
            rel: {"sha256": file_sha(stage / rel), "size_bytes": (stage / rel).stat().st_size}
            for rel in _tree(stage)
        }
        d["identity"] = digest(d)
        if fault:
            with pytest.raises((BoundedError, Base22Error)):
                audit_holder_revision_scope(root, d, budget)
        else:
            result = audit_holder_revision_scope(root, d, budget)
            assert result["rows"]["dwd_shareholder_count_observation"] == 2
            assert result["complete_Base22"] is False
