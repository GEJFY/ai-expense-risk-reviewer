"""データ契約（スキーマ適合・往復）のテスト。"""

from __future__ import annotations

from expense_risk.contracts import (
    Evidence,
    ExpenseLine,
    MatchedRule,
    Provenance,
    Rationale,
    RiskFinding,
    is_valid,
    validation_errors,
)


def test_expense_line_valid_and_extra_preserved():
    el = ExpenseLine.from_dict({
        "expense_line_id": "E1", "applicant_id": "A1", "transaction_date": "2026-01-05",
        "amount": 1000, "currency": "JPY", "expense_category": "会議費",
        "approver_authority_limit": 50000,  # スキーマ外の拡張列
    })
    assert is_valid(el.to_dict(), "ExpenseLine")
    assert el.get("approver_authority_limit") == 50000
    assert el.extra["approver_authority_limit"] == 50000


def test_expense_line_missing_required_flagged():
    errs = validation_errors({"expense_line_id": "E1"}, "ExpenseLine")
    assert any("applicant_id" in e for e in errs)


def test_risk_finding_requires_rationale():
    rf = RiskFinding(
        finding_id="F1", expense_line_id="E1", risk_score=50.0, severity="medium",
        triage="review", rationale=Rationale([MatchedRule("CTRL-001", 35)], None, []),
        model_version="rc-1.0", generated_at="2026-01-06T00:00:00Z",
    )
    assert is_valid(rf.to_dict(), "RiskFinding")
    # hitl_status の既定は pending（AI は confirmed にできない）
    assert rf.to_dict()["hitl_status"] == "pending"


def test_evidence_provenance_required():
    ev = Evidence(
        evidence_id="EV1", type="receipt_ocr", source="ocr.extract",
        collected_at="2026-01-06T00:00:00Z",
        provenance=Provenance(collected_by_role="auditor", access_scope="—"),
        content={"amount": 1000},
    )
    assert is_valid(ev.to_dict(), "Evidence")


def test_risk_score_bounds_enforced_by_schema():
    rf = RiskFinding("F1", "E1", 150.0, "critical", "escalate",
                     Rationale([], None, []), "rc-1.0", "2026-01-06T00:00:00Z")
    errs = validation_errors(rf.to_dict(), "RiskFinding")
    assert any("150" in e or "maximum" in e for e in errs)
