"""產生 Grafana 儀表板 JSON（看板即程式碼：Python 產生、進版控、可以 review）。

兩張看板：
- dashboards/qsearch-q6.json      「QSearch Q6 文字雲」：依資料流分層，一層一列（客戶 SLO → PM 頁 → 完整性 → 串流 → 字典發版收斂 →
                                   回補燃盡 → 品質日報 → Elasticsearch → PostgreSQL 與監控自身）。計畫書 P0 第 1、2、5–10 項都在這張。
- dashboards/qsearch-q6-collect.json「QSearch Q6 採集」：計畫書 P0 第 3、4 項，六列（結果分類、產出率、首抓延遲、退避與預算、延遲分段、監控自身）。

共同規則：
- 顏色用 palette-classic-by-name，同一個名稱（渠道、pipeline）在每個面板同色；六渠道另外寫死（計畫書：兩個面板同一平台顏色相反的教訓）。
- 變數 seg／pipeline／channel／feed；註記從 PostgreSQL live_events 讀（發版、簽核、限速、告警）疊在所有面板上。
- 180 秒 SLA 在耗時面板畫成門檻線；每個門檻都是提案值，寫在面板說明。
- 面板的 PromQL 指標名都在程式裡定義（q6/code/tests/test_p0_configs_consistency.py 檢查）；SQL 只讀 pg/init.sql、pg/live.sql 建的表。
"""
import json
from pathlib import Path

P = {"type": "prometheus", "uid": "prom"}
G = {"type": "grafana-postgresql-datasource", "uid": "pg"}
L = {"type": "loki", "uid": "loki"}
CHANNEL_COLORS = {"facebook": "#1877f2", "instagram": "#e1306c", "youtube": "#ff0000", "ptt": "#f59e0b", "media": "#10b981", "threads": "#111827",
                  "v0": "#9ca3af", "v1": "#6b7280", "v2": "#047857"}
SLA_S = 180


class Board:
    def __init__(self, uid, title, tags, variables, annotations=(), refresh="30s", time_from="now-6h"):
        self.uid, self.title, self.tags, self.variables, self.annotations = uid, title, tags, variables, list(annotations)
        self.refresh, self.time_from = refresh, time_from
        self.panels, self.y, self.x = [], 0, 0

    def row(self, title):
        self._newline()
        self.panels.append({"type": "row", "title": title, "gridPos": {"h": 1, "w": 24, "x": 0, "y": self.y}, "collapsed": False})
        self.y += 1

    def _newline(self):
        if self.x:
            self.y += self._row_h
            self.x = 0

    def add(self, p, w, h):
        if self.x + w > 24:
            self._newline()
        if self.x == 0:
            self._row_h = h
        p["gridPos"] = {"h": h, "w": w, "x": self.x, "y": self.y}
        self.panels.append(p)
        self.x += w
        self._row_h = max(self._row_h, h)

    def dump(self, path: Path):
        self._newline()
        dash = {"uid": self.uid, "title": self.title, "timezone": "Asia/Taipei", "refresh": self.refresh, "editable": True,
                "time": {"from": self.time_from, "to": "now"}, "schemaVersion": 41, "tags": self.tags, "panels": self.panels,
                "templating": {"list": self.variables},
                "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
                                          "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"}] + self.annotations},
                "links": [{"title": "QSearch Q6 文字雲", "type": "link", "url": "/d/qsearch-q6"}, {"title": "QSearch Q6 採集", "type": "link", "url": "/d/qsearch-q6-collect"},
                          {"title": "Alertmanager", "type": "link", "url": "http://127.0.0.1:9093", "targetBlank": True},
                          {"title": "PM 頁", "type": "link", "url": "http://127.0.0.1:8080", "targetBlank": True}]}
        path.write_text(json.dumps(dash, ensure_ascii=False, indent=1), encoding="utf-8")
        return len(self.panels)


def var(name, query, label=None, multi=True, all_=True):
    return {"name": name, "label": label or name, "type": "query", "datasource": P, "query": {"query": query, "refId": name}, "refresh": 2,
            "includeAll": all_, "multi": multi, "current": {"text": "All", "value": "$__all"}, "sort": 1}


def annotation(name, kinds, color):
    sql = ("SELECT extract(epoch FROM at) * 1000 AS time, kind AS tags, "
           "coalesce(actor, '') || ' ' || kind || ' ' || coalesce(detail->>'dict_version', detail->>'partition', detail->>'alertname', detail->>'feed', '') AS text "
           f"FROM live_events WHERE $__timeFilter(at) AND kind IN ({', '.join(repr(k) for k in kinds)})")
    return {"datasource": G, "enable": True, "iconColor": color, "name": name, "target": {"rawQuery": True, "rawSql": sql, "format": "table", "editorMode": "code"}}


def _overrides(names=None):
    out = []
    for n, c in CHANNEL_COLORS.items():
        if names is None or n in names:
            out.append({"matcher": {"id": "byName", "options": n}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]})
    return out


def ts(title, exprs, unit="short", desc="", stack=False, thresholds=None, kind="timeseries", legend_names=None, ds=P, h=8, w=8, min_=None, max_=None, interval=None):
    targets = [{"datasource": ds, "expr": e, "legendFormat": l, "refId": chr(65 + i)} for i, (e, l) in enumerate(exprs)]
    if interval:
        for t in targets:
            t["interval"] = interval
    defaults = {"unit": unit, "color": {"mode": "palette-classic-by-name"}, "custom": {"lineWidth": 1, "fillOpacity": 8 if not stack else 35}}
    if stack:
        defaults["custom"]["stacking"] = {"mode": "normal", "group": "A"}
    if thresholds:
        defaults["thresholds"] = {"mode": "absolute", "steps": [{"color": "green", "value": None}] + [{"color": c, "value": v} for v, c in thresholds]}
        defaults["custom"]["thresholdsStyle"] = {"mode": "line"}
    if min_ is not None:
        defaults["min"] = min_
    if max_ is not None:
        defaults["max"] = max_
    p = {"type": kind, "title": title, "description": desc, "datasource": ds, "targets": targets,
         "fieldConfig": {"defaults": defaults, "overrides": _overrides(legend_names)},
         "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "multi", "sort": "desc"}}}
    return p, w, h


def stat(title, expr, unit="short", desc="", thr=None, w=4, h=4, legend="", decimals=None, color_mode="background", ds=P, reverse=False):
    """單值；thr=[(值, 顏色), …]（提案值）。reverse：數字越小越糟（例如召回率）。"""
    steps = [{"color": "red" if reverse else "green", "value": None}] + [{"color": c, "value": v} for v, c in (thr or [])]
    p = {"type": "stat", "title": title, "description": desc, "datasource": ds,
         "targets": [{"datasource": ds, "expr": expr, "legendFormat": legend, "refId": "A"}],
         "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}, **({"decimals": decimals} if decimals is not None else {})}, "overrides": []},
         "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": color_mode, "graphMode": "area", "textMode": "auto"}}
    return p, w, h


