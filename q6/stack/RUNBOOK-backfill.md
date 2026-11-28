# 人工回補 runbook

回補 Job 自己處理不了、要人介入時照這份做。「狀態紀錄」指 ES 索引 `backfill_ledger`（一個分區一篇，分區＝渠道 × 月，編號像「ptt/2026-10」，_id 就是分區編號；PostgreSQL 的 `backfill_ledger` 表已停用、只留作歷史對照）。

## 0. 適用範圍（先看）

- **只適用本機整套系統（`q6/stack`）。** 每一條都已逐條對照程式查證過（行號以 2026-11-11 為準），但第 2、5、6、7 節的指令**沒有實際執行過**，第一次用之前先在本機整套系統試一次。
- **正式環境（GCP）目前沒有可執行的人工回補指令**：`shared/infra/gcp` 從來沒有 apply；Terraform 裡的 Cloud Run Job `argus-backfill` 的容器只設了映像，加上設了 `q6_raw_bq_table` 時才有的三個原文來源變數（`RAW_SOURCE`／`RAW_BQ_TABLE`／`RAW_BQ_PROJECT`），其他環境變數、參數與 secret 都沒有（`shared/infra/gcp/run.tf:122-160`，容器見 142-156），映像預設還是範例映像（`variables.tf:67-70`）。缺什麼列在第 8 節。
- 指令都在 `q6/stack` 目錄下、用 **Git Bash 或 WSL** 跑（和 `run.sh` 同一環境）；`V=…; … $V` 是 bash 寫法，PowerShell 不能直接貼。
- `V` 是這一輪回補的字典版本，`run.sh` 預設 `argus-q6-2026.10.30.3`（`run.sh:52、65`）。注意 compose 裡 `seg-job` 自己的預設是 `.2`（`docker-compose.yml:93`），所以指令一定要帶 `-e DICT_VERSION=$V`。
- 執行前 `es`、`pg`、`dictsrv` 要已經在跑（`./run.sh up`）。下面的 `docker compose run` 都帶 `--no-deps`，不會替你啟動它們；字典服務連不上時 Job 一開始就結束（`job.py:393`）。

## 1. 先看狀態紀錄

```bash
curl -s "http://127.0.0.1:8080/api/ops/ledger?status=failed"
```

`status` 可填 `pending`／`running`／`awaiting_pm`／`passed`／`failed`，不填＝全部（`api/app.py:647-651`）。每列要看：`status`、`attempts`、`checkpoint_line`、`raw_count`、`errors`、`dict_version`、`gates`。

PM 頁 http://127.0.0.1:8080 的回補帳本只顯示狀態、篇數、每秒篇數與 G1–G4 燈號（`web/app.js:251-259`）；`attempts`、`checkpoint_line`、`dict_version`、`attempts_exhausted` 只有上面的 curl 看得到。PM 頁的「閘門沒過」標籤對寫入出錯、被領滿的列也會出現，**判斷一律以 curl 為準**。

| 看到什麼 | 意思 | 去哪一節 |
|---|---|---|
| `awaiting_pm` | G1–G3 都過了（或已人工豁免），而且這個渠道還沒有任何分區通過，等 PM 簽核 | 3 |
| `failed`，`checkpoint_line` ＝ `raw_count`，`gates` 裡 G1–G3 有 `pass=false` | 整個分區寫完了，閘門沒過 | 2 或 4 |
| `failed`，`checkpoint_line` ＜ `raw_count` | 寫入 ES 出錯就中斷（`job.py:272-276`）。這條路徑**不會清掉舊的 `gates`**，所以 `gates` 可能是上一次的結果，以日誌的 `partition_failed` 確認 | 2（不要豁免） |
| `failed`，`gates` 只有 `attempts_exhausted` | 被領滿 3 次還沒跑完（`BACKFILL_MAX_ATTEMPTS`，`job.py:174-178`） | 2（不要豁免） |
| `running`，心跳超過 2 分鐘 | 見下方 | — |

