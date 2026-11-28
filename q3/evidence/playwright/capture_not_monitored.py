"""截「三件不監控的事」實際觸發時的畫面，畫紅框，併進架構頁瓶頸 5 的動畫彈窗。

    python q3/evidence/playwright/capture_not_monitored.py          # 截圖＋寫 not_monitored.json
    python q3/evidence/playwright/capture_not_monitored.py --embed  # 把截圖嵌進 architecture.html

三件事（對應 summary.txt §5「只上看板、不告警」的 (1)(2)(3)）：
  1 單次請求失敗：用 Playwright 攔截一次請求、回 503（模擬，不真的去壓 PTT；畫面上會註明）
  2 事後刪文 404：PTT 上的已刪文章 M.1792887225.A.5F3，回 404
  3 截斷留言：PTT 上的截斷文章 M.1792462201.A.2BD，網頁寫「檔案過大」

PTT 只各打 2 次（間隔 ≥ 1.5 秒），不碰爬蟲的 docker，不改 answer 目錄裡的既有證據圖。
"""
import asyncio
import base64
import io
import json
import pathlib
import re
import sys
import time

from playwright.async_api import async_playwright

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HTML = ROOT / "q3" / "architecture" / "src" / "architecture.html"
OUT_JSON = HERE / "not_monitored.json"
PTT = "https://www.ptt.cc/bbs/Gossiping"
DELETED_AID = "M.1792887225.A.5F3"
TRUNC_AID = "M.1792462201.A.2BD"
FAKE_503 = "https://www.ptt.cc/bbs/Gossiping/M.1700000000.A.000.html"
W, FH = 860, 440
GAP = 1.5
_last = [0.0]

OVERLAY = r"""
(spec) => {
  document.querySelectorAll('.qa-ann').forEach(e => e.remove());
  const r = spec.rect, sx = scrollX, sy = scrollY;
  const box = document.createElement('div'); box.className = 'qa-ann';
  Object.assign(box.style, {position: 'absolute', zIndex: 9999, pointerEvents: 'none',
    left: (r.x + sx - 6) + 'px', top: (r.y + sy - 6) + 'px', width: (r.w + 12) + 'px', height: (r.h + 12) + 'px',
    border: '3px solid #ff3b30', borderRadius: '6px', boxShadow: '0 0 0 2px rgba(255,59,48,.25)', boxSizing: 'border-box'});
  document.body.appendChild(box);
  const l = document.createElement('div'); l.className = 'qa-ann'; l.textContent = spec.label;
  Object.assign(l.style, {position: 'absolute', zIndex: 9999, pointerEvents: 'none', background: '#ff3b30', color: '#fff',
    font: '600 15px/1.35 system-ui, sans-serif', padding: '3px 8px', borderRadius: '5px', whiteSpace: 'nowrap',
    left: (r.x + sx) + 'px', top: (r.y + r.h + sy + 14) + 'px'});
  document.body.appendChild(l);
  const b = document.createElement('div'); b.className = 'qa-ann qa-badge'; b.textContent = spec.badge;
  Object.assign(b.style, {position: 'absolute', zIndex: 9999, pointerEvents: 'none', background: 'rgba(20,28,32,.92)',
    color: '#e2e9ec', font: '600 14px/1.4 ui-monospace, Consolas, monospace', padding: '4px 10px', borderRadius: '6px',
    border: '1px solid #ff3b30', left: '8px', top: (sy + 8) + 'px'});
  document.body.appendChild(b);
  return {cy: r.y + r.h / 2 + sy, docH: Math.max(document.documentElement.scrollHeight, 440)};
}
"""

SCROLL_TO = r"""
(needle) => {
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    if (n.textContent.includes(needle)) {
      const el = n.parentElement;
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return {x: r.x, y: r.y, w: r.width, h: r.height};
    }
  }
  return null;
}
"""


async def polite():
    wait = _last[0] + GAP - time.monotonic()
    if wait > 0:
        await asyncio.sleep(wait)
    _last[0] = time.monotonic()


async def shot(page, name, needle, label, badge):
    """把含 needle 的那一處捲到畫面中間、畫紅框，截一張 860×440（視窗座標，不用整頁截圖）。"""
    rect = await page.evaluate(SCROLL_TO, needle)
    if rect is None:
        raise RuntimeError(f"找不到「{needle}」")
    await page.evaluate(OVERLAY, {"rect": rect, "label": label, "badge": badge})
    y0 = max(0, min(round(rect["y"] + rect["h"] / 2 - FH / 2), 900 - FH))
    await page.evaluate("([y, h]) => { const b = document.querySelector('.qa-badge');"
                        " if (b) { b.style.top = (scrollY + y + h - 40) + 'px'; } }", [y0, FH])
    path = HERE / f"not_monitored_{name}.png"
    await page.screenshot(path=str(path), clip={"x": 0, "y": y0, "width": W, "height": FH}, full_page=False)
    return path


