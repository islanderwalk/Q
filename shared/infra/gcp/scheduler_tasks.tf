# Cloud Scheduler（每 15 分鐘敲 scheduler 服務；每小時跑 Q3 的 ptt-crawl Job）與 Cloud Tasks（每來源一條佇列）。

resource "google_cloud_scheduler_job" "crawl_tick" {
  name      = "crawl-tick"
  region    = var.region
  schedule  = var.scheduler_cron
  time_zone = var.scheduler_time_zone

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.scheduler.uri}/tick"
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      # 文件確認：audience 未指定時預設為 target 的 URI；這裡明確寫出，避免預設值改變。
      audience = google_cloud_run_v2_service.scheduler.uri
    }
  }

  depends_on = [google_cloud_run_v2_service_iam_member.scheduler_job_invoker]
}

resource "google_cloud_tasks_queue" "crawl" {
  for_each = toset(["crawl-fb", "crawl-yt", "crawl-news"])
  name     = each.key
  location = var.region

  rate_limits {
    max_dispatches_per_second = 5
    max_concurrent_dispatches = 10
  }

  retry_config {
    max_attempts = 5 # 文件確認：-1 代表無限重試，這裡不用。
    min_backoff  = "10s"
    max_backoff  = "300s"
  }

  depends_on = [google_project_service.required]
}

# 每個目標站一條有速率上限的佇列（Q3：PTT 過度請求會被擋；Q4：單 IP 約 5 req/s 就被擋，走住宅代理）。
# crawl-sub（Pub/Sub push）沒有每站速率上限，只留給 PoC；正式抓取經這兩條佇列。
resource "google_cloud_tasks_queue" "crawl_site" {
  for_each = {
    crawl-ptt     = { rps = 1, concurrent = 1 }   # Q3：PTT 禮貌速率
    crawl-threads = { rps = 20, concurrent = 50 } # Q4：每天 160 萬次 ≈ 18.5 req/s，經住宅代理
  }
  name     = each.key
  location = var.region

  rate_limits {
    max_dispatches_per_second = each.value.rps
    max_concurrent_dispatches = each.value.concurrent
  }

  retry_config {
    max_attempts = 5
    min_backoff  = "10s"
    max_backoff  = "300s"
  }

  depends_on = [google_project_service.required]
}

# scheduler 服務的 runtime SA 要能把任務丟進各佇列。
resource "google_cloud_tasks_queue_iam_member" "scheduler_enqueue" {
  for_each = merge(google_cloud_tasks_queue.crawl, google_cloud_tasks_queue.crawl_site)
  project  = var.project_id
  location = var.region
  name     = each.value.name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.scheduler_app.email}"
}

# --- Cloud Tasks → crawler-worker 的 OIDC 權限鏈（官方文件：建立帶 oidcToken 的 task 需對該 SA 有 actAs）---
# 1. scheduler_app 建 task 時指定 tasks_invoker 為 OIDC 身分：需要 iam.serviceAccounts.actAs。
resource "google_service_account_iam_member" "scheduler_app_actas_tasks_invoker" {
  service_account_id = google_service_account.tasks_invoker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.scheduler_app.email}"
}

# 2. Cloud Tasks 服務代理以 tasks_invoker 產生 token（官方步驟：Service Account User）。
resource "google_service_account_iam_member" "cloudtasks_agent_user" {
  service_account_id = google_service_account.tasks_invoker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:service-${var.project_number}@gcp-sa-cloudtasks.iam.gserviceaccount.com"
  depends_on         = [google_project_service_identity.cloudtasks]
}

# 3. tasks_invoker 只能呼叫 crawler-worker。
resource "google_cloud_run_v2_service_iam_member" "worker_tasks_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.crawler_worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.tasks_invoker.email}"
}

# --- Q3 答案的形狀：Cloud Scheduler 每小時直接執行 Cloud Run Job「ptt-crawl」---
# 文件確認：Scheduler 以 OAuth token 呼叫 run.googleapis.com/v2/.../jobs/JOB:run，SA 需要該 Job 的 run.invoker。
resource "google_cloud_run_v2_job_iam_member" "ptt_crawl_scheduler" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.ptt_crawl.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "ptt_crawl_hourly" {
  name      = "ptt-crawl-hourly"
  region    = var.region
  schedule  = var.ptt_crawl_cron
  time_zone = var.scheduler_time_zone

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.ptt_crawl.name}:run"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.ptt_crawl_scheduler]
}
