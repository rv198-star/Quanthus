import json
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import base23_required_union as u
from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base23_required import CONTRACT_ID


@pytest.mark.parametrize(
    "damage, reason",
    [
        ("consumer", "REQUIRED_UNION_IDENTITY_OR_CODE_CHANGED"),
        ("code", "REQUIRED_UNION_IDENTITY_OR_CODE_CHANGED"),
        ("missing", "REQUIRED_UNION_PHYSICAL_SET_CHANGED"),
        ("corrupt", "REQUIRED_UNION_PHYSICAL_HASH_CHANGED"),
    ],
)
def test_union_repacked_metadata_and_files_fail_before_source_replay(tmp_path, damage, reason):
    p = tmp_path / "member"
    p.write_bytes(b"synthetic")
    body = {
        "contract_id": CONTRACT_ID,
        "consumer_admission": "NOT_GRANTED",
        "complete_Base23": False,
        "code_sha256": file_sha(Path(u.__file__)),
        "merge_code_sha256": file_sha(Path(u.__file__).with_name("base23_merge.py")),
        "physical_members": {"member": {"sha256": file_sha(p), "size_bytes": p.stat().st_size}},
    }
    if damage == "consumer":
        body["consumer_admission"] = "GRANTED"
    elif damage == "code":
        body["merge_code_sha256"] = "sha256:" + "0" * 64
    elif damage == "missing":
        p.unlink()
    else:
        p.write_bytes(b"changed!!")
    body["identity"] = digest(body)
    (tmp_path / "SCOPE.json").write_text(json.dumps(body))
    with pytest.raises(BoundedError, match=reason):
        u.audit_required_union(tmp_path, None, require_ready=False)
