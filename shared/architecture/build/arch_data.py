# Content for the four added tabs and the node deploy rows.
# Every number here comes from a file in the repo; the source is named next to it.

TF_PLAN = "terraform（shared/infra/gcp）：第一輪對真實 GCP project plan 70 to add、0 change、0 destroy；第二輪（2026-10-30，離線）plan 102 to add、terraform test 30 passed、fmt／validate 退出碼 0，新增的 32 項尚未對真實專案 plan；未 apply、未啟用 API。"

# status → (tone, label). Tones reuse the shell's evidence colours.
STATUS = {
    "planned":   ("direct",   "已 plan"),
    "validated": ("build",    "只 validate"),
    "offline":   ("build",    "第二輪離線 plan"),
    "partial":   ("inferred", "部分涵蓋"),
    "gap":       ("inferred", "未寫 Terraform"),
    "external":  ("weak",     "外部服務"),
}

# int-variant node → GCP resource / on-prem counterpart
# sources: README.md 六段管線表, docs/handoff/gcp.md §1–§5, docs/architecture/gcp-vs-onprem.md, infra/onprem/README.md
DEPLOY = {
    "trigger":   {"gcp": ["google_cloud_scheduler_job.crawl_tick"], "st": "planned",
                  "onprem": "cron 或 Airflow", "diff": "地端排程機是單點，要自己做 HA 與冪等。第二輪加了 Q3 的 ptt-crawl-hourly：每小時直接執行 Cloud Run Job ptt-crawl（離線 plan）；地端 scheduler 用 deterministic job_id 去重，已實測同一時間窗不會重派。"},
    "sched":     {"gcp": ["google_cloud_run_v2_service.scheduler", "google_cloud_run_v2_service.api"], "st": "planned",
                  "onprem": "FastAPI 服務加排程器", "diff": "地端沒有自動擴縮。"},
    "jobdb":     {"gcp": ["google_sql_database_instance.crawler_db"], "st": "planned",
                  "onprem": "PostgreSQL", "diff": "備份與還原要自己演練。第二輪在 Cloud SQL 加了每日備份＋PITR 7 天、ENCRYPTED_ONLY 與 IAM 資料庫登入（離線 plan）。"},
    "dispatch":  {"gcp": ["google_cloud_tasks_queue.crawl[...]"], "st": "planned",
                  "onprem": "RabbitMQ（每來源一條 queue）", "diff": "每條佇列的速率要自己用 consumer 數量加令牌桶做（地端用 Redis 每秒計數器、固定視窗，已實作，限速效果未實測）。第二輪補上每站限速的 crawl-ptt（1 rps）與 crawl-threads（20 rps），以及 Cloud Tasks 以 OIDC 呼叫 worker 的權限鏈（離線 plan，尚未對真實專案驗證）；原本的 crawl-fb／yt／news 保留。"},
    "crawl":     {"gcp": ["google_cloud_run_v2_service.crawler_worker", "google_compute_router_nat.crawler"], "st": "planned",
                  "onprem": "Docker／K8s worker 加出口閘道", "diff": "出口 IP 是最大差異：雲端 IP 段常被目標站封鎖，地端 IP 也可能被封，兩邊都要代理池備案。Cloud NAT 的固定出口 IP 本身也是雲端 IP 段，不能繞過 Threads 封鎖，用途是給住宅代理商做 IP 白名單。"},
    "tokenpool": {"gcp": ["Firestore"], "st": "gap",
                  "onprem": "Redis（開 AOF）", "diff": "Firestore 不在 shared/infra/gcp 內。"},
    "tokenjob":  {"gcp": ["Cloud Run Job token-refresher"], "st": "gap",
                  "onprem": "K8s CronJob 加無頭瀏覽器", "diff": "session refresher 不在 shared/infra/gcp 內。"},
    "proxy":     {"gcp": ["google_secret_manager_secret.proxy_credentials"], "st": "external", "onprem": "同左（住宅輪替代理）", "diff": "外部付費服務；第二輪在 Terraform 寫了 Secret Manager 的帳密容器（只跑過離線 plan，不放值），worker 可讀。"},
    "platforms": {"gcp": [], "st": "external", "onprem": "同左", "diff": "目標站本身。"},
    "gcs":       {"gcp": ["google_storage_bucket.raw_scraped"], "st": "planned",
                  "onprem": "MinIO（S3 相容）", "diff": "磁碟、備份、容量規劃要自己管。plan 版是 30 天轉 COLDLINE。"},
    "fetched":   {"gcp": ["google_pubsub_topic.qsearch_fetched", "google_pubsub_schema.qsearch_event_v1"], "st": "planned",
                  "onprem": "RabbitMQ 或 Kafka topic 加 JSON schema", "diff": "schema 的強制力不同。"},
    "parser":    {"gcp": ["google_cloud_run_v2_service.parser", "google_pubsub_subscription.qsearch_parser_sub"], "st": "planned",
                  "onprem": "FastAPI consumer", "diff": "最大損失是重播：Pub/Sub 可 Seek 倒回重跑，RabbitMQ 已確認的訊息不會留。"},
    "replay":    {"gcp": ["google_cloud_run_v2_job.qsearch_backfill"], "st": "planned",
                  "onprem": "K8s Job 或 CronJob", "diff": "Pub/Sub Seek 的替代：crawl_jobs 表＋MinIO 原始檔重新產生事件（地端已實測，清空衍生資料後 2.1 秒重建）。"},
    "searchdb":  {"gcp": ["google_sql_database_instance.crawler_db"], "st": "partial",
                  "onprem": "PostgreSQL 加 pg_bigm", "diff": "第二輪已開 cloudsql.enable_pg_bigm flag（離線 plan），CREATE EXTENSION 要在真實專案驗證。地端用 shared/infra/onprem/postgres-bigm/Dockerfile 自建 pg_bigm 1.2：地端 stack（PostgreSQL 15.19）實測走索引；Q2 用同一個 Dockerfile 換成 PostgreSQL 16.15 映像，在 Docker 量了 100 萬則（PTT 索引 953,729,024 B）。EDB 的 Windows 版沒有附 pg_bigm（q2/code/RESULTS.md，PR #6）。"},
    "searchapi": {"gcp": ["BigQuery 資料表（依發文日分區）", "Cloud Run · 查詢 API"], "st": "gap", "onprem": "PostgreSQL 全表掃描加 FastAPI（推論）",
                  "diff": "BigQuery 表與查詢 API 都沒有 Terraform。地端沒有 BigQuery；7 天只有 22.9 MB，用 PostgreSQL 全表掃描應該夠（推論，未實測）。"},
    "es":        {"gcp": ["Elasticsearch", "google_secret_manager_secret.es_api_key"], "st": "gap", "onprem": "OpenSearch", "diff": "授權與版本相容性要測。叢集本體沒有 Terraform；第二輪只在 Terraform 寫了 API key 的 Secret（離線 plan）。"},
    "datalayer": {"gcp": ["QSearch 既有元件"], "st": "gap", "onprem": "同左", "diff": "題目透露的元件，部署方式題目沒說。"},
    "analytics": {"gcp": ["QSearch 既有元件"], "st": "gap", "onprem": "同左", "diff": "題目透露的元件，部署方式題目沒說。"},
    "frontend":  {"gcp": ["QSearch 既有元件"], "st": "gap", "onprem": "同左", "diff": "題目透露的元件，部署方式題目沒說。"},
    "export":    {"gcp": ["QSearch 既有元件"], "st": "gap", "onprem": "同左", "diff": "題目透露的元件，部署方式題目沒說。"},
}

