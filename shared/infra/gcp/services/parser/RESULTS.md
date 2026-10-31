# parser 服務驗證結果（Linux 重跑，2026-10-30）

## 結論

- 修正前的 `main.py` 對兩類事件回 204（ack），性質不同：
  - 少了 `schema_version` 或 `event_type`，或 `schema_version` 是 `"1"`、`1.0`、`true`：這些 Avro 不收。`qsearch-crawl-fetched` 主題綁了這份 Avro schema（JSON 編碼），官方文件寫「不符合 schema 的訊息不會被發佈」，所以照目前的部署，它們在發佈端就被擋，到不了 parser（依文件推論，沒有在 GCP 實測）。修正前的 204 只會影響繞過 schema 的訊息；這次修正是讓 parser 與 `.avsc` 契約一致，多一道防線，不是線上正在發生的 ack 漏洞。
  - `raw_data_uri` 是 `gs://bucket`、`gs://`、`gs://bucket/`、`gs:///obj` 這類缺 bucket 或 object 的 URI：這些是合法的 Avro 字串，真的發佈得出去、也會推到 parser。修正前會被 ack 掉，是實際的 bug；修正後回 422。
  - 修正方式：`Event` 改成全部必填、`strict=True`，並檢查 URI 必須是 `gs://<bucket>/<object>`。
- `Dockerfile` 的 CMD 原本沒有 `exec`：容器內 PID 1 是 `sh`（映像的 `/bin/sh` 是 dash），SIGTERM 被 sh 忽略、不會轉給 uvicorn，`docker stop -t 10` 要等到被 SIGKILL（三次實測 10.28 到 10.79 秒，退出碼 137）。加上 `exec` 後 PID 1 是 uvicorn，0.45 到 0.51 秒正常關機（退出碼 0）。
- 測試在 repo 內：`tests/` 共 106 項，在 3 個環境都全數通過（Python 3.13 共用 venv、依 `requirements.txt` 新建的 Python 3.12 venv、直接依賴取下限的 Python 3.12 venv）。同一套測試套在修正前（commit 80adb29）的 `main.py`／`Dockerfile` 上有 17 項失敗。
- `requirements.txt` 可以安裝（34 個套件，`pip check` 無衝突）；`docker build --no-cache` 成功（48.06 秒）。
- main.py 與 Terraform 的路徑、port、逾時、push 格式、OIDC 身分都一致。最大的部署風險在 Terraform：`var.image_parser` 預設是 Cloud Run hello 映像，它對任何 POST 都回 200，Pub/Sub 會把所有事件 ack 掉（詳見下方一致性表）。本檔沒有改 `.tf`；Terraform 分支（PR #7）之後加了 plan 時的警告，見下一節。

## 與 Terraform 分支（PR #7，67f0a79）合併後（2026-10-30 17:45 UTC）

兩個分支都改了這個資料夾，在 d03e106 合併，兩邊的修改都保留：

| 檔案 | 合併後 | 來源 |
|---|---|---|
| `main.py` | `Event` 六欄全部必填、依 `.avsc` 順序、`strict=True`；URI 要是 `gs://<bucket>/<object>`；解析失敗記 `log.warning`（不印 traceback） | 必填與 strict 兩邊都有；URI 檢查來自本分支；`log.warning` 與「Seek 重播會繞過主題 schema」的註解來自 PR #7 |
| `Dockerfile` | `USER 10001`（非 root）＋ CMD 用 `exec`，PID 1 是 uvicorn | USER 來自 PR #7；`exec` 來自本分支 |
| `requirements.txt` | 拿掉沒用到的 `google-cloud-pubsub` | PR #7 |
| `tests/` | 本分支 106 項＋PR #7 的 `test_push_endpoint.py` 13 項，`conftest.py` 合併兩邊的 fixture | 兩邊 |
| `local/` | Pub/Sub 模擬器的端到端實測（`local/VERIFY.md`） | PR #7 |

| 合併後重驗 | 退出碼 | 結果 | log（`<tmp>/work/parser/`） |
|---|---|---|---|
| pytest：共用 venv／`venv-req`／`venv-min` | 0／0／0 | 各 119 passed | 終端輸出（未另存） |
| `docker build --network host --build-arg HTTPS_PROXY=… -t qsearch-parser:merged shared/infra/gcp/services/parser` | 0 | 14.4 秒；映像 215 MB（合併前 294 MB，少了 pubsub 與它的 grpcio 等依賴） | `docker_build_merged.log`、`docker_images_merged.log` |
| `tests/smoke.sh qsearch-parser:merged 18095` | 0 | 12 項比對全部相符；PID 1 是 uvicorn，`docker stop` 0.457 秒，退出碼 0 | `smoke_merged.log` |
| `docker run --rm --network none --entrypoint sh qsearch-parser:merged -c 'id; …'` | 0 | `uid=10001(app)`；映像內沒有 `google.cloud.pubsub` | `image_merged_user.log` |

