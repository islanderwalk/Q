# Q2 實測結果：Postgres 16 貼文表與中文全文索引

## 結論

1. **原頁數字在 Linux 完全重現。** 與原頁同版本（PostgreSQL 16.15、Ubuntu）的雲端環境實跑 100 萬列：表 1,024,000,000 B、`to_tsvector('simple')` 3,268,608 B 都與原頁一模一樣；兩字詞 GIN −0.03%、pg_trgm +0.13%。
2. **Windows 上 simple 索引多 43% 的原因已查明，而且重現到位元組。** Windows 版 PostgreSQL 把 U+9FC4 到 U+9FFF 這 60 個較新的 CJK 字當成「非字母」，`to_tsvector` 與 pg_trgm 會在這些字斷開。在 Linux 先把這 60 個字換成空白再建索引，simple 得到 4,677,632 B、pg_trgm 得到 409,526,272 B，和 Windows 實測完全相同；換成相鄰的範圍（多 8 字或少 8 字）就對不上。
3. **原頁的 406 MB 只是下限，真實中文約大 2.3 到 2.9 倍。** 用真實中文 100 萬則（每則 250 字）實測：兩字詞 GIN 是 947 MB（PTT 八卦板問答）到 1,165 MB（中文維基），約是表的 1.0 到 1.3 倍，不是原頁的 40%；pg_trgm 更大，1.37 到 1.55 GB。
4. **2 字詞查詢是 pg_trgm 的弱點。** 2 字中文詞湊不出完整的三字詞，pg_trgm 的 LIKE 查詢在 100 萬則上全部退回全表掃描（0.46 到 0.68 秒）；兩字詞索引都走索引（9 到 278 毫秒，唯一例外見下文）。3、4 字詞兩者差不多。兩種索引在 14 組查詢的結果數全部一致。第一輪表上 9.2 到 10 秒的 4 格不是全表掃描：psycopg 自動 prepare 之後，PostgreSQL 改用不看參數的 generic plan，走了 pg_trgm 索引卻抽不出三字詞。這已在 PTT 100 萬則上重現（9.7、10.6 秒），程式也已修正（第四節）。
5. **真的 pg_bigm 也量了，和陣列模擬只差 0.8%。** 在 Docker 裡用官方 PostgreSQL 16.15 映像編 pg_bigm 1.2。原頁方式 100 萬列：pg_bigm GIN 409,567,232 B，比原頁 406,396,928 B 多 0.78%、比模擬多 0.81%；多出來的是 pg_bigm 在每個詞頭尾各補一個空白產生的兩字詞（每則 251 個，模擬是 249 個）。PTT 真實中文 100 萬則：pg_bigm 953,729,024 B，比模擬（946,946,048 B）多 0.72%，所以「真實中文約 1 GB」不變。2 字詞查詢 pg_bigm 走索引（119 到 147 毫秒），pg_trgm 全表掃描（0.54 到 0.66 秒）；「台灣」出現在 54% 的列，規劃器對 pg_bigm 也選全表掃描（0.61 秒），沒有模擬版 42.6 秒的問題。7 組查詢的結果數與 pg_trgm 全部相同。

對 Q2 答案的影響：(c) 中文全文索引的估算應改為「約與原始資料同大（1.0 到 1.3 倍），100 萬則約 1 GB」，表加索引約 2 GB；選兩字詞（pg_bigm）而不選 pg_trgm 的理由多了兩個實測數字：體積小 25% 到 31%，而且 2 字詞查詢走得到索引。這兩點都用真的 pg_bigm 在 PTT 資料上確認過（體積比 pg_trgm 小 30%；高雄、颱風、政府三個 2 字詞查詢快 3.7 到 4.9 倍，台灣兩邊都全表掃描）。另外，服務若用 prepared statement，pg_trgm 的 2 字詞 LIKE 會被 generic plan 拖到約 10 秒（實測 9.6 到 10.6 秒），這類查詢要設 `plan_cache_mode = force_custom_plan` 或不 prepare。

## 環境

