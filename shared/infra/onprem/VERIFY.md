# 地端 stack 實跑驗證紀錄（docker compose）

> 日期：2026-10-30。執行環境：雲端容器（Linux，16 GiB RAM，Docker 29.9.2、Docker Compose v5.6.1）。
> 「抓」只讀 repo 內的 fixture（`shared/capture/html/`、`q6/data/ptt_titles_7d.jsonl`），**沒有連任何真實網站**。
> 帳密全部是 `.env.example` 的示範值，只能本機用。

## 架構：同一條 6 段管線，換成地端元件

| 段 | 服務（`docker-compose.yml`） | 對應的 GCP 資源（`shared/infra/gcp`） | 這裡怎麼做 |
|---|---|---|---|
| 排 | `scheduler`（`qsearch_onprem.scheduler loop`） | Cloud Scheduler `crawl-tick`、`ptt-crawl-hourly` → scheduler Cloud Run | 每個時間窗產生 deterministic `job_id`，`crawl_jobs` 表 `INSERT ... ON CONFLICT DO NOTHING`，同一窗重跑不會重派 |
| 派 | `rabbitmq` 3.13.7（quorum queue `crawl.ptt`） | Cloud Tasks `crawl-ptt`（每站一條佇列）＋ Pub/Sub `crawler-tasks` | `x-delivery-limit=4`（共 5 次）→ DLX `qsearch.dlx` → `crawl.ptt.dlq`；速率用 Redis 每秒計數器（固定視窗，`CRAWL_RATE_PER_SECOND=5`） |
| 抓 | `worker` | Cloud Run `crawler-worker`／Job `ptt-crawl` | 讀 fixture，寫 MinIO，`crawl_jobs.status=fetched`，再發 `crawl.fetched` 事件（同一份 Avro schema 驗證） |
| 存 | `minio`（bitnamilegacy/minio 2025.4.22） | GCS `raw-scraped` | 物件 key `ptt/<board>/dt=YYYY-MM-DD/<job_id>.<ext>`，metadata 帶 sha256 |
| 解 | `parser` ＋ `postgres`（15.19 ＋ pg_bigm 1.2，自行建置） | Cloud Run `parser` ＋ Pub/Sub `qsearch-parser-sub` ＋ Cloud SQL（`cloudsql.enable_pg_bigm`） | inbox 表 `UNIQUE(event_id)` 去重；upsert `ptt_posts`；title／body 有 pg_bigm GIN 索引 |
| 用 | `clickhouse` 24.8、`opensearch` 2.19.2、`redis` 7.4.6（AOF） | BigQuery `ingest_events`、Elasticsearch、Firestore／Memorystore | ClickHouse MergeTree 依日分區、TTL 90 天；OpenSearch 以 `post_id` 當 `_id`；Redis 存速率與最後抓取時間 |

**Pub/Sub Seek 的替代**：RabbitMQ 已 ack 的訊息不保留，所以回放改用「`crawl_jobs` 表 ＋ MinIO 原始檔」重新產生事件（`qsearch_onprem.replay`）。
`--mode redeliver` 重送同一批 `event_id`（下游靠 inbox 去重），`--mode reparse` 強制從 MinIO 重讀重解析（Q6 換斷詞後回朔就是這種），兩種都可以加 `--since` 對應「Seek 到時間點」。

## 怎麼跑

```bash
# 一鍵：下載 pg_bigm 原始碼 → down -v → build → up --wait → smoke → 收集 stats/佇列/日誌 → down -v
bash shared/infra/onprem/scripts/run_e2e.sh /tmp/qsearch-onprem-verify

# 或手動
cd shared/infra/onprem
python3 -I postgres-bigm/fetch_pg_bigm.py         # 下載並核對 sha256，放在 vendor/（不進版控）
docker compose -p qsearch-onprem --env-file .env.example up -d --build --wait
docker compose -p qsearch-onprem --env-file .env.example run --rm smoke
docker compose -p qsearch-onprem --env-file .env.example down -v
```

主機埠只綁 `127.0.0.1:18100–18199`。本次容器的出口走 TLS 攔截 proxy，所以建置時另設了 `BUILD_CA_BUNDLE`（見 `.env.example` 註解）；一般環境不需要。

## 結果

### 腳本各步驟（第 8 次，`steps.tsv`）

| 步驟 | 結束碼 | 秒 |
|---|---|---|
| 01 下載 pg_bigm（已在 vendor/，只核對） | 0 | 0.1 |
| 03 build | 0 | 1.5（有快取） |
| 04 `up -d --wait` | 0 | 19.3 |
| 06 smoke | 0 | 18.9 |
| 12 `down -v` | 0 | 13.8 |

### smoke test（`app/tests/test_smoke.py`，19 項）

| 次 | 結果 | 備註 |
|---|---|---|
| 第 4 次 | 19 passed | |
| 第 5 次 | 18 passed，1 failed | `test_17` 間歇失敗，見下方「修過的問題」 |
| 第 6、7、8 次 | 19 passed（三次連續） | 修正 `parser.py` 後 |

每一項驗的事（數字取自第 8 次的 `smoke_summary.json`）：

