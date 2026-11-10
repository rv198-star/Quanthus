import shutil
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest, load_contract, partition, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.base22_output_audit import audit_observation_stage
from quanthus.pipelines.securities_warehouse.base22_outputs import write_partition
from tests.unit.test_base22_outputs import project, tail


@pytest.mark.parametrize("fault", [None, "corrupt", "delete", "extra"])
def test_cold_full_column_scope_and_physical_fail_closed(tmp_path, fault):
    c, m, e = load_contract(Path("config/pipelines"))
    ss = schemas(m, e)
    row, _ = project(*tail())
    rule = c["tables"]["dwd_security_bar"]
    root = tmp_path / "job"
    path = root / "observations" / "scope" / "payload" / "dwd_security_bar" / "one.parquet"
    with Budget(
        root, [], Limits(free_floor_bytes=1, allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2)
    ) as budget:
        item = write_partition(
            [row],
            table="dwd_security_bar",
            expected_rows=1,
            expected_partition=partition(row, rule),
            schema=ss["dwd_security_bar"],
            rule=rule,
            admitted={
                (row["record_source"], row["source_revision"], row["source_artifact_sha256"])
            },
            output=path,
            budget=budget,
        )
    item["path"] = str(path.relative_to(root))
    body = {
        "kind": "sealed_actual_domain_observations_not_complete_base22",
        "label": "scope",
        "outputs": [item],
        "output_rows": {"dwd_security_bar": 1},
        "consumer_admission": "NOT_GRANTED",
        "archived_true_source_observations": [],
    }
    desc = {**body, "identity": digest(body)}
    if fault == "corrupt":
        path.write_bytes(b"broken")
    if fault == "delete":
        path.unlink()
    if fault == "extra":
        shutil.copyfile(path, path.with_name("extra.parquet"))
    if fault:
        with pytest.raises((BoundedError, OSError)):
            audit_observation_stage(root, desc, Path("config/pipelines"))
    else:
        receipt = audit_observation_stage(root, desc, Path("config/pipelines"))
        assert (
            receipt["tables"] == {"dwd_security_bar": 1}
            and "NOT_IMAGE_ACCEPTANCE" in receipt["status"]
        )
