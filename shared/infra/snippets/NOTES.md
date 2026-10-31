# Terraform 對照表：驗證筆記

檔案：`shared/infra/snippets/terraform.json`（26 筆）。本檔為驗證紀錄。

## 版本與來源

- **hashicorp/google**：最新 tag 為 **v8.7.0**（GitHub API 查得，發布於 2026-10-13）。
- 文件來源：`raw.githubusercontent.com/hashicorp/terraform-provider-google/main/website/docs/r/*.html.markdown`，即 **main 分支**，不是 v8.7.0 tag 的檔案。registry.terraform.io 頁面是 JS 渲染，WebFetch 只拿到標題，無法讀取。main 可能比 v8.7.0 新，正式使用前請以 registry 頁面為準。
- **hashicorp/kubernetes**：未指定版本，HPA 段落未驗證（見下）。
- **terraform CLI**：本機未安裝（`terraform` 指令找不到），未執行 `fmt -check` / `validate`。只做了 JSON 重新解析、大括號配對、行數與字數檢查。snippets 是片段，不是可直接 plan 的模組，引用的資源未全部定義（見「跨片段依賴」）。

## 與原本前提不同的地方

1. **Pub/Sub 保留期**：subscription 的 `message_retention_duration` 預設 **7 天**，上限是 **31 天**（2678400s），下限 10 分鐘。原題「上限 7 天」不正確，7 天是預設值。topic 的上限也是 31 天。
2. **Cloud Run v2 service / job**：`deletion_protection` 預設 **true**，destroy 前要先設 false 並 apply。
3. **google_container_cluster 的 location**：工具摘要寫 Optional，我預期是 Required。未確認，snippet 仍有寫 location。
4. **BigQuery table**：`require_partition_filter` 應寫在頂層，`time_partitioning` 區塊內那個已棄用。沒設 field 的時間分區是依 load time（即 ingestion time）。
5. **node pool autoscaling**：min/max 是每個 zone 的數量，整體總量要用 `total_min_node_count` / `total_max_node_count`。
6. **Cloud Run resource 名稱**：用 `google_cloud_run_v2_service`，不是 v1 的 `google_cloud_run_service`。v2 頁面上沒有找到「v1 已棄用」的字句，所以「v1 已棄用」未在文件中確認，只能說 v2 是目前使用的 resource。
7. **Pub/Sub token creator**：已確認仍需要。Pub/Sub 服務代理 `service-PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com` 必須對 push 的 SA 有 Token Creator（或 `iam.serviceAccounts.getOpenIdToken` 權限），不是可選的。

## 已驗證的 IAM 與 DLQ 規則

- Pub/Sub push：push SA 需要 `roles/run.invoker`，且綁在 crawler-worker 服務上。
- Pub/Sub DLQ：死信 topic 授予 `roles/pubsub.publisher` 給 Pub/Sub 服務代理；訂閱授予 `roles/pubsub.subscriber`。要在建立 DLQ topic 後才授權。`max_delivery_attempts` 預設 5、範圍 5–100。
- Cloud Scheduler OIDC：job 以指定的 SA 執行（不是 Scheduler 服務代理），該 SA 需要 `roles/run.invoker`。Cloud Scheduler 服務代理需要 `roles/cloudscheduler.serviceAgent`，通常在啟用 API 時自動授予。

## 參數選擇與改寫

- Cloud Run worker 的 Direct VPC egress：只寫 `network_interfaces { subnetwork = ... }`，未驗證單獨 subnetwork 是否被接受（見未驗證）。
- Cloud Build：用 legacy 的 `github` 區塊。provider 同時有 `repository_event_config`（較新的 Repo API 觸發器），哪個是新專案的建議我未驗證。
- Cloud SQL：`edition = "ENTERPRISE"` 搭配 `db-custom-2-7680`，已依文件確認 shared-core 與 custom tier 只能用在 ENTERPRISE。
- Compute template：`disk` 用單行寫法 `disk { source_image = ... }`，依 HCL 語法應有效，未在 CLI 驗證。

## 未驗證（UNVERIFIED）

- **kubernetes_horizontal_pod_autoscaler_v2**：provider 文件兩個路徑都 404，搜尋也沒找到 v2 頁面。語法依 Kubernetes `autoscaling/v2` API 結構撰寫（metadata / spec / scale_target_ref / metric.resource.target）。**此 snippet 為 24 行，超過 14 行上限**，因為巢狀 block 無法壓縮到 14 行。使用前請到 registry 的 `horizontal_pod_autoscaler_v2` 頁面確認。
- **BigQuery subscription 的 IAM**：文件頁被轉址且沒有 IAM 段落。通常是 Pub/Sub 服務代理在目標表上需要 `roles/bigquery.dataEditor`，寫 metadata 時還需要 metadata 相關權限，未確認。
- **DLQ topic 需要 subscription 才會保留死信訊息**：來自經驗，未從文件驗證。
- **google_service_account_iam_member**：參數名（`service_account_id`, `role`, `member`）未抓取文件確認。
- **Autopilot 不能自己加 node pool**：來自經驗。文件只說 Autopilot 不支援部分 Standard 功能，未明說 node pool 的限制。
- **Cloud Run pull 消費者被 CPU 節流**：gotcha 只描述行為，未寫出 CPU 常開的參數名（`cpu_idle` 未驗證）。
- **Pub/Sub schema 的 `.id` 格式**：schema 字串格式 `projects/{project}/schemas/{schema}` 有文件；用 `google_pubsub_schema.<name>.id` 當引用是依格式推測。
- **Cloud Scheduler OIDC audience 預設值**：文件未說明是否預設為 uri，snippet 明確寫了 audience。
- **未使用的參數**：`google_compute_address` 的 `address_type`、`network_tier`，`google_compute_router` 的 bgp 必要性等，未驗證，也未用於 snippet。

## 跨片段依賴（snippets 中引用但未定義）

- google_service_account：`push`、`scheduler`、`api`、`parser`、`legacy`
- google_compute_network.crawler、google_compute_subnetwork.crawler、google_compute_health_check.legacy
- google_compute_router.crawler（定義在 `compute-router` 片段）
- google_pubsub_topic.crawler_tasks_dlq（DLQ topic，未單獨列出）
- google_cloud_run_v2_service.scheduler（未單獨列出）
- google_container_cluster.crawler_standard（Standard 叢集，未單獨列出）
- 變數：`var.project_id`、`var.project_number`

## 數量與格式偏離

- 共 **26 筆**，超過要求的約 18–24 筆。原因：NAT 拆成 `compute-static-ip-nat` 與 `compute-router` 兩筆，才能各自守住 14 行上限。
- `say` / `gotcha` 以中文字數計，全部 ≤ 40 字（ASCII 名稱如 `roles/run.invoker` 不計入）。
- HCL 行數：除 `k8s-hpa-v2-browser` 外，全部 ≤ 14 行。
