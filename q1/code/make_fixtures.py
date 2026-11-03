#!/usr/bin/env python3
"""Q1 合成資料產生器（固定 seed，只用標準函式庫）。

※ 這是合成資料，不是顧客資料。repo 裡沒有任何客服信件或問卷原文；公司「範例雲」、姓名、信箱（example.com）
  都是虛構的。電話不產生看起來像實際門號的號碼：顧客手機全部寫同一個佔位號碼 0900-000-000；
  客服市話 (02) 0000-xxxx 的用戶號碼以 0 開頭，不是可撥通的號碼。

用途：讓清理、分類、加權的程式有東西可以實跑，並且有「答案」（truth）可以量規則的誤刪、漏刪。
規則和產生器是同一個人寫的，所以合成資料上的準確度只是上限；實際準確度要用 300 則人工標註量。
產生器刻意放了規則抓不到的難例（hard_case），讓 RESULTS 看得到規則的失誤：
  pasted_reply    顧客把上次的罐頭回覆直接貼進正文，沒有引文標頭也沒有「>」
  unkeyed_wording 顧客用規則關鍵詞沒涵蓋的說法描述問題（例如「會自己跳掉」，機率 P_UNKEYED）
  negated_mention 顧客提到某功能「正常」（不算提及）
  no_signature    客服個別回覆沒有簽名檔，會被當成顧客信

產出（--out 目錄）：
  emails.jsonl          {"email_id", "text"}：純文字信件，沒有任何結構化欄位（題目設定）
  emails_truth.jsonl    {"email_id", "kind", "issues", "body_lines", "issue_lines", "noise_pasted", "hard_case", ...}：只給評估用
  emails_preview.jsonl  前 --preview 封（原文＋答案），小檔，可以 commit 供檢視
  survey.csv            resp_id, tenure_months（可空白）, satisfaction, issues（| 分隔）
  fixture_params.json   產生信件與問卷用的參數（組成比例、難例機率、各層勾選機率），只給評估用

用法：python make_fixtures.py --out DIR [--emails 40000] [--surveys 2000] [--seed 20261030]
"""
import argparse
import csv
import itertools
import json
import random
from pathlib import Path

ISSUES = ("閃退與當機", "同步與資料遺失", "付款與扣款", "登入與帳號",
          "速度與效能", "介面與操作", "通知與提醒", "匯出與報表")

# ---------- 信件組成比例（題目只說「夾雜大量罐頭回覆」，比例是假設） ----------
KIND_WEIGHTS = {"customer": 0.50, "customer_resend": 0.03, "customer_forward_only": 0.02,
                "canned": 0.35, "agent_reply": 0.10}
# 顧客信的主要問題分布（合成資料的「真相」，用來檢查估計值有沒有被清理步驟扭曲）
ISSUE_WEIGHTS = {"同步與資料遺失": 0.22, "閃退與當機": 0.18, "付款與扣款": 0.15, "登入與帳號": 0.10,
                 "速度與效能": 0.11, "介面與操作": 0.09, "通知與提醒": 0.05, "匯出與報表": 0.04, None: 0.06}
P_SECOND_ISSUE = 0.25
P_QUOTE = 0.40
P_SIGNATURE = 0.60
P_PASTED_REPLY = 0.03
P_NEGATED = 0.06
P_AGENT_NO_SIG = 0.05
P_UNKEYED = 0.12

# 顧客描述：每句由「前綴＋主詞＋症狀＋結尾」組合，同一句很少逐字重複（正式信件也是），
# 只有問候、結尾語、背景句（CONTEXT）這類常見句會大量重複，用來測罐頭偵測會不會誤判顧客信。
# 症狀分「規則關鍵詞涵蓋」與「沒涵蓋」（unkeyed_wording 難例，機率 P_UNKEYED）。
PREFIXES = ["", "我", "最近", "{when}", "從{when}開始", "這幾天", "不知道為什麼"]
TAILS = ["", "，很困擾", "，試了好幾次都一樣", "，已經第三次了", "，重開機也沒用", "！", "。", "，請幫我看一下"]
ACTIONS = ["打開 App", "編輯筆記", "上傳照片", "切換分頁", "開啟共用資料夾", "插入表格", "貼上圖片",
           "搜尋關鍵字", "新增待辦事項", "開啟行事曆", "錄音", "拖曳檔案", "掃描文件"]
