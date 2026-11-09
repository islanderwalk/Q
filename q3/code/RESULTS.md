# Q3 驗證結果（2026-10-30，Linux；2026-11-03 補 Windows 實際連線與一個完整日實測）

## 2026-11-03 Windows 重跑

環境：Windows 11 Pro、繁體中文介面（主控台預設 cp950）、Python 3.10.8、requests 2.32.5、beautifulsoup4 4.14.2、pytest 9.2.0。輸出寫到暫存資料夾，repo 內的資料沒動。

| 指令 | 退出碼 | 結果 |
|---|---|---|
| `pytest -q -p no:cacheprovider q3/code/tests` | 0 | 修正前 21 passed；修正後加 2 項，23 passed |
| `ptt_q3_recheck.py --out <tmp>/r.json`（修正前） | 1 | JSON 已寫完且與 `q3/data/runs/2026-10-30_recheck.json` 逐位元組相同，但最後 `print` 報告時遇到 `≥` 觸發 `UnicodeEncodeError: 'cp950'` |
| `ptt_q3_census.py --report --out <tmp>`（修正前） | 1 | `result_A.txt`、`result_B.txt` 已寫完且與 repo 相同，印 `≈` 時同樣中斷 |
| 上面兩個指令（修正後） | 0、0 | 輸出與 repo 逐位元組相同 |
| `ptt_measure.py --pages 2 --articles 3 --delay 5 --max-requests 6`，對 www.ptt.cc 實際連線 | 0 | 6 個請求，全部 200，最短間隔 5.34 秒；列表 28 篇（20:27–20:56）、抽 3 篇 |
| `ptt_q3_measure.py --days 1 --per-day 1 --delay 5 --max-requests 4`，實際連線 | 0 | 4 個列表頁，全部 200，碰到上限停下（`exit: capped`）；視窗內 69 篇，完整頁 3 頁都是 20 格，時間戳倒序 0 對 |
| `ptt_q3_census.py --index <上一步的 index_entries.csv> --window-end 2026-11-03T20:50 --days 0.02 --delay 5 --max-requests 5`，實際連線 | 0 | 目標 26 篇，抓 5 篇後碰到上限停下，全部 200，`run_summary.jsonl` 記 5 個請求，重跑會續抓 |

修正內容：四支程式在 `main()` 開頭把 `sys.stdout` 改成 UTF-8。繁中 Windows 把輸出導向檔案或管線時預設用 cp950 編碼，報告裡的 `≥`、`≈` 編不出來；改成 UTF-8 後在一般主控台照常顯示，導向檔案則與 Linux 版的 log 同為 UTF-8。新增的測試用 `PYTHONIOENCODING=cp950` 跑兩支報告程式，確認退出碼為 0。

這三次實際連線合計 15 個請求，只驗證程式在線上頁面上能跑完、上限與續抓正確，不是規模量測，數字不取代 README 的 6 個完整日平均。

## 2026-11-03 一個完整日實測（11/02 全日普查）

同一台 Windows，對 www.ptt.cc 實際連線，間隔 5 秒（比預設 1.5 秒保守，因為同一時段另有一支歷史爬蟲在讀其他看板）。輸出寫到暫存資料夾，repo 內的資料沒動。

| 步驟 | 指令 | 時間（台灣） | 請求 | 結果 |
|---|---|---|---|---|
| 列表 | `ptt_q3_measure.py --days 2 --per-day 0 --delay 5 --max-requests 200` | 21:03:02–21:11:39 | 96 | 全部 200，最短間隔 5.317 秒；`exit: ok`，沒碰到上限 |
| 普查 | `ptt_q3_census.py --window-end 2026-11-03T00:00 --days 1 --delay 5 --max-requests 1500` | 21:11:39–22:32:41（4,860 秒） | 889 | 883 篇全部 200；另 6 個請求是 Cloudflare 520（3 次）、525（3 次），退避 5 秒後重試成功；0 篇 404、0 篇失敗；最短間隔 5.296 秒 |

