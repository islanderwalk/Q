# 可選：方案 B/C 的 GKE（Autopilot + Standard browser pool + HPA）。
# 獨立 root module，不併入主 plan：kubernetes provider 需要叢集連線資訊，不能在主模組裡預設啟動。
# 使用前提：主模組 infra/gcp 已 apply（建立 crawler-vpc / crawler-subnet）。

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.7"
    }
    # 未驗證版本：kubernetes provider 版本未在 NOTES 指定；此處寬鬆限制 2.x–3.x。
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.0.0, < 4.0.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
