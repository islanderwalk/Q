# QSearch 筆試第 5 題：新指標上線的全鏈路時間估算｜圖片素材

依小題分資料夾。`diagram-*` 是在 FigJam 畫的資料流圖，`threads-*` 是用 Playwright 打開 Threads 公開頁的截圖（紅框＝轉發、藍框＝分享，框線是截圖時加上的標註）。

> **題目：** 客戶提出需求：希望在平台上看到 Instagram Reels 的觀看數（video_view_count）和 Threads 貼文的轉發數（repost_count）。
>
> 經過調查發現：爬蟲端其實「已經有爬到這些欄位」，只是從未寫入 Elasticsearch；需要在以下層級全部新增支援：爬蟲寫入 → ES mapping 更新 → Data Layer API 新增欄位 → Backend Analytics API 回傳 → Export Service 匯出 → 前端顯示；產品端期望：指標延遲時間不超過 1 小時（即文章發佈 1 小時後，數據應可被查詢）；歷史資料方面：Instagram video_view_count 的資料起始日為 2026/5/1，Threads 的轉發數和分享數過去已有爬取但未寫入 ES。
>
> *上限 600 字。*

資料來源：
- 資料流圖：FigJam 原檔 https://www.figma.com/board/69PjSj1MiGUqCfWTWIkzUa
- Threads 截圖：2026/10/29 在雲端環境用 Playwright 拍攝，未登入、介面語言繁體中文；每張截圖都和同一次載入的頁面 JSON 對照，數字是拍攝當下的值
- 手機 App 的轉發選單：使用者手機截圖，只裁選單部分，不含發文者資訊

---

## (a) 資料流圖 — [`architecture/`](architecture)、佐證截圖 [`evidence/a-dataflow/`](evidence/a-dataflow)

> **題目：** 請畫出（或文字描述）這個需求從「爬蟲已有原始資料」到「使用者在前端看到數據」的完整資料流圖。標示出每一個需要修改的元件。

| 檔案 | 內容 |
|---|---|
| [`diagram-dataflow-vertical-final.png`](architecture/diagram-dataflow-vertical-final.png) | 最終版（直式）。方塊顏色＝要不要改：橘＝修改、綠＝新增、白＝不改；背景顏色＝誰負責：灰＝既有系統、藍＝資料工程師、紫＝後端、粉紅＝前端 |
| [`diagram-dataflow-with-threads-evidence.png`](architecture/diagram-dataflow-with-threads-evidence.png) | 最終版加上右側的 Threads 佐證區，虛線箭頭接到「Threads（轉發數）」 |
| [`diagram-dataflow-horizontal-draft.png`](architecture/diagram-dataflow-horizontal-draft.png) | 橫式草稿（太扁，手機上看不清楚，僅留存） |

「排程」和「原始資料 GCS」參考的是 Google Drive 上的 QSearch 架構圖（Cloud Scheduler、Cloud Storage），題目本身沒有提到。

### Threads 真實畫面佐證：「轉發數」到底是哪個數字

