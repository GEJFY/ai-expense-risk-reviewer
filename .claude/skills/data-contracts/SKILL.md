---
name: data-contracts
description: Use when defining or changing data passed between modules — ExpenseLine inputs, RiskFinding outputs, Evidence records, or AuditLogEntry — or when implementing ETL output, the report schema, or the audit log. Ensures everything conforms to the JSON Schema.
---

# データ契約（モジュール間 I/O）

すべてのモジュール間の受け渡しは `config/schemas/data_contracts.json`（JSON Schema）に準拠する。スキーマ違反は実行時に検証し、所見に `data_quality` フラグを付す。4つの型がある:

- **ExpenseLine** — 入力の経費明細。
- **RiskFinding** — 出力の所見。**根拠（`rationale` ／説明可能性）が必須**。AIは `hitl_status` を自分で `confirmed` にできない（確定は人間のみ）。
- **Evidence** — 収集した証憑。出所（`provenance`）・法的基盤（`legal_basis`）・インジェクション検査結果（`injection_flags`）を保持する。信頼できない外部由来であることを記録に残すため。
- **AuditLogEntry** — 監査ログ。**WORM＋ハッシュチェーン**（各エントリが直前の `prev_hash` を含み、自身の `hash` で連結）＝1件でも改ざんすると鎖が切れて検知できる仕組み。

変更時は JSON がパースできることを確認する。改ざん不能ログの運用要件は `governance-independence` スキル。
