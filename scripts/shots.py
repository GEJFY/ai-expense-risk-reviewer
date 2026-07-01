"""各画面のスクリーンショットを撮る（UI 目視確認用）。"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("shots")
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8000"

errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))

    def shot(hash_, name, wait=1200):
        pg.goto(BASE + "/" + hash_, wait_until="networkidle")
        pg.wait_for_timeout(wait)
        pg.screenshot(path=str(OUT / name), full_page=True)
        print("shot", name)

    shot("#/dashboard", "01_dashboard.png")
    shot("#/findings", "02_findings.png")
    # open first escalate finding
    pg.goto(BASE + "/#/findings", wait_until="networkidle"); pg.wait_for_timeout(800)
    pg.eval_on_selector("select#f-triage", "el => { el.value='escalate'; el.dispatchEvent(new Event('change')); }")
    pg.wait_for_timeout(700)
    fid = pg.eval_on_selector("tbody tr[data-id]", "el => el.dataset.id")
    pg.goto(BASE + f"/#/finding/{fid}", wait_until="networkidle"); pg.wait_for_timeout(1400)
    pg.screenshot(path=str(OUT / "03_detail.png"), full_page=True); print("shot 03_detail.png", fid)
    shot("#/audit", "04_audit.png")
    shot("#/governance", "05_governance.png")
    b.close()

print("CONSOLE_ERRORS:", errors if errors else "none")
