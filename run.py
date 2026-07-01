#!/usr/bin/env python3
"""ルートからの実行エントリ（`python run.py demo` / `python run.py run --input ...`）.

パッケージをインストールせずに動かせるよう src をパスに追加して CLI を呼ぶ。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from expense_risk.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
