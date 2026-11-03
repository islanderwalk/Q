"""parser 與部署設定的契約測試：Avro schema ↔ Pydantic Event ↔ BigQuery 表、Terraform ↔ main.py／Dockerfile。

只讀 shared/infra/gcp 的 .tf 與 .avsc，不改。fastavro 有裝就用它驗 Avro；沒裝時只跑手寫的欄位與型別檢查。
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

from parser_cases import AVSC, AVSC_PATH, PARSER_DIR, PATH, VALID_EVENT, tf_text, without

AVRO_TO_PY = {"string": str, "int": int}
AVRO_TO_BQ = {"string": "STRING", "int": "INTEGER"}


# (id, record, Avro 是否合法)。schema_version=2 對 Avro 合法，是 parser 在語意層拒收（見 test_parser_push）。
SAMPLES = [
    ("valid", VALID_EVENT, True),
    ("extra-field", {**VALID_EVENT, "fetched_bytes": 1}, True),
    ("version-2", {**VALID_EVENT, "schema_version": 2}, True),
    *[(f"no-{f['name']}", without(VALID_EVENT, f["name"]), False) for f in AVSC["fields"]],
    ("version-bool", {**VALID_EVENT, "schema_version": True}, False),
    ("version-str", {**VALID_EVENT, "schema_version": "1"}, False),
    ("version-float", {**VALID_EVENT, "schema_version": 1.0}, False),
    ("version-int32-overflow", {**VALID_EVENT, "schema_version": 2**31}, False),
    ("event_id-int", {**VALID_EVENT, "event_id": 123}, False),
    ("job_id-null", {**VALID_EVENT, "job_id": None}, False),
]


def _avro_ok(record):
    """手寫的 Avro 檢查：只涵蓋這份 schema 用到的 string 與 int（32 位元），多的欄位忽略。"""
    for field in AVSC["fields"]:
        value = record.get(field["name"], ...)
        if field["type"] == "string" and not isinstance(value, str):
            return False
        if field["type"] == "int" and (type(value) is not int or not -2**31 <= value < 2**31):
            return False
    return True


def test_avsc_is_flat_record_of_primitives():
    assert AVSC["type"] == "record" and AVSC["name"] == "CrawlFetchedEvent"
    assert [f["name"] for f in AVSC["fields"]] == [
        "event_id", "schema_version", "job_id", "platform", "event_type", "raw_data_uri"]
    assert all(f["type"] in AVRO_TO_PY and "default" not in f for f in AVSC["fields"])


def test_event_model_matches_avsc(parser_main):
    """欄位名稱、順序、型別一致；Avro 欄位沒有 default，所以 Pydantic 欄位也都必填，且用 strict 不轉型。"""
    fields = parser_main.Event.model_fields
    assert list(fields) == [f["name"] for f in AVSC["fields"]]
    for f in AVSC["fields"]:
        assert fields[f["name"]].annotation is AVRO_TO_PY[f["type"]]
        assert fields[f["name"]].is_required(), f"{f['name']} 有預設值，Avro 沒有"
    assert parser_main.Event.model_config.get("strict") is True


@pytest.mark.parametrize("record,avro_valid", [(r, v) for _, r, v in SAMPLES], ids=[s[0] for s in SAMPLES])
def test_handwritten_avro_check(record, avro_valid):
    assert _avro_ok(record) is avro_valid


@pytest.mark.parametrize("record,avro_valid", [(r, v) for _, r, v in SAMPLES], ids=[s[0] for s in SAMPLES])
def test_fastavro_check(record, avro_valid):
    fastavro = pytest.importorskip("fastavro")
    from fastavro.validation import validate
    schema = fastavro.parse_schema(AVSC)
    assert validate(record, schema, raise_errors=False) is avro_valid


@pytest.mark.parametrize("record", [r for _, r, v in SAMPLES if not v], ids=[s[0] for s in SAMPLES if not s[2]])
def test_avro_invalid_event_is_never_acked(client, envelope, record):
    """主題的 Avro schema 會在發佈時擋掉的事件，parser 也一律不 ack。"""
    assert client.post(PATH, json=envelope(record)).status_code == 422


def test_avro_valid_v1_event_is_acked(client, envelope):
    assert _avro_ok(VALID_EVENT)
    assert client.post(PATH, json=envelope(VALID_EVENT)).status_code == 204


def test_bq_ingest_events_matches_avsc(tf_block):
    """Pub/Sub BigQuery 訂閱 use_topic_schema=true，表欄位要和 Avro 逐欄對應。"""
    table = tf_block("bq.tf", "google_bigquery_table", "ingest_events")
    cols = re.findall(r'name\s*=\s*"(\w+)",\s*type\s*=\s*"(\w+)"', table)
    assert cols == [(f["name"], AVRO_TO_BQ[f["type"]]) for f in AVSC["fields"]]


def test_topic_uses_this_avsc_with_json_encoding(tf_block):
    """main.py 把 data 當 JSON 解；主題若改成 BINARY 編碼，parser 會全部 422。"""
    schema = tf_block("pubsub.tf", "google_pubsub_schema", "qsearch_event_v1")
    assert 'type       = "AVRO"' in schema and AVSC_PATH.name in schema
    topic = tf_block("pubsub.tf", "google_pubsub_topic", "qsearch_fetched")
    assert re.search(r'encoding\s*=\s*"JSON"', topic)


def test_push_endpoint_is_a_post_route(parser_main, tf_block):
    sub = tf_block("pubsub.tf", "google_pubsub_subscription", "qsearch_parser_sub")
    endpoint = re.search(r'push_endpoint\s*=\s*"\$\{google_cloud_run_v2_service\.parser\.uri\}(/[^"]*)"', sub)
    assert endpoint, "push_endpoint 不是 parser 服務 URL 加路徑"
    routes = {(r.path, m) for r in parser_main.app.routes for m in getattr(r, "methods", ())}
    assert (endpoint.group(1), "POST") in routes
    assert "topic                      = google_pubsub_topic.qsearch_fetched.id" in sub


def test_push_is_wrapped_and_authenticated(tf_block):
    """main.py 解析 wrapped envelope（message.data），所以訂閱不能開 no_wrapper；OIDC 用 push SA，invoker 只給它。"""
    sub = tf_block("pubsub.tf", "google_pubsub_subscription", "qsearch_parser_sub")
    assert "no_wrapper" not in sub
    assert re.search(r"oidc_token\s*\{\s*service_account_email\s*=\s*google_service_account\.push\.email", sub)
    invoker = tf_block("run.tf", "google_cloud_run_v2_service_iam_member", "parser_push_invoker")
    assert 'role     = "roles/run.invoker"' in invoker
    assert "google_service_account.push.email" in invoker
    members = re.findall(r'member\s*=\s*"([^"]+)"', tf_text("run.tf"))
    assert members and not {"allUsers", "allAuthenticatedUsers"} & set(members)


def test_timeout_matches_ack_deadline_and_dlq(tf_block):
    service = tf_block("run.tf", "google_cloud_run_v2_service", "parser")
    sub = tf_block("pubsub.tf", "google_pubsub_subscription", "qsearch_parser_sub")
    timeout = int(re.search(r'timeout\s*=\s*"(\d+)s"', service).group(1))
    ack = int(re.search(r"ack_deadline_seconds\s*=\s*(\d+)", sub).group(1))
    attempts = int(re.search(r"max_delivery_attempts\s*=\s*(\d+)", sub).group(1))
    assert timeout == ack == 60
    assert 5 <= attempts <= 100
    assert "dead_letter_topic     = google_pubsub_topic.parser_dlq.id" in sub


def test_port_8080_in_terraform_and_dockerfile(tf_block):
    service = tf_block("run.tf", "google_cloud_run_v2_service", "parser")
    assert re.search(r"container_port\s*=\s*8080", service)
    assert "${PORT:-8080}" in (PARSER_DIR / "Dockerfile").read_text(encoding="utf-8")


def test_no_env_vars_needed(tf_block):
    """run.tf 沒給 parser 任何 env；main.py 也不讀環境變數，在空環境下就能載入。"""
    assert not re.search(r"\benv\s*\{", tf_block("run.tf", "google_cloud_run_v2_service", "parser"))
    source = (PARSER_DIR / "main.py").read_text(encoding="utf-8")
    assert "os.environ" not in source and "getenv" not in source
    code = f"import sys; sys.path.insert(0, {str(PARSER_DIR)!r}); import main; print(main.app.title)"
    env = {"PYTHONDONTWRITEBYTECODE": "1"}
    if "SYSTEMROOT" in os.environ:  # Windows 上 Python 少了它無法初始化，與 parser 設定無關
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    out = subprocess.run([sys.executable, "-B", "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "QSearch PubSub Parser Lab"


def _dockerfile_cmd():
    text = (PARSER_DIR / "Dockerfile").read_text(encoding="utf-8")
    return json.loads(re.search(r"^CMD\s+(\[.*\])\s*$", text, flags=re.M).group(1))


# 允許之後固定 digest（python:3.12-slim@sha256:…），RESULTS.md 建議這麼做。
FROM_RE = re.compile(r"^FROM python:3\.12-slim(@sha256:[0-9a-f]{64})?$", flags=re.M)


@pytest.mark.parametrize("line,ok", [
    ("FROM python:3.12-slim", True),
    ("FROM python:3.12-slim@sha256:" + "a" * 64, True),
    ("FROM python:3.12-slim@sha256:abc", False),
    ("FROM python:3.13-slim", False),
])
def test_from_pattern_allows_digest_pin(line, ok):
    assert bool(FROM_RE.search(line)) is ok


def test_dockerfile_copies_runtime_files_only():
    text = (PARSER_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert FROM_RE.search(text)
    assert re.findall(r"^COPY (\S+)", text, flags=re.M) == ["requirements.txt", "main.py"]
    assert re.search(r"^ENV PYTHONUNBUFFERED=1$", text, flags=re.M), "沒有 unbuffered，log 可能延遲或在當機時遺失"
    assert _dockerfile_cmd()[:2] == ["sh", "-c"]


# python:3.12-slim（Debian 13）的 /bin/sh 是 dash 0.5.12，它對 `sh -c "單一指令"` 不會自動 exec；
# bash 會，所以有 dash 時用 dash 跑，測試才分辨得出有沒有寫 exec。
SH = shutil.which("dash") or shutil.which("sh")


@pytest.mark.skipif(SH is None or os.name == "nt", reason="需要 POSIX sh（Windows 跳過）")
@pytest.mark.parametrize("port_env,expected", [(None, "8080"), ("9090", "9090")])
def test_dockerfile_cmd_port_default_and_exec(tmp_path, port_env, expected):
    """用假的 uvicorn 執行 Dockerfile 的 CMD：PORT 未設時用 8080；且 uvicorn 必須 exec 成為原本的程序（容器內的 PID 1），
    否則 sh 會留在 PID 1，收不到要轉送的 SIGTERM，Cloud Run 關機時只能等 SIGKILL。"""
    fake = tmp_path / "bin" / "uvicorn"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\necho "$$ $*"\n')
    fake.chmod(0o755)
    env = {"PATH": f"{fake.parent}{os.pathsep}/usr/bin:/bin"}
    if port_env is not None:
        env["PORT"] = port_env
    cmd = _dockerfile_cmd()
    proc = subprocess.Popen([SH, *cmd[1:]], env=env, stdout=subprocess.PIPE, text=True)
    out, _ = proc.communicate(timeout=10)
    pid, _, args = out.strip().partition(" ")
    assert proc.returncode == 0
    assert args == f"main:app --host 0.0.0.0 --port {expected}"
    assert int(pid) == proc.pid, "CMD 沒有 exec uvicorn"


def _requirement_names():
    lines = (PARSER_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return {re.split(r"[<>=!~\[ ;]", line.strip(), maxsplit=1)[0].lower().replace("_", "-")
            for line in lines if line.strip() and not line.lstrip().startswith("#")}


def test_requirements_cover_imports():
    """main.py 匯入的第三方套件與 Dockerfile 用的 uvicorn 都要在 requirements.txt。
    google-cloud-pubsub 沒用到（dry-run 不發佈後續事件），已從 requirements 拿掉（Terraform 整理後併入）；
    之後要發佈事件、加回來時也不會讓這個測試失敗。"""
    tree = ast.parse((PARSER_DIR / "main.py").read_text(encoding="utf-8"))
    imported = {(n.module if isinstance(n, ast.ImportFrom) else a.name).split(".")[0]
                for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in (n.names if isinstance(n, ast.Import) else [n])}
    third_party = {m for m in imported if m not in sys.stdlib_module_names}
    names = _requirement_names()
    assert third_party == {"fastapi", "pydantic"}
    assert third_party | {"uvicorn"} <= names
    assert "google" not in imported


SMOKE = PARSER_DIR / "tests" / "smoke.sh"


@pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="需要 bash（Windows 跳過）")
def test_smoke_script_syntax():
    """tests/smoke.sh 要 Docker 才能跑，這裡只用 bash -n 檢查語法，並確認它比對的是 Terraform 的 push 路徑與 port 8080。"""
    out = subprocess.run(["bash", "-n", str(SMOKE)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    text = SMOKE.read_text(encoding="utf-8")
    assert PATH in text and ':8080"' in text and 'exit "$fail"' in text
