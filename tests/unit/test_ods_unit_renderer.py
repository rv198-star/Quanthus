from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / "ops" / "bin" / "render-ods-node-units"


class OdsUnitRendererTests(unittest.TestCase):
    def _render(self, node_id: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output = Path(directory.name)
        subprocess.run(
            [str(RENDERER), "--node-id", node_id, "--output", str(output)],
            check=True,
            text=True,
            capture_output=True,
        )
        return output

    def test_renderer_creates_deterministic_cn01_units(self) -> None:
        output = self._render("cn01")

        units = sorted(output.iterdir())
        self.assertEqual(len(units), 11)
        node_units = [path for path in units if path.name != "quanthus-pipeline-failure@.service"]
        self.assertTrue(all(path.name.startswith("cn01-ods-") for path in node_units))
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o644 for path in units))

        market_timer = (output / "cn01-ods-market-daily-core.timer").read_text()
        market_service = (output / "cn01-ods-market-daily-core.service").read_text()
        self.assertIn("OnCalendar=Mon..Fri *-*-* 19:00:00 Asia/Shanghai", market_timer)
        self.assertIn("Unit=cn01-ods-market-daily-core.service", market_timer)
        self.assertIn("EnvironmentFile=-/etc/quanthus/cn01-ods-lane.env", market_service)
        self.assertIn("Environment=HERMES_ODS_NODE_ID=cn01", market_service)
        self.assertIn("TimeoutStartSec=50min", market_service)
        self.assertIn("ops/bin/run-hermes-ods-lane", market_service)
        self.assertNotIn("ops/bin/run-cn01-ods-lane", market_service)
        self.assertIn("date +%%F", market_service)
        self.assertNotIn("hermes-ods", market_timer)

        failure_handler = output / "quanthus-pipeline-failure@.service"
        self.assertEqual(
            failure_handler.read_bytes(),
            (ROOT / "ops/systemd/quanthus-pipeline-failure@.service").read_bytes(),
        )

    def test_hermes_render_is_byte_identical_to_repository_unit(self) -> None:
        output = self._render("hermes")

        rendered = (output / "hermes-ods-market-daily-core.timer").read_bytes()
        source = (ROOT / "ops/systemd/hermes-ods-market-daily-core.timer").read_bytes()
        self.assertEqual(rendered, source)


if __name__ == "__main__":
    unittest.main()
