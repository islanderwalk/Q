# 第三輪（2026-11-02）：Q6 文字雲（q6.tf）、Cloud NAT 配埠與日誌、Cloud SQL 逾時旗標。
# 與其他測試檔相同：mock provider、command = plan，不呼叫 GCP、不建資源；computed 值用 override_resource 給定。

mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id     = "qsearch-test"
  project_number = "123456789012"

  image_api           = "asia-east1-docker.pkg.dev/qsearch-test/crawler/api:test"
  image_worker        = "asia-east1-docker.pkg.dev/qsearch-test/crawler/worker:test"
  image_parser        = "asia-east1-docker.pkg.dev/qsearch-test/crawler/parser:test"
  image_scheduler     = "asia-east1-docker.pkg.dev/qsearch-test/crawler/scheduler:test"
  image_backfill      = "asia-east1-docker.pkg.dev/qsearch-test/crawler/backfill:test"
  image_ptt_crawl     = "asia-east1-docker.pkg.dev/qsearch-test/crawler/ptt-crawl:test"
  image_seg           = "asia-east1-docker.pkg.dev/qsearch-test/crawler/qsearch-seg:test"
  image_wordcloud_api = "asia-east1-docker.pkg.dev/qsearch-test/crawler/qsearch-wordcloud-api:test"
  image_collector     = "asia-east1-docker.pkg.dev/qsearch-test/crawler/qsearch-collector:test"
}

override_resource {
  target          = google_compute_address.egress
  override_during = plan
  values = {
    id        = "projects/qsearch-test/regions/asia-east1/addresses/crawler-egress-ip"
    self_link = "https://www.googleapis.com/compute/v1/projects/qsearch-test/regions/asia-east1/addresses/crawler-egress-ip"
    address   = "203.0.113.10"
  }
}

override_resource {
  target          = google_compute_address.egress_2
  override_during = plan
  values = {
    id        = "projects/qsearch-test/regions/asia-east1/addresses/crawler-egress-ip-2"
    self_link = "https://www.googleapis.com/compute/v1/projects/qsearch-test/regions/asia-east1/addresses/crawler-egress-ip-2"
    address   = "203.0.113.11"
  }
}

override_resource {
  target          = google_pubsub_topic.parsed_posts
  override_during = plan
  values = {
    id = "projects/qsearch-test/topics/qsearch-parsed-posts"
  }
}

override_resource {
  target          = google_pubsub_topic.parsed_posts_dlq
  override_during = plan
  values = {
    id = "projects/qsearch-test/topics/qsearch-parsed-posts-dlq"
  }
}

override_resource {
  target          = google_pubsub_topic.dict_releases
  override_during = plan
  values = {
    id = "projects/qsearch-test/topics/qsearch-dict-releases"
  }
}

