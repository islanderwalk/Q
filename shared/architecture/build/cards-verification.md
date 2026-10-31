# 六題卡片核對紀錄

## Q1

- 來源：複核後版本；佐證 0 張（面板 0 張）
- 修正 ask：與 docs/handoff/qsearch_questions.md 原文不完全相符：原文三個數量都是「約」；是「老闆要在董事會上用這份分析」，不是董事會要；漏了簽名檔、只有主動填答者、購買滿 6 個月以上、同一批帳號洗版，也漏了三個子題。 → 三天後老闆要在董事會用這份分析，主題是「今年最該優先修的三個產品問題」。客服信件約 4 萬封（純文字，夾雜罐頭回覆、簽名檔、轉寄引文）、問卷約 2,000 份（只有主動填答者，8 成是購買滿 6 個月以上的老客戶）、社群貼文約 15 萬則（業配與同一批帳號洗版），最多只能認真處理兩個。要答：處理哪兩個、放棄哪一個；被放棄的如何污染結論、報告怎麼揭露；三天怎麼分配。上限 400 字。
- 修正 facts[0]：q1/README.md 原句是「不是本公司顧客的直接反饋」，卡片少了「本公司」，意思變寬。 → 處理客服信件與問卷，放棄社群貼文：業配與洗版要先標帳號與文本，三天內難以可靠區分，也不是本公司顧客的直接反饋
- 修正 verify[1]：370 只有在同時去掉空白、粗體符號和項目編號 1. 2. 3. 時才成立；保留項目編號是 376 字，連粗體符號算是 388 字。沒寫計數方式，數字無法重現。 → 字數：本次以 python 計數 q1/README.md 答案段，不計空白與粗體符號，含標點 376 字；再去掉項目編號是 370 字，都在 400 字上限內。這個結果沒有寫進 repo。
- 修正 verify[3]：數字正確，但沒說明兩條未合併分支是否有新增截圖；核對後兩條分支的 manifest 也都是 22 筆。 → Playwright 沒有拍 Q1：tools/capture/capture_manifest.json 在 master 與兩條分支都是 22 筆，沒有一筆屬於 Q1。
- 修正 conflicts[1]：同 verify[1]，370 依計數方式而定，保留項目編號是 376 字。 → b：本次計數 q1/README.md 答案段，不計空白與粗體符號為 376 字，再去掉項目編號為 370 字；pick：370 至 376 字是從答案檔實際計數，比「約 400 字」準確，但沒有寫入 repo。
- 修正 conflicts[4]：根 README 那一欄的標題是「GCP 資源（Terraform 位址）」，不是「Terraform 欄」；int_nodes 點亮的是 5 個節點（含 Analytics API、Export Service）；README 六段管線表中 crawler-worker（Q3 Q4）、GCS（Q3 Q5 Q6）、parser（Q3 Q5 Q6）的題號都沒有 Q1，原卡片沒寫出這個分歧。 → a：int_nodes.json 讓 Q1 點亮 crawler-worker、Cloud Storage、parser、Analytics API、Export Service；docs/handoff/gcp.md 中前三個是 terraform planned；b：infra/gcp/README.md 寫「Q1 是取捨題，沒有對應資源」；根 README 第 32 行資料品質閘門的 GCP 資源欄為「無」，crawler-worker、GCS、parser 列的題號也沒有 Q1
- 衝突「Q1 答案狀態」：根目錄 README.md 第 44 行 Q1 關鍵數字寫「答案尚未撰寫」／q1/README.md 已有完整答案草稿；python.md 與 HANDOFF.md 也寫「答案草稿」。採用：顯示「答案草稿」。以答案檔 q1/README.md 為準，根 README 是較舊的狀態。
- 衝突「答案字數」：python.md 總覽表寫「答案草稿（約 400 字）」／本次計數 q1/README.md 答案段，不計空白與粗體符號為 376 字，再去掉項目編號為 370 字。採用：master 寫「400 字上限內」即可。370 至 376 字是從答案檔實際計數，比「約 400 字」準確，但沒有寫入 repo。
- 衝突「Q1 字數上限」：HANDOFF.md 未完成第 2 項寫「各題 600/800 字上限內的正式答案」／docs/handoff/qsearch_questions.md 題目原文 Q1「上限 400 字」。採用：以題目原文 400 字為準，HANDOFF 那句是各題的籠統寫法。
- 衝突「path 與答案內容」：現有卡片 path：crawler-worker（社群貼文）開頭，parser 寫「去罐頭、去重、洗版偵測」／q1/README.md 放棄社群貼文，不做洗版處理，只抽 200 則做定性佐證；parser 該做的是信件去罐頭回覆、簽名檔、轉寄引文。採用：以答案檔為準。parser 改標信件清理，crawler-worker 改標「只抽 200 則」，節點不變。
- 衝突「Q1 是否用到 GCP 資源」：int_nodes.json 讓 Q1 點亮 crawler-worker、Cloud Storage、parser、Analytics API、Export Service；docs/handoff/gcp.md 中前三個是 terraform planned／infra/gcp/README.md 寫「Q1 是取捨題，沒有對應資源」；根 README 第 32 行資料品質閘門的 GCP 資源欄為「無」，crawler-worker、GCS、parser 列的題號也沒有 Q1。採用：Q1 的驗證不附 terraform plan。點亮節點只標示資料品質落點，這點寫在 notes。

## Q2

