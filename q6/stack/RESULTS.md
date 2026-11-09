# Q6 實測結果（Docker 環境）

2026-10-30 在Linux 容器（4 核心、15 GB RAM）用 [`README.md`](README.md) 的環境實際跑出來的數字。每個數字後面都附原始檔，日誌與 JSON 在 [`results/`](results)，截圖在 [`../evidence/stack/`](../evidence/stack)。全部在本機 Docker 跑，沒有開任何 GCP 計費資源。

圖：[整套系統架構圖](../architecture/stack/diagram-stack.png)、[沙盤推演 10 種情境](../architecture/stack/diagram-sandbox-scenarios.png)。

## 一眼看完

| 問題 | 實測 | 原始檔 |
|---|---|---|
| 42.5 萬篇回補要多久？ | 1 個 task 每秒約 2,900 篇（限速 3,000）；3 個 task 共用全域限速 3,000 時合計約每秒 2,770 篇；不限速約每秒 5,170 篇 | `results/backfill/` |
| 回補會不會拖慢 PM 查文字雲？ | 全期查詢 p95：閒置 114 ms → 3 個 task 每秒 3,000 篇 192 ms → 不限速 262 ms。G3 閘門實際自動降速 3,000 → 1,500 → 750 後通過 | `results/latency_probe_summary.json` |
| 有沒有查詢超過三分鐘？ | 回補期間背景探針 3,727 次查詢，最慢 396.9 ms，超過 180 秒 0 次、錯誤 0 次 | `results/latency_probe.jsonl` |
| 字典加一個詞要重算多少？ | 加「肥宅」：42.5 萬篇裡含這個字串 22,271 篇，詞表會變 5,253 篇，不含這個字串的 0 篇；ES 定向重跑 22,271 篇 8.6 秒 | `results/backfill/40_*`、`41_*` |
| 新舊詞表能不能並行？ | 每篇同時有 `terms_v1`／`terms_v2` 與各自的 `seg_ver`；亂序、重送、晚到都不會倒退（8 個寫者、40 個版本亂序寫同一篇，最後一定是最大版） | `results/es/guard.json` |
| 六渠道都進得來嗎？ | FB 4、IG 48、YouTube 45、PTT 48、媒體 61、Threads 61，共 267 篇，送出後 1.73 秒全部有 v1 和 v2 | `results/stream/six_channels.json` |
| 新流程有比較乾淨嗎？ | 各渠道文字雲前 30 詞的雜訊（標點、單字、數字、網址殘片、停用詞）：舊版 21–29 個，新版 0 個（FB 剩 1 個「78th」） | `results/stream/channel_noise_v1_v2.json` |
| ES 和 PostgreSQL 算出來一樣嗎？ | 前 50 詞的篇數完全一樣；全期 ES 74 ms、PG 1,579 ms；近一個月 ES 11 ms、PG 70.6 ms | `results/es/pg_vs_es.json` |

## 1. 環境與資料

- **ES**：官方 `elasticsearch:9.5.5` 單節點，外掛 `analysis-smartcn`、`analysis-icu`（官方）與 IK 9.5.5（遠端字典 `http://dictsrv/current/ik_ext.dic`）。索引 `posts` 結束時 425,154 篇、227.5 MB。
- **資料**（第 1–14 節的實測，2026-10-30 到 10-31 白天）：PTT 共 424,887 篇。八卦板問答 418,202 組（`Gossiping-QA-Dataset`，約 41.8 萬組）依編號雜湊平均分到 2023-01 到 2026-09 的 45 個月；另加 [`../data/ptt_titles_7d.jsonl`](../data/ptt_titles_7d.jsonl) 的 6,685 個八卦板標題，放在 2026-10，合計 46 個月分區。另有六渠道樣本 267 篇（未登入公開頁，抓法見 [`data/channels/COLLECTION.md`](data/channels/COLLECTION.md)）。問答的時間是合成的（`time_basis: synthetic`），只用來切分區，不代表原始發文時間；2026-10 的標題用文章代碼 `M.<unix 秒>` 裡的原始發文時間，其中 425 篇已刪除文章沒有代碼，沿用前一篇的時間。**2026-10-31 晚上起原文湖改版**（第 16 節）：只收有原始發文時間的文章（兩個公開 PTT 語料＋標題），不再有合成時間、不再補時間；上面各節的數字仍是當時那份語料跑出來的。
- **字典**：r1（舊流程）到 r6，主字典 `dict.txt.big`，`total_pinned` 固定為 87,043,992。

| 版本 | 內容 | 為什麼發 | 重算範圍 |
|---|---|---|---|
| r1 `qsearch-q6-2026.10.30.1` | 舊流程（v1） | 模擬現在線上狀態 | — |
| r2 | 新流程 v2 第一版 | 新欄位 `terms_v2` | 全量 |
| r3 | 加 新北 31、好市多 2000、全聯盟 20 | 第一輪回補全部沒過閘門 | 全量 |
| r4 | 新北 → 1000、新北投 500 | 第 2 輪有 7 個分區的「新北」被切開（另外 5 個分區是 G3 沒過） | 第 2 輪沒過的 12 個分區整批用 r4 重跑（111,747 篇），其餘 34 個分區定向重跑 787 篇 |
| r5 | 加 肥宅 20 | 新詞情境 | 定向：22,271 篇 |
| r6 | 軟停用詞（中文虛詞、英文功能詞） | FB 英文貼文的 and／of／in | 不重算（只改顯示） |

## 2. 回補（Cloud Run Jobs 的本機版本）

