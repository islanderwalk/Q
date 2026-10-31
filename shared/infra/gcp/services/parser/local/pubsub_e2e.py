"""本機 Pub/Sub 模擬器端到端驗證腳本（不連真的 GCP）。

需先設定 PUBSUB_EMULATOR_HOST（例如 127.0.0.1:18285），google-cloud-pubsub 才會改連模擬器、
不需要任何憑證。資源名稱取自 shared/infra/gcp/pubsub.tf。

子命令：
  setup         建 topic、DLQ、push 訂閱（parser 與 header-echo 各一條），並回報模擬器接受／拒絕哪些設定
  publish       發佈 a～d 四則測試訊息到 qsearch-crawl-fetched
  probe-schema  建 Avro schema 與綁 schema 的探測 topic，看模擬器在 publish 時擋下哪些訊息
  dlq           從 qsearch-parser-dlq-inspect 拉訊息（確認死信轉送）
  seek          把 qsearch-parser-sub Seek 回 N 分鐘前（確認保留已 ACK 訊息後可重播）
  count         從 stdin 讀 parser 容器 log，統計每一類訊息被投遞幾次
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

from google.api_core import exceptions as gexc
from google.cloud import pubsub_v1
from google.protobuf import duration_pb2, timestamp_pb2
from google.pubsub_v1.types import (
    DeadLetterPolicy,
    Encoding,
    PushConfig,
    RetryPolicy,
    Schema,
    SchemaSettings,
)

PROJECT = os.environ.get("PUBSUB_PROJECT_ID", "qsearch-local")
HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "tests" / "fixtures"
AVSC = HERE.parents[2] / "schemas" / "qsearch-crawl-event-v1.avsc"

TOPIC = "qsearch-crawl-fetched"
DLQ_TOPIC = "qsearch-parser-dlq"
DLQ_SUB = "qsearch-parser-dlq-inspect"
PARSER_SUB = "qsearch-parser-sub"
ECHO_SUB = "qsearch-header-echo-sub"  # 本機專用，pubsub.tf 沒有
PROBE_TOPIC = "qsearch-crawl-fetched-schema-probe"  # 本機專用
SCHEMA_ID = "qsearch-crawl-event-v1"
# 模擬器容器看得到的 compose 服務名稱（不是 127.0.0.1）。
PARSER_ENDPOINT = os.environ.get("PARSER_PUSH_ENDPOINT", "http://parser:8080/events/crawl-fetched")
ECHO_ENDPOINT = os.environ.get("ECHO_PUSH_ENDPOINT", "http://header-echo:8080/echo")
PUSH_SA = "qsearch-push@qsearch-local.iam.gserviceaccount.com"  # 假的 SA，模擬器不驗證

SAMPLES = {
    "a_valid": (FIXTURES / "a_valid.json").read_bytes().strip(),
    "b_bad_schema_version": (FIXTURES / "b_bad_schema_version.json").read_bytes().strip(),
    "c_non_gcs_uri": (FIXTURES / "c_non_gcs_uri.json").read_bytes().strip(),
    "d_garbage": (FIXTURES / "d_garbage.txt").read_bytes().strip(),
}


def _dur(seconds: int) -> duration_pb2.Duration:
    return duration_pb2.Duration(seconds=seconds)


def _try(label: str, fn):
    try:
        out = fn()
        print(f"[OK]   {label}")
        return out
    except gexc.AlreadyExists:
        print(f"[OK]   {label}（已存在）")
        return None
    except Exception as exc:  # 記錄模擬器拒絕的設定
        print(f"[FAIL] {label}: {type(exc).__name__}: {exc}")
        return exc


def setup() -> None:
    pub = pubsub_v1.PublisherClient()
    sub = pubsub_v1.SubscriberClient()
    t = pub.topic_path(PROJECT, TOPIC)
    dlq = pub.topic_path(PROJECT, DLQ_TOPIC)
    _try(f"create topic {TOPIC}（不綁 schema，讓 d 能送到 parser；schema 另見 probe-schema）",
         lambda: pub.create_topic(name=t))
    _try(f"create topic {DLQ_TOPIC}", lambda: pub.create_topic(name=dlq))
    _try(f"create pull sub {DLQ_SUB}",
         lambda: sub.create_subscription(request={
             "name": sub.subscription_path(PROJECT, DLQ_SUB), "topic": dlq, "ack_deadline_seconds": 30}))

    def parser_sub(with_oidc: bool):
        push = PushConfig(push_endpoint=PARSER_ENDPOINT)
        if with_oidc:
            push.oidc_token = PushConfig.OidcToken(service_account_email=PUSH_SA)
        return sub.create_subscription(request={
            "name": sub.subscription_path(PROJECT, PARSER_SUB),
            "topic": t,
            "ack_deadline_seconds": 60,
            "retain_acked_messages": True,
            "message_retention_duration": _dur(604800),
            "push_config": push,
            "retry_policy": RetryPolicy(minimum_backoff=_dur(10), maximum_backoff=_dur(600)),
            "dead_letter_policy": DeadLetterPolicy(dead_letter_topic=dlq, max_delivery_attempts=5),
        })

    res = _try(f"create push sub {PARSER_SUB}（設定照 pubsub.tf：OIDC、retry 10s–600s、DLQ max 5、保留 7 天）",
               lambda: parser_sub(True))
    if isinstance(res, Exception):
        _try(f"create push sub {PARSER_SUB}（去掉 OIDC 重試）", lambda: parser_sub(False))

    echo_push = PushConfig(push_endpoint=ECHO_ENDPOINT,
                           oidc_token=PushConfig.OidcToken(service_account_email=PUSH_SA))
    _try(f"create push sub {ECHO_SUB}（帶 OIDC，檢查模擬器是否送 Authorization header）",
         lambda: sub.create_subscription(request={
             "name": sub.subscription_path(PROJECT, ECHO_SUB), "topic": t,
             "ack_deadline_seconds": 60, "push_config": echo_push}))

    got = sub.get_subscription(subscription=sub.subscription_path(PROJECT, PARSER_SUB))
    print("--- 模擬器回傳的 qsearch-parser-sub 設定 ---")
    print(type(got).to_json(got, indent=2) if hasattr(type(got), "to_json") else got)


def publish() -> None:
    pub = pubsub_v1.PublisherClient()
    t = pub.topic_path(PROJECT, TOPIC)
    for name, data in SAMPLES.items():
        mid = pub.publish(t, data, case=name).result(timeout=30)
        print(f"published {name} message_id={mid} at={time.strftime('%H:%M:%S')}")


def probe_schema() -> None:
    sch = pubsub_v1.SchemaServiceClient()
    pub = pubsub_v1.PublisherClient()
    schema_path = sch.schema_path(PROJECT, SCHEMA_ID)
    _try(f"create AVRO schema {SCHEMA_ID}（{AVSC.name}）",
         lambda: sch.create_schema(request={
             "parent": f"projects/{PROJECT}",
             "schema": Schema(name=schema_path, type_=Schema.Type.AVRO, definition=AVSC.read_text()),
             "schema_id": SCHEMA_ID}))
    probe = pub.topic_path(PROJECT, PROBE_TOPIC)
    _try(f"create topic {PROBE_TOPIC}（schema_settings: {SCHEMA_ID}, encoding=JSON）",
         lambda: pub.create_topic(request={
             "name": probe,
             "schema_settings": SchemaSettings(schema=schema_path, encoding=Encoding.JSON)}))
    extra = {
        "e_missing_event_type": json.dumps({k: v for k, v in json.loads(SAMPLES["a_valid"]).items()
                                            if k != "event_type"}).encode(),
        "f_schema_version_as_string": json.dumps({**json.loads(SAMPLES["a_valid"]),
                                                  "schema_version": "1"}).encode(),
        "g_extra_field": json.dumps({**json.loads(SAMPLES["a_valid"]), "unexpected": "x"}).encode(),
    }
    for name, data in {**SAMPLES, **extra}.items():
        try:
            mid = pub.publish(probe, data).result(timeout=30)
            print(f"probe publish {name}: ACCEPTED message_id={mid}")
        except Exception as exc:
            print(f"probe publish {name}: REJECTED {type(exc).__name__}: {str(exc)[:200]}")


def dlq() -> None:
    sub = pubsub_v1.SubscriberClient()
    path = sub.subscription_path(PROJECT, DLQ_SUB)
    resp = sub.pull(request={"subscription": path, "max_messages": 50}, timeout=15)
    print(f"DLQ messages pulled: {len(resp.received_messages)}")
    for rm in resp.received_messages:
        print(f"  dlq message_id={rm.message.message_id} attrs={dict(rm.message.attributes)} "
              f"data={rm.message.data[:80]!r}")
    if resp.received_messages:
        sub.acknowledge(request={"subscription": path,
                                 "ack_ids": [rm.ack_id for rm in resp.received_messages]})


def seek(minutes: int) -> None:
    sub = pubsub_v1.SubscriberClient()
    ts = timestamp_pb2.Timestamp()
    ts.FromSeconds(int(time.time()) - minutes * 60)
    sub.seek(request={"subscription": sub.subscription_path(PROJECT, PARSER_SUB), "time": ts})
    print(f"seek {PARSER_SUB} to now-{minutes}min OK at={time.strftime('%H:%M:%S')}")


PATTERNS = {
    "a_valid(204, Accepted)": re.compile(r"Accepted event_id=evt-local-0001"),
    "b_bad_schema_version(422)": re.compile(r"Cannot parse message: Unsupported event type/schema version"),
    "c_non_gcs_uri(422)": re.compile(r"Cannot parse message: Expected GCS URI"),
    "d_garbage(422)": re.compile(r"Cannot parse message: 1 validation error for Event"),
}


def count() -> None:
    text = sys.stdin.read()
    c = Counter()
    for line in text.splitlines():
        for k, p in PATTERNS.items():
            if p.search(line):
                c[k] += 1
        m = re.search(r'"POST /events/crawl-fetched HTTP/1.1" (\d{3})', line)
        if m:
            c[f"HTTP {m.group(1)}"] += 1
    for k in list(PATTERNS) + sorted(k for k in c if k.startswith("HTTP")):
        print(f"{k}: {c.get(k, 0)}")


if __name__ == "__main__":
    if "PUBSUB_EMULATOR_HOST" not in os.environ and sys.argv[1:2] != ["count"]:
        sys.exit("請先設定 PUBSUB_EMULATOR_HOST，避免連到真的 GCP")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "setup":
        setup()
    elif cmd == "publish":
        publish()
    elif cmd == "probe-schema":
        probe_schema()
    elif cmd == "dlq":
        dlq()
    elif cmd == "seek":
        seek(int(sys.argv[2]) if len(sys.argv) > 2 else 10)
    elif cmd == "count":
        count()
    else:
        sys.exit(__doc__)
