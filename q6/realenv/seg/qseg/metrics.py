from prometheus_client import Counter, Histogram, Gauge, start_http_server

MSGS = Counter("qseg_messages_total", "串流訊息數", ["pipeline", "result"])
DOCS = Counter("qseg_docs_total", "寫入結果（created/updated/noop/errors）", ["pipeline", "mode", "result"])
LAG = Histogram("qseg_e2e_lag_seconds", "Pub/Sub 發佈到 ES 寫入完成的延遲", ["pipeline"],
                buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120))
SEG_SEC = Histogram("qseg_segment_seconds", "每批斷詞耗時", ["pipeline", "mode"])
BULK_SEC = Histogram("qseg_bulk_seconds", "每批 bulk 寫入耗時", ["pipeline", "mode"])
DICT = Gauge("qseg_dict_info", "目前載入的字典版本", ["pipeline", "dict_version", "seg_ver", "build"])


def serve(port: int):
    start_http_server(port)
