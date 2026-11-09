"""QSearch 文字雲 API：讀 ES 的 terms 聚合，seg=v1|v2 切換新舊詞表，給 PM 並排比對與客戶文字雲；
加上 PM 頁「資料工程端」的實際操作（字典發版、回補簽核、重播樣本、限速、採集器），全部打到同一套 ES／PostgreSQL／Pub/Sub。

- 一次請求一個 ES 搜尋：query 決定文章範圍，terms 聚合算「範圍內有幾篇文章提到這個詞」
- 軟停用詞（問卦、有沒有…）在查詢時用 terms 聚合的 exclude 處理，改清單不必重跑歷史
- 變體合併（臺北市→台北市）也在查詢時合併 bucket（篇數相加是上限：同一篇兩種寫法都有會算兩次）
- 快取鍵＝（正規化查詢, 範圍, 渠道排序, seg, 字典版本, 發版世代號）；範圍含今天（或沒有結束日）才再加 ES 狀態 token。
  token 每秒重算一次（總篇數、三版已斷詞篇數、最大 seg_ver、最後斷詞時間），串流一寫入快取立刻失效，PM 頁每 2 秒輪詢 /api/status 看 token 有沒有變；
  已結束的日期範圍只會因回補分區完成或定向重跑完成而變（世代號），TTL 24 小時，正式環境命中率才不會接近 0。同鍵的併發未命中只算一次（single-flight）
- 逾時一層比一層短：連 ES 3 秒、讀最多 180 秒（＝SLA，不再多等 30 秒）；ES 查詢本身帶 timeout=170s，ES 回 timed_out=true 只有部分結果就當壞查詢（reason=partial）
- SLI：分母 wordcloud_duration_seconds_count（成功與失敗都記耗時），分子 le=180 且 status=ok 的桶；燃燒率規則由 Sloth 產生（monitoring/slo/ → rules/slo.yml）
- 告警：Prometheus → Alertmanager → POST /api/ops/alert → live_events(kind=alert)，PM 頁的事件流與橫幅直接讀（零成本通知管道）
- 發版：從 artifact 的 current 版建下一版、寫 rerun_runs 與 release_outbox（先寫 outbox 再切 current）、原子切 current、
  發控制訊息到 dict-releases；發不出去就留在 outbox，背景每 10 秒重送（斷詞服務另外每 15 秒輪詢 current/manifest.json，
  字典一定會收斂，控制訊息只決定誰做定向重跑）；斷詞服務 v2 收到後重載字典、定向重跑，進度寫回 rerun_runs（seg/qseg/control.py）
- 每次查詢寫一行結構化 JSON（event=wordcloud_query, duration_ms, es_call_ms…），Cloud Run 會收進 Cloud Logging；本機另有 Prometheus 指標
"""
import json
import os
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

try:
    from psycopg_pool import ConnectionPool    # psycopg[pool]；沒裝就退回每次查詢開一條連線
except ImportError:                            # pragma: no cover
    ConnectionPool = None

import ops

ES_URL = os.environ.get("ES_URL", "http://es:9200").rstrip("/")
INDEX = os.environ.get("ES_INDEX", "posts")
DICT_BASE_URL = os.environ.get("DICT_BASE_URL", "http://dictsrv").rstrip("/")
DICT_ROOT = Path(os.environ.get("DICT_ROOT", "/dict"))
PG_DSN = os.environ.get("PG_DSN", "postgresql://qsearch:qsearch@pg:5432/qsearch")
PROJECT = os.environ.get("PUBSUB_PROJECT", "qsearch-local")
EMULATOR = os.environ.get("PUBSUB_EMULATOR_HOST", "")
PARSED_TOPIC = os.environ.get("PARSED_TOPIC", "parsed-posts")
CONTROL_TOPIC = os.environ.get("CONTROL_TOPIC", "dict-releases")
CONTROL_SUB = os.environ.get("CONTROL_SUBSCRIPTION", "dict-releases-seg-v2")
SAMPLES = Path(os.environ.get("CHANNEL_SAMPLES", str(Path(__file__).resolve().parent / "data" / "channels")))
LIVE_SQL = Path(os.environ.get("LIVE_SQL", str(Path(__file__).resolve().parent / "live.sql")))
SLA_MS = 180_000
COMPARE_DEADLINE_S = SLA_MS / 1000 + 5       # PM 頁兩欄合計的上限：超過就回 JSON 504，瀏覽器一定收到回應，不會只看到連線中斷
COMPARE_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="compare")   # 兩欄同時查（原本一欄查完才查下一欄）
ES_CONNECT_TIMEOUT_S = float(os.environ.get("ES_CONNECT_TIMEOUT_S", "3"))
ES_SEARCH_TIMEOUT = os.environ.get("ES_SEARCH_TIMEOUT", "170s")        # ES 查詢自己的 timeout：比 SLA 早一點回 timed_out=true，當壞查詢（partial）
CACHE_TTL_CLOSED_S = int(os.environ.get("CACHE_TTL_CLOSED_S", "86400"))  # 已結束日期範圍：只會因回補／重跑而變（世代號失效）
CACHE_TTL_OPEN_S = int(os.environ.get("CACHE_TTL_OPEN_S", "300"))        # 含今天／沒有結束日：鍵裡還有 ES 狀態 token，串流一寫入就失效
STATE_TTL = 1.0
GEN_TTL = 5.0
SEGS = ("v0", "v1", "v2")
ERR_REASONS = ("timeout", "partial", "other")
OPS_KINDS = ("approve", "waive", "release", "replay", "control", "collect_request", "alert")
CHANNELS = list(ops.CHANNELS)
HARD_STOP = set("的 了 在 是 和 與 及 或 也 都 就 而 等 啊 吧 呢 嗎 喔 哦 耶 呀 嘛 這 那 個 我 你 他 她 它 我們 你們 他們 有 會 很 被 把 給".split())
NUM_RE = re.compile(r"^[\d\W_]*\d[\d\W_]*$")
SYMBOL_RE = re.compile(r"^[\W_]+$")
ASCII_MIX_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d]{4,}$|^(?:http|https|www|com|cc|tw|ly|html|php)$", re.I)
V1_NOTE = "舊流程 v1 = jieba 預設主字典、HMM 關、不清洗、不過濾。公司現行字典拿不到，這是對應元件；v1 的詞表是實際算出來的，但不是公司現在線上的詞表。"
V0_NOTE = "舊系統重現 v0 = 凍結於 2019-12-31 的字典（教育部 2015＋jieba-tw 2016＋公司詞表 2019＋舊行政區名）＋逆向最長匹配，不清洗、不過濾。依題目線索推論的對應元件，非公司原碼；重現「維護多年、停在某一年」的舊文字雲。"

DUR = Histogram("wordcloud_duration_seconds", "API 總耗時；status=ok 成功、error 失敗（失敗也記耗時）。SLI 分母用 _count，分子用 le=180 且 status=ok 的桶",
                ["seg", "cache", "status"], buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 180, 300))
ES_CALL = Histogram("wordcloud_es_call_seconds", "API 呼叫 ES 的往返耗時", ["seg"],
                    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 180))
PHASE = Histogram("wordcloud_phase_seconds", "未命中快取的查詢拆段：overlay（讀字典 overlay）、state（ES 狀態 token）、es（ES 搜尋往返）、post（合併變體與整理）",
                  ["seg", "phase"], buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10, 60, 180))
