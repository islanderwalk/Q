"""產生 Q3 專用 Grafana 看板 grafana/dashboards/q3-ptt-search.json。改看板就改這支再重跑：python monitoring/build_dashboard.py

版面依答案的結構排：
  1. 兩條資料規格與結果（會告警）  2. 資料量  3. 抓取（每秒最多 1 次、每日請求數對照估算）
  4. 寫入與自動 rollback  5. 重抓的邊際效益（答案第 3、6 題）  6. 只記錄、不告警（答案第 5 題）  7. 不變量與查詢 API
"""
import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "q3_prom"}
OUT = Path(__file__).parent / "grafana" / "dashboards" / "q3-ptt-search.json"
GREEN, ORANGE, RED, BLUE, GREY = "#2e9e5b", "#e8833a", "#d64545", "#3d7bd9", "#8a8f98"

panels = []
_id = [0]
_y = [0]


def nid():
    _id[0] += 1
    return _id[0]


def row(title):
    panels.append({"type": "row", "title": title, "id": nid(), "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": _y[0]}, "panels": []})
    _y[0] += 1


def target(expr, legend="", instant=False, ref="A"):
    t = {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref, "range": not instant, "instant": instant}
    return t


def thresholds(*steps):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in steps]}


def stat(title, exprs, x, w, h=5, unit="none", steps=((None, BLUE),), desc="", mappings=None, decimals=None,
         text_mode="value", color_mode="background", graph="none", no_value=None, instant=False):
    fc = {"defaults": {"unit": unit, "thresholds": thresholds(*steps), "color": {"mode": "thresholds"}}, "overrides": []}
    if no_value:
        fc["defaults"]["noValue"] = no_value
    if mappings:
        fc["defaults"]["mappings"] = mappings
    if decimals is not None:
        fc["defaults"]["decimals"] = decimals
    p = {"type": "stat", "title": title, "id": nid(), "datasource": DS, "description": desc,
         "gridPos": {"h": h, "w": w, "x": x, "y": _y[0]},
         "targets": [target(e, lg, instant=instant, ref=chr(65 + i)) for i, (e, lg) in enumerate(exprs)],
         "fieldConfig": fc,
         "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                     "colorMode": color_mode, "graphMode": graph, "textMode": text_mode, "justifyMode": "center",
                     "orientation": "auto", "wideLayout": True, "showPercentChange": False}}
    panels.append(p)
    return p


