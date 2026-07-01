/* 経費不正リスク分析 — 監査人コンソール（SPA）
   AIは所見の提示のみ。確定・是正・通報は監査人が判断する（HITL）。 */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const view = $("#view");

// ---- helpers ----
async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const yen = (n) => (n == null ? "—" : "¥" + Math.round(n).toLocaleString("ja-JP"));
const pct = (n) => (n == null ? "—" : Math.round(n * 100) + "%");
const SEV = { critical: "最重要", high: "高", medium: "中", low: "低" };
const TRI = { escalate: "深掘り", review: "要レビュー", auto_dismiss: "自動棄却" };
const HITL = { pending: "未判断", confirmed: "確定", dismissed: "棄却", needs_more: "追加調査" };
const scoreColor = (s) => (s >= 70 ? "var(--sev-critical)" : s >= 40 ? "var(--pwc-orange)" : "var(--sev-low)");
const FEAT = { log_amount: "金額（対数）", hour: "取引時刻", dow: "曜日", is_weekend: "休日フラグ",
  is_night: "深夜フラグ", is_round_1000: "キリ金額", headcount: "参加人数", amount_per_head: "一人当たり金額",
  amount_z_in_category: "費目内の金額偏差", amount_z_for_applicant: "本人比の金額偏差",
  vendor_use_by_applicant: "取引先利用頻度" };
const featLabel = (f) => FEAT[f] || f;
const sevBadge = (s) => `<span class="badge sev-${s}">${SEV[s] || s}</span>`;
const triBadge = (t) => `<span class="badge tri-${t}">${TRI[t] || t}</span>`;
const hitlBadge = (h) => `<span class="badge hitl-${h}">${HITL[h] || h}</span>`;

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(t._t); t._t = setTimeout(() => (t.hidden = true), 2600);
}
function animateBars(root = document) {
  requestAnimationFrame(() => root.querySelectorAll("[data-w]").forEach((e) => (e.style.width = e.dataset.w)));
}
function countUp(el, to) {
  const dur = 700, start = performance.now();
  const step = (t) => { const p = Math.min(1, (t - start) / dur); el.textContent = Math.round(to * (0.2 + 0.8 * p * (2 - p))).toLocaleString("ja-JP"); if (p < 1) requestAnimationFrame(step); else el.textContent = to.toLocaleString("ja-JP"); };
  requestAnimationFrame(step);
}

const DISCLAIMER = `本ツールは AI による<b>リスクの提示</b>です。確定・是正・通報・処分は監査人（人間）が判断します（HITL）。各所見には根拠（違反ルール／ML寄与／収集証憑）が付与されます。`;