# GCP／地端 tab: rows grouped by the six stages (source: docs/architecture/gcp-vs-onprem.md §1, infra/onprem/README.md)
DEPLOY_ROWS = [
    (0, "Cloud Scheduler", "google_cloud_scheduler_job.crawl_tick", "planned", "cron／Airflow", "地端要自己做 HA 與冪等", ["3"], "trigger"),
    (0, "scheduler（Cloud Run）", "google_cloud_run_v2_service.scheduler", "planned", "FastAPI 服務加排程器", "地端沒有自動擴縮", ["3"], "sched"),
    (0, "Cloud SQL PG15（私有 IP）", "google_sql_database_instance.crawler_db", "planned", "PostgreSQL 加 pg_bigm", "備份與還原要自己演練", ["2", "3"], "jobdb"),
    (0, "Scheduler ptt-crawl-hourly → Cloud Run Job ptt-crawl", "google_cloud_scheduler_job.ptt_crawl_hourly、google_cloud_run_v2_job.ptt_crawl", "offline", "cron 加 K8s CronJob", "Q3 每小時抓一次，經 Cloud NAT 出去", ["3"], "trigger"),
    (1, "Cloud Tasks ×3", 'google_cloud_tasks_queue.crawl["crawl-fb" / "crawl-yt" / "crawl-news"]', "planned", "RabbitMQ queue", "每條佇列的速率要自己控制", ["3", "4"], "dispatch"),
    (1, "Cloud Tasks 每站限速 ×2（PTT 1 rps、Threads 20 rps）", 'google_cloud_tasks_queue.crawl_site["crawl-ptt" / "crawl-threads"]', "offline", "RabbitMQ 加 Redis 每秒計數器（已實作，未實測限速）", "OIDC 呼叫 worker 的權限鏈要在真實專案驗證", ["3", "4"], "dispatch"),
    (1, "Pub/Sub crawler-tasks 加 DLQ", "google_pubsub_topic.crawler_tasks、google_pubsub_topic.crawler_tasks_dlq", "planned", "RabbitMQ 加 DLX", "DLX 對應 DLQ，但沒有 Seek", ["3"], None),
    (2, "Cloud Run crawler-worker", "google_cloud_run_v2_service.crawler_worker", "planned", "K8s／Docker worker", "IP 來源不同（Q4）", ["3", "4"], "crawl"),
    (2, "Cloud NAT 固定出口 IP", "google_compute_router_nat.crawler、google_compute_address.egress", "planned", "出口閘道加固定 IP 或代理池", "固定出口 IP 本身是雲端 IP 段，不能繞過 Threads 封鎖；用途是給住宅代理商做 IP 白名單", ["4"], "crawl"),
    (2, "GKE 瀏覽器池（方案 B）", "optional/gke：google_container_node_pool.browser_pool", "offline", "K8s Deployment 加 HPA", "雲端節點可以縮到 0；把 data source 換成字面值後 plan 8 to add", ["4"], None),
    (2, "Secret Manager（代理帳密、ES API key）", "google_secret_manager_secret.proxy_credentials、google_secret_manager_secret.es_api_key", "offline", ".env 或 Vault", "Terraform 只寫容器、不放值，只跑過離線 plan", ["4", "5"], "proxy"),
    (2, "token 池（Firestore）", "未寫", "gap", "Redis（開 AOF）", "持久化要自己設定", ["4"], "tokenpool"),
    (3, "GCS raw-scraped", "google_storage_bucket.raw_scraped", "planned", "MinIO", "地端要自己備份", ["3", "5", "6"], "gcs"),
    (3, "Pub/Sub qsearch-crawl-fetched（Avro）", "google_pubsub_topic.qsearch_fetched、google_pubsub_schema.qsearch_event_v1", "planned", "RabbitMQ／Kafka topic 加 JSON schema", "schema 強制力不同", ["5"], "fetched"),
    (4, "Cloud Run parser", "google_cloud_run_v2_service.parser", "planned", "FastAPI consumer", "—", ["3", "6"], "parser"),
    (4, "parser 訂閱（Seek 回放）", "google_pubsub_subscription.qsearch_parser_sub", "planned", "job 表加原始檔回放，或 Kafka log", "重播能力的損失", ["5", "6"], "parser"),
    (4, "Cloud Run Job qsearch-backfill", "google_cloud_run_v2_job.qsearch_backfill", "planned", "K8s Job／CronJob", "要自己管排程", ["5", "6"], "replay"),
    (5, "BigQuery ingest_events", "google_bigquery_table.ingest_events、qsearch_to_bq 訂閱", "planned", "ClickHouse", "逐筆寫入改成批次寫入", ["5"], None),
    (5, "pg_bigm 搜尋庫", "Cloud SQL flag cloudsql.enable_pg_bigm（第二輪）；CREATE EXTENSION 未實測", "partial", "PostgreSQL 加 pg_bigm（地端已實測走索引）", "擴充要自己裝", ["2"], "searchdb"),
    (5, "Elasticsearch 貼文索引", "叢集未寫（第二輪只在 Terraform 寫了 API key 的 Secret）", "gap", "OpenSearch", "授權與版本相容性", ["4", "5", "6"], "es"),
    (5, "Data Layer／Analytics／Export API、前端", "未寫（QSearch 既有元件）", "gap", "同左", "題目沒說部署方式", ["5"], "analytics"),
]

