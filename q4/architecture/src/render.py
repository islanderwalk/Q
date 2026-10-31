#!/usr/bin/env python3
"""把同目錄的架構圖 HTML 渲染成高解析 PNG（2x），輸出到上一層目錄。
用法：python3 render.py   （需要 playwright + chromium）
純本機，不連網。"""
import os
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.dirname(HERE)  # architecture/
JOBS = [
    ("diagram-system.html", "diagram-system.png", 1300, 880),
    ("diagram-request-layers.html", "diagram-request-layers.png", 1300, 900),
]

def main():
    with sync_playwright() as p:
        br = p.chromium.launch()
        for html, png, w, h in JOBS:
            ctx = br.new_context(viewport={"width": w, "height": h}, device_scale_factor=2)
            page = ctx.new_page()
            page.goto(f"file://{HERE}/{html}", wait_until="networkidle")
            page.wait_for_timeout(500)
            page.query_selector("svg").screenshot(path=os.path.join(OUT, png))
            print("rendered", png)
            ctx.close()
        br.close()

if __name__ == "__main__":
    main()
