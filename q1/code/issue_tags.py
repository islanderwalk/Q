"""Q1 第 2 步：規則式問題分類、提及數與佔比，以及 300 則分層抽樣（人工標註用）。

分類規則：
- 每個候選問題有一組關鍵詞（TAXONOMY），英文不分大小寫，全形半形先以 NFKC 統一。
- 信件先切成子句（標點、換行、「但是／可是／不過」）。同一子句若出現否定語（沒問題、正常、已解決…），
  該子句的關鍵詞不算提及。例：「登入都正常，但是同步後資料不見」只算「同步與資料遺失」。
  「沒辦法正常登入」「同步沒有正常運作」的「正常」前面有否定詞，是問題描述，不算否定；
  「有沒有問題」是疑問，也不算否定。
- 一封信可以提及多個問題；提及數以「封」計，不以關鍵詞出現次數計。
※ 關鍵詞是依合成資料的用語寫的。正式信件要先用人工標註的 300 則校正（evaluate_labels），再定案。
"""
import csv
import math
import random
import re
import unicodedata

TAXONOMY = {
    "閃退與當機": ("閃退", "當機", "強制關閉", "白畫面", "卡死", "crash"),
    "同步與資料遺失": ("同步", "不見", "遺失", "消失", "被覆蓋", "資料沒了"),
    "付款與扣款": ("扣款", "多扣", "重複扣", "退款", "信用卡", "帳單", "付款失敗"),
    "登入與帳號": ("登入", "登不進", "密碼", "驗證碼", "帳號被鎖"),
    "速度與效能": ("很慢", "好慢", "載入", "轉圈", "延遲", "等很久", "lag"),
    "介面與操作": ("找不到", "介面", "不直覺", "操作很複雜", "看不懂", "按鈕"),
    "通知與提醒": ("通知", "推播", "提醒"),
    "匯出與報表": ("匯出", "報表", "excel", "csv"),
}
ISSUES = tuple(TAXONOMY)
UNTAGGED = "未分類"
EXCLUDED_PREFIX = "排除"  # 抽樣層名稱以此開頭的是被清理排除的信（罐頭回覆、客服回覆），不是納入分析的信

CLAUSE_SPLIT = re.compile(r"[,，、。.；;！!？?\n]|但是|可是|不過|但")
# 「正常」前面接「不／未／沒有／無法／沒辦法／不能」時是問題描述，不是否定
NEGATION = re.compile(r"(?<!有)沒有?問題|(?<!不)(?<!未)(?<!沒有)(?<!無法)(?<!沒辦法)(?<!不能)正常|已經?解決|還好")


def _norm(text):
    return unicodedata.normalize("NFKC", text).lower()


def _compile(keyword):
    kw = _norm(keyword)
    if kw.isascii():
        # 英文關鍵詞要整字比對，避免 flag 裡的 lag
        return re.compile(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])")
    return re.compile(re.escape(kw))


_PATTERNS = {issue: [_compile(k) for k in kws] for issue, kws in TAXONOMY.items()}


def tag_text(text):
    """回傳這封信提及的問題（依 TAXONOMY 順序）；沒有命中回傳空 list。"""
    found = set()
    for clause in CLAUSE_SPLIT.split(_norm(text)):
        if not clause.strip() or NEGATION.search(clause):
            continue
        for issue, pats in _PATTERNS.items():
            if issue not in found and any(p.search(clause) for p in pats):
                found.add(issue)
    return [i for i in ISSUES if i in found]


def wilson(k, n, z=1.96):
    """比例的 Wilson 95% 信賴區間；n=0 回傳 (0, 0)。"""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def mention_table(tag_lists):
    """每個問題的提及封數、佔比（分母＝納入的信件數）、Wilson 95% CI 與排名。

    回傳 (rows, untagged)。rows 依提及數由多到少排序，同數依 TAXONOMY 順序。
    """
    total = len(tag_lists)
    counts = {i: 0 for i in ISSUES}
    untagged = 0
    for tags in tag_lists:
        if not tags:
            untagged += 1
        for t in tags:
            counts[t] += 1
    order = sorted(ISSUES, key=lambda i: (-counts[i], ISSUES.index(i)))
    rows = []
    for rank, issue in enumerate(order, 1):
        lo, hi = wilson(counts[issue], total)
        rows.append({"rank": rank, "issue": issue, "mentions": counts[issue],
                     "share": counts[issue] / total if total else 0.0, "ci_low": lo, "ci_high": hi})
    return rows, untagged


# ---------- 300 則分層抽樣（人工標註） ----------

def primary_stratum(tags):
    """分層用的主要標籤：依 TAXONOMY 順序的第一個標籤，沒有標籤歸「未分類」。"""
    return tags[0] if tags else UNTAGGED


