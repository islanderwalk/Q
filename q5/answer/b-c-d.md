# QSearch 第 5 題 (b)(c)(d)：工時、回補、SLA

本檔是 (b)(c)(d) 的完整版，交卷用的 600 字版在 `q5/answer/answer_600.txt`（599 字元，含換行）。回補程式在 `q5/code/backfill/`，工時重算程式在 `q5/code/schedule/`。(a) 資料流圖在 `q5/architecture/`，Threads 佐證截圖在 `q5/evidence/a-dataflow/`。

- 回補原型：`q5/code/backfill/backfill.py`（正規化、合併多次抓取、分區品質關卡、ES `_bulk` NDJSON、限速送出、續跑帳本）
- 示範資料：`q5/code/backfill/make_sample.py`（合成資料，附獨立算出的正確答案）
- 測試：`q5/code/backfill/tests/`（34 項）、`q5/code/schedule/tests/`（5 項），全部離線
- 示範腳本：`q5/code/backfill/demo_cli.sh`（命令列，含舊版對照）、`demo_send.py`（本機假 ES）
- 執行紀錄、指令與退出碼、ES 文件出處：`q5/code/RESULTS.md`

## (b) 工時與關鍵路徑

假設：1 位資料工程師（DE）＋1 位後端（BE）＋1 位前端（FE）平行；每日 8 小時；含自測，不含 PM 等待與 UAT。

| 層級 | 工作內容 | 負責 | 工時（h） | 依賴 |
|---|---|---|---|---|
| 1 | ES mapping 更新：新增 `text_post_app_info.repost_count／quote_count／reshare_count`、`video_view_count`，以及每欄的時間戳 `counts_at.<欄位>`（`date`，`format: epoch_millis`）；staging 驗證 | DE | 6 | 無 |
| 2 | 爬蟲寫入：讀 JSON 整數、null 保留、寫入新欄位；每次寫計數同時寫該欄的 `counts_at`＝抓取時間（epoch 毫秒），走和回補同一支腳本（(c) 的時間戳保護以此為前提），並加單元測試 | DE | 12 | 1 |
| 3 | 歷史回補：原型（已完成）＋ 欄位盤點 ＋ 分批執行與核對 | DE | 12 開發＋4 執行核對 | 1 |
| 4 | Data Layer API 新增欄位與契約 | BE | 8 | 1 的欄位名 |
| 5 | Backend Analytics API 回傳 | BE | 10 | 4 |
| 6 | Export Service 匯出欄位與指標定義 | BE | 6 | 5 |
| 7 | 前端顯示：欄位、null 顯示「—」、萬位格式（畫面是無條件捨去） | FE | 10 | 5 的 API 契約 |
| 8 | 端到端整合＋延遲 SLA 監控（指標與告警） | DE | 8 | 2、3、7 |
| 9 | 驗收：與 PM 確認「轉發數」與 IG 觀看數的定義（見 (c) 風險表最後兩列） | 全員 | 4 | 8 |

各角色合計：DE 42 小時（含執行核對 4 小時），BE 24 小時，FE 10 小時，全員驗收 4 小時。

純工時甘特圖（`python q5/code/schedule/schedule.py` 產生；每格 2 小時，`|` 是日界；未含 30% 審查修正與緩衝）：

```
                      D1   D2   D3   D4   D5   D6
DE  1 mapping         ■■■·|····|····|····|····|···  0-6h
DE  2 crawler         ···■|■■■■|■···|····|····|···  6-18h
DE  3 backfill        ····|····|·■■■|■■■■|■···|···  18-34h
BE  4 data-layer      ■■■■|····|····|····|····|···  0-8h
BE  5 analytics       ····|■■■■|■···|····|····|···  8-18h
BE  6 export          ····|····|·■■■|····|····|···  18-24h
FE  7 frontend        ····|■■■■|■···|····|····|···  8-18h
DE  8 integrate       ····|····|····|····|·■■■|■··  34-42h
ALL 9 accept          ····|····|····|····|····|·■■  42-46h
```

**關鍵路徑**：依序是 mapping（1）、爬蟲寫入（2）、回補（3）、端到端整合（8）、驗收（9），共 46 小時（DE 的 42 小時加全員驗收 4 小時）。因為只有一位 DE，這條路徑就是 DE 的整條工作線，它決定日程。

BE 與 FE 會不會拖慢上線，取決於依賴怎麼讀：

| 依賴讀法 | BE／FE 的浮時 | 結論 |
|---|---|---|
| 欄位名開工當天定案、FE 拿到 API 契約就開工（甘特圖的排法） | 4、5、7 各 16 小時，6 為 18 小時 | BE、FE 不在關鍵路徑上；API 契約最晚在第 3 天結束前定案即可 |
| 4 要等 mapping 做完、FE 要等 5 做完才開工 | 4、5、7 都是 0 | 1、4、5、7、8、9 這條也是 46 小時，變成第二條關鍵路徑，BE 或 FE 任何延誤都會延後上線 |

所以第 1 天就要把欄位名與 API 契約定下來，FE 先用 mock 資料做。

