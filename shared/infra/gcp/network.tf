# VPC、子網、Cloud Router、Cloud NAT（固定出口 IP）、Private Services Access（給 Cloud SQL 私有 IP）。
# 補齊 NOTES 跨片段依賴：google_compute_network.crawler、google_compute_subnetwork.crawler、google_compute_router.crawler。

resource "google_compute_network" "crawler" {
  name                    = "crawler-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
  depends_on              = [google_project_service.required]
}

resource "google_compute_subnetwork" "crawler" {
  name                     = "crawler-subnet"
  region                   = var.region
  network                  = google_compute_network.crawler.id
  ip_cidr_range            = "10.10.0.0/20"
  private_ip_google_access = true
}

# 補上 Cloud NAT 的承載點（片段 compute-router）。
resource "google_compute_router" "crawler" {
  name    = "crawler-router"
  network = google_compute_network.crawler.id
  region  = var.region
  bgp {
    asn = 64514
  }
}

# 固定出口 IP：nat_ip_allocate_option = MANUAL_ONLY 才會使用這個保留 IP（已由 provider 文件確認）。
resource "google_compute_address" "egress" {
  name   = "crawler-egress-ip"
  region = var.region
}

# 第二個固定 IP：一個 IP 只有 64,512 個埠，採集器＋Q6 斷詞／API 的 Direct VPC 實例每台至少配 1,024 埠，一個 IP 最多 63 台；
# 兩個 IP 也讓單一 IP 被目標站封鎖時還有退路（兩個都要給對方白名單）。
resource "google_compute_address" "egress_2" {
  name   = "crawler-egress-ip-2"
  region = var.region
}

resource "google_compute_router_nat" "crawler" {
  name                               = "crawler-nat"
  router                             = google_compute_router.crawler.name
  region                             = var.region
  nat_ip_allocate_option             = "MANUAL_ONLY"
  nat_ips                            = [google_compute_address.egress.self_link, google_compute_address.egress_2.self_link]
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  # 動態配埠：每台最少 1,024、最多 8,192（都要是 2 的冪）；爬蟲對同一站開很多連線時不會因為預設 64 埠而 NAT 掉包（連線逾時卻看不到錯誤）。
  # 動態配埠要求 endpoint-independent mapping 關閉（provider 文件）。
  enable_dynamic_port_allocation      = true
  enable_endpoint_independent_mapping = false
  min_ports_per_vm                    = 1024
  max_ports_per_vm                    = 8192

  # NAT 日誌只記錯誤（配埠用盡＝ERRORS），Cloud Logging 看得到「NAT 掉包」而不是猜；ALL 會把每條連線都記下來，量太大
  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# Cloud SQL 私有 IP 需要 Private Services Access（provider 的 private_network 單獨不夠）。
# 文件確認：Cloud SQL private IP 需設定 private services access。
resource "google_compute_global_address" "private_services" {
  name          = "crawler-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.crawler.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.crawler.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
  depends_on              = [google_project_service.required]
}
