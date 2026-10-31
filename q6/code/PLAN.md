# Q6 工時與流程估算（答案稿的展開版）

> 狀態：與 `q6/answer/answer_600.txt` 對齊（2026-10-30）。答案稿是定稿，本檔只補上數字的來源與算法；兩邊數字不同時以答案稿為準，並回頭修本檔。
> 數字分兩類：【實測】來自 `q6/code/RESULTS.md`、`q6/data/jieba_experiments/`、`q6/code/affected_demo.py`；【假設】是估算用參數，POC 後要換成實測值。

## (a) 歷史回朔斷詞的執行策略

**批次單位：渠道 × 月，約 270 批**：5 個渠道 × 46 個月（2023/01–2026/10）＝ 230 批，加上 Threads 自 2023/07 起 40 個月，共 270 批；5 億 ÷ 270 ≈ 平均 185 萬篇。
- 渠道決定清洗規則（PTT 標籤、短網址樣式不同），月份控制批量；失敗只重跑一批。
- 排程由新到舊，先補 PM 最常查的近期資料；大分區再依 `doc_id` 雜湊切 slice 平行跑。
- 新結果寫 `terms_v2`，舊欄位不動；API 以 `seg` 參數讓 PM 並排比對（A/B）。

**每批閘門（任一失敗即停該批）**
1. 筆數守恆：輸入筆數 ＝ 寫回筆數。
2. 洩漏為 0：網址、電話、停用詞不得出現在 `terms_v2`；必不可拆詞（地名等金標）全數通過。
3. ES 無寫入拒絕（429／bulk 錯誤），查詢 p95 不惡化。
4. 各渠道第一個月由 PM 抽查後才放行其餘月份。

**機器時間**

| 項目 | 數值 | 類別 |
|---|---|---|
| 每篇平均字數 | 300 字 | 假設（吞吐實驗用的八卦板問答語料平均 29 字，貼文內文會長很多） |
| jieba（dict.txt.big＋HMM）單核吞吐 | 每秒約 28 萬字（verify 三次 262,611／297,818／301,854 字/秒；exp1 三次 285,652／268,104／290,700 字/秒） | 實測，`q6/data/jieba_experiments/`、`q6/code/RESULTS.md` |
| 全量斷詞 CPU 時間 | 5 億 × 300 字 ÷ 28 萬字/秒 ≈ 535,714 核心秒 ≈ 150 核心小時 | 由上兩列算出；研究頁用 exp1 的 285,652 字/秒，算出約 146 核心小時，量級相同 |
| ES 寫入限速 | 每秒 3,000 篇；ES 部分更新仍會重寫整篇文件 | 假設（限速值），重寫整篇有官方文件佐證 |
| ES 寫入時間 | 5 億 ÷ 3,000 ≈ 166,667 秒 ≈ 46 小時；每晚離峰 8 小時，約 6 晚 | 由上列算出 |

斷詞可以加機器縮短，瓶頸在 ES 寫入限速，所以機器時間約 6 個晚上，並在背景跑。

**工作天（單一資料工程師，合計 45 天）**

| 階段 | 工作內容 | 工作天 |
|---|---|---|
| 基準 | 量現行舊字典的輸出與 ES 查詢基準，先講定驗收標準 | 2 |
| POC | 一個渠道一個月跑完全流程；金標句子集 | 5 |
| 字典整併 | 舊字典清理、與搜尋端共用核心詞庫（依題目提示「同字典約 10 天」） | 10 |
| 工程化 | 共用斷詞函式庫（串流與回朔同一映像）、批次 runner、ES 條件寫入、manifest 與續跑 | 6 |
| 六渠道回朔與驗收 | 6 渠道 × 2 天（含 PM 抽查與修正） | 12 |
| 壓測與切換 | 3 分鐘查詢目標壓測、查詢切換、回滾演練 | 3 |
| 緩衝 | 字典迭代與驗收來回 | 7 |
| **合計** | | **45** |

關鍵路徑是字典迭代與 PM 驗收，機器時間不是瓶頸。

## (b) 字典檔維護

**建議：斷詞與搜尋共用一份「核心詞庫」（版本化），各自只加用途層。**
- 核心詞庫：地名、品牌、人名、常用詞，同一個 `dict_version`（人看的發版號）。
- 文字雲用途層：停用詞、變體合併；粒度用精確切。
- 搜尋用途層：同義詞，只在查詢端套用；粒度可細切。
- 分開維護要多出同步、對帳與兩套回歸，而且點文字雲的詞未必搜得到同一批文章，這就是題目提示多出 20 天以上的來源。

