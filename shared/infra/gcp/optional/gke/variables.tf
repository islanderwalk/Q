variable "project_id" {
  type        = string
  description = "GCP project ID。"
}

variable "region" {
  type    = string
  default = "asia-east1"
}

variable "network_name" {
  type        = string
  description = "主模組建立的 VPC 名稱（infra/gcp/network.tf 的 crawler-vpc）。"
  default     = "crawler-vpc"
}

variable "subnetwork_name" {
  type    = string
  default = "crawler-subnet"
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "master_authorized_cidr" {
  type        = string
  description = "允許連到 GKE 控制平面（kubectl / Terraform kubernetes provider）的管理端 CIDR，例如辦公室固定 IP/32。"
}
