# shared/infra/gcp：QSearch 式爬蟲管線的 GCP Terraform 根模組

## 狀態摘要

- **第一輪（本機，對真實專案）**：`terraform plan` 退出碼 0，`Plan: 70 to add, 0 to change, 0 to destroy.`（project `project-823e124b-87e9-4f9e-a34`，project_number `425708154183`）。plan 檔存在 `S\tf\tfplan`（repo 外）。
- **第二輪（2026-10-30，雲端容器，沒有 GCP 憑證）**：補了 32 個資源（Cloud Tasks OIDC、每站限速佇列、Q3 的 PTT 每小時 Job、Cloud SQL 強化與 IAM 登入、Secret Manager、DLQ 不過期等）。
  fmt／validate 退出碼 0；**離線 plan `Plan: 102 to add`**（所有可選開關打開時 109）；`terraform test` **30 passed**；tflint 0 issues。細節見「第二輪驗證」。
- **新增的 32 個資源還沒有對真實專案 plan 過**（容器沒有憑證）。要確認，請在有登入的本機重跑 `terraform plan`，預期是 `Plan: 102 to add`。
- **沒有 apply。** 沒有啟用任何 API、沒有建立任何雲端資源。
- 映像檔全部用 `us-docker.pkg.dev/cloudrun/container/hello`（job 為 `.../job`），所以 plan 不需要先 build 映像。
- 本 README 的 `plan` 結果是 plan 當下的快照。若之後改了變數或程式，需要重跑。

## 檔案結構

| 檔案 | 內容 |
|---|---|
| `shared/infra/gcp/versions.tf` | Terraform >= 1.5；google / google-beta `~> 8.7`（安裝版本 v8.9.0；鎖定檔含 windows／linux／darwin 四個平台的 hash） |
| `shared/infra/gcp/variables.tf` | project_id、project_number（需數字驗證）、region、deletion_protection（預設 true）、映像變數、GCS 轉冷／刪除天數、PTT Job 排程、backfill 分片數、Cloud SQL 備份開關、Cloud Build 2nd gen 參數 |
| `shared/infra/gcp/apis.tf` | 13 個 API（含 Secret Manager）、Pub/Sub 與 Cloud Tasks 服務代理（google-beta） |
| `shared/infra/gcp/iam.tf` | 8 個 service account（另有 legacy 可選 1 個；新增 backfill 專用、Cloud Tasks OIDC 用的 tasks-invoker）、Pub/Sub push 的 token creator、api/worker 的 publisher、GCS 權限 |
| `shared/infra/gcp/network.tf` | VPC、子網、Cloud Router、Cloud NAT（固定 IP）、Private Services Access（Cloud SQL 私有 IP） |
| `shared/infra/gcp/pubsub.tf` | crawler-tasks、crawler-tasks-dlq、qsearch-crawl-fetched（綁 AVRO schema）、parser DLQ、6 個訂閱（DLQ 與 audit 訂閱永不過期、DLQ 保留 31 天）、DLQ 轉送權限 |
| `shared/infra/gcp/schemas/qsearch-crawl-event-v1.avsc` | `crawl.fetched` 事件的 Avro schema（欄位與 lab／parser 一致） |
| `shared/infra/gcp/run.tf` | Cloud Run：crawler-api（對外）、crawler-worker／scheduler／parser（internal ingress）；worker 每實例 4 個並行；scheduler／parser／backfill 走 Direct VPC egress（PRIVATE_RANGES_ONLY）才連得到 Cloud SQL 私有 IP；Job：qsearch-backfill（可分片）、ptt-crawl（Q3） |
| `shared/infra/gcp/scheduler_tasks.tf` | Cloud Scheduler crawl-tick（每 15 分鐘）與 ptt-crawl-hourly（呼叫 Job 的 `:run` API）、Cloud Tasks ×5（crawl-fb／yt／news，加上每站限速的 crawl-ptt 1 rps、crawl-threads 20 rps）、enqueuer 權限與完整 OIDC 鏈（actAs、Cloud Tasks 服務代理、run.invoker） |
| `shared/infra/gcp/storage.tf` | 原始資料 GCS bucket（統一權限、public access 強制擋、30 天轉 COLDLINE）、Artifact Registry `crawler` |
| `shared/infra/gcp/sql.tf` | Cloud SQL PostgreSQL 15、ENTERPRISE edition、私有 IP、資料庫 `crawler`；flag `cloudsql.enable_pg_bigm`、`cloudsql.iam_authentication`；`ssl_mode = ENCRYPTED_ONLY`；每日備份＋PITR 7 天；4 個 runtime SA 的 IAM 資料庫使用者與 `cloudsql.client`／`instanceUser` |
| `shared/infra/gcp/bq.tf` | BigQuery `sentiment_analytics`、分區表 `ingest_events`（欄位對應 Avro）、Pub/Sub BQ 訂閱所需權限 |
| `shared/infra/gcp/secrets.tf` | Secret Manager：住宅代理帳密（worker 可讀）、Elasticsearch API key（parser、backfill 可讀）；**只建容器，不在 Terraform 放密碼值** |
| `shared/infra/gcp/cloudbuild.tf` | Cloud Build trigger：1st gen（`github_owner` 留空時不建立，legacy）與 2nd gen `repository_event_config`＋`service_account`（`cloudbuild_repository_id` 留空時不建立） |
| `shared/infra/gcp/legacy_gce.tf` | 可選：legacy GCE instance template（Shielded VM、OS Login、擋專案 SSH key）+ 區域 MIG + health check + 健康檢查防火牆（`enable_legacy_gce`，預設 false） |
| `shared/infra/gcp/outputs.tf` | Cloud Run URL、Pub/Sub 主題／訂閱 id、bucket、BQ 表、AR repo、固定出口 IP、Cloud SQL 私有 IP |
| `shared/infra/gcp/services/parser/` | 從 lab 複製的 FastAPI parser（`POST /events/crawl-fetched`，目前只 log 並 ACK，**不是正式解析器**）；`Event` 已改成 strict 且六欄必填（與 avsc 一致）、映像以 uid 10001 執行；`tests/`（pytest 119 passed；PR #7 當時 13 項，與 Python 分支合併後 119 項）、`local/`（Pub/Sub 模擬器實跑，見 `local/VERIFY.md`） |
| `shared/infra/gcp/tests/` | `terraform test`（mock provider，不需憑證）：architecture 11、toggles 5、validation 4、hardening 10 個 run |
| `shared/infra/gcp/optional/gke/` | 方案 B／C 的 GKE：Autopilot 叢集、Standard 叢集＋browser-pool 節點池、HPA（**獨立 root**，不併入主 plan）；私有節點、`master_authorized_cidr`（必填）、專用節點 SA、Workload Identity、REGULAR channel |
| `shared/infra/gcp/.terraform.lock.hcl` | provider 鎖定檔（建議版控） |
| `shared/infra/gcp/terraform.tfvars.example` | 變數範本；`terraform.tfvars` 已列入 `shared/infra/gcp/.gitignore` |

