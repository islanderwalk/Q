"""從教育部《重編國語辭典修訂本》的下載檔（zip 或裡面的 xlsx）抽出「字詞名」欄，一行一詞輸出到 stdout。
只用標準函式庫（xlsx 是 zip 包 XML：xl/sharedStrings.xml ＋ xl/worksheets/sheet1.xml），不需要 openpyxl。
只留純中文詞（辭典裡用 &xxxx._104_0.gif; 代替的罕字條目會被略過），去重後排序，統計寫到 stderr。

用法：python dict/legacy/extract_moe.py dict_revised_2015_20260929.zip > moe_revised_words.txt
"""
import html
import io
import re
import sys
import zipfile

CJK_RE = re.compile(r"^[㐀-䶿一-鿿]+$")
SI_RE = re.compile(r"<si>(.*?)</si>", re.S)
T_RE = re.compile(r"<t[^>]*>(.*?)</t>", re.S)
ROW_RE = re.compile(r"<row [^>]*>(.*?)</row>", re.S)
# 先把 <c …> 的屬性整段抓下來再找 t="…"：教育部 2026-09-29 的匯出每格都有樣式屬性 s="1" 排在 t="s" 前面，
# 原本「可選的 t 群組」在惰性比對下會直接跳過，字詞名欄就被當成數字索引讀出 0、1、2…（找不到欄位「字詞名」）
CELL_RE = re.compile(r'<c r="([A-Z]+)\d+"([^>]*)>(?:<v>(.*?)</v>|<is>(.*?)</is>)?', re.S)
T_ATTR_RE = re.compile(r'\bt="(\w+)"')


def open_xlsx(path: str) -> zipfile.ZipFile:
    z = zipfile.ZipFile(path)
    inner = [n for n in z.namelist() if n.lower().endswith(".xlsx")]
    if inner:   # 教育部的 zip 裡放 xlsx（另一個是欄位說明），取主檔
        main = sorted(inner, key=len)[0]
        return zipfile.ZipFile(io.BytesIO(z.read(main)))
    return z


def column_values(z: zipfile.ZipFile, header: str):
    strings = ["".join(T_RE.findall(si)) for si in SI_RE.findall(z.read("xl/sharedStrings.xml").decode("utf-8"))]
    sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    col = None
    for row in ROW_RE.findall(sheet):
        cells = {}
        for c, attrs, v, inl in CELL_RE.findall(row):
            m = T_ATTR_RE.search(attrs)
            t = m.group(1) if m else ""
            if t == "s" and v:
                val = strings[int(v)]
            elif inl:
                val = "".join(T_RE.findall(inl))
            else:
                val = v or ""
            cells[c] = html.unescape(val)
        if col is None:
            col = next((c for c, val in cells.items() if val.strip() == header), None)
            if col is None:
                raise SystemExit(f"找不到欄位「{header}」，第一列是 {cells}")
            continue
        if cells.get(col):
            yield cells[col]


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    z = open_xlsx(argv[1])
    raw = total = 0
    words = set()
    for val in column_values(z, "字詞名"):
        total += 1
        w = val.strip()
        if CJK_RE.match(w):
            words.add(w)
        else:
            raw += 1
    for w in sorted(words):
        print(w)
    print(f"rows {total}, kept {len(words)} unique CJK words, skipped {raw} non-CJK entries", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv)
