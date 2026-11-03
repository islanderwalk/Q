# Q6 文字雲：串流斷詞服務 v0／v1／v2、文字雲 API（PM A/B 頁）、真實來源採集器；Pub/Sub parsed-posts（含 DLQ）與 dict-releases；
# 字典 artifact bucket。每一項都對應 q6/realenv/docker-compose.yml 與 scripts/setup.py 裡本機模擬器的同名主題／訂閱。
# 只補 Terraform、離線 plan 與 terraform test；沒有 apply（零成本原則）。
# Elasticsearch 在 Compute Engine VM（crawler-subnet 內的私有 IP，上面裝 Ops Agent），不在這個模組裡建：var.elasticsearch_url。

locals {
  seg_pipelines = toset(["v0", "v1", "v2"]) # v0 舊系統替身、v1 現況、v2 新流程：A/B 期間各寫各的欄位
}

# --- 字典 artifact：本機 dictsrv（nginx）的替身；斷詞服務每 15 秒輪詢 current/manifest.json，發版只增版本、再切 current ---
resource "google_storage_bucket" "dict_artifacts" {
  name                        = "${var.project_id}-qsearch-dict"
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning {
    enabled = true # current/manifest.json 寫壞可回上一版
  }
  depends_on = [google_project_service.required]
}

# --- Pub/Sub ---
resource "google_pubsub_topic" "parsed_posts" {
  name                       = "qsearch-parsed-posts"
  message_retention_duration = "604800s" # 7 天：斷詞服務全部停掉也不掉訊息（上限 31 天）
  depends_on                 = [google_project_service.required]
}

resource "google_pubsub_topic" "parsed_posts_dlq" {
  name       = "qsearch-parsed-posts-dlq"
  depends_on = [google_project_service.required]
}

# PM 頁發版後的控制訊息（qseg/control.py）：只是提示，每台服務以輪詢 manifest 為準，所以不需要 DLQ
resource "google_pubsub_topic" "dict_releases" {
  name       = "qsearch-dict-releases"
  depends_on = [google_project_service.required]
}

# DLQ topic 一定要有訂閱（沒有訂閱的 topic 訊息會遺失），同時是人工查死信的入口；永不過期、保留 31 天
resource "google_pubsub_subscription" "parsed_posts_dlq_inspect" {
  name                       = "qsearch-parsed-posts-dlq-inspect"
  topic                      = google_pubsub_topic.parsed_posts_dlq.id
  ack_deadline_seconds       = 30
  message_retention_duration = "2678400s"
  expiration_policy {
    ttl = ""
  }
}

# 每條流水線一個 pull 訂閱（服務用 streaming pull，不是 push：斷詞要帶字典狀態、常駐）。
# 5 次沒 ack 進 DLQ（本機 setup.py 同值）；重送退避 10 秒～10 分鐘，ES 429／PG 短暫不通時不會把自己打爆。
resource "google_pubsub_subscription" "parsed_posts_seg" {
  for_each             = local.seg_pipelines
  name                 = "qsearch-parsed-posts-seg-${each.key}"
  topic                = google_pubsub_topic.parsed_posts.id
  ack_deadline_seconds = 60
  expiration_policy {
    ttl = "" # 服務重部署期間沒人拉也不能被刪
  }
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.parsed_posts_dlq.id
    max_delivery_attempts = 5
  }
  depends_on = [google_pubsub_topic_iam_member.parsed_posts_dlq_publisher]
}

# 發版控制訊息：ack 600 秒給定向重跑時間（與本機 setup.py 同值）
resource "google_pubsub_subscription" "dict_releases_seg_v2" {
  name                 = "qsearch-dict-releases-seg-v2"
  topic                = google_pubsub_topic.dict_releases.id
  ack_deadline_seconds = 600
  expiration_policy {
    ttl = ""
  }
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

# 死信轉送權限（與 crawler-tasks／parser 的 DLQ 同一套）：服務代理要能發到 DLQ topic、能 ack 來源訂閱
resource "google_pubsub_topic_iam_member" "parsed_posts_dlq_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.parsed_posts_dlq.name
  role    = "roles/pubsub.publisher"
  member  = local.pubsub_agent_member
}

