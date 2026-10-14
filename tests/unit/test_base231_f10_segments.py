import hashlib
import struct

import pytest

from quanthus.pipelines.securities_warehouse.base231_f10_segments import (
    content_response_bytes,
    join_segments,
)


@pytest.mark.parametrize("terminator", [b"", b"\x00"])
def test_declared_content_excludes_only_proven_optional_frame_terminator(terminator):
    data = "股份".encode("gbk")
    header = b"\x00\x00" + b"000615" + b"\x00\x00" + struct.pack("<H", len(data))
    assert content_response_bytes(header + data + terminator, code="000615",
                                  requested_length=len(data)) == data


@pytest.mark.parametrize("fault", ["nonzero-tail", "extra-tail", "truncation", "code", "size"])
def test_wire_framing_cannot_hide_body_or_identity_changes(fault):
    data = "股份".encode("gbk")
    header = b"\x00\x00" + b"000615" + b"\x00\x00" + struct.pack("<H", len(data))
    body = header + data
    if fault == "nonzero-tail":
        body += b"x"
    elif fault == "extra-tail":
        body += b"\x00\x00"
    elif fault == "truncation":
        body = body[:-1]
    elif fault == "code":
        body = body.replace(b"000615", b"000616")
    else:
        body = body[:10] + struct.pack("<H", len(data) - 1) + body[12:]
    with pytest.raises(RuntimeError):
        content_response_bytes(body, code="000615", requested_length=len(data))


def records(data, start=100):
    chunks = [data[:1], data[1:5], data[5:]]
    result = []
    offset = start
    for chunk in chunks:
        result.append(dict(start=offset, requested_length=len(chunk), content=chunk,
                           sha256="sha256:" + hashlib.sha256(chunk).hexdigest()))
        offset += len(chunk)
    return result


def test_byte_segments_decode_gbk_only_after_complete_join():
    data = "股东研究\n中文跨字节边界\n└完整矩阵┘".encode("gbk")
    text, proof = join_segments(records(data), source_start=100, source_length=len(data))
    assert text.encode("gbk") == data
    assert proof["decoding"] == "GBK_STRICT_AFTER_BYTE_JOIN"
    assert proof["complete_source_range"] is True


@pytest.mark.parametrize("fault", ["gap", "overlap", "truncated", "extra", "hash", "order",
                                    "invalid_gbk", "zero", "bound"])
def test_no_partial_missing_duplicated_or_lossy_body_can_pass(fault):
    data = "股东研究\n完整原始矩阵".encode("gbk")
    rs = records(data)
    length = len(data)
    if fault == "gap":
        rs[1]["start"] += 1
    elif fault == "overlap":
        rs[1]["start"] -= 1
    elif fault == "truncated":
        rs[-1]["content"] = rs[-1]["content"][:-1]
    elif fault == "extra":
        rs.append(rs[-1])
    elif fault == "hash":
        rs[-1]["sha256"] = "sha256:" + "0" * 64
    elif fault == "order":
        rs.reverse()
    elif fault == "invalid_gbk":
        data = b"\x81"
        rs = [dict(start=100, requested_length=1, content=data,
                   sha256="sha256:" + hashlib.sha256(data).hexdigest())]
        length = 1
    elif fault == "zero":
        length = 0
    else:
        length = 1048577
    with pytest.raises((RuntimeError, ValueError)):
        join_segments(rs, source_start=100, source_length=length)
