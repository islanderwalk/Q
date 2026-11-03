# Q6 斷詞前後對照（實跑結果）

> 本檔分兩部分：上半部是手寫說明；下半部 `<!-- AUTO:compare:<tag> -->` 標記之間由 `run_compare.py` 產生，重跑只換掉標記內的內容，手寫段落不動。
> 最後更新：2026-10-30，Linux 容器重跑。

## 結論

- 同一份 6,685 篇 PTT 八卦板標題，新流程把前 50 詞裡的停用詞從 10 個降到 0 個（主字典＝jieba 內建；換成繁體 `dict.txt.big` 是 14 個降到 0 個），純數字或符號從 12 個降到 0 個，單字「高」從 222 次降到 0 次（`dict.txt.big` 是 172 次降到 0 次）。
- 「高雄市」整詞次數新舊都是 6，原因不是新流程沒效果，而是用 jieba 字典模擬的舊流程本來就沒拆（公司實際的舊字典不在 repo）：jieba 內建字典與 `dict.txt.big` 都收錄「高雄市」（詞頻 31），語料裡「高雄市」只出現 6 次（6 篇），舊流程 6 次全部整詞切出。題目說的「地名被拆散」在這份語料上出現在不帶「市」的「高雄」：兩份字典都沒有「高雄」這個詞（詞頻 0，只是「高雄市」的前綴佔位），舊流程在 125 篇含「高雄」的標題裡拆開 115 篇。新流程靠 HMM 降到 0 篇（內建）與 1 篇（`dict.txt.big`），但仍有 33 篇與 19 篇黏成「高雄為」「高雄會」這類非字典詞。
- 使用者字典本身有兩個問題：沒有收「高雄」，而且 `高雄市 5` 把主字典的詞頻 31 蓋成 5。試算在新流程加「高雄 31」（等同使用者字典多一行）：兩份主字典下 125 篇全部整詞，切分改變的 37 篇（內建）與 21 篇（`dict.txt.big`）全都含「高雄」，不含「高雄」的 0 篇。用 jieba 建議詞頻（2）時，`dict.txt.big` 下「高雄風力」仍會被「雄風」（詞頻 181）搶走。本次只試算，沒有改 `user_dict.txt`。
- 新增 `--base-dict` 選項（預設不變，仍是 jieba 內建字典），用繁體 `dict.txt.big` 重跑同一份標題：舊流程總詞數 113,845 降到 95,245，「新聞」「颱風」等詞能整詞切出，比內建字典更接近「維護多年的繁體字典」。但 `dict.txt.big` 收錄「萬安」（詞頻 191），「蔣萬安」被切成「蔣」「萬安」，新流程前 50 詞從「蔣萬安 117」變成「萬安 175」；換主字典要搭配人名詞條。
- jieba 實驗在 Linux 重跑兩次，`wordcloud_q6_verify.py` 與 exp1、exp2、exp3 最後退出碼皆為 0（第二次 exp2、exp3 先因輸出目錄不存在而退出 1，補上建目錄後重跑通過）；`results2.json`、`results3.json` 與 repo 版本位元組完全相同，`results.json` 只有 8 個計時欄位不同。兩個下載檔的 sha256 前 16 碼與 `verify.log` 相同。
- ES 中文分析器與字典熱更新的說法已上網查證，來源列在「外部依據」。

## 環境

| 項目 | 版本 |
|---|---|
| OS | Ubuntu 24.04.5 LTS（Linux 6.18.44 容器），4 vCPU Intel Xeon @ 2.10GHz；同時有其他工作在跑，吞吐數字會受影響 |
| Python | 3.13.17（`<tmp>/venv`） |
| 套件 | jieba 0.42.1、pytest 9.2.0、beautifulsoup4 4.15.0（只用來把下載的文件頁轉成文字） |
| 主字典 | jieba 內建 `dict.txt`；`dict.txt.big`（sha256 `b16011275c42955c…`，8,583,143 bytes，不進 repo） |
| 吞吐語料 | `Gossiping-QA-Dataset.txt`（sha256 `cf5ef0a931a8a144…`，34,079,536 bytes，不進 repo） |
| 使用者字典 | `q6/code/dict/user_dict.txt`，`qsearch-q6-2026.10.30.1`（sha256 前 16 碼 `b42e821556616362`）；本檔前面各節都是這一版的結果，10/31 起是 `qsearch-q6-2026.10.31.1`，見最後一節 |

## 指令與退出碼

在 repo 根目錄執行；`$W` 是 `<tmp>/work/q6`，`$PY` 是 `<tmp>/venv/bin/python`，皆設 `PYTHONDONTWRITEBYTECODE=1`。

