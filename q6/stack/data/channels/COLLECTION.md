# Q6 六渠道公開貼文樣本：收集紀錄

給 Q6 斷詞 pipeline 測試用的公開貼文，6 個渠道：論壇（PTT）、Threads、YouTube、媒體，加上 Facebook、Instagram（含 Reels）。Facebook、Instagram 在本機上抓（資料中心 IP 會被 Meta 擋），見文末一節。

- 抓取時間：2026-10-31 01:31–01:41（+08:00），全部未登入，使用桌面 Chrome UA。
- 不存 cookie、token、cursor。Threads 只留抽出的欄位，不存 HTML。
- 每個網站兩次載入至少間隔 2 秒（程式用 2.5 秒），每個網站最多 30 次載入。
- 欄位固定 9 個：`post_id, channel, sub_type, source, url, published_at, fetched_at, text, collect_method`。
- `text` 保留原文，網址、電話、表情符號、簽名檔、`※ 發信站` 都沒清，清理交給 pipeline。只有 RSS 的 description 去掉了 HTML 標籤。
- PTT 帳號是公開資訊，沒有遮。

## 總表

| 渠道 | 檔案 | 篇數 | 含網址 | 含短網址 | 含電話樣式 | published_at 範圍 | 方法 |
|---|---|---:|---:|---:|---:|---|---|
| 論壇 PTT | `ptt.jsonl` | 48 | 38 | 3 | 2（1 篇是誤判） | 2026-01-02 23:00 ~ 2026-10-31 01:30 | html 23 篇，rss 25 篇（Atom 摘要） |
| Threads | `threads.jsonl` | 61 | 10 | 0 | 0 | 2026-10-30 13:37 ~ 2026-10-31 00:56 | inline_json 61 篇 |
| YouTube | `youtube.jsonl` | 45 | 43 | 28 | 0 | 2026-10-30 20:19 ~ 23:49 | rss 45 篇 |
| 媒體 | `media.jsonl` | 61 | 0 | 0 | 0 | 2026-10-30 13:13 ~ 22:46 | rss 61 篇 |

正規式和 `q6/code/segmenter.py` 前處理用的一樣：

- 網址：`URL_RE`
- 短網址：`SHORT_URL_RE`，網域清單為 bit.ly、reurl.cc、pse.is、tinyurl.com 等
- 電話：`MOBILE_RE` 或 `PHONE_RE`

表中數字是「至少命中一次的篇數」，明細在 `_meta/stats.json`，用 `collect/stats.py` 產生。

## 論壇 PTT（`ptt.jsonl`，48 篇）

**收錄內容**
- 6 張雜訊截圖背後的文章（`q6-ptt-noise-01`～`06`）放在最前面。03 和 04 是同一篇，所以實際是 5 篇：
  - Kaohsiung：M.1793264859.A.8CF、M.1793281424.A.0CF、M.1793291047.A.07A
  - Lifeismoney：M.1767366026.A.B74、M.1793182709.A.DC3
- 再從 3 個板的公開 Atom feed 取最新文章：`https://www.ptt.cc/atom/Kaohsiung.xml`、`/atom/Lifeismoney.xml`、`/atom/Gossiping.xml`。

**各板篇數**

| 板 | 合計 | html（含雜訊文章） | rss |
|---|---:|---:|---:|
| Kaohsiung | 20 | 11 | 9 |
| Lifeismoney | 21 | 9 | 12 |
| Gossiping | 7 | 3 | 4 |

**方法**
- html 篇：`text` = 標題 + `\n` + `#main-content` 內文。內文拿掉作者／標題／時間列和推文列（`div.push`），其餘照原樣，包括 `※ 發信站`、`※ 文章網址`、`※ 編輯`、簽名檔和網址。
- rss 篇：`text` = 標題 + Atom `<content>`。PTT 的 Atom 每篇只給前幾行，所以 rss 篇是**截斷的開頭**。沒有全部改抓 HTML，是因為每站上限 30 次載入。
- Gossiping 的 `over18=1` 只放在那幾次請求的 Cookie 標頭，沒有寫入檔案，session 也沒有保留。
- `sub_type` 一律是 `article`，`source` 是板名。

