# 方案 C：GKE Autopilot（抓取 / 解析 Deployment，Google 管節點）。
# 方案 B：Standard 叢集 + 獨立 browser-pool 節點池（無頭瀏覽器，可縮到 0）。
# 注意：snippet 沒有 Standard 叢集本體，下面的 crawler_standard 是為了讓節點池有父層而補上的。

data "google_compute_network" "crawler" {
  name = var.network_name
}

data "google_compute_subnetwork" "crawler" {
  name   = var.subnetwork_name
  region = var.region
}

locals {
  gke_labels = { app = "qsearch", component = "crawler" }
}

# 節點專用 SA（不用 Compute Engine 預設 SA，避免節點／Pod 拿到過大權限）。
resource "google_service_account" "gke_nodes" {
  account_id   = "crawler-gke-nodes"
  display_name = "GKE node runtime (least privilege)"
}

resource "google_project_iam_member" "gke_nodes" {
  for_each = toset([
    "roles/container.defaultNodeServiceAccount",
    "roles/artifactregistry.reader",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.gke_nodes.email}"
}

resource "google_container_cluster" "crawler_autopilot" {
  name                = "crawler-autopilot"
  location            = var.region
  enable_autopilot    = true
  deletion_protection = var.deletion_protection
  network             = data.google_compute_network.crawler.id
  subnetwork          = data.google_compute_subnetwork.crawler.id

  # 未驗證：Autopilot 必須是 VPC-native；這裡給空的 ip_allocation_policy 讓 GKE 自動分配次要範圍（請 plan/apply 時再確認）。
  ip_allocation_policy {}

  resource_labels = local.gke_labels

  # 節點只有私有 IP，出站走 crawler-nat 的固定 IP（Q4 白名單前提）。
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
  }

  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = var.master_authorized_cidr
      display_name = "admin"
    }
  }

  release_channel {
    channel = "REGULAR"
  }

  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.gke_nodes.email
      oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    }
  }
}

# Standard 叢集（方案 B 用）。remove_default_node_pool 讓節點池完全由 google_container_node_pool 管理。
resource "google_container_cluster" "crawler_standard" {
  name                     = "crawler-standard"
  location                 = var.region
  remove_default_node_pool = true
  initial_node_count       = 1
  deletion_protection      = var.deletion_protection
  network                  = data.google_compute_network.crawler.id
  subnetwork               = data.google_compute_subnetwork.crawler.id

  # 未驗證：同上，依 GKE 自動分配次要範圍。
  ip_allocation_policy {}

  resource_labels   = local.gke_labels
  datapath_provider = "ADVANCED_DATAPATH" # Dataplane V2，內建 NetworkPolicy 執行

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
  }

  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = var.master_authorized_cidr
      display_name = "admin"
    }
  }

  release_channel {
    channel = "REGULAR"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }
}

# browser-pool：無頭瀏覽器。autoscaling 的 min/max 是「每個 zone」的數量；要整體上限請改用 total_min/max_node_count（provider 文件確認，兩者擇一）。
resource "google_container_node_pool" "browser_pool" {
  name     = "browser-pool"
  cluster  = google_container_cluster.crawler_standard.name
  location = var.region

  autoscaling {
    min_node_count = 0
    max_node_count = 10
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = "e2-standard-4"
    service_account = google_service_account.gke_nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    resource_labels = local.gke_labels

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
  }
}
