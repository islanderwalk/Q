"""POST /events/crawl-fetched 與 GET /health 的單元測試（FastAPI TestClient，不需網路與 GCP）。

四個主要情境對應 local/VERIFY.md 的模擬器實測：
  a 合法事件 -> 204；b schema_version 錯 -> 422；c raw_data_uri 不是 gs:// -> 422；d 非 JSON -> 422。
"""
import base64
import json
import logging

import pytest

ENDPOINT = "/events/crawl-fetched"


def envelope(data: bytes, message_id: str = "1", **message_extra) -> dict:
    """Pub/Sub push（wrapped）envelope。欄位形狀取自模擬器實際送出的內容（header_echo 紀錄）。"""
    msg = {
        "data": base64.b64encode(data).decode(),
        "messageId": message_id,
        "message_id": message_id,
        "publishTime": "2026-10-30T12:21:54.343Z",
        "publish_time": "2026-10-30T12:21:54.343Z",
        "attributes": {"case": "test"},
    }
    msg.update(message_extra)
    return {"message": msg, "subscription": "projects/qsearch-local/subscriptions/qsearch-parser-sub"}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_a_valid_event_returns_204(client, fixture_bytes, caplog):
    caplog.set_level(logging.INFO)
    r = client.post(ENDPOINT, json=envelope(fixture_bytes("a_valid.json")))
    assert r.status_code == 204
    assert r.content == b""
    assert "Accepted event_id=evt-local-0001" in caplog.text


def test_b_wrong_schema_version_returns_422(client, fixture_bytes):
    r = client.post(ENDPOINT, json=envelope(fixture_bytes("b_bad_schema_version.json")))
    assert r.status_code == 422
    assert r.json() == {"detail": "Invalid crawl event"}


def test_c_non_gcs_uri_returns_422(client, fixture_bytes):
    r = client.post(ENDPOINT, json=envelope(fixture_bytes("c_non_gcs_uri.json")))
    assert r.status_code == 422
    assert r.json() == {"detail": "Invalid crawl event"}


def test_d_garbage_returns_422(client, fixture_bytes):
    r = client.post(ENDPOINT, json=envelope(fixture_bytes("d_garbage.txt")))
    assert r.status_code == 422
    assert r.json() == {"detail": "Invalid crawl event"}


# ---- 補充情境 ----

def test_wrong_event_type_returns_422(client, fixture_bytes):
    ev = json.loads(fixture_bytes("a_valid.json"))
    ev["event_type"] = "crawl.failed"
    r = client.post(ENDPOINT, json=envelope(json.dumps(ev).encode()))
    assert r.status_code == 422


def test_invalid_base64_returns_422(client):
    body = envelope(b"x")
    body["message"]["data"] = "!!!not-base64!!!"
    r = client.post(ENDPOINT, json=body)
    assert r.status_code == 422


def test_envelope_without_message_returns_422(client):
    # FastAPI 自己的 request 驗證（不是 handler 的 422），回應 body 形狀不同。
    r = client.post(ENDPOINT, json={"subscription": "x"})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)


def test_envelope_without_attributes_returns_204(client, fixture_bytes):
    body = envelope(fixture_bytes("a_valid.json"))
    del body["message"]["attributes"]
    r = client.post(ENDPOINT, json=body)
    assert r.status_code == 204


def test_envelope_with_delivery_attempt_returns_204(client, fixture_bytes):
    # 設了 dead_letter_policy 時，正式 Pub/Sub 的 push body 會多 deliveryAttempt；多出的欄位要能忽略。
    body = envelope(fixture_bytes("a_valid.json"))
    body["deliveryAttempt"] = 3
    r = client.post(ENDPOINT, json=body)
    assert r.status_code == 204


# ---- Avro 必填與嚴格型別（2026-10-30 修正 main.py 後由 xfail 轉為一般測試）----

def test_missing_event_type_should_be_rejected(client, fixture_bytes):
    ev = json.loads(fixture_bytes("a_valid.json"))
    del ev["event_type"]
    r = client.post(ENDPOINT, json=envelope(json.dumps(ev).encode()))
    assert r.status_code == 422


def test_missing_schema_version_should_be_rejected(client, fixture_bytes):
    ev = json.loads(fixture_bytes("a_valid.json"))
    del ev["schema_version"]
    r = client.post(ENDPOINT, json=envelope(json.dumps(ev).encode()))
    assert r.status_code == 422


def test_schema_version_as_string_should_be_rejected(client, fixture_bytes):
    ev = json.loads(fixture_bytes("a_valid.json"))
    ev["schema_version"] = "1"
    r = client.post(ENDPOINT, json=envelope(json.dumps(ev).encode()))
    assert r.status_code == 422
