#!/usr/bin/env python3
"""抓真實 Threads 公開 profile 截圖當佐證，輸出 threads-profile-<acct>.png 到上一層目錄。
用法：python3 capture.py   （需要 playwright + chromium）

遵守 shared/capture 的規則：只抓未登入看得到的公開頁、不存 cookie/token/cursor、
不用代理輪替、不偽裝指紋、不存整頁 HTML。手機視窗；每個帳號單次載入、間隔 ≥4 秒。
流程：載入 → 輪詢等 splash 消失且真正的貼文（a[href*=/post/]）渲染 →
只隱藏 [role=dialog] 與半透明黑色遮罩（等同 devtools 隱藏彈窗）→ 截圖。
"""
import os, time, json
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.dirname(HERE)  # q4/evidence/
ACCTS = ["zuck", "nasa", "natgeo"]
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

HIDE = r"""() => {
  let h = 0;
  document.querySelectorAll('[role=dialog]').forEach(d => { d.style.display='none'; h++; });
  document.querySelectorAll('div,section').forEach(e => {
    const s = getComputedStyle(e);
    if (s.position!=='fixed') return;
    const r = e.getBoundingClientRect();
    const covers = r.width>=innerWidth*0.9 && r.height>=innerHeight*0.9;
    const m = (s.backgroundColor||'').match(/rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*(0?\.\d+)\s*\)/);
    const translucent = m && parseFloat(m[1]) > 0 && parseFloat(m[1]) < 0.95;
    if (covers && translucent) { e.style.display='none'; h++; }
  });
  document.documentElement.style.overflow='auto';
  document.body.style.overflow='auto';
  return h;
}"""

SPLASH = r"""() => {
  const big = [...document.querySelectorAll('div')].filter(e=>{
    const s=getComputedStyle(e), r=e.getBoundingClientRect();
    return s.position!=='static' && s.display!=='none'
           && r.width>=innerWidth*0.95 && r.height>=innerHeight*0.95;
  });
  for (const e of big){ const t=(e.innerText||'').trim();
    if (t.length < 14 && e.querySelector('svg')) return true; }
  return false;
}"""

def main():
    results = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        for acct in ACCTS:
            ctx = br.new_context(viewport={"width":440,"height":1000}, device_scale_factor=2,
                                 user_agent=UA, locale="en-US", is_mobile=True, has_touch=True)
            page = ctx.new_page()
            resp = page.goto(f"https://www.threads.com/@{acct}", wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else None
            rendered = 0
            for _ in range(36):
                try:
                    rendered = page.evaluate("() => document.querySelectorAll('a[href*=\"/post/\"]').length")
                    splash = page.evaluate(SPLASH)
                except Exception:
                    rendered, splash = 0, True
                if rendered and rendered >= 3 and not splash:
                    break
                page.wait_for_timeout(500)
            page.wait_for_timeout(800)
            try: page.evaluate("window.scrollTo(0,0)")
            except Exception: pass
            try: page.evaluate(HIDE)
            except Exception: pass
            page.wait_for_timeout(120)
            page.screenshot(path=os.path.join(OUT, f"threads-profile-{acct}.png"))
            print(f"@{acct}: HTTP {status}, post-links={rendered}")
            results.append({"account": acct, "status": status, "post_links": rendered})
            ctx.close()
            time.sleep(4)
        br.close()
    print(json.dumps(results, ensure_ascii=False))

if __name__ == "__main__":
    main()
