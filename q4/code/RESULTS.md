# Q4 Threads 量測程式：Linux 重測結果（2026-10-30）

## 結論

- 純 HTTP 回 SHELL，是因為請求少了 `Sec-Fetch-Mode`、`Sec-Fetch-Dest` 兩個導覽標頭。同一支程式不帶這兩個標頭時，3/3 個帳號都是 SHELL（0 串）；加上 `--sec-fetch` 後，3/3 個帳號都拿到 4 串。`shared/docs/python.md` 第四節的 SHELL 與 README 的「HTML 只內嵌 4 串」都對，只是條件不同。
- 未登入的 profile HTML 固定只內嵌 4 串，原因是 HTML 裡預排的 `BarcelonaProfileThreadsTabDirectQuery` 本身就帶 `first: 4`。程式算出的篇數是 nasa 5 篇、zuck 10 篇、natgeo 5 篇，每一篇的 URL、內文、發文時間都抽得到。篇數含串內回覆，也含被引用的貼文：natgeo 的 5 篇裡有 1 篇是 natgeo 自己的貼文所引用的 @natgeotv 貼文，natgeo 自己的串與回覆是 4 篇。
- 網頁自己送分頁 query（`BarcelonaProfileThreadsTabRefetchableDirectQuery`）時有時無。量測程式這天桌面 15 次載入都沒有送，其中只有最後 3 次確定把內層主欄捲到底；手機 3 次載入有 1 次（natgeo）。README 原本說「個人頁載入時網頁自己送」，依據的那張截圖在載入後還捲了 1 次 1,600 px 的滾輪，不是單純載入；幾分鐘前的第一版截圖腳本（手機視窗）捲了 5 次也沒收到帶貼文的 GraphQL 回應（`shared/capture/capture_log.txt` 第 11 行；第一版只記有貼文的回應，沒記 query 名稱）。合計截圖腳本兩次執行只有第二次看到（桌面 1/1、手機 1/2），量測程式桌面 0/15、手機 1/3。README 這句已改寫。
- natgeo 那次分頁 query 的 doc_id 是 `39825889313693314`、`first: 11`，回 11 串、17 篇。這個 doc_id 和 10-29 的 `39551525044493602`、10-30 凌晨的 `28880431481598345` 都不同，再次證實 doc_id 會換，不能寫死。存下的 HTML 裡只有這個 query 的模組名稱，沒有 doc_id 數字；doc_id 實際要怎麼取得，這次沒有驗證。
- 「5 req/s 約 80–100 次後 GraphQL 回 401」這次沒有重測。
- 第 4 題 600 字答案裡的數字，今天的證據沒有推翻任何一個，所以不改。答案以外的舊說明已更正（見「與既有說法的差異」）。2026-10-31 經確認，答案 (b) 末尾補一句「另 robots.txt 禁止未經書面許可自動蒐集」，見本檔最後一節。
- 對 threads.com 的瀏覽器開頁（`goto`）合計 21 次，沒有超過每站 30 次的上限。但手機視窗每次開頁，網站會自己再導覽一次；若把主框架文件都算進去，合計約 33 份（其中 9 份是推估），超過 30。

## 環境

| 項目 | 值 |
|---|---|
| OS | Linux 6.18.44（容器），對外連線經過容器的代理，出口 IP 沒有記錄 |
| Python | 3.13.17（`<tmp>/venv/bin/python`） |
| 套件 | requests 2.34.2、urllib3 2.8.0、playwright 1.64.1、pytest 9.2.0 |
| 瀏覽器 | Google Chrome for Testing 153.0.8010.12（`/opt/pw-browsers/chromium`），無頭 |
| 帳號 | nasa、zuck、natgeo（公開機構與公眾人物帳號） |
| 條件 | 不登入、每個帳號開新的 browser context、用完即關（不存 cookie），不用 stealth 或指紋偽裝 |

Playwright 1.64.1 預設要找 `chromium_headless_shell-1248`，這台只裝了 1243 版，直接 `chromium.launch()` 會失敗（#7）。程式因此加了 `--chromium`（或環境變數 `Q4_CHROMIUM`）來指定執行檔；兩個都沒給時，行為和以前一樣。

## 指令與退出碼

共同前綴（下表簡寫為「前綴」）：

```
cd <repo> && PYTHONDONTWRITEBYTECODE=1 <tmp>/venv/bin/python q4/code/threads_q4_measure.py \
  --accounts <tmp>/work/q4/accounts.txt --delay 6
```

`--out` 一律指向 `<tmp>/work/q4/final/<名稱>`，原始 HTML 不進 repo。#1–#5 是用 `<tmp>/work/q4/final/run.sh <名稱> <參數>` 跑的：它執行上面這一行，stdout、stderr 分別存成 `<名稱>.stdout`、`<名稱>.log`，緊接著用 `echo "exit=$?"` 把退出碼寫進 `<名稱>.exit`。

