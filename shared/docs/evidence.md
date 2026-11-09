# 證據總覽：實際網站截圖、F12 與 API、登入 session、壓力測試與監控過程

這份清單把 repo 裡所有「拍下來、量出來」的證據依題目排好，每一條寫明位置與它證明什麼。等級定義見 [README「證據等級」](../../README.md#證據等級)：有截圖、實跑結果或 plan 佐證的是 A。

拍攝方式有四種：

| 方式 | 範圍 | 規則 |
|---|---|---|
| Playwright 開未登入的公開頁（Linux 容器或住宅 IP） | Facebook、PTT、Threads、Instagram Reels 的公開頁，與 Q6 的 PTT 雜訊頁 | 每頁間隔 2 秒以上、每站 30 頁以內、不存 cookie／token、FB／IG／Threads 不存整頁 HTML；一般使用者的帳號、頭像、IP 黑框；PTT 帳號是公開資料，不遮 |
| 登入帳號的 Chrome profile 重爬（2026-11-01） | Facebook 4、Instagram 8、Threads 3 個目標頁，共 60 張 | 每頁捲動 25 次、間隔 5 秒以上、只讀不寫；頭像、通知數、分頁標題模糊；cookie／token 只留 sha256 前 10 碼與長度 |
| 登入帳號的 Chrome profile 副本＋DevTools 逐欄搜尋（2026-11-04） | Facebook 單則貼文 1 則：3 張畫面、17 張 F12、1 份 JSON | 只載入 2 次、只讀；每個欄位用 Sources 全域搜尋（鍵名＋值綁在同一個搜尋式）翻到所在位置再截圖；帳號頭像、名稱、留言者、分頁標題模糊；不存 HTML、cookie 與請求參數 |
| 整套系統實跑（本機 Docker，零成本） | Q6 的 PM 頁、Grafana、Kibana、Prometheus、Alertmanager、六渠道公開頁 | 官方映像；數字全部從實跑紀錄檔算出 |

## 各題一覽

| 題 | 實際網站截圖 | F12／API／JSON 證據 | 登入 session | 壓測／反制／監控 | 實跑數字 |
|---|---|---|---|---|---|
| Q1 顧客反饋 | 題目沒有外部網站 | — | — | — | 合成資料實跑 [`q1/code/RESULTS.md`](../../q1/code/RESULTS.md) |
| Q2 Facebook 貼文表 | 5 張 [`q2/evidence/playwright/`](../../q2/evidence/playwright)；單則貼文 3 張 [`q2/evidence/f12/`](../../q2/evidence/f12) | 貼文表 16 個值各一張 F12 截圖＋JSON 路徑（[`fb_post_fields.json`](../../q2/evidence/f12/fb_post_fields.json)）；畫面「14h」對上內嵌 `creation_time`；粉專 Page ID 與貼文頁 profile ID 不同；心情數畫面不顯示（`HIDE_COUNTS`），只在資料裡；一列 920 B 的逐 byte 配置 [`q2_row_layout_page0.json`](../../q2/data/q2_row_layout_page0.json)；登入側 104 個 API 回應的 GraphQL 名稱與 JSON 路徑 | 16 張 [`q6/evidence/meta-login/`](../../q6/evidence/meta-login)（4 → 65 篇） | 雲端 IP 開粉專：302 到登入頁 | 100 萬列 PostgreSQL [`q2/code/RESULTS.md`](../../q2/code/RESULTS.md) |
| Q3 PTT | 6 張 [`q3/evidence/playwright/`](../../q3/evidence/playwright)，各小題的 `ptt-*.png` 在 [`q3/answer/`](../../q3/answer) | 列表頁固定 20 格、文章網址即發文時間、連續 10 頁不變量、留言列欄位 | 不需登入，只要 over18 cookie | 刪文 404、「檔案過大」截斷留言 | 7 天實抓資料離線重算 [`q3/code/RESULTS.md`](../../q3/code/RESULTS.md) |
| Q4 Threads | 8 張 [`q4/evidence/`](../../q4/evidence) | 個人頁 HTML 只內嵌 4 串；網頁自己送的 GraphQL 分頁（F12）時有時無、doc_id 會換；登入側 295 個 API 回應 | 12 張 meta-login（59 → 588 篇） | 手機版未登入捲動不再載入下一頁 | [`q4/code/RESULTS.md`](../../q4/code/RESULTS.md) |
| Q5 Instagram Reels／Threads 指標 | 9 張＋2 份 JSON [`q5/evidence/playwright/`](../../q5/evidence/playwright)，9 張 [`q5/evidence/a-dataflow/`](../../q5/evidence/a-dataflow) | 縮圖數字對上同一次載入的 JSON（欄位是 `play_count`）；畫面轉發 22 ＝ repost 18 ＋ quote 4；登入側 353 個 API 回應 | 32 張 meta-login（48 → 1,134 篇，含 Reels 分頁） | 雲端 IP 開 Reels 回 429；同一批 Reels 隔 4.6 小時 play_count 漂移 | 官方 Elasticsearch 回補 21 項檢查 [`q5/code/RESULTS.md`](../../q5/code/RESULTS.md) |
| Q6 斷詞／回補／監控 | PTT 雜訊 6 張 [`q6/evidence/playwright/`](../../q6/evidence/playwright)；六渠道公開頁 12 張 [`q6/evidence/stack/channels/`](../../q6/evidence/stack/channels) | 官方文件與原始碼截圖 21 張（原始擷取 34 張在 [`q6/evidence/src/crops/`](../../q6/evidence/src/crops)） | 60 張 meta-login 整組：歷史回補走登入帳號的可行性與風險 | 下一節 | [`q6/stack/RESULTS.md`](../../q6/stack/RESULTS.md)、[`q6/code/RESULTS.md`](../../q6/code/RESULTS.md) |

## 各平台截圖證明什麼

### Facebook（未登入 5 張，Q2）

| 檔案 | 證明什麼 |
|---|---|
| [`fb_page_nasa.png`](../../q2/evidence/playwright/fb_page_nasa.png)、[`fb_post_nasa.png`](../../q2/evidence/playwright/fb_post_nasa.png) | 粉專頁與單則公開貼文在未登入時看得到的欄位 |
| [`fb_post_time_and_ids.png`](../../q2/evidence/playwright/fb_post_time_and_ids.png) | 畫面只有相對時間「14h」，頁面內嵌 `creation_time` 1793310300：發文時間可以存絕對時間 |
| [`fb_page_transparency_id.png`](../../q2/evidence/playwright/fb_page_transparency_id.png) | 粉專透明度頁 Page ID 54971236771，貼文頁資料只出現 profile ID 100044561550831：「粉絲頁 ID」要存 Page ID |
| [`fb_cloud_ip_login_redirect.png`](../../q2/evidence/playwright/fb_cloud_ip_login_redirect.png) | 雲端 IP 開粉專：HTTP 302 到 `/login/`，拿不到任何貼文欄位 |

### Facebook 單則貼文：一列 920 B 的每一格對到哪張圖（Q2）

貼文表的欄位在 FB 頁面哪裡、資料庫裡占幾個 byte，各有一條證據。F12 截圖、畫面截圖與 JSON 路徑在 [`q2/evidence/f12/`](../../q2/evidence/f12)（README 有完整欄位表）；byte 配置由 [`q2/code/row_layout.py`](../../q2/code/row_layout.py) 從 PostgreSQL 16 第 0 頁第 1 列的原始 bytes 讀出，結果在 [`q2_row_layout_page0.json`](../../q2/data/q2_row_layout_page0.json)。

| 一列裡的格子 | B | FB 上的位置（F12 截圖） | 這則 NASA 貼文的值 | byte 從哪裡來 |
|---|---|---|---|---|
| 列標頭 | 24 | 不是 FB 欄位 | — | JSON `row.header_fields`：xmin 4＋xmax 4＋cid 4＋ctid 6＋infomask2 2＋infomask 2＋hoff 1＝23，補 1 到 24 |
| 貼文 ID | 32 | `post_id`（[`fb_f12_post_id.png`](../../q2/evidence/f12/fb_f12_post_id.png)）；登入帳號重爬的 65 篇也都是 16 碼純數字，沒有底線 | `1671214107707292`（16 碼） | 測試資料把粉專 ID 和 15 碼亂數用底線接成 31 字元，存 text＋1 B 長度；爬到的是純數字，存 bigint 只要 8 B |
| 粉專 ID | 8 | `delegate_page.id`（[`fb_f12_page_id.png`](../../q2/evidence/f12/fb_f12_page_id.png)）；另有 profile ID（[`fb_f12_profile_id.png`](../../q2/evidence/f12/fb_f12_profile_id.png)） | Page ID `54971236771` | bigint |
| 粉專名稱 | 14 | `owning_profile.name`（[`fb_f12_page_name.png`](../../q2/evidence/f12/fb_f12_page_name.png)） | NASA - National Aeronautics and Space Administration（52 字） | 測試資料 `fanpage-07920` 13 字＋1 B 長度 |
| 補齊 | 2 | — | — | JSON `columns[3].pad_bytes_hex` 為 `00 00`：發文時間要從 8 的倍數開始 |
| 發文時間 | 8 | `creation_time`（[`fb_f12_creation_time.png`](../../q2/evidence/f12/fb_f12_creation_time.png)；畫面 [`fb_post_time_tooltip.png`](../../q2/evidence/f12/fb_post_time_tooltip.png)） | `1793310300` | timestamptz |
| 內文 | 754 | `message.text`（[`fb_f12_message.png`](../../q2/evidence/f12/fb_f12_message.png)） | 英文 207 字、213 B | 測試資料 250 個中文字 × 3 B＋4 B 長度 |
| 補齊 | 6 | — | — | JSON `columns[5].pad_bytes_hex` 為 6 個 `00` |
| 心情與分享 9 欄 | 72 | 七種心情 `top_reactions.edges[i].reaction_count`（`fb_f12_reaction_*.png` 7 張）、總數 `reaction_count.count`（[`fb_f12_reaction_count.png`](../../q2/evidence/f12/fb_f12_reaction_count.png)）、分享 `share_count.count`（[`fb_f12_share_count.png`](../../q2/evidence/f12/fb_f12_share_count.png)） | 讚 18961、大心 7510、加油 377、哇 121、哈 79、怒 9、嗚 6；總數 27063；分享 1406 | 9 × bigint 8 B |

- 合計 920 B，等於這一列的 `lp_len`；每欄長度都和 `pg_column_size` 相同，補齊的 byte 全是 0（JSON `checks` 三項皆為 true）。
- 心情數畫面上看不到：貼文設定 `reaction_display_strategy = HIDE_COUNTS`（[`fb_f12_reaction_display_strategy.png`](../../q2/evidence/f12/fb_f12_reaction_display_strategy.png)），心情對話框只列名稱（[`fb_post_reactions_dialog.png`](../../q2/evidence/f12/fb_post_reactions_dialog.png)）。第二來源是點開對話框時的 GraphQL 回應 `CometUFIReactionsDialogQuery`（[`fb_f12_reactions_dialog_graphql.png`](../../q2/evidence/f12/fb_f12_reactions_dialog_graphql.png)）。
- 數字會一直變：同一則貼文 23:56 載入時讚 18,961，00:06 再開對話框是 18,965。每筆紀錄要附擷取時間。
- 貼文 ID 改存 bigint（照 F12 拍到的 16 碼純數字）：同一批 100 萬列實測，一列 920 → 896 B，一頁放得下第 9 列，表 1,024,000,000 → 910,229,504 B（977 → 868 MB，小 11.1%）。
- byte 欄位用的是 100 萬列測試資料的長度。FB 證據證明的是「有這個欄位、在哪裡、是什麼型別」；這則 NASA 貼文換算成同一張表會是粉專名稱 53 B、內文 217 B，一列比 920 B 短。

### PTT（Q3、Q6）

| 位置 | 證明什麼 |
|---|---|
| [`q3/evidence/playwright/`](../../q3/evidence/playwright) 6 張 | 八卦板要先過 over18 確認；最新列表頁的結構；填滿的列表頁剛好 20 篇；連續 10 頁即時驗證不變量；文章頁的發文資訊與內文；留言列的欄位 |
| [`q3/answer/`](../../q3/answer) 各小題資料夾的 `ptt-*.png` | 每個小題（規模、成本、不變量、沒監控到的、規格改變、提問）各自的畫面證據 |
| [`q6/evidence/playwright/`](../../q6/evidence/playwright) 6 張 | 斷詞雜訊：短網址（reurl.cc、tinyurl.com、bit.ly）、電話、地名，以及被推文換行切成兩段的網址 |

### Threads（Q4、Q5）

| 檔案 | 證明什麼 |
|---|---|
| [`threads_profile_nasa.png`](../../q4/evidence/playwright/threads_profile_nasa.png)、[`threads_profile_zuck.png`](../../q4/evidence/playwright/threads_profile_zuck.png) | 個人頁 HTML 只內嵌 4 串，其餘靠分頁請求 |
| [`threads_graphql_desktop.png`](../../q4/evidence/playwright/threads_graphql_desktop.png) | F12：個人頁載入時網頁自己送的 GraphQL 分頁請求 |
| [`threads_mobile_wall.png`](../../q4/evidence/playwright/threads_mobile_wall.png) | 手機版未登入：捲動不再載入下一頁 |
| [`threads_post_url.png`](../../q4/evidence/playwright/threads_post_url.png) | 貼文網址 ＝ `/@帳號/post/{code}` |
| [`threads_post_repost.png`](../../q5/evidence/playwright/threads_post_repost.png)、[`threads_post_repost_drift.png`](../../q5/evidence/playwright/threads_post_repost_drift.png) | 貼文頁看得到轉發數；同一則三次抓取讚 148 → 266 → 288，畫面轉發 22 ＝ `repost_count` 18 ＋ `quote_count` 4 |
| [`q5/evidence/a-dataflow/`](../../q5/evidence/a-dataflow) 9 張 | 官方 `use_media` 文件、App 轉發選單、reshare 為 null 而不是 0、未登入的個人頁表頭 |

### Instagram Reels（未登入，Q5）

| 檔案 | 證明什麼 |
|---|---|
| [`ig_reels_grid.png`](../../q5/evidence/playwright/ig_reels_grid.png)、[`ig_reel_page.png`](../../q5/evidence/playwright/ig_reel_page.png) | Reels 分頁看得到觀看數，單支 Reel 頁未登入看不到 |
| [`ig_reels_grid_json_check.png`](../../q5/evidence/playwright/ig_reels_grid_json_check.png)、[`ig_reel_fields.json`](../../q5/evidence/playwright/ig_reel_fields.json) | F12：縮圖數字對上同一次載入的 JSON，欄位是 `play_count` |
| [`ig_reels_play_count_drift.png`](../../q5/evidence/playwright/ig_reels_play_count_drift.png)、[`ig_reels_play_count_drift.json`](../../q5/evidence/playwright/ig_reels_play_count_drift.json) | 同一批 9 支 Reels 隔 4.6 小時再抓：最新一支 +29.3%，舊的 +0.02%～+0.56% |
| [`ig_reels_cloud_ip_login_redirect.png`](../../q5/evidence/playwright/ig_reels_cloud_ip_login_redirect.png) | 雲端 IP 開 Reels：302 到登入頁，登入頁本身回 HTTP 429 |

### 登入帳號重爬（60 張，Q2／Q4／Q5／Q6）

全部在 [`q6/evidence/meta-login/`](../../q6/evidence/meta-login)（README 有逐欄比對表、證據鏈與每張圖）。

| 平台 | 免登入 → 登入帳號 | 目標頁 | 每個目標頁的 4 張 |
|---|---|---|---|
| Facebook | 4 → 65 篇，登入牆 0 次 | cnanewstaiwan、PNNPTS、ftvnews53（網址代號不對，0 篇）、NASA | 頁面頂部、捲動 25 次後、F12 Network 頂部、F12 Network 捲動後 |
| Instagram | 48 → 1,134 篇（清洗前 1,268） | cnanews_tw、pts.news、ftvnews、nasa 各含 Reels 分頁 | 同上 |
| Threads | 59 → 588 篇 | cnanews_tw、pts.news、ftvnews | 同上 |

證據鏈每個平台四格：session cookie（名稱、網域、httpOnly、長度）→ 頁面發出的 API 請求（GraphQL 名稱、doc_id、HTTP 狀態、次數）→ 回應 JSON 欄位路徑 → 落地欄位的有值篇數。

## 壓力測試與監控過程（Q6 整套系統實跑，本機 Docker）

| 過程 | 做了什麼 | 結果 | 截圖／紀錄（在 [`q6/evidence/stack/`](../../q6/evidence/stack) 與 [`q6/stack/results/`](../../q6/stack/results)） |
|---|---|---|---|
| 回補壓力：三輪 42.5 萬篇 | 1 → 3 個 task，限速 3,000 → 不限速；背景探針每 0.5 秒輪流查 PM 頁四種範圍 | 不限速時全期 p95 262 ms；G3 自動降速 3,000 → 1,500 → 750 後 166 ms；3,727 次探針沒有一次超過 180 秒、錯誤 0 | `grafana/01_dashboard_backfill_window.png`、`latency_probe_summary.json` |
| 寫入壓力 vs 查詢延遲 | 只有 ES 寫入、沒有斷詞程式，每秒 1,000／3,000／不限速（實際 9,573） | 全期 p95 +31%、近一個月 +103%；同一台機器上斷詞程式搶 CPU 的影響比 ES 寫入大 | `es/write_load_vs_query_latency.json` |
| 砍掉、接手、重跑 | 第 3,000 篇砍 task（exit 137）；心跳還新時搶領；心跳過期接手；同版 `--force` 重跑；映像字典版本與帳本不一致 | checkpoint 停在 3,000、搶領 0 個、從 3,000 接續、同版覆寫；不一致時重複領取 130,281 次，修成直接 exit 2 | `backfill/*.log` |
| 四道閘門與人工簽核 | G1 數量、G2 品質、G3 線上負載、G4 首月 PM 簽核 | 第 1 輪 46 個分區全被 G2 擋（檢查規則本身錯）；第 2 輪 34 過 12 沒過；第 3 輪 10 過 2 人工豁免 | `pm_live/01–05`、`backfill/final_ledger.json` |
| 告警鏈實跑 | Prometheus 96 條規則 → Alertmanager → webhook → PM 頁橫幅 | firing 與 resolved 都收到；驗證結束仍在響的 4 組都查過原因：磁碟 94%、Instagram 樣本沒有發文時間、整詞召回 0.925、SLM 排程還沒到 | `pm/30_alerts_strip.png`、`alertmanager/01_alerts.png`、`prometheus/01_alerts.png`、`grafana/03_alert_rules_from_prometheus.png` |
| 實跑抓到並修掉的問題 | 整套從頭跑一次 | 22 個離線測試看不到的問題：快照倉庫權限、exporter 旗標、`exists` 對空陣列為 false、幻影序列讓規則永遠成立…… | [`q6/stack/MONITORING.md`](../../q6/stack/MONITORING.md) |
| 平台端的反制 | 雲端 IP 開 FB、IG；YouTube 影片頁 | FB 302 登入頁、IG 429、YouTube 429 reCAPTCHA；沒有繞過 | `q2/evidence/playwright/fb_cloud_ip_login_redirect.png`、`q5/evidence/playwright/ig_reels_cloud_ip_login_redirect.png`、`channels/youtube_watch_429_recaptcha.png` |
| 六渠道爬蟲監測看板 | 本機實跑 3 小時；歷史爬蟲另跑約 10.5 小時 | 每分鐘請求數、2xx／4xx／5xx／失敗分佈、API p95 依平台、delay 與退避等級 | [`q6/evidence/crawl/grafana/crawler_monitor_3h_panels.png`](../../q6/evidence/crawl/grafana/crawler_monitor_3h_panels.png)、[`q6/evidence/history-crawl/grafana/`](../../q6/evidence/history-crawl/grafana)（30 分鐘與 6 小時，日誌面板已模糊） |
| 基礎設施的變異與壞事件 | terraform test 37 項、8 個刻意改壞一行的變異；parser 三種壞事件各投遞 5 次；地端 smoke 19 項連跑 3 次 | 8 個變異全部被抓到；壞事件每次回 422 進 DLQ；smoke 全過 | [`shared/infra/gcp/`](../infra/gcp)、[`shared/infra/gcp/services/parser/`](../infra/gcp/services/parser)、[`shared/infra/onprem/VERIFY.md`](../infra/onprem/VERIFY.md) |

## 電腦端程式與證據（2026-11-03 入庫）

| 項目 | 程式 | 證據與資料 | 重點 |
|---|---|---|---|
| 登入帳號重爬 | [`q6/code/meta-login/`](../../q6/code/meta-login) | 證據鏈圖 [`q6/evidence/meta-login/chain/`](../../q6/evidence/meta-login/chain)、落地資料 [`q6/data/meta-login/`](../../q6/data/meta-login) | 登入 → 只讀爬取 → F12 補拍 → 遮罩 → 比較 → 證據頁，六個步驟各一支程式；cookie 只留雜湊 |
| 六渠道歷史爬蟲（2023-01 起） | [`q6/code/history-crawl/`](../../q6/code/history-crawl) | [`q6/evidence/history-crawl/`](../../q6/evidence/history-crawl)：10 張執行中截圖、4 張 Grafana 看板、遙測統計 | 自適應節流（異常加倍、上限 600 秒、連續 3 次暫停 30 分鐘）、checkpoint 續跑；欄位與痛點見 [`fields-and-painpoints.md`](../../q6/evidence/history-crawl/fields-and-painpoints.md) |
| 規模試跑 | [`q6/code/scale-pilot/`](../../q6/code/scale-pilot) | [`q6/evidence/scale-pilot/`](../../q6/evidence/scale-pilot) | 單機 ES 灌入 28.3 萬篇、538.7 MB（約 1.99 KB／篇）；5 億篇外推約 950 GB，單機放不下 |

## 刻意不入庫的

- 未遮罩的原始截圖（有帳號頭像）、遮罩後的請求帳本（8.6 MB JSON）、session 紀錄、證據鏈 HTML 原始檔（含 cookie 雜湊）、清洗前的 instagram.jsonl。
- 每個請求的原始遙測日誌（只收統計摘要 `telemetry_summary.json`）、YouTube API 金鑰、瀏覽器 profile。
- 規模試跑的語料與斷詞中間檔（`sources.pkl` 約 155 MB）。
