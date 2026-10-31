"""Q1 客服信件清理的離線測試（不連網；語料是測試內手寫的合成資料）。"""
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))

import email_clean as ec  # noqa: E402
import issue_tags as it  # noqa: E402

BODY = "客服您好\n同步後筆記不見了\n請協助處理，謝謝。"

# 4 行的罐頭模板；案件編號的數字正規化後相同
TEMPLATE = ["感謝您的來信，我們已收到您的問題。", "您的案件編號為 C{n}，專人將於 3 個工作天內回覆。",
            "若仍收不到驗證碼，請檢查垃圾郵件匣。", "如有其他問題，歡迎隨時與我們聯繫。"]
# 顧客常見句：每句出現 20 封，但彼此獨立搭配（相鄰共同出現率約 0.35）
COMMON = ["已經重新安裝過了", "工作上很需要，拜託幫忙", "我是付費會員，用了一年多"]
CLOSING = ["請協助處理，謝謝。", "請問要怎麼處理？", "麻煩盡快回覆給我"]
SUBJ = ["筆記", "照片", "行事曆", "草稿", "待辦清單", "共用資料夾", "錄音檔", "表格", "相簿", "書籤"]
SYMP = ["同步後不見了", "打開就閃退", "載入很慢", "一直轉圈圈", "匯出格式跑掉", "被覆蓋掉了"]


def canned(j, drop=None):
    lines = [t.format(n=100000 + j) for k, t in enumerate(TEMPLATE) if k != drop]
    return "\n".join(["王小明 您好："] + lines)


