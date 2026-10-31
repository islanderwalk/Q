"""把原文湖灌進 ES，並用舊流程（v1）寫 terms_v1：重現「上線前的現況」——5 億篇只有舊詞表。
只在建測試環境時跑；正式環境的舊資料本來就在 ES 裡。多個 task 依 post_id 雜湊分工。"""
import json
import time
import zlib
from pathlib import Path

from . import config, dictionary
from .es import ES
from .logs import log
from .pipeline import base_doc, seg_fields, segment


def main(argv=None):
    dic = dictionary.load()
    assert dic.pipeline == "v1", "seed 只寫舊流程 v1"
    es = ES()
    es.put_guard_script()
    files = sorted(Path(config.RAW_ROOT).glob("*/*.jsonl"))
    t0, n = time.perf_counter(), 0
    for f in files:
        items = []
        for line in f.read_text(encoding="utf-8").splitlines():
            post = json.loads(line)
            if zlib.crc32(post["post_id"].encode()) % config.TASK_COUNT != config.TASK_INDEX:
                continue
            items.append((post["post_id"], base_doc(post), seg_fields(dic, segment(dic, post["channel"], post.get("text", ""))), "v1"))
            if len(items) >= 1000:
                es.bulk_seg(items)
                n += len(items)
                items = []
        if items:
            es.bulk_seg(items)
            n += len(items)
        log("seed_file", file=f.name, done=n, docs_per_sec=round(n / (time.perf_counter() - t0), 1))
    log("seed_done", docs=n, seconds=round(time.perf_counter() - t0, 1), task=config.TASK_INDEX)
