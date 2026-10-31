# Playwright 證據第二輪（2026-10-30，給 UIUX 總架構圖與 GitHub 整理）

原則：已有的不重拍，只補證據不足處；全部未登入、只開公開頁，不存 cookie／token／cursor，FB／IG／Threads 不存整頁 HTML。
一般使用者的帳號、頭像、IP 一律黑框遮住。主張、結果、來源網址與截取時間見 [`shared/capture/capture_manifest.json`](../capture/capture_manifest.json)（`round: round2-2026-10-30` 的條目）。

拍攝環境有兩個，差別本身就是證據：

- **雲端容器**（資料中心出口 IP）：`shared/capture/capture_round2_cloud.js`（Node＋Playwright）。PTT、Threads 照常開得到；FB、IG 直接被導去登入頁。
- **本機**（住宅 IP）：`shared/capture/capture_round2_residential.py`（Python＋Playwright 1.58）。FB、IG 只能在這裡拍。

## 新增的截圖

| 題號 | 架構節點 | 檔案 | 證明什麼 | 環境 |
|---|---|---|---|---|
| Q2 | 解析（parser） | [`q2/evidence/playwright/fb_post_time_and_ids.png`](../../q2/evidence/playwright/fb_post_time_and_ids.png) | 畫面只有相對時間（14h），頁面內嵌資料有 `creation_time` 1793310300（2026-10-30 05:45 台北），兩者一致：發文時間可以存絕對時間，不必解析「14h」 | 住宅 IP |
| Q2 | 平台 | [`q2/evidence/playwright/fb_page_transparency_id.png`](../../q2/evidence/playwright/fb_page_transparency_id.png) | 粉專透明度頁寫著 **Page ID 54971236771**（建立日 2009-03-04）。貼文頁資料欄位與 app-link 只出現 100044561550831（userID／actorID／fb://profile），兩者不同：「粉絲頁 ID」欄位要存 Page ID | 住宅 IP |
| Q2、Q4(b) | 抓取 | [`q2/evidence/playwright/fb_cloud_ip_login_redirect.png`](../../q2/evidence/playwright/fb_cloud_ip_login_redirect.png) | 雲端 IP 開 FB 粉專：HTTP 302 → `/login/`，拿不到任何貼文欄位。100 萬則貼文不能靠雲端爬網頁，要走 Graph API 或有授權的資料源 | 雲端 |
| Q5、Q4(b) | 抓取 | [`q5/evidence/playwright/ig_reels_cloud_ip_login_redirect.png`](../../q5/evidence/playwright/ig_reels_cloud_ip_login_redirect.png) | 雲端 IP 開 IG Reels：302 → 登入頁本身回 **HTTP 429**，頁面空白（當天第 5 次請求，前 4 次相同）。Reels 爬蟲要走住宅代理，不能直接從 Cloud Run 出口抓 | 雲端 |
| Q5 | 平台／SLA | [`q5/evidence/playwright/ig_reels_play_count_drift.png`](../../q5/evidence/playwright/ig_reels_play_count_drift.png)（數值 [`ig_reels_play_count_drift.json`](../../q5/evidence/playwright/ig_reels_play_count_drift.json)） | 同一批 9 支 Reels 隔 4.6 小時（11:05 → 15:39）再抓 `play_count`：最新一支 +323,431（+29.3%），舊的 +0.02%～+0.56%。觀看數發佈後持續漲、越新漲越快：1 小時 SLA 只管第一筆，之後要依發佈時間排程重抓 | 住宅 IP |
| Q5 | 平台／SLA | [`q5/evidence/playwright/threads_post_repost_drift.png`](../../q5/evidence/playwright/threads_post_repost_drift.png) | 同一則 Threads 貼文三次抓取：讚 148 → 266 → 288、轉發 16 → 22 → 22、瀏覽 5.5K → 9.1K → 10.1K。且 **畫面轉發 22 ＝ JSON repost_count 18 ＋ quote_count 4**，同一張圖裡看得到 | 雲端 |
| Q6 | 斷詞（tokenizer） | [`q6/evidence/playwright/ptt_noise_05.png`](../../q6/evidence/playwright/ptt_noise_05.png) | PTT 省錢板：推文裡的 `tinyurl.com` 短網址；另有一則網址被推文換行切成「http」＋「s://i.mopix.cc/…」兩段，前處理要先把同一人連續推文接回再比對網址 | 雲端 |
| Q6 | 斷詞（tokenizer） | [`q6/evidence/playwright/ptt_noise_06.png`](../../q6/evidence/playwright/ptt_noise_06.png) | PTT 省錢板：`bit.ly` 短網址。加上既有的 reurl.cc，短網址至少 3 種網域，要用網域清單＋路徑規則整段移除 | 雲端 |

