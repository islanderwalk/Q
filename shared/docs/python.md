# Python 實測總覽（Q1–Q6 與 parser）

驗證做了兩輪：

- **第二輪（目前，2026-10-30）**：Linux 容器（Ubuntu 24.04、Python 3.13.17、PostgreSQL 16.15、Docker 29.9.2）。每題的指令、退出碼與數字都在該題的 `code/RESULTS.md`，本檔只是總覽與待決事項。
- **第一輪**：本機 Windows 電腦（Python 3.10.8）。它的結果與限制已在第二輪逐項處理，對照見第二節；Q2 的 Windows 實測保留在 `q2/code/RESULTS.md`。

pytest 一律以 `PYTHONDONTWRITEBYTECODE=1`、`-p no:cacheprovider` 執行，repo 內沒有 `__pycache__`、`.pytest_cache`。

## 一、總覽（第二輪）

| 題 | 這輪實際跑了什麼 | pytest（退出碼） | 主要結論 | 詳細 |
|---|---|---|---|---|
| Q1 | 合成資料 40,000 封信加 2,000 份問卷，全流程退出碼 0（17.5 秒） | 45 passed（0） | 答案第 3 點每一步都有可實跑的程式。清理後前三名是同步 21.5%、閃退 19.4%、付款 15.8%，和產生器的答案一致。全部是合成資料。10/31 經確認，答案第 3 點改成「計算各問題的提及數」（373 字，不含空白與 `**`） | [`q1/code/RESULTS.md`](../../q1/code/RESULTS.md) |
| Q2 | PostgreSQL 16.15 實跑 100 萬列，重複內文與中文語料兩種；另在 Docker 編 pg_bigm 1.2，量pg_bigm 擴充套件 | 25 passed（0；10/31 加答案字數 1 項） | 原頁數字在 Linux 完全重現；Windows 上 simple 索引多 43% 已查明（60 個較新的 CJK 字被當成非字母，重現到位元組）；中文語料的兩字詞 GIN 是 947–1,165 MB，原頁的 406 MB 只是下限；pg_bigm 擴充套件 在原頁資料上是 409,567,232 B（+0.78%）；PTT 中文語料 953,729,024 B（比模擬 +0.72%），高雄、颱風、政府這類 2 字詞查詢走索引，比 pg_trgm 快 3.7 到 4.9 倍 | [`q2/code/RESULTS.md`](../../q2/code/RESULTS.md) |
| Q3 | 離線重算與重播。沒有重新抓 PTT：PTT 的 robots.txt 對 本次驗證用的 user agent 寫 `Disallow: /` | 21 passed（0；10/31 加答案字數 1 項） | README 的規模數字全部重現（890 篇/天、334 頁都剛好 20 格、規格 2／規格 1＝5,340／960＝5.6 倍）；第一輪 1.2 小時視窗的 1,781 篇/天不能外推（同樣算法在 7 天資料上得到 0–2,623 篇/天） | [`q3/code/RESULTS.md`](../../q3/code/RESULTS.md) |
| Q4 | Linux 實跑 nasa、zuck、natgeo：HTTP（帶與不帶 Sec-Fetch 標頭）與瀏覽器（桌面、手機），開頁共 21 次 | 17 passed（0） | 不帶 Sec-Fetch 標頭 3/3 是空殼，帶了 3/3 各 4 串（HTML 預排的 query 是 `first: 4`），和 README 一致；網頁自己送分頁 query 時有時無（桌面 0/15、手機 1/3）；分頁 query 的 doc_id 在 10/29、10/30 兩天出現 3 個不同的值。10/31 經確認，答案 (b) 補上 robots.txt 禁止未經書面許可自動蒐集，連空白與換行 600 字 | [`q4/code/RESULTS.md`](../../q4/code/RESULTS.md) |
| Q5 | 2,000 篇合成資料端到端；本機假 ES 5 個情境；官方 Elasticsearch 8.19.23 與 9.5.5（本機 Docker 單節點）各 21 項檢查 | 39 passed（0） | 每欄各一個時間戳，較舊的值不會蓋掉較新的值；一週欄位改名時由分區關卡停下（退出碼 3）；painless 腳本在官方 ES 跑過，與正確答案 0 筆不符、重送全部 noop。工時：關鍵路徑 46 小時、9 工作天、週一開工約 11 個日曆天 | [`q5/code/RESULTS.md`](../../q5/code/RESULTS.md) |
| Q6 | jieba 內建字典與繁體 `dict.txt.big` 各跑一次 6,685 篇 PTT 標題 | 28 passed（0） | 前 50 詞的停用詞 10 個降到 0 個（`dict.txt.big` 是 14 個降到 0 個），單字「高」222 次降到 0 次；「高雄市」新舊都是 6 次，是因為舊流程本來就沒拆，實際被拆的是「高雄」（125 篇裡拆開 115 篇） | [`q6/code/RESULTS.md`](../../q6/code/RESULTS.md) |
| parser | Cloud Run 推送端（FastAPI）；3 個 venv 跑測試；docker build 與容器 smoke | 119 passed（0） | 缺欄、型別不對、缺 bucket 或 object 的 URI 改回 422（原本被 ack 掉）；Dockerfile 加 `exec`，SIGTERM 送得到 uvicorn（停機 0.46 秒，原本 10 秒後被 SIGKILL）；以非 root（uid 10001）執行 | [`shared/infra/gcp/services/parser/RESULTS.md`](../infra/gcp/services/parser/RESULTS.md) |