- 來源：複核後版本；佐證 2 張（面板 2 張）
- 修正 ask：和 qsearch_questions.md 對照，只寫到估算與選方案，漏了第 3 到 5 小題（斷詞策略、換方案門檻、缺哪個數字）、題目列的欄位和 600 字上限。 → 100 萬則 FB 粉專貼文（平均 250 字中文、不含留言，另有心情統計、發文時間、粉專名稱與 ID），10 位同事用的內部工具，每天約 500 次查詢、新增約 3,000 則，一人維運，每月基礎設施預算 3,000 元。要估原始資料、粉專 ID 索引、中文全文索引，選方案並說出是哪個數字決定的；再答斷詞策略會漏哪類查詢、成長到多少要換、缺哪個數字誤差最大。上限 600 字。
- 修正 evidence[0].cap：放大截圖後，標「2｜心情互動統計」的框圈的是被遮留言旁的 79（留言時間 4h），不是貼文的心情；貼文本身顯示 5.8K。寫「心情統計可見」會指向那個錯的框。 → FB 單則貼文未登入：粉專名稱、發文時間 5h、貼文心情 5.8K 可見；找不到數字 ID，留言已遮
- 修正 evidence[1].cap：這張圖的說明欄只列 HTTP 200、粉專名稱、2 reactions、登入視窗、Graph API，沒有提到數字 ID；找不到 ID 是單則貼文那張圖和 playwright.md 寫的。 → FB 粉專頁未登入：HTTP 200、讀得到粉專名稱，但跳登入視窗；量大改走 Graph API
- 修正 evidence[*].ref 與 sources 的（HEAD）：本機 HEAD 其實是 80adb29（分支 claude/project-thread-n1bwea，重整過目錄），不是 717a237；qsearch_questions.md、q2/page/fb_q2_posts_table.html、capture_manifest.json 等在本機 HEAD 不存在。兩張截圖在兩邊位元組相同。 → 改成 origin/master
- 修正 verify[4]：gcp.md 只寫 fmt、validate「通過」，退出碼記在 infra/gcp/README.md；而且 fmt -check 第一次退出碼 3，修正格式後才是 0。 → Terraform：infra/gcp 的 terraform plan 退出碼 0：70 to add、0 change、0 destroy；fmt 第一次退出碼 3，修正後 0；validate 退出碼 0；未 apply（infra/gcp/README.md）。
- 修正 verify[5]：漏了一點：Q2 頁面原始 HTML 也找不到 bigm，只寫「兩字詞 GIN」，所以 pg_bigm 從沒被量過。另外 master 架構頁有 pg_bigm 的 HCL 片段，但不在 infra/gcp，也沒進 plan，應寫明是 infra/gcp 沒有。 → 沒驗證的部分：infra/gcp 沒有 pg_bigm 搜尋庫；重跑用的 EDB Windows 版沒有 pg_bigm，改用 GIN(array_ops) 模擬；Q2 頁面也只寫「兩字詞 GIN」。
- 修正 verify[6]：playwright.md 的「FB 9 次」是補抓那一輪的載入次數，含探測腳本；粉專頁那張列在「既有截圖（未重抓）」，用「共」不對。 → Playwright 未登入實抓 FB 粉專頁與單則貼文各 1 張，manifest 標 status real；補抓單則貼文那一輪 FB 載入 9 次，含探測腳本（docs/handoff/playwright.md）。
- 修正 conflicts[7].a（兩字詞索引的實作）：Q2 頁面原始 HTML 搜尋 bigm 為 0 處，只寫「兩字詞 GIN」；RESULTS.md 也只說「很可能」是 pg_bigm。寫 pg_bigm 的是 int 節點和 master 卡片。 → int 節點與 master 卡片寫 pg_bigm；Q2 頁面只寫「兩字詞 GIN」，沒提 pg_bigm
- 修正 conflicts[9].pick（FB 截圖的心情數）：截圖裡「心情互動統計」的框本身就圈在留言的 79 上，只寫「心情統計可見」仍會指向它；貼文自己的數字是 5.8K。 → 說明寫貼文心情 5.8K，不寫 79；manifest 的 79 是留言的心情，要另外更正。
- 修正 conflicts[10].pick（Q2 對應的 GCP 資源）：infra/gcp/README.md 這張表的表頭是「架構節點（poc／A／int）」，含整合版，說它是 PoC 時期的沒有依據。 → 路徑以 int_nodes 為準，它是整合圖實際顯示的節點；README 表頭含 int，題號欄應補上 Q2。
- 修正 conflicts（新增）：int 節點 searchdb 副標是「ptt_search｜7 天全文」，筆記寫 Q2 用同一套；但 Q2 是 100 萬則全量查詢，範圍不一致，原卡片沒列。 → 新增「搜尋庫的範圍」：路徑照接 pg_bigm 搜尋庫，但要註明 Q2 存全量 100 萬則，不受 7 天限制。
- 修正 say：406 MB 是十進位，facts 裡頁面的 388 MB 是二進位，同一個索引出現兩個 MB 數字，和卡片自己的單位註記矛盾。 → 資料約 1 GB，兩字詞全文索引至少是它的 40%；每天只有 500 次查詢，一台小 Cloud SQL 加 pg_bigm 就夠，不必另架搜尋叢集。
- 修正 todo：漏了預算：三個分支都找不到 Cloud SQL 月費對 3,000 元預算的計算，「一台小 Cloud SQL 就夠」在成本上沒有驗證。 → 真實中文內文的全文索引還沒量，只有下限與上限；pg_bigm 本身從未實跑；simple 多 43% 的原因未查；Cloud SQL 月費是否在 3,000 元內也還沒算。
- 修正 notes[4]：容器有 /root/.local/bin/pytest 9.1.1。在 master 匯出副本實跑 q2/code/tests，因缺 psycopg 在收集階段失敗，退出碼 2。 → 本次核對容器有 pytest 9.1.1，在 master 匯出副本重跑 q2/code/tests：缺 psycopg，收集階段失敗（退出碼 2），9 passed 只能引用 python.md。
- 衝突「全文索引大小」：master 卡片 todo 寫先前估 1–2.5 GB（repo 內只出現在架構頁這張卡片本身，找不到出處）／Q2 頁面 388 MB（406,396,928 B）；1M 重跑 406,257,664 B；10 萬列 distinct 每則 9,466 B。採用：顯示 1M 重跑 406,257,664 B 並標明是下限，因為它最新且實測；真實值介於下限與 distinct 上限之間。1–2.5 GB 沒有出處，拿掉。
- 衝突「重跑規模」：master 卡片 verify：本機 PostgreSQL 16 以 10 萬則重跑／python-verification：PostgreSQL 16.15 實跑 100 萬列，另跑 10 萬列 distinct。採用：顯示 100 萬列重跑，因為較新，規模也和題目相同。
- 衝突「一則列長」：Q2 頁面 pageinspect：lp_len 918 B／1M 重跑 page0：8 列都是 920 B。採用：兩者都是實測，每頁都放 8 則；卡片寫 918 B，並註明重跑是 920 B。
- 衝突「粉專 ID 索引是否有列」：RESULTS.md 只對照頁面的 const D，和 python.md 都寫 btree(page_id) 頁面未列／Q2 頁面內文有列：21 MB、6.6 MB（6,889,472 B，5,000 個粉專）。採用：以頁面為準，並附上重跑的 6,873,088 B（1 萬個粉專）；數字寫在頁面內文、不在 const D，所以被漏看。
- 衝突「重現誤差範圍」：RESULTS.md 開頭寫兩字詞與 pg_trgm 都在 0.03%～0.1% 之內／python.md 與 RESULTS.md 對照表寫 −0.03% 與 +0.07%。採用：用對照表的 −0.03% 與 +0.07%，這是實際算出的值。
- 衝突「simple 索引」：Q2 頁面 to_tsvector('simple') 3,268,608 B／1M 重跑 4,677,632 B（+43%，未解釋）。採用：卡片不放 simple 的數字；差異列進 todo。
- 衝突「建索引時間」：Q2 頁面（WSL Ubuntu）：兩字詞 7 分 39 秒、pg_trgm 1 分 17 秒、simple 5.5 秒／1M 重跑（Windows 11、EDB 版）：540 秒、183 秒、48.7 秒。採用：卡片不放建索引時間；兩次在不同系統上跑，數字不能直接比。
- 衝突「兩字詞索引的實作」：int 節點與 master 卡片寫 pg_bigm；Q2 頁面只寫「兩字詞 GIN」，沒提 pg_bigm／1M 重跑用 GIN(array_ops) 模擬，entry 數 248,926 對頁面 248,925。採用：方案照寫 pg_bigm，但註明頁面與重跑都不是 pg_bigm 實測，pg_bigm 本身待實跑。
- 衝突「PostgreSQL 版本」：Terraform 與 int 節點：crawler-db 是 Cloud SQL PG15，搜尋庫與它同台／Q2 頁面與 1M 重跑都用 PostgreSQL 16.15。採用：卡片寫量測版本 16.15；部署版本要和量測對齊，還沒決定。
- 衝突「FB 截圖的心情數」：master 卡片說明寫「心情 79」；manifest 寫 79／1／30／5 reactions／截圖標「心情互動統計」的框在被遮的留言列旁（留言時間 4h），貼文本身顯示 5.8K、367 shares。採用：說明寫貼文心情 5.8K，不寫 79；manifest 的 79 是留言的心情，要另外更正。
- 衝突「Q2 對應的 GCP 資源」：infra/gcp/README.md：Q2 對應 BigQuery、crawler-db、GKE；Cloud Tasks crawl-fb 只標 Q3、Q4／int_nodes：Q2 走 Scheduler、Cloud Tasks、worker、GCS、parser、pg_bigm 搜尋庫，不經 BigQuery。採用：路徑以 int_nodes 為準，它是整合圖實際顯示的節點；README 表頭含 int，題號欄應補上 Q2。
- 衝突「q2/README.md 現況」：README 寫 code/ 目前為空，Playwright 只列 fb_page_nasa.png／master 已有 run_q2.py、sql、tests 與 fb_post_nasa.png。採用：以實際檔案為準；README 已過時，合併時要更新。
- 衝突「搜尋庫的範圍」：int 節點 searchdb 副標是「ptt_search｜7 天全文」，筆記寫 Q2 用同一套／題目 Q2 是 100 萬則全量查詢，沒有 7 天視窗。採用：路徑照接 pg_bigm 搜尋庫，但要註明 Q2 存全量 100 萬則，不受 7 天限制。

