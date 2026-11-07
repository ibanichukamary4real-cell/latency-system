"""Automated tests for latency_monitor.py.

The tests never send real network traffic: ping output is simulated, so they
run anywhere (including on GitHub Actions) with no network access.
"""
import csv
import json
import os
import subprocess
import sys
import threading
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import latency_monitor as lm  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────
def fake_run(stdout, returncode=0, record=None):
    """Return a stand-in for subprocess.run that yields the given output."""
    def _run(cmd, capture_output, text, timeout):
        if record is not None:
            record.append(cmd)
        return types.SimpleNamespace(stdout=stdout, returncode=returncode)
    return _run


WIN_OK = ("Pinging 8.8.8.8 with 32 bytes of data:\n"
          "Reply from 8.8.8.8: bytes=32 time=58ms TTL=117\n")
WIN_SUB_MS = ("Pinging 192.168.0.1 with 32 bytes of data:\n"
              "Reply from 192.168.0.1: bytes=32 time<1ms TTL=64\n")
WIN_FAIL = ("Pinging 192.168.1.1 with 32 bytes of data:\n"
            "Request timed out.\n")
LINUX_OK = ("PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.\n"
            "64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=194.3 ms\n")
LINUX_FAIL = ("PING 10.255.255.1 (10.255.255.1) 56(84) bytes of data.\n\n"
              "--- 10.255.255.1 ping statistics ---\n"
              "1 packets transmitted, 0 received, 100% packet loss, time 0ms\n")


# ── ping parsing ──────────────────────────────────────────────────────────────
def test_ping_windows_reply(monkeypatch):
    calls = []
    monkeypatch.setattr(lm.platform, "system", lambda: "Windows")
    monkeypatch.setattr(lm.subprocess, "run", fake_run(WIN_OK, record=calls))
    assert lm.ping("8.8.8.8", 3) == 58.0
    assert calls[0] == ["ping", "-n", "1", "-w", "3000", "8.8.8.8"]


def test_ping_windows_sub_millisecond(monkeypatch):
    monkeypatch.setattr(lm.platform, "system", lambda: "Windows")
    monkeypatch.setattr(lm.subprocess, "run", fake_run(WIN_SUB_MS))
    assert lm.ping("192.168.0.1") == 1.0


def test_ping_windows_timeout(monkeypatch):
    monkeypatch.setattr(lm.platform, "system", lambda: "Windows")
    monkeypatch.setattr(lm.subprocess, "run", fake_run(WIN_FAIL, returncode=1))
    assert lm.ping("192.168.1.1") is None


def test_ping_linux_reply(monkeypatch):
    calls = []
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lm.subprocess, "run", fake_run(LINUX_OK, record=calls))
    assert lm.ping("1.1.1.1", 3) == pytest.approx(194.3)
    assert calls[0] == ["ping", "-c", "1", "-W", "3", "1.1.1.1"]


def test_ping_linux_no_reply(monkeypatch):
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lm.subprocess, "run", fake_run(LINUX_FAIL, returncode=1))
    assert lm.ping("10.255.255.1") is None


def test_ping_subprocess_timeout_returns_none(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ping", timeout=5)
    monkeypatch.setattr(lm.subprocess, "run", boom)
    assert lm.ping("8.8.8.8") is None


def test_ping_passes_argument_list_not_shell(monkeypatch):
    """A malicious host string must stay a single argument (no shell)."""
    calls = []
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lm.subprocess, "run", fake_run(LINUX_FAIL, 1, record=calls))
    lm.ping("8.8.8.8; rm -rf /")
    assert isinstance(calls[0], list)
    assert calls[0][-1] == "8.8.8.8; rm -rf /"


