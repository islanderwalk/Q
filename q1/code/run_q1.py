#!/usr/bin/env python3
"""Q1 原型：把答案第 3 點（三天的資料處理）串成一支可以實跑的程式。

第 1 天：客服信件去轉寄引文、簽名檔、罐頭回覆（email_clean）；匯出 300 則分層抽樣給人工標註（issue_tags）；
        問卷依購買期間分層（survey_weight）。
第 2 天：規則式問題分類，算各候選問題的提及數、佔比並排序（issue_tags）。
第 3 天：敏感度檢查：事後分層加權、排除老客戶後前三名是否改變、母體比例掃描（survey_weight）；
        董事會報告第一頁的覆蓋範圍與排除說明（coverage_table）。

輸入（--data 目錄，make_fixtures.py 產生的合成資料）：emails.jsonl、survey.csv。
  有 emails_truth.jsonl 時另外輸出「規則 vs 答案」的評估（只有合成資料有答案，真實資料要靠 300 則人工標註），
  並用答案模擬一位完全正確的標註者，看 300 則抽樣能估到什麼程度。
輸出（--out 目錄）：
  summary.json          全部數字（含模板相鄰對的 df 與共同出現率）
  report.md             人看的報告（含罐頭判定規則的逐步對照）
  coverage.md／.csv     覆蓋範圍與排除說明、各數字的樣本來源
  email_mentions.csv    清理後各問題的提及封數、佔比、Wilson 95% CI
  email_ablation.csv    未清理／只去引文與簽名／完整清理三種排序
  canned_clusters.csv   罐頭回覆群（大小、代表句），供人工確認
  label_sample_300.csv  300 則分層抽樣，給人工標註
  survey_methods.csv    未加權／加權／只看未滿 6 個月／只看滿 6 個月以上的排序
  survey_sweep.csv      母體老客戶比例 20%～80% 的前三名

用法：python run_q1.py --data DIR --out DIR [--base-old-share 0.5] [--sample-size 300]
"""
import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import coverage_table as cov  # noqa: E402
import email_clean as ec  # noqa: E402
import issue_tags as it  # noqa: E402
import survey_weight as sw  # noqa: E402

SOCIAL_POSTS = 150000      # 題目：社群貼文約 15 萬則（本原型沒有載入）
SOCIAL_QUAL_SAMPLE = 200   # 答案：抽樣 200 則做人工定性檢視（計畫；本原型沒有社群資料，沒有做）
EXCLUDED_STRATA = {"canned_template": "排除：罐頭回覆", "agent_signature": "排除：客服個別回覆"}
EXCLUDED_PER_STRATUM = 20
NAIVE_COVERAGE = 0.6
# 罐頭判定的逐步對照（消融）：同一批去完引文與簽名的信、同一份常見行，每一步只改一個條件，D 是本程式的規則。
# A→B 把「常見行佔比」換成「常見行以外的字數」；B→C 要求有兩行常見行相鄰、而且這個相鄰對出現在 ≥ min_df 封
# （TemplateDetector(min_pair_conf=0)）；C→D 再要求相鄰共同出現率 ≥ 0.5。B、C、D 一個比一個嚴（D ⊆ C ⊆ B）。
CANNED_RULES = (
    ("naive", "A. 只看行頻率：≥ 2 行常見行、常見行字數佔 ≥ 60%"),
    ("residual", "B. 改成常見行以外不到 12 字"),
    ("adjacent", "C. B＋常見行要成對相鄰（不看共同出現率）"),
    ("final", "D. C＋相鄰共同出現率 ≥ 0.5（本程式）"),
)


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_rows(path, rows, columns):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def pct(x):
    return f"{x:.1%}"


# ---------- 客服信件 ----------

def pair_rows(det, pairs):
    """相鄰對清單 [{"a", "b", "df", "conf"}]，依共同出現率由高到低。"""
    return [{"a": a, "b": b, "df": det.pair_df[(a, b)], "conf": round(conf, 3)}
            for (a, b), conf in sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))]


