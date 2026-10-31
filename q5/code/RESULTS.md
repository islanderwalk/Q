# Q5 回補原型與工時重算：驗證結果

驗證日期 2026-10-30，Linux 雲端環境。本檔涵蓋 `q5/code/backfill/` 與 `q5/code/schedule/`，原本放在 `q5/code/backfill/RESULTS.md`，依 repo 版面移到 `q5/code/RESULTS.md`，log 移到 `q5/code/logs/`。

版本名稱：**原型**＝`fc0b075^`（python.md 在 Windows 驗證的那一版），**上一輪**＝`fc0b075`，**本輪**＝目前工作樹。舊版程式由 `demo_cli.sh` 用 `git show` 取到 scratch 執行，不動工作樹。

## 結論

- **測試**：`q5/code/backfill/tests` 34 項、`q5/code/schedule/tests` 5 項，共 39 passed，退出碼 0。
- **欄位改名會停下來了**：把 2026-05-11 一週的計數欄位改成別名表沒有的名稱（652 筆抓取），上一輪退出碼 0、`gate_failures` 是空的，117 篇默默沒有寫入（未改名時是 21 篇）。本輪退出碼 3，指出 `threads/2026-05-11` 三個欄位 652/652 都不知道，不產生 bulk 檔；`quality.json` 的分區 key 清單直接看得到新名稱 `text_post_app_info.reposts` 等。
- **較舊的值不會再蓋掉較新的值**：線上 T2 已有轉發數 20，回補輸入只有 T1（三欄）與 T3（缺轉發 key）。上一輪整篇只有一個時間戳，T3 讓 T1 的舊值 10 蓋掉了 20；本輪每欄各有時間戳 `counts_at.<欄位>`，轉發數保留 20，另兩欄寫入 T3 的值。前提是爬蟲寫入也帶同一個時間戳，已寫進 b-c-d.md (b) 第 1、2 項與風險表。
- **IG 兩個 key 值不同時不寫入**：`play_count` 與 `video_view_count` 同時出現且值不同，當成不知道並記 `alias_conflict`，不再默默寫入 `play_count`。
- **端到端結果不變**：2,000 篇、3,521 筆抓取的合成資料，寫入 1,979 篇、略過 21 篇；重跑兩次 7 個輸出檔 sha256 相同；與獨立算出的正確答案 0 筆不符。送出端 5 個情境（本機假 ES）中，有比對內容的 S1、S3、S5 都是 0 筆不符（S2 沒送請求、S4 是中斷），包括 20 篇「爬蟲只寫過轉發數」的文件。
- **painless 腳本在真的 Elasticsearch 上跑過**：本機 Docker 的官方映像 8.19.23 與 9.5.5（單節點），`demo_real_es.sh` 的 21 項檢查兩個版本都通過。舊 mapping（`dynamic: strict`）時回補被擋（400 `strict_dynamic_mapping_exception`）；用 update mapping API 加新欄位（不 reindex）後首次送出 ok 1,940、noop 20、not_in_index 19，`_mget` 讀回的計數值與正確答案 0 筆不符，每欄的 `counts_at` 與 bulk 檔送出的時間戳（爬蟲較新的欄位則是爬蟲的時間戳）0 筆不符；整批重送 1,960 筆全部 noop。時間戳寫成 ISO 字串時，寫入就被 `epoch_millis` mapping 擋下（400）；寫成數字字串時寫得進去，但腳本丟錯（400 `illegal_argument_exception` ← `script_exception`），退出碼 4，文件沒有被改動。IG 寫入 193 篇，0 筆不符。HTTP 429、500 與單筆 429 仍只在假 ES 上模擬。
- **交卷用的 600 字答案**：`q5/answer/answer_600.txt`，Python `len()` 為 599 字元（含換行），去掉空白 530 字元。
- **工時不變**：關鍵路徑 46 小時，9 工作天，週一開工約 11 個日曆天。
- **示範可以從 repo 重跑**：`demo_cli.sh` 與 `demo_checks.py` 進 repo，`logs/demo_cli.log` 是它的完整輸出。

## 環境

| 項目 | 版本 |
|---|---|
| OS | Ubuntu 24.04.5 LTS，kernel 6.18.44 x86_64 |
| Python | 3.13.17（`<tmp>/venv`） |
| pytest | 9.2.0 |
| requests | 2.34.2（`--send` 與 `demo_real_es.py` 連 ES 時用到） |
| Docker | 29.9.2（`demo_real_es.sh` 起 ES 用） |
| Elasticsearch | 官方映像 `mirror.gcr.io/library/elasticsearch`：8.19.23（`sha256:ef5cafd961d7e273f13fed8def82e63c5ae0491106b5eecb5a69aa12d64f089b`，Lucene 9.12.2）、9.5.5（`sha256:52ffd9f66c98b7826a25da2ef7cac6186ecec158d8fb371bff325ad00b6eca93`，Lucene 10.5.1）；單節點、只綁 127.0.0.1 |
| bash／git／GNU coreutils | 5.2.21／2.43.0／9.4（`demo_cli.sh` 用到 `git show`、`sha256sum`、`stat`、`cut`） |
| beautifulsoup4／lxml、curl | 4.15.0／6.1.3、8.5.0（上一輪下載與抽取 ES 文件用，本輪沒有重新下載） |

