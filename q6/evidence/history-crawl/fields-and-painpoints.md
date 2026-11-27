# 六渠道歷史爬取：欄位與痛點

目的：把 Q6「2023-01 起的歷史資料」實際抓下來時，每個渠道拿得到哪些欄位、卡在哪裡、對分群有什麼影響，整理成一份可以直接拿來討論的清單。

- 標示規則：**[實測]** 是本次執行的數字或截圖，**[推測]** 是依數字的解讀、尚未直接驗證。
- 數字來源：[telemetry_summary.json](telemetry_summary.json)（2026-11-02 18:58 ~ 2026-11-03 05:29 +08，約 10.5 小時）與各渠道的 progress 快照。
- 程式：[../../code/history-crawl/](../../code/history-crawl/)（PTT、YouTube、FB／IG／Threads 深度爬取）與 [../../code/history-crawl/round1_public/](../../code/history-crawl/round1_public/)（未登入第一輪）。

## 1. 進度總覽 [實測]

| 渠道 | 目標 | 目前筆數 | 最早日期 | 狀態 |
|---|---|---|---|---|
| PTT | 9 個看板（Gossiping、Kaohsiung、Tainan、Stock、Baseball、MobileComm、Tech_Job、WomenTalk、Boy-Girl） | 每板約 7.4k 列（列表階段） | Tech_Job 2024-03-07、Boy-Girl 2024-05-18 | 列表往回翻中，尚未進入內文階段 |
| YouTube | TVBS NEWS、公視新聞網、東森新聞 等 7 頻道 | TVBS 19,958、公視 20,000、東森 8,550 | TVBS 2026-07-11、公視 2025-08-09、東森 2026-09-10 | TVBS／公視清單已到底；東森排隊中；當日配額用 903 units |
| Facebook | cnanewstaiwan | 4,265 篇／1,432 頁 | 2026-07-01 | 連續 10 頁沒有新貼文而停止 |
| Instagram | ftvnews | 12,951 篇／1,091 頁 | 2025-08-21 | 同上 |
| Threads | pts.news | 2,119 篇／115 頁 | 2026-07-25 | 同上 |
| FB PNNPTS、NASA，IG nasa，Threads ftvnews | — | 0 | — | 排隊中，尚未輪到 |

十小時內各平台的請求統計（api_trace）：

| 平台 | 請求數 | 200 | 無狀態（斷線） | 其他 | p50／p95 延遲 | 最高退避等級 |
|---|---|---|---|---|---|---|
| Facebook | 4,354 | 2,875 | 1,478（其中 1,463 為 ERR_INTERNET_DISCONNECTED） | — | 172.5／2,743.2 ms | 14 |
| Instagram | 4,306 | 3,132 | 1,174 | — | 268.2／1,675.4 ms | 10 |
| Threads | 6,914 | 6,213 | 698 | 404×2、502×1 | 322.1／5,560.4 ms | 24 |
| PTT | 3,392 | 3,367 | 22 | 525×3 | 334.7／688.9 ms | — |
| YouTube | 974 | 959 | 15 | — | 400.6／816 ms | — |

所有平台的 delay 上限都觸到 600 秒；每個 metrics 視窗的 session 狀態都是 ok，沒有登出、驗證挑戰或帳號鎖定。

## 2. 欄位

共同欄位（與 Q6 的 posts 表對齊）：`post_id`、`channel`、`sub_type`、`source`、`url`、`published_at`（+08:00）、`fetched_at`、`text`、`collect_method`。

### 2.1 PTT [實測]

| 階段 | 欄位 | 說明／陷阱 |
|---|---|---|
| 列表 | board、url、title、author、date_mmdd、published_at、push、fetched_at | `published_at` 由網址中的 `M.<epoch>.A` 換算，不靠列表上只有月／日的 `date_mmdd`；置底公告（分隔線之後）不收。 |
| 內文 | url、status、board、title、author、time_text、published_at、text、push_count、push_up、push_down、pushes[]、fetched_at | `text` 已移除 meta 列與推文；推文保留 tag／user／content／time。刪文以 `status: 404, deleted: true` 記錄，不重試。 |

- Gossiping 需要 `over18=1`；回應若是年齡確認頁（有 over18、沒有 r-ent）判為異常並退避。
- `author`、推文 `user` 是公開 PTT ID，進 Q6 前要雜湊。