def ts(title, exprs, x, w, h=8, unit="none", desc="", line=None, stack=False, bars=False, colors=None, decimals=None,
       no_value=None):
    defaults = {"unit": unit, "color": {"mode": "palette-classic"},
                "custom": {"drawStyle": "bars" if bars else "line", "lineWidth": 2, "fillOpacity": 60 if bars else 12,
                           "showPoints": "never", "spanNulls": True, "stacking": {"mode": "normal" if stack else "none"},
                           "thresholdsStyle": {"mode": "line+area" if line else "off"}, "axisSoftMin": 0}}
    if decimals is not None:
        defaults["decimals"] = decimals
    # 沒有資料時顯示的說明（取代 No data）。時間序列設了 decimals=0 時，Grafana 會把
    # 非整數的座標刻度也當成「無值」而印出這段說明，所以這種組合不設 noValue。
    if no_value and decimals is None:
        defaults["noValue"] = no_value
    if line:
        value, label = line
        defaults["thresholds"] = thresholds((None, "transparent"), (value, RED))
    overrides = []
    for name, col in (colors or {}).items():
        overrides.append({"matcher": {"id": "byName", "options": name},
                          "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": col}}]})
    p = {"type": "timeseries", "title": title, "id": nid(), "datasource": DS, "description": desc,
         "gridPos": {"h": h, "w": w, "x": x, "y": _y[0]},
         "targets": [target(e, lg, ref=chr(65 + i)) for i, (e, lg) in enumerate(exprs)],
         "fieldConfig": {"defaults": defaults, "overrides": overrides},
         "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                     "tooltip": {"mode": "multi", "sort": "desc"}}}
    panels.append(p)
    return p


def bargauge(title, exprs, x, w, h=8, unit="none", desc="", instant=True, no_value=None):
    defaults = {"unit": unit, "color": {"mode": "fixed", "fixedColor": BLUE}, "thresholds": thresholds((None, BLUE)), "min": 0}
    if no_value:
        defaults["noValue"] = no_value
    p = {"type": "bargauge", "title": title, "id": nid(), "datasource": DS, "description": desc,
         "gridPos": {"h": h, "w": w, "x": x, "y": _y[0]},
         "targets": [target(e, lg, instant=instant, ref=chr(65 + i)) for i, (e, lg) in enumerate(exprs)],
         "fieldConfig": {"defaults": defaults, "overrides": []},
         "options": {"orientation": "horizontal", "displayMode": "basic", "showUnfilled": True, "valueMode": "text",
                     "namePlacement": "left", "sizing": "auto", "minVizHeight": 16, "maxVizHeight": 28,
                     "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}}
    panels.append(p)
    return p


def text(content, x, w, h=5, title=""):
    panels.append({"type": "text", "title": title, "id": nid(), "gridPos": {"h": h, "w": w, "x": x, "y": _y[0]},
                   "options": {"mode": "markdown", "content": content}})


def nl(h):
    _y[0] += h


# 1. 規格與結果
row("1. 兩條資料規格與結果（只有這一區會告警）")
stat("新文章多久可搜尋：最久等待（分鐘）", [("max(q3_unsearchable_oldest_minutes)", "")], 0, 6, unit="m",
     steps=((None, GREEN), (300, ORANGE), (360, RED)), decimals=0,
     desc="列表上已出現、還沒入庫的 7 天內文章裡，發文最久的那篇等了幾分鐘。規格 1：6 小時（360 分鐘）內要能搜尋。")
stat("留言新鮮度：最久沒重抓（小時）", [("max(q3_comment_staleness_max_hours)", "")], 6, 6, unit="h",
     steps=((None, GREEN), (22, ORANGE), (24, RED)), decimals=1,
     desc="7 天內可搜尋文章裡，距離上次抓取最久的那篇。規格 2：每天至少更新一次（24 小時）。重抓門檻是 23 小時。")
stat("每日不變量檢查", [("max(q3_verify_ok) and on() (max(q3_verify_last_run_timestamp_seconds) > 0)", "")], 12, 4,
     steps=((None, GREY),),
     mappings=[{"type": "value", "options": {"0": {"text": "失敗", "color": RED}, "1": {"text": "通過", "color": GREEN}}}],
     no_value="還沒跑：第一輪結束後跑第一次", desc="填滿的列表頁恰好 20 格、每格是刪文佔位或已入庫、相鄰文章 ID 時間戳不倒序。")
last_run = stat("上一輪結果", [("max by (status) (q3_run_status) == 1", "{{status}}"),
                           ("vector(1) unless on() count(q3_run_status)", "第一輪還在跑")], 16, 4, text_mode="name",
     steps=((None, GREEN),), instant=True,
     desc="ok＝完整；partial＝有單篇失敗或沒讀完，下一輪自動補；gate_blocked＝品質檢查擋下；rolled_back＝整輪還原；failed＝程式錯誤或連續兩批寫不進去，這輪先停、下一輪補。")
last_run["fieldConfig"]["overrides"] = [
    {"matcher": {"id": "byName", "options": name}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": col}}]}
    for name, col in (("partial", ORANGE), ("gate_blocked", ORANGE), ("rolled_back", ORANGE), ("failed", RED),
                      ("第一輪還在跑", GREY))]
stat("距上一輪結束", [("time() - (max(q3_run_last_finished_timestamp_seconds) > 0)", "")], 20, 4, unit="s",
     steps=((None, GREEN), (5400, ORANGE), (7200, RED)), decimals=0, no_value="第一輪還在跑",
     desc="每小時第 5 分跑一輪；第一次回補 7 天約 2 小時。")
nl(5)

# 2. 資料量
row("2. 資料量（7 天內）")
stat("可搜尋文章", [("max(q3_window_posts)", "")], 0, 4, steps=((None, BLUE),), desc="答案估算：7 天 6,237 篇（每天約 890 篇）。")
stat("留言", [("max(q3_window_comments)", "")], 4, 4, steps=((None, BLUE),), desc="答案估算：7 天 234,577 則。")
stat("已標記刪文（404）", [("max(q3_window_deleted_posts)", "")], 8, 4, steps=((None, GREY),),
     desc="首抓之後才被刪的文章：重抓拿到 404 時標記，不再出現在搜尋結果，但舊內容保留。")
ts("待抓佇列", [("max(q3_backlog_new_posts)", "新文還沒入庫"), ("max(q3_backlog_refresh_posts)", "該重抓還沒重抓")], 12, 12, h=5,
   desc="新文：列表上看到但還沒入庫；重抓：7 天內超過 23 小時沒抓。停機多久，下一輪都會從這兩個佇列自動補。")
nl(5)

# 3. 抓取
row("3. 抓取（單一 worker，每秒最多 1 次）")
ts("每分鐘請求數（依種類）", [("sum by (kind) (rate(q3_http_requests_total[2m])) * 60", "{{kind}}")], 0, 12,
   line=(60, "每秒 1 次"),
   desc="list＝列表頁、article_new＝首抓、article_refresh＝重抓。紅線＝每分鐘 60 次（每秒 1 次的上限）。")
ts("PTT 回應時間", [("histogram_quantile(0.5, sum by (le) (rate(q3_http_latency_seconds_bucket[5m])))", "p50"),
                   ("histogram_quantile(0.95, sum by (le) (rate(q3_http_latency_seconds_bucket[5m])))", "p95")],
   12, 12, unit="s", desc="F12 實測單篇約 300 ms。")
nl(8)
stat("過去 24 小時請求數", [("sum(increase(q3_http_requests_total[24h]))", "")], 0, 6, decimals=0, steps=((None, BLUE),),
     desc="答案估算每天 6,300 次（首抓 890＋列表 70＋重抓 890×6）。剛啟動的第一天含 7 天回補，會比較高。")
text("**答案的估算**\n\n每日 **6,300** 次 ＝ 首抓 890 ＋ 列表 70 ＋ 重抓 890 × 6\n\n"
     "尖峰小時約 **450** 次 → 以每秒 1 次約 7.5 分鐘跑完\n\n規格 2（每天重抓）主導成本：5,340 對 960，約 **5.6 倍**",
     6, 6, title="對照")
ts("退避等待", [("max(q3_backoff_seconds)", "等待秒數")], 12, 6, unit="s",
   desc="429／5xx／連線錯誤會退避 5、10 秒（有 Retry-After 照它），連續 3 次失敗整體暫停 5 分鐘。0＝正常。")
ts("每小時入庫", [("sum(increase(q3_posts_new_total[1h])) or vector(0)", "新文"),
               ("sum(increase(q3_posts_refreshed_total[1h])) or vector(0)", "重抓"),
               ("sum(increase(q3_posts_deleted_total[1h])) or vector(0)", "標記刪文")], 18, 6, bars=False, decimals=0)
nl(8)

# 4. 寫入與自動 rollback
row("4. 寫入與自動 rollback")
ts("每小時寫入批次", [("sum by (result) (increase(q3_batches_total[1h]))", "{{result}}")], 0, 12, bars=True, stack=True,
   colors={"committed": GREEN, "rolled_back": ORANGE, "gate_blocked": RED}, decimals=0,
   desc="committed＝整批落地；rolled_back＝寫入中途失敗、已照 before-image 還原（下一輪補抓）；gate_blocked＝品質檢查不過，整批沒寫。")
stat("過去 24 小時整輪還原", [("sum(increase(q3_run_rollbacks_total[24h]))", "")], 12, 4, decimals=0,
     steps=((None, GREEN), (1, ORANGE)),
     desc="整輪結束後比對：可搜尋文章大減、空白暴增、刪文暴增、留言總數下降，就把這一輪全部還原。")
ts("新文入庫延遲（發文到可搜尋）", [("histogram_quantile(0.5, sum by (le) (rate(q3_ingest_lag_minutes_bucket[1h])))", "p50"),
                            ("histogram_quantile(0.95, sum by (le) (rate(q3_ingest_lag_minutes_bucket[1h])))", "p95")],
   16, 8, unit="m", line=(360, "6 小時"), desc="紅線＝規格 1 的 360 分鐘。第一次回補 7 天時延遲會很長，之後每小時一輪。")
nl(8)

# 5. 重抓的邊際效益
row("5. 重抓的邊際效益（答案第 3、6 題）")
bargauge("過去 24 小時新增留言：依文章發文第幾天", [
    ("sum(increase(q3_comments_new_total{fetch=\"first\"}[24h])) or vector(0)", "首抓（第 0 天）"),
    *[(f"sum(increase(q3_comments_new_total{{fetch=\"refresh\",age_day=\"{d}\"}}[24h])) or vector(0)", f"重抓：第 {d} 天")
      for d in range(0, 8)]],
    0, 12, h=9, unit="none", instant=True,
    desc="答案第 6 題：第 2–6 天的重抓每天 4,450 次，只多 0.76% 留言。這張圖用實際入庫的留言數驗證。")
ts("每小時重抓篇數：依發文第幾天", [("sum by (age_day) (increase(q3_posts_refreshed_total[1h]))", "第 {{age_day}} 天")],
   12, 12, h=9, bars=True, stack=True, decimals=0, no_value="還沒有重抓：文章抓到滿 23 小時才重抓")
nl(9)

# 6. 只記錄、不告警
row("6. 只記錄、不告警（答案第 5 題決定不監控的三件事）")
ts("① 單次請求失敗（非 200／404）", [("sum by (status) (increase(q3_http_requests_total{status!~\"200|404\"}[1h]))", "{{status}}")],
   0, 8, bars=True, decimals=0, no_value="這段時間沒有失敗的請求", desc="失敗的網址下一輪會補，只對結果（規格是否達成）告警。")
ts("② 事後刪文 404", [("sum by (when) (increase(q3_posts_deleted_total[1h]))", "{{when}}")], 8, 8, bars=True, decimals=0,
   no_value="這段時間沒有遇到 404", desc="first＝第一次抓就 404；refresh＝重抓時才發現被刪。答案普查：0.2%（下限）。")
stat("③ 「檔案過大」被截斷的文章（7 天內）", [("max(q3_window_truncated_posts)", "")], 16, 8, h=8, steps=((None, GREY),),
     desc="網頁版本身就拿不到被截掉的留言；答案普查 6,224 篇中 2 篇。")
nl(8)

# 7. 不變量與查詢 API
row("7. 每日不變量明細與查詢 API")
bargauge("上次不變量檢查", [("max(q3_verify{check=\"pages_checked\"})", "檢查頁數"),
                        ("max(q3_verify{check=\"pages_not_20\"})", "不是 20 格的頁"),
                        ("max(q3_verify{check=\"missing_ids\"})", "列表有、沒入庫"),
                        ("max(q3_verify{check=\"out_of_order_pairs\"})", "時間戳倒序的對數"),
                        ("max(q3_verify{check=\"nrec_checked\"})", "推文數比對篇數")], 0, 8, h=8, no_value="還沒跑：第一輪結束後跑第一次")
stat("推文數吻合比例（輔助）", [("max(q3_verify{check=\"nrec_agree_ratio\"})", "")], 8, 4, h=8, unit="percentunit", decimals=1,
     steps=((None, GREY), (0.9, GREEN)), mappings=[{"type": "range", "options": {"from": 0, "to": 0.8999, "result": {"color": ORANGE}}}],
     no_value="還沒跑：第一輪結束後跑第一次", desc="列表上的推文數和文章內「推−噓」換算值相同的比例，檢查解析有沒有算錯。只比列表快照和文章抓取時間相差 30 分鐘內的；"
          "舊的列表頁之後不會重讀，文章每天重抓後推文數本來就會變，拿來比沒有意義。")
ts("查詢 API", [("histogram_quantile(0.95, sum by (le) (rate(q3_search_latency_seconds_bucket[5m]))) > 0", "p95 延遲"),
              ("histogram_quantile(0.5, sum by (le) (rate(q3_search_latency_seconds_bucket[5m]))) > 0", "p50 延遲")],
   12, 8, h=8, unit="s", no_value="這段時間還沒有查詢",
   desc="貼文和留言分開查，再依文章合併；留言先找出命中的最新幾篇（預設 50 篇），只展開這幾篇的留言。")
stat("過去 24 小時 BigQuery 掃描量", [("(sum(increase(q3_bigquery_bytes_processed_total[24h])) or vector(0))"
                                  " + (sum(increase(q3_search_bytes_processed_total[24h])) or vector(0))", "")],
     20, 4, h=8, unit="decbytes", steps=((None, BLUE),), desc="抓取、檢查、查詢合計。每月前 1 TB 免費。本機用的 BigQuery 模擬器回報的掃描量不是 GCP 的計費算法"
          "（GCP 依讀到的欄位大小計，每個查詢至少 10 MB），這裡只看趨勢。")
nl(8)

dash = {
    "uid": "q3-ptt-search", "title": "Q3 PTT 八卦板全文搜尋 ETL", "tags": ["q3", "ptt"], "timezone": "Asia/Taipei",
    "schemaVersion": 39, "version": 1, "editable": False, "graphTooltip": 1, "refresh": "30s",
    "time": {"from": "now-6h", "to": "now"}, "fiscalYearStartMonth": 0, "liveNow": False,
    "templating": {"list": []}, "annotations": {"list": []}, "links": [
        {"title": "搜尋頁", "type": "link", "url": "http://127.0.0.1:8300/", "targetBlank": True, "icon": "external link"}],
    "panels": panels,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(dash, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(f"{OUT}：{len(panels)} 個面板")