所有 Python 指令都設 `PYTHONDONTWRITEBYTECODE=1`，pytest 加 `-p no:cacheprovider`；repo 內沒有 `__pycache__`、`.pytest_cache`。

## 指令與退出碼

#1 在 repo 根目錄執行：`PY=<tmp>/venv/bin/python bash q5/code/backfill/demo_cli.sh <tmp>/work/q5/fix/cli`。#2–#21 是這次執行裡的步驟（字母對應 log 的段落），`$R` = `<tmp>/work/q5/fix/cli/run`。#18–#21 的退出碼是 `demo_checks.py` 本身的，結果欄才是被測版本的行為。

| # | 指令 | 退出碼 | 結果 |
|---|---|---|---|
| 1 | `demo_cli.sh`（整支） | 0 | 輸出逐字存成 `logs/demo_cli.log` |
| 2 | A `make_sample.py --out $R/sample --posts 2000 --reels 200 --seed 5` | 0 | Threads 3,521 筆、IG 200 筆；`expected.json` sha256 前 16 碼 `2accd204911ea6d0`，與上一輪相同 |
| 3 | B 原型 `backfill.py --src $R/sample/src --out … --index threads-posts-v2 --batch-docs 1` | 0 | posts=745。原型寫明的輸入是 `src/**/*.json`，兩個 `.jsonl` 分區不在它的輸入範圍 |
| 4 | C 本輪，同一指令 | 0 | posts=2,000，寫入 1,979，略過 21，1,979 個 bulk 檔 |
| 5 | D 本輪預設批次跑兩次（`run1`、`run2`） | 0、0 | 7 個輸出檔 sha256 相同、stdout 相同；三個輸出目錄的 1,979 個 doc id 雜湊都是 `4f2316c1f30ae538` |
| 6 | E 覆寫 `run1` 再跑一次 | 0 | 7 個檔 sha256 不變 |
| 7 | F `demo_checks.py compare $R/run1 $R/sample/expected.json` | 0 | 1,979／1,979 一致，0 不符 |
| 8 | G 截斷 `threads/2026-05-04/batch_0001.json`：原型 | 1 | `JSONDecodeError` traceback |
| 9 | G 同上：本輪 | 3 | `bad_files 1 > 0`，只留 quality.json、rejects.jsonl |
| 10 | G 同上加 `--max-bad-files 1` | 0 | 明確放行後跑完，posts=1,968 |
| 11 | H `2026-05-18` 分區每 10 行截斷 1 行（101 行）：本輪 | 3 | `reject_rate 103/3523=0.0292 > 0.01` |
| 12 | H 同上：原型 | 0 | 沒有發現，因為 `.jsonl` 不在原型的輸入範圍 |
| 13 | I `backfill.py --src $R/nope --out $R/x` | 2 | `--src 不是目錄` |
| 14 | J `--src $R/sample/src/instagram --platform instagram` | 0 | 200 支，寫入 195（18 支來自假設的舊名），5 支兩個 key 都沒有；對正確答案 0 不符 |
| 15 | K `2026-05-11` 改名（1,862 個 key）：上一輪 | 0 | `gate_failures []`，寫入 1,883、117 篇沒有寫入 |
| 16 | K 同上：本輪 | 3 | 3 條 `unknown_rate threads/2026-05-11 <欄位> 652/652=1.0000 > 0.2`，不產生 bulk 檔 |
| 17 | K 同上加 `--max-unknown-rate 1` | 0 | 明確放行：寫入 1,883、略過 117 |
| 18 | L `demo_checks.py item429 --code <原型>`（單筆 429） | 0 | 原型：`{'failed': 1, 'files_sent': 1, 'ok': 1}`，被拒那筆沒有重送、沒有寫入 |
| 19 | L 同上，`--code q5/code/backfill` | 0 | 本輪：`retried_items` 1、ok 2，被拒那筆重送後寫入 |
| 20 | M `demo_checks.py guard-mixed --code <上一輪>` | 0 | 上一輪：ok 1，線上 repost 從 20 變成 10，`counts_crawled_at` 變成 T3 |
| 21 | M 同上，`--code q5/code/backfill` | 0 | 本輪：ok 1，repost 維持 20、`counts_at.repost_count` 維持 T2；quote 3、reshare 3 以 T3 寫入 |
| 22 | `python demo_send.py $SEND`（在 `q5/code/backfill/`，本機假 ES，5 個情境） | 0 | 見下方「送出端」，log 在 `logs/demo_send.log` |
| 23 | `python q5/code/schedule/schedule.py --mode contract`、`--mode strict` | 0、0 | 與上一輪的輸出逐字相同 |
| 24 | `pytest -q -p no:cacheprovider q5/code/backfill/tests q5/code/schedule/tests`（repo 根目錄） | 0 | 39 passed |

上一輪另外跑過、本輪沒有重跑的：原型程式＋原型測試 11 passed（退出碼 0，與 python.md 的 Windows 結果相同）；11 頁 elastic.co 文件的下載（全部 HTTP 200，sha256 見下方）。

## pytest 結果

