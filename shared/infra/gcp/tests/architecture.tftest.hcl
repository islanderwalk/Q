# infra/gcp 架構性質測試（Terraform 原生測試，mock provider，不需要 GCP 憑證）
#
# 用途：把 README.md、docs/handoff/gcp.md、docs/architecture/gcp-vs-onprem.md 宣稱的架構性質
#       寫成可重跑的斷言。所有 run 都是 command = plan，provider 全部 mock，
#       不會呼叫任何 GCP API、不會建立任何資源。
#
# 怎麼跑（在 repo 外指定 TF_DATA_DIR，避免 .terraform/ 寫進 repo）：
#   export TF_DATA_DIR=<repo 外的暫存目錄>/tfdata
#   cd infra/gcp
#   terraform init -lockfile=readonly
#   terraform test -verbose
#
# 為什麼要 override_resource：
#   mock provider 在 plan 階段不會產生 computed 屬性（id、email、uri、self_link 都是 unknown），
#   斷言若引用 unknown 值會報 "Unknown condition value"。下面對每個被引用的資源
#   各給一個「互不相同、格式接近實際」的值，並設 override_during = plan。
#   因為每個資源的值都不同，「A 指向 B」這類引用比對才有意義（指錯資源就會失敗）。

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

# --- 網路 ---
override_resource {
  target          = google_compute_network.crawler
  override_during = plan
  values = {
    id        = "projects/qsearch-test/global/networks/crawler-vpc"
    self_link = "https://www.googleapis.com/compute/v1/projects/qsearch-test/global/networks/crawler-vpc"
  }
}

override_resource {
  target          = google_compute_subnetwork.crawler
  override_during = plan
  values = {
    id        = "projects/qsearch-test/regions/asia-east1/subnetworks/crawler-subnet"
    self_link = "https://www.googleapis.com/compute/v1/projects/qsearch-test/regions/asia-east1/subnetworks/crawler-subnet"
  }
}

override_resource {
  target          = google_compute_address.egress
  override_during = plan
  values = {
    id        = "projects/qsearch-test/regions/asia-east1/addresses/crawler-egress-ip"
    self_link = "https://www.googleapis.com/compute/v1/projects/qsearch-test/regions/asia-east1/addresses/crawler-egress-ip"
    address   = "203.0.113.10"
  }
}

# 第二個固定出口 IP（Q6 第三輪加的：埠數與封鎖退路）
override_resource {
  target          = google_compute_address.egress_2
  override_during = plan
  values = {
    id        = "projects/qsearch-test/regions/asia-east1/addresses/crawler-egress-ip-2"
    self_link = "https://www.googleapis.com/compute/v1/projects/qsearch-test/regions/asia-east1/addresses/crawler-egress-ip-2"
    address   = "203.0.113.11"
  }
}

# --- Cloud Run 服務 URI ---
override_resource {
  target          = google_cloud_run_v2_service.api
  override_during = plan
  values = {
    uri = "https://crawler-api-mock-de.a.run.app"
  }
}

override_resource {
  target          = google_cloud_run_v2_service.crawler_worker
  override_during = plan
  values = {
    uri = "https://crawler-worker-mock-de.a.run.app"
  }
}

override_resource {
  target          = google_cloud_run_v2_service.scheduler
  override_during = plan
  values = {
    uri = "https://scheduler-mock-de.a.run.app"
  }
}

override_resource {
  target          = google_cloud_run_v2_service.parser
  override_during = plan
  values = {
    uri = "https://parser-mock-de.a.run.app"
  }
}