def allocate(sizes, total, min_per=10):
    """每層先給 min_per 則（不超過該層大小），剩下的依各層「剩餘大小」比例分配，以最大餘數法補足到 total。

    這不是「純比例配置再補到下限」：小層會比純比例多、大層少（300 則的實跑：同步層 44 則，純比例是 52 則）。
    各層的抽樣權重是 N_h / n_h，估計時會加權回來，所以配置方式不影響估計的偏誤，只影響各層的精度。
    """
    strata = [h for h in sizes if sizes[h] > 0]
    total = min(total, sum(sizes[h] for h in strata))
    if min_per * len(strata) > total:
        min_per = 0
    alloc = {h: min(sizes[h], min_per) for h in strata}
    remaining = total - sum(alloc.values())
    while remaining > 0:
        cap = {h: sizes[h] - alloc[h] for h in strata if sizes[h] > alloc[h]}
        cap_sum = sum(cap.values())
        quota = {h: remaining * cap[h] / cap_sum for h in cap}
        for h in cap:
            alloc[h] += min(cap[h], int(quota[h]))
        left = total - sum(alloc.values())
        for h in sorted(cap, key=lambda h: (quota[h] - int(quota[h]), sizes[h]), reverse=True):
            if left == 0:
                break
            if alloc[h] < sizes[h]:
                alloc[h] += 1
                left -= 1
        remaining = left
    return alloc


def stratified_sample(records, size=300, min_per=10, seed=20261030, fixed=None):
    """records：[{"email_id", "tags", "text"}，可另帶 "stratum"]。回傳抽樣列（含層大小與抽樣權重 N_h/n_h）。

    有 "stratum" 的列用它分層，其餘用 primary_stratum。run_q1 把被排除的信（罐頭回覆、客服發出的信）
    以「排除：…」層一起抽，讓標註者也檢查清理有沒有誤刪顧客信（estimate_exclusion_errors）。
    fixed：{層: 固定抽幾則}，這些層不參與配置（例如排除層各抽 20 則，其餘 260 則交給 allocate）。
    size 小於固定層合計時丟 ValueError，不會默默多抽。
    """
    groups = {}
    for r in records:
        groups.setdefault(r.get("stratum") or primary_stratum(r["tags"]), []).append(r)
    sizes = {h: len(v) for h, v in groups.items()}
    fixed = {h: min(n, sizes[h]) for h, n in (fixed or {}).items() if h in sizes}
    if size < sum(fixed.values()):
        raise ValueError(f"抽樣數 {size} 小於固定層合計 {sum(fixed.values())}（{fixed}）")
    rest = {h: n for h, n in sizes.items() if h not in fixed}
    alloc = {**allocate(rest, size - sum(fixed.values()), min_per), **fixed}
    rng = random.Random(seed)
    rows = []
    for h in sorted(alloc, key=lambda h: (h.startswith(EXCLUDED_PREFIX), h == UNTAGGED, h)):
        picked = rng.sample(groups[h], alloc[h])
        for r in sorted(picked, key=lambda r: r["email_id"]):
            rows.append({"email_id": r["email_id"], "stratum": h, "stratum_size": sizes[h],
                         "sample_n": alloc[h], "weight": sizes[h] / alloc[h],
                         "auto_tags": "|".join(r["tags"]), "text": r["text"]})
    for k, row in enumerate(rows, 1):
        row["sample_id"] = f"S{k:03d}"
    return rows


LABEL_COLUMNS = ("sample_id", "email_id", "stratum", "stratum_size", "sample_n", "weight",
                 "auto_tags", "text", "human_tags", "human_is_agent", "note")


def export_label_sample(rows, path, max_chars=200):
    """寫出給標註者的 CSV（utf-8-sig，Excel 可直接開）。human_* 與 note 欄留空。"""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LABEL_COLUMNS)
        w.writeheader()
        for r in rows:
            out = {k: r.get(k, "") for k in LABEL_COLUMNS}
            out["text"] = r["text"].replace("\n", " / ")[:max_chars]
            out["weight"] = f"{r['weight']:.4f}"
            w.writerow(out)


AGENT_YES = {"1", "y", "yes", "true", "是"}
AGENT_NO = {"", "0", "n", "no", "false", "否"}


