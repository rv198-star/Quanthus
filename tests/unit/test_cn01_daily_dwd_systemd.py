from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_cn01_daily_dwd_runs_after_ods_5m_lane() -> None:
    service = (ROOT / "ops/systemd/cn01-daily-dwd.service").read_text(encoding="ascii")
    timer = (ROOT / "ops/systemd/cn01-daily-dwd.timer").read_text(encoding="ascii")
    assert "After=network-online.target cn01-ods-intraday-5m.service" in service
    assert "run-cn01-daily-dwd" in service
    assert "OnCalendar=Mon..Fri *-*-* 23:20:00 Asia/Shanghai" in timer
    assert "Persistent=true" in timer
