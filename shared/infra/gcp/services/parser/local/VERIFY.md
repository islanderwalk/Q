# parser 本機實跑驗證紀錄（Pub/Sub 模擬器 + FastAPI parser）

> 日期：2026-10-30。執行環境：雲端容器（Linux、Docker 29.9.2、Docker Compose v5.6.1）。
> **沒有任何 GCP 憑證，也沒有呼叫任何真的 GCP API。** Pub/Sub 全部走本機模擬器，BigQuery、GCS、Cloud Run IAM 都沒有實測。
> 第一、二次實跑時 `main.py`、`Dockerfile`、`requirements.txt` 都是原樣；最後一節列出的問題 1、3、4 之後已在 repo 修正，
> 第三次實跑（下面「第三次：修正後重跑」）用修正後的映像重做一次，結論見該節。

## 這個目錄的檔案

| 檔案 | 用途 |
|---|---|
| `compose.yaml` | compose 專案 `qsearch-gcplocal`：Pub/Sub 模擬器（`127.0.0.1:18285`）、parser（`127.0.0.1:18280`）、header-echo（`127.0.0.1:18281`） |
| `pubsub_e2e.py` | 建 topic／訂閱（名稱取自 `pubsub.tf`）、發佈 a～d 四則訊息、拉 DLQ、Seek 重播、統計 parser log |
| `header_echo.py` | 第二條 push 訂閱的接收端，記錄模擬器實際送出的 header 與 envelope |
| `check_schema.py` | fastavro 解析 Avro schema，並比對 Avro × Pydantic `Event` × `bq.tf` 的 `ingest_events` 欄位 |
| `../tests/` | pytest（FastAPI TestClient）；`../tests/fixtures/` 是 a～d 四則樣本，模擬器實測與單元測試共用 |

## 版本

| 項目 | 版本 |
|---|---|
| Pub/Sub 模擬器 | `gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators`（Google Cloud SDK 591.0.0，pubsub-emulator 0.8.37，image id `sha256:112c21f8…`） |
| parser 映像 | `qsearch-gcplocal-parser:local`（由本 repo 的 `../Dockerfile` 建置；容器內 Python 3.12.16、fastapi 0.162.0、pydantic 2.14.2、uvicorn 0.54.0） |
| 主機端 venv | Python 3.13.17、google-cloud-pubsub 2.43.0、fastavro 1.13.1、pytest 9.2.0、httpx 0.28.1 |

## 重現步驟

以下 `$VENV` 是任一個裝了 `google-cloud-pubsub fastavro pytest httpx fastapi pydantic uvicorn` 的 Python venv。

```bash
cd shared/infra/gcp/services/parser/local

# 0. 建 parser 映像（一般環境）
docker compose -p qsearch-gcplocal -f compose.yaml build parser
#    本次容器裡 Docker Hub 匿名拉取被限流（HTTP 429），改用 GCR 的官方映像鏡像 mirror.gcr.io/library/python:3.12-slim
#    再疊上出口代理 CA，用 --build-context 頂替 Dockerfile 的 FROM python:3.12-slim（Dockerfile 本身未改）：
#    docker build --network host --build-arg https_proxy=http://127.0.0.1:<proxy-port> \
#      --build-context python:3.12-slim=docker-image://qsearch-gcplocal-pybase:local \
#      -t qsearch-gcplocal-parser:local ..

# 1. 起服務，確認 /health
docker compose -p qsearch-gcplocal -f compose.yaml up -d --no-build
curl -sS -i http://127.0.0.1:18280/health

# 2. 建 topic、DLQ 與 push 訂閱
export PUBSUB_EMULATOR_HOST=127.0.0.1:18285
$VENV/bin/python pubsub_e2e.py setup

# 3. 發佈 a～d，觀察 30～75 秒
$VENV/bin/python pubsub_e2e.py publish
docker logs qsearch-gcplocal-parser-1 2>&1 | $VENV/bin/python pubsub_e2e.py count
$VENV/bin/python pubsub_e2e.py dlq          # 多跑幾次，每次可能只拉到 1 則

# 4. 其他探測
docker logs qsearch-gcplocal-header-echo-1 2>&1 | grep -o '"has_authorization": [a-z]*' | sort | uniq -c
$VENV/bin/python pubsub_e2e.py probe-schema
$VENV/bin/python pubsub_e2e.py seek 10

# 5. schema 比對與單元測試（不需要模擬器）
$VENV/bin/python check_schema.py
cd .. && PYTHONDONTWRITEBYTECODE=1 $VENV/bin/python -m pytest -p no:cacheprovider -v -rxX tests

# 6. 清理（只清本專案）
cd local && docker compose -p qsearch-gcplocal -f compose.yaml down -v
```

