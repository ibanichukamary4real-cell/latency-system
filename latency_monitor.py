#!/usr/bin/env python3
"""
Network Latency Monitor
Multi-host latency monitoring tool with live terminal dashboard.

Probes every host in config.json concurrently (one thread per host) using the
operating system's ping utility, keeps a rolling window of the last 60
readings per host, shows last/avg/min/max latency, loss % and a status
(GOOD < 50 ms, FAIR 50-149 ms, HIGH >= 150 ms, TIMEOUT = no reply), and logs
every reading to latency_log.csv.

Author : Marcus Blessing Onurawa
Licence: MIT
"""

__version__ = "1.0.0"
import subprocess
import platform
import json
import time
import threading
import csv
import os
import sys
import re
import signal
import argparse
from datetime import datetime
from collections import deque

# ── Optional rich UI ──────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.panel import Panel
    from rich import box
    RICH = True
    console = Console()
except ImportError:
    RICH = False
    console = None

# ── Constants ─────────────────────────────────────────────────────────────────
CONFIG_FILE = "config.json"
LOG_FILE = "latency_log.csv"
HISTORY_SIZE = 60  # readings kept per host for avg/min/max

DEFAULT_CONFIG = {
    "interval": 2,
    "timeout": 3,
    "log_to_csv": True,
    "hosts": [
        {"name": "Google DNS", "host": "8.8.8.8"},
        {"name": "Cloudflare DNS", "host": "1.1.1.1"},
        {"name": "OpenDNS", "host": "208.67.222.222"},
        {"name": "Local Gateway", "host": "192.168.1.1"}
    ]
}


# ── Per-host state ────────────────────────────────────────────────────────────
class HostState:
    def __init__(self, name, host):
        self.name = name
        self.host = host
        self.latency = None  # last reading (ms or None)
        self.history = deque(maxlen=HISTORY_SIZE)
        self.sent = 0
        self.lost = 0
        self.lock = threading.Lock()

    @property
    def loss_pct(self):
        return (self.lost / self.sent * 100) if self.sent else 0.0

    @property
    def avg(self):
        vals = [v for v in self.history if v is not None]
        return sum(vals) / len(vals) if vals else None

    @property
    def min_lat(self):
        vals = [v for v in self.history if v is not None]
        return min(vals) if vals else None

    @property
    def max_lat(self):
        vals = [v for v in self.history if v is not None]
        return max(vals) if vals else None

    def record(self, ms):
        with self.lock:
            self.sent += 1
            self.latency = ms
            self.history.append(ms)
            if ms is None:
                self.lost += 1


# ── Ping ──────────────────────────────────────────────────────────────────────
def ping(host, timeout=3):
    """Return latency in ms (float) or None on failure."""
    system = platform.system().lower()
    try:
        if system == "windows":
            cmd = ["ping", "-n", "1", "-w", str(timeout * 1000), host]
        else:
            cmd = ["ping", "-c", "1", "-W", str(timeout), host]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 2
        )
        out = result.stdout
        if system == "windows":
            m = re.search(r"time[=<](\d+)ms", out)
        else:
            m = re.search(r"time[=<](\d+\.?\d*)\s*ms", out)
        if m and result.returncode == 0:
            return float(m.group(1))
        return None
    except Exception:
        return None


# ── Background polling thread per host ───────────────────────────────────────
def poll_host(state: HostState, interval: int, timeout: int, stop_event: threading.Event):
    while not stop_event.is_set():
        ms = ping(state.host, timeout)
        state.record(ms)
        stop_event.wait(interval)


# ── CSV logging ───────────────────────────────────────────────────────────────
def init_csv():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            csv.writer(f).writerow(["timestamp", "name", "host", "latency_ms", "status"])


def log_csv(states):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        for s in states:
            with s.lock:
                lat = s.latency
            status = "ok" if lat is not None else "timeout"
            w.writerow([ts, s.name, s.host, f"{lat:.1f}" if lat is not None else "", status])


# ── Status colour helpers ─────────────────────────────────────────────────────
def lat_colour(ms):
    if ms is None: return "red"
    if ms < 50: return "green"
    if ms < 150: return "yellow"
    return "red"


def fmt(ms, decimals=1):
    return f"{ms:.{decimals}f}" if ms is not None else "—"


