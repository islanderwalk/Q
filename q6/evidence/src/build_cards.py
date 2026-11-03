"""Build one evidence card (HTML) per claim: the claim, its source, how and when it was
captured, then the highlighted excerpts. render_cards.js turns each card into a PNG.

Usage: python3 build_cards.py <work_dir> <experiments_out_dir>
  work_dir: crops/, evidence_web.json, web_meta.json; cards/ is written here (styles come from q6/architecture/src/diagram.css)
  experiments_out_dir: results.json, results2.json, results3.json, verify.log, results.sha256
"""
import html
import json
import sys
from pathlib import Path

from PIL import Image

W = Path(sys.argv[1])
EXP = Path(sys.argv[2])
CARDS = W / "cards"
CARDS.mkdir(exist_ok=True)
esc = html.escape

CAT_LABEL = {
    "overview": "佐證 · 總覽",
    "a-backfill": "佐證 · (a) 歷史資料回朔",
    "b-dictionary": "佐證 · (b) 字典維護",
    "c-consistency": "佐證 · (c) 一致性與字典更新",
}
# card id -> file under q6/
TARGET = {
    "ev_00_gcp_qsearch_stack": "evidence/overview/evidence-qsearch-gcp-stack.png",
    "ev_00_research_page": "evidence/overview/evidence-research-page.png",
    "ev_a1_es_update_reindex": "evidence/a-backfill/evidence-es-update-reindex.png",
    "ev_a2_es_source_excludes": "evidence/a-backfill/evidence-es-source-excludes.png",
    "ev_a3_es_update_by_query": "evidence/a-backfill/evidence-es-update-by-query.png",
    "ev_a4_es_tune_indexing": "evidence/a-backfill/evidence-es-tune-indexing.png",
    "ev_a5_es_global_ordinals": "evidence/a-backfill/evidence-es-global-ordinals.png",
    "ev_a6_es_random_sampler": "evidence/a-backfill/evidence-es-random-sampler.png",
    "ev_a7_jieba_readme_speed": "evidence/a-backfill/evidence-jieba-readme-speed.png",
    "ev_a8_rocling_runtime": "evidence/a-backfill/evidence-rocling-runtime.png",
    "ev_a9_vm_throughput": "evidence/a-backfill/evidence-vm-throughput.png",
    "ev_b1_jieba_dict_big_userdict": "evidence/b-dictionary/evidence-jieba-dict-big-userdict.png",
    "ev_b2_ckiptagger_table_dictionary": "evidence/b-dictionary/evidence-ckiptagger-f1-dictionary.png",
    "ev_b3_ckip_transformers_table": "evidence/b-dictionary/evidence-ckip-transformers-f1.png",
    "ev_b4_rocling_f1": "evidence/b-dictionary/evidence-rocling-f1.png",
    "ev_b5_es_reload_search_analyzers": "evidence/b-dictionary/evidence-es-reload-search-analyzers.png",
    "ev_b6_vm_dict_coverage": "evidence/b-dictionary/evidence-vm-dict-coverage.png",
    "ev_c1_jieba_add_word_total": "evidence/c-consistency/evidence-jieba-add-word-total.png",
    "ev_c2_jieba_calc_logtotal": "evidence/c-consistency/evidence-jieba-calc-logtotal.png",
    "ev_c3_vm_blast_radius": "evidence/c-consistency/evidence-vm-blast-radius.png",
    "ev_c4_es_terms_exclude": "evidence/c-consistency/evidence-es-terms-exclude.png"
}
CSS = """
<link rel="stylesheet" href="../../../architecture/src/diagram.css">
<style>
  body { background: #fff; }
  .ev { width: 1280px; padding: 28px 32px 22px; background: #fff; }
  .ev-top { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
  .ev-cat { font-size: 14px; color: var(--muted); letter-spacing: .06em; }
  .ev-kind { font-size: 13px; font-weight: 700; padding: 3px 12px; border-radius: 999px; border: 1.5px solid currentColor; }
  .k-doc { color: #0c6b63; } .k-code { color: #1d4ed8; } .k-paper { color: #9a5806; } .k-run { color: #6a3cc0; } .k-page { color: #55646f; }
  h1.ev-claim { font-family: "Noto Serif TC", "WenQuanYi Zen Hei", serif; font-size: 25px; line-height: 1.5; margin: 10px 0 14px; }
  .ev-src { display: grid; grid-template-columns: 64px 1fr; gap: 6px 14px; font-size: 14.5px; line-height: 1.6; margin: 0 0 18px; padding: 12px 16px; background: #f2f5f6; border-radius: 10px; }
  .ev-src dt { color: var(--muted); font-weight: 700; }
  .ev-src dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
  .url { font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 13px; color: #0c6b63; }
  figure { margin: 0 0 16px; }
  figure img { display: block; border: 1.5px solid #c3cdd3; border-radius: 8px; max-width: 1216px; }
  figcaption { font-size: 13px; color: var(--muted); margin-top: 6px; }
  figcaption code { color: var(--ink); }
  .grid2 { display: grid; grid-template-columns: auto auto; gap: 18px; align-items: start; justify-content: start; }
  pre.term { margin: 0 0 16px; padding: 16px 18px; background: #0f1a1f; color: #d7e3e8; border-radius: 10px; font-family: "JetBrains Mono", "DejaVu Sans Mono", "WenQuanYi Zen Hei Mono", monospace; font-size: 13px; line-height: 1.75; white-space: pre-wrap; }
  pre.term .hl { color: #ffd166; font-weight: 600; }
  pre.term .dim { color: #8aa0aa; }
  table.t { border-collapse: collapse; width: 100%; font-size: 14.5px; margin: 0 0 16px; }
  table.t th, table.t td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #e2e8eb; vertical-align: top; }
  table.t th { font-size: 12.5px; color: var(--muted); background: #f2f5f6; }
  table.t td.n { text-align: right; font-family: "JetBrains Mono", monospace; font-variant-numeric: tabular-nums; white-space: nowrap; }
  table.t td.mono { font-family: "JetBrains Mono", "WenQuanYi Zen Hei Mono", monospace; font-size: 13.5px; }
  table.t tr.pin td { background: #e3f2ef; }
  .bad { color: #b0223d; font-weight: 700; } .good { color: #0c6b63; font-weight: 700; }
  h2.sec { font-size: 15px; margin: 4px 0 8px; color: var(--ink); }
  .ev-foot { display: flex; justify-content: space-between; gap: 16px; border-top: 1px solid #dde4e8; padding-top: 10px; margin-top: 6px; font-size: 12.5px; color: var(--muted); }
  .ev-foot span:last-child { font-family: "JetBrains Mono", monospace; }
</style>
"""

