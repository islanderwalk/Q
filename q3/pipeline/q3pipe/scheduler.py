"""排程器（答案：Cloud Scheduler 每小時第 5 分觸發 Cloud Run Job）。本機用常駐程序模擬同一個排程。

- 一啟動先跑一輪，之後每小時第 5 分跑；一輪跑超過一小時（第一次補 7 天）就跑完立刻接下一輪，不會重疊。
- 每日不變量檢查：台灣時間 04 點後的第一輪結束後跑；從沒跑過的話，第一輪結束就先跑一次。
- 收到 SIGTERM／SIGINT：當前這篇抓完、這批寫完就停，沒抓的下一輪補。
- 單一 worker：每一輪（含手動的 run、rollback）都先拿同一把檔案鎖，另一輪還在跑就等它跑完，
  對應答案 Cloud Run Job 的 parallelism 1。
"""
from __future__ import annotations

import os
import signal
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import job, verify
from .bq import Warehouse
from .config import Config
from .metrics import Metrics
from .ptt import TW, Fetcher
from .rawstore import make_store


def _retry(fn, what, tries=30, wait=2.0):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # 模擬器或 GCS 剛啟動還沒好
            if i == tries - 1:
                raise
            job.log("waiting_dependency", what=what, error=f"{type(e).__name__}: {e}"[:300], attempt=i + 1)
            time.sleep(wait)


@contextmanager
def single_worker(path=None):
    """同一時間只有一輪在寫：拿不到鎖就等。沒有 fcntl 的平台（Windows）不鎖。"""
    try:
        import fcntl
    except ImportError:
        yield
        return
    path = path or os.environ.get("Q3_LOCK_FILE", "/tmp/q3pipe-run.lock")
    with open(path, "w") as fd:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            job.log("waiting_for_other_run", lock=path)
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def drill_batches(cfg):
    """故障演練要讓哪幾批出錯："1,3" → {1, 3}。"""
    return {int(x) for x in cfg.drill_fail_batches.replace(" ", "").split(",") if x}


def build_ctx(cfg=None):
    cfg = cfg or Config.from_env()
    metrics = Metrics(cfg)

    def make_wh():
        wh = Warehouse(cfg)
        wh.ensure()
        purged = wh.purge_jobs()
        if purged:
            job.log("emulator_jobs_purged", jobs=purged)
        return wh

    wh = _retry(make_wh, "bigquery")
    store = _retry(lambda: make_store(cfg), "raw_store")
    fetcher = Fetcher(cfg, on_request=metrics.on_request, on_backoff=metrics.backoff.set, log=job.log)
    ctx = job.Ctx(cfg=cfg, wh=wh, store=store, fetcher=fetcher, metrics=metrics)
    try:
        job.restore_gauges(ctx)
    except Exception as e:  # 只影響看板，不影響抓取
        job.log("restore_gauges_failed", error=repr(e)[:200])
    drill = drill_batches(cfg)
    if drill:
        ctx.inject.update(fail_after=cfg.drill_fail_at, fail_batches=drill)
        job.log("drill_enabled", fail_batches=sorted(drill), fail_at=cfg.drill_fail_at)
    if cfg.drill_regression:
        ctx.inject["regression"] = True
        job.log("drill_enabled", regression=True)
    return ctx


def next_run_at(now, cfg):
    if cfg.schedule_minute < 0:
        return now + timedelta(seconds=cfg.interval_s)
    nxt = now.replace(minute=cfg.schedule_minute, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(hours=1)
    return nxt


def verify_due(cfg, last, now):
    if cfg.verify_every_run or last is None:
        return True
    tw_now, tw_last = now.astimezone(TW), last.astimezone(TW)
    return tw_now.hour >= cfg.verify_hour_tw and tw_last.date() < tw_now.date()


def loop(ctx, max_runs=None):
    cfg = ctx.cfg

    def stop(signum, _frame):
        job.log("stopping", signal=signum)
        ctx.stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    ctx.metrics.start_pusher()
    runs = 0
    while not ctx.stopping.is_set():
        with single_worker():
            res = job.run(ctx)
            runs += 1
            try:
                if res["status"] in ("ok", "partial") and verify_due(cfg, ctx.wh.last_verify_at(cfg.board), ctx.now()):
                    verify.run(ctx)
            except Exception as e:
                job.log("verify_failed", error=repr(e)[:200])
        if max_runs and runs >= max_runs:
            break
        nxt = next_run_at(datetime.now(timezone.utc), cfg)
        job.log("sleep_until", next_run=nxt.isoformat(timespec="seconds"))
        ctx.stopping.wait(max(0.0, (nxt - datetime.now(timezone.utc)).total_seconds()))
    ctx.metrics.stop()
