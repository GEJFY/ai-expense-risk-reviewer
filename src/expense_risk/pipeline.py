"""エンドツーエンド・パイプライン（L1→L5 のオーケストレーション）.

全件データを決定論的に選別（ルール+ML）し、高リスク部分集合だけをエージェントが
深掘りする「コスト・ファネル」を実装の骨格とする（analysis-pipeline スキル）。

    全件 → [L1 ETL] → [L2 特徴量] → [L3 ルール+ML] → [スコア/選別]
         → 高リスクのみ [L4 エージェント検証] → [RiskFinding 統合] → HITL

すべての ``RiskFinding`` は data_contracts に適合し、根拠（rationale）を必ず持つ。
AI は ``hitl_status`` を confirmed にできない（既定 pending のまま人間へ提示）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .agent import AgentConfig, AgentOrchestrator
from .agent.connectors import ConnectorRegistry
from .audit import AuditLog
from .config import (
    FraudScenarios,
    RuleCatalog,
    load_engagement_config,
    load_fraud_scenarios,
    load_rule_catalog,
)
from .contracts import (
    Evidence,
    MatchedRule,
    Rationale,
    RiskFinding,
    assert_valid,
)
from .etl import ingest
from .features import compute_features
from .governance import Governance, build_governance
from .ml import detect_anomalies
from .rules import RuleEngine
from .rules.engine import RuleHit
from .scoring import ScoredLine, combine_score, score_lines
from .util import now_iso


@dataclass
class PipelineConfig:
    ml_method: str = "isolation_forest"
    ml_weight: float = 0.5
    enable_agent: bool = True
    allow_sensitive_approved: bool = False  # 機微コネクタの G1 事前承認済みか
    agent_config: AgentConfig = field(default_factory=AgentConfig)
    rule_params: Optional[dict[str, float]] = None
    # ファネルの深掘り対象。既定は escalate のみ（コスト最小・spec忠実）。
    # review まで広げると agent 検証の網羅性が上がる（コスト増）。
    agent_triage_levels: tuple[str, ...] = ("escalate",)


@dataclass
class PipelineResult:
    findings: list[RiskFinding]
    scored: list[ScoredLine]
    lines: list[Any]  # list[ExpenseLine]（レポートの費目・金額集計に使う）
    evidence: list[Evidence]
    reconciliation: dict[str, Any]
    rule_coverage: dict[str, Any]
    anomaly_skipped: bool
    governance: Governance
    audit: AuditLog
    stats: dict[str, Any]

    def high_risk(self) -> list[RiskFinding]:
        return [f for f in self.findings if f.triage == "escalate"]


def _recompute(scored: ScoredLine, catalog: RuleCatalog, ml_weight: float) -> None:
    """エージェント検証で追加された該当ルールを反映してスコア等を再計算する。"""
    from .scoring import _severity, _triage  # 同一モジュール内ヘルパ

    scored.risk_score = combine_score(scored.rule_hits, scored.ml_score, ml_weight)
    scored.severity = _severity(scored.risk_score, scored.rule_hits, catalog)
    scored.triage = _triage(scored.risk_score, scored.rule_hits, catalog)


def _matched_rules(hits: list[RuleHit]) -> list[MatchedRule]:
    """rule_hits を rationale.matched_rules へ（rule_id で重複排除）。"""
    seen: dict[str, MatchedRule] = {}
    for h in hits:
        if h.rule_id not in seen:
            seen[h.rule_id] = MatchedRule(rule_id=h.rule_id, weight=h.weight, detail_ja=h.detail_ja)
    return list(seen.values())


def run_pipeline(
    records: list[dict[str, Any]],
    *,
    masters: Optional[dict[str, Any]] = None,
    control_totals: Optional[dict[str, Any]] = None,
    engagement_config: Optional[dict[str, Any]] = None,
    catalog: Optional[RuleCatalog] = None,
    scenarios: Optional[FraudScenarios] = None,
    config: Optional[PipelineConfig] = None,
    audit_path: Optional[str] = None,
    clock: Optional[datetime] = None,
) -> PipelineResult:
    config = config or PipelineConfig()
    catalog = catalog or load_rule_catalog()
    scenarios = scenarios or load_fraud_scenarios()
    engagement_config = engagement_config if engagement_config is not None else load_engagement_config()

    # ガバナンス: 独立性ゲート（track_b 未充足なら起動をブロック）
    governance = build_governance(
        catalog, engagement_config, ml_method=config.ml_method, ml_weight=config.ml_weight
    )
    governance.enforce_gate()

    audit = AuditLog(path=audit_path, model_version=governance.model_version)
    audit.append("observe", "agent", "分析実行を開始（スコープ承認済み前提）", clock=clock,
                 inputs={"records": len(records), "engagement_mode": governance.engagement_mode})

    # L1 ETL
    ingested = ingest(records, masters=masters, control_totals=control_totals)
    audit.append("observe", "agent", "取込・完全性照合", clock=clock,
                 outputs={"reconciliation": ingested.reconciliation.to_dict()})

    # L2 特徴量
    features = compute_features(ingested.lines)

    # L3 ルール + ML（全件・決定論）
    rule_result = RuleEngine(catalog).evaluate(ingested.lines, features, masters=masters, params=config.rule_params)
    anomaly = detect_anomalies(features, method=config.ml_method)

    # スコア・選別（ファネル）
    scored = score_lines(rule_result, anomaly, catalog,
                         data_quality=ingested.data_quality, ml_weight=config.ml_weight)
    scored_by_id = {s.expense_line_id: s for s in scored}
    lines_by_id = {ln.expense_line_id: ln for ln in ingested.lines}

    selected = [s for s in scored if s.triage in config.agent_triage_levels]
    audit.append("observe", "agent", "高リスク部分集合を選別（ファネル）", clock=clock,
                 outputs={"total": len(scored), "selected_for_agent": len(selected),
                          "triage_levels": list(config.agent_triage_levels)})

    # L4 エージェント検証（高リスクのみ）
    all_evidence: list[Evidence] = []
    if config.enable_agent and selected:
        registry = ConnectorRegistry(
            engagement_config,
            allow_sensitive_approved=config.allow_sensitive_approved,
            clock=clock,
        )
        orch = AgentOrchestrator(catalog, scenarios, registry, audit=audit,
                                 config=config.agent_config, clock=clock)
        for s in selected:
            line = lines_by_id[s.expense_line_id]
            finding_id = f"FIND-{s.expense_line_id}"
            outcome = orch.investigate(line, [h.rule_id for h in s.rule_hits], finding_id)
            if outcome.extra_rule_hits:
                s.rule_hits = s.rule_hits + outcome.extra_rule_hits
                _recompute(s, catalog, config.ml_weight)
            s._agent_outcome = outcome  # type: ignore[attr-defined]
            all_evidence.extend(outcome.evidence)

    # L5 RiskFinding 統合（全件）
    findings: list[RiskFinding] = []
    generated_at = now_iso(clock)
    for s in scored:
        outcome = getattr(s, "_agent_outcome", None)
        rationale = Rationale(
            matched_rules=_matched_rules(s.rule_hits),
            ml_attribution=s.ml_attribution,
            evidence_refs=list(outcome.evidence_refs) if outcome else [],
        )
        recommended = governance.finalize_recommendation(outcome.recommended_action_ja) if outcome else None
        finding = RiskFinding(
            finding_id=f"FIND-{s.expense_line_id}",
            expense_line_id=s.expense_line_id,
            risk_score=s.risk_score,
            severity=s.severity,
            triage=s.triage,
            rationale=rationale,
            model_version=governance.model_version,
            generated_at=generated_at,
            hypotheses=list(outcome.hypotheses) if outcome else [],
            recommended_action_ja=recommended,
            data_quality=s.data_quality or None,
            hitl_status="pending",  # AI は confirmed にできない
            engagement_mode=governance.engagement_mode,
        )
        assert_valid(finding.to_dict(), "RiskFinding")  # 出力スキーマ強制
        findings.append(finding)

    stats = _build_stats(scored, findings, rule_result.coverage, anomaly.skipped)
    audit.append("integrate", "agent", "全所見を統合しHITLへ提示", clock=clock,
                 outputs={"findings": len(findings), "stats_by_triage": stats["by_triage"]})

    return PipelineResult(
        findings=findings,
        scored=scored,
        lines=ingested.lines,
        evidence=all_evidence,
        reconciliation=ingested.reconciliation.to_dict(),
        rule_coverage=rule_result.coverage,
        anomaly_skipped=anomaly.skipped,
        governance=governance,
        audit=audit,
        stats=stats,
    )


def _build_stats(scored, findings, coverage, anomaly_skipped) -> dict[str, Any]:
    by_sev: dict[str, int] = {}
    by_triage: dict[str, int] = {}
    for s in scored:
        by_sev[s.severity] = by_sev.get(s.severity, 0) + 1
        by_triage[s.triage] = by_triage.get(s.triage, 0) + 1
    return {
        "total_lines": len(scored),
        "by_severity": by_sev,
        "by_triage": by_triage,
        "selected_for_agent": sum(1 for s in scored if s.triage == "escalate"),
        "anomaly_skipped": anomaly_skipped,
        "rule_coverage": coverage,
    }
