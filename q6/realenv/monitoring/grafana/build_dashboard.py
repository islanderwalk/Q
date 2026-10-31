"""產生 Grafana 儀表板 JSON（monitoring/grafana/dashboards/qsearch-q6.json）。
面板分四列，對應 QSearch_ES_GCP_討論筆記 §12、§13.6「把指標放在同一時間軸」：
客戶可見的 API 耗時 → ES 引擎負載 → 回補進度與吞吐（PostgreSQL 帳本）→ 串流與資料庫。"""
import json
from pathlib import Path

P = {"type": "prometheus", "uid": "prom"}
G = {"type": "grafana-postgresql-datasource", "uid": "pg"}
panels, y = [], 0


def row(title):
    global y
    panels.append({"type": "row", "title": title, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "collapsed": False})
    y += 1


def ts(title, exprs, x, w=8, h=8, unit="short", ds=P, sql=None, kind="timeseries", desc=""):
    targets = ([{"datasource": ds, "rawSql": sql, "format": "time_series" if kind == "timeseries" else "table", "rawQuery": True, "editorMode": "code", "refId": "A"}]
               if sql else [{"datasource": ds, "expr": e, "legendFormat": l, "refId": chr(65 + i)} for i, (e, l) in enumerate(exprs)])
    p = {"type": kind, "title": title, "description": desc, "datasource": ds, "gridPos": {"h": h, "w": w, "x": x, "y": y},
         "targets": targets, "fieldConfig": {"defaults": {"unit": unit}, "overrides": []}, "options": {}}
    if kind == "stat":
        p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background"}
        p["fieldConfig"]["defaults"]["thresholds"] = {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}
    panels.append(p)


row("① 客戶可見：文字雲 API（Cloud Run 服務；GCP 上看 Cloud Logging 的 duration_ms 與日誌型指標）")
ts("文字雲查詢耗時 p50／p95（未命中快取）", [
    ('histogram_quantile(0.95, sum by (le, seg) (rate(wordcloud_duration_seconds_bucket{cache="miss"}[1m])))', "p95 {{seg}}"),
    ('histogram_quantile(0.5, sum by (le, seg) (rate(wordcloud_duration_seconds_bucket{cache="miss"}[1m])))', "p50 {{seg}}")], 0, unit="s")
ts("超過 180 秒的查詢（累計）", [("sum(wordcloud_over_180s_total) or vector(0)", "over 180s")], 8, w=4, kind="stat",
   desc="對應 Cloud Logging 日誌型指標 wordcloud_over_180s；> 0 就告警")
ts("API 呼叫 ES 往返耗時 p95", [('histogram_quantile(0.95, sum by (le, seg) (rate(wordcloud_es_call_seconds_bucket[1m])))', "{{seg}}")], 12, w=6, unit="s")
ts("查詢量（每秒）", [('sum by (seg, cache) (rate(wordcloud_duration_seconds_count[1m]))', "{{seg}} {{cache}}")], 18, w=6, unit="reqps")
y += 8
row("② Elasticsearch（OTel elasticsearch receiver＝Ops Agent 的 ES 整合）")
ts("搜尋／寫入速率", [('rate(elasticsearch_node_operations_completed_total{operation="query"}[1m])', "query/s"),
                    ('rate(elasticsearch_node_operations_completed_total{operation="index"}[1m])', "index/s")], 0, w=6, unit="ops")
ts("執行緒池佇列", [('elasticsearch_node_thread_pool_tasks_queued{thread_pool_name=~"search|write"}', "{{thread_pool_name}}")], 6, w=6)
ts("寫入／搜尋拒絕（每分鐘）", [('increase(elasticsearch_node_thread_pool_tasks_finished_total{state="rejected",thread_pool_name=~"search|write"}[1m])', "{{thread_pool_name}}")], 12, w=6)
ts("JVM heap 與磁碟寫入", [("jvm_memory_heap_used_bytes", "heap used"), ("jvm_memory_heap_max_bytes", "heap max"),
                         ("rate(elasticsearch_node_disk_io_write_kibibytes[1m]) * 1024", "disk write B/s")], 18, w=6, unit="bytes")
y += 8
row("③ 回補（Cloud Run Jobs；進度與 checkpoint 存在 PostgreSQL 帳本，不是看日誌行數）")
ts("分區狀態", [], 0, w=6, ds=G, kind="table",
   sql="SELECT status AS 狀態, count(*) AS 分區數, sum(written) AS 已寫入, sum(noop) AS 未變更 FROM backfill_ledger GROUP BY status ORDER BY 1")
ts("回補吞吐（每秒篇數，按 bulk 加總）", [], 6, w=10, ds=G, unit="short",
   sql="SELECT date_trunc('second', at) AS time, sum(docs)::float / 1 AS docs_per_s FROM backfill_batches WHERE $__timeFilter(at) GROUP BY 1 ORDER BY 1")
ts("最近的分區（閘門結果）", [], 16, w=8, ds=G, kind="table",
   sql="SELECT partition_id AS 分區, status AS 狀態, raw_count AS 原文, written AS 寫入, checkpoint_line AS checkpoint, docs_per_sec AS 每秒, (gates->'G2'->>'pass') AS G2洩漏, (gates->'G3'->>'canary_p95_ms_after') AS 探針p95ms FROM backfill_ledger WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 12")
y += 8
row("④ 串流斷詞（Cloud Run 服務訂閱 Pub/Sub）與 PostgreSQL")
ts("發佈到寫進 ES 的延遲 p95", [('histogram_quantile(0.95, sum by (le, pipeline) (rate(qseg_e2e_lag_seconds_bucket[1m])))', "{{pipeline}}")], 0, w=8, unit="s")
ts("串流訊息 ack／nack", [('sum by (pipeline, result) (rate(qseg_messages_total[1m]))', "{{pipeline}} {{result}}")], 8, w=8, unit="short")
ts("PostgreSQL 交易與資料庫大小", [("rate(postgresql_commits_total[1m])", "commits/s"), ("postgresql_db_size_bytes", "db size")], 16, w=8)
y += 8

dash = {"uid": "qsearch-q6", "title": "QSearch Q6 文字雲：查詢、ES、回補、串流", "timezone": "Asia/Taipei", "refresh": "10s",
        "time": {"from": "now-30m", "to": "now"}, "schemaVersion": 41, "panels": panels, "tags": ["qsearch", "q6"]}
out = Path(__file__).resolve().parent / "dashboards" / "qsearch-q6.json"
out.write_text(json.dumps(dash, ensure_ascii=False, indent=1), encoding="utf-8")
print(out, len(panels))
