"""渠道別清洗（只給新流程 v2 用；舊流程 v1 直接吃原文）。

順序很重要（見 QSearch_ES_GCP_討論筆記 §5.1）：先把整段網址辨識掉，再做其他處理；
先去標點再去網址，網址會被拆成 https / short / X7Ab2 留在詞表裡。
共同的網址、Email、電話、PTT 標籤規則沿用 q6/code/segmenter.py 的 preprocess（與 repo 原型同一份）。
"""
import html
import re

import segmenter as sg

TAG_RE = re.compile(r"<[^>]+>")
# 沒有協定的網址、被換行切斷的網址殘片（PTT 推文把 https://… 切成「http」+「s://…」）
# 前面不能用 \w：Python 的 \w 包含中文，「信箱是msn.com」的「是」會讓整條規則失效（回補閘門 G2 抓到的實例）
BARE_URL_RE = re.compile(r"(?<![A-Za-z0-9_@.-])(?:[a-z0-9-]+\.)+(?:com|tw|cc|net|org|io|me|ly|gl|co|es|gd|be|to|info)(?:/[^\s，。、）)]*)?", re.I)
URL_FRAGMENT_RE = re.compile(r"(?<![a-z])s?://\S+|\bhttps?\b", re.I)
MENTION_RE = re.compile(r"@[\w.]+")
HASHTAG_RE = re.compile(r"#(\w+)")
TIMESTAMP_RE = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)")

PTT_DROP_LINE = re.compile(r"^(?:作者|看板|標題|時間)\s|^※\s*(?:發信站|文章網址|編輯|引述)|^Sent from |^: |^--$")
MEDIA_BYLINE_RE = re.compile(r"[（(]中央社[^）)]{0,40}[）)]|[〔\[［]記者[^〕\]］]{0,40}[〕\]］]|(?:責任)?編輯[:：]\s*\S+|記者\S{2,4}[／/]\S{2,6}報導")
FB_MORE_RE = re.compile(r"(?:See more|See translation|查看更多|顯示更多|…\s*更多)")


def _ptt(text: str) -> str:
    out = []
    for line in text.splitlines():
        if line.strip() == "--":            # 簽名檔分隔線之後全部不要
            break
        if PTT_DROP_LINE.search(line.strip()):
            continue
        out.append(line)
    return "\n".join(out)


def normalize(channel: str, text: str) -> str:
    t = html.unescape(text or "")
    t = TAG_RE.sub(" ", t)
    if channel == "ptt":
        t = _ptt(t)
    elif channel == "media":
        t = MEDIA_BYLINE_RE.sub(" ", t)
    elif channel in ("facebook", "instagram"):
        t = FB_MORE_RE.sub(" ", t)
    elif channel == "youtube":
        t = TIMESTAMP_RE.sub(" ", t)
    # 先處理完整網址（含短網址），再處理沒有協定的網域與被切斷的殘片
    t = sg.SHORT_URL_RE.sub(" ", t)
    t = sg.URL_RE.sub(" ", t)
    t = BARE_URL_RE.sub(" ", t)
    t = URL_FRAGMENT_RE.sub(" ", t)
    t = MENTION_RE.sub(" ", t)
    t = HASHTAG_RE.sub(r" \1 ", t)
    return sg.preprocess(t)
