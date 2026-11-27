#!/usr/bin/env python3
"""金絲雀帳號每小時驗證：對 ES 走一遍（帳號與時間是示範資料）。
每個金絲雀帳號每小時發一篇測試貼文；ES 寫入時算好 lag_min（寫入時間 − published_at）存在文件裡。
每小時檢查一次：這一小時內，每個金絲雀帳號都要有貼文，且 lag_min ≤ 60，否則警告。
用法：python canary_demo.py <ES 位址> [setup|check]（預設兩個都跑）"""
import json
import sys

import requests

ES = sys.argv[1].rstrip("/")
STAGE = sys.argv[2] if len(sys.argv) > 2 else "all"
IDX = "q5live-canary"

CHECK = '''ACCOUNTS = ["canary_ig_a", "canary_th_a", "canary_th_b"]
r = requests.post(f"{ES}/{IDX}/_search", json={
    "size": 0,
    "query": {"range": {"published_at": {"gte": "2026-11-16T10:00:00+08:00",
                                         "lt":  "2026-11-16T11:00:00+08:00"}}},
    "aggs": {"by_account": {"terms": {"field": "account", "size": 10},
                            "aggs": {"lag": {"max": {"field": "lag_min"}}}}}}).json()
seen = {b["key"]: b["lag"]["value"] for b in r["aggregations"]["by_account"]["buckets"]}
lag = {a: seen.get(a) for a in ACCOUNTS}                       # 沒出現＝None
bad = [a for a, v in lag.items() if v is None or v > 60]       # 缺貼文或超過 60 分
'''

DOCS = [  # 示範資料
    ("canary_ig_a", "DEMO_ig_a_1000", "2026-11-16T10:00:00+08:00", "2026-11-16T10:23:00+08:00", 23),
    ("canary_th_a", "DEMO_th_a_1000", "2026-11-16T10:00:00+08:00", "2026-11-16T10:41:00+08:00", 41),
]  # canary_th_b 這一小時的貼文一直沒進 ES


def setup():
    requests.delete(f"{ES}/{IDX}")
    r = requests.put(f"{ES}/{IDX}", json={
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {"dynamic": "strict", "properties": {
            "account": {"type": "keyword"}, "post_id": {"type": "keyword"},
            "published_at": {"type": "date"}, "indexed_at": {"type": "date"},
            "lag_min": {"type": "integer"}}}})
    assert r.ok, r.text
    for acc, pid, pub, idx, lag in DOCS:
        r = requests.put(f"{ES}/{IDX}/_doc/{pid}?refresh=true", json={
            "account": acc, "post_id": pid, "published_at": pub, "indexed_at": idx, "lag_min": lag})
        assert r.ok, r.text
    d = requests.post("http://127.0.0.1:15605/api/data_views/data_view",
                      headers={"kbn-xsrf": "1"}, json={"override": True, "data_view": {
                          "id": "q5canary", "title": IDX, "name": IDX, "timeFieldName": "published_at"}})
    print("setup", "ok", "dataview", d.status_code)


def check():
    ns = {"requests": requests, "ES": ES, "IDX": IDX}
    exec(CHECK, ns)
    print("lag", json.dumps(ns["lag"]))
    print("bad", ns["bad"])
    print("ALERT" if ns["bad"] else "OK")


if __name__ == "__main__":
    if STAGE in ("all", "setup"):
        setup()
    if STAGE in ("all", "check"):
        check()
