# QSearch 筆試第 3 題：PTT 全文搜尋系統｜圖片素材

依小題分資料夾。`chart-*.png` 是用實測資料畫的圖表（同名 `.html` 是可修改的原始檔），`ptt-*.png` 是用 Playwright 打開 PTT 實際頁面的截圖（黃色橫幅、編號、紅框是加上去的標註）。

> **題目：** 我們要做一個 PTT 八卦板全文搜尋系統：使用者輸入關鍵字，查找過去 7 天內所有包含該關鍵字的貼文，以及文章下的留言。請設計滿足此需求的 ETL Pipeline。
>
> 資料規格：文章即時性：新文章需在發布後 6 小時內納入搜尋範圍。留言新鮮度：包含關鍵字的文章，其留言在 7 天內每天至少更新一次。
>
> 工程約束：你一個人維運，兩週內上線。公司既有技術棧：GCP + Python。導入棧外的東西需要額外說明理由。PTT 是別人的網站，你沒有 API，且過度請求會被擋。
>
> *上限 800 字（不含架構圖與算式）。*

資料來源：
- `q3/data/ptt_q3_out/index_entries.csv`：2026/10/26 實爬八卦板列表頁，7 天視窗涵蓋 334 頁（index38880–39213）；檔案另含視窗起點前 1 頁（index38879），共 335 頁、6,685 列
- `q3/data/ptt_q3_census_out/articles.jsonl`：2026/10/27 打開 7 天內全部 6,237 篇文章普查
- PTT 截圖：2026/10/30 拍攝，頁面內容是拍攝當下的八卦板

---

## 正式答案 — [`answer/answer_800.txt`](answer/answer_800.txt)

交卷用的正式答案，785 字（上限 800 字，不含架構圖與算式；python len，空白與換行都算），算式附在同一檔的「【算式】」段。字數由 `code/tests/test_q3_answer_length.py` 檢查。六題合併版見 [`shared/docs/submission.md`](../shared/docs/submission.md)。

---

## 1. 規模估算 — [`q3/answer/1-scale/`](answer/1-scale)

> **題目：** 先估規模（文章數／留言數／文字量／每日抓取請求數），再開始設計。你的架構必須和你的估算數字一致。

| 檔案 | 佐證什麼 |
|---|---|
| [`chart-scale-table.png`](answer/1-scale/chart-scale-table.png) | 規模估算總表：每天 890 篇、7 天 23 萬則留言、純文字 22.9 MB、每天約 6,300 次請求 |
| [`chart-daily-posts.png`](answer/1-scale/chart-daily-posts.png) | 每天的發文量（744–1,109 篇），平均 890 篇怎麼來的 |
| [`chart-hourly-posts.png`](answer/1-scale/chart-hourly-posts.png) | 每小時發文量：尖峰 12 點每小時 64 篇，推出尖峰小時約 450 次請求 |
| [`ptt-list-page-20-slots.png`](answer/1-scale/ptt-list-page-20-slots.png) | 已填滿的列表頁固定 20 格，所以「頁數 × 20」就是文章數 |
| [`ptt-newest-page-pinned.png`](answer/1-scale/ptt-newest-page-pinned.png) | 最新頁還沒填滿，分隔線下的置底公告不算，爬蟲要跳過 |
| [`ptt-article-id-timestamp.png`](answer/1-scale/ptt-article-id-timestamp.png) | 文章網址裡的數字就是發文時間，和文章頁的「時間」欄一致 |
| [`ptt-comment-fields.png`](answer/1-scale/ptt-comment-fields.png) | 每則留言的欄位與實測大小：推/噓/→ 3.0 B、帳號 7.8 B、內容 33.7 B、時間含 IP 25.2 B ≈ 69.7 B |

## 2. 架構圖 — [`architecture/`](architecture)

> **題目：** 架構圖 + 每個環節的 infra 選擇與理由。不需寫程式碼，pseudo code 可。

| 檔案 | 內容 |
|---|---|
| [`architecture.png`](architecture/architecture.png) | Cloud Scheduler → Cloud Run Job「crawl」（discover＋refresh）→ Cloud Storage／BigQuery → Cloud Run Service「search」；每日「verify」檢查不變量 → Cloud Monitoring |
| [`architecture.html`](architecture/src/architecture.html) | 架構圖原始檔，改完用 `node q3/answer/src/capture.js charts` 重新產生 PNG |

## 3. 哪條規格主導成本 — [`q3/answer/3-cost/`](answer/3-cost)

> **題目：** 上述兩條資料規格，哪一條主導了你的成本？兩者相差多少倍？

| 檔案 | 佐證什麼 |
|---|---|
| [`chart-spec-cost.png`](answer/3-cost/chart-spec-cost.png) | 規格 1 每天 960 次、規格 2 每天 5,340 次，相差 5.6 倍 |
| [`chart-comment-arrival.png`](answer/3-cost/chart-comment-arrival.png) | 留言 92.5% 在 6 小時內、99.3% 在 24 小時內進來，規格 2 的成本集中在幾乎沒有新留言的時段 |

