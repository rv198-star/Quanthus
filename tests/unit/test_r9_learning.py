import hashlib
import json

import pytest

from quanthus.r9_learning import review_role


def fixture(tmp_path):
    rows = []
    opinions = []
    for i in range(8):
        packet = {
            "case_id": f"case-{i}",
            "available": True,
            "HLC40": [["105.0000", "95.0000", "100.0000"]] * 40,
        }
        rows.append(
            {
                "identity": {
                    "sample_id": f"case-{i}",
                    "company_id": f"company-{i}",
                    "role": "guard",
                    "anchor": "2020-07-31",
                },
                "available": True,
                "packet": packet,
            }
        )
        for reader in ["short_term", "fund"]:
            positive = i < 4
            cites = [
                {"offset": n, "field": "C", "value": "100.0000", "reason": "window evidence"}
                for n in (1, 21)
            ]
            opinion = {
                "case_id": f"case-{i}",
                "reviewer": reader,
                "label": "TRUE" if positive else "FALSE",
                "support": cites if positive else [],
                "counterevidence": [] if positive else cites,
                "rationale": "synthetic",
            }
            opinions.append(
                {"case_id": f"case-{i}", "reader": reader, "status": "VALID", "opinion": opinion}
            )
    qualified = tmp_path / "qualified.json"
    qualified.write_text(json.dumps({"rows": rows}))
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"opinions": opinions, "resolved": []}))
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "qualified_input": {
                    "qualified_cases_sha256": hashlib.sha256(qualified.read_bytes()).hexdigest()
                }
            }
        )
    )
    qa = {
        "role": "guard",
        "raw_reference_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        "reviewer": "independent-reader",
        "invalid_opinions": [],
    }
    review = tmp_path / "review.json"
    review.write_text(json.dumps(qa))
    return qualified, raw, review, request, tmp_path / "qa-sealed.json", qa


def test_qa_preserves_consensus_and_full_denominator(tmp_path):
    qualified, raw, review, request, out, _ = fixture(tmp_path)
    summary = review_role(qualified, raw, review, request, out, role="guard")
    assert (summary["positive"], summary["negative"], summary["unknown"]) == (4, 4, 0)
    assert summary["support_pass"]


def test_invalid_opinion_becomes_unknown_and_cannot_be_a_negative_label(tmp_path):
    qualified, raw, review, request, out, qa = fixture(tmp_path)
    qa["invalid_opinions"] = [
        {"case_id": f"case-{i}", "reader": "fund", "reason": "positive rationale unsupported"}
        for i in range(3)
    ]
    review.write_text(json.dumps(qa))
    summary = review_role(qualified, raw, review, request, out, role="guard")
    assert (summary["positive"], summary["negative"], summary["unknown"]) == (1, 4, 3)
    assert not summary["support_pass"]
    assert len(json.loads(out.read_bytes())["resolved"]) == 8


@pytest.mark.parametrize("change", ["raw_hash", "new_labels", "unknown_case", "duplicate"])
def test_qa_binding_and_no_third_vote(tmp_path, change):
    qualified, raw, review, request, out, qa = fixture(tmp_path)
    if change == "raw_hash":
        qa["raw_reference_sha256"] = "0" * 64
    elif change == "new_labels":
        qa["new_labels"] = {"case-0": "FALSE"}
    else:
        item = {
            "case_id": "case-0" if change == "duplicate" else "other",
            "reader": "fund",
            "reason": "invalid",
        }
        qa["invalid_opinions"] = [item, item] if change == "duplicate" else [item]
    review.write_text(json.dumps(qa))
    with pytest.raises(ValueError):
        review_role(qualified, raw, review, request, out, role="guard")
    assert not out.exists()


@pytest.mark.parametrize("bad", [{}, "", None])
def test_invalid_opinions_must_be_a_list(tmp_path, bad):
    qualified, raw, review, request, out, qa = fixture(tmp_path)
    qa["invalid_opinions"] = bad
    review.write_text(json.dumps(qa))
    with pytest.raises(ValueError, match="INVALID_LIST"):
        review_role(qualified, raw, review, request, out, role="guard")


def test_duplicate_qa_key_cannot_erase_an_exclusion(tmp_path):
    qualified, raw, review, request, out, qa = fixture(tmp_path)
    qa["invalid_opinions"] = [{"case_id": "case-0", "reader": "fund", "reason": "unsupported"}]
    review.write_text(json.dumps(qa)[:-1] + ',"invalid_opinions":[]}')
    with pytest.raises(ValueError, match="DUPLICATE_JSON_KEY"):
        review_role(qualified, raw, review, request, out, role="guard")


def test_duplicate_case_cannot_shrink_the_opinion_denominator(tmp_path):
    qualified, raw, review, request, out, qa = fixture(tmp_path)
    inputs = json.loads(qualified.read_text())
    inputs["rows"][7]["identity"]["sample_id"] = "case-6"
    qualified.write_text(json.dumps(inputs))
    request.write_text(
        json.dumps(
            {
                "qualified_input": {
                    "qualified_cases_sha256": hashlib.sha256(qualified.read_bytes()).hexdigest()
                }
            }
        )
    )
    with pytest.raises(ValueError, match="ROLE_DENOMINATOR"):
        review_role(qualified, raw, review, request, out, role="guard")
