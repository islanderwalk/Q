"""定向重跑：字典只「加詞」時，只重算原文含新詞字串的文章，不必全量回補。

成立的前提是 jieba 的 total 固定（manifest.total_pinned）：加詞不會改到不含該詞的文章（impact.py 用全量資料驗證）。
找文章用 ES 的 content.bigram 片語比對（中文兩字一組，等同「原文含這個字串」），只挑 seg_ver_v2 還比目標舊的，
重算後走同一支 seg_guard 條件更新：版本只前進，重跑中斷再跑也不會重複或倒退。
"""
import argparse
import json
import time

import psycopg

from . import config, dictionary
from .es import ES
from .logs import log
from .pipeline import seg_fields, seg_ver, segment


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", required=True, help="逗號分隔：這一版新增或改詞頻的詞")
    ap.add_argument("--batch", type=int, default=500)
    a = ap.parse_args(argv)
    words = [w.strip() for w in a.words.split(",") if w.strip()]
    dic = dictionary.load()
    es = ES()
    es.put_guard_script()
    target = seg_ver("v2", dic.version)
    q = {"bool": {"should": [{"match_phrase": {"content.bigram": w}} for w in words], "minimum_should_match": 1,
                  "filter": [{"range": {"seg_ver_v2": {"lt": target}}}]}}
    estimate = es.count(q)
    log("rerun_start", dict_version=dic.version, words=words, candidates=estimate, seg_build=config.SEG_BUILD)
    pit = es.req("POST", f"/{es.index}/_pit?keep_alive=2m")["id"]
    after, scanned, substring, changed = None, 0, 0, 0
    res = {"created": 0, "updated": 0, "noop": 0, "errors": 0, "rejected": 0}
    examples = []
    t0 = time.perf_counter()
    while True:
        body = {"size": a.batch, "query": q, "pit": {"id": pit, "keep_alive": "2m"}, "sort": [{"_shard_doc": "asc"}],
                "_source": ["channel", "content", "terms_v2"]}
        if after:
            body["search_after"] = after
        hits = es.req("POST", "/_search", body)["hits"]["hits"]
        if not hits:
            break
        items = []
        for h in hits:
            s = h["_source"]
            scanned += 1
            substring += any(w in s["content"] for w in words)
            terms = segment(dic, s["channel"], s["content"])
            if terms != s.get("terms_v2"):
                changed += 1
                if len(examples) < 10:
                    old = set(s.get("terms_v2") or [])
                    examples.append({"id": h["_id"], "removed": sorted(old - set(terms)), "added": sorted(set(terms) - old)})
            items.append((h["_id"], {}, seg_fields(dic, terms), "v2"))
        r = es.bulk_seg(items)
        for k in res:
            res[k] += r[k]
        after = hits[-1]["sort"]
    es.req("DELETE", "/_pit", {"id": pit})
    secs = round(time.perf_counter() - t0, 2)
    total = es.count({"match_all": {}})
    out = {"dict_version": dic.version, "words": words, "candidates": estimate, "scanned": scanned,
           "substring_hits": substring, "terms_changed": changed, "seconds": secs, "corpus": total,
           "share_of_corpus": round(scanned / total, 5) if total else None, "examples": examples, **res}
    log("rerun_done", **out)
    with psycopg.connect(config.PG_DSN, autocommit=True) as c:
        c.execute("""INSERT INTO dict_releases (dict_version, seg_ver, added_words, affected_estimate, rerun_scope)
                     VALUES (%s,%s,%s,%s,%s) ON CONFLICT (dict_version) DO UPDATE
                     SET affected_estimate=EXCLUDED.affected_estimate, rerun_scope=EXCLUDED.rerun_scope""",
                  (dic.version, target, words, estimate, f"targeted: {scanned} docs containing {','.join(words)}; {changed} changed terms"))
    print(json.dumps(out, ensure_ascii=False))
    return 0 if res["errors"] == 0 else 1
