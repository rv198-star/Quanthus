"""The public example must preserve model identity without granting influence."""

import json
import re
import subprocess
import sys
from pathlib import Path


def test_documented_pending_model_identity_is_auditable():
    root = Path(__file__).resolve().parents[2]
    document = (root / "docs/operations/learning-assets-consumer.md").read_text()
    block = re.findall(r"```python\n(.*?)\n```", document, re.S)[0]

    def run(code):
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                f"import sys;sys.path.insert(0, {str(root / 'src')!r});\n" + code,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return json.loads(result.stdout)

    original = run(block)
    changed = run(
        block.replace("synthetic-model-not-admitted", "different-model").replace(
            "synthetic-purpose", "different-purpose"
        )
    )
    for field in ("model_digest", "purpose_digest"):
        assert original["pending_head2"][field] != changed["pending_head2"][field]
    assert original["decisions"] == changed["decisions"]
    for output in (original, changed):
        assert output["pending_head2"]["admission"] == "NOT_GRANTED"
        assert output["pending_head2"]["applicability"] == "abstained"
        assert output["pending_head2"]["combined_action"] == "trial_entry"
        assert output["pending_head2"]["blocked_action"] == "no_action"
