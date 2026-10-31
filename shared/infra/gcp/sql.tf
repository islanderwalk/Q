# Cloud SQL（PostgreSQL 15，私有 IP，pg_bigm，IAM 登入）。
# tier 與 edition 配對：db-custom-* 只能搭配 ENTERPRISE（provider 文件確認）。
# PG16 以上未指定 edition 會預設 ENTERPRISE_PLUS，所以這裡明確寫 ENTERPRISE。

resource "google_sql_database_instance" "crawler_db" {
  name                = "crawler-db"
  region              = var.region
  database_version    = var.sql_version
  deletion_protection = var.deletion_protection

  settings {
    tier    = var.sql_tier
    edition = "ENTERPRISE"

    # Q2 選 pg_bigm：Cloud SQL 需先開這個旗標（預設 off、要重啟）才能 CREATE EXTENSION pg_bigm（文件確認）。
    database_flags {
      name  = "cloudsql.enable_pg_bigm"
      value = "on"
    }
    # 以 IAM 身分登入資料庫，不發密碼（google_sql_user type = CLOUD_IAM_SERVICE_ACCOUNT）。
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.crawler.id
      ssl_mode        = "ENCRYPTED_ONLY" # 只接受 TLS 連線；Cloud SQL connector / Auth Proxy 都支援
    }

    # 一個人維運：每日備份＋7 天 PITR。備份儲存約 US$0.08/GiB-月，資料量小時每月不到 1 美元。
    backup_configuration {
      enabled                        = var.sql_backup_enabled
      start_time                     = "18:00" # UTC，等於台北 02:00
      point_in_time_recovery_enabled = var.sql_backup_enabled
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 7
      }
    }
  }

  # 私有 IP 必須先完成 Private Services Access 連線。
  depends_on = [google_service_networking_connection.private_services]
}

resource "google_sql_database" "crawler" {
  name     = "crawler"
  instance = google_sql_database_instance.crawler_db.name
}

# --- IAM 資料庫登入：需要連 Cloud SQL 的每個 runtime SA ---
locals {
  # key 用靜態字串，for_each 才不會依賴 apply 後才知道的 email。
  sql_iam_sas = {
    worker        = google_service_account.worker.email
    parser        = google_service_account.parser.email
    scheduler_app = google_service_account.scheduler_app.email
    backfill      = google_service_account.backfill.email
  }
}

resource "google_project_iam_member" "sql_client" {
  for_each = local.sql_iam_sas
  project  = var.project_id
  role     = "roles/cloudsql.client"
  member   = "serviceAccount:${each.value}"
}

resource "google_project_iam_member" "sql_instance_user" {
  for_each = local.sql_iam_sas
  project  = var.project_id
  role     = "roles/cloudsql.instanceUser"
  member   = "serviceAccount:${each.value}"
}

# PostgreSQL 的 IAM 使用者名稱要去掉 .gserviceaccount.com（文件確認）。
resource "google_sql_user" "iam_sa" {
  for_each = local.sql_iam_sas
  name     = trimsuffix(each.value, ".gserviceaccount.com")
  instance = google_sql_database_instance.crawler_db.name
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}
