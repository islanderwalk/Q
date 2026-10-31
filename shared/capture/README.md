# Q2–Q5 實抓證據（2026-10-30，使用者電腦、未登入）

- `shared/capture/capture.py`：PTT（Q3）、Instagram（Q5）、Facebook（Q2）截圖，也包含第一版 Threads。
- `shared/capture/capture_threads.py`：Threads（Q4、Q5）第二版。10/30 手機視窗未登入會出現登入牆，改從桌面個人頁觀察 GraphQL 分頁 query；互動數改用 svg title 找。
- `shared/capture/capture_manifest.json`：每張截圖的題號、對應架構節點、主張、結果、網址、截取時間。
- 截圖不在本資料夾：`shared/capture/capture.py` 直接寫到各題 `q{N}/evidence/playwright/`（標註框為截圖時加上）。`shared/capture/html/`：PTT 頁面 HTML，給 parser 測試當真實 fixture。`shared/capture/fixtures/`：Threads 回應片段，只留 2 串的 code、帳號、taken_at、內文。

規則：只抓未登入看得到的公開頁面；每個網站兩次導覽至少間隔 1.5–2 秒，每站載入不超過 30 次；不存任何 cookie、token、cursor 的值；不用代理輪替、不偽裝指紋；Threads、IG、FB 不存整頁 HTML。

重跑：`python capture.py q2,q3,q5` 後再跑 `python capture_threads.py`。

暫存輸出：`capture.py` 的 HTML、fixture、manifest 草稿與 log，以及 `capture_round2_cloud.js`、`capture_round2_residential.py` 的全部輸出，都寫到 `shared/capture/out/`（已列入 .gitignore，目錄結構與 repo 相同）；確認後再手動併回 `html/`、`fixtures/`、各題 `evidence/playwright/` 與 `capture_manifest.json`。

- `shared/capture/capture_gaps.py`（10/30 補證據）：`fb` 單則 FB 貼文（Q2）、`ig` Reels 分頁 JSON 與縮圖對照／單支 Reel 頁負面結果（Q5，只存欄位名與數值到 `q5/evidence/playwright/ig_reel_fields.json`）、`ptt` 與 `ptt_refresh` 高雄板斷詞雜訊截圖（Q6）。作者 ID、留言者 ID、發信 IP、編輯者 ID、電話以黑框遮住（隱私遮罩）；登入或註冊視窗照實截下，不點擊。
