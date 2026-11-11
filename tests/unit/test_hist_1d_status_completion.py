from datetime import datetime, timezone

from quanthus.pipelines.hist_1d_status_completion import binary_value, range_mask, runs


def test_binary_mapping_preserves_unknown_and_explicit_risk_class() -> None:
    assert binary_value({"status_type": "PROVIDER_DAILY_STATE", "status_value": "NORMAL"}) == 1
    assert binary_value({"status_type": "SPECIAL_TREATMENT", "status_value": "DELISTING_RISK"}) == 2
    assert binary_value({"status_type": "SPECIAL_TREATMENT", "status_value": "UNKNOWN"}) == 0


def test_lifetime_mask_is_half_open_and_missing_runs_keep_holes() -> None:
    opens = [datetime(2011, 1, d, 1, 30, tzinfo=timezone.utc) for d in [4, 5, 6, 7]]
    assert range_mask(opens, [(opens[0], opens[2])]) == bytearray([1, 1, 0, 0])
    assert runs([0, 1, 3]) == [[0, 2], [3, 4]]
