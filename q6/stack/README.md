# Q6 整套系統實跑：從字典、回補、串流到 PM 文字雲，每一層都在 Docker 跑起來

這個資料夾把 Q6 答案裡的設計整套跑起來：官方 Elasticsearch 9.5.5（smartcn、ICU、IK 外掛）、PostgreSQL 16、Pub/Sub 官方模擬器、Kibana、OpenTelemetry、Prometheus、Grafana，加上本 repo 的斷詞映像、文字雲 API 與 PM A/B 頁面。全部在 Docker 裡，**不開任何 GCP 計費資源**。

- 實測結果與數字：[`RESULTS.md`](RESULTS.md)
- 監控：[`MONITORING.md`](MONITORING.md)（計畫書十項 P0 對照：指標、告警、看板、怎麼重算）
- 架構圖：[`../architecture/stack/diagram-stack.png`](../architecture/stack/diagram-stack.png)（每一層用什麼、跑出什麼數字）
- 沙盤推演圖：[`../architecture/stack/diagram-sandbox-scenarios.png`](../architecture/stack/diagram-sandbox-scenarios.png)（10 種情境：怎麼觸發、系統怎麼反應、實測）
- 截圖：[`../evidence/stack/`](../evidence/stack)（PM 前端、Kibana、Grafana、Prometheus、五個渠道的原始公開頁；PTT 的在 [`../evidence/playwright/`](../evidence/playwright)）

## GCP 元件與本機對應

| 答案裡的 GCP 元件 | 這裡用什麼 | 一樣的地方 | 不一樣的地方 |
|---|---|---|---|
| Cloud Storage（字典 artifact） | `dictsrv`：nginx 1.27 服務 `runtime/dictsrv/versions/<版本>/` 與 `current` | 依版本取檔、逐檔 sha256、`current` 原子切換、`Last-Modified`／`ETag`（IK 靠這兩個判斷重載） | 沒有 IAM、物件版本控制 |
| BigQuery／GCS 匯出（回補讀原文） | `runtime/raw/<渠道>/<yyyy-mm>.jsonl`（`scripts/prepare_raw.py`，只收有原始發文時間的文章） | 回補從原文讀，不從 ES scroll；分區 = 渠道 × 發文月份 | 只有 PTT 一個渠道 |
| Pub/Sub | 官方 `google-cloud-cli:emulators` 的 Pub/Sub 模擬器 | 同一套 client 程式碼；訂閱、ack／nack、dead letter 都會動 | 沒有配額、沒有跨區 |
| Cloud Run 服務（斷詞服務、文字雲 API、採集器） | 同一個容器映像 `docker compose` 起 | 程式碼、映像、環境變數完全相同；斷詞服務 v2 多訂閱 `dict-releases`，收到發版訊息就熱換字典、定向重跑 | 沒有自動擴縮 |
| Cloud Run Jobs（回補） | 同一個映像 `docker compose run`，`CLOUD_RUN_TASK_INDEX` 照 Cloud Run 的變數名 | 多 task 從帳本領分區、checkpoint、被砍後接續 | task 數要自己起 |
| Compute Engine 上的 Elasticsearch | 官方映像 `elasticsearch:9.5.5` 單節點＋3 個外掛 | 索引 mapping、Painless 腳本、IK 遠端字典、同義詞 API 都在官方映像上跑 | 單節點、0 副本、4 GB heap，跟斷詞程式搶同一台 4 核心 |
| Ops Agent（ES 與 PostgreSQL 指標） | OpenTelemetry Collector contrib 0.135.0 的 `elasticsearch`、`postgresql` receiver | Ops Agent 的第三方整合就是包這兩個 receiver | 送到 Prometheus，不是 Cloud Monitoring |
| Cloud Monitoring／告警 | Prometheus v3.5.0＋`monitoring/rules/*.yml`（66 條告警：3 分鐘 SLO 燃燒率（Sloth）、完整性四層對帳、採集軟封鎖與首抓延遲、積壓、發版收斂、回補燃盡、品質日報、ES 健康、PM 頁 RUM；門檻都是提案值）、Alertmanager v0.28.1 → `POST /api/ops/alert` → PM 頁橫幅、Grafana 12.1.1 兩張看板、Loki 3.5.3＋Alloy、elasticsearch_exporter | 同樣的指標與告警政策；SLO 用 Sloth 產生多視窗燃燒率 | 通知管道只有 webhook 到 PM 頁（Email／Telegram 要另加 receiver）；見 [`MONITORING.md`](MONITORING.md) |
| Cloud Logging | 各服務印到 stdout 的 JSON（`severity`、`event`、`duration_ms`、`seg_ver`…） | 欄位照 Cloud Logging 結構化日誌的慣例，日誌型指標可直接用 | 沒有集中收集 |
| Cloud SQL（帳本） | 官方 `postgres:16` | 回補帳本、checkpoint、限速、發版紀錄、影子比對都在這裡 | 沒有 HA |