**載入次數**：www.ptt.cc 共 29 次。手動測試 2 次，程式 27 次（feed 3 次 + 文章 24 次）。

**失敗**：`https://www.ptt.cc/bbs/Gossiping/M.1793381090.A.40A.html` 回 **404**，文章已刪除，沒有收錄（Atom 摘要也不補）。

**雜訊觀察**
- 網址主機：i.imgur.com 133 個、i.mopix.cc 24 個、ptt.cc 22 個、i.meee.com.tw、i.verb.tw、myppt.cc 等。
- 短網址：reurl.cc 2 篇（共 3 個）、bit.ly 1 篇。
- noise-05 截圖裡的 `tinyurl.com` 和被推文斷行切開的網址都在推文列。依規則推文不收，所以 `text` 裡沒有這兩項。
- 電話樣式 2 篇：
  - `Kaohsiung/M.1793291047.A.07A`：商家自家宣傳的手機號碼。
  - `Lifeismoney/M.1793168867.A.ECB`：**誤判**。`PHONE_RE` 把引言列「※ 引述《a05079944 (BACOBOO)》」中的帳號 `a05079944` 當成電話，因為前面是字母不是數字。這是 pipeline 可以拿來測的反例。

## Threads（`threads.jsonl`，61 篇）

**帳號**：@cnanews_tw（中央社）、@pts.news（公視新聞網）、@ftvnews（民視新聞）。

- 帳號是用網頁搜尋找到的，再抓個人頁確認存在。
- @news.ftv 是民視的舊帳號，最後貼文在 2026-06，所以改用本尊 @ftvnews。

**方法**：和 `q4/code/threads_q4_measure.py` 相同，讀個人頁 HTML 裡 `<script type="application/json" data-sjs>` 的內嵌 JSON。同時有 `code` 和 `taken_at` 的物件就是一篇，取以下欄位：

| 輸出欄位 | 來源 |
|---|---|
| `text` | `caption.text` |
| `published_at` | `taken_at` |
| `post_id` | `code` |
| `url` | `https://www.threads.com/@<帳號>/post/<code>` |

- 請求帶 `Sec-Fetch-Mode` 和 `Sec-Fetch-Dest` 導覽標頭。Q4 實測沒帶這兩個會拿到空殼頁。
- 未登入時個人頁只內嵌 5～8 篇，所以再開其中有回覆的 11 個貼文頁。只有 11 篇本人主貼文的 `direct_reply_count` > 0。

**分類規則**

| 內容 | 處理 |
|---|---|
| 本人貼文 | `sub_type=post`，19 篇 |
| 其他使用者的公開回覆（`text_post_app_info.is_reply` 為真） | `sub_type=comment_thread`，42 篇；`source` 記被回覆的帳號，`url` 用回覆者帳號 |
| 貼文頁下方的「相關貼文」 | 不收 |

**各帳號篇數**：@pts.news 39、@cnanews_tw 14、@ftvnews 8。

**頁面清單**：`https://www.threads.com/@cnanews_tw`、`/@pts.news`、`/@ftvnews`，另有 11 個 `/@<帳號>/post/<code>`。全部 HTTP 200，每頁抽到幾篇記在 `_meta/threads_pages.json`。

**載入次數**：www.threads.com 共 23 次。手動測試 7 次，程式 14 次，截圖 2 次。

**失敗**：無。

**雜訊觀察**
- 至少 13 篇含 emoji（以 U+1F300–1FAFF 範圍計）。
- 10 篇含網址：news.pts.org.tw、cna.com.tw、ftvnews.com.tw、youtube.com/live、facebook.com。
- 同一使用者 @togetherplay 對 @pts.news 貼了 3 次同一段「為了提倡正確直敘標題…」，另有 1 個帳號同文貼 2 次。

