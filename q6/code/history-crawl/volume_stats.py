"""從本機爬蟲資料算：每渠道每篇字數、每月篇數（2023-01 起）。"""
import json, sys, statistics, collections, pathlib
R = pathlib.Path(sys.argv[1])
out = {}

def scan(path, ch_of, text_of):
    seen = set()
    per = collections.defaultdict(lambda: {"n": 0, "chars": [], "months": collections.Counter(), "min": None, "max": None})
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            pid = r.get("post_id") or r.get("url")
            if pid in seen:
                continue
            seen.add(pid)
            p = r.get("published_at") or ""
            if p[:7] < "2023-01":
                continue
            k = ch_of(r)
            d = per[k]
            d["n"] += 1
            t = text_of(r)
            if t is not None:
                d["chars"].append(len(t))
            d["months"][p[:7]] += 1
            d["min"] = p[:10] if d["min"] is None or p[:10] < d["min"] else d["min"]
            d["max"] = p[:10] if d["max"] is None or p[:10] > d["max"] else d["max"]
    return per

def summ(per):
    res = {}
    for k, d in per.items():
        c = d["chars"]
        full = sorted(d["months"].items())
        # 完整月份（去掉頭尾不完整月）
        mid = [v for m, v in full[1:-1]] if len(full) > 2 else [v for m, v in full]
        res[k] = {"posts": d["n"], "span": f'{d["min"]}~{d["max"]}', "months": len(full),
                  "chars_mean": round(statistics.mean(c)) if c else None,
                  "chars_median": round(statistics.median(c)) if c else None,
                  "per_full_month_median": round(statistics.median(mid)) if mid else None,
                  "per_full_month_max": max(mid) if mid else None,
                  "per_full_month_min": min(mid) if mid else None}
    return res

out["youtube"] = summ(scan(R / "youtube/out/videos_dedup.jsonl", lambda r: r.get("source"),
                           lambda r: (r.get("title") or "") + "\n" + (r.get("text") or "")))
for plat in ("facebook", "instagram", "threads"):
    out[plat] = summ(scan(R / f"metalogin/deep/clean/{plat}.jsonl", lambda r: r.get("source"), lambda r: r.get("text") or ""))
ptt = {}
for f in sorted((R / "ptt/lists").glob("*.jsonl")):
    ptt.update(summ(scan(f, lambda r: r.get("board"), lambda r: None)))
out["ptt_lists"] = ptt
print(json.dumps(out, ensure_ascii=False, indent=1))
