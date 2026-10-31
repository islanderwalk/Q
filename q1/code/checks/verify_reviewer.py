"""Re-check the reviewer's numbers on the current 40,000-email fixtures (read-only; uses repo code)."""
import json, sys
from pathlib import Path
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # q1/code
import email_clean as ec
import issue_tags as it

FX = sys.argv[1]
emails = [json.loads(l) for l in open(f"{FX}/emails.jsonl", encoding="utf-8")]
truth = {t["email_id"]: t for t in (json.loads(l) for l in open(f"{FX}/emails_truth.jsonl", encoding="utf-8"))}
det = ec.TemplateDetector()
records, stats, clusters = ec.clean_corpus((e["text"] for e in emails), det)
loose = ec.TemplateDetector(min_pair_conf=0.0).fit(r["stripped_text"] for r in records)
print("min_df", det.min_df, "frequent", len(det.template_lines), "pairs(final)", len(det.template_pairs), "pairs(conf0)", len(loose.template_pairs))
by = {}
for e, r in zip(emails, records):
    kind = truth[e["email_id"]]["kind"]
    s = r["stripped_text"]
    a = det.analyze(s)
    v = {"naive": bool(s) and len(a["hits"]) >= 2 and a["coverage"] >= 0.6,
         "residual": bool(s) and len(a["hits"]) >= 2 and a["residual_chars"] < 12,
         "adjacent": bool(s) and loose.is_canned(s),
         "final": bool(s) and det.is_canned(s),
         "excluded_canned": r["excluded"] == "canned_template"}
    c = by.setdefault(kind, Counter())
    c["total"] += 1
    c.update(k for k, x in v.items() if x)
for k in sorted(by):
    print(f"{k:22s}", " ".join(f"{x}={by[k][x]:,}" for x in ("total", "naive", "residual", "adjacent", "final", "excluded_canned")))

print("\n# template pairs (final): df / conf")
for (a, b), conf in sorted(det.template_pairs.items(), key=lambda kv: -kv[1]):
    print(f"  {det.pair_df[(a, b)]:6,} {conf:.3f}  {a} -> {b}")
confs = list(det.template_pairs.values())
print("conf range", f"{min(confs):.3f}-{max(confs):.3f}")
print("# conf of pairs in loose but not in final:")
extra = sorted(((loose.pair_df[p], c, p) for p, c in loose.template_pairs.items() if p not in det.template_pairs), key=lambda x: -x[1])
for df, conf, (a, b) in extra[:12]:
    print(f"  {df:6,} {conf:.3f}  {a} -> {b}")
print("n extra pairs", len(extra), "max conf among extra", f"{max(x[1] for x in extra):.3f}" if extra else None)

# 47 customers excluded as canned: own text vs residual
print("\n# customers excluded as canned")
own_lt12 = 0
no_issue = 0
pasted = 0
rows = []
for e, r in zip(emails, records):
    t = truth[e["email_id"]]
    if t["kind"] not in ("customer", "customer_resend") or r["excluded"] != "canned_template":
        continue
    own = sum(len(x) for x in ec._content_lines("\n".join(t["body_lines"])))
    own_lt12 += own < 12
    no_issue += not t["issues"]
    pasted += "pasted_reply" in t["hard_case"]
    rows.append((t["kind"], own, r["residual_chars"], t["issues"], t["hard_case"]))
print("n", len(rows), "own<12", own_lt12, "no_issue", no_issue, "pasted_reply", pasted, Counter(k for k, *_ in rows))
print("own chars min/max", min(x[1] for x in rows), max(x[1] for x in rows), "residual max", max(x[2] for x in rows))
for x in rows[:5]:
    print("  ", x)

# negation breakdown among included customers with wrong tags
print("\n# negated_mention, included customer(+resend), tags wrong")
cnt = Counter()
extra_issues = Counter()
for e, r in zip(emails, records):
    t = truth[e["email_id"]]
    if t["kind"] not in ("customer", "customer_resend") or r["excluded"] is not None:
        continue
    if "negated_mention" not in t["hard_case"]:
        continue
    auto = set(it.tag_text(r["text"])); real = set(t["issues"])
    if auto == real:
        continue
    has_neg = "沒有閃退" in r["text"]
    unk = "unkeyed_wording" in t["hard_case"]
    cnt[(has_neg, unk)] += 1
    cnt["extra" if auto - real else "noextra"] += 1
    cnt["missing" if real - auto else "nomissing"] += 1
    extra_issues.update(auto - real)
print(dict(cnt), dict(extra_issues))

# pasted_reply wrongly excluded breakdown
pc = Counter()
for e, r in zip(emails, records):
    t = truth[e["email_id"]]
    if "pasted_reply" in t["hard_case"] and t["kind"] in ("customer", "customer_resend") and r["excluded"] not in (None, "duplicate"):
        pc[(t["kind"], r["excluded"])] += 1
print("pasted_reply wrongly excluded", dict(pc))
