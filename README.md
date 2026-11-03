<a href="https://claude.ai/artifact/Bg2W8o5WkhVLpniJTXtRPm">
  <img src="shared/architecture/readme-hero.png" width="100%" alt="點我開啟互動架構圖：一張整合架構圖，加上 Q1–Q6 各一頁（題目、答案、資料流、實測佐證）">
</a>

<p align="center">
  <a href="https://claude.ai/artifact/Bg2W8o5WkhVLpniJTXtRPm"><b>點我開啟互動架構圖 →</b></a><br>
  一張整合架構圖，加上 Q1–Q6 各一頁（題目、答案、資料流、實測佐證）
</p>

## 六題答案

**[一份看完：六題合併答案 →](shared/docs/submission.md)**

| 題目 | 字數 | 上限 |
|---|---:|---:|
| [Q1 顧客反饋整理](shared/docs/submission.md#q1) | 397 | 400 |
| [Q2 估算與選型：100 萬則 FB 貼文搜尋](shared/docs/submission.md#q2) | 585 | 600 |
| [Q3 PTT 全文搜尋系統](shared/docs/submission.md#q3) | 785 | 800 |
| [Q4 Threads 爬蟲系統](shared/docs/submission.md#q4) | 600 | 600 |
| [Q5 新指標上線的全鏈路時間估算](shared/docs/submission.md#q5) | 598 | 600 |
| [Q6 文字雲斷詞 Pipeline 重建](shared/docs/submission.md#q6) | 599 | 600 |

<sub>字數用 Python `len` 計算，空白與換行都算；Q2、Q3 的算式與 Q3 的架構圖依題目不計入字數。</sub>

---

### 程式碼

各題的程式、測試（pytest）與實跑紀錄（`RESULTS.md`）在各題資料夾的 `code/`。

- [Q1 顧客反饋整理](q1/)
- [Q2 估算與選型：100 萬則 FB 貼文搜尋](q2/)
- [Q3 PTT 全文搜尋系統](q3/)
- [Q4 Threads 爬蟲系統](q4/)
- [Q5 新指標上線的全鏈路時間估算](q5/)
- [Q6 文字雲斷詞 Pipeline 重建](q6/)

Q6 另有 [`q6/stack/`](q6/stack/)：整套系統的 Docker 組態與實跑紀錄（官方 Elasticsearch 9.5.5、PostgreSQL 16、Pub/Sub 模擬器、斷詞服務、文字雲 API 與 PM A/B 頁、Prometheus／Grafana／Loki／Alertmanager 監控），`run.sh` 一鍵啟動。

共用程式碼與 infra 在 [`shared/`](shared/)：[GCP Terraform 與 parser 服務](shared/infra/gcp/)・[地端 docker compose](shared/infra/onprem/)・[Playwright 截圖腳本](shared/capture/)・[題目原文與合併答案](shared/docs/)

### 佐證截圖

| 題目 | 位置 | 內容 |
|---|---|---|
| Q2 | [`q2/evidence/playwright/`](q2/evidence/playwright/) | FB 粉專與單則貼文：Page ID、內嵌的絕對發文時間、資料中心 IP 被導去登入頁 |
| Q3 | [`q3/evidence/playwright/`](q3/evidence/playwright/)、[`q3/answer/`](q3/answer/) | PTT 看板索引、文章與推文、over18 關卡；各小題的量測圖表 |
| Q4 | [`q4/evidence/`](q4/evidence/) | Threads 個人頁、桌機與手機版、GraphQL 回應、貼文網址 |
| Q5 | [`q5/evidence/`](q5/evidence/) | IG Reels 的 `play_count` 與單支頁、Threads 轉發數漂移；回補資料流 |
| Q6 | [`q6/evidence/`](q6/evidence/) | PTT 斷詞雜訊、回補／字典／一致性各子題；[`stack/`](q6/evidence/stack/) 是整套系統的 PM A/B 頁、Grafana／Prometheus／Alertmanager／Kibana 與六渠道公開頁截圖；[`crawl/`](q6/evidence/crawl/) 是六渠道爬蟲監測看板 |
| 登入帳號重爬（Facebook、Instagram、Threads） | [`q6/evidence/meta-login/`](q6/evidence/meta-login/) | 同一批目標頁用登入帳號再爬一次：Facebook 4 → 65、Instagram 48 → 1,134、Threads 59 → 588 篇，登入牆 0 次；每個目標頁有頁面與 F12 Network 截圖各 2 張，共 60 張，加上 cookie → API → JSON → 欄位的證據鏈（Q2、Q4、Q5、Q6） |

全部證據依題目與平台整理在 [`shared/docs/evidence.md`](shared/docs/evidence.md)：實際網站截圖、F12／API／JSON 證據、登入 session、壓測與監控過程、實跑數字。每張截圖的主張、來源網址與截取時間列在 [`shared/capture/capture_manifest.json`](shared/capture/capture_manifest.json) 與各題 README。