pytest 合計 294 項全過（45＋25＋21＋17＋39＋28＋119；10/31 Q6 依審查加 5 項、Q2 與 Q3 各加答案字數 1 項，原本 287）。Q6 的 Elasticsearch 實測（官方 ES 加分詞器、PM 文字雲前端）由 Q6 串另外做，不在本表。

## 二、第一輪的說法與待辦，第二輪怎麼處理

| 題 | 第一輪 | 第二輪 |
|---|---|---|
| Q1 | 不寫程式 | `q1/code/` 有原型（只用標準函式庫）與 45 項測試；RESULTS 裡沒有直接寫在輸出檔的數字，由 `q1/code/checks/` 的 8 支腳本核對（`q1/data/logs/claim_checks.log`） |
| Q2 | `to_tsvector('simple')` +43%，未解釋；中文語料體積只有上下限；pg_bigm 用陣列模擬 | 已查明並重現；中文語料 100 萬則實測（PTT 問答與中文維基）；pg_bigm 擴充套件 在 Docker 裡量了原頁方式與 PTT；第一輪查詢比較裡 pg_trgm 2 字詞的 9.2 到 10 秒，查明是 psycopg 自動 prepare 後 PostgreSQL 改用 generic plan，已重現並修正 |
| Q3 | 確切請求數沒寫進 log；`ptt_q3_census.py` 沒實跑、可能覆蓋 repo 內的資料；1.2 小時視窗的 1,781 篇/天與 README 不一致 | 三支抓取程式每次抓取都在 `run_summary.jsonl` 加一行（中斷也寫），預設間隔改成 1.5 秒；沒給 `--out` 時不會寫進 repo 內的資料；舊普查的請求數推得至少 6,237、最多約 6,247 次；短視窗不能外推已用 7 天資料證明 |
| Q4 | 純 HTTP 0 篇，與 README 不一致；瀏覽器模式沒跑 | 0 篇的原因是少了 `Sec-Fetch-Mode`、`Sec-Fetch-Dest`；瀏覽器桌面與手機都跑了（Chromium 用容器內建的） |
| Q5 | 11 passed；DE 關鍵路徑 42 小時、約 8 工作天（約 10 日曆天）；BE、FE 工時未核對；示範沒留 log | 39 passed；46 小時（漏算全員驗收 4 小時）、9 工作天、週一開工約 11 日曆天，BE、FE 已核對；示範由 `demo_cli.sh`、`demo_real_es.sh` 從 repo 重跑，log 在 `q5/code/logs/` |
| Q6 | `dict.txt.big` 未下載；估算 26–28 工作天；外部依據未查證 | 已下載（sha256 `b16011275c42955c…`）並對照；估算改成與答案稿一致的 45 工作天；ES、jieba 的外部依據附網址與 sha256 |
| parser | 契約測試在 repo 外（6 項） | 測試進 repo（`tests/`，119 項），修了兩個 bug（見總覽） |