## Q3

- 來源：複核後版本；佐證 24 張（面板 4 張）
- 修正 ask：題目原文的留言新鮮度只限「包含關鍵字的文章」，卡片漏掉這個範圍。這正是第 6 小題可以提修改的地方。也漏了「設計 ETL Pipeline」和上限 800 字。 → 設計 ETL：查 7 天內含關鍵字的貼文與留言。新文 6 小時內要搜得到；含關鍵字文章的留言，7 天內每天至少更新一次。一人維運、兩週上線、GCP＋Python；沒有 API，過度請求會被擋。上限 800 字。
- 修正 facts[4]：一頁 20 格裡有刪文佔位（ptt-list-slots-deleted：19 篇加 1 格刪文；live_invariants 10 頁有 3 列已刪除）。chart-invariant-results 寫的是「剛好 20 格」，寫 20 篇會誤導。 → 不變量：完整列表頁固定 20 格（334/334 頁，刪文也佔一格）、ID 時間戳倒序 0 筆、推文數 97.2% 等於推減噓
- 修正 facts[3]：數字正確。但「到齊」暗示留言已全部進來，圖表原文是「進來」。 → 留言 92.5% 在發文 6 小時內、99.3% 在 24 小時內進來；第 2–6 次重抓合計只多 0.76%
- 修正 verify[3]：README 和 gcp.md 都沒寫日期，2026-10-30 來自 commit 4e7f428 的日期。fmt 第一次檢查退出碼是 3，格式化後複查才是 0。 → Terraform：infra/gcp 在真實 GCP project 跑 terraform plan，退出碼 0：70 to add、0 change、0 destroy；未 apply。記在 infra/gcp/README.md（commit 4e7f428，2026-10-30）。另加一條：fmt 第一次檢查退出碼 3，格式化後複查 0；validate 退出碼 0。
- 修正 verify[0]：內容正確（test_ptt_smoke.py 註明 fixture 是 2026/10/30 實抓）。漏了 python-verification 的 HANDOFF.md 寫的「主代理已重跑確認」。 → pytest q3/code/tests：4 passed（退出碼 0），用 2026/10/30 實抓的 PTT HTML；記在 python.md，HANDOFF 註明主代理重跑確認（python-verification 分支）。
- 修正 verify[5]：playwright.md 說的 10 張是 9 張 chart-*.png 加 architecture.png，不是 10 張圖表。 → Playwright：PTT 實頁 6 張（2026-10-30 拍攝），主張與結果記在 capture_manifest.json；9 張圖表加架構圖共 10 張 PNG 由實測資料重產（playwright.md）。
- 修正 evidence[q3/evidence/playwright/ptt_article_comments.png].cap：截圖只拍到 12 列留言，看不到 108／7／88 這組數字。數字來自 capture_manifest 的 result（整篇的計數）。 → 留言列有推噓、帳號、內容、IP 與時間；全篇推 108、噓 7、→ 88
- 修正 conflicts[抓取間隔]：ptt_q3_census.py 的 --delay 預設也是 0.8，每次請求後 sleep。平均 1.11 秒剛好是 0.8 秒停頓加上平均 0.295 秒回應時間。所以「0.8 秒只用在 545 次」沒有根據；int_nodes 的 0.8 秒間隔說法大致成立。 → b：10/26 抽樣 545 次用 delay=0.8s；普查程式 --delay 預設也是 0.8，result_B 實測平均每 1.11 秒一次（含回應時間），6,237 次 0 失敗。pick：0.8 秒是停頓設定，不是實際間隔；改寫成「每次停 0.8 秒，普查實際平均 1.11 秒一次；545 次與 6,237 次都沒被擋」。
- 修正 conflicts（漏列）：int_nodes gcs 寫 PTT 每天約 100 MB（含留言回抓），這來自 10/26 抽樣的規格 2 下載量 100.6 MB，用的是 6.6 倍算法。卡片已經拿掉 9.1 倍，這條卻沒列。 → 新增衝突「PTT 每日下載量」：節點註明「10/26 抽樣推算」，或改用普查的 7 天 HTML 104.5 MB。
- 修正 sources[最後一條]：本機 HEAD 其實是 cfc725a（claude/project-thread-n1bwea），80adb29 是它的上一個 commit。任務說 checkout 等於 717a237，也和實際不符。內容未變這點已用 blob hash 核對（例如 chart-spec-cost、architecture.png 相同）。 → 本機 HEAD cfc725a（claude/project-thread-n1bwea，接在 80adb29 之後）與 origin/claude/project-thread-xej5op：Q3 圖片搬到 q3/answer/** 與 q3/architecture/architecture.png，blob 與 master 相同；evidence 用 origin/master 路徑
- 衝突「兩條規格的請求數與倍數」：ptt_q3_result.txt（10/26 抽樣）與舊卡片：規格 1 約 947 次、規格 2 約 6,262 次，6.6 倍；規格 2 把第 0 天也算進去。／chart-spec-cost、q3/README、根 README：規格 1 960 次、規格 2 5,340 次，5.6 倍；第 1–6 天各重抓一次。。採用：用 960／5,340／5.6 倍。這組比較新，跟 890 篇/天用同一套算法，根 README 也是這組。
- 衝突「下載量倍數」：舊卡片與 10/26 抽樣：規格 2 下載量是規格 1 的 9.1 倍（11.0 MB 對 100.6 MB）。／改用 5.6 倍的算法後，下載量倍數沒有重算，普查也沒有這個數字。。採用：卡片拿掉 9.1 倍，免得和 5.6 倍用兩套算法。
- 衝突「PTT 每日下載量」：int_nodes gcs：PTT 每天約 100 MB（含留言回抓）。／10/26 抽樣：規格 1 約 11.0 MB、規格 2 約 100.6 MB，用的是 6.6 倍算法；普查只有 7 天 HTML 合計 104.5 MB。。採用：節點註明「10/26 抽樣推算」，或改用普查的 7 天 HTML 104.5 MB；不要和 5.6 倍並列。
- 衝突「每日發文量」：10/26 抽樣：167.3 小時平均 895 篇/天。／圖表、q3/README、架構圖：6 個完整日平均 890 篇/天（同一份抽樣檔也有這個數字）。。採用：用 890。頭尾不完整的兩天已經排除，圖表和架構圖也都用它。
- 衝突「7 天文章數」：抽樣推算 6,262 篇；架構圖寫約 6,200 篇；規模總表寫約 6,230 篇（890 × 7）。／列表實數 6,237 篇；普查成功打開 6,224 篇，另有 13 篇 404。。採用：卡片用普查的 6,224 篇（真的打開過）；6,237 是列表上的篇數。
- 衝突「留言數與文字量」：10/26 抽樣推算：留言 266,498 則、純文字 25.2 MB、原始 HTML 106.6 MB。／10/27 全數普查：留言 234,577 則、純文字 22.9 MB、原始 HTML 104.5 MB。。採用：用普查。是全部數出來的，不是抽樣推算。
- 衝突「留言抵達比例」：抽樣 120 篇：6 小時內 93.1%、24 小時內 99.1%；24 小時後仍有新留言 17.5%。／普查 5,182 篇：6 小時內 92.47%、24 小時內 99.27%；24 小時後仍有新留言 9.7%。。採用：用普查。
- 衝突「推文數不變量」：抽樣 210 篇有 206 篇相同。／普查 3,797 篇有 97.2% 完全相同，差距在 2 以內的有 99.6%。。採用：用普查的 97.2%。
- 衝突「python.md 實跑的外推」：python.md：1.2 小時實跑（88 篇）的輸出是 1,781 篇/天、74,822 則留言。／q3/README 與圖表：890 篇/天、234,577 則留言。。採用：用 README。python.md 自己也註明窗口太短，不能外推。
- 衝突「搜尋資料放哪裡」：q3/2-architecture：Cloud Run Job 抓取，存 BigQuery 分區，子字串全掃，每次約 1–3 秒。／int_nodes：共用 worker 與 parser，寫進 Cloud SQL 上的 pg_bigm 搜尋庫，搜尋 API 用 LIKE。。採用：卡片 path 照規定用整合節點（pg_bigm），say 改成中性的「一個小資料庫」，並加註 Q3 圖用 BigQuery。交卷前要選一個。
- 衝突「派工佇列名稱」：int_nodes dispatch：crawl-ptt／threads／fb。／Terraform scheduler_tasks.tf：只有 crawl-fb／yt／news，每秒 5 次，沒有 crawl-ptt。。採用：path 保留 crawl-ptt，verify 寫明 plan 裡沒有這條佇列。Terraform 是實際跑過 plan 的來源。
- 衝突「排程頻率」：Q3 架構圖：Cloud Scheduler 每小時觸發一次。／Terraform crawl-tick 預設每 15 分鐘（*/15）。。採用：兩種都滿足 6 小時的規格。整合圖跟著 Terraform；交卷答案寫每小時也可以。
- 衝突「GCS 原始檔保存」：Q3 架構圖：gzip 後保留 30 天；int_nodes gcs：30 天轉 Archive。／Terraform storage.tf、根 README：30 天轉 COLDLINE。。採用：用 COLDLINE，因為 plan 裡實際是這個設定。
- 衝突「抓取間隔」：int_nodes platforms：以 0.8 秒間隔打了約 6,800 次，沒被擋過。／10/26 抽樣 545 次用 delay=0.8s；普查程式 --delay 預設也是 0.8，result_B 實測平均每 1.11 秒一次（含回應時間），6,237 次 0 失敗。。採用：0.8 秒是請求之間的停頓設定，不是實際間隔。改寫成「每次停 0.8 秒，普查實際平均 1.11 秒一次；545 次與 6,237 次都沒被擋」。
- 衝突「回應時間 P95」：q3/README 寫 0.46 秒；chart-crawl-stats 寫 0.45 秒。／result_B 原始結果：0.455 秒。。採用：文字用 0.455 秒；圖表的 0.45 是四捨五入，不必重產。