SUBJECTS = {
    "閃退與當機": [a + c for a in ACTIONS for c in ("", "時", "的時候")],
    "同步與資料遺失": ["筆記", "待辦清單", "照片", "行事曆", "共用資料夾裡的檔案", "草稿", "昨天寫的內容"],
    "付款與扣款": ["這個月", "升級年費方案時", "續訂的時候", "買加購容量時", "換方案後", "上週"],
    "登入與帳號": ["", "用手機", "在電腦版", "換新手機後", "用公司網路", "出國的時候"],
    "速度與效能": [a + c for a in ACTIONS for c in ("", "時")],
    "介面與操作": ["新版", "設定頁", "分享功能", "改版後的首頁", "編輯工具列", "共用設定"],
    "通知與提醒": ["行事曆", "待辦事項", "會議", "截止日", "每週例會"],
    "匯出與報表": ["", "月底", "這週", "整理資料時", "要交給主管時"],
}
SYMPTOMS = {  # (規則關鍵詞涵蓋的說法, 沒涵蓋的說法)
    "閃退與當機": (["就閃退", "整個當機", "會被強制關閉", "變成白畫面", "畫面卡死不能動"],
                ["會自己跳掉", "直接跳回桌面"]),
    "同步與資料遺失": (["同步後不見了", "在另一台裝置同步不了", "整篇消失", "被覆蓋掉了", "整個遺失"],
                  ["在兩台裝置上對不上", "變回上個月的版本"]),
    "付款與扣款": (["被重複扣款", "信用卡被多扣了一筆", "申請退款還沒收到", "付款失敗但還是被扣錢", "帳單金額跟方案不一樣"],
                ["被收了兩次錢", "多收了一個月的費用"]),
    "登入與帳號": (["一直登不進去", "收不到驗證碼", "重設密碼的信沒有寄來", "帳號被鎖住了", "用 Google 登入一直失敗"],
                ["每次都要重新輸入帳號", "一直被踢出去"]),
    "速度與效能": (["要等很久", "載入很慢", "一直轉圈圈", "會延遲半秒", "變得好慢"],
                ["頓頓的", "要卡個十幾秒"]),
    "介面與操作": (["很不直覺", "找不到在哪裡", "看不懂", "操作很複雜", "的按鈕太小"],
                ["藏太深", "要點好幾層才找得到"]),
    "通知與提醒": (["的提醒沒有響", "時間到了沒有跳通知", "的推播一直重複跳"],
                ["到時間了手機都沒反應", "一直重複跳出來洗版"]),
    "匯出與報表": (["匯出 PDF 格式跑掉", "匯出 Excel 中文變亂碼", "的報表數字跟畫面不一樣", "匯出 CSV 少了一半"],
                ["沒辦法整批下載成檔案", "下載下來的檔案打不開"]),
}
NO_ISSUE_LINES = ["想詢問企業方案的價格", "請問可以開統一編號的發票嗎", "想了解學生方案怎麼申請",
                  "請問有沒有合作提案的窗口", "想問年繳跟月繳差多少"]
NEGATED_LINES = {"登入與帳號": "登入都正常", "閃退與當機": "App 本身沒有閃退", "同步與資料遺失": "同步沒問題",
                 "速度與效能": "速度還好", "付款與扣款": "付款那邊已經解決了"}
DEVICES = ["iPhone 15", "iPhone 13", "Pixel 8", "三星 S24", "iPad", "Windows 筆電", "MacBook"]
VERSIONS = ["3.1.0", "3.1.2", "3.2.0", "3.2.1", "3.3.0"]
WHEN = ["上週五", "這兩天", "昨天晚上", "更新之後", "這個月初", "今天早上"]
CONTEXT = ["我用的是 {dev}，App 版本 {ver}", "從{when}開始就這樣", "{dev} 上才會這樣", "已經重新安裝過了",
           "我是付費會員，用了一年多", "工作上很需要，拜託幫忙", "{when}就發生了，到現在還是一樣",
           "已經清過快取了", "換了網路也一樣"]
