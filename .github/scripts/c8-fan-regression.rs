//! Real ported controller with isolated sysfs, not a replacement fan algorithm.
mod common;
use common::{mc, Fixture, StubProbe};
use h5000m_fancontrol::{controller, fan, safety};
use h5000m_fancontrol::sensor::{self, SensorId};

#[test]
fn c8_real_sensor_names_are_classified() {
    let f = Fixture::new("c8-hwmon-names");
    f.thermal_zone("cpu-thermal", mc(55));
    f.hwmon("mdio_bus:05", Some(mc(48)));
    f.hwmon("mt7915_phy0", Some(mc(49)));
    f.hwmon("mt7915_phy1", Some(mc(50)));
    let sensors = sensor::sample_all(&f.paths, &StubProbe::new(), 1000);
    assert_eq!(sensors.value(SensorId::Cpu), Some(55));
    assert_eq!(sensors.value(SensorId::Phy), Some(48));
    assert_eq!(sensors.value(SensorId::Wifi1), Some(49));
    assert_eq!(sensors.value(SensorId::Wifi2), Some(50));
}

#[test]
fn c8_never_writes_an_unrelated_pwm_chip() {
    let f = Fixture::new("c8-strict-pwm");
    let other = f.hwmon("unrelated-pwm", None);
    std::fs::write(other.join("pwm1"), "100").unwrap();
    assert!(fan::discover(&f.paths).is_none());
    f.pwm_fan(0, None);
    assert!(fan::discover(&f.paths).is_some());
}

#[test]
fn c8_cpu_unknown_or_75c_forces_full_output_even_in_manual_mode() {
    for temp in [None, Some(75), Some(95)] {
        let f = Fixture::new("c8-emergency");
        if let Some(t) = temp { f.thermal_zone("cpu-thermal", mc(t)); }
        f.hwmon("Phy-0", Some(mc(35))); // A good PHY must not hide missing CPU.
        let pwm = f.pwm_fan(0, None);
        f.dt_pwm_fan(7, &[0, 64, 128, 255]);
        f.uci("config settings 'settings'\n option mode 'manual'\n option manual_pwm '0'\n option start_boost_ms '0'\n");
        let fan = fan::discover(&f.paths).unwrap();
        assert_eq!(safety::evaluate_floor(&f.paths, &fan, temp).floor_pwm, 255);
        controller::run_cycle(&f.paths, &StubProbe::new(), None, 1000, &mut |_| {});
        assert_eq!(f.read_pwm(&pwm), Some(255));
    }
}

#[test]
fn c8_manual_and_automatic_modes_write_pwm_without_changing_cpu_policy() {
    for (mode, pwm) in [("manual", Some(160)), ("auto", None)] {
        let f = Fixture::new("c8-control");
        f.thermal_zone("cpu-thermal", mc(52));
        let hw = f.pwm_fan(0, None);
        f.pwm_fan_cooling_device(3);
        f.dt_pwm_fan(7, &[0, 64, 128, 255]);
        f.dt_trip("cpu-thermal", "active-low", "active", 60_000, 2_000, 11);
        f.dt_cooling_map("cpu-thermal", "map0", 7, 1, 11);
        f.uci(&format!("config settings 'settings'\n option mode '{mode}'\n option manual_pwm '160'\n option start_boost_ms '0'\n"));
        let zone = f.thermal_zone_path(0);
        let before = f.read_policy(&zone);
        controller::run_cycle(&f.paths, &StubProbe::new(), None, 1000, &mut |_| {});
        assert_eq!(f.read_policy(&zone), before);
        if let Some(pwm) = pwm { assert_eq!(f.read_pwm(&hw), Some(pwm)); }
        else { assert!(f.read_pwm(&hw).unwrap() > 0); }
    }
}

#[test]
fn c8_kernel_cooling_floor_is_retained() {
    let f = Fixture::new("c8-kernel-floor");
    f.pwm_fan(0, None);
    f.pwm_fan_cooling_device(3);
    f.dt_pwm_fan(7, &[0, 64, 128, 255]);
    f.dt_trip("cpu-thermal", "active-low", "active", 60_000, 2_000, 11);
    f.dt_cooling_map("cpu-thermal", "map0", 7, 2, 11);
    let fan = fan::discover(&f.paths).unwrap();
    assert_eq!(safety::evaluate_floor(&f.paths, &fan, Some(52)).floor_pwm, 0);
    assert_eq!(safety::evaluate_floor(&f.paths, &fan, Some(60)).floor_pwm, 128);
}
