"""設定ロード（ルール・シナリオ・エンゲージメント）のテスト。"""

from __future__ import annotations

from expense_risk.config import (
    load_engagement_config,
    load_fraud_scenarios,
    load_rule_catalog,
)


def test_rule_catalog_loads_all_rules_no_duplicates():
    cat = load_rule_catalog()
    assert len(cat.rules) == 62
    assert cat.high_threshold == 70
    assert cat.medium_threshold == 40
    # 全ルールが detection.type を持つ
    for r in cat.rules.values():
        assert r.get("detection", {}).get("type") in (
            "deterministic", "statistical", "ml_assisted", "agent_verified"
        )


def test_rule_catalog_false_positive_notes_present():
    # アラート疲れ対策: 全ルールに誤検知注記が必須（rule-authoring スキル）
    cat = load_rule_catalog()
    missing = [rid for rid, r in cat.rules.items() if not r.get("false_positive_notes_ja")]
    assert missing == [], f"false_positive_notes_ja 欠落: {missing}"


def test_fraud_scenarios_linked_rules_exist():
    cat = load_rule_catalog()
    fs = load_fraud_scenarios()
    assert len(fs.scenarios) >= 15
    for sid, sc in fs.scenarios.items():
        for rid in sc.get("linked_rules", []):
            assert cat.get(rid) is not None, f"{sid} が未知ルール {rid} を参照"
        assert sc.get("synthetic_test_ja"), f"{sid} に synthetic_test_ja が無い"


def test_engagement_default_track_a_and_sensitive_off():
    eng = load_engagement_config()
    assert eng["engagement_mode"] == "track_a"
    # 従業員個人データの機微コネクタは既定で無効
    assert eng["connectors"]["calendar.query"]["enabled"] is False