GREETINGS = ["您好", "客服您好", "你好", "Hi", "哈囉", "範例雲客服您好：", ""]
CLOSINGS = ["請協助處理，謝謝。", "麻煩盡快回覆", "謝謝", "請問要怎麼處理？", "急，感謝！", ""]

SURNAMES = "陳林黃張李王吳劉蔡楊許鄭謝洪郭邱曾廖賴徐周葉蘇莊呂江何蕭羅高潘"
GIVEN = "怡君志明雅婷家豪淑芬俊傑佳穎冠宇欣怡建宏美玲宗翰"
EN_NAMES = ["Allen Wang", "Vivian Lin", "Kevin Chen", "Amy Huang"]
AGENTS = ["林小美", "陳大文", "張雅琪", "王志豪", "吳佩珊"]

# ---------- 罐頭回覆模板（內容含問題關鍵詞，不排除就會灌進提及數） ----------
CANNED = {
    "T1 收件確認": ["感謝您的來信，我們已收到您的問題。", "您的案件編號為 {case}，專人將於 3 個工作天內回覆。",
                "如有其他問題，歡迎隨時與我們聯繫。"],
    "T2 登入說明": ["若您無法登入，請先確認帳號與密碼是否正確。", "忘記密碼可於登入頁點選「忘記密碼」重新設定。",
                "若仍收不到驗證碼，請檢查垃圾郵件匣。", "感謝您的耐心與配合。"],
    "T3 閃退排除": ["若 App 發生閃退或當機，請先更新至最新版本。", "更新後仍有問題，請重新開機並清除快取。",
                "若問題持續，請回信提供手機型號與 App 版本。"],
    "T4 退款流程": ["關於重複扣款或退款申請，我們已轉交帳務部門處理。", "退款將於 7 至 14 個工作天退回原信用卡。",
                "實際入帳時間依各發卡銀行作業為準。"],
    "T5 同步說明": ["若同步後資料不見，請先確認是否登入同一個帳號。", "您可在「設定 > 備份」查看最近 30 天的雲端備份。",
                "如需協助還原，請回覆本信並附上帳號。"],
    "T6 結案通知": ["您的案件 {case} 已處理完成，本案件將於 7 天後結案。",
                "誠摯邀請您填寫滿意度問卷：https://www.example.com/survey/{case}", "感謝您對範例雲的支持。"],
}
CANNED_WEIGHTS = {"T1 收件確認": 0.30, "T2 登入說明": 0.15, "T3 閃退排除": 0.15, "T4 退款流程": 0.15,
                  "T5 同步說明": 0.15, "T6 結案通知": 0.10}
AGENT_LINES = ["關於您反映的「{phrase}」，工程團隊已確認原因。", "我們會在 {ver} 版修正這個問題，請更新後再試。",
               "造成您的不便，深感抱歉。", "若還有{issue}的情況，請直接回覆本信。", "已幫您把案件升級給主管處理。"]

# ---------- 問卷：各層勾選「最想優先改善的問題」的機率（最多勾 3 項，題目沒給，是假設） ----------
SURVEY_P = {
    "old": {"同步與資料遺失": 0.40, "匯出與報表": 0.30, "速度與效能": 0.28, "閃退與當機": 0.25,
            "付款與扣款": 0.15, "介面與操作": 0.12, "登入與帳號": 0.10, "通知與提醒": 0.10},
    "new": {"登入與帳號": 0.40, "介面與操作": 0.38, "付款與扣款": 0.30, "閃退與當機": 0.25,
            "同步與資料遺失": 0.15, "速度與效能": 0.15, "通知與提醒": 0.12, "匯出與報表": 0.05},
}
SURVEY_OLD_SHARE = 0.80   # 題目：8 成是購買滿 6 個月以上
SURVEY_UNKNOWN_SHARE = 0.02
SATISFACTION_P = {"old": (0.05, 0.10, 0.25, 0.40, 0.20), "new": (0.12, 0.20, 0.33, 0.25, 0.10)}


def _pick(rng, weights):
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def _name(rng):
    return rng.choice(SURNAMES) + "".join(rng.sample(GIVEN, 2))


def _fill(rng, s, **kw):
    return s.format(dev=rng.choice(DEVICES), ver=rng.choice(VERSIONS), when=rng.choice(WHEN),
                    case=f"C{rng.randrange(10**6, 10**7)}", **kw)