`running` 心跳超過 2 分鐘時（`job.py:177、192-198`）：
- 有 task 在跑、`attempts` 還不到 3、同渠道沒有其他分區擋住 → 下一次領取會自動接手，從 `checkpoint_line` 接續。
- `attempts` 已到 3 → 下一次有 task 領取時被改成 `failed`（`attempts_exhausted`），走第 2 節。
- 沒有任何 task 在跑 → 不會有人接手，到第 6 節重開 task。
- 心跳是在讀完原文之後才開始（`job.py:228-233`）。本機 `RAW_SOURCE=file` 會先把整個分區檔讀進來；`bigquery` 只先跑一個計數查詢，通常快很多。分區很大、讀原文超過 2 分鐘時，別的 task 可能把它當成死掉重領，變成兩個 task 同時寫。`checkpoint_line` 一直是 0 時先看日誌是否還在讀原文。

其他規則：
- `failed` 的分區 Job **不會自動重領**（`job.py:191`）。
- 一個渠道還沒有任何分區 `passed` 時，只要有一個分區是 `failed`／`awaiting_pm`／`running`，這個渠道其他分區都不會被領（`job.py:195-198`）。
- 「首月」＝這個渠道第一個通過的分區。領取是由新到舊（`job.py:199`），所以通常是**最新**的月份。
- 燃盡圖與依它算的告警（`BackfillEtaBeyondWindow` 等）每小時才重算一次（`recon.py:227-241、276-307`），修完是否生效以狀態紀錄為準。

## 2. 重跑單一分區

**先決定要不要 `--force`：**

| 狀況 | 怎麼跑 |
|---|---|
| ES 暫時故障中斷，程式與原文都沒改 | 不加 `--force`，從 `checkpoint_line` 接著跑 |
| 閘門沒過、修了程式或字典 | 先重建映像，再加 `--force` |
| 原文有改（本機檔案，或 BigQuery 表補了資料） | 加 `--force` |

修的是程式時先重建映像：qseg 的原始碼是建進映像的，沒有掛載（`seg/Dockerfile:14`），不重建跑的還是舊程式。

```bash
./run.sh image
```

映像會帶 git commit 編號（有未提交的修改加 -dirty），寫進每篇的 `seg_build_v2`；之後查得出哪些文章是哪一份程式斷的。

重跑前先把舊的 `gates` 存下來：重跑會整個覆寫 `gates`（`job.py:308-309`），之前的豁免說明與 PM 簽核人都會消失。

```bash
curl -s "http://localhost:9200/backfill_ledger/_doc/ptt%2F2026-10" | python3 -c 'import json,sys; print(json.load(sys.stdin)["_source"]["gates"])'
```

接著跑：

```bash
V=argus-q6-2026.10.30.3; docker compose --profile job run --rm -T --no-deps -e DICT_VERSION=$V seg-job job --partition ptt/2026-10
```

```bash
V=argus-q6-2026.10.30.3; docker compose --profile job run --rm -T --no-deps -e DICT_VERSION=$V seg-job job --partition ptt/2026-10 --force
```