## 怎麼跑

在 repo 外指定 `TF_DATA_DIR`，避免 `.terraform/` 寫進 repo：

```bash
export TF_DATA_DIR="<scratchpad>/tf/data"
cd shared/infra/gcp
terraform fmt -check -recursive
terraform init
terraform validate
terraform plan -var project_id=<PROJECT_ID> -var project_number=<PROJECT_NUMBER> -out=<scratchpad>/tf/tfplan
# 不要執行 apply：這份 README 的範圍只到 plan。
```

project_number 用 `gcloud projects describe <PROJECT_ID> --format='value(projectNumber)'` 取得。

GKE 可選模組（另開 root，需先 apply 主模組）：`cd shared/infra/gcp/optional/gke && terraform init && terraform validate`。

## 資源清單（位址 ↔ 架構節點 ↔ 題號）

題號對應：Q2 估算與成本、Q3 PTT ETL、Q4 Threads 爬蟲（出口 IP／代理）、Q5 新指標上線與回補、Q6 斷詞 pipeline 回朔與串流。Q1 是取捨題，沒有對應資源。

| 架構節點（poc／A／int） | Terraform 位址 | 題號 |
|---|---|---|
| 排程：Cloud Scheduler crawl-tick | `google_cloud_scheduler_job.crawl_tick` | Q3 |
| Q3 排程：Cloud Scheduler ptt-crawl-hourly → Cloud Run Job ptt-crawl | `google_cloud_scheduler_job.ptt_crawl_hourly`、`google_cloud_run_v2_job.ptt_crawl`、`google_cloud_run_v2_job_iam_member.ptt_crawl_scheduler` | Q3（與 Q3 答案的 Scheduler → Job 圖一致） |
| 排程大腦：Cloud Run scheduler | `google_cloud_run_v2_service.scheduler` | Q3 |
| 派工：Cloud Tasks crawl-fb／yt／news | `google_cloud_tasks_queue.crawl["crawl-fb" / "crawl-yt" / "crawl-news"]` | Q3, Q4 |
| 每站限速：Cloud Tasks crawl-ptt（1 rps、並行 1）、crawl-threads（20 rps、並行 50） | `google_cloud_tasks_queue.crawl_site["crawl-ptt" / "crawl-threads"]` | Q3, Q4（過度請求會被擋） |
| Tasks → worker 的 OIDC | `google_service_account.tasks_invoker`、`google_service_account_iam_member.scheduler_app_actas_tasks_invoker`、`google_service_account_iam_member.cloudtasks_agent_user`、`google_cloud_run_v2_service_iam_member.worker_tasks_invoker`、`google_project_service_identity.cloudtasks` | Q3 |
| 入口：Cloud Run crawler-api | `google_cloud_run_v2_service.api` | Q3 |
| 派工主題：Pub/Sub crawler-tasks | `google_pubsub_topic.crawler_tasks` | Q3 |
| push 訂閱（重試＋DLQ）：crawl-sub | `google_pubsub_subscription.crawl_sub` | Q3 |
| 稽核複本：audit-sub | `google_pubsub_subscription.audit_sub` | Q3（不監控項目之一） |
| 抓取 worker：Cloud Run crawler-worker | `google_cloud_run_v2_service.crawler_worker` | Q3, Q4 |
| 固定出口 IP：Cloud NAT | `google_compute_router_nat.crawler`、`google_compute_address.egress`、`google_compute_router.crawler`、`google_compute_subnetwork.crawler`、`google_compute_network.crawler` | Q4（**不是**繞過封鎖的方案：固定 IP 本身就是雲端 IP 段。用途是給住宅代理商的 IP 白名單登入，以及 Google 建議爬蟲類流量用專用固定 IP） |
| 原始檔：GCS raw-scraped | `google_storage_bucket.raw_scraped` | Q3, Q5, Q6 |
| 事件：Pub/Sub qsearch-crawl-fetched（Avro，JSON） | `google_pubsub_topic.qsearch_fetched`、`google_pubsub_schema.qsearch_event_v1` | Q5（schema 演進） |
| 解析 parser push：qsearch-parser-sub（保留 7 天，可 Seek） | `google_pubsub_subscription.qsearch_parser_sub` | Q5, Q6 |
| 解析服務：Cloud Run parser | `google_cloud_run_v2_service.parser` | Q3, Q6 |
| 分析寫入：BQ 訂閱 | `google_pubsub_subscription.qsearch_to_bq` | Q5 |
| 分析表：BigQuery sentiment_analytics.ingest_events | `google_bigquery_dataset.sentiment`、`google_bigquery_table.ingest_events` | Q2, Q5 |
| 狀態：Cloud SQL crawler-db（PG15，私有 IP，pg_bigm flag，IAM 登入） | `google_sql_database_instance.crawler_db`、`google_sql_database.crawler`、`google_sql_user.iam_sa[*]`、`google_project_iam_member.sql_client[*]`、`sql_instance_user[*]` | Q2, Q3 |
| 密鑰：Secret Manager | `google_secret_manager_secret.proxy_credentials`、`google_secret_manager_secret.es_api_key` 與 accessor 綁定 | Q4（代理帳密）、Q5／Q6（ES） |
| 回補：Cloud Run Job qsearch-backfill（專用 SA、`backfill_task_count`／`backfill_parallelism` 分片） | `google_cloud_run_v2_job.qsearch_backfill`、`google_service_account.backfill` | Q5, Q6 |
| DLQ | `google_pubsub_topic.crawler_tasks_dlq`、`google_pubsub_topic.parser_dlq`、各 `*_dlq_inspect` 訂閱 | Q3, Q5 |
| 發版：Cloud Build trigger | `google_cloudbuild_trigger.crawler_api`（1st gen，count，預設 0）、`google_cloudbuild_trigger.crawler_api_v2`（2nd gen，count，預設 0） | — |
| 映像倉庫：Artifact Registry crawler | `google_artifact_registry_repository.crawler` | — |
| 方案 B／C：GKE Autopilot | `shared/infra/gcp/optional/gke`：`google_container_cluster.crawler_autopilot` | Q2, Q4（方案比較） |
| 方案 B：browser-pool 節點池 | `shared/infra/gcp/optional/gke`：`google_container_cluster.crawler_standard`、`google_container_node_pool.browser_pool` | Q4 |
| 方案 B：browser HPA | `shared/infra/gcp/optional/gke`：`kubernetes_horizontal_pod_autoscaler_v2.browser` | Q4 |
| legacy GCE（可選） | `google_compute_region_instance_group_manager.crawler_legacy`、`google_compute_firewall.legacy_health_check`（count） | — |