PR #7 在 Terraform 端另外處理了本檔記錄的兩個問題：`checks.tf` 的 `no_placeholder_images` 在任何 `image_*` 還是 Cloud Run 示範映像時於 plan 階段警告；push 訂閱的 `oidc_token` 明寫 `audience = 服務 URI`（不帶路徑），所以下方「OIDC audience」一列的預設值問題在合併後的 Terraform 不再適用。parser 映像的建置路徑仍未補（PR #7 寫進 `shared/infra/gcp/README.md` 的「未覆蓋」）。下方各節是合併前的紀錄，數字以合併前的檔案為準。

## 環境

| 項目 | 版本 |
|---|---|
| OS | Ubuntu 24.04.5 LTS（kernel 6.18.44），`/bin/sh` 是 dash 0.5.12-6ubuntu5 |
| 共用 venv | Python 3.13.17；fastapi 0.162.0、starlette 1.10.0、pydantic 2.14.2、httpx 0.28.1、pytest 9.2.0、fastavro 1.13.1。fastavro 原本沒有，是這次為了選用的 Avro 檢查用 pip 裝進共用 venv（`pip_fastavro_shared.log`） |
| `venv-req`（新建） | Python 3.12.3；`pip install -r requirements.txt` 解出 fastapi 0.162.0、uvicorn 0.54.0、pydantic 2.14.2、starlette 1.10.0、google-cloud-pubsub 2.43.0；另裝 `tests/requirements-test.txt` |
| `venv-min`（新建） | Python 3.12.3；requirements 直接依賴的下限：fastapi 0.115.0、uvicorn 0.30.0、pydantic 2.1.1（宣告的下限是 2.0，但實際可裝的最低是 2.1.1）、google-cloud-pubsub 2.20.0；starlette 由 fastapi 決定為 0.38.6。間接依賴是當時最新版（anyio 4.15.1、h11 0.16.0、httpx 0.28.1） |
| Docker | Docker Engine 29.9.2（client 與 server 同版，`dockerd --data-root` 放在 scratch）；映像內是 Debian 13（trixie）、Python 3.12.16、dash 0.5.12-12，`/bin/sh` 指向 dash（指令 #22，`image_env.log`） |

`venv-min` 的 pytest 只走到 fastapi、pydantic、starlette：TestClient 不經過 uvicorn，main.py 也沒有匯入 google-cloud-pubsub。uvicorn 0.30.0 另外用真的 server 跑過一次（指令 #17）；google-cloud-pubsub 2.20.0 只驗證了可以安裝。

## 指令與退出碼

所有 pytest 都以 `PYTHONDONTWRITEBYTECODE=1` 與 `-p no:cacheprovider` 執行；repo 內沒有產生 `__pycache__`。scratch 目錄是 `<tmp>/work/parser/`，下表的 log 檔都在那裡。