def email_stage(emails):
    detector = ec.TemplateDetector()
    records, stats, clusters = ec.clean_corpus((e["text"] for e in emails), detector)
    unpaired_top = [{"line": x, "df": detector.df[x]} for x in detector.unpaired_lines()[:6]]
    # 罐頭判定的逐步對照（CANNED_RULES）。C 用不設共同出現率門檻的偵測器，常見行與 D 完全相同，只差相鄰對
    loose = ec.TemplateDetector(min_pair_conf=0.0).fit(r["stripped_text"] for r in records)
    for r in records:
        s = r["stripped_text"]
        a = detector.analyze(s)
        many = bool(s) and len(a["hits"]) >= 2
        short = many and a["residual_chars"] < detector.max_residual_chars
        r["canned_rules"] = {"naive": many and a["coverage"] >= NAIVE_COVERAGE, "residual": short,
                             "adjacent": short and bool(loose.analyze(s)["block"]),
                             "final": short and bool(a["block"])}
    stats["canned_rule_counts"] = {k: sum(r["canned_rules"][k] for r in records) for k, _ in CANNED_RULES}
    pairs = {"kept": pair_rows(detector, detector.template_pairs),
             "dropped": pair_rows(loose, {p: c for p, c in loose.template_pairs.items()
                                          if p not in detector.template_pairs})}
    for e, r in zip(emails, records):
        r["email_id"] = e["email_id"]
        r["tags"] = it.tag_text(r["text"]) if r["excluded"] is None else []
    included = [r for r in records if r["excluded"] is None]
    table, untagged = it.mention_table([r["tags"] for r in included])

    # 消融：不清理、只去引文與簽名（不排除罐頭與客服信）、完整清理，三種前三名
    raw_tags = [it.tag_text(e["text"]) for e in emails]
    partial_tags = [it.tag_text(r["stripped_text"]) for r in records if r["stripped_text"]]
    ablation = {
        "raw": {"n": len(raw_tags), "table": it.mention_table(raw_tags)[0]},
        "quote_sig_only": {"n": len(partial_tags), "table": it.mention_table(partial_tags)[0]},
        "full": {"n": len(included), "table": table},
    }
    return records, stats, clusters, table, untagged, ablation, unpaired_top, pairs


def label_sample(records, size, seed):
    pool = []
    for r in records:
        if r["excluded"] is None:
            pool.append({"email_id": r["email_id"], "tags": r["tags"], "text": r["text"]})
        elif r["excluded"] in EXCLUDED_STRATA:
            pool.append({"email_id": r["email_id"], "tags": [], "text": r["text"],
                         "stratum": EXCLUDED_STRATA[r["excluded"]]})
    fixed = {h: EXCLUDED_PER_STRATUM for h in EXCLUDED_STRATA.values()}
    return it.stratified_sample(pool, size=size, seed=seed, fixed=fixed)


# ---------- 合成資料才有的答案評估 ----------

CUSTOMER_KINDS = ("customer", "customer_resend")


