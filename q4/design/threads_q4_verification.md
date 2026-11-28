# Threads 爬蟲：驗證紀錄（2026-10-28 ～ 10-30；11-15 補 §12、§13；11-16 補 §14）

這份紀錄逐條驗證 2026-10-27 第三次實測（`q4/design/threads_q4_result.txt` 舊版）的結論，並補測最終做法。修正後的結論與設計摘要見 `q4/design/threads_q4_result.txt`。§1–§10 是 10-28、10-29 的紀錄；§11 是 10-30 在 Linux 重跑量測程式的結果，和前面不一致的地方已在原段落加註；§12 是 11-15 驗證 doc_id 能從網頁 JS 取得；§13 是 11-15 重測 GraphQL 第 1 頁的傳輸量。

共同條件：

- 全程不登入、不帶 cookie。不用代理（10-30 重測經 Linux 容器的對外代理，非輪替代理），也不用 stealth 或指紋偽裝套件。瀏覽器一律無頭。
- 對 threads.com 的請求，10-28 約 950 次，10-29 約 130 次。10-30 重測：HTTP 20 次（含 robots.txt 2 次）；瀏覽器開頁 21 次，加上手機視窗網站自己的轉址，共 27 次主框架導覽；主框架文件有紀錄的 24 份，加上前一次嘗試手機 3 次沒有紀錄的推估 9 份，約 33 份。
- 沒有記錄或公開任何 token（csrftoken、lsd 等）或 cursor 值，只記名稱與長度。
- 抽樣的小帳號不列帳號名稱。表中的大帳號都是公開名人或機構帳號。
- 大小有兩種：「壓縮後」是網路上實際傳輸的位元組（`r.raw.read(decode_content=False)`），「解壓後」是內容大小。

## 1. 純 HTTP 為什麼回 SHELL

| 請求 | 結果 |
|---|---|
| UA、Accept、Accept-Language（舊腳本） | 200，約 259 KB（解壓後），0 篇，判定為 SHELL |
| 上述再加 `Sec-Fetch-Mode: navigate`、`Sec-Fetch-Dest: document` | 200，完整內嵌貼文 |
| 同樣的 header，Python/OpenSSL 與 curl/Schannel 兩種 TLS 實作 | 兩者都成功 |

結論：

- SHELL 是缺 Sec-Fetch 標頭造成的，與 TLS 指紋無關。
- HTML 首頁固定內嵌 4 個最上層串，16/16 帳號都是。10-30 重測 3/3 也是；原因是 HTML 預排的 `BarcelonaProfileThreadsTabDirectQuery` 本身就帶 `first: 4`（§11）。

## 2. 未登入能不能分頁

- 舊腳本用桌面 viewport 加 `mouse.wheel`，不會觸發分頁。
- 改用手機 viewport（390×844、is_mobile、has_touch），捲動後瀏覽器會送出（10-30 量測程式只有 1/3 帳號送出，截圖腳本 1/2，doc_id 與 `first` 也變了，見 §11）：
  - `POST /graphql/query`
  - friendly name `BarcelonaProfileThreadsTabRefetchableDirectQuery`
  - doc_id `39551525044493602`
  - variables：`{after, before:null, first:10, last:null, userID, allow_page_info_for_lox_user:false}`，加上 45 個 `__relay_internal__pv__*` 旗標
- 分頁資訊在 `mediaData.page_info`（`has_next_page`、120 字元的 `end_cursor`）。
- 直接帶 cursor 往下翻：第 2、3、4 頁各 11 串，`has_next_page` 都是 true。
- 未登入的翻頁上限只在前端，伺服器並不限制。

## 3. 最小可用請求

從瀏覽器取得一組請求後，逐項拿掉，看哪些是必要的：

