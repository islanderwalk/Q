# Meta 登入重爬（FB／IG／Threads）

問題：未登入時 FB／IG／Threads 只露出很少的貼文與欄位。用一個自己的帳號登入、只讀不互動地重爬同一批目標，能多拿到什麼？要付出什麼代價？

結果與證據：[../../evidence/meta-login/](../../evidence/meta-login/)（截圖、[證據鏈圖](../../evidence/meta-login/chain/)），資料：[../../data/meta-login/](../../data/meta-login/)。

## 執行順序

| 步驟 | 程式 | 做什麼 | 主要輸出 |
|---|---|---|---|
| 1 | [login_meta.py](login_meta.py) `[分鐘數]` | 開有頭的 Chromium（持久 profile）並打開三個登入頁；帳密由操作者手動輸入，程式不讀也不輸入密碼，只輪詢 cookie 名稱判斷是否已登入。 | `profile/`、`login_status.json`（cookie 值只留 sha256 前 10 碼與長度） |
| 2 | [crawl_loggedin.py](crawl_loggedin.py) `[捲動次數] [平台,...]` | 用同一個 profile 只讀地造訪與未登入時相同的目標；不按讚、不留言、不追蹤，每頁間隔至少 5 秒。 | `loggedin/<platform>.jsonl`、`evidence/` 下的頁面截圖、含 DevTools Network 面板的視窗截圖、`session_masked.json`、`api_ledger_masked.json` |
| 3 | [retake_f12.py](retake_f12.py) | 視窗截圖第一次沒抓到的目標重拍（FB／IG 會不斷改寫分頁標題，所以用計時器重設標記直到找到視窗）。 | 補齊的 F12 截圖 |
| 4 | [redact.py](redact.py) | 產生可分享的截圖副本：模糊帳號頭像、通知數、標題列未讀數。原圖只留在本機。 | `evidence/redacted/` |
| 5 | [build_compare.py](build_compare.py) | 未登入 vs 登入的欄位與筆數比較，加上每個平台的證據鏈圖，用 Playwright 轉成 PNG。 | `compare_loggedout_vs_loggedin.json`、`compare.html`、`chain_<platform>.png` |
| 6 | [build_site.py](build_site.py) + [site_template.html](site_template.html) | 只用遮罩後的截圖組成單頁證據網站。 | `site/index.html`、`site/shots/` |

```bash
pip install playwright pillow && playwright install chromium
```

```bash
META_WORKDIR=./work python login_meta.py 45
```

```bash
META_WORKDIR=./work python crawl_loggedin.py 25 facebook,instagram,threads
```

```bash
META_WORKDIR=./work python redact.py && META_WORKDIR=./work python build_compare.py
```

`META_WORKDIR` 是 profile 與所有輸出的根目錄，預設為本資料夾。`build_compare.py` 另外讀 `anon/` 底下的未登入輸出作為對照。

## 憑證與隱私處理

- 密碼：程式不經手，只在瀏覽器裡由操作者輸入。
- Cookie：只記名稱、旗標、到期時間與 sha256 前 10 碼（`session_masked.json`），值本身不落地。
- API 紀錄：只記 endpoint、friendly_name、doc_id、狀態碼與回應 JSON 的 key 路徑；不記請求本文與權杖。
- 截圖：只發佈 `redact.py` 處理過的版本。
- Instagram 登入後的動態會混入推薦貼文，`instagram_clean.jsonl` 已移除非目標作者的列，依據見 [instagram_contamination.json](../../evidence/meta-login/chain/instagram_contamination.json)。

## 不進版控

`profile/`、`evidence/`（原始截圖與未裁切的紀錄）、`loggedin/`、`anon/`、`site/`、`login_status.json`、`*.log`（見 [.gitignore](.gitignore)）。