OVER = Counter("wordcloud_over_180s_total", "超過 180 秒的文字雲查詢，成功與失敗都算（對應 Cloud Logging 日誌型指標 wordcloud_over_180s）", ["seg"])
ERR = Counter("wordcloud_errors_total", "文字雲查詢錯誤（reason=timeout：Python 端讀取逾時 180 秒；partial：ES 自己的 timeout 到了只回部分結果；other：ES 回錯或連不上）", ["seg", "reason"])
INFLIGHT = Gauge("wordcloud_inflight_queries", "處理中、正在等 ES 的未命中快取查詢數", ["seg"])
CACHE_EVENTS = Counter("wordcloud_cache_total", "快取結果：hit／miss／coalesced（同鍵的併發請求等前一個算完直接用）", ["seg", "result"])
PG_ERR = Counter("wordcloud_pg_errors_total", "API 對 PostgreSQL 的查詢失敗（原本只寫日誌）")
ALERTS = Counter("wordcloud_alert_webhooks_total", "Alertmanager 送到 /api/ops/alert 的告警數（status=firing／resolved）", ["status"])
# PM 頁 RUM（web/rum.js 自寫，不裝 Faro SDK；POST /api/rum）：瀏覽器端的 Web Vitals、API 往返、首次出雲、丟詞、不一致、JS 錯誤
WEB_VITALS = Histogram("wordcloud_web_vitals_seconds", "PM 頁 RUM：metric=lcp（最大內容繪製）／inp（互動延遲，頁面離開時的最大值）／ttfc（首次出雲）秒數", ["metric"],
                       buckets=(0.1, 0.2, 0.4, 0.8, 1.2, 1.6, 2.5, 4, 6, 8, 12, 20, 30, 60, 120, 180, 300))
WEB_CLS = Histogram("wordcloud_web_cls", "PM 頁 RUM：累積版面位移 CLS（無單位；頁面離開時送）", buckets=(0.01, 0.025, 0.05, 0.1, 0.15, 0.25, 0.5, 1.0))
WEB_API = Histogram("wordcloud_web_api_seconds", "PM 頁 RUM：瀏覽器端量到的 API 往返秒數（含網路；對照伺服器端 wordcloud_duration_seconds）", ["path"],
                    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 180, 300))
WEB_ERRORS = Counter("wordcloud_web_errors_total", "PM 頁 RUM：JS 錯誤（kind=error／unhandledrejection）", ["kind"])
WEB_RENDERS = Counter("wordcloud_web_renders_total", "PM 頁 RUM：畫了幾次文字雲（seg=v0／v1／v2）", ["seg"])
WEB_DROPPED = Counter("wordcloud_web_dropped_words_total", "PM 頁 RUM：排版放不下而沒畫出來的詞數（seg=v0／v1／v2）", ["seg"])
WEB_MISMATCH = Counter("wordcloud_web_count_mismatch_total", "PM 頁 RUM：雲上篇數與明細抽屜 total 不一致的次數（兩次查詢之間資料變了）", ["seg"])
WEB_BEACONS = Counter("wordcloud_web_beacons_total", "PM 頁 RUM：收到的 beacon 數（status=ok／bad）", ["status"])
WEB_PATHS = ("/api/compare", "/api/posts", "/api/status", "/api/overview", "/api/wordcloud", "/api/ops/ledger")
OUTBOX_PENDING = Gauge("wordcloud_release_outbox_pending", "發版 outbox 裡還沒送出去的控制訊息數（背景每 10 秒重送）")
OUTBOX_SENT = Counter("wordcloud_release_outbox_total", "發版控制訊息的送出結果（result=sent／retry_sent／failed）", ["result"])
OPS = Counter("pm_ops_total", "PM 頁資料工程端操作", ["kind"])


def precreate_labels():
    """啟動時先把每個 label 組合建成 0：帶 label 的 counter 第一次出現時，Prometheus 的 increase()／rate() 會算成 0，
    API 重啟後的第一次逾時就不會觸發告警。"""
    for seg in SEGS:
        OVER.labels(seg)
        ES_CALL.labels(seg)
        INFLIGHT.labels(seg)
        for r in ERR_REASONS:
            ERR.labels(seg, r)
        for cache, status in (("hit", "ok"), ("miss", "ok"), ("miss", "error")):
            DUR.labels(seg, cache, status)
        for r in ("hit", "miss", "coalesced"):
            CACHE_EVENTS.labels(seg, r)
    for m in ("lcp", "inp", "ttfc"):
        WEB_VITALS.labels(m)
    for pth in WEB_PATHS + ("other",):
        WEB_API.labels(pth)
    for k in ("error", "unhandledrejection"):
        WEB_ERRORS.labels(k)
    for seg in ("v0", "v1", "v2", "other"):
        WEB_RENDERS.labels(seg)
        WEB_DROPPED.labels(seg)
        WEB_MISMATCH.labels(seg)
    for st in ("ok", "bad"):
        WEB_BEACONS.labels(st)
        for ph in ("overlay", "state", "es", "post"):
            PHASE.labels(seg, ph)
    for k in OPS_KINDS:
        OPS.labels(k)
    for st in ("firing", "resolved"):
        ALERTS.labels(st)
    for r in ("sent", "retry_sent", "failed"):
        OUTBOX_SENT.labels(r)


precreate_labels()

S = requests.Session()
CACHE: dict = {}
_FLIGHTS: dict = {}                       # 正在計算中的快取鍵 → Event（single-flight）
_FLIGHTS_LOCK = threading.Lock()
_DICT = {"at": 0.0, "v": None}
_STATE = {"at": 0.0, "v": None, "lock": threading.Lock()}
_GEN = {"at": 0.0, "v": "0:0"}
_RECENT = {"token": None, "v": []}
RELEASE_LOCK = threading.Lock()
PUBSUB = ops.PubSub(PROJECT, EMULATOR or None)


