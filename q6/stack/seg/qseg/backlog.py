"""串流積壓追蹤（P0-4）：記住「已從 Pub/Sub 收到、還沒 ack／nack」的訊息，最舊的那則的 publish_time 就是積壓年齡。

- 在 Pub/Sub 的 callback 裡 received()，ack／nack 之後 settled()
- oldest_ts() 沒有未結訊息時回傳現在，所以 time() - min(...) 會是 0，不會因為閒置而誤報
- 只看「本程序」收到的訊息：還在 Pub/Sub 伺服器端、沒送到這個程序的積壓，要看 GCP 的 subscription/oldest_unacked_message_age（未驗證，見 MONITORING.md）
"""
import threading
import time

from . import metrics


class Tracker:
    def __init__(self, pipeline: str, clock=time.time):
        self.pipeline = pipeline
        self._clock = clock
        self._open: dict = {}          # message_id -> publish_time（Unix 秒）
        self._lock = threading.Lock()

    def received(self, message_id: str, publish_ts: float):
        with self._lock:
            self._open[message_id] = publish_ts

    def settled(self, message_id: str):
        with self._lock:
            self._open.pop(message_id, None)

    def open_count(self) -> int:
        with self._lock:
            return len(self._open)

    def oldest_ts(self) -> float:
        with self._lock:
            return min(self._open.values()) if self._open else self._clock()

    def publish(self):
        metrics.OLDEST_UNACKED.labels(self.pipeline).set(self.oldest_ts())
