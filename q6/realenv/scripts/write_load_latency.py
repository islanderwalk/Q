"""寫入壓力對文字雲查詢延遲的影響（只看 ES：寫入用 _update_by_query 在 ES 內部做部分更新，
每篇都會整篇重寫一次，等同回補的寫入成本，但不含斷詞的 CPU；GCP 上斷詞在 Cloud Run，不跟 ES 搶 CPU）。

階段：閒置 → 每秒 1,000 篇 → 每秒 3,000 篇（答案的限速）→ 不限速 → 閒置；每段 30 秒，同時持續打 API（cache=0）。
用法：python write_load_latency.py <輸出.json>
"""
import json
import statistics
import sys
import threading
import time
import urllib.parse

import requests

ES, API = "http://localhost:9200", "http://localhost:8080/api/wordcloud"
S = requests.Session()
SCOPES = [{"seg": "v2"}, {"seg": "v2", "date_from": "2025-10-30"}, {"seg": "v2", "date_from": "2026-10-01"}]


def probe(stop, samples):
    i = 0
    while not stop.is_set():
        sc = SCOPES[i % len(SCOPES)]
        i += 1
        t0 = time.perf_counter()
        r = S.get(f"{API}?" + urllib.parse.urlencode({**sc, "cache": 0, "size": 60}), timeout=120).json()
        samples.append({"t": time.time(), "scope": "全期" if len(sc) == 1 else ("近一年" if sc["date_from"] < "2026" else "近一個月"),
                        "ms": (time.perf_counter() - t0) * 1000, "es_took": r["es_took_ms"]})


def task_status(tid):
    return S.get(f"{ES}/_tasks/{tid}").json()


def main():
    out = sys.argv[1]
    phases, samples = [], []
    stop = threading.Event()
    th = threading.Thread(target=probe, args=(stop, samples))
    th.start()

    def phase(name, secs, tid=None):
        t0 = time.time()
        st0 = task_status(tid)["task"]["status"] if tid else None
        time.sleep(secs)
        st1 = task_status(tid)["task"]["status"] if tid else None
        done = (st1["updated"] - st0["updated"]) if tid else 0
        phases.append({"phase": name, "start": t0, "end": time.time(), "docs_written": done, "docs_per_sec": round(done / (time.time() - t0)) if tid else 0})

    phase("閒置", 30)
    body = {"query": {"match_all": {}}, "script": {"lang": "painless", "source": "ctx._source.seg_at_v1 = ctx._source.seg_at_v1"}}
    tid = S.post(f"{ES}/posts/_update_by_query?wait_for_completion=false&conflicts=proceed&requests_per_second=1000&scroll_size=500",
                 json=body).json()["task"]
    phase("寫入每秒 1,000 篇", 30, tid)
    S.post(f"{ES}/_update_by_query/{tid}/_rethrottle?requests_per_second=3000")
    phase("寫入每秒 3,000 篇", 30, tid)
    S.post(f"{ES}/_update_by_query/{tid}/_rethrottle?requests_per_second=-1")
    phase("寫入不限速", 20, tid)
    S.post(f"{ES}/_tasks/{tid}/_cancel")
    time.sleep(3)
    phase("閒置（寫完）", 30)
    stop.set()
    th.join()
    res = []
    for p in phases:
        xs = [s for s in samples if p["start"] <= s["t"] < p["end"]]
        row = {**p, "queries": len(xs)}
        for sc in ("全期", "近一年", "近一個月"):
            v = sorted(s["ms"] for s in xs if s["scope"] == sc)
            if v:
                row[sc] = {"n": len(v), "p50_ms": round(statistics.median(v), 1), "p95_ms": round(v[max(0, int(len(v) * 0.95) - 1)], 1)}
        res.append(row)
    json.dump({"note": __doc__.strip().splitlines()[0], "phases": res}, open(out, "w"), ensure_ascii=False, indent=2)
    for r in res:
        print(r["phase"], r["docs_per_sec"], {k: r.get(k) for k in ("全期", "近一年", "近一個月")})


if __name__ == "__main__":
    main()
