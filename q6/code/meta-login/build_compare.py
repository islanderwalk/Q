# Builds the logged-out vs logged-in comparison (JSON + HTML) and the per-platform evidence-chain diagram,
# then renders both to PNG with Playwright. Reads only local outputs; values of cookies are already masked.
import json, html, pathlib, collections
from playwright.sync_api import sync_playwright

import os  # noqa: E402
D = pathlib.Path(os.environ.get("META_WORKDIR") or pathlib.Path(__file__).parent)  # outputs + profile live here
EV = D / "evidence"
ANON = {"facebook": D / "anon/out/q6/channels/facebook.jsonl",
        "instagram": D / "anon/out/q6/channels/instagram.jsonl",
        "threads": D / "anon/q6/stack/data/channels/threads.jsonl"}
LOGGED = {k: D / f"loggedin/{k}.jsonl" for k in ANON}
LOGGED["instagram"] = D / "loggedin/instagram_clean.jsonl"  # feed/suggested posts from other authors removed, see evidence/instagram_contamination.json
FIELDS = ["post_id", "url", "published_at", "text", "author", "like_count", "comment_count", "play_count", "sub_type"]


def load(p):
    if not p.exists():
        return []
    rows = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            rows[r.get("post_id") or r.get("text", "")[:80]] = r
        except Exception:
            pass
    return list(rows.values())


def stats(rows):
    dates = sorted(r["published_at"] for r in rows if r.get("published_at"))
    fill = {f: sum(1 for r in rows if r.get(f) not in (None, "")) for f in FIELDS}
    per_src = collections.Counter(r.get("source") for r in rows)
    return {"posts": len(rows), "earliest": dates[0] if dates else None, "latest": dates[-1] if dates else None,
            "field_fill": fill, "per_source": dict(per_src)}