| 項目 | Linux（本次） | Windows（PR #2，本機） |
|---|---|---|
| OS | Ubuntu 24.04，Linux 6.18，4 核心、16 GB RAM（雲端容器） | Windows 11，18 邏輯核心、16.45 GB RAM |
| PostgreSQL | 16.15（Ubuntu 套件 `16.15-0ubuntu0.24.04.1`） | 16.15（EDB 官方 Windows x64 binaries，sha256 `25e6fcdf…8f81e`） |
| 叢集 | `initdb -E UTF8 --locale=C -A trust`，只聽 `127.0.0.1:55432` | 同左 |
| 測試資料庫 | `LC_COLLATE 'C'`、`LC_CTYPE 'C.UTF-8'` | `LC_COLLATE 'C'`、`LC_CTYPE 'zh-TW.UTF-8'` |
| 記憶體設定 | `shared_buffers` 128MB、`maintenance_work_mem` 1GB、`max_parallel_maintenance_workers` 2 | 同左 |
| Python | 3.13.17，psycopg 3.3.6、pytest 9.2.0、pyarrow | 3.10.8，psycopg 3.3.6、pytest 9.2.0 |
| 擴充 | pg_trgm、pageinspect | 同左 |
| pg_bigm | 1.2，在 Docker 容器裡（官方 `postgres:16.15-bookworm` 映像加 pg_bigm；PostgreSQL `16.15-1.pgdg12+2`，記憶體設定同上） | 沒有（EDB 發行版不含） |

兩字詞索引用 GIN(array_ops) 對 `q2_bigrams(message)` 建立（見 `sql/01_schema.sql`），結構與 pg_bigm 相同（key 是兩字詞，posting list 是列位址）。真的 pg_bigm 另外在 Docker 裡量（`run_q2.py --pg-bigm`，SQL 在 `sql/06_pg_bigm.sql`）：映像用 repo 的 `shared/infra/onprem/postgres-bigm/Dockerfile` 建，`PG_BASE` 換成 `mirror.gcr.io/library/postgres:16.15-bookworm@sha256:e8a07d25…bd938`；pg_bigm 1.2-20250903 的原始碼由同目錄的 `fetch_pg_bigm.py` 從 PGDG 的 SRPM 取出（tarball sha256 `4d4fb481…b3142`，建置時再驗一次），`postgresql-server-dev-16` 鎖在與映像相同的 `16.15-1.pgdg12+2`。主機上的 PostgreSQL 沒有裝任何外部套件。Windows 的 EDB 發行版不含 pg_bigm。

`run_q2.py` 新增 `--ctype`：Windows 預設 `zh-TW.UTF-8`、其他平台預設 `C.UTF-8`（Linux 沒有裝中文 locale 也能讓 pg_trgm 抽出中文三字詞，已實測）。

## 指令與退出碼（Linux）

