"""Q6 實驗重跑對帳：重跑的 results*.json 與 repo 版本比對。

逐檔印出原始 sha256、原始位元組是否相同、去掉計時欄位（seconds、chars_per_sec）後是否相同，
以及每個不同欄位的 JSON 路徑與前後值。計時欄位每次重跑都會變，其他欄位應完全相同。

用法: python -I compare_results.py <repo 的 data/jieba_experiments> <重跑輸出目錄（含 exp1/ exp2/ exp3/）>
退出碼: 去掉計時欄位後全部相同為 0，否則為 1。
"""
import hashlib
import json
import sys
from pathlib import Path

TIMING = frozenset({"seconds", "chars_per_sec"})
FILES = (("results.json", "exp1"), ("results2.json", "exp2"), ("results3.json", "exp3"))


def diff_paths(a, b, path="$", out=None):
    """回傳 [(JSON 路徑, 舊值, 新值)]；長度不同的 list 視為整個不同。"""
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            diff_paths(a.get(k), b.get(k), f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            diff_paths(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append((path, a, b))
    return out


def strip_timing(o):
    if isinstance(o, dict):
        return {k: strip_timing(v) for k, v in o.items() if k not in TIMING}
    if isinstance(o, list):
        return [strip_timing(v) for v in o]
    return o


def compare(repo_bytes: bytes, new_bytes: bytes) -> dict:
    a, b = json.loads(repo_bytes), json.loads(new_bytes)
    diffs = diff_paths(a, b)
    return {
        "repo_sha256": hashlib.sha256(repo_bytes).hexdigest(),
        "rerun_sha256": hashlib.sha256(new_bytes).hexdigest(),
        "raw_equal": repo_bytes == new_bytes,
        "equal_without_timing": strip_timing(a) == strip_timing(b),
        "diffs": diffs,
        "non_timing_diffs": [d for d in diffs if d[0].rsplit(".", 1)[-1] not in TIMING],
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    repo, new = Path(argv[0]), Path(argv[1])
    ok = True
    for name, sub in FILES:
        r = compare((repo / name).read_bytes(), (new / sub / name).read_bytes())
        ok &= r["equal_without_timing"]
        print(f"{name}: repo sha256={r['repo_sha256']} rerun sha256={r['rerun_sha256']} "
              f"raw_equal={r['raw_equal']} equal_without_timing={r['equal_without_timing']} "
              f"diff_fields={len(r['diffs'])} non_timing_diff_fields={len(r['non_timing_diffs'])}")
        for p, x, y in r["diffs"]:
            print(f"  {p}: {x} -> {y}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
