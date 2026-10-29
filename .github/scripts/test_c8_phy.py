"""Compile real DTBs to guard the C8 factory-confirmed MDIO wiring."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from device_image import check_dtb

REPO = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("dtc") and shutil.which("fdtget"), "Linux DTB tools required")
class C8PhyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.data = json.loads((REPO / "Profiles/C8-688.json").read_text())
        # Addresses come from the factory kernel DTB, independently of the
        # injected DTS: 0x15 is decimal 21, not decimal 15.
        self.source = '''/dts-v1/;
/ {
 compatible = "nradio,c8-688", "mediatek,mt7981";
 chosen { bootargs = "root=PARTLABEL=rootfs_2nd rootwait"; };
 soc {
  gpio: pinctrl { gpio-controller; #gpio-cells = <2>; };
  ethernet@15100000 {
   mac@1 { phy-mode = "2500base-x"; phy-handle = <&modem_phy>; };
   mdio-bus {
    lan_phy: ethernet-phy@5 {
     compatible = "ethernet-phy-ieee802.3-c45";
     reg = <5>; reset-gpios = <&gpio 7 1>;
    };
    modem_phy: ethernet-phy@15 {
     compatible = "ethernet-phy-ieee802.3-c45";
     reg = <0x15>; reset-gpios = <&gpio 8 1>;
    };
    switch@1f { ports { port@5 {
     phy-mode = "2500base-x"; phy-handle = <&lan_phy>;
    }; }; };
   };
  };
 };
};
'''

    def compile(self, text):
        source, dtb = self.path / "device.dts", self.path / "device.dtb"
        source.write_text(text)
        subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-o", str(dtb), str(source)],
                       check=True, capture_output=True)
        return dtb

    def test_factory_wiring_is_accepted(self):
        self.assertIn("nradio,c8-688", check_dtb(self.compile(self.source), self.data))

    def test_decimal_15_and_wrong_phy_handle_are_rejected(self):
        for old, new, error in (
            ("reg = <0x15>", "reg = <15>", "wrong PHY address"),
            ("phy-handle = <&modem_phy>", "phy-handle = <&lan_phy>", "wrong PHY connection"),
            ("<&gpio 8 1>", "<&gpio 7 1>", "wrong PHY reset"),
            ("<&gpio 8 1>", "<&gpio 8 0>", "wrong PHY reset"),
            ("phy-mode = \"2500base-x\"", "phy-mode = \"sgmii\"", "wrong PHY mode"),
        ):
            with self.subTest(change=new), self.assertRaisesRegex(ValueError, error):
                check_dtb(self.compile(self.source.replace(old, new, 1)), self.data)


if __name__ == "__main__":
    unittest.main()
