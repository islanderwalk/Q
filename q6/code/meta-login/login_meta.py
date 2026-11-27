# Opens a headed Chromium (persistent profile) with FB / IG / Threads login pages.
# The operator types credentials manually; this script never reads or types passwords.
# It only polls cookie NAMES to detect login, and writes a masked status file.
import json, time, hashlib, sys, pathlib
from playwright.sync_api import sync_playwright
import os  # noqa: E402
D = pathlib.Path(os.environ.get("META_WORKDIR") or pathlib.Path(__file__).parent)  # outputs + profile live here
PROFILE = D / "profile"
STATUS = D / "login_status.json"
SITES = {
    "facebook":  ("https://www.facebook.com/login/", "facebook.com", "c_user"),
    "instagram": ("https://www.instagram.com/accounts/login/", "instagram.com", "sessionid"),
    "threads":   ("https://www.threads.com/login", "threads.", "sessionid"),
}
def mask(v): return "sha256:" + hashlib.sha256(v.encode()).hexdigest()[:10] + f" (len {len(v)})"
deadline = time.time() + 60 * int(sys.argv[1] if len(sys.argv) > 1 else 45)
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(str(PROFILE), headless=False, locale="zh-TW",
                                               viewport={"width": 1280, "height": 860})
    pages = {}
    for k, (url, _, _) in SITES.items():
        pg = ctx.pages[0] if not pages and ctx.pages else ctx.new_page()
        pg.goto(url, timeout=60000); pages[k] = pg
    done = {}
    while time.time() < deadline and len(done) < len(SITES):
        cookies = ctx.cookies()
        for k, (_, dom, name) in SITES.items():
            if k in done: continue
            hit = [c for c in cookies if dom in c["domain"] and c["name"] == name and c["value"]]
            if hit:
                c = hit[0]
                done[k] = {"cookie": name, "domain": c["domain"], "value": mask(c["value"]),
                           "httpOnly": c["httpOnly"], "secure": c["secure"],
                           "expires": c["expires"], "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        STATUS.write_text(json.dumps({"logged_in": done, "waiting_for": [k for k in SITES if k not in done]},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(5)
    time.sleep(8)  # let post-login redirects settle
    ctx.close()
print(json.dumps(done, ensure_ascii=False))