兩步合計 985 個請求，沒有 429、403 或逾時。

| 項目 | 11/02 實測 | README（6 個完整日／7 天普查） |
|---|---|---|
| 一天的文章數 | 883 篇 | 平均 890 篇，範圍 744–1,109 |
| 每小時發文 | 尖峰 11 時 68 篇、離峰 04 時 2 篇；最忙的連續 6 小時 324 篇 | — |
| 列表不變量 | 完整列表頁 95 頁全部 20 格；列表順序與文章 ID 時間戳不一致的相鄰對 0 | 334/334 頁 20 格、倒序 0 |
| 已刪除（列表上無連結） | 約 87 篇／1,795 篇（4.6%，2 天列表） | — |
| 留言 | 35,928 則（推 15,097／噓 2,671／→ 18,160），每篇平均 40.7、中位數 15、P95 187、最大 934；0 則留言的文章 21 篇（2.4%） | 7 天 234,577 則，每篇最終 37.7 則 |
| 文字量 | 內文 1.0 MB＋留言 2.5 MB＝純文字 3.5 MB（留言占 71.8%）；原始 HTML 15.5 MB，每篇 17.1 KB | 7 天純文字 22.9 MB（約每天 3.3 MB） |
| 留言欄位 | 內容以外的欄位每則 36.0 B | 估算時假設 40 B |
| 文章回應時間 | 平均 0.473 秒、中位數 0.326 秒、P95 1.019 秒、最大 6.422 秒（6 篇 6.0–6.4 秒，即重試的那 6 篇） | P95 0.45 秒 |

- 一天 883 篇落在 README 的 744–1,109 範圍內，與平均 890 篇差 0.8%；不變量在新抓的 95 頁上同樣成立。
- 留言每篇 40.7 則比 7 天普查的 37.7 則多 8%，只是一天的數字，不改 README 的平均。這批文章在抓取時只發出 0.9–1.9 天，之後還會再長一些留言。
- 回應時間 P95 1.02 秒高於 README 的 0.45 秒；同一時段另一支爬蟲也遇到 520／525，判斷是當時 Cloudflare 那一端較慢，不是請求太密。
- 報告的「留言抵達時間」與「列表推文數 vs 推−噓」兩段沒有輸出：這兩段只統計抓取時已發文 3 天以上的文章，視窗是前一天，所以沒有符合的文章。要量這兩項，視窗要選 3 天以前。

## 結論

