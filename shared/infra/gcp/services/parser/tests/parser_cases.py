"""parser 測試共用的常數與小工具（conftest 與各測試檔都從這裡匯入）。

不用 `from conftest import ...`：repo 其他題目若也放 conftest.py，一起收集時 `conftest` 這個模組名會指到別人的檔案。
"""
import base64
import json
import re
from pathlib import Path

PARSER_DIR = Path(__file__).resolve().parents[1]   # shared/infra/gcp/services/parser
GCP_DIR = PARSER_DIR.parents[1]                     # shared/infra/gcp
AVSC_PATH = GCP_DIR / "schemas" / "qsearch-crawl-event-v1.avsc"
AVSC = json.loads(AVSC_PATH.read_text(encoding="utf-8"))

PATH = "/events/crawl-fetched"
# Pub/Sub push 只把這些狀態碼當 ack，其他都是 nack（docs.cloud.google.com/pubsub/docs/push，2026-10-30 查閱）。
ACK = frozenset({102, 200, 201, 202, 204})

# 對應 qsearch-crawl-event-v1.avsc 的一筆合法事件。
VALID_EVENT = {
    "event_id": "evt-0001",
    "schema_version": 1,
    "job_id": "job-ptt-gossiping-39390",
    "platform": "ptt",
    "event_type": "crawl.fetched",
    "raw_data_uri": "gs://demo-raw-scraped/ptt/Gossiping/M.1793000000.A.123.html",
}


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def without(event, key):
    return {k: v for k, v in event.items() if k != key}


def tf_text(filename):
    """讀 .tf 並去掉 # 註解。這幾個 .tf 的字串裡沒有 #（有的話先失敗，免得誤刪內容）。"""
    text = (GCP_DIR / filename).read_text(encoding="utf-8")
    assert not re.search(r'"[^"\n]*#[^"\n]*"', text), f"{filename} 的字串含 #，去註解的做法要改"
    return re.sub(r"#.*$", "", text, flags=re.M)


def tf_block(filename, rtype, name):
    """回傳指定 resource 區塊的文字（已去註解）。"""
    text = tf_text(filename)
    m = re.search(rf'resource\s+"{rtype}"\s+"{name}"\s*\{{', text)
    assert m, f"{filename} 找不到 {rtype}.{name}"
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.start():i]
