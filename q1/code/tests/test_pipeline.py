"""Q1 端到端煙霧測試：小量合成資料（1,500 封信、400 份問卷）跑完整流程。不連網，約 1 秒。"""
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))

import coverage_table as cov  # noqa: E402
import issue_tags as it  # noqa: E402
import make_fixtures as mf  # noqa: E402
import run_q1  # noqa: E402

FILES = ("emails.jsonl", "emails_truth.jsonl", "emails_preview.jsonl", "survey.csv", "fixture_params.json")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    base = tmp_path_factory.mktemp("q1")
    mf.write_fixtures(base / "fx", n_emails=1500, n_surveys=400, seed=7, preview=5)
    summary = run_q1.run(base / "fx", base / "out", base_old_share=0.5, sample_size=300, seed=7)
    return base, summary


def test_fixtures_are_deterministic(run, tmp_path):
    base, _ = run
    mf.write_fixtures(tmp_path / "again", n_emails=1500, n_surveys=400, seed=7, preview=5)
    for f in FILES:
        assert _sha(base / "fx" / f) == _sha(tmp_path / "again" / f), f
    assert json.loads((base / "fx" / "fixture_params.json").read_text(encoding="utf-8"))["synthetic"] is True


def test_generator_and_tagger_share_taxonomy():
    assert set(mf.ISSUES) == set(it.ISSUES)


def test_outputs_written(run):
    base, s = run
    for name in ("summary.json", "report.md", "coverage.md", "coverage.csv", "email_mentions.csv",
                 "email_ablation.csv", "canned_clusters.csv", "label_sample_300.csv",
                 "survey_methods.csv", "survey_sweep.csv"):
        assert (base / "out" / name).stat().st_size > 0, name
    assert s["synthetic"] is True
    report = (base / "out" / "report.md").read_text(encoding="utf-8")
    assert "合成資料" in report


def test_email_cleaning_against_truth(run):
    _, s = run
    st, ev = s["email"]["stats"], s["truth_eval"]
    assert st["total"] == 1500
    assert st["included"] + st["excluded_canned_template"] + st["excluded_agent_signature"] \
        + st["excluded_duplicate"] + st["excluded_empty"] == 1500
    out = ev["outcome"]
    canned_total = sum(n for k, n in out.items() if k.startswith("canned|"))
    assert out.get("canned|canned_template", 0) / canned_total >= 0.95
    customers = sum(n for k, n in out.items() if k.startswith("customer|"))
    assert out.get("customer|canned_template", 0) / customers <= 0.02
    assert ev["canned_customers"]["n"] == out.get("customer|canned_template", 0) \
        + out.get("customer_resend|canned_template", 0)
    assert ev["issue_lines_lost"] == 0


def test_canned_rule_steps_each_reduce_customer_misjudgements(run):
    """罐頭判定每加一個條件，被誤判的顧客信都要變少（seed 7：143 → 42 → 3 → 0；40,000 封：3,835 → 1,296 → 151 → 47）。"""
    _, s = run
    st, cb = s["email"]["stats"], s["truth_eval"]["canned_by_kind"]
    steps = [k for k, _ in run_q1.CANNED_RULES]
    assert steps == ["naive", "residual", "adjacent", "final"]
    customer = [cb["customer"][k] for k in steps]
    assert customer[0] > customer[1] > customer[2] >= customer[3]
    assert customer[0] / cb["customer"]["total"] >= 0.05             # 只看行頻率會誤判 5% 以上的顧客信
    # B、C、D 一個比一個嚴；D 就是實際排除的罐頭回覆
    counts = [st["canned_rule_counts"][k] for k in steps]
    assert counts[1] >= counts[2] >= counts[3] == st["excluded_canned_template"]
    assert cb["canned"]["final"] / cb["canned"]["total"] >= 0.95
    # 共同出現率：留下的模板相鄰對都 ≥ 0.5，被擋掉的都 < 0.5；summary 要存每一對的 df 與共同出現率
    pairs = s["email"]["template_pairs"]
    assert len(pairs["kept"]) == st["template_pairs"] > 0
    assert all(p["conf"] >= 0.5 and p["df"] >= st["min_df"] for p in pairs["kept"])
    assert all(p["conf"] < 0.5 for p in pairs["dropped"])