| # | 指令（摘要） | 退出碼 | 結果 | log |
|---|---|---|---|---|
| 1 | 共用 venv：`python -m pytest -p no:cacheprovider -q shared/infra/gcp/services/parser/tests`（接手時的測試） | 1 | 96 passed、1 failed：`allUsers` 出現在 run.tf 的註解裡，測試誤判。已改成只比對 `member = "..."` | 沒有保留 log |
| 2 | `dockerd --data-root <tmp>/work/parser/docker-data`（背景執行兩次：12:51 到 12:56、17:25 到 17:27 UTC，都以 SIGTERM 停止） | 0／0 | 兩次都是 `Daemon shutdown complete` | `dockerd3.log`、`dockerd4.log` |
| 3 | `docker pull mirror.gcr.io/library/python:3.12-slim@sha256:aa943ce4…` | 0 | `Image is up to date`（第一次拉取見 `docker_pull_mirror.log`） | `docker_pull_mirror_rerun.log` |
| 4 | `docker build -t python:3.12-slim <tmp>/work/parser/base-ca`（mirror 映像加 sandbox proxy 的 CA 與 `PIP_CERT`，在本機標成 `python:3.12-slim`） | 0 | 本機標籤 `python:3.12-slim` 指向這個包裝映像（第一次建置見 `docker_build_baseca.log`） | `docker_build_baseca_rerun.log` |
| 5 | `docker build --no-cache --network host --build-arg HTTPS_PROXY=… -t qsearch-parser:current shared/infra/gcp/services/parser` | 0 | 48.06 秒（pip 那層 43.2 秒）。這份 log 沒有記 `exit=`，0 是依 BuildKit 的輸出判斷：每一步都 DONE、沒有 ERROR，最後一步有 `naming to docker.io/library/qsearch-parser:current done` 並以 `#10 DONE 4.2s` 結束。有記退出碼的建置見 #6 | `docker_build_current_nocache.log` |
| 6 | 同上但沒有 `--no-cache`（#4 重建了基底，所以 pip 那層重跑） | 0 | 約 35 秒（pip 那層 27.4 秒），產生映像 `201066844cfc`；映像內 `pip freeze` 34 個套件。映像內 `/app/main.py`、`/app/requirements.txt` 與工作目錄相同：當時的 `diff -q` 沒有留 log，17:26 UTC 對同一個映像重做並記錄（#22） | `docker_build_rerun.log`、`image_current_freeze.txt` |
| 7 | `docker images` | 0 | `qsearch-parser:current` 294 MB（content 70.3 MB） | `docker_images.log`、`docker_images_after_rebuild.log` |
| 8 | `tests/smoke.sh qsearch-parser:current 18091`（12:54、17:26 UTC 各跑一次） | 0／0 | 兩次都是 12 項比對全部相符（`result: all ok`），停機 0.484 秒、0.505 秒 | `smoke_rerun.log`、`smoke_rev2.log` |
| 9 | `tests/smoke.sh qsearch-parser:orig 18092`（用 80adb29 的 `main.py` 與 `Dockerfile` 建的映像，見 #22） | 1／1 | 兩次都是同樣 6 項不符：PID 1、三個事件回 204、停機超過 5 秒（10.290 秒、10.279 秒）、退出碼 137 | `smoke_rerun.log`、`smoke_rev2.log` |
| 10 | `docker run -e PORT=9090 -p 127.0.0.1:18083:9090 qsearch-parser:current`，再 `curl /health` | 0 | PID 1 是 `uvicorn … --port 9090`，`/health` 200 | `hello_and_port.log` |
| 11 | `docker pull us-docker.pkg.dev/cloudrun/container/hello` | 0 | digest `sha256:d5dbac4a…` | `docker_pull_hello.log` |
| 12 | 以 digest 啟動 hello，再 POST 缺欄位的事件、亂碼 body、空 body 到 `/events/crawl-fetched` | 0 | 三個都回 200 | `hello_and_port.log` |
| 13 | `python3.12 -m venv venv-req && venv-req/bin/python -m pip install -r requirements.txt` | 0／0 | 34 個套件 | `pip_req2.log`、`venv_req_freeze.txt` |
| 14 | `venv-req/bin/python -m pip install -r tests/requirements-test.txt` | 0 | pytest 9.2.0、httpx 0.28.1、fastavro 1.13.1 | `pip_req_test.log` |
| 15 | `venv-min`：`pip install --only-binary=:all: fastapi==0.115.0 uvicorn==0.30.0 pydantic==2.0 google-cloud-pubsub==2.20.0` | 1 | ResolutionImpossible：fastapi 0.115.0 排除 pydantic 2.0.0、2.0.1、2.1.0 | `pip_min.log` |
| 16 | 同上，改 `pydantic==2.1.1` | 0 | 安裝成功 | `pip_min2.log`、`venv_min_freeze.txt` |
| 17 | `venv-min`：`python -m uvicorn main:app --app-dir … --port 18097`，curl 後送 SIGTERM | 143 | uvicorn 0.30.0 可啟動：`/health` 200、合法事件 204、`gs://bucket` 422；收到 SIGTERM 後 log 有 `Shutting down`、`Finished server process`。143 是 uvicorn 正常收尾後以 `signal.raise_signal` 把 SIGTERM 再送給自己的結果（`uvicorn/server.py` 第 328 行；這裡不是 PID 1，所以以訊號結束）。容器裡的 uvicorn 0.54.0 也會這樣做（同一個呼叫在第 349 行），但它是 PID 1，核心不會用預設動作結束 PID 1，所以退出碼是 0（#8） | `uvicorn_min.log` |
| 18 | `venv-req`、`venv-min` 各跑 `python -m pip check` | 0／0 | `No broken requirements found.` | `pip_check.log` |
| 19 | `venv-req` 遮掉 fastavro（`sys.modules['fastavro'] = None`）再跑 pytest（12:55、17:30 UTC 各一次） | 0／0 | 兩次都是 91 passed、15 skipped（fastavro 檢查自動略過） | `pytest_rev_nofastavro.log`、`pytest_r3_nofastavro.log` |
| 20 | 同一套測試套在修正前的 `main.py`、`Dockerfile` 上（`git show 80adb29:…` 放到 scratch 複本 `orig-tree/`，`.tf`、`.avsc`、`requirements.txt` 與工作目錄相同；80adb29 是修正進 git（1531eb4）之前最後一個動到 parser 的 commit） | 1／1 | 兩次都是 89 passed、17 failed，失敗的 17 項相同 | `pytest_rev_against_head.log`、`pytest_r3_against_80adb29.log` |
| 21 | 最終 pytest：共用 venv／`venv-req`／`venv-min`（12:55、17:30 UTC 各一輪；17:35 UTC 改完本檔後，共用 venv 再跑一次，`pytest_end_venv.log`） | 0／0／0 | 兩輪都是各 106 passed，17:35 那次也是 106 passed | `pytest_rev_venv.log`、`pytest_rev_venv-req.log`、`pytest_rev_venv-min.log`；`pytest_r3_venv.log`、`pytest_r3_venv-req.log`、`pytest_r3_venv-min.log` |
| 22 | （17:26 UTC）兩個映像各跑 `docker run --rm --network none … sh -c '. /etc/os-release; python -V; dpkg-query -W dash; readlink -f /bin/sh'`，再用 `docker inspect` 看 CMD、用 `diff` 比對映像內的 `/app/main.py` | 0 | 兩個映像都是 Debian 13（trixie）、Python 3.12.16、dash 0.5.12-12。current 的 CMD 有 `exec`，orig 沒有。current 的 `/app/main.py`、`/app/requirements.txt` 與工作目錄相同，orig 的 `/app/main.py` 與 80adb29 相同（`diff` 都退出 0） | `image_env.log` |
| 23 | `curl -sS -L` 下載 Pub/Sub 的 push 文件與 push 驗證文件，各一次（17:26、17:27 UTC） | 0／0 | 都是 HTTP 200，各轉址 1 次到 docs.cloud.google.com；sha256 見下方文件來源表 | `docs-dl-push/push.html`、`docs-dl-auth/auth_push.html` |
| 24 | 共用 venv 與 `venv-min` 各跑一段 `python -I -c`（17:33 UTC）：用 80adb29 的 `Event` 寫法（`schema_version: int = 1`，非 strict）解 `"1"`、`1.0`、`true` 與缺欄位 | 0／0 | pydantic 2.14.2 與 2.1.1 都把四種輸入解成 int 1 | `pydantic_lax.log` |