| # | 時間（UTC） | 指令 | 退出碼 | 結果 |
|---|---|---|---|---|
| 1 | 12:29:47–12:30:06 | 前綴 `--mode http` | 0 | 3/3 SHELL |
| 2 | 12:30:06–12:30:29 | 前綴 `--mode http --sec-fetch` | 0 | 3/3 OK，各 4 串 |
| 3 | 12:30:42–12:32:09 | 前綴 `--mode browser --device desktop --chromium /opt/pw-browsers/chromium` | 0 | 中間版程式：視窗捲不動時改用 `window.scrollTo`，但桌面版是內層捲動區，等於沒捲。結果由 #5 取代 |
| 4 | 12:33:05–12:34:37 | 前綴 `--mode browser --device mobile --chromium /opt/pw-browsers/chromium` | 0 | 3/3 成功；natgeo 收到分頁 query |
| 5 | 12:34:54–12:36:20 | 前綴 `--mode browser --device desktop --chromium /opt/pw-browsers/chromium` | 0 | 3/3 成功；沒有分頁 query |
| 6 | 12:37:17–12:37:19 | `curl -sS -o <tmp>/work/q4/dl_robots_final/robots.txt -w "status=%{http_code} bytes=%{size_download}\n" https://www.threads.com/robots.txt` | 0 | `status=200 bytes=8506` |
| 7 | 12:39:10–12:39:12 | 見下方程式碼區塊 | 0 | 預設 `chromium.launch()` 失敗：`Executable doesn't exist at /opt/pw-browsers/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell`；指定 `/opt/pw-browsers/chromium` 成功（153.0.8010.12） |
| 8 | 12:43:59–12:44:09 | pytest（當時 11 個測試） | 0 | 11 passed |
| 9 | 22:33:51–22:34:14 | pytest（審查修正後，指令見「pytest 結果」） | 0 | 16 passed |
| 10 | 22:34:22–22:34:23 | `PYTHONDONTWRITEBYTECODE=1 PLAYWRIGHT_BROWSERS_PATH=<tmp>/work/q4/fix2/empty_pw <tmp>/venv/bin/python -m pytest -q -rs -p no:cacheprovider q4/code/tests` | 0 | 13 passed、3 skipped（找不到 Chromium 時的行為） |
| 11 | 22:34:23 | `PYTHONDONTWRITEBYTECODE=1 HTTPS_PROXY=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 <tmp>/venv/bin/python q4/code/threads_q4_measure.py --mode http --delay 0 --out q4/data/threads_q4_out` | 2 | `results.json 已經存在，不覆蓋`；stdout 是空的，沒有開新目錄，舊檔 sha256 不變（`5926a514…`） |
| 12 | 22:26:45 | `cd <tmp>/work/q4/fix2 && PYTHONDONTWRITEBYTECODE=1 <tmp>/venv/bin/python -I replay_dump.py > replay_dump.log 2>&1` | 0 | 重放結果與 commit 進 repo 的 12:43:57 版逐位元組相同；12:32:37 與 12:43:57 兩版只差 1 行註解 |
| 13 | 22:23（結束於 22:23:46） | `cd /tmp && PYTHONDONTWRITEBYTECODE=1 timeout 120 <tmp>/venv/bin/python -I <tmp>/work/q4/fix2/navs_probe.py /opt/pw-browsers/chromium > <tmp>/work/q4/fix2/navs_probe.log 2>&1` | 0 | 本機假站：單純一頁 `navigations` 1 筆、文件 1 份；載入後 `history.replaceState` 一次是 2 筆、1 份；JS 轉址加 302 是 2 筆、3 份 |
| 14 | 2026-10-31 04:03:38–04:03:59 | `PYTHONDONTWRITEBYTECODE=1 <tmp>/venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=<tmp>/work/q4ans/pt14 q4/code/tests > <tmp>/work/q4ans/q4_pytest_1031.log 2>&1`（補答案字數測試 `test_q4_answer_length.py` 後） | 0 | 17 passed |
| 15 | 2026-10-31 04:24:05–04:24:29 | 同 #14，`--basetemp=<tmp>/work/q4ans/pt15`，log 是 `q4_pytest_1031b.log`（字數測試只改說明之後） | 0 | 17 passed |

#9–#15 都在指令後接 `echo "exit=$?"`，把退出碼寫進同名 log 的最後一行（#14、#15 的 log 在 `<tmp>/work/q4ans/`）；#9–#11、#14、#15 從 `<repo>` 執行，#11 把代理指到關著的本機埠，就算程式沒擋住也連不出去。#9–#11 在 22:28 也跑過一次（註解改用「DOM」用詞之前），結果相同，log 被 22:33 那次的同名檔覆蓋；審查修正中斷前（17:39–17:51 UTC）也各跑過，結果相同（pytest 3 次都是 16 passed），log 在 `<tmp>/work/q4/fix/`。

用 `-I` 跑、又載入 `threads_q4_measure.py` 的 scratch 腳本（#13 與重算被引用貼文）會忽略 `PYTHONDONTWRITEBYTECODE`，在 `q4/code/__pycache__/` 留下 .pyc。這個目錄被根目錄 `.gitignore` 擋住，不會進 repo，已刪除；改用 `-I -B` 實測不會再寫。

#7 的完整指令：

```
echo "PWB=$PLAYWRIGHT_BROWSERS_PATH"; cd /tmp && timeout 60 <tmp>/venv/bin/python -I -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    try:
        b = p.chromium.launch(); print('default launch ok', b.version); b.close()
    except Exception as e:
        print('default launch failed:', str(e).splitlines()[0][:200])
    b = p.chromium.launch(executable_path='/opt/pw-browsers/chromium'); print('explicit ok', b.version); b.close()
"; echo exit=$?
```

#6、#7、#8 的輸出當時沒有另存成檔案，是從當時的工作紀錄（不在 repo 內）找回的，摘錄在 `<tmp>/work/q4/fix/transcript_excerpts.txt`。

### 前一次被中斷的嘗試（07:46–07:57 UTC）

同一天稍早有一次被中斷的嘗試，輸出留在 scratch。上一版說「完整指令沒有留存」，其實工作紀錄裡有，這次找回。下面只留跑 curl 與 Python 的部分，照原文抄錄；原本的複合指令前後還有 `cd`、`mkdir`、`date`、`sleep`、`cat`，完整原文在 `transcript_excerpts.txt`。工作目錄是 `<tmp>/work/q4`，跑 Python 前都先 `export PYTHONDONTWRITEBYTECODE=1`，`S=<repo>/q4/code/threads_q4_measure.py`：

```
# 07:46:53
curl -sS --max-time 20 -o dl_robots/robots.txt https://www.threads.com/robots.txt; echo exit=$?
# 07:50:02（07:51:32 修好 wire 之後，同樣兩行再跑一次）
<tmp>/venv/bin/python -I $S --mode http --accounts accounts.txt --delay 3 --out <tmp>/work/q4/run_http > run_http.stdout 2> run_http.log; echo exit=$? | tee run_http.exit
<tmp>/venv/bin/python -I $S --mode http --sec-fetch --accounts accounts.txt --delay 3 --out <tmp>/work/q4/run_http_secfetch > run_http_secfetch.stdout 2> run_http_secfetch.log; echo exit=$? | tee run_http_secfetch.exit
# 07:52:07
<tmp>/venv/bin/python -I $S --mode browser --device desktop --chromium /opt/pw-browsers/chromium --accounts accounts.txt --delay 4 --out <tmp>/work/q4/run_browser_desktop > run_browser_desktop.stdout 2> run_browser_desktop.log; echo exit=$? | tee run_browser_desktop.exit
# 07:54:12
for dev in desktop mobile desktop-1440; do sleep 4; date -Is > run_browser_$dev.time; if [ $dev = mobile ]; then Q4_CHROMIUM=/opt/pw-browsers/chromium <tmp>/venv/bin/python -I $S --mode browser --device $dev --accounts accounts.txt --delay 4 --out <tmp>/work/q4/run_browser_$dev > run_browser_$dev.stdout 2> run_browser_$dev.log; else <tmp>/venv/bin/python -I $S --mode browser --device $dev --chromium /opt/pw-browsers/chromium --accounts accounts.txt --delay 4 --out <tmp>/work/q4/run_browser_$dev > run_browser_$dev.stdout 2> run_browser_$dev.log; fi; echo "$dev exit=$?" | tee run_browser_$dev.exit; cat run_browser_$dev.time run_browser_$dev.log; done
```