共用的 GCP 模組 `shared/infra/gcp` 先做過 `terraform plan`（102 個資源）與 `terraform test`（30 項通過），涵蓋回補 Job（`qsearch-backfill`）、GCS、Pub/Sub 與 Cloud SQL。2026-11-02 再補 `q6.tf`：斷詞服務 v0／v1／v2、文字雲 API、採集器的 Cloud Run 與 SA、`qsearch-parsed-posts`／`qsearch-dict-releases` 主題與訂閱、字典 bucket、YouTube 金鑰 secret，加上第二個 NAT 固定 IP 與 Cloud SQL 逾時旗標（離線 plan 147 個資源、`terraform test` 37 項通過；見該目錄 README「第三輪驗證」）。上表 Compute Engine 上的 Elasticsearch VM 與 Ops Agent、Cloud Monitoring 告警政策還沒有 Terraform。這裡沒有 apply。

## 資料夾

| 路徑 | 內容 |
|---|---|
| `docker-compose.yml` | 全部服務；`seg-job` 在 `job` profile 裡，用 `docker compose run` 起 |
| `run.sh` | 一步一步重跑：`build`、`up`、`seed`、`backfill`（＋`backfill-tasks`）、`live`、`stream`、`exp`、`ik`、`probe`、`shots` |
| `es/` | ES 映像（外掛下載與驗證 `fetch_plugins.sh`、IK 遠端字典設定）、索引定義 `index/posts.json` |
| `dict/` | 字典發版檔 `releases/qsearch-q6-2026.10.30.{1..6}.json` 與 `build_artifact.py`；v0 舊流程重現版 `releases/qsearch-q6-legacy-2019.12.31.1.json`＋`build_legacy.py`＋`legacy/`（教育部抽詞、凍結 2019 的公司詞表、舊行政區名） |
| `seg/qseg/` | 斷詞映像：`service`（串流；v2 另訂閱發版控制訊息 `control.py`，每台輪詢 manifest 收斂）、`job`（回補）、`rerun`（定向重跑）、`impact`（影響範圍地面真值）、`shadow`（影子比對）、`coverage`（覆蓋與缺值）、`recon`（四層對帳＋回補燃盡＋發版收斂）、`quality`（品質日報；金標在 `seg/quality/gold_150.json`）、`approve`／`baseline`、`seed` |
| `api/`、`web/` | 文字雲 API（FastAPI；`/api/ops/alert` 收 Alertmanager、`/api/rum` 收 PM 頁 RUM）、PM A/B 頁面（無外部套件；`rum.js` 自寫 RUM）、資料工程端操作（`ops.py`：發版（outbox）、簽核、重播、限速）、來源採集器（`collector.py`＋`feeds.json`：每次請求記 outcome，YouTube 走 Data API） |
| `pg/init.sql`、`pg/live.sql`、`pg/migrate_p0.sql` | 帳本、控制表、批次紀錄、發版紀錄、影子比對、對帳 `recon_daily`、品質 `quality_daily`、`posts_terms`；即時操作用的 `rerun_runs`、`service_state`、`live_events`（含告警）、`collector_state`、`release_outbox`；既有 volume 用 `migrate_p0.sql` 補表 |
| `tests/` | 即時操作的純函式測試（發版、版本號、feed 解析、原文湖解析）：`pytest -p no:cacheprovider q6/stack/tests` |
| `monitoring/` | OTel Collector、Prometheus（`rules/*.yml`；`slo/` 是 Sloth 規格）、Alertmanager、Loki＋Alloy、Grafana 資料來源與兩張儀表板（`grafana/build_dashboard.py` 產生：主看板 9 層、採集看板 6 列） |
| `collect/` | 六渠道樣本的抓取程式；`data/channels/` 是抓到的資料與紀錄 |
| `scripts/` | 準備原文、建索引與訂閱、送串流、各項實驗、延遲探針、截圖 |
| `results/` | 每次實測的日誌與 JSON（`backfill/`、`es/`、`stream/`、延遲探針） |
| `runtime/` | 執行時產生的字典 artifact 與原文（不進 git） |

