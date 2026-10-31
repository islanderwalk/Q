"""Q1 第 1 步：客服信件清理（轉寄引文、簽名檔、罐頭回覆、客服發出的信、重寄）。

處理順序：
1. strip_quotes（每封信）：刪掉「>」開頭的引文行；遇到引文標頭（-----Original Message-----、
   Forwarded message、寄件者:、From:、於 … 寫道、On … wrote:）就把那一行以下全部刪掉。
   「From:／寄件者:」要同一行有 @，或後面 4 行內還有 Sent/To/Subject/收件者/主旨 等欄位才算標頭，
   避免顧客正文剛好以「From:」開頭被誤刪。
2. strip_signature（每封信）：「-- 」分隔線以下整段刪；否則由下往上找連續的簽名行
   （結尾語、聯絡方式、公司／部門、職稱、裝置簽名、機密聲明），每種都要「整行」符合，
   正文最後一行剛好含「手機」「專員」或網址不會被刪。單獨的人名行是弱訊號，區塊內至少要有一個強訊號才刪。
3. TemplateDetector（全體信件做完 1、2 之後）：行正規化（數字、網址、信箱遮罩）後算 document
   frequency，df 達門檻的是「常見行」。常見行不一定是模板，顧客也有常見句，所以再算相鄰兩行 a→b 的
   共同出現率 df(a→b) / min(df(a), df(b))，≥ 0.5 才算模板相鄰對。一封信有模板相鄰對、而且常見行以外的
   文字不到 12 字，判為罐頭回覆；有模板相鄰對但顧客自己寫了更多字（把上次的罐頭回覆貼進正文），
   納入並刪掉那幾行模板（緊鄰區塊、本身屬於某個模板相鄰對的行也一起刪，處理模板被刪掉中間一行的情況）。
   罐頭回覆依常見行集合分群（Jaccard ≥ 0.5 併群），輸出每群大小與代表句，供人工確認。
4. 簽名檔含「客服專員／客服中心／客服部／客服團隊」判為客服發出的信（非罐頭的個別回覆）。
5. 清理後內容完全相同（≥ 20 字）的重寄信只留第一封；清理後沒有內容的信排除。
"""
import math
import re
import unicodedata
from collections import Counter

# ---------- 1. 轉寄引文 ----------

QUOTE_SEPARATOR = re.compile(
    r"^\s*-{2,}\s*(original message|原始郵件|forwarded message|轉寄的郵件|轉寄郵件)\s*-{2,}\s*$", re.I)
QUOTE_WROTE_ZH = re.compile(r"^\s*於.{4,100}寫道\s*[:：]?\s*$")
QUOTE_WROTE_EN = re.compile(r"^\s*on\s.{6,160}\swrote:\s*$", re.I)
QUOTE_WROTE_EN_TAIL = re.compile(r"wrote:\s*$", re.I)
HEADER_FROM = re.compile(r"^\s*(from|寄件者|寄件人)\s*[:：]", re.I)
HEADER_FIELD = re.compile(r"^\s*(sent|to|cc|subject|date|寄件日期|收件者|收件人|副本|主旨|日期)\s*[:：]", re.I)
QUOTED_LINE = re.compile(r"^\s*[>＞]")


def _is_quote_header(lines, i):
    line = lines[i]
    if QUOTE_SEPARATOR.match(line) or QUOTE_WROTE_ZH.match(line) or QUOTE_WROTE_EN.match(line):
        return True
    # Gmail 英文標頭被折成兩行：「On Mon, … 範例雲客服 <」＋「service@example.com> wrote:」
    if (re.match(r"^\s*on\s", line, re.I) and len(line) <= 160 and i + 1 < len(lines)
            and QUOTE_WROTE_EN_TAIL.search(lines[i + 1])):
        return True
    if HEADER_FROM.match(line):
        return "@" in line or any(HEADER_FIELD.match(x) for x in lines[i + 1:i + 5])
    return False


def strip_quotes(text):
    """回傳 (保留的文字, {"quote_header": bool, "quoted_lines": int, "quoted_chars": int})。"""
    lines = text.split("\n")
    cut = next((i for i in range(len(lines)) if _is_quote_header(lines, i)), len(lines))
    kept, quoted = [], [x for x in lines[cut:] if x.strip()]
    for line in lines[:cut]:
        (quoted if QUOTED_LINE.match(line) else kept).append(line)
    return "\n".join(kept).strip(), {"quote_header": cut < len(lines), "quoted_lines": len(quoted),
                                     "quoted_chars": sum(len(x.strip()) for x in quoted)}


