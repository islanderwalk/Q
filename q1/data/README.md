# Q1 資料：全部是合成資料

**這個資料夾沒有任何真實顧客資料。** 題目沒有附資料，repo 裡也沒有真實客服信件、問卷或社群貼文。
以下檔案都由 [`q1/code/make_fixtures.py`](../code/make_fixtures.py) 以固定 seed（20261030）產生，公司「範例雲」、姓名、信箱（example.com）都是虛構的。
電話不產生看起來像真實門號的號碼：顧客手機全部寫同一個佔位號碼 0900-000-000；客服市話 (02) 0000-xxxx 的用戶號碼以 0 開頭，不是可撥通的號碼。
數字只用來證明程式能跑、示範每一步的效果，不能拿來當任何產品的結論。

## 檔案

| 路徑 | 內容 | 大小 |
|---|---|---|
| `synthetic_fixtures/emails_preview.jsonl` | 前 30 封合成信件（原文＋產生器的答案 truth） | 21 KB |
| `synthetic_fixtures/survey.csv` | 2,000 份合成問卷：resp_id、tenure_months（可空白）、satisfaction、issues | 80 KB |
| `synthetic_fixtures/fixture_params.json` | 產生參數：信件組成、難例機率、問卷各層勾選機率 | 1 KB |
| `synthetic_run_40000/report.md` | 40,000 封信＋2,000 份問卷的完整實跑報告（含罐頭判定規則的逐步對照、模板相鄰對、模擬標註） | 19 KB |
| `synthetic_run_40000/coverage.md`、`coverage.csv` | 董事會報告第一頁的覆蓋範圍與排除說明、各數字的樣本來源 | 2 KB |
| `synthetic_run_40000/email_mentions.csv` | 清理後各問題的提及封數、佔比、Wilson 95% CI | 1 KB |
| `synthetic_run_40000/email_ablation.csv` | 未清理、只去引文與簽名、完整清理三種排序 | 1 KB |
| `synthetic_run_40000/canned_clusters.csv` | 罐頭回覆群（大小、代表句） | 4 KB |
| `synthetic_run_40000/label_sample_300.csv` | 300 則分層抽樣，給人工標註（human_* 欄留空，規則見下一節） | 67 KB |
| `synthetic_run_40000/survey_methods.csv`、`survey_sweep.csv` | 問卷四種排序（含每個問題相對前三名分界的位置）、母體老客戶比例 20%～80% 掃描 | 3 KB |
| `synthetic_run_40000/summary.json` | 上述全部數字（含模板相鄰對的 df 與共同出現率） | 49 KB |
| `logs/make_fixtures.log` | 產生合成資料、同 seed 重產的 sha256 比對、和上一版的比對 | 小 |
| `logs/run_q1.log` | 40,000 封實跑、`--base-old-share 0.65`、`--sample-size 30`（預期失敗）三次的輸出與退出碼 | 小 |
| `logs/pytest.log` | pytest 結果、各檔項數、答案字數 | 小 |
| `logs/claim_checks.log` | RESULTS 裡沒有直接寫在輸出檔的數字：檢查指令（腳本在 `q1/code/checks/`，附 sha256）與輸出 | 小 |
| `logs/expected_survey.log` | 產生器的問卷期望勾選比例（精確列舉，用來判斷哪些名次本來就同分） | 小 |
| `logs/prior_attempt.log` | 先前中斷的嘗試留在暫存區的輸出（未 commit）：sha256 與計數 | 小 |

## 標註規則（`label_sample_300.csv`）

標註者只填 `human_tags`、`human_is_agent`、`note` 三欄，其他欄不要改。`issue_tags.read_labels` 照下面的規則讀回，不符合規則的值會直接報錯，不會默默略過。

| 情況 | `human_tags` | `human_is_agent` |
|---|---|---|
| 還沒標 | 留白 | 留白（兩欄都空白的列會被略過） |
| 顧客信，提到一個以上的問題 | 問題名稱，用 `\|` 分隔，例如 `同步與資料遺失\|登入與帳號` | 0 或留白 |
| 顧客信，沒有提到任何問題 | **填「未分類」，不能留白**。`human_is_agent` 填了 0 而這欄空白會報錯；兩欄都空白會被當成還沒標而略過，precision 會高估 | 0 或留白 |
| 不是顧客寫的信（罐頭回覆、客服個別回覆、系統信） | 可以留白，填了也不算 | 1 |

- 問題名稱只能用 `issue_tags.TAXONOMY` 的 8 個名稱或「未分類」，打錯字會報錯。
- `human_is_agent` 只接受 1／0（也接受 是／否、y／n、yes／no、true／false）。
- 「排除：罐頭回覆」「排除：客服個別回覆」兩層各 20 則，是被清理排除的信。標註者主要判斷 `human_is_agent`：填 0 的就是被誤刪的顧客信，`estimate_exclusion_errors` 會依層大小推回全體。每層只有 20 則，0 / 20 的 95% 上界約 16%，只抓得到很大的錯誤。
- 其他層是納入分析的信：`evaluate_labels` 用它們算加權 precision／recall；`human_is_agent` 填 1 的列，規則標的問題都算誤報，也會算進誤收率。

## 沒放進 repo 的大檔

`emails.jsonl` 與 `emails_truth.jsonl` 各約 14 MB，放在暫存區（`<tmp>/work/q1/run3/fx`），不 commit。同一個 seed 重跑會得到逐位元組相同的檔案（2026-10-30 實測兩次，sha256 相同）：

| 檔案 | 位元組 | sha256 |
|---|---|---|
| emails.jsonl | 14,498,441 | `335833eebd1425e01e57bc93b2fa65877e5b1c13c218893e7e3dda7ab3773bfd` |
| emails_truth.jsonl | 14,801,298 | `70443d08a4870e4624d0407a31c14a64d8979c2a490910dff68a79662b48b705` |
| emails_preview.jsonl | 21,424 | `48299b3c4d14d92cf4085d6d55b30a2e2a6b828cbd5cba9f2f6543c767fc2b1a` |
| survey.csv | 80,296 | `5a29af088a49779b0c2e132296f937a6f5bc7efc76803c49c31b790ecbce8501` |
| fixture_params.json | 1,378 | `6469317598bb3ec5cb6d38e20a5d5c44b1c33775f31443209ea9f2d03b397eea` |

上一版的顧客手機尾碼是隨機 3 碼，`emails.jsonl` 的 sha256 是 `ce7f42f3…f162`、`emails_preview.jsonl` 是 `9490e6d7…37fc`。改成佔位號碼時保留了原本的亂數抽樣，所以只有電話號碼改變：把舊檔的電話換成 0900-000-000，5 個檔都和新檔逐位元組相同（`logs/make_fixtures.log`），其餘 3 個檔的 sha256 沒變，實跑的數字也完全相同。

重新產生：`python q1/code/make_fixtures.py --out <目錄>`（預設 40,000 封、2,000 份、seed 20261030）。