def main():
    summ = json.loads((EV / "loggedin_summary.json").read_text(encoding="utf-8")) if (EV / "loggedin_summary.json").exists() else []
    ledger = json.loads((EV / "api_ledger_masked.json").read_text(encoding="utf-8")) if (EV / "api_ledger_masked.json").exists() else []
    sess = json.loads((EV / "session_masked.json").read_text(encoding="utf-8")) if (EV / "session_masked.json").exists() else {"platforms": {}}
    cmp = {}
    for plat in ANON:
        a, l = load(ANON[plat]), load(LOGGED[plat])
        at = {r["text"][:60] for r in a}
        lt = {r["text"][:60] for r in l}
        cmp[plat] = {"logged_out": stats(a), "logged_in": stats(l),
                     "overlap_by_text": len(at & lt), "only_logged_out": len(at - lt), "only_logged_in": len(lt - at),
                     "loggedin_pages": [s for s in summ if s["platform"] == plat]}
    (EV / "compare_loggedout_vs_loggedin.json").write_text(json.dumps(cmp, ensure_ascii=False, indent=1), encoding="utf-8")

    css = """body{font-family:'Microsoft JhengHei','Noto Sans TC',sans-serif;margin:24px;background:#fff;color:#1b1f24}
    h1{font-size:22px}h2{font-size:18px;margin-top:28px}table{border-collapse:collapse;font-size:13px;margin:8px 0}
    td,th{border:1px solid #c9d1d9;padding:4px 8px;text-align:left;vertical-align:top}th{background:#f2f4f7}
    .chain{display:flex;gap:10px;align-items:stretch;margin:10px 0 24px}.box{border:2px solid #4b6bfb;border-radius:8px;padding:8px 10px;
    font-size:12px;min-width:200px;max-width:330px;background:#f7f9ff}.box b{display:block;font-size:13px;margin-bottom:4px}
    .arrow{align-self:center;font-size:22px;color:#4b6bfb}.mono{font-family:Consolas,monospace;font-size:11px;word-break:break-all}
    .note{color:#6a737d;font-size:12px}"""
    # comparison page
    h = [f"<html><head><meta charset=utf-8><style>{css}</style></head><body><h1>免登入（沿用原爬蟲）vs 登入帳號比對</h1>",
         "<p class=note>同一批目標頁。cookie/token 值一律遮罩為 sha256 前 10 碼＋長度。</p>"]
    for plat, c in cmp.items():
        lo, li = c["logged_out"], c["logged_in"]
        h.append(f"<h2>{plat}</h2><table><tr><th>指標</th><th>免登入</th><th>登入</th></tr>")
        for k in ("posts", "earliest", "latest"):
            h.append(f"<tr><td>{k}</td><td>{lo[k]}</td><td>{li[k]}</td></tr>")
        for f in FIELDS:
            h.append(f"<tr><td>欄位 {f} 有值</td><td>{lo['field_fill'][f]}/{lo['posts']}</td><td>{li['field_fill'][f]}/{li['posts']}</td></tr>")
        h.append(f"<tr><td>文字重疊／只在免登入／只在登入</td><td colspan=2>{c['overlap_by_text']} / {c['only_logged_out']} / {c['only_logged_in']}</td></tr>")
        h.append(f"<tr><td>各來源篇數</td><td class=mono>{html.escape(json.dumps(lo['per_source'], ensure_ascii=False))}</td><td class=mono>{html.escape(json.dumps(li['per_source'], ensure_ascii=False))}</td></tr></table>")
    h.append("</body></html>")
    (EV / "compare.html").write_text("".join(h), encoding="utf-8")

    # evidence chain per platform
    for plat in ANON:
        ck = sess["platforms"].get(plat, {}).get("cookies", [])
        led = [e for e in ledger if e["platform"] == plat]
        ops = collections.Counter((e["endpoint"].split("/", 3)[-1], e.get("friendly_name"), e.get("doc_id"), e["status"]) for e in led)
        keys = collections.Counter(k for e in led for k in e.get("resp_key_paths", []))
        interesting = [k for k, _ in keys.most_common(400) if any(t in k.split(".")[-1] for t in
                       ("text", "taken_at", "creation_time", "code", "like_count", "comment_count", "play_count", "username", "post_id", "permalink", "reshare", "reaction"))][:40]
        li = cmp[plat]["logged_in"]
        cbox = "".join(f"<div class=mono>{html.escape(c['name'])} · {html.escape(c['domain'])} · httpOnly={c['httpOnly']} · exp {html.escape(str(c['expires']))[:10]} · {html.escape(c['value'])}</div>" for c in ck[:12])
        obox = "".join(f"<div class=mono>{html.escape(str(o[0]))} | {html.escape(str(o[1]))} | doc_id={o[2]} | {o[3]} ×{n}</div>" for o, n in ops.most_common(14))
        kbox = "".join(f"<div class=mono>{html.escape(k)}</div>" for k in interesting)
        fbox = "".join(f"<div class=mono>{f}: {li['field_fill'][f]}/{li['posts']}</div>" for f in FIELDS)
        pg = (f"<html><head><meta charset=utf-8><style>{css}</style></head><body><h1>{plat} 登入爬取證據鏈</h1>"
              f"<p class=note>擷取時間 {html.escape(sess.get('captured_at', ''))}；值已遮罩。對照 F12 截圖：evidence/{plat}_*_devtools_F12*.png</p>"
              f"<div class=chain><div class=box><b>① Session（瀏覽器 cookie，遮罩）</b>{cbox or '（無）'}</div><div class=arrow>→</div>"
              f"<div class=box><b>② 發出的 API 請求（endpoint | friendly_name | doc_id | status）</b>{obox or '（無）'}</div><div class=arrow>→</div>"
              f"<div class=box><b>③ 回應 JSON 欄位路徑（摘錄）</b>{kbox or '（無）'}</div><div class=arrow>→</div>"
              f"<div class=box><b>④ 落地 raw lake 欄位（有值篇數）</b>{fbox}<div class=mono>posts={li['posts']} earliest={li['earliest']} latest={li['latest']}</div></div></div>"
              "</body></html>")
        (EV / f"chain_{plat}.html").write_text(pg, encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1600, "height": 900})
        for name in ["compare"] + [f"chain_{k}" for k in ANON]:
            pg.goto((EV / f"{name}.html").as_uri())
            pg.screenshot(path=str(EV / f"{name}.png"), full_page=True)
        b.close()
    print(json.dumps({k: {"out": v["logged_out"]["posts"], "in": v["logged_in"]["posts"],
                          "out_earliest": v["logged_out"]["earliest"], "in_earliest": v["logged_in"]["earliest"]} for k, v in cmp.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