07:47:05 另跑過一次和 #7 一樣的啟動檢查（`2>&1 | tail -5; echo exit=${PIPESTATUS[0]}`），結果相同。

| 時間（UTC） | 內容 | 退出碼 | 結果 |
|---|---|---|---|
| 07:46:53 | robots.txt | 0 | 狀態碼沒有記；內容與 #6 相同（sha256 一樣） |
| 07:50:02 | HTTP、HTTP `--sec-fetch` | 0、0 | SHELL 3/3、OK 3/3，但 `wire_bytes` 全是 0（當時程式的錯），所以重跑 |
| 07:51:32 | 同上，重跑 | 0、0 | 與 #1、#2 相同：不帶 Sec-Fetch 全 SHELL，帶了各 4 串 |
| 07:52:07 | 瀏覽器 desktop | 0 | 3/3 成功，沒有分頁 query |
| 07:54:12 | 瀏覽器 desktop、mobile、desktop-1440 | 0、0、0 | 桌面 6/6 沒有分頁 query。手機 3/3 都是 error：`Response.text: Protocol error (Network.getResponseBody): No resource with given identifier found`，這是當時程式的錯（見「程式修改」），已修好並由 #4 重跑 |

### 程式版本

#4、#5 跑完之後，程式檔在 12:43:57 又改過一次，所以光看檔案無法確定 commit 進去的就是當時跑的那一份。這次照工作紀錄的順序重放每一次編輯：整檔寫入與逐段取代直接套用；shell 裡的 python 取代只用 `ast` 解析出字串常數再做取代，不執行紀錄裡的程式。重放的最後結果和 commit 進 repo 的檔案逐位元組相同，所以下表就是各輪實際跑的版本：

| 版本（UTC） | 改了什麼 | sha256 | 跑了哪些 |
|---|---|---|---|
| 07:49:28 | 前一次嘗試寫的版本 | `fffc5f2c9fe75c757e9b096d7ded8a89af2e2f6a3f1a597e8e1f79f2171406a1` | 07:50:02 的 HTTP |
| 07:51:11 | 壓縮後大小改用 `r.raw.read(decode_content=False)` | `c9ef6221114b38a367cb97e81a05e69b0693c106cd8b09d02681476e27870b90` | 07:51:32 的 HTTP、07:52:07 的桌面 |
| 07:54:02 | 視窗設定改成 `DEVICES` 表；捲動前把滑鼠移到畫面中間 | `80e5450be2a3940a0f7a40d207a39e0322b12216c4b5dab37b1d13adccf53806` | 07:54:12 的桌面、手機、桌面 1440 |
| 12:29:03 | 第一份 HTML 改在 response 事件當下讀；加 `html_summary`、`url_shape`、捲動紀錄 | `58bc5edd58ad77b439d9bf1236b302789a5923152971aac035c141852663cb24` | #1–#3 |
| 12:32:37 | 視窗捲不動時改捲內層捲動區；dialog 記前 60 字 | `4777578f8411b081d2bd7290528af1ecf4d6dc24e182bfe28a515ca8dfd2aedf` | #4、#5 |
| 12:43:57 | 只改 `document_wire_bytes` 那一行註解 | `a1cdd2f4e749e3a6c429c16f8d379d2915575b32a273719dc807fa7c0cc9d0f2` | commit 進 repo 的版本；#8 |
| 審查修正，中斷前（17:51 UTC 以前） | 見「程式修改」 | `ced4fb0d45a13d10309d16741db9f55df77a01bf1d8d111cd356bd6312b1d3e9` | 中斷前的 pytest；22:23 的 pytest 與 #13；沒有再連 Threads |
| 審查修正，本次 | 只改註解與模組 docstring：`framenavigated` 記的是什麼；「畫面」改稱「渲染後 DOM 裡的貼文連結」 | `eeb94f64fc69e25bb0084591c0cbf8f7d89de29fb7effbef9e9d3c6a2cee3f51` | #9–#11；沒有再連 Threads |

#1、#2 只走 HTTP；12:32:37 那次只改瀏覽器的部分，不影響它們。重放程式是 `<tmp>/work/q4/fix/replay_edits.py`，輸出在同目錄的 `replay_edits.log`；#12 用同一套重放把每一版存成文字檔再比對，12:32:37 與 12:43:57 的差異存在 `<tmp>/work/q4/fix2/diff_1232_1243.txt`。本次只改註解與 docstring：把本次最早加的兩處註解改回去，可以重建出 `ced4fb0d…` 那一版（sha256 相符，`<tmp>/work/q4/fix2/versions/ced4fb0d_reconstructed.txt`）；它和現行檔案拿掉 docstring 後的 AST 相同（`ast_check.log`），逐行差異在 `diff_ced4_final.txt`。所以本次沒有改到程式行為。

### 流量

| | HTTP 請求 | 瀏覽器開頁（goto） | 主框架導覽（goto＋網站自己的一次 JS 轉址） | 主框架文件 |
|---|---|---|---|---|
| 前一次嘗試 | 12 ＋ robots.txt 1 | 12 | 15 | 桌面 9 份有紀錄；手機 3 次沒有紀錄（推估 9 份），合計推估 18 份 |
| 這次 | 6 ＋ robots.txt 1 | 9 | 12 | 15 |
| 合計 | 20 | 21 | 27 | 有紀錄 24 份，推估 33 份 |

