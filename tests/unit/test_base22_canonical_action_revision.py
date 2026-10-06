from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_canonical_action_revision import (
    CONTRACT_ID,
    choose_successor_sources,
    revise_canonical_action,
    validate_revision_partition,
)
from quanthus.pipelines.securities_warehouse.disclosure_event import _stable_id


def case():
    old_clock = datetime(2026, 7, 31, tzinfo=timezone.utc)
    source_clock = datetime(2026, 10, 5, tzinfo=timezone.utc)
    new_clock = datetime(2026, 10, 6, tzinfo=timezone.utc)
    raw = {
        "source_row_key": "original-raw-key",
        "ex_date": date(2019, 6, 3),
        "stock_code": "000001",
        "action_id": "original-action",
    }
    aid = _stable_id("corporate_action", "corporate_actions", raw["source_row_key"])
    old = {
        "corporate_action_id": aid,
        "company_id": "company",
        "security_id": "security",
        "effective_at": datetime(2019, 6, 3, tzinfo=timezone.utc),
        "ex_date": raw["ex_date"],
        "record_date": None,
        "payment_date": None,
        "action_type": "CASH_DIVIDEND",
        "action_status": "IMPLEMENTED",
        "announced_at": old_clock,
        "record_source": "corporate_actions",
        "source_record_id": raw["source_row_key"],
        "source_revision": "native-r13",
        "source_artifact_sha256": "sha256:" + "a" * 64,
        "knowledge_from": old_clock,
        "knowledge_to": None,
    }
    candidate = {
        **old,
        "corporate_action_id": "new-provider-entity",
        "record_source": "p10_retained_eastmoney_implementation",
        "source_record_id": raw["action_id"],
        "source_revision": "qualified-source",
        "source_artifact_sha256": "sha256:" + "b" * 64,
        "knowledge_from": source_clock,
        "announced_at": None,
    }
    term = {
        "corporate_action_id": aid,
        "corporate_action_term_id": _stable_id(
            "corporate_action_term", aid, "CASH_AMOUNT_PER_SHARE"
        ),
        "term_type": "CASH_AMOUNT_PER_SHARE",
        "option_id": None,
        "currency": "CNY",
        "amount_per_share": Decimal("1.2345678901"),
        "ratio_numerator": None,
        "ratio_denominator": None,
        "subscription_price": None,
        **{
            k: old[k]
            for k in (
                "effective_at",
                "announced_at",
                "knowledge_from",
                "knowledge_to",
                "record_source",
                "source_record_id",
                "source_revision",
                "source_artifact_sha256",
            )
        },
    }
    qualified_term = {
        **term,
        "corporate_action_id": candidate["corporate_action_id"],
        "corporate_action_term_id": "source-term",
        **{
            k: candidate[k]
            for k in (
                "knowledge_from",
                "announced_at",
                "record_source",
                "source_record_id",
                "source_revision",
                "source_artifact_sha256",
            )
        },
    }
    binding = {
        "source_scope_identity": "sha256:" + "c" * 64,
        "raw_replay_receipt_sha256": "sha256:" + "d" * 64,
        "original_raw_member_sha256": old["source_artifact_sha256"],
        "original_raw_row_sha256": digest(raw),
        "qualified_action_sha256": digest(candidate),
        "qualified_term_rows_sha256": sorted([digest(qualified_term)]),
        "disposition": "UNCONTESTED_RAW_SOURCE_BOUND_SUCCESSOR",
        "historical_PIT_admission": False,
        "old_manifest_restored": False,
    }
    return (
        raw,
        old,
        candidate,
        [qualified_term],
        binding,
        new_clock,
        CONTRACT_ID + ":sha256:" + "e" * 64,
    )


def project(c):
    raw, old, candidate, terms, binding, clock, revision = c
    return revise_canonical_action(
        raw, old, candidate, terms, binding=binding, observed_at=clock, revision=revision
    )


