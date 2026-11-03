"""三方欄位比對：Avro schema（.avsc）× parser 的 Pydantic Event × bq.tf 的 ingest_events。

1. fastavro.parse_schema 解析 .avsc；用 tests/fixtures 的樣本做 fastavro 驗證與二進位往返。
2. 比對欄位名稱、型別、必填／可空。
3. 有不一致就列出（結束碼 0 = 執行成功，不代表沒有不一致；不一致數量印在最後一行）。
不連網路、不連 GCP。
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import fastavro
from fastavro.validation import validate

HERE = Path(__file__).resolve().parent
PARSER_DIR = HERE.parent
GCP_DIR = HERE.parents[2]
AVSC = GCP_DIR / "schemas" / "qsearch-crawl-event-v1.avsc"
BQ_TF = GCP_DIR / "bq.tf"
FIXTURES = PARSER_DIR / "tests" / "fixtures"

AVRO_TO_PY = {"string": "str", "int": "int", "long": "int", "boolean": "bool", "double": "float"}
AVRO_TO_BQ = {"string": {"STRING"}, "int": {"INTEGER", "INT64"}, "long": {"INTEGER", "INT64"}}


def load_event_model():
    spec = importlib.util.spec_from_file_location("parser_main", PARSER_DIR / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Event


def avro_fields(schema: dict) -> dict:
    out = {}
    for f in schema["fields"]:
        t = f["type"]
        nullable = isinstance(t, list) and "null" in t
        base = [x for x in t if x != "null"][0] if isinstance(t, list) else t
        out[f["name"]] = {"type": base, "required": "default" not in f and not nullable,
                          "nullable": nullable, "has_default": "default" in f}
    return out


def bq_fields(tf_text: str) -> dict:
    block = tf_text.split('resource "google_bigquery_table" "ingest_events"', 1)[1]
    pat = re.compile(r'\{\s*name\s*=\s*"([^"]+)",\s*type\s*=\s*"([^"]+)",\s*mode\s*=\s*"([^"]+)"\s*\}')
    return {n: {"type": t, "mode": m} for n, t, m in pat.findall(block)}


def main() -> int:
    raw = json.loads(AVSC.read_text())
    parsed = fastavro.parse_schema(raw)
    print(f"[fastavro] parse_schema OK: name={parsed['name']} fields={[f['name'] for f in raw['fields']]}")

    samples = {p.name: p.read_bytes() for p in sorted(FIXTURES.iterdir())}
    for name, data in samples.items():
        try:
            rec = json.loads(data)
        except json.JSONDecodeError as exc:
            print(f"[fastavro] {name}: 不是 JSON（{exc.msg}），Avro JSON 編碼下 publish 會被拒")
            continue
        ok = validate(rec, parsed, raise_errors=False)
        print(f"[fastavro] validate {name}: {'VALID' if ok else 'INVALID'}")
        if ok:
            buf = io.BytesIO()
            fastavro.schemaless_writer(buf, parsed, rec)
            buf.seek(0)
            back = fastavro.schemaless_reader(buf, parsed)
            print(f"           binary round-trip equal={back == rec} bytes={len(buf.getvalue())}")

    base = json.loads(samples["a_valid.json"])
    edge = {
        "missing_event_type": {k: v for k, v in base.items() if k != "event_type"},
        "missing_schema_version": {k: v for k, v in base.items() if k != "schema_version"},
        "schema_version_as_string": {**base, "schema_version": "1"},
        "schema_version_as_bool_true": {**base, "schema_version": True},
        "extra_field": {**base, "unexpected": "x"},
    }
    Event = load_event_model()
    print("--- 邊界值：fastavro（Avro）vs Pydantic Event（parser）---")
    for name, rec in edge.items():
        a = validate(rec, parsed, raise_errors=False)
        try:
            ev = Event.model_validate_json(json.dumps(rec))
            p = f"ACCEPT（schema_version={ev.schema_version!r}, event_type={ev.event_type!r}）"
        except Exception as exc:  # pydantic.ValidationError
            p = f"REJECT（{type(exc).__name__}）"
        print(f"  {name:28s} avro={'VALID' if a else 'INVALID':8s} pydantic={p}")

    av = avro_fields(raw)
    py = {n: {"type": f.annotation.__name__, "required": f.is_required(), "default": f.default}
          for n, f in Event.model_fields.items()}
    bq = bq_fields(BQ_TF.read_text())

    print("--- 欄位比對 ---")
    print(f"{'field':16s} {'avro':22s} {'pydantic':30s} {'bigquery':20s}")
    mismatches = []
    for n in list(dict.fromkeys([*av, *py, *bq])):
        a, p, b = av.get(n), py.get(n), bq.get(n)
        a_s = f"{a['type']} {'required' if a['required'] else 'optional'}" if a else "-"
        p_s = (f"{p['type']} {'required' if p['required'] else 'default=' + repr(p['default'])}" if p else "-")
        b_s = f"{b['type']} {b['mode']}" if b else "-"
        print(f"{n:16s} {a_s:22s} {p_s:30s} {b_s:20s}")
        if not (a and p and b):
            mismatches.append(f"{n}: 欄位不在三處都存在 avro={bool(a)} pydantic={bool(p)} bq={bool(b)}")
            continue
        if AVRO_TO_PY.get(a["type"]) != p["type"]:
            mismatches.append(f"{n}: 型別 avro={a['type']} pydantic={p['type']}")
        if b["type"] not in AVRO_TO_BQ.get(a["type"], set()):
            mismatches.append(f"{n}: 型別 avro={a['type']} bq={b['type']}")
        if a["required"] != p["required"]:
            mismatches.append(f"{n}: 必填 avro={'required' if a['required'] else 'optional'} "
                              f"pydantic={'required' if p['required'] else 'default=' + repr(p['default'])}")
        if a["required"] and b["mode"] != "REQUIRED":
            mismatches.append(f"{n}: 必填 avro=required bq={b['mode']}（放寬方向；Pub/Sub BigQuery 訂閱是否接受未驗證，模擬器不會實際寫 BQ）")
    print(f"欄位名稱集合相同：{set(av) == set(py) == set(bq)}；順序 avro={list(av)} pydantic={list(py)}")
    print("--- 不一致 ---")
    for m in mismatches:
        print("  " + m)
    print(f"MISMATCHES={len(mismatches)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
