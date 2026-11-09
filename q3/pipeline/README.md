# Q3 ETL pipeline：照答案的設計寫成可以直接跑的程式

答案第 2 題架構圖上的每個環節，這裡都有對應的程式。GCP 服務換成本機容器，SQL 與流程不變，執行不花錢：

| 答案的環節 | 這裡的實作 | 程式 |
|---|---|---|
| Cloud Scheduler `5 * * * *` | 常駐排程：每小時第 5 分跑一輪。一輪超過一小時就跑完接著跑下一輪，兩輪不重疊 | [`q3pipe/scheduler.py`](q3pipe/scheduler.py) |
| Cloud Run Job「crawl」（discover＋refresh），單 worker，每秒最多 1 次 | 讀列表找新文、抓新文、重抓 7 天內超過 23 小時沒抓的文章。帶 over18 cookie；429／5xx 退避 5、10 秒（有 Retry-After 就照它）；連續 3 次失敗，整體暫停 5 分鐘；閒置超過 60 秒的連線不再沿用；每次失敗的種類記進 log | [`q3pipe/job.py`](q3pipe/job.py)、[`q3pipe/ptt.py`](q3pipe/ptt.py) |
| 原始 HTML 存 GCS，備重解析 | 每次抓到的列表頁與文章頁都 gzip 存一份到 `gs://qsearch-q3-raw/<看板>/<種類>/<頁碼或文章 ID>/<抓取時間>.html.gz`，`posts.raw_uri` 指向它。用的是 fake-gcs-server | [`q3pipe/rawstore.py`](q3pipe/rawstore.py) |
| MERGE 進 BigQuery，依發文日分區 | `posts` 依 `DATE(published_at)` 分區，以文章 ID 叢集；每批 50 篇先寫進暫存表，再 MERGE。用的是 BigQuery 模擬器 | [`q3pipe/bq.py`](q3pipe/bq.py) |
| 失敗下輪補 | 見下方「自動回補」 | `job.py`、`bq.py` |
| 查詢 API 用 Cloud Run | `/api/search?q=關鍵字&days=7`，另附一個搜尋頁。貼文與留言分開查，再依文章合併；模擬器上 7 天全量（6,230 篇、22.7 萬則留言）搜尋一次 1–3 秒，見[全量預演](#全量預演) | [`q3pipe/search.py`](q3pipe/search.py) |
| 每日 verify 檢查不變量 | 台灣時間 04 點後的第一輪跑完後檢查：填滿的列表頁恰好 20 格，每格是刪文佔位或已入庫，相鄰文章 ID 的時間戳不倒序 | [`q3pipe/verify.py`](q3pipe/verify.py) |
| Cloud Monitoring | Pushgateway＋Prometheus＋Grafana。告警只看結果（答案第 5 題） | [`monitoring/`](monitoring) |

## 資料表（dataset `ptt`）

| 表 | 一列是什麼 | 用途 |
|---|---|---|
| `posts` | 一篇文章。留言放在巢狀欄位 `comments`（REPEATED RECORD），每則留言有樓層、推噓、帳號、內容、IP、時間，以及第一次看到的時間；另存一份留言純文字 `comments_text`（每則一行） | 搜尋。文章和它的留言一起寫入、一起還原，不會有「文章是新的、留言是舊的」這種半套狀態 |
| `list_slots` | 列表頁的一格：頁碼、第幾格、文章 ID 或刪文佔位、推文數 | 不變量檢查；判斷哪些文章還沒入庫 |
| `post_versions` | 每批寫入前，被碰到的文章原本的樣子（before-image） | 還原；也是稽核紀錄 |
| `batch_log` | 每批的結局：committed 或 rolled_back | 找出中途當掉、沒有結局的批次 |
| `crawl_runs`、`verify_runs` | 每一輪抓取、每一次不變量檢查的結果 | 看板、下一輪要不要先試探 |

留言用巢狀欄位，不另開一張表，原因是 BigQuery 建議把一對多的資料放在 nested／repeated 欄位裡，省掉 JOIN。搜尋留言分兩步：先在 `comments_text` 找出留言命中的最新 50 篇，再只展開這 50 篇的 `comments`，取出命中的那幾則。這樣不必把 7 天的留言逐則展開，也不會每篇命中的文章都展開一次（模擬器上 7 天有 1,070 篇的留言提到「颱風」，寫成一個查詢要 3.3 秒，分兩步約 1 秒）。換行把每則留言隔開，關鍵字不會跨兩則命中。分區保留 30 天，搜尋只查 7 天內。

## 自動回補

停機、單篇失敗、整批被還原，事後都不需要人工補資料，下一輪會自己補齊：

1. **列表**：從最新頁往回讀，讀到上一輪的最新頁為止。第一次跑則讀到 7 天前。所以停機多久都一樣，中間發的文章都會讀到。讀到一半某頁失敗，就停在那頁，這輪記為 `partial`，下一輪補讀。
2. **新文**：每輪都算一次「列表上有、`posts` 沒有」的 7 天內文章，全部補抓，舊的先抓。單篇失敗不寫入，下一輪它還在清單上。
3. **重抓**：7 天內 `last_fetched_at` 超過 23 小時的文章就到期。重抓失敗不更新 `last_fetched_at`，下一輪會再到期。
4. **刪文**：列表上已經是「本文已被刪除」的格子不抓。之前抓過、重抓時拿到 404 的文章標記 `deleted_at`，舊內容與留言保留，但不出現在搜尋結果。
5. **新留言**：重抓時整串留言換成最新版本。每則留言用（帳號、時間與 IP、內容、同一分鐘同一句話的第幾次）識別，所以舊留言保留第一次看到的時間，也算得出這次新增了幾則。看板第 5 區的「重抓的邊際效益」就是用這個數字畫的。

## 自動 rollback

分三層，前兩層每批都做：

1. **寫入前的品質檢查**（每批 50 篇）：解析失敗超過 5%、內文與留言都空白超過 20%、404 超過 20%，或重抓後留言數縮水的文章超過 10%，這批就不寫，這輪停下，記為 `gate_blocked`。原始 HTML 已經存進 GCS，修好 parser 可以重新解析。下一輪先用 20 篇試探，通過才恢復正常量。這一層擋的是網站改版、被擋時拿到的空白頁或錯誤頁。
2. **每批的寫入協定**：
   1. 這批寫進暫存表；
   2. 把被碰到的文章原本的樣子存進 `post_versions`；
   3. MERGE 進 `posts`；
   4. 核對這批是否全部落地；
   5. 在 `batch_log` 記 `committed`。

   任何一步失敗，就照 before-image 把這批還原（新文刪掉，舊文換回原本的內容與留言），記 `rolled_back`，下一輪補抓。連續兩批都寫不進去（例如資料庫斷線），這輪就先停，不再繼續抓了又寫不進去。如果程序在中途被殺，那一批就沒有結局；下一輪開始前會先找出「有 before-image、沒有結局」的批次，把它們還原。
3. **整輪的回歸檢查**：這輪結束後，和這輪開始前比（7 天的起點相同）。出現以下任一種情況，就照批次倒序還原整輪，記 `rolled_back`，資料回到這輪開始前的樣子：
   - 可搜尋文章少了 5% 以上；
   - 空白文章多了 max(20 篇, 這輪處理量的 2%) 以上；
   - 刪文多了 max(30 篇, 這輪處理量的 10%) 以上；
   - 留言總數少了 5% 以上。

   這一層擋的是每批看起來都還好、合起來卻不對的情況，例如一部分文章被擋成 404，但列表沒變。

還有手動指令：`python -m q3pipe rollback <run_id>`。預設只能還原最近一輪，因為更早的輪次之後可能被重抓過。

為什麼不用 BigQuery 的 time travel：time travel 是把整張表倒回某個時間點，同一時間其他批次的寫入也會一起被倒回去。這裡需要只還原某一批或某一輪，所以每批自己存 before-image。這也不依賴多語句交易，因為本機模擬器的交易不是原子的（實測中途出錯時，前半段會留著）。

## 執行

需要 Docker。在這個資料夾執行：

```bash
docker compose up -d --build
```

| 服務 | 位址（只綁 127.0.0.1） |
|---|---|
| 搜尋頁／查詢 API | http://127.0.0.1:8300 、 http://127.0.0.1:8300/api/search?q=颱風 |
| Grafana（匿名唯讀，首頁就是 Q3 看板） | http://127.0.0.1:3300 |
| Prometheus（含告警規則） | http://127.0.0.1:9390 |
| Pushgateway | http://127.0.0.1:9391 |
| BigQuery 模擬器、GCS 模擬器 | 9050、4443 |

第一輪要補 7 天，約 6,300 篇。以每秒 1 次計算，大約 2 小時；之後每小時第 5 分跑一輪。資料存在 named volume 裡，重開機、重建容器都不會掉。

```bash
docker compose logs -f crawler                       # 每一批的進度（JSON 一行一筆）
docker compose exec crawler python -m q3pipe status  # 最近幾輪、7 天內統計、上次檢查
docker compose exec crawler python -m q3pipe search 颱風
docker compose exec crawler python -m q3pipe verify  # 立刻跑一次不變量檢查
docker compose exec crawler python -m q3pipe rollback <run_id>
```

同一時間只會有一輪在寫：常駐排程、手動的 `run`、`rollback` 都要先拿同一把鎖，另一輪還沒跑完就等它，對應答案裡 Cloud Run Job 的 parallelism 1。手動的 `run`、`verify` 不推指標，因為 Pushgateway 會整組覆蓋同名指標，和常駐程序的計數器互蓋會讓看板跳動；看板以常駐程序為準。

**故障演練**：兩層自動 rollback 各有一個開關，在看板上看還原與下一輪補抓。

```bash
Q3_DRILL_FAIL_BATCHES=1 docker compose up -d crawler   # 單批還原：重建 crawler，啟動後立刻跑的那一輪第 1 批寫入後出錯、被還原
docker compose up -d crawler                           # 關掉演練；重建後立刻跑一輪，把被還原的那批補回來
Q3_DRILL_REGRESSION=1 docker compose up -d crawler     # 整輪還原：啟動後那一輪結束時當成退步，這輪寫入的批次全部還原（只作用一輪）
docker compose up -d crawler                           # 關掉演練；下一輪先用一小批試探，正常就恢復
```

看板上：單批還原是「每小時寫入批次」的 rolled_back 長條；整輪還原是「過去 24 小時整輪還原」加 1、「上一輪結果」變成 rolled_back，下一輪恢復成 ok。帶標籤的計數器在啟動時先建成 0，第一次還原才算得進 `increase()`。

設定全部從環境變數 `Q3_<欄位名大寫>` 讀取，預設值就是答案的規格，見 [`q3pipe/config.py`](q3pipe/config.py)。例如 `Q3_REFRESH_POLICY=once24h` 會改成第 6 題建議的「發文滿 24 小時再抓一次」。`Q3_BQ_ENDPOINT` 留空就改連 GCP 上的 BigQuery；這時暫存表改用載入工作（WRITE_TRUNCATE），其餘 SQL 相同。這個分支沒有實際在 GCP 上執行過。

### 為了 BigQuery 模擬器做的調整

模擬器是 [goccy/bigquery-emulator](https://github.com/goccy/bigquery-emulator) 0.8.1，SQL 與 GCP 上的 BigQuery 相同，但有幾個限制，所以程式選了兩邊都能跑的寫法：

- 它的 SQL 解析器跑在 WASM 裡，記憶體要等 Go 的 GC 才會釋放。用預設值，實測幾分鐘就長到 2 GB 然後當掉；compose 裡設了 `GOGC=20`、`GOMEMLIMIT=600MiB`，不會再一路長到當掉，但寫入越多佔得越多：7 天回補完約 0.86 GB，24 小時重抓後約 0.97 GB，這時查詢比剛啟動時慢 2–3 倍。重啟模擬器就降回 0.1 GB、速度恢復，資料存在 volume 裡不會掉（`docker compose restart bigquery`）。
- MERGE 的 INSERT／UPDATE 裡，查詢參數不會被代入，所以 `run_id`、`batch_no` 放在暫存表的欄位裡。它也不支援 `MERGE … USING (子查詢)`，所以還原改用 DELETE＋INSERT SELECT。
- 它的底層是沒有索引的 SQLite，兩張大表 JOIN 或長的 IN 清單都很慢（實測 7,120 篇的留言展開後再用 500 個文章 ID 篩，要 131 秒）。所以「列表有、還沒入庫」這類差集改在程式裡算，SQL 只做單表掃描。在 GCP 上的 BigQuery 也是一樣的單表掃描。
- 它的加欄位只改得到表的描述，SQL 看不到新欄位。所以程式改版加了欄位時，模擬器要清掉資料重建（`docker compose down -v`）；GCP 上的 BigQuery 會在啟動時直接加上。
- 它刪一張表要十幾秒，所以暫存表每種只建一張，重複使用（先清空再寫入）。
- 它的 `SUBSTR` 以位元組計，會把中文字切成半個；搜尋結果的摘要改用 `LEFT`，兩邊都以字元計。
- 傳了 SQL 裡沒用到的查詢參數，它會直接回錯誤，所以每個查詢只傳自己用到的參數。
- 它會把每個查詢的工作紀錄連同結果存起來，用 `jobs.insert` 送的 SELECT 還會各多建一個存結果的 dataset；之後每個請求都先把這些全部讀出來。實測跑過幾千個查詢後，連 `SELECT 1` 都要 10 秒以上。所以讀取改走 `jobs.query`（不建結果 dataset），寫入仍走 `jobs.insert`（工作 ID 由用戶端產生，連線中斷重送不會重複寫入），查完記下掃描量就刪掉工作紀錄；crawler 啟動時也會清掉之前留下的。改完後端到端測試 6 個情境從 50 分鐘以上縮到 1 分半。
- 單次串流寫入超過約 3.5 MB 會解析失敗（留言多的熱門文，一批幾篇就會碰到），所以每次最多送約 2 MB；GCP 上的 BigQuery 上限是 10 MB。

## 測試

```bash
pip install -r requirements.txt pytest
docker compose up -d bigquery      # 端到端測試要用 BigQuery 模擬器；沒有就跳過
pytest -q tests/
```

| 測試 | 內容 |
|---|---|
| [`test_parse_real.py`](tests/test_parse_real.py) | 用存下來的 PTT 頁面（[`tests/fixtures/real/`](tests/fixtures/real)）測解析：最新頁的置底分隔線、填滿頁 20 格含刪文佔位、文章的推／噓／→ 與推文數、404、over18 確認頁、跨年的留言時間、退避與暫停、閒置過久的連線重連、每次失敗記進 log |
| [`test_rules.py`](tests/test_rules.py) | 品質檢查、整輪回歸檢查、推文數規則、不變量的各種失敗、排程時間、單一 worker 的鎖、故障演練設定、搜尋結果合併 |
| [`test_pipeline_emulator.py`](tests/test_pipeline_emulator.py) | 一個模擬的 PTT 八卦板（[`tests/fakeptt.py`](tests/fakeptt.py)，HTML 結構照存下來的頁面產生，時間由測試控制）接 BigQuery 模擬器，跑下面 8 個情境 |

| 情境 | 驗證什麼 |
|---|---|
| 第一次跑 → 1 小時後 → 24 小時後 | 7 天內的文章全部入庫，刪文佔位不抓；第二輪只讀最新幾頁、只抓 5 篇新文；重抓後新留言進來，舊留言第一次看到的時間不變；刪文標 404、保留內容，搜尋不出現；每次不變量檢查都通過 |
| 程序重啟 | 指標從 0 開始，但上一輪結果、上次不變量檢查、7 天內的數字先從 BigQuery 補回，看板不會歸零到下一輪跑完 |
| 停機 30 小時 | 下一輪從最新頁往回讀到上次的位置，期間的 70 篇全部補上 |
| 某一批 MERGE 之後出錯 | 那批整批不在（沒有半套），下一輪自動補抓；重抓時某批出錯，那批文章回到寫入前的樣子（含留言） |
| 程序在某批寫完、記錄結局前被殺 | 下一輪開始前先還原那批，再補抓，最後資料完整 |
| 網站改版，文章頁找不到內文區塊 | 整批不寫，這輪停下；恢復後先試探 20 篇，再恢復正常 |
| 重抓時三分之一回 404，但列表沒變 | 每批都過得了品質檢查，但整輪回歸檢查抓到刪文暴增，整輪還原到開始前（文章、留言、抓取時間都一樣）；下一輪試探後恢復 |
| 整輪還原演練（`Q3_DRILL_REGRESSION=1`） | 那一輪結束時當成退步，這輪新增的文章全部撤回；演練只作用一輪，下一輪試探後補回同樣幾篇 |

### 全量預演

[`tests/full_scale.py`](tests/full_scale.py) 用模擬八卦板放 8 天、每天 890 篇、每篇平均 37 則留言（答案第 1 題 POC 實測的量），照排程的順序跑一遍。抓取不限速，量到的是寫入、MERGE、還原紀錄、不變量與搜尋在模擬器上花的時間：

```bash
docker compose up -d bigquery
python tests/full_scale.py
```

| 步驟 | 結果 | 時間 |
|---|---|---|
| 7 天回補 | 讀 312 頁列表，6,230 篇、22.7 萬則留言分 125 批寫入，沒有一批被還原 | 461 秒 |
| 不變量 | 311 頁都是 20 格，沒有缺漏，時間不倒序；6,210 篇的推文數全部吻合 | 1 秒 |
| 搜尋「颱風」 | 貼文命中 50 篇、留言命中 50 則 | 1.1 秒 |
| 1 小時後那輪 | 只讀最新 3 頁，新文 30 篇 | 10 秒 |
| 24 小時後重抓 | 5,340 篇分 107 批重抓寫入，沒有一批被還原 | 793 秒 |
| 重抓後不變量 | 268 頁全部通過 | 2 秒 |
| 重抓後搜尋 | 同上。這時模擬器佔約 0.97 GB，查詢比較慢（見上面模擬器的說明） | 2.6 秒 |

## Grafana 看板

[`monitoring/build_dashboard.py`](monitoring/build_dashboard.py) 產生 [`q3-ptt-search.json`](monitoring/grafana/dashboards/q3-ptt-search.json)，共 7 區：

1. **兩條資料規格與結果**：新文章最久等了幾分鐘還不能搜尋（規格 1：360 分鐘）、留言最久多少小時沒重抓（規格 2：24 小時）、每日不變量、上一輪結果。只有這一區會告警（[`alerts.yml`](monitoring/alerts.yml)）。
2. **資料量**：7 天內可搜尋文章、留言、已標記刪文，以及待抓佇列。
3. **抓取**：每分鐘請求數與每秒 1 次的上限線、PTT 回應時間、24 小時請求數對照答案估算的 6,300 次、退避等待。
4. **寫入與自動 rollback**：每小時 committed／rolled_back／gate_blocked 批次數、整輪還原次數、發文到可搜尋的延遲。
5. **重抓的邊際效益**（答案第 3、6 題）：新增留言依文章發文第幾天分開統計。
6. **只記錄、不告警**（答案第 5 題）：單次請求失敗、事後刪文 404、「檔案過大」被截斷的文章。
7. **每日不變量明細與查詢 API**：每項檢查的數字、推文數吻合比例、查詢延遲、BigQuery 掃描量。

指標由 crawler 推到 Pushgateway。7 天內的數字在每輪開始、列表讀完、每寫 10 批、每輪結束時更新，第一次回補跑幾個小時也看得到進度；crawler 重啟時先從 BigQuery 的 `crawl_runs`、`verify_runs` 補回上一輪與上次檢查的結果。還沒有資料的面板直接寫原因（例如「還沒有重抓：文章抓到滿 23 小時才重抓」），不顯示 No data。

截圖用 Playwright（`pip install playwright`、`playwright install chromium`）：

```bash
python monitoring/capture.py                                   # 看板全頁、每一格、搜尋頁、查詢 API、告警規則，存到 q3/evidence/grafana/
python monitoring/capture.py --prefix drill- --range now-3h    # 故障演練後再拍一次
```

在本機常駐運行時拍的四組截圖（常駐運行、第一次回補、兩層故障演練、模擬器重啟後的搜尋），以及每一張怎麼看，見 [`q3/evidence/grafana/README.md`](../evidence/grafana/README.md)。