## 三、建議的驗證徽章文字（每題 1–2 行）

- **Q1**：合成資料 40,000 封信、2,000 份問卷實跑，pytest 45 項全過；清理後前三名同步、閃退、付款。
- **Q2**：PostgreSQL 16.15 實跑 100 萬列，原頁數字在 Linux 完全重現，pg_bigm 擴充套件 只多 0.8%；中文語料的兩字詞索引約 1 GB，原頁 406 MB 是下限。
- **Q3**：用 7 天實抓資料離線重算，README 數字全部重現（890 篇/天、5.6 倍）；PTT robots.txt 不允許本次驗證用的 user agent，這輪沒有重抓。
- **Q4**：Linux 實測 3 個帳號：帶 Sec-Fetch 標頭各 4 串、不帶是空殼；網頁分頁 query 時有時無，doc_id 會換。
- **Q5**：回補腳本在官方 Elasticsearch 8.19.23 與 9.5.5 實跑，0 筆不符、重送全部 noop；pytest 39 項全過。
- **Q6**：6,685 篇 PTT 標題實測：停用詞 10→0、單字「高」222→0；「高雄市」沒被拆，被拆的是「高雄」。
- **parser**：119 項測試全過；修正兩個會把壞訊息 ack 掉、讓容器無法正常停機的問題。

## 四、未完成與需要決定的事

1. **Q4 合規（10/31 已處理）**：Threads 的 robots.txt 開頭寫未經 Threads 書面許可禁止自動化蒐集。規則上 `User-agent: *` 與 GPTBot 等 14 組列名的爬蟲是 `Disallow: /`，Googlebot、Bingbot 等 22 組列名的爬蟲只擋部分路徑，所以先前「對所有 user agent 都是 `Disallow: /`」的說法不精確。已決定補進答案：(b) 末尾加「另 robots.txt 禁止未經書面許可自動蒐集。」，其他句子做了 8 處不改意思的縮短（列在 `q4/code/RESULTS.md` 最後一節），連空白與換行 600 字（不含空白 521 字），由 `q4/code/tests/test_q4_answer_length.py` 檢查。這輪的實測都在每站 30 次開頁的上限內，修正那一輪沒有再連 Threads。
2. **Q3 實抓**：PTT 的 robots.txt 不允許 本次驗證用的 user agent，所以驗證沒有重抓。`q3/code/RESULTS.md` 附了在自己電腦補跑的指令（列表最多 120 個請求、普查最多 1,080 個、間隔 1.5 秒）。另一個截圖流程（`capture_round2_cloud.js` 的 q3fix 模式）在 10/30 20:29（+08）載入同一篇文章頁 2 次，`q3/README.md` 已註明。
3. **Q2**：pg_bigm 擴充套件 量了原頁方式與 PTT，中文維基沒有重跑（模擬 1,165 MB）；pg_bigm 跑在 Docker 容器裡，不是原生安裝。維基的「颱風」「政府」兩格 pg_trgm 時間是 generic plan 量到的，資料庫已刪，沒有用修正後的程式重量。編 pg_bigm 的 Dockerfile 把 `postgresql-server-dev-16` 釘在映像的版本，PGDG 出下一個 16.x 後要改從 apt-archive 裝（獨立審查指出，這輪沒改共用的 Dockerfile）。
4. **Q5**：QSearch 正式 ES 的版本與 mapping 不知道，上線前要在 staging 對正式 mapping 跑一次 `demo_real_es.py`；舊欄位名都是假設，要對 GCS 原始檔盤點；HTTP 429／500 只在假 ES 上模擬。
5. **Q1**：全部是合成資料，規則與產生器同一人寫，準確度只是上限；實際準確度要等 300 則人工標註。答案第 3 點原本寫「計算三個候選問題的提及數」，原型是 8 類全部計數再取前三；10/31 經確認改成「計算各問題的提及數」，字數 376 → 373（不含空白與 `**`），RESULTS、測試說明與兩份 log 一起更新。
6. **根目錄 README 的數字**（各題 pytest、Q2 狀態、Q1 索引列、Q5 工時）由 GitHub 串更新，本串只提供要改的數字，見第五節。

