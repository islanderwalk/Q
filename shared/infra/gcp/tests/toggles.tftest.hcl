# infra/gcp 變數開關測試（mock provider，command = plan，不需要 GCP 憑證）
#
# 驗證「改一個變數 → 架構跟著變」的宣稱：
#   - raw_delete_after_days 設值才出現 GCS Delete 規則（COLDLINE 規則仍保留）
#   - deletion_protection = false 會套到 Cloud SQL、Cloud Run 服務與 job、BigQuery 表
#   - enable_legacy_gce = true 才建立 legacy GCE（SA、health check、instance template、區域 MIG）
#   - github_owner 有值才建立 Cloud Build trigger
#
# 跑法同 architecture.tftest.hcl：TF_DATA_DIR 指到 repo 外，cd infra/gcp，terraform test -verbose。

mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id     = "qsearch-test"
  project_number = "123456789012"

  # 不用預設的 hello 示範映像，否則 checks.tf 的 no_placeholder_images 會讓每個 run 失敗。
  image_api       = "asia-east1-docker.pkg.dev/qsearch-test/crawler/api:test"
  image_worker    = "asia-east1-docker.pkg.dev/qsearch-test/crawler/worker:test"
  image_parser    = "asia-east1-docker.pkg.dev/qsearch-test/crawler/parser:test"
  image_scheduler = "asia-east1-docker.pkg.dev/qsearch-test/crawler/scheduler:test"
  image_backfill  = "asia-east1-docker.pkg.dev/qsearch-test/crawler/backfill:test"
  image_ptt_crawl = "asia-east1-docker.pkg.dev/qsearch-test/crawler/ptt-crawl:test"
}

# legacy GCE 斷言會引用這些 computed 屬性；每個值都不同，指錯資源就會失敗。
override_resource {
  target          = google_compute_subnetwork.crawler
  override_during = plan
  values = {
    id        = "projects/qsearch-test/regions/asia-east1/subnetworks/crawler-subnet"
    self_link = "https://www.googleapis.com/compute/v1/projects/qsearch-test/regions/asia-east1/subnetworks/crawler-subnet"
  }
}

