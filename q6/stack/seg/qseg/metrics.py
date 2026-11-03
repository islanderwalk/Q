from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram, Gauge, generate_latest, start_http_server

MSGS = Counter("qseg_messages_total", "串流訊息數", ["pipeline", "result"])
DOCS = Counter("qseg_docs_total", "寫入結果（created/updated/noop/errors）", ["pipeline", "mode", "result"])
LAG = Histogram("qseg_e2e_lag_seconds", "Pub/Sub 發佈到 ES 寫入完成的延遲", ["pipeline"],
                buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120))
SEG_SEC = Histogram("qseg_segment_seconds", "每批斷詞耗時", ["pipeline", "mode"])
BULK_SEC = Histogram("qseg_bulk_seconds", "每批 bulk 寫入耗時", ["pipeline", "mode"])
DICT = Gauge("qseg_dict_info", "目前載入的字典版本", ["pipeline", "dict_version", "seg_ver", "build"])
# P0-2：ES 覆蓋檢查（coverage.py 每隔一段時間算一次，給 Prometheus 抓）
COV_DOCS = Gauge("qseg_coverage_docs", "覆蓋檢查：各渠道文章數", ["channel"])
COV_UNSEG = Gauge("qseg_coverage_unsegmented_docs", "覆蓋檢查：fetched_at 落在 1 小時到 15 分鐘前、沒有 seg_ver_v2 的文章數（未斷詞）", ["channel"])
COV_NO_PUB = Gauge("qseg_coverage_missing_published_at_docs", "覆蓋檢查：沒有 published_at 的文章數", ["channel"])
COV_AB = Gauge("qseg_coverage_terms_v2_ratio", "覆蓋檢查：有 terms_v2 的文章數 ÷ 文章數（A/B 覆蓋率）", ["channel"])
COV_OK = Gauge("qseg_coverage_last_success_timestamp_seconds", "覆蓋檢查最後一次成功的 Unix 時間")
COV_ERR = Counter("qseg_coverage_errors_total", "覆蓋檢查失敗次數（ES 查詢或解析失敗）")
# P0-4：串流積壓年齡（backlog.py 追蹤「已收到、尚未 ack／nack」的訊息）
# P0-5：字典發版定向重跑的實際寫入數（rerun.py；一次性 Job 結束後抓不到，正式數字看 dict_releases.rerun_written）
RERUN_DOCS = Counter("qseg_rerun_docs_total", "定向重跑寫入結果（created/updated/noop/errors；rejected 為 errors 的子集，ES 429 限流）", ["dict_version", "result"])
OLDEST_UNACKED = Gauge("qseg_oldest_unacked_publish_ts", "尚未 ack／nack 的最舊訊息之 publish_time（Unix 秒）；沒有未結訊息時＝現在，年齡為 0", ["pipeline"])


# 同一個模組定義全部指標，但每個常駐服務只能曝露自己擁有的：真實環境驗證抓到 seg-v0／v1／v2 也把
# qseg_recon_last_success_timestamp_seconds、qseg_quality_whole_word_recall 等「別人的」無標籤 gauge 以 0 曝露出去，
# ReconStale／QualityStale／QualityWholeWordRecallLow 每個 instance 各響一次（5 倍誤響）。owner → 自己的指標前綴。
OWNERS = {
    "coverage": ("qseg_coverage_",),
    "recon": ("qseg_recon_", "qseg_ledger_", "qseg_backfill_", "qseg_service_", "qseg_rerun_runs", "qseg_rerun_running_age",
              "qseg_release_outbox"),
    "quality": ("qseg_quality_",),
}
_BATCH_PREFIXES = tuple(p for ps in OWNERS.values() for p in ps)


class _Filtered:
    def __init__(self, keep):
        self.keep = keep

    def collect(self):
        for fam in REGISTRY.collect():
            if self.keep(fam.name):
                yield fam


def registry_for(owner: str = None) -> CollectorRegistry:
    """owner=None 是串流斷詞服務：曝露除了批次服務（coverage／recon／quality）以外的全部；
    owner="recon" 等只曝露自己的 qseg_ 指標。process_／python_ 這類非 qseg_ 指標每個服務都曝露。"""
    if owner is None:
        keep = lambda name: not name.startswith(_BATCH_PREFIXES)   # noqa: E731
    else:
        mine = OWNERS[owner]
        keep = lambda name: name.startswith(mine) or not name.startswith("qseg_")   # noqa: E731
    reg = CollectorRegistry()
    reg.register(_Filtered(keep))
    return reg


def exposed_names(owner: str = None) -> set:
    """registry_for(owner) 會曝露的指標名（測試用；與 /metrics 輸出同一條路）。"""
    return {line.split("{")[0].split(" ")[0] for line in generate_latest(registry_for(owner)).decode().splitlines()
            if line and not line.startswith("#")}


def serve(port: int, owner: str = None):
    start_http_server(port, registry=registry_for(owner))
