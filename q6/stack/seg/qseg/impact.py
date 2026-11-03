"""加詞影響範圍的地面真值：用全量原文比較「舊字典」與「新字典」斷詞結果，看哪些文章變了。

同時跑兩種新字典：固定 total（manifest.total_pinned）與不固定（jieba 預設，加詞後 total 會變），
回答「新增詞只重跑原文含該字串的文章」這句話在真實資料上是否成立。
用法：python -m qseg impact <舊版> <新版> <詞,詞> [processes] [--record]
--record：把「不含新詞卻變了的文章數（固定 total）」寫進 dict_releases.bigram_audit_missed（P0-5 的漏抓數）
"""
import json
import multiprocessing as mp
import sys
from pathlib import Path

from . import config, dictionary
from .normalize import normalize
from .pipeline import segment

_D = {}


def _init(old, new):
    _D["old"] = dictionary.load(old, "v2")
    _D["pinned"] = dictionary.load(new, "v2")
    _D["unpinned"] = dictionary.load(new, "v2", pin_total=False)


def _work(path):
    words = _D["words"]
    n = changed_p = changed_u = 0
    outside_p, outside_u, contains = [], [], 0
    raw_p = raw_u = 0  # 原始斷詞序列（未去重、未過濾）在不含新詞的文章上有沒有變
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        post = json.loads(line)
        text = post.get("text", "")
        n += 1
        has = any(w in text for w in words)
        contains += has
        a = segment(_D["old"], post["channel"], text)
        if segment(_D["pinned"], post["channel"], text) != a:
            changed_p += 1
            if not has:
                outside_p.append(post["post_id"])
        if segment(_D["unpinned"], post["channel"], text) != a:
            changed_u += 1
            if not has:
                outside_u.append(post["post_id"])
        if not has:
            clean = normalize(post["channel"], text)
            r0 = _D["old"].tokenizer.lcut(clean, HMM=True)
            raw_p += _D["pinned"].tokenizer.lcut(clean, HMM=True) != r0
            raw_u += _D["unpinned"].tokenizer.lcut(clean, HMM=True) != r0
    return n, contains, changed_p, changed_u, outside_p, outside_u, raw_p, raw_u


def record_missed(dsn, dict_version, missed) -> int:
    """把漏抓數寫進 dict_releases；回傳受影響列數（0 ＝ 版本不存在，呼叫端要視為失敗）。"""
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as c:
        cur = c.execute("UPDATE dict_releases SET bigram_audit_missed=%s WHERE dict_version=%s", (missed, dict_version))
        return cur.rowcount


def main(argv):
    record = "--record" in argv
    argv = [a for a in argv if a != "--record"]
    old, new, words = argv[0], argv[1], [w for w in argv[2].split(",") if w]
    procs = int(argv[3]) if len(argv) > 3 else 3
    files = sorted(str(p) for p in Path(config.RAW_ROOT).glob("*/*.jsonl"))
    _D["words"] = words
    _init(old, new)
    _D["tp"], _D["tu"] = _D["pinned"].tokenizer.total, _D["unpinned"].tokenizer.total
    with mp.get_context("fork").Pool(procs, initializer=_init, initargs=(old, new)) as pool:
        rows = pool.map(_work, files)
    out = {"old": old, "new": new, "words": words,
           "docs": sum(r[0] for r in rows), "contain_word": sum(r[1] for r in rows),
           "changed_pinned_total": sum(r[2] for r in rows), "changed_unpinned_total": sum(r[3] for r in rows),
           "changed_without_word_pinned": sum(len(r[4]) for r in rows),
           "changed_without_word_unpinned": sum(len(r[5]) for r in rows),
           "examples_without_word_unpinned": [x for r in rows for x in r[5]][:10],
           "raw_tokens_changed_without_word_pinned": sum(r[6] for r in rows),
           "raw_tokens_changed_without_word_unpinned": sum(r[7] for r in rows),
           "total_pinned": _D.get("tp"), "total_unpinned": _D.get("tu")}
    print(json.dumps(out, ensure_ascii=False))
    if record:
        n = record_missed(config.PG_DSN, new, out["changed_without_word_pinned"])
        if n != 1:
            print(f"bigram_audit_missed 寫入失敗：dict_releases 找到 {n} 列（dict_version={new}），應為 1", file=sys.stderr)
            return 1
    return 0
