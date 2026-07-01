"""データ契約（モジュール間 I/O）.

すべてのモジュール間の受け渡しは ``config/schemas/data_contracts.json``
（JSON Schema）に準拠する。ここでは Python 側の型（dataclass）と、スキーマへの
実行時検証を提供する。4つの型がある:

- ``ExpenseLine``  — 入力の経費明細（分析対象の最小単位）
- ``RiskFinding``  — 出力の所見。根拠（``rationale`` ／説明可能性）が必須。
                     AI は ``hitl_status`` を自分で ``confirmed`` にできない。
- ``Evidence``     — 収集した証憑。出所（``provenance``）とインジェクション検査
                     結果（``injection_flags``）を保持する。
- ``AuditLogEntry``— 監査ログ。WORM＋ハッシュチェーン（``audit.log`` が生成）。

設計原則: スキーマ違反は「除外」ではなく「明示」する（``data_quality`` フラグ）。
根拠なきスコア・所見は出さない（``rationale`` は必須項目）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

from jsonschema import Draft7Validator

from .paths import data_contracts_path

# ---------------------------------------------------------------------------
# JSON Schema の読み込みと検証
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _schema_document() -> dict[str, Any]:
    with open(data_contracts_path(), encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=None)
def _validator_for(definition: str) -> Draft7Validator:
    doc = _schema_document()
    if definition not in doc.get("definitions", {}):
        raise KeyError(f"未知のスキーマ定義: {definition}")
    # ルート文書に definitions を含めることで "#/definitions/X" が解決される。
    schema = {"$ref": f"#/definitions/{definition}", "definitions": doc["definitions"]}
    return Draft7Validator(schema)


def validation_errors(instance: dict[str, Any], definition: str) -> list[str]:
    """``instance`` を指定定義で検証し、エラーメッセージ一覧を返す（空なら適合）。"""
    validator = _validator_for(definition)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]


def is_valid(instance: dict[str, Any], definition: str) -> bool:
    return not validation_errors(instance, definition)


def assert_valid(instance: dict[str, Any], definition: str) -> None:
    errs = validation_errors(instance, definition)
    if errs:
        raise ValueError(f"{definition} スキーマ違反: " + "; ".join(errs))


def _compact(d: dict[str, Any]) -> dict[str, Any]:
    """None の任意フィールドを落として出力を整える（必須は呼び出し側で保証）。"""
    return {k: v for k, v in d.items() if v is not None}


# ---------------------------------------------------------------------------
# ExpenseLine（入力）
# ---------------------------------------------------------------------------

# スキーマで明示定義された ExpenseLine の既知フィールド。
# これ以外の入力キー（approver_authority_limit 等、ルールが参照する拡張列）は
# `extra` に保持する（スキーマは additionalProperties を禁止していない）。
_EXPENSE_KNOWN_FIELDS = (
    "expense_line_id", "applicant_id", "approver_id", "transaction_date",
    "transaction_datetime", "entry_timestamp", "approval_timestamp", "amount",
    "currency", "expense_category", "payment_method", "vendor_id", "vendor_name",
    "vendor_address", "department", "description", "participants", "location",
    "receipt_image", "approval_limit", "category_limit", "estimate_flag",
    "source_system",
)


@dataclass
class ExpenseLine:
    """経費精算明細の1行（分析対象の最小単位）。"""

    expense_line_id: str
    applicant_id: str
    transaction_date: str
    amount: float
    currency: str = "JPY"
    expense_category: str = ""
    approver_id: Optional[str] = None
    transaction_datetime: Optional[str] = None
    entry_timestamp: Optional[str] = None
    approval_timestamp: Optional[str] = None
    payment_method: Optional[str] = None
    vendor_id: Optional[str] = None
    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    department: Optional[str] = None
    description: Optional[str] = None
    participants: Optional[list[str]] = None
    location: Optional[dict[str, Any]] = None
    receipt_image: Optional[str] = None
    approval_limit: Optional[float] = None
    category_limit: Optional[float] = None
    estimate_flag: Optional[bool] = None
    source_system: Optional[str] = None
    # スキーマ外の拡張入力列（ルールが参照する: workflow_log, vendor_first_seen 等）
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExpenseLine":
        known = {k: d[k] for k in _EXPENSE_KNOWN_FIELDS if k in d}
        extra = {k: v for k, v in d.items() if k not in _EXPENSE_KNOWN_FIELDS and k != "extra"}
        # required の最低限を補完（欠損は ETL の data-quality ゲートで検出する）
        known.setdefault("expense_line_id", str(d.get("expense_line_id", "")))
        known.setdefault("applicant_id", str(d.get("applicant_id", "")))
        known.setdefault("transaction_date", str(d.get("transaction_date", "")))
        known.setdefault("amount", d.get("amount", 0))
        return cls(extra=extra, **known)

    def get(self, key: str, default: Any = None) -> Any:
        """既知フィールド → extra の順で値を引く（ルール評価器の統一アクセス）。"""
        if key in _EXPENSE_KNOWN_FIELDS and hasattr(self, key):
            val = getattr(self, key)
            return val if val is not None else self.extra.get(key, default)
        return self.extra.get(key, default)

    def to_dict(self) -> dict[str, Any]:
        base = {
            "expense_line_id": self.expense_line_id,
            "applicant_id": self.applicant_id,
            "approver_id": self.approver_id,
            "transaction_date": self.transaction_date,
            "transaction_datetime": self.transaction_datetime,
            "entry_timestamp": self.entry_timestamp,
            "approval_timestamp": self.approval_timestamp,
            "amount": self.amount,
            "currency": self.currency,
            "expense_category": self.expense_category,
            "payment_method": self.payment_method,
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "vendor_address": self.vendor_address,
            "department": self.department,
            "description": self.description,
            "participants": self.participants,
            "location": self.location,
            "receipt_image": self.receipt_image,
            "approval_limit": self.approval_limit,
            "category_limit": self.category_limit,
            "estimate_flag": self.estimate_flag,
            "source_system": self.source_system,
        }
        base = _compact(base)
        base.update(self.extra)
        return base


# ---------------------------------------------------------------------------
# RiskFinding（出力）
# ---------------------------------------------------------------------------


@dataclass
class MatchedRule:
    rule_id: str
    weight: float
    detail_ja: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return _compact({"rule_id": self.rule_id, "weight": self.weight, "detail_ja": self.detail_ja})


@dataclass
class ShapFeature:
    feature: str
    contribution: float

    def to_dict(self) -> dict[str, Any]:
        return {"feature": self.feature, "contribution": self.contribution}


@dataclass
class MlAttribution:
    model: str  # isolation_forest | lof | autoencoder | pca
    anomaly_score: float
    shap_top_features: list[ShapFeature] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "anomaly_score": self.anomaly_score,
            "shap_top_features": [f.to_dict() for f in self.shap_top_features],
        }


@dataclass
class Hypothesis:
    scenario_id: str
    hypothesis_ja: str
    verdict: str  # supported | refuted | inconclusive

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "hypothesis_ja": self.hypothesis_ja,
            "verdict": self.verdict,
        }


@dataclass
class Rationale:
    matched_rules: list[MatchedRule] = field(default_factory=list)
    ml_attribution: Optional[MlAttribution] = None
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched_rules": [r.to_dict() for r in self.matched_rules],
            "ml_attribution": self.ml_attribution.to_dict() if self.ml_attribution else None,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass
class RiskFinding:
    finding_id: str
    expense_line_id: str
    risk_score: float
    severity: str  # low | medium | high | critical
    triage: str    # auto_dismiss | review | escalate
    rationale: Rationale
    model_version: str
    generated_at: str
    hypotheses: list[Hypothesis] = field(default_factory=list)
    recommended_action_ja: Optional[str] = None
    data_quality: Optional[list[str]] = None
    hitl_status: str = "pending"  # AI は自分で confirmed にできない
    engagement_mode: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "finding_id": self.finding_id,
            "expense_line_id": self.expense_line_id,
            "risk_score": self.risk_score,
            "severity": self.severity,
            "triage": self.triage,
            "rationale": self.rationale.to_dict(),
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "recommended_action_ja": self.recommended_action_ja,
            "data_quality": self.data_quality,
            "hitl_status": self.hitl_status,
            "model_version": self.model_version,
            "engagement_mode": self.engagement_mode,
            "generated_at": self.generated_at,
        }
        return _compact(d)


# ---------------------------------------------------------------------------
# Evidence（証憑）
# ---------------------------------------------------------------------------


@dataclass
class Provenance:
    collected_by_role: str
    access_scope: str
    legal_basis_ref: Optional[str] = None
    retention_until: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return _compact({
            "collected_by_role": self.collected_by_role,
            "access_scope": self.access_scope,
            "legal_basis_ref": self.legal_basis_ref,
            "retention_until": self.retention_until,
        })


@dataclass
class Evidence:
    evidence_id: str
    type: str
    source: str
    collected_at: str
    provenance: Provenance
    content: dict[str, Any] = field(default_factory=dict)
    ocr_confidence: Optional[float] = None
    injection_flags: Optional[list[str]] = None

    def to_dict(self) -> dict[str, Any]:
        return _compact({
            "evidence_id": self.evidence_id,
            "type": self.type,
            "source": self.source,
            "collected_at": self.collected_at,
            "content": self.content,
            "ocr_confidence": self.ocr_confidence,
            "injection_flags": self.injection_flags,
            "provenance": self.provenance.to_dict(),
        })


# ---------------------------------------------------------------------------
# AuditLogEntry（監査ログ）
# ---------------------------------------------------------------------------


@dataclass
class AuditLogEntry:
    log_id: str
    timestamp: str
    phase: str   # observe | hypothesize | explore | verify | integrate | hitl
    actor: str   # agent | tool:<name> | human:<role>
    action: str
    prev_hash: str
    hash: str
    finding_id: Optional[str] = None
    expense_line_id: Optional[str] = None
    inputs: Optional[dict[str, Any]] = None
    outputs: Optional[dict[str, Any]] = None
    termination_reason: Optional[str] = None
    model_version: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return _compact({
            "log_id": self.log_id,
            "timestamp": self.timestamp,
            "finding_id": self.finding_id,
            "expense_line_id": self.expense_line_id,
            "phase": self.phase,
            "actor": self.actor,
            "action": self.action,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "termination_reason": self.termination_reason,
            "model_version": self.model_version,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        })