| 指令 | 位置 | 結果 | 退出碼 |
|---|---|---|---|
| `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider q5/code/backfill/tests q5/code/schedule/tests` | repo 根目錄 | 39 passed in 0.27s（`logs/pytest.log`） | 0 |
| `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests` | `q5/code/backfill/` | 34 passed | 0 |
| `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests` | `q5/code/schedule/` | 5 passed | 0 |

測試全部離線（假 ES 是記憶體內物件，不開 port）。本輪新增 8 項：分區 key 清單與不知道比例的關卡、一週改名時命令列退出碼 3、IG 兩個 key 值不同時不寫入、三欄來自不同次抓取時逐欄保護（T1／T2／T3）、較舊一週後送時只補還缺的欄位、線上時間戳是字串時回 400 並記為失敗、`demo_checks.py` 的兩個工具函式。`demo_send.py` 的測試改寫成三種線上文件。

## 示範資料與端到端結果

`make_sample.py` 產生合成資料（固定種子）。規則取自實抓證據；舊欄位名是故意混進去測別名表的假設。每個原始檔所在的目錄是一個分區。

| 分區 | 欄位名 | 大小 |
|---|---|---|
| `threads/2026-05-04/batch_0001.json` | 假設的舊名 `textPostAppInfo`＋`repostCount` 等 | 84,845 B |
| `threads/2026-05-11/batch_0001.json` | 假設的舊名 `repost_cnt` 等 | 171,725 B |
| `threads/2026-05-18/part-0000.jsonl` | 實抓看到的現行名稱 | 270,819 B |
| `threads/2026-05-25/part-0000.jsonl` | 現行名稱，另有 1 行壞 JSON、1 筆 ISO 字串 `crawled_at` | 415,078 B |
| `instagram/2026-05-04/reels.jsonl` | `play_count`，約 10% 用假設的舊名 `video_view_count` | 34,212 B |

Threads 執行結果（`run1/quality.json` 節錄）：

| 項目 | 數字 |
|---|---|
| 讀到的抓取／貼文 | 3,521 筆／2,000 篇（重複 1,521 筆，其中同一次抓取重複落地 46 筆） |
| 壞行／丟棄 | 1／1（`bad_json_line`、`bad_crawled_at`），比例 0.06%，通過 1% 關卡 |
| 用到假設的舊名 | `repost_cnt` 633 次、`repostCount` 316 次、`textPostAppInfo` 316 次 |
| 缺 key／整個容器不見／畫面字串 | 159／78／18 筆抓取 |
| 改用較舊一次抓取的值 | repost 28、quote 23、reshare 58 篇 |
| 最終 reshare_count 為 null（觀測值，不是 0） | 366 篇 |
| 最終仍不知道 | repost 26、quote 21、reshare 72 篇；三個都不知道 21 篇（不寫入） |
| bulk 檔 | 4 個（500／500／500／479 篇），共 1,884,002 B，每篇約 952 B（其中腳本原文 606 字元），每檔約 0.46–0.48 MB |

每個分區「不知道」的比例（以抓取計，給分區關卡用；門檻預設 20%）：

| 分區 | 抓取筆數 | repost | quote | reshare |
|---|---|---|---|---|
| `threads/2026-05-04` | 321 | 1.87% | 1.56% | 8.10% |
| `threads/2026-05-11` | 652 | 3.22% | 2.91% | 8.59% |
| `threads/2026-05-18` | 1,005 | 2.49% | 1.79% | 5.57% |
| `threads/2026-05-25` | 1,543 | 2.85% | 2.33% | 6.42% |
| `threads/2026-05-11` 改名後（K） | 652 | 100% | 100% | 100% |

門檻怎麼訂的：合成資料每筆抓取有 4% 缺 reshare key、2% 缺整個容器，所以 reshare 在大分區約 6%–9%；測試用的 300 篇樣本（種子 7）有一個分區只有 36 筆，reshare 6/36＝16.7%。改名的分區是 100%，但攤到全體只有 652/3,521＝18.5%，所以只看全體比例、門檻 20% 會擋不下來，必須逐分區看。20% 是讓合成資料的雜訊能通過的值；實抓證據顯示沒有分享數時 `reshare_count` 是 null 而不是缺 key，真實資料的缺 key 比例應該低很多，盤點後要依真實資料調低。小分區一兩筆缺漏就會超過門檻，確認後用 `--max-unknown-rate` 放行。

IG 執行結果：200 支，195 支寫入 `video_view_count`，其中 18 支來自假設的舊名，5 支兩個 key 都沒有（不寫入）。合成資料沒有兩個 key 同時出現的情況，值衝突只由單元測試涵蓋。

## 送出端（本機假 ES）

`demo_send.py` 在 127.0.0.1 起一個 HTTP 假 ES（`tests/fake_es.py`），`--rate-per-sec 4`。線上 index 預先放 1,980 篇：20 篇刻意不在 ES；20 篇爬蟲已寫過三個計數、時間都比回補新；20 篇爬蟲只寫過轉發數（比回補新），用來看逐欄保護；其餘有文件但沒有計數。