- 手機視窗每開一頁，網站會自己多導覽一次：先拿到 `/@帳號` 的 200，網頁接著自己導到 `/aymh/?next`，該網址回 302 轉回 `/@帳號?hpir&http_ref`。所以手機每次開頁是兩次導覽、三份文件（200、302 與轉回的 200）；桌面是一次導覽、一份文件。前一次嘗試的手機 3 次因為程式錯誤沒有記錄，導覽與文件數是照這個流程推估的（錯誤訊息寫著回應已經被導覽離開，表示至少轉走了一次）。
- 進 repo 的 JSON 裡的 `navigations` 不是導覽次數，是 Playwright 的 `framenavigated` 事件：同一份文件用 history API 改網址也會記一筆，302 的中間網址不會記。這輪桌面每次 2 筆、手機每次 5 筆，文件則分別是 1 份、3 份。#13 用本機假站證實了這個記法（單純一頁 1 筆；載入後 `history.replaceState` 一次變 2 筆、文件仍是 1 份；JS 轉址加 302 是 2 筆、3 份文件）。上表的導覽數是 goto 加上手機那一次 JS 轉址，不是 `navigations` 的筆數。
- HTTP 18 次（不含 robots.txt）都是 200。瀏覽器有紀錄的 18 次開頁都是 200；前一次嘗試的手機 3 次沒有記到狀態。requests 會自動跟隨轉址，這幾輪沒有記錄轉址次數，回應大小與內容都正常，推測沒有轉址；程式現在多記 `redirects`。
- 間隔：這次帳號之間等 6 秒；前一次嘗試 HTTP 等 3 秒、瀏覽器等 4 秒。瀏覽器每個帳號本身要 15.8–25.4 秒，所以同一站兩次開頁至少相隔約 20 秒。
- 每站 30 次的上限：以開頁（goto）算是 21 次，沒有超過；以主框架文件算約 33 份，超過了。

## pytest 結果

```
cd <repo> && PYTHONDONTWRITEBYTECODE=1 <tmp>/venv/bin/python -m pytest -q -p no:cacheprovider q4/code/tests
```

16 passed，退出碼 0（#9，22:33:51–22:34:14 UTC，22.25 秒，log：`<tmp>/work/q4/fix2/pytest_final.log`）。這是程式與測試檔都改完之後跑的，當時的 sha256 是 `threads_q4_measure.py` `eeb94f64…`、`test_threads_smoke.py` `40548a4b…`（`<tmp>/work/q4/fix2/code_sha_at_pytest_final.txt`），之後只改文件；文件改完後（22:35:11–22:35:33）再跑一次，16 passed、退出碼 0，程式與測試檔的 sha256 和 #9 相同（`pytest_end.log`、`code_sha_at_pytest_end.txt`）。這次動手前先跑過一次（22:23:04–22:23:34，16 passed，`pytest_baseline.log`），22:28 改用詞前又跑過一次（16 passed，23.19 秒，log 已被覆蓋），中斷前的修正也跑過 3 次，都是 16 passed。10-30 重測前的版本只有 2 個測試，10-30 12:43 commit 的版本是 11 個，10-30 這輪結束時是 16 個，都在 `test_threads_smoke.py`：

- 13 個是純離線測試：內嵌 JSON 解析、被引用貼文、GraphQL 摘要、壓縮後與解壓後大小、預排 query 與分頁資訊、`main()` 的參數與輸出目錄。
- 3 個用本機 127.0.0.1 的假站跑 Chromium，不連外網：「302 轉址＋內層捲動＋捲到底送 GraphQL」的流程用桌面、手機兩種視窗設定各跑一次，並檢查 `navigations` 只有 2 筆（302 的中間網址不記）；另一個是沒有內層捲動區時退回 `window.scrollTo`，同一頁載入後用 `history.replaceState` 改一次網址，檢查 `navigations` 變 2 筆、文件仍是 1 份。找 Chromium 的順序是 `Q4_CHROMIUM`、Playwright 自己的預設執行檔、`PLAYWRIGHT_BROWSERS_PATH` 底下的 `chromium` 連結；都找不到就 skip。把 `PLAYWRIGHT_BROWSERS_PATH` 指到空目錄實測（#10）：13 passed、3 skipped，退出碼 0。

2026-10-31 經確認在答案補 robots.txt 一句，同時加 `test_q4_answer_length.py`（1 項：答案段連空白與換行 ≤ 600 字、與標題行寫的兩個字數一致、含 robots.txt），現在共 17 個。有 log 的兩次都是 17 passed、退出碼 0（#14、#15，指令與 log 見指令表）；在 #14 之前，04:02:21–04:02:59 單跑 Q4（`--basetemp` 是 `pt_q4`，17 passed）、04:03:01–04:03:23 和 Q1 一起跑（`pt_both`，62 passed）各一次，沒有留 log；更早一次把新測試檔命名為 `test_answer_length.py`，和 Q1 的同名測試檔一起收集時衝突，因此改名；`threads_q4_measure.py` 與 `test_threads_smoke.py` 沒有改，sha256 仍是 `eeb94f64…`、`40548a4b…`。

## 量測結果

### HTTP（requests，`Accept-Encoding: gzip, deflate`）

| 帳號 | Sec-Fetch | 狀態 | 壓縮後 | 解壓後 | 串數 | 篇數 | 本人 | 被引用 | 三欄齊全 | 判定 |
|---|---|---|---|---|---|---|---|---|---|---|
| nasa | 無 | 200 | 58,564 B | 258,277 B | 0 | 0 | 0 | 0 | 0 | SHELL |
| zuck | 無 | 200 | 58,517 B | 258,238 B | 0 | 0 | 0 | 0 | 0 | SHELL |
| natgeo | 無 | 200 | 58,524 B | 258,238 B | 0 | 0 | 0 | 0 | 0 | SHELL |
| nasa | 有 | 200 | 178,458 B | 946,447 B | 4 | 5 | 5 | 0 | 5 | OK |
| zuck | 有 | 200 | 203,384 B | 1,206,757 B | 4 | 10 | 10 | 0 | 10 | OK |
| natgeo | 有 | 200 | 167,858 B | 848,792 B | 4 | 5 | 4 | 1 | 5 | OK |

「被引用」欄是這次用現行程式重讀 scratch 裡存下的 HTML 算的，進 repo 的 JSON 沒有這一欄（log：`<tmp>/work/q4/fix/recount.log`）。natgeo 那 1 篇嵌在 natgeo 自己某篇貼文的 `text_post_app_info.share_info.quoted_post` 底下，是被引用的 @natgeotv 貼文，不是轉發，也不是獨立的一串，但程式會把它算成 1 篇。

帶 Sec-Fetch 的 3 份 HTML 都有：

- `mediaData.page_info`：`has_next_page` 為 true，`end_cursor` 長 120 字元（值沒有存）。
- 預排 query：`BarcelonaProfileThreadsTabDirectQuery`（queryID `28967344372926951`，`first: 4`）與 `BarcelonaProfilePageDirectQuery`（queryID `28894437973498594`）。variables 裡有 `userID`，代表 userID 可以從 HTML 取得。
- 伺服器回的 HTML 已經含「Log in to see more from 帳號.」這行字。

### 瀏覽器（Playwright，#4、#5）