### 2.2 YouTube（Data API v3 playlistItems）[實測]

| 欄位 | 說明 |
|---|---|
| post_id | `yt-<video_id>` |
| channel／sub_type | `youtube`／`video` |
| source、channel_id | 頻道名稱與 UC… id |
| video_id、url | `https://www.youtube.com/watch?v=<id>` |
| title、text | `text` = 影片說明（description） |
| published_at、fetched_at | API 的 `publishedAt` 轉 +08:00 |
| collect_method | `youtube_data_api_v3_playlistItems` |

- 47,744 部影片 12 個欄位全部非空；續抓時最後一頁重抓造成 14 筆重複，已去重。
- 沒有觀看數、按讚數、留言數（statistics 要另外呼叫 videos.list），也沒有字幕。
- 留言（commentThreads）排在 uploads 之後：每天保留 2,000 units，每頻道每月取第一、中間、最後一部影片各 100 則頂層留言；uploads 預算用完前不會進入這一步，所以本批還沒有留言。
- 保存期限：YouTube API Developer Policies III.E.4，未授權資料最多保存 30 個日曆天，本批最晚 2026-12-02 前要刪除或重抓。

### 2.3 Facebook／Instagram／Threads 登入深度爬取 [實測]

| 欄位 | 說明 |
|---|---|
| post_id | `fa-`／`in-`／`th-` + 平台貼文 id 或 code |
| sub_type | `reel`（product_type = clips）、`comment_thread`（Threads 的回覆串）、其餘 `post` |
| source、author | 目標帳號 slug、貼文作者名稱 |
| url | FB permalink；IG `/p/<code>/`；Threads `/@<user>/post/<code>` |
| published_at | FB `creation_time`、IG／Threads `taken_at`（epoch）轉 +08:00 |
| like_count、comment_count、share_count、play_count、repost_count、quote_count | 依平台有無而定；FB 有 share_count，Threads 有 repost／quote，IG reel 有 play_count |
| collect_method | `loggedin_graphql_cursor_replay` |

做法：頁面正常載入一次並捲動，等它自己發出時間軸分頁的 GraphQL 請求；之後在頁面內以同一個請求只換 cursor 重送。權杖不離開瀏覽器，也不寫到硬碟。FB 只取最外層 story，被分享的內層貼文留在原 story 內。

### 2.4 Instagram 未登入第一輪（2026-11-01）[實測]

- 36 列（post 14、reel 22）+ 留言 191 列，來自 cnanews_tw、pts.news、ftvnews。
- 全部由頁面內嵌 JSON 取得（`collect_method = inline_json`），XHR 路徑沒有產生任何列。
- 非空率：`published_at` 32/36；`like_count`、`comment_count` 32/36；`play_count` 等觀看欄位 0/36；留言 `text` 179/191（12 列 [推測] 為純貼圖／GIF）。
- 讚數、粉絲數是字串（"3,148"、"139K"、"13.8萬"），需統一解析千分位、K、萬。
- 留言的 `parent_comment_id` 沒有保留，回覆層級無法重建；`child_comment_count` 全為 null。

## 3. 痛點

### P-1 YouTube uploads 清單只露出最近約 2 萬部 [實測]
- TVBS 19,958 部後 `nextPageToken` 消失，最早只到 2026-07-11；公視 20,000 部後結束，最早到 2025-08-09。
- 不是預算或錯誤，是清單本身的上限。TVBS 約每天 175 部，2023-01 ~ 2026-07 的缺口約 22 萬部。

### P-2 回補 YouTube 的替代方案很貴 [推測]
- `search.list` 依時間窗切，每次 100 units、每頁最多 50 部。TVBS + 公視缺口約 26 萬部，約 5,200 次呼叫、52 萬 units，約 52 天的預設配額（10,000／天）。
- 時間窗還要切更小（search.list 結果數有上限），實際呼叫數只會更多。另一條路是申請配額提升。

### P-3 每頻道每日配額讓後面的頻道排隊 [實測]
- 每天 8,000 units 給 uploads，平均分給 7 個頻道；東森未完成，中央社、民視、高雄相關頻道尚未開始。配額在太平洋時間午夜重置，程式從 `page_token` 續抓。