## 結果

整個流程跑了兩次（第一次逐步執行，第二次在全新的 compose 環境照上面步驟重跑），結論相同。下表數字標明出自哪一次。

### 1. `GET /health`

| 指令 | 結束碼 | 輸出 |
|---|---|---|
| `curl -sS -i http://127.0.0.1:18280/health` | 0 | `HTTP/1.1 200 OK`、`{"status":"ok"}`（兩次都是） |
| `curl -sS -i http://127.0.0.1:18285/`（模擬器） | 0 | `HTTP/1.1 200 OK`、`Ok` |

### 2. 建 topic 與 push 訂閱（`pubsub_e2e.py setup`，結束碼 0）

模擬器**全部接受**，`get_subscription` 讀回來的設定與 `pubsub.tf` 的 `qsearch_parser_sub` 一致：
`pushEndpoint=http://parser:8080/events/crawl-fetched`、`oidcToken.serviceAccountEmail` 有存、`ackDeadlineSeconds=60`、
`retainAckedMessages=true`、`messageRetentionDuration=604800s`、`retryPolicy 10s／600s`、`deadLetterPolicy max 5 → qsearch-parser-dlq`。

`qsearch-crawl-fetched` 在這裡**刻意不綁 schema**，否則 d（非 JSON）在 publish 時就被擋，到不了 parser。schema 行為另外用探測 topic 測（見第 5 點）。

### 3. 四則訊息的投遞結果

`pubsub_e2e.py publish` 結束碼 0。以下是第一次的 `count` 輸出（parser 容器 log，每 10 秒取一次）：

| 經過時間 | a 合法（204） | b schema_version=2（422） | c 非 gs://（422） | d 非 JSON（422） |
|---|---|---|---|---|
| +10 秒 | 1 | 2 | 2 | 2 |
| +20 秒 | 1 | 3 | 3 | 3 |
| +30 秒 | 1 | 3 | 3 | 3 |
| +40 秒 | 1 | 4 | 4 | 4 |
| +60 秒 | 1 | 5 | 5 | 5 |
| +70 秒 | 1 | 5 | 5 | 5 |

uvicorn access log 合計：`HTTP 204: 1`、`HTTP 422: 15`。第二次重跑：+30 秒 1／3／3／3，+60 與 +75 秒 1／5／5／5。

parser log 片段（第一次）：

```
12:21:55.063 INFO:main:Accepted event_id=evt-local-0001 ... message_id=1
12:21:55.064 "POST /events/crawl-fetched HTTP/1.1" 204 No Content
12:21:55.061 ERROR:main:Cannot parse message: 1 validation error for Event
12:21:55.061 ERROR:main:Cannot parse message: Expected GCS URI
12:21:55.061 ERROR:main:Cannot parse message: Unsupported event type/schema version
12:21:55.062 "POST /events/crawl-fetched HTTP/1.1" 422 Unprocessable Entity   （×3）
```

重送間隔（秒，從 parser log 時間戳計算）：

| 訊息 | 第一次 | 第二次 |
|---|---|---|
| b | 1.0, 15.0, 20.9, 20.0 | 1.0, 15.5, 19.0, 21.0 |
| c | 1.0, 16.0, 16.0, 20.0 | 1.0, 13.5, 20.0, 26.0 |
| d | 1.0, 17.0, 18.0, 22.1 | 1.0, 14.5, 18.0, 22.0 |