def customer(i):
    return "\n".join(["您好", SUBJ[i % 10] + SYMP[i // 10], COMMON[i % 3], CLOSING[(i // 3) % 3]])


def corpus():
    return [canned(j) for j in range(30)] + [customer(i) for i in range(60)]


# ---------- 轉寄引文 ----------

def test_strip_quotes_handles_common_headers():
    headers = [
        ["-----Original Message-----", "From: 範例雲客服 <service@example.com>", "Subject: RE: 問題"],
        ["寄件者: 範例雲客服 <service@example.com>", "收件者: user@example.com"],
        ["於 2026年3月2日 週一 上午10:15，範例雲客服 <service@example.com> 寫道：", "> 感謝您的來信"],
        ["On Mon, Mar 2, 2026 at 10:15 AM Support <service@example.com> wrote:", "> Thanks"],
        ["On Mon, Mar 2, 2026 at 10:15 AM Support <", "service@example.com> wrote:", "> Thanks"],
        ["---------- Forwarded message ---------", "From: Support <service@example.com>"],
    ]
    for head in headers:
        text = BODY + "\n\n" + "\n".join(head + ["若同步後資料不見，請先確認帳號。"])
        kept, info = ec.strip_quotes(text)
        assert kept == BODY, head[0]
        assert info["quote_header"] and info["quoted_lines"] >= 2, head[0]


def test_strip_quotes_keeps_body_that_starts_with_from():
    text = "From: 我同事說這個功能壞了\n同步後筆記不見了"
    kept, info = ec.strip_quotes(text)
    assert kept == text and not info["quote_header"]


def test_inline_quoted_lines_removed():
    kept, info = ec.strip_quotes("> 上次說會修\n還是一樣閃退")
    assert kept == "還是一樣閃退" and info["quoted_lines"] == 1


# ---------- 簽名檔 ----------

def test_strip_signature_delimiter_and_closing_blocks():
    assert ec.strip_signature("App 打開就閃退\n--\n王小明\n0900-000-000") == (
        "App 打開就閃退", ["王小明", "0900-000-000"])
    assert ec.strip_signature("App 打開就閃退\n\n謝謝！\n陳怡君") == ("App 打開就閃退", ["謝謝！", "陳怡君"])
    agent = "我們會在下一版修正。\n\n範例雲客服中心\n客服專員 林小美\n電話：(02) 0000-1234\nservice@example.com"
    kept, sig = ec.strip_signature(agent)
    assert kept == "我們會在下一版修正。" and len(sig) == 4


def test_signature_keeps_body_lines_with_contact_words():
    text = "我的手機收不到驗證碼\n客服專員說會回電但沒有"
    assert ec.strip_signature(text) == (text, [])


def test_name_only_line_is_weak_and_kept():
    assert ec.strip_signature("App 打開就閃退\n王小明") == ("App 打開就閃退\n王小明", [])


# ---------- 罐頭回覆 ----------

def test_template_pairs_found_and_customer_common_lines_unpaired():
    det = ec.TemplateDetector().fit(corpus())
    assert det.min_df == 5
    assert len(det.template_pairs) == 3  # L1→L2、L2→L3、L3→L4
    unpaired = set(det.unpaired_lines())
    assert {ec.normalize_line(x) for x in COMMON + CLOSING} <= unpaired
    assert det.is_canned(canned(99))
    assert not any(det.is_canned(customer(i)) for i in range(60))


def test_customer_email_of_only_common_lines_is_not_canned():
    """整封都是常見行（只看行頻率會判成罐頭），但沒有成塊的模板行，所以保留。"""
    common_only = "您好\n" + COMMON[0] + "\n" + CLOSING[1]
    det = ec.TemplateDetector().fit(corpus() + [common_only])
    a = det.analyze(common_only)
    assert a["coverage"] == 1.0 and len(a["hits"]) == 2
    assert not det.is_canned(common_only)
    # 拿掉相鄰共同出現率的門檻，就會誤判
    loose = ec.TemplateDetector(min_pair_conf=0.0).fit(corpus() + [common_only])
    assert loose.is_canned(common_only)


def test_clean_corpus_counts_and_pasted_reply():
    # 顧客把罐頭回覆貼進正文，而且模板少了第 2 行（第 1 行湊不成相鄰對，要靠「緊鄰區塊」併入）
    pasted = "\n".join(["您好", "照片打開就閃退，很困擾", "你們上次回覆我："]
                       + [TEMPLATE[k].format(n=7) for k in (0, 2, 3)])
    forward_only = "\n-----Original Message-----\nFrom: service@example.com\n感謝您的來信，我們已收到您的問題。"
    resend = customer(0) + "\n\n從我的 iPhone 傳送"
    agent = "王小明 您好：\n我們會在下一版修正這個問題。\n\n範例雲客服中心\n客服專員 林小美"
    records, stats, clusters = ec.clean_corpus(corpus() + [pasted, forward_only, resend, agent])
    assert stats["total"] == 94
    assert stats["excluded_canned_template"] == 30
    assert stats["excluded_empty"] == 1
    assert stats["excluded_duplicate"] == 1
    assert stats["excluded_agent_signature"] == 1
    assert stats["included"] == 61
    assert stats["canned_clusters"] == 1 and clusters[0]["size"] == 30
    assert stats["included_with_pasted_template"] == 1 and stats["pasted_template_lines_removed"] == 3
    p = records[90]
    assert p["excluded"] is None and p["text"] == "您好\n照片打開就閃退，很困擾\n你們上次回覆我："
    # 沒刪模板的話，「驗證碼」那行會多標一個登入問題
    assert it.tag_text(p["stripped_text"]) == ["閃退與當機", "登入與帳號"]
    assert it.tag_text(p["text"]) == ["閃退與當機"]


def test_known_limit_pasted_reply_dropped_when_little_text_is_left_after_common_lines():
    """已知限制：顧客貼上罐頭回覆，而且自己寫的內容扣掉常見句後不到 12 字，會被當成罐頭回覆排除。

    判斷看的是「常見行以外的字數」，不是顧客寫了多少字。40,000 封實跑被誤排除的 53 封，顧客自己都寫了
    27～73 字，但其餘幾行是背景句、「想詢問企業方案的價格」這類常見句，連「你們上次回覆我：」本身也是常見行。
    """
    short = "\n".join(["您好", "新版看不懂。"] + TEMPLATE).format(n=1)
    # 自己寫了 25 字（不含問候），但「已經重新安裝過了」「工作上很需要，拜託幫忙」在語料裡各出現 20 封，是常見行
    common = "\n".join(["您好", COMMON[0], COMMON[1], "新版看不懂。"] + TEMPLATE).format(n=2)
    records, _, _ = ec.clean_corpus(corpus() + [short, common])
    assert [r["excluded"] for r in records[-2:]] == ["canned_template", "canned_template"]
    det = ec.TemplateDetector().fit(corpus() + [short, common])
    own = sum(len(ec.normalize_line(x)) for x in (COMMON[0], COMMON[1], "新版看不懂。"))
    assert own == 25 and det.analyze(common)["residual_chars"] == 6
