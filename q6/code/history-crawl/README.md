# 六渠道歷史爬蟲（2023-01 起）

Q6 需要「2023-01 至今」的歷史貼文做分群。這個資料夾是實際跑過的爬蟲程式，執行紀錄與數字在 [../../evidence/history-crawl/](../../evidence/history-crawl/)。

## 檔案

| 檔案 | 渠道 | 做法 |
|---|---|---|
| [crawl_ptt.py](crawl_ptt.py) | PTT 9 個看板 | 公開頁面。先從最新 index 頁往回翻到 2023-01（列表），再依新到舊輪流抓內文與推文。 |
| [crawl_youtube.py](crawl_youtube.py) | YouTube 7 個新聞／在地頻道 | Data API v3 `playlistItems`（uploads 清單），之後每頻道每月抽 3 部影片抓留言。 |
| [crawl_deep.py](crawl_deep.py) | Facebook／Instagram／Threads | 登入狀態。頁面正常載入一次，等它自己發出時間軸分頁的 GraphQL 請求，再在頁面內只換 cursor 重送。 |
| [telem.py](telem.py) | 共用 | 自適應節流 `Throttle` + 遙測 `Telemetry`（api_trace／metrics／events，送 Loki 與 Pushgateway）。 |
| [watchdog.py](watchdog.py) | 共用 | 看門狗：守著監控堆疊與三支爬蟲，異常記錄加截圖，定期回補。見下方「看門狗與回補」。 |
| [backfill.py](backfill.py) | 共用 | 把本機遙測裡 Loki、Prometheus 漏收的部分，用原始時間戳補回去。可重複執行。 |
| [start_watchdog.cmd](start_watchdog.cmd) | 共用 | 以 pythonw 啟動部署好的看門狗；放進 Windows 啟動資料夾後，開機會自動帶起整套。 |
| [observability/](observability/) | 共用 | Loki、Prometheus、Pushgateway、Grafana 的 docker compose 與 crawler-monitor 看板。 |
| [round1_public/](round1_public/) | PTT、YouTube、Threads、新聞網站 | 第一輪：完全未登入、只走 robots 允許的路徑，用來量出「不登入能拿到多少」。 |

`crawl_deep.py` 會引用 [../meta-login/](../meta-login/) 的 `crawl_loggedin.py`（登入 profile、JSON 解析）與 `redact.py`（截圖遮罩），兩個資料夾要放在同一層。它用 Windows 的 user32 抓含 DevTools 的整個視窗，所以只能在 Windows 上跑。

## 節流與異常處理

`telem.Throttle(base, cap=600, pause=1800)`：

- 每次請求前等 `delay` 秒；PTT base 4 秒（再加 -0.8 ~ +1.0 秒抖動），YouTube 1 秒。
- 任何異常（非 200、回應過短、年齡確認頁、登出／驗證挑戰標記）都讓 delay 加倍，上限 600 秒。
- 延遲：只看 200 回應，連續 2 次大於 2 倍 p95 才算異常；單次尖峰只記錄，不加倍。
- 本機斷線（連線被拒、DNS 失敗、`ERR_INTERNET_DISCONNECTED` 等）由 `telem.NetGuard` 處理：探測對外連線，斷線就暫停到網路恢復再重送同一個請求，不算異常、不加倍、不消耗 YouTube 配額；事件記為 `network_down`／`network_restored`。網路探測正常但連線仍連續失敗 3 次，才回到一般異常處理。
- 連續 3 次異常暫停 30 分鐘。
- 連續 20 次正常才把 delay 減半，慢慢回到 base。
- 登出、驗證挑戰（checkpoint／challenge）、帳號鎖定：該平台直接停止，不嘗試繞過。

## 續跑（checkpoint）

| 渠道 | 狀態檔 | 重跑行為 |
|---|---|---|
| PTT | `ptt/state/<board>.json`（next_page、newest_page、done）、`ptt/progress.json` | 從 `next_page` 繼續往回翻；`lists/<board>.jsonl` 以 url 去重；內文跳過已在 `articles/<board>.jsonl` 的 url。 |
| YouTube | `youtube/state.json`（每頻道 page_token、當日用量） | 從 `page_token` 續抓；配額在太平洋時間午夜重置，每頻道每天平分 8,000 units，留言用到總計 9,000 為止；剩下的 1,000 units 留給共用同一把金鑰的 Q6 即時採集器（每天約 864）。上限可用 `YOUTUBE_UPLOAD_BUDGET`／`YOUTUBE_TOTAL_BUDGET` 調整。 |
| Meta | `deep/state/*.json`（每個目標的 cursor 與已見 post_id） | 從上次的 cursor 繼續；已見過的 post_id 不重寫。 |

中斷後直接重跑同一個指令即可。YouTube 重啟時最後一頁會重抓，輸出另有去重版。

### 頭部增量

每次重跑先補「上次之後的新內容」，再接著往 2023-01 回補：

| 渠道 | 頭部增量 | 停止條件 |
|---|---|---|
| PTT | 從看板目前最新頁往回翻，進度存在 state 的 `head` | 翻到上次的 `newest_page`；新文章排在內文佇列前面 |
| YouTube | 每個頻道（含已回補完成的）從上傳清單第一頁往後翻 | 遇到已抓過的 video_id，或發布時間不晚於上次的 `latest` |
| Meta | 每個已有貼文的目標從時間軸頂端重播分頁，回補 cursor 另存 `head.resume_cursor` | 整頁都不晚於上次的 `latest`、整頁都是已見貼文、時間軸到底或滿 300 頁；結束後回到原 cursor 續補 |