// ================= Dashboard =================
async function renderDashboard() {
  const s = await api("/api/summary");
  const st = s.stats, tr = st.by_triage, sv = st.by_severity, rec = s.reconciliation, v = s.validation;
  $("#engagement-chip").textContent = `mode: ${s.engagement_mode}`;
  $("#chain-chip").textContent = s.audit.chain_ok ? "監査証跡 改ざんなし" : "監査証跡 ⚠";
  $("#chain-chip").className = "chip " + (s.audit.chain_ok ? "chip-ok" : "chip-ghost");

  const total = st.total_lines;
  const funnelRows = [
    ["全件（決定論選別）", total, "var(--ink-2)"],
    ["要レビュー以上", (tr.review || 0) + (tr.escalate || 0), "var(--pwc-orange-2)"],
    ["エージェント深掘り", tr.escalate || 0, "var(--pwc-red)"],
  ];
  const maxF = total || 1;

  view.innerHTML = `
    <div class="page-head"><h1>ダッシュボード</h1>
      <span class="muted small">${esc(s.company)} ／ ${esc(s.generated_at || "")}</span></div>
    <p class="page-sub">対象 ${total.toLocaleString()} 明細を全件スコアリングし、高リスクをエージェントが深掘り検証しました。</p>
    <div class="banner"><span>ℹ️</span><div>${DISCLAIMER}</div></div>

    <div class="grid cols-4">
      ${kpi("総明細数", total, total, maxF, "", "")}
      ${kpi("エージェント深掘り", tr.escalate || 0, tr.escalate || 0, maxF, "accent-red", "escalate")}
      ${kpi("要レビュー", tr.review || 0, tr.review || 0, maxF, "accent-orange", "review")}
      ${kpi("自動棄却", tr.auto_dismiss || 0, tr.auto_dismiss || 0, maxF, "", "auto_dismiss")}
    </div>

    <div class="section-title">リスクの絞り込み（コスト・ファネル）</div>
    <div class="grid cols-2">
      <div class="card"><div class="funnel">
        ${funnelRows.map(([l, n, c]) => `
          <div class="funnel-step" data-w="${Math.max(24, (n / maxF) * 100)}%" style="width:0;background:${c}">
            ${esc(l)}<small>${n.toLocaleString()} 件</small></div>`).join("")}
      </div>
      <p class="muted small" style="margin-top:12px">ルール＋ML で全件を決定論的に選別し、推論コストの高い深掘りは高リスク部分集合に限定します。</p>
      </div>
      <div class="card">
        <div class="ev-head"><b class="small">重大度の分布</b></div>
        <div class="bars">
          ${["critical", "high", "medium", "low"].map((k) => barRow(SEV[k], sv[k] || 0, total, `var(--sev-${k})`)).join("")}
        </div>
        <hr class="soft">
        <div class="metric-mini">
          <div class="m"><b>${sv.critical || 0}</b><span>最重要</span></div>
          <div class="m"><b>${sv.high || 0}</b><span>高</span></div>
          <div class="m"><b>${(tr.review||0)+(tr.escalate||0)}</b><span>要確認合計</span></div>
        </div>
      </div>
    </div>

    <div class="section-title">データ完全性・モデル検証</div>
    <div class="grid cols-3">
      <div class="card">
        <b class="small">完全性照合</b>
        <p class="small" style="margin:10px 0 4px">取込 ${rec.ingested_count.toLocaleString()} 件 ／ 合計 ${yen(rec.ingested_amount_sum)}</p>
        <p class="small muted">重複ID: ${rec.duplicate_line_ids.length} 件 ／ 欠損・型不整合は所見に data_quality として明示</p>
      </div>
      <div class="card">
        <b class="small">監査証跡（WORM＋ハッシュチェーン）</b>
        <p style="margin:10px 0"><span class="badge ${s.audit.chain_ok ? "hitl-confirmed" : "sev-critical"}">${s.audit.chain_ok ? "改ざんなし" : "要確認"}</span></p>
        <p class="small muted">${s.audit.entries.toLocaleString()} 件の証跡。入力→ルール/モデル→証憑→スコア→結論→HITL を追跡可能。</p>
      </div>
      <div class="card">
        <b class="small">モデル検証（合成データ・参考値）</b>
        <div class="metric-mini" style="margin-top:10px">
          <div class="m"><b>${pct(v.recall)}</b><span>Recall</span></div>
          <div class="m"><b>${pct(v.precision)}</b><span>Precision</span></div>
          <div class="m"><b>${pct(v.false_positive_rate)}</b><span>誤検知率</span></div>
        </div>
        <p class="muted-xs" style="margin-top:10px">${esc(v.disclaimer)}</p>
      </div>
    </div>

    <div class="section-title">ルールカバレッジ（透明性）</div>
    <div class="card">
      <p class="small">ルール総数 <b>${s.coverage.total_rules}</b> ／ エンジン実装済み <b>${s.coverage.engine_implemented}</b>
      ／ エージェント検証 <b>${s.coverage.agent_verified}</b> ／ ML <b>${s.coverage.ml_assisted}</b></p>
      <p class="muted-xs">決定論/統計だが未実装（今後拡張・取りこぼしを明示）: ${s.coverage.engine_not_implemented.map((r)=>`<span class="pill">${esc(r)}</span>`).join(" ")}</p>
    </div>`;

  view.querySelectorAll(".k-val[data-count]").forEach((e) => countUp(e, +e.dataset.count));
  view.querySelectorAll(".kpi[data-goto]").forEach((c) => c.addEventListener("click", () => {
    _flt = { triage: c.dataset.goto, severity: "", category: "", q: "", hitl: "" };
    location.hash = "#/findings";
  }));
  animateBars(view);
}
function kpi(label, val, barVal, max, cls, goto) {
  return `<div class="card kpi ${cls}" ${goto ? `data-goto="${goto}" style="cursor:pointer"` : ""}>
    <div class="k-label">${esc(label)}${goto ? ' <span class="muted-xs">→</span>' : ""}</div>
    <div class="k-val" data-count="${val}">0</div>
    <div class="k-bar"><span data-w="${Math.min(100,(barVal/max)*100)}%"></span></div></div>`;
}
function barRow(label, val, total, color) {
  const w = total ? (val / total) * 100 : 0;
  return `<div class="bar-row"><span class="b-label">${esc(label)}</span>
    <span class="bar-track"><span data-w="${w}%" style="background:${color}"></span></span>
    <span class="b-val">${val.toLocaleString()}</span></div>`;
}