async def capture():
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": W, "height": 900}, device_scale_factor=1, locale="zh-TW")
        await ctx.add_cookies([{"name": "over18", "value": "1", "domain": "www.ptt.cc", "path": "/"}])

        # 1 單次請求失敗：攔截這一次請求，回 503（不真的送到 PTT）
        async def fail_once(route):
            await route.fulfill(status=503, content_type="text/plain; charset=utf-8",
                                body="503 Service Unavailable（模擬：Playwright 攔截）")
        await ctx.route(FAKE_503, fail_once)
        page = await ctx.new_page()
        await page.goto(FAKE_503, wait_until="domcontentloaded")
        path = await shot(page, "1_503", "503 Service Unavailable", "回 503：這輪跳過、下輪補", "① 單次請求失敗（模擬攔截，未壓 PTT）")
        results.append({"n": 1, "file": path.name, "status": 503, "real": False})

        # 2 事後刪文 404：PTT
        await ctx.unroute(FAKE_503)
        await polite()
        page = await ctx.new_page()
        resp = await page.goto(f"{PTT}/{DELETED_AID}.html", wait_until="domcontentloaded")
        status = resp.status
        if status != 404:
            raise RuntimeError(f"刪文頁不是 404（實際 {status}）")
        path = await shot(page, "2_404", "404 - Not Found", "已刪：回 404，最多晚 1 天下架", "② 事後刪文（PTT，回 404）")
        results.append({"n": 2, "file": path.name, "status": status, "real": True, "aid": DELETED_AID})

        # 3 截斷留言：PTT
        await polite()
        resp = await page.goto(f"{PTT}/{TRUNC_AID}.html", wait_until="domcontentloaded")
        status = resp.status
        if status != 200:
            raise RuntimeError(f"截斷文頁狀態 {status}")
        path = await shot(page, "3_truncated", "檔案過大", "留言被截斷：網頁版拿不到後面的", "③ 截斷留言（PTT，寫「檔案過大」）")
        results.append({"n": 3, "file": path.name, "status": status, "real": True, "aid": TRUNC_AID})

        await browser.close()
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


CAPTION_FONT = "C:/Windows/Fonts/msjhbd.ttc"


def to_webp(path, caption=None):
    """轉 WebP 嵌入用；caption 有值時在右下角疊一塊短字幕（原始 PNG 不動）。"""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(path).convert("RGB")
    if caption:
        im = im.convert("RGBA")
        font = ImageFont.truetype(CAPTION_FONT, 18)
        layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        l, t, r, b = d.textbbox((0, 0), caption, font=font)
        tw, th = r - l, b - t
        pad_x, pad_y, margin = 12, 7, 10
        x1 = im.width - margin
        y1 = im.height - margin
        x0 = x1 - tw - 2 * pad_x
        y0 = y1 - th - 2 * pad_y
        d.rounded_rectangle((x0, y0, x1, y1), radius=6, fill=(20, 28, 32, 235),
                            outline=(255, 59, 48, 255), width=2)
        d.text((x0 + pad_x - l, y0 + pad_y - t), caption, font=font, fill=(226, 233, 236, 255))
        im = Image.alpha_composite(im, layer).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=82, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def embed():
    frames = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    # 字幕（右下角，精簡）與下方說明文字（不再加①②③，播放器會自己加編號，避免「② ②」）
    corner = {
        1: "單次失敗：這輪跳過，下輪補",
        2: "刪文 404：最多晚 1 天下架",
        3: "截斷留言：網頁版拿不到",
    }
    caps = {
        1: "單次請求失敗（模擬 503）：這輪跳過，下輪補抓",
        2: "事後刪文（PTT 實際 404）：13 篇、0.2%，最多晚 1 天下架",
        3: "截斷留言（PTT）：6,224 篇中 2 篇，網頁版拿不到",
    }
    js_frames = []
    for f in frames:
        src = to_webp(HERE / f["file"], corner[f["n"]])
        js_frames.append('    { src: "%s", cap: %s, rules: [%d] }'
                         % (src, json.dumps(caps[f["n"]], ensure_ascii=False), f["n"]))
    un_block = ("  // 「不監控」三件事的實際觸發截圖（capture_not_monitored.py 產生；紅框＝那一處）。\n"
                "  var UN = [\n" + ",\n".join(js_frames) + "\n  ];\n")

    html = HTML.read_text(encoding="utf-8")
    html = re.sub(r"  // 「不監控」三件事的實際觸發截圖.*?\n  var UN = \[.*?\n  \];\n", "", html, flags=re.S)
    if "  var EV = {" not in html:
        raise RuntimeError("找不到 var EV = {")
    html = html.replace("  var EV = {", un_block + "  var EV = {", 1)

    entry = (
        "    u: {\n"
        '      t: "不監控的三件事，實際觸發時長這樣",\n'
        '      v: "紅框＝那一處。只上看板、不告警；會告警的只有結果（規格 1、規格 2、每日檢查、爬蟲心跳、整輪狀態）",\n'
        "      rules: [\n"
        '        "單次請求失敗：這輪跳過，下輪補抓；被擋時整輪失敗才告警",\n'
        '        "事後刪文 404：標記後下架，整批 404 過多才擋下寫入",\n'
        '        "截斷留言：6,224 篇中只有 2 篇，是 PTT 網頁版本身拿不到的後半段；沒有其他來源可以補，告警也無從處理，所以只在看板上留一個數字讓人看到"\n'
        "      ],\n"
        "      player: UN\n"
        "    },\n"
    )
    if "    u: {" not in html:
        html = html.replace("    g: {\n", entry + "    g: {\n", 1)

    old_li = re.search(r'    <li>你決定不監控哪三件事？各自的風險為何可以接受？</li>', html)
    new_li = ('    <li><a class="ev" data-ev="u" href="../../evidence/playwright/not_monitored_2_404.png" '
              'target="_blank" rel="noopener">你決定不監控哪三件事？各自的風險為何可以接受？</a></li>')
    if old_li:
        html = html.replace(old_li.group(0), new_li, 1)
    elif 'data-ev="u"' not in html:
        raise RuntimeError("找不到瓶頸 5 的 li")

    HTML.write_text(html, encoding="utf-8")
    print("embedded", len(frames), "frames into", HTML)


if __name__ == "__main__":
    if "--embed" in sys.argv:
        embed()
    else:
        res = asyncio.run(capture())
        print(json.dumps(res, ensure_ascii=False, indent=2))
