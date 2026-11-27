"""把臨時資料灌進 WSL 裡的 Postgres（pg_bigm）與 Elasticsearch，跑兩種查詢，結果存成 JSON。

前置：WSL 內已啟動 q2demo-pg（55433）與 q2demo-es（19310）。
用法：python q2/evidence/relevance_demo/run_demo.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from posts import KEYWORD, build  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ES = "http://127.0.0.1:19310"
POSTS = build()


def psql(sql: str, inp: str | None = None) -> str:
    cmd = ["wsl", "-u", "root", "--", "docker", "exec", "-i", "q2demo-pg",
           "psql", "-U", "postgres", "-X", "-q", "-A", "-t", "-F", "\t", "-c", sql]
    return subprocess.run(cmd, input=inp, capture_output=True, text=True, encoding="utf-8", check=True).stdout


def es(method: str, path: str, body=None):
    req = urllib.request.Request(ES + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def wait_ready():
    for _ in range(90):
        try:
            es("GET", "/_cluster/health")
            psql("select 1")
            return
        except Exception:
            time.sleep(2)
    raise SystemExit("服務沒起來")


def main():
    wait_ready()
    # ---- Postgres：pg_bigm ----
    psql("drop table if exists fb_posts; create extension if not exists pg_bigm;")
    psql("create table fb_posts (id int primary key, content text not null);")
    copy = "".join(f"{i}\t{p}\n" for i, p in enumerate(POSTS, 1))
    subprocess.run(["wsl", "-u", "root", "--", "docker", "exec", "-i", "q2demo-pg", "psql", "-U", "postgres", "-X", "-q",
                    "-c", "copy fb_posts from stdin"], input=copy, text=True, encoding="utf-8", check=True)
    psql("create index fb_posts_bigm on fb_posts using gin (content gin_bigm_ops); analyze fb_posts;")
    like_sql = f"SELECT id, content FROM fb_posts WHERE content LIKE '%{KEYWORD}%';"
    rows = [r.split("\t") for r in psql(like_sql).strip().splitlines()]
    plan = psql(f"EXPLAIN SELECT id FROM fb_posts WHERE content LIKE '%{KEYWORD}%';")

    # ---- Elasticsearch：2 字一切（對應 pg_bigm），BM25 排序 ----
    try:
        es("DELETE", "/fb_posts")
    except Exception:
        pass
    es("PUT", "/fb_posts", {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0,
                     "analysis": {"tokenizer": {"bi": {"type": "ngram", "min_gram": 2, "max_gram": 2,
                                                       "token_chars": ["letter", "digit"]}},
                                  "analyzer": {"bigram": {"type": "custom", "tokenizer": "bi"}}}},
        "mappings": {"properties": {"content": {"type": "text", "analyzer": "bigram"}}}})
    bulk = "".join(json.dumps({"index": {"_index": "fb_posts", "_id": i}}) + "\n" +
                   json.dumps({"content": p}, ensure_ascii=False) + "\n" for i, p in enumerate(POSTS, 1))
    req = urllib.request.Request(ES + "/_bulk?refresh=true", method="POST", data=bulk.encode(),
                                 headers={"Content-Type": "application/x-ndjson"})
    urllib.request.urlopen(req).read()
    es_body = {"size": 10, "_source": ["content"],
               "query": {"match_phrase": {"content": KEYWORD}},
               "highlight": {"fields": {"content": {"number_of_fragments": 0}},
                             "pre_tags": ["<em>"], "post_tags": ["</em>"]}}
    res = es("POST", "/fb_posts/_search", es_body)

    out = {
        "keyword": KEYWORD, "total_posts": len(POSTS),
        "pg": {"sql": like_sql, "rows": rows, "plan": plan.strip().splitlines()},
        "es": {"request": es_body,
               "took": res["took"], "total": res["hits"]["total"]["value"],
               "hits": [{"id": h["_id"], "score": h["_score"], "content": h["_source"]["content"],
                         "hl": h["highlight"]["content"][0]} for h in res["hits"]["hits"]]},
    }
    with open(os.path.join(HERE, "results.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("PG 命中", len(rows), "筆；ES 命中", out["es"]["total"], "筆")
    for r in rows:
        print("PG ", r[0], r[1][:24])
    for h in out["es"]["hits"]:
        print("ES ", h["id"], round(h["score"], 3), h["content"][:24])


if __name__ == "__main__":
    main()