## Q4

- 來源：複核後版本；佐證 10 張（面板 4 張）
- 修正 ask：官方題目 (a) 還問「對 Threads 網站上實質的 requests 呼叫狀況掌握多少」，原文漏掉。 → 40 萬個帳號（公開頁、不登入），每帳號每次至少 10 篇，每天 4 輪；欄位是貼文 URL、內文、發文時間。要估每日請求數，並說明對實際請求掌握多少；說明防封與代理選型（雲端 IP 段多半已被封）；再附牌價算每月代理成本。上限 600 字。
- 修正 facts[1]：兩個 doc_id 的取得條件不同：舊的是手機視窗捲動（verification §2），新的是桌面 1440×900 載入（manifest）。不能斷定是隨時間更換。 → doc_id 兩次不同：10/29 紀錄 39551525044493602，10/30 桌面網頁送出 28880431481598345，要由瀏覽器動態取得
- 修正 facts[4]：threads_q4_result.txt 標明 TLS 握手流量是「估計」，原文寫得像實測。 → 每月約 1.5 TB（含 TLS 估 1.8 TB）：IPRoyal $1.84/GB 約 US$2.8–3.3k，Oxylabs $2.5/GB 約 US$3.8–4.5k
- 修正 path：「每 6 小時」不在任何 Q4 來源裡。題目只說每天 4 輪，Terraform 的 crawl-tick 是每 15 分鐘敲一次。 → Cloud Scheduler → crawler-api（每天 4 輪）→ crawl-threads 佇列 → crawler-worker（Threads adapter，讀 token 池）→ 住宅輪替代理 → 目標平台（Threads GraphQL）→ Cloud Storage → 事件 → parser → Elasticsearch
- 修正 verify[1]：大小不同：python.md 是 261,523 B，§1 是約 259 KB。只有結論相同（SHELL、0 篇）。另外漏了沒跑瀏覽器模式的原因。 → threads_q4_measure.py --mode http 實跑 1 個請求（退出碼 0）：HTTP 200、261,523 B、0 篇（SHELL）。舊腳本只帶 UA、Accept、Accept-Language，沒有 Sec-Fetch，結果和驗證紀錄 §1 一致。瀏覽器模式沒有跑，因為沒有取得 Chromium 下載授權。紀錄：python.md。
- 修正 verify[2]：verification 只在 §7 壓測記錄了「個人網路 IP」，§6 的三帳號測試沒有記出口 IP。 → GraphQL 實測（10/29，未用代理）：只打第 1 頁，zuck、nba、nytimes 都回 10 串。個人網路 IP 壓測 102 次，82 次成功，之後全部 401。紀錄：q4/answer/threads_q4_verification.md §6、§7。
- 修正 verify[3]：capture_log.txt 顯示第一輪的 graphql-observed 是 pending（沒觀察到回應），第二輪才成功。原文沒提。 → Playwright 10/30 未登入實抓 Q4 截圖 5 張，manifest 狀態都是 real；桌面個人頁網頁自己送的 GraphQL 回 HTTP 200、11 串。第一輪沒觀察到這個回應（pending），第二輪才成功。紀錄：tools/capture/capture_manifest.json、capture_log.txt。
- 修正 verify[4]：infra/gcp/README.md 和 gcp.md 都沒有寫日期，2026-10-30 只能從 commit 4e7f428 推得。 → Terraform：infra/gcp 對真實 GCP project 跑 terraform plan，退出碼 0：70 to add、0 change、0 destroy；fmt、validate 退出碼 0；未 apply。plan 只有 crawl-fb／yt／news 佇列，沒有 crawl-threads；token 池、token-refresher、Elasticsearch 都沒有 Terraform。紀錄：infra/gcp/README.md、scheduler_tasks.tf。
- 修正 evidence[3].cap（threads_mobile_wall.png）：截圖標註與 manifest 都寫捲動期間仍有 2 個帶貼文的 GraphQL 回應。只寫登入牆，會讓人以為完全不能分頁。 → 手機視窗未登入出現登入牆；捲動期間帶貼文的回應仍有 2 個
- 修正 evidence[6].cap（threads_profile_zuck.png）：截圖被登入視窗蓋住，只露出 1 篇。標註和 manifest 寫的是 HTML 內嵌 4 串、10 篇，不是畫面可見 10 篇。 → @zuck 個人頁 HTML 只內嵌 4 串、共 10 篇；畫面被登入視窗蓋住
- 修正 evidence[9].cap（threads-profile-natgeo.png）：截圖裡 @natgeo 的貼文內嵌 @natgeotv 的原文卡片，是引用貼文，不是轉發。 → 手機視窗未登入：@natgeo 含一篇引用 @natgeotv 的貼文（已隱藏軟牆）
- 修正 conflicts[出口 IP 方案].pick：git log 顯示答案檔最終版是 2026-10-29 commit 8bfd8b0，Terraform 文件是 2026-10-30 commit 4e7f428，比實測晚。 → 照答案檔：Threads 走住宅輪替代理，不走 Cloud NAT。答案檔有單 IP 實測；Terraform 文件只把 NAT 列為選項，沒有對 Threads 實測。
- 修正 conflicts[Threads 貼文寫到哪裡].pick：Q5 題目說的是 Threads 轉發數「已爬到但從未寫入 ES」，不是說 Threads 資料寫進 ES。依據應是 Q6 題目：ES 存約 5 億筆、含 Threads 渠道。 → master 顯示 Elasticsearch。Q6 題目說 ES 存約 5 億筆社群貼文，渠道含 Threads；Q5 要把 Threads 轉發數補寫進 ES。Q4 圖是單題草圖。
- 修正 conflicts[未登入能不能捲動分頁].b：同一筆 manifest 結果是「帶貼文的 GraphQL 回應 2 個」，捲動仍有分頁，只是之後出現登入牆。 → capture_manifest q4-threads-mobile-wall（10/30）：捲動期間帶貼文的 GraphQL 回應 2 個，並出現「Log in to see more from nasa.」登入牆
- 修正 conflicts[GraphQL doc_id].pick：沒提到兩次的取得條件不同（手機捲動、桌面載入），「會換」的原因沒有被分開驗證。 → 兩個都列，結論寫「doc_id 會變，要由瀏覽器動態取得」。10/30 較新且是實抓。兩次的日期和視窗都不同，還分不出是哪個造成的。卡片與目標平台節點不要寫死單一 doc_id。
- 修正 sources（最後一項）：git rev-parse HEAD 是 bf86b23。80adb29 是它往前兩個 commit。 → 注意：工作樹 HEAD 是 bf86b23（分支 claude/project-thread-n1bwea），不是 717a237；本卡一律讀 origin/master（717a237）
- 衝突「GraphQL doc_id」：threads_q4_result.txt、verification §2、diagram-system.png、int_nodes 目標平台節點：doc_id 39551525044493602（手機視窗捲動取得，10/29 答案採用）／capture_manifest q4-threads-graphql-observed（10/30 桌面 1440×900 載入）：網頁送出的 doc_id 是 28880431481598345。採用：兩個都列，結論寫「doc_id 會變，要由瀏覽器動態取得」。10/30 較新且是實抓。兩次的日期和視窗都不同，還分不出是哪個造成的。卡片與目標平台節點不要寫死單一 doc_id。
- 衝突「純 HTTP 抓個人頁有沒有貼文」：python.md：threads_q4_measure.py --mode http 拿到 261,523 B、0 篇，寫「與 q4 README 不一致」／verification §1：舊腳本缺 Sec-Fetch 才回 SHELL（約 259 KB、0 篇），補上就有內嵌貼文；README 的 4 串是瀏覽器實抓。採用：不算矛盾，照 verification 寫。腳本的 BROWSER_HEADERS 只有 UA、Accept、Accept-Language，python.md 重現的是已知的 SHELL 結果。
- 衝突「threads_q4_measure.py 在卡片上的角色」：現有卡片 verify 與 build_arch.py：「以 1 個公開帳號低頻實跑」，讀起來像在佐證最終做法／threads_q4_result.txt：此腳本與 results.json 是舊量測紀錄，wire 欄位其實是解壓後大小。採用：改寫 verify：標明它是舊腳本，只證明缺 Sec-Fetch 會回 SHELL。最終數字來自 threads_q4_verification.md。
- 衝突「單一 IP 能撐的速率」：verification §7（10/28，出口 IP 未記錄）：打第 2 頁，衝到 10.7 req/s 共 560 次，0 次異常／verification §7（10/29，個人網路 IP）：打第 1 頁，5 req/s 約 80–100 次後全部 401，5 分鐘未解。採用：顯示 10/29：較新，和最終做法是同一種請求，也是答案檔採用的數字。兩次差異原因未查明，標為未驗證。
- 衝突「未登入能不能捲動分頁」：verification §2（10/28）：手機視窗捲動會觸發分頁 query，帶 cursor 可翻到第 4 頁／capture_manifest q4-threads-mobile-wall（10/30）：捲動期間帶貼文的 GraphQL 回應 2 個，並出現「Log in to see more from nasa.」登入牆。採用：顯示 10/30：未登入捲動只給看前幾串。最終做法直接 POST GraphQL，不靠捲動；但 10/30 之後沒有重測直接 POST，所以要監控每次回傳的串數。
- 衝突「Threads 貼文寫到哪裡」：q4/architecture/diagram-system.png：儲存畫成 PostgreSQL／物件儲存／int_nodes 的 Elasticsearch 節點與現有卡片 path：Threads 貼文寫進 Elasticsearch。採用：master 顯示 Elasticsearch。Q6 題目說 ES 存約 5 億筆社群貼文，渠道含 Threads；Q5 要把 Threads 轉發數補寫進 ES。Q4 圖是單題草圖。
- 衝突「出口 IP 方案」：infra/gcp/README.md 與根 README：Cloud NAT 固定出口 IP 列為 Q4 選項；gcp-vs-onprem.md 把代理池當備案／threads_q4_result.txt 與 int_nodes 抓取節點：單 IP 80–100 次就 401，主力用住宅輪替代理，ISP 固定 IP 不作主力。採用：照答案檔：Threads 走住宅輪替代理，不走 Cloud NAT。答案檔有單 IP 實測；Terraform 文件只把 NAT 列為選項，沒有對 Threads 實測。
- 衝突「Threads 佇列與限速」：infra/gcp/scheduler_tasks.tf：只有 crawl-fb／yt／news，每條每秒 5 次、同時 10 個／int_nodes 派工節點：crawl-threads 要 18.5 req/s，同時約 22 個請求在途。採用：卡片照 18.5 req/s，由答案檔的每天 160 萬次推得。Terraform 缺 crawl-threads 佇列，是待補的缺口，不改卡片數字。
- 衝突「Q4 pytest 狀態」：origin/master 根 README：pytest 由 python 代理執行中，本次未重跑／python-verification 的 python.md 與 HANDOFF.md：q4/code/tests 2 passed，退出碼 0，主代理已重跑確認。採用：顯示 2 passed。較新，而且有退出碼。
- 衝突「Playwright 能否看到 GraphQL 回應」：capture_log 第一輪：q4-threads-graphql-observed 為 pending，這次捲動沒觀察到帶貼文的 GraphQL 回應／capture_log 第二輪（v2）與 manifest：doc_id 28880431481598345、HTTP 200、11 串、401 KB，狀態 real。採用：顯示第二輪結果。第一輪失敗只寫在 verify 裡，說明觀察結果不一定每次都抓得到。
- 衝突「@zuck 截圖內容」：q4/README.md：@zuck 個人頁 HTML 只內嵌 4 串，畫面可見 10 篇／截圖標註與 manifest：HTML 內嵌 4 串、10 篇；畫面被登入視窗蓋住，只露出 1 篇。採用：照 manifest 與截圖標註寫「內嵌 4 串、共 10 篇」，不寫「畫面可見」。
- 衝突「@natgeo 截圖內容」：q4/README.md：含一篇轉發 @natgeotv 的貼文／截圖本身：@natgeo 的貼文內嵌 @natgeotv 的原文卡片，是引用貼文。採用：照截圖寫「引用」。

