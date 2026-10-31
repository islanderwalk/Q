"""Q1 問卷分層、事後分層加權與排序敏感度的離線測試（手算可驗證的小樣本）。"""
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))

import make_fixtures as mf  # noqa: E402
import survey_weight as sw  # noqa: E402

SYNC, CRASH, PAY, LOGIN, SPEED, UI = "同步與資料遺失", "閃退與當機", "付款與扣款", "登入與帳號", "速度與效能", "介面與操作"


def _row(k, tenure, issues):
    return {"resp_id": f"R{k}", "tenure_months": tenure, "satisfaction": 3, "issues": issues,
            "stratum": sw.stratum(tenure)}


def _toy():
    """8 位老客戶都勾同步，2 位新客戶都勾登入，1 位購買期間空白。"""
    return [_row(k, 12, [SYNC]) for k in range(8)] + [_row(8 + k, 2, [LOGIN]) for k in range(2)] + [_row(10, None, [PAY])]


def test_stratum_boundary():
    assert sw.stratum(6) == sw.OLD and sw.stratum(5) == sw.NEW and sw.stratum(None) == sw.UNKNOWN


def test_poststrat_weights_by_hand():
    w = sw.poststrat_weights(_toy(), 0.5)
    assert w[:8] == [pytest.approx(0.625)] * 8      # 0.5 / 0.8
    assert w[8:10] == [pytest.approx(2.5)] * 2      # 0.5 / 0.2
    assert w[10] == 0.0                             # 購買期間空白
    assert sum(w) == pytest.approx(10)
    assert sw.kish_neff(w) == pytest.approx(6.4)    # 10² / (8·0.625² + 2·2.5²)


def test_weighted_shares_and_tie_order():
    rows = _toy()
    ranking, _ = sw.weighted_ranking(rows, sw.poststrat_weights(rows, 0.5))
    share = {r["issue"]: r["share"] for r in ranking}
    assert share[SYNC] == pytest.approx(0.5) and share[LOGIN] == pytest.approx(0.5) and share[PAY] == 0.0
    assert sw.top3(ranking)[:2] == [SYNC, LOGIN]   # 同分依 ISSUES 順序


def test_poststrat_errors():
    with pytest.raises(ValueError):
        sw.poststrat_weights(_toy(), 1.5)
    with pytest.raises(ValueError):
        sw.poststrat_weights([_row(0, 12, [SYNC])], 0.5)


def test_compare_methods_detects_top3_change():
    # 老客戶 80 位：同步 80、閃退 60、速度 40；新客戶 20 位：登入 20、介面 15、付款 10
    rows = []
    for k in range(80):
        rows.append(_row(k, 24, [SYNC] + ([CRASH] if k % 4 else []) + ([SPEED] if k % 2 == 0 else [])))
    for k in range(20):
        rows.append(_row(100 + k, 1, [LOGIN] + ([UI] if k % 4 else []) + ([PAY] if k % 2 == 0 else [])))
    m = sw.compare_methods(rows, base_old_share=0.5)
    assert m["unweighted"]["top3"] == [SYNC, CRASH, SPEED]
    assert m["old_only"]["top3"] == [SYNC, CRASH, SPEED] and not m["old_only"]["top3_set_changed"]
    assert m["new_only"]["top3"] == [LOGIN, UI, PAY] and m["new_only"]["top3_set_changed"]
    assert m["weighted"]["n_eff"] < m["unweighted"]["n_eff"]
    sweep = sw.sensitivity_sweep(rows)
    assert [x["base_old_share"] for x in sweep][:2] == [0.2, 0.25] and len(sweep) == 13
    assert sweep[-1]["top3"] == [SYNC, CRASH, SPEED]
    assert sweep[0]["top3"][:2] == [LOGIN, UI]


def _ranking(pairs, half=0.02):
    """[(問題, 佔比)] → weighted_ranking 的格式，每個佔比的 CI 半寬都是 half。"""
    return [{"rank": k, "issue": i, "share": p, "ci_low": p - half, "ci_high": p + half}
            for k, (i, p) in enumerate(pairs, 1)]


