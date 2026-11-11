"""Complete repacking cannot hide changed typed atoms or unqualified repairs."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_holder_column_replay as audit
from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha, load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits
from quanthus.pipelines.securities_warehouse.base22_holder_column_continuation import (
    holder_observation,
)
from quanthus.pipelines.securities_warehouse.base22_holder_column_revision import CONTRACT_ID, TABLE
from tests.unit.test_base22_f10_ownership import raw
from tests.unit.test_base22_holder_column_revision import BODY, case
from tests.unit.test_base22_independent_image import _row
from tests.unit.test_base22_ownership_audit import seal


def image(root, budget):
    root.mkdir()
    shutil.copytree("config/pipelines", root / "contract")
    args = case()
    _, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    body = {
        "contract_id": CONTRACT_ID,
        "purpose": audit.PURPOSE,
        "observed_at": args["observed_at"],
        "consumer_admission": "NOT_GRANTED",
        "READY": False,
        "complete_Base22": False,
        "code_closure": {
            n: file_sha(Path(audit.__file__).with_name(n)) for n in audit.REPLAY_MODULES
        },
        "selection": "selection.json",
        "inputs": {},
        "raw_inputs": [],
        "parents": {},
        "stock_outputs": {},
    }
    for table in ("dwd_security", "dwd_security_identifier"):
        row = _row(ss[table])
        row.update(
            security_id="security",
            effective_from=datetime(2010, 1, 1, tzinfo=timezone.utc),
            knowledge_from=datetime(2026, 8, 3, 23, 59, tzinfo=timezone.utc),
        )
        if table == "dwd_security":
            row["company_id"] = "company"
        else:
            row.update(identifier_namespace="CN_STOCK_CODE:SZSE", identifier_value="002001")
        rel = table + ".parquet"
        pq.write_table(pa.Table.from_pylist([row], schema=ss[table]), root / rel)
        body["parents"][table] = [{"path": rel, "rows": 1}]
    weekly = []
    for day in ("2026-07-31", "2026-08-01", "2026-08-08"):
        rr = raw(BODY, "股东研究")
        rr["acquired_at"] = day + "T08:02:00+08:00"
        if day == "2026-07-31":
            rr = args["original_raw"]
        rel = day + ".parquet"
        pq.write_table(pa.Table.from_pylist([rr]), root / rel)
        sha = file_sha(root / rel)
        body["raw_inputs"].append(
            {
                "path": rel,
                "source_sha256": sha,
                "rows": 1,
                "day": day,
                "original": day == "2026-07-31",
            }
        )
        if day == "2026-07-31":
            args["native"][0]["source_artifact_sha256"] = sha
        else:
            weekly.extend(
                holder_observation(rr, source_sha256=sha, resolver=args["resolver"])[0][TABLE]
            )
    for origin, rows in (("native", args["native"]), ("weekly", weekly)):
        rel = origin + ".parquet"
        pq.write_table(pa.Table.from_pylist(rows, schema=ss[TABLE]), root / rel)
        body["inputs"][origin] = {"path": rel, "rows": len(rows), "sha256": file_sha(root / rel)}
    rule = args["rule"]
    from quanthus.pipelines.securities_warehouse.base22 import canonical

    selection = [
        {
            "business_key_sha256": digest([args["native"][0][k] for k in rule["business_key"]]),
            "stock_codes_from_original_provenance": ["002001"],
        }
    ]
    (root / body["selection"]).write_bytes(canonical(selection))

    def write(code, proof, nn, ww):
        rows = [r for item in proof["revisions"].values() for r in item["output"]]
        assert len(rows) == 2
        rel = code + ".parquet"
        pq.write_table(pa.Table.from_pylist(rows, schema=ss[TABLE]), root / rel)
        body["stock_outputs"][code] = {"path": rel, "qualification_identity": proof["identity"]}

    body["replay_summary"] = audit.replay_selected_inputs(root, body, budget, write)
    seal(root, body)
    return body


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "numeric",
        "old-clock",
        "empty-code",
        "duplicate-selection",
        "weekly-atom",
        "source-hash",
        "missing",
        "extra-empty",
    ],
)
def test_offline_replay_rejects_repacked_changes(tmp_path, defect):
    root = tmp_path / "image"
    with Budget(tmp_path / "TMP", [], Limits(free_floor_bytes=1)) as budget:
        body = image(root, budget)
        if defect in {"numeric", "old-clock"}:
            p = root / body["stock_outputs"]["002001"]["path"]
            table = pq.ParquetFile(p).read()
            rows = table.to_pylist()
            if defect == "numeric":
                rows[1]["holding_quantity"] += 1
            else:
                rows[0]["knowledge_from"] = rows[1]["knowledge_from"]
            pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), p)
        elif defect == "empty-code":
            body["code_closure"] = {}
        elif defect == "duplicate-selection":
            import json

            from quanthus.pipelines.securities_warehouse.base22 import canonical

            p = root / body["selection"]
            vv = json.loads(p.read_bytes())
            p.write_bytes(canonical(vv * 2))
        elif defect == "weekly-atom":
            item = body["inputs"]["weekly"]
            p = root / item["path"]
            table = pq.ParquetFile(p).read()
            rows = table.to_pylist()
            rows[0]["holding_percentage"] += 1
            pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), p)
            item["sha256"] = file_sha(p)
        elif defect == "source-hash":
            body["raw_inputs"][0]["source_sha256"] = "sha256:" + "0" * 64
        elif defect == "missing":
            (root / body["stock_outputs"]["002001"]["path"]).unlink()
        elif defect == "extra-empty":
            schema = pq.ParquetFile(root / body["stock_outputs"]["002001"]["path"]).schema_arrow
            pq.write_table(pa.Table.from_pylist([], schema=schema), root / "extra.parquet")
            body["stock_outputs"]["unregistered"] = {"path": "extra.parquet"}
        seal(root, body)
        if defect is None:
            result = audit.audit_column_restoration_scope(root, body, budget)
            assert result["qualified_keys"] == 1 and result["complete_Base22"] is False
        else:
            with pytest.raises(RuntimeError):
                audit.audit_column_restoration_scope(root, body, budget)
