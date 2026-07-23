from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


class ConfigurationError(ValueError):
    """Raised when a versioned project configuration violates its contract."""


def find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    if candidate.is_file():
        candidate = candidate.parent

    for directory in (candidate, *candidate.parents):
        if (directory / "pyproject.toml").is_file() and (directory / "config").is_dir():
            return directory

    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file():
        return source_root
    raise ConfigurationError("unable to locate the Quanthus project root")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"configuration file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"configuration root must be an object: {path}")
    return value


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{path} must be an object")
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{path} must be an array")
    return value


def _non_empty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{path} must be a non-empty string")
    return value


def _positive_int(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigurationError(f"{path} must be a positive integer")
    return value


def _year_range(value: Any, path: str) -> tuple[int, int]:
    mapping = _mapping(value, path)
    start = _positive_int(mapping.get("start"), f"{path}.start")
    end = _positive_int(mapping.get("end"), f"{path}.end")
    if start > end:
        raise ConfigurationError(f"{path}.start must not exceed {path}.end")
    return start, end


def validate_policy(raw: Mapping[str, Any]) -> None:
    required_sections = {
        "schema_version",
        "policy_version",
        "status",
        "effective_date",
        "data",
        "clock",
        "orders",
        "risk",
        "capital",
        "events",
        "modules",
        "counterfactual",
        "validation",
    }
    missing = sorted(required_sections.difference(raw))
    if missing:
        raise ConfigurationError(f"policy is missing sections: {', '.join(missing)}")

    _non_empty_string(raw["schema_version"], "schema_version")
    _non_empty_string(raw["policy_version"], "policy_version")
    if raw["status"] != "frozen":
        raise ConfigurationError("status must be frozen for the active P0 policy")

    data = _mapping(raw["data"], "data")
    snapshot_fields = set(
        _sequence(data.get("snapshot_required_fields"), "data.snapshot_required_fields")
    )
    required_snapshot_fields = {
        "market_date",
        "knowledge_cutoff",
        "source_revision",
        "snapshot_id",
    }
    if not required_snapshot_fields.issubset(snapshot_fields):
        raise ConfigurationError("data.snapshot_required_fields is incomplete")

    missing_data = _mapping(data.get("missing_data_policy"), "data.missing_data_policy")
    if missing_data.get("structural_break") is not False:
        raise ConfigurationError("missing data must not be classified as structural break")

    orders = _mapping(raw["orders"], "orders")
    priority = _sequence(orders.get("candidate_priority"), "orders.candidate_priority")
    if len(priority) != len(set(priority)):
        raise ConfigurationError("orders.candidate_priority must be deterministic and unique")

    risk = _mapping(raw["risk"], "risk")
    _positive_int(
        risk.get("max_units_per_level1_industry"),
        "risk.max_units_per_level1_industry",
    )
    _positive_int(risk.get("max_new_units_per_day"), "risk.max_new_units_per_day")
    veto_priority = _sequence(risk.get("veto_priority"), "risk.veto_priority")
    if len(veto_priority) != len(set(veto_priority)):
        raise ConfigurationError("risk.veto_priority must not contain duplicates")
    if len(_sequence(risk.get("exit_triggers"), "risk.exit_triggers")) != 4:
        raise ConfigurationError("risk.exit_triggers must contain the four frozen triggers")

    events = _mapping(raw["events"], "events")
    if events.get("feature_window_includes_signal_day") is not True:
        raise ConfigurationError("the event feature window must include signal day T")
    if events.get("unexecutable_outcome") != "not_executable":
        raise ConfigurationError("unexecutable events must use the not_executable outcome")

    counterfactual = _mapping(raw["counterfactual"], "counterfactual")
    _positive_int(
        counterfactual.get("maximum_holding_days"),
        "counterfactual.maximum_holding_days",
    )

    validation = _mapping(raw["validation"], "validation")
    development = _year_range(validation.get("development_years"), "validation.development_years")
    arbitration = _year_range(
        validation.get("arbitration_years"), "validation.arbitration_years"
    )
    blind = _year_range(
        validation.get("permanent_blind_years"), "validation.permanent_blind_years"
    )
    if not (development[1] < arbitration[0] <= arbitration[1] < blind[0]):
        raise ConfigurationError("validation year ranges must be ordered and non-overlapping")
    required_manifest_fields = _sequence(
        validation.get("run_manifest_required_fields"),
        "validation.run_manifest_required_fields",
    )
    if len(required_manifest_fields) != len(set(required_manifest_fields)):
        raise ConfigurationError("run manifest fields must be unique")


@dataclass(frozen=True)
class P0Policy:
    path: Path
    raw: Mapping[str, Any]

    @property
    def version(self) -> str:
        return str(self.raw["policy_version"])

    @property
    def digest(self) -> str:
        encoded = json.dumps(self.raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def at(self, *keys: str) -> Any:
        value: Any = self.raw
        traversed: list[str] = []
        for key in keys:
            traversed.append(key)
            if not isinstance(value, dict) or key not in value:
                raise ConfigurationError(f"missing policy path: {'.'.join(traversed)}")
            value = value[key]
        return value


def load_policy(path: Path | None = None) -> P0Policy:
    configured_path = os.environ.get("QUANTHUS_POLICY_PATH")
    resolved = path
    if resolved is None and configured_path:
        resolved = Path(configured_path)
    if resolved is None:
        resolved = find_project_root() / "config" / "p0-policy.v1.json"
    resolved = resolved.resolve()
    raw = _read_json(resolved)
    validate_policy(raw)
    return P0Policy(path=resolved, raw=raw)
