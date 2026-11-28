"""兩種抓法的代價：請求次數 vs 留言抓到比例。全部用離線資料重算，不連 PTT。

    python q3/evidence/playwright/build_tradeoff.py          # 算數字、產三張截圖（紅框）＋寫 tradeoff.json
    python q3/evidence/playwright/build_tradeoff.py --embed  # 把三張嵌進 architecture.html 瓶頸 6 的彈窗

數字來源：q3/data/ptt_q3_census_out/articles.jsonl（7 天普查，6,224 篇成功、234,577 則留言）。
請求數：首抓 890＋列表 48＋重抓（A: 890×6；B: 890×1）。每日 890 篇取自 RESULTS.md。
這不是 F12 的實際擷取：沒有對 PTT 實跑（robots.txt 對這個 user agent 是 Disallow: /）。
"""
import asyncio
import base64
import io
import json
import pathlib
import re
import sys

from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import async_playwright

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT / "q3" / "data" / "ptt_q3_census_out" / "articles.jsonl"
HTML = ROOT / "q3" / "architecture" / "src" / "architecture.html"
OUT_JSON = HERE / "tradeoff.json"
W, H = 860, 440
CAPTION_FONT = "C:/Windows/Fonts/msjhbd.ttc"
PER_DAY = 890
LIST_PAGES = 48


def numbers():
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    ok = [r for r in rows if r["status"] == 200]
    sub = [r for r in ok if r["fetched_at"] - r["ts"] >= 7 * 86400]
    delays = [x for r in sub for x in r["delays_min"]]
    n = len(delays)

    def cum(hours):
        return 100.0 * sum(1 for x in delays if x <= hours * 60) / n

    a_req = PER_DAY + LIST_PAGES + PER_DAY * 6   # 6,278
    b_req = PER_DAY + LIST_PAGES + PER_DAY * 1   # 1,828
    cap_a = cum(168)                             # 6 次重抓，7 天內都抓
    cap_b = cum(24)                              # 發文滿 24 小時重抓 1 次
    gap = cap_a - cap_b
    return {
        "articles_subset": len(sub), "comments_subset": n,
        "first_1h": round(cum(1), 2), "cum_24h": round(cap_b, 2), "cum_168h": round(cap_a, 2),
        "a_req": a_req, "b_req": b_req, "diff_req": a_req - b_req,
        "ratio": round(a_req / b_req, 1), "gap_pp": round(gap, 2),
        "gap_comments": round(n * gap / 100, -1),
        "ratio_recheck_24h": round(100.0 * 505 / 5182, 1),
    }


CSS = """
body{margin:0;width:860px;height:440px;background:#16191c;color:#e2e9ec;
  font:15px/1.5 "Microsoft JhengHei",system-ui,sans-serif;box-sizing:border-box;padding:20px 28px;overflow:hidden}
.bar{font:13px ui-monospace,Consolas,monospace;color:#8a969c;border-bottom:1px solid #2c3338;padding-bottom:8px;margin-bottom:12px}
h1{font-size:21px;margin:0 0 14px;font-weight:700;text-wrap:balance}
.hl{outline:3px solid #ff3b30;outline-offset:5px;border-radius:4px}
"""

FRAME1 = """<!doctype html><html><head><meta charset="utf-8"><style>@CSS@
table{width:100%;border-collapse:collapse}
th{text-align:left;color:#8a969c;font-weight:600;border-bottom:1px solid #2c3338;padding:7px 6px;font-size:14px}
td{padding:12px 6px;border-bottom:1px solid #2c3338}
td.n{text-align:right;font-family:ui-monospace,Consolas,monospace;font-size:16px}
td.tot{font-weight:700;color:#fff}
.note{margin-top:18px;font-size:16px;color:#c9d3d8}
</style></head><body>
<div class="bar">離線重算｜由 7 天普查推得，不是 F12 實際擷取</div>
<h1>同一批文章，兩種抓法每天的請求數</h1>
<table>
<tr><th>抓法</th><th style="text-align:right">首抓</th><th style="text-align:right">列表頁</th><th style="text-align:right">重抓</th><th style="text-align:right">每天合計</th></tr>
<tr><td>A：第 1–6 天每天各重抓一次（規格 2）</td><td class="n">890</td><td class="n">48</td><td class="n">5,340</td><td class="n tot"><span class="hl">@A@ 次</span></td></tr>
<tr><td>B：發文滿 24 小時再重抓一次（建議）</td><td class="n">890</td><td class="n">48</td><td class="n">890</td><td class="n tot"><span class="hl">@B@ 次</span></td></tr>
</table>
<div class="note">A 每天比 B 多 <b>@DIFF@</b> 次請求，A 是 B 的 @RATIO@ 倍</div>
</body></html>"""

