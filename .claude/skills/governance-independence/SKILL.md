---
name: governance-independence
description: Use for work involving auditor independence, deployment posture (internal audit vs continuous monitoring vs first-line real-time), engagement_mode configuration, ML model risk management, the immutable audit log, regulatory mapping (J-SOX, fraud standards, COSO, ACFE), or RACI and accountability.
---

# ガバナンス・独立性

## 独立性の区別（最重要・要法務確認）

本ソリューションを「誰の・どの手続として回すか」で、独立性（公認会計士法第24条の2 等）の扱いが変わる。2つの軸がある:

- **利用主体**: Track A＝被監査会社の内部監査/不正調査支援（既定）／ Track B＝監査法人による外部商用化・法定監査での利用。
- **配備レイヤ（未決定・要方針）**: 3線＝内部監査（定期・検知）→ 2線＝継続モニタリング → 1線＝現場のリアルタイム統制（予防）。前段（1線・2線）へ寄るほど「独立した保証」ではなく経営者自身の統制になり、監査クライアント相手では**自己レビューの脅威**（自分が作った統制を自分で監査してしまう問題）が強まる。緩和レバーは提供形態 ── PwC が個社向けに設計・運用するほど自己レビュー寄り、製品として渡してクライアントが自ら設定・運用するほど防御的になる。

これらは `engagement_mode` 等の構成で明示的に切り替える。詳細 `docs/governance.md` §1。

## モデルリスク管理

ML は導入して終わりではなく、バリデーション・ドリフト監視（＝時間とともに精度が劣化していないかの継続監視）・バイアス点検・定期再検証をライフサイクルで回す。`docs/governance.md` §2。

## 改ざん不能な監査ログ

WORM＋ハッシュチェーンで証跡を保全する（型は `data-contracts` スキル）。運用要件は `docs/governance.md` §4。

## 規制マッピング・RACI

J-SOX・不正リスク対応基準・監査基準報告書240・COSO・ACFE への対応関係は `docs/governance.md` §3。責任分担では **AIが説明責任（Accountable）を持つ行を作らない**（§5）。
