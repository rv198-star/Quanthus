from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REBUILD = ROOT / "ops/bin/rebuild-cn01-dwd-august-202608"


def test_august_rebuild_uses_staging_root_in_market_date_order(tmp_path: Path) -> None:
    calls = tmp_path / "calls.log"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
market_date = args[args.index('--market-date') + 1]
daily_root = Path(args[args.index('--daily-root') + 1])
daily_root.mkdir(parents=True, exist_ok=True)
with Path(os.environ['REBUILD_CALLS']).open('a', encoding='ascii') as stream:
    stream.write(market_date + '\\n')
(daily_root / 'CURRENT.json').write_text(
    json.dumps({'market_date': market_date}, separators=(',', ':')) + '\\n',
    encoding='ascii',
)
""",
        encoding="ascii",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
    canonical = tmp_path / "canonical"
    rebuild = tmp_path / "rebuild"
    env = os.environ.copy()
    env.update(
        {
            "QUANTHUS_DWD_ENV_FILE": str(tmp_path / "missing.env"),
            "QUANTHUS_DWD_PYTHON": str(fake_python),
            "QUANTHUS_DWD_DAILY_ROOT": str(canonical),
            "QUANTHUS_DWD_DAILY_REBUILD_ROOT": str(rebuild),
            "REBUILD_CALLS": str(calls),
        }
    )

    completed = subprocess.run(
        [str(REBUILD)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert calls.read_text(encoding="ascii").splitlines() == [
        "2026-08-03",
        "2026-08-04",
        "2026-08-05",
        "2026-08-06",
        "2026-08-07",
        "2026-08-10",
        "2026-08-11",
    ]
    assert json.loads((rebuild / "CURRENT.json").read_text())["market_date"] == "2026-08-11"
    assert not canonical.exists()