# --- Service accounts ---
override_resource {
  target          = google_service_account.api
  override_during = plan
  values = {
    email = "crawler-api@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/crawler-api@qsearch-test.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_service_account.worker
  override_during = plan
  values = {
    email = "crawler-worker@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/crawler-worker@qsearch-test.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_service_account.push
  override_during = plan
  values = {
    email = "crawler-pubsub-push@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/crawler-pubsub-push@qsearch-test.iam.gserviceaccount.com"
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
  target          = google_service_account.parser
  override_during = plan
  values = {
    email = "qsearch-parser@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/qsearch-parser@qsearch-test.iam.gserviceaccount.com"
  }
}

# --- Pub/Sub ---
override_resource {
  target          = google_pubsub_topic.crawler_tasks
  override_during = plan
  values = {
    id = "projects/qsearch-test/topics/crawler-tasks"
  }
}

override_resource {
  target          = google_pubsub_topic.crawler_tasks_dlq
  override_during = plan
  values = {
    id = "projects/qsearch-test/topics/crawler-tasks-dlq"
  }
}

override_resource {
  target          = google_pubsub_topic.qsearch_fetched
  override_during = plan
  values = {
    id = "projects/qsearch-test/topics/qsearch-crawl-fetched"
  }
}

override_resource {
  target          = google_pubsub_topic.parser_dlq
  override_during = plan
  values = {
    id = "projects/qsearch-test/topics/qsearch-parser-dlq"
  }
}

override_resource {
  target          = google_pubsub_schema.qsearch_event_v1
  override_during = plan
  values = {
    id = "projects/qsearch-test/schemas/qsearch-crawl-event-v1"
  }
}

# =====================================================================
# Cloud SQL：私有 IP only、ENTERPRISE、deletion_protection 跟隨變數
# =====================================================================
run "cloud_sql_private_ip_enterprise" {
  command = plan

  assert {
    condition     = google_sql_database_instance.crawler_db.settings[0].ip_configuration[0].ipv4_enabled == false
    error_message = "Cloud SQL 必須關閉公開 IPv4（ipv4_enabled = false，只走私有 IP）。"
  }

  assert {
    condition     = google_sql_database_instance.crawler_db.settings[0].ip_configuration[0].private_network == google_compute_network.crawler.id
    error_message = "Cloud SQL 的 private_network 必須指向 crawler-vpc。"
  }

  assert {
    condition     = google_sql_database_instance.crawler_db.settings[0].edition == "ENTERPRISE"
    error_message = "Cloud SQL edition 必須明寫 ENTERPRISE（db-custom-* 只能搭配 ENTERPRISE）。"
  }

  assert {
    condition     = google_sql_database_instance.crawler_db.settings[0].tier == "db-custom-2-7680"
    error_message = "Cloud SQL tier 預設應為 db-custom-2-7680。"
  }

  assert {
    condition     = google_sql_database_instance.crawler_db.database_version == "POSTGRES_15"
    error_message = "Cloud SQL 預設應為 PostgreSQL 15。"
  }

  assert {
    condition     = google_sql_database_instance.crawler_db.deletion_protection == var.deletion_protection && var.deletion_protection == true
    error_message = "Cloud SQL deletion_protection 必須跟隨 var.deletion_protection（預設 true）。"
  }

  assert {
    condition     = google_sql_database_instance.crawler_db.region == "asia-east1"
    error_message = "Cloud SQL 應在 var.region（asia-east1）。"
  }

  # Private Services Access：Cloud SQL 私有 IP 的前提。
  assert {
    condition     = google_compute_global_address.private_services.purpose == "VPC_PEERING" && google_compute_global_address.private_services.address_type == "INTERNAL"
    error_message = "Private Services Access 保留範圍必須是 INTERNAL / VPC_PEERING。"
  }

  assert {
    condition = (
      google_service_networking_connection.private_services.service == "servicenetworking.googleapis.com" &&
      contains(google_service_networking_connection.private_services.reserved_peering_ranges, google_compute_global_address.private_services.name) &&
      google_service_networking_connection.private_services.network == google_compute_network.crawler.id
    )
    error_message = "service networking 連線必須使用 crawler-private-services 範圍並接在 crawler-vpc。"
  }
}

# =====================================================================
# GCS raw bucket：統一權限、public access 強制擋、30 天轉 COLDLINE、預設不刪
# =====================================================================
run "gcs_raw_bucket_defaults" {
  command = plan

  assert {
    condition     = google_storage_bucket.raw_scraped.name == "qsearch-test-raw-scraped"
    error_message = "raw bucket 預設名稱應為 <project_id>-raw-scraped。"
  }

  assert {
    condition     = google_storage_bucket.raw_scraped.uniform_bucket_level_access == true
    error_message = "raw bucket 必須開啟 uniform bucket-level access。"
  }

  assert {
    condition     = google_storage_bucket.raw_scraped.public_access_prevention == "enforced"
    error_message = "raw bucket 的 public_access_prevention 必須是 enforced。"
  }

  assert {
    condition     = google_storage_bucket.raw_scraped.force_destroy == false
    error_message = "raw bucket 不可 force_destroy。"
  }

  assert {
    condition     = google_storage_bucket.raw_scraped.location == "ASIA-EAST1"
    error_message = "raw bucket 應在 upper(var.region)。"
  }

  assert {
    condition = length([
      for r in google_storage_bucket.raw_scraped.lifecycle_rule : r
      if one(r.action).type == "SetStorageClass" && one(r.action).storage_class == "COLDLINE" && one(r.condition).age == var.raw_coldline_after_days
    ]) == 1
    error_message = "raw bucket 必須有一條 SetStorageClass → COLDLINE 的規則，age = var.raw_coldline_after_days。"
  }

  assert {
    condition     = var.raw_coldline_after_days == 30
    error_message = "raw_coldline_after_days 預設必須是 30（架構 int.txt：30 天轉冷）。"
  }

  assert {
    condition     = length([for r in google_storage_bucket.raw_scraped.lifecycle_rule : r if one(r.action).type == "Delete"]) == 0
    error_message = "raw_delete_after_days = null（預設）時不可有 Delete 規則。"
  }

  assert {
    condition     = length(google_storage_bucket.raw_scraped.lifecycle_rule) == 1
    error_message = "預設只應有 1 條 lifecycle 規則（COLDLINE）。"
  }

  # least privilege：worker 只寫、parser 只讀，且都在 bucket 層級。
  assert {
    condition = (
      google_storage_bucket_iam_member.worker_create_raw.bucket == google_storage_bucket.raw_scraped.name &&
      google_storage_bucket_iam_member.worker_create_raw.role == "roles/storage.objectCreator" &&
      google_storage_bucket_iam_member.worker_create_raw.member == "serviceAccount:${google_service_account.worker.email}"
    )
    error_message = "worker 對 raw bucket 應只有 roles/storage.objectCreator。"
  }

  assert {
    condition = (
      google_storage_bucket_iam_member.parser_read_raw.bucket == google_storage_bucket.raw_scraped.name &&
      google_storage_bucket_iam_member.parser_read_raw.role == "roles/storage.objectViewer" &&
      google_storage_bucket_iam_member.parser_read_raw.member == "serviceAccount:${google_service_account.parser.email}"
    )
    error_message = "parser 對 raw bucket 應只有 roles/storage.objectViewer。"
  }
}

# =====================================================================
# Pub/Sub：schema 綁定、parser 訂閱保留已確認訊息（Seek）、DLQ、push OIDC
# =====================================================================
run "pubsub_fetched_topic_schema_binding" {
  command = plan

  assert {
    condition     = google_pubsub_schema.qsearch_event_v1.type == "AVRO"
    error_message = "qsearch-crawl-event-v1 schema 必須是 AVRO。"
  }

  assert {
    condition     = google_pubsub_schema.qsearch_event_v1.definition == file("${path.module}/schemas/qsearch-crawl-event-v1.avsc")
    error_message = "schema definition 必須來自 schemas/qsearch-crawl-event-v1.avsc。"
  }

  assert {
    condition     = google_pubsub_topic.qsearch_fetched.schema_settings[0].schema == google_pubsub_schema.qsearch_event_v1.id
    error_message = "qsearch-crawl-fetched 必須綁定 qsearch-crawl-event-v1 schema。"
  }

  assert {
    condition     = google_pubsub_topic.qsearch_fetched.schema_settings[0].encoding == "JSON"
    error_message = "qsearch-crawl-fetched 的 schema encoding 必須明寫 JSON。"
  }

  assert {
    condition     = google_pubsub_topic.crawler_tasks.message_retention_duration == "86400s"
    error_message = "crawler-tasks 主題保留期應為 86400s。"
  }
}

run "pubsub_parser_subscription_seek_and_dlq" {
  command = plan

  assert {
    condition     = google_pubsub_subscription.qsearch_parser_sub.topic == google_pubsub_topic.qsearch_fetched.id
    error_message = "qsearch-parser-sub 必須訂閱 qsearch-crawl-fetched。"
  }

  assert {
    condition     = google_pubsub_subscription.qsearch_parser_sub.retain_acked_messages == true
    error_message = "qsearch-parser-sub 必須 retain_acked_messages = true（Q5/Q6 回放靠 Seek）。"
  }

  assert {
    condition     = google_pubsub_subscription.qsearch_parser_sub.message_retention_duration == "604800s"
    error_message = "qsearch-parser-sub 保留期必須是 604800s（7 天）。"
  }

  # 文件確認：保留期下限 10 分鐘、上限 31 天（2678400s）。
  assert {
    condition = alltrue([
      for s in [google_pubsub_subscription.qsearch_parser_sub, google_pubsub_subscription.audit_sub] :
      tonumber(trimsuffix(s.message_retention_duration, "s")) >= 600 && tonumber(trimsuffix(s.message_retention_duration, "s")) <= 2678400
    ])
    error_message = "訂閱保留期必須介於 600s 與 2678400s（31 天）之間。"
  }

  assert {
    condition     = google_pubsub_subscription.qsearch_parser_sub.push_config[0].push_endpoint == "${google_cloud_run_v2_service.parser.uri}/events/crawl-fetched"
    error_message = "qsearch-parser-sub 必須 push 到 parser 的 /events/crawl-fetched（不是 /push）。"
  }

  assert {
    condition     = google_pubsub_subscription.qsearch_parser_sub.push_config[0].push_endpoint == "https://parser-mock-de.a.run.app/events/crawl-fetched"
    error_message = "qsearch-parser-sub push endpoint 字串不符預期。"
  }

  assert {
    condition     = google_pubsub_subscription.qsearch_parser_sub.push_config[0].oidc_token[0].audience == "https://parser-mock-de.a.run.app"
    error_message = "qsearch-parser-sub 的 OIDC audience 必須是服務 URL（不帶路徑）。"
  }

  assert {
    condition     = google_pubsub_subscription.qsearch_parser_sub.push_config[0].oidc_token[0].service_account_email == google_service_account.push.email
    error_message = "qsearch-parser-sub 必須用 push SA 簽 OIDC token。"
  }

  assert {
    condition     = google_pubsub_subscription.qsearch_parser_sub.dead_letter_policy[0].dead_letter_topic == google_pubsub_topic.parser_dlq.id
    error_message = "qsearch-parser-sub 的死信主題必須是 qsearch-parser-dlq。"
  }

  assert {
    condition     = google_pubsub_subscription.qsearch_parser_sub.dead_letter_policy[0].max_delivery_attempts == 5
    error_message = "qsearch-parser-sub max_delivery_attempts 應為 5。"
  }

  assert {
    condition     = google_pubsub_subscription.qsearch_parser_sub.ack_deadline_seconds == 60
    error_message = "qsearch-parser-sub ack_deadline_seconds 應為 60。"
  }

  assert {
    condition = (
      google_pubsub_subscription.qsearch_parser_sub.retry_policy[0].minimum_backoff == "10s" &&
      google_pubsub_subscription.qsearch_parser_sub.retry_policy[0].maximum_backoff == "600s"
    )
    error_message = "qsearch-parser-sub retry_policy 應為 10s–600s。"
  }
}

run "pubsub_crawl_sub_push_and_dlq" {
  command = plan

  assert {
    condition     = google_pubsub_subscription.crawl_sub.topic == google_pubsub_topic.crawler_tasks.id
    error_message = "crawl-sub 必須訂閱 crawler-tasks。"
  }

  assert {
    condition     = google_pubsub_subscription.crawl_sub.push_config[0].push_endpoint == "${google_cloud_run_v2_service.crawler_worker.uri}/push"
    error_message = "crawl-sub 必須 push 到 crawler-worker 的 /push。"
  }

  assert {
    condition     = google_pubsub_subscription.crawl_sub.push_config[0].push_endpoint == "https://crawler-worker-mock-de.a.run.app/push"
    error_message = "crawl-sub push endpoint 字串不符預期。"
  }

  assert {
    condition     = google_pubsub_subscription.crawl_sub.push_config[0].oidc_token[0].audience == "https://crawler-worker-mock-de.a.run.app"
    error_message = "crawl-sub 的 OIDC audience 必須是服務 URL（不帶路徑）。"
  }

  assert {
    condition     = google_pubsub_subscription.crawl_sub.push_config[0].oidc_token[0].service_account_email == google_service_account.push.email
    error_message = "crawl-sub 必須用 push SA 簽 OIDC token。"
  }

  assert {
    condition     = google_pubsub_subscription.crawl_sub.dead_letter_policy[0].dead_letter_topic == google_pubsub_topic.crawler_tasks_dlq.id
    error_message = "crawl-sub 的死信主題必須是 crawler-tasks-dlq。"
  }

  assert {
    condition     = google_pubsub_subscription.crawl_sub.dead_letter_policy[0].max_delivery_attempts == 10
    error_message = "crawl-sub max_delivery_attempts 應為 10。"
  }

  # 文件確認：max_delivery_attempts 合法範圍 5–100。
  assert {
    condition = alltrue([
      for s in [google_pubsub_subscription.crawl_sub, google_pubsub_subscription.qsearch_parser_sub] :
      s.dead_letter_policy[0].max_delivery_attempts >= 5 && s.dead_letter_policy[0].max_delivery_attempts <= 100
    ])
    error_message = "push 訂閱的 max_delivery_attempts 必須在 5–100 之間。"
  }

  assert {
    condition = (
      google_pubsub_subscription.crawl_sub.retry_policy[0].minimum_backoff == "10s" &&
      google_pubsub_subscription.crawl_sub.retry_policy[0].maximum_backoff == "600s"
    )
    error_message = "crawl-sub retry_policy 應為 10s–600s。"
  }

  # audit-sub：獨立 pull 訂閱（沒有 push_config）。
  assert {
    condition = (
      google_pubsub_subscription.audit_sub.topic == google_pubsub_topic.crawler_tasks.id &&
      length(google_pubsub_subscription.audit_sub.push_config) == 0 &&
      google_pubsub_subscription.audit_sub.message_retention_duration == "604800s"
    )
    error_message = "audit-sub 必須是 crawler-tasks 上的 pull 訂閱，保留 604800s。"
  }

  # DLQ 檢查用訂閱
  assert {
    condition = (
      google_pubsub_subscription.crawler_tasks_dlq_inspect.topic == google_pubsub_topic.crawler_tasks_dlq.id &&
      google_pubsub_subscription.parser_dlq_inspect.topic == google_pubsub_topic.parser_dlq.id
    )
    error_message = "每個 DLQ 主題都要有對應的 *_dlq_inspect 訂閱。"
  }
}

run "pubsub_dead_letter_and_push_iam" {
  command = plan

  # 文件確認：DLQ topic 要授 Pub/Sub 服務代理 publisher；來源訂閱要授 subscriber。
  assert {
    condition = alltrue([
      for m in [google_pubsub_topic_iam_member.crawler_dlq_publisher, google_pubsub_topic_iam_member.parser_dlq_publisher] :
      m.role == "roles/pubsub.publisher" && m.member == "serviceAccount:service-123456789012@gcp-sa-pubsub.iam.gserviceaccount.com"
    ])
    error_message = "兩個 DLQ topic 都必須授 Pub/Sub 服務代理 roles/pubsub.publisher。"
  }

  assert {
    condition = (
      google_pubsub_topic_iam_member.crawler_dlq_publisher.topic == google_pubsub_topic.crawler_tasks_dlq.name &&
      google_pubsub_topic_iam_member.parser_dlq_publisher.topic == google_pubsub_topic.parser_dlq.name
    )
    error_message = "DLQ publisher 綁定必須在正確的 DLQ topic 上。"
  }

  assert {
    condition = alltrue([
      for m in [google_pubsub_subscription_iam_member.crawl_sub_dlq_ack, google_pubsub_subscription_iam_member.parser_sub_dlq_ack] :
      m.role == "roles/pubsub.subscriber" && m.member == "serviceAccount:service-123456789012@gcp-sa-pubsub.iam.gserviceaccount.com"
    ])
    error_message = "兩個有 DLQ 的來源訂閱都必須授 Pub/Sub 服務代理 roles/pubsub.subscriber。"
  }

  assert {
    condition = (
      google_pubsub_subscription_iam_member.crawl_sub_dlq_ack.subscription == google_pubsub_subscription.crawl_sub.name &&
      google_pubsub_subscription_iam_member.parser_sub_dlq_ack.subscription == google_pubsub_subscription.qsearch_parser_sub.name
    )
    error_message = "subscriber 綁定必須在 crawl-sub 與 qsearch-parser-sub 上。"
  }

  # 文件確認：Pub/Sub 服務代理需要 iam.serviceAccountTokenCreator 才能替 push SA 簽 OIDC。
  assert {
    condition = (
      google_service_account_iam_member.push_token_creator.service_account_id == google_service_account.push.name &&
      google_service_account_iam_member.push_token_creator.role == "roles/iam.serviceAccountTokenCreator" &&
      google_service_account_iam_member.push_token_creator.member == "serviceAccount:service-123456789012@gcp-sa-pubsub.iam.gserviceaccount.com"
    )
    error_message = "Pub/Sub 服務代理必須對 push SA 有 roles/iam.serviceAccountTokenCreator。"
  }

  # push OIDC 的 SA 必須正好是目標服務 run.invoker 綁定的 SA（否則 push 會 403）。
  assert {
    condition = (
      google_cloud_run_v2_service_iam_member.worker_push_invoker.name == google_cloud_run_v2_service.crawler_worker.name &&
      google_cloud_run_v2_service_iam_member.worker_push_invoker.role == "roles/run.invoker" &&
      google_cloud_run_v2_service_iam_member.worker_push_invoker.member == "serviceAccount:${google_pubsub_subscription.crawl_sub.push_config[0].oidc_token[0].service_account_email}"
    )
    error_message = "crawl-sub 的 OIDC SA 必須是 crawler-worker 的 run.invoker。"
  }

  assert {
    condition = (
      google_cloud_run_v2_service_iam_member.parser_push_invoker.name == google_cloud_run_v2_service.parser.name &&
      google_cloud_run_v2_service_iam_member.parser_push_invoker.role == "roles/run.invoker" &&
      google_cloud_run_v2_service_iam_member.parser_push_invoker.member == "serviceAccount:${google_pubsub_subscription.qsearch_parser_sub.push_config[0].oidc_token[0].service_account_email}"
    )
    error_message = "qsearch-parser-sub 的 OIDC SA 必須是 parser 的 run.invoker。"
  }

  # 不開 allUsers / allAuthenticatedUsers。
  assert {
    condition = alltrue([
      for m in [
        google_cloud_run_v2_service_iam_member.worker_push_invoker,
        google_cloud_run_v2_service_iam_member.parser_push_invoker,
        google_cloud_run_v2_service_iam_member.scheduler_job_invoker,
      ] : !contains(["allUsers", "allAuthenticatedUsers"], m.member)
    ])
    error_message = "Cloud Run invoker 不可綁 allUsers / allAuthenticatedUsers。"
  }

  # worker 發佈 crawl.fetched；api 發佈 crawler-tasks。
  assert {
    condition = (
      google_pubsub_topic_iam_member.worker_publish_fetched.topic == google_pubsub_topic.qsearch_fetched.name &&
      google_pubsub_topic_iam_member.worker_publish_fetched.member == "serviceAccount:${google_service_account.worker.email}" &&
      google_pubsub_topic_iam_member.api_publish_tasks.topic == google_pubsub_topic.crawler_tasks.name &&
      google_pubsub_topic_iam_member.api_publish_tasks.member == "serviceAccount:${google_service_account.api.email}"
    )
    error_message = "api → crawler-tasks、worker → qsearch-crawl-fetched 的 publisher 綁定不符。"
  }
}

# =====================================================================
# Cloud Scheduler 與 Cloud Tasks
# =====================================================================
run "scheduler_and_cloud_tasks" {
  command = plan

  assert {
    condition     = google_cloud_scheduler_job.crawl_tick.schedule == "*/15 * * * *"
    error_message = "crawl-tick 必須每 15 分鐘（*/15 * * * *）。"
  }

  assert {
    condition     = google_cloud_scheduler_job.crawl_tick.time_zone == "Asia/Taipei"
    error_message = "crawl-tick 時區必須是 Asia/Taipei。"
  }

  assert {
    condition = (
      google_cloud_scheduler_job.crawl_tick.http_target[0].http_method == "POST" &&
      google_cloud_scheduler_job.crawl_tick.http_target[0].uri == "${google_cloud_run_v2_service.scheduler.uri}/tick"
    )
    error_message = "crawl-tick 必須 POST 到 scheduler 服務的 /tick。"
  }

  assert {
    condition = (
      google_cloud_scheduler_job.crawl_tick.http_target[0].oidc_token[0].service_account_email == google_service_account.scheduler.email &&
      google_cloud_scheduler_job.crawl_tick.http_target[0].oidc_token[0].audience == google_cloud_run_v2_service.scheduler.uri
    )
    error_message = "crawl-tick 必須用 scheduler invoker SA 的 OIDC，audience = scheduler 服務 URI。"
  }

  assert {
    condition = (
      google_cloud_run_v2_service_iam_member.scheduler_job_invoker.name == google_cloud_run_v2_service.scheduler.name &&
      google_cloud_run_v2_service_iam_member.scheduler_job_invoker.role == "roles/run.invoker" &&
      google_cloud_run_v2_service_iam_member.scheduler_job_invoker.member == "serviceAccount:${google_cloud_scheduler_job.crawl_tick.http_target[0].oidc_token[0].service_account_email}"
    )
    error_message = "Cloud Scheduler 的 OIDC SA 必須是 scheduler 服務的 run.invoker。"
  }

  assert {
    condition     = toset(keys(google_cloud_tasks_queue.crawl)) == toset(["crawl-fb", "crawl-yt", "crawl-news"])
    error_message = "Cloud Tasks 必須正好有 crawl-fb、crawl-yt、crawl-news 三條佇列。"
  }

  assert {
    condition = alltrue([
      for k, q in google_cloud_tasks_queue.crawl :
      q.name == k && q.location == "asia-east1" &&
      q.rate_limits[0].max_dispatches_per_second == 5 &&
      q.rate_limits[0].max_concurrent_dispatches == 10
    ])
    error_message = "每條佇列 rate_limits 應為 5 dispatch/s、10 並行。"
  }

  assert {
    condition = alltrue([
      for q in google_cloud_tasks_queue.crawl :
      q.retry_config[0].max_attempts == 5 &&
      q.retry_config[0].min_backoff == "10s" &&
      q.retry_config[0].max_backoff == "300s"
    ])
    error_message = "每條佇列 retry_config 應為 max_attempts 5、backoff 10s–300s（不可用 -1 無限重試）。"
  }

  assert {
    condition = (
      toset(keys(google_cloud_tasks_queue_iam_member.scheduler_enqueue)) == setunion(toset(keys(google_cloud_tasks_queue.crawl)), toset(keys(google_cloud_tasks_queue.crawl_site))) &&
      alltrue([
        for m in google_cloud_tasks_queue_iam_member.scheduler_enqueue :
        m.role == "roles/cloudtasks.enqueuer" && m.member == "serviceAccount:${google_service_account.scheduler_app.email}"
      ])
    )
    error_message = "scheduler 服務的 runtime SA 必須對每條佇列（含 crawl-ptt／crawl-threads）有 roles/cloudtasks.enqueuer。"
  }

  assert {
    condition     = output.cloud_tasks_queues == ["crawl-fb", "crawl-news", "crawl-yt"]
    error_message = "output cloud_tasks_queues 應列出三條佇列。"
  }
}

# =====================================================================
# Cloud Run：worker Direct VPC egress、scheduler 單實例、timeout 與 ack 對齊
# =====================================================================
run "cloud_run_services_and_job" {
  command = plan

  assert {
    condition     = google_cloud_run_v2_service.crawler_worker.template[0].vpc_access[0].egress == "ALL_TRAFFIC"
    error_message = "crawler-worker 必須 Direct VPC egress = ALL_TRAFFIC（才會走 Cloud NAT 固定 IP）。"
  }

  assert {
    condition = (
      google_cloud_run_v2_service.crawler_worker.template[0].vpc_access[0].network_interfaces[0].network == google_compute_network.crawler.name &&
      google_cloud_run_v2_service.crawler_worker.template[0].vpc_access[0].network_interfaces[0].subnetwork == google_compute_subnetwork.crawler.name
    )
    error_message = "crawler-worker 的 Direct VPC 介面必須接 crawler-vpc / crawler-subnet。"
  }

  assert {
    condition     = google_cloud_run_v2_service.crawler_worker.template[0].timeout == "60s"
    error_message = "crawler-worker timeout 應為 60s。"
  }

  assert {
    condition     = google_cloud_run_v2_service.crawler_worker.template[0].timeout == "${google_pubsub_subscription.crawl_sub.ack_deadline_seconds}s"
    error_message = "crawler-worker timeout 必須與 crawl-sub ack_deadline 對齊。"
  }

  assert {
    condition     = google_cloud_run_v2_service.crawler_worker.template[0].scaling[0].max_instance_count == 50
    error_message = "crawler-worker 最多 50 實例。"
  }

  assert {
    condition     = google_cloud_run_v2_service.crawler_worker.template[0].service_account == google_service_account.worker.email
    error_message = "crawler-worker 必須用自己的 runtime SA（不是 push SA）。"
  }

  assert {
    condition     = google_cloud_run_v2_service.scheduler.template[0].scaling[0].max_instance_count == 1
    error_message = "scheduler 服務 max_instance_count 必須是 1。"
  }

  assert {
    condition     = google_cloud_run_v2_service.scheduler.template[0].service_account == google_service_account.scheduler_app.email
    error_message = "scheduler 服務的 runtime SA 必須是 scheduler_app（與 Cloud Scheduler OIDC SA 分開）。"
  }

  assert {
    condition = (
      google_cloud_run_v2_service.parser.template[0].timeout == "60s" &&
      google_cloud_run_v2_service.parser.template[0].timeout == "${google_pubsub_subscription.qsearch_parser_sub.ack_deadline_seconds}s"
    )
    error_message = "parser timeout 必須是 60s 並與 qsearch-parser-sub ack_deadline 對齊。"
  }

  assert {
    condition = (
      google_cloud_run_v2_service.parser.template[0].max_instance_request_concurrency == 10 &&
      google_cloud_run_v2_service.parser.template[0].scaling[0].max_instance_count == 20 &&
      google_cloud_run_v2_service.parser.template[0].service_account == google_service_account.parser.email
    )
    error_message = "parser 應為 concurrency 10、最多 20 實例、用 parser SA。"
  }

  assert {
    condition = (
      google_cloud_run_v2_job.qsearch_backfill.template[0].template[0].timeout == "3600s" &&
      google_cloud_run_v2_job.qsearch_backfill.template[0].template[0].max_retries == 2
    )
    error_message = "qsearch-backfill job 應為 timeout 3600s、max_retries 2。"
  }

  assert {
    condition = alltrue([
      for s in [
        google_cloud_run_v2_service.api,
        google_cloud_run_v2_service.crawler_worker,
        google_cloud_run_v2_service.scheduler,
        google_cloud_run_v2_service.parser,
      ] : s.deletion_protection == var.deletion_protection && s.location == "asia-east1" && s.template[0].containers[0].ports[0].container_port == 8080
    ]) && google_cloud_run_v2_job.qsearch_backfill.deletion_protection == var.deletion_protection
    error_message = "所有 Cloud Run 服務／job 的 deletion_protection 必須跟隨變數、位於 var.region、容器 port 8080。"
  }

  assert {
    condition     = length(google_cloud_run_v2_service.api.template[0].vpc_access) == 0
    error_message = "crawler-api 不連資料庫，不應設 vpc_access。"
  }

  assert {
    condition = alltrue([
      for v in [
        google_cloud_run_v2_service.scheduler.template[0].vpc_access[0],
        google_cloud_run_v2_service.parser.template[0].vpc_access[0],
        google_cloud_run_v2_job.qsearch_backfill.template[0].template[0].vpc_access[0],
      ] : v.egress == "PRIVATE_RANGES_ONLY" && v.network_interfaces[0].subnetwork == "crawler-subnet"
    ])
    error_message = "要連私有 IP Cloud SQL 的 scheduler／parser／backfill 必須經 VPC，且只導私有網段（PRIVATE_RANGES_ONLY），不走 NAT。"
  }
}

# =====================================================================
# Cloud NAT：MANUAL_ONLY + 保留的固定出口 IP
# =====================================================================
run "cloud_nat_static_egress" {
  command = plan

  assert {
    condition     = google_compute_router_nat.crawler.nat_ip_allocate_option == "MANUAL_ONLY"
    error_message = "Cloud NAT 必須 MANUAL_ONLY，才會使用保留的固定 IP。"
  }

  assert {
    condition = (
      length(google_compute_router_nat.crawler.nat_ips) == 2 &&
      contains(google_compute_router_nat.crawler.nat_ips, google_compute_address.egress.self_link) &&
      contains(google_compute_router_nat.crawler.nat_ips, google_compute_address.egress_2.self_link)
    )
    error_message = "Cloud NAT nat_ips 必須正好是 crawler-egress-ip 與 crawler-egress-ip-2 這兩個保留位址（第三輪加第二個 IP：埠數與封鎖退路）。"
  }

  assert {
    condition = (
      google_compute_router_nat.crawler.router == google_compute_router.crawler.name &&
      google_compute_router.crawler.network == google_compute_network.crawler.id &&
      google_compute_router_nat.crawler.region == "asia-east1"
    )
    error_message = "Cloud NAT 必須掛在 crawler-router（crawler-vpc）上。"
  }

  assert {
    condition     = google_compute_router_nat.crawler.source_subnetwork_ip_ranges_to_nat == "ALL_SUBNETWORKS_ALL_IP_RANGES"
    error_message = "Cloud NAT 應涵蓋所有子網的所有 IP 範圍。"
  }

  assert {
    condition     = output.egress_static_ip == google_compute_address.egress.address
    error_message = "output egress_static_ip 必須是保留位址。"
  }

  assert {
    condition = (
      google_compute_network.crawler.auto_create_subnetworks == false &&
      google_compute_subnetwork.crawler.private_ip_google_access == true &&
      google_compute_subnetwork.crawler.ip_cidr_range == "10.10.0.0/20"
    )
    error_message = "crawler-vpc 必須是自訂子網模式，crawler-subnet 開 Private Google Access。"
  }
}

# =====================================================================
# BigQuery：每日分區、強制分區過濾、90 天到期、欄位與 Avro 一致
# =====================================================================
run "bigquery_ingest_events" {
  command = plan

  assert {
    condition     = google_bigquery_table.ingest_events.time_partitioning[0].type == "DAY"
    error_message = "ingest_events 必須每日分區。"
  }

  assert {
    condition     = google_bigquery_table.ingest_events.require_partition_filter == true
    error_message = "ingest_events 必須 require_partition_filter = true（表頂層）。"
  }

  assert {
    condition     = google_bigquery_table.ingest_events.time_partitioning[0].expiration_ms == 90 * 24 * 60 * 60 * 1000
    error_message = "ingest_events 分區到期必須是 90 天（7776000000 ms）。"
  }

  assert {
    condition     = google_bigquery_table.ingest_events.deletion_protection == var.deletion_protection
    error_message = "ingest_events deletion_protection 必須跟隨變數。"
  }

  assert {
    condition = (
      [for f in jsondecode(google_bigquery_table.ingest_events.schema) : f.name] ==
      [for f in jsondecode(file("${path.module}/schemas/qsearch-crawl-event-v1.avsc")).fields : f.name]
    )
    error_message = "ingest_events 欄位名稱（含順序）必須與 schemas/qsearch-crawl-event-v1.avsc 完全一致。"
  }

  # Avro → BigQuery 型別對應（Pub/Sub BigQuery 訂閱 use_topic_schema 需要相容）。
  assert {
    condition = alltrue([
      for i, f in jsondecode(file("${path.module}/schemas/qsearch-crawl-event-v1.avsc")).fields :
      # 先檢查索引存在（條件運算式只會求值選到的分支），欄位數不同時乾淨地 fail 而不是 Invalid index。
      i < length(jsondecode(google_bigquery_table.ingest_events.schema)) ? jsondecode(google_bigquery_table.ingest_events.schema)[i].type == lookup({
        string  = "STRING"
        int     = "INTEGER"
        long    = "INTEGER"
        boolean = "BOOLEAN"
        float   = "FLOAT"
        double  = "FLOAT"
        bytes   = "BYTES"
      }, f.type, "UNMAPPED") : false
    ])
    error_message = "ingest_events 欄位型別必須與 Avro 型別對應（string→STRING、int→INTEGER…）。"
  }

  assert {
    condition = (
      google_pubsub_subscription.qsearch_to_bq.topic == google_pubsub_topic.qsearch_fetched.id &&
      google_pubsub_subscription.qsearch_to_bq.bigquery_config[0].table == "qsearch-test.sentiment_analytics.ingest_events" &&
      google_pubsub_subscription.qsearch_to_bq.bigquery_config[0].use_topic_schema == true &&
      google_pubsub_subscription.qsearch_to_bq.bigquery_config[0].write_metadata == false
    )
    error_message = "qsearch-fetched-to-bq 必須寫入 sentiment_analytics.ingest_events，use_topic_schema = true、write_metadata = false。"
  }

  assert {
    condition = (
      google_bigquery_dataset_iam_member.pubsub_bq_editor.role == "roles/bigquery.dataEditor" &&
      google_bigquery_dataset_iam_member.pubsub_bq_editor.dataset_id == google_bigquery_dataset.sentiment.dataset_id &&
      google_bigquery_dataset_iam_member.pubsub_bq_editor.member == "serviceAccount:service-123456789012@gcp-sa-pubsub.iam.gserviceaccount.com" &&
      google_project_iam_member.pubsub_bq_metadata.role == "roles/bigquery.metadataViewer" &&
      google_project_iam_member.pubsub_bq_metadata.member == "serviceAccount:service-123456789012@gcp-sa-pubsub.iam.gserviceaccount.com"
    )
    error_message = "Pub/Sub 服務代理必須有 dataset 層級 dataEditor 與 metadataViewer（BigQuery 訂閱寫入者）。"
  }

  assert {
    condition     = output.bigquery_table == "qsearch-test.sentiment_analytics.ingest_events"
    error_message = "output bigquery_table 不符。"
  }
}

# =====================================================================
# 預設值下的可選資源與資源數量（對照 plan：70 to add）
# =====================================================================
run "defaults_optional_resources_off" {
  command = plan

  assert {
    condition = (
      length(google_compute_region_instance_group_manager.crawler_legacy) == 0 &&
      length(google_compute_instance_template.crawler_legacy) == 0 &&
      length(google_compute_health_check.legacy) == 0 &&
      length(google_service_account.legacy) == 0
    )
    error_message = "enable_legacy_gce 預設 false 時不可建立任何 legacy GCE 資源。"
  }

  assert {
    condition     = length(google_cloudbuild_trigger.crawler_api) == 0
    error_message = "github_owner 為空字串（預設）時不可建立 Cloud Build trigger。"
  }

  assert {
    condition     = output.legacy_mig_name == null
    error_message = "預設 legacy_mig_name 應為 null。"
  }

  assert {
    condition = toset(keys(google_project_service.required)) == toset([
      "iam.googleapis.com",
      "run.googleapis.com",
      "pubsub.googleapis.com",
      "cloudscheduler.googleapis.com",
      "cloudtasks.googleapis.com",
      "artifactregistry.googleapis.com",
      "cloudbuild.googleapis.com",
      "compute.googleapis.com",
      "servicenetworking.googleapis.com",
      "sqladmin.googleapis.com",
      "bigquery.googleapis.com",
      "storage.googleapis.com",
      "secretmanager.googleapis.com",
    ])
    error_message = "google_project_service.required 必須正好是 README 列的 13 個 API。"
  }

  assert {
    condition     = alltrue([for s in google_project_service.required : s.disable_on_destroy == false])
    error_message = "API 啟用資源必須 disable_on_destroy = false。"
  }

  assert {
    condition     = google_project_service_identity.pubsub.service == "pubsub.googleapis.com"
    error_message = "必須明確建立 Pub/Sub 服務代理（google-beta）。"
  }
}