- **（2026-11-03 已補：一個完整日 883 篇、985 個請求，三方對照見上方「一個完整日實測」；以下是 10/30 那輪的結論。）這次沒有對 www.ptt.cc 跑實際抓取。** `ptt_q3_measure.py` 與 `ptt_q3_census.py` 的實測都沒跑，所以「實測數字、普查、README 三方對照」沒有做成。原因是 PTT 的 `robots.txt` 只對幾個 AI 爬蟲的 user agent 寫 `Disallow: /`，這輪驗證環境屬於其中之一。我只抓了 `robots.txt` 本身，共 3 次（07:38:38Z、07:47:56Z、12:25:56Z，內容相同），沒有抓任何列表頁或文章頁。
- 用 repo 內已抓的資料離線重算，README 的規模數字都重現：每天 890 篇（6 個完整日 744–1,109 篇）、334/334 頁剛好 20 格、時間戳倒序 0 筆、留言 234,577 則、規格 1 每天 960 次、規格 2 每天 5,340 次（5.56 倍，README 寫 5.6 倍）。
- README 第 5 小題有一句要改，已經改了。原本寫「6,237 次請求 0 失敗、P95 0.46 秒」，但 6,237 是文章篇數：普查只記每篇最後的狀態，請求數只能推得至少 6,237、最多約 6,247 次；圖表上的「0」也是寫死在 `make_charts.py` 裡的。現在 README 寫成「6,237 篇最後狀態都是 200／404（0 篇失敗）」，圖表與 `result_B.txt` 也改用「篇」，三者都註明約 10 篇推測是第一次請求失敗、退避 5 秒後重試成功；README 的 P95 改成和圖表一致的 0.45 秒。
- 先前 1.2 小時視窗算出的「1,781 篇/天、74,822 則留言」不能外推。同一個算法套在 7 天資料的每個 1.2 小時視窗（857 個，其中 13 個沒有文章），估出的日均從 0 到 2,623 篇都有，只有 11.4% 落在 890 的 ±10% 內。留言少只有一部分是因為文章太新：文章才發 0–72 分鐘時，每篇平均看得到約 18 則（普查最終 37.7 則），而那次的結果是每篇 6.0 則；剩下的落差沒有 Windows 那次的 log 無法歸因。
- 三支抓取程式的改動：預設間隔都改成 1.5 秒；可用 `--max-requests` 設請求上限；每次執行都在輸出資料夾的 `run_summary.jsonl`（`ptt_measure.py` 是 `<CSV 檔名>_summary.jsonl`）加一行請求紀錄，按 Ctrl-C、出錯、碰到上限、連續失敗停下時也會寫，續抓後的總請求數是各行相加。`ptt_q3_measure.py` 不給 `--out` 會停下，不會覆蓋 repo 內的列表；`ptt_q3_census.py` 換了視窗或列表卻沒給 `--out`，或列表沒涵蓋整個視窗時，也會停下。
- pytest 20 passed，退出碼 0；在沒有網路的 network namespace 裡也是 20 passed。10/31 加 `test_q3_answer_length.py` 1 項後是 21 passed，退出碼 0。

## 環境

| 項目 | 版本 |
|---|---|
| OS | Ubuntu 24.04.5 LTS（Linux 6.18.44） |
| Python | 3.13.17（`<tmp>/venv`） |
| requests | 2.34.2 |
| beautifulsoup4 | 4.15.0（解析用 `html.parser`） |
| pytest | 9.2.0 |
| Playwright（Python） | 1.64.1，瀏覽器 Chrome for Testing 153.0.8010.12（`/opt/pw-browsers/chromium`） |
| Pillow | 13.0.0（只給 scratch 的 PNG 比對用） |
| 中文字型 | WenQuanYi Zen Hei |
| 其他 | `unshare`（util-linux 2.39.3）用來在沒有網路的環境跑；GNU `timeout` 9.4 用來送 SIGINT（等同 Ctrl-C） |

所有 Python 都以 `PYTHONDONTWRITEBYTECODE=1` 執行，pytest 加 `-p no:cacheprovider`。離線重播用的是 `python -I`，`-I` 會忽略這個環境變數，所以重播時在 `q3/code/` 產生了 `__pycache__`（`.gitignore` 本來就排除）；已刪掉，重播腳本也改成不寫 bytecode。最後 repo 內沒有 `__pycache__`／`.pytest_cache`。

## 指令與退出碼

在 repo 根目錄執行，`$W` 是 `<tmp>/work/q3/fix_1730`（本輪，17:22–17:45Z）。標「前一輪」的是 07:38–12:59Z 跑的。

