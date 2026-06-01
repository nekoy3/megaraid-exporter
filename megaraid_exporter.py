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
        elif line.startswith("Drive's position:"):
            match = re.search(r"DiskGroup:\s*(\d+),\s*Span:\s*(\d+),\s*Arm:\s*(\d+)", line)
            if match:
                current["diskgroup"] = match.group(1)
                current["span"] = match.group(2)
                current["arm"] = match.group(3)
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
            current["state"] = raw.split(",")[0].strip()
        elif line.startswith("Coerced Size:"):
            current["size"] = line.split("[", 1)[0].split(":", 1)[1].strip()
        elif line.startswith("Device Speed:"):
            current["speed"] = line.split(":", 1)[1].strip()
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


def parse_ld_pd_info():
    output = run_megacli("-LdPdInfo", "-aALL")
    members = []
    virtual_drives = []
    current_vd = None
    current_span = None
    current_pd = None

    def finish_pd():
        nonlocal current_pd
        if current_vd and current_pd and "slot" in current_pd:
            member = dict(current_pd)
            member["vd"] = current_vd.get("vd", "?")
            member["target_id"] = current_vd.get("target_id", "?")
            member["name"] = current_vd.get("name", current_vd.get("vd", "?"))
            member["raid_level"] = current_vd.get("raid_level", "unknown")
            member["vd_state"] = current_vd.get("state", "unknown")
            member["vd_size"] = current_vd.get("size", "unknown")
            member["drives_per_span"] = current_vd.get("drives_per_span", "unknown")
            member["span_depth"] = current_vd.get("span_depth", "unknown")
            member["number_of_spans"] = current_vd.get("number_of_spans", "unknown")
            member["span"] = current_span if current_span is not None else current_pd.get("span", "unknown")
            members.append(member)
        current_pd = None

    def finish_vd():
        nonlocal current_vd, current_span
        finish_pd()
        if current_vd and "vd" in current_vd:
            virtual_drives.append(current_vd)
        current_vd = None
        current_span = None

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("Virtual Drive:"):
            finish_vd()
            match = re.search(r"Virtual Drive:\s*(\d+).*Target Id:\s*(\d+)", line)
            current_vd = {
                "vd": match.group(1) if match else "?",
                "target_id": match.group(2) if match else "?",
            }
        elif current_vd is not None and line.startswith("Name"):
            current_vd["name"] = line.split(":", 1)[1].strip()
        elif current_vd is not None and line.startswith("RAID Level"):
            current_vd["raid_level"] = line.split(":", 1)[1].strip()
        elif current_vd is not None and line.startswith("Size"):
            current_vd["size"] = line.split(":", 1)[1].strip()
        elif current_vd is not None and line.startswith("State"):
            current_vd["state"] = line.split(":", 1)[1].strip()
        elif current_vd is not None and line.startswith("Number Of Drives per span:"):
            current_vd["drives_per_span"] = line.split(":", 1)[1].strip()
        elif current_vd is not None and line.startswith("Span Depth"):
            current_vd["span_depth"] = line.split(":", 1)[1].strip()
        elif current_vd is not None and line.startswith("Number of Spans:"):
            current_vd["number_of_spans"] = line.split(":", 1)[1].strip()
        elif current_vd is not None and line.startswith("Span:"):
            finish_pd()
            match = re.search(r"Span:\s*(\d+)", line)
            current_span = match.group(1) if match else "unknown"
        elif current_vd is not None and line.startswith("PD:"):
            finish_pd()
            current_pd = {}
        elif current_pd is not None and line.startswith("Slot Number:"):
            current_pd["slot"] = line.split(":", 1)[1].strip()
        elif current_pd is not None and line.startswith("Device Id:"):
            current_pd["device_id"] = line.split(":", 1)[1].strip()
        elif current_pd is not None and line.startswith("Drive's position:"):
            match = re.search(r"DiskGroup:\s*(\d+),\s*Span:\s*(\d+),\s*Arm:\s*(\d+)", line)
            if match:
                current_pd["diskgroup"] = match.group(1)
                current_pd["span"] = match.group(2)
                current_pd["arm"] = match.group(3)
        elif current_pd is not None and line.startswith("Firmware state:"):
            current_pd["firmware_state"] = line.split(":", 1)[1].strip().split(",")[0].strip()
        elif current_pd is not None and line.startswith("Inquiry Data:"):
            parse_inquiry_data(current_pd, line.split(":", 1)[1])

    finish_vd()
    return virtual_drives, members


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
    ld_pd_virtual_drives, ld_pd_members = parse_ld_pd_info()
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
        state = pd.get("state", fw)
        size = pd.get("size", "unknown")
        speed = pd.get("speed", "unknown")
        diskgroup = pd.get("diskgroup", "unknown")
        span = pd.get("span", "unknown")
        arm = pd.get("arm", "unknown")
        lines.append(
            f'megaraid_pd_info{{slot="{slot}",device_id="{device_id}",serial="{serial}",model="{model}",firmware_state="{fw}",state="{state}",size="{size}",speed="{speed}",diskgroup="{diskgroup}",span="{span}",arm="{arm}"}} 1'
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

    lines.append("# HELP megaraid_vd_info Virtual drive configuration info")
    lines.append("# TYPE megaraid_vd_info gauge")
    for vd in ld_pd_virtual_drives:
        lines.append(
            f'megaraid_vd_info{{vd="{vd.get("vd", "?")}",target_id="{vd.get("target_id", "?")}",name="{vd.get("name", "?")}",raid_level="{vd.get("raid_level", "unknown")}",state="{vd.get("state", "unknown")}",size="{vd.get("size", "unknown")}",drives_per_span="{vd.get("drives_per_span", "unknown")}",span_depth="{vd.get("span_depth", "unknown")}",number_of_spans="{vd.get("number_of_spans", "unknown")}"}} 1'
        )

    lines.append("# HELP megaraid_vd_member_info Virtual drive member layout info")
    lines.append("# TYPE megaraid_vd_member_info gauge")
    for member in ld_pd_members:
        model = re.sub(r"\s+", " ", member.get("model", member.get("inquiry_data", "unknown"))).strip()
        serial = member.get("serial", "unknown")
        lines.append(
            f'megaraid_vd_member_info{{vd="{member.get("vd", "?")}",target_id="{member.get("target_id", "?")}",name="{member.get("name", "?")}",raid_level="{member.get("raid_level", "unknown")}",vd_state="{member.get("vd_state", "unknown")}",vd_size="{member.get("vd_size", "unknown")}",drives_per_span="{member.get("drives_per_span", "unknown")}",span_depth="{member.get("span_depth", "unknown")}",number_of_spans="{member.get("number_of_spans", "unknown")}",diskgroup="{member.get("diskgroup", "unknown")}",span="{member.get("span", "unknown")}",arm="{member.get("arm", "unknown")}",slot="{member.get("slot", "?")}",device_id="{member.get("device_id", "unknown")}",serial="{serial}",model="{model}",firmware_state="{member.get("firmware_state", "unknown")}"}} 1'
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
