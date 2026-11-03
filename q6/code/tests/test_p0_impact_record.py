"""P0-5：impact.record_missed 的回傳值（受影響列數）。dictionary／normalize／pipeline／psycopg 都用替身，不連 PG、不跑斷詞。
impact.main 用 multiprocessing 的 fork，Windows 上不可用，所以這裡不測 main。"""
import importlib
import sys
import types

import pytest

from _p0_fakes import SEG

sys.path.insert(0, str(SEG))


class _Cur:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _Conn:
    def __init__(self, calls, rowcount):
        self._calls = calls
        self._rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._calls.append(("execute", sql, params))
        return _Cur(self._rowcount)


@pytest.fixture
def impact(monkeypatch):
    calls = []
    state = {"rowcount": 1}

    def connect(dsn, autocommit=False):
        calls.append(("connect", dsn, autocommit))
        return _Conn(calls, state["rowcount"])

    fake_psycopg = types.ModuleType("psycopg")
    fake_psycopg.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    stubs = {
        "qseg.dictionary": {"load": lambda *a, **k: None},
        "qseg.normalize": {"normalize": lambda *a, **k: ""},
        "qseg.pipeline": {"segment": lambda *a, **k: []},
    }
    for name, attrs in stubs.items():
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "qseg.impact", raising=False)
    mod = importlib.import_module("qseg.impact")
    return mod, calls, state


def test_record_missed_returns_rowcount_and_passes_params(impact):
    mod, calls, state = impact
    state["rowcount"] = 1
    assert mod.record_missed("dsn://x", "v9", 7) == 1
    assert calls[0] == ("connect", "dsn://x", True)
    _, sql, params = calls[1]
    assert "UPDATE dict_releases SET bigram_audit_missed" in sql
    assert params == (7, "v9")


def test_record_missed_is_zero_when_version_row_is_absent(impact):
    mod, calls, state = impact
    state["rowcount"] = 0
    assert mod.record_missed("dsn://x", "no-such-version", 3) == 0