def gauge(title, expr, unit="percentunit", desc="", thr=None, w=4, h=4, legend="", ds=P, min_=0, max_=1):
    steps = [{"color": "green", "value": None}] + [{"color": c, "value": v} for v, c in (thr or [])]
    p = {"type": "bargauge", "title": title, "description": desc, "datasource": ds,
         "targets": [{"datasource": ds, "expr": expr, "legendFormat": legend, "refId": "A"}],
         "fieldConfig": {"defaults": {"unit": unit, "min": min_, "max": max_, "thresholds": {"mode": "absolute", "steps": steps}, "color": {"mode": "thresholds"}}, "overrides": []},
         "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True}}
    return p, w, h


def table(title, sql, desc="", w=12, h=8, ds=G):
    p = {"type": "table", "title": title, "description": desc, "datasource": ds,
         "targets": [{"datasource": ds, "rawSql": sql, "format": "table", "rawQuery": True, "editorMode": "code", "refId": "A"}],
         "fieldConfig": {"defaults": {"custom": {"filterable": True}}, "overrides": []}, "options": {"cellHeight": "sm", "showHeader": True, "footer": {"show": False}}}
    return p, w, h


def sql_ts(title, sql, unit="short", desc="", w=8, h=8, stack=False, kind="timeseries"):
    p = {"type": kind, "title": title, "description": desc, "datasource": G,
         "targets": [{"datasource": G, "rawSql": sql, "format": "time_series", "rawQuery": True, "editorMode": "code", "refId": "A"}],
         "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "palette-classic-by-name"}, "custom": {"lineWidth": 1, "fillOpacity": 35 if stack else 8,
                                                                                                           **({"stacking": {"mode": "normal", "group": "A"}} if stack else {})}},
                         "overrides": _overrides()},
         "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "multi"}}}
    return p, w, h


def heatmap(title, expr, unit="s", desc="", w=8, h=8):
    p = {"type": "heatmap", "title": title, "description": desc, "datasource": P,
         "targets": [{"datasource": P, "expr": expr, "format": "heatmap", "legendFormat": "{{le}}", "refId": "A"}],
         "options": {"calculate": False, "yAxis": {"unit": unit}, "color": {"mode": "scheme", "scheme": "Oranges", "steps": 32},
                     "cellGap": 1, "tooltip": {"mode": "single", "showColorScale": True}, "legend": {"show": True}},
         "fieldConfig": {"defaults": {}, "overrides": []}}
    return p, w, h