同一個映像、`docker compose run`，`CLOUD_RUN_TASK_INDEX` 照 Cloud Run 的變數名。每個 task 從 PostgreSQL 帳本領分區（`pg_advisory_xact_lock` ＋ `FOR UPDATE SKIP LOCKED`），每 500 篇寫一次 checkpoint 與心跳。

### 2.1 三輪結果

| 輪 | 字典 | 分區 | 結果 | 抓到什麼 |
|---|---|---|---|---|
| 1 | r2 | 46 | **46 個全部沒過**（G2） | 46 個都被「網址殘片」擋，其中 10 個另有必不可拆詞被切開。查原文發現是**檢查規則本身錯**：PTT 的 `www` 是笑、`NET` 是服飾品牌、`PHP` 是話題。另外 PM 首月關卡有洞，首月還沒簽就讓其他 45 個分區跑完了 |
| 2 | r3 | 46 | 34 過、12 沒過 | 7 個 G2（「全新／北」「出新／北」「問新／北投」這類切錯）、5 個 G3（3 個 task 同時寫，探針 p95 超過上限） |
| 3 | r4 | 只重跑那 12 個 | 10 過、2 人工豁免 | G3 自動降速 3,000 → 1,500 → 750 後全部通過；剩 2 篇「2018換新北放半天」「夜市痛電新北耶誕城」詞頻拉到 3,000 仍切錯，字典負責人用 `approve --waive G2 --note …` 豁免，理由寫進帳本 |

最後帳本：r3 通過 34 個、r4 通過 12 個（`results/backfill/final_ledger.json`）。限速表最後停在每秒 750，原因欄寫著「G3 自動降速：ptt 探針 p95 127 ms > 上限 124 ms」（`final_control.json`）。

### 2.2 閘門

| 閘門 | 檢查什麼 | 實作 |
|---|---|---|
| G1 數量 | 原文篇數 = ES 裡 `seg_ver_v2 ≥ 目標版` 的篇數 | ES `_count` |
| G2 品質 | 電話、純數字（ES regexp）；網址殘片：詞出現在原文網址裡、而且網址外沒有這個字串才算；必不可拆詞：看斷點位置，「台北市長」→ 台北／市長 不算拆錯 | ES ＋ 回補程式內 |
| G3 線上負載 | 寫入拒絕數 0；PM 查詢探針 p95 ≤ min(max(基準×1.5, 基準+50 ms), 180 秒)；基準是發版前、沒有回補時量的 74 ms，上限 124 ms；超過就把全域限速砍半、等 15 秒再量，最多 3 次 | `job.py` `canary_after` |
| G4 PM | 每個渠道第一個月要 PM 在 A/B 頁抽查簽核，簽之前同渠道其他分區不能開跑 | `approve` 指令 |

### 2.3 被砍、接手、重跑

| 情境 | 日誌 | 結果 |
|---|---|---|
| 處理到第 3,000 篇時把 task 砍掉（exit 137） | `01_first_partition_killed.log` | checkpoint 停在第 3,000 行 |
| 心跳還新時另一個 task 來領 | `02_claim_while_heartbeat_fresh.log` | 領到 0 個分區 |
| 心跳過期後接手 | `03_resume_after_stale_heartbeat.log` | 從第 3,000 行接續 |
| 修程式後同版重跑 | `04_first_partition_force_rerun.log` | `--force` 從頭跑，同版也覆寫 |
| 首月關卡 | `10_*`、`11_*`、`12_*` | 首月 → `awaiting_pm`；簽之前另一個 task 領到 0 個；PM 簽完才放行 |
| 映像的字典版本和帳本不一致 | `06_dict_mismatch_loop.log` | 修之前重複領取同一分區 130,281 次；修完直接以 exit 2 結束 |

### 2.4 速度

- 每批 500 篇：斷詞 p50 89.9 ms、bulk 寫入 p50 60.1 ms（第 2 輪 869 批，`run2_batches_r3.json`）；平均每篇 29.1 字。
- 第 1 輪單一 task 限速 3,000：每秒 2,852–2,986 篇。
- 第 2 輪：3 個 task 共用全域 3,000 → 合計約每秒 2,770 篇；不限速約每秒 5,170 篇。
- `seg_ver` 排序 bug 修正後，用 `_update_by_query` 遷移 424,887 篇的 `seg_ver_v1`：22.3 秒（約每秒 1.9 萬篇），0 衝突（`00_update_by_query_seg_ver_v1.json`）。

## 3. 字典改版：影響範圍與定向重跑

先在整個語料上算地面真值，再看 ES 定向重跑有沒有漏。

| 改版 | 含該字串 | 詞表會變 | 不含該字串卻變了 | ES 定向重跑 |
|---|---|---|---|---|
| r4 新北、新北投 | 787（只算還停在 r3 的 34 個分區；另外 12 個分區已在第 3 輪整批用 r4 重跑，不在這個數字裡） | 27 | 沒有算地面真值 | 787 篇 0.28 秒（全庫 0.185%） |
| r5 肥宅 | 22,271 | 5,253 | **0**（固定 total）；不固定 total 時詞表也是 0，但原始斷詞序列有 10 篇變了 | 22,271 篇 8.56 秒（全庫 5.24%） |

定向重跑用 ES 的 `content.bigram` 做 `match_phrase` 找含該字串、`seg_ver_v2` 比目標舊的文章，PIT ＋ `search_after` 分頁。影子比對（`50_shadow_checks.log`）：r3、r4、r5 各抽 1,000 篇，用目前程式重算後和 ES 裡的詞表比，0 篇不一致。