# ---------- 2. 簽名檔 ----------
# 簽名行分成幾種「整行」型態，每種都要整行符合才算，避免顧客正文最後一行剛好含「手機」「專員」「網址」被誤刪：
# 結尾語、裝置簽名、聯絡方式（欄位名＋電話／信箱／網址，或整行就是電話／信箱／網址）、公司名、
# 部門名、職稱＋人名、機密聲明。單獨的人名行是弱訊號（常見姓氏開頭 2～3 字，或「王先生」），
# 區塊內至少要有一個其他型態的簽名行才刪，只有人名時保留（留著也不影響問題分類）。

SIG_DELIM = re.compile(r"^\s*--\s*$")  # RFC 3676 的「-- 」分隔線
SIG_MAX_LEN = 40

SURNAMES = set(
    "陳林黃張李王吳劉蔡楊許鄭謝洪郭邱曾廖賴徐周葉蘇莊呂江何蕭羅高潘簡朱鍾游彭詹胡施沈余盧梁趙顏柯翁"
    "魏孫戴范方宋鄧杜傅侯曹薛丁卓阮馬董温溫唐藍石蔣古紀姚連馮歐程湯田康姜白汪鄒尤巫鐘黎涂龔嚴韓袁金"
    "童陸夏柳凃邵錢伍倪于譚駱熊任甘秦顧毛章史官萬俞雷粘饒闕凌崔尹孔辛武辜陶段龍韋葛池孟褚殷麥賀賈莫"
    "文管關向包丘梅華利裴樊房全佘左花")
COMPOUND_SURNAMES = ("歐陽", "司馬", "上官", "諸葛", "張簡", "范姜")
CJK = r"[一-鿿]"

SIG_CLOSING = re.compile(
    r"^(謝謝|感謝|多謝)(您|你)?\s*[!！。.,，~～]*$"
    r"|敬上\s*$|謹上\s*$|^敬祝\s*\S{0,4}$|^祝\s*(順心|商祺|平安|安好|順利)\S{0,2}$"
    r"|^(best( regards)?|kind regards|regards|thanks|thank you|sincerely|cheers)\s*[,!.]?$", re.I)
SIG_DEVICE = re.compile(r"^(從我的.{1,20}傳送|透過.{1,30}傳送|sent from my .{1,30}|get outlook for .{1,20})$", re.I)
PHONE = r"\+?\(?\d{2,4}\)?[-\s]?\d{3,4}[-\s]?\d{3,4}(\s*(分機|ext\.?|#)\s*\d{1,5})?"
EMAIL_ADDR = r"[\w.+-]+@[\w-]+\.[\w.]+"
WEB = r"(https?://|www\.)\S+"
SIG_CONTACT_ONLY = re.compile(rf"^(({PHONE}|{EMAIL_ADDR}|{WEB})\s*[|/｜、,，]?\s*)+$", re.I)
SIG_PHONE_FIELD = re.compile(rf"^(tel|fax|mobile|phone|電話|手機|傳真|專線|市話)\s*[.:：]?\s*{PHONE}$", re.I)
SIG_OTHER_FIELD = re.compile(
    rf"^((e-?mail|信箱|電子郵件)\s*[:：]\s*{EMAIL_ADDR}|(網址|網站|web)\s*[:：]\s*{WEB}"
    r"|(地址|服務時間|營業時間|客服時間|line( id)?)\s*[:：].{1,30})$", re.I)
SIG_COMPANY = re.compile(r"^\S{2,20}((股份)?有限公司|商行|工作室|co\.,?\s*ltd\.?|inc\.?|corp\.?)$", re.I)
SIG_DEPARTMENT = re.compile(r"^[^\s我你您他了過給跟打問找說的]{0,10}(客服中心|客服部|客服團隊|服務中心|客戶服務部)$")
SIG_TITLE = re.compile(r"^(\S{1,12}\s+)?(客服)?(專員|經理|主任|副理|協理|總監|工程師|組長)\s*(?P<name>\S{0,4})$")
SIG_DISCLAIMER = re.compile(r"機密.*(信件|郵件|收件者)|confidential", re.I)
AGENT_SIGNATURE = re.compile(r"客服(專員|中心|部|團隊)")


