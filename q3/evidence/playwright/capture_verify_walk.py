"""用 Playwright 截 PTT 八卦板列表頁，畫紅框，做成「每日檢查」不變量的動畫分鏡。

    python q3/evidence/playwright/capture_verify_walk.py

分鏡（輸出到同一資料夾的 verify_walk_*.png ＋ verify_walk.json）：
  1 最新頁 index.html：紅框框「上頁」
  2 往回一頁：紅框框「上頁」
  3 走到出現早於 7 天前的文章的那一頁：紅框框那幾列
  4 走完再讀一次 index.html：「上頁」還是指向同一頁 → 最新頁沒變
  5 列表上的文章 ID：紅框＝要求入庫；灰框＝刪文佔位，不要求
  6 資料庫（BigQuery 模擬器的 posts）查這個 ID：status 200

只讀幾頁，不整條走（整條走是 verify.py 每天做的事）；每次請求間隔 ≥ 1.5 秒。
資料庫查詢透過 `wsl -u root -- docker exec q3pipe-crawler-1`。
"""
import asyncio
import json
import pathlib
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from playwright.async_api import async_playwright

OUT = pathlib.Path(__file__).resolve().parent
BASE = "https://www.ptt.cc"
BOARD = "Gossiping"
TW = timezone(timedelta(hours=8))
GAP = 1.5
AID_RE = re.compile(r"/bbs/Gossiping/(M\.(\d+)\.A\.[0-9A-F]+)\.html")

_last = [0.0]


async def polite():
    wait = _last[0] + GAP - time.monotonic()
    if wait > 0:
        await asyncio.sleep(wait)
    _last[0] = time.monotonic()


def url(n=None):
    return f"{BASE}/bbs/{BOARD}/index{'' if n is None else n}.html"


def fmt(ts):
    return datetime.fromtimestamp(ts, TW).strftime("%m/%d %H:%M")


