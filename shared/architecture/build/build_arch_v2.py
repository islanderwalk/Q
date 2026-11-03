# Add four tabs (Q2 估算, Q5 資料流, GCP／地端, 佐證總覽) and per-node deploy rows to the
# QSearch master architecture page, keeping the original look: same tokens, same nav, same panel.
#
#   python build_arch_v2.py <base.html> <out.html> --repo <repo root> --mode artifact|repo
#                           [--cards cards.json] [--files files.json]
#
# base.html: the page before these additions (the original architecture page HTML, saved as base.html).
# --mode artifact: images are served from the artifact as evidence/<flat name>; --files writes the
#                  publish map {published path: local path}. --mode repo: images use paths relative
#                  to shared/architecture/.
import argparse, base64, glob, html, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arch_data import (TF_PLAN, STATUS, DEPLOY, DEPLOY_ROWS, DEPLOY_TRADEOFFS, DEPLOY_SWITCH, DEPLOY_GAPS,
                       NODE_NOTE, NODE_Q_ADD, Q3_BQ_NOTE, Q5_NODES, Q5_TASKS, Q5_SLA, Q5_CAP)

ap = argparse.ArgumentParser()
ap.add_argument("src"); ap.add_argument("out")
ap.add_argument("--repo", required=True)
ap.add_argument("--mode", choices=["artifact", "repo"], required=True)
ap.add_argument("--cards"); ap.add_argument("--files"); ap.add_argument("--status")
ap.add_argument("--single", action="store_true", help="keep only the 整合 view (drop PoC/A/B/C)")
ap.add_argument("--icons", action="store_true", help="put official Google Cloud icons on the 整合 diagram nodes")
ap.add_argument("--q-tabs", action="store_true", help="one tab per question Q1–Q6 (Q2 估算 and Q5 資料流 move into Q2, Q5)")
ap.add_argument("--drop-tabs", action="store_true", help="drop the 請求時序, Uvicorn 背景, 推演筆記 and GCP／地端 tabs (2026-10-31)")
ap.add_argument("--obs", action="store_true", help="add the 7 觀測 band (request logs, crawler/collector and service metrics, ES health, completeness reconciliation, quality daily report, Grafana dashboards + Alertmanager → PM page) under the 整合 diagram (2026-11-02)")
a = ap.parse_args()
REPO = a.repo
E = lambda s: html.escape(str(s), quote=True)

s = open(a.src, encoding="utf8").read()
if 'id="pane-deploy"' in s:
    sys.exit("base page already has the added tabs; build from the pre-v2 page "
             "(the original architecture page HTML, saved as base.html)")
start = s.index('<!doctype html>\n<html lang="zh-Hant">')
page = s[start:]
end = page.index("</html>") + len("</html>")
page = page[:end] + "\n"

def raw(name):
    k = page.index(f"const {name} = ") + len(f"const {name} = ")
    obj, n = json.JSONDecoder().raw_decode(page[k:])
    return obj, k, k + n
D, _, _ = raw("D")
PAGES, _, _ = raw("PAGES")

# ---------------------------------------------------------------- images
FILES = {}
def flat(p):
    q = p.split("/")[0]
    return f"{q}_{os.path.basename(p)}" if re.fullmatch(r"q\d", q) else os.path.basename(p)
def img(p, thumb=False):
    full = os.path.join(REPO, p)
    if not os.path.exists(full):
        raise SystemExit(f"missing image {p}")
    if a.mode == "repo":
        return "../../" + p
    pub = "evidence/" + flat(p)
    FILES[pub] = full
    if thumb:
        t = os.path.join(THUMBS, flat(p).rsplit(".", 1)[0] + ".webp")
        if not os.path.exists(t):
            from PIL import Image
            im = Image.open(full).convert("RGB")
            w = 640
            if im.width > w:
                im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
            im = im.crop((0, 0, im.width, min(im.height, round(im.width * 0.75))))
            im.save(t, "WEBP", quality=78)
        tp = "evidence/t/" + os.path.basename(t)
        FILES[tp] = t
        return tp
    return pub
THUMBS = os.path.join(os.path.dirname(os.path.abspath(a.out)), "thumbs")
os.makedirs(THUMBS, exist_ok=True)