**未覆蓋的架構節點（沒有 Terraform）：** session refresher 與 Firestore session 池、Elasticsearch 叢集本體（只建了 API key 的 Secret）、Redis／Memorystore、Vector DB、Data Layer／Analytics／Export API、Digest／Search。Secret Manager 與 Job DB 的連線權限已在第二輪補上。見下方「未覆蓋」一節。

## 驗證結果（第一輪：本機，指令、退出碼、摘要）

| 步驟 | 指令（TF_DATA_DIR=`S\tf\data`） | 退出碼 | 摘要 |
|---|---|---|---|
| 版本 | `terraform version` | 0 | v1.16.6 windows_amd64 |
| 格式（第一次） | `terraform fmt -check -recursive` | 3 | 需格式化：`shared/infra/gcp/cloudbuild.tf`、`shared/infra/gcp/outputs.tf` |
| 格式（修正） | `terraform fmt -recursive` | 0 | 只改格式（我們自己的檔案） |
| 格式（複查） | `terraform fmt -check -recursive` | 0 | 通過 |
| 初始化（root） | `terraform init` | 0 | google v8.9.0、google-beta v8.9.0 |
| 驗證（root） | `terraform validate` | 0 | `Success! The configuration is valid.` |
| 初始化（optional/gke） | `terraform init` | 0 | kubernetes provider `>= 2.0, < 4.0` |
| 驗證（optional/gke） | `terraform validate` | 0 | 通過，有 1 個 warning（見下） |
| 登入檢查 | `gcloud auth list --filter=status:ACTIVE`、ADC 檔是否存在 | — | 1 個 ACTIVE 帳號；ADC 檔存在；gcloud config project 已設 |
| **計畫** | `terraform plan -var project_id=… -var project_number=425708154183 -out=…\tfplan` | **0** | **`Plan: 70 to add, 0 to change, 0 to destroy.`** 沒有錯誤 |

