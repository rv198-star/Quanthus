import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from quanthus.industry_mapping import IndustryMappingError, load_static_industry_mapping


def digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class IndustryMappingTests(unittest.TestCase):
    def payload(self) -> dict:
        value = {
            "schema_version": "1.0.0",
            "mapping_version": "static-level1-concentration-research-v1",
            "usage": "static_non_pit_concentration_only",
            "source": {
                "provider": "synthetic",
                "endpoint": "fixture://industry",
                "retrieved_at": "2026-07-29T00:00:00+00:00",
                "source_revision": "synthetic-industry-v1",
            },
            "memberships": [
                {
                    "security_id": "opaque-security-001",
                    "stock_code": "600001",
                    "exchange": "SSE",
                    "level1_industry_id": "industry-a",
                },
                {
                    "security_id": "opaque-security-002",
                    "stock_code": "000001",
                    "exchange": "SZSE",
                    "level1_industry_id": "industry-b",
                },
            ],
        }
        return {**value, "artifact_digest": digest(value)}

    def load(self, value: dict, required: tuple[str, ...] = ()):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "industry.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return load_static_industry_mapping(
                path,
                expected_version="static-level1-concentration-research-v1",
                required_security_ids=required,
            )

    def test_loads_checksum_bound_opaque_identity_memberships(self) -> None:
        mapping = self.load(self.payload(), ("opaque-security-001",))

        self.assertEqual(len(mapping.memberships), 2)
        self.assertEqual(
            mapping.by_security_id["opaque-security-001"].level1_industry_id,
            "industry-a",
        )
        self.assertIn("600001:SSE", mapping.by_security_id["opaque-security-001"].lineage_ref)

    def test_tampering_and_missing_required_identity_fail_closed(self) -> None:
        tampered = self.payload()
        tampered["memberships"][0]["level1_industry_id"] = "changed"
        with self.assertRaisesRegex(IndustryMappingError, "digest mismatch"):
            self.load(tampered)

        with self.assertRaisesRegex(IndustryMappingError, "missing 1 required"):
            self.load(self.payload(), ("opaque-security-003",))

    def test_duplicate_security_identity_is_ambiguous(self) -> None:
        duplicated = self.payload()
        unsigned = {key: value for key, value in duplicated.items() if key != "artifact_digest"}
        unsigned["memberships"].append(dict(unsigned["memberships"][0]))
        duplicated = {**unsigned, "artifact_digest": digest(unsigned)}

        with self.assertRaisesRegex(IndustryMappingError, "ambiguous"):
            self.load(duplicated)


if __name__ == "__main__":
    unittest.main()
