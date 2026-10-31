# 交接手冊：QSearch 筆試 Q1–Q6 整理（最後更新 2026-10-31 08:40 UTC）

給接手的人：整份讀完就能接手。**PR #1 已 merge 進 master（db50cde）**。第二次整理在分支 `claude/project-thread-xej5op`（併入 `python-verification` 與 `claude/q6-diagrams-evidence`，並把根目錄收成 README＋q1–q6＋shared/）；合併後從 master 開新分支。
規則：每完成一個階段就更新本檔並 push，讓別的帳號隨時能接手。

## 目標（使用者原始需求）
1. 把本 repo 依 Q1–Q6 分資料夾整理，不要散落；總架構 artifact 轉成根目錄 `README.md`。
2. 多代理分工：terraform_GCP（GCP 架構真實驗證＋地端架構）、python（程式真實環境驗證）、playwright（FB / PTT / Threads / IG Reels 真實截圖，已有的不重抓，只補缺）、UIUX（把所有佐證整理進總架構圖）、GitHub（整理、提交）。
3. 總架構圖要整合：原總架構 https://claude.ai/artifact/CMCjqnWwiTYxcAukZHS2pT 、Q2 https://claude.ai/artifact/ESxb1xrXdAiT69ocVzbi43 、Q5 FigJam https://www.figma.com/board/69PjSj1MiGUqCfWTWIkzUa （已匯出 PNG 在 `q5/architecture/`）。
4. 研究不夠深的題目自行補研究。題目全文：[`shared/docs/qsearch_questions.md`](qsearch_questions.md)（來源 Google Drive「QSearch 資料工程師筆試題目 - v2026.07」）。

## 資料夾結構（2026-10-30 第二次整理後）

```
README.md          總架構（由總架構 artifact 轉成）＋ Q1–Q6 索引
q1/ … q6/          每題一個資料夾，子資料夾名稱統一，只建用得到的：
  README.md          題目、答案、證據索引
  answer/            答案全文與逐小題圖表（可再依小題分子資料夾，例如 q3/answer/1-scale/）
  architecture/      架構圖、資料流圖；src/ 是可編輯原檔與渲染腳本
  code/              程式；tests/ 是 pytest；RESULTS.md 是實跑結果
  data/              實測輸出（CSV、JSON、log）
  evidence/          真實截圖；playwright/ 是共用腳本抓的，src/ 是截圖腳本，其餘多半依小題分（q4/evidence/ 根層 3 張與 q6/evidence/realenv/ 例外）
  realenv/           只有 q6：真實環境的整套 Docker（官方 Elasticsearch、PostgreSQL、Pub/Sub 模擬器與監控），自帶 README、RESULTS 與 run.sh（PR #8 加入）
shared/            跨題共用
  architecture/      總架構 HTML／PNG、官方圖、GCP 與地端對照、build/（建置腳本與資料）、artifact_text/（各分頁純文字版）
  infra/gcp/         Terraform（Q2–Q6 共用）；infra/onprem/ 地端對照；infra/snippets/ 對照表
  capture/           Playwright 共用截圖腳本、manifest、fixtures
  docs/              本檔、題目原文、各代理交接筆記
```

