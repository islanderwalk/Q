"""定向重跑：字典只「加詞」時，只重算原文含新詞字串的文章，不必全量回補。

成立的前提是 jieba 的 total 固定（manifest.total_pinned）：加詞不會改到不含該詞的文章（impact.py 用全量資料驗證）。
找文章用 ES 的 content.bigram 片語比對（中文兩字一組，等同「原文含這個字串」），只挑 seg_ver_v2 還比目標舊的，
重算後走同一支 seg_guard 條件更新：版本只前進，重跑中斷再跑也不會重複或倒退。

兩種進入點：命令列 `python -m qseg rerun --words 肥宅`（回補 Job 的用法），
或 control.py 收到發版訊息後在串流服務裡呼叫 run_rerun（PM 頁發版的用法），兩邊同一段程式。

規劃書修正：定向重跑也讀 backfill_control（限速、暫停）與回補共用額度，結束後也量 G3 探針（線上欄位 p95 不惡化，超過就把全域限速砍半）。
"""
import argparse
import html
import json
import time

import psycopg

from . import config, dictionary, metrics, promo
from .es import ES
from .logs import log
from .pipeline import seg_fields, seg_ver, segment


def rate_limit(conn, led=None):
    """回傳這一批的每秒篇數上限：全域上限 ÷（正在跑的回補分區數 ＋ 1）；paused 就等。conn 要 dict_row（backfill_control 在 PG）；
    正在跑的分區數從 ES 帳本算（led 不給就自己建一個）。"""
    from .job import throttle   # 與回補 Job 同一段：共用 backfill_control 的額度
    from .ledger import Ledger
    led = led or Ledger(config.ES_URL, config.LEDGER_INDEX)
    row = conn.execute("SELECT max_docs_per_sec, paused FROM backfill_control WHERE id=1").fetchone()
    if row is None:
        return None
    if row["paused"]:
        return throttle(conn, led)
    return max(1, row["max_docs_per_sec"] / (max(0, led.running_count(30)) + 1))


def promo_scope(dic, target: int, everything: bool = False):
    """宣傳句的重跑範圍。回傳 (ES 條件清單, 清單總句數, 挑不出查詢片語的句子)。
    每個條件＝某一版收的某個渠道的句子：渠道相符、原文含其中一段片語、斷詞版本比那一版舊。
    PM 頁的「宣傳句還沒清幾篇」用的是同一個條件（api/ops.py 的 promo_pending_filter）。
    everything=True 時版本門檻改成現在這一版：清單上的每一句都重找一次（例如第三欄的版本填錯、想整份重驗時用）。
    注意：條件只會從「現在這份清單」產生，所以從清單拿掉的句子不在範圍內；
    要讓那些貼文把句子長回來，用新版跑 rerun --words "<被拿掉那一句裡的片語>"。"""
    clauses, lines, no_probe = [], 0, []
    for since, by_channel in sorted((getattr(dic, "promo_since", None) or {}).items()):
        floor = target if everything else min(target, seg_ver("v2", since))
        for channel, keys in sorted(by_channel.items()):
            lines += len(keys)
            no_probe += [k for k in keys if not promo.probe(k)]
            phrases = promo.probes(keys)
            if phrases:
                clauses.append({"bool": {"filter": [{"term": {"channel": channel}}, {"range": {"seg_ver_v2": {"lt": floor}}}],
                                         "should": [{"match_phrase": {"content.bigram": p}} for p in phrases],
                                         "minimum_should_match": 1}})
    return clauses, lines, no_probe


