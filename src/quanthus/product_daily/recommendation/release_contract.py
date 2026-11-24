"""One file-bound boundary for release selection, composition and byte closure.

The registry chooses a release by the package's policy identity.  Public callers
cannot choose a wire or supply replacement policy/receipt/manifest objects.  The
historical structural-fixture route is registered separately from daily v2.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from quanthus.product_daily.contracts import ProductDailyError

CONFIG = "config/research/r11/d1b-20261124/"
REGISTRY = CONFIG + "release-registry.v1.json"
ROLES = {
    "seam": "src/quanthus/product_daily/recommendation/rule_seam.py",
    "producer": "src/quanthus/product_daily/recommendation/producer.py",
    "expert_runner": "src/quanthus/expert_strategy/runner.py",
    "event_scan": "src/quanthus/event_library/scan.py",
    "entry_seam": "src/quanthus/product_daily/risk.py",
    "trend_producer": "src/quanthus/expert_evaluation/experts/trend_stability.py",
    "context_builder": "src/quanthus/expert_strategy/runner.py:build_formal_expert_context",
    "publish_path": "src/quanthus/product_daily/recommendation/publish.py",
    "contracts_path": "src/quanthus/product_daily/recommendation/contracts.py",
    "runtime_identity": "src/quanthus/product_daily/recommendation/runtime_identity.py",
    "release_loader": "src/quanthus/product_daily/recommendation/release_contract.py",
}
EXPERT_FILES = {
    "card_sha256": "config/expert-strategies/p4-trend-risk.v1.0.1.json",
    "trend_expert_config_sha256": "config/expert-evaluations/experts/trend-stability.v1.json",
    "trend_eval_spec_sha256": (
        "config/expert-evaluations/specs/trend-stability-p4-first-passage.evaluation.v1.json"
    ),
    "position_eval_spec_sha256": (
        "config/expert-evaluations/specs/position-risk-formal-policy.evaluation.v1.json"
    ),
}
SEMANTIC_ROOTS = ("src/quanthus/", "config/", "tests/")
GOVERNANCE_FILES = frozenset(
    {
        "config/planning/learning-blueprint.v1.json",
        "config/planning/learning-progress.v1.json",
        "config/planning/exploration-conclusions.v1.json",
    }
)
SELF_MANIFESTS = frozenset(
    {CONFIG + "release-manifest.v2.json", CONFIG + "release-manifest.g1-compat.v2.json"}
)
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"(?:sha256:)?([0-9a-f]{64})\Z")


def _fail(message: str) -> NoReturn:
    raise ProductDailyError("manifest_closure_unverified", message)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        _fail(f"{label} must be a non-empty object")
    return dict(value)


def _path(root: Path, value: Any, label: str, *, required: bool = True) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        _fail(f"{label} requires a project-relative file path")
    path = root / value
    if ".." in Path(value).parts or path.resolve().is_relative_to(root) is False:
        _fail(f"{label} escapes the release root")
    if required and (not path.is_file() or path.is_symlink()):
        _fail(f"{label} path absent or not a regular file: {value}")
    return path


def _sha(value: Any, label: str) -> str:
    match = _HEX64.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        _fail(f"{label} requires a full sha256")
    assert match is not None
    return match[1]


def _file_binding(root: Path, value: Any, label: str) -> Path:
    binding = _object(value, label)
    path = _path(root, binding.get("path"), label)
    expected = _sha(binding.get("sha256"), label)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        _fail(f"{label} sha256 mismatch: {binding['path']}")
    return path


def _git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProductDailyError(
            "manifest_closure_unverified", f"Git closure unavailable: {args}"
        ) from exc


def _json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ProductDailyError(
            "manifest_closure_unverified", f"unreadable release file: {path}"
        ) from exc


def _registry(root: Path) -> tuple[dict[str, Any], ...]:
    raw = _object(_json_file(_path(root, REGISTRY, "registry")), "registry")
    if set(raw) != {"schema_version", "releases"} or raw["schema_version"] != (
        "quanthus.p11.release-registry.v1"
    ):
        _fail("release registry schema/fields differ")
    entries = raw["releases"]
    if not isinstance(entries, list) or not entries:
        _fail("release registry requires registrations")
    ids: set[str] = set()
    policies: set[tuple[str, str]] = set()
    required = {"manifest_id", "manifest_path", "policy_id", "policy_version", "evidence_grades"}
    result: list[dict[str, Any]] = []
    for value in entries:
        item = _object(value, "release registration")
        if set(item) != required or any(
            not isinstance(item[k], str) or not item[k] for k in required - {"evidence_grades"}
        ):
            _fail("release registration fields must be complete strings")
        grades = item["evidence_grades"]
        if (
            not isinstance(grades, list)
            or not grades
            or any(not isinstance(grade, str) or not grade for grade in grades)
        ):
            _fail("registered evidence_grades must be non-empty strings")
        identity = (item["policy_id"], item["policy_version"])
        if item["manifest_id"] in ids or identity in policies:
            _fail("duplicate release registration")
        _path(root, item["manifest_path"], "registered manifest")
        ids.add(item["manifest_id"])
        policies.add(identity)
        result.append(item)
    return tuple(result)


def _registration(root: Path, manifest_id: Any) -> dict[str, Any]:
    for item in _registry(root):
        if item["manifest_id"] == manifest_id:
            return item
    _fail(f"unregistered release manifest: {manifest_id}")
    raise AssertionError("unreachable")


def validate_manifest_structure(raw: dict[str, Any]) -> None:
    """Require every v2 role/binding even before doing I/O verification."""
    code = _object(raw.get("code_entry"), "code_entry")
    for key, expected in ROLES.items():
        if code.get(key) != expected:
            _fail(f"code_entry {key} must name {expected}")
    for key in ("code_commit", "code_tree"):
        if not isinstance(code.get(key), str) or _HEX40.fullmatch(code[key]) is None:
            _fail(f"code_entry.{key} requires a canonical full40hex identity")
    strategy = _object(raw.get("strategy"), "strategy")
    if strategy.get("card_path") != EXPERT_FILES["card_sha256"]:
        _fail("strategy.card_path differs from the registered card")
    _sha(strategy.get("card_sha256"), "strategy card")
    expert = _object(raw.get("expert_binding"), "expert_binding")
    for key in EXPERT_FILES:
        _sha(expert.get(key), f"expert_binding {key}")
    closure = _object(raw.get("input_closure"), "input_closure")
    registry = _object(closure.get("feature_registry"), "feature_registry")
    if registry.get("path") != "config/p3-baseline-features.research.v1.json":
        _fail("feature_registry path differs")
    _sha(registry.get("sha256"), "feature_registry")
    industry = _object(closure.get("industry_mapping"), "industry_mapping")
    if not isinstance(industry.get("path"), str) or not industry["path"]:
        _fail("industry_mapping path required even while pending")
    if industry.get("binding_state") in {"pending", "blocked"}:
        if industry.get("sha256") is not None:
            _fail("pending industry_mapping must have null sha256")
    elif industry.get("binding_state") == "bound":
        _sha(industry.get("sha256"), "industry_mapping")
    else:
        _fail("industry_mapping binding_state must be explicit")
    bindings = _object(raw.get("release_bindings"), "release_bindings")
    if set(bindings) != {"machine_policy", "action_receipts", "consumer"}:
        _fail("release_bindings requires machine_policy/action_receipts/consumer")
    for name, binding in bindings.items():
        item = _object(binding, name)
        if not isinstance(item.get("path"), str) or not item["path"]:
            _fail(f"{name} path is required")
        _sha(item.get("sha256"), name)
    wire = _object(raw.get("held_reduction_wire"), "held_reduction_wire")
    if wire.get("wire_version") not in {"p12.decision-set.v1", "p12.decision-set.v2"}:
        _fail("unregistered held_reduction_wire.wire_version")
    if (
        wire["wire_version"] == "p12.decision-set.v2"
        and closure.get("consumer_binding_v2") != (bindings["consumer"]["path"])
    ):
        _fail("input_closure.consumer_binding_v2 must name the bound consumer file")


def _without_code_identity(value: Any) -> dict[str, Any]:
    raw = _object(value, "self manifest")
    result = {**raw, "code_entry": _object(raw.get("code_entry"), "self manifest code")}
    result["code_entry"].pop("code_commit", None)
    result["code_entry"].pop("code_tree", None)
    return result


def _verify_git_closure(root: Path, code: dict[str, Any]) -> None:
    commit, tree = code["code_commit"], code["code_tree"]
    if _git(root, "rev-parse", f"{commit}^{{commit}}").decode().strip() != commit or (
        _git(root, "rev-parse", f"{commit}^{{tree}}").decode().strip() != tree
    ):
        _fail("code_entry commit/tree do not resolve to the declared canonical identity")
    _git(root, "merge-base", "--is-ancestor", commit, "HEAD")
    paths: set[str] = set()
    # Index is a separate semantic surface, even when the worktree was restored.
    for args in (
        ("diff", "--name-only", "-z", commit, "--", *SEMANTIC_ROOTS),
        ("diff", "--cached", "--name-only", "-z", commit, "--", *SEMANTIC_ROOTS),
        ("ls-files", "--others", "--exclude-standard", "-z", "--", *SEMANTIC_ROOTS),
    ):
        paths.update(p for p in _git(root, *args).decode().split("\0") if p)
    drifted = sorted(paths - GOVERNANCE_FILES - SELF_MANIFESTS)
    if drifted:
        _fail(f"semantic closure drifted vs {commit}: {drifted[:12]}")
    # Self-reference only permits rebinding the commit/tree, never omitting or
    # replacing release semantics. Index and disk must agree even for this case.
    for rel in sorted(paths & SELF_MANIFESTS):
        disk = _path(root, rel, "self manifest").read_bytes()
        index = _git(root, "show", f":{rel}")
        declared = _git(root, "show", f"{commit}:{rel}")
        if index != disk:
            _fail(f"self manifest index/worktree mismatch: {rel}")
        if _without_code_identity(json.loads(disk)) != _without_code_identity(json.loads(declared)):
            _fail(f"self manifest semantic drift: {rel}")


@dataclass(frozen=True)
class VerifiedRelease:
    registration: dict[str, Any]
    manifest: dict[str, Any]
    policy: dict[str, Any]
    receipts: tuple[dict[str, Any], ...]
    wire_version: str


def load_verified_release(
    root: Path, manifest_id: str, *, pending_is_failure: bool = False
) -> VerifiedRelease:
    """Reload all three pieces from registered files and verify their full closure."""
    from quanthus.product_daily.recommendation.rule_seam import (
        load_action_receipts,
        load_machine_policy,
        load_release_manifest,
    )

    root = root.resolve()
    registration = _registration(root, manifest_id)
    manifest = load_release_manifest(root / registration["manifest_path"])
    if manifest.get("manifest_id") != manifest_id:
        _fail("registered manifest_id differs from its file")
    validate_manifest_structure(manifest)
    for role, rel in ROLES.items():
        _path(root, rel.split(":", 1)[0], f"code_entry {role}")
    code = manifest["code_entry"]
    _verify_git_closure(root, code)
    closure = manifest["input_closure"]
    _file_binding(root, closure["feature_registry"], "feature_registry")
    _file_binding(
        root,
        {"path": manifest["strategy"]["card_path"], "sha256": manifest["strategy"]["card_sha256"]},
        "strategy",
    )
    for key, rel in EXPERT_FILES.items():
        _file_binding(root, {"path": rel, "sha256": manifest["expert_binding"][key]}, key)
    bindings = manifest["release_bindings"]
    bound = {name: _file_binding(root, value, name) for name, value in bindings.items()}
    policy = load_machine_policy(bound["machine_policy"])
    receipts = load_action_receipts(bound["action_receipts"])
    wire = manifest["held_reduction_wire"]["wire_version"]
    expected_policy_schema = "quanthus.p11-d1b.machine-policy." + (
        "v2" if wire == "p12.decision-set.v2" else "v1"
    )
    if policy.get("schema_version") != expected_policy_schema:
        _fail("machine-policy schema differs from the registered wire")
    if wire == "p12.decision-set.v2" and policy["held_reduction_wire"]["wire_version"] != wire:
        _fail("machine-policy held_reduction_wire differs")
    consumer = _object(_json_file(bound["consumer"]), "consumer binding")
    if wire == "p12.decision-set.v2" and consumer.get("consumer_schema") != wire:
        _fail("consumer schema differs from registered wire")
    industry = closure["industry_mapping"]
    if industry["binding_state"] == "bound":
        _file_binding(root, industry, "industry_mapping")
    else:
        _path(root, industry["path"], "pending industry_mapping", required=False)
        if pending_is_failure:
            _fail("industry_mapping binding is pending/blocked; release stays PENDING")
    if any(r["adoption_state"] == "adopted" for r in receipts) and not manifest["daily_adopted"]:
        _fail("action adopted while release-manifest daily_adopted=false")
    return VerifiedRelease(registration, manifest, policy, receipts, wire)


def verify_manifest(
    manifest: dict[str, Any], *, project_root: Path, pending_is_failure: bool
) -> VerifiedRelease:
    validate_manifest_structure(manifest)
    verified = load_verified_release(
        project_root, manifest.get("manifest_id", ""), pending_is_failure=pending_is_failure
    )
    if manifest != verified.manifest:
        _fail("release_manifest object differs from the registered verified file")
    return verified


def select_package_release(package: Any, root: Path) -> VerifiedRelease:
    """Select by the registered policy identity, never by a caller wire override."""
    identities = {
        (item.audit.policy_id, item.audit.policy_version) for item in package.decision_sets
    }
    if len(identities) != 1:
        _fail("one package must use one registered release policy")
    identity = next(iter(identities))
    for registration in _registry(root):
        if identity == (registration["policy_id"], registration["policy_version"]):
            if any(
                item.audit.evidence_grade not in registration["evidence_grades"]
                for item in package.decision_sets
            ):
                _fail("package evidence grade differs from its registered release")
            return load_verified_release(root, registration["manifest_id"])
    _fail(f"unregistered package policy identity: {identity}")
    raise AssertionError("unreachable")
