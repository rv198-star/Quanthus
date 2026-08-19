from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMOTE = ROOT / "ops/bin/promote-cn01-dwd-august-202608"


def test_promotion_rejects_incomplete_staging(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    canonical = tmp_path / "canonical"
    staging.mkdir()
    (staging / "CURRENT.json").write_text(
        json.dumps({"market_date": "2026-08-10"}), encoding="ascii"
    )

    completed = subprocess.run(
        [
            str(PROMOTE),
            "--staging-root",
            str(staging),
            "--canonical-root",
            str(canonical),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "did not reach 2026-08-11" in completed.stderr
    assert not canonical.exists()


def test_promotion_rejects_canonical_pointer_ahead(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    canonical = tmp_path / "canonical"
    staging.mkdir()
    canonical.mkdir()
    (staging / "CURRENT.json").write_text(
        json.dumps({"market_date": "2026-08-11"}), encoding="ascii"
    )
    (canonical / "CURRENT.json").write_text(
        json.dumps({"market_date": "2026-08-12"}), encoding="ascii"
    )

    completed = subprocess.run(
        [
            str(PROMOTE),
            "--staging-root",
            str(staging),
            "--canonical-root",
            str(canonical),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "move canonical CURRENT backwards" in completed.stderr
