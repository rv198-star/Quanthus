from __future__ import annotations

from datetime import date

import pytest

from quanthus.stage_learning.qualification_source import (
    QualificationInputPolicy,
    QualificationReadScope,
    StageQualificationSourceError,
)

SPEC = "sha256:" + "a" * 64


def test_qualification_scope_admits_only_2022_2024_result_dates() -> None:
    scope = QualificationReadScope(
        date(2021, 7, 1),
        date(2022, 1, 4),
        date(2024, 12, 31),
    )
    assert scope.years == (2021, 2022, 2023, 2024)
    assert scope.to_dict()["report_end"] == "2024-12-31"

    with pytest.raises(StageQualificationSourceError, match="only 2022"):
        QualificationReadScope(date(2021, 7, 1), date(2021, 12, 31), date(2024, 12, 31))
    with pytest.raises(StageQualificationSourceError, match="only 2022"):
        QualificationReadScope(date(2021, 7, 1), date(2022, 1, 4), date(2025, 1, 2))


def test_qualification_input_policy_is_a_narrow_arbitration_lane() -> None:
    policy = QualificationInputPolicy(
        SPEC,
        ("000001", "600519"),
        "reference.calendar.trading-session:SSE-SZSE-daily-v1",
    )
    assert policy.partition == "arbitration"
    assert policy.view == "retrospective"

    with pytest.raises(StageQualificationSourceError, match="lane differs"):
        QualificationInputPolicy(
            SPEC,
            ("000001",),
            "calendar",
            partition="development",
        )


def test_qualification_policy_rejects_unbounded_or_invalid_security_identity() -> None:
    with pytest.raises(StageQualificationSourceError, match="six-digit"):
        QualificationInputPolicy(SPEC, ("000001", "000001"), "calendar")
    with pytest.raises(StageQualificationSourceError, match="six-digit"):
        QualificationInputPolicy(SPEC, ("AAPL",), "calendar")