def test_top3_status_and_change_beyond_ci():
    # 合成半寬 √2 × 0.02 ≈ 0.028：差距小於它就分不出先後
    base = _ranking([(SYNC, 0.40), (CRASH, 0.30), (SPEED, 0.25), (PAY, 0.24), (LOGIN, 0.10), (UI, 0.05)])
    st = sw.top3_status(base)
    assert st == {SYNC: "in", CRASH: "in", SPEED: "tie", PAY: "tie", LOGIN: "out", UI: "out"}
    # 只有平手區的第 3、4 名互換：名單不同，但沒有超出誤差
    swapped = _ranking([(SYNC, 0.40), (CRASH, 0.30), (PAY, 0.25), (SPEED, 0.24), (LOGIN, 0.10), (UI, 0.05)])
    assert not sw.top3_change_beyond_ci(sw.top3_status(swapped), st)
    # 登入從明確在外變成明確在內：超出誤差
    moved = _ranking([(LOGIN, 0.40), (SYNC, 0.38), (CRASH, 0.30), (SPEED, 0.20), (PAY, 0.10), (UI, 0.05)])
    assert sw.top3_status(moved)[LOGIN] == "in"
    assert sw.top3_change_beyond_ci(sw.top3_status(moved), st)


def test_compare_methods_and_sweep_report_beyond_ci():
    rows = []
    for k in range(400):
        rows.append(_row(k, 24, [SYNC] + ([CRASH] if k % 4 else []) + ([SPEED] if k % 2 == 0 else [])))
    for k in range(100):
        rows.append(_row(1000 + k, 1, [LOGIN] + ([UI] if k % 4 else []) + ([PAY] if k % 2 == 0 else [])))
    m = sw.compare_methods(rows, base_old_share=0.5)
    assert not m["unweighted"]["top3_change_beyond_ci"]
    assert m["new_only"]["top3_set_changed"] and m["new_only"]["top3_change_beyond_ci"]
    assert m["old_only"]["top3_status"][SYNC] == "in"
    sweep = sw.sensitivity_sweep(rows)
    assert sweep[-1]["clear_top3"] == [SYNC, CRASH, SPEED] and not sweep[-1]["top3_change_beyond_ci"]
    assert sweep[0]["top3_change_beyond_ci"]


def test_generator_expected_shares_tie_ranks_by_design():
    """產生器的期望值：RESULTS 說「這幾名本來就設成幾乎同分」的依據。"""
    e0, e50, e100 = (mf.expected_survey_shares(s) for s in (0.0, 0.5, 1.0))
    for i in mf.ISSUES:
        assert e50[i] == pytest.approx((e0[i] + e100[i]) / 2)       # 加權到 50% 等於兩層期望值平均
        assert e100[i] <= mf.SURVEY_P["old"][i]                      # 最多勾 3 項只會讓比例變低
    assert sum(e100.values()) <= 3
    assert e100[SYNC] == pytest.approx(0.387, abs=1e-3)
    assert e50[SPEED] == pytest.approx(0.205, abs=1e-3)
    # 母體老客戶 50%：登入、介面、閃退相差不到 0.2 個百分點；80%（樣本組成）：速度、匯出、閃退相差不到 0.5 個百分點
    assert max(e50[i] for i in (LOGIN, UI, CRASH)) - min(e50[i] for i in (LOGIN, UI, CRASH)) < 0.002
    e80 = mf.expected_survey_shares(0.8)
    assert max(e80[i] for i in (SPEED, "匯出與報表", CRASH)) - min(e80[i] for i in (SPEED, "匯出與報表", CRASH)) < 0.005


def test_load_survey_blank_tenure(tmp_path):
    p = tmp_path / "survey.csv"
    p.write_text("resp_id,tenure_months,satisfaction,issues\nR1,12,4,同步與資料遺失|登入與帳號\nR2,,2,\n",
                 encoding="utf-8")
    rows = sw.load_survey(p)
    assert rows[0]["stratum"] == sw.OLD and rows[0]["issues"] == [SYNC, LOGIN]
    assert rows[1]["stratum"] == sw.UNKNOWN and rows[1]["issues"] == []
    assert sw.stratum_counts(rows) == {sw.OLD: 1, sw.NEW: 0, sw.UNKNOWN: 1}