| 指令 | 退出碼 | 結果 |
|---|---|---|
| `curl -sS -o robots.txt https://www.ptt.cc/robots.txt`（前一輪，共 3 次） | 第 3 次 0；前 2 次沒有留下退出碼 | 3 個檔案都是 3,259 bytes，sha256 都是 `0563ce67…f2ea36` |
| `ptt_q3_measure.py`、`ptt_q3_census.py` 對 www.ptt.cc 實跑 | 未執行 | robots.txt 對執行環境的 user agent 是 `Disallow: /` |
| `python q3/code/ptt_q3_census.py --report --out $W/census_report`（先複製 `articles.jsonl`） | 0 | 0.5 秒；`result_A.txt` 與 repo 逐位元組相同；`result_B.txt` 只有第 7 行改成推測語氣，複製回 repo |
| `python q3/code/ptt_q3_recheck.py --out $W/recheck.json` | 0 | 1.0 秒；與 `q3/data/runs/2026-10-30_recheck.json` 逐位元組相同 |
| `python q3/answer/src/make_charts.py`（repo 內跑一次，scratch 複本再跑一次） | 0、0 | repo 內只有 `chart-crawl-stats.html` 變了；複本與 repo 的 10 個輸出 sha256 全部相同 |
| Playwright 把 `chart-crawl-stats.html` 轉成 PNG（設定同 `capture.js charts`） | 0 | 1440×548；和改之前只差副標那一行（1.26% 像素），複製回 repo |
| Playwright 把其他 9 張轉成 PNG 比對（前一輪） | 0 | 9/9 雜湊不同，原因是字型渲染；HTML 沒變，repo 內這 9 張沒動 |
| 離線重播 13 次（見下表） | 0、1 或 130 | 都和預期相同 |
| `python q3/code/ptt_q3_census.py --window-end 2026-10-30T00:00 --days 1`（沒給 `--out`） | 1 | 防呆停下，沒有寫入 `q3/data/ptt_q3_census_out` |
| `python q3/code/ptt_q3_measure.py --days 1`（沒給 `--out`） | 1 | 防呆停下，沒有寫入 `q3/data/ptt_q3_out` |
| 四支程式的 `--help` | 0 | 新參數都有列出，三支抓取程式的預設間隔顯示 1.5 |

「離線重播」是把程式的 `requests.Session` 換成讀 `shared/capture/html` 的 stub，用系統時鐘在 `unshare -n`（沒有網路）裡跑 `main()`，只驗證請求計數、上限、間隔與紀錄，不是 PTT 的實測。stub 沒有回應時間，所以量到的間隔就是 `--delay`。

| 程式與參數 | 退出碼 | 請求 | 最短間隔 | 結果 |
|---|---|---|---|---|
| `ptt_measure.py --pages 2 --articles 3 --delay 1.5 --max-requests 4` | 0 | 4 | 1.518 秒 | 碰到上限停下 |
| `ptt_measure.py --pages 1 --articles 1`（預設間隔） | 0 | 2 | 1.610 秒 | 正常結束 |
| `ptt_q3_measure.py --days 2 --per-day 0 --delay 1.5 --max-requests 5` | 0 | 5（全是列表） | 1.515 秒 | 碰到上限停下 |
| `ptt_q3_measure.py --days 2 --per-day 0 --max-requests 3`（預設間隔） | 0 | 3 | 1.516 秒 | 碰到上限停下 |
| `ptt_q3_measure.py --days 2 --per-day 0 --max-requests 0` | 1 | 0 | — | 印「已達請求上限 0，第一個列表頁沒有送出」，仍寫了 1 行紀錄 |
| `ptt_q3_census.py`（視窗 10/29 22:36–10/30 01:00，22 篇）先加 `--max-requests 6`，再不設上限重跑 | 0、0 | 6＋16＝22 | 1.558、1.556 秒 | `run_summary.jsonl` 有 2 行，第 2 次印「累計 2 次執行、22 個請求」 |
| `ptt_q3_census.py`，視窗 10/29 02:00–10/30 02:00，列表最舊只到 10/29 22:33 | 1 | 0 | — | 列表沒涵蓋視窗，不抓，也沒建輸出資料夾 |
| 三支程式各跑 4 秒後送 SIGINT | 130 | 各 3 | 1.516–1.579 秒 | 3 份紀錄的 `exit` 都是 `interrupted` |
| 「未完成與限制」裡的 3 個補跑指令照抄（`--window-end` 當時展開成 `2026-10-31T00:00`） | 0、0、0 | 120＋15＝135 | 1.513、1.554 秒 | 列表碰到 120 的上限（假頁面永遠翻不到 2 天前）；普查先提醒「視窗尾端可能沒涵蓋」（假頁面最新只到 10/30 01:29），再抓完 15 篇；加總指令印出 135 |

## pytest 結果