def logs(title, expr, desc="", w=12, h=8):
    p = {"type": "logs", "title": title, "description": desc, "datasource": L,
         "targets": [{"datasource": L, "expr": expr, "refId": "A"}],
         "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "dedupStrategy": "none", "enableLogDetails": True}}
    return p, w, h


def text(title, md, w=24, h=3):
    return {"type": "text", "title": title, "options": {"mode": "markdown", "content": md}}, w, h


# ============================================================ 主看板 ============================================================
MAIN = Board("qsearch-q6", "QSearch Q6 文字雲：客戶 SLO → PM 頁 → 完整性 → 串流 → 發版收斂 → 回補 → 品質 → ES", ["qsearch", "q6"],
             variables=[var("seg", "label_values(wordcloud_duration_seconds_count, seg)", "文字雲版本"),
                        var("pipeline", "label_values(qseg_messages_total, pipeline)", "斷詞流程"),
                        var("channel", "label_values(qseg_recon_docs, channel)", "渠道")],
             annotations=[annotation("字典發版", ["release"], "#047857"), annotation("簽核／豁免／限速", ["approve", "waive", "control"], "#f59e0b"),
                          annotation("告警", ["alert"], "#b91c1c")])
B = MAIN

B.row("① 客戶可見：文字雲 API 的 3 分鐘 SLO（計畫書 P0 第 1 項；SLI＝180 秒內成功 ÷ 全部查詢，含錯誤與逾時；Sloth 多窗口燃燒率）")
B.add(*ts("查詢耗時 p50／p95／p99（未命中快取；錯誤與逾時也在分母）", [
    ('histogram_quantile(0.99, sum by (le, seg) (rate(wordcloud_duration_seconds_bucket{cache="miss", seg=~"$seg"}[$__rate_interval])))', "p99 {{seg}}"),
    ('histogram_quantile(0.95, sum by (le, seg) (rate(wordcloud_duration_seconds_bucket{cache="miss", seg=~"$seg"}[$__rate_interval])))', "p95 {{seg}}"),
    ('histogram_quantile(0.5, sum by (le, seg) (rate(wordcloud_duration_seconds_bucket{cache="miss", seg=~"$seg"}[$__rate_interval])))', "p50 {{seg}}")],
    unit="s", thresholds=[(SLA_S, "red")], desc=f"紅線＝{SLA_S} 秒 SLA。直方圖桶含 180 與 300，失敗的查詢也 observe（status=error），最慢的查詢不會從分布消失", w=10))
B.add(*stat("28 天錯誤預算剩餘（≤180 秒 99%）", 'slo:period_error_budget_remaining:ratio{sloth_id="wordcloud-api-query-within-180s"}', unit="percentunit",
            thr=[(0, "red"), (0.2, "orange"), (0.5, "green")], desc="Sloth 產生的記錄規則（monitoring/slo/wordcloud-api.yml → rules/slo.yml）；< 0 代表預算燒完。目標 99% 是提案值", w=4, reverse=True))
B.add(*stat("目前燃燒率（1 小時窗）", 'slo:sli_error:ratio_rate1h{sloth_id="wordcloud-api-query-within-180s"} / on () group_left slo:error_budget:ratio{sloth_id="wordcloud-api-query-within-180s"}',
            unit="short", thr=[(1, "orange"), (14.4, "red")], decimals=2, desc="燃燒率 1＝剛好在 28 天內用完預算；page 門檻 14.4（1 小時）／6（6 小時），ticket 3（1 天）／1（3 天），Sloth 預設", w=4))
B.add(*stat("最近 5 分鐘超過 180 秒的查詢", 'sum(increase(wordcloud_over_180s_total{seg=~"$seg"}[5m]))', thr=[(1, "orange")], desc="告警 WordcloudOver180s（ticket；對應 Cloud Logging 日誌型指標 wordcloud_over_180s）", w=3))
B.add(*stat("處理中的查詢", 'sum(wordcloud_inflight_queries{seg=~"$seg"})', thr=[(20, "orange"), (30, "red")], desc="FastAPI 同步端點共用 anyio 40 個 token；≥ 30 warn（提案值，含 /metrics 自己）", w=3))
B.add(*ts("錯誤分解（5 分鐘增量）：逾時／ES 部分結果／其他；慢成功另列", [
    ('sum by (reason) (increase(wordcloud_errors_total{seg=~"$seg"}[5m]))', "{{reason}}"),
    ('sum(increase(wordcloud_over_180s_total{seg=~"$seg"}[5m]))', "慢成功（>180 秒）")], stack=True, w=8,
    desc="timeout＝requests 讀逾時 180 秒；partial＝ES 自己的 170 秒 timeout 回 timed_out（WordcloudPartialResults）；other＝ES 4xx/5xx 或連不上。告警 WordcloudSlo180sBurn 看 SLI，不看這裡"))
B.add(*ts("查詢量與快取（每秒）", [('sum by (seg, result) (rate(wordcloud_cache_total{seg=~"$seg"}[$__rate_interval]))', "{{seg}} {{result}}")], unit="reqps", stack=True, w=8,
          desc="hit＝快取命中；coalesced＝同一鍵的查詢合併等第一筆（single-flight）；miss＝真的打 ES。已結束日期範圍的快取 24 小時、含今天的 300 秒"))
B.add(*ts("各階段耗時 p95：overlay／state／ES／post", [('histogram_quantile(0.95, sum by (le, phase) (rate(wordcloud_phase_seconds_bucket{seg=~"$seg"}[$__rate_interval])))', "{{phase}}")],
          unit="s", w=8, desc="ES 以外的階段應該都在毫秒級；PG 連線池（最多 4 條，statement_timeout 10 秒）出問題會先反映在 state／overlay"))

B.row("② PM 頁（計畫書 P1「PM 頁」：自寫 rum.js → /api/rum；沒有 Faro SDK，指標名相同）")
B.add(*ts("Web Vitals p75：LCP／INP／首次出雲（ttfc）", [
    ('histogram_quantile(0.75, sum by (le, metric) (rate(wordcloud_web_vitals_seconds_bucket[1h])))', "{{metric}}"),
    ('histogram_quantile(0.95, sum by (le, seg) (rate(wordcloud_duration_seconds_bucket{cache="miss"}[1h])))', "API p95 {{seg}}")],
    unit="s", w=8, thresholds=[(2.5, "orange")], desc="LCP > 2.5 秒、INP > 200 ms 是 web-vitals 的「良好」上限（WebLcpSlow／WebInpSlow，樣本 ≥ 5 才評）；ttfc 對照伺服器端 p95：差距＝頁面自己的成本"))
B.add(*ts("瀏覽器端量到的 API 往返 p95（含網路）", [('histogram_quantile(0.95, sum by (le, path) (rate(wordcloud_web_api_seconds_bucket[$__rate_interval])))', "{{path}}")], unit="s", w=8,
          desc="對照伺服器端 wordcloud_duration_seconds：差＝網路與瀏覽器解析；/api/status 每 2 秒一次"))
B.add(*stat("CLS p75", "histogram_quantile(0.75, sum by (le) (rate(wordcloud_web_cls_bucket[1h])))", unit="short", decimals=3, thr=[(0.1, "orange"), (0.25, "red")], desc="頁面離開時送；> 0.1 warn（WebClsHigh）", w=2))
B.add(*stat("JS 錯誤（1 小時）", "sum(increase(wordcloud_web_errors_total[1h]))", thr=[(1, "orange")], desc="WebJsErrors（ticket）", w=2))
B.add(*stat("排版放不下的詞（1 小時）", "sum(increase(wordcloud_web_dropped_words_total[1h]))", thr=[(1, "orange")], desc="頁面會顯示是哪些詞；WebDroppedWords（ticket）", w=2))
B.add(*stat("雲上篇數≠抽屜（1 小時）", "sum(increase(wordcloud_web_count_mismatch_total[1h]))", thr=[(1, "orange")], desc="兩次查詢之間資料變了（快取或新文章）；WebCountMismatch（ticket）", w=2))

B.row("③ 完整性：原文湖 → ES → 有 v2 → 最新版（計畫書 P0 第 2 項四層對帳 seg-recon，每小時；覆蓋與缺值 seg-coverage，每分鐘）")
B.add(*table("四層對帳（今天；渠道 × 月）", """SELECT channel AS 渠道, month AS 月, raw_docs AS "L1 原文", es_docs AS "L2 ES", es_deleted AS 已刪, es_with_v2 AS "L3 有v2", es_at_latest_v2 AS "L4 最新版",
 ledger_written + ledger_noop AS "帳本 寫入+noop", ledger_status AS 帳本狀態, round(missing_ratio * 100, 3) AS "缺 %", extra_docs AS 多出
 FROM recon_daily WHERE day = (SELECT max(day) FROM recon_daily) AND channel IN ($channel) ORDER BY missing_ratio DESC NULLS LAST, channel, month""",
              desc="L1 是 _manifest.json 的不重複 post_id（G1 也比這個）；缺 %＝max(0, L1 −（L2 − 已刪）) ÷ L1；告警 ReconMissingDocs（passed 分區 > 0.1% 持續 1 小時）", w=14, h=10))
B.add(*gauge("原文有、ES 沒有的比例（passed 分區）", 'qseg_recon_missing_ratio{channel=~"$channel"} and on (channel, month) qseg_recon_partition_passed == 1', unit="percentunit",
             thr=[(0.001, "orange"), (0.01, "red")], legend="{{channel}}/{{month}}", max_=0.05, desc="提案值：> 0.1% warn", w=5, h=10))
B.add(*gauge("A/B 覆蓋率（有 terms_v2 ÷ 渠道文章數）", 'qseg_coverage_terms_v2_ratio{channel=~"$channel"}', legend="{{channel}}", thr=[(0.5, "orange"), (0.99, "green")], desc="分母是該渠道全部文章", w=5, h=10))
B.add(*stat("超過 15 分鐘仍未斷詞", "sum(qseg_coverage_unsegmented_docs) or vector(0)", thr=[(1, "red")], desc="fetched_at 在 1 小時到 15 分鐘前、沒有 seg_ver_v2；SegUnsegmentedDocs（page，15 分鐘）", w=4))
B.add(*stat("缺 published_at", "sum(qseg_coverage_missing_published_at_docs) or vector(0)", thr=[(1, "orange")], desc="month=unknown 的文章不在任何回補分區（ReconUnknownMonthDocs）", w=4))
B.add(*stat("對帳最後成功", "time() - qseg_recon_last_success_timestamp_seconds", unit="s", thr=[(7200, "orange"), (93600, "red")], desc="ReconStale：26 小時", w=4))
B.add(*stat("覆蓋檢查最後成功", "time() - qseg_coverage_last_success_timestamp_seconds", unit="s", thr=[(600, "orange")], desc="SegCoverageStale：10 分鐘", w=4))
B.add(*ts("ES 篇數依渠道（layer=es）與多出的篇數", [('sum by (channel) (qseg_recon_docs{layer="es", channel=~"$channel"})', "{{channel}}"),
                                                  ('sum by (channel) (qseg_recon_extra_docs{channel=~"$channel"})', "多出 {{channel}}")], w=8,
          desc="多出＝ES 有、原文湖沒有（串流進來的新文章是正常的；回補範圍內多出要查重複 post_id）"))

B.row("④ 串流斷詞（Pub/Sub → seg-v1／seg-v2 → ES；計畫書 P0 第 5 項積壓與 DLQ，本機用程序內積壓年齡代替 Cloud Monitoring）")
B.add(*ts("積壓年齡（最舊未 ack 訊息）", [('time() - min by (pipeline) (qseg_oldest_unacked_publish_ts{pipeline=~"$pipeline"})', "{{pipeline}}")], unit="s",
          thresholds=[(600, "orange"), (2700, "red")], desc="提案值：> 10 分鐘 ticket、> 45 分鐘 page（SegBacklogAgeTicket／Page）；服務掛掉由 SegServiceDown 先響", w=8))
B.add(*ts("發佈到寫進 ES 的延遲 p95", [('histogram_quantile(0.95, sum by (le, pipeline) (rate(qseg_e2e_lag_seconds_bucket{pipeline=~"$pipeline"}[$__rate_interval])))', "{{pipeline}}")], unit="s", w=8,
          desc="只在 ack 時記錄：卡住的訊息看積壓年齡，不在這裡"))
B.add(*ts("訊息 ack／nack／bad（每秒）", [('sum by (pipeline, result) (rate(qseg_messages_total{pipeline=~"$pipeline"}[$__rate_interval]))', "{{pipeline}} {{result}}")], stack=True, w=8,
          desc="nack 比例 > 1% 持續 2 分鐘 warn（SegNackRatioHigh）；bad＝壞訊息進 DLQ"))
B.add(*ts("寫入結果（created／updated／noop／errors）", [('sum by (pipeline, result) (rate(qseg_docs_total{pipeline=~"$pipeline", mode="stream"}[$__rate_interval]))', "{{pipeline}} {{result}}")], stack=True, w=8,
          desc="同版重送＝noop（冪等）；errors 會 nack 重送"))
B.add(*ts("刪文 tombstone／改文／bulk 429 重送", [('sum by (pipeline, result) (increase(qseg_tombstones_total{pipeline=~"$pipeline"}[$__rate_interval]))', "刪文 {{pipeline}} {{result}}"),
                                                ('sum by (pipeline) (increase(qseg_edited_total{pipeline=~"$pipeline"}[$__rate_interval]))', "改文 {{pipeline}}"),
                                                ('sum by (pipeline, mode) (increase(qseg_bulk_retries_total{pipeline=~"$pipeline"}[$__rate_interval]))', "429 重送 {{pipeline}} {{mode}}")], w=8,
          desc="刪文只寫 deleted_at（文字雲查詢排除）；改文＝content_sha 不同，seg_guard 重寫本體與詞表；429 等 1、2、4 秒最多重送 3 次"))
B.add(*ts("必不可拆的詞被切開（串流，依渠道）", [('sum by (pipeline, channel) (increase(qseg_must_keep_split_total{pipeline=~"$pipeline"}[$__rate_interval]))', "{{pipeline}} {{channel}}")], w=8,
          desc="segment_checked 看斷點位置（台北市長 → 台北／市長 允許；鳳山區 → 鳳山／區 不允許）；回補 G2 看同一件事"))

B.row("⑤ 字典發版收斂（計畫書 P0 第 7 項：service_state 心跳裡有幾個不同 dict_version；rerun_runs；release_outbox）")
B.add(*stat("v2 執行個體載入的字典版本數", 'qseg_service_dict_versions{pipeline="v2"}', thr=[(2, "orange")], desc="> 1 持續 10 分鐘 warn（DictVersionDivergence）：每台每 15 秒輪詢 current/manifest.json，正常幾十秒內收斂", w=4))
B.add(*stat("v2 存活執行個體", 'qseg_service_instances_alive{pipeline="v2"}', thr=[(1, "green")], color_mode="value", desc="1 分鐘內有心跳；0 → SegNoAliveInstances（page）", w=4, reverse=True))
B.add(*stat("定向重跑執行中最久", "qseg_rerun_running_age_seconds", unit="s", thr=[(1800, "orange")], desc="> 30 分鐘 warn（RerunStuck）；計畫書「超過預估 2 倍」目前沒有預估值", w=4))
B.add(*stat("發版 outbox 待送", "qseg_release_outbox_pending", thr=[(1, "orange")], desc="控制訊息發不出去（Pub/Sub 不通）：字典已切 current，背景每 10 秒重送；> 5 分鐘 warn（ReleaseOutboxStuck）", w=4))
B.add(*ts("字典版本（各執行個體）", [('qseg_dict_info{pipeline=~"$pipeline"}', "{{pipeline}} {{dict_version}} seg_ver={{seg_ver}} {{build}}")], w=8, h=8,
          desc="熱換後舊版的 series 會被移除（原本會一直是 1）；同一時間兩條線＝沒收斂"))
B.add(*ts("熱換次數（source=control：發版訊息；manifest：輪詢發現新版）", [('sum by (pipeline, source) (increase(qseg_dict_swaps_total[$__rate_interval]))', "{{pipeline}} {{source}}")], w=8,
          desc="manifest 來源的熱換＝控制訊息沒到這一台（Pub/Sub 只交給一台），靠輪詢追上"))
B.add(*ts("rerun_runs 各狀態（最近 7 天）", [("qseg_rerun_runs", "{{status}}")], w=8, stack=True, desc="failed 增加 → RerunFailed（ticket）"))
B.add(*table("字典發版對帳（估計 vs 實際重跑寫入；加詞影響範圍的地面真值漏抓）", """SELECT dict_version AS 字典版本, seg_ver, array_to_string(added_words, '、') AS 加詞, affected_estimate AS 估計,
 rerun_written AS 實際寫入, round(100.0 * abs(affected_estimate - rerun_written) / NULLIF(affected_estimate, 0), 2) AS "差異 %", bigram_audit_missed AS 漏抓, released_at AS 發版時間
 FROM dict_releases ORDER BY released_at DESC LIMIT 20""", desc="差異來自 noop、errors 與併發寫入（定向重跑會處理每個候選）；漏抓 > 0 應阻擋發版（impact.py --record 才有值；未自動化）", w=24, h=7))

B.row("⑥ 回補燃盡（計畫書 P0 第 8 項：帳本剩餘 ÷ 近 15 分鐘速率＝ETA；attempts ≥ 3；awaiting_pm 等多久；限速與 G3 自動降速）")
B.add(*stat("剩餘篇數", "qseg_backfill_remaining_docs", desc="pending／running 分區的 raw_count − checkpoint_line", w=3, thr=[]))
B.add(*stat("近 15 分鐘速率", "qseg_backfill_docs_per_sec_15m", unit="short", decimals=0, thr=[(1450, "green")], reverse=True, color_mode="value",
            desc="< 1,450 篇／秒超過一晚 warn（12 晚 × 8 小時做完 5 億篇的下限；BackfillThroughputLow）", w=3))
B.add(*stat("ETA", "qseg_backfill_remaining_docs / (qseg_backfill_docs_per_sec_15m > 0)", unit="s", thr=[(8 * 3600, "orange")], desc="> 8 小時（剩餘離峰窗）warn（BackfillEtaBeyondWindow）", w=3))
B.add(*stat("attempts ≥ 3 的分區", "qseg_ledger_attempts_exhausted", thr=[(1, "red")], desc="OOM 後反覆被重領；轉 failed 不再重領（BackfillAttemptsExhausted）", w=3))
B.add(*stat("最老的 awaiting_pm", "qseg_ledger_oldest_awaiting_pm_age_seconds", unit="s", thr=[(4 * 3600, "orange")], desc="> 4 小時提醒 PM（BackfillAwaitingPmTooLong）", w=3))
B.add(*stat("目前限速", "qseg_backfill_max_docs_per_sec", unit="short", decimals=0, thr=[], desc="backfill_control；G3 探針 p95 惡化就砍半、恢復後每批回升 25% 到 ceiling", w=3))
B.add(*stat("暫停中", "qseg_backfill_paused", thr=[(1, "orange")], desc="BackfillPausedLong：暫停超過 2 小時且還有分區要跑", w=3))
B.add(*ts("帳本各狀態分區數", [("qseg_ledger_partitions", "{{status}}")], stack=True, w=3, h=4))
B.add(*sql_ts("回補吞吐（每秒篇數，按分鐘平均；BRIN(at) 索引）", "SELECT $__timeGroupAlias(at, 1m), sum(docs) / 60.0 AS docs_per_s FROM backfill_batches WHERE $__timeFilter(at) GROUP BY 1 ORDER BY 1",
              w=12, desc="一個 bulk 一列（日誌行數 ≠ 處理篇數）；對照 ① 的 p95：回補拖慢查詢在同一條時間軸看得到"))
B.add(*table("最近完成的分區（閘門）", """SELECT partition_id AS 分區, status AS 狀態, raw_count AS 原文, raw_lines AS 原始行, written AS 寫入, noop, errors AS 錯, attempts, docs_per_sec AS "每秒",
 (gates->'G1'->>'pass') AS G1, (gates->'G2'->>'pass') AS G2, (gates->'G3'->>'pass') AS G3, (gates->'G3'->>'canary_p95_ms_after') AS "探針 p95 ms", finished_at AS 完成
 FROM backfill_ledger WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 12""", desc="G1 比不重複 post_id；G2 每類洩漏 ≤ 0.1%；G3 線上欄位（terms_v1）探針 p95 不惡化；G4 首月 PM 簽核", w=12))

B.row("⑦ 文字雲品質日報（計畫書 P0 第 9 項：seg-quality 每天一次寫 quality_daily；「更乾淨」不等於「更好」）")
B.add(*gauge("洩漏率（抽樣篇數比例，依渠道 × 類別）", 'qseg_quality_leak_ratio{channel=~"$channel"}', legend="{{channel}} {{kind}}", thr=[(0.0001, "orange"), (0.01, "red")], max_=0.05,
             desc="url＝網址殘片（看原文）、phone、number、stopword＝硬停用詞、must_keep_split＝必不可拆詞被切開；> 0 warn（QualityLeak）", w=8, h=8))
B.add(*gauge("地名被切開（含地名的抽樣篇數比例）", 'qseg_quality_place_split_ratio{channel=~"$channel"}', legend="{{channel}}", thr=[(0.0001, "orange")], max_=0.05,
             desc="使用者字典裡縣市區鄉鎮村里結尾的詞；> 0 warn（QualityPlaceSplit）", w=4, h=8))
B.add(*stat("查詢詞整詞召回", "qseg_quality_whole_word_recall", unit="percentunit", decimals=2, thr=[(0.99, "green")], reverse=True, desc="整詞成為 token 的篇數 ÷ 片語比對篇數；< 99% warn（QualityWholeWordRecallLow）", w=4))
B.add(*stat("金標詞召回 v2／v1／v0", "qseg_quality_gold_recall", unit="percentunit", decimals=3, legend="{{pipeline}}", thr=[(0.71, "green")], reverse=True, color_mode="value",
            desc="150 題 PTT 標題、430 個金標詞（≥ 2 票）；10/31 版 75.6%（95% CI 71.3–79.8）；< 71% ticket（QualityGoldRecallDrop）", w=4))
B.add(*stat("McNemar p（v1→v2／v0→v2）", "qseg_quality_mcnemar_p", unit="short", decimals=6, legend="{{pair}}", thr=[], color_mode="value", desc="精確二項式雙尾；< 0.05 代表兩版留住的金標詞集合顯著不同", w=4))
B.add(*ts("前 50 詞對昨天的 RBO 與 JS 散度（依渠道）", [('qseg_quality_rbo{pair="prev_day", channel=~"$channel"}', "RBO {{channel}}"), ('qseg_quality_js_divergence{channel=~"$channel"}', "JS {{channel}}")],
          w=8, min_=0, max_=1, thresholds=[(0.5, "orange")], desc="RBO 1＝排名一樣；< 0.5 ticket（QualityTopShift）對照註記裡的發版與回補；JS 0＝分布一樣"))
B.add(*ts("v1 對 v2 的 RBO（A/B 距離）與新詞暴增", [('qseg_quality_rbo{pair="v1_v2", channel=~"$channel"}', "RBO v1↔v2 {{channel}}"), ("qseg_quality_new_word_surges", "暴增詞 {{kind}}")], w=8,
          desc="暴增＝今天 terms_v2 對前 7 天 z > 3 且 ≥ 20 篇；fragment＝整詞率 < 50%（新詞被切碎，該進字典；QualityNewWordFragment）"))
B.add(*table("品質日報（最近 7 天）", """SELECT day AS 日, channel AS 渠道, docs_sampled AS 抽樣, round(leak_url*100,2) AS "網址 %", round(leak_phone*100,2) AS "電話 %", round(leak_number*100,2) AS "數字 %",
 round(leak_stopword*100,2) AS "停用詞 %", round(must_keep_split*100,2) AS "必不可拆 %", place_split || '/' || place_docs AS 地名切開, round(rbo_prev,3) AS "RBO 昨", round(js_prev,3) AS "JS 昨",
 round(rbo_v1v2,3) AS "RBO v1v2", round(whole_word_recall*100,2) AS "整詞召回 %", whole_word_words AS 詞數, gold_recall->'v2'->>'recall' AS "金標 v2", mcnemar->'v1_v2'->>'p' AS "p v1v2",
 jsonb_array_length(coalesce(surges, '[]'::jsonb)) AS 暴增, dict_version AS 字典
 FROM quality_daily WHERE day > current_date - 7 ORDER BY day DESC, channel""", w=24, h=8, desc="channel='(all)' 那一列放整詞召回、金標、McNemar、暴增；whole_word_worst／surges 的明細在表的 jsonb 欄"))

B.row("⑧ Elasticsearch 叢集健康（計畫書 P0 第 10 項：elasticsearch_exporter v1.9.0；OTel receiver 的寫入／搜尋速率仍在）")
B.add(*stat("叢集狀態", 'elasticsearch_cluster_health_status{color="green"} * 0 + elasticsearch_cluster_health_status{color="yellow"} * 1 + elasticsearch_cluster_health_status{color="red"} * 2',
            thr=[(1, "orange"), (2, "red")], desc="0 green／1 yellow／2 red；本機單節點 0 副本本來就是 yellow；red 立即 page（ElasticsearchClusterRed）", w=3))
B.add(*stat("磁碟用量", "1 - (elasticsearch_filesystem_data_available_bytes / elasticsearch_filesystem_data_size_bytes)", unit="percentunit", thr=[(0.8, "orange"), (0.88, "red")],
            desc="> 80% warn、> 88% 或 4 小時內會滿 page；ES 85% 不再分配分片、90% 搬走、95% 設成唯讀。本機 compose 改用絕對值 4gb／2gb／1gb", w=3))
B.add(*stat("JVM heap", 'elasticsearch_jvm_memory_used_bytes{area="heap"} / elasticsearch_jvm_memory_max_bytes{area="heap"}', unit="percentunit", thr=[(0.75, "orange"), (0.9, "red")], desc="> 90% 持續 10 分鐘 warn", w=3))
B.add(*stat("未分配分片", "elasticsearch_cluster_health_unassigned_shards", thr=[(1, "orange")], desc="> 0 持續 15 分鐘 warn", w=3))
B.add(*stat("breaker 觸發（10 分鐘）", "sum(increase(elasticsearch_breakers_tripped[10m]))", thr=[(1, "orange")], desc="大聚合或 fielddata 打爆記憶體（ElasticsearchBreakerTripped）", w=3))
B.add(*stat("寫入拒絕（10 分鐘）", 'sum(increase(elasticsearch_thread_pool_rejected_count{type="write"}[10m]))', thr=[(1, "orange")], desc="429：回補或串流寫太快，G3 會自動降速", w=3))
B.add(*stat("SLM 快照失敗（1 天）", "sum(increase(elasticsearch_slm_stats_total_snapshots_failed_total[1d]))", thr=[(1, "orange")], desc="政策 daily-posts 每天 01:30（本機 fs 倉庫；GCP 上是 GCS 倉庫）", w=3))
B.add(*stat("快照成功（2 天）", "sum(increase(elasticsearch_slm_stats_total_snapshots_taken_total[2d]))", thr=[(1, "green")], reverse=True, color_mode="value", desc="2 天內 0 次 → ticket（ElasticsearchSnapshotStale）", w=3))
B.add(*ts("搜尋／寫入速率（OTel receiver）", [('rate(elasticsearch_node_operations_completed_total{operation="query"}[$__rate_interval])', "query/s"),
                                             ('rate(elasticsearch_node_operations_completed_total{operation="index"}[$__rate_interval])', "index/s")], unit="ops", w=8))
B.add(*ts("執行緒池佇列與拒絕", [('elasticsearch_node_thread_pool_tasks_queued{thread_pool_name=~"search|write"}', "佇列 {{thread_pool_name}}"),
                                 ('increase(elasticsearch_thread_pool_rejected_count{type=~"search|write"}[5m])', "拒絕 {{type}}")], w=8))
B.add(*ts("GC 時間占比與 heap", [("rate(elasticsearch_jvm_gc_collection_seconds_sum[5m])", "GC {{gc}}"), ('elasticsearch_jvm_memory_used_bytes{area="heap"} / elasticsearch_jvm_memory_max_bytes{area="heap"}', "heap 使用率")],
          unit="percentunit", w=8, thresholds=[(0.1, "orange")], desc="GC > 10% 持續 10 分鐘 warn（ElasticsearchGcTimeHigh）"))

B.row("⑨ PostgreSQL、日誌與監控自身（Alertmanager → /api/ops/alert → PM 頁橫幅；Loki／Alloy；Prometheus 目標）")
B.add(*ts("抓取目標 up（0＝掛了，各 *Down 告警先響）", [("up", "{{job}}")], w=8, min_=0, max_=1, desc="服務自己回報的指標在服務掛掉時是空向量，所以每個 job 都有 up==0／absent 規則"))
B.add(*ts("PostgreSQL 交易與資料庫大小", [("rate(postgresql_commits_total[$__rate_interval])", "commits/s"), ("postgresql_db_size_bytes", "db size")], w=8,
          desc="API 走連線池（最多 4 條）；/api/status 每 2 秒一次"))
B.add(*ts("告警 webhook（Alertmanager → API）與 PG 錯誤", [('sum by (status) (increase(wordcloud_alert_webhooks_total[$__rate_interval]))', "webhook {{status}}"),
                                                          ("increase(wordcloud_pg_errors_total[$__rate_interval])", "PG 錯誤")], w=8))
B.add(*logs("API 與斷詞服務的 WARNING／ERROR（Loki）", '{service=~"api|seg-v1|seg-v2|seg-recon|seg-quality"} | json | severity=~"WARNING|ERROR"', w=24, h=8,
            desc="Alloy 從 docker 標籤收容器日誌，json 解析 severity／event；慢查詢 slowlog > 60 秒也進 Loki（ES 容器）"))

# ============================================================ 採集看板 ============================================================
C = Board("qsearch-q6-collect", "QSearch Q6 採集：結果分類 → 產出率 → 首抓延遲 → 退避與預算 → 延遲分段 → 監控自身", ["qsearch", "q6", "collect"],
          variables=[var("channel", "label_values(collector_requests_total, channel)", "平台"), var("feed", "label_values(collector_requests_total, feed)", "來源")],
          annotations=[annotation("採集操作", ["collect_request"], "#0e7490"), annotation("告警", ["alert"], "#b91c1c")])
C.row("1. 結果分類（計畫書 P0 第 3 項：回 200 不等於抓到；outcome＝ok／empty／not_modified／login_wall／content_unavailable／rate_limited／forbidden／server_error／network_error／parse_error／skipped）")
C.add(*ts("每次請求的結果（每 5 分鐘，依平台 × 結果）", [('sum by (channel, outcome) (increase(collector_requests_total{channel=~"$channel", feed=~"$feed"}[5m]))', "{{channel}} {{outcome}}")], stack=True, w=12,
          desc="login_wall ≥ 1 次立即 page（CollectorLoginWall）；rate_limited／forbidden ≥ 2 次 warn（CollectorBlocked）；skipped＝沒有 YouTube API key 或 feed 被暫停"))
C.add(*ts("Loki ruler 算出的同一件事（crawl:requests:rate5m；只留日誌的爬蟲用這條）", [('sum by (channel, outcome) (crawl:requests:rate5m{channel=~"$channel"})', "{{channel}} {{outcome}}")], unit="reqps", stack=True, w=6,
          desc="Loki ruler → remote_write 進 Prometheus；電腦端只有 Loki 的爬蟲監測改 container 標籤就能用"))
C.add(*ts("Alloy stage.metrics 的日誌行數（取樣前）", [('sum by (channel, outcome) (rate(loki_process_custom_crawl_log_lines_total{channel=~"$channel"}[$__rate_interval]))', "{{channel}} {{outcome}}")], unit="reqps", w=6,
          desc="計數在 stage.sampling 之前算，取樣不會讓計數失真"))

C.row("2. 產出率與欄位填充率（每次請求解析出的篇數；new＝第一次看到；updated＝content_sha 變了）")
C.add(*ts("解析／新進／更新的篇數（每 5 分鐘）", [('sum by (channel) (increase(collector_posts_parsed_total{channel=~"$channel", feed=~"$feed"}[5m]))', "parsed {{channel}}"),
                                                ('sum by (channel) (increase(collector_posts_new_total{channel=~"$channel", feed=~"$feed"}[5m]))', "new {{channel}}"),
                                                ('sum by (channel) (increase(collector_posts_updated_total{channel=~"$channel", feed=~"$feed"}[5m]))', "updated {{channel}}")], w=12,
          desc="產出率低於 7 天前同時段的 50% 持續 30 分鐘 → warn（CollectorYieldDrop）"))
C.add(*ts("產出率對 7 天前同時段（parsed，1 小時窗）", [('sum by (channel) (increase(collector_posts_parsed_total{channel=~"$channel"}[1h])) / (sum by (channel) (increase(collector_posts_parsed_total{channel=~"$channel"}[1h] offset 7d)) > 0)', "{{channel}}")],
          unit="percentunit", w=6, thresholds=[(0.5, "red")], desc="< 50% 持續 30 分鐘 warn"))
C.add(*ts("Loki：每秒解析篇數（crawl:posts_parsed:rate5m）", [('crawl:posts_parsed:rate5m{channel=~"$channel"}', "{{channel}}")], w=6))
C.add(*table("每個來源的最後一次請求（outcome、解析篇數、轉址、延遲、robots）", """SELECT channel AS 渠道, feed AS 來源, outcome AS 結果, last_status AS "HTTP", posts_parsed AS 解析, last_new AS 新, total_new AS 累計新,
 items_seen AS 看到, round(ttfb_ms) AS "TTFB ms", round(total_ms) AS "總 ms", robots, final_url AS 轉址到, last_poll_at AS 上次抓, last_ok_at AS 上次成功, backoff_level AS 退避, next_poll_at AS 下次, error AS 錯誤
 FROM collector_state ORDER BY channel, feed""",
              desc="欄位填充率（發文時間、作者、互動數）的分母是 feed 給的欄位：PTT Atom 有作者、YouTube API 有統計、RSS 沒有互動數（欄位本來就沒有，不算缺）；缺 published_at 的篇數在主看板 ③", w=24, h=7))

C.row("3. 首抓延遲與逾期目標（計畫書 P0 第 4 項：抓到時間 − 發文時間，只算發文 24 小時內；桶有 2700 與 3600）")
C.add(*ts("首抓延遲 p95（1 小時窗）", [('histogram_quantile(0.95, sum by (le, channel) (rate(collector_first_seen_lag_seconds_bucket{channel=~"$channel"}[1h])))', "{{channel}}")], unit="s",
          thresholds=[(2700, "orange"), (3600, "red")], desc="> 45 分鐘 warn、> 60 分鐘 page（CollectorFirstSeenLagHigh／Critical）", w=8))
C.add(*ts("60 分鐘內抓到的比例", [('sum by (channel) (rate(collector_first_seen_lag_seconds_bucket{le="3600.0", channel=~"$channel"}[1h])) / sum by (channel) (rate(collector_first_seen_lag_seconds_count{channel=~"$channel"}[1h]))', "{{channel}}")],
          unit="percentunit", w=8, min_=0, max_=1, thresholds=[(0.95, "orange")], desc="< 95% warn（提案值）；le 桶的字串是 3600.0（prometheus_client 的浮點）"))
C.add(*heatmap("首抓延遲分布（直方圖）", 'sum by (le) (increase(collector_first_seen_lag_seconds_bucket{channel=~"$channel"}[5m]))', w=8))
C.add(*table("逾期沒抓的來源（超過輪詢週期還沒成功）", """SELECT feed AS 來源, channel AS 渠道, last_ok_at AS 上次成功, extract(epoch FROM now() - last_ok_at)::int AS 秒前, backoff_level AS 退避, outcome AS 最後結果, next_poll_at AS 下次, error AS 錯誤
 FROM collector_state WHERE last_ok_at IS NULL OR last_ok_at < now() - interval '15 minutes' ORDER BY last_ok_at NULLS FIRST""",
              desc="15 分鐘沒成功且沒在退避 → warn（CollectorFeedStale）；1 小時 → page（CollectorFeedDead）", w=24, h=6))

C.row("4. 帳號預算與退避（每個來源每小時請求數；退避等級持久化在 collector_state）")
C.add(*ts("每個來源每小時的請求數", [('sum by (feed) (increase(collector_requests_total{feed=~"$feed", channel=~"$channel"}[1h]))', "{{feed}}")], w=8, desc="同一主機間隔 ≥ 2.5 秒；YouTube Data API 每天 10,000 單位配額"))
C.add(*ts("退避等級（0＝正常；每次 429／5xx／login_wall 加 1，成功歸零）", [('collector_backoff_level{feed=~"$feed"}', "{{feed}}")], w=8, thresholds=[(1, "orange"), (3, "red")]))
C.add(*ts("YouTube 配額用量（單位，累計）與 robots.txt 變更", [("collector_youtube_quota_units_total", "YouTube 配額"), ("sum by (host) (increase(collector_robots_changed_total[1d]))", "robots 變更 {{host}}")], w=8,
          desc="playlistItems.list 每次 1 單位；robots.txt 每天比對 sha，一變就 ticket（CollectorRobotsChanged）並加註記"))

C.row("5. 延遲分段（phase＝dns／connect／tls／ttfb／total；本機 requests 只量 total 與 ttfb，其餘要 curl 風格的 hook）")
C.add(*ts("請求延遲 p95（依平台 × 階段）", [('histogram_quantile(0.95, sum by (le, channel, phase) (rate(collector_request_seconds_bucket{channel=~"$channel"}[$__rate_interval])))', "{{channel}} {{phase}}")], unit="s", w=12,
          desc="兩個來源數字不同時以 Loki 逐筆為準（面板說明寫明，計畫書的教訓）"))
C.add(*heatmap("總延遲分布", 'sum by (le) (increase(collector_request_seconds_bucket{phase="total", channel=~"$channel"}[5m]))', w=12))

C.row("6. 監控自身（採集器心跳、Loki 寫入量、Alloy、Prometheus 目標）")
C.add(*stat("採集器 up", 'up{job="collector"}', thr=[(1, "green")], reverse=True, color_mode="value", desc="CollectorDown：5 分鐘", w=4))
C.add(*stat("最後一次成功抓取（距今）", "time() - max(collector_last_ok_timestamp_seconds)", unit="s", thr=[(900, "orange"), (3600, "red")], w=4))
C.add(*ts("Loki 收到的日誌量（bytes/s）與 Alloy 送出", [("sum(rate(loki_distributor_bytes_received_total[$__rate_interval]))", "Loki 收到"), ("sum(rate(loki_write_sent_bytes_total[$__rate_interval]))", "Alloy 送出")], unit="Bps", w=8))
C.add(*ts("抓取目標 up", [('up{job=~"collector|alloy|loki|wordcloud-api"}', "{{job}}")], w=8, min_=0, max_=1))
C.add(*logs("採集器日誌（每次請求一行：outcome、posts_parsed、final_url、延遲分段）", '{service="collector"} | json | event="collect"', w=24, h=8))

if __name__ == "__main__":
    here = Path(__file__).resolve().parent / "dashboards"
    for b in (MAIN, C):
        n = b.dump(here / f"{b.uid}.json")
        print(here / f"{b.uid}.json", n, "panels")
