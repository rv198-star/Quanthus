from datetime import datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_disclosure_compatibility import (
    qualify_partial_disclosure,
)


def fixture():
    raw = {
        "ann_id": "doc1",
        "title": "synthetic",
        "url": "retained-uri",
        "category": "classified",
        "publish_time": "2026-07-31 12:00:00",
    }
    before = {
        "source_document_id": "doc1",
        "source_record_id": "doc1",
        "title": "synthetic",
        "document_uri": "retained-uri",
        "disclosure_type": "classified",
        "record_source": "announcement_archive",
        "disclosure_id": "d1",
        "effective_at": datetime(2026, 7, 31, 4, tzinfo=timezone.utc),
        "knowledge_from": datetime(2026, 8, 4, tzinfo=timezone.utc),
        "source_revision": "native",
        "source_artifact_sha256": "a" * 64,
    }
    incoming = dict(
        before,
        disclosure_type="UNCLASSIFIED",
        document_uri="alternate-uri",
        source_revision="partial",
        source_artifact_sha256="b" * 64,
    )
    subject = {
        "disclosure_id": "d1",
        "source_record_id": "doc1",
        "security_id": "s1",
        "knowledge_from": before["knowledge_from"],
        "source_revision": "native",
    }
    new_subject = dict(subject, source_revision="partial")
    return raw, before, incoming, subject, new_subject


def test_same_document_partial_capture_preserves_original_metadata_without_URI_equivalence():
    inputs = fixture()
    frozen = tuple(x.copy() for x in inputs)
    result = qualify_partial_disclosure(*inputs)
    assert result["changed_native_atoms"] == result["new_knowledge_versions"] == 0
    assert result["URI_byte_equivalence"] == "NOT_ASSERTED"
    assert result["old_provider_acquisition"] == "UNKNOWN"
    assert inputs == frozen


@pytest.mark.parametrize(
    "index,field,value",
    [
        (0, "title", "other"),
        (0, "category", "other"),
        (0, "url", "other"),
        (0, "publish_time", "2026-08-03 12:00:00"),
        (0, "ann_id", "other"),
        (2, "title", "new body"),
        (2, "disclosure_type", "new classification"),
        (2, "source_document_id", "other"),
        (2, "disclosure_id", "other"),
        (2, "knowledge_from", datetime(2026, 8, 5, tzinfo=timezone.utc)),
        (4, "security_id", "other"),
        (4, "disclosure_id", "other"),
    ],
)
def test_raw_changes_and_real_document_revisions_require_separate_admission(index, field, value):
    inputs = fixture()
    inputs[index][field] = value
    with pytest.raises(BoundedError):
        qualify_partial_disclosure(*inputs)
