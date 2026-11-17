import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nasdaq_morning_brief.render import write_png


class RenderTests(unittest.TestCase):
    def test_linux_uses_playwright_renderer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html_path = root / "brief.html"
            png_path = root / "brief.png"
            html_path.write_text('<main class="card">test</main>', encoding="utf-8")
            config = SimpleNamespace(
                render=SimpleNamespace(output_dir=root, viewport_width=1100, viewport_height=1600)
            )

            def create_png(command, check):
                self.assertEqual(command[0], "node")
                self.assertTrue(command[1].endswith("screenshot.js"))
                png_path.write_bytes(b"png")

            with patch("nasdaq_morning_brief.render.sys.platform", "linux"), patch(
                "nasdaq_morning_brief.render.subprocess.run", side_effect=create_png
            ):
                self.assertEqual(write_png(html_path, config), png_path)


if __name__ == "__main__":
    unittest.main()
