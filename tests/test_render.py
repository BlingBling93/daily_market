import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nasdaq_morning_brief.render import build_etf_sector_flow_html, write_png


class RenderTests(unittest.TestCase):
    def test_three_day_outflow_streak_uses_green_badge(self):
        html = build_etf_sector_flow_html(
            None,
            "2026-11-18",
            histories=[
                {
                    "theme": "红利",
                    "name": "红利 ETF",
                    "ticker": "510880",
                    "days": [{"date": "2026-11-14", "amount": -100_000_000}],
                    "streak": "连续3日净流出",
                },
                {
                    "theme": "科技",
                    "name": "科技 ETF",
                    "ticker": "159915",
                    "days": [{"date": "2026-11-14", "amount": 100_000_000}],
                    "streak": "连续3日净流入",
                },
            ],
        )

        self.assertIn('class="badge outflow">连续3日净流出', html)
        self.assertIn('.badge.outflow{background:#e8f7ef;color:#168761}', html)
        self.assertIn('class="badge inflow">连续3日净流入', html)

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