DEPLOY_TRADEOFFS = [
    ("重播（Seek）", "Pub/Sub 可以把已確認的訊息保留 7 天再倒回重跑。地端最接近的是「原始檔放 MinIO 加 job 表」，或改用 Kafka。這會影響 Q5 回補與 Q6 回朔。"),
    ("速率控制", "Cloud Tasks 內建每秒派送上限與重試；RabbitMQ 要靠 consumer 數量加令牌桶。這會影響 Q3、Q4 會不會被目標站封鎖。"),
    ("出口 IP", "GCP 用 Cloud NAT 固定 IP，但 Q4 題幹指出雲端 IP 段常被列入黑名單；地端 IP 也不一定乾淨，兩邊都要有代理池備案。"),
    ("冪等", "Pub/Sub 與 Cloud Tasks 都是至少送達一次，地端也一樣要在 DB 用 event_id 唯一鍵保證。"),
    ("維運成本", "地端省掉雲端用量，但多了主機、磁碟、備份、升級與憑證。Q2 的「一人維運、每月 3,000 元」是決定性限制：GCP 閒置約 US$104／月（約 NT$3,382，以第一輪 70 項計，Cloud SQL 佔 US$100），是預算的 1.13 倍；Cloud SQL 改 db-custom-1-3840，這一項約 US$51（待決定）。地端估 NT$1,515–3,914，不含人力。"),
]

