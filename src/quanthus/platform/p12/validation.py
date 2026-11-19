"""Validator for the closed subset of JSON Schema used by the P12 transport contract."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, TypeVar, cast

from quanthus.platform.p12.contract_generated import COMPONENTS

T = TypeVar("T")


class ContractViolation(ValueError):
    """A payload outside the transport contract; carries the contract reason code."""

    reason_code = "INVALID_REPORT_SHAPE"

    def __init__(self, path: str, detail: str) -> None:
        self.path = path
        super().__init__(f"INVALID_REPORT_SHAPE: {path}: {detail}")


def _ref(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], COMPONENTS[name.rsplit("/", 1)[-1]])


def _check(schema: dict[str, Any], value: Any, path: str) -> None:
    if "$ref" in schema:
        _check(_ref(schema["$ref"]), value, path)
        return
    if "anyOf" in schema:
        failures = []
        for option in schema["anyOf"]:
            try:
                _check(option, value, path)
                return
            except ContractViolation as exc:
                failures.append(str(exc))
        raise ContractViolation(path, "no admitted alternative: " + "; ".join(failures))
    if "const" in schema and value != schema["const"]:
        raise ContractViolation(path, f"must equal {schema['const']!r}")
    kind = schema.get("type")
    if kind == "null":
        if value is not None:
            raise ContractViolation(path, "must be null")
    elif kind == "string":
        if not isinstance(value, str):
            raise ContractViolation(path, "must be a string")
        if "enum" in schema and value not in schema["enum"]:
            raise ContractViolation(path, f"{value!r} is not an admitted value")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ContractViolation(path, "does not match the required format")
        if len(value) < schema.get("minLength", 0):
            raise ContractViolation(path, "is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ContractViolation(path, "is too long")
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError as exc:
                raise ContractViolation(path, "must be an ISO-8601 timestamp") from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ContractViolation(path, "timestamp must carry a timezone")
    elif kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ContractViolation(path, "must be an integer")
        if "minimum" in schema and value < schema["minimum"]:
            raise ContractViolation(path, f"must be >= {schema['minimum']}")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise ContractViolation(path, "must be a boolean")
    elif kind == "array":
        if not isinstance(value, list):
            raise ContractViolation(path, "must be an array")
        for index, item in enumerate(value):
            _check(schema["items"], item, f"{path}[{index}]")
    elif kind == "object":
        if not isinstance(value, dict):
            raise ContractViolation(path, "must be an object")
        expected = set(schema["required"])
        missing = sorted(expected.difference(value))
        extra = sorted(set(value).difference(expected))
        if missing or extra:
            raise ContractViolation(path, f"missing={missing} unexpected={extra}")
        for key, child in schema["properties"].items():
            _check(child, value[key], f"{path}.{key}")
    elif "enum" in schema and value not in schema["enum"]:
        raise ContractViolation(path, "not an admitted value")


def validate(component: str, value: T) -> T:
    """Raise ContractViolation unless ``value`` matches the named component exactly."""

    _check(COMPONENTS[component], value, component)
    return value