## 4. 新舊詞表並行與版本守門

ES 裡每篇同時有 `terms_v1`、`terms_v2`、`seg_ver_v1`、`seg_ver_v2`、`dict_version_v2`、`seg_build_v2`。`seg_ver` = 演算法 × 10¹⁰ ＋ (年 × 10⁴ ＋ 月 × 100 ＋ 日) × 100 ＋ 當天序號，例如 r5 是 22026103005。寫入一律用 Painless 腳本 `seg_guard`，只能往前。

`results/es/guard.json` 七步：

1. 斷詞比 parser 先到：scripted upsert 建出文件。
2. parser 晚到：部分更新本體欄位，斷詞欄位不動。
3. 同一版重送：noop。
4. 舊版（r4）晚到：noop，不倒退。
5. 新版（r6）：更新。
6. `force` 同版（修程式重跑）：覆寫。
7. `force` 舊版：仍然 noop。

並行：8 個寫者把 40 個版本打亂寫同一篇，最後是 22026103040（最大版），0 錯誤。

## 5. 串流（Pub/Sub 官方模擬器）

- 267 篇六渠道樣本 1.49 秒送完，送完後 1.73 秒全部同時有 v1 與 v2（`six_channels.json`）。
- 整批重送（至少一次投遞的重複）：v1、v2 各只有第一次送的 267 篇是 updated，其餘 364 次全是 noop、0 錯誤。這些計數取自 Prometheus 三小時內的最大值，含同一時段先前的重送測試，不是一次重送 267 篇的結果。
- 壞訊息 `{not json}`：兩條訂閱各 nack 5 次後進 DLQ，DLQ 裡帶 `CloudPubSubDeadLetterSourceDeliveryCount: 5`（`redelivery_and_dlq.json`）。
- 串流 v2 和回補用同一個映像、同一份字典 artifact；啟動時逐檔比對 sha256。

## 6. 文字雲品質

### 6.1 六渠道 v1 vs v2（前 30 詞裡的雜訊數）

| 渠道 | 篇數 | v1 | v2 | v2 前幾名 |
|---|---:|---:|---:|---|
| Facebook | 4 | 22 | 1 | 78th、accords、artemis… |
| Instagram | 48 | 26 | 0 | 表示、台灣、市長、民進黨 |
| YouTube | 45 | 21 | 0 | 訂閱、台灣、即時新聞 |
| PTT（2026-10 起） | 6,732 | 25 | 0 | 台灣、颱風、是不是、沈伯洋 |
| 媒體 | 61 | 24 | 0 | 今天、表示、市長、參選人 |
| Threads | 61 | 29 | 0 | 柯志恩、高雄、瑞隆 |

截圖：`../evidence/stack/pm/10_channel_*.png`（每個渠道一張 A/B）。

### 6.2 分析器對照（`results/es/analyzers.json`）

| 原文 | jieba v1（舊） | jieba v2（r6） | IK（搜尋） |
|---|---|---|---|
| 高雄市長陳其邁出席鳳山區活動 | 高／雄／市長／陳／其／邁… | 高雄／市長／陳其邁／出席／鳳山區／活動 | 高雄市／長／陳其邁… |
| 短網址 https://reurl.cc/abc123 跟 ppt.cc/fXyz 點這裡 | …https／:／/／reurl／.／cc／abc123… | 網址／點這裡 | …reurl.cc／abc123／ppt.cc／fxyz… |
| 客服電話 0912-345-678 或 (07)331-1234 | …0912／-／345／678… | 客服／電話 | …0912-345-678… |
| 肥宅在新北投排隊買全聯福利中心 | 肥／宅／在／新／北投… | 肥宅／新北投／排隊／全聯… | 肥宅／新北投… |

搜尋欄位（IK、`content.bigram`）保留網址與電話，方便找原文；文字雲欄位（`terms_v2`）不留。兩邊吃同一份 `ik_ext.dic`／`user.dict`，詞庫只維護一份。

### 6.3 軟停用詞 r6（只改顯示層）

API 每 30 秒讀一次 `current/soft_stopwords.txt`。字典切到 r6 後 21.2 秒 API 就套用，ES 寫入 0 次（`soft_stopwords_r6.json`）。這一步也抓到 API 讀清單時把 UTF-8 當 Latin-1 解碼，軟停用詞之前其實一直沒生效，已修。

## 7. 搜尋端：IK 熱更新與同義詞（`results/es/ik_hot_reload.json`）

| 步驟 | 結果 |
|---|---|
| 字典 current 從 r2 切到 r5（加「肥宅」） | IK 每分鐘看 `Last-Modified`／`ETag`，這次 58.1 秒後重載（另一次 8 秒） |
| 重載後 | `_analyze` 立刻切成「肥宅」，但**舊文件的片語搜尋從 22,088 掉到 0**：查詢端已經切成一個詞，索引裡還是「肥」「宅」兩個 |
| 對含該字串的 22,271 篇 `_update_by_query` | 1.93 秒，片語搜尋回到 22,271 |
| 同義詞 `肥宅, 阿宅`（synonyms API） | PUT 後 0.03 秒生效，搜「阿宅」從 155 篇變 22,408 篇，不用重建索引 |

第一次跑時 `_count` 一直回舊數字，原因是 ES 的 request cache（`size=0` 的請求會被快取，refresh 前後不一定失效）；改用 `_search?request_cache=false` 後才對（第一次的結果留在 `ik_hot_reload_first_try_cached_counts.json`）。