DEPLOY_SWITCH = [
    ("規模小、一人維運、要快上線", "GCP：託管服務降低維運負擔"),
    ("要固定出口 IP，而且有機房網路", "抓取層可考慮地端，其餘留在 GCP"),
    ("分析量大、查詢密集", "分析層可評估 ClickHouse，但寫入要改成批次"),
    ("要重播或回補大量歷史資料", "GCP 的 Seek 仍是優點；地端要先做出替代"),
]

DEPLOY_GAPS = [
    "沒有 Terraform：Elasticsearch 叢集本體、Firestore token 池與 session refresher、Redis／Memorystore、Vector DB、監控、crawler-api 以外的映像建置（parser、worker、scheduler、backfill、ptt-crawl）、Data Layer／Analytics／Export API。",
    "第二輪新增的 32 項（102 − 70）只跑過離線 plan 與 terraform test；Cloud Tasks 以 OIDC 實際派送、Cloud SQL CREATE EXTENSION pg_bigm、IAM 資料庫登入、BigQuery 訂閱寫入、Secret Manager 讀取都要真實專案才能驗，並要對真實專案重跑 plan（預期 102 to add）。",
    "GKE（optional/gke）只有 validate 與替代 plan（8 to add）；Deployment 本體與 backfill CronJob 沒寫。",
    "地端 docker compose 只讀 repo 內 fixture：沒有真實抓取、高可用、規模、TLS 與監控的實測（shared/infra/onprem/VERIFY.md）。",
]

