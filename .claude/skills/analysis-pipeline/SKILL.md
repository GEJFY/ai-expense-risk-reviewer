---
name: analysis-pipeline
description: Use when building or changing the data analysis pipeline — the ETL/ingestion layer, the four analysis approaches (rule-based, exploratory analysis, ML anomaly detection, autonomous agent), or the cost funnel that pre-filters transactions before the agent deep-dives. Triggers on work about scoring, the layered engine, data flow, or cost/scale design.
---

# 分析パイプライン

対象データ（会計仕訳・経費明細）を4層で評価し、リスクスコアを付ける。層は重ねて働く:

1. **ルールベース** — 監査知見の宣言的ルール（`config/rules/rule_catalog.yaml`）。決定論的＝入力が同じなら必ず同じ結果になる。
2. **探索的分析** — 可視化で申請者・費目・時系列の異常を洗い出す。
3. **機械学習（異常検知）** — Isolation Forest / LOF / Autoencoder / PCA で「多数派から外れた明細」を数値化。寄与要因は SHAP（＝スコアをどの特徴量がどれだけ押し上げたかの内訳）で提示。
4. **自律AIエージェント** — ①〜③を統合し、費目別シナリオから仮説を立て、外部証憑を集めて検証する（`agent-orchestration` スキル）。

## 絶対に守る: コスト・ファネル

全件（数百万明細）に LLM を直接流してはならない。順序が逆だと破綻する:

- **PHASE 1**: ルール＋ML を全件に決定論的・低コストで適用し、高リスク明細だけを選別する（＝ふるい分け）。
- **PHASE 2 以降**: LLM／エージェントの重い探索は、選別後の高リスク部分集合にのみ適用する。

これが「全件網羅」と「現実的な計算コスト」を両立させる根拠。選別ライン（例: `risk_score ≥ 70`）は `config/rules/rule_catalog.yaml` の `thresholds` に定義。

## 参照

- 全体像・データフロー・コスト見積: `docs/architecture.md`（§1 レイヤ構成 / §3 データフロー / コスト設計）
- 全件主張の裏付け（データ品質・完全性照合）: `docs/architecture.md` §4
- 各所見には必ず根拠（違反ルール名 / SHAP寄与上位）を添える。モジュール間の型は `data-contracts` スキル。
