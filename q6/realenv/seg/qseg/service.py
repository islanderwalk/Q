"""串流斷詞服務（Cloud Run 服務）：訂閱 parsed 主題，斷詞後條件寫入 ES，寫成功才 ack（至少一次＋冪等）。
本機用 Pub/Sub 官方模擬器（PUBSUB_EMULATOR_HOST），程式碼與連真 GCP 相同。"""
import json
import queue
import threading
import time

from google.cloud import pubsub_v1

from . import config, dictionary, metrics
from .es import ES
from .logs import log
from .pipeline import base_doc, seg_fields, seg_ver, segment

BATCH, FLUSH_SEC = 200, 1.0


def run():
    dic = dictionary.load()
    es = ES()
    es.put_guard_script()
    metrics.serve(config.METRICS_PORT)
    metrics.DICT.labels(dic.pipeline, dic.version, str(seg_ver(dic.pipeline, dic.version)), config.SEG_BUILD).set(1)
    log("seg_service_start", pipeline=dic.pipeline, dict_version=dic.version, subscription=config.SUBSCRIPTION,
        seg_ver=seg_ver(dic.pipeline, dic.version), build=config.SEG_BUILD)
    q: "queue.Queue" = queue.Queue()
    sub = pubsub_v1.SubscriberClient()
    path = sub.subscription_path(config.PROJECT, config.SUBSCRIPTION)
    flow = pubsub_v1.types.FlowControl(max_messages=1000)
    future = sub.subscribe(path, callback=q.put, flow_control=flow)

    while True:
        msgs, deadline = [], time.time() + FLUSH_SEC
        while len(msgs) < BATCH and time.time() < deadline:
            try:
                msgs.append(q.get(timeout=max(0.01, deadline - time.time())))
            except queue.Empty:
                break
        if not msgs:
            if future.done():
                future.result()
            continue
        t0 = time.perf_counter()
        items, ok_msgs = [], []
        for m in msgs:
            try:
                post = json.loads(m.data.decode("utf-8"))
                items.append((post["post_id"], base_doc(post), seg_fields(dic, segment(dic, post["channel"], post.get("text", ""))), dic.pipeline))
                ok_msgs.append(m)
            except Exception as e:  # 壞訊息：nack 讓它進 DLQ（GCP 上由訂閱的 dead_letter_policy 決定）
                log("bad_message", "ERROR", error=str(e)[:200])
                metrics.MSGS.labels(dic.pipeline, "bad").inc()
                m.nack()
        seg_ms = (time.perf_counter() - t0) * 1000
        metrics.SEG_SEC.labels(dic.pipeline, "stream").observe(seg_ms / 1000)
        if not items:
            continue
        try:
            res = es.bulk_seg(items)
        except Exception as e:
            log("bulk_failed", "ERROR", error=str(e)[:300], n=len(items))
            for m in ok_msgs:
                m.nack()
            metrics.MSGS.labels(dic.pipeline, "nack").inc(len(ok_msgs))
            continue
        metrics.BULK_SEC.labels(dic.pipeline, "stream").observe(res["took_ms"] / 1000)
        for k in ("created", "updated", "noop", "errors"):
            metrics.DOCS.labels(dic.pipeline, "stream", k).inc(res[k])
        now, failed = time.time(), set(res.pop("failed_ids"))
        for (pid, *_), m in zip(items, ok_msgs):
            if pid in failed:          # 這篇沒寫成功：nack，等重送（寫入是冪等的）
                m.nack()
                metrics.MSGS.labels(dic.pipeline, "nack").inc()
                continue
            metrics.LAG.labels(dic.pipeline).observe(now - m.publish_time.timestamp())
            m.ack()
            metrics.MSGS.labels(dic.pipeline, "ack").inc()
        log("seg_batch", pipeline=dic.pipeline, n=len(items), seg_ms=round(seg_ms, 1), **res)
