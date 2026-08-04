from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_cn01_dwd_daily_cleaning_runs_at_1930_from_base_seed() -> None:
    service = (ROOT / "ops/systemd/cn01-dwd-daily.service").read_text(encoding="ascii")
    timer = (ROOT / "ops/systemd/cn01-dwd-daily.timer").read_text(encoding="ascii")

    assert "run-cn01-daily-dwd" in service
    assert "QUANTHUS_DWD_DAILY_ROOT" in service
    assert "TimeoutStartSec=60min" in service
    assert "cn01-ods-daily-orchestrator.service" in service
    assert "OnCalendar=Mon..Fri *-*-* 19:30:00 Asia/Shanghai" in timer
    assert "Unit=cn01-dwd-daily.service" in timer
    assert "Persistent=false" in timer
    assert "ConditionPathExists" not in timer
