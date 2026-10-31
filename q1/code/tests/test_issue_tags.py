"""Q1 問題分類、提及數與 300 則分層抽樣的離線測試。"""
import csv
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))

import issue_tags as it  # noqa: E402

SYNC, CRASH, PAY, LOGIN, SPEED = "同步與資料遺失", "閃退與當機", "付款與扣款", "登入與帳號", "速度與效能"


def test_tag_text_negation_and_clauses():
    assert it.tag_text("登入都正常，但是同步後資料不見了") == [SYNC]
    assert it.tag_text("同步沒問題") == []
    assert it.tag_text("付款那邊已經解決了") == []
    # 「正常」前面有否定詞是問題描述
    assert it.tag_text("沒辦法正常登入") == [LOGIN]
    assert it.tag_text("信用卡被多扣，而且一直登不進去") == [PAY, LOGIN]


def test_tag_text_ascii_whole_word_and_fullwidth():
    assert it.tag_text("用起來很 lag") == [SPEED]
    assert it.tag_text("the flag icon is red") == []
    assert it.tag_text("ＣＲＡＳＨ again") == [CRASH]


def test_known_limit_negation_without_keyword():
    """已知限制：「沒有閃退」這類否定沒有列入規則，會誤標（合成資料的 negated_mention 難例）。"""
    assert it.tag_text("App 本身沒有閃退") == [CRASH]


def test_mention_table_counts_shares_and_ranks():
    rows, untagged = it.mention_table([[SYNC], [SYNC, CRASH], [], [CRASH], [SYNC]])
    assert untagged == 1
    top = {r["issue"]: r for r in rows}
    assert top[SYNC]["mentions"] == 3 and top[SYNC]["share"] == pytest.approx(0.6) and top[SYNC]["rank"] == 1
    assert top[CRASH]["mentions"] == 2 and top[CRASH]["rank"] == 2
    assert [r["rank"] for r in rows] == list(range(1, len(it.ISSUES) + 1))


def test_wilson_interval():
    assert it.wilson(0, 0) == (0.0, 0.0)
    lo, hi = it.wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3) and hi == pytest.approx(0.7634, abs=1e-3)


def test_allocate_floor_then_proportional_to_remaining_size():
    alloc = it.allocate({"a": 1000, "b": 100, "c": 5}, 300, min_per=10)
    # 先給 a、b 各 10 則、c 全部 5 則；剩下 275 則依剩餘大小 990：90 分，a 252.1、b 22.9，最大餘數補給 b。
    # 純比例配置（a 271、b 27）不是這個結果，RESULTS 不能寫成「比例配置」
    assert alloc == {"a": 262, "b": 33, "c": 5}
    # 層數 × 下限 > 總數時，下限取消
    assert sum(it.allocate({k: 50 for k in "abcdefghij"}, 50, min_per=10).values()) == 50


def _pool():
    pool = []
    for k in range(900):
        tags = [SYNC] if k % 3 == 0 else [CRASH] if k % 3 == 1 else []
        pool.append({"email_id": f"E{k:04d}", "tags": tags, "text": f"信件 {k}"})
    pool += [{"email_id": f"X{k:04d}", "tags": [], "text": "罐頭", "stratum": "排除：罐頭回覆"} for k in range(100)]
    return pool


def test_stratified_sample_size_weights_and_determinism():
    rows = it.stratified_sample(_pool(), size=300, fixed={"排除：罐頭回覆": 20})
    assert len(rows) == 300
    assert rows == it.stratified_sample(_pool(), size=300, fixed={"排除：罐頭回覆": 20})
    by = {}
    for r in rows:
        by.setdefault(r["stratum"], []).append(r)
    assert len(by["排除：罐頭回覆"]) == 20
    assert sum(len(v) for h, v in by.items() if not h.startswith("排除")) == 280
    for h, v in by.items():
        assert v[0]["weight"] == pytest.approx(v[0]["stratum_size"] / len(v))
    assert len({r["email_id"] for r in rows}) == 300
    assert rows[0]["sample_id"] == "S001" and rows[-1]["sample_id"] == "S300"


def test_stratified_sample_rejects_size_below_fixed_strata():
    """固定層合計 40 則時要 30 則：以前會默默回傳 40 則，現在丟錯。"""
    pool = _pool() + [{"email_id": f"Y{k:04d}", "tags": [], "text": "客服", "stratum": "排除：客服個別回覆"}
                      for k in range(100)]
    fixed = {"排除：罐頭回覆": 20, "排除：客服個別回覆": 20}
    with pytest.raises(ValueError):
        it.stratified_sample(pool, size=30, fixed=fixed)
    assert len(it.stratified_sample(pool, size=40, fixed=fixed)) == 40


def _write_labels(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=it.LABEL_COLUMNS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in it.LABEL_COLUMNS} for r in rows)


