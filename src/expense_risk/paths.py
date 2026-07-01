"""リポジトリ内の標準パス解決.

深い仕様は `docs/`、機械可読の定義は `config/` にある（CLAUDE.md のリポジトリ構成）。
実装はこれらを実行時に読み込むため、場所を一箇所で解決できるようにする。
環境変数 ``EXPENSE_RISK_CONFIG_DIR`` で `config/` を上書き可能（テスト・別テナント用）。
"""

from __future__ import annotations

import os
from pathlib import Path

# src/expense_risk/paths.py -> parents[2] がリポジトリルート
REPO_ROOT: Path = Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    """`config/` ディレクトリ。環境変数で上書き可能。"""
    override = os.environ.get("EXPENSE_RISK_CONFIG_DIR")
    return Path(override) if override else REPO_ROOT / "config"


def rule_catalog_path() -> Path:
    return config_dir() / "rules" / "rule_catalog.yaml"


def fraud_scenarios_path() -> Path:
    return config_dir() / "rules" / "fraud_scenarios.yaml"


def data_contracts_path() -> Path:
    return config_dir() / "schemas" / "data_contracts.json"


def engagement_config_path() -> Path:
    """エンゲージメント構成（engagement_mode / コネクタ法的基盤）。"""
    return config_dir() / "engagement.yaml"


def docs_dir() -> Path:
    return REPO_ROOT / "docs"
