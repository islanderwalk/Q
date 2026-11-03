"""parser push 端點的行為測試：合法 envelope、壞 envelope／壞事件、ack 與重送語意、重複投遞。

Pub/Sub push 只把 102、200、201、202、204 當作 ack（docs.cloud.google.com/pubsub/docs/push，2026-10-30 查閱）；
其他狀態碼都是 nack，Pub/Sub 依 retry_policy 重送，達 max_delivery_attempts 後轉 DLQ。
"""
import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from parser_cases import ACK, PATH, VALID_EVENT, b64, without


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_valid_envelope_is_acked(client, envelope):
    r = client.post(PATH, json=envelope())
    assert r.status_code == 204 and r.status_code in ACK
    assert r.content == b""


def test_full_wrapped_envelope_is_acked(client, valid_event):
    """文件「最大」範例的欄位：message_id／publishTime／publish_time／orderingKey 與頂層 deliveryAttempt 都要能收。"""
    body = {
        "message": {
            "attributes": {"platform": "ptt", "crawler": "worker-1"},
            "data": b64(json.dumps(valid_event).encode("utf-8")),
            "messageId": "2070443601311540",
            "message_id": "2070443601311540",
            "orderingKey": "ptt",
            "publishTime": "2026-10-30T07:00:00.000Z",
            "publish_time": "2026-10-30T07:00:00.000Z",
        },
        "subscription": "projects/demo/subscriptions/qsearch-parser-sub",
        "deliveryAttempt": 3,
    }
    assert client.post(PATH, json=body).status_code == 204


@pytest.mark.parametrize("attributes", [{}, {"platform": "ptt", "crawler": "worker-1"}], ids=["empty", "two"])
def test_attributes_are_optional(client, envelope, attributes):
    """parser 不靠 attributes 判斷任何事（欄位都在 data 裡），有沒有都 ack。"""
    assert client.post(PATH, json=envelope(attributes=attributes)).status_code == 204


def test_missing_attributes_key_is_acked(client, envelope):
    body = envelope()
    del body["message"]["attributes"]
    assert client.post(PATH, json=body).status_code == 204


def test_accept_log_has_ids(client, envelope, valid_event, parser_main, caplog):
    caplog.set_level(logging.INFO, logger=parser_main.log.name)
    client.post(PATH, json=envelope(message_id="m-42"))
    accepted = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Accepted")]
    assert len(accepted) == 1
    for value in (valid_event["event_id"], valid_event["job_id"], valid_event["raw_data_uri"], "m-42"):
        assert value in accepted[0]