- **閘門沒過的分區一定要加 `--force`**：不加時從 `checkpoint_line` 接著跑，G2 的「必不可拆詞／網址殘片」只算這次處理到的行（`job.py:246`）。分區已經跑到尾端時一行都不會重算，這兩項變成 0，G2 會「通過」但沒有意義。
- 加 `--force`：checkpoint 與計數歸零、從第一行重寫，同版本允許覆寫但版本不倒退；`RAW_SOURCE=bigquery` 另外把 `started_at` 改成現在，`file` 不動 `started_at`（`job.py:102-121、228`、`es.py:7、23`）。
- **分區跑到一半不要換 `RAW_SOURCE`**：`checkpoint_line` 在 `file` 是「去重後第幾行」，在 `bigquery` 是「依 `post_id` 排序第幾篇」，兩者對不上。換了來源一定要 `--force` 從頭跑，否則會漏寫或重寫一段（`job.py:114-115`）。
- `partition_start` 日誌的 `raw_null_ids`＝原文表裡 `post_id` 是空的列數。這些列不讀、不算進 `raw_count`（G1 不會因此失敗），只算進 `raw_lines`；大於 0 要請資料團隊查（`job.py:242`）。
- `RAW_SOURCE=bigquery` 時，讀的是 `started_at` 那個時間點的表（BigQuery 時光回溯），所以不加 `--force` 接著跑，看不到之後才進表的文章；加了 `--force` 才改讀現在的表。`started_at` 超過 7 天（`BQ_TIME_TRAVEL_HOURS`，提前 6 小時）就讀不到舊時間點，Job 會自己從頭重跑，日誌有 `raw_snapshot_expired`（`job.py:122-131`）；已寫過的文章會變 noop，不會重複。`--force` 只有搭配 `--partition` 才生效（`job.py:403`）。
- **`DICT_VERSION` 要等於該列的 `dict_version`。** 不一致時 ES 不會寫入，但帳本會被改：`attempts` 先加 1（`job.py:202`），再把該列改成 `pending`（不管原本是 `passed`、`failed` 還是 `awaiting_pm`），然後以代碼 2 結束（`job.py:222-226`）。`failed` 被改成 `pending` 之後會被自動領走。
- **結束代碼**：分區跑完不論 `passed` 或 `failed` 都回 0（`__main__.py:12-14`），所以 0 不代表成功，要看狀態紀錄。代碼 2＝字典版本不一致。代碼 1＝程式例外（原文檔不存在、BigQuery 查詢失敗或沒權限、ES 整批 HTTP 錯誤，`job.py:229`、`rawsrc.py:39、95`、`es.py:42、76`；原文表的 `published_at` 被建成 TIMESTAMP 也算這種，`rawsrc.py:83-85`）。`RAW_SOURCE`／`RAW_BQ_TABLE` 設錯則在領分區之前就結束，不會動到帳本（`rawsrc.py:113-118`），這時分區**停在 `running`**，不是 `failed`，先看日誌。
- 分區編號打錯（狀態紀錄沒有這一列）不會報錯，日誌只有 `job_done partitions=0`。
- **`--partition` 不檢查狀態**（`job.py:188-189`）：
  - 不要對正在 `running` 且心跳正常的分區下，會兩個 task 同時寫。
  - 對 `passed` 的分區下，若它是這個渠道唯一通過的分區，重跑後會變回 `awaiting_pm`，整個渠道暫停領取，要 PM 重新簽核（`job.py:234、306`）。沒必要不要重跑 `passed` 分區。
- 每跑一次 `attempts` 加 1，沒有任何指令會歸零；手動 `--partition` 不受 3 次上限限制（上限只在自動領取，`job.py:193`）。

這個指令形式是從 `run.sh:56` 加上 `job.py` 的 `--partition`／`--force` 推出來的，`run.sh` 裡沒有現成的單一分區指令。

## 3. PM 簽核（`awaiting_pm` → `passed`）

PM 頁該列填備註後簽核，或：

```bash
docker compose --profile job run --rm -T --no-deps seg-job approve ptt/2026-10 --by 王小明 --note "A/B 抽查通過"
```

- `--by` 必填。該列不在 `awaiting_pm` 時指令回 1、API 回 409，不會改任何東西。
- 從 PM 頁操作會寫進事件流（`api/app.py:699、706`）；命令列只寫在容器日誌（`pm_approved`，`job.py:381`），事件流看不到。
- 簽核只改狀態紀錄（`job.py:379`），原本的 task 多半已經結束，簽完到第 6 節重開 task。

## 4. 豁免閘門（`failed` → 放行）

只在人逐篇看過、確定是誤判時用（程式不會檢查你有沒有看過）。只能豁免 G1／G2／G3，一次一個，一定要寫說明：

```bash
docker compose --profile job run --rm -T --no-deps seg-job approve ptt/2026-09 --by 王小明 --waive G2 --note "看過 3 篇，https 是原文內容不是網址殘片"
```

或 PM 頁該列填說明後按該閘門的豁免鈕。結果（`job.py:341-356`，API 用同一段 SQL）：

- 還有別的閘門沒過又沒豁免 → 仍是 `failed`，逐一豁免。
- 首月（G4 沒記成已過，或這個渠道還沒有分區 `passed`）→ 變 `awaiting_pm`，還要走第 3 節。
- 其餘 → `passed`。

**下面兩種 `failed` 一律不要豁免，走第 2 節重跑**：資料沒寫完，豁免加簽核會把沒補完的分區記成通過，而且 CLI 與 API 都不會擋（`job.py:345-356`、`api/app.py:677-697`）。

