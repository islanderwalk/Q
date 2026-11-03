"""v0 舊流程重現版的純函式測試：逆向最長匹配、凍結字典的五個病徵、artifact 建版、教育部 xlsx 抽詞。
不需要 ES／jieba／pyarrow。執行：PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider q6/stack/tests"""
import io
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "seg" / "qseg"))
sys.path.insert(0, str(HERE.parent / "dict"))
sys.path.insert(0, str(HERE.parent / "dict" / "legacy"))
sys.path.insert(0, str(HERE.parent / "api"))

import legacy          # noqa: E402  (seg/qseg/legacy.py，只 import re)
import build_legacy    # noqa: E402
import extract_moe     # noqa: E402
import ops             # noqa: E402


# ---------- 逆向最長匹配：字典有的成詞，字典沒有的退回單字 ----------
def test_bmm_keeps_dict_word_and_splits_unknown():
    words = frozenset({"韓國瑜", "台積電"})     # 凍結字典有的（2019 前就紅）
    toks = legacy.tokens("韓國瑜談台積電和沈伯洋", words, 8)
    assert "韓國瑜" in toks and "台積電" in toks
    assert "沈伯洋" not in toks and "沈" in toks and "伯" in toks and "洋" in toks  # 2019 後的新詞被拆成單字


def test_bmm_is_backward_longest_match():
    # 「中華電信」在字典 → 整詞；「中華」「電信」也在也不拆（逆向先吃最長）
    assert legacy.tokens("中華電信漲價", frozenset({"中華電信", "中華", "電信", "漲價"}), 8) == ["中華電信", "漲價"]


# ---------- 五病徵：網址殘片、電話、助詞 ----------
def test_url_phone_particles_all_become_tokens():
    toks = legacy.tokens("打0800092000有的 https://bit.ly/3xYz", frozenset(), 8)
    assert "0800092000" in toks                       # 電話成詞
    assert "的" in toks                                # 沒有停用詞，助詞成詞
    assert {"https", "bit", "ly", "3xYz"} <= set(toks)  # 短網址後綴成詞，英數字不小寫（3xYz 不變 3xyz）


def test_load_words_one_per_line_filters_singles_and_nonzh(tmp_path):
    f = tmp_path / "legacy.dict"
    f.write_text("# 註解\n高雄市\n城市\n的\nA咖\n", encoding="utf-8")
    w = legacy.load_words(f, 8)
    assert {"高雄市", "城市"} <= w and "的" not in w and "A咖" not in w


# ---------- 建 v0 artifact：合併四層、不碰 current ----------
def test_build_legacy_merges_layers_and_leaves_current(tmp_path):
    moe = tmp_path / "moe.txt"; moe.write_text("高雄市\n台北\n&8ff0.gif;\n高雄市\n", encoding="utf-8")
    jb = tmp_path / "jieba.txt"; jb.write_text("沈伯洋 5 nr\n高雄 100 ns\nA咖 3 n\n的 999 uj\n", encoding="utf-8")
    out = build_legacy.build(tmp_path / "art", "qsearch-q6-legacy-2019.12.31.1", {"moe": str(moe), "jieba_tw": str(jb)})
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    words = set((out / "legacy.dict").read_text(encoding="utf-8").split())
    assert {"沈伯洋", "高雄", "高雄市", "台北"} <= words      # jieba 取第一欄、moe 一行一詞、repo 層整行
    assert "A咖" not in words and "的" not in words           # 非純中文、單字都不收
    assert man["pipeline"] == "v0" and man["frozen_at"] == "2019-12-31"
    assert man["total_words"] == len(words) and man["files"]["legacy.dict"]
    assert [l["id"] for l in man["layers"]] == ["moe_revised_2015", "jieba_tw_2016", "company_2019", "places_old"]
    assert not (tmp_path / "art" / "current").exists()        # v0 建版絕不動 current（current 是 v2 的）


def test_build_legacy_dict_is_sorted_and_unique(tmp_path):
    moe = tmp_path / "moe.txt"; moe.write_text("乙\n甲\n甲\n", encoding="utf-8")
    jb = tmp_path / "jieba.txt"; jb.write_text("丙 1 n\n", encoding="utf-8")
    out = build_legacy.build(tmp_path / "art", "qsearch-q6-legacy-2019.12.31.1", {"moe": str(moe), "jieba_tw": str(jb)})
    lines = (out / "legacy.dict").read_text(encoding="utf-8").split()
    assert lines == sorted(set(lines))


# ---------- 教育部重編國語辭典 xlsx 抽「字詞名」欄（只用標準函式庫） ----------
def test_extract_moe_reads_header_column_cjk_only(tmp_path, capsys):
    ss = "<sst><si><t>字詞名</t></si><si><t>高雄市</t></si><si><t>abc</t></si><si><t>台北</t></si></sst>"
    sheet = ("<worksheet><sheetData>"
             '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
             '<row r="2"><c r="A2" t="s"><v>1</v></c></row>'
             '<row r="3"><c r="A3" t="s"><v>2</v></c></row>'
             '<row r="4"><c r="A4" t="s"><v>3</v></c></row>'
             "</sheetData></worksheet>")
    zp = tmp_path / "d.xlsx"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("xl/sharedStrings.xml", ss)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    extract_moe.main(["x", str(zp)])
    assert capsys.readouterr().out.split() == ["台北", "高雄市"]   # 跳過 abc，去重排序


# ---------- v0 的 seg_ver：凍結日就是版本日，且比 v1／v2 小 ----------
def test_v0_seg_ver_from_frozen_date_and_ordering():
    assert ops.seg_ver("v0", "qsearch-q6-legacy-2019.12.31.1") == 2019123101
    assert ops.seg_ver("v0", "qsearch-q6-legacy-2019.12.31.1") < ops.seg_ver("v1", "qsearch-q6-2026.10.30.1")
    assert ops.seg_ver("v1", "qsearch-q6-2026.10.30.1") < ops.seg_ver("v2", "qsearch-q6-2026.10.30.1")