**日程**：關鍵路徑 46 小時 ÷ 8 = 5.75 工作天；加 30% 給審查與修正是 7.475，取 8 工作天；再加 1 天緩衝，**共 9 工作天**。週一或週二開工，中間跨一個週末，**約 11 個日曆天**；週三到週五開工會跨兩個週末，約 13 個日曆天。

## (c) 回補流程與風險

**流程**

1. 盤點：抽樣掃描 GCS 上 Threads 與 IG 歷史 JSON 的所有 key 組合，確認舊欄位名與檔案格式（原型讀 `.json` 與 `.jsonl`，GCS 實際格式待確認）。`quality.json` 會列出每個分區 post 物件兩層內的 key 與出現次數，可直接拿來比對。`normalize.py` 的別名表每組第一個名稱是實抓看到的（Threads `text_post_app_info.repost_count／quote_count／reshare_count`，IG Reels `play_count`），其餘（`repostCount`、`repost_cnt`、IG 舊檔的 `video_view_count` 等）都是待核對的假設。
2. Mapping：用 update mapping API 在現有 index 加新欄位與 `counts_at.<欄位>`。加新欄位不需要 reindex；只有改既有欄位的型別才要建新 index 再 reindex。
3. 切批：GCS 依平台分 prefix，再依週切，每批獨立產生 bulk 檔（預設 500 篇、5 MB 上限），檔案附 sha256；輸入檔的 sha256 也記在 manifest。
4. 合併：同一篇被抓多次時，基本欄位取最新一次；每個計數欄位取「有值」的最新一次，並記下那一次的抓取時間。JSON null 算有值（畫面不顯示數字也是觀測結果），key 不存在或型別錯算不知道，不拿來蓋掉較舊抓取的值。
5. 寫入（預設）：對線上 index 做部分更新（bulk `update`＋painless 腳本），只寫知道的計數欄位，不動其他欄位。腳本逐欄比較 `counts_at.<欄位>`：線上那一欄一樣新或更新就不動那一欄，一欄都沒寫就 noop。所以即使三個欄位來自不同次抓取，較舊的值也不會蓋掉爬蟲較新的寫入，重跑只會得到 noop。前提是爬蟲每次寫計數也寫同一欄的 `counts_at`（見 (b) 第 2 項與風險表）。ES 沒有這篇時回 404，記進 `send_errors.jsonl`，不建立殘缺文件。
6. 送出限速：`--rate-per-sec` 限制每秒幾個 bulk 請求（每個最多 500 篇，2 個請求/秒約 1,000 篇/秒），離峰執行。429 與 502／503／504 以隨機指數退避重送，只重送被拒的那幾筆。不改線上 index 的 `refresh_interval` 與副本數：那是大量初次載入新 index 用的調校，套在線上 index 會讓爬蟲新寫入的文件查不到，直接違反 1 小時 SLA。
7. 核對：`quality.json` 列出每種缺漏、別名、null、重複的筆數，以及每個分區每欄「不知道」的比例。以下任一情況程式停下來（退出碼 3），不產生 bulk 檔：壞行或丟棄比例超過 1%；有整檔壞掉；任一分區的任一計數欄位，不知道的比例超過 20%。最後一條擋的是欄位改名：改名的那一週接近 100%，攤到全體卻可能不到兩成。20% 依合成資料訂，盤點後依 GCS 上的資料調低。人工確認後才用參數放行。送出後抽樣 1% 讀回 ES 與 GCS 原文比對三個計數（null 必須是 null）。
8. 續跑：每送完一檔就把 sha256 記進 `sent.json`；中斷後重跑只送還沒送完的檔，有單筆失敗的檔整檔重送（已套用的會是 noop）。

另一種做法是建新 index（例如 `threads-posts-v2`）：先建好 mapping（reindex 不會複製 mapping 與設定），用 reindex 把舊 index 整份複製過去，再對新 index 套用同一套部分更新（`--index threads-posts-v2`），核對後用 alias 一次切換。它比較重（整份複製、暫時兩倍空間，期間的新寫入要補），只在 mapping 本來就要改型別時才划算。`--action index`（external version 整份寫入）只適用於回補能產生完整文件的情況；回補資料只有計數與少數欄位，直接整份寫入會蓋掉 ES 文件的其他欄位。

**風險與對策**

