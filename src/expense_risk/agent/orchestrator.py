"""自律監査ループ（観察→仮説生成→探索→検証→統合）.

高リスク部分集合（ファネルで選別済み）の各明細について、エージェントが5フェーズを
反復する（docs/agent-design.md §1）。本モジュールが守る不変条件:

- **read-only**: 証憑収集は ConnectorRegistry 経由のみ（書き込み手段を持たない）。
- **指示とデータの分離 / 証憑からのツール発火禁止**: 次に呼ぶツールは *シナリオの計画*
  （fraud_scenarios の connectors）だけが決める。証憑コンテンツはツール選択に影響しない。
- **出力検証**: 結論は根拠（違反ルール / ML寄与 / 収集証憑ID）が揃ってはじめて採用。
- **HITL**: エージェントは提示まで。``hitl_status`` を confirmed にできない（pipeline も同様）。
- **終了条件**: max_iterations / confidence_threshold / evidence_exhausted / cost_budget /
  rate_limit のいずれかで必ず停止する（暴走・コスト暴走の防止）。
- **監査ログ**: 各フェーズ・各ツール呼び出しを AuditLog に記録し後日再現可能にする。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ..audit import AuditLog
from ..config import FraudScenarios, RuleCatalog
from ..contracts import Evidence, ExpenseLine, Hypothesis
from ..rules.engine import RuleHit
from .connectors import NO_DATA, OK, ConnectorRegistry

# 会食・会議系の費目（実在性検証の対象）
_MEETING_CATEGORIES = {"交際費", "接待", "会議費"}
_AMOUNT_TOLERANCE = 1.0  # 領収書額と申請額の許容差（円）
_ROUTE_MARKUP = 1.2      # 妥当運賃に対する水増し判定倍率


@dataclass
class AgentConfig:
    max_iterations: int = 3     # 1明細あたりの探索→検証の反復上限
    cost_budget: int = 8        # 1明細あたりのツール呼び出し上限（コスト予算）
    max_hypotheses: int = 4     # 1明細あたりの検証仮説の上限


@dataclass
class AgentOutcome:
    expense_line_id: str
    finding_id: str
    hypotheses: list[Hypothesis] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    extra_rule_hits: list[RuleHit] = field(default_factory=list)  # 検証で新たに判明した該当ルール
    injection_detected: bool = False
    termination_reason: str = "evidence_exhausted"
    missing_evidence: list[str] = field(default_factory=list)     # 未取得の証憑（所見に明記）
    recommended_action_ja: Optional[str] = None


class AgentOrchestrator:
    def __init__(
        self,
        catalog: RuleCatalog,
        scenarios: FraudScenarios,
        registry: ConnectorRegistry,
        audit: Optional[AuditLog] = None,
        config: Optional[AgentConfig] = None,
        clock: Optional[datetime] = None,
    ) -> None:
        self.catalog = catalog
        self.scenarios = scenarios
        self.registry = registry
        self.audit = audit
        self.config = config or AgentConfig()
        self.clock = clock

    # --- 監査ログの薄いラッパ ---
    def _log(self, phase: str, actor: str, action: str, **kw: Any) -> None:
        if self.audit is not None:
            self.audit.append(phase, actor, action, clock=self.clock, **kw)

    def _rule_hit(self, rule_id: str, detail_ja: str) -> Optional[RuleHit]:
        rule = self.catalog.get(rule_id)
        if rule is None:
            return None
        return RuleHit(
            rule_id=rule_id,
            weight=float(rule.get("base_weight", 0)),
            severity=rule.get("severity", "low"),
            hitl=rule.get("hitl", "review"),
            category=rule.get("category", ""),
            detail_ja=detail_ja,
        )

    # --- メイン: 1明細の5フェーズ ---
    def investigate(self, line: ExpenseLine, matched_rule_ids: list[str], finding_id: str) -> AgentOutcome:
        outcome = AgentOutcome(expense_line_id=line.expense_line_id, finding_id=finding_id)

        # PHASE 1 観察
        self._log("observe", "agent", "高リスク明細を観察",
                  finding_id=finding_id, expense_line_id=line.expense_line_id,
                  inputs={"matched_rules": matched_rule_ids, "category": line.expense_category})

        # PHASE 2 仮説生成（費目別シナリオ由来。計画＝呼ぶべきコネクタもここで決まる）
        scenarios = self._candidate_scenarios(line, matched_rule_ids)
        planned_tools: list[str] = []
        for sc in scenarios:
            hypo = Hypothesis(scenario_id=sc["id"], hypothesis_ja=sc.get("hypothesis_ja", ""), verdict="inconclusive")
            outcome.hypotheses.append(hypo)
            for tool in sc.get("connectors", []) or []:
                if tool not in planned_tools:
                    planned_tools.append(tool)
        self._log("hypothesize", "agent", "費目別シナリオから仮説を生成",
                  finding_id=finding_id, expense_line_id=line.expense_line_id,
                  outputs={"scenarios": [s["id"] for s in scenarios], "planned_tools": planned_tools})

        # PHASE 3 探索（計画されたツールのみ・コスト/レート/ゲートを厳守）
        collected: dict[str, Evidence] = {}
        calls_made = 0
        terminated_early: Optional[str] = None
        for tool in planned_tools:
            if calls_made >= self.config.cost_budget:
                terminated_early = "cost_budget"
                break
            result = self.registry.call(tool, line)
            calls_made += 1
            if result.status == OK and result.evidence is not None:
                collected[result.evidence.type] = result.evidence
                outcome.evidence.append(result.evidence)
                outcome.evidence_refs.append(result.evidence.evidence_id)
                self._log("explore", f"tool:{tool}", "証憑を取得",
                          finding_id=finding_id, expense_line_id=line.expense_line_id,
                          outputs={"evidence_id": result.evidence.evidence_id,
                                   "injection_flags": result.evidence.injection_flags})
            else:
                if result.status in (NO_DATA,) or result.status.startswith("blocked") or result.status in ("requires_approval", "rate_limited"):
                    outcome.missing_evidence.append(f"{tool}:{result.status}")
                self._log("explore", f"tool:{tool}", "証憑取得できず",
                          finding_id=finding_id, expense_line_id=line.expense_line_id,
                          outputs={"status": result.status})

        # PHASE 4 検証（証憑と申請の整合を照合。証憑内容はデータとしてのみ扱う）
        agent_hits, refute_signals, injection = self._verify(line, collected)
        outcome.extra_rule_hits.extend(agent_hits)
        outcome.injection_detected = injection
        hit_rule_ids = {h.rule_id for h in agent_hits}
        for hypo in outcome.hypotheses:
            sc = self.scenarios.get(hypo.scenario_id) or {}
            linked = set(sc.get("linked_rules", []) or [])
            if hit_rule_ids & linked:
                hypo.verdict = "supported"
            elif refute_signals and (set(sc.get("connectors", []) or []) and not (hit_rule_ids & linked)):
                hypo.verdict = "refuted" if self._scenario_refuted(sc, collected) else "inconclusive"
            else:
                hypo.verdict = "inconclusive"
        self._log("verify", "agent", "証憑と仮説を照合し矛盾を判定",
                  finding_id=finding_id, expense_line_id=line.expense_line_id,
                  outputs={"agent_rule_hits": sorted(hit_rule_ids),
                           "verdicts": {h.scenario_id: h.verdict for h in outcome.hypotheses},
                           "injection_detected": injection})

        # 終了理由の決定
        if terminated_early:
            outcome.termination_reason = terminated_early
        elif injection or any(h.verdict == "supported" for h in outcome.hypotheses):
            outcome.termination_reason = "confidence_threshold"
        else:
            outcome.termination_reason = "evidence_exhausted"

        # PHASE 5 統合（助言表現の推奨アクション。確定は人間）
        outcome.recommended_action_ja = self._recommend(outcome)
        self._log("integrate", "agent", "所見に統合（HITLへ提示）",
                  finding_id=finding_id, expense_line_id=line.expense_line_id,
                  outputs={"evidence_refs": outcome.evidence_refs,
                           "missing_evidence": outcome.missing_evidence},
                  termination_reason=outcome.termination_reason)
        return outcome

    # --- 仮説生成: マッチしたルール／費目からシナリオを選ぶ ---
    def _candidate_scenarios(self, line: ExpenseLine, matched_rule_ids: list[str]) -> list[dict[str, Any]]:
        picked: dict[str, dict[str, Any]] = {}
        for rid in matched_rule_ids:
            for sc in self.scenarios.for_rule(rid):
                picked[sc["id"]] = sc
        for sc in self.scenarios.for_category(line.expense_category or ""):
            picked.setdefault(sc["id"], sc)
        # 安定した順序で上限まで
        ordered = sorted(picked.values(), key=lambda s: s["id"])
        return ordered[: self.config.max_hypotheses]

    # --- 検証: 証憑→エージェント該当ルール・反証シグナル・注入検出 ---
    def _verify(self, line: ExpenseLine, collected: dict[str, Evidence]) -> tuple[list[RuleHit], bool, bool]:
        hits: list[RuleHit] = []
        refute = False
        injection = False

        # 注入検出（最優先。検出自体が隠蔽の疑い ── CONS-006）
        for ev in collected.values():
            if ev.injection_flags:
                injection = True
                h = self._rule_hit("CONS-006", f"証憑 {ev.evidence_id} に注入/不可視テキストを検出: {ev.injection_flags}")
                if h:
                    hits.append(h)
                break

        cat = line.expense_category or ""

        cal = collected.get("calendar_event")
        if cal is not None:
            if cal.content.get("has_event") is False and cat in _MEETING_CATEGORIES:
                h = self._rule_hit("BEHV-002", "予定表に該当日時の会食/会議予定が存在しない")
                if h:
                    hits.append(h)
            elif cal.content.get("has_event") is True:
                refute = True

        meet = collected.get("meeting_attendees")
        if meet is not None and line.participants:
            attendees = meet.content.get("attendees")
            if isinstance(attendees, list) and set(map(str, attendees)) != set(map(str, line.participants)):
                h = self._rule_hit("PART-005", "申請参加者と会議出席者が不一致")
                if h:
                    hits.append(h)

        ocr = collected.get("receipt_ocr")
        if ocr is not None:
            ocr_amt = ocr.content.get("amount")
            if isinstance(ocr_amt, (int, float)) and abs(float(ocr_amt) - float(line.amount)) > _AMOUNT_TOLERANCE:
                h = self._rule_hit("CONS-001", f"領収書額 {ocr_amt:,.0f} が申請額 {float(line.amount):,.0f} と不一致")
                if h:
                    hits.append(h)
            elif isinstance(ocr_amt, (int, float)):
                refute = True

        sanc = collected.get("sanctions_match")
        if sanc is not None:
            if sanc.content.get("match") is True:
                h = self._rule_hit("VEND-001", f"取引先が反社/制裁/公開情報に該当: {sanc.content.get('list', '')}")
                if h:
                    hits.append(h)
            elif sanc.content.get("match") is False:
                refute = True

        geo = collected.get("geo_resolution")
        if geo is not None and geo.content.get("exists") is False:
            h = self._rule_hit("VEND-007", "地図/公開情報で取引先の実在を確認できない")
            if h:
                hits.append(h)

        route = collected.get("route_estimate")
        if route is not None:
            fair = route.content.get("fair_amount")
            if isinstance(fair, (int, float)) and fair > 0 and float(line.amount) > fair * _ROUTE_MARKUP:
                h = self._rule_hit("AMT-004", f"妥当運賃 {fair:,.0f} に対し申請 {float(line.amount):,.0f}（経路水増し疑い）")
                if h:
                    hits.append(h)

        return hits, refute, injection

    def _scenario_refuted(self, scenario: dict[str, Any], collected: dict[str, Evidence]) -> bool:
        """シナリオの証憑が明確に「問題なし」を示すか（実在性が確認できた等）。

        注: 領収書額と申請額の一致による反証は _verify で判定済み（refute シグナル）。
        ここでは明細に依存しない証憑（予定表・制裁照合）だけで判断する。
        """
        cal = collected.get("calendar_event")
        if cal is not None and cal.content.get("has_event") is True:
            return True
        sanc = collected.get("sanctions_match")
        if sanc is not None and sanc.content.get("match") is False:
            return True
        return False

    def _recommend(self, outcome: AgentOutcome) -> str:
        if outcome.injection_detected:
            return "証憑に検知回避の細工の疑い。原本確認と申請者ヒアリングを監査人が実施することを推奨。"
        supported = [h for h in outcome.hypotheses if h.verdict == "supported"]
        if supported:
            return "証憑と申請の矛盾を検出。監査人による事実確認・裏付け証憑の追加取得を推奨。"
        if outcome.missing_evidence:
            return "実在性を裏付ける証憑が未取得（該当コネクタ無効/要承認）。人手による確認を推奨。"
        return "自動検証では明確な矛盾なし。優先度に応じ監査人がサンプル確認することを推奨。"
