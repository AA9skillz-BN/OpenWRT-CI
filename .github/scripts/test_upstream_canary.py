"""Unit tests for upstream canary reporting and markdown formatting."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("upstream_canary", Path(__file__).with_name("upstream-canary.py"))
canary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(canary)


class UpstreamCanaryFormatTests(unittest.TestCase):
    def test_markdown_summary_distinguishes_clean_and_drifted_items(self):
        clean = {
            "schema": 1,
            "all_ok": True,
            "results": [
                {"category": "plugin", "target": "fm350 (AP3000M)", "locked": "3a101af3620e",
                 "latest": "a78e780cc6b9", "ok": True, "detail": "分支 main 适配校验通过"}
            ],
        }
        md_ok = canary.format_markdown(clean)
        self.assertIn("全部上游源码与插件当前均兼容", md_ok)
        self.assertIn("✅ 通过", md_ok)

        drifted = {
            "schema": 1,
            "all_ok": False,
            "results": [
                {"category": "plugin", "target": "fancontrol (AP3000M)", "locked": "f931c4681f7c",
                 "latest": "HEAD", "ok": False, "detail": "auto 模式将自动回退锁版本；原因: anchor|changed"}
            ],
        }
        md_bad = canary.format_markdown(drifted)
        self.assertIn("PLUGIN_MODE=auto", md_bad)
        self.assertIn("⚠️ 漂移", md_bad)
        self.assertIn("anchor\\|changed", md_bad)


if __name__ == "__main__":
    unittest.main()
