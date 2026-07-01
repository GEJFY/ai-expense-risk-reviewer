"""小さな共通ユーティリティ（時刻・正規JSON・ハッシュ）.

再現性のため、時刻は外部から注入できるようにする（テストは固定時刻を渡す）。
ハッシュチェーン（監査ログ）は決定論的なシリアライズが前提なので、
キーをソートした正規 JSON を一箇所で定義する。
"""

from __future__ import annotations

import hashlib
import json
import math
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


def finite_float(value: Any, default: float = 0.0) -> float:
    """float 化。変換不能・非有限（NaN/Inf）は default に落とす（NaN 伝播の防止）。

    JSON は本来 NaN/Infinity を許さないが、Python の ``json.loads`` は既定で受理する。
    非有限値がスコアや監査ログに伝播すると 0-100 制約を破るため、境界で必ず正規化する。
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def is_round(amount: float, base: int = 1000, tol: float = 0.01) -> bool:
    """金額が base の倍数か（浮動小数の丸め誤差に耐える許容判定）。"""
    if not math.isfinite(amount) or amount == 0:
        return False
    return abs(amount - round(amount / base) * base) < tol


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