SPLIT = Counter("qseg_must_keep_split_total", "串流斷詞時必不可拆的詞被切開的次數（依渠道；回補 G2 也看同一件事）", ["pipeline", "channel"])
TOMBSTONES = Counter("qseg_tombstones_total", "串流收到的刪文訊息（寫 deleted_at；result=updated／missing／errors）", ["pipeline", "result"])
EDITED = Counter("qseg_edited_total", "串流收到的改文訊息（edited=1：content_sha 不同，重寫本體與詞表）", ["pipeline"])
BULK_RETRIED = Counter("qseg_bulk_retries_total", "ES bulk 被 429 拒絕後重送的次數", ["pipeline", "mode"])
# 字典發版收斂（control.py 每台輪詢 manifest；recon.py 從 service_state 算）
DICT_SWAPS = Counter("qseg_dict_swaps_total", "服務熱換字典的次數（source=control：發版訊息；manifest：輪詢 current/manifest.json 發現新版）", ["pipeline", "source"])
SERVICE_VERSIONS = Gauge("qseg_service_dict_versions", "1 分鐘內有心跳的執行個體載入的不同 dict_version 數（> 1 表示還沒收斂）", ["pipeline"])
SERVICE_ALIVE = Gauge("qseg_service_instances_alive", "1 分鐘內有心跳的執行個體數", ["pipeline"])
RERUN_RUNNING_AGE = Gauge("qseg_rerun_running_age_seconds", "最久的 loading／running 定向重跑已經跑了多久（秒；沒有＝0）")
RERUN_RUNS = Gauge("qseg_rerun_runs", "rerun_runs 各狀態的筆數（最近 7 天）", ["status"])
# 文字雲品質日報（quality.py，每天一次；計畫書 P0 第 9 項）
QUALITY_SAMPLED = Gauge("qseg_quality_docs_sampled", "品質日報抽樣篇數（最近 QUALITY_WINDOW 內、有 terms_v2）", ["channel"])
QUALITY_LEAK = Gauge("qseg_quality_leak_ratio", "抽樣裡詞表有洩漏的篇數比例（kind=url／phone／number／stopword／must_keep_split）", ["channel", "kind"])
QUALITY_PLACE = Gauge("qseg_quality_place_split_ratio", "原文含地名（使用者字典裡縣市區鄉鎮村里結尾的詞）的抽樣篇數中，地名被切開的比例", ["channel"])
QUALITY_WHOLE = Gauge("qseg_quality_whole_word_recall", "查詢詞整詞召回（整詞成為 token 的篇數 ÷ 片語比對篇數，micro 平均；目標 ≥ 99%）")
QUALITY_WHOLE_WORDS = Gauge("qseg_quality_whole_word_words", "整詞召回算了幾個詞（片語比對 ≥ QUALITY_MIN_DOCS 篇）")
QUALITY_GOLD = Gauge("qseg_quality_gold_recall", "150 題金標詞召回（嚴格比對；q6/code/recall_eval.py 同一份金標）", ["pipeline"])
QUALITY_MCNEMAR_P = Gauge("qseg_quality_mcnemar_p", "金標召回配對檢定的 p 值（精確二項式；pair=v1_v2／v0_v2）", ["pair"])
QUALITY_SURGES = Gauge("qseg_quality_new_word_surges", "今天 terms_v2 對前 7 天 z > 3 的詞數（kind=all；fragment＝整詞率 < 50%，新詞被切碎）", ["kind"])
QUALITY_RBO = Gauge("qseg_quality_rbo", "前 50 詞的 RBO（pair=prev_day：對昨天；v1_v2：A/B 距離）", ["channel", "pair"])
QUALITY_JS = Gauge("qseg_quality_js_divergence", "前 50 詞分布對昨天的 JS 散度（0～1）", ["channel"])
QUALITY_OK = Gauge("qseg_quality_last_success_timestamp_seconds", "品質日報最後一次成功的 Unix 時間")
QUALITY_ERR = Counter("qseg_quality_errors_total", "品質日報失敗次數")
RELEASE_OUTBOX_PENDING = Gauge("qseg_release_outbox_pending", "發版 outbox 裡還沒送出去的控制訊息數（API 每 10 秒重試）")
# 完整性四層對帳與回補燃盡（recon.py；計畫書 P0 第 2、8 項）
RECON_DOCS = Gauge("qseg_recon_docs", "對帳：每個渠道×月的篇數，layer=raw（原文湖不重複 post_id）／es／es_with_v2／es_at_latest_v2／es_deleted／ledger（written＋noop）", ["layer", "channel", "month"])
RECON_MISSING = Gauge("qseg_recon_missing_ratio", "對帳：原文有、ES 沒有的比例（max(0, raw −（es − 已刪）) ÷ raw）；只有原文層存在的分區才有", ["channel", "month"])
RECON_EXTRA = Gauge("qseg_recon_extra_docs", "對帳：ES 有、原文沒有的篇數（串流進來的、重複 post_id、復活的刪文）", ["channel", "month"])
RECON_PASSED = Gauge("qseg_recon_partition_passed", "對帳：該分區的回補帳本是否 passed（1／0）", ["channel", "month"])
RECON_OK = Gauge("qseg_recon_last_success_timestamp_seconds", "對帳最後一次成功的 Unix 時間")
RECON_ERR = Counter("qseg_recon_errors_total", "對帳失敗次數（ES／PG 查詢或寫入失敗）")
LEDGER_PARTS = Gauge("qseg_ledger_partitions", "回補帳本各狀態的分區數", ["status"])
LEDGER_EXHAUSTED = Gauge("qseg_ledger_attempts_exhausted", "attempts ≥ 3 仍未 passed 的分區數（OOM 後反覆被重領）")
LEDGER_OLDEST_PM = Gauge("qseg_ledger_oldest_awaiting_pm_age_seconds", "最老的 awaiting_pm 分區等了多久（秒；沒有就是 0）")
BACKFILL_REMAINING = Gauge("qseg_backfill_remaining_docs", "回補剩餘篇數（pending／running 的 raw_count − checkpoint_line）")
BACKFILL_RATE = Gauge("qseg_backfill_docs_per_sec_15m", "近 15 分鐘的回補速率（backfill_batches 的篇數 ÷ 900）")
BACKFILL_MAX_DPS = Gauge("qseg_backfill_max_docs_per_sec", "目前的回補限速（backfill_control）")
BACKFILL_PAUSED = Gauge("qseg_backfill_paused", "回補是否暫停（1／0）")