## 8. 查詢延遲與三分鐘

### 8.1 依查詢範圍（API 量到的，`results/es/latency_by_scope.json`）

| 範圍 | 篇數 | v1 p50／p95 | v2 p50／p95 | 快取命中 |
|---|---:|---|---|---|
| 全期 | 425,154 | 94／120 ms | 130／166 ms | 約 3 ms |
| 近一年 | 109,450 | 30／36 ms | 44／56 ms | 約 3 ms |
| 近一個月 | 6,903 | 9.4／10.5 ms | 9.8／12.8 ms | 約 3 ms |
| 關鍵字「颱風」 | 2,053 | 8.8／12.6 ms | 8.4／10.8 ms | 約 3 ms |

v2 全期比 v1 慢：v2 的詞總數比較少（357 萬 vs 719 萬個），但不同的詞多一倍以上（22.6 萬 vs 10.0 萬種），`terms` 聚合要比的候選變多。

### 8.2 回補期間的背景探針（`results/latency_probe_summary.json`）

探針每 0.5 秒輪流查全期、近一年、近一個月、關鍵字，22:30–23:04 UTC 共 3,727 次。

| 階段 | 全期 p50／p95 | 近一年 p95 | 近一個月 p95 |
|---|---|---|---|
| 發版前閒置（基準） | 81／114 ms | 45 ms | 19.5 ms |
| 第 1 輪，1 個 task | 91／141 ms | 71 ms | 37 ms |
| 第 2 輪，1 個 task 限速 3,000 | 94／131 ms | 49 ms | 15.5 ms |
| 第 2 輪，3 個 task 全域 3,000 | 124／192 ms | 99 ms | 61 ms |
| 第 2 輪，3 個 task 不限速 | 189／262 ms | 139 ms | 74 ms |
| 第 3 輪，3 個 task（G3 自動降速） | 109／166 ms | 80 ms | 33 ms |
| 在 ES 內部 `_update_by_query`（每秒約 1.9 萬篇） | 220／338 ms | 164 ms | 140 ms |
| 回補後閒置 | 88／114 ms | 45 ms | 15 ms |

最慢一次 396.9 ms，超過 180 秒 0 次、錯誤 0 次。

### 8.3 只有 ES 寫入、沒有斷詞程式搶 CPU（`results/es/write_load_vs_query_latency.json`）

| 寫入速度（實際） | 全期 p95 | 近一年 p95 | 近一個月 p95 |
|---|---|---|---|
| 閒置 | 188.6 ms | 58.1 ms | 14.4 ms |
| 每秒 1,000（實際 916） | 183.5 ms | 62.8 ms | 15.4 ms |
| 每秒 3,000（實際 2,316） | 210.1 ms（+11%） | 67.5 ms（+16%） | 18.3 ms（+27%） |
| 不限速（實際 9,573） | 246.4 ms（+31%） | 84.0 ms（+45%） | 29.2 ms（+103%） |

和 8.2 對照：同一台 4 核心機器上，斷詞程式搶 CPU 的影響比 ES 寫入本身大。GCP 上斷詞跑在 Cloud Run、ES 在自己的 VM，影響會比這裡小，但仍要用 G3 量。

### 8.4 寫入的底層成本

`posts` 索引累計 index 操作 2,094,267 次（42.5 萬篇平均每篇被寫約 4.9 次：灌資料、v1 遷移、三輪回補、定向重跑），背景合併 257 次、合計 926 秒，結束時還有 34,582 筆已刪除文件等合併回收。

## 9. ES 與 PostgreSQL（`results/es/pg_vs_es.json`）

把 424,834 篇的 `terms_v2` 放進 PostgreSQL 16（`text[]` ＋ GIN，含索引 134 MB）。

| 範圍 | PG（`unnest` ＋ `GROUP BY`） | ES（`terms` 聚合 `took`） | 前 50 詞 |
|---|---|---|---|
| 全期 | 1,578.7 ms | 74 ms | 集合與篇數完全一樣 |
| 近一個月 | 70.6 ms | 11 ms | 完全一樣 |
| 含「颱風」的篇數 | GIN `@>` 5.7 ms、1,447 篇 | 1,447 篇 | 一樣 |

PG 也有倒排索引，找文章不慢；慢在把每篇的詞展開再分組計數。

## 10. 計數的定義與誤差（`results/es/aggs.json`）

- **`doc_count` 是篇數**：同一篇寫三次「颱風」，`doc_count` 仍是 1。
- **分片誤差**：預設 `shard_size` 時 `doc_count_error_upper_bound` 是 9,497；`shard_size: 500` 時降到 810。這個資料集前 10 名剛好都精確，但不能預設一定精確。
- **`query.filter` 和 `post_filter` 不同**：同樣限「近一個月」，放在 query 裡第一名是「什麼」480 篇；放在 `post_filter` 只過濾文章列表，聚合還是全期的「八卦」78,119 篇。
- **`_reindex` 不會重算斷詞**：把近一個月 6,903 篇 reindex 到新索引 0.77 秒，前 50 詞完全一樣，因為只是把已經算好的詞表複製過去。

## 11. 監控

