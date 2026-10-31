"""把背景延遲探針（latency_probe.jsonl）依 phases.jsonl 的階段切開，算每段每種範圍的 p50／p95。
用法：python summarize_probe.py <latency_probe.jsonl> <phases.jsonl> <輸出.json>"""
import json
import statistics
import sys

probe = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
phases = [json.loads(l) for l in open(sys.argv[2], encoding="utf-8")]
phases.append({"ts": float("inf"), "phase": "end"})
out = []
for a, b in zip(phases, phases[1:]):
    xs = [p for p in probe if a["ts"] <= p["ts"] < b["ts"]]
    if not xs:
        continue
    row = {"phase": a["phase"], "seconds": round(min(b["ts"], xs[-1]["ts"]) - a["ts"], 1), "queries": len(xs)}
    for sc in sorted({p["scope"] for p in xs}):
        v = sorted(p["wall_ms"] for p in xs if p["scope"] == sc)
        row[sc] = {"n": len(v), "p50_ms": round(statistics.median(v), 1), "p95_ms": round(v[max(0, int(len(v) * 0.95) - 1)], 1)}
    out.append(row)
first = probe[0]["ts"]
idle = [p for p in probe if p["ts"] < float(json.loads(open(sys.argv[2]).readline())["ts"])]
if idle:
    row = {"phase": "idle_baseline_before_any_backfill", "seconds": round(idle[-1]["ts"] - first, 1), "queries": len(idle)}
    for sc in sorted({p["scope"] for p in idle}):
        v = sorted(p["wall_ms"] for p in idle if p["scope"] == sc)
        row[sc] = {"n": len(v), "p50_ms": round(statistics.median(v), 1), "p95_ms": round(v[max(0, int(len(v) * 0.95) - 1)], 1)}
    out.insert(0, row)
json.dump(out, open(sys.argv[3], "w"), ensure_ascii=False, indent=2)
for r in out:
    print(r["phase"], r["seconds"], {k: (v["p50_ms"], v["p95_ms"]) for k, v in r.items() if isinstance(v, dict)})
