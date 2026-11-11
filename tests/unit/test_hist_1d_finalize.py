import pytest

from quanthus.pipelines.hist_1d_finalize import PROFILE, require_acceptance


def test_release_gate_binds_owner_scope_and_rejects_unknown_binary() -> None:
    contract = {
        "acceptance": {
            "profile": PROFILE,
            "authority": "explicit_owner_feedback",
            "binary_st_required": True,
            "owner_instruction": "binary required; subtype deferred",
            "deferred_issue": "https://github.com/rv198-star/Quanthus/issues/606",
        }
    }
    good = {"expected": 2, "known": 2, "missing": 0, "conflicting": 0}
    require_acceptance(contract, good)
    for bad in [
        {**good, "missing": 1, "known": 1},
        {**good, "conflicting": 1},
        {**good, "known": 1},
    ]:
        with pytest.raises(ValueError, match="REQUIRED_BINARY_ST"):
            require_acceptance(contract, bad)
    contract["acceptance"]["binary_st_required"] = False
    with pytest.raises(ValueError, match="OWNER_BINARY_ST"):
        require_acceptance(contract, good)
