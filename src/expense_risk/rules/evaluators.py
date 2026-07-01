"""ルール評価器（決定論的・統計的）と評価コンテキスト.

大原則: 検知ロジックは ``config/rules/rule_catalog.yaml`` に宣言的に定義し、コードは
それを読んで評価するだけにする（rule-authoring スキル）。ここでは各ルールIDに対応する
判定関数を実装する。カタログにあってもここに実装が無いルール（多くは agent_verified /
ml_assisted）は、エンジンが「未評価」として明示的に記録する（**暗黙の取りこぼしを作らない**）。

判定の数値パラメタ（閾値・窓幅）は現場調整できるよう定数化し、``RuleContext.params`` で
上書き可能にしている。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..contracts import ExpenseLine
from ..features import FeatureSet
from ..util import finite_float, is_round, parse_date, parse_datetime

# 統計ルールの既定パラメタ（クライアント別に調整可能）
DEFAULT_PARAMS: dict[str, float] = {
    "entry_lag_days": 30,            # TIME-003: 取引→入力の許容日数
    "limit_hug_ratio": 0.95,         # CTRL-002/AMT: 上限直下とみなす比率
    "limit_hug_min_count": 3,        # CTRL-002: 常態化とみなす件数
    "split_ratio": 0.8,              # DUP-003: 分割の下限比率
    "split_window_days": 7,          # DUP-003: 分割とみなす近接窓
    "split_min_count": 2,
    "structuring_ratio": 0.8,        # BEHV-006: スミングの下限比率
    "structuring_min_count": 3,
    "round_ratio_threshold": 0.6,    # AMT-001: キリ金額多発とみなす申請者比率
    "round_min_count": 3,
    "benford_min_n": 30,             # AMT-003: 検定に必要な最小件数
    "benford_chi2_crit": 15.507,     # df=8, p=0.05 のカイ二乗臨界値
    "perhead_z": 3.0,                # PART-003: 一人当たり金額の外れ値z
    "hhi_threshold": 0.5,            # VEND-002: 取引先集中(HHI)の閾値
    "hhi_min_count": 5,
}

_UNSPECIFIED_PARTICIPANT = re.compile(r"^\s*(他|ほか|その他)?\s*\d*\s*名?\s*$")
_UNSPECIFIED_TOKENS = {"", "-", "—", "不明", "なし", "未定", "n/a", "na"}


@dataclass
class RuleContext:
    """ルール評価に必要な明細群・特徴量・マスタ・事前計算インデックス。"""

    lines: list[ExpenseLine]
    features: FeatureSet
    masters: dict[str, Any] = field(default_factory=dict)
    params: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PARAMS))

    # 事前計算（build で埋める）
    lines_by_id: dict[str, ExpenseLine] = field(default_factory=dict)
    dup_index: dict[tuple, list[str]] = field(default_factory=dict)
    receipt_index: dict[str, list[str]] = field(default_factory=dict)
    employees: dict[str, Any] = field(default_factory=dict)
    vendors: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    internal_identifiers: set[str] = field(default_factory=set)
    retired_ids: set[str] = field(default_factory=set)

    def param(self, key: str) -> float:
        return self.params.get(key, DEFAULT_PARAMS[key])

    @classmethod
    def build(
        cls,
        lines: list[ExpenseLine],
        features: FeatureSet,
        masters: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, float]] = None,
    ) -> "RuleContext":
        masters = masters or {}
        merged = dict(DEFAULT_PARAMS)
        if params:
            merged.update(params)
        ctx = cls(lines=lines, features=features, masters=masters, params=merged)
        ctx.employees = masters.get("employees", {}) or {}
        ctx.vendors = masters.get("vendors", {}) or {}
        ctx.policy = masters.get("policy", {}) or {}

        # 社内識別子（PART-002 の社外/社内判定）: 従業員ID＋氏名
        idents: set[str] = set()
        for eid, emp in ctx.employees.items():
            idents.add(str(eid))
            name = (emp or {}).get("name")
            if name:
                idents.add(str(name))
            if (emp or {}).get("status") in ("retired", "退職", "resigned"):
                ctx.retired_ids.add(str(eid))
        ctx.internal_identifiers = idents

        for ln in lines:
            ctx.lines_by_id[ln.expense_line_id] = ln
            d = parse_date(ln.transaction_date)
            key = (d, ln.amount, ln.vendor_id)
            if ln.vendor_id is not None and d is not None:
                ctx.dup_index.setdefault(key, []).append(ln.expense_line_id)
            if ln.receipt_image:
                ctx.receipt_index.setdefault(str(ln.receipt_image), []).append(ln.expense_line_id)
        return ctx


# ---------------------------------------------------------------------------
# 決定論的評価器: (line, ctx) -> Optional[detail_ja]
# ---------------------------------------------------------------------------


def _ctrl_001(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    if line.approver_id and line.applicant_id and line.approver_id == line.applicant_id:
        return f"申請者と承認者が同一（{line.applicant_id}）＝自己承認"
    return None


def _ctrl_003(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    limit = line.get("approver_authority_limit")
    if limit is None and line.approver_id:
        limit = ctx.employees.get(line.approver_id, {}).get("authority_limit")
    if limit is None:
        return None
    try:
        if float(line.amount) > float(limit):
            return f"申請額 {line.amount:,.0f} が承認者の決裁権限 {float(limit):,.0f} を超過"
    except (TypeError, ValueError):
        return None
    return None


def _ctrl_004(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    appr = parse_date(line.approval_timestamp)
    txn = parse_date(line.transaction_date)
    if appr and txn and appr < txn:
        return f"承認日 {appr} が取引発生日 {txn} より前（日付逆転／前倒し承認の疑い）"
    return None


def _ctrl_006(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    steps = line.get("workflow_steps")
    required = line.get("required_approval_steps") or ctx.policy.get("required_approval_steps")
    if steps is None or required is None:
        return None
    try:
        if int(steps) < int(required):
            return f"承認段階 {steps} が規定 {required} 段階に不足（ワークフロー迂回の疑い）"
    except (TypeError, ValueError):
        return None
    return None


def _dup_001(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    d = parse_date(line.transaction_date)
    key = (d, line.amount, line.vendor_id)
    ids = ctx.dup_index.get(key, [])
    if len(ids) > 1:
        others = [i for i in ids if i != line.expense_line_id]
        return f"同一(日付/金額/取引先)の申請が {len(ids)} 件重複（{', '.join(others[:3])} 等）"
    return None


def _dup_002(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    ledger = ctx.masters.get("card_ledger")
    if not ledger:
        return None
    if line.payment_method not in (None, "cash", "invoice", "other"):
        return None  # カード明細そのものは対象外（現金/請求書精算側を疑う）
    d = parse_date(line.transaction_date)
    for entry in ledger:
        ed = parse_date(entry.get("transaction_date"))
        if ed is None or d is None:
            continue
        if abs((ed - d).days) <= 1 and abs(float(entry.get("amount", 0)) - float(line.amount)) < 1e-6:
            if not line.vendor_id or entry.get("vendor_id") in (None, line.vendor_id):
                return f"法人カード明細（{entry.get('id', '?')}）と同一支出の二重計上疑い"
    return None


def _dup_006(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    if not line.receipt_image:
        return None
    ids = ctx.receipt_index.get(str(line.receipt_image), [])
    if len(ids) > 1:
        others = [i for i in ids if i != line.expense_line_id]
        return f"同一領収書画像が {len(ids)} 明細で再利用（{', '.join(others[:3])} 等）"
    return None


def _amt_002(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    for field_name, label in (("category_limit", "費目上限"), ("approval_limit", "承認上限")):
        limit = getattr(line, field_name)
        if limit is not None:
            try:
                if abs(float(line.amount) - float(limit)) < 1e-6:
                    return f"申請額が{label} {float(limit):,.0f} にちょうど一致"
            except (TypeError, ValueError):
                continue
    return None


def _amt_007(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    if line.estimate_flag:
        return "概算/見積フラグのまま精算（後日調整の確認が必要）"
    return None


def _cons_003(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    threshold = line.get("receipt_required_threshold") or ctx.policy.get("receipt_required_threshold")
    if threshold is None:
        return None
    try:
        if float(line.amount) >= float(threshold) and not line.receipt_image:
            return f"領収書添付義務額 {float(threshold):,.0f} 以上だが領収書が欠落"
    except (TypeError, ValueError):
        return None
    return None


def _part_001(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    if line.expense_category not in ("交際費", "接待", "会議費"):
        return None
    parts = line.participants
    if not parts:
        # 空欄は接待系（参加者が実在性の核）でのみ検知。会議費の空欄は誤検知が多く許容。
        if line.expense_category in ("交際費", "接待"):
            return "交際費/接待だが参加者欄が空欄"
        return None
    if all(_is_unspecified(p) for p in parts):
        return f"参加者が不特定表現のみ（{', '.join(map(str, parts[:3]))}）"
    return None


def _part_002(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    if line.expense_category not in ("交際費", "接待"):
        return None
    if not line.participants or not ctx.internal_identifiers:
        return None
    if all(str(p) in ctx.internal_identifiers for p in line.participants):
        return "社外参加者がなく社内のみだが交際費（接待）計上（費目付替え/私的の疑い）"
    return None


def _time_001(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    dt = parse_datetime(line.transaction_datetime)
    if dt is None:
        return None
    reasons = []
    if dt.weekday() >= 5:
        reasons.append("休日")
    if dt.hour >= 22 or dt.hour < 5:
        reasons.append("深夜")
    if reasons:
        return f"{('・'.join(reasons))}に発生した経費（{dt.isoformat()}）"
    return None


def _time_003(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    entry = parse_date(line.entry_timestamp)
    txn = parse_date(line.transaction_date)
    if entry is None or txn is None:
        return None
    lag = abs((entry - txn).days)
    if lag > ctx.param("entry_lag_days"):
        return f"取引から入力まで {lag} 日（基準 {int(ctx.param('entry_lag_days'))} 日超）"
    return None


def _comm_001(line: ExpenseLine, ctx: RuleContext) -> Optional[str]:
    if line.expense_category != "通信・IT":
        return None
    subscriber = line.get("subscriber_id")
    status = line.get("employee_status")
    active = line.get("billing_active")
    is_retired = (subscriber in ctx.retired_ids) or (status in ("retired", "退職", "resigned"))
    if is_retired and (active is None or active):
        return f"退職者名義（{subscriber}）のサブスク/ライセンスが継続課金"
    return None


def _is_unspecified(token: Any) -> bool:
    s = str(token).strip().lower()
    if s in _UNSPECIFIED_TOKENS:
        return True
    return bool(_UNSPECIFIED_PARTICIPANT.match(str(token).strip()))


DETERMINISTIC_EVALUATORS: dict[str, Callable[[ExpenseLine, RuleContext], Optional[str]]] = {
    "CTRL-001": _ctrl_001,
    "CTRL-003": _ctrl_003,
    "CTRL-004": _ctrl_004,
    "CTRL-006": _ctrl_006,
    "DUP-001": _dup_001,
    "DUP-002": _dup_002,
    "DUP-006": _dup_006,
    "AMT-002": _amt_002,
    "AMT-007": _amt_007,
    "CONS-003": _cons_003,
    "PART-001": _part_001,
    "PART-002": _part_002,
    "TIME-001": _time_001,
    "TIME-003": _time_003,
    "COMM-001": _comm_001,
}


# ---------------------------------------------------------------------------
# 統計的評価器: (ctx) -> dict[expense_line_id -> detail_ja]
# ---------------------------------------------------------------------------


def _stat_ctrl_002(ctx: RuleContext) -> dict[str, str]:
    """上限ギリギリ申請の多発（同一申請者で上限95-100%が常態化）。"""
    ratio = ctx.param("limit_hug_ratio")
    min_count = int(ctx.param("limit_hug_min_count"))
    hug_by_applicant: dict[str, list[str]] = {}
    for ln in ctx.lines:
        if ln.approval_limit:
            try:
                r = float(ln.amount) / float(ln.approval_limit)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            if ratio <= r <= 1.0:
                hug_by_applicant.setdefault(ln.applicant_id, []).append(ln.expense_line_id)
    out: dict[str, str] = {}
    for appl, lids in hug_by_applicant.items():
        if len(lids) >= min_count:
            for lid in lids:
                out[lid] = f"承認上限の{int(ratio * 100)}-100%に張り付く申請が申請者 {appl} で {len(lids)} 件"
    return out


def _stat_dup_003(ctx: RuleContext) -> dict[str, str]:
    """上限回避のための分割（同一取引先・近接日に上限直下の申請が連続）。"""
    ratio = ctx.param("split_ratio")
    window = ctx.param("split_window_days")
    min_count = int(ctx.param("split_min_count"))
    groups: dict[tuple, list[tuple]] = {}
    for ln in ctx.lines:
        if not ln.vendor_id or not ln.approval_limit:
            continue
        try:
            r = float(ln.amount) / float(ln.approval_limit)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        d = parse_date(ln.transaction_date)
        if d is None or not (ratio <= r <= 1.0):
            continue
        groups.setdefault((ln.applicant_id, ln.vendor_id), []).append((d, ln.expense_line_id))
    out: dict[str, str] = {}
    for (appl, vendor), items in groups.items():
        items.sort()
        # 近接窓内に min_count 件以上あるか
        for i, (d0, _) in enumerate(items):
            window_ids = [lid for (d, lid) in items if 0 <= (d - d0).days <= window]
            if len(window_ids) >= min_count:
                for lid in window_ids:
                    out[lid] = f"取引先 {vendor} へ {window:.0f} 日内に上限直下の申請が {len(window_ids)} 件（分割疑い）"
                break
    return out


def _stat_behv_006(ctx: RuleContext) -> dict[str, str]:
    """閾値下の積み上げ（スミング）。領収書要否/承認閾値直下の小額を高頻度。"""
    ratio = ctx.param("structuring_ratio")
    min_count = int(ctx.param("structuring_min_count"))
    by_applicant: dict[str, list[str]] = {}
    for ln in ctx.lines:
        threshold = ln.get("receipt_required_threshold") or ctx.policy.get("receipt_required_threshold")
        if threshold is None:
            continue
        try:
            r = float(ln.amount) / float(threshold)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if ratio <= r < 1.0:
            by_applicant.setdefault(ln.applicant_id, []).append(ln.expense_line_id)
    out: dict[str, str] = {}
    for appl, lids in by_applicant.items():
        if len(lids) >= min_count:
            for lid in lids:
                out[lid] = f"閾値直下の小額申請が申請者 {appl} で {len(lids)} 件（スミング疑い）"
    return out


def _stat_amt_001(ctx: RuleContext) -> dict[str, str]:
    """キリのいい金額の多発（申請者のラウンド金額比率が高い）。"""
    thr = ctx.param("round_ratio_threshold")
    min_count = int(ctx.param("round_min_count"))
    out: dict[str, str] = {}
    for ln in ctx.lines:
        prof = ctx.features.applicant_profiles.get(ln.applicant_id, {})
        if prof.get("count", 0) < min_count:
            continue
        if prof.get("round_ratio", 0.0) >= thr:
            if is_round(finite_float(ln.amount)):
                out[ln.expense_line_id] = (
                    f"申請者 {ln.applicant_id} のキリ金額比率 {prof['round_ratio']:.0%}（水増し/概算の兆候）"
                )
    return out


def _stat_amt_003(ctx: RuleContext) -> dict[str, str]:
    """ベンフォードの法則からの乖離（申請者単位の先頭桁分布）。"""
    min_n = int(ctx.param("benford_min_n"))
    crit = ctx.param("benford_chi2_crit")
    expected = {d: math.log10(1 + 1 / d) for d in range(1, 10)}
    deviating: dict[str, float] = {}
    for appl, dist in ctx.features.global_first_digit.items():
        n = sum(dist.values())
        if n < min_n:
            continue
        chi2 = sum(((dist.get(d, 0) - n * p) ** 2) / (n * p) for d, p in expected.items())
        if chi2 > crit:
            deviating[appl] = chi2
    out: dict[str, str] = {}
    if not deviating:
        return out
    for ln in ctx.lines:
        if ln.applicant_id in deviating:
            out[ln.expense_line_id] = (
                f"申請者 {ln.applicant_id} の先頭桁分布がベンフォード期待から乖離（χ²={deviating[ln.applicant_id]:.1f}）"
            )
    return out


def _stat_part_003(ctx: RuleContext) -> dict[str, str]:
    """参加人数と金額の不整合（一人当たり金額が費目相場の外れ値）。"""
    z_thr = ctx.param("perhead_z")
    out: dict[str, str] = {}
    for ln in ctx.lines:
        if ln.expense_category not in ("交際費", "会議費", "接待"):
            continue
        heads = len(ln.participants) if ln.participants else 0
        if heads <= 0:
            continue
        cs = ctx.features.category_stats.get(ln.expense_category, {})
        std = cs.get("per_head_std", 0.0)
        if not std or not math.isfinite(std):  # NaN は `not std` を通過するため明示的に弾く
            continue
        per_head = finite_float(ln.amount) / heads
        z = (per_head - cs.get("per_head_mean", 0.0)) / std
        if abs(z) >= z_thr:
            out[ln.expense_line_id] = (
                f"一人当たり {per_head:,.0f} 円が費目相場から乖離（z={z:+.1f}、{heads}名）"
            )
    return out


def _stat_vend_002(ctx: RuleContext) -> dict[str, str]:
    """特定取引先への集中（申請者の取引先HHIが高い）。"""
    hhi_thr = ctx.param("hhi_threshold")
    min_count = int(ctx.param("hhi_min_count"))
    out: dict[str, str] = {}
    for ln in ctx.lines:
        if not ln.vendor_id:
            continue
        prof = ctx.features.applicant_profiles.get(ln.applicant_id, {})
        if prof.get("count", 0) < min_count:
            continue
        if prof.get("vendor_hhi", 0.0) >= hhi_thr:
            vc = prof.get("vendor_counts", {})
            top_vendor = max(vc, key=vc.get) if vc else None
            if ln.vendor_id == top_vendor:
                out[ln.expense_line_id] = (
                    f"申請者 {ln.applicant_id} の取引先集中(HHI={prof['vendor_hhi']:.2f})、主要先 {top_vendor}"
                )
    return out


STATISTICAL_EVALUATORS: dict[str, Callable[[RuleContext], dict[str, str]]] = {
    "CTRL-002": _stat_ctrl_002,
    "DUP-003": _stat_dup_003,
    "BEHV-006": _stat_behv_006,
    "AMT-001": _stat_amt_001,
    "AMT-003": _stat_amt_003,
    "PART-003": _stat_part_003,
    "VEND-002": _stat_vend_002,
}
