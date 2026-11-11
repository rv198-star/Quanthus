"""An internally repacked history dependency cannot grant its own scope."""

from copy import deepcopy

import pytest

from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_canonical_action_revision import CONTRACT_ID
from quanthus.pipelines.securities_warehouse.base22_history_action_audit import verify_local_closure


def descriptor():
    body = {
        "contract_id": CONTRACT_ID,
        "purpose": "QUALIFIED_NORMALIZATION_SUCCESSOR_HISTORY_TMP_WITH_EXPLICIT_UNRESOLVED_KEYS",
        "consumer_admission": "NOT_GRANTED",
        "READY": False,
        "historical_PIT_admission": False,
        "full_original_normalization_restored": False,
        "positive_source_priority_granted": False,
        "all_history_or_August33_or_Base22_qualified": False,
        "physical_members": {},
        "source_dependencies": {},
        "outputs": [],
    }
    body["identity"] = digest(body)
    return body


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "FULL_BASE22"),
        ("contract_id", "legacy-current"),
        ("consumer_admission", "ACCEPTED"),
        ("READY", True),
        ("historical_PIT_admission", True),
        ("full_original_normalization_restored", True),
        ("positive_source_priority_granted", True),
        ("all_history_or_August33_or_Base22_qualified", True),
    ],
)
def test_repacked_scope_does_not_grant_admission(tmp_path, field, value):
    body = deepcopy(descriptor())
    body[field] = value
    body["identity"] = digest({k: v for k, v in body.items() if k != "identity"})
    (tmp_path / "QUALIFICATION.json").write_bytes(canonical(body))
    with pytest.raises(BoundedError, match="SUCCESSOR_HISTORY_SCOPE_INVALID"):
        verify_local_closure(tmp_path, body, lambda: None)


def test_extra_physical_member_is_not_ignored(tmp_path):
    body = descriptor()
    (tmp_path / "QUALIFICATION.json").write_bytes(canonical(body))
    (tmp_path / "unregistered.parquet").write_bytes(b"synthetic")
    with pytest.raises(BoundedError, match="EXACT_PHYSICAL_SET"):
        verify_local_closure(tmp_path, body, lambda: None)


def test_changed_member_is_not_accepted_by_descriptor_count(tmp_path):
    body = descriptor()
    member = tmp_path / "raw.bin"
    member.write_bytes(b"before")
    body["physical_members"] = {
        "raw.bin": {"sha256": file_sha(member), "size_bytes": member.stat().st_size}
    }
    body["identity"] = digest({k: v for k, v in body.items() if k != "identity"})
    (tmp_path / "QUALIFICATION.json").write_bytes(canonical(body))
    member.write_bytes(b"after!")
    with pytest.raises(BoundedError, match="MEMBER_BYTES"):
        verify_local_closure(tmp_path, body, lambda: None)
