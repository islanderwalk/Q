# Builds a self-contained evidence page (site/index.html + site/shots/*.jpg) from the local outputs.
# Only the REDACTED screenshots are used; cookie values are already sha256-masked upstream.
import json, html, pathlib, collections
from PIL import Image

import os  # noqa: E402
D = pathlib.Path(os.environ.get("META_WORKDIR") or pathlib.Path(__file__).parent)  # outputs + profile live here
EV = D / "evidence"
SITE = D / "site"
(SITE / "shots").mkdir(parents=True, exist_ok=True)
E = html.escape

cmp = json.loads((EV / "compare_loggedout_vs_loggedin.json").read_text(encoding="utf-8"))
sess = json.loads((EV / "session_masked.json").read_text(encoding="utf-8"))
ledger = json.loads((EV / "api_ledger_masked.json").read_text(encoding="utf-8"))
contam = json.loads((EV / "instagram_contamination.json").read_text(encoding="utf-8"))
summ = json.loads((EV / "loggedin_summary.json").read_text(encoding="utf-8"))

PLAT = {"facebook": "Facebook", "instagram": "Instagram", "threads": "Threads"}
FIELDS = [("post_id", "貼文 ID"), ("url", "連結"), ("published_at", "發文時間"), ("text", "內文"), ("author", "作者"),
          ("like_count", "讚數"), ("comment_count", "留言數"), ("play_count", "播放數"), ("sub_type", "類型")]
KINDS = [("top", "頁面頂部"), ("after_scroll", "捲動後"), ("devtools_F12", "F12 Network｜頂部"), ("devtools_F12_after_scroll", "F12 Network｜捲動後")]

# screenshots -> jpeg
shots = {}
for s in summ:
    base = f"{s['platform']}_{s['target'].replace('.', '_')}"
    for k, _ in KINDS:
        src = EV / "redacted" / f"{base}_{k}.png"
        if not src.exists():
            continue
        dst = SITE / "shots" / f"{base}_{k}.jpg"
        im = Image.open(src).convert("RGB")
        if im.width > 1600:
            im = im.resize((1600, round(im.height * 1600 / im.width)), Image.LANCZOS)
        im.save(dst, "JPEG", quality=80, optimize=True)
        shots.setdefault(s["platform"], {}).setdefault(s["target"], []).append((k, dst.name))


def fmt_dt(v):
    return E(v[:16].replace("T", " ")) if v else "—"


def pct(n, d):
    return 0 if not d else round(100 * n / d)


out = []
# ---------- summary strip
cards = []
for p, c in cmp.items():
    lo, li = c["logged_out"], c["logged_in"]
    mult = f"×{li['posts'] / lo['posts']:.0f}" if lo["posts"] else ""
    cards.append(f"""<div class="stat"><div class="stat-h">{PLAT[p]}</div>
<div class="stat-n"><span class="muted">{lo['posts']}</span><span class="arr">→</span><b>{li['posts']:,}</b><span class="mult">{mult}</span></div>
<div class="stat-s">登入牆 0 次｜最早 {fmt_dt(li['earliest'])}</div></div>""")
out.append(f'<section class="stats">{"".join(cards)}</section>')

