# infra/onprem：地端對照（同一條 6 段管線）

這份文件讓 UIUX 能做 GCP ↔ 地端的切換。6 段管線與 `shared/infra/gcp` 一致：**排 → 派 → 抓 → 存 → 解 → 用**。每一列都寫出地端選項、取捨、以及對應的 Q 題號。

**已實際啟動驗證**：`docker-compose.yml`（9 個服務，映像固定 tag@digest）在雲端容器用 docker compose 跑完整條鏈，smoke test 19 項連續三次全過；重複投遞、回放、從 MinIO 原始檔重建衍生資料、兩條 DLQ 都有實測。數字與限制見 [`VERIFY.md`](VERIFY.md)。「抓」只讀 repo 內的 fixture，沒有連真實網站；高可用、規模、真實抓取仍未驗證。

## 對照表

| 段 | GCP（infra/gcp） | 地端候選 | 取捨（重點） | 題號 |
|---|---|---|---|---|
| **排** | Cloud Scheduler crawl-tick（每 15 分鐘）＋ scheduler Cloud Run | cron、Airflow、或 APScheduler 跑在一個 Python 服務；K8s CronJob | 地端排程機是單點故障，要自己做 HA（兩台 + 分散式鎖）。Cloud Scheduler 本身有 SLA，地端沒有。排程必須冪等，因為重試會重複觸發。 | Q3（6 小時新鮮度、7 天留言更新） |
| **派** | Cloud Tasks（每來源一條佇列，`max_dispatches_per_second`、`retry_config`）＋ Pub/Sub crawler-tasks（push＋DLQ） | **RabbitMQ**（每來源一個 queue、DLX 當 DLQ、consumer prefetch 控速）；Redis Streams（簡單但持久性較弱）；Kafka（一人維運太重） | RabbitMQ 的 per-queue 速率要自己用 consumer 數量＋令牌桶實作，Cloud Tasks 是內建的。DLX 對應 Pub/Sub DLQ，但沒有 Pub/Sub 的 `Seek` 回放（見「解」）。 | Q3, Q4（避免被目標站封鎖要靠速率控制） |
| **抓** | Cloud Run crawler-worker（push 觸發、Direct VPC egress＋Cloud NAT 固定 IP）；GKE browser-pool（方案 B） | Docker／K8s 上的 worker Pod；無頭瀏覽器用獨立 Deployment（HPA 或手動擴縮） | 出口 IP 是最大的差異：GCP 用 NAT 固定 IP，地端通常用家用／機房 IP，要處理 IP 信譽與代理池。雲端 IP 段會被目標站封鎖（Q4 題幹），地端的 IP 段不同，但也可能被封。 | Q4（代理成本、封鎖策略） |
| **存** | GCS raw-scraped（統一權限、30 天轉 COLDLINE） | **MinIO**（S3 相容）；或 Ceph／NAS 加上 S3 閘道 | 地端要自己管磁碟、備份、容量規劃。MinIO 單機沒有 erasure coding 的保障，要多節點才有。生命週期規則（轉冷／刪除）MinIO 支援，但要確認與 GCS 語意一致。 | Q3（原始檔）、Q6（回朔時的原始資料來源） |
| **解** | Cloud Run parser（Pub/Sub push 到 `/events/crawl-fetched`）；Pub/Sub qsearch-parser-sub（保留已 ACK 訊息 7 天，可 `Seek` 重播）；Cloud SQL（狀態、去重、pg_bigm） | FastAPI parser 當 RabbitMQ consumer；**PostgreSQL＋pg_bigm**（狀態與中文全文）；重播改用「原始檔在 MinIO ＋ job 表」的方式 | **最大的功能損失是重播。** Pub/Sub 的 Seek 可以把訂閱倒回任意時間點重跑；RabbitMQ 的已 ACK 訊息不會保留。地端要自己用 job 表＋原始檔實作回放，或改用 Kafka／Redis Streams 的 log 型態。冪等（`event_id` unique 或 inbox 表）必須在 DB 層保證。 | Q5（回補）、Q6（回朔斷詞） |
| **用** | BigQuery sentiment_analytics.ingest_events（分區表，Pub/Sub BQ 訂閱直寫）；Elasticsearch（架構 int.txt 有，infra 沒寫）；Firestore session 池（控制面） | **ClickHouse**（大量聚合、分析）；**OpenSearch**（搜尋，對應 ES）；**Redis**（session 池，要開 AOF 持久化）；PostgreSQL＋pg_bigm（中文搜尋的小規模替代） | ClickHouse 寫入是批次導向，不適合逐筆寫入；Pub/Sub BQ 訂閱是逐筆 streaming。OpenSearch 授權與 ES 不同（Apache 2.0 vs ES 的 SSPL/Elastic License），功能上大致可替代，但版本相容性要測。搜尋的中文斷詞要重新設計（Q6）。 | Q2（規模與成本）、Q5、Q6 |

