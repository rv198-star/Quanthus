"""Paired unsealed materialization sharing one admitted period and window DAG."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base22_bounded import BoundedError, Budget, Limits
from .base23_native_image import materialize_native_image
from .base_build_composition import build_composition_plan
from .base_build_inputs import file_sha, identity, member
from .base_build_ods import materialize_ods
from .base_build_period import VerifiedInputs, load
from .base_build_profile import profile
from .base_build_stages import Stage
from .base_build_windows import audit_windows, build_windows


def predecessor_root(inputs: VerifiedInputs, role: str) -> Path:
    selected = next(
        r for r in inputs.frozen["content"]["selection"]["packages"] if r["role"] == role
    )
    return Path(inputs.roots[selected["id"]])


def materialize_dwd(
    *,
    predecessor: Path,
    period: Path,
    windows: Path,
    ods: Path,
    binding: dict[str, Any],
    contract: dict[str, Any],
    rules: dict[str, Any],
    model: dict[str, Any],
    extension: dict[str, Any],
    config_root: Path,
    output: Path,
    budget: Budget,
) -> dict[str, Any]:
    admission = load(member(period, "ADMISSION.json"))
    if admission.get("binding") != binding or admission.get("identity") != identity(
        {k: v for k, v in admission.items() if k != "identity"}
    ):
        raise BoundedError("BUILD_PAIR_PERIOD_BINDING_CHANGED")
    window_receipt = audit_windows(windows, budget)
    if load(windows / "PLAN.json")["period_admission_identity"] != admission["identity"]:
        raise BoundedError("BUILD_PAIR_WINDOW_BINDING_CHANGED")
    ods_record = load(member(ods, "MATERIALIZATION.json"))
    if (
        ods_record.get("input_identity") != binding["frozen_input_identity"]
        or ods_record.get("period_admission_identity") != admission["identity"]
        or ods_record.get("window_identity") != window_receipt["identity"]
        or ods_record.get("identity")
        != identity({k: v for k, v in ods_record.items() if k != "identity"})
    ):
        raise BoundedError("BUILD_PAIR_ODS_MATERIALIZATION_BINDING_CHANGED")
    plan = build_composition_plan(
        predecessor=predecessor,
        delta=member(period, "delta"),
        rules=rules,
        contract=contract,
        binding=binding,
        budget=budget,
    )
    old = load(member(predecessor, "MATERIALIZATION.json"))
    limitations = load_list(member(predecessor, "evidence/limitations.json"))
    evidence = {}
    for limitation in limitations:
        for relative in limitation["evidence_paths"]:
            path = member(predecessor, relative)
            if old.get("evidence", {}).get(relative) != file_sha(path):
                raise BoundedError("BUILD_PAIR_INHERITED_LIMITATION_EVIDENCE_CHANGED")
            evidence[relative] = path
    # Keep the complete old metadata as archival evidence under a separate
    # namespace; its source-root strings are never runtime dependencies.
    for relative in old.get("evidence", {}):
        evidence["evidence/predecessor-control/" + relative] = member(predecessor, relative)
    evidence["evidence/predecessor-control/manifest.json"] = member(predecessor, "manifest.json")
    evidence["evidence/predecessor-control/MATERIALIZATION.json"] = member(
        predecessor, "MATERIALIZATION.json"
    )
    evidence["evidence/build/ADMISSION.json"] = member(period, "ADMISSION.json")
    evidence["evidence/build/ODS-MATERIALIZATION.json"] = member(ods, "MATERIALIZATION.json")
    for name in ("PLAN.json", "CHECKPOINT.json", "WINDOWS.parquet"):
        evidence["evidence/build/windows/" + name] = member(windows, name)
    return materialize_native_image(
        plan=plan,
        rules=rules,
        contract=contract,
        model=model,
        extension=extension,
        limitations=limitations,
        local_evidence=evidence,
        output=budget.destination(output),
        allocated_limit=budget.limits.allocated_bytes,
        free_floor=budget.limits.free_floor_bytes,
        rss_limit=budget.limits.rss_bytes,
        wall_seconds=budget.limits.wall_seconds,
        config_root=config_root,
        profile=profile(contract),
    )


def load_list(path: Path) -> list[dict[str, Any]]:
    import json

    value = json.loads(path.read_bytes())
    if not isinstance(value, list):
        raise BoundedError("BUILD_PAIR_LIMITATIONS_LIST_REQUIRED")
    return value


def materialization_stages(
    *,
    inputs: VerifiedInputs,
    binding: dict[str, Any],
    contract: dict[str, Any],
    config_root: Path,
    limits: Limits,
) -> list[Stage]:
    """The period is a single graph predecessor for both layer consumers."""
    sources = [Path(p) for p in inputs.roots.values()]

    def windows_build(output: Path, dependencies: dict[str, Path]) -> dict[str, Any]:
        period = member(dependencies["period"], "period")
        with Budget(output, sources + [period], limits) as budget:
            receipt = build_windows(
                predecessor=predecessor_root(inputs, "ods"),
                period=period,
                binding=binding,
                output=output / "windows",
                budget=budget,
            )
        return {
            "identity": receipt["identity"],
            "counts": receipt["counts"],
            "resources": dict(budget.peak),
        }

    def windows_verify(output: Path, result: dict[str, Any]) -> None:
        # StageRunner checks every byte; this verifies semantic plan closure.
        # No writes are required; Budget.check also supports an absent root.
        budget = Budget(output.parent / ".window-verification", [output], limits)
        receipt = audit_windows(member(output, "windows"), budget)
        if receipt["identity"] != result["identity"] or receipt["counts"] != result["counts"]:
            raise BoundedError("BUILD_PAIR_WINDOW_STAGE_CHANGED")

    def ods_build(output: Path, dependencies: dict[str, Path]) -> dict[str, Any]:
        period, windows = (
            member(dependencies["period"], "period"),
            member(dependencies["windows"], "windows"),
        )
        with Budget(output, sources + [period, windows], limits) as budget:
            record = materialize_ods(
                inputs=inputs,
                period=period,
                windows=windows,
                contract=contract,
                output=output / "ODS",
                budget=budget,
            )
        return {
            "identity": record["identity"],
            "materialization_sha256": file_sha(output / "ODS/MATERIALIZATION.json"),
            "formal_READY": False,
            "source_members": len(record["files"]),
            "source_bytes": sum(r["size_bytes"] for r in record["files"]),
            "resources": dict(budget.peak),
        }

    def dwd_build(output: Path, dependencies: dict[str, Path]) -> dict[str, Any]:
        period, windows = (
            member(dependencies["period"], "period"),
            member(dependencies["windows"], "windows"),
        )
        ods = member(dependencies["ods"], "ODS")
        with Budget(output, sources + [period, windows, ods], limits) as budget:
            record = materialize_dwd(
                predecessor=predecessor_root(inputs, "dwd"),
                period=period,
                windows=windows,
                binding=binding,
                contract=contract,
                ods=ods,
                rules=load(config_root / "base22-independent-image.v1.json"),
                model=load(config_root / "dwd-domain-model.v1.json"),
                extension=load(config_root / "dwd-domain-model.v1.1-extension.json"),
                config_root=config_root,
                output=output / "DWD",
                budget=budget,
            )
        return {
            "materialization_sha256": file_sha(output / "DWD/MATERIALIZATION.json"),
            "tables": len(record["layout"]),
            "formal_READY": False,
            "rows_by_table": {
                t: sum(r["rows"] for r in entry["files"]) for t, entry in record["layout"].items()
            },
            "resources": dict(budget.peak),
        }

    def verify_image(layer: str, output: Path, result: dict[str, Any]) -> None:
        root = member(output, layer)
        if (
            (root / "READY").exists()
            or (root / "manifest.json").exists()
            or file_sha(root / "MATERIALIZATION.json") != result["materialization_sha256"]
        ):
            raise BoundedError("BUILD_PAIR_UNSEALED_STAGE_CHANGED")

    return [
        Stage("windows", ("period",), windows_build, windows_verify),
        Stage(
            "ods",
            ("period", "windows"),
            ods_build,
            lambda output, result: verify_image("ODS", output, result),
        ),
        Stage(
            "dwd",
            ("period", "windows", "ods"),
            dwd_build,
            lambda output, result: verify_image("DWD", output, result),
        ),
    ]
