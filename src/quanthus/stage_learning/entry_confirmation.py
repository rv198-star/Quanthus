"""Fixed-cohort qualification and blind-reference support, without fitting/scoring.

The two opinions are operational research references. Their consensus does not
grant execution, production admission, or independently established ground truth.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any


def training_support(
    rows: Sequence[Mapping[str, Any]], identities: Sequence[Mapping[str, Any]], *, cutoff: str
) -> dict[str, Any]:
    expected = {r["sample_id"]: r for r in identities}
    actual = {r["sample_id"]: r for r in rows}
    if len(actual) != len(rows) or len(expected) != len(identities) or set(actual) != set(expected):
        raise ValueError("ENTRY_TRAIN_IDENTITY_SET")
    for key, row in actual.items():
        if any(row[name] != expected[key][name] for name in expected[key]):
            raise ValueError("ENTRY_TRAIN_IDENTITY_CHANGED")
        if date.fromisoformat(row["anchor"]) > date.fromisoformat(cutoff):
            raise ValueError("ENTRY_TRAIN_CUTOFF")
        if type(row["label"]) is not int or row["label"] not in (0, 1):
            raise ValueError("ENTRY_TRAIN_LABEL")
    classes = {str(k): sum(r["label"] == k for r in rows) for k in (0, 1)}
    if len(rows) < 12 or min(classes.values()) < 4 or len({r["anchor"] for r in rows}) < 2:
        raise ValueError("ENTRY_TRAIN_SUPPORT")
    return {
        "rows": len(rows),
        "classes": classes,
        "mask_support": sorted({r["features"][6] for r in rows}),
        "anchor_blocks": len({r["anchor"] for r in rows}),
    }


def reference_support(
    population: Sequence[Mapping[str, Any]],
    a: Sequence[Mapping[str, Any]],
    b: Sequence[Mapping[str, Any]],
    *,
    minimum_definite: int = 12,
    minimum_companies: int = 5,
    minimum_blocks: int = 2,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Keep the whole denominator; do not expose individual opinions in the receipt.

    A caller may seal the returned consensus until the model/prediction commitment.
    Only qualified input rows can contribute definite consensus support.
    """
    expected = {r["blind_id"]: r for r in population}
    if not expected or len(expected) != len(population):
        raise ValueError("ENTRY_REFERENCE_POPULATION")
    allowed = {"POSITIVE", "NEGATIVE", "UNKNOWN", "UNAVAILABLE"}
    panels = []
    for panel in (a, b):
        mapped = {r["blind_id"]: r for r in panel}
        if len(mapped) != len(panel) or set(mapped) != set(expected):
            raise ValueError("ENTRY_REFERENCE_EXACT_SET")
        for row in mapped.values():
            if row["opinion"] not in allowed:
                raise ValueError("ENTRY_REFERENCE_OPINION")
            if row["opinion"] in {"POSITIVE", "NEGATIVE"} and not all(
                isinstance(row.get(k), str) and row[k].strip()
                for k in ("evidence", "counterconsideration")
            ):
                raise ValueError("ENTRY_REFERENCE_RATIONALE")
        panels.append(mapped)
    consensus = {}
    for key, row in expected.items():
        x, y = (panel[key]["opinion"] for panel in panels)
        consensus[key] = x if x == y and x in {"POSITIVE", "NEGATIVE"} else "UNKNOWN"
        if not row["available"]:
            if x != "UNAVAILABLE" or y != "UNAVAILABLE":
                raise ValueError("ENTRY_REFERENCE_UNAVAILABLE_RELABELED")
            consensus[key] = "UNAVAILABLE"
    definite = [key for key, value in consensus.items() if value in {"POSITIVE", "NEGATIVE"}]
    company_counts = {
        label: len({expected[k]["company_id"] for k in definite if consensus[k] == label})
        for label in ("POSITIVE", "NEGATIVE")
    }
    blocks = len({expected[k]["time_block"] for k in definite})
    failures = []
    if len(definite) < minimum_definite:
        failures.append("insufficient_definite_references")
    if min(company_counts.values()) < minimum_companies:
        failures.append("insufficient_distinct_companies_per_class")
    if blocks < minimum_blocks:
        failures.append("insufficient_time_blocks")
    return (
        {
            "opportunities": len(population),
            "definite_consensus": len(definite),
            "companies_by_class": company_counts,
            "time_blocks": blocks,
            "unknown": sum(v == "UNKNOWN" for v in consensus.values()),
            "unavailable": sum(v == "UNAVAILABLE" for v in consensus.values()),
            "failures": failures,
            "gate": "PASS" if not failures else "STOP_FIXED_BATCH",
            "individual_opinions_revealed": False,
            "consumer_admission": "NOT_GRANTED",
        },
        consensus,
    )


def require_reference_admission(receipt: Mapping[str, Any]) -> None:
    if receipt.get("gate") != "PASS" or receipt.get("failures"):
        raise ValueError("ENTRY_REFERENCE_NOT_ADMITTED_NO_FIT")
