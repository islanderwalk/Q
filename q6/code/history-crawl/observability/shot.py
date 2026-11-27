# Full-page screenshots of the crawler-monitor dashboard.  python shot.py [out_dir] [prefix] [range ...]
# 127.0.0.1, not localhost: Windows tries ::1 first and WSL only forwards IPv4, so every request stalls ~5 s.
import os
import sys
from playwright.sync_api import sync_playwright

out = sys.argv[1] if len(sys.argv) > 1 else "."
prefix = sys.argv[2] if len(sys.argv) > 2 else "crawler_monitor"
ranges = sys.argv[3:] or ["6h", "30m"]
os.makedirs(out, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    for r in ranges:
        pg = b.new_page(viewport={"width": 1600, "height": 3200}, device_scale_factor=1)
        pg.goto(f"http://127.0.0.1:3000/d/crawler-monitor/x?orgId=1&from=now-{r}&to=now&kiosk&theme=dark",
                wait_until="networkidle", timeout=90000)
        pg.wait_for_timeout(15000)
        path = os.path.join(out, f"{prefix}_{r}.png")
        pg.screenshot(path=path, full_page=True)
        print(path)
    b.close()
