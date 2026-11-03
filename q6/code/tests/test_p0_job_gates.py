"""回補 Job 的計畫書修正：G1 比不重複 post_id、G2 比例門檻、認領 attempts < 3、心跳背景執行緒、G3 限速回升。
只測純函式與 SQL 字串；不連 ES／PG（ES 用假物件）。"""
import json
import sys
import threading

import pytest

from _p0_fakes import SEG, ensure_prometheus_client

ensure_prometheus_client()
if str(SEG) not in sys.path:
    sys.path.insert(0, str(SEG))
job = pytest.importorskip("qseg.job")


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


def test_claim_sql_skips_exhausted_partitions_and_marks_them_failed():
    import inspect
    src = inspect.getsource(job.claim)
    assert "attempts < %s" in src and "EXHAUST_SQL" in src
    assert "attempts >= %s" in job.EXHAUST_SQL and "status='failed'" in job.EXHAUST_SQL and "attempts_exhausted" in job.EXHAUST_SQL
    assert job.config.MAX_ATTEMPTS == 3


def test_heartbeat_thread_beats_on_its_own_connection(monkeypatch):
    executed = []

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=()): executed.append((sql, params))
    monkeypatch.setattr(job.psycopg, "connect", lambda dsn, **k: Conn())
    hb = job.Heartbeat("dsn", "ptt/2026-10", interval=0.05)
    hb.start()
    import time
    time.sleep(0.3)
    hb.stop()
    hb.join(2)
    assert hb.beats >= 2 and all("heartbeat_at=now()" in s and p == ("ptt/2026-10",) for s, p in executed)
    assert "status='running'" in executed[0][0]
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