| 情境 | 退出碼 | send 統計 | ES 與正確答案比對 |
|---|---|---|---|
| S1 首次送出（第 1 個請求 HTTP 429，8 筆單筆 429） | 0 | 請求 9 次、ok 1,940、noop 20、not_in_index 19、重送 508 筆 | 0 不符 |
| S2 同目錄重跑 | 0 | 4 檔都由 `sent.json` 跳過，0 個請求 | — |
| S3 新目錄整批重送 | 0 | 4 個請求、noop 1,960、not_in_index 19（4 個請求花 1.34 秒，每秒 4 個的限速生效） | 0 不符 |
| S4 第 3 個請求回 HTTP 500 | 4 | 3 檔送完、1 檔未完成，帳本記 bulk_00001、00002、00004 | — |
| S5 修好後重跑 | 0 | 只送 bulk_00003（1 個請求，ok 490） | 0 不符 |

noop 20 是「三欄線上都比較新」的那 20 篇。「只寫過轉發數」的 20 篇算在 ok 裡：另兩欄寫入，轉發數維持爬蟲的 999，比對時逐欄檢查。not_in_index 19 是 20 篇不在 ES 的貼文扣掉 1 篇三個計數都不知道、本來就沒產生動作的。

## 送出端（真的 Elasticsearch，本機 Docker）

假 ES 只用 Python 模擬腳本邏輯，所以另外用官方映像起單節點 ES，讓 `GUARD_SCRIPT` 真的執行。在 repo 根目錄執行：

```bash
PY=<tmp>/venv/bin/python bash q5/code/backfill/demo_real_es.sh <tmp>/work/q5es/sh8 mirror.gcr.io/library/elasticsearch:8.19.23 19200 > q5/code/logs/demo_real_es_8.19.23.log 2>&1   # exit 0
PY=<tmp>/venv/bin/python bash q5/code/backfill/demo_real_es.sh <tmp>/work/q5es/sh9 mirror.gcr.io/library/elasticsearch:9.5.5 19201 > q5/code/logs/demo_real_es_9.5.5.log 2>&1     # exit 0
```

`demo_real_es.sh` 起容器（關掉安全性與 ML、只綁 127.0.0.1）、用同一個種子產生 2,000 篇樣本、跑 `demo_real_es.py`，最後刪掉容器。線上文件的三種情況和假 ES 相同（`demo_send.py` 的 `seed()`）。R1、R3 後用 `_mget` 讀回全部 Threads 文件：計數值和 `expected.json` 比對；`expected.json` 沒有時間戳，每欄的 `counts_at` 和 bulk 檔的 `params.at`（爬蟲較新的欄位則是爬蟲寫的時間）比對。R4 只讀回被改壞的那篇；R5 比對 IG 的值，`counts_at` 只看有沒有。兩個版本的結果除了耗時以外完全相同：

| 情境 | 退出碼 | 結果（8.19.23 與 9.5.5 相同） |
|---|---|---|
| R0 舊 mapping：`dynamic: strict`，只有 `post_id`，放入 1,980 篇 | — | 送 1 筆回補：400 `strict_dynamic_mapping_exception`，文件沒變。update mapping API 加 `text_post_app_info.*`（long）與 `counts_at.*`（`date`、`epoch_millis`）：200，不需要 reindex。再寫入爬蟲較新的計數（三欄 20 篇、只有轉發數 20 篇） |
| R1 首次送出 | 0 | 4 個請求：ok 1,940、noop 20、not_in_index 19（`send_errors.jsonl` 的 19 筆都是 404 `document_missing_exception`）；0 筆不符 |
| R2 同目錄重跑 | 0 | 4 檔都由 `sent.json` 跳過，0 個請求 |
| R3 新目錄整批重送 | 0 | noop 1,960、ok 0；0 筆不符 |
| R4 線上時間戳型別錯誤（S5T000003） | 4 | 寫 ISO 字串 `2026-05-01T00:00:00Z`：400 `document_parsing_exception`，寫入時就擋下。寫數字字串 `"1900000000000"`：200，讀回的 `_source` 是字串。整批重送：failed 1、noop 1,959；`send_errors.jsonl` 有 19 筆 404（不在 ES 的貼文），其餘只有這篇（400 `illegal_argument_exception`，原因是 `script_exception: counts_at.repost_count is not epoch_millis`），文件沒變 |
| R5 IG：`ig-posts` 放 198 篇（2 篇不在 ES） | 0 | ok 193、not_in_index 2；0 筆不符，`play_count` 與 `video_view_count` 都沒有的 5 篇沒有寫入 |

這一段補上假 ES 證明不了的部分：腳本裡的 `instanceof`、`throw`、for 迴圈、Map 存取與 `ctx.op = 'noop'` 在兩個版本都能執行；mapping 的 `strict` 與 `epoch_millis` 確實會擋；文件不存在時 ES 回單筆 404 `document_missing_exception`，不會建立殘缺文件。沒有涵蓋的部分：HTTP 429／500 與單筆 429（真的 ES 不好故意觸發，仍只有假 ES 的 S1、S4）、連線逾時、多節點與副本、正式 index 的實際 mapping 與 ES 版本、負載下的吞吐。

雲端容器的磁碟回報為 252 GB 用了 96%（實際可寫的是每個 session 的額度），ES 依磁碟水位拒絕配置 shard，第一次建 index 後一直是 red、bulk 逾時；所以 `demo_real_es.sh` 起容器時設 `cluster.routing.allocation.disk.threshold_enabled=false`。這是本機示範的設定，正式環境不要關。