| 層 | 收什麼 | 本機 | GCP 對應 |
|---|---|---|---|
| ES | 索引／搜尋速率、拒絕、JVM、合併 | OTel `elasticsearch` receiver → Prometheus | Ops Agent Elasticsearch 整合 → Cloud Monitoring |
| PostgreSQL | 連線、交易、表大小 | OTel `postgresql` receiver | Ops Agent／Cloud SQL 指標 |
| 文字雲 API | `wordcloud_duration_seconds{seg,cache,status}`（失敗也記）、`wordcloud_es_call_seconds`、`wordcloud_over_180s_total`、`wordcloud_errors_total{reason}`、快取與 in-flight；每次請求一行 JSON（`event=wordcloud_query`、`duration_ms`、`es_call_ms`、`seg_ver`、`request_id`）；PM 頁 RUM `wordcloud_web_*` | Prometheus ＋ stdout | Cloud Run 指標 ＋ 日誌型指標 `wordcloud_over_180s` |
| 斷詞服務／回補 | `qseg_messages_total`、`qseg_docs_total`、`qseg_e2e_lag_seconds`、`qseg_oldest_unacked_publish_ts`、`qseg_dict_info`；回補每批寫 `backfill_batches`、每分區寫帳本；`seg-recon` 每小時四層對帳（`recon_daily`、`qseg_recon_*`、燃盡 `qseg_backfill_*`、發版收斂 `qseg_service_*`）；`seg-quality` 每天品質日報（`quality_daily`、`qseg_quality_*`） | Prometheus ＋ PostgreSQL | 自訂指標 ＋ Cloud SQL |
| 採集器 | `collector_requests_total{feed,channel,outcome}`、首抓延遲 `collector_first_seen_lag_seconds`、退避、robots 雜湊；每次請求一行日誌 → Loki ruler 轉指標 | Prometheus ＋ Loki／Alloy | Cloud Monitoring ＋ Cloud Logging 日誌型指標 |
| ES 叢集 | elasticsearch_exporter：叢集狀態、磁碟水位、breaker、拒絕、JVM、SLM 快照 | Prometheus | Ops Agent |
| 告警 | `monitoring/rules/*.yml` 66 條：3 分鐘 SLO 燃燒率 `WordcloudSlo180sBurn`（Sloth 多視窗）、`WordcloudOver180s`、完整性、採集、積壓、發版收斂、回補燃盡、品質、ES、PM 頁；Alertmanager → `POST /api/ops/alert` → PM 頁橫幅（門檻都是提案值；對照表 `MONITORING.md`） | Prometheus rules ＋ Alertmanager | Cloud Monitoring 告警政策 |
| 自動降速 | G3 量探針、改 `backfill_control.max_docs_per_sec`，所有 task 每批重讀 | 回補程式 | 同一支程式，表在 Cloud SQL |

告警只通知人；實際讓回補慢下來的是回補程式自己讀限速表。

2026-11-02 在同一個容器把告警鏈實跑過：Prometheus 96 條規則（66 告警＋30 記錄）全部健康，告警經 Alertmanager webhook 進 PM 頁橫幅（firing 與 resolved 都收到），SLM 快照、Loki 日誌、品質日報與四層對帳的指標都來自這次實跑。端對端跑才抓到並修掉的 14 個問題、結束時仍在響的 4 組告警，列在 [`MONITORING.md`](MONITORING.md)「端對端驗證」。

## 12. 截圖

| 檔案 | 內容 |
|---|---|
| `pm/01_all_channels_all_time.png` | PM A/B 頁（2026-11-03 重拍，即時湖 6,666 篇）：全渠道全期，左 v0 舊系統重現、右 terms_v2，下面依序是差異清單與排名對照、資料工程端（字典 artifact、發版、新進資料）、剛流來的文章、回補帳本、事件流 |
| `pm/02_ptt_2026-10_pm_spot_check.png` | PTT 2026-10（首月抽查用） |
| `pm/03_keyword_kaohsiung.png` | 關鍵字「高雄」 |
| `pm/10_channel_{facebook,instagram,youtube,ptt,media,threads}.png` | 六渠道各一張 |
| `pm/20_posts_drawer_typhoon.png`、`21_posts_drawer_new_word.png` | 點詞看原文（「颱風」、新詞「肥宅」） |
| `pm/30_alerts_strip.png` | PM 頁頂部的告警橫幅：Alertmanager webhook 送進來的 firing 告警（嚴重度、摘要、時間） |
| `kibana/01_discover_v1_v2_terms.png` | Discover：同一篇的 `terms_v1`／`terms_v2`／`seg_ver_v2`／`seg_build_v2` |
| `kibana/02_discover_six_channels.png` | 六渠道文件 |
| `kibana/03_index_management.png` | 索引大小與文件數 |
| `grafana/01_dashboard_backfill_window.png` | 回補時段：查詢延遲、ES 寫入速率、回補吞吐、串流延遲放在同一條時間軸；限速表 `backfill_control` 沒有畫成面板 |
| `grafana/01_dashboard_main.png`、`02_dashboard_collect.png` | 2026-11-02 重拍：主看板 9 層 69 面板與採集看板 6 列 21 面板，時間窗是 Prometheus 重建後的 19 分鐘；「No data」的面板是這段時間沒發生的事（沒有回補、沒有發版） |
| `grafana/03_alert_rules_from_prometheus.png` | Grafana 讀到 Prometheus 與 Loki ruler 的 99 條規則（2 firing、4 pending、61 normal、32 recording） |
| `alertmanager/01_alerts.png` | Alertmanager：正在響的 ES 磁碟告警（整台容器磁碟 94%），receiver `pm-page` |
| `prometheus/01_alerts.png`、`02_stream_lag_p95.png`、`03_es_index_rate.png` | 告警規則（2026-11-02 重拍）、串流延遲 p95、ES 寫入速率 |
| `channels/*.png` | 五個渠道（FB、IG、YouTube、Threads、媒體）的原始公開頁（YouTube 影片頁被導到 reCAPTCHA，HTTP 429，沒有繞過）；PTT 的原文截圖在 `q6/evidence/playwright/`，這次沒有重拍 |