| 指令 | 退出碼 | 輸出 |
|---|---|---|
| `python q2/code/run_q2.py --rows 1000000 --mode repeat --db q2_linux --out …/q2_run_1000000_repeat_linux.json` | 0 | [`q2_run_1000000_repeat_linux.json`](../data/q2_run_1000000_repeat_linux.json) |
| `python q2/code/probe_simple_gin.py --db q2_linux --mem 64MB 1GB --nonalpha-from 9FBC 9FC4 9FCC --out q2/data/q2_probe_simple_gin_linux.json` | 0 | [`q2_probe_simple_gin_linux.json`](../data/q2_probe_simple_gin_linux.json) |
| `python -I q2/code/prepare_corpus.py ptt --txt Gossiping-QA-Dataset.txt --how concat --out ptt_concat_250.txt` | 0 | 50,446 則（語料不進 repo） |
| `python -I q2/code/prepare_corpus.py ptt --txt Gossiping-QA-Dataset.txt --how recombine --rows 1000000 --out ptt_recombine_250.txt` | 0 | 1,000,000 則 |
| `python -I q2/code/prepare_corpus.py wiki --parquet train-0000{1,2,3,4}-of-00006.parquet --per-article 40 --rows 1000000 --out wiki_250.txt` | 0 | 1,000,000 則 |
| `python q2/code/run_q2.py --rows 50446 --mode corpus --corpus ptt_concat_250.txt --db q2_ptt_concat` | 0 | [`q2_run_50446_corpus-ptt_concat_250.json`](../data/q2_run_50446_corpus-ptt_concat_250.json) |
| `python q2/code/run_q2.py --rows 50446 --mode corpus --corpus ptt_recombine_250.txt --db q2_ptt_small` | 0 | [`q2_run_50446_corpus-ptt_recombine_250.json`](../data/q2_run_50446_corpus-ptt_recombine_250.json) |
| `python q2/code/run_q2.py --rows 1000000 --mode corpus --corpus ptt_recombine_250.txt --db q2_ptt --bigram-timeout-min 120` | 0 | [`q2_run_1000000_corpus-ptt_recombine_250.json`](../data/q2_run_1000000_corpus-ptt_recombine_250.json) |
| `python q2/code/run_q2.py --rows 1000000 --mode corpus --corpus wiki_250.txt --db q2_wiki --bigram-timeout-min 120` | 0 | [`q2_run_1000000_corpus-wiki_250.json`](../data/q2_run_1000000_corpus-wiki_250.json) |
| `python q2/code/query_bench.py --db q2_ptt --terms 台灣 高雄 颱風 政府 捷運站 柯文哲 中華民國 --out q2/data/q2_query_bench_ptt.json` | 0 | [`q2_query_bench_ptt.json`](../data/q2_query_bench_ptt.json) |
| `python q2/code/query_bench.py --db q2_wiki --terms （同上） --out q2/data/q2_query_bench_wiki.json` | 0 | [`q2_query_bench_wiki.json`](../data/q2_query_bench_wiki.json) |
| `python3 -I shared/infra/onprem/postgres-bigm/fetch_pg_bigm.py <build>/vendor` | 0 | `pg_bigm-1.2-20250903.tar.gz`，36,292 B，sha256 與檔內固定值相同 |
| `docker build --network host --build-arg PG_BASE=mirror.gcr.io/library/postgres:16.15-bookworm@sha256:e8a07d25…bd938 --secret id=build_ca,… -t qsearch-q2-pg16-bigm:local <build>`（Dockerfile 是 repo 的 `shared/infra/onprem/postgres-bigm/Dockerfile`） | 0 | builder 階段裝 `postgresql-server-dev-16=16.15-1.pgdg12+2` 編出 `pg_bigm.so`，33 秒 |
| `docker run -d --name q2bigm --shm-size=1g -p 127.0.0.1:55433:5432 -e POSTGRES_HOST_AUTH_METHOD=trust qsearch-q2-pg16-bigm:local` | 0 | PostgreSQL 16.15（Debian `16.15-1.pgdg12+2`），pg_bigm 1.2、pg_trgm 1.6、pageinspect 1.12 |
| `python q2/code/run_q2.py --rows 1000000 --mode repeat --port 55433 --skip-bigram --pg-bigm --out …` | 0 | [`q2_run_1000000_repeat_pg_bigm_linux.json`](../data/q2_run_1000000_repeat_pg_bigm_linux.json) |
| `python q2/code/run_q2.py --rows 1000000 --mode corpus --corpus ptt_recombine_250.txt --port 55433 --db q2_ptt_bigm --skip-bigram --pg-bigm --out …` | 0 | [`q2_run_1000000_corpus-ptt_recombine_250_pg_bigm.json`](../data/q2_run_1000000_corpus-ptt_recombine_250_pg_bigm.json) |
| `python q2/code/query_bench.py --port 55433 --db q2_ptt_bigm --queries trgm_like pg_bigm_like --terms （同上） --out …`（`prepare=False` 修正後重跑） | 0 | [`q2_query_bench_ptt_pg_bigm.json`](../data/q2_query_bench_ptt_pg_bigm.json) |
| `python q2/code/generic_plan_check.py --port 55433 --db q2_ptt_bigm --terms （同上） --explain-terms 颱風 政府 --out …` | 0 | [`q2_generic_plan_check_ptt.json`](../data/q2_generic_plan_check_ptt.json)：psycopg 預設 9,721／10,582 毫秒，`prepare=False` 547／666 毫秒 |
| `PY=… PORT=55434 bash q2/code/run_pg_bigm.sh <scratch> --rows 10000 --mode repeat --skip-bigram --db q2_smoke --out …`（上面 fetch、build、run、`run_q2.py` 四步包成一支，從 repo 重跑用；失敗即停的修正後重跑） | 0 | 約 1 分鐘；1 萬列的 pg_bigm GIN 4,145,152 B，LIKE 結果數與 pg_trgm 相同，最後刪掉容器 |
| `PY=… PORT=55433 bash q2/code/run_pg_bigm.sh <scratch> --rows 10 --mode repeat --skip-bigram --db q2_fail --out …`（55433 已被 `q2bigm` 佔用，故意測失敗） | 125 | `docker run` 綁不到埠就停，沒有跑 `run_q2.py`；`q2bigm` 的 `fb_posts` 仍是 1,000,000 列 |

