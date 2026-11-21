//! Run the real locked plugin's PDP/dial code through a Linux pseudo terminal.
use fm350d::at::AtHandle;
use fm350d::config::Config;
use fm350d::modem::pdp::{dial, pdp};
use std::ffi::CStr;
use std::fs::File;
use std::io::{Read, Write};
use std::os::unix::io::FromRawFd;
use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

#[link(name = "util")]
extern "C" {}

const DNS6: &str = "36.9.128.87.32.0.0.0.0.0.0.0.0.0.0.8";
const LOCAL6: &str = "32.1.13.184.0.0.0.0.0.0.0.0.0.0.0.2";

struct Modem {
    cfg: Config,
    stop: Arc<AtomicBool>,
    commands: Arc<Mutex<Vec<String>>>,
    worker: Option<JoinHandle<()>>,
    _slave: File,
}

impl Modem {
    fn new(cgact: &str, contrdp: &str, address: &str) -> Self {
        let (mut master, mut slave) = (-1, -1);
        let mut name = [0 as libc::c_char; 128];
        unsafe {
            assert_eq!(libc::openpty(&mut master, &mut slave, name.as_mut_ptr(),
                                    std::ptr::null(), std::ptr::null()), 0);
        }
        let mut cfg = Config::default();
        cfg.cid = 2;
        cfg.at_timeout = 2;
        cfg.at_port = unsafe { CStr::from_ptr(name.as_ptr()) }.to_str().unwrap().to_owned();
        let stop = Arc::new(AtomicBool::new(false));
        let commands = Arc::new(Mutex::new(Vec::new()));
        let done = stop.clone();
        let log = commands.clone();
        let initial_act = cgact.to_owned();
        let initial_contr = contrdp.to_owned();
        let initial_addr = address.to_owned();
        let worker = thread::spawn(move || {
            let mut port = unsafe { File::from_raw_fd(master) };
            let mut pending = Vec::new();
            let mut activated = false;
            let deadline = Instant::now() + Duration::from_secs(30);
            while !done.load(Ordering::Relaxed) && Instant::now() < deadline {
                let mut poll = libc::pollfd { fd: master, events: libc::POLLIN, revents: 0 };
                if unsafe { libc::poll(&mut poll, 1, 20) } <= 0 { continue; }
                let mut buffer = [0; 1024];
                let n = match port.read(&mut buffer) { Ok(n) if n > 0 => n, _ => continue };
                for &b in &buffer[..n] {
                    if b != b'\r' && b != b'\n' { pending.push(b); continue; }
                    if pending.is_empty() { continue; }
                    let command = String::from_utf8(std::mem::take(&mut pending)).unwrap();
                    log.lock().unwrap().push(command.clone());
                    let response = match command.as_str() {
                        "AT+CGDCONT?" => "+CGDCONT: 2,\"IPV4V6\",\"CMNET\"\r\nOK",
                        "AT+CGACT?" if activated => "+CGACT: 2,1\r\nOK",
                        "AT+CGACT?" => &initial_act,
                        "AT+CGCONTRDP=2" if activated =>
                            "+CGCONTRDP: 2,5,\"CMNET\",\"10.0.0.2\",\"10.0.0.1\",\"223.5.5.5\"\r\nOK",
                        "AT+CGCONTRDP=2" => &initial_contr,
                        "AT+CGPADDR=2" if activated => "+CGPADDR: 2,\"10.0.0.2\"\r\nOK",
                        "AT+CGPADDR=2" => &initial_addr,
                        "AT+GTDNS=2" => "ERROR",
                        "AT+CPIN?" => "+CPIN: READY\r\nOK",
                        "AT+CFUN?" => "+CFUN: 1\r\nOK",
                        "AT+CEREG?" => "+CEREG: 0,1\r\nOK",
                        "AT+CGACT=1,2" => { activated = true; "OK" },
                        _ => "OK",
                    };
                    port.write_all(format!("\r\n{}\r\n", response).as_bytes()).unwrap();
                }
            }
        });
        Self { cfg, stop, commands, worker: Some(worker),
               _slave: unsafe { File::from_raw_fd(slave) } }
    }
}

impl Drop for Modem {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        self.worker.take().unwrap().join().unwrap();
    }
}

fn dns_only() -> String {
    format!("+CGCONTRDP: 2,,\"CMNET\",\"\",\"\",\"{}\",\"223.5.5.5\"\r\nOK", DNS6)
}

