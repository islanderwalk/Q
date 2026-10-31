# Secret Manager：只建立 secret 容器與存取權，不在 Terraform 裡放值（值不進 state）。
# 值用 gcloud secrets versions add 另外寫入；程式在執行時以 runtime SA 讀取。

# Q4 住宅代理帳密：只有 crawler-worker（與用同一 SA 的 ptt-crawl Job）讀得到。
resource "google_secret_manager_secret" "proxy_credentials" {
  secret_id = "residential-proxy-credentials"
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "worker_proxy_access" {
  secret_id = google_secret_manager_secret.proxy_credentials.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}

# Q5／Q6 Elasticsearch API key：parser（串流寫入）與 backfill（回補）使用。
resource "google_secret_manager_secret" "es_api_key" {
  secret_id = "elasticsearch-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "es_api_key_access" {
  for_each = {
    parser   = google_service_account.parser.email
    backfill = google_service_account.backfill.email
  }
  secret_id = google_secret_manager_secret.es_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value}"
}