# ---------------------------------------------------------------- evidence registry
manifest = {m["image"]: m for m in json.load(open(os.path.join(REPO, "shared/capture/capture_manifest.json"), encoding="utf8"))}
caps = {}
for rd in sorted(glob.glob(os.path.join(REPO, "q*/README.md"))):
    base = os.path.dirname(rd)
    for line in open(rd, encoding="utf8"):
        if not line.startswith("|") or ".png" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        for l in re.findall(r"\]\(([^)]+\.png)\)", line):
            p = os.path.relpath(os.path.normpath(os.path.join(base, l)), REPO)
            c = cells[-1]
            if len(c) < 12 and len(cells) > 2:
                c = cells[1]
            caps.setdefault(p, re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", re.sub(r"`([^`]*)`", r"\1", c)))
CAP_FIX = {  # README captions that newer evidence corrected (Playwright round 2 audit: shared/docs/playwright-round2.md)
    "q2/evidence/playwright/fb_post_nasa.png": "FB 單則貼文未登入：粉專名稱可見；「79」與「4h」是第一則留言的數字，不是貼文本身（留言已遮）",
    "q2/evidence/playwright/fb_page_nasa.png": "FB 粉專頁未登入：只讀得到粉專名稱，心情數與發文時間被登入視窗蓋住",
    "q5/evidence/playwright/threads_post_repost.png": "Threads 貼文頁：畫面「轉發 16」是 repost_count＋quote_count，不是單獨的 repost_count（回覆區已遮）",
    "q3/evidence/playwright/ptt_article_comments.png": "留言欄位：261 則，推 150、噓 10、→ 101",
    "q4/evidence/playwright/threads_profile_nasa.png": "@nasa 個人頁：登入窗蓋住，畫面只看得到 1 篇（5 篇是 HTML 內嵌 JSON 的計數）；HTML 只內嵌 4 串，湊滿 10 篇要靠分頁 query",
    "q4/evidence/playwright/threads_graphql_desktop.png": "桌面未登入，載入後再捲 1 次：網頁自己送分頁 query，HTTP 200、11 串、401 KB；doc_id 和 10/29 不同。10/30 Linux 容器重測桌面 15 次載入都沒送，不是每次開頁都有",
    "q4/evidence/playwright/threads_mobile_wall.png": "手機視窗未登入：出現「Log in to see more from nasa.」登入提示，但捲動期間帶貼文的 GraphQL 回應仍有 2 個。10/30 Linux 容器重測手機 3 個帳號有 1 個載入下一頁，要監控每次回傳的串數",
    "q4/evidence/playwright/threads_profile_zuck.png": "@zuck 個人頁：畫面被登入視窗蓋住只露出 1 篇；HTML 內嵌 4 串、10 篇",
    "q6/evidence/playwright/ptt_noise_02.png": "PTT 高雄板：地名「鼓山」雜訊（作者 ID、IP 已遮）",
    "q6/evidence/playwright/ptt_noise_04.png": "PTT 高雄板：電話號碼雜訊（號碼已遮）",
    "q5/evidence/playwright/ig_reels_grid.png": "未登入的 Reels 分頁縮圖就印著觀看數（4.2M、2.9M、2.3M、3.9M），爬蟲端拿得到；欄位名是 play_count",
    "q4/architecture/diagram-request-layers.png": "單一 GraphQL 請求的分層解剖：封鎖判定在 L3 的來源 IP",
    "q4/architecture/diagram-system.png": "Threads 爬蟲系統：排程 → Worker → 住宅代理 → GraphQL → 解析",
    "q6/evidence/a-backfill/evidence-es-global-ordinals.png": "ES global ordinals：3 分鐘查詢目標的手段與限制",
    "q6/evidence/a-backfill/evidence-es-random-sampler.png": "ES random sampler：3 分鐘查詢目標的手段與限制",
}
def platform(p):
    b = os.path.basename(p)
    if "/architecture/" in p or b.startswith("diagram"): return "diagram"
    if b.startswith("chart-"): return "chart"
    if b.startswith("fb_"): return "facebook"
    if b.startswith("ig_"): return "instagram"
    if "threads" in b: return "threads"
    if "ptt" in b: return "ptt"
    return "doc"
def kind(p):
    b = os.path.basename(p)
    if "/evidence/playwright/" in p: return "Playwright 實抓"
    if b == "threads-app-repost-options-menu.png": return "手機 App 截圖"
    if p.startswith("q4/evidence/") or p.startswith("q5/evidence/a-dataflow/"): return "Playwright 實抓"
    if b.startswith("ptt-"): return "Playwright 實抓"
    if platform(p) == "diagram": return "架構圖"
    if platform(p) == "chart": return "實測圖表"
    if "evidence-vm-" in b: return "VM 實測"
    return "文件截圖"
for m in manifest.values():  # Playwright captures no README table lists yet (round 2)
    p = m["image"]
    if m.get("status") == "real" and p.endswith((".png", ".jpg", ".webp")) and p not in caps and os.path.exists(os.path.join(REPO, p)):
        caps[p] = m.get("title", "") + ("：" + m["result"] if m.get("result") and len(m.get("title", "")) < 40 else "")
EVID = []
for p in sorted(caps):
    if "horizontal-draft" in p:
        continue
    m = manifest.get(p, {})
    EVID.append({"path": p, "q": p[1], "pf": platform(p), "kind": kind(p),
                 "title": m.get("title", ""), "cap": CAP_FIX.get(p, caps[p]),
                 "result": m.get("result", ""), "at": (m.get("captured_at") or "")[:16].replace("T", " ")})
PF_LABEL = {"facebook": "Facebook", "ptt": "PTT", "threads": "Threads", "instagram": "Instagram Reels",
            "diagram": "架構圖", "chart": "實測圖表", "doc": "文件與實驗"}

# ---------------------------------------------------------------- question cards
if a.cards:
    cards = json.load(open(a.cards, encoding="utf8"))
    for q, c in cards.items():
        Q = D["questions"].setdefault(q, {})
        for k in ("title", "ask", "factsTitle", "facts", "path", "say", "notes", "verify", "todo", "src"):
            if k in c:
                if c[k] in ("", [], None):
                    Q.pop(k, None)
                else:
                    Q[k] = c[k]
        if "evidence" in c:
            Q["evidence"] = [{"img": img(e["path"]), "cap": e["cap"]} for e in c["evidence"]]
        if "figures" in c:  # full-width images on the question tab (Q6 監控看板, 2026-11-02)
            Q["figures"] = [{"img": img(e["path"]), "cap": e["cap"]} for e in c["figures"]]
        have = {e["path"] for e in EVID}
        for e in c.get("evidence_all", []):  # images a card cites that no README table lists
            p = e["path"]
            if p in have or not re.fullmatch(r"q\d/.*", p) or "horizontal-draft" in p:
                continue
            m = manifest.get(p, {})
            pf = e.get("platform") if e.get("platform") in PF_LABEL else platform(p)
            EVID.append({"path": p, "q": p[1], "pf": pf, "kind": kind(p), "title": m.get("title", ""), "cap": e["cap"],
                         "result": m.get("result", ""), "at": (m.get("captured_at") or "")[:16].replace("T", " ")})
            have.add(p)
else:  # keep current evidence but point at repo files
    OLD = {"q2_fb_post_nasa.png": "q2/evidence/playwright/fb_post_nasa.png", "q2_fb_page_nasa.png": "q2/evidence/playwright/fb_page_nasa.png",
           "q3_ptt_index_latest.png": "q3/evidence/playwright/ptt_index_latest.png", "q3_ptt_article_comments.png": "q3/evidence/playwright/ptt_article_comments.png",
           "q3_chart-invariant-results.png": "q3/answer/4-invariant/chart-invariant-results.png", "q3_architecture.png": "q3/architecture/architecture.png",
           "q4_threads_mobile_wall.png": "q4/evidence/playwright/threads_mobile_wall.png", "q4_threads_graphql_desktop.png": "q4/evidence/playwright/threads_graphql_desktop.png",
           "q4_diagram-system.png": "q4/architecture/diagram-system.png", "q5_diagram-dataflow-vertical-final.png": "q5/architecture/diagram-dataflow-vertical-final.png",
           "q5_ig_reels_grid_json_check.png": "q5/evidence/playwright/ig_reels_grid_json_check.png",
           "q5_threads-repost-equals-repost-plus-quote.png": "q5/evidence/a-dataflow/threads-repost-equals-repost-plus-quote.png",
           "q6_ptt_noise_01.png": "q6/evidence/playwright/ptt_noise_01.png", "q6_ptt_noise_02.png": "q6/evidence/playwright/ptt_noise_02.png"}
    for Q in D["questions"].values():
        for e in Q.get("evidence", []):
            b = os.path.basename(e["img"])
            e["img"] = img(OLD.get(b) or next(v for v in OLD.values() if v.endswith("/" + b)))

# links from a question panel to the new tabs
LINKS = {"2": [("q2", None, "Q2 估算解剖頁")], "5": [("q5flow", None, "Q5 資料流")]}
QTAB = {"1": "q1", "2": "q2", "3": "q3", "4": "q4", "5": "q5flow", "6": "q6"}
QLABEL = {"1": "Q1 取捨", "2": "Q2 估算", "3": "Q3 PTT", "4": "Q4 Threads", "5": "Q5 新指標", "6": "Q6 斷詞"}
if a.q_tabs:
    LINKS = {q: [(t, None, f"{QLABEL[q]} 分頁")] for q, t in QTAB.items()}
for q, Q in D["questions"].items():
    Q["links"] = [{"tab": t, "label": l} for t, _, l in LINKS.get(q, [])] + [{"tab": "evidence", "q": q, "label": f"Q{q} 全部佐證"}]
if not a.q_tabs:
    D["questions"]["1"]["links"] = [{"tab": "evidence", "q": "1", "label": "佐證總覽"}]

# deploy rows on the integrated variant
for nid, n in D["variants"]["int"]["nodes"].items():
    if nid in DEPLOY:
        d = DEPLOY[nid]
        tone, lab = STATUS[d["st"]]
        n["dep"] = {"gcp": d["gcp"], "tone": tone, "st": lab, "onprem": d["onprem"], "diff": d["diff"]}

V = D["variants"]["int"]
for nid, t in NODE_NOTE.items():
    V["nodes"][nid]["note"] = t
for nid, qs in NODE_Q_ADD.items():
    V["nodes"][nid]["q"] = sorted(set(V["nodes"][nid].get("q", [])) | set(qs))
# Q3's answer stores PTT in BigQuery (not pg_bigm); pg_bigm is Q2's FB post search.
# The diagram must match the Q3 answer, so the two stage-5 nodes are relabelled.
V["nodes"]["searchdb"].update(sub="Cloud SQL · Q2 FB 貼文", q=["2"])
V["nodes"]["searchapi"].update(label="BigQuery", sub="Q3 PTT 7 天 · 查詢 API", q=["3"], note=Q3_BQ_NOTE)
assert "下游接上 Q2、Q3 的 pg_bigm 搜尋庫" in V["summary"]
V["summary"] = V["summary"].replace("下游接上 Q2、Q3 的 pg_bigm 搜尋庫", "下游接上 Q2 的 pg_bigm 搜尋庫、Q3 的 BigQuery")
assert V["spine"][5] == "pg_bigm＋ES 鏈"
V["spine"][5] = "pg_bigm／BigQuery／ES"
# the base page already says what the question bar does; keep the longer sentence only
if "上方「依題目」可以點亮 Q1–Q6" in V["summary"]:
    V["summary"] = V["summary"].replace("上方「依題目」可以只亮某一題用到的元件。", "")
if "GCP／地端" not in V["summary"] and "佐證總覽" not in V["summary"]:
    V["summary"] += ("點節點可以看 Terraform plan 狀態與地端對照；上方分頁另有 Q2 估算、Q5 資料流與佐證總覽。" if a.drop_tabs else
                     "點節點可以看 Terraform plan 狀態與地端對照；上方分頁另有 Q2 估算、Q5 資料流、GCP／地端與佐證總覽。")

for Q in D["questions"].values():  # talk-track lines are not shown on the page
    Q.pop("say", None)
D["mnemonic"] = ""

# ---------------------------------------------------------------- Q2 page as a sub-page
PAGES["q2"] = base64.b64encode(open(os.path.join(REPO, "q2/answer/fb_q2_posts_table.html"), "rb").read()).decode()

# ---------------------------------------------------------------- static panes
def chip(tone, text):
    return f'<span class="st" data-tone="{tone}">{E(text)}</span>'
def qchips(qs):
    return "".join(f'<span class="qt">Q{E(q)}</span>' for q in qs)
STAGE = [(s["ch"], s["name"]) for s in D["stages"]]

STATUS_ROWS = json.load(open(a.status, encoding="utf8")) if a.status else []

# --- GCP／地端
rows = []
for i, (ch, nm) in enumerate(STAGE):
    items = [r for r in DEPLOY_ROWS if r[0] == i]
    if not items: continue
    body = []
    for _, comp, tf, st, onp, diff, qs, node in items:
        tone, lab = STATUS[st]
        go = f'<button type="button" class="lnk" data-goto-node="{node}">在圖上看</button>' if node else ""
        body.append(
            f'<div class="dr"><div class="dc"><b>{E(comp)}</b><span class="qs">{qchips(qs)}</span></div>'
            f'<div class="dc"><span class="k">GCP</span><code>{E(tf)}</code>{chip(tone, lab)}</div>'
            f'<div class="dc"><span class="k">地端</span>{E(onp)}</div>'
            f'<div class="dc"><span class="k">差異</span>{E(diff)}{go}</div></div>')
    rows.append(f'<section class="dgrp"><h3><b>{E(ch)}</b>{i + 1} {E(nm)}</h3>{"".join(body)}</section>')
n_planned = sum(1 for r in DEPLOY_ROWS if r[3] == "planned")
deploy_html = f'''<div class="doc-in">
  <div class="doc-h"><h2>部署對照：GCP 與地端</h2>
  <p>同一條六段管線，左邊是 Terraform 資源與驗證狀態，右邊是地端的對應元件與取捨。綠色＝第一輪對正式 GCP 專案 plan 的 70 項內，第二輪改過其中幾項的設定（例如 Cloud SQL 旗標與備份、Cloud Run 改成 internal ingress），這些改動只跑過離線 plan；藍色＝只跑過離線 plan（主模組第二輪新增的 32 項另跑過 terraform test；GKE 是另一個 root，只有 validate 與替代 plan）；橘色＝還沒有 Terraform 或部分涵蓋；灰色＝外部服務。</p></div>
  <div class="kpis">
    <div class="kpi"><span class="k">terraform plan（正式專案，第一輪）</span><span class="v">70 to add</span><span class="s">0 change、0 destroy；未 apply、未啟用 API</span></div>
    <div class="kpi"><span class="k">離線 plan（第二輪）</span><span class="v">102 to add</span><span class="s">新增 32 項；terraform test 30 passed；尚未對正式專案 plan</span></div>
    <div class="kpi"><span class="k">GKE（方案 B／C）</span><span class="v">validate＋替代 plan</span><span class="s">獨立 root；data source 換成字面值後 plan 8 to add</span></div>
    <div class="kpi"><span class="k">地端 docker compose</span><span class="v">smoke 19／19</span><span class="s">9 個服務實際啟動，連續三次；6,724 篇 PTT、約 1.6 GiB 記憶體</span></div>
    <div class="kpi"><span class="k">pg_bigm 1.2（地端，201,720 列標題）</span><span class="v">0.43 ms</span><span class="s">LIKE「蔡康永」走索引；強制全表掃描 38.3 ms</span></div>
    <div class="kpi"><span class="k">GCP 閒置月費</span><span class="v">US$104</span><span class="s">約 NT$3,382，Q2 預算 3,000 元的 1.13 倍，以第一輪 70 項計；地端估 NT$1,515–3,914，不含人力</span></div>
  </div>
  <div class="legend-row">{chip("direct", "已 plan")}{chip("build", "第二輪離線 plan")}{chip("inferred", "未寫 Terraform／部分涵蓋")}{chip("weak", "外部服務")}</div>
  {"".join(rows)}
  <section class="dgrp"><h3>取捨重點</h3><dl class="tl">{"".join(f"<dt>{E(k)}</dt><dd>{E(v)}</dd>" for k, v in DEPLOY_TRADEOFFS)}</dl></section>
  <section class="dgrp"><h3>什麼時候切到地端</h3><div class="sw">{"".join(f'<div><span>{E(k)}</span><b>{E(v)}</b></div>' for k, v in DEPLOY_SWITCH)}</div></section>
  <section class="dgrp"><h3>還沒涵蓋</h3><ul class="plain">{"".join(f"<li>{E(x)}</li>" for x in DEPLOY_GAPS)}</ul></section>
  <p class="src">來源：shared/infra/gcp/README.md（含「第二輪驗證」）、shared/docs/gcp.md、shared/architecture/gcp-vs-onprem.md、shared/infra/onprem/README.md、shared/infra/onprem/VERIFY.md。</p>
</div>'''

# --- Q5 data flow (same visual grammar as the 整合 diagram)
W, H = 150, 54
POS = {"ig": (20, 60), "threads": (20, 170), "sched": (200, 50), "crawler": (200, 150), "gcs": (370, 230),
       "writer": (570, 110), "backfill": (570, 230), "es": (760, 170), "monitor": (930, 60),
       "datalayer": (20, 360), "analytics": (200, 360), "export": (380, 430), "frontend": (570, 360), "customer": (760, 360)}
def node(nid):
    x, y = POS[nid]; n = Q5_NODES[nid]
    return (f'<g class="n {n["cls"]}" data-q5="{nid}" tabindex="0" role="button" aria-pressed="false" aria-label="{E(n["label"])}：{E(n["sub"])}">'
            f'<rect x="{x}" y="{y}" width="{W}" height="{H}" rx="8"/>'
            f'<text class="l" x="{x + W / 2}" y="{y + 19}" text-anchor="middle" dominant-baseline="central">{E(n["label"])}</text>'
            f'<text class="s" x="{x + W / 2}" y="{y + 37}" text-anchor="middle" dominant-baseline="central">{E(n["sub"])}</text></g>')
def edge(d, a_, b_, label=None, lx=0, ly=0, dashed=False):
    t = f'<text x="{lx}" y="{ly}" dominant-baseline="central">{E(label)}</text>' if label else ""
    return f'<g class="e{" c" if dashed else ""}" data-from="{a_}" data-to="{b_}"><path d="{d}" marker-end="url(#q5ah)"/>{t}</g>'
bands = [
    (10, 12, 170, 286, "", "來源"), (190, 12, 350, 286, "", "既有系統 · 不改"), (550, 12, 530, 286, "de", "資料工程師"),
    (10, 316, 530, 186, "be", "後端"), (550, 316, 530, 186, "fe", "前端 · 客戶")]
svg = ['<svg class="dg-svg" viewBox="0 0 1090 548" role="group" aria-label="Q5 新指標資料流：誰改什麼">',
       '<defs><marker id="q5ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z"/></marker></defs>']
for x, y, w, h, cls, lab in bands:
    svg.append(f'<rect class="band {cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="10"/><text class="bl {cls}" x="{x + 10}" y="{y + 20}">{E(lab)}</text>')
svg += [
    edge("M170 87 H185 V171 H198", "ig", "crawler"),
    edge("M170 197 H185 V183 H198", "threads", "crawler"),
    edge("M275 104 V148", "sched", "crawler", "定時觸發", 283, 126),
    edge("M275 204 V257 H368", "crawler", "gcs", "存原始資料", 283, 232),
    edge("M350 177 H460 V137 H568", "crawler", "writer", "新抓到的", 468, 162),
    edge("M520 257 H568", "gcs", "backfill", "讀舊資料", 524, 247, True),
    edge("M720 137 H835 V168", "writer", "es", "寫入兩欄", 735, 127),
    edge("M720 257 H738 V210 H758", "backfill", "es", "只補空的", 726, 274, True),
    edge("M910 170 V140 H1005 V116", "es", "monitor", "首次可見延遲", 918, 130, True),
    edge("M835 224 V300 H95 V358", "es", "datalayer", "查詢結果", 843, 263),
    edge("M170 387 H198", "datalayer", "analytics"),
    edge("M350 387 H568", "analytics", "frontend", "加總排序", 420, 377),
    edge("M275 414 V457 H378", "analytics", "export", "提供資料", 283, 440),
    edge("M530 457 H645 V416", "export", "frontend", "匯出檔", 560, 447),
    edge("M720 387 H758", "frontend", "customer"),
]
svg += [node(n) for n in POS]
lg = [("", "不改"), ("mod", "要修改"), ("new", "要新增"), ("ext", "外部")]
x = 20; leg = ['<g class="lg">']
for cls, lab in lg:
    leg.append(f'<g class="n sw {cls}"><rect x="{x}" y="520" width="16" height="12" rx="3"/></g><text x="{x + 22}" y="526" dominant-baseline="central">{E(lab)}</text>')
    x += 96
leg.append(f'<g class="e c"><path d="M{x} 526 H{x + 30}"/></g><text x="{x + 38}" y="526" dominant-baseline="central">回補／監控路徑</text></g>')
svg += leg + ["</svg>"]

EVQ5 = {"q5_ig_grid_json": "q5/evidence/playwright/ig_reels_grid_json_check.png", "q5_ig_reel_page": "q5/evidence/playwright/ig_reel_page.png",
        "q5_th_repost_plus_quote": "q5/evidence/a-dataflow/threads-repost-equals-repost-plus-quote.png",
        "q5_th_reshare_null": "q5/evidence/a-dataflow/threads-reshare-null-not-zero.png",
        "q5_th_wan": "q5/evidence/a-dataflow/threads-wan-truncated.png", "q5_th_post_repost": "q5/evidence/playwright/threads_post_repost.png",
        "q5_th_drift": "q5/evidence/playwright/threads_post_repost_drift.png", "q5_ig_drift": "q5/evidence/playwright/ig_reels_play_count_drift.png",
        "q5_ig_cloud": "q5/evidence/playwright/ig_reels_cloud_ip_login_redirect.png"}
q5n = {}
for nid, n in Q5_NODES.items():
    q5n[nid] = {"label": n["label"], "who": n["who"], "act": n["act"], "what": n["what"],
                "ev": [{"img": img(EVQ5[k]), "cap": Q5_CAP.get(k) or CAP_FIX.get(EVQ5[k], caps.get(EVQ5[k], ""))} for k in n["ev"]]}
tot = {}
for _, _, who, h, _, _ in Q5_TASKS:
    tot[who] = tot.get(who, 0) + h
maxh = max(t[3] for t in Q5_TASKS)
task_rows = "".join(
    f'<div class="tk{" crit" if crit else ""}"><span class="tn">{E(no)}</span><span class="tt">{E(t)}<small>依賴 {E(dep)}</small></span>'
    f'<span class="tw" data-who="{E(who)}">{E(who)}</span><span class="tb"><i style="width:{h / maxh * 100:.1f}%"></i><b>{h} 小時</b></span></div>'
    for no, t, who, h, dep, crit in Q5_TASKS)
sla_rows = "".join(f"<tr><td>{E(a_)}</td><td>{E(b_)}</td><td>{E(c_)}</td></tr>" for a_, b_, c_ in Q5_SLA)
q5_html = f'''<div class="doc-in">
  <div class="doc-h"><h2>Q5 新指標資料流：誰要改什麼</h2>
  <p>爬蟲早就抓到 IG 觀看數和 Threads 轉發數，缺的是寫進 Elasticsearch 之後的每一層。這張圖照 FigJam 原圖重畫成跟總架構同一套樣式：方框顏色是要不要改，底色是誰負責。點任一個方框看細節與佐證。</p></div>
  <div class="q5">
    <figure class="dg">{"".join(svg)}</figure>
    <aside class="q5d" aria-live="polite"></aside>
  </div>
  <section class="dgrp"><h3>欄位實際叫什麼（Playwright 實抓）</h3>
    <div class="facts3">
      <div><b>IG 觀看數是 play_count</b><span>題目寫 video_view_count，但未登入 Reels 分頁的 JSON 只有 play_count；縮圖 4,298,087 顯示 4.2M，是無條件捨去。</span></div>
      <div><b>畫面轉發數＝repost＋quote</b><span>@threads 16 篇加 2 篇 Use media 共 18 篇全部符合；只看 repost_count，16 篇只有 1 篇對得上。上線前要跟 PM 確認要哪一個。</span></div>
      <div><b>null 不是 0</b><span>分享沒有數字時 reshare_count 是 null。每一層都要存空值，前端顯示「—」，不補 0。</span></div>
    </div>
  </section>
  <section class="dgrp"><h3>(b) 工時與關鍵路徑</h3>
    <p class="lead">一位資料工程師（DE）、一位後端（BE）、一位前端（FE）平行；每天 8 小時，含自測，不含 PM 等待與 UAT。粗框是關鍵路徑：mapping → 爬蟲寫入 → 回補 → 整合 → 驗收，也就是 DE 的整條線。</p>
    <div class="tasks">{task_rows}</div>
    <div class="kpis">
      <div class="kpi"><span class="k">關鍵路徑</span><span class="v">{tot["DE"] + tot["全員"]} 小時</span><span class="s">DE {tot["DE"]} 小時＋全員驗收 {tot["全員"]} 小時；÷ 8 ＝ 5.75 工作天，加 30% 審查與修正取 8 天</span></div>
      <div class="kpi"><span class="k">BE／FE 平行</span><span class="v">{tot["BE"]}＋{tot["FE"]} 小時</span><span class="s">API 契約在第 2 天前定案就不會拖慢上線</span></div>
      <div class="kpi"><span class="k">上線時間</span><span class="v">約 9 工作天</span><span class="s">8 天加 1 天緩衝；週一或週二開工約 11 個日曆天（含週末）</span></div>
    </div>
  </section>
  <section class="dgrp"><h3>(d) SLA「發文 1 小時後查得到」怎麼監控</h3>
    <p class="lead">最可能的瓶頸是爬蟲的抓取頻率，不是 ES 寫入。量的是首次可見延遲，不是數字已經是最終值。</p>
    <div class="scroll"><table class="tbl"><thead><tr><th>指標</th><th>計算</th><th>告警</th></tr></thead><tbody>{sla_rows}</tbody></table></div>
  </section>
  <details class="orig"><summary>FigJam 原圖（匯出 PNG，Figma 未連線所以用 repo 裡的匯出檔）</summary>
    <img src="{img("q5/architecture/diagram-dataflow-vertical-final.png")}" alt="Q5 FigJam 資料流原圖" loading="lazy"></details>
  <p class="src">來源：q5/README.md、q5/answer/b-c-d.md、q5/code/backfill/、shared/capture/capture_manifest.json。</p>
</div>'''

# --- 佐證總覽
pf_order = ["facebook", "ptt", "threads", "instagram", "diagram", "chart", "doc"]
pf_count = {p: sum(1 for e in EVID if e["pf"] == p) for p in pf_order}
cards_html = []
for e in sorted(EVID, key=lambda e: (pf_order.index(e["pf"]), e["q"], e["path"])):
    meta = f'<span class="qt">Q{E(e["q"])}</span><span>{E(e["kind"])}</span>' + (f'<span>{E(e["at"])}</span>' if e["at"] else "")
    extra = f'<p class="res">{E(e["result"])}</p>' if e["result"] and e["result"] not in e["cap"] else ""
    cards_html.append(
        f'<figure class="ev" data-pf="{e["pf"]}" data-q="{E(e["q"])}"><a href="{E(img(e["path"]))}">'
        f'<img src="{E(img(e["path"], thumb=True))}" alt="{E(e["cap"])}" loading="lazy" decoding="async"></a>'
        f'<figcaption><p class="cap">{E(e["cap"])}</p>{extra}<div class="meta">{meta}</div><code>{E(e["path"])}</code></figcaption></figure>')
real = sum(1 for e in EVID if e["kind"] == "Playwright 實抓")

def ev_card(e):
    meta = f'<span class="qt">Q{E(e["q"])}</span><span>{E(e["kind"])}</span>' + (f'<span>{E(e["at"])}</span>' if e["at"] else "")
    extra = f'<p class="res">{E(e["result"])}</p>' if e["result"] and e["result"] not in e["cap"] else ""
    return (f'<figure class="ev" data-pf="{e["pf"]}" data-q="{E(e["q"])}"><a href="{E(img(e["path"]))}">'
            f'<img src="{E(img(e["path"], thumb=True))}" alt="{E(e["cap"])}" loading="lazy" decoding="async"></a>'
            f'<figcaption><p class="cap">{E(e["cap"])}</p>{extra}<div class="meta">{meta}</div><code>{E(e["path"])}</code></figcaption></figure>')

def q_doc(q):
    # one page per question: question, answer, data path, checks, evidence
    Q = D["questions"][q]
    ul = lambda xs: "<ul class=\"qlist\">" + "".join(f"<li>{E(x)}</li>" for x in xs) + "</ul>"
    parts = [f'<div class="doc-h"><h2>{E(Q.get("title", "Q" + q))}</h2><p class="qask">{E(Q.get("ask", ""))}</p></div>']
    if Q.get("facts"):
        parts.append(f'<section class="dgrp"><h3>{E(Q.get("factsTitle") or "答案重點")}</h3>{ul(Q["facts"])}</section>')
    if Q.get("path"):
        parts.append(f'<section class="dgrp"><h3>資料怎麼走</h3><p class="lead qpath">{E(Q["path"])}</p></section>')
    if Q.get("notes"):
        parts.append(f'<section class="dgrp"><h3>細節與限制</h3>{ul(Q["notes"])}</section>')
    if Q.get("figures"):
        figs = "".join(f'<figure><a href="{E(f["img"])}"><img src="{E(f["img"])}" alt="{E(f["cap"])}" loading="lazy" decoding="async"></a><figcaption>{E(f["cap"])}</figcaption></figure>' for f in Q["figures"])
        parts.append(f'<section class="dgrp"><h3>監控看板</h3><div class="figs">{figs}</div></section>')
    if Q.get("verify"):
        parts.append(f'<section class="dgrp"><h3>怎麼驗證的</h3>{ul(Q["verify"])}</section>')
    if Q.get("todo"):
        parts.append(f'<section class="dgrp"><h3>還沒驗證的部分</h3><p class="lead">{E(Q["todo"])}</p></section>')
    evs = sorted((e for e in EVID if e["q"] == q), key=lambda e: (pf_order.index(e["pf"]), e["path"]))
    if evs:
        parts.append(f'<section class="dgrp"><h3>佐證 <b>{len(evs)}</b> 張</h3><div class="gal">{"".join(ev_card(e) for e in evs)}</div></section>')
    if Q.get("src"):
        parts.append(f'<p class="src">{E(Q["src"])}</p>')
    return '<div class="doc-in qdoc">' + "".join(parts) + '</div>' 
st_rows = "".join(
    f'<tr><td><span class="qt">Q{E(r["q"])}</span></td><td>{E(r["run"])}</td><td>{E(r["test"])}</td><td>{E(r["note"])}</td></tr>'
    for r in STATUS_ROWS)
st_table = (f'<section class="dgrp"><h3>程式與基礎設施驗證</h3><div class="scroll"><table class="tbl"><thead><tr><th>題</th><th>實跑</th><th>測試</th><th>備註</th></tr></thead><tbody>{st_rows}</tbody></table></div>'
            f'<p class="lead">{E(TF_PLAN)}</p></section>') if STATUS_ROWS else ""
fbtn = lambda attr, val, lab, on=False: f'<button type="button" data-{attr}="{val}" aria-pressed="{"true" if on else "false"}">{E(lab)}</button>'
ev_html = f'''<div class="doc-in">
  <div class="doc-h"><h2>佐證總覽</h2>
  <p>總架構每個說法背後的截圖、圖表與架構圖，一共 {len(EVID)} 張，其中 {real} 張是 Playwright 或瀏覽器實抓（全部未登入、不存 cookie 或 token；FB、IG、Threads 截圖裡非公眾人物的姓名、ID、電話已遮，PTT 帳號屬公開資料，大多沒有遮；ptt_article_comments.png 是遮掉帳號與 IP 的重拍版）。點圖看原尺寸。</p></div>
  {st_table}
  <div class="fbars">
    <div class="fbar" role="group" aria-label="依平台" data-group="pf">{fbtn("f", "all", "全部", True)}{"".join(fbtn("f", p, f"{PF_LABEL[p]} {pf_count[p]}") for p in pf_order if pf_count[p])}</div>
    <div class="fbar" role="group" aria-label="依題目" data-group="q">{fbtn("fq", "all", "全部題目", True)}{"".join(fbtn("fq", q, f"Q{q}") for q in "123456")}</div>
  </div>
  <p class="cnt" aria-live="polite"></p>
  <div class="gal">{"".join(cards_html)}</div>
  <p class="src">來源：shared/capture/capture_manifest.json（每張實抓的主張、結果、時間）與各題 README 的圖說。</p>
</div>'''

# --- Q2 strip
# source: q2/code/RESULTS.md and q2/data/*.json
q2_strip = ('<div class="strip"><b>Q2 實測（PostgreSQL 16.15，100 萬則；Linux 容器與 Docker）</b>'
            '<span>Linux 重跑：表 1,024,000,000 B 與 simple 3,268,608 B 都和頁面相同</span>'
            '<span>頁面的 388 MB（406,396,928 B）約是表的 40%，兩種索引差不多大，合計 1.33 GB；這些都只在 1,000 段合成內文重複使用時成立，是下限</span>'
            '<span>以 GIN 模擬 pg_bigm，PTT 與維基各 100 萬則：兩字詞索引 946,946,048 到 1,165,492,224 B，是頁面的 2.3 到 2.9 倍</span>'
            '<span>pg_bigm 1.2 本體（Docker）：頁面做法 409,567,232 B，多 0.78%；PTT 100 萬則 953,729,024 B，比模擬多 0.72%</span>'
            '<span>2 字詞查詢（PTT）：pg_bigm 走索引 119 到 147 毫秒，pg_trgm 全表掃描 0.54 到 0.66 秒，快 3.7 到 4.9 倍；「台灣」兩者都全表掃描，約 0.6 秒</span>'
            '<span>中文語料的 pg_trgm 是 1,371,783,168 到 1,551,712,256 B，兩字詞索引比它小 25% 到 31%</span>'
            '<span>Windows 的 simple 多 43%：Linux 把 U+9FC4 到 U+9FFF 的 60 字換成空白就逐位元組重現</span>'
            '<span class="muted">來源：q2/code/RESULTS.md 與 q2/data/</span></div>')

# ---------------------------------------------------------------- write into the page
def rep(old, new, count=1):
    global page
    assert old in page, old[:80]
    page = page.replace(old, new, count)

# ---------------------------------------------------------------- one integrated view + GCP icons
HERE = os.path.dirname(os.path.abspath(__file__))
if a.single:
    for v in ("poc", "a", "b", "c"):
        D["variants"].pop(v, None); PAGES.pop(v, None)
        page = re.sub(r'<button type="button" role="tab" data-v="%s"[^>]*>.*?</button>' % v, "", page, count=1)
        page = re.sub(r'<div class="frame" data-v="%s"[^>]*></div>' % v, "", page, count=1)
    D["order"] = ["int"]
if a.drop_tabs:
    for t in ("seq", "uvicorn", "notes"):
        PAGES.pop(t, None)
    D["variants"]["int"]["changed"] = [False] * len(D["variants"]["int"]["changed"])
    page = page.replace('<button type="button" class="diff" aria-pressed="false"', '<button type="button" class="diff" hidden aria-pressed="false"', 1)
if a.obs:
    # 7 觀測: one band across the bottom of the 整合 diagram, two rows. Row 1: crawler/collector request logs, crawler and
    # collector metrics, service metrics, ES cluster health. Row 2: completeness reconciliation, quality daily report,
    # Grafana dashboards + Alertmanager → PM page. Local stand-ins (Loki+Alloy / Prometheus / elasticsearch_exporter /
    # Grafana / Alertmanager in Docker, q6/stack, all run end to end on 2026-11-02) carry the GCP product they map to in the
    # title; the monitoring itself has no Terraform. Nodes join the 抓取 (2) and 分析 (5) stages so the stage buttons and
    # the 依題目 lights keep working; no edges, the band reads as "everything above reports here".
    OBS_Y, OBS_H, NODE_W, GAP, ROW_Y = 636, 170, 240, 30, (670, 740)
    OBS = [
        ("obs-logs", "n new", "Cloud Logging", "每個請求一行 · 本機 Loki＋Alloy", 2, ["4", "6"], 0,
         "爬蟲端與 Q6 公開 feed 採集器每個請求都留一行 JSON 紀錄：平台／feed、網址、狀態碼（200–500）、TTFB 與總延遲、結果分類（outcome）、重試與退避，異常時加截圖；本機由 Alloy 收所有容器的 stdout 進 Loki，Alloy 在取樣前把採集紀錄算成行數指標，Loki ruler 再把紀錄轉成每秒請求數並出 CrawlLoginWallFromLogs（紀錄裡出現登入牆就告警）。GCP 對應 Cloud Logging：Cloud Run 的 stdout JSON 直接進 Cloud Logging，log-based metrics 出同樣的請求數與 4xx／5xx 比例。2026-11-02 六渠道爬蟲與 PTT、中央社、公視的公開 feed 採集都是靠這份紀錄看延遲尖峰與退避。沒有 Terraform。",
         {"gcp": ["Cloud Logging"], "tone": "direct", "st": "未寫 Terraform", "onprem": "Loki＋Alloy（Docker）",
          "diff": "本機是 Alloy 把 docker 標籤變成 service 標籤後送 Loki；雲端 Cloud Run 把 stdout 寫成 JSON 就進 Cloud Logging，保留天數與匯出到 BigQuery 要另外設。"}),
        ("obs-crawl", "n new", "Cloud Monitoring", "爬蟲與採集指標 · Prometheus", 2, ["4", "6"], 0,
         "兩種來源。六渠道爬蟲把計數與延遲直方圖推到 Pushgateway，Prometheus 定時抓：每分鐘請求數（依平台）、狀態 2xx／4xx／5xx／失敗、API p95（依平台）。Q6 公開 feed 採集器直接開 /metrics 給 Prometheus 抓：每次請求的結果分類（ok、empty、not_modified、login_wall、rate_limited、forbidden、server_error 等 11 種）、解析／新增／更新篇數、首抓延遲直方圖（只算發文 24 小時內）、退避等級、最後一次成功時間、YouTube 配額、robots.txt 雜湊。告警（提案值）：登入牆 1 次就 page、產出率比 7 天前同時段掉一半、feed 過期或死亡（沒有金鑰的 feed 不算）、首抓延遲 p95 超過 45 分鐘警告、60 分鐘 page。退避看這些指標自動放慢，不是硬停。GCP 對應 Cloud Monitoring 的自訂指標。沒有 Terraform。",
         {"gcp": ["Cloud Monitoring"], "tone": "direct", "st": "未寫 Terraform", "onprem": "Prometheus（Docker）；爬蟲經 Pushgateway，Q6 採集器直接被抓",
          "diff": "Pushgateway 的指標不會自己過期，爬蟲停了要看最後推送時間；Q6 採集器靠 collector_last_ok_timestamp 判過期；雲端改用 Cloud Monitoring 自訂指標就沒有這個問題。"}),
        ("obs-svc", "n new", "Cloud Monitoring", "服務指標 · OTel→Prometheus", 5, ["5", "6"], 0,
         "斷詞服務、回補 Job、文字雲 API 與 PM 頁出指標，Prometheus 抓：文字雲查詢延遲直方圖（失敗也記，桶含 180 與 300 秒）、超過 180 秒的查詢數、ES 逾時與部分結果、快取命中、串流 ack／nack 與最舊未確認訊息年齡、每台服務的字典版本與存活心跳、定向重跑、回補燃盡（剩餘篇數、每秒篇數、ETA）、PM 頁 RUM（LCP、CLS、INP、JS 錯誤）。3 分鐘 SLO 用 Sloth 產生多視窗燃燒率：28 天 99% 的查詢成功且在 180 秒內（提案值），1 小時燒掉 2% 預算（14.4 倍）或 6 小時燒掉 5%（6 倍）就呼叫，1 天 3 倍或 3 天 1 倍開單；沒有流量的時段不算進錯誤預算。本機沙盤回補時全期查詢 p95 從 114 ms 升到 192 ms，3,727 次查詢沒有一次超過 180 秒。GCP 對應 Cloud Monitoring（Cloud Run 內建指標加自訂指標）。沒有 Terraform。",
         {"gcp": ["Cloud Monitoring"], "tone": "direct", "st": "未寫 Terraform", "onprem": "OpenTelemetry Collector＋Prometheus＋Sloth（Docker）",
          "diff": "Cloud Run 的請求延遲與併發是內建指標；自訂的 ES 寫入速率、串流延遲、燃燒率要經 OTel exporter 或 Managed Service for Prometheus 進 Cloud Monitoring。"}),
        ("obs-es", "n new", "Cloud Monitoring", "ES 健康 · exporter→Prometheus", 5, ["5", "6"], 0,
         "elasticsearch_exporter 抓叢集狀態（紅／黃）、磁碟水位、circuit breaker、寫入與搜尋執行緒池拒絕、JVM heap 與 GC、未分配分片、SLM 快照；告警（提案值）：叢集紅 page、黃持續 10 分鐘、磁碟 80% 警告、88% 或 predict_linear 4 小時內滿 page、breaker 跳、拒絕、快照失敗或過期。另有索引 slowlog（查詢超過 60 秒）進 Loki。2026-11-02 端對端驗證結束時仍在響的 ES 磁碟告警：整台容器的磁碟用了 94%。GCP 對應 Compute Engine VM 上 Ops Agent 的 Elasticsearch 整合 → Cloud Monitoring。沒有 Terraform（ES VM 本身也沒有）。",
         {"gcp": ["Cloud Monitoring"], "tone": "direct", "st": "未寫 Terraform", "onprem": "elasticsearch_exporter v1.9.0＋Prometheus（Docker）",
          "diff": "Ops Agent 的 Elasticsearch 接收器出的是 workload.googleapis.com/elasticsearch.* 指標，名稱與 exporter 不同，告警規則要對照改寫。"}),
        ("obs-recon", "n new", "Cloud Monitoring", "完整性對帳 · 原文湖 vs ES 四層", 5, ["6"], 1,
         "每小時一次（seg-recon）逐月對帳：原文湖的篇數、ES 有文件、ES 有 v2、ES v2 是最新字典版、ES 已刪（tombstone）、帳本，算出缺多少篇、多多少篇、v2 覆蓋率，寫 recon_daily 表與指標。告警（提案值）：通過的分區缺口超過 0.1%、v2 覆蓋率掉、對帳超過 2 小時沒跑、月份不明的文件（沒有 published_at）、未斷詞的文件 page。2026-11-02 端對端驗證跑出的告警：Instagram 的 48 篇樣本沒有 published_at，被歸到月份不明。GCP 對應：同一支程式跑在 Cloud Run Job（Cloud Scheduler 每小時），指標進 Cloud Monitoring。沒有 Terraform。",
         {"gcp": ["Cloud Monitoring"], "tone": "direct", "st": "未寫 Terraform", "onprem": "seg-recon（Docker，每小時；心跳每 60 秒）",
          "diff": "本機讀 PostgreSQL 的 raw 分區當原文湖；雲端原文在 Cloud Storage 與 BigQuery 匯出，對帳的原文層要改讀 BigQuery 的計數。"}),
        ("obs-quality", "n new", "Cloud Monitoring", "品質日報 · 金標召回／RBO", 5, ["6"], 1,
         "每天一次（seg-quality）用 ES 裡的文章算文字雲品質：雜訊外漏比例（網址殘片、電話、單字、停用詞，依渠道）、地名被拆比例、整詞召回、150 題金標召回（v0／v1／v2 各一條）與 McNemar p 值、前 30 詞的 RBO 與 JS 散度（今天對昨天）、新詞暴增；寫 quality_daily 表。告警（提案值）：任何雜訊外漏、整詞召回低於 99%、金標召回低於 71%、前 30 詞 RBO 低於 0.5、日報過期。2026-11-02 端對端驗證：整詞召回 0.925 低於 0.99 的提案門檻（金標裡有地名被拆）。GCP 對應：Cloud Run Job（Cloud Scheduler 每天）→ Cloud Monitoring。沒有 Terraform。",
         {"gcp": ["Cloud Monitoring"], "tone": "direct", "st": "未寫 Terraform", "onprem": "seg-quality（Docker，每天）",
          "diff": "金標 150 題與停用詞清單跟著映像走，本機與雲端同一份；雲端把 quality_daily 放 Cloud SQL。"}),
        ("obs-grafana", "n new", "Grafana＋Alertmanager", "2 張看板 90 面板 · 告警 66 條 → PM 頁", 5, ["5", "6"], 1,
         "主看板 9 層 69 面板依資料流排：客戶 SLO（3 分鐘燃燒率、28 天錯誤預算）→ PM 頁 RUM → 完整性對帳 → 串流積壓 → 字典發版收斂 → 回補燃盡 → 品質日報 → ES 健康 → 平台（PostgreSQL、日誌、監控自身）；採集看板 6 列 21 面板（結果分類、產出率、首抓延遲、退避與配額、延遲分段、監控自身）。看板 JSON 由程式產生、進版控。告警 66 條（含 Sloth 產生的 SLO 燃燒率）加 30 條記錄規則，每條都有 for、嚴重度（page／warn／ticket）與摘要；Alertmanager 以 webhook 送到文字雲 API，PM A/B 頁頂部直接顯示正在響的告警（零成本通知管道，Email／Telegram 另加 receiver 即可）。2026-11-02 端對端跑過：Prometheus → Alertmanager → PM 橫幅，firing 與 resolved 都收到。GCP 對應 Cloud Monitoring 的看板與告警政策，或 Managed Service for Prometheus 直接載入同一份規則檔。截圖在 Q6 分頁。沒有 Terraform。",
         {"gcp": ["Cloud Monitoring"], "tone": "direct", "st": "未寫 Terraform", "onprem": "Grafana 12.1.1＋Alertmanager v0.28.1（Docker）",
          "diff": "看板 JSON 可以直接版本控制（q6/stack/monitoring/grafana）；雲端告警政策要寫成 google_monitoring_alert_policy，或用 Managed Service for Prometheus 載入同一份規則檔，目前都沒有。"}),
    ]
    V = D["variants"]["int"]
    for nid, cls, label, s_, stage, q, row, note, dep in OBS:
        V["nodes"][nid] = {"label": label, "sub": s_, "icon": None, "stage": stage, "tf": [], "note": note, "q": q, "dep": dep}
        D["stages"][stage]["ids"].append(nid)
    V["summary"] += " 最下面的「7 觀測」是爬蟲、採集與服務的紀錄、指標、對帳、品質日報、看板與告警七個節點：本機用 Loki＋Alloy、Prometheus、elasticsearch_exporter、Grafana、Alertmanager（2026-11-02 端對端跑過），GCP 對應 Cloud Logging 與 Cloud Monitoring，監控本身沒有 Terraform。"
    sub = base64.b64decode(PAGES["int"]).decode("utf8")
    for o, n in [('viewBox="0 0 1090 676"', 'viewBox="0 0 1090 858"'), ("h: 676 }", "h: 858 }"),
                 ('aria-label="QSearch 整合架構圖：排程、派工、抓取、落地、解析、拿去用"', 'aria-label="QSearch 整合架構圖：排程、派工、抓取、落地、解析、拿去用、觀測"')]:
        assert sub.count(o) == 1, o
        sub = sub.replace(o, n)
    i = sub.index('<g class="lg">'); k = sub.index("</svg>", i)
    legend = sub[i:k]
    assert legend.count('y="646"') == 4 and legend.count('y="652"') == 5 and legend.count("M510 652 H540") == 1
    legend = legend.replace('y="646"', 'y="828"').replace('y="652"', 'y="834"').replace("M510 652 H540", "M510 834 H540")
    band = [f'<rect class="band" x="10" y="{OBS_Y}" width="1070" height="{OBS_H}" rx="10"/>', f'<text class="bl" x="20" y="{OBS_Y + 20}">7 觀測</text>']
    col = [0, 0]
    for nid, cls, label, s_, stage, q, row, note, dep in OBS:
        x = 20 + col[row] * (NODE_W + GAP); col[row] += 1; cx = x + NODE_W // 2; y = ROW_Y[row]
        band.append(f'<g class="{cls}" data-node-id="{nid}" tabindex="0" role="button" aria-label="{label}：{s_}">'
                    f'<rect x="{x}" y="{y}" width="{NODE_W}" height="54" rx="8"/>'
                    f'<text class="l" x="{cx}" y="{y + 19}" text-anchor="middle" dominant-baseline="central">{label}</text>'
                    f'<text class="s" x="{cx}" y="{y + 37}" text-anchor="middle" dominant-baseline="central">{s_}</text></g>')
    sub = sub[:i] + "\n".join(band) + "\n" + legend + sub[k:]
    PAGES["int"] = base64.b64encode(sub.encode("utf8")).decode()
if a.icons:
    # latest Google Cloud icons (cloud.google.com/icons: core-products-icons.zip + category-icons.zip),
    # the same set the original page already uses in D.icons; no legacy icons
    ICON_FILE = ["cloud-run", "cloud-sql", "cloud-storage", "data-analytics", "developer-tools", "databases"] + (["observability"] if a.obs else [])
    GICON = {k: "data:image/svg+xml;base64," + base64.b64encode(open(os.path.join(HERE, "icons", k + ".svg"), "rb").read()).decode()
             for k in ICON_FILE}
    # same node -> icon picks as the original 整合 diagram; Firestore token pool gets the Databases category icon
    NODE_ICON = {"trigger": "developer-tools", "sched": "cloud-run", "jobdb": "cloud-sql", "dispatch": "developer-tools",
                 "crawl": "cloud-run", "tokenpool": "databases", "tokenjob": "cloud-run", "gcs": "cloud-storage",
                 "fetched": "data-analytics", "parser": "cloud-run", "replay": "cloud-run", "searchdb": "cloud-sql",
                 "searchapi": "data-analytics"}
    if a.obs:  # Cloud Logging / Cloud Monitoring have no product icon in the latest set; the Observability category icon covers both
        NODE_ICON.update({k: "observability" for k in ("obs-logs", "obs-crawl", "obs-svc", "obs-es", "obs-recon", "obs-quality")})
    for k, v in GICON.items():
        D["icons"].setdefault(k, v)
    for nid, ic in NODE_ICON.items():
        if nid in D["variants"]["int"]["nodes"]:
            D["variants"]["int"]["nodes"][nid]["icon"] = ic
    sub = base64.b64decode(PAGES["int"]).decode("utf8")
    for o, n in [  # stage 5: pg_bigm is Q2 only; Q3 lands in BigQuery, fed by the parser, not by pg_bigm
        ('aria-label="pg_bigm 搜尋庫：Cloud SQL · 7 天全文"', 'aria-label="pg_bigm 搜尋庫：Cloud SQL · Q2 FB 貼文"'),
        ('dominant-baseline="central">Cloud SQL · 7 天全文</text>', 'dominant-baseline="central">Cloud SQL · Q2 FB 貼文</text>'),
        ('aria-label="搜尋 API：Cloud Run · LIKE 查詢"', 'aria-label="BigQuery：Q3 PTT 7 天 · 查詢 API"'),
        ('dominant-baseline="central">搜尋 API</text>', 'dominant-baseline="central">BigQuery</text>'),
        ('dominant-baseline="central">Cloud Run · LIKE 查詢</text>', 'dominant-baseline="central">Q3 PTT 7 天 · 查詢 API</text>'),
        ('<g class="e" data-edge-from="searchdb" data-edge-to="searchapi"><path d="M170 490 H198" marker-end="url(#ah)"/></g>',
         '<g class="e" data-edge-from="parser" data-edge-to="searchapi"><path d="M995 213 V440 H275 V461" marker-end="url(#ah)"/></g>')]:
        assert sub.count(o) == 1, o
        sub = sub.replace(o, n)
    def add_icon(m):
        g = m.group(0); nid = m.group(1)
        if nid not in NODE_ICON:
            return g
        im = f'<image class="gi" href="{GICON[NODE_ICON[nid]]}" width="18" height="18" x="0" y="0" aria-hidden="true"/>'
        return g.replace('<text class="l"', im + '<text class="l"', 1)
    sub = re.sub(r'<g class="n[^"]*" data-node-id="([\w-]+)"[^>]*>.*?</g>', add_icon, sub, flags=re.S)
    # place each icon just left of its title; measure with canvas so it works before layout
    sub = sub.replace("</svg>", "</svg>\n<script>\n" + open(os.path.join(HERE, "icons.js"), encoding="utf8").read() + "</script>", 1)
    PAGES["int"] = base64.b64encode(sub.encode("utf8")).decode()

page = page[:page.index("const D = ") + 10] + json.dumps(D, ensure_ascii=False) + page[raw("D")[2]:]
page = page[:page.index("const PAGES = ") + 14] + json.dumps(PAGES) + page[raw("PAGES")[2]:]

# 底稿裡的兩處用字改寫
rep('"佐證（Playwright 實抓）"', '"佐證（Playwright 實抓）"')
rep('不會刪掉資源本身', '不會刪掉資源本身')
rep('<button type="button" role="tab" data-tab="notes">推演筆記</button>',
    ('' if a.drop_tabs else '<button type="button" role="tab" data-tab="notes">推演筆記</button>'
     '<button type="button" role="tab" data-tab="deploy">GCP／地端</button>') +
    ("".join(f'<button type="button" role="tab" data-tab="{QTAB[q]}">{QLABEL[q]}</button>' for q in "123456") if a.q_tabs else
     '<button type="button" role="tab" data-tab="q2">Q2 估算</button>'
     '<button type="button" role="tab" data-tab="q5flow">Q5 資料流</button>') +
    '<button type="button" role="tab" data-tab="evidence">佐證總覽</button>')
rep('<section class="pane" id="pane-notes" role="tabpanel" data-title="推演筆記"></section>',
    ('' if a.drop_tabs else '<section class="pane" id="pane-notes" role="tabpanel" data-title="推演筆記"></section>\n'
     f'  <section class="pane doc" id="pane-deploy" role="tabpanel" data-title="GCP／地端">{deploy_html}</section>\n') +
    ((f'  <section class="pane doc" id="pane-q1" role="tabpanel" data-title="Q1">{q_doc("1")}</section>\n'
      f'  <section class="pane doc qsplit" id="pane-q2" role="tabpanel" data-title="Q2">{q_doc("2")}<div class="doc-in"><section class="dgrp"><h3>Q2 估算解剖頁（互動）</h3>{q2_strip}<div class="frame-host q2frame" data-title="Q2 估算：Postgres 貼文表解剖"></div></section></div></section>\n'
      f'  <section class="pane doc" id="pane-q3" role="tabpanel" data-title="Q3">{q_doc("3")}</section>\n'
      f'  <section class="pane doc" id="pane-q4" role="tabpanel" data-title="Q4">{q_doc("4")}</section>\n'
      f'  <section class="pane doc" id="pane-q5flow" role="tabpanel" data-title="Q5">{q_doc("5")}{q5_html}</section>\n'
      f'  <section class="pane doc" id="pane-q6" role="tabpanel" data-title="Q6">{q_doc("6")}</section>\n') if a.q_tabs else
     (f'  <section class="pane split" id="pane-q2" role="tabpanel">{q2_strip}<div class="frame-host" data-title="Q2 估算：Postgres 貼文表解剖"></div></section>\n'
      f'  <section class="pane doc" id="pane-q5flow" role="tabpanel" data-title="Q5 資料流">{q5_html}</section>\n')) +
    f'  <section class="pane doc" id="pane-evidence" role="tabpanel" data-title="佐證總覽">{ev_html}</section>')

if a.drop_tabs:
    for t, name in (("seq", "請求時序"), ("uvicorn", "Uvicorn 背景")):
        rep(f'<button type="button" role="tab" data-tab="{t}">{name}</button>', "")
        rep(f'<section class="pane" id="pane-{t}" role="tabpanel" data-title="{name}"></section>', "")

CSS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "added.css"), encoding="utf8").read()
rep('@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }',
    CSS + '\n@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }')

# evidence images open in an in-page viewer: a new tab from inside the artifact frame hits claude.ai and is blocked
rep('</body>', '<script>\n' + open(os.path.join(HERE, "lightbox.js"), encoding="utf8").read() + '</script>\n</body>')
# sub-page loader: static panes have no PAGES entry; Q2 frame goes into its host
rep('    if (tab !== "shape") ensure($("#pane-" + tab), tab);',
    '    if (tab !== "shape" && PAGES[tab]) ensure($("#pane-" + tab + " .frame-host") || $("#pane-" + tab), tab);')
# reviewer-facing page (2026-10-31): no talk-track box or mnemonic on question cards; Terraform snippets keep their explanation under a neutral label
rep('    if (Q.say) box.append(el("p", "say", Q.say));', '')
rep('.say::before { content: "面試說法";', '.say::before { content: "說明";')
rep('.gotcha::before { content: "追問陷阱";', '.gotcha::before { content: "注意";')
rep('el("p", "p-sum", V.summary), el("p", "mn", D.mnemonic));', 'el("p", "p-sum", V.summary));')
# question panel: links to the new tabs
rep('    if (Q.todo) box.append(el("p", "note", Q.todo));',
    '''    if (Q.todo) box.append(el("p", "note", Q.todo));
    if (Q.links && Q.links.length) {
      const ls = el("p", "q-chips");
      ls.append(el("span", null, "另見"));
      Q.links.forEach(l => { const b = el("button", "q-chip", l.label + " →"); b.type = "button"; b.dataset.gotoTab = l.tab; if (l.q) b.dataset.evq = l.q; ls.append(b); });
      box.append(ls);
    }''')
# node panel: GCP / on-prem rows
rep('    if (n.note) panel.append(el("p", "note", n.note));',
    '''    if (n.dep) {
      const d = n.dep, box = el("section", "dep");
      const r1 = el("div", "dep-r"); r1.append(el("b", null, "GCP"));
      const c1 = el("span"); (d.gcp.length ? d.gcp : ["—"]).forEach(t => c1.append(el("code", null, t)));
      const st = el("span", "st", d.st); st.dataset.tone = d.tone; c1.append(st); r1.append(c1);
      const r2 = el("div", "dep-r"); r2.append(el("b", null, "地端"), el("span", null, d.onprem));
      const r3 = el("div", "dep-r"); r3.append(el("b", null, "差異"), el("span", null, d.diff));
      box.append(r1, r2, r3); panel.append(box);
    }
    if (n.note) panel.append(el("p", "note", n.note));''')
rep('''    if (e.target.closest("[data-back]")) return go({ node: null });''',
    '''    if (e.target.closest("[data-back]")) return go({ node: null });
    const tl = e.target.closest("[data-goto-tab]");
    if (tl) { if (tl.dataset.evq) evFilter("all", tl.dataset.evq); return go({ tab: tl.dataset.gotoTab }); }''')
JS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "added.js"), encoding="utf8").read()
JS = JS.replace("__Q5N__", json.dumps(q5n, ensure_ascii=False))
rep('  window.addEventListener("hashchange", route);', JS + '\n  window.addEventListener("hashchange", route);')

open(a.out, "w", encoding="utf8").write(page)
if a.files:
    json.dump(FILES, open(a.files, "w"), indent=1)
print("ok", a.mode, len(page), "bytes;", len(EVID), "evidence;", len(FILES), "files")
