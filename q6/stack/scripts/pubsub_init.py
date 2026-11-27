"""建 Pub/Sub 主題與訂閱（冪等：已存在回 409，視為成功）。
Pub/Sub 模擬器只存在記憶體，容器一重啟主題和訂閱就全沒了，seg 服務會因「Subscription does not exist」退出。
所以 docker compose 每次啟動先跑一次這支（pubsub-init 服務），seg 服務等它成功才起；scripts/setup.py 也呼叫同一份定義。
只用標準函式庫，任何有 Python 3 的映像都能跑：python pubsub_init.py [--wait 秒數]"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

PUBSUB = os.environ.get("PUBSUB", "http://localhost:8085")
PROJECT = os.environ.get("PUBSUB_PROJECT", "qsearch-local")


def _dlq(topic):
    return {"deadLetterTopic": f"projects/{PROJECT}/topics/{topic}", "maxDeliveryAttempts": 5}


TOPICS = ("parsed-posts", "parsed-posts-dlq", "dict-releases")
# parsed-posts：v0／v1／v2 各一個訂閱（A/B 期間各寫各的欄位），失敗 5 次進 DLQ
# dict-releases：PM 頁發版 → seg-v2 服務重載字典並定向重跑（seg/qseg/control.py）
SUBSCRIPTIONS = {
    "parsed-posts-seg-v2": {"topic": "parsed-posts", "ackDeadlineSeconds": 60, "deadLetterPolicy": _dlq("parsed-posts-dlq")},
    "parsed-posts-seg-v1": {"topic": "parsed-posts", "ackDeadlineSeconds": 60, "deadLetterPolicy": _dlq("parsed-posts-dlq")},
    "parsed-posts-seg-v0": {"topic": "parsed-posts", "ackDeadlineSeconds": 60, "deadLetterPolicy": _dlq("parsed-posts-dlq")},
    "parsed-posts-dlq-sub": {"topic": "parsed-posts-dlq"},
    "dict-releases-seg-v2": {"topic": "dict-releases", "ackDeadlineSeconds": 600},
}


def _put(path, body):
    req = urllib.request.Request(f"{PUBSUB}/v1/projects/{PROJECT}/{path}", data=json.dumps(body).encode(),
                                 method="PUT", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        if e.code == 409:          # 已存在
            return 409
        raise


def wait_ready(seconds):
    deadline = time.time() + seconds
    while True:
        try:
            with urllib.request.urlopen(PUBSUB + "/", timeout=5):
                return
        except (urllib.error.URLError, OSError):
            if time.time() > deadline:
                raise SystemExit(f"Pub/Sub 模擬器 {PUBSUB} 在 {seconds} 秒內沒有回應")
            time.sleep(2)


def ensure():
    for t in TOPICS:
        print("topic", t, _put(f"topics/{t}", {}), flush=True)
    for name, spec in SUBSCRIPTIONS.items():
        body = dict(spec, topic=f"projects/{PROJECT}/topics/{spec['topic']}")
        print("subscription", name, _put(f"subscriptions/{name}", body), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=0, help="先等模擬器回應最多幾秒")
    a = ap.parse_args(argv)
    if a.wait:
        wait_ready(a.wait)
    ensure()


if __name__ == "__main__":
    sys.exit(main())
