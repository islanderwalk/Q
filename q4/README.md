# QSearch 筆試第 4 題：Threads 公開貼文爬蟲｜圖片素材

依類型分資料夾。`q4/architecture/` 是用 HTML+SVG 畫、再以 Playwright 渲染的架構圖；`q4/evidence/` 是用 Playwright 打開 Threads 公開頁的真實截圖（未登入）。修正後的答案與逐條驗證見 `q4/answer/` 的 [`threads_q4_result.txt`](answer/threads_q4_result.txt) 與 [`threads_q4_verification.md`](answer/threads_q4_verification.md)。

> **題目（摘自本題設定）：** 要從 40 萬個公開 Threads 帳號收集貼文，每個帳號每輪至少擷取 10 篇貼文，每天執行 4 輪；每篇需要貼文 URL、全文、發佈時間三個欄位；以不登入的方式存取。
>
> **(a)** 估算每日 HTTP 請求數，並說明你對「實際打到 Threads 的請求」的理解。
> **(b)** 設計防封策略，並做代理選型。
> **(c)** 估算每月代理成本（要有計算過程與列出的牌價）。
>
> *上限 600 字。*

資料來源：
- 架構圖：本資料夾 `q4/architecture/src/` 的自包含 HTML（任何瀏覽器可直接開），以 `render.py` 渲染成 2x PNG
- Threads 截圖：2026/10/30 在雲端容器用 Playwright 無頭 Chromium、手機視窗拍攝；全程未登入、不帶 cookie、單頁載入，三帳號皆回 HTTP 200、貼文正常渲染
- 數字與請求結構的實測依據見 `q4/answer/threads_q4_verification.md`
- 量測程式 `q4/code/threads_q4_measure.py` 在 2026/10/30（UTC 07:50–12:37）於 Linux 重測 nasa、zuck、natgeo，HTTP 與瀏覽器兩種模式都跑了，結果見 [`q4/code/RESULTS.md`](code/RESULTS.md)，摘要 JSON（12:29–12:36 那一輪）在 `q4/data/threads_q4_out/rerun_2026-10-30_linux/`
- 測試在 [`q4/code/tests/`](code/tests)：`test_threads_smoke.py` 16 項不連外網（瀏覽器流程用本機假站跑 Chromium，找不到就略過），`test_q4_answer_length.py` 1 項檢查答案連空白與換行不超過 600 字，共 17 passed（見 [`q4/code/RESULTS.md`](code/RESULTS.md)）

---

## 架構圖 — [`q4/architecture/`](architecture)

| 檔案 | 內容 | 對應小題 |
|---|---|---|
| [`diagram-system.png`](architecture/diagram-system.png) | 整個爬蟲系統架構：目標來源 → 排程器 → Worker 池（每帳號每輪 1 次 `POST /graphql/query`）→ 住宅輪替代理 → Threads GraphQL 端點 → 解析器 → 儲存；含 Token 供應器、監控面板、控制迴路（401→換 IP 退避）與流量估算 | (a)(b)(c) 全覽 |
| [`diagram-request-layers.png`](architecture/diagram-request-layers.png) | 單一 GraphQL 請求的分層解剖：OSI 七層 × Chromium/Linux 底層。標出 GraphQL／token／401 都在 L7、封鎖的判定在 L3 的來源 IP，以及壓縮計費（L6）、Sec-Fetch 門檻（L7） | (a) 理解實際請求、(b) 為何封鎖壓在 IP |

重繪（需要 Playwright：`pip install playwright && playwright install chromium`）：

```
python3 q4/architecture/src/render.py
```

## Threads 真實畫面佐證 — [`q4/evidence/`](evidence)

未登入手機視窗；每張都可見頂端「Log in / Get app」，印證是匿名存取公開頁。

| 檔案 | 佐證什麼 |
|---|---|
| [`threads-profile-zuck.png`](evidence/threads-profile-zuck.png) | @zuck（5.7M followers）未登入即見完整貼文（Muse Wonder 模型表），含發佈日期 10/12/26 與互動數 → 公開頁匿名可取得 URL／全文／時間 |
| [`threads-profile-nasa.png`](evidence/threads-profile-nasa.png) | @nasa（12.7M followers）Spacewalk 貼文，含圖片、youtube 連結卡與互動數（235 讚），時間「10h」 |
| [`threads-profile-natgeo.png`](evidence/threads-profile-natgeo.png) | @natgeo（18.4M followers）第二篇是 natgeo 自己的貼文，裡面引用了 @natgeotv 的貼文（不是轉發）；時間「12h／1d」，被引用的那篇是 4d |