# diagram node text that newer results replace (source named in the text)
NODE_NOTE = {
    "searchdb": "Q2 的 FB 貼文搜尋用這台：pg_bigm 兩字一切，「台北」這種兩個字的查詢也用得到索引；只支援 LIKE，沒有相關度排序。"
                "真的 pg_bigm 1.2 在 Docker 裡量 PTT 100 萬則，索引 953,729,024 B，和表（910,172,160 B）差不多大；"
                "2 字詞查詢 pg_bigm 走索引 119 到 147 毫秒，pg_trgm 全表掃描 0.54 到 0.66 秒。「台灣」這種出現在一半列的詞，兩者都全表掃描，約 0.6 秒"
                "（q2/code/RESULTS.md，PR #6）。Q3 的 PTT 搜尋不用這台，存在右邊的 BigQuery。",
    "platforms": "PTT 沒有 API，八卦板要帶 over18=1 cookie；repo 以 0.8 秒間隔打了約 6,800 次，沒被擋過，抓取程式現在預設每次停 1.5 秒。"
                 "PTT 的 robots.txt 對一般爬蟲是 Allow: /，只對幾個 AI 爬蟲的 user agent 是 Disallow: /。"
                 "Threads 每帳號每輪一次 GraphQL POST（first=10），回最新 10 串的 code、全文、taken_at；doc_id 兩天出現 3 個值，會換，不能寫死。"
                 "FB 用 Graph API，要通過 Page Public Content Access 審核。",
    # Q6 sandbox (q6/stack/RESULTS.md, PR #8): local Docker, 42.5 萬 PTT posts
    "es": "QSearch 產品實際存貼文的地方：Q5、Q6 題目都寫到 ES，Q6 說約 5 億筆。Q4 的 Threads 貼文寫進這裡。"
          "Q5 要先改 mapping，新增 video_view_count 和 repost_count（long）。mapping 可以加欄位、不能改型別，型別錯了只能 reindex。"
          "Q6 新舊詞表並存：每篇同時有 terms_v1、terms_v2 與各自的 seg_ver，寫入一律經 Painless 腳本，版本只能往前。"
          "本機沙盤（官方 ES 9.5.5 單節點，加 smartcn、ICU、IK 外掛，42.5 萬篇）：回補時全期文字雲 p95 從 114 ms 升到 192 ms，回補時段最慢 343 ms；"
          "IK 每分鐘檢查一次遠端字典，兩次實測 8 秒與 58 秒後生效，但舊文件的片語搜尋會掉到 0，要對含新詞的文章 update_by_query 才回來。"
          "GCP 上規劃在 Compute Engine 自架，叢集仍沒有 Terraform。",
    "parser": "依 source 分流。PTT 沿用 repo 的選擇器（push-tag、push-userid、push-content、push-ipdatetime，切掉「※ 發信站」以下），"
              "但 repo 只算統計，要改成輸出內文和每則留言；普查 6,224 篇裡有 2 篇出現「檔案過大」，留言不完整，要標記。"
              "Threads 從 JSON 取 code、全文、taken_at；FB 沿用這邊寫的 parse。Q5 的兩個欄位也在這裡加進 ES 文件。"
              "Q6 的串流斷詞在 parser 之後另起一個 Cloud Run 服務（總圖不加節點，併在這裡）：parser 只部分更新本體欄位、不碰斷詞欄位，"
              "並把結果發到 Pub/Sub parsed-posts；斷詞服務 v1、v2 各訂閱一條，和回補 Job 同一個映像、同一份字典，啟動時逐檔比對 sha256。"
              "本機沙盤六渠道 267 篇送出後 1.73 秒全部有 v1 與 v2；parser 先寫或後寫都不會蓋掉斷詞欄位；整批重送不會重寫；"
              "壞訊息投遞 5 次都失敗後進 DLQ（Pub/Sub 官方模擬器）。",
    "replay": "Q5 回補：照日期分批讀 GCS 原始檔，重新發 fetched 事件給同一個 parser，新舊資料走同一套解析。用佇列控速，避免 ES 寫入拖慢線上查詢；"
              "每批跑完比對當天筆數與欄位非空比例。歷史欄位名不一致時，在 parser 加對照表。"
              "Q6 回朔不重發事件：Job 用和串流斷詞同一個映像，自己讀原文、斷詞，直接寫 ES 的 terms_v2。"
              "task 從 Cloud SQL 帳本領「渠道 × 月」分區（FOR UPDATE SKIP LOCKED），每 500 篇寫一次 checkpoint 與心跳，被砍後從 checkpoint 接續。"
              "每個分區要過 G1 筆數、G2 品質、G3 線上負載、G4 PM 首月簽核；G3 量到查詢變慢就把全域限速砍半，本機沙盤從每秒 3,000 降到 1,500 再到 750。"
              "沙盤用 42.5 萬篇 PTT、46 個月分區跑了三輪。",
    "gcs": "PoC 的 lifecycle 是 30 天就刪，但 Q5 要回補 2026/5/1 起的 IG 觀看數，這些原始檔早就被刪了。改成 30 天轉 Archive、不刪除。"
           "量：PTT 每天約 100 MB（含留言回抓）；Threads 每天約 45 GB，要存壓縮後的 bytes（以 zuck 第 2 頁實測，解壓後約 9 倍大）。"
           "Q6 回朔也從原文讀、不從 ES 撈；2023/01 起的原文若已被 30 天規則刪掉，要改從 BigQuery 或其他匯出讀（沙盤用本機檔案當替身）。"
           "Q6 的字典 artifact 規劃放 Cloud Storage：每版一個資料夾、逐檔 sha256。沙盤用 nginx 替身驗證了 current 原子切換，以及 IK 靠 Last-Modified／ETag 重載；"
           "搬到 Cloud Storage 後怎麼原子切版、IK 怎麼讀私有 bucket，還沒驗證。",
    "frontend": "Q5 要改的第 6 層。SLA 是發文 1 小時後查得到。前端和 Export 都只吃 Analytics API，兩邊可以平行開發（推論）。"
                "Q6 給 PM 的 A/B 頁：同一範圍左右並排舊版 terms_v1 和新版 terms_v2 文字雲，下面列只在一邊出現的詞，點詞可看原文。"
                "軟停用詞在文字雲 API 查詢時套用，API 每 30 秒讀一次清單，改清單不用寫 ES。沙盤的 PM 簽核是用指令模擬的。",
}
NODE_Q_ADD = {"gcs": ["6"]}  # Q6 re-parses from the raw files

