terraform {
  required_version = ">= 1.5.0"

  required_providers {
    # 已確認：provider 最新 tag 為 v8.7.0（見 README 驗證紀錄）。~> 8.7 = >= 8.7, < 9.0
    google = {
      source  = "hashicorp/google"
      version = "~> 8.7"
    }
    # google_project_service_identity 用於建立 Pub/Sub 服務代理（同 lab 做法）
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 8.7"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}
