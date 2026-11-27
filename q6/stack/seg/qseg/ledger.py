"""回補帳本：ES 索引 backfill_ledger，一篇＝一個「渠道 × 月」分區，_id＝partition_id（例如 ptt/2026-10）。

取代原本 PostgreSQL 的 backfill_ledger 表。只用標準庫＋requests，所以回補 Job（seg 映像）和 API（api 映像）共用同一份程式。

認領（claim）：
- 候選：status＝pending，或 running 但心跳超過 STALE_SEC（或沒有心跳）；attempts 還沒到上限；依 month 新到舊、channel 排序。
- 搶到：對那一篇做條件更新（if_seq_no＋if_primary_term，CAS）把 status 改成 running；搶輸了（409）就換下一篇。
- 渠道規則（同一渠道沒有任何 passed 時，同時只放行一篇）：ES 沒有交易，所以搶到之後再 refresh、重查一次；
  發現同渠道另一篇也在跑／等 PM／failed，就把自己退回原狀態。
  為什麼不會兩篇都留下：兩人都留下的話，後完成搶的那人的 refresh 一定看得到先搶的那篇（先搶的 CAS 在它 refresh 之前已完成），
  就會退回。最壞情況是兩人都退回（活性問題，靠隨機退避解）；不會有兩篇同時被保留。
"""
import datetime as dt
import json
import os
import random
import time
import urllib.parse

import requests

DEFAULT_INDEX = "backfill_ledger"
STALE_SEC = 120            # 心跳超過這麼久就視為死掉，可以被重領（與原本 PG 版本相同）
CLAIM_ROUNDS = 50          # 搶輸時最多重來幾輪（每輪都是 refresh＋重新搜尋）
TS_FIELDS = ("heartbeat_at", "started_at", "finished_at")

MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "partition_id": {"type": "keyword"},
            "channel": {"type": "keyword"},
            "month": {"type": "keyword"},
            "target_seg_ver": {"type": "long"},
            "dict_version": {"type": "keyword"},
            "status": {"type": "keyword"},          # pending/running/awaiting_pm/passed/failed
            "raw_count": {"type": "integer"},
            "raw_lines": {"type": "integer"},
            "checkpoint_line": {"type": "integer"},
            "written": {"type": "integer"},
            "noop": {"type": "integer"},
            "errors": {"type": "integer"},
            "attempts": {"type": "integer"},
            "worker": {"type": "keyword"},
            "heartbeat_at": {"type": "date"},
            "started_at": {"type": "date"},
            "finished_at": {"type": "date"},
            "docs_per_sec": {"type": "double"},
            "gates": {"type": "object", "enabled": False},   # 只存不搜，閘門明細整包放在 _source
        },
    },
}


class LedgerError(RuntimeError):
    pass


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def to_iso(v):
    if isinstance(v, dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=dt.timezone.utc)
        return v.isoformat(timespec="milliseconds")
    return v


def _from_source(src: dict, seq, pt) -> dict:
    row = dict(src)
    for k in TS_FIELDS:
        v = row.get(k)
        if isinstance(v, str):
            row[k] = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))   # 呼叫端（job 的 fresh_snapshot 等）拿到的是 aware datetime
    row["_seq_no"], row["_primary_term"] = seq, pt
    return row


def waive_plan(gates, gate: str, waiver: dict, channel_passed: bool):
    """人工豁免的狀態機（純函式，job.py 與 api 共用；與原 PG 版 WAIVE_SQL 的三條規則一致）。回傳 (新 status, 新 gates)。
    1. 豁免後 G1–G3 還有別的閘門沒過、又沒豁免 → 留在 failed
    2. 否則 G4 沒有記成已過（還沒簽、或根本沒算到）、或這個渠道還沒有任何 passed → awaiting_pm（PM 簽完才 passed）
    3. 其餘 → passed"""
    gates = dict(gates or {})
    blocking = any(
        k in ("G1", "G2", "G3") and k != gate and isinstance(v, dict) and v.get("pass") is False and "waiver" not in v
        for k, v in gates.items())
    g4_ok = isinstance(gates.get("G4"), dict) and gates["G4"].get("pass") is True
    if blocking:
        status = "failed"
    elif not g4_ok or not channel_passed:
        status = "awaiting_pm"
    else:
        status = "passed"
    new = dict(gates)
    new[gate] = {**(gates.get(gate) if isinstance(gates.get(gate), dict) else {}), "waiver": waiver}
    return status, new


