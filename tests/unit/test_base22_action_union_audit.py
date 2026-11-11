import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_action_union as u
from quanthus.pipelines.securities_warehouse.base22 import (
    canonical,
    digest,
    file_sha,
    load_contract,
    partition,
    schemas,
)
from quanthus.pipelines.securities_warehouse.base22_action_union_audit import (
    audit_selected_action_scope,
)
from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    Limits,
    Member,
    semantic_scan,
)
from quanthus.pipelines.securities_warehouse.base22_f10_dividend import CONTRACT_ID as F10_ID
from tests.unit.test_base22_action_union import action
from tests.unit.test_base22_independent_image import _row


def fixture(tmp_path):
    root = tmp_path / "scope"
    stage = root / "observations" / "selected"
    stage.mkdir(parents=True)
    cfg = stage / "contract"
    cfg.mkdir()
    for name in [
        "base22-independent-image.v1.json",
        "dwd-domain-model.v1.json",
        "dwd-domain-model.v1.1-extension.json",
    ]:
        shutil.copyfile(Path("config/pipelines") / name, cfg / name)
    c, m, e = load_contract(cfg)
    ss = schemas(m, e)
    donor = root / "archives" / "f10"
    ds = donor / "observations" / "f10-dividend"
    (ds / "contract").mkdir(parents=True)
    for p in cfg.iterdir():
        shutil.copyfile(p, ds / "contract" / p.name)

    def write(base, relative, t, rs):
        p = base / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rs, schema=ss[t]), p)
        return {
            "path": relative,
            "table": t,
            "partition": partition(rs[0], c["tables"][t]),
            "rows": len(rs),
            "sha256": file_sha(p),
            "size_bytes": p.stat().st_size,
            "semantic_scan": semantic_scan([p], ss[t]),
        }

    a = action("new")
    a["company_id"] = "company"
    a["security_id"] = "security"
    term = _row(ss["dwd_corporate_action_term"])
    term.update(
        {
            k: a[k]
            for k in [
                "record_source",
                "source_record_id",
                "source_revision",
                "source_artifact_sha256",
                "knowledge_from",
                "knowledge_to",
                "effective_at",
                "announced_at",
            ]
        }
    )
    term.update(
        corporate_action_id="new",
        corporate_action_term_id="term",
        term_type="CASH_AMOUNT_PER_SHARE",
        option_id=None,
        currency="CNY",
        amount_per_share=Decimal("1.0000000001"),
        ratio_numerator=None,
        ratio_denominator=None,
        subscription_price=None,
    )
    company = _row(ss["dwd_listed_company"])
    company["company_id"] = "company"
    security = _row(ss["dwd_security"])
    security.update(security_id="security", company_id="company")
    incoming = [
        write(donor, "observations/f10-dividend/payload/" + t + "/part.parquet", t, [rs])
        for t, rs in [("dwd_corporate_action", a), ("dwd_corporate_action_term", term)]
    ]
    parent_items = {
        t: [write(donor, "observations/f10-dividend/parents/" + t + "/part.parquet", t, [row])]
        for t, row in [("dwd_listed_company", company), ("dwd_security", security)]
    }
    d = {
        "kind": "sealed_actual_domain_observations_not_complete_base22",
        "label": "f10-dividend",
        "outputs": incoming,
        "output_rows": {o["table"]: o["rows"] for o in incoming},
        "archived_true_source_observations": [],
        "consumer_admission": "NOT_GRANTED",
        "full_471_acceptance": False,
        "full_33_table_image": False,
        "projection_contract_id": F10_ID,
        "parent_archive": parent_items,
        "inputs": [
            {
                "record_source": a["record_source"],
                "revision": a["source_revision"],
                "sha256": a["source_artifact_sha256"],
            }
        ],
    }
    d["identity"] = digest(d)
    dp = ds / "OBSERVATIONS.json"
    dp.write_bytes(canonical(d) + b"\n")
    old = action("old", date(2026, 7, 30))
    native = write(root, "archives/native/part.parquet", "dwd_corporate_action", [old])
    with Budget(tmp_path / "selection", [], Limits(free_floor_bytes=1)) as budget:
        selected = u.select_uncontested_actions(
            [Member(root / native["path"], native["sha256"], native["size_bytes"], 1)],
            [
                Member(
                    donor / incoming[0]["path"], incoming[0]["sha256"], incoming[0]["size_bytes"], 1
                )
            ],
            [],
            schema=ss["dwd_corporate_action"],
            budget=budget,
        )
    outputs = [
        write(root, "observations/selected/payload/" + t + "/part.parquet", t, [row])
        for t, row in [
            ("dwd_corporate_action", a),
            ("dwd_corporate_action_term", term),
            ("dwd_listed_company", company),
            ("dwd_security", security),
        ]
    ]
    descriptor = {
        "kind": "sealed_actual_domain_observations_not_complete_base22",
        "label": "selected",
        "consumer_admission": "NOT_GRANTED",
        "complete_Base22": False,
        "full471_acceptance": False,
        "native_rows_replaced": 0,
        "source_priority_granted": False,
        "projection_contract_id": u.CONTRACT_ID,
        "output_rows": {o["table"]: o["rows"] for o in outputs},
        "outputs": outputs,
        "archived_true_source_observations": [],
        "configuration_sha256": {p.name: file_sha(p) for p in cfg.iterdir()},
        "F10_local_root": "archives/f10",
        "F10_descriptor_sha256": file_sha(dp),
        "F10_scope_identity": d["identity"],
        "native_actions": [native],
        "competing_actions": [],
        "selection": selected,
    }
    return root, descriptor


