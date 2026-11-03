# Q6 監控：十項 P0 的實作對照

2026-11-02 的監控計畫書列了十項 P0（頭：平台與採集；中：斷詞、回補、發版；尾：PM 頁與客戶；交界：原文湖對 ES 的完整性）。這份文件記每一項做在哪裡、哪條告警、看板哪一列、怎麼重算。**所有門檻都是提案值**，寫在規則檔與面板說明裡，要用第一週的實測校正。嚴重度：`page`＝要人馬上處理、`warn`＝上班時間處理、`ticket`＝排進待辦。

## 元件（全部本機 Docker、零成本；GCP 對應見 README）

| 元件 | 版本 | 做什麼 | 設定 |
|---|---|---|---|
| Prometheus | v3.5.0 | 抓 11 個 job、評估 `monitoring/rules/*.yml`（66 條告警、30 條記錄規則）、收 Loki ruler 的 remote_write | `monitoring/prometheus.yml` |
| Alertmanager | v0.28.1 | 告警 → webhook `POST /api/ops/alert` → `live_events(kind=alert)` → PM 頁頂部橫幅與事件流（零成本通知管道；Email／Telegram／Discord 另加 receiver 即可）。所有嚴重度 `repeat_interval: 1h`，PM 頁把超過 `ALERT_STALE_SECONDS`（預設 2 小時 10 分）沒再收到的告警視為過期不顯示，所以 webhook 漏掉 resolved 時橫幅最多殘留一輪 | `monitoring/alertmanager/alertmanager.yml` |
| Sloth | v0.11.0 | 由 `monitoring/slo/wordcloud-api.yml` 產生多視窗燃燒率規則 `rules/slo.yml`（**產生檔，不要手改**；`./run.sh slo` 重算） | 28 天；SLO-1 ≤180 秒 99%、SLO-2 未命中快取 ≤10 秒 95% |
| Loki ＋ Alloy | 3.5.3 ／ v1.10.0 | 收所有容器的 JSON 日誌（docker 標籤 → `service`）；Alloy 只對 `{service="collector", event="collect"}` 的行（`stage.match`）在取樣前算 `loki_process_custom_crawl_log_lines_total`（Alloy 會加 `loki_process_custom_` 前綴）；Loki ruler 把採集日誌轉成 `crawl:requests:rate5m` 並出 `CrawlLoginWallFromLogs` | `monitoring/alloy/config.alloy`、`monitoring/loki/` |
| elasticsearch_exporter | v1.9.0 | 叢集狀態、磁碟水位、breaker、執行緒池拒絕、JVM、SLM 快照 | `docker-compose.yml` `es-exporter`（9114） |
| Grafana | 12.1.1 | 兩張看板（`build_dashboard.py` 產生、進版控）：主看板 9 層 69 面板、採集看板 6 列 21 面板；變數 seg／pipeline／channel／feed；註記來自 `live_events` | `monitoring/grafana/` |
| 自寫 RUM | `web/rum.js` | PM 頁的 LCP／CLS／INP、JS 錯誤、瀏覽器端 API 往返、首次出雲、丟詞、雲上篇數≠抽屜 → `POST /api/rum` | 不裝 Faro SDK；GCP 上可換 Faro → Alloy `faro.receiver`，指標名不變 |

## 十項 P0 對照