## 五、根目錄 README 要改的地方（行號以 1b4ee71 為準）

README 的 MB 以 1,048,576 B 計；各題 RESULTS 的 MB 是 10^6 B（例如 946,946,048 B 在 RESULTS 寫 947 MB，在 README 是 903 MB）。

| 行 | 現在 | 建議改成 |
|---|---|---|
| 37 | `code/` 說明「Q2、Q6 另有 RESULTS.md」 | 每題的 `code/` 都有 RESULTS.md（實跑指令、退出碼、結果） |
| 54 | Q1 證據「B（推論，無資料可實測）」 | B（推論）；答案第 3 點每一步都有原型，用合成資料 40,000 封信加 2,000 份問卷實跑（pytest 45） |
| 56 | Q2 證據「…與頁面差 0.07% 以內，simple 索引差 43%」 | A（PostgreSQL 16.15 實跑 100 萬列：Linux 的表與 simple 和頁面逐位元組相同，兩字詞 −0.03%、pg_trgm +0.13%；pg_bigm 擴充套件 +0.78%；Windows 的 simple +43% 已查明）。關鍵數字可補「中文語料 100 萬則的兩字詞索引 903–1,111 MB」 |
| 59 | Q5「約 8 工作天（約 10 日曆天）」 | 9 工作天，週一開工約 11 日曆天（關鍵路徑 46 小時） |
| 621–628 | 「重跑用 Windows…沒有 pg_bigm」，simple「+43%，原因未查」 | Linux 重跑：表、simple 與頁面相同，兩字詞 −0.03%、pg_trgm +0.13%；Windows 的 +43% 是把 U+9FC4–U+9FFF 這 60 個字當成非字母（在 Linux 重現到位元組）。可加一列「pg_bigm 擴充套件（Docker）409,567,232 B，+0.78%」 |
| 720 | Q5 (c)「先寫新 index…以 `crawled_at` 當 external version…alias 切換」 | 預設對線上 index 部分更新（bulk `update`＋painless），每個計數欄位各帶 `counts_at.<欄位>`，較舊的值不會蓋掉較新的；新 index 加 alias 是備案。已在官方 Elasticsearch 8.19.23、9.5.5 實跑 |
| 735、739、767 | 42 小時、約 8 工作天、約 10 個日曆天；「前提是 API 契約在第 2 天前定案」；「回補原型的測試共 11 項」 | 關鍵路徑 46 小時（資料工程師 42 小時加全員驗收 4 小時）；46 ÷ 8 ＝ 5.75，加 30% 取 8，再加 1 天緩衝共 9 工作天，週一開工約 11 個日曆天；API 契約最晚在第 3 天結束前定案；測試 39 項（backfill 34、schedule 5） |
| 799 | pytest 35 項（Q2 9、Q3 4、Q4 2、Q5 11、Q6 9） | 294 項全過：Q1 45、Q2 25、Q3 21、Q4 17、Q5 39、Q6 28、parser 119（Q6 串的 ES 實測另計；10/31 Q2、Q3、Q4 各加答案字數測試 1 項、Q6 依審查加 5 項，原本 286） |
| 800 | Q2「pg_trgm +0.07%；`simple` +43% 原因未查」 | 同第 621–628 行；另寫中文語料兩字詞索引 903–1,111 MB、pg_bigm 擴充套件 +0.78% |
| 807 | parser「pytest 13 passed」 | 119 passed（與 Terraform 整理合併後，3 個 venv）；docker build 與容器 smoke，非 root（uid 10001），SIGTERM 停機 0.46 秒 |
| 820 | Q6 兩份工時估算未對齊（26–28 vs 45 工作天） | 已對齊為 45 工作天（`q6/code/PLAN.md`），這句可刪 |