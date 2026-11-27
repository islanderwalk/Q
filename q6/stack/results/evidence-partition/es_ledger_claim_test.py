"""回補帳本（ES，qseg/ledger.py）認領的並行測試：真的起 8 個行程同時搶，驗證五件事。

1. 同一分區只會被一個行程領到（exactly-once），每個分區 attempts 只 +1
2. 渠道規則在搶的情況下仍成立：同一渠道沒有 passed 時，任一時刻最多一篇 running
3. 心跳過期（超過 120 秒）的 running 會被重領；心跳新鮮的不會
4. attempts 用盡的（pending 或心跳過期的 running）→ failed，之後不會被自動重領，也會擋住同渠道的其他分區
5. 指定分區（--partition 路徑）不看狀態、不看渠道

只使用測試索引 backfill_ledger_test（腳本開頭檢查名字，結尾刪除）。不碰 backfill_ledger 或 posts。
用法：ES_URL=http://localhost:9200 python es_ledger_claim_test.py
"""
import datetime as dt
import multiprocessing as mp
import os
import pathlib
import sys
import time

import requests

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "seg"))          # stack/seg，好讓 import qseg.ledger

from qseg import ledger as L  # noqa: E402

URL = os.environ.get("ES_URL", "http://localhost:9200").rstrip("/")
INDEX = "backfill_ledger_test"
MAX = 3
N_PROC = 8
TRIALS = 20


def guard():
    assert INDEX == "backfill_ledger_test" and INDEX not in ("backfill_ledger", "posts"), "只能用測試索引"