def test_new_source_version_keeps_old_entity_and_exact_atoms():
    c = case()
    original = deepcopy(c)
    action, terms, proof = project(c)
    assert c == original
    assert action["corporate_action_id"] == c[1]["corporate_action_id"]
    assert action["knowledge_from"] == c[5]
    assert action["announced_at"] is None
    assert terms[0]["amount_per_share"].as_tuple() == c[3][0]["amount_per_share"].as_tuple()
    assert terms[0]["corporate_action_id"] == action["corporate_action_id"]
    assert proof["historical_PIT_admission"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_raw",
        "wrong_old_source",
        "wrong_entity",
        "different_date",
        "different_parent",
        "bad_raw_receipt",
        "wrong_candidate_hash",
        "unknown_source",
        "priority_unresolved",
        "old_publication_inferred",
        "old_manifest_claim",
        "not_later_clock",
        "legacy_revision",
        "closed_candidate",
        "closed_before",
        "term_parent",
        "term_clock",
        "duplicate_term",
        "term_hash",
        "bad_number",
        "explicit_publication",
        "known_at_missing",
    ],
)
def test_unqualified_or_misbound_source_cannot_revise(mutation):
    c = list(case())
    raw, old, candidate, terms, binding, _, _ = c
    if mutation == "wrong_raw":
        raw["stock_code"] = "000002"
    elif mutation == "wrong_old_source":
        old["record_source"] = "different"
    elif mutation == "wrong_entity":
        old["corporate_action_id"] = "invented"
    elif mutation == "different_date":
        candidate["ex_date"] = date(2019, 6, 4)
    elif mutation == "different_parent":
        candidate["security_id"] = "other"
    elif mutation == "bad_raw_receipt":
        binding["raw_replay_receipt_sha256"] = "missing"
    elif mutation == "wrong_candidate_hash":
        binding["qualified_action_sha256"] = "sha256:" + "f" * 64
    elif mutation == "unknown_source":
        candidate["record_source"] = "unreviewed"
    elif mutation == "priority_unresolved":
        binding["disposition"] = "SOURCE_PRIORITY_UNAVAILABLE"
    elif mutation == "old_publication_inferred":
        binding["historical_PIT_admission"] = True
    elif mutation == "old_manifest_claim":
        binding["old_manifest_restored"] = True
    elif mutation == "not_later_clock":
        c[5] = old["knowledge_from"]
    elif mutation == "legacy_revision":
        c[6] = "correction001"
    elif mutation == "closed_candidate":
        candidate["knowledge_to"] = c[5]
    elif mutation == "closed_before":
        old["knowledge_to"] = c[5]
    elif mutation == "term_parent":
        terms[0]["corporate_action_id"] = "other"
    elif mutation == "term_clock":
        terms[0]["knowledge_from"] = old["knowledge_from"]
    elif mutation == "duplicate_term":
        terms.append(deepcopy(terms[0]))
    elif mutation == "term_hash":
        binding["qualified_term_rows_sha256"] = []
    elif mutation == "bad_number":
        terms[0]["amount_per_share"] = Decimal("NaN")
    elif mutation == "explicit_publication":
        candidate["announced_at"] = old["knowledge_from"]
    elif mutation == "known_at_missing":
        candidate["knowledge_from"] = None
    with pytest.raises(BoundedError):
        project(c)


def test_unmentioned_term_is_not_filled_from_old_source_zero():
    c = case()
    _, rows, proof = project(c)
    assert {r["term_type"] for r in rows} == {"CASH_AMOUNT_PER_SHARE"}
    assert proof["absent_terms_imputed"] is False


def test_partition_closes_only_selected_parent_and_keeps_all_old_atoms():
    c = case()
    new, _, _ = project(c)
    old = c[1]
    unrelated = {**old, "corporate_action_id": "unrelated"}
    after = [{**old, "knowledge_to": c[5]}, unrelated, new]
    result = validate_revision_partition(
        [old, unrelated], after, {old["corporate_action_id"]}, [new], observed_at=c[5], action=True
    )
    assert result["before_rows"] == 2 and result["new_versions"] == 1


