# megaraid-exporter

`megaraid-exporter` は、MegaRAID 系 RAID コントローラの状態を
Prometheus で監視するための、小さな Python 製 exporter です。

物理ドライブの Online / Offline 状態、温度、MegaCLI が返すエラー回数、
`smartctl` 経由で取得できる SMART 属性、仮想ドライブの劣化状態、
`/proc/diskstats` から取る読み書き量をまとめて `/metrics` として公開します。

## MegaRAID とは

MegaRAID は Broadcom / LSI 系の RAID コントローラ製品群です。
OS からは複数ディスクが 1 台または複数台の「仮想ドライブ」に見える一方、
実際の物理ドライブごとの状態確認には、コントローラ専用ツールや
パススルー経由の SMART 取得が必要になることがあります。

この exporter は、その「OS からは見えにくい物理ディスクの状態」を
Prometheus から観測しやすくすることを目的にしています。

## 想定環境

この実装は、次の環境で動作確認しています。

- ホスト OS: Proxmox VE 8.4.0
- カーネル: `6.8.12-9-pve`
- RAID コントローラ: Lenovo ServeRAID M5210
- 物理ドライブ: TOSHIBA `MQ01ABF050`

特に、物理ドライブの SMART 属性は
`smartctl -d megaraid,N /dev/bus/0` が使える構成を前提としています。

## 動作確認したソフトウェア

- Python: `3.11.2`
- smartmontools / `smartctl`: `7.3`
- MegaCLI: `8.07.14`

`megacli` の配置は `/usr/sbin/megacli` を前提にしています。

## 依存パッケージ

外部の Python パッケージは使っていません。
利用しているのは Python 標準ライブラリのみです。

- `http.server`
- `subprocess`
- `re`

実行に必要なコマンドは次の 2 つです。

- `megacli`
- `smartctl`

## 取得する情報

この exporter は大きく 3 種類の情報を集めます。

1. MegaCLI から取得する物理ドライブ状態
2. `smartctl -d megaraid,N /dev/bus/0` から取得する SMART 属性
3. `/proc/diskstats` から取得する仮想ドライブの I/O バイト数

SMART 属性の対応は次の通りです。

- `5`: Reallocated Sector Count
- `9`: Power-On Hours
- `197`: Current Pending Sector
- `198`: Offline Uncorrectable

## メトリクス一覧

### 物理ドライブ

- `megaraid_pd_info`
  - 物理ドライブ情報。`slot`、`device_id`、`serial`、`model`、`firmware_state` をラベルに持つ info 用 gauge
- `megaraid_pd_online`
  - 物理ドライブがオンラインなら `1`、オフラインなら `0`
- `megaraid_pd_temperature_celsius`
  - 物理ドライブ温度
- `megaraid_pd_media_errors_total`
  - Media Error Count
- `megaraid_pd_other_errors_total`
  - Other Error Count
- `megaraid_pd_predictive_failures_total`
  - Predictive Failure Count
- `megaraid_pd_smart_alert`
  - media error または predictive failure があれば `1`
- `megaraid_pd_reallocated_sectors`
  - SMART 属性 5
- `megaraid_pd_pending_sectors`
  - SMART 属性 197
- `megaraid_pd_uncorrectable_sectors`
  - SMART 属性 198
- `megaraid_pd_power_on_hours`
  - SMART 属性 9

### 仮想ドライブ

- `megaraid_vd_optimal`
  - 仮想ドライブが `Optimal` なら `1`、それ以外なら `0`
- `megaraid_vd_read_bytes_total`
  - 仮想ドライブの累積読み込みバイト数
- `megaraid_vd_write_bytes_total`
  - 仮想ドライブの累積書き込みバイト数

## 実行方法

```bash
python3 megaraid_exporter.py
```

デフォルトでは `0.0.0.0:9925` で待ち受け、`/metrics` を公開します。

## systemd

簡単な unit file の例を同梱しています。

- [examples/megaraid-exporter.service](./examples/megaraid-exporter.service)

## 実装上の前提

- 仮想ドライブの I/O バイト数は `/proc/diskstats` を読みます
- `VD_TO_BLOCKDEV` は、現在の検証環境では `0 -> sda`, `1 -> sdb` を前提にしています
- 物理ドライブと `smartctl -d megaraid,N` の対応付けには、MegaCLI の `Device Id` を使っています

環境によっては、仮想ドライブと block device の対応付け部分は
調整が必要になるかもしれません。

## ライセンス

MIT License です。
詳細は [LICENSE](./LICENSE) を参照してください。
