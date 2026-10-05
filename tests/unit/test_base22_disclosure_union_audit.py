import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_disclosure_union as u
from quanthus.pipelines.securities_warehouse.base22 import (
    canonical,
    digest,
    file_sha,
    load_contract,
    partition,
    schemas,
)
from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    Limits,
    Member,
    semantic_scan,
)
from quanthus.pipelines.securities_warehouse.base22_disclosure_union_audit import (
    audit_disclosure_union,
)
from quanthus.pipelines.securities_warehouse.base22_tail_union import member_identity
from quanthus.pipelines.securities_warehouse.disclosure_event import materialize_announcement_rows
from tests.unit.test_base22_independent_image import _row


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "scope-purpose",
        "proof-purpose",
        "proof-census",
        "source-label",
        "selected-change",
        "dated-company",
        "parent",
        "unknown-lineage",
        "extra-physical-file",
    ],
)
def test_offline_binds_selection_native_typed_streams_and_dated_parents(tmp_path, fault):
    contract, model, extension = load_contract(Path("config/pipelines"))
    ss = schemas(model, extension)
    early = datetime(2011, 1, 1, tzinfo=timezone.utc)
    clock = datetime(2026, 8, 4, tzinfo=timezone.utc)
    with Budget(
        tmp_path / "job",
        [],
        Limits(free_floor_bytes=1, allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2),
    ) as budget:
        root = budget.root / "image"
        stage = root / "observations/synthetic"
        cfg = stage / "contract"
        cfg.mkdir(parents=True)
        for name in [
            "base22-independent-image.v1.json",
            "dwd-domain-model.v1.json",
            "dwd-domain-model.v1.1-extension.json",
        ]:
            shutil.copyfile(Path("config/pipelines") / name, cfg / name)

        def observe(identifier):
            return materialize_announcement_rows(
                {"ann_id": identifier, "publish_time": "2026-07-25 09:00:00", "title": "synthetic"},
                company_id="company",
                security_id="security",
                source_sha256="sha256:" + "a" * 64,
                source_revision="synthetic",
                parent_knowledge_from=clock,
            )

        old, new = observe("old"), observe("new")
        outputs = []
        archives = []
        proofs = []
        donor_outputs = []
        registries = {}

        def archive(path, rows, table, role):
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(rows, schema=ss[table]), path)
            member = Member(path, file_sha(path), path.stat().st_size, len(rows))
            archives.append(
                {
                    "path": str(path.relative_to(root)),
                    "sha256": member.sha256,
                    "size_bytes": member.size_bytes,
                    "rows": member.rows,
                    "role": role,
                }
            )
            return member

        for t in u.ENTITIES:
            p = partition(new[t][0], contract["tables"][t])
            incoming = archive(
                stage / "archives" / t / "incoming.parquet",
                new[t],
                t,
                "original_qualified_partition",
            )
            selected = new[t]
            if fault == "selected-change" and t == "dwd_disclosure":
                selected = [dict(new[t][0], title="changed-after-qualified-input")]
            added = archive(
                stage / "archives" / t / "added.parquet", selected, t, "exact_selected_additions"
            )
            native = archive(
                stage / "archives" / t / "native.parquet", old[t], t, "native_selected_partition"
            )
            proof = {
                "contract_id": u.CONTRACT_ID,
                "table": t,
                "partition": p,
                "native": [member_identity(native)],
                "additions": member_identity(added),
                "added_row_census_sha256": digest(sorted(digest(r) for r in selected)),
                "whole_native_absence_proof_sha256": "sha256:" + "b" * 64,
                "source_qualification_sha256": "pending",
                "configuration_sha256": digest(
                    {"schema": str(ss[t]), "rule": contract["tables"][t]}
                ),
                "evaluator_sha256": file_sha(Path(u.__file__)),
                "consumer_admission": "NOT_GRANTED",
            }
            if fault == "proof-purpose":
                proof["consumer_admission"] = "ADMITTED"
            if fault == "proof-census":
                proof["added_row_census_sha256"] = "sha256:" + "c" * 64
            out = stage / "payload" / t / "part.parquet"
            out.parent.mkdir(parents=True)
            rows = old[t] + selected
            if fault == "parent" and t == "dwd_disclosure_subject":
                rows = [dict(r, disclosure_id="unavailable-parent") for r in rows]
            pq.write_table(pa.Table.from_pylist(rows, schema=ss[t]), out)
            outputs.append(
                {
                    "table": t,
                    "path": str(out.relative_to(root)),
                    "rows": len(rows),
                    "size_bytes": out.stat().st_size,
                    "sha256": file_sha(out),
                    "semantic_scan": semantic_scan([out], ss[t]),
                }
            )
            registries[t] = sorted(
                {
                    (r["record_source"], r["source_revision"], r["source_artifact_sha256"])
                    for r in rows
                }
            )
            donor_outputs.append(
                {
                    "table": t,
                    "partition": p,
                    "sha256": incoming.sha256,
                    "size_bytes": incoming.size_bytes,
                    "rows": incoming.rows,
                }
            )
            proofs.append(
                {
                    "path": str((stage / "proofs" / t).relative_to(root)),
                    "table": t,
                    "partition": p,
                    "native_local_members": [str(native.path.relative_to(root))],
                    "additions_local_member": str(added.path.relative_to(root)),
                    "incoming_local_member": str(incoming.path.relative_to(root)),
                    "held_existing_entity_rows": 0,
                    "held_row_census_sha256": digest([]),
                    "output_local_member": str(out.relative_to(root)),
                    "_body": proof,
                }
            )
        donor = {
            "kind": "sealed_actual_domain_observations_not_complete_base22",
            "label": "announcements-publication-revision-v1",
            "consumer_admission": "NOT_GRANTED",
            "outputs": donor_outputs,
        }
        if fault == "source-label":
            donor["label"] = "unqualified-source"
        donor["identity"] = digest(donor)
        dp = stage / "archives/donor.json"
        dp.write_bytes(canonical(donor))
        archives.append(
            {
                "path": str(dp.relative_to(root)),
                "sha256": file_sha(dp),
                "size_bytes": dp.stat().st_size,
                "rows": 0,
                "role": "source_scoped_descriptor",
            }
        )
        for entry in proofs:
            proof = entry.pop("_body")
            proof["source_qualification_sha256"] = file_sha(dp)
            # Strings retained for provenance cannot be dereferenced at cold time.
            for item in [*proof["native"], proof["additions"]]:
                item["path"] = "/unreachable-old-root/never-open.parquet"
            proof["identity"] = digest(proof)
            pp = root / entry["path"]
            pp.parent.mkdir(exist_ok=True)
            pp.write_bytes(canonical(proof))
            entry.update(sha256=file_sha(pp), proof_identity=proof["identity"])
        for t, key, value in [
            ("dwd_listed_company", "company_id", "company"),
            ("dwd_security", "security_id", "security"),
        ]:
            row = _row(ss[t])
            row.update({key: value, "knowledge_from": early, "effective_from": early})
            if fault == "dated-company" and t == "dwd_listed_company":
                row["effective_from"] = clock + timedelta(days=1)
            p = stage / "payload" / t / "parent.parquet"
            p.parent.mkdir()
            pq.write_table(pa.Table.from_pylist([row], schema=ss[t]), p)
            outputs.append(
                {
                    "table": t,
                    "path": str(p.relative_to(root)),
                    "rows": 1,
                    "size_bytes": p.stat().st_size,
                    "sha256": file_sha(p),
                    "semantic_scan": semantic_scan([p], ss[t]),
                }
            )
            registries[t] = [
                (row["record_source"], row["source_revision"], row["source_artifact_sha256"])
            ]
        desc = {
            "kind": "sealed_actual_domain_observations_not_complete_base22",
            "label": "synthetic",
            "consumer_admission": "NOT_GRANTED",
            "projection_contract_id": u.CONTRACT_ID,
            "complete_Base22": False,
            "full33_candidate_acceptance": False,
            "outputs": outputs,
            "output_rows": {
                t: sum(o["rows"] for o in outputs if o["table"] == t) for t in registries
            },
            "archived_true_source_observations": archives,
            "source_registries": registries,
            "exact_union_proofs": proofs,
            "source_scoped_descriptor_sha256": file_sha(dp),
            "source_scoped_identity": donor["identity"],
        }
        if fault == "scope-purpose":
            desc["complete_Base22"] = True
        if fault == "unknown-lineage":
            desc["source_registries"]["dwd_disclosure"] = [
                ("unknown", "unknown", "sha256:" + "d" * 64)
            ]
        if fault == "extra-physical-file":
            shutil.copyfile(root / outputs[0]["path"], stage / "payload/extra.parquet")
        desc["identity"] = digest(desc)
        if fault:
            with pytest.raises(BoundedError):
                audit_disclosure_union(root, desc, budget)
        else:
            receipt = audit_disclosure_union(root, desc, budget)
            assert receipt["added_versions"] == {"dwd_disclosure": 1, "dwd_disclosure_subject": 1}
            assert receipt["native_typed_columns_preserved"]
            assert receipt["original_source_root_dereferences"] == 0
            assert not receipt["all33_foreign_keys_or_image_acceptance"]
