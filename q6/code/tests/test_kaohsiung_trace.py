"""kaohsiung_trace.py 與 jieba_experiments/compare_results.py 的離線測試（極小內嵌字典，不需要 dict.txt.big）。"""
import json
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(CODE / "jieba_experiments"))

import compare_results as cr  # noqa: E402
import kaohsiung_trace as kt  # noqa: E402
import segmenter as sg  # noqa: E402

# 沒有「高雄」，只有「高雄市」；「雄風」讓「高雄風力」在純字典下切成 高／雄風／力（對照 dict.txt.big 的實例）
TINY_NO_KAOHSIUNG = "高雄市 31 ns\n雄風 50 n\n風力 40 n\n今天 80 t\n很 50 d\n熱 40 a\n高 100 a\n雄 30 n\n力 100 n\n"
DOCS = {"a": "高雄風力很強", "b": "高雄市今天很熱", "c": "今天很熱", "d": "[問卦] 高雄今天很熱"}


@pytest.fixture()
def tiny(tmp_path):
    p = tmp_path / "tiny_no_kaohsiung.txt"
    p.write_text(TINY_NO_KAOHSIUNG, encoding="utf-8")
    return p


def test_trace_shows_高雄市_already_whole_in_legacy(tiny):
    r = kt.trace(DOCS, base_dict=tiny)
    assert r["titles_containing"] == {"高雄市": 1, "高雄": 3}
    # 主字典已有「高雄市」，舊流程本來就整詞切出；「高雄」只是前綴佔位（0），舊流程拆成 高／雄
    assert r["freq"]["legacy"]["高雄市"] == 31 and r["freq"]["legacy"]["高雄"] == 0
    assert r["tokens_containing_高雄"]["legacy"] == {"高雄市": 1}
    # 使用者字典的「高雄市 5」把詞頻從 31 蓋成 5
    assert r["freq"]["new"]["高雄市"] == 5
    assert [t["text"] for t in r["titles_高雄市"]] == ["高雄市今天很熱"]
    json.dumps(r, ensure_ascii=False)  # 可序列化


def test_what_if_low_freq_word_can_undo_hmm_and_pin_total_keeps_total(tiny):
    # 加詞前 HMM 已把「高雄」黏對；加低詞頻的「高雄」後反而拆開
    assert "高雄" in sg.segment_new(sg.Segmenters.build(base_dict=tiny), DOCS["d"])
    low = kt.what_if_add_word(sg.Segmenters.build(base_dict=tiny), DOCS, freq=2)
    # 詞頻太低時，原本靠 HMM 黏對的「高雄今天很熱」反而被拆開
    assert low["status"] == {"whole": 1, "glued": 0, "split": 2}
    assert {s["text"] for s in low["still_split"]} == {"高雄風力很強", "[問卦] 高雄今天很熱"}
    assert low["total_after"] == low["total_before"] + 2
    ok = kt.what_if_add_word(sg.Segmenters.build(base_dict=tiny), DOCS, freq=31, pin_total=True)
    assert ok["status"] == {"whole": 3, "glued": 0, "split": 0} and ok["still_split"] == []
    assert ok["total_after"] == ok["total_before"]
    assert ok["changed_containing"] == 1 and ok["changed_not_containing"] == 0


def test_what_if_counts_use_preprocessed_text(tiny):
    # 方括號只有白名單標籤會被去掉；「[高雄]」不是標籤，前處理後保留，會被計入
    docs = {"t": "[高雄] 今天很熱", "b": "高雄市今天很熱"}
    r = kt.what_if_add_word(sg.Segmenters.build(base_dict=tiny), docs, freq=31, pin_total=True)
    assert sum(r["status"].values()) == 2 and r["status"]["whole"] == 2
    assert r["changed_not_containing"] == 0
    # 舊規則（去掉所有方括號）時，「高雄」只在標籤裡的那篇不算
    assert sg.preprocess("[高雄] 今天很熱", bracket_mode="all").strip() == "今天很熱"


def test_trace_main_writes_tagged_json(tmp_path, tiny):
    idx = tmp_path / "index.csv"
    idx.write_text("page,aid,title\n1,A1,高雄風力很強\n1,,高雄市今天很熱\n", encoding="utf-8")
    out = tmp_path / "trace.json"
    kt.main(["--index", str(idx), "--base-dict", str(tiny), "--out", str(out)])
    r = json.loads(out.read_text(encoding="utf-8"))
    assert r["base_dict"] == "tiny_no_kaohsiung.txt" and r["docs"] == 2
    assert len(r["what_if_add_高雄"]) == len(kt.SCENARIOS)


def test_compare_results_ignores_only_timing_fields():
    a = {"throughput": {"x": {"seconds": 5.66, "chars_per_sec": 512651}}, "n": [1, 2]}
    b = {"throughput": {"x": {"seconds": 5.86, "chars_per_sec": 495231}}, "n": [1, 2]}
    r = cr.compare(json.dumps(a).encode(), json.dumps(b).encode())
    assert not r["raw_equal"] and r["equal_without_timing"]
    assert len(r["diffs"]) == 2 and r["non_timing_diffs"] == []
    c = dict(b, n=[1, 3])
    r2 = cr.compare(json.dumps(a).encode(), json.dumps(c).encode())
    assert not r2["equal_without_timing"] and r2["non_timing_diffs"] == [("$.n[1]", 2, 3)]