## YouTube（`youtube.jsonl`，45 篇）

**頻道 ID 來源**：開公開 @handle 頁，讀 `"externalId"`。

| 頻道 | @handle 頁 | channel_id |
|---|---|---|
| TVBS NEWS | `https://www.youtube.com/@TVBSNEWS01` | `UC5nwNW4KdC0SzrhF9BXEYOQ` |
| 公視新聞網 | `https://www.youtube.com/@PNNPTS` | `UCexpzYDEnfmAvPSfG4xbcjA` |
| 東森新聞 CH51 | `https://www.youtube.com/@newsebc` | `UCR3asjvr_WAaxwJYEDV_Bfw` |

**RSS**：`https://www.youtube.com/feeds/videos.xml?channel_id=<id>`，3 個都是 HTTP 200，每個 15 支，共 45 支。

**方法**：`text` = 影片標題 + `\n` + `media:description`，照原樣保留。`sub_type=video`，`source` 是頻道名稱。

**載入次數**：www.youtube.com 共 9 次。手動測試 1 次，程式 6 次，截圖 2 次。

**失敗**：Playwright 開影片頁 `https://www.youtube.com/watch?v=9qqyUi2Npmc` 回 **429**，被導到 google.com/sorry 的 reCAPTCHA。沒有繞過，截圖留存為 `youtube_watch_429_recaptcha.png`，之後改拍公開頻道頁（200）。

**雜訊觀察**：說明欄的固定宣傳區塊重複出現很多次。
- `tvbs.pse.is` 90 個、`tvbs.to` 30 個、`pse.is` 26 個、facebook／instagram／t.me 各約 15 個。
- `tvbs.to`（15 篇）**不在** `segmenter.py` 的短網址清單裡，只會被一般 `URL_RE` 清掉。

## 媒體（`media.jsonl`，61 篇）

| feed | 狀態 | feed 內則數 | 收錄 |
|---|---|---:|---:|
| `https://feeds.feedburner.com/rsscna/politics`（中央社 國內政治） | 200 | 20 | 16 |
| `https://feeds.feedburner.com/rsscna/social`（中央社 社會） | 200 | 20 | 16 |
| `https://feeds.feedburner.com/rsscna/local`（中央社 地方） | 200 | 20 | 13（另 3 則 guid 與前兩個 feed 重複，已去重） |
| `https://news.pts.org.tw/xml/newsfeed.xml`（公視新聞網，Atom） | 200 | 25 | 16 |
| `https://news.ltn.com.tw/rss/all.xml`（自由時報） | **403** | — | 0 |

**方法**
- `text` = 標題 + `\n` + description／summary。只有 description 去 HTML 標籤並還原 HTML 實體。
- 標題照原樣，所以有 1 則標題含字面上的 `&quot;`（公視：台南&quot;聽見寂靜&quot;）。
- `sub_type=news`，`source` 是 feed 代號，例如 `cna/politics`。每個 feed 最多取 16 則。

**載入次數**

| 網站 | 次數 | 組成 |
|---|---:|---|
| feeds.feedburner.com | 4 | 手動 1 次、程式 3 次 |
| news.pts.org.tw | 2 | 手動 1 次、程式 1 次 |
| news.ltn.com.tw | 3 | 手動 curl 1 次、程式 1 次、手動重試 1 次 |
| www.cna.com.tw | 1 | 截圖 |

**失敗**：自由時報 `all.xml` 用 curl 測試時回 200，但收集程式（python requests）回 **403**（nginx）。把 Accept 改成 RSS 類型再試一次，仍是 403。沒有再嘗試其他方式，所以媒體渠道沒有自由時報。

**雜訊觀察**：RSS 摘要是編輯過的新聞導言，網址 0 篇、電話 0 篇，是 4 個渠道裡最乾淨的。

