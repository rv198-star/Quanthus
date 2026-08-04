from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_cn01_dwd_input_units_use_new_control_plane() -> None:
    daily_service = (ROOT / "ops/systemd/cn01-dwd-daily-input.service").read_text(
        encoding="ascii"
    )
    weekly_service = (ROOT / "ops/systemd/cn01-dwd-weekly-input.service").read_text(
        encoding="ascii"
    )
    daily_timer = (ROOT / "ops/systemd/cn01-dwd-daily-input.timer").read_text(encoding="ascii")
    weekly_timer = (ROOT / "ops/systemd/cn01-dwd-weekly-input.timer").read_text(encoding="ascii")

    for service, mode in ((daily_service, "daily"), (weekly_service, "weekly")):
        assert "run-cn01-dwd" in service
        assert f"--mode {mode}" in service
        assert "--ods-base" in service
        assert "--dwd-base" in service
        assert "--run-root" in service
        assert "--wait-seconds" in service
        assert "--poll-seconds" in service
        assert "run-cn01-daily-dwd" not in service

    assert "After=network-online.target cn01-ods-intraday-5m.service" in daily_service
    assert "After=network-online.target cn01-ods-weekly-corrections.service" in weekly_service

    assert "OnCalendar=Mon..Fri *-*-* 23:20:00 Asia/Shanghai" in daily_timer
    assert "Persistent=true" in daily_timer
    assert "OnCalendar=Sat *-*-* 09:00:00 Asia/Shanghai" in weekly_timer
    assert "Persistent=false" in weekly_timer
    assert "TimeoutStartSec=6h" in weekly_service
