# BigQuery：分析資料集與分區表（ingest_events）。
# 表結構與 Avro schema（qsearch-crawl-event-v1）逐欄一致，供 Pub/Sub BigQuery 訂閱 use_topic_schema 寫入。

resource "google_bigquery_dataset" "sentiment" {
  dataset_id                 = "sentiment_analytics"
  location                   = var.region
  description                = "Crawler sentiment analytics"
  delete_contents_on_destroy = false
  depends_on                 = [google_project_service.required]
}

resource "google_bigquery_table" "ingest_events" {
  dataset_id = google_bigquery_dataset.sentiment.dataset_id
  table_id   = "ingest_events"

  deletion_protection      = var.deletion_protection
  require_partition_filter = true # 文件確認：寫在表頂層（time_partitioning 內那個已棄用）

  time_partitioning {
    type          = "DAY"
    expiration_ms = 7776000000 # 90 天
  }

  # 欄位對應 qsearch-crawl-event-v1.avsc。mode 為 NULLABLE（與 Avro 的 required 對應未驗證，見 README）。
  schema = jsonencode([
    { name = "event_id", type = "STRING", mode = "NULLABLE" },
    { name = "schema_version", type = "INTEGER", mode = "NULLABLE" },
    { name = "job_id", type = "STRING", mode = "NULLABLE" },
    { name = "platform", type = "STRING", mode = "NULLABLE" },
    { name = "event_type", type = "STRING", mode = "NULLABLE" },
    { name = "raw_data_uri", type = "STRING", mode = "NULLABLE" },
  ])
}

# 文件確認：BigQuery 訂閱未指定 service_account_email 時，寫入者是 Pub/Sub 服務代理。
# 範例中授予 dataEditor 與 metadataViewer；這裡 dataEditor 限縮到 dataset，metadataViewer 依範例放在 project 層級。
resource "google_bigquery_dataset_iam_member" "pubsub_bq_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.sentiment.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = local.pubsub_agent_member
}

resource "google_project_iam_member" "pubsub_bq_metadata" {
  project = var.project_id
  role    = "roles/bigquery.metadataViewer"
  member  = local.pubsub_agent_member
}

# parser 寫入分析結果（Cloud Run parser 直接寫 BQ 的架構分支）。
resource "google_bigquery_dataset_iam_member" "parser_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.sentiment.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.parser.email}"
}