Plan 過程沒有觸發 quota project 錯誤，所以沒有使用 `user_project_override`。

optional/gke 的 warning：`remove_default_node_pool` 在新版叢集上被標為 deprecated，文件建議省略 `initial_node_count`／`node_config` 或設 `initial_node_count = 0`。**仍未修**（第二輪只做了 harness plan，見下）；使用前需再確認。

**版本落差：** NOTES 寫最新 tag 是 v8.7.0，但 registry 上裝到的是 v8.9.0。文件比對用的是 v8.7.0 的 tag；validate 與 plan 用的是 v8.9.0。兩者的欄位應該相同，但這個差距沒有逐一確認。

## 第二輪驗證（2026-10-30，雲端容器，沒有 GCP 憑證）

Terraform v1.17.1 linux_amd64、google／google-beta v8.9.0。`TF_DATA_DIR`、plan 檔、state 都放在 repo 外。容器沒有任何 GCP 憑證，所以 plan 用假 token 並擋掉對外連線（只有在不需要讀雲端時 plan 才會成功，本模組沒有 data source，所以可以）。

| 步驟 | 指令 | 退出碼 | 結果 |
|---|---|---|---|
| 鎖定檔 | `terraform providers lock -platform=windows_amd64 -platform=linux_amd64 -platform=darwin_amd64 -platform=darwin_arm64`（root 與 optional/gke） | 0 | 原本只有 Windows 的 hash，在 Linux 上 `init -lockfile=readonly` 後 validate 會失敗；補齊四個平台 |
| 格式 | `terraform fmt -check -recursive` | 0 | 通過 |
| 驗證 | `terraform validate` | 0 | `Success! The configuration is valid.` |
| 離線 plan（修改前） | `GOOGLE_OAUTH_ACCESS_TOKEN=fake terraform plan -refresh=false -var project_id=qsearch-offline-check -var project_number=123456789012` | 0 | `Plan: 70 to add`（與第一輪真實專案相同） |
| 離線 plan（修改後） | 同上 | 0 | **`Plan: 102 to add, 0 to change, 0 to destroy.`**（+32，沒有刪掉任何既有資源） |
| 離線 plan（可選全開） | 加 `enable_legacy_gce=true`、`github_owner=example-org` | 0 | `Plan: 109 to add` |
| 單元測試 | `terraform test`（`tests/*.tftest.hcl`，mock provider） | 0 | **`Success! 30 passed, 0 failed.`**（含「預設 hello 映像會觸發 check」的反向測試） |
| 變異測試 | 在暫存複本各改壞一行：pg_bigm flag、parser ingress、actAs 成員、audit 訂閱 ttl、backfill SA、ssl_mode、PTT 佇列 rps、parser push audience | 1（每次） | **8/8 被抓到**（每次都只有 1 個 run 失敗） |
| tflint 0.65.0＋google ruleset 0.41.0（recommended） | root 與 optional/gke | 0 | 0 issues |
| trivy 0.76.1 | HCL／plan JSON | 0 | HCL 26 → 11、plan 12 → 10；**HIGH 全部清掉**（Cloud SQL 未強制加密、GKE 公開節點等）。剩下的見「待使用者決定」 |
| checkov 3.4.0 | plan JSON | 1 | 失敗 24／70 資源 → 23／102 資源（通過 27 → 69）。剩下的是 SQL log flags、flow logs、bucket versioning／存取 log、CMEK；CKV_GCP_6 要求客戶端憑證（`TRUSTED_CLIENT_CERTIFICATE_REQUIRED`），本模組刻意用 `ENCRYPTED_ONLY`＋IAM 登入 |
| optional/gke | validate；把兩個 data source 換成字面值的 harness plan | 0／0 | validate 通過（1 個 deprecation warning）；`Plan: 8 to add`；checkov 25 → 11、trivy 13 → 1 |
| parser 單元測試 | `pytest services/parser/tests` | 0 | 13 passed |
| Pub/Sub 模擬器實跑 | `services/parser/local/`（見該目錄 VERIFY.md） | 0 | 合法事件 204 一次；三種壞事件各 422 五次後進 DLQ；容器以 uid 10001 執行 |

