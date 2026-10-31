"""More RESULTS claims: lost body lines, leaked lines, resend duplicates, lines of the 53 canned-excluded customers."""
import json, sys
from pathlib import Path
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # q1/code
import email_clean as ec
import run_q1
S = sys.argv[1]
s = json.load(open(f"{S}/out/summary.json", encoding="utf-8"))
ev = s["truth_eval"]
print("outcome", ev["outcome"])
pi = ev["per_issue"]
print("max|est-oracle| (pp, 4 dp)", max(round(100 * abs(r["est_share"] - r["oracle_share"]), 4) for r in pi))
emails = run_q1.read_jsonl(f"{S}/fx/emails.jsonl")
truth = {t["email_id"]: t for t in run_q1.read_jsonl(f"{S}/fx/emails_truth.jsonl")}
records, stats, *_ = run_q1.email_stage(emails)
det = ec.TemplateDetector().fit(r["stripped_text"] for r in records)
lost, leaked, kinds = Counter(), Counter(), Counter()
own_lines_freq = Counter()
for r in records:
    t = truth[r["email_id"]]
    if t["kind"] in run_q1.CUSTOMER_KINDS and r["excluded"] is None:
        kept = {x.strip() for x in r["text"].split("\n") if x.strip()}
        body = [x for x in t["body_lines"] if x.strip()]
        lost.update(x for x in body if x not in kept)
        for x in kept - set(body):
            leaked["name_only" if ec.is_name_line(x) else "other:" + x] += 1
    if t["kind"] in run_q1.CUSTOMER_KINDS and r["excluded"] == "canned_template":
        for x in ec._content_lines("\n".join(t["body_lines"])):
            own_lines_freq["frequent" if x in det.template_lines else "not_frequent"] += 1
            if x not in det.template_lines:
                kinds["nonfreq_chars"] += len(x)
print("lost body lines", dict(lost.most_common(5)), "total", sum(lost.values()))
print("leaked", dict(leaked.most_common(5)), "total", sum(leaked.values()))
print("53 canned-excluded customers: own lines", dict(own_lines_freq), dict(kinds))
for line in ("想詢問企業方案的價格", "請問可以開統一編號的發票嗎", "想了解學生方案怎麼申請", "請問有沒有合作提案的窗口", "想問年繳跟月繳差多少"):
    n = ec.normalize_line(line)
    print("  no-issue line", line, "df", det.df[n], "frequent", n in det.template_lines)