| 拿掉的項目 | 結果 |
|---|---|
| cookie | 仍然成功，所以不需要 cookie |
| body 只留 `doc_id, variables, lsd, fb_api_req_friendly_name, server_timestamps, fb_api_caller_class` | 成功 |
| `x-asbd-id, x-bloks-version-id, x-root-field-name, x-web-session-id, x-fb-friendly-name` | 成功，所以這 5 個不需要 |
| `x-logged-out-threads-migrated-request` | 200，但只回 289 bytes 的空回應 |
| `x-csrftoken` | 403 |
| 只用 HTML 裡的 LSD 自己組 header | 403（缺 csrftoken 與 migrated header） |

必要的 x-header 有 4 個：`x-csrftoken`、`x-fb-lsd`、`x-ig-app-id`、`x-logged-out-threads-migrated-request`。

- token 要先用無頭瀏覽器開頁取得。
- 同一組 token 可以跨帳號共用，放 361 秒以上仍然有效；實際能用多久沒有測到底。

## 4. 傳輸量

舊結果的「wire」欄位其實是 `len(r.content)`，也就是解壓後大小，標錯了。

| 內容 | 壓縮後 | 解壓後 |
|---|---|---|
| profile HTML | 138–172 KB | 約 0.8–1.23 MB |
| GraphQL 第 2 頁（zuck） | 26.6 KB | 233 KB |
| GraphQL 第 3 頁（zuck） | 22.5 KB | – |
| GraphQL 第 4 頁（zuck） | 50.8 KB | – |
| GraphQL 第 1 頁（10-29，102 次平均） | 回應 27.8 KB，上傳 body 4.08 KB | – |
| profile HTML（10-30，gzip，3 帳號） | 163.9–198.6 KB | 848,792–1,206,757 B |
| profile HTML（10-30，Chromium 自己協商的壓縮，6 次） | 144.1–164.5 KB | 846,469–1,275,840 B |
| 網頁自己送的分頁 query（10-30，natgeo，11 串） | 24.0 KB | 210 KB |
| GraphQL 第 1 頁（11-15 重測，12 帳號平均，§13） | 回應 26.4 KB，上傳 body 4.6 KB | 平均 231 KB |

## 5. 「HTML ＋ 1 頁 GraphQL」能不能達到 10 篇（10-28）

- 抽樣 9 個帳號，追蹤數從 1k–10k 到 100 萬以上，9/9 都拿到 15–17 篇；zuck 拿到 33 篇。
- 7 個大帳號重測，結果完全一致。

## 6. 只打 GraphQL 第 1 頁（after=null），不抓 HTML（10-29）

token 只取一次，三個帳號共用：

| 帳號 | 參數 | 狀態 | 壓縮後 | 串數 | 涵蓋 HTML 第 1 頁 | 最新一則一致 | has_next |
|---|---|---|---|---|---|---|---|
| zuck | after=null, first=10 | 200 | 38.6 KB | 10 | 10/10 | 是 | 是 |
| zuck | 不帶 after | 200 | 40.3 KB | 10 | 10/10 | 是 | 是 |
| zuck | first=25 | 200 | 71.1 KB | 25 | 10/10 | 是 | 是 |
| zuck | first=50 | 200 | 73.0 KB | 25（伺服器上限） | 10/10 | 是 | 是 |
| nba | after=null, first=10 | 200 | 36.9 KB | 10 | 4/4 | 是 | 是 |
| nytimes | after=null, first=10 | 200 | 14.3 KB | 10 | 4/4 | 是 | 是 |

結論：每帳號每輪 1 次請求就能拿到 ≥10 串，HTML 不用抓（userID 事先快取）。

## 7. 頻率與封鎖

**10-28（打的是第 2 頁，帶 cursor；出口 IP 沒有記錄）**

- 依序打 250 次，約 0.8 req/s：0 次異常。
- 併發加速到 10.7 req/s，共 560 次：0 次異常。
- 延遲中位數 1.2 秒；+30 秒、+120 秒的恢復檢查都正常。

**10-29（打的是第 1 頁 after=null；出口為個人網路 IP，壓測前先確認過）**