語料來源（只記來源與 sha256，檔案不進 repo）：

| 語料 | 來源 | sha256 |
|---|---|---|
| PTT 八卦板問答 `Gossiping-QA-Dataset.txt`（34,079,536 B） | https://github.com/zake7749/Gossiping-Chinese-Corpus （raw.githubusercontent.com） | `cf5ef0a931a8a14444a9854aa13cf6e7516b3ec4922b7cde5d45d13ed825ae79`（與 Q6 `verify.log` 的前 16 碼相同） |
| 中文維基 `wikimedia/wikipedia` 20231101.zh，第 1 到 4 個 parquet | https://huggingface.co/datasets/wikimedia/wikipedia | `038b08e5…5f703`、`ec8f6c0d…1338f`、`4884e493…4cbb1`、`415a5cd2…150cd` |
| 產生的 `ptt_recombine_250.txt` | `prepare_corpus.py`，seed 2026 | `8ae10854…05056` |
| 產生的 `wiki_250.txt` | 同上 | `41d37843…abdd23` |

## pytest

| 指令 | 結果 | 退出碼 |
|---|---|---|
| `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider q2/code/tests` | 24 passed（原 9 項＋語料模式、探針 12 項＋pg_bigm 2 項＋查詢不讓 psycopg 自動 prepare 1 項）；10/31 加 `test_q2_answer_length.py` 1 項後 25 passed | 0 |

## 一、原頁、Linux、Windows 三方對照（100 萬列，repeat 模式＝原頁的造資料方式）

| 項目 | 原頁 `const D` | Linux 實測 | Windows 實測 |
|---|---|---|---|
| (a) 表大小 | 1,024,000,000 B／125,000 頁 | 1,024,000,000 B／125,000 頁（0%） | 同左（0%） |
| 每頁列數／`lp_len` | 8／918 B | 8／920 B | 8／920 B |
| (b) btree(page_id) | 未列 | 6,873,088 B（約 6.9 B/列） | 6,873,088 B |
| (c) 兩字詞 GIN | 406,396,928 B | 406,257,664 B（−0.03%） | 406,257,664 B（−0.03%） |
| 兩字詞 entry 數 | 248,925 | 248,926 | 248,926 |
| (c) pg_trgm GIN | 409,247,744 B | 409,788,416 B（+0.13%） | 409,526,272 B（+0.07%） |
| (c) simple GIN | 3,268,608 B／399 頁 | **3,268,608 B／399 頁（0%）** | 4,677,632 B（+43%，原因見下節） |
| (c) 真的 pg_bigm GIN | 未列 | 409,567,232 B（+0.78%；比模擬 +0.81%） | 沒有 pg_bigm |
| pg_bigm entry 數／每則兩字詞 | 248,925／249（原頁的兩字詞索引） | 250,885／251 | — |
| 建索引：兩字詞 | 7 分 39 秒 | 387 秒 | 540 秒 |
| 建索引：pg_trgm | 1 分 17 秒 | 114 秒 | 183 秒 |
| 建索引：simple | 5.5 秒 | 7.2 秒 | 48.7 秒 |
| 建索引：pg_bigm | — | 132 秒（容器） | — |
| 造資料 | 未列 | 24 秒 | 295 秒 |

兩個平台的造資料結果相同（`setseed` 後的亂數序列一致，兩字詞索引在兩邊都是 406,257,664 B、entry 數相同）。原頁的資料是另一次產生的（entry 數差 1、pg_trgm 差 0.13%），所以原頁與本次只比到「同級」。查詢核對：4 字詞在兩字詞索引與 pg_trgm 都回 1,000 列，simple 回 0 列（整段 250 字是一個詞），與原頁說明一致。

**真的 pg_bigm。** pg_bigm 先依空白切詞，每個詞頭尾各補一個空白再切兩字詞（原始碼 `bigm_op.c` 的 `generate_bigm`，`LPADDING`、`RPADDING` 各 1），所以每則比模擬多「空白＋首字」「尾字＋空白」兩個；1,000 段內文的首字、尾字去重後，entry 多 1,959 個，體積多 0.81%。原頁每則算 249 個兩字詞（`D.postings1m` 是 249,000,000），entry 數 248,925 也和模擬（248,926）幾乎相同；用 pg_bigm 的話 entry 應該多約 2,000 個，所以原頁量的大概也是陣列做法，不是 pg_bigm 本身（推論，原頁沒寫用哪一種）。同一份資料在容器（Debian 套件）與 Ubuntu 套件上，表、simple、pg_trgm 的體積逐位元組相同。查詢詞 4 字：pg_bigm 與 pg_trgm 都回 1,000 列，計畫分別走自己的索引（1.0 毫秒、1.3 毫秒）。