def evaluate_against_truth(records, truth, table):
    """拿產生器的答案量規則的誤刪、漏刪與標籤準確度。

    - 行層級：只看「應該納入、也真的納入」的顧客信，檢查描述問題的行有沒有被刪、有沒有殘留非顧客寫的行。
    - 標籤：納入的信都算（含不該納入卻納入的客服信，它們的標籤都是誤報）。
    - 難例：顧客信被排除算 wrongly_excluded，客服信被納入算 wrongly_included，納入的顧客信標籤不對算 tags_wrong，
      再拆成多標（tags_extra，規則多標了問題）與漏標（tags_missing），一封可以兩者都有。
    - oracle_share：清理完全正確（只拿顧客自己寫的行）時同一套分類規則會給的佔比。估計佔比和它的差距是清理造成的，
      它和真實佔比的差距是分類規則（關鍵詞、否定）造成的。
    """
    by_id = {t["email_id"]: t for t in truth}
    outcome = Counter()
    canned_by_kind, canned_by_hard = {}, {}
    for r in records:
        t = by_id[r["email_id"]]
        outcome[(t["kind"], r["excluded"] or "included")] += 1
        for key, bucket in [(t["kind"], canned_by_kind)] + [(h, canned_by_hard) for h in t["hard_case"]]:
            c = bucket.setdefault(key, {"total": 0, **{k: 0 for k, _ in CANNED_RULES}})
            c["total"] += 1
            for k, _ in CANNED_RULES:
                c[k] += r["canned_rules"][k]

    issue_lines = issue_lost = body_lines = body_lost = 0
    leaked_emails = leaked_lines = pasted_left = 0
    # 被當成罐頭排除的顧客信：顧客自己寫了幾字（用罐頭偵測同一套行規則算，不含問候行），有沒有提到問題
    canned_customers = {"n": 0, "own_chars": [], "with_issues": 0, "pasted_reply": 0}
    hard = {}
    tp, fp, fn = Counter(), Counter(), Counter()
    true_share, oracle = Counter(), Counter()
    customers = [t for t in truth if t["kind"] == "customer"]
    for t in customers:
        true_share.update(t["issues"])
        oracle.update(it.tag_text("\n".join(t["body_lines"])))
    for r in records:
        t = by_id[r["email_id"]]
        is_customer, included = t["kind"] in CUSTOMER_KINDS, r["excluded"] is None
        auto, real = set(r["tags"]), set(t["issues"])
        if is_customer and r["excluded"] == "canned_template":
            canned_customers["n"] += 1
            canned_customers["own_chars"].append(sum(len(x) for x in ec._content_lines("\n".join(t["body_lines"]))))
            canned_customers["with_issues"] += bool(real)
            canned_customers["pasted_reply"] += "pasted_reply" in t["hard_case"]
        for h in t["hard_case"]:
            c = hard.setdefault(h, {"n": 0, "wrongly_excluded": 0, "wrongly_included": 0, "tags_wrong": 0,
                                    "tags_extra": 0, "tags_missing": 0, "extra_issues": Counter()})
            c["n"] += 1
            if is_customer and not included and r["excluded"] != "duplicate":
                c["wrongly_excluded"] += 1
            elif not is_customer and included:
                c["wrongly_included"] += 1
            elif is_customer and included and auto != real:
                c["tags_wrong"] += 1
                c["tags_extra"] += bool(auto - real)
                c["tags_missing"] += bool(real - auto)
                c["extra_issues"].update(auto - real)
        if not included:
            continue
        if not is_customer:  # 不該納入卻納入（例如沒簽名檔的客服信）：標籤都是誤報
            fp.update(auto)
            continue
        kept = {x.strip() for x in r["text"].split("\n") if x.strip()}
        issue_lines += len(t["issue_lines"])
        issue_lost += sum(1 for x in t["issue_lines"] if x not in kept)
        body = [x for x in t["body_lines"] if x.strip()]
        body_lines += len(body)
        body_lost += sum(1 for x in body if x not in kept)
        leaked = kept - set(body)
        if leaked:
            leaked_emails += 1
            leaked_lines += len(leaked)
        pasted_left += sum(1 for x in t.get("noise_pasted", []) if x in kept)
        for i in auto & real:
            tp[i] += 1
        for i in auto - real:
            fp[i] += 1
        for i in real - auto:
            fn[i] += 1

    est = {row["issue"]: row for row in table}
    n_cust = len(customers)
    per_issue = []
    for i in it.ISSUES:
        prec = tp[i] / (tp[i] + fp[i]) if tp[i] + fp[i] else None
        rec = tp[i] / (tp[i] + fn[i]) if tp[i] + fn[i] else None
        per_issue.append({"issue": i, "tp": tp[i], "fp": fp[i], "fn": fn[i], "precision": prec, "recall": rec,
                          "true_share": true_share[i] / n_cust if n_cust else 0.0,
                          "oracle_share": oracle[i] / n_cust if n_cust else 0.0,
                          "est_share": est[i]["share"], "est_rank": est[i]["rank"]})
    true_rank = sorted(it.ISSUES, key=lambda i: (-true_share[i], it.ISSUES.index(i)))
    for row in per_issue:
        row["true_rank"] = true_rank.index(row["issue"]) + 1
    kinds = Counter(t["kind"] for t in truth)
    own = canned_customers.pop("own_chars")
    canned_customers.update(own_chars_min=min(own, default=None), own_chars_max=max(own, default=None),
                            own_chars_under_12=sum(1 for x in own if x < 12))
    return {
        "kinds": dict(kinds),
        "canned_customers": canned_customers,
        "outcome": {f"{k}|{o}": n for (k, o), n in sorted(outcome.items())},
        "canned_by_kind": {k: canned_by_kind[k] for k in sorted(canned_by_kind)},
        "canned_by_hard_case": {h: canned_by_hard[h] for h in sorted(canned_by_hard)},
        "issue_lines": issue_lines, "issue_lines_lost": issue_lost,
        "body_lines": body_lines, "body_lines_lost": body_lost,
        "included_customer_emails_with_leaked_lines": leaked_emails, "leaked_lines": leaked_lines,
        "pasted_template_lines_left": pasted_left,
        "hard_cases": {h: dict(hard[h], extra_issues=dict(hard[h]["extra_issues"].most_common()))
                       for h in sorted(hard)},
        "per_issue": per_issue,
        "true_top3": true_rank[:3],
    }


