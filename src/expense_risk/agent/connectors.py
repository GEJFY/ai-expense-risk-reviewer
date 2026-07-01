"""証憑コネクタ（すべて read-only・最小権限・ゲート制御）.

エージェントが呼べるツールは docs/agent-design.md §2 の read-only 8種に限定する。
**書き込み系ツールは存在しない**（メソッドとして持たせない）。各コネクタは:

- **能力の技術的最小化**: 許可リスト外は拒否。レート上限。外部送信なし。
- **プライバシー・ゲート**: 従業員個人データを扱う機微コネクタ（calendar/mail/meeting）は
  engagement.yaml で ``enabled`` かつ法的基盤（``legal_basis_ref``）が揃い、かつ G1 承認が
  なければ起動しない（security-privacy §3）。
- **来歴付与**: 取得証憑に provenance（取得者ロール・スコープ・法的基盤・保存期限）を付す。
- **注入検査**: 非信頼コンテンツ（OCR/メール/公開情報）は injection スキャンし
  ``Evidence.injection_flags`` に記録する。

本実装のコネクタは決定論的な**モック**。実データ接続時も同じインタフェース／ゲートを保つ。
モックは明細の ``extra['mock']`` に置かれた「証憑の世界」を読む（合成テストが制御する）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..contracts import Evidence, Provenance
from ..util import now_iso, parse_date
from .injection import scan_content

# read-only 8ツール → Evidence.type（スキーマ enum）
TOOL_EVIDENCE_TYPE = {
    "calendar.query": "calendar_event",
    "mail.search": "email",
    "meeting.attendees": "meeting_attendees",
    "route.estimate": "route_estimate",
    "geo.resolve": "geo_resolution",
    "ocr.extract": "receipt_ocr",
    "sanctions.lookup": "sanctions_match",
    "master.lookup": "master_record",
}
ALLOWED_TOOLS = frozenset(TOOL_EVIDENCE_TYPE)
# 非信頼コンテンツを返すコネクタ（注入検査の対象）
UNTRUSTED_TOOLS = frozenset({"ocr.extract", "mail.search", "sanctions.lookup"})
# 法的基盤の未記入・仮置きとみなす値（機微コネクタの起動を許さない）
_PLACEHOLDER_LEGAL_BASIS = frozenset({"", "-", "todo", "tbd", "tba", "placeholder", "xxx", "none", "null", "n/a", "na"})

# call status
OK = "ok"
BLOCKED_DISABLED = "blocked_disabled"
BLOCKED_NO_LEGAL_BASIS = "blocked_no_legal_basis"
REQUIRES_APPROVAL = "requires_approval"   # G1: 機微アクションの事前承認
RATE_LIMITED = "rate_limited"
NO_DATA = "no_data"
UNKNOWN_TOOL = "unknown_tool"


@dataclass
class ConnectorCallResult:
    tool: str
    status: str
    evidence: Optional[Evidence] = None
    reason: str = ""


def _mock_for(line: Any, aspect: str) -> Optional[dict[str, Any]]:
    mock = line.get("mock") if hasattr(line, "get") else None
    if not isinstance(mock, dict):
        return None
    val = mock.get(aspect)
    return val if isinstance(val, dict) else None


class ConnectorRegistry:
    """コネクタの起動可否ゲート・レート制御・証憑生成を一元管理する。"""

    def __init__(
        self,
        engagement_config: dict[str, Any],
        *,
        rate_limit: int = 25,
        allow_sensitive_approved: bool = False,
        collected_by_role: str = "auditor:read_only",
        clock: Optional[datetime] = None,
    ) -> None:
        self.config = engagement_config or {}
        self.connectors_cfg = self.config.get("connectors", {}) or {}
        self.rate_limit = rate_limit
        self.allow_sensitive_approved = allow_sensitive_approved  # G1 承認済みフラグ
        self.collected_by_role = collected_by_role
        self.clock = clock
        self._call_counts: dict[str, int] = {}
        self._seq = 0
        retention_days = self.config.get("evidence_retention_days")
        self._retention_days = int(retention_days) if retention_days else None

    # --- ゲート判定 ---
    def gate(self, tool: str) -> tuple[bool, str]:
        """このツールを今呼べるか。``(可否, 理由ステータス)``。"""
        if tool not in ALLOWED_TOOLS:
            return False, UNKNOWN_TOOL
        cfg = self.connectors_cfg.get(tool, {})
        if not cfg.get("enabled", False):
            return False, BLOCKED_DISABLED
        if cfg.get("sensitive", False):
            lb = cfg.get("legal_basis_ref")
            if not lb or str(lb).strip().lower() in _PLACEHOLDER_LEGAL_BASIS:
                return False, BLOCKED_NO_LEGAL_BASIS   # プライバシー法的基盤の未充足/仮置き
            if not self.allow_sensitive_approved:
                return False, REQUIRES_APPROVAL          # G1 事前承認が必要
        if self._call_counts.get(tool, 0) >= self.rate_limit:
            return False, RATE_LIMITED
        return True, OK

    # --- 呼び出し ---
    def call(self, tool: str, line: Any) -> ConnectorCallResult:
        ok, status = self.gate(tool)
        if not ok:
            return ConnectorCallResult(tool=tool, status=status, reason=f"gate: {status}")
        self._call_counts[tool] = self._call_counts.get(tool, 0) + 1

        content = self._fetch(tool, line)
        if content is None:
            return ConnectorCallResult(tool=tool, status=NO_DATA, reason="該当証憑なし")

        self._seq += 1
        cfg = self.connectors_cfg.get(tool, {})
        injection_flags = scan_content(content) if tool in UNTRUSTED_TOOLS else None
        ocr_conf = content.pop("_ocr_confidence", None)
        prov = Provenance(
            collected_by_role=self.collected_by_role,
            access_scope=str(cfg.get("access_scope", "read")),
            legal_basis_ref=cfg.get("legal_basis_ref"),
            retention_until=self._retention_until(line),
        )
        ev = Evidence(
            evidence_id=f"EV-{line.expense_line_id}-{tool.split('.')[0]}-{self._seq}",
            type=TOOL_EVIDENCE_TYPE[tool],
            source=tool,
            collected_at=now_iso(self.clock),
            provenance=prov,
            content=content,
            ocr_confidence=ocr_conf,
            injection_flags=injection_flags or None,
        )
        return ConnectorCallResult(tool=tool, status=OK, evidence=ev)

    def _retention_until(self, line: Any) -> Optional[str]:
        if not self._retention_days:
            return None
        base = parse_date(getattr(line, "transaction_date", None)) or (self.clock or datetime.now(timezone.utc)).date()
        return (base + timedelta(days=self._retention_days)).isoformat()

    # --- 各ツールの取得（モック。実データ接続時も戻り値の形は同じ） ---
    def _fetch(self, tool: str, line: Any) -> Optional[dict[str, Any]]:
        if tool == "calendar.query":
            return _mock_for(line, "calendar")
        if tool == "mail.search":
            return _mock_for(line, "mail")
        if tool == "meeting.attendees":
            return _mock_for(line, "meeting")
        if tool == "route.estimate":
            return _mock_for(line, "route")
        if tool == "geo.resolve":
            return _mock_for(line, "geo")
        if tool == "ocr.extract":
            return _mock_for(line, "ocr")
        if tool == "sanctions.lookup":
            return _mock_for(line, "sanctions")
        if tool == "master.lookup":
            return _mock_for(line, "master")
        return None

    def call_counts(self) -> dict[str, int]:
        return dict(self._call_counts)
