# QSearch 筆試第 6 題：文字雲斷詞 Pipeline 重建

> **題目：** 你加入一間社群輿情分析 SaaS 公司，主力產品是讓企業客戶輸入關鍵字後，即時查詢跨社群平台（Facebook、Instagram、YouTube、論壇、媒體、Threads）的聲量趨勢與文字雲。
> 目前的文字雲功能使用一份維護多年的繁體中文字典檔進行斷詞，但產出品質有嚴重問題：短網址後綴出現在結果中、電話號碼被當成詞彙、中文助詞（的、了、在）未被過濾、地名變體被拆散（例如「高雄市」被拆成「高」「雄」「市」）、搜尋高頻詞反而沒出現在文字雲中。
> 團隊決定重寫斷詞演算法，你被指派負責整個 Data Pipeline 的設計。以下是已知條件：
> - 需處理 6 個渠道、覆蓋 2023 年 1 月至今的所有歷史資料
> - 目前 Elasticsearch 中儲存的社群貼文總量約 5 億筆
> - 新舊斷詞結果需並行提供，讓 PM 可 A/B 比對驗收
> - 產品端的查詢回應時間目標：單次文字雲查詢不超過 3 分鐘（初期可放寬，但正式上線須達標）
> - 新資料需透過 Streaming Pipeline 即時斷詞寫入
> (a) 請設計這個「歷史資料回朔斷詞」的執行策略。你會怎麼拆分批次？以什麼為單位排程（年份？渠道？還是其他維度）？每個批次之間需要什麼樣的品質檢查點？請具體估算：如果團隊只有 1 位資料工程師負責，從 POC 驗證到 6 渠道全部完成上線，你估計需要多少個工作天？請列出你的假設。
> (b) 字典檔品質直接影響斷詞結果。請問你會怎麼設計字典檔的維護流程？如何處理「斷詞用字典」和「搜尋用字典」是否共用的問題？如果走不同字典檔，對系統架構有什麼影響？（提示：原團隊估計走不同字典檔需額外 20+ 工作天，走相同字典檔約 10 天）
> (c) Streaming Pipeline 上線後，如何確保新進資料的斷詞結果與歷史回朔資料的品質一致？如果字典檔日後更新了一個新詞，是否需要重新跑全部歷史資料？你會怎麼設計讓這件事可控？
> *上限 600 字。*

---

## 目前狀態

| 項目 | 位置 | 說明 |
|---|---|---|
| 正式答案（599 字，連空白與換行；(a)(b)(c)） | [`q6/answer/answer_600.txt`](answer/answer_600.txt) | 2026-10-31 依審查四點改寫，字數由 `code/tests/test_answer_length.py` 檢查；研究頁 [`q6/answer/research-page.html`](answer/research-page.html)（下載後用瀏覽器開，答案分頁與本檔相同） |
| 架構圖 11 張 | [`q6/architecture/`](architecture) | 依子題分資料夾，整套系統實跑的 2 張在 `architecture/stack/`；原始檔 `architecture/src/arch_*.html`，用 Playwright 渲染成 PNG |
| 佐證 21 張 | [`q6/evidence/`](evidence) | 官方文件／原始碼／論文用 Playwright 擷取後加黃框；VM 實測做成卡片；原始擷取在 `q6/evidence/src/crops/` |
| PTT 雜訊截圖 6 張 | [`q6/evidence/playwright/`](evidence/playwright) | 01 短網址與地名、02 地名「鼓山區」、03 地名與電話、04 電話（已遮）、05 `tinyurl.com` 短網址與被推文換行切開的網址、06 `bit.ly` 短網址。沒有一張標助詞，也沒有一張證明「高雄市」被拆（jieba 預設字典本來就保留「高雄市」） |
| 斷詞原型 | [`q6/code/`](code) | `segmenter.py`（新舊並行，`Segmenters.build(base_dict=…)` 可換主字典；`run_compare.py`、`kaohsiung_trace.py` 用 `--base-dict`）、`run_compare.py`、`kaohsiung_trace.py`（「高雄市」6→6 的證據：用 jieba 字典模擬的舊流程本來就沒拆）、`affected_demo.py`、`tests/`；結果 [`q6/code/RESULTS.md`](code/RESULTS.md)（jieba 內建字典與繁體 dict.txt.big 各一組對照）、估算 [`q6/code/PLAN.md`](code/PLAN.md) |
| jieba 實驗 | [`q6/code/jieba_experiments/`](code/jieba_experiments) | 吞吐、dict.txt.big 覆蓋、加詞影響範圍、固定 total；輸出與 sha256 在 [`q6/data/jieba_experiments/`](data/jieba_experiments)；2026-10-30 在 Linux 重跑，除計時欄位外與 repo 版本相同（`rerun_linux.log`） |
| 登入帳號重爬 60 張 | [`q6/evidence/meta-login/`](evidence/meta-login) | 2026-11-01 用登入帳號的 Chrome profile 把 Facebook 4、Instagram 8、Threads 3 個目標頁再爬一次：Facebook 4 → 65、Instagram 48 → 1,134、Threads 59 → 588 篇，登入牆 0 次；每頁有頁面與 F12 Network 截圖各 2 張，加上 cookie → API → JSON → 欄位的證據鏈；頭像、通知數、分頁標題已模糊 |
| 整套系統實跑（Docker） | [`q6/stack/`](stack) | 官方 Elasticsearch 9.5.5（smartcn、ICU、IK）、PostgreSQL 16、Pub/Sub 官方模擬器、Kibana、OTel／Prometheus／Grafana 全部在 Docker，零成本；42.5 萬篇 PTT 回補三輪、六渠道串流、PM A/B 文字雲頁。數字在 [`RESULTS.md`](stack/RESULTS.md)，截圖在 [`q6/evidence/stack/`](evidence/stack) |

