import json
from pathlib import Path

import pytest

from quanthus.pipelines.dwd_v21_rebuild_preflight import (
    DwdV21PreflightError,
    load_environment,
    write_receipt,
)


def test_environment_parser_does_not_evaluate_shell(tmp_path: Path) -> None:
    path = tmp_path / "rebuild.env"
    path.write_text("# comment\nQUANTHUS_DWD_EXECUTION_MODE=pilot_only\n", encoding="ascii")
    assert load_environment(path) == {"QUANTHUS_DWD_EXECUTION_MODE": "pilot_only"}
    path.write_text("export BAD=value\n", encoding="ascii")
    with pytest.raises(DwdV21PreflightError, match="invalid environment"):
        load_environment(path)


def test_preflight_receipt_is_immutable_and_canonically_bound(tmp_path: Path) -> None:
    path = write_receipt(
        tmp_path,
        {
            "schema_version": 1,
            "kind": "dwd_base_v2_1_gpu_p100_preflight_receipt",
            "completed_at": "2026-08-07T22:00:00+00:00",
            "status": "passed",
        },
    )
    value = json.loads(path.read_text(encoding="ascii"))
    assert value["canonical_digest"].startswith("sha256:")
    assert path.stat().st_mode & 0o777 == 0o440
    with pytest.raises(FileExistsError):
        write_receipt(
            tmp_path,
            {
                "schema_version": 1,
                "kind": "dwd_base_v2_1_gpu_p100_preflight_receipt",
                "completed_at": "2026-08-07T22:00:00+00:00",
                "status": "passed",
            },
        )
