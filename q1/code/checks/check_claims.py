"""Check RESULTS claims against run3 outputs (read-only; uses repo code for the line normalizer)."""
import csv, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # q1/code
import email_clean as ec
S = sys.argv[1]
s = json.load(open(f"{S}/out/summary.json", encoding="utf-8"))
em, ev, lc = s["email"], s["truth_eval"], s["label_check"]
st = em["stats"]
emails = [json.loads(l) for l in open(f"{S}/fx/emails.jsonl", encoding="utf-8")]
det = ec.TemplateDetector()
records, _, _ = ec.clean_corpus((e["text"] for e in emails), det)
line = ec.normalize_line("你們上次回覆我：")
print("pasted-intro line", repr(line), "len", len(line), "df", det.df[line], "frequent", line in det.template_lines, "min_df", det.min_df)
print("unpaired_top", [(x["line"], x["df"]) for x in em["unpaired_top"]])
print("outcome canned|*", {k: v for k, v in ev["outcome"].items() if k.startswith("canned|")})
print("outcome agent_reply|*", {k: v for k, v in ev["outcome"].items() if k.startswith("agent_reply|")})
print("canned_customers", ev["canned_customers"])
hb = ev["canned_by_hard_case"]
print("hard pasted_reply", hb["pasted_reply"], "no_signature", hb["no_signature"])
print("template pairs kept", len(em["template_pairs"]["kept"]), "conf", min(p["conf"] for p in em["template_pairs"]["kept"]), max(p["conf"] for p in em["template_pairs"]["kept"]),
      "dropped", len(em["template_pairs"]["dropped"]), "max dropped conf", em["template_pairs"]["dropped"][0]["conf"])
print("rule counts", st["canned_rule_counts"])
pi = ev["per_issue"]
d_o = [abs(r["est_share"] - r["oracle_share"]) for r in pi]; d_t = [r["true_share"] - r["est_share"] for r in pi]
print("max|est-oracle| pp", round(100 * max(d_o), 3), "true-est pp min/max", round(100 * min(d_t), 2), round(100 * max(d_t), 2), "all below", all(x > 0 for x in d_t))
for r in pi:
    print(f"  {r['issue']} true={r['true_share']:.4f} oracle={r['oracle_share']:.4f} est={r['est_share']:.4f} prec={r['precision']:.3f} rec={r['recall']:.3f}")
print("prec range", min(r["precision"] for r in pi), max(r["precision"] for r in pi), "rec range", min(r["recall"] for r in pi), max(r["recall"] for r in pi))
print("hard_cases", {h: {k: v for k, v in c.items()} for h, c in ev["hard_cases"].items()})
print("lines", ev["issue_lines_lost"], ev["issue_lines"], ev["body_lines_lost"], ev["body_lines"], ev["included_customer_emails_with_leaked_lines"], ev["leaked_lines"], ev["pasted_template_lines_left"])
# label check
t = lc["tags"]
print("label prec", min(v["precision"] for v in t.values()), max(v["precision"] for v in t.values()), "n_auto", min(v["n_auto"] for v in t.values()), max(v["n_auto"] for v in t.values()))
print("label rec", min(v["recall"] for v in t.values()), max(v["recall"] for v in t.values()), "n_human", min(v["n_human"] for v in t.values()), max(v["n_human"] for v in t.values()))
print("label rec by issue", {k: round(v["recall"], 3) for k, v in t.items()})
for h in lc["exclusion"]["strata"]:
    if h["excluded"]:
        print("  stratum", h)
print("included", lc["exclusion"]["included"], "actual", lc["actual"])
# allocation
print("sample strata", s["sample"]["strata"])
sizes = {}
with open(f"{S}/out/label_sample_300.csv", encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        sizes[r["stratum"]] = int(r["stratum_size"])
inc = {h: n for h, n in sizes.items() if not h.startswith("排除")}
tot = sum(inc.values())
print("included strata sizes", inc, "sum", tot)
print("pure proportional 260:", {h: round(260 * n / tot, 1) for h, n in inc.items()})
print("coverage row 3", s["coverage"]["rows"][3])
print("satisfaction", s["survey"]["satisfaction"], "counts", s["survey"]["counts"])
print("clusters", st["canned_clusters"], [c["size"] for c in em["clusters"][:8]], "rest", sum(c["size"] for c in em["clusters"][6:]), len(em["clusters"][6:]))
print("stats", {k: st[k] for k in ("with_quote", "quoted_lines_removed", "quoted_chars_removed", "with_signature", "signature_lines_removed", "signature_chars_removed", "frequent_lines", "template_pairs", "unpaired_frequent_lines", "included_with_pasted_template", "pasted_template_lines_removed", "included_with_frequent_lines")})
print("table top3", [(r["issue"], r["mentions"], round(r["share"], 4)) for r in em["table"][:3]], "untagged", em["untagged"])
for k, v in em["ablation"].items():
    print("ablation", k, v["n"], [(r["issue"], r["mentions"], round(r["share"], 4)) for r in v["table"][:3]])
