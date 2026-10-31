# GCP 與地端架構對照

對應 Terraform：`shared/infra/gcp`（離線 plan 102 to add、`terraform test` 30 passed，未 apply）；地端：`shared/infra/onprem`（`docker-compose.yml` 已實際啟動，smoke 19/19，見 `shared/infra/onprem/VERIFY.md`）。

## 1. 元件對照

| 管線段 | GCP 節點 | Terraform 位址（主要） | 地端候選 | 主要差異 |
|---|---|---|---|---|
| 排 | Cloud Scheduler | `google_cloud_scheduler_job.crawl_tick`、`google_cloud_scheduler_job.ptt_crawl_hourly`（Q3 每小時跑 PTT Job） | `scheduler` 服務（已實作，job_id 以時間窗決定、`ON CONFLICT DO NOTHING`） | 地端要自己做 HA；冪等已實測 |
| 排 | scheduler（Cloud Run） | `google_cloud_run_v2_service.scheduler` | FastAPI 服務＋排程器 | 地端沒有自動擴縮 |
| 派 | Cloud Tasks（每站一條） | `google_cloud_tasks_queue.crawl["crawl-fb" / "crawl-yt" / "crawl-news"]`、`google_cloud_tasks_queue.crawl_site["crawl-ptt"（1 rps）/ "crawl-threads"（20 rps）]`；OIDC 呼叫 worker（`tasks_invoker` SA） | RabbitMQ quorum queue `crawl.ptt` ＋ Redis 每秒計數器 | per-queue 速率要自己寫（已實作固定視窗） |
| 派 | Pub/Sub crawler-tasks＋DLQ | `google_pubsub_topic.crawler_tasks`、`google_pubsub_subscription.crawl_sub`、`google_pubsub_topic.crawler_tasks_dlq` | RabbitMQ `x-delivery-limit` ＋ DLX | 5 次後進 DLQ 已實測；沒有 Seek |
| 入口 | Cloud Run crawler-api | `google_cloud_run_v2_service.api` | FastAPI 容器 | — |
| 抓 | Cloud Run crawler-worker（internal ingress）／Job `ptt-crawl` | `google_cloud_run_v2_service.crawler_worker`、`google_cloud_run_v2_job.ptt_crawl` | `worker` 服務（本次只讀 fixture） | IP 來源不同（Q4） |
| 抓 | Cloud NAT 固定 IP | `google_compute_router_nat.crawler`、`google_compute_address.egress` | 出口閘道＋固定 IP／proxy 池 | 固定 IP 需要控制出口 |
| 抓 | GKE browser-pool（方案 B） | `optional/gke`：`google_container_node_pool.browser_pool` | K8s Deployment＋HPA | 雲端節點可縮到 0 |
| 存 | GCS raw-scraped | `google_storage_bucket.raw_scraped` | MinIO（已實作，key 帶 `dt=`、metadata 帶 sha256） | 地端要自己備份 |
| 事件 | Pub/Sub qsearch-crawl-fetched（Avro） | `google_pubsub_topic.qsearch_fetched`、`google_pubsub_schema.qsearch_event_v1` | RabbitMQ／Kafka topic＋JSON schema | schema 強制力不同 |
| 解 | Cloud Run parser | `google_cloud_run_v2_service.parser` | FastAPI consumer | — |
| 解 | Pub/Sub parser sub（Seek 回放） | `google_pubsub_subscription.qsearch_parser_sub` | `crawl_jobs` 表＋MinIO 原始檔回放（`qsearch_onprem.replay`，已實測可重建全部衍生資料） | 回放要自己寫，但已做出替代 |
| 狀態 | Cloud SQL PG15（私有 IP、`cloudsql.enable_pg_bigm`、IAM 登入、只收加密連線、備份＋PITR） | `google_sql_database_instance.crawler_db`、`google_sql_user.iam_sa` | PostgreSQL 15＋pg_bigm 1.2（自建，已實測走 GIN 索引） | 備份與還原要自己演練 |
| 用（分析） | BigQuery ingest_events | `google_bigquery_table.ingest_events`、`qsearch_to_bq` 訂閱 | ClickHouse MergeTree（依日分區、TTL 90 天） | 逐筆寫入 vs 批次寫入 |
| 用（搜尋） | Elasticsearch（infra 未寫；API key 放 Secret Manager） | `google_secret_manager_secret.es_api_key` | OpenSearch 2.19.2（`cjk` analyzer） | 授權與版本相容性 |
| 用（session） | Firestore（infra 未寫） | — | Redis（AOF） | 持久化要自己設定 |
| 回補 | Cloud Run Job qsearch-backfill（專用 SA、可分片 `task_count`） | `google_cloud_run_v2_job.qsearch_backfill` | `replay --mode reparse [--since]` | 要自己管排程 |
| 密鑰 | Secret Manager | `google_secret_manager_secret.proxy_credentials`、`es_api_key` | `.env`（僅開發）；正式要 Vault／sops | 地端沒有代管 |
| 發版 | Cloud Build trigger | `google_cloudbuild_trigger.crawler_api`（1st gen）、`crawler_api_v2`（2nd gen repository，count） | GitLab CI／Jenkins | — |

## 2. 取捨重點（給決策用）

1. **重播（Seek）**：GCP 的 Pub/Sub 可以把已 ACK 訊息保留 7 天（`retain_acked_messages = true`）後倒回重跑。地端最接近的替代是「原始檔放 MinIO ＋ job 表記錄每個檔的解析狀態」，或改用 Kafka／Redis Streams。這影響 Q5（指標回補）與 Q6（斷詞回朔）。
2. **速率控制**：Cloud Tasks 內建 `max_dispatches_per_second` 與重試。RabbitMQ 要靠 consumer 數量與自寫的限速（本 stack 用 Redis 每秒計數器）；這會影響 Q3、Q4 的「不被目標站封鎖」。
3. **出口 IP**：GCP 用 Cloud NAT 固定 IP，且 Q4 題幹指出雲端 IP 段常被列入黑名單。地端 IP 不一定比較乾淨，兩者都要有代理池的備案。
4. **冪等**：Pub/Sub 與 Cloud Tasks 都是至少一次投遞。地端無論用哪個佇列，都要在 DB 層用 `event_id` 唯一鍵或 inbox 表保證，這與 GCP 版相同，不因切換而改變。
5. **維運成本**：地端省掉雲端用量，但增加主機、磁碟、備份、升級、憑證。Q2 的預算 3,000 元／月與「一個人維運」是決定性限制。

## 3. 切換判斷

| 條件 | 建議 |
|---|---|
| 規模小、一個人維運、要快速上線 | GCP（managed 服務降低維運負擔） |
| 需要固定出口 IP 且有機房網路 | 抓取層可考慮地端，其餘留在 GCP |
| 分析資料量大、查詢密集 | 分析層（ClickHouse）可評估，但寫入模式要改成批次 |
| 必須重播／回補大量歷史資料 | GCP 的 Seek 目前仍是優點；地端要先做出替代 |

## 4. 尚未涵蓋

- 地端只驗證了功能（6,724 篇 PTT fixture、20 萬列 pg_bigm 放大測試）；高可用、真實抓取、Q2 規模都沒測。
- GCP 側沒有真實憑證，只做離線 plan、mock 測試與 Pub/Sub 模擬器；OIDC 呼叫、internal ingress、Cloud SQL `CREATE EXTENSION pg_bigm`、IAM DB 登入、BigQuery 訂閱寫入都未實測。
- 兩邊都還沒有 Terraform／compose 的：session refresher、Elasticsearch 叢集本體、Memorystore／Redis（GCP 側）、Vector DB、監控。
