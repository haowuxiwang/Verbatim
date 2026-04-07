from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import frozen_business_smoke


class _Result:
    def __init__(self) -> None:
        self.page_candidates = [object()]
        self.items_payload = [(0, 1, (0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 1.0, 1.0), 0.5, "ok")]


class TestFrozenBusinessSmoke(unittest.TestCase):
    def test_main_source_mode_success(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            left = root / "original.pdf"
            right = root / "digest.pdf"
            left.write_bytes(b"%PDF-left")
            right.write_bytes(b"%PDF-right")

            original_resolve = frozen_business_smoke.resolve_sample_path
            original_root = frozen_business_smoke.ROOT
            try:
                frozen_business_smoke.resolve_sample_path = lambda x: left if str(x) == "original.pdf" else right
                frozen_business_smoke.ROOT = root

                def _fake_run(cmd, **kwargs):
                    output_arg = cmd[cmd.index("--output") + 1]
                    Path(output_arg).write_bytes(__import__("pickle").dumps(_Result()))
                    return type("P", (), {"returncode": 0, "stdout": "ok"})()

                buf = io.StringIO()
                with patch("scripts.frozen_business_smoke.subprocess.run", side_effect=_fake_run):
                    with patch.object(sys, "argv", ["frozen_business_smoke.py"]):
                        with redirect_stdout(buf):
                            rc = frozen_business_smoke.main()
            finally:
                frozen_business_smoke.resolve_sample_path = original_resolve
                frozen_business_smoke.ROOT = original_root

        self.assertEqual(0, rc)
        line = buf.getvalue().strip().splitlines()[-1]
        self.assertTrue(line.startswith("BUSINESS_SMOKE "))
        payload = json.loads(line.split(" ", 1)[1])
        self.assertEqual("source", payload["mode"])
        self.assertEqual(1, payload["page_candidates"])
        self.assertEqual(1, payload["items_payload"])


if __name__ == "__main__":
    unittest.main()