| # | 項目 | 指標（程式） | 告警（`monitoring/rules/`） | 看板 |
|---|---|---|---|---|
| 1 | SLA 燃燒率：3 分鐘 SLO 不漏報 | `wordcloud_duration_seconds{seg,cache,status}`（失敗也 observe，桶含 180／300）、`wordcloud_errors_total{reason=timeout\|partial\|other}`、`wordcloud_over_180s_total`、`wordcloud_inflight_queries`、`wordcloud_cache_total`（`api/app.py`：ES 逾時 (3, 180)、ES 查詢 `timeout: 170s`、label 啟動預建、快取鍵含世代號、single-flight、psycopg 連線池） | `slo.yml` `WordcloudSlo180sBurn`（page 14.4×/6×，ticket 3×/1×）、`WordcloudSlo10sBurn`；`api.yml` `WordcloudOver180s`（ticket）、`WordcloudPartialResults`、`WordcloudApiDown` | 主看板 ① |
| 2 | 完整性四層對帳 | `seg/qseg/recon.py`（seg-recon，每小時）：`qseg_recon_docs{layer=raw\|es\|es_with_v2\|es_at_latest_v2\|es_deleted\|ledger}`、`qseg_recon_missing_ratio`、`qseg_recon_extra_docs`、表 `recon_daily`；`coverage.py` 的未斷詞與缺 `published_at` | `completeness.yml` `ReconMissingDocs`（passed 分區 > 0.1%）、`ReconV2CoverageGap`、`ReconStale`、`ReconUnknownMonthDocs`、`SegUnsegmentedDocs`（page） | 主看板 ③ |
| 3 | 採集產出率與軟封鎖 | `api/collector.py`：`collector_requests_total{feed,channel,outcome}`（outcome 11 種）、`collector_posts_parsed\|new\|updated_total`、`collector_backoff_level`、`collector_last_ok_timestamp_seconds`、`collector_robots_info`、`collector_youtube_quota_units_total`；每次請求一行日誌（`outcome`、`posts_parsed`、`final_url`、`ttfb_ms`） | `collector.yml` `CollectorLoginWall`（page，≥1 次）、`CollectorBlocked`、`CollectorYieldDrop`（7 天前同時段 50%）、`CollectorFeedStale\|Dead`、`CollectorRobotsChanged`；Loki `CrawlLoginWallFromLogs` | 採集看板 1、2、4、6 |
| 4 | 首抓延遲 | `collector_first_seen_lag_seconds`（只算發文 24 小時內；桶含 2700、3600） | `CollectorFirstSeenLagHigh`（p95 > 45 分）、`Critical`（> 60 分，page） | 採集看板 3 |
| 5 | Pub/Sub 積壓與 DLQ | 本機用程序內 `qseg_oldest_unacked_publish_ts`（`backlog.py`）、`qseg_messages_total{result=ack\|nack\|bad}`；GCP 上改看 `subscription/oldest_unacked_message_age`、`dead_letter_message_count` | `stream.yml` `SegBacklogAgeTicket`（10 分）、`SegBacklogAgePage`（45 分）、`SegNackRatioHigh`（1%）、`SegServiceDown` | 主看板 ④ |
| 6 | 影子比對與版本覆蓋 | `shadow_checks` 表（`shadow.py`）；覆蓋率 `qseg_coverage_terms_v2_ratio`、`qseg_recon_docs{layer=es_at_latest_v2}` | `ReconV2CoverageGap`；影子比對目前只有表、沒有排程（未做） | 主看板 ③ |
| 7 | 字典發版收斂 | `recon.py` 從 `service_state`／`rerun_runs`／`release_outbox` 算 `qseg_service_dict_versions`、`qseg_service_instances_alive`、`qseg_rerun_running_age_seconds`、`qseg_rerun_runs`、`qseg_release_outbox_pending`；`control.py` 每台每 15 秒輪詢 `current/manifest.json` 熱換（`qseg_dict_swaps_total{source}`）；`app.py` 先寫 outbox 再切 current，背景每 10 秒重送；`es.py` seg_guard 看 `content_sha`、429 重送 1/2/4 秒 | `release.yml` `DictVersionDivergence`（> 1 版持續 10 分）、`RerunStuck`、`RerunFailed`、`ReleaseOutboxStuck`、`SegNoAliveInstances`（page） | 主看板 ⑤ |
| 8 | 回補燃盡與卡住分區 | `recon.py`：`qseg_backfill_remaining_docs`、`qseg_backfill_docs_per_sec_15m`、`qseg_ledger_partitions{status}`、`qseg_ledger_attempts_exhausted`、`qseg_ledger_oldest_awaiting_pm_age_seconds`、`qseg_backfill_max_docs_per_sec`、`qseg_backfill_paused`；`job.py` G1 比不重複 post_id、認領 `attempts < 3`、心跳背景執行緒 20 秒、G2 比例門檻 0.1%、G3 看線上欄位並允許回升 | `backfill.yml` `BackfillEtaBeyondWindow`（> 8 小時）、`BackfillThroughputLow`（< 1,450 篇/秒一晚）、`BackfillAttemptsExhausted`、`BackfillAwaitingPmTooLong`（4 小時）、`BackfillPausedLong` | 主看板 ⑥ |
| 9 | 文字雲品質日報 | `seg/qseg/quality.py`（seg-quality，每天）：`qseg_quality_leak_ratio{channel,kind}`、`qseg_quality_place_split_ratio`、`qseg_quality_whole_word_recall`、`qseg_quality_gold_recall{pipeline}`（150 題金標 `seg/quality/gold_150.json`）、`qseg_quality_mcnemar_p{pair}`、`qseg_quality_new_word_surges{kind}`、`qseg_quality_rbo{pair}`、`qseg_quality_js_divergence`；表 `quality_daily` | `quality.yml` `QualityLeak`（> 0）、`QualityPlaceSplit`、`QualityWholeWordRecallLow`（< 99%）、`QualityGoldRecallDrop`（< 71%）、`QualityNewWordFragment`、`QualityTopShift`（RBO < 0.5）、`QualityStale` | 主看板 ⑦ |
| 10 | ES 叢集健康 | elasticsearch_exporter；`scripts/setup.py` 另建 fs 快照倉庫 + SLM `daily-posts`、索引 slowlog（查詢 > 60 秒 warn 進 Loki） | `es.yml` `ElasticsearchClusterRed`（page）、`Yellow`（10 分）、`DiskHigh`（80%）、`DiskCritical`（88% 或 `predict_linear` 4 小時內滿，page）、`BreakerTripped`、`Write\|SearchRejections`、`HeapHigh`、`GcTimeHigh`、`UnassignedShards`、`SnapshotFailed\|Stale` | 主看板 ⑧ |