## 2026-10-31 審查四點的處理

細節與數字在 [`q6/code/RESULTS.md`](code/RESULTS.md) 最後一節「2026-10-31 審查四點的補驗」。

| 審查點 | 做了什麼 | 結果 | 還沒證明的 |
|---|---|---|---|
| 1. 重要詞召回 | 150 個標題的金標集，三組獨立的模型標註（不是人工），兩組以上同意算數（[`data/recall_eval/`](data/recall_eval)）；方括號改白名單、加單字保留名單、字典加「颱風假」 | 金標詞召回：舊流程 64.7%，10/30 版 74.4%，10/31 版 75.6%；審查舉的三個例子都留住 | 剩下的漏詞多是字典沒有的人名與新詞，以及 jieba 切成兩段的複合詞（例如改變世界、護樹團體），要靠新詞發現；樣本只有 150 題 |
| 2. 三分鐘查詢路徑 | 文字雲用 ES terms 聚合，`doc_count` 是篇數；100 萬、300 萬篇實測（[`data/es_terms_scale/`](data/es_terms_scale)） | 300 萬篇全命中精確聚合 317 ms；抽樣 1% 14 ms，前 50 詞重疊 49/50 | 5 億篇是單節點線性外推（約 50 秒），沒在正式叢集量 |
| 3. 版本 | 指紋涵蓋 jieba 版本、主字典、使用者字典、停用詞、標籤與單字名單、固定的 total、程式碼；原文另記 `content_sha`；PLAN 不再用 `dict_version` 當 ES external version | 測試 `test_fingerprint_covers_stopwords_base_dict_and_code` 等 | |
| 4. 增量重跑 | 5 億篇規模用 ES `content.bigram` 的 `match_phrase` 加 PIT（`stack/seg/qseg/rerun.py`）；「只影響含新詞的文件」改成以固定 total 為前提，預設固定 | 42.5 萬篇加「肥宅」定向重跑 22,271 篇 8.6 秒，不含字串的 0 篇變 | 全量重跑「離峰約 6 晚」的寫入上限是假設 |

## 依子題分類的素材

### 總覽
| 圖 | 內容 |
|---|---|
| [`q6/architecture/overview/diagram-overall.png`](architecture/overview/diagram-overall.png) | 四條泳道：字典治理 → (b)、回朔 → (a)、串流 → (c)、儲存與查詢。斷詞是 parser 之後的獨立 Cloud Run 服務，與回補 Job 同一映像 |
| [`q6/evidence/overview/evidence-qsearch-gcp-stack.png`](evidence/overview/evidence-qsearch-gcp-stack.png) | Google Cloud 客戶案例：QSearch 用 BigQuery、Cloud Storage、Pub/Sub |
| [`q6/evidence/overview/evidence-research-page.png`](evidence/overview/evidence-research-page.png) | 研究頁本身的截圖 |