**仍然沒有實測（需要真實專案）：** Cloud Tasks 以 OIDC 呼叫 internal ingress 的 worker、Pub/Sub push 到 internal ingress 的服務、Cloud SQL 上 `CREATE EXTENSION pg_bigm`、IAM 資料庫登入、BigQuery 訂閱實際寫入、Secret Manager 讀取。

### 成本（asia-east1 牌價，2026-10-30，以修改前 70 個資源計）

| 情境 | 月費 | 備註 |
|---|---|---|
| 閒置（零流量） | US$104.12（約 NT$3,382） | Cloud SQL db-custom-2-7680 佔 US$100.32；是 Q2 預算 NT$3,000 的 1.13 倍 |
| Q2／Q3 規模 | US$110.91（約 NT$3,602） | 1.20 倍預算 |
| Q4 規模（每天 160 萬次請求） | US$1,788（GCP） | GCS 每次抓取一個物件的操作費 US$1,217，批次寫可降到約 US$573；住宅代理另加 US$2.8k–3.3k |
| 地端（B 級估算） | NT$1,515–3,914 | 不含人力 |

第二輪新增的備份、PITR、Secret、Scheduler job 沒有重新計價；備份依資料量計費。符合 Q2 預算的 Cloud SQL 規格見「待使用者決定」。

## NOTES.md「未驗證」清單：逐條處理

判定規則：**已確認**＝有 provider 文件（v8.7.0 tag 或 kubernetes provider main）或 GCP 官方文件原文，或 `terraform validate` 通過；**未驗證**＝沒有找到依據。

