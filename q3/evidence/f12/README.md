# Q3 爬蟲設計依據：瀏覽器 F12 Network 截圖

PTT 八卦板（Gossiping）爬蟲的每個設計決定，都先在瀏覽器開發者工具（F12 → Network，勾選 Preserve log）實際觀察過才寫進程式。
截圖條件如下：

- 全新暫存設定檔、有畫面的 Chrome、繁體中文介面。
- 每次換頁至少間隔 5 秒，與爬蟲的節流設定一致。
- Cookie 欄位只保留 `over18=1` 可見；Cloudflare 與分析用 Cookie 的值（`cf_clearance`、`__cf_bm`、`_ga` 等）一律模糊處理。

同一批頁面的原始 HTML 存在 [`../../pipeline/tests/fixtures/real/`](../../pipeline/tests/fixtures/real/)，供解析器單元測試使用，抓取時間與狀態碼記在 `SOURCES.json`。

| # | 截圖 | 看到什麼 | 對應的爬蟲設計 |
|---|------|----------|----------------|
| 1 | [`01_over18_gate_f12.png`](01_over18_gate_f12.png) | 首次進入看板時出現「我同意，我已年滿十八歲」的確認頁。 | **over18 Cookie**：實測不帶 Cookie 直接請求看板，伺服器同樣回 200、不轉址（記錄於 `SOURCES.json`），年齡確認屬前端流程；爬蟲固定帶 `over18=1`，取得與已同意使用者相同的頁面，不依賴前端行為。 |
| 2 | [`02_over18_accept_f12.png`](02_over18_accept_f12.png) | 按下同意後 `POST /ask/over18` 回 302，`Location: /bbs/Gossiping/index.html`，回應標頭 `Set-Cookie: over18=1; Path=/`。 | **over18 Cookie**：確認伺服器唯一設定的就是 `over18=1`（Session Cookie，無到期時間），爬蟲只需在請求帶上這個值，不需要模擬點擊或保存登入狀態。 |
| 3 | [`03_index_newest_f12.png`](03_index_newest_f12.png) | `GET /bbs/Gossiping/index.html` 回 200；Request Headers 的 Cookie 含 `over18=1`；頁面可見分隔線與下方置頂公告。 | **最新頁解析**：最新頁的置頂文章位於分隔線（`r-list-sep`）之後，解析時以分隔線切開，置頂文不列入一般文章，避免每輪重複入庫。 |
| 4 | [`04_index_full_20_slots_f12.png`](04_index_full_20_slots_f12.png) | `index38958.html` 回 200（22.8 kB、324 ms），一頁完整 20 個位置，其中一格為「(本文已被刪除)」。為一次呈現 20 格，頁面縮放 0.5、開發者工具停靠右側。 | **每頁 20 格**：非最新頁固定 20 格；已刪除文章仍佔位但沒有連結，解析器以「有無連結」判斷，不能用列數推算文章數，也不能把刪除格當成解析錯誤。 |
| 5 | [`05_article_pushes_f12.png`](05_article_pushes_f12.png) | 單篇文章 `M.1793703227.A.462.html` 回 200，約 27.9 kB、319 ms；畫面捲到推文區，可見推／→／噓、帳號、IP 與時間。 | **單篇大小與延遲**：一篇約 28 kB、300 ms 級，作為吞吐估算與逾時設定的基準；推文欄位結構即推文解析器的輸入格式。 |
| 6 | [`06_article_deleted_404_f12.png`](06_article_deleted_404_f12.png) | 已刪除文章 `M.1792887225.A.5F3.html` 回 404（2.7 kB），頁面只有「404 - Not Found.」。 | **刪文 404**：回補時遇到 404 視為「文章已刪除」並標記狀態，不重試、不當成網路錯誤，也不刪除已入庫的歷史資料。 |
| 7 | [`07_prev_page_f12.png`](07_prev_page_f12.png) | 在最新頁點「‹ 上頁」，Network 依序出現 `index.html` → `index38963.html`，網址列同步變為 `index38963.html`。 | **翻頁**：「上頁」連結直接指向 `index{N-1}.html`，爬蟲從最新頁讀出 N 後以頁碼遞減往回抓，不需要依賴瀏覽器狀態。 |

## 對應的測試資料

| 檔案 | 來源 | 用途 |
|------|------|------|
| `index_newest.html` | `/bbs/Gossiping/index.html`（200） | 分隔線與置頂文切分 |
| `index_full.html` | `/bbs/Gossiping/index38958.html`（200，20 格，1 格刪除） | 20 格解析、刪除格處理 |
| `article_pushes.html` | `/bbs/Gossiping/M.1793703227.A.462.html`（200，推文數 45） | 內文與推文解析 |
| `article_404.html` | `/bbs/Gossiping/M.1792887225.A.5F3.html`（404） | 刪文判斷 |
| `over18.html` | `/ask/over18?from=...`（200） | 年齡確認頁辨識 |
| `SOURCES.json` | — | 每個檔案的網址、狀態碼、位元組數、抓取時間；另記錄不帶 Cookie 的看板請求（200、無轉址）與 `POST /ask/over18`（302、`Set-Cookie: over18=1; Path=/`） |
