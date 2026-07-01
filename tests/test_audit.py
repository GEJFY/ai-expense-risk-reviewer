"""監査ログ（WORM＋ハッシュチェーン）のテスト。"""

from __future__ import annotations

from expense_risk.audit import GENESIS_HASH, AuditLog


def _fill(log):
    log.append("observe", "agent", "選別", expense_line_id="E1")
    log.append("hypothesize", "agent", "仮説", finding_id="F1", expense_line_id="E1")
    log.append("explore", "tool:calendar.query", "照会", outputs={"evidence_id": "EV1"})


def test_chain_valid_and_links():
    log = AuditLog(model_version="rc-1.0")
    _fill(log)
    ok, problems = log.verify_chain()
    assert ok and problems == []
    assert log.entries[0].prev_hash == GENESIS_HASH
    assert log.entries[1].prev_hash == log.entries[0].hash


def test_tamper_detected():
    log = AuditLog(model_version="rc-1.0")
    _fill(log)
    log.entries[1].action = "改ざん"
    ok, problems = log.verify_chain()
    assert not ok
    assert any(kind == "hash_mismatch" for _, kind in problems)


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path=path, model_version="rc-1.0")
    _fill(log)
    log.close()
    # 追記専用で書かれた内容を再読込 → チェーン整合
    reloaded = AuditLog.load(path)
    assert len(reloaded) == 3
    assert reloaded.verify_chain()[0] is True


def test_worm_append_continues_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path=path, model_version="rc-1.0")
    log.append("observe", "agent", "1")
    log.close()
    # 既存ファイルに追記してもチェーンが継続する
    log2 = AuditLog(path=path, model_version="rc-1.0")
    log2.append("verify", "agent", "2")
    log2.close()
    assert len(log2) == 2
    assert log2.verify_chain()[0] is True