- 計畫：5 req/s 跑 60 秒 → 10 req/s 跑 60 秒 → 20 req/s 跑 120 秒 → 20 req/s 撐 600 秒。
- 停止條件：最近的請求異常超過 5%。
- 實際結果：

| 時間 | 結果 |
|---|---|
| 0–10 秒 | 43 次全部 200，p50 1.18 秒 |
| 10–20 秒 | 57 次，其中 18 次 401（回應 119 bytes） |
| 停止 | 共 102 次，82 次成功；第 82 次之後全部 401 |
| +5 秒 | GraphQL 401；HTML 200，有貼文 |
| +60 秒 | GraphQL 401；**換新 token 一樣 401**；HTML 200 |
| +300 秒 | GraphQL 401；HTML 200 |

結論：

- 封鎖以 IP 為單位，換新 token 無效。
- 只擋匿名 GraphQL，HTML 照常。
- 觸發點大約是 5 req/s 下累積 80–100 次，封鎖時間超過 5 分鐘。
- 10-28 衝到 10.7 req/s 都沒被擋，和這次結果矛盾。兩次的請求類型（第 1 頁或第 2 頁）不同，出口 IP 也可能不同，目前分不出是哪個造成的。
- 尚未測得：單一 IP 能長期撐住的速率，以及解封時間。

## 8. 雲端 IP

- 從 GitHub Actions（Azure AS8075）打 1 次：200，有 10 篇。
- 只打了一次，看不出雲端 IP 是否會被直接封鎖；那次的執行紀錄沒有保留。

## 9. 代理牌價（各家官網，2026-10-28）

| 供應商 | 方案 | 價格 |
|---|---|---|
| IPRoyal | 住宅 | $1.75/GB（10 TB 以上）；按量 $1.84/GB |
| IPRoyal | ISP | 每 IP 24 小時 $1.80、30 天 $2.70；「不限流量」實際是限速 100 Mbps |
| Decodo | 住宅 | $2/GB（1 TB 企業方案，$2,000/月）；按量 $4/GB；TLS 握手流量也計費 |
| Oxylabs | 住宅 | $2.5/GB（1K GB 以上） |
| Oxylabs | ISP | $1.1–1.5/IP |
| Bright Data | 住宅 | $5–8/GB（牌價） |

## 10. 舊結論驗證總表

| 舊結論 | 驗證結果 |
|---|---|
| 純 HTTP 永遠回 SHELL | ✗ 缺 Sec-Fetch 才會（§1） |
| 登出捲動不能分頁 | ✗ 手機 viewport 會觸發（§2；10-30 重測 1/3，§11） |
| end_cursor 無用 | ✗ 可以直接翻到第 4 頁以上（§2） |
| 匿名沒有回傳貼文的 GraphQL | ✗ 分頁 query 不帶 cookie 就能拿到（§3） |
| 每次 156 KB | ✗ 那是 HTML；只打 GraphQL 約 32 KB（§4、§6） |
| ISP $1.8/IP/月、185 IP 足夠 | ✗ 價格是 24 小時價；單 IP 會被封（§7、§9） |
| HTML 首頁 4 串 | ✓ 16/16（§1）；10-30 重測 3/3（§11） |
| IPRoyal 住宅 $1.75/GB | ✓ 但限 10 TB 以上（§9） |

## 11. 10-30 Linux 重測（量測程式 HTTP 與瀏覽器模式）

細節、指令與退出碼見 `q4/code/RESULTS.md`，摘要 JSON 在 `q4/data/threads_q4_out/rerun_2026-10-30_linux/`。

條件：Linux 容器經代理連線，帳號 nasa、zuck、natgeo，不登入、每個帳號開新的 browser context、不存 cookie。時間 07:50–07:57 與 12:29–12:37 UTC；下面的大小與篇數取自 12:29–12:36 那一輪。

**HTML（requests，gzip）**

