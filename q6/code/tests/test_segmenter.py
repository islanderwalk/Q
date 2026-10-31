"""Q6 斷詞 pipeline 離線煙霧測試（不連網；字典為 repo 內的 dict/）。"""
import sys
from pathlib import Path

import jieba
import pytest

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))

import segmenter as sg  # noqa: E402


@pytest.fixture(scope="module")
def seg():
    return sg.Segmenters.build()


def test_preprocess_removes_short_urls_emails_and_phones():
    text = "看這個 ppt.cc/abc123 還有 bit.ly/xyz 和 https://example.com/a?b=1 聯絡 a.b@mail.com"
    text += " 電話 0912-345-678 市話 07-3451234"
    out = sg.preprocess(text)
    for junk in ("ppt.cc", "bit.ly", "https", "example.com", "@mail", "0912", "3451234"):
        assert junk not in out, junk
    assert "看這個" in out


def test_ptt_tags_and_reply_prefix_removed():
    out = sg.preprocess("Re: [問卦] 高雄市今天下雨 [新聞]")
    assert "問卦" not in out and "新聞" not in out and "Re" not in out
    assert "高雄市今天下雨" in out


def test_new_pipeline_drops_particles_and_numbers(seg):
    toks = sg.segment_new(seg, "我的貓在桌上，了不起的 2026 年 ppt.cc/x")
    assert "的" not in toks and "了" not in toks and "在" not in toks
    assert not any(t.isdigit() for t in toks)
    assert all(len(t) >= 2 for t in toks)


def test_user_dictionary_protects_place_name(seg):
    toks = sg.segment_new(seg, "高雄市政府今天宣布新措施")
    assert "高雄市" in toks
    assert "高" not in toks and "雄" not in toks


def test_legacy_pipeline_keeps_noise_for_comparison(seg):
    # 舊流程保留停用詞與原始符號，用來對照（不是正確結果）
    legacy = sg.segment_legacy(seg, "我的貓在桌上")
    assert "的" in legacy


def test_dual_output_has_both_fields_and_version(seg):
    out = sg.dual_output(seg, "d1", "高雄市的天氣")
    assert set(out) == {"doc_id", "dict_version", "dict_hash", "seg_build", "content_sha", "seg_legacy", "seg_new"}
    assert out["seg_build"] == f"{out['dict_version']}@{seg.fingerprint}"
    assert out["dict_version"] == sg.read_dict_version()
    assert len(out["dict_hash"]) == 16


def test_new_terms_is_set_difference():
    assert sg.new_terms({"高雄市", "臺北市"}, {"高雄市", "臺北市", "麥當勞"}) == {"麥當勞"}


def test_affected_docs_only_contain_the_character_sequence():
    docs = {
        "a": "今天麥當勞的漢堡很好吃",
        "b": "麥當 勞 分開寫不算",
        "c": "完全無關的內容",
    }
    index = sg.BigramIndex(docs)
    assert sg.affected_doc_ids(index, {"麥當勞"}) == {"a"}
    assert sg.affected_doc_ids(index, {"無關"}) == {"c"}
    assert sg.affected_doc_ids(index, {"不存在的詞"}) == set()


def test_dict_version_header_present():
    assert sg.read_dict_version().startswith("qsearch-q6-")


# ---- 主字典選項（--base-dict）：用極小的內嵌字典，不需要 dict.txt.big ----------------
import run_compare as rc  # noqa: E402

TINY_BASE = "高雄 100 ns\n高雄市 31 ns\n今天 80 t\n很 50 d\n熱 40 a\n市集 20 n\n"


@pytest.fixture()
def tiny_base(tmp_path):
    p = tmp_path / "tiny_dict.txt"
    p.write_text(TINY_BASE, encoding="utf-8")
    return p


def test_default_build_uses_jieba_builtin_where_高雄_is_only_a_prefix(seg):
    assert seg.base_dict == sg.BUILTIN_BASE and seg.base_dict_hash == ""
    # 內建字典有「高雄市」，但「高雄」只是前綴佔位（詞頻 0），純字典模式會拆成 高／雄
    assert seg.legacy.FREQ.get("高雄市", 0) > 0
    assert seg.legacy.FREQ.get("高雄") == 0
    assert sg.segment_legacy(seg, "高雄今天很熱")[:2] == ["高", "雄"]


def test_base_dict_option_replaces_dictionary_for_both_pipelines(tiny_base):
    seg = sg.Segmenters.build(base_dict=tiny_base)
    assert seg.base_dict == "tiny_dict.txt"
    assert seg.base_dict_hash == sg.dict_content_hash(tiny_base) and len(seg.base_dict_hash) == 16
    assert seg.legacy.total == 100 + 31 + 80 + 50 + 40 + 20
    assert sg.segment_legacy(seg, "高雄今天很熱") == ["高雄", "今天", "很", "熱"]
    assert "高雄" in sg.segment_new(seg, "[問卦] 高雄今天很熱")


def test_user_dict_overlay_keeps_place_names_on_tiny_base(tiny_base):
    seg = sg.Segmenters.build(base_dict=tiny_base)
    assert "台南市" not in seg.legacy.FREQ          # 主字典沒有
    assert "台南市" in sg.segment_new(seg, "台南市今天很熱")  # 使用者字典補上