def test_read_labels_annotation_rules(tmp_path):
    path = tmp_path / "label.csv"
    _write_labels(path, [
        {"email_id": "E1", "human_tags": SYNC + "|" + CRASH},
        {"email_id": "E2", "human_tags": it.UNTAGGED},                 # 顧客信、沒有提到問題：要填「未分類」
        {"email_id": "E3"},                                            # 兩欄都空白：還沒標，略過
        {"email_id": "E4", "human_is_agent": "1", "human_tags": SYNC},  # 客服信：標籤不算
        {"email_id": "E5", "human_is_agent": "0", "human_tags": LOGIN},
    ])
    labels = it.read_labels(path)
    assert set(labels) == {"E1", "E2", "E4", "E5"}
    assert labels["E1"] == {"tags": {SYNC, CRASH}, "is_agent": False}
    assert labels["E2"] == {"tags": set(), "is_agent": False}
    assert labels["E4"] == {"tags": set(), "is_agent": True}
    assert labels["E5"] == {"tags": {LOGIN}, "is_agent": False}
    _write_labels(path, [{"email_id": "E6", "human_tags": "同步"}])       # 打錯問題名稱
    with pytest.raises(ValueError):
        it.read_labels(path)
    _write_labels(path, [{"email_id": "E7", "human_is_agent": "不確定"}])
    with pytest.raises(ValueError):
        it.read_labels(path)
    # 標了顧客信卻沒填問題：不默默當成「沒有問題」，要明確填「未分類」
    _write_labels(path, [{"email_id": "E8", "human_is_agent": "0"}])
    with pytest.raises(ValueError, match="未分類"):
        it.read_labels(path)


def test_evaluate_labels_counts_agent_rows_as_false_positives_and_skips_excluded_strata():
    rows = [{"email_id": "A", "stratum": SYNC, "weight": "10", "auto_tags": SYNC},
            {"email_id": "B", "stratum": SYNC, "weight": "10", "auto_tags": SYNC},
            {"email_id": "X", "stratum": "排除：罐頭回覆", "weight": "500", "auto_tags": ""}]
    labels = {"A": {"tags": {SYNC}, "is_agent": False}, "B": {"tags": set(), "is_agent": True},
              "X": {"tags": {SYNC}, "is_agent": False}}
    ev = it.evaluate_labels(rows, labels)
    assert ev[SYNC]["precision"] == pytest.approx(0.5)   # B 是客服信，規則標的同步是誤報
    assert ev[SYNC]["recall"] == pytest.approx(1.0)      # X 在排除層，不算進 recall
    assert ev[SYNC]["n_auto"] == 2 and ev[SYNC]["n_human"] == 1


def test_estimate_exclusion_errors_by_hand():
    rows = ([{"email_id": f"X{k}", "stratum": "排除：罐頭回覆", "stratum_size": "1000"} for k in range(20)]
            + [{"email_id": f"E{k}", "stratum": SYNC, "stratum_size": "500"} for k in range(10)]
            + [{"email_id": "U0", "stratum": CRASH, "stratum_size": "300"}])      # 沒有標註的層
    labels = {f"X{k}": {"tags": {SYNC} if k < 2 else set(), "is_agent": k >= 2} for k in range(20)}
    labels.update({f"E{k}": {"tags": set() if k == 0 else {SYNC}, "is_agent": k == 0} for k in range(10)})
    out = it.estimate_exclusion_errors(rows, labels)
    canned = next(h for h in out["strata"] if h["stratum"] == "排除：罐頭回覆")
    assert canned["errors"] == 2 and canned["rate"] == pytest.approx(0.1)       # 2 / 20 是顧客信：誤刪
    assert canned["est_emails"] == pytest.approx(100)                            # 0.1 × 1,000
    assert canned["ci_low"] == pytest.approx(it.wilson(2, 20)[0])
    assert out["excluded"]["est_emails"] == pytest.approx(100)
    assert out["included"] == {"stratum_size": 500, "labeled": 10, "errors": 1, "est_emails": pytest.approx(50),
                               "rate": pytest.approx(0.1)}                       # 1 / 10 是客服信：誤收
    unlabeled = next(h for h in out["strata"] if h["stratum"] == CRASH)
    assert unlabeled["labeled"] == 0 and unlabeled["rate"] is None


def test_export_read_and_evaluate_labels(tmp_path):
    rows = it.stratified_sample(_pool(), size=30, min_per=5)
    path = tmp_path / "label.csv"
    it.export_label_sample(rows, path)
    with open(path, encoding="utf-8-sig", newline="") as f:
        data = list(csv.DictReader(f))
    assert len(data) == 30 and list(data[0]) == list(it.LABEL_COLUMNS)
    # 模擬標註者：規則標 SYNC 的都同意；CRASH 的有一半其實是 SYNC
    for k, r in enumerate(data):
        auto = r["auto_tags"]
        r["human_tags"] = SYNC if auto == SYNC or (auto == CRASH and k % 2 == 0) else auto or it.UNTAGGED
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=it.LABEL_COLUMNS)
        w.writeheader()
        w.writerows(data)
    labels = it.read_labels(path)
    assert len(labels) == 30
    ev = it.evaluate_labels(data, labels)
    assert ev[SYNC]["precision"] == pytest.approx(1.0)
    assert ev[CRASH]["recall"] == pytest.approx(1.0)
    assert ev[CRASH]["precision"] < 1.0 and ev[SYNC]["recall"] < 1.0
