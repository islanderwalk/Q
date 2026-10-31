# Service accounts。最小權限：每個服務一個 runtime SA；push 與 scheduler 各有單一用途 SA。
#
# 與 NOTES.md 相比的補強：
#   - worker 另建 runtime SA（snippet 原本 worker 直接用 push SA 當 runtime，權限混在一起）。
#   - scheduler_app（排程服務 runtime）與 scheduler（Cloud Scheduler OIDC）分開。

locals {
  # Pub/Sub 服務代理。Push OIDC 與 DLQ 轉送都以它為主體。
  pubsub_agent_member = "serviceAccount:service-${var.project_number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_service_account" "api" {
  account_id   = "crawler-api"
  display_name = "crawler-api Cloud Run runtime"
  depends_on   = [google_project_service.required]
}

resource "google_service_account" "worker" {
  account_id   = "crawler-worker"
  display_name = "crawler-worker Cloud Run runtime"
  depends_on   = [google_project_service.required]
}

# 專供 Pub/Sub push OIDC 使用的 SA。只授予 run.invoker（綁在 worker/parser 服務上）。
resource "google_service_account" "push" {
  account_id   = "crawler-pubsub-push"
  display_name = "Pub/Sub push OIDC identity"
  depends_on   = [google_project_service.required]
}

# Cloud Scheduler OIDC 用的 SA（呼叫 scheduler 服務的 /tick）。
resource "google_service_account" "scheduler" {
  account_id   = "crawler-scheduler-invoker"
  display_name = "Cloud Scheduler OIDC invoker"
  depends_on   = [google_project_service.required]
}

# scheduler Cloud Run 服務的 runtime SA（排程大腦，派工到 Cloud Tasks）。
resource "google_service_account" "scheduler_app" {
  account_id   = "crawler-scheduler-app"
  display_name = "scheduler Cloud Run runtime"
  depends_on   = [google_project_service.required]
}

resource "google_service_account" "parser" {
  account_id   = "qsearch-parser"
  display_name = "qsearch parser Cloud Run runtime"
  depends_on   = [google_project_service.required]
}

# 回補 Job 的 runtime SA（不再共用 parser SA：parser 對整個 BigQuery dataset 有寫入權，回補不需要）。
resource "google_service_account" "backfill" {
  account_id   = "qsearch-backfill"
  display_name = "qsearch backfill Cloud Run Job runtime"
  depends_on   = [google_project_service.required]
}

# Cloud Tasks 派送到 crawler-worker 時，task 帶的 OIDC 身分。只綁 worker 的 run.invoker。
resource "google_service_account" "tasks_invoker" {
  account_id   = "crawler-tasks-invoker"
  display_name = "Cloud Tasks OIDC invoker (crawler-worker)"
  depends_on   = [google_project_service.required]
}

# 讓 Pub/Sub 服務代理能替 push SA 簽發 OIDC token（文件確認：需 iam.serviceAccountTokenCreator）。
resource "google_service_account_iam_member" "push_token_creator" {
  service_account_id = google_service_account.push.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.pubsub_agent_member
  depends_on         = [google_project_service_identity.pubsub]
}

# 派工：api 發佈到 crawler-tasks；worker 發佈 crawl.fetched 事件。
resource "google_pubsub_topic_iam_member" "api_publish_tasks" {
  project = var.project_id
  topic   = google_pubsub_topic.crawler_tasks.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_pubsub_topic_iam_member" "worker_publish_fetched" {
  project = var.project_id
  topic   = google_pubsub_topic.qsearch_fetched.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

# worker 寫入 raw bucket；parser 只讀（least privilege，bucket 層級而非 project 層級）。
resource "google_storage_bucket_iam_member" "worker_create_raw" {
  bucket = google_storage_bucket.raw_scraped.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_storage_bucket_iam_member" "parser_read_raw" {
  bucket = google_storage_bucket.raw_scraped.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.parser.email}"
}

resource "google_storage_bucket_iam_member" "backfill_read_raw" {
  bucket = google_storage_bucket.raw_scraped.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.backfill.email}"
}
