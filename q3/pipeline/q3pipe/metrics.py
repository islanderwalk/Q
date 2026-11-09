"""監控指標（答案：Cloud Monitoring；本機用 Pushgateway＋Prometheus＋Grafana）。

排程器是常駐程序，計數器跨輪累加，每 15 秒推一次到 Pushgateway；推不上去會印警告，不中斷抓取。
"""
from __future__ import annotations

import sys
import threading
import time

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, push_to_gateway


class Metrics:
    def __init__(self, cfg):
        self.cfg = cfg
        r = self.reg = CollectorRegistry()
        self.requests = Counter("q3_http_requests_total", "送到 PTT 的請求數，每次嘗試都算", ["kind", "status"], registry=r)
        self.latency = Histogram("q3_http_latency_seconds", "PTT 回應時間", ["kind"], registry=r,
                                 buckets=(0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10, 20))
        self.bytes = Counter("q3_http_bytes_total", "PTT 回應大小", ["kind"], registry=r)
        self.posts_new = Counter("q3_posts_new_total", "第一次入庫的文章數", registry=r)
        self.posts_refreshed = Counter("q3_posts_refreshed_total", "重抓後寫回的文章數（依發文第幾天）", ["age_day"], registry=r)
        self.posts_deleted = Counter("q3_posts_deleted_total", "抓到 404 而標記刪文的篇數", ["when"], registry=r)
        self.comments_new = Counter("q3_comments_new_total", "新增的留言數：首抓／重抓，依文章發文第幾天", ["fetch", "age_day"], registry=r)
        self.fetch_failures = Counter("q3_fetch_failures_total", "試完仍失敗、留給下一輪補抓的網址數", ["kind"], registry=r)
        self.batches = Counter("q3_batches_total", "寫入批次", ["result"], registry=r)  # committed/rolled_back/gate_blocked
        self.run_rollbacks = Counter("q3_run_rollbacks_total", "整輪 rollback 次數", registry=r)
        self.ingest_lag = Histogram("q3_ingest_lag_minutes", "文章從發文到第一次可搜尋的分鐘數", registry=r,
                                    buckets=(5, 15, 30, 60, 120, 240, 360, 720, 1440, 4320, 10080))
        g = lambda name, doc, labels=(): Gauge(name, doc, list(labels), registry=r)  # noqa: E731
        self.backoff = g("q3_backoff_seconds", "目前退避等待秒數，0＝正常")
        self.run_in_progress = g("q3_run_in_progress", "1＝正在跑一輪")
        self.run_duration = g("q3_run_duration_seconds", "上一輪花的秒數")
        self.run_last_finished = g("q3_run_last_finished_timestamp_seconds", "上一輪結束時間")
        self.run_status = g("q3_run_status", "上一輪結果，1＝這個狀態", ["status"])
        self.list_pages = Counter("q3_list_pages_read_total", "讀過的列表頁", registry=r)
        self.backlog_new = g("q3_backlog_new_posts", "列表上看到、還沒入庫的 7 天內文章")
        self.backlog_refresh = g("q3_backlog_refresh_posts", "該重抓還沒重抓的文章")
        self.unsearchable_oldest = g("q3_unsearchable_oldest_minutes", "還不能搜尋的文章裡最久的已發文分鐘數（規格：360）")
        self.staleness = g("q3_comment_staleness_max_hours", "7 天內文章距上次抓取最久幾小時（規格：每天至少一次）")
        self.window_posts = g("q3_window_posts", "7 天內可搜尋的文章數")
        self.window_deleted = g("q3_window_deleted_posts", "7 天內已標記刪文的文章數")
        self.window_comments = g("q3_window_comments", "7 天內文章的留言數")
        self.window_truncated = g("q3_window_truncated_posts", "7 天內「檔案過大」留言被截斷的文章數（只記錄不告警）")
        self.verify = g("q3_verify", "每日不變量檢查結果", ["check"])
        self.verify_ok = g("q3_verify_ok", "1＝上次不變量檢查通過")
        self.verify_last = g("q3_verify_last_run_timestamp_seconds", "上次不變量檢查時間")
        self.bq_bytes = Counter("q3_bigquery_bytes_processed_total", "BigQuery 查詢掃描量", ["caller"], registry=r)
        # 帶標籤的計數器先建成 0。序列第一次出現就是 1 的話，increase() 算不到那一次：故障演練那一批還原、
        # 第一次重抓都會從看板上消失。狀態碼沒辦法列舉，只先建 error（連線失敗）。
        for res in ("committed", "rolled_back", "gate_blocked"):
            self.batches.labels(res)
        for when in ("first", "refresh"):
            self.posts_deleted.labels(when)
        for age in map(str, range(8)):
            self.posts_refreshed.labels(age)
            for fetch in ("first", "refresh"):
                self.comments_new.labels(fetch, age)
        for kind in ("list", "article_new", "article_refresh"):
            self.requests.labels(kind, "error")
            self.fetch_failures.labels(kind)
        self._warned_at = 0.0
        self._stop = threading.Event()

    # Fetcher 的 callback
    def on_request(self, kind, status, latency_s, nbytes, **_):
        self.requests.labels(kind, str(status) if status is not None else "error").inc()
        self.latency.labels(kind).observe(latency_s)
        self.bytes.labels(kind).inc(nbytes)

    def set_run_status(self, status):
        for s in ("ok", "partial", "gate_blocked", "rolled_back", "failed"):
            self.run_status.labels(s).set(1 if s == status else 0)

    def push(self):
        if not self.cfg.pushgateway:
            return
        try:
            push_to_gateway(self.cfg.pushgateway, job=self.cfg.metrics_job, registry=self.reg,
                            grouping_key={"board": self.cfg.board}, timeout=5)
        except Exception as e:  # 監控掛了不影響抓取，但要看得到
            if time.time() - self._warned_at > 60:
                self._warned_at = time.time()
                print(f'{{"event":"push_failed","error":"{type(e).__name__}"}}', file=sys.stderr, flush=True)

    def start_pusher(self, every_s=15.0):
        def loop():
            while not self._stop.wait(every_s):
                self.push()
        threading.Thread(target=loop, daemon=True, name="q3-metrics-pusher").start()

    def stop(self):
        self._stop.set()
        self.push()
