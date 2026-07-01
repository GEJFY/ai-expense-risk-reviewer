"""ML 異常検知とスコア統合のテスト。"""

from __future__ import annotations

import random

from expense_risk.etl import ingest
from expense_risk.features import compute_features
from expense_risk.ml import detect_anomalies
from expense_risk.rules.engine import RuleHit
from expense_risk.scoring import combine_score


def _bulk(n=30):
    rng = random.Random(0)
    recs = [{"expense_line_id": f"E{i}", "applicant_id": f"A{i%4}", "transaction_date": "2026-02-10",
             "transaction_datetime": "2026-02-10T12:00:00", "amount": rng.randint(3000, 6000),
             "currency": "JPY", "expense_category": "会議費", "vendor_id": f"V{i%3}",
             "participants": ["a", "b"]} for i in range(n)]
    recs.append({"expense_line_id": "OUT", "applicant_id": "A0", "transaction_date": "2026-02-10",
                 "transaction_datetime": "2026-02-10T03:00:00", "amount": 900000, "currency": "JPY",
                 "expense_category": "会議費", "vendor_id": "V9", "participants": ["a"]})
    return recs


def test_outlier_ranked_top_with_attribution():
    res = ingest(_bulk())
    fs = compute_features(res.lines)
    anom = detect_anomalies(fs, method="isolation_forest")
    top = max(anom.per_line.items(), key=lambda kv: kv[1]["anomaly_score"])[0]
    assert top == "OUT"
    att = anom.attribution_for("OUT")
    assert att.model == "isolation_forest"
    assert 0.0 <= att.anomaly_score <= 1.0
    assert len(att.shap_top_features) >= 1


def test_small_sample_skips_ml():
    res = ingest(_bulk(3)[:3])
    fs = compute_features(res.lines)
    anom = detect_anomalies(fs)
    assert anom.skipped is True


def test_combine_score_capped_and_diminishing():
    h = lambda w: RuleHit("X", w, "high", "review", "AMT", "d")
    s1 = combine_score([h(35)], 0.0)
    s2 = combine_score([h(35), h(35)], 0.0)
    s_many = combine_score([h(50)] * 10, 1.0)
    assert 0 <= s1 <= 100 and 0 <= s2 <= 100
    assert s2 > s1                 # シグナルが増えると単調増加
    assert (s2 - s1) < s1          # ただし逓減（2つ目の寄与は1つ目より小さい）
    assert s_many <= 100           # 上限100


def test_ml_only_bounded_by_weight():
    # ルール0・ML最大でも ml_weight=0.5 → 最大 50 点
    assert combine_score([], 1.0, ml_weight=0.5) == 50.0