```
PYTHONDONTWRITEBYTECODE=1 <tmp>/venv/bin/python -m pytest -q -p no:cacheprovider q3/code/tests
20 passed in 2.34s   退出碼 0
```

同一個指令加 `unshare -n` 在沒有網路的環境跑：20 passed，退出碼 0。

| 測試 | 驗證什麼 |
|---|---|
| 解析 4 個 | 滿頁 20 格、最新頁置底線以上 15 篇、文章推／噓／→ 108／7／88 共 203 則、Playwright 快照 10 頁都是 20 格且時間戳不倒退 |
| 請求計數、上限與紀錄 7 個 | 重試也算請求、第 `max_requests + 1` 個不送、不設上限時行為不變、預設間隔 1.5 秒、輸出寫到 `--out`／`--out-dir`、輸出檔不含 cookie；`ptt_q3_measure.py` 不給 `--out` 會停下且不動 repo 內的列表；`--max-requests 0`、連不到列表頁、Ctrl-C 都會寫紀錄，累計數正確 |
| 普查 5 個 | 視窗是〔結束時間 − days，結束時間）；碰到上限後重跑只補抓剩下的，兩次的紀錄都在，相加等於實際請求數；Ctrl-C 與連續 30 篇失敗停下時都有紀錄（沒寫進 JSONL 的那篇也算到）；列表沒涵蓋視窗或缺頁時不抓；換視窗沒給 `--out` 會停下且不動 repo 內的檔案 |
| 重產 2 個 | `--report` 重產的 `result_A.txt`、`result_B.txt`，以及 `make_charts.py` 的 9 個 HTML 與 `ptt_targets.json`，都與 repo 逐位元組相同；重試只寫成推測 |
| 重算 1 個 | `ptt_q3_recheck.py` 算出 README 的 890、744–1,109、334/334、0 倒序、234,577、960／5,340，沒有文章的視窗算 0 篇/天 |
| 其他 1 個 | 不到 24 小時的視窗會加上「不可外推」的提示 |

## 輸出與 log 位置

| 位置 | 內容 |
|---|---|
| `q3/data/runs/2026-10-30_checks.txt` | 驗證摘要：3 次 robots.txt、報告重產、圖表雜湊、PNG、離線重播、防呆 |
| `q3/data/runs/2026-10-30_recheck.json` | `ptt_q3_recheck.py` 的輸出：列表不變量、每日發文量、普查請求數的推估、短視窗分布、新文章看得到的留言、兩條規格的請求數 |
| `<tmp>/work/q3/robots/`、`robots_recheck/`、`robots_2/` | 3 次抓的 robots.txt（未進 repo） |
| `<tmp>/work/q3/fix_1730/` | 本輪的 pytest log、報告重產、圖表與 PNG、離線重播的輸出與紀錄（未進 repo） |
| `<tmp>/work/q3/run_1230/`、`charts2/`、`review_fix/` | 前一輪的輸出，以及 9 張 PNG 的比對（未進 repo） |

## 與既有說法的差異／修正