resource "google_pubsub_subscription_iam_member" "parsed_posts_seg_dlq_ack" {
  for_each     = google_pubsub_subscription.parsed_posts_seg
  project      = var.project_id
  subscription = each.value.name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent_member
}

# --- Service accounts（每個服務一個 runtime SA，最小權限）---
resource "google_service_account" "seg" {
  account_id   = "qsearch-seg"
  display_name = "qsearch seg v0/v1/v2 Cloud Run runtime"
  depends_on   = [google_project_service.required]
}

resource "google_service_account" "wordcloud_api" {
  account_id   = "qsearch-wordcloud-api"
  display_name = "qsearch wordcloud API + PM page Cloud Run runtime"
  depends_on   = [google_project_service.required]
}

resource "google_service_account" "collector" {
  account_id   = "qsearch-collector"
  display_name = "qsearch real-source collector Cloud Run runtime"
  depends_on   = [google_project_service.required]
}

# 斷詞服務：拉自己的 parsed-posts 訂閱、讀字典 bucket；v2 另拉 dict-releases
resource "google_pubsub_subscription_iam_member" "seg_pull_parsed_posts" {
  for_each     = google_pubsub_subscription.parsed_posts_seg
  project      = var.project_id
  subscription = each.value.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.seg.email}"
}

resource "google_pubsub_subscription_iam_member" "seg_pull_dict_releases" {
  project      = var.project_id
  subscription = google_pubsub_subscription.dict_releases_seg_v2.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.seg.email}"
}

resource "google_storage_bucket_iam_member" "seg_read_dict" {
  bucket = google_storage_bucket.dict_artifacts.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.seg.email}"
}

# 文字雲 API：發版寫 versions/<版本>/ 與 current/、發 dict-releases；PM 頁重播六渠道樣本發 parsed-posts
resource "google_storage_bucket_iam_member" "api_write_dict" {
  bucket = google_storage_bucket.dict_artifacts.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.wordcloud_api.email}"
}

resource "google_pubsub_topic_iam_member" "api_publish_dict_releases" {
  project = var.project_id
  topic   = google_pubsub_topic.dict_releases.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.wordcloud_api.email}"
}

resource "google_pubsub_topic_iam_member" "api_publish_parsed_posts" {
  project = var.project_id
  topic   = google_pubsub_topic.parsed_posts.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.wordcloud_api.email}"
}

# 採集器：parser 寫本體後發 parsed-posts
resource "google_pubsub_topic_iam_member" "collector_publish_parsed_posts" {
  project = var.project_id
  topic   = google_pubsub_topic.parsed_posts.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.collector.email}"
}

# YouTube Data API 金鑰：只建 secret 容器，值用 gcloud secrets versions add 另外寫（本機是 YOUTUBE_API_KEY_FILE 掛進容器，都不進 git）
resource "google_secret_manager_secret" "youtube_api_key" {
  secret_id = "youtube-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "collector_youtube_key_access" {
  secret_id = google_secret_manager_secret.youtube_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.collector.email}"
}

# Elasticsearch API key：斷詞服務、文字雲 API、採集器也要寫／查 ES（secrets.tf 原本只給 parser 與 backfill）
resource "google_secret_manager_secret_iam_member" "q6_es_api_key_access" {
  for_each = {
    seg           = google_service_account.seg.email
    wordcloud_api = google_service_account.wordcloud_api.email
    collector     = google_service_account.collector.email
  }
  secret_id = google_secret_manager_secret.es_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value}"
}