def is_name_line(s):
    """常見姓氏開頭的 2～3 字中文名（複姓 3～4 字）、「王先生／林小姐」或英文名（Allen Wang）。"""
    if re.fullmatch(r"[A-Z][a-z]+( [A-Z][a-z]+){0,2}", s):
        return True
    if re.fullmatch(rf"{CJK}{{1,2}}(先生|小姐|女士)", s):
        return s[0] in SURNAMES or s[:2] in COMPOUND_SURNAMES
    if not re.fullmatch(rf"{CJK}{{2,4}}", s):
        return False
    if s[:2] in COMPOUND_SURNAMES:
        return len(s) in (3, 4)
    return len(s) in (2, 3) and s[0] in SURNAMES


def _sig_kind(line):
    """回傳 "strong"（簽名型態）、"weak"（只有人名）或 None（正文）。"""
    s = line.strip()
    if SIG_DISCLAIMER.search(s):
        return "strong"
    if len(s) > SIG_MAX_LEN:
        return None
    for p in (SIG_CLOSING, SIG_DEVICE, SIG_CONTACT_ONLY, SIG_PHONE_FIELD, SIG_OTHER_FIELD,
              SIG_COMPANY, SIG_DEPARTMENT):
        if p.search(s):
            return "strong"
    m = SIG_TITLE.match(s)
    if m and (not m.group("name") or is_name_line(m.group("name"))):
        return "strong"
    return "weak" if is_name_line(s) else None


def strip_signature(text):
    """回傳 (保留的文字, 刪掉的簽名行 list)。"""
    lines = text.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if SIG_DELIM.match(lines[i]) and len(lines) - i <= 11:
            return "\n".join(lines[:i]).strip(), [x.strip() for x in lines[i + 1:] if x.strip()]
    end = len(lines)
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    start, strong = end, False
    while start > 0:
        s = lines[start - 1].strip()
        if s:
            kind = _sig_kind(s)
            if kind is None:
                break
            strong = strong or kind == "strong"
        start -= 1
    if not strong:
        return text.strip(), []
    return "\n".join(lines[:start]).strip(), [x.strip() for x in lines[start:end] if x.strip()]


# ---------- 3. 罐頭回覆（模板聚類） ----------