| # | 指令 | 退出碼 | 說明 |
|---|---|---|---|
| 1 | `curl -sSL --max-time 120 -o $W/dl_dict2/dict.txt.big https://raw.githubusercontent.com/fxsjy/jieba/master/extra_dict/dict.txt.big` | 0（沒有留 log） | sha256 `b16011275c42955c…`，與 `verify.log` 相同。第二次下載只用來複核雜湊，第 3–11 列讀的是第一次下載的 `$W/dl_dict/` |
| 2 | `curl -sSL --max-time 300 -o $W/dl_corpus2/Gossiping-QA-Dataset.txt https://raw.githubusercontent.com/zake7749/Gossiping-Chinese-Corpus/master/data/Gossiping-QA-Dataset.txt` | 0（沒有留 log） | sha256 `cf5ef0a931a8a144…`，與 `verify.log` 相同。同上，只用來複核雜湊，各指令讀 `$W/dl_corpus/` |
| 3 | `$PY -I q6/code/jieba_experiments/wordcloud_q6_verify.py $W/dl_dict/dict.txt.big $W/dl_corpus/Gossiping-QA-Dataset.txt` | 0 | 兩次（07:46Z、12:32Z） |
| 4 | `$PY -I q6/code/jieba_experiments/wordcloud_q6_exp1_measure.py <dict> <corpus> <out>/exp1` | 0 | 兩次 |
| 5 | `$PY -I q6/code/jieba_experiments/wordcloud_q6_exp2_pinned_total.py <dict> <corpus> <out>/exp2` | 1，修正後 0 | 07:46Z 為 0；12:32Z 第一次為 1（`FileNotFoundError`：輸出目錄不存在，exp2、exp3 不像 exp1 會自己建目錄）。補上 `OUT.mkdir(...)` 後重跑為 0 |
| 6 | `$PY -I q6/code/jieba_experiments/wordcloud_q6_exp3_demo_tokens.py <dict> <out>/exp3` | 1，修正後 0 | 同上 |
| 7 | `$PY -I q6/code/jieba_experiments/compare_results.py q6/data/jieba_experiments <out>` | 1，修正後 0 | 第一次因 exp2、exp3 沒有輸出而失敗；重跑後去掉計時欄位三個檔都相同 |
| 8 | `$PY q6/code/run_compare.py` | 0 | 產生 `default` 區塊；重跑後 RESULTS.md、`top50.json`、`segmented_ab.jsonl` 不變 |
| 9 | `$PY q6/code/run_compare.py --base-dict $W/dl_dict/dict.txt.big --tag dict_big --segmented-out $W/out/segmented_ab_dict_big.jsonl` | 0 | 產生 `dict_big` 區塊與 `q6/data/top50_dict_big.json` |
| 10 | `$PY q6/code/kaohsiung_trace.py` | 0 | `q6/data/kaohsiung_trace.json` |
| 11 | `$PY q6/code/kaohsiung_trace.py --base-dict $W/dl_dict/dict.txt.big --tag dict_big` | 0 | `q6/data/kaohsiung_trace_dict_big.json` |
| 12 | `$PY q6/code/affected_demo.py` | 0 | 與舊版程式的輸出逐字相同 |
| 13 | `$PY -m pytest -q -p no:cacheprovider q6/code/tests` | 0 | 見下節 |
| 14 | `curl -sSL` 抓 13 份文件頁（見「外部依據」） | 0 | 沒有另存 HTTP 狀態碼（`curl` 沒加 `-f`，4xx 也會退出 0）；13 份內容都是正常的文件頁，不是錯誤頁 |

07:46Z 那一輪（第 3–6 列）是前一次被中斷的嘗試用 scratch 的 `run_exps.sh` 跑的，log 在 `$W/logs/`；12:32Z 那一輪用 `$W/rerun2/run.sh` 與 `run_b.sh`，log 在 `$W/rerun2/logs/`。

## pytest 結果

指令：`PYTHONDONTWRITEBYTECODE=1 <tmp>/venv/bin/python -m pytest -q -p no:cacheprovider q6/code/tests`

2026-10-30 結果：23 passed，退出碼 0，耗時 5.98 秒（`q6/code/tests/test_segmenter.py` 18 項、`test_kaohsiung_trace.py` 5 項；複核前是 21 項，兩次各 1.45 秒與 1.30 秒）。測試全部離線；主字典選項與「高雄」試算用測試內嵌的極小字典，不需要 `dict.txt.big`。

## 「高雄市」為什麼是 6→6

證據來自 `q6/code/kaohsiung_trace.py` 的輸出（`q6/data/kaohsiung_trace.json`、`kaohsiung_trace_dict_big.json`）與兩份字典原檔。

| 項目 | jieba 內建 | dict.txt.big |
|---|---|---|
| 字典裡的「高雄市」詞頻（舊） | 31 | 31 |
| 載入使用者字典後的「高雄市」詞頻（新） | 5 | 5 |
| 字典裡的「高雄」詞頻 | 0（只是前綴） | 0（只是前綴） |
| 字典有收、以「高雄」開頭的詞 | 高雄市、高雄人、高雄港、高雄县 等 10 個 | 加上繁體共 17 個，同樣沒有「高雄」 |
| 語料：「高雄市」出現次數／篇數 | 6／6 | 6／6 |
| 語料：「高雄」出現次數／篇數 | 129／125 | 129／125 |
| 舊流程切出、含「高雄」的 token | 高雄市 6、高雄人 3、高雄港 1 | 高雄市 6、高雄人 3、高雄港 1 |

舊流程把 6 次「高雄市」全部整詞切出，所以「高雄市」的次數不可能再往上；這個指標量不出改善。該看的是只寫「高雄」的標題，例如「[問卦] 高雄肥橘害死人，沒人負責？」舊流程切成「高 / 雄 / 肥 / 橘」。另外 6 篇裡有一篇「高雄市集棚架掀」，語意是「高雄 市集」，新舊都切成「高雄市 / 集」，整詞次數高也不代表切對。

試算：在新流程加「高雄」（不改 repo 字典；total 照預設累加與固定的結果在這份語料上相同）

| 主字典 | 「高雄」詞頻 | 整詞／黏詞／拆開 | 改變的標題（含「高雄」） | 改變的標題（不含「高雄」） |
|---|---|---|---|---|
| jieba 內建 | 3（jieba 建議值） | 125／0／0 | 37 | 0 |
| jieba 內建 | 31 | 125／0／0 | 37 | 0 |
| dict.txt.big | 2（jieba 建議值） | 124／0／1 | 20 | 0 |
| dict.txt.big | 31 | 125／0／0 | 21 | 0 |

`dict.txt.big` 下剩下的 1 篇是「[問卦] 高雄風力就達標了」：字典有「雄風」181、「風力」333，詞頻 2 的「高雄」比不過，切成「高 / 雄風 / 力」；詞頻 31 時切成「高雄 / 風力」。jieba README 也寫明「自動計算的詞頻在使用 HMM 新詞發現功能時可能無效」。離線測試另外用極小字典重現一個反例：詞頻太低時，原本靠 HMM 黏對的「高雄」反而被拆開。

## 換成 dict.txt.big 的差異