關於 `smoke.sh` 的退出碼：更早的 scratch 腳本（`scripts/smoke.sh`）沒有任何比對，退出碼只是最後一個 `docker rm` 的結果，所以審查前草稿記的「0」只代表腳本跑完，結果要看印出的狀態碼。現在的 `tests/smoke.sh` 會逐項和修正後的預期比對，有不符就退出 1，所以 #8 的 0 與 #9 的 1 都是比對結果。

#20 的 17 項失敗：`Event` 結構 1 項、Avro 不收的事件 10 項（contract 5、push 5）、缺 bucket 或 object 的 URI 4 項、CMD 沒有 exec 2 項。

`pip_check.log` 裡的套件數（`venv-req` 42 個、`venv-min` 37 個）包含 `tests/requirements-test.txt` 的測試套件。只裝 `requirements.txt` 時是 34 個（`venv_req_freeze.txt`、映像內的 `image_current_freeze.txt`）。

## pytest 結果

指令（在 repo 根目錄）：

```
PYTHONDONTWRITEBYTECODE=1 <tmp>/venv/bin/python -m pytest -p no:cacheprovider -q shared/infra/gcp/services/parser/tests
```

| 環境 | 結果 | 退出碼 |
|---|---|---|
| 共用 venv（Python 3.13.17） | 106 passed | 0 |
| `venv-req`（Python 3.12.3，requirements 最新解） | 106 passed | 0 |
| `venv-min`（Python 3.12.3，直接依賴取下限） | 106 passed | 0 |
| `venv-req` 遮掉 fastavro | 91 passed、15 skipped | 0 |

12:55 與 17:30 UTC 各跑一輪，兩輪結果相同（指令 #19 到 #21）。

唯一的 warning 是 starlette 的 TestClient 提示：starlette 1.10 建議改用 `httpx2`，`venv-min` 則是 anyio 的 BlockingPortal 棄用提示，都只影響測試。

測試內容（`test_parser_push.py` 45 項、`test_parser_contract.py` 61 項）：

