"""機械可読の定義（ルール・シナリオ・エンゲージメント構成）の読み込み.

検知ロジックはコードに直書きせず、必ず ``config/rules/*.yaml`` に宣言的に定義する
（rule-authoring スキル）。ここではその YAML をロードして構造化する。実装はこれを
読んで評価するだけにする ── こうすると監査人がコードを触らずルールを保守できる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

import yaml

from .paths import (
    engagement_config_path,
    fraud_scenarios_path,
    rule_catalog_path,
)


# ---------------------------------------------------------------------------
# ルールカタログ
# ---------------------------------------------------------------------------


@dataclass
class RuleCatalog:
    version: str
    thresholds: dict[str, Any]
    risk_scoring: dict[str, Any]
    categories: list[dict[str, Any]]
    rules: dict[str, dict[str, Any]]  # rule_id -> rule 定義

    def get(self, rule_id: str) -> Optional[dict[str, Any]]:
        return self.rules.get(rule_id)

    def rules_by_detection_type(self, detection_type: str) -> list[dict[str, Any]]:
        return [r for r in self.rules.values() if r.get("detection", {}).get("type") == detection_type]

    def category_name(self, category_id: str) -> str:
        for c in self.categories:
            if c.get("id") == category_id:
                return c.get("name_ja", category_id)
        return category_id

    # --- 閾値アクセサ（ファネル・トリアージの単一情報源） ---
    @property
    def high_threshold(self) -> float:
        return float(self.thresholds.get("high", 70))

    @property
    def medium_threshold(self) -> float:
        return float(self.thresholds.get("medium", 40))

    @property
    def auto_dismiss_below(self) -> float:
        return float(self.thresholds.get("triage", {}).get("auto_dismiss_below", 20))

    @property
    def escalate_at_or_above(self) -> float:
        return float(self.thresholds.get("triage", {}).get("escalate_at_or_above", self.high_threshold))


@lru_cache(maxsize=None)
def _load_rule_catalog_cached(path_str: str) -> RuleCatalog:
    with open(path_str, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    rules_list = raw.get("rules", []) or []
    rules: dict[str, dict[str, Any]] = {}
    for rule in rules_list:
        rid = rule.get("id")
        if rid is None:
            raise ValueError(f"id を持たないルールがある: {rule!r}")
        if rid in rules:
            raise ValueError(f"ルールIDが重複している: {rid}")  # ID 重複は禁止（rule-authoring）
        rules[rid] = rule

    return RuleCatalog(
        version=str(raw.get("version", "0")),
        thresholds=raw.get("thresholds", {}) or {},
        risk_scoring=raw.get("risk_scoring", {}) or {},
        categories=raw.get("categories", []) or [],
        rules=rules,
    )


def load_rule_catalog(path: Optional[str] = None) -> RuleCatalog:
    return _load_rule_catalog_cached(str(path) if path else str(rule_catalog_path()))


# ---------------------------------------------------------------------------
# 不正シナリオ
# ---------------------------------------------------------------------------


@dataclass
class FraudScenarios:
    version: str
    scenarios: dict[str, dict[str, Any]]  # scenario_id -> シナリオ定義
    _by_linked_rule: dict[str, list[str]] = field(default_factory=dict)
    _by_category: dict[str, list[str]] = field(default_factory=dict)

    def get(self, scenario_id: str) -> Optional[dict[str, Any]]:
        return self.scenarios.get(scenario_id)

    def for_rule(self, rule_id: str) -> list[dict[str, Any]]:
        return [self.scenarios[s] for s in self._by_linked_rule.get(rule_id, [])]

    def for_category(self, category_ja: str) -> list[dict[str, Any]]:
        return [self.scenarios[s] for s in self._by_category.get(category_ja, [])]


@lru_cache(maxsize=None)
def _load_fraud_scenarios_cached(path_str: str) -> FraudScenarios:
    with open(path_str, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    scenarios: dict[str, dict[str, Any]] = {}
    by_linked_rule: dict[str, list[str]] = {}
    by_category: dict[str, list[str]] = {}
    for sc in raw.get("scenarios", []) or []:
        sid = sc.get("id")
        if sid is None:
            raise ValueError(f"id を持たないシナリオがある: {sc!r}")
        if sid in scenarios:
            raise ValueError(f"シナリオIDが重複している: {sid}")
        scenarios[sid] = sc
        for rid in sc.get("linked_rules", []) or []:
            by_linked_rule.setdefault(rid, []).append(sid)
        cat = sc.get("category_ja")
        if cat:
            by_category.setdefault(cat, []).append(sid)

    return FraudScenarios(
        version=str(raw.get("version", "0")),
        scenarios=scenarios,
        _by_linked_rule=by_linked_rule,
        _by_category=by_category,
    )


def load_fraud_scenarios(path: Optional[str] = None) -> FraudScenarios:
    return _load_fraud_scenarios_cached(str(path) if path else str(fraud_scenarios_path()))


# ---------------------------------------------------------------------------
# エンゲージメント構成（独立性・コネクタ法的基盤）
# ---------------------------------------------------------------------------

# ファイルが無い場合の安全側デフォルト（既定は Track A・機微コネクタは無効）。
_DEFAULT_ENGAGEMENT: dict[str, Any] = {
    "engagement_mode": "track_a",
    "model_risk": {"model_owner": "unspecified"},
    "independence_checklist": {},
    "connectors": {},
}


@lru_cache(maxsize=None)
def _load_engagement_cached(path_str: str) -> dict[str, Any]:
    from os.path import exists

    if not exists(path_str):
        return dict(_DEFAULT_ENGAGEMENT)
    with open(path_str, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    merged = dict(_DEFAULT_ENGAGEMENT)
    merged.update(raw)
    return merged


def load_engagement_config(path: Optional[str] = None) -> dict[str, Any]:
    return _load_engagement_cached(str(path) if path else str(engagement_config_path()))


def clear_caches() -> None:
    """テストで別の config を読ませたいときにキャッシュを消す。"""
    _load_rule_catalog_cached.cache_clear()
    _load_fraud_scenarios_cached.cache_clear()
    _load_engagement_cached.cache_clear()