| 檢查項 | 內建：舊 | 內建：新 | big：舊 | big：新 |
|---|---|---|---|---|
| 前 50 詞中的停用詞／助詞 | 10 | 0 | 14 | 0 |
| 前 50 詞中的純數字或符號 | 12 | 0 | 12 | 0 |
| 「高雄市」整詞次數 | 6 | 6 | 6 | 6 |
| 「高雄」整詞次數 | 0 | 85 | 0 | 99 |
| 單字「高」次數 | 222 | 0 | 172 | 0 |
| 含「高雄」的標題被拆開 | 115 | 0 | 115 | 1 |
| 含「高雄」的標題黏成非字典詞 | 0 | 33 | 0 | 19 |
| 總詞數 | 113,845 | 30,775 | 95,245 | 31,973 |
| 不同詞數 | 6,304 | 10,944 | 9,655 | 10,234 |

- 內建字典是簡體，「新聞」「颱風」不在字典裡，舊流程拆成單字；`dict.txt.big` 有這些詞（「新聞」12,562、「颱風」340），舊流程的單字少很多。
- 內建字典只有簡體「万安」，新流程靠 HMM 黏出「蔣萬安」（117 次）；`dict.txt.big` 有「萬安」，切成「蔣 / 萬安」，「蔣」是單字被過濾，前 50 詞變成「萬安 175」。
- 兩份字典的「高雄市」詞頻都是 31，所以「高雄市」的結果不受主字典影響。

## jieba 實驗重跑（Linux）

repo 版本是 2026-10-29 在 VM 上跑的（`q6/data/jieba_experiments/verify.log`）。本次在 Linux 容器重跑兩次（07:46Z 與 12:32Z），輸出寫在 scratch，與 repo 版本比對：

| 檔案 | repo sha256 前 16 碼 | 重跑 sha256 前 16 碼 | 結果 |
|---|---|---|---|
| results.json（exp1） | `d0b50118846b53cd` | `84d3391a63859179`（07:46Z）、`2568b59a564c162a`（12:32Z） | 只有 8 個計時欄位不同（4 組 `seconds`、`chars_per_sec`），其餘相同 |
| results2.json（exp2） | `12b3fbd79af98f84` | `12b3fbd79af98f84` | 位元組完全相同 |
| results3.json（exp3） | `22a51907c4409926` | `22a51907c4409926` | 位元組完全相同 |

| 量測（dict.txt.big＋HMM，單一行程） | VM 2026-10-29 | Linux 07:46Z | Linux 12:32Z |
|---|---|---|---|
| verify：50,000 篇／1,448,432 字 | 262,611 字/秒 | 297,818 字/秒 | 301,854 字/秒 |
| exp1：100,000 篇／2,902,513 字 | 285,652 字/秒 | 268,104 字/秒 | 290,700 字/秒 |
| 加「肥宅」，total 累加：不含該詞卻改變 | 52 篇 | 52 篇 | 52 篇 |
| 加「肥宅」，total 固定：不含該詞卻改變 | 0 篇 | 0 篇 | 0 篇 |

計時欄位會隨機器與負載變動，其他欄位（語料筆數、收錄判斷、改變篇數、範例）完全一致。答案稿的「每核每秒 28 萬字」落在 262,611–301,854 的範圍內。

## 外部依據（2026-10-30 查證）

文件頁用 `curl -sSL` 抓到 scratch（不進 repo），sha256 是抓取當下的檔案；官網頁面是動態產生，日後重抓雜湊可能不同。

| 主張 | 來源 | sha256 前 16 碼 |
|---|---|---|
| smartcn 針對簡體中文；`smartcn` analyzer、`smartcn_tokenizer`、`smartcn_stop` 都不能設定，沒有自訂詞典；每個節點都要安裝並重啟 | https://www.elastic.co/docs/reference/elasticsearch/plugins/analysis-smartcn | `57535c490a7dda9d` |
| ICU tokenizer 依 UAX #29，中文用字典法切詞；可自訂的只有 RBBI 規則檔，且標為實驗性 | https://www.elastic.co/docs/reference/elasticsearch/plugins/analysis-icu-tokenizer | `0b5492c8d8df32ef` |
| `_reload_search_analyzers` 只重載 search analyzer 裡 `updateable: true` 的同義詞；重載後要清 request cache；同義詞檔要更新到每個資料節點 | https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-indices-reload-search-analyzers | `c35f3ba997f4d222` |
| `updateable` 預設 false，只能用在 search analyzer | https://www.elastic.co/docs/reference/text-analysis/analysis-synonym-graph-tokenfilter | `ddd44b704a7804ac` |
| `analyzer` 不能用 update mapping API 在既有欄位上更新 | https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/analyzer | `5b949c6665891c05` |
| 既有文件不會有新加 multi-field 的值，要用 update by query 補；欄位型別要改只能新索引＋reindex | https://www.elastic.co/docs/manage-data/data-store/mapping/update-mappings-examples | `f341e9619ce6bc74` |
| IK 有 `ik_smart`、`ik_max_word`；`remote_ext_dict`／`remote_ext_stopwords` 回應的 `Last-Modified` 或 `ETag` 變了就重新載入，不必重啟 ES | https://raw.githubusercontent.com/infinilabs/analysis-ik/master/README.md | `a149a73e3f7173b9` |
| IK 每個遠端字典一個排程：初始延遲 10 秒、每 60 秒送一次 HEAD（帶 `If-Modified-Since`／`If-None-Match`），有變就 `reLoadMainDict()` | https://raw.githubusercontent.com/infinilabs/analysis-ik/master/core/src/main/java/org/wltea/analyzer/dic/Dictionary.java （第 158、161 行）、同目錄 `Monitor.java`（第 74–92 行） | `b62009eeb9f96da3`、`ddba0028f9f79bef` |
| ES jieba 外掛（sing1ee）預設支援 ES 8.11.1，「支持动态添加字典，不重启 ES」 | https://raw.githubusercontent.com/sing1ee/elasticsearch-jieba-plugin/master/README.md | `3e9f590c8d0b058a` |
| jieba 使用者字典「一个词占一行；每一行分三部分：词语、词频（可省略）、词性（可省略）」；詞頻省略時自動計算；`dict.txt.big` 是「支持繁体分词更好的词典文件」 | https://raw.githubusercontent.com/fxsjy/jieba/master/README.md | `d6754b68da8fae70` |
| （抓了但本檔沒有引用）指定 analyzer 的優先順序 | https://www.elastic.co/docs/manage-data/data-store/text-analysis/specify-an-analyzer | `feced1c5599b52f5` |
| jieba `load_userdict` 只跳過空行（第 408 行），註解行也會被當成詞；`add_word` 直接覆寫 `FREQ[word]` 並把新詞頻加到 `total`（第 428–429 行），舊詞頻不扣；切詞分數用 `log(total)`（第 175 行） | https://raw.githubusercontent.com/fxsjy/jieba/master/jieba/__init__.py （與本機 jieba 0.42.1 的檔案逐位元組相同） | `b47cefcfd0bf683b` |

