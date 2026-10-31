"""ES 寫入：條件更新（seg_ver 只能前進）＋ scripted upsert。

- 同一篇文章、同一版本重送 → noop（重試安全、冪等）
- 舊版本晚到（例如回補用舊字典、串流已用新字典）→ noop，不會倒退
- 文章還沒被 parser 寫進來也沒關係：scripted_upsert 用 base_doc 建出文件，之後 parser 的部分更新不會蓋掉斷詞欄位
- 只動 terms_<f> 這一組欄位，另一版（例如 terms_v1）原封不動，PM 才能 A/B 並排
- force=True（修程式、字典不變而要重跑某分區）：同版本可以覆寫，但仍不准倒退
"""
import json
import time

import requests

from . import config

GUARD_SCRIPT = """
String f = params.f;
def cur = ctx._source['seg_ver_' + f];
boolean force = params.containsKey('force') && params.force;
if (cur != null && (force ? cur > params.seg_ver : cur >= params.seg_ver)) { ctx.op = 'noop'; return; }
for (entry in params.base.entrySet()) {
  if (!ctx._source.containsKey(entry.getKey()) || ctx._source[entry.getKey()] == null) { ctx._source[entry.getKey()] = entry.getValue(); }
}
for (entry in params.seg.entrySet()) { ctx._source[entry.getKey()] = entry.getValue(); }
"""


class ES:
    def __init__(self, url: str = None, index: str = None):
        self.url = (url or config.ES_URL).rstrip("/")
        self.index = index or config.INDEX
        self.s = requests.Session()

    def req(self, method, path, body=None, **kw):
        r = self.s.request(method, f"{self.url}{path}", json=body, timeout=kw.pop("timeout", 120), **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else {}

    def put_guard_script(self):
        return self.req("PUT", "/_scripts/seg_guard", {"script": {"lang": "painless", "source": GUARD_SCRIPT}})

    def bulk_seg(self, items, refresh=False, force=False):
        """items: [(post_id, base_doc, seg_fields, pipeline)]. 回傳 {created, updated, noop, errors, rejected, took_ms}"""
        lines = []
        for pid, base, seg, f in items:
            ver = seg[f"seg_ver_{f}"]
            lines.append(json.dumps({"update": {"_index": self.index, "_id": pid, "retry_on_conflict": 3}}))
            lines.append(json.dumps({
                "scripted_upsert": True,
                "script": {"id": "seg_guard", "params": {"f": f, "seg_ver": ver, "base": base, "seg": seg, "force": force}},
                "upsert": {},
            }, ensure_ascii=False))
        body = ("\n".join(lines) + "\n").encode("utf-8")
        t0 = time.perf_counter()
        r = self.s.post(f"{self.url}/_bulk" + ("?refresh=wait_for" if refresh else ""), data=body,
                        headers={"Content-Type": "application/x-ndjson"}, timeout=300)
        took = (time.perf_counter() - t0) * 1000
        r.raise_for_status()
        res = r.json()
        out = {"created": 0, "updated": 0, "noop": 0, "errors": 0, "rejected": 0, "took_ms": round(took, 1), "failed_ids": []}
        for it in res["items"]:
            u = it["update"]
            if "error" in u:
                out["errors"] += 1
                out["failed_ids"].append(u["_id"])
                if u.get("status") == 429:
                    out["rejected"] += 1
            else:
                out[u["result"] if u["result"] in out else "updated"] += 1
        return out

    def upsert_base(self, items, refresh=False):
        """parser／既有索引器那一段：只寫文章本體欄位（部分更新＋doc_as_upsert），不碰任何斷詞欄位。"""
        lines = []
        for pid, base in items:
            lines.append(json.dumps({"update": {"_index": self.index, "_id": pid, "retry_on_conflict": 3}}))
            lines.append(json.dumps({"doc": base, "doc_as_upsert": True}, ensure_ascii=False))
        r = self.s.post(f"{self.url}/_bulk" + ("?refresh=wait_for" if refresh else ""),
                        data=("\n".join(lines) + "\n").encode("utf-8"),
                        headers={"Content-Type": "application/x-ndjson"}, timeout=300)
        r.raise_for_status()
        return r.json()

    def count(self, query):
        return self.req("POST", f"/{self.index}/_count", {"query": query})["count"]

    def write_rejected_total(self) -> int:
        st = self.req("GET", "/_nodes/stats/thread_pool")
        return sum(n["thread_pool"]["write"]["rejected"] for n in st["nodes"].values())