| 帳號 | 視窗 | 狀態 | 文件壓縮後 | 文件解壓後 | HTML 內嵌串／篇 | DOM 貼文連結：載入後（本人） | DOM 貼文連結：捲動後（本人） | 網頁自己送的 GraphQL | 整頁請求數 | 整頁傳輸 |
|---|---|---|---|---|---|---|---|---|---|---|
| nasa | 桌面 1280×720 | 200 | 154,279 B | 949,061 B | 4／5 | 5（5） | 5（5） | `fetchBarcelonaExperimentQuery` ×1 | 47 | 2,680,515 B |
| zuck | 桌面 1280×720 | 200 | 168,453 B | 1,202,617 B | 4／10 | 10（10） | 10（10） | `fetchBarcelonaExperimentQuery` ×1 | 52 | 2,695,453 B |
| natgeo | 桌面 1280×720 | 200 | 152,741 B | 846,771 B | 4／5 | 5（4） | 5（4） | `fetchBarcelonaExperimentQuery` ×1 | 39 | 2,513,462 B |
| nasa | 手機 390×844 | 200 | 152,341 B | 1,004,607 B | 4／5 | 5（5） | 5（5） | `fetchBarcelonaExperimentQuery` ×2 | 92 | 2,930,413 B |
| zuck | 手機 390×844 | 200 | 164,859 B | 1,275,840 B | 4／10 | 10（10） | 10（10） | `fetchBarcelonaExperimentQuery` ×2 | 106 | 3,548,588 B |
| natgeo | 手機 390×844 | 200 | 147,570 B | 846,469 B | 4／5 | 5（4） | 22（18） | `fetchBarcelonaExperimentQuery` ×2，`BarcelonaProfileThreadsTabRefetchableDirectQuery` ×1 | 98 | 3,084,172 B |

- 「文件」指瀏覽器收到的第一份原始 HTML，壓縮方式由 Chromium 自己協商（沒有記錄是 br 還是 zstd），所以比 #2 的 gzip 小。`fetchBarcelonaExperimentQuery` 每次都是 200、解壓後 216 B、0 串，只是實驗旗標查詢。
- 「DOM 貼文連結」數的是渲染後 DOM 裡 `/@帳號/post/{code}` 連結的不重複 code，不管有沒有捲進視窗、有沒有被對話框蓋住，所以不等於截圖裡看得到的篇數（例如 `threads_profile_nasa.png` 只看得到 1 篇）。含被引用貼文的連結，括號是帳號本人的。natgeo 載入後的 5 個連結裡有 1 個是被引用的 @natgeotv 貼文。

natgeo 手機那次分頁 query 的內容：

| 項目 | 值 |
|---|---|
| friendly name | `BarcelonaProfileThreadsTabRefetchableDirectQuery` |
| doc_id | `39825889313693314` |
| 狀態 | 200 |
| 觸發時機 | 回應在捲動階段第 1 步收到。第 1 步先送滾輪、等 1 秒，再用 JS 把主欄捲到底；JS 捲動當下主欄已經是 8,224 px（nasa 手機 5 篇是 2,636 px），表示下一頁在 JS 捲到底之前就載入了。觸發的是滾輪、滑鼠移動還是網頁自己的計時，分不出來，因為程式只記回應落在哪個階段，沒有記請求送出的時間。載入階段結束時 DOM 裡還是 5 個連結 |
| variables 鍵名 | `after`、`allow_page_info_for_lox_user`、`before`、`first`、`last`、`userID`。程式不記 `__relay_internal__` 開頭的旗標鍵，所以這次請求有沒有這類旗標不知道（10-29 的 verification §2 看到 45 個） |
| first、after | `first` 是 11；`after` 不是空的（`after_is_null` 為 false），值沒有存。它接在 HTML 那 4 串的 cursor 後面是推論：DOM 裡 5 個連結加上回應的 17 篇剛好是 22 個，沒有重複 |
| 回應 | 11 串、17 篇，17 篇三欄齊全，`has_next_page` 為 true。17 篇可能含被引用的貼文，回應本文沒有存，分不出來；DOM 裡本人的連結由 4 個變 18 個 |
| 大小 | 壓縮後 24,623 B，解壓後 215,228 B |

其他觀察：

- 手機視窗每次載入都會經過網站自己的轉址（見「流量」）。前一次被中斷的嘗試裡，程式在頁面轉走之後才讀第一份 HTML，所以手機模式 3/3 失敗。
- 桌面版的視窗本身不能捲（`scrollHeight` 等於視窗高 720），主欄是內層捲動區。#5 把內層捲到底（nasa 1,638 px、zuck 2,876 px、natgeo 1,387 px）之後沒有新貼文。滾輪本身沒有讓主欄移動：#4、#5 共 6 次載入，第 1 步 JS 捲動前內層捲動區的位置都是 0。#3 與前一次嘗試的 12 次桌面載入只有滾輪（#3 另加 `window.scrollTo`），沒有證據顯示主欄有被捲動。
- 畫面上有一個 `role=dialog`：桌面是「Say more with Threads」註冊提示，手機是「Thread on Threads」開 App 提示。
- 三個欄位：HTML 內嵌 JSON 每一篇都有 URL、內文、時間；GraphQL 回應 17/17；DOM 裡每個貼文連結都有 `<time>`。
- 程式的 `inline_*` 欄位（進 repo 的 JSON 裡是舊名 `posts_before_scroll`、`posts_after_scroll` 等）量的是 `page.content()` 裡的內嵌 JSON，不是渲染後的貼文連結：natgeo 手機那次 DOM 裡的連結由 5 個變 22 個，內嵌 JSON 仍是 5 篇。10-27 的舊 `results.json` 也是這樣算，而且當時的 `scroll_loaded_more` 是比內嵌 JSON 的篇數，就算網頁確實載入下一頁也看不出來。
- HTML 預排的 `BarcelonaProfilePageDirectQuery` 在 07:50–07:57 存下的 HTML 裡是 `39586378077644222`，12:30 變成 `28894437973498594`；`BarcelonaProfileThreadsTabDirectQuery` 兩個時段都是 `28967344372926951`（07:50 的值是用現行程式重讀 scratch 裡的 HTML 得到的，log：`<tmp>/work/q4/fix/preloaded.log`）。

## 輸出與 log 位置