## 重拍或補遮的既有截圖

| 檔案 | 做了什麼 |
|---|---|
| [`q3/evidence/playwright/ptt_article_comments.png`](../../q3/evidence/playwright/ptt_article_comments.png) | 原圖露出一般使用者帳號與 IP（總架構圖有用到這張）。同一篇文章重拍：帳號 261 個、IP 262 處黑框，捲到第一則「噓」讓推／噓／→ 都在畫面裡 |
| `q4/evidence/playwright/threads_post_url.png`、`q5/evidence/playwright/threads_post_repost.png` | 回覆區一般使用者的帳號與頭像補黑框（像素遮罩，其他未動） |
| `q5/evidence/playwright/ig_reel_page.png` | 右側留言區露出的帳號尾段補黑框（像素遮罩，其他未動） |

## 稽核發現（6 個代理逐張看過既有截圖），UIUX 與 GitHub 整理請照這裡改說明文字

圖說和圖對不上的地方（manifest 已加 `correction` 欄位）：

- **Q2 `fb_post_nasa.png`：** 「心情 79」與「4h」是第一則留言的數字，不是貼文。總架構圖 Q2 的圖說要改；貼文層級的心情與分享數改用 `fb_post_time_and_ids.png`（16K 心情、846 分享）。
- **Q2 `fb_page_nasa.png`：** 圖上只有粉專名稱可讀，心情數與發文時間被登入視窗蓋住。
- **Q5 `ig_reels_grid.png`：** 縮圖觀看數在圖中被裁掉；q5/README 還寫 `video_view_count`，實際欄位是 `play_count`。
- **Q5 `threads_post_repost.png`：** 畫面「轉發 16」是 repost＋quote，不是單獨的 `repost_count`。新圖 `threads_post_repost_drift.png` 直接示範 22＝18＋4。
- **Q5 `a-dataflow/threads-repost-equals-repost-plus-quote.png`：** 圖上只看得到 60，35 與 25 只在 FigJam 手打的 JSON 裡。要「同一張圖看到兩邊」請改用 `threads_post_repost_drift.png`。
- **Q6：** 總架構圖把 `ptt_noise_02.png` 標成「電話號碼（已遮）」，但電話在 `ptt_noise_04.png`，02 只有地名「鼓山區」。q6/README 說 4 張截圖涵蓋「助詞、地名被拆」，實際沒有任何一張標助詞，也沒有一張證明「高雄市」被拆（jieba 預設字典本來就保留「高雄市」）。
- **Q4 `threads_profile_zuck.png`：** README「畫面可見 10 篇」不對，畫面只看得到 1 篇，10 是 DOM 計數。`threads_graphql_desktop.png` 的數字全在說明卡上，畫面上看不到請求本身。

Q3 的 PTT 截圖：PTT 是公開資料，10/30 決定帳號不用遮，`q3/answer/` 與 `q3/evidence/playwright/` 的 PTT 截圖維持原圖，不重拍。

## 已知限制

- FB 與 IG 的頁面一律有登入／註冊視窗，照實截下、沒有點擊；`fb_page_transparency_id.png` 的 Page ID 在視窗下方仍可讀。
- `ig_reels_play_count_drift` 的 JSON 沒有 `taken_at`，「越新漲越快」是以 Reels 分頁排序（最新在最左上）推論。
- Threads 貼文頁的「分享」按鈕被登入視窗蓋住，分享數取自 DOM 與 JSON（reshare_count 1）。
- 雲端 IP 被擋只測了這一個容器的出口 IP，不代表所有雲端供應商；但與 Q4 題目前提（主流雲端 IP 段多被列黑名單）一致：同一 IP 對 FB、IG 被擋，對 Threads 單頁仍回 200。