## 已完成
| 階段 | 狀態 | 證據 |
|---|---|---|
| GitHub 重整 | ✅ 第一次：42 個 `git mv`，PR #1 已 merge。第二次：根目錄只剩 README.md、q1–q6、shared/；每題子資料夾統一；183 處連結與 Q3/Q4/Q5 腳本路徑已改，0 個斷連結，pytest 35 項全過 | https://github.com/islanderwalk/claude-code/pull/1 、分支 `claude/project-thread-xej5op` |
| GCP Terraform | ✅ `shared/infra/gcp/`；`fmt`/`init`/`validate` exit 0；**第一輪 `terraform plan` 對真實 project 成功：70 to add, 0 change, 0 destroy**（未 apply、未啟用 API）。**第二輪（PR #7，4a33886、67f0a79，已併入 #3）**：補 Cloud Tasks→worker 的 OIDC 權限鏈、每站限速佇列 crawl-ptt／crawl-threads、Q3 的 ptt-crawl Job 與每小時 Scheduler、Cloud SQL pg_bigm／IAM 登入 flag 與備份、Secret Manager（不存值）等；離線 plan **102 to add**、`terraform test` 30 passed、tflint 0；整理 PR 時用 Terraform 1.17.1 獨立重跑 fmt／validate／離線 plan／test，結果相同。新增的 32 項還沒對真實 project plan 過 | [`shared/infra/gcp/README.md`](../infra/gcp/README.md)、[`shared/docs/gcp.md`](gcp.md) |
| 地端架構 | ✅ 對照表＋可實際啟動的 docker compose（9 個常駐服務），smoke 19 項連續 3 次全過，pg_bigm 1.2 實測走索引（見 `shared/infra/onprem/VERIFY.md`） | [`shared/infra/onprem/`](../infra/onprem)、[`shared/architecture/gcp-vs-onprem.md`](../architecture/gcp-vs-onprem.md) |
| Playwright 補證據 | ✅ Q2 FB 單則貼文（留言者已遮）、Q5 IG `play_count` 對照、Q6 PTT 斷詞雜訊 4 張、Q3 圖表重產 | [`shared/docs/playwright.md`](playwright.md)、[`shared/capture/capture_manifest.json`](../capture/capture_manifest.json) |
| 總架構 | ✅ https://claude.ai/artifact/Bg2W8o5WkhVLpniJTXtRPm ：以原 artifact 原始碼為底，**同色同版面**（使用者明確要求「一模一樣」，不可重新設計），第一版只加 Q1/Q6 依題目按鈕與每題「驗證」「真實佐證」區；v2（PR #4）再加 GCP／地端、Q2 估算、Q5 資料流、佐證總覽四個分頁與節點細節，並依要求只留「整合」一張圖、加上 GCP 官方圖示。10/31 依要求撤下請求時序、Uvicorn 背景、推演筆記、GCP／地端四個分頁，拿掉面試說法、口訣與內部備註（因為頁面要給面試官看），卡片改成最終數字（65130f9、2e8b7c2）；同日再改成一張圖、Q1 到 Q6 各一頁（題目、答案重點、資料怎麼走、細節與限制、怎麼驗證的、還沒驗證的部分、該題佐證；Q2 頁含估算解剖頁，Q5 頁含資料流圖）、佐證總覽（98 張），分頁 id 是 shape、q1、q2、q3、q4、q5flow、q6、evidence（988650f，`--q-tabs`）。v11（f9160bb）：Q1 到 Q6 題目照 Drive 原文逐字，一張圖的 Q3 改成 BigQuery 節點（parser 直接寫入，pg_bigm 只標 Q2），佐證圖改成頁內放大，Q2、Q3 測試數 25、21。做法：解出原頁 `const D` 資料，加 `questions["1"]`、`["6"]` 與 `evidence`/`verify`，Q1/Q6 只點亮既有節點。repo 版 `shared/architecture/qsearch-architecture.html`（圖片用 repo 相對路徑） | `shared/architecture/` |
| Python 實測 | ✅ 第二輪（PR #6，雲端 Linux 容器，已併入 #3）：pytest 294 項全過，Q1 45、Q2 25、Q3 21、Q4 17、Q5 39、Q6 28、parser 119（10/31 PR #9 加 Q4 答案字數測試、PR #10 依 Q6 審查加 5 項、收尾分支加 Q2 與 Q3 答案字數測試各 1 項，原本 286 項），整理 PR 時獨立重跑，併入後也是 294 項。Q2 原頁數字在 Linux 重現，Windows 的 simple +43% 已查明，真實中文 100 萬則的兩字詞索引 947–1,165 MB（10^6 B），真的 pg_bigm +0.78%；Q5 回補在官方 ES 8.19.23、9.5.5 實跑。各題指令、退出碼與數字在該題 `code/RESULTS.md`，總覽與待決事項在 python.md | [`shared/docs/python.md`](python.md) |
| Q5 (b)(c)(d)、Q6、Q1 | ✅ `q5/answer/b-c-d.md`＋`q5/code/backfill/`、`q5/code/schedule/`（回補原型、工時 9 工作天，週一開工約 11 日曆天）、`q6/code/`（斷詞 pipeline、RESULTS.md、PLAN.md，工時 45 工作天）、`q1/code/`（答案第 3 點的原型）、`q1/README.md` 答案草稿 | 各資料夾 |
| Q6 真實環境 | ✅ PR #8（2f2e648，已併入 #3）：`q6/realenv/` 用官方 Elasticsearch 9.5.5（smartcn、ICU、IK）、PostgreSQL 16、Pub/Sub 官方模擬器、Prometheus／Grafana 在 Docker 跑完回補、串流、字典發版與 PM A/B 頁，零成本；架構圖與沙盤推演圖在 `q6/architecture/realenv/`，截圖 30 張在 `q6/evidence/realenv/` | [`q6/realenv/RESULTS.md`](../../q6/realenv/RESULTS.md) |
| Q6 審查四點 | ✅ PR #10（f1a14a6，合併 commit c52527b）：依 10/31 審查的四點追問補驗。150 個標題（三組獨立的模型標註，兩組以上同意算金標）的金標詞召回舊流程 64.7%、10/30 規則 74.4%、10/31 規則 75.6%；ES terms 聚合命中 300 萬篇 317 ms（抽樣 1% 14 ms）；版本指紋涵蓋所有輸入，原文另記 `content_sha`；增量重跑以固定 total 為前提；正式答案 599 字（連空白與換行，有字數測試） | [`q6/README.md`](../../q6/README.md)「2026-10-31 審查四點的處理」、[`q6/code/RESULTS.md`](../../q6/code/RESULTS.md) 最後一節 |
| Q6 架構圖與佐證 | ✅ 分支 `claude/q6-diagrams-evidence` 已併入整理分支：9 張架構圖、21 張 Playwright 佐證、599 字答案稿，依子題放在 `q6/architecture/`、`q6/evidence/`、`q6/answer/`；jieba 實驗的程式與輸出放在這個分支新增的 `q6/code/jieba_experiments/` 與 `q6/data/jieba_experiments/`；另一個 session 在 `q6/code/`、`q6/data/`、`q6/evidence/playwright/` 的既有檔案沒動。總覽圖把斷詞畫成 parser 之後的獨立 Cloud Run 服務（與回補 Job 同一映像） | [`q6/README.md`](../../q6/README.md) |
| 根目錄 README | ✅ 由總架構 artifact 轉成文字＋Mermaid 圖（GitHub 可渲染，用 mermaid 10.9.1 實測 0 錯誤），只留整合圖（2026-10-30 決定）；10/31 跟著互動版刪去請求時序、Uvicorn 背景、推演筆記三段與 GCP 分頁截圖，也拿掉寫給自己看的說法（README 要給面試官看），剩 3 張 Mermaid 圖；5 個查核代理逐段比對來源後套用 36 處更正；0 斷連結 | [`README.md`](../../README.md) |
| 併入其他 thread | ✅ UIUX 總架構 v2（PR #4：cfc725a、bf86b23，只留整合圖＋GCP 官方圖示）與 Playwright 第二輪（PR #5：72a6463，8 張新圖、4 張補遮）、UIUX 逐題核對的 Q1–Q6 卡片（fc76fb5）都 fast-forward 進 PR #3 分支；之後併入 Terraform 第二輪（PR #7）、UIUX 頁面更新（ca45f9f）、Python 第二輪（PR #6，快轉到 cbd25a2）、Q6 真實環境（PR #8，合併 commit 975f93d）、Python 的 Q1、Q4 答案修正（PR #9，合併 commit 65e42aa）與 Q6 審查修正（PR #10，合併 commit c52527b）；各題 README 的圖說已照 manifest `correction` 欄位更正 | PR #3 |

