"""python -m q3pipe <指令>

  loop                 常駐：每小時跑一輪＋每日不變量檢查（docker compose 的 crawler 用這個）
  run                  只跑一輪
  verify               現在跑一次不變量檢查
  serve                搜尋頁與查詢 API
  search 關鍵字         在終端機查
  status               最近幾輪、7 天內統計、上次檢查
  rollback RUN_ID      手動把某一輪寫進去的資料還原（只允許最近一輪；--force 可還原更早的）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from . import job, scheduler, search, verify
from .bq import Warehouse
from .config import Config


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="q3pipe")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("loop")
    sub.add_parser("run")
    sub.add_parser("verify")
    sub.add_parser("serve")
    s = sub.add_parser("search")
    s.add_argument("keyword")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--limit", type=int, default=20)
    sub.add_parser("status")
    r = sub.add_parser("rollback")
    r.add_argument("run_id")
    r.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    cmd = a.cmd or "loop"

    if cmd == "serve":
        return search.serve()
    if cmd in ("loop", "run", "verify"):
        # 手動的 run／verify 不推指標：Pushgateway 同一組指標會被整組覆蓋，和常駐程序的計數器互蓋會讓看板跳針
        ctx = scheduler.build_ctx(None if cmd == "loop" else Config.from_env(pushgateway=""))
        if cmd == "loop":
            return scheduler.loop(ctx)
        if cmd == "run":
            with scheduler.single_worker():
                res = job.run(ctx)
            ctx.metrics.stop()
            return 0 if res["status"] in ("ok", "partial") else 1
        res = verify.run(ctx)
        return 0 if res["ok"] else 1

    cfg = Config.from_env()
    wh = Warehouse(cfg)
    if cmd == "search":
        res = search.do_search(wh, cfg, a.keyword, min(max(a.days, 1), cfg.window_days), a.limit)
        for p in res["results"]:
            print(f"{p['published_at'][:16]}  {p['title']}  [{'/'.join(p['matched_in'])}]  {p['url']}")
            for c in p["matched_comments"]:
                print(f"      {c['tag']} {c['user_id']}: {c['content']}")
        print(f"-- {len(res['results'])} 篇，{res['took_ms']} ms", file=sys.stderr)
        return 0
    if cmd == "status":
        now = datetime.now(timezone.utc)
        runs = wh.q(f"SELECT * FROM {cfg.table('crawl_runs')} WHERE board = @b ORDER BY started_at DESC LIMIT 5", b=cfg.board)
        ver = wh.q(f"SELECT * FROM {cfg.table('verify_runs')} WHERE board = @b ORDER BY run_at DESC LIMIT 1", b=cfg.board)
        _print({"window": wh.window_stats(cfg.board, now, cfg.window_days),
                "runs": [dict(x.items()) for x in runs], "last_verify": [dict(x.items()) for x in ver]})
        return 0
    if cmd == "rollback":
        latest = wh.q(f"SELECT run_id FROM {cfg.table('crawl_runs')} WHERE board = @b ORDER BY started_at DESC LIMIT 1",
                      b=cfg.board)
        if latest and latest[0].run_id != a.run_id and not a.force:
            print("只能還原最近一輪；更早的輪次之後可能被重抓過，要還原請加 --force（之後的重抓會在下一輪補回）",
                  file=sys.stderr)
            return 2
        with scheduler.single_worker():
            undone = wh.rollback_run(a.run_id)
        wh.insert_rows("crawl_runs", [{"run_id": f"manual-rollback-{a.run_id[:8]}", "board": cfg.board,
                                       "started_at": datetime.now(timezone.utc), "finished_at": datetime.now(timezone.utc),
                                       "status": "rolled_back", "batches": len(undone),
                                       "note": f"手動還原 {a.run_id}：{len(undone)} 批"}])
        job.log("manual_rollback", run_id=a.run_id, batches=undone)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
