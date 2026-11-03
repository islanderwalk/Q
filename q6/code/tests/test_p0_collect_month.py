"""P0-3：上游計數的月份。month_tw 把 published_at 轉成 +08:00 的 yyyy-mm，缺值或解析不了回 "unknown"。
collect/common.py 在 import 時會建立 data 目錄，所以這裡用 ast 只取出 month_tw 這一個函式，不 import 整個模組。"""
import ast
from datetime import datetime, timedelta, timezone

from _p0_fakes import COLLECT, read_text


def _load_month_tw():
    tree = ast.parse(read_text(COLLECT / "common.py"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "month_tw")
    ns = {"datetime": datetime, "timezone": timezone, "timedelta": timedelta,
          "TW": timezone(timedelta(hours=8))}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "month_tw", "exec"), ns)
    return ns["month_tw"]


month_tw = _load_month_tw()


def test_utc_late_on_last_day_rolls_into_next_month_in_taipei():
    # 2026-01-31T20:00Z 在台北已是 2026-02-01 04:00
    assert month_tw("2026-01-31T20:00:00+00:00") == "2026-02"


def test_offset_timestamp_keeps_taipei_month():
    assert month_tw("2026-03-15T10:00:00+08:00") == "2026-03"


def test_naive_timestamp_is_read_as_taipei_time():
    assert month_tw("2026-04-30T23:30:00") == "2026-04"


def test_missing_or_bad_values_are_unknown_not_guessed():
    assert month_tw(None) == "unknown"
    assert month_tw("") == "unknown"
    assert month_tw("not a date") == "unknown"


def test_z_suffix_is_read_as_utc_on_python_310():
    # Python 3.10 的 datetime.fromisoformat 不吃結尾 Z；這裡當成 UTC 處理
    assert month_tw("2026-01-31T20:00:00Z") == "2026-02"
    assert month_tw("2026-03-15T10:00:00Z") == "2026-03"