| 內容 | 位置 | 進 repo |
|---|---|---|
| #1、#2、#5、#4 的 results.json（原樣複製） | `q4/data/threads_q4_out/rerun_2026-10-30_linux/http.json`、`http_secfetch.json`、`browser_desktop.json`、`browser_mobile.json` | 是 |
| 10-27 的舊量測（wire 欄位其實是解壓後大小） | `q4/data/threads_q4_out/results.json`，未更動（sha256 `5926a514ecd3e9cb8600618c7490959576ae28a29dae996c7664cab138414ae7`） | 是（原本就在） |
| 原始 HTML、results.json、stderr log、時間與退出碼 | `<tmp>/work/q4/final/<名稱>/`、`<名稱>.log`、`.stdout`、`.exit`、`.time` | 否 |
| 前一次嘗試的輸出 | `<tmp>/work/q4/attempt1/`、`<tmp>/work/q4/run_*` | 否 |
| robots.txt | `<tmp>/work/q4/dl_robots_final/robots.txt`，sha256 `3230a25a1f3c2001a34d8038afe383660a22c0119f37f32bef040d44f0210e44`（07:46 抓的 `dl_robots/robots.txt` sha256 相同） | 否 |
| 審查修正中斷前的證據：工作紀錄摘錄、程式重放、重算與 pytest 的 log | `<tmp>/work/q4/fix/`（`transcript_excerpts.txt`、`replay_edits.py`、`replay_edits.log`、`recount.log`、`preloaded.log`、`pytest_final.log`、`pytest_end.log`、`pytest_nochromium.log`、`refuse.log`） | 否 |
| 審查修正本次的證據：#9–#13 的 log 與 `.time`、流量重算（`traffic.py`、`traffic.log`）、各版程式（`versions/`，含重建的 `ced4fb0d_reconstructed.txt`）、`diff_1232_1243.txt`、`ast_check.log`、`diff_ced4_final.txt`、用現行程式重算被引用貼文（`recount.log`，與中斷前相同）、文件改完後的 pytest（`pytest_end.log`）、`code_sha_at_pytest_final.txt`、`code_sha_at_pytest_end.txt` | `<tmp>/work/q4/fix2/` | 否 |

進 repo 的 4 個 JSON 共 34,197 B，只有計數、大小、狀態、query 名稱與 doc_id，不含 token、cookie、cursor 或 userID 的值。它們是審查修正前的程式產生的，所以沒有 `quoted_posts`、`redirects`，瀏覽器欄位也還是舊名。

## 程式修改（`threads_q4_measure.py`）

上一版（10-30 12:43 的 commit，相對 10-30 重測前的版本）：

- `--sec-fetch`：HTTP 模式多帶 `Sec-Fetch-Mode: navigate`、`Sec-Fetch-Dest: document`。
- `wire_bytes` 改成壓縮後的實際大小（`r.raw.read(decode_content=False)`），`html_bytes` 是解壓後大小。舊版兩個欄位都是解壓後。
- `--device`（desktop、desktop-1440、mobile）與 `--chromium`／`Q4_CHROMIUM`。
- 瀏覽器模式記錄網頁自己送的 `/graphql`：friendly name、doc_id、狀態、大小、串數、篇數，variables 只記鍵名、`first` 與 `after` 是否為空。
- 第一份 HTML 改在 `response` 事件當下讀取，並記下每份主框架文件的狀態與網址形狀（`url_shape` 只留路徑與 query 鍵名）。10-30 重測前的版本沒有手機模式，讀的是 `page.content()`；「轉址後才呼叫 `resp.text()`」這個錯是前一次被中斷的嘗試在 07:49 寫進去的，commit 前已經修好，沒有進過 repo。
- 捲動時，視窗不動就改捲最大的內層捲動區，並逐步記下捲動位置、DOM 貼文連結數、GraphQL 累計數。
- `html_summary`：HTML 的分頁資訊（`has_next_page`、cursor 長度）與預排 query（`preloaded_queries`），HTTP 與瀏覽器共用。
- 畫面量測多記 `scroll_height`、視窗高度、dialog 的前 60 個字。

這次審查修正（相對上一版）：

- 被引用的貼文：`posts_in_json` 把嵌在 `share_info` 底下的貼文標成 `quoted`（同一篇只要在別處出現過就不算），`post_stats` 與 GraphQL 摘要多記 `quoted_posts`。帳號自己的串與回覆是 `posts − quoted_posts`。
- 瀏覽器的 `posts_before_scroll` 等四個欄位改名為 `inline_posts_before_scroll` 等，並註明量的是內嵌 JSON，不是渲染後的貼文連結。
- `--out` 預設改成 `q4/data/threads_q4_out/run_<UTC 時間>/`；目錄裡已經有 `results.json` 就以退出碼 2 拒絕。實測 `--out q4/data/threads_q4_out --mode http`：退出碼 2，沒有發出請求（命令列把代理指到 127.0.0.1:9 防止意外連線），舊 `results.json` 的 sha256 不變。另加 `q4/data/threads_q4_out/.gitignore` 擋住子目錄裡的原始 HTML，根目錄的 `.gitignore` 只擋這一層。
- HTTP 模式多記 `redirects`（`len(r.history)`）。
- `main()` 接受 `argv`，測試可以直接呼叫。
- `document_wire_bytes` 的註解改正：手機視窗有 200、302、200 三份文件，不是兩份。
- `framenavigated` 那一行加註解：`navigations` 的筆數不是導覽次數（見「流量」）。
- 註解與 docstring 把「畫面」改稱「渲染後 DOM 裡的貼文連結」：`RENDERED_JS` 數的是 DOM 裡的連結，被對話框蓋住或還沒捲進視窗的也算。

測試（`q4/code/tests/test_threads_smoke.py`）相對 10-30 重測前的版本從 2 個增加到 16 個。這次新增：被引用貼文的計數、瀏覽器流程的手機視窗版、沒有內層捲動區時退回 `window.scrollTo`、`main()` 把 `--chromium`／`Q4_CHROMIUM` 傳給 `browser_fetch` 並開新的輸出目錄、`main()` 拒絕覆蓋既有的 `results.json`、`navigations` 在轉址與 `history.replaceState` 時各記幾筆。瀏覽器測試不再寫死 `/opt/pw-browsers/chromium`。

## 與既有說法的差異／修正

