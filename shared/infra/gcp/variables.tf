variable "project_id" {
  type        = string
  description = "GCP project ID（不是 display name）。"
}

variable "project_number" {
  type        = string
  description = "GCP project number（純數字）。用來組 Pub/Sub 服務代理 member：service-<NUMBER>@gcp-sa-pubsub.iam.gserviceaccount.com。"

  validation {
    condition     = can(regex("^[0-9]+$", var.project_number))
    error_message = "project_number 必須是純數字，請用 gcloud projects describe <project_id> --format='value(projectNumber)' 取得。"
  }
}

variable "region" {
  type        = string
  description = "主要區域（Cloud Run、Pub/Sub 位置無關、Cloud SQL、BigQuery、Artifact Registry）。"
  default     = "asia-east1"
}

variable "deletion_protection" {
  type        = bool
  description = "套用到 Cloud Run、Cloud SQL、BigQuery table 的 deletion_protection。預設 true；要 destroy 前先設 false 並 apply。"
  default     = true
}

variable "raw_bucket_name" {
  type        = string
  description = "原始資料 GCS bucket 名稱（全球唯一）。null 時用 <project_id>-raw-scraped。"
  default     = null
}

variable "raw_coldline_after_days" {
  type        = number
  description = "原始檔轉 COLDLINE 的天數。架構 int.txt 寫「30 天轉冷」。"
  default     = 30
}

variable "raw_delete_after_days" {
  type        = number
  description = "原始檔刪除的天數。null = 不刪。舊 snippet 寫 age 30 Delete，與「30 天轉冷」衝突，需使用者確認，預設不刪。"
  default     = null
}

# 映像檔變數：預設用 Cloud Run 公開 hello 映像，讓 plan 在還沒 build 映像前也成立。
variable "image_api" {
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "image_worker" {
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "image_parser" {
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "image_scheduler" {
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "image_backfill" {
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/job"
}

variable "github_owner" {
  type        = string
  description = "Cloud Build GitHub trigger 的 owner。空字串 = 不建立 trigger（需先在 GCP 完成 GitHub 連線）。"
  default     = ""
}

variable "github_repo" {
  type    = string
  default = "crawler"
}

variable "enable_legacy_gce" {
  type        = bool
  description = "是否建立 legacy GCE MIG（instance template + 區域 MIG + health check + legacy SA）。預設關閉。"
  default     = false
}

variable "scheduler_cron" {
  type    = string
  default = "*/15 * * * *"
}

variable "scheduler_time_zone" {
  type    = string
  default = "Asia/Taipei"
}

variable "sql_version" {
  type    = string
  default = "POSTGRES_15"
}

variable "sql_tier" {
  type        = string
  description = "shared-core 與 db-custom-* 只能搭配 edition = ENTERPRISE（已由 provider 文件確認）。"
  default     = "db-custom-2-7680"
}

variable "image_ptt_crawl" {
  type        = string
  description = "Q3 PTT 抓取 Job 的映像（預設 Cloud Run job 範例映像，讓 plan 在還沒 build 前成立）。"
  default     = "us-docker.pkg.dev/cloudrun/container/job"
}

variable "ptt_crawl_cron" {
  type        = string
  description = "Q3 PTT 抓取 Job 的排程。每小時一次可滿足「新文章 6 小時內可搜」與「留言每天至少更新一次」。"
  default     = "5 * * * *"
}

variable "backfill_task_count" {
  type        = number
  description = "回補 Job 的 task 數（Q6 依渠道 × 月約 270 批；上限 10,000）。"
  default     = 1
}

variable "backfill_parallelism" {
  type        = number
  description = "回補 Job 同時執行的 task 數，用來限制對 Elasticsearch 的寫入負載。"
  default     = 1
}

variable "sql_backup_enabled" {
  type        = bool
  description = "Cloud SQL 每日備份與 7 天 PITR。一個人維運建議開啟。"
  default     = true
}

variable "cloudbuild_repository_id" {
  type        = string
  description = "Cloud Build 2nd gen 的 google_cloudbuildv2_repository id。空字串 = 不建立 2nd gen trigger。"
  default     = ""
}

variable "cloudbuild_service_account_id" {
  type        = string
  description = "2nd gen trigger 使用的 SA，格式 projects/<project>/serviceAccounts/<email>（新專案必填）。"
  default     = ""
}