## 13. 對照知識檔（QSearch_ES_GCP_討論筆記.md）

| 節 | 筆記的說法 | 這裡怎麼驗證 | 結果 |
|---|---|---|---|
| §3.4 | `doc_count` 是篇數；分散式聚合可能有誤差 | `aggs.json` | 寫三次只算 1 篇；預設誤差上限 9,497，`shard_size` 500 降到 810 |
| §3.5 | 搜尋欄位和文字雲欄位可以分開 | `analyzers.json`、§6.2 | IK／bigram 留網址電話，`terms_v2` 不留，詞庫共用一份 |
| §4 | API 組查詢、ES 搜尋＋聚合、`bool.filter` 與 `post_filter` 不能混 | `app.py`、`aggs.json` | 兩者聚合結果差很多（480 vs 78,119） |
| §5.1 | 先處理完整網址再去標點 | `normalize.py`、§6.2 | v2 的網址、電話全部不進詞表 |
| §5.2 網址格式沒涵蓋 | 待驗證 | G2 抓到 | 「信箱是msn.com」：Python 的 `\w` 包含中文，lookbehind 讓規則失效，已修 |
| §5.2 HTML 不完整 | 待驗證 | `normalize.py` 先 `html.unescape` 再去標籤 | 六渠道 v2 前 30 詞的網址／程式碼類只剩 FB 1 個；但樣本裡 HTML 不多，這條驗得不深 |
| §5.2 程式用錯變數 | 待驗證 | G2 拿原文網址比對詞表 | 第一輪抓到的 46 個分區最後證明是檢查規則誤判，不是用錯變數 |
| §5.2 六渠道各自處理 | 待驗證 | 同一個映像，依渠道清洗 | 前 30 詞雜訊從 21 到 29 個降到 0 個（FB 剩 1 個「78th」） |
| §5.2 批次與即時版本不同 | 待驗證 | 同一映像、`seg_build` 寫進每篇、字典版本不符直接 exit 2、影子比對 | r3–r5 各 1,000 篇 0 不一致 |
| §5.2 舊資料未重算 | 待驗證 | IK 熱更新 | 只換字典不重建：片語搜尋 22,088 → 0 |
| §5.2 查錯欄位或快取 | 待驗證 | request cache | `_count` 回舊數字，改 `request_cache=false` |
| §6.2 | 一篇同時留新舊詞表，`seg_ver` 不是 ES `_version` | `guard.json` | 7 步全部符合，亂序 40 版取最大 |
| §6.3 | 讀原文 → 斷詞 → 寫入 → 過閘門，失敗重跑該批 | §2 | 三輪、checkpoint、被砍接續、只重跑沒過的分區 |
| §6.4 | 局部回補要能可靠找出受影響文章 | §3 | 地面真值：不含該字串 0 篇；ES 找到的 22,271 篇和全量算的一致 |
| §8／8.1 | PG 也有 GIN 倒排索引 | `pg_vs_es.json` | GIN 找文章 5.7 ms；分組計數全期 1,579 ms vs ES 74 ms |
| §9.3 | 更新也有底層成本 | §8.3、§8.4 | 平均每篇被寫 4.9 次、合併 926 秒；不限速寫入讓近一個月 p95 翻倍 |
| §12 | 分層監控 | §11 | ES、PG、API、斷詞、回補都有指標 |
| §12.1 | 回補拖慢查詢怎麼推查 | §8.2、Grafana 截圖 | 同一時間軸看到 p95 114 → 192 → 262 ms |
| §12.2 | 不能只看平均，要數超過 180 秒的筆數 | `wordcloud_over_180s_total`、探針 | 3,727 次 0 筆，最慢 396.9 ms |
| §13.2 | ES 指標靠 Ops Agent 的 ES 整合 | OTel `elasticsearch` receiver | Prometheus 有 ES 寫入速率等指標 |
| §13.3 | API 每次請求寫結構化 JSON | `app.py` | `duration_ms`、`es_call_ms`、`seg_ver`、`request_id` |
| §13.4 | 日誌型計數指標 `wordcloud_over_180s` | Counter ＋ 告警規則 | `WordcloudOver180s` |
| §13.5 | 回補自己回報進度、checkpoint 可靠保存 | `backfill_batches`、帳本 | 每 500 篇一筆；被砍後從 3,000 接續 |
| §13.6 | 指標放同一時間軸 | Grafana | `grafana/01_dashboard_backfill_window.png` |
| §13.7 | 告警和自動減速是兩件事 | G3 `canary_after` | 告警只通知；自動降速 3,000 → 1,500 → 750 |
| §15.3 | 數值要附抓取時間 | `data/channels/` | 每篇有 `fetched_at`；IG 未登入拿不到 `published_at`，2026-11-04 補上：43 篇取自登入爬取的 `taken_at`、5 篇由 shortcode 推算，逐列來源在 `_meta/instagram_published_at.json`（不是合成時間） |

## 14. 沙盤推演

前 9 種情境在這個環境都實際觸發過一次，第 10 種（GCP 部署）沒有在這裡觸發，引用的是 Terraform 結果；10 種情境整理在 [`../architecture/stack/diagram-sandbox-scenarios.png`](../architecture/stack/diagram-sandbox-scenarios.png)：