`q6/stack` 實測（ES 9.5.5 單節點，`q6/stack/results/es/ik_hot_reload.json`）：IK 重載後 `_analyze` 立刻切成新詞，但舊文件的片語搜尋從 22,088 掉到 0；`_update_by_query` 重分析後回到 22,271。已寫入的 tokens 不會跟著字典變。本設計在 ES 外用 jieba 斷詞後寫入 `terms_v2`，字典更新靠 (c) 的定向重跑，不依賴 IK 熱更新。

## 輸出與 log 位置

| 內容 | 位置 |
|---|---|
| 新舊對照（內建字典） | `q6/data/top50.json`、`q6/data/segmented_ab.jsonl`、本檔 `default` 區塊 |
| 新舊對照（dict.txt.big） | `q6/data/top50_dict_big.json`、本檔 `dict_big` 區塊；`segmented_ab_dict_big.jsonl`（1,982,307 bytes）只放 scratch `$W/out/` |
| 「高雄市」證據 | `q6/data/kaohsiung_trace.json`、`q6/data/kaohsiung_trace_dict_big.json` |
| jieba 實驗重跑摘要 | `q6/data/jieba_experiments/rerun_linux.log`（verify 輸出與 compare_results 輸出） |
| 完整 log 與重跑輸出（不進 repo） | `$W/logs/`、`$W/rerun2/logs/`、`$W/out/`、`$W/rerun2/out/` |
| 下載檔（不進 repo） | `$W/dl_dict/`、`$W/dl_corpus/`（第一次）、`$W/dl_dict2/`、`$W/dl_corpus2/`（第二次，雜湊相同）；文件頁 `$W/dl_pages*/` |

## 與既有說法的差異／修正

| 既有說法 | 位置 | 現在 | 證據 |
|---|---|---|---|
| 新流程「『高雄市』整詞保留 6 次」，列為新流程的成果 | `shared/docs/python.md` 六、舊版本檔 | 舊流程也是 6 次，這個指標沒有變化；改善在「高雄」：拆開 115 篇降到 0 篇（內建）／1 篇（dict.txt.big） | 「「高雄市」為什麼是 6→6」一節 |
| 「jieba 內建字典是簡體…正式對照要用同一份繁體字典」「dict.txt.big 本次未下載」 | 舊版本檔、舊版 `PLAN.md` | 已下載（sha256 與 `verify.log` 相同），用 `--base-dict` 在同一份標題上對照 | `dict_big` 區塊 |
| 工作天 26–28、每核 1,000 則/秒（假設）、120 字、276 批 | 舊版 `PLAN.md` | 與答案稿對齊：45 工作天、每核每秒約 28 萬字（實測）、300 字（假設）、270 批（Threads 2023/07 起） | `q6/code/PLAN.md`、`q6/answer/answer_600.txt`、`q6/architecture/src/arch_a1_partition_grid.html` |
| 「外部依據未上網查證」 | 舊版 `PLAN.md`、`shared/docs/python.md` 十之 4 | 已查證，來源見上 | 「外部依據」一節 |
| 舊版 `segmenter` 直接 `load_userdict(檔案路徑)` | 舊版 `q6/code/segmenter.py` | jieba 不跳過 `#` 註解，`# dict_version: …` 和 `# 格式：…` 兩行被當成詞載入。現在先濾掉註解行再載入。這 6,685 篇的切分結果不受影響：重跑後 `segmented_ab.jsonl` 與舊版輸出逐位元組相同 | jieba `__init__.py` 第 408 行；測試 `test_user_dict_comment_lines_are_not_loaded_as_words` |
| 舊版 `run_compare.py` 每次覆寫整份 RESULTS.md | 舊版 `q6/code/run_compare.py` | 只改寫 `AUTO` 標記之間的區塊，手寫段落保留 | 測試 `test_upsert_block_keeps_handwritten_text` |
| pytest 9 passed | `shared/docs/python.md` 六 | 10/30 增加為 23 項，10/31 依審查再加 5 項，共 28 項，全部通過 | 「pytest 結果」一節 |

`shared/docs/python.md` 不在本資料夾，這裡只記錄差異，沒有改它。

獨立複核（2026-10-30）沒有中、高嚴重度問題；以下小問題已修：

