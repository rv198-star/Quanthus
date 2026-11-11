import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base231_domain_pack as adapter
from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits
from tests.unit.test_base23_ownership import BODY as HOLDERS
from tests.unit.test_base23_ownership_reconfirmation import fixture
from tests.unit.test_base231_financial_summary import BODY as FINANCE

LIMITS = Limits(allocated_bytes=2**30, free_floor_bytes=1, wall_seconds=120)


def prepare(root):
    evidence = root / "evidence"
    evidence.mkdir()
    rows = []
    for category, body in [("财务分析", FINANCE), ("股东研究", HOLDERS)]:
        raw = fixture()
        raw.update(stock_code="002001", security_id="002001", category=category, content=body,
                   market_date="2026-09-05", acquired_at="2026-09-05T00:00:00+08:00",
                   content_sha256="sha256:" + hashlib.sha256(body.encode()).hexdigest())
        rows.append(raw)
    pq.write_table(pa.Table.from_pylist(rows), evidence / "raw.parquet")
    common = dict(effective_from=datetime(2010, 1, 1, tzinfo=timezone.utc), effective_to=None,
                  knowledge_from=datetime(2026, 7, 31, tzinfo=timezone.utc))
    parents = {
        "dwd_security": [dict(common, security_id="security", company_id="company")],
        "dwd_security_identifier": [dict(common, security_id="security",
                                         identifier_namespace="CN_STOCK_CODE:SZSE",
                                         identifier_value="002001")],
    }
    for name, values in parents.items():
        pq.write_table(pa.Table.from_pylist(values), evidence / (name + ".parquet"))
    config = root / "config"
    config.mkdir()
    for name in ("base231-domains.v1.json", "base22-holder-component-grain.v2.json",
                 "base22-independent-image.v1.json", "dwd-domain-model.v1.json",
                 "dwd-domain-model.v1.1-extension.json"):
        shutil.copyfile(Path("config/pipelines") / name, config / name)
    files = [{"path": str(p.relative_to(root)), "bytes": p.stat().st_size,
              "sha256": file_sha(p)} for p in sorted(root.rglob("*")) if p.is_file()]
    code = {p.name: file_sha(p) for p in Path(adapter.__file__).parent.glob("*.py")}
    return dict(kind=adapter.KIND, business_through="2026-09-30", consumer_admission="NOT_GRANTED",
                provider_PIT_qualified=False, code_inputs=code,
                domain_contract="config/base231-domains.v1.json", component_config_root="config",
                governance_observed_at="2026-10-12T10:00:00+00:00", files=files,
                parents={t: ["evidence/" + t + ".parquet"] for t in parents},
                raw_members=[dict(path="evidence/raw.parquet", source_revision="retained-synthetic",
                                  sha256=file_sha(evidence / "raw.parquet"))])


def build(tmp_path):
    root = tmp_path / "pack"
    with Budget(root, [], LIMITS) as budget:
        manifest = adapter.build_domain_pack(root, prepare(root), budget)
    return root, manifest


def test_complete_domain_projection_replays_from_independent_copy(tmp_path):
    root, manifest = build(tmp_path)
    assert manifest["metrics"]["rows"]["dwd_financial_fact"] == 6
    assert manifest["metrics"]["rows"]["dwd_shareholding_observation"] == 1
    relocated = tmp_path / "relocated"
    shutil.copytree(root, relocated)
    shutil.rmtree(root)  # Disposable synthetic original only.
    with Budget(tmp_path / "audit", [relocated], LIMITS) as budget:
        result = adapter.audit_domain_pack(relocated, budget)
    assert result["status"] == "PASS_SOURCE_CONTAINED_DOMAIN_REPLAY"
    assert not result["full_base_acceptance"]


@pytest.mark.parametrize("mode", ["missing", "corrupt", "extra"])
def test_disposable_damage_fails_closed_without_sources(tmp_path, mode):
    root, _ = build(tmp_path)
    path = root / "evidence/raw.parquet"
    if mode == "missing":
        path.unlink()
    elif mode == "corrupt":
        path.write_bytes(b"broken")
    else:
        (root / "undeclared").write_bytes(b"extra")
    with (Budget(tmp_path / "audit", [root], LIMITS) as budget,
          pytest.raises(RuntimeError, match="BASE231_DOMAIN")):
        adapter.audit_domain_pack(root, budget)


def test_fully_resealed_wrong_purpose_does_not_become_admitted(tmp_path):
    root, manifest = build(tmp_path)
    inputs = json.loads((root / "INPUTS.json").read_bytes())
    inputs["consumer_admission"] = "GRANTED"
    (root / "INPUTS.json").write_bytes(canonical(inputs))
    manifest["input_digest"] = digest(inputs)
    for item in manifest["members"]:
        path = root / item["path"]
        item.update(sha256=file_sha(path), bytes=path.stat().st_size)
    (root / "manifest.json").write_bytes(canonical(manifest))
    (root / "READY").write_bytes(canonical({"manifest_sha256": file_sha(root / "manifest.json")}))
    with (Budget(tmp_path / "audit", [root], LIMITS) as budget,
          pytest.raises(RuntimeError, match="INVENTORY_INVALID")):
        adapter.audit_domain_pack(root, budget)