### 橫向元件

| 項目 | GCP | 地端 | 注意 |
|---|---|---|---|
| 密鑰 | Secret Manager | Vault、sops＋age，或 `.env`（僅開發） | 地端最常見的錯誤是把密碼放進 compose 檔；示意檔用 `:?` 強制從 `.env` 讀。 |
| 出口 | Cloud NAT（MANUAL_ONLY 固定 IP） | 出口閘道＋固定 IP，或 proxy 池 | 固定 IP 只有在你控制出口時才有效。 |
| CI／發版 | Cloud Build trigger（push main） | GitLab CI、Jenkins、Drone | 地端 CI 的映像倉庫（Harbor）也要自己維運。 |
| 監控 | Cloud Monitoring（lab 列的指標：未確認訊息數、最舊訊息年齡、DLQ 數） | Prometheus＋Grafana、Loki | 指標名稱要自己對應；原本 Pub/Sub 的 `oldest_unacked_message_age` 在 RabbitMQ 要用 management API 或 exporter 取得。 |
| 網路 | VPC、子網、Private Services Access | 內網 VLAN、防火牆規則 | Cloud SQL 的私有 IP 在地端就是 PostgreSQL 監聽內網。 |

## 成本與維運（Q2 的限制）

- 題目限制是「一個人維運、每月基礎設施預算 3,000 元」。地端省掉的是雲端用量費，但多出的是主機、電費、磁碟、備份與人力。
- 以單人維運來說，最少要包含：PostgreSQL 備份與還原演練、MinIO／磁碟容量告警、RabbitMQ 與 Redis 的重啟行為、憑證輪替。這些在 GCP 側多數由服務代管。

## 什麼時候切到地端（給 UIUX 的判斷）

- 出口 IP 的要求（固定 IP、特定地區）若用地端的閘道比 NAT 便宜，地端抓取層有意義。
- 資料量大到雲端的 BigQuery／GCS 費用超過主機成本，才值得搬分析層。
- 重播（Seek）與 per-queue 速率控制是 GCP 版的優點；地端切換時，這兩個功能要先做出替代方案，否則 Q5、Q6 的回補策略會變弱。

## 檔案

- `docker-compose.yml`：可實際啟動的地端 stack（rabbitmq、minio、postgres＋pg_bigm、clickhouse、opensearch、redis，加上 app 的 scheduler／worker／parser 與一次性的 bootstrap、smoke）。
- `app/`：Python 實作（`qsearch_onprem/` 的 scheduler、worker、parser、replay）與 `tests/test_smoke.py`。
- `postgres-bigm/`：PostgreSQL 15 ＋ pg_bigm 1.2 的建置檔；原始碼 tarball 由 `fetch_pg_bigm.py` 下載並核對 sha256，不進版控。
- `scripts/run_e2e.sh`：一鍵驗證腳本，`VERIFY.md` 的數字都來自它的輸出。
- `.env.example`：示範帳密（只能本機用）。
- `VERIFY.md`：實跑紀錄、沒有證明的事、與 GCP 版的功能差異。
- `docker-compose.example.yml`：早期的 10 服務示意檔，保留作對照，**未啟動**；以 `docker-compose.yml` 為準。
- 對照表的完整版（含 GCP 資源位址）：`shared/architecture/gcp-vs-onprem.md`。
