# 監控看門狗：自動回補與踩坑紀錄（2026-11-03）

程式在 [../../../code/history-crawl/](../../../code/history-crawl/)：`watchdog.py` 守著 crawlobs 監控堆疊與三支歷史爬蟲，`backfill.py` 把堆疊漏收的資料用原始時間戳補回 Loki 與 Prometheus。爬蟲本身的欄位與痛點（PTT 525、IG GraphQL errors、民視 FB 找不到粉專等）在 [../fields-and-painpoints.md](../fields-and-painpoints.md)，這裡只記監控、回補與看門狗這一層踩過的坑。

## 看門狗做什麼

每 120 秒一輪：

1. **堆疊**：Prometheus、Loki、Pushgateway、Grafana 任一個沒回應，就在 WSL 裡 `docker compose -p crawlobs up -d`，等它們好，再把 Loki 能收的 7 天內缺的資料全部回補。5 分鐘後再補第二輪 3 小時（理由見坑 10）。
2. **爬蟲**：活著的行程直接接管；不見了就重開。當掉依 5、10、20 到 60 分鐘退避；正常跑完則冷卻（PTT 與 Meta 30 分鐘，YouTube 等配額重置）。遙測安靜超過 1 小時而網路正常，就判定卡住，結束整個行程樹後重開。Meta 某平台若因登出或驗證停下，就先排除，不嘗試繞過登入牆。
3. **異常**：爬蟲寫進 events.jsonl 的異常事件（anomaly、push_failed、crash 等）逐筆寫進 `pitfalls.jsonl` 與 Loki，同一種異常每 30 分鐘拍一張 crawler-monitor 看板截圖。爬蟲自己已經放慢，看門狗負責留下紀錄。
4. **定期回補**：每 30 分鐘回補最近 3 小時，短暫的推送失敗不用等堆疊重啟就能補齊。

回補可以重複跑：Loki 對相同的時間戳加內容會去重，Prometheus 寫入同一分鐘同一值也不會多一筆。所以斷線後的「還原」就是把同一段再補一次，不需要另外的 rollback 步驟。

本資料夾的 [pitfalls.jsonl](pitfalls.jsonl) 與 [backfill.jsonl](backfill.jsonl) 是當天看門狗的原始紀錄。

## 斷線演練（21:05 到 21:07）

刻意停掉 Loki 與 Pushgateway，看看門狗能不能自己救回來。

| 時間 | 發生什麼 | 截圖 |
|---|---|---|
| 21:05 前 | 看板正常，各面板都有資料。 | [demo1_before_outage_1h.png](demo1_before_outage_1h.png) |
| 21:05:20 | 停掉 Loki 與 Pushgateway。請求數、狀態分布、事件日誌等 Loki 面板變成 No data；Prometheus 面板還在，但最後一段是 Pushgateway 留下的舊值。 | [demo2_during_outage_1h.png](demo2_during_outage_1h.png) |
| 21:06:21 | 看門狗發現 loki、pushgateway 沒回應，記一筆 stack_down，執行 compose up。 | |
| 21:06:47 | 堆疊回來，回補 7 天：Loki 補回 7 行（ptt 1、watchdog 1、youtube 1、meta 4）。記一筆 stack_recovered 並截圖。 | [wd_stack_recovered_6h.png](wd_stack_recovered_6h.png) |
| 21:07 後 | 面板恢復，事件日誌可看到 stack_down、stack_recovered 兩筆。 | [demo3_after_recovery_1h.png](demo3_after_recovery_1h.png) |

實際中斷約 86 秒；看門狗記的 down_s 是 23 秒，因為它從偵測到的那一輪開始算。演練結束後再跑一次 167 小時的檢查（只比對不寫入），Loki 與 Prometheus 缺漏都是 0。

## 回補前後

| 檔案 | 看到什麼 |
|---|---|
| [before_backfill_7d.png](before_backfill_7d.png) | 回補前 7 天視窗。電腦約 19:48 重開機後爬蟲沒有自動起來，加上 Pushgateway 原本不存檔，容器重建後舊指標消失。 |
| [before_backfill_6h.png](before_backfill_6h.png) | 回補前 6 小時視窗，同一段空白。 |

本機有資料的時段都已補回。爬蟲沒在跑的那段（約 05 時到 20 時）本機也沒有資料，只能如實留白。

## 踩過的坑

### 監控堆疊

1. **localhost 卡 5 秒**：Windows 先試 IPv6 `::1`，WSL 只轉發 IPv4。實測 `localhost` 5,204 ms，`127.0.0.1` 46 ms。爬蟲、回補、看門狗全部改用 127.0.0.1。
2. **重開機後全部沒起來**：電腦約 19:48 重開機，爬蟲沒有自動啟動，Loki 與 Pushgateway 收不到東西，看板變 No data。修法是 `start_watchdog.cmd` 放進 Windows 啟動資料夾，開機後由看門狗把堆疊與爬蟲帶起來。這一步要在電腦上手動放（見程式 README）。
3. **Pushgateway 沒存檔**：容器一重建舊指標就清掉。改加 `--persistence.file`，資料放在 pgwdata volume。
4. **爬蟲停了，看板卻是一條平線**：Pushgateway 會一直留著最後的值，看起來像還在跑。看門狗在爬蟲停下時把它的 gauge 推成 0。
5. **WSL 裡的 docker 要 root**：`wsl -d Ubuntu -u root -- docker ...`，不然沒有權限開 socket。

### 回補

