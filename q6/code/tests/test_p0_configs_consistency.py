"""P0 監控設定的一致性：告警與儀表板引用的指標，必須真的在程式裡定義；告警 YAML 能解析；
儀表板 SQL 用到的資料表與視圖，必須在 pg/init.sql 裡建立。只讀檔案，不連任何服務。
注意：_defined 是子字串比對（指標字串有出現在程式碼裡就算定義），不是完整的 PromQL 解析。"""
import json
import re

import pytest

from _p0_fakes import API, COLLECT, MON, PG, SEG, REALENV, read_text

yaml = pytest.importorskip("yaml")

PROM_NAME = re.compile(r"\b(wordcloud_[a-z0-9_]+|qseg_[a-z0-9_]+|crawl_posts_[a-z0-9_]+|collector_[a-z0-9_]+)\b")


def _code_text():
    parts = [read_text(API / "app.py"), read_text(API / "collector.py")]
    parts += [read_text(p) for p in sorted((SEG / "qseg").glob("*.py"))]
    parts.append(read_text(COLLECT / "common.py"))
    return "\n".join(parts)


def _base(name):
    # 直方圖會多出 _bucket／_count／_sum 後綴，定義處只有基底名
    return re.sub(r"_(bucket|count|sum)$", "", name)


def _alerts():
    rules = []
    for f in sorted((MON / "rules").glob("*.yml")):   # 告警規則依關注面分檔，Prometheus 讀 rules/*.yml
        doc = yaml.safe_load(read_text(f))
        rules += [r for g in doc["groups"] for r in g["rules"] if "alert" in r]
    return rules


def _dashboard_exprs_and_sql():
    """兩張看板（主看板＋採集看板）的 PromQL 與 SQL；註記的 SQL 也算。Loki 的 LogQL 不算 PromQL。"""
    exprs, sqls = [], []
    for f in sorted((MON / "grafana" / "dashboards").glob("*.json")):
        dash = json.loads(read_text(f))
        for p in dash["panels"]:
            for t in p.get("targets", []):
                if t.get("expr") and (t.get("datasource") or {}).get("type") != "loki":
                    exprs.append((p.get("title", ""), t["expr"]))
                if t.get("rawSql"):
                    sqls.append((p.get("title", ""), t["rawSql"]))
        for a in dash.get("annotations", {}).get("list", []):
            if "target" in a and a["target"].get("rawSql"):
                sqls.append((a["name"], a["target"]["rawSql"]))
    return exprs, sqls


def test_alert_file_parses_and_has_known_severities():
    rules = _alerts()
    names = [(r["alert"], r["labels"]["severity"]) for r in rules]   # Sloth 的 page／ticket 同名、只差 severity
    assert len(names) == len(set(names))
    for r in rules:
        assert r["expr"].strip()
        assert r["labels"]["severity"] in {"page", "ticket", "warn"}, r["alert"]


def test_prometheus_config_parses_and_scrapes_coverage_job():
    doc = yaml.safe_load(read_text(MON / "prometheus.yml"))
    targets = {j["job_name"]: [t for s in j["static_configs"] for t in s["targets"]]
               for j in doc["scrape_configs"]}
    assert "seg-coverage:9110" in targets["seg-coverage"]
    compose = read_text(REALENV / "docker-compose.yml")
    assert "COVERAGE_PORT" in compose and '"9110"' in compose


def test_every_alert_metric_is_defined_in_code():
    code = _code_text()
    missing = []
    for r in _alerts():
        for name in PROM_NAME.findall(r["expr"]):
            if _base(name) not in code:
                missing.append((r["alert"], name))
    assert missing == []


def test_every_dashboard_metric_is_defined_in_code():
    code = _code_text()
    exprs, _ = _dashboard_exprs_and_sql()
    assert len(exprs) >= 90, len(exprs)          # 主看板 9 層＋採集看板 6 列
    missing = []
    for title, expr in exprs:
        for name in PROM_NAME.findall(expr):
            if _base(name) not in code:
                missing.append((title, name))
    assert missing == []


def test_dashboard_sql_only_reads_relations_defined_in_init_sql():
    init = read_text(PG / "init.sql") + read_text(PG / "live.sql")   # 帳本在 init.sql；即時操作表（live_events、collector_state…）在 live.sql
    defined = set(re.findall(r"CREATE (?:OR REPLACE )?(?:TABLE|VIEW)(?: IF NOT EXISTS)? (\w+)", init, re.I))
    _, sqls = _dashboard_exprs_and_sql()
    assert sqls, "dashboard should contain SQL panels"
    for title, sql in sqls:
        plain = re.sub(r"extract\s*\([^)]*\)", "", sql, flags=re.I)   # extract(epoch FROM at) 的 FROM 不是資料表
        for rel in re.findall(r"\bFROM\s+(\w+)", plain, re.I):
            assert rel in defined, (title, rel)


def test_dict_releases_has_p0_5_columns_and_gap_view_in_init_sql():
    init = read_text(PG / "init.sql")
    assert "ADD COLUMN IF NOT EXISTS rerun_written" in init
    assert "ADD COLUMN IF NOT EXISTS bigram_audit_missed" in init
    assert "CREATE OR REPLACE VIEW backfill_gap" in init


