"""測試用的假 ES：只實作回補會用到的 _bulk 行為，不執行 painless。

行為依 elastic.co 8.19 文件（網址見 q5/code/RESULTS.md）：
- update 動作：文件不存在回 404 document_missing_exception；腳本以 Python 模擬 backfill.GUARD_SCRIPT 的邏輯
  （逐欄比較 counts_at.<欄位>：線上一樣新或更新就不動那一欄，否則寫入值與時間戳；一欄都沒寫就 noop；
  線上時間戳不是數字時回 400）。noop 或錯誤時文件不變，所以先在副本上改。
- index 動作＋version_type=external：給的版本小於或等於現有版本時回 409。
- 可注入單筆 429（es_rejected_execution_exception）、整個請求 HTTP 429，以及單筆 400。
這只能驗證送出端的流程與冪等性，painless 腳本本身要在真的 ES 上驗證。
"""
import copy
import json


class FakeES:
    def __init__(self, docs=None, reject_items_once=(), http_429_times=0, bad_ids=()):
        self.docs = docs if docs is not None else {}  # (index, _id) -> {"_source": dict, "_version": int}
        self.reject_items_once = set(reject_items_once)  # 第一次看到這些 _id 時回單筆 429
        self.http_429_times = http_429_times  # 前幾個請求整個回 HTTP 429
        self.bad_ids = set(bad_ids)  # 這些 _id 回 400（模擬 mapping 錯誤）
        self.requests = 0

    def _update(self, meta, body):
        key = (meta["_index"], meta["_id"])
        cur = self.docs.get(key)
        if cur is None:
            return 404, {"error": {"type": "document_missing_exception"}}
        p = body["script"]["params"]
        src = copy.deepcopy(cur["_source"])
        target = src
        if p["obj"] is not None:
            if not isinstance(src.get(p["obj"]), dict):
                src[p["obj"]] = {}
            target = src[p["obj"]]
        if not isinstance(src.get("counts_at"), dict):
            src["counts_at"] = {}
        at, changed = src["counts_at"], False
        for f, value in p["values"].items():
            stamp = at.get(f)
            if stamp is not None and (isinstance(stamp, bool) or not isinstance(stamp, (int, float))):
                return 400, {"error": {"type": "illegal_argument_exception",
                                       "reason": f"counts_at.{f} is not epoch_millis"}}
            if stamp is None or stamp < p["at"][f]:
                target[f] = value
                at[f] = p["at"][f]
                changed = True
        if not changed:
            return 200, {"result": "noop"}
        cur["_source"] = src
        cur["_version"] += 1
        return 200, {"result": "updated"}

    def _index(self, meta, body):
        key = (meta["_index"], meta["_id"])
        cur = self.docs.get(key)
        if cur is not None and meta["version"] <= cur["_version"]:
            return 409, {"error": {"type": "version_conflict_engine_exception"}}
        self.docs[key] = {"_source": json.loads(json.dumps(body)), "_version": meta["version"]}
        return (200, {"result": "updated"}) if cur else (201, {"result": "created"})

    def bulk(self, payload: bytes):
        """回傳 (HTTP 狀態, 回應 dict)。"""
        self.requests += 1
        if self.http_429_times > 0:
            self.http_429_times -= 1
            return 429, {"error": {"type": "es_rejected_execution_exception"}}
        lines = payload.decode("utf-8").splitlines()
        items = []
        for i in range(0, len(lines), 2):
            action = json.loads(lines[i])
            (op, meta), = action.items()
            body = json.loads(lines[i + 1])
            if meta["_id"] in self.reject_items_once:
                self.reject_items_once.discard(meta["_id"])
                status, extra = 429, {"error": {"type": "es_rejected_execution_exception"}}
            elif meta["_id"] in self.bad_ids:
                status, extra = 400, {"error": {"type": "document_parsing_exception"}}
            elif op == "update":
                status, extra = self._update(meta, body)
            else:
                status, extra = self._index(meta, body)
            items.append({op: {"_index": meta["_index"], "_id": meta["_id"], "status": status, **extra}})
        return 200, {"errors": any(next(iter(it.values()))["status"] >= 300 for it in items), "items": items}


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def poster(es: FakeES, calls=None):
    """給 backfill.send_bulk_files 注入的 post 函式。"""
    def post(url, data, headers):
        if calls is not None:
            calls.append(url)
        return Resp(*es.bulk(data))
    return post
