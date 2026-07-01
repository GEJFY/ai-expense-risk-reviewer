"""テスト共通フィクスチャ。"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from expense_risk.config import load_engagement_config

FIXED_CLOCK = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def fixed_clock() -> datetime:
    return FIXED_CLOCK


@pytest.fixture
def demo_engagement() -> dict:
    """全コネクタを有効化した構成（同意・法的基盤を充足した想定）。"""
    eng = copy.deepcopy(load_engagement_config())
    conns = eng.get("connectors", {})
    for cfg in conns.values():
        cfg["enabled"] = True
        if cfg.get("sensitive") and not cfg.get("legal_basis_ref"):
            cfg["legal_basis_ref"] = "test-consulted"
    return eng