@pytest.mark.parametrize("body", [
    b"",
    b"not json",
    b"[]",
    b'{"subscription": "projects/demo/subscriptions/s"}',          # 沒有 message
    b'{"message": {"messageId": "1", "attributes": {"a": "b"}}}',  # 沒有 data
    b'{"message": {"data": 123}}',                                 # data 不是字串
    b'{"message": {"data": "e30=", "attributes": {"a": 1}}}',       # attribute 值不是字串
    b'{"message": {"data": "e30=", "attributes": null}}',
    json.dumps(VALID_EVENT).encode("utf-8"),                       # 未包裝（no_wrapper）：事件本身當 body
], ids=["empty", "not-json", "array", "no-message", "no-data", "data-int", "attr-int", "attr-null", "unwrapped"])
def test_malformed_envelope_is_nacked(client, body):
    r = client.post(PATH, content=body, headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.status_code not in ACK


@pytest.mark.parametrize("data", [
    "",
    "!!!not-base64!!!",
    "é",
    b64(b"hello"),
    b64(b"[]"),
    b64(b'{"event_id": "\xff"}'),
], ids=["empty", "not-base64", "non-ascii", "not-json", "json-array", "invalid-utf8"])
def test_bad_data_is_nacked(client, envelope, data):
    r = client.post(PATH, json=envelope(data=data))
    assert r.status_code == 422 and r.json() == {"detail": "Invalid crawl event"}


# 標 * 的情況在修正前會被 ack（204），分兩類：
# - 少 schema_version／event_type、schema_version 是 True、"1"、1.0：Avro 不收，經過有 schema 的主題時在發佈端就被擋，
#   parser 拒收是與 .avsc 對齊的防線（只對繞過 schema 的訊息有差）。
# - "gs://" 開頭但沒有 bucket 或 object 的 URI：Avro 合法、發佈得出去，修正前會被 ack 掉，是實際的 bug。
@pytest.mark.parametrize("mutate", [
    lambda e: {**e, "event_type": "crawl.failed"},
    lambda e: {**e, "schema_version": 2},
    lambda e: without(e, "event_id"),
    lambda e: without(e, "job_id"),
    lambda e: without(e, "platform"),
    lambda e: without(e, "raw_data_uri"),
    lambda e: without(e, "schema_version"),      # *
    lambda e: without(e, "event_type"),          # *
    lambda e: {**e, "schema_version": True},      # *
    lambda e: {**e, "schema_version": "1"},       # *
    lambda e: {**e, "schema_version": 1.0},       # *
    lambda e: {**e, "event_id": 123},
    lambda e: {**e, "platform": None},
    lambda e: {**e, "raw_data_uri": "https://example.com/a.html"},
    lambda e: {**e, "raw_data_uri": "gs://"},          # *
    lambda e: {**e, "raw_data_uri": "gs://bucket"},    # *
    lambda e: {**e, "raw_data_uri": "gs://bucket/"},   # *
    lambda e: {**e, "raw_data_uri": "gs:///obj"},      # *
], ids=[
    "wrong-type", "version-2", "no-event_id", "no-job_id", "no-platform", "no-raw_data_uri",
    "no-schema_version", "no-event_type", "version-bool", "version-str", "version-float",
    "event_id-int", "platform-null", "uri-https", "uri-gs-empty", "uri-no-object",
    "uri-trailing-slash", "uri-no-bucket",
])
def test_invalid_event_is_nacked(client, envelope, valid_event, mutate):
    r = client.post(PATH, json=envelope(mutate(valid_event)))
    assert r.status_code == 422 and r.status_code not in ACK


def test_extra_event_field_is_ignored(client, envelope, valid_event):
    """多出的欄位不擋（與 fastavro.validate 預設一致），方便 schema 之後加欄位。"""
    assert client.post(PATH, json=envelope({**valid_event, "fetched_bytes": 261523})).status_code == 204


def test_duplicate_delivery_is_acked_each_time(client, envelope, valid_event, parser_main, caplog):
    """Pub/Sub 至少一次投遞：同一則訊息送兩次，兩次都 ack。
    dry-run 沒有副作用，重複 ack 無害；但服務也沒有去重，正式版要在回 204 之前以 UNIQUE(event_id) 寫入。"""
    caplog.set_level(logging.INFO, logger=parser_main.log.name)
    body = envelope()
    assert [client.post(PATH, json=body).status_code for _ in range(2)] == [204, 204]
    accepted = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Accepted")]
    assert len(accepted) == 2 and all(valid_event["event_id"] in m for m in accepted)


def test_poison_message_is_nacked_on_every_attempt(client, envelope, valid_event, tf_block):
    """永久錯誤（schema_version=2）每次都回 422。Pub/Sub 共投遞約 max_delivery_attempts 次（首次加重送 4 次，best effort）
    後轉 parser DLQ；投遞次數的定義是 1 + nack 次數 + ack 逾時次數（REST reference DeadLetterPolicy.maxDeliveryAttempts）。"""
    sub = tf_block("pubsub.tf", "google_pubsub_subscription", "qsearch_parser_sub")
    attempts = int(re.search(r"max_delivery_attempts\s*=\s*(\d+)", sub).group(1))
    body = envelope({**valid_event, "schema_version": 2})
    codes = [client.post(PATH, json=body).status_code for _ in range(attempts)]
    assert attempts == 5 and codes == [422] * attempts


def test_unexpected_error_is_500_and_nacked(parser_main, envelope, monkeypatch):
    """驗證通過後的處理（正式版是抓 GCS、寫 DB）若丟出非 ValueError 例外，回 500，同樣是 nack、會重送。"""
    def boom(*args, **kwargs):
        raise RuntimeError("downstream unavailable")
    monkeypatch.setattr(parser_main.log, "info", boom)
    r = TestClient(parser_main.app, raise_server_exceptions=False).post(PATH, json=envelope())
    assert r.status_code == 500 and r.status_code not in ACK


def test_wrong_path_or_method_is_not_acked(client, envelope):
    """若 push_endpoint 寫成舊 snippet 的 /push，每則訊息都會 404 → 重送 → DLQ。"""
    assert client.post("/push", json=envelope()).status_code == 404
    assert client.get(PATH).status_code == 405
