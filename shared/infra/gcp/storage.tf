# 原始資料 bucket（raw）與 Artifact Registry（容器映像）。

locals {
  raw_bucket_name = coalesce(var.raw_bucket_name, "${var.project_id}-raw-scraped")
}

resource "google_storage_bucket" "raw_scraped" {
  name                        = local.raw_bucket_name
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  # 架構 int.txt：30 天轉冷（COLDLINE）。
  lifecycle_rule {
    condition {
      age = var.raw_coldline_after_days
    }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }

  # 舊 snippet 的「age 30 Delete」與 int.txt 衝突，改為可選；預設不刪。
  dynamic "lifecycle_rule" {
    for_each = var.raw_delete_after_days == null ? [] : [var.raw_delete_after_days]
    content {
      condition {
        age = lifecycle_rule.value
      }
      action {
        type = "Delete"
      }
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_artifact_registry_repository" "crawler" {
  location      = var.region
  repository_id = "crawler"
  format        = "DOCKER"
  description   = "crawler / parser / backfill 容器映像"
  depends_on    = [google_project_service.required]
}
