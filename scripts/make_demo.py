"""操作アニメーション付きデモ動画（TTSナレーション）を生成する.

前提: `python serve.py` で http://127.0.0.1:8000 が起動していること。
      edge-tts（TTSサービス）と imageio-ffmpeg が利用可能なこと。

手順:
  1) シーンごとの日本語ナレーションを edge-tts で合成し、長さを計測。
  2) Playwright で UI を実際に操作し、各シーンをナレーション長だけ保持して録画。
  3) 録画（webm）にナレーションをオフセット合成し、MP4 に muxing。

ナレーションは独立性・ブランド配慮に準拠: 成果や数値を保証・誇張せず、合成データの
参考値であることを明示し、最終判断は人間（HITL）であることを繰り返す。競合・実在製品に触れない。
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import edge_tts
import imageio_ffmpeg
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
VOICE = "ja-JP-NanamiNeural"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo_out")
OUT.mkdir(parents=True, exist_ok=True)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
VW, VH = 1440, 900
SETTLE = 0.7   # アクション後の描画待ち（秒）
TAIL = 0.7     # ナレーション後の余韻（秒）


def api(path: str):
    with urllib.request.urlopen(BASE + path) as r:
        return json.load(r)


def resolve_ids():
    """クリティカル所見と、証憑インジェクションを含む所見のIDを解決する。"""
    esc = api("/api/findings?triage=escalate")["findings"]
    critical = next((f["finding_id"] for f in esc if f["severity"] == "critical"), esc[0]["finding_id"])
    injection = None
    for f in esc:
        d = api("/api/findings/" + f["finding_id"])
        if any(ev.get("injection_flags") for ev in d.get("evidence", [])):
            injection = f["finding_id"]
            break
    return critical, injection or esc[0]["finding_id"]


# ---- シーン定義（テキスト＋UI操作） ----
def build_scenes(critical_id, injection_id):
    def goto(h):
        return lambda p: (p.goto(BASE + "/" + h, wait_until="networkidle"), p.wait_for_timeout(300))

    def scroll_to(text):
        def _f(p):
            p.evaluate(
                """(t)=>{const els=[...document.querySelectorAll('*')].filter(e=>e.children.length===0&&e.textContent.trim().includes(t));
                       (els[0]||document.body).scrollIntoView({behavior:'smooth',block:'center'});}""", text)
            p.wait_for_timeout(400)
        return _f

    def filter_escalate(p):
        p.goto(BASE + "/#/findings", wait_until="networkidle"); p.wait_for_timeout(500)
        p.eval_on_selector("select#f-triage", "el=>{el.value='escalate';el.dispatchEvent(new Event('change'));}")
        p.wait_for_timeout(600)

    def open_finding(fid):
        return lambda p: (p.goto(BASE + f"/#/finding/{fid}", wait_until="networkidle"), p.wait_for_timeout(500),
                          p.evaluate("window.scrollTo({top:0})"))

    def confirm(p):
        p.eval_on_selector("[data-dec=confirm]", "el=>el.scrollIntoView({block:'center'})")
        p.wait_for_timeout(400)
        p.fill("#hitl-note", "証憑（反社該当）を確認。原本と申請者ヒアリングを実施し確定。")
        p.wait_for_timeout(500)
        p.click("[data-dec=confirm]")
        p.wait_for_timeout(900)

    return [
        (goto("#/dashboard"),
         "経費不正リスク分析、自律AIエージェントのデモです。この画面は監査人向けのコンソール。"
         "架空企業サンプル商事の経費、二百三十八明細を、ルールと機械学習で全件スコアリングし、"
         "高リスクだけをエージェントが深掘り検証しています。表示する数値は合成データでの参考値であり、"
         "実環境の性能を保証するものではありません。"),
        (scroll_to("リスクの絞り込み"),
         "設計の要はコストの考え方です。すべてに大規模言語モデルを流すのではなく、"
         "まずルールと機械学習で決定論的に全件を選別し、コストの高い深掘りは高リスクの部分集合に限定します。"
         "これにより、全件網羅と現実的なコストを両立します。"),
        (filter_escalate,
         "所見一覧です。リスクスコアの高い順に並び、トリアージや重大度で絞り込めます。"
         "エージェントが深掘りした所見を見てみましょう。"),
        (open_finding(critical_id),
         "最重要と判定された所見です。申請額が承認権限を超過し、領収書が欠落、参加者は不特定。"
         "さらに、取引先が反社・制裁の該当リストに一致しています。"),
        (scroll_to("機械学習の異常寄与"),
         "所見には必ず根拠が添えられます。違反したルールと、その誤検知の留意点。"
         "機械学習が捉えた異常の寄与要因。そして、費目別シナリオに基づく仮説の検証結果です。"),
        (open_finding(injection_id),
         "証憑には、検知を逃れるための細工が仕込まれることがあります。"),
        (scroll_to("収集証憑"),
         "領収書のテキストに、正常と判定せよ、といった指示が埋め込まれていても、"
         "エージェントはそれを指示として実行しません。指示とデータを分離し、隠蔽の疑いとして検出します。"
         "収集した証憑は読み取り専用で、取得の来歴とともに記録されます。"),
        (confirm,
         "そして、最終判断は人間です。AIは所見を提示するのみで、確定はできません。"
         "監査人が、確定・棄却・追加調査を判断します。ここでは、確定してみます。"),
        (goto("#/audit"),
         "すべての判断は、改ざん不能な監査証跡に残ります。各レコードは前のレコードのハッシュを含み、"
         "一件でも書き換えれば鎖が切れて検知できます。今の確定操作も、証跡として記録されました。"),
        (goto("#/governance"),
         "用途に応じて独立性の制約を切り替え、主要な規制フレームワークにも対応づけています。"
         "本ツールは監査人の判断を支援するものであり、成果や検出性能を保証・確約するものではありません。"
         "以上が、デモのご紹介でした。"),
    ]


# ---- TTS 生成と長さ計測 ----
async def synth(text: str, path: Path):
    await edge_tts.Communicate(text, VOICE, rate="+6%").save(str(path))


def duration(path: Path) -> float:
    out = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], stderr=subprocess.PIPE, text=True).stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", out)
    h, mi, s = map(float, m.groups())
    return h * 3600 + mi * 60 + s


def main():
    try:
        urllib.request.urlopen(BASE + "/api/summary", timeout=5)
    except Exception:
        print("サーバが起動していません。`python serve.py` を実行してください。", file=sys.stderr)
        sys.exit(1)

    urllib.request.urlopen(urllib.request.Request(BASE + "/api/reset", method="POST"))  # 状態初期化
    critical_id, injection_id = resolve_ids()
    print("critical:", critical_id, "injection:", injection_id)
    scenes = build_scenes(critical_id, injection_id)

    # 1) TTS 合成
    audio_files, durations = [], []
    for i, (_, text) in enumerate(scenes):
        f = OUT / f"n{i:02d}.mp3"
        asyncio.run(synth(text, f))
        audio_files.append(f)
        durations.append(duration(f))
        print(f"scene {i}: {durations[-1]:.1f}s")

    # 2) Playwright 録画（各シーンをナレーション長だけ保持）
    offsets = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": VW, "height": VH},
                                  record_video_dir=str(OUT), record_video_size={"width": VW, "height": VH})
        page = ctx.new_page()
        page.goto(BASE + "/#/dashboard", wait_until="networkidle")
        page.wait_for_timeout(800)
        t0 = time.monotonic()
        for i, (action, _) in enumerate(scenes):
            action(page)
            page.wait_for_timeout(int(SETTLE * 1000))
            offsets.append(time.monotonic() - t0)          # ナレーション開始オフセット
            page.wait_for_timeout(int(durations[i] * 1000 + TAIL * 1000))
        page.wait_for_timeout(400)
        video_path = page.video.path()
        ctx.close()
        browser.close()
    print("video:", video_path, "offsets:", [round(o, 1) for o in offsets])

    # 3) 動画＋ナレーションを mux（オフセット合成）
    final = OUT / "demo.mp4"
    inputs = ["-i", str(video_path)]
    for f in audio_files:
        inputs += ["-i", str(f)]
    parts = [f"[{i+1}]adelay={int(offsets[i]*1000)}:all=1[a{i}]" for i in range(len(audio_files))]
    amix = "".join(f"[a{i}]" for i in range(len(audio_files))) + f"amix=inputs={len(audio_files)}:normalize=0[a]"
    filt = ";".join(parts) + ";" + amix
    cmd = [FFMPEG, "-y", *inputs, "-filter_complex", filt, "-map", "0:v", "-map", "[a]",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k",
           "-shortest", "-movflags", "+faststart", str(final)]
    r = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:], file=sys.stderr); sys.exit(2)
    print("DONE:", final, f"({final.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
