"""L1: データ取込・ETL（データ品質・完全性照合）.

「全件網羅」を主張する以上、対象データの完全性を証明できねばならない
（docs/architecture.md §4）。ここでは:

- **取込**: JSON/dict のリストを ``ExpenseLine`` へ正規化する。
- **データ品質ゲート**: 必須項目欠落・型不整合・参照整合性違反を検出し、該当明細に
  ``data_quality`` フラグを付す（**除外ではなく明示**）。
- **完全性照合**: 取込件数・金額合計を管理値（control totals）と突合し、欠損・重複・
  期ズレの兆候を検出して記録する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .contracts import ExpenseLine, validation_errors
from .util import parse_date

# データ品質フラグのコード（安定した識別子。所見の data_quality に載る）
DQ_SCHEMA_VIOLATION = "schema_violation"
DQ_AMOUNT_NON_POSITIVE = "amount_non_positive"
DQ_UNPARSEABLE_DATE = "unparseable_date"
DQ_UNKNOWN_APPLICANT = "unknown_applicant"
DQ_UNKNOWN_VENDOR = "unknown_vendor"
DQ_DUPLICATE_LINE_ID = "duplicate_line_id"


@dataclass
class Reconciliation:
    """完全性照合の結果（管理値との突合）。"""

    ingested_count: int
    ingested_amount_sum: float
    control_count: Optional[int] = None
    control_amount_sum: Optional[float] = None
    count_matches: Optional[bool] = None
    amount_matches: Optional[bool] = None
    duplicate_line_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ingested_count": self.ingested_count,
            "ingested_amount_sum": round(self.ingested_amount_sum, 2),
            "control_count": self.control_count,
            "control_amount_sum": self.control_amount_sum,
            "count_matches": self.count_matches,
            "amount_matches": self.amount_matches,
            "duplicate_line_ids": self.duplicate_line_ids,
        }


@dataclass
class IngestResult:
    lines: list[ExpenseLine]
    data_quality: dict[str, list[str]]  # expense_line_id -> フラグ
    reconciliation: Reconciliation

    def flags_for(self, expense_line_id: str) -> list[str]:
        return self.data_quality.get(expense_line_id, [])


def ingest(
    records: list[dict[str, Any]],
    *,
    masters: Optional[dict[str, Any]] = None,
    control_totals: Optional[dict[str, Any]] = None,
) -> IngestResult:
    """明細レコード群を取り込み、品質フラグと完全性照合を付す。

    Args:
        records: 経費明細（dict）のリスト。
        masters: ``{"employees": {id: {...}}, "vendors": {id: {...}}}`` 等の参照マスタ。
        control_totals: ``{"count": N, "amount_sum": X}`` 会計/ERP 側の管理値。
    """
    masters = masters or {}
    employees = masters.get("employees", {}) or {}
    vendors = masters.get("vendors", {}) or {}

    lines: list[ExpenseLine] = []
    data_quality: dict[str, list[str]] = {}
    seen_ids: dict[str, int] = {}
    amount_sum = 0.0

    for idx, rec in enumerate(records):
        line = ExpenseLine.from_dict(rec)
        # id が空なら安定キーを付与（照合・追跡のため）
        lid = line.expense_line_id or f"__row_{idx}"
        if not line.expense_line_id:
            line.expense_line_id = lid
        flags: list[str] = []

        # 重複 ID
        seen_ids[lid] = seen_ids.get(lid, 0) + 1
        if seen_ids[lid] > 1:
            flags.append(DQ_DUPLICATE_LINE_ID)

        # スキーマ適合（型・必須）
        schema_errs = validation_errors(line.to_dict(), "ExpenseLine")
        if schema_errs:
            flags.append(DQ_SCHEMA_VIOLATION)

        # 金額の妥当性
        try:
            amt = float(line.amount)
            amount_sum += amt
            if amt <= 0:
                flags.append(DQ_AMOUNT_NON_POSITIVE)
        except (TypeError, ValueError):
            flags.append(DQ_AMOUNT_NON_POSITIVE)

        # 日付のパース可否
        if parse_date(line.transaction_date) is None:
            flags.append(DQ_UNPARSEABLE_DATE)

        # 参照整合性（マスタがあるときのみ）
        if employees and line.applicant_id not in employees:
            flags.append(DQ_UNKNOWN_APPLICANT)
        if vendors and line.vendor_id and line.vendor_id not in vendors:
            flags.append(DQ_UNKNOWN_VENDOR)

        lines.append(line)
        if flags:
            # 重複IDでも各行のフラグを取りこぼさないよう蓄積（上書きしない）
            existing = data_quality.setdefault(lid, [])
            for fl in flags:
                if fl not in existing:
                    existing.append(fl)

    duplicate_ids = sorted([lid for lid, n in seen_ids.items() if n > 1])
    recon = Reconciliation(
        ingested_count=len(lines),
        ingested_amount_sum=amount_sum,
        duplicate_line_ids=duplicate_ids,
    )
    if control_totals:
        recon.control_count = control_totals.get("count")
        recon.control_amount_sum = control_totals.get("amount_sum")
        if recon.control_count is not None:
            recon.count_matches = recon.control_count == recon.ingested_count
        if recon.control_amount_sum is not None:
            recon.amount_matches = abs(float(recon.control_amount_sum) - amount_sum) < 1e-6

    return IngestResult(lines=lines, data_quality=data_quality, reconciliation=recon)
