"""Bounded original PDF decode with a narrowly proven page-footer removal.

Only isolated centered bottom page numbers supported by consecutive PDF pages
and matching last text lines are removed. No body token, value, date or order is
rewritten. This successor is explicit; the original decoder stays unchanged.
"""

from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha
from .base22_bounded import BoundedError

CONTRACT_ID = "p10-base22-notice-bbox-proven-page-footers-v2"
MAX_TEXT_BYTES = 1024**2
MAX_BBOX_BYTES = 8 * 1024**2
_NS = {"p": "http://www.w3.org/1999/xhtml"}


def clean_proven_page_footers(layout: str, bbox: str) -> tuple[str, dict[str, Any]]:
    """Preserve all characters except exactly proven isolated footer digits."""
    if len(layout.encode()) > MAX_TEXT_BYTES or len(bbox.encode()) > MAX_BBOX_BYTES:
        raise BoundedError("NOTICE_PDF_DECODE_BYTE_BOUND_INVALID")
    # Poppler can emit a retained font control glyph as literal U+0001 inside
    # word text, which XML 1.0 cannot parse. Keep it exactly, including in layout;
    # only shield it while parsing markup. Controls in geometry/markup still fail.
    marker = "P10_NOTICE_LITERAL_XML_CONTROL_"
    if marker in bbox:
        raise BoundedError("NOTICE_PDF_BBOX_CONTROL_MARKER_COLLISION")
    controls = Counter(re.findall(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", bbox))
    shielded = re.sub(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f]", lambda m: marker + format(ord(m[0]), "04X") + "_END", bbox
    )
    try:
        root = ET.fromstring(shielded)
    except ET.ParseError:
        raise BoundedError("NOTICE_PDF_BBOX_UNREADABLE") from None
    for element in root.iter():
        if any(marker in v for v in element.attrib.values()) or marker in (element.tail or ""):
            raise BoundedError("NOTICE_PDF_BBOX_CONTROL_OUTSIDE_WORD_TEXT")
        if marker in (element.text or ""):
            if element.tag != "{" + _NS["p"] + "}word":
                raise BoundedError("NOTICE_PDF_BBOX_CONTROL_OUTSIDE_WORD_TEXT")
            element.text = re.sub(
                marker + r"([0-9A-F]{4})_END", lambda m: chr(int(m[1], 16)), element.text or ""
            )
    pages = root.findall(".//p:page", _NS)
    chunks = layout.split("\f")
    if chunks and not chunks[-1].strip():
        chunks.pop()
    if not 1 <= len(pages) <= 64 or len(chunks) != len(pages):
        raise BoundedError("NOTICE_PDF_EXACT_PAGE_SET_MISMATCH")
    candidates: dict[int, tuple[list[str], int, list[Decimal]]] = {}
    for number, page in enumerate(pages, 1):
        try:
            width, height = Decimal(page.attrib["width"]), Decimal(page.attrib["height"])
            if (
                not width.is_finite()
                or not height.is_finite()
                or not 0 < width < 2000
                or not 0 < height < 4000
            ):
                raise ValueError
            matching = []
            for line in page.findall(".//p:line", _NS):
                words = line.findall("p:word", _NS)
                if len(words) != 1 or (words[0].text or "").strip() != str(number):
                    continue
                coords = [Decimal(line.attrib[k]) for k in ("xMin", "yMin", "xMax", "yMax")]
                if not all(c.is_finite() for c in coords):
                    raise ValueError
                x0, y0, x1, y1 = coords
                if (
                    abs((x0 + x1) / 2 - width / 2) <= width / 20
                    and height * Decimal("0.90") <= y0 < y1 <= height
                    and 0 < x1 - x0 < 32
                    and 0 < y1 - y0 <= 18
                ):
                    matching.append(coords)
            if len(matching) > 1:
                raise BoundedError("NOTICE_PDF_FOOTER_GEOMETRY_AMBIGUOUS")
            if matching:
                lines = chunks[number - 1].splitlines(keepends=True)
                positions = [n for n, line in enumerate(lines) if line.strip()]
                if not positions or lines[positions[-1]].strip() != str(number):
                    raise BoundedError("NOTICE_PDF_FOOTER_LAYOUT_POSITION_DISAGREES")
                candidates[number] = (lines, positions[-1], matching[0])
        except (KeyError, InvalidOperation, ValueError):
            raise BoundedError("NOTICE_PDF_PAGE_GEOMETRY_INVALID") from None
    # One lone coincident numeric cell is not proof of a page counter. Only a
    # consecutive pair of matching PDF ordinal footers enables this profile.
    proven: set[int] = set()
    for number in candidates:
        if number + 1 in candidates:
            proven.update((number, number + 1))
    removed = []
    for number in sorted(proven):
        lines, position, _ = candidates[number]
        token = str(number)
        lines[position] = re.sub(r"(?<!\d)" + token + r"(?!\d)", "", lines[position], count=1)
        chunks[number - 1] = "".join(lines)
        removed.append(number)
    # Retain original page separators/trailing whitespace, including its final
    # form feed; no paragraph, currency, date or other body character is removed.
    original_chunks = layout.split("\f")
    result_chunks = original_chunks[:]
    for number in removed:
        result_chunks[number - 1] = chunks[number - 1]
    cleaned = "\f".join(result_chunks)
    evidence = {
        "contract_id": CONTRACT_ID,
        "original_text_sha256": digest(layout),
        "bbox_sha256": digest(bbox),
        "cleaned_text_sha256": digest(cleaned),
        "PDF_pages": len(pages),
        "removed_page_footers": removed,
        "removed_footer_digit_count": sum(len(str(n)) for n in removed),
        "body_digits_changed": False,
        "body_order_changed": False,
        "retained_XML_control_glyphs": {
            "U+" + format(ord(c), "04X"): n for c, n in controls.items()
        },
        "footer_geometry": {str(n): [str(v) for v in candidates[n][2]] for n in removed},
        "historical_time_or_value_inference": False,
    }
    evidence["identity"] = digest(evidence)
    return cleaned, evidence


def decode_proven_notice(pdf: Path, destination: Path) -> tuple[str, dict[str, Any]]:
    """Decode both representations locally, with no supplier or source access."""
    if pdf.stat().st_size > 10 * 1024**2 or destination.exists():
        raise BoundedError("NOTICE_PDF_DECODE_PHYSICAL_BOUND_INVALID")
    raw = destination.with_name(destination.name + ".layout")
    bbox = destination.with_name(destination.name + ".bbox")
    if raw.exists() or bbox.exists():
        raise BoundedError("NOTICE_PDF_DECODE_OUTPUT_ALREADY_EXISTS")
    before = file_sha(pdf)
    for args, output in [
        (["-layout", "-enc", "UTF-8"], raw),
        (["-bbox-layout", "-enc", "UTF-8"], bbox),
    ]:
        completed = subprocess.run(
            ["/usr/bin/pdftotext", *args, str(pdf), str(output)],
            capture_output=True,
            timeout=30,
            check=False,
        )
        maximum = MAX_TEXT_BYTES if output == raw else MAX_BBOX_BYTES
        if completed.returncode or not output.is_file() or output.stat().st_size > maximum:
            raise BoundedError("NOTICE_PDF_DECODE_LOCAL_REPRESENTATION_FAILED")
    text, evidence = clean_proven_page_footers(raw.read_text(), bbox.read_text())
    if file_sha(pdf) != before:
        raise BoundedError("NOTICE_PDF_SOURCE_BYTES_CHANGED_DURING_DECODE")
    evidence["PDF_sha256"] = before
    evidence["pdftotext_sha256"] = file_sha(Path("/usr/bin/pdftotext"))
    evidence.pop("identity")
    evidence["identity"] = digest(evidence)
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(text)
    return text, evidence
