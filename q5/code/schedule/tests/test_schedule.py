"""q5/code/schedule/schedule.py 的測試：b-c-d.md (b) 引用的數字都要從這裡算得出來。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import schedule as s  # noqa: E402


def test_role_hours_match_table():
    assert s.role_hours() == {"DE": 42, "BE": 24, "FE": 10, "ALL": 4}


def test_contract_mode_critical_path_is_de_line_plus_acceptance():
    times = s.schedule("contract")
    assert max(e for _, e in times.values()) == 46
    assert s.critical_chain(times, "contract") == [1, 2, 3, 8, 9]
    floats = s.total_float(times, "contract")
    assert [t for t, f in sorted(floats.items()) if f == 0] == [1, 2, 3, 8, 9]
    assert floats[7] == 16  # 前端最晚可晚 16 小時（2 天）完成


def test_strict_mode_makes_be_fe_chain_co_critical():
    times = s.schedule("strict")
    assert max(e for _, e in times.values()) == 46
    floats = s.total_float(times, "strict")
    assert [t for t, f in sorted(floats.items()) if f == 0] == [1, 2, 3, 4, 5, 7, 8, 9]
    assert times[7] == (24, 34) and times[8] == (34, 42)


def test_working_and_calendar_days():
    raw, with_review, total = s.working_days(46)
    assert (raw, round(with_review, 3), total) == (5.75, 7.475, 9)
    assert [s.calendar_days(9, w) for w in range(5)] == [11, 11, 13, 13, 13]
    assert s.calendar_days(8, 0) == 10 and s.calendar_days(5, 0) == 5


def test_main_prints_summary(capsys):
    assert s.main(["--mode", "contract"]) == 0
    out = capsys.readouterr().out
    assert "project_hours=46 critical_chain=1→2→3→8→9" in out and "ceil+1=9" in out