- `checkpoint_line` ＜ `raw_count`（寫入中斷）。不論 `gates` 有沒有內容都一樣：寫入出錯不會清掉舊 `gates`，PM 頁可能照樣出現豁免鈕。
- `gates` 只有 `attempts_exhausted`（被領滿 3 次）。

## 5. 暫停與限速

```bash
curl -s -X POST http://127.0.0.1:8080/api/ops/control -H "Content-Type: application/json" -d '{"by":"王小明","paused":true,"reason":"查詢變慢，先停"}'
```

```bash
curl -s -X POST http://127.0.0.1:8080/api/ops/control -H "Content-Type: application/json" -d '{"by":"王小明","paused":false,"max_docs_per_sec":1500}'
```

- **只想暫停時用第一條 curl，不要按 PM 頁的「套用」**：「套用」會連同限速欄一起送出（`web/app.js:359`），而限速欄顯示的是目前值（`web/app.js:266`）；API 會把 G3 回升上限一起設成這個數（`api/app.py:802-803`）。G3 自動降速後按「套用」，回升上限就被永久壓在降過的值。
- 暫停只在每批（500 篇）開始前生效：正在跑的那一批會先做完，暫停中的 task 每 2 秒再看一次，不會退出（`job.py:208-217、251`）。暫停期間 task 仍可能領到新分區、卡在等待，這些分區也算在分配限速的分母裡。要完全停下來，才 `docker stop job-0 job-1 job-2`；被停的分區留在 `running`，心跳過 2 分鐘後重開的 task 會從 `checkpoint_line` 接續。
- `max_docs_per_sec` 只收 50–50000，同時成為 G3 自動回升的上限（`api/app.py:798-803`）。這是全域額度，所有正在跑的分區平分（`job.py:208-216`）。
- **G3 自動降速**：只在探針 p95 超過 G3 上限時觸發。每個分區跑完量最多 3 次，最多降 2 次，每次降成 `max(250, min(目前值, 3000) ÷ 2)`，所以目前值超過 3000 時第一次就降到 1500；降完等 15 秒再量（`job.py:72-87`）。G3 因寫入被拒（`write_rejected` 不是 0）而沒過時不會自動降速，要人看（`job.py:167`）。

## 6. 處理完之後：重開 task

簽核或豁免只改狀態紀錄；原本的 task 領不到分區就結束了（`job.py:399-402`），要再開。

先確認上一輪的容器狀態（`run.sh:64-67` 沒帶 `--rm`，容器會留著）：

```bash
docker ps -a --filter "name=^job-" --format "{{.Names}} {{.Status}}"
```

- 有 `Up` 的 → task 還在跑，不要重開。
- 全部 `Exited` → 先移除再開：

```bash
docker rm job-0 job-1 job-2
```

```bash
./run.sh backfill-tasks
```

- 重開前先確認 `pending` 列的 `dict_version` 都等於 `V`（`curl -s "http://127.0.0.1:8080/api/ops/ledger?status=pending"`）。`backfill-tasks` 用 `V` 預設 `.3`（`run.sh:65`），但列是用 `.2` 建的（`setup.py:66`），沒跑過 `./run.sh backfill` 的話會對不上：task 把列改回 `pending` 後退出，而且每次都多算一次 `attempts`。
- **不要拿 `./run.sh backfill` 來重跑**，加 `AUTO_APPROVE=0` 也一樣不行。它會把所有 `pending`／`failed` 列的字典版本改成 `$V`、覆寫探針基準、再跑一次 job（`run.sh:52-56`）；`AUTO_APPROVE` 預設 1，還會以「PM」名義自動簽核「ptt/2026-10」（`run.sh:58-59`）。只有首輪回補才用它，並設 `AUTO_APPROVE=0`。
- `AUTO_APPROVE=1` 只簽「ptt/2026-10」。原文湖若還有其他渠道的檔，各渠道的首月會停在 `awaiting_pm`，要到 PM 頁逐一簽核。

## 7. 串流漏斷詞（不是分區回補）

