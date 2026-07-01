"""コマンドラインインタフェース（run / demo）.

    expense-risk run  --input <明細.json> [--masters m.json] [--out out/]
    expense-risk demo [--n-normal 60] [--fraud-per 2] [--seed 0] [--out out/]

- ``run``  : 実データ（JSON）を分析しレポート一式を生成する。
- ``demo`` : 合成不正データを生成→分析→検出力（Recall/Precision）を評価する
             （証憑コネクタは「同意・労使協議済み」を想定して有効化した構成で走る）。

最終判断は必ず監査人（人間）が行う（HITL）。本CLIは所見の提示までを担う。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from .config import load_engagement_config
from .governance import IndependenceError
from .pipeline import PipelineConfig, run_pipeline
from .report import write_reports
from .synthetic import evaluate, generate_dataset


def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _demo_engagement(base: dict[str, Any]) -> dict[str, Any]:
    """全コネクタを有効化した構成（demo。同意・労使協議・法的基盤を充足した想定）。"""
    eng = dict(base)
    conns = dict(eng.get("connectors", {}))
    for tool, cfg in list(conns.items()):
        cfg = dict(cfg)
        cfg["enabled"] = True
        if cfg.get("sensitive") and not cfg.get("legal_basis_ref"):
            cfg["legal_basis_ref"] = "demo-consulted"
        conns[tool] = cfg
    eng["connectors"] = conns
    return eng


def _print_summary(result: Any, paths: dict[str, str]) -> None:
    st = result.stats
    print("\n=== 分析サマリ ===")
    print(f"総明細数            : {st['total_lines']}")
    print(f"escalate（深掘り）  : {st['by_triage'].get('escalate', 0)}")
    print(f"review（人手確認）  : {st['by_triage'].get('review', 0)}")
    print(f"auto_dismiss        : {st['by_triage'].get('auto_dismiss', 0)}")
    print(f"重大度 critical/high: {st['by_severity'].get('critical', 0)} / {st['by_severity'].get('high', 0)}")
    print(f"engagement_mode     : {result.governance.engagement_mode}")
    print(f"model_version       : {result.governance.model_version}")
    ok = result.audit.verify_chain()[0]
    print(f"監査ログ            : {len(result.audit)} 件 / チェーン整合性 {'OK' if ok else '改ざん検知'}")
    print("\n=== 生成レポート ===")
    for name, p in paths.items():
        print(f"  {name:18}: {p}")
    print("\n※ 最終判断は監査人（人間）が行います（HITL）。")


def cmd_run(args: argparse.Namespace) -> int:
    records = _load_json(args.input)
    if not isinstance(records, list):
        print("エラー: --input は明細(dict)の配列(JSON)である必要があります。", file=sys.stderr)
        return 2
    masters = _load_json(args.masters) if args.masters else None
    control = _load_json(args.control_totals) if args.control_totals else None
    engagement = _load_json(args.engagement) if args.engagement else load_engagement_config()

    cfg = PipelineConfig(
        ml_method=args.ml_method,
        enable_agent=not args.no_agent,
        allow_sensitive_approved=args.approve_sensitive,
        agent_triage_levels=tuple(args.agent_triage),
    )
    try:
        result = run_pipeline(records, masters=masters, control_totals=control,
                              engagement_config=engagement, config=cfg,
                              audit_path=str(Path(args.out) / "audit_log.jsonl"))
    except IndependenceError as e:
        print(f"独立性ゲートによりブロック: {e}", file=sys.stderr)
        return 3
    paths = write_reports(result, args.out)
    _print_summary(result, paths)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    ds = generate_dataset(n_normal=args.n_normal, fraud_per_scenario=args.fraud_per, seed=args.seed)
    engagement = _demo_engagement(load_engagement_config())
    cfg = PipelineConfig(allow_sensitive_approved=True, agent_triage_levels=("escalate", "review"))
    result = run_pipeline(ds.records, masters=ds.masters, engagement_config=engagement, config=cfg,
                          audit_path=str(Path(args.out) / "audit_log.jsonl"))
    paths = write_reports(result, args.out)
    metrics = evaluate(result.findings, ds.labels)
    _print_summary(result, paths)
    print("\n=== 合成不正 検出力評価（テスト戦略 §9）===")
    print(f"注入不正 {metrics['n_fraud']} 件 / 正常 {metrics['n_normal']} 件")
    print(f"Recall（捕捉率）        : {metrics['recall']}")
    print(f"Precision               : {metrics['precision']}")
    print(f"False Positive Rate     : {metrics['false_positive_rate']}")
    if metrics["missed"]:
        print(f"見逃し                  : {metrics['missed']}")
    print("シナリオ別 Recall:")
    for tag, v in metrics["by_scenario"].items():
        print(f"  {tag:12}: {v['caught']}/{v['total']} (recall={v['recall']})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="expense-risk", description="経費不正リスク分析 自律AIエージェント")
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("run", help="実データ(JSON)を分析しレポートを生成")
    pr.add_argument("--input", required=True, help="経費明細 JSON（dict の配列）")
    pr.add_argument("--masters", help="マスタ JSON（employees/vendors/policy）")
    pr.add_argument("--control-totals", help="完全性照合の管理値 JSON（count/amount_sum）")
    pr.add_argument("--engagement", help="engagement 構成 JSON（未指定なら config/engagement.yaml）")
    pr.add_argument("--out", default="out", help="レポート出力ディレクトリ（既定: out）")
    pr.add_argument("--ml-method", default="isolation_forest", choices=["isolation_forest", "pca", "lof"])
    pr.add_argument("--no-agent", action="store_true", help="エージェント深掘りを無効化")
    pr.add_argument("--approve-sensitive", action="store_true", help="機微コネクタの G1 事前承認済みとする")
    pr.add_argument("--agent-triage", nargs="+", default=["escalate"],
                    choices=["escalate", "review"], help="深掘り対象トリアージ（既定: escalate）")
    pr.set_defaults(func=cmd_run)

    pd = sub.add_parser("demo", help="合成不正を生成→分析→検出力を評価")
    pd.add_argument("--n-normal", type=int, default=60)
    pd.add_argument("--fraud-per", type=int, default=2)
    pd.add_argument("--seed", type=int, default=0)
    pd.add_argument("--out", default="out", help="レポート出力ディレクトリ（既定: out）")
    pd.set_defaults(func=cmd_demo)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