# Q3 answer (q3/answer, shared/docs/submission.md): PTT goes to BigQuery, not pg_bigm
Q3_BQ_NOTE = ("Q3 的 PTT 搜尋存這裡：每小時的 Cloud Run Job 抓新文並重抓 7 天內超過 23 小時沒抓的文章，原始 HTML 存 Cloud Storage，"
              "結果 MERGE 進 BigQuery，依發文日分區，失敗的下一輪補。查詢 API 用 Cloud Run，對 7 天分區做 LIKE 全掃，貼文和留言各查一次再合併。"
              "選 BigQuery 不選 pg_bigm：7 天純文字只有 22.9 MB，全掃不用建索引，也沒有資料庫主機要維護；"
              "查詢延遲沒有實測，1 到 3 秒是預估。總圖把 PTT 的解析畫在共用的 parser；Q3 答案是 Job 自己解析後直接寫入，兩者寫進同一張表。")  # Q6 re-parses from the raw files

# ---- Q5 data-flow tab -----------------------------------------------------
Q5_CAP = {  # captions for round-2 captures (shared/docs/playwright-round2.md)
    "q5_th_drift": "同一則 Threads 貼文三次抓取：轉發 16 → 22 → 22；畫面 22＝repost_count 18＋quote_count 4",
    "q5_ig_drift": "9 支 Reels 隔 4.6 小時重抓 play_count：最新一支 +29.3%，舊的 +0.02%～+0.56%",
    "q5_ig_cloud": "雲端 IP 開 IG Reels：302 到登入頁，登入頁回 HTTP 429；要走住宅代理",
}
# sources: q5/README.md, q5/b-c-d/README.md, tools/capture/capture_manifest.json, docs/handoff/playwright.md
Q5_NODES = {
    "ig": {"label": "Instagram Reels", "sub": "觀看數 play_count", "cls": "ext", "who": "外部平台", "act": "來源",
           "what": "未登入的 Reels 分頁縮圖就印著觀看數，同一次載入的頁面 JSON 欄位是 play_count；題目寫的 video_view_count 沒有出現。單支 Reel 頁未登入時沒有觀看數，所以要從 Reels 分頁取。",
           "ev": ["q5_ig_grid_json", "q5_ig_drift", "q5_ig_cloud"]},
    "threads": {"label": "Threads", "sub": "轉發數 repost＋quote", "cls": "ext", "who": "外部平台", "act": "來源",
           "what": "畫面上的轉發數等於 repost_count 加 quote_count：@threads 16 篇加 2 篇 Use media 貼文，18 篇全部符合；只看 repost_count，16 篇裡只有 1 篇對得上。分享沒有數字時 reshare_count 是 null，不是 0。",
           "ev": ["q5_th_drift", "q5_th_reshare_null", "q5_th_wan"]},
    "sched": {"label": "排程", "sub": "不改 · Cloud Scheduler", "cls": "", "who": "既有系統", "act": "不改",
           "what": "沿用既有的定時觸發。GCP 版是 Cloud Scheduler crawl-tick，已在 terraform plan 的 70 項內。", "ev": []},
    "crawler": {"label": "爬蟲", "sub": "不改 · 已抓到兩個欄位", "cls": "", "who": "既有系統", "act": "不改",
           "what": "爬蟲端本來就抓得到這兩個數字（Playwright 實抓：IG Reels 分頁、Threads 貼文頁），缺的是之後寫進 ES 的每一層。",
           "ev": ["q5_th_post_repost"]},
    "gcs": {"label": "原始資料 GCS", "sub": "不改 · 回補從這裡讀", "cls": "", "who": "既有系統", "act": "不改",
           "what": "回補要讀舊原始檔。PoC 的生命週期是 30 天刪除，2026/5/1 起的 IG 原始檔已經不在；plan 版改成 30 天轉 COLDLINE。超過 30 天的部分要看爬蟲端原本存在哪裡。",
           "ev": []},
    "writer": {"label": "爬蟲寫入", "sub": "修改 · DE 12 小時", "cls": "mod", "who": "資料工程師", "act": "修改",
           "what": "讀 JSON 整數、null 保留、寫入新欄位，加單元測試。不抓畫面文字，因為畫面的「萬」是無條件捨去（12,716 顯示 1.2 萬）。依賴 ES mapping。",
           "ev": ["q5_th_wan"]},
    "backfill": {"label": "回補程式", "sub": "新增 · DE 12＋4 小時", "cls": "new", "who": "資料工程師", "act": "新增",
           "what": "原型 backfill.py 已完成：正規化、同一篇只留 crawled_at 最新、ES _bulk 以 version_type=external、version＝crawled_at 寫入，舊資料不會蓋掉新資料；依週 × 平台切批、限速送出。pytest 11 項通過。",
           "ev": []},
    "es": {"label": "Elasticsearch", "sub": "修改 · mapping 加欄位", "cls": "mod", "who": "資料工程師", "act": "修改",
           "what": "mapping 新增 text_post_app_info.repost_count／quote_count／reshare_count 與觀看數欄位（DE 6 小時，staging 驗證）。回補寫進新 index threads-posts-v2，核對後再用 alias 切換，查詢不受影響。",
           "ev": []},
    "monitor": {"label": "延遲監控", "sub": "新增 · 首次可見 P95", "cls": "new", "who": "資料工程師", "act": "新增",
           "what": "SLA 是發文 1 小時後查得到。最可能的瓶頸是爬蟲抓取頻率，不是 ES 寫入。監控首次可見延遲（P95 超過 45 分鐘警告、超過 60 分鐘呼叫）、1 小時內首次抓取達成率（低於 95% 警告）、Pub/Sub 最舊未確認訊息年齡。端到端整合加監控 DE 8 小時。",
           "ev": ["q5_ig_drift", "q5_th_drift"]},
    "datalayer": {"label": "Data Layer API", "sub": "修改 · BE 8 小時", "cls": "mod", "who": "後端", "act": "修改",
           "what": "查 ES 時把新欄位帶出來，並定好 API 契約。契約要在第 2 天前定案，前端和 Export 才能平行開發。", "ev": []},
    "analytics": {"label": "Analytics API", "sub": "修改 · BE 10 小時", "cls": "mod", "who": "後端", "act": "修改",
           "what": "回傳觀看數與轉發數。「轉發數」要先跟 PM 確認：API 的 repost_count，還是畫面上的 repost_count＋quote_count。原型只存兩個分量，畫面值在 Data Layer 計算。",
           "ev": ["q5_th_drift"]},
    "export": {"label": "Export Service", "sub": "修改 · BE 6 小時", "cls": "mod", "who": "後端", "act": "修改",
           "what": "匯出檔加欄位並寫清楚指標定義。只依賴 Analytics API。", "ev": []},
    "frontend": {"label": "前端畫面", "sub": "修改 · FE 10 小時", "cls": "mod", "who": "前端", "act": "修改",
           "what": "顯示新欄位；null 顯示「—」，萬位格式照平台無條件捨去。等 Analytics API 的契約。", "ev": []},
    "customer": {"label": "客戶", "sub": "發文 1 小時後查得到", "cls": "ext", "who": "使用者", "act": "看到數字",
           "what": "SLA 量的是首次可見延遲，不是「1 小時內數字已經是最終值」，轉發數會一直增加。", "ev": []},
}