P1 裡已順手做的：PM 頁 RUM（`web.yml` 7 條；主看板 ②）、ES slowlog、robots 雜湊、YouTube 配額、刪文 tombstone 與改文、Loki 日誌面板。

每個抓取 job 都有 `up == 0 or absent()` 的 *Down 規則，因為服務自己回報的指標在服務掛掉時是空向量；Sloth 的多視窗告警沒有 `for`（它自己用兩個窗口），測試對它豁免。

## 怎麼重算

```bash
cd q6/stack
./run.sh slo                              # 改 monitoring/slo/wordcloud-api.yml 後重產 rules/slo.yml（Sloth）
python monitoring/grafana/build_dashboard.py   # 改面板後重產 dashboards/*.json（Grafana 由 provisioning 讀檔）
docker run --rm -v "$PWD/monitoring:/w:ro" --entrypoint sh quay.io/prometheus/prometheus:v3.5.0 -c "promtool check rules /w/rules/*.yml && promtool check config /w/prometheus.yml"
docker compose exec -T pg psql -U qsearch -d qsearch -v ON_ERROR_STOP=1 < pg/migrate_p0.sql   # 既有 volume 補新表（recon_daily、quality_daily、release_outbox 等；可重複執行）
python -m pytest -q -p no:cacheprovider q6/stack/tests q6/code/tests     # 從 repo 根跑
```

打開：Grafana http://127.0.0.1:3000（`QSearch Q6 文字雲`、`QSearch Q6 採集`）、Prometheus http://127.0.0.1:9090/alerts、Alertmanager http://127.0.0.1:9093、Loki 由 Grafana 的 Explore 查、PM 頁 http://127.0.0.1:8080（頂部橫幅＝正在響的告警）。