## 二、simple 索引在 Windows 多 43% 的原因

Linux 上每則內文只有 1 個 lexeme（`lexemes_per_row = 1.0`），1,000 段內文就是 1,000 個 entry，399 頁。調 `maintenance_work_mem`（64MB 與 1GB）體積完全相同，排除建索引記憶體的影響。

假設：Windows 的字元分類表不把部分 CJK 字當字母，`to_tsvector` 的 parser 與 pg_trgm 都在非字母處斷詞，entry 變多。驗證方法：在 Linux 把指定範圍的字換成空白再建索引（`probe_simple_gin.py --nonalpha-from`），看體積能否與 Windows 實測一致。

| 當成非字母的範圍 | 字數 | simple 體積（entry 數） | pg_trgm 體積 |
|---|---|---|---|
| 無（Linux 原樣） | 0 | 3,268,608 B（1,000） | 409,788,416 B |
| U+9FBC–U+9FFF | 68 | 4,825,088 B（1,787） | 409,649,152 B |
| **U+9FC4–U+9FFF** | **60** | **4,677,632 B（1,700）** | **409,526,272 B** |
| U+9FCC–U+9FFF | 52 | 4,505,600 B（1,616） | 409,485,312 B |
| Windows 實測 | — | 4,677,632 B | 409,526,272 B |

只有 U+9FC4 起的範圍讓兩種索引同時和 Windows 逐位元組相同。U+9FC4–U+9FCB 是 Unicode 5.2（2009）新增的字，所以推論 Windows 這部分的字元表停在 Unicode 5.1（這點是推論，沒有在 Windows 上直接驗證 `iswalpha`）。

影響範圍：造資料從 U+4E00–U+9FFF 的 20,992 字均勻抽，這 60 字平均每則出現 0.7 次，所以合成資料被放大；真實中文幾乎不用這段的字，對真實資料的影響可以忽略。實務上的結論是：**中文斷詞與索引體積會隨作業系統的字元表而變，要在部署的平台（Linux 或 Cloud SQL）上量。**

## 三、真實中文的體積（100 萬則，每則 250 字）

| 100 萬則 | 原頁方式（repeat） | PTT 八卦板問答（重組） | 中文維基 | 隨機不重複（100k，參考上限） |
|---|---|---|---|---|
| 表 | 1,024,000,000 B（8 列/頁） | 910,172,160 B（9 列/頁） | 902,643,712 B（9 列/頁） | 102,400,000 B |
| 內文平均 bytes | 754 | 680 | 684 | 754 |
| 兩字詞 GIN | 406,257,664 B（406 B/則） | **946,946,048 B（947 B/則）** | **1,165,492,224 B（1,165 B/則）** | 9,466 B/則 |
| 兩字詞 GIN ÷ 表 | 40% | 104% | 129% | 924% |
| 兩字詞 entry 數 | 248,926 | 848,556 | 4,716,600 | 24,210,346（10 萬則） |
| 每個 posting 的 bytes | 1.63 | 3.80 | 4.68 | — |
| 真的 pg_bigm GIN | 409,567,232 B | **953,729,024 B（954 B/則，比模擬 +0.72%）** | 未量 | — |
| pg_bigm entry 數 | 250,885 | 849,328 | — | — |
| pg_trgm GIN | 409,788,416 B | 1,371,783,168 B | 1,551,712,256 B | 6,511 B/則 |
| simple GIN | 3,268,608 B | 340,230,144 B | 1,480,933,376 B | 1,649 B/則 |
| btree(page_id) | 6,873,088 B | 6,873,088 B | 6,873,088 B | — |
| 建索引：兩字詞／pg_trgm／simple | 387／114／7 秒 | 890／574／133 秒 | 1,044／755／235 秒 | — |
| 建索引：真的 pg_bigm（容器） | 132 秒 | 570 秒 | — | — |

