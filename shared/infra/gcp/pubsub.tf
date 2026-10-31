# Pub/Sub：任務主題、事件主題（綁 AVRO schema）、DLQ、訂閱。
# 保留期：預設 7 天，上限 31 天（已由 provider 文件確認；原題「上限 7 天」不正確）。

# --- 派工 ---
resource "google_pubsub_topic" "crawler_tasks" {
  name                       = "crawler-tasks"
  message_retention_duration = "86400s"
  depends_on                 = [google_project_service.required]
}

resource "google_pubsub_topic" "crawler_tasks_dlq" {
  name       = "crawler-tasks-dlq"
  depends_on = [google_project_service.required]
}

# 已確認（官方 dead-letter 文件）：「沒有訂閱的 topic，發佈進去的訊息會遺失」，所以 DLQ topic 一定要有訂閱。
# 這個訂閱同時是人工檢查死信的入口。
resource "google_pubsub_subscription" "crawler_tasks_dlq_inspect" {
  name                       = "crawler-tasks-dlq-inspect"
  topic                      = google_pubsub_topic.crawler_tasks_dlq.id
  ack_deadline_seconds       = 30
  message_retention_duration = "2678400s" # 31 天上限，留時間人工查死信
  # 預設 31 天沒有訂閱者活動就刪除訂閱；沒人拉的 DLQ 訂閱一定會被刪，之後的死信就遺失。
  expiration_policy {
    ttl = ""
  }
}

# --- 事件（crawl.fetched）---
resource "google_pubsub_schema" "qsearch_event_v1" {
  name       = "qsearch-crawl-event-v1"
  type       = "AVRO"
  definition = file("${path.module}/schemas/qsearch-crawl-event-v1.avsc")
}

resource "google_pubsub_topic" "qsearch_fetched" {
  name = "qsearch-crawl-fetched"
  schema_settings {
    schema   = google_pubsub_schema.qsearch_event_v1.id
    encoding = "JSON" # 文件確認：預設 ENCODING_UNSPECIFIED，必須明確指定。
  }
  depends_on = [google_project_service.required]
}

resource "google_pubsub_topic" "parser_dlq" {
  name       = "qsearch-parser-dlq"
  depends_on = [google_project_service.required]
}

resource "google_pubsub_subscription" "parser_dlq_inspect" {
  name                       = "qsearch-parser-dlq-inspect"
  topic                      = google_pubsub_topic.parser_dlq.id
  ack_deadline_seconds       = 30
  message_retention_duration = "2678400s" # 31 天上限，留時間人工查死信
  # 預設 31 天沒有訂閱者活動就刪除訂閱；沒人拉的 DLQ 訂閱一定會被刪，之後的死信就遺失。
  expiration_policy {
    ttl = ""
  }
}

# --- 死信轉送權限（文件確認）---
# DLQ topic：Pub/Sub 服務代理需要 roles/pubsub.publisher。
resource "google_pubsub_topic_iam_member" "crawler_dlq_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.crawler_tasks_dlq.name
  role    = "roles/pubsub.publisher"
  member  = local.pubsub_agent_member
}

resource "google_pubsub_topic_iam_member" "parser_dlq_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.parser_dlq.name
  role    = "roles/pubsub.publisher"
  member  = local.pubsub_agent_member
}

# 來源訂閱：Pub/Sub 服務代理需要 roles/pubsub.subscriber 才能 ack 轉送出去的訊息（文件確認）。
resource "google_pubsub_subscription_iam_member" "crawl_sub_dlq_ack" {
  project      = var.project_id
  subscription = google_pubsub_subscription.crawl_sub.name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent_member
}

resource "google_pubsub_subscription_iam_member" "parser_sub_dlq_ack" {
  project      = var.project_id
  subscription = google_pubsub_subscription.qsearch_parser_sub.name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent_member
}

# --- 訂閱 ---
# crawl-sub：push 到 crawler-worker，附 OIDC（push SA）。
resource "google_pubsub_subscription" "crawl_sub" {
  name                 = "crawl-sub"
  topic                = google_pubsub_topic.crawler_tasks.id
  ack_deadline_seconds = 60

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.crawler_worker.uri}/push"
    oidc_token {
      service_account_email = google_service_account.push.email
      # 明寫成服務 URL（不帶路徑）。未指定時預設是整個 push endpoint（含路徑），Cloud Run 是否接受帶路徑的 audience 沒有實測。
      audience = google_cloud_run_v2_service.crawler_worker.uri
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.crawler_tasks_dlq.id
    max_delivery_attempts = 10 # 文件確認範圍 5–100
  }

  depends_on = [
    google_cloud_run_v2_service_iam_member.worker_push_invoker,
    google_service_account_iam_member.push_token_creator,
    google_pubsub_topic_iam_member.crawler_dlq_publisher,
  ]
}

# audit-sub：獨立 pull 訂閱，稽核程式自行拉取，不影響主流程。
resource "google_pubsub_subscription" "audit_sub" {
  name                       = "audit-sub"
  topic                      = google_pubsub_topic.crawler_tasks.id
  ack_deadline_seconds       = 30
  message_retention_duration = "604800s"
  expiration_policy {
    ttl = "" # 稽核程式可能長時間不拉，不要讓訂閱 31 天後被刪
  }
}

# qsearch-parser-sub：push 到 parser。路徑 /events/crawl-fetched 取自 services/parser/main.py（不是 snippet 的 /push）。
resource "google_pubsub_subscription" "qsearch_parser_sub" {
  name                       = "qsearch-parser-sub"
  topic                      = google_pubsub_topic.qsearch_fetched.id
  ack_deadline_seconds       = 60
  retain_acked_messages      = true
  message_retention_duration = "604800s" # 保留已確認訊息 7 天，供 Seek 回放

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.parser.uri}/events/crawl-fetched"
    oidc_token {
      service_account_email = google_service_account.push.email
      # 明寫成服務 URL（不帶路徑）。未指定時預設是整個 push endpoint（含路徑），Cloud Run 是否接受帶路徑的 audience 沒有實測。
      audience = google_cloud_run_v2_service.parser.uri
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.parser_dlq.id
    max_delivery_attempts = 5
  }

  depends_on = [
    google_cloud_run_v2_service_iam_member.parser_push_invoker,
    google_service_account_iam_member.push_token_creator,
    google_pubsub_topic_iam_member.parser_dlq_publisher,
  ]
}

# fetched-to-bq：Pub/Sub BigQuery 訂閱，直接寫入 ingest_events（替代 consumer 的選項）。
# use_topic_schema = true 時，BQ 表欄位須與 Avro schema 相符（見 bq.tf）；write_metadata 改為 false，避免額外的 metadata 欄位。
resource "google_pubsub_subscription" "qsearch_to_bq" {
  name  = "qsearch-fetched-to-bq"
  topic = google_pubsub_topic.qsearch_fetched.id

  bigquery_config {
    table            = "${var.project_id}.${google_bigquery_dataset.sentiment.dataset_id}.${google_bigquery_table.ingest_events.table_id}"
    use_topic_schema = true
    write_metadata   = false
  }

  depends_on = [
    google_bigquery_dataset_iam_member.pubsub_bq_editor,
    google_project_iam_member.pubsub_bq_metadata,
  ]
}