| 既有說法（出處） | 證據 | 處理 |
|---|---|---|
| `shared/docs/python.md` §四：純 HTTP 抓 @nasa 得 0 篇（SHELL），與 README「HTML 只內嵌 4 串」不一致 | 不帶 Sec-Fetch 3/3 SHELL；帶 Sec-Fetch 3/3 各 4 串；瀏覽器 6/6 各 4 串 | 兩者都對，條件不同。README 補上條件。python.md 不在本資料夾，沒有改 |
| `q4/README.md`：@nasa HTML 只內嵌 4 串、畫面 5 篇 | 4 串、5 篇在 HTTP＋Sec-Fetch、桌面、手機三種都一致。但 `threads_profile_nasa.png` 的畫面被「Say more with Threads」對話框蓋住，只看得到置頂那 1 篇；5 篇是截圖腳本數 HTML 內嵌 JSON 得到的（`shared/capture/capture_threads.py` 第 80–84 行，截圖標註也寫「HTML 內嵌 4 串、5 篇」） | 4 串正確，補上條件；「畫面 5 篇」改成「畫面只看得到 1 篇，5 篇是 HTML 內嵌 JSON 的計數」（稽核指出，看過截圖後更正） |
| `q4/README.md`：@zuck 4 串、畫面 10 篇（「畫面」部分 2026-10-30 稽核已更正） | 4 串、10 篇；10 篇是 4 串加上串內回覆，不是 10 串 | 正確，補上說明 |
| `q4/README.md` 的 `threads_profile_zuck`：稽核更正寫「10 篇是 DOM 計數」 | 截圖腳本的 10 篇也是數 HTML 內嵌 JSON 得到的（同上），截圖標註寫「HTML 內嵌 4 串、10 篇」。10/30 重測畫面 DOM 裡的貼文連結也是 10 個，數字本身沒錯 | 改成「HTML 內嵌 JSON 的計數」，並註明原寫「DOM 計數」 |
| `q4/README.md` 的 `threads_graphql_desktop`：「個人頁載入時網頁自己送 `BarcelonaProfileThreadsTabRefetchableDirectQuery`」（11 串、401 KB） | 那張截圖的觀察時間包含載入後 4.5 秒與 1 次 1,600 px 的滾輪（`shared/capture/capture_threads.py`）。桌面：截圖腳本 1/1，量測程式 0/15（只有 #5 的 3 次確定捲到主欄）。手機：截圖腳本 1/2（01:51–01:52 +08 第一版 `shared/capture/capture.py` 捲 5 次沒收到帶貼文的回應，`capture_log.txt` 第 11 行，沒記 query 名稱；01:56 第二版捲 4 次看到），量測程式 1/3 | 改寫：那次是載入加 1 次捲動後看到的，網頁不是每次開頁都會送 |
| `q4/README.md` 的 `threads_mobile_wall`：10/30 出現登入牆，捲動不再分頁 | 這張截圖自己的標註寫著捲動期間收到 `BarcelonaProfileThreadsTabRefetchableDirectQuery`、帶貼文的回應 2 個（`shared/capture/capture_manifest.json` 的 result 也是）；登入提示在桌面、手機的伺服器 HTML 裡都有；量測程式手機 1/3 帳號載入下一頁 | 改成：出現登入提示不代表停止分頁，拍這張時其實有分頁 |
| `q4/README.md` 的 `threads-profile-natgeo.png`：「含一篇轉發 @natgeotv 的貼文」 | 截圖裡是 natgeo 自己的貼文，下方嵌著 @natgeotv 的貼文卡片；HTML 裡被引用那篇在 `share_info.quoted_post` | 改成「引用」 |
| README、verification：doc_id 會換 | 第三個值 `39825889313693314`；HTML 預排的 `BarcelonaProfilePageDirectQuery` 同一天 07:50 是 `39586378077644222`，12:30 變成 `28894437973498594` | 正確，補上證據 |
| verification §2：手機 viewport 捲動就會送分頁 query，`first: 10` | 量測程式手機 1/3 帳號送出，網頁自己帶的是 `first: 11` | 補 10-30 重測（§11） |
| verification §4：profile HTML 壓縮後 138–172 KB | gzip 163.9–198.6 KB；Chromium 自己協商的壓縮 144.1–164.5 KB | 補今天數字。壓縮方式不同，頁面內容也每天變，不算推翻 |
| `threads_q4_result.txt` 開頭：程式與 results.json「保留為舊量測紀錄，wire 欄位實為解壓後大小」；標題「修正版，2026-10-29」 | 程式的 wire 已改記壓縮後；results.json 仍是 10-27 舊紀錄；檔案有 10-30 的加註 | 改寫該行，標題註明 10-30 加註 |
| `threads_q4_result.txt` [A]：「手機 viewport 捲動會觸發分頁 query」 | 量測程式手機 1/3 帳號觸發 | 加註「10-30 重測只有 1/3 帳號觸發」 |
| `threads_q4_result.txt` [B]：doc_id `39551525044493602`；上一版加註「實作要從網頁 JS 動態取得」 | 今天網頁用的是 `39825889313693314`。10-30 兩個時段存下的 36 份 HTML（含 9 份空殼）都找不到 10-29 以來看過的這三個分頁 query doc_id，有內容的 HTML 只有模組名 `BarcelonaProfileThreadsTabRefetchableDirectQuery.threads.graphql`；從 JS 取得 doc_id 沒有試過 | 標為 10-29 的值，改成「需從網頁 JS 取得，取得方式未驗證」 |
| `q4/README.md` 第一段：答案與驗證檔在「根目錄」 | 檔案在 `q4/answer/` | 改正 |
| `q4/README.md`：「2026/10/30 下午（UTC）重測」「10/30 下午重測：桌面 15 次載入」 | 15 次桌面載入有 9 次在 07:52–07:57 UTC | 改成「10/30（UTC 07:50–12:37）」 |

上一版 RESULTS 自己的說法，這次更正的有：

