"""字典發版的控制訊息（Pub/Sub 主題 dict-releases）：串流斷詞服務 v2 多訂閱這一個主題，收到後

1. 從字典 artifact 伺服器載入該版（逐檔比對 sha256，對不上就拒絕）
2. 原子換掉服務使用的字典：之後進來的串流訊息立刻用新版、寫新的 seg_ver
3. 對「原文含新詞字串」的文章定向重跑（rerun.run_rerun），進度每批寫 PostgreSQL rerun_runs，PM 頁每 2 秒讀一次

GCP 上這就是 Cloud Run 服務多一個訂閱；本機是官方 Pub/Sub 模擬器，client 程式碼相同。
同一時間只處理一則發版訊息（HANDLE_LOCK），重複投遞的舊版本直接略過（seg_ver 只能前進）。

計畫書修正：所有 v2 執行個體共用一個發版訂閱，Pub/Sub 每則訊息只交給其中一台，其他台會停在舊字典直到重啟。
所以每台另外每 MANIFEST_POLL_SEC 秒輪詢 current/manifest.json 當真相：版本比自己新就載入並熱換（不重跑）；
控制訊息只當提示，收到的那台負責定向重跑。熱換時移除 qseg_dict_info 的舊版 label（原本舊版會一直是 1）。
"""
import json
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from . import config, dictionary, metrics
from .logs import log
from .pipeline import seg_ver
from .rerun import run_rerun

LIVE_SQL = Path(__file__).resolve().parent.parent / "live.sql"   # 映像裡的 /app/live.sql（與 pg/live.sql 同一份）
HANDLE_LOCK = threading.Lock()
MANIFEST_POLL_SEC = float(__import__("os").environ.get("MANIFEST_POLL_SEC", "15"))
RUN_FIELDS = {"status", "worker", "candidates", "scanned", "terms_changed", "written", "noop", "errors", "seconds",
              "examples", "error", "started_at", "finished_at"}


class SegState:
    """服務目前使用的字典；換版時整個物件替換（舊 Tokenizer 讓正在處理的那一批用完就丟）。"""

    def __init__(self, dic):
        self._dic = dic
        self.lock = threading.Lock()

    @property
    def dic(self):
        return self._dic

    def swap(self, dic):
        with self.lock:
            old, self._dic = self._dic, dic
        if old is not None and old.version != dic.version:
            try:   # 熱換後舊版的 qseg_dict_info 不能還是 1
                metrics.DICT.remove(old.pipeline, old.version, str(seg_ver(old.pipeline, old.version)), config.SEG_BUILD)
            except KeyError:
                pass
        metrics.DICT.labels(dic.pipeline, dic.version, str(seg_ver(dic.pipeline, dic.version)), config.SEG_BUILD).set(1)


def service_name() -> str:
    return f"{config.PIPELINE}@{socket.gethostname()}"


def _pg():
    return psycopg.connect(config.PG_DSN, autocommit=True)


def ensure_tables() -> None:
    """live.sql 全是 IF NOT EXISTS；既有的 PostgreSQL volume（沒跑過 initdb）也能補上新表。"""
    if not LIVE_SQL.exists():
        return
    try:
        with _pg() as c:
            c.execute(LIVE_SQL.read_text(encoding="utf-8"))
    except Exception as e:  # PG 沒起來不該讓斷詞服務起不來
        log("live_tables_failed", "WARNING", error=str(e)[:200])


def record_service_state(dic) -> None:
    try:
        with _pg() as c:
            c.execute("""INSERT INTO service_state (service, pipeline, dict_version, seg_ver, build, loaded_at, heartbeat_at)
                         VALUES (%s,%s,%s,%s,%s,now(),now())
                         ON CONFLICT (service) DO UPDATE SET pipeline=EXCLUDED.pipeline, dict_version=EXCLUDED.dict_version,
                           seg_ver=EXCLUDED.seg_ver, build=EXCLUDED.build, loaded_at=now(), heartbeat_at=now()""",
                      (service_name(), dic.pipeline, dic.version, seg_ver(dic.pipeline, dic.version), config.SEG_BUILD))
    except Exception as e:
        log("service_state_failed", "WARNING", error=str(e)[:200])


