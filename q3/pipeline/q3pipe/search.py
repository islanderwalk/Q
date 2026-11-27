"""查詢 API（答案：Cloud Run 上的查詢服務）。貼文和留言分開查，再依文章合併。

GET /                 搜尋頁
GET /api/search?q=關鍵字&days=7&limit=50
GET /healthz
GET /metrics          Prometheus 指標（請求數、延遲、BigQuery 掃描量）
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest

from .bq import Warehouse
from .config import Config

REG = CollectorRegistry()
REQS = Counter("q3_search_requests_total", "搜尋 API 請求數", ["code"], registry=REG)
LAT = Histogram("q3_search_latency_seconds", "搜尋 API 回應時間（含 BigQuery）", registry=REG,
                buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10))
SCANNED = Counter("q3_search_bytes_processed_total", "搜尋查詢的 BigQuery 掃描量", registry=REG)
HITS = Histogram("q3_search_result_posts", "每次搜尋命中的文章數", registry=REG, buckets=(0, 1, 5, 10, 20, 50, 100))


def _iso(v):
    return v.astimezone(timezone.utc).isoformat() if isinstance(v, datetime) else v


def do_search(wh, cfg, kw, days, limit, now=None):
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    b0 = wh.bytes_processed
    t0 = time.perf_counter()
    posts, more, comments = wh.search(cfg.board, kw, since, limit)
    took = time.perf_counter() - t0
    by_aid = {}
    for p in posts + more:
        p = {k: _iso(v) for k, v in p.items()}
        p["matched_in"] = [w for w, txt in (("title", p.get("title")), ("content", p.get("snippet"))) if txt and kw in txt]
        p["matched_comments"] = []
        by_aid[p["aid"]] = p
    for p in posts:  # 內文命中但 160 字摘要沒包含關鍵字
        if not by_aid[p["aid"]]["matched_in"]:
            by_aid[p["aid"]]["matched_in"] = ["content"]
    for c in comments:
        p = by_aid.get(c["aid"])
        if p is None:
            continue
        if "comments" not in p["matched_in"]:
            p["matched_in"].append("comments")
        if len(p["matched_comments"]) < 5:
            p["matched_comments"].append({k: _iso(v) for k, v in c.items() if k != "aid"})
        p["matched_comment_count"] = p.get("matched_comment_count", 0) + 1
    results = sorted(by_aid.values(), key=lambda p: p["published_at"], reverse=True)[:limit]
    scanned = wh.bytes_processed - b0
    return {"query": kw, "since": since.isoformat(), "days": days, "took_ms": round(took * 1000),
            "bytes_processed": scanned, "post_hits": len(posts), "comment_hits": len(comments),
            "results": results}


PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>PTT 八卦板搜尋</title>
<style>
:root{--bg:#f7f7f5;--fg:#1d1d1f;--mute:#6b6b70;--card:#fff;--line:#e3e3e0;--acc:#1f5fbf;--mark:#ffe58a}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececee;--mute:#9a9aa2;--card:#1d1d21;--line:#2c2c31;--acc:#7fb0ff;--mark:#6b5300}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,-apple-system,"Noto Sans TC","PingFang TC",sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mute);font-size:13px;margin:0 0 16px}
form{display:flex;gap:8px;flex-wrap:wrap}input[type=search]{flex:1 1 240px;padding:10px 12px;font-size:16px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg)}
select,button{padding:10px 12px;font-size:15px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg)}button{background:var(--acc);color:#fff;border-color:var(--acc);cursor:pointer}
#meta{color:var(--mute);font-size:13px;margin:14px 0 8px;min-height:20px}.post{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
.post a{color:var(--acc);text-decoration:none;font-weight:600}.info{color:var(--mute);font-size:12.5px;margin:2px 0 6px}.snip{white-space:pre-wrap;word-break:break-word;margin:0;font-size:14px}
.tags span{display:inline-block;font-size:11.5px;border:1px solid var(--line);border-radius:99px;padding:0 8px;margin-right:4px;color:var(--mute)}
ul{margin:8px 0 0;padding-left:18px}li{font-size:13.5px;margin:2px 0}mark{background:var(--mark);color:inherit;border-radius:2px}
</style></head><body><main>
<h1>PTT 八卦板全文搜尋</h1><p class="sub">過去 7 天內的貼文與留言，資料每小時更新；貼文與留言分開查，再依文章合併。</p>
<form id="f"><input type="search" id="q" placeholder="輸入關鍵字" maxlength="50" required autofocus>
<select id="d"><option value="1">1 天</option><option value="3">3 天</option><option value="7" selected>7 天</option></select>
<button>搜尋</button></form><div id="meta"></div><div id="out"></div></main>
<script>
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const hl=(s,k)=>String(s??"").split(k).map(esc).join("<mark>"+esc(k)+"</mark>");
const tw=t=>new Date(t).toLocaleString("zh-TW",{timeZone:"Asia/Taipei",hour12:false});
const W={title:"標題",content:"內文",comments:"留言"};
async function go(q,d){const m=document.getElementById("meta"),o=document.getElementById("out");m.textContent="查詢中…";o.innerHTML="";
 const r=await fetch(`/api/search?q=${encodeURIComponent(q)}&days=${d}`);const j=await r.json();
 if(!r.ok){m.textContent=j.error||"查詢失敗";return}
 m.textContent=`${j.results.length} 篇文章（貼文命中 ${j.post_hits}、留言命中 ${j.comment_hits}）· ${j.took_ms} ms · 掃描 ${j.bytes_processed >= 1e6 ? (j.bytes_processed/1e6).toFixed(1) + ' MB' : (j.bytes_processed/1e3).toFixed(1) + ' kB'}`;
 o.innerHTML=j.results.map(p=>`<div class="post"><a href="${esc(p.url)}" target="_blank" rel="noopener">${hl(p.title,q)}</a>
 <div class="info">${esc(p.author)} · ${tw(p.published_at)} · 推 ${p.push} 噓 ${p.boo} → ${p.arrow} · 留言 ${p.comment_count}</div>
 <div class="tags">${p.matched_in.map(w=>`<span>${W[w]}</span>`).join("")}</div>
 ${p.snippet?`<p class="snip">${hl(p.snippet,q)}</p>`:""}
 ${p.matched_comments.length?`<ul>${p.matched_comments.map(c=>`<li>${esc(c.tag)} <b>${esc(c.user_id)}</b>：${hl(c.content,q)}</li>`).join("")}</ul>`:""}</div>`).join("");
 history.replaceState(null,"",`?q=${encodeURIComponent(q)}&days=${d}`)}
document.getElementById("f").onsubmit=e=>{e.preventDefault();go(document.getElementById("q").value.trim(),document.getElementById("d").value)};
const u=new URLSearchParams(location.search);if(u.get("q")){document.getElementById("q").value=u.get("q");if(u.get("days"))document.getElementById("d").value=u.get("days");go(u.get("q"),u.get("days")||7)}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    cfg: Config = None
    local = threading.local()

    def wh(self):
        if not hasattr(self.local, "wh"):
            self.local.wh = Warehouse(self.cfg)
        return self.local.wh

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if u.path == "/healthz":
            return self._send(200, '{"ok":true}')
        if u.path == "/metrics":
            return self._send(200, generate_latest(REG), CONTENT_TYPE_LATEST)
        if u.path != "/api/search":
            return self._send(404, '{"error":"not found"}')
        qs = parse_qs(u.query)
        kw = (qs.get("q", [""])[0]).strip()
        try:
            days = min(max(int(qs.get("days", [str(self.cfg.window_days)])[0]), 1), self.cfg.window_days)
            limit = min(max(int(qs.get("limit", [str(self.cfg.search_limit)])[0]), 1), 100)
        except ValueError:
            REQS.labels("400").inc()
            return self._send(400, json.dumps({"error": "days、limit 要是整數"}, ensure_ascii=False))
        if not kw or len(kw) > 50:
            REQS.labels("400").inc()
            return self._send(400, json.dumps({"error": "關鍵字長度要在 1–50 字"}, ensure_ascii=False))
        t0 = time.perf_counter()
        try:
            res = do_search(self.wh(), self.cfg, kw, days, limit)
        except Exception as e:
            REQS.labels("500").inc()
            LAT.observe(time.perf_counter() - t0)
            return self._send(500, json.dumps({"error": type(e).__name__}, ensure_ascii=False))
        LAT.observe(time.perf_counter() - t0)
        REQS.labels("200").inc()
        SCANNED.inc(res["bytes_processed"])
        HITS.observe(len(res["results"]))
        self._send(200, json.dumps(res, ensure_ascii=False, default=str))

    def log_message(self, fmt, *args):
        print(json.dumps({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": "http",
                          "line": fmt % args}, ensure_ascii=False), flush=True)


def serve(cfg=None, host="0.0.0.0"):
    cfg = cfg or Config.from_env()
    Handler.cfg = cfg
    srv = ThreadingHTTPServer((host, cfg.search_port), Handler)
    print(json.dumps({"event": "search_listening", "port": cfg.search_port}), flush=True)
    srv.serve_forever()