// ================= Findings list =================
let _flt = { triage: "", severity: "", category: "", q: "", hitl: "" };
async function renderFindings() {
  const qs = new URLSearchParams(Object.entries(_flt).filter(([, v]) => v)).toString();
  const data = await api("/api/findings?" + qs);
  view.innerHTML = `
    <div class="page-head"><h1>所見一覧</h1></div>
    <p class="page-sub">リスクスコア順。行をクリックすると根拠・証憑を確認し、確定/棄却/追加調査を判断できます。</p>
    <div class="filters">
      ${sel("triage", "トリアージ", { "": "すべて", escalate: "深掘り", review: "要レビュー", auto_dismiss: "自動棄却" })}
      ${sel("severity", "重大度", { "": "すべて", critical: "最重要", high: "高", medium: "中", low: "低" })}
      ${sel("hitl", "判断", { "": "すべて", pending: "未判断", confirmed: "確定", dismissed: "棄却", needs_more: "追加調査" })}
      <input id="f-q" type="text" placeholder="申請者・部門・取引先・ルールで検索" value="${esc(_flt.q)}" />
      <span class="count">${data.count.toLocaleString()} 件</span>
    </div>
    <div class="tablewrap"><table>
      <thead><tr><th>スコア</th><th>重大度</th><th>トリアージ</th><th>明細ID</th><th>申請者</th><th>部門</th><th>費目</th><th class="num">金額</th><th>主な根拠</th><th>判断</th></tr></thead>
      <tbody>
        ${data.findings.map(rowHtml).join("") || `<tr><td colspan="10" class="muted" style="text-align:center;padding:30px">該当なし</td></tr>`}
      </tbody></table></div>`;

  ["triage", "severity", "hitl"].forEach((k) => $("#f-" + k).addEventListener("change", (e) => { _flt[k] = e.target.value; renderFindings(); }));
  const qi = $("#f-q"); qi.addEventListener("input", debounce((e) => { _flt.q = e.target.value; renderFindings(); }, 250));
  view.querySelectorAll("tbody tr[data-id]").forEach((tr) => tr.addEventListener("click", () => (location.hash = "#/finding/" + tr.dataset.id)));
  animateBars(view);
}
function rowHtml(f) {
  return `<tr data-id="${esc(f.finding_id)}">
    <td><span class="score-pill" style="background:${scoreColor(f.risk_score)}">${f.risk_score}</span></td>
    <td>${sevBadge(f.severity)}</td><td>${triBadge(f.triage)}</td>
    <td class="muted small">${esc(f.expense_line_id)}</td>
    <td>${esc(f.applicant)}</td><td class="small">${esc(f.department || "")}</td>
    <td class="small">${esc(f.expense_category || "")}</td>
    <td class="num">${yen(f.amount)}</td>
    <td class="pill-list">${(f.top_rules || []).map((r) => `<span class="pill">${esc(r)}</span>`).join("")}</td>
    <td>${hitlBadge(f.hitl_status)}</td></tr>`;
}
function sel(key, label, opts) {
  return `<select id="f-${key}" title="${label}">${Object.entries(opts).map(([v, l]) => `<option value="${v}" ${_flt[key] === v ? "selected" : ""}>${l}</option>`).join("")}</select>`;
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// ================= Finding detail (HITL) =================
async function renderDetail(id) {
  const f = await api("/api/findings/" + encodeURIComponent(id));
  const g = gauge(f.risk_score);
  view.innerHTML = `
    <a class="back-link" href="#/findings">← 所見一覧へ戻る</a>
    <div class="page-head"><h1>所見 ${esc(f.finding_id)}</h1>${hitlBadge(f.hitl_status)}</div>
    <div class="detail-grid">
      <div>
        <div class="card">
          <div class="gauge">${g}
            <div><div style="display:flex;gap:8px;align-items:center">${sevBadge(f.severity)} ${triBadge(f.triage)}</div>
            <p class="muted small" style="margin:8px 0 0">費目 ${esc(f.expense_category || "")} ／ ${esc(f.vendor_name || "")}</p></div>
          </div>
          <div class="facts">
            ${fact("申請者", f.applicant)}${fact("部門", f.department)}
            ${fact("取引日", f.transaction_date)}${fact("金額", yen(f.amount))}
            ${fact("承認者", f.approver)}${fact("支払", f.payment_method)}
            ${fact("参加者", (f.participants || []).join("、") || "—")}${fact("摘要", f.description)}
          </div>
          ${f.data_quality.length ? `<p class="small" style="margin-top:10px"><span class="badge sev-medium">データ品質</span> ${f.data_quality.map(esc).join(", ")}</p>` : ""}
        </div>

        <div class="card" style="margin-top:16px">
          <b class="small">根拠 — 違反ルール</b>
          ${f.rationale_rules.length ? f.rationale_rules.map(ruleHtml).join("") : `<p class="muted small">決定論ルールの該当なし（ML/証憑に基づく）</p>`}
        </div>

        ${f.ml_attribution ? mlCard(f.ml_attribution) : ""}

        ${f.hypotheses.length ? `<div class="card" style="margin-top:16px">
          <b class="small">検証した仮説（費目別シナリオ）</b>
          ${f.hypotheses.map(hypoHtml).join("")}</div>` : ""}

        ${f.evidence.length ? `<div class="card" style="margin-top:16px">
          <b class="small">収集証憑（read-only）</b>
          ${f.evidence.map(evHtml).join("")}</div>` : ""}
      </div>

      <aside class="hitl-panel">
        <div class="card">
          <b class="small">監査人の判断（HITL）</b>
          <div class="status-line">現在: ${hitlBadge(f.hitl_status)}</div>
          ${f.recommended_action_ja ? `<div class="advisory"><b>推奨（助言）:</b> ${esc(f.recommended_action_ja)}</div>` : ""}
          <textarea id="hitl-note" class="hitl-note" placeholder="判断メモ（任意）">${esc((f.hitl_note && f.hitl_note.note) || "")}</textarea>
          <div class="hitl-actions">
            <button class="btn btn-primary" data-dec="confirm">確定する</button>
            <button class="btn btn-neutral" data-dec="investigate">追加調査に回す</button>
            <button class="btn btn-danger" data-dec="dismiss">棄却する</button>
          </div>
          <p class="hitl-hint">AIは所見を提示するのみで、<b>hitl_status を confirmed にできません</b>。ここでの操作（人間の判断）だけが確定でき、判断は監査ログに追記されます。</p>
        </div>
        <div class="card" style="margin-top:14px">
          <b class="small">再現性</b>
          <p class="small muted" style="margin:8px 0 0">model_version<br><code style="font-size:11px">${esc(f.model_version)}</code></p>
          <p class="small"><a class="back-link" href="#/audit/${esc(f.finding_id)}">この所見の監査証跡を見る →</a></p>
        </div>
      </aside>
    </div>`;

  view.querySelectorAll("[data-dec]").forEach((b) => b.addEventListener("click", () => decide(f.finding_id, b.dataset.dec)));
  animateBars(view);
}
function fact(k, v) { return `<div class="fact"><div class="f-k">${esc(k)}</div><div class="f-v">${esc(v ?? "—")}</div></div>`; }
function ruleHtml(r) {
  return `<div class="rule-row">
    <div class="rule-id">${esc(r.rule_id)}</div>
    <div class="rule-body"><div class="r-name">${esc(r.name_ja)} <span class="muted" style="font-weight:400">/ ${esc(r.category)}</span></div>
      <div class="r-detail">${esc(r.detail_ja || "")}</div>
      <div class="wbar"><span data-w="${Math.min(100, r.weight)}%"></span></div>
      ${r.fp_notes ? `<div class="r-fp">誤検知の留意: ${esc(r.fp_notes)}</div>` : ""}
    </div></div>`;
}
function mlCard(ml) {
  const max = Math.max(1, ...ml.shap_top_features.map((f) => Math.abs(f.contribution)));
  return `<div class="card" style="margin-top:16px">
    <b class="small">根拠 — 機械学習の異常寄与（${esc(ml.model)}）</b>
    <p class="small muted" style="margin:6px 0 10px">異常スコア ${ml.anomaly_score}（標準化偏差ベースの透明な寄与。SHAP等への差替えを想定）</p>
    ${ml.shap_top_features.map((f) => `<div class="shap-row"><span class="s-name">${esc(featLabel(f.feature))}</span>
      <span class="shap-bar"><span class="${f.contribution < 0 ? "neg" : ""}" data-w="${(Math.abs(f.contribution) / max) * 100}%"></span></span>
      <span style="width:52px;text-align:right">${f.contribution.toFixed(2)}</span></div>`).join("")}</div>`;
}
function hypoHtml(h) {
  return `<div class="hypo"><span class="pill">${esc(h.scenario_id)}</span>
    <span style="flex:1">${esc(h.hypothesis_ja || "")}</span>
    <span class="verdict-${h.verdict}">${{ supported: "支持", refuted: "反証", inconclusive: "判断保留" }[h.verdict] || h.verdict}</span></div>`;
}
function evHtml(e) {
  const flagged = e.injection_flags && e.injection_flags.length;
  const c = e.content || {};
  const body = Object.entries(c).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join("\n");
  return `<div class="evidence-item ${flagged ? "flagged" : ""}">
    <div class="ev-head"><span class="ev-type">${esc(e.type)} <span class="muted" style="font-weight:400">via ${esc(e.source)}</span></span>
      ${e.ocr_confidence != null ? `<span class="muted small">OCR信頼度 ${pct(e.ocr_confidence)}</span>` : ""}</div>
    <div class="ev-content">${esc(body)}</div>
    ${flagged ? `<div class="inj-flag">⚠ プロンプトインジェクション検出: ${e.injection_flags.map(esc).join(", ")} → CONS-006（隠蔽の疑いとしてエスカレート）</div>` : ""}
    <div class="ev-prov">来歴: ${esc(e.provenance.collected_by_role)} ／ scope ${esc(e.provenance.access_scope)} ／ 法的基盤 ${esc(e.provenance.legal_basis_ref || "—")}</div>
  </div>`;
}
function gauge(score) {
  const r = 40, c = 2 * Math.PI * r, off = c * (1 - score / 100), col = scoreColor(score);
  return `<svg viewBox="0 0 96 96"><circle cx="48" cy="48" r="${r}" fill="none" stroke="var(--line-2)" stroke-width="9"/>
    <circle cx="48" cy="48" r="${r}" fill="none" stroke="${col}" stroke-width="9" stroke-linecap="round"
      stroke-dasharray="${c}" stroke-dashoffset="${c}" transform="rotate(-90 48 48)">
      <animate attributeName="stroke-dashoffset" from="${c}" to="${off}" dur="0.9s" fill="freeze" calcMode="spline" keySplines="0.2 0.8 0.2 1" keyTimes="0;1"/></circle>
    <text x="48" y="53" text-anchor="middle" class="g-num" style="font-size:22px;font-weight:800" fill="${col}">${score}</text></svg>`;
}
async function decide(id, dec) {
  const note = $("#hitl-note") ? $("#hitl-note").value : "";
  await api(`/api/findings/${encodeURIComponent(id)}/decision`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision: dec, note }),
  });
  toast({ confirm: "所見を確定しました（監査ログに記録）", dismiss: "所見を棄却しました", investigate: "追加調査に回しました" }[dec]);
  renderDetail(id);
}