| 既有說法 | 現在 | 依據 |
|---|---|---|
| README 第 5 小題與 `chart-crawl-stats.png`：「6,237 次請求 0 失敗、P95 0.46 秒」；前一版 RESULTS 說這句「不算錯，README 未改」，結論還寫「README 沒有需要修正的數字」 | 已改。6,237 是篇數，不是請求數，圖表的 0 原本寫死在 `make_charts.py`。README 改成「6,237 篇最後狀態都是 200／404（0 篇失敗），另有約 10 篇推測是第一次請求失敗、退避 5 秒後重試成功；回應時間 P95 0.45 秒」。圖表的 0 改由資料算出、單位改成「篇」，副標改成「中途若有失敗也由退避重試補回」，PNG 重轉。`result_B.txt` 的「請求 6,237 次」改成「文章 6,237 篇（最後狀態）」，重試那行改成「可能是…（若是，每篇多送 1 個請求）」 | 10 篇的回應時間是 5.593–7.186 秒，其他每篇最多 2.423 秒；這 10 篇集中在 10/27 16:39–17:48 與 21:08–21:20（21:08 就有 3 篇），符合「失敗後退避 5 秒再重試」，但也可能只是單次很慢 |
| README「P95 0.46 秒」與圖表「0.45 秒」；前一版 RESULTS 說兩者都是 0.455 四捨五入 | 精確值是 0.45499999999999996（`statistics.quantiles`），四捨五入到兩位是 0.45。0.46 是 `result_B.txt` 先印成 0.455、再四捨五入一次得到的。README 改成 0.45 秒 | `result_B.txt`、`make_charts.py` |
| `shared/docs/python.md`：確切請求數沒有寫進 log；前一版改成每次覆蓋的 `run_summary.json` | 三支程式每次執行都在 `run_summary.jsonl`（`ptt_measure.py` 是 `<CSV 檔名>_summary.jsonl`）加一行，寫在 `finally` 裡，所以 Ctrl-C、出錯、碰到上限、連續失敗停下時都有紀錄。續抓幾次就有幾行，總請求數是各行 `requests` 相加，程式結束時也會印「累計 N 次執行、M 個請求」 | 離線重播、pytest |
| 前一版 RESULTS：普查「實際送出的請求是 6,237–6,247 次」 | 紀錄推得至少 6,237、最多約 6,247 次：每篇至少 1 次；回應時間 ≥5 秒的 10 篇若各重試 1 次就是 6,247 次；普查分 2 段跑，第 1 段中斷時若有正在送的請求，那個請求沒有紀錄，所以 6,247 不是硬上限。10/26 的 `ptt_q3_measure.py` 有記錄：545 次（列表 335、文章 210）。Windows 那次的 log 不在 repo，無法重算 | `2026-10-30_recheck.json` 的 `http_requests_range`、`q3/data/ptt_q3_result.txt` |
| 前一版 RESULTS 與 checks：robots.txt 抓了 2 次（07:47Z、12:25Z） | 抓了 3 次：07:38:38Z、07:47:56Z、12:25:56Z，3 個檔案 sha256 相同 | scratch 裡 3 個檔案的修改時間與 sha256 |
| 前一版 RESULTS：1.2 小時視窗 844 個，最小 22、P5 121、中位數 1,043、P95 1,637，11.6% 落在 ±10% | `ptt_q3_recheck.py` 原本略過沒有文章的視窗。6 個完整日裡可取 857 個，其中 13 個沒有文章（10/21 03:18 起 98.5 分鐘、10/25 04:56 起 174.2 分鐘都沒有新文），`ptt_q3_measure.py` 在這種視窗會印 0 篇/天。算進去後是最小 0、P5 103、中位數 1,027、P95 1,634，11.4%；1,781 篇/天 落在最高的 2.9%（原本 3.0%）。不能外推的結論更明確 | `2026-10-30_recheck.json` 的 `short_windows` |
| 前一版 RESULTS：留言因為文章才發 0–72 分鐘，被低估到每篇約 6.0 則 | 文章太新只解釋得了一部分：每篇從 37.7 則降到約 18 則（年齡在 0–72 分鐘平均分布時，每分鐘取一點平均是 17.9 則，每 4 分鐘取一點是 17.8 則）。6.0 則和 18 則之間的落差無法歸因，見下一節 | `young_articles` |
| 前一版 RESULTS：`ptt_q3_result.txt` 把首抓也算進規格 2（895 × 7 = 6,262 次） | 895 × 7 = 6,265；6,262 是 894.6 × 7。`ptt_q3_measure.py` 用沒取整的 894.6 篇/天 計算，印出時才顯示 895。README 的 5.6 倍不變：首抓算在規格 1、規格 2 只算第 1–6 天的重抓；原始輸出把首抓也算進規格 2，所以是 6.6 倍；兩種算法的每日合計都約 6,300 次 | `spec_cost` |
| 前一版 RESULTS：三支程式預設 0.5／0.8／0.8 秒，實際約每 1.1 秒一次，為了不改預設行為沒調整 | 「約 1.1 秒」只適用於 0.8 秒的兩支（10/26 量測 545 次花 9.8 分鐘，普查平均每 1.11 秒一篇）；`ptt_measure.py` 是 0.5 秒，加上 10/26 量到的平均回應時間 0.256 秒，約每 0.76 秒一次。現在三支的預設都改成 1.5 秒，這是預設行為的改變；對 PTT 實跑約每 1.8 秒一次 | 程式、`--help`、離線重播 |
| 前一版 RESULTS：`ptt_q3_measure.py` 不加 `--out` 會覆蓋 repo 內的列表，沒改 | 現在不給 `--out` 就停下（退出碼 1）；確定要覆蓋得另外加 `--overwrite-repo-data` | 防呆指令、pytest |
| `shared/docs/python.md`：`ptt_q3_census.py` 未實跑，會寫進 repo 並可能覆蓋既有結果 | `--report` 在 scratch 複本上重產，`result_A.txt` 相同，`result_B.txt` 只差上面那行措辭。抓取仍未實跑（robots.txt）。換視窗或列表卻沒給 `--out`，或列表沒涵蓋整個視窗（最舊一篇不早於視窗起點，或列表頁碼有缺）時，會直接停下 | 指令表、pytest |
| `shared/docs/python.md`：1.2 小時視窗得到 1,781 篇/天、74,822 則留言 | 不能外推，見下一節；README 的 890 篇/天 以 7 天列表與普查為準 | `short_windows`、`young_articles` |

