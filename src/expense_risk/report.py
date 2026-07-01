"""L5: レポート生成（CSV / Excel / JSON / Markdown）.

出力はすべて AI の分析結果に基づき、最終判断は HITL で監査人が行う（docs/architecture.md §2）。

- **all_findings.csv** — 全件リスクスコア一覧（追加分析用）。
- **alerts.xlsx** — アラート（review/escalate）＋サマリ＋カバレッジ＋完全性照合＋規制マップ。
- **summary.json** — 機械可読の統合サマリ（他システム連携・再現性）。
- **summary.md** — 経営層/監査人向けの平易なエグゼクティブサマリ。
- **audit_log.jsonl** — 改ざん不能な自律監査ログ（WORM＋ハッシュチェーン）。

PDF/PPTX は将来拡張（reportlab / python-pptx を差し込む想定）。ここでは依存を増やさず、
確実に生成できる形式に限定する（過大主張を避け、実際に動く成果物を出す）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from .config import RuleCatalog, load_rule_catalog
from .governance import REGULATORY_MAP
from .pipeline import PipelineResult

_DISCLAIMER = (
    "本レポートは AI による**リスクの提示**であり、確定・是正・通報・処分は監査人（人間）が"
    "判断します（HITL）。各所見には根拠（違反ルール／ML寄与／収集証憑）が付与されています。"
)


def _finding_row(finding: Any, line: Any) -> dict[str, Any]:
    return {
        "finding_id": finding.finding_id,
        "expense_line_id": finding.expense_line_id,
        "applicant_id": getattr(line, "applicant_id", None),
        "department": getattr(line, "department", None),
        "transaction_date": getattr(line, "transaction_date", None),
        "amount": getattr(line, "amount", None),
        "currency": getattr(line, "currency", None),
        "expense_category": getattr(line, "expense_category", None),
        "risk_score": finding.risk_score,
        "severity": finding.severity,
        "triage": finding.triage,
        "matched_rules": ",".join(m.rule_id for m in finding.rationale.matched_rules),
        "ml_score": round(finding.rationale.ml_attribution.anomaly_score, 4) if finding.rationale.ml_attribution else None,
        "hypotheses": ";".join(f"{h.scenario_id}:{h.verdict}" for h in finding.hypotheses),
        "evidence_refs": ";".join(finding.rationale.evidence_refs),
        "data_quality": ";".join(finding.data_quality or []),
        "hitl_status": finding.hitl_status,
        "recommended_action_ja": finding.recommended_action_ja,
        "model_version": finding.model_version,
    }


def _category_summary(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["expense_category", "件数", "平均スコア", "最大スコア", "escalate件数"])
    df = pd.DataFrame(rows)
    grp = df.groupby("expense_category").agg(
        件数=("risk_score", "size"),
        平均スコア=("risk_score", "mean"),
        最大スコア=("risk_score", "max"),
        escalate件数=("triage", lambda s: int((s == "escalate").sum())),
    ).reset_index()
    grp["平均スコア"] = grp["平均スコア"].round(1)
    return grp.sort_values("最大スコア", ascending=False)


def write_reports(
    result: PipelineResult,
    out_dir: str | Path,
    *,
    catalog: Optional[RuleCatalog] = None,
    top_n: int = 10,
) -> dict[str, str]:
    """パイプライン結果から一連のレポートを ``out_dir`` に生成し、パスを返す。"""
    catalog = catalog or load_rule_catalog()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    lines_by_id = {ln.expense_line_id: ln for ln in result.lines}

    rows = [_finding_row(f, lines_by_id.get(f.expense_line_id)) for f in result.findings]
    rows.sort(key=lambda r: r["risk_score"], reverse=True)
    df_all = pd.DataFrame(rows)
    paths: dict[str, str] = {}

    # --- CSV（全件） ---
    csv_path = out / "all_findings.csv"
    df_all.to_csv(csv_path, index=False, encoding="utf-8-sig")  # Excel 互換 BOM
    paths["all_findings_csv"] = str(csv_path)

    # --- Excel（アラート＋各種サマリ） ---
    xlsx_path = out / "alerts.xlsx"
    alerts = df_all[df_all["triage"].isin(["review", "escalate"])] if not df_all.empty else df_all
    cat_df = _category_summary(rows)
    cov = result.rule_coverage
    exec_rows = _exec_rows(result)
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as xw:
        pd.DataFrame(exec_rows).to_excel(xw, sheet_name="Exec Summary", index=False)
        (alerts if not alerts.empty else pd.DataFrame([{"info": "アラートなし"}])).to_excel(
            xw, sheet_name="Alerts", index=False)
        (df_all if not df_all.empty else pd.DataFrame([{"info": "所見なし"}])).to_excel(
            xw, sheet_name="All Findings", index=False)
        cat_df.to_excel(xw, sheet_name="費目別", index=False)
        _coverage_df(cov).to_excel(xw, sheet_name="Rule Coverage", index=False)
        pd.DataFrame([result.reconciliation]).to_excel(xw, sheet_name="完全性照合", index=False)
        pd.DataFrame(REGULATORY_MAP).to_excel(xw, sheet_name="規制マッピング", index=False)
    paths["alerts_xlsx"] = str(xlsx_path)

    # --- JSON（統合サマリ） ---
    chain_ok, chain_problems = result.audit.verify_chain()
    summary = {
        "generated_at": result.findings[0].generated_at if result.findings else None,
        "engagement_mode": result.governance.engagement_mode,
        "model_version": result.governance.model_version,
        "stats": result.stats,
        "reconciliation": result.reconciliation,
        "rule_coverage": cov,
        "regulatory_map": REGULATORY_MAP,
        "top_findings": [_finding_row(f, lines_by_id.get(f.expense_line_id)) for f in
                         sorted(result.findings, key=lambda f: f.risk_score, reverse=True)[:top_n]],
        "audit": {"entries": len(result.audit), "chain_ok": chain_ok, "problems": chain_problems},
        "disclaimer_ja": _DISCLAIMER,
    }
    json_path = out / "summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["summary_json"] = str(json_path)

    # --- Markdown（エグゼクティブサマリ） ---
    md_path = out / "summary.md"
    md_path.write_text(_markdown_summary(result, rows, cat_df, top_n), encoding="utf-8")
    paths["summary_md"] = str(md_path)

    # --- 監査ログ（JSONL） ---
    audit_path = out / "audit_log.jsonl"
    result.audit.write_jsonl(audit_path)
    paths["audit_log"] = str(audit_path)

    return paths


def _exec_rows(result: PipelineResult) -> list[dict[str, Any]]:
    st = result.stats
    return [
        {"指標": "総明細数", "値": st["total_lines"]},
        {"指標": "エージェント深掘り対象（escalate）", "値": st["selected_for_agent"]},
        {"指標": "人手レビュー（review）", "値": st["by_triage"].get("review", 0)},
        {"指標": "自動棄却（auto_dismiss）", "値": st["by_triage"].get("auto_dismiss", 0)},
        {"指標": "critical 件数", "値": st["by_severity"].get("critical", 0)},
        {"指標": "high 件数", "値": st["by_severity"].get("high", 0)},
        {"指標": "engagement_mode", "値": result.governance.engagement_mode},
        {"指標": "model_version", "値": result.governance.model_version},
        {"指標": "監査ログ件数", "値": len(result.audit)},
        {"指標": "監査ログ整合性", "値": "OK" if result.audit.verify_chain()[0] else "改ざん検知"},
    ]


def _coverage_df(cov: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame([
        {"区分": "ルール総数", "件数": cov.get("total_rules", 0), "詳細": ""},
        {"区分": "エンジン実装済み", "件数": len(cov.get("engine_implemented", [])),
         "詳細": ", ".join(cov.get("engine_implemented", []))},
        {"区分": "決定論/統計だが未実装", "件数": len(cov.get("engine_not_implemented", [])),
         "詳細": ", ".join(cov.get("engine_not_implemented", []))},
        {"区分": "agent_verified（L4で処理）", "件数": len(cov.get("agent_verified_rules", [])),
         "詳細": ", ".join(cov.get("agent_verified_rules", []))},
        {"区分": "ml_assisted（ML層で処理）", "件数": len(cov.get("ml_assisted_rules", [])),
         "詳細": ", ".join(cov.get("ml_assisted_rules", []))},
    ])


def _markdown_summary(result: PipelineResult, rows, cat_df, top_n) -> str:
    st = result.stats
    recon = result.reconciliation
    lines_by_id = {ln.expense_line_id: ln for ln in result.lines}
    chain_ok = result.audit.verify_chain()[0]

    parts: list[str] = []
    parts.append("# 経費不正リスク分析レポート（エグゼクティブサマリ）\n")
    parts.append(f"- 生成日時: {result.findings[0].generated_at if result.findings else '—'}")
    parts.append(f"- engagement_mode: `{result.governance.engagement_mode}` / model_version: `{result.governance.model_version}`")
    parts.append(f"\n> {_DISCLAIMER}\n")

    parts.append("## 全体サマリ\n")
    parts.append(f"- 総明細数: **{st['total_lines']}**")
    parts.append(f"- トリアージ: escalate **{st['by_triage'].get('escalate', 0)}** / review "
                 f"{st['by_triage'].get('review', 0)} / auto_dismiss {st['by_triage'].get('auto_dismiss', 0)}")
    parts.append(f"- 重大度: critical {st['by_severity'].get('critical', 0)} / high {st['by_severity'].get('high', 0)} "
                 f"/ medium {st['by_severity'].get('medium', 0)} / low {st['by_severity'].get('low', 0)}")

    parts.append("\n## データ完全性照合\n")
    cm, am = recon.get("count_matches"), recon.get("amount_matches")
    parts.append(f"- 取込件数: {recon['ingested_count']} / 管理値: {recon.get('control_count', '—')} "
                 f"→ {'一致' if cm else ('不一致' if cm is False else '未照合')}")
    parts.append(f"- 取込金額合計: {recon['ingested_amount_sum']:,} / 管理値: {recon.get('control_amount_sum', '—')} "
                 f"→ {'一致' if am else ('不一致' if am is False else '未照合')}")
    if recon.get("duplicate_line_ids"):
        parts.append(f"- ⚠️ 重複ID: {', '.join(recon['duplicate_line_ids'][:10])}")

    parts.append(f"\n## 注目リスク TOP {top_n}\n")
    parts.append("| finding | 明細 | 費目 | スコア | 重大度 | トリアージ | 主な根拠 |")
    parts.append("|---|---|---|---:|---|---|---|")
    for f in sorted(result.findings, key=lambda f: f.risk_score, reverse=True)[:top_n]:
        line = lines_by_id.get(f.expense_line_id)
        cat = getattr(line, "expense_category", "") or ""
        rules = ", ".join(m.rule_id for m in f.rationale.matched_rules[:4]) or "ML/その他"
        parts.append(f"| {f.finding_id} | {f.expense_line_id} | {cat} | {f.risk_score} | {f.severity} | {f.triage} | {rules} |")

    if not cat_df.empty:
        parts.append("\n## 費目別サマリ\n")
        parts.append("| 費目 | 件数 | 平均スコア | 最大スコア | escalate |")
        parts.append("|---|---:|---:|---:|---:|")
        for _, r in cat_df.iterrows():
            parts.append(f"| {r['expense_category']} | {int(r['件数'])} | {r['平均スコア']} | {r['最大スコア']} | {int(r['escalate件数'])} |")

    # データ品質フラグ
    dq_counts: dict[str, int] = {}
    for f in result.findings:
        for flag in (f.data_quality or []):
            dq_counts[flag] = dq_counts.get(flag, 0) + 1
    if dq_counts:
        parts.append("\n## データ品質フラグ\n")
        for flag, n in sorted(dq_counts.items(), key=lambda kv: -kv[1]):
            parts.append(f"- `{flag}`: {n} 件")

    cov = result.rule_coverage
    parts.append("\n## ルールカバレッジ（透明性）\n")
    parts.append(f"- ルール総数 {cov.get('total_rules', 0)} / エンジン実装済み {len(cov.get('engine_implemented', []))}")
    if cov.get("engine_not_implemented"):
        parts.append(f"- 決定論/統計だが**未実装**（今後拡張）: {', '.join(cov['engine_not_implemented'])}")
    parts.append(f"- agent_verified（L4検証で処理）: {len(cov.get('agent_verified_rules', []))} 件 / "
                 f"ml_assisted（ML層）: {len(cov.get('ml_assisted_rules', []))} 件")

    parts.append("\n## 規制・基準マッピング\n")
    parts.append("| フレームワーク | 関連 | 寄与 |")
    parts.append("|---|---|---|")
    for m in REGULATORY_MAP:
        parts.append(f"| {m['framework']} | {m['area']} | {m['contribution']} |")

    parts.append("\n## 監査証跡\n")
    parts.append(f"- 自律監査ログ: **{len(result.audit)}** 件、ハッシュチェーン整合性: "
                 f"**{'OK（改ざんなし）' if chain_ok else '⚠️ 改ざん検知'}**")
    parts.append("- 入力明細 → 適用ルール/モデル → 収集証憑 → スコア → 結論 → HITL の連鎖を追跡可能。\n")
    return "\n".join(parts)