| # | NOTES 項目 | 結果 | 來源／依據 |
|---|---|---|---|
| 1 | kubernetes_horizontal_pod_autoscaler_v2 schema | **已確認** | kubernetes provider `docs/resources/horizontal_pod_autoscaler_v2.md`（main）：metadata／spec／scale_target_ref／metric.resource.target；optional/gke validate 通過。snippet 的 24 行已改寫為 k8s.tf |
| 2 | BigQuery 訂閱的 IAM | **已確認** | pubsub_subscription 文件：未指定 service_account_email 時寫入者是 Pub/Sub 服務代理；範例授予 `roles/bigquery.dataEditor` 與 `roles/bigquery.metadataViewer`。已在 bq.tf 實作 |
| 3 | DLQ topic 要有 subscription 才保留死信 | **已確認（第二輪）** | GCP dead-letter 文件："Remember to attach a subscription to the dead letter topic because messages published to a topic with no subscriptions are lost." 另外沒有活動的訂閱預設 31 天後會被刪，所以 `*_dlq_inspect` 與 audit 訂閱改成 `expiration_policy { ttl = "" }`，DLQ 保留期拉到 31 天 |
| 4 | google_service_account_iam_member 參數名 | **已確認（validate）** | `service_account_iam.md` 在 v8.7.0 tag 中 404，改由 `terraform validate`（schema 檢查）確認 service_account_id／role／member 通過 |
| 5 | Autopilot 不能自己加 node pool | **已確認（第二輪）** | GKE node-pools 文件："If you choose to use Autopilot, you don't need to configure node pools: GKE manages the nodes for you"。本模組的 browser_pool 本來就掛在 Standard 叢集，不用改 |
| 6 | Cloud Run pull 消費者被 CPU 節流；cpu_idle | **已確認（欄位）** | cloud_run_v2_service 文件：`cpu_idle` 在 `resources` 內；`resources` 有設時必須明寫 `true` 才維持預設。要 CPU 常開要設 `false`。本模組的 parser 是 push，不需要 |
| 7 | Pub/Sub schema 的 .id 格式 | **已確認** | pubsub_schema 文件：`projects/{{project}}/schemas/{{name}}` |
| 8 | Scheduler OIDC audience 預設 | **已確認** | cloud_scheduler_job 文件：未指定時預設為 target URI。我們仍明寫 `audience` |
| 9 | 未使用參數（address_type、network_tier、bgp） | **已確認** | compute_address：`address_type = INTERNAL` 時不能設 network_tier；本模組用預設 EXTERNAL，不設 network_tier。router 的 bgp.asn 文件範例都有寫，本模組有給 |
| 10 | Cloud SQL tier 與 edition | **已確認** | sql_database_instance 文件：shared-core 與 db-custom 只能用 ENTERPRISE；PG16 以上未指定 edition 會預設 ENTERPRISE_PLUS。本模組 PG15 + 明寫 ENTERPRISE |
| 11 | container_cluster location 是 Required 還是 Optional | **已確認：Optional** | container_cluster 文件標 `location` 為 Optional（NOTES 的懷疑不成立，但仍建議明寫） |
| 12 | node pool autoscaling 是每 zone | **已確認** | container_node_pool 文件：`min/max_node_count` 是每個 zone；`total_min/max_node_count` 是整體，兩者擇一 |
| 13 | Cloud Run v1 已棄用 | **已確認：說法不正確** | Cloud Run about-apis 文件："Each version is generally available (GA) and serves a different use case, so v2 does not obsolete v1." 正確說法是「建議用 v2」；本模組只用 v2 |
| 14 | Cloud Build：`github` 區塊 vs `repository_event_config` | **已確認（第二輪）** | Cloud Build 文件："Cloud Build repositories (1st gen) is superseded by 2nd gen, but is available as a legacy option." 新專案建立 trigger 時必須指定 service account。已加 2nd gen 的 `crawler_api_v2`（`repository_event_config`＋`service_account`），1st gen 保留但標為 legacy |
| 15 | Direct VPC egress 只給 subnetwork | **已確認** | cloud_run_v2_service 文件：network 與 subnetwork「至少給一個」。本模組兩個都給 |
| 16 | BigQuery require_partition_filter 寫在哪 | **已確認** | bigquery_table 文件：頂層 `require_partition_filter`；`time_partitioning` 內那個已棄用 |
| 17 | Cloud Tasks max_attempts -1 | **已確認** | cloud_tasks_queue 文件：`-1` = 無限次。本模組用 5 |
| 18 | Pub/Sub 保留期上限與預設 | **已確認** | pubsub_subscription 文件：預設 7 天；上限 31 天（2678400s）；下限 10 分鐘。原題「上限 7 天」不正確 |
| 19 | Pub/Sub DLQ 的 IAM | **已確認** | GCP dead-letter 文件：DLQ topic 授權 Pub/Sub 服務代理 `roles/pubsub.publisher`；來源訂閱授權 `roles/pubsub.subscriber` 才能 ack。已在 pubsub.tf 實作 |
| 20 | Pub/Sub push 的 token creator | **已確認** | GCP authenticate-push 文件：Pub/Sub 服務代理需要 `iam.serviceAccountTokenCreator` |
| 21 | instance template 不可原地改、name_prefix | **已確認** | compute_instance_template 文件：建議 `name_prefix` 搭配 `create_before_destroy` |
| 22 | MIG target_size 與 autoscaler 打架 | **已確認** | compute_region_instance_group_manager 文件：設了 target_size 會與 autoscaler 衝突 |
| 23 | Cloud Run Job 雙層 template.template | **已確認** | cloud_run_v2_job 文件範例：`template { template { containers ... } }` |
| 24 | Cloud Scheduler 的 OIDC SA 需要 run.invoker | **已確認** | GCP http-target-auth 文件：Cloud Run 目標需要 `roles/run.invoker` |
| 25 | Cloud Tasks 派送到 Cloud Run 所需的 SA 權限（actAs 等） | **已確認並實作（第二輪）** | Cloud Tasks OidcToken 文件："The service account must be within the same project as the queue. The caller must have iam.serviceAccounts.actAs permission for the service account." 已加：tasks-invoker SA、scheduler_app 對它的 `serviceAccountUser`、Cloud Tasks 服務代理的 `serviceAccountUser`、worker 的 `run.invoker`。`terraform test` 的 `cloud_tasks_oidc_chain` 檢查這四項；實際派送未在真實專案測 |
| 26 | Autopilot 的 ip_allocation_policy | **已確認（文件），未對真實專案 plan** | provider 文件：未設定時由 GKE 後端決定；Autopilot 1.27+／Standard 1.29+ 的 Service 用 GKE 管理的 34.118.224.0/20。GKE 加到 crawler-subnet 的次要範圍不會被主 root 刪掉（`send_secondary_ip_range_if_empty` 預設 false）。harness plan 通過 |

