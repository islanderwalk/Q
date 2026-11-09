"""Q3 pipeline 的設定。全部從環境變數 Q3_<欄位名大寫> 讀，預設值就是答案裡的規格。"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields


def _cast(raw: str, default):
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return type(default)(raw)


@dataclass(frozen=True)
class Config:
    # 抓取規格（答案第 1、2 題）
    board: str = "Gossiping"
    base_url: str = "https://www.ptt.cc"
    window_days: int = 7               # 搜尋範圍：過去 7 天
    refresh_after_hours: float = 23.0  # 重抓：7 天內超過 23 小時沒抓的文章（每天至少更新一次）
    refresh_policy: str = "daily"      # daily＝原規格；once24h＝第 6 題的修改建議（發文滿 24 小時再抓一次）
    min_interval_s: float = 1.0        # 單 worker，每秒最多 1 次請求
    request_timeout_s: float = 20.0
    max_attempts: int = 3              # 單一網址最多試 3 次，間隔 5、10 秒
    backoff_base_s: float = 5.0
    pause_after_failures: int = 3      # 連續 3 次失敗：整體暫停
    pause_s: float = 300.0
    reuse_idle_s: float = 60.0         # 閒置超過 60 秒的連線不再沿用，下一個請求重新連線
    user_agent: str = "Mozilla/5.0 (compatible; qsearch-q3-pipeline/1.0)"
    max_list_pages: int = 2000         # 安全上限：一輪最多往回翻幾頁列表
    batch_size: int = 50               # 每批幾篇文章寫一次 BigQuery

    # 寫入前的品質檢查（不過就整批不寫，原始 HTML 仍保留）
    gate_min_batch: int = 20           # 批次太小不判斷比例
    gate_max_parse_fail: float = 0.05  # 200 但找不到內文區塊
    gate_max_empty: float = 0.20       # 內文與留言都是空的
    gate_max_404: float = 0.20         # 實測刪文率約 0.2%，一批超過 20% 多半是被擋或網站異常
    gate_max_shrink: float = 0.10      # 重抓後留言數掉一半以上（且至少少 10 則）的文章比例
    # 整輪結束後的回歸檢查（不過就整輪 rollback）
    run_max_searchable_drop: float = 0.05  # 7 天內可搜尋文章少超過 5%
    run_max_empty_increase: int = 20       # 內文空白的文章多超過 max(20, 這輪處理量 2%)
    run_max_deleted_increase: int = 30     # 刪文多超過 max(30, 這輪處理量 10%)：列表上刪文佔位約 6%，留一點餘裕
    run_max_deleted_ratio: float = 0.10
    run_max_comment_drop: float = 0.05     # 7 天內留言總數少超過 5%

    # BigQuery／GCS
    project: str = "qsearch-q3"
    dataset: str = "ptt"
    bq_endpoint: str = ""              # 有值＝本機 BigQuery 模擬器
    partition_expire_days: int = 30
    raw_backend: str = "gcs"           # gcs｜local｜none
    raw_bucket: str = "qsearch-q3-raw"
    raw_dir: str = "/data/raw"

    # 排程與監控
    schedule_minute: int = 5           # Cloud Scheduler cron "5 * * * *"：每小時第 5 分跑一輪；-1＝改用 interval_s
    interval_s: float = 3600.0
    prune_after_days: int = 30         # list_slots 只留 30 天內的列表頁
    verify_hour_tw: int = 4            # 每日不變量檢查：台灣時間 04 點後的第一輪
    verify_every_run: bool = False
    pushgateway: str = ""              # host:port
    metrics_job: str = "q3_crawler"
    search_port: int = 8300
    search_limit: int = 50
    # 故障演練：例如 "1" 或 "1,3"，讓這一輪的這幾批寫入後故意出錯，看自動 rollback 與下一輪補抓（平常留空）
    drill_fail_batches: str = ""
    drill_fail_at: str = "merge"       # merge＝MERGE 之後；versions＝存完 before-image、MERGE 之前
    # 整輪還原演練：啟動後第一輪結束時當成退步，整輪 rollback（只作用一輪，忘了關也不會每輪都還原）
    drill_regression: bool = False

    @classmethod
    def from_env(cls, **override) -> "Config":
        kw = {}
        for f in fields(cls):
            raw = os.environ.get("Q3_" + f.name.upper())
            if raw not in (None, ""):
                kw[f.name] = _cast(raw, f.default)
        kw.update(override)
        cfg = cls(**kw)
        if cfg.refresh_policy not in ("daily", "once24h"):
            raise ValueError(f"Q3_REFRESH_POLICY 只能是 daily 或 once24h：{cfg.refresh_policy}")
        if cfg.drill_fail_at not in ("merge", "versions"):
            raise ValueError(f"Q3_DRILL_FAIL_AT 只能是 merge 或 versions：{cfg.drill_fail_at}")
        return cfg

    def table(self, name: str) -> str:
        return f"`{self.project}.{self.dataset}.{name}`"

    def table_id(self, name: str) -> str:
        return f"{self.project}.{self.dataset}.{name}"
