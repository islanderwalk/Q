# ARGUS｜Threads 爬蟲：40 萬帳號、每天 4 輪

> 一句話：每天從 40 萬個公開 Threads 帳號各抓最新 10 篇貼文（URL、全文、發佈時間），不登入，用 GraphQL 分頁查詢加住宅輪替代理；這個模組負責把請求量、防封與代理成本估出來，並用小規模實測驗證。

依類型分資料夾。`q4/architecture/` 是用 HTML+SVG 畫、再以 Playwright 渲染的架構圖；`q4/evidence/` 是用 Playwright 打開 Threads 公開頁的截圖（未登入）。設計摘要與逐條驗證見 `q4/design/` 的 [`threads_q4_result.txt`](design/threads_q4_result.txt) 與 [`threads_q4_verification.md`](design/threads_q4_verification.md)。

## 情境與限制

- 40 萬個公開 Threads 帳號，每個帳號每輪至少擷取 10 篇貼文，每天執行 4 輪。
- 每篇要有貼文 URL、全文、發佈時間三個欄位；以不登入的方式存取。
- robots.txt 禁止未經書面許可自動蒐集，這個限制我照實寫進設計裡。
- 數字來自 12 個帳號的小規模實測加估算，沒有實際跑過 40 萬帳號。

## 遇到的瓶頸

1. **請求量與實際請求長什麼樣**：未登入的個人頁只內嵌 4 串，湊滿 10 串要靠分頁 query，每日請求數與每次大小都得自己量。
2. **防封與代理選擇**：單一 IP 打一陣子後 GraphQL 會全部回 401，得決定怎麼換 IP、用哪種代理。
3. **每月代理成本**：流量要換算到 TB，代理牌價是按 GB 計、還有起購條件，得算出一個能比的數字。

## 我怎麼解

瓶頸一（請求量）：未登入的個人頁 HTML 只內嵌 4 串，不夠 10 串。改打分頁 GraphQL query，帶 userID、after=null、first=10，就能拿到最新 10 串，含 code（組 URL）、全文、taken_at，不必抓 HTML。每帳號每輪 1 次：40 萬×4＝每日 160 萬次（≈18.5 req/s）。userID 我快取一次；csrftoken／lsd 由無頭瀏覽器定期取得、跨帳號共用，免登入、不帶 cookie。

瓶頸二（防封與代理）：缺瀏覽器 Sec-Fetch 標頭或 4 個 x-header 任一，GraphQL 就沒有貼文。實測單一 IP 以 5 req/s 打約 80–100 次後，GraphQL 即按 IP 封鎖（401，換 token 無效，5 分鐘未解；HTML 照常）。所以我選住宅輪替代理、每請求換 IP；遇 401 就剔除該 IP 並退避，監控成功率與每次串數，異常即降速。robots.txt 禁止未經書面許可自動蒐集，這一點我保留在範圍裡。

瓶頸三（月費）：實測每次回應 27.8 KB、上傳 4.1 KB，每月 4,800 萬次約 1.5 TB，含 TLS 1.8 TB。IPRoyal $1.84/GB（10 TB 起購、不過期）約 US$2.8–3.3k/月；Oxylabs $2.5/GB 約 US$3.8–4.5k/月。ISP 固定 IP 因單 IP 可持續速率未測得、封鎖又久，我不作主力。

資料來源：
- 架構圖：本資料夾 `q4/architecture/src/` 的自包含 HTML（任何瀏覽器可直接開），以 `render.py` 渲染成 2x PNG
- Threads 截圖：2026/10/30 在 Linux 容器用 Playwright 無頭 Chromium、手機視窗拍攝；全程未登入、不帶 cookie、單頁載入，三帳號皆回 HTTP 200、貼文正常渲染
- 數字與請求結構的實測依據見 `q4/design/threads_q4_verification.md`
- 量測程式 `q4/code/threads_q4_measure.py` 在 2026/10/30（UTC 07:50–12:37）於 Linux 重測 nasa、zuck、natgeo，HTTP 與瀏覽器兩種模式都跑了，結果見 [`q4/code/RESULTS.md`](code/RESULTS.md)，摘要 JSON（12:29–12:36 那一輪）在 `q4/data/threads_q4_out/rerun_2026-10-30_linux/`
- 測試在 [`q4/code/tests/`](code/tests)：`test_threads_smoke.py` 16 項不連外網（瀏覽器流程用本機假站跑 Chromium，找不到就略過），`test_q4_summary_length.py` 1 項檢查設計摘要連空白與換行不超過 650 字，共 17 passed（見 [`q4/code/RESULTS.md`](code/RESULTS.md)）

---

## 架構圖 — [`q4/architecture/`](architecture)

| 檔案 | 內容 | 對應瓶頸 |
|---|---|---|
| [`diagram-system.png`](architecture/diagram-system.png) | 整個爬蟲系統架構：目標來源 → 排程器 → Worker 池（每帳號每輪 1 次 `POST /graphql/query`）→ 住宅輪替代理 → Threads GraphQL 端點 → 解析器 → 儲存；含 Token 供應器、監控面板、控制迴路（401→換 IP 退避）與流量估算 | 瓶頸一～三全覽 |
| [`diagram-request-layers.png`](architecture/diagram-request-layers.png) | 單一 GraphQL 請求的分層解剖：OSI 七層 × Chromium/Linux 底層。標出 GraphQL／token／401 都在 L7、封鎖的判定在 L3 的來源 IP，以及壓縮計費（L6）、Sec-Fetch 門檻（L7） | 瓶頸一 理解實際請求、瓶頸二 為何封鎖壓在 IP |

重繪（需要 Playwright：`pip install playwright && playwright install chromium`）：

