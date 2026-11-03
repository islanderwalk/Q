"""每日影子比對：從 ES 隨機抽樣已是目前版本的文章，用目前映像＋字典重算，與 ES 裡的 terms_v2 比。
批次與串流跑同一映像、同一字典版本，理論上差異必須是 0；不是 0 代表有某條路徑用了別的版本或別的程式。"""
import argparse
import json

import psycopg

from . import config, dictionary
from .es import ES
from .logs import log
from .pipeline import seg_ver, segment


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20261030)
    a = ap.parse_args(argv)
    dic = dictionary.load()
    es = ES()
    target = seg_ver("v2", dic.version)
    body = {"size": a.sample, "_source": ["channel", "content", "terms_v2", "seg_build_v2"],
            "query": {"function_score": {"query": {"term": {"seg_ver_v2": target}},
                                         "random_score": {"seed": a.seed, "field": "_seq_no"}}}}
    hits = es.req("POST", f"/{es.index}/_search", body)["hits"]["hits"]
    bad = []
    by_build = {}
    for h in hits:
        s = h["_source"]
        by_build[s.get("seg_build_v2")] = by_build.get(s.get("seg_build_v2"), 0) + 1
        again = segment(dic, s["channel"], s["content"])
        if again != s.get("terms_v2"):
            bad.append({"id": h["_id"], "es": s.get("terms_v2")[:20], "recomputed": again[:20]})
    log("shadow_check", dict_version=dic.version, sampled=len(hits), mismatched=len(bad), builds=by_build)
    try:
        with psycopg.connect(config.PG_DSN, autocommit=True) as c:
            c.execute("INSERT INTO shadow_checks (dict_version, sampled, mismatched, examples) VALUES (%s,%s,%s,%s)",
                      (dic.version, len(hits), len(bad), json.dumps(bad[:5], ensure_ascii=False)))
    except Exception as e:
        log("shadow_pg_skip", "WARNING", error=str(e)[:200])
    return 1 if bad else 0
