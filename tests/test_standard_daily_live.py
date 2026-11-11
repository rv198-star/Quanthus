from datetime import date
from pathlib import Path

from quanthus.pipelines.securities_warehouse.standard_daily_live import finish


def test_legacy_daily_does_not_enable_standard_publication_implicitly(tmp_path: Path):
    value = {"status": "QUALIFIED_1D_NATIVE5M"}
    finish({}, tmp_path, date(2026, 11, 2), value, events=tmp_path / "absent")
    assert value == {"status": "QUALIFIED_1D_NATIVE5M"}