## 工時重算（`q5/code/schedule/schedule.py`）

本輪沒有改工時程式，重跑的輸出與上一輪逐字相同。

| 項目 | 原本 b-c-d.md | 重算 |
|---|---|---|
| DE／BE／FE 合計 | 42／24／10 小時（BE、FE 未核對） | 42／24／10 小時，驗收另計 4 小時（全員） |
| 關鍵路徑 | mapping、爬蟲、回補、整合、驗收，「42 小時」 | 同一條路徑，加總是 46 小時（42＋驗收 4） |
| 工作天 | 42 ÷ 8 = 5.25，×1.3 ≈ 7，＋1 = 8 | 46 ÷ 8 = 5.75，×1.3 = 7.475 取 8，＋1 = 9 |
| 日曆天 | 約 10 | 週一、週二開工 11；週三到週五開工 13 |
| 甘特圖 | 手畫：DE 第 2 天同時排了 mapping 與爬蟲、BE 第 3 天同時排了 data layer 與 analytics；mapping 6 小時畫成 2 天、前端 10 小時畫成 5 天 | 由程式依依賴與「一人一次一件」排出，每格 2 小時 |
| BE、FE 是否會拖慢 | 「只要 API 契約在第 2 天前定案」就不會 | 契約先行時 BE、FE 浮時 16 小時（契約最晚第 3 天結束）；若 FE 要等 5 做完，1、4、5、7、8、9 也是 46 小時，變成第二條關鍵路徑 |

## 欄位名與證據對照

| 欄位 | 程式採用的來源 key | 證據 | 狀態 |
|---|---|---|---|
| Threads 轉發 | `text_post_app_info.repost_count` | `q5/evidence/a-dataflow/threads-evidence-panel.png` 的 JSON 片段；`q5/evidence/src/threads_verify.js` 讀的 key | 實抓 |
| Threads 引用 | `text_post_app_info.quote_count` | 同上（畫面轉發數＝repost＋quote，18 篇） | 實抓 |
| Threads 分享 | `text_post_app_info.reshare_count` | 同上；`threads-reshare-null-not-zero.png`（沒有數字時是 null） | 實抓 |
| IG 觀看數 | `play_count`，ES 欄位名 `video_view_count` | `q5/evidence/playwright/ig_reel_fields.json` 12 筆、`ig_reels_grid_json_check.png` | 實抓；與題目 `video_view_count` 的定義是否相同未查證，兩個 key 值不同時不寫入 |
| 舊名 | `textPostAppInfo`、`repostCount`、`repost_cnt`、`quoteCount`、`quote_cnt`、`reshareCount`、`reshare_cnt`、IG `video_view_count` | 無 | 假設，要用 GCS 原始檔核對；`quality.json` 的分區 key 清單是盤點的工具 |

`shared/capture/fixtures/threads_graphql_sample.json` 只有 `code`、`username`、`taken_at`、`text`，沒有任何計數欄位，不能拿來核對欄位名（有測試鎖住這點）。`capture_gaps.py` 只記錄「值是數字」的 key，所以 IG 頁面上有沒有值為 null 的 `video_view_count`，這份證據看不出來。

## Elasticsearch 文件查證

全部是 elastic.co 官方文件，上一輪 2026-10-30 下載（每頁存在 `<tmp>/work/q5/dl2/<名稱>/page.html`，不進 repo），本輪沒有重新下載。8.19 指南頁標示「This documentation is no longer updated」，所以 bulk 的大小說法另外對照了現行 API 文件；QSearch 實際用的 ES 版本題目沒有寫。