| 檔案 | 佐證什麼 |
|---|---|
| [`threads-evidence-panel.png`](evidence/a-dataflow/threads-evidence-panel.png) | FigJam 佐證區整張：下面四張的組合、JSON 片段、16 篇核對結論 |
| [`threads-profile-header-logged-out.png`](evidence/a-dataflow/threads-profile-header-logged-out.png) | Threads 官方帳號 @threads，未登入也看得到貼文和互動數字 |
| [`threads-repost-equals-repost-plus-quote.png`](evidence/a-dataflow/threads-repost-equals-repost-plus-quote.png) | 畫面轉發數 60 ＝ `repost_count` 35 ＋ `quote_count` 25，畫面上的數字不是 `repost_count` |
| [`threads-reshare-null-not-zero.png`](evidence/a-dataflow/threads-reshare-null-not-zero.png) | 分享旁邊沒有數字時，JSON 的 `reshare_count` 是 `null`，不是 0：每一層都要存空值、不能補 0 |
| [`threads-wan-truncated.png`](evidence/a-dataflow/threads-wan-truncated.png) | 讚顯示「1.2 萬」，JSON 是 12,716：畫面無條件捨去。爬蟲與 (c) 回補都要讀 JSON 整數，不抓畫面文字 |
| [`threads-profile-full-page.png`](evidence/a-dataflow/threads-profile-full-page.png) | @threads 個人頁整頁（第一次拍攝） |
| [`threads-use-media-demo-official.png`](evidence/a-dataflow/threads-use-media-demo-official.png) | Threads 官方的 Use media 示範文：圖片左上角標示原作者；JSON 用 `share_info.quoted_attachment_post` 指向原文；畫面轉發數 103 ＝ 76 ＋ 27 |
| [`threads-use-media-original.png`](evidence/a-dataflow/threads-use-media-original.png) | 被 Use media 的原文（@whitneymuseum）：畫面轉發數 11 ＝ 10 ＋ 1 |
| [`threads-app-repost-options-menu.png`](evidence/a-dataflow/threads-app-repost-options-menu.png) | 手機 App 的轉發選單：Quote／Markup／Use media／Repost only 都從同一顆 🔁 按出來，所以按鈕旁的數字會把引用一起算進去 |

核對結論：
- 畫面上的轉發數 ＝ `repost_count` ＋ `quote_count`：@threads 個人頁 16/16 篇，加上兩篇 Use media 相關貼文，共 18 篇全部符合。只看 `repost_count` 的話，16 篇裡只有 1 篇對得上（那篇引用數剛好是 0）。
- 分享數 ＝ `reshare_count`，`null` 時畫面不顯示數字：16/16 篇。
- 「萬」是無條件捨去：12,716 顯示 1.2 萬、74,572 顯示 7.4 萬。
- 欄位位置：`post.text_post_app_info.repost_count`，同一層還有 `quote_count`、`reshare_count`、`is_markup`、`share_info`。
- Markup 和 Use media 只有手機 App 有。Use media 已確認透過 `quoted_attachment_post` 掛住原文；兩者是否計入 `quote_count` 尚未實測。

對答案的影響：客戶要的「轉發數」是 API 的 `repost_count`，還是畫面上看到的數字（含引用）？兩者對不上，上線前要先跟 PM 確認。

## (b) 工時與關鍵路徑、(c) 回補、(d) SLA

交卷用的 600 字答案見 [`answer/answer_600.txt`](answer/answer_600.txt)（599 字元，含換行），完整版見 [`answer/b-c-d.md`](answer/b-c-d.md)。回補程式見 [`code/backfill/`](code/backfill)（pytest 34 passed），工時重算見 [`code/schedule/`](code/schedule)（pytest 5 passed），執行紀錄與 log 見 [`code/RESULTS.md`](code/RESULTS.md) 與 [`code/logs/`](code/logs)；回補的 painless 腳本在本機 Docker 的官方 Elasticsearch 8.19.23 與 9.5.5 上實際跑過（[`code/backfill/demo_real_es.sh`](code/backfill/demo_real_es.sh)，log 在 `code/logs/demo_real_es_*.log`）。目前沒有圖片素材。Q5 的 Playwright 實抓截圖（IG Reels 頁面、Threads 貼文）見下方「Playwright 實抓證據」一節。

重點：關鍵路徑是 DE 的整條工作線（mapping、爬蟲寫入、回補、整合）再接全員驗收，共 46 小時；加 30% 審查修正與 1 天緩衝共 9 工作天，週一開工約 11 個日曆天。回補預設對線上 index 做部分更新，每個計數欄位各帶一個時間戳 `counts_at.<欄位>`，較舊的值不會蓋掉較新的值（前提是爬蟲寫入也帶同一個時間戳），重跑結果相同。任一分區某個欄位讀不到的比例超過 20%（通常是欄位改名）就停下來，不產生 bulk 檔。

