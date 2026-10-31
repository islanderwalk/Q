"""Seed-7 small run (same as test_pipeline): canned rule counts, per-issue est/oracle/true, survey flags."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # q1/code
import make_fixtures as mf, run_q1
base = Path(tempfile.mkdtemp(prefix="q1_seed7_"))
mf.write_fixtures(base / "fx", n_emails=1500, n_surveys=400, seed=7, preview=5)
s = run_q1.run(base / "fx", base / "out", base_old_share=0.5, sample_size=300, seed=7)
ev = s["truth_eval"]
print("canned_by_kind customer", ev["canned_by_kind"]["customer"])
print("canned_by_kind canned", ev["canned_by_kind"]["canned"])
print("rule counts", s["email"]["stats"]["canned_rule_counts"], "excluded canned", s["email"]["stats"]["excluded_canned_template"])
mx_o = mx_t = 0
for r in ev["per_issue"]:
    d_o = r["est_share"] - r["oracle_share"]; d_t = r["est_share"] - r["true_share"]
    mx_o = max(mx_o, abs(d_o)); mx_t = max(mx_t, abs(d_t))
    print(f"  {r['issue']:8s} true={r['true_share']:.3f} oracle={r['oracle_share']:.3f} est={r['est_share']:.3f} "
          f"est-oracle={d_o:+.4f} est-true={d_t:+.4f} oracle/true={r['oracle_share']/r['true_share']:.3f} rec={r['recall']:.3f} prec={r['precision']:.3f}")
print(f"max|est-oracle|={mx_o:.4f} max|est-true|={mx_t:.4f}")
print("true top3", ev["true_top3"], "est top3", [r["issue"] for r in s["email"]["table"][:3]])
for k, v in s["survey"]["methods"].items():
    print("survey", k, v["top3"], "changed", v["top3_set_changed"], "beyond", v["top3_change_beyond_ci"])
print("label_check exclusion", s["label_check"]["exclusion"]["excluded"], s["label_check"]["actual"])
print("sample strata", s["sample"]["strata"])
