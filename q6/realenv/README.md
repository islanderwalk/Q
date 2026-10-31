# Q6 真實環境：從字典、回補、串流到 PM 文字雲，每一層都跑真的程式

這個資料夾把 Q6 答案裡的設計整套跑起來：官方 Elasticsearch 9.5.5（smartcn、ICU、IK 外掛）、PostgreSQL 16、Pub/Sub 官方模擬器、Kibana、OpenTelemetry、Prometheus、Grafana，加上本 repo 的斷詞映像、文字雲 API 與 PM A/B 頁面。全部在 Docker 裡，**不開任何 GCP 計費資源**。

- 實測結果與數字：[`RESULTS.md`](RESULTS.md)
- 架構圖：[`../architecture/realenv/diagram-realenv-stack.png`](../architecture/realenv/diagram-realenv-stack.png)（每一層用什麼、跑出什麼數字）
- 沙盤推演圖：[`../architecture/realenv/diagram-sandbox-scenarios.png`](../architecture/realenv/diagram-sandbox-scenarios.png)（10 種情境：怎麼觸發、系統怎麼反應、實測）
- 截圖：[`../evidence/realenv/`](../evidence/realenv)（PM 前端、Kibana、Grafana、Prometheus、五個渠道的原始公開頁；PTT 的在 [`../evidence/playwright/`](../evidence/playwright)）

## GCP 元件與本機替身

| 答案裡的 GCP 元件 | 這裡用什麼 | 一樣的地方 | 不一樣的地方 |
|---|---|---|---|
| Cloud Storage（字典 artifact） | `dictsrv`：nginx 1.27 服務 `runtime/dictsrv/versions/<版本>/` 與 `current` | 依版本取檔、逐檔 sha256、`current` 原子切換、`Last-Modified`／`ETag`（IK 靠這兩個判斷重載） | 沒有 IAM、物件版本控制 |
| BigQuery／GCS 匯出（回補讀原文） | `runtime/raw/<渠道>/<yyyy-mm>.jsonl` | 回補從原文讀，不從 ES scroll | 只有 PTT 一個渠道、42.5 萬篇 |
| Pub/Sub | 官方 `google-cloud-cli:emulators` 的 Pub/Sub 模擬器 | 同一套 client 程式碼；訂閱、ack／nack、dead letter 都會動 | 沒有配額、沒有跨區 |
| Cloud Run 服務（斷詞服務、文字雲 API） | 同一個容器映像 `docker compose` 起 | 程式碼、映像、環境變數完全相同 | 沒有自動擴縮 |
| Cloud Run Jobs（回補） | 同一個映像 `docker compose run`，`CLOUD_RUN_TASK_INDEX` 照 Cloud Run 的變數名 | 多 task 從帳本領分區、checkpoint、被砍後接續 | task 數要自己起 |
| Compute Engine 上的 Elasticsearch | 官方映像 `elasticsearch:9.5.5` 單節點＋3 個外掛 | 索引 mapping、Painless 腳本、IK 遠端字典、同義詞 API 都是真的 | 單節點、0 副本、4 GB heap，跟斷詞程式搶同一台 4 核心 |
| Ops Agent（ES 與 PostgreSQL 指標） | OpenTelemetry Collector contrib 0.135.0 的 `elasticsearch`、`postgresql` receiver | Ops Agent 的第三方整合就是包這兩個 receiver | 送到 Prometheus，不是 Cloud Monitoring |
| Cloud Monitoring／告警 | Prometheus v3.5.0＋告警規則、Grafana 12.1.1 | 同樣的指標（超過 180 秒的查詢、串流延遲）；告警規則只有一條，就是超過 180 秒的查詢（`WordcloudOver180s`），串流延遲只收指標、沒有門檻 | 沒有通知管道 |
| Cloud Logging | 各服務印到 stdout 的 JSON（`severity`、`event`、`duration_ms`、`seg_ver`…） | 欄位照 Cloud Logging 結構化日誌的慣例，日誌型指標可直接用 | 沒有集中收集 |
| Cloud SQL（帳本） | 官方 `postgres:16` | 回補帳本、checkpoint、限速、發版紀錄、影子比對都在這裡 | 沒有 HA |

