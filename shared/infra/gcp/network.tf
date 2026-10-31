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

resource "google_compute_router_nat" "crawler" {
  name                               = "crawler-nat"
  router                             = google_compute_router.crawler.name
  region                             = var.region
  nat_ip_allocate_option             = "MANUAL_ONLY"
  nat_ips                            = [google_compute_address.egress.self_link]
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
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