| 主張（答案或程式依賴的） | 文件原文重點 | 網址 | sha256 |
|---|---|---|---|
| bulk 沒有固定的最佳筆數，單一 HTTP 請求預設上限 100 MB | "There is no "correct" number of actions to perform in a single bulk request" … "limits the maximum size of a HTTP request to 100mb by default" | https://www.elastic.co/guide/en/elasticsearch/reference/8.19/docs-bulk.html | `7276221bb4233b937f1fdb7b59cbd236d7e737484719bb3663bad631dbca627f` |
| 同上（現行版），bulk 的 update 動作可帶 `retry_on_conflict` | 同上兩句；"use `retry_on_conflict` as a field in the action itself" | https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-bulk | `988b31f6bf07f267163f75997d71c4e9d246b169d42e27645dfd9a175cf41095` |
| 文件不存在時 update 回 404 | bulk 文件範例：update 回 `"status": 404`、`document_missing_exception` | docs-bulk.html（同第一列） | 同第一列 |
| 批次大小用倍增法實測；不要超過數十 MB；429 要隨機指數退避 | "First try to index 100 documents at once, then 200, then 400" … "avoid going beyond a couple tens of megabytes per request" … "TOO_MANY_REQUESTS (429) … randomized exponential backoff" | https://www.elastic.co/guide/en/elasticsearch/reference/8.19/tune-for-indexing-speed.html | `6afcd3643681cd19ea9296d31e98d5671dc8b7724cd7f5d0955f8873c4c4a1bd` |
| 大量載入時可調大 refresh、初次載入可把副本設 0 | "increasing the index.refresh_interval to a larger value, e.g. 30s" … "set index.number_of_replicas to 0" | 同上 | 同上 |
| refresh 預設 1 秒、-1 關閉；30 秒沒搜尋的 shard 不會背景 refresh | "Defaults to 1s. Can be set to -1 to disable refresh" … "will not receive background refreshes until they receive a search request" | https://www.elastic.co/guide/en/elasticsearch/reference/8.19/index-modules.html | `e0f9558e2fb9c701ade8a321588fd9be60cd1d3e243ea0b16be186d734f4c158` |
| 大量寫入前把 refresh 設 -1，完成後改回 | "Bulk indexing usage" 一節的範例 | https://www.elastic.co/guide/en/elasticsearch/reference/8.19/indices-update-settings.html | `2c8d4d7c3920eb0e42c1ff50572c4caa04866237096159ce80bb1ae4cef1c70d` |
| 加新欄位不需 reindex；改型別才要新 index＋reindex | "Add new fields to an existing index" … "does not allow in-place field type changes" | https://www.elastic.co/guide/en/elasticsearch/reference/8.19/indices-put-mapping.html | `af26126011b0b362be43f999e572d159fe79660ea5c566927aa9e89ae7bb3814` |
| alias 切換是原子操作 | "perform multiple actions in a single atomic operation" … "never points to both streams at the same time" | https://www.elastic.co/guide/en/elasticsearch/reference/8.19/aliases.html | `43e53a91873dc9289e9983106fae2ac0b5df22500850216e62dbf4ce62dc2f58` |
| reindex 不複製設定與 mapping；可用 `requests_per_second` 限速 | "Reindex does not copy the settings from the source" … "throttled by padding each batch with a wait time" | https://www.elastic.co/guide/en/elasticsearch/reference/8.19/docs-reindex.html | `d888b954f64391fbc477bf7dbe55947ffdba88337cfc10246678e9b5066ff4ec` |
| `_update_by_query` 的 `requests_per_second` 是每秒子請求數（約等於文件數），預設 -1 不限速，以批次間等待實作 | "sub-requests per second. Defaults to -1 (no throttle)" … 批次 1000、設 500 時 "target_time = 1000 / 500 per second = 2 seconds" | https://www.elastic.co/guide/en/elasticsearch/reference/8.19/docs-update-by-query.html | `a92826a1fc3132ecc07ec91b54ad8c328050d0bfc12d3a7f3678d376517d865d` |
| 腳本可設 `ctx.op = 'noop'`；update 仍會整份重新索引；`retry_on_conflict` 預設 0 | `ctx.op = 'noop'` 範例；"The document must still be reindexed"；"retry_on_conflict … Default: 0" | https://www.elastic.co/guide/en/elasticsearch/reference/8.19/docs-update.html | `ace6cbbbf33bca05c7c614e058af6a3e9164ccd20087d17b7088b74c6a8801ff` |
| external version 小於或等於現有版本時失敗（409） | "If the value provided is less than or equal to the stored document’s version number, a version conflict will occur" | https://www.elastic.co/guide/en/elasticsearch/reference/8.19/docs-index_.html | `005a0d6f08320c65722c93149d916d1e0809b0255add0ea83dd68729d6a27e74` |

這些文件對答案的影響：

- `_update_by_query` 只能用 ES 裡已有的資料改文件，回補的數字在 GCS，所以不適用；但它的限速單位是「每秒文件數」，本程式的 `--rate-per-sec` 是「每秒 bulk 請求數」，換算是請求數 × 每批篇數（2 × 500 = 每秒約 1,000 篇）。
- 「調大 refresh、副本設 0」是給大量初次載入新 index 用的。回補走線上 index 的部分更新時不能這樣做，否則爬蟲新寫入的文件查不到，直接違反 1 小時 SLA；b-c-d.md 已寫明，並把 refresh 設定加進 (d) 的監控。
- update 會整份重新索引文件，所以部分更新對線上 index 不是零成本，限速與離峰仍然必要。
- update 寫法每篇約 952 B（大部分是重複的腳本原文），500 篇一檔約 0.48 MB，`--max-bytes 5MB` 在這種寫法下不會先觸發；批次大小應依文件建議用 100、200、400 倍增實測。
- 逐欄時間戳的腳本（`GUARD_SCRIPT`）只用到上表的 `ctx.op = 'noop'` 與一般的 Map 存取；painless 的 `instanceof`、`throw` 與 for 迴圈沒有另外查文件，但已在 ES 8.19.23 與 9.5.5 上實際執行過（見「送出端（真的 Elasticsearch，本機 Docker）」）。

## 輸出與 log 位置

