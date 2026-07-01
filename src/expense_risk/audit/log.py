"""改ざん不能な自律監査ログ（WORM＋ハッシュチェーン）.

各レコードは直前レコードの ``prev_hash`` を含み、自身の ``hash`` で連結する。
1件でも改ざんすると鎖が切れて検知できる（data-contracts / governance-independence）。

- **WORM（Write Once Read Many）**: ファイルは追記専用で開き、既存行を書き換えない。
- **ハッシュチェーン**: ``hash = sha256(canonical_json(レコード − hash))``。
  ``canonical_json`` はキーソート済みで決定論的（util.canonical_json）。
- **再現性**: どの明細が・どのフェーズで・どのツールをどう呼び・どう判定したかを
  欠落なく記録する（docs/agent-design.md §6）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..contracts import AuditLogEntry, assert_valid
from ..util import canonical_json, now_iso, sha256_hex

GENESIS_HASH = "0" * 64


def _entry_from_dict(d: dict[str, Any]) -> AuditLogEntry:
    return AuditLogEntry(
        log_id=d["log_id"],
        timestamp=d["timestamp"],
        phase=d["phase"],
        actor=d["actor"],
        action=d["action"],
        prev_hash=d["prev_hash"],
        hash=d["hash"],
        finding_id=d.get("finding_id"),
        expense_line_id=d.get("expense_line_id"),
        inputs=d.get("inputs"),
        outputs=d.get("outputs"),
        termination_reason=d.get("termination_reason"),
        model_version=d.get("model_version"),
    )


def _recompute_hash(entry: AuditLogEntry) -> str:
    payload = entry.to_dict()
    payload.pop("hash", None)  # hash 自身は計算対象から除く
    return sha256_hex(canonical_json(payload))


class AuditLog:
    """追記専用のハッシュチェーン付き監査ログ。

    ``path`` を渡すと各 ``append`` が即座にファイルへ1行追記される（WORM）。
    既存ファイルがあればチェーンを引き継ぐ。
    """

    def __init__(self, path: Optional[str | Path] = None, model_version: Optional[str] = None) -> None:
        self.entries: list[AuditLogEntry] = []
        self.model_version = model_version
        self.path = Path(path) if path else None
        self._fh = None
        if self.path is not None:
            if self.path.exists() and self.path.stat().st_size > 0:
                self._load_existing(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.path, "a", encoding="utf-8")  # 追記専用（既存行は不変）

    # --- 読み込み ---
    def _load_existing(self, path: Path) -> None:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    self.entries.append(_entry_from_dict(json.loads(line)))

    @classmethod
    def load(cls, path: str | Path) -> "AuditLog":
        log = cls.__new__(cls)  # ファイルハンドルを開かずに読み込むだけ
        log.entries = []
        log.model_version = None
        log.path = Path(path)
        log._fh = None
        log._load_existing(log.path)
        return log

    # --- 追記 ---
    @property
    def last_hash(self) -> str:
        return self.entries[-1].hash if self.entries else GENESIS_HASH

    def append(
        self,
        phase: str,
        actor: str,
        action: str,
        *,
        finding_id: Optional[str] = None,
        expense_line_id: Optional[str] = None,
        inputs: Optional[dict[str, Any]] = None,
        outputs: Optional[dict[str, Any]] = None,
        termination_reason: Optional[str] = None,
        model_version: Optional[str] = None,
        clock: Optional[datetime] = None,
        validate: bool = True,
    ) -> AuditLogEntry:
        """監査ログを1件追記し、生成した ``AuditLogEntry`` を返す。"""
        seq = len(self.entries) + 1
        prev = self.last_hash
        entry = AuditLogEntry(
            log_id=f"LOG-{seq:06d}",
            timestamp=now_iso(clock),
            phase=phase,
            actor=actor,
            action=action,
            prev_hash=prev,
            hash="",  # 直後に確定
            finding_id=finding_id,
            expense_line_id=expense_line_id,
            inputs=inputs,
            outputs=outputs,
            termination_reason=termination_reason,
            model_version=model_version or self.model_version,
        )
        entry.hash = _recompute_hash(entry)
        if validate:
            assert_valid(entry.to_dict(), "AuditLogEntry")
        self.entries.append(entry)
        if self._fh is not None:
            self._fh.write(canonical_json(entry.to_dict()) + "\n")
            self._fh.flush()
        return entry

    # --- 検証・出力 ---
    def verify_chain(self) -> tuple[bool, list[tuple[int, str]]]:
        """チェーン整合性を検証。``(ok, [(index, 理由), ...])`` を返す。"""
        problems: list[tuple[int, str]] = []
        prev = GENESIS_HASH
        for i, e in enumerate(self.entries):
            if e.prev_hash != prev:
                problems.append((i, "prev_hash_mismatch"))
            if _recompute_hash(e) != e.hash:
                problems.append((i, "hash_mismatch"))
            prev = e.hash
        return (not problems, problems)

    def to_list(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.entries]

    def write_jsonl(self, path: str | Path) -> None:
        """全レコードを JSONL として書き出す（読み取り用エクスポート）。"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            for e in self.entries:
                fh.write(canonical_json(e.to_dict()) + "\n")

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __len__(self) -> int:
        return len(self.entries)