def log(**rec):
    rec.setdefault("severity", "INFO")
    rec["time"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    sys.stdout.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


class EsPartial(RuntimeError):
    """ES 查詢自己的 timeout 到了（回應 timed_out=true）：只回部分結果，文字雲會少算，當壞查詢處理。"""


def es(method, path, body=None, timeout=None):
    """連 ES 3 秒、讀取最多 180 秒（＝SLA）：超過 SLA 的查詢就是失敗，不再多等 30 秒才算；逾時層層遞減（ES 自己 170 秒 → 這裡 180 秒）。"""
    t0 = time.perf_counter()
    r = S.request(method, f"{ES_URL}{path}", json=body, timeout=timeout or (ES_CONNECT_TIMEOUT_S, SLA_MS / 1000))
    ms = (time.perf_counter() - t0) * 1000
    if r.status_code == 404 and "index_not_found" in r.text:
        raise HTTPException(503, f"ES 索引 {INDEX} 還沒建立（先跑 ./run.sh seed）")
    r.raise_for_status()
    return r.json(), ms


# ---------- PostgreSQL（帳本、發版進度、服務狀態、事件流） ----------
def _pg_kwargs():
    from psycopg.rows import dict_row
    # 每條敘述最多 10 秒：PM 頁每 2 秒輪詢，一條卡住的查詢不該把 API 的執行緒全吃掉
    return {"autocommit": True, "row_factory": dict_row, "options": "-c statement_timeout=10000"}


POOL = (ConnectionPool(PG_DSN, min_size=1, max_size=int(os.environ.get("PG_POOL_MAX", "4")), open=False, timeout=10,
                       kwargs=_pg_kwargs(), name="wordcloud-api") if ConnectionPool else None)


@contextmanager
def pg():
    """PostgreSQL 連線：有 psycopg_pool 就從池裡拿（每個執行個體 1–4 條，不再每次查詢新開連線）；池沒開（測試）或沒裝就單次連線。"""
    if POOL is not None and not POOL.closed:
        with POOL.connection() as c:
            yield c
    else:
        import psycopg
        with psycopg.connect(PG_DSN, **_pg_kwargs()) as c:
            yield c


def pg_rows(sql, params=()):
    try:
        with pg() as c:
            return c.execute(sql, params).fetchall()
    except Exception as e:
        PG_ERR.inc()
        log(severity="WARNING", event="pg_query_failed", error=str(e)[:200])
        return []


def pg_exec(sql, params=()):
    with pg() as c:
        cur = c.execute(sql, params)
        return cur.rowcount, (cur.fetchone() if cur.description else None)


def event(kind, actor, **detail):
    OPS.labels(kind).inc()
    try:
        pg_exec("INSERT INTO live_events (kind, actor, detail) VALUES (%s,%s,%s)", (kind, actor, json.dumps(detail, ensure_ascii=False, default=str)))
    except Exception as e:
        log(severity="WARNING", event="event_failed", error=str(e)[:200])


def ensure_live():
    """API 啟動：開連線池、補 live.sql 的表（全 IF NOT EXISTS）、確保主題與 v2 的發版訂閱存在（模擬器）；都失敗也不擋啟動。"""
    if POOL is not None:
        try:
            POOL.open(wait=False)
        except Exception as e:
            log(severity="WARNING", event="pg_pool_open_failed", error=str(e)[:200])
    try:
        if LIVE_SQL.exists():
            pg_exec(LIVE_SQL.read_text(encoding="utf-8"))
    except Exception as e:
        log(severity="WARNING", event="live_sql_failed", error=str(e)[:200])
    try:
        PUBSUB.ensure(PARSED_TOPIC)
        PUBSUB.ensure(CONTROL_TOPIC, CONTROL_SUB)
    except Exception as e:
        log(severity="WARNING", event="pubsub_ensure_failed", error=str(e)[:200])


def outbox_flush(limit: int = 20) -> int:
    """重送 outbox 裡 pending 的發版控制訊息；回傳送出幾則。送出就標 sent，失敗只加 attempts（下次再試）。"""
    rows = pg_rows("SELECT id, dict_version, run_id, payload, attempts FROM release_outbox WHERE status='pending' ORDER BY id LIMIT %s", (limit,))
    sent = 0
    for r in rows:
        try:
            msg_id = PUBSUB.publish(CONTROL_TOPIC, r["payload"], dict_version=r["dict_version"], run_id=r["run_id"])
            pg_exec("UPDATE release_outbox SET status='sent', sent_at=now(), attempts=attempts+1, message_id=%s WHERE id=%s", (msg_id, r["id"]))
            OUTBOX_SENT.labels("retry_sent").inc()
            log(event="release_outbox_sent", outbox_id=r["id"], dict_version=r["dict_version"], run_id=r["run_id"], attempts=r["attempts"] + 1)
            sent += 1
        except Exception as e:
            pg_exec("UPDATE release_outbox SET attempts=attempts+1, last_error=%s WHERE id=%s", (str(e)[:200], r["id"]))
            log(severity="WARNING", event="release_outbox_retry_failed", outbox_id=r["id"], error=str(e)[:200])
    left = pg_rows("SELECT count(*) AS n FROM release_outbox WHERE status='pending'")
    OUTBOX_PENDING.set(left[0]["n"] if left else 0)
    return sent


def _outbox_loop(interval: float = 10.0):
    while True:
        time.sleep(interval)
        try:
            outbox_flush()
        except Exception as e:
            log(severity="WARNING", event="release_outbox_loop_error", error=str(e)[:200])


@asynccontextmanager
async def lifespan(_app):
    ensure_live()
    threading.Thread(target=_outbox_loop, daemon=True, name="release-outbox").start()
    log(event="api_start", dict_root=str(DICT_ROOT), dict_root_mounted=DICT_ROOT.exists(), samples=str(SAMPLES), emulator=EMULATOR)
    yield


app = FastAPI(title="QSearch 文字雲 API（Q6）", lifespan=lifespan)


# ---------- 字典 overlay 與 ES 狀態 token ----------
def dict_overlay():
    """軟停用詞與合併規則：從字典 artifact 的 current 讀（每 30 秒更新一次；發版後立刻重讀），與斷詞服務同一份來源。"""
    if time.time() - _DICT["at"] > 30 or _DICT["v"] is None:
        try:
            man = S.get(f"{DICT_BASE_URL}/current/manifest.json", timeout=5).json()
            # 一定要自己用 UTF-8 解：nginx 回 text/plain 沒帶 charset，requests 的 .text 會當 Latin-1，中文全變亂碼、軟停用詞等於沒套
            txt = lambda name: S.get(f"{DICT_BASE_URL}/current/{name}", timeout=5).content.decode("utf-8")
            soft = [w for w in txt("soft_stopwords.txt").splitlines() if w.strip()]
            merges = dict(l.split("\t") for l in txt("merges.tsv").splitlines() if "\t" in l)
            _DICT["v"] = {"dict_version": man["dict_version"], "soft": soft, "merges": merges}
        except Exception as e:
            _DICT["v"] = _DICT["v"] or {"dict_version": None, "soft": [], "merges": {}, "error": str(e)}
        _DICT["at"] = time.time()
    return _DICT["v"]


def es_state():
    """ES 狀態 token（每秒最多算一次）：任何寫入（新文章、斷詞、重跑）都會改變它。進快取鍵，也給 PM 頁判斷「有沒有變」。"""
    with _STATE["lock"]:
        if _STATE["v"] is None or time.time() - _STATE["at"] > STATE_TTL:
            body = {"size": 0, "track_total_hits": True, "aggs": {
                # 「有 vX」看 seg_ver_vX：exists 對空陣列是 false，詞表為空的文章（標題只剩符號）也已經斷過詞
                "v0": {"filter": {"exists": {"field": "seg_ver_v0"}}},
                "v1": {"filter": {"exists": {"field": "seg_ver_v1"}}}, "v2": {"filter": {"exists": {"field": "seg_ver_v2"}}},
                "max_v0": {"max": {"field": "seg_ver_v0"}}, "max_v1": {"max": {"field": "seg_ver_v1"}}, "max_v2": {"max": {"field": "seg_ver_v2"}},
                "at_v0": {"max": {"field": "seg_at_v0"}}, "at_v1": {"max": {"field": "seg_at_v1"}}, "at_v2": {"max": {"field": "seg_at_v2"}},
                "pub": {"max": {"field": "published_at"}}, "fet": {"max": {"field": "fetched_at"}}}}
            try:
                res, ms = es("POST", f"/{INDEX}/_search?request_cache=false", body, timeout=(ES_CONNECT_TIMEOUT_S, 30))
                ag = res["aggregations"]
                v = {"ok": True, "total": res["hits"]["total"]["value"], "with_v0": ag["v0"]["doc_count"], "with_v1": ag["v1"]["doc_count"], "with_v2": ag["v2"]["doc_count"],
                     "max_seg_ver_v0": ag["max_v0"]["value"], "max_seg_ver_v1": ag["max_v1"]["value"], "max_seg_ver_v2": ag["max_v2"]["value"],
                     "last_seg_at_v0": ag["at_v0"].get("value_as_string"), "last_seg_at_v1": ag["at_v1"].get("value_as_string"), "last_seg_at_v2": ag["at_v2"].get("value_as_string"),
                     "latest_published": ag["pub"].get("value_as_string"), "latest_fetched": ag["fet"].get("value_as_string"), "es_call_ms": round(ms, 1)}
                v["token"] = ":".join(str(v[k]) for k in ("total", "with_v0", "with_v1", "with_v2", "max_seg_ver_v0", "max_seg_ver_v1", "max_seg_ver_v2", "last_seg_at_v0", "last_seg_at_v1", "last_seg_at_v2", "latest_fetched"))
            except HTTPException as e:
                v = {"ok": False, "error": e.detail, "token": "no-index"}
            except Exception as e:
                v = {"ok": False, "error": str(e)[:200], "token": "es-error"}
            _STATE.update(at=time.time(), v=v)
        return _STATE["v"]


def recent_posts(token, n=12):
    """最近進來的文章（fetched_at 最新）＋ 兩版詞表與各自的斷詞時間：串流 → 斷詞 → 可查的延遲一眼看到。token 沒變就不重查。"""
    if _RECENT["token"] == token:
        return _RECENT["v"]
    body = {"size": n, "query": {"match_all": {}}, "sort": [{"fetched_at": {"order": "desc", "missing": "_last"}}, {"published_at": {"order": "desc", "missing": "_last"}}],
            "_source": ["post_id", "channel", "sub_type", "source", "url", "published_at", "fetched_at", "content", "terms_v0", "terms_v1", "terms_v2",
                        "seg_at_v0", "seg_at_v1", "seg_at_v2", "dict_version_v0", "dict_version_v1", "dict_version_v2", "seg_ver_v2"]}
    try:
        res, _ = es("POST", f"/{INDEX}/_search?request_cache=false", body)
        out = []
        for h in res["hits"]["hits"]:
            s = h["_source"]
            s["content"] = (s.get("content") or "")[:160]
            s["terms_v0"] = (s.get("terms_v0") or [])[:14]
            s["terms_v1"] = (s.get("terms_v1") or [])[:14]
            s["terms_v2"] = (s.get("terms_v2") or [])[:14]
            out.append(s)
    except Exception:
        out = []
    _RECENT.update(token=token, v=out)
    return out


def dict_info():
    """字典 artifact：current 與版本清單。有掛 DICT_ROOT 就直接讀檔（API 也在這裡寫新版）；沒掛就只回 overlay 看到的版本。"""
    info = {"root_mounted": DICT_ROOT.exists(), "current": None, "versions": [], "overlay_version": dict_overlay().get("dict_version")}
    if DICT_ROOT.exists():
        try:
            info["current"] = ops.current_version(DICT_ROOT)
            info["versions"] = ops.list_versions(DICT_ROOT)[-8:]
        except Exception as e:
            info["error"] = str(e)[:200]
    return info


# ---------- 文字雲 ----------
def scope_query(q, channels, date_from, date_to):
    flt = []
    if channels:
        flt.append({"terms": {"channel": channels}})
    if date_from or date_to:
        rng = {}
        if date_from:
            rng["gte"] = date_from
        if date_to:
            rng["lte"] = date_to
        flt.append({"range": {"published_at": rng}})
    must = [{"match": {"content": {"query": q, "operator": "and"}}}] if q else []
    return {"bool": {"filter": flt, "must": must, "must_not": [{"exists": {"field": "deleted_at"}}]}}   # 已刪文章（tombstone）不進文字雲


def noise_of(term: str) -> str:
    if term in HARD_STOP:
        return "stopword"
    if NUM_RE.match(term):
        return "number"
    if SYMBOL_RE.match(term):
        return "symbol"
    if ASCII_MIX_RE.match(term):
        return "url_or_code"
    if len(term) == 1:
        return "single_char"
    return ""


def closed_range(date_from, date_to) -> bool:
    """日期範圍是否已結束（有 date_to 且早於今天 UTC）：已結束範圍的資料只會因回補分區完成或定向重跑而變。"""
    if not date_to:
        return False
    try:
        return datetime.strptime(date_to[:10], "%Y-%m-%d").date() < datetime.now(timezone.utc).date()
    except ValueError:
        return False


def generation():
    """發版世代號：定向重跑（rerun_runs）或回補分區（backfill_ledger）每完成一個就變，已結束日期範圍的快取跟著失效；每 5 秒最多查一次 PG。"""
    if time.time() - _GEN["at"] > GEN_TTL:
        rows = pg_rows("SELECT (SELECT count(*) FROM rerun_runs WHERE finished_at IS NOT NULL) AS runs, "
                       "(SELECT count(*) FROM backfill_ledger WHERE finished_at IS NOT NULL) AS parts")
        if rows:
            _GEN["v"] = f"{rows[0]['runs']}:{rows[0]['parts']}"
        _GEN["at"] = time.time()
    return _GEN["v"]


def cache_key(seg, q, channels, date_from, date_to, size, soft, merge, shard_size, dict_version, gen, token):
    """快取鍵：正規化查詢（去頭尾空白、連續空白壓成一格、渠道去重排序）＋範圍＋seg＋字典版本＋發版世代號；
    範圍含今天或沒有結束日才再加 ES 狀態 token（串流一寫入就失效，PM 頁才看得到即時變化）。"""
    parts = [seg, " ".join((q or "").split()), sorted(set(channels or [])), date_from or "", date_to or "", int(size),
             int(bool(soft)), int(bool(merge)), int(shard_size or 0), dict_version, gen]
    if not closed_range(date_from, date_to):
        parts.append(token)
    return json.dumps(parts, ensure_ascii=False)


def _cached(key):
    hit = CACHE.get(key)
    return hit if hit and time.time() < hit["exp"] else None


def wordcloud(seg, q, channels, date_from, date_to, size, soft, merge, shard_size=None, use_cache=True):
    rid = uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    ov = dict_overlay()
    t1 = time.perf_counter()
    st = es_state()
    t2 = time.perf_counter()
    excl = ov["soft"] if (soft and seg == "v2") else []
    closed = closed_range(date_from, date_to)
    key = cache_key(seg, q, channels, date_from, date_to, size, soft, merge, shard_size, ov["dict_version"], generation() if closed else "-", st.get("token"))
    hit = _cached(key) if use_cache else None
    if hit:
        out = dict(hit["out"], cache="hit", request_id=rid)
        out["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        DUR.labels(seg, "hit", "ok").observe(out["duration_ms"] / 1000)
        CACHE_EVENTS.labels(seg, "hit").inc()
        log(event="wordcloud_query", request_id=rid, seg_ver=seg, cache="hit", duration_ms=out["duration_ms"], es_call_ms=0, status="ok")
        return out
    # single-flight：同一個鍵同時有多個未命中，只有第一個去打 ES，其他等它算完直接用（PM 頁兩欄、重新整理、多個 PM 同時看）
    leader, ev = True, None
    if use_cache:
        with _FLIGHTS_LOCK:
            ev = _FLIGHTS.get(key)
            if ev is None:
                ev = _FLIGHTS[key] = threading.Event()
            else:
                leader = False
        if not leader:
            ev.wait(SLA_MS / 1000)
            hit = _cached(key)
            if hit:
                out = dict(hit["out"], cache="hit", request_id=rid, coalesced=True)
                out["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                DUR.labels(seg, "hit", "ok").observe(out["duration_ms"] / 1000)
                CACHE_EVENTS.labels(seg, "coalesced").inc()
                log(event="wordcloud_query", request_id=rid, seg_ver=seg, cache="hit", coalesced=True, duration_ms=out["duration_ms"], es_call_ms=0, status="ok")
                return out
            # 前一個算失敗了：自己算一次（不再登記，失敗就各自失敗）
    try:
        return _wordcloud_compute(rid, t0, t1, t2, ov, st, excl, closed, key, seg, q, channels, date_from, date_to, size, merge, shard_size)
    finally:
        if leader and ev is not None:   # 成功已寫進快取、失敗則沒有：等的人醒來自己判斷
            with _FLIGHTS_LOCK:
                _FLIGHTS.pop(key, None)
            ev.set()


def _wordcloud_compute(rid, t0, t1, t2, ov, st, excl, closed, key, seg, q, channels, date_from, date_to, size, merge, shard_size):
    CACHE_EVENTS.labels(seg, "miss").inc()
    PHASE.labels(seg, "overlay").observe(t1 - t0)
    PHASE.labels(seg, "state").observe(t2 - t1)
    terms_agg = {"field": f"terms_{seg}", "size": size}
    if excl:
        terms_agg["exclude"] = excl
    if shard_size:
        terms_agg["shard_size"] = shard_size
    body = {
        "size": 0, "track_total_hits": True, "timeout": ES_SEARCH_TIMEOUT,   # ES 自己的 timeout：到了回 timed_out=true（部分結果），下面當壞查詢
        "query": scope_query(q, channels, date_from, date_to),
        "aggs": {
            "cloud": {"terms": terms_agg},
            "versions": {"terms": {"field": f"dict_version_{seg}", "size": 10, "missing": "(未斷詞)"}},
            "channels": {"terms": {"field": "channel", "size": 10}},
        },
    }
    INFLIGHT.labels(seg).inc()
    try:
        res, es_ms = es("POST", f"/{INDEX}/_search?request_cache=false", body)
        if res.get("timed_out"):
            raise EsPartial(f"ES 查詢在 {ES_SEARCH_TIMEOUT} 內沒跑完，只回部分結果（took={res.get('took')} ms）")
    except Exception as e:
        # 失敗也記耗時與 over_180s：最慢的查詢（逾時）才不會只算在錯誤數裡而從耗時分布消失
        dur = (time.perf_counter() - t0) * 1000
        reason = "timeout" if isinstance(e, requests.exceptions.Timeout) else "partial" if isinstance(e, EsPartial) else "other"
        ERR.labels(seg, reason).inc()
        DUR.labels(seg, "miss", "error").observe(dur / 1000)
        if dur > SLA_MS:
            OVER.labels(seg).inc()
        log(severity="ERROR", event="wordcloud_query", request_id=rid, seg_ver=seg, cache="miss", status="error", error_reason=reason,
            duration_ms=round(dur, 1), error=str(e)[:300])
        if isinstance(e, HTTPException):
            raise
        if reason == "other":
            raise HTTPException(502, f"ES 查詢失敗（{type(e).__name__}）：{str(e)[:200]}")
        raise HTTPException(504, f"文字雲查詢超過 {SLA_MS // 1000} 秒（{reason}）：{str(e)[:200]}")
    finally:
        INFLIGHT.labels(seg).dec()
    t3 = time.perf_counter()
    PHASE.labels(seg, "es").observe(t3 - t2)
    buckets = [{"term": b["key"], "count": b["doc_count"], "noise": noise_of(b["key"])} for b in res["aggregations"]["cloud"]["buckets"]]
    if merge and seg == "v2" and ov["merges"]:
        acc = {}
        for b in buckets:
            t = ov["merges"].get(b["term"], b["term"])
            if t in acc:
                acc[t]["count"] += b["count"]
                acc[t]["merged_from"] = acc[t].get("merged_from", []) + [b["term"]]
            else:
                acc[t] = dict(b, term=t, **({"merged_from": [b["term"]]} if t != b["term"] else {}))
        buckets = sorted(acc.values(), key=lambda x: -x["count"])
    dur = (time.perf_counter() - t0) * 1000
    out = {
        "request_id": rid, "seg": seg, "cache": "miss", "state_token": st.get("token"),
        "total_docs": res["hits"]["total"]["value"],
        "es_took_ms": res["took"], "es_call_ms": round(es_ms, 1), "duration_ms": round(dur, 1),
        "doc_count_error_upper_bound": res["aggregations"]["cloud"].get("doc_count_error_upper_bound"),
        "sum_other_doc_count": res["aggregations"]["cloud"].get("sum_other_doc_count"),
        "dict_versions": {b["key"]: b["doc_count"] for b in res["aggregations"]["versions"]["buckets"]},
        "channels": {b["key"]: b["doc_count"] for b in res["aggregations"]["channels"]["buckets"]},
        "excluded_soft_stopwords": excl, "overlay_dict_version": ov["dict_version"],
        "buckets": buckets,
        "noise_in_top": {k: sum(1 for b in buckets if b["noise"] == k) for k in ("stopword", "symbol", "number", "url_or_code", "single_char")},
    }
    if len(CACHE) > 400:   # token 一變舊鍵就沒用了，別讓它們堆著
        for k in sorted(CACHE, key=lambda k: CACHE[k]["at"])[:200]:
            CACHE.pop(k, None)
    CACHE[key] = {"at": time.time(), "exp": time.time() + (CACHE_TTL_CLOSED_S if closed else CACHE_TTL_OPEN_S), "out": out}
    PHASE.labels(seg, "post").observe(time.perf_counter() - t3)
    DUR.labels(seg, "miss", "ok").observe(dur / 1000)
    ES_CALL.labels(seg).observe(es_ms / 1000)
    sev = "INFO"
    if dur > SLA_MS:
        OVER.labels(seg).inc()
        sev = "WARNING"
    log(severity=sev, event="wordcloud_query", request_id=rid, seg_ver=seg, cache="miss", q=q, channels=channels,
        total_docs=out["total_docs"], duration_ms=out["duration_ms"], es_call_ms=out["es_call_ms"], es_took_ms=res["took"], status="ok")
    return out


def _channels(ch: str):
    return [c for c in (ch or "").split(",") if c]


@app.get("/api/wordcloud")
def api_wordcloud(seg: str = "v2", q: str = "", channels: str = "", date_from: str = "", date_to: str = "",
                  size: int = Query(60, le=500), soft: int = 1, merge: int = 1, shard_size: int = 0, cache: int = 1):
    return wordcloud(seg, q, _channels(channels), date_from, date_to, size, bool(soft), bool(merge), shard_size or None, bool(cache))


@app.get("/api/compare")
def api_compare(a: str = "v0", b: str = "v2", q: str = "", channels: str = "", date_from: str = "", date_to: str = "", size: int = Query(60, le=300), soft: int = 1, merge: int = 1):
    """並排兩版文字雲。預設 a=v0（舊系統重現）vs b=v2（新流程）；軟停用詞與變體合併只對 v2 生效（舊流程不做）。"""
    if a not in ("v0", "v1", "v2") or b not in ("v0", "v1", "v2"):
        raise HTTPException(400, "a／b 只能是 v0／v1／v2")
    ch = _channels(channels)
    # 兩欄同時查：快取沒命中時，總耗時是較慢的那一欄，不是兩欄相加；合計超過上限就回 504（JSON），不讓連線一直掛著
    fa, fb = (COMPARE_POOL.submit(wordcloud, s, q, ch, date_from, date_to, size, bool(soft), bool(merge)) for s in (a, b))
    end = time.monotonic() + COMPARE_DEADLINE_S
    try:
        ra = fa.result(timeout=max(0.0, end - time.monotonic()))
        rb = fb.result(timeout=max(0.0, end - time.monotonic()))
    except FutureTimeout:
        raise HTTPException(504, f"兩欄文字雲 {COMPARE_DEADLINE_S:.0f} 秒內沒算完")
    ta = {x["term"]: (i + 1, x["count"]) for i, x in enumerate(ra["buckets"])}
    tb = {x["term"]: (i + 1, x["count"]) for i, x in enumerate(rb["buckets"])}
    rows = []
    for t in list(dict.fromkeys(list(tb) + list(ta))):
        rows.append({"term": t, "a_rank": ta.get(t, (None, None))[0], "a_count": ta.get(t, (None, None))[1],
                     "b_rank": tb.get(t, (None, None))[0], "b_count": tb.get(t, (None, None))[1], "noise": noise_of(t)})
    return {"a_seg": a, "b_seg": b, "a": ra, "b": rb, "diff": rows, "state_token": rb.get("state_token"),
            "only_a": [r for r in rows if r["b_rank"] is None][:30],
            "only_b": [r for r in rows if r["a_rank"] is None][:30],
            "same_scope_docs": [ra["total_docs"], rb["total_docs"]]}


@app.get("/api/posts")
def api_posts(term: str, seg: str = "v2", q: str = "", channels: str = "", date_from: str = "", date_to: str = "", size: int = Query(20, le=100)):
    qry = scope_query(q, _channels(channels), date_from, date_to)
    qry["bool"]["filter"].append({"term": {f"terms_{seg}": term}})
    body = {"size": size, "track_total_hits": True,  # 預設只數到 10,000，PM 會看到「10,000 篇」以為就是全部
            "query": qry, "sort": [{"published_at": {"order": "desc", "missing": "_last"}}],
            "_source": ["post_id", "channel", "source", "url", "published_at", "content", "terms_v0", "terms_v1", "terms_v2", "dict_version_v0", "dict_version_v2", "seg_ver_v2", "seg_at_v2"],
            "highlight": {"fields": {"content": {"fragment_size": 120, "number_of_fragments": 2,
                                                 "highlight_query": {"match_phrase": {"content.bigram": term}}},
                                     "content.bigram": {"fragment_size": 120, "number_of_fragments": 2}},
                          "pre_tags": ["<mark>"], "post_tags": ["</mark>"]}}
    res, ms = es("POST", f"/{INDEX}/_search", body)
    return {"term": term, "seg": seg, "total": res["hits"]["total"]["value"], "es_call_ms": round(ms, 1),
            "posts": [dict(h["_source"], highlight=(h.get("highlight", {}).get("content.bigram") or h.get("highlight", {}).get("content") or [])) for h in res["hits"]["hits"]]}


@app.get("/api/overview")
def api_overview():
    body = {"size": 0, "track_total_hits": True, "aggs": {
        "ch": {"terms": {"field": "channel", "size": 10}, "aggs": {
            "v0": {"filter": {"exists": {"field": "seg_ver_v0"}}},
            "v1": {"filter": {"exists": {"field": "seg_ver_v1"}}},
            "v2": {"filter": {"exists": {"field": "seg_ver_v2"}}},
            "dv": {"terms": {"field": "dict_version_v2", "size": 5}},
            "latest": {"max": {"field": "published_at"}}, "earliest": {"min": {"field": "published_at"}}}},
        "dv": {"terms": {"field": "dict_version_v2", "size": 10}},
        "maxver": {"max": {"field": "seg_ver_v2"}}}}
    res, ms = es("POST", f"/{INDEX}/_search", body)
    info, _ = es("GET", "/")
    ag = res["aggregations"]
    return {"es_version": info["version"]["number"], "total": res["hits"]["total"]["value"], "es_call_ms": round(ms, 1),
            "overlay": dict_overlay(), "max_seg_ver_v2": ag["maxver"]["value"], "v1_note": V1_NOTE, "v0_note": V0_NOTE,
            "dict_versions": {b["key"]: b["doc_count"] for b in ag["dv"]["buckets"]},
            "channels": [{"channel": b["key"], "docs": b["doc_count"], "v0": b["v0"]["doc_count"], "v1": b["v1"]["doc_count"], "v2": b["v2"]["doc_count"],
                          "dict_versions": {x["key"]: x["doc_count"] for x in b["dv"]["buckets"]},
                          "earliest": b["earliest"].get("value_as_string"), "latest": b["latest"].get("value_as_string")} for b in ag["ch"]["buckets"]]}


# ---------- 即時狀態（PM 頁每 2 秒讀一次） ----------
@app.get("/api/status")
def api_status(events: int = Query(30, le=200)):
    st = es_state()
    services = pg_rows("SELECT service, pipeline, dict_version, seg_ver, build, loaded_at, heartbeat_at, "
                       "extract(epoch FROM now() - heartbeat_at) AS age_sec FROM service_state ORDER BY pipeline, service")
    for s in services:
        s["alive"] = s["age_sec"] is not None and float(s["age_sec"]) < 60
    by_status = {r["status"]: r["n"] for r in pg_rows("SELECT status, count(*) AS n FROM backfill_ledger GROUP BY status")}
    attention = pg_rows("""SELECT partition_id, channel, month, status, raw_count, written, noop, errors, docs_per_sec, gates, finished_at, dict_version, worker
                           FROM backfill_ledger WHERE status IN ('awaiting_pm','failed','running') ORDER BY status, month DESC LIMIT 40""")
    control = pg_rows("SELECT max_docs_per_sec, ceiling_docs_per_sec, paused, reason, canary_baseline_ms, updated_at FROM backfill_control WHERE id=1")
    coll_ctl = pg_rows("SELECT run_now, paused, poll_sec, updated_at FROM collector_control WHERE id=1")
    return {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "token": st.get("token"), "es": st,
            "dict": dict_info(), "services": services, "v1_note": V1_NOTE, "v0_note": V0_NOTE,
            "runs": pg_rows("SELECT * FROM rerun_runs ORDER BY id DESC LIMIT 5"),
            "outbox_pending": (pg_rows("SELECT count(*) AS n FROM release_outbox WHERE status='pending'") or [{"n": 0}])[0]["n"],
            "alerts": api_alerts(24)["firing"],
            "ledger": {"by_status": by_status, "attention": attention},
            "control": control[0] if control else None,
            "collector": {"control": coll_ctl[0] if coll_ctl else None, "feeds": pg_rows("SELECT * FROM collector_state ORDER BY feed")},
            "events": pg_rows("SELECT id, at, kind, actor, detail FROM live_events ORDER BY id DESC LIMIT %s", (events,)),
            "recent": recent_posts(st.get("token"))}


@app.get("/api/ops/ledger")
def api_ledger(status: str = "", limit: int = Query(500, le=5000)):
    rows = pg_rows("""SELECT partition_id, channel, month, status, raw_count, checkpoint_line, written, noop, errors, docs_per_sec, attempts,
                      worker, started_at, finished_at, dict_version, target_seg_ver, gates FROM backfill_ledger
                      WHERE (%s = '' OR status = %s) ORDER BY month DESC, channel LIMIT %s""", (status, status, limit))
    return {"rows": rows, "by_status": {r["status"]: r["n"] for r in pg_rows("SELECT status, count(*) AS n FROM backfill_ledger GROUP BY status")}}


@app.get("/api/ops/runs")
def api_runs(limit: int = Query(20, le=200)):
    return {"runs": pg_rows("SELECT * FROM rerun_runs ORDER BY id DESC LIMIT %s", (limit,))}


def _actor(body) -> str:
    by = str(body.get("by") or "").strip()[:40]
    if not by:
        raise HTTPException(400, "要填操作人（by）")
    return by


@app.post("/api/ops/approve")
def api_approve(body: dict = Body(...)):
    """PM 抽查簽核（awaiting_pm → passed，簽核人與時間寫進 gates.G4），或人工豁免某個沒過的閘門（failed → passed，一定要寫說明）。
    與 `python -m qseg approve` 同一段 SQL。"""
    by = _actor(body)
    partition, note, waive = str(body.get("partition") or "").strip(), str(body.get("note") or "").strip(), body.get("waive")
    if not partition:
        raise HTTPException(400, "要指定分區（partition）")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if waive:
        if waive not in ("G1", "G2", "G3"):
            raise HTTPException(400, "只能豁免 G1／G2／G3")
        if not note:
            raise HTTPException(400, "豁免一定要寫說明（逐篇看過什麼）")
        w = json.dumps({"waived_by": by, "waived_at": now, "note": note}, ensure_ascii=False)
        n, _ = pg_exec("""UPDATE backfill_ledger SET status='passed', gates=jsonb_set(gates, %s, %s::jsonb)
                          WHERE partition_id=%s AND status='failed'""", ([waive, "waiver"], w, partition))
        if not n:
            raise HTTPException(409, f"{partition} 不在 failed 狀態，沒有可豁免的閘門")
        event("waive", by, partition=partition, gate=waive, note=note)
        return {"ok": True, "partition": partition, "status": "passed", "waived": waive}
    g4 = json.dumps({"pass": True, "pm_spot_check_required": True, "approved_by": by, "note": note, "approved_at": now}, ensure_ascii=False)
    n, _ = pg_exec("""UPDATE backfill_ledger SET status='passed', gates=jsonb_set(gates, '{G4}', %s::jsonb)
                      WHERE partition_id=%s AND status='awaiting_pm'""", (g4, partition))
    if not n:
        raise HTTPException(409, f"{partition} 不在 awaiting_pm 狀態")
    event("approve", by, partition=partition, note=note)
    return {"ok": True, "partition": partition, "status": "passed"}


@app.post("/api/ops/release")
def api_release(body: dict = Body(...)):
    """字典發版：current 版 + 新詞 → 下一版（逐檔 sha256、manifest）→ 原子切 current → rerun_runs 一列 → 控制訊息到 dict-releases。
    斷詞服務 v2 收到後重載字典、定向重跑含新詞字串的文章；進度在 /api/status 的 runs。"""
    by = _actor(body)
    try:
        words = ops.validate_words(body.get("words"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not DICT_ROOT.exists():
        raise HTTPException(503, f"API 沒掛字典 artifact 目錄 {DICT_ROOT}（docker-compose 的 api.volumes）")
    note = str(body.get("note") or "").strip()[:200]
    with RELEASE_LOCK:
        try:
            man = ops.build_release(DICT_ROOT, words, note, by)
        except (FileExistsError, FileNotFoundError, ValueError) as e:
            raise HTTPException(409, f"建版失敗：{e}")
        v = man["dict_version"]
        # 先寫 outbox 再切 current：切了之後就算控制訊息發不出去，outbox 會重送、斷詞服務也會輪詢 manifest 收斂
        _, row = pg_exec("INSERT INTO rerun_runs (dict_version, seg_ver, words, requested_by, status) VALUES (%s,%s,%s,%s,'queued') RETURNING id",
                         (v, ops.seg_ver("v2", v), [w for w, _, _ in words], by))
        run_id = row["id"]
        payload = {"action": "release", "dict_version": v, "words": [w for w, _, _ in words], "run_id": run_id, "requested_by": by}
        _, ob = pg_exec("INSERT INTO release_outbox (dict_version, run_id, payload) VALUES (%s,%s,%s) RETURNING id", (v, run_id, json.dumps(payload, ensure_ascii=False)))
        ops.switch_current(DICT_ROOT, v)
        _DICT["at"] = 0.0   # overlay 立刻重讀
        _GEN["at"] = 0.0    # 世代號下次查詢立刻重讀（重跑完成後已結束範圍的快取才會換）
        msg_id, pending = None, False
        try:
            msg_id = PUBSUB.publish(CONTROL_TOPIC, payload, dict_version=v, run_id=run_id)
            pg_exec("UPDATE release_outbox SET status='sent', sent_at=now(), attempts=1, message_id=%s WHERE id=%s", (msg_id, ob["id"]))
            OUTBOX_SENT.labels("sent").inc()
        except Exception as e:
            pending = True
            pg_exec("UPDATE release_outbox SET attempts=1, last_error=%s WHERE id=%s", (str(e)[:200], ob["id"]))
            OUTBOX_SENT.labels("failed").inc()
            log(severity="WARNING", event="dict_release_publish_failed", dict_version=v, run_id=run_id, outbox_id=ob["id"], error=str(e)[:200])
    left = pg_rows("SELECT count(*) AS n FROM release_outbox WHERE status='pending'")
    OUTBOX_PENDING.set(left[0]["n"] if left else 0)
    event("release", by, dict_version=v, extends=man["extends"], words=man["add_words"], run_id=run_id, note=note, outbox_pending=pending)
    log(event="dict_release", dict_version=v, words=man["add_words"], run_id=run_id, by=by, message_id=msg_id, outbox_pending=pending)
    return {"ok": True, "dict_version": v, "seg_ver": ops.seg_ver("v2", v), "extends": man["extends"], "add_words": man["add_words"],
            "run_id": run_id, "message_id": msg_id, "outbox_pending": pending}


def _parser_write(posts):
    lines = []
    for p in posts:
        lines.append(json.dumps({"update": {"_index": INDEX, "_id": p["post_id"], "retry_on_conflict": 3}}))
        lines.append(json.dumps({"doc": ops.base_doc(p), "doc_as_upsert": True}, ensure_ascii=False))
    r = S.post(f"{ES_URL}/_bulk", data=("\n".join(lines) + "\n").encode("utf-8"), headers={"Content-Type": "application/x-ndjson"}, timeout=120)
    r.raise_for_status()
    return sum(1 for it in r.json()["items"] if "error" in it["update"])


@app.post("/api/ops/replay")
def api_replay(body: dict = Body(...)):
    """重播 data/channels 的六渠道樣本（267 篇，2026-10-31 未登入公開頁抓的）：parser 寫本體 + 發 parsed-posts，
    seg-v1／seg-v2 各自斷詞。已經在 ES 裡且同版本的，斷詞服務會 noop（冪等）；回傳有幾篇是新的。"""
    by = _actor(body)
    chans = [c for c in (body.get("channels") or CHANNELS) if c in CHANNELS]
    limit = int(body.get("limit") or 0)
    posts = [p for p in ops.load_channel_samples(SAMPLES) if p["channel"] in chans]
    if limit > 0:
        posts = posts[-limit:]
    if not posts:
        raise HTTPException(404, f"找不到樣本（{SAMPLES}）")
    r = S.post(f"{ES_URL}/{INDEX}/_mget?_source=false", json={"ids": [p["post_id"] for p in posts]}, timeout=60)
    have = {d["_id"] for d in r.json().get("docs", []) if d.get("found")} if r.status_code == 200 else set()
    PUBSUB.ensure(PARSED_TOPIC)
    errors = _parser_write(posts)
    for p in posts:
        PUBSUB.publish(PARSED_TOPIC, p, channel=p["channel"], post_id=p["post_id"], replay="1")
    event("replay", by, channels=chans, published=len(posts), already_in_es=len(have), write_errors=errors)
    return {"ok": True, "published": len(posts), "already_in_es": len(have), "new": len(posts) - len(have), "write_errors": errors,
            "channels": chans, "post_ids": [p["post_id"] for p in posts][:30]}


@app.post("/api/ops/control")
def api_control(body: dict = Body(...)):
    """回補限速／暫停（backfill_control）：Job 每批前都讀，調整立刻生效。"""
    by = _actor(body)
    sets, params = [], []
    if body.get("paused") is not None:
        sets.append("paused=%s")
        params.append(bool(body["paused"]))
    if body.get("max_docs_per_sec") is not None:
        n = int(body["max_docs_per_sec"])
        if not 50 <= n <= 50000:
            raise HTTPException(400, "每秒篇數上限要在 50–50000")
        sets.append("max_docs_per_sec=%s")
        params.append(n)
        sets.append("ceiling_docs_per_sec=%s")   # PM 手動設的限速也是 G3 回升的上限
        params.append(n)
    if not sets:
        raise HTTPException(400, "沒有要改的欄位")
    reason = str(body.get("reason") or f"PM 頁操作（{by}）")[:200]
    pg_exec(f"UPDATE backfill_control SET {', '.join(sets)}, reason=%s, updated_at=now() WHERE id=1", (*params, reason))
    event("control", by, paused=body.get("paused"), max_docs_per_sec=body.get("max_docs_per_sec"), reason=reason)
    return {"ok": True, "control": (pg_rows("SELECT max_docs_per_sec, ceiling_docs_per_sec, paused, reason, canary_baseline_ms, updated_at FROM backfill_control WHERE id=1") or [None])[0]}


@app.post("/api/ops/collect")
def api_collect(body: dict = Body(...)):
    """採集器：立刻抓一次（run_now）、暫停、恢復。採集器每 3 秒讀一次 collector_control。"""
    by = _actor(body)
    action = body.get("action")
    if action == "run_now":
        pg_exec("UPDATE collector_control SET run_now=true, updated_at=now() WHERE id=1")
    elif action in ("pause", "resume"):
        pg_exec("UPDATE collector_control SET paused=%s, updated_at=now() WHERE id=1", (action == "pause",))
    else:
        raise HTTPException(400, "action 要是 run_now／pause／resume")
    event("collect_request", by, action=action)
    return {"ok": True, "action": action}


@app.post("/api/ops/alert")
def api_alert(body: dict = Body(...)):
    """Alertmanager 的 webhook 接收器（零成本通知管道）：每條告警寫一列 live_events（kind=alert），PM 頁的事件流與告警橫幅直接讀。
    payload 是 Alertmanager 的 webhook 格式：{status, receiver, alerts:[{status, labels, annotations, startsAt, endsAt, fingerprint}], …}"""
    alerts = body.get("alerts") or []
    if not isinstance(alerts, list):
        raise HTTPException(400, "alerts 要是陣列")
    n = 0
    for a in alerts[:200]:
        labels, ann = (a.get("labels") or {}), (a.get("annotations") or {})
        status = "resolved" if a.get("status") == "resolved" else "firing"
        ALERTS.labels(status).inc()
        event("alert", "alertmanager", status=status, alertname=labels.get("alertname"), severity=labels.get("severity"), summary=ann.get("summary"),
              labels=labels, starts_at=a.get("startsAt"), ends_at=a.get("endsAt"), fingerprint=a.get("fingerprint"), receiver=body.get("receiver"))
        n += 1
    log(event="alert_webhook", received=n, status=body.get("status"), receiver=body.get("receiver"))
    return {"ok": True, "received": n}


def rum_ingest(raw: bytes) -> dict:
    """解析 web/rum.js 送來的 beacon（sendBeacon 的 Content-Type 是 text/plain，所以自己 json.loads），只收秒數與計數，寫進 Prometheus 直方圖與計數器。
    壞資料（太大、不是 JSON、沒有 events）算一個 bad beacon；單筆事件不合格就跳過。"""
    if len(raw) > 65536:
        WEB_BEACONS.labels("bad").inc()
        raise HTTPException(413, "beacon 超過 64 KB")
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        WEB_BEACONS.labels("bad").inc()
        raise HTTPException(400, "beacon 不是 JSON")
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        WEB_BEACONS.labels("bad").inc()
        raise HTTPException(400, "events 要是陣列")
    n, skipped = 0, 0
    for e in events[:500]:
        if not isinstance(e, dict):
            skipped += 1
            continue
        m = e.get("metric")
        try:
            v = float(e.get("value"))
        except (TypeError, ValueError):
            skipped += 1
            continue
        seg = e.get("seg") if e.get("seg") in ("v0", "v1", "v2") else "other"
        if m in ("lcp", "inp", "ttfc") and 0 <= v < 3600:
            WEB_VITALS.labels(m).observe(v)
        elif m == "cls" and 0 <= v < 100:
            WEB_CLS.observe(v)
        elif m == "api" and 0 <= v < 3600:
            WEB_API.labels(e.get("path") if e.get("path") in WEB_PATHS else "other").observe(v)
        elif m == "error":
            WEB_ERRORS.labels("unhandledrejection" if e.get("kind") == "unhandledrejection" else "error").inc()
        elif m == "render":
            WEB_RENDERS.labels(seg).inc()
            try:
                WEB_DROPPED.labels(seg).inc(max(0, min(1000, int(e.get("dropped") or 0))))
            except (TypeError, ValueError):
                pass
        elif m == "mismatch":
            WEB_MISMATCH.labels(seg).inc()
        else:
            skipped += 1
            continue
        n += 1
    WEB_BEACONS.labels("ok").inc()
    return {"ok": True, "accepted": n, "skipped": skipped}


@app.post("/api/rum")
async def api_rum(request: Request):
    """PM 頁的 RUM beacon（web/rum.js）。"""
    out = rum_ingest(await request.body())
    log(event="rum_beacon", accepted=out["accepted"], skipped=out["skipped"])
    return out


SEVERITY_ORDER = {"page": 0, "warn": 1, "ticket": 2}
# Alertmanager 還在響的告警每 repeat_interval（pm-page 路由 1 小時）會再送一次 firing；Alertmanager 重啟會遺失記憶體裡的告警，
# resolved 永遠不會送來（端對端驗證：重啟後 SegServiceDown 在 PM 頁掛了一小時）。超過兩個週期加緩衝沒再收到就當已失聯、不再顯示。
ALERT_STALE_SECONDS = int(os.environ.get("ALERT_STALE_SECONDS", str(2 * 3600 + 10 * 60)))


def alert_is_stale(at) -> bool:
    return isinstance(at, datetime) and at.tzinfo is not None and (datetime.now(timezone.utc) - at).total_seconds() > ALERT_STALE_SECONDS


@app.get("/api/ops/alerts")
def api_alerts(hours: int = Query(24, le=720)):
    """還在響的告警：live_events 的 alert 事件（最近 N 小時）依 fingerprint 取最後一筆，status=firing 且最近 ALERT_STALE_SECONDS 內有再收到的
    就是還沒解除的。給 PM 頁橫幅。stale 是「最後一筆是 firing 但太久沒再收到」的條數（Alertmanager 重啟或 webhook 斷線）。"""
    rows = pg_rows("""SELECT DISTINCT ON (detail->>'fingerprint') at, detail FROM live_events
                      WHERE kind='alert' AND at > now() - make_interval(hours => %s) ORDER BY detail->>'fingerprint', id DESC""", (hours,))
    last_firing = [dict(r["detail"] or {}, at=r["at"]) for r in rows if (r["detail"] or {}).get("status") == "firing"]
    firing = [a for a in last_firing if not alert_is_stale(a["at"])]
    firing.sort(key=lambda a: (SEVERITY_ORDER.get(a.get("severity"), 3), str(a.get("at"))))
    return {"firing": firing, "seen": len(rows), "stale": len(last_firing) - len(firing), "hours": hours, "stale_after_seconds": ALERT_STALE_SECONDS}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


WEB = Path(__file__).resolve().parent / "web"
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.get("/")
def index():
    return FileResponse(str(WEB / "index.html"))