| 帳號 | 不帶 Sec-Fetch | 帶 Sec-Fetch |
|---|---|---|
| nasa | 200，58,564 B（解壓後 258,277 B），0 串，判定為 SHELL | 200，178,458 B（解壓後 946,447 B），4 串 5 篇 |
| zuck | 200，58,517 B（解壓後 258,238 B），0 串，判定為 SHELL | 200，203,384 B（解壓後 1,206,757 B），4 串 10 篇 |
| natgeo | 200，58,524 B（解壓後 258,238 B），0 串，判定為 SHELL | 200，167,858 B（解壓後 848,792 B），4 串 5 篇（其中 1 篇是 natgeo 自己的貼文所引用的 @natgeotv 貼文） |

- §1 的結論重現：SHELL 是缺 Sec-Fetch 造成的。
- 篇數含串內回覆，也含被引用的貼文。被引用的貼文嵌在另一篇的 `share_info.quoted_post` 底下，不是獨立的一串；扣掉之後 natgeo 自己的串與回覆是 4 篇。
- 帶 Sec-Fetch 的 HTML 裡有預排的 `BarcelonaProfileThreadsTabDirectQuery`（queryID `28967344372926951`，`first: 4`，variables 有 `userID` 與 `allow_page_info_for_lox_user`），所以固定是 4 串；`page_info.has_next_page` 為 true，`end_cursor` 120 字元。
- 「Log in to see more from 帳號.」這行字已經在伺服器回的 HTML 裡。

**瀏覽器**

| 視窗 | 載入次數 | HTML 內嵌 | 網頁自己送的 GraphQL | 捲動後 |
|---|---|---|---|---|
| 桌面 1280×720 或 1440×900 | 15（3 帳號 × 5 輪；9 次在 07:52–07:57，6 次在 12:30–12:36） | 每次 4 串 | 每次只有 `fetchBarcelonaExperimentQuery`（0 串） | 最後 3 次確定把內層主欄捲到底，DOM 裡的貼文連結數不變；其餘 12 次只有滾輪，沒有證據顯示主欄有被捲動 |
| 手機 390×844 | 3（12:33–12:34；07:55 另有 3 次因程式錯誤沒有紀錄） | 每次 4 串 | nasa、zuck 只有 `fetchBarcelonaExperimentQuery`；natgeo 另有 1 次分頁 query | natgeo DOM 裡的貼文連結 5 個變 22 個（本人 4 個變 18 個），其他不變 |

「DOM 裡的貼文連結」是渲染後頁面裡 `/@帳號/post/{code}` 連結的不重複 code，不管有沒有捲進視窗、有沒有被對話框蓋住，所以不等於截圖裡看得到的篇數。

natgeo 的分頁 query：`BarcelonaProfileThreadsTabRefetchableDirectQuery`、doc_id `39825889313693314`、HTTP 200、`first: 11`，回 11 串 17 篇（三欄齊全 17/17；可能含被引用的貼文，回應本文沒存，分不出來），壓縮後 24,623 B、解壓後 215,228 B。`after` 不是空的，值沒有存；它接在 HTML 那 4 串的 cursor 後面是推論，依據是 DOM 裡 5 個連結加 17 篇剛好 22 個、沒有重複。回應在捲動階段第 1 步就到了，那時 JS 還沒把主欄捲到底，觸發的是滾輪、滑鼠移動還是網頁自己的計時，分不出來。

**同一天的截圖腳本（`shared/capture/`，10-30 凌晨 +08）**

| 視窗 | 腳本 | 動作 | 有沒有分頁 query |
|---|---|---|---|
| 手機 390×844 | 第一版 `capture.py`（01:51–01:52） | 載入後捲 5 次 | 沒有帶貼文的回應（`capture_log.txt` 第 11 行；沒記 query 名稱） |
| 桌面 1440×900 | 第二版 `capture_threads.py`（01:55:59） | 載入後 4.5 秒，再滾輪 1,600 px | 有 1 次，doc_id `28880431481598345`、11 串（`threads_graphql_desktop.png`） |
| 手機 390×844 | 第二版 `capture_threads.py`（01:56:17） | 載入後滾輪 4 次，每次 2,000 px | 有，帶貼文的回應 2 個（`threads_mobile_wall.png` 的標註） |

