# Cloud Build 觸發（push 到 main 後建置 crawler-api）。
# 已確認（Cloud Build 文件）：github 區塊是 1st gen 連線，已被 2nd gen 取代、只留作 legacy；
# 2024 年 5–6 月後第一次使用 Cloud Build 的專案，建立 trigger 時必須指定 service_account。
# 新專案請用下面的 crawler_api_v2（repository_event_config）；1st gen 只為相容舊 snippet 保留。

# 1st gen（legacy）：github_owner 留空時不建立。

resource "google_cloudbuild_trigger" "crawler_api" {
  count    = var.github_owner == "" ? 0 : 1
  name     = "crawler-api-main"
  location = var.region

  github {
    owner = var.github_owner
    name  = var.github_repo
    push {
      branch = "^main$"
    }
  }

  filename   = "cloudbuild.yaml"
  depends_on = [google_project_service.required]
}

# 2nd gen：cloudbuild_repository_id 留空時不建立（需先建立 google_cloudbuildv2_connection 與 repository）。
resource "google_cloudbuild_trigger" "crawler_api_v2" {
  count           = var.cloudbuild_repository_id == "" ? 0 : 1
  name            = "crawler-api-main-v2"
  location        = var.region
  service_account = var.cloudbuild_service_account_id

  repository_event_config {
    repository = var.cloudbuild_repository_id
    push {
      branch = "^main$"
    }
  }

  filename   = "cloudbuild.yaml"
  depends_on = [google_project_service.required]
}
