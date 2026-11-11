"""Self-consistently repacked admissions must fail before any file access."""

from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import base22_factor_lineage_audit as audit
from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError


def descriptor():
    return {
        "schema_version": 1,
        "contract_id": audit.CONTRACT_ID,
        "purpose": audit.PURPOSE,
        "READY": False,
        "complete_Base22": False,
        "historical_PIT_admission": False,
        "original_normalization_manifest_restored": False,
        "original_vendor_decimal_precision_recovered": False,
        "provider_acquisition_inferred": False,
        "native_atoms_changed": 0,
        "consumer_admission": "NOT_GRANTED",
        "members": {
            r: {
                "path": ("payload/" if r in {*audit.TABLES, *audit.PARENTS} else "sources/")
                + r
                + ".parquet"
            }
            for r in audit.ROLES
        },
        "bindings": {"path": "evidence/availability-bindings.parquet"},
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("READY", True),
        ("complete_Base22", True),
        ("historical_PIT_admission", True),
        ("original_normalization_manifest_restored", True),
        ("original_vendor_decimal_precision_recovered", True),
        ("provider_acquisition_inferred", True),
        ("native_atoms_changed", 1),
        ("native_atoms_changed", False),
        ("consumer_admission", "PASS"),
        ("purpose", "FINAL_BASE22"),
    ],
)
def test_forged_admission_is_rejected_before_io(tmp_path, monkeypatch, field, value):
    def forbidden(*args, **kwargs):
        raise AssertionError("scope validation must precede filesystem access")

    monkeypatch.setattr(audit, "_tree", forbidden)
    d = descriptor()
    d[field] = value
    d["identity"] = digest(d)
    with pytest.raises(BoundedError, match="DISPOSITION"):
        audit.audit_native_factor_scope(tmp_path / "absent", d, None)


@pytest.mark.parametrize(
    "mutation", ["role", "external", "renamed", "extra-file", "empty-partition"]
)
def test_physical_roles_and_zero_row_extra_partition_cannot_be_declared_away(
    tmp_path, monkeypatch, mutation
):
    monkeypatch.setattr(
        audit, "_tree", lambda *args: (_ for _ in ()).throw(AssertionError("premature IO"))
    )
    d = descriptor()
    if mutation == "role":
        d["members"].pop(audit.TABLES[0])
    elif mutation in {"external", "renamed"}:
        d["members"][audit.TABLES[0]]["path"] = (
            "/old/source/table.parquet" if mutation == "external" else "payload/renamed.parquet"
        )
    else:
        d["physical_members"] = (
            {m["path"]: {} for m in d["members"].values()}
            | {"evidence/availability-bindings.parquet": {}}
            | {"contract/" + n: {} for n in audit.CONFIGURATION}
        )
        extra = (
            "payload/extra.parquet" if mutation == "extra-file" else "payload/empty/part.parquet"
        )
        d["physical_members"][extra] = {"rows": 0}
    d["identity"] = digest(d)
    with pytest.raises(BoundedError):
        audit.audit_native_factor_scope(Path(tmp_path / "absent"), d, None)