def test_user_dict_overwrite_lowers_freq_but_total_only_grows(tiny_base):
    # jieba add_word 覆寫既有詞時 total 只加新詞頻、不扣舊詞頻（__init__.py add_word）
    seg = sg.Segmenters.build(base_dict=tiny_base, pin_total=False)
    n_user = len(sg.user_dict_terms())
    assert seg.legacy.FREQ["高雄市"] == 31 and seg.new.FREQ["高雄市"] == 5
    assert seg.new.total == seg.legacy.total + 5 * n_user
    # 預設固定 total：加詞不改其他詞的機率，不含新詞的文件分詞不變
    pinned = sg.Segmenters.build(base_dict=tiny_base)
    assert pinned.new.total == pinned.legacy.total == pinned.total_pinned


def test_user_dict_comment_lines_are_not_loaded_as_words(tiny_base):
    # jieba load_userdict 不跳過 # 註解；直接載入時整行註解會變成詞頻 1 的「詞」
    raw = jieba.Tokenizer(dictionary=str(tiny_base))
    raw.load_userdict(str(sg.USER_DICT_PATH))
    assert any(w.startswith("# dict_version") and raw.FREQ[w] for w in raw.FREQ)
    seg = sg.Segmenters.build(base_dict=tiny_base)
    assert not any(w.startswith("#") and seg.new.FREQ[w] for w in seg.new.FREQ)
    assert sg.user_dict_terms() == {line.split()[0] for line in sg.user_dict_lines()}


def test_place_status_classifies_whole_glued_split():
    freq = {"高雄人": 3}
    assert rc.place_status(["高雄", "下雨"], freq) == "whole"
    assert rc.place_status(["高雄人", "好熱"], freq) == "whole"
    assert rc.place_status(["高雄為", "什麼"], freq) == "glued"
    assert rc.place_status(["高", "雄", "下雨"], freq) == "split"


def test_upsert_block_keeps_handwritten_text():
    md = "# 標題\n\n手寫段落\n"
    md = rc.upsert_block(md, "default", "A1")
    md = rc.upsert_block(md, "dict_big", "B1")
    md = rc.upsert_block(md, "default", "A2")
    assert "手寫段落" in md and "A2" in md and "B1" in md and "A1" not in md
    assert md.count("<!-- AUTO:compare:default BEGIN -->") == 1
    assert md.index("A2") < md.index("B1")  # 原位置替換，不會跑到檔尾


def test_upsert_block_refuses_unmatched_markers():
    broken = "# 標題\n<!-- AUTO:compare:default BEGIN -->\n舊內容\n"
    with pytest.raises(ValueError):
        rc.upsert_block(broken, "default", "A1")
    with pytest.raises(ValueError):
        rc.upsert_block("<!-- AUTO:compare:default END -->\n", "default", "A1")


def test_run_compare_main_with_base_dict_writes_tagged_outputs(tmp_path, tiny_base):
    idx = tmp_path / "index.csv"
    idx.write_text("page,aid,title\n1,A1,[問卦] 高雄今天很熱\n1,,[新聞] 高雄市集\n2,A3,今天很熱\n", encoding="utf-8")
    res = tmp_path / "RESULTS.md"
    res.write_text("# 手寫\n", encoding="utf-8")
    checks = rc.main(["--index", str(idx), "--base-dict", str(tiny_base), "--tag", "tiny",
                      "--data-dir", str(tmp_path / "data"), "--results", str(res)])
    assert (tmp_path / "data" / "top50_tiny.json").exists()
    assert (tmp_path / "data" / "segmented_ab_tiny.jsonl").exists()
    assert not (tmp_path / "data" / "ptt_titles_7d.jsonl").exists()  # 語料只在 default 寫
    assert checks["titles_containing_高雄"] == 2 and checks["titles_containing_高雄市"] == 1
    assert checks["titles_高雄_status"]["legacy"]["split"] == 0
    md = res.read_text(encoding="utf-8")
    assert md.startswith("# 手寫") and "<!-- AUTO:compare:tiny BEGIN -->" in md and "tiny_dict.txt" in md


# ---- 2026-10-31 審查：重要詞召回、版本指紋 ----

def test_wen_examples_keep_important_terms(seg):
    # 颱風假、癌、[高雄市]今天下雨：舊規則會誤刪，新規則要留住
    assert "颱風假" in sg.segment_new(seg, "颱風假要放幾天")
    assert "癌" in sg.segment_new(seg, "癌")
    out = sg.segment_new(seg, "[高雄市]今天下雨")
    assert "高雄市" in out and "下雨" in out
    # PTT 白名單標籤照樣去掉
    assert "問卦" not in sg.segment_new(seg, "[問卦] 今天下雨")


def test_fingerprint_covers_stopwords_base_dict_and_code(seg, tiny_base, tmp_path):
    inputs = sg.fingerprint_inputs(seg)
    for k in ("jieba", "hmm", "base_dict", "user_dict", "stopwords", "ptt_tags", "keep_single",
              "total_pinned", "code"):
        assert k in inputs, k
    other = sg.Segmenters.build(base_dict=tiny_base)
    assert other.fingerprint != seg.fingerprint


def test_content_sha_is_independent_of_dictionary(seg, tiny_base):
    a = sg.dual_output(seg, "d1", "高雄市的天氣")
    b = sg.dual_output(sg.Segmenters.build(base_dict=tiny_base), "d1", "高雄市的天氣")
    assert a["content_sha"] == b["content_sha"] and a["seg_build"] != b["seg_build"]