PLACEHOLDER_MOBILE = "0900-000-000"  # 佔位號碼；不用隨機尾碼，免得產生剛好是真人的門號


def customer_signature(rng):
    name = _name(rng)
    rng.randrange(100, 1000)  # 舊版用來產生電話尾碼；保留這次抽樣，其餘內容才會和先前的檔案逐位元組相同
    phone = PLACEHOLDER_MOBILE
    return rng.choice([
        [name], [name, phone], ["從我的 iPhone 傳送"], ["Sent from my iPhone"], [f"{name} 敬上"],
        [rng.choice(EN_NAMES), f"user{rng.randrange(1000, 10000)}@example.com"],
        ["--", name, phone], [name, f"{rng.choice(SURNAMES)}記範例商行"], ["謝謝！", name],
    ])


def agent_signature(rng):
    agent = rng.choice(AGENTS)
    return ["範例雲客服中心", f"客服專員 {agent}", f"電話：(02) 0000-{rng.randrange(1000, 10000)}",
            "service@example.com"]


def canned_body(rng, key):
    lines = [_fill(rng, x) for x in CANNED[key]]
    if len(lines) > 3 and rng.random() < 0.3:
        lines.pop(rng.randrange(1, len(lines)))  # 客服有時刪掉一行
    return lines


def quote_block(rng, quoted_lines):
    """轉寄／回覆引文，6 種常見格式。"""
    d = f"2026年{rng.randrange(1, 10)}月{rng.randrange(1, 28)}日"
    kind = rng.randrange(6)
    if kind == 0:
        head = ["-----Original Message-----", "From: 範例雲客服 <service@example.com>",
                f"Sent: {d} 上午 10:15", "To: user@example.com", "Subject: RE: 問題回報"]
        return head + quoted_lines
    if kind == 1:
        head = ["寄件者: 範例雲客服 <service@example.com>", f"寄件日期: {d} 上午 10:15",
                "收件者: user@example.com", "主旨: 回覆：問題回報"]
        return head + quoted_lines
    if kind == 2:
        return [f"於 {d} 週一 上午10:15，範例雲客服 <service@example.com> 寫道：", ""] + ["> " + x for x in quoted_lines]
    if kind == 3:
        return ["On Mon, Mar 2, 2026 at 10:15 AM 範例雲客服 <service@example.com> wrote:"] + ["> " + x for x in quoted_lines]
    if kind == 4:  # Gmail 把英文標頭折成兩行
        return ["On Mon, Mar 2, 2026 at 10:15 AM 範例雲客服 <", "service@example.com> wrote:"] + ["> " + x for x in quoted_lines]
    return ["---------- Forwarded message ---------", "From: 範例雲客服 <service@example.com>",
            f"Date: {d}", "Subject: 問題回報", ""] + quoted_lines


def issue_sentence(rng, issue):
    """回傳 (描述問題的句子, 規則關鍵詞是否涵蓋)。"""
    keyed_list, unkeyed_list = SYMPTOMS[issue]
    keyed = rng.random() >= P_UNKEYED
    symptom = rng.choice(keyed_list if keyed else unkeyed_list)
    s = _fill(rng, rng.choice(PREFIXES)) + rng.choice(SUBJECTS[issue]) + symptom + rng.choice(TAILS)
    return s, keyed