### (a) 回朔策略與工作天
| 圖 | 內容 |
|---|---|
| [`diagram-partition-grid.png`](architecture/a-backfill/diagram-partition-grid.png) | 渠道 × 月 ≈ 270 批，由新到舊 |
| [`diagram-batch-flow-gates.png`](architecture/a-backfill/diagram-batch-flow-gates.png) | 每批流程與閘門 G1–G4 |
| [`diagram-timeline-45d.png`](architecture/a-backfill/diagram-timeline-45d.png) | 45 工作天甘特圖 |
| [`evidence-vm-throughput.png`](evidence/a-backfill/evidence-vm-throughput.png) | VM 實測 jieba 每核每秒約 26–29 萬字 |
| [`evidence-jieba-readme-speed.png`](evidence/a-backfill/evidence-jieba-readme-speed.png)、[`evidence-rocling-runtime.png`](evidence/a-backfill/evidence-rocling-runtime.png) | 官方與論文的速度數字，量級一致 |
| [`evidence-es-update-reindex.png`](evidence/a-backfill/evidence-es-update-reindex.png)、[`evidence-es-source-excludes.png`](evidence/a-backfill/evidence-es-source-excludes.png)、[`evidence-es-update-by-query.png`](evidence/a-backfill/evidence-es-update-by-query.png)、[`evidence-es-tune-indexing.png`](evidence/a-backfill/evidence-es-tune-indexing.png) | ES 部分更新仍重寫整篇、要留 `_source`、限速與大量寫入調校 |
| [`evidence-es-global-ordinals.png`](evidence/a-backfill/evidence-es-global-ordinals.png)、[`evidence-es-random-sampler.png`](evidence/a-backfill/evidence-es-random-sampler.png) | 3 分鐘查詢目標的手段與限制 |

### (b) 字典維護與是否共用
| 圖 | 內容 |
|---|---|
| [`diagram-dictionary-lifecycle.png`](architecture/b-dictionary/diagram-dictionary-lifecycle.png) | 候選 → 審核 PR → CI 回歸與影響估算 → 每週發版 |
| [`diagram-shared-core-overlays.png`](architecture/b-dictionary/diagram-shared-core-overlays.png) | 共用核心詞庫＋搜尋／文字雲用途層；與分開維護的比較 |
| [`evidence-vm-dict-coverage.png`](evidence/b-dictionary/evidence-vm-dict-coverage.png) | dict.txt.big 實測：台積電沒有、高雄市有 |
| [`evidence-jieba-dict-big-userdict.png`](evidence/b-dictionary/evidence-jieba-dict-big-userdict.png) | jieba 繁體字典與自訂詞典格式 |
| [`evidence-ckiptagger-f1-dictionary.png`](evidence/b-dictionary/evidence-ckiptagger-f1-dictionary.png)、[`evidence-ckip-transformers-f1.png`](evidence/b-dictionary/evidence-ckip-transformers-f1.png)、[`evidence-rocling-f1.png`](evidence/b-dictionary/evidence-rocling-f1.png) | CKIP 與 jieba 的 F1、授權 |
| [`evidence-es-reload-search-analyzers.png`](evidence/b-dictionary/evidence-es-reload-search-analyzers.png) | 搜尋端同義詞可熱更新的條件 |

### (c) 串流一致性與字典更新
| 圖 | 內容 |
|---|---|
| [`diagram-same-artifact.png`](architecture/c-consistency/diagram-same-artifact.png) | 批次與串流載入同一映像、同一字典版本；seg_ver 只能前進；每日影子比對 |
| [`diagram-change-tiers.png`](architecture/c-consistency/diagram-change-tiers.png) | 變更分三層：顯示層不重跑、斷詞層只重跑含該字串的文章、演算法層開新欄位 |
| [`diagram-release-sequence.png`](architecture/c-consistency/diagram-release-sequence.png) | 字典發版時序 |
| [`evidence-vm-blast-radius.png`](evidence/c-consistency/evidence-vm-blast-radius.png) | VM 實測：加 1 個詞，不固定 total 時 52 篇不含該詞也變，固定後 0 篇 |
| [`evidence-jieba-add-word-total.png`](evidence/c-consistency/evidence-jieba-add-word-total.png)、[`evidence-jieba-calc-logtotal.png`](evidence/c-consistency/evidence-jieba-calc-logtotal.png) | jieba 原始碼：`add_word` 會改 `total`，分數用 `log(total)` |
| [`evidence-es-terms-exclude.png`](evidence/c-consistency/evidence-es-terms-exclude.png) | 軟停用詞放在查詢時的 terms exclude |

## 整套系統實跑（Docker） — [`q6/stack/`](stack)