## 測試在哪裡

`q6/code/tests/test_p0_*.py`：API SLO 與快取、採集 outcome 字彙與首抓延遲、四層對帳、Job 閘門、發版收斂（seg_guard、429、tombstone、outbox）、品質日報純函式（RBO／JS／McNemar／暴增）、RUM、設定一致性（每條告警與每個面板引用的指標都在程式裡定義；看板 SQL 只讀 `pg/init.sql`、`pg/live.sql` 建的表；每條告警有 `for` 與 `summary`；每個 job 有 Down 規則）。離線檢查：promtool 96 條規則、看板 99 條 PromQL 逐條用 promtool 解析、12 條看板 SQL 在 PostgreSQL 16 的實際 schema 上執行過。

## 整套系統實跑（2026-11-02，Linux 容器，全部本機 Docker）

同一個容器（4 核心、15 GB RAM）把整套從頭跑一次：`./run.sh up` → `seed`（v0／v1）→ `backfill`（G1／G2／G3 三道閘門通過）→ `live` → `stream`（六渠道 267 篇全部有 v1 與 v2）→ 來源採集（PTT、中央社、公視；容器對外要經代理，用 `docker-compose.ca.yml` 把宿主機 CA 掛進採集器）→ 告警鏈 Prometheus → Alertmanager → `POST /api/ops/alert` → PM 頁橫幅（firing 與 resolved 都收到，`pm/30_alerts_strip.png`）→ SLM `daily-posts` 手動執行 SUCCESS → Grafana 兩張看板由 provisioning 讀到、Loki 收到 19 個服務的日誌、品質日報與四層對帳的指標都由實跑資料算出。結束時 Prometheus 96 條規則（66 告警＋30 記錄）健康、11 個 job 全部 up。截圖在 `../evidence/stack/{pm,grafana,alertmanager,prometheus}/`。

### 實跑抓到並修掉的 14 個問題

| # | 現象（離線測試看不到） | 修正 |
|---|---|---|
| 1 | ES 建快照倉庫回 500：具名 volume 的目錄擁有者不是 elasticsearch | `es/Dockerfile` 先建 `/usr/share/elasticsearch/snapshots` 並 chown 1000:0（volume 繼承） |
| 2 | 舊字典（v0）建不出來：教育部 xlsx 的儲存格 `t=` 前面有 `s=` 樣式屬性，正規式吃不到 | `dict/legacy/extract_moe.py` 的 `CELL_RE`／`T_ATTR_RE` |
| 3 | elasticsearch_exporter 起不來：v1.9.0 的旗標是 `--collector.*`，不是 `--es.*` | `docker-compose.yml` |
| 4 | ES 看板與規則引用的指標名稱有幾個不存在 | 依 exporter `/metrics` 實際名稱改 `build_dashboard.py` 與 `rules/es.yml` |
| 5 | 「有 v2」用 `exists terms_v2`：ES 對空陣列 `exists` 為 false，斷詞結果空的文件被算成沒斷過 | API、coverage、recon 一律改 `exists seg_ver_vX`；測試同步 |
| 6 | PM 橫幅殘留已解除的告警（webhook 漏到 resolved 就永遠在） | `ALERT_STALE_SECONDS`（預設 2h10m）＋ Alertmanager `repeat_interval: 1h`；`/api/ops/alerts` 回 stale 數 |
| 7 | 採集器 TLS 失敗（容器對外要經代理 CA） | 新增 `docker-compose.ca.yml`（`CA=<宿主機 CA 檔> docker compose -f docker-compose.yml -f docker-compose.ca.yml up`） |
| 8 | 服務存活 gauge 每小時才更新，服務掛了一小時內看不出來 | seg-recon 每 60 秒刷新心跳指標（`idle()`），對帳仍每小時 |
| 9 | coverage／recon／quality 共用一個 registry，三個 `/metrics` 互相漏指標，Prometheus 重複抓 | `qseg/metrics.py` 每服務獨立 registry（`serve(port, owner)`） |
| 10 | 沒有 YouTube 金鑰時 feed 被判 `CollectorFeedDead` | `collector_feed_enabled{feed,channel}`，停用的 feed 不建 last_ok／backoff 序列，規則加 `and on (feed) collector_feed_enabled == 1` |
| 11 | Alloy 指標名多了 `loki_process_custom_` 前綴而且把所有行都算進去 | `stage.match` 只算採集日誌；面板與規則改用 `loki_process_custom_crawl_log_lines_total` |
| 12 | `metric or vector(0)` 產生沒有標籤的幻影序列，`time() - …` 規則永遠成立 | 統一 `(time() - (max(metric) or vector(0))) > N`；`test_p0_configs_consistency.py` 禁止舊寫法 |
| 13 | 冷啟動第一次抓到的舊文被算成首抓延遲，直接 page | 第一次成功不算 lag（`lag_skipped_first_success`） |
| 14 | 沒有請求時 SLI 0/0＝NaN，Sloth 的 4 週 `sum_over_time` 整段 NaN，錯誤預算面板空白 | SLO 規格的分母加 `> 0`（沒有請求就沒樣本）；Prometheus 改用具名 volume `promdata` 並重建 TSDB |