# ── Rich table builder ────────────────────────────────────────────────────────
def build_table(states):
    table = Table(
        title=f"[bold cyan]Network Latency Monitor[/]  [dim]{datetime.now().strftime('%H:%M:%S')}[/]",
        box=box.ROUNDED, border_style="cyan", show_footer=False
    )
    table.add_column("Host", style="bold white", min_width=16)
    table.add_column("Address", style="dim", min_width=15)
    table.add_column("Last (ms)", justify="right", min_width=10)
    table.add_column("Avg (ms)", justify="right", min_width=10)
    table.add_column("Min (ms)", justify="right", min_width=10)
    table.add_column("Max (ms)", justify="right", min_width=10)
    table.add_column("Loss %", justify="right", min_width=9)
    table.add_column("Status", justify="center", min_width=10)
    for s in states:
        with s.lock:
            lat = s.latency
            avg = s.avg
            mn = s.min_lat
            mx = s.max_lat
            loss = s.loss_pct
        colour = lat_colour(lat)
        if lat is None:
            status = "[red]● TIMEOUT[/]"
        elif lat < 50:
            status = "[green]● GOOD[/]"
        elif lat < 150:
            status = "[yellow]● FAIR[/]"
        else:
            status = "[red]● HIGH[/]"
        table.add_row(
            s.name,
            s.host,
            f"[{colour}]{fmt(lat)}[/]",
            fmt(avg),
            fmt(mn),
            fmt(mx),
            f"[{'red' if loss > 5 else 'green'}]{loss:.1f}%[/]",
            status,
        )
    return table


# ── Plain-text fallback display ───────────────────────────────────────────────
def plain_display(states):
    os.system("cls" if platform.system().lower() == "windows" else "clear")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== Network Latency Monitor  {ts} ===\n")
    header = f"{'Host':<18} {'Address':<17} {'Last':>8} {'Avg':>8} {'Min':>8} {'Max':>8} {'Loss%':>7} Status"
    print(header)
    print("-" * len(header))
    for s in states:
        with s.lock:
            lat, avg, mn, mx, loss = s.latency, s.avg, s.min_lat, s.max_lat, s.loss_pct
        status = "TIMEOUT" if lat is None else ("GOOD" if lat < 50 else ("FAIR" if lat < 150 else "HIGH"))
        print(
            f"{s.name:<18} {s.host:<17} {fmt(lat):>8} {fmt(avg):>8} {fmt(mn):>8} {fmt(mx):>8} {loss:>6.1f}% {status}"
        )
    print("\n[Ctrl+C to quit]  Log →", LOG_FILE)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Network Latency Monitor")
    parser.add_argument("--config", default=CONFIG_FILE, help="Config file path")
    parser.add_argument("--interval", type=int, help="Ping interval (seconds)")
    parser.add_argument("--no-log", action="store_true", help="Disable CSV logging")
    parser.add_argument("--add-host", nargs=2, metavar=("NAME", "HOST"), help="Add a host to config and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    # ── Load / create config ──────────────────────────────────────────────
    if not os.path.exists(args.config):
        with open(args.config, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        print(f"Created default config: {args.config}")

    with open(args.config) as f:
        cfg = json.load(f)

    # ── --add-host shortcut ───────────────────────────────────────────────
    if args.add_host:
        name, host = args.add_host
        cfg["hosts"].append({"name": name, "host": host})
        with open(args.config, "w") as f:
            json.dump(cfg, f, indent=2)
        print(f"Added host: {name} ({host})")
        sys.exit(0)

    interval = args.interval or cfg.get("interval", 2)
    timeout = cfg.get("timeout", 3)
    log_csv_enabled = not args.no_log and cfg.get("log_to_csv", True)

    if not cfg.get("hosts"):
        print("No hosts defined in config. Edit config.json and try again.")
        sys.exit(1)

    # ── Build host states ─────────────────────────────────────────────────
    states = [HostState(h["name"], h["host"]) for h in cfg["hosts"]]
    if log_csv_enabled:
        init_csv()

    # ── Start background threads ──────────────────────────────────────────
    stop_event = threading.Event()
    threads = []
    for s in states:
        t = threading.Thread(target=poll_host, args=(s, interval, timeout, stop_event), daemon=True)
        t.start()
        threads.append(t)

    def shutdown(sig=None, frame=None):
        print("\nStopping…")
        stop_event.set()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("Starting… (first readings in a moment)")
    time.sleep(1)

    # ── Display loop ──────────────────────────────────────────────────────
    if RICH:
        with Live(build_table(states), refresh_per_second=2, screen=False) as live:
            while not stop_event.is_set():
                live.update(build_table(states))
                if log_csv_enabled:
                    log_csv(states)
                time.sleep(1)
    else:
        while not stop_event.is_set():
            plain_display(states)
            if log_csv_enabled:
                log_csv(states)
            time.sleep(interval)


if __name__ == "__main__":
    main()