def run_rerun(dic, es: ES, words: list, batch: int = 500, on_progress=None, record_release: bool = True, limit: bool = True,
              promo_mode: str = "") -> dict:
    """promo_mode：""＝照 words 找（加詞）；"since"＝照宣傳句清單找（每句以開始收它的版本為界，清單沒變的後續版本候選是 0 篇）；
    "all"＝清單全部句子、以現在這版為界（整份重驗；從清單拿掉的句子不在範圍內，見 promo_scope）。"""
    words = [] if promo_mode else [w.strip() for w in words if w and w.strip()]
    conn = None
    if limit or record_release:
        try:
            conn = psycopg.connect(config.PG_DSN, autocommit=True, row_factory=psycopg.rows.dict_row)
        except Exception as e:   # PG 不在：不限速也不記錄，但重跑照做（日誌看得到）
            log("rerun_pg_unavailable", "WARNING", error=str(e)[:200])
            conn = None
    p95_before = None
    if conn is not None and limit:
        from .job import canary_p95
        base = conn.execute("SELECT canary_baseline_ms FROM backfill_control WHERE id=1").fetchone()
        p95_before = base["canary_baseline_ms"] if base and base["canary_baseline_ms"] is not None else canary_p95(es, "ptt")
    target = seg_ver("v2", dic.version)
    promo_info, promo_rules = None, getattr(dic, "promo_rules", None)
    if promo_mode:
        clauses, n_lines, no_probe = promo_scope(dic, target, everything=(promo_mode == "all"))
        promo_info = {"mode": promo_mode, "list_lines": n_lines, "no_probe_lines": len(no_probe)}
        if no_probe:   # 沒有中文也沒有夠長的英數字：ES 找不到候選，這幾句只對之後新寫入的貼文生效
            log("rerun_promo_no_probe", "WARNING", count=len(no_probe), lines=no_probe[:20])
        q = {"bool": {"should": clauses, "minimum_should_match": 1}} if clauses else {"match_none": {}}
    else:
        q = {"bool": {"should": [{"match_phrase": {"content.bigram": w}} for w in words], "minimum_should_match": 1,
                      "filter": [{"range": {"seg_ver_v2": {"lt": target}}}]}}
    estimate = es.count(q)
    log("rerun_start", dict_version=dic.version, words=words, promo=promo_info, candidates=estimate, seg_build=config.SEG_BUILD)
    pit = es.req("POST", f"/{es.index}/_pit?keep_alive=2m")["id"]
    after, scanned, substring, changed = None, 0, 0, 0
    res = {"created": 0, "updated": 0, "noop": 0, "errors": 0, "rejected": 0}
    examples = []
    t0 = time.perf_counter()
    while True:
        body = {"size": batch, "query": q, "pit": {"id": pit, "keep_alive": "2m"}, "sort": [{"_shard_doc": "asc"}],
                "_source": ["channel", "content", "terms_v2"]}
        if after:
            body["search_after"] = after
        hits = es.req("POST", "/_search", body)["hits"]["hits"]
        if not hits:
            break
        items = []
        tb = time.perf_counter()
        rps = rate_limit(conn) if (conn is not None and limit) else None
        for h in hits:
            s = h["_source"]
            scanned += 1
            if promo_mode:   # 這一篇真的有整行被拿掉嗎（候選是用片語找的，會比實際多）
                substring += promo.strip(s["channel"], html.unescape(s["content"] or ""), promo_rules)[1] > 0
            else:
                substring += any(w in s["content"] for w in words)
            terms = segment(dic, s["channel"], s["content"])
            if terms != s.get("terms_v2"):
                changed += 1
                if len(examples) < 10:
                    old = set(s.get("terms_v2") or [])
                    examples.append({"id": h["_id"], "removed": sorted(old - set(terms)), "added": sorted(set(terms) - old)})
            items.append((h["_id"], {}, seg_fields(dic, terms, s["content"]), "v2"))
        r = es.bulk_seg(items)
        for k in res:
            res[k] += r[k]
            metrics.RERUN_DOCS.labels(dic.version, k).inc(r[k])
        after = hits[-1]["sort"]
        if rps:
            should = len(items) / rps
            spent = time.perf_counter() - tb
            if spent < should:
                time.sleep(should - spent)
        if on_progress:
            on_progress({"candidates": estimate, "scanned": scanned, "changed": changed, "written": res["created"] + res["updated"],
                         "noop": res["noop"], "errors": res["errors"]})
    es.req("DELETE", "/_pit", {"id": pit})
    secs = round(time.perf_counter() - t0, 2)
    total = es.count({"match_all": {}})
    out = {"dict_version": dic.version, "words": words, "candidates": estimate, "scanned": scanned,
           "substring_hits": substring, "terms_changed": changed, "seconds": secs, "corpus": total,
           "share_of_corpus": round(scanned / total, 5) if total else None, "examples": examples, **res}
    if promo_info is not None:
        out["promo"] = dict(promo_info, docs_with_listed_line=substring)
    if conn is not None and limit and p95_before is not None and scanned:
        from .job import canary_after, g3_limit
        p95_after, tries = canary_after(conn, es, "ptt", p95_before)
        out["g3"] = {"pass": p95_after <= g3_limit(p95_before), "canary_baseline_ms": p95_before, "canary_p95_ms_after": p95_after,
                     "limit_ms": g3_limit(p95_before), "tries": tries}
    log("rerun_done", **out)
    if record_release and conn is not None and promo_mode:
        if scanned:   # 同一版可能先因加詞重跑過：宣傳句這一筆是「加上去」，不蓋掉原本的數字
            conn.execute("""INSERT INTO dict_releases (dict_version, seg_ver, added_words, affected_estimate, rerun_scope, rerun_written)
                            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (dict_version) DO UPDATE
                            SET affected_estimate=COALESCE(dict_releases.affected_estimate,0)+EXCLUDED.affected_estimate,
                                rerun_scope=concat_ws('; ', dict_releases.rerun_scope, EXCLUDED.rerun_scope),
                                rerun_written=COALESCE(dict_releases.rerun_written,0)+EXCLUDED.rerun_written""",
                         (dic.version, target, [], estimate,
                          f"promo({promo_mode}): {scanned} docs matched list phrases; {substring} had a listed line; {changed} changed terms",
                          res["created"] + res["updated"]))
            # 文字雲 API 的快取以「完成的重跑筆數」當版次（api/app.py 的 generation）：這裡也記一筆完成，
            # 否則已結束月份的文字雲與「還沒清幾篇」會沿用舊快取最多一天
            # 狀態照實寫：有寫入失敗就是 failed（RerunFailed 告警與 PM 頁都看這一欄），不能一律記成 done
            conn.execute("""INSERT INTO rerun_runs (dict_version, seg_ver, words, requested_by, status, candidates, scanned, terms_changed,
                                                    written, noop, errors, seconds, error, started_at, finished_at)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now() - make_interval(secs => %s),now())""",
                         (dic.version, target, [], f"cli-promo-{promo_mode}", "failed" if res["errors"] else "done", estimate, scanned,
                          changed, res["created"] + res["updated"], res["noop"], res["errors"], secs,
                          f"{res['errors']} 篇寫入失敗；修好後再跑一次 --promo {promo_mode}" if res["errors"] else None, secs))
    elif record_release and conn is not None:
        # P0-5：估計（affected_estimate）與實際寫入（rerun_written＝created＋updated）並存，對帳面板與告警看這兩欄
        conn.execute("""INSERT INTO dict_releases (dict_version, seg_ver, added_words, affected_estimate, rerun_scope, rerun_written)
                        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (dict_version) DO UPDATE
                        SET affected_estimate=EXCLUDED.affected_estimate, rerun_scope=EXCLUDED.rerun_scope, rerun_written=EXCLUDED.rerun_written""",
                     (dic.version, target, words, estimate, f"targeted: {scanned} docs containing {','.join(words)}; {changed} changed terms",
                      res["created"] + res["updated"]))
    if conn is not None:
        conn.close()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", default="", help="逗號分隔：這一版新增或改詞頻的詞")
    ap.add_argument("--promo", choices=("since", "all"), default="",
                    help="照宣傳句清單重跑：since＝每句以開始收它的版本為界（平常用這個）；all＝全部句子以現在這版為界（整份重驗）。"
                         "從清單拿掉的句子兩種都找不到，要另外跑 --words \"<那一句裡的片語>\"")
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--no-limit", action="store_true", help="不讀 backfill_control 限速、不量 G3（測試用）")
    a = ap.parse_args(argv)
    words = [w.strip() for w in a.words.split(",") if w.strip()]
    if bool(words) == bool(a.promo):
        ap.error("--words 和 --promo 要給其中一個（各跑一次，不能同時）")
    dic = dictionary.load()
    es = ES()
    es.put_guard_script()
    out = run_rerun(dic, es, words, batch=a.batch, limit=not a.no_limit, promo_mode=a.promo)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["errors"] == 0 else 1