| 內容 | 位置 |
|---|---|
| 命令列示範 log（樣本、原型與上一輪對照、冪等、壞資料、分區關卡、單筆 429、逐欄時間戳） | `q5/code/logs/demo_cli.log`（`demo_cli.sh` 的完整輸出，逐字） |
| 送出端示範 log | `q5/code/logs/demo_send.log`（假 ES）；`q5/code/logs/demo_real_es_8.19.23.log`、`demo_real_es_9.5.5.log`（真的 ES，`demo_real_es.sh` 的完整輸出） |
| 工時重算輸出（兩種依賴讀法、甘特圖） | `q5/code/logs/schedule.log` |
| pytest 最後一次執行 | `q5/code/logs/pytest.log` |
| 示範程式 | `q5/code/backfill/demo_cli.sh`、`demo_checks.py`、`demo_send.py`、`demo_real_es.sh`、`demo_real_es.py`、`make_sample.py` |
| 樣本、bulk 檔、quality.json、rejects.jsonl | `<tmp>/work/q5/fix/cli/run/`、`<tmp>/work/q5/fix/send/`（不進 repo） |
| ES 文件原始 HTML 與下載紀錄 | `<tmp>/work/q5/dl2/*/page.html`、`<tmp>/work/q5/dl2/fetch_log.json`（上一輪；不進 repo） |

## 與既有說法的差異／修正

本輪：

| 原本的說法（位置） | 現在 | 依據 |
|---|---|---|
| 「回補不會蓋掉爬蟲較新的寫入」（b-c-d.md (c) 第 5 點；q5/README.md「以 `counts_crawled_at` 防止舊資料蓋掉新資料」） | 上一輪整篇只有一個時間戳，三欄來自不同次抓取時不成立：20 被蓋成 10。本輪改成逐欄時間戳，說法加上前提「爬蟲寫入也帶 `counts_at`」 | 指令表 #20、#21；`test_guard_is_per_field_so_older_value_never_overwrites_newer` |
| 上一輪的限制寫「整篇一個時間戳只會讓某一欄留空」 | 也會讓較舊的值蓋掉較新的值（上一列）。兩種情況本輪都解決 | `test_guard_older_run_sent_later_fills_only_fields_still_missing` |
| 欄位名的風險只靠 `alias_used` 統計（b-c-d.md (c) 第 7 點與風險表）；原型的答案寫「數量異常就停」，但程式從來沒有看欄位名的關卡 | 加了分區關卡：任一分區任一欄不知道的比例超過 20% 就退出碼 3，quality.json 列出分區 key 清單 | 指令表 #15–#17；`test_cli_gate_stops_partition_with_unrecognized_field_names` |
| 「舊版程式只讀 `*.json`、2,000 篇只處理到 745 篇」被當成舊版的問題（上一輪本檔） | 原型寫明的輸入就是 `src/**/*.json`，`.jsonl` 分區是上一輪自己做的樣本格式；這是把輸入擴大到 `.jsonl`，不是原型的缺陷。GCS 實際格式要在盤點時確認 | 指令表 #3、#12；原型 docstring |
| 「b-c-d.md 現在約 10,700 字元（原本約 6,800）」（上一輪本檔） | 那是位元組數（`wc -c` 10,896／6,776）。Python `len()` 的字元數是 5,927（原本 3,947）；本輪後 7,038 字元（13,259 B）。題目上限 600 字，交卷用 `answer_600.txt`（599 字元） | Python `len()` |
| IG 兩個 key 都有且值不同時，寫入 `play_count`、只記旗標（上一輪 normalize.py） | 當成不知道、不寫入，記 `alias_conflict` | `test_ig_conflicting_keys_are_unknown_until_pm_decides` |
| 示範 log 由 scratch 裡的腳本產生；指令表的「HEAD 單筆 429」沒有進 repo 的 log（上一輪 #17） | `demo_cli.sh`、`demo_checks.py` 進 repo，單筆 429 是 log 的 L 段 | `logs/demo_cli.log` |
| update 寫法每篇約 647 B、每檔約 0.3 MB | 腳本變長，每篇約 952 B、每檔約 0.46–0.48 MB；5 MB 上限仍不會先觸發 | `run1/manifest.json` |
| RESULTS.md 在 `q5/code/backfill/`、`schedule.log` 放在 `backfill/logs/` | `q5/code/RESULTS.md`、`q5/code/logs/` | repo 版面規定 `qN/code/RESULTS.md` |

上一輪的修正（本輪重跑後仍成立）：

| 原本的說法（位置） | 現在 | 依據 |
|---|---|---|
| 示範指令「未留 log，退出碼未核對」（python.md 五） | 退出碼 0，log 在 `logs/demo_cli.log` C 段 | 指令表 #4 |
| DE 關鍵路徑 42 h，約 8 工作天 ≈ 10 日曆天（b-c-d.md、python.md） | 關鍵路徑 46 h，9 工作天，週一開工約 11 日曆天 | 工時重算表、`schedule.py` |
| BE、FE 工時未核對（python.md 五） | BE 24 h、FE 10 h，與表格一致 | `test_role_hours_match_table` |
| 回補寫進新 index（`index`＋external version）再切 alias（原型的 b-c-d.md (c)） | 預設改為線上 index 部分更新；新 index 要先 reindex 才能用 | 回補資料只有計數與少數欄位，整份 `index` 會把 ES 文件的其他欄位蓋掉；put mapping 文件說加新欄位不需 reindex |
| 同一篇只留 crawled_at 最大的一筆（原型的 `dedupe_latest`） | 每個計數欄位取「有值」的最新一次；JSON null 算有值 | 最新一次缺 key 時，原型會把較舊的真實值丟掉；樣本中 reshare 有 58 篇屬於這種 |
| 429 時「退避重送同一檔」（原型的 b-c-d.md (c)） | 只重送被拒的那幾筆；單筆 429 也會重送；送出失敗時退出碼 4 | 指令表 #18、#19 |
| `video_view_count` 印在 Reels 縮圖上（q5/README.md） | 縮圖的數字對應 JSON 的 `play_count` | `shared/capture/capture_manifest.json` 10/30 更正 |
| 測試資料「畫面 60（35＋25，reshare=null）」（原型的 test_backfill.py） | 35＋25 那篇分享是 15；reshare 為 null 的是 4＋0 那篇 | `threads-evidence-panel.png` ①② |
| 任務說明提到「GraphQL fixture 裡的 repost_count／reshare_count／quote_count」 | fixture 沒有任何計數欄位 | `test_fixture_has_no_count_fields` |
| ES 寫入錯誤率「排除 409」（原型的 b-c-d.md (d)） | noop 與 index 寫法的 409 不算失敗；update 寫法的 409 會重送 | 程式 `send_bulk_files` |