def test_shares_cleaning_bias_small_and_tagger_bias_downward(run):
    """估計佔比和實際佔比的差距拆成兩段，分開檢查。

    - 清理造成的：和「清理完全正確時同一套規則的佔比」（oracle_share）比，seed 7 最大差 0.33 個百分點，門檻 1 個百分點。
    - 分類規則造成的：產生器有 12% 的說法不在關鍵詞裡（recall 約 0.82～0.92），所以估計值應該全部偏低。
      直接拿估計值和實際值比固定門檻（以前是 ≤ 5 個百分點）量到的是產生器的亂數，不是程式，所以不這樣測。
    """
    _, s = run
    ev = s["truth_eval"]
    for r in ev["per_issue"]:
        assert abs(r["est_share"] - r["oracle_share"]) <= 0.01, r["issue"]
        assert r["est_share"] < r["true_share"], r["issue"]
    # 前三名組成要對；名次在小樣本可能互換（seed 7 時第 1、2 名互換：「沒有閃退」誤標＋同步的說法漏抓）
    assert {r["issue"] for r in s["email"]["table"][:3]} == set(ev["true_top3"])


def test_label_sample_has_300_rows_with_excluded_strata(run):
    base, s = run
    with open(base / "out" / "label_sample_300.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 300 and s["sample"]["size"] == 300
    strata = {r["stratum"] for r in rows}
    assert {"排除：罐頭回覆", "排除：客服個別回覆"} <= strata
    assert all(r["human_tags"] == "" and r["human_is_agent"] == "" for r in rows)


def test_simulated_annotator_runs_the_label_contract(run):
    """用答案模擬完全正確的標註者：evaluate_labels 與 estimate_exclusion_errors 都跑得出數字。"""
    _, s = run
    lc = s["label_check"]
    assert set(lc["tags"]) == set(it.ISSUES)
    ex = lc["exclusion"]
    assert ex["excluded"]["labeled"] == 40                    # 兩個排除層各 20 則
    assert ex["included"]["labeled"] == 260
    assert set(lc["actual"]["excluded_customers"]) == {"排除：罐頭回覆", "排除：客服個別回覆"}
    # 沒有簽名檔的客服回覆會混進分析；模擬標註者看得出來
    assert lc["actual"]["included_non_customers"] > 0


def test_survey_sensitivity(run):
    _, s = run
    m = s["survey"]["methods"]
    assert s["survey"]["counts"]["未知"] == 8                # 400 × 2%
    assert m["new_only"]["top3_set_changed"]                 # 排除老客戶後前三名改變
    assert m["new_only"]["top3_change_beyond_ci"]            # 而且超出誤差（產生器的新客戶在意的問題不同）
    assert all("top3_change_beyond_ci" in v and "top3_status" in v for v in m.values())
    assert m["weighted"]["n_eff"] < m["unweighted"]["n_eff"]
    assert len(s["survey"]["sweep"]) == 13
    assert all("clear_top3" in x for x in s["survey"]["sweep"])


def test_coverage_table_lists_dropped_channel(run):
    base, s = run
    md = (base / "out" / "coverage.md").read_text(encoding="utf-8")
    assert "社群貼文" in md and "未納入計數" in md and "只作佐證" in md
    # 社群抽樣 200 則沒有做：筆數欄留「—」，寫明計畫、未執行
    social = s["coverage"]["rows"][3]
    assert social["raw"] == "—" and social["method"].startswith("計畫，未執行")
    email = s["coverage"]["rows"][0]
    assert email["excluded"] == email["raw"] - email["included"]


def test_coverage_render_formats_numbers():
    rows = [cov.dropped_row("社群貼文", 150000, "不納入", "低估公開抱怨")]
    md = cov.render_markdown(rows)
    assert "| 社群貼文 | 150,000 | 0 | 150,000 |" in md
    assert md.splitlines()[2].startswith("| 通路 | 原始筆數 |")
    done = cov.qualitative_row("社群貼文抽樣", 200, "人工定性檢視", done=True)
    assert done["raw"] == 200 and not done["method"].startswith("計畫")


def test_fixture_phone_numbers_are_placeholders(run):
    """顧客手機一律是佔位號碼；舊版的尾碼是隨機 3 碼，產生的號碼可能剛好是真人門號。"""
    base, _ = run
    text = (base / "fx" / "emails.jsonl").read_text(encoding="utf-8")
    assert mf.PLACEHOLDER_MOBILE in text
    assert not re.search(r"09\d{2}-\d{3}-\d{3}", text.replace(mf.PLACEHOLDER_MOBILE, ""))
