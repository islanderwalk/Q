"""Q1 第 4 步：覆蓋範圍與揭露表（董事會報告第一頁）。

每個通路一列：原始筆數、納入計數的筆數、排除筆數與原因、處理方式、偏誤與限制、在結論裡的角色。
被放棄的通路（社群貼文）也要列出來，寫明「未納入計數」與原因；抽樣定性檢視另列一行，標「只作佐證」，
還沒做的寫「計畫，未執行」。
另有「數字來源」表：每個候選問題的數字各來自哪個通路、分母是多少。
"""
import csv

COLUMNS = ("channel", "raw", "included", "excluded", "exclusion_detail", "method", "bias", "role")
HEADERS = {
    "channel": "通路", "raw": "原始筆數", "included": "納入計數", "excluded": "排除",
    "exclusion_detail": "排除原因（筆數）", "method": "處理方式", "bias": "偏誤與限制", "role": "在結論中的角色",
}
EMAIL_REASONS = (
    ("excluded_canned_template", "罐頭回覆"),
    ("excluded_agent_signature", "客服發出的個別回覆"),
    ("excluded_duplicate", "重寄（內容相同）"),
    ("excluded_empty", "清理後無內容（只有轉寄）"),
)


def fmt_int(n):
    return f"{n:,}" if isinstance(n, int) else str(n)


def email_row(stats):
    detail = "；".join(f"{name} {stats[key]:,}" for key, name in EMAIL_REASONS if stats.get(key))
    return {
        "channel": "客服信件",
        "raw": stats["total"],
        "included": stats["included"],
        "excluded": stats["total"] - stats["included"],
        "exclusion_detail": detail or "無",
        "method": (f"去轉寄引文（{stats['with_quote']:,} 封）、去簽名檔（{stats['with_signature']:,} 封）、"
                   f"模板聚類找罐頭回覆（{stats['canned_clusters']:,} 群）、規則式問題分類"),
        "bias": "只涵蓋會寫信給客服的顧客；以「封」計，一位顧客多次來信會重複計",
        "role": "主要計數：各問題的提及封數與佔比",
    }


def survey_row(n_total, counts, base_old_share, n_eff, old_label, new_label, unknown_label):
    known = counts[old_label] + counts[new_label]
    old_pct = counts[old_label] / known if known else 0.0
    return {
        "channel": "問卷",
        "raw": n_total,
        "included": known,
        "excluded": counts[unknown_label],
        "exclusion_detail": f"購買期間空白 {counts[unknown_label]:,}（加權時權重為 0）" if counts[unknown_label] else "無",
        "method": (f"依購買期間分層（滿 6 個月以上 {counts[old_label]:,}、未滿 {counts[new_label]:,}，"
                   f"樣本老客戶 {old_pct:.0%}），事後分層加權到母體老客戶 {base_old_share:.0%}（假設），"
                   f"有效樣本數 {n_eff:,.0f}"),
        "bias": "只有主動填答者；加權只修正購買期間組成，同一層內填答者與不填答者的差異修正不了",
        "role": "佐證排序；並做敏感度檢查（排除老客戶後前三名是否改變）",
    }


def dropped_row(channel, approx_raw, reason, bias):
    return {"channel": channel, "raw": approx_raw, "included": 0, "excluded": approx_raw,
            "exclusion_detail": reason, "method": "未處理", "bias": bias, "role": "未納入計數"}


def qualitative_row(channel, planned_n, purpose, done=False):
    """抽樣定性檢視一列。done=False（本原型的情況：沒有社群資料，沒有做）時筆數欄留「—」，
    處理方式寫「計畫，未執行」，避免讀者以為這 planned_n 則已經看過。"""
    method = f"{purpose}（{planned_n:,} 則）" if done else f"計畫，未執行：{purpose}（預計 {planned_n:,} 則）"
    return {"channel": channel, "raw": planned_n if done else "—", "included": 0, "excluded": 0 if done else "—",
            "exclusion_detail": "—", "method": method, "bias": "樣本小，只能看有沒有前三名以外的大問題",
            "role": "只作佐證，不納入計數"}


def render_markdown(rows, title="資料覆蓋範圍與排除說明"):
    out = [f"### {title}", "", "| " + " | ".join(HEADERS[c] for c in COLUMNS) + " |",
           "|" + "---|" * len(COLUMNS)]
    for r in rows:
        out.append("| " + " | ".join(fmt_int(r[c]) for c in COLUMNS) + " |")
    return "\n".join(out) + "\n"


def write_csv(rows, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writerow(HEADERS)
        for r in rows:
            w.writerow({c: r[c] for c in COLUMNS})


def source_rows(issues, email_table, n_email, survey_ranking, n_eff):
    """數字來源表：每個問題在客服信件與問卷的數字、分母。

    email_table：issue_tags.mention_table 的 rows；survey_ranking：survey_weight.weighted_ranking 的 ranking。
    """
    em = {r["issue"]: r for r in email_table}
    sv = {r["issue"]: r for r in survey_ranking}
    rows = []
    for issue in issues:
        e, s = em[issue], sv[issue]
        rows.append({
            "issue": issue,
            "email": f"{e['mentions']:,} / {n_email:,} 封（{e['share']:.1%}，第 {e['rank']} 名）",
            "survey": f"加權 {s['share']:.1%}（95% CI {s['ci_low']:.1%}～{s['ci_high']:.1%}，第 {s['rank']} 名，n_eff {n_eff:,.0f}）",
        })
    return rows


def render_sources(rows, title="各數字的樣本來源"):
    out = [f"### {title}", "", "| 問題 | 客服信件（提及封數／納入封數） | 問卷（勾選比例，事後分層加權） |", "|---|---|---|"]
    out += [f"| {r['issue']} | {r['email']} | {r['survey']} |" for r in rows]
    return "\n".join(out) + "\n"
