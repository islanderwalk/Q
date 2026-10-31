"""Q1 第 1、3 天：問卷依購買期間分層、事後分層加權（post-stratification）與排序敏感度檢查。

- 分層：購買滿 6 個月以上 vs 未滿 6 個月；購買期間空白的列歸「未知」，加權時權重為 0（照實揭露筆數）。
- 權重：w_h = P_h / p_h。P_h 是「顧客母體」的購買期間組成，是假設參數（base_old_share），
  正式報告要改用 CRM 的實際購買日期分布；p_h 是問卷樣本中已知分層的組成。
- 佔比：Σ w·1[勾選該問題] / Σ w；95% CI 用 Kish 有效樣本數 n_eff = (Σw)² / Σw² 做常態近似。
- 敏感度：未加權、加權、只看未滿 6 個月（排除老客戶）、只看老客戶四種排序並列，
  另掃描 base_old_share 看前三名在哪個假設下改變。
- 前三名「改變」要分兩種：名單不同（top3_set_changed），與名單不同而且超出誤差（top3_change_beyond_ci）。
  兩個佔比差距小於兩者 CI 半寬的合成值就分不出先後（is_close）；一個問題在一邊明確在前三名內、
  在另一邊明確在前三名外，才算超出誤差。只有名單不同、沒有超出誤差的改變，不能當成發現。
※ 加權只修正「購買期間」的組成偏差；同一層內「會主動填答的人」與不填答的人的差異（自選偏誤）修正不了。
"""
import csv
import math

from issue_tags import ISSUES

OLD = "滿 6 個月以上"
NEW = "未滿 6 個月"
UNKNOWN = "未知"
STRATA = (OLD, NEW)


def stratum(tenure_months):
    if tenure_months is None:
        return UNKNOWN
    return OLD if tenure_months >= 6 else NEW


def load_survey(path):
    """讀 survey.csv：resp_id, tenure_months（可空白）, satisfaction, issues（以 | 分隔，可空白）。"""
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            tenure = r["tenure_months"].strip()
            rows.append({"resp_id": r["resp_id"],
                         "tenure_months": int(tenure) if tenure else None,
                         "satisfaction": int(r["satisfaction"]),
                         "issues": [x for x in r["issues"].split("|") if x]})
    for r in rows:
        r["stratum"] = stratum(r["tenure_months"])
    return rows


def stratum_counts(rows):
    out = {h: 0 for h in STRATA + (UNKNOWN,)}
    for r in rows:
        out[r["stratum"]] += 1
    return out


def poststrat_weights(rows, base_old_share):
    """回傳每列權重；未知分層為 0。樣本某層為 0 時無法加權，丟 ValueError。"""
    if not 0 <= base_old_share <= 1:
        raise ValueError("base_old_share 必須介於 0 與 1")
    counts = stratum_counts(rows)
    known = counts[OLD] + counts[NEW]
    if counts[OLD] == 0 or counts[NEW] == 0:
        raise ValueError("樣本缺少某一層，無法事後分層加權")
    target = {OLD: base_old_share, NEW: 1 - base_old_share}
    factor = {h: target[h] / (counts[h] / known) for h in STRATA}
    return [factor.get(r["stratum"], 0.0) for r in rows]


def kish_neff(weights):
    s, s2 = sum(weights), sum(w * w for w in weights)
    return s * s / s2 if s2 else 0.0


def weighted_ranking(rows, weights):
    """回傳依佔比排序的 [{"rank", "issue", "share", "ci_low", "ci_high"}] 與 n_eff。"""
    total = sum(weights)
    n_eff = kish_neff(weights)
    shares = {}
    for issue in ISSUES:
        hit = sum(w for r, w in zip(rows, weights) if issue in r["issues"])
        shares[issue] = hit / total if total else 0.0
    order = sorted(ISSUES, key=lambda i: (-shares[i], ISSUES.index(i)))
    out = []
    for rank, issue in enumerate(order, 1):
        p = shares[issue]
        half = 1.96 * math.sqrt(p * (1 - p) / n_eff) if n_eff else 0.0
        out.append({"rank": rank, "issue": issue, "share": p,
                    "ci_low": max(0.0, p - half), "ci_high": min(1.0, p + half)})
    return out, n_eff


