# 本模組使用到的 Google API。plan 不會啟用 API 之外的任何資源；apply 時才會實際啟用。
locals {
  required_apis = toset([
    "iam.googleapis.com",
    "run.googleapis.com",
    "pubsub.googleapis.com",
    "cloudscheduler.googleapis.com",
    "cloudtasks.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "compute.googleapis.com",
    "servicenetworking.googleapis.com", # Cloud SQL private IP 需要 Private Services Access
    "sqladmin.googleapis.com",
    "bigquery.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com", # 代理帳密、Elasticsearch API key
  ])
}

resource "google_project_service" "required" {
  for_each           = local.required_apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# 明確建立 Pub/Sub 服務代理（同 lab 做法），避免 IAM binding 指向尚未存在的 member。
resource "google_project_service_identity" "pubsub" {
  provider   = google-beta
  project    = var.project_id
  service    = "pubsub.googleapis.com"
  depends_on = [google_project_service.required]
}

# Cloud Tasks 服務代理：要替 tasks_invoker SA 產生 OIDC token（見 scheduler_tasks.tf）。
resource "google_project_service_identity" "cloudtasks" {
  provider   = google-beta
  project    = var.project_id
  service    = "cloudtasks.googleapis.com"
  depends_on = [google_project_service.required]
}
