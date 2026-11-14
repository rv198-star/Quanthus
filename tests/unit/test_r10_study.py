"""Commitment attacks and an actual synthetic scheduled-correction orchestration."""

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from quanthus import r10_study as study
from quanthus.r10_inputs import REVISION, read, sha, write
from quanthus.stage_learning import head2_revision as hr
from quanthus.stage_learning import local_head as lh


def inputs(tmp_path):
    root = tmp_path / "qualification"
    root.mkdir()
    contract = tmp_path / "contract.json"
    write(
        contract,
        {
            "schema_version": "quanthus.r10-controlled-replay.v1",
            "owner_approved": True,
            "source": {"revision": REVISION},
            "purpose": "EXPOSED_CONTROLLED_ENGINEERING_REPLAY",
            "consumer_admission": "NOT_GRANTED",
            "runtime": {
                "total_deadline": (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
            },
        },
    )
    for name in (
        "selected.private.json",
        "E.private.json",
        "E-cases.private.json",
        "A-v0-predictions.private.json",
        "A-actions.private.json",
    ):
        write(root / name, {"rows": []})
    seal(root, contract)
    return root, contract


def seal(root, contract):
    freeze = {
        "contract_sha256": sha(contract),
        "selection_sha256": sha(root / "selected.private.json"),
        "E_sha256": sha(root / "E.private.json"),
        "E_cases_sha256": sha(root / "E-cases.private.json"),
    }
    (root / "source-freeze.safe.json").write_text(json.dumps(freeze))
    (root / "A-admission.safe.json").write_text(
        json.dumps(
            {
                "result_execution_ready": True,
                "contract_sha256": sha(contract),
                "source_freeze_sha256": sha(root / "source-freeze.safe.json"),
                "prediction_sha256": sha(root / "A-v0-predictions.private.json"),
                "action_sha256": sha(root / "A-actions.private.json"),
                "B_deadline": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
            }
        )
    )


@pytest.mark.parametrize(
    "name", ["selected.private.json", "E.private.json", "E-cases.private.json"]
)
def test_frozen_identity_or_features_changed_stops_before_update(tmp_path, monkeypatch, name):
    root, contract = inputs(tmp_path)
    (root / name).write_text('{"rows":[{"tampered_feature_or_identity":true}]}')
    monkeypatch.setattr(
        study, "begin_scheduled_correction", lambda *a, **k: pytest.fail("gradient")
    )
    with pytest.raises(ValueError, match="A_FROZEN_INPUT_CHANGED"):
        study.fit_correction(contract, root)


def test_replaced_evaluation_support_rejected_before_any_label_bytes(tmp_path, monkeypatch):
    root, contract = inputs(tmp_path)
    fit, ref = tmp_path / "fit", tmp_path / "reference"
    fit.mkdir()
    ref.mkdir()
    write(
        fit / "manifest.json",
        {
            "contract_sha256": sha(contract),
            "selected_sha256": sha(root / "selected.private.json"),
            "evaluation_label_files_opened": 0,
            "files": {},
            "supports": {"evaluation": {"resolved_sha256": "original"}},
        },
    )
    write(ref / "support-evaluation.safe.json", {"resolved_sha256": "replacement"})
    original = study.sha

    def guarded(path):
        if path.name == "resolved.private.json":
            pytest.fail("evaluation bytes read before commitment check")
        return original(path)

    monkeypatch.setattr(study, "sha", guarded)
    with pytest.raises(ValueError, match="EVALUATION_COMMITMENT_CHANGED"):
        study.compare(contract, root)


def test_actual_100_steps_without_evaluation_read_and_exact_parent_restore(tmp_path, monkeypatch):
    root, contract = inputs(tmp_path)
    original = tmp_path / "original"
    original.mkdir()

    def group(prefix, count, role, anchor, positives):
        return [
            {
                "identity": {
                    "sample_id": f"{prefix}{i}",
                    "security_id": f"sec-{prefix}{i}",
                    "episode_id": f"episode-{prefix}{i}",
                    "company_id": f"co-{prefix}{i}",
                    "anchor": anchor,
                    "role": role,
                },
                "features": [i / 25, 0.1 + i / 500, -0.4 + i / 100, 0.01, 0.8, 0.4, 0.0],
                "available": True,
                "test_label": "TRUE" if i < positives else "FALSE",
            }
            for i in range(count)
        ]

    train = group("t", 24, "train", "2020-12-31", 5)
    update = group("u", 8, "guard", "2021-05-31", 4)
    update[-1]["test_label"] = "UNKNOWN"
    extra = group("o", 32, "other", "2021-06-30", 4)
    guard = group("g", 8, "guard", "2021-05-31", 2)
    for r in guard[4:]:
        r["test_label"] = "UNKNOWN"
    evaluation = group("e", 24, "evaluation", "2021-07-01", 8)

    def labels(rows):
        return [{"case_id": r["identity"]["sample_id"], "label": r["test_label"]} for r in rows]

    def heads(rows):
        return [
            lh.HeadRow(
                r["identity"]["sample_id"],
                r["identity"]["security_id"],
                r["identity"]["episode_id"],
                r["identity"]["anchor"],
                tuple(r["features"]),
                int(r["test_label"] == "TRUE"),
            )
            for r in rows
        ]

    bindings = lh.HeadBindings(*("sha256:" + "1" * 64 for _ in range(8)))
    bindings = lh.HeadBindings(**dict(asdict(bindings), features=hr.feature_contract_digest()))
    parent = lh.fit_head(heads(train), policy=hr.revision_policy(), bindings=bindings)
    (original / "v0.json").write_bytes(lh.dump_checkpoint(parent))
    write(original / "selected.json", {"rows": train + update + extra})
    write(original / "train.json", {"resolved": labels(train)})
    write(original / "update.json", {"resolved": labels(update)})
    (original / "fit").mkdir()
    write(
        original / "fit/predictions.sealed.json",
        {
            "rows": [
                {
                    "case_id": r["identity"]["sample_id"],
                    "v0": lh.predict_head(parent, r["features"]).raw_score,
                }
                for r in train + update + extra
            ]
        },
    )
    pub = original / "publication-manifest.json"
    write(
        pub,
        {
            "files": [
                {
                    "path": "study/fit/predictions.sealed.json",
                    "sha256": sha(original / "fit/predictions.sealed.json"),
                }
            ]
        },
    )
    c = read(contract)
    c["old_assets"] = dict(
        root=str(original),
        parent=str(original / "v0.json"),
        parent_sha256=sha(original / "v0.json"),
        selected=str(original / "selected.json"),
        train_reference=str(original / "train.json"),
        update_reference=str(original / "update.json"),
    )
    c["asset_hashes"] = {str(p): sha(p) for p in original.glob("*.json")}
    contract.write_text(json.dumps(c))
    (root / "selected.private.json").write_text(
        json.dumps({"rows": [{"source_row": r} for r in guard + evaluation]})
    )
    seal(root, contract)
    ref = tmp_path / "reference"
    (ref / "qa-guard").mkdir(parents=True)
    write(ref / "qa-guard/resolved.private.json", {"resolved": labels(guard)})
    for role, count in (("guard", 8), ("evaluation", 24)):
        write(
            ref / ("support-" + role + ".safe.json"),
            {
                "support_pass": True,
                "contract_sha256": sha(contract),
                "role": role,
                "subjects": count,
                "resolved_sha256": sha(ref / "qa-guard/resolved.private.json")
                if role == "guard"
                else "sealed-evaluation-not-opened",
            },
        )
    real_read, real_sha = study.read, study.sha

    def guarded_read(path):
        if path.parent.name == "qa-evaluation":
            pytest.fail("evaluation labels opened in fitter")
        return real_read(path)

    monkeypatch.setattr(study, "read", guarded_read)
    monkeypatch.setattr(
        study,
        "sha",
        lambda p: (
            "d54cafeb0a8365e85e1d7a9a8ff6fa85c5071610d119c4bf8949fe51172b35c7"
            if p == pub
            else real_sha(p)
        ),
    )
    result = study.fit_correction(contract, root)
    assert result["new_updates"] == 1 and result["new_fits"] == 0
    assert result["steps"] == 100 and result["evaluation_label_files_opened"] == 0
    steps = read(tmp_path / "fit/steps.private.json")["rows"]
    assert [s["step"] for s in steps] == list(range(1, 101))
    assert result["train_replay_rows"] == 24 and result["update_definite_rows"] == 7
    assert sha(original / "v0.json") == sha(tmp_path / "fit/v0.json")
    assert read(tmp_path / "fit/original-restoration.safe.json")["predictions"] == 64
