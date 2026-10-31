# Kubernetes 物件（HPA）：由 kubernetes provider 管理，不是 google provider。
# HPA 依 CPU 使用率擴縮，前提是 Pod 有設定 CPU request。
# 注意：snippet 的 HPA 目標 Deployment "browser" 不在 26 片段內，需先由另一份 manifest 或 Terraform 建立，否則 apply 會失敗。

data "google_client_config" "default" {}

provider "kubernetes" {
  host                   = "https://${google_container_cluster.crawler_standard.endpoint}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(google_container_cluster.crawler_standard.master_auth[0].cluster_ca_certificate)
}

resource "kubernetes_namespace_v1" "crawler" {
  metadata {
    name = "crawler"
  }
}

# 已由 kubernetes provider 文件確認 schema：metadata / spec / scale_target_ref / metric.resource.target。
resource "kubernetes_horizontal_pod_autoscaler_v2" "browser" {
  metadata {
    name      = "browser-hpa"
    namespace = kubernetes_namespace_v1.crawler.metadata[0].name
  }

  spec {
    min_replicas = 1
    max_replicas = 20

    scale_target_ref {
      kind = "Deployment"
      name = "browser"
    }

    metric {
      type = "Resource"
      resource {
        name = "cpu"
        target {
          type                = "Utilization"
          average_utilization = 70
        }
      }
    }
  }
}
