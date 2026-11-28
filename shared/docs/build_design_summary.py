"""把六個模組的設計摘要合成一份 shared/docs/design-summary.md。

每個 qN/README.md 擷取三段（各段到下一個 `## ` 為止）：
  ## 情境與限制、## 遇到的瓶頸、## 我怎麼解
「我怎麼解」若 README 還沒有這一段，退回：
  - q4：design/threads_q4_result.txt 裡的 `[D. 設計摘要…]` 段落（跨模組約定）
  - 其他：qN/design/summary.txt 全文
缺段或缺檔時印出警告並放入佔位文字，不中斷。

用法：python shared/docs/build_design_summary.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "shared" / "docs" / "design-summary.md"

MODULES = [
    ("q1", "反饋分診：三通路顧客反饋排出前三大問題"),
    ("q2", "FB 貼文搜尋：100 萬則貼文的容量估算與選型"),
    ("q3", "PTT 全文搜尋：八卦板 7 天全文搜尋 ETL"),
    ("q4", "Threads 爬蟲：40 萬帳號、每天 4 輪"),
    ("q5", "新指標上線：Reels 觀看數與 Threads 轉發數"),
    ("q6", "文字雲斷詞重建：六渠道、約 5 億筆"),
]
HEADINGS = ["情境與限制", "遇到的瓶頸", "我怎麼解"]
PLACEHOLDER = "（這一段還沒有從模組 README 擷取到，請補上 `## {}`）"


def warn(msg):
    print(f"WARN: {msg}", file=sys.stderr)


def read(rel):
    path = ROOT / rel
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def section(text, heading):
    """回傳 `## heading` 到下一個 `## ` 之間的內文（不含標題行），找不到回傳 None。"""
    m = re.search(rf"^## {re.escape(heading)}[ \t]*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return None
    return re.sub(r"(^|\n)-{3,}\s*$", "", m.group(1).strip()).strip()


def one_liner(text):
    m = re.search(r"^> 一句話[:：].*$", text, re.M)
    return m.group(0) if m else None


def solution_fallback(q):
    """README 沒有「我怎麼解」時的退路。"""
    if q == "q4":
        txt = read("q4/design/threads_q4_result.txt")
        if txt:
            m = re.search(r"^\[D\. 設計摘要[^\n]*\]\n(.*?)(?=^\[|^驗證細節|\Z)", txt, re.M | re.S)
            if m:
                return m.group(1).strip()
    txt = read(f"{q}/design/summary.txt")
    return txt.strip() if txt else None


def demote(body):
    """模組 README 裡的 `### ` 小節在摘要裡往下降一級，避免蓋過模組標題。"""
    return re.sub(r"^### ", "#### ", body, flags=re.M)


def build_module(q, name):
    text = read(f"{q}/README.md")
    if text is None:
        warn(f"{q}/README.md 不存在")
        text = ""

    parts = {}
    for h in HEADINGS:
        body = section(text, h)
        if body is None:
            if h == "我怎麼解":
                body = solution_fallback(q)
                if body is not None:
                    warn(f"{q}/README.md 沒有「## 我怎麼解」，改用 {q} 的解法檔")
            if body is None:
                warn(f"{q}/README.md 沒有「## {h}」")
                body = PLACEHOLDER.format(h)
        parts[h] = body

    sec = [f'<a id="{q}"></a>', "", f"## {name}", ""]
    line = one_liner(text)
    if line:
        sec += [line, ""]
    for h in HEADINGS:
        sec += [f"### {h}", "", demote(parts[h]), ""]
    sec += [f"程式與佐證：[`{q}/`](../../{q}/)", ""]
    return "\n".join(sec)


def main():
    lines = [
        "# ARGUS 設計摘要：六個模組的瓶頸與解法",
        "",
        "六個模組各是我在 side project 裡卡住過的一個問題。每一段都照同樣的順序寫："
        "先講情境與限制，列出遇到的瓶頸，再說我怎麼解。程式、實跑紀錄與截圖在各模組資料夾裡。",
        "",
        "本檔由 `shared/docs/build_design_summary.py` 從各模組的 `README.md` 擷取產生，要改內容請改那邊再重跑。",
        "",
        "| 模組 | 連結 |",
        "|---|---|",
    ]
    lines += [f"| {name} | [跳到這一段](#{q}) |" for q, name in MODULES]
    lines += [""]
    for q, name in MODULES:
        lines.append(build_module(q, name))
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