PROGRESS_SRC = """ctx._source.heartbeat_at = params.t;
ctx._source.checkpoint_line = params.cp;
ctx._source.written = (ctx._source.written ?: 0) + params.w;
ctx._source.noop = (ctx._source.noop ?: 0) + params.n;
ctx._source.errors = (ctx._source.errors ?: 0) + params.e;"""
HEARTBEAT_SRC = "if (ctx._source.status == 'running') { ctx._source.heartbeat_at = params.t; } else { ctx.op = 'noop'; }"


class Ledger:
    def __init__(self, url: str = None, index: str = DEFAULT_INDEX, timeout: int = 60):
        self.url = (url or os.environ.get("ES_URL", "http://es:9200")).rstrip("/")
        self.index = index
        self.timeout = timeout
        self.s = requests.Session()

    # ---- HTTP ----
    def raw(self, method, path, body=None, data: bytes = None):
        """回傳 (HTTP 狀態碼, JSON)；不因 4xx/5xx 拋例外（409 CAS 衝突要自己判斷）。"""
        if data is not None:
            r = self.s.request(method, self.url + path, data=data, headers={"Content-Type": "application/x-ndjson"}, timeout=self.timeout)
        else:
            r = self.s.request(method, self.url + path, json=body, timeout=self.timeout)
        try:
            out = r.json() if r.content else {}
        except ValueError:
            out = {"text": r.text[:300]}
        return r.status_code, out

    def req(self, method, path, body=None):
        status, out = self.raw(method, path, body)
        if status >= 400:
            raise LedgerError(f"{method} {path} -> {status}: {str(out)[:300]}")
        return out

    def _id(self, pid: str) -> str:
        return urllib.parse.quote(pid, safe="")   # partition_id 含 "/"，要編碼

    def ensure_index(self) -> bool:
        """建立索引（已存在就不動）。回傳是否真的新建。"""
        status, out = self.raw("PUT", f"/{self.index}", MAPPING)
        if status == 400 and "resource_already_exists_exception" in str(out):
            return False
        if status >= 400:
            raise LedgerError(f"create {self.index} -> {status}: {str(out)[:300]}")
        return True

    def refresh(self):
        self.req("POST", f"/{self.index}/_refresh")

    def count(self, query) -> int:
        return self.req("POST", f"/{self.index}/_count", {"query": query})["count"]

    def search(self, query, size: int = 1000, sort=None) -> list:
        body = {"size": size, "query": query, "seq_no_primary_term": True,
                "sort": sort or [{"month": "desc"}, {"channel": "asc"}]}
        hits = self.req("POST", f"/{self.index}/_search", body)["hits"]["hits"]
        return [_from_source(h["_source"], h["_seq_no"], h["_primary_term"]) for h in hits]

    def get(self, pid: str):
        status, out = self.raw("GET", f"/{self.index}/_doc/{self._id(pid)}")
        if status == 404:
            return None
        if status >= 400:
            raise LedgerError(f"get {pid} -> {status}: {str(out)[:300]}")
        return _from_source(out["_source"], out["_seq_no"], out["_primary_term"])

    def all_rows(self) -> list:
        self.refresh()
        return self.search({"match_all": {}}, size=10000, sort=[{"month": "desc"}, {"channel": "asc"}])

    def counts_by_status(self) -> dict:
        self.refresh()
        body = {"size": 0, "aggs": {"s": {"terms": {"field": "status", "size": 50}}}}
        buckets = self.req("POST", f"/{self.index}/_search", body)["aggregations"]["s"]["buckets"]
        return {b["key"]: b["doc_count"] for b in buckets}

    # ---- 寫入 ----
    def create_many(self, docs: list) -> dict:
        """plan 用：op_type=create，已存在的 partition_id 不動（回傳 exists 數）。"""
        lines = []
        for d in docs:
            lines.append(json.dumps({"create": {"_index": self.index, "_id": d["partition_id"]}}))
            lines.append(json.dumps({k: to_iso(v) for k, v in d.items()}, ensure_ascii=False))
        status, out = self.raw("POST", "/_bulk?refresh=wait_for", data=("\n".join(lines) + "\n").encode("utf-8"))
        if status >= 400:
            raise LedgerError(f"bulk create -> {status}: {str(out)[:300]}")
        res = {"created": 0, "exists": 0}
        for it in out["items"]:
            st = it["create"]["status"]
            if st == 201:
                res["created"] += 1
            elif st == 409:
                res["exists"] += 1
            else:
                raise LedgerError(f"bulk create item: {str(it['create'])[:300]}")
        return res

    def cas(self, pid: str, seq_no, primary_term, fields: dict):
        """條件更新：seq_no／primary_term 對不上（別人先改了）回傳 None，否則回傳新的 (seq, primary_term)。status 轉換一律 refresh=wait_for。"""
        body = {"doc": {k: to_iso(v) for k, v in fields.items()}}
        path = (f"/{self.index}/_update/{self._id(pid)}?if_seq_no={seq_no}&if_primary_term={primary_term}"
                "&refresh=wait_for")
        status, out = self.raw("POST", path, body)
        if status == 409:
            return None
        if status >= 400:
            raise LedgerError(f"cas {pid} -> {status}: {str(out)[:300]}")
        return {"_seq_no": out["_seq_no"], "_primary_term": out["_primary_term"]}

    def set(self, pid: str, fields: dict, refresh: bool = False):
        """無條件部分更新（只給「已經是本人持有」的分區用，例如 run_partition 寫 raw_count、完成時寫 status）。"""
        path = f"/{self.index}/_update/{self._id(pid)}" + ("?refresh=wait_for" if refresh else "")
        self.req("POST", path, {"doc": {k: to_iso(v) for k, v in fields.items()}})

    def progress(self, pid: str, checkpoint_line: int, written: int = 0, noop: int = 0, errors: int = 0, now: str = None):
        """每批寫一次：checkpoint 與計數用腳本累加（同一分區只有持有者會寫，不需要 CAS）。"""
        self.req("POST", f"/{self.index}/_update/{self._id(pid)}", {"script": {
            "lang": "painless", "source": PROGRESS_SRC,
            "params": {"t": now or now_iso(), "cp": int(checkpoint_line), "w": int(written), "n": int(noop), "e": int(errors)}}})

    def heartbeat(self, pid: str) -> bool:
        """只有 status 還是 running 才更新心跳（被別人接手或已結束就不動）。"""
        status, out = self.raw("POST", f"/{self.index}/_update/{self._id(pid)}", {"script": {
            "lang": "painless", "source": HEARTBEAT_SRC, "params": {"t": now_iso()}}})
        return status < 400 and out.get("result") == "updated"

    # ---- 查詢（給 throttle、閘門、燃盡用）----
    def running_count(self, fresh_sec: int = 30) -> int:
        self.refresh()
        return self.count({"bool": {"filter": [{"term": {"status": "running"}},
                                               {"range": {"heartbeat_at": {"gt": f"now-{int(fresh_sec)}s"}}}]}})

    def channel_has_passed(self, channel: str) -> bool:
        self.refresh()
        return self.count({"bool": {"filter": [{"term": {"channel": channel}}, {"term": {"status": "passed"}}]}}) > 0

    def channel_open(self, channel: str, exclude_pid: str) -> bool:
        """這個渠道現在可以再放行一篇嗎：有 passed 就可以；否則要沒有別篇（不含自己）在 running／awaiting_pm／failed。"""
        if self.channel_has_passed(channel):
            return True
        busy = {"bool": {"filter": [{"term": {"channel": channel}}, {"terms": {"status": ["running", "awaiting_pm", "failed"]}}],
                         "must_not": [{"term": {"partition_id": exclude_pid}}]}}
        return self.count(busy) == 0

    @staticmethod
    def _claimable_clause(stale_sec: int):
        return {"bool": {"should": [
            {"term": {"status": "pending"}},
            {"bool": {"filter": [{"term": {"status": "running"}}], "should": [
                {"bool": {"must_not": [{"exists": {"field": "heartbeat_at"}}]}},
                {"range": {"heartbeat_at": {"lt": f"now-{int(stale_sec)}s"}}}], "minimum_should_match": 1}}],
            "minimum_should_match": 1}}

    def _claimable_query(self, max_attempts: int, stale_sec: int):
        return {"bool": {"filter": [{"range": {"attempts": {"lt": max_attempts}}}, self._claimable_clause(stale_sec)]}}

    def _exhausted_query(self, max_attempts: int, stale_sec: int):
        return {"bool": {"filter": [{"range": {"attempts": {"gte": max_attempts}}}, self._claimable_clause(stale_sec)]}}

    # ---- 狀態轉換 ----
    def _take(self, d: dict, worker: str, now: str):
        fields = {"status": "running", "worker": worker, "attempts": (d.get("attempts") or 0) + 1,
                  "started_at": d.get("started_at") or now, "heartbeat_at": now}
        out = self.cas(d["partition_id"], d["_seq_no"], d["_primary_term"], fields)
        if out is None:
            return None
        return {**d, **fields, **out}

    def _give_back(self, orig: dict, claimed: dict) -> bool:
        fields = {k: orig.get(k) for k in ("status", "worker", "attempts", "started_at", "heartbeat_at")}
        return self.cas(orig["partition_id"], claimed["_seq_no"], claimed["_primary_term"], fields) is not None

    def claim(self, max_attempts: int, worker: str, stale_sec: int = STALE_SEC, partition_id: str = None):
        """領一篇。partition_id 指定時不看狀態、不看渠道（與原本 --partition 相同，用來重跑）。回傳領到的列，沒有就 None。"""
        if partition_id:
            d = self.get(partition_id)
            return self._take(d, worker, now_iso()) if d else None
        for _ in range(CLAIM_ROUNDS):
            self.refresh()
            cands = self.search(self._claimable_query(max_attempts, stale_sec), size=1000)
            if not cands:
                return None
            contended = False
            for d in cands:
                pid, ch = d["partition_id"], d["channel"]
                if not self.channel_open(ch, pid):
                    continue
                took = self._take(d, worker, now_iso())
                if took is None:            # 別人先搶到了
                    contended = True
                    continue
                if self.channel_open(ch, pid):   # refresh 後重查：同渠道沒有別人也在跑
                    return took
                contended = True            # 同渠道另一篇也在跑：退回，換下一篇
                # 退回失敗（別人已改過）：不再動它；它的心跳不會更新，超過 STALE_SEC 後會被其他人回收
                self._give_back(d, took)
            if not contended:
                return None                 # 候選都被渠道規則擋住：和原本 PG 版一樣，回傳沒有
            time.sleep(random.uniform(0.01, 0.05))
        return None

    def exhaust(self, max_attempts: int, stale_sec: int = STALE_SEC) -> list:
        """attempts 已達上限仍沒做完（pending，或 running 但心跳過期）→ failed，不再自動重領。回傳 [(partition_id, attempts)]。"""
        self.refresh()
        out = []
        for d in self.search(self._exhausted_query(max_attempts, stale_sec), size=1000):
            at = now_iso()
            gates = {**(d.get("gates") or {}), "attempts_exhausted": {"attempts": d["attempts"], "at": at}}
            if self.cas(d["partition_id"], d["_seq_no"], d["_primary_term"],
                        {"status": "failed", "finished_at": at, "gates": gates}) is not None:
                out.append((d["partition_id"], d["attempts"]))
        return out

    def waive(self, pid: str, gate: str, waiver: dict):
        """failed → 依 waive_plan 轉換。回傳新 status；不是 failed 或被別人改過就回傳 None。"""
        d = self.get(pid)
        if d is None or d.get("status") != "failed":
            return None
        status, gates = waive_plan(d.get("gates"), gate, waiver, self.channel_has_passed(d["channel"]))
        out = self.cas(pid, d["_seq_no"], d["_primary_term"], {"status": status, "gates": gates, "finished_at": now_iso()})
        return status if out is not None else None

    def approve(self, pid: str, g4: dict) -> bool:
        """awaiting_pm → passed，G4 整段換成簽核紀錄。"""
        d = self.get(pid)
        if d is None or d.get("status") != "awaiting_pm":
            return False
        gates = {**(d.get("gates") or {}), "G4": g4}
        return self.cas(pid, d["_seq_no"], d["_primary_term"], {"status": "passed", "gates": gates}) is not None