override_resource {
  target          = google_service_account.legacy
  override_during = plan
  values = {
    email = "crawler-legacy@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/crawler-legacy@qsearch-test.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_compute_health_check.legacy
  override_during = plan
  values = {
    id        = "projects/qsearch-test/global/healthChecks/crawler-legacy-hc"
    self_link = "https://www.googleapis.com/compute/v1/projects/qsearch-test/global/healthChecks/crawler-legacy-hc"
  }
}

override_resource {
  target          = google_compute_instance_template.crawler_legacy
  override_during = plan
  values = {
    id        = "projects/qsearch-test/global/instanceTemplates/crawler-legacy-20261030000000000000000001"
    self_link = "https://www.googleapis.com/compute/v1/projects/qsearch-test/global/instanceTemplates/crawler-legacy-20261030000000000000000001"
  }
}

# =====================================================================
# GCS：raw_delete_after_days 設值 → 出現 Delete 規則，COLDLINE 規則保留
# =====================================================================
run "gcs_delete_rule_when_set" {
  command = plan

  variables {
    raw_delete_after_days = 365
  }

  assert {
    condition     = length(google_storage_bucket.raw_scraped.lifecycle_rule) == 2
    error_message = "raw_delete_after_days 設值時應有 2 條 lifecycle 規則（COLDLINE + Delete）。"
  }

  assert {
    condition = length([
      for r in google_storage_bucket.raw_scraped.lifecycle_rule : r
      if one(r.action).type == "Delete" && one(r.condition).age == 365
    ]) == 1
    error_message = "raw_delete_after_days = 365 時必須有一條 age = 365 的 Delete 規則。"
  }

  assert {
    condition = length([
      for r in google_storage_bucket.raw_scraped.lifecycle_rule : r
      if one(r.action).type == "SetStorageClass" && one(r.action).storage_class == "COLDLINE" && one(r.condition).age == 30
    ]) == 1
    error_message = "加上 Delete 規則後，30 天轉 COLDLINE 的規則仍必須存在。"
  }
}

run "gcs_custom_coldline_days" {
  command = plan

  variables {
    raw_coldline_after_days = 45
    raw_bucket_name         = "qsearch-custom-raw"
  }

  assert {
    condition = length([
      for r in google_storage_bucket.raw_scraped.lifecycle_rule : r
      if one(r.action).storage_class == "COLDLINE" && one(r.condition).age == 45
    ]) == 1
    error_message = "COLDLINE 天數必須跟隨 var.raw_coldline_after_days。"
  }

  assert {
    condition     = length([for r in google_storage_bucket.raw_scraped.lifecycle_rule : r if one(r.action).type == "Delete"]) == 0
    error_message = "沒設 raw_delete_after_days 時不可有 Delete 規則。"
  }

  assert {
    condition     = google_storage_bucket.raw_scraped.name == "qsearch-custom-raw"
    error_message = "raw_bucket_name 有值時 bucket 名稱必須使用它。"
  }
}

# =====================================================================
# deletion_protection = false：所有支援的資源一起跟著變
# =====================================================================
run "deletion_protection_follows_var" {
  command = plan

  variables {
    deletion_protection = false
  }

  assert {
    condition     = google_sql_database_instance.crawler_db.deletion_protection == false
    error_message = "deletion_protection = false 時 Cloud SQL 必須跟著是 false。"
  }

  assert {
    condition = alltrue([
      for s in [
        google_cloud_run_v2_service.api,
        google_cloud_run_v2_service.crawler_worker,
        google_cloud_run_v2_service.scheduler,
        google_cloud_run_v2_service.parser,
      ] : s.deletion_protection == false
    ])
    error_message = "deletion_protection = false 時四個 Cloud Run 服務必須跟著是 false。"
  }

  assert {
    condition     = google_cloud_run_v2_job.qsearch_backfill.deletion_protection == false
    error_message = "deletion_protection = false 時 backfill job 必須跟著是 false。"
  }

  assert {
    condition     = google_bigquery_table.ingest_events.deletion_protection == false
    error_message = "deletion_protection = false 時 BigQuery 表必須跟著是 false。"
  }
}

# =====================================================================
# enable_legacy_gce = true：建立 legacy GCE 四件組
# =====================================================================
run "legacy_gce_enabled" {
  command = plan

  variables {
    enable_legacy_gce = true
  }

  assert {
    condition = (
      length(google_service_account.legacy) == 1 &&
      length(google_compute_health_check.legacy) == 1 &&
      length(google_compute_instance_template.crawler_legacy) == 1 &&
      length(google_compute_region_instance_group_manager.crawler_legacy) == 1
    )
    error_message = "enable_legacy_gce = true 時必須各建立 1 個 legacy SA、health check、instance template、區域 MIG。"
  }

  assert {
    condition = (
      try(google_compute_region_instance_group_manager.crawler_legacy[0].name, null) == "crawler-legacy-mig" &&
      try(google_compute_region_instance_group_manager.crawler_legacy[0].region, null) == "asia-east1" &&
      try(google_compute_region_instance_group_manager.crawler_legacy[0].target_size, null) == 2
    )
    error_message = "legacy MIG 應為 asia-east1 區域 MIG、固定 target_size 2。"
  }

  assert {
    condition     = try(google_compute_region_instance_group_manager.crawler_legacy[0].version[0].instance_template, null) == try(google_compute_instance_template.crawler_legacy[0].self_link, null)
    error_message = "legacy MIG 必須使用 crawler_legacy instance template。"
  }

  assert {
    condition = (
      try(google_compute_region_instance_group_manager.crawler_legacy[0].auto_healing_policies[0].health_check, null) == try(google_compute_health_check.legacy[0].id, null) &&
      try(google_compute_region_instance_group_manager.crawler_legacy[0].auto_healing_policies[0].initial_delay_sec, null) == 300
    )
    error_message = "legacy MIG 的 auto-healing 必須使用 crawler-legacy-hc，initial_delay 300s。"
  }

  assert {
    condition = (
      try(google_compute_instance_template.crawler_legacy[0].name_prefix, null) == "crawler-legacy-" &&
      try(google_compute_instance_template.crawler_legacy[0].network_interface[0].subnetwork, null) == google_compute_subnetwork.crawler.id &&
      try(google_compute_instance_template.crawler_legacy[0].service_account[0].email, null) == try(google_service_account.legacy[0].email, null)
    )
    error_message = "instance template 必須用 name_prefix、接 crawler-subnet、用 legacy SA。"
  }

  assert {
    condition = (
      try(google_compute_health_check.legacy[0].http_health_check[0].port, null) == 8080 &&
      try(google_compute_health_check.legacy[0].http_health_check[0].request_path, null) == "/health"
    )
    error_message = "legacy health check 應打 :8080/health。"
  }

  assert {
    condition     = output.legacy_mig_name == "crawler-legacy-mig"
    error_message = "enable_legacy_gce = true 時 output legacy_mig_name 必須有值。"
  }

  assert {
    condition     = length(google_cloudbuild_trigger.crawler_api) == 0
    error_message = "只開 legacy 時不應建立 Cloud Build trigger。"
  }
}

# =====================================================================
# github_owner 有值：建立 Cloud Build trigger
# =====================================================================
run "cloudbuild_trigger_when_github_owner_set" {
  command = plan

  variables {
    github_owner = "example-org"
  }

  assert {
    condition     = length(google_cloudbuild_trigger.crawler_api) == 1
    error_message = "github_owner 有值時必須建立 1 個 Cloud Build trigger。"
  }

  assert {
    condition = (
      try(google_cloudbuild_trigger.crawler_api[0].name, null) == "crawler-api-main" &&
      try(google_cloudbuild_trigger.crawler_api[0].location, null) == "asia-east1" &&
      try(google_cloudbuild_trigger.crawler_api[0].filename, null) == "cloudbuild.yaml"
    )
    error_message = "Cloud Build trigger 名稱／位置／cloudbuild.yaml 不符。"
  }

  assert {
    condition = (
      try(google_cloudbuild_trigger.crawler_api[0].github[0].owner, null) == "example-org" &&
      try(google_cloudbuild_trigger.crawler_api[0].github[0].name, null) == "crawler" &&
      try(google_cloudbuild_trigger.crawler_api[0].github[0].push[0].branch, null) == "^main$"
    )
    error_message = "Cloud Build trigger 必須監聽 example-org/crawler 的 main 分支。"
  }

  assert {
    condition     = length(google_compute_region_instance_group_manager.crawler_legacy) == 0
    error_message = "只設 github_owner 時不應建立 legacy MIG。"
  }
}
