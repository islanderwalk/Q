"""IK 遠端字典熱更新 ＋ 搜尋端同義詞即時生效：真的切一次字典 current，量多久生效、舊文件會怎樣。

1. 切換前：_analyze 看新詞怎麼切；用 match_phrase 搜 content（IK 索引）與 content.bigram（雙字，當地面真值）
2. 把字典伺服器的 current 從舊版原子切到新版（GCS 上就是改 current 物件）
3. 每 2 秒 _analyze 一次，直到 IK 吐出新詞（IK 每 60 秒對遠端字典發 HEAD，看 Last-Modified／ETag）
4. 切換後再搜：查詢端已用新字典、舊文件還是舊切法 → 片語搜尋會漏
5. 只對含新詞字串的文件 _update_by_query（重新分析），再搜一次
6. 同義詞：PUT _synonyms 加一條規則，不重建索引、不重開節點，下一個查詢就生效
用法：python ik_hot_reload.py <runtime/dictsrv> <舊版> <新版> <新詞> <輸出.json>
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

ES = "http://localhost:9200"
S = requests.Session()


def es(method, path, body=None):
    r = S.request(method, ES + path, json=body, timeout=300)
    r.raise_for_status()
    return r.json()


def tokens(analyzer, text):
    return [t["token"] for t in es("POST", "/_analyze", {"analyzer": analyzer, "text": text})["tokens"]]


def count(q):
    # 一定要關 request cache：size=0 的請求會被 shard request cache 記住，沒有 refresh 就一直回舊數字
    # （第一次跑就踩到：切字典後片語搜尋看起來「沒漏」，其實是快取）
    return es("POST", "/posts/_search?request_cache=false", {"size": 0, "track_total_hits": True, "query": q})["hits"]["total"]["value"]


def switch(root: Path, version: str):
    tmp = root / ".current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(Path("versions") / version)
    os.replace(tmp, root / "current")  # rename 是原子的：nginx 不會讀到一半


def main():
    root, old, new, word, out = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
    text = f"{word}在新北投好市多排隊"
    phrase = {"match_phrase": {"content": word}}
    truth = {"match_phrase": {"content.bigram": word}}
    res = {"word": word, "from": old, "to": new, "sample_text": text}
    es("POST", "/posts/_refresh")
    res["before"] = {"ik_smart": tokens("ik_smart", text), "ik_max_word": tokens("ik_max_word", text),
                     "phrase_hits_ik": count(phrase), "docs_containing_word_bigram": count(truth)}
    switch(root, new)
    t0 = time.time()
    res["switched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0))
    polls = 0
    while word not in tokens("ik_smart", text):
        polls += 1
        if time.time() - t0 > 180:
            res["reload_timeout"] = True
            break
        time.sleep(2)
    res["reload_seconds"] = round(time.time() - t0, 1)
    res["polls"] = polls
    res["after_reload"] = {"ik_smart": tokens("ik_smart", text), "ik_max_word": tokens("ik_max_word", text),
                           "phrase_hits_ik": count(phrase), "docs_containing_word_bigram": count(truth)}
    t1 = time.time()
    ubq = es("POST", "/posts/_update_by_query?refresh=true&conflicts=proceed", {"query": truth})
    res["update_by_query"] = {"updated": ubq["updated"], "took_ms": ubq["took"], "failures": len(ubq["failures"]),
                              "wall_seconds": round(time.time() - t1, 2)}
    res["after_reindex"] = {"phrase_hits_ik": count(phrase), "docs_containing_word_bigram": count(truth)}

    # 同義詞：搜尋端用 synonyms set（updateable），改規則不用重建索引
    syn = es("GET", "/_synonyms/qsearch-search")
    rules = [{"id": r["id"], "synonyms": r["synonyms"]} for r in syn["synonyms_set"]]
    alias = "阿宅"
    q_alias = {"match_phrase": {"content": alias}}
    before = count(q_alias)
    es("PUT", "/_synonyms/qsearch-search", {"synonyms_set": rules + [{"id": "q6-fatnerd", "synonyms": f"{word}, {alias}"}]})
    t2 = time.time()
    res["synonyms"] = {"rule": f"{word}, {alias}", "hits_for_alias_before": before,
                       "hits_for_alias_after": count(q_alias), "seconds_after_put": round(time.time() - t2, 2),
                       "analyze_search": [t["token"] for t in es("POST", "/posts/_analyze", {"analyzer": "ik_search", "text": alias})["tokens"]]}
    Path(out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