def simulated_label_check(records, sample, truth):
    """用答案模擬一位完全正確的標註者標完抽樣，走和真實標註同一條路（evaluate_labels、estimate_exclusion_errors），
    再和全體的實際值比，看 300 則估得到什麼程度。真實標註者會犯錯，所以這是上限。"""
    by_id = {t["email_id"]: t for t in truth}
    labels = {}
    for r in sample:
        t = by_id[r["email_id"]]
        agent = t["kind"] not in CUSTOMER_KINDS
        labels[r["email_id"]] = {"tags": set() if agent else set(t["issues"]), "is_agent": agent}
    actual = Counter()
    for r in records:
        customer = by_id[r["email_id"]]["kind"] in CUSTOMER_KINDS
        if r["excluded"] in EXCLUDED_STRATA and customer:
            actual[EXCLUDED_STRATA[r["excluded"]]] += 1
        elif r["excluded"] is None and not customer:
            actual["included_non_customer"] += 1
    return {"tags": it.evaluate_labels(sample, labels), "exclusion": it.estimate_exclusion_errors(sample, labels),
            "actual": {"excluded_customers": {h: actual[h] for h in EXCLUDED_STRATA.values()},
                       "included_non_customers": actual["included_non_customer"]}}


# ---------- 問卷 ----------

def survey_stage(path, base_old_share):
    rows = sw.load_survey(path)
    counts = sw.stratum_counts(rows)
    weights = sw.poststrat_weights(rows, base_old_share)
    methods = sw.compare_methods(rows, base_old_share)
    sweep = sw.sensitivity_sweep(rows)
    known = [(r, 1.0) for r in rows if r["stratum"] != sw.UNKNOWN]
    sat = {"unweighted": sw.weighted_mean_satisfaction([r for r, _ in known], [w for _, w in known]),
           "weighted": sw.weighted_mean_satisfaction(rows, weights)}
    return rows, counts, methods, sweep, sat


# ---------- 報告 ----------

def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def yes(flag):
    return "是" if flag else "否"


def ratio(x, digits=3):
    return "—" if x is None else f"{x:.{digits}f}"


METHOD_NAMES = {"unweighted": "未加權（已知購買期間）", "weighted": "事後分層加權",
                "new_only": "只看未滿 6 個月", "old_only": "只看滿 6 個月以上"}
ABLATION_NAMES = {"raw": "未清理（原文全部）", "quote_sig_only": "只去引文與簽名",
                  "full": "完整清理（再排除罐頭、客服信、重寄）"}
KIND_ORDER = ("canned", "customer", "customer_resend", "agent_reply", "customer_forward_only")