### 為什麼 1.2 小時視窗不能外推

`ptt_q3_measure.py` 的日均算法是「視窗內篇數 ÷ 視窗涵蓋的時間」，視窗內沒有文章時印 0。把它套在 10/20–10/25 這 6 個完整日裡，每 10 分鐘取一個結束時間：

| 視窗長度 | 視窗數 | 沒有文章的視窗 | 最小 | P5 | 中位數 | P95 | 最大 | 落在 890 ±10% |
|---|---|---|---|---|---|---|---|---|
| 1.2 小時 | 857 | 13 | 0 | 103 | 1,027 | 1,634 | 2,623 | 11.4% |
| 6 小時 | 829 | 0 | 112 | 194 | 1,019 | 1,500 | 1,751 | 10.0% |
| 24 小時 | 721 | 0 | 684 | 703 | 896 | 1,116 | 1,135 | 59.6% |

- 發文量跟時段強烈相關：1.2 小時視窗的結束時間落在 04 點那一小時，估出的日均中位數只有 140 篇；落在 13 點是 1,466 篇。1,781 篇/天 落在最高的 2.9%，這種視窗都在 10–13 點或 19–20 點結束。
- 留言數：文章發出 0 分鐘時平均看得到 1.9 則、36 分鐘 19.6 則、72 分鐘 24.7 則，最終是 37.7 則；文章年齡在 0–72 分鐘平均分布時是 17.9 則。所以文章太新會把每篇留言從 37.7 則壓到約 18 則。那次的結果是 74,822 ÷（1,781 × 7），約每篇 6.0 則，只有 18 則的三分之一。這段落差沒有 Windows 那次的 log 無法歸因，因為抽了幾篇、在什麼時段跑都不知道。時段確實有影響（凌晨 02、04 點發的文章 36 分鐘時只有 8.3、9.0 則），但估得出 1,781 篇/天 的 25 個視窗都在白天或晚上的尖峰，視窗結束時每篇平均看得到 17.2–30.7 則。
- 結論：規模估算至少要用一個完整日，最好用多個完整日的平均。README 用的是 6 個完整日（744–1,109 篇），7 天普查也確認了留言總數。

## 未完成與限制