6. **Prometheus 只收對齊整分鐘的樣本**：回補用 remote-write（自己組 protobuf 再用 snappy 壓縮），Prometheus 要開 `--web.enable-remote-write-receiver`，並把 `out_of_order_time_window` 設成 7 天。回補的時間窗也對齊整分鐘。
7. **Loki 只收一小時內的亂序資料**：同一個 stream 裡，比最新一筆早超過約 1 小時的資料會被拒收。回補改寫進標籤 `backfill=<YYYYMMDDHH>` 的獨立 stream。超過 7 天的資料 Loki 一律不收，所以看門狗回補上限是 167 小時。
8. **補進去了卻查不到**：Loki 的 `query_ingesters_within` 是 3 小時，剛寫進 ingester 的舊時間資料查詢時看不到。每次推送完都呼叫 `POST /flush`。
9. **本機重複行與結束前沒送出的行**：爬蟲被結束時，最後一批還沒推送的行只留在本機檔案；本機檔案也偶有重複行。回補以（時間戳、內容）比對，重複的只算一次，沒送出的會被補上。
10. **回補跑完後又冒出缺口**：堆疊掛掉期間，爬蟲把 push_failed 寫在本機；它下一次推送成功之前還會再寫幾行，這些行晚於第一次回補。演練後檢查還差 ptt 1 行、meta 3 行。修法是堆疊恢復 5 分鐘後再自動補一輪 3 小時；當時手動補回 ptt 16 行、meta 34 行。
11. **測試留下的一行**：手動測推送時，在 job=meta_crawl、platform=threads、backfill=testprobe 留下一行 `{"probe":1}`（約 20:52:08）。Loki 不方便刪單行，留著並在此註明。

### 看門狗與爬蟲

12. **記異常時自己當掉**：21:02 看門狗把異常事件寫進遙測時，參數名 `kind` 與 telem 的參數衝突，丟出 TypeError。改名 `trouble`，並讓單筆記錄失敗不會中斷整輪掃描。因為當掉時 state.json 還沒存，重啟後有幾筆異常記了兩次。
13. **YouTube 關留言被當成伺服器異常**：高雄市政府頻道有三部影片關閉留言，API 回 403 commentsDisabled，被計為異常一路退避到暫停 30 分鐘。改成 commentsDisabled、videoNotFound 等「這部影片沒資料」的狀態不退避，只記 content_unavailable。截圖：[wd_youtube_comments_disabled_6h.png](wd_youtube_comments_disabled_6h.png)。
14. **爬蟲被外部結束，卻被當成跑完**：演練時 YouTube 被結束，看門狗接管的行程拿不到結束碼，又沒看到 crash 事件，就當成正常跑完，排到隔天 16:05 配額重置才重開（demo3 事件日誌裡的 crawler_done、restart_at 2026-11-04T16:05）。當時配額只用了 1,195。改成：沒有在 log 印出 `YT_DONE`、`PTT_DONE`、`ALL_DONE` 就消失的行程記為 crawler_vanished，5 分鐘後重開。部署後 21:13 YouTube 已重新開跑。
15. **從 repo 直接執行看門狗**：21:12 在 repo 裡跑 `python watchdog.py --help` 想看用法，結果看門狗把 repo 下的 q6/crawl 當工作目錄跑起來，pid 檔在另一個資料夾，擋不住第二個實例，還起了一支 PTT 爬蟲。約 1 秒內就結束，沒有送出任何 PTT 請求（api_trace 0 行），YouTube 因找不到 API key 當場失敗；產生的資料夾已刪除。修法是只接受 `--once`，而且工作目錄不存在就拒絕執行。
16. **IG nasa GraphQL field_exception**：回應 200 但帶 errors，爬蟲自己退避到等級 3。看門狗只記錄與截圖，不改節流。截圖：[wd_ig_nasa_graphql_6h.png](wd_ig_nasa_graphql_6h.png)。
17. **推送失敗的紀錄**：堆疊掛掉期間 PTT 與 Meta 各寫了幾筆 push_failed，看門狗都記成異常並截圖：[wd_ptt_push_failed_6h.png](wd_ptt_push_failed_6h.png)。這些行之後都由回補送進 Loki。

20. **看板「No data」分不出爬蟲停了還是資料掉了**：看門狗每輪推 `crawler_up{crawler}`（1 執行中、2 跑完等下一輪、0 停止）和 `watchdog_heartbeat_seconds` 到 Pushgateway，看板最上方加「爬蟲狀態」與「看門狗上次回報（秒前）」兩個面板，超過 5 分鐘轉橘、10 分鐘轉紅；看門狗本身沒在跑時顯示「看門狗沒回報」。截圖：[wd_status_panel_1h.png](wd_status_panel_1h.png)（YouTube 已印 `YT_DONE`，顯示「跑完，等下一輪」）。

### 環境

18. **工作目錄放在暫存資料夾**：早期爬蟲的工作目錄在暫存資料夾，工作階段結束就可能被清掉。改放固定目錄 `q6dl\crawl`，程式部署在 `q6dl\runtime\history-crawl`。
19. **開機自動啟動要手動放**：把 `start_watchdog.cmd` 複製進啟動資料夾屬於在電腦上加常駐設定，由使用者手動放，看門狗不會自己加。
21. **兩份 compose 同一個專案名**：repo 裡的 `observability/` 與部署的 `q6dl/runtime/history-crawl/observability/` 都用 `-p crawlobs`，從任一邊執行 `up.sh` 都會接管同一組容器與 volume，設定改用那一邊的檔案。目前兩邊內容相同；要改設定時先改 repo 再複製到部署目錄，只從部署目錄啟動。

21:13 與 21:18 看門狗兩度換 pid（36216→2628→11764→18964）都是部署新版時手動重啟，不是當掉；它接管既有的爬蟲行程，爬蟲沒有中斷。
