import json
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base23_required import CONTRACT_ID
from quanthus.pipelines.securities_warehouse.base23_required_scope import (
    CONFIG_SHA,
    audit_required_scope,
    code_closure,
)


def test_versioned_contract_bytes_are_pinned_and_cannot_silently_change():
    p = Path(__file__).resolve().parents[2] / "config/pipelines/base23-required-packages.v1.json"
    assert file_sha(p) == CONFIG_SHA


@pytest.mark.parametrize(
    "damage, reason",
    [
        ("purpose", "REQUIRED_SCOPE_CONTRACT_OR_IDENTITY_CHANGED"),
        ("extra_table", "ACTION_SCOPE_TABLE_SET_CHANGED"),
        ("empty_directory", "NATIVE_IMAGE_UNDECLARED_PHYSICAL_DIRECTORY"),
    ],
)
def test_fully_rehashed_structural_forgery_fails_before_any_source_replay(tmp_path, damage, reason):
    body = {
        "contract_id": CONTRACT_ID,
        "code_closure": code_closure(),
        "consumer_admission": "NOT_GRANTED",
        "complete_Base23": False,
        "physical_members": {},
        "outputs": {"dwd_corporate_action": [], "dwd_corporate_action_term": []},
    }
    if damage == "purpose":
        body["contract_id"] = "research"
    elif damage == "extra_table":
        body["outputs"]["dwd_unqualified"] = []
    else:
        (tmp_path / "payload/unregistered-empty").mkdir(parents=True)
    body["identity"] = digest(body)
    (tmp_path / "SCOPE.json").write_text(json.dumps(body))
    # No budget or source fixture: rejection must precede either being accessed.
    with pytest.raises(BoundedError, match=reason):
        audit_required_scope(tmp_path, None, require_ready=False)
