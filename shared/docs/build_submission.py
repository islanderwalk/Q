"""把 Q1–Q6 的正式答案合成一份交卷文件 shared/docs/submission.md。

答案只從各題的正式答案檔讀出，不在這裡改字；字數一律用 python len（空白與換行都算），
Q2、Q3 的算式不計入字數。用法：python shared/docs/build_submission.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "shared" / "docs" / "submission.md"
FORMULA_MARK = "【算式"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def questions():
    text = read("shared/docs/qsearch_questions.md")
    parts = re.split(r"^## (Q\d) ", text, flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def split_formulas(text):
    if FORMULA_MARK in text:
        body, formulas = text.split(FORMULA_MARK, 1)
        return body.strip(), (FORMULA_MARK + formulas).strip()
    return text.strip(), ""


def as_paragraphs(text):
    """純文字答案的每一行在 Markdown 裡各成一段，換行才不會被併掉；單獨一行的小題編號（如 1(a)、(b)）併到下一段開頭並加粗。"""
    paras, label = [], ""
    for line in (l.strip() for l in text.splitlines()):
        if not line:
            continue
        if len(line) <= 5:
            label = f"**{line}** "
            continue
        paras.append(label + line)
        label = ""
    return "\n\n".join(paras)


def q1():
    m = re.search(r"^## 答案.*?$(.*?)^---\s*$", read("q1/README.md"), re.M | re.S)
    return m.group(1).strip(), ""


def q4():
    m = re.search(r"^\[D\. 第 4 題答案.*?\]\n(.*?)\n\s*驗證細節：", read("q4/answer/threads_q4_result.txt"), re.M | re.S)
    return m.group(1).strip(), ""


ANSWERS = {
    "Q1": (q1, 400, "q1/README.md", []),
    "Q2": (lambda: split_formulas(read("q2/answer/answer_600.txt")), 600, "q2/answer/answer_600.txt", []),
    "Q3": (lambda: split_formulas(read("q3/answer/answer_800.txt")), 800, "q3/answer/answer_800.txt",
           ["q3/architecture/architecture.png"]),
    "Q4": (q4, 600, "q4/answer/threads_q4_result.txt", ["q4/architecture/diagram-system.png"]),
    "Q5": (lambda: split_formulas(read("q5/answer/answer_600.txt")), 600, "q5/answer/answer_600.txt",
           ["q5/architecture/diagram-dataflow-vertical-final.png"]),
    "Q6": (lambda: split_formulas(read("q6/answer/answer_600.txt")), 600, "q6/answer/answer_600.txt",
           ["q6/architecture/overview/diagram-overall.png"]),
}


def main():
    qs = questions()
    lines = ["# QSearch 資料工程師筆試：Q1–Q6 交卷答案", "",
             "字數算法：python `len`，空白與換行都算；Q2、Q3 的算式與 Q3 的架構圖依題目不計入字數。"
             "每題的程式、實測結果與截圖在 repo 的 `qN/` 資料夾，總架構見根目錄 README。", ""]
    lines += ["| 題 | 上限 | 本文字數 | 答案檔 |", "|---|---|---|---|"]
    rows, sections = [], []
    for q, (get, limit, src, figs) in ANSWERS.items():
        body, formulas = get()
        n = len(body)
        assert n <= limit, f"{q} 超過上限：{n} > {limit}"
        rows.append(f"| {q} | {limit} | {n} | [`{src}`](../../{src}) |")
        sec = [f"## {q}", "", "\n".join("> " + l if l else ">" for l in qs[q].splitlines()), "",
               "### 答案", "", body if q == "Q1" else as_paragraphs(body), ""]
        if formulas:
            sec += ["### 算式（不計入字數）", "", "```text", formulas, "```", ""]
        for f in figs:
            sec += [f"![{q} 附圖](../../{f})", ""]
        sec += [f"程式與佐證：[`{q.lower()}/`](../../{q.lower()}/README.md)", ""]
        sections.append("\n".join(sec))
    OUT.write_text("\n".join(lines + rows + [""] + sections), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    for r in rows:
        print(r)


if __name__ == "__main__":
    main()
