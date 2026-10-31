"""Why are 161 resends included (not deduplicated)?"""
import sys
from pathlib import Path
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # q1/code
import email_clean as ec
import run_q1
S = sys.argv[1]
emails = run_q1.read_jsonl(f"{S}/fx/emails.jsonl")
truth = {t["email_id"]: t for t in run_q1.read_jsonl(f"{S}/fx/emails_truth.jsonl")}
records, *_ = run_q1.email_stage(emails)
by_id = {r["email_id"]: r for r in records}
c = Counter()
for r in records:
    t = truth[r["email_id"]]
    if t["kind"] != "customer_resend" or r["excluded"] is not None:
        continue
    orig = by_id[t["resend_of"]]
    if orig["excluded"] is not None:
        c["original_excluded:" + orig["excluded"]] += 1
        continue
    a = [x for x in r["text"].split("\n") if x.strip()]
    b = [x for x in orig["text"].split("\n") if x.strip()]
    extra = set(a) ^ set(b)
    c["texts_differ_by_name_line_only" if extra and all(ec.is_name_line(x.strip()) for x in extra) else
      "same_text" if not extra else "other"] += 1
print(dict(c), "total", sum(c.values()))
short = [r for r in records if truth[r["email_id"]]["kind"] == "customer_resend" and r["excluded"] is None
         and ec._dedupe_key(r["text"]) is None]
print("included resends with text under the 20-char dedupe minimum:", len(short), [len(r["text"].replace("\n", "")) for r in short])
