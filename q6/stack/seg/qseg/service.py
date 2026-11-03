"""串流斷詞服務（Cloud Run 服務）：訂閱 parsed 主題，斷詞後條件寫入 ES，寫成功才 ack（至少一次＋冪等）。
本機用 Pub/Sub 官方模擬器（PUBSUB_EMULATOR_HOST），程式碼與連真 GCP 相同。

v2 服務另外訂閱字典發版主題（control.py）：收到發版訊息就重載字典、定向重跑；之後的串流訊息立刻用新版字典；
每台另外輪詢 current/manifest.json 收斂。目前載入的字典版本與心跳寫在 PostgreSQL service_state，PM 頁會顯示。
串流也走 segment_checked（必不可拆的詞被切開就計數 qseg_must_keep_split_total）；訊息屬性 action=delete 是刪文（只寫 deleted_at），
edited=1 是改文（content_sha 不同，seg_guard 會重寫本體與詞表）。"""
import json
import queue
import threading
import time

from google.cloud import pubsub_v1

from . import config, control, dictionary, metrics
from .backlog import Tracker
from .es import ES
from .logs import log
from .pipeline import base_doc, seg_fields, seg_ver, segment_checked

BATCH, FLUSH_SEC, HEARTBEAT_SEC = 200, 1.0, 15.0


def run():
    state = control.SegState(dictionary.load())
    es = ES()
    es.put_guard_script()
    metrics.serve(config.METRICS_PORT)
    dic = state.dic
    metrics.DICT.labels(dic.pipeline, dic.version, str(seg_ver(dic.pipeline, dic.version)), config.SEG_BUILD).set(1)
    for r in ("updated", "missing", "errors"):
        metrics.TOMBSTONES.labels(dic.pipeline, r)
    metrics.EDITED.labels(dic.pipeline)
    metrics.BULK_RETRIED.labels(dic.pipeline, "stream")
    control.ensure_tables()
    control.record_service_state(dic)
    log("seg_service_start", pipeline=dic.pipeline, dict_version=dic.version, subscription=config.SUBSCRIPTION,
        seg_ver=seg_ver(dic.pipeline, dic.version), build=config.SEG_BUILD,
        control_subscription=config.CONTROL_SUBSCRIPTION if dic.pipeline == "v2" else None)
    control_future = control.start(state, es)
    q: "queue.Queue" = queue.Queue()
    tracker = Tracker(dic.pipeline)   # P0-4：收到就記下 publish_time，ack／nack 後移除；最舊的那則＝積壓年齡

    def on_message(m):
        tracker.received(m.message_id, m.publish_time.timestamp())
        tracker.publish()   # 串流被 bulk_seg 卡住時（最長 300 秒）也要看得到較舊的新訊息；Gauge.set 與 Tracker 皆有鎖，可在 callback 執行緒呼叫
        q.put(m)

    sub = pubsub_v1.SubscriberClient()
    path = sub.subscription_path(config.PROJECT, config.SUBSCRIPTION)
    flow = pubsub_v1.types.FlowControl(max_messages=1000)
    future = sub.subscribe(path, callback=on_message, flow_control=flow)
    last_beat = time.time()

    while True:
        tracker.publish()
        msgs, deadline = [], time.time() + FLUSH_SEC
        while len(msgs) < BATCH and time.time() < deadline:
            try:
                msgs.append(q.get(timeout=max(0.01, deadline - time.time())))
            except queue.Empty:
                break
        if time.time() - last_beat > HEARTBEAT_SEC:
            control.heartbeat()
            last_beat = time.time()
        if not msgs:
            if future.done():
                future.result()
            if control_future is not None and control_future.done():
                control_future.result()
            continue
        dic = state.dic   # 這一批用當下的字典；發版後下一批就是新版
        t0 = time.perf_counter()
        items, ok_msgs, tombs, tomb_msgs = [], [], [], []
        for m in msgs:
            try:
                post = json.loads(m.data.decode("utf-8"))
                attrs = dict(getattr(m, "attributes", {}) or {})
                if attrs.get("action") == "delete":   # 刪文：只寫 deleted_at，不碰詞表；文字雲查詢會排除
                    tombs.append((post["post_id"], post.get("deleted_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
                    tomb_msgs.append(m)
                    continue
                if attrs.get("edited") == "1":
                    metrics.EDITED.labels(dic.pipeline).inc()
                terms, bad = segment_checked(dic, post["channel"], post.get("text", ""))
                for w in bad:
                    metrics.SPLIT.labels(dic.pipeline, post.get("channel", "")).inc()
                items.append((post["post_id"], base_doc(post), seg_fields(dic, terms), dic.pipeline))
                ok_msgs.append(m)
            except Exception as e:  # 壞訊息：nack 讓它進 DLQ（GCP 上由訂閱的 dead_letter_policy 決定）
                log("bad_message", "ERROR", error=str(e)[:200])
                metrics.MSGS.labels(dic.pipeline, "bad").inc()
                tracker.settled(m.message_id)
                m.nack()
        seg_ms = (time.perf_counter() - t0) * 1000
        metrics.SEG_SEC.labels(dic.pipeline, "stream").observe(seg_ms / 1000)
        if tombs:
            try:
                tr = es.write_tombstones(tombs)
                for k, v in tr.items():
                    metrics.TOMBSTONES.labels(dic.pipeline, k).inc(v)
                for m in tomb_msgs:
                    tracker.settled(m.message_id)
                    m.ack()
                    metrics.MSGS.labels(dic.pipeline, "ack").inc()
                log("tombstones", pipeline=dic.pipeline, **tr)
            except Exception as e:
                log("tombstones_failed", "ERROR", error=str(e)[:300], n=len(tombs))
                for m in tomb_msgs:
                    tracker.settled(m.message_id)
                    m.nack()
                metrics.MSGS.labels(dic.pipeline, "nack").inc(len(tomb_msgs))
        if not items:
            continue
        try:
            res = es.bulk_seg(items)
        except Exception as e:
            log("bulk_failed", "ERROR", error=str(e)[:300], n=len(items))
            for m in ok_msgs:
                tracker.settled(m.message_id)
                m.nack()
            metrics.MSGS.labels(dic.pipeline, "nack").inc(len(ok_msgs))
            continue
        metrics.BULK_SEC.labels(dic.pipeline, "stream").observe(res["took_ms"] / 1000)
        metrics.BULK_RETRIED.labels(dic.pipeline, "stream").inc(res.get("retries", 0))
        for k in ("created", "updated", "noop", "errors"):
            metrics.DOCS.labels(dic.pipeline, "stream", k).inc(res[k])
        now, failed = time.time(), set(res.pop("failed_ids"))
        for (pid, *_), m in zip(items, ok_msgs):
            if pid in failed:          # 這篇沒寫成功：nack，等重送（寫入是冪等的）
                tracker.settled(m.message_id)
                m.nack()
                metrics.MSGS.labels(dic.pipeline, "nack").inc()
                continue
            metrics.LAG.labels(dic.pipeline).observe(now - m.publish_time.timestamp())
            tracker.settled(m.message_id)
            m.ack()
            metrics.MSGS.labels(dic.pipeline, "ack").inc()
        log("seg_batch", pipeline=dic.pipeline, dict_version=dic.version, n=len(items), seg_ms=round(seg_ms, 1), **res)
