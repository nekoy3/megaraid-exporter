#!/usr/bin/env python3
"""MegaRAID Prometheus Exporter - matches Grafana dashboard metric names."""

import re
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 9925
MEGACLI = "/usr/sbin/megacli"

# VD Target ID -> block device name mapping
VD_TO_BLOCKDEV = {0: "sda", 1: "sdb"}


def run_megacli(*args):
    try:
        result = subprocess.run(
            [MEGACLI] + list(args),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except Exception:
        return ""


def parse_pd_list():
    output = run_megacli("-PDList", "-aALL")
    drives = []
    current = {}
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Slot Number:"):
            if current and "slot" in current:
                drives.append(current)
            current = {"slot": line.split(":", 1)[1].strip()}
        elif line.startswith("Device Id:"):
            current["device_id"] = line.split(":", 1)[1].strip()
        elif line.startswith("Media Error Count:"):
            current["media_errors"] = int(line.split(":", 1)[1].strip())
        elif line.startswith("Other Error Count:"):
            current["other_errors"] = int(line.split(":", 1)[1].strip())
        elif line.startswith("Predictive Failure Count:"):
            current["predictive_failures"] = int(line.split(":", 1)[1].strip())
        elif line.startswith("Firmware state:"):
            raw = line.split(":", 1)[1].strip()
            current["firmware_state_str"] = raw
            base = raw.split(",")[0].strip().lower()
            current["online"] = 1 if "online" in base else 0
        elif line.startswith("Drive Temperature"):
            match = re.search(r"(\d+)C", line)
            if match:
                current["temperature"] = int(match.group(1))
        elif line.startswith("Inquiry Data:"):
            parse_inquiry_data(current, line.split(":", 1)[1])
    if current and "slot" in current:
        drives.append(current)
    return drives


def parse_inquiry_data(current, raw):
    """Split MegaCLI Inquiry Data into stable serial/model/firmware fields."""
    inquiry = re.sub(r"\s+", " ", raw).strip()
    current["inquiry_data"] = inquiry

    # Common MegaCLI format for these Toshiba disks has no separator between
    # serial and model, e.g. X9QFT0CQTTOSHIBA MQ01ABF050 AM0P6M.
    match = re.match(
        r"(?P<serial>[A-Z0-9]+)(?P<model>TOSHIBA\s+MQ01ABF050)\s+(?P<firmware>\S+)",
        inquiry,
    )
    if match:
        current.update(match.groupdict())
        return

    parts = inquiry.split()
    if parts:
        current["serial"] = parts[0]
        current["model"] = " ".join(parts[1:-1]) if len(parts) > 2 else inquiry
        if len(parts) > 1:
            current["firmware"] = parts[-1]
    else:
        current["model"] = "unknown"


def parse_ld_list():
    output = run_megacli("-LDInfo", "-Lall", "-aALL")
    drives = []
    current = {}
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Virtual Drive:"):
            if current and "vd" in current:
                drives.append(current)
            match = re.search(r"Virtual Drive:\s*(\d+).*Target Id:\s*(\d+)", line)
            current = {
                "vd": match.group(1) if match else "?",
                "target_id": int(match.group(2)) if match else -1,
            }
        elif line.startswith("Name"):
            current["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("RAID Level"):
            current["raid_level"] = line.split(":", 1)[1].strip()
        elif line.startswith("State"):
            raw = line.split(":", 1)[1].strip().lower()
            current["state_str"] = raw
            current["optimal"] = 1 if raw == "optimal" else 0
        elif line.startswith("Size"):
            current["size"] = line.split(":", 1)[1].strip()
    if current and "vd" in current:
        drives.append(current)
    return drives


def build_smart_device_ids():
    """Return megaraid,N ids advertised by smartctl --scan for /dev/bus/0."""
    ids = set()
    try:
        scan = subprocess.run(
            ["smartctl", "--scan"], capture_output=True, text=True, timeout=10
        )
        for line in scan.stdout.splitlines():
            match = re.search(r"/dev/bus/0\s+-d\s+megaraid,(\d+)", line)
            if match:
                ids.add(match.group(1))
    except Exception:
        pass
    return ids


def get_smart_attrs(smartctl_n):
    """Get SMART attributes using /dev/bus/0 megaraid,N interface."""
    attrs = {}
    try:
        result = subprocess.run(
            ["smartctl", "-A", "-d", f"megaraid,{smartctl_n}", "/dev/bus/0"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 10 and parts[0].isdigit():
                attr_id = int(parts[0])
                raw_val = parts[-1]
                if attr_id == 5:
                    attrs["reallocated_sectors"] = int(raw_val)
                elif attr_id == 9:
                    attrs["power_on_hours"] = int(raw_val)
                elif attr_id == 197:
                    attrs["pending_sectors"] = int(raw_val)
                elif attr_id == 198:
                    attrs["uncorrectable_sectors"] = int(raw_val)
    except Exception:
        pass
    return attrs


def get_diskstats():
    """Read /proc/diskstats and return {devname: {reads_bytes, writes_bytes}}"""
    stats = {}
    try:
        with open("/proc/diskstats") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 14:
                    continue
                dev = parts[2]
                if dev in ("sda", "sdb"):
                    sectors_read = int(parts[5])
                    sectors_written = int(parts[9])
                    stats[dev] = {
                        "read_bytes": sectors_read * 512,
                        "write_bytes": sectors_written * 512,
                    }
    except Exception:
        pass
    return stats


def generate_metrics():
    lines = []

    pd_list = parse_pd_list()
    ld_list = parse_ld_list()
    diskstats = get_diskstats()
    smart_device_ids = build_smart_device_ids()

    lines.append("# HELP megaraid_pd_info Physical drive info")
    lines.append("# TYPE megaraid_pd_info gauge")
    for pd in pd_list:
        slot = pd.get("slot", "?")
        model = re.sub(r"\s+", " ", pd.get("model", pd.get("inquiry_data", "unknown"))).strip()
        serial = pd.get("serial", "unknown")
        device_id = pd.get("device_id", "unknown")
        fw = pd.get("firmware_state_str", "unknown").split(",")[0].strip()
        lines.append(
            f'megaraid_pd_info{{slot="{slot}",device_id="{device_id}",serial="{serial}",model="{model}",firmware_state="{fw}"}} 1'
        )

    lines.append("# HELP megaraid_pd_online Physical drive online status (1=online)")
    lines.append("# TYPE megaraid_pd_online gauge")
    for pd in pd_list:
        slot = pd.get("slot", "?")
        model = re.sub(r"\s+", " ", pd.get("model", pd.get("inquiry_data", "unknown"))).strip()
        serial = pd.get("serial", "unknown")
        device_id = pd.get("device_id", "unknown")
        lines.append(
            f'megaraid_pd_online{{slot="{slot}",device_id="{device_id}",serial="{serial}",model="{model}"}} {pd.get("online", 0)}'
        )

    lines.append("# HELP megaraid_pd_temperature_celsius Physical drive temperature in Celsius")
    lines.append("# TYPE megaraid_pd_temperature_celsius gauge")
    for pd in pd_list:
        if "temperature" in pd:
            slot = pd.get("slot", "?")
            model = re.sub(r"\s+", " ", pd.get("model", pd.get("inquiry_data", "unknown"))).strip()
            serial = pd.get("serial", "unknown")
            device_id = pd.get("device_id", "unknown")
            lines.append(
                f'megaraid_pd_temperature_celsius{{slot="{slot}",device_id="{device_id}",serial="{serial}",model="{model}"}} {pd["temperature"]}'
            )

    lines.append("# HELP megaraid_pd_media_errors_total Physical drive media error count")
    lines.append("# TYPE megaraid_pd_media_errors_total counter")
    for pd in pd_list:
        slot = pd.get("slot", "?")
        model = re.sub(r"\s+", " ", pd.get("model", pd.get("inquiry_data", "unknown"))).strip()
        serial = pd.get("serial", "unknown")
        device_id = pd.get("device_id", "unknown")
        lines.append(
            f'megaraid_pd_media_errors_total{{slot="{slot}",device_id="{device_id}",serial="{serial}",model="{model}"}} {pd.get("media_errors", 0)}'
        )

    lines.append("# HELP megaraid_pd_other_errors_total Physical drive other error count")
    lines.append("# TYPE megaraid_pd_other_errors_total counter")
    for pd in pd_list:
        slot = pd.get("slot", "?")
        model = re.sub(r"\s+", " ", pd.get("model", pd.get("inquiry_data", "unknown"))).strip()
        serial = pd.get("serial", "unknown")
        device_id = pd.get("device_id", "unknown")
        lines.append(
            f'megaraid_pd_other_errors_total{{slot="{slot}",device_id="{device_id}",serial="{serial}",model="{model}"}} {pd.get("other_errors", 0)}'
        )

    lines.append("# HELP megaraid_pd_predictive_failures_total Physical drive predictive failure count")
    lines.append("# TYPE megaraid_pd_predictive_failures_total counter")
    for pd in pd_list:
        slot = pd.get("slot", "?")
        model = re.sub(r"\s+", " ", pd.get("model", pd.get("inquiry_data", "unknown"))).strip()
        serial = pd.get("serial", "unknown")
        device_id = pd.get("device_id", "unknown")
        lines.append(
            f'megaraid_pd_predictive_failures_total{{slot="{slot}",device_id="{device_id}",serial="{serial}",model="{model}"}} {pd.get("predictive_failures", 0)}'
        )

    lines.append("# HELP megaraid_pd_smart_alert Physical drive SMART alert (1=alert)")
    lines.append("# TYPE megaraid_pd_smart_alert gauge")
    for pd in pd_list:
        slot = pd.get("slot", "?")
        model = re.sub(r"\s+", " ", pd.get("model", pd.get("inquiry_data", "unknown"))).strip()
        serial = pd.get("serial", "unknown")
        device_id = pd.get("device_id", "unknown")
        alert = 1 if (pd.get("media_errors", 0) > 0 or pd.get("predictive_failures", 0) > 0) else 0
        lines.append(
            f'megaraid_pd_smart_alert{{slot="{slot}",device_id="{device_id}",serial="{serial}",model="{model}"}} {alert}'
        )

    lines.append("# HELP megaraid_pd_reallocated_sectors Reallocated sector count (SMART attr 5)")
    lines.append("# TYPE megaraid_pd_reallocated_sectors gauge")
    lines.append("# HELP megaraid_pd_pending_sectors Current pending sector count (SMART attr 197)")
    lines.append("# TYPE megaraid_pd_pending_sectors gauge")
    lines.append("# HELP megaraid_pd_uncorrectable_sectors Uncorrectable sector count (SMART attr 198)")
    lines.append("# TYPE megaraid_pd_uncorrectable_sectors gauge")
    lines.append("# HELP megaraid_pd_power_on_hours Power-on hours (SMART attr 9)")
    lines.append("# TYPE megaraid_pd_power_on_hours counter")

    for pd in pd_list:
        slot = pd.get("slot", "?")
        model = re.sub(r"\s+", " ", pd.get("model", pd.get("inquiry_data", "unknown"))).strip()
        serial = pd.get("serial", "unknown")
        device_id = pd.get("device_id", "unknown")
        smart = get_smart_attrs(device_id) if device_id in smart_device_ids else {}
        lbl = (
            f'slot="{slot}",device_id="{device_id}",serial="{serial}",model="{model}"'
        )
        lines.append(
            f'megaraid_pd_reallocated_sectors{{{lbl}}} {smart.get("reallocated_sectors", 0)}'
        )
        lines.append(
            f'megaraid_pd_pending_sectors{{{lbl}}} {smart.get("pending_sectors", 0)}'
        )
        lines.append(
            f'megaraid_pd_uncorrectable_sectors{{{lbl}}} {smart.get("uncorrectable_sectors", 0)}'
        )
        lines.append(
            f'megaraid_pd_power_on_hours{{{lbl}}} {smart.get("power_on_hours", 0)}'
        )

    lines.append("# HELP megaraid_vd_optimal Virtual drive optimal status (1=optimal, 0=degraded/failed)")
    lines.append("# TYPE megaraid_vd_optimal gauge")
    for ld in ld_list:
        name = ld.get("name", ld.get("vd", "?"))
        raid = ld.get("raid_level", "unknown")
        lines.append(
            f'megaraid_vd_optimal{{name="{name}",raid_level="{raid}"}} {ld.get("optimal", 0)}'
        )

    lines.append("# HELP megaraid_vd_read_bytes_total Total bytes read from virtual drive")
    lines.append("# TYPE megaraid_vd_read_bytes_total counter")
    lines.append("# HELP megaraid_vd_write_bytes_total Total bytes written to virtual drive")
    lines.append("# TYPE megaraid_vd_write_bytes_total counter")
    for ld in ld_list:
        name = ld.get("name", ld.get("vd", "?"))
        target_id = ld.get("target_id", -1)
        blockdev = VD_TO_BLOCKDEV.get(target_id)
        if blockdev and blockdev in diskstats:
            ds = diskstats[blockdev]
            lines.append(
                f'megaraid_vd_read_bytes_total{{name="{name}",dev="{blockdev}"}} {ds["read_bytes"]}'
            )
            lines.append(
                f'megaraid_vd_write_bytes_total{{name="{name}",dev="{blockdev}"}} {ds["write_bytes"]}'
            )

    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            try:
                body = generate_metrics().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(exc).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"MegaRAID exporter listening on port {PORT}")
    server.serve_forever()
