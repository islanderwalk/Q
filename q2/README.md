# QSearch 筆試第 2 題：估算與選型（Postgres 貼文表解剖）

> **題目：** 有 100 萬則 Facebook 粉絲頁貼文，平均 250 字中文，不含留言，另含心情互動統計、發文時間、粉絲頁名稱、粉絲頁 ID 等欄位。
> 這是一個內部工具：約 10 位同事使用，每天查詢量約 500 次，資料每日增量約 3,000 則。由你一個人維運，每月基礎設施預算 3,000 元。
> 1. 請估算：(a) 原始資料總量；(b) 粉絲頁 ID 索引；(c) 貼文中文全文索引。寫出算式與你假設的每一個參數，數量級對即可。
> 2. 承上，你選哪一套方案？請說明是估算結果的哪一個數字決定了你的選擇。
> 3. 你選的中文斷詞策略是什麼？它會漏掉哪一類查詢？
> 4. 資料量或查詢量成長到多少，你會換掉現在的選擇？換成什麼？
> 5. 題目中缺了哪一個數字，會讓你的估算差距最大？
> *上限 600 字（不含算式）。*

---

## 正式答案 — [`answer/answer_600.txt`](answer/answer_600.txt)

交卷用的正式答案，585 字（上限 600 字，不含算式；python len，空白與換行都算），算式附在同一檔的「【算式】」段。字數由 `code/tests/test_q2_answer_length.py` 檢查。六題合併版見 [`shared/docs/submission.md`](../shared/docs/submission.md)。

---

## 資料來源與頁面

- 公開頁面：https://claude.ai/artifact/ESxb1xrXdAiT69ocVzbi43

| 檔案 | 內容 |
|---|---|
| [`fb_q2_posts_table.html`](answer/fb_q2_posts_table.html) | Q2 公開頁面的原始 HTML（下載後用瀏覽器開啟） |

## Playwright 實抓證據 — [`q2/evidence/playwright/`](evidence/playwright)

| 檔案 | 佐證什麼 |
|---|---|
| [`fb_page_nasa.png`](evidence/playwright/fb_page_nasa.png) | 粉專頁（未登入）：只有粉專名稱（NASA，在登入視窗標題）可讀，心情數與發文時間被登入視窗蓋住；量大時走 Graph API，不靠網頁 |
| [`fb_post_nasa.png`](evidence/playwright/fb_post_nasa.png) | 單則公開貼文（未登入）：粉專名稱可讀；圖上的心情 79／1／30／5 與「4h」是第一則留言的數字，不是貼文本身（2026-10-30 稽核更正）；一般留言者已遮 |
| [`fb_post_time_and_ids.png`](evidence/playwright/fb_post_time_and_ids.png) | 貼文層級：16K 心情、846 分享；畫面只有「14h」，頁面內嵌 `creation_time` 1793310300（2026-10-30 05:45 台北），發文時間可存絕對時間 |
| [`fb_page_transparency_id.png`](evidence/playwright/fb_page_transparency_id.png) | 粉專透明度頁的 Page ID 54971236771，和貼文頁出現的 profile ID 100044561550831 不同：「粉絲頁 ID」欄位要存 Page ID |
| [`fb_cloud_ip_login_redirect.png`](evidence/playwright/fb_cloud_ip_login_redirect.png) | 雲端 IP 開 FB 粉專：HTTP 302 → `/login/`，拿不到貼文欄位；100 萬則不能靠雲端爬網頁，要走 Graph API 或授權資料源 |

第一輪截圖由 `shared/capture/capture.py` 產生；第二輪（住宅 IP 與雲端 IP 對照）見 [`shared/docs/playwright-round2.md`](../shared/docs/playwright-round2.md)。主張、結果與更正見 `shared/capture/capture_manifest.json`。

## 程式與實測 — [`code/`](code)

| 檔案 | 內容 |
|---|---|
| [`run_q2.py`](code/run_q2.py) | 在 PostgreSQL 16 建表、造 10 萬或 100 萬列（重複內文／隨機內文／真實中文語料）、建四種索引並量體積與查詢時間，結果寫到 [`data/`](data)；`--pg-bigm` 另外建真的 pg_bigm 索引 |
| [`prepare_corpus.py`](code/prepare_corpus.py) | 把真實中文語料（PTT 八卦板問答、中文維基）切成每則 250 字，給 `--mode corpus` 用 |
| [`probe_simple_gin.py`](code/probe_simple_gin.py) | 追 `simple` 索引在 Windows 多 43% 的原因，並在 Linux 重現 Windows 的數字 |
| [`query_bench.py`](code/query_bench.py) | 用 2 到 4 字中文詞比較兩字詞索引、pg_trgm 與真的 pg_bigm 的結果數與查詢時間 |
| [`run_pg_bigm.sh`](code/run_pg_bigm.sh) | 用 repo 的 `shared/infra/onprem/postgres-bigm/Dockerfile` 在官方 PostgreSQL 16.15 映像上編 pg_bigm，起容器跑 `run_q2.py --pg-bigm`，跑完刪容器 |
| [`generic_plan_check.py`](code/generic_plan_check.py) | 查第一輪 pg_trgm 2 字詞 9.2 到 10 秒的原因：psycopg 自動 prepare 之後 PostgreSQL 改用 generic plan，計時與 EXPLAIN 看到的計畫不同 |
| [`sql/`](code/sql) | 建表、造資料、建索引、量測、查詢的 SQL；`06_pg_bigm.sql` 是真的 pg_bigm |
| [`q2_model.py`](code/q2_model.py) | 估算模型（題目 (a)(b)(c) 的算式），與頁面 `const D` 的實測數字對照 |
| [`tests/`](code/tests) | pytest 24 項（離線） |
| [`RESULTS.md`](code/RESULTS.md) | 實測結論：Linux 100 萬列與原頁一致（simple 0%）；Windows 的 +43% 已重現到位元組；真實中文的兩字詞索引 947 MB 到 1,165 MB，是原頁 406 MB 的 2.3 到 2.9 倍；2 字詞查詢 pg_trgm 退回全表掃描 |