1. 新詞爆紅（加「肥宅」）→ §3、§7
2. 字典切錯被閘門擋 → §2.1 第 2、3 輪
3. 檢查規則本身錯 → §2.1 第 1 輪
4. 回補拖慢 PM 查詢 → §8.2、§2.2 G3
5. 回補 task 被砍 → §2.3
6. 亂序、重送、壞訊息 → §4、§5
7. 停用詞要加 → §6.3
8. 六種渠道格式不同 → §6.1
9. 版本號排序（2026.10.30.10 對上 2026.11.01.1）→ §2.4
10. GCP 部署不花錢 → `shared/infra/gcp` 做過 `terraform plan`（102 個資源）與 `terraform test`（30 項通過），涵蓋回補 Job、GCS、Pub/Sub 與 Cloud SQL；Elasticsearch 叢集、斷詞服務、文字雲 API 與監控告警還沒有 Terraform。這裡不 apply。

## 15. 限制

- 單節點、0 副本、4 核心同時跑 ES 與斷詞；絕對延遲只能當相對比較，GCP 上要重量。
- PTT 以外的五個渠道只有 4–61 篇，FB 未登入只拿到 4 篇；回補只有 PTT 一個渠道。
- 第 1–14 節用的 PTT 問答時間是合成的，只用來切月份分區；第 16 節起的原文湖只收原始發文時間（見 README「怎麼重跑」）。
- Pub/Sub 模擬器沒有配額、排序鍵與跨區行為；DLQ 與重送行為和正式環境一樣。
- 第 1–14 節的 PM 簽核與字典負責人豁免是用指令做的；第 16 節起 PM 頁有實際的簽核／豁免／發版按鈕，走同一段 SQL 與同一條 Pub/Sub 路。
- 舊流程 v1 是用 jieba 預設主字典算的對應元件（公司現行字典拿不到）；v1 詞表是用這個重現版算出來的，但不是公司現在線上的詞表。

## 16. 2026-10-31 晚：原文湖只收原始發文時間、PM 頁即時操作（Linux 容器端對端）

這一節是改版後在Linux 容器（4 核心、ES heap 2 GB）從零再跑一次的數字；操作人欄位一律是測試腳本 `e2e-test`，不是 PM。原始輸出在 [`results/live/e2e_2026-10-31.json`](results/live/e2e_2026-10-31.json)，頁面截圖在 [`../evidence/stack/pm_live/`](../evidence/stack/pm_live)。

| 步驟 | 系統做什麼 | 數字 |
|---|---|---|
| 原文湖 `prepare_raw.py --since 2023-01 --max-per-source 30000` | 只收有原始發文時間的文章 | 46,132 篇、26 個月份分區（2023-01～2026-10）。標題 6,260（略過 425 篇已刪除、沒有代碼）；Gossiping 100K 取前 30,000（同 id 重複 1,625）；PTT-pretrain 2023 年起 9,872（時間解析不出 19,845、檔頭格式不符 1,528、2023 以前 476,307 不收） |
| 灌資料 v1（3 個 task） | 舊流程寫 `terms_v1` | 每個 task 15,090～15,567 篇、17～20 秒 |
| 回補 r6 首月 `ptt/2026-10` | 跑完等 PM | 6,260 篇 2.6 秒（2,686 篇/秒）→ `awaiting_pm` |
| PM 頁「簽核通過」 | `backfill_ledger` → `passed`，簽核人、時間、備註寫進 `gates.G4`，事件流一筆 | 按下去 3 秒內頁面的狀態列從「等 PM 簽核 1」變「通過 1」 |
| `backfill-tasks`（3 個 task） | 其餘 25 個分區 | 約 1 分鐘；19 個通過、7 個 G2 沒過：5 個是網址殘片 `https` 各 1 篇、2 個是必不可拆詞被切各 1 次（新北／新北市／賴清德、好市多）。頁面對每個沒過的分區顯示「豁免 G2」（一定要寫說明） |
| `live` | seg-v1 載 r1、seg-v2 載 current（r6）、API、採集器 | 兩個服務的 `service_state` 心跳 15 秒一次，頁面顯示載入版本 |
| PM 頁「重播六渠道樣本」 | parser 寫本體 → `parsed-posts` → 兩個服務各自斷詞 | 267 篇送出，252 篇新、15 篇原本就在（採集器剛抓到的中央社新聞）；同版本的重送是 noop |
| 公開 feed 採集 7 個（YouTube 3、中央社 3、公視 1） | `collector.poll_feed`：抓 → 解析 → `_mget` 去重 → 寫本體 → 發訊息 | 11.6 秒抓完、123 篇新文章（跨 feed 重複 7 篇只收一次），最新一篇發文 2026-10-31 23:49（+08:00）；每篇從寫入到兩版詞表可查 1 秒（頁面「剛進來的文章」的延遲欄） |
| PM 頁發版加「沈伯洋、藍白」 | 建 `qsearch-q6-2026.11.01.1`（seg_ver 22026110101）、切 current、`rerun_runs` 一列、控制訊息 | 建版＋切 current 0.3 秒；seg-v2 收到後 1.4 秒載入新版並換掉服務用的字典；定向重跑候選 516 篇（原文含「沈伯洋」或「藍白」且 seg_ver 比新版舊）、516 篇全部重寫、詞表變了 140 篇、0.37 秒；完成後 2 秒內 A/B 頁自動重查並標出「沈伯洋 286→326」。重跑例子：沈伯洋助→沈伯洋、藍白合→藍白、沈伯／洋人→人設／沈伯洋 |