| 主題 | 測什麼 |
|---|---|
| 合法 push envelope | base64 data、attributes（空、多個、整個沒有）、文件最大範例的欄位（`message_id`、`publishTime`、`orderingKey`、頂層 `deliveryAttempt`）都回 204 |
| 壞 envelope | 空 body、非 JSON、陣列、沒有 `message`、沒有 `data`、`data` 不是字串、attribute 值不是字串、未包裝（`no_wrapper`）的 body，都回 422 |
| 壞 data | 空字串、非 base64、非 ASCII、非 JSON、JSON 陣列、非 UTF-8，都回 422 |
| Avro 契約 | `Event` 欄位名稱、型別與 `.avsc` 相同且全部必填（也比對順序，但順序只影響閱讀，不影響 JSON 解析）；15 組樣本同時用手寫檢查與 fastavro 1.13.1 驗證；Avro 不收的樣本 parser 一律 422 |
| ack 語意 | Pub/Sub 只把 102、200、201、202、204 當 ack（push 文件，2026-10-30 查閱，存檔見文件來源表）；永久錯誤每次都 422，共投遞約 5 次（首次加重送 4 次，best effort）後轉 DLQ；非預期例外回 500，也是 nack |
| 冪等 | 同一則訊息送兩次，兩次都 204、log 兩次。dry-run 沒有副作用所以無害，但服務本身沒有去重 |
| 環境變數 | main.py 不讀任何環境變數，在空環境可載入；CMD 在 `PORT` 未設時用 8080、設 9090 時用 9090；`PYTHONUNBUFFERED=1` |
| Dockerfile | 只 COPY `requirements.txt`、`main.py`；FROM 允許 `python:3.12-slim` 或固定 digest 的寫法；用 dash 執行 CMD 檢查 uvicorn 是否 exec 成原本的程序 |
| requirements | main.py 匯入的第三方套件（fastapi、pydantic）與 uvicorn 都在 requirements；main.py 沒有匯入 `google`。不要求 google-cloud-pubsub 存在，之後刪掉不會讓測試失敗 |
| smoke 腳本 | `bash -n` 檢查 `tests/smoke.sh` 語法，並確認它用的是 Terraform 的 push 路徑與 port 8080（腳本本身要 Docker，不在 pytest 裡跑） |
| Terraform 對照（只讀） | push 路徑、OIDC SA、invoker、逾時與 ack deadline、DLQ、port、主題 schema 與 JSON 編碼、BigQuery 表欄位 |

## 容器實測（`tests/smoke.sh`，2026-10-30 12:54 與 17:26 UTC 各跑一次，兩次結果相同）

| 請求 | 修正後（current） | 修正前（orig） |
|---|---|---|
| 容器 PID 1 | `python3.12 uvicorn main:app … --port 8080` | `sh -c uvicorn main:app …` |
| `GET /health` | 200 | 200 |
| 合法事件（第 1 次、重複第 2 次） | 204、204 | 204、204 |
| 少 `schema_version`（Avro 不收，主題在發佈端會擋） | 422 | **204** |
| `schema_version` 為 `"1"`（Avro 不收，主題在發佈端會擋） | 422 | **204** |
| `raw_data_uri` 為 `gs://bucket`（Avro 合法，會送到 parser） | 422 | **204** |
| data 不是 base64 | 422 | 422 |
| 未包裝的 body | 422 | 422 |
| `POST /push`（舊 snippet 的路徑） | 404 | 404 |
| `docker stop -t 10` | 0.484 秒、0.505 秒，退出碼 0，log 有 `Shutting down`、`Finished server process`（更早用沒有比對的舊腳本量到 0.45 秒） | **10.290 秒、10.279 秒，退出碼 137（SIGKILL）**（舊腳本量到 10.79 秒） |
| `smoke.sh` 退出碼 | 0、0 | 1、1 |

`PORT=9090` 時 current 映像以 `--port 9090` 啟動，`/health` 回 200。17:26 那次用的 `tests/smoke.sh` 與 HEAD 版相同（sha256 `684cf368…`，記在 `smoke_rev2.log` 開頭）。

建置環境說明：本機無法直接用 Docker Hub（第一次 `docker pull` 收到 `429 Too Many Requests`，`docker_pull.log`），基底改從 `mirror.gcr.io/library/python:3.12-slim`（digest `sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159`）取得，加一層 sandbox proxy 的 CA 與 `PIP_CERT` 後在本機標成 `python:3.12-slim`（指令 #3、#4）。Dockerfile 本身沒改 FROM；正式在 Cloud Build 建置時不會有這一層。hello 映像 digest：`sha256:d5dbac4a196296d8d517f8bdc302ad967deb9d83accf8bdbe491d3479e86a5e5`。

## main.py 與 Terraform 一致性（只讀 `.tf`，未修改）

