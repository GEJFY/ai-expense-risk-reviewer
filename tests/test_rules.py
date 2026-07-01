"""L3 ルールエンジンのテスト。"""

from __future__ import annotations

from expense_risk.etl import ingest
from expense_risk.features import compute_features
from expense_risk.rules import RuleEngine


def _run(recs, masters=None):
    res = ingest(recs, masters=masters)
    fs = compute_features(res.lines)
    return RuleEngine().evaluate(res.lines, fs, masters=masters)


def _rule_ids(result, lid):
    return {h.rule_id for h in result.hits_for(lid)}


def test_self_approval_ctrl001():
    recs = [{"expense_line_id": "E1", "applicant_id": "A1", "approver_id": "A1",
             "transaction_date": "2026-01-05", "amount": 1000, "currency": "JPY",
             "expense_category": "会議費"}]
    res = _run(recs)
    assert "CTRL-001" in _rule_ids(res, "E1")


def test_duplicate_dup001():
    common = dict(transaction_date="2026-01-05", amount=5000, currency="JPY",
                  expense_category="会議費", vendor_id="V1")
    recs = [{"expense_line_id": "E1", "applicant_id": "A1", **common},
            {"expense_line_id": "E2", "applicant_id": "A2", **common}]
    res = _run(recs)
    assert "DUP-001" in _rule_ids(res, "E1")
    assert "DUP-001" in _rule_ids(res, "E2")


def test_unspecified_participants_part001():
    recs = [{"expense_line_id": "E1", "applicant_id": "A1", "transaction_date": "2026-01-05",
             "amount": 1000, "currency": "JPY", "expense_category": "交際費",
             "participants": ["他3名"]}]
    res = _run(recs)
    assert "PART-001" in _rule_ids(res, "E1")


def test_limit_match_amt002():
    recs = [{"expense_line_id": "E1", "applicant_id": "A1", "transaction_date": "2026-01-05",
             "amount": 50000, "currency": "JPY", "expense_category": "会議費",
             "approval_limit": 50000}]
    res = _run(recs)
    assert "AMT-002" in _rule_ids(res, "E1")


def test_weekend_night_time001():
    recs = [{"expense_line_id": "E1", "applicant_id": "A1", "transaction_date": "2026-02-08",
             "transaction_datetime": "2026-02-08T23:30:00", "amount": 1000, "currency": "JPY",
             "expense_category": "会議費"}]
    res = _run(recs)
    assert "TIME-001" in _rule_ids(res, "E1")


def test_missing_receipt_cons003():
    recs = [{"expense_line_id": "E1", "applicant_id": "A1", "transaction_date": "2026-01-05",
             "amount": 40000, "currency": "JPY", "expense_category": "会議費",
             "receipt_required_threshold": 30000}]
    res = _run(recs)
    assert "CONS-003" in _rule_ids(res, "E1")


def test_clean_line_no_hits():
    recs = [{"expense_line_id": "E1", "applicant_id": "A1", "approver_id": "A2",
             "transaction_date": "2026-01-05", "transaction_datetime": "2026-01-06T13:00:00",
             "amount": 3210, "currency": "JPY", "expense_category": "会議費", "vendor_id": "V1"}]
    res = _run(recs)
    assert _rule_ids(res, "E1") == set()


def test_coverage_reports_not_implemented():
    res = _run([{"expense_line_id": "E1", "applicant_id": "A1", "transaction_date": "2026-01-05",
                 "amount": 1000, "currency": "JPY", "expense_category": "会議費"}])
    cov = res.coverage
    assert cov["total_rules"] == 62
    # agent_verified / ml_assisted は別層で処理される（エンジン対象外）
    assert len(cov["agent_verified_rules"]) >= 1
    assert len(cov["ml_assisted_rules"]) >= 1
    # 未実装ルールは明示（暗黙の取りこぼしを作らない）
    assert isinstance(cov["engine_not_implemented"], list)