把答案的設計整套跑起來：官方 ES 9.5.5＋3 個外掛、PostgreSQL 帳本、Pub/Sub 官方模擬器、斷詞服務與回補 Job（同一個映像）、文字雲 API 與 PM A/B 頁、Kibana、OTel／Prometheus／Grafana。GCP 元件與本機對應元件的對照、怎麼重跑在 [`stack/README.md`](stack/README.md)；全部數字與知識檔逐節對照在 [`stack/RESULTS.md`](stack/RESULTS.md)。

| 圖／截圖 | 內容 |
|---|---|
| [`diagram-stack.png`](architecture/stack/diagram-stack.png) | 每一層用什麼、對應哪個 GCP 元件、實際跑出什麼數字 |
| [`diagram-sandbox-scenarios.png`](architecture/stack/diagram-sandbox-scenarios.png) | 10 種情境：怎麼觸發、系統怎麼反應、實測 |
| [`pm/`](evidence/stack/pm) | PM A/B 文字雲頁：全期、首月抽查、關鍵字、六渠道各一張、點詞看原文 |
| [`kibana/`](evidence/stack/kibana)、[`grafana/`](evidence/stack/grafana)、[`prometheus/`](evidence/stack/prometheus) | 同一篇的 v1／v2 詞表、回補時段的查詢延遲與 ES 寫入、告警規則 |
| [`crawler_monitor_3h_panels.png`](evidence/crawl/grafana/crawler_monitor_3h_panels.png) | 六渠道爬蟲監測看板（2026-11-02 本機實跑，台灣時間 19:00–21:40）：每分鐘請求數依平台、狀態 2xx／4xx／5xx／失敗、API p95 依平台與 Pushgateway；Loki 收每個請求的紀錄，Pushgateway→Prometheus→Grafana 出指標 |
| [`channels/`](evidence/stack/channels) | 五個渠道（FB、IG、YouTube、Threads、媒體）的原始公開頁；PTT 的原文截圖在 [`playwright/`](evidence/playwright) |

幾個關鍵數字：

- 回補：1 個 task 每秒約 2,900 篇；3 個 task 共用全域限速 3,000 時，全期查詢 p95 從 114 ms 升到 192 ms，G3 閘門自動降速 3,000 → 1,500 → 750 後通過。回補期間 3,727 次查詢最慢 396.9 ms，沒有一次超過 180 秒。
- 加詞「肥宅」：42.5 萬篇裡詞表會變 5,253 篇，不含這個字串的 0 篇；ES 定向重跑 22,271 篇 8.6 秒。
- 六渠道 267 篇送出後 1.73 秒全部有新舊詞表；文字雲前 30 詞的雜訊從 21–29 個降到 0 個（FB 剩 1 個）。
- 同樣的前 50 詞，ES 74 ms、PostgreSQL（GIN）1,579 ms，篇數完全一樣。

## 估算已對齊（2026-10-30）

`q6/code/PLAN.md` 改成答案稿的展開版，數字以 `q6/answer/answer_600.txt` 為準：

| | 舊版 PLAN.md | 現在（答案稿＝PLAN.md） |
|---|---|---|
| 工作天 | 26–28 | 45（基準 2、POC 5、字典整併 10、工程化 6、六渠道回朔與驗收 12、壓測切換 3、緩衝 7） |
| 批數 | 276（6 渠道 × 46 個月） | 270（Threads 2023/07 才有資料，只有 40 個月） |
| 每則字數 | 120 字（假設） | 300 字（假設） |
| 斷詞速度 | 每核 1,000 則/秒（假設） | 每核每秒約 28 萬字（實測 262,611–301,854 字/秒），約 150 核心小時 |
| total 風險 | 推測 | 實測：20 萬篇加 1 詞，52 篇不含該詞也變；固定 total 後 0 篇 |
| dict.txt.big | 未下載 | 已下載，並在 6,685 篇標題上對照 |

差異的來源與證據見 `q6/code/RESULTS.md` 的「與既有說法的差異／修正」。

## 斷詞資料與字典 — [`q6/data/`](data)、[`q6/code/dict/`](code/dict)

`q6/data/` 根層的檔案都由斷詞原型產生，語料是 Q3 在 2026/10/26 實抓的八卦板 7 天列表（[`q3/data/ptt_q3_out/index_entries.csv`](../q3/data/ptt_q3_out/index_entries.csv)）裡的 6,685 列標題。Q3 的普查只有時間與計數、沒有內文，所以這裡用標題。