def top3(ranking):
    return [r["issue"] for r in ranking[:3]]


def is_close(a, b):
    """兩個佔比的差距是否小於兩者 CI 半寬的合成值（分不出先後）。粗略準則：沒有考慮同一批樣本內的共變異。"""
    half_a, half_b = (a["ci_high"] - a["ci_low"]) / 2, (b["ci_high"] - b["ci_low"]) / 2
    return abs(a["share"] - b["share"]) < math.sqrt(half_a ** 2 + half_b ** 2)


def third_place_is_close(ranking):
    """第 3 名與第 4 名的佔比差距是否在誤差內（代表第 3 名不穩）。"""
    return is_close(ranking[2], ranking[3])


def top3_status(ranking):
    """每個問題相對「前三名分界」的位置：in（明確在前三名內）、out（明確在外）、tie（和分界分不出先後）。

    前三名的問題和第 4 名比，其餘的和第 3 名比，用 is_close。
    """
    third, fourth = ranking[2], ranking[3]
    out = {}
    for r in ranking:
        inside = r["rank"] <= 3
        out[r["issue"]] = "tie" if is_close(r, fourth if inside else third) else ("in" if inside else "out")
    return out


def top3_change_beyond_ci(status, base_status):
    """有問題在一邊明確在前三名內、在另一邊明確在外，前三名的改變才算超出誤差。"""
    return any({status[i], base_status[i]} == {"in", "out"} for i in status)


def compare_methods(rows, base_old_share):
    """四種排序並列：未加權、事後分層加權、只看未滿 6 個月、只看滿 6 個月以上。"""
    known = [r for r in rows if r["stratum"] != UNKNOWN]
    new_only = [r for r in rows if r["stratum"] == NEW]
    old_only = [r for r in rows if r["stratum"] == OLD]
    methods = {
        "unweighted": (known, [1.0] * len(known)),
        "weighted": (rows, poststrat_weights(rows, base_old_share)),
        "new_only": (new_only, [1.0] * len(new_only)),
        "old_only": (old_only, [1.0] * len(old_only)),
    }
    out = {}
    for name, (rs, ws) in methods.items():
        ranking, n_eff = weighted_ranking(rs, ws)
        out[name] = {"n": sum(1 for w in ws if w > 0), "n_eff": n_eff, "ranking": ranking,
                     "top3": top3(ranking), "third_place_close": third_place_is_close(ranking),
                     "top3_status": top3_status(ranking)}
    base = out["unweighted"]
    for name in out:
        out[name]["top3_set_changed"] = set(out[name]["top3"]) != set(base["top3"])
        out[name]["top3_order_changed"] = out[name]["top3"] != base["top3"]
        out[name]["top3_change_beyond_ci"] = top3_change_beyond_ci(out[name]["top3_status"], base["top3_status"])
    return out


def sensitivity_sweep(rows, shares=None):
    """掃描母體老客戶比例，回傳 [{"base_old_share", "top3", "n_eff", "clear_top3", "top3_set_changed",
    "top3_change_beyond_ci"}]；clear_top3 是明確在前三名內的問題，改變都是和未加權比。"""
    shares = shares or [round(0.20 + 0.05 * k, 2) for k in range(13)]  # 0.20～0.80
    known = [r for r in rows if r["stratum"] != UNKNOWN]
    base_ranking, _ = weighted_ranking(known, [1.0] * len(known))
    base_status = top3_status(base_ranking)
    out = []
    for s in shares:
        ranking, n_eff = weighted_ranking(rows, poststrat_weights(rows, s))
        status = top3_status(ranking)
        out.append({"base_old_share": s, "top3": top3(ranking), "n_eff": n_eff,
                    "clear_top3": [i for i in top3(ranking) if status[i] == "in"],
                    "top3_set_changed": set(top3(ranking)) != set(top3(base_ranking)),
                    "top3_change_beyond_ci": top3_change_beyond_ci(status, base_status)})
    return out


def weighted_mean_satisfaction(rows, weights):
    total = sum(weights)
    return sum(w * r["satisfaction"] for r, w in zip(rows, weights)) / total if total else None
