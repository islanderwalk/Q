"""P0 監控測試共用的替身。本機沒有 prometheus_client 時才安裝最小替身（只支援 Counter／Gauge／Histogram 的 labels、inc、set、observe），
有裝真的就用真的。讀數一律走 metric.labels(...)._value.get()，兩種實作都成立。
不碰網路、不碰 GCP、不碰 ES；ES 用測試內的假物件。"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # .../q6/code/tests → repo 根
REALENV = ROOT / "q6" / "realenv"
SEG = REALENV / "seg"
API = REALENV / "api"
MON = REALENV / "monitoring"
PG = REALENV / "pg"
COLLECT = REALENV / "collect"


class _Val:
    def __init__(self):
        self.v = 0.0

    def get(self):
        return self.v


class _Child:
    def __init__(self):
        self._value = _Val()

    def inc(self, amount=1):
        self._value.v += amount

    def set(self, v):
        self._value.v = float(v)

    def observe(self, v):
        self._value.v += float(v)


class _Metric:
    def __init__(self, name, doc="", labelnames=(), **kw):
        self._labelnames = tuple(labelnames or ())
        self._children = {}
        self._root = _Child()

    def labels(self, *args, **kwargs):
        key = tuple(str(a) for a in args) or tuple(str(kwargs[n]) for n in self._labelnames)
        if key not in self._children:
            self._children[key] = _Child()
        return self._children[key]

    def remove(self, *labelvalues):
        key = tuple(str(a) for a in labelvalues)
        del self._children[key]   # 與 prometheus_client 一致：不存在時 KeyError

    @property
    def _value(self):
        return self._root._value

    def inc(self, amount=1):
        self._root.inc(amount)

    def set(self, v):
        self._root.set(v)

    def observe(self, v):
        self._root.observe(v)


def ensure_prometheus_client():
    try:
        import prometheus_client  # noqa: F401
        return
    except ImportError:
        pass
    mod = types.ModuleType("prometheus_client")
    mod.Counter = type("Counter", (_Metric,), {})
    mod.Gauge = type("Gauge", (_Metric,), {})
    mod.Histogram = type("Histogram", (_Metric,), {})
    mod.start_http_server = lambda port, *a, **k: None
    mod.generate_latest = lambda *a, **k: b""
    mod.CONTENT_TYPE_LATEST = "text/plain"
    sys.modules["prometheus_client"] = mod


def read_value(metric, *labels):
    """讀某個 label 組合的目前值（沒有 label 就讀根）。"""
    child = metric.labels(*labels) if labels else metric
    return child._value.get()


def has_series(metric, *labels) -> bool:
    """某個 label 組合是否還存在（fake 看 _children；真的 prometheus_client 看 _metrics）。"""
    key = tuple(str(a) for a in labels)
    if hasattr(metric, "_children"):
        return key in metric._children
    return key in metric._metrics


def read_text(path: Path) -> str:
    """讀文字檔（UTF-8），統一成 LF 方便比對。"""
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")
