import json
import tempfile
import unittest
from pathlib import Path

from quanthus.policy import ConfigurationError
from quanthus.universe import load_universe_policy


class UniversePolicyTests(unittest.TestCase):
    def test_default_policy_excludes_the_cdr_and_bse(self) -> None:
        policy = load_universe_policy()

        self.assertTrue(policy.includes("600000", "SSE"))
        self.assertFalse(policy.includes("689009", "SSE"))
        self.assertFalse(policy.includes("430001", "BSE"))
        self.assertTrue(policy.is_out_of_scope_code("201872"))
        self.assertFalse(policy.is_out_of_scope_code("600000"))

    def test_policy_rejects_an_unfrozen_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "policy_version": "test",
                        "status": "draft",
                        "allowed_exchanges": ["SSE", "SZSE"],
                        "excluded_security_ids": {},
                        "out_of_scope_code_prefixes": {},
                        "invalid_bar_handling": "retain_with_data_valid_false",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigurationError, "status must be frozen"):
                load_universe_policy(path)


if __name__ == "__main__":
    unittest.main()