| 檔案 | 由誰產生 | 內容 |
|---|---|---|
| [`ptt_titles_7d.jsonl`](data/ptt_titles_7d.jsonl) | `run_compare.py`（只在預設主字典時寫） | 語料，每行一篇 `doc_id`、`text`，共 6,685 行 |
| [`segmented_ab.jsonl`](data/segmented_ab.jsonl) | `run_compare.py` | 新舊並行輸出：每篇的 `seg_legacy`（舊流程）、`seg_new`（新流程），加上 `dict_version` 與字典 sha256 前 16 碼。這是 10/30 規則的輸出，10/31 改規則後沒有重產 |
| [`top50.json`](data/top50.json) | `run_compare.py` | 新舊各自的前 50 詞與檢查項（前 50 詞裡的停用詞、純數字或符號，「高雄市」「高雄」「高」的次數，總詞數），主字典是 jieba 內建 |
| [`top50_dict_big.json`](data/top50_dict_big.json) | `run_compare.py --base-dict dict.txt.big --tag dict_big` | 同上，主字典換成繁體 `dict.txt.big`。這組的 `segmented_ab` 寫到 repo 外（`--segmented-out`），沒有進 repo |
| [`kaohsiung_trace.json`](data/kaohsiung_trace.json) | `kaohsiung_trace.py` | 「高雄市」新舊都是 6 次的證據：字典詞頻、含「高雄市」與「高雄」的每篇切分，以及加「高雄」這個詞的試算 |
| [`kaohsiung_trace_dict_big.json`](data/kaohsiung_trace_dict_big.json) | `kaohsiung_trace.py --base-dict dict.txt.big --tag dict_big` | 同上，主字典換成 `dict.txt.big` |
| [`jieba_experiments/`](data/jieba_experiments) | `code/jieba_experiments/` 的實驗腳本 | 吞吐、`dict.txt.big` 覆蓋、加詞影響範圍、固定 total 的輸出與 sha256 |

[`q6/code/dict/`](code/dict) 是新流程用的字典，舊流程不載入：

- [`user_dict.txt`](code/dict/user_dict.txt)：17 個縣市名詞條（「台／臺」兩種寫法各算一條；jieba 使用者字典格式「詞 詞頻 詞性」），10/31 加「颱風假」。第一行 `# dict_version: qsearch-q6-2026.10.31.1` 是字典版本；每筆輸出另帶涵蓋所有輸入的 `seg_build` 指紋與原文的 `content_sha`。這份字典沒有收「高雄」，`高雄市 5` 也把主字典的詞頻 31 蓋成 5；兩個問題都只在 `RESULTS.md` 試算，沒有改這個檔。
- [`stopwords.txt`](code/dict/stopwords.txt)：42 個停用詞與助詞，新流程在斷詞後濾掉。
- [`ptt_tags.txt`](code/dict/ptt_tags.txt)：PTT 分類標籤白名單，方括號裡是這些字才去掉，其他方括號文字（例如「[高雄市]」）保留。
- [`keep_single.txt`](code/dict/keep_single.txt)：單字保留名單（癌、稅），其他單字照樣過濾。

`dict.txt.big`（8,583,143 bytes）不進 repo，下載網址與 sha256 在 `q6/code/RESULTS.md`。重產資料（在 repo 根目錄，需要 jieba）：

```bash
python q6/code/run_compare.py          # ptt_titles_7d.jsonl、segmented_ab.jsonl、top50.json，並改寫 RESULTS.md 的 AUTO 區塊
python q6/code/kaohsiung_trace.py      # kaohsiung_trace.json
python q6/code/run_compare.py --base-dict /path/to/dict.txt.big --tag dict_big --segmented-out /tmp/segmented_ab_dict_big.jsonl
python q6/code/kaohsiung_trace.py --base-dict /path/to/dict.txt.big --tag dict_big
python -m pytest -q q6/code/tests      # 28 項，離線，不需要 dict.txt.big
```

## 重新產生圖片

需要 Node 與 Playwright（Chromium），Python 3。

```bash
node architecture/src/render_diagrams.js architecture/src .
```

```bash
node evidence/src/capture_web.js evidence/src/evidence_web.json evidence/src/crops evidence/src/web_meta.json
```

```bash
python3 evidence/src/build_cards.py evidence/src data/jieba_experiments
```

```bash
node evidence/src/render_cards.js evidence/src/cards .
```

以上在 `q6/` 底下執行。`capture_web.js` 會重新連線抓官方頁面（只抓未登入公開頁、每站間隔 ≥ 2 秒）；不重抓時直接用 repo 裡的 `q6/evidence/src/crops/`。