合計網頁自己送分頁 query：桌面是截圖腳本 1/1、量測程式 0/15；手機是截圖腳本 1/2、量測程式 1/3。

**doc_id 與 queryID 的變化**

| 時間 | 來源 | 值 |
|---|---|---|
| 10-28／10-29 | §2 手機捲動 | 分頁 query `39551525044493602` |
| 10-30 01:55（+08） | `shared/capture` 桌面截圖 | 分頁 query `28880431481598345` |
| 10-30 12:34（UTC） | 本節手機 natgeo | 分頁 query `39825889313693314` |
| 10-30 07:50 與 12:30（UTC） | HTML 預排的 `BarcelonaProfilePageDirectQuery` | 從 `39586378077644222` 變成 `28894437973498594` |
| 10-30 07:50、12:30（UTC） | HTML 預排的 `BarcelonaProfileThreadsTabDirectQuery` | 兩次都是 `28967344372926951` |

結論：

- 「HTML 只內嵌 4 串」與「doc_id 會換」再次成立。
- 網頁自己送分頁 query 今天不穩定：量測程式桌面 0/15、手機 1/3。實作不能依賴「開頁就會看到分頁 query」，要監控每次回傳的串數。doc_id 要另外取得：10-30 兩個時段存下的 36 份 HTML（含 9 份空殼）都找不到上面三個分頁 query 的 doc_id，有內容的 HTML 只有模組名 `BarcelonaProfileThreadsTabRefetchableDirectQuery.threads.graphql`。推測要從網頁的 JS 取得，但這個做法沒有驗證（2026-11-15 已驗證可行，見 §12）。
- 手機視窗每次載入都會經過網站自己的轉址：先拿到 `/@帳號` 的 200，網頁接著自己導到 `/aymh/?next`，該網址回 302 轉回 `/@帳號?hpir&http_ref`。
- 沒有重測：§3 的最小請求、§6 的 `after=null` 直接呼叫（都需要取用 token），以及 §7 的 401 封鎖。
- robots.txt（10-30 抓取，sha256 `3230a25a…0e44`）對 `User-agent: *` 是 `Disallow: /`，並註明未經 Threads 書面許可不得以自動化方式蒐集資料（GPTBot 等 14 組列名的爬蟲也是 `Disallow: /`，Googlebot 等 22 組列名的爬蟲只擋部分路徑）。這點 10-31 已補進設計摘要的瓶頸二。

## 12. 分頁 query 的 doc_id 從網頁 JS 取得（2026-11-15）

要驗證的是 §11 留下的推測：doc_id 不在 HTML 裡，要從網頁的 JS 取得。證據與腳本在 `q4/data/threads_q4_out/docid_2026-11-15/`（不含 token、cursor）。

做法（帳號 zuck）：

1. 純 HTTP 抓 `/@zuck`，帶 §1 的瀏覽器標頭與 Sec-Fetch：200，解壓後 1,127,051 B。HTML 裡分頁 query 只出現模組名 2 次，和 §11 一樣沒有 doc_id。
2. HTML 的 43 個 `<script src>` 都是內嵌的 `data:` 小腳本，不含 query 模組。
3. HTML 裡 `rsrcMap` 列出的 649 個 JS（共 32 MB）全部下載：沒有一個含分頁 query 的模組，但已看到 doc_id 的寫法，136 個其他 query 都是 `__d("<Query名>_threadsRelayOperation",[],(function(...){a.exports="<doc_id>"}),null)`。
4. 把 HTML 裡多層跳脫的 JS 網址（含 `rel="preload"` 的串流資料）一起解開，共 663 個不重複網址，全部下載：其中 1 個 bundle 含 `__d("BarcelonaProfileThreadsTabRefetchableDirectQuery_threadsRelayOperation", …)`，值是 `29427891256864466`。同一個 bundle 裡 query 定義寫的是 `params:{id:n("BarcelonaProfileThreadsTabRefetchableDirectQuery_threadsRelayOperation"), …}`，也就是網頁送分頁 query 時用的 doc_id 就是這個模組的值。
5. 用 Playwright 手機視窗（§2 的設定）開同一頁對照：載入後、捲動前就下載了同名 bundle，頁面裡 `require("BarcelonaProfileThreadsTabRefetchableDirectQuery_threadsRelayOperation")` 回 `29427891256864466`，捲動 8 次後不變，和步驟 4 一致。這次網頁自己沒有送分頁 query（和 §11 的不穩定一致），所以「瀏覽器實際送出的 doc_id」是用 require 的值對照，沒有攔到實際請求。