def db_status(aids):
    """posts 表裡這些 ID 的狀態（查的是 verify.py 用的同一張表）。"""
    code = (
        "import json,sys\n"
        "from q3pipe.bq import Warehouse\nfrom q3pipe.config import Config\n"
        "cfg=Config.from_env(); wh=Warehouse(cfg)\n"
        "aids=json.loads(sys.argv[1])\n"
        "rows=wh.q(f\"SELECT aid, status, title, published_at, last_fetched_at FROM {wh.t('posts')} "
        "WHERE board=@b AND aid IN UNNEST(@a)\", b=cfg.board, a=aids)\n"
        "print(json.dumps({r.aid: dict(r.items()) for r in rows}, default=str, ensure_ascii=False))\n"
    )
    r = subprocess.run(["wsl", "-u", "root", "--", "docker", "exec", "-i", "q3pipe-crawler-1",
                        "python", "-c", code, json.dumps(aids)],
                       capture_output=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace"))
    return json.loads(r.stdout.decode("utf-8"))


ANNOTATE = r"""
(spec) => {
  document.querySelectorAll('.qa-ann').forEach(e => e.remove());
  const add = (html, style) => { const d = document.createElement('div'); d.className = 'qa-ann';
    d.innerHTML = html; Object.assign(d.style, {position: 'absolute', zIndex: 9999, pointerEvents: 'none'}, style);
    document.body.appendChild(d); return d; };
  const box = (el, kind, label, side) => {
    const r = el.getBoundingClientRect(); const pad = 4;
    const red = kind === 'red';
    const c = red ? '#ff3b30' : '#b0b8bf';
    add('', {left: (r.left + scrollX - pad) + 'px', top: (r.top + scrollY - pad) + 'px',
             width: (r.width + pad * 2) + 'px', height: (r.height + pad * 2) + 'px',
             border: (red ? '3px solid ' : '3px dashed ') + c, borderRadius: '6px',
             boxShadow: red ? '0 0 0 2px rgba(255,59,48,.25)' : 'none'});
    if (label) {
      const l = add(label, {background: c, color: red ? '#fff' : '#111', font: '600 15px/1.35 system-ui, sans-serif',
                            padding: '3px 8px', borderRadius: '5px', whiteSpace: 'nowrap'});
      const lr = l.getBoundingClientRect();
      let x = side === 'left' ? r.left - lr.width - 12 : r.right + 12;
      let y = r.top + r.height / 2 - lr.height / 2;
      if (side === 'below') { x = r.left; y = r.bottom + 8; }
      if (side === 'above') { x = r.left; y = r.top - lr.height - 8; }
      if (side === 'inside') { x = r.right - lr.width - 10; y = r.top + 4; }   // 框內右上（列表列的右半邊是空的）
      x = Math.max(4, Math.min(x, innerWidth - lr.width - 4));
      l.style.left = (x + scrollX) + 'px'; l.style.top = (y + scrollY) + 'px';
    }
  };
  const prevBtn = () => [...document.querySelectorAll('.btn-group-paging a')].find(a => a.textContent.includes('上頁'));
  const rowOf = (aid) => [...document.querySelectorAll('.r-ent')].find(r => {
      const a = r.querySelector('.title a'); return a && a.getAttribute('href').includes(aid); });
  const deletedRows = () => [...document.querySelectorAll('.r-ent')].filter(r => !r.querySelector('.title a'));
  for (const b of spec.boxes) {
    let el = null;
    if (b.target === 'prev') el = prevBtn();
    else if (b.target === 'row') el = rowOf(b.aid);
    else if (b.target === 'deleted') el = deletedRows()[b.i || 0];
    else if (b.target === 'sel') el = document.querySelector(b.sel);
    if (!el) throw new Error('target not found: ' + JSON.stringify(b));
    box(el, b.kind, b.label, b.side);
  }
  if (spec.badge) add(spec.badge, {left: '8px', top: (scrollY + 8) + 'px', background: 'rgba(20,28,32,.92)', color: '#e2e9ec',
      font: '600 14px/1.4 ui-monospace, Consolas, monospace', padding: '4px 10px', borderRadius: '6px',
      border: '1px solid #ff3b30'}).classList.add('qa-badge');
}
"""

PARSE = r"""
() => {
  const prev = [...document.querySelectorAll('.btn-group-paging a')].find(a => a.textContent.includes('上頁'));
  const rows = [];
  for (const r of document.querySelectorAll('.r-list-container > div')) {
    if (r.classList.contains('r-list-sep')) break;   // 置底公告以下不算
    if (!r.classList.contains('r-ent')) continue;
    const a = r.querySelector('.title a');
    rows.push({href: a ? a.getAttribute('href') : null, title: r.querySelector('.title').textContent.trim(),
               date: (r.querySelector('.date') || {}).textContent});
  }
  return {prev: prev ? prev.getAttribute('href') : null, rows};
}
"""


def parse_rows(info):
    out = []
    for r in info["rows"]:
        m = AID_RE.search(r["href"] or "")
        out.append({"aid": m.group(1) if m else None, "ts": int(m.group(2)) if m else None,
                    "title": r["title"], "date": (r["date"] or "").strip()})
    return out


def prev_no(info):
    m = re.search(r"index(\d+)\.html", info["prev"] or "")
    return int(m.group(1)) if m else None


async def main():
    shots, meta = [], {"captured_at": None, "frames": []}
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 860, "height": 900}, device_scale_factor=1,
                                        locale="zh-TW")
        await ctx.add_cookies([{"name": "over18", "value": "1", "domain": "www.ptt.cc", "path": "/"}])
        # 只放行 PTT 自己的頁面與樣式，其它（統計、廣告）一律擋掉，也少打外部請求
        await ctx.route("**/*", lambda r: r.continue_() if "ptt.cc" in r.request.url and
                        r.request.resource_type in ("document", "stylesheet", "font") else r.abort())
        page = await ctx.new_page()

        async def visit(u):
            await polite()
            resp = await page.goto(u, wait_until="domcontentloaded")
            await page.wait_for_load_state("load")
            info = await page.evaluate(PARSE)
            return resp.status, info

        async def snap(name, spec):
            await page.evaluate(ANNOTATE, spec)
            # 每張一樣大（860×440，動畫換頁不跳）：以所有標註的中心為準；徽章移到截圖區左上角
            clip = await page.evaluate("""(top0) => {
              const FH = 440;
              const els = [...document.querySelectorAll('.qa-ann')].filter(e => !e.classList.contains('qa-badge'));
              let t = Infinity, b = 0;
              for (const e of els) { const r = e.getBoundingClientRect(); t = Math.min(t, r.top + scrollY); b = Math.max(b, r.bottom + scrollY); }
              const H = Math.max(document.documentElement.scrollHeight, FH);
              let y0 = top0 ? 0 : Math.round((t + b) / 2 - FH / 2 + 20);
              y0 = Math.max(0, Math.min(y0, H - FH));
              if (t < y0 || b > y0 + FH) throw new Error('annotations do not fit in ' + FH + 'px: ' + t + '..' + b);
              const badge = document.querySelector('.qa-badge');
              if (badge) badge.style.top = (y0 + 8) + 'px';
              return {x: 0, y: y0, width: innerWidth, height: FH};
            }""", bool(spec.get("top")))
            path = OUT / f"verify_walk_{name}.png"
            await page.screenshot(path=str(path), clip=clip, full_page=True)
            shots.append(path.name)
            return path.name

        now = datetime.now(timezone.utc)
        since_ts = int((now - timedelta(days=7)).timestamp())
        meta["captured_at"] = now.astimezone(TW).isoformat(timespec="seconds")
        meta["since"] = datetime.fromtimestamp(since_ts, TW).isoformat(timespec="seconds")

        # 1 最新頁
        st, info = await visit(url())
        head = prev_no(info) + 1
        meta["head_start"] = head
        f1 = await snap("1_head", {"badge": f"GET index.html → {st}",
                                   "boxes": [{"target": "prev", "kind": "red", "side": "below",
                                              "label": f"上頁 → index{head - 1}.html，所以最新頁是第 {head:,} 頁"}]})
        meta["frames"].append({"file": f1, "status": st, "page": head})

        # 2 往回一頁
        st, info = await visit(url(head - 1))
        f2 = await snap("2_prev", {"badge": f"GET index{head - 1}.html → {st}",
                                   "boxes": [{"target": "prev", "kind": "red", "side": "below",
                                              "label": f"再按上頁 → index{prev_no(info)}.html，一頁一頁往回"}]})
        meta["frames"].append({"file": f2, "status": st, "page": head - 1, "prev": prev_no(info)})

        # 3 找第一頁「出現早於 7 天前的文章」：先用二分搜尋定位（每頁 20 篇、時間大致遞增）
        cache = {}

        async def probe(n):
            if n not in cache:
                await polite()
                r = await page.request.get(url(n))
                html = (await r.text()).split('r-list-sep')[0]
                aids = [(m.group(1), int(m.group(2))) for m in AID_RE.finditer(html)]
                deleted = html.count('class="r-ent"') - len(aids)   # 沒有連結的列＝刪文佔位
                cache[n] = (r.status, aids, deleted > 0)
            return cache[n]

        lo, hi = head - 420, head - 300   # 實測每天約 48 頁 → 7 天約 338 頁
        assert min(t for _, t in (await probe(lo))[1]) < since_ts, "lo 還不夠早"
        assert min(t for _, t in (await probe(hi))[1]) >= since_ts, "hi 已經早於 7 天"
        while hi - lo > 1:  # 不變：lo 頁有早於 7 天的文，hi 頁沒有
            mid = (lo + hi) // 2
            if min(t for _, t in (await probe(mid))[1]) < since_ts:
                lo = mid
            else:
                hi = mid
        stop = lo
        st, info = await visit(url(stop))
        rows = parse_rows(info)
        old = [r for r in rows if r["ts"] is not None and r["ts"] < since_ts]
        newest_old = max(old, key=lambda r: r["ts"])
        pages_walked = head - stop + 1
        f3 = await snap("3_stop", {
            "badge": f"GET index{stop}.html → {st}（往回第 {pages_walked} 頁）",
            "boxes": [{"target": "row", "aid": newest_old["aid"], "kind": "red", "side": "inside",
                       "label": f"發文 {fmt(newest_old['ts'])}，早於 7 天前（{fmt(since_ts)}）→ 停"}]})
        meta["frames"].append({"file": f3, "status": st, "page": stop, "pages_walked": pages_walked,
                               "older_rows": [(r["aid"], fmt(r["ts"])) for r in old],
                               "since": fmt(since_ts)})

        # 4 走完再讀一次最新頁
        st, info = await visit(url())
        head_end = prev_no(info) + 1
        same = head_end == head
        f4 = await snap("4_head_again", {
            "badge": f"再 GET 一次 index.html → {st}",
            "boxes": [{"target": "prev", "kind": "red", "side": "below",
                       "label": (f"上頁還是 → index{head - 1}.html：最新頁沒變" if same else
                                 f"上頁變成 → index{head_end - 1}.html：最新頁變了，這次不算數")}]})
        meta["frames"].append({"file": f4, "status": st, "head_end": head_end, "unchanged": same})

        # 5、6 文章 ID 與資料庫：挑一頁有刪文佔位、而且那頁的文已經入庫的。
        # 刪文佔位在列表上不多（實測約每 10～20 頁一列），可用參數直接指定頁碼，免得多讀很多頁：
        #   python capture_verify_walk.py 38660
        pages5 = [int(sys.argv[1])] if len(sys.argv) > 1 else range(head - 10, head - 70, -1)
        cand, ok = None, []
        for n in pages5:
            _, aids, has_deleted = await probe(n)
            if not (has_deleted and aids and all(t >= since_ts for _, t in aids)):
                continue
            st, info = await visit(url(n))
            rows = parse_rows(info)
            inwin = [r for r in rows if r["ts"] and r["ts"] >= since_ts]
            dbs = db_status([r["aid"] for r in inwin])
            ok = [r for r in inwin if dbs.get(r["aid"], {}).get("status") == 200]
            if ok:
                cand = n
                break
        assert cand, "附近沒有刪文佔位且已入庫的頁面"
        # 挑離刪文佔位最近的一篇（兩個框才能放進同一張截圖）
        di = next(i for i, r in enumerate(rows) if r["aid"] is None)
        idx = {r["aid"]: i for i, r in enumerate(rows)}
        pick = min(ok, key=lambda r: (abs(idx[r["aid"]] - di), idx[r["aid"]] > di))
        del_i = 0
        f5 = await snap("5_ids", {
            "badge": f"GET index{cand}.html → {st}",
            "boxes": [{"target": "row", "aid": pick["aid"], "kind": "red", "side": "inside",
                       "label": f"文章 ID {pick['aid']}：要在資料庫裡"},
                      {"target": "deleted", "i": del_i, "kind": "gray", "side": "inside",
                       "label": "刪文佔位，沒有文章 ID：不要求"}]})
        meta["frames"].append({"file": f5, "status": st, "page": cand, "aid": pick["aid"],
                               "in_window_on_page": len(inwin), "in_db_200": len(ok),
                               "db_missing": [r["aid"] for r in inwin if r["aid"] not in dbs]})

        # 6 資料庫那一筆
        rec = dbs[pick["aid"]]

        def tw(v):  # 時間欄換成台灣時間顯示
            try:
                return datetime.fromisoformat(str(v)).astimezone(TW).strftime("%Y-%m-%d %H:%M:%S（台灣時間）")
            except ValueError:
                return v
        rows_html = "".join(
            f"<tr><th>{k}</th><td><span{' id=st' if k == 'status' else ''}>{tw(v) if k.endswith('_at') else v}</span></td></tr>"
            for k, v in rec.items())
        sql = (f"SELECT aid, status, title, published_at, last_fetched_at\nFROM posts\n"
               f"WHERE board = '{BOARD}' AND aid = '{pick['aid']}'")
        await page.set_content(f"""<!doctype html><meta charset=utf-8><style>
          body{{margin:0;background:#141c20;color:#e2e9ec;font:15px/1.5 system-ui,sans-serif;padding:22px}}
          h3{{margin:0 0 10px;font-size:17px;color:#3cc2b2}} pre{{background:#0d1316;border:1px solid #2a3840;
          border-radius:8px;padding:10px 12px;font:14px/1.5 ui-monospace,Consolas,monospace;margin:0 0 14px;color:#c3ced4}}
          table{{border-collapse:collapse;font-size:14px}} th,td{{border:1px solid #2a3840;padding:6px 10px;text-align:left;
          white-space:nowrap}} th{{background:#1b2429;color:#94a3ac;font-weight:600}}</style>
          <h3>BigQuery（本機模擬器）posts 表，查詢時間 {datetime.now(TW).strftime('%Y-%m-%d %H:%M')}</h3>
          <pre>{sql}</pre><table>{rows_html}</table>""")
        f6 = await snap("6_db", {"top": True, "boxes": [{"target": "sel", "sel": "#st", "kind": "red", "side": "right",
                                            "label": "status 200：抓取成功"}]})
        meta["frames"].append({"file": f6, "record": rec})
        await browser.close()

    meta["requests_note"] = f"二分搜尋讀了 {len(cache)} 頁（只取 HTML，不渲染）"
    (OUT / "verify_walk.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=1, default=str))


asyncio.run(main())
