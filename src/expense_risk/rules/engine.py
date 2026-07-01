"""ルールエンジン: カタログのルールを明細に適用し、該当（RuleHit）を集約する.

- ``detection.type`` が ``deterministic`` / ``statistical`` のルールをここで評価する。
- ``agent_verified`` は L4 エージェント層、``ml_assisted`` は ML 層で扱う（本エンジン対象外）。
- カタログにあるが本エンジンに評価器が無いルールは ``coverage`` に「未実装」として
  明示する（暗黙の取りこぼしを作らない ── docs/spec-improvements の姿勢）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import RuleCatalog, load_rule_catalog
from ..contracts import ExpenseLine
from ..features import FeatureSet
from .evaluators import (
    DETERMINISTIC_EVALUATORS,
    STATISTICAL_EVALUATORS,
    RuleContext,
)


@dataclass
class RuleHit:
    rule_id: str
    weight: float
    severity: str
    hitl: str
    category: str
    detail_ja: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "weight": self.weight,
            "severity": self.severity,
            "hitl": self.hitl,
            "category": self.category,
            "detail_ja": self.detail_ja,
        }


@dataclass
class RuleEvalResult:
    hits_by_line: dict[str, list[RuleHit]]
    coverage: dict[str, Any] = field(default_factory=dict)

    def hits_for(self, expense_line_id: str) -> list[RuleHit]:
        return self.hits_by_line.get(expense_line_id, [])


class RuleEngine:
    def __init__(self, catalog: Optional[RuleCatalog] = None) -> None:
        self.catalog = catalog or load_rule_catalog()

    def _make_hit(self, rule_id: str, detail_ja: str) -> Optional[RuleHit]:
        rule = self.catalog.get(rule_id)
        if rule is None:
            return None  # カタログに無いIDの評価器は無視（防御的）
        return RuleHit(
            rule_id=rule_id,
            weight=float(rule.get("base_weight", 0)),
            severity=rule.get("severity", "low"),
            hitl=rule.get("hitl", "review"),
            category=rule.get("category", ""),
            detail_ja=detail_ja,
        )

    def evaluate(
        self,
        lines: list[ExpenseLine],
        features: FeatureSet,
        masters: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, float]] = None,
    ) -> RuleEvalResult:
        ctx = RuleContext.build(lines, features, masters, params)
        hits_by_line: dict[str, list[RuleHit]] = {ln.expense_line_id: [] for ln in lines}

        # --- 決定論的（明細ごと） ---
        for rule_id, fn in DETERMINISTIC_EVALUATORS.items():
            if self.catalog.get(rule_id) is None:
                continue
            for ln in lines:
                detail = fn(ln, ctx)
                if detail:
                    hit = self._make_hit(rule_id, detail)
                    if hit:
                        hits_by_line[ln.expense_line_id].append(hit)

        # --- 統計的（バッチ） ---
        for rule_id, fn in STATISTICAL_EVALUATORS.items():
            if self.catalog.get(rule_id) is None:
                continue
            for lid, detail in fn(ctx).items():
                if lid in hits_by_line and detail:
                    hit = self._make_hit(rule_id, detail)
                    if hit:
                        hits_by_line[lid].append(hit)

        return RuleEvalResult(hits_by_line=hits_by_line, coverage=self._coverage())

    def _coverage(self) -> dict[str, Any]:
        implemented = set(DETERMINISTIC_EVALUATORS) | set(STATISTICAL_EVALUATORS)
        det_stat_ids = {
            rid
            for rid, r in self.catalog.rules.items()
            if r.get("detection", {}).get("type") in ("deterministic", "statistical")
        }
        agent_ids = sorted(
            rid for rid, r in self.catalog.rules.items()
            if r.get("detection", {}).get("type") == "agent_verified"
        )
        ml_ids = sorted(
            rid for rid, r in self.catalog.rules.items()
            if r.get("detection", {}).get("type") == "ml_assisted"
        )
        not_implemented = sorted(det_stat_ids - implemented)
        return {
            "total_rules": len(self.catalog.rules),
            "engine_implemented": sorted(implemented & set(self.catalog.rules)),
            "engine_not_implemented": not_implemented,  # 決定論/統計だが未実装（透明性のため明示）
            "agent_verified_rules": agent_ids,          # L4 で扱う
            "ml_assisted_rules": ml_ids,                # ML 層で扱う
        }
