"""Read-only consumer entry for qualified OCI daily/native-minute increments.

Historical event/replay profiles keep their frozen windows. A consumer explicitly
requests this source and its required intervals; execution remains unauthorized.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from quanthus.pipelines.p10_complete_daily_schedule import CONFIG, POLICY_ID
from quanthus.pipelines.p10_delivery_common import (
    BASE23_CONTENT,
    BASE23_MANIFEST,
    file_digest,
    object_at,
    safe_path,
)
from quanthus.pipelines.p10_delivery_common import COMPLETE_KIND as KIND
from quanthus.pipelines.p10_delivery_common import COMPLETE_MINUTE_FILE as MINUTE_FILE
from quanthus.pipelines.p10_delivery_common import COMPLETE_POINTER as POINTER


class CompleteDailySource:
    def __init__(self, root: Path, *, purpose: str = "gap_aware_non_execution_development"):
        if purpose != "gap_aware_non_execution_development":
            raise ValueError(
                "complete Daily does not grant execution or strict historical research"
            )
        self.root = root.resolve()
        self.purpose = purpose

    def admission(self, required_intervals: tuple[str, ...] = ("1d", "1m", "5m")) -> dict[str, Any]:
        pointer = object_at(self.root / POINTER)
        package = safe_path(self.root, pointer["manifest_path"]).parent
        manifest = object_at(package / "manifest.json")
        visibility = object_at(
            self.root
            / "visibility"
            / (f"{pointer['market_date']}-{pointer['manifest_sha256'][7:23]}.json")
        )
        if (
            manifest.get("kind") != KIND
            or manifest.get("policy_id") != POLICY_ID
            or manifest.get("policy_sha256") != file_digest(CONFIG)
            or manifest.get("base", {}).get("manifest_sha256") != BASE23_MANIFEST
            or manifest.get("base", {}).get("content_digest") != BASE23_CONTENT
            or manifest.get("consumer_purpose") != self.purpose
            or manifest.get("execution_authorized") is not False
            or file_digest(package / "manifest.json") != pointer["manifest_sha256"]
            or (package / "READY").read_text().strip() != pointer["manifest_sha256"]
            or pointer["content_digest"] != manifest["content_digest"]
            or pointer["market_date"] != manifest["market_date"]
            or visibility.get("manifest_sha256") != pointer["manifest_sha256"]
            or visibility.get("content_digest") != pointer["content_digest"]
            or visibility.get("exact_source_match") is not True
            or visibility.get("execution_authorized") is not False
            or manifest.get("consumable_intervals") != ["1d", "1m", "5m"]
            or not required_intervals
            or not set(required_intervals) <= {"1d", "1m", "5m"}
            or any(
                manifest["minute_quality_grades"][interval].get("R3", 0)
                for interval in ("1m", "5m")
            )
        ):
            raise ValueError(
                "complete Daily source missing exact OCI visibility or joint quality admission"
            )
        return {
            "status": "admitted",
            "market_date": pointer["market_date"],
            "manifest_sha256": pointer["manifest_sha256"],
            "content_digest": pointer["content_digest"],
            "package": str(package),
            "required_intervals": list(required_intervals),
            "minute_quality_grades": manifest["minute_quality_grades"],
            "consumer_purpose": self.purpose,
            "execution_authorized": False,
            "first_verified_visible_at": visibility["first_verified_visible_at"],
        }

    def iter_bars(
        self,
        *,
        required_intervals: tuple[str, ...],
        knowledge_cutoff: datetime,
        continuous_only: bool = True,
    ) -> Any:
        import pyarrow.parquet as pq

        if knowledge_cutoff.tzinfo is None:
            raise ValueError("consumer requires explicit timezone-aware knowledge cutoff")
        admission = self.admission(required_intervals)
        if knowledge_cutoff < datetime.fromisoformat(admission["first_verified_visible_at"]):
            raise ValueError("OCI product was not yet actually visible at requested cutoff")
        package = Path(admission["package"])
        manifest = object_at(package / "manifest.json")
        inventory = {item["path"]: item for item in manifest["files"]}
        paths = list(
            (package / "members/1d").glob("payload/*/dwd_security_bar/market_date=*/part-*.parquet")
        )
        paths.append(package / MINUTE_FILE)
        for path in paths:
            rel = path.relative_to(package).as_posix()
            item = inventory.get(rel)
            if item is None or file_digest(path) != item["sha256"]:
                raise ValueError("consumer bar file differs from accepted complete product")
            for batch in pq.ParquetFile(path).iter_batches(batch_size=20000):
                for row in batch.to_pylist():
                    if row["interval_size"] not in required_intervals:
                        continue
                    if row["knowledge_from"] > knowledge_cutoff:
                        continue
                    if continuous_only and row.get("exchange_session_phase") == "auction":
                        continue
                    if row.get("quality_grade", "R0") not in {"R0", "R1", "R2"}:
                        raise ValueError("rejected minute row reached consumer")
                    yield row

    def iter_domain_rows(self, *, table: str, knowledge_cutoff: datetime) -> Any:
        """Explicit source-native versions; no canonical/execution/PIT upgrade implied."""
        import pyarrow.parquet as pq

        from quanthus.pipelines.p10_domain_continuation import verify as verify_domain

        if table not in {"dwd_disclosure", "dwd_disclosure_subject", "dwd_financial_report",
            "dwd_financial_concept", "dwd_financial_fact"} or knowledge_cutoff.tzinfo is None:
            raise ValueError("unsupported domain table or naive cutoff")
        admission = self.admission()
        if knowledge_cutoff < datetime.fromisoformat(admission["first_verified_visible_at"]):
            raise ValueError("domain product was not visible at requested OCI cutoff")
        package = Path(admission["package"])
        manifest = object_at(package / "manifest.json")
        for key, sha in manifest.get("domain_manifest_sha256", {}).items():
            root = safe_path(package, "domains/" + key)
            domain = verify_domain(root)
            if domain["manifest_sha256"] != sha:
                raise ValueError("consumer domain identity differs")
            member = root / "payload" / table / "part-00000.parquet"
            if not member.exists():
                continue
            for batch in pq.ParquetFile(member).iter_batches(batch_size=20000):
                for row in batch.to_pylist():
                    if row["knowledge_from"] <= knowledge_cutoff:
                        yield row