# --- Cloud Run 服務 ---
# 斷詞服務：常駐 streaming pull，min 1 實例（縮到 0 就沒人拉）；不對外（只有 /metrics 與健康檢查在 9108）；
# Direct VPC（PRIVATE_RANGES_ONLY）連 ES VM 與 Cloud SQL 私有 IP。max_instance_request_concurrency 無 HTTP 流量所以 1。
resource "google_cloud_run_v2_service" "seg" {
  for_each            = local.seg_pipelines
  name                = "qsearch-seg-${each.key}"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = var.deletion_protection

  template {
    service_account                  = google_service_account.seg.email
    max_instance_request_concurrency = 1
    scaling {
      min_instance_count = 1
      max_instance_count = each.key == "v2" ? 10 : 3 # 線上只有 v2 要跟上回補＋串流；v0／v1 只為 A/B
    }
    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.crawler.name
        subnetwork = google_compute_subnetwork.crawler.name
      }
    }
    containers {
      image = var.image_seg
      ports {
        container_port = 9108
      }
      env {
        name  = "PIPELINE"
        value = each.key
      }
      env {
        name  = "PUBSUB_SUBSCRIPTION"
        value = google_pubsub_subscription.parsed_posts_seg[each.key].name
      }
      env {
        name  = "ES_URL"
        value = var.elasticsearch_url
      }
      env {
        name  = "DICT_BASE_URL"
        value = "https://storage.googleapis.com/${google_storage_bucket.dict_artifacts.name}"
      }
    }
  }
  depends_on = [google_project_service.required]
}

# 文字雲 API ＋ PM A/B 頁：timeout 190 秒 = 題目 SLA 180 秒 ＋ 回應餘裕（ES 查詢本身在 api/app.py 以 170 秒逾時）；
# min 1 實例：查詢快取與單飛在記憶體，冷啟動不能吃掉 3 分鐘。不綁 allUsers invoker，誰能開 PM 頁由 IAP／run.invoker 另外決定。
resource "google_cloud_run_v2_service" "wordcloud_api" {
  name                = "qsearch-wordcloud-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = var.deletion_protection

  template {
    service_account                  = google_service_account.wordcloud_api.email
    timeout                          = "190s"
    max_instance_request_concurrency = 8
    scaling {
      min_instance_count = 1
      max_instance_count = 10
    }
    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.crawler.name
        subnetwork = google_compute_subnetwork.crawler.name
      }
    }
    containers {
      image = var.image_wordcloud_api
      ports {
        container_port = 8080
      }
      env {
        name  = "ES_URL"
        value = var.elasticsearch_url
      }
      env {
        name  = "DICT_BUCKET"
        value = google_storage_bucket.dict_artifacts.name
      }
      env {
        name  = "PUBSUB_TOPIC_DICT_RELEASES"
        value = google_pubsub_topic.dict_releases.name
      }
      env {
        name  = "PUBSUB_TOPIC_PARSED_POSTS"
        value = google_pubsub_topic.parsed_posts.name
      }
    }
  }
  depends_on = [google_project_service.required]
}

# 真實來源採集器：單實例（同一個 feed 不能兩台同時抓：禮貌速率、ETag 與退避狀態在 collector_state）；
# 出站走 Direct VPC egress ALL_TRAFFIC → Cloud NAT 固定 IP（目標站看到的永遠是同幾個 IP）。
resource "google_cloud_run_v2_service" "collector" {
  name                = "qsearch-collector"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = var.deletion_protection

  template {
    service_account                  = google_service_account.collector.email
    max_instance_request_concurrency = 1
    scaling {
      min_instance_count = 1
      max_instance_count = 1
    }
    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = google_compute_network.crawler.name
        subnetwork = google_compute_subnetwork.crawler.name
      }
    }
    containers {
      image = var.image_collector
      ports {
        container_port = 9111 # /metrics（collector_requests_total 等）
      }
      env {
        name  = "ES_URL"
        value = var.elasticsearch_url
      }
      env {
        name  = "PUBSUB_TOPIC_PARSED_POSTS"
        value = google_pubsub_topic.parsed_posts.name
      }
      env {
        name = "YOUTUBE_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.youtube_api_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }
  depends_on = [google_project_service.required, google_compute_router_nat.crawler, google_secret_manager_secret_iam_member.collector_youtube_key_access]
}