實打 GraphQL（token 由無頭瀏覽器取一次，請求本身不帶 cookie，只帶 §3 的 4 個 x-header）：

| doc_id | variables | 結果 |
|---|---|---|
| `29427891256864466`（今天從 JS 取得） | userID、after=null、first=10，不帶 `__relay_internal__pv__*` | 200，`execution error`，`data: null` |
| `39551525044493602`（10-29 的值） | 同上 | 200，`execution error` |
| `29427891256864466` | 加上 query 定義列出的 52 個 `__relay_internal__pv__*` 旗標（值全部取自 HTML 預排 query 的 variables，52/52 都找得到，19 個 true） | 200，10 串、20 篇三欄齊全（code、taken_at、全文），壓縮後 62,230 B、解壓後 547,052 B；第一篇 code 也在 HTML 裡 |
| `39551525044493602` | 同上加旗標 | 200，`execution error` |

結論：

- 「doc_id 要從網頁的 JS 取得」成立，而且不需要瀏覽器：HTML → 解開 JS 網址 → 下載 bundle → 用上面的 `__d(...)` 樣式取值。今天要下載 663 個檔案才找到；實作可先只找網址裡含已知 bundle 名的檔案，但 bundle 名會隨版本變，最穩的是全找。
- 舊 doc_id 會失效：10-29 的值今天回 `execution error`。所以 doc_id 要定期重新取得，並以「回應沒有 `data.mediaData`」當作要更新的訊號。
- 只換 doc_id 不夠，`__relay_internal__pv__*` 旗標也要帶，名單來自 query 定義（今天 52 個，10-29 是 45 個），值可從 HTML 取。§3 拿掉的是 body 欄位與 header，沒有測過拿掉這些旗標。
- 今天 zuck 分頁 GraphQL 第 1 頁的回應壓縮後（gzip 線路位元組，不是 HTML）62.2 KB，10-29 是 38.6 KB（§6）。重測與成因見 §13：主要是 zuck 這 10 串的內容變大，平均值與成本沒有明顯變化。
- 請求量：threads.com 純 HTTP 1 次（HTML）；無頭瀏覽器開頁 3 次；GraphQL 直接呼叫 4 次。static.cdninstagram.com 的 JS 約 1,300 次（步驟 3、4 分開下載）。出口為個人網路 IP，沒有遇到 401。

## 13. GraphQL 第 1 頁傳輸量重測（2026-11-15）

§12 看到 zuck 的 GraphQL 第 1 頁回應（不是 HTML）從 38.6 KB 變成 62.2 KB，成本段用的 27.8 KB 平均值需要重測。10-29 那 102 次的原始資料沒有存下來，27.8 KB 怎麼平均的（例如 20 次 401 有沒有算進去）無法回頭確認，所以這次重新量，以這次的數字為準。

條件：doc_id `29427891256864466`、`after=null`、`first=10`、帶 52 個旗標；token 由無頭瀏覽器取一次，請求不帶 cookie；個人網路 IP、每 3 秒 1 次。帳號是量測程式預設的 7 個大帳號，加上這 7 個回應裡出現的另外 5 個帳號（不列名稱）。結果在 `q4/data/threads_q4_out/docid_2026-11-15/size_rerun.json`。

