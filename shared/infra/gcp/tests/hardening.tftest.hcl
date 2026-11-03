# shared/infra/gcp 審查修正的回歸測試（mock provider，command = plan，不需要 GCP 憑證）
#
# 2026-10-30 架構審查與靜態掃描找到的問題，修正後在這裡寫成斷言，避免之後被改回去：
#   - Cloud Tasks → crawler-worker 的 OIDC 權限鏈（actAs、Cloud Tasks 服務代理、run.invoker）
#   - 每個目標站一條有速率上限的佇列（crawl-ptt 1 req/s、crawl-threads 20 req/s），worker 同時連線 4
#   - worker／parser／scheduler 只收內部流量（Pub/Sub push、Scheduler、Tasks）
#   - Cloud SQL：pg_bigm 旗標、IAM 登入、只收 TLS、每日備份＋PITR、每個 runtime SA 一個 IAM 使用者
#   - DLQ 檢查訂閱與 audit-sub 永不過期
#   - 回補 Job 獨立 SA、task 數與平行度可調；Q3 的 ptt-crawl Job 由 Scheduler 每小時執行
#   - Secret Manager（代理帳密、Elasticsearch API key）只開給需要的 SA
#
# 跑法同 architecture.tftest.hcl：TF_DATA_DIR 指到 repo 外，cd shared/infra/gcp，terraform test -verbose。

mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id     = "qsearch-test"
  project_number = "123456789012"

  # 不用預設的 hello 示範映像，否則 checks.tf 的 no_placeholder_images 會讓每個 run 失敗。
  image_api           = "asia-east1-docker.pkg.dev/qsearch-test/crawler/api:test"
  image_worker        = "asia-east1-docker.pkg.dev/qsearch-test/crawler/worker:test"
  image_parser        = "asia-east1-docker.pkg.dev/qsearch-test/crawler/parser:test"
  image_scheduler     = "asia-east1-docker.pkg.dev/qsearch-test/crawler/scheduler:test"
  image_backfill      = "asia-east1-docker.pkg.dev/qsearch-test/crawler/backfill:test"
  image_ptt_crawl     = "asia-east1-docker.pkg.dev/qsearch-test/crawler/ptt-crawl:test"
  image_seg           = "asia-east1-docker.pkg.dev/qsearch-test/crawler/qsearch-seg:test"
  image_wordcloud_api = "asia-east1-docker.pkg.dev/qsearch-test/crawler/qsearch-wordcloud-api:test"
  image_collector     = "asia-east1-docker.pkg.dev/qsearch-test/crawler/qsearch-collector:test"
}

