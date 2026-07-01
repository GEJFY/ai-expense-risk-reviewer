"""機械学習による異常検知（多数派から外れた明細を数値化）.

「健康診断で基準値を外れた項目に印がつく」のと同じ発想（onboarding-explainer）。
費目・申請者で正規化した特徴量空間で、多数派から外れた明細を異常スコア化する。

- **モデル**: Isolation Forest を主とし、PCA 再構成誤差・LOF を補助として併用できる
  （docs/architecture.md L3、governance のモデルリスク管理対象）。
- **説明可能性（必須）**: 各異常所見に寄与要因（どの特徴量がスコアを押し上げたか）を
  添える。ここでは標準化偏差ベースの**透明な寄与度**を算出する（重い SHAP ライブラリに
  依存せず、決定論的に再現できる近似）。根拠なきスコアは出さない。
- **再現性**: ``random_state`` 固定で同じ入力なら同じスコア（モデルリスク管理・監査対応）。

注: 本実装の寄与度は SHAP の厳密値ではなく、標準化偏差に基づく透明な近似である点を
明示する（過大主張を避ける）。本番では SHAP 等への差し替えを想定した差込口とする。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from ..contracts import MlAttribution, ShapFeature
from ..features import NUMERIC_FEATURES, FeatureSet

# スキーマ enum: isolation_forest | lof | autoencoder | pca
SUPPORTED_MODELS = ("isolation_forest", "pca", "lof")
MIN_SAMPLES = 8  # これ未満は統計的に不安定なため ML をスキップ（決定論ルールに委ねる）


@dataclass
class AnomalyResult:
    """明細ごとの異常スコアと寄与要因。"""

    model: str
    per_line: dict[str, dict[str, Any]] = field(default_factory=dict)  # lid -> {anomaly_score, top_features}
    skipped: bool = False
    reason: Optional[str] = None

    def attribution_for(self, expense_line_id: str) -> Optional[MlAttribution]:
        """RiskFinding.rationale.ml_attribution に載せる寄与を返す（無ければ None）。"""
        row = self.per_line.get(expense_line_id)
        if not row:
            return None
        return MlAttribution(
            model=self.model,
            anomaly_score=round(float(row["anomaly_score"]), 4),
            shap_top_features=[
                ShapFeature(feature=f, contribution=round(float(c), 4)) for f, c in row["top_features"]
            ],
        )

    def score_for(self, expense_line_id: str) -> float:
        row = self.per_line.get(expense_line_id)
        return float(row["anomaly_score"]) if row else 0.0


def _minmax(values: np.ndarray) -> np.ndarray:
    lo, hi = float(values.min()), float(values.max())
    if hi - lo < 1e-12:
        return np.zeros_like(values)
    return (values - lo) / (hi - lo)


def _raw_scores(method: str, X: np.ndarray) -> np.ndarray:
    """手法ごとの生の異常度（高いほど異常）を返す。"""
    if method == "isolation_forest":
        clf = IsolationForest(n_estimators=200, contamination="auto", random_state=42)
        clf.fit(X)
        return -clf.score_samples(X)  # score_samples は高いほど正常 → 反転
    if method == "lof":
        from sklearn.neighbors import LocalOutlierFactor

        n_neighbors = min(20, max(2, X.shape[0] - 1))
        lof = LocalOutlierFactor(n_neighbors=n_neighbors)
        lof.fit_predict(X)
        return -lof.negative_outlier_factor_  # 高いほど異常
    if method == "pca":
        from sklearn.decomposition import PCA

        n_comp = min(X.shape[1], max(1, X.shape[0] - 1))
        pca = PCA(n_components=n_comp, random_state=42)
        recon = pca.inverse_transform(pca.fit_transform(X))
        return np.sqrt(((X - recon) ** 2).sum(axis=1))  # 再構成誤差
    raise ValueError(f"未対応のモデル: {method}")


def detect_anomalies(
    features: FeatureSet,
    method: str = "isolation_forest",
    top_k: int = 3,
) -> AnomalyResult:
    """特徴量から異常スコアと寄与要因を算出する。

    Args:
        features: L2 の特徴量セット。
        method: ``isolation_forest`` | ``pca`` | ``lof``。
        top_k: 寄与要因の上位表示数。
    """
    if method not in SUPPORTED_MODELS:
        raise ValueError(f"未対応のモデル: {method}（{SUPPORTED_MODELS} のいずれか）")

    ids, X = features.matrix()
    if len(ids) < MIN_SAMPLES:
        return AnomalyResult(model=method, skipped=True,
                             reason=f"サンプル数 {len(ids)} < {MIN_SAMPLES}（ML をスキップ）")

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)

    raw = _raw_scores(method, Xs)
    scores = _minmax(np.asarray(raw, dtype=float))

    per_line: dict[str, dict[str, Any]] = {}
    for i, lid in enumerate(ids):
        # 寄与度: 標準化偏差の大きい特徴量ほどスコアを押し上げたとみなす（透明な近似）
        z = Xs[i]
        order = np.argsort(-np.abs(z))[:top_k]
        top_features = [(NUMERIC_FEATURES[j], float(z[j])) for j in order if abs(z[j]) > 1e-9]
        per_line[lid] = {"anomaly_score": float(scores[i]), "top_features": top_features}

    return AnomalyResult(model=method, per_line=per_line)