def customer_email(rng):
    """回傳 (text, truth)。

    truth：body_lines 是顧客自己寫的行（依原順序，含問候、結尾語與「你們上次回覆我：」）；issue_lines 是描述問題的行；
    noise_pasted 是顧客貼進正文的罐頭回覆行（pasted_reply 難例），不是顧客寫的。
    """
    primary = _pick(rng, ISSUE_WEIGHTS)
    issues = [] if primary is None else [primary]
    if primary is not None and rng.random() < P_SECOND_ISSUE:
        issues.append(rng.choice([i for i in ISSUES if i != primary]))
    hard = []
    issue_lines = []
    for issue in issues:
        phrase, keyed = issue_sentence(rng, issue)
        if not keyed:
            hard.append("unkeyed_wording")
        issue_lines.append(phrase)
    if rng.random() < P_NEGATED:
        # 「登入都正常，但是同步後資料不見了」：前半句提到別的功能但不是問題，不算提及
        neg = NEGATED_LINES[rng.choice([i for i in NEGATED_LINES if i not in issues])]
        if issue_lines:
            issue_lines[0] = f"{neg}，但是{issue_lines[0]}"
        hard.append("negated_mention")
    else:
        neg = None
    content = list(issue_lines) if issues else [rng.choice(NO_ISSUE_LINES)]
    if neg and not issues:
        content.insert(0, neg)
    for _ in range(rng.randrange(1, 3)):
        content.insert(rng.randrange(len(content) + 1), _fill(rng, rng.choice(CONTEXT)))
    greeting, closing = rng.choice(GREETINGS), rng.choice(CLOSINGS)
    body = ([greeting] if greeting else []) + content + ([closing] if closing else [])
    pasted = []
    rendered = list(body)
    if rng.random() < P_PASTED_REPLY:
        pasted = canned_body(rng, _pick(rng, CANNED_WEIGHTS))
        body.append("你們上次回覆我：")
        rendered += ["", "你們上次回覆我："] + pasted
        hard.append("pasted_reply")
    sig = customer_signature(rng) if rng.random() < P_SIGNATURE else []
    quote = quote_block(rng, ["您好：", ""] + canned_body(rng, _pick(rng, CANNED_WEIGHTS))) if rng.random() < P_QUOTE else []
    lines = rendered + ([""] + sig if sig else []) + ([""] + quote if quote else [])
    truth = {"kind": "customer", "issues": issues, "body_lines": body, "issue_lines": issue_lines,
             "noise_pasted": pasted, "rendered_body": rendered, "has_signature": bool(sig),
             "has_quote": bool(quote), "hard_case": hard}
    return "\n".join(lines), truth


def canned_email(rng):
    key = _pick(rng, CANNED_WEIGHTS)
    lines = [f"{_name(rng)} 您好："] + canned_body(rng, key)
    if rng.random() < 0.2:
        lines.insert(rng.randrange(2, len(lines) + 1), "另外您提到的狀況，我們也會一併追蹤。")
    lines += [""] + agent_signature(rng)
    if rng.random() < 0.3:  # 下方附上顧客原信
        _, ct = customer_email(rng)
        lines += [""] + quote_block(rng, ct["body_lines"])
    return "\n".join(lines), {"kind": "canned", "template": key, "issues": [], "hard_case": []}


def agent_email(rng):
    issue = rng.choice(ISSUES)
    phrase = issue_sentence(rng, issue)[0]
    picks = rng.sample(AGENT_LINES, 3)
    lines = [f"{_name(rng)} 您好："] + [_fill(rng, x, phrase=phrase, issue=issue[:2]) for x in picks]
    hard = []
    if rng.random() < P_AGENT_NO_SIG:
        hard.append("no_signature")
    else:
        lines += [""] + agent_signature(rng)
    return "\n".join(lines), {"kind": "agent_reply", "issues": [], "hard_case": hard}


def forward_only_email(rng):
    quoted = ["您好：", ""] + canned_body(rng, _pick(rng, CANNED_WEIGHTS))
    lines = [""] + quote_block(rng, quoted)
    return "\n".join(lines), {"kind": "customer_forward_only", "issues": [], "hard_case": []}


def resend_email(rng, previous):
    """顧客重寄：正文相同，簽名可能不同，沒有引文。"""
    sig = customer_signature(rng) if rng.random() < 0.5 else []
    lines = previous["rendered_body"] + ([""] + sig if sig else [])
    truth = dict(previous, kind="customer_resend", resend_of=previous["email_id"],
                 has_signature=bool(sig), has_quote=False)
    return "\n".join(lines), truth


def make_emails(n, rng):
    out, customers = [], []
    for i in range(1, n + 1):
        eid = f"E{i:05d}"
        kind = _pick(rng, KIND_WEIGHTS)
        if kind == "customer_resend" and not customers:
            kind = "customer"
        if kind == "customer":
            text, truth = customer_email(rng)
            customers.append(dict(truth, email_id=eid))
        elif kind == "customer_resend":
            text, truth = resend_email(rng, rng.choice(customers))
        elif kind == "customer_forward_only":
            text, truth = forward_only_email(rng)
        elif kind == "canned":
            text, truth = canned_email(rng)
        else:
            text, truth = agent_email(rng)
        out.append(({"email_id": eid, "text": text}, dict(truth, email_id=eid)))
    return out