頭部增量中斷也能續跑（state 的 `head` 欄位），結果記在 `head_last` 並發 `head_done` 事件。

## 執行

```bash
pip install requests beautifulsoup4 playwright pillow
```

```bash
bash observability/up.sh
```

```bash
CRAWL_WORKDIR=./work python crawl_ptt.py
```

```bash
CRAWL_WORKDIR=./work YOUTUBE_API_KEY_FILE=./youtube_api_key.txt python crawl_youtube.py
```

```bash
CRAWL_WORKDIR=./work META_WORKDIR=../meta-login python crawl_deep.py facebook,instagram,threads
```

| 環境變數 | 用途 | 預設 |
|---|---|---|
| `CRAWL_WORKDIR` | 輸出、狀態檔、遙測的根目錄 | `./work` |
| `YOUTUBE_API_KEY` 或 `YOUTUBE_API_KEY_FILE` | YouTube API 金鑰（擇一）；金鑰檔不進版控 | 無 |
| `META_WORKDIR` | 登入 profile 所在目錄（見 [../meta-login/](../meta-login/)） | `../meta-login` |
| `Q6HIST_ROOT` | round1_public 的輸出根目錄 | `./q6hist` |

`crawl_deep.py` 第一個參數可以只跑部分平台（逗號分隔）。

## 輸出與遙測

- 輸出：`ptt/lists`、`ptt/articles`、`youtube/out/videos.jsonl`、`deep/clean/<platform>.jsonl`，欄位見 [fields-and-painpoints.md](../../evidence/history-crawl/fields-and-painpoints.md)。
- 遙測：每個請求一列 `api_trace.jsonl`（網址只留路徑與參數名稱，不留參數值；標頭值只留 10 碼雜湊），每分鐘一列 `metrics.jsonl`，事件一列 `events.jsonl`。
- Grafana `crawler-monitor` 看板：<http://127.0.0.1:3000/d/crawler-monitor>（用 127.0.0.1，localhost 在 Windows 會先試 IPv6 而卡約 5 秒）；`observability/shot.py` 以 Playwright 截看板。
- `observability/up.sh` 啟動 Loki、Prometheus、Pushgateway、Grafana 四個服務，逐一等到 ready 才結束。連接埠只綁 127.0.0.1（Grafana 開匿名 Admin，不對區網開放）。
- Pushgateway 以 `--persistence.file` 寫入 volume，容器重建後指標仍在。
- 推送 Loki／Pushgateway 失敗時，每個目的地每分鐘最多記一筆 `push_failed` 事件（只寫本機 events.jsonl）並印出警告，不影響爬取。
- 原始遙測不進版控，只放統計摘要 [telemetry_summary.json](../../evidence/history-crawl/telemetry_summary.json)。

## 看門狗與回補

`watchdog.py` 每 120 秒一輪：

1. 堆疊任一服務沒回應就在 WSL 執行 `docker compose -p crawlobs up -d`，恢復後回補 7 天內的缺口，5 分鐘後再補一輪 3 小時（接住斷線期間晚寫入的 push_failed 行）。
2. 爬蟲行程不見就重開：當掉依 5、10、20 到 60 分鐘退避；印出完成行（`PTT_DONE`、`YT_DONE`、`ALL_DONE`）才算跑完，跑完後冷卻（PTT、Meta 30 分鐘，YouTube 等配額重置）；沒印完成行就消失的記成 crawler_vanished，5 分鐘後重開。遙測安靜超過 1 小時且網路正常，結束行程樹後重開。
3. 爬蟲 events.jsonl 裡的異常逐筆寫進 `watchdog/pitfalls.jsonl` 與 Loki，同種異常每 30 分鐘截一張看板。看門狗不改節流，也不碰登入牆。
4. 每 30 分鐘回補最近 3 小時。

`backfill.py` 比對本機 `*/telemetry/*.jsonl` 與 Loki、Prometheus 的內容，只補缺的部分：Loki 寫進 `backfill=<YYYYMMDDHH>` 標籤的 stream（避開一小時亂序限制），寫完呼叫 `/flush`；Prometheus 走 remote-write，對齊整分鐘。相同時間戳加內容重送不會多一筆，所以斷線後重補同一段就是還原。

```bash
python backfill.py --hours 3 --dry-run
```

部署與開機啟動（Windows）：

```bash
CRAWL_ROOT=C:/q6dl/crawl pythonw watchdog.py
```

- 程式部署在 `q6dl/runtime/history-crawl`，工作目錄 `q6dl/crawl`；看門狗只接受 `--once`，工作目錄不存在就拒絕執行，避免從 repo 誤啟第二個實例。
- 開機自動啟動：把 `start_watchdog.cmd` 複製進 `shell:startup`（`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`），刪掉那份複本就關閉。
- 踩過的坑與截圖：[../../evidence/history-crawl/pitfalls/](../../evidence/history-crawl/pitfalls/)。

## 不進版控

`work/`、`q6hist/`、`*.log`（見 [.gitignore](.gitignore)），以及 YouTube 金鑰檔、登入 profile。