def test_migrate_p0_sql_is_idempotent_and_matches_init_view():
    mig = read_text(PG / "migrate_p0.sql")
    assert "ADD COLUMN IF NOT EXISTS rerun_written" in mig
    assert "ADD COLUMN IF NOT EXISTS bigram_audit_missed" in mig
    assert "CREATE OR REPLACE VIEW backfill_gap" in mig
    assert "raw_unknown" in mig
    assert "raw_unknown" in read_text(PG / "init.sql")


def test_every_alert_has_for_and_summary():
    """計畫書：告警都要有 for、severity、summary；單次事件的 for 要比 increase 的 5 分鐘窗短。
    Sloth 產生的燃燒率告警沒有 for：多視窗（短窗 and 長窗）本身就是持續條件，不另外加。"""
    for r in _alerts():
        assert "for" in r or "sloth_severity" in r["labels"], r["alert"]
        assert r.get("annotations", {}).get("summary"), r["alert"]


def test_slo_burn_rate_rules_replace_any_error_page():
    """P0 第 1 項：3 分鐘 SLO 用 Sloth 的多視窗燃燒率（28 天），SLI 分子是 le=180 且 status=ok 的桶、分母是 _count（失敗也記耗時）；
    「任何一次錯誤就 page」的 WordcloudSlaFailures 已拿掉，單次超時改開單（ticket）。"""
    rules = _alerts()
    names = {r["alert"] for r in rules}
    assert "WordcloudSlaFailures" not in names
    burn = [r for r in rules if r["alert"] == "WordcloudSlo180sBurn"]
    assert {r["labels"]["severity"] for r in burn} == {"page", "ticket"}
    slo = yaml.safe_load(read_text(MON / "rules" / "slo.yml"))
    recs = [r for g in slo["groups"] for r in g["rules"] if "record" in r]
    sli = next(r for r in recs if r["record"] == "slo:sli_error:ratio_rate5m" and r["labels"]["sloth_slo"] == "query-within-180s")
    assert 'wordcloud_duration_seconds_bucket{le="180.0",status="ok"}' in sli["expr"]
    assert "wordcloud_duration_seconds_count[5m]" in sli["expr"]
    assert "wordcloud_errors_total" not in sli["expr"]   # 失敗已在 _count 裡，再加 errors_total 會重複計數
    windows = {r["labels"]["sloth_window"] for r in recs if r["labels"].get("sloth_slo") == "query-within-180s" and "sloth_window" in r["labels"]}
    assert {"5m", "30m", "1h", "2h", "6h", "1d", "3d"} <= windows
    spec = yaml.safe_load(read_text(MON / "slo" / "wordcloud-api.yml"))
    assert [s["objective"] for s in spec["slos"]] == [99, 95]
    over = next(r for r in rules if r["alert"] == "WordcloudOver180s")
    assert over["labels"]["severity"] == "ticket" and "wordcloud_over_180s_total" in over["expr"]


def test_every_scraped_job_has_a_down_guard():
    """服務掛掉保護：靠服務自己回報的指標（積壓年齡、錯誤數）在服務掛掉時是空向量，要有 up==0／absent 的規則先響。"""
    exprs = " ".join(r["expr"] for r in _alerts())
    for job in ("wordcloud-api", "seg-service"):
        assert f'up{{job="{job}"}} == 0' in exprs and f'absent(up{{job="{job}"}})' in exprs, job
    assert "max(qseg_coverage_last_success_timestamp_seconds) or vector(0)" in exprs   # coverage 服務：從未成功也算 stale
    # 「有標籤的指標 or vector(0)」會同時留下有標籤的序列和無標籤的 0，多出一條永遠在響的 {} 告警（真實環境驗證抓到）：先 max() 去標籤
    import re as _re
    assert not _re.search(r"\((?:qseg|collector|wordcloud)_\w+(?:\{[^}]*\})? or vector\(0\)\)", exprs), "raw metric or vector(0)"


def test_alertmanager_is_wired_to_prometheus_compose_and_api_webhook():
    prom = yaml.safe_load(read_text(MON / "prometheus.yml"))
    targets = [t for am in prom["alerting"]["alertmanagers"] for s in am["static_configs"] for t in s["targets"]]
    assert targets == ["alertmanager:9093"]
    am = yaml.safe_load(read_text(MON / "alertmanager" / "alertmanager.yml"))
    urls = [w["url"] for r in am["receivers"] for w in r.get("webhook_configs", [])]
    assert urls == ["http://api:8080/api/ops/alert"]
    assert all(w.get("send_resolved") for r in am["receivers"] for w in r.get("webhook_configs", []))
    assert am["route"]["receiver"] in {r["name"] for r in am["receivers"]}
    compose = read_text(REALENV / "docker-compose.yml")
    assert "quay.io/prometheus/alertmanager:v0.28.1" in compose and "alertmanager.yml:/etc/alertmanager/alertmanager.yml" in compose
    assert '@app.post("/api/ops/alert")' in read_text(API / "app.py")
    ds = read_text(MON / "grafana" / "provisioning" / "datasources" / "ds.yml")
    assert "type: alertmanager" in ds and "alertmanager:9093" in ds