| 帳號 | 壓縮後 | 解壓後 | 串 | 篇 |
|---|---|---|---|---|
| zuck | 61,420 B | 543,652 B | 10 | 20 |
| nasa | 46,591 B | 392,278 B | 10 | 12 |
| 其他 5 | 36,073 B | 298,232 B | 10 | 11 |
| natgeo | 30,187 B | 226,731 B | 10 | 13 |
| 其他 1 | 27,383 B | 258,759 B | 10 | 10 |
| nba | 25,532 B | 239,294 B | 10 | 10 |
| instagram | 20,813 B | 196,344 B | 10 | 10 |
| 其他 2 | 20,476 B | 178,520 B | 10 | 10 |
| mosseri | 19,730 B | 168,966 B | 10 | 11 |
| nytimes | 15,446 B | 128,915 B | 10 | 10 |
| 其他 4 | 6,941 B | 74,221 B | 10 | 10 |
| 其他 3 | 5,861 B | 66,734 B | 10 | 10 |

- 12/12 都是 200、10 串；137 篇裡 136 篇三欄齊全。
- GraphQL 第 1 頁回應壓縮後（不含 HTML）：平均 26,371 B（26.4 KB），中位數 23,173 B，範圍 5.9–61.4 KB。7 個大帳號平均 31.4 KB，另外 5 個平均 19.3 KB。
- 上傳 body：每次 4,637 B（4.6 KB），比 10-29 的 4.08 KB 多，因為旗標從 45 個變成 52 個。
- 成因：同一刻打 zuck，旗標照 HTML 值是 62,227 B，全部設 false 是 59,570 B，只差 4%；完全不帶旗標則是 `execution error`。所以變大的主因是內容：今天 zuck 這 10 串含 20 篇（有串內回覆），10-29 的內容不同。同帳號比 10-29（§6）也是有增有減：zuck 38.6 → 61.4 KB、nba 36.9 → 25.5 KB、nytimes 14.3 → 15.4 KB。

重新計算成本（每月 4,800 萬次，TLS 加 18% 仍是估計）：

| | 每次 | 每月 | 含 TLS | IPRoyal $1.84/GB | Oxylabs $2.5/GB |
|---|---|---|---|---|---|
| 10-29（設計摘要用的） | 27.8 ＋ 4.08 ＝ 31.9 KB | 1.53 TB | 1.81 TB | US$2,816–3,322 | US$3,826–4,514 |
| 11-15 重測 | 26.4 ＋ 4.6 ＝ 31.0 KB | 1.49 TB | 1.76 TB | US$2,739–3,232 | US$3,721–4,391 |

結論：

- 每次請求總量少 3%，月流量仍是約 1.5 TB、含 TLS 約 1.8 TB，設計摘要瓶頸三的 US$2.8–3.3k 與 US$3.8–4.5k 在四捨五入範圍內仍成立。
- 單一帳號差異很大（5.9–61.4 KB，約 10 倍），平均值取決於 40 萬帳號的組成。這次樣本偏大帳號（12 個裡 7 個是名人／機構），不能代表一般帳號；一般帳號若較小，實際流量會低於估計。
- 請求量：threads.com 純 HTTP 7 次（HTML 取 userID）、無頭瀏覽器開頁 1 次、GraphQL 15 次，沒有遇到 401。

## 14. 單一 IP 被封後的解封時間重測（2026-11-16）

環境：測試機走 iPhone 個人熱點（區網 172.20.10.3），對外 IP 全程 39.10.64.xxx（電信業者 NAT，可能多人共用）；cmd `ipconfig /all`、`curl api.ipify.org` 與 DevTools 讀到的 ipify 回應都已截圖。原始紀錄：docid_2026-11-15 的 rate_ladder.log、rate_curve.log、unblock_probe.log。

