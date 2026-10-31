#!/usr/bin/env python3
"""從實測資料產生第 3 題各小題的圖表 HTML（每頁一張 .card），再由 capture.js 轉成 PNG。

資料來源：
    q3/data/ptt_q3_out/index_entries.csv        7 天列表頁全抓（10/19–10/26）
    q3/data/ptt_q3_census_out/articles.jsonl    6,237 篇文章全數普查

用法（在 repo 根目錄）：
    python q3/answer/src/make_charts.py      # 輸出到 q3/answer/<小題>/chart-*.html，與 PNG 放在一起
"""
import csv
import json
import statistics
from collections import Counter
from datetime import datetime, timezone, timedelta
from html import escape
from pathlib import Path

TW = timezone(timedelta(hours=8))
WINDOW_END = datetime(2026, 10, 26, 3, 36, tzinfo=TW)
WINDOW_START = WINDOW_END - timedelta(days=7)
ROOT = Path(__file__).resolve().parents[3]
Q3 = ROOT / "q3"
ANSWER = Q3 / "answer"
CHART_DIRS = {"1": "1-scale", "3": "3-cost", "4": "4-invariant", "5": "5-not-monitored",
              "6": "6-spec-change", "7": "7-question"}

# 參考色盤（淺色）：dataviz skill references/palette.md
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
BLUE_LIGHT = "#b7d3f6"

CSS = f"""
* {{ box-sizing: border-box; margin: 0; }}
body {{ background: #f9f9f7; font-family: "WenQuanYi Zen Hei", system-ui, sans-serif; color: {INK}; padding: 24px; }}
.card {{ background: {SURFACE}; border: 1px solid rgba(11,11,11,.10); border-radius: 12px; padding: 28px 32px 22px; width: 960px; }}
.tag {{ display: inline-block; font-size: 13px; color: {INK2}; border: 1px solid {GRID}; border-radius: 99px; padding: 2px 10px; margin-bottom: 10px; }}
h1 {{ font-size: 24px; font-weight: 600; line-height: 1.35; }}
.sub {{ font-size: 15px; color: {INK2}; margin-top: 6px; line-height: 1.5; }}
.src {{ font-size: 12.5px; color: {MUTED}; margin-top: 14px; line-height: 1.5; }}
svg text {{ font-family: "WenQuanYi Zen Hei", system-ui, sans-serif; }}
.tiles {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-top: 20px; }}
.tile {{ border: 1px solid {GRID}; border-radius: 10px; padding: 16px 18px; }}
.tile .v {{ font-size: 32px; font-weight: 600; margin-top: 6px; }}
.tile .k {{ font-size: 14px; color: {INK2}; }}
.tile .n {{ font-size: 13px; color: {MUTED}; margin-top: 6px; line-height: 1.45; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 18px; font-size: 16px; }}
th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid {GRID}; }}
th {{ color: {INK2}; font-weight: 500; font-size: 14px; }}
td.num {{ font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }}
th.num {{ text-align: right; }}
"""


def page(name, tag, title, sub, body, src):
    html = (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><style>{CSS}</style></head>'
            f'<body><div class="card"><div class="tag">{escape(tag)}</div><h1>{escape(title)}</h1>'
            f'<div class="sub">{sub}</div>{body}<div class="src">{src}</div></div></body></html>')
    num, rest = name.split("-", 1)
    folder = ANSWER / CHART_DIRS[num]
    folder.mkdir(exist_ok=True)
    (folder / f"chart-{rest}.html").write_text(html, encoding="utf-8")