@pytest.mark.parametrize(
    "mutation",
    ["old_value", "unrelated_closed", "new_value", "missing_old", "duplicate_new", "missing_new"],
)
def test_full_repacked_partition_rejects_unauthorized_change(mutation):
    c = case()
    new, _, _ = project(c)
    old = c[1]
    unrelated = {**old, "corporate_action_id": "unrelated"}
    after = [{**old, "knowledge_to": c[5]}, deepcopy(unrelated), deepcopy(new)]
    if mutation == "old_value":
        after[0]["action_type"] = "changed"
    elif mutation == "unrelated_closed":
        after[1]["knowledge_to"] = c[5]
    elif mutation == "new_value":
        after[2]["record_date"] = date(2019, 6, 4)
    elif mutation == "missing_old":
        del after[0]
    elif mutation == "duplicate_new":
        after.append(deepcopy(new))
    elif mutation == "missing_new":
        del after[2]
    with pytest.raises(BoundedError):
        validate_revision_partition(
            [old, unrelated],
            after,
            {old["corporate_action_id"]},
            [new],
            observed_at=c[5],
            action=True,
        )


def test_new_term_type_may_be_added_but_old_term_is_retained_with_closed_interval():
    c = case()
    old = c[3][0]
    old = {**old, "corporate_action_id": c[1]["corporate_action_id"]}
    new = {
        **old,
        "corporate_action_term_id": "new-raw-bound-type",
        "term_type": "TRANSFER_SHARES_PER_SHARE",
        "knowledge_from": c[5],
        "announced_at": None,
    }
    after = [{**old, "knowledge_to": c[5]}, new]
    result = validate_revision_partition(
        [old], after, {old["corporate_action_id"]}, [new], observed_at=c[5], action=False
    )
    assert result["old_atoms_changed_except_selected_interval_end"] == 0


def test_source_selection_explicitly_holds_cash_and_prelisting_and_uses_exact_old_key():
    c = case()
    rows = [
        {
            "action_id": "positive",
            "effective_date": c[0]["ex_date"],
            "authority_source_row_ref": "actual-raw-ref",
        },
        {
            "action_id": "cash",
            "effective_date": c[0]["ex_date"],
            "authority_source_row_ref": "actual-raw-ref2",
        },
        {
            "action_id": "early",
            "effective_date": c[0]["ex_date"],
            "authority_source_row_ref": "actual-raw-ref3",
        },
    ]
    selected, held = choose_successor_sources(
        rows,
        {
            "EM": ({"positive": c[2]}, {c[2]["corporate_action_id"]: c[3]}),
            "F10": ({}, {}),
            "factor": ({}, {}),
        },
        {},
        cash_conflicts={"cash"},
        prelisting={"early"},
    )
    assert list(selected) == ["positive"]
    assert held == {
        "cash": "CASH_SOURCE_PRIORITY_UNAVAILABLE",
        "early": "BEFORE_FIRST_LISTED_COMPANY_IDENTITY",
    }


@pytest.mark.parametrize(
    "mutation",
    ["missing_raw_key", "other_date", "missing_terms", "unknown_held", "duplicate_required"],
)
def test_complete_required_key_scope_cannot_shorten_or_infer(mutation):
    c = case()
    rows = [
        {
            "action_id": "positive",
            "effective_date": c[0]["ex_date"],
            "authority_source_row_ref": "actual-raw-ref",
        }
    ]
    sources = {
        "EM": ({"positive": c[2]}, {c[2]["corporate_action_id"]: c[3]}),
        "F10": ({}, {}),
        "factor": ({}, {}),
    }
    conflicts = set()
    if mutation == "missing_raw_key":
        sources["EM"][0].clear()
    elif mutation == "other_date":
        c[2]["ex_date"] = date(2019, 6, 4)
    elif mutation == "missing_terms":
        sources["EM"][1].clear()
    elif mutation == "unknown_held":
        conflicts.add("outside-scope")
    elif mutation == "duplicate_required":
        rows.append(dict(rows[0]))
    with pytest.raises(BoundedError):
        choose_successor_sources(rows, sources, {}, cash_conflicts=conflicts, prelisting=set())