manifest = []


def page(cid, kind, kind_cls, claim, src_rows, body, foot_left):
    out = TARGET[cid]
    cat = out.split("/")[1]
    rows = "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in src_rows)
    doc = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">{CSS}</head><body>
<div class="ev">
  <div class="ev-top"><span class="ev-cat">{CAT_LABEL[cat]} · QSearch 筆試第 6 題</span><span class="ev-kind {kind_cls}">{esc(kind)}</span></div>
  <h1 class="ev-claim">{esc(claim)}</h1>
  <dl class="ev-src">{rows}</dl>
  {body}
  <div class="ev-foot"><span>{foot_left}</span><span>q6/{out}</span></div>
</div></body></html>"""
    (CARDS / f"{cid}.html").write_text(doc, encoding="utf-8")
    manifest.append({"id": cid, "out": out})


def img_tag(name, css_w):
    return f'<img src="../crops/{esc(name)}" style="width:{min(css_w, 1216)}px">'


def natural_css(name, dsf):
    w, h = Image.open(W / "crops" / name).size
    return round(w / dsf), round(h / dsf)


# ---------- web evidence ----------
cfg = json.loads((W / "evidence_web.json").read_text(encoding="utf-8"))
meta = json.loads((W / "web_meta.json").read_text(encoding="utf-8"))
for ev in cfg:
    m = meta[ev["id"]]
    url = ev["url"]
    if "elastic.co" in url:
        kind, kc = "官方文件", "k-doc"
    elif "cloud.google.com" in url:
        kind, kc = "官方案例", "k-doc"
    elif "/blob/" in url:
        kind, kc = "原始碼", "k-code"
    else:
        kind, kc = "專案 README", "k-code"
    figs = []
    for i, c in enumerate(m["crops"], 1):
        if ev.get("keep") and i not in ev["keep"]:
            continue
        figs.append(f'<figure>{img_tag(c["file"], c["cssW"])}<figcaption>節錄 {i}：頁面中含 <code>{esc(c["find"])}</code> 的段落（黃框）</figcaption></figure>')
    when = m["capturedAt"].replace("T", " ").replace("Z", " UTC")
    page(
        ev["id"], kind, kc, ev["claim"],
        [("來源", f'{esc(ev["source"])}<br><span class="url">{esc(m.get("finalUrl") or url)}</span>'),
         ("擷取", f'{esc(when)} · HTTP {m.get("status")} · Playwright（Chromium 無頭、1280×900 視窗）在本研究沙盒 VM 擷取 · 黃框為標示的重點')],
        "\n".join(figs),
        "截圖腳本 q6/evidence/src/capture_web.js · 設定 q6/evidence/src/evidence_web.json",
    )

# ---------- paper (ROCLING 2022) ----------
PAPER = "https://aclanthology.org/2022.rocling-1.24.pdf"
paper_src = [
    ("來源", f'Yeh, Hsieh, Chang, Hsu（2022）。正體中文斷詞系統應用於大型語料庫之多方評估研究。ROCLING 2022，頁 193–199<br><span class="url">{PAPER}</span>'),
    ("擷取", "PDF 於 2026-10-29 下載（sha256 前 16 碼 79cb1d637b1daa6e），以 pdftoppm 200 dpi 轉成圖片後依 pdftotext 座標裁切；卡片由 Playwright 輸出"),
]


def paper_fig(name, cap):
    w, _ = natural_css(name, 1.5)
    return f'<figure>{img_tag(name, w)}<figcaption>{cap}</figcaption></figure>'


page(
    "ev_a8_rocling_runtime", "論文", "k-paper",
    "同一台機器斷 4 萬句（每句 200 字）：jieba 約 45 秒、CKIP 約 540 秒（圖 3 讀值）。jieba 快一個數量級，回朔 5 億篇用字典式斷詞，CKIP 留給離線工作。",
    paper_src,
    '<div class="grid2"><div>'
    + paper_fig("rocling_p194_hardware_versions.png", "p.194：硬體（4 × AMD EPYC 7252、7 × RTX 3090）與工具版本（jieba 0.42.1、CKIP 0.2.1、MONPA 0.3.3）")
    + paper_fig("rocling_p195_method_40000.png", "p.195：大資料集測試，每句 200 字、5,000～40,000 句")
    + "</div><div>"
    + paper_fig("rocling_p196_fig3_conclusion.png", "p.196：圖 3 大資料集斷詞耗費時間，與 3.3 節結論「Jieba 確實是最快速的斷詞工具」")
    + "</div></div>",
    "數字為圖 3 讀值（40,000 句處），屬近似值",
)
page(
    "ev_b4_rocling_f1", "論文", "k-paper",
    "SIGHAN AS 資料集斷詞 F1：CKIP 97.68–98.22，匯入正體字典的 JiebaD 只有 73.87–76.52。品質要靠清洗、台灣詞庫與過濾來補，CKIP 適合離線做新詞發現與預標。",
    paper_src,
    paper_fig("rocling_p197_table1_f1.png", "p.197：表 1，各工具在 AS 與 City U 資料集的斷詞 F1")
    + paper_fig("rocling_p197_jieba_note.png", "p.197：論文說明 Jieba 雖匯入正體中文字典檔，斷詞效果依然沒有太大提升"),
    "JiebaD = jieba 0.42.1 匯入 4 MB 正體中文字典",
)

# ---------- VM measurements ----------
log_lines = (EXP / "verify.log").read_text(encoding="utf-8").splitlines()
sha_lines = (EXP / "results.sha256").read_text(encoding="utf-8").splitlines()
r1 = json.loads((EXP / "results.json").read_text(encoding="utf-8"))
r2 = json.loads((EXP / "results2.json").read_text(encoding="utf-8"))
r3 = json.loads((EXP / "results3.json").read_text(encoding="utf-8"))


def term(lines, hl_keys):
    out = []
    for ln in lines:
        e = esc(ln)
        if any(k in ln for k in hl_keys):
            out.append(f'<span class="hl">{e}</span>')
        elif ln.startswith("$"):
            out.append(f'<span class="dim">{e}</span>')
        else:
            out.append(e)
    return '<pre class="term">' + "\n".join(out) + "</pre>"


vm_src = [
    ("來源", "本研究沙盒 VM 實測（Linux、Python 3.13.17、jieba 0.42.1、Intel Xeon 2.1GHz、單一行程）<br>語料：PTT 八卦板問答 <span class=\"url\">github.com/zake7749/Gossiping-Chinese-Corpus</span>（Gossiping-QA-Dataset.txt，418,202 筆）"),
    ("執行", "2026-10-29 17:38–17:39 UTC 重跑 <code>wordcloud_q6_verify.py</code>；結果檔 sha256：" + "；".join(esc(s.split()[1] + " " + s.split()[0][:16]) for s in sha_lines)),
]

tp = r1["throughput"]
rows = "".join(
    f'<tr><td>{d}</td><td>{"開" if h else "關"}</td><td class="n">{tp[k]["seconds"]:.2f}</td><td class="n">{tp[k]["chars_per_sec"]:,}</td></tr>'
    for d, h, k in [("預設 dict.txt", True, "default_dict_hmm_True"), ("預設 dict.txt", False, "default_dict_hmm_False"),
                    ("dict.txt.big", True, "dict_big_hmm_True"), ("dict.txt.big", False, "dict_big_hmm_False")]
)
page(
    "ev_a9_vm_throughput", "VM 實測", "k-run",
    "jieba（dict.txt.big＋HMM）在本研究 VM 每核每秒約 26–29 萬字。5 億篇 × 平均 300 字約 146 核心小時，64 核心約 2.3 小時：斷詞運算不是瓶頸。",
    vm_src,
    term(log_lines[:5], ["吞吐"])
    + '<h2 class="sec">第一次量測：前 10 萬篇、2,902,513 字，單一行程</h2>'
    + f'<table class="t"><tr><th>字典</th><th>HMM</th><th>秒</th><th>字／秒</th></tr>{rows}</table>',
    "實驗腳本 q6/code/jieba_experiments/wordcloud_q6_exp1_measure.py、wordcloud_q6_verify.py",
)

pt = r1["place_tests"]
place_rows = "".join(
    f'<tr><td>{esc(s)}</td><td class="mono">{esc(v["default_dict"])}</td><td class="mono">{esc(v["dict_big"])}</td><td class="mono">{esc(v["dict_big_no_hmm"])}</td></tr>'
    for s, v in pt.items()
)
gran_rows = "".join(
    f'<tr><td>{esc(s)}</td><td class="mono">{esc(v["precise"])}</td><td class="mono">{esc(v["search"])}</td></tr>'
    for s, v in r3["granularity"].items()
)
page(
    "ev_b6_vm_dict_coverage", "VM 實測", "k-run",
    "開源的 dict.txt.big 沒收台積電、蝦皮購物、臺北車站、瑞豐夜市等台灣實體，所以要自建台灣詞庫；同一份字典可切出精確（文字雲）與搜尋（召回）兩種粒度。",
    vm_src,
    term([log_lines[0], log_lines[1], log_lines[5]], ["是否收錄"])
    + '<h2 class="sec">地名：三種設定的切法</h2>'
    + f'<table class="t"><tr><th>句子</th><th>預設字典</th><th>大字典</th><th>大字典、關 HMM</th></tr>{place_rows}</table>'
    + '<h2 class="sec">同一份字典（大字典＋自訂詞），兩種粒度</h2>'
    + f'<table class="t"><tr><th>句子</th><th>精確模式（文字雲）</th><th>搜尋模式（召回）</th></tr>{gran_rows}</table>',
    "實驗腳本 q6/code/jieba_experiments/（exp1、exp3、verify）",
)

names = ["新增 1 個詞（肥宅）", "新增 1 個詞（肥宅）", "一次發版 6 個詞", "一次發版 6 個詞", "1 個詞給極端頻率 1e7", "1 個詞給極端頻率 1e7"]
br_rows = "".join(
    f'<tr class="{"pin" if "固定" in c["label"] else ""}"><td>{names[i]}</td><td>{"固定" if "固定" in c["label"] else "預設累加"}</td>'
    f'<td class="n">{c["changed_containing"]:,}</td><td class="n"><span class="{"good" if c["changed_NOT_containing"] == 0 else "bad"}">{c["changed_NOT_containing"]:,}</span></td></tr>'
    for i, c in enumerate(r2["cases"])
)
hot = r2["hot_vs_fresh"]
page(
    "ev_c3_vm_blast_radius", "VM 實測", "k-run",
    "加一個新詞時，total 照 jieba 預設累加，20 萬篇裡有 52 篇不含該詞也改變；把 total 固定成字典版本的常數後是 0 篇。影響範圍只有在 total 固定時才能精確圈出。",
    vm_src,
    term([log_lines[0], log_lines[1], log_lines[3], log_lines[6], log_lines[7], log_lines[8]], ["新增「肥宅」"])
    + '<h2 class="sec">六種情境（前 20 萬篇）</h2>'
    + f'<table class="t"><tr><th>情境</th><th>total</th><th>含新詞且改變</th><th>不含新詞卻改變</th></tr>{br_rows}</table>'
    + '<h2 class="sec">常駐 worker 逐步 add_word vs 批次 job 整份重載</h2>'
    + f'<table class="t"><tr><th>載入方式</th><th>total</th><th>說明</th></tr>'
    + f'<tr><td>常駐 worker 逐步 add_word（含把「{esc(hot["bump"]["word"])}」頻率從 {hot["bump"]["old_freq"]} 調到 {hot["bump"]["new_freq"]}）</td><td class="n">{hot["hot_total"]:,}</td><td>覆寫時舊頻率沒扣回</td></tr>'
    + f'<tr><td>批次 job 整份載入合併後的字典檔</td><td class="n">{hot["fresh_total"]:,}</td><td>相差 {hot["hot_total"] - hot["fresh_total"]:,}；這次 20 萬篇剛好 {hot["posts_differ"]} 篇不同，但沒有一致性保證</td></tr></table>',
    "實驗腳本 q6/code/jieba_experiments/wordcloud_q6_exp2_pinned_total.py、wordcloud_q6_verify.py",
)

# ---------- research page ----------
w1, _ = natural_css("ev_00_research_page_1.png", 1.5)
w2, _ = natural_css("ev_00_research_page_2.png", 1.5)
page(
    "ev_00_research_page", "研究頁", "k-page",
    "研究頁：五個症狀對應到修法與閘門；斷詞實驗台的 v1／v2 是 jieba 的實際輸出（示意貼文為自寫）。",
    [("來源", '研究頁 QSearch Q6 文字雲斷詞（線上版）<br><span class="url">https://claude.ai/artifact/BFHkeqUYvKUwzkkWDkMYUU</span>'),
     ("擷取", "同一份頁面原始檔（q6/answer/research-page.html）在沙盒 VM 以 Playwright 渲染後截圖，淺色主題、1280 寬")],
    f'<figure>{img_tag("ev_00_research_page_1.png", w1)}<figcaption>總覽：策略、四個關鍵數字、五個症狀對照表</figcaption></figure>'
    f'<figure>{img_tag("ev_00_research_page_2.png", w2)}<figcaption>實測：斷詞實驗台（第一則示意貼文）</figcaption></figure>',
    "頁面原始檔 q6/answer/research-page.html",
)

(CARDS / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print(len(manifest), "cards")
