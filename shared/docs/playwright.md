# Playwright 截圖紀錄（第一輪）

日期：2026-10-30。證據等級：A＝實抓，B＝推論。

## 新增的截圖與資料

| 題號 | 架構節點 | 檔案 | 佐證什麼 | 等級 |
|---|---|---|---|---|
| Q2 | platforms | q2/evidence/playwright/fb_post_nasa.png | 未登入的 NASA 單則貼文頁：粉專名稱、發文時間「5h」、心情統計可見；登入視窗擋住，照實截下；底部一般留言者的姓名、頭像、內文已黑框遮住（2026-10-30 補遮，未重新載入） | A |
| Q5 | platforms | q5/evidence/playwright/ig_reels_grid_json_check.png | Reels 分頁縮圖的觀看數（4.2M、2.9M、2.4M）與同一次載入的 JSON 對照；註冊視窗照實截下 | A |
| Q5 | platforms | q5/evidence/playwright/ig_reel_fields.json | 只含欄位名與數值：Reels 分頁 12 筆，欄位是 play_count（不是 video_view_count），另有 code 作對照 | A（資料） |
| Q6 | tokenizer | q6/evidence/playwright/ptt_noise_01.png | 高雄板文章：短網址 reurl.cc 後綴、「高雄市」「高雄」紅框 | A |
| Q6 | tokenizer | q6/evidence/playwright/ptt_noise_02.png | 「鼓山區」地名紅框 | A |
| Q6 | tokenizer | q6/evidence/playwright/ptt_noise_03.png | 地址中的「高雄市」「鳳山區」紅框 | A |
| Q6 | tokenizer | q6/evidence/playwright/ptt_noise_04.png | 同篇聚焦電話：號碼以黑框遮住，紅框標位置 | A |
| Q3 | 圖表 | q3/answer/*/chart-*.png、q3/architecture/architecture.png | 以 `node q3/answer/src/capture.js charts` 重產 10 張，來源說明已更新 | A |

Q6 使用 Kaohsiung 板（不需 18 歲確認），不是八卦板。

## 既有截圖（未重抓）

- Q2：q2/evidence/playwright/fb_page_nasa.png（粉專頁，有登入視窗）
- Q3：q3/evidence/playwright/ptt_over18_gate.png、ptt_index_latest.png、ptt_index_older.png、ptt_live_invariants.png、ptt_article_top.png、ptt_article_comments.png
- Q4：q4/evidence/playwright/threads_profile_nasa.png、threads_profile_zuck.png、threads_graphql_desktop.png、threads_mobile_wall.png、threads_post_url.png；q4/evidence/ 下 3 張舊圖
- Q5：q5/evidence/playwright/ig_reels_grid.png、ig_reel_page.png、threads_post_repost.png；q5/evidence/a-dataflow/ 內的 Threads 圖（使用者提供的截圖與 FigJam 圖，不是本次 Playwright 抓的）

## 仍缺或需注意的證據

- Q2 粉專數字 ID：未登入頁面找不到。網址使用者名稱是 NASA。曾用 `fb://profile/` 比對到 100044561550831，但那是 profile 連結，不是粉專 ID，已排除，不要採用。發文時間只有相對時間（5h），沒有絕對時間。
- Q5 單支 Reel 頁（De7XCa_TJ4h）：內嵌 JSON 42 段、觀看數欄位 0 筆，XHR 也是 0 筆。觀看數只能從 Reels 分頁取。Reels 分頁的縮圖 8 支都有 play_count，前 6 支經無條件捨去後與縮圖文字一致（例：4298087 顯示 4.2M，不是 4.3M）。
- Q5 欄位名：現有 manifest 的 q5-ig-reels-grid 寫 video_view_count，實際觀察到的是 play_count。建議修正 claim，這次沒有改既有條目。
- Q6：只有 reurl.cc 一例短網址，沒有 bit.ly 或 ppt.cc；電話只有一例（商家電話，已遮）。「高雄市」被拆成三字的斷詞結果不能靠截圖證明，要靠斷詞器輸出。

## 隱私與遮罩

- 作者 ID、留言者 ID、發信 IP、編輯者 ID、電話以黑框遮住。Q6 標題與時間列保留。
- FB、IG 的登入或註冊視窗照實截下，沒有點擊。
- 未存 HTML、cookie、token、cursor。FB、IG 只存欄位名與數值。

## 載入次數（含探測腳本）

- FB 9 次，IG 9 次，PTT 30 次（已達上限，不再載入）。每站兩次導覽間隔 ≥2 秒。

## 異動的檔案

- 新增：shared/capture/capture_gaps.py（原 tools/capture/）、q2/evidence/playwright/fb_post_nasa.png、q5/evidence/playwright/ig_reels_grid_json_check.png、q5/evidence/playwright/ig_reel_fields.json、q6/evidence/playwright/ptt_noise_01..04.png
- 修改：shared/capture/capture_manifest.json（新增 7 筆，原條目未動）、shared/capture/capture_log.txt（追加一段）、shared/capture/README.md（新增一段說明）
- 重產：q3 十張圖表與架構圖 PNG（看過 9 張：scale-table、daily-posts、hourly-posts、spec-cost、refresh-marginal、comment-arrival、invariant-results、text-share、crawl-stats，以及 architecture.png）