def heartbeat() -> None:
    try:
        with _pg() as c:
            c.execute("UPDATE service_state SET heartbeat_at=now() WHERE service=%s", (service_name(),))
    except Exception:
        pass


def set_run(run_id, **fields) -> None:
    if run_id is None:
        return
    cols = {k: v for k, v in fields.items() if k in RUN_FIELDS}
    if not cols:
        return
    if "examples" in cols:
        cols["examples"] = json.dumps(cols["examples"], ensure_ascii=False)
    sets = ", ".join(f"{k}=%s" for k in cols) + ", updated_at=now()"
    try:
        with _pg() as c:
            c.execute(f"UPDATE rerun_runs SET {sets} WHERE id=%s", (*cols.values(), run_id))
    except Exception as e:
        log("rerun_run_update_failed", "WARNING", error=str(e)[:200])


def parse_message(data: bytes):
    """發版訊息：{"action":"release","dict_version":"qsearch-q6-2026.10.31.1","words":["颱風假"],"run_id":12}。不合格回 None。"""
    try:
        msg = json.loads(data.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(msg, dict) or msg.get("action") != "release":
        return None
    v, words = msg.get("dict_version"), msg.get("words")
    if not isinstance(v, str) or not isinstance(words, list) or not words or not all(isinstance(w, str) and w.strip() for w in words):
        return None
    try:
        seg_ver("v2", v)
    except Exception:
        return None
    return {"dict_version": v, "words": [w.strip() for w in words], "run_id": msg.get("run_id"), "requested_by": msg.get("requested_by")}


def handle_release(msg: dict, state: SegState, es) -> None:
    run_id = msg.get("run_id")
    now = datetime.now(timezone.utc)
    target, current = seg_ver("v2", msg["dict_version"]), seg_ver("v2", state.dic.version)
    if target <= current:
        log("release_skipped", dict_version=msg["dict_version"], loaded=state.dic.version, reason="已載入同版或更新版")
        set_run(run_id, status="skipped", worker=service_name(), error=f"服務已在 {state.dic.version}", finished_at=now)
        return
    set_run(run_id, status="loading", worker=service_name(), started_at=now)
    try:
        dic = load_and_swap(state, msg["dict_version"], source="control", words=msg["words"], requested_by=msg.get("requested_by"))
    except Exception as e:
        set_run(run_id, status="failed", error=f"載入字典失敗：{str(e)[:200]}", finished_at=datetime.now(timezone.utc))
        return
    set_run(run_id, status="running")
    last = [0.0]

    def progress(p):
        if time.time() - last[0] >= 0.5:
            last[0] = time.time()
            set_run(run_id, candidates=p["candidates"], scanned=p["scanned"], terms_changed=p["changed"],
                    written=p["written"], noop=p["noop"], errors=p["errors"])

    try:
        out = run_rerun(dic, es, msg["words"], batch=500, on_progress=progress)
    except Exception as e:
        log("rerun_failed", "ERROR", error=str(e)[:300])
        set_run(run_id, status="failed", error=str(e)[:300], finished_at=datetime.now(timezone.utc))
        return
    set_run(run_id, status="done" if out["errors"] == 0 else "failed", candidates=out["candidates"], scanned=out["scanned"],
            terms_changed=out["terms_changed"], written=out["created"] + out["updated"], noop=out["noop"], errors=out["errors"],
            seconds=out["seconds"], examples=out["examples"], finished_at=datetime.now(timezone.utc))


def load_and_swap(state: SegState, version: str, source: str, **extra):
    """載入指定版本（逐檔 sha256）、熱換、寫 service_state、記指標。失敗會丟例外（呼叫端決定要不要寫 rerun_runs）。"""
    t0 = time.perf_counter()
    try:
        dic = dictionary.load(version=version)
    except Exception as e:
        log("dict_load_failed", "ERROR", dict_version=version, source=source, error=str(e)[:300])
        raise
    state.swap(dic)
    record_service_state(dic)
    metrics.DICT_SWAPS.labels(dic.pipeline, source).inc()
    log("dict_reloaded", dict_version=dic.version, seg_ver=seg_ver(dic.pipeline, dic.version), load_seconds=round(time.perf_counter() - t0, 2),
        source=source, **extra)
    return dic


def current_manifest_version(base_url: str = None):
    """字典伺服器 current/manifest.json 的 dict_version；抓不到回 None。"""
    import requests
    try:
        r = requests.get(f"{(base_url or config.DICT_BASE_URL).rstrip('/')}/current/manifest.json", timeout=10)
        r.raise_for_status()
        return r.json().get("dict_version")
    except Exception as e:
        log("manifest_poll_failed", "WARNING", error=str(e)[:200])
        return None


def newer_version(loaded: str, seen: str):
    """current 的版本比已載入的新（seg_ver 比大小）才換；舊版或壞版本號一律不動。"""
    if not seen or seen == loaded:
        return False
    try:
        return seg_ver("v2", seen) > seg_ver("v2", loaded)
    except Exception:
        return False


def start_manifest_poller(state: SegState, interval: float = None, stop: threading.Event = None) -> threading.Thread:
    """每台 v2 執行個體自己盯 current/manifest.json：發版訊息只會到其中一台，其他台靠這裡收斂（不重跑，只換字典）。stop 是測試用的停止旗標。"""
    def loop():
        while not (stop is not None and stop.is_set()):
            time.sleep(interval or MANIFEST_POLL_SEC)
            if stop is not None and stop.is_set():
                break
            try:
                v = current_manifest_version()
                if newer_version(state.dic.version, v):
                    with HANDLE_LOCK:
                        if newer_version(state.dic.version, v):   # 拿到鎖時可能已被控制訊息換掉
                            load_and_swap(state, v, source="manifest")
            except Exception as e:
                log("manifest_poll_error", "WARNING", error=str(e)[:200])
    t = threading.Thread(target=loop, daemon=True, name="manifest-poller")
    t.start()
    return t


def _ensure_subscription() -> None:
    """本機模擬器：主題與訂閱不存在就建（真 GCP 由 Terraform 管，這裡不動）。"""
    if not config.PUBSUB_EMULATOR_HOST:
        return
    import requests
    base = f"http://{config.PUBSUB_EMULATOR_HOST}/v1/projects/{config.PROJECT}"
    requests.put(f"{base}/topics/{config.CONTROL_TOPIC}", json={}, timeout=15)
    requests.put(f"{base}/subscriptions/{config.CONTROL_SUBSCRIPTION}", timeout=15,
                 json={"topic": f"projects/{config.PROJECT}/topics/{config.CONTROL_TOPIC}", "ackDeadlineSeconds": 600})


def start(state: SegState, es):
    """只有 v2 且設了 CONTROL_SUBSCRIPTION 的服務才訂閱；回傳 streaming pull 的 future（背景執行緒）。"""
    if not config.CONTROL_SUBSCRIPTION or state.dic.pipeline != "v2":
        return None
    from google.cloud import pubsub_v1
    _ensure_subscription()
    sub = pubsub_v1.SubscriberClient()
    path = sub.subscription_path(config.PROJECT, config.CONTROL_SUBSCRIPTION)

    def callback(m):
        msg = parse_message(m.data)
        if msg is None:
            log("control_bad_message", "WARNING", data=m.data[:200].decode("utf-8", "replace"))
            m.ack()
            return
        with HANDLE_LOCK:
            try:
                handle_release(msg, state, es)
            finally:
                m.ack()   # 失敗也 ack：狀態已寫進 rerun_runs，重送只會再失敗一次

    future = sub.subscribe(path, callback=callback, flow_control=pubsub_v1.types.FlowControl(max_messages=1))
    start_manifest_poller(state)
    log("control_subscribed", subscription=config.CONTROL_SUBSCRIPTION, topic=config.CONTROL_TOPIC, manifest_poll_sec=MANIFEST_POLL_SEC)
    return future