class CountingLedger(L.Ledger):
    """數 CAS 衝突（409）次數，當作搶奪激烈程度的證據。"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.conflicts = 0

    def cas(self, pid, seq_no, primary_term, fields):
        out = super().cas(pid, seq_no, primary_term, fields)
        if out is None:
            self.conflicts += 1
        return out


def _claim_worker(barrier, q, rounds):
    led = CountingLedger(URL, INDEX)
    barrier.wait()                                   # 所有行程同時起跑
    got = []
    for _ in range(rounds):
        d = led.claim(MAX, f"proc{os.getpid()}")
        if d is None:
            break
        got.append((d["partition_id"], d["attempts"]))
    q.put({"got": got, "conflicts": led.conflicts})


def run_procs(rounds, n=N_PROC):
    ctx = mp.get_context("spawn")
    bar, q = ctx.Barrier(n), ctx.Queue()
    ps = [ctx.Process(target=_claim_worker, args=(bar, q, rounds)) for _ in range(n)]
    for p in ps:
        p.start()
    res = [q.get(timeout=300) for _ in ps]
    for p in ps:
        p.join(60)
    return res


def iso_ago(sec):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=sec)).isoformat(timespec="milliseconds")


def reset(led):
    guard()
    requests.delete(f"{URL}/{INDEX}")                 # 只刪測試索引
    led.ensure_index()


def seed(led, rows):
    base = {"target_seg_ver": 1, "dict_version": "t", "checkpoint_line": 0, "written": 0, "noop": 0, "errors": 0}
    docs = []
    for pid, ch, month, status in rows:
        docs.append({**base, "partition_id": pid, "channel": ch, "month": month, "status": status, "attempts": 0})
    led.create_many(docs)
    led.refresh()


def state(led, pid):
    return led.get(pid)


def main():
    guard()
    sys.stdout.reconfigure(encoding="utf-8")      # Windows 主控台預設 cp950，印中文會炸
    led = L.Ledger(URL, INDEX)
    results = []

    def check(name, ok, detail):
        results.append((name, ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}\n        {detail}")

    print(f"es_ledger_claim_test  url={URL}  index={INDEX}  processes={N_PROC}  max_attempts={MAX}  trials(B)={TRIALS}")
    print(f"started {dt.datetime.now().isoformat(timespec='seconds')}")

    # ---- 1. exactly-once ----
    reset(led)
    rows = []
    for c in range(8):
        rows.append((f"ch{c}/2026-00", f"ch{c}", "2026-00", "passed"))      # 每個渠道先有一篇 passed，渠道規則放行
        for m in ("2026-01", "2026-02", "2026-03"):
            rows.append((f"ch{c}/{m}", f"ch{c}", m, "pending"))
    seed(led, rows)
    res = run_procs(rounds=100)
    got = [g for r in res for g in r["got"]]
    pids = [p for p, _ in got]
    conflicts = sum(r["conflicts"] for r in res)
    per_proc = [len(r["got"]) for r in res]
    ok1 = len(got) == 24 and len(set(pids)) == 24 and all(a == 1 for _, a in got)
    check("1 exactly-once: 24 個 pending 分區被 8 個行程各自循環領取",
          ok1, f"claimed={len(got)} unique={len(set(pids))} attempts_all_1={all(a == 1 for _, a in got)} "
               f"per_process={per_proc} cas_conflicts={conflicts}")

    # ---- 2. channel blocking under race ----
    blocked_ok, multi, zero, per_trial = 0, 0, 0, []
    for t in range(TRIALS):
        reset(led)
        seed(led, [(f"race/2026-{m:02d}", "race", f"2026-{m:02d}", "pending") for m in range(1, 7)])
        res = run_procs(rounds=1)                       # 每行程只领一次，領到就持有不放
        running = led.count({"bool": {"filter": [{"term": {"channel": "race"}}, {"term": {"status": "running"}}]}})
        got_n = sum(len(r["got"]) for r in res)
        conf = sum(r["conflicts"] for r in res)
        per_trial.append((running, got_n, conf))
        if running > 1:
            multi += 1
        elif running == 0:
            zero += 1
        else:
            blocked_ok += 1
    ok2 = multi == 0 and zero == 0
    check(f"2 channel blocking under race: {TRIALS} 次、每次 8 行程同時搶同一渠道（無 passed）",
          ok2, f"trials_with_exactly_1_running={blocked_ok} trials_with_more_than_1_running={multi} "
               f"trials_with_0_running={zero}  (running, claimed, cas_conflicts) per trial = {per_trial}")

    # ---- 3. stale heartbeat reclaim ----
    reset(led)
    seed(led, [("stale/2026-01", "stale", "2026-01", "pending"), ("live/2026-01", "live", "2026-01", "pending")])
    led.set("stale/2026-01", {"status": "running", "worker": "dead-host#0", "attempts": 1,
                              "heartbeat_at": iso_ago(600), "started_at": iso_ago(900)}, refresh=True)
    led.set("live/2026-01", {"status": "running", "worker": "alive-host#0", "attempts": 1,
                             "heartbeat_at": iso_ago(5), "started_at": iso_ago(60)}, refresh=True)
    d1 = led.claim(MAX, "reclaimer")
    d2 = led.claim(MAX, "reclaimer")
    ok3 = (d1 is not None and d1["partition_id"] == "stale/2026-01" and d1["attempts"] == 2 and d1["worker"] == "reclaimer"
           and d2 is None)
    check("3 stale heartbeat: 心跳 600 秒前的 running 被重領（attempts 1→2），5 秒前的不動",
          ok3, f"first={d1 and (d1['partition_id'], d1['attempts'])} second={d2} "
               f"live_status={state(led, 'live/2026-01')['status']}")

    # ---- 4. attempts exhaustion -> failed, blocks channel, never auto-reclaimed ----
    reset(led)
    seed(led, [("ex/2026-01", "ex", "2026-01", "pending"), ("ex/2026-02", "ex", "2026-02", "pending"),
               ("ex/2026-03", "ex", "2026-03", "pending"), ("ex2/2026-01", "ex2", "2026-01", "pending")])
    led.set("ex/2026-01", {"attempts": MAX}, refresh=True)                       # pending 但已用盡
    led.set("ex/2026-02", {"status": "running", "attempts": MAX, "worker": "dead#0",
                           "heartbeat_at": iso_ago(900), "started_at": iso_ago(1000)}, refresh=True)   # 心跳過期且已用盡
    exhausted = led.exhaust(MAX)
    s1, s2 = state(led, "ex/2026-01"), state(led, "ex/2026-02")
    ok4a = (sorted(p for p, _ in exhausted) == ["ex/2026-01", "ex/2026-02"]
            and s1["status"] == "failed" and s2["status"] == "failed"
            and "attempts_exhausted" in (s1.get("gates") or {}) and "attempts_exhausted" in (s2.get("gates") or {}))
    got4 = []
    for _ in range(5):
        d = led.claim(MAX, "after-exhaust")
        if d is None:
            break
        got4.append(d["partition_id"])
    ok4b = "ex/2026-01" not in got4 and "ex/2026-02" not in got4 and state(led, "ex/2026-01")["status"] == "failed"
    ok4c = "ex/2026-03" not in got4 and state(led, "ex/2026-03")["status"] == "pending"   # 同渠道有 failed：擋住
    ok4d = got4 == ["ex2/2026-01"]                                                  # 另一渠道不受影響
    check("4 attempts exhaustion: 用盡 → failed（附 attempts_exhausted）；不自動重領；同渠道被 failed 擋住；他渠道照常",
          ok4a and ok4b and ok4c and ok4d,
          f"exhausted={exhausted} claimed_after={got4} "
          f"statuses={ {p: state(led, p)['status'] for p in ('ex/2026-01', 'ex/2026-02', 'ex/2026-03')} }")

    # ---- 5. --partition path ignores status and channel; heartbeat only for running ----
    d = led.claim(MAX, "manual", partition_id="ex/2026-01")                      # failed 且 ex 渠道被擋
    ok5a = d is not None and d["status"] == "running" and d["attempts"] == MAX + 1 and state(led, "ex/2026-01")["status"] == "running"
    hb_running = led.heartbeat("ex/2026-01")
    hb_failed = led.heartbeat("ex/2026-02")
    ok5b = hb_running is True and hb_failed is False
    check("5 --partition 路徑：忽略 failed 與渠道規則；心跳只更新 running",
          ok5a and ok5b, f"claimed={d and (d['partition_id'], d['status'], d['attempts'])} "
                         f"heartbeat(running)={hb_running} heartbeat(failed)={hb_failed}")

    passed = sum(1 for _, ok in results if ok)
    print(f"\nRESULT {passed}/{len(results)} scenarios passed")

    guard()
    requests.delete(f"{URL}/{INDEX}")
    print(f"deleted test index {INDEX}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