**維護流程**
1. 候選：搜尋熱詞、客戶監測詞、地名庫、每週新詞發現。
2. 審核後發 PR，附正例與反例句子。
3. CI：金標回歸全過；估影響篇數（`affected_doc_ids`，bigram 索引先篩、子字串再驗）。
4. 每週發版：`dict_version` 遞增，記錄 sha256。每篇輸出另帶 `seg_fingerprint`（jieba 版本、主字典、使用者字典、停用詞、標籤與單字保留名單、固定的 total、程式碼的 sha256）與原文的 `content_sha`；ES 文件的 `seg_ver` 是只能前進的數字，用 Painless 腳本 `seg_guard` 擋舊版覆寫（`q6/realenv/seg/qseg/es.py`），不是用 `dict_version` 當 ES external version：內容更新與字典更新是兩條獨立的版本線，混用會讓內容修正被字典版本擋掉。

**實測影響範圍（6,685 篇 PTT 標題，`q6/code/affected_demo.py`）**

| 假設新增的詞 | 含該字元序列的標題 | 比例 |
|---|---|---|
| 麥當勞 | 10 | 0.1% |
| 柯文哲 | 33 | 0.5% |
| 八卦 | 95 | 1.4% |
| 高雄 | 125 | 1.9% |

**實測要注意的字典細節**（`q6/code/RESULTS.md`）
- 使用者字典的詞頻會「覆寫」主字典：`高雄市 5` 把主字典的 31 蓋成 5。使用者字典的詞頻不應低於主字典。
- 主字典不一定比較好：`dict.txt.big` 收錄「萬安」，「蔣萬安」因此被切成「蔣」「萬安」，新流程前 50 詞看不到市長全名；換主字典要搭配人名詞條。
- jieba 的 `load_userdict` 不跳過 `#` 註解行，會把註解整行當成詞載入；`segmenter.load_user_dict` 已先濾掉。

## (c) 串流與回朔品質一致性

1. **同一映像、同一字典版本**：串流與回朔載入同一份斷詞函式庫與字典；每篇記錄 `seg_ver`（字典版本＋程式版本），寫入時版本只能前進。
2. **每日影子比對**：抽樣新文章用回朔 job 重算，結果差異必須為 0。
3. **字典更新不必全量重跑**：停用詞與變體合併放在查詢時處理（ES terms exclude）；新增詞只重跑原文含該字串的文章。
4. **固定 total**：jieba 加詞會改 `total`，所有詞的分數都跟著變。實測 20 萬篇 PTT 問答加 1 個詞，預設累加 total 時有 52 篇不含新詞也改變；固定 total 後為 0 篇（2026-10-29 VM 與 2026-10-30 Linux 兩次重跑相同）。所以發版時固定 total，影響範圍才算得準。
5. **換演算法才全量重跑**：開新欄位（如 `terms_v3`），依 (a) 的批次重跑。

## 外部依據（已查證，2026-10-30）

| 主張 | 來源 |
|---|---|
| smartcn 針對簡體中文，analyzer／tokenizer／stop filter 都不能設定（沒有自訂詞典） | https://www.elastic.co/docs/reference/elasticsearch/plugins/analysis-smartcn |
| ICU tokenizer 依 UAX #29 加字典法切中文；自訂只有 RBBI 規則檔（實驗性） | https://www.elastic.co/docs/reference/elasticsearch/plugins/analysis-icu-tokenizer |
| IK 的 `remote_ext_dict` 可熱更新：看 `Last-Modified`／`ETag`，不必重啟 ES；原始碼每 60 秒送一次 HEAD | https://github.com/infinilabs/analysis-ik （README、`Dictionary.java`、`Monitor.java`） |
| `analyzer` 不能在既有欄位上更新；既有文件不會自動有新 multi-field 的值 | https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/analyzer 、https://www.elastic.co/docs/manage-data/data-store/mapping/update-mappings-examples |
| 只有 search analyzer 裡 `updateable: true` 的同義詞可以 reload | https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-indices-reload-search-analyzers |
| jieba 使用者字典格式「詞 詞頻 詞性」、詞頻可省略；`add_word` 會累加 `total` | https://github.com/fxsjy/jieba （README、`jieba/__init__.py`） |

本設計在 ES 外用 jieba 函式庫斷詞，結果寫入 `terms_v2`，所以不依賴 IK 熱更新；熱更新只影響之後才分析的文字，已寫入的 tokens 不會跟著變（`q6/realenv` 在 ES 9.5.5 單節點實測：IK 重載後舊文件的片語搜尋 22,088→0，`_update_by_query` 重分析後回到 22,271，見 `q6/realenv/results/es/ik_hot_reload.json`）。

## 已知限制

- 公司現行舊字典不在 repo，「舊」流程是用 jieba 字典模擬。
- 語料只有 6,685 個標題；`articles.jsonl` 沒有內文。
- ES 只在 `q6/realenv` 單節點實測（42.5 萬篇 p95、定向重跑 22,271 篇 8.6 秒；terms 聚合到 300 萬篇見 `q6/data/es_terms_scale/`）；正式叢集的寫入限速與離峰時數仍是假設，要先壓測。
- 與舊版本檔的差異見 `q6/code/RESULTS.md` 的「與既有說法的差異／修正」。
