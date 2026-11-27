"""回補 Job 的計畫書修正：G1 比不重複 post_id、G2 比例門檻、認領 attempts < 3、心跳背景執行緒、G3 限速回升。
只測純函式與假帳本（FakeLedger）；不連 ES／PG。"""
import json
import sys
import threading

import pytest

from _p0_fakes import SEG, FakeLedger, ensure_prometheus_client

ensure_prometheus_client()
if str(SEG) not in sys.path:
    sys.path.insert(0, str(SEG))
job = pytest.importorskip("qseg.job")
ledger = pytest.importorskip("qseg.ledger")


def test_load_partition_lines_dedupes_post_id_first_wins(tmp_path):
    p = tmp_path / "2026-10.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in [{"post_id": "a", "text": "1"}, {"post_id": "b", "text": "2"}, {"post_id": "a", "text": "3"}, {"post_id": "c", "text": "4"}]) + "\n\n",
                 encoding="utf-8")
    lines, raw_lines = job.load_partition_lines(p)
    assert raw_lines == 4 and len(lines) == 3
    assert [json.loads(l)["post_id"] for l in lines] == ["a", "b", "c"] and json.loads(lines[0])["text"] == "1"


def test_g2_ratio_threshold_replaces_zero_tolerance():
    ratios = job.g2_ratios({"phone_like": 5, "pure_number": 0, "url_fragment": 2, "hard_stopwords": 0}, {"颱風假": 3}, raw_count=10000)
    assert ratios["phone_like"] == 0.0005 and ratios["must_keep_split"] == 0.0003
    assert job.g2_pass(ratios)                                   # 每類 ≤ 0.1%（提案值）
    assert not job.g2_pass(job.g2_ratios({"phone_like": 11}, {}, 10000))
    assert not job.g2_pass(ratios, max_ratio=0.0001)
    assert job.g2_pass(job.g2_ratios({"phone_like": 0}, {}, 0))  # 空分區不會除以 0


def test_claim_hands_exhausted_partitions_to_failed_first_and_uses_max_attempts():
    # 領的規則在 ledger.py（認領條件、渠道規則、CAS，見下一個測試）；job.claim 只負責：先把用盡 attempts 的轉 failed，再用 MAX_ATTEMPTS 領
    class Exhausted(FakeLedger):
        def exhaust(self, max_attempts, stale_sec=120):
            self.calls.append(("exhaust", max_attempts))
            return [("ptt/2026-09", 3)]
    led = Exhausted()
    assert job.claim(led, None) is None
    assert led.calls[0] == ("exhaust", 3)
    assert led.calls[1][:2] == ("claim", 3) and led.calls[1][3] is None
    assert job.config.MAX_ATTEMPTS == 3


def test_ledger_claim_queries_skip_exhausted_and_use_the_stale_window():
    lg = ledger.Ledger("http://fake-es:9200", "backfill_ledger")
    assert {"range": {"attempts": {"lt": 3}}} in lg._claimable_query(3, 120)["bool"]["filter"]
    assert {"range": {"attempts": {"gte": 3}}} in lg._exhausted_query(3, 120)["bool"]["filter"]
    assert ledger.STALE_SEC == 120 and ledger.CLAIM_ROUNDS == 50


def test_heartbeat_thread_beats_on_its_own_ledger_instance(monkeypatch):
    import time
    made = []

    def factory(url, index, *a, **k):
        made.append(FakeLedger(url, index))
        return made[-1]
    monkeypatch.setattr(job, "Ledger", factory)
    outer = FakeLedger()
    hb = job.Heartbeat(outer, "ptt/2026-10", interval=0.05)
    hb.start()
    time.sleep(0.3)
    hb.stop()
    hb.join(2)
    assert hb.beats >= 2 and hb.led is made[0] and hb.led is not outer   # 自己的帳本實例，不和主執行緒共用
    assert all(c == ("heartbeat", "ptt/2026-10") for c in hb.led.calls)
    assert job.HEARTBEAT_SEC == 20


def test_canary_uses_online_field_by_default():
    calls = []

    class ES:
        index = "posts"
        def req(self, method, path, body=None, **k):
            calls.append(body)
            return {"took": 7}
    assert job.canary_p95(ES(), "ptt", n=3) == 7.0
    assert calls[0]["aggs"]["t"]["terms"]["field"] == "terms_v1" == job.CANARY_FIELD
    job.canary_p95(ES(), "ptt", n=1, field="terms_v2")
    assert calls[-1]["aggs"]["t"]["terms"]["field"] == "terms_v2"


def test_recover_throttle_raises_25_percent_up_to_ceiling():
    class Tx:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class Conn:
        def __init__(self, cur, ceiling):
            self.rows, self.updates = {"max_docs_per_sec": cur, "ceiling_docs_per_sec": ceiling}, []
        def transaction(self): return Tx()
        def execute(self, sql, params=()):
            if sql.startswith("SELECT"):
                class R:
                    def fetchone(_): return self.rows
                return R()
            self.updates.append(params)
    c = Conn(750, 3000)
    assert job.recover_throttle(c, "ptt") == 937 and c.updates[0][0] == 937
    c = Conn(2900, 3000)
    assert job.recover_throttle(c, "ptt") == 3000
    c = Conn(3000, 3000)
    assert job.recover_throttle(c, "ptt") is None and c.updates == []
