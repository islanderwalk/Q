# Retakes the OS-level window screenshot (DevTools/F12 Network panel open) for targets whose first attempt
# failed. FB/IG keep rewriting document.title (notification counts), so the title marker is re-applied on a
# timer until the window is found. Read-only page visits, >=5s apart, same profile as crawl_loggedin.py.
import json, sys, time, ctypes, ctypes.wintypes as wt, pathlib
from playwright.sync_api import sync_playwright
from PIL import ImageGrab

import os  # noqa: E402
D = pathlib.Path(os.environ.get("META_WORKDIR") or pathlib.Path(__file__).parent)  # outputs + profile live here
sys.argv = [sys.argv[0], "0"] + sys.argv[1:]
import crawl_loggedin as C  # noqa: E402  (reuses TARGETS, find_window, DevTools prefs)

FORCE = """m => { window.__t = document.title; window.__qsev = setInterval(() => {
  if (!document.title.startsWith(m)) document.title = m + ' ' + (window.__t || ''); }, 150); }"""
UNFORCE = """() => { clearInterval(window.__qsev); if (window.__t !== undefined) document.title = window.__t; }"""


def grab(page, path):
    marker = f"QSEV{int(time.time() * 1000)}"
    page.evaluate(FORCE, marker)
    hwnd = None
    for _ in range(20):
        time.sleep(0.5)
        hwnd = C.find_window(marker)
        if hwnd:
            break
    if hwnd:
        C.user32.ShowWindow(hwnd, 3)
        C.user32.SetForegroundWindow(hwnd)
        time.sleep(1.5)
        r = wt.RECT()
        C.user32.GetWindowRect(hwnd, ctypes.byref(r))
        ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True).save(path)
    page.evaluate(UNFORCE)
    return bool(hwnd)


def main():
    need = [t for t in C.TARGETS if not (C.EV / f"{t[0]}_{t[1].replace('.', '_')}_devtools_F12.png").exists()]
    print("retake", [t[1] for t in need], flush=True)
    C.set_devtools_network_pref()
    res = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(C.PROFILE), headless=False, locale="zh-TW", timezone_id="Asia/Taipei",
                                                   no_viewport=True, args=["--auto-open-devtools-for-tabs", "--start-maximized"])
        for plat, slug, url in need:
            page = ctx.new_page()
            time.sleep(5)
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(7000)
            base = f"{plat}_{slug.replace('.', '_')}"
            ok1 = grab(page, str(C.EV / f"{base}_devtools_F12.png"))
            for _ in range(4):
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(3000)
            ok2 = grab(page, str(C.EV / f"{base}_devtools_F12_after_scroll.png"))
            res.append({"target": base, "top": ok1, "after_scroll": ok2})
            print(json.dumps(res[-1]), flush=True)
            page.close()
        ctx.close()
    (C.EV / "retake_f12.json").write_text(json.dumps(res, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