告警 `SegUnsegmentedStale` 響＝某一版「抓到超過 15 分鐘還沒有斷詞」的文章數連續 30 分鐘大於 0（`monitoring/rules/completeness.yml:19-20`，15 分鐘的定義在 `seg/qseg/coverage.py:25`）。補法是 `qseg fill`，要在容器裡跑，不能在主機上跑（ES、PG、字典服務的位址只在 compose 網路內解得開，`config.py:3-11`）。

先查告警那一版的串流服務實際載入的字典版本：

```bash
docker compose exec -T pg psql -U argus -d argus -c "SELECT service, pipeline, dict_version FROM service_state ORDER BY service"
```

再用同一個映像跑一次性容器補（`P`＝告警標的那一版，`D`＝上面查到的該版 `dict_version`）：

```bash
P=v2; D=<查到的版本>; docker compose --profile job run --rm -T --no-deps -e PIPELINE=$P -e DICT_VERSION=$D seg-job fill --channel ptt --max-docs 1000
```

- **`DICT_VERSION` 一定要帶。** 不帶時是 `current`（`config.py:6`），也就是 v2 的字典：拿去補 v0 會失敗或失真，補 v1 會把 v2 的詞寫進 v1 欄位、污染 A/B 比對。v0 只能用 legacy 版（`argus-q6-legacy-2019.12.31.1`），v1 固定 `argus-q6-2026.10.30.1`（`docker-compose.yml:79、85`）。v2 的實際版本取決於最後跑了 `run.sh` 的哪一步，以查詢結果為準。
- 映像的 ENTRYPOINT 已經是 `python -m qseg`（`seg/Dockerfile:19`），所以只接子命令 `fill`。不帶 `--channel`、`--max-docs` 就補全部。
- 它直接讀 ES 裡的原文重算，候選是「這一版還沒有斷詞欄位、未刪除、抓到超過 15 分鐘」的文章，所以中斷後再跑一次會接著補（`seg/qseg/fill.py`）。
- 和回補共用 `backfill_control` 的限速，每次分到全域上限 ÷（正在跑的分區數＋1）。PG 連不上時只印 `fill_pg_unavailable` 警告，然後**不限速**照跑；`--no-limit` 也會關掉限速（`fill.py:35-39、89`、`rerun.py:23-33`）。

歷史爬蟲是另一支工具，和狀態紀錄無關，見 `q6/code/history-crawl/README.md`。

## 8. 正式環境還缺什麼

要在 GCP 上做同一件事，下面每一項都還沒有：

