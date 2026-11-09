import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakeptt import FakeBoard, FakeClock, FakePtt  # noqa: E402

BQ = os.environ.get("Q3_TEST_BQ_ENDPOINT", "http://127.0.0.1:9050")


def _bq_up():
    try:
        requests.get(f"{BQ}/bigquery/v2/projects/qsearch-q3/datasets", timeout=3)
        return True
    except requests.RequestException:
        return False


def _dataset_name():
    # 模擬器對「底線後接數字」的資料集名稱有解析問題，只用英文字母
    return "t" + "".join(chr(97 + int(c, 16) % 26) for c in uuid.uuid4().hex[:10])


@pytest.fixture
def pipe(tmp_path):
    """一個獨立的 dataset＋模擬的 PTT 八卦板＋可以撥快的時鐘。沒有 BigQuery 模擬器就跳過。"""
    if not _bq_up():
        pytest.skip(f"BigQuery 模擬器沒開（{BQ}）：docker compose up -d bigquery")
    from q3pipe import job
    from q3pipe.bq import Warehouse
    from q3pipe.config import Config
    from q3pipe.metrics import Metrics
    from q3pipe.ptt import Fetcher
    from q3pipe.rawstore import LocalStore

    clock = FakeClock()
    board = FakeBoard(clock)
    site = FakePtt(board).__enter__()
    made = []

    def make(**override):
        kw = dict(base_url=site.base_url, bq_endpoint=BQ, dataset=_dataset_name(), raw_backend="local",
                  raw_dir=str(tmp_path / "raw"), min_interval_s=0.0, backoff_base_s=0.0, pause_s=0.0,
                  batch_size=10, gate_min_batch=5)
        kw.update(override)
        cfg = Config(**kw)
        wh = Warehouse(cfg)
        wh.ensure()
        metrics = Metrics(cfg)
        fetcher = Fetcher(cfg, on_request=metrics.on_request, on_backoff=metrics.backoff.set, sleep=lambda s: None)
        ctx = job.Ctx(cfg=cfg, wh=wh, store=LocalStore(cfg.raw_dir), fetcher=fetcher, metrics=metrics, now=clock.now)
        made.append(wh)
        return ctx

    yield SimpleNamespace(clock=clock, board=board, site=site, make=make)
    site.__exit__(None, None, None)
    for wh in made:
        try:
            wh.c.delete_dataset(wh.cfg.dataset, delete_contents=True, not_found_ok=True)
        except Exception:
            pass
