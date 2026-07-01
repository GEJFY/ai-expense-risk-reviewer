"""ガバナンス（独立性ゲート・モデルバージョン・助言表現）のテスト。"""

from __future__ import annotations

import copy

import pytest

from expense_risk.config import load_engagement_config, load_rule_catalog
from expense_risk.governance import (
    IndependenceError,
    build_governance,
    build_model_version,
)


def test_track_a_no_independence_issues():
    gov = build_governance(load_rule_catalog(), load_engagement_config())
    assert gov.engagement_mode == "track_a"
    assert gov.independence_issues() == []
    gov.enforce_gate()  # 例外なし


def test_track_b_blocks_when_checklist_unmet():
    eng = copy.deepcopy(load_engagement_config())
    eng["engagement_mode"] = "track_b"
    gov = build_governance(load_rule_catalog(), eng)
    assert gov.independence_issues()  # 未充足項目あり
    with pytest.raises(IndependenceError):
        gov.enforce_gate()


def test_track_b_passes_when_checklist_met():
    eng = copy.deepcopy(load_engagement_config())
    eng["engagement_mode"] = "track_b"
    for k in eng["independence_checklist"]:
        eng["independence_checklist"][k] = True
    gov = build_governance(load_rule_catalog(), eng)
    gov.enforce_gate()  # 例外なし
    # track_b では助言表現に制限
    text = gov.finalize_recommendation("是正を推奨")
    assert "助言" in text


def test_model_version_deterministic():
    cat = load_rule_catalog()
    eng = load_engagement_config()
    v1 = build_model_version(cat, eng, ml_method="isolation_forest", ml_weight=0.5)
    v2 = build_model_version(cat, eng, ml_method="isolation_forest", ml_weight=0.5)
    v3 = build_model_version(cat, eng, ml_method="pca", ml_weight=0.5)
    assert v1 == v2            # 同一構成 → 同一バージョン（再現性）
    assert v1 != v3            # モデルが変われば別バージョン
    assert v1.startswith("rc-")
