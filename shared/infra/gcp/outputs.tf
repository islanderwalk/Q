output "cloud_run_urls" {
  description = "Cloud Run 服務 URL。"
  value = {
    api       = google_cloud_run_v2_service.api.uri
    worker    = google_cloud_run_v2_service.crawler_worker.uri
    scheduler = google_cloud_run_v2_service.scheduler.uri
    parser    = google_cloud_run_v2_service.parser.uri
  }
}

output "pubsub_topics" {
  value = {
    crawler_tasks     = google_pubsub_topic.crawler_tasks.id
    crawler_tasks_dlq = google_pubsub_topic.crawler_tasks_dlq.id
    qsearch_fetched   = google_pubsub_topic.qsearch_fetched.id
    parser_dlq        = google_pubsub_topic.parser_dlq.id
  }
}

output "pubsub_subscriptions" {
  value = {
    crawl_sub      = google_pubsub_subscription.crawl_sub.id
    audit_sub      = google_pubsub_subscription.audit_sub.id
    qsearch_parser = google_pubsub_subscription.qsearch_parser_sub.id
    qsearch_to_bq  = google_pubsub_subscription.qsearch_to_bq.id
  }
}

output "cloud_tasks_queues" {
  value = [for q in google_cloud_tasks_queue.crawl : q.name]
}

output "raw_bucket_url" {
  value = google_storage_bucket.raw_scraped.url
}

output "bigquery_table" {
  value = "${var.project_id}.${google_bigquery_dataset.sentiment.dataset_id}.${google_bigquery_table.ingest_events.table_id}"
}

output "artifact_registry_repo" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.crawler.repository_id}"
}

output "egress_static_ip" {
  description = "Cloud NAT 固定出口 IP（目標站白名單用）。"
  value       = google_compute_address.egress.address
}

output "egress_static_ips" {
  description = "Cloud NAT 全部固定出口 IP（兩個都要給目標站白名單）。"
  value       = [google_compute_address.egress.address, google_compute_address.egress_2.address]
}

output "q6" {
  description = "Q6 文字雲：Cloud Run 服務 URL、Pub/Sub 主題／訂閱、字典 bucket。"
  value = {
    seg_urls          = { for k, s in google_cloud_run_v2_service.seg : k => s.uri }
    wordcloud_api_url = google_cloud_run_v2_service.wordcloud_api.uri
    collector_url     = google_cloud_run_v2_service.collector.uri
    topics            = { parsed_posts = google_pubsub_topic.parsed_posts.id, parsed_posts_dlq = google_pubsub_topic.parsed_posts_dlq.id, dict_releases = google_pubsub_topic.dict_releases.id }
    subscriptions     = merge({ for k, s in google_pubsub_subscription.parsed_posts_seg : "parsed_posts_seg_${k}" => s.id }, { dict_releases_seg_v2 = google_pubsub_subscription.dict_releases_seg_v2.id, parsed_posts_dlq_inspect = google_pubsub_subscription.parsed_posts_dlq_inspect.id })
    dict_bucket       = google_storage_bucket.dict_artifacts.url
  }
}

output "cloud_sql_private_ip" {
  value = google_sql_database_instance.crawler_db.private_ip_address
}

output "legacy_mig_name" {
  value = one(google_compute_region_instance_group_manager.crawler_legacy[*].name)
}