| 時間 | 動作 | 結果 |
|---|---|---|
| 04:08 | 開測前 1 次 | 200 |
| 04:08–04:09 | 1 req/s 連發 | 前 50 次 200，第 51 次起 401（本文 119 B），連續 8 次 401 即停；HTML 頁仍 200、貼文照常 |
| 04:09:43 | 封後 22 秒 1 次 | 200，緊接著 3 次都是 401 |
| 04:10–04:30 | 完全不送，休息 30／60／120／300／600 秒後各以 2 req/s 連發 | 6、6、6、6、7 次，0 次成功 |
| 04:30–04:56 | 每 2 分鐘 1 次 | 13 次全 401 |
| 04:58:52 | 同上 | 200（被封約 49 分鐘後第一次） |
| 05:00 | 7 次（每 3 秒 1 次，DevTools 實拍） | 全 401 |
| 05:15 | 7 次（每 3 秒 1 次，DevTools 實拍） | 全 401（距封鎖約 66 分鐘） |
| 05:54 | 7 次（每 3 秒 1 次，DevTools 實拍），對外 IP 仍是 39.10.64.xxx | 全 401（距封鎖約 105 分鐘） |
| 05:56 | 熱點開關飛航換出口 IP（39.10.64.xxx → 39.10.24.xxx），同一台機器、同一批請求 | 立即 200（7 次全 200），證實封鎖綁 IP |
| 05:57–06:00 | 7 個大帳號第 1 頁 query，DevTools 實拍（evidence/size_list_dev.png、size_hdr*_dev.png） | Size 7.7～41.1 kB（合計 174 kB、平均約 24.8 kB）、請求 Content-Length 4637 B |

結論與限制：

- 1 req/s 約 50 次就被封，比 10-29 的 5 req/s 約 82 次更少；換 token 無效、HTML 照常 200（與 §7 一致）。
- 「5 分鐘未解」的寫法偏樂觀：休息 10 分鐘仍 0 次成功，約 49 分鐘才出現 1 次 200，隨後又 401。
- 單 IP 可長期撐住的速率仍沒有量到；觀察是封後 1 小時內平均不到 1 次成功，但我們自己的探測請求可能延長封鎖，數字只能當參考。
- 單一 IP、單一電信 NAT，樣本只有 1 個；手機未登入的行動網路／已登入 App 是否同樣被封沒有測。
- 這些測試期間測試機的 GraphQL 被封，HTML 不受影響。

補充（2026-11-16，壓縮格式）：同一個 zuck 第 1 頁 query，展開後 JSON 約 547 KB；requests（Accept-Encoding gzip, deflate）傳輸 61,410 B，Chromium 協商 zstd 傳輸約 39～41 KB。完整新版 Chromium（channel chromium／chrome）以 CDP 開 F12 時曾出現沒壓縮的 550 kB 傳輸，改用預設 headless shell 後穩定為壓縮；Size 欄的數字以壓縮後為準。成本段的 27.8 KB（10-29）、26.4 KB（11-15）、24.8 KB（11-16）都是壓縮後傳輸量。

補充（2026-11-16，TLS 握手實測）：用 tshark 抓 3 條「每次新建連線」（new Session，模擬輪換 IP 代理）的 threads.com TLS 1.3，Wireshark 開抓到的封包檔（tcp.stream eq 3）截圖為 `evidence/tls_ws.png`（封包檔含網卡位址，公開版只放截圖，截圖已遮網卡位址）。握手：ClientHello 517 B、伺服器首包 2784＋746＝3530 B、CCS＋Finished 64 B，合計約 4.1 KB；加 session ticket 約 184 B、close_notify 約 23 B，每條新連線約 4.2～4.3 KB，約佔每個請求（30～33 KB）的 13～14%，低於設計摘要用的估計 18%（約 5.6 KB）；以 13.5% 重算約 1.7 TB（設計摘要寫 1.8 TB 為估計上緣）。未量 CONNECT 代理那一段；若代理商按 L3 封包計費再多約 3%。只有每次請求都換 IP 才每次付握手，同一連線重用則幾乎為零。