| 上一版說法 | 更正後 | 依據 |
|---|---|---|
| 流量表「主框架文件（含網站自己的 302 轉址）」這次 12、合計 27 | 12、27 是主框架導覽數；文件這次 15 份，合計推估 33 份 | `browser_mobile.json` 每次開頁 3 份文件（`requests_by_type.document` 為 3） |
| 「手機視窗每開一頁，網站自己會轉址一次（多一份文件）」 | 多一次導覽、兩份文件（302 與轉回的 200） | 同上 |
| 「20 次 HTTP、21 次開頁全部 200」；工作回報「都沒超過 30 次的上限」 | 18 次有紀錄的開頁都是 200，前一次嘗試的手機 3 次沒有記到狀態；以文件算約 33 份，超過 30 | `run_browser_mobile/results.json` 只有 error |
| #6 列的指令沒有 `-w`，卻寫狀態 200 | 實際指令有 `-w "status=%{http_code} …"`，輸出 `status=200 bytes=8506`；表中已換成完整指令 | 工作紀錄 |
| #7 指令寫成 `python -I -c "...p.chromium.launch()..."`、時間「12:37 前後」 | 完整指令見上，時間 12:39:10–12:39:12 | 工作紀錄 |
| 前一次嘗試「完整指令沒有留存」 | 已找回並列出 | 工作紀錄 |
| #4、#5 用的是「最終版程式」 | 程式在 12:43:57 又改了一行註解；各輪實際跑的版本與 sha256 見「程式版本」 | 重放結果與 commit 進 repo 的檔案逐位元組相同 |
| 「測試從 6 個增加到 11 個」 | 相對 10-30 重測前的版本是從 2 個增加；6 個是前一次嘗試沒有 commit 的中間狀態 | 10-30 重測前版本的 `q4/code/tests/test_threads_smoke.py` |
| 「手機模式原本在頁面轉址後才呼叫 `resp.text()`」 | 這個錯是前一次嘗試寫進去、commit 前修好的，10-30 重測前的版本沒有手機模式 | 同上 |
| natgeo 分頁 query「在捲到底後送出」「第 1 次把主欄捲到底之後」 | 回應在 JS 捲到底之前就到了，觸發原因分不出來 | `browser_mobile.json` 的 `scroll_steps`：第 1 步 JS 捲動當下主欄已是 8,224 px |
| 「after 接在 HTML 那 4 串的 cursor 後面」 | 改標為推論，程式只記 `after` 不是空的 | DOM 裡 5 個連結加 17 篇剛好 22 個 |
| 「natgeo 的 5 篇裡有 1 篇不是本人發的（轉發 @natgeotv）」 | 是 natgeo 自己貼文裡引用的 @natgeotv 貼文，程式把被引用那篇也算成 1 篇 | HTML 路徑 `share_info.quoted_post`、`q4/evidence/threads-profile-natgeo.png` |
| 瀏覽器表的欄名「載入後畫面篇數」「捲到底後畫面篇數」 | 改成「DOM 貼文連結：載入後／捲動後」。程式數的是渲染後 DOM 裡的貼文連結，被對話框蓋住或在視窗外的也算，不等於截圖看得到的篇數；「捲到底」也只有 #5 與手機那 3 次做到 | `RENDERED_JS`；`threads_profile_nasa.png` 只看得到 1 篇，10/30 重測桌面 nasa 的 DOM 有 5 個連結 |
| natgeo 分頁 query 的 variables「另有 `__relay_internal__*` 旗標（未列）」 | 程式記錄前就把這類鍵拿掉，這次請求有沒有、有幾個都不知道 | `graphql_summary` 的寫法；10-29 的 45 個是 verification §2 的紀錄 |

## 未完成與限制

- 401／速率封鎖沒有重測。verification §7 的數字仍是 10-29 的單次結果。
- 沒有自己組 GraphQL 請求（`after=null`、`first=10`、帶 csrftoken／lsd），因為那需要取用 token；verification §3、§6 仍是 10-29 的證據。今天只看網頁自己送出的請求。
- doc_id 要怎麼取得沒有驗證：存下的 HTML 沒有 doc_id 數字，JS bundle 沒有存，也沒有試著從裡面抽。
- 只測 3 個帳號、一個時段、一個出口（容器代理）。手機分頁只在 natgeo 出現，原因（帳號、實驗分組或時間）分不出來；觸發它的是滾輪、滑鼠移動還是網頁計時，也分不出來。
- natgeo 分頁回應的 17 篇有幾篇是被引用的貼文，回應本文沒有存，無法重算。verification §5（10-28）的篇數當時有沒有把被引用的貼文算進去，現有資料也看不出來。
- 流量若以主框架文件計約 33 份，超過每站 30 次的上限；其中前一次嘗試手機的 9 份是推估。
- Chromium 的文件壓縮方式（br 或 zstd）沒有記錄。
- Threads 頁面上 `navigations` 多出來的筆數（桌面 1 筆、手機 3 筆）推測是網站用 history API 改網址。本機假站證實 `framenavigated` 會這樣記，但 Threads 實際呼叫了哪個 API 沒有直接觀察。
- `q4/evidence/src/capture.py` 沒有重跑，截圖也沒有重拍。它用預設的 `chromium.launch()`，在這台機器上會因為找不到 headless shell 1248 而失敗；這次沒有改它。
- `shared/capture/capture_manifest.json` 的 `q4-threads-mobile-wall` 標題「捲動不再載入下一頁」和同一筆的 result「帶貼文的 GraphQL 回應 2 個」互相矛盾，`shared/capture/capture_threads.py` 開頭的說明也這樣寫。同一個檔案的 `q4-threads-graphql-observed` 標題仍是「個人頁載入時網頁用 GraphQL 分頁」，但那次的觀察包含 1 次滾輪捲動。這兩個檔案不在本資料夾，沒有改。
- 瀏覽器測試要開 Chromium，3 個合計約 20 秒。
- robots.txt 開頭註明未經 Threads 書面許可不得自動化蒐集資料，並要求列名的授權爬蟲遵守 Meta 的 Automated Data Collection Terms。第 4 題要設計 40 萬帳號的爬蟲，原本的答案沒有提到這一點。檔內 37 組規則：`User-agent: *` 與 GPTBot、Scrapy 等 14 組列名的爬蟲是 `Disallow: /`；Googlebot、Bingbot 等 22 組列名的爬蟲只擋部分路徑（`/accounts/`、`/query/` 等），所以「對所有 user agent 都是 `Disallow: /`」的說法不精確。2026-10-31 經確認，答案 (b) 末尾補「另 robots.txt 禁止未經書面許可自動蒐集。」（連空白 26 字，不含空白 24 字）。為了連空白與換行一起算仍在 600 字內，其他句子做了 8 處不改意思的縮短，兩種算法都合計少 12 字（照原文，含標點與空白）：「first=10，即一次回」→「first=10 即回」、「一次性快取」→「快取一次」、「，可跨帳號共用」→「、跨帳號共用」、「不需登入」→「免登入」、「請求須帶」→「須帶」、「缺任一」→「缺一」、「以 IP 為單位封鎖」→「按 IP 封鎖」、「IP 並退避」→「IP、退避」。除了新加的 robots.txt，數字與英文詞都沒有變。改後連空白與換行 600 字，不含空白 521 字（原本 586、509 字）；`tests/test_q4_answer_length.py` 檢查這兩個數字與檔內標示一致、而且不超過 600。
