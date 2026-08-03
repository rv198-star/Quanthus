from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from quanthus.pipelines.securities_warehouse.model_extension import (
    ModelExtensionError,
    verify_additive_model_extension,
)

_ROOT = Path(__file__).resolve().parents[2]


class ModelExtensionTests(unittest.TestCase):
    def test_repository_extension_is_strictly_additive(self) -> None:
        extension, digest = verify_additive_model_extension(
            _ROOT / "config/pipelines/dwd-domain-model.v1.json",
            _ROOT / "config/pipelines/dwd-domain-model.v1.1-extension.json",
        )

        self.assertEqual(extension["allowed_issue_owners"], [63, 64])
        self.assertTrue(digest.startswith("sha256:"))

    def test_base_column_replacement_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            source = _ROOT / "config/pipelines/dwd-domain-model.v1.1-extension.json"
            value = json.loads(source.read_text(encoding="utf-8"))
            value["table_extensions"]["dwd_quality_exception"]["columns"][
                "rule_id"
            ] = "string"
            path = Path(temporary) / "extension.json"
            path.write_text(json.dumps(value), encoding="utf-8")

            with self.assertRaisesRegex(ModelExtensionError, "replaces base columns"):
                verify_additive_model_extension(
                    _ROOT / "config/pipelines/dwd-domain-model.v1.json", path
                )


if __name__ == "__main__":
    unittest.main()
