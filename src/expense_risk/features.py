"""L2: 特徴量・正規化層.

費目別の特徴量空間・申請者行動プロファイル・マスタ結合を用意する
（docs/architecture.md §1 L2）。上位の L3（統計ルール・機械学習）はここが作る
特徴量を読む。決定論的に計算し、同じ入力なら必ず同じ特徴量になる。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from .contracts import ExpenseLine
from .util import parse_datetime

# ML が使う数値特徴量の列（この順序で行列化する）
NUMERIC_FEATURES = (
    "log_amount",
    "hour",
    "dow",
    "is_weekend",
    "is_night",
    "is_round_1000",
    "headcount",
    "amount_per_head",
    "amount_z_in_category",
    "amount_z_for_applicant",
    "vendor_use_by_applicant",
)


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return float(np.std(values, ddof=0))


@dataclass
class FeatureSet:
    per_line: dict[str, dict[str, float]]  # expense_line_id -> {feature: value}
    applicant_profiles: dict[str, dict[str, Any]]
    category_stats: dict[str, dict[str, Any]]
    global_first_digit: dict[str, dict[int, int]]  # scope_key -> {digit: count}

    def matrix(self) -> tuple[list[str], np.ndarray]:
        """ML 用の行列 ``(ids, X)`` を返す（NUMERIC_FEATURES 順）。"""
        ids = list(self.per_line.keys())
        rows = [[self.per_line[i].get(f, 0.0) for f in NUMERIC_FEATURES] for i in ids]
        return ids, np.asarray(rows, dtype=float) if rows else np.empty((0, len(NUMERIC_FEATURES)))


def _first_digit(amount: float) -> Optional[int]:
    """整数部の先頭有効桁（1-9）。ベンフォード分析用。0/小数のみは None。"""
    a = int(abs(amount))
    if a == 0:
        return None
    d = int(str(a)[0])
    return d if 1 <= d <= 9 else None


def compute_features(lines: list[ExpenseLine]) -> FeatureSet:
    """明細群から特徴量・行動プロファイル・費目統計を計算する。"""
    # --- 第1パス: 集計（費目・申請者・取引先の統計） ---
    cat_amounts: dict[str, list[float]] = {}
    cat_perhead: dict[str, list[float]] = {}
    appl_amounts: dict[str, list[float]] = {}
    appl_vendor_counts: dict[str, dict[str, int]] = {}
    appl_hours: dict[str, list[int]] = {}
    appl_categories: dict[str, dict[str, int]] = {}
    appl_rounds: dict[str, list[int]] = {}
    appl_weekend: dict[str, list[int]] = {}
    first_digit_by_applicant: dict[str, dict[int, int]] = {}

    for ln in lines:
        try:
            amt = float(ln.amount)
        except (TypeError, ValueError):
            amt = 0.0
        cat = ln.expense_category or "unknown"
        appl = ln.applicant_id or "unknown"
        heads = len(ln.participants) if ln.participants else 0
        per_head = amt / heads if heads > 0 else amt

        cat_amounts.setdefault(cat, []).append(amt)
        cat_perhead.setdefault(cat, []).append(per_head)
        appl_amounts.setdefault(appl, []).append(amt)
        appl_rounds.setdefault(appl, []).append(1 if amt and amt % 1000 == 0 else 0)
        appl_categories.setdefault(appl, {})
        appl_categories[appl][cat] = appl_categories[appl].get(cat, 0) + 1
        if ln.vendor_id:
            appl_vendor_counts.setdefault(appl, {})
            appl_vendor_counts[appl][ln.vendor_id] = appl_vendor_counts[appl].get(ln.vendor_id, 0) + 1

        dt = parse_datetime(ln.transaction_datetime)
        if dt is not None:
            appl_hours.setdefault(appl, []).append(dt.hour)
            appl_weekend.setdefault(appl, []).append(1 if dt.weekday() >= 5 else 0)

        fd = _first_digit(amt)
        if fd is not None:
            first_digit_by_applicant.setdefault(appl, {})
            first_digit_by_applicant[appl][fd] = first_digit_by_applicant[appl].get(fd, 0) + 1

    # --- 費目統計 ---
    category_stats: dict[str, dict[str, Any]] = {}
    for cat, amounts in cat_amounts.items():
        category_stats[cat] = {
            "count": len(amounts),
            "amount_mean": float(np.mean(amounts)) if amounts else 0.0,
            "amount_std": _std(amounts),
            "per_head_mean": float(np.mean(cat_perhead[cat])) if cat_perhead[cat] else 0.0,
            "per_head_std": _std(cat_perhead[cat]),
        }

    # --- 申請者プロファイル ---
    applicant_profiles: dict[str, dict[str, Any]] = {}
    for appl, amounts in appl_amounts.items():
        vend_counts = appl_vendor_counts.get(appl, {})
        total_vend = sum(vend_counts.values())
        hhi = sum((c / total_vend) ** 2 for c in vend_counts.values()) if total_vend else 0.0
        hours = appl_hours.get(appl, [])
        rounds = appl_rounds.get(appl, [])
        weekends = appl_weekend.get(appl, [])
        applicant_profiles[appl] = {
            "count": len(amounts),
            "amount_mean": float(np.mean(amounts)) if amounts else 0.0,
            "amount_std": _std(amounts),
            "amount_median": float(np.median(amounts)) if amounts else 0.0,
            "vendor_counts": vend_counts,
            "vendor_hhi": hhi,
            "category_counts": appl_categories.get(appl, {}),
            "night_ratio": (sum(1 for h in hours if h >= 22 or h < 5) / len(hours)) if hours else 0.0,
            "weekend_ratio": (sum(weekends) / len(weekends)) if weekends else 0.0,
            "round_ratio": (sum(rounds) / len(rounds)) if rounds else 0.0,
        }

    # --- 第2パス: 明細ごとの特徴量 ---
    per_line: dict[str, dict[str, float]] = {}
    for ln in lines:
        try:
            amt = float(ln.amount)
        except (TypeError, ValueError):
            amt = 0.0
        cat = ln.expense_category or "unknown"
        appl = ln.applicant_id or "unknown"
        heads = len(ln.participants) if ln.participants else 0
        per_head = amt / heads if heads > 0 else amt

        cs = category_stats.get(cat, {})
        ap = applicant_profiles.get(appl, {})
        cat_std = cs.get("amount_std", 0.0) or 0.0
        appl_std = ap.get("amount_std", 0.0) or 0.0

        dt = parse_datetime(ln.transaction_datetime)
        hour = dt.hour if dt else -1
        dow = dt.weekday() if dt else -1
        is_weekend = 1 if (dt and dt.weekday() >= 5) else 0
        is_night = 1 if (dt and (dt.hour >= 22 or dt.hour < 5)) else 0

        vend_use = 0
        if ln.vendor_id:
            vend_use = ap.get("vendor_counts", {}).get(ln.vendor_id, 0)

        per_line[ln.expense_line_id] = {
            "amount": amt,
            "log_amount": math.log1p(max(amt, 0.0)),
            "hour": float(hour),
            "dow": float(dow),
            "is_weekend": float(is_weekend),
            "is_night": float(is_night),
            "is_round_1000": 1.0 if (amt and amt % 1000 == 0) else 0.0,
            "headcount": float(heads),
            "amount_per_head": per_head,
            "amount_z_in_category": ((amt - cs.get("amount_mean", 0.0)) / cat_std) if cat_std > 0 else 0.0,
            "amount_z_for_applicant": ((amt - ap.get("amount_mean", 0.0)) / appl_std) if appl_std > 0 else 0.0,
            "vendor_use_by_applicant": float(vend_use),
        }

    return FeatureSet(
        per_line=per_line,
        applicant_profiles=applicant_profiles,
        category_stats=category_stats,
        global_first_digit=first_digit_by_applicant,
    )