| 項目 | main.py／Dockerfile | Terraform | 判定 |
|---|---|---|---|
| push 路徑 | `POST /events/crawl-fetched` | `push_endpoint = "${…parser.uri}/events/crawl-fetched"` | 一致 |
| port | `--port ${PORT:-8080}` | `container_port = 8080` | 一致 |
| push 格式 | 解析 wrapped envelope（`message.data`） | 沒有 `no_wrapper` | 一致 |
| data 編碼 | `json` 解 base64 後的內容 | 主題 `schema_settings.encoding = "JSON"` | 一致 |
| 逾時 | 單次請求同步處理 | Cloud Run `timeout = "60s"`、`ack_deadline_seconds = 60` | 一致 |
| 身分驗證 | 程式不驗 token，靠 Cloud Run IAM | `oidc_token` 用 push SA；`roles/run.invoker` 只給 push SA，沒有 `allUsers` | 一致 |
| 重送與 DLQ | 永久錯誤回 422（nack） | `max_delivery_attempts = 5`、`dead_letter_topic = parser_dlq` | 一致。永久錯誤共投遞約 5 次（首次加重送 4 次，best effort）後轉 DLQ：REST reference 的 `DeadLetterPolicy.maxDeliveryAttempts` 把投遞次數定義為「1 + nack 次數 + 超過 ack deadline 的次數」，並寫明是 best effort |
| 環境變數 | 不讀 | parser 沒有 `env` 區塊 | 一致（正式版需要 bucket、dataset 等設定時兩邊都要加） |
| **映像預設值** | — | `var.image_parser` 預設 `us-docker.pkg.dev/cloudrun/container/hello` | **風險**：hello 對 `/events/crawl-fetched` 的 POST 一律回 200（指令 #12），用預設值 apply 後，訂閱會把所有 `crawl.fetched` 事件 ack 掉。`retain_acked_messages = true` 保留 7 天，可以用 Seek 補回 |
| 映像建置 | 有 Dockerfile | `cloudbuild.tf` 只有 crawler-api 的 trigger | 缺口：parser 映像沒有自動建置路徑 |
| runtime SA 權限 | dry-run，不讀 GCS、不寫 BigQuery、不發佈 | parser SA 有 raw bucket `objectViewer`、dataset `dataEditor`，沒有任何主題的 publisher | 權限目前用不到；若之後要發佈 `parse.outbox`，Terraform 要補 publisher |
| 依賴 | `google-cloud-pubsub` 在 requirements，但 main.py 沒有匯入 | — | 多裝 20 個套件（34 個對 14 個，含 grpcio、cryptography、protobuf） |
| OIDC audience | — | `oidc_token` 沒設 `audience` | 預設 audience 是 push endpoint URL，也就是服務 URI 加上 `/events/crawl-fetched`（REST reference `OidcToken.audience`：「if not specified, the Push endpoint URL will be used」，2026-10-30 查閱）。Pub/Sub 的 push 驗證文件寫 Cloud Run 會自己驗證 Pub/Sub 產生的 token，「The only configuration that you require is to grant the necessary IAM roles to the caller account」，依這句推論不必另設 audience。但文件沒有明講帶路徑的 audience，Cloud Run 實際接不接受仍未實測 |

文件來源（2026-10-30 下載，每頁一個獨立的 scratch 目錄，未進 git）：

| 頁面 | sha256 | 用到的內容 |
|---|---|---|
| `https://cloud.google.com/pubsub/docs/reference/rest/v1/projects.subscriptions`（轉到 docs.cloud.google.com，12:51 UTC，`docs-dl/rest_subscriptions.html`） | `a6d44199c478db9747de9fb6cc7fa100f2f9725751f27d2d3fbe6eb174e85299` | `OidcToken.audience` 預設值；`DeadLetterPolicy.maxDeliveryAttempts`：「The number of delivery attempts is defined as 1 + (the sum of number of NACKs and number of times the acknowledgment deadline has been exceeded for the message)」「This field will be honored on a best effort basis」 |
| `https://cloud.google.com/pubsub/docs/schemas`（轉到 docs.cloud.google.com，12:52 UTC，`docs-dl/schemas.html`） | `7bbf2a08e4dc4c572f1ce481ca9a396dd8f8bfabbd9081f466d86973e7c0f5c5` | 「If a specific message does not conform to the schema, the message is not published.」 |
| `https://cloud.google.com/pubsub/docs/push`（轉到 docs.cloud.google.com，17:26 UTC，`docs-dl-push/push.html`） | `0db9b1975c1b86786233e028b11a16250fd000b6fe974de6cfddf5117b744ba5` | 「To acknowledge the message, return one of the following status codes: 102 200 201 202 204」「To send a negative acknowledgment for the message, return any other status code」 |
| `https://cloud.google.com/pubsub/docs/authenticate-push-subscriptions`（轉到 docs.cloud.google.com，17:27 UTC，`docs-dl-auth/auth_push.html`） | `95f2950591587c258c0e4ad4ddb97582d67bc4f3d182612e53bb31cd913742b1` | 「Cloud Run and App Engine functions authenticate HTTP calls from Pub/Sub by verifying Pub/Sub-generated tokens. The only configuration that you require is to grant the necessary IAM roles to the caller account.」 |

引文是用 `docs-scripts/extract.py`（BeautifulSoup 取純文字再用 regex 找句子，以 `python -I` 執行）從存檔裡找出來的，不是憑記憶。

## 與既有說法的差異／修正

repo 裡既有的說法與程式（含本檔進 git 的 1531eb4 版）：