限制：PTT 的三個 feed 在Linux 容器不抓（robots.txt 規則），要在一般住宅網路的機器上才會動；Linux 容器的出口是攔 TLS 的代理，容器內的採集器對外 TLS 驗證失敗（頁面上看得到錯誤與退避時間），這一輪改由宿主機執行同一支 `collector.poll_feed`。舊流程 v1 仍是 jieba 預設主字典的對應元件（頁面上寫明）。單元測試 `tests/test_live_ops.py` 23 項。

## 17. 舊系統「維護多年的文字雲」重現 v0（在 4.6 萬篇真文上量到的五病徵）

第 6 題描述的是一套**維護多年、字典檔多年沒大改**的舊文字雲系統，症狀有五個：短網址後綴成詞、電話成詞、助詞沒過濾、地名變體被拆散、搜尋高頻詞反而不在文字雲。先前 PM 頁左欄的 `v1`（jieba 預設主字典、HMM 關）只是「沒有使用者字典」的對應元件，還不夠像「凍結多年的字典」。這一節把舊系統**重現**成 `v0`，和新流程 `v2` 並排，A/B 才站得住腳。

**v0 不是公司原始碼**，是依題目線索推論出來的對應元件（頁面與 `api/app.py` 的 `V0_NOTE` 都寫明）。組成（`dict/build_legacy.py` 建出 `dict/releases/qsearch-q6-legacy-2019.12.31.1.json`，每層記 sha256）：

| 層 | 來源 | 凍結日 | 收錄詞數 |
|---|---|---|---|
| `moe_revised_2015` | 教育部《重編國語辭典修訂本》2015（CC BY-ND 3.0 TW，`extract_moe.py` 抽「字詞名」欄） | 2015-12-31 | 148,714 |
| `jieba_tw_2016` | jieba-zh_TW 繁體詞表（MIT，只取第一欄的詞） | 2016-12-31 | +253,667 |
| `company_2019` | 依題目線索推論的「公司字典」對應元件（客戶、政治人物、地名、網路用語），凍結 2019-12-31 | 2019-12-31 | +16 |
| `places_old` | 舊行政區名（五都改制前、「臺」字寫法） | 2010-12-25 | +67 |
| **合計** | | **2019-12-31** | **402,464** |

斷詞是**逆向最長匹配（BMM）**、字典沒有的退回單字、英數字連續段落當一個 token 不切不小寫、沒有停用詞表、只丟標點（`seg/qseg/legacy.py`）。`seg_ver("v0", …) = 2019123101`（凍結日就是版本序號，比 v1／v2 小）。

把 v0 跑過原文湖的 **46,132 篇 PTT 文章**（`results/legacy/v0_symptoms.json`，用 `legacy.py` 直接算，不需要起 ES）：

- **助詞沒過濾**：`的` 14,433 篇、`了` 10,055、`有` 6,668、`是` 6,616、`我` 5,058、`在` 5,831 全進詞表；前 25 名幾乎只有助詞、網址殘片和單一數字（`的 了 有 是 嗎 https 在 com 問 我 就 一 卦 1 要 不 2 被 都 這 會 也 後 imgur 你`）。
- **短網址後綴成詞**：`https` 5,889 篇、`com` 5,391、`www` 1,763、`tw` 572、`cc` 523、`imgur` 進了前 25、`php`／`html`／`ly` 也在。
- **電話成詞**：2,333 個像電話／長數字的 token，例如 `0800076666`、`0919610214`，還有一堆貼文 ID 的長數字。
- **搜尋高頻詞不在文字雲（凍結在 2019 的代價）**：2019 後才紅的詞被拆成單字，整詞成立 0 篇——`沈伯洋` 原文 316 篇→v0 整詞 **0**、`藍白` 170→**0**、`大罷免` 15→0、`普發` 11→0、`新冠` 16→0；對照之下 2019 前就在字典裡的 `韓國瑜` 183→183、`蔣萬安` 271→269、`台積電` 171→168 都還是整詞。這正是「使用者搜尋量很大的詞，文字雲裡卻看不到」。
- **地名變體被拆散／沒合併**：v0 沒有變體合併層，`臺北市`(23)／`台北市`(147)、`臺灣`(138)／`台灣`(3,075) 各自分到不同 bucket；新流程 v2 在查詢時用 `merges.tsv` 把「臺→台」合併成一格。

新流程 v2 對同一批文章：渠道清洗把網址／電話拿掉、停用詞表擋掉助詞、使用者字典（固定 total）讓 `沈伯洋`／`藍白` 發版後成詞、查詢時合併變體——五個症狀逐一對上解法（見第 4、6 節與 §16 的發版實測：發版加「沈伯洋、藍白」後 0.37 秒定向重跑 516 篇，`沈伯洋` 286→326）。

**頁面與重跑**：`docker-compose.yml` 多一個 `seg-v0` 串流服務，`./run.sh build` 會建 v0 字典 artifact，`./run.sh seed` 另跑 3 個 task 寫 `terms_v0`；PM A/B 頁左欄可切「v0 舊系統重現／v1 jieba 預設」，右欄固定 v2，`/api/compare?a=v0&b=v2`。v0 的字典 artifact 與 v1／v2 同一套 sha256 驗證、同一個 `seg_ver` 守門機制，發版永遠不動 `current`（current 是 v2 的）。單元測試 `tests/test_legacy.py` 8 項（逆向最長匹配、五病徵、建版、xlsx 抽詞、seg_ver），全套 `tests/` 31 項通過。