FRAME2 = """<!doctype html><html><head><meta charset="utf-8"><style>@CSS@
.row{display:grid;grid-template-columns:190px 1fr 90px;align-items:center;gap:12px;margin:14px 0}
.lab{font-size:15px}
.track{height:34px;background:#23292e;border-radius:6px;position:relative;overflow:hidden}
.fill{position:absolute;left:0;top:0;bottom:0;border-radius:6px}
.a{background:#2a78d6}.b{background:#eb6834}
.val{font:700 17px ui-monospace,Consolas,monospace;text-align:right}
.axis{font-size:13px;color:#8a969c;margin-top:4px}
.gap{display:inline-block;margin-top:18px;font-size:17px;font-weight:700;padding:6px 12px;color:#fff}
.cum{margin-top:16px;font-size:14px;color:#c9d3d8}
</style></head><body>
<div class="bar">離線重算｜7 天普查：1,414 篇發文已滿 7 天、54,006 則留言</div>
<h1>留言抓到的比例：A 比 B 多 @GAP@ 個百分點</h1>
<div class="row"><div class="lab">A：第 1–6 天重抓</div><div class="track"><div class="fill a" style="width:@WA@%"></div></div><div class="val">@CAPA@%</div></div>
<div class="row"><div class="lab">B：發文滿 24 小時重抓 1 次</div><div class="track"><div class="fill b" style="width:@WB@%"></div></div><div class="val">@CAPB@%</div></div>
<div class="axis">橫軸從 98% 開始畫，差距才看得到</div>
<div class="gap hl">差 @GAP@ 個百分點 ≈ 樣本中約 @GAPN@ 則留言</div>
<div class="cum">首抓（1 小時內）@F1@% → 24 小時內 @C24@% → 168 小時內 @C168@%</div>
</body></html>"""

FRAME3 = """<!doctype html><html><head><meta charset="utf-8"><style>@CSS@
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.card{background:#1f252a;border:1px solid #2c3338;border-radius:8px;padding:14px 16px;min-height:200px}
.t{font-weight:700;font-size:16px;margin-bottom:8px}
.g{color:#7fd39a;margin:6px 0;font-size:15px}
.r{color:#f0b3a8;margin:6px 0;font-size:15px}
.hl2{padding:4px 6px}
</style></head><body>
<div class="bar">離線重算｜代價對照（數字同前兩張）</div>
<h1>換成發文 24 小時重抓一次，要付出什麼代價</h1>
<div class="cols">
  <div class="card"><div class="t">B：發文滿 24 小時重抓 1 次</div>
    <div class="g">＋ 每天 @B@ 次請求，比 A 少 @DIFF@ 次</div>
    <div class="r hl2"><span class="hl">－ 代價：比 A 少抓到 @GAP@% 的留言（樣本約 @GAPN@ 則）</span></div>
    <div class="r">－ 3 天以上的文章裡，24 小時後仍有新留言的：@R24@%（505 ÷ 5,182 篇）</div>
  </div>
  <div class="card"><div class="t">A：第 1–6 天每天重抓</div>
    <div class="g">＋ 留言抓到 @CAPA@%（7 天內）</div>
    <div class="r">－ 每天 @A@ 次請求，PTT 負擔是 B 的 @RATIO@ 倍</div>
  </div>
</div>
</body></html>"""


def fill(tpl, mapping):
    out = tpl.replace("@CSS@", CSS)
    for k, v in mapping.items():
        out = out.replace("@" + k + "@", str(v))
    return out


def build_frames(d):
    fmt = lambda x: f"{x:,}"
    base = {
        "A": fmt(d["a_req"]), "B": fmt(d["b_req"]), "DIFF": fmt(d["diff_req"]), "RATIO": d["ratio"],
        "CAPA": f"{d['cum_168h']:.2f}", "CAPB": f"{d['cum_24h']:.2f}", "GAP": f"{d['gap_pp']:.2f}",
        "GAPN": fmt(int(d["gap_comments"])), "WA": round((d["cum_168h"] - 98) / 2 * 100, 1),
        "WB": round((d["cum_24h"] - 98) / 2 * 100, 1), "F1": f"{d['first_1h']:.1f}",
        "C24": f"{d['cum_24h']:.2f}", "C168": f"{d['cum_168h']:.2f}", "R24": d["ratio_recheck_24h"],
    }
    return [
        (1, fill(FRAME1, base), "每天請求：A 6,278 次、B 1,828 次（離線重算）"),
        (2, fill(FRAME2, base), "B 漏掉的留言：約 0.76%（樣本約 410 則）"),
        (3, fill(FRAME3, base), "代價：B 每天少 4,450 次，換來少抓 0.76%"),
    ]