| 複核指出 | 修正 |
|---|---|
| 結論把模擬的舊流程講得像公司實際的舊系統 | 結論改成「用 jieba 字典模擬的舊流程」 |
| 自動區塊的前 50 詞次數沒有千分位 | `render_block` 加千分位後重產兩個區塊；只有格式改變，數字相同 |
| 非 default tag 也會覆寫 repo 的 `ptt_titles_7d.jsonl` | 只在 tag=default 時寫語料；加測試 |
| `upsert_block` 遇到只有 BEGIN 沒有 END 時會再附加一個 BEGIN | 標記不成對時直接報錯；加測試 |
| `what_if_add_word` 的兩種計數一個看原文、一個看前處理後的文字 | 都改看前處理後的文字；這份語料沒有只在標籤裡出現「高雄」的標題，重跑後 `kaohsiung_trace*.json` 位元組不變 |
| 「低詞頻反而拆開『高雄』」的測試沒有先確認加詞前是整詞 | 測試先斷言加詞前切出「高雄」 |
| 外部依據少列一份已下載的文件頁；「全部 HTTP 200」沒有紀錄可查 | 補上該頁網址與雜湊；第 14 列改寫成實際能證明的內容 |

## 未完成與限制

- 公司現行舊字典不在 repo，「舊」流程是用 jieba 字典模擬；`dict.txt.big` 比內建字典更接近，但仍不是公司字典。
- 沒有改 `user_dict.txt`。建議下一版字典加「高雄」並把「高雄市」等詞頻調到不低於主字典（例如 31），同時遞增 `dict_version`；本次只用 `kaohsiung_trace.py` 試算。
- 語料只有 6,685 個標題（平均很短），不是貼文內文；`articles.jsonl` 沒有內文。
- 吞吐是單一行程、容器同時有其他工作時量的，只能當量級參考。
- 本檔的 ES 依據是官方文件與原始碼；IK 熱更新與 smartcn／ICU 切詞後來在 `q6/stack` 單節點實測（`q6/stack/results/es/ik_hot_reload.json`、`analyzers.json`），沒有在多節點叢集上測。
- `q6/architecture/` 與 `q6/evidence/` 的圖片沒有重新產生。

## 自動產生的對照

<!-- AUTO:compare:default BEGIN -->
### 對照：主字典 = jieba-builtin（tag `default`，自動產生）

- 語料：6,685 個 PTT 八卦板標題（7 天內，`q3/data/ptt_q3_out/index_entries.csv`）
- 主字典：jieba-builtin；新舊兩邊用同一份
- 使用者字典：`qsearch-q6-2026.10.30.1`（sha256 前 16 碼 `b42e821556616362`），只載入新流程
- 舊：純字典（HMM 關閉、不做前處理、不過濾停用詞）。舊團隊字典檔不在 repo，此為模擬。
- 新：前處理（PTT 標籤／短網址／網址／電郵／電話）→ jieba＋使用者字典＋HMM → 停用詞、長度 ≥ 2、純數字過濾

| 檢查項 | 舊 | 新 |
|---|---|---|
| 前 50 詞中的停用詞／助詞 | 10 | 0 |
| 前 50 詞中的純數字或符號 | 12 | 0 |
| 「高雄市」整詞次數 | 6 | 6 |
| 「高雄」整詞次數 | 0 | 85 |
| 單字「高」次數 | 222 | 0 |
| 含「高雄」的標題：整詞保留 | 10 | 92 |
| 含「高雄」的標題：HMM 黏成非字典詞（如「高雄為」） | 0 | 33 |
| 含「高雄」的標題：被拆開（沒有 token 含「高雄」） | 115 | 0 |
| 總詞數 | 113,845 | 30,775 |
| 不同詞數 | 6,304 | 10,944 |

含「高雄」的標題 125 篇，其中含「高雄市」6 篇。
字典詞頻（jieba `FREQ`；0 代表只是前綴佔位、不算詞）：舊「高雄市」31、「高雄」0；新（載入使用者字典後）「高雄市」5、「高雄」0。

切分樣本（前 3 篇含「高雄市」、前 3 篇只含「高雄」；新流程為過濾後的 tokens）：

| 標題 | 舊 | 新 |
|---|---|---|
| [新聞] 賺到颱風假? 午後變天了 高雄市集棚架掀 | [ / 新 / 聞 / ] / 賺 / 到 / 颱 / 風 / 假 / ? / 午 / 後 / 變 / 天 / 了 / 高雄市 / 集 / 棚架 / 掀 | 風假 / 午後變 / 高雄市 / 棚架 |
| Re: [爆卦] 高雄市明天(23日)停止上班上課 | Re / : / [ / 爆 / 卦 / ] / 高雄市 / 明天 / ( / 23 / 日 / ) / 停止 / 上班 / 上 / 課 | 高雄市 / 明天 / 停止 / 上班 / 上課 |
| Re: [新聞] 安倍夫人5度訪高雄市紅毛港保安堂 綠議 | Re / : / [ / 新 / 聞 / ] / 安倍 / 夫人 / 5 / 度 / 訪 / 高雄市 / 紅 / 毛 / 港 / 保安 / 堂 / 綠 / 議 | 安倍 / 夫人 / 度訪 / 高雄市 / 紅毛港 / 保安 / 綠議 |
| [問卦] 有人知道高雄捷運美麗島站也有鋼琴嗎？ | [ / 問 / 卦 / ] / 有人 / 知道 / 高 / 雄 / 捷 / 運 / 美 / 麗 / 島 / 站 / 也 / 有 / 鋼 / 琴 / 嗎 / ？ | 有人 / 知道 / 高雄捷 / 運美麗島 / 鋼琴 |
| [問卦] 高雄肥橘害死人，沒人負責？ | [ / 問 / 卦 / ] / 高 / 雄 / 肥 / 橘 / 害死 / 人 / ， / 沒 / 人 / 負 / 責 / ？ | 高雄 / 肥橘 / 害死 / 沒人 / 負責 |
| [新聞] 神明「開示」高雄出女市長？比政見還有 | [ / 新 / 聞 / ] / 神明 / 「 / 開 / 示 / 」 / 高 / 雄 / 出 / 女 / 市 / 長 / ？ / 比 / 政 / 見 / 還 / 有 | 神明 / 開示 / 高雄出 / 女市 / 比政見 / 還有 |

前 50 詞（舊 vs 新）：