URL = re.compile(r"https?://\S+|www\.\S+", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
DIGITS = re.compile(r"\d+")
SPACES = re.compile(r"\s+")
GREETING = re.compile(r"您好|你好|^hi\b|^hello\b|^dear\b", re.I)


def normalize_line(line):
    s = unicodedata.normalize("NFKC", line).strip().lower()
    s = URL.sub("<url>", s)
    s = EMAIL.sub("<email>", s)
    s = DIGITS.sub("0", s)
    return SPACES.sub(" ", s)


def _content_lines(text):
    """正規化後的內容行；問候行（≤ 16 字且含「您好」等）不計，因為它帶著顧客姓名，每封都不同。"""
    out = []
    for raw in text.split("\n"):
        s = normalize_line(raw)
        if s and not (len(s) <= 16 and GREETING.search(s)):
            out.append(s)
    return out


class TemplateDetector:
    """罐頭回覆偵測：行頻率找「常見行」，再用相鄰兩行的共同出現率確認它們是成塊出現的模板。

    只看行頻率不夠：顧客也會寫很多一樣的句子（「請協助處理，謝謝。」「已經重新安裝過了」），
    一封顧客信可能整封都是常見行。模板的特徵是「整塊照順序出現」：相鄰兩行 a→b 一起出現的信數
    df(a→b) 接近 min(df(a), df(b))；顧客的常見句彼此獨立，df(a→b) 只有一小部分。
    """

    def __init__(self, min_df_ratio=0.005, min_df_abs=5, min_line_chars=8,
                 min_pair_conf=0.5, max_residual_chars=12, cluster_jaccard=0.5):
        self.min_df_ratio = min_df_ratio
        self.min_df_abs = min_df_abs
        self.min_line_chars = min_line_chars
        self.min_pair_conf = min_pair_conf
        self.max_residual_chars = max_residual_chars
        self.cluster_jaccard = cluster_jaccard
        self.df = Counter()
        self.pair_df = Counter()
        self.min_df = min_df_abs
        self.template_lines = set()
        self.template_pairs = {}
        self.paired_lines = set()

    def fit(self, texts):
        docs = [_content_lines(t) for t in texts]
        self.df = Counter()
        for lines in docs:
            self.df.update({s for s in lines if len(s) >= self.min_line_chars})
        self.min_df = max(self.min_df_abs, math.ceil(self.min_df_ratio * len(docs)))
        self.template_lines = {s for s, c in self.df.items() if c >= self.min_df}
        self.pair_df = Counter()
        for lines in docs:
            self.pair_df.update({(a, b) for a, b in zip(lines, lines[1:])
                                 if a != b and a in self.template_lines and b in self.template_lines})
        self.template_pairs = {}
        for (a, b), c in self.pair_df.items():
            conf = c / min(self.df[a], self.df[b])
            if c >= self.min_df and conf >= self.min_pair_conf:
                self.template_pairs[(a, b)] = conf
        self.paired_lines = {x for p in self.template_pairs for x in p}
        return self

    def unpaired_lines(self):
        """常見但不成塊的行（不屬於任何模板相鄰對），依 df 排序。多半是顧客的常見句，不刪。"""
        return sorted((s for s in self.template_lines if s not in self.paired_lines), key=lambda s: (-self.df[s], s))

    def analyze(self, text):
        """回傳 {"hits": 常見行集合, "block": 成塊模板行集合, "residual_chars": 非常見行字數, "coverage"}。"""
        lines = _content_lines(text)
        in_block = [False] * len(lines)
        for i in range(len(lines) - 1):
            if (lines[i], lines[i + 1]) in self.template_pairs:
                in_block[i] = in_block[i + 1] = True
        # 模板被刪掉中間一行時，前後兩行湊不成相鄰對：緊鄰區塊、而且本身屬於某個模板相鄰對的行也併入
        changed = True
        while changed:
            changed = False
            for i, s in enumerate(lines):
                if not in_block[i] and s in self.paired_lines and (
                        (i > 0 and in_block[i - 1]) or (i + 1 < len(lines) and in_block[i + 1])):
                    in_block[i] = changed = True
        block = {s for s, b in zip(lines, in_block) if b}
        total = sum(len(s) for s in lines)
        hit_chars = sum(len(s) for s in lines if s in self.template_lines)
        return {"hits": frozenset(s for s in lines if s in self.template_lines), "block": frozenset(block),
                "residual_chars": total - hit_chars, "coverage": hit_chars / total if total else 0.0}

    def is_canned(self, text):
        """罐頭回覆：有成塊的模板行，而且模板以外的文字不到 max_residual_chars 字。"""
        a = self.analyze(text)
        return bool(a["block"]) and a["residual_chars"] < self.max_residual_chars

    def cluster(self, line_sets):
        """把罐頭回覆依模板行集合分群。回傳 [{"size", "members", "lines"}]，依大小排序。"""
        groups = {}
        for idx, s in enumerate(line_sets):
            groups.setdefault(s, []).append(idx)
        clusters = []
        for s, members in sorted(groups.items(), key=lambda kv: (-len(kv[1]), sorted(kv[0]))):
            for c in clusters:
                if len(s & c["key"]) / len(s | c["key"]) >= self.cluster_jaccard:
                    c["members"].extend(members)
                    c["lines"].update({x: len(members) for x in s})
                    break
            else:
                clusters.append({"key": s, "members": list(members), "lines": Counter({x: len(members) for x in s})})
        # 代表句依「群內幾封有這行」排序，不用全體 df（全體 df 高的常見句會被誤當代表句）
        out = [{"size": len(c["members"]), "members": sorted(c["members"]),
                "lines": sorted(c["lines"], key=lambda x: (-c["lines"][x], x))} for c in clusters]
        return sorted(out, key=lambda c: -c["size"])


def remove_lines(text, normalized):
    """刪掉正規化後屬於 normalized 的行，回傳 (保留的文字, 刪掉的行數)。"""
    kept, removed = [], 0
    for raw in text.split("\n"):
        if raw.strip() and normalize_line(raw) in normalized:
            removed += 1
        else:
            kept.append(raw)
    return "\n".join(kept).strip(), removed


# ---------- 整批清理 ----------

def _dedupe_key(text):
    s = SPACES.sub("", unicodedata.normalize("NFKC", text))
    return s if len(s) >= 20 else None


def clean_corpus(raw_texts, detector=None):
    """回傳 (records, stats, clusters)。

    records[i]：{"text", "stripped_text"（只去引文與簽名、還沒刪模板行）, "excluded"（None 或排除原因）, "cluster", "quote_header", "quoted_lines",
    "signature_lines", "template_lines", "residual_chars", "pasted_template_lines"}。
    排除原因：canned_template、agent_signature、duplicate、empty。
    納入的信若含成塊的模板行（顧客把上次的罐頭回覆貼進正文、沒有引文標頭），刪掉那幾行再分類。
    """
    detector = detector or TemplateDetector()
    records = []
    for raw in raw_texts:
        text = raw.replace("\r\n", "\n").replace("\r", "\n")
        text, q = strip_quotes(text)
        text, sig = strip_signature(text)
        records.append({"text": text, "stripped_text": text, "excluded": None, "cluster": None,
                        "quote_header": q["quote_header"], "quoted_lines": q["quoted_lines"],
                        "quoted_chars": q["quoted_chars"], "signature_lines": sig,
                        "template_lines": 0, "residual_chars": 0, "pasted_template_lines": 0})

    detector.fit(r["text"] for r in records)
    canned_idx, canned_sets = [], []
    seen = set()
    for i, r in enumerate(records):
        a = detector.analyze(r["text"])
        r["template_lines"], r["residual_chars"] = len(a["hits"]), a["residual_chars"]
        if not r["text"]:
            r["excluded"] = "empty"
        elif a["block"] and a["residual_chars"] < detector.max_residual_chars:
            r["excluded"] = "canned_template"
            canned_idx.append(i)
            canned_sets.append(a["hits"])
        elif any(AGENT_SIGNATURE.search(x) for x in r["signature_lines"]):
            r["excluded"] = "agent_signature"
        else:
            if a["block"]:
                r["text"], r["pasted_template_lines"] = remove_lines(r["text"], a["block"])
            key = _dedupe_key(r["text"])
            if key is not None and key in seen:
                r["excluded"] = "duplicate"
            elif key is not None:
                seen.add(key)

    clusters = detector.cluster(canned_sets)
    for cid, c in enumerate(clusters, 1):
        c["members"] = [canned_idx[m] for m in c["members"]]
        for m in c["members"]:
            records[m]["cluster"] = cid

    reasons = Counter(r["excluded"] for r in records)
    included = [r for r in records if r["excluded"] is None]
    stats = {
        "total": len(records),
        "with_quote": sum(1 for r in records if r["quoted_lines"] or r["quote_header"]),
        "quoted_lines_removed": sum(r["quoted_lines"] for r in records),
        "quoted_chars_removed": sum(r["quoted_chars"] for r in records),
        "with_signature": sum(1 for r in records if r["signature_lines"]),
        "signature_lines_removed": sum(len(r["signature_lines"]) for r in records),
        "signature_chars_removed": sum(len(x) for r in records for x in r["signature_lines"]),
        "min_df": detector.min_df,
        "frequent_lines": len(detector.template_lines),
        "template_pairs": len(detector.template_pairs),
        "unpaired_frequent_lines": len(detector.unpaired_lines()),
        "canned_clusters": len(clusters),
        "excluded_canned_template": reasons.get("canned_template", 0),
        "excluded_agent_signature": reasons.get("agent_signature", 0),
        "excluded_duplicate": reasons.get("duplicate", 0),
        "excluded_empty": reasons.get("empty", 0),
        "included": reasons.get(None, 0),
        "included_with_pasted_template": sum(1 for r in included if r["pasted_template_lines"]),
        "pasted_template_lines_removed": sum(r["pasted_template_lines"] for r in included),
        # 納入的信裡仍有常見行（顧客常見句，或沒成塊的模板行）：只計數不刪
        "included_with_frequent_lines": sum(1 for r in included if r["template_lines"]),
    }
    return records, stats, clusters
