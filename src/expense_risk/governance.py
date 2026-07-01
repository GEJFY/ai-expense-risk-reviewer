"""ガバナンス（独立性の用途切替・モデルバージョン・規制マッピング）.

「誰が・どの立場で・どの規制下で使うか」を構成で明示的に切り替える（docs/governance.md §1）。

- **独立性（Track A/B）**: track_b（監査法人の外部商用化・法定監査利用）では、独立性
  チェックリストの充足を**起動条件**とし、是正提案を「助言」表現に制限する。要法務確認。
- **モデルバージョン**: ルール/モデル/閾値のバージョンを所見に紐付け再現性を担保する
  （モデルリスク管理・監査対応）。
- **規制マッピング**: J-SOX・不正リスク対応基準・監査基準報告書240・COSO・ACFE への対応。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .config import RuleCatalog
from .util import content_hash

TRACK_A = "track_a"
TRACK_B = "track_b"

# 規制・基準フレームワークへのマッピング（docs/governance.md §3）
REGULATORY_MAP: list[dict[str, str]] = [
    {"framework": "J-SOX（内部統制報告制度）", "area": "業務プロセス統制・ITGC",
     "contribution": "経費プロセスの統制逸脱・職務分掌違反（自己承認等）の全件検知"},
    {"framework": "監査基準報告書240", "area": "不正に関連した監査人の責任",
     "contribution": "不正シナリオに基づく仮説生成と証憑検証"},
    {"framework": "不正リスク対応基準", "area": "不正による重要な虚偽表示リスク",
     "contribution": "全件網羅による想定外リスクの発見"},
    {"framework": "COSO（内部統制・ERM）", "area": "統制活動・モニタリング",
     "contribution": "継続的モニタリングとアラート配信"},
    {"framework": "ACFE 不正分類", "area": "資産の不正流用（経費不正）",
     "contribution": "費目別の手口分類（fraud_scenarios.yaml）"},
]


class IndependenceError(RuntimeError):
    """track_b で独立性チェックリストが未充足のまま起動しようとした。"""


@dataclass
class Governance:
    engagement_mode: str
    model_version: str
    engagement_config: dict[str, Any] = field(default_factory=dict)

    @property
    def is_track_b(self) -> bool:
        return self.engagement_mode == TRACK_B

    def independence_issues(self) -> list[str]:
        """track_b で未充足の独立性チェック項目を返す（track_a では空）。"""
        if not self.is_track_b:
            return []
        checklist = self.engagement_config.get("independence_checklist", {}) or {}
        return sorted(k for k, v in checklist.items() if not v)

    def enforce_gate(self) -> None:
        """独立性ゲート。track_b で未充足なら実行をブロックする（起動条件）。"""
        issues = self.independence_issues()
        if issues:
            raise IndependenceError(
                "track_b の独立性チェックリストが未充足のため実行をブロックしました: "
                + ", ".join(issues)
                + "（要 法務・品質管理部門の確認）"
            )

    def finalize_recommendation(self, text: Optional[str]) -> Optional[str]:
        """track_b では是正提案を助言表現に制限し、意思決定主体を明示する。"""
        if text is None:
            return None
        if self.is_track_b:
            return text + "（※助言。是正・通報・処分の意思決定はクライアント／独立した立場が行う）"
        return text


def build_model_version(
    catalog: RuleCatalog,
    engagement_config: dict[str, Any],
    *,
    ml_method: str,
    ml_weight: float,
) -> str:
    """ルール/モデル/閾値/構成から再現性のあるモデルバージョン文字列を生成する。"""
    fingerprint = {
        "rule_catalog_version": catalog.version,
        "thresholds": catalog.thresholds,
        "ml_method": ml_method,
        "ml_weight": ml_weight,
        "engagement_mode": engagement_config.get("engagement_mode", TRACK_A),
    }
    return f"rc-{catalog.version}+cfg-{content_hash(fingerprint)[:8]}"


def build_governance(
    catalog: RuleCatalog,
    engagement_config: dict[str, Any],
    *,
    ml_method: str = "isolation_forest",
    ml_weight: float = 0.5,
) -> Governance:
    mode = engagement_config.get("engagement_mode", TRACK_A)
    if mode not in (TRACK_A, TRACK_B):
        raise ValueError(f"未知の engagement_mode: {mode}")
    version = build_model_version(catalog, engagement_config, ml_method=ml_method, ml_weight=ml_weight)
    return Governance(engagement_mode=mode, model_version=version, engagement_config=engagement_config)