1. `terraform apply` 沒做過；也沒有把 `q6/stack/seg/Dockerfile` 建置並推到 Artifact Registry 的流程。
2. Cloud Run Job 沒有設 `DICT_VERSION`、`DICT_BASE_URL`、`PG_DSN`、`ES_URL` 等環境變數（只有原文來源那三個，見第 5 項），也沒有掛 ES 金鑰。回補的服務帳號沒有讀字典 bucket 的權限（只有斷詞服務帳號有，`shared/infra/gcp/q6.tf:138-142`）。
3. Elasticsearch VM 不在 Terraform 裡（`shared/infra/gcp/README.md:238`），`ES_URL` 沒有目標。
4. 帳本（批次清單）要先建：`gcloud run jobs execute argus-backfill --region=<region> --tasks=1 --args=job,--plan`。`--plan` 從 BigQuery 原文表撈出每個「渠道 × 月」，各寫一列 `pending`，`dict_version` 用這次載到的字典（`job.py` 的 `plan()`、`rawsrc.py` 的 `list_partitions()`）；已在帳本的列不動，可以重跑。建完再用 `--tasks=N` 跑 `job` 開始回補。這兩個 `gcloud` 指令沒在 GCP 上跑過；單一分區用 `--args=job,--partition,<渠道>/<月>`，也沒試過。
5. 讀 BigQuery 原文的程式已經有（`seg/qseg/rawsrc.py`，`RAW_SOURCE=bigquery`），Terraform 設了 `q6_raw_bq_table` 就會給回補服務帳號那張表的唯讀與本專案的 `bigquery.jobUser`、並設好 Job 的三個變數（`shared/infra/gcp/q6.tf:200-220`、`run.tf:145-155`）。但**正式的原文表還不存在**：欄位、`month` 的算法、`CLUSTER BY` 都只是 `rawsrc.py` 開頭寫的假設，要資料團隊照這個建表。2026-11-14 照這個假設在本專案建了實測表 `q6_raw.posts`（asia-east1，`CLUSTER BY channel, month`），灌進本機 `runtime/raw` 的 659,751 篇，從 Windows 用擁有者帳號的 ADC 連BigQuery 跑：`list_partitions()` 回 177 個「渠道 × 月」，跟本機檔案一模一樣，順序是月份新到舊、同月依渠道（約 10 秒）；`open_partition()` 抽兩個分區（facebook 2026-09 有 1,840 篇、youtube 2025-02 有 43 篇），`raw_count` 和逐篇讀出的篇數都跟本機去重後一致；`plan()` 用這份清單產生 177 列 `pending`。BigQuery 主控台跑同一句切批查詢的畫面：`results/bigquery-partitions-2026-11-14.jpg`。同日再補三項實測：① PostgreSQL（本機 pg 另開資料庫 `q6bq`）：`job --plan` 寫入 177 列 `pending`。② 整個分區從 BigQuery 讀、斷詞、寫 ES（另開索引 `posts_bq`，不碰正式 `posts`）：facebook 2026-09 1,840 篇、約 180 篇/秒，G1（1,840 = 1,840）、G2、G3 過，首月停在 `awaiting_pm`，PM 簽核後 `passed`；ptt 2026-09 32,652 篇、約 313 篇/秒，G1（32,652 = 32,652）到 G3 過，停在 `awaiting_pm`；facebook 2026-08 1,923 篇 G1 過、G2 不過（`url_fragment` 2/1,923 = 0.104%，門檻 0.1%，兩篇的 `https` 漏成詞），閘門照設計擋下，留 `failed` 給人看。③ Cloud Run Job 用 `argus-backfill` 服務帳號（只有那張表的 `dataViewer` 和本專案的 `jobUser`）跑同一個映像：`list_partitions()` 177 個、facebook 2026-09 `raw_count` 1,840、逐篇讀出 1,840，exit 0。雲端還沒跑完整的 `job`：Terraform 沒 apply，沒有 Cloud SQL 和 ES，Cloud Run 連不到本機的帳本和 ES。假物件測試在 `q6/code/tests/test_p0_raw_source.py`。對帳（`recon.py`）、`seed`、`impact` 還是讀本機 `RAW_ROOT`，沒有改。
6. Cloud SQL 上的表（`backfill_control`、`backfill_batches`）不是 Terraform 建的；帳本在 ES 索引 `backfill_ledger`，由 ES 端點與權限管。`PG_DSN` 預設是本機帳密與 `argus` 庫（`config.py:11`），正式環境要改成私有 IP、IAM 使用者（回補服務帳號）、TLS（`sql.tf:21-24、36-40、58-61`）。
7. task 數預設 1（`variables.tf:122-132`），本機是 3 個；Job timeout 3600 秒、重試 2 次（`run.tf:132-133`），超過一小時的分區會被砍，每次被重領都多耗一次 `attempts`。
8. PM 頁與 `/api/ops/*` 在正式環境的網址與存取方式沒有定義；簽核、豁免、限速的 API 只有一個自由填寫的 `by`，沒有身分驗證。

## 9. 查證範圍

- 2026-11-11 對照 `seg/qseg`、`api/app.py`、`web/app.js`、`run.sh`、`docker-compose.yml`、`shared/infra/gcp` 逐條查過；行號以當天為準。
- **沒有實際執行過**：第 2 節的 `--partition`／`--force` 與 `build seg-job`、第 5 節的 curl 與 `docker stop`、第 6 節的 `docker ps`／`docker rm`、第 7 節在容器裡跑 `fill`。第一次用之前先在本機整套系統試一次。
- PM 頁按鈕的確切文字沒有逐一核對，以畫面為準。
- 程式面已知的坑（runbook 只能靠人避開，修程式另議）：寫入出錯不清舊 `gates`、豁免不擋未寫完的分區、心跳在讀完原文後才開始、PM 頁「套用」會一起改限速上限。