| 原本的說法 | 現在 | 依據 |
|---|---|---|
| `shared/docs/python.md`：「lab parser：`S\py\tests` 6 passed」 | 那 6 項測試不在 git 裡，無法重跑；現在 repo 內有 106 項測試 | 本檔 pytest 結果 |
| `.avsc` 的 doc：「欄位與 services/parser 的 Pydantic model 與 lab 的 publish 範例一致」；`shared/infra/gcp/README.md` 第 20 行：「欄位與 lab／parser 一致」 | 欄位名稱與型別在修正前就一致。真正的差別是預設值與寬鬆轉型：`schema_version`、`event_type` 有預設值（Avro 沒有），而且會把 `"1"`、`1.0`、`true` 轉成 1（pydantic 2.14.2 與 2.1.1 都實測過，指令 #24）。欄位順序也不同，但只是外觀，不影響 JSON 解析。main.py 修正後兩邊一致（這兩份檔案不在本次範圍，沒有改） | 修正前 17 項測試失敗（指令 #20）；容器實測 orig 欄 |
| 80adb29 的 main.py 註解：「Non-2xx -> Pub/Sub redelivery」 | 改成「Push acks only 102/200/201/202/204」。102 不是 2xx 卻算 ack，203、205 等 2xx 則算 nack | push 文件（`docs-dl-push/push.html`） |
| 80adb29 的 Dockerfile CMD 沒有 `exec` | 加上 `exec` | 容器實測：10.279 到 10.79 秒、137，對 0.45 到 0.505 秒、0 |
| 本檔 1531eb4 版的指令 #20：「`git show HEAD:…`」 | 改成 `git show 80adb29:…`。修正已在 1531eb4 進 git，現在的 HEAD 是修正後的版本，照原寫法會拿到修正後的檔案 | `git log -- shared/infra/gcp/services/parser/`；`orig-tree/` 的 `main.py`、`Dockerfile` 與 80adb29 `diff` 退出 0 |
| 本檔 1531eb4 版的環境列：映像是 Debian 13、Python 3.12.16、dash 0.5.12-12（沒有 log） | 數字不變，補了 log | 指令 #22，`image_env.log` |
| 本檔 1531eb4 版的 #5：退出碼 0 | 數字不變，註明 log 裡沒有 `exit=` 行、0 是依 BuildKit 輸出判斷 | `docker_build_current_nocache.log` |
| 本檔 1531eb4 版與 `tests/parser_cases.py`：ack 狀態碼引用「push 文件，2026-10-30 查閱」（沒有存檔） | 內容不變，存了頁面並記 sha256 | 指令 #23，文件來源表 |
| 本檔 1531eb4 版：Cloud Run 是否接受帶路徑的 audience「未實測」 | 仍未實測；補上 push 驗證文件的說法：Cloud Run 只需要 IAM 角色 | `docs-dl-auth/auth_push.html` |

審查前的草稿與接手時的測試（都沒有進 git，列出來是交代審查意見怎麼處理）：

| 原本的說法 | 現在 | 依據 |
|---|---|---|
| 草稿的結論：「Avro schema 不接受的事件原本會被 ack」，並把 `gs://bucket` 算在裡面 | 分成兩類：Avro 不收的事件在發佈端就會被主題擋掉，修正是契約一致與防線；`gs://bucket` 這類 URI 是合法 Avro、真的會送到 parser，修正前被 ack 是實際的 bug | `.avsc`、fastavro 驗證、schema 文件 |
| 草稿：「重送 5 次（`max_delivery_attempts`）後進 DLQ」 | 共投遞約 5 次（首次加重送 4 次，best effort）後轉 DLQ | REST reference `DeadLetterPolicy.maxDeliveryAttempts` |
| 草稿：「查的兩頁官方文件沒有寫預設 audience」 | REST reference 有寫：未指定時用 push endpoint URL。只剩 Cloud Run 是否接受帶路徑的 audience 未實測 | REST reference `OidcToken.audience` |
| 草稿把 `venv-min` 稱為「各套件最低版本」「requirements 的下限」 | 只有直接依賴取下限，pydantic 是實際可裝的最低 2.1.1，間接依賴是最新版；pytest 只走到 fastapi、pydantic、starlette | `venv_min_freeze.txt` |
| 草稿 smoke 列的退出碼 0 | 舊腳本沒有比對，0 只代表腳本跑完；現在 `tests/smoke.sh` 有比對，current 0、orig 1 | `smoke_rerun.log`、`smoke_rev2.log` |
| 草稿的測試鎖住了要修的缺點：FROM 只接受 `python:3.12-slim`、requirements 一定要有 google-cloud-pubsub | FROM 也接受 `@sha256:<64 位 hex>`；不再要求 google-cloud-pubsub，只檢查 main.py 沒有匯入 `google` | `test_from_pattern_allows_digest_pin`、`test_requirements_cover_imports` |
| 接手時的 `test_push_is_wrapped_and_authenticated` 直接找字串 `allUsers` | 改為只比對 `member = "..."`，因為 run.tf 的註解寫了「不開 allUsers」 | 指令 #1 |
| 接手時的 `test_parser_contract.py` 用 `from conftest import …` | 改成獨立的 `tests/parser_cases.py`，避免其他題目也有 `conftest.py` 時抓錯模組 | — |

