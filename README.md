# megaraid-exporter

`megaraid-exporter` is a small Prometheus exporter for MegaRAID controllers.
It collects:

- physical drive online/offline state
- media, other, and predictive failure counts from MegaCLI
- drive temperature from MegaCLI
- SMART-derived counters through `smartctl -d megaraid,N /dev/bus/0`
- virtual drive optimal/degraded state
- virtual drive read/write byte counters from `/proc/diskstats`

The current implementation was validated against a Lenovo ServeRAID M5210 on
Proxmox with Toshiba MQ01ABF050 drives.

## Metrics

The exporter currently emits:

- `megaraid_pd_info`
- `megaraid_pd_online`
- `megaraid_pd_temperature_celsius`
- `megaraid_pd_media_errors_total`
- `megaraid_pd_other_errors_total`
- `megaraid_pd_predictive_failures_total`
- `megaraid_pd_smart_alert`
- `megaraid_pd_reallocated_sectors`
- `megaraid_pd_pending_sectors`
- `megaraid_pd_uncorrectable_sectors`
- `megaraid_pd_power_on_hours`
- `megaraid_vd_optimal`
- `megaraid_vd_read_bytes_total`
- `megaraid_vd_write_bytes_total`

## Requirements

- Python 3
- `megacli`
- `smartctl`
- access to `/dev/bus/0` for MegaRAID passthrough SMART reads

## Running

```bash
python3 megaraid_exporter.py
```

The exporter listens on `:9925` and exposes Prometheus metrics at `/metrics`.

## systemd

An example unit file is included at:

- [megaraid-exporter.service](./examples/megaraid-exporter.service)
