"""串流實測：把六個渠道的真實樣本當成 parser 的輸出，送進 Pub/Sub（官方模擬器），讓 seg-v1／seg-v2 兩個服務各自斷詞寫 ES。

同時模擬 parser 自己也在寫 ES（只寫文章本體欄位）：一半先發訊息再寫本體、一半先寫本體再發訊息，
驗證兩條路徑誰先到都不會互相蓋掉。另外：整批重送一次（冪等：應該全是 noop）、送一則壞訊息（應該進 DLQ）。
用法：PUBSUB_EMULATOR_HOST=localhost:8085 python publish_channels.py <data/channels> <輸出.json>
"""
import json
import sys
import time
import urllib.parse
from pathlib import Path

import requests
from google.cloud import pubsub_v1

ES = "http://localhost:9200"
PROJECT, TOPIC = "qsearch-local", "parsed-posts"
CHANNELS = ["facebook", "instagram", "youtube", "ptt", "media", "threads"]
BASE_KEYS = ("post_id", "channel", "sub_type", "source", "url", "published_at", "fetched_at")


def base_doc(post):
    doc = {k: post.get(k) for k in BASE_KEYS}
    doc["content"] = post.get("text") or ""
    doc["month"] = (post.get("published_at") or "")[:7] or "unknown"
    return doc


def parser_write(post):
    pid = urllib.parse.quote(post["post_id"], safe="")  # PTT 的 id 有斜線（看板/M.xxx）
    r = requests.post(f"{ES}/posts/_update/{pid}", json={"doc": base_doc(post), "doc_as_upsert": True}, timeout=30)
    r.raise_for_status()


def main():
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    pub = pubsub_v1.PublisherClient()
    topic = pub.topic_path(PROJECT, TOPIC)
    posts = []
    for ch in CHANNELS:
        posts += [json.loads(l) for l in (root / f"{ch}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    ids = [p["post_id"] for p in posts]
    t0 = time.time()
    futures = []
    for i, p in enumerate(posts):
        data = json.dumps(p, ensure_ascii=False).encode("utf-8")
        if i % 2 == 0:
            futures.append(pub.publish(topic, data, channel=p["channel"], post_id=p["post_id"]))
            parser_write(p)
        else:
            parser_write(p)
            futures.append(pub.publish(topic, data, channel=p["channel"], post_id=p["post_id"]))
    for f in futures:
        f.result()
    t_pub = time.time()

    def state():
        q = {"size": len(ids), "query": {"ids": {"values": ids}},
             "_source": ["channel", "content", "seg_ver_v1", "seg_ver_v2", "terms_v2", "dict_version_v2"]}
        return requests.post(f"{ES}/posts/_search?request_cache=false", json=q, timeout=30).json()["hits"]["hits"]

    while True:
        requests.post(f"{ES}/posts/_refresh")
        hits = state()
        done = [h for h in hits if h["_source"].get("seg_ver_v1") and h["_source"].get("seg_ver_v2") and h["_source"].get("content")]
        if len(done) == len(ids) or time.time() - t_pub > 120:
            break
        time.sleep(0.5)
    t_done = time.time()
    by_ch = {}
    for h in hits:
        s = h["_source"]
        c = by_ch.setdefault(s["channel"], {"docs": 0, "with_content": 0, "with_v1": 0, "with_v2": 0})
        c["docs"] += 1
        c["with_content"] += bool(s.get("content"))
        c["with_v1"] += bool(s.get("seg_ver_v1"))
        c["with_v2"] += bool(s.get("seg_ver_v2"))

    # 重送整批：至少一次投遞的重複訊息，應該全部 noop
    for p in posts:
        pub.publish(topic, json.dumps(p, ensure_ascii=False).encode("utf-8"), channel=p["channel"], post_id=p["post_id"], redelivery="1").result()
    # 壞訊息：不是 JSON，服務 nack，超過 5 次進 DLQ
    pub.publish(topic, b"{not json", channel="ptt", post_id="bad-1").result()
    res = {"posts": len(ids), "published_seconds": round(t_pub - t0, 2), "all_segmented_seconds_after_publish": round(t_done - t_pub, 2),
           "complete": len(done) == len(ids), "by_channel": by_ch,
           "dict_versions_v2": sorted({h["_source"].get("dict_version_v2") for h in hits if h["_source"].get("dict_version_v2")})}
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