## 4. 不變量 — [`q3/answer/4-invariant/`](answer/4-invariant)

> **題目：** 你如何驗證抓取的資料沒有遺漏？請給出一個可自動檢查的不變量（invariant），而不只是「比對筆數」。

| 檔案 | 佐證什麼 |
|---|---|
| [`ptt-list-slots-deleted.png`](answer/4-invariant/ptt-list-slots-deleted.png) | 一頁 20 格＝文章＋刪文佔位，且文章 ID 的時間由上而下遞增 |
| [`ptt-nrec-vs-comments.png`](answer/4-invariant/ptt-nrec-vs-comments.png) | 列表推文數＝文章內「推 − 噓」（→ 不計），可當輔助檢查 |
| [`chart-invariant-results.png`](answer/4-invariant/chart-invariant-results.png) | 實測結果：334/334 頁剛好 20 格、0 筆倒序、推文數 97.2% 完全一致 |

## 5. 不監控的事 — [`q3/answer/5-not-monitored/`](answer/5-not-monitored)

> **題目：** 你決定不監控哪三件事？各自的風險為何可以接受？

| 檔案 | 佐證什麼 |
|---|---|
| [`chart-crawl-stats.png`](answer/5-not-monitored/chart-crawl-stats.png) | 6,237 篇最後狀態都是 200／404（0 篇失敗），另有約 10 篇推測是第一次請求失敗、退避 5 秒後重試成功；回應時間 P95 0.45 秒：單次請求的延遲與失敗不值得告警 |
| [`ptt-deleted-404.png`](answer/5-not-monitored/ptt-deleted-404.png) | 快照時還在、隔天變 404 的文章（7 天內 0.2%）：每天重抓時標記即可 |
| [`ptt-truncated-article.png`](answer/5-not-monitored/ptt-truncated-article.png) | 網頁版「檔案過大」截斷留言（6,224 篇中 2 篇）：已知且接受的風險 |

## 6. 規格修改 — [`q3/answer/6-spec-change/`](answer/6-spec-change)

> **題目：** 若你認為某項規格不合理或定義不清，可以提出修改，但必須說明代價。

| 檔案 | 佐證什麼 |
|---|---|
| [`chart-refresh-marginal.png`](answer/6-spec-change/chart-refresh-marginal.png) | 每次重抓都花 890 個請求，但第 2–6 次重抓合計只多抓到 0.76% 的留言 |

## 7. 動工前想問的一個問題 — [`q3/answer/7-question/`](answer/7-question)

> **題目：** 交卷時可附一個（只能一個）動工前想問的問題。

| 檔案 | 佐證什麼 |
|---|---|
| [`chart-text-share.png`](answer/7-question/chart-text-share.png) | 純文字 71% 是留言，所以「關鍵字要不要比對留言」讓要索引的文字量差 3.5 倍 |

---

## Playwright 實抓證據 — [`q3/evidence/playwright/`](evidence/playwright)

用 Playwright 打開 PTT 實頁截的圖（黃色橫幅、紅框是截圖時加上的標註）。由 `shared/capture/capture.py` 產生（`ptt_article_comments.png` 是 `shared/capture/capture_round2_cloud.js` 的 q3fix 模式重拍），每張的主張與結果見 `shared/capture/capture_manifest.json`。

| 檔案 | 佐證什麼 |
|---|---|
| [`ptt_over18_gate.png`](evidence/playwright/ptt_over18_gate.png) | 爬蟲要帶 over18=1 cookie，否則拿到的是確認頁；沒帶 cookie 時被導到 `ask/over18` |
| [`ptt_index_latest.png`](evidence/playwright/ptt_index_latest.png) | 列表頁結構固定：每列一篇、推文數、上頁頁碼、置底分隔線；爬蟲用頁碼往回翻，最新頁 index39391，置底線以上 15 篇 |
| [`ptt_index_older.png`](evidence/playwright/ptt_index_older.png) | 除了最新頁，每個列表頁固定 20 篇，可當自動檢查的不變量（index39386：20 篇） |
| [`ptt_live_invariants.png`](evidence/playwright/ptt_live_invariants.png) | 實測 10 頁：篇數不是 20 的頁有 0 頁；文章 ID 時間戳倒退 0 次（共 197 篇） |
| [`ptt_article_top.png`](evidence/playwright/ptt_article_top.png) | 文章網址帶發文時間（`M.{unix}.A.{hex}`），留言與內文在同一頁；這篇留言 203 則 |
| [`ptt_article_comments.png`](evidence/playwright/ptt_article_comments.png) | 每則留言都有推噓、帳號、內容、IP 與時間，parser 依這些欄位拆成資料列。這張是 10/30 20:29（+08）重拍、遮掉帳號與 IP 的版本，留言 261 則：推 150、噓 10、→ 101（同一篇在 01:51 拍 `ptt_article_top.png` 時是 203 則：推 108、噓 7、→ 88） |