| 排名 | 舊：詞 | 舊：次數 | 新：詞 | 新：次數 |
|---|---|---|---|---|
| 1 | [ | 6,599 | 什麼 | 418 |
| 2 | ] | 6,599 | 是不是 | 300 |
| 3 | 問 | 4,512 | 台灣 | 298 |
| 4 | 卦 | 4,502 | 怎麼 | 278 |
| 5 | ？ | 2,330 | 沈伯洋 | 250 |
| 6 | 新 | 1,861 | 法國 | 186 |
| 7 | 聞 | 1,813 | 風假 | 164 |
| 8 | : | 1,625 | 可以 | 135 |
| 9 | Re | 1,556 | 日本 | 117 |
| 10 | 的 | 1,189 | 蔣萬安 | 117 |
| 11 | 麼 | 918 | 台北 | 117 |
| 12 | 嗎 | 899 | 洋流 | 115 |
| 13 | ！ | 843 | 明天 | 108 |
| 14 | 國 | 821 | 真的 | 104 |
| 15 | ? | 773 | 到底 | 104 |
| 16 | 被 | 741 | 中國 | 102 |
| 17 | 有 | 728 | 有人 | 100 |
| 18 | 是 | 723 | 八卦 | 98 |
| 19 | 會 | 720 | 烏克蘭 | 95 |
| 20 | 了 | 717 | 現在 | 95 |
| 21 | 「 | 710 | 放假 | 95 |
| 22 | 台 | 708 | 市民 | 89 |
| 23 | 人 | 693 | 如果 | 87 |
| 24 | 」 | 528 | 高雄 | 85 |
| 25 | 風 | 517 | AI | 78 |
| 26 | 不 | 503 | 不是 | 78 |
| 27 | 什 | 487 | 暴動 | 77 |
| 28 | 已 | 484 | 還是 | 70 |
| 29 | ( | 453 | 發票 | 67 |
| 30 | ) | 449 | 自己 | 66 |
| 31 | 沒 | 440 | 台南 | 62 |
| 32 | 在 | 439 | 民調 | 62 |
| 33 | 為 | 429 | 今天 | 61 |
| 34 | 刪 | 428 | 沒有 | 60 |
| 35 | 除 | 428 | 這麼 | 60 |
| 36 | 怎 | 423 | 不會 | 55 |
| 37 | ： | 420 | 美國 | 53 |
| 38 | 本文 | 407 | 為何 | 51 |
| 39 | 要 | 392 | 大家 | 50 |
| 40 | 大 | 390 | 這樣 | 50 |
| 41 | 颱 | 389 | 南部 | 49 |
| 42 | 灣 | 384 | 怎樣 | 48 |
| 43 | 萬 | 376 | 那麼 | 48 |
| 44 | 沈 | 334 | 河口 | 47 |
| 45 | 法 | 314 | 一堆 | 44 |
| 46 | 伯 | 313 | 如何 | 44 |
| 47 | 還 | 312 | 中共 | 44 |
| 48 | 洋 | 310 | 澎湖 | 43 |
| 49 | 都 | 304 | 女生 | 41 |
| 50 | ， | 303 | 股市 | 41 |
<!-- AUTO:compare:default END -->

<!-- AUTO:compare:dict_big BEGIN -->
### 對照：主字典 = dict.txt.big（tag `dict_big`，自動產生）

- 語料：6,685 個 PTT 八卦板標題（7 天內，`q3/data/ptt_q3_out/index_entries.csv`）
- 主字典：dict.txt.big（sha256 前 16 碼 `b16011275c42955c`）；新舊兩邊用同一份
- 使用者字典：`qsearch-q6-2026.10.30.1`（sha256 前 16 碼 `b42e821556616362`），只載入新流程
- 舊：純字典（HMM 關閉、不做前處理、不過濾停用詞）。舊團隊字典檔不在 repo，此為模擬。
- 新：前處理（PTT 標籤／短網址／網址／電郵／電話）→ jieba＋使用者字典＋HMM → 停用詞、長度 ≥ 2、純數字過濾

| 檢查項 | 舊 | 新 |
|---|---|---|
| 前 50 詞中的停用詞／助詞 | 14 | 0 |
| 前 50 詞中的純數字或符號 | 12 | 0 |
| 「高雄市」整詞次數 | 6 | 6 |
| 「高雄」整詞次數 | 0 | 99 |
| 單字「高」次數 | 172 | 0 |
| 含「高雄」的標題：整詞保留 | 10 | 105 |
| 含「高雄」的標題：HMM 黏成非字典詞（如「高雄為」） | 0 | 19 |
| 含「高雄」的標題：被拆開（沒有 token 含「高雄」） | 115 | 1 |
| 總詞數 | 95,245 | 31,973 |
| 不同詞數 | 9,655 | 10,234 |

含「高雄」的標題 125 篇，其中含「高雄市」6 篇。
字典詞頻（jieba `FREQ`；0 代表只是前綴佔位、不算詞）：舊「高雄市」31、「高雄」0；新（載入使用者字典後）「高雄市」5、「高雄」0。

切分樣本（前 3 篇含「高雄市」、前 3 篇只含「高雄」；新流程為過濾後的 tokens）：

