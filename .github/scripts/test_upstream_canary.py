"""Unit tests for upstream canary reporting and markdown formatting."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("upstream_canary", Path(__file__).with_name("upstream-canary.py"))
canary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(canary)


class UpstreamCanaryFormatTests(unittest.TestCase):
    def test_tr3000_canary_rejects_wrong_ubi_length_and_image_size(self):
        layout = b"partition@5c0000 { reg = <0x5c0000 0x4000000>; };"
        recipe = b"define Device/cudy_tr3000-v1\n  IMAGE_SIZE := 65536k\n  BLOCKSIZE := 128k\n  PAGESIZE := 2048\nendef\n"
        for dts, image, error in (
            (layout.replace(b"4000000", b"8000000"), recipe, "Unexpected upstream UBI layout"),
            (layout, recipe.replace(b"65536k", b"131072k"), "Unexpected TR3000-v1 layout setting"),
        ):
            files = {"mac80211.uc": b"", "dhcp.conf": b"", "mt7981b-cudy-tr3000-v1.dts": dts,
                     "filogic.mk": image}
            with patch.object(canary.wifi_mod, "patch_generator"), patch.object(
                    canary.tr_sys_mod, "patch_dhcp_conf"), patch.object(
                    canary, "fetch_bytes", side_effect=lambda url: files[url.rsplit("/", 1)[1]]):
                with self.assertRaisesRegex(ValueError, error):
                    canary.check_source_anchors("TR3000", "VIKINGYFY/immortalwrt", "owrt",
                                                "TR3000-512", {"TR3000-512": {"commit": "a"*40}},
                                                head_sha="b"*40)

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
        self.assertIn("已覆盖的上游适配预检全部通过", md_ok)
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
