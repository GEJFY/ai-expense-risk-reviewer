#!/usr/bin/env python3
"""HITL コンソール（Web UI）の起動エントリ.

    python serve.py            # http://127.0.0.1:8000
    python serve.py --port 8080

起動時に実務規模の合成データへ解析を実行し、監査人向けの画面を提供する。
最終判断は必ず監査人（人間）が行う（HITL）。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main() -> None:
    p = argparse.ArgumentParser(description="経費不正リスク分析 HITL コンソール")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    args = p.parse_args()

    import uvicorn

    uvicorn.run("expense_risk.webapp.server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