| 標題 | 舊 | 新 |
|---|---|---|
| [新聞] 賺到颱風假? 午後變天了 高雄市集棚架掀 | [ / 新聞 / ] / 賺 / 到 / 颱風 / 假 / ? / 午後 / 變天 / 了 / 高雄市 / 集 / 棚架 / 掀 | 颱風 / 午後 / 變天 / 高雄市 / 棚架 |
| Re: [爆卦] 高雄市明天(23日)停止上班上課 | Re / : / [ / 爆 / 卦 / ] / 高雄市 / 明天 / ( / 23 / 日 / ) / 停止 / 上班 / 上課 | 高雄市 / 明天 / 停止 / 上班 / 上課 |
| Re: [新聞] 安倍夫人5度訪高雄市紅毛港保安堂 綠議 | Re / : / [ / 新聞 / ] / 安倍 / 夫人 / 5 / 度 / 訪 / 高雄市 / 紅毛 / 港 / 保安 / 堂 / 綠 / 議 | 安倍 / 夫人 / 度訪 / 高雄市 / 紅毛 / 保安 / 綠議 |
| [問卦] 有人知道高雄捷運美麗島站也有鋼琴嗎？ | [ / 問 / 卦 / ] / 有人 / 知道 / 高 / 雄 / 捷運 / 美麗 / 島 / 站 / 也 / 有 / 鋼琴 / 嗎 / ？ | 有人 / 知道 / 高雄 / 捷運 / 美麗 / 島站 / 鋼琴 |
| [問卦] 高雄肥橘害死人，沒人負責？ | [ / 問 / 卦 / ] / 高 / 雄 / 肥 / 橘 / 害死 / 人 / ， / 沒 / 人 / 負責 / ？ | 高雄 / 肥橘 / 害死 / 負責 |
| [新聞] 神明「開示」高雄出女市長？比政見還有 | [ / 新聞 / ] / 神明 / 「 / 開示 / 」 / 高 / 雄 / 出 / 女 / 市長 / ？ / 比 / 政見 / 還有 | 神明 / 開示 / 高雄出 / 市長 / 政見 / 還有 |

前 50 詞（舊 vs 新）：

| 排名 | 舊：詞 | 舊：次數 | 新：詞 | 新：次數 |
|---|---|---|---|---|
| 1 | [ | 6,599 | 什麼 | 472 |
| 2 | ] | 6,599 | 台灣 | 329 |
| 3 | 卦 | 4,502 | 颱風 | 327 |
| 4 | 問 | 4,461 | 是不是 | 300 |
| 5 | ？ | 2,330 | 沈伯洋 | 264 |
| 6 | 新聞 | 1,805 | 怎麼 | 229 |
| 7 | : | 1,625 | 法國 | 226 |
| 8 | Re | 1,556 | 萬安 | 175 |
| 9 | 的 | 1,174 | 中國 | 161 |
| 10 | 嗎 | 895 | 現在 | 154 |
| 11 | ！ | 843 | 可以 | 135 |
| 12 | ? | 773 | 日本 | 118 |
| 13 | 被 | 737 | 台北 | 118 |
| 14 | 了 | 713 | 民調 | 116 |
| 15 | 「 | 710 | 洋流 | 115 |
| 16 | 台 | 701 | 明天 | 106 |
| 17 | 是 | 626 | 到底 | 104 |
| 18 | 」 | 528 | 烏克蘭 | 103 |
| 19 | 會 | 473 | 高雄 | 99 |
| 20 | 什麼 | 472 | 八卦 | 98 |
| 21 | 人 | 463 | 放假 | 97 |
| 22 | ( | 453 | 真的 | 95 |
| 23 | 已 | 450 | 還是 | 93 |
| 24 | ) | 449 | 暴動 | 90 |
| 25 | 為 | 429 | 沒有 | 89 |
| 26 | 有 | 427 | 市民 | 89 |
| 27 | 刪除 | 426 | 如果 | 87 |
| 28 | ： | 420 | 不會 | 80 |
| 29 | 本文 | 407 | 有沒有 | 80 |
| 30 | 灣 | 375 | AI | 78 |
| 31 | 要 | 361 | 不是 | 78 |
| 32 | 不 | 346 | 有人 | 72 |
| 33 | 沈 | 334 | 發票 | 69 |
| 34 | 颱風 | 327 | 美國 | 66 |
| 35 | 伯 | 313 | 自己 | 65 |
| 36 | 洋 | 304 | 這麼 | 64 |
| 37 | ， | 303 | 台南 | 63 |
| 38 | 都 | 302 | 覺得 | 63 |
| 39 | 是不是 | 300 | 比較 | 62 |
| 40 | 在 | 276 | 怎麼辦 | 61 |
| 41 | 假 | 255 | 今天 | 60 |
| 42 | 大 | 243 | 千萬 | 57 |
| 43 | 蔣 | 234 | 為何 | 53 |
| 44 | 怎麼 | 229 | 開始 | 52 |
| 45 | 法國 | 226 | 這樣 | 52 |
| 46 | 沒 | 214 | 那麼 | 50 |
| 47 | 很 | 202 | 大家 | 50 |
| 48 | 吧 | 193 | 徐佳青 | 49 |
| 49 | 我 | 178 | 南部 | 49 |
| 50 | 萬安 | 175 | 怎樣 | 47 |
<!-- AUTO:compare:dict_big END -->

## 2026-10-31 審查四點的補驗

10/31 審查 Q6 提了四點。下面每點寫做了什麼、數字在哪、還剩什麼沒證明。

### 1. 重要詞召回（`recall_eval.py`，結果 `q6/data/recall_eval/recall.json`）

舊的「高 222→0」只說明單字全被刪，不代表重要詞留住了。這次做金標集：

- 從 6,685 個標題隨機抽 150 個（seed 20261031）。三位標註者各自獨立列出「文字雲該出現的詞」，沒看過任何分詞結果；兩位以上同意才算金標詞，共 430 個，12 個標題沒有金標詞。
- 指標是金標詞有沒有原封不動成為一個 token。被拆開或被過濾都算漏。主字典 `dict.txt.big`。

| 流程 | 召回（全部） | A 半 | B 半 | 每標題不同 token 數 |
|---|---:|---:|---:|---:|
| 舊流程（原文純字典，不過濾） | 64.7% | 64.5% | 64.8% | 13.75 |
| 10/30 新流程（方括號一律刪、單字全刪） | 74.4% | 72.4% | 76.5% | 4.65 |
| 10/31 新流程（白名單標籤、單字保留名單、颱風假、固定 total） | 75.6% | 73.3% | 77.9% | 4.60 |

審查舉的三個例子，10/30 版與 10/31 版的輸出：

