# Cloud Run 服務與 Job。
# deletion_protection 一律明確設定（預設 true，provider 文件確認預設即為 true）。
# image 全部用變數；預設 hello 映像讓 plan 成立，實際部署時改 var.image_* 為 Artifact Registry 映像。

# 入口：管理後台 POST /crawl。
resource "google_cloud_run_v2_service" "api" {
  name                = "crawler-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = var.deletion_protection

  template {
    service_account = google_service_account.api.email
    containers {
      image = var.image_api
      ports {
        container_port = 8080
      }
    }
  }
  depends_on = [google_project_service.required]
}

# 抓取 worker：由 Pub/Sub push 觸發（crawl-sub）。
# 出站走 Direct VPC egress（ALL_TRAFFIC），搭配 Cloud NAT 的固定 IP。
# 文件確認：network / subnetwork 至少要給一個；這裡兩者都給。
resource "google_cloud_run_v2_service" "crawler_worker" {
  name                = "crawler-worker"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY" # 只由 Pub/Sub push 與 Cloud Tasks 呼叫（同專案、run.app URL，文件確認屬於 internal）
  deletion_protection = var.deletion_protection

  template {
    service_account                  = google_service_account.worker.email
    timeout                          = "60s" # 與 crawl-sub ack_deadline 60s 對齊；長時間抓取應改走 Cloud Run Jobs（見 README 風險）
    max_instance_request_concurrency = 4     # 預設 80；爬蟲要限制同時連線，速率由 Cloud Tasks 佇列控制
    scaling {
      max_instance_count = 50
    }
    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = google_compute_network.crawler.name
        subnetwork = google_compute_subnetwork.crawler.name
      }
    }
    containers {
      image = var.image_worker
      ports {
        container_port = 8080
      }
    }
  }
  depends_on = [google_project_service.required, google_compute_router_nat.crawler]
}

# 排程大腦：只由 Cloud Scheduler 每 15 分鐘敲一次 /tick。max 1 實例減少重複派工，但派工仍需冪等。
resource "google_cloud_run_v2_service" "scheduler" {
  name                = "scheduler"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY" # 只由 Cloud Scheduler 呼叫
  deletion_protection = var.deletion_protection

  template {
    service_account = google_service_account.scheduler_app.email
    scaling {
      max_instance_count = 1
    }
    # 私有 IP 的 Cloud SQL 只能經 VPC 連線；只把私有網段導進 VPC，其餘照常走 Google 網路。
    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.crawler.name
        subnetwork = google_compute_subnetwork.crawler.name
      }
    }
    containers {
      image = var.image_scheduler
      ports {
        container_port = 8080
      }
    }
  }
  depends_on = [google_project_service.required]
}

# parser：Pub/Sub push（qsearch-parser-sub）。timeout 60s 與 ack_deadline 對齊，避免超時後重送。
resource "google_cloud_run_v2_service" "parser" {
  name                = "parser"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY" # 只由 Pub/Sub push 呼叫
  deletion_protection = var.deletion_protection

  template {
    service_account                  = google_service_account.parser.email
    timeout                          = "60s"
    max_instance_request_concurrency = 10
    scaling {
      max_instance_count = 20
    }
    # 私有 IP 的 Cloud SQL 只能經 VPC 連線；只把私有網段導進 VPC，其餘照常走 Google 網路。
    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.crawler.name
        subnetwork = google_compute_subnetwork.crawler.name
      }
    }
    containers {
      image = var.image_parser
      ports {
        container_port = 8080
      }
    }
  }
  depends_on = [google_project_service.required]
}

# 回補（Q5 指標回補、Q6 斷詞回朔）：Cloud Run Job，跑完即結束，沒有 HTTP 入口。雙層 template.template 是 provider 結構（文件確認）。
# Q6 依「渠道 × 月」約 270 批：task_count = 批數，每個 task 以 CLOUD_RUN_TASK_INDEX 選自己的批次；
# parallelism 限制同時寫入 Elasticsearch 的 task 數，保護線上查詢。預設 1/1，實際跑時以變數覆寫。
resource "google_cloud_run_v2_job" "qsearch_backfill" {
  name                = "qsearch-backfill"
  location            = var.region
  deletion_protection = var.deletion_protection

  template {
    task_count  = var.backfill_task_count
    parallelism = var.backfill_parallelism
    template {
      service_account = google_service_account.backfill.email
      timeout         = "3600s"
      max_retries     = 2
      # 私有 IP 的 Cloud SQL 只能經 VPC 連線；只把私有網段導進 VPC，其餘照常走 Google 網路。
      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.crawler.name
          subnetwork = google_compute_subnetwork.crawler.name
        }
      }
      containers {
        image = var.image_backfill
      }
    }
  }
  depends_on = [google_project_service.required]
}

# Q3：PTT 八卦板抓取 Job（discover 新文章＋refresh 7 天內留言），由 Cloud Scheduler 每小時執行（scheduler_tasks.tf）。
# 實測尖峰每小時約 450 次請求；以 0.5–1 req/s 禮貌速率需 450–900 秒，超過 Job 預設 600 秒，所以明寫 3000 秒。
resource "google_cloud_run_v2_job" "ptt_crawl" {
  name                = "ptt-crawl"
  location            = var.region
  deletion_protection = var.deletion_protection

  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.worker.email
      timeout         = "3000s"
      max_retries     = 1
      vpc_access {
        egress = "ALL_TRAFFIC" # 經 Cloud NAT 固定 IP 出去（GCP 建議爬蟲不要用 Cloud Run 共用 IP 池）
        network_interfaces {
          network    = google_compute_network.crawler.name
          subnetwork = google_compute_subnetwork.crawler.name
        }
      }
      containers {
        image = var.image_ptt_crawl
      }
    }
  }
  depends_on = [google_project_service.required, google_compute_router_nat.crawler]
}

# --- Cloud Run invoker 綁定（只給單一 SA，不開 allUsers）---
resource "google_cloud_run_v2_service_iam_member" "worker_push_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.crawler_worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.push.email}"
}

resource "google_cloud_run_v2_service_iam_member" "parser_push_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.parser.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.push.email}"
}

# Cloud Scheduler OIDC 的 SA 需要 run.invoker（文件確認：Cloud Run 目標需 roles/run.invoker）。
resource "google_cloud_run_v2_service_iam_member" "scheduler_job_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.scheduler.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}