def read_labels(path):
    """讀回標註完成的 CSV：{email_id: {"tags": set(問題), "is_agent": bool}}。

    標註規則（q1/data/README.md 有同樣的說明，標註前要先給標註者看）：
    - human_tags 與 human_is_agent 都空白＝還沒標，這列略過。
    - 顧客信的 human_tags 一定要填，沒有提到任何問題填「未分類」。human_is_agent 填 0 但 human_tags 空白丟 ValueError；
      兩欄都空白分不出「沒標」和「沒有問題」，只能略過，如果其實是沒有問題的信，precision 會高估。
    - human_tags 用 | 分隔，只能填 TAXONOMY 的問題名稱或「未分類」，其他字（打錯字）丟 ValueError，不默默略過。
    - 不是顧客寫的信（罐頭回覆、客服個別回覆、系統信）human_is_agent 填 1，human_tags 可以空白（填了也不算）；
      顧客信填 0 或留白。排除層（罐頭回覆、客服回覆）的列，標註者主要就是判斷這一欄。
    """
    labels = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            raw = (row.get("human_tags") or "").strip()
            agent = (row.get("human_is_agent") or "").strip().lower()
            if not raw and not agent:
                continue
            if agent not in AGENT_YES | AGENT_NO:
                raise ValueError(f"{row['email_id']}：human_is_agent 只能填 1 或 0，收到 {agent!r}")
            tags = {t.strip() for t in raw.split("|") if t.strip()}
            unknown = tags - set(ISSUES) - {UNTAGGED}
            if unknown:
                raise ValueError(f"{row['email_id']}：human_tags 有不認得的問題名稱 {sorted(unknown)}")
            is_agent = agent in AGENT_YES
            if not is_agent and not tags:
                raise ValueError(f"{row['email_id']}：顧客信要填 human_tags，沒有提到問題填「{UNTAGGED}」")
            labels[row["email_id"]] = {"tags": set() if is_agent else tags - {UNTAGGED}, "is_agent": is_agent}
    return labels


def evaluate_labels(rows, labels):
    """以抽樣權重估計每個問題的規則 precision／recall（只用已標註、而且是納入分析的列）。

    precision(t) = 規則標 t 的信裡，人工也標 t 的加權比例；recall(t) = 人工標 t 的信裡，規則也標 t 的加權比例。
    人工判為非顧客信（is_agent）的列，人工標籤視為空：規則標的都算誤報。
    排除層（罐頭回覆、客服回覆）不在這裡算，它們的誤刪由 estimate_exclusion_errors 估計。
    """
    out = {}
    for t in ISSUES:
        auto_w = auto_hit = human_w = human_hit = 0.0
        n_auto = n_human = 0
        for r in rows:
            if r["email_id"] not in labels or r["stratum"].startswith(EXCLUDED_PREFIX):
                continue
            w = float(r["weight"])
            auto = t in r["auto_tags"].split("|")
            human = t in labels[r["email_id"]]["tags"]
            if auto:
                n_auto += 1
                auto_w += w
                auto_hit += w * human
            if human:
                n_human += 1
                human_w += w
                human_hit += w * auto
        out[t] = {"precision": auto_hit / auto_w if auto_w else None,
                  "recall": human_hit / human_w if human_w else None,
                  "n_auto": n_auto, "n_human": n_human}
    return out


def estimate_exclusion_errors(rows, labels):
    """用 human_is_agent 估計清理的兩種錯誤，依層大小 N_h 推回全體（每層內是簡單隨機抽樣）。

    - 排除層（stratum 以「排除」開頭）：人工判為顧客信的比例＝誤刪率；乘上 N_h 是估計被誤刪的顧客信封數。
    - 其他層（納入分析）：人工判為非顧客信的比例＝誤收率；乘上 N_h 是估計混進分析的非顧客信封數。
    每層附 Wilson 95% CI。每層只抽 20 則時，0 / 20 的上界約 16%：只抓得到很大的錯誤，抓不到 1% 以下的。
    回傳 {"strata": [每層一列], "excluded": 排除層合計, "included": 納入層合計}；沒有標註的層不算進合計。
    """
    strata = {}
    for r in rows:
        name = r["stratum"]
        h = strata.setdefault(name, {"stratum": name, "excluded": name.startswith(EXCLUDED_PREFIX),
                                     "stratum_size": int(r["stratum_size"]), "labeled": 0, "errors": 0})
        lab = labels.get(r["email_id"])
        if lab is None:
            continue
        h["labeled"] += 1
        h["errors"] += (not lab["is_agent"]) if h["excluded"] else lab["is_agent"]
    out = {"strata": [], "excluded": None, "included": None}
    for h in strata.values():
        n = h["labeled"]
        h["rate"] = h["errors"] / n if n else None
        h["ci_low"], h["ci_high"] = wilson(h["errors"], n) if n else (None, None)
        h["est_emails"] = h["rate"] * h["stratum_size"] if n else None
        out["strata"].append(h)
    for side, flag in (("excluded", True), ("included", False)):
        done = [h for h in out["strata"] if h["excluded"] == flag and h["labeled"]]
        if done:
            size = sum(h["stratum_size"] for h in done)
            est = sum(h["est_emails"] for h in done)
            out[side] = {"stratum_size": size, "labeled": sum(h["labeled"] for h in done),
                         "errors": sum(h["errors"] for h in done), "est_emails": est, "rate": est / size}
    return out