### 實跑結束時仍在響的告警（各有實際原因，不是誤報）

| 告警 | 原因 |
|---|---|
| `ElasticsearchDiskHigh`／`DiskCritical` | 容器磁碟用了 94%（整台 Linux 容器的固定配額，不是 ES 寫滿） |
| `ReconUnknownMonthDocs`、`SegPublishedAtMissing{channel="instagram"}` | Instagram 的 48 篇公開頁樣本沒有 `published_at`（頁面沒給時間），四層對帳歸到「月份不明」 |
| `QualityWholeWordRecallLow` | 整詞召回 0.925 < 提案值 0.99（金標 150 題裡有地名被拆） |
| `ElasticsearchSnapshotStale` | SLM 每日排程還沒到第一次自動執行（手動執行的那次 SUCCESS） |

### 這輪新增的設定

- `docker-compose.ca.yml`：宿主機的 CA 檔掛到採集器 `/run/ca/ca-bundle.crt` 並設 `REQUESTS_CA_BUNDLE`／`SSL_CERT_FILE`；只有對外要經代理時才疊上。
- `promdata` 具名 volume：Prometheus 的 TSDB 不再隨容器消失；要清空就 `docker volume rm qsearch-q6_promdata`。
- `ALERT_STALE_SECONDS`（API）：橫幅過期秒數，預設 7,800。
- `collector_feed_enabled{feed,channel}`：沒金鑰的 YouTube feed 為 0，告警不把它當死。
- `loki_process_custom_crawl_log_lines_total`：Alloy 由採集日誌算出的行數，Loki ruler 的 `crawl:requests:rate5m` 用它。

## 已知限制與沒做的

- YouTube 走 Data API v3 `playlistItems.list`，要 `YOUTUBE_API_KEY`（或 `YOUTUBE_API_KEY_FILE`）；沒有金鑰就記 `skipped`。Facebook／Instagram／Threads 沒有公開 feed，只有抓過的公開頁樣本可重播；刪文 tombstone 只有逐頁抓的爬蟲才看得到，feed 採集器看不到。
- 影子比對（P0 第 6 項）只有表與程式，沒有排程與面板。新詞暴增要「今天」有文章才有東西（本機語料到採集時間為止）。
- `rules/slo.yml` 是 Sloth 產生檔；`alerts.yml` 已拆成依關注面的 `rules/*.yml`。
- 每月一個 ES 索引＋別名（回補與查詢分開）只評估沒做：單節點單索引 3 分片在 50 萬篇下夠用，5 億篇才需要。
- 舊的 `P0_NOTES.md`（23 項審查判定）已併進這裡與程式註解；全文在 git 歷史。