| 風險 | 對策 |
|---|---|
| 歷史欄位名不一致（`repost_count` 可能有 `repostCount`、`repost_cnt` 等舊版本，目前都是假設） | 盤點後補別名表；`alias_used:<欄位>=<舊名>` 統計每個舊名用了幾次，同一筆有兩個名稱且值不同記 `alias_conflict`。別名表沒有的新名稱會讓那個分區的欄位全部變成不知道，超過 20% 就停（退出碼 3），`quality.json` 的分區 key 清單看得到新名稱 |
| 原始資料缺漏（某次抓取少了 key 或整個容器） | 記為不知道，不蓋掉其他次抓取的值；全部抓取都缺時不寫入，前端顯示「—」，不補 0 |
| 把 null 寫成 0，把「萬」字串當數字 | 只接受 JSON 整數；字串標 `invalid_type`，當成不知道（有測試） |
| 回補覆蓋線上較新的資料 | 腳本逐欄比較 `counts_at.<欄位>`，較舊的值不寫入（三欄來自不同次抓取也一樣）；`--action index` 時用 external version，舊版寫入得到 409 |
| 爬蟲沒寫 `counts_at`（回補會蓋掉爬蟲的值），或寫成字串 | 列為 (b) 第 1、2 項的驗收條件：mapping 用 `date`＋`epoch_millis`，爬蟲走同一支腳本；時間戳不是數字時腳本丟錯（400、退出碼 4），不默默略過 |
| 回補期間的寫入負載影響線上查詢 | 限速、批次上限、離峰；不改線上 index 的 refresh 設定；監控 ES 查詢 P95 與 429 次數 |
| 同一篇多次抓取 | 批內先合併（每欄取有值的最新一次），再由腳本的時間戳保護兜底 |
| 壞檔、壞行 | 寫進 `rejects.jsonl`；比例超過門檻或有整檔壞掉就停，人工確認後才用參數放行 |
| 「轉發數」定義不一致（畫面＝`repost_count`＋`quote_count`，API 只回 `repost_count`） | 在 (b) 第 9 項先與 PM 確認；本原型只存兩個分量，畫面值在 Data Layer 計算 |
| IG 的 `play_count` 與題目說的 `video_view_count` 是否同一個定義 | 實抓只看到 `play_count`；ES 欄位沿用 `video_view_count`，定義上線前與 PM 確認。同一筆兩個 key 都有且值不同時當成不知道、不寫入，記 `alias_conflict` |

## (d) SLA「延遲 ≤ 1 小時」的瓶頸與監控

**最可能的瓶頸：爬蟲的抓取頻率，而不是 ES 寫入。** 文章在發布後 1 小時內，要等爬蟲第一次抓到它。若某帳號的輪詢間隔是 60 分鐘，最壞情況會剛好超過 SLA；寫入鏈路依序經過 GCS、Pub/Sub、parser、ES bulk 與 refresh，通常在秒到分鐘級。另一個風險是限速後的排隊時間（Pub/Sub 積壓），以及回補或其他批次作業把線上 index 的 `refresh_interval` 調大或關掉。

**需要定義清楚**：SLA 是「1 小時內可被查詢到」，不是「1 小時內數字已是最終值」。轉發數會持續增加，所以本題量的是首次可見的延遲。

**監控指標**

| 指標 | 計算 | 告警 |
|---|---|---|
| 首次可見延遲 | ES 寫入時間 − `published_at`（每篇一筆，記在文件裡） | P95 > 45 分鐘 警告；P95 > 60 分鐘 呼叫 |
| 首次抓取達成率 | 1 小時內被抓到的新文章數 ÷ 新文章總數 | < 95% 警告 |
| 爬蟲週期 | 每帳號的抓取週期與耗時 | 週期 > 30 分鐘 警告 |
| Pub/Sub 積壓 | 最舊未確認訊息的年齡 | > 10 分鐘 警告 |
| ES 寫入錯誤率 | bulk 單筆失敗比例（noop 與 index 寫法的 409 不算失敗） | > 0.1% 警告 |
| 線上 index 的 refresh 設定 | `index.refresh_interval` 是否被改成 -1 或大於 1 分鐘 | 任一即警告 |
| 金絲雀帳號 | 固定幾個帳號，每小時檢查新貼文是否在 60 分鐘內出現 | 任一失敗即警告 |

延遲的分佈用直方圖與 P50／P95／P99 呈現，並依平台與帳號群組切片。

## 執行的實測

- 單元測試：`python -m pytest -q -p no:cacheprovider q5/code/backfill/tests q5/code/schedule/tests`（39 項通過，退出碼 0）。
- 合成資料 2,000 篇、3,521 筆抓取的端到端示範（`demo_cli.sh`）：退出碼 0；重跑兩次所有輸出檔的 sha256 相同；寫入的 1,979 篇與獨立算出的正確答案完全一致。壞檔、壞行、一週欄位改名、參數錯誤分別以退出碼 3、3、3、2 停下。細節見 `q5/code/RESULTS.md`。
- 時間戳：線上 T2 已有轉發數 20、回補只有 T1 與 T3（T3 缺轉發數）時，上一版（整篇一個時間戳）把 20 蓋成 T1 的 10，本版保留 20。
- 送出端：painless 腳本在本機 Docker 的官方 Elasticsearch 8.19.23 與 9.5.5（單節點）實際執行過（`demo_real_es.sh`）：`dynamic: strict` 的舊 mapping 會擋（正式 index 是否 strict 未知）、update mapping 加欄位不需 reindex、首次送出與正確答案 0 筆不符、整批重送全部 noop、時間戳型別錯誤時丟錯不改文件。HTTP 429／500 與單筆 429 只在本機假 ES 上模擬；正式 ES 的版本與 mapping 未知，上線前要在 staging 再跑一次。
