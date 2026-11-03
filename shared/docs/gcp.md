# GCP Terraform 驗證紀錄（第一輪）

## 摘要

- Terraform 根模組：`shared/infra/gcp`（第一輪在本機 Windows 工作樹執行，當時路徑是 `infra\gcp`）
- 驗證：fmt 通過、init 通過、validate 通過（root 與 optional/gke）。
- **plan 已執行：退出碼 0，`Plan: 70 to add, 0 to change, 0 to destroy.`**（project `project-823e124b-87e9-4f9e-a34`，project_number `425708154183`）。
- **沒有 apply，沒有啟用 API，沒有建立任何資源。**
- 完整驗證紀錄與 NOTES 未驗證項逐條結果：`shared/infra/gcp/README.md`（第二輪結果見同檔「第二輪驗證」）。
- 地端對照：`shared/infra/onprem/README.md`、`shared/architecture/gcp-vs-onprem.md`。第一輪的 `docker-compose.example.yml` 當時沒有啟動（本機沒有 docker）；第二輪改成可實際啟動的 `shared/infra/onprem/docker-compose.yml`，實跑紀錄見 `shared/infra/onprem/VERIFY.md`。
- plan 檔與 plan 日誌留在 repo 外。

## 1. 架構節點 → Terraform 位址（JSON）

節點 id 取自原架構圖的 `diagrams/{poc,a,b,c}.json`。diagrams 的節點沒有明確的 snippet id 欄位，這份對照是依節點 label 對應到 resource 的，請以此為準。

`terraform` 內的位址是 root（`shared/infra/gcp`）的，除非標註 `optional/gke`。`status` 欄位：`planned` = 在 plan 的 70 個新增中；`optional-validated` = 在 `optional/gke`，有 validate 沒有 plan；`external` = 不在 Terraform 內；`gap` = 架構節點沒有 Terraform。

