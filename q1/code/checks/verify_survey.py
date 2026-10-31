"""Survey: observed shares/CI per method, pairwise 'close' checks, and generator expected shares (exact enumeration)."""
import itertools, math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # q1/code
import make_fixtures as mf
import survey_weight as sw

rows = sw.load_survey(sys.argv[1])
m = sw.compare_methods(rows, 0.5)

def half(r):
    return (r["ci_high"] - r["ci_low"]) / 2

for name, v in m.items():
    rk = v["ranking"]
    print(f"\n## {name} n={v['n']:,} n_eff={v['n_eff']:.0f}")
    for r in rk:
        print(f"  {r['rank']} {r['issue']:8s} {r['share']:.3f} ±{half(r):.4f}")
    # pairwise closeness among ranks 1..5
    for i in range(5):
        for j in range(i + 1, 5):
            a, b = rk[i], rk[j]
            d = a["share"] - b["share"]
            c = math.sqrt(half(a) ** 2 + half(b) ** 2)
            if j == i + 1 or (i, j) in ((1, 4), (0, 3), (0, 1)):
                print(f"    rank{i+1} vs rank{j+1}: diff={d:.4f} combined={c:.4f} close={d < c}")

# generator expected shares
exp = {}
for g, p in mf.SURVEY_P.items():
    e = {i: 0.0 for i in mf.ISSUES}
    for bits in itertools.product([0, 1], repeat=len(mf.ISSUES)):
        pr = 1.0
        for i, b in zip(mf.ISSUES, bits):
            pr *= p[i] if b else 1 - p[i]
        picked = [i for i, b in zip(mf.ISSUES, bits) if b]
        for i in picked:
            e[i] += pr * (1 if len(picked) <= 3 else 3 / len(picked))
    exp[g] = e
print("\n## generator expected shares")
for s in (0.2, 0.35, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8):
    mix = {i: s * exp["old"][i] + (1 - s) * exp["new"][i] for i in mf.ISSUES}
    print(f"  old={s:.2f} ", "  ".join(f"{i[:2]} {v:.1%}" for i, v in sorted(mix.items(), key=lambda kv: -kv[1])))
for g in ("new", "old"):
    print(f"  {g}-only  ", "  ".join(f"{i[:2]} {v:.1%}" for i, v in sorted(exp[g].items(), key=lambda kv: -kv[1])))