## Playwright 實抓證據 — [`q5/evidence/playwright/`](evidence/playwright)

用 Playwright 打開 IG 與 Threads 公開頁截的圖。由 `shared/capture/` 的腳本產生，每張的主張與結果見 `shared/capture/capture_manifest.json`。

| 檔案 | 佐證什麼 |
|---|---|
| [`ig_reels_grid.png`](evidence/playwright/ig_reels_grid.png) | 未登入的 Reels 分頁。縮圖上的觀看數在這張圖被裁切、看不到（2026-10-30 稽核更正）；欄位實際名稱是 `play_count`，數值見 [`ig_reel_fields.json`](evidence/playwright/ig_reel_fields.json) 與下方漂移圖 |
| [`ig_reels_grid_json_check.png`](evidence/playwright/ig_reels_grid_json_check.png) | 同一次載入的縮圖文字與頁面 JSON 對照（11:04 拍攝；DOM 文字 1.1M、4.2M、2.9M、2.4M，圖上看得到後三個）：縮圖是 JSON 的 `play_count` 無條件捨去，例如 4,298,087 顯示 4.2M |
| [`ig_reel_fields.json`](evidence/playwright/ig_reel_fields.json) | Reels 分頁內嵌 JSON 的 12 筆 `play_count`。JSON 裡的欄位名是 `play_count`，沒有看到題目說的 `video_view_count` 有數值；ES 欄位沿用題目名稱，來源 key 用 `play_count` |
| [`ig_reel_page.png`](evidence/playwright/ig_reel_page.png) | 單支 Reel 頁未登入時看不到觀看數文字，所以觀看數要從 Reels 分頁取 |
| [`threads_post_repost.png`](evidence/playwright/threads_post_repost.png) | Threads 貼文頁就有轉發數（轉發 16、讚 148、分享 1）。畫面「轉發 16」是 `repost_count`＋`quote_count`，不是單獨的 `repost_count`；一般使用者已遮 |
| [`threads_post_repost_drift.png`](evidence/playwright/threads_post_repost_drift.png) | 同一則貼文三次抓取：讚 148 → 266 → 288、轉發 16 → 22 → 22；同一張圖看得到畫面轉發 22 ＝ `repost_count` 18 ＋ `quote_count` 4 |
| [`ig_reels_play_count_drift.png`](evidence/playwright/ig_reels_play_count_drift.png) | 同一批 9 支 Reels 隔 4.6 小時再抓 `play_count`：最新一支 +29.3%，舊的 +0.02%～+0.56%（數值 [`ig_reels_play_count_drift.json`](evidence/playwright/ig_reels_play_count_drift.json)）；1 小時 SLA 下觀看數要定期重抓 |
| [`ig_reels_cloud_ip_login_redirect.png`](evidence/playwright/ig_reels_cloud_ip_login_redirect.png) | 雲端 IP 開 IG Reels：302 → 登入頁回 HTTP 429；Reels 爬蟲不能直接從 Cloud Run 出口抓 |

## 工具 — [`evidence/src/`](evidence/src)

| 檔案 | 用途 |
|---|---|
| [`threads_verify.js`](evidence/src/threads_verify.js) | 打開 Threads 個人頁，逐篇比對畫面數字與頁面 JSON，並拍下三篇代表貼文（產生上面的 `threads-repost-*`、`threads-reshare-*`、`threads-wan-*`） |
| [`threads_post_struct.js`](evidence/src/threads_post_struct.js) | 讀單篇貼文的 `is_markup`、`share_info`（轉發／引用了誰）和各種計數欄位 |

重跑（需要 Node.js 與 Playwright：`npm i playwright && npx playwright install chromium`）：

```
node q5/evidence/src/threads_verify.js https://www.threads.com/@threads <輸出資料夾>
node q5/evidence/src/threads_post_struct.js <輸出資料夾> https://www.threads.com/@threads/post/DDut1btS3ED
```