#[test]
fn dns_only_with_stale_cgpaddr_is_inactive() {
    let modem = Modem::new("OK", &dns_only(), "+CGPADDR: 2,\"10.0.0.99\"\r\nOK");
    let state = pdp(&AtHandle::new(), &modem.cfg);
    assert!(!state.active, "DNS must not make an empty session active: {:?}", state);
    assert!(!state.has_address());
    assert!(state.dns.is_empty());
}

#[test]
fn dial_activates_dns_only_session_instead_of_reusing_stale_address() {
    let modem = Modem::new("OK", &dns_only(), "+CGPADDR: 2,\"10.0.0.99\"\r\nOK");
    let state = dial(&AtHandle::new(), &modem.cfg).unwrap();
    assert!(modem.commands.lock().unwrap().iter().any(|s| s == "AT+CGACT=1,2"));
    assert!(state.active);
    assert_eq!(state.ipv4, "10.0.0.2");
    assert!(state.ipv6.is_empty());
}

#[test]
fn ipv6_dns_is_preserved_without_becoming_an_ipv6_local_address() {
    let contrdp = format!("+CGCONTRDP: 2,5,\"CMNET\",\"10.0.0.2.255.255.255.0\",\"10.0.0.1\",\"{}\",\"223.5.5.5\"\r\nOK", DNS6);
    let modem = Modem::new("OK", &contrdp, "+CGPADDR: 2,\"10.0.0.2\"\r\nOK");
    let state = pdp(&AtHandle::new(), &modem.cfg);
    assert!(state.active);
    assert_eq!(state.ipv4, "10.0.0.2");
    assert_eq!(state.netmask4, "255.255.255.0");
    assert!(state.ipv6.is_empty(), "DNS is not the local IPv6: {:?}", state);
    assert_eq!(state.dns, vec!["2409:8057:2000::8", "223.5.5.5"]);
}

#[test]
fn ipv6_only_local_address_and_dns_remain_valid() {
    let contrdp = format!("+CGCONTRDP: 2,5,\"CMNET\",\"{}\",\"\",\"{}\"\r\nOK", LOCAL6, DNS6);
    let modem = Modem::new("OK", &contrdp, "+CGPADDR: 2\r\nOK");
    let state = pdp(&AtHandle::new(), &modem.cfg);
    assert!(state.active);
    assert!(state.ipv4.is_empty());
    assert_eq!(state.ipv6, "2001:db8::2");
    assert_eq!(state.dns, vec!["2409:8057:2000::8"]);
}

#[test]
fn other_cid_cannot_supply_address_gateway_or_dns() {
    let contrdp = "+CGCONTRDP: 1,5,\"ims\",\"192.0.2.9\",\"192.0.2.1\",\"192.0.2.53\"\r\n+CGCONTRDP: 2,5,\"CMNET\",\"10.0.0.2\",\"10.0.0.1\",\"223.5.5.5\"\r\nOK";
    let modem = Modem::new("OK", contrdp, "+CGPADDR: 2\r\nOK");
    let state = pdp(&AtHandle::new(), &modem.cfg);
    assert!(state.active);
    assert_eq!(state.ipv4, "10.0.0.2");
    assert_eq!(state.gw4, "10.0.0.1");
    assert_eq!(state.dns, vec!["223.5.5.5"]);
}

#[test]
fn explicit_inactive_state_overrides_residual_local_address() {
    let contrdp = "+CGCONTRDP: 2,5,\"CMNET\",\"10.0.0.2\",\"10.0.0.1\",\"223.5.5.5\"\r\nOK";
    let modem = Modem::new("+CGACT: 2,0\r\nOK", contrdp,
                           "+CGPADDR: 2,\"10.0.0.2\"\r\nOK");
    let state = pdp(&AtHandle::new(), &modem.cfg);
    assert!(!state.active);
    assert!(!state.has_address());
    assert!(state.dns.is_empty());
}

#[test]
fn explicit_active_state_uses_cgpaddr_and_filters_ipv6_placeholder() {
    let modem = Modem::new("+CGACT: 2,1\r\nOK", &dns_only(),
        "+CGPADDR: 2,\"10.0.0.2\",\"0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.1\"\r\nOK");
    let state = pdp(&AtHandle::new(), &modem.cfg);
    assert!(state.active);
    assert_eq!(state.ipv4, "10.0.0.2");
    assert!(state.ipv6.is_empty());
    assert_eq!(state.dns, vec!["2409:8057:2000::8", "223.5.5.5"]);
}