# 斷言會引用這些 computed 屬性；每個值都不同，指錯資源就會失敗。
override_resource {
  target          = google_service_account.worker
  override_during = plan
  values = {
    email = "crawler-worker@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/crawler-worker@qsearch-test.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_service_account.parser
  override_during = plan
  values = {
    email = "qsearch-parser@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/qsearch-parser@qsearch-test.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_service_account.scheduler
  override_during = plan
  values = {
    email = "crawler-scheduler-invoker@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/crawler-scheduler-invoker@qsearch-test.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_service_account.scheduler_app
  override_during = plan
  values = {
    email = "crawler-scheduler-app@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/crawler-scheduler-app@qsearch-test.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_service_account.backfill
  override_during = plan
  values = {
    email = "qsearch-backfill@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/qsearch-backfill@qsearch-test.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_service_account.tasks_invoker
  override_during = plan
  values = {
    email = "crawler-tasks-invoker@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/crawler-tasks-invoker@qsearch-test.iam.gserviceaccount.com"
  }
}

# Q6：斷詞服務／文字雲 API／採集器也連 Cloud SQL（sql_iam_sas 的 key 要能算出 email）
override_resource {
  target          = google_service_account.seg
  override_during = plan
  values = {
    email = "qsearch-seg@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/qsearch-seg@qsearch-test.iam.gserviceaccount.com"
  }
}

# Q6：斷詞服務／文字雲 API／採集器也連 Cloud SQL（sql_iam_sas 的 key 要能算出 email）
override_resource {
  target          = google_service_account.wordcloud_api
  override_during = plan
  values = {
    email = "qsearch-wordcloud-api@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/qsearch-wordcloud-api@qsearch-test.iam.gserviceaccount.com"
  }
}

# Q6：斷詞服務／文字雲 API／採集器也連 Cloud SQL（sql_iam_sas 的 key 要能算出 email）
override_resource {
  target          = google_service_account.collector
  override_during = plan
  values = {
    email = "qsearch-collector@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/qsearch-collector@qsearch-test.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_secret_manager_secret.proxy_credentials
  override_during = plan
  values = {
    id = "projects/qsearch-test/secrets/residential-proxy-credentials"
  }
}

override_resource {
  target          = google_secret_manager_secret.es_api_key
  override_during = plan
  values = {
    id = "projects/qsearch-test/secrets/elasticsearch-api-key"
  }
}

# =====================================================================
# Cloud Tasks → crawler-worker：OIDC 權限鏈三件事都要在
# =====================================================================
run "cloud_tasks_oidc_chain" {
  command = plan

  assert {
    condition = (
      google_service_account_iam_member.scheduler_app_actas_tasks_invoker.service_account_id == google_service_account.tasks_invoker.name &&
      google_service_account_iam_member.scheduler_app_actas_tasks_invoker.role == "roles/iam.serviceAccountUser" &&
      google_service_account_iam_member.scheduler_app_actas_tasks_invoker.member == "serviceAccount:${google_service_account.scheduler_app.email}"
    )
    error_message = "scheduler_app 建立帶 OIDC 的 task 時需要對 tasks_invoker 有 actAs（roles/iam.serviceAccountUser）。"
  }

  assert {
    condition = (
      google_service_account_iam_member.cloudtasks_agent_user.service_account_id == google_service_account.tasks_invoker.name &&
      google_service_account_iam_member.cloudtasks_agent_user.member == "serviceAccount:service-123456789012@gcp-sa-cloudtasks.iam.gserviceaccount.com"
    )
    error_message = "Cloud Tasks 服務代理必須能以 tasks_invoker 產生 token。"
  }

  assert {
    condition = (
      google_cloud_run_v2_service_iam_member.worker_tasks_invoker.name == google_cloud_run_v2_service.crawler_worker.name &&
      google_cloud_run_v2_service_iam_member.worker_tasks_invoker.role == "roles/run.invoker" &&
      google_cloud_run_v2_service_iam_member.worker_tasks_invoker.member == "serviceAccount:${google_service_account.tasks_invoker.email}"
    )
    error_message = "tasks_invoker 只能、而且必須能呼叫 crawler-worker。"
  }
}

# =====================================================================
# 每站速率：PTT 1 req/s、Threads 20 req/s；worker 同時連線 4；內部流量
# =====================================================================
run "per_site_rate_limits_and_ingress" {
  command = plan

  assert {
    condition = (
      google_cloud_tasks_queue.crawl_site["crawl-ptt"].rate_limits[0].max_dispatches_per_second == 1 &&
      google_cloud_tasks_queue.crawl_site["crawl-ptt"].rate_limits[0].max_concurrent_dispatches == 1 &&
      google_cloud_tasks_queue.crawl_site["crawl-threads"].rate_limits[0].max_dispatches_per_second == 20 &&
      google_cloud_tasks_queue.crawl_site["crawl-threads"].rate_limits[0].max_concurrent_dispatches == 50
    )
    error_message = "crawl-ptt 必須 1 req/s、同時 1；crawl-threads 必須 20 req/s（≥ Q4 平均 18.5 req/s）、同時 50。"
  }

  assert {
    condition     = google_cloud_run_v2_service.crawler_worker.template[0].max_instance_request_concurrency == 4
    error_message = "crawler-worker 每個實例同時處理的請求數應限制為 4（預設 80 會對目標站同時開太多連線）。"
  }

  assert {
    condition = alltrue([
      for s in [
        google_cloud_run_v2_service.crawler_worker,
        google_cloud_run_v2_service.parser,
        google_cloud_run_v2_service.scheduler,
      ] : s.ingress == "INGRESS_TRAFFIC_INTERNAL_ONLY"
    ]) && google_cloud_run_v2_service.api.ingress == "INGRESS_TRAFFIC_ALL"
    error_message = "worker／parser／scheduler 只收內部流量；只有管理入口 crawler-api 對外。"
  }
}

# =====================================================================
# Cloud SQL：pg_bigm、IAM 登入、TLS、備份、IAM 使用者
# =====================================================================
run "cloud_sql_hardening" {
  command = plan

  assert {
    condition = (
      contains([for f in google_sql_database_instance.crawler_db.settings[0].database_flags : "${f.name}=${f.value}"], "cloudsql.enable_pg_bigm=on") &&
      contains([for f in google_sql_database_instance.crawler_db.settings[0].database_flags : "${f.name}=${f.value}"], "cloudsql.iam_authentication=on")
    )
    error_message = "Q2 選 pg_bigm：必須開 cloudsql.enable_pg_bigm；IAM 登入必須開 cloudsql.iam_authentication。"
  }

  assert {
    condition     = google_sql_database_instance.crawler_db.settings[0].ip_configuration[0].ssl_mode == "ENCRYPTED_ONLY"
    error_message = "Cloud SQL 只接受 TLS 連線。"
  }

  assert {
    condition = (
      google_sql_database_instance.crawler_db.settings[0].backup_configuration[0].enabled == true &&
      google_sql_database_instance.crawler_db.settings[0].backup_configuration[0].point_in_time_recovery_enabled == true &&
      google_sql_database_instance.crawler_db.settings[0].backup_configuration[0].transaction_log_retention_days == 7 &&
      google_sql_database_instance.crawler_db.settings[0].backup_configuration[0].backup_retention_settings[0].retained_backups == 7
    )
    error_message = "預設要有每日備份、7 天 PITR、保留 7 份備份。"
  }

  assert {
    condition = (
      toset(keys(google_sql_user.iam_sa)) == toset(["worker", "parser", "scheduler_app", "backfill", "seg", "wordcloud_api", "collector"]) &&
      alltrue([for u in google_sql_user.iam_sa : u.type == "CLOUD_IAM_SERVICE_ACCOUNT" && !endswith(u.name, ".gserviceaccount.com")]) &&
      google_sql_user.iam_sa["backfill"].name == "qsearch-backfill@qsearch-test.iam"
    )
    error_message = "每個連資料庫的 runtime SA 都要有 IAM 使用者，PostgreSQL 使用者名稱要去掉 .gserviceaccount.com。"
  }

  assert {
    condition = alltrue([
      for k in ["worker", "parser", "scheduler_app", "backfill", "seg", "wordcloud_api", "collector"] :
      google_project_iam_member.sql_client[k].role == "roles/cloudsql.client" &&
      google_project_iam_member.sql_instance_user[k].role == "roles/cloudsql.instanceUser" &&
      google_project_iam_member.sql_client[k].member == "serviceAccount:${google_sql_user.iam_sa[k].name}.gserviceaccount.com"
    ])
    error_message = "IAM 使用者對應的 SA 必須同時有 cloudsql.client 與 cloudsql.instanceUser。"
  }
}

run "cloud_sql_backup_can_be_turned_off" {
  command = plan

  variables {
    sql_backup_enabled = false
  }

  assert {
    condition = (
      google_sql_database_instance.crawler_db.settings[0].backup_configuration[0].enabled == false &&
      google_sql_database_instance.crawler_db.settings[0].backup_configuration[0].point_in_time_recovery_enabled == false
    )
    error_message = "sql_backup_enabled = false 時備份與 PITR 都要關閉。"
  }
}

# =====================================================================
# DLQ 檢查訂閱與 audit-sub 不會因 31 天無活動被刪
# =====================================================================
run "dead_letter_subscriptions_never_expire" {
  command = plan

  assert {
    condition = alltrue([
      for s in [
        google_pubsub_subscription.crawler_tasks_dlq_inspect,
        google_pubsub_subscription.parser_dlq_inspect,
      ] : s.expiration_policy[0].ttl == "" && s.message_retention_duration == "2678400s"
    ])
    error_message = "DLQ 檢查訂閱必須永不過期，並保留 31 天（上限）。"
  }

  assert {
    condition     = google_pubsub_subscription.audit_sub.expiration_policy[0].ttl == ""
    error_message = "audit-sub 必須永不過期。"
  }
}

# =====================================================================
# 回補 Job 與 Q3 ptt-crawl Job
# =====================================================================
run "backfill_job_partitioned" {
  command = plan

  variables {
    backfill_task_count  = 270
    backfill_parallelism = 20
  }

  assert {
    condition = (
      google_cloud_run_v2_job.qsearch_backfill.template[0].task_count == 270 &&
      google_cloud_run_v2_job.qsearch_backfill.template[0].parallelism == 20 &&
      google_cloud_run_v2_job.qsearch_backfill.template[0].template[0].service_account == google_service_account.backfill.email
    )
    error_message = "回補 Job 要用獨立 SA，task 數與平行度跟隨變數（Q6：渠道 × 月約 270 批，平行 20 保護 ES）。"
  }

  assert {
    condition = (
      google_storage_bucket_iam_member.backfill_read_raw.role == "roles/storage.objectViewer" &&
      google_storage_bucket_iam_member.backfill_read_raw.member == "serviceAccount:${google_service_account.backfill.email}"
    )
    error_message = "回補 SA 只讀原始檔。"
  }
}

run "q3_ptt_crawl_job_hourly" {
  command = plan

  assert {
    condition = (
      google_cloud_run_v2_job.ptt_crawl.template[0].template[0].timeout == "3000s" &&
      google_cloud_run_v2_job.ptt_crawl.template[0].template[0].service_account == google_service_account.worker.email &&
      google_cloud_run_v2_job.ptt_crawl.template[0].template[0].vpc_access[0].egress == "ALL_TRAFFIC"
    )
    error_message = "ptt-crawl Job：timeout 3000 秒（尖峰約 450 次請求 × 1–2 秒）、用 worker SA、出口走 NAT 固定 IP。"
  }

  assert {
    condition = (
      google_cloud_scheduler_job.ptt_crawl_hourly.schedule == "5 * * * *" &&
      google_cloud_scheduler_job.ptt_crawl_hourly.http_target[0].uri == "https://run.googleapis.com/v2/projects/qsearch-test/locations/asia-east1/jobs/ptt-crawl:run" &&
      google_cloud_scheduler_job.ptt_crawl_hourly.http_target[0].oauth_token[0].service_account_email == google_service_account.scheduler.email &&
      google_cloud_run_v2_job_iam_member.ptt_crawl_scheduler.role == "roles/run.invoker" &&
      google_cloud_run_v2_job_iam_member.ptt_crawl_scheduler.member == "serviceAccount:${google_service_account.scheduler.email}"
    )
    error_message = "Cloud Scheduler 每小時以 OAuth 呼叫 ptt-crawl:run，該 SA 需有 Job 的 run.invoker。"
  }
}

# =====================================================================
# Secret Manager：只開給需要的 SA
# =====================================================================
run "secrets_least_privilege" {
  command = plan

  assert {
    condition = (
      google_secret_manager_secret_iam_member.worker_proxy_access.secret_id == google_secret_manager_secret.proxy_credentials.id &&
      google_secret_manager_secret_iam_member.worker_proxy_access.member == "serviceAccount:${google_service_account.worker.email}"
    )
    error_message = "住宅代理帳密只開給 worker。"
  }

  assert {
    condition = (
      toset([for m in google_secret_manager_secret_iam_member.es_api_key_access : m.member]) == toset([
        "serviceAccount:${google_service_account.parser.email}",
        "serviceAccount:${google_service_account.backfill.email}",
      ]) &&
      alltrue([for m in google_secret_manager_secret_iam_member.es_api_key_access : m.role == "roles/secretmanager.secretAccessor" && m.secret_id == google_secret_manager_secret.es_api_key.id])
    )
    error_message = "Elasticsearch API key 只開給 parser 與 backfill。"
  }
}

# =====================================================================
# legacy GCE（開啟時）：健康檢查防火牆、Shielded VM、封鎖專案 SSH 金鑰
# =====================================================================
run "legacy_gce_hardening_when_enabled" {
  command = plan

  variables {
    enable_legacy_gce = true
  }

  assert {
    condition = (
      length(google_compute_firewall.legacy_health_check) == 1 &&
      toset(google_compute_firewall.legacy_health_check[0].source_ranges) == toset(["35.191.0.0/16", "130.211.0.0/22"]) &&
      toset(google_compute_firewall.legacy_health_check[0].target_tags) == toset(google_compute_instance_template.crawler_legacy[0].tags)
    )
    error_message = "MIG autohealing 需要放行 GCP 健康檢查來源，且目標 tag 要對到 instance template。"
  }

  assert {
    condition = (
      google_compute_instance_template.crawler_legacy[0].shielded_instance_config[0].enable_secure_boot == true &&
      google_compute_instance_template.crawler_legacy[0].metadata["block-project-ssh-keys"] == "true"
    )
    error_message = "legacy VM 要開 Secure Boot 並封鎖專案層級 SSH 金鑰。"
  }
}

# Python 驗證 thread 實測：hello 示範映像對任何 POST 都回 200，Pub/Sub 會把事件全部 ack 掉。
# 只要有一個 image_* 還是示範映像，check 就要報出來。
run "placeholder_image_is_flagged" {
  command = plan

  variables {
    image_parser = "us-docker.pkg.dev/cloudrun/container/hello"
  }

  expect_failures = [check.no_placeholder_images]
}