## 怎麼重跑

需要 Docker、Python 3（`requests`、`jieba`、`psycopg[binary]`、`google-cloud-pubsub`、`pyarrow`）、Node＋Playwright。原始檔不進 git，放在 `DL` 目錄：

| 檔案 | 來源 | 大小／sha256 前 16 碼 |
|---|---|---|
| `dict.txt.big` | jieba 官方 repo 的繁體加強主字典 | `b16011275c42955c` |
| `ptt_pretrain.parquet` | Hugging Face `yuhuanstudio/PTT-pretrain-zhtw`（Apache-2.0）：507,555 篇 PTT 文章全文，每篇帶看板、標題與原始發文時間（2001–2025） | 508,167,773 bytes；`https://huggingface.co/api/datasets/yuhuanstudio/PTT-pretrain-zhtw/parquet/default/train/0.parquet` |
| `ptt_gossiping_100k.parquet` | Hugging Face `Riyuechang/PTT-Corpus-100K_Gossiping-1400-39400`（Apache-2.0）：105,608 篇八卦板文章，`article_id` 的 `M.<unix 秒>` 就是原始發文時間 | 7,892,857 bytes；`…/Riyuechang/PTT-Corpus-100K_Gossiping-1400-39400/parquet/default/train/0.parquet` |

另有 repo 內的 [`../data/ptt_titles_7d.jsonl`](../data/ptt_titles_7d.jsonl)（八卦板標題，時間取自文章代碼）。v0 舊流程重現版字典另需 `moe_revised_words.txt`（教育部《重編國語辭典修訂本》2015，`dict/legacy/extract_moe.py` 從官方 zip 抽「字詞名」欄）與 `jieba_tw_dict.txt`（jieba-zh_TW 的 `jieba/dict.txt`），`./run.sh build` 會一併建出 v0 字典 artifact（見 RESULTS §17）。**原文湖裡只有原文＋原始發文時間**：解析不出時間的文章略過、已刪除文章（沒有代碼）略過，沒有任何合成時間；舊版用的 `Gossiping-QA-Dataset`（問答沒有發文時間）不再使用。每個來源收了多少、篩掉多少，在 `runtime/raw/_manifest.json`。

```bash
cd q6/stack
DL=/path/to/downloads ./run.sh build   # 公司代理攔 TLS 時加 CA=/path/to/ca.crt
ES_HEAP=2g ./run.sh up                  # 記憶體小的機器（例如 7 GB 的 WSL）用 2g
DL=/path/to/downloads ./run.sh seed     # 小機器可加 SINCE=2020-01 MAX_PER_SOURCE=50000（來源不變，只是少收）
AUTO_APPROVE=0 ./run.sh backfill        # 首月分區跑完 → 到 PM 頁簽核 → ./run.sh backfill-tasks（AUTO_APPROVE=1 則用指令簽核）
./run.sh live                           # current 切到最新版；起 seg-v0／v1／v2、API、採集器、覆蓋／對帳／品質服務 → PM 頁開始每 2 秒反映
./run.sh stream                         # 六渠道樣本（含重送冪等、壞訊息進 DLQ 的實測）
./run.sh exp
./run.sh ik            # 會把字典 current 切到 r5
./run.sh probe         # 另開終端機，回補時在旁邊量查詢延遲
./run.sh shots
./run.sh slo           # 改 SLO 目標後重產 monitoring/rules/slo.yml（Sloth）
```