真的 pg_bigm 那一欄是另一次執行（Docker 容器，同一份語料、同樣 seed）：表、pg_trgm、simple 與上一欄逐位元組相同，pg_bigm 比模擬多 0.72%（entry 多 772 個，是空白補位產生的兩字詞）。PTT 那次建 pg_bigm 的 570 秒裡，有約 5 分鐘同一台機器也在跑 Q5 的 Elasticsearch 示範，時間只供參考。

真實文字含標點、空白與英數字，內文 bytes 比全 CJK 少，所以表反而小一點（每頁 9 列）。兩字詞 GIN 落在原頁下限與隨機上限之間，偏下限那一側，符合齊夫分布的預期。

PTT 語料只有約 1,260 萬字，切 250 字只湊得出 50,446 則，所以 100 萬則是「隨機抽句子重組」。在 50,446 則上比較兩種做法，量出重組造成的低估：

| 50,446 則 | 每句只用一次（concat） | 隨機重組（recombine） | 重組的低估 |
|---|---|---|---|
| 兩字詞 GIN | 99,033,088 B（1,963 B/則） | 90,742,784 B（1,799 B/則） | −8% |
| pg_trgm GIN | 227,287,040 B | 188,932,096 B | −17% |
| simple GIN | 116,154,368 B | 80,625,664 B | −31% |

所以 PTT 100 萬則的兩字詞 GIN 估約 1.0 GB（947 MB × 1.09，補回 8.4% 的低估；8.4% 是 5 萬則量到的，套到 100 萬則是估計，沒有直接量）。另外，同一份語料 5 萬則是 1,799 B/則、100 萬則是 947 B/則：真實文字的索引每則體積會隨資料量下降（posting list 越長壓縮越好），**不能用 5 萬或 10 萬則線性外推**，一定要用目標量級實測。

## 四、2 到 4 字詞的查詢比較（100 萬則真實中文）

每個詞 1 次暖機加 3 次計時取中位數；兩種索引的結果數 14 組全部相同。下表是第一輪（`query_bench.py` 修正前）的結果；標＊的 4 格不是全表掃描的時間，原因見表下。

| 查詢詞 | PTT：結果數 | PTT：兩字詞／pg_trgm（毫秒） | 維基：結果數 | 維基：兩字詞／pg_trgm（毫秒） |
|---|---|---|---|---|
| 台灣 | 542,820 | 42,615 ／ 578（全表掃描） | 43,711 | 119 ／ 683（全表掃描） |
| 高雄 | 46,393 | 139 ／ 513（全表掃描） | 7,144 | 13 ／ 464（全表掃描） |
| 颱風 | 33,977 | 113 ／ 9,504＊ | 4,260 | 9 ／ 9,222＊ |
| 政府 | 34,617 | 109 ／ 10,011＊ | 89,155 | 278 ／ 9,386＊ |
| 捷運站 | 3,152 | 23 ／ 24 | 89 | 18 ／ 18 |
| 柯文哲 | 1,578 | 26 ／ 3 | 549 | 21 ／ 1 |
| 中華民國 | 28,336 | 101 ／ 126 | 16,275 | 36 ／ 27 |

- **2 字詞**：`show_trgm('颱風')` 只有補空白的三字詞，LIKE 用不到 pg_trgm 索引。計畫是 Seq Scan，量到 0.46 到 0.68 秒；PTT 的颱風、政府用修正後的程式重量是 0.55、0.67 秒。兩字詞索引都走 Bitmap Index Scan。
- **標＊的 9.2 到 10 秒是量測方法的問題，已修正。**
  - 原因：第一輪 `query_bench.py` 在同一條連線上，對 7 個詞依序各跑 5 次同一句 LIKE。psycopg 預設同一句 SQL 執行到第 6 次就改成伺服器端 prepared statement。PostgreSQL 對 prepared statement 前 5 次用 custom plan，之後若不看參數的 generic plan 估得比較便宜，就改用它。所以第 3、4 個詞（颱風、政府）計時的是 generic plan：它走 pg_trgm 索引，但 2 字詞抽不出三字詞，索引回傳全部列，再逐列 recheck。`plan_uses` 是另一句 EXPLAIN 抓的，看到的還是 custom plan 的 Seq Scan。
  - 重現：`generic_plan_check.py` 在 PTT 100 萬則上跑（[`q2_generic_plan_check_ptt.json`](../data/q2_generic_plan_check_ptt.json)）。照第一輪的方式，颱風 9,721 毫秒、政府 10,582 毫秒，計畫欄一樣顯示 Seq Scan。`prepare=False` 時是 547、666 毫秒。直接 `EXPLAIN ANALYZE EXECUTE`：generic plan 走 `fb_posts_trgm_gin`，9,708、9,601 毫秒；custom plan 是 Seq Scan，579、645 毫秒。
  - 修正：`run_q2.run_queries` 現在一律 `prepare=False`。
  - 其他格：第 5 到 7 個詞是 3、4 字詞，generic plan 一樣走索引，時間與修正後相同（24／24、3／4、126／127 毫秒）。
  - 沒重量的：維基那 2 格的資料庫已刪，沒有重量；同一份資料台灣、高雄的全表掃描是 0.68、0.46 秒。