```json
{
  "poc": {
    "trigger":   { "label": "使用者 POST /crawl", "status": "external", "terraform": [] },
    "sched":     { "label": "Cloud Run crawler-api", "status": "planned",
                   "terraform": ["google_cloud_run_v2_service.api"] },
    "dispatch":  { "label": "Pub/Sub crawler-tasks（push + DLQ）", "status": "planned",
                   "terraform": ["google_pubsub_topic.crawler_tasks", "google_pubsub_subscription.crawl_sub", "google_pubsub_topic.crawler_tasks_dlq"] },
    "audit":     { "label": "audit-sub（複本，稽核）", "status": "planned",
                   "terraform": ["google_pubsub_subscription.audit_sub"] },
    "crawl":     { "label": "Cloud Run crawler-worker", "status": "planned",
                   "terraform": ["google_cloud_run_v2_service.crawler_worker"] },
    "gcs":       { "label": "Cloud Storage raw-scraped", "status": "planned",
                   "terraform": ["google_storage_bucket.raw_scraped"] },
    "fetched":   { "label": "Pub/Sub qsearch-crawl-fetched（Avro，JSON）", "status": "planned",
                   "terraform": ["google_pubsub_topic.qsearch_fetched", "google_pubsub_schema.qsearch_event_v1"] },
    "platforms": { "label": "目標平台 API（HTTP 出口）", "status": "external",
                   "terraform": ["google_compute_router_nat.crawler", "google_compute_address.egress"],
                   "note": "節點本身不是資源；出口靠 Cloud NAT 固定 IP" },
    "parser":    { "label": "Cloud Run parser", "status": "planned",
                   "terraform": ["google_cloud_run_v2_service.parser", "google_pubsub_subscription.qsearch_parser_sub"] },
    "ops":       { "label": "Cloud Build（發版）", "status": "planned (count=0 預設)",
                   "terraform": ["google_cloudbuild_trigger.crawler_api"] },
    "db":        { "label": "Cloud SQL crawler-db（PG15，私有 IP）", "status": "planned",
                   "terraform": ["google_sql_database_instance.crawler_db", "google_sql_database.crawler"] },
    "bq":        { "label": "BigQuery sentiment_analytics", "status": "planned",
                   "terraform": ["google_bigquery_dataset.sentiment", "google_bigquery_table.ingest_events", "google_pubsub_subscription.qsearch_to_bq"] }
  },
  "a": {
    "trigger":   { "label": "Cloud Scheduler crawl-tick", "status": "planned",
                   "terraform": ["google_cloud_scheduler_job.crawl_tick"] },
    "sched":     { "label": "Cloud Run scheduler（排程大腦）", "status": "planned",
                   "terraform": ["google_cloud_run_v2_service.scheduler"] },
    "jobdb":     { "label": "Job DB（任務狀態/去重，假設）", "status": "gap",
                   "terraform": [], "note": "沒有 TF；可能由 Cloud SQL crawler 承接，尚未決定" },
    "dispatch":  { "label": "Cloud Tasks crawl-fb/yt/news", "status": "planned",
                   "terraform": ["google_cloud_tasks_queue.crawl[\"crawl-fb\"]", "google_cloud_tasks_queue.crawl[\"crawl-yt\"]", "google_cloud_tasks_queue.crawl[\"crawl-news\"]"] },
    "backfill":  { "label": "Cloud Run Job 回補", "status": "planned",
                   "terraform": ["google_cloud_run_v2_job.qsearch_backfill"] },
    "crawl":     { "label": "Cloud Run crawler（抓取）", "status": "planned",
                   "terraform": ["google_cloud_run_v2_service.crawler_worker"] },
    "gcs":       { "label": "GCS raw-scraped", "status": "planned", "terraform": ["google_storage_bucket.raw_scraped"] },
    "fetched":   { "label": "Pub/Sub fetched（事件）", "status": "planned",
                   "terraform": ["google_pubsub_topic.qsearch_fetched"] },
    "platforms": { "label": "目標平台 API", "status": "external", "terraform": [] },
    "parser":    { "label": "Cloud Run parser", "status": "planned", "terraform": ["google_cloud_run_v2_service.parser"] },
    "bq":        { "label": "BigQuery", "status": "planned", "terraform": ["google_bigquery_table.ingest_events"] },
    "browser":   { "label": "（僅 B）GKE browser-pool", "status": "not in A", "terraform": [] },
    "sessionTick": { "label": "Cloud Scheduler session-refresh", "status": "gap", "terraform": [] },
    "refresher": { "label": "Cloud Run session-refresher", "status": "gap", "terraform": [] },
    "sessions":  { "label": "Firestore session 池", "status": "gap", "terraform": [] },
    "accounts":  { "label": "Secret Manager 帳密", "status": "gap", "terraform": [] }
  },
  "b": {
    "_note": "與 A 相同，另加以下節點",
    "browser":   { "label": "GKE deployment browser（常駐瀏覽器池）", "status": "optional-validated (not planned)",
                   "terraform": ["optional/gke: google_container_cluster.crawler_standard", "optional/gke: google_container_node_pool.browser_pool", "optional/gke: kubernetes_horizontal_pod_autoscaler_v2.browser"] }
  },
  "c": {
    "_note": "與 A 相同，但抓取、解析、回填都在 GKE",
    "crawl":     { "label": "GKE deployment crawler（HPA）", "status": "optional-validated (not planned); Deployment 本體 gap",
                   "terraform": ["optional/gke: google_container_cluster.crawler_autopilot", "optional/gke: kubernetes_horizontal_pod_autoscaler_v2.browser（HPA 目標 browser，不是 crawler）"] },
    "parser":    { "label": "GKE deployment parser", "status": "optional-validated; Deployment 本體 gap",
                   "terraform": ["optional/gke: google_container_cluster.crawler_autopilot"] },
    "backfill":  { "label": "GKE CronJob backfill", "status": "gap", "terraform": [] }
  },
  "not_covered_by_any_snippet": ["Elasticsearch", "pg_bigm 搜尋庫", "Redis", "Vector DB", "Data Layer / Analytics / Export API", "Digest / Search", "parse.completed 主題", "audit consumer"]
}
```

## 2. 每個節點的驗證與 plan 狀態

| 節點 | validate | plan | 備註 |
|---|---|---|---|
| Cloud Scheduler crawl-tick、Cloud Tasks ×3 | 通過 | planned | 補上 scheduler run.invoker 與 enqueuer |
| Cloud Run api／worker／scheduler／parser | 通過 | planned | 映像為 hello 佔位；deletion_protection 預設 true |
| Cloud Run Job qsearch-backfill | 通過 | planned | 雙層 `template { template {} }` |
| Pub/Sub 主題、schema、5 個訂閱、DLQ | 通過 | planned | 保留期上限 31 天（已確認） |
| GCS raw-scraped | 通過 | planned | 30 天轉 COLDLINE；刪除待決定 |
| Artifact Registry crawler | 通過 | planned | — |
| Cloud NAT＋固定 IP | 通過 | planned | MANUAL_ONLY 已確認 |
| Cloud SQL PG15（私有 IP） | 通過 | planned | 需 Private Services Access（已建立） |
| BigQuery 表＋Pub/Sub BQ 訂閱 | 通過 | planned | 表 schema 已補（對應 Avro）；mode NULLABLE 未驗證 |
| Cloud Build trigger | 通過 | 未建立（github_owner 預設空） | 需先在 GCP 完成 GitHub 連線 |
| GKE Autopilot／Standard＋node pool＋HPA | 通過（有 deprecation warning） | **未 plan**（獨立 root） | 需先 apply 主模組；`ip_allocation_policy {}` 未驗證 |

