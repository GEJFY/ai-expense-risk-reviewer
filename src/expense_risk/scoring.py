"""リスクスコア統合とファネル選別（誤検知管理・トリアージの基盤）.

ルールスコアと ML 外れ値スコアを**重み付き統合**して最終 ``risk_score``(0-100) を出す
（rule_catalog.yaml の risk_scoring）。合成は単純合算ではなく「上限100で逓減加算」＝
確率的 OR（各シグナルは足すほど効きが逓減し、100を超えない）で行う。

トリアージ（自動棄却／人手レビュー／エスカレート）は ``thresholds`` に従い決める。
これがファネル ── ルール+ML で全件を決定論的に選別し、高リスク部分集合だけを
エージェント深掘りへ回す ── の選別ラインになる（analysis-pipeline スキル）。
最終判断は必ず HITL（人間）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .config import RuleCatalog
from .contracts import MlAttribution
from .ml.anomaly import AnomalyResult
from .rules.engine import RuleEvalResult, RuleHit

DEFAULT_ML_WEIGHT = 0.5  # ML 単独では最大 50 点（ルールを主・MLを従とする既定）


@dataclass
class ScoredLine:
    expense_line_id: str
    risk_score: float
    severity: str
    triage: str
    rule_hits: list[RuleHit] = field(default_factory=list)
    ml_attribution: Optional[MlAttribution] = None
    ml_score: float = 0.0
    data_quality: list[str] = field(default_factory=list)

    @property
    def selected_for_agent(self) -> bool:
        """ファネル選別: エスカレート＝エージェント深掘り対象。"""
        return self.triage == "escalate"


def _clamp(x: float, lo: float = 0.0, hi: float = 0.999) -> float:
    return max(lo, min(hi, x))


def combine_score(rule_hits: list[RuleHit], ml_score: float, ml_weight: float = DEFAULT_ML_WEIGHT) -> float:
    """ルール群 + ML を確率的 OR で統合（逓減加算・上限100）。"""
    prod = 1.0
    for h in rule_hits:
        prod *= 1.0 - _clamp(h.weight / 100.0)
    rule_p = 1.0 - prod
    combined = 1.0 - (1.0 - rule_p) * (1.0 - ml_weight * _clamp(ml_score, 0.0, 1.0))
    return round(100.0 * combined, 1)


def _severity(score: float, rule_hits: list[RuleHit], catalog: RuleCatalog) -> str:
    if any(h.severity == "critical" for h in rule_hits) or score >= 90:
        return "critical"
    if score >= catalog.high_threshold:
        return "high"
    if score >= catalog.medium_threshold:
        return "medium"
    return "low"


def _triage(score: float, rule_hits: list[RuleHit], catalog: RuleCatalog) -> str:
    # 個別ルールが escalate 指定なら、スコアに関わらずエスカレート（例: 反社/自己承認）
    if any(h.hitl == "escalate" for h in rule_hits) or score >= catalog.escalate_at_or_above:
        return "escalate"
    if score < catalog.auto_dismiss_below:
        return "auto_dismiss"
    return "review"


def score_lines(
    rule_result: RuleEvalResult,
    anomaly_result: AnomalyResult,
    catalog: RuleCatalog,
    *,
    data_quality: Optional[dict[str, list[str]]] = None,
    ml_weight: float = DEFAULT_ML_WEIGHT,
) -> list[ScoredLine]:
    """全明細のスコア・重大度・トリアージを算出する（全件・決定論）。"""
    data_quality = data_quality or {}
    scored: list[ScoredLine] = []
    line_ids = list(rule_result.hits_by_line.keys())
    for lid in line_ids:
        hits = rule_result.hits_for(lid)
        ml_score = anomaly_result.score_for(lid)
        score = combine_score(hits, ml_score, ml_weight)
        scored.append(
            ScoredLine(
                expense_line_id=lid,
                risk_score=score,
                severity=_severity(score, hits, catalog),
                triage=_triage(score, hits, catalog),
                rule_hits=hits,
                ml_attribution=anomaly_result.attribution_for(lid),
                ml_score=ml_score,
                data_quality=list(data_quality.get(lid, [])),
            )
        )
    scored.sort(key=lambda s: s.risk_score, reverse=True)
    return scored


def select_high_risk(scored: list[ScoredLine]) -> list[ScoredLine]:
    """エージェント深掘り対象（高リスク部分集合）を返す。"""
    return [s for s in scored if s.selected_for_agent]