async def render(frames):
    paths = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        for n, html, _ in frames:
            await page.set_content(html, wait_until="load")
            path = HERE / f"tradeoff_{n}.png"
            await page.screenshot(path=str(path), clip={"x": 0, "y": 0, "width": W, "height": H})
            paths.append(path)
        await browser.close()
    return paths


def to_webp(path, caption):
    im = Image.open(path).convert("RGBA")
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


def embed(d, frames):
    captions = {1: "每天請求：A 6,278 次、B 1,828 次", 2: "B 漏掉約 0.76%（樣本約 410 則）",
                3: "代價：B 每天少 4,450 次，少抓 0.76%"}
    js_frames = []
    for n, _, _ in frames:
        src = to_webp(HERE / f"tradeoff_{n}.png", captions[n])
        js_frames.append('    { src: "%s", cap: %s, rules: [%d] }' % (
            src, json.dumps(captions[n], ensure_ascii=False), n))
    to_block = ("  // 「兩種抓法」離線重算截圖（build_tradeoff.py 產生；紅框＝那一處）。\n"
                "  var TO = [\n" + ",\n".join(js_frames) + "\n  ];\n")

    html = HTML.read_text(encoding="utf-8")
    html = re.sub(r"  // 「兩種抓法」離線重算截圖.*?\n  var TO = \[.*?\n  \];\n", "", html, flags=re.S)
    if "  var EV = {" not in html:
        raise RuntimeError("找不到 var EV = {")
    html = html.replace("  var EV = {", to_block + "  var EV = {", 1)

    gap_n = f"{int(d['gap_comments']):,}"
    rule1 = "每天請求：A 每天重抓 6 次＝6,278 次；B 發文滿 24 小時重抓 1 次＝1,828 次"
    rule2 = (f"留言抓到：A 在 7 天內抓到 {d['cum_168h']:.2f}%；B 在 24 小時重抓一次，"
             f"抓到 {d['cum_24h']:.2f}%，差 {d['gap_pp']:.2f} 個百分點")
    rule3 = (f"代價：B 每天少 4,450 次請求，換來少抓 {d['gap_pp']:.2f}% 的留言（樣本約 {gap_n} 則）；"
             "3 天以上的文章裡，24 小時後仍有新留言的 9.7%")
    entry = (
        "    s: {\n"
        '      t: "兩種抓法的代價：請求次數 vs 留言抓到比例",\n'
        '      v: "紅框＝那一處。數字是 7 天普查離線重算，不是 F12 實際擷取（沒有對 PTT 實跑）",\n'
        "      rules: [\n"
        f'        "{rule1}",\n'
        f'        "{rule2}",\n'
        f'        "{rule3}"\n'
        "      ],\n"
        "      player: TO\n"
        "    },\n"
    )
    if "    s: {" not in html:
        if "    g: {\n" not in html:
            raise RuntimeError("找不到 g 項")
        html = html.replace("    g: {\n", entry + "    g: {\n", 1)

    old_li = '    <li>若你認為某項規格不合理或定義不清，可以提出修改，但必須說明代價。</li>'
    new_li = ('    <li><a class="ev" data-ev="s" href="../../evidence/playwright/tradeoff_1.png" '
              'target="_blank" rel="noopener">若你認為某項規格不合理或定義不清，可以提出修改，但必須說明代價。</a></li>')
    if old_li in html:
        html = html.replace(old_li, new_li, 1)
    elif 'data-ev="s"' not in html:
        raise RuntimeError("找不到瓶頸 6 的 li")

    HTML.write_text(html, encoding="utf-8")
    print("embedded", len(frames), "frames into", HTML)


def main():
    d = numbers()
    OUT_JSON.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(d, ensure_ascii=False, indent=2))
    frames = build_frames(d)
    if "--embed" in sys.argv:
        embed(d, frames)
        return
    paths = asyncio.run(render(frames))
    for p_ in paths:
        print("wrote", p_)


if __name__ == "__main__":
    main()