## 版本範圍的問題

- `pydantic>=2.0` 的下限名不符實：fastapi 0.115 排除 pydantic 2.0.0、2.0.1、2.1.0，實際最低是 2.1.1。直接依賴的下限組合（fastapi 0.115.0、uvicorn 0.30.0、pydantic 2.1.1、google-cloud-pubsub 2.20.0）可以只用 wheel 安裝，106 項測試全過，uvicorn 0.30.0 也能啟動服務。`requirements.txt` 沒有改，只在這裡記錄。
- 沒有 lock 檔或 hash，基底映像也沒有固定 digest：每次建置會解到範圍內最新版（這次是 fastapi 0.162.0、starlette 1.10.0）。要可重現的話，建議加 constraints 檔並固定 `python:3.12-slim@sha256:…`；測試已允許這種寫法。

## 輸出與 log 位置

- repo 內：`tests/conftest.py`、`tests/parser_cases.py`、`tests/test_parser_push.py`、`tests/test_parser_contract.py`、`tests/requirements-test.txt`、`tests/smoke.sh`、本檔。
- scratch（不進 git）：`<tmp>/work/parser/`
  - pytest：最後一次是 17:35 UTC 的 `pytest_end_venv.log`；17:30 UTC 這輪是 `pytest_r3_venv.log`、`pytest_r3_venv-req.log`、`pytest_r3_venv-min.log`、`pytest_r3_nofastavro.log`、`pytest_r3_against_80adb29.log`；12:55 那輪是 `pytest_rev_*.log`、`pytest_rev_against_head.log`；更早（101 項）的是 `pytest_final_*.log`、`pytest_against_head.log`
  - pip：`pip_req2.log`、`venv_req_freeze.txt`、`pip_req_test.log`、`pip_min.log`（失敗）、`pip_min2.log`、`venv_min_freeze.txt`、`pip_check.log`、`pip_fastavro_shared.log`、`pip_nopubsub.log`；pydantic 寬鬆轉型：`pydantic_lax.log`
  - Docker：`dockerd3.log`、`dockerd4.log`、`docker_pull_mirror.log`、`docker_pull_mirror_rerun.log`、`docker_build_baseca.log`、`docker_build_baseca_rerun.log`、`docker_build_current_nocache.log`、`docker_build_rerun.log`、`image_current_freeze.txt`、`image_env.log`、`docker_images.log`、`docker_images_after_rebuild.log`、`smoke_rerun.log`、`smoke_rev2.log`、`hello_and_port.log`、`docker_pull_hello.log`；更早的 `smoke_final.log` 是沒有比對的舊腳本輸出
  - uvicorn 下限：`uvicorn_min.log`、`uvicorn_min_server.log`
  - 文件：`docs-dl/rest_subscriptions.html`、`docs-dl/schemas.html`、`docs-dl-push/push.html`、`docs-dl-auth/auth_push.html`（sha256 見上表）；找引文的腳本 `docs-scripts/extract.py`

## 未完成與限制

- 沒有部署到 GCP，也沒有用真的 Pub/Sub 推送；ack、重送與「主題在發佈端擋掉 Avro 不收的訊息」都是依官方文件加上本機 HTTP 狀態碼判斷。
- OIDC：合併 PR #7 後 audience 明寫為服務 URI（不帶路徑），避開了「帶路徑的預設 audience」的問題；但 Cloud Run 實際驗證 token 的行為仍要在真的專案上確認。
- 服務仍是 dry-run：不讀 GCS、不解析 HTML、不寫 DB、沒有去重。冪等測試只證明重複投遞時兩次都 ack，不代表正式版的去重已完成。
- Terraform 的兩個問題本檔只記錄，沒有改 `.tf`。合併 PR #7 後，hello 映像預設值會在 plan 時警告（仍是預設值，不是錯誤）；parser 仍沒有建置 trigger。
- `google-cloud-pubsub` 已由 PR #7 從 `requirements.txt` 拿掉；之後若 parser 要發佈後續事件，要再加回來並補測試。
- Docker 建置用了 sandbox 專用的基底（mirror 加 CA），和 Cloud Build 的環境不同。`tests/smoke.sh` 用了 GNU `base64 -w0` 與 `date +%s%N`，只在 Linux 上驗證過。
