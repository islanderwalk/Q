# QSearch 資料工程師筆試 Q1–Q6

## 架構圖

請看這：**https://claude.ai/artifact/Bg2W8o5WkhVLpniJTXtRPm**

一張整合架構圖，加上 Q1–Q6 各一頁（題目、答案、資料流、實測佐證）。

## 程式碼

各題程式碼請看 Q1–Q6 資料夾。每題的 `code/` 放程式、`tests/`（pytest）與 `RESULTS.md`（實跑指令與結果）。

| 題目 | 資料夾 |
|---|---|
| Q1 顧客反饋整理 | [q1/](q1/) |
| Q2 估算與選型：100 萬則 FB 貼文搜尋 | [q2/](q2/) |
| Q3 PTT 全文搜尋系統 | [q3/](q3/) |
| Q4 Threads 爬蟲系統 | [q4/](q4/) |
| Q5 新指標上線的全鏈路時間估算 | [q5/](q5/) |
| Q6 文字雲斷詞 Pipeline 重建 | [q6/](q6/) |

## 共用程式碼與 infra：`shared/`

| 資料夾 | 內容 |
|---|---|
| [shared/infra/gcp/](shared/infra/gcp/) | GCP Terraform（Q2–Q6 共用）與 parser 服務 |
| [shared/infra/onprem/](shared/infra/onprem/) | 地端版（docker compose） |
| [shared/capture/](shared/capture/) | Playwright 截圖腳本 |
| [shared/docs/](shared/docs/) | 題目原文與六題合併答案 |
