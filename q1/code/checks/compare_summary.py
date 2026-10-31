"""Compare keys shared by the old committed summary.json and the new one (ignores elapsed_sec)."""
import json, sys
old = json.load(open(sys.argv[1], encoding="utf-8"))
new = json.load(open(sys.argv[2], encoding="utf-8"))
diffs, shared = [], 0
def walk(a, b, path):
    global shared
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            if k in b:
                walk(a[k], b[k], f"{path}.{k}")
            else:
                diffs.append(f"only-old {path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"len {path}: {len(a)} vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, f"{path}[{i}]")
    else:
        shared += 1
        if a != b and not path.endswith("elapsed_sec"):
            diffs.append(f"value {path}: {a!r} -> {b!r}")
walk(old, new, "")
print(f"shared leaf values={shared:,} differences={len(diffs)}")
for d in diffs[:40]:
    print("  ", d)
