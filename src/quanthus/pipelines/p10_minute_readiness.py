"""One P10 research READY gate: R0/R1/R2 admitted, no R3, no secondary approval.

A READY verdict is valid only AFTER the calling warehouse verifier has checked
the immutable SHA/SEALED evidence, genuine R0/R1/R2 source and physical
minute bars, authoritative market date and full security population.
Individual consumers can still require stricter observed bars by inspecting
the existing quality grade and missing timestamps; this gate does not relax
their separate exactness or point-in-time contracts.
"""

from __future__ import annotations

from collections.abc import Mapping

POLICY_ID = "p10-1m-r2-minimum-ready-v1"
GRADES = ("R0", "R1", "R2", "R3", "SUSPENDED")
DEFAULT_POPULATION = 4992


def ready_after_verified_storage(
    grades: Mapping[str, object],
    *,
    admitted_raw: int,
    expected_population: int = DEFAULT_POPULATION,
) -> bool:
    """Validate denominator and admission, then test R3=0 (no 99%/98% gates)."""
    if not isinstance(grades, Mapping):
        raise ValueError("minute readiness missing grade distribution")
    if (
        isinstance(admitted_raw, bool)
        or not isinstance(admitted_raw, int)
        or admitted_raw < 0
        or expected_population < 1
    ):
        raise ValueError("minute readiness missing admitted real originals")
    if any(name not in GRADES for name in grades):
        raise ValueError("minute readiness includes unknown security grade")
    values = {}
    for name in GRADES:
        value = grades.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("minute readiness grade counts must be nonnegative integers")
        values[name] = value
    if sum(values.values()) != expected_population:
        raise ValueError("minute readiness source population mismatch")
    traded = expected_population - values["SUSPENDED"]
    if traded <= 0 or admitted_raw != values["R0"] + values["R1"] + values["R2"]:
        raise ValueError("minute readiness admitted-source counts inconsistent")
    return values["R3"] == 0 and admitted_raw == traded