共用的 GCP 模組 `shared/infra/gcp` 在 PR #7 做過 `terraform plan`（102 個資源）與 `terraform test`（30 項通過），涵蓋回補 Job（`qsearch-backfill`）、GCS、Pub/Sub 與 Cloud SQL。上表 Compute Engine 上的 Elasticsearch 與 Ops Agent、斷詞服務、文字雲 API、Cloud Monitoring 告警都還沒有 Terraform。這裡沒有 apply。

## 資料夾

| 路徑 | 內容 |
|---|---|
| `docker-compose.yml` | 全部服務；`seg-job` 在 `job` profile 裡，用 `docker compose run` 起 |
| `run.sh` | 一步一步重跑：`build`、`up`、`seed`、`backfill`、`stream`、`exp`、`ik`、`probe`、`shots` |
| `es/` | ES 映像（外掛下載與驗證 `fetch_plugins.sh`、IK 遠端字典設定）、索引定義 `index/posts.json` |
| `dict/` | 字典發版檔 `releases/qsearch-q6-2026.10.30.{1..6}.json` 與 `build_artifact.py` |
| `seg/qseg/` | 斷詞映像：`service`（串流）、`job`（回補）、`rerun`（定向重跑）、`impact`（影響範圍地面真值）、`shadow`（影子比對）、`approve`／`baseline`、`seed` |
| `api/`、`web/` | 文字雲 API（FastAPI）與 PM A/B 頁面（無外部套件） |
| `pg/init.sql` | 帳本、控制表、批次紀錄、發版紀錄、影子比對、`posts_terms`（PG 對照用） |
| `monitoring/` | OTel Collector、Prometheus（含告警規則）、Grafana 資料來源與儀表板（`build_dashboard.py` 產生） |
| `collect/` | 六渠道樣本的抓取程式；`data/channels/` 是抓到的資料與紀錄 |
| `scripts/` | 準備原文、建索引與訂閱、送串流、各項實驗、延遲探針、截圖 |
| `results/` | 每次實測的日誌與 JSON（`backfill/`、`es/`、`stream/`、延遲探針） |
| `runtime/` | 執行時產生的字典 artifact 與原文（不進 git） |

## 怎麼重跑

需要 Docker、Python 3（`requests`、`jieba`、`psycopg[binary]`、`google-cloud-pubsub`）、Node＋Playwright。兩個原始檔不進 git，放在 `DL` 目錄：

| 檔案 | 來源 | sha256 前 16 碼 |
|---|---|---|
| `dict.txt.big` | jieba 官方 repo 的繁體加強主字典 | `b16011275c42955c` |
| `Gossiping-QA-Dataset.txt` | Gossiping-Chinese-Corpus（PTT 八卦板問答 41.8 萬組） | `cf5ef0a931a8a144` |

```bash
cd q6/realenv
DL=/path/to/downloads ./run.sh build   # 公司代理攔 TLS 時加 CA=/path/to/ca.crt
./run.sh up
DL=/path/to/downloads ./run.sh seed
./run.sh backfill        # 預設 r3；V=qsearch-q6-2026.10.30.4 可指定別版
./run.sh stream
./run.sh exp
./run.sh ik            # 會把字典 current 切到 r5
./run.sh probe         # 另開終端機，回補時在旁邊量查詢延遲
./run.sh shots
```

打開：PM 頁 http://localhost:8080、Kibana http://localhost:5601、Grafana http://localhost:3000、Prometheus http://localhost:9090。

這次在雲端容器裡跑：4 核心、15 GB RAM；ES heap 4 GB。容器的磁碟是按 session 配額，ES 依「總容量百分比」算的磁碟水位會誤判已滿，所以 compose 裡改用絕對剩餘空間（4 GB／2 GB／1 GB）。