| 輸入 | 10/30 | 10/31 |
|---|---|---|
| 颱風假 | 颱風（`dict.txt.big`；內建字典是「風假」） | 颱風假 |
| 癌 | （空） | 癌 |
| [高雄市]今天下雨 | 今天、下雨 | 高雄市、今天、下雨 |

改了什麼：方括號只有 `dict/ptt_tags.txt` 白名單裡的標籤與純英數帳號才去掉，其他文字保留；單字只保留 `dict/keep_single.txt` 列的詞；使用者字典加「颱風假」並升到 `qsearch-q6-2026.10.31.1`。

還沒解決的：剩下約 24% 漏掉的金標詞多半是字典沒有的人名與新詞（蔣萬安、黃暐瀚、敖幼祥、0元購、白黨團），以及標註者當成一個詞、jieba 切成兩個的複合詞（改變世界、護樹團體）。這要靠 (b) 的新詞發現進字典，不是過濾規則能修的。單字保留名單只收了 2 個，金標裡的「跌」「樹」仍會被刪。保留名單與字典改動只參考了審查的例子，沒有看 B 半，所以 B 半是沒看過的對照；A、B 兩半的提升幅度相近。樣本只有 150 個標題，三位標註者是互相獨立的 LLM，不是 PM。

### 2. 三分鐘查詢路徑（`es_terms_scale.py`，結果 `q6/data/es_terms_scale/`）

文字雲的數字定義：ES `terms` 聚合的 `doc_count` 是「含這個詞的篇數」，同一篇出現三次只算一次；`q6/code/run_compare.py` 的 `Counter` 數的是出現次數。正式上線用篇數，PM A/B 頁也用 ES 聚合，兩者口徑一致。

把 41.8 萬篇 PTT 問答用 10/31 流程斷詞，複製成 100 萬與 300 萬篇（換月份與渠道），灌進 ES 9.5.5 單節點 3 分片的 `keyword` 欄位，量 terms 聚合（size 50）的 took 中位數：

| 命中篇數 | 精確聚合 | 抽樣 1% | 抽樣 1% 前 50 與精確重疊 |
|---:|---:|---:|---:|
| 3,000,000 | 317 ms | 14 ms | 49/50 |
| 2,100,259 | 261 ms | 14 ms | 49/50 |
| 782,604 | 124 ms | 12 ms | 47/50 |
| 65,217 | 21 ms | 12 ms | 40/50 |
| 6,061 | 7 ms | 6 ms | 26/50 |

查詢路徑：關鍵字與時間範圍先過濾出命中的文件，再對 `terms_v2`（新）或 `terms_v1`（舊）做 terms 聚合，API 的 `seg` 參數決定用哪個欄位。精確聚合大約每 100 萬篇命中 100 ms，線性外推 5 億篇全命中約 50 秒，在 3 分鐘內，但這是單節點外推，沒有在正式叢集量過。命中超過約 100 萬篇時改用 `random_sampler`，抽樣 1% 在 70 萬篇以上命中時前 50 詞重疊 46–49/50；命中少時抽樣誤差大（6,061 篇只有 26/50），所以小範圍一律精確。`execution_hint: map` 在大命中時慢 10 倍以上，不用。

### 3. 版本：指紋涵蓋所有輸入，內容與字典分開

`dict_hash` 只算使用者字典，改停用詞、主字典或程式都不會變，會出現「版本相同、輸出不同」。現在每筆輸出帶：

- `seg_build`：`<dict_version>@<seg_fingerprint>`。指紋是下列輸入的 sha256：jieba 版本、HMM 設定、主字典、使用者字典、停用詞、標籤白名單、單字保留名單、固定的 total、`segmenter.py` 本身（`segmenter.fingerprint_inputs`）。
- `content_sha`：原文的 sha256，與字典無關。

PLAN 舊稿把 `dict_version` 當 ES external version，這是錯的：內容修正與字典更新是兩條獨立的版本線，混用時內容更新會被字典版本擋掉，字典回滾也寫不回去。實作用的是 `q6/stack/es` 的 `seg_ver`：只能前進的數字，寫入時由 Painless 腳本 `seg_guard`（`q6/stack/seg/qseg/es.py`）比較，舊版不能覆寫新版。測試 `test_fingerprint_covers_stopwords_base_dict_and_code`、`test_content_sha_is_independent_of_dictionary`。

### 4. 增量重跑

`segmenter.BigramIndex` 是記憶體裡的示範，只能給小語料用。5 億篇的做法在 `q6/stack`：ES 的 `content.bigram` 欄位用 `match_phrase` 找出原文含新詞字串的文件，以 PIT 分頁取回後重新斷詞寫回。實測 42.5 萬篇加「肥宅」：含這個字串 22,271 篇，8.6 秒重跑完，不含這個字串的文件 0 篇改變（`q6/stack/RESULTS.md`）。

「只影響含新詞的文件」只在固定 jieba total 時成立：不固定時，20 萬篇有 52 篇不含新詞也變了。現在 `Segmenters.build` 預設固定 total，程式註解也改了。

全量重跑的工時：答案寫「ES 限速每秒 3,000 篇、離峰約 6 晚」，這是假設的寫入上限，沒有在正式叢集驗證。本次單節點灌入 `keyword` 欄位是每秒約 6.4 萬篇，但那是沒有其他負載、文件很小的情況，不能直接當正式叢集的上限。

pytest（2026-10-31，Linux 容器、Python 3.13.17、pytest 9.2.0、jieba 0.42.1）：`PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider q6/code/tests`，28 passed，退出碼 0。`test_segmenter.py` 21 項、`test_kaohsiung_trace.py` 5 項、`test_answer_length.py` 2 項；比 10/30 多的 5 項是答案 600 字內、答案涵蓋審查四點、審查舉的三個例子都留住、指紋涵蓋停用詞與主字典與程式、`content_sha` 不受字典影響。
