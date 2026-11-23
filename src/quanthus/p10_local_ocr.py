"""Bounded failed-page OCR worker. Only local model files are used."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def run(pdf: Path, pages: list[int], *, expected_models: str) -> dict[str, Any]:
    package = importlib.import_module("rapidocr_onnxruntime")

    if importlib.metadata.version("rapidocr-onnxruntime") != "1.4.4":
        raise ValueError("OCR_VERSION_MISMATCH")
    if package.__file__ is None:
        raise ValueError("OCR_PACKAGE_LOCATION_UNKNOWN")
    directory = Path(package.__file__).parent
    models = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((directory / "models").glob("*.onnx"))
    }
    digest = hashlib.sha256(json.dumps(models, sort_keys=True).encode()).hexdigest()
    if not models or digest != expected_models:
        raise ValueError("OCR_MODEL_IDENTITY_MISMATCH")
    engine = package.RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)
    results = []
    for page in pages:
        with tempfile.TemporaryDirectory(prefix="p10-ocr-page-") as tmp:
            target = Path(tmp) / "page"
            subprocess.run(
                [
                    "/usr/bin/pdftoppm",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-scale-to",
                    "1600",
                    "-singlefile",
                    "-png",
                    str(pdf),
                    str(target),
                ],
                check=True,
                timeout=20,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            found, _ = engine(str(target) + ".png")
            text = "\n".join(str(row[1]) for row in (found or []))
            results.append(
                {"page": page, "text": text, "state": "OCR_TEXT" if text.strip() else "FAILED"}
            )
    return {"pages": results, "models_sha256": digest, "api_calls": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--pages", required=True)
    parser.add_argument("--expected-models-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pages = [int(x) for x in args.pages.split(",")]
    if not pages or len(pages) > 4 or any(x < 1 or x > 100 for x in pages):
        raise ValueError("OCR_PAGE_BUDGET")
    result = run(args.pdf, pages, expected_models=args.expected_models_sha256)
    with args.output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
