# 経費不正リスク分析 自律AIエージェント — リソース一式

PwC「統合自律型のデータ分析監査 — 次世代リスク評価アプローチ」の提案スライドを、Claude Code で実装可能なプロジェクトリソースに落とし込んだものです。スライドの設計意図を保持しつつ、実装・運用・ガバナンスの観点で**不足していた仕様を補強**しています。

構成は Claude Code のベストプラクティスに沿っています。要点は「Claude の文脈ウィンドウはすぐ埋まり、埋まるほど精度が落ちる」という制約への対処で、**常に効く原則だけを薄い `CLAUDE.md` に置き、深い仕様は作業内容に応じて自動ロードされるスキルへ分離**しています。

## このリポジトリの構成

| 層 | 役割 | 読み込まれ方 |
|---|---|---|
| `CLAUDE.md` | 常に効く原則・進め方・対話スタイル | 毎セッション自動 |
| `.claude/skills/*/SKILL.md` | タスク別の深い指示（7本） | 作業内容が説明文に合致した時だけ自動ロード |
| `.claude/settings.json` | 権限スコープ（allow/ask/deny）の雛形 | Claude Code が参照 |
| `docs/*.md` | 深い仕様の一次資料（人間向け） | スキルから参照。必要時に読む |
| `config/**` | 機械可読の定義（ルール・シナリオ・スキーマ） | 実装が読む／編集する |

この分離により、CLAUDE.md は薄く保たれ（＝重要な原則が埋もれない）、詳細は必要な時にだけ文脈に載ります。

## スキル（`.claude/skills/`）

作業内容がスキルの説明文に合致すると、Claude Code が自動でロードします（`/スキル名` で明示呼び出しも可）。

| スキル | 発火する作業 |
|---|---|
| `analysis-pipeline` | ETL・4層分析エンジン・コストファネルの実装/変更 |
| `agent-orchestration` | 5フェーズ自律ループ・read-onlyツール・HITLゲートの実装 |
| `rule-authoring` | ルール/不正シナリオの追加・調整（`config/rules/`） |
| `data-contracts` | モジュール間I/O・レポート・監査ログのスキーマ準拠 |
| `security-and-privacy` | 外部証憑の取込・コネクタ入力・従業員個人データの取扱い |
| `governance-independence` | 独立性・配備レイヤ・モデルリスク・規制対応・監査ログ |
| `onboarding-explainer` | 非技術者への説明・オンボーディング・用語の平易な解説 |

## 対話スタイル ── 読んでいるだけで技術が身につく

このプロジェクトの Claude は、相手が技術に不慣れでも理解が深まるように話すよう設定してあります（`CLAUDE.md` の「対話と説明のしかた」＋ `onboarding-explainer` スキル）。専門用語は初出でやさしく言い換え、抽象概念は日常のたとえを添えてから正確な定義に進み、「なぜここで必要か」を短く付けます。わかりやすさは正確さへの"足し算"で、厳密さは削りません。

## Claude Code での使い方

1. このフォルダをリポジトリのルートに置く。
2. Claude Code を起動する（`CLAUDE.md` は自動で読まれます）。
3. 実装させたい層を指定する。関連スキルが自動でロードされます。例:
   - 「`config/rules/rule_catalog.yaml` を使ってルールベース評価器を実装して」→ `rule-authoring` / `analysis-pipeline`
   - 「`docs/agent-design.md` の5フェーズループをオーケストレータとして実装して」→ `agent-orchestration`
   - 「この仕組みを非エンジニアの監査チームに説明する資料を作って」→ `onboarding-explainer`
4. `docs/` が仕様、`config/` が機械可読の定義。**実装は `src/expense_risk/` にある**（下記「実装」参照）。
5. **定石**: 不確実な設計や複数ファイルにまたがる変更は plan mode で計画してからコードへ。変更後はテスト/スキーマ検証で確認。無関係なタスクに移るときは `/clear`。

## ファイル一覧

| パス | 役割 |
|---|---|
| `CLAUDE.md` | 常に効く原則・進め方・対話スタイル。**最初に読まれる** |
| `.claude/settings.json` | 権限スコープの雛形（秘密情報の読取は既定で拒否） |
| `.claude/skills/` | タスク別スキル7本 |
| `docs/architecture.md` | システム全体像、4層エンジン、データフロー、コストファネル設計 |
| `docs/agent-design.md` | 自律ループ、ツール定義、HITLゲート、終了条件 |
| `docs/governance.md` | 独立性（Track A/B＋配備レイヤ）、モデルリスク管理、規制マッピング、監査ログ |
| `docs/security-privacy.md` | エージェント安全性、プロンプトインジェクション対策、従業員プライバシーの法的論点 |
| `docs/spec-improvements.md` | スライドからの改善提案（課題→改善仕様→効果）。**仕様見直しの本体** |
| `config/rules/rule_catalog.yaml` | 62 ルール × 10 カテゴリ。スコア重み・必要データ・誤検知注記つき |
| `config/rules/fraud_scenarios.yaml` | 費目別 不正シナリオ（手口・着眼点・検証データ・適用手法） |
| `config/schemas/data_contracts.json` | 入出力データ契約（JSON Schema） |

## スライドから補強した主要仕様（要約）

詳細は `docs/spec-improvements.md`。特に重要なのは以下の4点です。