第 5 次失敗後模擬器把 b、c、d 轉到 `qsearch-parser-dlq`。`pubsub_e2e.py dlq`（結束碼 0）兩次都拉到 3 則，每則都帶
`CloudPubSubDeadLetterSourceDeliveryCount=5`、`CloudPubSubDeadLetterSourceSubscription=qsearch-parser-sub`，以及原本的 attribute `case`。之後 parser 不再收到這三則。

**結論：** a 只投遞一次並回 204；b、c、d 每次都回 422，被重送，滿 5 次後進 DLQ。與 `main.py` 註解描述的行為一致。

### 4. OIDC（header-echo 訂閱）

`qsearch-header-echo-sub` 設了 `oidc_token`，模擬器接受設定，但送出的 4 個 push request **都沒有 `Authorization` header**（`4 "has_authorization": false`，兩次相同）。
送出的 envelope 形狀：`{"subscription": ..., "message": {"data", "messageId", "message_id", "publishTime", "publish_time", "attributes"}}`，parser 的 `PushEnvelope` 可以正確解析（多出的欄位被忽略）。

### 5. Avro schema 在模擬器上的行為（`pubsub_e2e.py probe-schema`，結束碼 0）

建 AVRO schema 與綁 schema（encoding=JSON）的探測 topic 都成功。publish 結果：

| 訊息 | 結果 |
|---|---|
| a 合法 | ACCEPTED |
| b schema_version=2 | ACCEPTED（Avro 只檢查型別，值不檢查） |
| c 非 gs:// | ACCEPTED |
| d 非 JSON | REJECTED `400 Could not parse message` |
| 缺 `event_type` | REJECTED |
| `schema_version` 是字串 `"1"` | REJECTED |
| 多一個未定義欄位 | ACCEPTED |

意思是：正式環境的 topic 綁了 schema，d 這類訊息在 publish 時就會被擋，不會到 parser；b、c 這類「型別正確但內容不對」的訊息仍會到 parser，而且也會被 `qsearch-fetched-to-bq` 寫進 BigQuery（BigQuery 寫入未在本機驗證）。

### 6. Seek 重播（`pubsub_e2e.py seek 10`，結束碼 0）

把 `qsearch-parser-sub` Seek 回 10 分鐘前之後，已 ACK 的 a 又送了一次，parser 第二次記錄
`Accepted event_id=evt-local-0001 ... message_id=1`（兩次都看到）。b、c、d 也重新各送 5 次後再度進 DLQ（第一次的 log：最終各 10 次，DLQ 再拉到 3 則）。
這證實 `retain_acked_messages` 讓重播可行，同時也說明 parser **目前沒有冪等**：同一個 `event_id` 會被處理兩次。

### 7. Avro × Pydantic × BigQuery 欄位比對（`check_schema.py`，結束碼 0）

- `fastavro.parse_schema` 成功（`qsearch.crawl.CrawlFetchedEvent`，6 個欄位）。
- a、b、c 三個樣本 fastavro 驗證都是 VALID，二進位往返相等；d 不是 JSON。
- 三處欄位**名稱集合相同**（6 個），型別對應一致（Avro `string`／`int` ↔ Pydantic `str`／`int` ↔ BQ `STRING`／`INTEGER`）。欄位順序不同（Pydantic 把 `raw_data_uri` 放在 `event_type` 前面），JSON 編碼下沒有影響。
- 必填／可空不一致 8 項：

| 欄位 | Avro | Pydantic `Event` | BigQuery |
|---|---|---|---|
| `event_id` | 必填 | 必填 | NULLABLE |
| `schema_version` | 必填 | **預設 1** | NULLABLE |
| `job_id` | 必填 | 必填 | NULLABLE |
| `platform` | 必填 | 必填 | NULLABLE |
| `event_type` | 必填 | **預設 `crawl.fetched`** | NULLABLE |
| `raw_data_uri` | 必填 | 必填 | NULLABLE |