def expected_survey_shares(old_share):
    """問卷各問題的期望勾選比例（含「最多勾 3 項，超過就隨機留 3 項」），精確列舉 2^8 種勾選組合。

    old_share 是老客戶佔比：0.8 是樣本組成（對應未加權），其他值對應事後分層加權到該母體比例。
    只給評估用：檢查樣本裡的名次差異，是不是產生器本來就設成幾乎同分。
    """
    out = dict.fromkeys(ISSUES, 0.0)
    for g, share in (("old", old_share), ("new", 1 - old_share)):
        p = SURVEY_P[g]
        for bits in itertools.product((0, 1), repeat=len(ISSUES)):
            prob = 1.0
            for i, b in zip(ISSUES, bits):
                prob *= p[i] if b else 1 - p[i]
            picked = [i for i, b in zip(ISSUES, bits) if b]
            keep = 1.0 if len(picked) <= 3 else 3 / len(picked)
            for i in picked:
                out[i] += share * prob * keep
    return out


def make_survey(n, rng):
    n_old = round(n * SURVEY_OLD_SHARE)
    groups = ["old"] * n_old + ["new"] * (n - n_old)
    rng.shuffle(groups)
    unknown = set(rng.sample(range(n), round(n * SURVEY_UNKNOWN_SHARE)))
    rows = []
    for k, g in enumerate(groups):
        tenure = rng.randrange(6, 49) if g == "old" else rng.randrange(0, 6)
        picked = [i for i in ISSUES if rng.random() < SURVEY_P[g][i]]
        if len(picked) > 3:
            picked = rng.sample(picked, 3)
        sat = rng.choices(range(1, 6), weights=SATISFACTION_P[g])[0]
        rows.append({"resp_id": f"R{k + 1:04d}", "tenure_months": "" if k in unknown else tenure,
                     "satisfaction": sat, "issues": "|".join(i for i in ISSUES if i in picked)})
    return rows


def write_fixtures(out, n_emails=40000, n_surveys=2000, seed=20261030, preview=30):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    emails = make_emails(n_emails, rng)
    with open(out / "emails.jsonl", "w", encoding="utf-8") as f:
        for e, _ in emails:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    with open(out / "emails_truth.jsonl", "w", encoding="utf-8") as f:
        for _, t in emails:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    with open(out / "emails_preview.jsonl", "w", encoding="utf-8") as f:
        for e, t in emails[:preview]:
            f.write(json.dumps({**e, "truth": t}, ensure_ascii=False) + "\n")
    survey = make_survey(n_surveys, random.Random(seed + 1))
    with open(out / "survey.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=("resp_id", "tenure_months", "satisfaction", "issues"))
        w.writeheader()
        w.writerows(survey)
    params = {"synthetic": True, "seed": seed, "n_emails": n_emails, "n_surveys": n_surveys,
              "kind_weights": KIND_WEIGHTS, "issue_weights": {str(k): v for k, v in ISSUE_WEIGHTS.items()},
              "p_second_issue": P_SECOND_ISSUE, "p_quote": P_QUOTE, "p_signature": P_SIGNATURE,
              "p_pasted_reply": P_PASTED_REPLY, "p_negated": P_NEGATED, "p_agent_no_sig": P_AGENT_NO_SIG,
              "p_unkeyed": P_UNKEYED, "canned_weights": CANNED_WEIGHTS, "survey_old_share": SURVEY_OLD_SHARE,
              "survey_unknown_share": SURVEY_UNKNOWN_SHARE, "survey_p": SURVEY_P}
    (out / "fixture_params.json").write_text(json.dumps(params, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description="產生 Q1 合成資料（非顧客資料）")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--emails", type=int, default=40000)
    ap.add_argument("--surveys", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261030)
    ap.add_argument("--preview", type=int, default=30)
    a = ap.parse_args()
    out = write_fixtures(a.out, a.emails, a.surveys, a.seed, a.preview)
    print(f"wrote {out}: emails={a.emails} surveys={a.surveys} seed={a.seed}")


if __name__ == "__main__":
    main()