# ---------- comparison tables
tabs = []
for p, c in cmp.items():
    lo, li = c["logged_out"], c["logged_in"]
    rows = [f"<tr><th>篇數</th><td class=num>{lo['posts']}</td><td class=num>{li['posts']:,}</td></tr>",
            f"<tr><th>最早</th><td>{fmt_dt(lo['earliest'])}</td><td>{fmt_dt(li['earliest'])}</td></tr>",
            f"<tr><th>最新</th><td>{fmt_dt(lo['latest'])}</td><td>{fmt_dt(li['latest'])}</td></tr>"]
    for f, label in FIELDS:
        a, b = lo["field_fill"][f], li["field_fill"][f]
        pa, pb = pct(a, lo["posts"]), pct(b, li["posts"])
        rows.append(f"<tr><th>{label}</th><td><span class=bar><i style='width:{pa}%'></i></span><span class=num>{a}/{lo['posts']}</span></td>"
                    f"<td><span class='bar in'><i style='width:{pb}%'></i></span><span class=num>{b}/{li['posts']}</span></td></tr>")
    rows.append(f"<tr><th>文字重疊</th><td colspan=2>兩邊相同 <b>{c['overlap_by_text']}</b>｜只在免登入 {c['only_logged_out']}｜只在登入 {c['only_logged_in']:,}</td></tr>")
    src = lambda d: "、".join(f"{E(k.lstrip('@'))} {v}" for k, v in d.items())
    rows.append(f"<tr><th>各帳號篇數</th><td>{src(lo['per_source'])}</td><td>{src(li['per_source'])}</td></tr>")
    tabs.append(f"""<div class="cmp"><h3>{PLAT[p]}</h3><div class="scroll"><table><thead><tr><th></th><th>免登入（沿用原爬蟲）</th><th>登入帳號</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div></div>""")
out.append(f'<section id="compare"><h2>免登入 vs 登入</h2><p class="lede">同一批目標頁。欄位列的長條是「有值的篇數比例」。</p><div class="cmp-grid">{"".join(tabs)}</div></section>')

# ---------- findings
drop_rows = "".join(f"<tr><td>{E(s)}</td><td>{E(a)}</td><td class=num>{n}</td></tr>" for s, a, n in contam["dropped_by_source_author"])
out.append(f"""<section id="findings"><h2>驗證時抓到的問題</h2><div class="find">
<article class="issue warn"><h3>Instagram 混入登入帳號的首頁動態</h3><p>登入後的頁面會夾帶首頁動態和推薦貼文，例如 dpk.ink、iamironj 這兩則同時被算進 4 個帳號。以「作者 = 目標帳號」剔除 {contam['raw'] - contam['clean']} 筆，原始 {contam['raw']:,} 筆保留，清洗後 {contam['clean']:,} 筆才進比對。nasa 底下多數是合作作者（astro_*、nasajpl），規則一樣剔除。</p>
<details><summary>剔除明細（來源頁 × 作者）</summary><div class="scroll"><table class="mini"><thead><tr><th>來源頁</th><th>作者</th><th>筆數</th></tr></thead><tbody>{drop_rows}</tbody></table></div></details></article>
<article class="issue"><h3>IG 每帳號 312 篇是捲動上限</h3><p>每頁固定捲 25 次，所以 4 個帳號都停在 312 篇左右。中央社回溯到 2026-04；民視一天發 30 多篇，312 篇只涵蓋 8 天。要到 2023-01，每帳號估計要捲數百次，會用登入帳號大量請求。</p></article>
<article class="issue warn"><h3>民視 FB（ftvnews53）0 篇</h3><p>頁面本身顯示「目前無法查看此內容」，是網址代號不對，不是爬蟲失敗。需要正確的民視粉專網址。</p></article>
<article class="issue"><h3>欄位缺口</h3><p>FB 兩邊都沒有讚數、留言數。Threads 登入版缺連結和留言數。IG 沒有播放數。</p></article>
<article class="issue ok"><h3>沒有外洩憑證</h3><p>所有輸出檔掃過，沒有任何 cookie 或 token 原值。cookie 只留 sha256 前 10 碼和長度。截圖已模糊頭像、通知數和分頁標題（紅框處）。Threads F12 截圖的請求網址看得到 lsd 參數前幾碼，那是每次載入都換的防 CSRF 值，不是登入憑證。</p></article>
</div></section>""")

