"""Retain richer native metadata when an exact same-clock capture is partial."""

from __future__ import annotations

from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .disclosure_event import _provider_timestamp

CONTRACT_ID = "p10-base22-same-document-partial-capture-retain-v1"
PROVENANCE = {"source_revision", "source_artifact_sha256"}


def qualify_partial_disclosure(
    raw: dict[str, Any],
    before: dict[str, Any],
    incoming: dict[str, Any],
    before_subject: dict[str, Any],
    incoming_subject: dict[str, Any],
) -> dict[str, Any]:
    """No body overwrite, inferred publication/acquisition or URI equivalence.

    A raw archive keyed by ann_id independently supports the exact native title,
    classification and URI. The partial capture cannot erase them. Changing a
    title, date, identity or typed subject requires a separate revision contract.
    The caller binds original whole-file SHA and dated identities separately.
    """
    try:
        if (
            raw["ann_id"] != before["source_document_id"]
            or raw["ann_id"] != before["source_record_id"]
            or raw["title"] != before["title"]
            or raw["url"] != before["document_uri"]
            or raw["category"] != before["disclosure_type"]
            or _provider_timestamp(raw["publish_time"], "retained disclosure publication")
            != before["effective_at"]
            or before["record_source"] != "announcement_archive"
            or incoming["disclosure_type"] != "UNCLASSIFIED"
        ):
            raise BoundedError("DISCLOSURE_COMPATIBILITY_EXACT_RAW_OR_PARTIAL_SCOPE_INVALID")
        if (
            set(before) != set(incoming)
            or set(before_subject) != set(incoming_subject)
            or any(
                before[k] != incoming[k]
                for k in before
                if k not in PROVENANCE | {"disclosure_type", "document_uri"}
            )
            or any(
                before_subject[k] != incoming_subject[k]
                for k in before_subject
                if k not in PROVENANCE
            )
            or before_subject["disclosure_id"] != before["disclosure_id"]
            or before_subject["source_record_id"] != before["source_record_id"]
        ):
            raise BoundedError("DISCLOSURE_COMPATIBILITY_CHANGED_DOCUMENT_OR_SUBJECT")
    except (KeyError, ValueError, TypeError, RuntimeError) as exc:
        if isinstance(exc, BoundedError):
            raise
        raise BoundedError("DISCLOSURE_COMPATIBILITY_INPUT_INVALID") from None
    result = {
        "contract_id": CONTRACT_ID,
        "source_document_key_sha256": digest(raw["ann_id"]),
        "before_raw_sha256": digest(raw),
        "before_row_sha256": digest(before),
        "incoming_row_sha256": digest(incoming),
        "before_subject_sha256": digest(before_subject),
        "incoming_subject_sha256": digest(incoming_subject),
        "disposition": "RETAIN_EXACT_NATIVE_BODY_AND_INTERVAL_ARCHIVE_PARTIAL_CAPTURE",
        "changed_native_atoms": 0,
        "new_knowledge_versions": 0,
        "old_provider_acquisition": "UNKNOWN",
        "URI_byte_equivalence": "NOT_ASSERTED",
        "consumer_admission": "NOT_GRANTED",
    }
    result["identity"] = digest(result)
    return result
