"""Port the reviewed H5000M plugin to C8 PWM without a second controller.

Keep upstream license, ABI and UI. Hash guards reject an unreviewed lock update.
No H5000M kernel patch is applied to C8, nor is CPU thermal policy changed.
"""
import hashlib
from pathlib import Path
import sys

HASHES = {
    "src/src/fan.rs": "5ac3fd9689a8fc9249bc376e1eab4ff293e505bd4218262c1ef931c83d115c2a",
    "src/src/safety.rs": "44f6391896bc0688f195c42d1058ed0d00a0aa297395d444ac95fbf373494091",
    "src/src/paths.rs": "45d89df8ef13e9359dc3a6dbde5081a992a3039b89acd4b0d9336b9c8e714358",
    "src/Makefile": "5011d8b33416349058da686bd585edd2c9122a5915c8789bfd54a9533680d739",
    "root/etc/init.d/h5000m-fancontrol": "01abdab45d024b5029683ba25dae469872c147c94142e3627bba009faf78da0e",
    "root/etc/config/h5000m_fancontrol": "67e84ea1d43b3170771a09e1bfccbadd8eebef2af6617a5d743a8cac78b62566",
    "htdocs/luci-static/resources/view/h5000m/fancontrol.js": "a99003ddc214f85f11ee0155dc18ba77940e4711b3516e34079c21d77d66c790",
    "po/zh_Hans/h5000m-fancontrol.po": "03497f28fa8fef1de6abc1cc812552db7305b91a512a8bced11ce922e19fedd4",
    "root/usr/share/luci/menu.d/luci-app-h5000m-fancontrol.json": "b26e287d7472b32bfd6267c4e357b0427cc5188c07b4e3c0ff7ff85df0e78399",
}

FLOOR = '''    // C8: retain every kernel map, with the previous controller's full-speed
    // threshold as an additional floor, including in manual mode. Unknown CPU
    // readings cannot be masked by a valid PHY/radio sensor.
    if cpu_temp.map_or(true, |t| t >= 75) {
        assessment.floor_pwm = 255;
        assessment.thermal_state = 3;
        assessment.basis = "C8 CPU unknown or >=75 C: full output".to_string();
        return assessment;
    }

'''


def transform(name, text):
    if name == "src/src/fan.rs":
        # Never select an unrelated chip's PWM, even when pwmfan is missing.
        text = text.replace("let pwm_path = chosen.or(fallback)?;", "let _ = fallback;\n    let pwm_path = chosen?;")
        text = text.replace("Ok(self.read_pwm().unwrap_or(value))", '''self.read_pwm().ok_or_else(|| Error::PwmWrite {
            path: self.pwm_path.display().to_string(),
            message: "PWM readback unavailable".to_string(),
        })''')
    elif name == "src/src/safety.rs":
        text = text.replace("    let of_dir = fan.of_node();", FLOOR + "    let of_dir = fan.of_node();", 1)
        # C8's actual upstream trips are named active-low/med/high, hot, crit.
        # H5000M's trip-point* filter would silently miss all of these.
        text = text.replace('list_dirs(&zone.join("trips"), "trip-point")',
                            'list_dirs(&zone.join("trips"), "")')
        text = text.replace("    assessment.map_states = matched_maps;", '''    if matched_maps.is_empty() {
        assessment.floor_pwm = 255;
        assessment.basis = "C8 cooling map unavailable: full output".to_string();
        return assessment;
    }
    assessment.map_states = matched_maps;''')
        text = text.replace("    let mut state = best_state;\n    if state == 0 {",
                            "    let mut state = best_state;\n    if state == 0 && !map_found {")
    elif name == "src/src/paths.rs":
        # FAN789's C8 plugin has no H5000M at-webserver RPC on 8765. Do not
        # contend for its AT transport. Unavailable sensors stay unknown.
        text = text.replace("mt5700_rpc_port: 8765", "mt5700_rpc_port: 0")
        text = text.replace('port_from_env("H5000M_FAN_MT5700M_RPC_PORT", 8765)',
                            'port_from_env("H5000M_FAN_MT5700M_RPC_PORT", 0)')
    elif name == "src/Makefile":
        text = text.replace("--release --offline --target", "--release --locked --offline --target")
    elif name == "root/etc/init.d/h5000m-fancontrol":
        text = text.replace("start_service() {", '''start_service() {
	[ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = 'nradio,c8-688' ] || return 1''')
    elif name == "root/etc/config/h5000m_fancontrol":
        text = text.replace("option temp_source 'max'", "option temp_source 'cpu'")
    elif name.endswith("fancontrol.js"):
        text = text.replace("H5000M cooling management", "C8-688 cooling management")
    elif name.endswith(".po"):
        text = text.replace("H5000M cooling management", "C8-688 cooling management")
        text = text.replace("H5000M 散热管理", "C8-688 散热管理")
    elif name.endswith(".json"):
        text = text.replace('"title": "Fan Control"', '"title": "C8 风扇管理"')
    return text


def adapt(tree):
    originals = tree / ".c8-reviewed-originals"
    changes = []
    for name, digest in HASHES.items():
        file = tree / name
        raw = file.read_bytes()
        backup = originals / name
        original = backup.read_bytes() if backup.exists() else raw
        if hashlib.sha256(original).hexdigest() != digest:
            raise ValueError(f"{name}: not the reviewed locked source")
        patched = transform(name, original.decode()).encode()
        if raw not in (original, patched) or patched == original:
            raise ValueError(f"{name}: source/patch drift; review the adaptation")
        changes.append((file, backup, original, patched))
    for file, backup, original, patched in changes:
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(original)
        file.write_bytes(patched)


if __name__ == "__main__":
    adapt(Path(sys.argv[1]))
