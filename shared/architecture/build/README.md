# 總架構圖 v2 建置

`qsearch-architecture.html` 是在原本的總架構頁上「只加不改」：顏色、版面、節點樣式都沿用原頁，另外加四個分頁與節點細節。

| 加了什麼 | 內容來源 |
|---|---|
| Q2 估算 分頁 | `q2/answer/fb_q2_posts_table.html` 原樣嵌入，上方說明列放 `q2/code/RESULTS.md` 的實測（Linux 重現、PTT 與維基各 100 萬則） |
| Q5 資料流 分頁 | Q5 FigJam 匯出圖（`q5/architecture/`）用總架構圖同款樣式重畫；工時、SLA 取自 `q5/answer/b-c-d.md` |
| 佐證總覽 分頁 | 各題 `README.md` 圖表說明 + `shared/capture/capture_manifest.json`，可依平台與題號篩選 |
| 節點面板的 GCP／地端／差異 | 每個節點對應的 Terraform 資源與 plan 狀態；新結果改寫的節點說明在 `arch_data.py` 的 `NODE_NOTE` |
| 只留「整合」一張圖（`--single`） | 拿掉 PoC／A／B／C 四張舊版 |
| Q1–Q6 各一個分頁（`--q-tabs`） | 給讀者看：每題的題目、答案重點、資料路徑、細節與限制、驗證方式、佐證圖；Q2 頁附原本的估算解剖頁，Q5 頁附資料流圖 |
| 撤下四個分頁（`--drop-tabs`） | 2026-10-31 撤下「請求時序」「Uvicorn 背景」「推演筆記」「GCP／地端」；節點面板的 GCP／地端／差異列保留 |
| 節點標題旁的 GCP 圖示（`--icons`） | Google Cloud 最新版官方圖示（`icons/`，取自 cloud.google.com/icons 的 core-products-icons.zip 與 category-icons.zip，和原頁面板用的同一套；不用 legacy 圖示包），13 個 GCP 節點。最新版沒有 Scheduler／Tasks／Pub/Sub／Firestore 專屬圖示，沿用原頁的分類圖示：Developer Tools、Data Analytics、Databases |

| 一張圖最下面的「7 觀測」帶（`--obs`，2026-11-02；11-03 改成兩列七個節點） | Cloud Logging（每個請求一行，本機 Loki＋Alloy）、Cloud Monitoring（爬蟲與採集指標，Prometheus）、服務指標（OTel→Prometheus）、ES 健康（elasticsearch_exporter）、完整性對帳（原文湖 vs ES 四層）、品質日報（金標召回／RBO）、Grafana＋Alertmanager（2 張看板 90 面板、告警 66 條 → PM 頁）各一個節點，點了看本機對應元件、GCP 對應（用最新版 Observability 分類圖示）與 Terraform 狀態（七個都還沒寫）；節點掛在 3 抓取與 6 分析兩段，沒有連線。Q6 分頁的「監控看板」放主看板、採集看板、PM 頁告警橫幅與較早的兩張圖（`cards.json` 的 `figures`） |
| 六題卡片（`--cards cards.json`） | 每題的卡片逐項核對過來源檔。Playwright 第二輪（`shared/docs/playwright-round2.md`）的新截圖與圖說更正也已併入 |

## 重建

```bash
# 底稿要用加分頁之前的版本（原架構頁的 HTML，另存為 /tmp/base.html）
python3 shared/architecture/build/build_arch_v2.py /tmp/base.html shared/architecture/qsearch-architecture.html \
  --repo . --mode repo --status shared/architecture/build/status.json --single --icons --drop-tabs --q-tabs --obs \
  [--cards shared/architecture/build/cards.json]
```

`--mode artifact` 產生發布用版本：圖片改成 `evidence/…` 並用 `--files` 輸出發布清單。

整合圖的純文字版（`shared/architecture/artifact_text/int.txt`）：

```bash
python3 shared/architecture/build/export_text.py shared/architecture/qsearch-architecture.html shared/architecture/artifact_text
```

## 檢查與截圖

```bash
python3 -m http.server 8765 --bind 127.0.0.1 &
node shared/architecture/build/qa.js /tmp/shots http://127.0.0.1:8765/shared/architecture/qsearch-architecture.html   # 桌機／手機 × 深淺色，回報錯誤與橫向溢出
node shared/architecture/build/render.js http://127.0.0.1:8765/shared/architecture/qsearch-architecture.html shared/architecture  # README 用的 PNG
```
