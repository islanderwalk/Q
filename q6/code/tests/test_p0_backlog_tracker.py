"""P0-4：串流積壓年齡。最舊的未 ack／nack 訊息的 publish_time，沒有未結訊息時年齡為 0（不誤報）。
時鐘用假函式，不等真時間。service.py 因為 google-cloud-pubsub 不在本機，無法 import，這裡只測 Tracker 本身。"""
import sys

from _p0_fakes import SEG, ensure_prometheus_client, read_value

ensure_prometheus_client()
sys.path.insert(0, str(SEG))

from qseg import metrics  # noqa: E402
from qseg.backlog import Tracker  # noqa: E402


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_empty_tracker_reports_now_so_age_is_zero():
    clk = Clock(1000.0)
    tr = Tracker("v2-test-empty", clock=clk)
    assert tr.open_count() == 0
    assert tr.oldest_ts() == 1000.0
    tr.publish()
    assert read_value(metrics.OLDEST_UNACKED, "v2-test-empty") == 1000.0


def test_oldest_is_min_publish_time_of_open_messages():
    clk = Clock(2000.0)
    tr = Tracker("v2-test-min", clock=clk)
    tr.received("m1", 1900.0)
    tr.received("m2", 1500.0)
    tr.received("m3", 1800.0)
    assert tr.open_count() == 3
    assert tr.oldest_ts() == 1500.0


def test_settling_the_oldest_moves_the_age_to_the_next_oldest():
    clk = Clock(2000.0)
    tr = Tracker("v2-test-settle", clock=clk)
    tr.received("m1", 1500.0)
    tr.received("m2", 1800.0)
    tr.settled("m1")
    assert tr.oldest_ts() == 1800.0
    tr.settled("m2")
    assert tr.oldest_ts() == clk.t            # 全部結清 → 年齡回到 0


def test_settling_unknown_id_is_harmless():
    tr = Tracker("v2-test-unknown", clock=Clock(5.0))
    tr.settled("never-received")
    assert tr.open_count() == 0


def test_publish_sets_age_gauge_from_tracker_clock():
    clk = Clock(3000.0)
    tr = Tracker("v2-test-age", clock=clk)
    tr.received("m1", 2700.0)
    tr.publish()
    gauge = read_value(metrics.OLDEST_UNACKED, "v2-test-age")
    assert clk.t - gauge == 300.0            # PromQL 的 time() - min(...) 會得到 300 秒
