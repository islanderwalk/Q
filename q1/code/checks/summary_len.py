"""ARGUS｜反饋分診：數一下 q1/README.md「我怎麼解」段的字數（從標題到下一條 '---'），用幾種算法。"""
import re, sys, unicodedata
text = open(sys.argv[1], encoding="utf-8").read()
ans = re.split(r"^## 我怎麼解.*$", text, maxsplit=1, flags=re.M)[1].split("\n---", 1)[0]
nows = re.sub(r"\s", "", ans)
no_bold = nows.replace("**", "")
no_num = re.sub(r"(^|\n)\s*\d+\.", r"\1", ans)
no_num = re.sub(r"\s", "", no_num).replace("**", "")
cjk = sum(1 for ch in no_bold if unicodedata.east_asian_width(ch) in ("W", "F"))
print(f"non-whitespace with **: {len(nows)}")
print(f"non-whitespace without **: {len(no_bold)}")
print(f"also without list numbers: {len(no_num)}")
print(f"wide/fullwidth chars only (CJK + fullwidth punctuation): {cjk}")