# ── statistics ────────────────────────────────────────────────────────────────
def test_hoststate_statistics():
    s = lm.HostState("t", "x")
    for v in [44.0, 58.0, None, 360.0]:
        s.record(v)
    assert s.sent == 4 and s.lost == 1
    assert s.avg == pytest.approx(154.0)
    assert s.min_lat == 44.0
    assert s.max_lat == 360.0
    assert s.loss_pct == pytest.approx(25.0)
    assert s.latency == 360.0


def test_hoststate_empty():
    s = lm.HostState("t", "x")
    assert s.avg is None and s.min_lat is None and s.max_lat is None
    assert s.loss_pct == 0.0


def test_hoststate_all_lost():
    s = lm.HostState("gw", "192.168.1.1")
    for _ in range(5):
        s.record(None)
    assert s.loss_pct == 100.0
    assert s.avg is None


def test_rolling_window_keeps_last_60():
    s = lm.HostState("t", "x")
    for v in range(100):
        s.record(float(v))
    assert len(s.history) == lm.HISTORY_SIZE == 60
    assert s.min_lat == 40.0 and s.max_lat == 99.0


def test_record_is_thread_safe():
    s = lm.HostState("t", "x")

    def worker():
        for _ in range(500):
            s.record(10.0)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert s.sent == 4000


# ── classification and formatting ────────────────────────────────────────────
@pytest.mark.parametrize("ms,colour", [
    (None, "red"), (10, "green"), (49.9, "green"),
    (50, "yellow"), (149, "yellow"), (150, "red"), (400, "red"),
])
def test_lat_colour(ms, colour):
    assert lm.lat_colour(ms) == colour


def test_fmt():
    assert lm.fmt(None) == "—"
    assert lm.fmt(58) == "58.0"
    assert lm.fmt(174.456, 2) == "174.46"


# ── CSV logging ──────────────────────────────────────────────────────────────
def test_csv_logging(tmp_path, monkeypatch):
    log = tmp_path / "latency_log.csv"
    monkeypatch.setattr(lm, "LOG_FILE", str(log))
    lm.init_csv()
    a, b, c = lm.HostState("A", "1.1.1.1"), lm.HostState("B", "2.2.2.2"), lm.HostState("C", "3.3.3.3")
    a.record(12.34)
    b.record(None)
    c.record(0.0)
    lm.log_csv([a, b, c])
    rows = list(csv.reader(open(log, newline="")))
    assert rows[0] == ["timestamp", "name", "host", "latency_ms", "status"]
    assert rows[1][1:] == ["A", "1.1.1.1", "12.3", "ok"]
    assert rows[2][1:] == ["B", "2.2.2.2", "", "timeout"]
    assert rows[3][1:] == ["C", "3.3.3.3", "0.0", "ok"]


def test_init_csv_does_not_overwrite(tmp_path, monkeypatch):
    log = tmp_path / "latency_log.csv"
    log.write_text("existing\n")
    monkeypatch.setattr(lm, "LOG_FILE", str(log))
    lm.init_csv()
    assert log.read_text() == "existing\n"


# ── command line and configuration ───────────────────────────────────────────
def run_main(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["latency_monitor.py"] + args)
    with pytest.raises(SystemExit) as exc:
        lm.main()
    return exc.value.code


def test_default_config_created_and_add_host(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    code = run_main(monkeypatch, ["--config", str(cfg), "--add-host", "Office Router", "192.168.0.1"])
    assert code == 0
    data = json.loads(cfg.read_text())
    assert data["interval"] == 2 and data["timeout"] == 3
    assert {"name": "Office Router", "host": "192.168.0.1"} in data["hosts"]
    assert len(data["hosts"]) == 5  # four defaults + the new one


def test_no_hosts_exits_with_error(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"interval": 2, "timeout": 3, "log_to_csv": False, "hosts": []}))
    assert run_main(monkeypatch, ["--config", str(cfg)]) == 1


def test_version_option(monkeypatch, capsys):
    assert run_main(monkeypatch, ["--version"]) == 0
    assert lm.__version__ in capsys.readouterr().out