## 3. 地端對照

| 段 | GCP | 地端 |
|---|---|---|
| 排 | Cloud Scheduler | cron／Airflow（需 HA） |
| 派 | Cloud Tasks＋Pub/Sub | RabbitMQ（DLX 當 DLQ，沒有 Seek） |
| 抓 | Cloud Run worker＋NAT | Docker／K8s worker＋出口閘道或 proxy 池 |
| 存 | GCS | MinIO（S3 相容） |
| 解 | Cloud Run parser＋Cloud SQL | FastAPI consumer＋PostgreSQL＋pg_bigm |
| 用 | BigQuery／Elasticsearch／Firestore | ClickHouse／OpenSearch／Redis |

切換時最大的功能損失是 **重播（Seek）**，需要用「原始檔在 MinIO ＋ job 表」或 Kafka 補上，否則 Q5、Q6 的回補策略會變弱。

## 4. 要放進總架構圖的重點（5–8 條）

1. 排→派：Cloud Scheduler 每 15 分鐘敲 scheduler 服務，派工到 Cloud Tasks 三條佇列（fb／yt／news），速率與重試都在佇列層設定。
2. 派→抓：Pub/Sub crawl-sub 以 push（OIDC）觸發 crawler-worker，10 次投遞失敗後進 DLQ（`crawler-tasks-dlq`）。
3. 抓取出口：worker 走 Direct VPC egress 經 Cloud NAT，使用單一保留 IP，目標站白名單只需一個 IP。
4. 事件：`qsearch-crawl-fetched` 綁 AVRO schema（JSON 編碼）；parser 訂閱保留已確認訊息 7 天，可 Seek 回放（保留期上限 31 天）。
5. 解析與分析：parser 以 push 消費事件，同時 Pub/Sub BigQuery 訂閱直寫 `ingest_events`（依天分區，90 天到期，要求分區條件）。
6. 原始檔：GCS raw-scraped 統一權限、public access 強制擋、30 天轉 COLDLINE（與舊 snippet 的 30 天刪除衝突，待決定）。
7. 狀態：Cloud SQL PG15 私有 IP（需 Private Services Access），ENTERPRISE edition。
8. 瀏覽器池（方案 B/C）在 GKE，**只驗證、沒有 plan**；Firestore session 池、帳密、Elasticsearch 等在 TF 之外，圖上要標示為「未覆蓋」。

## 5. 未覆蓋與待決定

- 沒有 Terraform：Job DB、session refresher／Firestore、Secret Manager、Elasticsearch、pg_bigm 搜尋庫、Redis、Vector DB、Data Layer／Analytics／Export API、Digest／Search、parse.completed、audit consumer、GKE 的 Deployment 本體（crawler、parser、browser）與 backfill CronJob。
- 待決定：(a) 原始檔是轉冷還是刪除；(b) worker timeout 60s 與長時間抓取的衝突（應移到 Job／GKE）；(c) Cloud Build 連線方式（`github` 或 `repository_event_config`）；(d) backfill 共用 parser SA；(e) Cloud SQL 是否兼任 Job DB；(f) BigQuery 表 mode（NULLABLE 或 REQUIRED）。
- 未驗證：Autopilot 是否禁止自加節點池；DLQ 是否必須有 subscription 才保留訊息；Cloud Tasks → Cloud Run 所需的 `iam.serviceAccountUser`。

## 6. 附註

- 這份 TF 不是 QSearch 正式基礎設施；`services/parser` 只 log 並 ACK，不是正式解析器。
- Terraform state 含敏感資訊，不要進公開 repo；目前使用 local backend。
- repo 內沒有留下 `.terraform/`、`*.tfstate`、tfplan；`.terraform.lock.hcl` 有留（建議版控）。