override_resource {
  target          = google_service_account.seg
  override_during = plan
  values = {
    email = "qsearch-seg@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/qsearch-seg@qsearch-test.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_service_account.wordcloud_api
  override_during = plan
  values = {
    email = "qsearch-wordcloud-api@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/qsearch-wordcloud-api@qsearch-test.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_service_account.collector
  override_during = plan
  values = {
    email = "qsearch-collector@qsearch-test.iam.gserviceaccount.com"
    name  = "projects/qsearch-test/serviceAccounts/qsearch-collector@qsearch-test.iam.gserviceaccount.com"
  }
}

# =====================================================================
# Pub/Sub：每條流水線一個 pull 訂閱，重送退避 10s–600s、5 次進 DLQ、永不過期；DLQ 檢查訂閱保留 31 天
# =====================================================================
run "q6_pubsub_pull_subscriptions_retry_and_dlq" {
  command = plan

  assert {
    condition     = toset(keys(google_pubsub_subscription.parsed_posts_seg)) == toset(["v0", "v1", "v2"])
    error_message = "parsed-posts 要有 v0／v1／v2 三個訂閱（A/B 期間各寫各的欄位）。"
  }

  assert {
    condition = alltrue([
      for k, s in google_pubsub_subscription.parsed_posts_seg :
      s.name == "qsearch-parsed-posts-seg-${k}" &&
      s.topic == google_pubsub_topic.parsed_posts.id &&
      length(s.push_config) == 0 &&
      s.ack_deadline_seconds == 60 &&
      s.expiration_policy[0].ttl == "" &&
      s.retry_policy[0].minimum_backoff == "10s" &&
      s.retry_policy[0].maximum_backoff == "600s" &&
      s.dead_letter_policy[0].dead_letter_topic == google_pubsub_topic.parsed_posts_dlq.id &&
      s.dead_letter_policy[0].max_delivery_attempts == 5
    ])
    error_message = "每個 parsed-posts 訂閱必須是 pull、ack 60 秒、退避 10s–600s、5 次進 parsed-posts DLQ、永不過期。"
  }

  assert {
    condition = (
      google_pubsub_subscription.parsed_posts_dlq_inspect.topic == google_pubsub_topic.parsed_posts_dlq.id &&
      google_pubsub_subscription.parsed_posts_dlq_inspect.expiration_policy[0].ttl == "" &&
      google_pubsub_subscription.parsed_posts_dlq_inspect.message_retention_duration == "2678400s"
    )
    error_message = "parsed-posts DLQ 必須有永不過期、保留 31 天的檢查訂閱（沒有訂閱的 topic 訊息會遺失）。"
  }

  assert {
    condition = (
      google_pubsub_subscription.dict_releases_seg_v2.topic == google_pubsub_topic.dict_releases.id &&
      google_pubsub_subscription.dict_releases_seg_v2.ack_deadline_seconds == 600 &&
      length(google_pubsub_subscription.dict_releases_seg_v2.dead_letter_policy) == 0 &&
      google_pubsub_subscription.dict_releases_seg_v2.expiration_policy[0].ttl == ""
    )
    error_message = "dict-releases 訂閱：ack 600 秒給定向重跑時間、不需要 DLQ（服務以輪詢 manifest 為準）、永不過期。"
  }

  assert {
    condition = (
      google_pubsub_topic_iam_member.parsed_posts_dlq_publisher.member == "serviceAccount:service-123456789012@gcp-sa-pubsub.iam.gserviceaccount.com" &&
      google_pubsub_topic_iam_member.parsed_posts_dlq_publisher.role == "roles/pubsub.publisher" &&
      toset(keys(google_pubsub_subscription_iam_member.parsed_posts_seg_dlq_ack)) == toset(["v0", "v1", "v2"]) &&
      alltrue([for m in google_pubsub_subscription_iam_member.parsed_posts_seg_dlq_ack : m.role == "roles/pubsub.subscriber" && m.member == "serviceAccount:service-123456789012@gcp-sa-pubsub.iam.gserviceaccount.com"])
    )
    error_message = "死信轉送權限：Pub/Sub 服務代理要能發到 DLQ topic、能 ack 三個來源訂閱。"
  }

  assert {
    condition     = google_pubsub_topic.parsed_posts.message_retention_duration == "604800s"
    error_message = "parsed-posts 主題保留 7 天（斷詞服務全停也不掉訊息）。"
  }
}

# =====================================================================
# Cloud Run：斷詞服務常駐 pull（min 1）、不對外、私有網段；API timeout 190 秒；採集器單實例走 NAT
# =====================================================================
run "q6_cloud_run_services" {
  command = plan

  assert {
    condition     = toset(keys(google_cloud_run_v2_service.seg)) == toset(["v0", "v1", "v2"])
    error_message = "斷詞服務要有 v0／v1／v2 三個。"
  }

  assert {
    condition = alltrue([
      for k, s in google_cloud_run_v2_service.seg :
      s.name == "qsearch-seg-${k}" &&
      s.ingress == "INGRESS_TRAFFIC_INTERNAL_ONLY" &&
      s.template[0].service_account == google_service_account.seg.email &&
      s.template[0].scaling[0].min_instance_count == 1 &&
      s.template[0].scaling[0].max_instance_count == (k == "v2" ? 10 : 3) &&
      s.template[0].max_instance_request_concurrency == 1 &&
      s.template[0].vpc_access[0].egress == "PRIVATE_RANGES_ONLY" &&
      s.template[0].vpc_access[0].network_interfaces[0].network == google_compute_network.crawler.name &&
      s.template[0].containers[0].ports[0].container_port == 9108 &&
      [for e in s.template[0].containers[0].env : e.value if e.name == "PIPELINE"][0] == k &&
      [for e in s.template[0].containers[0].env : e.value if e.name == "PUBSUB_SUBSCRIPTION"][0] == google_pubsub_subscription.parsed_posts_seg[k].name &&
      [for e in s.template[0].containers[0].env : e.value if e.name == "ES_URL"][0] == "http://10.10.0.10:9200"
    ])
    error_message = "斷詞服務：常駐 pull（min 1、v2 最多 10、其餘 3）、不對外、私有網段、port 9108、PIPELINE／PUBSUB_SUBSCRIPTION／ES_URL 對應。"
  }

  assert {
    condition = (
      google_cloud_run_v2_service.wordcloud_api.template[0].timeout == "190s" &&
      google_cloud_run_v2_service.wordcloud_api.template[0].scaling[0].min_instance_count == 1 &&
      google_cloud_run_v2_service.wordcloud_api.template[0].scaling[0].max_instance_count == 10 &&
      google_cloud_run_v2_service.wordcloud_api.template[0].max_instance_request_concurrency == 8 &&
      google_cloud_run_v2_service.wordcloud_api.template[0].vpc_access[0].egress == "PRIVATE_RANGES_ONLY" &&
      google_cloud_run_v2_service.wordcloud_api.template[0].service_account == google_service_account.wordcloud_api.email &&
      google_cloud_run_v2_service.wordcloud_api.template[0].containers[0].ports[0].container_port == 8080
    )
    error_message = "文字雲 API：timeout 190 秒（SLA 180 ＋ 餘裕）、min 1 max 10、並行 8、私有網段、自己的 SA、port 8080。"
  }

  assert {
    condition = (
      google_cloud_run_v2_service.collector.template[0].scaling[0].min_instance_count == 1 &&
      google_cloud_run_v2_service.collector.template[0].scaling[0].max_instance_count == 1 &&
      google_cloud_run_v2_service.collector.template[0].vpc_access[0].egress == "ALL_TRAFFIC" &&
      google_cloud_run_v2_service.collector.ingress == "INGRESS_TRAFFIC_INTERNAL_ONLY" &&
      google_cloud_run_v2_service.collector.template[0].service_account == google_service_account.collector.email &&
      [for e in google_cloud_run_v2_service.collector.template[0].containers[0].env : e.value_source[0].secret_key_ref[0].secret if e.name == "YOUTUBE_API_KEY"][0] == google_secret_manager_secret.youtube_api_key.secret_id
    )
    error_message = "採集器：單實例、出站 ALL_TRAFFIC 走 Cloud NAT 固定 IP、不對外、YouTube 金鑰從 Secret Manager 注入。"
  }

  assert {
    condition = alltrue([
      for s in concat(values(google_cloud_run_v2_service.seg), [google_cloud_run_v2_service.wordcloud_api, google_cloud_run_v2_service.collector]) :
      s.deletion_protection == var.deletion_protection && s.location == "asia-east1"
    ])
    error_message = "Q6 服務的 deletion_protection 必須跟隨變數、位於 var.region。"
  }
}

# =====================================================================
# 最小權限：各服務只拿自己要用的訂閱／主題／bucket／secret；Cloud SQL IAM 登入
# =====================================================================
run "q6_least_privilege_iam" {
  command = plan

  assert {
    condition = (
      toset(keys(google_pubsub_subscription_iam_member.seg_pull_parsed_posts)) == toset(["v0", "v1", "v2"]) &&
      alltrue([for m in google_pubsub_subscription_iam_member.seg_pull_parsed_posts : m.role == "roles/pubsub.subscriber" && m.member == "serviceAccount:${google_service_account.seg.email}"]) &&
      google_pubsub_subscription_iam_member.seg_pull_dict_releases.subscription == google_pubsub_subscription.dict_releases_seg_v2.name &&
      google_storage_bucket_iam_member.seg_read_dict.role == "roles/storage.objectViewer"
    )
    error_message = "斷詞服務只能拉自己的訂閱、唯讀字典 bucket。"
  }

  assert {
    condition = (
      google_pubsub_topic_iam_member.api_publish_dict_releases.topic == google_pubsub_topic.dict_releases.name &&
      google_pubsub_topic_iam_member.api_publish_parsed_posts.topic == google_pubsub_topic.parsed_posts.name &&
      google_storage_bucket_iam_member.api_write_dict.role == "roles/storage.objectAdmin" &&
      google_storage_bucket_iam_member.api_write_dict.member == "serviceAccount:${google_service_account.wordcloud_api.email}" &&
      google_pubsub_topic_iam_member.collector_publish_parsed_posts.member == "serviceAccount:${google_service_account.collector.email}"
    )
    error_message = "API 發版寫字典 bucket 與 dict-releases、重播發 parsed-posts；採集器只發 parsed-posts。"
  }

  assert {
    condition = (
      toset(keys(google_secret_manager_secret_iam_member.q6_es_api_key_access)) == toset(["seg", "wordcloud_api", "collector"]) &&
      google_secret_manager_secret_iam_member.collector_youtube_key_access.member == "serviceAccount:${google_service_account.collector.email}" &&
      google_secret_manager_secret.youtube_api_key.secret_id == "youtube-api-key" &&
      contains(keys(google_sql_user.iam_sa), "seg") && contains(keys(google_sql_user.iam_sa), "wordcloud_api") && contains(keys(google_sql_user.iam_sa), "collector")
    )
    error_message = "ES 金鑰給三個 Q6 服務、YouTube 金鑰只給採集器；三個 SA 都有 Cloud SQL IAM 使用者。"
  }
}

# =====================================================================
# Cloud NAT：兩個固定 IP、動態配埠 1024–8192、只記錯誤日誌
# =====================================================================
run "cloud_nat_two_ips_dynamic_ports_and_error_logging" {
  command = plan

  assert {
    condition = (
      length(google_compute_router_nat.crawler.nat_ips) == 2 &&
      contains(google_compute_router_nat.crawler.nat_ips, google_compute_address.egress.self_link) &&
      contains(google_compute_router_nat.crawler.nat_ips, google_compute_address.egress_2.self_link) &&
      google_compute_router_nat.crawler.nat_ip_allocate_option == "MANUAL_ONLY"
    )
    error_message = "Cloud NAT 要用兩個保留的固定 IP（MANUAL_ONLY）。"
  }

  assert {
    condition = (
      google_compute_router_nat.crawler.enable_dynamic_port_allocation == true &&
      google_compute_router_nat.crawler.enable_endpoint_independent_mapping == false &&
      google_compute_router_nat.crawler.min_ports_per_vm == 1024 &&
      google_compute_router_nat.crawler.max_ports_per_vm == 8192
    )
    error_message = "Cloud NAT 動態配埠：每台 1024–8192（2 的冪），endpoint-independent mapping 關閉。"
  }

  assert {
    condition     = google_compute_router_nat.crawler.log_config[0].enable == true && google_compute_router_nat.crawler.log_config[0].filter == "ERRORS_ONLY"
    error_message = "Cloud NAT 要開 ERRORS_ONLY 日誌（配埠用盡才看得到）。"
  }

  assert {
    condition     = tolist(output.egress_static_ips) == tolist(["203.0.113.10", "203.0.113.11"])
    error_message = "egress_static_ips 要列出兩個固定 IP（目標站白名單）。"
  }
}

# =====================================================================
# Cloud SQL：交易閒置 60 秒踢掉、慢查詢 1 秒進日誌
# =====================================================================
run "cloud_sql_timeout_flags" {
  command = plan

  assert {
    condition = (
      contains([for f in google_sql_database_instance.crawler_db.settings[0].database_flags : "${f.name}=${f.value}"], "idle_in_transaction_session_timeout=60000") &&
      contains([for f in google_sql_database_instance.crawler_db.settings[0].database_flags : "${f.name}=${f.value}"], "log_min_duration_statement=1000")
    )
    error_message = "Cloud SQL 必須設 idle_in_transaction_session_timeout=60000 與 log_min_duration_statement=1000。"
  }
}

# =====================================================================
# 字典 artifact bucket：版本化、不公開；ES 位址只能是私有網段或 https
# =====================================================================
run "dict_bucket_versioned_and_private" {
  command = plan

  assert {
    condition = (
      google_storage_bucket.dict_artifacts.name == "qsearch-test-qsearch-dict" &&
      google_storage_bucket.dict_artifacts.versioning[0].enabled == true &&
      google_storage_bucket.dict_artifacts.public_access_prevention == "enforced" &&
      google_storage_bucket.dict_artifacts.uniform_bucket_level_access == true &&
      google_storage_bucket.dict_artifacts.force_destroy == false
    )
    error_message = "字典 bucket 要版本化、強制不公開、統一權限、不可 force_destroy。"
  }
}

run "elasticsearch_url_rejects_public_http" {
  command = plan

  variables {
    elasticsearch_url = "http://example.com:9200"
  }

  expect_failures = [var.elasticsearch_url]
}
