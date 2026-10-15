"""Every automatic complete publisher resolves the same actual Git release."""

from pathlib import Path


def test_domain_and_complete_cycle_share_the_actual_writer_release():
    root = Path(__file__).resolve().parents[2]
    domain = (root / "ops/systemd/cn01-dwd-domain-continuation@.service").read_text()
    cycle = (root / "ops/systemd/quanthus-complete-daily.service").read_text()
    for unit in (domain, cycle):
        assert "WorkingDirectory=/opt/quanthus/data-delivery-current\n" in unit
        assert "/opt/quanthus/daily-core-current" not in unit
    assert "ExecStart=/opt/quanthus/data-delivery-current/ops/bin/" in domain
    assert "PYTHONPATH=/opt/quanthus/data-delivery-current/src" in domain