// ================= Audit trail =================
async function renderAudit(findingId) {
  const data = await api("/api/audit?limit=80" + (findingId ? "&finding_id=" + encodeURIComponent(findingId) : ""));
  view.innerHTML = `
    <div class="page-head"><h1>監査証跡</h1>
      <span class="chip ${data.chain_ok ? "chip-ok" : "chip-ghost"}">${data.chain_ok ? "ハッシュチェーン: 改ざんなし" : "⚠ 改ざん検知"}</span></div>
    <p class="page-sub">${findingId ? `所見 ${esc(findingId)} に関する証跡` : `全 ${data.total.toLocaleString()} 件`}。各レコードは前レコードのハッシュを含み、1件でも改ざんすると鎖が切れて検知できます（WORM＋ハッシュチェーン）。
      ${findingId ? `<a class="back-link" href="#/audit"> すべて表示 →</a>` : ""}</p>
    <div class="card"><div class="timeline">
      ${data.entries.map(tlItem).join("") || `<p class="muted">証跡なし</p>`}
    </div></div>`;
}
function tlItem(e) {
  const isHitl = e.phase === "hitl";
  return `<div class="tl-item ${isHitl ? "hitl" : ""}">
    <div class="tl-phase">${esc(e.phase)} · ${esc(e.actor)}</div>
    <div class="tl-action">${esc(e.action)}</div>
    <div class="tl-meta">${esc(e.timestamp)} ${e.expense_line_id ? "· " + esc(e.expense_line_id) : ""} ${e.termination_reason ? "· 終了:" + esc(e.termination_reason) : ""}</div>
    <div class="tl-hash">hash ${esc((e.hash || "").slice(0, 16))}…</div></div>`;
}