- **對服務的意思**：應用程式若用 prepared statement（psycopg 預設就會），pg_trgm 的 2 字詞 LIKE 在同一句執行幾次後會變成約 10 秒。這類查詢要設 `plan_cache_mode = force_custom_plan` 或不 prepare。
- **例外**：PTT 的「台灣」出現在 54% 的列，兩字詞索引要對 54 萬列重算 `q2_bigrams()`（SQL 函式）做 recheck，花 42.6 秒；台灣是第 1 個詞，這時還沒 prepare，是 custom plan。這是用 SQL 函式模擬 pg_bigm 的代價；真的 pg_bigm 沒有這個問題：規劃器看到這個詞太常見，直接選全表掃描，0.61 秒（下表）。高頻詞仍要靠快取或分頁限制筆數。
- **3、4 字詞**：兩者都走索引，時間同級。

**真的 pg_bigm（PTT 100 萬則，Docker 容器）。** 同一份語料重建，`query_bench.py --queries trgm_like pg_bigm_like`（修正後的程式，不 prepare）；兩個 LIKE 查詢各自量時，在交易內拿掉對方的索引（見「修正過程」第 6 點）。7 組結果數全部相同：

| 查詢詞 | 結果數 | pg_bigm（毫秒，計畫） | pg_trgm（毫秒，計畫） |
|---|---|---|---|
| 台灣 | 542,820 | 606（全表掃描） | 603（全表掃描） |
| 高雄 | 46,393 | 147（索引） | 542（全表掃描） |
| 颱風 | 33,977 | 119（索引） | 540（全表掃描） |
| 政府 | 34,617 | 136（索引） | 659（全表掃描） |
| 捷運站 | 3,152 | 8（索引） | 23（索引） |
| 柯文哲 | 1,578 | 4（索引） | 4（索引） |
| 中華民國 | 28,336 | 125（索引） | 134（索引） |

- **兩字詞索引會漏的查詢**：1 字查詢（「台」）切不出兩字詞，索引用不到；異體字（台／臺）與同義詞要另外處理，兩種索引都一樣。

## 修正過程（照實記錄）

Windows 那一輪（PR #2）的修正：int4 溢位改 bigint；純量子查詢被當成 InitPlan 只算一次，改成 `GROUP BY`／`LATERAL`；hash join 打散列順序，加 `ORDER BY r.g`；`LC_CTYPE='C'` 讓 pg_trgm 抽不出中文三字詞，改用中文或 UTF-8 locale；第一次 1M 執行在量測階段被中斷，整次重跑。

本次：