邊界值比較（fastavro vs 目前的 Pydantic）：缺 `event_type`、缺 `schema_version`、`schema_version` 為字串 `"1"`、為 `true`，Avro 都判 INVALID，Pydantic 都**接受**並當成 v1。多一個未定義欄位兩邊都接受。

BigQuery 全部 NULLABLE 是放寬方向；Pub/Sub BigQuery 訂閱是否接受「Avro 必填 → BQ NULLABLE」沒有驗證（模擬器的 BigQuery 訂閱不會真的寫入）。

### 8. pytest（`../tests/test_push_endpoint.py`）

| 指令 | 結束碼 | 結果 |
|---|---|---|
| 主機 venv（Python 3.13.17）：`python -m pytest -p no:cacheprovider -v -rxX tests` | 0 | **10 passed, 3 xfailed** |
| parser 映像內（Python 3.12.16）：`docker run --rm -v $PWD/tests:/app/tests:ro qsearch-gcplocal-parser:local sh -c 'pip install pytest httpx && python -m pytest -p no:cacheprovider -q -rxX tests'` | 0 | **10 passed, 3 xfailed** |

10 個通過：`/health`、a～d 四個主要情境、`event_type` 錯誤、base64 錯誤、envelope 缺 `message`、envelope 沒有 `attributes`、envelope 帶 `deliveryAttempt`。
3 個 strict xfail 是第 7 點的已知不一致（缺 `event_type`、缺 `schema_version`、`schema_version` 為字串時應回 422，目前回 204）。修好後這 3 個會變成 XPASS 而失敗，提醒移除標記。

建議修正（見下一節）已在暫存目錄的 `main.py` 副本上驗證：`pytest --runxfail` 結果 **13 passed**，結束碼 0。repo 的 `main.py` 沒有改。

### 第三次：修正後重跑

修正內容：`Event` 改成 `ConfigDict(strict=True)` 且六個欄位都必填（與 avsc 一致）、`log.exception` 改成 `log.warning`、
`requirements.txt` 拿掉 `google-cloud-pubsub`、`Dockerfile` 加 `useradd --uid 10001` 與 `USER 10001`（不再以 root 執行）。
重建映像後在全新的 compose 環境照上面步驟 0～3 重跑：

| 項目 | 結束碼 | 結果 |
|---|---|---|
| `docker exec qsearch-gcplocal-parser-1 id` | 0 | `uid=10001(app)` |
| `docker image inspect` Size | 0 | 215,028,483 bytes（原本 294,442,882） |
| `curl -i /health` | 0 | `HTTP/1.1 200 OK` |
| `pubsub_e2e.py setup`／`publish` | 0／0 | 同前 |
| `pubsub_e2e.py count`（約 75 秒後） | 0 | a 204 ×1；b、c、d 各 422 ×5；合計 `HTTP 204: 1`、`HTTP 422: 15` |
| `pubsub_e2e.py dlq` | 0 | 3 則，`CloudPubSubDeadLetterSourceDeliveryCount=5` |
| parser log 的 `Traceback` 行數 | 0 | 0（原本 30 段） |
| `check_schema.py` | 0 | Avro 與 Pydantic 六欄都必填；剩下 6 項是「Avro 必填 → BQ NULLABLE」的放寬方向 |
| 主機 venv `pytest tests` | 0 | **13 passed**（3 個 xfail 標記已移除） |

問題 2（冪等）仍未處理，屬於正式 DB 落地時的工作；問題 5 不變。

### 9. 清理

`docker compose -p qsearch-gcplocal -f compose.yaml down -v` 結束碼 0；之後 `docker ps -a --filter name=qsearch-gcplocal` 與 `docker network ls --filter name=qsearch-gcplocal` 都是 0 筆。

## 模擬器與正式環境的差異（不能用本機結果代替的部分）