> 這三張是手機視窗的乾淨截圖（載入後以前端隱藏登入軟牆，等同 devtools 操作自己的瀏覽器）。使用者本機另抓的 Q4 桌面版截圖（含 `threads_mobile_wall`、`threads_graphql_desktop`、`threads_post_url`）在下方「Playwright 實抓證據」一節，兩者互補。

重跑（遵守 `shared/capture` 的規則：只抓未登入公開頁、不存 cookie/token、不用代理、不偽裝指紋）：

```
python3 q4/evidence/src/capture.py
```

## Playwright 實抓證據 — [`q4/evidence/playwright/`](evidence/playwright)

用 Playwright 打開 Threads 公開頁截的圖（未登入）。由 `shared/capture/` 的腳本產生，每張的主張與結果見 `shared/capture/capture_manifest.json`。

| 檔案 | 佐證什麼 |
|---|---|
| [`threads_profile_nasa.png`](evidence/playwright/threads_profile_nasa.png) | @nasa 個人頁 HTML 只內嵌 4 串；畫面只看得到 1 篇（置頂那篇，一部分被「Say more with Threads」對話框蓋住），5 篇是 HTML 內嵌 JSON 的計數（2026-10-30 稽核更正）。要湊滿 10 篇得靠分頁 query。這是瀏覽器拿到的 HTML；10/30 重測時，帶 `Sec-Fetch-Mode`、`Sec-Fetch-Dest` 的 HTTP 請求也一樣是 4 串 5 篇，不帶這兩個標頭的純 HTTP 只拿到 258,277 B 的空殼（0 串），第一輪純 HTTP 判定的 SHELL 就是這種情況（見 `q4/answer/threads_q4_verification.md` 第 1 節與 `shared/docs/python.md` 第二節的 Q4 列）。4 串是因為 HTML 預排的 `BarcelonaProfileThreadsTabDirectQuery` 本身帶 `first: 4` |
| [`threads_profile_zuck.png`](evidence/playwright/threads_profile_zuck.png) | @zuck 個人頁 HTML 只內嵌 4 串；畫面只看得到 1 篇，10 篇是 HTML 內嵌 JSON 的計數（2026-10-30 稽核更正；原寫「DOM 計數」，但截圖腳本數的是內嵌 JSON）。這 10 篇是 4 串加上串內的自我回覆，不是 10 串；10/30 重測結果相同 |
| [`threads_graphql_desktop.png`](evidence/playwright/threads_graphql_desktop.png) | 10/30 凌晨（+08）拍這張時，網頁自己送了 `BarcelonaProfileThreadsTabRefetchableDirectQuery`（doc_id 28880431481598345，與 10/29 不同）：HTTP 200、11 串、401 KB。這次觀察的時間包含載入後 4.5 秒與 1 次 1,600 px 的滾輪捲動，不是單純「載入時」；幾分鐘前（01:51–01:52 +08）的第一版截圖腳本在手機視窗捲動 5 次，沒有收到帶貼文的 GraphQL 回應（`shared/capture/capture_log.txt` 第 11 行；第一版只記有貼文的回應，有沒有送出這個 query 分不出來）。10/30（UTC 07:50–12:37）量測程式重測：桌面 15 次載入都只送 `fetchBarcelonaExperimentQuery`（0 串），其中只有最後 3 次確定把主欄捲到底；手機 3 個帳號只有 natgeo 在捲動時收到一次，doc_id 又換成 39825889313693314、`first: 11`、11 串。合計桌面是截圖腳本 1/1、量測程式 0/15，手機是截圖腳本 1/2、量測程式 1/3。所以這張證明 doc_id 會換，不代表每次開頁網頁都會送分頁 query |
| [`threads_mobile_wall.png`](evidence/playwright/threads_mobile_wall.png) | 手機版未登入：10/28 捲動還會分頁，10/30 出現「Log in to see more from nasa.」登入提示，所以要監控每次回傳的串數。不過這張截圖自己的標註就寫著捲動期間收到 `BarcelonaProfileThreadsTabRefetchableDirectQuery`、帶貼文的回應 2 個，所以拍這張時手機捲動其實還有分頁。10/30（UTC 07:50–12:37）重測：這行提示已經在伺服器回的 HTML 裡，桌面、手機都有；手機 3 個帳號有 1 個在捲動時載入下一頁（natgeo 頁面 DOM 裡的貼文連結 5 個變 22 個）。出現登入提示不代表停止分頁，分頁是時有時無 |
| [`threads_post_url.png`](evidence/playwright/threads_post_url.png) | 貼文網址是 `/@帳號/post/{code}`，可直接用回應裡的 code 組出，不必再抓貼文頁 |