def repack(root, descriptor):
    p = root / "observations/selected/OBSERVATIONS.json"
    descriptor["physical_files"] = [
        {
            "path": f.relative_to(root).as_posix(),
            "sha256": file_sha(f),
            "size_bytes": f.stat().st_size,
        }
        for f in sorted(root.rglob("*"))
        if f.is_file() and f != p
    ]
    descriptor.pop("identity", None)
    descriptor["identity"] = digest(descriptor)
    p.write_bytes(canonical(descriptor) + b"\n")


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "priority",
        "purpose",
        "missing",
        "corrupt",
        "clock",
        "value",
        "partition",
        "extra-empty",
        "selection",
        "unknown-source",
    ],
)
def test_complete_repack_still_checks_selected_semantics(tmp_path, fault):
    root, d = fixture(tmp_path)
    target = next(o for o in d["outputs"] if o["table"] == "dwd_corporate_action_term")
    if fault == "priority":
        d["source_priority_granted"] = True
    if fault == "purpose":
        d["kind"] = "qualified_current"
    if fault == "selection":
        d["selection"]["selected_entity_ids"] = []
    if fault == "partition":
        target["partition"] = "effective_at_year=2025"
    p = root / target["path"]
    if fault == "missing":
        p.unlink()
    elif fault == "corrupt":
        p.write_bytes(b"broken")
    elif fault in ["clock", "value", "unknown-source"]:
        t = pq.read_table(p)
        rs = t.to_pylist()
        field = {
            "clock": "announced_at",
            "value": "amount_per_share",
            "unknown-source": "record_source",
        }[fault]
        rs[0][field] = (
            rs[0]["knowledge_from"]
            if fault == "clock"
            else Decimal("2.0000000001")
            if fault == "value"
            else "unknown"
        )
        pq.write_table(pa.Table.from_pylist(rs, schema=t.schema), p)
        target.update(
            sha256=file_sha(p),
            size_bytes=p.stat().st_size,
            semantic_scan=semantic_scan([p], t.schema),
        )
    elif fault == "extra-empty":
        t = pq.read_table(p)
        pq.write_table(t.slice(0, 0), p.with_name("extra.parquet"))
    repack(root, d)
    with Budget(tmp_path / "cold", [], Limits(free_floor_bytes=1)) as budget:
        local = budget.root / "image"
        shutil.copytree(root, local)
        if fault:
            with pytest.raises((BoundedError, OSError, ValueError)):
                audit_selected_action_scope(local, d, budget)
        else:
            result = audit_selected_action_scope(local, d, budget)
            assert result["selected_actions"] == 1
            assert (
                result["native_rows_replaced"] == 0 and not result["full471_or_Base22_acceptance"]
            )