### P-4 200 回應的延遲尖峰也會觸發退避 [實測]
- 判定規則是「延遲大於 2 倍 p95」就加倍 delay。YouTube 東森 2.2 ~ 3.5 秒的 200 回應把 delay 從 128 秒推到 512 秒；Threads pts.news 約 6.3 秒的 200 回應把 delay 推到 600 秒上限（退避等級最高 24）。
- 符合「有異常就變慢」的保守原則，但 200 的延遲尖峰不代表被限流。[推測] 改成只對 4xx／5xx 或連續多次延遲異常退避，速度可回到約每秒 1 次，配額仍是上限。

### P-5 斷線被計成異常 [實測]
- FB 1,478 筆無狀態請求中 1,463 筆是 `ERR_INTERNET_DISCONNECTED`，是本機網路中斷，不是平台回應。
- 這段時間同樣推高退避等級（FB 最高 14）。可改為斷線時暫停並等待網路恢復，不計入平台異常。

### P-6 三個目標都停在「連續 10 頁沒有新貼文」[實測]
- FB cnanewstaiwan 停在 2026-07-01、Threads pts.news 停在 2026-07-25，離 2023-01 仍很遠；IG ftvnews 回到 2025-08-21。
- [推測] 原因可能是 cursor 重送拿到重複頁、時間軸對舊貼文改用不同查詢，或帳號層級的分頁上限。要分清楚，需要比對停止前後幾頁的回應內容。

### P-7 民視 FB 找不到可爬的粉專 [實測]
- 探測多個 slug 與搜尋得到的 profile.php?id=…，h1 都沒有「民視」，追蹤數只有 4 到 3,529。
- 已知的 FTVnews53（page id 100044539895059，公開粉專）登入與未登入都拿不到貼文。依規則跳過，不處理任何驗證挑戰。民視目前只有 IG 與 Threads 兩條路。

### P-8 Instagram 未登入的限制 [實測]
- 38 頁全部出現登入彈窗（約 1.0 ~ 2.3 秒出現），但沒有轉址到登入頁；彈窗鎖住捲動，個人頁固定只有 12 個貼文連結。
- 留言每個媒體最多約 12 則：宣告 1,134 則，只取到 191 則（約 17%）。
- 第 47 次導覽遇到 HTTP 429（`/ajax/bulk-route-definitions/`），依規則停止 instagram.com，高雄相關帳號與標籤頁沒有取得。
- 頁面自己發出 604 個 XHR（bulk-route-definitions 298、graphql 169、bz 126、bootloader 8），腳本只計導覽 47 次與捲動 2 次。若「每站載入上限」也要計入頁面自發的 XHR，就已經超過，這個計算口徑要先定義清楚。

### P-9 PTT 列表與內文分兩階段 [實測]
- 先把 9 板列表翻到 2023-01，再依新到舊輪流抓內文；目前仍在列表階段，內文是 0。
- 525 狀態 3 筆、斷線 22 筆，都在退避後重抓同一頁成功。

## 4. 對 Q6 分群的影響

| 編號 | 影響 | 依據 |
|---|---|---|
| C-1 | YouTube 與 Meta 目前都還沒回到 2023-01，「2023 起」的月份分群在這些渠道只有後段月份，跨渠道比較要按月份正規化。 | P-1、P-6 |
| C-2 | 無日期的列（IG 未登入 4/36）會落入 unknown 月份。 | 2.4 |
| C-3 | 留言每媒體上限約 12 則，熱門媒體被截斷，以留言數加權會系統性低估 IG 熱度。 | P-8 |
| C-4 | 未登入沒有觀看數，reel 無法依觀看數加權；登入深度爬取有 play_count，兩批不能混著排序。 | 2.3、2.4 |
| C-5 | 詞雲雜訊：雜湊後的 @ 提及、純 emoji、空字串、重複留言（191 則只有 177 種文字）要先清掉。 | 2.4 |
| C-6 | 目標帳號都是官方媒體，使用者生成內容只來自留言與 PTT，分群時要分開看「官方發文」和「使用者回應」。 | 2.3、2.4 |
| C-7 | 高雄相關的 IG 覆蓋目前是 0（429 後停止），地區分群要靠 PTT Kaohsiung 板與其他渠道補。 | P-8 |
| C-8 | 文字含人名與家庭細節，PTT ID、留言作者需雜湊，內文需在 Q6 端再遮罩。 | 2.1、2.4 |
| C-9 | YouTube 資料 30 天保存期限，分群結果可以保留，原始影片資料要在期限前刪除或重抓。 | 2.2 |