1. **コスト・スケールのファネル明示** — 全件にLLMは流せない。ルール+MLで決定論的に全件選別 → 高リスク部分集合のみエージェントが深掘り。これが「全件網羅×現実的コスト」の前提。
2. **プロンプトインジェクションを脅威モデルに追加** — エージェントはメール・領収書OCRという信頼できない外部入力を読む。証憑コンテンツを指示として実行しないための隔離・検証を必須化。
3. **従業員プライバシーの法的基盤を前提条件化** — 予定表・メール・移動記録の解析は、利用目的特定・必要最小限・労使協議・保存期間・本人通知を満たさないと運用できない。「read-only/同意/監査ログ」だけでは不十分。
4. **誤検知（アラート疲れ）管理とモデルリスク管理** — トリアージ階層・スコア較正・確定事案からの学習ループ、およびMLモデルのバリデーション/ドリフト監視/再検証を仕様化。

加えて、独立性の用途切替（Track A/B）と配備レイヤ（3線→2線→1線）、規制フレームワーク（J-SOX・不正リスク対応基準・監査基準報告書240・COSO・ACFE）へのマッピング、データ品質・完全性照合、合成不正によるテスト戦略、他ドメイン（仕訳・購買・給与）への拡張性を補強しています。

---

## 実装

仕様（`docs/`・`config/`）に基づく参照実装が `src/expense_risk/` にあります。Python 3.11+。

### クイックスタート

```bash
pip install -r requirements.txt

# 合成不正データで一気通貫デモ（検出力 Recall/Precision を表示）
python run.py demo --out out/

# 実データ(JSON)を分析しレポート生成
python run.py run --input samples/synthetic/expense_lines.json \
                  --masters samples/synthetic/masters.json --out out/

# テスト
pytest
```

生成物: `out/all_findings.csv`（全件スコア）、`out/alerts.xlsx`（アラート＋サマリ）、
`out/summary.json`、`out/summary.md`（エグゼクティブサマリ）、
`out/audit_log.jsonl`（WORM＋ハッシュチェーンの監査ログ）。

### モジュール構成（docs のレイヤに対応）

| モジュール | 役割 | 対応ドキュメント |
|---|---|---|
| `contracts.py` | 4つのデータ契約＋JSON Schema検証 | `data-contracts` / `data_contracts.json` |
| `config.py` | ルール/シナリオ/エンゲージメント構成のロード | `rule-authoring` |
| `etl.py` | L1 取込・データ品質ゲート・完全性照合 | architecture §4 |
| `features.py` | L2 特徴量・申請者行動プロファイル | architecture §1 |
| `rules/` | L3① ルール評価（決定論20＋統計7を実装、他は透明に未実装明示） | `analysis-pipeline` |
| `ml/anomaly.py` | L3③ Isolation Forest / PCA / LOF ＋ 寄与要因 | architecture §1 |
| `scoring.py` | ルール+ML統合（逓減加算・上限100）・トリアージ・ファネル選別 | analysis-pipeline |
| `agent/` | L4 5フェーズ自律ループ・read-only コネクタ・注入検知・HITL | `agent-orchestration` / `security-and-privacy` |
| `audit/` | WORM＋ハッシュチェーン監査ログ | `governance-independence` |
| `governance.py` | Track A/B独立性ゲート・model_version・規制マップ | `governance-independence` |
| `report.py` | L5 CSV/Excel/JSON/Markdown レポート | architecture §2 |
| `synthetic.py` | 合成不正生成＋検出力評価（テスト戦略 §9） | spec-improvements §9 |
| `pipeline.py` | L1→L5 一気通貫のオーケストレーション | architecture §3 |

### 実装が守る絶対原則（テストで検証）

- **HITL**: すべての所見は `hitl_status = pending`。AIは `confirmed` にできない。
- **プロンプトインジェクション耐性**: 証憑テキストの注入/不可視文字を検知（CONS-006）。
  ツール選択は計画（シナリオ）のみが決め、証憑コンテンツから発火させない。
- **証跡保全**: 監査ログはWORM＋ハッシュチェーン。1件でも改ざんすると鎖が切れて検知。
- **read-only・最小権限・プライバシーゲート**: 機微コネクタ（予定表/メール/会議）は
  法的基盤＋G1承認が揃わないと技術的に起動しない（既定は無効）。
- **根拠なきスコア禁止**: 全所見に rationale（違反ルール/ML寄与/収集証憑）を付与しスキーマ強制。
- **決定論・再現性**: 同じ入力・構成なら同じスコア／同じ model_version。

> ⚠️ **本参照実装は完全に決定論的で、LLM を呼び出しません**。`docs/agent-design.md` は
> Phase 2（仮説生成）/4（検証）/5（統合）で LLM を用いる**目標アーキテクチャ**を記述して
> いますが、本実装では Phase 2＝設定（`fraud_scenarios.yaml`）からのシナリオ選択、
> Phase 4＝収集証憑の決定論的照合、Phase 5＝定型文の推奨、で置き換えています
> （LLM 統合はプロダクション拡張の差込口）。`AgentConfig.cost_budget` は LLM トークンでは
> なく**ツール呼び出し回数**の上限です。コスト・ファネル（ルール+ML で全件選別 → 高リスク
> 部分集合のみ深掘り）は実装済みで、将来 LLM を差し込む際のコスト前提として機能します。
>
> ⚠️ 証憑コネクタ（`agent/connectors.py`）は決定論的な**モック**です。実データ接続時も同じ
> インタフェースとゲートを保つ差込口として実装しています。PDF/PPTX 出力は将来拡張
> （CSV/XLSX/JSON/Markdown は生成可能）。検知ルールは決定論20＋統計7を実装済みで、
> 残りの決定論/統計ルールと agent_verified/ml_assisted の対応は `summary.md` の
> 「ルールカバレッジ」に**透明に明示**されます（暗黙の取りこぼしを作らない）。
