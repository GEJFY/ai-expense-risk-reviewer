"""HITL コンソール（Web UI バックエンド）の API スモークテスト。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from expense_risk.webapp.server import create_app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def test_index_served(client):
    assert client.get("/").status_code == 200


def test_summary_shape_and_disclaimer(client):
    s = client.get("/api/summary").json()
    assert s["engagement_mode"] == "track_a"
    assert s["audit"]["chain_ok"] is True
    # 数値は「合成データ・参考値」であることを免責として明示（誇張・保証をしない）
    assert "保証するものではありません" in s["validation"]["disclaimer"]


def test_findings_filter(client):
    esc = client.get("/api/findings?triage=escalate").json()
    assert esc["count"] >= 1
    assert all(f["triage"] == "escalate" for f in esc["findings"])


def test_finding_detail_has_rationale(client):
    fid = client.get("/api/findings?triage=escalate").json()["findings"][0]["finding_id"]
    d = client.get(f"/api/findings/{fid}").json()
    # 根拠（違反ルール or ML or 証憑）を必ず持つ
    assert d["rationale_rules"] or d["ml_attribution"] or d["evidence"]
    assert d["hitl_status"] == "pending"
    # 摘要は費目と整合（データ品質）
    if d.get("description") and d.get("expense_category"):
        assert d["expense_category"] in d["description"]


def test_hitl_decision_only_human_confirms(client):
    fid = client.get("/api/findings?triage=escalate").json()["findings"][0]["finding_id"]
    before = client.get(f"/api/findings/{fid}").json()
    assert before["hitl_status"] == "pending"       # AI は pending のまま
    after = client.post(f"/api/findings/{fid}/decision", json={"decision": "confirm"}).json()
    assert after["hitl_status"] == "confirmed"      # 人間の操作で確定
    # 監査ログに人間の判断が追記され、チェーンは維持される
    au = client.get(f"/api/audit?finding_id={fid}").json()
    assert au["chain_ok"] is True
    assert any(e["phase"] == "hitl" for e in au["entries"])
    client.post("/api/reset")                        # 後続テストのため初期化


def test_reset(client):
    assert client.post("/api/reset").json()["ok"] is True
