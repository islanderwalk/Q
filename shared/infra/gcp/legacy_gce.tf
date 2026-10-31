# legacy 爬蟲（GCE 區域 MIG）。預設關閉：enable_legacy_gce = true 才會建立。
# 文件確認：instance template 不能原地修改，要設 create_before_destroy 並用 name_prefix。
# 文件確認：MIG 的 target_size 與 autoscaler 會互相打架，這裡沒有 autoscaler，所以固定 target_size。

resource "google_service_account" "legacy" {
  count        = var.enable_legacy_gce ? 1 : 0
  account_id   = "crawler-legacy"
  display_name = "legacy GCE crawler"
  depends_on   = [google_project_service.required]
}

resource "google_compute_health_check" "legacy" {
  count               = var.enable_legacy_gce ? 1 : 0
  name                = "crawler-legacy-hc"
  check_interval_sec  = 10
  timeout_sec         = 5
  healthy_threshold   = 2
  unhealthy_threshold = 3

  http_health_check {
    port         = 8080
    request_path = "/health"
  }
  depends_on = [google_project_service.required]
}

resource "google_compute_instance_template" "crawler_legacy" {
  count        = var.enable_legacy_gce ? 1 : 0
  name_prefix  = "crawler-legacy-"
  machine_type = "e2-medium"
  region       = var.region
  tags         = ["crawler-legacy"]

  metadata = {
    block-project-ssh-keys = "true"
    enable-oslogin         = "TRUE"
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  disk {
    source_image = "debian-cloud/debian-12"
    auto_delete  = true
    boot         = true
  }

  network_interface {
    subnetwork = google_compute_subnetwork.crawler.id
  }

  service_account {
    email  = google_service_account.legacy[0].email
    scopes = ["cloud-platform"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_region_instance_group_manager" "crawler_legacy" {
  count              = var.enable_legacy_gce ? 1 : 0
  name               = "crawler-legacy-mig"
  base_instance_name = "crawler-legacy"
  region             = var.region
  target_size        = 2

  version {
    instance_template = google_compute_instance_template.crawler_legacy[0].self_link
  }

  auto_healing_policies {
    health_check      = google_compute_health_check.legacy[0].id
    initial_delay_sec = 300
  }
}

# MIG autohealing 的健康檢查探針來自 35.191.0.0/16 與 130.211.0.0/22；自訂 VPC 預設拒絕所有 ingress，需明確放行。
resource "google_compute_firewall" "legacy_health_check" {
  count         = var.enable_legacy_gce ? 1 : 0
  name          = "crawler-allow-gcp-health-check"
  network       = google_compute_network.crawler.id
  direction     = "INGRESS"
  source_ranges = ["35.191.0.0/16", "130.211.0.0/22"]
  target_tags   = ["crawler-legacy"]

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }
}