**另外補強（NOTES 沒列）：** Cloud SQL 私有 IP 需要 Private Services Access（GCP 文件標題「Configure private services access」已確認；`google_service_networking_connection` 的寫法依 provider 文件，plan 中已通過）。

## 與 snippet 不同之處（每一條都是有意的修改）

1. **worker 另建 runtime SA**（`crawler-worker`）。snippet 讓 worker 直接用 push SA，權限混在一起。
2. **parser 訂閱直接用 push**，endpoint 為 `/events/crawl-fetched`（取自 lab 的 parser main.py）。snippet 寫的是 `/push`，與 parser 程式不符。lab 的 Stage 1 Pull 不採用。
3. **deletion_protection 預設 true**（變數化）。snippet 大多寫 false。要 destroy 前需先設 false 並 apply。
4. **Cloud Run worker 的 subnetwork／network 用 `.name`**。文件範例用的是名稱；snippet 用 `.id`。
5. **BigQuery 表補上 schema**（對應 Avro），`use_topic_schema = true`、`write_metadata = false`。snippet 沒有 schema，表會沒有欄位，寫入會失敗。
6. **GCS 原始檔**：預設 30 天轉 COLDLINE（int.txt）；snippet 的 30 天 Delete 只能在 `raw_delete_after_days` 設定時啟用（見待決定事項）。
7. **補上**：scheduler_app SA、Cloud Scheduler 的 run.invoker、parser／worker 的 DLQ 訂閱與 ack 權限、Cloud SQL 的 Private Services Access、Artifact Registry、parser 的 BigQuery 寫入權限與 GCS 唯讀權限。
8. **region 全部用變數**（snippet 寫死 asia-east1）。
9. **GKE Standard 叢集**（`crawler_standard`）是為了讓節點池有父層而補上的；snippet 沒有。
10. **GKE 與 k8s HPA 放 `shared/infra/gcp/optional/gke`**（獨立 root），不併入主 plan。
11. **（第二輪）worker／scheduler／parser 改成 internal ingress**，只有 crawler-api 對外。Pub/Sub push、Cloud Scheduler、Cloud Tasks 都還能呼叫 internal 服務（GCP ingress 文件）。
12. **（第二輪）worker 每實例並行 4**（原本預設 80 × 50 實例 = 4,000 個同時抓取），每站速率改由 Cloud Tasks `crawl_site` 佇列控制。
13. **（第二輪）scheduler、parser、backfill 加 Direct VPC egress（PRIVATE_RANGES_ONLY）**：Cloud SQL 只有私有 IP，沒有 VPC 出口就連不到；PRIVATE_RANGES_ONLY 讓其他流量不經 NAT。ptt-crawl Job 用 ALL_TRAFFIC，出口走固定 IP。
14. **（第二輪）push 訂閱的 OIDC `audience` 明寫成服務 URL**（不帶路徑）。未指定時預設是整個 push endpoint（含 `/events/crawl-fetched`），Cloud Run 是否接受帶路徑的 audience 沒有實測，明寫可以避開這個不確定。
15. **（第二輪）`checks.tf` 加 `no_placeholder_images`**：只要 `image_*` 還是 hello 示範映像，plan／apply 會出現警告。Python 驗證 thread 用 docker 實測，hello 對任何 POST 都回 200，Pub/Sub 會把事件全部 ack 掉、也不會進 DLQ。

## 未覆蓋（沒有 Terraform，架構圖上仍存在）

