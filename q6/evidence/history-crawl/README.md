# 六渠道歷史爬取：執行證據

程式在 [../../code/history-crawl/](../../code/history-crawl/)。每個渠道拿到哪些欄位、卡在哪裡、對 Q6 的影響，整理在 [fields-and-painpoints.md](fields-and-painpoints.md)。

## 執行中截圖（含 DevTools）

截圖都已模糊帳號頭像、通知數、帳號名稱與分頁標題。

| 檔案 | 看到什麼 |
|---|---|
| [01_fb_cnanewstaiwan_open.jpg](01_fb_cnanewstaiwan_open.jpg) | FB 中央社粉專以登入狀態正常開啟，時間軸開始載入。 |
| [02_fb_cnanewstaiwan_open_f12.jpg](02_fb_cnanewstaiwan_open_f12.jpg) | 同一頁開著 DevTools Network，可看到時間軸分頁的 GraphQL 請求。 |
| [03_ig_ftvnews_milestone100.jpg](03_ig_ftvnews_milestone100.jpg) | IG 民視新聞累積到第 100 篇時的里程碑截圖。 |
| [04_ig_cnanews_graphql_errors.jpg](04_ig_cnanews_graphql_errors.jpg) | IG 中央社的 GraphQL 回應帶 errors，被節流器記為異常。 |
| [05_threads_pts_latency_gt_2x_p95_f12.jpg](05_threads_pts_latency_gt_2x_p95_f12.jpg) | Threads 公視：回應 200 但延遲超過 2 倍 p95，觸發退避（DevTools 可看到慢請求）。 |
| [06_threads_pts_http502.jpg](06_threads_pts_http502.jpg) | Threads 公視：分頁請求回 HTTP 502。 |
| [07_threads_cnanews_cookie_rotated.jpg](07_threads_cnanews_cookie_rotated.jpg) | Threads 中央社：偵測到 session cookie 被平台輪替時自動拍下的頁面（遙測只記雜湊變化，不記值）；左側帳號區已模糊。 |
| [08_fb_cnanewstaiwan_offline.jpg](08_fb_cnanewstaiwan_offline.jpg) | FB：網路中斷時的離線畫面。 |
| [09_ig_nasa_disconnected.jpg](09_ig_nasa_disconnected.jpg) | IG：`ERR_INTERNET_DISCONNECTED`，計為異常並退避。 |
| [10_threads_ftvnews_goto_error.jpg](10_threads_ftvnews_goto_error.jpg) | Threads 民視：頁面導覽失敗（goto error）。 |

## Grafana crawler-monitor 看板

日誌面板已模糊（含目標網址與事件細節）。

| 檔案 | 範圍 |
|---|---|
| [grafana/crawler_monitor_30m.png](grafana/crawler_monitor_30m.png) | 近 30 分鐘：各渠道請求數、延遲、delay、退避等級與事件日誌 |
| [grafana/crawler_monitor_30m_metrics.png](grafana/crawler_monitor_30m_metrics.png) | 近 30 分鐘：只有指標面板 |
| [grafana/crawler_monitor_6h.png](grafana/crawler_monitor_6h.png) | 近 6 小時：同上 |
| [grafana/crawler_monitor_6h_metrics.png](grafana/crawler_monitor_6h_metrics.png) | 近 6 小時：只有指標面板 |

## 數字

- [telemetry_summary.json](telemetry_summary.json)：從 api_trace／metrics／events 彙總的統計（每平台請求數、狀態碼分布、失敗原因、延遲 p50／p95／p99、退避等級分布、事件種類與次數，以及各渠道進度）。逐筆原始紀錄不進版控。
- [fields-and-painpoints.md](fields-and-painpoints.md)：進度總覽、各渠道欄位、痛點 P-1 ~ P-9 與對 Q6 的影響 C-1 ~ C-9。
