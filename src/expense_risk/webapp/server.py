"""HITL コンソールの FastAPI バックエンド.

起動時に実務規模の合成データ（demo_data）へパイプラインを実行し、結果を保持する。
監査人は所見をレビューし、確定/棄却/追加調査を判断する（HITL）。その判断は監査ログに
追記され、ハッシュチェーンが継続する。**AI は hitl_status を confirmed にできない** ──
確定は本エンドポイント（人間の操作）だけが行える、という不変条件をここで体現する。

独立性・ブランド配慮: 本ツールは所見の「提示」に留まり、成果や数値を保証・誇張しない。
検出力の数値は合成データでの評価（参考値）として明示する。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import load_engagement_config, load_fraud_scenarios, load_rule_catalog
from ..demo_data import generate_realistic
from ..pipeline import PipelineConfig, run_pipeline
from ..synthetic import evaluate

_STATIC = Path(__file__).parent / "static"
_ANALYSIS_CLOCK = datetime(2026, 4, 1, tzinfo=timezone.utc)

# HITL 判断 → hitl_status のマッピング
_DECISION_MAP = {"confirm": "confirmed", "dismiss": "dismissed", "investigate": "needs_more"}
_DECISION_LABEL = {"confirm": "確定", "dismiss": "棄却", "investigate": "追加調査"}


class DecisionBody(BaseModel):
    decision: str                     # confirm | dismiss | investigate
    note: Optional[str] = None
    reviewer: str = "auditor"


def _demo_engagement() -> dict[str, Any]:
    """同意・労使協議・法的基盤を充足した想定の構成（全コネクタ有効）。"""
    eng = dict(load_engagement_config())
    conns = {k: dict(v) for k, v in eng.get("connectors", {}).items()}
    for cfg in conns.values():
        cfg["enabled"] = True
        if cfg.get("sensitive") and not cfg.get("legal_basis_ref"):
            cfg["legal_basis_ref"] = "consulted-2026"
    eng["connectors"] = conns
    return eng


class AppState:
    """解析結果と HITL 判断を保持するインメモリ状態。"""

    def __init__(self) -> None:
        self.catalog = load_rule_catalog()
        self.scenarios = load_fraud_scenarios()
        self.build()

    def build(self) -> None:
        ds = generate_realistic()
        self.company = ds.company_name
        self.labels = ds.labels
        cfg = PipelineConfig(allow_sensitive_approved=True, agent_triage_levels=("escalate", "review"))
        self.result = run_pipeline(
            ds.records, masters=ds.masters, engagement_config=_demo_engagement(),
            config=cfg, clock=_ANALYSIS_CLOCK,
        )
        self.lines_by_id = {ln.expense_line_id: ln for ln in self.result.lines}
        self.emp = ds.masters["employees"]
        self.evidence_by_id = {e.evidence_id: e for e in self.result.evidence}
        # 所見をUI向けにdict化（hitl_status は可変）
        self.findings = {f.finding_id: f for f in self.result.findings}
        self.hitl_notes: dict[str, dict[str, Any]] = {}
        self.metrics = evaluate(self.result.findings, self.labels)

    # --- 整形ヘルパ ---
    def _applicant_name(self, line) -> str:
        return self.emp.get(getattr(line, "applicant_id", ""), {}).get("name", getattr(line, "applicant_id", ""))

    def finding_summary(self, f) -> dict[str, Any]:
        line = self.lines_by_id.get(f.expense_line_id)
        return {
            "finding_id": f.finding_id,
            "expense_line_id": f.expense_line_id,
            "applicant": self._applicant_name(line),
            "department": getattr(line, "department", None),
            "transaction_date": getattr(line, "transaction_date", None),
            "amount": getattr(line, "amount", None),
            "expense_category": getattr(line, "expense_category", None),
            "vendor_name": getattr(line, "vendor_name", None),
            "risk_score": f.risk_score,
            "severity": f.severity,
            "triage": f.triage,
            "hitl_status": f.hitl_status,
            "top_rules": [m.rule_id for m in f.rationale.matched_rules[:4]],
        }

    def finding_detail(self, f) -> dict[str, Any]:
        line = self.lines_by_id.get(f.expense_line_id)
        rules = []
        for m in f.rationale.matched_rules:
            r = self.catalog.get(m.rule_id) or {}
            rules.append({
                "rule_id": m.rule_id, "weight": m.weight, "detail_ja": m.detail_ja,
                "name_ja": r.get("name_ja", ""), "severity": r.get("severity", ""),
                "category": self.catalog.category_name(r.get("category", "")),
                "fp_notes": r.get("false_positive_notes_ja", ""),
            })
        ml = f.rationale.ml_attribution
        evidence = []
        for eid in f.rationale.evidence_refs:
            ev = self.evidence_by_id.get(eid)
            if ev:
                evidence.append(ev.to_dict())
        return {
            **self.finding_summary(f),
            "currency": getattr(line, "currency", "JPY"),
            "payment_method": getattr(line, "payment_method", None),
            "participants": getattr(line, "participants", None),
            "approver": self.emp.get(getattr(line, "approver_id", ""), {}).get(
                "name", getattr(line, "approver_id", None)),
            "description": getattr(line, "description", None),
            "rationale_rules": rules,
            "ml_attribution": ml.to_dict() if ml else None,
            "hypotheses": [h.to_dict() for h in f.hypotheses],
            "evidence": evidence,
            "recommended_action_ja": f.recommended_action_ja,
            "data_quality": f.data_quality or [],
            "model_version": f.model_version,
            "engagement_mode": f.engagement_mode,
            "generated_at": f.generated_at,
            "hitl_note": self.hitl_notes.get(f.finding_id),
        }


def create_app() -> FastAPI:
    app = FastAPI(title="経費不正リスク分析 HITL コンソール", docs_url=None, redoc_url=None)
    state = AppState()
    app.state.data = state

    @app.get("/api/summary")
    def summary() -> Any:
        r = state.result
        chain_ok, _ = r.audit.verify_chain()
        cov = r.rule_coverage
        return {
            "company": state.company,
            "generated_at": r.findings[0].generated_at if r.findings else None,
            "engagement_mode": r.governance.engagement_mode,
            "model_version": r.governance.model_version,
            "stats": r.stats,
            "reconciliation": r.reconciliation,
            "coverage": {
                "total_rules": cov.get("total_rules"),
                "engine_implemented": len(cov.get("engine_implemented", [])),
                "engine_not_implemented": cov.get("engine_not_implemented", []),
                "agent_verified": len(cov.get("agent_verified_rules", [])),
                "ml_assisted": len(cov.get("ml_assisted_rules", [])),
            },
            "audit": {"entries": len(r.audit), "chain_ok": chain_ok},
            "validation": {  # 合成データでの検出力（参考値・性能保証ではない）
                "recall": state.metrics["recall"],
                "precision": state.metrics["precision"],
                "false_positive_rate": state.metrics["false_positive_rate"],
                "n_fraud": state.metrics["n_fraud"],
                "n_normal": state.metrics["n_normal"],
                "disclaimer": "合成データでの評価値であり、実環境の性能を保証するものではありません。",
            },
            "hitl_progress": _hitl_progress(state),
        }

    @app.get("/api/findings")
    def findings(triage: Optional[str] = None, severity: Optional[str] = None,
                 category: Optional[str] = None, q: Optional[str] = None,
                 hitl: Optional[str] = None) -> Any:
        out = []
        for f in sorted(state.findings.values(), key=lambda x: x.risk_score, reverse=True):
            s = state.finding_summary(f)
            if triage and s["triage"] != triage:
                continue
            if severity and s["severity"] != severity:
                continue
            if category and s["expense_category"] != category:
                continue
            if hitl and s["hitl_status"] != hitl:
                continue
            if q:
                hay = " ".join(str(v) for v in (s["applicant"], s["department"], s["vendor_name"],
                                                s["expense_line_id"], " ".join(s["top_rules"]))).lower()
                if q.lower() not in hay:
                    continue
            out.append(s)
        return {"count": len(out), "findings": out}

    @app.get("/api/findings/{finding_id}")
    def finding(finding_id: str) -> Any:
        f = state.findings.get(finding_id)
        if not f:
            raise HTTPException(404, "所見が見つかりません")
        return state.finding_detail(f)

    @app.post("/api/findings/{finding_id}/decision")
    def decide(finding_id: str, body: DecisionBody) -> Any:
        f = state.findings.get(finding_id)
        if not f:
            raise HTTPException(404, "所見が見つかりません")
        if body.decision not in _DECISION_MAP:
            raise HTTPException(400, "不正な判断です")
        f.hitl_status = _DECISION_MAP[body.decision]           # 人間だけがここを更新できる
        state.hitl_notes[finding_id] = {"decision": body.decision, "note": body.note,
                                        "reviewer": body.reviewer}
        # 監査ログに人間の判断を追記（チェーン継続）
        state.result.audit.append(
            "hitl", f"human:{body.reviewer}", f"所見を{_DECISION_LABEL[body.decision]}",
            finding_id=finding_id, expense_line_id=f.expense_line_id,
            inputs={"decision": body.decision, "note": body.note},
            outputs={"hitl_status": f.hitl_status}, clock=_ANALYSIS_CLOCK,
        )
        return state.finding_detail(f)

    @app.get("/api/audit")
    def audit(limit: int = 60, finding_id: Optional[str] = None) -> Any:
        entries = state.result.audit.to_list()
        if finding_id:
            entries = [e for e in entries if e.get("finding_id") == finding_id]
        chain_ok, problems = state.result.audit.verify_chain()
        return {"total": len(state.result.audit), "chain_ok": chain_ok, "problems": problems,
                "entries": entries[-limit:][::-1]}

    @app.post("/api/reset")
    def reset() -> Any:
        state.build()
        return {"ok": True}

    @app.get("/api/regmap")
    def regmap() -> Any:
        from ..governance import REGULATORY_MAP
        return {"map": REGULATORY_MAP}

    @app.get("/")
    def index() -> Any:
        return FileResponse(_STATIC / "index.html")

    @app.exception_handler(404)
    def _spa_fallback(request, exc):  # type: ignore[no-untyped-def]
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        return FileResponse(_STATIC / "index.html")

    app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")
    return app


def _hitl_progress(state: AppState) -> dict[str, int]:
    prog = {"pending": 0, "confirmed": 0, "dismissed": 0, "needs_more": 0}
    for f in state.findings.values():
        if f.triage in ("escalate", "review"):
            prog[f.hitl_status] = prog.get(f.hitl_status, 0) + 1
    return prog


app = create_app()
