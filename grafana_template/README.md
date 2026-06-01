# grafana_template

このディレクトリには、Grafana ダッシュボードのうち
MegaRAID 関連パネルだけを抜き出した JSON を置いています。

- [megaraid_panels.json](./megaraid_panels.json)

元になっているのは、検証環境で使っている
「🏠 ホームネットワーク統合ダッシュボード」の
MegaRAID セクションです。

そのまま 1 枚の完成ダッシュボード JSON ではなく、
「どのパネルをどう組んでいるか」を確認・再利用しやすくするための
部分テンプレートとして置いています。

含まれている主な内容:

- 仮想ドライブの劣化状態
- 物理ドライブの温度、エラー、SMART 情報
- `🧩 RAID構成 / ディスク配分` テーブル
  - 仮想ドライブごとの `DiskGroup / Span / Arm / Slot` を一覧表示