# ---------- evidence chain
chains = []
for p in PLAT:
    ck = sess["platforms"].get(p, {}).get("cookies", [])
    led = [e for e in ledger if e["platform"] == p]
    ops = collections.Counter((e.get("friendly_name") or e["endpoint"].split("/", 3)[-1], e.get("doc_id"), e["status"]) for e in led)
    keys = collections.Counter()
    for e in led:
        kp = e.get("resp_key_paths") or []
        if isinstance(kp, str):
            try:
                kp = json.loads(kp.replace("'", '"'))
            except Exception:
                kp = []
        keys.update(kp)
    want = ("text", "taken_at", "creation_time", "code", "like_count", "comment_count", "play_count", "username", "post_id", "permalink", "reaction")
    interesting = [k for k, _ in keys.most_common(600) if any(t in k.split(".")[-1] for t in want)][:16]
    li = cmp[p]["logged_in"]
    c1 = "".join(f"<li><b>{E(c['name'])}</b><span>{E(c['domain'])}{' · httpOnly' if c['httpOnly'] else ''}</span><code>{E(c['value'])}</code></li>" for c in ck[:10])
    c2 = "".join(f"<li><b>{E(str(o[0]))}</b><span>doc_id {E(str(o[1]))} · HTTP {E(str(o[2]))}</span><code>×{n}</code></li>" for o, n in ops.most_common(10))
    c3 = "".join(f"<li><code>{E(k)}</code></li>" for k in interesting)
    c4 = "".join(f"<li><b>{lbl}</b><code>{li['field_fill'][f]}/{li['posts']}</code></li>" for f, lbl in FIELDS)
    chains.append(f"""<div class="chain"><h3>{PLAT[p]} <small>{len(led)} 個 API 回應｜{len(ck)} 個 cookie</small></h3><div class="steps">
<div class="step"><h4><i>1</i>Session cookie（遮罩）</h4><ul>{c1 or '<li>無</li>'}</ul></div>
<div class="step"><h4><i>2</i>頁面發出的 API 請求</h4><ul>{c2 or '<li>無</li>'}</ul></div>
<div class="step"><h4><i>3</i>回應 JSON 欄位路徑</h4><ul class="paths">{c3 or '<li>無</li>'}</ul></div>
<div class="step"><h4><i>4</i>落地欄位（有值篇數）</h4><ul>{c4}</ul></div></div></div>""")
out.append(f'<section id="chain"><h2>證據鏈：session → API → JSON → 欄位</h2><p class="lede">擷取時間 {E(sess.get("captured_at", "")[:19].replace("T", " "))}。每一格都來自本機的遮罩紀錄檔，可以和下方 F12 截圖裡的請求對照。</p>{"".join(chains)}</section>')

# ---------- gallery
gal = []
for p in PLAT:
    for tgt, lst in shots.get(p, {}).items():
        meta = next((s for s in summ if s["platform"] == p and s["target"] == tgt), {})
        figs = "".join(f"<figure><a href='shots/{fn}' class='zoom'><img loading='lazy' src='shots/{fn}' alt='{PLAT[p]} {E(tgt)} {lbl}'></a><figcaption>{lbl}</figcaption></figure>"
                       for k, fn in lst for kk, lbl in KINDS if kk == k)
        gal.append(f"""<div class="target" data-p="{p}"><h3>{PLAT[p]} · {E(tgt)} <small>HTTP {meta.get('status')} · 捲動 {meta.get('scrolls')} 次 · API 回應 {meta.get('api_responses')} · 抓到 {meta.get('posts')} 篇</small></h3><div class="shots">{figs}</div></div>""")
filters = "".join(f"<button type='button' data-f='{p}' id='f-{p}'>{PLAT[p]}</button>" for p in PLAT)
out.append(f"""<section id="shots"><h2>Playwright 截圖（{sum(len(v) for d in shots.values() for v in d.values())} 張，已遮罩）</h2>
<div class="filters"><button type="button" data-f="all" id="f-all" aria-pressed="true">全部</button>{filters}</div>{"".join(gal)}</section>""")

page = (pathlib.Path(__file__).parent / "site_template.html").read_text(encoding="utf-8").replace("<!--BODY-->", "\n".join(out))
(SITE / "index.html").write_text(page, encoding="utf-8")
print("shots", sum(len(v) for d in shots.values() for v in d.values()), "size MB", round(sum(f.stat().st_size for f in (SITE / "shots").iterdir()) / 1e6, 1))
