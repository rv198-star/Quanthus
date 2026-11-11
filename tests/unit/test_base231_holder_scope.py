from datetime import date, datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse import base231_holder_scope as adapter
from tests.unit.test_base22_holder_change_source import project as old_project
from tests.unit.test_base23_ownership import BODY, project
from tests.unit.test_base23_ownership_reconfirmation import fixture


def change_project(body):
    import hashlib

    from tests.unit.test_base22_f10_ownership import resolve

    raw = fixture()
    raw.update(stock_code="002001", security_id="002001", content=body,
               market_date="2026-09-05", acquired_at="2026-09-05T08:00:03.123456+08:00",
               content_sha256="sha256:" + hashlib.sha256(body.encode()).hexdigest())
    return adapter.project_change_tables(
        raw, source_sha256="sha256:" + "a" * 64, resolver=resolve(),
        observed_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
    )


def test_certified_frame_keeps_component_denominator_and_source_extent():
    rows, proof = project(BODY)
    certificate = adapter.certify_frames(proof, rows["dwd_shareholding_observation"])
    assert certificate["certified_frames"] == 1
    assert certificate["rows"] == 1
    assert not certificate["issuer_top_ten_coverage_inferred"]
    assert not certificate["absent_holder_is_exit"]


def test_incomplete_and_conflicting_frames_do_not_get_certified():
    rows, proof = project(BODY.replace("\n└", "\n"))
    certificate = adapter.certify_frames(proof, rows["dwd_shareholding_observation"])
    assert certificate["certified_frames"] == 0 and certificate["held_frames"] == 1


def test_certificate_rejects_modified_rows_or_counts():
    rows, proof = project(BODY)
    values = rows["dwd_shareholding_observation"]
    with pytest.raises(RuntimeError, match="CERTIFICATE"):
        adapter.certify_frames(proof, [])
    values[0]["holding_quantity"] += 1
    with pytest.raises(RuntimeError, match="CERTIFICATE"):
        adapter.certify_frames(proof, values)


def test_same_date_values_do_not_grant_transaction_equivalence():
    a = dict(original_provider="tdx", original_event_id=None, original_disclosure_id=None,
             original_atom_sha256="sha256:" + "a" * 64)
    b = dict(a, original_provider="exchange", original_atom_sha256="sha256:" + "b" * 64)
    assert adapter.event_equivalence(a, b) == "UNPROVEN_OVERLAP_RETAIN_SEPARATE"


def test_matching_original_provider_event_identity_needs_exact_atom():
    a = dict(original_provider="exchange", original_event_id="event-1",
             original_disclosure_id="notice-1", original_atom_sha256="sha256:" + "a" * 64)
    assert adapter.event_equivalence(a, dict(a)) == "EXACT_ORIGINAL_EVENT_RECONFIRMATION"
    assert adapter.event_equivalence(a, dict(a, original_atom_sha256="sha256:" + "b" * 64)) == (
        "ORIGINAL_EVENT_BODY_REVISION_RETAIN_NEW_OBSERVATION"
    )


def test_existing_august_change_scope_is_preserved():
    rows, proof = old_project()
    assert rows["dwd_shareholding_change"]
    assert proof["contract_id"] != adapter.CHANGE_CONTRACT_ID


def test_september_successor_requires_new_observation():
    with pytest.raises(RuntimeError, match="SUCCESSOR"):
        adapter.project_change_tables({}, source_sha256="sha256:" + "a" * 64, resolver=None,
                                      through_date=date(2026, 9, 30))
    with pytest.raises(RuntimeError, match="SUCCESSOR"):
        adapter.project_change_tables({}, source_sha256="sha256:" + "a" * 64, resolver=None,
                                      through_date=date(2026, 10, 31),
                                      observed_at=datetime(2026, 10, 12, tzinfo=timezone.utc))


def test_september_change_preserves_signed_exact_source_and_current_knowledge():
    from tests.unit.test_base22_holder_change_source import BODY as CHANGE_BODY

    rows, proof = change_project(CHANGE_BODY.replace("2026-06-30", "2026-09-03"))
    row = rows[adapter.TABLE][0]
    assert str(row["share_change"]) == "-12500.0000000000"
    assert row["knowledge_from"] == datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert row["announced_at"] is None and row["disclosure_id"] is None
    assert not proof["transaction_equivalence_granted"]


def test_nonempty_unrecognized_change_section_is_a_hold_not_empty():
    rows, proof = change_project("【3.股东持股变动】\n未审定非空格式\n【4.其他】")
    assert not rows[adapter.TABLE]
    assert proof["blocks"][0]["reason"] == "HOLDER_CHANGE_NONEMPTY_SECTION_HEADER_UNQUALIFIED"


def test_future_implementation_and_october_are_not_fabricated_events():
    from tests.unit.test_base22_holder_change_source import BODY as CHANGE_BODY

    rows, proof = change_project(CHANGE_BODY.replace("2026-06-30", "2026-09-07"))
    assert not rows[adapter.TABLE]
    assert proof["blocks"][0]["status"] == "HELD_NOT_EMPTY_COVERAGE"
    rows, proof = change_project(CHANGE_BODY.replace("2026-06-30", "2026-10-01"))
    assert not rows[adapter.TABLE] and proof["counts"]["excluded_after_cutoff_rows"] == 1
