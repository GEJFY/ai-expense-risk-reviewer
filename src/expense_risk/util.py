"""小さな共通ユーティリティ（時刻・正規JSON・ハッシュ）.

再現性のため、時刻は外部から注入できるようにする（テストは固定時刻を渡す）。
ハッシュチェーン（監査ログ）は決定論的なシリアライズが前提なので、
キーをソートした正規 JSON を一箇所で定義する。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Optional


def now_iso(clock: Optional[datetime] = None) -> str:
    """ISO 8601（UTC・秒精度・末尾Z）文字列。``clock`` 未指定なら現在時刻。"""
    dt = clock or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(obj: Any) -> str:
    """決定論的な JSON 文字列（キーソート・空白なし）。ハッシュ計算の基盤。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(obj: Any) -> str:
    """任意オブジェクトの正規化ハッシュ（証憑画像ハッシュ・設定ハッシュ等）。"""
    return sha256_hex(canonical_json(obj))


def parse_datetime(value: Any) -> Optional[datetime]:
    """ISO 8601（`Z` 許容）を datetime に。失敗時 None。"""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_date(value: Any) -> Optional[date]:
    """日付/日時文字列を date に。失敗時 None。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    dt = parse_datetime(text)
    if dt is not None:
        return dt.date()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
