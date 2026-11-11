from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_holder_compatibility_audit import (
    audit_compatible_holder_scope,
)


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "legacy_current"),
        ("label", "../source"),
        ("old_body_changes", 1),
        ("old_exact13_changes", 1),
        ("old_publication_atoms_preserved", False),
        ("new_publication_unknown", False),
        ("original_4173_visibility", "ZEROED"),
        ("full_image_schema_admission", True),
        ("consumer_admission", "ADMITTED"),
        ("whole33_or_Base22_accepted", True),
        ("READY", True),
        ("old_body_changes", False),
    ],
)
def test_self_consistent_repacked_scope_cannot_claim_consumer_or_legacy_admission(field, value):
    body = {
        "kind": "sealed_scoped_compatible_holder_governance_revision_NOT_COMPLETE_BASE22",
        "label": "holder-binary-compatible-new-governance-version",
        "old_body_changes": 0,
        "old_exact13_changes": 0,
        "old_publication_atoms_preserved": True,
        "new_publication_unknown": True,
        "original_4173_visibility": "UNCHANGED_UNAVAILABLE",
        "full_image_schema_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "whole33_or_Base22_accepted": False,
        "READY": False,
    }
    body[field] = value
    descriptor = {**body, "identity": digest(body)}
    # No readable root or Budget is supplied: disallowed claims must fail before I/O.
    with pytest.raises(BoundedError, match="SCOPE_OR_DISPOSITION_INVALID"):
        audit_compatible_holder_scope(Path("/synthetic-unreachable-source"), descriptor, None)