官方文件（https://docs.cloud.google.com/pubsub/docs/emulator ）列出模擬器支援 "Forwarding messages to dead-letter topics"、"Retry policies on message delivery"、"Schema support for Avro"；已知限制包括 "IAM operations are not supported."、"Configurable message retention is not supported; all messages are retained indefinitely."、"BigQuery subscriptions can be created, but don't send messages to BigQuery."。模擬器啟動時也印出 "Implementation may be incomplete or differ from the real system."

本次實測到的差異：

1. **OIDC 沒有作用**：模擬器接受 `oidc_token` 設定，但不送 `Authorization` header。Cloud Run 的 `roles/run.invoker` 驗證完全沒有測到。
2. **重送間隔與正式環境不同**：第一次重送只隔約 1 秒（`minimum_backoff` 是 10 秒），之後約 13～26 秒，沒有看到指數倍增到 600 秒。正式環境的實際間隔要以 GCP 為準。
3. **死信轉送有作用**：滿 5 次轉到 DLQ，attribute 與正式環境文件描述的名稱相同。但 DLQ 需要的 IAM（服務代理的 publisher／subscriber 角色）在模擬器上不檢查。
4. **保留期不生效**：`message_retention_duration=604800s` 會被存起來，但依文件，模擬器實際上永久保留。
5. **Avro schema 會檢查**：見第 5 點。模擬器接受多出的未定義欄位，正式環境是否相同沒有驗證。
6. **BigQuery 訂閱、GCS、Cloud Run 的 HTTPS 與 IAM 都沒有測**。模擬器允許 `http` push endpoint，正式環境只允許 `https`。

## 發現的問題與建議修正

1. **（中，已修正）`Event` 的必填與型別比 Avro 寬鬆。** `schema_version`、`event_type` 有預設值，Pydantic lax 模式又會把 `"1"`、`true`、`1.0` 轉成 `1`。缺欄位或型別錯的事件會被當成 v1 接受。正式 topic 有綁 schema，多數會在 publish 時被擋，但 parser 不該依賴這一點（例如 Seek 回放舊資料、或改用沒綁 schema 的 topic）。建議修改 `main.py`：

   ```python
   from pydantic import BaseModel, ConfigDict, Field, ValidationError

   class Event(BaseModel):
       # 與 qsearch-crawl-event-v1.avsc 一致：六個欄位皆必填、型別不做寬鬆轉換。
       model_config = ConfigDict(strict=True)

       event_id: str
       schema_version: int
       job_id: str
       platform: str
       event_type: str
       raw_data_uri: str
   ```

   修改後把 `tests/test_push_endpoint.py` 的 3 個 `xfail` 標記拿掉。

2. **（中，已知）沒有冪等。** Seek 重播實測同一個 `event_id` 被接受兩次。正式版本要依 `main.py` 註解，在 DB 用 `UNIQUE(event_id)` upsert 後再回 204。

3. **（低，log 部分已修正）永久性錯誤也會重送 5 次。** b、c、d 是內容錯誤，重送不會變好，卻各送 5 次、每次印一段完整 traceback（第一次實跑最後共 30 段）。建議：永久性錯誤直接寫入隔離區後回 204，或至少把 `log.exception` 改成 `log.warning("Cannot parse message: %s", exc)`。

4. **（低，已修正）`requirements.txt` 有用不到的 `google-cloud-pubsub`。** `main.py` 沒有 import 它。拿掉這一行後映像從 294,442,882 bytes 降到 214,985,730 bytes（`docker image inspect` 的 Size）。建議刪除 `google-cloud-pubsub>=2.20,<3`。

5. **（資訊）`ingest_events` 會收到內容錯誤的事件。** `qsearch-fetched-to-bq` 直接用 topic schema 寫入，b、c 這類通過 Avro 但被 parser 拒絕的事件仍會進 BigQuery。分析時要自行過濾 `schema_version` 與 `raw_data_uri`。
