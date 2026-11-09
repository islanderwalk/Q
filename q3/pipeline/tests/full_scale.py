"""全量預演：模擬的八卦板放 8 天、每天 890 篇、每篇平均 37 則留言，接 BigQuery 模擬器，照每小時排程的順序跑：

    7 天回補 → 不變量 → 搜尋 → 1 小時後那輪 → 24 小時後重抓 → 不變量 → 搜尋

    docker compose up -d bigquery
    python tests/full_scale.py        # Q3_TEST_BQ_ENDPOINT 可改連別的模擬器

抓取不限速（PTT 換成本機的模擬站），所以量到的是寫入、MERGE、還原紀錄、不變量與搜尋在模擬器上花的時間。
用一個獨立的 dataset，跑完刪掉。
"""
import json
import os
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakeptt import FakeBoard, FakeClock, FakePtt  # noqa: E402
from q3pipe import job, search, verify  # noqa: E402
from q3pipe.bq import Warehouse  # noqa: E402
from q3pipe.config import Config  # noqa: E402
from q3pipe.metrics import Metrics  # noqa: E402
from q3pipe.ptt import Fetcher  # noqa: E402
from q3pipe.rawstore import NullStore  # noqa: E402

BQ = os.environ.get("Q3_TEST_BQ_ENDPOINT", "http://127.0.0.1:9050")


def main():
    clock = FakeClock()
    board = FakeBoard(clock)
    board.seed(days=8, per_day=890, pushes_mean=37, content_len=1000)
    print(f"模擬八卦板：{len(board.posts)} 篇、{sum(len(p['pushes']) for p in board.posts)} 則留言（8 天）", flush=True)
    summary = {}
    with FakePtt(board) as site:
        dataset = "full" + "".join(chr(97 + int(c, 16) % 26) for c in uuid.uuid4().hex[:8])
        cfg = Config(base_url=site.base_url, bq_endpoint=BQ, dataset=dataset, raw_backend="none",
                     min_interval_s=0.0, backoff_base_s=0.0, pause_s=0.0)
        wh = Warehouse(cfg)
        wh.ensure()
        metrics = Metrics(cfg)
        fetcher = Fetcher(cfg, on_request=metrics.on_request, on_backoff=metrics.backoff.set, sleep=lambda s: None)
        ctx = job.Ctx(cfg=cfg, wh=wh, store=NullStore(), fetcher=fetcher, metrics=metrics, now=clock.now)

        def step(name, fn, keep):
            t = time.time()
            r = fn()
            took = round(time.time() - t, 1)
            row = {"seconds": took, **{k: r.get(k) for k in keep}}
            summary[name] = row
            print(f"{name}: {json.dumps(row, ensure_ascii=False, default=str)}", flush=True)
            return r

        run_keys = ("status", "new", "refreshed", "batches", "rolled_back_batches", "list_pages")
        verify_keys = ("ok", "pages_checked", "pages_not_20", "missing_ids", "out_of_order_pairs", "nrec_checked",
                       "nrec_agree_ratio")
        search_keys = ("took_ms", "post_hits", "comment_hits")

        def find():
            return search.do_search(wh, cfg, "颱風", 7, 50, now=clock.now())

        try:
            step("7 天回補", lambda: job.run(ctx), run_keys)
            step("不變量", lambda: verify.run(ctx), verify_keys)
            step("搜尋", find, search_keys)

            clock.advance(hours=1)
            for i in range(30):
                board.add_post(clock.now() - timedelta(minutes=59 - 2 * i), n_pushes=5)
            step("1 小時後", lambda: job.run(ctx), run_keys)

            clock.advance(hours=23)
            for p in board.posts[-3000:]:
                board.add_push(p["aid"])
            step("24 小時後重抓", lambda: job.run(ctx), run_keys)
            step("重抓後不變量", lambda: verify.run(ctx), verify_keys)
            step("重抓後搜尋", find, search_keys)
        finally:
            wh.c.delete_dataset(dataset, delete_contents=True, not_found_ok=True)
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