# (b) 工時表，source: q5/b-c-d/README.md
Q5_TASKS = [
    ("1", "ES mapping 更新", "DE", 6, "無", True),
    ("2", "爬蟲寫入", "DE", 12, "1", True),
    ("3", "歷史回補（開發＋執行核對）", "DE", 16, "1", True),
    ("4", "Data Layer API", "BE", 8, "1 的欄位名", False),
    ("5", "Analytics API", "BE", 10, "4", False),
    ("6", "Export Service", "BE", 6, "5", False),
    ("7", "前端顯示", "FE", 10, "5 的 API 契約", False),
    ("8", "端到端整合＋SLA 監控", "DE", 8, "2、3、7", True),
    ("9", "驗收：確認「轉發數」定義", "全員", 4, "8", True),
]

Q5_SLA = [
    ("首次可見延遲", "ES 寫入時間 − 發文時間", "P95 > 45 分鐘警告；> 60 分鐘呼叫"),
    ("首次抓取達成率", "1 小時內被抓到的新文 ÷ 新文總數", "< 95% 警告"),
    ("爬蟲週期", "每帳號的抓取週期與耗時", "週期 > 30 分鐘警告"),
    ("Pub/Sub 積壓", "最舊未確認訊息的年齡", "> 10 分鐘警告"),
    ("ES 寫入錯誤率", "bulk 4xx／5xx 比例（排除 409）", "> 0.1% 警告"),
    ("金絲雀帳號", "固定幾個帳號每小時檢查", "任一失敗即警告"),
]