## 未完成（依序接手）
1. **10/31 已決定**（PR #9，詳見 [`shared/docs/python.md`](python.md) 第四節）：Q4 答案 (b) 末尾補「另 robots.txt 禁止未經書面許可自動蒐集。」（連空白與換行 600 字，有字數測試）；Q1 答案第 3 點改成「計算各問題的提及數」（373 字）。Threads 的 robots.txt 對 `User-agent: *` 與 GPTBot 等 14 組列名的爬蟲是 `Disallow: /`，Googlebot、Bingbot 等 22 組只擋部分路徑。其他已知限制：Q3 沒有重抓 PTT（robots.txt 不允許 這輪驗證環境的 user agent，自己電腦補跑的指令在 `q3/code/RESULTS.md`）；Q2 中文維基沒用真的 pg_bigm 重量，編 pg_bigm 的 Dockerfile 版本釘死；Q5 要在 staging 對正式 mapping 跑一次 `demo_real_es.py`。第一輪列的其他缺口都已在第二輪處理。
2. **答案定稿**：六題都是字數上限內的正式答案（python len，空白與換行都算）：Q1 397、Q2 585、Q3 785、Q4 600、Q5 598（檔案含檔尾換行是 599）、Q6 599。合併版 `shared/docs/submission.md` 由 `shared/docs/build_submission.py` 產生，改任何一題答案後要重跑。q1/README.md 的段落標題已由「答案（草稿）」改成「答案」，`q1/code/checks/answer_len.py` 也改成用標題比對切段。
3. 總架構若要更新：artifact 是使用者自己的副本 Bg2W8o5WkhVLpniJTXtRPm（原版 CMCjqnWwiTYxcAukZHS2pT 不在本 org、無法原地改；DjJWPGpRtbBFARDD2g7MzH 是先前另發的版本，已不用）。現在的建置腳本是 `shared/architecture/build/build_arch_v2.py`（`--single --icons --cards`，`--mode artifact` 產生發布版與檔案清單），步驟見 [`shared/architecture/build/README.md`](../architecture/build/README.md)；節點面板的 GCP／地端資料在 `build/arch_data.py`（GCP／地端分頁 10/31 已撤下），已含第二輪 Terraform（離線 plan 102 項、terraform test 30 passed）與地端 smoke 19/19；Q2 卡片與 Q2 分頁已換成 PR #6 的實測（Linux 重現、真實中文 100 萬則）。不要重新設計樣式。
4. 分支 `q5-assets` 是 `q5/` 的重複舊版，可刪；`claude/project-thread-o3w4pk` 多出的兩個 commit 是加遮罩再撤回，淨變更為 0，可刪；`python-verification`、`claude/q6-diagrams-evidence`、`ptt-q3-measure`、`restructure-q1-q6` 與其他 `claude/project-thread-*` 分支都已併入整理分支，合併後也可刪。
5. **Q6 的斷詞位置**：工時估算已對齊為 45 工作天（`q6/code/PLAN.md` 與答案稿相同）。斷詞的位置兩邊畫法不同：總架構畫在 parser 內，q6 設計是 parser 之後的獨立 Cloud Run 斷詞服務、回補 Job 用同一映像；根目錄 README 六段管線表「5 解｜斷詞 pipeline（Q6）」列與元件表回補列都已註明。

