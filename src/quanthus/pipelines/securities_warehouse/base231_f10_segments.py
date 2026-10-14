"""Reconstruct an exact declared TDX F10 byte range before strict GBK decoding.

The SDK's per-response decode can silently drop a character split across byte
ranges. Segment collection preserves wire evidence and actual receipt clocks;
this helper proves completeness, never historical availability or source rank.
"""

from __future__ import annotations

import hashlib
import re
import struct
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError

CONTRACT_ID = "p10-base231-complete-f10-byte-segments-v1"


def content_response_bytes(body: bytes, *, code: str, requested_length: int) -> bytes:
    """Validate the echoed security, declared bytes and optional one NUL trailer.

    Retain the complete wire frame separately. Its one observed terminating NUL
    lies outside the declared content and must never consume a content byte.
    """
    if (not isinstance(body, bytes) or len(body) < 12
            or not re.fullmatch(r"[0-9]{6}", code)
            or body[2:8] != code.encode("ascii")
            or type(requested_length) is not int or not 0 < requested_length <= 8192):
        raise BoundedError("F10_CONTENT_RESPONSE_IDENTITY_OR_BOUND_INVALID")
    declared = struct.unpack("<H", body[10:12])[0]
    if (declared != requested_length or len(body) < 12 + declared
            or body[12 + declared:] not in {b"", b"\x00"}):
        raise BoundedError("F10_CONTENT_RESPONSE_SIZE_OR_TRAILER_UNPROVEN")
    return body[12:12 + declared]


def join_segments(
    segments: list[dict[str, Any]], *, source_start: int, source_length: int,
) -> tuple[str, dict[str, Any]]:
    if (type(source_start) is not int or source_start < 0
            or type(source_length) is not int or not 0 < source_length <= 1048576
            or not 0 < len(segments) <= 256):
        raise BoundedError("F10_SEGMENT_DECLARED_RANGE_OR_BOUND_INVALID")
    offset, end = source_start, source_start + source_length
    parts: list[bytes] = []
    members = []
    for segment in segments:
        data = segment.get("content")
        length = segment.get("requested_length")
        if (type(segment.get("start")) is not int or segment["start"] != offset
                or type(length) is not int or not 0 < length <= 8192
                or not isinstance(data, bytes) or len(data) != length
                or offset + length > end
                or segment.get("sha256") != "sha256:" + hashlib.sha256(data).hexdigest()):
            raise BoundedError("F10_SEGMENT_GAP_OVERLAP_SIZE_OR_HASH_CHANGED")
        parts.append(data)
        members.append({k: segment[k] for k in ("start", "requested_length", "sha256")})
        offset += length
    if offset != end:
        raise BoundedError("F10_SEGMENT_DECLARED_BODY_INCOMPLETE")
    joined = b"".join(parts)
    try:
        text = joined.decode("gbk", errors="strict")
    except UnicodeDecodeError:
        raise BoundedError("F10_SEGMENT_SOURCE_ENCODING_UNQUALIFIED") from None
    if not text.strip() or text.encode("gbk", errors="strict") != joined:
        raise BoundedError("F10_SEGMENT_EXACT_SOURCE_ROUNDTRIP_UNQUALIFIED")
    proof = dict(
        contract_id=CONTRACT_ID, source_start=source_start, source_length=source_length,
        segments=members, complete_source_range=True,
        source_bytes_sha256="sha256:" + hashlib.sha256(joined).hexdigest(),
        UTF8_content_sha256="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        decoding="GBK_STRICT_AFTER_BYTE_JOIN", provider_PIT_qualified=False,
        source_priority_granted=False,
    )
    proof["identity"] = digest(proof)
    return text, proof