def bar_path(x, y, w, h, r=4):
    """上端圓角、底部貼齊基線的長條。"""
    if h <= 0:
        return ""
    r = min(r, w / 2, h)
    return (f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z")


def hbar_path(x, y, w, h, r=4):
    """右端圓角、左端貼齊基線的橫條。"""
    if w <= 0:
        return ""
    r = min(r, h / 2, w)
    return (f"M{x:.1f},{y:.1f} H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
            f"V{y + h - r:.1f} Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f} H{x:.1f} Z")


def vbar_chart(labels, values, colors, value_labels, width=896, height=330, ymax=None, ticks=5,
               note_labels=None, label_size=14, unit=""):
    left, right, top, bottom = 54, 8, 24, 46
    pw, ph = width - left - right, height - top - bottom
    ymax = ymax or max(values) * 1.12
    step = nice_step(ymax / ticks)
    ymax = step * ticks
    n = len(values)
    slot = pw / n
    bw = slot - 2 if n > 12 else min(slot * 0.62, 90)
    out = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    for i in range(ticks + 1):
        v = step * i
        y = top + ph - ph * v / ymax
        out.append(f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID if i else BASE}" stroke-width="1"/>')
        out.append(f'<text x="{left - 8}" y="{y + 4:.1f}" font-size="12" fill="{MUTED}" text-anchor="end">{v:,.0f}{unit}</text>')
    for i, (lab, v, c) in enumerate(zip(labels, values, colors)):
        x = left + slot * i + (slot - bw) / 2
        h = ph * v / ymax
        y = top + ph - h
        out.append(f'<path d="{bar_path(x, y, bw, h)}" fill="{c}"/>')
        if value_labels[i]:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{y - 7:.1f}" font-size="{label_size}" fill="{INK}" text-anchor="middle">{value_labels[i]}</text>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{top + ph + 20:.1f}" font-size="{12 if n > 12 else 14}" fill="{INK2}" text-anchor="middle">{lab}</text>')
        if note_labels and note_labels[i]:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{top + ph + 38:.1f}" font-size="12" fill="{MUTED}" text-anchor="middle">{note_labels[i]}</text>')
    out.append("</svg>")
    return "".join(out)


def nice_step(raw):
    """不小於 raw 的最小「漂亮」刻度（1、2、2.5、5 × 10^n）。"""
    cands = [m * 10 ** e for e in range(-3, 8) for m in (1, 2, 2.5, 5)]
    return min(c for c in cands if c >= raw)


def load():
    index = [r for r in csv.DictReader(open(Q3 / "data/ptt_q3_out/index_entries.csv", encoding="utf-8-sig"))]
    census = {}
    for line in open(Q3 / "data/ptt_q3_census_out/articles.jsonl", encoding="utf-8"):
        rec = json.loads(line)
        census[rec["aid"]] = rec
    return index, list(census.values())


def main():
    index, census = load()
    window = [r for r in index if r["ts"] and int(r["ts"]) >= WINDOW_START.timestamp()]
    ok = [r for r in census if r.get("status") == 200 and "comments" in r]
    src_index = "資料：2026/10/26 實爬八卦板 7 天全部 334 個列表頁（q3/data/ptt_q3_out/index_entries.csv）"
    src_census = "資料：2026/10/27 打開 7 天內全部 6,237 篇文章普查（q3/data/ptt_q3_census_out/articles.jsonl）"

    # ── 1. 每日發文量 ────────────────────────────────────────────────────
    days = Counter(datetime.fromtimestamp(int(r["ts"]), TW).date() for r in window)
    ds = sorted(days)
    full = ds[1:-1]
    avg = sum(days[d] for d in full) / len(full)
    labels = [d.strftime("%m/%d") + "（" + "一二三四五六日"[d.weekday()] + "）" for d in ds]
    vals = [days[d] for d in ds]
    colors = [BLUE if d in full else BLUE_LIGHT for d in ds]
    notes = ["不完整" if d not in full else "" for d in ds]
    page("1-daily-posts", "第 3 題・第 1 小題｜規模估算",
         f"八卦板每天約 {avg:,.0f} 篇新文章",
         f"6 個完整日平均 {avg:,.0f} 篇（最少 {min(days[d] for d in full):,}、最多 {max(days[d] for d in full):,}）；"
         "頭尾兩天只涵蓋部分時段，不列入平均",
         vbar_chart(labels, vals, colors, [f"{v:,}" for v in vals], note_labels=notes), src_index)

    # ── 2. 每小時發文量 ──────────────────────────────────────────────────
    hours = Counter(datetime.fromtimestamp(int(r["ts"]), TW).hour for r in window
                    if datetime.fromtimestamp(int(r["ts"]), TW).date() in full)
    hv = [hours.get(h, 0) / len(full) for h in range(24)]
    peak = max(range(24), key=lambda h: hv[h])
    low = min(range(24), key=lambda h: hv[h])
    vl = [f"{v:.0f}" if h in (peak, low) else "" for h, v in enumerate(hv)]
    page("1-hourly-posts", "第 3 題・第 1 小題｜規模估算",
         f"尖峰在 {peak} 點，每小時約 {hv[peak]:.0f} 篇；清晨 {low} 點最少",
         "每小時平均發文數（6 個完整日平均）。尖峰小時的抓取量 ≈ 64 篇 × 7 批 ≈ 450 次，以每秒 1 次約 7.5 分鐘",
         vbar_chart([f"{h:02d}" for h in range(24)], hv, [BLUE] * 24, vl), src_index)

    # ── 3. 規模估算總表 ──────────────────────────────────────────────────
    comments = sum(r["comments"] for r in ok)
    body_b = sum(r["body_bytes"] for r in ok)
    cm_b = sum(r["tag_bytes"] + r["user_bytes"] + r["content_bytes"] + r["time_bytes"] for r in ok)
    html_b = sum(r["html_bytes"] for r in ok)
    rows = [
        ("每日新文章", f"{avg:,.0f} 篇", "6 個完整日平均（列表頁全抓）"),
        ("7 天文章", f"≈ {avg * 7:,.0f} 篇", f"{avg:,.0f} × 7（被刪文章未計，誤差 < 7%）"),
        ("7 天留言", f"{comments:,} 則", f"{len(ok):,} 篇全數打開數出；每篇平均 {comments / len(ok):.1f}、中位數 {statistics.median(r['comments'] for r in ok):.0f}"),
        ("純文字", f"{(body_b + cm_b) / 1e6:.1f} MB", f"內文 {body_b / 1e6:.1f} MB + 留言 {cm_b / 1e6:.1f} MB（每則 {cm_b / comments:.1f} B 實測）"),
        ("原始 HTML", f"{html_b / 1e6:.1f} MB", f"每篇平均 {html_b / len(ok) / 1024:.1f} KB"),
        ("每日請求", "≈ 6,300 次", "新文章 890 + 重抓 890 × 6 + 列表頁 70 = 0.073 次/秒"),
    ]
    body = "<table><tr><th>項目</th><th class='num'>數值</th><th>怎麼來的</th></tr>" + "".join(
        f"<tr><td>{a}</td><td class='num'><b>{b}</b></td><td>{c}</td></tr>" for a, b, c in rows) + "</table>"
    page("1-scale-table", "第 3 題・第 1 小題｜規模估算", "規模估算總表：全部是實測值",
         "文章數來自列表頁全抓，留言與文字量來自 6,237 篇全數普查，不是抽樣推估", body,
         src_index + "<br>" + src_census)

    # ── 4. 文字量組成（附問題用：留言佔多少） ─────────────────────────────
    total = body_b + cm_b
    w = 896
    bw_body = w * body_b / total
    svg = (f'<svg width="{w}" height="120" viewBox="0 0 {w} 120">'
           f'<path d="{hbar_path(0, 20, bw_body - 1, 44, 0)}" fill="{BLUE}"/>'
           f'<path d="{hbar_path(bw_body + 1, 20, w - bw_body - 1, 44)}" fill="{ORANGE}"/>'
           f'<text x="10" y="48" font-size="16" fill="#fff">內文 {body_b / 1e6:.1f} MB</text>'
           f'<text x="{bw_body + 12:.0f}" y="48" font-size="16" fill="#fff">留言 {cm_b / 1e6:.1f} MB（{cm_b / total * 100:.0f}%）</text>'
           f'<rect x="0" y="84" width="14" height="14" rx="3" fill="{BLUE}"/>'
           f'<text x="22" y="96" font-size="14" fill="{INK2}">內文：{len(ok):,} 篇 × 平均 {body_b / len(ok):,.0f} B</text>'
           f'<rect x="420" y="84" width="14" height="14" rx="3" fill="{ORANGE}"/>'
           f'<text x="442" y="96" font-size="14" fill="{INK2}">留言：{comments:,} 則 × 平均 {cm_b / comments:.1f} B</text>'
           "</svg>")
    page("7-text-share", "第 3 題・動工前的問題", f"7 天純文字 {total / 1e6:.1f} MB，其中 {cm_b / total * 100:.0f}% 是留言",
         "所以「關鍵字要不要比對留言」決定了要索引的文字量差 3.5 倍，這是動工前最值得問的一題", svg, src_census)

    # ── 5. 兩條規格的成本 ────────────────────────────────────────────────
    spec1 = round(avg) + 70
    spec2 = round(avg) * 6
    page("3-spec-cost", "第 3 題・第 3 小題｜哪條規格主導成本",
         f"規格 2（每天更新留言）的請求數是規格 1 的 {spec2 / spec1:.1f} 倍",
         f"規格 1：新文章首抓 {round(avg):,} + 列表頁 70 = {spec1:,} 次/天；規格 2：每篇在第 1–6 天各重抓一次 = {round(avg):,} × 6 = {spec2:,} 次/天",
         vbar_chart(["規格 1：6 小時內入庫", "規格 2：7 天內每天更新留言"], [spec1, spec2], [BLUE, ORANGE],
                    [f"{spec1:,} 次/天", f"{spec2:,} 次/天"], height=300, label_size=16, ymax=6000, ticks=3),
         src_index + "；把「6 小時」收緊到 1 小時只多約 20 次列表請求（+2%）")

    # ── 6. 留言抵達時間（累積） ──────────────────────────────────────────
    sub3 = [r for r in ok if (r["fetched_at"] - r["ts"]) / 3600 >= 72]
    delays = sorted(d for r in sub3 for d in r["delays_min"])
    n = len(delays)
    pts = []
    j = 0
    xs = [i / 4 for i in range(0, 8 * 4)] + list(range(8, 73))
    for x in xs:
        while j < n and delays[j] <= x * 60:
            j += 1
        pts.append((x, j / n * 100))
    width, height, left, right, top, bottom = 896, 330, 54, 16, 30, 42
    pw, ph = width - left - right, height - top - bottom
    X = lambda h: left + pw * h / 72
    Y = lambda p: top + ph - ph * p / 100
    o = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    for p in range(0, 101, 20):
        o.append(f'<line x1="{left}" x2="{width - right}" y1="{Y(p):.1f}" y2="{Y(p):.1f}" stroke="{GRID if p else BASE}"/>')
        o.append(f'<text x="{left - 8}" y="{Y(p) + 4:.1f}" font-size="12" fill="{MUTED}" text-anchor="end">{p}%</text>')
    for h in (0, 6, 12, 24, 36, 48, 60, 72):
        o.append(f'<text x="{X(h):.1f}" y="{top + ph + 20}" font-size="12" fill="{MUTED}" text-anchor="middle">{h}h</text>')
    o.append('<polyline fill="none" stroke="%s" stroke-width="2" points="%s"/>' % (
        BLUE, " ".join(f"{X(x):.1f},{Y(p):.1f}" for x, p in pts)))
    for h, dx, dy, anchor in ((6, 10, 26, "start"), (24, 10, 30, "start")):
        p = sum(d <= h * 60 for d in delays) / n * 100
        o.append(f'<circle cx="{X(h):.1f}" cy="{Y(p):.1f}" r="5" fill="{BLUE}" stroke="{SURFACE}" stroke-width="2"/>')
        o.append(f'<text x="{X(h) + dx:.1f}" y="{Y(p) + dy:.1f}" font-size="15" fill="{INK}" text-anchor="{anchor}">{h} 小時內 {p:.2f}%</text>')
    o.append(f'<text x="{left + pw / 2:.1f}" y="{height - 4}" font-size="12" fill="{INK2}" text-anchor="middle">發文後經過時間</text>')
    o.append("</svg>")
    p24 = sum(d <= 1440 for d in delays) / n * 100
    page("3-comment-arrival", "第 3 題・第 3 小題｜哪條規格主導成本",
         f"留言有 {p24:.1f}% 在發文 24 小時內就進來",
         f"每天重抓的成本集中在第 2–7 天，但那段時間只會多出 {100 - p24:.1f}% 的留言（{len(sub3):,} 篇發文已滿 3 天的文章、{n:,} 則留言）",
         "".join(o), src_census)

    # ── 7. 每次重抓多抓到的留言 ─────────────────────────────────────────
    sub7 = [r for r in ok if (r["fetched_at"] - r["ts"]) / 3600 >= 168]
    d7 = [d for r in sub7 for d in r["delays_min"]]
    n7 = len(d7)
    edges = [(0, 60, "首抓\n（1 小時內）")] + [((k - 1) * 1440 if k > 1 else 60, k * 1440, f"第 {k} 次重抓\n（第 {k} 天）") for k in range(1, 7)]
    shares = [sum(lo < d <= hi if lo else d <= hi for d in d7) / n7 * 100 for lo, hi, _ in edges]
    labels = [e[2].split("\n")[0] for e in edges]
    notes = [e[2].split("\n")[1] for e in edges]
    vl = [f"{s:.1f}%" if s >= 1 else f"{s:.2f}%" for s in shares]
    cols = [BLUE] + [ORANGE] + [ORANGE] * 5
    page("6-refresh-marginal", "第 3 題・第 6 小題｜規格修改",
         "每次重抓都花 890 個請求，但第 2 次以後幾乎抓不到新留言",
         f"把每則留言歸到「第一次被哪一次抓取看到」：首抓 + 第 1 次重抓已涵蓋 {shares[0] + shares[1]:.2f}%，"
         f"第 2–6 次重抓合計只多 {sum(shares[2:]):.2f}%（{len(sub7):,} 篇發文已滿 7 天的文章、{n7:,} 則留言）",
         vbar_chart(labels, shares, cols, vl, ymax=80, ticks=4, note_labels=notes, height=340, unit="%"),
         src_census + "；首抓假設在發文後 1 小時內（每小時檢查一次）")

    # ── 8. 不變量實測結果 ────────────────────────────────────────────────
    pages = {}
    for r in index:
        pages.setdefault(int(r["page"]), []).append(r)
    newest = max(pages)
    full_pages = [p for p in pages if p != newest]
    good_pages = sum(len(pages[p]) == 20 for p in full_pages)
    seq = [int(r["ts"]) for p in sorted(pages) for r in pages[p] if r["ts"]]
    inv = sum(b < a for a, b in zip(seq, seq[1:]))

    def disp(x):
        return 100 if x >= 100 else -100 if x <= -100 else -((-x) // 10) * 10 if x <= -10 else max(x, 0)
    old = [r for r in ok if WINDOW_END.timestamp() - r["ts"] >= 3 * 86400]
    match = sum(disp(r["push"] - r["boo"]) == r["nrec"] for r in old)
    near = sum(abs(disp(r["push"] - r["boo"]) - r["nrec"]) <= 2 for r in old)
    tiles = (
        f'<div class="tiles">'
        f'<div class="tile"><div class="k">填滿的列表頁剛好 20 格</div><div class="v">{good_pages}/{len(full_pages)}</div>'
        f'<div class="n">刪文也佔一格，所以「每頁 20 格」可以定位到第幾頁第幾格漏抓</div></div>'
        f'<div class="tile"><div class="k">列表順序與 ID 時間戳倒序</div><div class="v">{inv} 筆</div>'
        f'<div class="n">{len(seq):,} 筆相鄰比較；ID 內含發文時間，順序必須單調遞增</div></div>'
        f'<div class="tile"><div class="k">列表推文數 = 推 − 噓</div><div class="v">{match / len(old) * 100:.1f}%</div>'
        f'<div class="n">{len(old):,} 篇穩定文章；差距 ≤ 2 的有 {near / len(old) * 100:.1f}%，適合當容許誤差的輔助檢查</div></div>'
        f'</div>')
    page("4-invariant-results", "第 3 題・第 4 小題｜不變量", "三個不變量都在實測資料上驗證過",
         "前兩個是硬性檢查（違反就是漏抓或解析錯）；第三個是軟性檢查（±2 容許誤差）", tiles,
         src_index + "<br>" + src_census)

    # ── 9. 抓取過程（不監控單次延遲的依據） ─────────────────────────────
    got = census
    lat = sorted(r["latency"] for r in ok)
    p95 = statistics.quantiles(lat, n=20)[-1]
    st = Counter(r["status"] for r in got)
    warn = sum(r.get("warn_rows", 0) > 0 for r in ok)
    # 普查每篇只記最後狀態；失敗一次會先退避 5 秒再重試，所以回應時間 ≥5 秒的文章可能各多送 1 個請求
    failed = sum(r["status"] not in (200, 404) for r in got)
    slow = [r["latency"] for r in got if r["latency"] >= 5]
    retry_note = (f"；另有 {len(slow)} 篇回應時間 {min(slow):.1f}–{max(slow):.1f} 秒，"
                  f"可能是第一次失敗、退避 5 秒後重試成功" if slow else "")
    tiles = (
        f'<div class="tiles">'
        f'<div class="tile"><div class="k">最後仍抓不到的文章</div><div class="v">{failed} / {len(got):,} 篇</div>'
        f'<div class="n">200 有 {st[200]:,} 篇、404（已刪文）{st[404]} 篇{retry_note}</div></div>'
        f'<div class="tile"><div class="k">回應時間 P95</div><div class="v">{p95:.2f} 秒</div>'
        f'<div class="n">中位數 {statistics.median(lat):.2f} 秒；每天的抓取只需約 1.75 小時，有十幾倍餘裕</div></div>'
        f'<div class="tile"><div class="k">「檔案過大」截斷的文章</div><div class="v">{warn} 篇</div>'
        f'<div class="n">佔 {warn / len(ok) * 100:.2f}%；網頁版抓不到完整留言，列為已知且接受的風險</div></div>'
        f'</div>')
    page("5-crawl-stats", "第 3 題・第 5 小題｜不監控的事", "單次請求的延遲與失敗不值得告警",
         (f"{len(got):,} 篇文章最後全部拿到 200 或 404，中途若有失敗也由退避重試補回" if not failed
          else f"{len(got):,} 篇文章最後有 {failed:,} 篇仍然失敗") + "；只監控結果（入庫延遲、job 失敗、不變量），不監控過程",
         tiles, src_census)
    # ── 給 capture.js 的截圖目標：被刪的文章、被截斷的文章 ───────────────
    titles = {r["aid"]: r["title"] for r in index if r["aid"]}
    targets = {
        "deleted": [{"aid": r["aid"], "ts": r["ts"], "title": titles.get(r["aid"], "")}
                    for r in got if r.get("status") == 404],
        "truncated": [{"aid": r["aid"], "ts": r["ts"], "title": titles.get(r["aid"], ""), "comments": r["comments"]}
                      for r in ok if r.get("warn_rows", 0) > 0],
        "deleted_total": st[404],
        "articles_total": len(got),
    }
    (ANSWER / "src/ptt_targets.json").write_text(json.dumps(targets, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已輸出 {len(list(ANSWER.glob('*/chart-*.html')))} 個圖表 HTML 與 q3/answer/src/ptt_targets.json")


if __name__ == "__main__":
    main()