## 截圖（`q6/evidence/stack/channels/`，1440×900，非整頁，未登入）

| 檔案 | 網址 | 狀態 |
|---|---|---|
| `youtube_pnnpts_channel.png` | https://www.youtube.com/@PNNPTS/videos | 200 |
| `media_cna_article.png` | https://www.cna.com.tw/news/aloc/202610300316.aspx | 200 |
| `threads_pts_news.png` | https://www.threads.com/@pts.news | 200 |
| `youtube_watch_429_recaptcha.png` | https://www.youtube.com/watch?v=9qqyUi2Npmc | 429（失敗紀錄） |

- Threads 的登入邀請框是按 Esc 關掉後才拍的。
- PTT 的雜訊截圖本來就在 `q6/evidence/playwright/`，這次沒有重拍。
- 截圖紀錄在 `screenshots.json`。

## 程式（`q6/stack/collect/`）

```bash
cd q6/stack/collect
python3 collect_ptt.py --used-before 0      # 論壇
python3 collect_threads.py --used-before 0  # Threads（不支援快取，不留 HTML）
python3 collect_youtube.py --used-before 0  # YouTube
python3 collect_media.py                    # 媒體
python3 stats.py                            # 統計與欄位檢查 → _meta/stats.json
node screenshots.js [名稱…]                 # 截圖（Node Playwright）
```

| 檔案 | 用途 |
|---|---|
| `common.py` | 每站計時（2.5 秒）、30 次上限、每次請求後清掉 cookie、寫 `_meta/fetch_log.jsonl` |
| `_meta/fetch_log.jsonl` | 只記網址、狀態碼、位元組、時間，不記標頭 |

- `--used-before` 用來把這次之前已經用掉的載入次數算進上限。本次執行時填的是 PTT 2、Threads 7、YouTube 1。
- `--cache DIR` 只在開發時用，把原始回應暫存在 repo 外，重跑解析時不必再連線。Threads 沒有這個選項。
- 手動測試的請求記在 `_meta/manual_probes.json`。
- 每次重跑都會抓到當下最新的文章，所以內容和篇數會不同。

## Facebook、Instagram（`facebook.jsonl` 4 篇、`instagram.jsonl` 48 篇）

- 抓取時間：2026-10-31 01:46（+08:00），在本機 Windows 電腦用 `collect/capture_q6_fb_ig.py` 跑（住宅網路；資料中心 IP 會被 Meta 導去登入頁）。
- 全部未登入。登入邀請框只按 Esc／不點，不輸入任何帳密；不存 cookie、token、cursor，也不存 HTML，只留從頁面內嵌 JSON 抽出的欄位。
- 每個網址載入一次：FB 4 次、IG 8 次（4 個帳號各自的主頁與 Reels 頁），紀錄在 `_meta/capture_q6_fb_ig_log.txt`。

| 渠道 | 來源 | 篇數 | sub_type | 含網址 | published_at |
|---|---|---:|---|---:|---|
| Facebook | 中央社、公視新聞網、民視新聞、NASA 粉專 | 4（每頁未登入只給 1 篇） | post 4 | 2 | 2026-10-30 05:45 ~ 2026-10-31 01:30 |
| Instagram | @cnanews_tw、@pts.news、@ftvnews、@nasa | 48（每個帳號 12 篇） | post 26、reel 22 | 4 | 沒有：未登入的主頁 JSON 不帶發文時間，`month` 會是 `unknown` |

- IG 的 Reels 頁未登入時是 0 篇；`reel` 是從主頁 12 篇裡 `product_type = clips` 判斷出來的。
- Facebook 未登入只拿到每頁最上面 1 篇，所以 Facebook 樣本偏少；NASA 那篇是英文，剛好測到英文虛詞（and、of、in）會進文字雲，見 `q6/stack/RESULTS.md`。
- 截圖：`q6/evidence/stack/channels/fb_*.png`、`ig_*.png`。