打開：PM 頁 http://127.0.0.1:8080、Kibana http://127.0.0.1:5601、Grafana http://127.0.0.1:3000（看板「QSearch Q6 文字雲」「QSearch Q6 採集」）、Prometheus http://127.0.0.1:9090、Alertmanager http://127.0.0.1:9093（WSL mirrored 網路模式請用 127.0.0.1，`localhost` 會先走 IPv6）。

## PM 頁上的資料工程端（即時）

PM 頁下半部是「資料工程端」：每一個按鈕都打同一套 ES／PostgreSQL／Pub/Sub，上半部的 A/B 視窗每 2 秒讀一次 `/api/status`，ES 狀態 token（總篇數、兩版已斷詞篇數、最大 `seg_ver`、最後斷詞時間）一變就重查兩版文字雲，並標出這次才進前 N 的詞、掉出去的詞、篇數變了的詞。

| 操作 | 實際發生什麼 | 哪裡看得到 |
|---|---|---|
| 字典發版（填詞、按「建版並發佈」） | API 從 artifact 的 `current` 版複製一份、加詞（詞頻不低於主字典）、逐檔 sha256、寫 manifest、原子切 `current`；`rerun_runs` 一列；控制訊息發到 Pub/Sub `dict-releases` | seg-v2 收到後載入新版（sha256 對不上就拒絕）、換掉服務用的字典、對原文含新詞字串的文章定向重跑；進度每 0.5 秒寫回 `rerun_runs`，頁面顯示候選／掃過／詞表實際變了幾篇、前 3 個例子；IK 60 秒內看到 `ik_ext.dic` 變了也會重載 |
| 回補簽核／豁免 | `backfill_ledger` 由 `awaiting_pm`→`passed`（簽核人、時間寫進 `gates.G4`）；豁免只允許 `failed` 分區且一定要寫說明 | 回補 Job 下一次領分區就會放行；事件流 |
| 回補限速／暫停 | `backfill_control`；Job 每批前都讀 | 控制列、Grafana 吞吐 |
| 重播六渠道樣本 | 267 篇（`data/channels/`）由 parser 寫本體 → `parsed-posts` → seg-v1／seg-v2 各自斷詞；同版本重送是 noop | 「剛進來的文章」每篇兩版詞表與延遲 |
| 來源採集器 | `collector.py` 輪詢 PTT 看板 Atom、YouTube 頻道 RSS、中央社／公視 RSS（`api/feeds.json`）：只抓站方公開給閱讀器的 feed，不登入、帶 ETag／If-Modified-Since、同一主機間隔 ≥ 2.5 秒、429／5xx 退避；新文章走同一條串流路 | 每個 feed 的上次抓取、HTTP 狀態、新進篇數；「立刻抓一次」3 秒內開始 |

哪些是對應做法，頁面上也寫明：**舊流程 v1 是用 jieba 預設主字典（HMM 關、不清洗、不過濾）算的**，公司現行的字典拿不到，所以 v1 的詞表是實際算出來的，但不是公司現在線上的詞表；Pub/Sub 是官方模擬器（client 程式碼與 GCP 上相同）；Facebook／Instagram／Threads 沒有公開 feed，只有抓過的公開頁樣本可重播。

這次在 Linux 容器裡跑：4 核心、15 GB RAM；ES heap 4 GB。容器的磁碟是固定配額，ES 依「總容量百分比」算的磁碟水位會誤判已滿，所以 compose 裡改用絕對剩餘空間（4 GB／2 GB／1 GB）。
