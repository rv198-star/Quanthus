"""AR02: P12 module dependency direction, public entries and acyclic capabilities."""

from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY = json.loads((ROOT / "config/architecture/p12-module-boundaries.v1.json").read_text())


def _imports(path: Path) -> list[tuple[str, tuple[str, ...]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, ()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module, tuple(alias.name for alias in node.names)))
    return found


def _layer_files(layer: dict) -> list[Path]:
    return sorted((ROOT / layer["path"]).rglob("*.py"))


def _matches(module: str, prefixes: list[str]) -> bool:
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def test_layers_only_import_admitted_quanthus_modules() -> None:
    violations = []
    for layer in POLICY["layers"]:
        for path in _layer_files(layer):
            for module, _ in _imports(path):
                if module.startswith("quanthus") and not _matches(module, layer["may_import"]):
                    violations.append((layer["id"], path.relative_to(ROOT).as_posix(), module))
    assert violations == []


def test_core_layers_do_not_import_transport_storage_or_ui() -> None:
    core = {
        layer["id"]: layer for layer in POLICY["layers"] if layer["id"] in POLICY["core_layers"]
    }
    violations = []
    for layer in core.values():
        for path in _layer_files(layer):
            for module, _ in _imports(path):
                if _matches(module, POLICY["forbidden_in_core"]):
                    violations.append((path.relative_to(ROOT).as_posix(), module))
    assert violations == []


def test_capabilities_are_acyclic_and_independent_by_declared_edges() -> None:
    root = ROOT / "src/quanthus/capabilities"
    names = sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "__init__.py").exists())
    assert names == sorted(POLICY["capability_edges"])
    edges: dict[str, set[str]] = {name: set() for name in names}
    for name in names:
        for path in (root / name).rglob("*.py"):
            for module, _ in _imports(path):
                parts = module.split(".")
                if module.startswith("quanthus.capabilities.") and parts[2] != name:
                    edges[name].add(parts[2])
    for name, targets in edges.items():
        assert targets <= set(POLICY["capability_edges"][name]), (name, targets)
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node: str) -> None:
        assert node not in visiting, f"capability cycle through {node}"
        if node in done:
            return
        visiting.add(node)
        for target in edges[node]:
            visit(target)
        visiting.discard(node)
        done.add(node)

    for name in names:
        visit(name)


def test_outside_callers_use_capability_public_entries_only() -> None:
    callers = [
        ROOT / "src/quanthus/product_daily/application",
        ROOT / "src/quanthus/platform/p12",
        ROOT / "src/quanthus/platform/adapters",
    ]
    violations = []
    for base in callers:
        for path in sorted(base.rglob("*.py")):
            for module, names in _imports(path):
                if not module.startswith("quanthus.capabilities."):
                    continue
                parts = module.split(".")
                if len(parts) != 3:
                    violations.append((path.name, module, "submodule import"))
                    continue
                public = set(importlib.import_module(module).__all__)
                hidden = sorted(set(names).difference(public))
                if hidden:
                    violations.append((path.name, module, hidden))
    assert violations == []


def test_core_layers_load_in_a_minimal_process_without_platform_or_http() -> None:
    probe = (
        "import sys\n"
        "import quanthus.capabilities.accounting, quanthus.capabilities.execution\n"
        "import quanthus.capabilities.funding, quanthus.capabilities.risk\n"
        "import quanthus.capabilities.decision_access\n"
        "import quanthus.product_daily.application.desktop\n"
        "bad = sorted(m for m in sys.modules if m.startswith(('quanthus.platform', 'wsgiref',"
        " 'sqlite3', 'http.server', 'psycopg')))\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == ""


def test_platform_api_without_a_p12_runtime_does_not_load_the_p12_closure() -> None:
    """The API image copies only src/quanthus/platform plus listed modules; keep it bootable."""

    probe = (
        "import sys\n"
        "import quanthus.platform.composition, quanthus.platform.api\n"
        "bad = sorted(m for m in sys.modules if m.startswith(('quanthus.product_daily',"
        " 'quanthus.capabilities', 'quanthus.shared')))\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == ""