## 待使用者決定（GCP 代理提出）
最新清單見 [`shared/infra/gcp/README.md`](../infra/gcp/README.md)「待使用者決定」（第二輪已處理 backfill 專用 SA；成本與 Q2 預算對應的 Cloud SQL 規格、checkov 剩下的項目都列在那裡）。

## 已知缺口
- 答案與 Terraform、整合圖的差異：整合圖 10/31 已把 Q3 改成 BigQuery 節點（pg_bigm 只標 Q2），但 BigQuery 表與查詢 API 沒有 Terraform、查詢延遲沒實測，整合圖把 PTT 的解析畫在共用的 parser，Q3 答案是 Job 自己解析後直接寫入；Q2 答案為了放進每月 NT$3,000 選 db-custom-1-3840（約 NT$2,000），Terraform 預設仍是 db-custom-2-7680、POSTGRES_15（`shared/infra/gcp/variables.tf`）。
- IG 單支 Reel 頁無觀看數（只能從 Reels 分頁 JSON 的 `play_count` 取）。FB 粉專數字 ID 與絕對發文時間已在第二輪補齊（住宅 IP 未登入：`q2/evidence/playwright/fb_page_transparency_id.png`、`fb_post_time_and_ids.png`；雲端 IP 會被導去登入頁）；Q6 的短網址已有 reurl.cc、tinyurl.com、bit.ly 三種網域。
- Terraform 未涵蓋：Elasticsearch 叢集本體（只建 API key 的 Secret）、Redis／Memorystore、Firestore session 池與 session refresher、Vector DB、Data Layer／Analytics／Export API、監控告警、parser 等映像的建置、GKE workloads；pg_bigm 只開了 flag，`CREATE EXTENSION` 還沒在真實資料庫跑過；第二輪新增的 32 項只跑過離線 plan 與 `terraform test`。清單見 shared/infra/gcp/README.md「未覆蓋」。
- Q6 審查後還沒證明的：漏掉的金標詞主要是字典沒有的人名與新詞，以及 jieba 切成兩段的複合詞；5 億篇查詢時間是單節點線性外推；全量重跑的寫入上限是假設；`q6/data/segmented_ab.jsonl` 沒有依 10/31 規則重產（前三項見 [`q6/README.md`](../../q6/README.md)「2026-10-31 審查四點的處理」，segmented_ab.jsonl 見同檔「斷詞資料與字典」一節）。
- Q3–Q6 原雲端 session（claude.ai/code/cshk_…）本帳號瀏覽器未登入讀不到；Q6 已依題目重新研究。

## 規則（必守）
- 只抓未登入公開頁；每站導覽間隔 ≥1.5–2 秒、每站 ≤30 次載入；不存 cookie/token/cursor；不用代理、不偽裝指紋、不繞登入牆。帳號遮罩不是必要項目：2026-10-30 決定 PTT 是公開資料、帳號不用遮，PTT 截圖維持原圖；FB／IG／Threads 既有的遮罩保留，不要再為了遮帳號重拍或額外加工。
- 不 `terraform apply`、不啟用 GCP API、不 merge PR。state、tfplan、`.terraform/` 不進 repo。
- 本機工具：terraform 1.16.6（winget）、gcloud 591.0.0（`%USERPROFILE%\google-cloud-sdk`，已登入）；換帳號/機器需自行安裝與登入。