def render_report(s):
    em, sv = s["email"], s["survey"]
    st = em["stats"]
    kept, dropped = em["template_pairs"]["kept"], em["template_pairs"]["dropped"]
    conf_range = f"{min(p['conf'] for p in kept):.3f}～{max(p['conf'] for p in kept):.3f}" if kept else "—"
    lines = ["# Q1 原型實跑報告（合成資料）", "",
             "> 合成資料：由 `make_fixtures.py` 以固定 seed 產生，不是真實顧客資料。"
             "數字只用來驗證程式會動、示範每個步驟的效果，不能當成任何產品的結論。", "",
             f"- 參數：母體老客戶比例 base_old_share = {sv['base_old_share']:.0%}（假設，正式報告要換成 CRM 實際分布）；"
             f"抽樣 {s['sample']['size']:,} 則，seed {s['sample']['seed']}",
             f"- 執行時間：{s['elapsed_sec']:.1f} 秒", "",
             cov.render_markdown(s["coverage"]["rows"]), cov.render_sources(s["coverage"]["sources"]),
             "## 1. 客服信件清理", "",
             md_table(["項目", "封數或行數"], [
                 ["原始信件", f"{st['total']:,}"],
                 ["含轉寄引文的信", f"{st['with_quote']:,}"],
                 ["刪掉的引文行（字數）", f"{st['quoted_lines_removed']:,}（{st['quoted_chars_removed']:,} 字）"],
                 ["含簽名檔的信", f"{st['with_signature']:,}"],
                 ["刪掉的簽名行（字數）", f"{st['signature_lines_removed']:,}（{st['signature_chars_removed']:,} 字）"],
                 [f"常見行（df ≥ {st['min_df']:,}）", f"{st['frequent_lines']:,}"],
                 [f"模板相鄰對（共同出現率 ≥ 0.5；實際 {conf_range}）", f"{st['template_pairs']:,}"],
                 ["常見但不成塊的行（多半是顧客常見句，不當模板）", f"{st['unpaired_frequent_lines']:,}"],
                 ["罐頭回覆群數", f"{st['canned_clusters']:,}"],
                 ["排除：罐頭回覆", f"{st['excluded_canned_template']:,}"],
                 ["排除：客服個別回覆（簽名含客服專員／中心）", f"{st['excluded_agent_signature']:,}"],
                 ["排除：重寄", f"{st['excluded_duplicate']:,}"],
                 ["排除：清理後無內容", f"{st['excluded_empty']:,}"],
                 ["納入分析", f"{st['included']:,}"],
                 ["納入的信含貼上的罐頭回覆（刪掉模板行後納入）",
                  f"{st['included_with_pasted_template']:,} 封（{st['pasted_template_lines_removed']:,} 行）"],
                 ["納入的信仍含常見行（只計數，不刪）", f"{st['included_with_frequent_lines']:,}"],
             ]), "",
             "### 罐頭判定規則的逐步對照（全部信件，判成罐頭的封數）", "",
             "同一批去完引文與簽名的信、同一份常見行，每一步只改一個條件；D 是本程式採用的規則。"
             "各類信件分別被判成幾封見第 4 節。", "",
             md_table(["規則", "判成罐頭"], [[name, f"{st['canned_rule_counts'][k]:,}"] for k, name in CANNED_RULES]), "",
             "### 模板相鄰對（共同出現率由高到低）", "",
             "共同出現率 = 兩行相鄰出現的封數 / 兩行各自 df 的較小值。", "",
             md_table(["前一行（正規化後）", "後一行", "相鄰出現封數", "共同出現率"],
                      [[p["a"], p["b"], f"{p['df']:,}", f"{p['conf']:.3f}"] for p in kept]), "",
             f"相鄰出現 ≥ {st['min_df']:,} 封、但共同出現率不到 0.5 而被擋掉的相鄰對 {len(dropped):,} 對"
             + (f"，最高 {dropped[0]['conf']:.3f}（前 3 對如下）。" if dropped else "。"), "",
             md_table(["前一行（正規化後）", "後一行", "相鄰出現封數", "共同出現率"],
                      [[p["a"], p["b"], f"{p['df']:,}", f"{p['conf']:.3f}"] for p in dropped[:3]]), "",
             "### 常見但不成塊的行（前 6 行，df 由高到低）", "",
             "這些行的 df 達門檻，但跟前後行沒有固定搭配，判為顧客常見句；只看行頻率的話會被當成模板。", "",
             md_table(["行（正規化後）", "df"], [[x["line"], f"{x['df']:,}"] for x in em["unpaired_top"]]), "",
             "### 罐頭回覆群（前 8 群）", "",
             md_table(["群", "封數", "代表句（群內最多封出現的兩行）"],
                      [[c["cluster"], f"{c['size']:,}", " ／ ".join(c["lines"][:2])] for c in em["clusters"][:8]]), "",
             "## 2. 問題分類與排序（客服信件）", "",
             md_table(["排名", "問題", "提及封數", "佔比", "95% CI"],
                      [[r["rank"], r["issue"], f"{r['mentions']:,}", pct(r["share"]),
                        f"{pct(r['ci_low'])}～{pct(r['ci_high'])}"] for r in em["table"]]), "",
             f"未分類（沒有命中任何規則）：{em['untagged']:,} 封。", "",
             "### 清理步驟對排序的影響（消融）", "",
             md_table(["做法", "分母", "前三名"],
                      [[ABLATION_NAMES[k], f"{v['n']:,}", "、".join(r["issue"] for r in v["table"][:3])]
                       for k, v in em["ablation"].items()]), "",
             "## 3. 問卷：分層、加權與敏感度", "",
             md_table(["分層", "份數"], [[h, f"{n:,}"] for h, n in sv["counts"].items()]), "",
             "「明確在前三名內」：和第 4 名的差距大於兩者 CI 半寬的合成值。「改變超出誤差」：有問題在未加權時明確在前三名內、"
             "在這個做法明確在外（或反過來）。只有名單不同、沒有超出誤差的改變，不能當成發現。", "",
             md_table(["做法", "n", "有效樣本數", "前三名", "明確在前三名內", "前三名組成改變", "改變超出誤差",
                       "第 3、4 名差距在誤差內"],
                      [[METHOD_NAMES[k], f"{v['n']:,}", f"{v['n_eff']:,.0f}", "、".join(v["top3"]),
                        "、".join(i for i in v["top3"] if v["top3_status"][i] == "in") or "（無）",
                        yes(v["top3_set_changed"]), yes(v["top3_change_beyond_ci"]), yes(v["third_place_close"])]
                       for k, v in sv["methods"].items()]), "",
             md_table(["做法"] + [f"第 {k} 名" for k in range(1, 6)],
                      [[METHOD_NAMES[k]] + [f"{r['issue']} {pct(r['share'])}±{(r['ci_high'] - r['ci_low']) / 2:.1%}"
                                            for r in v["ranking"][:5]] for k, v in sv["methods"].items()]), "",
             "### 母體老客戶比例掃描", "",
             md_table(["母體老客戶比例", "有效樣本數", "前三名", "明確在前三名內", "名單和未加權不同", "改變超出誤差"],
                      [[f"{x['base_old_share']:.0%}", f"{x['n_eff']:,.0f}", "、".join(x["top3"]),
                        "、".join(x["clear_top3"]) or "（無）", yes(x["top3_set_changed"]), yes(x["top3_change_beyond_ci"])]
                       for x in sv["sweep"]]), "",
             f"平均滿意度：未加權 {sv['satisfaction']['unweighted']:.2f}，加權 {sv['satisfaction']['weighted']:.2f}（1～5 分）。", ""]
    ev = s.get("truth_eval")
    if ev:
        cb, hb = ev["canned_by_kind"], ev["canned_by_hard_case"]
        cols = [(k, cb[k]) for k in KIND_ORDER if k in cb] + [(k, cb[k]) for k in cb if k not in KIND_ORDER]
        cols += [(f"其中 {h}", hb[h]) for h in ("pasted_reply", "no_signature") if h in hb]
        lines += ["## 4. 規則 vs 答案（只有合成資料做得到）", "",
                  "規則與產生器是同一個人寫的，以下準確度是上限；真實資料要用 300 則人工標註（`evaluate_labels`）量。", "",
                  md_table(["信件真實類型 → 處理結果", "封數"], [[k.replace("|", " → "), f"{n:,}"] for k, n in ev["outcome"].items()]), "",
                  "判成罐頭回覆的封數，依信件真實類型（罐頭判定規則見第 1 節，每一步只改一個條件）。"
                  "最後兩欄是難例，已包含在前面的類型裡：pasted_reply 是顧客把罐頭回覆貼進正文，"
                  "no_signature 是沒有簽名檔的客服回覆（簽名規則排除不了，只能靠罐頭判定）。", "",
                  md_table(["規則"] + [f"{k}（{c['total']:,}）" for k, c in cols],
                           [[name] + [f"{c[k]:,}" for _, c in cols] for k, name in CANNED_RULES]), ""]
        cc = ev["canned_customers"]
        if cc["n"]:
            lines += [f"被當成罐頭排除的顧客信（含重寄）{cc['n']:,} 封，其中 pasted_reply {cc['pasted_reply']:,} 封。"
                      f"顧客自己寫了 {cc['own_chars_min']}～{cc['own_chars_max']} 字（不到 12 字的 {cc['own_chars_under_12']:,} 封），"
                      "被排除是因為其餘幾行是常見句，扣掉常見行後剩不到 12 字；"
                      f"{cc['n'] - cc['with_issues']:,} 封沒有提到任何問題。", ""]
        lines += [md_table(["檢查（納入的顧客信）", "結果"], [
                      ["顧客描述問題的行被誤刪", f"{ev['issue_lines_lost']:,} / {ev['issue_lines']:,}"],
                      ["顧客正文行被刪（含問候、結尾語）", f"{ev['body_lines_lost']:,} / {ev['body_lines']:,}"],
                      ["仍殘留非顧客寫的行（多為只有人名的簽名）",
                       f"{ev['included_customer_emails_with_leaked_lines']:,} 封（{ev['leaked_lines']:,} 行）"],
                      ["貼上的罐頭回覆行沒刪掉", f"{ev['pasted_template_lines_left']:,}"],
                  ]), "",
                  md_table(["難例", "封數", "顧客信被排除", "客服信被納入", "納入但標籤與答案不同", "其中多標", "其中漏標",
                            "多標的問題"],
                           [[h, f"{v['n']:,}", f"{v['wrongly_excluded']:,}", f"{v['wrongly_included']:,}",
                             f"{v['tags_wrong']:,}", f"{v['tags_extra']:,}", f"{v['tags_missing']:,}",
                             "、".join(f"{i} {n:,}" for i, n in v["extra_issues"].items()) or "—"]
                            for h, v in ev["hard_cases"].items()]), "",
                  "一封信可以同時多標和漏標，所以「其中多標」加「其中漏標」可能大於「標籤不同」。", "",
                  "清理完全正確時的佔比：只拿顧客自己寫的行跑同一套分類規則。估計佔比和它的差距是清理造成的，"
                  "它和真實佔比的差距是分類規則（關鍵詞沒涵蓋的說法、否定）造成的。", "",
                  md_table(["問題", "precision", "recall", "真實佔比（排名）", "清理完全正確時的佔比", "估計佔比（排名）"],
                           [[r["issue"], ratio(r["precision"]), ratio(r["recall"]),
                             f"{pct(r['true_share'])}（{r['true_rank']}）", pct(r["oracle_share"]),
                             f"{pct(r['est_share'])}（{r['est_rank']}）"] for r in ev["per_issue"]]), "",
                  f"真實前三名：{'、'.join(ev['true_top3'])}；估計前三名：{'、'.join(r['issue'] for r in em['table'][:3])}。", ""]
    lc = s.get("label_check")
    if lc:
        actual = {r["issue"]: r for r in ev["per_issue"]}
        ex = lc["exclusion"]
        lines += [f"### {s['sample']['size']:,} 則抽樣標註能估到什麼程度（用答案模擬完全正確的標註者）", "",
                  "走和真實標註同一條路：`evaluate_labels` 算加權 precision／recall（只用納入分析的層），"
                  "`estimate_exclusion_errors` 用 human_is_agent 估計誤刪與誤收。真實標註者會犯錯，所以這是上限。", "",
                  md_table(["問題", "precision 估計（規則標的則數）", "precision 全體實際", "recall 估計（人工標的則數）",
                            "recall 全體實際"],
                           [[i, f"{ratio(v['precision'])}（{v['n_auto']}）", ratio(actual[i]["precision"]),
                             f"{ratio(v['recall'])}（{v['n_human']}）", ratio(actual[i]["recall"])]
                            for i, v in lc["tags"].items()]), "",
                  md_table(["層", "層大小", "已標", "錯誤則數", "估計錯誤率（95% CI）", "推回全體（封）", "全體實際（封）"],
                           [[h["stratum"], f"{h['stratum_size']:,}", f"{h['labeled']:,}", f"{h['errors']:,}",
                             f"{pct(h['rate'])}（{pct(h['ci_low'])}～{pct(h['ci_high'])}）", f"{h['est_emails']:,.0f}",
                             f"{lc['actual']['excluded_customers'][h['stratum']]:,}"]
                            for h in ex["strata"] if h["excluded"] and h["labeled"]]), "",
                  "排除層的錯誤＝人工判為顧客信（誤刪）。" + (
                      f"納入分析的層合計：已標 {ex['included']['labeled']:,} 則，判為非顧客信 {ex['included']['errors']:,} 則，"
                      f"推回全體約 {ex['included']['est_emails']:,.0f} 封（全體實際 {lc['actual']['included_non_customers']:,} 封）。"
                      if ex["included"] else ""), ""]
    return "\n".join(lines)


