from __future__ import annotations

import shutil
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_tail_union as u
from quanthus.pipelines.securities_warehouse import base22_tail_union_audit as a
from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    Limits,
    semantic_scan,
)
from quanthus.pipelines.securities_warehouse.base22_outputs import tail_observation
from tests.unit.test_base22_independent_image import _row
from tests.unit.test_base22_tail_union import fixture


@pytest.mark.parametrize("fault", [None, "purpose", "source", "raw-byte", "raw-value", "calendar"])
def test_cold_union_recomputes_global_constraints_and_original_raw(tmp_path, fault):
    schema, rule, old, _, save, freeze = fixture(tmp_path)
    from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas

    contract, model, extension = load_contract(Path("config/pipelines"))
    ss = schemas(model, extension)
    early = datetime(2011, 1, 1, tzinfo=timezone.utc)
    knowledge = datetime(2026, 10, 4, tzinfo=timezone.utc)
    limits = Limits(free_floor_bytes=1, allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2)
    with Budget(tmp_path / "job", [], limits) as budget:
        root = budget.root / "image"
        stage = root / "observations" / "synthetic-tail"
        cfg = stage / "contract"
        cfg.mkdir(parents=True)
        for name in (
            "base22-independent-image.v1.json",
            "dwd-domain-model.v1.json",
            "dwd-domain-model.v1.1-extension.json",
        ):
            shutil.copyfile(Path("config/pipelines") / name, cfg / name)
        listing = _row(ss["dwd_listing"])
        listing.update(
            listing_id="missing-listing",
            venue_id="venue",
            listing_currency="CNY",
            knowledge_from=early,
            effective_from=early,
        )
        calendar = _row(ss["dwd_trading_calendar"])
        calendar.update(
            calendar_session_id="correct-calendar",
            venue_id="venue",
            trade_date=date(2026, 7, 31),
            knowledge_from=early,
            effective_from=early,
        )
        raw_path = stage / "original-tail.json"
        envelope = {
            "fields": [
                "date",
                "code",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "adjustflag",
                "turn",
                "pctChg",
                "tradestatus",
                "isST",
            ],
            "bs_code": "sz.000919",
            "n_rows": 1,
            "meta": {"error_code": "0"},
            "rows": [["2026-07-31", "sz.000919"] + ["1.0000000001"] * 6 + ["3", "", "", "1", "0"]],
        }
        raw_path.write_bytes(canonical(envelope))
        raw_sha = file_sha(raw_path)
        raw = dict(
            stock_code="000919",
            trade_date=date(2026, 7, 31),
            tradestatus="1",
            isST="0",
            **{
                f: Decimal("1.0000000001")
                for f in ("open", "high", "low", "close", "volume", "amount")
            },
        )
        new, _ = tail_observation(
            raw,
            listing=listing,
            calendar=calendar,
            observed_at=knowledge,
            source_sha256=raw_sha,
            revision="real-new-observation",
        )
        native = [save("old.parquet", [old])]
        added = save("added.parquet", [new])
        proof = freeze(native, added, [new])
        # Archived source names deliberately point to unavailable directories.
        proof["native"][0]["path"] = "/unavailable-old-base/no-file.parquet"
        proof["additions"]["path"] = "/unavailable-history/no-file.parquet"
        if fault == "purpose":
            proof["operation"] = "REPLACE_CONFLICTS"
        if fault == "raw-value":
            new["turnover"] = Decimal("2.0000000001")
            proof["allowed_additions"] = [u.addition_identity(new, rule)]
        proof["identity"] = digest({k: v for k, v in proof.items() if k != "identity"})
        proof_path = stage / "union-proof.json"
        proof_path.write_bytes(canonical(proof))
        if fault == "calendar":
            calendar["trade_date"] = date(2026, 8, 3)
        rows_by_table = {
            u.TABLE: [old, new],
            "dwd_listing": [dict(listing, listing_id=old["listing_id"]), listing],
            "dwd_trading_calendar": [calendar],
        }
        outputs = []
        for table, rows in rows_by_table.items():
            p = stage / "payload" / table / "part.parquet"
            p.parent.mkdir(parents=True)
            pq.write_table(pa.Table.from_pylist(rows, schema=ss[table]), p)
            item = {
                "table": table,
                "path": str(p.relative_to(root)),
                "sha256": file_sha(p),
                "size_bytes": p.stat().st_size,
                "rows": len(rows),
                "semantic_scan": semantic_scan([p], ss[table]),
            }
            if table == u.TABLE:
                item.update(
                    proof_path=str(proof_path.relative_to(root)), proof_identity=proof["identity"]
                )
            outputs.append(item)
        governance_path = stage / "governance.json"
        governance_path.write_bytes(
            canonical({"regular_observations": 1, "quarantined_rows": 0, "three_conflicts": []})
        )
        proof["repair_evidence_sha256"] = file_sha(governance_path)
        proof["identity"] = digest({k: v for k, v in proof.items() if k != "identity"})
        proof_path.write_bytes(canonical(proof))
        outputs[0]["proof_identity"] = proof["identity"]
        registry = {
            t: sorted(
                {
                    (r["record_source"], r["source_revision"], r["source_artifact_sha256"])
                    for r in rows
                }
            )
            for t, rows in rows_by_table.items()
        }
        if fault == "source":
            registry[u.TABLE] = []
        body = {
            "kind": "sealed_actual_domain_observations_not_complete_base22",
            "label": "synthetic-tail",
            "consumer_admission": "NOT_GRANTED",
            "projection_contract_id": u.CONTRACT_ID,
            "outputs": outputs,
            "output_rows": {t: len(rs) for t, rs in rows_by_table.items()},
            "source_registries": registry,
            "mandatory_472_repair_acceptance": False,
            "full33_candidate_acceptance": False,
            "exact_union_proofs": [
                {
                    "path": str(proof_path.relative_to(root)),
                    "sha256": file_sha(proof_path),
                    "proof_identity": proof["identity"],
                }
            ],
            "history_descriptor_sha256": proof["history_descriptor_sha256"],
            "governance_evidence_path": str(governance_path.relative_to(root)),
            "governance_evidence_sha256": file_sha(governance_path),
            "new_knowledge_at": knowledge.isoformat(),
            "new_missing_observations": 1,
            "held_conflicts_preserved": [],
            "archived_true_source_observations": [
                {
                    "role": "original_retained_tail_raw",
                    "path": str(raw_path.relative_to(root)),
                    "sha256": raw_sha,
                    "stock_code": "000919",
                }
            ],
        }
        body["identity"] = digest(body)
        if fault == "raw-byte":
            raw_path.write_bytes(b"corrupted")
        shutil.rmtree(tmp_path / "inputs")  # This is only disposable synthetic input.
        if fault:
            reason = {
                "purpose": "OFFLINE_PROOF_INVALID",
                "source": "TABLE_SOURCE_CLOSURE_EMPTY",
                "raw-byte": "ARCHIVED_SOURCE_BYTES_MISMATCH",
                "raw-value": "ORIGINAL_RAW_REPLAY_DRIFT",
                "calendar": "CALENDAR_LISTING_KNOWLEDGE_CLOSURE_FAILED",
            }[fault]
            with pytest.raises(BoundedError, match=reason):
                a.audit_tail_union_scope(root, body, budget)
        else:
            result = a.audit_tail_union_scope(root, body, budget)
            assert result["raw_replay"] == {"rows": 1, "regular": 1, "quarantined": 0}
            assert result["global_keys_versions_calendar_venue_lineage"] == "RECOMPUTED_PASS"
            assert result["full33_candidate_acceptance"] is False