```
python3 q4/architecture/src/render.py
```

## Threads 畫面佐證 — [`q4/evidence/`](evidence)

未登入手機視窗；每張都可見頂端「Log in / Get app」，印證是匿名存取公開頁。

| 檔案 | 佐證什麼 |
|---|---|
| [`threads-profile-zuck.png`](evidence/threads-profile-zuck.png) | @zuck（5.7M followers）未登入即見完整貼文（Muse Wonder 模型表），含發佈日期 10/12/26 與互動數 → 公開頁匿名可取得 URL／全文／時間 |
| [`threads-profile-nasa.png`](evidence/threads-profile-nasa.png) | @nasa（12.7M followers）Spacewalk 貼文，含圖片、youtube 連結卡與互動數（235 讚），時間「10h」 |
| [`threads-profile-natgeo.png`](evidence/threads-profile-natgeo.png) | @natgeo（18.4M followers）第二篇是 natgeo 自己的貼文，裡面引用了 @natgeotv 的貼文（不是轉發）；時間「12h／1d」，被引用的那篇是 4d |

> 這三張是手機視窗的乾淨截圖（載入後以前端隱藏登入軟牆，等同 devtools 操作自己的瀏覽器）。本機另抓的桌面版截圖（含 `threads_mobile_wall`、`threads_graphql_desktop`、`threads_post_url`）在下方「Playwright 實抓證據」一節，兩者互補。

重跑（遵守 `shared/capture` 的規則：只抓未登入公開頁、不存 cookie/token、不用代理、不偽裝指紋）：

```
python3 q4/evidence/src/capture.py
```

## Playwright 實抓證據 — [`q4/evidence/playwright/`](evidence/playwright)

用 Playwright 打開 Threads 公開頁截的圖（未登入）。由 `shared/capture/` 的腳本產生，每張的主張與結果見 `shared/capture/capture_manifest.json`。

| 檔案 | 佐證什麼 |
|---|---|
| [`threads_profile_nasa.png`](evidence/playwright/threads_profile_nasa.png) | @nasa 個人頁 HTML 只內嵌 4 串；畫面只看得到 1 篇（置頂那篇，一部分被「Say more with Threads」對話框蓋住），5 篇是 HTML 內嵌 JSON 的計數（2026-10-30 稽核更正）。要湊滿 10 篇得靠分頁 query。這是瀏覽器拿到的 HTML；10/30 重測時，帶 `Sec-Fetch-Mode`、`Sec-Fetch-Dest` 的 HTTP 請求也一樣是 4 串 5 篇，不帶這兩個標頭的純 HTTP 只拿到 258,277 B 的空殼（0 串），第一輪純 HTTP 判定的 SHELL 就是這種情況（見 `q4/design/threads_q4_verification.md` 第 1 節與 `shared/docs/python.md` 第二節的 Threads 列）。4 串是因為 HTML 預排的 `BarcelonaProfileThreadsTabDirectQuery` 本身帶 `first: 4` |
| [`threads_profile_zuck.png`](evidence/playwright/threads_profile_zuck.png) | @zuck 個人頁 HTML 只內嵌 4 串；畫面只看得到 1 篇，10 篇是 HTML 內嵌 JSON 的計數（2026-10-30 稽核更正；原寫「DOM 計數」，但截圖腳本數的是內嵌 JSON）。這 10 篇是 4 串加上串內的自我回覆，不是 10 串；10/30 重測結果相同 |
| [`threads_graphql_desktop.png`](evidence/playwright/threads_graphql_desktop.png) | 10/30 凌晨（+08）拍這張時，網頁自己送了 `BarcelonaProfileThreadsTabRefetchableDirectQuery`（doc_id 28880431481598345，與 10/29 不同）：HTTP 200、11 串、401 KB。這次觀察的時間包含載入後 4.5 秒與 1 次 1,600 px 的滾輪捲動，不是單純「載入時」；幾分鐘前（01:51–01:52 +08）的第一版截圖腳本在手機視窗捲動 5 次，沒有收到帶貼文的 GraphQL 回應（`shared/capture/capture_log.txt` 第 11 行；第一版只記有貼文的回應，有沒有送出這個 query 分不出來）。10/30（UTC 07:50–12:37）量測程式重測：桌面 15 次載入都只送 `fetchBarcelonaExperimentQuery`（0 串），其中只有最後 3 次確定把主欄捲到底；手機 3 個帳號只有 natgeo 在捲動時收到一次，doc_id 又換成 39825889313693314、`first: 11`、11 串。合計桌面是截圖腳本 1/1、量測程式 0/15，手機是截圖腳本 1/2、量測程式 1/3。所以這張證明 doc_id 會換，不代表每次開頁網頁都會送分頁 query |
| [`threads_mobile_wall.png`](evidence/playwright/threads_mobile_wall.png) | 手機版未登入：10/28 捲動還會分頁，10/30 出現「Log in to see more from nasa.」登入提示，所以要監控每次回傳的串數。不過這張截圖自己的標註就寫著捲動期間收到 `BarcelonaProfileThreadsTabRefetchableDirectQuery`、帶貼文的回應 2 個，所以拍這張時手機捲動其實還有分頁。10/30（UTC 07:50–12:37）重測：這行提示已經在伺服器回的 HTML 裡，桌面、手機都有；手機 3 個帳號有 1 個在捲動時載入下一頁（natgeo 頁面 DOM 裡的貼文連結 5 個變 22 個）。出現登入提示不代表停止分頁，分頁是時有時無 |
| [`threads_post_url.png`](evidence/playwright/threads_post_url.png) | 貼文網址是 `/@帳號/post/{code}`，可直接用回應裡的 code 組出，不必再抓貼文頁 |