## Q5

- 來源：複核後版本；佐證 16 張（面板 4 張）
- 修正 verify[6]：容器裡有 /root/.local/bin/pytest（pytest 9.1.1，以 uv tool 安裝），只是 /usr/bin/python3 -m pytest 找不到它。用它在暫存區的 origin/master 複本跑，結果是 11 passed、退出碼 0。 → 本次查核重跑同一組測試：容器內有 pytest 9.1.1（Python 3.13.17），在暫存區的 origin/master 複本跑，11 passed（退出碼 0）。結果沒有寫進 repo。
- 修正 verify[1]：對 repo 的描述正確，但不完整。本次在暫存區用測試的 4 筆 record 跑了 CLI，沒有送 ES，退出碼 0，產出 3 篇、3 個 bulk 檔。 → backfill.py 示範：python.md 第五節與 HANDOFF 都說沒留 log、退出碼未核對。本次在暫存區用測試的 4 筆 record 跑 CLI（不送 ES）：退出碼 0，產出 3 篇、3 個 bulk 檔。
- 修正 notes[6]：在三個分支的原始檔裡都找不到 indexed_at。它只出現在舊主卡產生的 qsearch-architecture.html。b-c-d README 的定義是「ES 寫入時間 − published_at，每篇記在文件裡」，backfill 的文件也沒有 indexed_at 欄位。 → 監控 SLA：首次可見延遲＝ES 寫入時間減 published_at，每篇記在文件裡。P95 超過 45 分鐘警告、超過 60 分鐘呼叫；1 小時內首次抓取率低於 95% 警告。
- 修正 say：b-c-d README (d) 寫的是「爬蟲的抓取頻率，而不是 ES 寫入」，沒有比較 API。 → 最可能的瓶頸在抓取頻率，不在 ES 寫入：資料要先被抓到，1 小時的 SLA 才有意義。
- 修正 facts[0]：題目、FigJam 圖和 b-c-d 都把第一層叫「爬蟲寫入」。parser 是總架構 int_nodes 的對應節點，不是題目的用語。 → 要改的元件依序是爬蟲寫入（總架構的 parser）、ES mapping、Data Layer API、Analytics API、Export Service、前端。
- 修正 facts[1]：manifest 寫的是「單支 Reel 頁未登入載入時沒有」。登入後有沒有這個欄位，並沒有測。 → ……Reels 分頁 JSON 有 12 筆，未登入的單支 Reel 頁沒有。
- 修正 factsTitle：第 5 項工時是答案檔的估算，不是題目內容，也不是實測。 → 題目、實測與答案估算
- 修正 notes[1]：「已改成」容易被讀成已部署。實際上只跑到 plan，沒有 apply，gcp.md 也把轉冷或刪除列為待決定。保留 7 天的是 qsearch-parser-sub 的設定。 → ……Terraform 設定改成 30 天轉 COLDLINE、預設不刪，但未 apply，轉冷或刪除仍待決定。parser 訂閱只保留 7 天，Seek 回不到 5/1。
- 修正 conflicts[本機 checkout 與路徑].b：git rev-parse HEAD 是 bf86b23。80adb29 是往前兩個 commit 的搬路徑 commit。比對路徑 diff 後，Q5 內容只改了路徑字串。 → 實際 HEAD 是 bf86b23（claude/project-thread-n1bwea），已合併兩個分支；80adb29 搬了路徑，例如 q5/b-c-d/README.md 改到 q5/answer/b-c-d.md。
- 修正 conflicts[SLA 抓取間隔假設].b：README 寫的是「若某帳號的輪詢間隔是 60 分鐘，最壞情況會剛好超過 SLA」。這是舉例，不是答案的假設。 → b-c-d (d)：舉例輪詢間隔 60 分鐘時，最壞會剛好超過 SLA；爬蟲週期超過 30 分鐘就警告。
- 修正 conflicts[IG 縮圖數字與筆數].a：01:52 那筆 manifest 的 result 只列 4 個數字，沒有寫 8 支。「8 支」出現在兩張截圖的標註和 11:04 的條目。真正的筆數矛盾在 q5-ig-reel-fields-json 條目，它寫「12 筆（Reels 分頁 8 支…）」，但檔案實際有 12 筆。看圖確認 2.3M 與 2.4M 都是 MARS 那支。 → 01:52 的 ig_reels_grid：MARS 那支顯示 2.3M；manifest 的 json 條目又寫「12 筆（Reels 分頁 8 支）」。
- 修正 evidence[threads-profile-header-logged-out.png].cap：打開圖確認，這張只有個人頁頁首和右側登入提示，看不到任何貼文或互動數字。互動數字在整頁圖 threads-profile-full-page.png。 → @threads 個人頁頁首：未登入可開，右側是登入提示
- 修正 evidence[threads-profile-full-page.png].cap：說明不完整：圖裡未登入就看得到每篇的讚、回覆、轉發、分享數字，這正是頁首圖原本想說明的事。 → @threads 個人頁整頁：未登入可見每篇互動數字（第一次拍攝）
- 修正 verify[3]：漏掉一個限制：plan 的映像都是 Cloud Run hello 範例（infra/gcp/README.md），services/parser 只 log 並 ACK（gcp.md）。所以「parser 加欄位」沒有任何程式驗證。 → plan 涵蓋 Q5 用到的 GCS、Pub/Sub fetched、parser、qsearch-backfill；映像都是 hello 範例，services/parser 只 log 並 ACK。ES、Data Layer、Analytics、Export 不在 Terraform 內（docs/handoff/gcp.md）。
- 修正 sources：HEAD 是 bf86b23，不是 80adb29。 → HEAD (bf86b23):q5/**（只比對路徑異動）
- 衝突「IG 觀看數欄位名」：題目、q5/README.md、int_nodes 的 es 節點、b-c-d mapping 都寫 video_view_count。／capture_manifest（10/30 更正）、playwright.md、ig_reel_fields.json：頁面 JSON 是 play_count，共 12 筆。。採用：主卡寫 play_count，並註明題目稱 video_view_count；實抓 JSON 是較新的實測。
- 衝突「Threads 轉發數定義」：題目、int_nodes、manifest 的 q5-threads-repost-visible：畫面轉發數就是 repost_count。／q5/README.md：畫面轉發＝repost_count＋quote_count，18 篇全部符合。。採用：採 q5/README.md，它逐篇對過 JSON；ES 存兩個分量，定義待 PM 確認。
- 衝突「ES mapping 新欄位」：int_nodes 的 es 節點：只加 video_view_count、repost_count 兩欄（long）。／b-c-d README：加 repost_count、quote_count、reshare_count 與觀看數欄位。。採用：採 b-c-d：畫面轉發要 quote_count，分享要 reshare_count。
- 衝突「回補路徑」：目前卡片 facts 與 int_nodes 的 replay 節點：讀 GCS，重發 fetched 事件給同一個 parser。／b-c-d README、backfill.py、FigJam 圖：回補程式直接產生 ES _bulk，寫新 index 再切 alias。。採用：採 b-c-d：有程式和 11 項測試；replay 節點的說明也要跟著改。
- 衝突「回補寫入規則」：FigJam 資料流圖：回補「只補空的」。／b-c-d README：external version＝crawled_at，較新的抓取才覆蓋，409 視為已套用。。採用：採 b-c-d：有測試覆蓋，也能防止舊資料蓋掉線上新值。
- 衝突「GCS 原始檔生命週期」：目前卡片 notes 與 int_nodes 的 gcs 節點：30 天轉 Archive。／storage.tf、infra/gcp/README.md、gcp.md、根 README：30 天轉 COLDLINE，刪除預設關閉。。採用：採 COLDLINE：這是 terraform plan 驗過的設定；轉冷或刪除仍待使用者決定。
- 衝突「SLA 抓取間隔假設」：目前卡片：依 Q4 每天 4 輪，每 6 小時抓一次。／b-c-d (d)：舉例輪詢間隔 60 分鐘時，最壞會剛好超過 SLA；爬蟲週期超過 30 分鐘就警告。。採用：保留 6 小時的推論，它直接來自題目；兩邊結論都是瓶頸在抓取頻率。
- 衝突「SLA 告警門檻」：目前卡片：P95 超過 60 分鐘就告警。／b-c-d (d)：P95 超過 45 分鐘警告、超過 60 分鐘呼叫；首次抓取率低於 95% 警告。。採用：採 b-c-d：它是答案檔，分級也更清楚。
- 衝突「回補示範退出碼」：python.md 總覽表：backfill.py 端到端示範退出 0。／python.md 第五節與 HANDOFF：示範沒留 log，退出碼未核對。。採用：repo 內寫未核對；主卡另註本次查核在暫存區重跑，退出碼 0，但 repo 沒有 log。
- 衝突「關鍵路徑工時」：b-c-d README：關鍵路徑列到驗收（第 9 項），寫 DE 42 小時。／同檔工時表：42 小時只加第 1、2、3、8 項，驗收的 4 小時沒算進去。。採用：主卡照答案寫 8 工作天、10 個日曆天（甘特圖把驗收放在第 8 天），另註 42 小時不含驗收。
- 衝突「IG 縮圖數字與筆數」：01:52 的 ig_reels_grid：MARS 那支顯示 2.3M；manifest 的 json 條目又寫「12 筆（Reels 分頁 8 支）」。／11:04 的 json_check 與 ig_reel_fields.json：同一支是 2,400,913，顯示 2.4M；檔案實有 12 筆，畫面可見 8 支。。採用：不是錯誤，兩次拍攝之間觀看數增加了；主卡用 11:04 那次，寫 JSON 12 筆、畫面 8 支。
- 衝突「目標平台節點」：capture_manifest：Q5 的 5 筆實抓都掛在 platforms 節點。／int_nodes 的 platforms：q 清單沒有 5，副標也沒有 Instagram。。採用：platforms 加上 5 與 Instagram，Q5 的佐證才有節點可掛。
- 衝突「a-dataflow 的 Threads 截圖來源」：playwright.md：q5/a-dataflow 的 Threads 圖是使用者提供的截圖與 FigJam 圖，不是本次 Playwright 抓的。／q5/README.md：threads-* 是 2026/10/29 用 Playwright 拍的，由 threads_verify.js 產生；只有 App 選單是使用者截圖。。採用：採 q5/README.md：有產生腳本；playwright.md 只是說不是那一輪拍的。
- 衝突「本機 checkout 與路徑」：任務說明：目前 checkout 等於 origin/master（717a237）。／實際 HEAD 是 bf86b23（claude/project-thread-n1bwea），已合併兩個分支；80adb29 搬了路徑，例如 q5/b-c-d/README.md 改到 q5/answer/b-c-d.md。。採用：佐證路徑以 origin/master 為準；若主卡改用 HEAD 的版面，要換成新路徑，內容只改了路徑。

## Q6

- 來源：複核後版本；佐證 35 張（面板 4 張）
- 修正 ask：官方題目 docs/handoff/qsearch_questions.md 的 Q6 有 (a)(b)(c) 三小題。原句只寫 (a)，漏掉 (b) 字典維護與共用（含 20+ 天與 10 天的提示）、(c) 串流一致與新詞重跑，也漏掉第五個症狀「搜尋高頻詞不在文字雲」與 600 字上限。 → 6 個渠道、2023/01 至今、ES 約 5 億筆；新舊斷詞並行給 PM 做 A/B；文字雲查詢 3 分鐘內；新資料走 Streaming 即時斷詞。舊字典有短網址、電話、助詞、地名被拆散、搜尋高頻詞不在文字雲等問題。(a) 回朔怎麼分批、批次間怎麼檢查，估 1 位資料工程師從 POC 到 6 渠道上線的工作天；(b) 字典怎麼維護、斷詞與搜尋字典要不要共用（提示：不同字典多 20+ 天，同字典約 10 天）；(c) 串流與回朔品質怎麼一致，加新詞要不要全部重跑。上限 600 字。
- 修正 facts[0]：數字正確，但「高」從 222 降到 0 是 segmenter.py 第 99 行丟掉長度小於 2 的詞造成，不是斷詞變好。RESULTS.md 同一張表的「高雄市」整詞是 6 與 6，沒有變化。python-verification 的 HANDOFF.md 也記「高雄市」未改善。 → 6,685 篇 PTT 標題實跑：前 50 詞停用詞 10 降到 0、純數字或符號 12 降到 0；「高雄市」整詞 6 次沒變（「高」降到 0 的原因移到 notes）
- 修正 facts[1]：研究頁寫 G4 的 PM 抽查只做各渠道第一個月，之後的月份 G1–G3 正常就自動放行。answer_600.txt 也寫「各渠道首月 PM 抽查」。說每批都過 G1–G4 不精確。 → 回朔以「渠道 × 月」切約 270 批、平均 185 萬篇，由新到舊；每批過閘門才放行，PM 只抽查各渠道首月
- 修正 facts[3]：150 核心小時是用「每篇平均 300 字」這個假設算的（研究頁算式 1.5e11 字），原句沒有標明，讀起來像實測。 → jieba 實測每核每秒 26–29 萬字；以每篇 300 字估約 150 核心小時；ES 假設每秒 3,000 篇，離峰約 6 晚
- 修正 verify[1]：python.md 只記兩支程式退出碼 0 與 RESULTS.md 自動產生，沒有提到 top50.json。top50.json 是 run_compare.py 原始碼寫出的檔案，不是 python.md 的紀錄。 → run_compare.py、affected_demo.py 皆退出碼 0，記錄在同一份 python.md。RESULTS.md 與 top50.json 都由 run_compare.py 寫出，兩份的 10、12、222 三個數字一致。
- 修正 evidence[docs/architecture/qsearch-architecture-q6.png].cap：打開圖片後確認，這是舊卡片的截圖，右欄仍寫「約 276 批」、「dict_version」與「高雄 1.9%、柯文哲 0.5%」，和新卡片矛盾。Cloud Storage 也沒有點亮。 → 總架構頁舊版 Q6 截圖：仍寫約 276 批與 dict_version
- 修正 conflicts[3].a：PLAN.md 原文是「改排該渠道該月的全量重跑」，範圍是一個分區，不是 5 億筆全量。 → PLAN.md：推測 total 改變有風險，抽 0.1% 比對，不一致超過 0.5% 就改排該渠道該月全量重跑
- 修正 conflicts[9].b：研究頁的假設清單把「原文可從 BigQuery 讀」標成推論，不是已確認的事實。 → 分支架構圖與研究頁：原文從 BigQuery / GCS 讀（Storage Read API），不從 ES scroll；研究頁把「從 BigQuery 讀」標為推論
- 修正 conflicts（缺漏）：int_nodes 的回補節點與 master 架構截圖畫的是「重發 fetched 事件給 parser」，分支圖則是回補 Job 自己斷詞、直接條件寫入 ES。PLAN.md 有 5 個檢查點，答案檔是 G1–G4。這兩處分歧原卡片都沒列。 → 新增「回補怎麼寫進 ES」與「每批品質檢查點」兩筆衝突，也新增「地名拆散有沒有改善」一筆。
- 衝突「工作天估算」：q6/code/PLAN.md 與 python.md：約 26–28 工作天（PLAN.md 假設每則 120 字、每核 1,000 則/秒）／q6/answer/answer_600.txt：45 工作天（多算字典整併 10 天、緩衝 7 天；每則 300 字、jieba VM 實測）。採用：顯示 45 天。答案檔較新，吞吐是實測，且依題目提示把同字典 10 天算進去。
- 衝突「回朔批次數」：PLAN.md、目前卡片與 master 架構截圖：6 × 46 約 276 批，每批約 180 萬則／answer_600.txt 與研究頁：約 270 批，平均 185 萬篇（研究頁：Threads 從 2023/07 起只有約 40 個月）。採用：顯示約 270 批、185 萬篇。答案檔扣掉了 Threads 上線前的月份。
- 衝突「每篇版本欄位名」：PLAN.md 與目前卡片路徑：每筆寫 dict_version／answer_600.txt 與分支架構圖：ES 新增 terms_v2、seg_ver，舊欄位 terms_v1 不動；dict_version 只是字典 artifact 的版本號。採用：顯示 terms_v2＋seg_ver。這是答案檔的命名，也和分支 9 張圖一致。
- 衝突「新詞會不會誤動其他文件」：PLAN.md：推測 total 改變有風險，抽 0.1% 比對，不一致超過 0.5% 就改排該渠道該月全量重跑／verify.log 實測：20 萬篇裡 52 篇不含新詞也變，固定 total 後為 0。採用：顯示 52 降到 0。分支已實測，處理方式是固定 total，不靠抽樣門檻。
- 衝突「全量重跑的機器時間」：PLAN.md：斷詞 8 核約 17 小時，限速後約 3–4 天背景執行／answer_600.txt：約 150 核心小時；ES 每秒 3,000 篇、離峰約 6 晚。採用：顯示約 6 晚。答案檔較新，寫入速度仍標為假設。
- 衝突「jieba 吞吐」：results.json：285,652 字/秒（10 萬篇）；答案寫 28 萬、研究頁 28.6 萬／verify.log 重跑：262,611 字/秒（5 萬篇）。採用：顯示 26–29 萬字，與分支 q6/README.md 相同，涵蓋兩次實測。
- 衝突「前 50 詞停用詞數」：master 根目錄 README.md：停用詞 9 降到 0／q6/code/RESULTS.md、q6/data/top50.json、python.md：10 降到 0。採用：顯示 10。top50.json 是程式自動產生的實測檔，三份來源一致。
- 衝突「地名拆散有沒有改善」：q6/code/RESULTS.md：單字「高」222 降到 0，標為地名被拆散的證據／segmenter.py 丟掉長度小於 2 的詞；「高雄市」整詞 6 次沒變；python-verification 的 HANDOFF.md 記「高雄市」未改善。採用：facts 只放停用詞與數字兩項，「高」降到 0 在 notes 註明是長度過濾造成。
- 衝突「dict.txt.big 是否已測」：PLAN.md、python.md 與 python-verification 的 HANDOFF.md：dict.txt.big 未取得，需核准／分支 verify.log 與 q6/README.md：已在 VM 下載實測（高雄市有、台積電沒有）。採用：顯示已實測。但要註明 6,685 標題對照仍用簡體內建字典。
- 衝突「斷詞放在哪個元件」：分支 diagram-overall：parser 之後的獨立 Cloud Run 斷詞服務，訂閱 parsed 主題／master README 與 int_nodes：承載於 parser 或回補 Job，沒有獨立節點。採用：master 用 parser 節點顯示，加註與回補 Job 同映像。master 圖不新增節點。
- 衝突「回補怎麼寫進 ES」：int_nodes 回補節點與 master 架構截圖：重發 fetched 事件給同一個 parser（Q5 做法）／分支 diagram-overall 與研究頁：回補 Job 用同一映像斷詞，bulk 條件寫入 terms_v2＋seg_ver。採用：Q6 路徑依分支圖寫回補 Job 直接寫 ES，回補節點說明加註 Q6 不經 parser。
- 衝突「回補讀原文的來源」：目前卡片：回補 Job 重放 GCS 原始檔／分支架構圖與研究頁：原文從 BigQuery / GCS 讀（Storage Read API），不從 ES scroll；研究頁把「從 BigQuery 讀」標為推論。採用：路徑寫 Cloud Storage，因為 int_nodes 沒有 BigQuery 節點。BigQuery 留在分支圖說明。
- 衝突「每批品質檢查點」：PLAN.md 與目前卡片：5 個檢查點（筆數對帳、dict_version、金標 200 則錯誤率 < 2%、異常 ±20%、寫入失敗 < 0.1%）／answer_600.txt 與研究頁：閘門 G1–G4；G4 的 PM 抽查只在各渠道首月，之後 G1–G3 正常就自動放行。採用：顯示 G1–G4。這是答案檔的版本，也和分支閘門圖一致。
- 衝突「ptt_noise_02 的圖說」：目前卡片：電話號碼（已遮）等斷詞雜訊／capture_manifest.json 與 playwright.md：02 是地名「鼓山區」，電話在 04。採用：依 manifest 改圖說，電話改用 ptt_noise_04。
- 衝突「pytest 狀態」：master README.md：pytest 由 python 代理執行中，本次未重跑／python.md：q6/code/tests 9 passed（退出碼 0）；python-verification 的 HANDOFF.md 記主代理已重跑確認。採用：顯示 9 passed。python.md 是之後補跑的紀錄。
