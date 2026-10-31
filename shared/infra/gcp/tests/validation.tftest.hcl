# infra/gcp 變數驗證測試（mock provider，command = plan，不需要 GCP 憑證）
#
# project_number 用來組 Pub/Sub 服務代理 member
# （service-<NUMBER>@gcp-sa-pubsub.iam.gserviceaccount.com），必須是純數字。
# 填錯（例如把 project_id 填進來）時，plan 必須在 variables.tf 的 validation 就失敗。

mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id     = "qsearch-test"
  project_number = "123456789012"

  # 不用預設的 hello 示範映像，否則 checks.tf 的 no_placeholder_images 會讓每個 run 失敗。
  image_api       = "asia-east1-docker.pkg.dev/qsearch-test/crawler/api:test"
  image_worker    = "asia-east1-docker.pkg.dev/qsearch-test/crawler/worker:test"
  image_parser    = "asia-east1-docker.pkg.dev/qsearch-test/crawler/parser:test"
  image_scheduler = "asia-east1-docker.pkg.dev/qsearch-test/crawler/scheduler:test"
  image_backfill  = "asia-east1-docker.pkg.dev/qsearch-test/crawler/backfill:test"
  image_ptt_crawl = "asia-east1-docker.pkg.dev/qsearch-test/crawler/ptt-crawl:test"
}

run "project_number_rejects_letters" {
  command = plan

  variables {
    project_number = "abc"
  }

  expect_failures = [
    var.project_number,
  ]
}

run "project_number_rejects_project_id" {
  command = plan

  variables {
    project_number = "qsearch-test"
  }

  expect_failures = [
    var.project_number,
  ]
}

run "project_number_rejects_empty" {
  command = plan

  variables {
    project_number = ""
  }

  expect_failures = [
    var.project_number,
  ]
}

run "project_number_accepts_digits" {
  command = plan

  variables {
    project_number = "425708154183"
  }

  assert {
    condition     = google_pubsub_topic_iam_member.parser_dlq_publisher.member == "serviceAccount:service-425708154183@gcp-sa-pubsub.iam.gserviceaccount.com"
    error_message = "純數字 project_number 必須通過驗證，並組出正確的 Pub/Sub 服務代理 member。"
  }
}
