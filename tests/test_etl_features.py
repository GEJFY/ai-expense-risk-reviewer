"""L1 ETL / L2 特徴量のテスト。"""

from __future__ import annotations

from expense_risk.etl import (
    DQ_AMOUNT_NON_POSITIVE,
    DQ_DUPLICATE_LINE_ID,
    DQ_UNKNOWN_APPLICANT,
    ingest,
)
from expense_risk.features import NUMERIC_FEATURES, compute_features


def _rec(i, **kw):
    base = {"expense_line_id": f"E{i}", "applicant_id": "A1", "transaction_date": "2026-01-05",
            "amount": 1000, "currency": "JPY", "expense_category": "会議費"}
    base.update(kw)
    return base


def test_data_quality_flags():
    recs = [
        _rec(1, amount=-50),                       # 非正金額
        _rec(1),                                   # 重複ID(E1)
        _rec(2, applicant_id="ZZZ"),               # マスタに無い申請者
    ]
    res = ingest(recs, masters={"employees": {"A1": {}}})
    assert DQ_AMOUNT_NON_POSITIVE in res.flags_for("E1")
    assert DQ_DUPLICATE_LINE_ID in res.flags_for("E1")
    assert DQ_UNKNOWN_APPLICANT in res.flags_for("E2")


def test_reconciliation_control_totals():
    recs = [_rec(1, amount=1000), _rec(2, amount=2000)]
    res = ingest(recs, control_totals={"count": 2, "amount_sum": 3000})
    assert res.reconciliation.count_matches is True
    assert res.reconciliation.amount_matches is True

    res2 = ingest(recs, control_totals={"count": 3, "amount_sum": 9999})
    assert res2.reconciliation.count_matches is False
    assert res2.reconciliation.amount_matches is False


def test_features_night_weekend_and_matrix():
    recs = [_rec(i, transaction_datetime="2026-02-08T23:30:00", vendor_id=f"V{i}") for i in range(3)]
    res = ingest(recs)
    fs = compute_features(res.lines)
    # 2026-02-08 は日曜・23:30 → weekend & night
    assert fs.per_line["E0"]["is_weekend"] == 1.0
    assert fs.per_line["E0"]["is_night"] == 1.0
    ids, X = fs.matrix()
    assert X.shape == (3, len(NUMERIC_FEATURES))


def test_vendor_hhi_concentration():
    recs = [_rec(i, vendor_id="V1") for i in range(5)]  # 全て同一取引先 → HHI=1
    res = ingest(recs)
    fs = compute_features(res.lines)
    assert fs.applicant_profiles["A1"]["vendor_hhi"] == 1.0
