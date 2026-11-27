# Q6 規模試跑程式

量測單機 Elasticsearch 在 Q6 詞頻聚合情境下的灌入速度、每篇磁碟用量與查詢延遲，用來推算 5 億篇的容量。結果與判讀見 [../../evidence/scale-pilot/](../../evidence/scale-pilot/)。

## 兩個步驟

| 步驟 | 程式 | 輸入 | 輸出 |
|---|---|---|---|
| 1 斷詞 | [build_sources.py](build_sources.py) | `$SCALE_ROOT/data/` 下的四類來源：公開的 Gossiping-QA-Dataset.txt（PTT 問答語料，不進版控）、[ptt_titles_7d.jsonl](../../data/ptt_titles_7d.jsonl)、六渠道公開頁樣本（[../../stack/data/channels/](../../stack/data/channels/)）、歷史爬蟲的 YouTube 樣本 hist_youtube_2026-10.jsonl | `data/sources.pkl`（每則來源的文字、渠道、三組詞的 vocab id）、`data/sources_stats.json` |
| 2 灌入與量測 | [load_pilot.py](load_pilot.py) | `data/sources.pkl` | `out/stage_<目標>.json`、`progress.json` |

斷詞直接用 repo 的 qseg（[../../stack/seg/qseg/](../../stack/seg/qseg/)），先複製到 `$SCALE_ROOT/code/qseg`；字典從 dictsrv（`DICT_BASE_URL`）下載並快取在 `$SCALE_ROOT/dictcache`。三組詞：

- v0：近似 ES standard analyzer（CJK 逐字），用 Python 重現。
- v1：舊字典 `qsearch-q6-2026.10.30.1`（純字典、HMM 關）。
- v2：新字典 `qsearch-q6-2026.10.30.2`（目前的 current）。

## load_pilot.py 每一段做什麼

1. bulk 灌到目標篇數（refresh 關閉、0 replica）。每篇 = 兩則公開來源拼接；渠道比例 PTT 70、FB 10、IG 6、媒體 6、YouTube 5、Threads 3；發佈時間在 2023-01 ~ 2026-10 的 46 個月內均勻抽樣；固定亂數種子。
2. refresh 後量 store.size、docs、segments、heap、fielddata。
3. `force_merge(max_num_segments=1)` 後再量一次磁碟。
4. 4 種時間範圍 × 3 種聚合（精確、random_sampler 1%、0.1%），冷熱各 5 次取中位數，並算前 50 詞與精確結果的重疊。

## 安全閘

- C 槽（或 `DISK_GUARD_PATH`）剩餘小於 `STOP_GIB` 就停；每 10 萬篇、每段開始前、force_merge 前都檢查。
- 在 `$SCALE_ROOT` 放一個 `STOP` 檔，會在下一個檢查點停下。
- 只寫 `PILOT_INDEX`（預設 `scale_pilot_main`），不碰正式的 posts 索引，也不寫 repo。

## 執行

```bash
SCALE_ROOT=./scale500m python build_sources.py
```

```bash
SCALE_ROOT=./scale500m ES_URL=http://127.0.0.1:9200 TARGETS=1000000,5000000 python load_pilot.py
```

| 環境變數 | 用途 | 預設 |
|---|---|---|
| `SCALE_ROOT` | 資料、字典快取、輸出的根目錄 | `./scale500m` |
| `ES_URL` | Elasticsearch | `http://127.0.0.1:9200` |
| `PILOT_INDEX` | 試跑索引名稱 | `scale_pilot_main` |
| `TARGETS` | 每段目標篇數（逗號分隔） | `1000000,5000000,20000000,50000000` |
| `BATCH` | bulk 每批篇數 | `5000` |
| `STOP_GIB` | 磁碟剩餘下限（GiB） | `130` |
| `DISK_GUARD_PATH` | 檢查剩餘空間的路徑 | `$SCALE_ROOT` |
| `DICT_BASE_URL`、`DICT_CACHE` | 字典服務與快取 | `http://127.0.0.1:8081`、`$SCALE_ROOT/dictcache` |

索引 mapping 使用 IK 分詞器（`ik_max_word`），ES 需要先裝 analysis-ik 外掛。
