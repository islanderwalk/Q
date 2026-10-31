"""parser 服務測試的共用 fixture（離線：TestClient 直接呼叫 ASGI app，不開 port、不連網）。

main.py 以獨立模組名 qsearch_parser_main 載入，避免和 repo 其他地方的 main 模組撞名。
Terraform 只讀不改：tf_block 取出指定 resource 區塊的文字，供契約測試比對。
fixture_bytes 讀 tests/fixtures/ 的事件檔（test_push_endpoint.py 用，與 local/VERIFY.md 的模擬器實測同一批）。
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import parser_cases as pc  # noqa: E402


def _load_main():
    name = "qsearch_parser_main"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, pc.PARSER_DIR / "main.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@pytest.fixture(scope="session")
def parser_main():
    return _load_main()


@pytest.fixture
def client(parser_main):
    return TestClient(parser_main.app)


@pytest.fixture
def valid_event():
    return dict(pc.VALID_EVENT)


@pytest.fixture
def envelope():
    """組 Pub/Sub push 的 wrapped envelope。event 是 dict 時轉 JSON 再 base64；data 直接指定時原樣放入。"""
    def make(event=None, *, data=None, attributes=None, message_id="2070443601311540"):
        if data is None:
            data = pc.b64(json.dumps(pc.VALID_EVENT if event is None else event).encode("utf-8"))
        return {
            "message": {
                "data": data,
                "messageId": message_id,
                "attributes": {"platform": "ptt"} if attributes is None else attributes,
            },
            "subscription": "projects/demo/subscriptions/qsearch-parser-sub",
        }
    return make


@pytest.fixture(scope="session")
def tf_block():
    """回傳 tf_block(檔名, resource 型別, 名稱) → 該 resource 區塊文字（已去註解）。"""
    return pc.tf_block


@pytest.fixture
def fixture_bytes():
    def _read(name: str) -> bytes:
        return (FIXTURES / name).read_bytes().strip()
    return _read