- **（2026-11-03 已補，見上方「一個完整日實測」；以下是 10/30 那輪的原文。）實測沒跑。** 原本計畫把 `ptt_q3_measure.py` 與一個完整日（最多 1,200 個請求）的 `ptt_q3_census.py` 對 www.ptt.cc 實跑，並在實測資料上檢查不變量。因為 robots.txt 對 本次驗證用的 user agent 寫 `Disallow: /`，這些都沒做。若你要自己在自己的機器上補跑，可以照下面的指令：列表最多 120 個、普查最多 1,080 個，合計最多 1,200 個請求，間隔 1.5 秒，約 25–40 分鐘。是否執行由你決定：robots.txt 對一般爬蟲（`User-agent: *`）是 `Allow: /`，另外附了 `Content-Signal: search=yes,ai-train=no,use=reference`。

  ```bash
  # 兩個指令在台灣時間的同一天接著跑。列表往回 2 天（要早於普查視窗的起點），不抽文章：約 75–115 個請求
  python q3/code/ptt_q3_measure.py --days 2 --per-day 0 --delay 1.5 --max-requests 120 --out /tmp/q3_live
  # 普查前一個完整日（台灣時間今天 00:00 往回 1 天）：約 750–1,100 篇
  python q3/code/ptt_q3_census.py --index /tmp/q3_live/index_entries.csv --sample /tmp/q3_live/article_sample.json \
      --window-end "$(TZ=Asia/Taipei date +%Y-%m-%dT00:00)" --days 1 --delay 1.5 --max-requests 1080 --out /tmp/q3_live_census
  # 總請求數：兩個 run_summary.jsonl 每一行的 requests 相加
  python -c "import json,sys; print(sum(json.loads(l)['requests'] for f in sys.argv[1:] for l in open(f, encoding='utf-8')))" \
      /tmp/q3_live/run_summary.jsonl /tmp/q3_live_census/run_summary.jsonl
  ```

  列表若沒往回翻到視窗起點（例如 120 個請求用完），普查會直接停下，不會只抓一部分。那天若超過 1,080 篇，普查會碰到上限停下；重跑同一個指令會從斷點續抓，但合計會超過 1,200 個請求，要不要續抓由你決定。跑完後把 `result_B.txt` 的篇數、留言數、不變量和本頁的離線數字對照。這只會是一天的數字，不能取代 README 的 6 個完整日平均。
- 不變量在 10/26 的列表資料（334 頁）、10/30 的 Playwright 快照（10 頁）與 2026-11-03 新抓的 95 頁列表上都檢查過。「列表推文數 vs 推−噓」只統計發文 3 天以上的文章，11/02 的視窗還沒有符合的文章，這一項在新資料上仍未檢查。
- `chart-crawl-stats.png` 因為圖表文字改了，在 Linux 用 Playwright（Chrome for Testing 153、WenQuanYi Zen Hei）重轉，字型和 repo 內其他 PNG 不同。其他 9 張的 HTML 沒變，PNG 沒動；它們在 Linux 重轉會有 1–5 px 的高度差，`chart-scale-table.png` 與 `architecture.png` 尺寸相同但約 6% 像素不同。重轉用的是 scratch 裡模仿 `capture.js charts` 的 Python 腳本（同樣 viewport 1700×1200、deviceScaleFactor 1.5、只截 `.card`），只轉這一張；`capture.js charts` 會把 10 張全部重轉。
- `chart-crawl-stats` 的「每天的抓取只需約 1.75 小時」是用每秒約 1 次算的。若正式環境也照 1.5 秒間隔（約每 1.8 秒一次），每天約 6,300 次請求需要約 3.2 小時，仍有約 7 倍餘裕。圖表這句沒改。
- `node q3/answer/src/capture.js ptt` 與 `shared/capture/capture.py` 也會打開 PTT 頁面，同樣受 robots.txt 的限制，本次沒有執行。但 `shared/capture/capture_round2_cloud.js`（q3fix 模式）在 10/30 12:29Z 執行過，載入 PTT 文章頁 2 次，產出 `q3/evidence/playwright/ptt_article_comments.png`。
- Windows 那次執行的 log 不在 repo，所以它的請求數、抽樣篇數、執行時段都無法重算。