1. `run_q2.py` 原本把 `LC_CTYPE` 寫死成 Windows 才有的 `zh-TW.UTF-8`，Linux 建庫會失敗，改成 `--ctype`（依平台給預設）。
2. 想記錄 `SHOW lc_ctype`，但 PostgreSQL 16 已移除這個參數，第一次執行因此退出碼 1；改讀 `pg_database.datctype` 後重跑，退出碼 0。
3. 第二次執行時，暫存目錄的權限被環境重設，PostgreSQL checkpointer 寫不了 `pg_control` 而 PANIC（伺服器 log：`could not open file ".../global/pg_control": Permission denied`）；資料目錄移到 `/var/lib/qsearch-pg` 後整次重跑。
4. `prepare_corpus.py` 的重組函式第一版少算一個空白，部分貼文只有 249 字；測試抓到後修正，重組語料重新產生（每則都是 250 字，已逐行檢查）。
5. 加 `--pg-bigm` 時讀程式發現：`--skip-bigram` 會跳過建兩字詞索引，量測步驟卻仍查它的 metapage，索引不存在就會失敗。改成一起跳過。
6. pg_trgm 與 pg_bigm 的索引都能處理 LIKE。第一次 100 萬列執行時，pg_bigm 查詢的計畫走的是 pg_trgm 的索引（JSON 的 `plan_uses`），量到的不是 pg_bigm。改成量其中一個時在交易內先拿掉另一個索引、量完 rollback（`run_queries_isolated`，`query_bench.py` 也用它），整次重跑；體積與第一次逐位元組相同。
7. 獨立審查指出第一輪查詢比較的計時被 psycopg 自動 prepare 影響（第四節標＊的 4 格），`generic_plan_check.py` 重現後，`run_q2.run_queries` 改成一律 `prepare=False`，PTT 的 pg_bigm 查詢比較用修正後的程式重跑（數字與修正前差在 10% 內，那次因為每個詞都 rollback，本來就沒有 prepare）。
8. 同一次審查：`run_pg_bigm.sh` 的 fetch、build、`docker run`、等待啟動失敗時只印退出碼，仍會接著跑 `run_q2.py`（會 `DROP TABLE fb_posts`），埠被別的 PostgreSQL 佔用時會打到那台。改成任何一步失敗就停；另外 `--bigram-timeout-min` 超時時，量測步驟仍會查不存在的兩字詞索引，改成跟 `--skip-bigram` 一樣跳過。修正後重跑 smoke 與「埠已被佔用」兩種情況（指令表最後兩列）。

## 檔案

| 檔案 | 內容 |
|---|---|
| `run_q2.py` | 建表、造資料（repeat／distinct／corpus）、建四種索引、量體積與查詢 |
| `prepare_corpus.py` | 真實中文語料切成每則 250 字（PTT 問答、中文維基 parquet） |
| `probe_simple_gin.py` | 追 simple 索引差異：逐頁統計 GIN、重現 Windows 的非字母範圍 |
| `query_bench.py` | 指定 2 到 4 字詞比較兩字詞索引、pg_trgm 與真的 pg_bigm（`--queries`） |
| `run_pg_bigm.sh` | 建 pg_bigm 映像、起容器、跑 `run_q2.py --pg-bigm`、刪容器 |
| `generic_plan_check.py` | 重現第一輪 pg_trgm 2 字詞的 9.2 到 10 秒：psycopg 自動 prepare 後改用 generic plan |
| `q2_model.py` | 原頁常數與 8 KB page 換算 |
| `sql/` | `01_schema`、`02_generate_{repeat,distinct,corpus}`、`03_indexes`、`04_measure`、`05_queries`、`06_pg_bigm` |
| `tests/` | `test_q2_model.py` 12 項、`test_corpus.py` 12 項，皆離線 |
| `../data/` | 每次實跑的 JSON（含參數、伺服器設定、每步秒數、索引 metapage、查詢計畫） |

## 未完成與限制

- 真的 pg_bigm 量了原頁方式與 PTT 兩種資料，中文維基沒有用 pg_bigm 重跑（模擬是 1,165 MB；原頁方式 +0.81%、PTT +0.72%，維基預期也差不到 1%，但沒有量）。pg_bigm 跑在 Docker 容器裡（同版本 PostgreSQL 的 Debian 套件），不是原生安裝。
- 真實語料不是粉專貼文：PTT 問答偏口語、維基偏書面，粉專貼文大概落在兩者之間。PTT 100 萬則是重組的，已量出約 8% 低估。
- 查詢時間只有 3 次中位數、單機、無並發，只用來比較同級差異，不是服務水準。
- 第四節第一輪表上維基的「颱風」「政府」兩格 pg_trgm 時間是 generic plan 量到的，那個資料庫已刪，沒有用修正後的程式重量。
- `shared/infra/onprem/postgres-bigm/Dockerfile` 把 `postgresql-server-dev-16` 釘在映像的 PostgreSQL 版本（16.15-1.pgdg12+2）。PGDG 的 apt 只留最新版，出了下一個 16.x 之後這個版本只在 apt-archive.postgresql.org，到時要加 archive 的來源才建得起來。這是共用的 Dockerfile，這輪沒有改。
- 隨機不重複（distinct）只在 Windows 跑了 10 萬則，沒有在 Linux 跑 100 萬則（推估要約 9 GB 磁碟與 1 小時以上），表中只當參考上限。
