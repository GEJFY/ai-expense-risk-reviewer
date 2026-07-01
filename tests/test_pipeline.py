"""エンドツーエンド・パイプラインの不変条件テスト（絶対原則の検証）。"""

from __future__ import annotations

from datetime import datetime, timezone

from expense_risk.contracts import is_valid
from expense_risk.pipeline import PipelineConfig, run_pipeline
from expense_risk.synthetic import evaluate, generate_dataset

CLK = datetime(2026, 3, 1, tzinfo=timezone.utc)


def _dataset():
    ds = generate_dataset(n_normal=50, fraud_per_scenario=2, seed=5)
    return ds


def test_all_findings_schema_valid_and_hitl_pending(demo_engagement):
    ds = _dataset()
    res = run_pipeline(ds.records, masters=ds.masters, engagement_config=demo_engagement,
                       config=PipelineConfig(allow_sensitive_approved=True,
                                             agent_triage_levels=("escalate", "review")), clock=CLK)
    assert len(res.findings) == len(ds.records)
    for f in res.findings:
        assert is_valid(f.to_dict(), "RiskFinding")     # 出力スキーマ強制
        assert f.hitl_status == "pending"               # AI は confirmed にできない（HITL）
        # 根拠なきスコア禁止: rationale を必ず持つ
        assert "matched_rules" in f.to_dict()["rationale"]


def test_audit_chain_integrity(demo_engagement):
    ds = _dataset()
    res = run_pipeline(ds.records, masters=ds.masters, engagement_config=demo_engagement,
                       config=PipelineConfig(allow_sensitive_approved=True), clock=CLK)
    assert res.audit.verify_chain()[0] is True
    assert len(res.audit) > 0


def test_determinism_same_scores(demo_engagement):
    ds = _dataset()
    cfg = PipelineConfig(allow_sensitive_approved=True, agent_triage_levels=("escalate", "review"))
    r1 = run_pipeline(ds.records, masters=ds.masters, engagement_config=demo_engagement, config=cfg, clock=CLK)
    r2 = run_pipeline(ds.records, masters=ds.masters, engagement_config=demo_engagement, config=cfg, clock=CLK)
    s1 = {f.finding_id: f.risk_score for f in r1.findings}
    s2 = {f.finding_id: f.risk_score for f in r2.findings}
    assert s1 == s2  # 同じ入力 → 同じスコア（再現性）


def test_funnel_agent_only_on_selected(demo_engagement):
    ds = _dataset()
    # escalate のみを深掘り対象にすると、review だけの明細には証憑が付かない
    res = run_pipeline(ds.records, masters=ds.masters, engagement_config=demo_engagement,
                       config=PipelineConfig(allow_sensitive_approved=True,
                                             agent_triage_levels=("escalate",)), clock=CLK)
    for f in res.findings:
        if f.triage != "escalate":
            assert f.rationale.evidence_refs == []  # 深掘り対象外は証憑なし


def test_synthetic_recall_high(demo_engagement):
    ds = _dataset()
    res = run_pipeline(ds.records, masters=ds.masters, engagement_config=demo_engagement,
                       config=PipelineConfig(allow_sensitive_approved=True,
                                             agent_triage_levels=("escalate", "review")), clock=CLK)
    metrics = evaluate(res.findings, ds.labels)
    assert metrics["recall"] == 1.0          # 全注入不正を捕捉
    assert metrics["false_positive_rate"] <= 0.2


def test_data_quality_flag_propagates(demo_engagement):
    recs = [{"expense_line_id": "E1", "applicant_id": "A1", "transaction_date": "2026-01-05",
             "amount": -100, "currency": "JPY", "expense_category": "会議費"}]
    res = run_pipeline(recs, engagement_config=demo_engagement,
                       config=PipelineConfig(enable_agent=False), clock=CLK)
    f = res.findings[0]
    assert f.data_quality and "amount_non_positive" in f.data_quality