- **browser Deployment**：HPA 的目標，不在 26 片段內。需要另外的 manifest 或 kubernetes_deployment，否則 HPA apply 會失敗。
- **session 池（Firestore）、session refresher**：架構 A／B／C 的控制面。（帳密的 Secret Manager 第二輪已補，只建容器不放值。）
- **Job DB 的資料表**：連線權限（IAM 使用者、`cloudsql.client`）已補，但 schema 與冪等表（`UNIQUE(event_id)`）沒有寫；地端版的 inbox 表 DDL 可直接參考 `shared/infra/onprem/app/qsearch_onprem/common.py`（`ingest_events`）。
- **Elasticsearch 叢集本體**：只有 API key 的 Secret。建議另開 Elastic Cloud root（`elastic/ec` provider，已在暫存目錄 validate 通過，未併入）；mapping 變更不是 Terraform 的工作。
- **Redis／Memorystore、Vector DB、Data Layer／Analytics／Export API、Digest／Search**：架構 int.txt 與 QSearch 官方圖有，但沒有 Terraform。
- **pg_bigm 的 `CREATE EXTENSION`**：flag 已設，extension 要在資料庫裡建（migration 的工作）。
- **parse.completed 主題、audit 的 consumer**：lab 有，本模組沒有。
- **映像建置**：Cloud Build trigger 只有 crawler-api；parser、worker、scheduler、backfill、ptt-crawl 沒有建置資源，`image_*` 只能指向已存在的映像。
- **監控與告警**（未確認訊息數、最舊訊息年齡、DLQ 數）：沒有 Terraform。

## 待使用者決定

1. **原始檔**：是 30 天轉 COLDLINE（int.txt），還是 30 天刪除（snippet）？兩者不能同時成立。預設採 COLDLINE，不刪。
2. **worker 的 timeout 60s** 與 crawl-sub 的 ack_deadline 60s 對齊。長時間抓取（需要無頭瀏覽器）應移到 Cloud Run Job 或 GKE；超過 ack 時間會重送，需要 worker 冪等。
3. **Cloud Build**：第二輪已加 2nd gen trigger，要用時填 `cloudbuild_repository_id`（`google_cloudbuildv2_repository` 的 id）與 `cloudbuild_service_account_id`；1st gen 是否刪除待決定。
4. ~~backfill job 共用 parser SA~~：第二輪已改成專用 `qsearch-backfill` SA。
5. **Cloud SQL 是否同時作為 Job DB**（架構 A 的假設）。第二輪已讓 worker、scheduler、parser、backfill 都能以 IAM 登入。
6. ~~BigQuery 表的 mode~~：**已確認可行**。Pub/Sub BigQuery 訂閱文件："Required fields in the topic schema don't need to be required in the BigQuery schema." 保留 NULLABLE，日後加 optional 欄位不用改表。
7. **Cloud SQL 規格與 Q2 預算**：目前 db-custom-2-7680（閒置約 US$100／月，超過 NT$3,000）。建議 db-custom-1-3840（約 US$51.42，保留 SLA）；更省是 db-g1-small（US$27.66）或 db-f1-micro（US$9.77），但沒有 SLA。
8. **Q4 規模的 GCS 寫法**：每次抓取一個物件的操作費約 US$1,217／月，改成批次寫入約 US$573；30 天轉 Coldline 對小物件不划算。
9. **GKE 只留一個叢集**：兩個叢集約 US$146／月；kubernetes provider 也建議拆到另一個 root。
10. **是否需要 Cloud NAT**：只有住宅代理商要求 IP 白名單時才需要（Q4 規模 NAT 處理費約 US$85／月）。
11. **還沒處理的掃描項目**：VPC flow logs、bucket versioning 與存取 log、Cloud SQL 的 log flags（`log_connections` 等）、CMEK、state 改用 GCS backend。都是成本或維運取捨，沒有直接套用。
12. **optional/gke 的 `remove_default_node_pool` deprecation warning**。

## 注意事項

- 這個模組**不是 QSearch 的正式基礎設施**。`shared/infra/gcp/services/parser` 只驗證事件後 log 並 ACK，不讀 GCS、不解析、沒有冪等，不能當正式 pipeline。可實際跑完整條鏈（含冪等、回放）的是地端版 `shared/infra/onprem`。
- **正式部署前一定要覆寫全部 `image_*`**（`checks.tf` 會在 plan 時警告）。預設的 hello 示範映像只為了讓 plan 成立，照預設 apply 會讓 parser 把所有事件 ack 掉。
- Secret Manager 只建容器。密碼值要用 `gcloud secrets versions add` 另外放，不要寫進 tfvars 或 state。
- 建立資源會產生費用（Cloud SQL、NAT、AR 儲存、Cloud Run、Pub/Sub 保留）。要實際 apply 前，先設預算警示。
- `terraform destroy` 會刪除所有由本模組建立的資源；要先把 `deletion_protection` 設為 false 並 apply。
- Terraform state 含有敏感的資源細節；不要放進公開 repo。遠端 backend 目前沒有設定（本模組使用 local backend）。
