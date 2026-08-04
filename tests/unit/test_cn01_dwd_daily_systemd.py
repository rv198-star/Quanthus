from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_cn01_fixed_time_dwd_is_a_disabled_manual_fallback() -> None:
    service = (ROOT / "ops/systemd/cn01-dwd-daily.service").read_text(encoding="ascii")
    timer = (ROOT / "ops/systemd/cn01-dwd-daily.timer").read_text(encoding="ascii")

    assert "run-cn01-daily-dwd" in service
    assert "QUANTHUS_DWD_DAILY_ROOT" in service
    assert "Group=quanthus-dwd-ro" in service
    assert "TimeoutStartSec=60min" in service
    assert "manual fallback" in service
    assert "OnCalendar=Mon..Fri *-*-* 19:30:00 Asia/Shanghai" in timer
    assert "Unit=cn01-dwd-daily.service" in timer
    assert "Persistent=false" in timer
    assert "ConditionPathExists=/etc/quanthus/cn01-dwd-fixed-timer-enabled" in timer