---

## 程式與資料 — [`code/`](code)、[`data/`](data)

三支抓取程式的預設間隔都是 1.5 秒、不平行，`--max-requests` 可設請求上限；每次抓取都會在輸出資料夾加一行請求紀錄（`run_summary.jsonl`；`ptt_measure.py` 是 CSV 旁的 `<CSV 檔名>_summary.jsonl`），中斷或出錯也會寫；只產報告（`--report`）或防呆停下時沒有送請求，不寫。repo 內的資料是 10/26–10/27 抓的，當時還沒有這份紀錄。

| 程式 | 做什麼 | 產生 `data/` 的哪個檔 |
|---|---|---|
| [`ptt_measure.py`](code/ptt_measure.py) | 初步量測：連線（狀態碼、回應時間、大小）與看板列表，抽樣打開文章數推噓 | [`ptt_Gossiping.csv`](data/ptt_Gossiping.csv)：10/25–10/26 的 84 篇文章列表（預設寫到目前資料夾，用 `--out-dir` 指定；請求紀錄寫在 CSV 旁的 `ptt_Gossiping_summary.jsonl`） |
| [`ptt_q3_measure.py`](code/ptt_q3_measure.py) | 第 1 小題的規模量測：往回爬滿 7 天列表，依文章年齡分層抽樣 | [`ptt_q3_out/index_entries.csv`](data/ptt_q3_out/index_entries.csv)（6,685 列：334 個填滿的列表頁加最新頁）、[`ptt_q3_out/article_sample.json`](data/ptt_q3_out/article_sample.json)（210 篇抽樣）。印出的結果是 [`ptt_q3_result.txt`](data/ptt_q3_result.txt)（10/26 03:36 那次，545 個請求；第 40 行括號內的不符明細與第 53 行 bootstrap 信賴區間是事後手動補上的，程式不會印，第 54 行的路徑是搬資料夾時改的），進度在 [`ptt_q3_out.log`](data/ptt_q3_out.log)。一定要給 `--out`，要覆蓋 repo 內這份列表得另外加 `--overwrite-repo-data` |
| [`ptt_q3_census.py`](code/ptt_q3_census.py) | 普查：A 重開 210 篇抽樣、逐欄量留言 bytes；B 打開 7 天內全部 6,237 篇。讀上一列的列表，斷線後重跑會續抓 | [`ptt_q3_census_out/articles.jsonl`](data/ptt_q3_census_out/articles.jsonl)（每篇一行）、[`result_A.txt`](data/ptt_q3_census_out/result_A.txt)、[`result_B.txt`](data/ptt_q3_census_out/result_B.txt)；進度在 [`ptt_q3_census.log`](data/ptt_q3_census.log)。`--report` 只用已抓的資料重產兩份報告；換視窗卻沒給 `--out` 會停下 |
| [`ptt_q3_recheck.py`](code/ptt_q3_recheck.py) | 不連網，用上面的列表與普查重算 README 的數字（列表不變量、890 篇/天、短視窗不能外推、兩條規格的請求數） | [`runs/2026-10-30_recheck.json`](data/runs/2026-10-30_recheck.json)（加 `--out` 才寫檔，不加只印出來） |

- 測試：[`code/tests/test_ptt_smoke.py`](code/tests/test_ptt_smoke.py) 20 項，全部離線（用存下來的頁面，不連 PTT）。
- 驗證紀錄：[`code/RESULTS.md`](code/RESULTS.md) 是 10/30 在 Linux 重跑的指令、退出碼、重算結果與修正；逐項檢查在 [`data/runs/2026-10-30_checks.txt`](data/runs/2026-10-30_checks.txt)。PTT 的 robots.txt 對 這輪驗證環境的 user agent 寫 `Disallow: /`，所以這輪驗證沒有重新抓 PTT，只做離線重算與重播。另一個截圖流程（`shared/capture/capture_round2_cloud.js` 的 q3fix 模式）在 10/30 20:29（+08）載入同一篇文章頁 2 次，重拍了 `ptt_article_comments.png`（見 `shared/capture/capture_log.txt`）。
- 圖表：[`answer/src/make_charts.py`](answer/src/make_charts.py) 讀 `ptt_q3_out/index_entries.csv` 與 `ptt_q3_census_out/articles.jsonl` 畫各小題的 `chart-*.html`。

---

## 重新產生

在 repo 根目錄執行：

```bash
python q3/answer/src/make_charts.py      # 從實測資料產生 q3/answer/<小題>/chart-*.html 與截圖目標清單
node q3/answer/src/capture.js charts     # 各資料夾的 chart-*.html 與架構圖轉成同名 PNG（不連 PTT）
node q3/answer/src/capture.js ptt        # 打開 PTT 截圖（每次換頁間隔 1 秒，約 25 個請求）
```

PTT 截圖拍的是執行當下的頁面，所以文章、頁碼每次都會不同；被刪與被截斷的文章取自普查資料。
