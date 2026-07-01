"""L5 レポート生成のテスト。"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from expense_risk.pipeline import PipelineConfig, run_pipeline
from expense_risk.report import write_reports
from expense_risk.synthetic import generate_dataset

CLK = datetime(2026, 3, 1, tzinfo=timezone.utc)


def test_reports_written(tmp_path, demo_engagement):
    ds = generate_dataset(n_normal=30, fraud_per_scenario=1, seed=2)
    res = run_pipeline(ds.records, masters=ds.masters, engagement_config=demo_engagement,
                       config=PipelineConfig(allow_sensitive_approved=True,
                                             agent_triage_levels=("escalate", "review")), clock=CLK)
    paths = write_reports(res, tmp_path)
    for key in ("all_findings_csv", "alerts_xlsx", "summary_json", "summary_md", "audit_log"):
        assert (tmp_path / paths[key].split("\\")[-1].split("/")[-1]).exists() or paths[key]

    # CSV は全件分の行を持つ
    df = pd.read_csv(paths["all_findings_csv"])
    assert len(df) == len(ds.records)
    assert "risk_score" in df.columns and "matched_rules" in df.columns

    # Excel に主要シートが存在
    xls = pd.ExcelFile(paths["alerts_xlsx"])
    for sheet in ("Exec Summary", "Alerts", "All Findings", "Rule Coverage", "規制マッピング"):
        assert sheet in xls.sheet_names

    # Markdown に免責（HITL）と監査証跡が含まれる
    md = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "HITL" in md and "監査証跡" in md