// ================= Governance =================
async function renderGovernance() {
  const [s, rm] = await Promise.all([api("/api/summary"), api("/api/regmap")]);
  view.innerHTML = `
    <div class="page-head"><h1>ガバナンス・独立性</h1></div>
    <p class="page-sub">用途（Track A/B）で独立性の制約が変わります。本デモは内部監査支援（track_a）想定。</p>
    <div class="grid cols-2">
      <div class="card"><b class="small">エンゲージメント</b>
        <p class="small" style="margin:8px 0">engagement_mode: <b>${esc(s.engagement_mode)}</b>（track_a＝内部監査支援 / track_b＝外部商用化・要独立性チェック）</p>
        <p class="small muted">track_b では是正提案を助言表現に制限し、独立性チェックリストの充足を起動条件とします（要 法務・品質管理確認）。</p>
      </div>
      <div class="card"><b class="small">モデルリスク管理</b>
        <p class="small" style="margin:8px 0">model_version: <code style="font-size:11px">${esc(s.model_version)}</code></p>
        <p class="small muted">ルール/モデル/閾値のバージョンを所見に紐付け、再現性を担保。検出力は合成データで継続評価（参考値）。</p>
      </div>
    </div>
    <div class="section-title">規制・基準フレームワークへのマッピング</div>
    <div class="tablewrap"><table><thead><tr><th>フレームワーク</th><th>関連</th><th>本ソリューションの寄与</th></tr></thead>
      <tbody>${rm.map.map((m) => `<tr style="cursor:default"><td><b>${esc(m.framework)}</b></td><td class="small">${esc(m.area)}</td><td class="small">${esc(m.contribution)}</td></tr>`).join("")}</tbody></table></div>
    <div class="banner" style="margin-top:22px"><span>⚖️</span><div>本ツールは監査人の判断を支援するものであり、成果や検出性能を保証・確約するものではありません。是正・通報・処分の意思決定は人間が行います。各基準の最新版・条番号は運用前に確認してください。</div></div>`;
}

// ================= Router =================
const routes = [
  [/^#\/finding\/(.+)$/, (m) => renderDetail(decodeURIComponent(m[1]))],
  [/^#\/audit\/(.+)$/, (m) => renderAudit(decodeURIComponent(m[1]))],
  [/^#\/audit$/, () => renderAudit()],
  [/^#\/findings$/, renderFindings],
  [/^#\/governance$/, renderGovernance],
  [/^#\/dashboard$/, renderDashboard],
];
function router() {
  const h = location.hash || "#/dashboard";
  const nav = h.replace(/^#\//, "").split("/")[0] || "dashboard";
  document.querySelectorAll(".nav-item").forEach((a) => a.classList.toggle("active", a.dataset.route === nav));
  for (const [re, fn] of routes) { const m = h.match(re); if (m) { Promise.resolve(fn(m)).catch((e) => (view.innerHTML = `<div class="card">読み込みエラー: ${esc(e.message)}</div>`)); return; } }
  location.hash = "#/dashboard";
}
window.addEventListener("hashchange", router);
$("#reset-btn").addEventListener("click", async () => { await api("/api/reset", { method: "POST" }); toast("デモ状態を初期化しました"); router(); });
router();
