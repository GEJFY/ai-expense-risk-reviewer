---
name: rule-authoring
description: Use when adding, editing, or reviewing detection rules or fraud scenarios in config/rules/*.yaml — creating a new rule, tuning severity or weights, defining a fraud scenario, or doing false-positive / alert-fatigue tuning.
---

# ルール／シナリオの作成

## 大原則: ルールはコードに直書きしない

検知ロジックは必ず `config/rules/rule_catalog.yaml` に宣言的に定義する（コードは YAML を読んで評価するだけにする）。こうすると監査人がコードを触らずにルールを保守でき、変更履歴も追える。

## rule_catalog.yaml

- スキーマはファイル冒頭 `# schema:` コメントに従う。ID はカテゴリ接頭辞（CTRL/DUP/TIME/AMT/VEND/PART/CONS/BEHV/PATT/COMM）＋連番。ID の重複は禁止。
- 各ルールに **`false_positive_notes`（誤検知の典型と除外条件）を必ず記載する**。これは必須項目 ── 誤検知の多いルールは現場で無視され（＝アラート疲れ）、プロダクト全体の信頼を損なうため。
- `severity` と `base_weight` がスコアに効く。`critical` は反社/制裁（VEND-001）や公務員贈収賄（PART-006）など。
- 選別ライン（例 `high: 70`）は `thresholds` セクション。これがファネルの選別に直結する（`analysis-pipeline` スキル）。

## fraud_scenarios.yaml

費目別の手口・着眼点・検証データ・適用手法を定義する。これは**検知とテストの単一の定義源**である: 同じシナリオ定義から、検知ルールと合成テスト（模擬不正データ）の両方を導く（`docs/spec-improvements.md` §9）。新しいシナリオには `linked_rules` と `synthetic_test` を必ず付ける。

## 変更後

YAML が壊れていないか（パースできるか＋ID重複がないか）を検証してから終える。
