"""Q3 看板、搜尋頁、告警規則截圖（Playwright 無頭 Chromium，只連本機 127.0.0.1）。

    python monitoring/capture.py                       # 看板全頁＋每一格＋搜尋頁＋查詢 API＋告警規則
    python monitoring/capture.py --prefix drill- --range now-3h   # 故障演練後再拍一次，檔名加前綴

輸出到 q3/evidence/grafana/（--out 可改），另寫 captured.json：每張圖對應哪一格、拍攝時間、看板時間範圍。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
DASH = HERE / "grafana" / "dashboards" / "q3-ptt-search.json"
GRID_PX = 38  # Grafana 一格高度 30 px＋間距 8 px


def wait_grafana(page, ms):
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(ms)  # 面板查完 Prometheus 之後還要畫圖


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--grafana", default="http://127.0.0.1:3300")
    ap.add_argument("--search", default="http://127.0.0.1:8300")
    ap.add_argument("--prometheus", default="http://127.0.0.1:9390")
    ap.add_argument("--out", default=str(HERE.parent.parent / "evidence" / "grafana"))
    ap.add_argument("--range", default="now-6h", help="看板時間範圍的起點，例如 now-6h、now-24h")
    ap.add_argument("--keyword", action="append", help="搜尋頁要查的關鍵字，可重複；預設 颱風")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--no-panels", action="store_true", help="不拍每一格，只拍全頁")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    dash = json.loads(DASH.read_text(encoding="utf-8"))
    uid, panels = dash["uid"], dash["panels"]
    height = max(p["gridPos"]["y"] + p["gridPos"]["h"] for p in panels) * GRID_PX + 120
    base = f"{a.grafana}/d/{uid}/?orgId=1&from={a.range}&to=now&kiosk"
    shots = []

    def shot(page, name, what, **extra):
        path = out / f"{a.prefix}{name}.png"
        page.screenshot(path=str(path), full_page=True)
        shots.append({"file": path.name, "what": what, **extra})
        print(path.name, "-", what, flush=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        opts = dict(device_scale_factor=2, locale="zh-TW", timezone_id="Asia/Taipei", color_scheme="light")
        # 1. 看板全頁：Grafana 的捲動在內層容器，所以直接開一個夠高的視窗讓每一格都畫出來
        page = browser.new_context(viewport={"width": 1600, "height": height}, **opts).new_page()
        page.goto(base)
        wait_grafana(page, 6000)
        shot(page, "dashboard-full", f"看板全頁（{a.range} 到現在）")
        # 2. 每一格單獨放大
        if not a.no_panels:
            page = browser.new_context(viewport={"width": 1400, "height": 720}, **opts).new_page()
            for p in panels:
                if p["type"] in ("row", "text"):
                    continue
                page.goto(f"{base}&viewPanel={p['id']}")
                wait_grafana(page, 2500)
                shot(page, f"panel-{p['id']:02d}", p["title"], panel_id=p["id"])
        # 3. 搜尋頁與查詢 API
        page = browser.new_context(viewport={"width": 1100, "height": 900}, **opts).new_page()
        for i, kw in enumerate(a.keyword or ["颱風"], 1):
            page.goto(f"{a.search}/?q={quote(kw)}&days=7")
            page.wait_for_function("!document.getElementById('meta').textContent.includes('查詢中')", timeout=120000)
            page.wait_for_timeout(500)
            shot(page, f"search-{i}", f"搜尋頁：{kw}", keyword=kw,
                 meta=page.inner_text("#meta"))
            page.goto(f"{a.search}/api/search?q={quote(kw)}&days=7&limit=3")
            page.wait_for_timeout(500)
            shot(page, f"search-{i}-api", f"查詢 API 回傳的 JSON：{kw}（limit=3）", keyword=kw)
        # 4. 告警規則（只對結果告警）
        page.goto(f"{a.prometheus}/alerts")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        shot(page, "prometheus-alerts", "Prometheus 告警規則與目前狀態")
        browser.close()

    meta = out / f"{a.prefix}captured.json"
    meta.write_text(json.dumps({"captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "dashboard_range": f"{a.range} → now", "shots": shots},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    print(meta.name, len(shots), "張", flush=True)


if __name__ == "__main__":
    main()