def run(data, out, base_old_share=0.5, sample_size=300, seed=20261030):
    t0 = time.perf_counter()
    data, out = Path(data), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    emails = read_jsonl(data / "emails.jsonl")
    records, stats, clusters, table, untagged, ablation, unpaired_top, pairs = email_stage(emails)
    id_of = [r["email_id"] for r in records]
    cluster_rows = [{"cluster": k, "size": c["size"], "lines": c["lines"],
                     "example_email_id": id_of[c["members"][0]]} for k, c in enumerate(clusters, 1)]

    sample = label_sample(records, sample_size, seed)
    it.export_label_sample(sample, out / f"label_sample_{sample_size}.csv")

    srows, counts, methods, sweep, sat = survey_stage(data / "survey.csv", base_old_share)
    w = methods["weighted"]

    cov_rows = [
        cov.email_row(stats),
        cov.survey_row(len(srows), counts, base_old_share, w["n_eff"], sw.OLD, sw.NEW, sw.UNKNOWN),
        cov.dropped_row("社群貼文", SOCIAL_POSTS, "整個通路不納入：業配與同一批帳號洗版，三天內無法可靠區分",
                        "結論會低估潛在客戶與公開抱怨的問題"),
        cov.qualitative_row("社群貼文抽樣", SOCIAL_QUAL_SAMPLE, "人工定性檢視，看有沒有信件與問卷沒出現的大問題"),
    ]
    top_issues = [r["issue"] for r in table[:3]]
    for issue in w["top3"]:
        if issue not in top_issues:
            top_issues.append(issue)
    sources = cov.source_rows(top_issues, table, stats["included"], w["ranking"], w["n_eff"])
    cov.write_csv(cov_rows, out / "coverage.csv")
    (out / "coverage.md").write_text(
        "> 合成資料示範，不是真實數字。\n\n" + cov.render_markdown(cov_rows) + "\n" + cov.render_sources(sources),
        encoding="utf-8")

    write_rows(out / "email_mentions.csv", [dict(r, untagged=untagged) for r in table],
               ("rank", "issue", "mentions", "share", "ci_low", "ci_high"))
    write_rows(out / "email_ablation.csv",
               [{"method": k, "n": v["n"], "rank": r["rank"], "issue": r["issue"], "mentions": r["mentions"],
                 "share": r["share"]} for k, v in ablation.items() for r in v["table"]],
               ("method", "n", "rank", "issue", "mentions", "share"))
    write_rows(out / "canned_clusters.csv",
               [{"cluster": c["cluster"], "size": c["size"], "example_email_id": c["example_email_id"],
                 "lines": " / ".join(c["lines"][:4])} for c in cluster_rows],
               ("cluster", "size", "example_email_id", "lines"))
    write_rows(out / "survey_methods.csv",
               [{"method": k, "n": v["n"], "n_eff": round(v["n_eff"], 1), "rank": r["rank"], "issue": r["issue"],
                 "share": round(r["share"], 4), "ci_low": round(r["ci_low"], 4), "ci_high": round(r["ci_high"], 4),
                 "top3_status": v["top3_status"][r["issue"]]}
                for k, v in methods.items() for r in v["ranking"]],
               ("method", "n", "n_eff", "rank", "issue", "share", "ci_low", "ci_high", "top3_status"))
    write_rows(out / "survey_sweep.csv",
               [{"base_old_share": x["base_old_share"], "n_eff": round(x["n_eff"], 1), "top3": "|".join(x["top3"]),
                 "clear_top3": "|".join(x["clear_top3"]), "top3_set_changed": int(x["top3_set_changed"]),
                 "top3_change_beyond_ci": int(x["top3_change_beyond_ci"])} for x in sweep],
               ("base_old_share", "n_eff", "top3", "clear_top3", "top3_set_changed", "top3_change_beyond_ci"))

    summary = {
        "synthetic": True,
        "email": {"stats": stats, "table": table, "untagged": untagged, "ablation": ablation,
                  "clusters": cluster_rows, "unpaired_top": unpaired_top, "template_pairs": pairs},
        "survey": {"base_old_share": base_old_share, "counts": counts, "satisfaction": sat,
                   "methods": {k: {kk: vv for kk, vv in v.items()} for k, v in methods.items()},
                   "sweep": sweep},
        "sample": {"size": len(sample), "seed": seed,
                   "strata": dict(Counter(r["stratum"] for r in sample))},
        "coverage": {"rows": cov_rows, "sources": sources},
    }
    truth_path = data / "emails_truth.jsonl"
    if truth_path.exists():
        truth = read_jsonl(truth_path)
        summary["truth_eval"] = evaluate_against_truth(records, truth, table)
        summary["label_check"] = simulated_label_check(records, sample, truth)
    summary["elapsed_sec"] = time.perf_counter() - t0
    (out / "report.md").write_text(render_report(summary), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main():
    ap = argparse.ArgumentParser(description="Q1 原型：信件清理、問題分類、問卷加權、覆蓋表")
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--base-old-share", type=float, default=0.5,
                    help="顧客母體中購買滿 6 個月以上的比例（假設參數，正式報告改用 CRM 實際分布）")
    ap.add_argument("--sample-size", type=int, default=300,
                    help=f"標註抽樣則數；排除層固定各抽 {EXCLUDED_PER_STRATUM} 則，所以至少要 "
                         f"{EXCLUDED_PER_STRATUM * len(EXCLUDED_STRATA)}")
    ap.add_argument("--seed", type=int, default=20261030)
    a = ap.parse_args()
    s = run(a.data, a.out, a.base_old_share, a.sample_size, a.seed)
    st = s["email"]["stats"]
    print(f"emails total={st['total']:,} included={st['included']:,} canned={st['excluded_canned_template']:,} "
          f"agent={st['excluded_agent_signature']:,} dup={st['excluded_duplicate']:,} empty={st['excluded_empty']:,}")
    print("canned rules A/B/C/D:", " / ".join(f"{st['canned_rule_counts'][k]:,}" for k, _ in CANNED_RULES))
    print("email top3:", "、".join(r["issue"] for r in s["email"]["table"][:3]))
    for k, v in s["survey"]["methods"].items():
        print(f"survey {k}: top3={'、'.join(v['top3'])} changed={v['top3_set_changed']} "
              f"beyond_ci={v['top3_change_beyond_ci']}")
    print(f"sample rows={s['sample']['size']} elapsed={s['elapsed_sec']:.1f}s out={a.out}")


if __name__ == "__main__":
    main()