| 測項 | 實測 |
|---|---|
| 排：同一時間窗 tick 兩次 | 第一次 inserted 4／published 4，第二次 0／0 |
| 管線排空 | tick 到 4 個 job 全部解析完 2.66 秒 |
| 存：MinIO | 4 個物件，key 帶 `dt=2026-10-31` |
| 事件格式 | 發出的事件通過 `qsearch-crawl-event-v1.avsc`（與 GCP 共用） |
| PostgreSQL | `ptt_posts` 6,724 列；`pg_bigm` extversion 1.2；`show_bigm('蔡康永')` = 康永、永␠、蔡康、␠蔡 |
| pg_bigm 走索引 | `title LIKE '%蔡康永%'` 33 列，`Bitmap Index Scan on ptt_posts_title_bigm_idx`；body 查詢也走 `ptt_posts_body_bigm_idx` |
| ClickHouse | 4 列、1 個分區、`sum(post_count)=6724`；`MergeTree PARTITION BY toDate(ingested_at) ... TTL +90 天` |
| OpenSearch | 6,724 份文件，`match_phrase 蔡康永` 33 筆 |
| Redis | `appendonly=yes`、`aof_last_write_status=ok` |
| 抓取失敗進 DLQ | 不存在的 fixture：5 次後進 `crawl.ptt.dlq`，`x-death reason=delivery_limit` |
| 解析失敗進 DLQ | 非 JSON 事件：5 次後進 `qsearch.parser.dlq` |
| 重複發佈 | 同一 `event_id` 再送兩次：deliveries 1→3，六個儲存的筆數都不變 |
| `replay --mode redeliver` | 4 個重送，0.62 秒，筆數不變，processed_count 不變 |
| `replay --mode reparse` | 4 個重解析，2.74 秒，processed_count 4→8，筆數不變 |
| 從原始檔重建 | 清空 PG 文章／inbox、ClickHouse、OpenSearch 後 reparse：2.1 秒恢復成 6,724／4／6,724 |

### 資源（`docker stats`，第 8 次，閒置時）

OpenSearch 914 MiB、MinIO 230 MiB、RabbitMQ 184 MiB、ClickHouse 164 MiB、parser 67 MiB、PostgreSQL 51 MiB、worker 37 MiB、scheduler 26 MiB、Redis 4 MiB，合計約 1.6 GiB。映像合計約 5.2 GB（OpenSearch 2.5 GB 最大）。

### pg_bigm 放大測試（手動，第 2 次）

在同一個 PostgreSQL 把 `ptt_posts` 的標題複製成 201,720 列的 `bigm_scale_check` 表（測完 DROP），建 pg_bigm GIN 索引後比較：

| 項目 | 結果 |
|---|---|
| 建 GIN 索引 | 3.17 秒，索引 17 MB（表 21 MB） |
| `LIKE '%蔡康永%'`（990 列）走索引 | 0.43 ms |
| 同一查詢強制 Seq Scan | 38.3 ms |

## 修過的問題

1. **RabbitMQ 起不來**（`.erlang.cookie: eacces`）：healthcheck 以 root 先執行 `rabbitmq-diagnostics`，搶先建立了 cookie 檔。改成 `test -f /var/lib/rabbitmq/.erlang.cookie && rabbitmq-diagnostics -q check_port_connectivity`。
2. **`test_17` 間歇失敗**（清空後 `os_docs` 還有 102）：`processed_count` 在 PostgreSQL 階段就 +1，`replay --wait` 以它判斷完成，OpenSearch bulk 還在途時就返回。改成寫完 OpenSearch 才 +1（`parser.py` 有註解），之後連續三次 19/19。

## 沒有證明的事

- **真實抓取**：worker 只讀 fixture，不碰真實網站、代理池、出口 IP、封鎖行為（Q4 的核心問題）。
- **高可用**：每個服務都是單機單副本。RabbitMQ quorum queue 只有 1 個節點，MinIO 沒有 erasure coding，PostgreSQL 沒有 replica，沒有備份還原演練。
- **規模**：資料只有 6,724 篇（加上 20 萬列的 pg_bigm 放大測試）。Q2 的每日量、ClickHouse 批次寫入、OpenSearch 分片都沒測。
- **中文斷詞**：OpenSearch 用內建 `cjk` analyzer（中日韓字元 bigram，與 pg_bigm 同概念），沒有裝 IK／smartcn，Q6 的斷詞方案沒驗證。
- **安全**：沒有 TLS、沒有帳號分權，帳密是示範值。
- **監控**：沒有 Prometheus／Grafana。

## 與 GCP 版的差異（給架構圖用）

| 功能 | GCP | 地端（本 stack） |
|---|---|---|
| 每站速率 | Cloud Tasks `max_dispatches_per_second` 內建 | Redis 每秒計數器（固定視窗），自己寫 |
| 重試＋DLQ | Cloud Tasks retry／Pub/Sub dead_letter_policy | quorum queue `x-delivery-limit` ＋ DLX（實測 5 次後進 DLQ） |
| 回放 | Pub/Sub Seek（保留已 ACK 訊息 7 天） | `crawl_jobs` ＋ MinIO 原始檔重新產生事件（實測可重建全部衍生資料） |
| 冪等 | 需在 Cloud SQL 用 `UNIQUE(event_id)`（GCP parser 尚未實作） | inbox 表 `UNIQUE(event_id)`，已實測 |
| 中文 LIKE 搜尋 | Cloud SQL `cloudsql.enable_pg_bigm`（Terraform 已設 flag，`CREATE EXTENSION` 未實測） | 自建 pg_bigm 1.2，已實測走索引 |
| 分析 | BigQuery（Pub/Sub BQ 訂閱逐筆寫） | ClickHouse（本 stack 逐事件一列；量大時要改批次） |