不在本資料夾、當時沒有改的（行號是 HEAD `b9da1cb` 的）。2026-10-31 整理 PR #3 時逐項確認都已改好：根目錄 README、`shared/docs/python.md`、`shared/docs/HANDOFF.md` 都寫 9 工作天、關鍵路徑 46 小時、Q5 39 項；`build_arch.py` 已由 `shared/architecture/build/build_arch_v2.py` 取代；`capture_manifest.json` 已寫 12 筆。下表留作紀錄：

| 檔案與行號 | 現在寫的 | 應改成 |
|---|---|---|
| `README.md` 第 59 行（Q1–Q6 索引；審查時在 `fc0b075` 的第 48 行） | 約 8 工作天（約 10 日曆天） | 9 工作天（週一開工約 11 日曆天） |
| `README.md` 第 799 行（審查時在第 58 行） | 35 項全過：…Q5 11… | Q5 39（backfill 34＋schedule 5），合計依各題最新數字重算 |
| `README.md` 第 735、739、767 行 | 資料工程師合計 42 小時就是關鍵路徑；42 ÷ 8＝5.25，約 8 工作天、約 10 日曆天；測試共 11 項 | 關鍵路徑 46 小時（DE 42＋全員驗收 4），9 工作天，週一開工約 11 日曆天；測試 39 項 |
| `README.md` 第 720 行 | (c) 先寫新 index，以 `crawled_at` 當 external version，再用 alias 切換 | 預設對線上 index 部分更新，逐欄時間戳 `counts_at`；新 index 是替代做法 |
| `shared/docs/python.md` 第 15、57–59、83 行 | 11 passed；42 h；8 工作天 ≈ 10 日曆天 | 39 passed（34＋5）；46 h；9 工作天、約 11 日曆天 |
| `shared/docs/HANDOFF.md` 第 39 行 | 工時約 8 工作天／10 日曆天 | 9 工作天／約 11 日曆天 |
| `shared/architecture/build_arch.py` 第 39 行 | crawled_at 當 external version；約 8 工作天、10 個日曆天 | 逐欄時間戳部分更新；9 工作天、約 11 個日曆天 |
| `shared/capture/capture_manifest.json` 第 239 行 | Reels 分頁 8 支的 play_count | `ig_reel_fields.json` 有 12 筆（畫面看得到 8 支，JSON 有 12 個 edge） |

## 未完成與限制

- painless 腳本（`GUARD_SCRIPT`）只在本機 Docker 的單節點 ES 8.19.23 與 9.5.5、用合成樣本跑過。QSearch 正式的 ES 版本、index 的實際 mapping（是否 `strict`、計數欄位現在的型別）都不知道，上線前仍要在 staging 對正式 mapping 跑一次 `demo_real_es.py` 的情境。HTTP 429／500、單筆 429 與連線逾時只在假 ES 上測過；吞吐沒有量。
- 逐欄保護要靠爬蟲配合：爬蟲每次寫計數也寫 `counts_at.<欄位>`（epoch 毫秒），最好走同一支腳本。爬蟲端的程式不在 q5，本輪只寫進答案的 (b) 第 1、2 項與風險表。
- 分區關卡的門檻 20% 是依合成資料訂的；真實資料的缺 key 比例要盤點後才知道。key 清單只列 post 物件兩層內的名稱，第三層以下改名看不到名稱，但仍會被不知道的比例擋下。
- 舊欄位名全部是假設；沒有 GCS 原始檔可以盤點。GCS 檔案格式（`.json` 或 `.jsonl`）也待確認。
- IG `play_count` 與題目的 `video_view_count` 是否同一個定義（例如播放次數與觀看次數的算法）沒有查證；兩個 key 值不同的那一筆目前不寫入。
- 送出時的連線錯誤（逾時、連線中斷）沒有攔截，程式會中止；帳本會讓重跑只送沒送完的檔。
- 合併在記憶體內做，每次跑的量要靠「平台 × 週」切批控制。
- 工時本身是估計，只核對了算術、依賴與日曆天，沒有驗證每項工時是否合理。
- b-c-d.md 是完整版（7,038 字元），遠超過題目上限 600 字；交卷用 `answer_600.txt`。
- ES 文件引用的是 8.19 指南（已停止更新）與現行 API 文件；QSearch 實際的 ES 版本未知。
